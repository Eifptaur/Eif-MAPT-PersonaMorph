# -*- coding: utf-8 -*-
"""t7b4_greenrule.py —— 用【绿带高度】当"行距标尺"（DPI 无关）。

★ 想法：微信把"当前会话行"整个填成绿色。**这条绿带的高度 = 一个整行的行高**，
  它是**图内实测出来的**，DPI 怎么变它都跟着变 ⇒ 天然是"跳过 DPI"的标尺。
  ⇒ 用它作 `row_h`，再用 `row_h` 去推其它几何，就无需任何绝对像素常量。

★ 自证：把绿带上下沿画回原图，人眼确认贴住高亮行。
用法：./runtime/python/python.exe -u scripts/t7b4_greenrule.py
"""
import io
import json
import os
import sys
import logging

log = logging.getLogger("persona-morph")

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
        for m in pick[:10]:
            p = os.path.join(BASE, d, m["png"])
            if not os.path.isfile(p):
                continue
            im = Image.open(p).convert("RGB")
            W, H = im.size
            try:
                bands = co.green_bands(im) or []
            except Exception:
                bands = []
            # 取高度最大的那条（就是高亮行）
            bh = bw = None
            if bands:
                try:
                    cand = [(int(b[1]) - int(b[0]), int(b[0]), int(b[1])) for b in bands]
                    cand = [c for c in cand if c[0] > 20]
                    if cand:
                        bh, y0, y1 = max(cand)
                        bh = bh
                        bw = (y0, y1)
                except Exception as _e:
                    log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                    pass
            # 用绿带的 x 左端当"列表内文字起点"参照（结构锚）
            try:
                gx = co._green_x(im)
            except Exception:
                gx = None
            rows.append({"dir": d, "png": m["png"], "size": [W, H], "theme": m.get("theme"),
                         "green_h": bh, "green_y": bw, "green_x": gx})
            print("%-18s %-14s W=%4d %-5s 绿带高=%-5s y=%-12s 绿x=%s"
                  % (d[-14:], m["png"], W, m.get("theme"), bh, bw, gx))
            if bw:
                im2 = im.copy()
                dr = ImageDraw.Draw(im2)
                dr.line([(0, bw[0]), (W, bw[0])], fill=(255, 0, 0), width=2)
                dr.line([(0, bw[1]), (W, bw[1])], fill=(255, 255, 0), width=2)
                tag = "L" if "150538" in d else "D"
                im2.crop((0, 0, min(W, 500), min(H, 1000))).save(
                    os.path.join(OUTD, "t7b4-green-%s-%s" % (tag, m["png"])))

    hs = [r["green_h"] for r in rows if r["green_h"]]
    if hs:
        print("\n绿带高值域: %d .. %d  (n=%d)  ← 这就是图内实测的『一行高』" % (min(hs), max(hs), len(hs)))
    io.open(os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "t7b4-绿带标尺.json"),
            "w", encoding="utf-8").write(json.dumps({
                "_说明": ["绿带高 = 图内实测的一行高，作为 DPI 无关的行距标尺。",
                          "把绿带上下沿画回原图自证（_annot/t7b4-green-*.png）。"],
                "summary": {"green_h_range": [min(hs), max(hs)] if hs else None, "n": len(hs)},
                "frames": rows}, ensure_ascii=False, indent=1))
    print("落盘：报告/ocr瓶颈-20260930/t7b4-绿带标尺.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
