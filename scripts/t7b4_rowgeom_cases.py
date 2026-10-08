# -*- coding: utf-8 -*-
"""精确核查 3 个"未命中绿带"的帧 —— 用 samples.jsonl 里登记的精确帧，不靠同名猜。"""
import io, json, os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
BASE = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")

TARGETS = [("sizes-dark-20260930-150538", "frame-009.png"),
           ("sizes-dark2-20260930-151519", "frame-031.png"),
           ("sizes-dark2-20260930-151519", "frame-035.png")]


def main():
    from PIL import Image
    from agent import row_geometry as rg
    from agent import chat_ocr as co

    for d, png in TARGETS:
        j = os.path.join(BASE, d, "samples.jsonl")
        meta = [json.loads(l) for l in io.open(j, encoding="utf-8")]
        rec = [m for m in meta if m.get("png") == png]
        p = os.path.join(BASE, d, png)
        if not os.path.isfile(p):
            print("!! 缺文件:", p); continue
        im = Image.open(p).convert("RGB")
        print("\n" + "=" * 92)
        print("帧: %s / %s  实际尺寸=%s  元数据=%s"
              % (d[-10:], png, im.size,
                 {k: rec[0].get(k) for k in ("w", "h", "theme", "pane_left", "hl_name")} if rec else "未登记"))

        bands = co.green_bands(im)
        print("  green_bands() -> %d 条: %s" % (len(bands), bands[:3]))
        try:
            x0, x1 = co._green_x(im)
            print("  _green_x 取样窗=(%s,%s) 宽=%s  (要求 >=40 才扫)" % (x0, x1, x1 - x0))
        except Exception as e:
            print("  _green_x 抛错:", e)

        # 逐行扫绿占比（直接量：这帧到底有没有"整行绿"）
        try:
            rgb = im.convert("RGB").load()
            W, H = im.size
            best = []
            for y in range(0, H, 4):
                hit = sum(1 for x in range(0, W, max(1, W // 120))
                          if co._is_green(*rgb[x, y][:3]))
                n = len(range(0, W, max(1, W // 120)))
                best.append((hit / float(n), y))
            best.sort(reverse=True)
            print("  全宽逐行绿占比 Top5: %s"
                  % [(round(r, 3), y) for r, y in best[:5]])
        except Exception as e:
            print("  逐行扫绿抛错:", e)

        g = rg.measure(im)
        print("  rg.measure => row_h=%s src=%s pane=%s n_names=%s pitch=%s note=%s"
              % (g.row_h, g.source, g.pane, g.n_names, g.pitch_ocr, g.note))
    return 0


if __name__ == "__main__":
    sys.exit(main())
