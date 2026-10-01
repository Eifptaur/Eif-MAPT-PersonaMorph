# -*- coding: utf-8 -*-
"""probe_row_height_avatar.py —— 用「头像列」测会话列表行高（**完全不依赖 OCR**）

⛔ 为什么要换数据源（2026-09-30 连续两轮失败换来的）
  行高这条锚本身是对的（用户实机验证：行高恒定、窗口矮时只裁剪不压缩；图核对 ≈95~100）。
  但我前两版都用 **`session_rows` 的 `y_abs`** 去算：
    · 第一版「相邻间距众数」⇒ 被"名字↔预览"的**半行距**污染（实测冒出 48/78）；
    · 第二版加"滤半行 + 区间校验" ⇒ 仍有 80/85 的偏低值，且**约一半帧测不出**。
  ⇒ 结论：**问题不在统计量，在数据源** —— `session_rows` 的 y 混着「会话行/名字/预览」三种。

★本脚本的数据源：**头像列**（纯像素，不经过 OCR）
  会话列表每一行**最左都有一个头像方块**（位置与尺寸恒定）⇒
  **头像块的垂直间距 = 行高**，而且它**不受 OCR 漏读/错读影响**。

  算法：
    1. 定 x 扫描带 = `[列表左沿 + pad, 列表左沿 + pad + 头像宽]`
       （列表左沿优先取**绿底左端**——绿底是整行底色，从列表左沿铺起；取不到则用比例兜底）；
    2. 在该带里统计**整带出现最多的颜色** = 列表底色（背景）；
    3. 逐行判"这一行在带内是否有头像"：与底色差异明显的像素占比 ≥ 阈值；
    4. 把连续"有头像"的行并成块 ⇒ 取块的**中心 y**；
    5. 相邻中心间距的**众数** = `h_row`（并做 [80,130] 区间校验，超出给 0）。

用法：./runtime/python/python.exe -u scripts/probe_row_height_avatar.py
"""
import glob
import json
import os
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

H_LO, H_HI = 80, 130


def detect_row_height_by_avatar(img, x_left=None, pad=8, avatar_w=52,
                                ink_thr=60, ink_ratio=0.35, min_block=8):
    """用「头像列」测行高（px）；测不出返回 0。**不依赖 OCR**。"""
    try:
        w, h = img.size
        px = img.load()
        # ① 列表左沿：优先用绿底左端（绿底从列表左沿铺起）
        if not x_left:
            from . import chat_header as _ch  # noqa
            # 绿底左端：复用 detect_green_right 的"最长连续绿段"思路太重 ⇒ 这里用轻量版
            x_left = 0
            try:
                from agent import chat_header as _ch
                _gx = _ch.detect_green_right(img)
                if _gx:
                    x_left = max(0, int(_gx * 0.06))     # 绿底左端 ≈ 其右端的 6%（实测 30/443）
            except Exception:
                x_left = 0
            if not x_left:
                x_left = max(0, int(w * 0.03))
        x0 = min(w - 1, int(x_left) + pad)
        x1 = min(w, x0 + avatar_w)
        if x1 - x0 < 12:
            return 0
        xs = list(range(x0, x1, max(1, (x1 - x0) // 20)))
        y_from, y_to = int(h * 0.08), h          # 跳过最顶部（搜索栏/标题带）

        # ② 列表底色 = 该带里出现最多的颜色（量化到 16 级）
        c = Counter()
        for y in range(y_from, y_to, 3):
            for x in xs:
                r, g, b = px[x, y][:3]
                c[(r // 16 * 16, g // 16 * 16, b // 16 * 16)] += 1
        if not c:
            return 0
        bg = c.most_common(1)[0][0]

        # ③ 逐行判"有没有头像"
        rows = []
        for y in range(y_from, y_to):
            hit = 0
            for x in xs:
                r, g, b = px[x, y][:3]
                if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > ink_thr:
                    hit += 1
            rows.append((y, hit / float(len(xs))))

        # ④ 并块 ⇒ 取中心
        centers, run, start = [], 0, None
        for y, v in rows:
            if v >= ink_ratio:
                if run == 0:
                    start = y
                run += 1
            else:
                if run >= min_block:
                    centers.append((start + y - 1) // 2)
                run, start = 0, None
        if run >= min_block:
            centers.append((start + rows[-1][0]) // 2)
        if len(centers) < 3:
            return 0
        gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
        gaps = [d for d in gaps if 30 < d < 200]
        if len(gaps) < 2:
            return 0
        h_row = Counter(gaps).most_common(1)[0][0]
        return int(h_row) if (H_LO <= h_row <= H_HI) else 0
    except Exception:
        return 0


def main():
    from PIL import Image
    _base = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")
    _dirs = sorted(d for d in glob.glob(os.path.join(_base, "sizes-*")) if os.path.isdir(d))
    res = []
    for _d in _dirs:
        raw = [json.loads(l) for l in open(os.path.join(_d, "samples.jsonl"), encoding="utf-8")]
        for r in raw:
            if not r.get("png"):
                continue
            p = os.path.join(_d, r["png"])
            if not os.path.isfile(p):
                continue
            try:
                img = Image.open(p).convert("RGB")
            except Exception:
                continue
            res.append((img.size, r.get("theme"), detect_row_height_by_avatar(img)))
    print("=" * 74)
    print("行高（**头像列**版本 · 不依赖 OCR）· 共 %d 帧" % len(res))
    print("=" * 74)
    ok = [x[2] for x in res if x[2]]
    print("行高分布：%s" % dict(sorted(Counter(ok).items())))
    print("测不出（0）的帧：%d / %d（%.0f%%）"
          % (len(res) - len(ok), len(res), 100.0 * (len(res) - len(ok)) / max(1, len(res))))
    if ok:
        ok.sort()
        print("⇒ 中位 %d · 均值 %.1f · 区间 [%d, %d]"
              % (ok[len(ok) // 2], sum(ok) / float(len(ok)), ok[0], ok[-1]))
    print()
    seen = {}
    for sz, th, hh in res:
        seen.setdefault("%dx%d" % sz, hh)
    print("按尺寸抽样：")
    for k, v in sorted(seen.items()):
        print("  %-11s ⇒ %s" % (k, v or "测不出"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
