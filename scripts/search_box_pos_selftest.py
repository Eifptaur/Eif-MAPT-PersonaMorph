# -*- coding: utf-8 -*-
"""`chat_ocr.search_box_rect` 的**位置闸**判据（2026-10-10 补，生产反馈驱动）。

## 为什么要这条判据
生产反馈：「识别白底圆边矩形时，可能会将微信左上角的搜索框识别到，然后把信息送出」。
根因：`search_box_rect()` 会被当作**通用"白底圆角矩形"探测器**用在**浮层帧**上
（`wechat._focus_search_input`）和**兜底放宽列范围**的路径上（`find_search_entry` ②b，
上界放到 0.60W）⇒ 画面里**别处**只要有一条"接近白色、横向连贯、够高"的东西，
就可能被当成搜索框 ⇒ 上层**点它、往它投字**。
⇒ 补**位置闸**：矩形中心必须落在画面**左 60%**（微信搜索框在三种形态下实测都在左上）。
`SEARCH_BOX_MAX_CX_REL` 就是这道闸。

## 本判据守三件事
  A **不误杀**：白框在左上（真搜索框位置）⇒ 必须照旧认出。
  B **拦误报**：白框中心越过 60%W ⇒ 必须返回 None。
  C **反向控制**：把闸放开，那个被拦的框**应该**被认出来（证明闸真的在拦）。

## 合成帧为什么这么造
`search_box_rect` 有自己的必要条件（`peak>=0.20 且 背景列占比<=bg_max`），
随便画一个白框**根本触发不了它**（实测：白框占满整个 span ⇒ 背景列本身也白 ⇒ 被 bg_max 挡掉）。
⇒ 本判据按"**窄框 + 暗底**"造帧：span 内留出足够暗的背景列，框只占一段 ⇒ 才真能触发探测器。

用法：`python scripts/search_box_pos_selftest.py`
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw  # noqa: E402

from agent import chat_ocr as CO  # noqa: E402

_P = _F = 0


def ok(name, cond, detail=""):
    global _P, _F
    if cond:
        _P += 1
    else:
        _F += 1
    print("  %s %s%s" % ("OK  " if cond else "FAIL", name,
                         ("  [%s]" % detail) if detail else ""))


# 与既有判据同源的几何：1139×890，导轨 0..89、列表 89..383
W, H, RAIL, LIST_HI, TITLE = 1139, 890, 89, 383, 45


def frame(box, list_bg=210, span_box=None):
    """造一帧"像主窗"的画面：标题栏 + 左导轨 + 列表列（暗底） + 一个白圆角框。

    ⛔ **底色必须是暗的，不能留白**（2026-10-10 修）：
       首版帧底色是 (255,255,255) 纯白，只在 `span_box` 那段铺暗底 ⇒
       当 `span_box` 从 400 起时，`100..399` 是**纯白**、宽 299 —— 比用户真画的那个框还长，
       于是探测器"取最长连续段"**必然选中那片纯白**，
       判据就永远看不到自己画的框（实测：三条 B/D 判据全返回 `(100,49,399,126)`）。
       那不是"闸没生效"，而是**帧本身不真实**：真实微信画面里，
       被搜索的那条带**整体是暗底**（会话列表 / 浮层底色），搜索框是上面**唯一**的白块。
       ⇒ 改成：**先把整个被搜索条带铺成暗底（`tape`），再画白框**。
       `span_box` 保留，用于把暗底**再扩**到框所在的那一段（模拟"带比列表列更宽"的放宽路径）。
    """
    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, W, TITLE - 1), fill=(117, 117, 117))
    d.rectangle((0, TITLE, RAIL - 1, H), fill=(58, 58, 58))
    # ① 整条被搜索带先铺暗底：从导轨右侧一直到画面右端（覆盖任何可能的 span 上界）
    d.rectangle((RAIL, TITLE, W - 1, H), fill=(list_bg, list_bg, list_bg))
    # ② `span_box` 若另给一段更暗的底色，用来模拟"某个更暗的区域"（可选）
    if span_box:
        d.rectangle((span_box[0], TITLE, span_box[1], H), fill=(list_bg, list_bg, list_bg))
    if box:
        d.rounded_rectangle(box, radius=8, fill=(252, 252, 252))
    return im


print("=" * 72)
print("search_box_rect 位置闸判据")
print("=" * 72)
print("  位置闸阈值 SEARCH_BOX_MAX_CX_REL = %.2f" % CO.SEARCH_BOX_MAX_CX_REL)

# ── 正向控制：先证明"框放对地方能认出来"，否则后面的拦是对着空气拦 ──
print("\n── 0. 正向控制：span 内左侧窄框 ⇒ 认得出（否则后面判据全是空转）──")
_r_probe = CO.search_box_rect(frame((160, 60, 270, 110)))
ok("左侧窄框认得出", _r_probe is not None, str(_r_probe))

# ── A. 不误杀（三种真实形态的"左上"位置）──
print("\n── A. 左上（真位置）⇒ 必须照旧认出 ──")
_left_cases = [
    ("主窗·列表列左侧", (160, 60, 270, 110)),
]
for _name, _box in _left_cases:
    _r = CO.search_box_rect(frame(_box))
    _rel = (((_r[0] + _r[2]) / 2.0) / W) if _r else -1
    ok("%s 认得出且中心 ≤0.60W" % _name, bool(_r) and _rel <= CO.SEARCH_BOX_MAX_CX_REL,
       "rect=%s cx_rel=%.3f" % (_r, _rel))

# ── B. 拦误报 ──
print("\n── B. 中心越过 60%W ⇒ 必须返回 None（不许点、不许投字）──")
# 注意：框必须**落在被搜索的列范围里**才谈得上"误报"。
# 主窗的列范围是 [left-240, left-6]；这里直接给一个跨过 0.60W 的 span，
# 模拟 `find_search_entry` ②b 与浮层那两条"列范围放宽"的路径。
_r_bad = CO.search_box_rect(frame((700, 60, 810, 110)), span=(100, 1000))
ok("放宽列范围后，中心≈0.66W 的框被拦", _r_bad is None, str(_r_bad))
_r_bad2 = CO.search_box_rect(frame((900, 60, 1010, 110)), span=(100, 1050))
ok("中心≈0.84W 的框被拦", _r_bad2 is None, str(_r_bad2))

# ── C. 反向控制：把闸放开，被拦的那个框**应该**被认出来 ──
print("\n── C. 反向控制：闸放开 ⇒ 刚才被拦的框应该认出来 ──")
_orig = CO.SEARCH_BOX_MAX_CX_REL
try:
    CO.SEARCH_BOX_MAX_CX_REL = 1.0
    _r_relaxed = CO.search_box_rect(frame((700, 60, 810, 110)), span=(100, 1000))
    ok("闸放开后中心≈0.66W 的框被认出（证明闸真的在拦）",
       _r_relaxed is not None, str(_r_relaxed))
finally:
    CO.SEARCH_BOX_MAX_CX_REL = _orig

# ── D. 边界（比例判据，不写死像素）──
print("\n── D. 边界行为 ──")
_r_in = CO.search_box_rect(frame((550, 60, 660, 110)), span=(100, 1000))
_rel_in = (((_r_in[0] + _r_in[2]) / 2.0) / W) if _r_in else -1
ok("中心≈0.53W ⇒ 通过", bool(_r_in), "cx_rel=%.3f" % _rel_in)
_r_out = CO.search_box_rect(frame((640, 60, 750, 110)), span=(100, 1000))
ok("中心≈0.61W ⇒ 拦下", _r_out is None, str(_r_out))

print("\n" + "=" * 72)
print("通过 %d / 失败 %d" % (_P, _F))
print("=" * 72)
sys.exit(1 if _F else 0)
