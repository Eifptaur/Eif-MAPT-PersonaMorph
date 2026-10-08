# -*- coding: utf-8 -*-
"""t7b4_dpi_free_geometry.py —— 不依赖 DPI 的几何口径（用"字高"当天然标尺）。

★ 目标：找一个**不依赖 DPI 数值**的方法 —— 常规数据表覆盖不到的 DPI 也要成立。
  ⇒ 不能查表，必须**用同一张图里量出来的东西当标尺**。

★ 本项目里**已经有**这个机制（不必新造）：
  `agent/chat_ocr.py:897 text_height_hint(im)` —— 逐行统计墨迹占比，
  取连续墨迹段的**中位高度** = "字高(px)"。**它不依赖 DPI**：DPI 变 ⇒ 字高按比例变，
  但"字高"是按图内像素量出来的，天然跟着走。
  `auto_zoom()` 就是拿它把字缩放到固定像素带（45~90px）——**已是本仓成熟做法**。

★ 于是 T7b 的常量应改成**相对字高的倍数**（相对量），而不是绝对 px：
   · 名字框高 h ≈ ? × 字高        （原 19~22px 是 150% 下的绝对值）
   · 行距   pitch ≈ ? × 字高      （原 97px）
   · 名字列左沿 x ≈ 列表左沿 + ? × 字高

本脚本：在 28 帧上量 ① 字高 ② 名字框高 ③ 行距，算出**倍数**，看倍数是否跨帧恒定
  （若恒定 ⇒ 该口径跨 DPI 自然成立；若仍散 ⇒ 说明这个方法也不灵，要如实说）。

★ 判据仍要"框高相对":
  取"本帧 h 的众数带"里的框当名字（不用绝对 [19,22]）。
输出：报告/ocr瓶颈-20260930/t7b4-DPI无关口径.json
"""
import io
import json
import os
import statistics
import sys
import collections

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

BASE = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")


def main():
    from PIL import Image
    from agent import chat_ocr as co
    from agent import chat_header as ch

    sets = []
    for d in sorted(os.listdir(BASE)):
        j = os.path.join(BASE, d, "samples.jsonl")
        if os.path.isfile(j):
            sets.append((d, j))

    out = []
    for d, j in sets:
        meta = [json.loads(l) for l in io.open(j, encoding="utf-8")]
        meta = [m for m in meta if m.get("png")]
        seen, picked = set(), []
        for m in meta:
            k = (m["w"] // 150, m["h"] // 150)
            if k in seen:
                continue
            seen.add(k)
            picked.append(m)
        picked = picked[:14]
        for m in picked:
            p = os.path.join(BASE, d, m["png"])
            if not os.path.isfile(p):
                continue
            im = Image.open(p).convert("RGB")
            W, H = im.size
            try:
                pane = ch.pane_left_for(im)
            except Exception:
                pane = 0
            items = co.recognize(im, timeout=40.0) or []
            boxes = [(str(t).strip(), x, y, w, h) for (t, x, y, w, h) in items]

            # ① 图内字高（DPI 无关标尺）
            th = co.text_height_hint(im)

            # ② 名字框：**结构性** —— x 落在列表左沿左侧的近邻带，且 h 是本帧众数
            #    先取"所有 h∈[10,30] 的框"的 h 众数（名字与正文同高），
            #    再在 x < pane 里取 x 最左的那一簇
            hs = [b[4] for b in boxes if 10 <= b[4] <= 30]
            if not hs:
                out.append({"dir": d, "png": m["png"], "size": [W, H], "theme": m.get("theme"),
                            "text_h": th, "why": "无可用框"})
                continue
            h_mode = collections.Counter(hs).most_common(1)[0][0]
            name_cand = [b for b in boxes if b[1] < (pane or W) and abs(b[4] - h_mode) <= 2]
            name_xs = sorted(set(b[1] for b in name_cand))
            # 名字列 x = 最左的成簇者（取出现次数最多的 x 带）
            xhist = collections.Counter((b[1] // 8) * 8 for b in name_cand)
            name_x_band = xhist.most_common(1)[0][0] if xhist else None
            names = sorted([b for b in name_cand if name_x_band is not None
                            and name_x_band <= b[1] < name_x_band + 16],
                           key=lambda b: b[2])
            cys = [b[2] + b[4] / 2.0 for b in names]
            steps = [b - a for a, b in zip(cys, cys[1:]) if (th * 1.5) <= (b - a) <= (th * 6)]
            pitch = statistics.median(steps) if steps else None

            rec = {
                "dir": d, "png": m["png"], "size": [W, H], "dpi": m.get("dpi"),
                "theme": m.get("theme"), "pane": pane,
                "text_h_px": th,
                "h_mode_px": h_mode,
                "name_x_band": name_x_band,
                "n_names": len(names),
                "pitch_px": pitch,
                "pitch_over_texth": round(pitch / th, 3) if (pitch and th) else None,
                "h_over_texth": round(h_mode / float(th), 3) if th else None,
                "name_x_over_pane": round(name_x_band / float(pane), 3) if (name_x_band and pane) else None,
                "name_x_over_W": round(name_x_band / float(W), 3) if (name_x_band and W) else None,
            }
            out.append(rec)
            print("%-14s %5dx%-5d %-5s pane=%-5s | 字高%2d 框高%2d | 名字x=%-4s n%2d | 行距%-6s = %.2f×字高 | x/pane=%.2f"
                  % (m["png"], W, H, m.get("theme"), pane, th, h_mode, name_x_band,
                     len(names), pitch, rec["pitch_over_texth"] or 0,
                     rec["name_x_over_pane"] or 0))

    # ── 汇总：倍数是否恒定？ ──
    def stats(key):
        vs = [r[key] for r in out if r.get(key)]
        if not vs:
            return None
        return {"n": len(vs), "min": min(vs), "max": max(vs),
                "range": round(max(vs) - min(vs), 3), "mean": round(statistics.mean(vs), 3)}

    print("\n" + "=" * 96)
    print("【核心】行距 ÷ 字高 是否跨帧恒定？（恒定 ⇒ 跨 DPI 自动成立）")
    s = stats("pitch_over_texth")
    print("  ", s)
    print(" 框高 ÷ 字高：", stats("h_over_texth"))
    print(" 名字x ÷ 列表左沿pane：", stats("name_x_over_pane"))
    print(" 名字x ÷ 窗口宽W：", stats("name_x_over_W"))

    # 对照：行距的**绝对 px** 值域（上一轮结论）
    absp = [r["pitch_px"] for r in out if r.get("pitch_px")]
    print(" 对照 行距绝对值域：%.1f .. %.1f  极差 %.1f (n=%d)"
          % (min(absp), max(absp), max(absp) - min(absp), len(absp)))

    io.open(os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "t7b4-DPI无关口径.json"),
            "w", encoding="utf-8").write(json.dumps({
                "_说明": [
                    "要求『找能跳过 DPI 的方法』。",
                    "本文件用图内量出的『字高』(chat_ocr.text_height_hint) 当天然标尺，",
                    "把 T7b 的绝对像素常量改成『字高的倍数』，看倍数是否跨帧恒定。",
                    "★ 若倍数恒定 ⇒ 该口径跨 DPI 自动成立（DPI 变则字高按比例变，倍数不变）。",
                ],
                "summary": {
                    "pitch_over_texth": stats("pitch_over_texth"),
                    "h_over_texth": stats("h_over_texth"),
                    "name_x_over_pane": stats("name_x_over_pane"),
                    "name_x_over_W": stats("name_x_over_W"),
                    "pitch_abs_range": [min(absp), max(absp)] if absp else None,
                },
                "frames": out}, ensure_ascii=False, indent=1))
    print("\n落盘：报告/ocr瓶颈-20260930/t7b4-DPI无关口径.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
