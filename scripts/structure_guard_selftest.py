# -*- coding: utf-8 -*-
"""结构体量闸门：**产品代码**里「单函数 ≥300 行」的处数**只许降**（目标是 0）。

为什么要有它：`webui.start()` 那个 2715 行的函数里，**2647 行是一个嵌套类**——
巨类藏在函数体里，读不动、也没法按功能拆。这类"结构病灶"只要没人记账，
下次重构就会**又长回去**（把类塞回函数、把分支平铺进 `main`）。
本闸门把"当前有几处、分别是哪些"钉成可复核的账。

口径（唯一真源就在本文件，探针 `root-probes/_c11_verify.py` 只验搬家是否逐行等价）：
  · 扫描面 = 仓库根的 `agent/` + `scripts/` + `ui_qt/` 三棵树里的 `.py`；
  · 跳过 `__pycache__` / `assets` / `whale-widget`（含 `upstream` 上游原文）等非产品代码；
  · 行数 = AST 的 `end_lineno - lineno + 1`（含 `def` 行，**不含装饰器行**）；
  · **豁免测试文件**（`selftest.py` / `*_selftest.py`）：本闸门守的是"产品代码可读性"，
    判据自身的篇幅不是同一种问题。豁免要付代价 ⇒ 见 `EXEMPT_*` 分母守卫（豁免面不许被撑大）。
  ⛔ 阈值是 **300**，不是"看着长就算"：切点写死在 `BIG` 里，反向控制拿 299/301 两行样本试。

⛔ **为什么不直接清零**：这些长函数是主循环 / 路由表 / 面板构建 / 工具定义表，
静态拆完**不敢直接发**（要真机冒烟逐段验），所以先钉"不许新增一个"；
每拆掉一个，把 `BASE_COUNT` 减一、从 `REGISTERED` 里删掉那一行。
新增一处 ≥300 行的函数 ⇒ 本闸门当场红 ⇒ 要么拆掉，要么**显式登记并写明为什么不拆**。

F 段守的是**另一种、可以零风险消掉的长**：长函数里若有"**闭包面为 0 的顶层嵌套 def/class**"，
那种块**随时可以整体搬到模块级**（不抓宿主任何局部 ⇒ 搬走不改变任何解析结果），
留着只是让宿主更读不动。这类块必须为 **0**：
  · 判"闭包面为 0"用 `symtable` 的 `is_free`（**按作用域**求，不能用 `ast.walk` 求名字交集 —— 会被嵌套作用域污染）；
  · 只看**顶层**嵌套块（在 `if`/`try` 里的搬出去会变成"无条件定义"，不是等价搬家）；
  · 只看 ≥300 行的函数（短函数里就近放一个 helper 是正常的可读性选择）。
"""
import ast
import io
import os
import re
import symtable
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _srcslice

PASS, FAIL = [0], [0]


def ok(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("  [%s]" % detail if detail else ""))
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
    return bool(cond)


#: 扫描面
ROOTS = ("agent", "scripts", "ui_qt")
SKIP_DIRS = {"__pycache__", "assets", "whale-widget", "upstream"}

#: 阈值（≥ 这么多行算"结构病灶"）
BIG = 300

#: 棘轮基线：**只许降**。每拆掉一处 → 减一（并同步从 REGISTERED 删除）
BASE_COUNT = 10

#: 点名清单：这一处的 `(相对路径, 函数名)`。清单与实测**必须完全一致** ——
#: 少一个（说明有处缩到 <300 行却没人改清单）或多一个（说明新长出一处没人拆）都判红。
#: 想在表里加条目，就得先回答"为什么不拆"。
REGISTERED = {
    ("agent/webui.py", "_make_handler"): "控制台 137 个路由方法的处理器类；按功能分模块需真机冒烟",
    ("scripts/persona_morph.py", "main"): "启动→监听→唤醒→巡检的主流程（已搬走 14 个可自由搬走的嵌套函数）",
    ("ui_qt/panels_custom.py", "persona_panel"): "人设面板构建；按面板拆文件待做",
    ("ui_qt/panels_custom.py", "overview_panel"): "总览面板构建",
    ("ui_qt/panels_custom.py", "check_panel"): "体检面板构建",
    ("agent/tools.py", "_builtin_tool_defs"): "内置工具声明表（一条 return，长但平铺 ⇒ 明确不拆）",
    ("agent/wechat.py", "send_text_posted"): "发送链主流程（一条 397 行 try，拆要传大量局部状态）",
    ("scripts/onestart.py", "main"): "一键启动编排",
    ("ui_qt/panels_custom.py", "memory_panel"): "记忆面板构建",
    ("agent/wechat.py", "open_chat_by_search"): "按搜索打开会话（一条 279 行 try）",
}

#: 判据侧"用 `def` 行当**文本切片边界**"的棘轮基线（只许降）。
#: 这类写法靠"函数在文件里的位置"定边界 ⇒ 函数一旦被搬位置，就会**崩**或**静默切错**
#: （判据含义悄悄变了、甚至因为别处的文字而假绿）。能机械改写的都已换成 `_srcslice` 的 AST 锚点。
JUDGE_TEXT_ANCHOR_BASE = 120

#: 其中**仍属"可机械改写形态"但有意留下**的（按文件计数；表与实测必须**完全一致**）。
#: 留下的三类理由：① 切片目标是**片段**（再对片段做 AST 解析会语法错）；
#: ② 该变量**下游还要按"下一个 def"再切**（上游一收窄，下游就找不到锚点）；
#: ③ `\n    def ` 这种"下一个同级方法"边界，语义要另做，不在等价改写范围内。
JUDGE_KEEP = {
    ("scripts/account_follow_selftest.py", 1),
    ("scripts/background_selftest.py", 1),
    ("scripts/config_mask_selftest.py", 1),
    ("scripts/console_open_selftest.py", 1),
    ("scripts/memory_selftest.py", 4),
    ("scripts/misdelivery_selftest.py", 1),
    ("scripts/self_local_selftest.py", 3),
    ("scripts/send_fail_reason_selftest.py", 1),
    ("scripts/send_file_posted_selftest.py", 2),
    ("scripts/send_guard_selftest.py", 4),
    ("scripts/send_loop_behavior_selftest.py", 1),
    ("scripts/send_verdict_selftest.py", 2),
    ("scripts/store_archive_selftest.py", 1),
    ("scripts/webui_route_selftest.py", 1),
    ("scripts/window_borrow_selftest.py", 1),
    ("ui_qt/selftest.py", 1),
}

_Q = r"""["']"""
#: "可机械改写"的形态（与搬迁器同口径）：判据里新出现这类写法 ⇒ 直接红
_CONVERTIBLE = (
    re.compile(r"\w+(?:\.\w+)*\.split\(\s*" + _Q + r"def (?:%s|\w+)\([^\"']*" + _Q +
               r"\s*(?:%\s*\w+\s*)?\)\[1\](?:\[:\d+\])?"),
    re.compile(r"\w+\[\s*\w+\.(?:index|find)\(\s*" + _Q + r"def \w+[^\"']*" + _Q + r"\)\s*:\s*\]"),
    re.compile(r"\w+\[\s*:\s*\w+\.(?:index|find)\(\s*" + _Q + r"def \w+[^\"']*" + _Q + r"\)\s*\]"),
    re.compile(r"\w+\[\w+\.(?:index|find)\(\s*" + _Q + r"def \w+[^\"']*" + _Q +
               r"\)\s*:\s*\w+\.(?:index|find)\(\s*" + _Q + r"def \w+"),
)
#: 判据里"用 `def` 行当边界"的总量口径（含故意保留的与存在性/计数用途）
_TEXT_ANCHOR = re.compile(r"(index|find|rfind|split)\s*\(\s*" + _Q + r"(?:\\n|\s)*def\s")


#: ⛔ 本文件必须从扫描面里排除：它把"可改写形态"的正则**当数据写在源码里**
#: ⇒ 扫它等于自指（自己的模式字面量会被自己数成违规）。排除要付代价：见 G 段的
#: "豁免面就这一个文件"断言 + 分母守卫（扫到的判据文件数下限）。
_SELF_REL = "scripts/structure_guard_selftest.py"


def _judge_files(exclude_self=True):
    """→ 判据文件名列表（`scripts/*_selftest.py` + `ui_qt/selftest.py`）。

    `exclude_self=True` 时排除本文件（它把"可改写形态"的正则当**数据**写在源码里 ⇒ 扫它是自指）。
    排除要付代价：G 段有一条"豁免面就这一个文件"的断言（拿 `exclude_self=False` 的数对账）。
    """
    out = []
    for sub, only in (("scripts", "_selftest.py"), ("ui_qt", "selftest.py")):
        d = os.path.join(ROOT, sub)
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if f.endswith(".py") and (f.endswith(only) or f == only):
                rel = os.path.join(sub, f).replace("\\", "/")
                if exclude_self and rel == _SELF_REL:
                    continue
                out.append(rel)
    return out


def judge_text_anchor_report():
    """→ (总量, 可改写形态计数{(文件, 处数)}, 文件数)"""
    total, per, nfiles = 0, [], 0
    for rel in _judge_files():
        src = _read(rel)
        nfiles += 1
        total += len(_TEXT_ANCHOR.findall(src))
        n = sum(1 for line in src.split("\n")
                if any(rx.search(line) for rx in _CONVERTIBLE))
        if n:
            per.append((rel, n))
    return total, sorted(per), nfiles

#: 豁免面的**分母**（豁免测试文件但这几棵树不许被改窄到"什么都没扫到"）
EXEMPT_MIN_FILES = 100
EXEMPT_MIN_FUNCS = 1000
#: 被扫描面的**分母**（同上用途，防 `ROOTS` 被改名后静默漏扫）
MIN_FILES = 120
MIN_FUNCS = 3000
#: 每棵子树的贡献下限（防某棵树整体消失后"恰好没有长函数"）
MIN_PER_ROOT = 50


def is_test_file(rel):
    base = os.path.basename(rel)
    return base == "selftest.py" or base.endswith("_selftest.py")


def big_functions_of(tree):
    """→ [(行数, 函数名)]（口径见文件头：AST 行数差，不含装饰器行）。"""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            span = (n.end_lineno or n.lineno) - n.lineno + 1
            if span >= BIG:
                out.append((span, n.name))
    return out


def big_functions(src):
    """同 `big_functions_of`，但入参是源码文本（反向控制用）。"""
    return big_functions_of(ast.parse(src))


def _read(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8", errors="ignore").read()


def find_owner(tree, name):
    """→ 该名字对应的函数节点（**不限层级**：模块级函数与类方法都要能查到）。"""
    hits = [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    return hits


def liftable_blocks(src, rel, fname):
    """→ [(嵌套块名, 行数)]：`fname` 的**顶层**嵌套 def/class 里，**闭包面为 0** 的那些。

    这类块与宿主函数零耦合 ⇒ 可以整体搬到模块级且不改变任何解析结果（"等价搬家"）。
    口径：`symtable` 里该块上 `is_free()` 为真的名字 = 它从**外层函数作用域**抓的名字。
    宿主不限层级（模块级函数与类方法都算）—— 方法里同样可能藏着这种块。
    """
    hits = find_owner(ast.parse(src), fname)
    if not hits:
        return []
    owner = hits[0]
    direct = {c.name: c for c in owner.body
              if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}

    def _walk(t):
        yield t
        for c in t.get_children():
            yield from _walk(c)

    tab = None
    for t in _walk(symtable.symtable(src, rel, "exec")):
        if t.get_name() == fname and t.get_type() == "function":
            tab = t
            break
    if tab is None:
        return []
    out = []
    for c in tab.get_children():
        if c.get_type() not in ("function", "class") or c.get_name() not in direct:
            continue
        if [s for s in c.get_symbols() if s.is_free()]:
            continue
        node = direct[c.get_name()]
        out.append((c.get_name(), (node.end_lineno or node.lineno) - node.lineno + 1))
    return sorted(out, key=lambda r: -r[1])


def collect():
    """→ (产品侧 [(rel, name, 行数)], (测试文件数, 测试函数数), 逐树函数数, 产品文件数, 解析失败清单)"""
    prod, per_root, unparsed = [], {}, []
    test_files = test_funcs = prod_files = 0
    for sub in ROOTS:
        per_root[sub] = 0
        for dp, dn, fns in os.walk(os.path.join(ROOT, sub)):
            dn[:] = [d for d in dn if d not in SKIP_DIRS]
            for fn in sorted(fns):
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dp, fn)
                rel = os.path.relpath(p, ROOT).replace("\\", "/")
                try:
                    tree = ast.parse(io.open(p, encoding="utf-8", errors="ignore").read())
                except SyntaxError as exc:  # noqa: BLE001
                    # ⛔ 不许静默跳过：跳过等于"这个文件不算数"，扫描面会**悄悄变小**
                    #   而上面的分母守卫只会少算一个文件、照样绿 ⇒ 得单独记下来判红。
                    unparsed.append("%s: %s" % (rel, exc))
                    continue
                found = big_functions_of(tree)
                nfun = sum(1 for n in ast.walk(tree)
                           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
                if is_test_file(rel):
                    test_files += 1
                    test_funcs += nfun
                    continue
                prod_files += 1
                per_root[sub] += nfun
                for span, name in found:
                    prod.append((rel, name, span))
    prod.sort(key=lambda r: -r[2])
    return prod, (test_files, test_funcs), per_root, prod_files, unparsed


def main():
    prod, (ex_files, ex_funcs), per_root, prod_files, unparsed = collect()
    actual = {(rel, name) for rel, name, _ in prod}

    print("== A. 读数 ==")
    print("   产品侧函数合计 %d 个（%d 棵子树）；测试侧 %d 文件 / %d 函数"
          % (sum(per_root.values()), len(ROOTS), ex_files, ex_funcs))
    print("   单函数 >= %d 行：**%d 处**" % (BIG, len(prod)))
    for rel, name, span in prod:
        print("     %5d 行  %s :: %s" % (span, rel, name))

    print("== B. 棘轮（只许降）==")
    ok("结构病灶处数不超过基线", len(prod) <= BASE_COUNT, "当前 %d / 基线 %d" % (len(prod), BASE_COUNT))

    print("== C. 点名清单 ==")
    missing = sorted(actual - set(REGISTERED))
    stale = sorted(set(REGISTERED) - actual)
    ok("没有「越过清单长出来的」长函数", not missing,
       "未登记 %d 处：%s" % (len(missing), "、".join("%s::%s" % m for m in missing[:4])))
    ok("清单里没有已经不合格的条目（缩到 <%d 行要删条目）" % BIG, not stale,
       "待删 %d 条：%s" % (len(stale), "、".join("%s::%s" % s for s in stale[:4])))

    print("== D. 分母守卫（防「扫描面被改窄后恰好全绿」）==")
    ok("产品侧分母：真扫到了足够多的函数", sum(per_root.values()) >= MIN_FUNCS,
       "扫到 %d / 下限 %d" % (sum(per_root.values()), MIN_FUNCS))
    ok("产品侧分母：真扫到了足够多的文件", prod_files >= MIN_FILES,
       "扫到 %d / 下限 %d" % (prod_files, MIN_FILES))
    for sub in ROOTS:
        ok("子树 `%s/` 仍在扫描面内" % sub, per_root[sub] >= MIN_PER_ROOT,
           "%d 个函数 / 下限 %d" % (per_root[sub], MIN_PER_ROOT))
    ok("豁免面的分母：测试文件与测试函数都真扫到了", ex_files >= EXEMPT_MIN_FILES and ex_funcs >= EXEMPT_MIN_FUNCS,
       "测试侧 %d 文件 / %d 函数（下限 %d / %d）" % (ex_files, ex_funcs, EXEMPT_MIN_FILES, EXEMPT_MIN_FUNCS))
    ok("扫描面里的 .py 全部可解析（解析失败会被静默排除出扫描面 ⇒ 必须响亮地红）", not unparsed,
       "失败 %d 个：%s" % (len(unparsed), "；".join(unparsed[:3])))

    print("== E. 反向控制 ==")
    #: 切点真的按 300 在切（不是"全都算"或"全都不算"）：拿阈值两侧各一个样本试
    _p300 = "def f():\n" + "    x = 1\n" * (BIG - 2) + "    return x\n"
    _p299 = "def f():\n" + "    x = 1\n" * (BIG - 3) + "    return x\n"
    ok("反向控制：正好 %d 行的函数算结构病灶" % BIG, len(big_functions(_p300)) == 1,
       "实测 %d 处" % len(big_functions(_p300)))
    ok("反向控制：差一行就不算（阈值不是形同虚设）", not big_functions(_p299),
       "实测 %d 处" % len(big_functions(_p299)))
    ok("反向控制：测试文件识别有效（豁免口径可复核）",
       is_test_file("scripts/x_selftest.py") and is_test_file("ui_qt/selftest.py")
       and not is_test_file("agent/webui.py") and not is_test_file("scripts/store.py"), "")

    print("== F. 长函数里的「可自由搬走的整块」==")
    lifts, found, dup = [], 0, []
    for rel, name in sorted(REGISTERED):
        try:
            src = _read(rel)
            hits = find_owner(ast.parse(src), name)
            if hits:
                found += 1
            if len(hits) > 1:
                dup.append("%s::%s×%d" % (rel, name, len(hits)))
            for nm, span in liftable_blocks(src, rel, name):
                lifts.append("%s::%s::%s(%d 行)" % (rel, name, nm, span))
        except Exception as exc:  # noqa: BLE001
            lifts.append("%s::%s 读不出来：%s" % (rel, name, exc))
    ok("分母守卫：登记在册的长函数都真的找到了（不然 F 段会因空集全绿）",
       found == len(REGISTERED), "找到 %d / 登记 %d" % (found, len(REGISTERED)))
    ok("名字唯一：登记的函数名在各自文件里只有一个（否则查到的是另一个同名函数）",
       not dup, "重名 %d 处：%s" % (len(dup), "、".join(dup[:3])))
    ok("长函数里没有「闭包面为 0 的顶层嵌套块」（那种块随时能整体搬走，留着只是更难读）",
       not lifts, "还剩 %d 个：%s" % (len(lifts), "、".join(lifts[:4])))
    _probe = ("def outer():\n"
              "    def freeable():\n        return 1\n"
              "    x = 2\n"
              "    def bound():\n        return x\n"
              "    return freeable, bound\n")
    _got = [nm for nm, _s in liftable_blocks(_probe, "probe.py", "outer")]
    ok("反向控制：可搬性判据只认「自由名 0 个」那一个（抓了宿主局部的不认）",
       _got == ["freeable"], "实测 %s" % _got)

    print("== G. 判据的「函数切片」必须按 AST 锚定（`_srcslice`）==")
    _total, _per, _nfiles = judge_text_anchor_report()
    _excluded = len(_judge_files(exclude_self=False)) - _nfiles
    ok("豁免面就本文件一个（模式写在源码里 ⇒ 必须自排除；但豁免不许扩大）",
       _excluded == 1, "排除了 %d 个" % _excluded)
    ok("分母守卫：真扫到了足够多的判据文件", _nfiles >= 120, "扫到 %d 个" % _nfiles)
    ok("分母守卫：真扫到了足够多的 def 文本边界处", _total >= 20, "扫到 %d 处" % _total)
    ok("棘轮：用 `def` 行当文本边界的处数不超过基线（只许降）",
       _total <= JUDGE_TEXT_ANCHOR_BASE, "当前 %d / 基线 %d" % (_total, JUDGE_TEXT_ANCHOR_BASE))
    _have = {tuple(x) for x in _per}
    _miss = sorted(_have - JUDGE_KEEP)
    _stale = sorted(JUDGE_KEEP - _have)
    ok("点名表：没有新写下的「可机械改写」形态（新写就得先换成 `_srcslice`）",
       not _miss, "新增 %d 处：%s" % (len(_miss), "、".join("%s×%d" % m for m in _miss[:4])))
    ok("点名表：表里没有已失效的条目（改掉一处就要从表里删）",
       not _stale, "待删 %d 条：%s" % (len(_stale), "、".join("%s×%d" % s for s in _stale[:4])))
    _p_hit = ('_s = SRC.split("def foo(")[1][:100]\n'
              '_t = SRC[SRC.index("def bar("):]\n'
              '_u = SRC[:SRC.index("def baz(")]\n')
    _p_safe = ('_x = "def foo(" in SRC\n'
               '_y = SRC.count("def foo(")\n')
    ok("反向控制：三种「可改写形态」都能认出来",
       sum(1 for l in _p_hit.split("\n") if any(rx.search(l) for rx in _CONVERTIBLE)) == 3, "")
    ok("反向控制：`「def X」 in SRC` 与 `.count(...)` 这类存在性/计数用途不被误认",
       not any(any(rx.search(l) for rx in _CONVERTIBLE) for l in _p_safe.split("\n")), "")
    # 功能断言：helper 的返回值与"函数在文件里的位置"无关 —— 这正是它存在的理由
    _a = "def one():\n    return 1\n\n\ndef two():\n    return 2\n"
    _b = "def two():\n    return 2\n\n\ndef one():\n    return 1\n"
    ok("功能断言：`func_src` 与函数在文件里的位置无关（换个排列，结果一样）",
       _srcslice.func_src(_a, "two") == _srcslice.func_src(_b, "two") == "def two():\n    return 2", "")
    try:
        _srcslice.func_src(_a, "missing")
        _raised = False
    except LookupError:
        _raised = True
    ok("功能断言：锚点找不到时**抛**（不返回空串 —— 空串会让后续断言全部假绿）", _raised, "")

    print("== 结构体量判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
    return 0 if FAIL[0] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
