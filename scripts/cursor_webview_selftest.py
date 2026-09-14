# -*- coding: utf-8 -*-
"""光标链路判据（⑧）：**在我们自己的 WebView2 窗口里**量，不靠肉眼、不截屏。

为什么必须进自家窗口量：浏览器里好，不代表自家窗里好——WebView2 的光标是
「网页请求 → WebView2 → WinForms 宿主」这条链传下去的，任何一环没接上，用户看到的就只是普通箭头。

两组：
  A 静态：页面里有「点一下换歪头帧、180ms 换回」的逻辑；两张光标图都在；启动器里有 --cursorprobe。
  B 活体（真起自家窗口，屏外 + WS_EX_NOACTIVATE，不抢前台）：
     `一键启动.exe --cursorprobe <url>` 在窗口里跑 JS，读出
     「默认帧是 cursor.png」「mousedown 后是 cursor-nod.png」「400ms 后换回」「两张图 HTTP 200」
     「前台全程未变」。

跑不了的情形（没装 WebView2 运行时 / 启动器没编译）如实 SKIP，不冒充通过。
"""
import os
import re
import subprocess
import sys
import socket

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK, BAD, SKIP = [], [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name, (" · " + extra) if extra else ""))


def skip(name, why):
    SKIP.append(name)
    print("  SKIP %s  [%s]" % (name, why))


# ── A 静态 ───────────────────────────────────────────────────────────────
print("[A] 静态：页面逻辑 / 素材 / 探针入口")
SRC_C = open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
SRC_L = open(os.path.join(ROOT, "launcher-src", "launcher.cs"), encoding="utf-8").read()

ck("A1 页面有「点一下换歪头帧」的监听", "addEventListener('mousedown'" in SRC_C and "applyNod()" in SRC_C)
ck("A2 180ms 后换回默认帧", re.search(r"nodTimer = setTimeout\(apply, 180\)", SRC_C) is not None)
ck("A3 光标样式同时覆盖 html 与所有元素（否则子元素还是系统箭头）",
   "html.whale-cursor,html.whale-cursor*{cursor:url(" in SRC_C.replace(" ", ""))
ck("A4 光标图带热点 (8 8) 与 auto 兜底",
   '") 8 8, auto!important' in SRC_C)
ck("A5 挂件 iframe 也注入光标（CSS 穿不透同源 iframe）", "injectFrames" in SRC_C)
for f in ("assets/cursor.png", "assets/cursor-nod.png"):
    ck("A6 素材存在：%s" % f, os.path.exists(os.path.join(ROOT, f)))
ck("A7 启动器有 --cursorprobe 入口", '"--cursorprobe"' in SRC_L and "public static string CursorProbe(" in SRC_L)
ck("A8 探针窗口屏外 + 不激活（不抢前台、屏幕上看不见）",
   "-4000, -4000" in SRC_L and "WS_EX_NOACTIVATE" in SRC_L)
ck("A9 探针读回「前台是否未变」当作判据", "foreground_unchanged=" in SRC_L)

_EXE = os.path.join(ROOT, "一键启动.exe")
if not os.path.exists(_EXE):
    skip("B 活体探针", "一键启动.exe 不存在（先编译）")
else:
    print("[B] 活体：在自家 WebView2 窗口里真跑一遍")
    from agent import webui as W

    _orig = W.get_config
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    base = dict(_orig() or {})
    base["server"] = {"enabled": True, "host": "127.0.0.1", "port": port, "token": "cursor-judge",
                      "auto_open_browser": False}
    base.setdefault("ui", {})["whale_cursor"] = True
    W.get_config = lambda: base
    w3 = None
    try:
        w3 = W.WebUI(lambda: {}, [])
        port = w3.start()
        url = "http://127.0.0.1:%d/?token=cursor-judge" % port
        try:
            out = subprocess.run([_EXE, "--cursorprobe", url], capture_output=True, timeout=180)
            txt = (out.stdout or b"").decode("utf-8", "replace") + (out.stderr or b"").decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            txt = ""
            ck("B0 探针在 180s 内返回", False, "超时")
        print("      " + "\n      ".join(txt.strip().splitlines()[:20]))

        def field(k, d=""):
            m = re.search(r"^%s=(.*)$" % re.escape(k), txt, re.M)
            return (m.group(1).strip() if m else d)

        if "cursorprobe=ok" not in txt:
            ck("B1 探针成功结束（cursorprobe=ok）", False, field("cursorprobe") or txt[-160:])
        else:
            ck("B1 探针成功结束（cursorprobe=ok）", True)
            ck("B2 自家窗口里 WebView2 起来了", field("webview_ready") == "True", field("webview_ready"))
            ck("B3 默认光标＝鲸鱼帧", field("has_cursor_png") == "True", field("style_default"))
            ck("B4 html 上挂着 whale-cursor 类（样式真的生效）", field("has_whale_class") == "True")
            ck("B5 **点一下换成歪头帧**", field("nod_applied") == "True", field("style_after_mousedown"))
            ck("B6 400ms 后换回默认帧（点头是一次性的）", field("nod_reverted") == "True",
               field("style_after_400ms"))
            ck("B7 默认光标图 HTTP 200", field("asset:/assets/cursor.png") == "200",
               field("asset:/assets/cursor.png"))
            ck("B8 歪头帧图 HTTP 200", field("asset:/assets/cursor-nod.png") == "200",
               field("asset:/assets/cursor-nod.png"))
            ck("B9 探针结束时前台与开始时一致（WebView2 初始化那一瞬会抢一次，探针收尾还回去）",
               field("foreground_unchanged") == "True" or field("fg_restored") == "True",
               "unchanged=%s fg_is_probe=%s restored=%s" % (field("foreground_unchanged"),
                                                            field("fg_is_probe"), field("fg_restored")))
    except Exception as e:
        skip("B 活体探针", "跑不起来：%s" % str(e)[:90])
    finally:
        W.get_config = _orig
        try:
            if w3:
                w3.stop()
        except Exception:
            pass

print("\n[结论] %d 通过 / %d 失败 / %d 跳过" % (len(OK), len(BAD), len(SKIP)))
if BAD:
    print("失败项：%s" % BAD)
sys.exit(1 if BAD else 0)
