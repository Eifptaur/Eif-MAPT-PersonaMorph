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
from agent.routes import ROUTES, PATTERNS, HANDLERS # noqa: E402

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
    """代码里三条链实际应答的字面路由：`{路径: {"GET"/"POST"}}`，外加非字面分支数。

    ⚠️ **为什么必须是三条**：`do_GET` 遇到 `/dsh-whale/…` 会转交给 `Handler._whale_get`
    再分派（那一支在 `do_GET` 里是 `path.startswith(...)`，属非字面分支、已登记在
    `agent/routes.py::PATTERNS`）。以前只扫两条链 ⇒ **挂件那条 GET 链完全在判据的覆盖之外**：
    它独占的 13 条路径既不在表里、判据也不会报（实测：表 113 条、真值 126 条，而判据只报出
    6 条"漏登记"）。这类"判据自己看不到的一面"最危险 —— 全绿并不代表全量对齐。
    """
    def _any_fn(name):
        """按名字在**整棵树**里找函数：三条链不在同一个类里（`do_GET`/`_handle_body_request`
        在 `Handler`，而 `_whale_get` 在 `WebUI`）⇒ 只搜 `Handler` 会找不到、直接 None 崩。"""
        for _n in ast.walk(TREE):
            if isinstance(_n, ast.FunctionDef) and _n.name == name:
                return _n
        return None

    out, npatt = {}, 0
    for fname, method in (("do_GET", "GET"), ("_handle_body_request", "POST"),
                          ("_whale_get", "GET")):
        f = _any_fn(fname)
        if f is None:
            raise RuntimeError("分派链 %s 找不到（改名了？判据得跟着改）" % fname)
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
_console = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
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

print("== E. 声明 ↔ 实现（「声明了却没人接」与「接了却没声明」双向为 0）==")
#    为什么要这一轴：前面几条只对账"表 ↔ 链 ↔ 前端"，而**表里的函数名到底存不存在**、
#    **有实现却忘了接线**这两件事没人管 —— 那正是"声明了却恒空实现"那一类缺陷的入口
#    （用户按表调 ⇒ 404，或者能力写了却永远走不到）。
def _handler_names():
    out = {}
    for _path, _m in HANDLERS.items():
        for _meth, _h in _m.items():
            out.setdefault(_h, []).append("%s %s" % (_path, _meth))
    return out

_W_SRC = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
_W_METHODS = set(re.findall(r"^\s*def (\w+)\(", _W_SRC, re.M))
_names = _handler_names()
_missing = sorted("%s（%s）" % (h, "、".join(v[:2])) for h, v in _names.items() if h not in _W_METHODS)
ok("E1 `HANDLERS` 指的每个函数名都在 `webui.py` 里有实定义（指了不存在的名字 ⇒ 按表调就 404）",
   not _missing, "缺失：%s" % _missing[:5])
_rapi = sorted(re.findall(r"^\s*def (_rapi_\w+)\(", _W_SRC, re.M))
_unwired = [x for x in _rapi if x not in _names]
ok("E2 `webui.py` 里每个 `_rapi_*` 都被接线（有实现却没进表 ⇒ 那个能力永远走不到）",
   not _unwired, "未接线：%s" % _unwired[:6])
#: E3：只声明、没有 HANDLERS 条目的路径必须**落在登记的类别里**（不是"漏搬"）
_ROUTES_ONLY = sorted(set(ROUTES) - set(HANDLERS))
_ALLOW = ("/", "/index.html", "/api/update", "/api/version")   # 静态页 + 探活/版本（刻意留链上）
_bad_only = [x for x in _ROUTES_ONLY
             if not (x in _ALLOW or x.startswith("/dsh-whale/"))]
ok("E3 只声明、没搬进 `HANDLERS` 的路径都是登记过的类别（静态页 / 挂件资源 / 探活接口）",
   not _bad_only, "没登记的：%s" % _bad_only[:6])
#: 分母守卫：三条都得真的扫到东西
ok("E4 分母守卫（方法数/接线数/差集规模都要像样）",
   len(_W_METHODS) > 120 and len(_rapi) > 80 and len(_names) > 80,
   "方法 %d · _rapi %d · 已接线 %d" % (len(_W_METHODS), len(_rapi), len(_names)))
#: 反向控制：判定器必须**能认出**"指了不存在的名字"（拿合成数据试）
_fake = {"x": {"GET": "_这个函数不存在"}}
ok("E5 反向控制：合成一条「指向不存在的函数」必须被判出来",
   bool([h for h in _fake["x"].values() if h not in _W_METHODS]), "")

print("== 路由表判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
sys.exit(1 if FAIL[0] else 0)
