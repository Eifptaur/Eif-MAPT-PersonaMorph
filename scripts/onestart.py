# -*- coding: utf-8 -*-
"""一键启动：依赖检查 →（缺则自动安装）→ 自检 → 启动机器人（无窗口）。

由 一键启动.vbs 以 pythonw 隐藏调用；完整进度写入 logs/onestart.log，
用户可在 Web 控制台「运行日志」或日志文件查看。
"""
from __future__ import annotations
import os
import sys
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs")
LOG_PATH = os.path.join(LOG_DIR, "onestart.log")
os.makedirs(LOG_DIR, exist_ok=True)


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def run_visible(cmd, timeout=900):
    """运行一个可能显示输出的命令（安装/自检），捕获输出进日志。"""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=0x08000000)
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        if out:
            log("  " + out.strip().replace("\n", "\n  ")[-3000:])
        if err:
            log("  [err] " + err.strip().replace("\n", "\n  ")[-1000:])
        return r.returncode == 0, (out + "\n" + err)
    except Exception as e:
        log("命令失败: %s" % e)
        return False, str(e)


def popup_fail(reason, tail=""):
    """失败时弹窗给出具体原因（获取不到终端时用户也能看懂）。"""
    try:
        import ctypes
        msg = ("wx-agent 一键启动失败：%s\n\n" % reason)
        if tail:
            msg += "—— 最近日志（看前几行即可定位）：\n" + tail[-1500:]
        ctypes.windll.user32.MessageBoxW(0, msg, "wx-agent 启动失败", 0x10)
    except Exception:
        pass


def main():
    log("=" * 46)
    log("一键启动开始（1/3 依赖检查）")
    py = sys.executable or "python"

    # 1. 依赖
    ok, tail = run_visible([py, "-X", "utf8", os.path.join(ROOT, "scripts", "setup_deps.py")])
    if not ok:
        log("[失败] 依赖未就绪，请查看上方日志后重试。")
        popup_fail("依赖安装未通过（见最近日志）", tail)
        log("一键启动结束（失败：依赖）")
        return 1
    log("依赖检查通过 ✔")

    # 2. 自检
    log("一键启动（2/3 自检 53 项）")
    ok, tail = run_visible([py, "-X", "utf8", os.path.join(ROOT, "scripts", "selftest.py")])
    if not ok:
        # 提取失败项行（FAIL 开头）供提示
        lines = [ln for ln in str(tail).splitlines() if "FAIL" in ln][:8]
        hint = ("\n".join(lines) if lines else "：多数是 依赖未装全 / 微信未安装 / 网络问题，见日志")
        log("[失败] 自检未全部通过（通常是未填 API Key；到控制台首次向导填写）。")
        popup_fail("自检未通过（失败项：%s）" % hint, tail)
        log("一键启动结束（失败：自检）")
        return 1
    log("自检全部通过 ✔")

    # 3. 启动机器人（复用 watchdog）；已有实例在跑 → 直接打开控制台（不再重复拉起）
    log("一键启动（3/3 启动机器人）")
    watchdog = os.path.join(ROOT, "scripts", "watchdog.py")
    existing = None
    try:
        _lk = os.path.join(ROOT, "data", "bot.lock")
        if os.path.exists(_lk):
            with open(_lk, "r", encoding="utf-8") as f:
                pid = int((f.read().strip() or "0"))
            if pid and os.name == "nt":
                import ctypes
                h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                if h:
                    ctypes.windll.kernel32.CloseHandle(h)
                    existing = pid
    except Exception:
        existing = None
    if existing:
        log("检测到机器人已在运行（pid=%s）→ 打开控制台，不再重复启动。%s" % (
            existing, "如想重启请先「停止机器人」."))
        try:
            import json as _j
            cfg = _j.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
            token = str(cfg.get("server", {}).get("token") or "")
            port = int(cfg.get("server", {}).get("port") or 3210)
            url = "http://127.0.0.1:%d" % port + (("/?token=" + token) if token else "")
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
        return 0
    try:
        subprocess.Popen([py, watchdog], creationflags=0x08000000,
                         cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log("启动失败: %s" % e)
        return 1
    log("机器人已启动 ✔（首次运行会自动打开 Web 控制台）")
    log("一键启动完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
