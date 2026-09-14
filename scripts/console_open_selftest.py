# -*- coding: utf-8 -*-
"""控制台"地址来源 + 谁去开窗 + 窗口能不能拉"判据（2026-09-14，另一台机器实测的症状收口）。

症状（用户原话）：「**点击一键关闭也关不掉，关完还显示"保证唯一"**」·
「**我点那个"打开控制台"，弹出的又是浏览器的弹窗**」·
「**而且又显示 {"error": "unauthorized"}**」·
「**窗口大小也不对，而且还拉伸放缩不了，左边的导航各个项的 UI 靠得太近了**」。

根因与本判据的对应：
  ① **401＝手写字符串找 JSON 字段**：启动器原来用 `IndexOf("\"token\"")` 取口令，
     而 config.json 里**排在前面的 `cloud.token` 是空串** ⇒ 拼出 `/?token=` ⇒ 服务端 401。
     新口径：**token 的拥有者（webui 启动时）把现成地址写进 `logs/console.url`，别人只读**。
     判据用真 config.json 跑一次"旧写法"当**阴性对照**，证明它确实抓错。
  ② **双窗/误弹浏览器**：`agent/util.take_console_lock` 是**所有开窗入口共用的一把锁**；
     `notify_ui.open_console` 是**唯一开窗实现**（先查 readiness，再决定自家窗口/浏览器）。
     锁在有效期内必须**一次都不 Popen**。
  ③ **一键关闭关不掉**：入口脚本已改名 `wx_agent.py` → `scripts/persona_morph.py`，
     close.cs 的 markers 里缺了它 ⇒ 看门狗被杀、控制台子进程活着 ⇒ 再点一键启动弹"为保持唯一…"。
  ④ **窗口拉伸放缩不了**：无边框窗要**自己留一圈内边距**（环内像素归父窗）+ `WM_NCHITTEST` 回缩放码，
     否则客户区被 WebView2 子窗盖住、命中测试全被它吃掉；圆角 Region 还必须随尺寸重算。

用法：py -3 scripts/console_open_selftest.py
"""
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["WX_NO_UI_POP"] = "1"

from agent import notify_ui as NU          # noqa: E402
from agent import util as U                # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


def skip(name, why=""):
    global SKIP
    SKIP += 1
    print("  SKIP {}  [{}]".format(name, why))


def src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def rm_url_file():
    try:
        os.remove(U.console_url_path())
    except Exception:
        pass


_urlf = U.console_url_path()
_old_url = None
try:
    if os.path.exists(_urlf):
        with open(_urlf, encoding="utf-8") as f:
            _old_url = f.read()
except Exception:
    _old_url = None

print("── A. 控制台地址：现成文件优先（token 由拥有者落盘）──")
try:
    ok("原子落盘成功", U.write_console_url("http://127.0.0.1:39999/?token=AAA") is True)
    ok("读回＝写入值（没有多余空白）", U.read_console_url() == "http://127.0.0.1:39999/?token=AAA",
       U.read_console_url())
    ok("没有 .tmp 残留（os.replace 是原子的）", not os.path.exists(_urlf + ".tmp"))
    ok("地址优先取自 console.url（配置里有别的 token 也不影响）",
       NU.console_url() == "http://127.0.0.1:39999/?token=AAA", NU.console_url())
    ok("锚点仍走 #（不改成 query，控制台按 #sec-xxx 跳节）",
       NU.console_url("sec-model").endswith("#sec-model"))
finally:
    rm_url_file()

print("── B. 阴性对照：旧写法（IndexOf 第一个 \"token\"）确实会抓错 ──")
_cf = os.path.join(ROOT, "config.json")
if os.path.exists(_cf):
    _txt = open(_cf, encoding="utf-8").read()
    try:
        _cfg = json.loads(_txt)
    except Exception:
        _cfg = {}
    _real_tok = str(((_cfg.get("server") or {}).get("token")) or "")
    _i = _txt.find('"token"')
    _old_tok = ""
    if _i >= 0:
        _j = _txt.find('"', _i + 8)
        _k = _txt.find('"', _j + 1)
        if _j >= 0 and _k > _j:
            _old_tok = _txt[_j + 1:_k]
    if _real_tok:
        ok("旧写法抓到的**不是** server.token（这就是 401 的来源）", _old_tok != _real_tok,
           "旧=%r 新=%r" % (_old_tok[:6], _real_tok[:6]))
        _u = NU.console_url()
        ok("server.token 非空 ⇒ 新写法必须带口令", ("token=" in _u) and _u.split("token=")[-1] == _real_tok)
    else:
        ok("config.json 里 server.token 为空 ⇒ 地址不带口令（不伪造）", "token=" not in NU.console_url())
else:
    skip("B. 阴性对照", "没有 config.json（判据机器未初始化）")

print("── C. WebView2 readiness：先查清楚再决定用哪个窗口 ──")
_rd = NU.webview_ready()
ok("报告字段齐全（exe/dll/runtime/why/ok）",
   all(k in _rd for k in ("exe", "dll", "runtime", "why", "ok")), str(sorted(_rd.keys()))[:60])
ok("本机 exe/程序集在位", _rd["exe"] and _rd["dll"], "exe=%s dll=%s" % (_rd["exe"], _rd["dll"]))
if _rd["runtime"]:
    ok("WebView2 运行时已装（读注册表 pv）", bool(_rd["runtime_ver"]), _rd["runtime_ver"])
else:
    ok("运行时缺失 ⇒ why 说清楚 + ok=False", (not _rd["ok"]) and bool(_rd["why"]), _rd["why"])

print("── D. open_console：单点开窗（一把锁 + 一个优先级）──")
_real_popen, _real_ready, _real_url = NU.subprocess.Popen, NU.webview_ready, NU.console_url
_real_lock = U.take_console_lock


class _FakePopen(object):
    def __init__(self, cmd, **kw):
        calls.append(cmd)


calls = []
try:
    NU.subprocess.Popen = _FakePopen
    NU.console_url = lambda anchor="": "http://127.0.0.1:39998/?token=ZZZ"
    U.take_console_lock = lambda *a, **k: True

    NU.webview_ready = lambda: {"exe": True, "dll": True, "runtime": True, "runtime_ver": "1.0",
                                "why": "", "ok": True}
    calls[:] = []
    _r1 = NU.open_console(take_lock=False)
    ok("① 自家窗口就绪 ⇒ 走 WebView2，只开一次",
       _r1.get("how") == "webview" and len(calls) == 1 and calls[0][1] == "--console", str(_r1.get("how")))
    ok("① 传的地址就是权威地址（带口令）", bool(calls) and calls[0][2].endswith("token=ZZZ"),
       calls[0][2] if calls else "")

    NU.webview_ready = lambda: {"exe": False, "dll": False, "runtime": False, "runtime_ver": "",
                                "why": "目录里没有 一键启动.exe", "ok": False}
    calls[:] = []
    _r2 = NU.open_console(take_lock=False)
    ok("② 自家窗口不可用 ⇒ 才回退浏览器，且**如实报原因**",
       _r2.get("how") == "browser" and bool(_r2.get("why")) and len(calls) == 1, str(_r2.get("why"))[:30])

    U.take_console_lock = lambda *a, **k: False
    calls[:] = []
    _r3 = NU.open_console()
    ok("③ 锁被占 ⇒ how=skip 且**一次都不开**（防双窗的硬判据）",
       _r3.get("how") == "skip" and len(calls) == 0, str(_r3.get("why"))[:30])

    U.take_console_lock = lambda *a, **k: True
    NU.console_url = lambda anchor="": ""
    _r4 = NU.open_console()
    ok("④ 拿不到地址 ⇒ ok=False + 原因（不瞎开一个 401 的页）",
       _r4.get("ok") is False and bool(_r4.get("why")), str(_r4.get("why"))[:40])
finally:
    NU.subprocess.Popen, NU.webview_ready = _real_popen, _real_ready
    NU.console_url = _real_url
    U.take_console_lock = _real_lock
    rm_url_file()

print("── D2. 真锁的行为（不 mock）：一次成功、期内再抢失败、过期可再抢 ──")
try:
    rm_url_file()
    try:
        os.remove(U.console_lock_path())
    except Exception:
        pass
    ok("第一次拿到锁", U.take_console_lock() is True)
    ok("同一把锁 90 秒内第二次拿不到", U.take_console_lock() is False)
    ok("锁新鲜度可读（fresh=True）", U.console_lock_fresh() is True)
    with open(U.console_lock_path(), "w", encoding="utf-8") as f:
        f.write("1")                      # 伪造一个很旧的锁
    ok("过期锁不算新鲜", U.console_lock_fresh() is False)
    ok("过期锁可以再抢到", U.take_console_lock() is True)
finally:
    try:
        os.remove(U.console_lock_path())
    except Exception:
        pass

print("── E. 端到端：真起一个控制台，用落盘的地址访问必须 200（空口令地址必须 401）──")
try:
    import socket
    import urllib.error
    import urllib.request

    from agent import webui as W

    _s = socket.socket()
    _s.bind(("127.0.0.1", 0))
    _free = _s.getsockname()[1]
    _s.close()
    _orig_get = W.get_config
    _base = dict(_orig_get() or {})
    _base["server"] = {"enabled": True, "host": "127.0.0.1", "port": _free,
                       "token": "judge-token-1234567890", "auto_open_browser": False}
    W.get_config = lambda: _base
    _w = None
    try:
        _w = W.WebUI(lambda: {"state": "judge"}, [])
        _port = _w.start()
        _file_url = U.read_console_url()
        ok("webui.start() 之后 logs/console.url 已落盘（谁拥有 token 谁写）",
           _file_url.startswith("http://127.0.0.1:") and _file_url.endswith("token=judge-token-1234567890"),
           _file_url.replace("judge-token-1234567890", "***"))

        def _code(u):
            try:
                with urllib.request.urlopen(u, timeout=5) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                return e.code
            except Exception as e:
                return "ERR:%s" % e

        _good = _code(_file_url)
        ok("落盘的地址访问 ⇒ 200（不是 401）", _good == 200, str(_good))
        _bad = _code("http://127.0.0.1:%d/?token=" % _port)
        ok("阴性对照：空口令地址 ⇒ 401 unauthorized（旧写法就是这样打开的）", _bad == 401, str(_bad))
    finally:
        try:
            if _w is not None:
                _w.stop()
        except Exception:
            pass
        W.get_config = _orig_get
except Exception as e:
    skip("E. 端到端", "起不了控制台：%s" % e)
finally:
    rm_url_file()

print("── F. 接线：onestart / persona_morph 不再各开一处 ──")
_on = src("scripts/onestart.py")
_wx = src("scripts/persona_morph.py")
ok("persona_morph 走唯一的 open_console", "from agent.notify_ui import open_console" in _wx)
ok("persona_morph 里没有自己写的浏览器锁（[browser-lock] 已撤）", "[browser-lock]" not in _wx)
ok("persona_morph 不再自己 cmd start 开浏览器", '"cmd", "/c", "start"' not in _wx)
ok("persona_morph 仍尊重 auto_open_browser 开关", 'auto_open_browser", True' in _wx)
ok("控制台地址只由 webui 落盘（agent/webui.py 里有 write_console_url）", "write_console_url" in src("agent/webui.py"))
ok("onestart 侧开窗也走同一实现", "notify_ui" in _on or "open_console" in _on)

print("── G. 一键关闭：关得掉（markers 含现行入口 + 关后回读端口）──")
_cl = src("launcher-src/close.cs")
ok("close.cs markers 含 persona_morph.py（现行入口）", "persona_morph.py" in _cl)
ok("close.cs 有端口占用兜底 + 关后回读", "PortOwner(" in _cl and "仍被 pid" in _cl)
ok("close_all.ps1 也含 persona_morph.py", "persona_morph.py" in src("scripts/close_all.ps1"))

print("── H. 自家控制台窗口：尺寸按 DPI/工作区算 + 能拉边缩放 ──")
_exe = os.path.join(ROOT, "一键启动.exe")
if os.path.exists(_exe):
    _out = subprocess.run([_exe, "--winprobe"], capture_output=True, cwd=ROOT).stdout.decode("utf-8", "replace")
    _lines = [l.strip() for l in _out.splitlines()]
    _g = {}
    for l in _lines:
        if l.startswith("geom k="):
            _p = dict(x.split("=") for x in l.split()[1:])
            _g[_p["k"]] = _p
    ok("几何纯函数：四档缩放都有结果", len(_g) == 4, str(sorted(_g.keys())))
    if "1.25" in _g and "1" in _g:
        _w1 = int(_g["1"]["client"].split("x")[0])
        _w125 = int(_g["1.25"]["client"].split("x")[0])
        ok("125% 那档客户区大于 100% 那档（按 DPI 放大，不是写死像素）", _w125 > _w1,
           "100%%=%d 125%%=%d" % (_w1, _w125))
        ok("缩放环随 DPI 变宽（125% 的环比 100% 的环宽）",
           int(_g["1.25"]["ring"]) > int(_g["1"]["ring"]),
           "ring %s / %s" % (_g["1"]["ring"], _g["1.25"]["ring"]))

    _live = [l for l in _lines if l.startswith("live ")]
    if _live:
        _L = _live[0]
        _W, _H = [int(x) for x in _L.split("client=")[1].split(" ")[0].split("x")]
        _pad = [int(x) for x in _L.split("pad=")[1].split(" ")[0].split(",")]
        ok("活窗口：无边框", "border=None" in _L, _L)
        ok("留了缩放环（左/右/下 ≥5px）", _pad[0] >= 5 and _pad[2] >= 5 and _pad[3] >= 5, str(_pad))
        ok("顶部 0（标题带要能拖，不被缩放环抢走）", _pad[1] == 0, str(_pad))
        _wv = [l for l in _lines if "ctrl WebView2" in l]
        if _wv:
            _x = int(_wv[0].split("X=")[1].split(",")[0])
            ok("WebView2 被环让开（左边距＝环宽，不盖住缩放环）", _x == _pad[0], "x=%d ring=%d" % (_x, _pad[0]))
        else:
            skip("WebView2 让开环", "探针里没有 WebView2 控件行")
        _hit = {}
        for l in _lines:
            if l.startswith("hit("):
                _k, _v = l[4:].split("=")
                _hit[_k.rstrip(")")] = int(_v)
        _need = {
            "2,%d" % (_H // 2): 10,                 # 左边 ⇒ HTLEFT
            "%d,%d" % (_W - 2, _H // 2): 11,        # 右边 ⇒ HTRIGHT
            "%d,%d" % (_W // 2, _H - 2): 15,        # 底边 ⇒ HTBOTTOM
            "2,%d" % (_H - 2): 16,                  # 左下角 ⇒ HTBOTTOMLEFT
            "%d,%d" % (_W - 2, _H - 2): 17,         # 右下角 ⇒ HTBOTTOMRIGHT
            "%d,5" % (_W // 2): 1,                  # 顶部 ⇒ HTCLIENT（拖标题带，不缩放）
            "%d,%d" % (_W // 2, _H // 2): 1,        # 中部 ⇒ HTCLIENT
        }
        _bad = {k: _hit.get(k) for k, v in _need.items() if _hit.get(k) != v}
        ok("命中码：边/角给缩放码、顶与中部给 HTCLIENT", not _bad,
           str(_bad) if _bad else "7 点全中（%d×%d）" % (_W, _H))
    else:
        skip("活窗口探针", "没有 live 行")
    ok("源码：WM_NCHITTEST 生效点 + 圆角随尺寸重算",
       "m.Msg == 0x0084" in src("launcher-src/launcher.cs") and
       "f.Resize += delegate { Reclip(f); }" in src("launcher-src/stylekit.cs"))
else:
    skip("H. 自家控制台窗口探针", "没编译出 一键启动.exe")

print("── I. 左导航间距（用户：靠得太近，放大 + 拉开）──")
_html = src("agent/console_html.py")
from agent import console_html as _CH          # noqa: E402
_css = _CH.HTML.split("<style>", 1)[1].split("</style>", 1)[0]
_i = _css.rfind(".side .nav a{")                # 生效的是最后一条同选择器规则
_seg = _css[_i:_css.find("}", _i)] if _i >= 0 else ""
ok("生效的那条 `.side .nav a` 规则里真带上了 padding/margin/字号（不是躺在死字符串里）",
   "padding:12px 14px" in _seg and "margin:4px 0" in _seg and "font-size:14.5px" in _seg,
   _seg.strip()[:70])
ok("没有把 CSS 写成裸字符串字面量（历史坑：相邻字符串只是算一下扔掉 ⇒ 浏览器收不到）",
   '".nav a{' not in _html)
_seg_icon = _css[_css.find(".side .nav a svg{"):]
_seg_icon = _seg_icon[: _seg_icon.find("}")]
ok("图标 ≥18px（原来 16）", "width:18px" in _seg_icon and "height:18px" in _seg_icon, _seg_icon.strip())
ok("侧栏列宽 ≥252px（原来 216）", "grid-template-columns:252px 1fr" in _html)

print("── J. 收起态导航 + 长清单折叠 + 微信后台纪律（2026-09-14 用户三项反馈）──")
ok("收起态：栏更宽（≥84px）", ".side.tight{width:88px}" in _html)
ok("收起态：图标更大（22px）", ".side.tight .nav a svg{width:22px" in _html)
ok("收起态：行内边距更松（≥14px）", ".side.tight .nav a{justify-content:center;gap:0;padding:14px 0}" in _html)
ok("收起把手贴右缘（不是左侧）", ".side .nav-tg{position:absolute;right:2px" in _html)
ok("长清单折叠：有按钮条样式", ".fold-bar" in _html and ".fold-tg" in _html)
ok("长清单折叠：目标覆盖 ≥8 类", _html.count('["#') + _html.count('[".') >= 8, str(_html.count('["#')))
ok("长清单折叠：默认是收起态（按钮写着「展开全部」）", "b.textContent = '展开全部'" in _html)
ok("长清单折叠：列表重渲染后能自动补回按钮（MutationObserver）", "MutationObserver" in _html)

_cfg_src = src("agent/config.py")
ok("默认不摆弄微信窗口（ui.lock_window_pos=False）", '"lock_window_pos": False' in _cfg_src)
ok("默认不抢前台（ui.allow_foreground=False）", '"allow_foreground": False' in _cfg_src)
_ua = src("agent/ui_adapt.py")
_fg_body = _ua[_ua.index("def _force_geometry"):]
_fg_body = _fg_body[:_fg_body.index("\n\n\ndef ")]
# 去掉 docstring（里面为了说明历史坑**引用了** `ShowWindow(hwnd, 9)` 这句话本身）
_dq = _fg_body.find('"""')
_dq2 = _fg_body.find('"""', _dq + 3)
if _dq >= 0 and _dq2 > _dq:
    _fg_body = _fg_body[:_dq] + _fg_body[_dq2 + 3:]
ok("_force_geometry 里不再强行 SW_RESTORE（把最小化的微信弹出来）", "ShowWindow" not in _fg_body)
ok("_force_geometry 默认关 + 最小化时不动", 'lock_window_pos", False' in _ua and "IsIconic" in _fg_body)
ok("抢前台的三处都有 allow_foreground 门", _ua.count("_cfg_bool(\"allow_foreground\", False)") >= 3,
   str(_ua.count("_cfg_bool(\"allow_foreground\", False)")))
ok("两个开关都映射到界面（有 data-cfg）",
   'data-cfg="ui.lock_window_pos"' in _html and 'data-cfg="ui.allow_foreground"' in _html)

print("── K. 自家控制台窗口：全屏键（用户：「自创原生显示屏是没有全屏键的…需要一个全屏键」）──")
_lc = src("launcher-src/launcher.cs")
ok("顶栏有三个键（最小化 / 最大化还原 / 关闭）",
   'min.Text = "—"' in _lc and '_btnMax = maxb' in _lc and 'cls.Text = "✕"' in _lc)
ok("最大化走 ToggleMax（按钮与标题栏双击同一处）",
   "public void ToggleMax()" in _lc and "m.Msg == 0x00A3" in _lc)
ok("无边框窗自己处理 WM_GETMINMAXINFO + WM_NCCALCSIZE（否则会盖住任务栏）",
   "WM_GETMINMAXINFO" in _lc and "0x0024" in _lc and "0x0083" in _lc and "NCCALCSIZE_PARAMS" in _lc)
if os.path.exists(_exe):
    try:
        _wpo = subprocess.run([_exe, "--winprobe"], capture_output=True, cwd=ROOT).stdout.decode("utf-8", "replace")
        _mx = [l.strip() for l in _wpo.splitlines() if l.strip().startswith("max ")]
        # 探针行同时带 ASCII 标记（`eq_workarea=True` / `restored_state=Normal`）：
        # 被重定向时 .NET 可能按 OEM 代码页输出，判据锚 ASCII 才不会被编码坑到。
        ok("实测：最大化后窗口正好等于工作区（不吃任务栏）",
           any("eq_workarea=True" in l for l in _mx), _mx[0] if _mx else "no max line")
        ok("实测：再点一次能还原回 Normal",
           any("restored_state=Normal" in l for l in _mx), str(_mx[1:2]))
    except Exception as e:
        skip("K. 全屏键实测", str(e)[:40])

# 复原运行态（判据不该留下自己的痕迹）
try:
    if _old_url is None:
        rm_url_file()
    else:
        U.write_console_url(_old_url)
except Exception:
    pass

print("")
print("控制台开窗/窗口/导航判据：%d 通过 / %d 失败 / %d 跳过" % (PASS, FAIL, SKIP))
sys.exit(1 if FAIL else 0)
