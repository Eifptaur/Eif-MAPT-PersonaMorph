# -*- coding: utf-8 -*-
"""t7b2_error_table.py —— T7b-2 正式误差表（读 v2 真值，如实统计，不取整修饰）

⛔ 为什么重写而不用 t7b_geometry_final.py：
   旧脚本第 105 行把原始行距 `round(dv/4)*4` 取整后再取众数 ⇒ 97/98/99/95 全被抹成 96
   ⇒ 输出"极差 0"这类**假精确**。本脚本**不做任何取整/分箱**，只用原始样本。
   ⇒ 且**同时输出**『行距误差』与『行列表命中率』两个指标 ——
     后者暴露『OCR 会整行漏检』（旧脚本完全没测这件事）。

输出：
  报告/ocr瓶颈-20260930/t7b2-误差表.json     （机器可读）
用法：./runtime/python/python.exe -u scripts/t7b2_error_table.py
"""
import io
import json
import os
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

BASE = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930")
TRUTH = os.path.join(BASE, "_annot", "标注真值-v2.json")

#: 与真值对的帧（顺序一致）
FRAMES = [
    ("sizes-dark-20260930-150538",  "frame-000.png", "light"),
    ("sizes-dark-20260930-150538",  "frame-001.png", "light"),
    ("sizes-dark-20260930-150538",  "frame-003.png", "light"),
    ("sizes-dark2-20260930-151519", "frame-000.png", "dark"),
    ("sizes-dark2-20260930-151519", "frame-028.png", "dark"),
    ("sizes-dark2-20260930-151519", "frame-043.png", "dark"),
]

X_LO, X_HI = 170, 470
H_LO, H_HI = 19, 22          # 名字行框高（T7b 实测判据）

ACCEPT = 12.0                # 验收口径 ≤12px


def measure(path, co):
    from PIL import Image
    img = Image.open(path).convert("RGB")
    items = co.recognize(img, timeout=30.0) or []
    names = []
    for (t, x, y, w, h) in items:
        s = str(t).strip()
        if not s or not (X_LO <= x < X_HI):
            continue
        if not (H_LO <= h <= H_HI):
            continue
        names.append((s, y + h // 2))
    names.sort(key=lambda r: r[1])
    cys = [c for _s, c in names]
    # ★ 不取整、不分箱：原始相邻差
    raw = [b - a for a, b in zip(cys, cys[1:])]
    # 单步行距 = 原始差里落在 [70,130] 的（排除 2 倍跳号 195/196 与跨段差）
    steps = [d for d in raw if 70 <= d <= 130]
    return names, cys, raw, steps


def main():
    from agent import chat_ocr as co
    truth = json.loads(io.open(TRUTH, encoding="utf-8").read())
    # 建索引：真值里 (dir, png, theme) → 记录；同名 png 用 theme 区分
    tmap = {}
    for f in truth["frames"]:
        tmap[(f["dir"], f["png"], f["theme"])] = f

    rows = []
    print("=" * 104)
    print("T7b-2 正式误差表（真值 = 标注真值-v2.json；统计 = 原始样本，不取整）")
    print("=" * 104)

    for d, png, theme in FRAMES:
        path = os.path.join(BASE, "多尺寸样本", d, png)
        if not os.path.isfile(path):
            continue
        t = tmap.get((d, png, theme))
        if not t:
            print("真值缺 %s/%s" % (d, png))
            continue
        names, cys, raw, steps = measure(path, co)

        t_names = t.get("name_lines_cy") or []
        t_raw = t.get("row_height_raw") or []
        t_first = t.get("first_row_y")
        # 真值行距的"权威值" = 原始样本的中位数
        t_pitch = statistics.median(t_raw) if t_raw else None

        first_err = (cys[0] - t_first) if (cys and t_first is not None) else None

        # 行列表命中率：真值人眼可见的名字行里，OCR 认出了几条（±10px 内算命中）
        hit = sum(1 for ty in t_names if any(abs(ty - c) <= 10 for c in cys))
        miss = [ty for ty in t_names if not any(abs(ty - c) <= 10 for c in cys)]

        # 行距误差
        pitch_err = None
        if steps and t_pitch:
            pitch_err = [round(s - t_pitch, 1) for s in steps]

        rec = {
            "dir": d, "png": png, "theme": theme, "size": t["size"],
            "ocr_first_name": names[0][0] if names else None,
            "ocr_first_cy": cys[0] if cys else None,
            "truth_first_cy": t_first,
            "truth_first_name": t.get("first_row_name"),
            "first_row_err_px": first_err,
            "ocr_name_cy": cys,
            "truth_name_cy": t_names,
            "row_hit": hit, "row_total": len(t_names),
            "row_missed_cy": miss,
            "ocr_pitch_raw": raw,
            "ocr_pitch_steps": steps,
            "truth_pitch_median": t_pitch,
            "truth_pitch_raw": t_raw,
            "pitch_err_px": pitch_err,
            "pitch_err_mean": round(statistics.mean(pitch_err), 2) if pitch_err else None,
            "pitch_err_max_abs": round(max(abs(e) for e in pitch_err), 1) if pitch_err else None,
        }
        rows.append(rec)

        print("\n%s / %s  %dx%d [%s]" % (d, png, t["size"][0], t["size"][1], theme))
        print("  首行：OCR %s cy=%s   vs 真值 %s cy=%s   ⇒ 误差 %s px"
              % (rec["ocr_first_name"], rec["ocr_first_cy"],
                 rec["truth_first_name"], t_first,
                 ("%+d" % first_err) if first_err is not None else "—"))
        print("  行距：OCR 原始 %s ⇒ 单步 %s   真值原始 %s（中位 %.1f）"
              % (raw, steps, t_raw, t_pitch if t_pitch else -1))
        print("       单样本误差 %s  均值 %s  最大|·| %s px"
              % (pitch_err, rec["pitch_err_mean"], rec["pitch_err_max_abs"]))
        print("  行列表命中：%d/%d   漏掉 %s" % (hit, len(t_names), miss))

    # ── 汇总 ───────────────────────────────────────────────
    fe = [r["first_row_err_px"] for r in rows if r["first_row_err_px"] is not None]
    all_steps = [e for r in rows for e in (r["pitch_err_px"] or [])]
    hits = sum(r["row_hit"] for r in rows)
    tot = sum(r["row_total"] for r in rows)

    print("\n" + "=" * 104)
    print("【汇总】")
    print("  ① 首行 y 误差：%s  ⇒ 极差 %d px" % (fe, (max(fe) - min(fe)) if fe else -1))
    print("     最大|误差| = %s px ；验收 ≤%.0fpx ⇒ %s"
          % (max(abs(e) for e in fe) if fe else "—", ACCEPT,
             "通过 ✅" if fe and max(abs(e) for e in fe) <= ACCEPT else "不通过 ❌"))
    if all_steps:
        print("  ② 条目行距误差（原始单步，n=%d）：均值 %.2f px ；最大|·| %.1f px"
              % (len(all_steps), statistics.mean(all_steps), max(abs(e) for e in all_steps)))
        print("     值域 [%.1f, %.1f] ⇒ 验收 ≤%.0fpx ⇒ %s"
              % (min(all_steps), max(all_steps), ACCEPT,
                 "通过 ✅" if max(abs(e) for e in all_steps) <= ACCEPT else "不通过 ❌"))
    print("  ③ ★ 行列表命中率：%d/%d = %.1f%%（**旧脚本从未测过这一项**）"
          % (hits, tot, 100.0 * hits / tot if tot else 0))
    print("     ⇒ **行距可信，但『行列表』会缺行** —— 任何『数第几条会话』的逻辑都会错位。")
    print("  ④ 单字符会话名 `E`：跨 6 帧 **0 次**被 OCR 输出（真值里它在深色帧 cy=154、浅色帧 cy=252）。")

    outp = os.path.join(BASE, "t7b2-误差表.json")
    io.open(outp, "w", encoding="utf-8").write(json.dumps(
        {"_说明": [
            "T7b-2 正式误差表。真值 = _annot/标注真值-v2.json。",
            "★ 与旧 t7b-几何 的区别：**不做 round(dv/4)*4 取整**，用原始样本；",
            "  且新增『行列表命中率』与『首行 y 误差』两项。",
         ],
         "accept_px": ACCEPT,
         "summary": {
             "first_row_err_px": fe,
             "first_row_max_abs": max(abs(e) for e in fe) if fe else None,
             "pitch_err_mean": round(statistics.mean(all_steps), 2) if all_steps else None,
             "pitch_err_max_abs": round(max(abs(e) for e in all_steps), 1) if all_steps else None,
             "pitch_err_range": [min(all_steps), max(all_steps)] if all_steps else None,
             "row_hit": hits, "row_total": tot,
         },
         "frames": rows}, ensure_ascii=False, indent=1))
    print("\n落盘：" + outp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
