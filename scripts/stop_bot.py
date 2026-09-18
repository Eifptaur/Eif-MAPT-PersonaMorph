# -*- coding: utf-8 -*-
"""停止 Persona Morph（无窗口）：按 PID 结束机器人 + 看门狗，零 PowerShell 依赖。

pid 文件由机器人(persona_morph.py)/看门狗(看门狗.py)启动时写入。
若 PID 文件缺失（升级前的老进程），回退到按命令行特征查找并结束。
"""
import os
import subprocess
import sys
import time  # 第 59 行 time.strftime 需要；缺失会导致 stop_bot 一运行即崩溃（"停止机器人文件"失效）

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
    """读 pid 文件里的**第一个整数**。

    ⛔ 2026-09-18 修（作者实测「点了一键关闭，再点一键启动，仍然不起窗口」）：`watchdog.pid` 现在是
    **两行**（`<pid>\\n<看门狗版本号>`，见 watchdog.py:120），原来 `int(f.read().strip())` 直接
    **ValueError** ⇒ 返回 None ⇒ 看门狗杀不掉（wmic 在新版 Windows 又常常不可用）⇒ 用户以为
    "关掉了"，其实残留实例还在，接着点一键启动就互抢 ⇒ 表现就是"不起窗口"。
    """
    try:
        import re as _re
        with open(os.path.join(DATA, name), "r", encoding="utf-8") as f:
            m = _re.search(r"\\d+", f.read() or "")
        return int(m.group(0)) if m else None
    except Exception:
        return None


def kill_by_cmdline(marker):
    """回退：按命令行特征找 PID —— wmic 优先，**再补一条 PowerShell**（新版 Windows 已移除 wmic）。"""
    pids = _kill_by_wmic(marker)
    if not pids:
        pids = _kill_by_ps(marker)
    return pids


def _kill_by_ps(marker):
    try:
        ps = ('Get-CimInstance Win32_Process -Filter "Name=\'pythonw.exe\' or Name=\'python.exe\'" | '
              "Where-Object { $_.CommandLine -like '*%s*' } | ForEach-Object { $_.ProcessId }" % marker)
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, creationflags=0x08000000, timeout=25)
        out = (r.stdout or b"").decode("utf-8", "ignore")
        return [int(t) for t in out.split() if t.isdigit() and int(t) > 0]
    except Exception:
        return []


def _kill_by_wmic(marker):
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
    failed = 0
    # 标记「手动停止」：watchdog 见标记不再自动拉起
    try:
        with open(os.path.join(DATA, "stopped.flag"), "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass
    for name, marker in (("bot.pid", "persona_morph.py"), ("watchdog.pid", "watchdog")):
        pid = read_pid(name)
        if pid and pid != os.getpid() and kill_pid(pid):
            killed += 1
            try:
                os.remove(os.path.join(DATA, name))
            except Exception:
                pass
        elif pid and pid != os.getpid():
            failed += 1
    # 回退：老进程没有 pid 文件时按命令行特征找
    try:
        for pid in kill_by_cmdline("persona_morph.py"):
            if pid != os.getpid() and kill_pid(pid):
                killed += 1
    except Exception:
        pass
    # 2026-09-18 加：看门狗也得按命令行兜一遍（它不在上面那份名单里时），并把"开窗锁/实例证据"
    # 一起清掉——残留的锁会让下一次「一键启动」判"已经开着"从而什么都不弹（用户实测就是这个）。
    try:
        for pid in kill_by_cmdline("watchdog.py"):
            if pid != os.getpid() and kill_pid(pid):
                killed += 1
    except Exception:
        pass
    for _p in (os.path.join(ROOT, "logs", "browser_opened.lock"),
               os.path.join(DATA, "bot.lock"),
               os.path.join(DATA, "watchdog.pid")):
        try:
            if os.path.exists(_p):
                os.remove(_p)
        except Exception:
            pass
    print("已结束 %d 个进程" % killed)
    if failed:
        print("有 %d 个进程未能结束（权限或已退出），请重试" % failed)
        sys.exit(1)
    if killed == 0:
        print("未发现正在运行的机器人进程（已处于停止状态）")
    sys.exit(0)


if __name__ == "__main__":
    main()
