# -*- coding: utf-8 -*-
"""计划落点核查：PLAN 里「代码怎么写」提到的**文件 / 函数**，现在还存不存在？

## 为什么需要它

PLAN 里的条目是**分批写的**，早期写的"代码怎么写"会指向当时存在、后来改名/删除的东西。
我按着错落点开工 ⇒ 要么做错地方，要么白做一轮。

实例（本判据第一次跑就抓到的两个）：
  · 第 143 项写「`agent/chat_store.py` 的 FTS5 查询口」⇒ **该文件不存在**（`agent/` 里 FTS5 零命中）；
  · 第 164 项写「整行模板当兜底」⇒ `chat_header.py` 里**没有任何整行模板实现**。

## 它做什么（只读）

扫 `报告/PLAN.md` 里**未完成**的条目，把 `· **代码怎么写**` 那一行里的
`` `路径` `` / `` `函数()` `` 抠出来，逐个判断在当前工作区是否存在，输出**对不上的清单**。
⛔ 只报事实（存在/不存在），**不改 PLAN**（要不要改由人决定）。

用法：runtime\\python\\python.exe scripts/plan_anchor_check.py
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN = os.path.join(ROOT, "报告", "PLAN.md")

#: 只看这些形态的引用：带扩展名的路径、`xxx.py`、`yyy()` 函数名
_RE_TICK = re.compile(r"`([^`]+)`")
_RE_FILE = re.compile(r"^[A-Za-z0-9_./\\\-]+\.(py|cs|json|md|html|ps1|cmd)$")
_RE_FUNC = re.compile(r"^([a-z_][A-Za-z0-9_]{2,})\(\)$")


#: **运行期路径**：这些目录里的文件是跑起来才生成的 ⇒ 检查它们存在与否没有意义（不算对不上）
_RUNTIME_PREFIX = ("data/", "logs/", "wechatauto_logs/", "runtime/", "offline/")

#: `（新建）` 标记：条目明确写着这个落点**还没写出来、要新建**
#   ⇒ 它"不存在"是**预期的**，不是"锚失效"。标了这一句的那一行整行跳过检查。
#   ⛔ 反过来也要防：标了"新建"却**已经存在** ⇒ 那是标记过期（要么删标记，要么条目该重看）
#     —— 这正是"标记不许当挡箭牌"，所以下面把这种情况单独列出来。
_NEW_MARK = "（**新建**）"
_NEW_MARK_ALT = "（新建）"


def _is_runtime(rel: str) -> bool:
    r = rel.replace("\\", "/").lstrip("./")
    return r.startswith(_RUNTIME_PREFIX)


def _exists_path(rel: str) -> bool:
    for cand in (rel, rel.replace("\\", "/"), os.path.join(ROOT, rel)):
        if os.path.exists(os.path.join(ROOT, cand)) if not os.path.isabs(cand) else os.path.exists(cand):
            return True
    return False


def _find_symbol_in(sym: str) -> str:
    """在 agent/ui_qt 里找 `def sym(` / `class sym` / `def sym(` 出现在哪个文件（找不到返回空）。"""
    for d in ("agent", "ui_qt", "scripts"):
        p = os.path.join(ROOT, d)
        if not os.path.isdir(p):
            continue
        for f in os.listdir(p):
            if not f.endswith(".py"):
                continue
            try:
                t = io.open(os.path.join(p, f), encoding="utf-8", errors="ignore").read()
            except Exception:  # noqa: BLE001
                continue
            if ("def %s(" % sym) in t or ("class %s" % sym) in t:
                return os.path.join(d, f)
    return ""


def main() -> int:
    lines = io.open(PLAN, encoding="utf-8").read().splitlines()
    cur, items = None, {}
    for ln in lines:
        m = re.match(r"^\s*- \[ \] (\d+)\.\s*(.*)$", ln)
        if m:
            cur = int(m.group(1))
            items[cur] = {"title": m.group(2)[:70], "howto": "", "who": ""}
            continue
        if cur is None:
            continue
        if "代码怎么写" in ln:
            items[cur]["howto"] = ln
    miss_file, miss_sym, planned_new, stale_mark = [], [], [], []
    for n, it in sorted(items.items()):
        _how = it["howto"]
        if (_NEW_MARK in _how) or (_NEW_MARK_ALT in _how):
            # 这一行声明了"落点要新建" ⇒ 不存在是预期的（但仍要核"标记没过期"）
            for raw in _RE_TICK.findall(_how):
                ref = raw.strip()
                if _RE_FILE.match(ref):
                    planned_new.append((n, ref))
                    if (not _is_runtime(ref)) and _exists_path(ref):
                        stale_mark.append((n, ref))
            continue
        for raw in _RE_TICK.findall(_how):
            ref = raw.strip()
            if _RE_FILE.match(ref):
                if _is_runtime(ref):
                    continue                      # 运行期生成的文件，跳过
                if not _exists_path(ref):
                    miss_file.append((n, ref))
                continue
            fm = _RE_FUNC.match(ref)
            if fm:
                where = _find_symbol_in(fm.group(1))
                if not where:
                    miss_sym.append((n, ref))
    print("=== 有「代码怎么写」的未完成项：%d 条 ===" % sum(1 for v in items.values() if v["howto"]))
    print("\n=== ❌ 指向**不存在的文件**（按它会做错地方）===")
    for n, r in miss_file:
        print("  [%4d] %-38s  ← %s" % (n, r, items[n]["title"][:44]))
    print("\n=== ❌ 指向**找不到的函数** ===")
    for n, r in miss_sym:
        print("  [%4d] %-24s  ← %s" % (n, r, items[n]["title"][:44]))
    print("\n=== ✔ 标了「（新建）」的落点（不参与上面的「对不上」，但要核标记没过期）===")
    for n, r in planned_new:
        print("  [%4d] %-38s  ← %s" % (n, r, items[n]["title"][:44]))
    if stale_mark:
        print("\n=== ⚠ 标记过期：标了「（新建）」可文件**已经存在**（要么删标记，要么条目该重看）===")
        for n, r in stale_mark:
            print("  [%4d] %-38s" % (n, r))
    print("\n汇总：文件对不上 %d 处 · 函数对不上 %d 处 · 待新建 %d 处 · 标记过期 %d 处"
          % (len(miss_file), len(miss_sym), len(planned_new), len(stale_mark)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
