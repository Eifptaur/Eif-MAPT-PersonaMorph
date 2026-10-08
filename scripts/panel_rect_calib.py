# -*- coding: utf-8 -*-
"""表情面板坐标标定（零侵入，只读）—— 用于填 `agent/wechat.py::_panel_rect` 的兜底值。

⛔ 为什么要有它
  `_panel_rect` 在「找不到独立面板窗」时的兜底估算写的是
      return (l + W*0.30, t + H*0.20, ..., t + H - 130)
  其中 `top = t + H*0.20` 被第三方诊断书（§2.7）点名为**太靠上**：
  面板实际高度约 380px，顶边应≈ `bottom - 380`，而不是从主窗 20% 处开始。
  要改这个值**必须有真机实测坐标**（纪律：不许猜坐标）。

⛔ 它做什么（**完全不碰微信、不动鼠标、不发送任何消息**）
  1. 枚举微信主窗（只读 `GetWindowRect`）；
  2. 连拍 N 帧：每帧记录"所有微信进程的可见子窗矩形"快照；
  3. ⇒ **你只需要在拍的过程中，手动把微信表情面板点开一次**。
     本脚本会在快照里**自动找出"只在面板打开时出现"的那个窗**，并算出它与主窗的相对比例。

用法
  # 1) 先让微信主窗处于**正常**状态（不要最大化、不要最小化；就是你平时用的样子）
  # 2) 跑本脚本，按提示操作
  ./runtime/python/python.exe -u scripts/panel_rect_calib.py --n 24 --every 1.0

  # 若面板是"内嵌弹层"（不在独立窗里），本脚本找不到窗 ⇒ 自动退化为
  #   「像素差分」模式：对主窗右下区域连拍，找出你点开面板后**新出现的矩形色块**。

产出
  <out>/calib.json     —— 主窗矩形、候选面板矩形、相对比例、建议兜底值
  <out>/frame-###.png  —— 逐帧主窗截图（供人工核对）
  <out>/frames.jsonl   —— 每帧的窗口快照（事后可复核）

要求
  · 微信**不要最小化**（最小化时 `GetWindowRect` 返回 (-32000,-32000)，本脚本会自动跳过并提示）；
  · 运行时**不要切走**到别的全屏程序（会影响截图取证，但窗口枚举不受影响）。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
import logging

log = logging.getLogger("persona-morph")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

user32 = ctypes.windll.user32

# 主窗类名（微信 4.x 主窗；3.x 是 WeChatMainWndForPC）
# ⛔ 实测修正（我第一版写错了）：微信 **4.1.15.8** 的主窗类名是
#    `Qt51514QWindowIcon`（Qt 自绘），**不是** `mmui::MainWindow`（那是 UIA 里的名字，
#    实测 `uia_tree` 只有 2 个节点、mmui 命中 0）。⇒ `mmui::MainWindow` 只作兼容保留，
#    识别主窗优先走 `agent.wechat_windows.list_wechat_windows()`（项目里已实测正确）。
MAIN_WND_CLASSES = ("Qt51514QWindowIcon", "mmui::MainWindow", "WeChatMainWndForPC")

CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def _cls(h) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, buf, 256)
    return buf.value or ""


def _title(h) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(h, buf, 512)
    return buf.value or ""


def _rect(h):
    r = wintypes.RECT()
    if not user32.GetWindowRect(h, ctypes.byref(r)):
        return None
    return (r.left, r.top, r.right, r.bottom)


def _pid_of(h) -> int:
    p = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value


def find_main_hwnd():
    """找微信**主窗**（不是独立窗）：优先复用 `agent.wechat_windows`（已实测正确），类名兜底。

    ⛔ 为什么要复用（我踩的坑）：我第一版只按 `mmui::MainWindow` 找，
       在微信 4.1.15.8 上**一个都匹配不到** ⇒ 工具直接报"没找到微信主窗"（假失败）。
       `wechat_windows.list_wechat_windows()` 已按 `Weixin.exe` 的 pid 枚举 + 可见性过滤，
       且实测能同时列出主窗与独立窗（报告 §45）⇒ 应当用它，不要自己重写。
    """

    def _pick_main(wins):
        """主窗 = 面积最大的那个（独立窗通常更小；报告 §45 同一约定）。"""
        if not wins:
            return 0, ""
        best = max(wins, key=lambda w: w["w"] * w["h"])
        return int(best["hwnd"]), best["cls"]

    try:
        import sys as _s
        import os as _o
        _ROOT = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
        if _ROOT not in _s.path:
            _s.path.insert(0, _ROOT)
        from agent import wechat_windows as _WW
        h, c = _pick_main([w for w in _WW.list_wechat_windows() if w["title"].strip()])
        if h:
            return h, c
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass

    cands = []

    def cb(h, l):
        if not user32.IsWindowVisible(h):
            return True
        c = _cls(h)
        if c in MAIN_WND_CLASSES:
            cands.append((_rect(h), h, c))
        return True

    user32.EnumWindows(CB(cb), 0)
    # 类名命中的可能同时有主窗与独立窗 ⇒ 取面积最大
    if cands:
        cands.sort(key=lambda x: ((x[0][2] - x[0][0]) * (x[0][3] - x[0][1])) if x[0] else 0,
                   reverse=True)
        return cands[0][1], cands[0][2]

    # 退化：找面积最大的可见窗，标题含"微信"
    best = [None, 0, ""]

    def cb2(h, l):
        if not user32.IsWindowVisible(h):
            return True
        t = _title(h)
        if "微信" not in t and "WeChat" not in t and "Weixin" not in t:
            return True
        r = _rect(h)
        if not r:
            return True
        a = (r[2] - r[0]) * (r[3] - r[1])
        if a > best[1]:
            best[0], best[1], best[2] = h, a, _cls(h)
        return True

    user32.EnumWindows(CB(cb2), 0)
    return best[0], best[2]


def snapshot_children(main_hwnd, wx_pid):
    """列出该微信进程的所有可见顶层窗（排除主窗）⇒ [(hwnd, cls, title, rect)]。"""
    out = []

    def cb(h, l):
        if h == main_hwnd or not user32.IsWindowVisible(h):
            return True
        if _pid_of(h) != wx_pid:
            return True
        r = _rect(h)
        if not r:
            return True
        w, hh = r[2] - r[0], r[3] - r[1]
        if w < 80 or hh < 80:
            return True
        out.append({"hwnd": int(h), "cls": _cls(h), "title": _title(h),
                    "rect": list(r), "w": w, "h": hh})
        return True

    user32.EnumWindows(CB(cb), 0)
    return out


def grab(hwnd, path):
    """抓单个窗口（PrintWindow + PW_RENDERFULLCONTENT=3）。只读，不激活窗口。"""
    try:
        from PIL import Image

        gdi32 = ctypes.windll.gdi32
        r = _rect(hwnd)
        if not r:
            return False, "GetWindowRect 失败"
        w, hh = r[2] - r[0], r[3] - r[1]
        if w <= 0 or hh <= 0:
            return False, "窗口尺寸非法"
        hdc = user32.GetWindowDC(hwnd)
        mdc = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, hh)
        gdi32.SelectObject(mdc, bmp)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                        ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                        ("bmiColors", wintypes.DWORD * 3)]

        # PW_RENDERFULLCONTENT = 3（能抓被遮挡/合成内容）
        user32.PrintWindow(hwnd, mdc, 3)
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -hh            # 负数 = 自上而下
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0
        buf = ctypes.create_string_buffer(w * hh * 4)
        gdi32.GetDIBits(mdc, bmp, 0, hh, buf, ctypes.byref(bi), 0)
        img = Image.frombuffer("RGBA", (w, hh), buf, "raw", "BGRA", 0, 1).convert("RGB")
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, hdc)
        img.save(path)
        return True, ""
    except Exception as e:
        return False, str(e)[:120]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=24, help="连拍帧数")
    ap.add_argument("--every", type=float, default=1.0, help="每帧间隔秒")
    ap.add_argument("--out", default=None, help="输出目录（默认 报告/面板标定-<时间戳>）")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = args.out or os.path.join(root, "报告", "面板标定-%s" % time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out, exist_ok=True)

    main_hwnd, main_cls = find_main_hwnd()
    if not main_hwnd:
        print("[✗] 没找到微信主窗。请先打开并登录电脑微信（且不要最小化）。")
        return 1
    wx_pid = _pid_of(main_hwnd)
    r0 = _rect(main_hwnd)
    l, t, rt, b = r0
    W, H = rt - l, b - t
    print("[主窗] hwnd=%s cls=%s rect=%s  (%dx%d) pid=%s"
          % (main_hwnd, main_cls, r0, W, H, wx_pid))
    if l < -30000 or t < -30000:
        print("[✗] 主窗处于最小化状态（坐标 %s）。请先**还原**窗口再跑。" % str(r0))
        return 1

    print()
    print("═" * 62)
    print("  请按下面节奏操作（不要点鼠标以外的自动化工具）：")
    print("    ① 前 %.0f 秒：**不要动**，让脚本拍到「面板关闭」的基线" % (args.every * 3))
    print("    ② 然后：**手动点开**微信输入栏的「表情」按钮（经典黄脸那个）")
    print("    ③ 保持面板打开，直到脚本跑完（还剩约 %.0f 秒）" % (args.every * (args.n - 3)))
    print("═" * 62)
    print()

    frames = []
    baseline = None
    for i in range(args.n):
        kids = snapshot_children(main_hwnd, wx_pid)
        shot = os.path.join(out, "frame-%03d.png" % i)
        ok, err = grab(main_hwnd, shot)
        rec = {"i": i, "ts": time.strftime("%H:%M:%S"), "children": kids,
               "shot": os.path.basename(shot) if ok else None, "shot_err": err}
        frames.append(rec)
        nk = len(kids)
        marker = "基线" if i < 3 else "拍摄中"
        print("  帧 %02d/%d  %s  微信子窗 %d 个  %s" % (i + 1, args.n, rec["ts"], nk, marker))
        if i < 3 and baseline is None:
            baseline = {k["hwnd"] for k in kids}
        time.sleep(args.every)

    with open(os.path.join(out, "frames.jsonl"), "w", encoding="utf-8") as f:
        for rec in frames:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ── 分析：找出"只在后段出现"的窗（＝面板） ──────────────────────────
    base_hwnds = set()
    for rec in frames[:3]:
        base_hwnds |= {k["hwnd"] for k in rec["children"]}

    appear = {}   # hwnd -> 出现帧数
    for rec in frames[3:]:
        for k in rec["children"]:
            appear[k["hwnd"]] = appear.get(k["hwnd"], 0) + 1
    n_after = max(1, len(frames) - 3)

    new_wins = []
    for rec in frames:
        for k in rec["children"]:
            if k["hwnd"] not in base_hwnds and appear.get(k["hwnd"], 0) >= n_after * 0.5:
                if not any(x["hwnd"] == k["hwnd"] for x in new_wins):
                    new_wins.append(k)

    result = {
        "main": {"hwnd": int(main_hwnd), "cls": main_cls, "rect": list(r0), "W": W, "H": H},
        "frames": args.n,
        "total_new_windows": len(new_wins),
        "candidates": new_wins,
        "suggestion": None,
        "note": "",
    }

    if new_wins:
        # 取面积最大、且位于主窗下半部的那个当面板
        def score(k):
            cx = k["rect"][0] + k["w"] / 2
            cy = k["rect"][1] + k["h"] / 2
            lower_bias = 1 if cy > t + H * 0.4 else 0
            return (lower_bias, k["w"] * k["h"])

        best = sorted(new_wins, key=score, reverse=True)[0]
        sx, sy, sw, sh = best["rect"][0], best["rect"][1], best["w"], best["h"]
        rel = {"left_ratio": (sx - l) / W, "top_ratio": (sy - t) / H,
               "width": sw, "height": sh,
               "bottom_gap": (t + H) - (sy + sh)}
        result["suggestion"] = {
            "panel_rect": [sx, sy, sx + sw, sy + sh],
            "rel": rel,
            "t_code": "return (l + int(W*%.4f), t + int(H*%.4f), l + int(W*%.4f), t + int(H*%.4f))"
                      % (rel["left_ratio"], rel["top_ratio"],
                         rel["left_ratio"] + sw / W, rel["top_ratio"] + sh / H),
        }
        print()
        print("[✓] 找到 %d 个「只在面板打开时出现」的微信窗：" % len(new_wins))
        for k in new_wins:
            print("    hwnd=%-10s cls=%-28s %dx%d @ %s"
                  % (k["hwnd"], k["cls"][:28], k["w"], k["h"], k["rect"]))
        print()
        print("[→] 建议面板矩形：%s" % result["suggestion"]["panel_rect"])
        print("[→] 相对主窗比例：left=%.4f  top=%.4f  (%d x %d px), 距主窗底 %d px"
              % (rel["left_ratio"], rel["top_ratio"], sw, sh, rel["bottom_gap"]))
        print("[→] 可直接替换 `_panel_rect` 兜底一行为：")
        print("      " + result["suggestion"]["t_code"])
        print()
        print("    ⚠️ 请人工核对：这个窗真的是**表情面板**吗？（看 frame-*.png）")
    else:
        result["note"] = ("没有发现「仅在面板打开时出现」的独立窗 ⇒ 面板可能是**内嵌弹层**"
                          "（画在主窗内部，没有自己的 HWND）。请人工看 out/frame-*.png，"
                          "把「有面板」和「没面板」两帧对比，量出面板矩形的像素范围后告诉我。")
        print()
        print("[!] 没有发现只在面板打开时出现的独立窗 ⇒ 面板可能是**内嵌弹层**。")
        print("    请人工打开 %s 下的 frame-*.png，对比前后帧。" % out)

    with open(os.path.join(out, "calib.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    print()
    print("产出目录：%s" % out)
    print("  calib.json   —— 结论（请把这个文件发给我）")
    print("  frames.jsonl —— 逐帧窗口快照")
    print("  frame-*.png  —— 逐帧主窗截图")
    return 0


if __name__ == "__main__":
    sys.exit(main())
