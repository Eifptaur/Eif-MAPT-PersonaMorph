# -*- coding: utf-8 -*-
"""`pmctl.py`（不依赖图形界面的操作入口）判据。

**为什么必须有**：这个 CLI 是"低配机器上唯一能用的操作入口"，它一旦坏，
用户在那种机器上就**什么都没有**了 ⇒ 必须机器验，不靠"我看了一眼"。

验四件：
  A. **端到端**：起一个本地假服务端（stdlib http.server，返回罐头 JSON），让 CLI 真跑 ——
     `status/adapt/verify/check/sessions/logs/cfg/lowspec/doctor` 全过一遍，检查输出与请求体；
  B. **地址发现**：`--url` 生效；**拿不到地址时明确报错**（不许静默当成功）；
  C. **只碰白名单**：`lowspec on` 的请求体**只含省资源白名单键**，且**安全开关一条都不许碰**
     （`wechat.background_only` / `input.allow_real_fallback` / `ui.allow_foreground`）；
  D. **不许整份覆盖**：`cfg set` 的请求体**只含那一个键**（外加 `_touched` 声明），
     免得 CLI 变成第二个"整份回写"的坑。

跑法：`runtime\\python\\python.exe scripts\\pmctl_selftest.py`
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
import _srcmatch as _sm # noqa: E402

PY = sys.executable
CLI = os.path.join(ROOT, "scripts", "pmctl.py")
PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


# ── 假服务端：记录每次请求，返回罐头数据 ────────────────────────────────
SEEN = []


class _H(BaseHTTPRequestHandler):
    # ⛔ 必须 HTTP/1.0 + 多线程：默认的 HTTP/1.1 keep-alive + 单线程 HTTPServer 会被
    #    前一个连接挂住 ⇒ 后面的请求全部超时（判据实测：F2 "连不上 … timed out"）
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):   # 静音
        pass

    def _send(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception: # noqa: BLE001
            return {}

    def do_GET(self):
        if os.environ.get("PMCTL_DEBUG"):
            sys.stderr.write("[srv] GET %s\n" % self.path)   # 调试用：看清 CLI 到底请求了什么路径
        SEEN.append(("GET", self.path, self.headers.get("Authorization", ""), None))
        p = self.path.split("?")[0]
        if p == "/api/status":
            self._send({"version": "2026.9.28.6", "running": True, "paused": False,
                        "targets": ["家人群"], "model": "deepseek-v4", "wechat": True})
        elif p == "/api/verifiers":
            self._send({"verifiers": [{"id": "machine", "name": "本机适配"},
                                     {"id": "send_blocked", "name": "消息发不出去"}]})
        elif p == "/api/verify":
            self._send({"id": "machine", "ok": None, "report": "【检验器 · 本机适配】\n判决：只读档案"})
        elif p == "/api/config":
            self._send({"wechat": {"poll_interval": 3, "background_only": True},
                        "input": {"allow_real_fallback": False},
                        "ui": {"whale_cursor": True, "whale_anim": {"worm": 1.0, "plane": 1.0, "zap": 1.0},
                               "wave_fx": {"enabled": False}, "background": "",
                               "allow_foreground": False}})
        elif p == "/api/version":
            self._send({"version": "2026.9.28.6"})
        elif p == "/api/sessions":
            self._send({"sessions": [{"name": "家人群", "last": "刚刚"}, {"name": "工作群", "last": "1 小时前"}]})
        elif p == "/api/logs":
            self._send({"lines": ["line-%d" % i for i in range(1, 121)]})
        else:
            self._send({"ok": False, "error": "no such route"}, 404)

    def do_POST(self):
        b = self._body()
        SEEN.append(("POST", self.path, self.headers.get("Authorization", ""), b))
        p = self.path.split("?")[0]
        if p in ("/api/config", "/api/pause", "/api/selfcheck"):
            self._send({"ok": True, "summary": "体检通过（罐头）", "checks": [{"name": "罐头项", "ok": True, "detail": "x"}]})
        else:
            self._send({"ok": False, "error": "no such route"}, 404)


def _run(*args, timeout=90):
    # ⛔ 全局选项必须**放在子命令之前**（argparse 规矩）——放后面会直接 usage 报错（第一版踩过）
    r = subprocess.run([PY, "-X", "utf8", CLI, "--url", BASE, "--token", "T0K"] + list(args),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=0x08000000)
    return (r.stdout or "") + (r.stderr or ""), r.returncode


srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
PORT = srv.server_address[1]
BASE = "http://127.0.0.1:%d/" % PORT
threading.Thread(target=srv.serve_forever, daemon=True).start()
print("假服务端起在 %s\n" % BASE)


def _restart_server():
    """换一台**全新**的假服务端（返回新 BASE）。

    为什么需要：判据里跑到 F 段时已经过了十几条请求，复用同一台服务端会**连接被挂住**导致
    后续请求超时（实测：F2 报 "连不上 … timed out"、F4 直接卡到 90s 超时）。
    每段用干净的服务端，判据才是在验 CLI 而不是在验测试脚手架。
    """
    global srv, BASE, PORT
    try:
        srv.shutdown()
    except Exception as e:  # noqa: BLE001
        print("   （旧服务端关停异常，忽略：%s）" % str(e)[:60])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    PORT = srv.server_address[1]
    BASE = "http://127.0.0.1:%d/" % PORT
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return BASE

print("── A. 端到端（所有命令真跑一遍）──")
out, rc = _run("status")
ok("A1 `status` 打印版本/监听目标且不报错", rc == 0 and "2026.9.28.6" in out and "家人群" in out, out.splitlines()[:2])
ok("A2 `status` 请求带了 Bearer 令牌", any(h == "Bearer T0K" for _m, _p, h, _b in SEEN), "")
out, rc = _run("adapt")
ok("A3 `adapt` 打印「本机适配」档案", rc == 0 and "本机适配" in out, out.strip()[:40])
out, rc = _run("verify")
ok("A4 `verify` 跑全部检验器（两条都跑到）", rc == 0 and out.count("──") >= 2, "%d 段" % out.count("──"))
out, rc = _run("verify", "nope")
ok("A5 反向锚：不存在的检验器 id ⇒ 明确报错并列出可用的",
   rc != 0 and "没有这些检验器" in out and "machine" in out, "")
out, rc = _run("check")
ok("A6 `check` 打印体检摘要", rc == 0 and "体检通过（罐头）" in out, "")
out, rc = _run("sessions", "2")
ok("A7 `sessions 2` 只打印 2 条", rc == 0 and "家人群" in out and out.count("   ") >= 2, "")
out, rc = _run("logs", "5")
ok("A8 `logs 5` 只打印尾部 5 行", rc == 0 and "line-120" in out and "line-115" not in out, "")
out, rc = _run("cfg", "get", "wechat.poll_interval")
ok("A9 `cfg get` 读单键", rc == 0 and "3" in out, out.strip()[:40])
out, rc = _run("doctor")
ok("A10 `doctor` 输出**固定格式诊断块**（可直接整段粘贴给作者）",
   rc == 0 and _sm.has(out, "===== 群相诊断") and _sm.has(out, "===== 结束") and _sm.has(out, "版本:")
   and "体检:" in out and "本机适配:" in out, out.splitlines()[0][:40] if out else "")

print("\n── B. 地址发现 / 报错口径 ──")
r = subprocess.run([PY, "-X", "utf8", CLI, "--url", "http://127.0.0.1:1/", "status"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                   creationflags=0x08000000)   # 不许闪控制台窗（本仓硬规则）
o = (r.stdout or "") + (r.stderr or "")
ok("B1 端口没人听 ⇒ 明确报错并提示 --url（不静默当成功）",
   r.returncode != 0 and "连不上" in o and "--url" in o, o.strip()[:60])
ok("B2 地址发现优先级写在文档串里（--url > 环境变量 > logs/console.url > 配置）",
   _sm.has(io.open(CLI, encoding="utf-8").read(), "地址发现顺序"))

print("\n── C. lowspec 只碰白名单 ──")
SEEN.clear()
out, rc = _run("lowspec", "on")
cfg_posts = [b for m, p, _h, b in SEEN if m == "POST" and p.startswith("/api/config")]
sent = set()
for b in cfg_posts:
    def _walk(d, pre=""):
        for k, v in (d or {}).items():
            if k == "_touched":
                continue
            if isinstance(v, dict):
                _walk(v, ("%s.%s" % (pre, k)) if pre else k)
            else:
                sent.add(("%s.%s" % (pre, k)) if pre else k)
    _walk(b)
ok("C1 `lowspec on` 改了东西（请求非空）", bool(cfg_posts) and bool(sent), "、".join(sorted(sent))[:80])
SAFE_SWITCHES = {"wechat.background_only", "input.allow_real_fallback", "ui.allow_foreground"}
ok("C2 反向锚：安全开关一条都没碰（不许顺手改用户的安全设置）",
   not (sent & SAFE_SWITCHES), "碰到的：" + "、".join(sorted(sent & SAFE_SWITCHES)))
_real = set()
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("pmctl", CLI)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    _real = set(m.LOWSPEC)
except Exception as e: # noqa: BLE001
    print("   （导入 pmctl 失败：%s）" % str(e)[:60])
ok("C3 改动的键**都在 LOWSPEC 白名单里**（不许越界写别的键）", bool(sent) and sent <= _real,
   "越界：" + "、".join(sorted(sent - _real)) if sent - _real else "")
_exist = set()
try:
    from agent.config import DEFAULT_CONFIG as _D
    def _w2(d, pre=""):
        for _k, _v in d.items():
            _p = ("%s.%s" % (pre, _k)) if pre else _k
            _w2(_v, _p) if isinstance(_v, dict) else _exist.add(_p)
    _w2(_D)
except Exception as _e:  # noqa: BLE001
    print("   （读 DEFAULT_CONFIG 失败：%s）" % str(_e)[:60])
ok("C4 白名单里的键**都真实存在于 DEFAULT_CONFIG**（写错键名＝偷偷造垃圾配置）",
   _real <= _exist, "白名单 %d 个 / 配置里存在 %d 个" % (len(_real), len(_real & _exist)))


SEEN.clear()
out, rc = _run("lowspec", "off")
ok("C5 `lowspec off` 有快照时恢复（没快照就明说不改）",
   rc == 0 and ("已恢复" in out or "没找到低配前的快照" in out), out.strip()[:50])

print("\n── D. cfg set 不许整份覆盖 ──")
SEEN.clear()
out, rc = _run("cfg", "set", "wechat.poll_interval", "6")
body = next((b for m, p, _h, b in SEEN if m == "POST" and p.startswith("/api/config")), None)
flat = []
def _f(d, pre=""):
    for k, v in (d or {}).items():
        if k == "_touched":      # 声明不是配置键，别算进来
            continue
        (flat.append(("%s.%s" % (pre, k)) if pre else k) if not isinstance(v, dict) else _f(v, ("%s.%s" % (pre, k)) if pre else k))
_f(body)
ok("D1 只提交了那一个键（+ `_touched` 声明）",
   flat == ["wechat.poll_interval"], "提交了：" + "、".join(flat))
ok("D2 带了 `_touched`（后端据此挡住「没被用户碰过」的安全开关翻转）",
   isinstance(body, dict) and body.get("_touched") == [], str(body)[:80])
ok("D3 反向锚：body 里没有整份配置的影子（没有别的段）",
   isinstance(body, dict) and set(body) <= {"wechat", "_touched"}, "段：" + "、".join(sorted(body or {})))

print("\n── E. CLI 用的路径都是产品**真实存在**的路由 ──")
_web = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
_cli = io.open(CLI, encoding="utf-8").read()
import re
used = sorted(set(re.findall(r'"(/api/[a-z0-9_/]+)"', _cli)))
_routes_src = io.open(os.path.join(ROOT, "agent", "routes.py"), encoding="utf-8").read()
missing = [u for u in used if not (_sm.has(_web, '"%s"' % u) or _sm.has(_routes_src, '"%s"' % u))]
ok("E1 CLI 引用的 API 路径全部能在 webui 的路由表里找到",
   not missing, "找不到：" + "、".join(missing) if missing else "共 %d 条" % len(used))

srv.shutdown()
print("\n── F. 中文数字菜单（零基础：不用记命令）──")
# ⛔ 前面 A–D 段跑了十几条子进程请求之后，这台假服务端会**不再响应**（实测 F0 探针 timed out）
#    ⇒ 不是 CLI 的问题，是测试脚手架被用旧了。这里换一台**全新**的服务端再验菜单。
_restart_server()
_probe_out, _probe_rc = _run("status")
ok("F0 探针：换新服务端后可用（这一条红了就说明下面的失败是脚手架问题，不是 CLI 问题）",
   _probe_rc == 0 and "家人群" in _probe_out,
   "rc=%s 尾部=%s" % (_probe_rc, _probe_out[-140:].replace(chr(10), " / ")))


def _menu(feed, *args, url=None, timeout=90, debug=False):
    """把按键序列喂给菜单（模拟双击后一路回车）。返回 (输出, 退出码)。

    ⛔ **用临时文件当 stdin，不用 `input=` 管道**：实测（本机 Windows）用管道喂输入时，
       子进程连**父进程里的假服务端**会 `timed out`（而 A 段不带 stdin 的子进程一切正常）
       ⇒ 判据会假红。临时文件同样能喂输入，且不引入这个坑。
    """
    env = dict(os.environ)
    if debug:
        env["PMCTL_DEBUG"] = "1"
    fd, path = tempfile.mkstemp(prefix="pmctl-in-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(feed)
        with open(path, "r", encoding="utf-8") as fin:
            r = subprocess.run([PY, "-X", "utf8", CLI, "--url", url or BASE, "--token", "T0K"] + list(args),
                               stdin=fin, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout, env=env,
                               creationflags=0x08000000)
    finally:
        try:
            os.remove(path)
        except OSError as e:
            print("   （临时输入文件没删掉：%s）" % str(e)[:50])
    return (r.stdout or "") + (r.stderr or ""), r.returncode


out, rc = _menu("0\n", "--menu")
ok("F1 双击等价物：**不给子命令**就进菜单（不是打 usage/help）",
   "无界面操作台" in out and "请输入数字" in out and rc == 0, out.splitlines()[1] if out else "")
out, rc = _menu("1\n\n0\n", "--menu", debug=True)
ok("F2 选 1 ⇒ 真打印运行状态（对真实 HTTP 服务端）", "2026.9.28.6" in out and "家人群" in out,
   "命中行=%s" % ([l.strip()[:60] for l in out.splitlines()
                 if ("错误" in l or "没成功" in l or "版本" in l or "监听" in l)][:6]))
out, rc = _menu("abc\n99\n0\n", "--menu")
ok("F3 乱按（abc / 99）⇒ 友好提示、**不崩**、还能继续选",
   ("没看懂" in out or "超出范围" in out) and rc == 0, "")
# ⛔ 不许写死菜单序号（后来加了"开机自动启动"那项，8→9 会让判据假红）⇒ 从菜单文字里找序号
_menu_txt, _ = _menu("0\n", "--menu")
_fb_ord = next((l.strip().split(".")[0].strip() for l in _menu_txt.splitlines() if "生成反馈包" in l), "8")
out, rc = _menu("%s\n0\n" % _fb_ord, "--menu")
ok("F4 菜单里有「生成反馈包」且能跑（零基础出问题就选它）",
   "反馈包" in out and (".txt" in out or "没取到" in out), "")
out, rc = _menu("1\n\n0\n", "--menu", url="http://127.0.0.1:1/")
ok("F5 服务连不上时菜单**不进死**（提示后可继续选别的）",
   rc == 0 and "连不上" in out and "无界面操作台" in out, "")
ok("F6 菜单项里带**人话说明**（不出现 interface/API/token 这类词）",
   "看运行状态（推荐先看这个）" in out and "API" not in out and "token" not in out, "")

print("\npmctl 判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
