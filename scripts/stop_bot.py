# -*- coding: utf-8 -*-
"""停止 wx-agent（无窗口）：按 PID 结束机器人 + 看门狗，零 PowerShell 依赖。

pid 文件由机器人(wx_agent.py)/看门狗(看门狗.py)启动时写入。
若 PID 文件缺失（升级前的老进程），回退到按命令行特征查找并结束。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def kill_pid(pid):
    if not pid:
        return False
    try:
        r = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, creationflags=0x08000000, timeout=10)
        return r.returncode == 0 and b"not found" not in r.stderr.lower()
    except Exception:
        return False


def read_pid(name):
    try:
        with open(os.path.join(DATA, name), "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


def kill_by_cmdline(marker):
    """回退：用 wmic 按命令行特征找 PID（wmic 不可用时直接放弃）。"""
    pids = []
    try:
        r = subprocess.run(
            ["wmic", "process", "where",
             "name like '%python%.exe' and commandline like '%{0}%'".format(marker),
             "get", "processid", "/value"],
            capture_output=True, creationflags=0x08000000, timeout=15)
        for line in r.stdout.decode("utf-8", "ignore").splitlines():
            line = line.strip()
            if line.startswith("ProcessId="):
                v = line.split("=", 1)[1].strip()
                if v.isdigit() and int(v) > 0:
                    pids.append(int(v))
    except Exception:
        pass
    return pids


def main():
    killed = 0
    for name, marker in (("bot.pid", "wx_agent.py"), ("watchdog.pid", "watchdog")):
        pid = read_pid(name)
        if pid and pid != os.getpid() and kill_pid(pid):
            killed += 1
            try:
                os.remove(os.path.join(DATA, name))
            except Exception:
                pass
    # 回退：老进程没有 pid 文件时按命令行特征找
    try:
        for pid in kill_by_cmdline("wx_agent.py"):
            if pid != os.getpid() and kill_pid(pid):
                killed += 1
    except Exception:
        pass
    print("已结束 %d 个进程" % killed)
    if killed == 0:
        print("未发现正在运行的机器人进程")
    sys.exit(0 if killed else 1)


if __name__ == "__main__":
    main()
