#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""控制台「重启」按钮判据（2026-09-16 用户报「我点了重启，咋没动静啊」之后立）。

**事故**：`agent/webui.py::_handle_body_request` 里 `/api/code-check` 分支写了 `import threading`，
而 Python 的规则是"函数体内只要有 import 该名字，整个函数里它就是局部变量" ⇒ 同函数更早的
`/api/restart` 分支（`threading.Timer(0.5, parent.restart_fn)`）在赋值前引用 ⇒ `UnboundLocalError`
⇒ **按钮点了没反应**。更坑的是 HTTP 仍回 200「正在后台重启机器人…」（`self._json` 在崩之前就发了）
⇒ 前端与用户都看不出错，只有 socketserver 把异常打到 `data/runtime.log` 里。

**判据分两半**（教训：光断言"源码里有那行字符串"永远抓不到这种 bug）：
  A **行为**：真起一个控制台（随机端口 + 口令），真 POST `/api/restart`，断言
    「HTTP 200」+「restart_fn 真的被调用了」——这就是用户按下去那一刻发生的事；
  B **静态**：扫出"函数体内重复 import 模块级名字，且该名字在 import 之前已被引用"的地方 ——
    这正是本事故的形态，必须是 0 处（同类隐患一次收干净，不留残余）。
"""
import ast
import io
import json
import os
import socket
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from agent import webui as W          # noqa: E402
from agent.config import get_config   # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   " + name)
    else:
        FAIL += 1
        print("  FAIL " + name + ("   [%s]" % detail if detail else ""))


print("── A. 行为：真起控制台 + 真 POST /api/restart（用户按下去那一刻）──")
_orig = W.get_config
_s = socket.socket()
_s.bind(("127.0.0.1", 0))
_free = _s.getsockname()[1]
_s.close()
TOK = "restart-judge"
_called = {"n": 0, "at": 0.0}


def _stub_restart():
    _called["n"] += 1
    _called["at"] = time.time()


w = None
try:
    base = dict(_orig() or {})
    base["server"] = {"enabled": True, "host": "127.0.0.1", "port": _free, "token": TOK,
                      "auto_open_browser": False}
    W.get_config = lambda: base
    w = W.WebUI(lambda: {}, [], restart_fn=_stub_restart)
    port = w.start()
    req = urllib.request.Request("http://127.0.0.1:%d/api/restart?token=%s" % (port, TOK),
                                 data=b"{}", headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=8) as r:
        code = r.status
        body = r.read().decode("utf-8", "replace")
    ok("POST /api/restart 返回 200", code == 200, "code=%s body=%s" % (code, body[:80]))
    ok("响应体是 {ok: true, note: 正在后台重启机器人…}",
       '"ok": true' in body or '"ok":true' in body, body[:100])
    for _ in range(30):                     # 定时器 0.5 秒后触发，最多等 3 秒
        if _called["n"]:
            break
        time.sleep(0.1)
    ok("**restart_fn 真的被调用了**（按钮背后的那一跳通了）", _called["n"] >= 1,
       "被调用 %d 次" % _called["n"])
    ok("不是「回了 200 但什么都没发生」（本事故的形态）", _called["n"] >= 1)
except Exception as e:
    ok("控制台能起来且能收 POST", False, str(e)[:160])
finally:
    W.get_config = _orig
    try:
        if w is not None:
            w.stop()
    except Exception:
        pass

print("── B. 静态：函数体内“重复 import 模块级名字且此前已引用”必须是 0 处 ──")
_hits = []
for root, dirs, files in os.walk(os.path.join(ROOT, "agent")):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for f in sorted(files):
        if not f.endswith(".py"):
            continue
        p = os.path.join(root, f)
        try:
            tree = ast.parse(io.open(p, encoding="utf-8").read())
        except Exception:
            continue
        top = set()
        for n in tree.body:
            if isinstance(n, ast.Import):
                for a in n.names:
                    top.add((a.asname or a.name).split(".")[0])
            elif isinstance(n, ast.ImportFrom):
                for a in n.names:
                    top.add(a.asname or a.name)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            local = []
            for n in fn.body:               # ⚠️ 只看**直接挂在函数体**上的 import；
                #                            嵌套 def 里的 import 属于另一个作用域、不构成这个坑
                if isinstance(n, ast.Import):
                    for a in n.names:
                        nm = (a.asname or a.name).split(".")[0]
                        if nm in top:
                            local.append((nm, n.lineno))
                elif isinstance(n, ast.ImportFrom):
                    for a in n.names:
                        nm = a.asname or a.name
                        if nm in top:
                            local.append((nm, n.lineno))
            if not local:
                continue
            first_use = {}
            for n in ast.walk(fn):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                    first_use.setdefault(n.id, n.lineno)
            for nm, ln in local:
                u = first_use.get(nm, 10 ** 9)
                if u < ln:                  # 先用后导 ⇒ 精确命中本事故的形态
                    _hits.append((os.path.relpath(p, ROOT), fn.name, u, ln, nm))
ok("没有“先用后导”的函数内重复 import", not _hits,
   "；".join("%s::%s 第%d行用/第%d行导 %s" % h for h in _hits[:4]))

print("── C. 源码级：这次那处已清掉，且模块顶部保留 import threading ──")
_src = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
_i = _src.index('elif path == "/api/code-check":')
_seg = _src[_i:_i + 1400]
# ⚠️ 别用 `"import threading" not in seg`：修这处时我在旁边写了注释解释它（注释里就提到这个名字）
#    ⇒ 按整串搜会假红。按**语句**判（整行就是 import threading）。
ok("`/api/code-check` 分支里不再有 `import threading` **语句**",
   not any(l.strip() == "import threading" for l in _seg.split("\n")))
ok("webui.py 顶部保留 `import threading`（模块级一处就够）", "\nimport threading\n" in _src)
ok("`/api/restart` 仍走 restart_fn（没被顺手改成别的）",
   "threading.Timer(0.5, parent.restart_fn).start()" in _src)

print("\n==== 重启按钮判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
