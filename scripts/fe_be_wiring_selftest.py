# -*- coding: utf-8 -*-
"""前后端连接判据（第十五轮 **V-R15-1**）—— 网友报「点了没反应」那类问题的守备。

**为什么要它**：`agent/webui.py` 里有两套独立的 `/api/...` 分派链 —— `do_GET`（读）与
`_handle_body_request`（POST/PUT，动）。同一个能力若**只注册在其中一条**，而前端用的是另一条，
请求就会落到那条链末尾的 `else: 404`；前端 `getJSON` 在 `!r.ok` 时抛，接住的地方弹一句
`{"error": "not found"}` 或干脆什么都不弹 ⇒ 用户看到的是「点了没反应」。
这个毛病**第十二轮就修过一次**（`/api/archive`：原先只有 POST 链、前端用 GET ⇒ 整个「屏蔽存档」面板是死的），
第十五轮又实测抓到 **6 处**（方向相反：五条只有 GET、前端发 POST；一条只有 POST、前端发 GET）。

判据两条腿：
  A. **静态对账**（全量、机械）：把 `agent/console_html.py` 里每个 API 调用的**真方法**
     （`getJSON(url,{method:'POST'})` 里的 method，不是 helper 名）抠出来，逐条断言它能在
     `webui.py` 的**对应链**里找到该路径（含"链 → 共用方法"的一跳）。
     反向锚：把任一镜像分支删掉 ⇒ 本条立刻红。
  B. **活体路由**（行为级）：真起一个 `WebUI`（桩 parent + 打桩各依赖模块，**零副作用**），
     对那 6 条修好的路径按前端用的方法各发一次请求，断言**不是 404**（且 `/api/prompt/preview`
     回的是提示词预览而不是 not found）。反向锚：把镜像删掉 ⇒ 404 ⇒ 红。

跑法：runtime\\python\\python.exe scripts\\fe_be_wiring_selftest.py
"""
import io
import os
import re
import socket
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  [%s]" % (name, extra))


WEBUI = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
CONSOLE = io.open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
FUNC = re.compile(r"^(\s*)def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
PATHPART = re.compile(r"""["'](/api/[A-Za-z0-9_\-/.]*)["']""")
CALLSELF = re.compile(r"self\.(_[A-Za-z_][A-Za-z0-9_]*)\s*\(")


def _func_ranges(src):
    """按**缩进**切出每个 `def` 的行区间（嵌套 def 也要正确收尾，这是上一版审计脚本的坑）。"""
    lines = src.splitlines()
    stack = []                      # [(indent, name, start_idx)]
    out = {}
    for i, ln in enumerate(lines):
        m = FUNC.match(ln)
        if m:
            ind, name = len(m.group(1)), m.group(2)
            while stack and stack[-1][0] >= ind:
                _ind, _nm, _st = stack.pop()
                out.setdefault(_nm, []).append((_st, i - 1))
            stack.append((ind, name, i))
    while stack:
        _ind, _nm, _st = stack.pop()
        out.setdefault(_nm, []).append((_st, len(lines) - 1))
    return lines, out


LINES, RANGES = _func_ranges(WEBUI)


def _body(name):
    return "\n".join(LINES[RANGES[name][0][0]: RANGES[name][0][1] + 1])


def _paths_in(text):
    return set(PATHPART.findall(text))


def _reachable(chain):
    """这条链能应答的路径集合：链自己的字面量 + 它调用的同文件方法（含共用方法）里的字面量。"""
    seen, todo = set(), [chain]
    paths = set()
    while todo:
        fn = todo.pop()
        if fn in seen or fn not in RANGES:
            continue
        seen.add(fn)
        txt = _body(fn)
        paths |= _paths_in(txt)
        for callee in CALLSELF.findall(txt):
            if callee in RANGES:
                todo.append(callee)
    return paths


GET_PATHS = _reachable("do_GET")
BODY_PATHS = _reachable("_handle_body_request")

# ── 前端调用（方法从 opts 里读；helper 名字不是方法） ─────────────────────
CLINES = CONSOLE.splitlines()
CALL = re.compile(r"""(getJSON|postJSON|putJSON|delJSON|fetch)\s*\(\s*([`'"])([^`'"]+)\2""")
METHOD = re.compile(r"""method\s*:\s*['"](\w+)['"]""")
fe_calls = []
for i, ln in enumerate(CLINES, 1):
    for m in CALL.finditer(ln):
        fn, _, url = m.group(1), m.group(2), m.group(3)
        if not url.startswith("/api"):
            continue
        window = "\n".join(CLINES[i - 1: i + 6])
        mm = METHOD.search(window)
        method = (mm.group(1).upper() if mm
                  else ("POST" if fn in ("postJSON", "putJSON") else "GET"))
        fe_calls.append((i, method, url.split("?")[0]))

print("── A. 静态对账：前端每个调用都必须落在**对应那条链**里 ──")
uniq = sorted(set((m, u) for _, m, u in fe_calls))
missing = []
for method, url in uniq:
    pool = GET_PATHS if method == "GET" else BODY_PATHS
    if url not in pool:
        missing.append((method, url, [l for l, m2, u2 in fe_calls if u2 == url and m2 == method]))
ok("A1 前端 %d 个「方法+路径」组合，全部能在对应链里找到（GET→do_GET｜POST/PUT→body 链）"
   % len(uniq), not missing,
   "缺路由：" + "; ".join("%s %s（console_html.py:%s）" % (m, u, ",".join(map(str, ls)))
                          for m, u, ls in missing[:6]))

# 反向锚（源码级）：那 6 条共享动作必须**两条链都能应答**（删任一侧的调用就会掉出集合）
SHARED = ("/api/file_search/add", "/api/file_search/del", "/api/tools/new_manifest",
          "/api/ui_fingerprint/take", "/api/ui_fingerprint/forget", "/api/prompt/preview",
          "/api/personas/favs")
_both = [p for p in SHARED if p in GET_PATHS and p in BODY_PATHS]
ok("A2 六条「两条链都接」的动作确实两侧都可应答（V-R15-1 的修法）", len(_both) == len(SHARED),
   "只在一侧的：%s" % [p for p in SHARED if p not in _both])
ok("A3 反向锚就位：把 `_handle_body_request` 里的共用方法调用删掉，A1 会红（判据真读调用关系）",
   "self._file_search_dirs(" in _body("_handle_body_request")
   and "self._ui_fingerprint_take(" in _body("_handle_body_request"))

print("\n── B. 活体路由：真起 WebUI（桩 parent + 打桩依赖）按前端用的方法各打一次 ──")
_tmp = None
_w = None
try:
    import tempfile
    import json as _json
    from agent import webui as W
    from agent import config as C
    from agent import file_search as FS
    from agent import user_tools as UT
    from agent import ui_fingerprint as UFP
    from agent import system_prompt as SP
    from agent import wechat as WX

    _tmp = tempfile.mkdtemp(prefix="pm-febewire-")
    _saved = {}

    def _stub(mod, name, val):
        _saved[(mod, name)] = getattr(mod, name, None)
        setattr(mod, name, val)

    # **零副作用**：配置写、模板写、指纹写全部打桩到临时区。
    # ⛔ 这里必须把 `wechat.WeChatAdapter` 也打桩 —— 第一次跑这条判据时忘了它，
    #   结果 `_ui_fingerprint_take` 真去构造了驱动库的 GUI，**真的借用并还原了用户微信窗口的几何**
    #   （实测日志：`借用了微信窗口几何：hwnd=447223716 …`）⇒ 判据碰了用户的微信、还写了
    #   `data/window_borrow.json`。判据不许有这种副作用（本项目的"不许打扰用户"红线）。
    class _FakeWx(object):
        def _get_gui(self):
            return None

    _stub(WX, "WeChatAdapter", _FakeWx)
    C.set_config(C.DEFAULT_CONFIG) if hasattr(C, "DEFAULT_CONFIG") else None
    _stub(C, "CONFIG_FILE", os.path.join(_tmp, "config.json"))
    _stub(C, "save_config", lambda cfg=None, path=None: os.path.join(_tmp, "config.json"))
    _stub(FS, "snapshot", lambda: {"dirs": []})
    _stub(UT, "write_template", lambda: (os.path.join(_tmp, "t.json"), ""))
    _stub(UFP, "take", lambda gui, names=None: {"ok": ["probe"], "failed": []})
    _stub(UFP, "forget", lambda key=None: 1)
    _stub(UFP, "hits", lambda: {})
    _stub(UFP, "digest", lambda: "0" * 12)
    _stub(SP, "preview", lambda: {"ok": True, "system": "判据用假提示词"})

    _s = socket.socket()
    _s.bind(("127.0.0.1", 0))
    _port = _s.getsockname()[1]
    _s.close()
    _cfg = dict(C.get_config() or {})
    _cfg["server"] = {"enabled": True, "host": "127.0.0.1", "port": _port,
                      "token": "wiring-judge-token", "auto_open_browser": False}
    _orig_get = W.get_config
    W.get_config = lambda: _cfg
    _w = W.WebUI(lambda: {}, [])
    # 判据不许写产品那份 `logs/console.url`（那是带口令的地址文件，控制台下一次开窗要读它）
    _w.console_url_root = _tmp
    _port = _w.start()
    _base = "http://127.0.0.1:%d" % _port
    _tok = "?token=wiring-judge-token"

    def _req(method, path, body=None):
        url = _base + path + _tok
        data = _json.dumps(body or {}).encode("utf-8") if method != "GET" else None
        req = urllib.request.Request(url, data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, (r.read() or b"").decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, (e.read() or b"").decode("utf-8", "replace")

    CASES = [("POST", "/api/file_search/add", {"dir": "D:\\judge-dir"}),
             ("POST", "/api/file_search/del", {"dir": "D:\\judge-dir"}),
             ("POST", "/api/tools/new_manifest", {}),
             ("POST", "/api/ui_fingerprint/take", {}),
             ("POST", "/api/ui_fingerprint/forget", {}),
             ("GET", "/api/prompt/preview", None),
             ("GET", "/api/personas/favs", None)]
    _bad = []
    for method, path, body in CASES:
        code, txt = _req(method, path, body)
        if code == 404 or "not found" in txt:
            _bad.append("%s %s ⇒ HTTP %s %s" % (method, path, code, txt[:60]))
    ok("B1 七条修好的路径按**前端用的方法**请求都不再 404（反向锚：删掉镜像 ⇒ 这里红）",
       not _bad, "; ".join(_bad[:4]))
    _code, _txt = _req("GET", "/api/prompt/preview")
    ok("B2 `/api/prompt/preview` 的 GET 真回了预览内容（不是只有路由、内容是空的）",
       "假提示词" in _txt, _txt[:80])
    _code, _txt = _req("POST", "/api/file_search/add", {"dir": "D:\\judge-dir"})
    ok("B3 加目录这条路**回执可读**（`ok` + `note`），不是 404 的原始 JSON",
       '"ok"' in _txt and '"note"' in _txt, _txt[:80])
finally:
    try:
        if _w is not None:
            _w.stop()
    except Exception:
        pass
    try:
        W.get_config = _orig_get
    except Exception:
        pass
    for (_m, _n), _v in (_saved if '_saved' in dir() else {}).items():
        try:
            setattr(_m, _n, _v)
        except Exception:
            pass
    try:
        import shutil
        if _tmp:
            shutil.rmtree(_tmp, ignore_errors=True)
    except Exception:
        pass

print("\n==== 前后端连接判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
