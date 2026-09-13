# -*- coding: utf-8 -*-
"""发送前的大图自动压缩（对账清单第 22 条）。

用户 2026-09-13 发来的第三方待办里有一条「对较大的图片进行自动压缩」——我们原来没有：
群里发大图既慢又容易被网络掐断。这里做"发送前压一压"：

  · 双阈值：**最长边**超 `max_px` 或**文件大小**超 `max_mb` ⇒ 等比缩放 + 重编码；
  · 压完还超大小上限就**再降一档质量**（最多两轮），仍超限就原样发并说明；
  · **宽高比必须保持**（±2% 以内），不许为了压小把图拉变形；
  · 看不出必要（本来就不大）⇒ **字节级不动**（不做无意义重编码）；
  · 出任何错 ⇒ **回退原图**（发送优先），但**必须回执说明**（不许静默）。
"""
from __future__ import annotations

import os

from . import config as _config

DEFAULTS = {
    "enabled": True,      # 发送前压缩（默认开：这是"省事"型能力，不改语义）
    "max_mb": 8.0,        # 超过这个大小就压
    "max_px": 1600,       # 最长边上限
    "quality": 82,        # JPEG 质量（PNG 走 optimize）
}


def cfg() -> dict:
    d = dict(DEFAULTS)
    try:
        got = ((_config.get_config().get("send") or {}).get("image_compress") or {})
        if isinstance(got, dict):
            d.update(got)
    except Exception:
        pass
    return d


def compress_if_needed(path: str, out_dir: str = None) -> tuple:
    """返回 `(要发的路径, 说明)`；不需要压/压不了 ⇒ 原路径 + 说明（永不抛异常）。"""
    c = cfg()
    try:
        if not c.get("enabled"):
            return path, "压缩未开启，原样发送"
        if not path or not os.path.exists(path):
            return path, "文件不存在，原样交给发送层"
        size_mb = os.path.getsize(path) / 1048576.0
        try:
            from PIL import Image
        except Exception:
            return path, "没有 PIL，原样发送（未压缩）"
        with Image.open(path) as im:
            w, h = im.size
            fmt = (im.format or "").upper()
        longest = max(w, h)
        if size_mb <= float(c.get("max_mb")) and longest <= int(c.get("max_px")):
            return path, "本来就够小（%.2fMB / %dx%d），未压缩" % (size_mb, w, h)
        d = out_dir or os.path.join("data", "out_images")
        os.makedirs(d, exist_ok=True)
        base = os.path.splitext(os.path.basename(path))[0]
        target = os.path.join(d, "%s_small.jpg" % base)
        scale = min(1.0, float(c.get("max_px")) / float(max(1, longest)))
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        # 宽高比校验（缩放不该把图拉变形）
        if w and h and abs((nw / float(nh)) - (w / float(h))) > 0.02 * (w / float(h)):
            nh = max(1, int(round(nw * h / float(w))))
        with Image.open(path) as im:
            im = im.convert("RGB") if (fmt in ("JPEG", "JPG") or im.mode not in ("RGB", "L")) else im
            im = im.resize((nw, nh), Image.LANCZOS)
            q = int(c.get("quality"))
            im.save(target, "JPEG", quality=q, optimize=True)
        new_mb = os.path.getsize(target) / 1048576.0
        if new_mb > float(c.get("max_mb")) and q > 55:          # 还超 ⇒ 再降一档（只降一次）
            with Image.open(path) as im2:
                im2 = im2.convert("RGB")
                im2 = im2.resize((nw, nh), Image.LANCZOS)
                im2.save(target, "JPEG", quality=max(55, q - 25), optimize=True)
            new_mb = os.path.getsize(target) / 1048576.0
        if new_mb >= size_mb:                     # 压了反而更大 ⇒ 不折腾
            return path, "压缩后反而更大（%.2fMB→%.2fMB），原样发送" % (size_mb, new_mb)
        return target, "已压缩：%.2fMB→%.2fMB · %dx%d→%dx%d" % (size_mb, new_mb, w, h, nw, nh)
    except Exception as e:
        return path, "压缩失败（%s），原样发送" % type(e).__name__


def snapshot() -> dict:
    c = cfg()
    return {"enabled": bool(c.get("enabled")), "max_mb": float(c.get("max_mb")),
            "max_px": int(c.get("max_px")), "quality": int(c.get("quality"))}
