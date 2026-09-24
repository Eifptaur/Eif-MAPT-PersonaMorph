# -*- coding: utf-8 -*-
"""**唯一路由表**的对账判据。

`agent/routes.py` 声明了"每个 `/api/…` 路径允许哪些方法"，而代码仍是 `webui.py` 里那两条互相独立的
分派链（`do_GET` / `_handle_body_request`）。**表与代码必须逐条一致**：
  · 表里多一条、代码没有 ⇒ 用户按表调会 404（纸面能力）；
  · 代码里多一条、表没有 ⇒ 加路由的人漏登记（对账失效，CowAgent 的教训：文档不在判据范围内就会漂）。
本判据双向锁死，并**自带灵敏度自证**（两个反向锚：喂合成的"只多一边"数据 ⇒ 检测器必须报）。

跑法：runtime\\python\\python.exe scripts\\route_table_selftest.py
"""
import ast
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception: # noqa: BLE001
    pass

import _srcmatch as _sm # noqa: E402
from agent.routes import ROUTES, PATTERNS # noqa: E402

PASS, FAIL = [0], [0]


def ok(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("  [%s]" % detail if detail else ""))
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
    return bool(cond)


SRC = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
TREE = ast.parse(SRC)


def _handler():
    for n in ast.walk(TREE):
        if isinstance(n, ast.ClassDef) and n.name == "Handler":
            return n


H = _handler()


def _fn(name):
    for n in ast.walk(H):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n


def _chain(node):
    out, cur = [], node
    while True:
        out.append((cur.test, cur.body, cur.lineno))
        nxt = cur.orelse
        if not nxt:
            break
        if len(nxt) == 1 and isinstance(nxt[0], ast.If):
            cur = nxt[0]
        else:
            break
    return out


def _lit(test):
    if test is None:
        return set()
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq):
        l, r = test.left, test.comparators[0]
        for a, b in ((l, r), (r, l)):
            if isinstance(a, ast.Name) and a.id == "path" and isinstance(b, ast.Constant) \
                    and isinstance(b.value, str) and b.value.startswith("/"):
                return {b.value}
        return set()
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.In):
        if isinstance(test.left, ast.Name) and test.left.id == "path":
            return {c.value for c in ast.walk(test.comparators[0])
                    if isinstance(c, ast.Constant) and isinstance(c.value, str)
                    and c.value.startswith("/")}
    return set()


def code_routes():
    """代码里两条链实际应答的字面路由：`{路径: {"GET"/"POST"}}`，外加非字面分支数。"""
    out, npatt = {}, 0
    for fname, method in (("do_GET", "GET"), ("_handle_body_request", "POST")):
        f = _fn(fname)
        # ⚠️ 扫**每一条**顶层 if 链：`do_GET` 里 `/api/version`（免认证）与 `/api/update`
        #   在主链**之前**的另一条链上（只取最大的那条会漏掉它们）。
        for st in f.body:
            if not isinstance(st, ast.If):
                continue
            for test, _b, _l in _chain(st):
                ps = _lit(test)
                if ps:
                    for p in ps:
                        out.setdefault(p, set()).add(method)
                elif test is not None:
                    seg = re.sub(r"\s+", " ", ast.get_source_segment(SRC, test) or "")
                    if seg and not seg.startswith("path.startswith(\"/assets") \
                            and "_dispatch(" not in seg:
                        npatt += 1
    # ⛔ 已搬到路由表的那些路径**不在链里了** ⇒
    #   "能不能应答"要算上 `agent/routes.py::HANDLERS`；另外 `elif self._dispatch(...)` 那一支
    #   是**搬迁本身**留下的，不算"非字面路由分支"。
    try:
        from agent.routes import HANDLERS as _H
    except Exception: # noqa: BLE001
        _H = {}
    for _p2, _row in _H.items():
        for _m2 in _row:
            out.setdefault(_p2, set()).add(str(_m2).upper())
    return out, npatt


def diff(table, code):
    """双向差异（**纯函数**，反向锚直接喂合成数据测它）。返回人类可读的问题列表。"""
    bad = []
    for p in sorted(set(table) - set(code)):
        bad.append("表里有、代码没有：%s（用户按表调会 404）" % p)
    for p in sorted(set(code) - set(table)):
        bad.append("代码里有、表里没有：%s（加路由漏登记）" % p)
    for p in sorted(set(table) & set(code)):
        t, c = set(table[p]), set(code[p])
        if t != c:
            bad.append("%s 的方法对不上：表=%s 代码=%s" % (p, sorted(t), sorted(c)))
    return bad


CODE, NPATT = code_routes()
print("== A. 表 == 代码（双向）==")
_bad = diff(ROUTES, CODE)
ok("A1 %d 条路由逐条对齐（表 %d · 代码 %d）" % (len(CODE), len(ROUTES), len(CODE)),
   not _bad, "; ".join(_bad[:6]))
ok("A2 非字面分支也登记在案（代码 %d 个 · 表里 PATTERNS %d 条）" % (NPATT, len(PATTERNS)),
   len(PATTERNS) == NPATT, "PATTERNS=%d" % len(PATTERNS))

print("== B. 前端契约：页面调的每个「方法+路径」都必须被表覆盖 ==")
_console = io.open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
CL = _console.splitlines()
CALL = re.compile(r"""(getJSON|postJSON|putJSON|delJSON|fetch)\s*\(\s*([`'"])([^`'"]+)\2""")
METHOD = re.compile(r"""method\s*:\s*['"](\w+)['"]""")
fe = set()
for i, ln in enumerate(CL, 1):
    for m in CALL.finditer(ln):
        fn, url = m.group(1), m.group(3)
        if not url.startswith("/api"):
            continue
        w = "\n".join(CL[i - 1: i + 6])
        mm = METHOD.search(w)
        method = (mm.group(1).upper() if mm else ("POST" if fn in ("postJSON", "putJSON") else "GET"))
        fe.add((method, url.split("?")[0]))
_miss = ["%s %s" % (m, u) for (m, u) in sorted(fe) if u not in ROUTES or m not in ROUTES[u]]
ok("B1 前端 %d 个「方法+路径」组合全在表里且方法对得上" % len(fe), not _miss, "缺：%s" % _miss[:6])

print("== C. 反向锚：判据自己也抓得住「只多一边」==")
_syn_code = {"/api/x": {"GET"}}
ok("C1 合成「表里有、代码没有」⇒ 报「用户按表调会 404」",
   any("表里有、代码没有" in x for x in diff({"/api/x": {"GET"}}, {})),
   str(diff({"/api/x": {"GET"}}, {})[:1]))
ok("C2 合成「代码里有、表里没有」⇒ 报「加路由漏登记」",
   any("代码里有、表里没有" in x for x in diff({}, {"/api/y": {"POST"}})),
   str(diff({}, {"/api/y": {"POST"}})[:1]))
ok("C3 合成「方法对不上」⇒ 报出来",
   any("方法对不上" in x for x in diff({"/api/z": {"GET"}}, {"/api/z": {"GET", "POST"}})),
   str(diff({"/api/z": {"GET"}}, {"/api/z": {"GET", "POST"}})[:1]))
ok("C4 两边一致 ⇒ 一条问题都不报（不虚报）", diff(ROUTES, CODE) == [], str(_bad[:2]))

print("== D. 表本身可读（这一条是 Phase A 的全部意义）==")
ok("D1 每条都写明了允许的方法，且只出现 GET/POST/PUT",
   all(isinstance(v, (tuple, list)) and len(v) >= 1
       and all(str(x).upper() in ("GET", "POST", "PUT") for x in v) for v in ROUTES.values()),
   str([k for k, v in ROUTES.items() if not v][:3]))
ok("D2 两条链都接的路由在表里也能看出来（方法超过一个）",
   sum(1 for v in ROUTES.values() if len(v) > 1) == sum(1 for v in CODE.values() if len(v) > 1),
   "%d / %d" % (sum(1 for v in ROUTES.values() if len(v) > 1),
                sum(1 for v in CODE.values() if len(v) > 1)))
_src_routes = io.open(os.path.join(ROOT, "agent", "routes.py"), encoding="utf-8").read()
ok("D3 Phase B（物理合并）的三个机械陷阱写进了表文件的备注（不然下一批会重新踩）",
   _sm.has(_src_routes, "body[-1].end_lineno") and _sm.has(_src_routes, "Handler") \
   and _sm.has(_src_routes, "类体内") and _sm.has(_src_routes, "插入下标"))

print("== 路由表判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
sys.exit(1 if FAIL[0] else 0)
