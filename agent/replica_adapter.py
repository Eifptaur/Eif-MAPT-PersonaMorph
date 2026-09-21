"""wechatauto-replica 适配层 —— **唯一**允许触碰该库私有 API 的地方。

为什么要这一层（2026-09-13 W1）
------------------------------
我们原先散着调它三个私有接口：
  - `WeChatDB._db_files`（实例属性：[(rel, path, size)]）—— 在 `_load_nicknames` / `_load_groups` 里遍历
  - `WeChatDB._open(rel)` —— 同上两处，直连 sqlite 查 contact 表
  - `WeChatDB._msg_conn(user)` —— 引用消息取图时用；**1.2.2 起它已被降级为"只返回第一个命中分片"的兼容接口**
    ⇒ 跨分片时**不报错、静默少查**（这是最危险的一种升级后果）
并且 `dep_check()` 对它的版本判定是**严格等值**，等于把它钉死在 1.1.5.1。

本适配层的三条规矩：
1. **能走公开 API 就走公开 API**（例如 `get_groups()` 已能满足列群需求，就不再自己查 contact.db）；
2. 必须用私有的地方**只在这里用**，并写明"为什么非用不可"；
3. **版本守卫**：低于 `MIN_VERSION` 拒绝、高于 `MAX_TESTED` 只警告（提示"本仓库未实测该版本"），绝不静默假定行为一致。

判据：`py -3 scripts/replica_adapter_selftest.py`（假 DB 单测，不需要微信在跑）。
"""

from __future__ import annotations

import importlib.metadata as md
import os

# ---- 版本口径 ---------------------------------------------------------------
PKG = "wechatauto-replica"
MIN_VERSION = "1.1.5.1"      # 低于它：不支持（老接口语义不同）
KNOWN_GOOD = "1.2.2.2"       # 本仓库实测通过并据此改写调用点的版本
MAX_TESTED = "1.2.2.2"       # 高于它：只警告（未实测），不阻断


def ver_tuple(v) -> tuple:
    """把版本串转成可比较的元组：'1.2.2.2' -> (1,2,2,2)。"""
    nums = []
    cur = ""
    for ch in str(v or ""):
        if ch.isdigit():
            cur += ch
        else:
            if cur:
                nums.append(int(cur))
                cur = ""
    if cur:
        nums.append(int(cur))
    return tuple(nums) if nums else (0,)


def installed_version() -> str:
    try:
        return md.version(PKG)
    except Exception:
        return ""


def version_report() -> dict:
    """返回 {installed, min, known_good, ok, level, note}；level ∈ {ok, too_old, untested, missing}。"""
    inst = installed_version()
    out = {"installed": inst, "min": MIN_VERSION, "known_good": KNOWN_GOOD,
           "ok": False, "level": "missing", "note": ""}
    if not inst:
        out["note"] = "未安装 %s：驱动层不可用（pip install -r requirements.txt）" % PKG
        return out
    t, lo, hi = ver_tuple(inst), ver_tuple(MIN_VERSION), ver_tuple(MAX_TESTED)
    if t < lo:
        out["level"] = "too_old"
        out["note"] = "版本 %s 低于最低要求 %s：请升级（老接口语义不同，不能直接跑）" % (inst, MIN_VERSION)
        return out
    out["ok"] = True
    if t > hi:
        out["level"] = "untested"
        out["note"] = "版本 %s 高于本仓库实测过的 %s：功能可用但**行为未实测**，出现异常先怀疑它" % (inst, MAX_TESTED)
    else:
        out["level"] = "ok"
        out["note"] = "版本 %s 在实测范围内（最低 %s / 实测 %s）" % (inst, MIN_VERSION, KNOWN_GOOD)
    return out


def has_api(obj, name: str) -> bool:
    return callable(getattr(obj, name, None))


def capabilities(db) -> dict:
    """能力位：调用点据此决定走哪条支路（判据也用它做机械断言）。"""
    return {
        "msg_conns": has_api(db, "_msg_conns"),     # 1.2.x：跨分片全量
        "msg_conn": has_api(db, "_msg_conn"),       # 1.1.x：只取第一个命中分片
        "db_files": hasattr(db, "_db_files"),
        "open_shard": has_api(db, "_open"),
        "get_groups": has_api(db, "get_groups"),
    }


# ---- 私有 API 收口 -----------------------------------------------------------
# 为什么这几处非用私有不可：contact 表（昵称/群）没有等价的"整表"公开接口
# （`get_nickname` 是一次一个、`search_contact` 是按关键词搜），而我们要的是一张映射表。

def iter_shards(db):
    """遍历该库收集到的所有 db 分片：[(rel, path)]（收口 `_db_files`）。"""
    out = []
    for item in (getattr(db, "_db_files", None) or []):
        try:
            rel, path = item[0], item[1]
        except Exception:
            continue
        out.append((rel, path))
    return out


def missing_key_shards(db) -> list:
    """列出**当前没有可用密钥**的分片 rel。

    为什么要它（2026-09-16 网友 A 的报告：`KeyError: 'message\\media 1.db'`）：微信会**懒创建**
    新分片（收到媒体就多一个 `media N.db`），而驱动库的密钥表是它 `__init__` 时的快照
    ⇒ 新分片没密钥 ⇒ 库自己的 `_open()`（`db.py:1311` 直接 `self._keys[rel]`）**抛 KeyError 且它不兜**
    ⇒ 读消息 / 会话头 / 投递回读整条链全断（他报的"白名单设置不了、读取会话失败"就是这个）。
    """
    out = []
    for rel, _path in iter_shards(db):
        try:
            if not db._key_works(rel):
                out.append(rel)
        except Exception:
            out.append(rel)
    return out


def refresh_shards(db) -> dict:
    """刷新分片表并**补齐新分片的密钥**。返回 `{added, missing, dropped, refreshed}`。

    调用时机：①接入时（一次，见 `WeChatAdapter._init_db`）②`open_shard` 撞上 KeyError 时
    ③以后若加「重新校准密钥」按钮也走这里。

    ⚠️ **二级兜底（2026-09-16 网友 A 的 `KeyError: message\\media 1.db`）**：补不回来的分片
    **从分片表里摘掉**（只影响那个分片的内容），换整条链可用 —— 因为驱动库**内部**遍历
    `_db_files` 时会挨个 `_open()`，一个没密钥的分片就能把「读消息 / 会话头 / 投递发送 / 群列表」
    一起打死，而它自己不兜。摘掉是**有代价的补救**，所以必须留痕（`dropped` 由调用方记日志/显示）。
    安全线：**只在还剩至少一个分片时摘**，绝不把分片表清空。
    """
    before = set(rel for rel, _ in iter_shards(db))
    try:
        db._refresh_db_files()
    except Exception:
        pass
    miss = missing_key_shards(db)
    if miss:
        try:
            db._load_or_extract_keys()
        except Exception:
            pass
    dropped = []
    miss2 = missing_key_shards(db)
    if miss2:
        try:
            _all = list(getattr(db, "_db_files", None) or [])
            _keep = [t for t in _all if t[0] not in set(miss2)]
            if _keep and len(_keep) != len(_all):
                dropped = list(miss2)
                db._db_files = _keep
                miss2 = missing_key_shards(db)
        except Exception:
            dropped = []
    after = set(rel for rel, _ in iter_shards(db))
    return {"added": sorted(after - before), "missing": miss2, "dropped": dropped,
            "refreshed": bool(miss)}


def open_shard(db, rel):
    """打开某个分片（收口 `_open`）；调用方负责 close。**拿不到就返回 None**（跳过这个分片）。

    ⚠️ 2026-09-16（网友 A 的 `KeyError: 'message\\media 1.db'`）：驱动库对"没有密钥的分片"不兜。
    这里先补一次密钥再试；仍不行就如实返回 None —— 一个懒创建的媒体分片不该把整条链打死
    （调用方都已在 try 里用连接，None 会被它们当成"这个分片读不了"跳过）。
    """
    try:
        return db._open(rel)
    except KeyError:
        refresh_shards(db)
        try:
            return db._open(rel)
        except Exception:
            return None


def close_all(conns):
    for c in list(conns or []):
        try:
            c.close()
        except Exception:
            pass


def contact_db_rel(db):
    """找到 contact.db 对应的分片 rel（找不到返回 None）。"""
    for rel, path in iter_shards(db):
        try:
            if os.path.basename(path) == "contact.db":
                return rel
        except Exception:
            continue
    return None


def load_nickname_map(db) -> dict:
    """wxid -> 显示名 的整表映射（老 `_load_nicknames` 的唯一实现，走适配层）。

    ⛔ 2026-09-21 修（第九轮 **V-R9-7** · P1）：原来**三类失败全部静默返回 `{}`**（`rel is None`
    直接 return、查询异常 `except: pass`）—— 而 2026-09-20 那次同族修复只改了 `load_groups`
    与 `load_privates`（都改成"真失败就抛"）⇒ `wechat._load_nicknames` 的 `_cap["contacts"]`
    一个字节都记不到、昵称静默退化成 wxid、"登记大号按昵称"永远匹配不上（用户报过的
    「无法识别我的大号」）。⇒ 与那两条同口径：**真失败就抛**（由 `wechat._load_nicknames`
    记进 `_cap` 并如实显示）；查询成功但确实一个联系人都没有 ⇒ 返回空表（那是事实）。
    """
    mapping = {}
    rel = contact_db_rel(db)
    if rel is None:
        raise RuntimeError("找不到联系人库 contact.db")
    conn = None
    try:
        conn = open_shard(db, rel)
        rows = conn.execute("SELECT username, nick_name, remark FROM contact").fetchall()
        for r in rows:
            mapping[str(r["username"])] = str(r["remark"] or r["nick_name"] or r["username"])
    except Exception as _e:
        raise RuntimeError("读 contact.db 失败：%s" % (str(_e)[:80] or type(_e).__name__))
    finally:
        close_all([conn])
    return mapping


def load_groups(db) -> list:
    """群列表 [{'name','wxid'}]：**优先公开 `get_groups()`**，拿不到再回退查 contact.db。

    ⛔ 2026-09-20 修（网友 v0920-1227 报「**微信已连接却找不到群聊**」，截图里控制台写着
      「没读到任何群聊：请先在「运行状态」确认微信已连接」）：这里原来把**两条路都失败**的情况
      用 `except Exception: pass` 悄悄吞成"0 个群" —— 于是上层 `wechat._cap["groups"]` **永远拿不到
      fail**（它只在真抛异常时才记），`groups_fn` 便返回 `ok:True + groups:[]`，控制台据此把用户
      引向"确认微信已连接"这个**完全错误的方向**（他那台微信明明连上了）；而白名单勾不到群 ⇒
      用户只能手打群名 ⇒ 与 `self._groups` 里的名字对不上 ⇒ `targets` 为空 ⇒ **监听循环什么都不做、
      群里 @ 也不回**（正是那份反馈的后半段）。
    ⇒ 现在：**查询真的失败了就抛**（由 `wechat._load_groups` 记进 `_cap` 并如实显示给用户）；
      **查询成功、确实一个群都没有**才返回空列表（那是事实，不是错误）。
    """
    errs = []
    if has_api(db, "get_groups"):
        try:
            rows = db.get_groups() or []
            out = []
            for r in rows:
                try:
                    wxid = str(r.get("username") or "")
                    name = str(r.get("name") or r.get("nick_name") or wxid)
                except Exception:
                    continue
                if wxid:
                    out.append({"name": name, "wxid": wxid})
            if out:
                return out
            errs.append("get_groups() 给出 0 条")
        except Exception as _e:
            errs.append("get_groups()：%s" % (str(_e)[:80] or type(_e).__name__))
    rel = contact_db_rel(db)
    if rel is None:
        raise RuntimeError("找不到联系人库 contact.db%s"
                           % ("（%s）" % "；".join(errs) if errs else ""))
    conn = None
    try:
        conn = open_shard(db, rel)
        rows = conn.execute(
            "SELECT username, nick_name, remark FROM contact WHERE username LIKE '%@chatroom'").fetchall()
    except Exception as _e:
        raise RuntimeError("读 contact.db 失败：%s%s"
                           % (str(_e)[:80] or type(_e).__name__,
                              ("（此前：%s）" % "；".join(errs)) if errs else ""))
    finally:
        close_all([conn])
    return [{"name": str(r["remark"] or r["nick_name"] or r["username"]),
             "wxid": str(r["username"])} for r in rows]


# 微信系统号 / 服务号（不是真人私聊）——私聊发现时要排掉
_SYS_CONTACTS = {
    "filehelper", "newsapp", "fmessage", "medianote", "floatbottle",
    "notifymessage", "officialaccounts", "mphelper", "weixin", "qqmail",
    "tmessage", "qmessage", "weibo", "facebook", "feedsapp", "blogapp",
}


def load_privates(db) -> list:
    """列出**私聊联系人**（非群、非系统号）。

    用途（2026-09-16 用户原话）：「他也许是那种私聊的想法，**大号跟小号对谈**，相当于借一个智能体
    进来跟自己聊天，这个应该也可以做吧」—— 原来监听目标**只来自群列表**，私聊根本不在监听范围里。
    只读 `contact` 表；**只排掉** `@chatroom` 与已知系统号 —— 不做"只留 wxid_ 开头"那种硬过滤，
    因为自定义微信号不是 wxid_ 开头，滤掉会把真人漏掉。
    """
    out = []
    rel = contact_db_rel(db)
    if rel is None:
        # ⛔ 2026-09-20：与 `load_groups` 同族 —— 原来这里返回空列表，把"找不到联系人库"
        #   说成"没有私聊联系人"。现在如实抛（由 `wechat._load_privates` 记进 `_cap`）。
        raise RuntimeError("找不到联系人库 contact.db")
    conn = None
    try:
        conn = open_shard(db, rel)
        rows = conn.execute("SELECT username, nick_name, remark FROM contact").fetchall()
    except Exception as _e:
        raise RuntimeError("读 contact.db 失败：%s" % (str(_e)[:80] or type(_e).__name__))
    finally:
        close_all([conn])
    for r in rows:
        wxid = str(r["username"] or "")
        if not wxid or wxid.endswith("@chatroom"):
            continue
        if wxid.lower() in _SYS_CONTACTS:
            continue
        out.append({"name": str(r["remark"] or r["nick_name"] or wxid), "wxid": wxid})
    return out


def message_conns(db, user):
    """某会话**全部分片**的 [(conn, table)]（调用方负责 close_all）——跨分片必须读齐。

    1.2.x：直接 `_msg_conns`（库内部已处理"跨分片 + 损坏自愈"）；
    1.1.x：只有 `_msg_conn`，退化成单分片（并在 note 里如实说明）。
    """
    caps = capabilities(db)
    if caps["msg_conns"]:
        try:
            return list(db._msg_conns(user) or [])
        except Exception:
            return []
    if caps["msg_conn"]:
        try:
            one = db._msg_conn(user)
            return [one] if one else []
        except Exception:
            return []
    return []


def find_server_id_local_id(db, user, server_id):
    """在**所有分片**里按 server_id 找 local_id（原实现只查第一个分片 ⇒ 静默少查）。

    返回 int 或 None。等价于旧代码里那段 `conn.execute("SELECT local_id FROM %s WHERE server_id=?")`。
    """
    conns = message_conns(db, user)
    try:
        for conn, table in conns:
            try:
                rr = conn.execute(
                    "SELECT local_id FROM %s WHERE server_id=?" % table, (int(server_id),)).fetchone()
            except Exception:
                continue
            if rr:
                return rr[0]
    finally:
        close_all([c for c, _ in conns])
    return None


def patch_driver_quirks() -> list:
    """给驱动库打"我们这侧的补丁"（**不改 site-packages 文件**，所以随包在别的机器上也生效）。

    ⚠️ 2026-09-18 实测踩到的第三方真 bug：`wechatauto/guia.py` 全文**没有 `import threading`**，
    却在布局校准里用了 `threading` ⇒ 一旦触发「输入框探测连续失败 → 自动重新校准布局」，
    校准必然抛 `name 'threading' is not defined` 被吞成一行 debug 日志 ⇒ **校准永远不生效**。
    现场日志（2026-09-18 03:43:22）：
        `未检测到输入框` → `输入框探测连续失败，自动重新校准布局…` → `布局校准失败：name 'threading' is not defined`

    ⇒ 做法：**在导入后把缺的名字注入该模块**（幂等，重复调用无害）。返回 (模块名, 注入的名字) 列表。
    """
    import threading as _threading
    import time as _time
    out = []
    try:
        import wechatauto.guia as _g
        for _name, _val in (("threading", _threading), ("time", _time)):
            if not hasattr(_g, _name):
                try:
                    setattr(_g, _name, _val)
                    out.append("wechatauto.guia.%s" % _name)
                except Exception:
                    pass
    except Exception:
        pass
    # 🔴 2026-09-18：**顺手把"置前/置顶"的闸上到库的类上**（在造 WeChatGUI 之前调用本函数）。
    #   为什么放在这里：`WeChatGUI.__init__` 里就会 `calibrate_layout() → bring_to_front()`
    #   （窗口尺寸与上次校准差 >15% 时），实例级上闸来不及 ⇒ 必须**类级**、且在构造之前。
    try:
        from . import ui_adapt as _ua
        if _ua.harden_gui_class():
            out.append("WeChatGUI.bring_to_front/calibrate_layout/ensure_visible(闸)")
    except Exception:
        pass
    return out


def selfcheck() -> list:
    """给自检脚本/控制台用的一行行结论（不碰微信、不碰数据库）。"""
    rep = version_report()
    rows = [{"item": "适配层版本", "ok": rep["ok"], "detail": rep["note"]}]
    try:
        from wechatauto import WeChatDB  # noqa: F401
        rows.append({"item": "驱动库可导入", "ok": True, "detail": "from wechatauto import WeChatDB"})
    except Exception as e:  # pragma: no cover
        rows.append({"item": "驱动库可导入", "ok": False, "detail": str(e)})
    try:
        _patched = patch_driver_quirks()
        rows.append({"item": "驱动库补丁（缺名字注入）", "ok": True,
                     "detail": ("已注入 " + "、".join(_patched)) if _patched else "无需注入（该库已自带）"})
    except Exception as e:  # pragma: no cover
        rows.append({"item": "驱动库补丁（缺名字注入）", "ok": False, "detail": str(e)})
    return rows


# ── 正文还原：把"库里存着、读法只给类型标签"的消息正文解出来（2026-09-14）─────────────
# 为什么必须有这一层（既有口径：障原话：「我每次都是把你的话复制到微信发过去了，但是随后你好像就
# 不太能正常识别并操作了」）——**实测机制**：
#   · 微信 4.x 把**长文本与文件卡**的 `content` **zstd 压缩**存库（实测 local_id=703 是 1791 字节的
#     zstd 帧，magic `28 b5 2f fd`）；
#   · 而库的友好化读法（`get_messages` / `get_new_messages`）遇到压缩体**只回类型标签**
#     （`[文本]` / `[文件/链接/卡片]`）⇒ **正文直接消失**：短 token 读得到（没压缩），用户粘过来的
#     长段落一律读成 `[文本]` ⇒ 机器人"看不见"他说的话，身份闸的"针"也全变成占位符。
#   · `get_message_row(chat, local_id)` 是**库的公开方法**，能拿到原始行（content 就是那串 zstd 帧）。
# 规矩：只读、不写库；解不开就返回空串（绝不抛、绝不猜内容）；调用点都包在 try 里。
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def zstd_module():
    """运行时自带的 zstd 实现（实测 `zstandard 0.25.0` 随驱动库一起装进来了）。"""
    try:
        import zstandard  # type: ignore
        return zstandard
    except Exception:
        return None


def recover_text(raw) -> str:
    """把原始 `content` 还原成正文（str）。

    · 已经是 str ⇒ 原样返回；
    · zstd 帧 ⇒ 解开并 utf-8 解码；
    · 其它 bytes ⇒ utf-8 尽力解码；
    · 拿不到/解不开 ⇒ `''`（**不抛异常、不返回占位符**）。
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    try:
        b = bytes(raw)
    except Exception:
        return ""
    if not b:
        return ""
    if b[:4] == ZSTD_MAGIC:
        z = zstd_module()
        if z is None:
            return ""
        try:                                   # 一次性解（带输出上限，防解压炸弹）
            return z.ZstdDecompressor().decompress(
                b, max_output_size=8 * 1024 * 1024).decode("utf-8", "replace")
        except Exception:
            try:                               # 流式兜底（有些帧只能在流式 API 下解）
                import io
                # ⛔ 2026-09-21 修（第十轮 **V-R10-11**）：流式兜底原来**没有上限** —— 审计实测
                #   403 字节的帧解出 12MB、1299 字节解出 40MB（把上面那句 `max_output_size=8MB`
                #   整个绕穿）。⇒ 改成**读的时候就卡住**：多读一个字节发现超限就判失败。
                _cap = 8 * 1024 * 1024
                with z.ZstdDecompressor().stream_reader(io.BytesIO(b)) as rd:
                    _buf = rd.read(_cap + 1)
                if len(_buf) > _cap:
                    log.warning("zstd 流式解压超过上限（%d 字节 > %d）⇒ 丢弃这条正文（防解压炸弹）",
                                len(_buf), _cap)
                    return ""
                return _buf.decode("utf-8", "replace")
            except Exception:
                return ""
    return b.decode("utf-8", "replace")


def message_text(db, chat_id: str, local_id) -> str:
    """取某条消息的**正文**（必要时解压）。任何异常 ⇒ `''`。"""
    if db is None or not chat_id or local_id in (None, ""):
        return ""
    try:
        row = db.get_message_row(str(chat_id), int(local_id))
    except Exception:
        return ""
    if not isinstance(row, dict):
        return ""
    txt = recover_text(row.get("content"))
    if not txt:
        txt = recover_text(row.get("compress_content"))
    return txt


def fill_text(db, chat_id: str, local_id, friendly: str) -> str:
    """友好化结果是个**类型标签**时，用它换回正文；否则原样返回 friendly。

    识别"类型标签"的口径：整串就是一个方括号标签（`[文本]` / `[文件/链接/卡片]` / `[图片]`…，
    长度 ≤ 16、内部无方括号）。
    """
    import re as _re
    s = str(friendly or "")
    if not _re.match(r"^\[[^\[\]]{1,16}\]$", s):
        return s
    got = message_text(db, chat_id, local_id)
    return got or s
