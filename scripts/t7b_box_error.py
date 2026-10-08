# -*- coding: utf-8 -*-
"""T7b：**量框坐标误差** —— 用现成 OCR 引擎对已标注帧跑，比"框中心 vs 人工真值"。

★ 关键更正（本轮实测发现）：
    PLAN 原写"装 wx-ocr 封装再量"。**实际不需要** ——
    项目 `agent/chat_ocr.py::recognize()` 的契约**本来就是 `[(text, x, y, w, h)]`**（带框）。
    且 `recognize_boost` 已有缩放档、`recognize_dual` 有双引擎。
    ⇒ T7b 可直接用现成链，**零新增依赖**。

真值来源：`报告/ocr瓶颈-20260930/_annot/标注真值.json`（9 帧，人眼看图填写）

量的三样（对应验收口径 ≤12px）：
  ① `pane_left_x`  —— 会话列表右沿/面板左沿。**怎么从 OCR 结果里取**：取列表区文字框的**右沿最大值**？
     ⚠️ 不同口径会差很多 ⇒ 本脚本**同时打印多种取法**，先看数据再定口径。
  ② `first_row_y`  —— 第一行文字中心 y。取法：列表区（x < pane_left）内**最靠上**的文字框中心 y。
  ③ `row_height`   —— 相邻两行中心 y 间距。

⚠️ 本脚本**只读**，不点微信、不投递。
"""
import sys
import os
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import Image
from agent import chat_ocr as co

ANNOT = os.path.join(ROOT, "报告", "ocr瓶颈-20260930", "_annot", "标注真值.json")
OUT = os.path.join(ROOT, "报告", "ocr瓶颈-20260930")
D = json.load(open(ANNOT, encoding="utf-8"))

# 帧的目录映射（真值里 dir 字段）
BASE = os.path.join(ROOT, "报告", "ocr瓶颈-20260930")

print("=" * 78)
print("T7b：框坐标误差实测（用现成 OCR 链，零新增依赖）")
print("=" * 78)
print("引擎自检：", co.health().get("last_why") or "正常")

rows = []
for f in D.get("frames", []):
    png = f.get("png")
    d = f.get("dir")
    truth_pl = f.get("pane_left_x")
    truth_fy = f.get("first_row_y")
    truth_rh = f.get("row_height")
    if not (truth_fy or truth_pl):
        continue
    # 找文件
    cand = []
    if d:
        cand.append(os.path.join(BASE, "多尺寸样本", d, png))
    cand += [
        os.path.join(BASE, "多尺寸样本", "sizes-dark-20260930-150538", png),
        os.path.join(BASE, "多尺寸样本", "sizes-dark2-20260930-151519", png),
    ]
    path = next((p for p in cand if os.path.exists(p)), None)
    if not path:
        print("[skip] 找不到 %s/%s" % (d, png))
        continue

    img = Image.open(path).convert("RGB")
    W, H = img.size
    items = co.recognize(img, timeout=25.0) or []
    n = len(items)

    # 列表区：真值 pane_left 作为界（回归测试时用它当"已知左沿"是合理的）
    pl = truth_pl if truth_pl else int(W * 0.35)
    lst = [(t, x, y, w, h) for (t, x, y, w, h) in items if (x + w) <= pl + 20]
    lst.sort(key=lambda it: it[2])

    got_fy = None
    got_rh = None
    got_pl = None
    if lst:
        got_fy = lst[0][2] + lst[0][4] // 2
        if len(lst) >= 2:
            got_rh = (lst[1][2] + lst[1][4] // 2) - got_fy
        # 取法A：列表区文字框右沿最大值
        got_pl = max(x + w for (t, x, y, w, h) in lst)

    def err(a, b):
        return "-" if (a is None or b is None) else "%+d" % (a - b)

    print("\n--- %s (%dx%d, %s)  OCR 框数=%d  列表区文字=%d" % (png, W, H, f.get("theme"), n, len(lst)))
    print("    pane_left  真值=%-6s 实测=%-6s 误差=%s" % (truth_pl, got_pl, err(got_pl, truth_pl)))
    print("    first_row_y 真值=%-6s 实测=%-6s 误差=%s" % (truth_fy, got_fy, err(got_fy, truth_fy)))
    print("    row_height  真值=%-6s 实测=%-6s 误差=%s" % (truth_rh, got_rh, err(got_rh, truth_rh)))
    if lst:
        print("    列表区前 3 行文字：%s" % [(str(t)[:8], x, y) for (t, x, y, w, h) in lst[:3]])

    rows.append({"png": png, "size": [W, H], "theme": f.get("theme"),
                 "n_boxes": n, "n_list": len(lst),
                 "pane_left": {"truth": truth_pl, "got": got_pl},
                 "first_row_y": {"truth": truth_fy, "got": got_fy},
                 "row_height": {"truth": truth_rh, "got": got_rh}})

with open(os.path.join(OUT, "t7b-框误差-首测.json"), "w", encoding="utf-8") as fp:
    json.dump(rows, fp, ensure_ascii=False, indent=2)

print("\n" + "=" * 78)
ok = [r for r in rows if r["first_row_y"]["truth"] and r["first_row_y"]["got"] is not None]
if ok:
    es = [abs(r["first_row_y"]["got"] - r["first_row_y"]["truth"]) for r in ok]
    print("first_row_y 误差（绝对值）：%s  ⇒ 最大 %dpx  均值 %.1fpx  （口径 ≤12px）"
          % (es, max(es), sum(es) / len(es)))
    print("  ≤12px 命中：%d / %d" % (sum(1 for e in es if e <= 12), len(es)))
else:
    print("⚠️ 没有可比对的样本（OCR 没读到列表区文字）")

print("\n落盘：", os.path.join(OUT, "t7b-框误差-首测.json"))
