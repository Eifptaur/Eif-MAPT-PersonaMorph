"""兼容性指纹：一台机器上"可能各不相同、而且真会把功能搞坏"的那些事实。

⛔ 为什么要有这个模块（2026-09-22 立；作者原话：「其实最重要的就是兼容性，你们有什么方案能解决
  兼容性问题？你看，好多人的电脑跟好多人的情况都不一样」）：
  我们已经被"**从版本推出行为**"这种写死表打过至少两次 ——
    ① 会话行点击到底要投主窗还是渲染子窗（同机同落点，2026-09-13 与 09-21 结论**正好相反**）；
    ② 适配层 `wechatauto/db.py` 对"页 1 是不是明文头"的**模式判断**（有人机器上是明文头、有人
       机器上是全加密；判错就解密出坏页、报成"数据库合并失败(文件被微信并发改写)"，而其实与
       并发无关 —— 另一位维护者在**别人机器上**实测出来的）。
  ⇒ 结论：兼容性**不能靠我们猜，只能靠每台机器上现测 + 把实测值带回来**。
     本模块只做**只读**探测：不碰微信进程内存、不动任何窗口、不写任何文件（判据守着这条）。
"""

from __future__ import annotations

import os
import sys


def _win() -> dict:
    """Windows 版本 / 位数 / 是否管理员（都走只读 API；失败留 null，不抛）。"""
    rep = {"release": "Windows", "build": "", "arch": "", "admin": None}
    try:
        rep["arch"] = "64-bit" if sys.maxsize > 2 ** 32 else "32-bit"
    except Exception:
        pass
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            rep["release"] = str(winreg.QueryValueEx(k, "ProductName")[0] or "Windows")
            rep["build"] = str(winreg.QueryValueEx(k, "CurrentBuildNumber")[0] or "")
            try:
                rep["build"] += ".%s" % str(winreg.QueryValueEx(k, "UBR")[0])
            except Exception:
                pass
        # ⚠️ `ProductName` 在 Win11 上仍是 "Windows 10"（微软没改这个值）⇒ 按 build 号纠正，
        #    否则报告里会出现"用户说他明明是 Win11"这种对不上的噪音。
        try:
            if int(str(rep["build"]).split(".")[0]) >= 22000:
                rep["release"] = "Windows 11"
        except Exception:
            pass
    except Exception:
        pass
    try:
        import ctypes
        rep["admin"] = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        rep["admin"] = None
    return rep


def _display() -> dict:
    """显示器：数量 / 主屏分辨率 / 系统缩放（DPI 感知差异是"点不准"的头号环境变量）。"""
    rep = {"monitors": None, "primary": "", "scale": ""}
    try:
        import ctypes
        u = ctypes.windll.user32
        try:
            u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))     # PerMonitorV2；失败也无妨
        except Exception:
            pass
        rep["primary"] = "%dx%d" % (int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1)))
        try:
            dpi = int(ctypes.windll.user32.GetDpiForSystem())
            rep["scale"] = "%d%%" % round(dpi / 96.0 * 100)
        except Exception:
            dc = u.GetDC(0)
            try:
                dpi = int(ctypes.windll.gdi32.GetDeviceCaps(dc, 88))   # LOGPIXELSX
                rep["scale"] = "%d%%" % round(dpi / 96.0 * 100)
            finally:
                u.ReleaseDC(0, dc)
        cnt = [0]

        def _cb(h, m, d, i):
            cnt[0] += 1
            return 1

        try:
            CB = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                                    ctypes.POINTER(ctypes.c_long * 4), ctypes.c_double)
            u.EnumDisplayMonitors(0, None, CB(_cb), 0)
            rep["monitors"] = cnt[0]
        except Exception:
            pass
    except Exception:
        pass
    return rep


def _wechat_window() -> dict:
    """微信主窗（**只枚举、不激活、不改几何**）：类名与可见性 —— UI 代与窗口状态的直接证据。"""
    rep = {"hwnd": 0, "class": "", "visible": None, "title": ""}
    try:
        from . import input_backend as _ib
        hwnd = int(_ib.find_main_window() or 0)
        rep["hwnd"] = hwnd
        if hwnd:
            import ctypes
            u = ctypes.windll.user32
            buf = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(ctypes.c_void_p(hwnd), buf, 256)
            rep["class"] = str(buf.value)
            rep["visible"] = bool(u.IsWindowVisible(ctypes.c_void_p(hwnd)))
            t = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(ctypes.c_void_p(hwnd), t, 256)
            rep["title"] = str(t.value)[:40]
    except Exception:
        pass
    return rep


def _data_dir() -> dict:
    """消息库目录这一滩：怎么定的 / 有几个账号 / 分片怎么命名 / 页 1 是明文头还是全加密。"""
    rep = {"dir": "", "how": "", "accounts": None, "shards": "", "pragma": "",
           "page1": "", "patched_lib": ""}
    try:
        from . import wechat_dir
        st = wechat_dir.status() or {}
        if not st.get("effective"):
            # 机器人没在跑时 `status()` 不会扫盘（它靠运行中 adapter 的 `_db_how` 说"实际在用哪个"）
            # ⇒ 报告里要的是"这台机器上到底有没有库"，所以这里显式扫一次（有界预算，只读）。
            try:
                _d = wechat_dir.decide(None, probe_all=True) or {}
                if _d.get("effective"):
                    st["effective"] = _d["effective"]
                    st["source_text"] = "扫盘（%s）" % (_d.get("source_text") or _d.get("src") or "")
            except Exception:
                pass
        rep["dir"] = str(st.get("effective") or st.get("now") or "")
        rep["how"] = str(st.get("source_text") or st.get("src") or st.get("effective_from") or "")
        if st.get("account"):
            rep["how"] += " · 账号=%s(%s)" % (st.get("account"), st.get("account_live"))
        up = str(st.get("account_up") or "")
        if not up and rep["dir"]:
            up = os.path.dirname(rep["dir"]) if os.path.basename(rep["dir"]) == "db_storage" else rep["dir"]
            up = os.path.dirname(up) if os.path.basename(up) not in ("xwechat_files", "") else up
        try:
            if up:
                rep["accounts"] = len(wechat_dir.accounts(up) or [])
        except Exception:
            rep["accounts"] = None
    except Exception:
        pass
    try:
        root = rep["dir"]
        if root and os.path.isdir(root):
            names = [n for n in os.listdir(os.path.join(root, "db_storage", "message"))
                     if n.endswith(".db")] if os.path.isdir(os.path.join(root, "db_storage", "message")) else []
            if not names:
                for d, _sub, fs in os.walk(root):
                    names += [f for f in fs if f.startswith("message") and f.endswith(".db")]
                    if names:
                        break
            rep["shards"] = ",".join(sorted(names)[:4])
            # 页 1 的头 16 字节：SQLite 明文头 vs 密文（**只读一小段，不解密、不写**）
            for d, _sub, fs in os.walk(root):
                cand = [f for f in fs if f.startswith("message") and f.endswith(".db")]
                if cand:
                    p = os.path.join(d, sorted(cand)[0])
                    with open(p, "rb") as fh:
                        head = fh.read(16)
                    rep["page1"] = ("密文（无 SQLite 明文头）" if head[:6] != b"SQLite"
                                    else "明文头（SQLite）")
                    break
    except Exception:
        pass
    try:
        import wechatauto.db as _wdb
        d = os.path.dirname(os.path.abspath(_wdb.__file__))
        baks = [n for n in os.listdir(d) if ".bak-" in n] if os.path.isdir(d) else []
        try:
            from . import replica_adapter as _ra
            ver = (_ra.version_report() or {}).get("installed") or ""
        except Exception:
            ver = ""
        rep["patched_lib"] = ("%s%s" % (ver or "?", "（本地打过补丁：%s）" % ",".join(baks[:2]) if baks else ""))
    except Exception:
        pass
    return rep


def _runtime() -> dict:
    """跑起来的这一套：Python / 端口 / WebView2 / 控制台地址活性。"""
    rep = {"python": "", "port": "", "webview2": "", "console_live": None}
    try:
        rep["python"] = "%d.%d.%d (%s)" % (sys.version_info[0], sys.version_info[1], sys.version_info[2],
                                           "portable" if "runtime" in sys.executable.lower() else "system")
    except Exception:
        pass
    try:
        from .notify_ui import console_url, webview_ready, _url_live
        u = console_url()
        rep["port"] = str(u).split("/")[2] if "//" in str(u) else ""
        rep["console_live"] = bool(_url_live(u))
        rep["webview2"] = str((webview_ready() or {}).get("runtime_ver") or "未装/未知")
    except Exception:
        pass
    return rep


def fingerprint() -> dict:
    """一张"这台机器长什么样"的只读快照（任何一节失败都不影响其它节）。"""
    out = {}
    for key, fn in (("windows", _win), ("display", _display), ("wechat_window", _wechat_window),
                    ("data_dir", _data_dir), ("runtime", _runtime)):
        try:
            out[key] = fn()
        except Exception as e:                                    # noqa: BLE001
            out[key] = {"error": "%s: %s" % (type(e).__name__, str(e)[:60])}
    return out


def lines() -> list:
    """给人看 / 给作者粘贴的几行（**不含口令、不含 wxid 全名、不含账号目录名**）。"""
    f = fingerprint()
    w, d, c = f.get("windows", {}) or {}, f.get("display", {}) or {}, f.get("wechat_window", {}) or {}
    g, r = f.get("data_dir", {}) or {}, f.get("runtime", {}) or {}
    out = [
        "系统: %s build %s · %s · 管理员=%s" % (w.get("release") or "?", w.get("build") or "?",
                                              w.get("arch") or "?", w.get("admin")),
        "显示: %s · 缩放 %s · 显示器 %s 个" % (d.get("primary") or "?", d.get("scale") or "?",
                                             d.get("monitors")),
        "微信主窗: 类名=%s · 可见=%s · 标题=%s" % (c.get("class") or "(没找到)", c.get("visible"),
                                              c.get("title") or ""),
        "运行环境: Python %s · 控制台端口 %s · 在听=%s · WebView2=%s" % (
            r.get("python") or "?", r.get("port") or "?", r.get("console_live"), r.get("webview2") or "?"),
        "消息库: 来源=%s · 账号目录 %s 个 · 分片=%s" % (g.get("how") or "?", g.get("accounts"),
                                               g.get("shards") or "?"),
        "库页1: %s · 适配层=%s" % (g.get("page1") or "?", g.get("patched_lib") or "?"),
    ]
    return out


def _click_pref() -> str:
    """「会话行点击该投哪个窗」这条轴的事实：**学习到的偏好**（不是按版本猜的表）。"""
    try:
        from . import click_pref as _cp
        d = {}
        try:
            import json
            with open(getattr(_cp, "PATH", ""), encoding="utf-8") as fh:
                d = json.load(fh) or {}
        except Exception:
            d = {}
        if not isinstance(d, dict) or not d:
            return "未记录（还没成功过 ⇒ 用默认候选顺序）"
        keys = d.get("keys") if isinstance(d.get("keys"), dict) else {}
        items = []
        for k, v in list(keys.items())[:2]:
            if isinstance(v, dict):
                items.append("%s ⇒ 成功过 %s（成 %s / 败 %s）"
                             % (str(k)[:44], v.get("ok") or "?", v.get("okN"), v.get("failN")))
        return "；".join(items) if items else "未记录（还没成功过 ⇒ 用默认候选顺序）"
    except Exception:
        return "未知（读不到偏好文件）"


def _update_state() -> str:
    """更新源这条轴：只报**上一次探测的结论**（不在体检里联网，联网留给更新面板）。"""
    try:
        import json
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "update_state.json")
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh) or {}
        st = str(d.get("lastStatus") or d.get("status") or "")
        src = str(d.get("lastSource") or d.get("source") or "")
        if st or src:
            return "上次探测：%s%s" % (st or "?", ("（源=%s）" % src) if src else "")
    except Exception:
        pass
    return "未探测（点控制台「更新」即得）"


def axes(how: dict | None = None) -> list:
    """**机器可读的兼容性矩阵**：11 条"我们真踩过"的差异轴 → 此刻探测到的事实。

    为什么是"事实"而不是"支持/不支持"（2026-09-22，依据业界调研）：
      官方口径统一＝**探测能力，别按版本分支**（MDN / 微软 Edge 文档）；微软自家的 `winapp ui`
      也是拿一张"框架 × 能力"矩阵说话。⇒ 我们这张表的每一行都必须是**这台机器上现测到的值**，
      测不到就如实写"未知"，**绝不允许按版本外推**。
    """
    f = fingerprint() if not isinstance(how, dict) else fingerprint()
    w, d, c = f.get("windows", {}) or {}, f.get("display", {}) or {}, f.get("wechat_window", {}) or {}
    g, r = f.get("data_dir", {}) or {}, f.get("runtime", {}) or {}
    _cls = str(c.get("class") or "")
    return [
        {"axis": "微信版本 / UI 代", "fact": "主窗类名（UI 代的直接证据）+ 可见性",
         "value": ("%s · 可见=%s" % (_cls or "没找到主窗", c.get("visible")))},
        {"axis": "Windows 版本", "fact": "ProductName（按 build 号纠正）+ build",
         "value": "%s build %s" % (w.get("release") or "?", w.get("build") or "?")},
        {"axis": "DPI 缩放 / 多显示器", "fact": "主屏分辨率 + 系统缩放 + 显示器数",
         "value": "%s · %s · %s 个" % (d.get("primary") or "?", d.get("scale") or "?",
                                      d.get("monitors"))},
        {"axis": "会话行点击投哪个窗", "fact": "学习到的偏好（按 版本×适配层×尺寸×DPI×后端 记）",
         "value": _click_pref()},
        {"axis": "消息库目录 / 账号数", "fact": "实际在用目录的来源 + 账号目录数 + 分片名",
         "value": "来源=%s · 账号 %s 个 · 分片=%s" % (g.get("how") or "?", g.get("accounts"),
                                                 g.get("shards") or "?")},
        {"axis": "加密模式（页 1）", "fact": "库文件前 16 字节 = SQLite 魔数？",
         "value": str(g.get("page1") or "未知（没定位到库文件）")},
        {"axis": "WebView2", "fact": "运行时版本（读注册表 pv）",
         "value": str(r.get("webview2") or "?")},
        {"axis": "Python 运行环境", "fact": "解释器版本 + 便携/系统",
         "value": str(r.get("python") or "?")},
        {"axis": "控制台端口", "fact": "权威地址里的端口 + 此刻有没有人在听",
         "value": "%s · 在听=%s" % (r.get("port") or "?", r.get("console_live"))},
        {"axis": "更新源可达性 / 延迟", "fact": "上一次探测的结论（体检不联网）",
         "value": _update_state()},
        {"axis": "权限 / 完整性级别（UIPI）", "fact": "我方进程是否管理员（与微信不一致时注入会被静默拦）",
         "value": "本进程管理员=%s" % w.get("admin")},
    ]


def axis_lines(how: dict | None = None) -> list:
    """矩阵的"给人看"形态（报告里跟指纹一起贴回来）。"""
    out = []
    for a in axes(how):
        out.append("  [%s] %s ← %s" % (a["axis"], a["value"], a["fact"]))
    return out


def _dpi_awareness() -> str:
    """DPI 感知模式（0=不感知 1=系统 2=每显示器）——**可读本身**就是这条轴的行为判据。"""
    try:
        import ctypes
        u = ctypes.windll.user32
        ctx = u.GetThreadDpiAwarenessContext()
        aw = int(u.GetAwarenessFromDpiAwarenessContext(ctx))
        return {0: "不感知（UNAWARE，坐标要按 DPI 换算）", 1: "系统级（SYSTEM）",
                2: "每显示器（PER_MONITOR）"}.get(aw, "未知值 %s" % aw)
    except Exception as e:                                        # noqa: BLE001
        return "读不到（%s）" % type(e).__name__


def _can_listen() -> str:
    """本机能不能起回环监听（控制台那条路的前提）——真 bind 一个临时端口再关掉。"""
    try:
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.listen(1)
        s.close()
        return "可以（临时端口 %d 起得来又立刻关掉）" % port
    except Exception as e:                                        # noqa: BLE001
        return "不行：%s: %s" % (type(e).__name__, str(e)[:40])


def _python_ok() -> str:
    """解释器是否在支持区间（我们在 3.10~3.12 上实测过；其它版本只提示不拦）。"""
    v = (sys.version_info[0], sys.version_info[1])
    ok = (3, 10) <= v <= (3, 12)
    return "%d.%d.%d · %s · %s" % (sys.version_info[0], sys.version_info[1], sys.version_info[2],
                                  "64-bit" if sys.maxsize > 2 ** 32 else "32-bit",
                                  "在支持区间" if ok else "**不在实测区间（3.10~3.12）**，只提示不拦")


def smoke() -> list:
    """**最小冒烟矩阵**：11 条轴各一条**行为断言**（不是"支持/不支持"，也不是"读了个值"）。

    判据形态＝ `{axis, probe(做了什么), verdict(ok/skip/fail), evidence}`：
      · `ok`   ＝ 这条能力**在这台机器上当场成立**（做了那件事、拿到了结果）
      · `skip` ＝ 这次测不了，**并说清为什么**（例：微信没在跑 / 还没触发过 / 体检不联网）
      · `fail` ＝ 做了但确实不行（这才是要处理的那类）
    全部**只读**：不碰微信进程内存、不动窗口、不发消息、不写任何文件（判据守着这三条）。
    """
    f = fingerprint()
    w, d = f.get("windows", {}) or {}, f.get("display", {}) or {}
    c, g = f.get("wechat_window", {}) or {}, f.get("data_dir", {}) or {}
    r = f.get("runtime", {}) or {}
    out = []

    def add(axis, probe, verdict, evidence):
        out.append({"axis": axis, "probe": probe, "verdict": verdict, "evidence": evidence})

    # 1. 微信版本 / UI 代
    if c.get("hwnd"):
        add("微信版本 / UI 代", "找到微信主窗并读类名（UI 代的直接证据）", "ok",
            "类名=%s · 可见=%s" % (c.get("class"), c.get("visible")))
    else:
        add("微信版本 / UI 代", "找微信主窗", "skip", "现在没找到主窗（微信没在跑 / 收在托盘）⇒ 这条测不了")
    # 2. Windows 版本
    add("Windows 版本", "读系统版本与 build", "ok" if w.get("build") else "fail",
        "%s build %s · %s" % (w.get("release"), w.get("build"), w.get("arch")))
    # 3. DPI / 多显示器
    _dpi = _dpi_awareness()
    add("DPI 缩放 / 多显示器", "读主屏分辨率 / 缩放 / 显示器数 + 读本进程 DPI 感知",
        "ok" if (d.get("primary") and "读不到" not in _dpi) else "fail",
        "%s · %s · %s 个 · 本进程=%s" % (d.get("primary"), d.get("scale"), d.get("monitors"), _dpi))
    # 4. 会话行点击投哪个窗（学习到的偏好＝本机真的成功过）
    _cp = _click_pref()
    add("会话行点击投哪个窗", "读「哪个目标窗成功过」的学习记录（click_pref）",
        "ok" if "成功过" in _cp else "skip", _cp)
    # 5. 消息库目录 / 账号数
    if g.get("dir"):
        add("消息库目录 / 账号数", "定位在用的库目录 + 数账号 + 列分片", "ok",
            "来源=%s · 账号 %s 个 · 分片=%s" % (g.get("how"), g.get("accounts"), g.get("shards") or "?"))
    else:
        add("消息库目录 / 账号数", "定位在用的库目录", "skip",
            "这次没定位到（来源=%s）⇒ 换台机器/换目录时这一步会先失败" % (g.get("how") or "?"))
    # 6. 加密模式（页 1）
    _p1 = str(g.get("page1") or "")
    add("加密模式（页 1）", "读库文件前 16 字节判明文头/密文",
        "ok" if _p1.startswith(("明文头", "密文")) else "skip",
        _p1 or "没定位到库文件 ⇒ 这条测不了")
    # 7. WebView2
    _wv = str(r.get("webview2") or "")
    add("WebView2", "读 WebView2 运行时版本（决定控制台用自家窗口还是回退浏览器）",
        "ok" if _wv and "未装" not in _wv else "skip", _wv or "未装 ⇒ 会自动回退浏览器（不是故障）")
    # 8. Python
    _py = _python_ok()
    add("Python 运行环境", "解释器版本/位数是否在实测区间", "ok" if "在支持区间" in _py else "skip", _py)
    # 9. 控制台端口
    _ls = _can_listen()
    add("控制台端口", "真起一次回环监听（并看权威端口此刻在不在听）",
        "ok" if _ls.startswith("可以") else "fail",
        "%s · 权威端口 %s 在听=%s" % (_ls, r.get("port") or "?", r.get("console_live")))
    # 10. 更新源（体检不联网 —— 宁可 skip，也不假装测过）
    add("更新源可达性 / 延迟", "读上次探测结论（**体检不联网**）", "skip",
        "%s ——这条**体检不联网**，所以只报上次结论；要现测就点控制台「更新」" % _update_state())
    # 11. 权限 / UIPI
    add("权限 / 完整性级别（UIPI）", "读本进程是否管理员（与微信不一致时注入会被静默拦）",
        "ok" if w.get("admin") is not None else "skip",
        "本进程管理员=%s（微信侧要等接入后才比得上）" % w.get("admin"))
    return out


def smoke_lines() -> list:
    """冒烟矩阵的 ASCII 形态（判据逐行解析；也给用户复制）。"""
    out = []
    for a in smoke():
        out.append("AXIS|%s|%s|%s|%s" % (a["axis"], a["verdict"], a["probe"], a["evidence"]))
    return out


if __name__ == "__main__":
    for _ln in lines():
        print(_ln)
    print("—— 兼容性矩阵（11 条轴，全部是现测事实）——")
    for _ln in axis_lines():
        print(_ln)
    print("—— 最小冒烟矩阵（11 条轴，逐条行为断言）——")
    for _ln in smoke_lines():
        print(_ln)
