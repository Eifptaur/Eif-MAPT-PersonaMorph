# -*- coding: utf-8 -*-
"""t7b3_const_compat.py —— 我的 T7b/T7b-2 常量到底在多大范围内成立？（诚实压力测试）

⛔ 为什么必须做这一步
   ：「**你这么搞能确保在不同电脑、不同 DPI、不同放缩倍率、不同窗口大小下生效吗**」
   而我的实测样本**只有 DPI=144（150%）一种**，本机也是 144 ⇒ **跨 DPI 我没有数据**。
   ⇒ 唯一能做的诚实检验：**在已有的 130 帧上，把常量拿去用，看它在哪里开始不成立**。

   本脚本**不预测**，只如实报告三种结果：
     · 成立（命中）      · 不成立（漏）      · 无法判定（该帧没有可对照的真值）

检验三个常量：
   C1 名字列 x ∈ [170, 470)   —— 是否在**任何窗口宽度**下都框得住名字
   C2 名字行框高 h ∈ [19, 22] —— 是否在**任何窗口高度/宽度**下都成立
   C3 条目行距 ≈97            —— 是否在任何尺寸下都稳定

输出：报告/ocr瓶颈-20260930/t7b3-常量兼容性.json
用法：./runtime/python/python.exe -u scripts/t7b3_const_compat.py
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
X_LO, X_HI = 170, 470
H_LO, H_HI = 19, 22


def main():
    from PIL import Image
    from agent import chat_ocr as co

    sets = []
    for d in sorted(os.listdir(BASE)):
        p = os.path.join(BASE, d)
        j = os.path.join(p, "samples.jsonl")
        if os.path.isfile(j):
            sets.append((d, j))

    rows = []
    for d, j in sets:
        meta = [json.loads(l) for l in io.open(j, encoding="utf-8")]
        meta = [m for m in meta if m.get("png")]
        print("\n" + "=" * 96)
        print("样本集 %s  （%d 帧, dpi=%s）" % (d, len(meta), sorted(set(m.get("dpi") for m in meta))))
        print("=" * 96)
        # 抽样：每集取 12 帧（尺寸去重，覆盖宽窄）
        seen, picked = set(), []
        for m in meta:
            key = (m["w"] // 150, m["h"] // 150)
            if key in seen:
                continue
            seen.add(key)
            picked.append(m)
        picked = picked[:14]

        for m in picked:
            imgp = os.path.join(BASE, d, m["png"])
            if not os.path.isfile(imgp):
                continue
            im = Image.open(imgp).convert("RGB")
            W, H = im.size
            items = co.recognize(im, timeout=30.0) or []

            all_boxes = [(str(t).strip(), x, y, w, h) for (t, x, y, w, h) in items]
            # C1：名字列窗口里一共有多少文字框（应 >0，否则窗口错了）
            in_x = [b for b in all_boxes if X_LO <= b[1] < X_HI]
            # C2：里面框高落在 [19,22] 的有几个（应有若干"名字行"）
            name_h = [b for b in in_x if H_LO <= b[4] <= H_HI]

            cys = sorted(b[2] + b[4] // 2 for b in name_h)
            steps = [b - a for a, b in zip(cys, cys[1:]) if 70 <= b - a <= 130]

            rec = {
                "dir": d, "png": m["png"], "size": [W, H], "dpi": m.get("dpi"),
                "theme": m.get("theme"),
                "n_boxes_total": len(all_boxes),
                "n_in_x_window": len(in_x),
                "n_name_h": len(name_h),
                "name_cy": cys[:10],
                "pitch_steps": steps[:8],
                "pitch_median": statistics.median(steps) if steps else None,
                "c1_ok": len(in_x) > 0,
                "c2_ok": len(name_h) > 0,
                "c2_names": [b[0][:10] for b in name_h[:6]],
            }
            rows.append(rec)
            print("  %-14s %5dx%-5d dpi=%-4s %-5s | 总框%3d | x窗内%3d %s | 名字行%2d %s | 行距中位 %s"
                  % (m["png"], W, H, m.get("dpi"), m.get("theme"),
                     len(all_boxes), len(in_x), "✓" if rec["c1_ok"] else "✗",
                     len(name_h), "✓" if rec["c2_ok"] else "✗",
                     rec["pitch_median"]))

    # ── 汇总 ──
    print("\n" + "=" * 96)
    print("【汇总】跨 %d 帧（**全部 dpi=144**）" % len(rows))
    ok1 = sum(1 for r in rows if r["c1_ok"])
    ok2 = sum(1 for r in rows if r["c2_ok"])
    pitches = [r["pitch_median"] for r in rows if r["pitch_median"]]
    print("  C1 名字列 x∈[170,470) 命中：%d/%d" % (ok1, len(rows)))
    print("  C2 名字行高 h∈[19,22] 命中：%d/%d" % (ok2, len(rows)))
    if pitches:
        print("  C3 行距中位：值域 %.1f..%.1f  极差 %.1f  均值 %.1f  （n=%d）"
              % (min(pitches), max(pitches), max(pitches) - min(pitches),
                 statistics.mean(pitches), len(pitches)))
    # ★ 关键：行距是否随窗口宽度变化？（宽度跨度很大，看它是否恒定）
    print("\n  ★ 行距 vs 窗口宽度（看是否随尺寸漂移）：")
    for r in sorted(rows, key=lambda r: r["size"][0]):
        if r["pitch_median"] is not None:
            print("     宽 %5d  高 %5d  ⇒  行距 %s" % (r["size"][0], r["size"][1], r["pitch_median"]))

    outp = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "t7b3-常量兼容性.json")
    io.open(outp, "w", encoding="utf-8").write(json.dumps({
        "_说明": [
            "要跨电脑 / DPI / 放缩 / 窗口大小都生效。",
            "★ 事实：本样本库 130 帧 **全部 dpi=144**（本机也是 144）⇒ **跨 DPI 无数据，不能声称支持**。",
            "本文件只如实报告『在已有帧上，常量在哪里成立 / 哪里不成立』。",
        ],
        "dpi_coverage": sorted(set(r["dpi"] for r in rows)),
        "summary": {
            "n_frames": len(rows), "c1_ok": ok1, "c2_ok": ok2,
            "pitch_range": [min(pitches), max(pitches)] if pitches else None,
            "pitch_mean": round(statistics.mean(pitches), 1) if pitches else None,
        },
        "frames": rows}, ensure_ascii=False, indent=1))
    print("\n落盘：" + outp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
