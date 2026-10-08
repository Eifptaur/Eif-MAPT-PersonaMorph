# -*- coding: utf-8 -*-
"""探针：**投递右键弹出的浮层（菜单）——它是"置顶窗"吗？点别的地方它会消失吗？**

：
  「你投递的那个浮层，为啥一直在最上，而且点其他窗口，他好像不隐去，只有回去点他了，
    才消失」

这句话拆成两个**可测事实**：
  ① **它是不是 TOPMOST（置顶）窗？**
     → 读它自己的扩展样式 `GWL_EXSTYLE`，看有没有 `WS_EX_TOPMOST = 0x8`；
       再看它有没有 `WS_EX_NOACTIVATE`（0x08000000）/ `WS_EX_TOOLWINDOW`（0x80）——
       这两个是"浮层永不激活、永不进 alt-tab"的典型组合。
  ② **点别的窗口，它会不会消失？**
     → 采样点 A=菜单窗自身矩形，B=菜单外**同进程**的微信主窗区域，C=**别的进程**的窗口区域。
       每次点完立刻查菜单窗是否还在（`IsWindow` + 可见 + 类名）。
       **关键：C 类点必须真鼠标**（要动光标）⇒ 本探针默认**不跑 C**，
       只跑 B（同进程投递），并打印"要测 C 需要你同意动光标"。

  ⚠️ 不移动真实光标、不 SetForegroundWindow（除非将来显式加 C 档）。
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

u32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
u32.GetWindowLongW.restype = ctypes.c_long
try:
    u32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
except Exception:
    pass

GWL_EXSTYLE = -20
GW_HWNDPREV = 3
MAIN_CLASS = "Qt51514QWindowIcon"
MENU_CLASS = "Qt51514QWindowToolSaveBits"

# 扩展样式位（逐位释义，用于打印）
EX_BITS = [
    (0x00000001, "WS_EX_DLGMODALFRAME"),
    (0x00000008, "WS_EX_TOPMOST"),        # ★ 置顶
    (0x00000010, "WS_EX_ACCEPTFILES"),
    (0x00000020, "WS_EX_TRANSPARENT"),    # ★ 鼠标穿透
    (0x00000080, "WS_EX_TOOLWINDOW"),     # 不在 alt-tab
    (0x00000100, "WS_EX_WINDOWEDGE"),
    (0x00000200, "WS_EX_CLIENTEDGE"),
    (0x00010000, "WS_EX_CONTROLPARENT"),
    (0x00040000, "WS_EX_APPWINDOW"),
    (0x00080000, "WS_EX_LAYERED"),
    (0x00100000, "WS_EX_NOINHERITLAYOUT"),
    (0x08000000, "WS_EX_NOACTIVATE"),     # ★ 被点也不激活
    (0x10000000, "WS_EX_LAYOUTRTL"),
]


def ex_style(h):
    try:
        v = u32.GetWindowLongPtrW(ctypes.c_void_p(int(h)), GWL_EXSTYLE)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        v = u32.GetWindowLongW(ctypes.c_void_p(int(h)), GWL_EXSTYLE)
    return int(v) & 0xFFFFFFFF


def ex_repr(v):
    return " | ".join(n for b, n in EX_BITS if v & b) or "(无已知位)"


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
                             win32gui.GetWindowRect(h),
                             win32process.GetWindowThreadProcessId(h)[1]))
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass
        return True
    win32gui.EnumWindows(cb, None)
    return rows


def menus(pids):
    return [w for w in top_windows(pids) if w[1] == MENU_CLASS]


def find_main(pids):
    best, ba = 0, 0
    for h, cls, t, r, pid in top_windows(pids):
        if cls != MAIN_CLASS:
            continue
        a = (r[2] - r[0]) * (r[3] - r[1])
        if a > ba:
            best, ba = h, a
    return int(best)


def alive(h):
    """菜单窗还在吗（存在 + 可见 + 类名仍对）。"""
    h = int(h)
    if not h or not win32gui.IsWindow(h):
        return False
    if not win32gui.IsWindowVisible(h):
        return False
    try:
        return win32gui.GetClassName(h) == MENU_CLASS
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return False


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
    print("右键浮层：它是置顶窗吗？点别处会消失吗？")
    print("=" * 78)
    print("\n[!] 约 20 秒：请不要动鼠标、不要切窗口。全程只投递，不动真光标。\n")

    from agent import input_backend as ib
    from agent import wechat_windows as _WW
    from agent import chat_ocr as _co
    _WW.set_dpi_aware()

    pids = wechat_pids()
    main_h = find_main(pids)
    print("微信 pid=%s 主窗=%d" % (sorted(pids), main_h))

    # ── 顺手把主窗自己的扩展样式也读出来当对照 ──
    m_ex = ex_style(main_h)
    print("主窗 exStyle = 0x%08X  %s" % (m_ex, ex_repr(m_ex)))

    L, T, R, B = win32gui.GetWindowRect(main_h)

    # ── 找气泡靶点 ──
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
    bubble = (L + x0 + w0 // 2, T + y0 + h0 // 2)
    print("气泡靶点=「%s」 屏幕=%s" % (str(t0)[:20], bubble))

    b = ib.MessageBackend()
    b.activate = False

    def post_click(screen_pt, right=False):
        cx, cy = ib.to_client(main_h, screen_pt)
        lp = ib.pack_lparam(cx, cy)
        ib._post(main_h, ib.WM_MOUSEMOVE, 0, lp)
        time.sleep(0.05)
        if right:
            ib._post(main_h, ib.WM_RBUTTONDOWN, 2, lp)
            time.sleep(0.06)
            ib._post(main_h, ib.WM_RBUTTONUP, 0, lp)
        else:
            ib._post(main_h, ib.WM_LBUTTONDOWN, 1, lp)
            time.sleep(0.06)
            ib._post(main_h, ib.WM_LBUTTONUP, 0, lp)

    # ── 步骤 1：弹出菜单 ──
    print("\n【步骤 1】投递右键，弹菜单")
    post_click(bubble, right=True)
    time.sleep(0.8)
    ms = menus(pids)
    if not ms:
        print("  ⚠️ 没弹出菜单 ⇒ 场景不成立（可能上一次菜单还开着，先点空白关掉再重试）")
        # 尝试先关掉遗留菜单
        post_click(((L + R) // 2, (T + B) // 2 - 30))
        time.sleep(0.6)
        post_click(bubble, right=True)
        time.sleep(0.8)
        ms = menus(pids)
    if not ms:
        print("  [x] 仍无菜单，退出")
        return

    mh, mcls, mtitle, mrect, mpid = ms[0]
    mex = ex_style(mh)
    print("  ✓ 菜单窗 hwnd=%d title=%r rect=%s" % (mh, mtitle, mrect))
    print("    ★ exStyle = 0x%08X" % mex)
    print("      %s" % ex_repr(mex))
    print("      WS_EX_TOPMOST = %s" % bool(mex & 0x8))
    print("      WS_EX_NOACTIVATE = %s" % bool(mex & 0x08000000))
    print("      WS_EX_TOOLWINDOW = %s" % bool(mex & 0x80))
    print("      WS_EX_LAYERED = %s" % bool(mex & 0x00080000))
    print("      WS_EX_TRANSPARENT = %s" % bool(mex & 0x20))

    def z(h):
        n, cur = 0, int(h)
        for _ in range(400):
            p = u32.GetWindow(ctypes.c_void_p(cur), GW_HWNDPREV)
            if not p:
                break
            n += 1
            cur = int(p)
        return n
    print("    菜单窗 Z 深度 = %d（前面压着 %d 个顶层窗；0=最上）" % (z(mh), z(mh)))
    print("    主窗   Z 深度 = %d" % z(main_h))

    res = {"menu": {"hwnd": mh, "title": str(mtitle), "rect": list(mrect),
                    "ex": mex, "ex_repr": ex_repr(mex), "z": z(mh)},
           "main_ex": m_ex, "main_z": z(main_h)}

    # ── 步骤 2：点菜单外、同进程主窗别处 —— 菜单会不会消？ ──
    print("\n【步骤 2】点**菜单矩形之外**、但仍在微信主窗上的地方（同进程投递）")
    mx0, my0, mx1, my1 = mrect
    # 取主窗左上角靠里的位置（一定在菜单矩形外）
    far = (L + 60, T + 140)
    print("  落点 = %s （菜单矩形 = %s）" % (far, mrect))
    post_click(far)
    for i in range(6):
        time.sleep(0.15)
        print("   +%.2fs 菜单还在？ %s   前台=%s" % ((i + 1) * 0.15, alive(mh), fg_brief(pids)))
    gone_after_inproc = not alive(mh)
    res["click_inside_wechat_outside_menu"] = {"pt": list(far), "menu_gone": gone_after_inproc}
    print("  → 菜单消失？ %s" % gone_after_inproc)

    if not alive(mh):
        print("  （菜单已消，重新弹一次继续测下一档）")
        post_click(bubble, right=True)
        time.sleep(0.8)
        ms = menus(pids)
        if not ms:
            print("  重新弹失败，收尾")
            return
        mh = ms[0][0]
        print("  新菜单 hwnd=%d rect=%s" % (mh, ms[0][3]))

    # ── 步骤 3：点**别的进程**的窗（需要动真光标）⇒ 只说明、不执行 ──
    print("\n【步骤 3】点**别的程序的窗口**（Chrome 等）")
    print("  ⚠️ 这一档必须**真的动光标**（要激活别的进程，投递做不到）")
    print("     ⇒ 本探针不跑。如果你要我实测，说一声「动光标测」我就加这一档。")
    res["click_other_process"] = {"skipped": True,
                                  "reason": "需要真实鼠标（会动你的光标），等用户授权"}

    # ── 收尾：点菜单外把菜单收掉 ──
    post_click(far)
    time.sleep(0.5)
    print("\n【收尾】菜单还在？ %s   前台=%s" % (alive(mh), fg_brief(pids)))

    with open(os.path.join(OUT, "menu-style.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("落盘：", OUT)


if __name__ == "__main__":
    main()
