# -*- coding: utf-8 -*-
"""make_annotation_sheets.py —— 生成「带标尺网格」的标注辅助图（T7a 的输入）

⛔ 为什么这样做（避免循环论证）
  要量"wxocr 框坐标 ≤12px"，先要有**人工真值**。而真值若直接用我方脚本算，
  就等于"拿程序验程序" ⇒ 无效。⇒ 本脚本只做一件事：
  **把候选帧裁出来、叠上 50px 标尺网格与坐标数字**，让人（我）**肉眼读出**坐标。
  ⇒ 真值由人读，脚本不提供任何"猜测值"，从根上避免确认偏差。

输出：每 4 帧拼成一张 `sheet-NN.png`（含标尺），存到 `_annot/`。
用法：./runtime/python/python.exe -u scripts/make_annotation_sheets.py
"""
import glob
import io
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: 每帧裁哪一块（相对渲染区左上角、绝对像素；只取"顶部 + 左侧"这块 —— 
#: 它同时包含：面板左沿、会话列表首行、若干行、聊天区标题）
CROP = (0, 0, 560, 520)     # 只取"列表左侧 + 首行 + 3~4 行"（放大后才读得出网格）
GRID = 50
TILE_N = 3                       # 每张拼图放几帧（放大后每帧更占地方）
SCALE = 1.45                     # 放大倍数：让 50px 网格与数字可读


def main():
    from PIL import Image, ImageDraw
    base = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")
    out = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "_annot")
    os.makedirs(out, exist_ok=True)

    # 选帧：深浅各半、宽窄都要；每帧取一张，尺寸去重
    picked, seen = [], set()
    for d in sorted(glob.glob(os.path.join(base, "sizes-*"))):
        rows = [json.loads(l) for l in io.open(os.path.join(d, "samples.jsonl"), encoding="utf-8")]
        for r in rows:
            if not r.get("png"):
                continue
            p = os.path.join(d, r["png"])
            if not os.path.isfile(p):
                continue
            try:
                im = Image.open(p)
            except Exception:
                continue
            w, h = im.size
            if w < 900:                                    # 太窄的不好标
                continue
            key = (r.get("theme"), w // 400)               # 主题 + 粗粒度宽度
            if key in seen:
                continue
            seen.add(key)
            picked.append({"path": p, "w": w, "h": h, "theme": r.get("theme"),
                           "dir": os.path.basename(d), "png": r["png"]})
    # 截断到 12 帧，深浅尽量均衡
    light = [x for x in picked if x["theme"] == "light"][:6]
    dark = [x for x in picked if x["theme"] == "dark"][:6]
    picked = light + dark
    print("选中 %d 帧（浅 %d / 深 %d）" % (len(picked), len(light), len(dark)))

    sheets = []
    for si in range(0, len(picked), TILE_N):
        group = picked[si:si + TILE_N]
        tiles = []
        for g in group:
            im = Image.open(g["path"]).convert("RGB").crop(CROP)
            im = im.resize((int(im.width * SCALE), int(im.height * SCALE)), Image.LANCZOS)
            d = ImageDraw.Draw(im)
            w, h = im.size
            for x in range(0, w, GRID):
                d.line([(x, 0), (x, h)], fill=(255, 0, 0) if x % 200 == 0 else (255, 160, 160), width=1)
                if x % 200 == 0:
                    d.text((x + 2, 2), str(x), fill=(255, 0, 0))
            for y in range(0, h, GRID):
                d.line([(0, y), (w, y)], fill=(0, 120, 255) if y % 200 == 0 else (170, 200, 255), width=1)
                if y % 200 == 0:
                    d.text((2, y + 2), str(y), fill=(0, 90, 255))
            tiles.append((g, im))
        sw = max(t[1].width for t in tiles)
        sh = sum(t[1].height + 26 for t in tiles)
        sheet = Image.new("RGB", (sw, sh), (255, 255, 255))
        dd = ImageDraw.Draw(sheet)
        yy = 0
        for gi, (g, im) in enumerate(tiles):
            _label = "【#%d】%s   %dx%d   theme=%s" % (
                si + gi, g["png"], g["w"], g["h"], g["theme"])
            # 大字标签 + 底色条，确保"是哪一帧"一眼可见（小字会被缩放糊掉）
            dd.rectangle([0, yy, sheet.width, yy + 34], fill=(255, 244, 180))
            dd.text((8, yy + 9), _label, fill=(0, 0, 0))
            yy += 34
            sheet.paste(im, (0, yy))
            yy += im.height
        f = os.path.join(out, "sheet-%02d.png" % (si // TILE_N))
        sheet.save(f)
        sheets.append(f)
        print("  已存 %s  (%d 帧 / %dx%d)" % (os.path.basename(f), len(tiles), sheet.width, sheet.height))
        # ★ 权威对应关系：显式打印"这张 sheet 里从上到下是哪几帧"（不靠人眼看小字）
        for gi, (g, _im) in enumerate(tiles):
            print("        #%-3d %-14s %dx%d  theme=%-5s  %s" % (
                si + gi, g["png"], g["w"], g["h"], g.get("theme"), g["dir"]))

    io.open(os.path.join(out, "picked.json"), "w", encoding="utf-8").write(
        json.dumps([{k: v for k, v in g.items() if k != "path"} for g in picked],
                   ensure_ascii=False, indent=1))
    print("\n拼图：")
    for f in sheets:
        print("  " + f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
