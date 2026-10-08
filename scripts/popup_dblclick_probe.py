# -*- coding: utf-8 -*-
"""**双击开独立窗 · 真机探针**（的正路）。


  「开头怎么把目标会话认出来，然后变成独立窗口，后续都只操作这个独立窗口，不就完了吗？
    怎么叉掉他还能再弹出来，或者重新打出来」

本探针只回答**前提问题**（不做业务改动）：
  P1 在**会话列表某一行的落点**上连投两枪（间隔 T），能否打出**新的独立顶层窗**？
  P2 间隔 T 取多少才被判成双击？（太短会把会话拖成浮动窗/关掉；太长不算双击）
  P3 打出来的独立窗，**标题是不是会话名**、**是不是新 hwnd**？
  P4 若已有同名独立窗，再双击会出现**第二个**吗（=会重复开窗，必须防）

⛔ 不动光标（全走投递）；不发任何消息。
⛔ 只对 `--chat-name` 指定的会话做。
"""
import argparse
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import version_gate as vg          # noqa: E402
from agent.config import get_config           # noqa: E402
from agent.wechat import WeChatAdapter        # noqa: E402
from agent import chat_ocr as co              # noqa: E402
from agent import chat_header as ch           # noqa: E402
from agent import input_backend as ib         # noqa: E402
from agent import wechat_windows as ww        # noqa: E402
import logging

log = logging.getLogger("persona-morph")


def _wins():
    try:
        return ww.list_wechat_windows()
    except Exception:
        return []


def _titles(ws):
    return sorted(w.get("title", "") for w in ws)


def main() -> int:
    ap = argparse.ArgumentParser(description="双击开独立窗真机探针")
    ap.add_argument("--chat-name", required=True, help="目标会话名（须在主窗列表里看得见）")
    ap.add_argument("--gap-ms", type=float, default=180.0, help="两枪间隔（毫秒）")
    ap.add_argument("--rounds", type=int, default=1, help="重复几轮（每轮前会先记录基线）")
    a = ap.parse_args()

    vg.allow_session("双击开独立窗探针（%s）" % a.chat_name)
    ad = WeChatAdapter(get_config())
    gui = ad._get_gui()
    try:
        gui._update_render_rect()
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    main_hwnd = int(getattr(gui, "main_hwnd", 0) or 0) or ib.find_main_window()
    if not main_hwnd:
        print("⛔ 找不到微信主窗"); return 2

    out = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "chat": a.chat_name,
           "gap_ms": a.gap_ms, "main": main_hwnd, "rounds": []}
    print("① 现场：main=%s  目标=%s" % (main_hwnd, a.chat_name))
    w0 = _wins()
    print("   窗口基线 %d 个：%s" % (len(w0), _titles(w0)))
    for w in w0:
        print("      hwnd=%-12s %-30s %dx%d" % (w.get("hwnd"), str(w.get("title"))[:28], w.get("w", 0), w.get("h", 0)))

    for r in range(int(a.rounds)):
        print("\n② 第 %d/%d 轮" % (r + 1, a.rounds))
        # 抓一帧，定位目标行
        img = ch.capture_image(gui=gui)
        if img is None:
            print("   ⛔ 抓图失败"); break
        row = None
        try:
            row = co.find_row_info(img, a.chat_name)
        except Exception as e:  # noqa: BLE001
            print("   find_row_info 异常：%s" % e)
        if not row:
            try:
                _i2 = co.capture_best(gui=gui, frames=3)
                row = co.find_row_info(_i2, a.chat_name) if _i2 is not None else None
            except Exception:
                row = None
        if not row:
            print("   ⛔ 列表里没看到「%s」那一行 ⇒ 本轮跳过" % a.chat_name)
            out["rounds"].append({"i": r, "skip": "row_not_found", "titles": _titles(_wins())})
            continue
        ox, oy = int(getattr(gui, "origin_x", 0)), int(getattr(gui, "origin_y", 0))
        pt = (ox + int(row["pos"][0]), oy + int(row["pos"][1]))
        print("   目标行 y=%s  落点=%s" % (row.get("y_abs"), pt))

        before = _wins()
        before_hwnds = set(int(w["hwnd"]) for w in before)
        print("   双击前窗口 %d 个" % len(before))

        # 连投两枪（不动光标）
        be = ib.MessageBackend(activate=True)
        targets = ib.row_click_targets(main_hwnd) or [main_hwnd]
        tgt = targets[0]
        print("   投递目标窗 = %s（%s）" % (tgt, ib.win_kind(main_hwnd, tgt)))
        ks = []
        for shot in (1, 2):
            ok, why = ad._click_posted(be, tgt, pt, "双击开独立窗第%d枪" % shot,
                                       allow_new=True, hover_ms=300, press_ms=150)
            ks.append((bool(ok), str(why)[:80]))
            print("     枪%d ok=%s why=%s" % (shot, ok, str(why)[:70]))
            if shot == 1:
                time.sleep(float(a.gap_ms) / 1000.0)
        time.sleep(1.0)
        after = _wins()
        new = [w for w in after if int(w["hwnd"]) not in before_hwnds]
        print("   双击后窗口 %d 个；新增 %d 个：%s"
              % (len(after), len(new), [(w.get("hwnd"), w.get("title"), w.get("w"), w.get("h")) for w in new]))
        hit = ww.find_chat_window(a.chat_name)
        print("   find_chat_window(%r) => hwnd=%s title=%r" % (a.chat_name, hit.get("hwnd"), hit.get("title")))
        out["rounds"].append({
            "i": r, "row_y": row.get("y_abs"), "pt": pt, "target_hwnd": tgt,
            "shots": ks, "before_n": len(before), "after_n": len(after),
            "new_windows": [{"hwnd": w.get("hwnd"), "title": w.get("title"), "w": w.get("w"), "h": w.get("h")} for w in new],
            "find": hit,
        })

    d = os.path.join(ROOT, "wechatauto_logs", "popup_dblclick")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n③ 明细落盘：%s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
