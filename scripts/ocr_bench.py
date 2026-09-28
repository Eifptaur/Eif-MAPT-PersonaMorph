# -*- coding: utf-8 -*-
"""OCR 精度基准（**量化**每次改动的收益；不是判据，是一把尺子）。

为什么要有它：OCR 是"所有操作的地基"（找会话行、判当前会话、点搜索、读时间戳全靠它），
而"改一改觉得应该更好"是没法验收的。这份基准用**我们自己渲染的已知文本**（真值确定、离线、
不依赖真机微信）把三条路线的命中率摆在一起：

  ① `raw`      —— 原图直接送引擎（不放大、不二值化）
  ② `legacy`   —— 老管线：`preprocess_ink(zoom=2/3/4/5)` 按序试，读到就停
  ③ `auto`     —— 新档：`auto_zoom` 按**实测字高**把最可能那一档排最前，其余兜底照旧
  ④ `+snap`    —— 在 ③ 之上加**候选词表纠错**（`best_candidate`，与线上同一份实现）

用法：
    runtime\\python\\python.exe scripts\\ocr_bench.py            # 默认 12 组 × 3 档字号
    runtime\\python\\python.exe scripts\\ocr_bench.py --verbose  # 逐条打印读数
    runtime\\python\\python.exe scripts\\ocr_bench.py --repeat 2 # **连跑两遍**（数字必须一致才算数）

⛔ **教训（2026-09-29）**：本人曾拿一份"86.1% / 94.4%"的读数去汇报，**后来复现不出来**
（当时第二引擎还没正式装上，读数里混了它的贡献）。⇒ 从此**基准数字必须连跑两遍一致**才敢写进回执。
"""
import argparse
import io
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import chat_ocr as O # noqa: E402

#: 真值样本：贴近真实会话名的形态（中文 / 带括号成员数 / 中英数字混排 / 长名 / 单字名）
CASES = ("演示", "演示(3)", "测试(2)", "家人群", "工作汇报", "老张", "E", "宋孟",
         "社区团购2026", "Python 学习", "星期天播报", "买菜的群(12)")

#: 模拟的三种字号（px 字高）——对应 100% / 125% / 150% 缩放下会话名的实际像素高
SIZES = (13, 17, 22)

FONT_CANDIDATES = (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyh.ttf",
                   r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc")


def _font(size: int):
    from PIL import ImageFont
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception: # noqa: BLE001
                continue
    return ImageFont.load_default()


def _render(text: str, px: int):
    """渲成"标题带"样的小图（浅底深字 + 左右留白，模拟真实截图里的名字区）。"""
    from PIL import Image, ImageDraw
    f = _font(px)
    tmp = ImageDraw.Draw(Image.new("L", (10, 10)))
    try:
        box = tmp.textbbox((0, 0), text, font=f)
        w = max(8, box[2] - box[0])
        h = max(8, box[3] - box[1])
    except Exception: # noqa: BLE001
        w, h = len(text) * px, px
    im = Image.new("RGB", (w + 24, h + 16), (247, 247, 247))
    ImageDraw.Draw(im).text((12, 8), text, fill=(38, 38, 38), font=f)
    return im


def _read(img, zooms) -> tuple:
    """按给定 zoom 顺序试读 → (文本, 试了几档, 用时秒)。读到就停（与线上同一套 `preprocess_ink`）。"""
    t0 = time.time()
    for i, z in enumerate(zooms):
        c = O.preprocess_ink(img, zoom=z, invert=False) if z > 1 else img
        got = "".join(str(x[0]) for x in O.recognize(c)).strip()
        if got:
            return got, i + 1, time.time() - t0
    return "", len(zooms), time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--repeat", type=int, default=1,
                    help="连跑 N 遍核对**复现性**（数字必须一致才算数）")
    a = ap.parse_args()

    if not O.available():
        print("⚠️ 本机 OCR 引擎不可用（Windows.Media.Ocr / 语言包缺失）⇒ 基准跑不了，如实退出。")
        return 2
    print("OCR 基准 · 引擎可用=%s · RapidOCR=%s" % (
        O.available(), O.rapidocr_status().get("installed")))
    print("样本 %d 组 × 字号 %s（px 字高）\n" % (len(CASES), list(SIZES)))

    stat = {"raw": [0, 0], "legacy": [0, 0], "auto": [0, 0], "snap": [0, 0], "tries": 0.0, "secs": 0.0}
    total = 0
    for px in SIZES:
        print("── 字号 %dpx ──" % px)
        for text in CASES:
            img = _render(text, px)
            total += 1
            truth = O.norm(text)
            # ① 原图直读
            _raw = "".join(str(x[0]) for x in O.recognize(img)).strip()
            stat["raw"][0] += 1 if O.norm(_raw) == truth else 0
            stat["raw"][1] += 1
            # ② 老管线（固定档序）
            _leg, _n1, _t1 = _read(img, (2, 3, 4, 5))
            stat["legacy"][0] += 1 if O.norm(_leg) == truth else 0
            stat["legacy"][1] += 1
            # ③ 上线逻辑：`_read_band_crop(候选集=会话名表)` —— 多档择优（不是"读到就停"）
            _calls0 = int(O.health().get("calls") or 0)
            _t2 = time.time()
            _new, _nz = O._read_band_crop(img, 2, cands=list(CASES))
            _dt2 = time.time() - _t2
            stat["auto"][0] += 1 if O.norm(_new) == truth else 0
            stat["auto"][1] += 1
            # ④ 生产路径的最终名字 = ③ 之后再 snap 一次（调用方会做；这里量最终形态）
            _snap = O.snap_name(_new, CASES)
            stat["snap"][0] += 1 if O.norm(_snap) == truth else 0
            stat["snap"][1] += 1
            stat["tries"] += max(0, int(O.health().get("calls") or 0) - _calls0)
            stat["secs"] += _dt2
            if a.verbose:
                print("   %-14s 字高估=%2d 命中档=%d | raw=%-10r legacy=%-10r 择优=%-10r snap=%-10r %s"
                      % (text, O.text_height_hint(img), _nz, _raw[:10], _leg[:10], _new[:10],
                         _snap[:10], "✓" if O.norm(_snap) == truth else "✗"))
    print()
    print("==== 命中率（归一化后完全相等）====")
    for k, label in (("raw", "① 原图直读"), ("legacy", "② 老管线（固定档序 2/3/4/5）"),
                     ("auto", "③ 新档（按实测字高先试）"), ("snap", "④ ③ + 候选词表纠错")):
        ok_n, n = stat[k]
        print("   %-26s %3d/%3d = %5.1f%%" % (label, ok_n, n, 100.0 * ok_n / max(1, n)))
    print("   平均 OCR 调用 %.2f 次/条 · 平均单条耗时 %.2fs（%d 条）"
          % (stat["tries"] / max(1, total), stat["secs"] / max(1, total), total))
    if int(a.repeat or 1) > 1:
        # ⛔ 复现性纪律：**数字连跑两遍不一致 ⇒ 不许写进回执**（本人踩过：86.1% 那次复现不出来）。
        import subprocess
        _flags = 0x08000000 if os.name == "nt" else 0   # ⛔ 不许闪控制台窗（判据钉着这一条）
        r = subprocess.run([sys.executable, __file__], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", creationflags=_flags)
        tails = [ln for ln in (r.stdout or "").splitlines() if ln.strip().startswith(("①", "②", "③", "④"))]
        print("\n==== 第 2 遍（复现性核对）====")
        same = True
        for ln in tails:
            print("   " + ln.strip())
            k = ln.strip().split()[0]
            want = {"①": stat["raw"], "②": stat["legacy"], "③": stat["auto"], "④": stat["snap"]}.get(k)
            # ⛔ 不用 try/except 解析（本仓静默点棘轮只许降）：解析不出来就给 -1，显式比较。
            # ⚠️ 标签里可能含空格（"老管线（固定档序 2/3/4/5）"）⇒ 从 `n/ N =` 那一段取数，别按空格切
            _m2 = re.search(r"(\d+)/\s*\d+\s*=", ln)
            got_hit = int(_m2.group(1)) if _m2 else -1
            if want and got_hit != want[0]:
                same = False
        print("   ⇒ 复现性：%s" % ("一致 ✓" if same else "**不一致**（这份数字不可用，先查为什么）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
