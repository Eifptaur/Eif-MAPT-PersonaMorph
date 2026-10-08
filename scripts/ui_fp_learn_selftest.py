# -*- coding: utf-8 -*-
"""**偏移学习**判据：把"实跑/素材"变成准确率的第一步 —— 但学习最容易出事，所以先钉纪律。

纪律（每条都有对应锚）：
  L1 系统性偏移学得出来（10 次 (2,1) ⇒ 学到 (2,1)）
  L2 ★ **抖动主导时不学**（一半 +3 一半 -3 ⇒ MAD 超限 ⇒ 不校正，别把噪声当规律）
  L3 样本不足不学（< LEARN_MIN_N）
  L4 偏移太小不值得校正（< LEARN_MIN_PX）
  L5 ★ **超容差样本不进学习**（那些 offset 是"乱找出来的"，不是标定误差）
  L6 ★ **安全锚：学习只挪"从哪儿开始找"，绝不放松判定** —— 帧里换成完全不同的图标时，
      即使学到的偏移把它挪过去，也必须判「不是」
  L7 ★ 只记**通过校验**的样本（对不上的样本会污染学习）
  L8 校正量有上界（LEARN_MAX_PX），绝不把点击挪远
  L9 reset_learning 能清掉；statistics 看得见

跑法：`runtime\\python\\python.exe scripts\\ui_fp_learn_selftest.py`
"""
import io
import os
import sys
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
os.environ.setdefault("PM_NO_WINDOW_TOUCH", "1")
import _srcmatch as _sm # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


from PIL import Image, ImageDraw # noqa: E402

from agent import ui_fingerprint as F # noqa: E402


def icon(shape="a", shift=(0, 0)):
    im = Image.new("RGB", (200, 200), (246, 246, 248))
    d = ImageDraw.Draw(im)
    x, y = 100 + shift[0], 100 + shift[1]
    if shape == "a":
        d.rectangle([x - 30, y - 30, x + 30, y + 30], fill=(30, 120, 220))
        for k in range(7):
            d.line([x - 26 + k * 8, y - 26, x - 26 + k * 8, y + 26], fill=(255, 255, 255), width=2)
    else:
        d.ellipse([x - 30, y - 30, x + 30, y + 30], fill=(220, 60, 60))
        d.line([x - 24, y - 24, x + 24, y + 24], fill=(20, 20, 20), width=4)
    return im


print("── L1~L5：学习的统计纪律（纯函数，确定性）──")
r = {}
for _ in range(10):
    F.learn_offset(r, (2, 1))
ok("L1 系统性偏移学得出来（10 次 (2,1) ⇒ (2,1)）", F.learned_shift(r) == (2, 1), str(F.learn_stats(r)))
r2 = {}
for i in range(10):
    F.learn_offset(r2, (3 if i % 2 else -3, 0))
ok("L2 ★ 抖动主导时不学（一半 +3 一半 -3 ⇒ MAD 超限 ⇒ 不校正）",
   F.learned_shift(r2) == (0, 0) and F.learn_stats(r2)["mad"][0] > F.LEARN_STABLE_MAD,
   str(F.learn_stats(r2)))
r3 = {}
for _ in range(F.LEARN_MIN_N - 3):
    F.learn_offset(r3, (4, 4))
ok("L3 样本不足不学（少于 %d 次）" % F.LEARN_MIN_N, F.learned_shift(r3) == (0, 0),
   str(F.learn_stats(r3)["n"]) + " 次")
r4 = {}
for _ in range(12):
    F.learn_offset(r4, (1, 0))
ok("L4 偏移太小不值得校正（1px < %d）" % F.LEARN_MIN_PX, F.learned_shift(r4) == (0, 0))
r5 = {}
for _ in range(12):
    F.learn_offset(r5, (20, 20))
ok("L5 ★ 超容差样本不进学习（20px 那些是「乱找出来的」）",
   F.learned_shift(r5) == (0, 0) and not r5.get("learn"), str(r5.get("learn")))
r6 = {}
for _ in range(12):
    F.learn_offset(r6, (99, 99))
ok("L8 校正量有上界（观测 99px 也只记到容差内 / 不记）",
   max(abs(x) for x in F.learned_shift(r6)) <= F.LEARN_MAX_PX, str(F.learned_shift(r6)))

print("\n── L6/L7：安全锚（学习不许放松判定）＋ 只记通过校验的样本 ──")
_SRC = io.open(os.path.join(ROOT, "agent", "ui_fingerprint.py"), encoding="utf-8").read()
ok("L7 ★ 源码锚：记录样本的调用在「放行」分支内（对不上的样本不许污染学习）",
   # 分支条件现在是 `d <= max_dist and not _too_close`（加了最近邻归属判据）
   _sm.has(_SRC, "if d <= max_dist and not _too_close:") and _sm.has(_SRC, "learn_offset(rec, off)"))

_real = {k: getattr(F, k) for k in ("key", "load", "save", "_grab")}
GUI = types.SimpleNamespace(render_rect=(0, 0, 200, 200))
PT = (100, 100)
try:
    F.key = lambda *a, **k: "test-key"
    # ⛔ 参考指纹必须是**48×48 裁剪**的哈希（＝产品口径）。第一版直接 `dhash(整张 200×200)`，
    #    量纲完全不同 ⇒ 距离恒定二十几 ⇒ L1b/L6b 假红（判据自己写错，不是实现错）。
    F._grab = lambda gui: icon("a")
    F.reset_frame_cache()
    _ref = F.capture(GUI, "tA", pts={"tA": PT})
    assert _ref.get("ok"), "参考指纹取不到：%s" % _ref.get("why")
    ref_rec = {"hash": _ref["hash"], "pt": list(PT), "learn": {"xs": [3] * 12, "ys": [3] * 12}}
    F.load = lambda path=None: {"test-key": {"tA": dict(ref_rec)}}
    F._grab = lambda gui: icon("b")          # 帧里是**完全不同的**图标
    F.reset_frame_cache()
    _saved = []
    F.save = lambda db, path=None: (_saved.append(1), True)[1]
    v, info = F.verify(GUI, "tA", pts={"tA": PT})
    ok("L6 ★ 即便学到的偏移把搜索中心挪过去，**换成别的图标仍必须判「不是」**",
       v is False, "verdict=%s · dist=%s" % (v, info.get("dist")))
    ok("L7b 判「不是」时**没有**往学习里记样本（保存次数 %d）" % len(_saved), not _saved)

    # 通过校验 ⇒ 记样本（并且节流保存）
    F._grab = lambda gui: icon("a", (3, 3))
    F.reset_frame_cache()
    _saved.clear()
    v2, info2 = F.verify(GUI, "tA", pts={"tA": PT})
    ok("L1b 通过校验 ⇒ 记 1 个样本（且写盘有节流）",
       v2 is True and len(_saved) <= 1, "verdict=%s · 保存 %d 次" % (v2, len(_saved)))
    ok("L6b 校正后信息里写明「已按学到的偏移校正」+ 收窄后的半径",
       "学到的偏移" in str(info2.get("reason")), str(info2.get("reason"))[-46:])
finally:
    for k, v in _real.items():
        setattr(F, k, v)

print("\n── L9：可清除 / 可见 ──")
try:
    _db = {"k1": {"tA": {"hash": "x", "learn": {"xs": [2], "ys": [1]}}},
           "k2": {"tB": {"hash": "y", "learn": {"xs": [1], "ys": [1]}}}}
    F.load = lambda path=None: _db
    _wrote = []
    F.save = lambda db, path=None: (_wrote.append(db), True)[1]
    n = F.reset_learning("k1")["cleared"]
    ok("L9 reset_learning 按 key 清掉学习（且真的落盘）",
       n == 1 and "learn" not in _db["k1"]["tA"] and _db["k2"]["tB"].get("learn") and _wrote,
       "清了 %d 条" % n)
finally:
    F.load, F.save = _real["load"], _real["save"]

print("\n── L10：落盘（学习必须能跨重启）──")
import tempfile # noqa: E402
_tmpf = os.path.join(tempfile.mkdtemp(prefix="ufp_learn_"), "fp.json")
_real_file = getattr(F, "FP_FILE", None)
try:
    F.FP_FILE = _tmpf
    F.key = lambda *a, **k: "test-key"
    F.reset_frame_cache()
    F._grab = lambda gui: icon("a")
    _base = F.capture(GUI, "tA", pts={"tA": PT})
    F.save({"test-key": {"tA": {"hash": _base["hash"], "pt": list(PT)}}})
    F._grab = lambda gui: icon("a", (3, 3))
    _seen = []
    for _i in range(3):
        F.reset_frame_cache()
        _v, _if = F.verify(GUI, "tA", pts={"tA": PT})
        _seen.append(_v)
    _lr = ((F.load().get("test-key") or {}).get("tA", {}) or {}).get("learn") or {}
    ok("L10 ★ 通过校验后学习样本**真的落盘**（跨重启仍在）",
       bool(_lr.get("xs")) and all(_v is True for _v in _seen),
       "落盘样本 %d 个 · 三次校验 %s" % (len(_lr.get("xs") or []), _seen))
finally:
    if _real_file is not None:
        F.FP_FILE = _real_file
    F.load, F.save = _real["load"], _real["save"]

print("\n偏移学习判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
