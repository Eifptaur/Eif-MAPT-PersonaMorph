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


def check(p: str) -> dict:
    """**手动指定必须过这一关**：存在 + 里面有 `db_storage` 或消息库文件。

    返回 `{"path","ok","why","dbs","has_db_storage","newest"}`；`ok=False` 时 `why` **必有话**
    （防静默：调用方不许在 `why` 为空时判失败）。
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
           "configured_dbs": int(c_ex.get("dbs") or 0),
           "candidates": [], "effective": "", "src": "", "note": "",
           "source_text": "驱动库自探测"}
    if ex and c_ex["ok"]:
        # 显式配置且**过校验** ⇒ 用它（哪怕扫盘还有别的）——「我自己选的地址」优先
        out["effective"] = ex
        out["src"] = "config"
        out["source_text"] = "你填的目录"
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
