# -*- coding: utf-8 -*-
"""人性化行为决策引擎（省 token：纯规则/启发式，不调 LLM）

目标：让机器人在正确时机做真人会做的小事——
  收藏表情 / 发表情 / 点赞朋友圈 / @ 提醒 / 主动开话题 / 引用 / 拍一拍…
但所有操作的<b>频率与偏好</b>由「角色卡人设档位」调节（同一套规则作用于所有角色，
只是系数不同——人设是"风格"，不是"开关"）。

设计原则（省 token）：
  1. 全部决策 = 确定性规则（无 LLM 调用，零 token）；
  2. 随机性用本地 random（种子化，可测试），时机窗口由时间/计数状态机控制；
  3. 决策结果只下发"行为意图"（collect_emoji / send_emoji / like_moments…），
     由调用方（persona_morph）执行对应 wechat/tools 动作；
  4. 每类行为有：基础概率 × 人设系数 × 冷却时间 × 每日上限，全部可调。

人设档位（persona.participation: low/medium/high）+ sticker_level 自动映射成系数：
  participation 影响"活跃度"（low=0.5×、medium=1.0×、high=1.6×）
  sticker_level 影响表情类频率（0=0.3×、1=1.0×、2=1.8×、3=3.0×）
  自定义角色卡：不额外改频率——角色卡决定"怎么说"，引擎决定"做不做"，
  两者解耦（因此任何角色卡都不会让机器人刷屏/失控）。
"""
from __future__ import annotations

import random
import time

from .config import get_config


# 行为默认表（系数 1=标准；冷却/上限防刷屏）
DEFAULTS = {
    # 收藏表情（群气氛热时概率收藏）
    "collect_emoji": {"enabled": True, "probability": 0.5, "cooldown_s": 900, "daily_limit": 6},
    # 用收藏的表情回发（对方发表情后，概率挑一个回敬）
    "send_emoji": {"enabled": True, "probability": 0.35, "cooldown_s": 600, "daily_limit": 6},
    # 点赞朋友圈（浏览时概率点赞）
    "like_moments": {"enabled": False, "probability": 0.4, "cooldown_s": 3600, "daily_limit": 5},
    # 主动 @ 群友（话题相关时点名）
    "at_member": {"enabled": True, "probability": 0.18, "cooldown_s": 1200, "daily_limit": 10},
    # 引用（冷场/接话时）由 sender 的 quote 规则负责，这里只统计
    "quote": {"enabled": True, "probability": 1.0, "cooldown_s": 0, "daily_limit": 0},
    # 拍一拍（主动皮）
    "poke_active": {"enabled": True, "probability": 0.1, "cooldown_s": 600, "daily_limit": 3},
}

# 活跃系数（participation → 乘数）
ACTIVITY = {"low": 0.5, "medium": 1.0, "high": 1.6}
# 表情系数（sticker_level → 乘数）
STICKER = {"0": 0.3, "1": 1.0, "2": 1.8, "3": 3.0}


class BehaviorDecider:
    """纯本地行为决策（无 LLM）。调用方在事件时机询问 should_xxx()。"""

    def __init__(self):
        self._state = {}   # action -> {"last": ts, "today": count, "day": yyyy-mm-dd}

    def _cfg(self):
        return get_config().get("behavior", {}) or {}

    def _persona(self):
        p = get_config().get("persona", {}) or {}
        return p

    def multipliers(self) -> dict:
        """人设 → 频率乘数（角色卡解耦：只调系数不改规则）。"""
        act = ACTIVITY.get(str(self._persona().get("participation") or "medium"), 1.0)
        sl = get_config().get("store", {}).get("sticker_level")
        sl = str(sl if sl is not None else 1)
        st = STICKER.get(sl, 1.0)
        return {"activity": act, "sticker": st}

    def _tick(self, action: str):
        today = time.strftime("%Y-%m-%d")
        s = self._state.setdefault(action, {"last": 0, "today": 0, "day": today})
        if s["day"] != today:
            s["day"] = today; s["today"] = 0
        return s

    def should(self, action: str, context: dict | None = None) -> bool:
        """是否执行某个行为。context 可带:
          force=True（用户明确要求 → 绕过概率只查上限）
          triggers（如 "对方发了表情"）。"""
        spec = {**DEFAULTS.get(action, {}), **self._cfg().get(action, {})}
        if not spec.get("enabled", True):
            return False
        ctx = context or {}
        mul = self.multipliers()
        # 表情类乘 sticker，其他乘 activity
        factor = mul["sticker"] if "emoji" in action else mul["activity"]
        # 冷却（用户明确要求 force 时跳过冷却，只留每日上限防刷屏）
        s = self._tick(action)
        now = time.time()
        if not ctx.get("force") and spec.get("cooldown_s") and now - s["last"] < float(spec["cooldown_s"]):
            return False
        # 每日上限
        if spec.get("daily_limit") and s["today"] >= int(spec["daily_limit"]):
            return False
        # 概率（受角色系数影响；用户明确要求 force=True 时跳过概率）
        if not ctx.get("force"):
            prob = max(0.0, min(1.0, float(spec.get("probability", 0)) * factor))
            if random.random() > prob:
                return False
        s["last"] = now
        s["today"] += 1
        return True

    def triggers_check(self, action: str, context: dict | None = None) -> bool:
        """事件触发版：context 里给了具体信号（如对方刚发表情）时按 should 决定。"""
        return self.should(action, context)


decider = BehaviorDecider()
