# -*- coding: utf-8 -*-
"""角色代理（W4）：群里 **@某个角色名** ⇒ 只有那个人设回话。

=====================================================================
它解决什么
=====================================================================
多槽位人设（`persona.slots`）装上之后，"这一条消息该用哪个人设回"就没人管了 ——
默认永远走 `persona.active` 那一个。于是群里 @「小鲸鱼」和 @「林黛玉」得到的是同一张嘴。

本模块只做**一件事**：从消息文本里判"被 @ 的是哪个槽位"，并给出**那一个**角色卡的正文。
⛔ 它**不**做触发是否成立的总判定（那还在 `prompt.resolve_context_tier`）——
   它只在"已经决定要回"之后，回答"**用谁回**"。

=====================================================================
三条纪律
=====================================================================
① **@ 不到槽位名字 ⇒ 回落默认**（不是"不回话"）—— 判不出来就不该改变原行为；
② **多个人设共享同一套记忆** —— 本模块不碰记忆层（`ChatStore` / `memory`），
   所以这条是**结构性成立**的，不靠"记得别分家"；
③ **名字判定必须严**（照抄 `prompt.is_at_me` 的教训）：`@小明` 不许命中「小明同学」这个槽位名 ——
   微信 @ 后面必须是**分隔符或结束**（复用 `prompt.AT_SEPS`）。

用法：
    card = pick_persona("@林黛玉 你怎么看", cfg)   # ⇒ 林黛玉那张卡的 dict；判不出 ⇒ {}
    text = role_text(card)                          # ⇒ 该卡的正文；空 ⇒ 调用方回落默认
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: 槽位名与 @ 名字的**别名**分隔符：`slots` 里可以用 `"林黛玉|黛玉"` 这种写法给一个槽位挂多个别名。
ALIAS_SEP = "|"


def _as_text(v) -> str:
    return "" if v is None else str(v).strip()


def _slots(cfg) -> dict:
    """取 `persona.slots`（**宽容**：任何异常/类型不对 ⇒ 空表，绝不抛）。"""
    try:
        c = cfg
        if not isinstance(c, dict):
            return {}
        # 两种入参口径都接：整个配置 / persona 段本身（与 `persona_card.slot_card` 的坑一致）
        p = c.get("persona")
        seg = p if isinstance(p, dict) else c
        slots = seg.get("slots")
        return slots if isinstance(slots, dict) else {}
    except Exception as e:  # noqa: BLE001
        log.debug("读 persona.slots 失败（按「没有槽位」处理）：%s", e, exc_info=True)
        return {}


def slot_names(cfg) -> list:
    """所有槽位的**可 @ 名字**（含别名）⇒ 扁平列表。**新→旧**无关，保持配置里的顺序。"""
    out = []
    for key, val in _slots(cfg).items():
        out.extend(_names_of(key, val))
    return out


def _names_of(key, val) -> list:
    """一个槽位的全部可 @ 名字：**槽位键** ＋ 卡里的 `bot_name` ＋ `键|别名` 里的别名。"""
    names = []
    k = _as_text(key)
    if k:
        names.extend([x.strip() for x in k.split(ALIAS_SEP) if x.strip()])
    if isinstance(val, dict):
        bn = _as_text(val.get("bot_name")) or _as_text(val.get("name"))
        if bn:
            names.append(bn)
    # 去重且保序
    seen, out = set(), []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _at_names(text) -> list:
    """文本里**全部** `@某某` 的那个「某某」（可有多处）。判不出 ⇒ 空列表。**只读、绝不抛**。"""
    try:
        from . import prompt as _p

        seps = getattr(_p, "AT_SEPS", None)
        t = str(text or "")
        out, i = [], t.find("@")
        while i >= 0:
            j = i + 1
            while j < len(t):
                ch = t[j]
                if seps is not None and ch in seps:
                    break
                if ch in " \u3000\t\r\n":      # 兜底：常见空白也算分隔（AT_SEPS 拿不到时）
                    break
                j += 1
            nm = t[i + 1:j].strip()
            if nm:
                out.append(nm)
            i = t.find("@", j if j > i else i + 1)
        return out
    except Exception as e:  # noqa: BLE001
        log.debug("解析 @ 名字失败（按判不出处理）：%s", e, exc_info=True)
        return []


def pick_slot(text, cfg) -> str:
    """被 @ 的是哪个**槽位键**。判不出（没 @ / @ 的不是槽位名）⇒ 空串。"""
    slots = _slots(cfg)
    if not slots:
        return ""
    ats = _at_names(text)
    if not ats:
        return ""
    for key, val in slots.items():
        names = _names_of(key, val)
        if any(a == n for a in ats for n in names):   # ⛔ **全等**，不是子串（见模块头纪律③）
            return _as_text(key)
    return ""


def pick_persona(text, cfg) -> dict:
    """从消息里判出该用哪个槽位的人设 ⇒ 那张卡的 dict；判不出 ⇒ **空 dict**（调用方回落默认）。

    ⛔ 返回空 dict 是**正常结果**（不是错误）—— 它表示"没 @ 到任何槽位，照旧用默认人设"。
    """
    try:
        from . import persona_card as _pc

        name = pick_slot(text, cfg)
        if not name:
            return {}
        card = _pc.slot_card(cfg, active=name)
        return card if isinstance(card, dict) else {}
    except Exception as e:  # noqa: BLE001
        log.warning("取槽位人设失败 ⇒ 回落默认人设（不影响回话）：%s", e)
        return {}


def role_text(card) -> str:
    """把选中的槽位卡渲染成**角色正文**；空卡 ⇒ 空串（调用方据此回落默认人设）。"""
    if not isinstance(card, dict) or not card:
        return ""
    try:
        from . import persona_card as _pc

        # `slot_card` 回的是字段 dict ⇒ 直接渲染（不重复取槽位）
        return _pc.render_prompt(card) if hasattr(_pc, "render_prompt") else ""
    except Exception as e:  # noqa: BLE001
        log.warning("渲染槽位人设失败 ⇒ 回落默认人设：%s", e)
        return ""
