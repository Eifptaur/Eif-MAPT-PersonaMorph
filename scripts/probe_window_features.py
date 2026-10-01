# -*- coding: utf-8 -*-
"""probe_window_features.py —— 逐项对比「主窗 vs 独立弹窗」的窗口特征（找出抓不到的成因）

动机（用户指出）：主窗 `PrintWindow` 抓到 37.37% 内容、独立窗只有 1.98%，
    且三种前置动作全部无效 ⇒ **"不可能抓不到"，独立窗一定有某个特征不同**。
本脚本把两个窗口的这些属性全部打印出来对照：
  · 样式 GWL_STYLE / 扩展样式 GWL_EXSTYLE（含 WS_EX_LAYERED / WS_EX_NOREDIRECTIONBITMAP）
  · 所有权 GW_OWNER（Owned window 常影响重定向）
  · IsIconic / IsZoomed / IsWindowVisible / IsWindowEnabled
  · DwmGetWindowAttribute(DWMWA_CLOAKED) —— **被 DWM cloaked 的窗口往往抓不到**
  · DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)（真实可见边界）
  · GetWindowRect vs GetClientRect（客户区偏移）
  · GetLayeredWindowAttributes（若分层）
  · 父/子关系 GetAncestor(GA_ROOT) / GA_PARENT
用法：./runtime/python/python.exe -u scripts/probe_window_features.py
"""
import ctypes as ct
import logging
import os
import sys

logging.disable(logging.INFO)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent import wechat_windows as WW  # noqa: E402

_U32 = ct.windll.user32
_DWM = ct.windll.dwmapi

GWL_STYLE = -16
GWL_EXSTYLE = -20
GW_OWNER = 4
DWMWA_CLOAKED = 14
DWMWA_EXTENDED_FRAME_BOUNDS = 9

# 关键样式位（有名字的）
STYLE_BITS = {
    0x80000000: "WS_POPUP",
    0x40000000: "WS_CHILD",
    0x10000000: "WS_VISIBLE",
    0x00C00000: "WS_CAPTION",
    0x00040000: "WS_THICKFRAME",
    0x00010000: "WS_MAXIMIZEBOX",
    0x00020000: "WS_MINIMIZEBOX",
    0x00080000: "WS_SYSMENU",
}
EXSTYLE_BITS = {
    0x00000001: "WS_EX_DLGMODALFRAME",
    0x00000008: "WS_EX_TOPMOST",
    0x00000080: "WS_EX_TOOLWINDOW",
    0x00080000: "WS_EX_LAYERED",
    0x00000100: "WS_EX_WINDOWEDGE",
    0x00000200: "WS_EX_CLIENTEDGE",
    0x08000000: "WS_EX_NOACTIVATE",
    0x00000020: "WS_EX_TRANSPARENT",
    # ⭐ 重点怀疑项：
    0x00200000: "WS_EX_NOREDIRECTIONBITMAP",
}


class _R(ct.Structure):
    _fields_ = [("l", ct.c_long), ("t", ct.c_long), ("r", ct.c_long), ("b", ct.c_long)]


def bits(v, table):
    out = []
    for k, name in table.items():
        if v & k:
            out.append(name)
    return ",".join(out) or "-"


def feat(hw):
    d = {}
    d["hwnd"] = hw
    d["style"] = _U32.GetWindowLongW(hw, GWL_STYLE) & 0xFFFFFFFF
    d["exstyle"] = _U32.GetWindowLongW(hw, GWL_EXSTYLE) & 0xFFFFFFFF
    d["owner"] = _U32.GetWindow(hw, GW_OWNER)
    d["root"] = _U32.GetAncestor(hw, 2)          # GA_ROOT
    d["parent"] = _U32.GetAncestor(hw, 1)        # GA_PARENT
    d["iconic"] = bool(_U32.IsIconic(hw))
    d["zoomed"] = bool(_U32.IsZoomed(hw))
    d["visible"] = bool(_U32.IsWindowVisible(hw))
    d["enabled"] = bool(_U32.IsWindowEnabled(hw))
    d["cloaked"] = None
    try:
        val = ct.c_int(0)
        _DWM.DwmGetWindowAttribute(hw, DWMWA_CLOAKED, ct.byref(val), ct.sizeof(val))
        d["cloaked"] = val.value
    except Exception:
        pass
    r = _R()
    _U32.GetWindowRect(hw, ct.byref(r))
    d["wrect"] = (r.l, r.t, r.r, r.b, r.r - r.l, r.b - r.t)
    cr = _R()
    _U32.GetClientRect(hw, ct.byref(cr))
    d["crect"] = (cr.r - cr.l, cr.b - cr.t)
    try:
        _p = ct.c_uint(0)
        _fl = ct.c_uint(0)
        _col = ct.c_uint(0)
        _ok = _U32.GetLayeredWindowAttributes(hw, ct.byref(_col), ct.byref(_p), ct.byref(_fl))
        d["layered_attrs"] = (bool(_ok), _p.value, hex(_fl.value))
    except Exception:
        d["layered_attrs"] = None
    try:
        fr = _R()
        _DWM.DwmGetWindowAttribute(hw, DWMWA_EXTENDED_FRAME_BOUNDS, ct.byref(fr), ct.sizeof(fr))
        d["dwm_frame"] = (fr.l, fr.t, fr.r, fr.b, fr.r - fr.l, fr.b - fr.t)
    except Exception:
        d["dwm_frame"] = None
    return d


def main():
    wins = [w for w in WW.list_wechat_windows() if w["title"].strip()]
    if not wins:
        print("没有可见微信窗口")
        return 1
    main_w = max(wins, key=lambda w: w["w"] * w["h"])
    feats = [(w["title"], feat(w["hwnd"])) for w in wins]
    print("=" * 96)
    print("窗口特征对照（主窗 = 面积最大的那个）")
    print("=" * 96)
    keys = ["hwnd", "wrect", "crect", "dwm_frame", "cloaked", "iconic", "zoomed",
            "visible", "enabled", "owner", "root", "parent", "layered_attrs"]
    for title, d in feats:
        mark = "  ← 主窗" if d["hwnd"] == main_w["hwnd"] else "  ← 独立窗"
        print("\n[%s]%s" % (title, mark))
        for k in keys:
            print("    %-14s %s" % (k, d.get(k)))
        print("    %-14s 0x%08X  %s" % ("STYLE", d["style"], bits(d["style"], STYLE_BITS)))
        print("    %-14s 0x%08X  %s" % ("EXSTYLE", d["exstyle"], bits(d["exstyle"], EXSTYLE_BITS)))
    print()
    print("=" * 96)
    print("★ 重点看这几项（抓不到的最可能成因）：")
    print("  · EXSTYLE 里的 **WS_EX_NOREDIRECTIONBITMAP** ⇒ `PrintWindow` 拿不到内容（最典型）")
    print("  · EXSTYLE 里的 **WS_EX_LAYERED** ⇒ 需要 `PrintWindow(flag=2)` 或先取分层位图")
    print("  · **cloaked** != 0 ⇒ 被 DWM 遮蔽（抓到的会是空/旧内容）")
    print("  · **dwm_frame 尺寸 != wrect 尺寸** ⇒ 存在 DWM 补偿，坐标要按 dwm_frame 换算")
    return 0


if __name__ == "__main__":
    sys.exit(main())
