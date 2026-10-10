# -*- coding: utf-8 -*-
"""**按缺口精准处理**：把一张卡缺的那几栏，**能推的推、推不了的明确标「待补」**。

## 为什么另写一个（不改 `persona_enrich.py`）
`agent/persona_enrich.py::enrich_all()` 的做法是**给每张卡追加同一段通用文字**
（「说话规则（群聊通用）」+ 示例模板 + 沉默扩展）。后果有两层：
  ① **趋中的机械成因**：199/254 张卡（78%）挤在 80~89 档、标准差只有 6.69 ——
     所有卡被"补"上同样的东西，自然被拉平；
  ② **补错了地方**：评分细则里 `fit`（权重 30 最高）看的是 `motive`+`background`，
     `coher` 看 `rules`+`values`+`motive`，而 `enrich()` **一个字都没补动机/价值观/缺点局限**。
实测佐证：缺 3 栏的 146 张卡（62%）平均分 80.37，与缺 1 栏的 80.50 几乎相同
—— 说明"写着缺"这件事**没被分数体现**，补通用段对区分度零贡献。

## ⛔ 关键实测结论：动机/价值观/缺点局限 **推不出来**（2026-10-10 定量确认）
最初的设想是"从卡自己的既有内容里推出缺的那几栏"。**实测证明不可行**：

库里卡的正文是**第一人称角色口吻**（"我是那个接手爷爷旧农场的农夫…"），
里面**没有第三人称的心理描写**可供摘取。硬推的结果是三类垃圾——
  · `# 角色卡：西尔维娅（密教模拟器·蛾系追随者）` —— 抽到了**卡标题行**；
  · `（每次要跑路时，最常说的开场白）` —— 抽到了**台词舞台说明**；
  · `一条消息一行，send_message 传数组` —— 抽到了 `persona_enrich` 追的**群聊管家话**。
定量：176 张缺动机的卡里，能"推出内容"的 88 张**人工抽查全部是上列三类垃圾**；
对照库中**已有的**优秀动机（如 `narukami`："他要的是「身边这些人一个都不掉出去」…"），
那是**人写的第三-person 归纳**，机器从角色口吻里**不可能**还原。
⇒ **结论：机器补动机 = 编。** 与用户口径（"不许编，查不到写「无」"）冲突 ⇒ 不做。

## 本模块做什么（改后的设计）
| 栏 | 能不能机器补 | 怎么做 |
|---|---|---|
| 行为规则 | **能**（有限） | 从说话风格推"在群里怎么插话"，是**行为事实**不是心理 |
| 动机 | **不能** | 写「待补」——**明确告诉人"这栏要人来写"**，不编 |
| 价值观 | **不能** | 同上 |
| 缺点局限 | **不能** | 同上 |

⛔ **「待补」≠「无」**：`「无」` 是"原作确实没有这一项"的**结论**；
   `「待补」` 是"原作可能有、但我们手上这份卡没写、需要人去查证"的**待办**。
   两者不能混 —— 混了就会把 236 张待重写的卡伪装成"已完备"。

## 用法
    from agent import persona_gapfill
    new_text, report = persona_gapfill.fill_text(old_text)
    # report: {"filled": {"rules": "推导", "motive": "待补"}, "kept": [...], "unchanged": bool}
"""
from __future__ import annotations

import re

from .persona_card import FIELD_CN, FIELDS, missing_fields, to_fields

#: 各栏在 `text` 里的固定小标题（与 `persona_card` 一致，一字不差）
HEAD = {
    "background": "## 身份背景", "personality": "## 性格", "speech": "## 说话风格",
    "motive": "## 动机", "values": "## 价值观", "rules": "## 行为规则",
    "limits": "## 缺点局限",
}

#: 要处理的栏。`background` / `personality` / `speech` 是**卡的骨架**，
#: 库里 236 张全都有（实测：三栏 236/236 非空）⇒ 不用补；乱补反而把原味盖掉。
FILLABLE = ("motive", "values", "limits", "rules")

#: **只能靠人写**的栏（机器一补就是编）
HUMAN_ONLY = ("motive", "values", "limits")

#: 人还没写时填的占位符。⛔ 用途是**如实标记待办**，不是补内容。
TODO_MARK = "待补"

#: 句末语气/语气词（推"说话风格→行为规则"用）
_SHORT_SPEECH_CUES = ("短句", "一两个词", "极短", "只说", "少言", "话少", "沉默", "拟声词")


def _derive_rules(f: dict) -> str:
    """行为规则：从"说话风格"推**在群里怎么插话**（短句⇒短回；话少⇒不硬找话题）。

    ⛔ 这是本模块**唯一**敢做的推导，理由：它推的是**行为事实**（"话少的人不会硬找话题"），
       不是心理活动（"他在意什么"）。前者是常识推论，后者是编。
    """
    sp = str(f.get("speech", "") or "")
    if not sp.strip():
        return ""
    if any(c in sp for c in _SHORT_SPEECH_CUES):
        return ("在群里回得短，一句就够，不铺陈；被叫到必应一声，没人接话时不硬找话题。"
                "（据其说话风格推）")
    return ("被叫到就回，回完不追问；看到能接的话接一句，接不上就安静待着。"
            "（据其说话风格推）")


def fill_text(text: str) -> tuple:
    """把 `text` 里**缺的那几栏**补上，返回 `(新 text, 报告)`。

    报告：`{"filled": {栏: "推导"|"待补"}, "kept": [没动的栏], "unchanged": bool}`
    ⛔ 已有内容的栏**一个字节都不动**；不缺任何栏 ⇒ `unchanged=True`，原样返回。
    """
    raw = str(text or "")
    f = to_fields(raw)
    lack = [k for k in FILLABLE if k in missing_fields(f, strict=True)]
    if not lack:
        return raw, {"filled": {}, "kept": list(FIELDS), "unchanged": True}

    lines = raw.rstrip("\n").split("\n")
    filled, kept = {}, []

    # 逐栏处理；**顺序固定**（按 HEAD 的顺序插，保证 `to_fields` 能按序拆开）
    for key in FILLABLE:
        if key not in lack:
            kept.append(key)
            continue
        if key in HUMAN_ONLY:
            # ⛔ 机器补不了 ⇒ 如实标「待补」，绝不编内容（编了就是让模型演错人）
            body, mark = TODO_MARK, "待补"
        else:
            body = _derive_rules(f).strip()
            if body:
                mark = "推导"
            else:
                body, mark = TODO_MARK, "待补"
        filled[key] = mark
        head = HEAD[key]
        # 找小标题行；找不到 ⇒ **在「## 对话示例」之前插一节**（没有对话示例就追加到末尾）
        idx = None
        for i, ln in enumerate(lines):
            if ln.strip() == head:
                idx = i
                break
        block = [head, body, ""]
        if idx is not None:
            # 标题在、内容为空 ⇒ 填在标题下一行（**保留原标题行，不动其他**）
            lines = lines[:idx + 1] + [body] + lines[idx + 1:]
        else:
            ex = None
            for i, ln in enumerate(lines):
                if ln.strip().startswith("## 对话示例"):
                    ex = i
                    break
            if ex is not None:
                lines = lines[:ex] + block + lines[ex:]
            else:
                lines = lines + [""] + block

    return "\n".join(lines).rstrip("\n") + "\n", {"filled": filled, "kept": kept, "unchanged": False}


def fill_card(card: dict) -> tuple:
    """给一张 `{name, text, ...}` 卡做同样的事；其余键（`note` 等）原样保留。"""
    if not isinstance(card, dict):
        return card, {"filled": {}, "kept": [], "unchanged": True}
    new_text, report = fill_text(card.get("text", ""))
    out = dict(card)
    out["text"] = new_text
    return out, report


def gap_report(cards: dict) -> dict:
    """统计一批卡还差哪些栏 —— **交付给施工方的待办凭据**。

    返回 `{"total": n, "by_field": {栏: 还缺几张}, "need_human": [卡id...]}`。
    ⛔ 用途：让"还缺多少"这件事**可量化、可验收**，而不是靠感觉说"补过了"。
    """
    by_field = {k: 0 for k in FIELDS}
    need_human = []
    for cid, card in (cards or {}).items():
        f = to_fields((card or {}).get("text", ""))
        miss = missing_fields(f, strict=True)
        for k in miss:
            by_field[k] = by_field.get(k, 0) + 1
        if any(k in miss for k in HUMAN_ONLY):
            need_human.append(cid)
    return {"total": len(cards or {}), "by_field": by_field, "need_human": need_human}
