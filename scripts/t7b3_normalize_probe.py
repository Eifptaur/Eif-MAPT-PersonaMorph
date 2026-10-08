# -*- coding: utf-8 -*-
"""t7b3_normalize_probe.py —— 行距到底是"绝对像素"还是"随列表宽度/窗口缩放"？

⛔ 为什么必须查清（换窗口大小 / 换放缩倍率后还生不生效）
   t7b3_const_compat 实测：行距有 **两档 85~86 与 95~97.5**，极差 12.5px。
   而 T7b-2 报的"极差 0"只是**抽样偏了**（抽到的全是 95~97.5 那档）。
   ⇒ 现在必须回答：**这个差是"缩放导致的（可归一化）"还是"布局不同导致的（不可归一化）"？**

判据（三条，互相对照）：
   A. 行距 vs 列表宽度(list_w)      —— 若 row_h/list_w ≈ 常数 ⇒ 随列表宽度缩放
   B. 行距 vs 窗口宽度(w)           —— 若 row_h/w ≈ 常数 ⇒ 随窗口缩放
   C. 行距 vs 会话名字**字号**（用名字行框高 h 代理）—— 若 row_h/h ≈ 常数 ⇒ 是"排版比例"

用法：./runtime/python/python.exe -u scripts/t7b3_normalize_probe.py
"""
import io
import json
import os
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
BASE = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")
X_LO, X_HI = 150, 480          # 放宽下限，好让窄列表也能进
H_LO, H_HI = 19, 22


def main():
    from PIL import Image
    from agent import chat_ocr as co
    from agent import chat_header as ch

    rows = []
    for d in sorted(os.listdir(BASE)):
        j = os.path.join(BASE, d, "samples.jsonl")
        if not os.path.isfile(j):
            continue
        meta = [json.loads(l) for l in io.open(j, encoding="utf-8")]
        seen, picked = set(), []
        for m in meta:
            if not m.get("png"):
                continue
            k = (m["w"] // 200, m["h"] // 200)
            if k in seen:
                continue
            seen.add(k)
            picked.append(m)
        for m in picked[:18]:
            p = os.path.join(BASE, d, m["png"])
            if not os.path.isfile(p):
                continue
            im = Image.open(p).convert("RGB")
            W, H = im.size
            try:
                lw = ch.pane_left_for(im) or 0
            except Exception:
                lw = 0
            items = co.recognize(im, timeout=30.0) or []
            hs = [h for (t, x, y, w, h) in items
                  if X_LO <= x < X_HI and H_LO <= h <= H_HI]
            cys = sorted(y + h // 2 for (t, x, y, w, h) in items
                         if X_LO <= x < X_HI and H_LO <= h <= H_HI)
            steps = [b - a for a, b in zip(cys, cys[1:]) if 70 <= b - a <= 130]
            if not steps:
                continue
            rh = statistics.median(steps)
            rows.append({
                "png": m["png"], "w": W, "h": H, "theme": m.get("theme"),
                "list_w": lw, "row_h": rh,
                "name_box_h": statistics.median(hs) if hs else None,
                "row_over_listw": (rh / lw) if lw else None,
                "row_over_w": rh / W,
                "row_over_h": (rh / statistics.median(hs)) if hs else None,
            })

    print("=" * 108)
    print("行距的归一化探查（%d 帧，全部 dpi=144）" % len(rows))
    print("=" * 108)
    print("%-13s %5s %5s %7s %7s %6s %11s %9s %8s"
          % ("帧", "窗口w", "窗口h", "列表宽", "行距", "字高", "行距/列表宽", "行距/w", "行距/字高"))
    for r in sorted(rows, key=lambda r: r["row_h"]):
        print("%-13s %5d %5d %7d %7.1f %6s %11s %9.5f %8s"
              % (r["png"], r["w"], r["h"], r["list_w"], r["row_h"], r["name_box_h"],
                 ("%.4f" % r["row_over_listw"]) if r["row_over_listw"] else "—",
                 r["row_over_w"],
                 ("%.2f" % r["row_over_h"]) if r["row_over_h"] else "—"))

    # ── 三种归一化各自的离散度（越小越"恒定"）──
    def spread(vals):
        vals = [v for v in vals if v is not None]
        if len(vals) < 2:
            return None
        return (min(vals), max(vals), max(vals) - min(vals),
                (max(vals) - min(vals)) / statistics.mean(vals))

    print("\n" + "=" * 108)
    print("【三种归一化的离散度】（极差/均值 越小 ⇒ 该归一化越成立）")
    for label, key in [("原始行距(px)", "row_h"),
                       ("行距 ÷ 列表宽", "row_over_listw"),
                       ("行距 ÷ 窗口宽", "row_over_w"),
                       ("行距 ÷ 名字字高", "row_over_h")]:
        s = spread([r[key] for r in rows])
        if s:
            print("  %-16s 值域 %.4f..%.4f  极差 %.4f  相对极差 %.1f%%"
                  % (label, s[0], s[1], s[2], s[3] * 100))
    print("\n★ 判定：谁的『相对极差』最小 ⇒ 行距就随谁缩放 ⇒ 就该拿谁去做归一化。")

    outp = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "t7b3-归一化探查.json")
    io.open(outp, "w", encoding="utf-8").write(json.dumps(
        {"_说明": ["行距是绝对像素还是随宽度缩放 —— 三种归一化对照。",
                   "全部 dpi=144 ⇒ 本表**不能**说明跨 DPI 行为。"], "frames": rows},
        ensure_ascii=False, indent=1))
    print("\n落盘：" + outp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
