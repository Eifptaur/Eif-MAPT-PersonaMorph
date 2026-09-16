# -*- coding: utf-8 -*-
"""控制台「死按钮」判据（2026-09-17 立，起因＝用户报障）。

用户原话：「我要勾选删除记录的时候，**删除选中勾选的日志，它是没有亮起来，又按不了，也删不掉**」。
真因：`agent/console_html.py` 里 `<button id="sessSelDel" class="danger" disabled>` **出生就带 `disabled`**，
而**全文件没有任何一行设置过 `sessSelDel.disabled`** —— 勾选谁也不亮，按钮一辈子按不了、功能等于不存在。

判据（通用，不针对某一个按钮）：**凡是 HTML 里出生带 `disabled` 的按钮，必须在 JS 里有一处把它点亮**
（该 id 出现在源码任意位置时，前后 ±300 字内要看得到 `.disabled` 赋值）。
这条能自动抓住"加了按钮、忘了接启用逻辑"这一类，不必等用户再踩。

用法：`runtime\\python\\python.exe -X utf8 scripts\\console_deadbutton_selftest.py`
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "agent", "console_html.py")

PASS = 0
FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  \u2714 %s %s" % (name, extra))
    else:
        FAIL += 1
        print("  \u2718 %s %s" % (name, extra))


def main():
    src = open(SRC, encoding="utf-8").read()
    print("\n[一] 出生带 disabled 的按钮，必须有一处点亮它")
    born = []
    for m in re.finditer(r"<button\b[^>]*>", src, re.I):
        tag = m.group(0)
        if not re.search(r"\bdisabled\b", tag):
            continue
        idm = re.search(r'\bid="([^"]+)"', tag)
        if idm:
            born.append(idm.group(1))
    ok("扫到出生即禁用的按钮", len(born) > 0, "共 %d 个：%s" % (len(born), ", ".join(born)))

    dead = []
    for bid in born:
        # 候选变量名 = 该 id 本身 ＋ 任何"从该 id 取到的局部别名"（`const b = $('x')` / `ub = document.getElementById('x')`）
        cands = {bid}
        for am in re.finditer(
                r"([A-Za-z_$][\w$]*)\s*=\s*(?:document\.(?:getElementById|querySelector)|[$])\s*\(\s*['\"]"
                + re.escape(bid) + r"['\"]", src):
            cands.add(am.group(1))
        wired = False
        for c in cands:
            if re.search(r"\b" + re.escape(c) + r"\s*\.\s*disabled\s*=", src):
                wired = True
                break
        if not wired:
            # 兜底：该 id 出现在源码任意位置时，前后 ±600 字内看得到 `.disabled` 赋值也算接上
            for om in re.finditer(re.escape(bid), src):
                if re.search(r"\.\s*disabled\s*=", src[max(0, om.start() - 600): om.end() + 600]):
                    wired = True
                    break
        if not wired:
            dead.append(bid)
        ok("「%s」有启用逻辑" % bid, wired, "（认到的变量名：%s）" % ",".join(sorted(cands)))
    ok("没有死按钮", not dead, ("死按钮：%s" % ", ".join(dead)) if dead else "0 个")

    print("\n[二] 运行明细：勾选→点亮→重绘复位 三件齐全")
    ok("定义了 syncSessSel()", "function syncSessSel(" in src)
    ok("勾选框事件委托（列表重绘不用重绑）", "__sessSelWired" in src and "sessSel" in src)
    ok("重绘后会复位", "syncSessSel();" in src)
    ok("删除选中仍绑着 onclick", "$('sessSelDel').onclick" in src)
    ok("删除接口存在（/api/sessions/delete）", "'/api/sessions/delete'" in src)

    print("\n[三] 记忆页：勾选→点亮→重绘复位")
    ok("memPick 的 onchange 会点亮按钮", "memClearSel" in src and "b.disabled = !any" in src)
    ok("记忆页重绘后复位", "_mb.disabled = true" in src)

    print("\n%d 通过 / %d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
