# -*- coding: utf-8 -*-
"""V-R2-1 判据：看门狗对「机器人立刻退出」必须退避 + 有上限（不许永远 5 秒一圈）。

复现（改前，一行；跑 22 秒即见 5 次拉起）：
    $wd = 临时目录 ; 复制 scripts\\watchdog.py 进去 ; 造一个 `sys.exit(3)` 的 stub 机器人 ;
    起 watchdog 22 秒 ⇒ 数 data/spawns.log 的行数 = **5**，日志里"拉起/退出：code=3"刷成一串。

守四条（阴/阳对照）：
  A 阴：stub 机器人**秒退 3**（双开/已有实例在跑）⇒ 只拉起 1 次、看门狗**自己退场**（rc=0）、留痕；
  B 阴：stub 机器人**秒退 1**（起不来）⇒ 间隔按 `5→10→20→40` 递增（±20% 抖动）、**连续 5 次就停手**；
  C 阳：stub 机器人"活够 12 秒才退" ⇒ 仍按 5 秒重拉（**正常重启语义不许被改坏**）；
  D 真机端到端：临时副本里**真起一个看门狗进程**（退出码 3 的 stub）⇒ 它自己退出、只拉起 1 次、
    且收掉了自己的 `watchdog.pid`（不留"指向死进程"的把手）。

⛔ 纪律：全程用临时目录 + stub 机器人（**不起真机器人、不开窗、不动鼠标**）；
   `PID_FILE`/`CRASH_LOG`/`STOP_FLAG` 全部指到临时目录，产品的 `data/runtime.log` 字节数必须不变。
"""
from __future__ import annotations

import os
import shutil
import subprocess as _sp
import sys
import tempfile
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import watchdog as WD          # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [%s]" % detail) if detail else ""))


STUB = ("import os, sys\n"
        "d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')\n"
        "os.makedirs(d, exist_ok=True)\n"
        "with open(os.path.join(d, 'spawns.log'), 'a', encoding='utf-8') as f:\n"
        "    f.write('spawn\\n')\n"
        "sys.exit(%d)\n")


class _Bot(object):
    """假机器人进程：`wait()` 立刻返回退出码；`alive>0` 时把假时钟往前推（模拟"活了一会儿"）。"""

    def __init__(self, rc, alive=0.0, clock=None):
        self.returncode, self._alive, self._clock = rc, alive, clock

    def wait(self):
        if self._clock is not None and self._alive:
            self._clock[0] += self._alive
        return self.returncode


class _Sub(object):
    """假 subprocess：只记录、只返回假进程（**绝不真起进程**）。"""

    DEVNULL = -1

    def __init__(self, rc, alive=0.0, clock=None):
        self.rc, self.alive, self.clock = rc, alive, clock
        self.spawns, self.runs = [], []

    def Popen(self, *a, **k):
        self.spawns.append((a, k))
        return _Bot(self.rc, self.alive, self.clock)

    def run(self, *a, **k):
        self.runs.append((a, k))
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")


class _Stop(Exception):
    """睡眠桩用来把 `while True` 收场（判据自己停，不让它轮到天亮）。"""


def _boot(tmp):
    """把 watchdog 的模块级路径与 subprocess/time 换成隔离的桩件。"""
    WD.PID_FILE = os.path.join(tmp, "watchdog.pid")
    WD.CRASH_LOG = os.path.join(tmp, "runtime.log")
    WD.STOP_FLAG = os.path.join(tmp, "stopped.flag")
    os.makedirs(tmp, exist_ok=True)


def _drive(sub, clock, max_sleeps=6):
    """用桩件跑 `WD.main()`，返回 (返回值, 记录的睡眠秒数)。睡眠超过 max_sleeps 就收场。"""
    slept = []

    def _sleep(s):
        slept.append(float(s))
        if len(slept) > max_sleeps:
            raise _Stop()

    WD.subprocess = sub
    WD.time = types.SimpleNamespace(sleep=_sleep, time=lambda: clock[0], strftime=time.strftime,
                                    localtime=time.localtime, monotonic=time.monotonic)
    rc = None
    try:
        rc = WD.main()
    except _Stop:
        rc = "STOPPED"
    finally:
        WD.time = time
    return rc, slept


def main():
    tmp = tempfile.mkdtemp(prefix="pm-wdbackoff-")
    _prod_log = os.path.join(ROOT, "data", "runtime.log")
    _prod_before = (os.path.getsize(_prod_log) if os.path.exists(_prod_log) else 0)
    _real_sub = WD.subprocess
    try:
        _boot(tmp)
        print("== A. 阴：机器人秒退 3（已有实例在跑）⇒ 只拉 1 次、看门狗自己退场 ==")
        sub = _Sub(rc=3)
        rc, slept = _drive(sub, [time.time()])
        ok("A1 看门狗**自己退出**（不再永远每 5 秒拉一次）", rc == 0, "rc=%r" % (rc,))
        ok("A2 只拉起 1 次（改前 22 秒 5 次）", len(sub.spawns) == 1, "spawns=%d" % len(sub.spawns))
        ok("A3 一次退避睡眠都不该有（exit 3 重启没意义，不是「再试一次」）", slept == [], str(slept))
        _log = open(WD.CRASH_LOG, encoding="utf-8", errors="replace").read()
        ok("A4 留痕：写清「为什么退场」（exit 3 / 已有实例在运行）",
           ("已有实例在运行" in _log) and ("退场" in _log), _log.strip().splitlines()[-1:])
        ok("A5 退场时把自己的 watchdog.pid 收掉（不留指向死进程的把手）",
           not os.path.exists(WD.PID_FILE), WD.PID_FILE)

        print("\n== B. 阴：机器人秒退 1（起不来）⇒ 指数退避 + 连续 5 次停手 ==")
        sub2 = _Sub(rc=1)
        _boot(tmp)
        rc2, slept2 = _drive(sub2, [time.time()], max_sleeps=10)
        ok("B1 连续失败到上限后**停手**（返回值非 0、不再拉）", rc2 == 1, "rc=%r" % (rc2,))
        ok("B2 拉起次数 = MAX_EARLY_FAILS（不是无限）",
           len(sub2.spawns) == WD.MAX_EARLY_FAILS, "spawns=%d" % len(sub2.spawns))
        ok("B3 退避**递增**（每一项都不小于前一项 ×1.3 —— 抖动 ±20% 下 1.8 不成立，按抖动区间断言）",
           len(slept2) >= 4 and all(slept2[i] >= slept2[i - 1] * 1.3 for i in range(1, len(slept2))),
           str([round(x, 1) for x in slept2]))
        _nominal = [WD.BACKOFF_BASE_S * (2 ** i) for i in range(len(slept2))]
        ok("B4 每一项都落在「名义值 ±20% 抖动」区间内（不是拍脑袋的常量）",
           all(_nominal[i] * 0.8 - 1e-6 <= slept2[i] <= min(WD.BACKOFF_CAP_S, _nominal[i] * 1.2) + 1e-6
               for i in range(len(slept2))),
           "实际=%s 名义=%s" % ([round(x, 1) for x in slept2], _nominal))
        ok("B5 有上限（任何一次退避都不超过 BACKOFF_CAP_S=10 分钟）",
           all(x <= WD.BACKOFF_CAP_S + 1e-6 for x in slept2), str(WD.BACKOFF_CAP_S))
        _log2 = open(WD.CRASH_LOG, encoding="utf-8", errors="replace").read()
        ok("B6 停手时留痕（说清「连续几次失败 + 去日志看原因」）",
           ("连续" in _log2) and ("停手" in _log2), _log2.strip().splitlines()[-1:])

        print("\n== C. 阳：机器人活够 12 秒才退 ⇒ 仍按 5 秒重拉（别把正常重启改坏）==")
        _boot(tmp)
        _clock = [time.time()]
        sub3 = _Sub(rc=0, alive=WD.EARLY_EXIT_S + 2.0, clock=_clock)
        rc3, slept3 = _drive(sub3, _clock, max_sleeps=3)
        # ⚠️ 睡眠桩在第 4 次 sleep 时收场，而循环是"先拉、再睡" ⇒ 拉起次数 = max_sleeps + 1
        ok("C1 长活进程不算失败：连拉 4 次都没被退避/停手", len(sub3.spawns) == 4,
           "spawns=%d" % len(sub3.spawns))
        ok("C2 每次间隔都还是 RESTART_GAP_S（老口径没被改坏）",
           all(abs(x - WD.RESTART_GAP_S) < 1e-6 for x in slept3), str(slept3))
        ok("C3 没有提前进入退避（sleep 值里不含退避那一档）",
           all(x < WD.BACKOFF_BASE_S + 1e-6 for x in slept3), str(slept3))

        print("\n== D. 单元：_backoff_delay 的形状（递增 / 封顶 / 抖动有界）==")
        _d = [WD._backoff_delay(n) for n in range(1, 12)]
        _exp = [min(WD.BACKOFF_CAP_S, WD.BACKOFF_BASE_S * (2 ** (n - 1))) for n in range(1, 12)]
        _ncap = len([e for e in _exp if e < WD.BACKOFF_CAP_S])      # 还没封顶的档位数
        _dpre, _dpost = _d[:_ncap], _d[_ncap:]
        ok("D1 封顶之前递增（每项 ≥ 前一项 ×1.3）",
           len(_dpre) >= 2 and all(_dpre[i] >= _dpre[i - 1] * 1.3 for i in range(1, len(_dpre))),
           str([round(x, 1) for x in _d]))
        ok("D1b 封顶之后都在 [0.8·CAP, CAP]（抖动上下浮动，不再要求严格单调）",
           all(WD.BACKOFF_CAP_S * 0.8 - 1e-6 <= x <= WD.BACKOFF_CAP_S + 1e-6 for x in _dpost),
           str([round(x, 1) for x in _dpost]))
        ok("D2 抖动落在 ±20%（不会退化成 0 延迟重试风暴）",
           all(_exp[i] * 0.8 - 1e-6 <= _d[i] <= min(WD.BACKOFF_CAP_S, _exp[i] * 1.2) + 1e-6
               for i in range(len(_d))), str([round(x, 1) for x in _d]))
        ok("D3 封顶 10 分钟", max(_d) <= WD.BACKOFF_CAP_S + 1e-6, "max=%.1f" % max(_d))
        ok("D4 抖动**真的在抖**（同一档多次调用不总是同一个数）",
           len({round(WD._backoff_delay(4), 6) for _ in range(20)}) > 1, "")
        ok("D5 源码级：主循环里不再有无条件的 `time.sleep(5)` 兜底",
           "time.sleep(5)" not in open(os.path.join(ROOT, "scripts", "watchdog.py"),
                                       encoding="utf-8").read(), "")

        print("\n== E. 真机端到端：临时副本里真起一个看门狗（stub 机器人 exit 3）==")
        if os.environ.get("PM_JUDGE_NO_PROC") == "1":
            # ⛔ V-R7-12：判据环境（`run_all_selftests.py` 会带这个开关）⇒ **只跑静态/内存那半**，
            #   本段"真 Popen 一个看门狗进程"整段跳过，并**明确打一行 SKIP**（不冒充通过）。
            #   单跑（不带这个环境变量）时，这一段照旧真起真收 —— 那是有价值的证据。
            print("  SKIP E. 真机端到端：PM_JUDGE_NO_PROC=1 ⇒ 不真起看门狗进程（E1~E4 不判）")
        else:
            wd = tempfile.mkdtemp(prefix="pm-wdreal-")
            try:
                os.makedirs(os.path.join(wd, "scripts"), exist_ok=True)
                shutil.copy(os.path.join(ROOT, "scripts", "watchdog.py"),
                            os.path.join(wd, "scripts", "watchdog.py"))
                with open(os.path.join(wd, "scripts", "persona_morph.py"), "w", encoding="utf-8") as f:
                    f.write(STUB % 3)
                t0 = time.time()
                p = _sp.Popen([sys.executable, os.path.join(wd, "scripts", "watchdog.py")],
                              cwd=wd, creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
                              stdin=_sp.DEVNULL, stdout=_sp.PIPE, stderr=_sp.PIPE)
                try:
                    rc4 = p.wait(timeout=60)                  # ⛔ 必须带超时，别把判据挂死
                except _sp.TimeoutExpired:
                    p.kill()
                    rc4 = "TIMEOUT"
                el = time.time() - t0
                ok("E1 真看门狗进程**自己退出**（exit 3 条件下不再转圈）", rc4 == 0, "rc=%r 用时=%.1fs" % (rc4, el))
                ok("E2 且是「很快」退出（≤20 秒，不是等天亮）", isinstance(el, float) and el <= 20.0, "%.1fs" % el)
                _spawns = os.path.join(wd, "data", "spawns.log")
                _n = len(open(_spawns, encoding="utf-8").read().strip().splitlines()) if os.path.exists(_spawns) else 0
                ok("E3 stub 机器人只被拉起 1 次（改前 22 秒 5 次）", _n == 1, "spawns=%d" % _n)
                ok("E4 真跑也收掉了自己的 watchdog.pid", not os.path.exists(os.path.join(wd, "data", "watchdog.pid")), "")
            finally:
                shutil.rmtree(wd, ignore_errors=True)

        print("\n== F. 判据自身的纪律 ==")
        _prod_after = (os.path.getsize(_prod_log) if os.path.exists(_prod_log) else 0)
        ok("F1 判据没有写产品的 data/runtime.log", _prod_before == _prod_after,
           "%d → %d 字节" % (_prod_before, _prod_after))
    finally:
        WD.subprocess = _real_sub
        WD.time = time
        shutil.rmtree(tmp, ignore_errors=True)

    # ── 窗口限流（第五轮回执 · 业界对账第 ⑤ 条）────────────────────────────────
    #   老口径：只有"活不足 EARLY_EXIT_S"才算失败，跑够时长了就 `fails = 0` ⇒ **"每次都在第 61 秒崩"
    #   会无限重启**（退避永远从头开始）。⇒ 再加一个滑动窗口闸：WINDOW_S 内重启 ≥ WINDOW_MAX ⇒ 停手。
    _wsrc = open(os.path.join(ROOT, "scripts", "watchdog.py"), encoding="utf-8").read()
    ok("⑤ 窗口限流在位：常量 + 滑动窗口计数 + 「反复崩溃就停手」的文案",
       hasattr(WD, "WINDOW_S") and hasattr(WD, "WINDOW_MAX") and "_stamps" in _wsrc
       and "判为**反复崩溃**" in _wsrc, str((getattr(WD, "WINDOW_S", None), getattr(WD, "WINDOW_MAX", None))))
    ok("⑤ 反例锚：窗口必须比「秒退阈值」宽得多，否则它跟老口径没区别（这就是老口径的漏洞）",
       float(WD.WINDOW_S) > float(WD.EARLY_EXIT_S) * 10, str((WD.WINDOW_S, WD.EARLY_EXIT_S)))

    print("\n==== 看门狗退避判据（V-R2-1）：%d 通过 / %d 失败 ====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
