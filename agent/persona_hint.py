# -*- coding: utf-8 -*-
"""角色卡 → 行为档位建议（零 token：本地关键词检测）。

用户填写/切换自定义角色卡时，程序根据角色卡文本推断「行为偏好」：
    participation（参与度：low/medium/high）与 sticker_level（表情包 0~3）。
推断结果只作为「建议」，展示在控制台由用户一键应用——不强制（角色卡管"怎么说"，
这里建议"做多做少"，最终用户拍板）。

省 token 设计：纯字符串匹配，不调 LLM；换角色卡后 0 成本出建议。
规则表集中在 ROLE_HINTS，后续可扩充关键词。
"""
from __future__ import annotations

# 关键词 → 参与度倾向（命中即 +1 分）
PARTICIPATION_HINTS = {
    "high": ["活跃", "话痨", "话多", "话唠", "开朗", "热情", "外向", "爱说话", "爱聊", "自来熟",
             "中二", "卖萌", "活泼", "调皮", "皮", "社牛", "气氛组", "群聊发动机", "爱抛梗", "爱接话"],
    "low": ["高冷", "安静", "沉默", "内向", "潜水", "话少", "不爱说话", "寡言", "冷淡", "宅",
            "爱答不理", "懒得理", "别打扰", "离我远点", "懒得多说", "懒得回应",
            "旁观", "看戏", "围观", "佛系", "淡定", "靠谱但不爱发言", "话不多"],
}

# 关键词 → 表情包积极度（命中即取该值）
STICKER_HINTS = {
    0: ["不用表情", "不发表情", "不用表情包", "正经", "严肃", "老成", "严谨", "书卷气", "文绉绉"],
    1: ["偶尔表情", "偶尔用用", "表情克制"],
    2: ["表情包", "用表情", "表情帝", "爱用表情", "颜文字", "w(゜Д゜)w", "喵喵", "可爱", "萌"],
    3: ["表情包爱好者", "表情狂魔", "表情轰炸", "表情包小能手", "斗图", "斗表情", "表情包大师"],
}


def suggest_from_role_text(role_text: str) -> dict:
    """根据角色卡文本推断行为档位建议。返回 {participation, sticker_level, reason}。
    只分析"角色本体"部分（去掉通用群聊规则段——避免通用措辞误判成安静/少话）。
    """
    t = str(role_text or "")
    # 排除补足引擎通用段（"说话规则（群聊通用）"及其后），只看角色本身
    cut = t.find("## 说话规则（群聊通用）")
    if cut > 0:
        t = t[:cut]
    t = t.lower()
    if not t.strip():
        return {"participation": "medium", "sticker_level": 0, "reason": "角色卡为空→默认"}

    # 参与度计分（≥1 即分档；词越多信心越高）
    hi = sum(1 for k in PARTICIPATION_HINTS["high"] if k.lower() in t)
    lo = sum(1 for k in PARTICIPATION_HINTS["low"] if k.lower() in t)
    if hi >= 1 and hi >= lo:
        participation = "high"
        reason = "角色卡表现出活跃倾向（%s）" % "、".join([k for k in PARTICIPATION_HINTS["high"] if k.lower() in t][:3])
    elif lo >= 1:
        participation = "low"
        reason = "角色卡表现出安静/高冷倾向（%s）" % "、".join([k for k in PARTICIPATION_HINTS["low"] if k.lower() in t][:3])
    else:
        participation = "medium"
        reason = "角色卡无明显活跃/安静倾向（用默认档）"

    # 表情包积极度
    sticker = 0
    for lv in (3, 2, 1, 0):
        if any(k.lower() in t for k in STICKER_HINTS[lv]):
            sticker = lv
            break
    return {"participation": participation, "sticker_level": sticker, "reason": reason}
