# -*- coding: utf-8 -*-
"""session_order.py —— **从 DB 读"会话列表的准确顺序"**（不用 OCR）。

★为什么有它
  屏幕 OCR 认会话名**不可用**：中文群名常被读错（示例：`某中文群名` → `五宝．吸巴“，`；
  另一例被读成 `訴闻`/`啃使用者`），连 `loose_matches`（字符集合 Jaccard）都匹配不上。
  · 而 **DB 里是准的**：`session/session.db` 的 `SessionTable` 有
    `username`（= wxid / `xxx@chatroom` / `filehelper`）+ `sort_timestamp`（列表排序键）。
  · **逐行核对通过**（同一时刻）：
      屏#0（绿底＝当前）`文件传输助手` ↔ DB `filehelper`                      ✅
      屏#1                            ↔ DB `10000000003@chatroom`             ✅
      屏#2                            ↔ DB `10000000004@chatroom`             ✅
    ⇒ **`sort_timestamp DESC` 的顺序 ＝ 屏幕从上到下的顺序**。

★于是"认会话/定位那一行"**整条从 OCR 手里拿回来**：
    序号 i（0 基，按 sort_timestamp 倒序）⇒ 屏幕第 i 行 ⇒ y 坐标由行高算（**不认字**）。

★★★ 二次实测修正：**`is_hidden` 不是"列表可见性"**
  第一次用 `is_hidden` 当过滤器 —— **错了**。实测暴露多处硬伤：

  【硬伤 1】`is_hidden` **不是"列表可见性"**，而是"已隐藏会话"（界面上根本看不到）：
      · 若干条 `is_hidden=1` 的项屏幕上**都没有** ⇒ **必须滤掉**。
      · `include_hidden=False` 滤掉后，序号本应对齐 —— 实测前 7 行**确实全对**：
          屏0 filehelper / 屏1 newsapp / 屏2 10000000003@chatroom / 屏3 10000000004@chatroom
          / 屏4（个人号）/ 屏5 weixin（= 微信团队）/ 屏6 brandservicesessionholder（= 服务号）
      · ⚠️ 曾经出现的"顺序不对"其真因是**核对基准用错了**（把一个号当成了另一个号）
        —— **排序列本身没错**。

  【硬伤 2】`gh_*`（公众号/服务号内部条目）**不该出现在顶层会话列表**：
      · `gh_a73e2407e0f8`（腾讯云助手）、`gh_4b451db73917`（指引社）的 `is_hidden=0`，
        但它们装在「服务号」聚合入口里（点开「服务号」才看得到），
        不在顶层 7 行里 ⇒ **必须单独滤掉**（`gh_` 前缀）。
      · 结论：顶层可见性 = `is_hidden == 0` **且** `username` 不是 `gh_*`。

  【硬伤 3】`type` 列**全为 0**（13/13），**完全无法**区分 群/人/公众号/系统号 ⇒ 不能用它筛。
  【硬伤 4】`sort_timestamp` **会并列**（`brandservicesessionholder` 与 `gh_a73e2407e0f8`
      同为一个时间戳）⇒ 并列时顺序不稳，需二级键（用 username 保稳定）。

★★★ 三次实测：**"界面上显示的名字" ≠ "DB 里的身份"，两套必须分开记**

  铁证（同一时刻）：
    · `open_db` 的 `account` ＝ **`<wxid>_<后缀>`** ⇒ 可据此取到**本机登录号**
    · 该号在 `contact` 表：`alias` **非空**、`nick_name` 是显示名
    · 而会话列表里另一条 `nick_name='E'`、**`alias` 空**
    ⇒ **别把显示名当身份**（详见下方"纪律"）。

  ⇒ **纪律**：
    1. `username`（`wxid_*`/`@chatroom`/`gh_*`/系统 key）＝ **微信原始 ID，稳定不变** → 认会话**只用它**。
    2. `nick_name`/`remark` ＝ **用户改过的显示名**（会改、会重名、会被覆盖写） → **只能看，不能当钥匙**。
    3. ★★ `alias`（`contact.alias`）＝ **"自定义微信号"**，**只有改过微信号的人才有值**。
       ⚠️⚠️ **`alias` 空 ≠ 没有微信号** —— 实测实证：
          · 界面「微信号」栏可能明写一个 `wxid_*` 原始串（该号**从没改过微信号**）
          · 而 DB `contact.alias` **是空的**
          ⇒ 微信规则：**改过 ⇒ 界面显示 alias；没改过 ⇒ 界面回退显示原始 `wxid_*`**。
          ⇒ 判"界面微信号"必须用 `display_wechat_id()`，**不能直接拿 alias 当答案**。
       ⚠️ **本机登录号自己的 alias 也在表里**，别误当联系人。
    4. **别用"界面上叫什么"反推"DB 里是谁"** —— 极易错认。
    5. **多开时**：本机跑的号 **不等于** 主号，**不能拿"本机账号"当"用户主号"**。

★★★ 四次实测：**多开账号 —— `open_db` 按"谁的库最近在写"挑，读到的是"当前活跃号"**

  铁证（同一时刻）：
    · `xwechat_files` 下**有多个账号目录** `<wxid>_<后缀>`；
    · `open_db` 的 `account_why` ＝ "这个账号的库刚刚还在写（-wal 时间）"
      ⇒ **`open_db` 挑的号 ＝ 当前活跃号**（判据是 -wal 修改时间，**不是**"哪个是主号"）
    · **alias 在不同号上不同**（有的非空、有的空）⇒ 不能跨号混用。

  ⇒ **纪律（多开专用）**：
    1. **读会话列表前先问"我现在读的是哪个号"** —— 用 `self_username(adapter)` 取，
       不要假设"就是。
    2. **两个号各有独立会话列表**（各自的 `session.db`）⇒ 切号后 `username` 全变，
       **不要把两次读到的 username 混在一起用**。
    3. `open_db` 的挑号判据是 **"-wal 最近在写"** ⇒ 它给的是"**当前活跃号**"，
       **不是**"用户心里的主号"。需要"指定某个号"时**必须显式传账号目录**。

⚠️ 边界（如实标注）
  · **多开切号**：本模块**只读"当前活跃号"的库**；要读别的号需另开（`open_db(explicit=账号目录)`）。
  · **置顶会话**：`SessionTable` **未见到"置顶"专列**（列清单见下）⇒ 本模块**不猜**，
    `pinned` 判据：`sort_timestamp > last_timestamp`（真机实测 —— 微信置顶＝把 sort 抬到最新）。
    列清单（本机 4.1.15.13）：username / type / unread_count / unread_first_msg_srv_id /
    unread_first_pat_msg_local_id / unread_first_pat_msg_sort_seq / is_hidden / summary / draft /
    status / last_timestamp / sort_timestamp / last_clear_unread_timestamp / last_msg_locald_id /
    last_msg_type / last_msg_sub_type / last_msg_sender / last_sender_display_name / last_msg_ext_type
  · **"微信号"列位置**：在 **`contact.db::contact.alias`**，**不在** `session.db`（易找错）。
  · **列表滚动位置**：本模块只给"完整有序列表"，**不负责当前滚到第几行** ——
    那一层要用"屏幕首行 y + 绿底高亮行反查"来定（见 `agent/chat_ocr.py` 的绿带口径）。
  · **行高**：屏幕实测 **97~104px**（本机），而项目常量 `chat_ocr.ROW_PITCH = 64` **是错的**（差 ~34px）
    ⇒ 行高一律**按实测**，不用那个常量。
"""
from __future__ import annotations

import json
import os
import logging

# ⛔ **必须在第一次用到之前导入**：`_save_pinned()` 里原来写的是裸 `persist.tmp_for(p)`，
#   而 `from . import persist` 写在那行**之后**（同一函数内）⇒ 运行时 `NameError`
#   ⇒ 被外层 `except Exception: pass` 吞掉 ⇒ "曾观察到置顶"名单**永远落不了盘、静默失败**。
#   （第二台机复核实测：`list_sessions` 明明观察到 `sort_gt_last`，盘上却始终没有那个文件。）
#   ⚠️ 与 `wechat.py` 少 `import sys`、`autostart.py` 用不存在的 `ROOT` 是**同一类错**
#   —— 那个判据（"`log` 的定义都在使用之前"）就是为这一族立的。
from . import persist

log = logging.getLogger("persona-morph")

#: 会话排序键所在列（实测本机 4.1.15.13）
_COL_SORT = "sort_timestamp"
_COL_USER = "username"
_COL_HIDDEN = "is_hidden"
#: 系统会话（不是"人/群"，双击不会开出聊天独立窗）——定位时要跳过或如实标注
SYSTEM_USERS = frozenset({
    "newsapp",            # 腾讯新闻
    "weixin",             # 微信团队
    "notifymessage",      # 服务通知
    "brandservicesessionholder",   # 服务号（**聚合入口**，点开里面装公众号；顶层可见）
    "fmessage",           # 朋友推荐
    "floatbottle",        # 漂流瓶
    "qqmail",
    "medianote",
})
#: 文件传输助手是**可以发消息**的（本项目测试常用），不算"不可操作的系统会话"
USABLE_SPECIAL = frozenset({"filehelper"})
#: system username → 屏幕显示名（DB 里存的是英文 key，屏幕上显示中文）
SYSTEM_DISPLAY = {
    "newsapp": "腾讯新闻",
    "weixin": "微信团队",
    "notifymessage": "服务通知",
    "brandservicesessionholder": "服务号",
    "fmessage": "朋友推荐",
    "floatbottle": "漂流瓶",
    "qqmail": "QQ邮箱提醒",
    "medianote": "语音记事本",
    "filehelper": "文件传输助手",
}
#: 公众号/服务号**内部**条目（`gh_*`）—— **不在顶层会话列表**，
#: 实测：`gh_a73e2407e0f8`（腾讯云助手）、`gh_4b451db73917`（指引社）都在「服务号」里面。
#: ⇒ 顶层定位一律排除。
GH_PREFIX = "gh_"


def _is_top_level(username: str) -> bool:
    """这条会话**是否出现在顶层会话列表**（实测口径，见模块 docstring 硬伤 2）。"""
    u = str(username or "")
    if not u:
        return False
    if u.startswith(GH_PREFIX):      # 公众号 → 在「服务号」内部
        return False
    return True



def _session_db_rel(db):
    """找 `session\\session.db` 的分片 rel（找不到给空串）。"""
    from . import replica_adapter as _ra
    try:
        for rel, path in _ra.iter_shards(db):
            try:
                if os.path.basename(path) == "session.db":
                    return rel
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                continue
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return ""
    return ""


def _name_of(username: str, nickname_map: dict, group_map: dict) -> str:
    """把 username 翻成**显示名**（昵称/群名优先，取不到退回 username）。"""
    u = str(username or "")
    if u in SYSTEM_DISPLAY:
        return SYSTEM_DISPLAY[u]
    if u in group_map:
        return str(group_map[u])
    if u in nickname_map:
        return str(nickname_map[u])
    return u


def display_wechat_id(username: str, alias: str = "") -> str:
    """★ **界面上「微信号」那一栏显示的东西**。

    微信规则：
        · **改过微信号** ⇒ 界面显示 `alias`（自定义微信号串）
        · **没改过**     ⇒ 界面显示 **`wxid_*` 原始串**

    ⇒ **`alias == ""` 绝不能解释成"没有微信号"**，而应解释成
      "**没改过 ⇒ 界面回退显示原始 `wxid_*`**"。

    ⚠️ 判"界面微信号"**不能用 `alias` 直接当答案** —— 必须走本函数。
    """
    u = str(username or "")
    a = str(alias or "").strip()
    if a:
        return a
    if u.startswith("wxid_"):
        return u        # 没改过 ⇒ 界面回退显示原始 wxid
    return ""           # 群/公众号/系统号：界面「微信号」栏为空


def _contact_db_rel(db):
    """找 `contact\\contact.db` 的分片 rel（找不到给空串）。"""
    from . import replica_adapter as _ra
    try:
        for rel, path in _ra.iter_shards(db):
            try:
                if os.path.basename(path) == "contact.db":
                    return rel
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                continue
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return ""
    return ""


def load_aliases(adapter) -> dict:
    """★ **自定义微信号表**：`{username(内部ID): alias}`（读 `contact.db::contact.alias`）。

    · **`alias` 住在 `contact.db`**，**不在** `session.db`。
      （搜"哪个是微信号"时最容易找错 —— `session.SessionTable` 里没有 alias 列。）
    · ⚠️ **`alias` 有值 ⇔ 这个人「改过微信号」**；**没改过 ⇒ `alias` 空**，
      此时**界面上「微信号」栏显示的是原始 `wxid_*` 串**（见 `display_wechat_id`）。
    · 群（`@chatroom`）、系统号（`filehelper`/`weixin`/`newsapp`）、
      多数公众号（`gh_*`）**alias 为空**（群/公众号本来就没有"微信号"）。

    失败语义：读不出来 **返回 `{}`**（alias 是"辅助信息"，缺了不该让整条链断）——
    但**调用方据此判"某会话有没有微信号"时必须知道"空字典 ≠ 没有微信号"**，故另给
    `has_alias_data(adapter)` 供严格判别（见下）。
    """
    from . import replica_adapter as _ra
    db = getattr(adapter, "_db", None)
    if db is None:
        return {}
    rel = _contact_db_rel(db)
    if not rel:
        return {}
    conn = _ra.open_shard(db, rel)
    if conn is None:
        return {}
    out = {}
    try:
        for r in conn.execute("SELECT username, alias FROM contact").fetchall():
            d = dict(r) if hasattr(r, "keys") else None
            u = str((d.get("username") if d else r[0]) or "")
            a = str((d.get("alias") if d else r[1]) or "").strip()
            if u and a:
                out[u] = a
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return out
    finally:
        _ra.close_all([conn])
    return out


def has_alias_data(adapter) -> bool:
    """`contact.db` 是否真的读到了（区分"读失败"与"确实没有微信号"）。"""
    try:
        from . import replica_adapter as _ra
        db = getattr(adapter, "_db", None)
        if db is None:
            return False
        rel = _contact_db_rel(db)
        if not rel:
            return False
        conn = _ra.open_shard(db, rel)
        if conn is None:
            return False
        n = conn.execute("SELECT COUNT(*) FROM contact").fetchone()[0]
        _ra.close_all([conn])
        return int(n or 0) > 0
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return False


def list_sessions(adapter, *, include_hidden: bool = False, include_system: bool = True,
                  include_gh: bool = False, with_alias: bool = True) -> list:
    """**按屏幕顺序**返回会话列表 ⇒ `[{order, username, name, sort_ts, hidden, system, alias}]`。

    `order` ＝ 0 基序号（**与屏幕从上到下的行号一一对应**，前提：列表未滚动且无置顶差异）。

    ★可见性口径（实测钉死，见模块 docstring）：
        **顶层可见 ＝ `is_hidden == 0` 且 `username` 不是 `gh_*`**
        · `is_hidden != 0` ⇒ 已隐藏会话（用户在界面上看不到）⇒ 默认滤掉
        · `gh_*`          ⇒ 公众号（装在「服务号」内部）⇒ 默认滤掉
      实测逐行核对（本机同时刻）：前 7 行与用户屏幕真值 **100% 对上**。

    `alias` 字段 ＝ **微信号**（读 `contact.alias`，见 `load_aliases`）；个人号才有，
    群/系统号/多数公众号为空串。`with_alias=False` 可跳过一次 DB 读（省时）。

    参数 `adapter` ＝ `agent.wechat.WeChatAdapter`（要用它的 `_db`）。

    失败语义：**读不出来就抛**（`RuntimeError`）—— 绝不返回空列表冒充"没有会话"
    （同 `replica_adapter.load_groups` 的口径：空列表＝事实，抛异常＝读失败）。
    """
    from . import replica_adapter as _ra
    db = getattr(adapter, "_db", None)
    if db is None:
        raise RuntimeError("会话库不可用（adapter._db 为空）")
    rel = _session_db_rel(db)
    if not rel:
        raise RuntimeError("找不到 session.db 分片")
    conn = _ra.open_shard(db, rel)
    if conn is None:
        raise RuntimeError("打不开 session.db（无密钥？）")
    # ⭐ **列存在性自证**：不同微信版本的表结构可能不同 ⇒
    #   缺 `last_timestamp` 就**如实报"测不了"**（`pinned=None`），⛔ **不许默认成 False** ——
    #   那会把"不知道"说成"没置顶"（三态纪律：是/不是/测不了 必须分开）。
    _has_last = _table_has_column(conn, "SessionTable", "last_timestamp")
    try:
        rows = conn.execute(
            "SELECT %s, %s, %s, %s FROM SessionTable"
            % (_COL_USER, _COL_SORT, _COL_HIDDEN,
               "last_timestamp" if _has_last else "NULL")
        ).fetchall()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("读 SessionTable 失败：%s" % (str(e)[:80] or type(e).__name__))
    finally:
        _ra.close_all([conn])

    nickname_map, group_map = {}, {}
    try:
        nickname_map = _ra.load_nickname_map(db) or {}
    except Exception:
        nickname_map = {}
    try:
        for g in (_ra.load_groups(db) or []):
            gid = str(g.get("wxid") or "")
            if gid:
                group_map[gid] = str(g.get("name") or gid)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        group_map = {}
    alias_map = load_aliases(adapter) if with_alias else {}

    # ⭐ **已置顶名单（记住）**：`sort_ts > last_ts` 只在"刚置顶、之后还没来消息"时成立 ——
    #   一旦该会话来了新消息，`last_ts` 会追上 `sort_ts`，两者相等 ⇒ **判据失效**。
    #   ⇒ 把**曾经观察到置顶**的会话记下来（按 username）。
    #   ⛔⛔ **名单只证明"曾观察到"，不证明"现在还是"**（真机实测：某会话早已 `sort == last`，
    #     而名单仍把它报成置顶）⇒ 名单给出的 True **可能是假阳性**，成因两种、DB 分不出：
    #     ① 置顶后又来了新消息；② 用户**已取消置顶**。
    #   ⇒ 所以结论要**带依据等级**（`pinned_state()` 返回的 `how`）：需要确定性的消费方
    #     **必须**看 `pinned_how == "sort_gt_last"`，别只看 `pinned`。
    #   ⛔ 只增不减：解置顶需要用户显式操作（或另找信号），**不猜**。
    _seen_pinned = _load_pinned()
    _new_pinned = []

    out = []
    for r in rows:
        try:
            d = dict(r) if hasattr(r, "keys") else None
            u = str((d.get(_COL_USER) if d else r[0]) or "")
            ts = int((d.get(_COL_SORT) if d else r[1]) or 0)
            hid = int((d.get(_COL_HIDDEN) if d else r[2]) or 0)
            # ⭐ **置顶的判据（真机实测）**：`sort_timestamp > last_timestamp` ⇒ 被置顶。
            #   微信置顶的做法是**把该会话的 sort_timestamp 人为抬到最新**（超过它真实的
            #   `last_timestamp`）⇒ 于是它排到最前，而 DB 里**没有单独的置顶布尔列**。
            #   实测：刚置顶那个会话 `last_ts=1791118137 / sort_ts=1791217611`（差 ≈27.6 小时），
            #   其余 12 条**两者完全相等**。
            #   ⛔ 别去 `status` 里找位 —— 实测 13 条的 `status` 全是 0。
            try:
                _last_ts = int((d.get("last_timestamp") if d else r[3]) or 0)
            except Exception:  # noqa: BLE001
                _last_ts = 0
            # 三态：能判 ⇒ True/False；缺列/读不到 ⇒ None（**测不了 ≠ 没置顶**）
            _pinned, _how = pinned_state(_last_ts, ts, u in _seen_pinned, _has_last)
        except Exception:
            continue
        if not u:
            continue
        if hid and not include_hidden:
            continue
        if (not include_gh) and (not _is_top_level(u)):
            continue
        sys_ = (u in SYSTEM_USERS) and (u not in USABLE_SPECIAL)
        if sys_ and not include_system:
            continue
        out.append({
            "username": u,
            "name": _name_of(u, nickname_map, group_map),
            "alias": str(alias_map.get(u) or ""),   # ★自定义微信号；空 ⇒ 没改过（界面显示 wxid_*）
            "wechat_id": display_wechat_id(u, alias_map.get(u) or ""),  # ★界面上「微信号」栏显示的
            "sort_ts": ts,
            "hidden": bool(hid),
            "system": bool(sys_),
            "pinned": _pinned,   # ⭐ True/False/None；**含义见 pinned_state()**（None＝测不了）
            "pinned_how": _how,  # ⭐ 依据等级：sort_gt_last(正面证据) / seen_only / no_evidence / no_column
            "pinned_seen": bool(u in _seen_pinned),  # 是否在"曾观察到置顶"的名单里
        })
    # 排序：sort_timestamp DESC 为主键；username 为二级键（并列时保稳定，见 docstring 硬伤 4）
    out.sort(key=lambda x: (-int(x["sort_ts"] or 0), str(x["username"])))
    for i, x in enumerate(out):
        x["order"] = i
    # 把"这一轮新观察到的置顶"落盘（只增不减）
    #   ⛔ **只把正面证据（`sort_gt_last`）记进名单** —— 名单本身的 True 不能再喂回名单，
    #     否则一个假阳性会自我加固、永远洗不掉。
    try:
        _cur = _load_pinned()
        for x in out:
            if x.get("pinned_how") == "sort_gt_last" and x["username"] not in _cur:
                _cur.add(x["username"])
                _new_pinned.append(x["username"])
        if _new_pinned:
            _save_pinned(_cur)
    except Exception as _e:  # noqa: BLE001
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    return out


def pinned_state(last_ts, sort_ts, in_seen: bool, has_last: bool = True) -> tuple:
    """**置顶判定的唯一实现点** ⇒ `(pinned, how)`。

    ⛔ 为什么必须同时给出"依据等级"：两种来源强度完全不同，把它们混成一个 True 会骗人。
      · `"sort_gt_last"` —— **正面证据**：`sort_timestamp > last_timestamp`
        （微信置顶的做法＝把该会话的 `sort_timestamp` 人为抬到最新，DB 里**没有置顶布尔列**）。
      · `"seen_only"` —— **只记过、此刻证不了**：在"曾观察到置顶"的名单里，但此刻 `sort == last`。
        两种成因 DB **分不出来**：① 置顶后又来了新消息（`last` 追上 `sort`）；② 用户**已取消置顶**
        ⇒ 它**不是**"现在还是置顶"的证据（真机实测过这个组合）。
      · `"no_evidence"` —— `sort == last` 且不在名单里；同样分不出"从没置顶"与"置顶很久后来了消息"。
      · `"no_column"` —— 这个微信版本的表里没有 `last_timestamp` ⇒ **测不了**（`pinned=None`）。

    返回的 `pinned` 沿用布尔（`seen_only` 也算 `True`，兼容既有消费方）；
    ⛔ **需要确定性的消费方（如"靠置顶把目标顶到第一条再点它"）必须看 `how == "sort_gt_last"`**。
    """
    if not has_last:
        return None, "no_column"
    if last_ts and int(sort_ts or 0) > int(last_ts):
        return True, "sort_gt_last"
    if in_seen:
        return True, "seen_only"
    return False, "no_evidence"


def _table_has_column(conn, table: str, col: str) -> bool:
    """这张表有没有这一列 —— **缺列时不许当 False 用**（不同微信版本表结构可能不同）。

    为什么要它：`pinned` 的判据依赖 `last_timestamp`；某个版本没有这列时，
      若不检查就会 `SELECT` 报错或拿到 NULL ⇒ 把「测不了」说成「没置顶」。
      ⇒ 先自证列存在，缺了就**如实报 None**。
    """
    try:
        return any(str(r[1]) == col for r in conn.execute("PRAGMA table_info(%s)" % table))
    except Exception as _e:  # noqa: BLE001
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return False


def _pinned_store_path() -> str:
    try:
        from .config import DATA_DIR
        return os.path.join(DATA_DIR, "pinned_sessions.json")
    except Exception:  # noqa: BLE001
        return ""


def _load_pinned() -> set:
    """读"曾观察到置顶"的会话集合（按 username）。读不到 ⇒ 空集（不报错）。"""
    try:
        p = _pinned_store_path()
        if p and os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                return set(json.load(fh).get("pinned") or [])
    except Exception:  # noqa: BLE001
        pass
    return set()


def _save_pinned(s) -> None:
    """落盘（原子写；失败只记日志，不影响调用方）。"""
    try:
        p = _pinned_store_path()
        if not p:
            return
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = persist.tmp_for(p)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"pinned": sorted(s)}, fh, ensure_ascii=False)
        # ⛔ 不许裸 `os.replace`：Windows 上目标档被任何人打开着就撞 WinError 5/32
        #   （实测共享冲突下裸调用点 **71% 的写会静默丢掉**）⇒ 走项目的退避重试实现。
        #   本处外层 `except` 只为"绝不因为台账写不进去而影响发送"，与退避重试不冲突。
        #   （`persist` 已在模块顶部导入；这里不再局部重复导入 —— 局部导入正是上面那个 bug 的成因。）
        persist.replace_into(tmp, p)
    except Exception:  # noqa: BLE001
        pass


def order_of(adapter, username: str, **kw) -> int:
    """某会话在**屏幕列表**里的 0 基行号（找不到给 -1）。"""
    u = str(username or "")
    for s in list_sessions(adapter, **kw):
        if s["username"] == u:
            return int(s["order"])
    return -1


def self_username(adapter) -> str:
    """★ **本机登录账号**的 `wxid_*`（读 `adapter._db.account`，形如 `wxid_xxx_<4位后缀>`）。

    用途：**防止把"自己"当成"联系人"** —— 本机号自己的 `contact` 行也在库里，
    且它同样有 alias / 昵称，**极易被误认成某个联系人**。
    """
    db = getattr(adapter, "_db", None)
    if db is None:
        return ""
    acc = str(getattr(db, "account", "") or "")
    if not acc:
        return ""
    # 账号目录名可能是 `wxid_xxx_<4位后缀>`；取 `wxid_` 起、到最后一个 `_` 前
    m = acc
    if m.startswith("wxid_"):
        i = m.rfind("_")
        if i > 5:
            tail = m[i + 1:]
            if len(tail) <= 6 and all(c in "0123456789abcdef" for c in tail.lower()):
                m = m[:i]
    return m


def is_self(adapter, username: str) -> bool:
    """这条会话是不是**本机登录账号自己**。"""
    s = self_username(adapter)
    return bool(s) and str(username or "") == s


def find_session(adapter, query: str, **kw) -> dict:
    """**按"任一标识"找会话** ⇒ 命中项 或 `None`。★推荐用 `username`（稳定）。

    `query` 依次匹配（**顺序即优先级**）：
        ① `username`（`wxid_*`/`@chatroom`/`gh_*`/系统 key）—— **精确，推荐**
        ② `alias`（**自定义微信号**，改过的人才有）—— 精确、不经用户改
        ③ `wechat_id`（**界面上「微信号」栏显示的那串** = alias 或回退 wxid）—— 精确
        ④ `name`（显示名 = 群名/昵称）—— **模糊**，改名/重名会误中

    ⚠️ **不要指望"界面上叫什么"能当唯一钥匙**（本次教训）：显示名会改、会重名。
    """
    q = str(query or "").strip()
    if not q:
        return None
    sessions = list_sessions(adapter, **kw)
    for s in sessions:
        if s["username"] == q:
            return s
    for s in sessions:
        if s["alias"] and s["alias"] == q:
            return s
    for s in sessions:
        if s["wechat_id"] and s["wechat_id"] == q:
            return s
    for s in sessions:
        if s["name"] == q:
            return s
    return None


def describe(adapter, **kw) -> list:
    """给"看清全貌"用：每行含 `order/username/alias/wechat_id/name/ts/hidden/is_self`。"""
    self_u = self_username(adapter)
    out = []
    for s in list_sessions(adapter, **kw):
        out.append({
            "order": s["order"],
            "username": s["username"],
            "alias": s["alias"],
            "wechat_id": s["wechat_id"],
            "name": s["name"],
            "ts": s["sort_ts"],
            "hidden": s["hidden"],
            "is_self": bool(self_u) and s["username"] == self_u,
        })
    return out


def row_y(sessions: list, order: int, first_row_y: int, row_pitch: int) -> int:
    """**屏幕第 `order` 行的 y 坐标** ＝ 首行 y + order × 行高。

    行高**必须按实测给**（本机实测 97~104，均值 ~98）——
    ⛔ 项目常量 `chat_ocr.ROW_PITCH=64` 是错的（差 ~34px），**不许用它**。

    ⛔ **不许拿它直接去点会话行**（本函数保留只作"算几何"用）。
      会话列表的排序键是 `sort_timestamp` —— **来一条新消息就重排**。
      于是"读顺序 → 算 y → 投点击"这三步之间**必然存在窗口期**：只要中间插进一条新消息，
      算出来的 y 指向的已经是**下一行**（点下去就打开了别的会话，接着发出去就是**发错人**；
      这是不可逆的）。真机已出过一次这类事故。
      ⇒ 要点某一行的正确做法是：**当场抓一帧、按名字认出那一行、在同一帧上算 y 再点**
        （见 `chat_ocr.find_row_info` / `wechat._click_visible_session`）。
      本函数目前**没有任何调用方**，请保持这样。
    """
    return int(first_row_y) + int(order) * int(row_pitch)
