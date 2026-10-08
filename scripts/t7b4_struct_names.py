# -*- coding: utf-8 -*-
"""t7b4_struct_names.py —— 用【结构性判据】取代绝对像素窗口，找回会话名单行。

★ 背景（本轮新发现，推翻上一轮的"行距两档"结论）：
  上一轮报"行距两档 86/97"，把 86 档当成真的第二档。
  本轮转储原始框后发现：**86 档是"调试面板文字污染 x∈[170,470) 窗口"造成的假象** ——
    frame-033/040 里 x=453~480 的调试面板文字（h=20~22、行距 86）被当成了"名单行"，
    而真正的会话名单行（x=177）**一直落在同一张 95~98 的网格上**。

★ 所以正确答案不是"检测两档"，而是**换判决书**：
  用"这一行里它是不是**最靠左的文字**"来认名字 —— 即**相对结构**，不是绝对 x 区间。
  头像在最左（是个图形/方框，OCR 常读不出或读成 0/口），名字文字排在头像右侧，
  且**名字左侧还有一个更靠左的图元**。用"本行内 x 最小"或"x 落在列表左沿附近的一个
  相对带里"来选，就与 DPI/缩放/窗口宽全部无关。

本脚本对比三种判据在 28 帧上的**行距值域**与**命中率**：
  J0 旧：170 <= x < 470 且 19<=h<=22           （绝对区间）
  J1 结构A：x 是本帧所有框里"落在左半区且成簇"的那一簇的众数带
  J2 结构B：x 与**列表左沿(pane_left)** 的相对关系 —— 名字在 pane_left 左侧的近邻带
输出：报告/ocr瓶颈-20260930/t7b4-结构判据-对比.json
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


def load_frames(limit_sets=2, per_set=14):
    sets = []
    for d in sorted(os.listdir(BASE)):
        j = os.path.join(BASE, d, "samples.jsonl")
        if os.path.isfile(j):
            sets.append((d, j))
    picked = []
    for d, j in sets[:limit_sets]:
        meta = [json.loads(l) for l in io.open(j, encoding="utf-8")]
        meta = [m for m in meta if m.get("png")]
        seen = set()
        for m in meta:
            k = (m["w"] // 150, m["h"] // 150)
            if k in seen:
                continue
            seen.add(k)
            picked.append((d, m))
        # 每集限 14
        base = [p for p in picked]
    return sets, picked


def main():
    from PIL import Image
    from agent import chat_ocr as co
    from agent import chat_header as ch

    sets, _ = load_frames()

    frames = []
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
                pane = None
            items = co.recognize(im, timeout=40.0) or []
            boxes = [(str(t).strip(), x, y, w, h) for (t, x, y, w, h) in items]
            frames.append({"dir": d, "png": m["png"], "W": W, "H": H,
                           "dpi": m.get("dpi"), "pane": pane, "boxes": boxes})
            print("读帧 %-14s %5dx%-5d pane=%-5s 框%3d" % (m["png"], W, H, pane, len(boxes)))

    def pitch_from(cys):
        st = [b - a for a, b in zip(cys, cys[1:]) if 70 <= b - a <= 130]
        return st

    out = []
    for f in frames:
        boxes, W, H, pane = f["boxes"], f["W"], f["H"], f["pane"]

        # ── J0 旧判据 ──
        j0 = [b for b in boxes if 170 <= b[1] < 470 and 19 <= b[4] <= 22]
        j0_cy = sorted(b[2] + b[4] // 2 for b in j0)
        j0_st = pitch_from(j0_cy)

        # ── J1 结构A：名字列 x 的"众数带" ──
        #     取所有 h∈[16,24] 的框，按 x 做 24px 分箱取最左的密集带
        cand = [b for b in boxes if 16 <= b[4] <= 24 and b[1] < W * 0.45]
        hist = collections.Counter((b[1] // 24) * 24 for b in cand)
        if hist:
            band_x = hist.most_common(1)[0][0]
            j1 = [b for b in cand if band_x <= b[1] < band_x + 48]
        else:
            band_x, j1 = None, []
        j1_cy = sorted(b[2] + b[4] // 2 for b in j1)
        j1_st = pitch_from(j1_cy)

        # ── J2 结构B：名字在"列表左沿"附近的相对带 ──
        #     经验：名字 x 恒在 pane_left 的左侧一个相对距离内；用左右两个锚定
        if pane:
            j2 = [b for b in boxes if b[1] < pane and 16 <= b[4] <= 24
                  and b[1] > W * 0.02]
        else:
            j2 = []
        j2_cy = sorted(b[2] + b[4] // 2 for b in j2)
        j2_st = pitch_from(j2_cy)

        rec = {"dir": f["dir"], "png": f["png"], "size": [W, H], "dpi": f["dpi"],
               "pane": pane,
               "J0": {"n": len(j0), "steps": j0_st[:10],
                      "med": statistics.median(j0_st) if j0_st else None},
               "J1": {"band_x": band_x, "n": len(j1), "steps": j1_st[:10],
                      "med": statistics.median(j1_st) if j1_st else None},
               "J2": {"n": len(j2), "steps": j2_st[:10],
                      "med": statistics.median(j2_st) if j2_st else None}}
        out.append(rec)
        print("  %-14s W=%5d pane=%-5s | J0 n%2d med%-6s | J1 band=%-5s n%2d med%-6s | J2 n%2d med%-6s"
              % (f["png"], W, pane,
                 rec["J0"]["n"], rec["J0"]["med"],
                 band_x, rec["J1"]["n"], rec["J1"]["med"],
                 rec["J2"]["n"], rec["J2"]["med"]))

    def rng(key):
        vs = [r[key]["med"] for r in out if r[key]["med"]]
        return (min(vs), max(vs), len(vs)) if vs else (None, None, 0)

    print("\n" + "=" * 92)
    for k in ("J0", "J1", "J2"):
        lo, hi, n = rng(k)
        print("%s 行距中位值域: %s .. %s   (n=%d)" % (k, lo, hi, n))

    io.open(os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "t7b4-结构判据-对比.json"),
            "w", encoding="utf-8").write(
        json.dumps({"_说明": ["结构性判据 vs 绝对像素窗口，在 28 帧上的行距值域对比。",
                            "★ 发现：J0 的 86 档是调试面板污染；J1/J2 应把值域收拢到 95~98。"],
                    "frames": out}, ensure_ascii=False, indent=1))
    print("\n落盘：报告/ocr瓶颈-20260930/t7b4-结构判据-对比.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
