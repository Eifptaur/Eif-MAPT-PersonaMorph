# -*- coding: utf-8 -*-
"""探针：**带上"投递之后真的出现菜单窗"这一条件**，再看 Z 序 / 前台。

上一发（`wake_zorder_probe.py`）里的"完整右键"打在**会话区空白**（759,625），
很可能根本没弹出菜单（没有命中气泡）⇒ 那次只测到"投递鼠标本身 +1 位"的噪声。

本发做两件事：
  · 让场景成立：**先把菜单真的弹出来**（verify：顶层窗变多 ⇒ 菜单在），
    再在"菜单开着"的状态下测 Z 序 / 前台；
  · 顺带量 **从气泡到菜单出现需要多久**（给"短暂 1~3 秒"这个口径一个实测数）。

⚠️ 要打招呼**。本探针只投递（PostMessage），
   不移动真实光标、不 SetForegroundWindow。
"""
import sys
import os
import time
import json
import ctypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

OUT = os.path.join(ROOT, "报告", "右键-Z序探针-20261001")
os.makedirs(OUT, exist_ok=True)

import win32gui
import win32process
import logging

log = logging.getLogger("persona-morph")

u32 = ctypes.WinDLL("user32", use_last_error=True)
u32.GetForegroundWindow.restype = ctypes.c_void_p
u32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
u32.GetWindow.restype = ctypes.c_void_p
GW_HWNDPREV = 3
MAIN_CLASS = "Qt51514QWindowIcon"
MENU_CLASS = "Qt51514QWindowToolSaveBits"


def wechat_pids():
    pids = set()

    def cb(h, _):
        try:
            if win32gui.IsWindowVisible(h) and win32gui.GetClassName(h).startswith("Qt51514QWindow"):
                pids.add(win32process.GetWindowThreadProcessId(h)[1])
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass
        return True
    win32gui.EnumWindows(cb, None)
    return pids


def top_windows(pids):
    rows = []

    def cb(h, _):
        try:
            if win32gui.IsWindowVisible(h) and win32process.GetWindowThreadProcessId(h)[1] in pids:
                rows.append((int(h), win32gui.GetClassName(h), win32gui.GetWindowText(h),
                             win32gui.GetWindowRect(h)))
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass
        return True
    win32gui.EnumWindows(cb, None)
    return rows


def z_depth(hwnd):
    n, cur = 0, int(hwnd)
    for _ in range(300):
        p = u32.GetWindow(ctypes.c_void_p(cur), GW_HWNDPREV)
        if not p:
            break
        n += 1
        cur = int(p)
    return n


def fg_brief(pids):
    fg = u32.GetForegroundWindow()
    if not fg:
        return "(无)"
    h = int(fg)
    try:
        cls = win32gui.GetClassName(h)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        cls = "?"
    pid = win32process.GetWindowThreadProcessId(h)[1]
    return "%s%s" % (cls[:24], "[微信]" if pid in pids else "")


def main():
    print("=" * 78)
    print("带菜单场景的 Z 序 / 前台实测")
    print("=" * 78)
    print("\n[!] 约 15 秒：请不要动鼠标、不要切窗口。\n")

    from agent import input_backend as ib
    from agent import wechat_windows as _WW
    from agent import chat_ocr as _co
    _WW.set_dpi_aware()

    pids = wechat_pids()
    rows = top_windows(pids)
    main_h = 0
    ba = 0
    for h, cls, t, r in rows:
        if cls == MAIN_CLASS:
            a = (r[2] - r[0]) * (r[3] - r[1])
            if a > ba:
                main_h, ba = h, a
    main_h = int(main_h)
    print("微信 pid=%s 主窗=%d" % (sorted(pids), main_h))

    L, T, R, B = win32gui.GetWindowRect(main_h)

    # ── 找气泡靶点（同上一发的过滤链）──
    img0 = _WW.grab_window(main_h, physical=True)[0]
    W, H = img0.size
    items = _co.recognize(img0, timeout=6.0) or []

    def _bad(s):
        s = str(s)
        return ("http" in s.lower() or "/" in s or s.strip().isdigit() or ":" in s)
    cands = [(t, x, y, w, h) for (t, x, y, w, h) in items
             if 0.16 * H < y < 0.62 * H and 0.36 * W < x < 0.95 * W
             and len(str(t).strip()) >= 4 and not _bad(t)]
    cands.sort(key=lambda it: it[1])
    if not cands:
        print("[x] 没找到气泡靶点")
        return
    t0, x0, y0, w0, h0 = cands[0]
    pt = (L + x0 + w0 // 2, T + y0 + h0 // 2)
    print("靶点=「%s」 屏幕=%s" % (str(t0)[:20], pt))

    b = ib.MessageBackend()
    b.activate = False  # 后台档
    cx, cy = ib.to_client(main_h, pt)
    lp = ib.pack_lparam(cx, cy)

    def snap():
        return {"z": z_depth(main_h), "fg": fg_brief(pids),
                "nwin": len(top_windows(pids)),
                "menus": [(w[0], w[1], w[3]) for w in top_windows(pids) if w[1] == MENU_CLASS]}

    print("\n【基线】", snap())

    # ── 投递右键，密集采样：何时出现菜单窗 / 各帧 Z 与前台 ──
    t0s = time.time()
    ib._post(main_h, ib.WM_MOUSEMOVE, 0, lp)
    time.sleep(0.05)
    ib._post(main_h, ib.WM_RBUTTONDOWN, 2, lp)
    time.sleep(0.06)
    ib._post(main_h, ib.WM_RBUTTONUP, 0, lp)
    print("投递完成，开始采样 …\n")

    frames = []
    menu_at = None
    for i in range(30):
        time.sleep(0.10)
        s = snap()
        s["i"] = i
        s["t"] = round(time.time() - t0s, 3)
        frames.append(s)
        if s["menus"] and menu_at is None:
            menu_at = s["t"]
        if i < 10 or i % 5 == 0:
            print("  f%02d t=%.2fs Z=%d 前台=%-26s 微信顶层窗=%d 菜单窗=%d"
                  % (i, s["t"], s["z"], s["fg"], s["nwin"], len(s["menus"])))

    zs = [f["z"] for f in frames]
    hit_fg = sum(1 for f in frames if "微信]" in f["fg"])
    z_open = [f["z"] for f in frames if f["menus"]]
    print("\n【汇总】")
    print("  · 菜单窗首次出现 t=%s" % menu_at)
    print("  · Z 深度：基线=%d  全程 min=%d max=%d" % (z_depth(main_h), min(zs), max(zs)))
    print("  · 菜单开着时的 Z 深度 = %s" % (z_open or "(菜单没出现)"))
    print("  · 微信在前台的帧数 = %d / %d" % (hit_fg, len(frames)))
    print("  · 前台分布 = %s" % sorted(set(f["fg"] for f in frames)))

    print("\n==> 判定：")
    if menu_at is None:
        print("    ⚠️ 这一发**没弹出菜单** ⇒ 场景不成立，Z 序结论不可用（落点没命中气泡）")
    else:
        print("    ✓ 菜单确实弹出来了（t=%.2fs）⇒ 场景成立" % menu_at)
        if hit_fg == 0:
            print("      **微信全程没成为前台**（0/%d）⇒ 弹菜单**不会把微信抢到前台**" % len(frames))
        else:
            print("      **微信有 %d/%d 帧在前台** ⇒ 弹菜单会短暂置前" % (hit_fg, len(frames)))
        if z_open and max(z_open) < 72:
            print("      菜单开着时 Z=%s ⇒ **比基线更靠上**（被提前了）" % z_open)
        elif z_open:
            print("      菜单开着时 Z=%s ⇒ 与基线同档，**Z 序没有被提前**" % z_open)

    # 收尾：关掉菜单
    ib._post(main_h, ib.WM_LBUTTONDOWN, 1, lp)
    time.sleep(0.06)
    ib._post(main_h, ib.WM_LBUTTONUP, 0, lp)
    time.sleep(0.4)
    print("\n【收尾】", snap())

    with open(os.path.join(OUT, "menu-zorder.json"), "w", encoding="utf-8") as f:
        json.dump({"main": main_h, "pt": pt, "target": str(t0),
                   "menu_first_at": menu_at, "frames": frames}, f,
                  ensure_ascii=False, indent=2)
    print("落盘：", OUT)


if __name__ == "__main__":
    main()
