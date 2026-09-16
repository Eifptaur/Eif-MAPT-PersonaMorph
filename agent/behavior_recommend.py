# -*- coding: utf-8 -*-
"""行为推荐规则（控制台「根据角色卡推荐行为档」用）：
从角色卡文本提取五维行为因子 → 加权 → 参与度档 + 表情包档。纯本地启发式，零 token。
"""
from __future__ import annotations


# 高活跃特征（词/短语出现在角色卡 → 加分）
ACTIVE_MARKERS = [
    "话痨", "话多", "话唠", "爱说", "爱聊", "自来熟", "社牛", "开朗", "热情", "外向",
    "活泼", "调皮", "元气", "卖萌", "中二", "气氛组", "爱抛梗", "爱接话", "活跃",
    "热闹", "话匣子", "口若悬河", "贫嘴", "损人", "逗趣", "整活", "起哄", "捧场",
    "主动", "积极", "话痨记者", "话多", "爱安利", "吐槽役", "接得住",
]

# 低活跃特征
PASSIVE_MARKERS = [
    "高冷", "安静", "沉默", "内向", "潜水", "话少", "不爱说话", "寡言", "冷淡",
    "佛系", "淡定", "旁观", "看戏", "围观", "懒", "躺平", "咸鱼", "宅", "少言",
    "惜字如金", "极简", "不爱解释", "懒得理", "爱答不理", "沉默是金", "不动声色",
    "看破不说破", "稳重", "严肃",
]

# 表情包特征
STICKER_HI = ["表情包", "斗图", "颜文字", "发图", "表情", "猫娘", "卖萌", "撒娇", "颜文字", "可爱"]
STICKER_LO = ["不写 Markdown", "不刷屏", "严肃", "冷", "庄重", "严肃班主任", "高冷", "短句"]


def recommend(text: str) -> dict:
    """返回 {participation: low|medium|high, sticker: 0~3, reasons:[...]}"""
    t = text or ""
    hit_active = sum(1 for m in ACTIVE_MARKERS if m in t)
    hit_passive = sum(1 for m in PASSIVE_MARKERS if m in t)
    hit_sticker_hi = sum(1 for m in STICKER_HI if m in t)
    hit_sticker_lo = sum(1 for m in STICKER_LO if m in t)

    # 参与度：活跃特征显著 → high；消极特征显著 → low；否则 medium
    if hit_active - hit_passive >= 2 or hit_active >= 3:
        part = "high"
    elif hit_passive - hit_active >= 1 or hit_passive >= 2:
        part = "low"
    else:
        part = "medium"

    # 表情包：0~3 级
    score = hit_sticker_hi - hit_sticker_lo
    sticker = 0 if score <= 0 else (1 if score <= 1 else (2 if score <= 3 else 3))

    reasons = []
    reasons.append("活跃标记×%d（%s）" % (hit_active, "、".join([m for m in ACTIVE_MARKERS if m in t][:3]) or "无"))
    reasons.append("安静标记×%d（%s）" % (hit_passive, "、".join([m for m in PASSIVE_MARKERS if m in t][:3]) or "无"))
    reasons.append("表情包标记 %d / 克制标记 %d" % (hit_sticker_hi, hit_sticker_lo))
    return {"participation": part, "sticker": sticker, "reasons": reasons,
            "marks": {"active": hit_active, "passive": hit_passive,
                      "sticker_hi": hit_sticker_hi, "sticker_lo": hit_sticker_lo}}
