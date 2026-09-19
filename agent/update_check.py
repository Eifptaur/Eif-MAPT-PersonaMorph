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
API_URL = ("https://api.github.com/repos/Eifptaur/Eif-MAPT-PersonaMorph"
           "/contents/persona-morph-manifest.json?ref=main")
#: ⚠️ 2026-09-18 作者另一台机器现场：「一直 timeout，拉取不到更新源，试几次都不行」
#:   ⇒ 原来只有 4 条源（raw / jsDelivr / ghfast / ghproxy），那台机器上**全都不通**。
#:   这里按"**换网络路径**"而不是"多堆同域名"来扩容：
#:     · `api.github.com` 的 contents 接口 —— 域名解析与路由跟 raw 完全不同（本机实测 api 一直通、
#:       raw/uploads 会挂）⇒ 真正的兜底；返回体是 base64 JSON，由 `fetch()` 特判解码；
#:     · `cdn.statically.io` —— 另一家静态 CDN（跟 jsDelivr 不同网络）；
#:     · `raw.gitmirror.com` —— raw 的镜像（同样内容、不同出口）；
#:     · `gh-proxy.com` / `gh.llkk.cc` —— 另外两个 GitHub 反代。
DEFAULT_URLS = (
    DEFAULT_URL,
    "https://cdn.jsdelivr.net/gh/Eifptaur/Eif-MAPT-PersonaMorph@main/persona-morph-manifest.json",
    "https://ghfast.top/" + DEFAULT_URL,
    "https://ghproxy.net/" + DEFAULT_URL,
    "https://gh-proxy.com/" + DEFAULT_URL,
    "https://gh.llkk.cc/" + DEFAULT_URL,
    "https://raw.gitmirror.com/Eifptaur/Eif-MAPT-PersonaMorph/main/persona-morph-manifest.json",
    "https://cdn.statically.io/gh/Eifptaur/Eif-MAPT-PersonaMorph/main/persona-morph-manifest.json",
    API_URL,
)

#: 官方域：**只有这些**（或内嵌这些域的镜像 URL）才有资格"决定版本与下载地址"。
OFFICIAL_HOSTS = ("github.com", "www.github.com", "raw.githubusercontent.com", "api.github.com",
                  "objects.githubusercontent.com", "codeload.github.com", "githubusercontent.com")
#: 允许当**传输通道**的镜像（它们必须把官方地址整串内嵌在路径里 ⇒ 身份仍由内嵌地址决定）。
PROXY_HOSTS = ("ghfast.top", "ghproxy.net", "gh-proxy.com", "gh.llkk.cc", "raw.gitmirror.com",
               "cdn.statically.io", "cdn.jsdelivr.net", "fastly.jsdelivr.net", "gcore.jsdelivr.net")
#: 本仓库 slug（小写）—— jsDelivr / statically / gitmirror 这类 CDN 的路径形如
#: `cdn.jsdelivr.net/gh/<owner>/<repo>@main/...`，**没有内嵌完整 URL** ⇒ 用"路径里必须出现本仓库"
#: 当它们的信任判据（否则任何 CDN 上的任意内容都能冒充我们的清单）。
REPO_SLUG = "eifptaur/eif-mapt-personamorph"


def _embedded_url(u: str) -> str:
    """取 `https://<镜像>/https://raw.githubusercontent.com/...` 里**内嵌的那个官方地址**。

    镜像的信任身份由内嵌地址决定：镜像只配当"同一份官方清单的传输通道"，
    **不许**它自己决定"版本号与下载地址"（2026-09-20 V-R1-2）。
    """
    s = str(u or "")
    i = s.find("://")
    if i < 0:
        return ""
    for scheme in ("https://", "http://"):
        j = s.find(scheme, i + 3)
        if j > 0:
            return s[j:]
    return ""


def manifest_origin_ok(u: str, cfg: dict = None) -> tuple:
    """这个源**能不能决定"版本与下载地址"**。返回 `(ok, why)`。

    ⛔ 2026-09-20 修 **V-R1-2（P0）**：原来是"**版本最高者胜**"，而清单本身没有任何真实性
    （9 条源里 6 条是第三方反代/CDN）⇒ 任一被投毒/被劫持的镜像回一份版本号更高的清单就能赢过
    官方源，用户点「立即更新」就会装上任意代码（而"哈希校验"只保证包与清单自洽，自洽即通过）。
    现在：①**只有官方域**（或内嵌官方地址的已知镜像）的清单才有资格参与"选版本"；
    ②用户自己填的 `update.url` 若不在官方/已知镜像里，需要显式 `update.trust_custom_url=true`
    才放行（否则拒绝并告诉他开关在哪）；③第三方镜像从此只作传输通道，不作权威。
    """
    s = str(u or "").strip()
    if not s:
        return False, "空地址"
    if not s.lower().startswith(("http://", "https://")):
        return False, "非 http(s) 地址（本地路径只能用于离线自测）"
    inner = _embedded_url(s)
    target = inner or s
    try:
        from urllib.parse import urlparse
        host = (urlparse(target).hostname or "").lower()
        outer = (urlparse(s).hostname or "").lower()
    except Exception:
        return False, "地址解析不了"
    if host in OFFICIAL_HOSTS or host.endswith(".githubusercontent.com"):
        if outer in PROXY_HOSTS or outer == host or inner == "":
            return True, ("官方域" if inner == "" else "镜像内嵌官方地址（%s）" % host)
        return False, "外层不是已知镜像：%s" % outer
    # 已知 CDN/镜像：**必须承载本仓库的路径**才算可信（它们没有内嵌完整 URL，只能这样认）
    if host in PROXY_HOSTS:
        try:
            from urllib.parse import urlparse as _up2
            _path = (_up2(target).path or "").lower()
        except Exception:
            _path = ""
        if REPO_SLUG in _path:
            return True, "已知镜像承载本仓库路径（%s）" % host
        return False, "已知镜像但路径不是本仓库：%s" % host
    c = cfg if isinstance(cfg, dict) else _cfg()
    _cfg_url = str((c or {}).get("url") or "").strip()
    if _cfg_url and s == _cfg_url and bool((c or {}).get("trust_custom_url")):
        return True, "用户显式信任的自定义源（update.trust_custom_url=true）"
    return False, "非官方域：%s" % (host or "?")


def _base_url_ok(u: str) -> tuple:
    """清单里给的**下载地址**也必须落在官方域内（跨域即拒）。"""
    s = str(u or "").strip()
    if not s:
        return True, ""                      # 没给地址 ⇒ 由调用方按"缺 base.url"处理
    try:
        from urllib.parse import urlparse
        host = (urlparse(s).hostname or "").lower()
    except Exception:
        return False, "下载地址解析不了"
    if host in OFFICIAL_HOSTS or host.endswith(".githubusercontent.com"):
        return True, ""
    return False, "清单给的下载地址不在官方域：%s" % (host or "?")


def allow_local_update() -> bool:
    """**本地路径当更新源**只在显式开关下可用（离线自测/内网中转）。

    生产路径（`state()` / `run_once()`）不设这个开关 ⇒ 任何"把 update.url 指向一个本地文件"
    的注入都失效（2026-09-20 V-R1-2 的第二半）。
    """
    import os as _os
    v = str(_os.environ.get("PM_ALLOW_LOCAL_UPDATE") or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    try:
        c = _cfg() or {}
        return bool((c.get("update") or {}).get("allow_local"))
    except Exception:
        return False


def _short_url(u: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(str(u)).hostname or str(u)[:24]
    except Exception:
        return str(u)[:24]


#: 拿到第一份清单后**再等这么久**，让"版本更高"的源也说上话（防 CDN 旧缓存抢先）。
#: 取值依据（2026-09-17 本机实测）：jsDelivr 0.8s、ghfast 0.9s、ghproxy 0.7s ⇒ 1.5s 足够把它们都收进来，
#: 而控制台最坏等待仍是 `timeout + 1`（不变）。
GRACE_S = 2.0


def _ranked(got: dict, urls: list):
    """从已拿到的清单里挑**版本最高**的一份；版本相同或不可解析时，按 `urls` 顺序取先者。

    为什么不是"先到的赢"：四个源里 jsDelivr 是 CDN 缓存（可能是几小时前的旧清单），
    它常常答得最快 ⇒ 按先到挑，用户会**看不到刚发布的版本**、点「立即更新」还会照旧清单装。
    版本号是数值元组（`vtuple`），比较不会踩字符串比较的坑。

    ⛔ 2026-09-20 修 **V-R1-2**：**只有"官方域（或内嵌官方地址的已知镜像）"的清单才有资格
    参与选版本** —— 否则"版本最高者胜"等于"谁被投毒谁说了算"（第三方反代回一份 2099.1.1 就赢）。
    被忽略的来源通过 `_ranked_rejected` 暴露给调用方写进原因里，方便排障时看见。
    """
    global _ranked_rejected
    best_u, best_m, best_v = "", None, ()
    _ranked_rejected = []
    for u in urls:
        man = (got.get(u) or (None, ""))[0]
        if not isinstance(man, dict):
            continue
        _ok, _why = manifest_origin_ok(u)
        if not _ok:
            _ranked_rejected.append("%s（%s）" % (_short_url(u), _why))
            continue
        base = man.get("base")
        v = vtuple((base or {}).get("version")) if isinstance(base, dict) else ()
        if best_m is None or (v and v > best_v):
            best_u, best_m, best_v = u, man, v or ()
    return best_m, best_u


#: 上一轮 `_ranked()` 里被"来源不官方"挡掉的源（给 reason 用，只读）
_ranked_rejected = []


def fetch_any(urls, timeout: float = 12.0, patient: float = None):
    """**并行**拉多个源，**版本最高的赢**。返回 `(清单或 None, 说明, 用到的 url)`。

    为什么要并行（2026-09-16）：串行试 4 个源、每个超时 6 秒 = 最坏 24 秒，控制台一打开就卡住；
    并行 ⇒ 最坏 ≈ 一个超时。
    为什么按版本挑（2026-09-17）：见 `_ranked()` 的注释——先到的可能是 CDN 的旧缓存。
    为什么有"耐心阶段"（2026-09-18 作者另一台机器实测**延迟 ~1900ms**）：
      老实现 `while time.time() - t0 < timeout + 1.0` 到点就收摊 —— 2 秒 RTT 的链路上，
      TCP+TLS 握手（好几轮）加首个响应字节很容易超过 9 秒 ⇒ **明明能通的源被我们自己掐掉**，
      报出来还是"所有源都拉不到"。⇒ 先按老窗口等（有货立刻返回、平时不卡），
      一条都没成时再进**耐心阶段**继续等（默认 30 秒），慢网也能成。
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

    def _one_to(u, to):
        """耐心阶段专用：用**更大的超时**再打一次同一个源（V-R3-2）。"""
        man, why = fetch(u, to)
        with lock:
            got[u] = (man, why)

    for u in urls:
        threading.Thread(target=_one, args=(u,), daemon=True).start()
    t0 = time.time()
    t_first = 0.0
    _patient = float(patient if patient is not None else 30.0)
    _waited_patient = False
    _attempts = 0
    while True:
        _el = time.time() - t0
        if not _waited_patient and _el >= timeout + 1.0:
            # 老窗口过了、一条都没成 ⇒ **进**耐心阶段（**慢网**：2s RTT 也够握手 + 取回）
            # ⛔ 2026-09-20 修 **V5**：原来这里写成 `if _waited_patient or _el >= _patient: break`，
            #    而 `_waited_patient` 刚被置 True ⇒ **下一轮立刻 break** ⇒ 实际只多等了 50 毫秒
            #    （注释承诺 10~30 秒，差三个数量级）。实测：源在 t=5.0s 交回合法清单，
            #    函数 t≈3.0s 就放弃，还报"所有源都拉不到"。
            #    现在把"进入条件"与"退出条件"分开：
            _waited_patient = True
            print("[update] 更新源很慢，继续等（最多 %.0f 秒）…" % _patient)
        elif _waited_patient and _el >= _patient:
            break                      # 耐心也用完了 ⇒ 收摊（下面的逐源复核会把原因列出来）
        with lock:
            done_all = len(got) == len(urls)
            got_one = any((got.get(u) or (None, ""))[0] is not None for u in urls)
        if got_one:
            if not t_first:
                t_first = time.time()
            if done_all or time.time() - t_first >= GRACE_S:
                break
        elif done_all:
            # ⛔ 2026-09-20 修 **V-R3-2**（V5 只闭了一半）：全失败就收摊 —— 但真实慢网里
            #   "第一遍超时"≠"源不可用"：源在 5~15 秒才答是实测存在的情况（作者那台机器 RTT ~1900ms），
            #   而首遍用的是 `timeout`（2~8 秒）⇒ 必然先失败。⇒ 只要还有耐心预算，就把失败的源
            #   **用「剩余预算」当新超时再打一遍**（这才是"耐心阶段"的本意），而不是让死线程白等。
            _left = _patient - _el
            if _attempts < 2 and _left > 2.0:
                _attempts += 1
                with lock:
                    _failed = [u for u in urls if (got.get(u) or (None, ""))[0] is None]
                    for u in _failed:
                        got.pop(u, None)
                _t2 = max(timeout, min(_left, 15.0))
                print("[update] 第一遍全是失败，用剩余 %.0f 秒（单源超时 %.0f 秒）把 %d 个源再试一遍…"
                      % (_left, _t2, len(_failed)))
                for u in _failed:
                    threading.Thread(target=_one_to, args=(u, _t2), daemon=True).start()
                continue
            break
        time.sleep(0.05)
    with lock:
        man, used = _ranked(got, urls)
        if man is not None:
            return man, "", used
        why = "；".join("%s→%s" % (_short_url(u), str((got.get(u) or ("", "超时"))[1])[:40])
                        for u in urls[:3])
    # ⛔ 2026-09-18（那台机器"一直 timeout"的现场）：全失败时要**逐源列出**哪条挂了、错什么 ——
    #    用户把这段粘给我，我一眼就知道"他那台机器哪几条路能通"，不用再来回问。
    try:
        import threading as _th
        errs = {}

        def _one(u):
            _m, _w = fetch(u, timeout)
            errs[_short_url(u)] = "ok" if _m else str(_w)[:70]
            if _m is not None:
                retry_hit.append((u, _m))

        retry_hit = []
        _ts = [_th.Thread(target=_one, args=(u,), daemon=True) for u in list(urls)[:9]]
        [_t.start() for _t in _ts]
        [_t.join(timeout + 1.0) for _t in _ts]
        if errs:
            why = "；".join("%s → %s" % (k, v) for k, v in errs.items())
        # ⭐ V5 的第二半：复核里**真的拿到清单**的源，就直接用它 —— 上一版只把 `ok` 写进错误原因，
        #   于是出现"复核说 b.invalid → ok、函数却仍返回『所有源都拉不到』"这种自相矛盾的结论。
        if retry_hit:
            return retry_hit[0][1], "", retry_hit[0][0]
    except Exception:
        pass
    if _ranked_rejected:
        why = (why + "；" if why else "") + "已忽略非官方来源：" + "、".join(_ranked_rejected[:3])
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


def fetch(url: str, timeout: float = 8.0):
    """支持 http(s) 与**本地路径**（本地路径便于离线自测）。返回 (dict 或 None, 说明)。

    ⛔ 2026-09-20 修 **V-R1-2 的第二半**：本地路径以前在生产路径上**照样可达**（把 `update.url`
    或清单来源指向盘上一个文件就能当更新源）⇒ 现在要求显式开关（`PM_ALLOW_LOCAL_UPDATE=1`
    或配置 `update.allow_local=true`），否则直接拒绝并说明。
    """
    if not url:
        return None, "未配置更新源"
    if not str(url).lower().startswith(("http://", "https://")) and not allow_local_update():
        return None, "这个更新源不是 http(s) 地址（本地文件当更新源需要显式开启，已拒绝）"
    try:
        if url.lower().startswith(("http://", "https://")):
            req = urllib.request.Request(url, headers={"User-Agent": "persona-morph-update/1"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read(512 * 1024)
            # `api.github.com/.../contents/...` 返回 `{"content": "<base64>", ...}` ⇒ 特判解回来。
            # 加它的理由：它的**域名解析与路由跟 raw.githubusercontent 完全不同**——
            # 2026-09-18 作者另一台机器"一直 timeout、拉不到更新源"，正是 raw/反代那条路全不通；
            # 换一条完全不同的网络路径才可能通。
            if "api.github.com" in str(url):
                try:
                    _d = json.loads(raw.decode("utf-8", "replace"))
                    if isinstance(_d, dict) and _d.get("encoding") == "base64" and _d.get("content"):
                        import base64 as _b64
                        raw = _b64.b64decode(_d["content"])
                except Exception:
                    pass
        else:
            with open(url, "rb") as fh:
                raw = fh.read(512 * 1024)
    except Exception as e:
        return None, "拉不到更新源：%s" % (str(e)[:80] or type(e).__name__)
    try:
        return json.loads(raw.decode("utf-8", "replace")), ""
    except Exception as e:
        return None, "更新源不是合法 JSON：%s" % str(e)[:60]


def state(cfg: dict | None = None, timeout: float = 12.0) -> dict:
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
    man, why, used = fetch_any(urls, timeout, patient=10.0)
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
    # ⚡ 2026-09-18 晚：**内容指纹**（见 `agent/version.py::BUILD` 的说明）——同名版本换包也能看出来
    try:
        from .version import BUILD as _MINE_BUILD
    except Exception:
        _MINE_BUILD = ""
    out["build"] = str(base.get("build") or "")
    out["mineBuild"] = str(_MINE_BUILD or "")
    out["forceBase"] = bool(an.get("forceBase"))
    out["minBase"] = str(an.get("minBase") or "")
    out["checkedAt"] = int(time.time())
    if not theirs:
        out["status"] = "error"
        out["why"] = "清单里没有版本号（清单坏了）"
    elif theirs == mine:
        # ⚡ 2026-09-18 晚：**同名版本换包也要能看出来**（作者 2026-09-16 一问：「那就没有办法让他们
        #   也接到更新提示吗」）。两边都有指纹且不同 ⇒ 判"有新包"；任一侧缺指纹（老包/老清单）⇒ 按原口径
        #   判 current（**不误报**：没有指纹时我们无法区分"同一个包"和"换了包"）。
        _tb, _mb = str(out.get("build") or ""), str(out.get("mineBuild") or "")
        if _tb and _mb and _tb != _mb:
            out["status"] = "newer"
            out["why"] = ("同一个版本号（%s）但**包的内容变了**（内容指纹 %s ≠ %s）⇒ 有可更新的包"
                          % (theirs, _tb, _mb))
        else:
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
