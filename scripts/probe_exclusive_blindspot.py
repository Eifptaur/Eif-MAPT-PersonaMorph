# -*- coding: utf-8 -*-
"""probe_exclusive_blindspot.py —— 排他性核对里的**同构 B2 缺口**：参照集只有白名单

⛔ 背景（先把链条读清楚，别误判）：
  `chat_identity_ok` 里有一个**刻意设计**的放宽分支（v2.1.43 之后加的，为修
  「用户想发也发不出去」）：时间档给不出结论时（①活动行时间戳读不出 ②同一分钟有别人
  也在说话），**改由「内容排他」定论** —— 看得到目标正文、看不到别家正文 ⇒ 放行。

  问题在于 `_pane_excludes_others` 遍历的参照集是 `self._monitored_chat_ids()`
  （＝白名单∩群列表＋私聊目标）⇒ **名单外**的会话**根本不在参照集里**，
  它的正文再像也"看不见" ⇒ 判「排他」⇒ 放行。

  这与 §11 修的 B2（同分钟 rival 候选集）是**同一个病根**，方向同样是**放宽**。

本探针只做一件事：**把这条链在"名单外会话与目标逐字雷同"时会不会放行记下来**。
判定口径：
  E1 名单外会话与目标**逐字雷同**，且它的正文出现在聊天区 ⇒ **不许**判"排他"
     （因为"雷同"这条正是白名单侧已有的防线，见 `_pane_excludes_others` 里那段注释；
       名单外的同一情形却漏掉了）
  E2 名单外会话与目标正文**不同**、且出现在聊天区 ⇒ **不许**判"排他"
     （当前实现也漏 —— 参照集里没有它）
  E3 反例锚：**扩集拿不到**（`_all_chat_last_hhmm` 返回空）时，不许把"看不见"当"干净"
  E4 正例锚：名单外会话与目标正文都**不在**聊天区 ⇒ 判"排他"（不许因收紧把正常的砍掉）
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import agent.wechat as W  # noqa: E402

A_ID = "10000000001@chatroom"   # 目标（在白名单）
C_ID = "66000000111@chatroom"   # ★ 名单外的野群（B2 的关键角色）
_SAME = "这条消息两个会话一模一样逐字雷同"      # 与目标**逐字雷同**
_OTHER = "野群说的完全是另外一句不相干的话"      # 与目标不同

_n = 0
_bad = 0


def ck(name, cond, extra=""):
    global _n, _bad
    _n += 1
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name, ("  [%s]" % extra) if extra else ""))
    if not cond:
        _bad += 1
    return bool(cond)


def _mk_adapter(target_texts, wild_texts, all_hhmm):
    """只桩最小面：目标/野群的 recent_texts、白名单、全量扩集。"""
    ad = W.WeChatAdapter.__new__(W.WeChatAdapter)
    ad._monitored_chat_ids = lambda: ["group:" + A_ID]        # ← 白名单里**只有目标**
    _tx = {A_ID: list(target_texts), C_ID: list(wild_texts)}
    ad.recent_texts = lambda cid, *a, **k: _tx.get(str(cid).split(":", 1)[-1], [])
    ad._all_chat_last_hhmm = lambda: dict(all_hhmm)
    return ad


def main():
    print("=" * 78)
    print("排他性核对的「名单外」缺口探针（返回 True ＝ 判「排他」⇒ 会被用来放行）")
    print("=" * 78)

    print("\n── E1 ★名单外会话与目标**逐字雷同**，且它的正文出现在聊天区 ──")
    _ad = _mk_adapter([_SAME], [_SAME], {C_ID: "16:52"})
    _e1, _w1 = _ad._pane_excludes_others("group:" + A_ID, _SAME)
    print("     ⇒ %s / %s" % (_e1, _w1))
    ck("E1 野群正文**逐字雷同**且在聊天区 ⇒ 不许判「排他」（这正是白名单侧已有的防线，"
       "名单外却漏了）", _e1 is False, _e1)

    print("\n── E2 ★名单外会话与目标正文**不同**，且它的正文出现在聊天区 ──")
    _ad2 = _mk_adapter([_SAME], [_OTHER], {C_ID: "16:52"})
    _e2, _w2 = _ad2._pane_excludes_others("group:" + A_ID, _SAME + "\n" + _OTHER)
    print("     ⇒ %s / %s" % (_e2, _w2))
    ck("E2 野群正文与目标不同、但出现在聊天区 ⇒ 不许判「排他」", _e2 is False, _e2)

    print("\n── E3 扩集**拿不到**（`_all_chat_last_hhmm` 返回空）──")
    _ad3 = _mk_adapter([_SAME], [_OTHER], {})
    _e3, _w3 = _ad3._pane_excludes_others("group:" + A_ID, _SAME)
    print("     ⇒ %s / %s" % (_e3, _w3))
    ck("E3 ★扩集拿不到 ⇒ 不许把「看不见名单外」当「干净」（fail-closed 收紧）",
       _e3 is False, _e3)

    print("\n── E4 正例锚：名单外会话的正文**不在**聊天区 ──")
    _ad4 = _mk_adapter([_SAME], [_OTHER], {C_ID: "16:52"})
    _e4, _w4 = _ad4._pane_excludes_others("group:" + A_ID, _SAME)
    print("     ⇒ %s / %s" % (_e4, _w4))
    ck("E4 聊天区里确实没有别家正文 ⇒ 判「排他」（不许因收紧把正常的也砍掉）",
       _e4 is True, _e4)

    print("\n" + "=" * 78)
    print("== 汇总：%d 通过 / %d 失败 ==" % (_n, _bad))
    print("=" * 78)
    raise SystemExit(1 if _bad else 0)


if __name__ == "__main__":
    main()
