#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 223 项：独立窗停在「多选 / 转发」模式时，发送链能认出来并**先复位再打**。

守的是这个已被真机证实的缺陷：
  · 独立窗停在「多选 / 转发」模式时，底部换成一条工具条（逐条转发 / 合并转发 / 复制 / 收藏 /
    保存 / 删除），**输入框整条不存在**；
  · 于是 `send_button_green_ratio` 恒为 0 ⇒ 被读成"字没打进去"，
    真相是"这个窗口此刻根本没有输入框" ⇒ 用户看到的是「发了没反应」。

判据分五段：
  A. `_probe_forward_bar`：**正向**——给一张"底部是工具条"的合成图，必须认出来；
     **反向锚**——给一张"底部是正常输入框"的合成图，**必须判不出**（不许把正常态误判成多选态）；
     再给一张空白图 ⇒ 也不许判出（判不出来就不动手）。
  B. `_composer_reset_multiselect`：落点来自工具条上最右侧那个深色图标（✕），
     **不许用 ESC**（实测投 ESC 会把独立窗整个关掉）。
  C. 发送链接入：`send_text_posted` 里在打字前调一次；复位失败 ⇒ 不硬打、不假报成功。
  D. 语义边界：复位**不构成拒发理由**（第 215 项定调）——复位成功后照常发。
  E. 源码层：全文件内这套件只按"正面证据"触发，触发点唯一。

跑法： runtime\\python\\python.exe scripts\\multiselect_reset_selftest.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import _srcslice as _ss  # noqa: E402
import agent.wechat as W  # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


SRC = open(os.path.join(ROOT, "agent", "wechat.py"), "r", encoding="utf-8",
           errors="replace").read()


# ── 合成图工具 ────────────────────────────────────────────────────────────────
def _mkimg(W_, H_, rows):
    """造一张 `W_ x H_` 的图；`rows` = 函数 `(y) -> (bg, [(x0,x1), ...] 深色簇)`。

    返回值给出 `.load()` / `.size`，形状与 PIL.Image 一致（本函数族只用到这两样）。
    """
    px = {}
    for y in range(H_):
        bg, blobs = rows(y)
        for x in range(W_):
            px[(x, y)] = (bg, bg, bg)
        for (x0, x1) in blobs:
            for x in range(max(0, x0), min(W_, x1)):
                px[(x, y)] = (60, 60, 60)
    class _Img:
        size = (W_, H_)
        def load(self):
            return px
    return _Img()


def _normal_bottom(y):
    """正常态底带：上半全是亮底（像输入框），最底部一条工具栏带。"""
    if y < 15:
        return (250, [])
    return (245, [])


def _toolbar_bottom(y):
    """多选态底带：背景灰（不是亮白），稀疏的深色图标簇。"""
    base = 236 if y < 14 else 228
    if y % 100 < 6:                      # 每行图标的下半部分才出现深色
        return (base, [(40, 52), (160, 172), (280, 292), (400, 412), (520, 532),
                       (700, 712)])
    return (base, [])


class FakeGui:
    origin_x = 1000
    origin_y = 200
    render_rect = (1020, 220, 1516, 767)
    right_pane_left = 60


class FakeBackend:
    def __init__(self):
        self.calls = []
    def click(self, hwnd, pt, right=False, **kw):
        self.calls.append(("click", pt, right))
        return True, ""


def _looks_like_toolbar(rows, box=(0, 0, 456, 330)):
    """`_row_is_input_box` 的**逐行**判据：整行亮且深色少 ⇒ 像输入框内部。"""
    px = {}
    for y in range(box[3]):
        bg, blobs = rows(y + 100)
        for x in range(box[2]):
            px[(x, y)] = (bg, bg, bg)
        for (x0, x1) in blobs:
            for x in range(x0, x1):
                if 0 <= x < box[2]:
                    px[(x, y)] = (60, 60, 60)
    return px


print("=== 第 223 项：多选态识别与复位 ===")

# ⚠️ **合成图是"结构近似"，不是真机取证** —— 真机那一帧由
#    `_scratch/_realrun/probe_multiselect.py --go` 采（见本文件 D 段）。
#    这里的合成图只用来钉住"判据的形状"：稀疏深色图标簇 ⇒ 认得出；整片亮白 ⇒ 不许认。
#    ⛔ 所以下面正向用例的背景亮度取**实测工具条那一档**（比输入框暗的中性灰），
#      不许调成"随便一个数只要让用例过"。

# ── A. `_probe_forward_bar`：正向 + 反向锚 ──────────────────────────────────
print("\n── A. `_probe_forward_bar`（正向认得出 / 反向不误判）──")
_ICON_ROWS, _BAND_H = 14, 117


def _mk_strip(H_, W_, band_is_toolbar, bg_lit=250, bg_bar=214):
    """造一张图：上半是**亮底**（像输入框/消息区），最下方 `_BAND_H` 那条带按需换。

    `band_is_toolbar=True` ⇒ 那条带是**工具条**：背景是**偏暗的中性灰**（`bg_bar`）＋
      一排稀疏深色图标簇；
    `False` ⇒ 那条带仍是**亮白**（像输入框内部）。
    """
    band_top = H_ - _BAND_H
    px = {}
    for y in range(H_):
        strip = (y - band_top) if y >= band_top else -1
        toolbar = band_is_toolbar and strip >= 0
        base = (bg_bar if (toolbar and strip >= 12) else bg_lit)
        blobs = []
        if toolbar and 0 <= strip < _ICON_ROWS:
            blobs = [(int(W_ * 0.05) + i * int(W_ * 0.13),
                      int(W_ * 0.05) + i * int(W_ * 0.13) + 12) for i in range(6)]
        for x in range(W_):
            px[(x, y)] = (base, base, base)
        for (a, b) in blobs:
            for x in range(max(0, a), min(W_, b)):
                px[(x, y)] = (60, 60, 60)

    class _Img:
        size = (W_, H_)

        def load(self):
            return px
    return _Img()


try:
    _img_ms = _mk_strip(971, 1200, True)
    _r_ms = W._probe_forward_bar(FakeGui(), img=_img_ms)
    ok("正向：底部是工具条（中性灰底 + 稀疏图标簇）⇒ 认得出",
       bool(_r_ms) and _r_ms.get("how") == "forward_bar", str(_r_ms))
    if _r_ms:
        ok("正向：证据里带**簇数**与**横跨比例**（可对账）",
           int(_r_ms.get("clusters") or 0) >= 3 and float(_r_ms.get("span") or 0) >= 0.6,
           "clusters=%s span=%s" % (_r_ms.get("clusters"), _r_ms.get("span")))
except Exception as _e:
    ok("正向：底部是工具条（中性灰底 + 稀疏图标簇）⇒ 认得出", False, "异常 %s" % _e)

try:
    _img_nm = _mk_strip(971, 1200, False)
    _r_nm = W._probe_forward_bar(FakeGui(), img=_img_nm)
    ok("★反向锚：底部是**正常输入框**（整片亮白）⇒ **必须判不出**",
       _r_nm is None, str(_r_nm))
except Exception as _e:
    ok("★反向锚：底部是**正常输入框**（整片亮白）⇒ **必须判不出**", False, "异常 %s" % _e)

try:
    _r_blank = W._probe_forward_bar(FakeGui(), img=_mk_strip(100, 180, False))
    ok("★反向锚：尺寸过小 ⇒ 判不出（**判不出来就不动手**）", _r_blank is None, str(_r_blank))
except Exception as _e:
    ok("★反向锚：尺寸过小 ⇒ 判不出（**判不出来就不动手**）", False, "异常 %s" % _e)

# ── B. 复位手法：不许用 ESC ────────────────────────────────────────────────
print("\n── B. 复位手法（✕，不用 ESC）──")
_body = _ss.func_src(SRC, "_composer_reset_multiselect")
ok("复位函数存在", bool(_body))
ok("★复位**不投 ESC**（ESC 会关掉独立窗）", ("0x1B" not in _body), _body[:120])
ok("复位投的是**投递点击**（走 `_click_posted` 咽喉点，点后自检新窗）",
   "_click_posted" in _body)
ok("复位后**复探输入框**（闭环，不假报成功）", "_probe_input_box_frame" in _body)
ok("复位找不到 ✕ 时**不猜落点**", "不猜落点" in _body or "找不到深色图标" in _body)

# ── C. 发送链接入 ──────────────────────────────────────────────────────────
print("\n── C. 发送链接入（**独立窗**那条链：`wechat_windows.send_to_popup`）──")
# ⛔ 接入点必须在**独立窗**链上：多选态是"独立窗停在转发模式"这个形态，
#   而 `wechat.py::send_text_posted` 那条链用的是**主窗**帧（`gui`）⇒ 判不到独立窗的底部。
_WW = open(os.path.join(ROOT, "agent", "wechat_windows.py"), "r", encoding="utf-8",
           errors="replace").read()
_post = _ss.func_src(_WW, "send_to_popup")
ok("★独立窗发送链在**打字前**调了多选态检测", "_probe_forward_bar_img(" in _post)
ok("★检测到就**先复位**再往下走", "_composer_reset_multiselect" in _post)
ok("★复位失败 ⇒ **不硬打**（如实返回失败）", "没能复位" in _post)
ok("★复位成功 ⇒ **照常发**（不是拒发理由）", "先复位再打字" in _post)
ok("★帧来自**这个独立窗**（`grab_window(hwnd)`），不是主窗那套 `gui`",
   "grab_window(hwnd" in _post)
ok("复位结果**写进回执**（`multiselect_reset`，可对账）", "multiselect_reset" in _post)

# ── D. 触发点唯一 / 不走别的路 ─────────────────────────────────────────
print("\n── D. 触发点唯一 / 判据形状 ──")
_n_call = _WW.count("_probe_forward_bar_img(")
ok("独立窗链里只被调 1 处（另 1 处在复位函数内，属 wechat.py）", _n_call == 1, "n=%d" % _n_call)
ok("★多选证据里带着**簇数与横跨比例**（可对账，不是一句布尔）",
   ("clusters" in _ss.func_src(SRC, "_probe_forward_bar_img"))
   and ("span" in _ss.func_src(SRC, "_probe_forward_bar_img")), "")
ok("★判据里**不含背景亮度门槛**（主题相关的量 ⇒ 一换主题就失效）",
   "dark_ratio" in SRC and "232.0" not in _ss.func_src(SRC, "_probe_forward_bar_img"),
   "")

print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
