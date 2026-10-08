# -*- coding: utf-8 -*-
"""T7b 定稿脚本：会话条目几何量测（行距网格法）。

════════════════════════════════════════════════════════════════════════
一、结论（本轮实测，可复现）
────────────────────────────────────────────────────────────────────
会话条目在 OCR 框表里的形态（跨 light/dark、跨 1760/1886/2078 宽度**完全一致**）：

    名字行 cy=252   预览行 cy=282     ← 条目内 名字↔预览 间距 ≈30
    名字行 cy=349   预览行 cy=379
    名字行 cy=447   预览行 cy=478
    名字行 cy=546   预览行 cy=575
    名字行 cy=643   预览行 cy=672
    名字行 cy=738   预览行 cy=771
    ⇒ 条目行距（名字→名字）= 97 / 98 / 99 / 97 / 95

二、误差（对 ≤12px 验收口径）
────────────────────────────────────────────────────────────────────
    · 条目行距 97（人工真值 #6/#8） vs OCR 97~99  ⇒ **误差 ≤2px**  ✅
    · 名字↔预览间距 ≈30                                          ✅
    · OCR 文字框坐标经 2x 叠加图目视核对**逐框吻合**（`腾讯新闻` cy=154 等） ✅

三、必须留档的两处**真值缺陷**（不改真值，只登记）
────────────────────────────────────────────────────────────────────
    ① 浅色三帧 `first_row_y=372` **标错了行**：372 落在第 3 条 `流离：…` 的预览行；
       首行 `腾讯新闻` 真实 cy = **154**。
    ② 深色三帧 `pane_left_x=45` 与浅色三帧 `pane_left_x=407` **字段含义不一致**
       （45 落在最左工具栏图标列，407 才是会话列表右沿）。
    ⇒ 含义：真值文件需回炉统一字段定义后再作为 T7b 标尺。
       **本次以「OCR 自量 + 叠加图目视核对」互证为准，不单信真值。**

四、判据（可用代码）
────────────────────────────────────────────────────────────────────
    会话列表名字列：`170 <= x < 470`（头像/图标列 x≈110..150 被排除；
                                  右侧聊天区/调试面板 x>470 被排除）
    排除噪声：`len(text.strip()) <= 1`（单字符图标 `O`/`0`/`E`）
              注意⭐：**单字符会话名（如 `E`）会被一并排除** —— 这是已知代价，
              需在后续用"绿底高亮条"或"头像列"另行补回（见待办）。
    首行会话 = 名字列 cy 最小的框。
    条目行距 = 名字列相邻 cy 差中落在 [40,160] 的众数。

⚠️ 只读脚本：不点微信、不投递。
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

X_NAME_LO, X_NAME_HI = 170, 470

# ★ 名字行 vs 预览行的分界（本轮实测得出，比任何 x/w 判据都稳）：
#   名字行 h = 19~20（粗体大字号）    ← 见 t7b-定稿-条目几何.json 的逐框表
#   预览行 h = 16~18（小字号被压行高）
#   时间/角标 h = 11~13（单行小字）
#   ⇒ 取 `h >= NAME_MIN_H` 即得**每个会话的名字行**，其 cy 间距 = 条目行距。
NAME_MIN_H = 19
# ★ 上界也要卡：搜索框右侧「圆加号」图标被读成 `O` 时 h=26（比名字行还大）
#   ⇒ 名字行 h ∈ [19, 22] 才是真名字行。
NAME_MAX_H = 22


def session_boxes(items, x_lo=X_NAME_LO, x_hi=X_NAME_HI):
    out = []
    for (t, x, y, w, h) in items:
        s = str(t).strip()
        if not s or not (x_lo <= x < x_hi):
            continue
        if not (NAME_MIN_H <= h <= NAME_MAX_H):   # 只留名字行（粗体大字）
            continue
        out.append((s, x, y, w, h))
    out.sort(key=lambda r: (r[2], r[1]))
    return out


def geometry(boxes):
    g = {"first_name": None, "first_name_y": None, "entry_pitch": None,
         "pitch_samples": [], "n": len(boxes)}
    if not boxes:
        return g
    g["first_name"] = boxes[0][0]
    g["first_name_y"] = boxes[0][2] + boxes[0][4] // 2
    cys = [y + h // 2 for (s, x, y, w, h) in boxes]
    diffs = [b - a for a, b in zip(cys, cys[1:]) if 40 <= b - a <= 160]
    g["pitch_samples"] = diffs
    if diffs:
        hist = {}
        for dv in diffs:
            k = round(dv / 4.0) * 4
            hist[k] = hist.get(k, 0) + 1
        g["entry_pitch"] = sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return g


print("=" * 100)
print("T7b 定稿：会话条目几何（行距网格法）")
print("=" * 100)

rows = []
for d, png, theme in FRAMES:
    path = os.path.join(BASE, "多尺寸样本", d, png)
    if not os.path.exists(path):
        continue
    img = Image.open(path).convert("RGB")
    W, H = img.size
    items = co.recognize(img, timeout=30.0) or []
    bx = session_boxes(items)
    g = geometry(bx)
    print("\n%s / %s  %dx%d [%s]" % (d, png, W, H, theme))
    print("  首行名字='%s'  名字行 cy=%s" % (g["first_name"], g["first_name_y"]))
    print("  条目行距=%s  样本=%s" % (g["entry_pitch"], g["pitch_samples"][:8]))
    print("  条目表：%s" % [(s[:10], y + h // 2) for (s, x, y, w, h) in bx[:8]])
    rows.append({"dir": d, "png": png, "theme": theme, "size": [W, H], **g})

outp = os.path.join(BASE, "t7b-定稿-条目几何.json")
with open(outp, "w", encoding="utf-8") as fp:
    json.dump(rows, fp, ensure_ascii=False, indent=2)

pitch = [r["entry_pitch"] for r in rows if r["entry_pitch"]]
print("\n" + "=" * 100)
print("【汇总】条目行距：%s" % pitch)
if pitch:
    print("   值域 %d..%d  极差 %d px" % (min(pitch), max(pitch), max(pitch) - min(pitch)))
    print("   对真值 97 的误差：%s" % ["%+d" % (v - 97) for v in pitch])
    print("   ⇒ 全部落在 ≤12px 验收口径内？%s"
          % ("是 ✅" if all(abs(v - 97) <= 12 for v in pitch) else "否 ❌"))
print("落盘：" + outp)
