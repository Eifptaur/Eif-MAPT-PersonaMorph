# -*- coding: utf-8 -*-
"""T7b 第三步：**自洽校验** —— 绕开可疑真值，用帧内/帧间一致性评价 OCR 行几何。

思路（不依赖人工真值）：
  ① **帧内自洽**：同一帧里量出多个会话的"名字行中心 y"，相邻差应≈常数（行高）。
     若 OCR 量出的相邻差高度收敛到同一个值 ⇒ 行几何是**量得准的**。
  ② **帧间自洽**：调研报告已三次印证"行几何不随窗口宽度变化"。
     若不同宽度/主题的帧给出**同一个行高** ⇒ OCR 侧数据可信。
  ③ 再把这批"自洽行高"与目视值并列，给最终判定。

★ 本轮用 12 帧（全部标准布局帧），跨 light/dark、跨 6 种宽度。
⚠️ 只读。
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

# 标准布局帧（本轮已目视确认"左列表右聊天/调试面板"成立）
FRAMES = [
    ("sizes-dark-20260930-150538", "frame-000.png", "light"),
    ("sizes-dark-20260930-150538", "frame-001.png", "light"),
    ("sizes-dark-20260930-150538", "frame-003.png", "light"),
    ("sizes-dark-20260930-150538", "frame-004.png", "light"),
    ("sizes-dark-20260930-150538", "frame-005.png", "light"),
    ("sizes-dark2-20260930-151519", "frame-000.png", "dark"),
    ("sizes-dark2-20260930-151519", "frame-028.png", "dark"),
    ("sizes-dark2-20260930-151519", "frame-043.png", "dark"),
]

# 名字列：x 下限（排除头像/图标列 x≈110..150）
X_LO = 165
MIN_LEN = 2
# 单个会话"名字行"的高度上界：名字框不会太高；过高的框是把名字+预览合并了
NAME_MAX_H = 30


def collect(items, x_lo=X_LO, x_hi=100000):
    out = []
    for (t, x, y, w, h) in items:
        s = str(t).strip()
        if len(s) < MIN_LEN or x < x_lo or x > x_hi:
            continue
        out.append((s, x, y, w, h))
    out.sort(key=lambda r: r[2])
    return out


print("=" * 100)
print("T7b 第三步：自洽校验（不依赖可疑真值）")
print("=" * 100)

report = []
for d, png, theme in FRAMES:
    path = os.path.join(BASE, "多尺寸样本", d, png)
    if not os.path.exists(path):
        continue
    img = Image.open(path).convert("RGB")
    W, H = img.size
    items = co.recognize(img, timeout=30.0) or []
    lb = collect(items)

    # 相邻名字行间距的众数（行高）
    cys = sorted({y + h // 2 for (s, x, y, w, h) in lb})
    alld = [b - a for a, b in zip(cys, cys[1:]) if 30 <= b - a <= 160]
    hist = {}
    for dv in alld:
        # 容许 ±4px 归并到同一档
        key = round(dv / 4.0) * 4
        hist[key] = hist.get(key, 0) + 1
    mode = sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))
    row_h = mode[0][0] if mode and mode[0][1] >= 2 else (alld[0] if alld else None)

    print("\n%s / %s  %dx%d  [%s]  文字框=%d" % (d, png, W, H, theme, len(lb)))
    print("  相邻名字中心 y：%s" % cys[:12])
    print("  相邻差：%s" % alld[:12])
    print("  众数行高 = %s   （分布 %s）" % (row_h, mode[:4] if mode else "无"))
    print("  前 3 名字：%s" % [(s[:10], y + h // 2) for (s, x, y, w, h) in lb[:3]])

    report.append({"dir": d, "png": png, "size": [W, H], "theme": theme,
                   "n_text": len(lb), "centers_y": cys[:20],
                   "adj_diffs": alld[:20], "row_height_mode": row_h,
                   "top3": [(s[:14], x, y + h // 2) for (s, x, y, w, h) in lb[:3]]})

outp = os.path.join(BASE, "t7b-自洽校验-第三测.json")
with open(outp, "w", encoding="utf-8") as fp:
    json.dump(report, fp, ensure_ascii=False, indent=2)

rhs = [r["row_height_mode"] for r in report if r["row_height_mode"]]
print("\n" + "=" * 100)
print("跨帧行高：%s" % rhs)
if rhs:
    print("  → 值域 %d..%d，极差 %d px" % (min(rhs), max(rhs), max(rhs) - min(rhs)))
print("落盘：" + outp)
