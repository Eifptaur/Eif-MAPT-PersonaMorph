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
import ctypes
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 同目录的 `_srcmatch`
import _srcmatch as _sm                                          # noqa: E402  空白容忍的源码断言（V-R4-13 第三条）
# ⛔ V-R14-7 隔离：D2 段要"真锁的行为"（自己建锁再删）⇒ 跑前跑后对账看不见（净变化 0），
#   可那一刻产品的 `logs\browser_opened.lock` 确实在 ⇒ 真机器人正在开窗时会被判成"别人刚开过"。
import _iso14                                                     # noqa: E402
_iso14.console_lock()
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


def _live_ok(url):
    """这个地址的端口有人在听吗？（判据自己判，不借产品代码，免得把被测对象当尺子）"""
    try:
        from urllib.parse import urlparse
        import socket as _s
        p = urlparse(str(url or ""))
        with _s.create_connection((p.hostname or "127.0.0.1", int(p.port or 80)), 0.4):
            return True
    except Exception:
        return False


_urlf = U.console_url_path()
_old_url = None
try:
    if os.path.exists(_urlf):
        with open(_urlf, encoding="utf-8") as f:
            _old_url = f.read()
except Exception:
    _old_url = None

print("── A. 控制台地址：现成文件优先（token 由拥有者落盘）──")
# ⚠️ 2026-09-18：**判据不许碰产品的 `logs/console.url`** —— 老版本这一节直接写真实文件、
#    末尾只把"进来时读到的值"写回去（若那次读到的已经是脏值 ⇒ 脏值就此固化）；E 段还真起了一个
#    WebUI 在**随机空闲端口**上 ⇒ `start()` 把产品地址覆写成 `…:14675`（端口随判据结束就没了）
#    ⇒ 启动器照着它开窗就是 `ERR_CONNECTION_REFUSED`（作者现场撞上的就是这个）。
#    ⇒ 现在全程在**临时根目录**里读写，真实文件只读不写。
import shutil                                                        # noqa: E402
import tempfile                                                      # noqa: E402

_judge_tmp = tempfile.mkdtemp(prefix="console-url-judge-")
_real_read_url = U.read_console_url
_real_url_before = _real_read_url()                                   # 真实文件（只做"没被动过"的对照）
try:
    ok("隔离落盘成功", U.write_console_url("http://127.0.0.1:39999/?token=AAA", root=_judge_tmp) is True)
    ok("读回＝写入值（没有多余空白）",
       U.read_console_url(root=_judge_tmp) == "http://127.0.0.1:39999/?token=AAA",
       U.read_console_url(root=_judge_tmp))
    ok("没有 .tmp 残留（os.replace 是原子的）",
       not os.path.exists(U.console_url_path(root=_judge_tmp) + ".tmp"))
    # 让 `NU.console_url()` 读隔离文件（它内部 `from .util import read_console_url` 是调用时取属性）
    U.read_console_url = lambda root="": _real_read_url(root or _judge_tmp)
    try:
        # 新契约（2026-09-18 加）：文件里的**端口连不上** ⇒ 弃用、回落配置地址。
        # 旧行为会把这个死链原样交给启动器 ⇒ 开出一个 `ERR_CONNECTION_REFUSED` 的窗（实测 14675）。
        _u_dead = NU.console_url()
        ok("文件里的端口连不上 ⇒ 弃用死链、回落配置地址（不再拿它开窗）",
           "39999" not in _u_dead and "127.0.0.1" in _u_dead, _u_dead.split("?")[0])
        ok("锚点仍走 #（不改成 query，控制台按 #sec-xxx 跳节）",
           NU.console_url("sec-model").endswith("#sec-model"))
        # 反正：把隔离文件写成一个**活着**的地址 ⇒ 仍以文件优先
        if _real_url_before and _live_ok(_real_url_before):
            U.write_console_url(_real_url_before, root=_judge_tmp)
            ok("文件里的端口活着 ⇒ 仍以文件为准（不被配置覆盖）",
               NU.console_url() == _real_url_before, NU.console_url().split("?")[0])
        else:
            skip("A. 文件优先（活端口）", "本机此刻没有在听的控制台端口")
    finally:
        U.read_console_url = _real_read_url
finally:
    shutil.rmtree(_judge_tmp, ignore_errors=True)
    ok("判据没动过产品那份 logs/console.url", _real_read_url() == _real_url_before,
       _real_url_before.split("?")[0] if _real_url_before else "(本来就没有)")

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
# ⚠️ 2026-09-17 补：本段原来**没 mock `find_console_window`**，而它当时因为一个拼错的全局名
#   （`_WNDUMPROC`）永远返回 0 ⇒ 复用分支从没被执行过，本段照样全绿（假绿）。现在显式钉住它。
_real_popen, _real_ready, _real_url = NU.subprocess.Popen, NU.webview_ready, NU.console_url
_real_lock = U.take_console_lock
_real_findc, _real_raise = NU.find_console_window, NU.raise_without_stealing
_GW = int(ctypes.windll.kernel32.GetConsoleWindow() or 0)     # 自检进程自己的控制台窗（真窗口）


class _FakePopen(object):
    def __init__(self, cmd, **kw):
        calls.append(cmd)


calls = []
try:
    NU.subprocess.Popen = _FakePopen
    NU.console_url = lambda anchor="": "http://127.0.0.1:39998/?token=ZZZ"
    U.take_console_lock = lambda *a, **k: True
    NU.find_console_window = lambda: 0            # 本机没有开着的控制台 ⇒ 走"新开"分支

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
    NU._WAIT_WINDOW_TRIES = 1            # 判据里别真等 6 秒
    calls[:] = []
    _r3 = NU.open_console()
    ok("③ 锁被占**但一个窗口都没有** ⇒ 照开（锁不许吞掉「根本没有窗口」）",
       _r3.get("how") == "browser" and len(calls) == 1,
       "%s / 开窗 %d 次" % (_r3.get("how"), len(calls)))
    # ③b 锁被占 + **窗口真的在** ⇒ 复用、不新开（防双窗的硬判据仍然守住）
    class _U32(object):
        @staticmethod
        def IsWindow(h):
            return True
    _real_u, _real_find = NU._u, NU.find_console_window
    NU._u = lambda: _U32()
    NU.find_console_window = lambda: 4242
    calls[:] = []
    try:
        _r3b = NU.open_console()
    finally:
        NU._u, NU.find_console_window = _real_u, _real_find
    ok("③b 锁被占 + 窗口真在 ⇒ 复用那个窗口，一次都不新开",
       _r3b.get("how") == "reuse" and len(calls) == 0,
       "%s / 新开 %d 次" % (_r3b.get("how"), len(calls)))

    U.take_console_lock = lambda *a, **k: True
    NU.console_url = lambda anchor="": ""
    _r4 = NU.open_console()
    ok("④ 拿不到地址 ⇒ ok=False + 原因（不瞎开一个 401 的页）",
       _r4.get("ok") is False and bool(_r4.get("why")), str(_r4.get("why"))[:40])

    # ⑤ 已经有开着的控制台窗口 ⇒ **复用那个窗口**，一次都不新开
    #    （用户 2026-09-17 原话：重启几次就攒出 4 个控制台，互相抢）
    NU.console_url = lambda anchor="": "http://127.0.0.1:39998/?token=ZZZ"
    NU.webview_ready = lambda: {"exe": True, "dll": True, "runtime": True, "runtime_ver": "1.0",
                                "why": "", "ok": True}
    _raised = []
    NU.raise_without_stealing = lambda h=0: (_raised.append(int(h or 0)) or
                                             {"ok": True, "hwnd": int(h or 0), "why": "假抬起"})
    if _GW:
        NU.find_console_window = lambda: _GW
        calls[:] = []
        _r5 = NU.open_console(take_lock=False)
        ok("⑤ 已有开着的控制台 ⇒ how=reuse 且**一次都不新开**",
           _r5.get("how") == "reuse" and len(calls) == 0, "%s / 新开=%d" % (_r5.get("how"), len(calls)))
        ok("⑤ 复用报的是**那个**窗口句柄（不许抬错窗）", _raised == [_GW], str(_raised))
    else:
        skip("⑤ 复用已开的控制台", "本会话没有真窗口可用")

    # ⑥ 复用失败（抬起抛异常）⇒ 必须**退回新开**，不许"什么都没发生"
    def _boom(h=0):
        raise RuntimeError("假失败：抬不起来")

    NU.raise_without_stealing = _boom
    NU.find_console_window = lambda: (_GW or 4242)
    calls[:] = []
    _r6 = NU.open_console(take_lock=False)
    ok("⑥ 复用失败 ⇒ 退回新开窗口（不放空炮）",
       _r6.get("how") == "webview" and len(calls) == 1, "%s / 新开=%d" % (_r6.get("how"), len(calls)))
finally:
    NU.subprocess.Popen, NU.webview_ready = _real_popen, _real_ready
    NU.console_url = _real_url
    U.take_console_lock = _real_lock
    NU.find_console_window, NU.raise_without_stealing = _real_findc, _real_raise
    # 2026-09-18：这里原来 `rm_url_file()`（删产品那份地址文件）——删它对本节断言毫无用处，
    #   只会让后面的 E 段读不到文件、并把产品推到"只能靠配置兜底"的路上。改为不动它。

print("── D2. 真锁的行为（不 mock）：一次成功、期内再抢失败、过期可再抢 ──")
try:
    # 同上：本节只需一把干净的**锁**文件（下一行已显式删锁），不需要动地址文件。
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
    # ⛔ 2026-09-16 加（用户实测："第一次有窗口，我关掉之后第二次点连窗口都不弹了"）：
    #   写锁的那个进程已经退出 ⇒ 它不可能再开窗 ⇒ 锁必须作废。否则启动器判"机器人会开窗"、
    #   新机器人 `take_console_lock()` 又抢不到 ⇒ **两边都不开**，用户永远看不到窗口。
    with open(U.console_lock_path(), "w", encoding="utf-8") as f:
        f.write("%r -1" % U.time.time())        # 时间很新鲜，但 pid 是个绝不存在的值
    ok("写锁的进程已死 ⇒ 锁不算新鲜", U.console_lock_fresh() is False)
    ok("写锁的进程已死 ⇒ 可以抢到", U.take_console_lock() is True)
    # 再贴近一层：一个**确实已经退出**的进程写下的锁——用户踩的就是这个场景
    try:
        import subprocess as _sp
        import sys as _sys
        _p = _sp.Popen([_sys.executable, "-c", "pass"],
                       creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
        _p.wait()
        _dead = _p.pid
    except Exception:
        _dead = 0
    if _dead > 0:
        with open(U.console_lock_path(), "w", encoding="utf-8") as f:
            f.write("%r %d" % (U.time.time(), _dead))
        ok("真退出的进程写的锁 ⇒ 不算新鲜", U.console_lock_fresh() is False)
        ok("真退出的进程写的锁 ⇒ 可以抢到", U.take_console_lock() is True)
    with open(U.console_lock_path(), "w", encoding="utf-8") as f:
        f.write("%r %d" % (U.time.time(), os.getpid()))   # 时间新鲜 + 写锁的人还活着
    ok("写锁的进程还活着 ⇒ 锁仍然新鲜", U.console_lock_fresh() is True)
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
    _e_tmp = tempfile.mkdtemp(prefix="console-url-judge-e-")
    try:
        _w = W.WebUI(lambda: {"state": "judge"}, [])
        _w.console_url_root = _e_tmp          # ⭐ 地址落到隔离目录，**绝不碰产品那份**
        _port = _w.start()
        _file_url = U.read_console_url(root=_e_tmp)
        ok("webui.start() 之后地址已落盘（谁拥有 token 谁写）—— 落到隔离目录，不污染产品",
           _file_url.startswith("http://127.0.0.1:") and _file_url.endswith("token=judge-token-1234567890"),
           _file_url.replace("judge-token-1234567890", "***"))
        ok("E 段跑完产品那份 logs/console.url 一字未变", U.read_console_url() == _real_url_before,
           (U.read_console_url() or "(空)").split("?")[0])

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

        # ⛔ 2026-09-21 加（第九轮 **V-R9-33** / 第十轮 **V-R10-9**）：**每一条写入口的空口令必须 fail-closed**。
        #   审计实测：我第一版手写了 11 条"安全路由" ⇒ 实际有 71 条 POST + 一条 **`do_PUT`**
        #   （`PUT /api/config` 无口令 **200 且真落盘**）而判据 84/0 全绿 —— 那次"覆盖"是假的。
        #   ⇒ 现在**从产品源码里枚举**路由（`do_POST` + `do_PUT` 两段都扫），逐条打"不带口令"，
        #   断言 401/403（鉴权在分发之前就把请求拒了 ⇒ 不可能误改任何东西）。
        #   ⚠️ 判据起的是**自己的** WebUI 实例（parent 是桩，没有 shutdown/restart 那些回调）
        #      ⇒ 即便某条路由的鉴权真坏了，落到桩上也只会 500，不会把本进程弄死。
        def _code_post(u, body=b"", method="POST"):
            # ⚠️ 空体（Content-Length: 0）：带 JSON 体时服务端在鉴权处直接拒、**不回读 body**，
            #    有些路由上客户端会看到连接被重置 —— 那是"被拒"不是"放行"，但会让断言分不清。
            _rq = urllib.request.Request(u, data=body, method=method,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(_rq, timeout=5) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                return e.code
            except Exception as e:
                return "ERR:%s" % e

        import re as _re
        _uisrc = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
        _routes = []
        for _mname, _meth in (("def do_POST", "POST"), ("def do_PUT", "PUT"), ("def do_DELETE", "DELETE")):
            if _mname not in _uisrc:
                continue
            _seg = _uisrc[_uisrc.index(_mname):]
            _nxt = _seg.find("\n    def ", 10)
            if _nxt > 0:
                _seg = _seg[:_nxt]
            for _p in sorted(set(_re.findall(r'path == "(/api/[A-Za-z0-9_/\-]+)"', _seg))):
                _routes.append((_meth, _p))
        _open_w = []
        for _method, _rp in _routes:
            _c = _code_post("http://127.0.0.1:%d%s" % (_port, _rp), method=_method)
            if _c not in (401, 403):
                _open_w.append("%s %s⇒%s" % (_method, _rp, _c))
        ok("**枚举出来的每条写入口（POST/PUT/DELETE，%d 条）不带口令 ⇒ 401/403**"
           "（第九轮只测了手写的 11 条 POST、漏了 do_PUT ⇒ V-R10-9）" % len(_routes),
           bool(_routes) and not _open_w,
           "没有拒绝的（%d/%d 条）：%s" % (len(_open_w), len(_routes), "、".join(_open_w[:6])))
        _auth_c = _code_post("http://127.0.0.1:%d/api/verifiers?token=judge-token-1234567890" % _port)
        ok("阳性对照：带对口令的 POST 不被拦（不是「一律 401」）", _auth_c == 200, str(_auth_c))
        _wrong = _code_post("http://127.0.0.1:%d/api/verify?token=wrong-token-000000" % _port)
        ok("错口令也拒（401/403）", _wrong in (401, 403), str(_wrong))
        _src_ui = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
        _seg_post = _src_ui[_src_ui.index("def do_POST"):_src_ui.index("\n    def ", _src_ui.index("def do_POST") + 10)]
        ok("do_POST 里有统一鉴权（`_auth_ok` 出现在 POST 段）", "_auth_ok" in _seg_post)
        _seg_put = _src_ui[_src_ui.index("def do_PUT"):_src_ui.index("\n    def ", _src_ui.index("def do_PUT") + 10)]
        ok("do_PUT 里有统一鉴权（第十轮点名的第二条写入口）", "_auth_ok" in _seg_put)
        # ⛔ 2026-09-21 加（第十轮 **V-R10-10**）：`save_config` 必须有**可注入根**。
        #   判据一旦照 V-R9-33 的建议扩到 `/api/config` 的落盘，就会把**用户真的 config.json**
        #   覆写掉（B 线实测撞上两次：那个副本 25991 字节 → 164 字节，事后从真仓库只读拷回）。
        from agent import config as _C10
        _iso_dir = tempfile.mkdtemp(prefix="pm-iso-cfg-")
        _iso = os.path.join(_iso_dir, "config.json")
        _C10.save_config({"probe": 1}, path=_iso)
        ok("`save_config(path=…)` 真写到指定文件（判据不必再拿产品配置当靶子）",
           json.load(open(_iso, encoding="utf-8")).get("probe") == 1)
        ok("不传 `path` 时默认仍是产品 `CONFIG_FILE`（产品调用点零改动）",
           _sm.has(open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read(),
                   "p = str(path or CONFIG_FILE)"))
        shutil.rmtree(_iso_dir, ignore_errors=True)
        # ⛔ 第十轮 **V-R10-34**：请求体上限 —— 原来按 `Content-Length` **全收**（64MB 全读），
        #   而且"声明 200MB、只发 1MB"会把线程**卡死**。现在超限直接 413（**在读体之前**判）。
        #   用裸 socket 发一个"声明天量、体不发"的请求：带对口令 ⇒ 进到读体那一步 ⇒ 应立刻 413。
        try:
            import socket as _sk
            _s2 = _sk.create_connection(("127.0.0.1", _port), timeout=5)
            _s2.sendall(("POST /api/verifiers?token=judge-token-1234567890 HTTP/1.1\r\n"
                         "Host: 127.0.0.1:%d\r\nContent-Length: 9000000\r\n"
                         "Connection: close\r\n\r\n" % _port).encode("ascii"))
            _resp = _s2.recv(200).decode("latin1", "replace")
            _s2.close()
            ok("请求体超上限 ⇒ **413**（不再全收，也不会守着读不满的体卡死）", "413" in _resp,
               _resp.splitlines()[0] if _resp else "(空)")
        except Exception as _e413:
            ok("请求体超上限 ⇒ 413", False, str(_e413)[:80])
        # ⛔ 2026-09-22 加（第十四轮 **V-R14-6** · P3）：**负的 `Content-Length` 也要拒** ——
        #   第十一轮（V-R11-10）修的就是这条，而它从修好到今天**一条判据都没有**（审计连点四轮）。
        #   为什么要判：`self.rfile.read(-1)` 的语义是**读到底**，老写法只挡"太大"⇒ 上限形同虚设。
        #   可达性诚实说明：`do_POST` 第一句就是口令校验 ⇒ 只有本机控制台能走到这一步，
        #   所以这条是"口径闭合"，不是"远程可利用"。
        try:
            import socket as _sk2
            _s3 = _sk2.create_connection(("127.0.0.1", _port), timeout=5)
            _s3.sendall(("POST /api/verifiers?token=judge-token-1234567890 HTTP/1.1\r\n"
                         "Host: 127.0.0.1:%d\r\nContent-Length: -1\r\n"
                         "Connection: close\r\n\r\n" % _port).encode("ascii"))
            _resp3 = _s3.recv(200).decode("latin1", "replace")
            _s3.close()
            ok("负 `Content-Length` ⇒ **413**（`read(-1)`＝读到底，上限会被绕过）",
               "413" in _resp3, _resp3.splitlines()[0] if _resp3 else "(空)")
        except Exception as _e413b:
            ok("负 `Content-Length` ⇒ 413", False, str(_e413b)[:80])
        try:
            import socket as _sk3
            _s4 = _sk3.create_connection(("127.0.0.1", _port), timeout=5)
            # ⚠️ 阳性对照要打**不存在的路由**：路由分派发生在读完体之后，未知路径回 404 且不碰任何
            #   真实子系统的桩（拿 `/api/verifiers` 当靶子会让桩里的 `catalog()` 抛异常、
            #   服务端打一串 traceback ⇒ runner 的"有 traceback 即判红"会把这条**好判据**判成假红）。
            _s4.sendall(("POST /api/__judge_probe_none__?token=judge-token-1234567890 HTTP/1.1\r\n"
                         "Host: 127.0.0.1:%d\r\nContent-Length: 2\r\n"
                         "Connection: close\r\n\r\n{}" % _port).encode("ascii"))
            _resp4 = b""
            try:
                while True:
                    _chunk = _s4.recv(4096)
                    if not _chunk:
                        break
                    _resp4 += _chunk
                    if len(_resp4) > 8192:
                        break
            except Exception:
                pass
            _s4.close()
            _resp4s = _resp4.decode("latin1", "replace")
            ok("阳性对照：正常长度**不**被 413 误伤（未知路由照常回 404）",
               bool(_resp4s) and "413" not in _resp4s and "404" in _resp4s,
               _resp4s.splitlines()[0] if _resp4s else "(空)")
        except Exception as _e413c:
            ok("阳性对照：正常长度不被 413 误伤", False, str(_e413c)[:80])
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
    # ⛔ 老版本这里是 `rm_url_file()` —— 判据跑完**把产品的 logs/console.url 删了**
    #    （下一次启动器只能靠配置兜底；万一配置里没口令就又回到 401 那条老路）。现在只清隔离目录。
    shutil.rmtree(_e_tmp, ignore_errors=True)

print("── E2. 端口被占而顺延时，落盘的地址必须是**真实端口**（第十五轮 V-R15-3）──")
try:
    import socket as _skp
    from agent import webui as _W2
    _hold = _skp.socket()
    _hold.bind(("127.0.0.1", 0))
    _hold.listen(1)
    _occ = int(_hold.getsockname()[1])          # 故意把"配置端口"占住 ⇒ 逼 webui 走顺延
    _iso2 = tempfile.mkdtemp(prefix="pm-url-judge-")
    _orig_get2 = _W2.get_config
    _base2 = dict(_orig_get2() or {})
    _base2["server"] = {"enabled": True, "host": "127.0.0.1", "port": _occ,
                        "token": "judge-token-1234567890", "auto_open_browser": False}
    _W2.get_config = lambda: _base2
    _w2 = None
    try:
        _w2 = _W2.WebUI(lambda: {}, [])
        _w2.console_url_root = _iso2          # 判据绝不碰产品那份 logs/console.url
        _p2 = int(_w2.start())
        _u2 = U.read_console_url(root=_iso2)
        ok("E2a 配置端口被占 ⇒ 真的顺延到别的端口（实际 ≠ 配置）", _p2 != _occ,
           "配置=%d 实际=%d" % (_occ, _p2))
        # ⛔ 反例锚：老写法 `write_console_url("…:%d…" % port)`（用**配置端口**）⇒ 这条必红，
        #   而启动器照那份地址开窗就是一屏 ERR_CONNECTION_REFUSED（＝网友说的"打不开控制台"）。
        ok("E2b 落盘地址用的是**真实端口**（老写法写配置端口 ⇒ 必红）",
           (":%d/" % _p2) in _u2 and (":%d/" % _occ) not in _u2,
           (_u2 or "").replace("token=", "token=***"))
    finally:
        try:
            if _w2 is not None:
                _w2.stop()
        except Exception:
            pass
        _W2.get_config = _orig_get2
        try:
            _hold.close()
        except Exception:
            pass
        shutil.rmtree(_iso2, ignore_errors=True)
except Exception as _e2b:
    skip("E2 端口顺延一致性", "起不了控制台：%s" % _e2b)

print("── F. 接线：onestart / persona_morph 不再各开一处 ──")
_on = src("scripts/onestart.py")
_wx = src("scripts/persona_morph.py")
ok("persona_morph 走唯一的 open_console", _sm.has(_wx, "from agent.notify_ui import open_console"))
ok("persona_morph 里没有自己写的浏览器锁（[browser-lock] 已撤）", "[browser-lock]" not in _wx)
ok("persona_morph 不再自己 cmd start 开浏览器", not _sm.has(_wx, '"cmd", "/c", "start"'))
ok("persona_morph 仍尊重 auto_open_browser 开关", _sm.has(_wx, 'auto_open_browser", True'))
ok("控制台地址只由 webui 落盘（agent/webui.py 里有 write_console_url）", "write_console_url" in src("agent/webui.py"))
ok("onestart 侧开窗也走同一实现", "notify_ui" in _on or "open_console" in _on)

print("── G. 一键关闭：关得掉（markers 含现行入口 + 关后回读端口）──")
_cl = src("launcher-src/close.cs")
ok("close.cs markers 含 persona_morph.py（现行入口）", "persona_morph.py" in _cl)
ok("close.cs 有端口占用兜底 + 关后回读", "PortOwner(" in _cl and _sm.has(_cl, "仍被 pid"))
ok("close_all.ps1 也含 persona_morph.py", "persona_morph.py" in src("scripts/close_all.ps1"))

print("── H. 自家控制台窗口：尺寸按 DPI/工作区算 + 能拉边缩放 ──")
_exe = os.path.join(ROOT, "一键启动.exe")
# ⛔ V-R7-4：`一键启动.exe --winprobe` 会把窗口几何**落盘**到 exe 同级的 `data/console_window.txt`
#   （`launcher.cs:1177 GeoFile()` ＝ `Application.ExecutablePath` 所在目录 + `data/`）⇒ 判据每跑
#   一次就改掉用户存好的控制台位置与大小。修法＝把 exe 与它启动要用的 DLL **复制到临时目录再跑**，
#   产物落在副本里；exe 与产品行为一字未改（探针输出、下面所有断言完全不变）。
_exe_dir = ""
if os.path.exists(_exe):
    _exe_dir = tempfile.mkdtemp(prefix="pm-exe-judge-")
    shutil.copy(_exe, _exe_dir)
    if os.path.exists(os.path.join(ROOT, "WebView2Loader.dll")):
        shutil.copy(os.path.join(ROOT, "WebView2Loader.dll"), _exe_dir)
    if os.path.isdir(os.path.join(ROOT, "lib")):
        shutil.copytree(os.path.join(ROOT, "lib"), os.path.join(_exe_dir, "lib"))
    _exe = os.path.join(_exe_dir, "一键启动.exe")
if os.path.exists(_exe):
    _out = subprocess.run([_exe, "--winprobe"], capture_output=True, cwd=ROOT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.decode("utf-8", "replace")
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
        _wv = [l for l in _lines if _sm.has(l, "ctrl WebView2")]
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
       _sm.has(src("launcher-src/launcher.cs"), "m.Msg == 0x0084") and
       _sm.has(src("launcher-src/stylekit.cs"), "f.Resize += delegate { Reclip(f); }"))
else:
    skip("H. 自家控制台窗口探针", "没编译出 一键启动.exe")

print("── I. 左导航间距（用户：靠得太近，放大 + 拉开）──")
_html = src("agent/console_html.py")
from agent import console_html as _CH          # noqa: E402
_css = _CH.HTML.split("<style>", 1)[1].split("</style>", 1)[0]
_i = _css.rfind(".side .nav a{")                # 生效的是最后一条同选择器规则
_seg = _css[_i:_css.find("}", _i)] if _i >= 0 else ""
ok("生效的那条 `.side .nav a` 规则里真带上了 padding/margin/字号（不是躺在死字符串里）",
   _sm.has(_seg, "padding:12px 14px") and _sm.has(_seg, "margin:4px 0") and "font-size:14.5px" in _seg,
   _seg.strip()[:70])
ok("没有把 CSS 写成裸字符串字面量（历史坑：相邻字符串只是算一下扔掉 ⇒ 浏览器收不到）",
   not _sm.has(_html, '".nav a{'))
_seg_icon = _css[_css.find(".side .nav a svg{"):]
_seg_icon = _seg_icon[: _seg_icon.find("}")]
ok("图标 ≥18px（原来 16）", "width:18px" in _seg_icon and "height:18px" in _seg_icon, _seg_icon.strip())
ok("侧栏列宽 ≥252px（原来 216）", _sm.has(_html, "grid-template-columns:252px 1fr"))

print("── J. 收起态导航 + 长清单折叠 + 微信后台纪律（2026-09-14 用户三项反馈）──")
# 2026-09-15 口径变更（用户当面点第 ⑤⑥ 条）：收起按钮从小把手改成**整行**「‹ 收起 / › 展开」、
# 栅格列宽跟着收（.shell.tight 64px）⇒ 旧的 88px 与 absolute right:2px 断言已作废。
ok("收起态：栅格列跟着收（.shell.tight 64px）", _sm.has(_html, ".shell.tight{grid-template-columns:64px 1fr;gap:8px}"))
ok("收起态：图标更大（22px）", _sm.has(_html, ".side.tight .nav a svg{width:22px"))
ok("收起态：行内边距更松（≥14px）", _sm.has(_html, ".side.tight .nav a{justify-content:center;gap:0;padding:14px 0}"))
ok("收起按钮是整行（不是贴右缘的小把手）", _sm.has(_html, ".side .nav-tg{position:static;width:100%"))
ok("长清单折叠：有按钮条样式", ".fold-bar" in _html and ".fold-tg" in _html)
ok("长清单折叠：目标覆盖 ≥8 类", _html.count('["#') + _html.count('[".') >= 8, str(_html.count('["#')))
ok("长清单折叠：默认是收起态（按钮写着「展开全部」）", _sm.has(_html, "b.textContent = '展开全部'"))
ok("长清单折叠：列表重渲染后能自动补回按钮（MutationObserver）", "MutationObserver" in _html)

_cfg_src = src("agent/config.py")
# 2026-09-16 改口径（已知现象：「你把限位设成默认吧，因为用户在后台都不在意这个，而且也能防止点错」）：
# 「限位」默认**开**；关掉时才"绝不动窗口"——两处读取点的 fail-closed 语义必须还在。
ok("默认开「限位」（ui.lock_window_pos=True）", _sm.has(_cfg_src, '"lock_window_pos": True'))
ok("默认不抢前台（ui.allow_foreground=False）", _sm.has(_cfg_src, '"allow_foreground": False'))
_ua = src("agent/ui_adapt.py")
_fg_body = _ua[_ua.index("def _force_geometry"):]
_fg_body = _fg_body[:_fg_body.index("\n\n\ndef ")]
# 去掉 docstring（里面为了说明历史坑**引用了** `ShowWindow(hwnd, 9)` 这句话本身）
_dq = _fg_body.find('"""')
_dq2 = _fg_body.find('"""', _dq + 3)
if _dq >= 0 and _dq2 > _dq:
    _fg_body = _fg_body[:_dq] + _fg_body[_dq2 + 3:]
ok("_force_geometry 里不再强行 SW_RESTORE（把最小化的微信弹出来）", "ShowWindow" not in _fg_body)
ok("_force_geometry 只在开关为真时才动（缺省按「不动」办）+ 最小化时不动",
   _sm.has(_ua, 'lock_window_pos", False') and "IsIconic" in _fg_body)
ok("抢前台的三处都有 allow_foreground 门", _ua.count("_cfg_bool(\"allow_foreground\", False)") >= 3,
   str(_ua.count("_cfg_bool(\"allow_foreground\", False)")))
ok("两个开关都映射到界面（有 data-cfg）",
   'data-cfg="ui.lock_window_pos"' in _html and 'data-cfg="ui.allow_foreground"' in _html)

print("── K. 自家控制台窗口：全屏键（用户：「自创原生显示屏是没有全屏键的…需要一个全屏键」）──")
_lc = src("launcher-src/launcher.cs")
# 2026-09-15 口径变更：顶栏三键从「— / □❐ / ✕ 三种异族字形」改成同一个自绘控件 GlyphButton
# （launcher-src/wingliphs.cs 的 GlyphKind{Min,Max,Restore,Close}，同一支笔按高度等比）。
ok("顶栏三个键是同一套自绘字形（GlyphButton + GlyphKind）",
   "GlyphButton" in _lc and "GlyphKind" in _lc and _sm.has(_lc, "_btnMax = maxb"))
ok("最大化走 ToggleMax（按钮与标题栏双击同一处）",
   _sm.has(_lc, "public void ToggleMax()") and _sm.has(_lc, "m.Msg == 0x00A3"))
ok("无边框窗自己处理 WM_GETMINMAXINFO + WM_NCCALCSIZE（否则会盖住任务栏）",
   "WM_GETMINMAXINFO" in _lc and "0x0024" in _lc and "0x0083" in _lc and "NCCALCSIZE_PARAMS" in _lc)
if os.path.exists(_exe):
    try:
        _wpo = subprocess.run([_exe, "--winprobe"], capture_output=True, cwd=ROOT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.decode("utf-8", "replace")
        _mx = [l.strip() for l in _wpo.splitlines() if l.strip().startswith("max ")]
        # 探针行同时带 ASCII 标记（`eq_workarea=True` / `restored_state=Normal`）：
        # 被重定向时 .NET 可能按 OEM 代码页输出，自检锚 ASCII 才不会被编码坑到。
        ok("实测：最大化后窗口正好等于工作区（不吃任务栏）",
           any("eq_workarea=True" in l for l in _mx), _mx[0] if _mx else "no max line")
        ok("实测：再点一次能还原回 Normal",
           any("restored_state=Normal" in l for l in _mx), str(_mx[1:2]))
    except Exception as e:
        skip("K. 全屏键实测", str(e)[:40])

# 复原运行态（自检不该留下自己的痕迹）
# 2026-09-18：本判据现在**全程在隔离目录里读写地址文件**，产品那份从头到尾没碰过
#   ⇒ 这里不再"写回 _old_url"（老写法若进来时读到的已经是脏值，就等于把脏值钉死）。
try:
    _now_url = U.read_console_url()
    ok("收尾：产品那份 logs/console.url 与判据启动时一致（全程零污染）",
       _now_url == _real_url_before, (_now_url or "(空)").split("?")[0])
except Exception as e:
    skip("收尾零污染检查", str(e)[:40])

print("")
print("── L. 「重启后页面自己连回来」（2026-09-18 用户实测后加）──")
# 用户原话：「每次都是这样，现在这个窗口就是显示被停止了，但是既没有关掉，也不弹出一个新的」
# 真因：控制台**没有任何周期性状态轮询**，也没有"重启后重连"的判断 —— 而页面文案早就写着
#      「正在重启，页面稍后会自己连回来」⇒ **承诺没有实现**。这三条钉住它别再退化。
_ch = src("agent/console_html.py")
ok("L1 有周期性状态轮询（外部重启/改状态后页面会自己更新）",
   _sm.has(_ch, "__statusPoll = setInterval"), "")
ok("L2 服务器换进程（started_at 变）⇒ 页面自己重载",
   _sm.has(_ch, "window.__srvStartedAt !== s.started_at") and "location.reload()" in _ch)
ok("L3 连续取不到状态 ⇒ 也重载一次（带次数上限，防风暴）",
   _sm.has(_ch, "window.__pollFails >= 3") and "__reloads" in _ch)
ok("L4 文案与机制一致（页面确实写着「稍后会自己连回来」，而机制真的存在）",
   "页面稍后会自己连回来" in _ch and "location.reload()" in _ch)
ok("L5 轮询只启动一次（不重复叠加）", "if(!window.__statusPoll)" in _ch)

print("")
print("── M. 死链不许被启动器取用 + 派生文件必须被 runner 还原（第十五轮 V-R15-3）──")
# ⛔ 为什么：`logs/console.url` 是"上一次跑控制台"写下的地址，机器人停了它还在。老代码（C# 侧
#   `Ui.ConsoleUrl`）只判"以 http 开头"就照它开窗 ⇒ **一屏 ERR_CONNECTION_REFUSED**
#   ＝ 网友报的「打不开控制台」。这是那条 bug 的行为级守备：探针 `--urlprobe` 不许取出死链。
if os.path.exists(_exe) and _exe_dir:
    try:
        import json as _json2
        import socket as _sk3
        # 一个**没人听**的端口（绑上再关掉 ⇒ 大概率仍空闲且无人应答）
        _sD = _sk3.socket()
        _sD.bind(("127.0.0.1", 0))
        _dead_p = int(_sD.getsockname()[1])
        _sD.close()
        # 一个**真有人听**的端口（阳性对照）
        _sL = _sk3.socket()
        _sL.bind(("127.0.0.1", 0))
        _sL.listen(1)
        _live_p = int(_sL.getsockname()[1])
        os.makedirs(os.path.join(_exe_dir, "logs"), exist_ok=True)
        _cf2 = os.path.join(_exe_dir, "config.json")
        # 夹具设计：**地址文件指向死端口、配置指向活端口** —— 这样才能验证"文件里的死链被拒、
        # 而不是被别的原因（配置也死了）一起算进去"。
        with open(_cf2, "w", encoding="utf-8") as _f2:
            _json2.dump({"server": {"port": _live_p, "token": "judge-token-abcdef"}}, _f2)
        _uf2 = os.path.join(_exe_dir, "logs", "console.url")

        def _probe():
            o = subprocess.run([_exe, "--urlprobe"], capture_output=True, cwd=ROOT,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            t = (o.stdout or b"").decode("utf-8", "replace") + (o.stderr or b"").decode("utf-8", "replace")
            f = {}
            for ln in t.splitlines():
                if "=" in ln:
                    k, v = ln.split("=", 1)
                    f[k.strip()] = v.strip()
            return f

        with open(_uf2, "w", encoding="utf-8") as _f3:
            _f3.write("http://127.0.0.1:%d/?token=deadbeef" % _dead_p)
        _pA = _probe()
        ok("M1 死链（端口没人应答）**不许**被 `--urlprobe` 取用（老写法只判 http ⇒ 必红）",
           (":%d/" % _dead_p) not in str(_pA.get("url") or ""),
           "url=%s kind=%s" % (_pA.get("url"), _pA.get("fallback_kind")))
        # ⚠️ 用 **ASCII 标记**断言，不用中文（`--urlprobe` 的输出经 OEM 代码页重定向会变乱码 ——
        #   与 `--winprobe` 的 `eq_workarea=True` 同一套教训）。
        ok("M2 拒绝时**说得出原因**（`fallback_kind=dead_link`，现场不用猜）",
           str(_pA.get("fallback_kind") or "") == "dead_link", str(_pA.get("fallback_kind")))
        with open(_uf2, "w", encoding="utf-8") as _f4:
            _f4.write("http://127.0.0.1:%d/?token=livebeef" % _live_p)
        _pB = _probe()
        ok("M3 阳性对照：活着的那条地址照常取用（不是「一律拒绝」）",
           (":%d/" % _live_p) in str(_pB.get("url") or "") and str(_pB.get("fallback_kind") or "") == "",
           "url=%s kind=%s" % (_pB.get("url"), _pB.get("fallback_kind")))
        _sL.close()
    except Exception as _eM:
        skip("M. 死链守备实测", str(_eM)[:60])
else:
    skip("M. 死链守备实测", "没有 一键启动.exe（先编译）")
_RA = src("scripts/run_all_selftests.py")
ok("M4 派生文件（console.url / 开窗锁）跑完**一律还原**（判红归判红，伤害不许留到下一次）",
   "_DERIVED" in _RA and "_restore_derived" in _RA and "logs/console.url" in _RA)
ok("M5 还原动作**有可见回执**（不许悄悄做）", "派生文件已还原" in _RA)

print("控制台开窗/窗口/导航判据：%d 通过 / %d 失败 / %d 跳过" % (PASS, FAIL, SKIP))
if _exe_dir:
    shutil.rmtree(_exe_dir, ignore_errors=True)      # V-R7-4：判据自带的 exe 副本不留痕迹
sys.exit(1 if FAIL else 0)
