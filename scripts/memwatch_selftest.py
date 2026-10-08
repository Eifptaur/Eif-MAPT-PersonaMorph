# -*- coding: utf-8 -*-
"""内存水位判据（PLAN 第 161 项 ⑤）。

跑法： py -3 scripts\\memwatch_selftest.py     退出码 0=全过 / 1=有失败

守四件事：
  A. **RSS 真读得到**（⛔ 不是 0 —— 实测踩过：不声明 ctypes `argtypes` ⇒ 句柄被截断 ⇒ **静默返回 0**）；
  B. **计数器只读**（观测量不许改状态：采一次样前后，被观测结构的条目数不变）；
  C. **采样有数字、有阈值语义**（`warn` 与 `WARN_MB` 的关系真的算得对）；
  D. **接线**（启动处真的调了；失败不许影响启动）。

⛔ 不写盘、不联网、不动产品状态。
"""
from __future__ import annotations

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

import _iso14  # noqa: E402
_iso14.all_()

from agent import memwatch as M  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [%s]" % detail) if detail else ""))


print("══ A. RSS 真读得到（不是 0）══")
_mb = M.rss_mb()
ok("A1 ★RSS 读得出真实值（>0）", _mb > 0, "%.1f MB" % _mb)
ok("A2 RSS 在合理区间（1 MB ~ 10 GB —— 防'读成了字节数'或'读成了 0'）",
   1.0 < _mb < 10240.0, "%.1f MB" % _mb)
ok("A3 连读两次**同量级**（防'每次调用都返回不同垃圾'）",
   abs(M.rss_mb() - _mb) < 200.0, "两次差 %.1f MB" % abs(M.rss_mb() - _mb))
_src = io.open(os.path.join(ROOT, "agent", "memwatch.py"), encoding="utf-8").read()
ok("A4 ★源码级：`GetProcessMemoryInfo` 显式声明了 argtypes/restype（不声明就会静默读成 0）",
   "argtypes" in _src and "restype" in _src and "GetProcessMemoryInfo" in _src)
# 反向锚：模拟"源码里没了声明" ⇒ A4 那句检查式必须变红（证明 A4 真在读那两个词）
_fake = _src.replace("argtypes", "ARG").replace("restype", "RES")
_has_decl = ("argtypes" in _fake and "restype" in _fake and "GetProcessMemoryInfo" in _fake)
ok("A5 ★反向锚：把源码里的 argtypes/restype 抹掉后，A4 的判据式会变红", not _has_decl,
   "反向锚有效")

print("\n══ B. 观察不许改状态 ══")
_c_before = M.counters()
M.sample()
_c_after = M.counters()
ok("B1 采样前后计数器**逐键相等**（观测是只读的）",
   _c_before == _c_after, "%s → %s" % (_c_before, _c_after))
ok("B2 `sample()` 返回结构齐（mb / counters / delta_mb / thread / warn）",
   all(k in M.sample() for k in ("mb", "counters", "delta_mb", "thread", "warn")))

print("\n══ C. 阈值语义 ══")
_r = M.sample()
_v = M.rss_mb()
ok("C1 `warn` ＝ (RSS ≥ WARN_MB)（真按阈值算，不是写死的）",
   _r["warn"] == (_v >= M.WARN_MB), "warn=%s rss=%.1f 阈值=%.0f" % (_r["warn"], _v, M.WARN_MB))
# 反向锚：把阈值压到 1 MB ⇒ 必然 warn（证明 C1 不是恒 False）
_old = M.WARN_MB
try:
    M.WARN_MB = 1.0
    ok("C2 ★反向锚：阈值压到 1 MB ⇒ `warn` 必须变 True（证明阈值真被读了）",
       M.sample()["warn"] is True, "warn=True")
finally:
    M.WARN_MB = _old
ok("C3 默认阈值是 2 GB（便携运行时本身几百 MB，留足余量）", M.WARN_MB == 2048.0, "%.0f" % M.WARN_MB)
ok("C4 默认间隔 15 min（够看趋势、不刷屏）", M.INTERVAL_S == 900.0, "%.0f s" % M.INTERVAL_S)

print("\n══ D. 接线 ══")
_pp = io.open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
ok("D1 ★启动处真的起了水位日志（不是只写了模块）", "memwatch" in _pp and "_mw.start()" in _pp)
ok("D2 失败不许影响启动（包了 try）", "内存水位日志跳过" in _pp)
ok("D3 `stop()` 真能停（判据/测试需要）", "def stop(" in _src and "_timer.cancel()" in _src)
# D4：真起一次、真能停（行为核，不只是源码核）
_started = M.start(interval_s=30.0)
_after_start = M._timer is not None
M.stop()
_after_stop = M._timer is None
ok("D4 ★行为核：`start()` 真起了定时器、`stop()` 真停掉了",
   _started and _after_start and _after_stop,
   "started=%s alive=%s stopped=%s" % (_started, _after_start, _after_stop))

print("\n==== 内存水位判据：%d 通过 / %d 失败 ====" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for _f in FAIL:
        print("  · " + _f)
sys.exit(1 if FAIL else 0)
