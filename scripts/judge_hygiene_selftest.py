# -*- coding: utf-8 -*-
"""判据卫生：**所有判据脚本里，断言的"条件位"不许是字符串字面量**（＋名字位不许是布尔）。

跑法： runtime\\python\\python.exe scripts\\judge_hygiene_selftest.py   退出码 0=全过 / 1=有违规

为什么要这条（2026-09-21，第四轮审计 **V-R4-4（P1）**）：
  `vuln_fix_selftest.py` 里有 4 条调用把 `ok(name, cond, detail)` 的前两个实参写反了 ——
  条件位塞了一段**非空描述文本**（永远为真）⇒ 这 4 条**恒真**，
  而它们守的正是"口令文件并发首建不许互相覆盖"那条修复：
  **把修复整段退回旧实现，判据照样 94/0 全绿**（判据自己还打印着 `OK False` 这种没有人话的行）。
  ⇒ 这类错误光靠"跑一遍看绿不绿"发现不了（它永远绿），必须**静态**扫出来。

判定规则（先看该文件自己的 helper 签名，认不出就跳过，绝不猜）：
  · 名字位（`name/title/label/desc/msg`…）拿到**布尔字面量** ⇒ 违规（典型长相：`ok(True, "…")`）。
  · 条件位（头两个参数里剩下的那个）拿到**字符串字面量** ⇒ 违规（恒真）。
"""
from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.join(ROOT, "scripts")
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


_NAME_WORDS = {"name", "title", "label", "desc", "what", "case", "why", "msg"}
_SELF = os.path.basename(os.path.abspath(__file__))
#: 脆断言（`"带空白的整段源码" in SRC_xxx`）的**基线**：2026-09-21 实测 383（审计口径 379+）。
#   **只许降不许升** —— 把旧的换成 `scripts/_srcmatch.py::has()` 就会降；新写判据别再加这类写法。
BRITTLE_BASELINE = 383


def _helper_styles(tree: ast.AST) -> dict:
    """本文件里 `ok/ck/check` 这类断言的 (名字位下标, 条件位下标)。认不出就不给。"""
    out = {}
    for node in tree.body if hasattr(tree, "body") else []:
        if not isinstance(node, ast.FunctionDef) or len(node.args.args) < 2:
            continue
        if node.name not in ("ok", "ck", "check", "_ok", "assert_ok", "ck_ok"):
            continue
        params = [a.arg for a in node.args.args]
        named = [i for i, p in enumerate(params[:3]) if p.lower() in _NAME_WORDS]
        if named:
            ni = named[0]
            ci = 1 - ni if ni in (0, 1) else None
        else:
            ni, ci = None, None
        if ci is None and ni is None:
            continue
        out[node.name] = (ni, ci)
    return out


def _is_str_const(n) -> bool:
    return isinstance(n, ast.Constant) and isinstance(n.value, str)


def _is_bool_const(n) -> bool:
    return isinstance(n, ast.Constant) and isinstance(n.value, bool)


def main():
    files = sorted(f for f in os.listdir(HERE)
                   if "selftest" in f and f.endswith(".py") and f != _SELF)
    scanned, skipped = 0, []
    bad = []
    for fn in files:
        p = os.path.join(HERE, fn)
        try:
            src = open(p, encoding="utf-8").read()
            tree = ast.parse(src)
        except Exception as e:
            bad.append("%s : 读不了/解析不了（%s）" % (fn, str(e)[:60]))
            continue
        styles = _helper_styles(tree)
        if not styles:
            skipped.append(fn)
            continue
        scanned += 1
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            st = styles.get(node.func.id)
            if not st:
                continue
            ni, ci = st
            args = node.args
            if ci is not None and len(args) > ci and _is_str_const(args[ci]):
                bad.append("%s:%d %s() 的条件位是**字符串字面量**（恒真）⇒ 实参写反了"
                           % (fn, getattr(node, "lineno", 0), node.func.id))
            if ni is not None and len(args) > ni and _is_bool_const(args[ni]):
                bad.append("%s:%d %s() 的名字位是**布尔字面量**（打印出来是 True/False，没有人话）⇒ 实参写反了"
                           % (fn, getattr(node, "lineno", 0), node.func.id))
    ok("① 扫到了判据脚本（≥100 个才叫扫到了；认不出 helper 签名的跳过并列出）",
       scanned >= 100, "有签名的 %d 个 / 共 %d 个；跳过 %d 个" % (scanned, len(files), len(skipped)))
    ok("② **零**违规：条件位不许是字符串、名字位不许是布尔（V-R4-4 这一类）",
       not bad, bad[:8] if bad else "")
    if skipped:
        print("   （没认出击签名、按规矩跳过的：%s）" % ("、".join(skipped[:12]) + ("…" if len(skipped) > 12 else "")))

    # ③ 负例：把两种错法各造成一段源码，同一个判定器必须都能抓出来
    _old = ("def ok(name, cond, detail=\"\"):\n"
            "    pass\n"
            "ok(1 == 1 and 2 == 2, \"口令文件已存在 ⇒ 读回它\", \"got=X\")\n"
            "ok(True, \"这条也写反了\")\n")
    _tree = ast.parse(_old)
    _styles = _helper_styles(_tree)
    _hits = []
    for node in ast.walk(_tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _styles:
            _ni, _ci = _styles[node.func.id]
            if _ci is not None and len(node.args) > _ci and _is_str_const(node.args[_ci]):
                _hits.append("条件位是字符串")
            if _ni is not None and len(node.args) > _ni and _is_bool_const(node.args[_ni]):
                _hits.append("名字位是布尔")
    ok("③ 负例：写反的两条（条件位放描述文本 / 名字位放 True）都被同一判定器抓出",
       len(_hits) == 3 and "条件位是字符串" in _hits and "名字位是布尔" in _hits, _hits)

    # ── ④ 源码文本的**脆断言**普查（第四轮审计 V-R4-13 第三条）────────────────────────
    #   定义：`"带空白的整段源码" in SRC_xxx` —— 源码一改缩进/换行就红（行为没变）。
    #   审计实测 80/129 个判据文件都有这类写法（前 26 个文件共 379 条）。
    #   处置：①新写判据**优先用 `scripts/_srcmatch.py::has()`**（空白容忍、不计数）；
    #        ②这个数**只许降不许升**（把旧的换成 `_srcmatch.has` 就会降）。
    import ast as _ast
    import re as _re3
    _SRCISH = _re3.compile(r"src|web|html|body|code|h$", _re3.I)

    def _brittle_in(src_text):
        """返回这份判据里"脆断言"的条数与位置（只看 `"…" in <src 变量>`）。"""
        hits = []
        try:
            tree = _ast.parse(src_text)
        except Exception:
            return hits
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Compare):
                continue
            if not any(isinstance(op, (_ast.In, _ast.NotIn)) for op in node.ops):
                continue
            left = node.left
            if not (isinstance(left, _ast.Constant) and isinstance(left.value, str)):
                continue
            if not _re3.search(r"\s", left.value):          # 单token 的针（没有空白）不算脆
                continue
            names = [s.id for c in node.comparators for s in _ast.walk(c) if isinstance(s, _ast.Name)]
            if any(_SRCISH.search(n) for n in names):
                hits.append("%s:%d" % ("", getattr(node, "lineno", 0)))
        return hits

    _cens = {}
    for _fn in files:
        try:
            _txt = open(os.path.join(HERE, _fn), encoding="utf-8").read()
        except Exception:
            continue
        _h = _brittle_in(_txt)
        if _h:
            _cens[_fn] = len(_h)
    _total = sum(_cens.values())
    ok("④ 普查跑得起来且**只许降不许升**（当前 %d ≤ 基线 %d）" % (_total, BRITTLE_BASELINE),
       _total <= BRITTLE_BASELINE, "超了 %d 条：%s" % (_total - BRITTLE_BASELINE,
                                                     sorted(_cens.items(), key=lambda x: -x[1])[:4]))
    _top = sorted(_cens.items(), key=lambda x: -x[1])[:5]
    print("   （最脆的五个文件：%s —— 改这几个文件时优先把新写/要动的断言换成 `_srcmatch.has()`）"
          % "、".join("%s %d" % (k, v) for k, v in _top))
    # 空白容忍的 helper 必须在位，并且**真的**解决"改缩进就红"
    try:
        sys.path.insert(0, HERE)
        import _srcmatch as _sm2
        _has_ok = _sm2.has("def f(a, b):\n    return a + b\n",
                           "def f(a, b):", "return a + b")
        _old_style = ("def f(a, b):\n        return a + b\n"          # 换个缩进
                      .find("def f(a, b):\n    return a + b\n") >= 0)
    except Exception as _e4:
        _has_ok, _old_style = False, True
        print("   （_srcmatch 导入失败：%s）" % str(_e4)[:60])
    ok("④ `_srcmatch.has()` 在位且**空白容忍**", _has_ok is True)
    ok("④ 反例锚：老写法（整段带缩进一起比）**换个缩进就找不到** ⇒ 这就是「脆」的来历",
       _old_style is False)

    # ── ⑤ 判据不许碰**用户正在跑的服务**（2026-09-21 真机事故后立的规矩）───────────────
    #   现场：本机 7860 上跑着用户的本地生图服务，我跑了一次全量套件（`image_gen_selftest` 的
    #   后端选择段会调真 `pick_backend()`）⇒ 它内部 `sd_local.status()` 探不到（服务正忙着加载
    #   CUDA 模型）⇒ 调 `ensure_running()` ⇒ **把用户正在用的实例杀掉重启**（pidfile 4848 → 40232，
    #   03:40:59 实测）。注意：判据跑在 `%TEMP%` 副本里也一样 —— `server_alive()` 探的是**机器级端口**。
    #   ⇒ 规矩：判据里凡出现 `pick_backend(` / `ensure_running(`，必须同时对 `sd_local` 打桩。
    _offenders = []
    for _fn in files:
        try:
            _t = open(os.path.join(HERE, _fn), encoding="utf-8").read()
        except Exception:
            continue
        if ("pick_backend(" in _t) or ("ensure_running(" in _t):
            if ("ensure_running = lambda" not in _t) and ("ensure_running = _orig" not in _t):
                _offenders.append(_fn)
    ok("⑤ 判据里用到 `pick_backend()`/`ensure_running()` 的，都必须打桩（否则会杀用户正在跑的服务）",
       not _offenders, _offenders[:4])
    ok("⑤ 反例锚：老写法（只关在线后端、不打桩 `ensure_running`）确实会被这条扫出来",
       ("pick_backend(" in "_x = IG.pick_backend()") and ("ensure_running = lambda" not in "_x = IG.pick_backend()"))

    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
