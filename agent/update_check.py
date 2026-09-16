# -*- coding: utf-8 -*-
"""更新检查（群相 · **本机 UI 用**）—— 拉清单、判三态、给控制台显示公告条。

设计见 `docs/设计-本体与DLC.md` §五，四条口径（都是硬规矩）：
  ① **离线 / 拉不到 ⇒ 静默跳过**：不弹任何东西，也不在微信侧留任何痕迹；
  ② **清单坏了 ⇒ 如实说「更新源异常」**（不假装"已是最新"）；
  ③ **有新版 ⇒ 公告条**：当前版本 → 远端版本 + 要点（notes）+ 三选项（立即更新 / 稍后 / 不再提醒这个版本）；
  ④ **公告只在本机 UI 出现，绝不往微信侧发**（与风险闸门同一口径）。

只做**判断与状态**，不下载、不换文件（那是 `scripts/pm_update.py` 的活）。
"""
import json
import os
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_REL = os.path.join("data", "update_state.json")


def _cfg() -> dict:
    try:
        from .config import get_config
        return dict(get_config().get("update") or {})
    except Exception:
        return {}


def _state_path() -> str:
    return os.path.join(ROOT, STATE_REL)


def _read_state() -> dict:
    p = _state_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _write_state(d: dict) -> None:
    p = _state_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, p)
    except Exception:
        pass


def current_version() -> str:
    try:
        from .version import VERSION
        return str(VERSION)
    except Exception:
        return ""


def vtuple(v):
    """`2026.9.15.1` → (2026, 9, 15, 1)；非法值返回 ()。"""
    out = []
    for part in str(v or "").split("."):
        part = part.strip()
        if not part.isdigit():
            return ()
        out.append(int(part))
    return tuple(out)


DEFAULT_URL = "https://raw.githubusercontent.com/Eifptaur/Eif-MAPT-PersonaMorph/main/persona-morph-manifest.json"


def manifest_url(cfg: dict | None = None) -> str:
    """更新源地址；**空值一律回落到内置默认**（2026-09-16）。

    为什么：老用户的 `config.json` 是"默认值为空"那阵子存下来的，里面很可能留着一个空的
    `update.url` ⇒ 它会**盖住新默认值**，让这些用户永远接不到更新通知。
    空 ＝ 没配过 ⇒ 用默认（真想关掉更新检查，用「不再提醒」或把 url 填成别的）。
    """
    c = cfg if isinstance(cfg, dict) else _cfg()
    return str(c.get("url") or "").strip() or DEFAULT_URL


def fetch(url: str, timeout: float = 6.0):
    """支持 http(s) 与**本地路径**（本地路径便于离线自测）。返回 (dict 或 None, 说明)。"""
    if not url:
        return None, "未配置更新源"
    try:
        if url.lower().startswith(("http://", "https://")):
            req = urllib.request.Request(url, headers={"User-Agent": "persona-morph-update/1"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read(512 * 1024)
        else:
            with open(url, "rb") as fh:
                raw = fh.read(512 * 1024)
    except Exception as e:
        return None, "拉不到更新源：%s" % (str(e)[:80] or type(e).__name__)
    try:
        return json.loads(raw.decode("utf-8", "replace")), ""
    except Exception as e:
        return None, "更新源不是合法 JSON：%s" % str(e)[:60]


def state(cfg: dict | None = None, timeout: float = 6.0) -> dict:
    """给控制台的**如实**三态。`status ∈ off | error | current | newer | older`。

    · `off`     ＝没配更新源（或开了"不再提醒"）⇒ 界面**什么都不显示**
    · `error`   ＝配了但拉不到/清单坏了 ⇒ 如实显示异常（不假装最新）
    · `current` ＝已是最新
    · `newer`   ＝有新版本（附 notes，供公告条显示）
    · `older`   ＝远端比本机旧（多半是配置指错了）
    """
    c = cfg if isinstance(cfg, dict) else _cfg()
    mine = current_version()
    out = {"status": "off", "mine": mine, "theirs": "", "notes": [], "url": manifest_url(c),
           "why": "", "forceBase": False, "minBase": "", "checkedAt": 0}
    if not out["url"]:
        out["why"] = "没配更新源（update.url）"
        return out
    if c.get("muted"):
        out["why"] = "用户开了「不再提醒」"
        return out
    man, why = fetch(out["url"], timeout)
    if man is None:
        out["status"] = "error"
        out["why"] = why
        out["checkedAt"] = int(time.time())
        st = _read_state()
        st.update({"lastCheck": out["checkedAt"], "lastError": why, "lastStatus": "error"})
        _write_state(st)
        return out
    base = man.get("base") or {}
    an = man.get("announce") or {}
    theirs = str(base.get("version") or an.get("version") or "")
    out["theirs"] = theirs
    out["notes"] = [str(x) for x in (an.get("notes") or [])][:8]
    out["forceBase"] = bool(an.get("forceBase"))
    out["minBase"] = str(an.get("minBase") or "")
    out["checkedAt"] = int(time.time())
    if not theirs:
        out["status"] = "error"
        out["why"] = "清单里没有版本号（清单坏了）"
    elif theirs == mine:
        out["status"] = "current"
        out["why"] = "已是最新（%s）" % mine
    elif c.get("skip_version") and str(c["skip_version"]) == theirs:
        out["status"] = "off"
        out["why"] = "用户选了「不再提醒 %s」" % theirs
    elif vtuple(theirs) and vtuple(mine) and vtuple(theirs) < vtuple(mine):
        out["status"] = "older"
        out["why"] = "远端（%s）比本机（%s）旧——更新源可能指错了" % (theirs, mine)
    else:
        out["status"] = "newer"
        out["why"] = "有新版本 %s（当前 %s）" % (theirs, mine or "未记录")
    st = _read_state()
    st.update({"lastCheck": out["checkedAt"], "lastStatus": out["status"], "lastError": ""})
    _write_state(st)
    return out


def skip_version(v: str) -> dict:
    """记下「不再提醒这个版本」——由控制台按钮调用（改的是 config.json 的 update.skip_version）。"""
    try:
        from .config import get_config, save_config
        cfg = get_config()
        cfg.setdefault("update", {})
        cfg["update"]["skip_version"] = str(v or "")
        save_config(cfg)
        return {"ok": True, "skip_version": str(v or "")}
    except Exception as e:
        return {"ok": False, "why": str(e)[:80]}
