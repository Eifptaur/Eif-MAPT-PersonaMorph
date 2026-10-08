# -*- coding: utf-8 -*-
"""判据共用件：**分母守卫** —— 先证明"这条用例真的走到了要拦的那一步"，再断言"它被拦住了"。

要防的是一种**会静默变绿**的判据：安全类判据里最常见的形式是**负向断言**
（"一次真鼠标都没走"、"一个违规都没放过"、"落点校验一次都没通过"），
而这类断言在**探针自己失效**时同样成立 —— 被测入口改了名、打桩没打到真源上、
场景压根没跑起来，结果都是"0 次"：判据看着是绿的，其实一个字都没验到。

⇒ 口径（两条都过才算通过）：
    ① 这条用例确实构造出了要拦的那个东西（调用真的发生了 / 样本存在且被扫到）；
    ② 它被拦住了。
  ⛔ 凡"断言 0 次"的地方，前面**必须先断言分母 > 0**。

用法（判据自己的 `ok(name, cond, detail)` 传进来，报告口径与其它断言一致）：
    from _probe import assert_probe_hit
    assert_probe_hit(ok, "B0 探针有效：这次真的发出了投递尝试", len(p.calls))
    ok("B1 拒绝执行", r[0] is False, ...)      # 有了上面那条，这一条才敢信

口径说明：
  · `hits` 是"**探针命中数**"（调用次数 / 命中样本数 / 扫到的路径数），**不是**"违规数"；
  · 默认要求 ≥1；要更严就传 `min_expected`（例如"三条写通路都要活"传 3）；
  · 分母不足时**打印「探针失效」并计失败**，绝不静默放过。
"""
from __future__ import annotations


def assert_probe_hit(ok, name, hits, min_expected: int = 1):
    """分母守卫：断言探针确实命中了要拦的目标。返回是否达标（便于调用方决定后续怎么报）。"""
    try:
        got = int(hits)
    except Exception:  # noqa: BLE001
        got = -1 # 数不出来（None / 不可转）一律当"分母不足"，不许当成 0 次放过
    good = got >= int(min_expected)
    detail = "分母 %d（要求 ≥%d）" % (got, min_expected)
    if not good:
        detail += " ⇒ 探针失效：这条用例没走到要拦的那一步，后面的「0 次」不算数，先修用例"
    ok(name, good, detail)
    return good
