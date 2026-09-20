# -*- coding: utf-8 -*-
"""判据：`do_GET` 里读的请求体变量必须**在执行路径上真的存在**（第四轮审计 V-R4-15，P2）。

跑法： runtime\\python\\python.exe scripts\\webui_route_selftest.py   退出码 0=全过 / 1=有失败

为什么（审计实测）：`data` 原来**只在 `do_POST` 里定义**，而 `do_GET` 有三处分支读 `data.get(...)`
（本地生图 `/api/image_gen/local/install`、历史目录、打开路径）⇒ **必抛 NameError**，
再被外层 `except` 吞成 `{ok:false,"error":"name 'data' is not defined"}` ⇒ 4 条 GET 路由白坏、
界面上还看不出来。用 AST 判：**do_GET 里第一次出现 `data` 之前必须先给它赋值**。
"""
from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WEBUI = os.path.join(ROOT, "agent", "webui.py")

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _first_assign_lineno(fn, var):
    """函数体里**对 var 赋值**的最早行号（含 `data = {}` / `data["k"]=v` 这类）。"""
    best = None
    for node in ast.walk(fn):
        tgt = None
        if isinstance(node, ast.Assign) and node.targets:
            tgt = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            tgt = node.target
        elif isinstance(node, ast.AugAssign):
            tgt = node.target
        names = []
        if isinstance(tgt, ast.Name):
            names.append(tgt.id)
        elif isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name):
            names.append(tgt.value.id)
        if var in names:
            ln = getattr(node, "lineno", 0)
            if best is None or ln < best:
                best = ln
    return best


def _first_load_lineno(fn, var):
    """函数体里最早**读 var**（Load）的行号，但**跳过**它自己的赋值目标（Store 才算读）。"""
    best = None
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id == var and isinstance(node.ctx, ast.Load):
            ln = getattr(node, "lineno", 0)
            if best is None or ln < best:
                best = ln
    return best


def _rule(tree, getter_name="do_GET", var="data"):
    """返回 (赋值的行号, 第一次读的行号, 读的次数)；认不出来返回 (None, None, 0)。"""
    fn = _func(tree, getter_name)
    if fn is None:
        return None, None, 0
    n = sum(1 for node in ast.walk(fn)
            if isinstance(node, ast.Name) and node.id == var and isinstance(node.ctx, ast.Load))
    return _first_assign_lineno(fn, var), _first_load_lineno(fn, var), n


def main():
    src = open(WEBUI, encoding="utf-8").read()
    tree = ast.parse(src)
    _a, _l, _n = _rule(tree, "do_GET", "data")
    ok("① `do_GET` 里确实在读 `data`（说明这条判据扫的是真现场，不是空转）", _n >= 3, "读了 %d 次" % _n)
    ok("② `do_GET` 里第一次读 `data` 之前**必须先赋值**（否则 NameError 被 except 吞成 ok:false）",
       _a is not None and _l is not None and _a < _l, "赋值 L%s / 首读 L%s" % (_a, _l))
    _seg = src[src.find("def do_GET(self):"):]
    _seg = _seg[:_seg.find("def do_POST(self):")]
    ok("③ `data` 来自**查询串**（GET 没有请求体；`?dir=…&allow_online=1` 这类调用照样能用）",
       "parse_qs(parsed.query)" in _seg)
    ok("④ 开关真值走 `_truthy`（`bool(\"false\")` 是 True ⇒ 会反向打开开关）",
       "def _truthy(" in src and "_truthy(data.get(" in _seg)
    ok("④ `_truthy` 把 \"false\"/\"0\"/\"off\"/\"no\"/空串都判假",
       all(k in src[src.find("def _truthy("):][:600] for k in ('"false"', '"0"', '"off"', '"no"', '""')))

    # ⑤ 反例锚：老写法（do_GET 不定义 data 就读）必须被同一判定器判**不合格**
    _OLD = ("def do_GET(self):\n"
            "    path = parsed.path\n"
            "    if path == '/api/x':\n"
            "        d = str(data.get('dir') or '')\n"
            "    return\n")
    _a2, _l2, _n2 = _rule(ast.parse(_OLD), "do_GET", "data")
    ok("⑤ 反例锚：老写法（先读后无赋值）被判不合格",
       _n2 >= 1 and (_a2 is None or _l2 is None or _a2 > _l2), "赋值 L%s / 首读 L%s" % (_a2, _l2))

    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
