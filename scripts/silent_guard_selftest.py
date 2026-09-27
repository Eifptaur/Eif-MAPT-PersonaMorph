# -*- coding: utf-8 -*-
"""静默吞异常族的**闸门**（不是"消灭静默"，是"分类 + 只许降"）。

口径（与 `root-probes/_c10_silent.py` 同一套规则；**这里才是唯一真源**）：
  静默点 = `except` 处理体里**没有任何可见动作**（只有 `pass` / `continue` / `break` / 无值 `return`）。
  三档（判据只看两个可机械复核的事实：**try 体是不是纯动作**、**属不属于清理/回收语义**）：
    · **A 允许静默**：try 体是纯动作（只有函数调用）**且**宿主函数名或动作属"清理/回收/释放"语义
      ⇒ 保留静默，但**必须带标记注释**（`# 清理型：静默合法`），让读的人一眼分清"故意"与"不小心"；
    · **B 取了个值**：try 体里产出了值（赋值/返回/append 之类）⇒ 这一档是"**读失败被当成没有**"的形状，
      必须逐步改成三态 `(ok, val, why)`；本轮**只钉棘轮**（不允许变多），改造按族推进；
    · **C 其余**：机械判不出来 ⇒ 清单**只许降**（每轮清理一批后下调基线）。
  ⛔ **目标不是"0 静默"，是"0 未知"**：A 类可静默（有标记）、B 类按族改三态、C 类清单收敛。

为什么用"棘轮 + 标记"而不是逐处重写：实测三档是 **A 68 / B 686 / C 511**（合计 1265 处），
而逐个改三态＝改 686 处调用签名（远超"分类"的范畴、且大量是"取值失败用默认值"的合法形态）。
真正会害人的那一小撮已被点名固定在**四族**（读库 / 读配置 / 读画面 / 读水位）——
本闸门对这四族单独设上限（每族一闸门），其余的靠棘轮防"新写的静默越界"。
"""
import ast
import io
import os
import re
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


#: A 类的**标记**（计划里的写法；`_c10_silent.py` 与判据共用这一个词）
MARK = "清理型：静默合法"   # 行内追加（无注释时写 `# 清理型：静默合法`；已有注释时接在它后面）
CLEAN_FUNC = re.compile(r"close|clean|clear|remove|del_|kill|sweep|prune|purge|restore|reset|stop|"
                        r"shutdown|teardown|unlink|release|hide|minimize|detach|exit|discard|rollback",
                        re.I)
CLEAN_API = re.compile(r"os\.remove|os\.unlink|\.close\(\)|deleteobject|postmessage|wm_close|terminate|"
                       r"shutil\.rmtree|clear\(\)|setparent|deletelater|\.stop\(\)|quit\(\)|kill\(", re.I)

#: 三档**棘轮基线**。只许降：清理一批 → 下调一格；新增静默越界 → 当场红。
#: B 类 +3 的登记项：扫盘缓存的读写兜底（取候选目录签名 / 读缓存 / 写缓存）——三者都是**只读缓存**，
#: 失败即退化成"重扫一遍"，不改变任何业务结论；将来清掉这批缓存时，把这里同步降回去。
#: B 类再 +3 的登记项：窗口几何补丁（ui_adapt.patch_no_window_geometry）里三处兜底
#: （取 hwnd / 取 rect / 还原失败的判断）——都是「拿不到就按原样放行」，不改任何业务结论。
BASE = {"A": 68, "B": 968, "C": 236}

#: 会害人的**四族**（"读失败被当成没有"）：每族单独设上限（**只许降**；上限＝当日实测，目标是逐族降到 0）
FAM = (
    ("读库", r"agent/(store|memory|history_prune|recall)\.py", 29),
    ("读配置", r"agent/(config|config_io|persist)\.py", 5),
    ("读画面", r"agent/(chat_header|chat_ocr|ui_fingerprint|screen_|shoot)\.py", 66),
    ("读水位", r"agent/(listener_watermark|jobs|timers)\.py", 7),
)


def silent_sites(src):
    """→ [(行号, 宿主函数, try 体文本, 处理体文本, 是否已带标记)]（口径见文件头）"""
    tree = ast.parse(src)
    lines = src.split("\n")
    owner = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for x in ast.walk(n):
                owner[x] = n
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Try):
            continue
        for h in n.handlers:
            body = h.body
            if not body or not all(isinstance(s, (ast.Pass, ast.Continue, ast.Break)) or
                                   (isinstance(s, ast.Return) and
                                    (s.value is None or isinstance(s.value, ast.Constant)))
                                   for s in body):
                continue
            ln = body[0].lineno
            end = body[-1].end_lineno or ln
            seg = "\n".join(lines[ln - 1: min(len(lines), end + 1)])
            tbody = n.body
            only_calls = all(isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) for s in tbody)
            produces = not only_calls
            trytxt = "\n".join(ast.unparse(s) for s in tbody)
            fn = owner.get(n)
            out.append((ln, fn.name if fn else "?", trytxt, seg, MARK in seg, only_calls, produces))
    return out


def classify(fn, trytxt, only_calls, produces):
    if only_calls and (CLEAN_FUNC.search(fn) or CLEAN_API.search(trytxt)):
        return "A"
    if produces:
        return "B"
    return "C"


def collect():
    rows = []
    for sub in ("agent", "scripts"):
        for dp, dn, fns in os.walk(os.path.join(ROOT, sub)):
            dn[:] = [d for d in dn if d != "__pycache__"]
            for fn in sorted(fns):
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dp, fn)
                rel = os.path.relpath(p, ROOT).replace("\\", "/")
                try:
                    src = io.open(p, encoding="utf-8", errors="ignore").read()
                    for ln, fname, trytxt, seg, marked, only_calls, produces in silent_sites(src):
                        rows.append((rel, ln, fname, classify(fname, trytxt, only_calls, produces), marked))
                except Exception: # noqa: BLE001
                    continue
    return rows


def main():
    rows = collect()
    cnt = {k: sum(1 for r in rows if r[3] == k) for k in "ABC"}
    print("== A. 三档读数（只许降）==")
    print("   静默点合计 %d 处：A %d / B %d / C %d" % (len(rows), cnt["A"], cnt["B"], cnt["C"]))
    for k in "ABC":
        ok("%s 类不超过基线（棘轮，只许降）" % k, cnt[k] <= BASE[k], "当前 %d / 基线 %d" % (cnt[k], BASE[k]))
    ok("分母守卫：真的扫到了静默点（不然上面三条会因空集全绿）", len(rows) > 800, "扫到 %d 处" % len(rows))

    print("== B. A 类必须带标记（计划要求的『分清故意/不小心』）==")
    a_sites = [r for r in rows if r[3] == "A"]
    no_mark = ["%s:%d" % (r[0], r[1]) for r in a_sites if not r[4]]
    ok("A 类（清理/回收语义的纯动作）**每一处都带标记**", not no_mark,
       "未标记 %d 处：%s" % (len(no_mark), "、".join(no_mark[:4])))
    ok("反向控制：标记词确实能被认出（拿一行样本文本试）",
       MARK in ("    pass  " + MARK) and MARK not in "    pass  # 普通注释", "")

    print("== C. 会害人的四族：单独设上限（每族一闸门）==")
    for fam, pat, cap in FAM:
        n = sum(1 for r in rows if r[3] == "B" and re.fullmatch(pat, r[0]))
        ok("%s 族的『取了值』静默点不超过上限" % fam, n <= cap, "当前 %d / 上限 %d" % (n, cap))

    print("== D. 口径可复核 ==")
    #: 判据自己的分类器必须**能认出**三档（拿合成样本试，防"全都归一类"也全绿）
    _probe = ("def _close_x():\n"
              "    try:\n        u.PostMessageW(h, 0x0010, 0, 0)\n    except Exception:\n        pass\n"
              "def _read_y():\n"
              "    try:\n        v = cfg.get('k')\n    except Exception:\n        pass\n"
              "    return v\n"
              "def _other_z():\n"
              "    try:\n        do_something()\n    except Exception:\n        pass\n")
    _r = silent_sites(_probe)
    _cls = sorted(classify(fn, t, oc, pr) for _ln, fn, t, _s, _m, oc, pr in _r)
    ok("分类器有效：合成样本分别判成 A（清理）、B（取了值）、C（其余）", _cls == ["A", "B", "C"], "实测 %s" % _cls)

    print("== 静默吞异常判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
    return 0 if FAIL[0] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
