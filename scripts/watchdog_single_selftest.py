# -*- coding: utf-8 -*-
"""看门狗单实例判据（2026-09-17 立，起因＝用户当晚连报三次「又起 N 个控制台」）。

守的是什么：`watchdog.py` 的「已有同版本看门狗 ⇒ 本实例退出，**绝不拉起任何子进程**」。

改前的 bug（真因）：`main()` 一进来就把自己的 pid 写进 `PID_FILE`，紧接着才去读这个文件
比对 ⇒ `old == os.getpid()` 恒成立 ⇒ **单实例检查是死代码**。后果：每跑一次
`onestart.py` 就多一个看门狗，多个看门狗各自拉一个机器人、各自开一个控制台窗口，
互相抢微信窗口 —— 用户看到的就是"控制台越冒越多"。

改法两条：①先读旧 pid（`_pre`）、再决定；②自己的 pid **等赢下检查之后再写**
（否则重复实例退出时把 `watchdog.pid` 指到一个马上要死的进程上，
控制台「停止机器人」就找不到真看门狗）。

用法：`runtime\python\python.exe -X utf8 scripts\watchdog_single_selftest.py`
（不联网、不起真机器人：`subprocess` 在判据里被替换成记录器）
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import subprocess as _sp          # noqa: E402  真 subprocess，只用来造一个"活着的进程"
import watchdog as WD             # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  \u2714 %s %s" % (name, extra))
    else:
        FAIL += 1
        print("  \u2718 %s %s" % (name, extra))


class _Rec(object):
    """替换掉 watchdog 的 subprocess：只记录，绝不起真进程。"""
    popen_calls = []
    run_calls = []

    @staticmethod
    def Popen(*a, **k):
        _Rec.popen_calls.append((a, k))
        raise AssertionError("不该拉起任何子进程")

    @staticmethod
    def run(*a, **k):
        _Rec.run_calls.append((a, k))
        return None


def main():
    tmp = tempfile.mkdtemp(prefix="pm-wd-")
    WD.PID_FILE = os.path.join(tmp, "watchdog.pid")
    real_sub, real_sleep = WD.subprocess, WD.time.sleep

    # 造一个"真的活着"的看门狗进程（借一个睡 30 秒的 python 冒充），
    # 不能用 os.getpid()：那会命中 `old == os.getpid()` 那条"就是我自己"的短路。
    sleeper = _sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                        creationflags=0x08000000)

    print("\n[一] 已有同版本看门狗活着 ⇒ 本实例退出，一个子进程都不拉")
    try:
        with open(WD.PID_FILE, "w", encoding="utf-8") as f:
            f.write("%d\n%s" % (sleeper.pid, WD.WATCHDOG_VER))
        WD.subprocess, WD.time.sleep = _Rec, (lambda s: None)
        try:
            rc = WD.main()
        except Exception as e:                                   # noqa: BLE001
            rc = "EXC:%r" % (e,)
        finally:
            WD.subprocess, WD.time.sleep = real_sub, real_sleep

        ok("main() 正常返回 0（不是异常、不是跑去拉机器人）", rc == 0, "rc=%r" % (rc,))
        ok("没有拉起任何子进程（Popen 0 次、taskkill 0 次）",
           not _Rec.popen_calls and not _Rec.run_calls,
           "popen=%d run=%d" % (len(_Rec.popen_calls), len(_Rec.run_calls)))
        with open(WD.PID_FILE, encoding="utf-8") as f:
            _pid = (f.read().strip().splitlines() or [""])[0].strip()
        ok("重复实例**不改写** watchdog.pid（否则停止机器人会指到一个马上就死的进程）",
           _pid == str(sleeper.pid), "pid=%s 期望=%s" % (_pid, sleeper.pid))
    finally:
        try:
            sleeper.terminate()
        except Exception:
            pass

    print("\n[二] 源码级看守（防下次被人顺手改回死代码）")
    src = open(os.path.join(ROOT, "scripts", "watchdog.py"), encoding="utf-8").read()
    _loop_i = src.index("for _i in range(3):")
    _head = src[src.index("def main():"):_loop_i]
    ok("先读旧 pid（_pre）再进主循环", "_pre = _read_watchdog_pid()" in _head)
    ok("循环第一圈用 _pre，不再读一次把它覆盖掉",
       "old, ver = _pre if _i == 0" in src)
    ok("main() 开头不再无条件写自己的 pid",
       "with open(PID_FILE, \"w\"" not in _head)
    ok("赢家分支里仍然会写自己的 pid",
       "with open(PID_FILE, \"w\"" in src[src.index("for _i in range(3):"):])

    print("\n%d 通过 / %d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
