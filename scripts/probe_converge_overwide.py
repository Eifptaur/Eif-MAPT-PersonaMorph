# -*- coding: utf-8 -*-
"""probe_converge_overwide.py —— `_converge_by_highlight` 会不会**收敛过宽**？

⛔ 为什么要单开这支：
  `_converge_by_highlight` 返回 `[]` 的语义是「**没有 rival 在抢**」——
  一旦它把"其实是别人"的绿底行误判成"目标"，rival 的告警就被**丢掉**了，
  下游 `_row_time_conflict` 于是给 `decided=True`「确定是它」⇒ **可能发错会话**。
  也就是说：**收敛过宽 = 放宽方向 = 安全问题**（不是可用性问题）。

  `_hit()` 同时用了两条匹配：
    · `matches_strict()` —— 归一化后**完全相等**（项目里的"授权档"口径）
    · `matches()`        —— **互相包含**即可（为容忍"名字＋预览＋时间"整行文本）
  第二条很宽。**绿底行的名字**本该是短短一个名字区，用"互相包含"合适吗？

本探针只做一件事：**枚举危险场景，把当前行为如实打印出来**，不下结论。
判定口径（每条都先写"应该怎样"，再看"实际怎样"）：
  正例（应收敛）：绿底行＝目标 ⇒ `[]`
  反例（**不许**收敛）：绿底行只是"**包含**目标名"或"被目标名包含"但**不是同一个会话**
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import agent.wechat as W  # noqa: E402
from agent import chat_ocr as CO  # noqa: E402

A_ID = "10000000001@chatroom"
B_ID = "10000000002@chatroom"
_n = 0
_bad = 0


def ck(name, cond, extra=""):
    global _n, _bad
    _n += 1
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name, ("  [%s]" % extra) if extra else ""))
    if not cond:
        _bad += 1
    return bool(cond)


def _conv(name, rivals, hl_name):
    """直接调 `_converge_by_highlight`，把 `CO.highlight` 桩成指定绿底行。"""
    ad = W.WeChatAdapter.__new__(W.WeChatAdapter)
    _keep = CO.highlight
    try:
        CO.highlight = lambda img: ({"y_abs": 100, "score": 0.97, "name": hl_name}
                                    if hl_name is not None else None)
        return ad._converge_by_highlight(object(), "group:" + A_ID, name, rivals)
    finally:
        CO.highlight = _keep


def main():
    print("=" * 78)
    print("`_converge_by_highlight` 收敛宽度探针（返回 [] ＝「没有 rival 在抢」）")
    print("=" * 78)

    _riv_kc = ["KC（group:%s）" % B_ID]
    _riv_tq = ["测试群（group:%s）" % B_ID]

    print("\n── A. 正例（应当收敛，作为对照，证明探针本身有效）──")
    print("  A1 绿底行＝目标「测试」，rival＝KC")
    _a1 = _conv("测试", _riv_kc, "测试")
    print("     ⇒ %r" % (_a1,))
    ck("A1 绿底行确实是目标 ⇒ 返回 []（无竞争）", _a1 == [], _a1)

    print("  A2 绿底行＝某个 rival（KC），目标＝测试")
    _a2 = _conv("测试", _riv_kc, "KC")
    print("     ⇒ %r" % (_a2,))
    ck("A2 绿底行是 rival ⇒ 收敛到只剩它（仍非空 ⇒ 不放宽）",
       _a2 is not None and len(_a2) == 1 and "KC" in str(_a2[0]), _a2)

    print("\n── B. ★反例（**不许**收敛成 []：绿底行只是「包含」目标名而已）──")
    print("  B1 目标「测试」，绿底行读到「测试机器人」（另一个会话，只是包含目标名）")
    _b1 = _conv("测试", _riv_kc, "测试机器人")
    print("     ⇒ %r" % (_b1,))
    ck("B1 「测试机器人」≠「测试」⇒ **不许**返回 []（那会把 rival 告警丢掉）",
       _b1 != [], _b1)

    print("  B2 目标「测试」，绿底行读到「测试2」（撞名群）")
    _b2 = _conv("测试", _riv_kc, "测试2")
    print("     ⇒ %r" % (_b2,))
    ck("B2 撞名群「测试2」≠「测试」⇒ **不许**返回 []", _b2 != [], _b2)

    print("  B3 目标单字「E」，绿底行读到「Editor」（`matches` 走 len(b)==1 的 startswith 分支）")
    _b3 = _conv("E", _riv_kc, "Editor")
    print("     ⇒ %r" % (_b3,))
    ck("B3 单字目标「E」+ 绿底行「Editor」⇒ **不许**返回 []（startswith 太宽）",
       _b3 != [], _b3)

    print("  B4 目标「测试」，绿底行读到「公测测试组」（目标名是其子串）")
    _b4 = _conv("测试", _riv_kc, "公测测试组")
    print("     ⇒ %r" % (_b4,))
    ck("B4 「公测测试组」≠「测试」⇒ **不许**返回 []", _b4 != [], _b4)

    print("\n── C. 撞名 rivle 也在场时（rival 名也包含目标名）──")
    print("  C1 目标「测试」，rival「测试群」，绿底行「测试群」")
    _c1 = _conv("测试", _riv_tq, "测试群")
    print("     ⇒ %r" % (_c1,))
    ck("C1 绿底行是 rival ⇒ 应收敛到 [rival]（非空）", _c1 is not None and len(_c1) == 1, _c1)

    print("\n── D. 兜底：读不出 / 太弱 ⇒ 一律不许收敛（fail-closed）──")
    for _tag, _hl in (("空名", ""), ("量不到", None), ("绿底太弱", "__weak__")):
        ad = W.WeChatAdapter.__new__(W.WeChatAdapter)
        _keep = CO.highlight
        try:
            if _hl == "__weak__":
                CO.highlight = lambda img: {"y_abs": 100, "score": 0.10, "name": "测试"}
            else:
                CO.highlight = (lambda img, _v=_hl: (None if _v is None
                                                    else {"y_abs": 100, "score": 0.97, "name": _v}))
            _d = ad._converge_by_highlight(object(), "group:" + A_ID, "测试", _riv_kc)
        finally:
            CO.highlight = _keep
        ck("D %s ⇒ 弃权（返回 None，调用方保持原样）" % _tag, _d is None, _d)

    print("\n" + "=" * 78)
    print("== 汇总：%d 通过 / %d 失败 ==" % (_n, _bad))
    print("=" * 78)
    raise SystemExit(1 if _bad else 0)


if __name__ == "__main__":
    main()
