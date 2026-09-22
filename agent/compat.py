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


if __name__ == "__main__":
    for _ln in lines():
        print(_ln)
