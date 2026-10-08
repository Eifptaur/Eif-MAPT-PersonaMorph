# -*- coding: utf-8 -*-
"""t7b4_dpi_free_v2.py —— 真正的"跳过 DPI"方案：全用【相对结构】，零绝对像素。

★ 三个绝对常量（上一轮被证明只在 150% + 宽列表成立）：
    C1 名字列 x ∈ [170,470)   C2 名字框高 h ∈ [19,22]   C3 行距 ≈ 97

★ 本轮先纠一个**上一轮的错误**（见 t7b4_verify_bucket 的输出）：
    "行距两档(86/97)"**是假的** —— 86 档来自 **x=453~480 的调试面板文字**污染了
    [170,470) 窗口。真名字一直在同一张 95~98 网格上。
    ⇒ 所以正确的修法**不是"检测两档"**，而是**别用绝对 x 窗口**。

★ 本方案（不查表、不看 DPI）：
    ① 名字列 = **结构定位**：会话列表左沿 `pane_left` 已知 ⇒
       名字左端 x 应在 `pane_left` 的**左侧一小段**（实测 x≈176，pane≈329~1087 时在左半边）。
       更稳：取"x < pane_left 的框中、x 最小的一簇"（头像右侧紧邻）。
       ⇒ **不用绝对 170/470**。
    ② 名字框高 = **本帧众数**：所有框的 h 里挑"出现次数最多的 h"（名字与预览同高）。
       ⇒ **不用绝对 19/22**（DPI 变则整体按比例变，众数自动跟上）。
    ③ 行距 = **本帧相邻名字 cy 之差的众数/中位**，
       不做任何"两档检测"，因为**根本没有两档**。
    ④ 校验锚（可选，最稳）：绿底高亮行的上下沿 = 一个**行高**的实测标尺。

本脚本在 28 帧上跑，报**值域**，与旧判据对照。
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


def pick_names(boxes, pane, W, H):
    """结构性取名字框 —— 全程用相对判据。

    返回 (names, info)
      names: [(text, x, y, w, h)] 按 y 排序
    """
    info = {}
    cand = [b for b in boxes if b[1] < max(1, int((pane or W) * 1.0))]
    if not cand:
        cand = [b for b in boxes if b[1] < W * 0.5]
    if not cand:
        return [], info
    # ① 本帧"正文框高"众数（避开图标 26px 与细分隔线 2~10px）
    hs = [b[4] for b in cand if 11 <= b[4] <= 34]
    if not hs:
        return [], info
    h_mode = collections.Counter(hs).most_common(1)[0][0]
    info["h_mode"] = h_mode
    band = [b for b in cand if abs(b[4] - h_mode) <= 2]
    if not band:
        return [], info
    # ② 名字列 = x 的最小密集簇（8px 分箱取众数，再取最小带）
    xh = collections.Counter((b[1] // 8) * 8 for b in band)
    # 取"计数 >= 2 的带"里 x 最小的（单个的可能是零星噪声）
    xtop = sorted([x for x, c in xh.items() if c >= 2])
    x_band = xtop[0] if xtop else min(xh)
    info["x_band"] = x_band
    names = [b for b in band if x_band <= b[1] < x_band + 24]
    names = sorted(names, key=lambda b: b[2])
    return names, info


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

            names, info = pick_names(boxes, pane, W, H)
            cys = [b[2] + b[4] / 2.0 for b in names]
            steps = [b - a for a, b in zip(cys, cys[1:]) if 40 <= b - a <= 200]
            pitch = statistics.median(steps) if steps else None

            # 旧的绝对判据（对照）
            old = [b for b in boxes if 170 <= b[1] < 470 and 19 <= b[4] <= 22]
            old_cy = sorted(b[2] + b[4] // 2 for b in old)
            old_steps = [b - a for a, b in zip(old_cy, old_cy[1:]) if 70 <= b - a <= 130]

            rec = {"dir": d, "png": m["png"], "size": [W, H], "dpi": m.get("dpi"),
                   "theme": m.get("theme"), "pane": pane,
                   "h_mode": info.get("h_mode"), "x_band": info.get("x_band"),
                   "n_names": len(names),
                   "names": [b[0][:12] for b in names],
                   "cys": cys,
                   "pitch": pitch,
                   "old_n": len(old), "old_pitch": statistics.median(old_steps) if old_steps else None}
            out.append(rec)
            print("%-14s %5dx%-5d %-5s pane=%-5s | h众数%2s 名字x带%-4s n%2d | 新行距 %-6s | 旧行距 %-6s (n%2d)"
                  % (m["png"], W, H, m.get("theme"), pane, info.get("h_mode"),
                     info.get("x_band"), len(names), pitch, rec["old_pitch"], len(old)))

    np_ = [r["pitch"] for r in out if r.get("pitch")]
    op_ = [r["old_pitch"] for r in out if r.get("old_pitch")]
    print("\n" + "=" * 96)
    print("新判据 行距值域：%.1f .. %.1f  极差 %.1f  (n=%d)"
          % (min(np_), max(np_), max(np_) - min(np_), len(np_)))
    print("旧判据 行距值域：%.1f .. %.1f  极差 %.1f  (n=%d)"
          % (min(op_), max(op_), max(op_) - min(op_), len(op_)))
    # 有多少帧拿到了 ≥3 个名字（可用于开机锚定）
    good = sum(1 for r in out if r["n_names"] >= 3)
    print("拿到 ≥3 个名字行的帧：%d/%d" % (good, len(out)))

    io.open(os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "t7b4-DPI无关口径-v2.json"),
            "w", encoding="utf-8").write(json.dumps({
                "_说明": [
                    "全相对结构的名字列/行高/行距判定（零绝对像素）。",
                    "对照旧的绝对判据 x∈[170,470) & h∈[19,22]。",
                ],
                "summary": {
                    "new_pitch_range": [min(np_), max(np_), len(np_)],
                    "old_pitch_range": [min(op_), max(op_), len(op_)],
                    "frames_with_ge3_names": good, "n_frames": len(out),
                }, "frames": out}, ensure_ascii=False, indent=1))
    print("\n落盘：报告/ocr瓶颈-20260930/t7b4-DPI无关口径-v2.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
