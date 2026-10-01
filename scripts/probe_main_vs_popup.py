# -*- coding: utf-8 -*-
"""probe_main_vs_popup.py —— **主窗 vs 独立窗**抓图对照（找"独立窗抓不到图形层"的原因）

背景（报告 §49）：`PrintWindow` 抓独立窗时**只拿到文字层**（标题/时间戳），
    漏掉自绘图形层（气泡/输入框/按钮）—— 内容像素占比只有 1.89%，而正常聊天窗约 8~20%。
    但项目**主窗截图一直是好的**（会话头 OCR 就靠它）⇒ 机制本身可行 ⇒ 差异在"窗口状态"。
本脚本：对主窗与独立窗各做一组对照，输出**内容占比**，并试"先还原/触发重绘再抓"：
    ① 直接 PrintWindow       ② 先 SW_SHOWNOACTIVATE 再 PrintWindow
    ③ 先 SetWindowPos(SWP_NOMOVE|NOSIZE|NOZORDER) 触发 WM_PAINT 再 PrintWindow
用法：./runtime/python/python.exe -u scripts/probe_main_vs_popup.py
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
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SW_SHOWNOACTIVATE = 4
_HWND_TOP = 0


class _R(ct.Structure):
    _fields_ = [("l", ct.c_long), ("t", ct.c_long), ("r", ct.c_long), ("b", ct.c_long)]


class _BMIH(ct.Structure):
    _fields_ = [("biSize", ct.c_uint32), ("biWidth", ct.c_long), ("biHeight", ct.c_long),
                ("biPlanes", ct.c_ushort), ("biBitCount", ct.c_ushort),
                ("biCompression", ct.c_uint32), ("biSizeImage", ct.c_uint32),
                ("biXPelsPerMeter", ct.c_long), ("biYPelsPerMeter", ct.c_long),
                ("biClrUsed", ct.c_uint32), ("biClrImportant", ct.c_uint32)]


def _grab_pw(hw):
    r = _R()
    _U32.GetWindowRect(hw, ct.byref(r))
    w, h = r.r - r.l, r.b - r.t
    if w <= 0 or h <= 0:
        return None
    hdc = _U32.GetWindowDC(hw)
    mdc = _G32.CreateCompatibleDC(hdc)
    bmp = _G32.CreateCompatibleBitmap(hdc, w, h)
    _G32.SelectObject(mdc, bmp)
    _U32.PrintWindow(hw, mdc, 2)
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
    _U32.ReleaseDC(hw, hdc)
    return img


def content_ratio(img):
    """返回 (内容像素占比%, 主色, 主色占比%, 去重色数)。"""
    px = img.load()
    w, h = img.size
    c = Counter()
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b = px[x, y][:3]
            c[(r // 8 * 8, g // 8 * 8, b // 8 * 8)] += 1
    tot = sum(c.values()) or 1
    bg, bgn = c.most_common(1)[0]
    keep = 0
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            r, g, b = px[x, y][:3]
            if not (abs(r - bg[0]) <= 14 and abs(g - bg[1]) <= 14 and abs(b - bg[2]) <= 14):
                keep += 1
    n = len(range(0, w, 3)) * len(range(0, h, 3))
    return 100.0 * keep / max(1, n), bg, 100.0 * bgn / tot, len(c)


def try_variants(hw, tag, out_dir):
    rows = []
    # ① 直接抓
    img = _grab_pw(hw)
    if img:
        r = content_ratio(img)
        img.save(os.path.join(out_dir, "mv-%s-1direct.png" % tag))
        rows.append(("① 直接 PrintWindow", r))
    # ② 先 SW_SHOWNOACTIVATE 还原
    try:
        _U32.ShowWindow(hw, _SW_SHOWNOACTIVATE)
        time.sleep(0.35)
        img = _grab_pw(hw)
        if img:
            r = content_ratio(img)
            img.save(os.path.join(out_dir, "mv-%s-2shownoactivate.png" % tag))
            rows.append(("② 先 SW_SHOWNOACTIVATE", r))
    except Exception as e:
        rows.append(("② 先 SW_SHOWNOACTIVATE", ("异常 " + str(e)[:40],) * 4))
    # ③ 触发一次 WM_PAINT（SetWindowPos 带 NOMOVE/NOSIZE/NOZORDER/NOACTIVATE）
    try:
        _U32.SetWindowPos(hw, 0, 0, 0, 0, 0,
                          _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE)
        time.sleep(0.35)
        img = _grab_pw(hw)
        if img:
            r = content_ratio(img)
            img.save(os.path.join(out_dir, "mv-%s-3setwindowpos.png" % tag))
            rows.append(("③ 触发 WM_PAINT 后再抓", r))
    except Exception as e:
        rows.append(("③ 触发 WM_PAINT 后再抓", ("异常 " + str(e)[:40],) * 4))
    return rows


def main():
    wins = [w for w in WW.list_wechat_windows() if w["title"].strip()]
    print("微信可见会话窗口 %d 个：" % len(wins))
    for w in wins:
        print("  hwnd=%-12d %-24s %dx%d" % (w["hwnd"], w["title"], w["w"], w["h"]))
    if not wins:
        return 1
    out_dir = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "_临界样本")
    os.makedirs(out_dir, exist_ok=True)
    # 主窗 = 面积最大的那个；独立窗 = 其余
    main_w = max(wins, key=lambda w: w["w"] * w["h"])
    popups = [w for w in wins if w["hwnd"] != main_w["hwnd"]] or []
    print()
    print("=" * 84)
    print("对照实验：内容像素占比（越高越好；正常聊天窗约 8~20%%）")
    print("=" * 84)
    print("%-28s %-12s %-16s %-10s %s" % ("窗口/方法", "主色", "主色占比", "内容占比", "去重色数"))
    for tag, w in [("main", main_w)] + [("popup", p) for p in popups]:
        for name, r in try_variants(w["hwnd"], tag, out_dir):
            print("%-28s %-12s %13.1f%% %9.2f%% %8d"
                  % ("%s [%s]" % (name, w["title"][:10]), str(r[1]), r[2], r[0], r[3]))
    print()
    print("判读：若主窗某档能到 8%%+ 而独立窗三档都 <3%% ⇒ 差异在【窗口本身】而不在抓法；")
    print("     若某档让独立窗也升上来 ⇒ 差异在【抓之前的前置动作】，方案 B 就成立了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
