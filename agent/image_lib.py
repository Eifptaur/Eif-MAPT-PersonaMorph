# -*- coding: utf-8 -*-
"""随机图图库（本地优先，零出网）：扫描目录 → 随机挑一张（尽量不重复）→ 交给 send_image 本地直发。

设计口径（用户 2026-09-13："没有的话，加上"）：
  · **本地优先**：`image_reply.dir` 里放你自己的图（默认 `assets/anime/`），全程不联网；
  · **可选在线图源**：`mode=api` 时从 `api_url` 取一张（要联网，默认留空＝不联网）；
  · **不许发错/发大**：超过 `max_mb` 的跳过；同一会话 `min_gap_seconds` 内不允许再发；
  · **记录最近发过**：`data/image_recent.json` 存最近 N 张路径（尽量不重复），原子落盘。

只依赖标准库 + PIL（项目已有）；任何异常都返回可读原因，不抛给调用方。
"""
from __future__ import annotations

import json
import os
import queue as _queue
import random
import threading as _threading
import time
import urllib.request

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")

_recent_path = None          # 由 ROOT 决定（便于单测注入）


def _root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def recent_file(root: str = None) -> str:
    return os.path.join(root or _root(), "data", "image_recent.json")


def _load_recent(root: str = None) -> dict:
    try:
        with open(recent_file(root), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_recent(d: dict, root: str = None) -> None:
    try:
        p = recent_file(root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False)
        os.replace(tmp, p)                      # 原子替换，避免半截文件
    except Exception:
        pass


def _cache_pick(cache_dir: str, chat_id: str = "", keep: int = 40) -> str:
    """在线图源没取到时，从**以前要到的图**里挑一张（秒回）。

    这些图本来就留在 `media/images/`，所以零额外下载。顺手只保留最近 `keep` 张
    （用户口径：凡会往磁盘写东西的功能，都要有上限与清理）。
    """
    try:
        files = [os.path.join(cache_dir, f) for f in os.listdir(cache_dir)
                 if f.lower().endswith(IMAGE_EXT) and not f.endswith(".part")]
    except OSError:
        return ""
    files = [f for f in files if os.path.getsize(f) > 0]
    if not files:
        return ""
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for old in files[keep:]:                      # 超出上限的老图删掉（只删我们自己下载的 src_*）
        if os.path.basename(old).startswith("src_"):
            try:
                os.remove(old)
            except OSError:
                pass
    files = files[:keep]
    last = str((_load_recent() or {}).get("last") or "")
    pool = [f for f in files if f != last] or files
    return random.choice(pool)


def lib_dir(cfg: dict = None, root: str = None) -> str:
    """图库目录（配置里可填相对项目根或绝对路径）。"""
    raw = ""
    try:
        raw = str(((cfg or {}).get("image_reply") or {}).get("dir") or "assets/anime")
    except Exception:
        raw = "assets/anime"
    return raw if os.path.isabs(raw) else os.path.join(root or _root(), raw)


def scan(cfg: dict = None, root: str = None) -> list:
    """列出图库里可用的图片（绝对路径，按名字排序保证可复现）。"""
    d = lib_dir(cfg, root)
    allow_gif = bool(((cfg or {}).get("image_reply") or {}).get("include_gif", True))
    out = []
    try:
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            low = name.lower()
            if not low.endswith(IMAGE_EXT):
                continue
            if (not allow_gif) and low.endswith(".gif"):
                continue
            out.append(p)
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return out


def _too_big(path: str, max_mb: float) -> bool:
    try:
        return os.path.getsize(path) > float(max_mb) * 1024 * 1024
    except Exception:
        return True


def pick(cfg: dict = None, root: str = None, chat_id: str = "") -> tuple:
    """随机挑一张：返回 (路径, 说明)。挑不到返回 (None, 原因)。

    规则：先按 `avoid_recent` 排除最近发过的；全都发过就重置最近记录再挑（不会因为图少而卡死）。
    大小超 `max_mb` 的直接丢弃（并在说明里讲清）。
    """
    conf = (cfg or {}).get("image_reply") or {}
    max_mb = conf.get("max_mb", 8)
    avoid = int(conf.get("avoid_recent", 30) or 0)
    files = [p for p in scan(cfg, root) if not _too_big(p, max_mb)]
    if not files:
        d = lib_dir(cfg, root)
        return None, "图库是空的（目录：%s）。把你的图放进去，或在控制台改「图库目录」" % d
    rec = _load_recent(root)
    used = list(rec.get("recent") or [])
    cand = [p for p in files if p not in used] or files
    if not cand:
        cand = files
        rec["recent"] = []
    path = random.choice(cand)
    recent = ([path] + [p for p in used if p != path])[:max(1, avoid)]
    rec["recent"] = recent
    rec["last"] = {"path": path, "at": int(time.time()), "chat": chat_id}
    _save_recent(rec, root)
    return path, "从图库随机挑中（图库 %d 张，最近已发 %d 张）" % (len(files), len(recent))


def cooldown_left(cfg: dict, chat_id: str, root: str = None) -> int:
    """距离上次给同一会话发随机图还差几秒（0＝可以发）。"""
    try:
        gap = int(((cfg or {}).get("image_reply") or {}).get("min_gap_seconds", 20) or 0)
    except Exception:
        gap = 20
    if gap <= 0:
        return 0
    last = (_load_recent(root).get("last") or {})
    if last.get("chat") != chat_id:
        return 0
    left = gap - int(time.time() - int(last.get("at") or 0))
    return max(0, left)


def fetch_api(cfg: dict, root: str = None) -> tuple:
    """在线图源取一张（mode=api）：下载到 media/images/ 后返回 (路径, 说明)。"""
    conf = (cfg or {}).get("image_reply") or {}
    url = str(conf.get("api_url") or "").strip()
    if not url:
        return None, "在线图源没配置（image_reply.api_url 为空）⇒ 请改用本地图库，或填一个图源地址"
    timeout = max(1.0, float(conf.get("api_timeout_ms", 8000)) / 1000.0)
    max_mb = float(conf.get("max_mb", 8))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PersonaMorph/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = str(resp.headers.get("Content-Type") or "")
            data = resp.read(int(max_mb * 1024 * 1024) + 1)
        if len(data) > max_mb * 1024 * 1024:
            return None, "图源返回的图超过 %.0fMB 上限，已放弃" % max_mb
        ext = ".jpg"
        for e in IMAGE_EXT:
            if e.strip(".") in ctype.lower():
                ext = e
                break
        d = os.path.join(root or _root(), "media", "images")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "api_%d%s" % (int(time.time()), ext))
        with open(p, "wb") as fh:
            fh.write(data)
        try:                                    # 能打开才算真图片
            from PIL import Image
            Image.open(p).verify()
        except Exception:
            os.remove(p)
            return None, "图源返回的内容不是有效图片（%s）" % (ctype or "未知类型")
        return p, "已从在线图源取图并校验通过（%.0fKB）" % (len(data) / 1024.0)
    except Exception as e:
        return None, "在线图源取图失败：%s: %s" % (type(e).__name__, str(e)[:120])


def next_image(cfg: dict, root: str = None, chat_id: str = "") -> tuple:
    """按配置挑下一张要发的图：返回 (路径, 说明)。总开关关着就直接说明原因。

    mode=online 时：逐个图源取元数据 → 下载 → **过过滤链**（agent/image_filter.py）→ 通过才返回；
    被拒的会写 reject 日志并换下一个图源；全都不行就把每个图源/过滤器的原因汇总返回。
    """
    conf = (cfg or {}).get("image_reply") or {}
    if not conf.get("enabled"):
        return None, "随机图功能当前是关闭的（控制台「随机图」面板可打开）"
    left = cooldown_left(cfg, chat_id, root)
    if left:
        return None, "同一会话两次随机图要间隔 %d 秒，还需等 %d 秒" % (conf.get("min_gap_seconds", 20), left)
    mode = str(conf.get("mode") or "local")
    if mode == "online":
        return fetch_filtered(cfg, root, chat_id)
    if mode == "api":
        path, why = fetch_api(cfg, root)
        if not path:
            return None, why
        return _filter_or_reject(path, {"source": "api", "rating": "", "tags": []}, cfg, root, why)
    return pick(cfg, root, chat_id)


def _filter_or_reject(path: str, meta: dict, cfg: dict, root: str, why: str) -> tuple:
    """过一遍过滤链：通过就返回路径，不通过就删掉临时文件并返回原因。"""
    try:
        from . import image_filter as _f
        res = _f.check(path, meta, cfg, root=root)
    except Exception as e:
        return None, "过滤链异常（按不安全处理）：%s: %s" % (type(e).__name__, str(e)[:100])
    if res.get("ok"):
        return path, "%s · %s" % (why, res.get("reason"))
    return None, "被过滤链拦下（%s）：%s" % (res.get("rejected_by"), res.get("reason"))


# ── 图源健康（短冷却）：失败过的图源短时间内不再白等 ──────────────────────────────
#   2026-09-17 实测：本机 safebooru（SSL 握手失败）白等 9.3 秒、yande 超时 8.0 秒、
#   konachan 403、waifu 域名解析失败 —— 四个死源合计 **27 秒**，而用户看到的就是"发张图一分多钟"。
_FAIL_UNTIL = {}
_FAIL_LOCK = _threading.Lock()


def _fail_note(src: str, ttl_s: int) -> None:
    try:
        with _FAIL_LOCK:
            _FAIL_UNTIL[str(src)] = time.time() + max(0, int(ttl_s))
    except Exception:
        pass


def _fail_skip(src: str) -> bool:
    try:
        with _FAIL_LOCK:
            until = float(_FAIL_UNTIL.get(str(src)) or 0)
            if until <= time.time():
                _FAIL_UNTIL.pop(str(src), None)
                return False
            return True
    except Exception:
        return False


def source_health() -> dict:
    """当前哪些图源在冷却、还剩几秒（控制台/判据可读）。"""
    now = time.time()
    with _FAIL_LOCK:
        return {k: round(v - now, 1) for k, v in _FAIL_UNTIL.items() if v > now}


def fetch_filtered(cfg: dict, root: str = None, chat_id: str = "", tag: str = "") -> tuple:
    """mode=online：取图并过过滤链，返回第一张通过的。

    `tag`：本次请求的**关键词覆盖**（用户/模型说"找张猫的图"时用），留空则用配置里的 `image_reply.tag`。

    速度纪律（2026-09-17 重做，起因＝用户实测"发张图一分多钟"）：
      ① 图源元数据**并行**问（原来按顺序一个个等，四个死源就 27 秒）；
      ② 失败过的图源**短冷却**（默认 600 秒内直接跳过，不再每次白等）；
      ③ 下载**有整张图的总时长上限**（原来 socket 超时只管单次 recv，慢速代理能涓流几分钟）；
      ④ 整件事还有**总预算**（默认 15 秒），用完就如实说"没找到"，不让用户干等。
    """
    conf = (cfg or {}).get("image_reply") or {}
    use_tag = str(tag or conf.get("tag") or "").strip()
    try:
        from . import image_sources as _src
    except Exception as e:
        return None, "图源模块不可用：%s" % e
    sources = [str(s).strip().lower() for s in (conf.get("sources") or _src.available()) if str(s).strip()]
    per_try = max(1, int(conf.get("sources_per_try", 4) or 4))
    meta_ms = int(conf.get("meta_timeout_ms") or conf.get("api_timeout_ms") or 6000)
    dl_ms = int(conf.get("download_timeout_ms") or conf.get("api_timeout_ms") or 8000)
    budget = max(3.0, int(conf.get("total_budget_ms") or 15000) / 1000.0)
    cooldown = int(conf.get("source_cooldown_s") or 600)
    dest = os.path.join(root or _root(), "media", "images")
    t0 = time.time()
    errs = []
    attempts = max(1, int(conf.get("attempts", 2) or 2))
    race = max(1, int(conf.get("download_race", 4) or 4))
    prefer_mb = float(conf.get("prefer_max_mb", 4) or 4)
    last_n = 0

    def _metas() -> tuple:
        """① 并行问元数据：**拿到第一个能用的就不等慢的了**（原来会一直等到最慢的源超时，白等 6 秒）。"""
        pool = sources[:per_try]
        cand = [s for s in pool if not _fail_skip(s)]
        cooled = [s for s in pool if s not in cand]
        e = []
        if cooled:
            e.append("冷却中跳过：%s" % "、".join(cooled))
            if not cand:
                cand = pool                      # 全在冷却里 ⇒ 还是试一轮，别把功能锁死
        if not cand:
            return [], ["没有可用的图源（配置里的 sources 都不认识）"]
        out = []
        try:
            from concurrent import futures as _fut
            ex = _fut.ThreadPoolExecutor(max_workers=min(6, len(cand)))
            jobs = {}
            for s in cand:
                jobs[ex.submit(_src.fetch_meta, s, {"tag": use_tag, "timeout_ms": meta_ms,
                                                    "category": conf.get("category")})] = s
            pending = set(jobs)
            slack = max(0.2, float(conf.get("meta_grace_ms", 800) or 800) / 1000.0)
            hard = time.time() + max(0.5, meta_ms / 1000.0 + 0.5)
            first_ok_at = None
            while pending:
                if first_ok_at is not None and (time.time() - first_ok_at) >= slack:
                    break
                if time.time() > hard:
                    break
                done_set, pending = _fut.wait(pending, timeout=0.15, return_when=_fut.FIRST_COMPLETED)
                for fu in done_set:
                    s = jobs[fu]
                    try:
                        meta, err = fu.result()
                    except Exception as ex2:
                        meta, err = None, "图源 %s 异常：%s: %s" % (s, type(ex2).__name__, ex2)
                    if meta:
                        out.append((s, meta))
                        if first_ok_at is None:
                            first_ok_at = time.time()
                    else:
                        e.append(err or ("图源 %s 失败" % s))
                        # 只对**真错误**（403/SSL/DNS/格式）上冷却；"慢/超时"是环境问题，
                        # 冷却它反而把好源冤枉掉（2026-09-17 实测：pixiv 一次超时被冤 10 分钟）
                        if not any(k in str(err or "") for k in ("TimeoutError", "timed out", "超时", "未在")):
                            _fail_note(s, cooldown)
            for fu in pending:
                e.append("图源 %s 未在 %dms 内返回" % (jobs[fu], meta_ms))
            try:
                ex.shutdown(wait=False, cancel_futures=True)     # 不等落后线程（受 socket 超时兜底）
            except TypeError:
                ex.shutdown(wait=False)
        except Exception as ex3:
            return [], ["并发取图失败：%s: %s" % (type(ex3).__name__, ex3)]
        order = {s: i for i, s in enumerate(cand)}       # 按配置顺序排（顺序＝用户心里的优先级）
        out.sort(key=lambda x: order.get(x[0], 99))
        return out, e

    # ② 候选图**并发下载赛跑**：谁先下完并通过过滤链就用谁
    #    起因（2026-09-17 实测）：i.pixiv.re 今晚只有 6~8 KB/s，一张 master1200 下 11 秒还没完；
    #    按顺序等它就会把整段预算吃光，后面的快图源一张也轮不上。
    def _race(picks: list, soft_mb: float) -> tuple:
        """并发下一轮：返回 (赢家路径, 说明, 本轮错误)。第一个下完且过过滤链的赢。"""
        stop = _threading.Event()
        q = _queue.Queue()

        def _worker(src: str, meta: dict, budget_ms: int) -> None:
            # ⚠️ 线程里必须自己兜住异常：2026-09-17 判据暴露过一个真坑 —— 图源函数签名不匹配抛
            #   TypeError，线程静默死掉、队列永远等不到结果 ⇒ **整件事只能等到预算耗尽**。
            try:
                if stop.is_set():
                    return
                path, derr = _src.download(meta["url"], dest, max_mb=conf.get("max_mb", 8),
                                           timeout_ms=budget_ms, soft_max_mb=soft_mb)
                if stop.is_set():                    # 已经有赢家了 ⇒ 别留自己的半成品
                    if path:
                        try:
                            os.remove(path)
                        except OSError:
                            pass
                    return
                if derr:
                    if "TimeoutError" in derr:       # 慢源别每次都来占位（短冷却，不是判死刑）
                        _fail_note(src, int(conf.get("slow_cooldown_s") or 120))
                    q.put(("err", src, derr))
                    return
                got, why = _filter_or_reject(path, meta, cfg, root, "图源 %s 取图" % src)
                if got:
                    q.put(("ok", src, "%s（%s）" % (why, meta.get("page") or ""), got))
                    return
                try:
                    os.remove(path)                  # 被拒的临时文件不留
                except OSError:
                    pass
                q.put(("err", src, why))
            except Exception as e:
                q.put(("err", src, "取图异常：%s: %s" % (type(e).__name__, str(e)[:80])))

        left = budget - (time.time() - t0)
        per_ms = int(max(1000, min(dl_ms, left * 1000)))
        ths = [_threading.Thread(target=_worker, args=(s, m, per_ms), daemon=True) for s, m in picks]
        for th in ths:
            th.start()
        done, errs2 = 0, []
        try:
            while done < len(ths):
                left = budget - (time.time() - t0)
                if left <= 0.2:
                    errs2.append("总时长预算 %.0f 秒用完" % budget)
                    break
                try:
                    item = q.get(timeout=min(left, 1.0))
                except _queue.Empty:
                    continue
                done += 1
                if item[0] == "ok":
                    stop.set()
                    return item[3], item[2], errs2
                errs2.append("图源 %s：%s" % (item[1], item[2]))
        finally:
            stop.set()                               # 收工：还在下的线程自己删半成品
        return None, "", errs2

    for attempt in range(attempts):
        metas, e0 = _metas()
        errs.extend(e0)
        last_n = len(metas)
        if metas:
            # ③ 先只收"小图"（聊天气泡用不着原图：实测 5.34MB / 2894×4970 下完还要压到 1600px）
            got, why, e1 = _race(metas[:race], prefer_mb)
            errs.extend(e1)
            if got:
                return got, why
            # ④ 全是超大图 ⇒ 放宽到只受 max_mb 硬上限，再赛一轮（预算还够才做）
            if any("软上限" in x for x in e1) and (budget - (time.time() - t0)) > 1.5:
                got, why, e2 = _race(metas[:race], 0)
                errs.extend(e2)
                if got:
                    return got, why
        if attempt + 1 >= attempts or (budget - (time.time() - t0)) < 2.0:
            break
        errs.append("第 %d 轮没成，再试一次" % (attempt + 1))
    if not last_n:
        return None, "试了 %d 个图源都没拿到图：%s" % (per_try, "；".join(errs[:4]) or "无响应")
    return None, "试了 %d 个图源都没通过过滤：%s" % (last_n, "；".join(errs[:5]))


def search_image(cfg: dict, keyword: str, root: str = None, chat_id: str = "") -> tuple:
    """**按关键词找一张图**（在线图源 + 同一套过滤链）。返回 `(路径 或 None, 说明)`。

    与 `next_image` 的区别：这是"**有人点名要什么图**"，关键词由请求带进来（`image_reply.tag` 只是默认偏好）。
    过滤链完全复用（安全分级 / 标签黑名单 / 肤色比 / 视觉审核）——任何一道说不行就不返回。
    """
    kw = str(keyword or "").strip()
    if not kw:
        return None, "没给关键词（比如「猫」「风景」「赛博朋克」）"
    conf = (cfg or {}).get("image_reply") or {}
    if not conf.get("allow_search", True):
        return None, "「按关键词找图」被设置关掉了（控制台「随机图」面板 → 允许按关键词找图）"
    got, why = fetch_filtered(cfg, root=root, chat_id=chat_id, tag=kw)
    if got:
        return got, why
    # 兜底：在线图源整体不可用时（外网图站夜里经常抽风），从"以前要到的图"里挑一张 —— 秒回，
    # 总比让用户在群里干等十几秒最后什么都没收到好（口径：宁可给一张旧的，也不空手）。
    cache = os.path.join(root or _root(), "media", "images")
    fb = _cache_pick(cache, chat_id)
    if fb:
        return fb, "在线图源这次没取到（%s）⇒ 从以前要到的图里挑了一张" % str(why)[:80]
    return None, why


def status(cfg: dict = None, root: str = None) -> dict:
    """给控制台用的状态快照（含空库/未配置等可读文案）。"""
    conf = (cfg or {}).get("image_reply") or {}
    d = lib_dir(cfg, root)
    files = scan(cfg, root)
    rec = _load_recent(root)
    return {
        "enabled": bool(conf.get("enabled")),
        "mode": conf.get("mode", "local"),
        "dir": d,
        "dir_exists": os.path.isdir(d),
        "count": len(files),
        "api_url_set": bool(str(conf.get("api_url") or "").strip()),
        "recent": len(rec.get("recent") or []),
        "last": rec.get("last") or {},
        "text": ("已开启" if conf.get("enabled") else "未开启") + " · " + (
            "图库 %d 张（%s）" % (len(files), d) if os.path.isdir(d) else "图库目录不存在：%s" % d),
    }
