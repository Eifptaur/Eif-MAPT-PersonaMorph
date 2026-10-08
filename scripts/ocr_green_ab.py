# -*- coding: utf-8 -*-
"""绿底白字**端到端**实测 —— 回答用户 当面问的那句：

    朋友那版裸像素喂 OCR 读不出绿底白字 ⇒ **不代表我方增强版也不行**。到底行不行？

⭐ **被测对象是「生产入口」，不是"人工选路线"**：
    绿底高亮行的名字，线上由 `chat_ocr._band_name(img, y_abs, cands)` 取
    （`highlight()` / `_current_chat_name_impl()` 都调它）。所以这里直接调它，
    而不是我自己裁一刀去喂别的函数——**我裁的那刀不是生产路径，读数不算数**
    （本轮就踩过两次：整屏宽裁剪把聊天区读进来；用 `_green_x()` 当横界砍掉了名字开头）。

对照三条路（判据要能分辨，不许恒真恒假）：
    A. 裸图直读整条绿带 —— 等价"朋友那版"的做法（不做任何预处理）
    B. `_band_name` 无候选集 —— 纯像素路径（`highlight()` 那条）
    C. `_band_name` 带候选集 —— 线上真实路径（候选集＝本机真实会话名）

素材：用户专供真机微信截图（`dev-workspace/persona-morph/ocr-samples/wechat/`，150% DPI）。
      ⛔ 该目录**不在版本库**，换机器就没了 ⇒ 脚本会打印「跳过」而不是假报结果。

用法：
    runtime\\python\\python.exe scripts\\ocr_green_ab.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import Image # noqa: E402

from agent import chat_ocr as O # noqa: E402

_SAMPLES = os.path.join(os.path.dirname(ROOT), "dev-workspace", "persona-morph",
                        "ocr-samples", "wechat")

#: `(文件, 真值会话名)` —— 真值由 + 我逐张读图核对（`_probe` 裁图目视比对过）
CASES = [
    ("0af83564-b60d-4812-aa4f-3c330c6399c0.png", "微信团队"),
    ("7bcf1579-61e9-4c80-a54a-47eb45341b53.png", "某会话"),
    ("6c29e9f4-2c81-4b4d-9a71-a826c521f403.png", None),   # 真值未核：只量"读到什么"
]


def _hit(got, truth):
    """真名有没有被读到（**不要求**读数就等于真名——真实场景允许"名字+时间戳残留"）。"""
    if truth is None:
        return None
    a, t = O.norm(got), O.norm(truth)
    if not a or not t:
        return False
    return t in a or (len(t) >= 3 and O._edit_dist_le(a, t, 1))


def _fmt(r):
    return "（无真值，只量读数）" if r is None else ("命中 ✓" if r else "**未命中**")


def _bare_read(im, b):
    """A：整条绿带裸图直读 —— 等价"朋友那版"（不做任何预处理）。

    绿带横向取**绿像素的实际边界**（不是 `_green_x()`，那个是"不含头像的取样窗"）。
    """
    import numpy as np
    rgb = np.asarray(im.convert("RGB")).astype(int)
    y0, y1 = max(0, b["y0"]), min(im.height - 1, b["y1"])
    sub = rgb[y0:y1 + 1]
    r, g, bl = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
    # 与 `O._is_green` 同款判据（向量化版）：本机深绿 ± 容差，或"绿占优"
    deep = ((np.abs(r - O.GREEN[0]) <= O.GREEN_TOL)
            & (np.abs(g - O.GREEN[1]) <= O.GREEN_TOL)
            & (np.abs(bl - O.GREEN[2]) <= O.GREEN_TOL))
    dom = (g - r >= 12) & (g - bl >= 10) & (g >= 90) & (g >= np.maximum(r, bl) + 8)
    gm = deep | dom
    idx = [i for i, v in enumerate(gm.mean(axis=0)) if v > 0.5]
    if not idx:
        idx = [0, im.width - 1]
    crop = im.crop((idx[0], y0, idx[-1] + 1, y1 + 1))
    return "".join(str(i[0]) for i in O.recognize_dual(crop)).strip()


def main():
    if not O.available():
        print("⚠️ 内置 OCR 不可用")
        return 2
    if not os.path.isdir(_SAMPLES) or not any(
            os.path.exists(os.path.join(_SAMPLES, f)) for f, _ in CASES):
        print("↷ 跳过：找不到真机素材目录 %s（该目录不在版本库，换机器就没有）" % _SAMPLES)
        return 0
    print("绿底白字端到端实测（被测＝生产入口 `_band_name`）· 内置=%s · RapidOCR=%s"
          % (O.available(), (O.rapidocr_status() or {}).get("installed")))
    print("素材：%s" % _SAMPLES)
    print("")

    agg = {"A": [0, 0], "B": [0, 0], "C": [0, 0]}
    for fn, truth in CASES:
        p = os.path.join(_SAMPLES, fn)
        if not os.path.exists(p):
            print("（跳过：找不到 %s）" % fn)
            continue
        im = Image.open(p).convert("RGB")
        bands = O.green_bands(im, min_ratio=0.30, min_h=28)
        if not bands:
            print("── %s 没找到绿底高亮行，跳过" % fn[:8])
            continue
        b = bands[0]
        cands = [truth, "文件传输助手", "演示(3)"] if truth else None
        print("── %s 绿底带 y=%s..%s（真值 %s）" % (fn[:8], b.get("y0"), b.get("y1"), truth))

        got_a = _bare_read(im, b)
        print("   A 整带裸图直读（朋友那版做法） %r  → %s" % (got_a[:40], _fmt(_hit(got_a, truth))))
        agg["A"][0] += 1 if _hit(got_a, truth) else 0

        got_b = O._band_name(im, b["y_abs"])
        print("   B 生产入口·无候选集（纯像素）  %r  → %s" % (got_b[:40], _fmt(_hit(got_b, truth))))
        agg["B"][0] += 1 if _hit(got_b, truth) else 0

        got_c = O._band_name(im, b["y_abs"], cands)
        print("   C 生产入口·带候选集（线上路径）%r  → %s" % (got_c[:40], _fmt(_hit(got_c, truth))))
        agg["C"][0] += 1 if _hit(got_c, truth) else 0

        n = 1 if truth is not None else 0
        for k in "ABC":
            agg[k][1] += n
        print("")

    print("==== 绿底白字命中（只算有真值的样本）====")
    for k, name in (("A", "整带裸图直读（朋友那版）"), ("B", "生产入口·无候选集"),
                    ("C", "生产入口·带候选集（线上）")):
        h, n = agg[k]
        print("   %-26s %d/%d = %s" % (name, h, n,
                                       ("%5.1f%%" % (100.0 * h / n)) if n else "  n/a"))
    print("")
    print("结论口径：C（线上路径）≥ B ≥ A 才算「增强版有效」；"
          "C==A 说明增强链没起作用（本轮就踩过：第 0 档把多行糊字收下 ⇒ 短路了后面整条链）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
