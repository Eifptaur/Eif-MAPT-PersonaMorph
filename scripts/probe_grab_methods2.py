# -*- coding: utf-8 -*-
"""probe_grab_methods2.py —— 第二轮抓法穷举（`PrintWindow` 之外的手段）

用户指出：「**他一直都在桌面上，我一直能看到他呀**……**他肯定渲染出来了的**」
⇒ 说明"没渲染"这个假设不成立 ⇒ 问题在**抓法**上。
本脚本一次穷举这些**尚未试过**的手段：
  A. `SendMessage(WM_PRINT, hdc, PRF_*)` —— 比 `PrintWindow` 更底层，直接要窗口自己画
  B. `PrintWindow` 到 **物理像素尺寸** 的位图（排除"逻辑/物理尺寸不匹配"）
  C. `PrintWindow` 前先 `RedrawWindow(hwnd, NULL, NULL, RDW_ALLCHILDREN|RDW_UPDATENOW|RDW_FRAME)`
  D. `BitBlt` 从**窗口 DC**（不是屏幕 DC）—— 抓"窗口自身绘制缓冲"
  E. `GetDCEx(hwnd, NULL, DCX_CACHE|DCX_WINDOW)` 后 `BitBlt` 自窗口 DC
输出：`_临界样本/g2-<方法>-<窗口>.png` + 内容占比对比表
用法：./runtime/python/python.exe -u scripts/probe_grab_methods2.py
"""
import ctypes as ct
import logging
import os
import sys
import time
from collections import Counter

logging.disable(logging.INFO)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PIL import Image  # noqa: E402
from agent import wechat_windows as WW  # noqa: E402

_U32 = ct.windll.user32
_G32 = ct.windll.gdi32
_SRCCOPY = 0x00CC0020
_WM_PRINT = 0x0317
_PRF_NONCLIENT = 0x00000002
_PRF_CLIENT = 0x00000004
_PRF_ERASEBKGND = 0x00000008
_PRF_CHILDREN = 0x00000010
_PRF_OWNED = 0x00000020
_RDW_INVALIDATE = 0x0001
_RDW_UPDATENOW = 0x0100
_RDW_ALLCHILDREN = 0x0080
_RDW_FRAME = 0x0400
_DCX_CACHE = 0x00000020
_DCX_WINDOW = 0x00000002


class _R(ct.Structure):
    _fields_ = [("l", ct.c_long), ("t", ct.c_long), ("r", ct.c_long), ("b", ct.c_long)]


class _BMIH(ct.Structure):
    _fields_ = [("biSize", ct.c_uint32), ("biWidth", ct.c_long), ("biHeight", ct.c_long),
                ("biPlanes", ct.c_ushort), ("biBitCount", ct.c_ushort),
                ("biCompression", ct.c_uint32), ("biSizeImage", ct.c_uint32),
                ("biXPelsPerMeter", ct.c_long), ("biYPelsPerMeter", ct.c_long),
                ("biClrUsed", ct.c_uint32), ("biClrImportant", ct.c_uint32)]


def _size(hw):
    r = _R()
    _U32.GetWindowRect(hw, ct.byref(r))
    return r.r - r.l, r.b - r.t


def _to_img(mdc, bmp, w, h):
    bi = _BMIH()
    bi.biSize = ct.sizeof(_BMIH)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = ct.create_string_buffer(w * h * 4)
    _G32.GetDIBits(mdc, bmp, 0, h, buf, ct.byref(bi), 0)
    return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")


def _mk(hw, scale=1.0):
    w, h = _size(hw)
    w, h = int(w * scale), int(h * scale)
    hdc = _U32.GetWindowDC(hw)
    mdc = _G32.CreateCompatibleDC(hdc)
    bmp = _G32.CreateCompatibleBitmap(hdc, w, h)
    _G32.SelectObject(mdc, bmp)
    return hdc, mdc, bmp, w, h


def _done(hdc, mdc, bmp, hw):
    _G32.DeleteObject(bmp)
    _G32.DeleteDC(mdc)
    _U32.ReleaseDC(hw, hdc)


def method_A_wmprint(hw, scale=1.0):
    hdc, mdc, bmp, w, h = _mk(hw, scale)
    flags = _PRF_NONCLIENT | _PRF_CLIENT | _PRF_ERASEBKGND | _PRF_CHILDREN | _PRF_OWNED
    _U32.SendMessageW(hw, _WM_PRINT, mdc, flags)
    img = _to_img(mdc, bmp, w, h)
    _done(hdc, mdc, bmp, hw)
    return img


def method_B_pw_physical(hw, scale=1.5):
    hdc, mdc, bmp, w, h = _mk(hw, scale)
    _U32.PrintWindow(hw, mdc, 2)
    img = _to_img(mdc, bmp, w, h)
    _done(hdc, mdc, bmp, hw)
    return img


def method_C_redraw_pw(hw):
    _U32.RedrawWindow(hw, None, None,
                      _RDW_INVALIDATE | _RDW_UPDATENOW | _RDW_ALLCHILDREN | _RDW_FRAME)
    time.sleep(0.2)
    hdc, mdc, bmp, w, h = _mk(hw)
    _U32.PrintWindow(hw, mdc, 2)
    img = _to_img(mdc, bmp, w, h)
    _done(hdc, mdc, bmp, hw)
    return img


def method_D_bitblt_windowdc(hw):
    hdc, mdc, bmp, w, h = _mk(hw)
    _G32.BitBlt(mdc, 0, 0, w, h, hdc, 0, 0, _SRCCOPY)
    img = _to_img(mdc, bmp, w, h)
    _done(hdc, mdc, bmp, hw)
    return img


def method_E_dcEX(hw):
    w, h = _size(hw)
    wdc = _U32.GetDCEx(hw, None, _DCX_CACHE | _DCX_WINDOW)
    mdc = _G32.CreateCompatibleDC(wdc)
    bmp = _G32.CreateCompatibleBitmap(wdc, w, h)
    _G32.SelectObject(mdc, bmp)
    _G32.BitBlt(mdc, 0, 0, w, h, wdc, 0, 0, _SRCCOPY)
    img = _to_img(mdc, bmp, w, h)
    _G32.DeleteObject(bmp)
    _G32.DeleteDC(mdc)
    _U32.ReleaseDC(hw, wdc)
    return img


def content_ratio(img):
    px = img.load()
    w, h = img.size
    c = Counter()
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b = px[x, y][:3]
            c[(r // 8 * 8, g // 8 * 8, b // 8 * 8)] += 1
    tot = sum(c.values()) or 1
    bg, bgn = c.most_common(1)[0]
    keep = tot_n = 0
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            r, g, b = px[x, y][:3]
            tot_n += 1
            if not (abs(r - bg[0]) <= 14 and abs(g - bg[1]) <= 14 and abs(b - bg[2]) <= 14):
                keep += 1
    return 100.0 * keep / max(1, tot_n), bg, 100.0 * bgn / tot


def main():
    wins = [w for w in WW.list_wechat_windows() if w["title"].strip()]
    if not wins:
        print("没有可见微信窗口")
        return 1
    print("微信可见窗口：")
    for w in wins:
        print("  hwnd=%-12d %-24s %dx%d" % (w["hwnd"], w["title"], w["w"], w["h"]))
    out_dir = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "_临界样本")
    os.makedirs(out_dir, exist_ok=True)
    methods = [
        ("A WM_PRINT(SendMessage)", method_A_wmprint),
        ("B PrintWindow@1.5x物理", method_B_pw_physical),
        ("C RedrawWindow+PrintWindow", method_C_redraw_pw),
        ("D BitBlt(窗口DC)", method_D_bitblt_windowdc),
        ("E GetDCEx+BitBlt", method_E_dcEX),
    ]
    print()
    print("%-38s %-11s %-9s %s" % ("窗口 / 方法", "尺寸", "内容占比", "主色"))
    for w in wins:
        safe = "".join(ch for ch in w["title"] if ch not in '\\/:*?"<>|')[:12]
        for name, fn in methods:
            try:
                img = fn(w["hwnd"])
                r, bg, bgr = content_ratio(img)
                img.save(os.path.join(out_dir, "g2-%s-%s.png" % (name.split()[0], safe)))
                print("%-38s %-11s %8.2f%% %s(%.0f%%)"
                      % ("[%s] %s" % (w["title"][:12], name),
                         "%dx%d" % img.size, r, str(bg), bgr))
            except Exception as e:
                print("%-38s 异常 %s" % ("[%s] %s" % (w["title"][:12], name), str(e)[:44]))
        print()
    print("判读：正常聊天窗内容占比约 8~20%；哪一行显著升高，那种方法就是答案。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
