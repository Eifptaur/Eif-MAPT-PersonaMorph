# -*- coding: utf-8 -*-
"""判据：`agent/rhythm.py` 拟人节奏层。

钉住四件事（每件都有**反向锚**，删掉实现必红）：

A. **抖动只加长、不缩短** —— 这是本层**最重要**的不变量。
   外部项目的原话：各处 `time.sleep(x)` 都是在等微信把画面渲染完，
   **缩短就是拿稳定性换抖动**。⇒ 任何档位下 `nap(x) >= x`。
B. **状态跨进程落盘** —— 它的理由："一个演示脚本一个新 Python 进程，
   纯内存限速等于没有。" ⇒ 落盘 + 新实例能读回。
C. **`gate()` 只慢不拦** —— 它**必须**返回等待秒数而**不是**抛异常/返回 False；
   且 `off` 档必须精确还原"加这层之前"（0 秒、不做任何事）。
D. **接线正确** —— 发送链上**先 `risk`（判发不发）后 `rhythm`（判多慢发）**；
   反了就白等。且节奏层**不许挡住发送**（异常被吞）。

跑法：`runtime/python/python.exe scripts/rhythm_selftest.py`
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent.rhythm as R  # noqa: E402

PASS = 0
FAIL = 0
FAILS = []


def T(name: str, cond, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s%s" % (name, ("  [%s]" % detail) if detail else ""))
    else:
        FAIL += 1
        FAILS.append(name)
        print("  FAIL %s%s" % (name, ("  [%s]" % detail) if detail else ""))


def _with_tmp_state():
    """把状态文件指到临时目录，避免污染真实 data/。"""
    d = tempfile.mkdtemp(prefix="pm-rhythm-")
    R.STATE_PATH = os.path.join(d, "rhythm.json")
    return d


# ══════════════════════════════════════════════════════════════════════
print("[A] 抖动只加长、不缩短（★ 本层最重要的不变量）")

_slept = []
_real_sleep = time.sleep
time.sleep = lambda s: _slept.append(s)
try:
    for _name in ("gentle", "calm", "off"):
        R.set_profile(_name)
        vals = [R.nap(0.5) for _ in range(30)]
        T("nap 不缩短既有等待（%s 档）" % _name, min(vals) >= 0.5,
          "%.3f~%.3f" % (min(vals), max(vals)))
    # 反向锚①：档位表里每一档的倍率下限都必须 >= 1.0
    bad = [n for n, p in R.PROFILES.items() if p.nap[0] < 1.0]
    T("★反向锚：所有档位 nap 倍率下限 >= 1.0", not bad, ",".join(bad) or "全部合规")
    # 反向锚②：off 档必须精确等于原值（还原"加这层之前"）
    R.set_profile("off")
    T("★反向锚：off 档 nap 精确还原（倍率 1.0）",
      all(abs(R.nap(0.3) - 0.3) < 1e-9 for _ in range(5)))
    # gentle 档要真的发散（不是固定值假抖动）
    R.set_profile("gentle")
    vals = [R.nap(0.5) for _ in range(40)]
    T("gentle 档取值发散（不是固定值）", len({round(v, 4) for v in vals}) > 5,
      "%d 个不同值" % len({round(v, 4) for v in vals}))
finally:
    time.sleep = _real_sleep
    _slept.clear()

print("[A2] spread / 区间语义")
R.set_profile("off")
T("off 档 spread 取下限", R.spread(7, 7) == 7)
R.set_profile("gentle")
_sp = {round(R.spread(62, 80), 1) for _ in range(60)}
T("spread 落在区间内且发散", 62 <= min(_sp) and max(_sp) <= 80 and len(_sp) > 5,
  "%d 个值" % len(_sp))
T("区间退化时取下限", R.spread(7, 7) == 7)


# ══════════════════════════════════════════════════════════════════════
print("[B] 状态跨进程落盘（它踩过的坑：新进程＝纯内存限速等于没有）")
_with_tmp_state()
R.reset()
R.set_profile("gentle")

_slept2 = []
time.sleep = lambda s: _slept2.append(s)
try:
    R.reset()
    w0 = R.gate("t0")
    T("冷启动第一次不等待", w0 == 0.0, "%.2f" % w0)
    w1 = R.gate("t1")
    T("紧接的第二次会等待（间隔生效）", w1 > 0.0, "%.2f" % w1)
finally:
    time.sleep = _real_sleep

T("状态文件已落盘", os.path.isfile(R.STATE_PATH),
  os.path.basename(R.STATE_PATH))
# ★反向锚：模拟"另一个进程" —— 强制重读盘，最近一次写入必须能读回
_snap = R.snapshot()
T("★反向锚：新实例能读回最近一次写入（跨进程）",
  _snap["since_last"] is not None and _snap["since_last"] < 60,
  "since_last=%s" % _snap["since_last"])
# ★反向锚：删掉落盘调用 ⇒ 上面这条必红（此处直接验证落盘文件内容非空）
try:
    import json as _json
    with open(R.STATE_PATH, "r", encoding="utf-8") as _fh:
        _d = _json.load(_fh)
    T("★反向锚：落盘内容含 last/stamps/v",
      "last" in _d and "stamps" in _d and _d.get("v") == R.STATE_VERSION)
finally:
    pass


# ══════════════════════════════════════════════════════════════════════
print("[C] gate() 只慢不拦；off 档精确还原")
_with_tmp_state()
R.reset()
R.set_profile("off")
_slept3 = []
time.sleep = lambda s: _slept3.append(s)
try:
    R.reset()
    r = R.gate("off-test")
    T("★反向锚：off 档 gate 返回 0（精确还原加这层之前）", r == 0.0, "%.2f" % r)
    # off 档连发多次都不该有任何等待（也不该写盘）
    _t0 = time.time()
    for _ in range(5):
        R.gate("off-test")
    T("off 档连发 5 次零等待", len(_slept3) == 0, "%d 次 sleep" % len(_slept3))
finally:
    time.sleep = _real_sleep

# ★反向锚：gate 的返回值语义 —— 必须是"等待秒数"，不是布尔/异常
R.reset()
R.set_profile("gentle")
_slept4 = []
time.sleep = lambda s: _slept4.append(s)
try:
    R.reset()
    R.gate("a")            # 第一次不等待
    v = R.gate("b")        # 第二次等待
    T("★反向锚：gate 返回等待秒数（float，不是布尔）",
      isinstance(v, float) and v > 0, "type=%s val=%s" % (type(v).__name__, v))
finally:
    time.sleep = _real_sleep

# 时钟回拨防护：状态里塞个未来时间戳，不该把闸门永久锁死
import json as _json2  # noqa: E402
_with_tmp_state()
with open(R.STATE_PATH, "w", encoding="utf-8") as _fh:
    _json2.dump({"v": R.STATE_VERSION, "last": time.time() + 99999, "stamps": []}, _fh)
R._loaded = False
R._profile = None
_slept5 = []
time.sleep = lambda s: _slept5.append(s)
try:
    w = R.gate("future")
    T("未来时间戳（时钟回拨）不会永久卡死闸门", w == 0.0, "%.2f" % w)
finally:
    time.sleep = _real_sleep


# ══════════════════════════════════════════════════════════════════════
print("[D] 接线：先 risk 后 rhythm，且节奏层不许挡住发送")
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "agent", "sender.py")
_src = open(_SRC, "r", encoding="utf-8").read()

T("sender.py 已接入 rhythm", "from . import rhythm" in _src or "import rhythm" in _src)
# ★反向锚：顺序必须是 risk 在前、rhythm 在后
_i_risk = _src.find("_risk.check(chat_key, _t)")
_i_rhy = _src.find('_rhythm.gate("send_text")')
T("★反向锚：文本链 risk 在 rhythm 之前（先判发不发，再判多慢发）",
  0 <= _i_risk < _i_rhy, "risk@%d rhythm@%d" % (_i_risk, _i_rhy))
_i_risk_img = _src.find('_risk.check(chat_key, "[图片]")')
_i_rhy_img = _src.find('_rhythm.gate("send_image")')
T("★反向锚：图片链 risk 也在 rhythm 之前",
  0 <= _i_risk_img < _i_rhy_img, "risk@%d rhythm@%d" % (_i_risk_img, _i_rhy_img))
# ★反向锚：节奏层异常被吞（不许挡住发送）
T("★反向锚：节奏层异常被吞（except 包住，不挡发送）",
  _src.count("拟人节奏层跳过") >= 2, "%d 处" % _src.count("拟人节奏层跳过"))
# ★反向锚：读路径不许调 gate（截图/OCR/库读取都不节流）
T("★反向锚：risk.py 里没有调 rhythm.gate（拦与慢分离，互不越界）",
  "rhythm.gate" not in open(os.path.join(os.path.dirname(_SRC), "risk.py"),
                            encoding="utf-8").read())
_RHY = os.path.join(os.path.dirname(_SRC), "rhythm.py")
_rhy_src = open(_RHY, encoding="utf-8").read()
T("★反向锚：rhythm.py 不 import risk（单向依赖：节奏不认识闸门）",
  "import risk" not in _rhy_src)


# ══════════════════════════════════════════════════════════════════════
print()
print("=" * 62)
print("rhythm_selftest：%d 通过 / %d 失败" % (PASS, FAIL))
if FAILS:
    print("失败项：")
    for _f in FAILS:
        print("  - %s" % _f)
print("=" * 62)
sys.exit(1 if FAIL else 0)
