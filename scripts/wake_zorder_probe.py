# -*- coding: utf-8 -*-
"""探针：**伪激活（`_wake`）本身，会不会把微信带到上层？**

：「你点右键的时候，为啥窗口到上层来了。**是你把他整到上层的，还是他自己这样的**」

上一发（`rclick_zorder_probe.py`）测出来：
  · 投递右键时**前台一直是 Chrome**（0/24 帧微信在前台）⇒ 不是"抢前台"
  · 但主窗 Z 深度 72 → 75~76（+3~4 位）⇒ Z 序上**确实被提了**

本发把三件事分离开，逐一测 Z 深度：
  A 基线（什么都不做）
  B 只投伪激活 `WM_ACTIVATE(1) + WM_NCACTIVATE(1)`          ← 我们自己的代码干的
  C 投完 B 再投 `WM_MOUSEACTIVATE`（右键链里我们**没投**过它）
  D 只投一条 `WM_MOUSEMOVE`
  E 完整右键（MOUSEMOVE + RBUTTONDOWN + RBUTTONUP）
  F 完整左键
每步之间记 Z 深度 + 前台窗，**每步跑两遍并取稳定值**（单次读数会被其它进程扰动）。
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


def find_main(pids):
    best, ba = 0, 0
    rows = []

    def cb(h, _):
        try:
            if win32gui.IsWindowVisible(h) and win32process.GetWindowThreadProcessId(h)[1] in pids:
                rows.append((int(h), win32gui.GetClassName(h), win32gui.GetWindowRect(h)))
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass
        return True
    win32gui.EnumWindows(cb, None)
    for h, cls, r in rows:
        if cls != MAIN_CLASS:
            continue
        a = (r[2] - r[0]) * (r[3] - r[1])
        if a > ba:
            best, ba = h, a
    return best


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
    cls = win32gui.GetClassName(h)
    pid = win32process.GetWindowThreadProcessId(h)[1]
    return "%s%s" % (cls[:22], "[微信]" if pid in pids else "")


def snap(pids, main):
    """一次观测：Z 深度（连读 3 次取最小，抗瞬时扰动）+ 前台。"""
    zs = []
    for _ in range(3):
        zs.append(z_depth(main))
        time.sleep(0.05)
    return {"z_min": min(zs), "z_raw": zs, "fg": fg_brief(pids)}


def main():
    print("=" * 78)
    print("伪激活 / 各类投递 —— 谁在把微信往上提？")
    print("=" * 78)
    print("\n[!] 约 15 秒：请不要动鼠标、不要切窗口。\n")

    from agent import input_backend as ib
    from agent import wechat_windows as _WW
    _WW.set_dpi_aware()

    pids = wechat_pids()
    main_h = find_main(pids)
    main_h = int(main_h)
    print("微信 pid=%s  主窗=%d  rect=%s" % (sorted(pids), main_h, win32gui.GetWindowRect(main_h)))

    # 靶点：主窗客户区中心偏上一点，落在会话区里（够触发右键菜单的分支）
    L, T, R, B = win32gui.GetWindowRect(main_h)
    pt = ((L + R) // 2 + 160, (T + B) // 2 - 40)
    print("测试落点（屏幕）= %s" % (pt,))

    b = ib.MessageBackend()
    print("MessageBackend: press_ms=%d activate=%s" % (b.press_ms, b.activate))

    try:
        from agent import ui_adapt as _ua
        print("fg_allowed=%s" % (_ua.fg_allowed()[0],))
    except Exception as e:
        print("fg_allowed 读取失败：", e)

    res = {}

    def step(name, fn, repeat=2):
        vals = []
        for i in range(repeat):
            s0 = snap(pids, main_h)
            fn()
            time.sleep(0.45)
            s1 = snap(pids, main_h)
            vals.append({"before": s0, "after": s1})
            time.sleep(0.35)
        za = [v["after"]["z_min"] for v in vals]
        z0 = [v["before"]["z_min"] for v in vals]
        print("  %-34s Z: %s -> %s   前台: %s" %
              (name, z0, za, [v["after"]["fg"] for v in vals]))
        res[name] = vals
        return vals

    cx, cy = ib.to_client(main_h, pt)
    lp = ib.pack_lparam(cx, cy)

    print("\n【逐步测】（Z 深度越小 = 越靠上层）")

    # A 基线：不动
    step("A 什么都不做（对照）", lambda: None)

    # B 只投伪激活两件套（我们自己代码里的 _wake）
    def _wake_only():
        ib._post(main_h, ib.WM_ACTIVATE, 1, 0)
        ib._post(main_h, ib.WM_NCACTIVATE, 1, 0)
    step("B 只投 WM_ACTIVATE+NCACTIVATE", _wake_only)

    # C 再补一条 WM_MOUSEACTIVATE（我们没投过）
    def _wake_plus_ma():
        ib._post(main_h, ib.WM_ACTIVATE, 1, 0)
        ib._post(main_h, ib.WM_NCACTIVATE, 1, 0)
        time.sleep(0.08)
        ib._post(main_h, ib.WM_MOUSEACTIVATE, 0, 0)
    step("C 再补 WM_MOUSEACTIVATE", _wake_plus_ma)

    # D 只 MOVEMOVE
    step("D 只投 WM_MOUSEMOVE", lambda: ib._post(main_h, ib.WM_MOUSEMOVE, 0, lp))

    # E 完整右键（关伪激活，与上一发探针同路径）
    def _rclick():
        ib._post(main_h, ib.WM_MOUSEMOVE, 0, lp)
        time.sleep(0.05)
        ib._post(main_h, ib.WM_RBUTTONDOWN, 2, lp)
        time.sleep(0.06)
        ib._post(main_h, ib.WM_RBUTTONUP, 0, lp)
    step("E 完整右键（无伪激活）", _rclick)

    # E2 完整右键 + 伪激活
    def _rclick_wake():
        ib._post(main_h, ib.WM_ACTIVATE, 1, 0)
        ib._post(main_h, ib.WM_NCACTIVATE, 1, 0)
        time.sleep(0.08)
        ib._post(main_h, ib.WM_MOUSEMOVE, 0, lp)
        time.sleep(0.05)
        ib._post(main_h, ib.WM_RBUTTONDOWN, 2, lp)
        time.sleep(0.06)
        ib._post(main_h, ib.WM_RBUTTONUP, 0, lp)
    step("E2 完整右键 + 伪激活", _rclick_wake)

    # F 完整左键（收尾：把菜单关掉）
    def _lclick():
        ib._post(main_h, ib.WM_MOUSEMOVE, 0, lp)
        time.sleep(0.05)
        ib._post(main_h, ib.WM_LBUTTONDOWN, 1, lp)
        time.sleep(0.06)
        ib._post(main_h, ib.WM_LBUTTONUP, 0, lp)
    step("F 完整左键（无伪激活）", _lclick)

    print("\n【汇总：每种操作后 Z 深度的变化量（正值=被提前）】")
    base_after = [v["after"]["z_min"] for v in res["A 什么都不做（对照）"]]
    b_med = sorted(base_after)[len(base_after) // 2]
    table = []
    for name, vals in res.items():
        za = [v["after"]["z_min"] for v in vals]
        zb = [v["before"]["z_min"] for v in vals]
        delta = sorted(za)[len(za) // 2] - sorted(zb)[len(zb) // 2]
        table.append((name, sorted(zb)[len(zb) // 2], sorted(za)[len(za) // 2], delta))
        mark = "  ← 被提前" if delta >= 2 else ("" if delta == 0 else "  ← 反而更靠后")
        print("  %-34s  %3d -> %3d   Δ=%+d%s" % (name, table[-1][1], table[-1][2], delta, mark))
    print("\n  （对照基准 A 的 Z 中位 = %d）" % b_med)

    print("\n==> 判定：")
    wake_d = [t for t in table if t[0].startswith("B ")][0][3]
    rclick_d = [t for t in table if t[0].startswith("E ")][0][3]
    rclickw_d = [t for t in table if t[0].startswith("E2")][0][3]
    mv_d = [t for t in table if t[0].startswith("D ")][0][3]
    if wake_d >= 2:
        print("    **单投伪激活就足以把微信往上提 %+d 位** ⇒ 来源是我们自己的 `_wake()`。" % wake_d)
    else:
        print("    · 单投伪激活 Δ=%+d ⇒ 伪激活**不是**主因。" % wake_d)
    if mv_d >= 2:
        print("    **单投一条鼠标消息也能提 %+d 位** ⇒ 投递鼠标本身就会让窗口管理器重排 Z 序。" % mv_d)
    if rclick_d >= 2:
        print("    · 不带伪激活的完整右键 Δ=%+d ⇒ **右键链路本身就自带这个位移**。" % rclick_d)
    if rclickw_d > rclick_d:
        print("    · 带伪激活的右键 Δ=%+d（比不带多 %+d）⇒ 伪激活是**叠加**上去的。" % (rclickw_d, rclickw_d - rclick_d))

    with open(os.path.join(OUT, "wake-zorder.json"), "w", encoding="utf-8") as f:
        json.dump({"pids": sorted(pids), "main": main_h, "pt": pt,
                   "activate_default": b.activate, "result": res}, f,
                  ensure_ascii=False, indent=2)
    print("\n落盘：", OUT)


if __name__ == "__main__":
    main()
