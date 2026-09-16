# -*- coding: utf-8 -*-
"""角色卡补足引擎：短人设自动补足到完整可用（评估机制 scores ≥60 基准）。

补足内容：
  1. 通用群聊规则段（说话节奏/AI 味禁令/被@必回/分条——与默认卡同源要求）
  2. 对话示例（从角色口头禅自动提取 3 条 → 引导模型按该风格说话="效果补足"）
  3. 沉默类角色补「必要对话扩展」（林克/皮卡丘/小骑士/盾山——群里必须能正常交流）
用法：import agent.persona_enrich → enrich_all(PERSONAS) 返回补足后的 dict（原地增强）。
"""
from __future__ import annotations

import re

_COMMON_RULES = """## 说话规则（群聊通用）
- 短句像打字不像写作文；一条消息一行，想补充就发下一条（send_message 传数组），绝不空格假装分条。
- 默认 1~10 字；能一个字说清就不说整句；允许只发"？""唉""哈哈"。
- 不总结、不升华、不免费人生建议、不每条都以问句结尾；不用 Markdown（**/#/列表在微信是乱码）。
- 被 @ 或提到你的名字必回；没被叫但有能接的话题也主动参与；不想接就安静，不需要解释。
- 被怀疑是 AI：大方承认但按你的风格打闹，绝不解释模型/训练/提示词（打闹也要贴合你的人设——高冷就"嗯，你猜"，活泼就"才不是呢"，不玩当代网络梗）。
- 保持你的口头禅和口吻：那是你的辨识度，但别每句都甩。
"""

_EXAMPLES_TMPL = """## 对话示例（空格=分两条）
- 群友问你"在吗" → 你：「{q1}」
- 群友说"今天好累" → 你：「{q2}」或按你风格吐槽两句
- 群友说"再来一句" → 你：「{q3}」
（示例是风格参考——用你的口气说话，别照抄文字。）
"""

_SILENT_EXT = """## 必要对话扩展（很重要）
- 你平时话少，但群友【直接问你、需要帮助、或明显在等你回应】时，必须用【完整但简短的中文句子】回答，不许只回"…"或符号。
- 只有日常闲聊时你才保持少话/拟声；被人认真提问时，一秒切换成正常群友。
- 实在接不住就发"嗯？""你说"——但不要沉默。
"""


def _quotes(text: str) -> list:
    return [q for q in re.findall(r"[「“\"]([^」”\"]{2,24})[」”\"]", text) if q][:6]


def enrich(text: str, silent: bool = False) -> str:
    """把一张短角色卡补足为完整可用（保留原内容 + 通用规则 + 示例 + 可选沉默扩展）。"""
    t = (text or "").strip()
    if not t:
        return t
    if "说话规则（群聊通用）" in t:
        return t  # 已补足
    # 已有完整"说话+示例"结构（如内置默认小鲸鱼卡：说话铁律/节奏习惯/对话示例齐全）
    # → 不再重复追加通用段（否则系统提示出现两份说话规则/示例，浪费 token 且干扰）
    if "## 说话" in t and ("## 对话示例" in t or "## 会话示例" in t):
        return t
    parts = [t, "", _COMMON_RULES]
    qs = _quotes(t)
    if len(qs) < 3:
        # 口头禅不足：用角色名兜底生成
        qs = (qs + ["（按你的风格说一句）", "（用你的口气回）", "（摆出你的样子）"])[:3]
    parts += ["", _EXAMPLES_TMPL.format(q1=qs[0][:18], q2=qs[1][:18], q3=qs[2][:18])]
    if silent:
        parts += ["", _SILENT_EXT]
    return "\n".join(parts)


def enrich_all(personas: dict) -> dict:
    """原地补足并返回；silent 判定同 persona_check。"""
    silent_keys = ("link_zelda", "pika", "kongqishi", "dunshan", "novice_jieluo")
    for k, v in personas.items():
        if not isinstance(v, dict) or not v.get("text"):
            continue
        silent = any(s in k for s in silent_keys) or ("台词极少" in v["text"]) \
            or ("沉默是金" in v["text"]) or ("全拟声词" in v["text"]) or ("基本只有" in v["text"]) \
            or ("话极少" in v["text"])
        v["text"] = enrich(v["text"], silent=silent)
    return personas


# 直接使用时的快捷方式：from agent.persona_enrich import enrich_all
