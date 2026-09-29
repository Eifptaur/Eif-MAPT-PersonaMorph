# -*- coding: utf-8 -*-
"""图标指纹度量（协议修正版）：**非重叠位置**当"不同目标"，类内给 ±0~3px 抖动。

## 上一版协议的两处错（别再犯）
① 采样步长 64px、裁剪 48px ⇒ **相邻裁剪重叠 32px** ⇒ "不同位置"其实大半是同一块内容
   ⇒ 类间最小距离被拉到 0、误配对 13 万，全是假象；
② 把 dHash（汉明位数 0~64）与灰度相关（0~100）放进同一列比"类内最大/类间最小"⇒ 量纲不同，没法比。
⇒ 本版：位置间距 ≥ 96px（不重叠）当"不同目标"；每个特征**各自**报"类内最大 / 类间最小 / 现行阈值下的
  误配与误拒"，并**额外测一条规则**：在 ±3px 窗口内取最小距离（best-of-offsets）。

跑法：`runtime\\python\\python.exe scripts\\ui_fp_measure.py`
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.chdir(ROOT)
os.environ.setdefault("PM_NO_WINDOW_TOUCH", "1")

from PIL import Image # noqa: E402

from agent import ui_fingerprint as F # noqa: E402

WEB = os.path.abspath(os.path.join(ROOT, "..", "dev-workspace", "persona-morph",
                                  "ocr-samples", "web"))
PICK = ("gh-005-keepassxc-main_interface.png", "gh-001-PowerToys-pt_run_ui.png",
        "gh-027-Files-FilesScreenshot.png", "gh-036-keepassxc-autoopen.png")
STEP = 96          # > 48 ⇒ 相邻裁剪不重叠
HALF = 24          # 48×48
JITTER = ((1, 0), (-1, 0), (0, 1), (0, -1), (2, 0), (0, 2), (2, 2), (-2, 2), (3, 0), (0, 3))


def crop_at(img, pt):
    x, y = pt
    return img.crop((x - HALF, y - HALF, x + HALF, y + HALF))


def positions(w, h):
    out = []
    y = 70
    while y < h - 70:
        x = 70
        while x < w - 70:
            out.append((x, y))
            x += STEP
        y += STEP
    return out


def gray_vec(crop, size=(16, 16)):
    try:
        return list(crop.convert("L").resize(size).getdata())
    except Exception:
        return []


def corr_dist(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 100.0
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a) ** 0.5
    vb = sum((x - mb) ** 2 for x in b) ** 0.5
    if va < 1e-6 or vb < 1e-6:
        return 100.0
    return (1.0 - sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (va * vb)) * 100.0


def best_dist(img, pt, ref, fn, metric, radius=3) -> int:
    """在 ±radius 窗口内取**最小**距离（best-of-offsets）：模拟"标定偏了几像素"时的宽容判法。"""
    best = metric(ref, fn(crop_at(img, pt)))
    for r in range(1, radius + 1):
        for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r), (r, r), (-r, -r), (r, -r), (-r, r)):
            d = metric(ref, fn(crop_at(img, (pt[0] + dx, pt[1] + dy))))
            if d < best:
                best = d
    return best


FEATS = {
    "dHash64（现行）": (F.dhash, lambda a, b: F.dist(a, b)),
    "灰度16×16相关": (gray_vec, lambda a, b: int(round(corr_dist(a, b)))),
}

print("图 / 位置数 / 特征 / 类内最大(±0~3px) / 类间最小 / 现行阈下：误配对数 / 位移±2 误拒数")
agg = {}
for name in PICK:
    p = os.path.join(WEB, name)
    if not os.path.exists(p):
        continue
    img = Image.open(p).convert("RGB")
    pts = positions(*img.size)
    for tag, (fn, metric) in FEATS.items():
        refs = [(pt, fn(crop_at(img, pt))) for pt in pts]
        intra = 0
        shift_reject = 0
        for pt, ref in refs:
            for dx, dy in JITTER:
                d = metric(ref, fn(crop_at(img, (pt[0] + dx, pt[1] + dy))))
                intra = max(intra, d)
            if metric(ref, fn(crop_at(img, (pt[0] + 2, pt[1] + 2)))) > F.MAX_DIST:
                shift_reject += 1
        inter = 10 ** 9
        false_accept = 0
        for i in range(len(refs)):
            for j in range(i + 1, len(refs)):
                d = metric(refs[i][1], refs[j][1])
                inter = min(inter, d)
                if d <= F.MAX_DIST:
                    false_accept += 1
        # best-of-offsets：类内最大距离会降到多少
        intra_best = 0
        for pt, ref in refs:
            intra_best = max(intra_best, best_dist(img, pt, ref, fn, metric))
        a = agg.setdefault(tag, {"intra": 0, "inter": 10 ** 9, "fa": 0, "sr": 0, "ib": 0, "n": 0})
        a["intra"] = max(a["intra"], intra)
        a["inter"] = min(a["inter"], inter)
        a["fa"] += false_accept
        a["sr"] += shift_reject
        a["ib"] = max(a["ib"], intra_best)
        a["n"] += len(pts)
        print("%-34s %3d  %-16s %3d              %3d          %4d            %d/%d"
              % (name[:34], len(pts), tag, intra, inter, false_accept, shift_reject, len(pts)))

print("\n==== 汇总 ====")
for tag, a in agg.items():
    print("%-16s 类内最大 %3d · 类间最小 %3d · 现行阈(%d)误配 %d · 位移±2 误拒 %d/%d · "
          "**best-of-offsets 后类内最大 %d**"
          % (tag, a["intra"], a["inter"], F.MAX_DIST, a["fa"], a["sr"], a["n"], a["ib"]))


# ══════════════ 公平比较（滤空白 + 每个特征取"类间零误配"的阈值）══════════════
# 为什么必须这么比：dHash 是 0~64 位、灰度相关是 0~100 ⇒ 拿同一个 MAX_DIST=10 去套两边是耍流氓。
# 正确比法：各自取"**类间零误配**"的最大阈值，再看**该阈值下**的位移误拒率。

def _fair():
    feats = {"dHash64": (F.dhash, lambda a, b: F.dist(a, b)),
             "灰度16×16相关": (gray_vec, lambda a, b: int(round(corr_dist(a, b))))}
    res = {}
    for name in PICK:
        p = os.path.join(WEB, name)
        if not os.path.exists(p):
            continue
        img = Image.open(p).convert("RGB")
        for tag, (fn, metric) in feats.items():
            kept = []
            for pt in positions(*img.size):
                c = crop_at(img, pt)
                mean, std = F._stats(c)          # 复用产品的"空白判定"
                if mean < 6 or std < 2:          # 空白/全黑：产品本来就不写指纹，这里也不计
                    continue
                kept.append((pt, fn(c)))
            if len(kept) < 3:
                continue
            inter = min(metric(kept[i][1], kept[j][1])
                        for i in range(len(kept)) for j in range(i + 1, len(kept)))
            thr = max(0, inter - 1)              # 类间零误配的最大阈值
            rej = rej_best = intra = intra_best = 0
            for pt, ref in kept:
                d_p = max(metric(ref, fn(crop_at(img, (pt[0] + dx, pt[1] + dy)))) for dx, dy in JITTER)
                d_b = best_dist(img, pt, ref, fn, metric, radius=3)
                intra = max(intra, d_p)
                intra_best = max(intra_best, d_b)
                rej += 1 if d_p > thr else 0
                rej_best += 1 if d_b > thr else 0
            r = res.setdefault(tag, {"intra": 0, "intra_best": 0, "inter": 10 ** 9,
                                     "rej": 0, "rej_best": 0, "n": 0})
            r["intra"] = max(r["intra"], intra)
            r["intra_best"] = max(r["intra_best"], intra_best)
            r["inter"] = min(r["inter"], inter)
            r["rej"] += rej
            r["rej_best"] += rej_best
            r["n"] += len(kept)
    print("\n==== 公平比较（滤掉空白块；各自用「类间零误配」的阈值）====")
    for tag, r in res.items():
        thr = max(0, r["inter"] - 1)
        print("%-14s 非空白位置 %3d · 类间最小 %3d ⇒ 可用阈值 %2d · 类内最大 %3d ⇒ 抖动误拒 %d/%d（%.0f%%）"
              % (tag, r["n"], r["inter"], thr, r["intra"], r["rej"], r["n"], 100.0 * r["rej"] / max(1, r["n"])))
        print("%-14s   → best-of-offsets(±3px) 后：类内最大 %3d · 误拒 %d/%d（%.0f%%）"
              % ("", r["intra_best"], r["rej_best"], r["n"], 100.0 * r["rej_best"] / max(1, r["n"])))


_fair()
