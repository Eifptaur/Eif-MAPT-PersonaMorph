# -*- coding: utf-8 -*-
"""wx-agent 看门狗（无窗口）：机器人崩溃/退出后自动重启。

用 pythonw 运行、零 PowerShell 依赖（无 cmd、无 powershell 窗口）。
启动时把自己的 PID 写到 data/watchdog.pid，供 停止机器人 读取。
"""
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
PID_FILE = os.path.join(DATA, "watchdog.pid")
CRASH_LOG = os.path.join(DATA, "bot_crash.log")
STOP_FLAG = os.path.join(DATA, "stopped.flag")
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


def main():
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
    # 单实例看门狗：已有 watchdog 存活（多 watchdog 会互相拉起→窗口反复跳出）→ 本实例退出
    try:
        import ctypes
        if os.path.exists(PID_FILE):
            with open(PID_FILE, "r", encoding="utf-8") as f:
                old = int(f.read().strip() or 0)
            if old and old != os.getpid():
                if os.name == "nt":
                    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, old)
                    if h:
                        ctypes.windll.kernel32.CloseHandle(h)
                        print("已有看门狗在运行（pid=%d），本实例退出" % old)
                        return 0
                else:
                    try:
                        os.kill(old, 0)
                        return 0
                    except Exception:
                        pass
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
    exe = find_pythonw()
    flags = 0x08000000 | 0x00000008 if os.name == "nt" else 0
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
            # （wx_agent 若 import 失败/启动即崩溃，之前 stderr=DEVNULL 会静默重启，无从排查）
            crash = open(CRASH_LOG, "a", encoding="utf-8")
            crash.write("\n[watchdog] %s 拉起 wx_agent…\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            crash.flush()
            p = subprocess.Popen([exe, os.path.join(ROOT, "wx_agent.py")],
                                 cwd=ROOT, creationflags=flags,
                                 stdin=subprocess.DEVNULL, stdout=crash, stderr=crash)
            crash.close()
            p.wait()
        except Exception as e:
            try:
                with open(CRASH_LOG, "a", encoding="utf-8") as crash:
                    crash.write("[watchdog] 异常：%s\n" % e)
            except Exception:
                pass
        time.sleep(5)


if __name__ == "__main__":
    main()
