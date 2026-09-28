# -*- coding: utf-8 -*-
"""OCR 精度基准 · **真机截图**版（合成图之外的另一半；真值由看图人工给定）。

为什么要这一半：`ocr_bench.py` 用 PIL 渲染的合成图（真值确定、可复现），
但它**不是真实截图**——真实截图里有抗锯齿、半透明、深浅主题、贴图与压缩噪声。
用户口径：*"没有图片，你不会去网上广泛地搜集吗"* ⇒ 这里用**本地真机截图**（探针抓的真实画面）
＋ 我逐张读出的**真值串**，量"真图上到底读得出多少"。

口径（召回式，不看整串相等）：
  · 真值 = 图像里**确实存在**的若干串（我读图给定，见 `CASES`）；
  · 命中 = 某个真值串（归一化后）**出现在**该引擎识别出的任意一行里；
  · 召回 = 命中数 / 真值数。这样不需要精确 crop，也不因换行/分栏而误判。
  ⚠️ 真值只收录**清晰可辨**的串（我读不出的不写进去），宁可少而准。

用法：
    runtime\\python\\python.exe scripts\\ocr_bench_real.py              # 默认两张真图
    runtime\\python\\python.exe scripts\\ocr_bench_real.py --verbose    # 逐条打印命中/漏
"""
import argparse
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import Image # noqa: E402

from agent import chat_ocr as O # noqa: E402

#: 真机截图（探针抓的真实画面，在 dev-workspace；不在包里）+ 逐张读出的真值串
_DEV = os.path.join(os.path.dirname(ROOT), "dev-workspace", "persona-morph", "root-probes")
CASES = [
    (os.path.join(_DEV, "_r18_live.png"),
     ["新建任务", "助理", "项目", "专家", "连接器", "定时任务", "资料库", "更多"]),
    (os.path.join(_DEV, "_r19_pm_real_final_v.png"),
     ["product-strategy", "新建", "图库", "桌面", "下载", "运行状态", "微信已连接"]),
    (os.path.join(_DEV, "_r18_full.png"),
     ["修复启动崩溃发送器错误", "深度思考", "正在执行命令", "新建任务", "资料库",
      "完整访问", "生成项目功能"]),
]


def _norm(s: str) -> str:
    return O.norm(str(s or ""))


def _hit(lines, truth: str) -> bool:
    """某行里出现该真值串（归一化后），或**近似**出现（差 ≤1 字，容忍 OCR 单字误读）。"""
    t = _norm(truth)
    if not t:
        return False
    for ln in lines:
        a = _norm(ln)
        if not a:
            continue
        if t in a:
            return True
        if O._edit_dist_le(a, t, 1) and len(t) >= 3:   # 整行≈真值（短行场景）
            return True
    return False


def _run(img_path: str, verbose: bool) -> dict:
    img = Image.open(img_path).convert("RGB")
    out = {"file": os.path.basename(img_path), "size": img.size}
    engines = (
        ("内置(WinRT)·原图", lambda: O.recognize(img)),
        ("内置(WinRT)·预处理", lambda: O.recognize(O.preprocess_ink(img, zoom=max(2, O.auto_zoom(img) or 2)))),
        ("RapidOCR·原图", lambda: O._rapid_recognize(img)),
    )
    truths = [c[1] for c in CASES if c[0] == img_path][0]
    for label, fn in engines:
        t0 = time.time()
        try:
            items = fn() or []
        except Exception as e: # noqa: BLE001
            out[label] = {"err": str(e)[:60], "secs": time.time() - t0}
            continue
        lines = [str(it[0]) for it in items]
        hit = [t for t in truths if _hit(lines, t)]
        out[label] = {"recall": len(hit) / max(1, len(truths)), "hit": len(hit),
                      "n": len(truths), "lines": len(lines), "secs": time.time() - t0,
                      "miss": [t for t in truths if t not in hit]}
        if verbose:
            print("    %-18s 召回 %d/%d · 行数 %2d · %.2fs · 漏：%s"
                  % (label, len(hit), len(truths), len(lines), time.time() - t0,
                     "、".join(out[label]["miss"])[:60]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    if not O.available():
        print("⚠️ 内置 OCR 不可用（Windows.Media.Ocr / 语言包缺失）")
        return 2
    try:
        _rs = O.rapidocr_status()
    except Exception: # noqa: BLE001
        _rs = {}
    print("真机截图基准 · 内置 OCR 可用=%s · RapidOCR=%s" % (O.available(), _rs.get("installed")))
    print("")
    agg = {}
    for path, truths in CASES:
        if not os.path.exists(path):
            print("（跳过：找不到 %s）" % path)
            continue
        print("── %s（真值 %d 条）" % (os.path.basename(path), len(truths)))
        got = _run(path, a.verbose)
        for k, v in got.items():
            if not isinstance(v, dict) or "recall" not in v:
                continue
            d = agg.setdefault(k, {"hit": 0, "n": 0, "secs": 0.0})
            d["hit"] += v["hit"]
            d["n"] += v["n"]
            d["secs"] += v["secs"]
    print("")
    print("==== 真图召回率（命中真值串 / 真值总数）====")
    for k, d in sorted(agg.items(), key=lambda x: -x[1]["hit"] / max(1, x[1]["n"])):
        print("   %-18s %2d/%2d = %5.1f%% · 用时合计 %.1fs"
              % (k, d["hit"], d["n"], 100.0 * d["hit"] / max(1, d["n"]), d["secs"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
