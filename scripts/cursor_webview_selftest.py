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
# ── 中键（滚轮键）特效：鲸鱼转一圈 360°（2026-09-17 用户点单）──
# ⚠️ 取样窗口**必须收紧到 spin() 函数体**（原来取"后面 900 字符"，越界扫进 setCustom() 里那句
#    合法换版本号的 `ver = '?v=' + Date.now()` ⇒ 判据假红；2026-09-17 修）
_i_spin, _i_spin_end = SRC_C.find("function spin()"), SRC_C.find("// 点击时点头")
_SPIN = SRC_C[_i_spin:_i_spin_end] if (_i_spin >= 0 and _i_spin_end > _i_spin) else ""
ck("A10 中键会转一圈：12 帧 canvas 预转（不新增素材文件）",
   "ev.button === 1" in SRC_C and "i * 2 * Math.PI / SPIN_FRAMES" in SRC_C and "toDataURL('image/png')" in SRC_C)
ck("A11 帧走 dataURL，**不拼 ?v=Date.now()**（每帧换 URL 会让光标闪回系统箭头）",
   "setStyle(frames[" in _SPIN and "Date.now()" not in _SPIN, _SPIN[:0] or "spin() 内无 Date.now")
ck("A12 转完一圈回到默认帧", "clearInterval(spinTimer); spinTimer = null; apply(); return;" in SRC_C)
ck("A13 帧没备好时退回「点头」（不许按了没反应）", "if(spin()) return;" in SRC_C and "applyNod();" in SRC_C)
ck("A14 中键**吃掉浏览器原生自动滚动**（否则光标被浏览器接管，只看得到「闪」）",
   "ev.preventDefault()" in SRC_C and "原生自动滚动" in SRC_C)
ck("A15 帧备好没有对外可读（判据/探针要能等到它，否则假红）",
   "framesReady: ()=>frames.length > 0" in SRC_C and "spin, framesReady" in SRC_C)
# ── 自研「滚轮模式」：中键要**真的滚**（用户 2026-09-17 第二次澄清：「我要的是滚轮…均匀平滑的速度往下滚动」；
#    上一版只做了"转"、把原生滚动 preventDefault 掉了 ⇒ 用户实测「它是旋转了，但是也滚不动啊」）──
ck("A16 中键＝**自研滚轮**（基础匀速 + rAF 持续滚 + 自绘徽标），不是只转一下",
   "PM_WHEEL" in SRC_C and "requestAnimationFrame(tick)" in SRC_C and "BASE + (off - DEAD) * GAIN" in SRC_C)
ck("A17 滚轮只在「鲸鱼光标开着」时接管（关掉光标就把原生行为原样留给用户）",
   "if(!WHALE_CURSOR.enabled()) return;" in SRC_C)
ck("A18 退出路径齐：再按中键 / 按任意其它键 / 滚真实滚轮 / Esc / 失焦",
   "document.addEventListener('wheel'" in SRC_C and "ev.keyCode === 27" in SRC_C
   and "window.addEventListener('blur'" in SRC_C and "if(on){ stop(); return; }" in SRC_C)
ck("A19 滚动目标现算（从落点往上找真能滚的祖先；找不到就整页滚）",
   "scrollHeight - n.clientHeight > 8" in SRC_C and "document.scrollingElement" in SRC_C)
ck("A20 自绘徽标＝那只鱼持续 360° 旋转（CSS 动画，不是一次性换帧）",
   "animation:pmWheelSpin 1.05s linear infinite" in SRC_C and "@keyframes pmWheelSpin" in SRC_C)

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
            out = subprocess.run([_EXE, "--cursorprobe", url], capture_output=True, timeout=180,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
            ck("B10 旋转帧已备好（异步造帧，探针要等到它）", field("spin_frames_ready") == "True")
            ck("B11 **按中键换成旋转帧**（dataURL，不是原图）", field("spin_applied") == "True",
               field("style_after_middle"))
            ck("B12 约 0.9 秒后回到默认鲸鱼帧（转完一圈就收）", field("spin_reverted") == "True",
               field("style_after_spin"))
            # ── 自研滚轮模式（活体）：真的在滚 + 徽标在转 + 左键退出 ──
            def _w(k):
                return [p.strip().lower() for p in (field(k) or "").split("|")]

            _t0, _w1, _w2, _w3, _w4 = field("wheel_t0"), _w("wheel_1"), _w("wheel_2"), _w("wheel_3"), _w("wheel_4")
            try:
                _d1 = float(_w1[1]) - float(_t0)
                _d2 = float(_w2[1]) - float(_w1[1])
                _d4 = abs(float(_w4[1]) - float(_w3[1]))
            except Exception:
                _d1 = _d2 = _d4 = -999.0
            ck("B13 中键进入滚轮模式", _w1[:1] == ["true"], field("wheel_1"))
            ck("B14 **它真的在滚**（0.7 秒内往下滚 >20px）", _d1 > 20, "滚了 %.0fpx" % _d1)
            ck("B15 滚是**持续的**（再来 0.3 秒又在滚）", _d2 > 8, "又滚 %.0fpx" % _d2)
            ck("B16 自绘滚轮徽标在（那只鱼）", _w1[2:3] == ["true"], str(_w1))
            ck("B17 那只鱼**在转**（两次取样的 transform 不同）", bool(_w1[3:4]) and _w1[3:4] != _w2[3:4],
               "%s → %s" % (_w1[3:4], _w2[3:4]))
            ck("B18 左键退出：模式关、徽标撤、不再滚",
               _w3[:1] == ["false"] and _w3[2:3] == ["false"] and _d4 < 5, "Δ=%.0fpx · %s" % (_d4, field("wheel_3")))
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
