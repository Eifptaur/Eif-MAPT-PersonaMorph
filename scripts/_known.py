# -*- coding: utf-8 -*-
"""判据共用件：**known 三态** —— 把"已知但未修"从"失败"里分出来，修好后**自动**转成通过。

为什么要有它：
  判据一旦因为"一条早就知道的问题"长期报红，所有人就会**习惯性忽略红灯**，整份判据就废了；
  可把那条断言**直接删掉**，问题又会消失得无影无踪（下次没人知道它存在过）。
  ⇒ 第三种结果：**不计失败、但必须打印**，而且**一旦断言真的过了就自动升级成通过**
    （明细里打一行「已修复」）⇒ 问题既不会淹掉别的红灯，也不会被忘掉。

用法（判据自己的 `check` / `ck` / `ok` 传进来，报告口径与其它断言一致）：
    from _known import known, report_known
    known(check, "检验·某条历史回归", "标记串" in SRC,
          "标记串已随实现演进失效，待人工复核后重定位或删掉这条检验")
    …
    report_known(print)      # 收尾：把"已知未修"单列一档打出来

口径：
  · `cond` 为真 ⇒ 走**正常通过**（明细写明「已修复，可把这条登记删掉」）；
  · `cond` 为假 ⇒ 记一条 known —— **不调用判据自己的报告函数** ⇒ **不进失败计数**；
  · `note` **必填**：不写清"为什么它现在算已知"，这一档就等于把红灯藏起来。
  ⛔ 登记表与被观测的集合**必须对账**（见 `fullcheck` 那条"注意项全部已登记"）——
     否则新冒出来的问题会悄悄溜进这一档。
"""
from __future__ import annotations

KNOWN: list = [] # [(名字, 备注)]


def known(ck, name, cond, note):
    """三态登记。返回 `cond`（便于调用方判断当前是"通过"还是"已知未修"）。"""
    if cond:
        ck(name + "（已修复）", True, "已修复 —— 可以把这条 known 登记删掉")
        return True
    KNOWN.append((str(name), str(note)))
    print("  KNOWN " + str(name) + "   [" + str(note) + "]")
    return False


def report_known(emit=print) -> int:
    """收尾打印。返回 known 条数（0 条也打一行，免得"这一档不见了"没人发现）。"""
    emit("---- 已知未修（不计失败）：%d 条 ----" % len(KNOWN))
    for _n, _note in KNOWN:
        emit("  · " + _n + "   → " + _note)
    return len(KNOWN)
