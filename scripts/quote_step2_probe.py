# -*- coding: utf-8 -*-
"""真机探针第二步：**点「引用」之后，引用条是独立顶层窗还是内嵌在主窗渲染区里？**

判据（**唯一可靠**）：
  · 顶层窗数 3 → 3  ⇒ 引用条**内嵌**在消息窗的渲染区里
  · 顶层窗数 3 → 4  ⇒ 引用条是**另一个独立顶层窗**
外加主窗画面差分（引用条出现会让输入框**变高**，画面必有变化）。

前置：`scripts/quote_window_probe.py` 已经把气泡菜单弹出来了（窗 `Qt51514QWindowToolSaveBits`）。
本脚本自己重做一遍完整链路：右键气泡 → 找菜单窗 → 点「引用」→ 密集连拍看窗口数。
"""
import sys
import os
import time
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

MENU_CLASS = "Qt51514QWindowToolSaveBits"
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
        a = (R - L) * (B - T)
        if a > ba:
            best, ba = r["hwnd"], a
    return best


def grab(hwnd, path=None):
    from agent import wechat_windows as _WW
    img, meta = _WW.grab_window(int(hwnd), physical=True)
    if img is not None and path:
        img.save(path)
    return img


def diff_ratio(a, b):
    if a is None or b is None:
        return -1.0
    if a.size != b.size:
        b = b.resize(a.size, 1)
    pa, pb = a.convert("RGB").load(), b.convert("RGB").load()
    W, H = a.size
    n = ch = 0
    for y in range(0, H, 2):
        for x in range(0, W, 2):
            n += 1
            r1, g1, b1 = pa[x, y]
            r2, g2, b2 = pb[x, y]
            if abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2) > 30:
                ch += 1
    return (ch / n) if n else -1.0


def main():
    print("=" * 78)
    print("点「引用」之后：引用条是独立窗还是内嵌？")
    print("=" * 78)
    print("\n[!] 接下来约 12 秒：请不要动鼠标、不要点任何地方。\n")

    # ⛔ 先 import 固定 DPI 口径（见 quote_window_probe.py 的说明）
    from agent import input_backend as _ib
    from agent import wechat_windows as _WW
    from agent import chat_ocr as _co
    _WW.set_dpi_aware()

    pids = wechat_pids()
    main_hwnd = find_main(pids)
    print("微信 pid=%s  主窗 hwnd=%d" % (sorted(pids), main_hwnd))
    w0 = scan(pids)
    print("基线顶层窗 %d 个：" % len(w0))
    for r in sorted(w0, key=lambda x: x["hwnd"]):
        L, T, R, B = r["rect"]
        print("   hwnd=%-10d %-32s %-18s (%dx%d)"
              % (r["hwnd"], r["cls"], r["title"][:16], R - L, B - T))
    base_ids = [r["hwnd"] for r in w0]

    img0 = grab(main_hwnd, os.path.join(OUT, "10-before-quote.png"))
    print("\n基线主窗图：%dx%d" % img0.size)

    # ── 找气泡靶点（聊天区、≥4 字、非 URL、最靠左=对方气泡）──
    items = _co.recognize(img0, timeout=6.0) or []
    W, H = img0.size
    print("OCR %d 段" % len(items))

    def _bad(t):
        s = str(t)
        return ("http" in s.lower() or "/" in s or s.strip().isdigit()
                or "：" in s or ":" in s)

    cands = [(t, x, y, w, h) for (t, x, y, w, h) in items
             if 0.16 * H < y < 0.62 * H and 0.36 * W < x < 0.95 * W
             and len(str(t).strip()) >= 4 and not _bad(t)]
    cands.sort(key=lambda it: it[1])
    if not cands:
        print("[x] 没有可用气泡靶点")
        return
    t0, x0, y0, w0_, h0 = cands[0]
    rel = (x0 + w0_ // 2, y0 + h0 // 2)
    L, T, R, B = win32gui.GetWindowRect(main_hwnd)
    pt = (L + rel[0], T + rel[1])
    print("靶点 = 「%s」 主窗相对 %s -> 屏幕 %s" % (str(t0)[:24], rel, pt))

    backend = _ib.select_backend()
    try:
        from agent import ui_adapt as _ua
        if not _ua.fg_allowed()[0] and getattr(backend, "activate", False):
            backend.activate = False
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass

    pid = sorted(pids)[0]
    rep = {"pid": pid, "main": main_hwnd, "target": str(t0), "rel": list(rel)}

    # ── ① 投递右键 ──
    ok, why = backend.click(main_hwnd, pt, right=True)
    print("\n① 投递右键 ok=%s" % ok)
    time.sleep(0.7)
    new_menu = [r for r in scan(pids) if r["hwnd"] not in base_ids]
    print("   新窗 %d 个：%s" % (len(new_menu), [(r["hwnd"], r["cls"], r["rect"][2] - r["rect"][0], r["rect"][3] - r["rect"][1]) for r in new_menu]))
    if not new_menu:
        print("[x] 菜单没弹出来")
        return
    menu = new_menu[0]
    menu_img = grab(menu["hwnd"], os.path.join(OUT, "11-menu.png"))
    print("   菜单窗图：%s -> 11-menu.png" % (str(None if menu_img is None else menu_img.size)))
    rep["menu_hwnd"] = menu["hwnd"]
    rep["menu_rect"] = menu["rect"]

    # ── ② 在菜单窗里 OCR 找「引用」那一项 ──
    mi = _co.recognize(menu_img, timeout=5.0) or []
    print("\n② 菜单 OCR %d 项：" % len(mi))
    hit = None
    for (t, x, y, w, h) in mi:
        print("   %-14s  中心(%d,%d)" % (str(t)[:12], x + w // 2, y + h // 2))
        s = str(t)
        if ("引用" in s) or ("引" in s and "用" in s):
            hit = (t, x + w // 2, y + h // 2)
    if not hit:
        # 放大重读（项目实测：微信自绘菜单原尺寸常读不出）
        try:
            from PIL import Image as _I
            big = menu_img.resize((menu_img.width * 3, menu_img.height * 3), _I.LANCZOS)
            mi2 = _co.recognize(big, timeout=6.0) or []
            print("   放大 3x 重读 %d 项" % len(mi2))
            for (t, x, y, w, h) in mi2:
                s = str(t)
                print("      %-14s 中心(%d,%d)" % (s[:12], (x + w // 2) // 3, (y + h // 2) // 3))
                if "引用" in s or ("引" in s and "用" in s):
                    hit = (t, (x + w // 2) // 3, (y + h // 2) // 3)
        except Exception as e:
            print("   放大重读失败：", e)

    if not hit:
        print("[x] 菜单里没找到「引用」")
        rep["quote_found"] = False
        with open(os.path.join(OUT, "quote-step2.json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        return

    qt, qx, qy = hit
    print("\n★ 找到「引用」= 「%s」 菜单窗相对 (%d,%d)" % (qt, qx, qy))
    mL, mT, mR, mB = win32gui.GetWindowRect(menu["hwnd"])
    qpt = (mL + qx, mT + qy)
    rep["quote_item"] = str(qt)
    rep["quote_rel"] = [qx, qy]
    rep["quote_screen"] = list(qpt)

    # ── ③ 投递点「引用」，密集连拍看顶层窗数 ──
    img_before_click = grab(main_hwnd, os.path.join(OUT, "12-before-quote-click.png"))
    ok2, why2 = backend.click(menu["hwnd"], qpt)
    print("③ 投递点「引用」 ok=%s why=%s" % (ok2, why2))

    frames = []
    t_start = time.time()
    for i in range(24):
        time.sleep(0.12)
        rows = scan(pids)
        im = grab(main_hwnd, os.path.join(OUT, "qframe-%02d.png" % i))
        dr = diff_ratio(img_before_click, im)
        n = len(rows)
        frames.append({"i": i, "t": round(time.time() - t_start, 3), "nwin": n,
                       "diff": round(dr, 4),
                       "wins": [{"hwnd": r["hwnd"], "cls": r["cls"], "title": r["title"],
                                 "rect": r["rect"]} for r in rows]})
        print("   qframe %02d t=%.2fs 顶层窗=%d 主窗画面差=%.4f" % (i, frames[-1]["t"], n, dr))

    seen = {}
    for f in frames:
        for w in f["wins"]:
            seen.setdefault(w["hwnd"], w)
    print("\n【④ 点「引用」之后】出现过的全部顶层窗：")
    for h, w in sorted(seen.items()):
        L, T, R, B = w["rect"]
        mark = "  <== 基线没有的" if h not in base_ids else ""
        print("   hwnd=%-10d %-32s %-18s (%dx%d)%s"
              % (h, w["cls"], w["title"][:16], R - L, B - T, mark))

    nset = sorted(set(f["nwin"] for f in frames))
    maxd = max(frames, key=lambda f: f["diff"])
    print("\n★ 基线顶层窗数 = %d（菜单弹出时曾到 %d）" % (len(base_ids), len(base_ids) + 1))
    print("★ 点「引用」之后顶层窗数取值集合 = %s" % nset)
    print("★ 主窗画面最大变化 = %.4f（frame %d）" % (maxd["diff"], maxd["i"]))
    # ⚠️ 判据：点「引用」后**菜单窗已关**，若从此**不再出现新顶层窗**（= 基线数）且主窗画面有明显变化
    #    ⇒ 引用条是**内嵌**在主窗渲染区里的。
    #   （上一版把 base_ids 在菜单弹出后当成基线，导致判断写反 —— 这里用"最新一帧的窗口集合"比。）
    last_ids = set(w["hwnd"] for w in frames[-1]["wins"])
    extra = last_ids - set(base_ids)
    if not extra and maxd["diff"] > 0.02:
        print("\n==> 结论：**引用条是内嵌在主窗渲染区里的**")
        print("    （点「引用」后顶层窗数回到基线 %d、无新窗；主窗画面差 %.4f ⇒ 内容变了但没开新窗）"
              % (len(base_ids), maxd["diff"]))
    elif extra:
        print("\n==> 结论：**引用条涉及新的独立顶层窗**：%s" % sorted(extra))
    else:
        print("\n==> 结论：**不确定**（无新窗，但主窗画面也没变 ⇒ 引用可能没点上）")

    rep["frames"] = frames
    rep["nwin_set"] = nset
    rep["baseline_nwin"] = len(base_ids)
    rep["max_main_diff"] = maxd["diff"]
    rep["seen"] = list(seen.values())
    with open(os.path.join(OUT, "quote-step2.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    print("\n落盘：", OUT)


if __name__ == "__main__":
    main()
