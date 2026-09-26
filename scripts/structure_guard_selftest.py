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
"""
import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
BASE_COUNT = 11

#: 点名清单：这一处的 `(相对路径, 函数名)`。清单与实测**必须完全一致** ——
#: 少一个（说明有处缩到 <300 行却没人改清单）或多一个（说明新长出一处没人拆）都判红。
#: 想在表里加条目，就得先回答"为什么不拆"。
REGISTERED = {
    ("agent/webui.py", "_make_handler"): "控制台 137 个路由方法的处理器类；按功能分模块需真机冒烟",
    ("scripts/persona_morph.py", "main"): "启动→监听→唤醒→巡检的主流程；拆分要动主循环",
    ("ui_qt/panels_custom.py", "persona_panel"): "人设面板构建；按面板拆文件待做",
    ("ui_qt/panels_custom.py", "overview_panel"): "总览面板构建",
    ("ui_qt/panels_custom.py", "check_panel"): "体检面板构建",
    ("agent/tools.py", "_builtin_tool_defs"): "内置工具声明表（长但平铺）",
    ("agent/wechat.py", "send_text_posted"): "发送链主流程（含清残留）",
    ("scripts/onestart.py", "main"): "一键启动编排",
    ("ui_qt/panels_custom.py", "memory_panel"): "记忆面板构建",
    ("agent/wechat.py", "open_chat_by_search"): "按搜索打开会话",
    ("ui_qt/panels_custom.py", "_sd_local_appendix"): "本地绘图面板附属区",
}

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

    print("== 结构体量判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
    return 0 if FAIL[0] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
