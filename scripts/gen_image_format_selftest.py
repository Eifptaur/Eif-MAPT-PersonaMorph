# -*- coding: utf-8 -*-
"""新图落盘格式：**存 JPEG**（PLAN 第 171 项实施；决策＝用户看过 q85 对照后「取最省那一档」）。

⛔ 为什么值得单独一条判据：`image_gen` 原有的判据**全部把 `_save_image_bytes` 打桩替换掉了**
   （为了不往产品目录落文件）⇒ **真实落盘路径此前一条断言都没有** ⇒ 改坏了没人会知道。

判据分五层：
  ① 转换本身：出的是 **合法 JPEG**、**比 PNG 小**、而且 `quality` 参数**真的生效**；
  ② **透明通道不许变黑**（JPEG 没有 alpha；直接 `convert("RGB")` 会把透明区**变黑** ＝ 真把图画坏）；
  ③ **失败必须原样落盘**（宁可多占空间，也不能丢图）；
  ④ 真落盘：文件后缀与真实格式一致、**长宽不变**、能被打开；
  ⑤ 静态锚：档位就是用户看过的那一档，且**不做色度抽样**（否则颜色会偏）。
"""
from __future__ import annotations

import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.environ["PM_JUDGE_NO_GUI"] = "1"

from PIL import Image, ImageDraw # noqa: E402
from agent import image_gen as IG # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def _photo_png(size=(256, 256)) -> bytes:
    """**照片类**夹具（噪声 + 渐变 + 模糊）—— 这才是真实 AI 出图的统计特征：
    PNG 对它压不动、JPEG 优势大。

    ⛔ 第一版我用的是"纯色 + 几条细线"的合成图 ⇒ PNG 只 4.7 KB、JPEG 反而 43 KB
       ⇒ 产品的「转完更大就回退 PNG」那道闸当场生效、判据全红 —— **错在夹具不真实，不在产品**。
    """
    import random
    _r = random.Random(20261007)   # 固定种子 ⇒ 判据可复现
    _n = Image.effect_noise(size, 48).convert("L")
    _im = Image.merge("RGB", (_n, _n.point(lambda v: min(255, int(v * 1.05))), _n.point(lambda v: max(0, int(v * 0.9)))))
    _d = ImageDraw.Draw(_im)
    for _i in range(0, size[0], 3):                       # 加一些结构
        _d.line([(0, _i), (size[0], size[1] - _i)], fill=(_r.randrange(256), _r.randrange(256), _r.randrange(256)), width=1)
    return _png_of(_im.filter(__import__("PIL.ImageFilter", fromlist=["ImageFilter"]).GaussianBlur(1.2)))


def _flat_png(size=(256, 256), color=(30, 120, 200)) -> bytes:
    """**纯色/合成类**夹具 —— 用来证明"转完更大时**必须回退 PNG**"（不为了省而变大）。"""
    return _png_of(Image.new("RGB", size, color))


def _png_of(im: Image.Image) -> bytes:
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


print("\n══ ① 转换本身（照片类夹具）══")
_src = _photo_png()
_out, _ext = IG._to_jpeg_bytes(_src)
ok("① 出的是 JPEG（后缀 jpg）", _ext == "jpg", "ext=%s" % _ext)
_im = Image.open(io.BytesIO(_out))
ok("② 是**合法** JPEG（PIL 能打开、格式对）", _im.format == "JPEG", str(_im.format))
ok("③ **比 PNG 小**（这是本次改动的全部意义）",
   len(_out) < len(_src), "PNG %d B → JPEG %d B（↓%.0f%%）"
   % (len(_src), len(_out), 100 * (1 - len(_out) / float(len(_src)))))
ok("④ 长宽**不变**（不许顺手缩放）", _im.size == (256, 256), str(_im.size))
_q1 = IG._to_jpeg_bytes(_src, quality=1)[0]
ok("⑤ 反例锚：`quality` 参数**真的生效**（q=1 必须明显小于 q=%d）—— 否则这条判据是恒真的"
   % IG.IMAGE_JPEG_QUALITY, len(_q1) < len(_out) * 0.6,
   "q1=%d B  q%d=%d B" % (len(_q1), IG.IMAGE_JPEG_QUALITY, len(_out)))

print("\n══ ② 透明通道不许变黑（JPEG 无 alpha）══")
# ⛔ 夹具要**带噪声**：纯色 RGBA 会让 JPEG 反而更大、代码回退 PNG
#   ⇒ 那时测到的是"PNG 丢 alpha 变黑"，而不是"JPEG 合成白底"（我第一次就栽在这）。
_rgba_base = Image.open(io.BytesIO(_photo_png())).convert("RGB")
_rgba = _rgba_base.convert("RGBA")
_alpha = Image.new("L", _rgba.size, 255)
ImageDraw.Draw(_alpha).rectangle([0, 0, 24, 24], fill=0)      # 左上角**全透明**
_rgba.putalpha(_alpha)
_b = io.BytesIO()
_rgba.save(_b, "PNG")
_j, _e2 = IG._to_jpeg_bytes(_b.getvalue())
ok("⑥ 前置：这一张**确实走了 JPEG**（否则测不到白底合成）", _e2 == "jpg", "ext=%s" % _e2)
_px = Image.open(io.BytesIO(_j)).convert("RGB").getpixel((2, 2))
ok("⑦ 透明区合成到**白底**（原来是黑 ⇒ 那才是把图画坏）",
   all(_v >= 240 for _v in _px), "透明区像素=%s" % (_px,))
_px2 = Image.open(io.BytesIO(_j)).convert("RGB").getpixel((200, 200))
ok("⑧ 非透明区**没被改成白**（只动透明那块）", sum(_px2) > 30, "%s" % (_px2,))

print("\n══ ③ 失败必须原样落盘（不许丢图）══")
_junk = b"this is not an image at all"
_o3, _e3 = IG._to_jpeg_bytes(_junk)
ok("⑧ 不是位图 ⇒ **原样返回**（字节一字不差、后缀回 png）",
   _o3 == _junk and _e3 == "png", "ext=%s len=%d" % (_e3, len(_o3)))
_tiny = _flat_png(size=(4, 4), color=(9, 9, 9))
_o4, _e4 = IG._to_jpeg_bytes(_tiny)
ok("⑨ ⭐ 纯色/合成图（转完**反而更大**）⇒ 必须**回退 PNG**（不为了「省」而变大）"
   "　　实测：纯色 256² 的 PNG 极小，JPEG 会大好几倍 —— 这道闸不是装饰",
   _e4 == "png" and len(_o4) == len(_tiny), "ext=%s %d → %d" % (_e4, len(_tiny), len(_o4)))

print("\n══ ④ 真落盘（这是原来完全没覆盖的一段）══")
_tmp = tempfile.mkdtemp(prefix="pm_imgfmt_")
_old = os.getcwd()
try:
    os.chdir(_tmp)
    _p = IG._save_image_bytes(_photo_png(), "probe")
    _exists = os.path.exists(_p)
    _real_ext = os.path.splitext(_p)[1].lower()
    _fmt = Image.open(_p).format if _exists else "?"
    ok("⑩ 落了盘、且**后缀与真实格式一致**（原来写死 `.png` 其实就是谎）",
       _exists and _real_ext == ".jpg" and _fmt == "JPEG", "path=%s fmt=%s" % (os.path.basename(_p), _fmt))
    _now = Image.open(_p)
    ok("⑪ 落盘后长宽与原图一致", _now.size == (256, 256), str(_now.size))
    _sz = os.path.getsize(_p)
    ok("⑫ 落盘体量确实小（≤ 原 PNG 的 45%，实测口径见 `IMAGE_JPEG_QUALITY`）",
       _sz <= len(_src) * 0.45, "%d B vs PNG %d B" % (_sz, len(_src)))
finally:
    os.chdir(_old)
    import shutil as _sh
    _sh.rmtree(_tmp, ignore_errors=True)

print("\n══ ⑤ 静态锚：档位与口径 ══")
_src_txt = open(os.path.join(ROOT, "agent", "image_gen.py"), encoding="utf-8").read()
ok("⑬ 画质常量就是用户看过的 **85**（不是别人改的别的数）",
   IG.IMAGE_JPEG_QUALITY == 85, "IMAGE_JPEG_QUALITY=%s" % IG.IMAGE_JPEG_QUALITY)
ok("⑭ **不做色度抽样**（`subsampling=0`）：抽了颜色会偏，而颜色比细节更容易被看出来",
   "subsampling=0" in _src_txt)
ok("⑮ 只影响**新图**：源码里没有「重压既有图库」的动作（既有 PNG 一张不动）",
   "gen_images" in _src_txt and "rglob" not in _src_txt.split("_save_image_bytes")[0][-2000:])
ok("⑯ 回退路径写明「宁可多占空间也不丢图」（这是本改动的红线）",
   "宁可多占空间" in _src_txt or "原样落盘" in _src_txt)

print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for _x in FAIL:
        print("  - " + _x)
sys.exit(1 if FAIL else 0)
