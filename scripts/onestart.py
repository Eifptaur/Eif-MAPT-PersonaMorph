# -*- coding: utf-8 -*-
"""一键启动：依赖检查 →（缺则自动安装）→ 自检 → 启动机器人（可见进度窗口）。

由 一键启动.vbs 以可见 console 调用：安装/自检输出实时显示在窗口
（下载百分比、依赖安装进度），全部成功后进程退出、窗口自动关闭；
失败则弹窗说明原因。完整进度同步写入 logs/onestart.log。
"""
from __future__ import annotations
import os
import sys
import subprocess
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
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


_LAST_PROG = {}


def _prog(prefix, done, total):
    """进度行（每 5% 打印一次，避免刷屏；done==total 必打完成行）。"""
    try:
        pct = int(done * 100 / max(1, total))
        if _LAST_PROG.get(prefix) == pct:
            return
        if pct % 5 != 0 and done < total:
            return
        _LAST_PROG[prefix] = pct
        try:
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        except Exception:
            bar = ""
        print("  ┃ %s %3d%% [%s] (%d/%d)" % (prefix, pct, bar, done, total), flush=True)
    except Exception:
        pass


def run_stream(cmd, timeout=900, on_line=None):
    """运行子命令并实时转发输出到窗口（同时截留尾部进日志）。
    子命令以 -u 启动保证输出立即到达；无输出超 30 秒打印心跳行，
    安装/下载期间窗口不会显得"卡住"。on_line 用于统计行做阶段百分比。"""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=0x08000000)
    except Exception as e:
        log("命令启动失败: %s" % e)
        return False, str(e)
    parts = []
    done = threading.Event()

    def _reader():
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                txt = chunk.decode("utf-8", "replace")
                parts.append(txt)
                if on_line:
                    try:
                        for ln in txt.splitlines():
                            on_line(ln)
                    except Exception:
                        pass
                try:
                    sys.stdout.write(txt)
                    sys.stdout.flush()
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            done.set()

    threading.Thread(target=_reader, daemon=True).start()
    t0 = time.time()
    last_out = t0
    rc = None
    while rc is None:
        if proc.poll() is not None and done.is_set():
            rc = proc.poll()
            break
        if time.time() - t0 > timeout:
            proc.kill()
            rc = proc.wait()
            log("[超时] 命令超过 %d 秒未完成，已终止。" % timeout)
            break
        if time.time() - last_out > 30:
            log("  ...仍在运行（已 %d 秒，通常为下载/安装中，请耐心等待）"
                % int(time.time() - t0))
            last_out = time.time()
        time.sleep(1)
    done.wait(5)
    tail = "".join(parts)
    if tail.strip():
        log("  " + tail.strip().replace("\n", "\n  ")[-3000:])
    return rc == 0, tail


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


def _open_console(url, browser_path=""):
    """打开控制台浏览器：配置/探测的浏览器 exe 优先，否则系统默认（start）。"""
    try:
        from agent.util import mask_url_token, pick_browser
        bp = pick_browser(browser_path)
        if bp:
            subprocess.Popen([bp, url], creationflags=0x08000000)
            log("已打开浏览器：%s" % bp)
            return True
    except Exception:
        pass
    try:
        import webbrowser
        webbrowser.open(url)
        log("已打开浏览器（系统默认）")
        return True
    except Exception as e:
        log("打开浏览器失败：%s（请手动访问 %s）" % (e, mask_url_token(url)))
        return False


def main():
    check_only = (os.environ.get("WX_ONESTART_CHECK") == "1") or ("--check-only" in sys.argv[1:])
    log("")
    log("╔══════════════════════════════════════════════╗")
    log("║        wx-agent 一键启动（全程进度）         ║")
    log("╚══════════════════════════════════════════════╝")
    log("[1/3] 依赖检查（缺则自动安装；已装自动跳过）")
    py = sys.executable or "python"

    # 1. 依赖（检查表 14 项逐项百分比；安装阶段由 pip 自带百分比条显示）
    deps_done = [0]

    def _deps_progress(ln):
        if ln.startswith("OK"):
            deps_done[0] += 1
            _prog("依赖检查", min(deps_done[0], 14), 14)
        elif ("安装缺失" in ln or "正在安装" in ln) and _LAST_PROG.get("安装依赖") is None:
            _prog("安装依赖", 14, 14)

    ok, tail = run_stream([py, "-X", "utf8", "-u", os.path.join(ROOT, "scripts", "setup_deps.py")],
                          on_line=_deps_progress)
    if not ok:
        log("[失败] 依赖未就绪，请查看上方日志后重试。")
        popup_fail("依赖安装未通过（见最近日志）", tail)
        log("一键启动结束（失败：依赖）")
        return 1
    log("依赖检查通过 ✔")

    # 2. 自检（55 项逐项百分比；WARN 提示项也计入完成）
    log("")
    log("[2/3] 环境自检 55 项（每项实时百分比见下）")
    st_done = [0]

    def _st_progress(ln):
        if ln.startswith("OK") or ln.startswith("FAIL") or ln.startswith("WARN"):
            st_done[0] += 1
            _prog("自检", st_done[0], 55)

    ok, tail = run_stream([py, "-X", "utf8", "-u", os.path.join(ROOT, "scripts", "selftest.py")],
                          on_line=_st_progress)
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
    if check_only:
        log("验证模式：仅执行依赖与自检，不拉起机器人（WX_ONESTART_CHECK=1）。")
        log("一键启动（验证）通过。")
        return 0
    log("[3/3] 启动机器人（等待控制台就绪，随后自动打开浏览器）")
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
    log("机器人已启动 ✔（等待控制台就绪，随后自动打开浏览器；完成后本窗口自动关闭）")
    # 轮询等控制台就绪再打开浏览器：webui 会因微信布局校准等延迟就绪，只试一次会漏掉
    try:
        import json as _j
        cfg = _j.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
        _tok = str(cfg.get("server", {}).get("token") or "")
        _port = int(cfg.get("server", {}).get("port") or 3210)
        _bpath = str((cfg.get("server", {}) or {}).get("browser_path") or "")
        _url = "http://127.0.0.1:%d" % _port + (("/?token=" + _tok) if _tok else "")
        t0 = time.time()
        opened = False
        _nxt_hint = 15
        while time.time() - t0 < 120:
            try:
                import socket as _sock
                _s = _sock.socket()
                _s.settimeout(1.5)
                _up = _s.connect_ex(("127.0.0.1", _port)) == 0
                _s.close()
                if _up:
                    try:
                        _open_console(_url, _bpath)
                    except Exception as e:
                        log("控制台已就绪但打开浏览器失败（请手动访问 %s）：%s" % (mask_url_token(_url), e))
                    opened = True
                    break
            except Exception:
                pass
            spent = int(time.time() - t0)
            if spent >= _nxt_hint:
                log("等待控制台就绪（已 %d 秒，微信/控制台初始化中）…" % spent)
                _nxt_hint += 15
            time.sleep(2)
        if not opened:
            log("120 秒内控制台仍未就绪 —— 请查看 logs\\wx_agent.log / data\\bot_crash.log")
            try:
                _open_console(_url, _bpath)
            except Exception:
                pass
    except Exception:
        pass
    log("一键启动完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
