# -*- coding: utf-8 -*-
"""probe_popup_grab.py —— 找出**能抓全独立弹窗**的方法（T12 的卡点）

背景：用户给了一张完整截图（896×959，含标题/聊天区/输入框/发送键），
     而我方 `PrintWindow(flag=2)` 抓到的 613×647 里 **90%+ 是浅灰底色** ⇒
     **只抓到底色、没抓到内容**（Qt 自绘窗的典型失效）。
     本脚本把几种抓法都试一遍，用「主色占比」客观判别哪种能抓到真内容：
       · `PrintWindow` flag = 0 / 1 / 2 / 3
       · 屏幕 DC `BitBlt`（SRCCOPY / CAPTUREBLT）—— 要求窗口可见未被遮挡
输出：`_临界样本/popup-grab-<方法>.png` + 控制台对比表
用法：./runtime/python/python.exe -u scripts/probe_popup_grab.py [会话名关键字]
"""
import ctypes as ct
import logging
import os
import sys
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
_CAPTUREBLT = 0x40000000


class _R(ct.Structure):
    _fields_ = [("l", ct.c_long), ("t", ct.c_long), ("r", ct.c_long), ("b", ct.c_long)]


class _BMIH(ct.Structure):
    _fields_ = [("biSize", ct.c_uint32), ("biWidth", ct.c_long), ("biHeight", ct.c_long),
                ("biPlanes", ct.c_ushort), ("biBitCount", ct.c_ushort),
                ("biCompression", ct.c_uint32), ("biSizeImage", ct.c_uint32),
                ("biXPelsPerMeter", ct.c_long), ("biYPelsPerMeter", ct.c_long),
                ("biClrUsed", ct.c_uint32), ("biClrImportant", ct.c_uint32)]


def _rect(hw):
    r = _R()
    _U32.GetWindowRect(hw, ct.byref(r))
    return r.l, r.t, r.r - r.l, r.b - r.t


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


def grab_printwindow(hw, flag):
    _l, _t, w, h = _rect(hw)
    hdc = _U32.GetWindowDC(hw)
    mdc = _G32.CreateCompatibleDC(hdc)
    bmp = _G32.CreateCompatibleBitmap(hdc, w, h)
    _G32.SelectObject(mdc, bmp)
    ok = _U32.PrintWindow(hw, mdc, flag)
    img = _to_img(mdc, bmp, w, h)
    _G32.DeleteObject(bmp)
    _G32.DeleteDC(mdc)
    _U32.ReleaseDC(hw, hdc)
    return ok, img


def grab_screen_bitblt(hw, captureblt=False):
    l, t, w, h = _rect(hw)
    sdc = _U32.GetDC(0)
    mdc = _G32.CreateCompatibleDC(sdc)
    bmp = _G32.CreateCompatibleBitmap(sdc, w, h)
    _G32.SelectObject(mdc, bmp)
    flags = _SRCCOPY | (_CAPTUREBLT if captureblt else 0)
    ok = _G32.BitBlt(mdc, 0, 0, w, h, sdc, l, t, flags)
    img = _to_img(mdc, bmp, w, h)
    _G32.DeleteObject(bmp)
    _G32.DeleteDC(mdc)
    _U32.ReleaseDC(0, sdc)
    return ok, img


def dominant_ratio(img):
    """返回 (主色, 主色占比, 去重色数) —— 用来客观判"抓到内容没有"。

    ⚠️ 判据：**只抓到底色**时主色占比会很高（如 90%+）；
             抓到真内容时主色占比会明显下降、去重色数明显上升。
    """
    px = img.load()
    w, h = img.size
    c = Counter()
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            r, g, b = px[x, y][:3]
            c[(r // 16 * 16, g // 16 * 16, b // 16 * 16)] += 1
    tot = sum(c.values()) or 1
    top, n = c.most_common(1)[0]
    return top, 100.0 * n / tot, len(c)


def main():
    key = sys.argv[1] if len(sys.argv) > 1 else ""
    wins = [w for w in WW.list_wechat_windows() if w["title"].strip()]
    print("可见会话窗口 %d 个：" % len(wins))
    for w in wins:
        print("  hwnd=%-12d %-24s %dx%d" % (w["hwnd"], w["title"], w["w"], w["h"]))
    if not wins:
        return 1
    tgt = [w for w in wins if key and key in w["title"]] or [wins[-1]]
    hw = tgt[0]["hwnd"]
    print("\n目标：hwnd=%d 标题=%r" % (hw, tgt[0]["title"]))
    print("外层窗口矩形 = %dx%d（GetWindowRect）" % (tgt[0]["w"], tgt[0]["h"]))
    print()
    print("%-30s %-8s %-24s %-8s %s" % ("方法", "ok", "主色", "占比", "去重色数"))
    out_dir = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "_临界样本")
    os.makedirs(out_dir, exist_ok=True)
    res = []
    for flag in (0, 1, 2, 3):
        try:
            ok, img = grab_printwindow(hw, flag)
            c, ratio, n = dominant_ratio(img)
            f = os.path.join(out_dir, "popup-grab-pwflag%d.png" % flag)
            img.save(f)
            print("%-30s %-8s %-24s %7.1f%% %8d" % ("PrintWindow flag=%d" % flag, ok, str(c), ratio, n))
            res.append(("pwflag%d" % flag, ratio, n))
        except Exception as e:
            print("%-30s 异常 %s" % ("PrintWindow flag=%d" % flag, str(e)[:50]))
    for cb in (False, True):
        try:
            ok, img = grab_screen_bitblt(hw, cb)
            c, ratio, n = dominant_ratio(img)
            f = os.path.join(out_dir, "popup-grab-bitblt%s.png" % ("-captureblt" if cb else ""))
            img.save(f)
            print("%-30s %-8s %-24s %7.1f%% %8d" % ("BitBlt(screen)" + ("+CAPTUREBLT" if cb else ""),
                                                    ok, str(c), ratio, n))
            res.append(("bitblt%s" % cb, ratio, n))
        except Exception as e:
            print("%-30s 异常 %s" % ("BitBlt(screen)", str(e)[:50]))
    print()
    if res:
        # 判据：主色占比最低 + 去重色数最多 的那个最可能"抓到了真内容"
        res.sort(key=lambda t: (t[1], -t[2]))
        print("⇒ 最可能抓到内容的方法：%s（主色占比 %.1f%%、去重色数 %d）" % (res[0][0], res[0][1], res[0][2]))
        print("  判据：只抓到底色时主色占比高（如 90%%+）；抓到内容时占比降、色数升。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
