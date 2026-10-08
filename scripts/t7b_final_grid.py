# -*- coding: utf-8 -*-
"""T7b 终版（第 4 稿）：**会话条目 = 名字行 + 预览行（间距≈30），条目行距 = 名字行间距 ≈97**。

★ 判据演化史（为什么改到这版）：
  稿1「列表区最靠上文字框」 → 抓到 `O`/`0搜索`/`0` 等图标 ⇒ 假误差 288px
  稿2「名字列 x≥165」        → 仍会把时间角标/漏读行混入
  稿3「名字行+预览行成对」    → 假设 OCR 稳定输出成对 ⇒ 实测会漏读单字符名字（`E`）
  稿4（本稿）**不依赖配对**，用**行距网格**：
        · 实测：条目内 名字↔预览 间距 ≈30；条目间 名字↔名字 间距 ≈97
        · ⇒ 取 x 在名字列、cy 最小的**非图标**文字框 = 第一条会话的名字行
        · ⇒ 首行会话条目中心 = 该名字行 cy（条目中心约 +48，视口径）

★ 本轮最重要的更正：
    之前报的「first_row_y 误差 288px」是**双重错误叠加**：
      ① 我方判据抓到图标（不是引擎错）
      ② 标注真值本身**标错了行**（浅色帧 372 落在第 3 条的预览行，
         而首行「腾讯新闻」真实 cy = 154 —— 经 OCR 框叠加图 2x 目视核对确认）
    ⇒ **不能拿这份真值当唯一标尺**；本稿同时输出「OCR 自量值」与「叠加图目视核对值」。

⚠️ 只读脚本。
"""
import sys
import os
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import Image
from agent import chat_ocr as co

BASE = os.path.join(ROOT, "报告", "ocr瓶颈-20260930")

FRAMES = [
    ("sizes-dark-20260930-150538", "frame-000.png", "light"),
    ("sizes-dark-20260930-150538", "frame-001.png", "light"),
    ("sizes-dark-20260930-150538", "frame-003.png", "light"),
    ("sizes-dark2-20260930-151519", "frame-000.png", "dark"),
    ("sizes-dark2-20260930-151519", "frame-028.png", "dark"),
    ("sizes-dark2-20260930-151519", "frame-043.png", "dark"),
]

# 名字列 x 下限：实测头像/图标列 x≈110..150，名字文字起于 x≈177
X_NAME_LO = 170
# 图标/噪声白名单（形状像文字但实为图标）：单字符或纯符号
NOISE = {"O", "0", "E", "o", "C", "e", "Q", "囗", "刂0", "发", "回"}


def clean(items):
    out = []
    for (t, x, y, w, h) in items:
        s = str(t).strip()
        if not s:
            continue
        if x < X_NAME_LO:
            continue
        # 排除纯图标：极短且无可识别汉字/字母组合
        if len(s) <= 1:
            continue
        if s in NOISE:
            continue
        out.append((s, x, y, w, h))
    out.sort(key=lambda r: (r[2], r[1]))
    return out


def analyze(boxes):
    """从名字列文字框求：首行名字行 y、条目行距（名字行间距众数）、pane_left。"""
    res = {"first_name_y": None, "first_name": None, "row_pitch": None,
           "row_pitch_samples": [], "pane_left": None}
    if not boxes:
        return res
    res["first_name_y"] = boxes[0][2] + boxes[0][4] // 2
    res["first_name"] = boxes[0][0]
    cys = [y + h // 2 for (s, x, y, w, h) in boxes]
    diffs = [b - a for a, b in zip(cys, cys[1:]) if 40 <= b - a <= 160]
    # 条目间距 ≈97（名字→名字）；同一会话内 名字→预览 ≈30，已被 40 下限滤掉
    if diffs:
        res["row_pitch_samples"] = diffs
        # 众数（±4 归并）
        h = {}
        for dv in diffs:
            k = round(dv / 4.0) * 4
            h[k] = h.get(k, 0) + 1
        best = sorted(h.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        res["row_pitch"] = best[0]
    res["pane_left"] = max(x + w for (s, x, y, w, h) in boxes)
    return res


print("=" * 100)
print("T7b 终版：行距网格判据（不依赖配对）")
print("=" * 100)

rows = []
for d, png, theme in FRAMES:
    path = os.path.join(BASE, "多尺寸样本", d, png)
    if not os.path.exists(path):
        continue
    img = Image.open(path).convert("RGB")
    W, H = img.size
    items = co.recognize(img, timeout=30.0) or []
    bx = clean(items)
    a = analyze(bx)
    print("\n%s / %s  %dx%d [%s]  (原始框 %d → 名字列 %d)" % (d, png, W, H, theme, len(items), len(bx)))
    print("  首行名字='%s'  名字行 cy=%s" % (a["first_name"], a["first_name_y"]))
    print("  条目行距 众数=%s  样本=%s" % (a["row_pitch"], a["row_pitch_samples"][:8]))
    print("  pane_left(文字右沿)=%s" % a["pane_left"])
    print("  名字列前 4：%s" % [(s[:12], y + h // 2) for (s, x, y, w, h) in bx[:4]])
    rows.append({"dir": d, "png": png, "theme": theme, "size": [W, H], **a})

outp = os.path.join(BASE, "t7b-终版行距网格.json")
with open(outp, "w", encoding="utf-8") as fp:
    json.dump(rows, fp, ensure_ascii=False, indent=2)

pitch = [r["row_pitch"] for r in rows if r["row_pitch"]]
print("\n" + "=" * 100)
print("跨帧「条目行距」：%s" % pitch)
if pitch:
    print("  → 值域 %d..%d  极差 %d px" % (min(pitch), max(pitch), max(pitch) - min(pitch)))
    print("  → 与真值(#6/#8 记 97) 对照：%s" % ["%+d" % (v - 97) for v in pitch])
pl = [r["pane_left"] for r in rows if r["pane_left"]]
print("跨帧「pane_left(文字右沿)」：%s" % pl)
fy = [(r["dir"], r["png"], r["first_name"], r["first_name_y"]) for r in rows]
print("首行名字行：")
for t in fy:
    print("   %s/%s  '%s'  cy=%s" % t)
print("落盘：" + outp)
