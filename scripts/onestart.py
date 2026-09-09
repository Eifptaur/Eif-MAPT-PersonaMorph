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
# GUI 模式（WX_GUI=1）：stdout 只输出 ASCII 事件行（@@PHASE/@@PROG/@@DONE/@@FAIL/@@REQ_SHORTCUT），
# 由 scripts/installer.ps1 安装器窗口解析驱动进度条；详细日志仍写 onestart.log。
GUI = os.environ.get("WX_GUI") == "1"


def evt(kind, *args):
    """GUI 事件行（ASCII）：stdout（安装器读取）+ 日志文件（installer 轮询日志，无事件线程）。"""
    line = "@@%s:%s" % (kind, ":".join(str(a) for a in args))
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if not GUI:
        try:
            print(line, flush=True)
        except Exception:
            pass


_LAST_PROG = {}


def _prog(prefix, done, total):
    """进度行（每 5% 打印一次，避免刷屏；done==total 必打完成行）。"""
    try:
        pct = int(done * 100 / max(1, total))
        if GUI:
            key = {"依赖检查": "deps", "自检": "selftest", "安装依赖": "install"}.get(prefix, "step")
            evt("PROG", key, done, total)
            return
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


def _ask_shortcut():
    """启动完成弹窗：桌面无「一键启动」快捷方式时，弹自定义窗口询问是否创建
    （图标+标题+说明+彩色按钮，不是系统简陋消息框）。选择「立即创建」则生成 lnk。"""
    try:
        desktop = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
        lnk = os.path.join(desktop, "一键启动 wx-agent.lnk")
        if os.path.exists(lnk):
            log("桌面快捷方式已存在，跳过询问。")
            return
        if GUI:
            evt("REQ_SHORTCUT")
            return
        icon_png = os.path.join(ROOT, "assets", "app-icon.png")
        icon_ico = os.path.join(ROOT, "assets", "app.ico")
        vbs = os.path.join(ROOT, "一键启动.vbs")
        if not (os.path.exists(vbs) and os.path.exists(icon_ico)):
            return
        ps = r'''
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$f = New-Object System.Windows.Forms.Form
$f.Text = 'wx-agent 启动完成'
$f.StartPosition = 'CenterScreen'
$f.FormBorderStyle = 'FixedDialog'
$f.MaximizeBox = $false; $f.MinimizeBox = $false
$f.BackColor = [System.Drawing.Color]::FromArgb(246,248,252)
$f.ClientSize = New-Object System.Drawing.Size(470, 244)
try { $f.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon('__ICONICO__') } catch {}
$pic = New-Object System.Windows.Forms.PictureBox
try { $pic.Image = [System.Drawing.Image]::FromFile('__ICONPNG__') } catch {}
$pic.SizeMode = 'Zoom'
$pic.Location = New-Object System.Drawing.Point(26, 26)
$pic.Size = New-Object System.Drawing.Size(76, 76)
$f.Controls.Add($pic)
$l1 = New-Object System.Windows.Forms.Label
$l1.Text = 'wx-agent 启动完成'
$l1.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 15, [System.Drawing.FontStyle]::Bold)
$l1.Location = New-Object System.Drawing.Point(118, 26)
$l1.AutoSize = $true
$f.Controls.Add($l1)
$l2 = New-Object System.Windows.Forms.Label
$l2.Text = '机器人已启动，Web 控制台已打开。' + [char]10 + '之后双击桌面快捷方式即可一键启动。' + [char]10 + [char]10 + '是否在桌面创建「一键启动」快捷方式？'
$l2.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 9.5)
$l2.ForeColor = [System.Drawing.Color]::FromArgb(76,92,118)
$l2.Location = New-Object System.Drawing.Point(118, 70)
$l2.Size = New-Object System.Drawing.Size(330, 92)
$f.Controls.Add($l2)
$ok = New-Object System.Windows.Forms.Button
$ok.Text = '立即创建'
$ok.Size = New-Object System.Drawing.Size(150, 36)
$ok.Location = New-Object System.Drawing.Point(296, 190)
$ok.FlatStyle = 'Flat'
$ok.BackColor = [System.Drawing.Color]::FromArgb(64, 140, 255)
$ok.ForeColor = [System.Drawing.Color]::White
$ok.DialogResult = 'OK'
$f.Controls.Add($ok)
$no = New-Object System.Windows.Forms.Button
$no.Text = '暂不'
$no.Size = New-Object System.Drawing.Size(90, 36)
$no.Location = New-Object System.Drawing.Point(190, 190)
$no.FlatStyle = 'Flat'
$no.DialogResult = 'Cancel'
$f.Controls.Add($no)
$f.AcceptButton = $ok
$f.CancelButton = $no
if ($f.ShowDialog() -eq 'OK') {
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\一键启动 wx-agent.lnk')
    $s.TargetPath = '__VBS__'
    $s.WorkingDirectory = '__DIR__'
    $s.IconLocation = '__ICONICO__'
    $s.Save()
}
'''
        ps = ps.replace("__ICONPNG__", icon_png).replace("__ICONICO__", icon_ico) \
               .replace("__VBS__", vbs).replace("__DIR__", ROOT)
        import base64
        enc = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
        import subprocess as _sp
        _sp.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-WindowStyle", "Hidden", "-EncodedCommand", enc],
                  creationflags=0x08000000, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        log("已弹窗询问：是否创建桌面快捷方式（自动创建则双击即可启动）")
    except Exception as e:
        log("快捷方式询问失败：%s" % e)


def popup_fail(reason, tail=""):
    """失败时弹窗给出具体原因（获取不到终端时用户也能看懂）。"""
    if GUI:
        evt("FAIL", "failed")
        return
    try:
        import ctypes
        msg = ("wx-agent 一键启动失败：%s\n\n" % reason)
        if tail:
            msg += "—— 最近日志（看前几行即可定位）：\n" + tail[-1500:]
        ctypes.windll.user32.MessageBoxW(0, msg, "wx-agent 启动失败", 0x10)
    except Exception:
        pass


_BROWSER_MARK = None


def _mark_browser_opened():
    try:
        with open(os.path.join(LOG_DIR, "browser_opened.txt"), "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except Exception:
        pass


def _try_browser_lock(seconds=90):
    """原子抢占"打开浏览器"锁：并发下只有一个进程成功（O_CREAT|O_EXCL 不可重入）。"""
    mk = os.path.join(LOG_DIR, "browser_opened.lock")
    try:
        if os.path.exists(mk):
            try:
                with open(mk, encoding="utf-8") as f:
                    t = float((f.read() or "0").strip() or 0)
                if time.time() - t < seconds:
                    return False      # 别人刚打开（锁未过期）
            except Exception:
                pass
            try:
                os.remove(mk)
            except Exception:
                pass
        fd = os.open(mk, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(time.time()).encode("ascii", "replace"))
        os.close(fd)
        return True                   # 拿到锁 → 我来打开
    except FileExistsError:
        return False
    except Exception:
        return True                   # 极端情况：放开（避免全都不打开）


def _build_tag_local():
    try:
        import datetime
        mt = os.path.getmtime(os.path.join(ROOT, "agent", "console_html.py"))
        return "b." + datetime.datetime.fromtimestamp(mt).strftime("%m%d-%H%M")
    except Exception:
        return "b?"


def _bot_opens_console():
    """机器人侧是否承担"打开控制台"职责（server.auto_open_browser=True 即由它单点执行）。"""
    try:
        from agent.config import get_config
        return bool(get_config().get("server", {}).get("auto_open_browser", True))
    except Exception:
        return True


def _probe_browser_was_opened():
    """浏览器是否已被打开（原子锁存在且 90 秒内 = 已执行过一次打开）。"""
    try:
        mk = os.path.join(LOG_DIR, "browser_opened.lock")
        if os.path.exists(mk):
            with open(mk, encoding="utf-8") as f:
                t = float((f.read() or "0").strip() or 0)
            return time.time() - t < 90
    except Exception:
        pass
    return False


def _probe_running_instance(timeout=2):
    """探测控制台：'same'=当前版本在跑 / 'old'=旧版本在跑 / 'none'=无实例（端口从 config 读取，与启动器一致）。"""
    try:
        import urllib.request
        import json as _j
        _port = 3210
        try:
            from agent.config import get_config
            _port = int(get_config().get("server", {}).get("port") or 3210)
        except Exception:
            pass
        with urllib.request.urlopen("http://127.0.0.1:%d/api/version" % _port, timeout=timeout) as _r:
            _d = _j.loads(_r.read().decode("utf-8", "replace"))
            return "same" if str(_d.get("ver") or "") == _build_tag_local() else "old"
    except Exception:
        return "none"


def _kick_old_instance():
    """踢掉旧版本实例（3210 的 wx_agent/watchdog + 启动器/关闭器进程）。"""
    try:
        out = subprocess.check_output(
            'wmic process where "name like \'python%\' or name like \'cscript%\'" get processid,commandline '
            '/format:csv', shell=True, text=True, errors="replace")
        for line in out.splitlines():
            if any(k in line for k in ("wx_agent.py", "watchdog.py", "onestart.py")) and "plugin" not in line:
                parts = line.rsplit(",", 1)
                if parts and parts[-1].strip().isdigit():
                    subprocess.run(["taskkill", "/F", "/PID", parts[-1].strip()],
                                   capture_output=True, creationflags=0x08000000)
    except Exception:
        pass
    time.sleep(1.5)


def _open_current_console():
    """同版本已在运行：走原子锁拿到才打开（否则不重复开第二个）。"""
    try:
        if not _try_browser_lock():
            log("浏览器已打开（同版本控制台在运行），本次不重复打开。")
            return True
        from agent.config import get_config
        sc = get_config().get("server", {})
        url = "http://127.0.0.1:%s/?token=%s" % (int(sc.get("port") or 3210), str(sc.get("token") or ""))
        return _open_console(url)
    except Exception as e:
        log("打开控制台失败：%s" % e)
        return False


def _open_console(url, browser_path=""):
    """打开控制台浏览器：配置/探测的浏览器 exe 优先，否则系统默认（start）。"""
    try:
        from agent.util import mask_url_token, pick_browser
        bp = pick_browser(browser_path)
        if bp:
            subprocess.Popen([bp, url], creationflags=0x08000000)
            log("已打开浏览器：%s" % bp)
            _mark_browser_opened()
            return True
    except Exception:
        pass
    try:
        import webbrowser
        webbrowser.open(url)
        log("已打开浏览器（系统默认）")
        _mark_browser_opened()
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

    # 自动检测旧实例：同版本→直接开控制台；旧版本→踢掉再启动新版（杜绝 404/旧代码）
    if not check_only:
        try:
            _st = _probe_running_instance()
            if _st == "same":
                log("检测到当前版本控制台已在运行，直接打开浏览器（不再重复启动）。")
                evt("PHASE", "boot")
                _open_current_console()
                evt("DONE")
                return 0
            if _st == "old":
                log("检测到旧版本实例（/api/version 指纹不同），自动踢出旧进程后启动新版…")
                evt("PHASE", "boot")
                _kick_old_instance()
                log("旧实例已清理，继续一键启动。")
        except Exception:
            pass

    log("[1/3] 依赖检查（缺则自动安装；已装自动跳过）")
    py = sys.executable or "python"

    # 1. 依赖（检查表 14 项逐项百分比；安装阶段由 pip 自带百分比条显示）
    deps_done = [0]
    evt("PHASE", "deps")

    # 依赖安装实时进度：统计 requirements 包数作为总量，逐包 +1（pip Collecting/Downloading/安装缺失 均计）
    try:
        _req_n = len([_l for _l in open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8")
                      if _l.strip() and not _l.strip().startswith("#")])
    except Exception:
        _req_n = 21
    deps_install = [0]

    def _deps_progress(ln):
        if ln.startswith("OK"):
            deps_done[0] += 1
            _prog("依赖检查", min(deps_done[0], 14), 14)
        elif ("安装缺失" in ln or "正在安装" in ln or ln.startswith("Collecting")
                or ln.startswith("Downloading")):
            deps_install[0] += 1
            _prog("安装依赖", min(deps_install[0], _req_n), _req_n)

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
    evt("PHASE", "selftest")
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
    evt("PHASE", "boot")
    if check_only:
        log("验证模式：仅执行依赖与自检，不拉起机器人（WX_ONESTART_CHECK=1）。")
        log("一键启动（验证）通过。")
        if GUI:
            evt("DONE")
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
                    # 单点打开策略：打开动作由机器人侧（webui 就绪后、原子锁保护）唯一执行；
                    # 启动器只等待就绪，绝不自己打开（避免与 bot 抢开 → 双开）。
                    # 若 bot 未打开（探测锁未命中且长期无人开），此处兜底触发（仍走同一把原子锁）。
                    if not _bot_opens_console():
                        pass  # bot 已处理（锁表示已开/或 bot 将开），无需兜底
                    _opened_by_bot = _probe_browser_was_opened()
                    if not _opened_by_bot:
                        try:
                            _open_console(_url, _bpath)
                        except Exception as e:
                            log("控制台已就绪但打开浏览器失败（请手动访问 %s）：%s" % (mask_url_token(_url), e))
                    else:
                        log("浏览器已由机器人侧打开（单点执行），启动器不再打开。")
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
    _ask_shortcut()
    if GUI:
        evt("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
