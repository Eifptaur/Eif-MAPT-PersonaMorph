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
import re
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
    import tempfile as _tf
    w.console_url_root = _tf.mkdtemp(prefix="cuj-")   # ⚠️ 判据不写产品那份 logs/console.url（2026-09-18）
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

print("── D. 重启那一跳的**顺序**（2026-09-17 用户报「你更新后的重启又关不掉自己了；不是说会再起一个新的"
      "吗，我从来没见过这个再起一个；之前杀掉就没了，现在更是杀都杀不掉」）──")
_pm = io.open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
_i_rf = _pm.index("def restart_fn():")
_rf = _pm[_i_rf:_pm.index("def community_export_fn", _i_rf)]
# ⚠️ 必须**先把注释行去掉**再数/找名字：这段的注释里就写着 `_spawn_watchdog()` / `orch.shutdown()`
#    （解释老顺序错在哪）⇒ 直接 `index/count` 会命中注释，判据假红（本文件第 150 行的同款坑）。
_rf_code = "\n".join(l for l in _rf.splitlines() if not l.strip().startswith("#"))
_m_t = re.search(r"threading\.Timer\((\d+(?:\.\d+)?), _exit_now\)", _rf_code)
_i_exit = _m_t.start() if _m_t else -1
_hard_s = float(_m_t.group(1)) if _m_t else 0
ok("restart_fn 里**先装强退**再做慢活（老顺序把它排在同步 orch.shutdown() 之后 ⇒ 一阻塞就永不退出）",
   _i_exit < _rf_code.index("_spawn_watchdog(") and _i_exit < _rf_code.index("orch.shutdown()"))
ok("orch.shutdown() 不再同步挡路（挪进守护线程）",
   "threading.Thread(target=lambda: (orch.shutdown()" in _rf_code and "daemon=True" in _rf_code)
ok("新看门狗**等旧实例退干净**再开机器人（`_spawn_watchdog(delay=6)`，6 秒 > 强退 2 秒）",
   "_spawn_watchdog(delay=" in _rf_code and (_m_t is not None) and float(re.search(r"delay=(\d+)", _rf_code).group(1)) > _hard_s)
ok("⚠️ 不许回到「用 Timer 等 6 秒再拉看门狗」那版 —— 本进程 2 秒后就 os._exit，Timer 永远不会触发"
   "（2026-09-17 第一版就是这么错的，活体自检抓到「旧退了、新没接上」）",
   "_respawn" not in _rf_code and not re.search(r"Timer\([0-9.]+,\s*[^)]*(respawn|_spawn_watchdog)", _rf_code))
ok("`_spawn_watchdog` 只在 restart_fn 里出现一次、且带 delay",
   _rf_code.count("_spawn_watchdog(") == 1 and "_spawn_watchdog(delay=" in _rf_code and (_m_t is not None) and float(re.search(r"delay=(\d+)", _rf_code).group(1)) > _hard_s)
_wd_src = io.open(os.path.join(ROOT, "scripts", "watchdog.py"), encoding="utf-8").read()
_wd_code = "\n".join(l for l in _wd_src.splitlines() if not l.strip().startswith("#"))
ok("看门狗认 `--delay=<秒>`（并支持环境变量），且**在拉起机器人之前**睡",
   '--delay=' in _wd_code and "time.sleep(_delay)" in _wd_code
   and _wd_code.index("time.sleep(_delay)") < _wd_code.index("p = subprocess.Popen"))
ok("delay 有上限（不放任意大的值进来）", "min(600, _delay)" in _wd_code)
ok("有停止标记时不白等（先看 stopped.flag 再睡）",
   "if _delay and not os.path.exists(STOP_FLAG)" in _wd_code)
ok("启动闸门会**等旧实例放开锁**（最多 12 秒）才报冲突",
   "while _legacy_pid and (time.time() - _gate_t0) < 12.0" in _pm
   and "while (not _lock_res.ok) and (time.time() - _gate_t0) < 12.0" in _pm)
ok("等锁期间有日志（不是静默重试，用户/我们事后能查到）", "等它放开锁再接手" in _pm)
# ── 2026-09-18 加（作者在另一台机器实测「现在重启不了」）──────────────────────
ok("重启会**清掉手动停止标记**（重启＝用户要它跑；留着 stopped.flag 会让新看门狗/启动器判「停着」）",
   "stopped.flag" in _rf_code and "_sf" in _rf_code
   and _rf_code.index("stopped.flag") < _rf_code.index("_spawn_watchdog("))
ok("拉起看门狗后**自证**（读 data/watchdog.pid + 判 pid 还活着）",
   "watchdog.pid" in _rf_code and "_wpid" in _rf_code)
ok("自证不过 ⇒ **直接拉起机器人本体兜底**（不再出现「旧退了、新没接上 ⇒ 机器人没了」）",
   "_spawn_bot_direct()" in _rf_code and "def _spawn_bot_direct" in _pm)
ok("兜底拉起的是机器人本体（pythonw scripts/persona_morph.py），不是再来一个看门狗",
   "_spawn_bot_direct" in _pm and '"persona_morph.py"' in _pm.split("def _spawn_bot_direct")[1][:900])


# ── 2026-09-18 加（作者：「更新就做到更新成功，不能让用户还得去下新包」）──
_ua = io.open(os.path.join(ROOT, "agent", "update_apply.py"), encoding="utf-8").read()
_wd2 = io.open(os.path.join(ROOT, "scripts", "watchdog.py"), encoding="utf-8").read()
ok("更新成功后**由自己完成交接**：spawn 新看门狗（--takeover）再 os._exit",
   "_relaunch_after_update" in _ua and "--takeover" in _ua and "os._exit(0)" in _ua)
ok("看门狗支持 --takeover（收掉残留看门狗 + 清 bot.lock/bot.pid）",
   "--takeover" in _wd2 and "bot.lock" in _wd2 and "bot.pid" in _wd2)
ok("看门狗 takeover **不许用 /T**（看门狗是机器人父进程，连树杀会把机器人一起杀掉）",
   chr(34) + "/F" + chr(34) in _wd2 and chr(34) + "/PID" + chr(34) in _wd2 and chr(34) + "/T" + chr(34) + ", " not in _wd2)

print("\n==== 重启按钮判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
