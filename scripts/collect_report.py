# -*- coding: utf-8 -*-
"""随包检验工具：在**任意一台电脑**上跑一次，生成一份"能发回来直接定位问题"的报告。

用途（用户口径）：他把包拷到另一台电脑，双击 `一键检验.cmd` 跑一遍；
遇到任何问题，**把生成的报告文件发回来**，我据此改进。

设计原则：
  · **永不崩**：每个小节各自 try/except，缺什么就在报告里写明"这一节失败/不适用"，绝不让整份报告出不来；
  · **只读为主**：默认不发送任何消息、不点击、不发表；只有加 `--send-test` 才做一次"投递给文件传输助手"的实测；
  · **自包含**：报告里带环境指纹（系统/DPI/分辨率/显示器/微信版本/UI 类名/窗口几何/依赖/输入档位/会话头可读性），
    拿到它不看日志就能判断"是没找到还是找错了"；
  · **落两份**：人读的 `.txt` 与可机器解析的 `.json`。

用法：
    runtime\\python\\python.exe scripts\\collect_report.py            # 只读体检
    runtime\\python\\python.exe scripts\\collect_report.py --send-test # 额外做一次投递发送实测（会真发一条测试消息）
"""
import argparse
import re
import ctypes
import json
import os
import platform
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT_DIR = os.path.join(ROOT, "报告")
SECTIONS = []       # [(标题, 行列表, 原始dict)]


def redact(s):
    """打码：家目录 → %USERPROFILE% · 主机名 → %COMPUTERNAME%。
    用户口径（2026-09-13）：发回来的报告里不许带他的个人信息。"""
    try:
        home = os.path.expanduser("~")
        if home:
            s = s.replace(home, "%USERPROFILE%")
            s = s.replace(home.replace("\\", "/"), "%USERPROFILE%")
        s = re.sub(r"[A-Za-z]:\\+Users\\+[^\\\s\"']+", "%USERPROFILE%", s)
        s = re.sub(r"[A-Za-z]:/Users/[^/\s\"']+", "%USERPROFILE%", s)
        node = platform.node()
        if node:
            s = s.replace(node, "%COMPUTERNAME%")
    except Exception:
        pass
    return s


def add(title, lines, raw=None):
    SECTIONS.append((title, list(lines), raw or {}))


def safe(fn, title, fallback="这一节失败（不影响其它节）"):
    """跑一小节，失败也只在报告里留痕。"""
    try:
        return fn()
    except Exception as e:
        add(title, ["  ✘ %s: %s" % (type(e).__name__, str(e)[:200])])
        return None


# ── 1 系统 / 显示 ──────────────────────────────────────────────────────
def _os_registry():
    """系统口径（注册表）。

    为什么不能只信 `platform.version()`（2026-09-15 跨机报告实测）：它读的是**进程 manifest**，
    Win10 上会报 `10.0.19041`，而那台机器真值是 **19045 / 22H2** ⇒ 报告报错了版本。
    """
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", 0,
                             winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0))
        out = {}
        for name, field in (("ProductName", "product"), ("DisplayVersion", "display"),
                            ("CurrentBuildNumber", "build"), ("UBR", "ubr"),
                            ("EditionID", "edition")):
            try:
                out[field] = str(winreg.QueryValueEx(key, name)[0])
            except Exception:
                pass
        winreg.CloseKey(key)
        # ⚠️ 注册表的 `ProductName` 在 **Windows 11** 上仍然写着 "Windows 10"（微软一直没改，
        #    本机实测：ProductName=Windows 10 Home China，真身是 Win11 build 26200）⇒
        #    **按 build 号判代际**（≥22000 即 Win11），别拿 ProductName 当文件名。
        try:
            out["family"] = "Windows 11" if int(out.get("build", "0")) >= 22000 else "Windows 10"
        except Exception:
            out["family"] = ""
        return out
    except Exception:
        return {}


def sec_system():
    lines, raw = [], {}
    lines.append("  操作系统: %s %s (build %s) · %s" % (
        platform.system(), platform.release(), platform.version(), platform.machine()))
    reg = _os_registry()
    if reg:
        raw["os_registry"] = reg
        lines.append("  系统口径（注册表，按 build 判代际）: %s · build %s.%s · 版本号 %s · EditionID=%s"
                     % (reg.get("family", "?"), reg.get("build", "?"), reg.get("ubr", "?"),
                        reg.get("display", "?"), reg.get("edition", "?")))
    lines.append("  Python: %s (%s)" % (platform.python_version(), sys.executable))
    lines.append("  是否 64 位解释器: %s" % (sys.maxsize > 2 ** 32))
    try:
        # 自包含的 DPI 自查（不依赖任何开发期模块）
        u32 = ctypes.windll.user32
        # ⚠️ 缩放必须**按 DPI 算**，不能拿分辨率相除（2026-09-15 跨机报告抓到假值，本机复现过）：
        #    · 锁了 PerMonitorV2 之后 `HORZRES` 与 `DESKTOPHORZRES` **都是物理值**
        #      （本机实测 2560/2560）⇒ 两者相除必然得「1.0×」，而真值是 150%；
        #    · 不锁（进程 DPI-unaware）时 `GetDpiForSystem()` 返回 **96**、`HORZRES` 给逻辑值
        #      （本机 1707）⇒ 照样报错。
        #    ⇒ 正确取法：**先锁 PerMonitorV2，再问屏幕 DC 的 LOGPIXELSX**（本机锁后实测 144 ⇒ 150%）。
        try:
            u32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            u32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
            aware = bool(u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)))
        except Exception:
            aware = False
        gdi = ctypes.windll.gdi32
        gdi.GetDeviceCaps.restype = ctypes.c_int
        hdc0 = u32.GetDC(0)
        dpi = int(gdi.GetDeviceCaps(hdc0, 88))                                         # LOGPIXELSX
        phys_w, phys_h = gdi.GetDeviceCaps(hdc0, 118), gdi.GetDeviceCaps(hdc0, 117)   # DESKTOPHORZRES/VERTRES
        seen_w, seen_h = gdi.GetDeviceCaps(hdc0, 8), gdi.GetDeviceCaps(hdc0, 10)       # HORZRES/VERTRES
        u32.ReleaseDC(0, hdc0)
        if not dpi:
            dpi = 96
        zoom = round(dpi / 96.0, 3)
        raw["dpi"] = {"permonitorv2": aware, "dpi": dpi, "zoom": zoom,
                      "physical": (phys_w, phys_h), "seen": (seen_w, seen_h),
                      "ratio_is_meaningless": round(phys_w / seen_w, 2) if seen_w else None}
        lines.append("  缩放: %d%%（屏幕 DC 的 LOGPIXELSX = %d；96=100%%）· 小数 %.3f×"
                     % (round(zoom * 100), dpi, zoom))
        lines.append("  物理分辨率: %s×%s · 进程所见: %s×%s（PerMonitorV2 下两者相同属正常，"
                     "**不要**用它们相除来算缩放）" % (phys_w, phys_h, seen_w, seen_h))
        lines.append("  DPI 上下文: %s（本报告是在锁定之后取的 DPI，顺序刻意如此）"
                     % ("PerMonitorV2" if aware else "未锁上/已是别的档"))
    except Exception as e:
        lines.append("  DPI 自查失败: %s" % e)
    try:
        u32 = ctypes.windll.user32
        monitors = []

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        MONITORENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                             ctypes.POINTER(RECT), ctypes.c_double)

        def cb(h, hdc, r, data):
            monitors.append((r.contents.left, r.contents.top, r.contents.right, r.contents.bottom))
            return 1
        u32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(cb), 0)
        raw["monitors"] = monitors
        lines.append("  显示器数量: %d ⇒ %s" % (len(monitors), monitors))
    except Exception as e:
        lines.append("  显示器枚举失败: %s" % e)
    return lines, raw


# ── 2 微信版本 / UI 类名 / 窗口几何 ─────────────────────────────────────
def sec_wechat():
    lines, raw = [], {}
    try:
        from agent import wechat_version_info  # type: ignore
    except Exception:
        try:
            from agent.wechat import wechat_version_info   # type: ignore
        except Exception:
            wechat_version_info = None
    if wechat_version_info:
        try:
            info = wechat_version_info() or {}
            raw["version_info"] = info
            for k in ("version", "path", "supported", "message"):
                if k in info:
                    lines.append("  %s: %s" % (k, str(info.get(k))[:160]))
        except Exception as e:
            lines.append("  版本检测失败: %s" % e)
    try:
        import glob
        exes = []
        for pat in ("C:/Program Files/Tencent/WeChat/Weixin.exe", "C:/Program Files (x86)/Tencent/WeChat/Weixin.exe",
                    "D:/WX/Weixin/Weixin.exe", "M:/WX/Weixin/Weixin.exe"):
            if os.path.exists(pat):
                exes.append(pat)
        lines.append("  常见安装路径命中: %s" % (exes or "无（可能在其它盘，看上面的 path）"))
    except Exception:
        pass
    # 窗口枚举（不依赖库）
    try:
        from agent import input_backend as ib
        raw["input"] = ib.status()
        main = ib.find_main_window()
        lines.append("  输入后端档位: %s（level=%s, touches_cursor=%s, DPI=%s）" % (
            raw["input"].get("backend"), raw["input"].get("level"),
            raw["input"].get("touches_cursor"), raw["input"].get("dpi_mode")))
        lines.append("  主窗 hwnd=%s class=%s rect=%s visible=%s" % (
            main, ib._class_of(main) if main else "-",
            ib.window_rect(main) if main else "-",
            bool(ctypes.windll.user32.IsWindowVisible(main)) if main else "-"))
        lines.append("  候选同类名窗口（应含主窗；朋友圈编辑窗也算，注意区分）:")
        CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        found = []

        def cb2(h, _l):
            if ib._class_of(h) == ib.MAIN_CLASS:
                found.append((int(h), ib.window_rect(h), ib.RENDER_CHILD in ib._child_classes(h)))
            return True
        ctypes.windll.user32.EnumWindows(CB(cb2), 0)
        for h, r, child in found:
            lines.append("    hwnd=%-9s rect=%s 带渲染子窗=%s" % (h, r, child))
        raw["windows"] = [{"hwnd": h, "rect": r, "has_render_child": c} for h, r, c in found]
    except Exception as e:
        lines.append("  窗口枚举失败: %s" % e)
    return lines, raw


# ── 3 依赖 / 版本能力矩阵 / UIA ────────────────────────────────────────
def sec_deps_and_caps():
    lines, raw = [], {}
    try:
        from agent import dep_heal as dh
        diag = dh.diagnose()
        raw["deps"] = diag
        lines.append("  体检来源: %s" % dh.probe_source())
        lines.append("  依赖合计 %d 项：%s" % (len(diag), dh.summary_line()))
        bad = [d for d in diag if d["status"] != "ok"]
        for d in bad[:12]:
            lines.append("    ! %s 需 %s，实装 %s（%s）" % (d["name"], d["required"], d["installed"] or "无", d["status"]))
        lines.append("  离线 wheel 可用: %s" % dh.offline_available())
    except Exception as e:
        lines.append("  依赖体检失败: %s" % e)
    try:
        from agent import version_matrix as vm
        cur = vm.current()
        raw["caps"] = {"wechat": cur["wechat"], "adapter": cur["adapter"],
                       "summary": cur["summary"], "gate": cur["gate"]}
        lines.append("  版本能力矩阵: 微信 %s × 适配层 %s ⇒ %s" % (
            cur["wechat"], cur["adapter"], cur["summary"]))
        lines.append("  版本门: measured=%s %s" % (cur["gate"]["measured"], cur["gate"]["advice"][:120]))
        for cid, v in sorted(cur["caps"].items()):
            lines.append("    %-18s %-10s %s" % (cid, v["status"], v["evidence"][:80]))
    except Exception as e:
        lines.append("  能力矩阵失败: %s" % e)
    try:
        from agent import uia_probe
        res = uia_probe.probe(save=False)
        raw["uia"] = {k: res.get(k) for k in ("nodes", "materialized", "fingerprint", "error")}
        lines.append("  UIA 探针: 节点 %s · 物化=%s · 指纹 %s %s" % (
            res.get("nodes"), res.get("materialized"), res.get("fingerprint"),
            ("· " + str(res.get("error"))[:100]) if res.get("error") else ""))
    except Exception as e:
        lines.append("  UIA 探针失败: %s" % e)
    return lines, raw


# ── 4 会话头可读性 / 面板探测 ──────────────────────────────────────────
def sec_visual():
    lines, raw = [], {}
    # ⚠️ 2026-09-17（网友那份检验报告）：**先把"消息库用的哪个目录"写进报告**——这行原来缺着，
    #    于是"配置里填的目录用不了、产品死在那条路上"谁也看不出来（同一份报告里诊断却说六步全过）。
    # ⛔ 2026-09-18（用户反馈原文：「通过文件夹中的脚本检查出来的报告显示，他回我之前自定义的地址里
    #    去看文件了」）：这里原来为了拿 `_db_how` 去 **new 一个 WeChatAdapter**，库打不开时构造函数
    #    直接抛 ⇒ 整段退化成"消息库: 取不到"，那句"你填的用不了、实际回落到了 X"根本印不出来。
    #    现在分两步：① 运行中的那条路（`account_dir`）**试探、抛了也继续**；
    #              ② 结论一律由 `wechat_dir.status` 给（配置是否可用 + 实际在读哪个 + 回落原因）。
    _conf_db = ""
    try:
        from agent.config import get_config
        _conf_db = str((get_config().get("wechat") or {}).get("db_dir") or "").strip()
    except Exception:
        _conf_db = ""
    _how, _how_err = {}, ""
    raw["db_dir_config"] = _conf_db
    try:
        from agent.wechat import WeChatAdapter as _WCA
        _how = dict(getattr(_WCA(), "_db_how", None) or {})
        raw["db_how"] = _how
    except Exception as e:
        _how_err = "%s: %s" % (type(e).__name__, str(e)[:140])
    try:
        from agent import wechat_dir as _wd
        _info = _wd.status(_how)
        raw["wechat_dir"] = _info
        lines.append("  微信数据目录: 实际在读 %s（来源=%s）"
                     % (_info.get("now") or "取不到", _info.get("src") or "?"))
        if _info.get("configured") and not _info.get("configured_ok"):
            lines.append("  ⚠️ 配置「数据库目录」里填的 %s **不可用**：%s ⇒ 已回落到 %s"
                         "（控制台「微信」面板那一行可改）"
                         % (_info.get("configured"), _info.get("configured_why") or "用不了",
                            _info.get("now")))
        elif _info.get("note"):
            lines.append("  ⚠️ %s" % _info.get("note"))
        if _how:
            # 账号这一维（2026-09-19 加，网友反馈：「切换微信号使用后提示寻找不到库、还要求相同的权限」
            # 「只有前几句话会正常回复，后面不再回复」）：多账号机器上**读的是哪个号**是看不见的第一杀手
            # ——读到旧号时新消息一条都进不来，而暂停/水位/key 全是好的 ⇒ 报告里必须留下这个证据。
            _alv = _info.get("account_live")
            _anames = list(_info.get("account_names") or [])
            lines.append("  消息库账号: %s%s（来源=%s）"
                         % (_how.get("account") or _how.get("account_dir") or _how.get("dir") or "取不到",
                            "" if _alv is None else ("（库正在被写）" if _alv
                                                     else "（**没在动**：微信可能已经切号了）"),
                            _how.get("src") or "?"))
            if _how.get("account_why"):
                lines.append("  为什么读这个账号: %s" % _how.get("account_why"))
            if len(_anames) > 1:
                lines.append("  这台机器上的微信账号目录: %s（**只有「正在被写」的那个该读**；"
                             "切号没跟上的话，新消息一条都看不到）" % "、".join(_anames))
    except Exception as e:
        lines.append("  微信数据目录: 取不到（%s: %s）" % (type(e).__name__, str(e)[:120]))
    if _how_err:
        # 库打不开**不等于**"这条结论取不到"：可用性/回落由上面那两行如实给出
        lines.append("  消息库: 打不开或取不到（%s）" % _how_err)
    try:
        from agent import chat_header as ch
        fp = ch.capture()
        raw["header_len"] = len(fp)
        if fp and ch.is_blank(fp):
            # 2026-09-15 跨机实测抓到的误报：微信**最小化**时 PrintWindow 返回全白帧（64 维全 255），
            # 而旧版报告照样写「可抓」⇒ 缺的正是这条"空白图不算抓到"的自检。
            raw["header_blank"] = True
            lines.append("  会话头指纹: **无效（空白图）** —— 抓到的整幅是纯色帧（样例 %s），不是真画面；"
                         "微信最小化 / 被隐藏时就是这样 ⇒ 这一项按「抓不到」算" % (list(fp)[:6],))
        elif fp:
            lines.append("  会话头指纹: 可抓，%d 维，样例 %s" % (len(fp), fp[:6]))
            pane = None
            try:
                from PIL import ImageGrab
                from agent.wechat import WeChatAdapter
                gui = WeChatAdapter()._get_gui()
                gui._update_render_rect()
                img = ImageGrab.grab(tuple(gui.render_rect))
                pane = ch.detect_pane_left(img)
                lines.append("  探测到的聊天面板左沿: %s px（渲染宽 %d）" % (
                    pane or "未探测到（将用比例兜底）", gui.render_rect[2] - gui.render_rect[0]))
                raw["pane_left"] = pane
            except Exception as e:
                lines.append("  面板左沿探测失败: %s" % e)
        else:
            lines.append("  会话头指纹: **抓不到**（微信窗口不可见/被最小化/权限不足？）")
    except Exception as e:
        lines.append("  会话头检查失败: %s" % e)
    return lines, raw


# ── 5 可选：投递发送实测 ───────────────────────────────────────────────
def sec_send_test():
    lines, raw = [], {}
    token = "检验%05d" % (int(time.time()) % 100000)
    # ⛔ **自检工具绝不许动用户的鼠标**（2026-09-16 跨机 r12 事故：对面那台跑本工具时投递档切不过去 ⇒
    #    自动退回真实路径 ⇒ 动了 16 秒光标）。⇒ 这里强制关掉真鼠标兜底（环境变量优先级最高，无视 config）。
    os.environ["WXAGENT_REAL_FALLBACK"] = "0"
    try:
        from agent.chat_header import check, reference, ref_sizes
        from agent.wechat import WeChatAdapter
        wx = WeChatAdapter()
        # 版本门状态（2026-09-15 跨机实测：这一环会把"发送实测"整条拦下 ⇒ 报告必须自己说清楚，
        # 别让对面看着"False · 0.0s"猜；末尾还要给出放行的确切办法）。
        try:
            from agent import version_gate as _vg
            gt = _vg.check("send")
        except Exception as _e:                      # noqa: BLE001
            gt = {"level": "?", "allow": True, "reason": "版本门查不了：%s" % _e}
        raw["gate"] = gt
        lines.append("  版本门: level=%s · 本次放行=%s · %s"
                     % (gt.get("level"), gt.get("allow"), str(gt.get("reason"))[:120]))
        lines.append("  文件传输助手参照: %s · 已学尺寸 %s" % (bool(reference("filehelper")), ref_sizes("filehelper")))
        st = check("filehelper")
        lines.append("  发送前会话头三态: %s · %s" % (st.get("status"), st.get("note")))
        # ⚠️ 一律走**生产路径** `send_text`：会话头判 ok 才投递，否则真实路径（按名字打开会话 +
        #    顺手学该尺寸的参照，下次即可投递）。**不允许"没参照就直接投递"**——
        #    2026-09-13 自测事故：no_ref 照发 ⇒ 消息被打进当时打开的另一个会话（发给了联系人 E）。
        t0 = time.time()
        # ⚠️ 发送必须带**硬超时**：真实路径兜底可能长时间不返回（实测 >120s 未回），
        #    没有这道闸会让整份报告卡死 ⇒ 违反本工具"永不卡住"的设计原则。
        _box = {}

        def _do_send():
            try:
                _box["r"] = wx.send_text("filehelper", token)
            except Exception as _e:      # noqa: BLE001
                _box["e"] = "%s: %s" % (type(_e).__name__, str(_e)[:150])

        _th = threading.Thread(target=_do_send, daemon=True)
        _th.start()
        _th.join(90)
        if _th.is_alive():
            ok, msg = False, "发送实测超时（90 秒未返回）⇒ 放弃等待；报告继续（这本身是有用的现象）"
        elif "e" in _box:
            ok, msg = False, "发送抛异常：%s" % _box["e"]
        else:
            ok, msg = _box.get("r", (False, "无返回"))
        lines.append("  发送结果: %s · %s · %.1fs · token=%s" % (ok, msg, time.time() - t0, token))
        raw.update({"token": token, "ok": bool(ok), "msg": str(msg),
                    "header_status": st.get("status"), "gate_allow": gt.get("allow")})
        # ⚠️ 「走了哪条路」必须**据实**说（2026-09-15 跨机实测抓到的误报）：旧版只要消息里没出现
        #    「投递档」就写「用了真实路径兜底」，可实际是**被版本门拦在发送之前**（0.0s、一条都
        #    没发、光标没动）⇒ 那句话会让人以为"真鼠标兜底跑过了但失败了"。
        if "投递档" in str(msg):
            lines.append("  说明: 本次走的是**投递档（L5）**：全程不动光标；可能短暂把微信带到前台"
                     "（跨机实测约 1~3 秒，伪激活的代价）后**自动还回**你的窗口")
        elif not ok and not gt.get("allow", True):
            lines.append("  说明: 本次在**发送之前**就被版本门拦下 —— 没进入发送流程、光标未动、"
                         "**一条消息都没发出去**（不是「发失败了」）")
            lines.append("  放行办法: 重跑本报告并加 `--allow-send`（只在本次进程内放行），"
                         "或在控制台点「本次允许发送」")
        elif ok:
            lines.append("  说明: 本次走了**真实路径兜底**（会短暂动光标/切前台，属库的既有兜底），且发送成功")
        else:
            lines.append("  说明: 本次走了**真实路径兜底**（会短暂动光标/切前台），但没成功")
        if not ok:
            lines.append("  ⇒ 请把本报告发回；若希望用真鼠标兜底，可在控制台把 input.backend 设为 real 再试")
    except Exception as e:
        lines.append("  投递发送实测失败: %s: %s" % (type(e).__name__, str(e)[:200]))
    return lines, raw


def sec_delivery():
    """2026-09-15 新增：**交付面**自检——新加的那些功能在这台机器上到底能不能用。

    为什么单独一节：原来的检验报告只覆盖"环境 + 微信窗口 + 依赖/UIA + 会话头 + 投递发送"，
    而这一年新加的音源（edge 三档）、B 站解析、模型端点、更新链、磁盘占用**一条都没进**，
    另一台电脑跑完也不知道这些能不能用。这一节全部只读、不打字不发消息。
    """
    lines, raw = [], {}

    # ① 版本与更新链
    try:
        from agent.version import VERSION
        lines.append("  本机版本: %s" % VERSION)
        raw["version"] = VERSION
    except Exception as e:
        lines.append("  本机版本: 读不到（%s）" % type(e).__name__)
    try:
        from agent import update_check as UC
        st = UC.state()
        lines.append("  更新检查: status=%s · 本机=%s · 源上=%s · %s"
                     % (st.get("status"), st.get("mine") or "未记录", st.get("theirs") or "-",
                        str(st.get("why") or "")[:60]))
        raw["update"] = {k: st.get(k) for k in ("status", "mine", "theirs", "why")}
    except Exception as e:
        lines.append("  更新检查: 跑不了（%s）" % type(e).__name__)

    # ② 语音音源三档（edge 要联网、sapi 离线、http 是你自己的服务）
    try:
        from agent import voice_models as VM
        s = VM.status()
        lines.append("  语音音源: 档位=%s · 引擎=%s · 可用=%s · %s"
                     % (s.get("backend"), s.get("engine"), s.get("ok"), str(s.get("why") or "")[:70]))
        if s.get("backend") == "edge":
            vs = [v.get("name") for v in (s.get("voices") or []) if isinstance(v, dict)]
            lines.append("  可用音色: %d 个 %s" % (len(vs), ("（%s…）" % vs[0]) if vs else ""))
        raw["voice_models"] = {"backend": s.get("backend"), "engine": s.get("engine"), "ok": s.get("ok")}
    except Exception as e:
        lines.append("  语音音源: 读不到（%s）" % type(e).__name__)
    try:
        # ⚠️ 2026-09-17：这里原来只 `which("ffmpeg")`（只查 PATH）⇒ 干净机器上报告写着"没找到"，
        #    而 `requirements.txt` 里的 imageio-ffmpeg **本来就自带一份**（`agent/ffmpeg_bin.py`
        #    是唯一解析入口：配置 → PATH → 自带）⇒ 那条结论是假的，会把人指向"去装 ffmpeg"。
        from agent import ffmpeg_bin as _FB
        _fp = _FB.path()
        lines.append("  ffmpeg: %s" % (("%s（来源=%s）" % (_fp, _FB.source() or "?")) if _fp else
                                       "**没找到**（配置 / PATH / imageio-ffmpeg 自带那份都没有；"
                                       "合成要转 wav，必需）"))
    except Exception:
        import shutil as _sh
        lines.append("  ffmpeg: %s" % (_sh.which("ffmpeg") or "**没找到**（合成要转 wav，必需）"))

    # ③ 模型端点：能不能拉这个地址上的模型列表（＝"自己读模型清单"的第一步）
    try:
        from agent.config import get_config
        from agent.llm import join_url, _auth_headers
        import urllib.request as _ur
        api = (get_config() or {}).get("api") or {}
        base = str(api.get("base_url") or "")
        if not base:
            lines.append("  模型端点: 没填地址")
        else:
            u = join_url(base, "/models")      # ⚠️ 必须带前导斜杠：join_url 只 rstrip("/") 再拼，不补斜杠
            req = _ur.Request(u, headers=_auth_headers(str(api.get("api_key") or "")))
            with _ur.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            ids = [str((x or {}).get("id") or "") for x in (d.get("data") or [])]
            lines.append("  模型端点: 通 · 这个 key 能用 %d 个模型 %s"
                         % (len(ids), ("（%s…）" % ", ".join(ids[:4])) if ids else ""))
            cur = str(api.get("model") or "")
            lines.append("  当前模型 %s：%s" % (cur or "(未填)",
                                              "在列表里" if cur in ids else "**不在这个端点返回的列表里**"))
            raw["models"] = {"base": base, "count": len(ids), "has_current": cur in ids}
    except Exception as e:
        lines.append("  模型端点: 拉不到列表（%s）——不影响聊天，但无法核对模型名" % str(e)[:60])

    # ④ B 站（新功能：群友丢链接能不能看懂）
    try:
        from agent import bilibili as BL
        d, why = BL._get_json(BL.API_VIEW % "BV1LLjH6hEio", timeout=8)
        if d and d.get("code") == 0:
            dd = d.get("data") or {}
            lines.append("  B站连通: 通（示例稿件「%s」）" % str(dd.get("title") or "")[:26])
            raw["bilibili"] = {"ok": True}
        else:
            lines.append("  B站连通: 不通（%s）" % (why or ("code=%s" % (d or {}).get("code"))))
            raw["bilibili"] = {"ok": False, "why": why}
    except Exception as e:
        lines.append("  B站连通: 探不了（%s）" % type(e).__name__)

    # ⑤ 磁盘占用与清理
    try:
        from agent import housekeeping as HK
        f = HK.footprint()
        _dirs = f["dirs"]
        # ⛔ V-R14-4（第十四轮，P3）：①把**占用最大的目录**也报出来（原来只报临时残留与语音产物，
        #   而 `data/gen_images` 实测 665MB＝`data/` 的 99%，用户看不到）；②写明**删除不可恢复**
        #   （`housekeeping_pruned.jsonl` 是审计记录、不是回收站）——"清得掉多少"与"删了能不能回来"
        #   是两件事，用户有权先知道再决定。
        _big = sorted(((_v.get("mb", 0), _k, _v.get("files", 0)) for _k, _v in _dirs.items()),
                      reverse=True)[:2]
        lines.append("  磁盘: 临时残留 %d 项 / %.2f MB · 语音产物 %d 个 / %.2f MB · 占用最大 %s"
                     % (f["temp"]["entries"], f["temp"]["mb"],
                        _dirs.get("media/tts", {}).get("files", 0),
                        _dirs.get("media/tts", {}).get("mb", 0),
                        " · ".join("%s %.1f MB/%d 个" % (_k, _m, _n) for _m, _k, _n in _big) or "—"))
        lines.append("        （清理**只删我们自己造的文件**；删除**不可恢复**——那份清单只记「删了什么」、"
                     "不是回收站；图库只报数不清理）")
        raw["disk"] = {"temp": f["temp"], "tts": _dirs.get("media/tts"), "dirs": _dirs,
                       "note": "删除不可恢复（无备份）；图库 data/gen_images 只报数"}
    except Exception as e:
        lines.append("  磁盘: 查不了（%s）" % type(e).__name__)
    return lines, raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send-test", action="store_true", help="额外做一次投递发送实测（会真发一条测试消息）")
    ap.add_argument("--allow-send", action="store_true",
                    help="本次进程内放行版本门（等价于控制台点「本次允许发送」）⇒ 让 --send-test 真跑一次")
    ap.add_argument("--open", action="store_true",
                    help="跑完打开报告目录（⚠️ 会把资源管理器弹到前台 ⇒ 两个 .cmd 已不再默认带它；"
                         "跨机 r13 实测：带上它跑完，t=27.4s 前台被切到「报告」窗口）")
    args = ap.parse_args()

    if args.allow_send:
        # 解「鸡生蛋」（2026-09-15 跨机实测）：在没实测过的版本对上，发送会被版本门拦下，
        # 而"报告想测的正是发送" ⇒ 报告工具必须自带一条显式放行路。**只在本次进程内有效**。
        try:
            from agent import version_gate as _vg
            _vg.allow_session("collect_report --allow-send")
            print("已临时放行版本门（仅本次运行，重启后重新拦）")
        except Exception as _e:                      # noqa: BLE001
            print("放行版本门失败：%s" % _e)

    t0 = time.time()
    for title, fn in (("一、系统与显示", sec_system),
                      ("二、微信与窗口", sec_wechat),
                      ("三、依赖 / 版本能力矩阵 / UIA", sec_deps_and_caps),
                      ("四、会话头可读性", sec_visual),
                      ("五、交付面：音源 / 模型端点 / B站 / 更新 / 磁盘", sec_delivery)):
        lines, raw = safe(fn, title) or ([], {})
        add(title, lines, raw)
    if args.send_test:
        lines, raw = safe(sec_send_test, "六、投递发送实测") or ([], {})
        add("六、投递发送实测", lines, raw)
    else:
        add("六、投递发送实测", ["  未执行（加 --send-test 才会真发一条测试消息）"], {})

    ts = time.strftime("%Y%m%d-%H%M%S")
    os.makedirs(OUT_DIR, exist_ok=True)
    base = os.path.join(OUT_DIR, "检验报告-%s" % ts)
    raw_all = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "host": platform.node(),
               "elapsed_s": round(time.time() - t0, 1), "send_test": bool(args.send_test),
               "sections": [{"title": t, "lines": ln, "raw": r} for t, ln, r in SECTIONS]}
    with open(base + ".json", "w", encoding="utf-8") as f:
        f.write(redact(json.dumps(raw_all, ensure_ascii=False, indent=1)))
    txt = ["群相 · 环境检验报告",
           "生成时间: %s · 主机: %s · 耗时 %.1fs · 发送实测: %s" % (
               raw_all["when"], raw_all["host"], raw_all["elapsed_s"], "是" if args.send_test else "否"),
           "=" * 72]
    for t, ln, _r in SECTIONS:
        txt.append("\n【%s】" % t)
        txt.extend(ln)
    txt.append("\n" + "=" * 72)
    txt.append("把本文件（或同名 .json）发回即可定位问题。")
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write(redact("\n".join(txt) + "\n"))
    print("[完成] 报告已生成：")
    print("  " + base + ".txt")
    print("  " + base + ".json")
    if args.open:
        try:
            os.startfile(OUT_DIR)      # noqa: S606  打开报告目录
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
