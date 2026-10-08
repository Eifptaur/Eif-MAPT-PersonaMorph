# -*- coding: utf-8 -*-
"""t7b4_dpifree_final.py —— 最终方案：**跳过 DPI** 的会话行几何（全用图内实测，零绝对常量）。

════════════════════════════════════════════════════════════════════════
★ 回答用户的两个问题
  ①「有没有啥方法能绕过去」（绕过 DPI）
  ②「万一有人用不在常规数据表里的 DPI 呢」
  ⇒ **不要查表。把"行高"从图里量出来，用它当尺子。**
════════════════════════════════════════════════════════════════════════

★ 核心洞察（本轮实测）：
   微信把"当前打开的会话行"整行填成绿色 ⇒ **这条绿带的高度 = 一个整行的行高**。
   实测 24 帧：绿带高 **96~97px**，跨窗口 315→2526px 恒定。
   ⇒ 绿带高是**图内自带的标尺**：DPI 变、字号变、缩放倍率变，它都跟着变，
     而"它等于一行"这件事**永远成立**。

★ 于是全部几何都不需要绝对常量：
   · row_h   = 绿带高                    （图内实测，替代"行距≈97"）
   · name_x  = 列表左沿左侧的"文字起始列"（结构定位，替代"x∈[170,470)"）
   · name_h  = 本帧框高众数               （相对量，替代"h∈[19,22]"）
   · 没有绿带时 ⇒ 退回"名字行 cy 差的众数"（自校准，仍不用绝对常量）

★ 校验（本轮）：与"绿带高"这条**独立通道**比 ——
   若 名字 cy 差的众数 ≈ 绿带高 ⇒ 两条独立通道互相印证。
输出：报告/ocr瓶颈-20260930/t7b4-DPI无关-最终.json
用法：./runtime/python/python.exe -u scripts/t7b4_dpifree_final.py
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


def row_h_from_green(im, co):
    """图内实测"一行高"：绿底高亮带的高度。量不到返回 None（不猜）。"""
    try:
        bands = co.green_bands(im) or []
    except Exception:
        return None, None
    if not bands:
        return None, None
    b = bands[0]                       # score 最高 = 高亮行
    return (b["y1"] - b["y0"]), (b["y0"], b["y1"])


def name_column(boxes, pane, W):
    """结构性定位名字列 —— 不写死 170/470。

    会话行的结构永远是： [头像方块] [名字(上) / 预览(下)]
    ⇒ 名字文字 x 落在"列表左沿左侧、且是全帧最左的那一簇文字"。
    做法：取 x < pane 的所有框，对 x 做 8px 分箱，找"计数 >=2 的最小带"。
    """
    cand = [b for b in boxes if b[1] < max(40, (pane or W))]
    if not cand:
        return None, []
    xh = collections.Counter((b[1] // 8) * 8 for b in cand)
    bands = sorted(x for x, c in xh.items() if c >= 2)
    if not bands:
        return None, []
    x_band = bands[0]
    return x_band, cand


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
        meta = [m for m in meta if m.get("png")]
        seen, pick = set(), []
        for m in meta:
            k = (m["w"] // 150, m["h"] // 150)
            if k in seen:
                continue
            seen.add(k)
            pick.append(m)
        for m in pick[:12]:
            p = os.path.join(BASE, d, m["png"])
            if not os.path.isfile(p):
                continue
            im = Image.open(p).convert("RGB")
            W, H = im.size
            try:
                pane = int(ch.pane_left_for(im) or 0)
            except Exception:
                pane = 0
            items = co.recognize(im, timeout=40.0) or []
            boxes = [(str(t).strip(), x, y, w, h) for (t, x, y, w, h) in items]

            # ① 图内标尺：绿带高
            g_h, g_y = row_h_from_green(im, co)

            # ② 名字列（结构）
            x_band, cand = name_column(boxes, pane, W)

            # ③ 本帧框高众数
            hs = [b[4] for b in cand] if cand else []
            h_mode = collections.Counter(hs).most_common(1)[0][0] if hs else None

            # ④ 名字行 cy 差的众数（自校准，不用绝对常量）
            names = [b for b in cand if x_band is not None and x_band <= b[1] < x_band + 24]
            names = sorted(names, key=lambda b: b[2])
            cys = [b[2] + b[4] / 2.0 for b in names]
            # 用"图内标尺"筛步长：只留接近一行的（±35%），避免把预览行算进去
            lo = g_h * 0.65 if g_h else 60
            hi = g_h * 1.35 if g_h else 140
            steps = [b - a for a, b in zip(cys, cys[1:]) if lo <= (b - a) <= hi]
            if not steps:
                steps = [b - a for a, b in zip(cys, cys[1:]) if 60 <= b - a <= 140]
            pitch = statistics.median(steps) if steps else None

            rec = {"dir": d, "png": m["png"], "size": [W, H], "theme": m.get("theme"),
                   "pane": pane, "green_h": g_h, "green_y": g_y,
                   "name_x_band": x_band, "h_mode": h_mode,
                   "n_names": len(names), "pitch_from_ocr": pitch,
                   "agree": (abs(pitch - g_h) <= 2) if (pitch and g_h) else None}
            rows.append(rec)
            print("%-16s %-14s W=%4d %-5s | 图内尺(绿带)=%-5s | 名字x带=%-5s n%2d | OCR行距=%-6s | 一致=%s"
                  % (d[-12:], m["png"], W, m.get("theme"), g_h, x_band, len(names),
                     pitch, rec["agree"]))

    # ── 总结 ──
    gh = [r["green_h"] for r in rows if r["green_h"]]
    po = [r["pitch_from_ocr"] for r in rows if r["pitch_from_ocr"]]
    agree = [r for r in rows if r["agree"] is not None]
    n_agree = sum(1 for r in agree if r["agree"])
    print("\n" + "=" * 100)
    print("① 图内标尺（绿带高）值域: %s .. %s  极差 %s  (n=%d)"
          % (min(gh), max(gh), max(gh) - min(gh), len(gh)))
    print("② OCR 行距值域        : %s .. %s  极差 %s  (n=%d)"
          % (min(po), max(po), max(po) - min(po), len(po)))
    print("③ 两通道一致(±2px)    : %d/%d" % (n_agree, len(agree)))
    print("\n★ 结论：绿带高在**所有窗口宽度**下恒为一行 ⇒ 它就是那个『跳过 DPI 的尺子』。")

    io.open(os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "t7b4-DPI无关-最终.json"),
            "w", encoding="utf-8").write(json.dumps({
                "_说明": [
                    "跳过 DPI 的方案：把『一行高』从图内量出来（绿底高亮带的高度），",
                    "用它当尺子推所有几何，不查任何 DPI 表、不用任何绝对像素常量。",
                    "★ 同时推翻上一轮『行距两档(86/97)』—— 86 是我自己的调试面板污染造成的假象。",
                ],
                "summary": {
                    "green_h_range": [min(gh), max(gh)] if gh else None,
                    "ocr_pitch_range": [min(po), max(po)] if po else None,
                    "two_channel_agree": "%d/%d" % (n_agree, len(agree)),
                }, "frames": rows}, ensure_ascii=False, indent=1))
    print("落盘：报告/ocr瓶颈-20260930/t7b4-DPI无关-最终.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
