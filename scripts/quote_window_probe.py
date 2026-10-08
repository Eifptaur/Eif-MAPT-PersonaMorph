# -*- coding: utf-8 -*-
"""真机探针：**引用功能到底涉及哪些窗**（独立顶层窗 / 内嵌渲染区）。

：「引用功能那个窗究竟是内嵌的还是独立窗，不是先右键别人气泡，
        弹出一个菜单，然后引用吗」

本脚本实测三件事：
  ① 右键别人气泡之后，微信进程**是否多出新的顶层窗**（＝菜单是独立窗？）
  ② 那个新窗的类名 / 标题 / 尺寸（对比已知的 Qt51514QWindowToolSaveBits）
  ③ 点「引用」之后，引用条出现前后——**顶层窗数量变不变**（不变 ⇒ 引用条内嵌在主窗渲染区里）

手法：密集连拍 + 全程枚举微信顶层窗（与抓表情面板同一套）。
⚠️ 运行期间约 10 秒请不要动鼠标（菜单窗是「失焦即关」的浮窗）。
"""
import sys
import os
import time
import ctypes
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

OUT = os.path.join(ROOT, "报告", "引用窗探针-20261001")
os.makedirs(OUT, exist_ok=True)

import win32gui
import win32process
import logging

log = logging.getLogger("persona-morph")


u32 = ctypes.WinDLL("user32", use_last_error=True)

MENU_CLASS = "Qt51514QWindowToolSaveBits"
RENDER_CHILD = "MMUIRenderSubWindowHW"
MAIN_CLASS = "Qt51514QWindowIcon"


def wechat_pids():
    pids = set()

    def cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h):
                return True
            c = win32gui.GetClassName(h)
            if c.startswith("Qt51514QWindow") or "WeChat" in c or "mmui" in c:
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
            pid = win32process.GetWindowThreadProcessId(h)[1]
            if pid not in pids:
                return True
            rows.append({
                "hwnd": int(h),
                "pid": pid,
                "cls": win32gui.GetClassName(h),
                "title": win32gui.GetWindowText(h),
                "rect": win32gui.GetWindowRect(h),
            })
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return rows


def show(rows, tag):
    print("【%s】可见顶层窗 %d 个：" % (tag, len(rows)))
    for r in sorted(rows, key=lambda x: x["hwnd"]):
        L, T, R, B = r["rect"]
        print("   hwnd=%-10d cls=%-32s title=%-20s rect=%s (%dx%d)"
              % (r["hwnd"], r["cls"], r["title"][:18], r["rect"], R - L, B - T))


def children_classes(hwnd):
    out = set()

    def cb(h, _):
        try:
            out.add(win32gui.GetClassName(h))
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, cb, None)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    return out


def find_main():
    cands = []
    for r in scan(wechat_pids()):
        if r["cls"] == MAIN_CLASS:
            L, T, R, B = r["rect"]
            cands.append((r["hwnd"], (R - L) * (B - T), R - L, B - T))
    if not cands:
        return 0, []
    cands.sort(key=lambda x: -x[1])
    return cands[0][0], cands


def grab(hwnd, path=None):
    """抓窗口自身画面（**用项目已实测正确的 PrintWindow 实现**，不被遮挡、不要求前台）。

    ⚠️ 别用 ImageGrab.grab(bbox=...) —— 那是**屏幕截取**，微信不在前台就抓到别的窗口；
    实测第一版就踩了这个坑（抓出来是 WorkBuddy 的面板）。
    """
    from agent import wechat_windows as _WW
    img, meta = _WW.grab_window(int(hwnd), physical=True)
    if img is not None and path:
        img.save(path)
    return img


def grab_to_scale(hwnd, path=None):
    """同上，但返回 (img, scale)，便于把图上像素换算回逻辑坐标。"""
    from agent import wechat_windows as _WW
    img, meta = _WW.grab_window(int(hwnd), physical=True)
    if img is not None and path:
        img.save(path)
    return img, float(meta.get("scale", 1.0) or 1.0)


def diff_ratio(a, b):
    """两张图的变化像素占比（0..1）；**尺寸不同先对齐**（DPI 口径漂移的容错）。"""
    if a is None or b is None:
        return -1.0
    if a.size != b.size:
        b = b.resize(a.size, 1)  # PIL.Image.NEAREST
    pa, pb = a.convert("RGB").load(), b.convert("RGB").load()
    W, H = a.size
    step = 2
    n = ch = 0
    for y in range(0, H, step):
        for x in range(0, W, step):
            n += 1
            r1, g1, b1 = pa[x, y]
            r2, g2, b2 = pb[x, y]
            if abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2) > 30:
                ch += 1
    return (ch / n) if n else -1.0


def main():
    print("=" * 78)
    print("引用窗探针：右键气泡 -> 菜单是独立窗吗？引用条是独立窗吗？")
    print("=" * 78)
    print("\n[!] 接下来约 10 秒：请不要动鼠标、不要点任何地方。\n")

    pids = wechat_pids()
    print("微信 pid：", pids)
    base = scan(pids)
    show(base, "1 基线（右键之前）")

    main_hwnd, cands = find_main()
    print("\n主窗 = hwnd=%d  (面积最大的 %s)" % (main_hwnd, MAIN_CLASS))
    if not main_hwnd:
        print("[x] 找不到主窗，退出")
        return

    ch = sorted(children_classes(main_hwnd))
    print("主窗子窗类名（共 %d 个）：%s" % (len(ch), ch[:10]))

    # ⛔ **必须先把 DPI 口径定死再抓图**（实测大坑）：
    #   `agent/input_backend.py:65` 有 `DPI_MODE = _lock_dpi()`，**import 它就会把整个进程
    #   设成 PerMonitorV2**（进程级、不可逆）；而 `wechat_windows._DPI_AWARE` 仍是 False
    #   ⇒ 于是 `grab_window` 会**再乘一次 1.5**（实测 1212x1090 变 1818x1636）。
    #   ⇒ 先 import 依赖（固定 DPI 状态），**再**取 scale 与抓基线图。
    try:
        from agent import input_backend as _ib_pre  # noqa: F401
        print("已先 import input_backend（固定 DPI 状态），DPI_MODE=%s" % _ib_pre.DPI_MODE)
    except Exception as e:
        print("预 import input_backend 失败：", e)
    from agent import chat_ocr as _co
    from agent import wechat_windows as _WW
    _WW.set_dpi_aware()  # 让 wechat_windows 自己的标志与进程实际状态一致
    print("wechat_windows._DPI_AWARE=%s" % _WW._DPI_AWARE)

    img0 = grab(main_hwnd, os.path.join(OUT, "01-main-before.png"))
    print("主窗基线图：%dx%d  -> 01-main-before.png" % img0.size)
    meta0 = _WW.grab_window(main_hwnd, physical=True)[1]
    sc = float(meta0.get("scale", 1.0) or 1.0)
    lw, lh = meta0.get("logical", (0, 0))
    print("  逻辑尺寸=%s  scale=%s  物理尺寸=%s"
          % (meta0.get("logical"), sc, meta0.get("physical_size")))
    # ⚠️ OCR 坐标是**物理像素**（图上的），换算成逻辑要 /scale
    W, H = img0.size
    items = []
    try:
        items = _co.recognize(img0, timeout=6.0) or []
    except Exception as e:
        print("OCR 失败：", e)
    print("主窗 OCR 出 %d 段文字" % len(items))
    # 聊天区：物理图里取中部（排除顶部标题栏、底部输入框）
    # ⚠️ 上一版两次踩坑：
    #   ① 取"最靠右的文字"取到了右上角标题栏的 `link-test-ok`（不是气泡）；
    #   ② 取"最靠下"取到了底部输入框里的 URL（y=793，已经在输入区了）。
    # ⇒ 现在：① 竖向限 0.16~0.62（避开标题栏与输入框）；② 文字 ≥4 字；③ 排除 URL；
    #         ④ **取最靠左的一条**（左＝对方气泡，正对的"右键别人气泡"）。
    def _bad(t):
        s = str(t)
        return ("http" in s.lower() or "/" in s or s.strip().isdigit()
                or "：" in s or ":" in s)

    # ⚠️ 左边会话列表宽约 0.35w ⇒ 左界必须 >0.36w，否则打到的是"会话列表菜单"
    #   （实测第一次靶点 09/22 落在 x=422/1212=0.35 ⇒ 打出的是"置顶/标为未读/删除"那个菜单）
    cands_txt = [(t, x, y, w, h) for (t, x, y, w, h) in items
                 if 0.16 * H < y < 0.62 * H and 0.36 * W < x < 0.95 * W
                 and len(str(t).strip()) >= 4 and not _bad(t)]
    cands_txt.sort(key=lambda it: it[1])  # 最靠左（对方气泡）
    for t, x, y, w, h in cands_txt[:12]:
        print("   候选文字：%-26s  图上中心(%d,%d)"
              % (str(t)[:24], x + w // 2, y + h // 2))

    reports = {"pids": sorted(pids), "baseline": base, "main_hwnd": main_hwnd}

    if not cands_txt:
        print("\n[x] 聊天区没 OCR 到文字 -> 无法选靶点。")
        with open(os.path.join(OUT, "probe.json"), "w", encoding="utf-8") as f:
            json.dump(reports, f, ensure_ascii=False, indent=2)
        return

    t0, x0, y0, w0, h0 = cands_txt[0]
    # 图上中心 -> 逻辑相对坐标（scale 可能为 1.0，也可能 1.5，统一用 sc 除）
    rel = (int((x0 + w0 // 2) / sc), int((y0 + h0 // 2) / sc))
    L, T, R, B = win32gui.GetWindowRect(main_hwnd)
    screen_pt = (L + rel[0], T + rel[1])
    print("\n> 靶点（主窗逻辑相对 %s）= 文字「%s」  -> 屏幕 %s"
          % (rel, str(t0)[:20], screen_pt))

    from agent import input_backend as ib
    backend = ib.select_backend()
    print("投递后端：", type(backend).__name__, " activate=", getattr(backend, "activate", None))

    try:
        from agent import ui_adapt as _ua
        if not _ua.fg_allowed()[0] and getattr(backend, "activate", False):
            backend.activate = False
            print("已按后台档关掉伪激活（实测带伪激活反而不弹菜单）")
    except Exception as e:
        print("读 fg_allowed 失败：", e)

    frames = []
    ok, why = backend.click(main_hwnd, screen_pt, right=True)
    print("\n投递右键 ok=%s why=%s" % (ok, why))
    t_start = time.time()
    for i in range(26):
        time.sleep(0.12)
        rows = scan(pids)
        im = grab(main_hwnd, os.path.join(OUT, "frame-%02d.png" % i))
        dr = diff_ratio(img0, im)
        nwin = len(rows)
        changed = (i > 0 and nwin != frames[-1]["nwin"])
        frames.append({"i": i, "t": round(time.time() - t_start, 3),
                       "nwin": nwin, "diff": round(dr, 4),
                       "wins": [{"hwnd": r["hwnd"], "cls": r["cls"],
                                 "title": r["title"], "rect": r["rect"]} for r in rows]})
        if changed or i % 4 == 0:
            print("   frame %02d  t=%.2fs  顶层窗=%d  画面差=%.4f  %s"
                  % (i, frames[-1]["t"], nwin, dr,
                     "<== 窗口数变了！" if changed else ""))

    seen = {}
    for f in frames:
        for w in f["wins"]:
            seen.setdefault(w["hwnd"], w)
    base_ids = [b["hwnd"] for b in base]
    print("\n【2 右键之后】出现过的全部顶层窗（含过程态）：")
    for h, w in sorted(seen.items()):
        L, T, R, B = w["rect"]
        mark = "   <== 基线没有的！" if h not in base_ids else ""
        print("   hwnd=%-10d cls=%-32s title=%-20s (%dx%d)%s"
              % (h, w["cls"], w["title"][:18], R - L, B - T, mark))

    maxdiff = max(frames, key=lambda f: f["diff"])
    print("\n* 画面变化最大的帧：frame %02d  t=%.2fs  差=%.4f"
          % (maxdiff["i"], maxdiff["t"], maxdiff["diff"]))
    print("  该帧顶层窗数=%d" % maxdiff["nwin"])
    grab(main_hwnd, os.path.join(OUT, "02-after-rightclick.png"))

    reports["frames"] = frames
    reports["seen_windows"] = list(seen.values())
    reports["target_text"] = str(t0)
    reports["target_rel"] = list(rel)
    with open(os.path.join(OUT, "probe.json"), "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)
    print("\n落盘：", OUT)


if __name__ == "__main__":
    main()
