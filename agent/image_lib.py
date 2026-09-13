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
import random
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


def fetch_filtered(cfg: dict, root: str = None, chat_id: str = "") -> tuple:
    """mode=online：按 sources 顺序取图并过过滤链，返回第一张通过的。"""
    conf = (cfg or {}).get("image_reply") or {}
    try:
        from . import image_sources as _src
    except Exception as e:
        return None, "图源模块不可用：%s" % e
    sources = [str(s).strip().lower() for s in (conf.get("sources") or _src.available()) if str(s).strip()]
    per_try = max(1, int(conf.get("sources_per_try", 4) or 4))
    dest = os.path.join(root or _root(), "media", "images")
    tries, errs = 0, []
    for src in sources:
        if tries >= per_try:
            break
        tries += 1
        meta, err = _src.fetch_meta(src, {"tag": conf.get("tag"), "timeout_ms": conf.get("api_timeout_ms"),
                                          "category": conf.get("category")})
        if err:
            errs.append(err)
            continue
        path, derr = _src.download(meta["url"], dest, max_mb=conf.get("max_mb", 8),
                                   timeout_ms=conf.get("api_timeout_ms") or 9000)
        if derr:
            errs.append("图源 %s：%s" % (src, derr))
            continue
        got, why = _filter_or_reject(path, meta, cfg, root, "图源 %s 取图" % src)
        if got:
            return got, "%s（%s）" % (why, meta.get("page") or "")
        errs.append("图源 %s：%s" % (src, why))
        try:
            os.remove(path)                      # 被拒的临时文件不留（日志里已记）
        except OSError:
            pass
    if not errs:
        return None, "没有可用的图源（配置里的 sources 都不认识）"
    return None, "试了 %d 个图源都没通过过滤：%s" % (tries, "；".join(errs[:4]))


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
