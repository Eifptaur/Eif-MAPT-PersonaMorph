# -*- coding: utf-8 -*-
"""两个强度不同的"还算不算数"谓词 —— **数据有效性** 与 **界面可更新性** 分开判（P0-4）。

**要解决的病**：把"这次操作还算不算数"和"能不能更新当前界面"**当成一件事**，
必然掉进两种错里的一种：

- **对不上就整个放弃** —— 该清的缓存没清，下一次错误地复用（"怕复活"却复活了）；
- **不管对不对都往界面上写** —— 把 A 会话的结果画到 B 会话的界面上（串台）。

**两个谓词**（强度与管辖范围都不同，**不许互相顶替**）：

| 谓词 | 强度 | 判什么 | 决定 |
|---|---|---|---|
| `is_mutation_valid()` | **强** | 账号 ＋ 会话 ＋ 代次 ＋ 操作上下文 **四项全对** | **写不写**（发不发 / 记不记账 / 清不清缓存） |
| `can_apply_to_visible()` | **弱** | 只要"**还看着这个会话**" | **界面/日志更不更新** |

**落地原则**（这条是本模块存在的全部意义）：

> **持久层无条件失效（怕复活）；内存层有条件更新（怕串台）。**

拿发送举例：消息确实发出去了，但用户在发送完成前切到了别的会话 ——
- **发送记录必须写**（不写的话，下次没人知道这条已经发过 ⇒ 重试会重复发）；
- **界面不该跟着更新**（此刻用户看的是 B 会话，把 A 的"已发送"画上去就是串台）。

**为什么"代次"（generation）也要进强谓词**：同一会话可能被**重新打开**过。
会话 key 相同、但中间发生过"关闭再打开"（缓存被换、消息列表重载）⇒
拿"关开之前"算出的上下文去写，落到的是"关开之后"的对象上。
代次就是用来认出"这不是同一个上下文"的。
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("persona-morph")

#: 全局代次计数器（进程内单调递增）。**不用时间戳**：
#: 同一毫秒内可能发生两次"重新打开"，时间戳会给出同一个值 ⇒ 代次分不开两次事件。
_seq_lock = threading.Lock()
_seq = 0


def next_generation() -> int:
    """取一个新的代次号（进程内单调递增，线程安全）。"""
    global _seq
    with _seq_lock:
        _seq += 1
        return _seq


def _norm(v) -> str:
    return str(v or "").strip()


def is_mutation_valid(*, account: str = "", chat_key: str = "", generation=None,
                      ctx_account: str = "", ctx_chat_key: str = "",
                      ctx_generation=None) -> tuple:
    """**强谓词**：这次改动还算不算数？（决定**写不写**）

    四项全对才算数，**任一项对不上就判无效**。为什么用"四项全对"这么严：
    写进持久层的记录**一旦写错就很难撤**（发件台账、缓存、去重窗口都靠它），
    宁可漏记一次（下次重试会再发一遍 —— 有去重窗口兜着），不可错记一次。

    返回 `(ok: bool, reason: str)`。`reason` 为空表示通过；否则是**给人看的**说明
    （形如 `"账号不符"`、`"代次已过期(3→5)"`），供调用方写日志/回执。

    ⚠️ **空值不算"一致"**：两侧都空时若判通过，等于"不知道是谁 ⇒ 就当对吧"，
    那正好是"有人切了会话、我这边读不到会话名"的场景 ⇒ **必须判无效**。
    """
    if not _norm(account) or not _norm(ctx_account):
        return False, "账号缺失（一侧为空 ⇒ 无法确认是不是同一个账号）"
    if _norm(account) != _norm(ctx_account):
        return False, "账号不符（当前 %s / 记账 %s）" % (_norm(account), _norm(ctx_account))
    if not _norm(chat_key) or not _norm(ctx_chat_key):
        return False, "会话缺失（一侧为空 ⇒ 无法确认是不是同一个会话）"
    if _norm(chat_key) != _norm(ctx_chat_key):
        return False, "会话不符（当前 %s / 记账 %s）" % (_norm(chat_key), _norm(ctx_chat_key))
    if generation is None or ctx_generation is None:
        return False, "代次缺失（无法确认会话有没有被重新打开过）"
    try:
        if int(generation) != int(ctx_generation):
            return False, "代次已过期(%s→%s)" % (ctx_generation, generation)
    except Exception:  # noqa: BLE001
        return False, "代次不可比（%r / %r）" % (generation, ctx_generation)
    return True, ""


def can_apply_to_visible(*, visible_chat: str = "", target_chat: str = "",
                         visible_account: str = "", target_account: str = "") -> tuple:
    """**弱谓词**：界面能不能跟着更新？（决定**更不更新**）

    比强谓词松一档：只要"**此刻看着的就是目标会话**"就允许更新，
    **不要求**代次一致（界面是内存态，画错了下一帧就重画，代价远小于持久层写错）。

    返回 `(ok: bool, reason: str)`。

    ⚠️ **读不到"当前看着谁"时判不更新**（fail-closed）：
    宁可这一帧不刷新（用户会看到"界面没动"，下次刷新自然补上），
    也不能在"不知道用户在看哪"的情况下往界面上画另一个会话的结果。
    """
    if not _norm(visible_chat):
        return False, "读不到当前正打开的会话 ⇒ 不更新界面（避免串台）"
    # ★ P0-3 接点：`visible_chat` 若是个**隔离桶**（身份未解析时由
    #   `scope.pending_scope()` 新建），它**与任何身份都不相等** —— 包括与
    #   另一个隔离桶。这一条显式写出来，是为了不依赖"桶名恰好长得不同"这种巧合：
    #   哪天有人改了桶名格式（比如不再带时间戳），相等比较仍必须判不同。
    try:
        from . import scope as _sc
        if _sc.is_pending(visible_chat) or _sc.is_pending(target_chat):
            return False, "身份未解析（隔离桶 %r）⇒ 不更新界面（避免把结果画到别的会话上）" % (
                str(visible_chat)[:24],)
    except Exception:  # noqa: BLE001 — 隔离件取不到时退回下面的字面比较（仍然 fail-closed）
        pass
    if _norm(visible_chat) != _norm(target_chat):
        return False, "用户已切到别的会话（当前 %s / 目标 %s）⇒ 不更新界面" % (
            _norm(visible_chat), _norm(target_chat))
    if _norm(visible_account) and _norm(target_account) \
            and _norm(visible_account) != _norm(target_account):
        return False, "账号不符（界面 %s / 目标 %s）⇒ 不更新界面" % (
            _norm(visible_account), _norm(target_account))
    return True, ""


def decide(*, account: str = "", chat_key: str = "", generation=None,
           ctx_account: str = "", ctx_chat_key: str = "", ctx_generation=None,
           visible_chat: str = "", visible_account: str = "") -> dict:
    """**一次算清两个谓词**，并给出"该做什么"的结论（调用方不必自己拼）。

    返回 `{"persist": bool, "visible": bool, "persist_reason": str, "visible_reason": str}`。

    ★ 这里落实那条落地原则：
      · `persist` 走**强**谓词 —— 通过 ⇒ 记录照写；
      · `visible` 走**弱**谓词，且**只在强谓词通过时才可能为真** ——
        强谓词没过说明"这次改动根本不是这个会话的"，界面当然更不该更新。
    """
    ok_p, why_p = is_mutation_valid(
        account=account, chat_key=chat_key, generation=generation,
        ctx_account=ctx_account, ctx_chat_key=ctx_chat_key, ctx_generation=ctx_generation)
    if not ok_p:
        # 强谓词没过：界面也没道理更新 —— 但**reason 分开写**，因为两者是独立的判断，
        # 排障时"持久层为什么没写"与"界面为什么没更新"是两个问题。
        return {"persist": False, "visible": False,
                "persist_reason": why_p,
                "visible_reason": "改动本身无效（%s）⇒ 界面同样不更新" % why_p}
    ok_v, why_v = can_apply_to_visible(
        visible_chat=visible_chat, target_chat=chat_key,
        visible_account=visible_account, target_account=account)
    if ok_v:
        return {"persist": True, "visible": True, "persist_reason": "", "visible_reason": ""}
    # ★ 核心场景：改动有效（发出去了）⇒ **记录照写**；只是界面不跟。
    return {"persist": True, "visible": False, "persist_reason": "",
            "visible_reason": why_v}


def after_switch_mark() -> int:
    """切走会话时调用，取一个新代次 —— **切走本身就意味着"上一个上下文过期"**。

    为什么不让调用方自己 `next_generation()`：那样每个调用点都要记得"切会话也要换代次"，
    漏一处就留一个永不过期的上下文。收敛到一个名字里，语义更好守。
    """
    gen = next_generation()
    log.debug("会话上下文代次 +1 ⇒ %d", gen)
    return gen


__all__ = ["next_generation", "after_switch_mark", "is_mutation_valid",
           "can_apply_to_visible", "decide"]
