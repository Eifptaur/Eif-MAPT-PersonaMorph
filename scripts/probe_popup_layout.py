# -*- coding: utf-8 -*-
"""probe_popup_layout.py —— 抓「独立会话弹窗」的图，看它的布局（T12 关键验证）

用途：用户提出「让目标群变成独立弹窗，程序只对那个弹窗操作」⇒ 需要先看清独立窗里**到底有什么**：
     有没有会话列表？输入框在哪？标题栏什么样？
输出：`报告/ocr瓶颈-20260930/_临界样本/popup-<名字>.png` + 控制台打印布局要素
用法：./runtime/python/python.exe -u scripts/probe_popup_layout.py [会话名]
"""
import ctypes as ct
import ctypes.wintypes as wt
import logging
import os
import sys

logging.disable(logging.INFO)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PIL import Image  # noqa: E402

_USER32 = ct.windll.user32
_G32 = ct.windll.gdi32


class _RECT(ct.Structure):
    _fields_ = [("l", ct.c_long), ("t", ct.c_long), ("r", ct.c_long), ("b", ct.c_long)]


class _BMIH(ct.Structure):
    _fields_ = [("biSize", ct.c_uint32), ("biWidth", ct.c_long), ("biHeight", ct.c_long),
                ("biPlanes", ct.c_ushort), ("biBitCount", ct.c_ushort),
                ("biCompression", ct.c_uint32), ("biSizeImage", ct.c_uint32),
                ("biXPelsPerMeter", ct.c_long), ("biYPelsPerMeter", ct.c_long),
                ("biClrUsed", ct.c_uint32), ("biClrImportant", ct.c_uint32)]


def grab(hwnd: int):
    """PrintWindow 抓某窗口 ⇒ PIL.Image；失败给 None。"""
    r = _RECT()
    _USER32.GetWindowRect(hwnd, ct.byref(r))
    w, h = r.r - r.l, r.b - r.t
    if w <= 0 or h <= 0:
        return None
    hdc = _USER32.GetWindowDC(hwnd)
    mdc = _G32.CreateCompatibleDC(hdc)
    bmp = _G32.CreateCompatibleBitmap(hdc, w, h)
    _G32.SelectObject(mdc, bmp)
    ok = _USER32.PrintWindow(hwnd, mdc, 2)
    bi = _BMIH()
    bi.biSize = ct.sizeof(_BMIH)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = ct.create_string_buffer(w * h * 4)
    _G32.GetDIBits(mdc, bmp, 0, h, buf, ct.byref(bi), 0)
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    _G32.DeleteObject(bmp)
    _G32.DeleteDC(mdc)
    _USER32.ReleaseDC(hwnd, hdc)
    return img if ok else img          # ok=0 在部分机器上仍能拿到内容，交由调用方判空


def main():
    from agent import wechat_windows as WW
    wins = [w for w in WW.list_wechat_windows() if w["title"].strip()]
    print("微信可见会话窗口 %d 个：" % len(wins))
    for w in wins:
        print("  hwnd=%-12d %-24s %dx%d" % (w["hwnd"], w["title"], w["w"], w["h"]))
    want = sys.argv[1] if len(sys.argv) > 1 else (wins[-1]["title"] if wins else "")
    tgt = [w for w in wins if w["title"] == want]
    if not tgt:
        print("\n没找到 %r 的窗口（可用参数指定）" % want)
        return 1
    hw = tgt[0]["hwnd"]
    img = grab(hw)
    out_dir = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "_临界样本")
    os.makedirs(out_dir, exist_ok=True)
    safe = "".join(ch for ch in want if ch not in '\\/:*?"<>|')[:20]
    out = os.path.join(out_dir, "popup-%s.png" % safe)
    if img is None:
        print("抓图失败")
        return 1
    img.save(out)
    print("\n抓 %r（hwnd=%d）⇒ %s  %dx%d" % (want, hw, out, img.width, img.height))
    # 粗判：这张图里有没有"会话列表特征"（左侧一列头像块）与"输入框特征"（底部一条浅色带）
    px = img.load()
    w, h = img.size
    bottom = []
    for y in range(int(h * 0.82), h, max(1, int(h * 0.02))):
        dark = sum(1 for x in range(0, w, 8)
                   if sum(px[x, y][:3]) < 600)
        bottom.append((y, dark))
    print("  底部区域(82pct~100pct) 的暗像素列数：%s" % bottom[:6])
    print("  ⇒ 若底部出现'明显的一条'，很可能就是输入框所在")
    return 0


if __name__ == "__main__":
    sys.exit(main())
