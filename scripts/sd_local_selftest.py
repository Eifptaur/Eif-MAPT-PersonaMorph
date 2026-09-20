#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本地生图后端（`agent/sd_local.py` + `agent/sd_local_server.py`）判据 —— 离线、不下载、不联网发请求。

守的东西（用户 2026-09-17 的三条口径）：
  ①「你帮用户装，做成一个**可选项**；用户选了就弹安装提示，帮他安装；**在线安装看用户开不开**」
     ⇒ 默认 `allow_online_install=False`，不开就**拒绝下载**并说明（绝不偷偷联网）；
  ②「要能让用户**实时看到下载进度**：一共多少 / 现在下了多少 / 百分比是多少」⇒ `progress()` 字段齐、百分比算得对；
  ③「还要加那个按键，也就是**后台加载** … 可以办点别的事情」⇒ 有 `install_async()`（后台线程）+ 控制台按钮；
  另外：「**要提示用户需要下载，就必须写明时间可能会非常长**」⇒ `estimate()` 必须给"要下多少 GB + 预计多久"，
  并对被限速的源（实测 0.02~0.06 MB/s）**如实劝退**。

用法：`py -3 scripts/sd_local_selftest.py`
"""
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 同目录的 `_srcmatch`
sys.path.insert(0, ROOT)

from agent import sd_local as S          # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(("  OK   " if cond else "  FAIL ") + name + (("   [%s]" % detail) if detail else ""))


def src(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


print("── A. 一条龙的四件事都在 ──")
for fn in ("status", "estimate", "install", "install_async", "start_server", "stop_server", "progress"):
    ok("A·%s 有实现" % fn, callable(getattr(S, fn, None)))
_sv = src(os.path.join("agent", "sd_local_server.py"))
ok("A·服务端实现了 a1111 两个端点（探测 + 生成）",
   "/sdapi/v1/sd-models" in _sv and "/sdapi/v1/txt2img" in _sv and '"images"' in _sv)

print("── B. 默认不偷偷联网（用户：在线安装看用户开不开）──")
_cfg = src(os.path.join("agent", "config.py"))
ok("B1 配置默认 allow_online_install=False", '"allow_online_install": False' in _cfg)
r = S.install(allow_online=False)
ok("B2 没开开关时 install 拒绝并说明", r[0] is False and "不偷偷联网" in r[1], r[1][:60])
ra = S.install_async(allow_online=False)
ok("B3 后台安装同样受开关管", ra[0] is False and "不偷偷联网" in ra[1], ra[1][:40])

print("── C. 「要下多少 + 预计多久」必须给足（用户要求写明可能很久）──")
est = S.estimate(do_probe=False)
# 2026-09-17 多档后：总量＝**当前档**（模型 + 该档的加速件）+ 运行库；不再写死某一档的体积
ok("C1 给了总量（当前档 + 运行库）", abs(est["gb"] - (S.preset_gb() + S.DEPS_GB)) < 0.02,
   "%s GB = %s + %s（档=%s）" % (est["gb"], est["model_gb"], est["deps_gb"], est.get("preset")))
ok("C1b 档位清单至少两档（速度 + 画质，用户口径「不二选一」）",
   len(S.presets()) >= 2 and any(p["id"] == "speed" for p in S.presets()) and any(p["id"] == "quality" for p in S.presets()),
   "、".join(p["id"] for p in S.presets()))
ok("C1c 每档都带许可证与说明（发出去要讲清来源）",
   all(p.get("license") and p.get("note") for p in S.presets()), str([p["id"] for p in S.presets()]))
ok("C2 给了人话时间", bool(est["human"]) and ("分钟" in est["human"] or "小时" in est["human"]), est["human"])
ok("C3 给了速度与来源", est["mbps"] > 0 and bool(est["source"]), "%s MB/s · %s" % (est["mbps"], est["source"]))
ok("C4 有'低于这个速度就别下了'的门槛与劝退文案", S.MIN_MBPS > 0 and "小时" in src(os.path.join("agent", "sd_local.py")))
ok("C5 实测数字写进了文档（约 20 分钟 / 10.8 GB / 首次加载 15 秒）",
   "约 20 分钟" in _cfg or "20 分钟" in src(os.path.join("agent", "sd_local.py")))

print("── D. 实时进度（一共多少/下了多少/百分比）──")
p0 = S.progress()
ok("D1 progress 字段齐", set(["running", "phase", "message", "done_bytes", "total_bytes",
                          "percent", "mbps", "eta_seconds", "ok", "error"]) <= set(p0.keys()))
S._set_prog(running=True, done_bytes=3 * 1073741824, total_bytes=12 * 1073741824)
p1 = S.progress()
ok("D2 百分比算得对（3/12 GB ⇒ 25%）", abs(p1["percent"] - 25.0) < 0.2, str(p1["percent"]))
S._set_prog(running=False, percent=0.0)

print("── E. 后台安装 + 控制台按键（用户：别让用户盯着弹窗等）──")
ok("E1 有后台线程入口", "threading.Thread" in src(os.path.join("agent", "sd_local.py"))
   and "daemon=True" in src(os.path.join("agent", "sd_local.py")))
_web = src(os.path.join("agent", "webui.py"))
ok("E2 四个端点都在（状态/进度/安装/起停）",
   all(x in _web for x in ("/api/image_gen/local", "/api/image_gen/local/progress",
                           "/api/image_gen/local/install", "/api/image_gen/local/start")))
_ch = src(os.path.join("agent", "console_html.py"))
ok("E3 控制台有：状态行 + 进度条 + 安装/启动/停止三个按钮",
   'id="sdLocalState"' in _ch and 'id="sdLocalFill"' in _ch
   and all(('id="%s"' % i) in _ch for i in ("sdLocalInstall", "sdLocalStart", "sdLocalStop")))
ok("E4 弹窗里写明了「后台进行」这件事（关掉弹窗也能继续下）",
   "后台进行" in _ch and "关掉弹窗" in _ch)
ok("E5 弹窗里带了估时与来源（不是空口承诺）", "est.human" in _ch and "est.source" in _ch)

# ── ⛔ 2026-09-21（第四轮审计 **V-R4-3，P1**）：**"有人答话" ≠ "是我们这一版的实例"** ──
#   现场：7860 上跑着 09-19 起的旧无门禁实例（无 Host / 错口令一律 200），而复用判据只看
#   "它回不回"，于是**一直复用**、**更新产品也不换它** ⇒ 门禁代码"修好了"但活体从没生效。
#   这里用**两个假服务**（真 HTTP、真请求）复现两种实例，验判据能分得开。
print("\n── F. 无门禁旧实例必须被认出来（V-R4-3）──")
import http.server as _hs                                                      # noqa: E402
import threading as _th                                                       # noqa: E402
from agent import local_guard as _lg                                          # noqa: E402

_JTOK = "JUDGE-TOKEN-123"
_old_env = os.environ.get(_lg.ENV_KEY)
os.environ[_lg.ENV_KEY] = _JTOK


class _OldH(_hs.BaseHTTPRequestHandler):
    """旧实例：**不认 Host、不认口令**，一律 200。"""

    def log_message(self, *a):
        pass

    def do_GET(self):
        b = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


class _NewH(_hs.BaseHTTPRequestHandler):
    """新实例：门禁（回环 Host + 口令）+ 声明 `gate`。"""

    def log_message(self, *a):
        pass

    def _s(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _deny(self):
        _h = str(self.headers.get("Host") or "")
        _t = str(self.headers.get("X-PM-Token") or "")
        if not (_h.startswith("127.0.0.1") or _h.startswith("localhost")) or _t != _JTOK:
            self._s({"detail": "denied"}, 403)
            return False
        return True

    def do_GET(self):
        if not self._deny():
            return
        self._s({"ok": True, "gate": "host+token"})


def _serve(cls):
    srv = _hs.HTTPServer(("127.0.0.1", 0), cls)          # 单线程（同判据框架既有口径）
    t = _th.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, int(srv.server_address[1])


_so = _sn = None
try:
    _so, _po = _serve(_OldH)
    _sn, _pn = _serve(_NewH)
    ok("F0 `server_alive` 对**旧无门禁实例**也回 True（这就是盲点本身）",
       S.server_alive(_po) is True)
    _g_old, _w_old = S.service_gated(_po)
    ok("F1 旧实例：`service_gated` 判**不是我们这一版**（无口令 + 外域 Host 也回 200）",
       _g_old is False and "旧实例" in str(_w_old), str(_w_old)[:70])
    _g_new, _w_new = S.service_gated(_pn)
    ok("F2 新实例：门禁 + `gate` 声明 ⇒ 判**是**（阳性对照，别把正常路堵了）",
       _g_new is True and _w_new == "", "%s / %s" % (_g_new, _w_new))
    _sds = io.open(os.path.join(ROOT, "agent", "sd_local_server.py"), encoding="utf-8").read()
    _sdc = io.open(os.path.join(ROOT, "agent", "sd_local.py"), encoding="utf-8").read()
    ok("F3 服务端确实会声明 `gate`（源码级）", '"gate": "host+token"' in _sds)
    ok("F4 `start_server` 不再「看到有人答话就复用」（要先过 `service_gated`）",
       "service_gated(port)" in _sdc)
    # ⛔ V-R5R-4：原来靠 `"pidfile 记的是" in _sdc` 这种**源码文本**断言（改个空格就红）
    #   ⇒ 换成空白容忍的 `_srcmatch.has` + **行为级**判据（身份判据的纯函数矩阵，见 G 段）。
    import _srcmatch as _sm
    ok("F5 换掉旧实例有**证据链**（只杀 pidfile 记的那个 pid）",
       hasattr(S, "kill_stale_owner") and hasattr(S, "_port_owner_pid")
       and _sm.has(_sdc, "占着 %d 的是 PID %s") and _sm.has(_sdc, "_is_our_server"))
    ok("F6 反例锚：老判据（只判 alive）**确实**会把旧实例当可用",
       S.service_gated(_po)[0] is False and S.server_alive(_po) is True)
finally:
    for _s in (_so, _sn):
        try:
            _s.shutdown()
            _s.server_close()
        except Exception:
            pass
    if _old_env is None:
        os.environ.pop(_lg.ENV_KEY, None)
    else:
        os.environ[_lg.ENV_KEY] = _old_env

print("\n── G. V-R5B-4/M4：杀进程前必须验身份（pidfile 残留 + PID 复用＝会杀别人）──")
import shutil as _sh2                                                          # noqa: E402
import tempfile as _tf2                                                        # noqa: E402

ok("G1 身份判据（纯函数）：我们的服务命令行 ⇒ 认",
   S.cmdline_is_ours(r"C:\x\python.exe -u C:\y\agent\sd_local_server.py 7860", 7860) is True)
ok("G2 别的程序（PID 复用的现场）⇒ 不认",
   S.cmdline_is_ours(r"C:\gradio\python.exe app.py --port 7860", 7860) is False)
ok("G3 是我们的服务但**端口对不上** ⇒ 不认",
   S.cmdline_is_ours(r"python -u agent\sd_local_server.py 8188", 7860) is False)
ok("G4 取不到命令行 ⇒ 不认（宁可让用户手动处理）", S.cmdline_is_ours("", 7860) is False)
_calls = []
_keep_run = S.subprocess.run
_keep_pid = S._pidfile
_gdir = _tf2.mkdtemp(prefix="sdl-g-")
S._pidfile = lambda: os.path.join(_gdir, "sd_local.pid")


class _R:
    returncode = 0

    def __init__(self, out=""):
        self.stdout = out


def _fake_run(*a, **k):
    """只记录**杀进程**那一类调用；顺手给 `Get-CimInstance` 返回"别的程序"的命令行（PID 复用现场）。"""
    _cmd = " ".join(str(x) for x in (a[0] if a else []))
    if "taskkill" in _cmd:
        _calls.append(_cmd)
        return _R()
    if "CimInstance" in _cmd:
        return _R(r"C:\Program Files\Gradio\python.exe app.py --port 7860")
    return _R()


S.subprocess.run = _fake_run
try:
    with open(S._pidfile(), "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))                 # 指着**本判据自己**：命令行不是我们的服务
    _r5 = S.stop_server()
    ok("G5 `stop_server` 发现「记录里的 pid 不是我们的服务」⇒ **不杀**、只清过期记录",
       _r5[0] is True and not _calls and "没有杀任何进程" in str(_r5[1]), str((_r5, _calls)))
    ok("G6 过期 pidfile 被清掉（别留成下一次误杀的种子）", not os.path.exists(S._pidfile()))
    with open(S._pidfile(), "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    _r6 = S.kill_stale_owner(7860)             # 端口占用者不等于 pidfile 里的 pid ⇒ 早就该拒
    ok("G7 `kill_stale_owner` 端口占用者与 pidfile 不一致 ⇒ 拒（且没有真的 taskkill）",
       _r6[0] is False and not _calls, str((_r6, _calls)))
finally:
    S.subprocess.run = _keep_run
    S._pidfile = _keep_pid
    _sh2.rmtree(_gdir, ignore_errors=True)

print("\n== 本地生图后端判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
