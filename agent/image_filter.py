# -*- coding: utf-8 -*-
"""随机图"过滤插件链"（纵深防御）：任何一道说不行，就**不发**。

用户口径（2026-09-13）："**做好插件过滤，反正能过滤的都做好，以免涉黄之类的**"
⇒ 把"过滤"做成**可插拔的流水线**（每个过滤器一个函数，可单独开关、可看到它说了什么），
   顺序＝**先便宜后昂贵**（元数据 → 本地像素 → 视觉模型），命中即刻短路；被拒的图**只记录、绝不发送**。

过滤器一览（配置项都在 `image_reply.*`）：
  1 `source_rating`  分级硬约束：`explicit` 一律拒；`questionable` 默认拒（`allow_questionable=False`）
  2 `tag_blacklist`  标签黑名单（默认含 r18/explicit/nsfw/hentai/エロ/裸/色情/福利/guro/loli 等，用户可改）
  3 `source_allow`   只允许配置里列出的图源
  4 `geometry`       尺寸/大小：太小（< min_side）或超过 max_mb 的丢掉
  5 `skin_ratio`     本地像素启发式：肤色像素占比超过 `skin_max_ratio`（默认 0.45）判定可疑
  6 `vision`         视觉模型审核（有视觉能力时默认开）：让模型只回 SAFE/UNSAFE，**解析不出就当 UNSAFE**（fail-closed）
  7 `reject_log`     被拒记录写 `data/image_rejected.jsonl`（含原因/图源/标签/时间），供人工复核

⚠️ 这只降低概率，**不保证 100%**：README 与文案里必须如实这么写（不做"绝对安全"的承诺）。
"""
from __future__ import annotations

import json
import os
import re
import time

REJECT_LOG_REL = os.path.join("data", "image_rejected.jsonl")

DEFAULT_TAG_BLACKLIST = [
    "r18", "r-18", "r18g", "explicit", "nsfw", "porn", "pornographic", "sex", "sexy", "nude", "nudity",
    "naked", "hentai", "ecchi", "ero", "エロ", "エッチ", "裸", "全裸", "色情", "福利", "工口", "里番",
    "guro", "blood", "血腥", "猎奇", "loli", "shota", "幼女", "正太", "underwear", "lingerie", "bikini",
]
# 只用于兜底判断的"安全分级"
SAFE_RATINGS = ("safe", "general", "s")
BAD_RATINGS = ("explicit", "e", "questionable", "q", "r18", "nsfw")

_vision_prompt = (
    "You are a strict content moderator for a public chat bot. Look at the image and answer with ONE word only:\n"
    "SAFE  = no nudity, no sexual content, no fetish/bondage, no gore; ordinary anime/illustration is fine.\n"
    "UNSAFE= any nudity or sexual suggestion, revealing underwear/swimwear focus, fetish/kink, gore, or anything\n"
    "        that could be reported as NSFW in a general-purpose chat.\n"
    "If you are unsure, answer UNSAFE. Reply with exactly one of: SAFE / UNSAFE"
)


def _root(ctx_root: str = None) -> str:
    return ctx_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cfg(cfg: dict) -> dict:
    return (cfg or {}).get("image_reply") or {}


def tags_of(meta: dict) -> list:
    return [str(t).strip().lower() for t in ((meta or {}).get("tags") or []) if str(t).strip()]


# ── 各过滤器：返回 (ok, 原因) ────────────────────────────────────────────────
def f_source_rating(path, meta, cfg, ctx):
    conf = _cfg(cfg)
    rating = str((meta or {}).get("rating") or "").strip().lower()
    if rating in ("explicit", "e", "r18", "nsfw"):
        return False, "图源标注为限制级（rating=%s）" % rating
    if rating in ("questionable", "q"):
        if not conf.get("allow_questionable"):
            return False, "图源标注为暧昧级（rating=%s，可在配置里放宽，但不建议）" % rating
        # 显式放宽：只对"暧昧级"这一个桶松口，不再被下面的 safe_only 兜底判死
        return True, "分级 questionable，但配置里已显式放宽（不建议长期开启）"
    if conf.get("safe_only", True) and rating and rating not in SAFE_RATINGS:
        return False, "分级不是安全级（rating=%s）" % rating
    return True, "分级 %s 可接受" % (rating or "未知") if rating else "分级未知（由后续过滤把关）"


def f_tag_blacklist(path, meta, cfg, ctx):
    conf = _cfg(cfg)
    black = [str(x).lower() for x in (conf.get("tag_blacklist") or DEFAULT_TAG_BLACKLIST)]
    for t in tags_of(meta):
        for b in black:
            if b and b in t:
                return False, "标签命中黑名单「%s」（标签：%s）" % (b, t)
    return True, "标签干净（%d 个标签）" % len(tags_of(meta))


def f_source_allow(path, meta, cfg, ctx):
    conf = _cfg(cfg)
    allow = [str(s).lower() for s in (conf.get("sources") or [])]
    src = str((meta or {}).get("source") or "").lower()
    if allow and src and src not in allow:
        return False, "图源「%s」不在允许清单里" % src
    return True, "图源 %s 允许" % (src or "本地")


def f_geometry(path, meta, cfg, ctx):
    conf = _cfg(cfg)
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
            im.verify()
    except Exception as e:
        return False, "不是有效图片（%s）" % type(e).__name__
    min_side = int(conf.get("min_side", 300) or 0)
    if min_side and min(w, h) < min_side:
        return False, "分辨率太小（%dx%d < %d）" % (w, h, min_side)
    max_mb = float(conf.get("max_mb", 8) or 8)
    try:
        if os.path.getsize(path) > max_mb * 1024 * 1024:
            return False, "文件超过 %.0fMB" % max_mb
    except OSError:
        return False, "读不到文件大小"
    return True, "尺寸 %dx%d 合规" % (w, h)


def _skin_ratio(path) -> float:
    """古典肤色检测（RGB 规则）下的肤色像素占比——只做粗筛，不做结论。"""
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB").resize((64, 64))
    px = im.load()
    hit = tot = 0
    for y in range(im.height):
        for x in range(im.width):
            r, g, b = px[x, y]
            tot += 1
            mx, mn = max(r, g, b), min(r, g, b)
            if r > 95 and g > 40 and b > 20 and (mx - mn) > 15 and abs(r - g) > 15 and r > g and r > b:
                hit += 1
    return (hit / float(tot)) if tot else 0.0


def f_skin_ratio(path, meta, cfg, ctx):
    conf = _cfg(cfg)
    limit = float(conf.get("skin_max_ratio", 0.45) or 0)
    if limit <= 0:
        return True, "肤色比过滤已关"
    ratio = _skin_ratio(path)
    ctx["skin_ratio"] = round(ratio, 3)
    if ratio > limit:
        return False, "肤色像素占比 %.2f 超过阈值 %.2f（疑似露肤过多）" % (ratio, limit)
    return True, "肤色像素占比 %.2f（阈值 %.2f）" % (ratio, limit)


def _data_url(path: str, max_side: int = 768, quality: int = 85) -> str:
    """本地图片 → data URL（缩到 max_side 再转 JPEG）。自己实现，不依赖别的模块（避免引用到过期副本）。"""
    import base64
    import io
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def vision_check(path, cfg, root: str = None) -> tuple:
    """让视觉模型只看这一张图并回 SAFE/UNSAFE。失败/解析不出 ⇒ 当 UNSAFE（fail-closed）。"""
    try:
        from .llm import chat_completion
        data_url = _data_url(path)
        if not data_url:
            return False, "图片转 data URL 失败"
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": _vision_prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}]
        r = chat_completion(msgs, temperature=0)
        text = ""
        try:
            text = str(((r or {}).get("message") or {}).get("content") or "")
        except Exception:
            text = str(r)
        up = text.upper()
        if "UNSAFE" in up:
            return False, "视觉模型判定 UNSAFE（%s）" % text.strip()[:40]
        if "SAFE" in up:
            return True, "视觉模型判定 SAFE"
        return False, "视觉模型没给出明确结论（%s）⇒ 按不安全处理" % text.strip()[:40]
    except Exception as e:
        return False, "视觉模型审核失败：%s: %s ⇒ 按不安全处理" % (type(e).__name__, str(e)[:80])


def f_vision(path, meta, cfg, ctx):
    conf = _cfg(cfg)
    if not conf.get("vision_filter", True):
        return True, "视觉模型审核已关（配置里关闭）"
    ok, why = vision_check(path, cfg, ctx.get("root"))
    if not ok and conf.get("vision_fail_open"):
        return True, "%s（配置为失败放行）" % why
    return ok, why


# 流水线顺序：便宜的先跑、昂贵的后跑（命中即短路，省 token）
PIPELINE = [
    ("source_rating", f_source_rating, False),
    ("tag_blacklist", f_tag_blacklist, False),
    ("source_allow", f_source_allow, False),
    ("geometry", f_geometry, False),
    ("skin_ratio", f_skin_ratio, False),
    ("vision", f_vision, True),        # True＝会花 token，可关
]


def enabled_names(cfg: dict) -> list:
    conf = _cfg(cfg)
    return [n for n, _f, costly in PIPELINE if (not costly) or conf.get("vision_filter", True)]


def check(path: str, meta: dict = None, cfg: dict = None, root: str = None, log_reject: bool = True) -> dict:
    """跑一遍过滤链。返回 {'ok':bool, 'reason':str, 'trace':[(名字, ok, 说明)], 'rejected_by':str}"""
    ctx = {"root": root or _root(), "cfg": cfg}
    trace = []
    for name, fn, _costly in PIPELINE:
        if name == "vision" and not _cfg(cfg).get("vision_filter", True):
            trace.append((name, True, "已关闭"))
            continue
        try:
            ok, why = fn(path, meta or {}, cfg or {}, ctx)
        except Exception as e:
            ok, why = False, "过滤器异常（按不安全处理）：%s: %s" % (type(e).__name__, str(e)[:80])
        trace.append((name, ok, why))
        if not ok:
            out = {"ok": False, "reason": why, "rejected_by": name, "trace": trace}
            if log_reject:
                _log_reject(path, meta, cfg, name, why, ctx, root)
            return out
    return {"ok": True, "reason": "全部过滤器通过（%d 道）" % len(trace), "rejected_by": "", "trace": trace,
            "skin_ratio": ctx.get("skin_ratio")}


def _log_reject(path, meta, cfg, name, why, ctx, root=None) -> None:
    """被拒的图只记录、绝不发送（含图源/标签/肤色比，便于人工复核与调参）。"""
    try:
        rec = {"at": int(time.time()), "filter": name, "reason": why,
               "source": (meta or {}).get("source"), "tags": tags_of(meta)[:20],
               "rating": (meta or {}).get("rating"), "page": (meta or {}).get("page"),
               "skin_ratio": ctx.get("skin_ratio"), "path": os.path.basename(path or "")}
        p = os.path.join(root or _root(), REJECT_LOG_REL)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        keep = []
        try:                                    # 只留最近 200 条，避免日志无限涨
            if os.path.exists(p):
                with open(p, encoding="utf-8") as fh:
                    keep = fh.readlines()[-200:]
        except Exception:
            keep = []
        keep.append(json.dumps(rec, ensure_ascii=False) + "\n")
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(keep[-200:])
        os.replace(tmp, p)
    except Exception:
        pass


def summary(cfg: dict = None, root: str = None) -> dict:
    """给控制台用：当前开了哪些过滤、黑名单条数、被拒条数。"""
    conf = _cfg(cfg)
    rej = 0
    try:
        with open(os.path.join(root or _root(), REJECT_LOG_REL), encoding="utf-8") as fh:
            rej = sum(1 for _ in fh)
    except Exception:
        pass
    return {"enabled": enabled_names(cfg or {}),
            "safe_only": bool(conf.get("safe_only", True)),
            "allow_questionable": bool(conf.get("allow_questionable")),
            "vision_filter": bool(conf.get("vision_filter", True)),
            "skin_max_ratio": conf.get("skin_max_ratio", 0.45),
            "blacklist_count": len(conf.get("tag_blacklist") or DEFAULT_TAG_BLACKLIST),
            "rejected_total": rej}
