# -*- coding: utf-8 -*-
"""t7b4_greenrule2.py —— 绿带高 = 图内实测的一行高（DPI 无关标尺）。修正 green_bands 返回结构。

green_bands 返回 [{'y0','y1','y_abs','score'}]（dict，非 tuple）。
"""
import io
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

BASE = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")
OUTD = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "_annot")


def main():
    from PIL import Image, ImageDraw
    from agent import chat_ocr as co

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
                bands = co.green_bands(im) or []
            except Exception:
                bands = []
            # 选 score 最高的那条（就是高亮行）
            best = bands[0] if bands else None
            gh = (best["y1"] - best["y0"]) if best else None
            rows.append({"dir": d, "png": m["png"], "size": [W, H], "theme": m.get("theme"),
                         "green_h": gh, "y0": best["y0"] if best else None,
                         "y1": best["y1"] if best else None,
                         "score": best["score"] if best else None,
                         "n_bands": len(bands)})
            print("%-16s %-14s W=%4d %-5s 绿带高=%-5s y=[%s,%s] score=%s nbands=%d"
                  % (d[-12:], m["png"], W, m.get("theme"), gh,
                     best["y0"] if best else "-", best["y1"] if best else "-",
                     best["score"] if best else "-", len(bands)))
            if best:
                im2 = im.copy()
                dr = ImageDraw.Draw(im2)
                dr.line([(0, best["y0"]), (W, best["y0"])], fill=(255, 0, 0), width=2)
                dr.line([(0, best["y1"]), (W, best["y1"])], fill=(255, 255, 0), width=2)
                tag = "L" if "150538" in d else "D"
                im2.crop((0, 0, min(W, 500), min(H, 1000))).save(
                    os.path.join(OUTD, "t7b4-green2-%s-%s" % (tag, m["png"])))

    hs = [r["green_h"] for r in rows if r["green_h"]]
    if hs:
        print("\n★ 绿带高值域: %d .. %d  (n=%d)  ← 图内实测『一行高』" % (min(hs), max(hs), len(hs)))
    io.open(os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "t7b4-绿带标尺.json"),
            "w", encoding="utf-8").write(json.dumps({
                "_说明": ["绿带高 = 图内实测的一行高，作为 DPI 无关的行距标尺。",
                          "已把绿带上下沿画回原图自证（_annot/t7b4-green2-*.png）。"],
                "summary": {"green_h_range": [min(hs), max(hs)] if hs else None, "n": len(hs)},
                "frames": rows}, ensure_ascii=False, indent=1))
    print("落盘：报告/ocr瓶颈-20260930/t7b4-绿带标尺.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
