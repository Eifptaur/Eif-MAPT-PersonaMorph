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
sys.path.insert(0, ROOT)

# 启动即治理日志体积（主日志在 persona_morph 里轮转，这里处理"只追加"的那几份）
try:
    from agent import log_housekeeping as _lh
    _lh.sweep(ROOT)
except Exception:
    pass
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
    last_out = [time.time()]        # 读者线程负责刷新 ⇒ 主循环按"空闲"判超时

    def _reader():
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                last_out[0] = time.time()
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
    last_hb = t0
    rc = None
    while rc is None:
        if proc.poll() is not None and done.is_set():
            rc = proc.poll()
            break
        # 2026-09-16 改：**按"空闲"判超时，不设总时长上限**。慢网装依赖十几分钟是正常的，
        # 原来按起始时刻算总时长（默认 900 秒）会把正常等待直接杀成"一键启动失败"。
        if time.time() - last_out[0] > timeout:
            proc.kill()
            rc = proc.wait()
            log("[超时] 连续 %d 秒没有任何输出，已终止（再点一次会接着装，已下完的不会重下）。" % timeout)
            break
        if time.time() - last_hb > 30:
            log("  ...仍在运行（已 %d 秒，通常为下载/安装中，请耐心等待）"
                % int(time.time() - t0))
            last_hb = time.time()
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
        lnk = os.path.join(desktop, "一键启动 Persona Morph.lnk")
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
$f.Text = 'Persona Morph 启动完成'
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
$l1.Text = 'Persona Morph 启动完成'
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
    $s = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\一键启动 Persona Morph.lnk')
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
        msg = ("群相 一键启动失败：%s\n\n" % reason)
        if tail:
            msg += "—— 最近日志（看前几行即可定位）：\n" + tail[-1500:]
        ctypes.windll.user32.MessageBoxW(0, msg, "Persona Morph 启动失败", 0x10)
    except Exception:
        pass


_BROWSER_MARK = None


def _mark_browser_opened():
    try:
        with open(os.path.join(LOG_DIR, "browser_opened.txt"), "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except Exception:
        pass


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


def _probe_console_window():
    """**屏幕上真的已经有一个控制台窗口了吗**——是则返回句柄，否则 0（2026-09-18 改口径：不再只信锁）。

    ⛔ 原实现读 `console_lock_fresh(90)` —— 那是"90 秒内有人开过"，**不代表窗口还在**。
    作者在另一台机器实测：「更新之后，一键启动不弹窗口，还得再点一次」：更新完新机器人起来时开过一次窗
    并落锁，紧接着点一键启动 ⇒ 这里判"机器人侧已打开" ⇒ 启动器不开、而窗口其实没影 ⇒ 一屏空白；
    等 90 秒锁过期再点才出来。⇒ 现在**以真窗口为准**（`find_console_window()` + `IsWindow`）。
    ⛔ 2026-09-19 再修：判据本身（`notify_ui.classify_console_window`）原来按标题子串认窗，
    会把启动器自己的窗「群相 一键启动」当成控制台 ⇒ 同一个症状复发。现已收紧到唯一标题「群相 控制台」，
    并在这里**把看到的窗口标题一起带出去**，写在日志里当证据（函数返回句柄，标题由调用方取）。
    """
    try:
        from agent.notify_ui import find_console_window
        h = int(find_console_window() or 0)
        if not h:
            return 0
        import ctypes
        return h if ctypes.windll.user32.IsWindow(ctypes.c_void_p(h)) else 0
    except Exception:
        return 0


def _probe_browser_was_opened():
    """兼容旧调用点：只问"有没有"。"""
    return bool(_probe_console_window())


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


def _enum_old_procs_ps():
    """主路：PowerShell `Get-CimInstance Win32_Process` 枚举 python/cscript 进程 → [(pid, 命令行)]。

    ⛔ 2026-09-20 修 V7：`wmic` 在 Win11 24H2 起**已被系统移除**（本机就没有），原先只靠 wmic +
    `except Exception: pass` ⇒ 在这台机器上"踢旧实例"等于没做、连一行日志都没有。写法照
    `scripts/watchdog.py:183-189` 那份已跑通的。
    """
    _ps = ("Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe' or Name='cscript.exe'\" | "
           "Where-Object { $_.CommandLine } | ForEach-Object { [string]$_.ProcessId + \"`t\" + $_.CommandLine }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", _ps], capture_output=True,
                       creationflags=0x08000000, timeout=25)
    rc = int(getattr(r, "returncode", 1) or 0)
    if rc != 0:
        raise RuntimeError("powershell 退出码 %s" % rc)
    raw = getattr(r, "stdout", b"") or b""
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", "ignore")
    out = []
    for line in str(raw).splitlines():
        pid, _sep, cmd = line.strip().partition("\t")
        if pid.isdigit() and cmd.strip():
            out.append((int(pid), cmd.strip()))
    return out


def _enum_old_procs_wmic():
    """次选：老的 wmic（新版 Windows 已移除，能走到这里说明这台机器还留着它）。"""
    txt = subprocess.check_output(
        'wmic process where "name like \'python%\' or name like \'cscript%\'" get processid,commandline '
        '/format:csv', shell=True, text=True, errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = []
    for line in str(txt or "").splitlines():
        parts = line.rsplit(",", 1)
        if len(parts) > 1 and parts[-1].strip().isdigit():
            out.append((int(parts[-1].strip()), line))
    return out


def _enum_old_procs():
    """枚举候选旧实例进程 → (procs, 用了哪条路, 失败原因)。

    `procs is None` ＝**两条路都失败**；这时原因必须由调用方留痕（不许静默降级成"没有旧实例"）。
    """
    errs = []
    for how, fn in (("PowerShell Get-CimInstance", _enum_old_procs_ps), ("wmic", _enum_old_procs_wmic)):
        try:
            return fn(), how, ""
        except Exception as e:
            errs.append("%s 失败：%s" % (how, e))
    return None, "", "；".join(errs)


_OWN_SCRIPT_NAMES = ("persona_morph.py", "watchdog.py", "onestart.py")


def _cmd_script_paths(cmd):
    """从一条命令行里抽出"指我们的那三个脚本"的**路径 token**（单一实现见 `agent/proc_match.py`）。

    为什么要回溯（V-R1-3）：原来的判据是"整条命令行里含子串 `persona_morph.py`"，于是
    `D:\\tools\\onestart.py`、**别人项目**里的 `watchdog.py`、另一份解压目录里的群相副本
    **统统会被 `taskkill /F` 强杀**（用户正在写的文件可能当场损坏）。
    ⚠️ 2026-09-20：实现**下沉到 `agent/proc_match`**，与 `scripts/stop_bot.py`（一键关闭）共用一份 ——
    两处各写一套必然漂移（"踢旧实例"修了、"一键关闭"还在同名就杀）。
    """
    from agent.proc_match import script_paths
    return script_paths(cmd)


def _is_our_install(cmd):
    """这条命令行的**脚本完整路径**是否落在本安装目录（ROOT）下、且文件名是那三个之一。

    ⛔ 这是 V-R1-3 的正解：`taskkill /F` 是强制终止，判据必须是"这个进程属于本次安装"，
    而不是"它的命令行里有几个像样的字"。实现见 `agent/proc_match.is_our_install`（单一来源）。
    """
    from agent.proc_match import is_our_install
    return is_our_install(cmd, ROOT)


def _kick_why(res, rc):
    """把 `taskkill` 失败的原因抠成一行（stderr 原文；抠不出就报退出码）。"""
    try:
        raw = getattr(res, "stderr", b"") or b""
        if isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw).decode("utf-8", "ignore")
        txt = " ".join(str(raw).split())
    except Exception:
        txt = ""
    return txt[:160] or ("taskkill 退出码 %s" % rc)


def _kick_old_instance():
    """踢掉旧版本实例（3210 的 persona_morph/watchdog + 启动器/关闭器进程）。

    返回 {"killed": [pid…], "failed": [{pid,rc,why}…], "skipped": [{pid,why}…],
          "how": 枚举方式, "error": 原因}：
      · **枚举失败时 error 非空且必写日志**——"这台机器没有这个能力"不许被吞成"没有旧实例要踢"（V7）；
      · V-R1-3 两条：①只杀**命令行里出现本安装 ROOT 路径**的进程（原来"文件名像就杀"⇒ 误伤别人项目）；
        ②`taskkill` 的**退出码纳入结果**（原来丢弃返回值、一律 append ⇒ 没杀掉也说"已踢"）。
    """
    procs, how, err = _enum_old_procs()
    if procs is None:
        log("⚠ 没能枚举进程 ⇒ 本次**没有踢任何旧实例**（旧版可能还在跑，症状＝点了没反应/还是老界面）。"
            "原因：%s" % err)
        time.sleep(1.5)
        return {"killed": [], "failed": [], "skipped": [], "how": "", "error": err}
    killed, failed, skipped = [], [], []
    for pid, cmd in procs:
        if "plugin" in cmd:
            skipped.append({"pid": pid, "why": "命令行含 plugin（插件/别的入口）"})
            continue
        if not _is_our_install(cmd):
            skipped.append({"pid": pid, "why": "脚本路径不在本安装目录（别人的同名脚本，按 V-R1-3 不许杀）"})
            continue
        if pid == os.getpid():
            continue            # ⛔ 自己的命令行里也有 onestart.py ⇒ 不加这条会把启动器自己踢掉
        try:
            res = subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                 capture_output=True, creationflags=0x08000000)
        except Exception as e:
            failed.append({"pid": pid, "rc": -1, "why": str(e)})
            log("踢旧实例 PID=%s 失败：%s" % (pid, e))
            continue
        rc = int(getattr(res, "returncode", 1) or 0)
        if rc == 0:
            killed.append(pid)
        else:
            why = _kick_why(res, rc)
            failed.append({"pid": pid, "rc": rc, "why": why})
            log("踢旧实例 PID=%s 失败：taskkill 退出码 %s（%s）" % (pid, rc, why))
    if skipped:
        log("旧实例清理：跳过 %d 个不属于本安装的候选（%s）"
            % (len(skipped), "；".join("PID=%s %s" % (x["pid"], x["why"]) for x in skipped[:5])))
    log("旧实例清理：枚举方式=%s · 候选 %d 个 · 已踢 %d 个 · 失败 %d 个 · 跳过 %d 个%s"
        % (how, len(procs), len(killed), len(failed), len(skipped),
           ("（PID=%s）" % ",".join(str(x) for x in killed)) if killed else ""))
    time.sleep(1.5)
    return {"killed": killed, "failed": failed, "skipped": skipped, "how": how, "error": ""}


def _open_current_console():
    """同版本已在运行 ⇒ 打开控制台。

    ⛔ 2026-09-22 修（第十五轮 **V-R15-3** · 网友报「打不开控制台」）：老实现是
      **"抢不到开窗锁就直接 return True"** —— 于是"锁在（90 秒新鲜期内）但其实一个控制台窗口都没有"
      时，这一跳**什么都不开也不说**（用户主观就是"点了一次没反应，等一分多钟再点一下才出来"）。
      正确的收口 2026-09-18 已经做在 `notify_ui.open_console` 里（先看真窗口 → 在就复用；
      不在才谈锁，锁抢不到也等窗口、等不到照开）。⇒ 这里**不再自己判**，一律交给它。
    """
    try:
        from agent.config import get_config
        sc = get_config().get("server", {})
        url = "http://127.0.0.1:%s/?token=%s" % (int(sc.get("port") or 3210), str(sc.get("token") or ""))
        return _open_console(url)
    except Exception as e:
        log("打开控制台失败：%s" % e)
        return False


def _open_console(url, browser_path=""):
    """打开控制台。

    唯一实现是 `agent/notify_ui.open_console`（优先级＝自家 WebView2 窗口 → 浏览器，
    并且**所有入口共用一把锁**）。本函数只做进度日志，不再自己判断/自己开——
    2026-09-14 修"自家窗口 + 浏览器同时弹"：原先启动器与机器人各开一处、各拿一把锁 ⇒ 双窗。
    """
    try:
        from agent.notify_ui import open_console as _oc
        rep = _oc(url, browser_path=browser_path)
        how = str(rep.get("how") or "")
        if how == "webview":
            log("已在我们自己的窗口里打开控制台（不依赖浏览器）")
        elif how == "reuse":
            log("控制台已经开着 ⇒ 直接把那个窗口抬起来（不再新开，免得攒一堆窗口）")
        elif how == "browser":
            log("自家控制台窗口不可用（%s）⇒ 已回退浏览器" % (rep.get("why") or "原因未知"))
        elif how == "skip":
            log("%s" % (rep.get("why") or "本次不重复打开"))
        else:
            log("打开控制台失败：%s（请手动访问 %s）" % (rep.get("why") or "", mask_url_token(url)))
        return bool(rep.get("ok"))
    except Exception as e:
        log("打开控制台失败：%s" % e)
        return False


def main():
    check_only = (os.environ.get("WX_ONESTART_CHECK") == "1") or ("--check-only" in sys.argv[1:])
    log("")
    log("╔══════════════════════════════════════════════╗")
    log("║        群相 一键启动（全程进度）         ║")
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
                _rep_kick = _kick_old_instance()
                if _rep_kick.get("error"):
                    # ⛔ 不许再无条件说"旧实例已清理"：枚举失败时那句话是假的（V7）
                    log("⚠ 旧实例**没能清理**（%s）⇒ 仍然继续启动；若出现「点了没反应/还是老界面」，"
                        "请看 data\\runtime.log 与 logs\\console.log" % _rep_kick["error"])
                else:
                    _n_kill = len(_rep_kick.get("killed") or [])
                    if _n_kill:
                        log("旧实例已清理（踢掉 PID=%s），继续一键启动。"
                            % ",".join(str(x) for x in _rep_kick["killed"]))
                    elif _rep_kick.get("skipped"):
                        # V-R1-3 的取舍必须说出来：旧版本进程存在、但它的脚本**不在本安装目录**
                        # ⇒ 我们不碰它（不然就回到"文件名像就强杀"那条误伤路）。它若占着端口，
                        # 用户需要自己收掉（「一键关闭」/任务管理器），所以这里给出可照着做的动作。
                        log("⚠ 检测到被跳过的候选进程（PID=%s）——它们的脚本不在本安装目录，"
                            "按「只踢本安装」的口径**没有动它们**。若启动后仍是老界面/端口被占，"
                            "请先用「一键关闭」或任务管理器结束那几个进程。"
                            % ",".join(str(x.get("pid")) for x in _rep_kick["skipped"][:5]))
                    else:
                        log("旧实例已清理（本次没有需要踢的进程），继续一键启动。")
                _bad_kick = _rep_kick.get("failed") or []
                if _bad_kick:
                    # V-R1-3：踢失败也是**结果**（原来丢弃 taskkill 退出码 ⇒ 没杀掉也报"已踢"）
                    log("⚠ 有 %d 个旧实例**没能踢掉**（%s）⇒ 它们可能仍占着端口/单实例锁。"
                        % (len(_bad_kick),
                           "、".join("PID=%s rc=%s" % (x.get("pid"), x.get("rc")) for x in _bad_kick)))
        except Exception:
            pass

    log("[1/3] 依赖检查（缺则自动安装；已装自动跳过）")
    py = sys.executable or "python"

    # 1. 依赖（检查表 14 项逐项百分比；安装阶段由 pip 自带百分比条显示）
    deps_done = [0]
    evt("PHASE", "deps")
    log("      首次安装要下载约 100~150MB（慢网十几分钟正常，中途别关窗；装过的不会重下）")

    # 依赖安装实时进度：统计 requirements 包数作为总量，逐包 +1（pip Collecting/Downloading/安装缺失 均计）
    try:
        _req_n = len([_l for _l in open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8")
                      if _l.strip() and not _l.strip().startswith("#")])
    except Exception:
        _req_n = 21
    deps_install = [0]

    def _deps_progress(ln):
        _s = str(ln or "").strip()          # pip 的行是缩进的（"  Downloading …"）⇒ 必须先 strip
        if _s.startswith("OK"):
            deps_done[0] += 1
            _prog("依赖检查", min(deps_done[0], 14), 14)
        elif ("安装缺失" in _s or "正在安装" in _s or _s.startswith("Collecting")
                or _s.startswith("Downloading") or _s.startswith("Installing collected")):
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
        # 单实例自检＝命名互斥体（probe 只查不占；旧版实例只写 pid 文件、没有互斥体，用 legacy_holder 兜住）
        from agent.single_instance import probe as _si_probe, legacy_holder as _si_legacy
        _lk = os.path.join(ROOT, "data", "bot.lock")
        _held, _hpid = _si_probe(lock_path=_lk)
        existing = ((_hpid or -1) if _held else (_si_legacy(_lk) or None))
    except Exception:
        existing = None
    # ⛔ 2026-09-18（作者：「不许覆盖解压，一定要直接更新」）：**残留的旧包实例不算"已在运行"**。
    #    旧包更新完可能没人接替（旧看门狗还在、机器人已死），这时如果这里直接 return 0，
    #    用户看到的就是"点了一键启动没反应"，于是只能手工覆盖解压 —— 那条路被作者否掉了。
    #    ⇒ 先比 `watchdog.pid` 第二行记的**包版本**：一致才是真在跑；不一致（含旧包写的 "2" 这种）
    #      就照常拉起 watchdog，让新看门狗按"整包版本不一致"接管（杀旧 + 清证据 + 拉起新机器人）。
    _stale = False
    try:
        with open(os.path.join(ROOT, "data", "watchdog.pid"), encoding="utf-8", errors="replace") as _f:
            _lines = [x.strip() for x in (_f.read() or "").splitlines()]
        _rec = _lines[1] if len(_lines) > 1 else ""
        _cur = ""
        with open(os.path.join(ROOT, "agent", "version.py"), encoding="utf-8") as _f2:
            import re as _re
            _m = _re.search(r"VERSION\s*=\s*['\"]([^'\"]+)['\"]", _f2.read())
            _cur = _m.group(1) if _m else ""
        if _cur and _rec != _cur:
            _stale = True
            log("发现**旧包残留实例**（watchdog.pid 记的版本=%r ≠ 本包 %r）⇒ 不跳过，拉起新看门狗接管"
                % (_rec or "(读不出，多半是旧版格式)", _cur))
    except Exception:
        _stale = False
    if existing is not None and not _stale:
        _who = ("pid=%s" % existing) if (existing and existing > 0) else "pid 未知"
        log("检测到机器人已在运行（%s）→ 打开控制台，不再重复启动。%s" % (
            _who, "如想重启请先「停止机器人」."))
        try:
            # 已有实例：走同一个开窗实现（自家 WebView2 窗口优先；地址从权威来源取，不再手拼 token）
            _open_console("")
        except Exception:
            pass
        return 0
    try:
        subprocess.Popen([py, watchdog], creationflags=0x08000000,
                         cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log("启动失败: %s" % e)
        return 1
    log("机器人已启动 ✔（等待控制台就绪，随后自动打开控制台窗口；完成后本窗口自动关闭）")
    # 轮询等控制台就绪再开窗：webui 会因微信布局校准等延迟就绪，只试一次会漏掉
    try:
        import json as _j
        cfg = _j.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
        _bpath = str((cfg.get("server", {}) or {}).get("browser_path") or "")
        _port = int((cfg.get("server", {}) or {}).get("port") or 3210)
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
                    # 单点打开策略：优先由机器人侧（webui 就绪后、原子锁保护）打开；
                    # 启动器只等待就绪，不抢开（避免双开）。
                    # ⛔ 2026-09-16 修：原来这里有一句 `if not _bot_opens_console(): pass`——
                    #   **空分支死逻辑**，看着像"兜底触发"其实什么都不做；真正决定开不开的是下面
                    #   这句 `_probe_browser_was_opened()`。而它读的是 `console_lock_fresh(90)`，
                    #   写锁的机器人若已退出（用户关窗+关进程后重开），旧锁仍"新鲜" ⇒ 判成
                    #   "机器人侧已打开" ⇒ 启动器不开、新机器人又抢不到锁 ⇒ **两边都不开**。
                    #   修法在 `agent/util.py`：锁文件带 pid，**写锁进程已死即视为过期**，
                    #   于是这里会正确地走到下面的兜底 `_open_console()`。
                    _w_hwnd = int(_probe_console_window() or 0)
                    _opened_by_bot = bool(_w_hwnd)
                    if not _opened_by_bot:
                        try:
                            _open_console("", _bpath)     # 地址为空 ⇒ 由 open_console 取权威地址（此刻已落盘）
                        except Exception as e:
                            log("控制台已就绪但打不开窗口：%s" % e)
                    else:
                        # ⛔ 2026-09-19：这句"机器人侧已打开"必须**带证据**。上一版就是这样一句
                        #   无凭据的结论酿成事故——窗口判据把启动器自己的窗（「群相 一键启动」）
                        #   当成了控制台 ⇒ 两边都不开、屏幕上什么都没有、用户得再点一次。
                        #   现在把"我到底看到了哪个窗口"写进日志（判据唯一源＝notify_ui.classify_console_window）。
                        _seen = ""
                        try:
                            from agent.notify_ui import window_title as _wt
                            _seen = "［看到窗口 hwnd=%s 标题=「%s」］" % (_w_hwnd, _wt(_w_hwnd))
                        except Exception:
                            _seen = ""
                        log("控制台已由机器人侧打开（单点执行），启动器不再打开。%s" % _seen)
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
            log("120 秒内控制台仍未就绪 —— 请查看 主运行日志 data\\runtime.log（启动与收尾在 logs\\persona_morph.log）")
            try:
                _open_console("", _bpath)
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
