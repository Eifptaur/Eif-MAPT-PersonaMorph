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
#: 备用源（2026-09-16 已知现象：「更新源异常：拉不到更新源：The read operation timed out」）：
#: `raw.githubusercontent.com` 在国内经常超时 ⇒ **并行**试这几个。
#: ⚠️ jsDelivr 有 CDN 缓存（本机实测：新版本已发布，它还给着上一版）⇒ **不能按"先到的赢"挑**
#: （2026-09-17 实测：它 0.8s 就答、镜像 0.7~0.9s 也有货 ⇒ 先到的恰好是旧的那份，新版在控制台里"消失"）；
#: 现在由 `_ranked()` 按**版本最高者胜**挑，缓存旧的那份抢不赢，但仍然是可用的兜底源。
DEFAULT_URLS = (
    DEFAULT_URL,
    "https://cdn.jsdelivr.net/gh/Eifptaur/Eif-MAPT-PersonaMorph@main/persona-morph-manifest.json",
    "https://ghfast.top/" + DEFAULT_URL,
    "https://ghproxy.net/" + DEFAULT_URL,
)


def _short_url(u: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(str(u)).hostname or str(u)[:24]
    except Exception:
        return str(u)[:24]


#: 拿到第一份清单后**再等这么久**，让"版本更高"的源也说上话（防 CDN 旧缓存抢先）。
#: 取值依据（2026-09-17 本机实测）：jsDelivr 0.8s、ghfast 0.9s、ghproxy 0.7s ⇒ 1.5s 足够把它们都收进来，
#: 而控制台最坏等待仍是 `timeout + 1`（不变）。
GRACE_S = 1.5


def _ranked(got: dict, urls: list):
    """从已拿到的清单里挑**版本最高**的一份；版本相同或不可解析时，按 `urls` 顺序取先者。

    为什么不是"先到的赢"：四个源里 jsDelivr 是 CDN 缓存（可能是几小时前的旧清单），
    它常常答得最快 ⇒ 按先到挑，用户会**看不到刚发布的版本**、点「立即更新」还会照旧清单装。
    版本号是数值元组（`vtuple`），比较不会踩字符串比较的坑。
    """
    best_u, best_m, best_v = "", None, ()
    for u in urls:
        man = (got.get(u) or (None, ""))[0]
        if not isinstance(man, dict):
            continue
        base = man.get("base")
        v = vtuple((base or {}).get("version")) if isinstance(base, dict) else ()
        if best_m is None or (v and v > best_v):
            best_u, best_m, best_v = u, man, v or ()
    return best_m, best_u


def fetch_any(urls, timeout: float = 6.0):
    """**并行**拉多个源，**版本最高的赢**。返回 `(清单或 None, 说明, 用到的 url)`。

    为什么要并行（2026-09-16）：串行试 4 个源、每个超时 6 秒 = 最坏 24 秒，控制台一打开就卡住；
    并行 ⇒ 最坏 ≈ 一个超时。
    为什么按版本挑（2026-09-17）：见 `_ranked()` 的注释——先到的可能是 CDN 的旧缓存。
    """
    urls = [u for u in (urls or []) if u]
    if not urls:
        return None, "未配置更新源", ""
    if len(urls) == 1:
        man, why = fetch(urls[0], timeout)
        return man, ("" if man is not None else why), (urls[0] if man is not None else "")
    import threading
    got, lock = {}, threading.Lock()

    def _one(u):
        man, why = fetch(u, timeout)
        with lock:
            got[u] = (man, why)

    for u in urls:
        threading.Thread(target=_one, args=(u,), daemon=True).start()
    t0 = time.time()
    t_first = 0.0
    while time.time() - t0 < timeout + 1.0:
        with lock:
            done_all = len(got) == len(urls)
            got_one = any((got.get(u) or (None, ""))[0] is not None for u in urls)
        if got_one:
            if not t_first:
                t_first = time.time()
            if done_all or time.time() - t_first >= GRACE_S:
                break
        elif done_all:
            break
        time.sleep(0.05)
    with lock:
        man, used = _ranked(got, urls)
        if man is not None:
            return man, "", used
        why = "；".join("%s→%s" % (_short_url(u), str((got.get(u) or ("", "超时"))[1])[:40])
                        for u in urls[:3])
    return None, "所有源都拉不到（%s）" % why, ""


def manifest_url(cfg: dict | None = None) -> str:
    """更新源地址；**空值一律回落到内置默认**（2026-09-16）。

    为什么：老用户的 `config.json` 是"默认值为空"那阵子存下来的，里面很可能留着一个空的
    `update.url` ⇒ 它会**盖住新默认值**，让这些用户永远接不到更新通知。
    空 ＝ 没配过 ⇒ 用默认（真想关掉更新检查，用「不再提醒」或把 url 填成别的）。
    """
    c = cfg if isinstance(cfg, dict) else _cfg()
    return str(c.get("url") or "").strip() or DEFAULT_URL


def candidate_urls(cfg: dict | None = None) -> list:
    """**该试哪几个清单源**——"检查更新"与"立即更新"必须用同一份（2026-09-17 修）。

    为什么单独立一个函数：以前"检查"走 `fetch_any`（并行多源 + 记住上次能用的源），
    而"立即更新"只试 `manifest_url()` **一个**地址（默认＝国内常年超时的
    `raw.githubusercontent.com`）⇒ 用户遇到「第一次一定拉不到更新源，第二次才成功」。
    两条路共用这里，就不会再各写一套。
    """
    c = cfg if isinstance(cfg, dict) else _cfg()
    cfg_url = str(c.get("url") or "").strip()
    if cfg_url:
        # 用户自己填的源：失败就**如实报**，不去偷偷换别人的源（换了他会更懵）
        return [cfg_url]
    _last = str((_read_state() or {}).get("lastGoodUrl") or "")
    return ([_last] if _last else []) + [u for u in DEFAULT_URLS if u != _last]


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
    if c.get("muted"):
        out["why"] = "用户开了「不再提醒」"
        return out
    urls = candidate_urls(c)
    man, why, used = fetch_any(urls, timeout)
    if man is None:
        out["status"] = "error"
        out["why"] = why
        out["checkedAt"] = int(time.time())
        st = _read_state()
        st.update({"lastCheck": out["checkedAt"], "lastError": why, "lastStatus": "error"})
        _write_state(st)
        return out
    if used:
        out["url"] = used
    base = man.get("base") or {}
    an = man.get("announce") or {}
    theirs = str(base.get("version") or an.get("version") or "")
    out["theirs"] = theirs
    out["notes"] = [str(x) for x in (an.get("notes") or [])][:8]
    # 自更新要用它俩（2026-09-16：控制台「立即更新」真正开始下载+换入，不再只打印指路文案）
    out["baseUrl"] = str(base.get("url") or "")
    out["baseSha256"] = str(base.get("sha256") or "")
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
    st.update({"lastCheck": out["checkedAt"], "lastStatus": out["status"], "lastError": "",
               "lastGoodUrl": out["url"]})          # 记住"哪个源能用"，下次先试它
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
