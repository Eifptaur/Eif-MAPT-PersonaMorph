# -*- coding: utf-8 -*-
"""开机自启（**当前用户级、免管理员、默认关、随时可撤**）。

## 为什么这么设计
- **只动"当前用户的启动文件夹"**：`%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup`。
  不写注册表（`HKCU\\...\\Run`）、不建计划任务、不碰系统目录 ⇒ 免管理员、用户自己也能一眼看到并删掉。
- **默认关**：不在前后端悄悄替用户决定，也不擅自开机就跑（要开必须由用户在界面上显式选）。
- **产物可读可撤**：正常是启动文件夹里的一个快捷方式（指向 `一键启动.exe`，窗口最小化启动）；
  创建快捷方式失败（少见：PowerShell 被策略挡）⇒ 退回写一个启动脚本（**CRLF + 纯 ASCII 内容**——
  本仓血泪：cmd 按 ANSI 读文件，LF 会让它一行都跑不动、中文注释会变乱码命令）。
- **删掉那个文件＝关闭自启**：`disable()` 就是删它；删也一样生效（`status()` 以文件为准）。

⚠️ 非 Windows 平台：`status()` 如实返回 `supported=False`，`enable()` 明确拒绝（不假装成功）。
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys

log = logging.getLogger("persona-morph")

#: 启动文件夹（当前用户）
STARTUP_DIR = os.path.join(os.environ.get("APPDATA", ""),
                           "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
LNK_NAME = "群相." + "lnk"
CMD_NAME = "群相" + ".cmd"
#: 首选启动目标：包内的 `群相.exe`（产品的正规入口）。
#: ⛔ **文件名＝产品名**（用户口径："启动器的名字就是产品的名字"）——
#:   参照 WorkBuddy／微信：用户看到的就是一个以产品命名的 exe，直接双击就能用。
#:   ⚠️ 改名后**必须留老名**：老安装里已经建好的快捷方式、以及"正在运行的旧版进程"
#:   都还叫 `一键启动.exe` ⇒ 见下面的 `LEGACY_LAUNCHER_EXES`（只用于"认出来"，不用于新建）。
LAUNCHER_EXE = "群相.exe"
#: 历史名（**只读、只用于识别**：老快捷方式、老进程、老窗口标题）——⛔ 不要删。
LEGACY_LAUNCHER_EXES = ("一键启动.exe",)


def find_launcher_exe(root: str | None = None) -> str:
    """启动器的**实际**路径：新名优先，找不到退老名；都没有 ⇒ 返回新名（报错信息更贴近现状）。

    ⛔ 为什么要有它（而不是各处写字符串）：文件名刚从 `一键启动.exe` 改成 `群相.exe`，
      而**老安装**里可能只有老名 ⇒ 取"实际存在的那个"，新老两种安装都不坏。
      **名字的来源只有本模块一处**（`LAUNCHER_EXE` / `LEGACY_LAUNCHER_EXES`），
      判据、`notify_ui` 等一律调这里，别各自拼字符串。
    """
    # ⛔ **本模块没有模块级 `ROOT`** ⇒ 这里自己按 `__file__` 算（我第一版直接用了 `ROOT`
    #   ⇒ `NameError`，而且那异常在判据侧表现为"探针无输出/无汇总行"，很难一眼看出。
    #   与 `wechat.py` 里少了 `import sys` 那次是**同一类错**：别假设别的模块有那个名字。）
    _r = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for _n in (LAUNCHER_EXE,) + tuple(LEGACY_LAUNCHER_EXES):
        _p = os.path.join(_r, _n)
        if os.path.exists(_p):
            return _p
    return os.path.join(_r, LAUNCHER_EXE)
#: ⚠️ **老机制**（产品里原本就有）：`scripts\\开机自启.bat` 用计划任务注册，**要管理员**。
#    两套并存时必须**互相看得见**，否则用户开了老的那套、新开关却显示"未开启"（最误导）。
#    口径：新开关**默认只动启动文件夹**（免管理员）；老任务**只认、只删、不建**（建它要管理员，
#    交给原来那个 .bat），状态与关闭都如实报出来。
LEGACY_TASK = "Persona Morph"


def _root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def supported() -> bool:
    return os.name == "nt" and bool(os.environ.get("APPDATA"))


def startup_dir(path: str = None) -> str:
    """启动文件夹路径（`path` 参数是**判据隔离口**：判据不许真写用户的启动文件夹）。"""
    return str(path or STARTUP_DIR)


def _lnk(dir_path: str = None) -> str:
    return os.path.join(startup_dir(dir_path), LNK_NAME)


def _cmd(dir_path: str = None) -> str:
    return os.path.join(startup_dir(dir_path), CMD_NAME)


def target() -> tuple:
    """返回 `(要启动的东西, 工作目录, 说明)`；找不到正规入口时返回空串（调用方如实报错）。"""
    root = _root()
    exe = os.path.join(root, LAUNCHER_EXE)
    if os.path.exists(exe):
        return exe, root, "包内的「%s」" % LAUNCHER_EXE
    # 没有 exe（源码运行/开发机）：退到 Qt 壳入口（与 exe 同一套启动逻辑）
    boot = os.path.join(root, "qt_bootstrap.py")
    py = os.path.join(root, "runtime", "python", "python.exe")
    if os.path.exists(boot) and os.path.exists(py):
        return "%s|%s" % (py, boot), root, "自带 Python + qt_bootstrap.py（源码运行方式）"
    return "", root, "没找到可启动的入口（既没有「%s」，也没有 runtime\\python + qt_bootstrap.py）" % LAUNCHER_EXE


def _legacy_task_enabled() -> bool:
    """老机制（计划任务）在不在。**只查，不建** —— 建它要管理员，交给 `scripts\\开机自启.bat`。"""
    if not supported():
        return False
    try:
        r = subprocess.run(["schtasks", "/query", "/tn", LEGACY_TASK],
                           capture_output=True, text=True, timeout=30,
                           # ⛔ 中文 Windows 上 schtasks 的输出是 **GBK(mbcs)**
                           #    ⇒ 不显式指定编码，这串文本流到别处（按 utf-8 读）会直接炸：
                           #    实测本仓 run_all 的读取线程报 `UnicodeDecodeError: 0xb4`
                           #    ⇒ 判据 22/0 全过却被判红。text=True 不够，必须给 encoding。
                           encoding="mbcs", errors="replace",
                           creationflags=0x08000000)
        return r.returncode == 0
    except Exception as e:  # noqa: BLE001
        log.debug("查老的自启计划任务失败（当作没有）：%s", e)
        return False


def _remove_legacy_task() -> tuple:
    """删老的计划任务（用户想关就得能真关掉）。失败**如实说**，并指向原来那个 .bat。"""
    try:
        r = subprocess.run(["schtasks", "/delete", "/tn", LEGACY_TASK, "/f"],
                           capture_output=True, text=True, timeout=60,
                           encoding="mbcs", errors="replace",   # 同上：中文输出是 GBK
                           creationflags=0x08000000)
        if r.returncode == 0:
            return True, "（老的开机任务也一起删掉了）"
        return False, ("（老的开机任务删不掉：%s —— 它需要管理员，"
                       "请右键以管理员身份运行 scripts 目录里那个「取消开机自启」）"
                       % ((r.stdout or "") + (r.stderr or "")).strip()[-80:])
    except Exception as e:  # noqa: BLE001
        return False, "（删老的开机任务时出错：%s）" % str(e)[:60]


def status(dir_path: str = None) -> dict:
    """以**文件/任务为准**报状态（删掉快捷方式、或删掉计划任务 ⇒ 这里立刻变回"未开启"）。

    ⚠️ 两套机制都要认：我们放的启动文件夹产物（新，免管理员）＋ 老的计划任务（要管理员）
    —— 只认一套会让"其实开着"显示成"未开启"，那是最误导用户的一种错。
    """
    if not supported():
        return {"supported": False, "enabled": False, "method": "none", "path": "",
                "why": "只支持 Windows（当前系统 %s）" % sys.platform}
    lnk, cmd = _lnk(dir_path), _cmd(dir_path)
    for p, method in ((lnk, "shortcut"), (cmd, "cmd")):
        if os.path.exists(p):
            tgt, _wd, why = target()
            return {"supported": True, "enabled": True, "method": method, "path": p,
                    "target": tgt, "why": "已开启（%s；删掉这个文件即关闭）" % why}
    if _legacy_task_enabled():
        return {"supported": True, "enabled": True, "method": "legacy-task", "path": LEGACY_TASK,
                "target": target()[0],
                "why": "已开启（用的是**老机制**：计划任务「%s」，需要管理员才能关；"
                       "点关闭会尝试删它）" % LEGACY_TASK}
    return {"supported": True, "enabled": False, "method": "none", "path": "",
            "target": target()[0], "why": "未开启（启动文件夹里没有 %s，也没有老的计划任务）" % LNK_NAME}


def _make_shortcut(lnk_path: str, target_path: str, workdir: str) -> tuple:
    """用 PowerShell 建 .lnk（**窗口最小化**启动，开机时不抢你的屏幕）。失败返回 (False, 原因)。"""
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
          "$s.TargetPath='%s';$s.WorkingDirectory='%s';$s.WindowStyle=7;$s.Description='群相 开机自启';$s.Save()"
          % (lnk_path.replace("'", "''"), target_path.replace("'", "''"), workdir.replace("'", "''")))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=60,
                           # 中文 Windows 上它输出的也是 GBK(mbcs) ⇒ 同 schtasks 那两处的问题
                           encoding="mbcs", errors="replace",
                           creationflags=0x08000000 if os.name == "nt" else 0)
    except Exception as e:  # noqa: BLE001
        return False, "调用 PowerShell 失败：%s" % str(e)[:80]
    if r.returncode != 0 or not os.path.exists(lnk_path):
        return False, "PowerShell 没能建出快捷方式（rc=%s）：%s" % (
            r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()[-120:])
    return True, ""


def _write_cmd_launcher(cmd_path: str, target_path: str, workdir: str) -> tuple:
    """退路：写一个启动器 .cmd（**CRLF + 纯 ASCII 内容**）。

    ⛔ 内容必须纯 ASCII：cmd 按 ANSI 读文件，中文注释会变成乱码命令（本仓实测踩过）；
    ⛔ 行尾必须 CRLF：LF 会让 cmd 一行都跑不动（仓库根目录那个只读体检启动器头几行就写着这条）。
    """
    if len(target_path.split("|")) == 2:
        py, boot = target_path.split("|", 1)
        run = '"%s" -X utf8 "%s"' % (py, boot)
    else:
        run = 'start "" "%s"' % target_path
    body = "\r\n".join([
        "@echo off",
        "rem Auto-start launcher generated by persona-morph. Delete this file to turn it off.",
        "chcp 65001 >nul",
        'cd /d "%s"' % workdir,
        run,
        "",
    ])
    try:
        with open(cmd_path, "w", encoding="ascii", errors="strict", newline="") as fh:
            fh.write(body)
    except UnicodeEncodeError as e:
        return False, ("启动器内容含非 ASCII（不该发生，路径里有中文时会这样）：%s"
                       % str(e)[:80])
    except Exception as e:  # noqa: BLE001
        return False, "写启动器失败：%s" % str(e)[:80]
    return True, ""


def enable(dir_path: str = None) -> tuple:
    """开启开机自启。返回 `(ok, 人话说明)`。**已开则原样返回 ok**（幂等）。"""
    if not supported():
        return False, "这个系统不支持（只做 Windows 当前用户级自启），没做任何改动。"
    st = status(dir_path)
    if st.get("enabled"):
        return True, "本来就是开着的（%s）" % st.get("path")
    tgt, wd, why = target()
    if not tgt:
        return False, "找不到要启动的程序：%s" % why
    d = startup_dir(dir_path)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        return False, "启动文件夹不可写（%s）：%s" % (d, str(e)[:80])
    if len(str(tgt).split("|")) != 2:
        ok, err = _make_shortcut(_lnk(dir_path), tgt, wd)
        if ok:
            return True, ("已开启：在启动文件夹放了「%s」，开机后最小化启动（%s）。关掉：删掉该文件，或点界面的开关。"
                          % (LNK_NAME, why))
        log.warning("建快捷方式失败，改用启动器 .cmd：%s", err)
        ok2, err2 = _write_cmd_launcher(_cmd(dir_path), tgt, wd)
        if ok2:
            return True, ("已开启：启动文件夹里放了「%s」（快捷方式没能建出来：%s）。删掉该文件即关闭。"
                          % (CMD_NAME, err[:60]))
        return False, "没能开启：%s；退路也失败：%s" % (err, err2)
    ok, err = _write_cmd_launcher(_cmd(dir_path), tgt, wd)
    if not ok:
        return False, "没能开启：%s" % err
    return True, "已开启：启动文件夹里放了「%s」（%s）。删掉该文件即关闭。" % (CMD_NAME, why)


def disable(dir_path: str = None) -> tuple:
    """关闭自启：删我们放的产物；**老的计划任务也一并尝试删掉**（否则用户以为关了，其实还开着）。"""
    if not supported():
        return False, "这个系统不支持（没做任何改动）。"
    removed = []
    for p in (_lnk(dir_path), _cmd(dir_path)):
        try:
            if os.path.exists(p):
                os.remove(p)
                removed.append(os.path.basename(p))
        except Exception as e:  # noqa: BLE001
            return False, "删不掉 %s（%s）⇒ 你也可以手动删掉它。" % (os.path.basename(p), str(e)[:60])
    _tail = ""
    if _legacy_task_enabled():
        _ok, _tail = _remove_legacy_task()
    if removed:
        return True, "已关闭（删掉了 %s）%s" % ("、".join(removed), _tail)
    if _tail:
        return True, "已关闭%s" % _tail
    return True, "本来就是关着的（启动文件夹里没有我们的文件，也没有老的计划任务）。"


def status_text() -> str:
    """一行人类可读状态（控制台/CLI/菜单都用它，口径单一）。"""
    st = status()
    if not st.get("supported"):
        return "开机自启：不支持（%s）" % st.get("why")
    if st.get("enabled"):
        _how = {"shortcut": "快捷方式", "cmd": "启动脚本",
                "legacy-task": "老机制（计划任务，要管理员才能关）"}.get(st.get("method"), "已开启")
        return "开机自启：已开启（%s；位置 %s）" % (_how, st.get("path"))
    return "开机自启：未开启（%s）" % st.get("why")
