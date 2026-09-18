# -*- coding: utf-8 -*-
"""Persona Morph 看门狗（无窗口）：机器人崩溃/退出后自动重启。

用 pythonw 运行、零 PowerShell 依赖（无 cmd、无 powershell 窗口）。
启动时把自己的 PID 写到 data/watchdog.pid，供 停止机器人 读取。
"""
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
PID_FILE = os.path.join(DATA, "watchdog.pid")
# ⛔ 2026-09-16 改名（名字误导排查）：这个文件**不是"崩溃日志"，它是主运行日志** ——
#    机器人跑起来后绝大多数日志（带 `persona_morph.py:<行号>`）都写在这里，
#    而 `logs/persona_morph.log` 反而只记启动与收尾。旧名 `bot_crash.log` 让人不止一次找错文件
#    ⇒ 改成 `runtime.log`；启动时做一次性改名，老文件内容不丢。
CRASH_LOG = os.path.join(DATA, "runtime.log")
_OLD_CRASH_LOG = os.path.join(DATA, "bot_crash.log")


def _migrate_crash_log_name() -> None:
    """把旧名 `data/bot_crash.log` 一次性并到 `data/runtime.log`（失败就留着，不抛）。"""
    try:
        if not os.path.exists(_OLD_CRASH_LOG):
            return
        if not os.path.exists(CRASH_LOG):
            os.replace(_OLD_CRASH_LOG, CRASH_LOG)
        else:
            with open(_OLD_CRASH_LOG, "r", encoding="utf-8", errors="replace") as f:
                old = f.read()
            with open(CRASH_LOG, "a", encoding="utf-8") as f:
                f.write("\n===== 以下为旧名 bot_crash.log 并入的内容（2026-09-16 改名）=====\n")
                f.write(old)
            os.remove(_OLD_CRASH_LOG)
    except Exception:
        pass


_migrate_crash_log_name()
STOP_FLAG = os.path.join(DATA, "stopped.flag")
# 看门狗逻辑版本：解释器选择等关键行为变更时自增，旧版看门狗会被新版自动接管
WATCHDOG_VER = "2"


def pkg_version() -> str:
    """整包版本号（`agent/version.py` 里的 VERSION）——**接管判据用它，不再只看看门狗版本**。

    为什么（作者 2026-09-18：「不许覆盖解压，一定要直接更新」）：旧包里的更新链可能换完文件却交接失败，
    用户点「一键启动」时新看门狗读到**还活着的旧看门狗**，而两者 `WATCHDOG_VER` 都是 "2" ⇒
    误判成"同版本、自己退出" ⇒ 什么都没发生。改成比**包版本**后：只要包变了就接管，
    旧包写的 `"2"` 这种也天然算"不一致" ⇒ 一定会继续把更新做完。
    """
    try:
        with open(os.path.join(ROOT, "agent", "version.py"), encoding="utf-8") as f:
            m = re.search(r"VERSION\s*=\s*['\"]([^'\"]+)['\"]", f.read())
        return m.group(1) if m else ""
    except Exception:
        return ""
os.makedirs(DATA, exist_ok=True)


def find_pythonw():
    """返回与当前进程同版本的 Python（优先 pythonw）。
    绝不能回退到系统 PATH 的 pythonw：版本可能不同（如系统 3.14 vs 便携 3.10），
    依赖全部装在当前解释器的环境里，版本不一致机器人启动即崩溃（本实例由 onestart
    用同一解释器拉起，直接取 sys.executable 才是版本一致的来源）。"""
    if sys.executable:
        base = os.path.basename(sys.executable).lower()
        if base.endswith("pythonw.exe"):
            return sys.executable
        if base == "python.exe":
            pyw = sys.executable[:-10] + "pythonw.exe"
            if os.path.exists(pyw):
                return pyw
            return sys.executable  # 无 pythonw（便携 embed）→ 用 python.exe，DETACHED 保证无窗口
        return sys.executable
    return "pythonw"


def _read_watchdog_pid():
    """读 data/watchdog.pid，返回 (pid, 版本号)；旧版文件只有一行 pid。"""
    try:
        with open(PID_FILE, "r", encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
        pid = int(lines[0].strip() or 0)
        ver = lines[1].strip() if len(lines) > 1 else ""
        return pid, ver
    except Exception:
        return 0, ""


def main():
    # ⛔ 2026-09-17 修：**先读旧 pid、再写自己的**。原来这里一进来就把自己的 pid 写进 PID_FILE，
    #   紧接着下面那句 `old == os.getpid()` 立刻成立 ⇒ **单实例检查从来没生效过**（死代码）。
    #   实测后果：每跑一次 onestart 就多一个看门狗，多个看门狗各自拉一个机器人 ⇒
    #   抢窗口 + 每 37 秒冒一个新控制台（用户 2026-09-17 连报三次「又起 N 个控制台」）。
    _pre = _read_watchdog_pid()
    #   自己的 pid **等赢下单实例检查之后再写**（见下面赢家分支里的那次写）——否则重复实例
    #   退出时会把 watchpid 指向一个马上要死的进程，「停止机器人」就找不到真看门狗了。
    # 单实例看门狗：已有旧版看门狗 → 结束并接管（旧版可能用错误解释器循环拉起机器人）；
    # 已有同版本 → 本实例退出（多 watchdog 会互相拉起→窗口反复跳出）。
    try:
        import ctypes
        for _i in range(3):
            old, ver = _pre if _i == 0 else _read_watchdog_pid()
            if not old or old == os.getpid():
                break
            alive = False
            if os.name == "nt":
                h = ctypes.windll.kernel32.OpenProcess(0x1000, False, old)
                if h:
                    ctypes.windll.kernel32.CloseHandle(h)
                    alive = True
            else:
                try:
                    os.kill(old, 0)
                    alive = True
                except Exception:
                    pass
            if not alive:
                break
            _mine = pkg_version() or WATCHDOG_VER
            if ver and ver == _mine:
                print("已有看门狗在运行（pid=%d，包版本 %s），本实例退出" % (old, ver))
                return 0
            # ⛔ 只要**包版本对不上**（含旧包写的 "2"）就接管：更新装完没人接替时，
            #    用户"点一下一键启动"就能把这次更新接着做完（不再需要覆盖解压）。
            print("检测到旧实例（pid=%d，记录版本=%r ≠ 本包 %r），结束并由新版接管"
                  % (old, ver, _mine))
            try:
                # ⛔ 不用 `/T`（2026-09-18 修，同一条红线）：看门狗是**机器人的父进程**，
                #    连树一起杀会把**正在干活的机器人本体**也杀掉（旧版看门狗被杀时，
                #    它刚拉起的机器人会一起没）⇒ 只杀看门狗自己。
                subprocess.run(["taskkill", "/F", "/PID", str(old)],
                               creationflags=0x08000000, timeout=10,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
            time.sleep(1)
        with open(PID_FILE, "w", encoding="utf-8") as f:
            # 第二行写**整包版本**（读的人按"格式不认识就当旧版"处理也会接管，见 `_verify_own_pid`）
            f.write("%s\n%s" % (os.getpid(), pkg_version() or WATCHDOG_VER))
    except Exception:
        pass
    except Exception:
        pass
    exe = find_pythonw()
    flags = 0x08000000 | 0x00000008 if os.name == "nt" else 0
    # ── `--delay=<秒>`：**晚一点再开机器人**（2026-09-17 加，给"重启"那一跳用）──
    #    重启时本进程还要 2 秒才退，新机器人要是立刻起来就会撞**单实例锁**当场 exit 3
    #    ⇒ 用户看到的就是"启了但又没有新的"。看门狗自己先到、等几秒再开，最稳。
    _delay = 0
    try:
        for _a in sys.argv[1:]:
            if str(_a).startswith("--delay="):
                _delay = max(_delay, int(str(_a).split("=", 1)[1] or 0))
        _env = os.environ.get("WXAGENT_WATCHDOG_DELAY")
        if _env:
            _delay = max(_delay, int(_env))
    except Exception:
        _delay = 0
    _delay = max(0, min(600, _delay))
    # ── `--takeover`（2026-09-18 加，**更新/重启交接专用**）──────────────────────────────
    #    我是"新的那一个看门狗"：先把**残留的旧看门狗**收掉、清掉会挡住接管的实例证据，
    #    然后再按 `--delay` 开机器人。
    #    为什么要它：作者实测「更新完控制台变『无法访问』、窗口不关、再点一键启动也不弹窗」——
    #    旧版本的进程内 `_kill_watchdog()` 因为 `watchdog.pid` 变两行而**静默失效**（ValueError 被吞），
    #    更新装上了却没人接替。⇒ **更新这条链不再依赖旧的进程内重启**：磁盘上的新看门狗自己做交接。
    if any(str(_a) == "--takeover" for _a in sys.argv[1:]):
        _others = []
        try:
            with open(PID_FILE, "r", encoding="utf-8") as _f:
                _m = re.search(r"\d+", _f.read() or "")
            if _m and int(_m.group(0)) != os.getpid():
                _others.append(int(_m.group(0)))
        except Exception:
            pass
        try:
            _ps = ("Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" | "
                   "Where-Object { $_.CommandLine -like '*watchdog.py*' } | ForEach-Object { $_.ProcessId }")
            _r = subprocess.run(["powershell", "-NoProfile", "-Command", _ps], capture_output=True,
                                creationflags=0x08000000, timeout=25)
            for _t in (_r.stdout or b"").decode("utf-8", "ignore").split():
                if _t.isdigit() and int(_t) != os.getpid():
                    _others.append(int(_t))
        except Exception:
            pass
        for _p in sorted(set(_others)):
            if _p == os.getpid():
                continue
            try:                                   # ⛔ 不用 /T：看门狗是机器人的父进程，连树杀会把机器人一起杀掉
                subprocess.run(["taskkill", "/F", "/PID", str(_p)],
                               capture_output=True, creationflags=0x08000000, timeout=10)
            except Exception:
                pass
        for _f2 in ("bot.lock", "bot.pid"):
            try:
                _p3 = os.path.join(DATA, _f2)
                if os.path.exists(_p3):
                    os.remove(_p3)
            except Exception:
                pass
        print("看门狗：takeover —— 收掉残留看门狗 %s，并清掉实例证据（bot.lock/bot.pid）"
              % (sorted(set(_others)) or "无"))
    if _delay and not os.path.exists(STOP_FLAG):
        print("看门狗：等 %d 秒再接管机器人（重启交接用）" % _delay)
        time.sleep(_delay)
    # 启动了就是"要跑"：清掉手动停止标记（除非刚被停止——一键启动/启动机器人.vbs 先删 flag）
    try:
        if os.path.exists(STOP_FLAG):
            os.remove(STOP_FLAG)
    except Exception:
        pass
    while True:
        # 用户在 5 秒宽限期内的「停止」请求 → 不再拉起，直接退场
        if os.path.exists(STOP_FLAG):
            try:
                os.remove(PID_FILE)
            except Exception:
                pass
            return 0
        try:
            # stderr 重定向到崩溃日志：下次机器人无声挂掉时能查到原因
            # （persona_morph 若 import 失败/启动即崩溃，之前 stderr=DEVNULL 会静默重启，无从排查）
            crash = open(CRASH_LOG, "a", encoding="utf-8")
            crash.write("\n[watchdog] %s 拉起 persona_morph…\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            crash.flush()
            p = subprocess.Popen([exe, os.path.join(ROOT, "scripts", "persona_morph.py")],
                                 cwd=ROOT, creationflags=flags,
                                 stdin=subprocess.DEVNULL, stdout=crash, stderr=crash)
            crash.close()
            p.wait()
            # ⛔ 2026-09-17 加：机器人 **静默死掉**（runtime.log 里一句遗言都没有）时必须能分清
            #   它是"自己干净退出"（0）还是"被系统/别人杀掉 / 原生崩溃"（0xC0000005、0xC0000409…）。
            #   之前这里丢掉退出码，导致只能靠猜（当晚为此白烧了半轮）。
            try:
                with open(CRASH_LOG, "a", encoding="utf-8") as _c:
                    _c.write("[watchdog] persona_morph 退出：code=%s (0x%08X)\n"
                             % (p.returncode, (p.returncode or 0) & 0xFFFFFFFF))
            except Exception:
                pass
        except Exception as e:
            try:
                with open(CRASH_LOG, "a", encoding="utf-8") as crash:
                    crash.write("[watchdog] 异常：%s\n" % e)
            except Exception:
                pass
        time.sleep(5)


if __name__ == "__main__":
    main()
