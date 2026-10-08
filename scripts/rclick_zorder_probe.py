# -*- coding: utf-8 -*-
"""探针：**投递右键的时候，微信到底有没有跑到上层（Z 序 / 前台）？**

：「你点右键的时候，为啥窗口到上层来了。是你把他整到上层的，还是他自己这样的」

→ 这是**事实问题，必须测，不能凭代码猜**。三个观测量：

  ① `GetForegroundWindow()`  —— 谁在前台（投递前 / 中 / 后）
  ② `GetWindow(h, GW_HWNDPREV)` 逐层向上 —— 主窗在 Z 序里的**位置**（前面压着几个窗）
  ③ 每帧窗口 rect 不变（排除被移动/改变尺寸）

做法：先记基线，投递右键，然后**密集采样 24 帧**（每帧 0.12s）把三个量全记下来。
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
u32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
u32.GetAncestor.restype = ctypes.c_void_p

GW_HWNDPREV = 3
GA_ROOT = 2

MAIN_CLASS = "Qt51514QWindowIcon"


def wechat_pids():
    pids = set()

    def cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h):
                return True
            if win32gui.GetClassName(h).startswith("Qt51514QWindow"):
                pids.add(win32process.GetWindowThreadProcessId(h)[1])
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass
        return True
    win32gui.EnumWindows(cb, None)
    return pids


def scan(pids):
    rows = []

    def cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h):
                return True
            if win32process.GetWindowThreadProcessId(h)[1] not in pids:
                return True
            rows.append({"hwnd": int(h), "cls": win32gui.GetClassName(h),
                         "title": win32gui.GetWindowText(h),
                         "rect": win32gui.GetWindowRect(h)})
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass
        return True
    win32gui.EnumWindows(cb, None)
    return rows


def find_main(pids):
    best, ba = 0, 0
    for r in scan(pids):
        if r["cls"] != MAIN_CLASS:
            continue
        L, T, R, B = r["rect"]
        if (R - L) * (B - T) > ba:
            best, ba = r["hwnd"], (R - L) * (B - T)
    return best


def z_depth(hwnd):
    """Z 序位置：该窗**前面压着几个可见顶层窗**（0 = 最上层）。

    用 `GetWindow(prev)` 沿 GW_HWNDPREV 往上数（prev 在 Z 序里更靠上）。
    """
    n = 0
    cur = int(hwnd)
    for _ in range(200):
        p = u32.GetWindow(ctypes.c_void_p(cur), GW_HWNDPREV)
        if not p:
            break
        n += 1
        cur = int(p)
    return n


def fg_info():
    """当前前台窗：返回 (hwnd, cls, title, 是否微信进程)。"""
    fg = u32.GetForegroundWindow()
    if not fg:
        return (0, "", "", False)
    h = int(fg)
    try:
        root = int(u32.GetAncestor(ctypes.c_void_p(h), GA_ROOT) or h)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        root = h
    cls = win32gui.GetClassName(h)
    try:
        title = win32gui.GetWindowText(h)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        title = ""
    pid = win32process.GetWindowThreadProcessId(h)[1]
    is_wx = pid in wechat_pids()
    return (h, cls, title, is_wx, root)


def main():
    print("=" * 76)
    print("右键时微信是否被带到上层？")
    print("=" * 76)
    print("\n[!] 接下来约 10 秒：请不要动鼠标、不要点任何地方。\n")

    from agent import input_backend as _ib
    from agent import wechat_windows as _WW
    from agent import chat_ocr as _co
    _WW.set_dpi_aware()  # DPI 口径对齐（铁律）

    pids = wechat_pids()
    main_hwnd = find_main(pids)
    print("微信 pid=%s 主窗=%d" % (sorted(pids), main_hwnd))

    # ── 基线 ──
    f0 = fg_info()
    z0 = z_depth(main_hwnd)
    print("\n【基线】")
    print("  前台窗 hwnd=%-10d cls=%-28s title=%-20s 是微信=%s"
          % (f0[0], f0[1][:26], str(f0[2])[:18], f0[3]))
    print("  主窗 Z 深度 = %d（前面压着 %d 个可见顶层窗）" % (z0, z0))

    img0 = _WW.grab_window(main_hwnd, physical=True)[0]
    rect0 = win32gui.GetWindowRect(main_hwnd)

    # ── 找气泡靶点 ──
    items = _co.recognize(img0, timeout=6.0) or []
    W, H = img0.size

    def _bad(t):
        s = str(t)
        return ("http" in s.lower() or "/" in s or s.strip().isdigit()
                or "：" in s or ":" in s)

    cands = [(t, x, y, w, h) for (t, x, y, w, h) in items
             if 0.16 * H < y < 0.62 * H and 0.36 * W < x < 0.95 * W
             and len(str(t).strip()) >= 4 and not _bad(t)]
    cands.sort(key=lambda it: it[1])
    if not cands:
        print("[x] 没有气泡靶点")
        return
    t0, x0, y0, w0_, h0 = cands[0]
    rel = (x0 + w0_ // 2, y0 + h0 // 2)
    L, T, R, B = win32gui.GetWindowRect(main_hwnd)
    pt = (L + rel[0], T + rel[1])
    print("\n靶点 = 「%s」 屏幕 %s" % (str(t0)[:22], pt))

    backend = _ib.select_backend()
    print("后端 = %s  activate=%s" % (type(backend).__name__,
                                   getattr(backend, "activate", None)))
    try:
        from agent import ui_adapt as _ua
        fg_ok = bool(_ua.fg_allowed()[0])
        print("fg_allowed（是否允许真鼠标/置前）= %s" % fg_ok)
        if not fg_ok and getattr(backend, "activate", False):
            backend.activate = False
            print("⇒ 按后台档**关掉伪激活**（项目实测：带伪激活反而不弹菜单）")
    except Exception as e:
        print("读 fg_allowed 失败：", e)

    # ── 投递右键，密集采样 ──
    ok, why = backend.click(main_hwnd, pt, right=True)
    print("\n投递右键 ok=%s" % ok)

    frames = []
    t_start = time.time()
    hit_fg_wx = 0
    for i in range(24):
        time.sleep(0.12)
        fg = fg_info()
        z = z_depth(main_hwnd)
        rect = win32gui.GetWindowRect(main_hwnd)
        rows = scan(pids)
        frames.append({"i": i, "t": round(time.time() - t_start, 3),
                       "fg": fg[0], "fg_cls": fg[1], "fg_title": str(fg[2]),
                       "fg_is_wx": fg[3], "z": z,
                       "rect": rect, "nwin": len(rows)})
        if fg[3]:
            hit_fg_wx += 1
        if i < 8 or i % 4 == 0:
            print("   f%02d t=%.2fs  前台=%-24s(微信=%-5s)  主窗Z=%d  顶层窗=%d"
                  % (i, frames[-1]["t"], (fg[1][:20] or "(无)"), fg[3], z, len(rows)))

    print("\n【汇总】")
    print("  · 24 帧里**微信在前台**的帧数 = %d / 24" % hit_fg_wx)
    zs = [f["z"] for f in frames]
    print("  · 主窗 Z 深度：基线=%d  投递后 min=%d max=%d" % (z0, min(zs), max(zs)))
    print("  · 主窗 rect 是否变化：基线=%s  投递后全部相同=%s"
          % (rect0, all(f["rect"] == rect0 for f in frames)))
    print("  · 期间顶层窗数变化：%s" % sorted(set(f["nwin"] for f in frames)))

    # 结论
    print("\n==> 判定：")
    if hit_fg_wx == 0:
        print("    **微信全程没有成为前台窗**（0/24 帧）⇒ 右键**没有把微信带到上层**（前台意义上）")
    elif hit_fg_wx < 24:
        print("    **微信有 %d/24 帧在前台** ⇒ 投递过程**短暂置前**（项目已知口径 1~3 秒）" % hit_fg_wx)
    else:
        print("    **微信全程在前台**（24/24）⇒ 投递后它一直占着前台")
    if max(zs) > z0:
        print("    ⚠️ 但主窗 **Z 深度变了**（%d → %d）⇒ Z 序上它确实被提前过" % (z0, max(zs)))
    else:
        print("    ✓ 主窗 Z 深度**没变**（一直 = %d）⇒ Z 序上没被提前" % z0)

    with open(os.path.join(OUT, "zorder.json"), "w", encoding="utf-8") as f:
        json.dump({"pids": sorted(pids), "main": main_hwnd, "baseline_z": z0,
                   "baseline_fg": {"hwnd": f0[0], "cls": f0[1], "title": str(f0[2]),
                                   "is_wechat": f0[3]},
                   "frames": frames, "hit_fg_wechat": hit_fg_wx}, f,
                  ensure_ascii=False, indent=2)
    print("\n落盘：", OUT)


if __name__ == "__main__":
    main()
