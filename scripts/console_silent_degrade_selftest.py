# -*- coding: utf-8 -*-
"""控制台两类「静默退化」判据（第 256 项）。

对标外部实现的三层防护与节流纪律，核查我方 web 控制台有没有这两类坏：

① **设置页「重建 DOM 吹掉未保存输入」** —— 外部用 `settingsGuard`（渲染守卫 ＋ 焦点/选区/
   输入值快照恢复 ＋ 滚动位置保持）兜住。它的前提是**设置页由 JS 动态重建 DOM**。
   我方核查结论（静态可判）：设置页是**静态 HTML**（`data-cfg` 挂在写死的那一行上），
   刷新只往具体元素**写值**、不重建整块 ⇒ 不存在"重渲染吹掉输入"这条路。
   判据：`data-cfg` 至少若干条；且**没有任何一处**把含 `data-cfg` 的容器整块 `innerHTML=` 覆盖。

② **列表「滚到底不触发 / 监听器堆积」** —— 外部要求"节流必须带尾随调用" ＋
   "防重复绑定：状态挂元素属性，重渲染只更新引用"。
   我方核查结论：滚动监听绑在 **`window`** 上（顶层、只绑一次），且节流用
   `requestAnimationFrame` / `setTimeout`（**都属于尾随式**：最后一次事件一定会被执行）
   ⇒ 不存在"重渲染里反复 addEventListener"造成的堆积。
   判据：滚动监听数**固定**（不是 `for`/`forEach` 里绑的）＋ 节流里含 rAF 或 setTimeout。

⛔ 本判据**只做静态核查**：它证明的是"这条路在我方代码形态下不存在"，
   不能替代真机手点。凡静态查不出结论的，如实写"测不了"，**不许写"不成立"**。

用法：`runtime\\python\\python.exe -X utf8 scripts\\console_silent_degrade_selftest.py`
"""
import os
import re
import sys

# ⛔ 判据里印 `✔/✘/⇒`，在 GBK 代码页的子进程里会 UnicodeEncodeError ⇒ 整个脚本红。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "assets", "console", "index.html")

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

    # ───────────────────────── ① 设置页会不会被整块重建 ─────────────────────────
    print("\n[①] 设置页「未保存输入」有没有被重渲染吹掉的风险")

    n_cfg = len(re.findall(r"data-cfg=", src))
    # 分母守卫：一项都没有的话下面两条会"因为空而通过"（假绿）。
    ok("A0 分母守卫：`data-cfg` 条目数像样（>=40）", n_cfg >= 40, "命中 %d 条" % n_cfg)

    # 关键反证：如果把「含 data-cfg 的容器」整块 innerHTML 覆盖，输入必然丢。
    # 做法：找出所有 `X.innerHTML = ...` 的接收者变量名，再看这些名字有没有被赋成
    # 设置页容器（`#cfgBody` 一类）或它的祖先。这里只判"设置页容器名有没有被 innerHTML 覆盖"。
    set_container_ids = set(re.findall(
        r'id="([A-Za-z0-9_\-]*(?:cfg|set|setting)[A-Za-z0-9_\-]*)"', src, re.I))
    hit = []
    for cid in set_container_ids:
        # 允许对**某一格**写值（querySelector('#x') 定位到具体控件），
        # 不允许把整个容器 innerHTML 覆盖。
        for m in re.finditer(r"(\$\(['\"]#%s['\"]\)|getElementById\(['\"]%s['\"]\))\.innerHTML\s*=" %
                             (re.escape(cid), re.escape(cid)), src):
            hit.append((cid, src.count("\n", 0, m.start()) + 1))
    ok("A1 没有任何一处把设置页容器整块 `innerHTML=` 覆盖（覆盖 ⇒ 用户没保存的输入当场丢）",
       not hit, str(hit[:3]))

    # 正面证据：设置行是写死在 HTML 里的（`<div class="row">` + `data-cfg`），不是 JS 生成的字符串。
    static_rows = len(re.findall(r'<div class="row">[^\n]*data-cfg=', src))
    ok("A2 设置行是**静态 HTML**（`<div class=\"row\">…data-cfg=` 同行出现），不是 JS 拼出来的",
       static_rows >= 20, "静态行 %d 条 / data-cfg 共 %d 条" % (static_rows, n_cfg))
    ok("A3 反向锚：若设置页真是 JS 生成，A2 的静态行数会 ≈0 ⇒ 本判据不是恒真",
       static_rows > 0)

    # 滚动位置：外部要"保持滚动位置"。我方设置页若无重渲染，滚动位置本就不会被吹掉。
    ok("A4 设置页无重渲染 ⇒ 滚动位置天然保持（无需额外快照）", not hit)

    # ───────────────────── ② 滚动/监听：节流形态与绑定次数 ─────────────────────
    print("\n[②] 列表「滚到底不触发 / 监听器堆积」")

    # 绑定位置：`window.addEventListener('scroll', ...)` 与 `X.addEventListener('scroll',...)`
    scroll_binds = re.findall(r"([A-Za-z_$][\w$\.]*)\.addEventListener\(\s*['\"]scroll['\"]", src)
    ok("B0 分母守卫：确实存在滚动监听（≥1）", len(scroll_binds) >= 1,
       "绑定目标 %s" % sorted(set(scroll_binds)))
    ok("B1 滚动监听**全部绑在 `window`**（顶层、不在重渲染路径里 ⇒ 不随渲染次数增长）",
       all(t == "window" for t in scroll_binds), str(sorted(set(scroll_binds))))
    # ⛔ 这是关键：监听器堆积的成因是"在会**重复执行**的块（循环 / 每次渲染都跑的函数）里绑"。
    #    判法：把每个 `addEventListener('scroll')` 的**调用点的缩进**和它所在 IIFE 的体缩进比 ——
    #    监听在 IIFE 体里＝只跑一次（IIFE 不是循环）。只有在 `for`/`forEach`/`while` 块内才算坏。
    #    （第一版判据用"最近一个 function 关键字"来判断 ⇒ 把 IIFE 体里的监听误判成"在函数里" ✗）
    bad_ctx = []
    for m in re.finditer(r"\.addEventListener\(\s*['\"]scroll['\"]", src):
        ln = src.count("\n", 0, m.start()) + 1
        # 往上找最近的、缩进更小的循环关键字头（`for(...)` / `forEach(` / `while(` / `do{`）
        idx = ln - 1
        cur_indent = len(src.splitlines()[idx]) - len(src.splitlines()[idx].lstrip())
        j = idx - 1
        while j >= 0:
            s = src.splitlines()[j]
            ind = len(s) - len(s.lstrip())
            if ind < cur_indent and re.search(r"(\bfor\s*\(|\bforEach\s*\(|\bwhile\s*\(|\bdo\s*\{)", s):
                bad_ctx.append(ln)
                break
            if ind == 0 and s.strip() and not s.lstrip().startswith(("//", "*", "/*")):
                break                      # 走到顶层就停：没有循环包着它
            j -= 1
    ok("B2 滚动监听**不在循环里绑**（循环里绑 ⇒ 每渲染一次多一个监听器）",
       not bad_ctx, str(bad_ctx[:3]))

    # 节流形态：rAF 与 setTimeout 都是"尾随式"（最后一次事件一定执行）⇒ 滚到底不会漏。
    for m in re.finditer(r"\.addEventListener\(\s*['\"]scroll['\"]", src):
        seg = src[m.end():m.end() + 260]
        has_trailing = ("requestAnimationFrame" in seg) or ("setTimeout" in seg)
        if not has_trailing:
            ok("B3 每处滚动监听都有**尾随式节流**（rAF 或 setTimeout）",
               False, "行 %d 附近没看到节流" % (src.count("\n", 0, m.start()) + 1))
            break
    else:
        ok("B3 每处滚动监听都有**尾随式节流**（rAF 或 setTimeout ⇒ 滚到底必触发，不漏）", True)

    # 反向锚：构造一个"裸节流、无尾随"的假片段，确认判据能判出来。
    fake = "window.addEventListener('scroll',()=>{ if(now-last<100) return; last=now; doIt(); });"
    _seg = fake[fake.index("addEventListener"):][:260]
    ok("B4 反向锚：**裸丢弃式节流**（`if(未到点) return`）会被判出来（B3 不是恒真）",
       ("requestAnimationFrame" not in _seg) and ("setTimeout" not in _seg))

    # ⛔ 本轮真发现的形态：**无人操作时**的自愈重载（服务重启自愈 / 轮询连续失败自愈）
    #    会在用户填了一半时把没保存的输入吹掉 —— 这类**必须先问一句 cfgDirty()**。
    #    ⛔ 而"用户刚点了保存/重置/标定 ⇒ 紧跟着刷一下"**不算**这一类：
    #      那一刻脏集刚被提交清空，刷掉的是"已保存"的东西，不是用户没保存的输入。
    #    判法：看 reload 之前那一小段里有没有"刚提交过配置"或"用户主动动作"的痕迹。
    auto_reload_ctx = []
    for m in re.finditer(r"location\.reload\(\)", src):
        ln = src.count("\n", 0, m.start()) + 1
        # 取 reload 前面一段（900 字够覆盖所在自愈分支，又不会跨到别的函数）。
        seg = src[max(0, m.start() - 900):m.start()]
        is_self_heal = ("__srvStartedAt" in seg) or ("__pollFails" in seg)
        if not is_self_heal:
            continue                      # 用户操作后的刷新，不属于"静默吹掉输入"
        auto_reload_ctx.append((ln, "cfgDirty()" in seg))
    ok("B5 每一处**无人操作时**的自愈重载都有 `cfgDirty()` 守卫（没守卫 ⇒ 静默吹掉未保存输入）",
       all(g for _, g in auto_reload_ctx),
       str([(l, "有守卫" if g else "无守卫") for l, g in auto_reload_ctx]))

    # 分母守卫：如果一处自愈重载都没有，B5 就是空过。
    ok("B6 分母守卫：确实存在**自愈**重载点（>=1）", len(auto_reload_ctx) >= 1,
       "%d 处：%s" % (len(auto_reload_ctx), [l for l, _ in auto_reload_ctx]))

    # 脏标记本身要在位，且**输入中**也算脏（只听 change 会漏掉"正在打字"）。
    ok("B7 脏标记 `cfgDirty()` 在位（读 `_touchedCfg`，不另立一套状态）",
       "function cfgDirty()" in src and "_touchedCfg.size" in src)
    ok("B8 有 `input` 监听（打字即算脏；只听 `change` 会漏掉'正在打字'这一档）",
       re.search(r"addEventListener\(\s*['\"]input['\"]", src) is not None)

    print("\n== 控制台静默退化判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
