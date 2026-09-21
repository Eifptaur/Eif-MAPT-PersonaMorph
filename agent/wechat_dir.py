# -*- coding: utf-8 -*-
"""「微信数据目录」的唯一权威：**校验 / 探测 / 决策 / 暴露**。

用户反馈（2026-09-18 22:23，控制台「反馈」面板原文）：
    「能不能让我自己选微信的地址，自己自定义的地址他检测不到，移动回默认地址后好了，
      但是监听后没反应，重启后又连接不上了，通过文件夹中的脚本检查出来的报告显示，
      他回我之前自定义的地址里去看文件了」

拆成四件事，少一件用户还是看不出来（本模块只做这四件，不碰窗口、不碰驱动库）：
  ① **校验**：填进来的目录必须**存在**且**里面有 `db_storage` 或消息库文件**（`check`）。
     不通过就明确给出原因，绝不"看着像没事"就用了。
  ② **决策**：优先级＝**显式配置（通过校验）> 扫盘探到的最新可用目录 > 驱动库自探测**（`decide`）。
  ③ **失效**：每次决策都**现读当前配置 + 现扫盘**，不缓存任何一条结论 —— 配置一变，旧路径立刻作废
     （用户那句"他回我之前自定义的地址里去看文件了"就是旧值继续生效的症状）。
  ④ **暴露**：报告 / 控制台 / `/api/status` 显示的是**当前实际生效的目录**（不是配置值），
     不可用时写明原因与回落到哪（`status` / `line`）。

与既有代码的关系（不另起一套）：
  · 候选目录表复用 `agent/wechat.py::_db_dir_candidates`（家目录 / 系统真文档 / OneDrive / 各固定盘）；
  · 路径展开复用 `wechat.py::_expand_path`（`%VAR%`、`~`、成对引号）；
  · `wechat.py::db_open_tries`/`open_db` 那条三档链**不动**（配置那条仍然照试，试不了才退），
    本模块管的是"**该用哪个 / 实际在用哪个 / 为什么没用那个**"，给日志、报告与控制台用。
"""
from __future__ import annotations

import os
import time

from . import wechat as _W

# 扫一个目录最多看多少个文件（防网络盘/超大目录把控制台拖住；够判断"有没有库文件"了）
_WALK_BUDGET = 6000
_ACCEPT_NAMES = ("db_storage",)


def _same(a: str, b: str) -> bool:
    """两个路径是不是同一个（只做规范化，不碰盘；任一端为空 ⇒ 不算同一个）。"""
    try:
        if not a or not b:
            return False
        return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))
    except Exception:
        return False


def expand(p: str) -> str:
    """展开人填进来的路径（环境变量 / `~` / 成对引号），复用既有实现。"""
    try:
        return str(_W._expand_path(p) or "").strip()
    except Exception:
        return str(p or "").strip()


def scan(p: str) -> dict:
    """**只读**扫一个目录：有没有 `db_storage`、有多少 `.db`、最新的 `.db` 什么时候写的。

    走法是**照微信 4.x 的真结构**（`<xwechat_files>/<账号>/db_storage/<库>/`）：
      ① 先在 `<p>` 底下找带 `db_storage` 的账号目录，**只往 db_storage 里走**（那边才是库，
         旁边全是图片/视频缓存，整棵树扫会很慢）；
      ② 用户直接填到 `db_storage` 这一层也算对（既有口径：填哪一层都算对）；
      ③ 都不像时再整棵树找 `.db`，带**预算法**（`_WALK_BUDGET`）防止被超大目录拖住。
    """
    out = {"path": p, "exists": False, "is_dir": False, "has_db_storage": False,
           "dbs": 0, "newest": 0.0}
    if not p:
        return out
    try:
        out["exists"] = os.path.exists(p)
        out["is_dir"] = os.path.isdir(p)
    except Exception:
        return out
    if not out["is_dir"]:
        return out
    base = os.path.normpath(p)
    roots = []
    try:
        for name in os.listdir(base):
            if name.lower() in _ACCEPT_NAMES and os.path.isdir(os.path.join(base, name)):
                out["has_db_storage"] = True
                roots.append(os.path.join(base, name))
            elif os.path.isdir(os.path.join(base, name, "db_storage")):
                out["has_db_storage"] = True
                roots.append(os.path.join(base, name, "db_storage"))
    except Exception:
        pass
    if os.path.basename(base).lower() in _ACCEPT_NAMES:
        out["has_db_storage"] = True
        if base not in roots:
            roots.insert(0, base)
    if not roots:
        roots = [base]                     # 兜底：整棵树找（带预算）
    seen = 0
    newest = 0.0
    stop = False
    for r in roots:
        if stop:
            break
        try:
            for root, dirs, files in os.walk(r):
                for f in files:
                    seen += 1
                    if seen > _WALK_BUDGET:
                        stop = True
                        break
                    if not f.lower().endswith(".db"):
                        continue
                    out["dbs"] += 1
                    try:
                        m = os.path.getmtime(os.path.join(root, f))
                        if m > newest:
                            newest = m
                    except OSError:
                        pass
                if stop:
                    break
        except Exception:
            continue
    out["newest"] = newest
    return out


def _acct_layer(p: str) -> dict:
    """`p` 是不是**账号层**（填到了一个具体的微信号那一层）+ 该建议改填的**上一级**。

    为什么单独做这一件事（2026-09-19，网友反馈「大号能连、小号连不上」的行业口径）：填到账号层
    ＝**把某个号钉死**（`pick_account` 里 `pinned` 那条），微信切号之后它还照着这个号读 ⇒ 新消息
    一条都进不来。系统不报错、其它检查全绿，用户只能看到"连不上/不回复"。
    只读，代价＝一次 `isdir`；真判成账号层时才多一次 `listdir` 数同级账号。

    返回 `{"is_account","name","up","siblings"}`（判不出来时 `is_account=False`）。
    """
    out = {"is_account": False, "name": "", "up": "", "siblings": 0}
    try:
        base = os.path.normpath(str(p or ""))
        if not base or not os.path.isdir(base):
            return out
        tail = os.path.basename(base).lower()
        if tail in _ACCEPT_NAMES:
            # 直接填到了 `db_storage` 这一层 ⇒ 账号目录是它的上一层，「上一级」要再往上走一层
            out["is_account"] = True
            out["name"] = "db_storage"
            out["up"] = os.path.dirname(os.path.dirname(base))
        elif os.path.isdir(os.path.join(base, "db_storage")):
            out["is_account"] = True
            out["name"] = os.path.basename(base)
            out["up"] = os.path.dirname(base)
        else:
            return out
        up = str(out["up"] or "")
        if up and os.path.isdir(up):
            n = 0
            try:
                for name in os.listdir(up):
                    if len(name) > 200:
                        break
                    if name.lower() in _ACCEPT_NAMES:
                        continue
                    if _same(os.path.join(up, name), base):
                        continue
                    try:
                        if os.path.isdir(os.path.join(up, name, "db_storage")):
                            n += 1
                    except OSError:
                        continue
            except OSError:
                n = 0
            out["siblings"] = n
    except Exception:
        return {"is_account": False, "name": "", "up": "", "siblings": 0}
    return out


def account_hint(p: str, al: dict = None) -> str:
    """「你填到账号层了，改填上一级」那句提示（**一处实现**：面板、保存回执、检验器共用）。

    ⚠️ 话术里**不写括号式解释**（`wechat_dir_selftest` E 段钉着这条）。
    """
    try:
        al = al if isinstance(al, dict) and al else _acct_layer(expand(p))
    except Exception:
        return ""
    if not al.get("is_account"):
        return ""
    up = str(al.get("up") or "")
    if not up:
        return ""
    n = int(al.get("siblings") or 0)
    if n >= 1:
        return ("这个目录是账号层，同级还有 %d 个账号目录；微信切号后它不会跟着走。"
                "建议「数据库目录」改填上一级 %s 让它自动跟随正在用的号" % (n, up))
    return ("这个目录是账号层；建议「数据库目录」填上一级 %s，"
            "以后切号或再登一个号时能自动跟随，填这一个号会一直读它" % up)


def check(p: str) -> dict:
    """**手动指定必须过这一关**：存在 + 里面有 `db_storage` 或消息库文件。

    返回 `{"path","ok","why","dbs","has_db_storage","newest","account_layer","account_up",
    "account_siblings","hint"}`；`ok=False` 时 `why` **必有话**（防静默：调用方不许在 `why`
    为空时判失败）。

    ⚠️「账号层」**不改判 `ok`**（填到账号层今天也能跑，只是切号后不跟随）⇒ 只给 `hint`，
    由控制台/报告如实说出来，不拦用户保存（口径见 `account_hint`）。
    """
    path = expand(p)
    c = scan(path)
    why = ""
    if not path:
        why = "还没填"
    elif not c["exists"]:
        why = "这个目录不存在"
    elif not c["is_dir"]:
        why = "这个路径不是一个目录"
    elif not c["has_db_storage"] and not c["dbs"]:
        why = "这个目录里既没有 db_storage 子目录，也没有任何 .db 消息库文件"
    out = dict(c)
    out["ok"] = (why == "")
    out["why"] = why
    _al = _acct_layer(path) if (path and c["is_dir"]) else {"is_account": False, "up": "", "siblings": 0}
    out["account_layer"] = bool(_al.get("is_account"))
    out["account_up"] = str(_al.get("up") or "")
    out["account_siblings"] = int(_al.get("siblings") or 0)
    out["hint"] = account_hint(path, _al) if out["ok"] else ""
    return out


def probe(extra: str = "") -> dict:
    """探一遍候选目录（含人填的那个），每个都给出**是否可用 + 为什么**。只读，不写配置。"""
    ex = expand(extra)
    out = []
    try:
        cands = list(_W._db_dir_candidates(extra) or [])
    except Exception:
        cands = []
    if ex and ex not in cands:
        cands.insert(0, ex)
    seen = set()
    for p in cands:
        try:
            key = os.path.normcase(os.path.normpath(str(p)))
        except Exception:
            key = str(p)
        if not p or key in seen:
            continue
        seen.add(key)
        c = check(p)
        c["source"] = "配置" if (ex and str(p) == ex) else "自动检测"
        out.append(c)
    usable = [c for c in out if c["ok"]]
    return {"configured": ex, "candidates": out, "usable": usable}


# ── 多账号（切换微信号 / 多开）：**哪个账号目录正在被用** ─────────────────────────────
# 网友反馈（2026-09-19，附检验报告）：「切换微信号使用后，提示寻找不到库，还要求给予相同的权限」
#   「只有前几句话会正常回复，后面不再回复」。
# 现场（`wechatauto/db.py::_pick_account`）：驱动库按**账号目录里最新 `.db` 的 mtime** 挑账号，
#   而**切走的那一刻微信会把旧账号的库 checkpoint 一遍** ⇒ 旧账号的 `.db` 反而最新
#   ⇒ 挑中**已经不在用的那个账号**，我们跟着读它的库。后果正好两条：
#     ① 旧账号的缓存密钥还能过页1校验 ⇒ 驱动库的账号自愈（`if still:` 那条）**根本不触发**
#        ⇒ **静默**读旧库：新消息全在新账号的库里 ⇒ 监听像死了一样（「后面不回复」）；
#     ② 旧账号的密钥对不上时 ⇒ 它报「数据库无可用密钥…②本程序权限低于微信（微信以管理员运行时…）」
#        ⇒ 用户在控制台看到的就是「找不到库 + 要权限」（那句"权限"是**提示语里的一种可能**，
#        不是真相 —— 真相是选错了账号）。
# ⇒ 我们**自己挑账号**再显式传 `account=`：判据换成 **-wal（库正在被写）**。
#   `.db` 主库可能几小时不 checkpoint（微信平时只写 -wal），所以 mtime 会骗人；
#   `-wal/-shm` 的写入时间才是"这个账号现在被用着"的直接证据。
# ⚠️ 只读：这里**不碰**驱动库、不写配置（配置那条仍由 `check`/`save` 把关）。
_LIVE_WINDOW_S = 180.0     # -wal 在这个窗口内被写过 ⇒ 判"这个账号正在被用"
_ACCT_WALK_BUDGET = 20000  # 防网络盘/超大目录把控制台轮询拖住


def accounts(parent: str) -> list:
    """列出 `parent` 底下的**账号目录**（含 `db_storage`），每个带写入证据。只读。

    证据口径（`wal` 优先，`db` 只当兜底）：
      `wal` ＝ 该账号库里所有 `-wal/-shm` 的**最新 mtime** ⇒ "库正在被写"；
      `db`  ＝ 各 `.db` 主库的最新 mtime ⇒ **切号时旧账号会被 checkpoint**，这一项会骗人。
    `parent` 本身就是一个账号目录（用户直接填到账号那一层）时，只返回它自己。

    返回 `[{"name","dir","wal","db","live"}]`，`live = wal or db`；目录不可读时返回 `[]`。
    """
    p = expand(parent)
    out = []
    if not p:
        return out
    try:
        if not os.path.isdir(p):
            return out
    except Exception:
        return out
    dirs = []
    try:
        if os.path.isdir(os.path.join(p, "db_storage")):
            dirs = [p]
        else:
            for name in sorted(os.listdir(p)):
                d = os.path.join(p, name)
                try:
                    if os.path.isdir(os.path.join(d, "db_storage")):
                        dirs.append(d)
                except OSError:
                    continue
    except OSError:
        return out
    for d in dirs:
        wal = db = 0.0
        seen = 0
        try:
            for root, _dirs, files in os.walk(os.path.join(d, "db_storage")):
                for f in files:
                    seen += 1
                    if seen > _ACCT_WALK_BUDGET:
                        break
                    low = f.lower()
                    if not (low.endswith(".db") or low.endswith("-wal") or low.endswith("-shm")):
                        continue
                    try:
                        m = os.path.getmtime(os.path.join(root, f))
                    except OSError:
                        continue
                    if low.endswith("-wal") or low.endswith("-shm"):
                        if m > wal:
                            wal = m
                    elif m > db:
                        db = m
                if seen > _ACCT_WALK_BUDGET:
                    break
        except OSError:
            pass
        out.append({"name": os.path.basename(d), "dir": d, "wal": wal, "db": db,
                    "live": wal or db})
    return out


def pick_account(parent: str, prefer: str = "") -> dict:
    """挑**该用哪个账号**：正在被写的那个 > 你填的那个（填到账号层＝钉死）> 写入最新。

    返回 `{"name","dir","why","note","accounts":[…],"fresh":[…],"pinned":bool}`；
    `parent` 底下一个账号目录都没有时返回 `{}`（调用方照旧让驱动库自探测）。
    为什么这么排：**用户的真实目标是"读我正在用的那个号的群"**，而 `.db` 的 mtime 在
    切号那一刻会指向已经不在用的旧号（见本段顶部注释）—— 这条优先于"我填的哪个"，
    但**填到账号目录这一层**（`…\\wxid_xxx_482e`）＝ 明确钉死一个号，这时照填的来，只留一句 note。
    """
    accs = accounts(parent)
    if not accs:
        return {}
    now = time.time()
    fresh = [a for a in accs if a["wal"] and (now - a["wal"]) <= _LIVE_WINDOW_S]
    fresh.sort(key=lambda a: a["wal"], reverse=True)
    pref_dir = expand(prefer)
    pinned = {}
    for a in accs:
        if pref_dir and _same(a["dir"], pref_dir):
            pinned = a
            break
    if pinned:
        _warn = ("你钉死了账号 %s，但**正在写的是 %s**（-wal %s）⇒ 若它不回复，把「数据库目录」"
                 "填成上一级目录让它自动跟随"
                 % (pinned["name"], fresh[0]["name"], _fmt_ts(fresh[0]["wal"]))
                 if (fresh and fresh[0]["name"] != pinned["name"]) else "")
        return {"name": pinned["name"], "dir": pinned["dir"],
                "why": "你填的账号目录", "note": _warn, "pinned": True,
                "accounts": accs, "fresh": [a["name"] for a in fresh]}
    if fresh:
        a = fresh[0]
        why = ("这个账号的库刚刚还在写（-wal %s）" % _fmt_ts(a["wal"]))
        if len(fresh) > 1:
            why += "；另有 %d 个账号也在写，取写得最新那个" % (len(fresh) - 1)
        return {"name": a["name"], "dir": a["dir"], "why": why, "note": "",
                "pinned": False, "accounts": accs, "fresh": [x["name"] for x in fresh]}
    # ⚠️ 都不"新鲜"时**照样按 -wal 比**，不许退回 `.db`（本机实测踩到：微信闲置 6 分钟，正在用的那个号
    #    的 -wal 就超出 180 秒窗口了；此时如果退回 `.db`，就正好落进"切号时旧号被 checkpoint、`.db` 最新"
    #    那个陷阱）。两个号都没在写时，**谁的 -wal 更晚**才是"最近还在被用"的证据。
    a = max(accs, key=lambda x: (float(x.get("wal") or 0.0), float(x.get("db") or 0.0), x["name"]))
    _others = [x for x in accs if x["name"] != a["name"]]
    if a.get("wal"):
        why = ("两个号现在都没在写（-wal 都超过 %d 秒没动）⇒ 按**最近写过 -wal 的那个**挑：%s；"
               "另一个号最后一次写 -wal 是 %s（`.db` 主库时间会被「切走时 checkpoint」骗，所以不看它）"
               % (int(_LIVE_WINDOW_S), _fmt_ts(a["wal"]),
                  _fmt_ts(_others[0]["wal"]) if _others else "-"))
    else:
        why = ("这台机器上任何账号都没有 -wal ⇒ 退回按最新 .db 挑：%s" % _fmt_ts(a["db"]))
    return {"name": a["name"], "dir": a["dir"], "pinned": False, "accounts": accs,
            "fresh": [], "why": why, "note": ""}


def _fmt_ts(ts) -> str:
    """时间戳 → `HH:MM:SS（N 分钟前）`；非法值给 `-`。"""
    try:
        t = float(ts)
    except Exception:
        return "-"
    if t <= 0:
        return "-"
    try:
        ago = max(0, int(time.time() - t))
        human = ("%d 秒前" % ago) if ago < 90 else ("%d 分钟前" % (ago // 60)) \
            if ago < 5400 else ("%d 小时前" % (ago // 3600))
        return "%s（%s）" % (time.strftime("%H:%M:%S", time.localtime(t)), human)
    except Exception:
        return "-"


def configured_path() -> str:
    """配置里那条「数据库目录」（**现读、不缓存** —— 2026-09-18 用户那句「他回我之前自定义的地址里去看
    文件了」要的就是"旧值立刻失效"）。拿不到配置就返回空串。"""
    try:
        from .config import get_config
        return str(((get_config() or {}).get("wechat") or {}).get("db_dir") or "")
    except Exception:
        return ""


def switched(mine: str, parent: str, window_s: float = _LIVE_WINDOW_S, pin=None) -> dict:
    """**要不要跟着切号**（监听循环每 15 秒问一次；只读、便宜）。

    保守四条（宁可不切，也不许来回抖 —— 多开时两个号同时活着很常见）：
      ① 只有一个账号目录 ⇒ 永不切；
      ② 我正在读的那个号**自己的 -wal 还新鲜** ⇒ 不切；
      ③ **你把某个账号目录钉死了** ⇒ 不切（切了还是它 ⇒ 会变成每 15 秒重连一次的循环）；
      ④ 目标号＝**`pick_account()` 挑出来的那一个**（唯一来源，V-R10-28）——"别的号明显在写、
         我这个已经静默"与"两个号都没在写"都走它；只有"全机器都没有 -wal 证据"时才不动。
    返回 `{"stale":bool, "mine","live","why","accounts"}`；`mine` 为空（不知道在读哪个）时不切。
    `pin`：显式配置那条路径（不传就现读配置）。
    """
    out = {"stale": False, "mine": str(mine or ""), "live": "", "why": "", "accounts": []}
    accs = accounts(parent)
    if len(accs) < 2 or not out["mine"]:
        return out
    out["accounts"] = [a["name"] for a in accs]
    if pin is None:
        pin = configured_path()
    try:
        _pk = pick_account(parent, pin)
    except Exception:
        _pk = {}
    if _pk.get("pinned") and _pk.get("name") == out["mine"]:
        out["why"] = ("你把账号目录钉死了（%s）⇒ 不跟着切；想跟随就把「数据库目录」填成上一级目录"
                      % out["mine"])
        return out
    me = [a for a in accs if a["name"] == out["mine"]]
    if not me:
        # 我读的那个账号目录已经不在了（被删/改名）⇒ 换到正在写的那个
        a = sorted(accs, key=lambda x: x["live"], reverse=True)[0]
        out.update(stale=True, live=a["name"],
                   why="原来在读的账号目录 %s 不见了" % out["mine"])
        return out
    now = time.time()
    if me[0]["wal"] and (now - me[0]["wal"]) <= window_s:
        return out                      # ② 我自己还活着 ⇒ 不动（多开时不许来回抖）
    # ⛔ 2026-09-22 修 **V-R10-28（P1）**：这里原来是「别的号里 -wal 新鲜的」列表 + `if not others: return out`
    #   ⇒ **两个号都没在写时永不跟切**；而同一夹具下 `pick_account()` 能挑对号 ⇒ **同一事实两条路相反**。
    #   用户故事：切到 B 号后 B 号短期没收到消息 ⇒ 我们继续读 A 号旧库、**无任何异常**，非得"新号先收到
    #   一条消息"才自愈 —— 而它盯的正是"读不到消息的那个库"（自指）。
    #   ⇒ 现在**唯一来源**：该切到哪个号＝`pick_account()` 的答案（上面已算好的 `_pk`），规则④只是它的执行者。
    #   唯一保留的保守分支：**全机器都没有 -wal 证据**时不动（`.db` 会被「切走时 checkpoint」骗，宁可不切）。
    _wanted = str(_pk.get("name") or "")
    _wl = [x for x in accs if x["name"] == _wanted]
    if not _wanted or _wanted == out["mine"] or not _wl:
        return out
    a = _wl[0]
    if _pk.get("pinned"):
        out.update(stale=True, live=a["name"],
                   why=("你把账号目录钉死在 %s ⇒ 跟着切到它（现在读的是 %s）" % (a["name"], out["mine"])))
        return out
    if not (a.get("wal") and (not me[0].get("wal") or float(a["wal"]) > float(me[0]["wal"]))):
        return out
    out.update(stale=True, live=a["name"],
               why=("在读的账号 %s 已经 %s 没往库里写了，而账号 %s 的 -wal 是 %s"
                    "⇒ 微信像是切到了 %s"
                    % (out["mine"], _fmt_ts(me[0]["wal"]), a["name"], _fmt_ts(a["wal"]), a["name"])))
    return out


def pick_latest(cands: list) -> dict:
    """在可用候选里挑**最新的那个**（按库里最新 `.db` 的写入时间；并列时按候选顺序）。"""
    best = None
    for i, c in enumerate(cands or []):
        if not c or not c.get("ok"):
            continue
        key = (float(c.get("newest") or 0.0), -i)
        if best is None or key > best[0]:
            best = (key, c)
    return best[1] if best else {}


def decide(explicit=None, probe_all: bool = True) -> dict:
    """**该用哪个目录**（优先级：显式配置 > 扫盘最新可用 > 驱动库自探测）+ 为什么。

    `explicit=None` ⇒ 现读当前配置（**不缓存**，这正是"配置变了旧值必须失效"的落点）。
    `probe_all=False` ⇒ 配置那条过校验时直接收工、连扫盘都不扫（`/api/status` 会被轮询，
    而"实际在用哪个"由运行中的 adapter 那份 `_db_how` 说得更准，没必要每次去盘上重算）。
    """
    if explicit is None:
        explicit = ""
        try:
            from .config import get_config
            explicit = str(((get_config() or {}).get("wechat") or {}).get("db_dir") or "")
        except Exception:
            explicit = ""
    ex = expand(explicit)
    c_ex = check(ex) if ex else {"path": "", "ok": False, "why": "还没填", "dbs": 0, "newest": 0.0}
    out = {"configured": ex, "configured_ok": bool(c_ex["ok"]), "configured_why": str(c_ex["why"]),
           "configured_dbs": int(c_ex.get("dbs") or 0), "hint": "",
           "candidates": [], "effective": "", "src": "", "note": "",
           "source_text": "驱动库自探测"}
    if ex and c_ex["ok"]:
        # 显式配置且**过校验** ⇒ 用它（哪怕扫盘还有别的）——「我自己选的地址」优先
        out["effective"] = ex
        out["src"] = "config"
        out["source_text"] = "你填的目录"
        # ⚠️ 配置可用但**填到了账号层**：不拦，但必须当场说出来（切号后不跟随的真凶，网友 2026-09-19）
        out["hint"] = str(c_ex.get("hint") or "")
        out["note"] = out["hint"]
        return out
    if not probe_all:
        return out
    _p = probe(ex)
    out["candidates"] = _p["candidates"]
    latest = pick_latest(_p["usable"])
    if latest:
        out["effective"] = str(latest.get("path") or "")
        out["src"] = "scanned"
        out["source_text"] = "自动检测"
    else:
        out["src"] = "auto"
    if ex and not c_ex["ok"]:
        # **不许静默**：填了却用不了，必须说清"你的目录为什么不行 + 现在实际用哪个"
        out["note"] = ("配置的目录 %s 不可用：%s ⇒ 已回落到 %s"
                       % (ex, c_ex["why"], out["effective"] or "驱动库自探测"))
    if not out["note"] and out["effective"]:
        # 实际在用的那个也可能是账号层（没填配置、或回落到了别处）⇒ 同一句话术，一处实现
        _h = str((latest or {}).get("hint") or "") or account_hint(out["effective"])
        if _h:
            out["hint"] = _h
            out["note"] = _h
    return out


def status(how: dict = None, explicit=None, dir_info: dict = None) -> dict:
    """给 `/api/status`、控制台与检验报告用的一份**权威结论**。

    `how` ＝ 运行中的 adapter 的 `_db_how`（**它才是"实际在用哪个"的铁证**，比"应该用哪个"更权威）。
    """
    _has_how = bool(isinstance(how, dict) and (how.get("dir") or how.get("src")))
    d = decide(explicit, probe_all=not _has_how)
    if _has_how:
        _d, _s = str(how.get("dir") or ""), str(how.get("src") or "")
        if _d or _s:
            d["effective"] = _d
            d["src"] = _s
            d["effective_from"] = "running"
            _txt = {"config": "你填的目录", "scanned": "自动检测", "auto": "驱动库自探测"}
            d["source_text"] = _txt.get(_s, _s or "未知来源")
    else:
        d["effective_from"] = "planned"
    if isinstance(dir_info, dict) and dir_info.get("note") and not d.get("note"):
        d["note"] = str(dir_info.get("note") or "")
    if d["configured"] and not d["configured_ok"] and not d.get("note"):
        # 配置不可用、又没扫盘（有活 adapter 时走这条）：回落目标就是"实际在用的那个"
        if d["effective"] and _same(d["effective"], d["configured"]):
            # 兜底路：校验没过、驱动库却把它开起来了 ⇒ 现在读的就是它，实话实说
            d["note"] = ("你填的目录 %s 没通过校验：%s ⇒ 现在仍然按它读，建议在控制台改掉"
                         % (d["configured"], d["configured_why"] or "用不了"))
        else:
            d["note"] = ("配置的目录 %s 不可用：%s ⇒ 已回落到 %s"
                         % (d["configured"], d["configured_why"] or "用不了", d["effective"] or "驱动库自探测"))
    d["now"] = d["effective"] or "驱动库自探测到的目录"
    d["ok"] = bool(d["effective"]) and (d["configured_ok"] or not d["configured"])
    # ── 账号这一维（2026-09-19 加，网友反馈「切换微信号后找不到库 / 后面不回复」）──────────
    # 只在"运行中的 adapter 告诉了我们它在读哪个账号"或"本次真的去盘上算了"时才给；
    # /api/status 每 4 秒轮询，**绝不能在这里走盘**（那是把控制台拖住的写法）。
    _acct = str((how or {}).get("account") or "") if _has_how else ""
    if _acct:
        d["account"] = _acct
        d["account_from"] = "running"
        _als = (how or {}).get("accounts_live") or []
        d["account_live"] = _acct in _als if _als else None
        d["account_why"] = str((how or {}).get("account_why") or "")
        if (how or {}).get("account_names"):
            d["account_names"] = list(how["account_names"])
        if (how or {}).get("account_note") and not d.get("note"):
            d["note"] = str(how.get("account_note"))
        if d["account_live"] is False and _als:
            _t = "；".join(_als)
            d["account_note"] = ("正在读的账号 %s 的库没在动，而 %s 在写 ⇒ 微信可能已经切号："
                                 "新版会在 15 秒内自动跟着切过去；旧版本请重启一次机器人"
                                 % (_acct, _t))
            d["note"] = d["note"] or d["account_note"]
    elif d.get("effective") and not _has_how:
        _pk = pick_account(d["effective"], d.get("configured") or "")
        if _pk:
            d["account"] = _pk["name"]
            d["account_from"] = "disk"
            d["account_why"] = _pk["why"]
            d["account_live"] = bool(_pk["fresh"]) and _pk["name"] in (_pk["fresh"] or [])
            d["account_names"] = [a["name"] for a in _pk["accounts"]]
            if _pk.get("note"):
                d["account_note"] = _pk["note"]
                d["note"] = d["note"] or _pk["note"]
    # ⚠️ 候选清单**不随 status 下发**：控制台每 4 秒轮询一次 /api/status，若这里带一个空清单，
    #    会把用户刚点「自动检测」探出来的那张列表**清空**（实测踩到）。要清单就调 `probe()`。
    d.pop("candidates", None)
    d["text"] = line(d)
    return d


def line(info: dict) -> str:
    """一行话术（报告 / 日志 / 控制台共用，**不写括号式解释**）。"""
    eff = str((info or {}).get("now") or "") or "驱动库自探测到的目录"
    src = str((info or {}).get("source_text") or "未知来源")
    note = str((info or {}).get("note") or "")
    base = "当前在读 %s，来源：%s" % (eff, src)
    # 账号这一维（2026-09-19）：多账号机器上"读的是哪个号"和"读的是哪个目录"一样要命
    # —— 切号后读旧号＝新消息一条都看不到（网友反馈「后面不回复」就是这么来的）。
    _a = str((info or {}).get("account") or "")
    if _a:
        _lv = (info or {}).get("account_live")
        # ⚠️ 这一行**不写括号式解释**（`wechat_dir_selftest` E 段钉着这条）⇒ 用中点接
        base += "，账号：%s%s" % (_a, " · 正在被写" if _lv else (" · 已静默" if _lv is False else ""))
    return (base + "；" + note) if note else base


def save(path: str, on_save=None) -> dict:
    """控制台「保存并重探」：**校验通过才写进配置**，不通过就把原因与回落目标返回去。

    返回 `{"ok","saved","error", ...decide(...)}`；`ok=False` 时配置**一个字都不动**。
    """
    p = expand(path)
    if p:
        c = check(p)
        if not c["ok"]:
            d = decide(p)
            return dict(d, ok=False, saved=False,
                        error="这个目录用不了：%s" % c["why"], reason=c["why"],
                        fallback=d.get("effective") or "")
    from .config import get_config, save_config, set_config
    import copy as _copy
    cfg = _copy.deepcopy(get_config() or {})
    cfg.setdefault("wechat", {})["db_dir"] = p
    set_config(cfg)
    save_config(cfg)
    if callable(on_save):
        try:
            on_save(cfg)
        except Exception:
            pass
    return dict(decide(p), ok=True, saved=True, error="", reason="")
