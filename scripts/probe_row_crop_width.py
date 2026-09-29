# -*- coding: utf-8 -*-
"""一次性证据采集：裁框宽度消融（**只读，不改生产**）。

背景：外部真机样本上，绿底会话名 `测试` 读不出。已定位根因方向 =
`agent/chat_ocr.py:1808 list_rows` 的裁框：

    x0 = max(0, left - 240)
    x1 = max(x0 + 40, left - 6)      # ← 右沿紧贴面板左沿

在外部图（left=315、绿带右界真值 308）上，右沿 = 309 ⇒ 卡在绿带边缘 ⇒ 名字被切半。

本探针**不改生产**，只在探针内部复刻 list_rows 的裁法，扫描 `(列表宽, 右沿余量)`，
看哪些档位能让 `测试` 读出来，同时对自家 3 图做回归，确认不退化。

用法：./runtime/python/python.exe scripts/probe_row_crop_width.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import logging                                                    # noqa: E402
logging.disable(logging.CRITICAL)

from PIL import Image                                             # noqa: E402
from agent import chat_ocr as co                                  # noqa: E402

SAMPLE = os.path.abspath(os.path.join(
    os.path.dirname(ROOT), "dev-workspace", "persona-morph", "ocr-samples", "wechat"))

# 外部图（真机故障样本）——名字真实存在但读不出
EXTERNAL = os.path.join(SAMPLE, "external-反馈来源-1151x1185.png")
EXTERNAL_FULL = [
    r"C:\Users\ptmou\.workbuddy\clipboard-images\clipboard-2026-09-29T21-59-54-426Z-8feb10ca.png",
]
# 自家 tier-1（绿底名字读得出，回归用）
MINE = [
    "0af83564-4b7d-4e2f-8d6c-1e2f3a4b5c6d.png",
    "7bcf1579-2c3d-4e5f-6a7b-8c9d0e1f2a3b.png",
    "6c29e9f4-5e6f-7a8b-9c0d-1e2f3a4b5c6d.png",
]


def _find(names):
    """样本目录里按前缀找文件（全名可能与我记的不一致）。"""
    out = []
    for n in names:
        pre = n.split("-")[0]
        hit = [f for f in os.listdir(SAMPLE) if f.startswith(pre) and f.endswith(".png")]
        if hit:
            out.append(os.path.join(SAMPLE, hit[0]))
    return out


def crop_ocr(img, box, zoom=2):
    """复刻 list_rows 的裁块 + OCR，返回原始 items 的文本列表。"""
    crop = img.crop(box)
    if zoom > 1:
        crop = crop.resize((crop.width * zoom, crop.height * zoom))
    return [str(t) for t, *_ in co.recognize(crop)]


def scan(img, left, widths, margins, key="测试", zoom=2):
    """扫 (列表宽, 右沿余量) 两维，返回哪些档读出了 key。"""
    w, h = img.size
    print(f"\n  图 {w}x{h}  left={left}")
    print(f"  {'列表宽':>6} {'右沿=left-?':>12} {'裁框 x':>16} {'zoom':>4}  读出 {key}?")
    print("  " + "-" * 62)
    ok = []
    for lw in widths:
        for mg in margins:
            x0 = max(0, left - lw)
            x1 = max(x0 + 40, left - mg)
            texts = crop_ocr(img, (x0, 30, x1, h), zoom)
            joined = " ".join(texts)
            hit = key in joined
            if hit:
                ok.append((lw, mg, x1))
            print(f"  {lw:>6} {mg:>12} {f'({x0},{x1})':>16} {zoom:>4}  "
                  f"{'✅ 是' if hit else '❌ 否'}{'' if not hit else '   ← ' + joined[:70]}")
    return ok


def main():
    widths = [240, 260, 280, 300, 320]
    margins = [6, 20, 40]

    # ---- 1) 外部图：找能读出 `测试` 的档 ----
    print("=" * 78)
    print("【1】外部真机图：扫裁框宽度 × 右沿余量")
    print("=" * 78)
    img = None
    for p in [EXTERNAL] + EXTERNAL_FULL:
        if os.path.exists(p):
            img = Image.open(p).convert("RGB")
            print(f"  用图：{os.path.basename(p)}")
            break
    if img is None:
        print("  ！外部图都找不到，跳过")
        return

    left = co.ch.pane_left_for(img) or int(img.size[0] * co.ch.PANE_LEFT_REL)
    ok_ext = scan(img, left, widths, margins)

    # ---- 2) 自家 3 图：回归，确认不退化 ----
    print("\n" + "=" * 78)
    print("【2】自家 3 张 tier-1 图：回归（名字应仍读得出）")
    print("=" * 78)
    for path in _find(MINE):
        m = Image.open(path).convert("RGB")
        lf = co.ch.pane_left_for(m) or int(m.size[0] * co.ch.PANE_LEFT_REL)
        scan(m, lf, [240], margins, key="")           # 只跑默认宽 + 各余量
        # 打印默认档全文，人眼核
        x0 = max(0, lf - 240)
        for mg in [6, 40]:
            x1 = max(x0 + 40, lf - mg)
            texts = crop_ocr(m, (x0, 30, x1, m.size[1]), 2)
            print(f"    余量 {mg:>3} → {texts}")


if __name__ == "__main__":
    main()
