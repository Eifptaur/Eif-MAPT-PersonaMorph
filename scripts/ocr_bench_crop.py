# -*- coding: utf-8 -*-
"""OCR 基准 · **真实小块**（我们真正的场景：会话名/标题带那种小截图）。

为什么单开一份：`ocr_bench.py` 是合成图（可复现但不像真机）、`ocr_bench_real.py` 是全图召回
（真实但太"粗"）。我们要答的问题其实是第三种：**在真实的小块上，喂进去之前该怎么处理最划算**。
⇒ 这里用**真机截图裁出的小块**（真值由我逐块读图给定、并已用拼图核对过），比较几条预处理/融合路线。

`--repeat 2` 同 `ocr_bench.py`：**两遍数字一致**才许写进回执。

用法：
    runtime\\python\\python.exe scripts\\ocr_bench_crop.py
    runtime\\python\\python.exe scripts\\ocr_bench_crop.py --verbose --repeat 2
"""
import argparse
import os
import re
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import Image, ImageFilter, ImageOps # noqa: E402

from agent import chat_ocr as O # noqa: E402

_DEV = os.path.join(os.path.dirname(ROOT), "dev-workspace", "persona-morph", "root-probes")
_LIVE = os.path.join(_DEV, "_r18_live.png")      # 控制台窗口（1060×1060）
_FULL = os.path.join(_DEV, "_r18_full.png")      # 整屏（2560×1600）

#: 真实小块：`(图, 像素框 x0,y0,x1,y1, 真值串...)` —— 框与真值都**目视核对过**（拼图 `_crops_verify.png`）
#: ⛔ 改这里之前先重新做拼图核对（上一版就是坐标拍脑袋，量出来全是垃圾数据）。
CASES = [
    (_FULL, (0.05, 0.20, 0.30, 0.25), ("新建任务",)),        # 侧栏首项（真实小字）
    (_FULL, (0.05, 0.20, 0.30, 0.30), ("新建任务", "助理", "项目")),   # 侧栏三项
    (_FULL, (0.30, 0.55, 0.75, 0.585), ("正在执行命令",)),    # 对话框一行
    (_FULL, (0.30, 0.55, 0.75, 0.62), ("正在执行命令", "已消耗", "文档")),  # 对话框三行
]


def _box(im, frac):
    return (int(im.width * frac[0]), int(im.height * frac[1]),
            int(im.width * frac[2]), int(im.height * frac[3]))


def _gray_up(c, z):
    """灰度 + LANCZOS 放大（**不二值化**；真机实测二值化会咬掉抗锯齿笔画）。"""
    g = ImageOps.autocontrast(c.convert("L")).convert("RGB")
    return g.resize((g.width * z, g.height * z), Image.LANCZOS)


def _sharp_up(c, z):
    """灰度 + 锐化（USM）+ 放大（屏幕字抗锯齿，适度锐化常能把笔画"提"出来）。"""
    g = ImageOps.autocontrast(c.convert("L")).filter(
        ImageFilter.UnsharpMask(radius=1.2, percent=160, threshold=3)).convert("RGB")
    return g.resize((g.width * z, g.height * z), Image.LANCZOS)


def _variants(crop, cands):
    """被测路线：名字 → 返回识别文本列表的函数。"""
    def _dual(img):
        return [str(x[0]) for x in O.recognize_dual(img)]

    def _winrt(img):
        return [str(x[0]) for x in O.recognize(img)]

    def _fuse(img):
        """双引擎融合：两边都读，取**更像候选集**的那一份（用现有 `best_candidate` 打分）。"""
        a = [str(x[0]) for x in O.recognize(img)]
        b = []
        try:
            if (O.rapidocr_status() or {}).get("installed"):
                b = [str(x[0]) for x in O._rapid_recognize(img)]
        except Exception as e: # noqa: BLE001
            print("（RapidOCR 不可用：%s）" % str(e)[:50])
        if not a or not b:
            return a or b
        sa = max([O.best_candidate(x, cands)[1] for x in a] or [0.0])
        sb = max([O.best_candidate(x, cands)[1] for x in b] or [0.0])
        return b if sb > sa else a

    return [
        ("① 裸图·内置", lambda c=crop: _winrt(c)),
        ("② 灰度+放大×2·内置", lambda c=crop: _winrt(_gray_up(c, 2))),
        ("③ 灰度+放大×3·内置", lambda c=crop: _winrt(_gray_up(c, 3))),
        ("④ 二值化×2·内置（现有 preprocess_ink）", lambda c=crop: _winrt(O.preprocess_ink(c, zoom=2))),
        ("⑤ 锐化+放大×2·内置", lambda c=crop: _winrt(_sharp_up(c, 2))),
        ("⑥ 灰度+放大×2·双引擎融合", lambda c=crop: _fuse(_gray_up(c, 2))),
    ]


def _hit(lines, truth):
    t = O.norm(truth)
    for ln in lines:
        a = O.norm(ln)
        if t and a and (t in a or (len(t) >= 3 and O._edit_dist_le(a, t, 1))):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--repeat", type=int, default=1, help="连跑 N 遍核对复现性")
    a = ap.parse_args()
    if not O.available():
        print("⚠️ 内置 OCR 不可用")
        return 2
    print("真实小块基准 · 内置=%s · RapidOCR=%s" % (
        O.available(), (O.rapidocr_status() or {}).get("installed")))
    print("")
    agg = {}
    for path, frac, truths in CASES:
        if not os.path.exists(path):
            print("（跳过：找不到 %s）" % path)
            continue
        im = Image.open(path).convert("RGB")
        crop = im.crop(_box(im, frac))
        print("── %s %s（%dx%d，真值 %d 条）" % (os.path.basename(path), frac, crop.width,
                                             crop.height, len(truths)))
        for label, fn in _variants(crop, list(truths)):
            t0 = time.time()
            lines = fn() or []
            dt = time.time() - t0
            hit = [t for t in truths if _hit(lines, t)]
            d = agg.setdefault(label, {"hit": 0, "n": 0, "secs": 0.0})
            d["hit"] += len(hit)
            d["n"] += len(truths)
            d["secs"] += dt
            if a.verbose:
                print("    %-38s %d/%d · %.2fs · 读到 %s"
                      % (label, len(hit), len(truths), dt, str(lines)[:60]))
    print("")
    print("==== 真实小块命中率 ====（第 1 遍）")
    for label, d in sorted(agg.items(), key=lambda x: -x[1]["hit"] / max(1, x[1]["n"])):
        print("   %-38s %2d/%2d = %5.1f%% · 用时 %.2fs"
              % (label, d["hit"], d["n"], 100.0 * d["hit"] / max(1, d["n"]), d["secs"]))
    if int(a.repeat or 1) > 1:
        # ⛔ 复现性纪律（同 ocr_bench.py）：连跑两遍数字不一致 ⇒ 这份读数不许写进回执
        _flags = 0x08000000 if os.name == "nt" else 0
        r = subprocess.run([sys.executable, __file__], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", creationflags=_flags)
        lines2 = [ln for ln in (r.stdout or "").splitlines() if "/" in ln and "=" in ln and "%" in ln]
        print("\n==== 第 2 遍（复现性核对）====")
        same = len(lines2) == len(agg)
        for ln in lines2:
            print("   " + ln.strip())
            m2 = re.search(r"(\d+)/\s*(\d+)", ln)
            name = ln.strip().split()[0:2]
            key = None
            for label in agg:
                if label.startswith(name[0] if name else ""):
                    key = label
                    break
            if key and m2 and int(m2.group(1)) != agg[key]["hit"]:
                same = False
        print("   ⇒ 复现性：%s" % ("一致 ✓" if same else "**不一致**（这份数字不可用）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
