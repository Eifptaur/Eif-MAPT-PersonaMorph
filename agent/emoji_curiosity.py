# -*- coding: utf-8 -*-
"""**向群友请教**：低置信度表情的语义，靠"问"补全（用户 2026-10-02 提的方案）。

## 为什么需要它
自定义表情里有一批**疑似梗图**（`confidence: low`）—— 看图读不出、检索也常搜不到
（没有可搜的关键词）。而**梗的含义本就在使用社群的共识里**
⇒ **直接问群里的人**是最可靠的来源，且**问本身就是互动内容**。

## 闭环（四步）
```
① 挑      `pick_low()`            从 low 里挑一条（可带冷却，避免反复问同一条）
② 问      `mark_asked(id, chat)`  发图 + 问句，并**记下待答状态**
③ 收+判   `judge_prompt(...)`     收到群友回复后，**交给模型判别**：
                                   是认真解释，还是玩笑/无关？
④ 记      `apply_verdict(...)`    判定为"认真" ⇒ `emoji_index.learn()` 回填并升为 high
                                  ⇒ **下次就会用了**；不认真 ⇒ 丢弃（可重试）
```

## 三条纪律（都来自踩坑）
1. **必须过滤"不是回答"的消息**：机器人自己的话、别人的无关闲聊、超过应答窗口的旧消息
   ⇒ 否则会把**无关内容**当成语义写进库（污染比不学更糟）。
2. **必须让模型判"是不是玩笑"**：群里常常是**玩梗/反讽**，直接采信会把语义记反。
3. **判定要结构化**：模型必须回 JSON（`is_answer` / `caption` / `situation` / `confidence`），
   拿不到合法 JSON 就**当作"没学到"**，绝不半信半疑地写进去。

⚠️ 触发**保守优先**：默认**不主动问**（问多了像刷屏）；由上层按配置的概率/频率决定。
"""
from __future__ import annotations

import json
import os
import time

from . import persist  # noqa: PLC0415 — 换档必须走唯一实现点（带退避重试）

_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "emoji_ask_state.json")

#: 应答窗口（秒）：超过这么久才回的，**不算**对这次提问的回答
ANSWER_WINDOW_S = 120.0
#: 同一条表情的提问冷却（秒）：避免反复问同一张
ASK_COOLDOWN_S = 6 * 3600.0


def _load() -> dict:
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
        with open(_STATE_PATH + ".tmp", "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        persist.replace_into(_STATE_PATH + ".tmp", _STATE_PATH)
    except Exception:
        pass


def pick_low(exclude=None) -> int | None:
    """挑一条**低置信度、且不在冷却期**的表情用于提问 ⇒ 返回 id；没有则 None。"""
    from . import emoji_index as ei
    st = _load()
    asked = st.get("asked_at") or {}
    now = time.time()
    exclude = set(exclude or [])
    for e in ei.need_web():
        i = e["id"]
        if i in exclude:
            continue
        if now - float(asked.get(str(i), 0) or 0) < ASK_COOLDOWN_S:
            continue
        return i
    return None


def question_text(id_or_md5) -> str:
    """问句（可在发送前由上层微调语气；这里给一个中性、自然的版本）。"""
    return "这个表情是什么意思呀？我没看明白，谁给我讲讲"


def mark_asked(id_or_md5, chat_key: str, *, my_id: str = "") -> dict:
    """记下"我们问过了" ⇒ 写待答状态 + 更新冷却。返回该状态。"""
    st = _load()
    now = time.time()
    st.setdefault("asked_at", {})[str(id_or_md5)] = now
    state = {"emoji_id": int(id_or_md5), "chat_key": str(chat_key),
             "asked_at": now, "my_id": str(my_id), "answered": False}
    st["pending"] = state
    _save(st)
    return state


def pending(chat_key: str = "", *, now: float | None = None) -> dict | None:
    """取"仍在应答窗口内"的待答状态（过期自动清掉并返回 None）。"""
    st = _load()
    p = st.get("pending")
    if not p:
        return None
    if chat_key and str(p.get("chat_key") or "") != str(chat_key):
        return None
    now = time.time() if now is None else float(now)
    if now - float(p.get("asked_at") or 0) > ANSWER_WINDOW_S:
        st["pending"] = None
        _save(st)
        return None
    return p


def judge_prompt(emoji_id: int, answer: str, *, asker_text: str = "") -> str:
    """★ 生成**判别提示词**（交给上层调模型）。

    产出要求：模型**只回 JSON**，字段固定 ⇒ 上层好解析、也便于"解析失败就当没学到"。
    """
    from . import emoji_index as ei
    e = None
    for x in ei.list_for_model():
        if x["id"] == emoji_id:
            e = x
            break
    known = ("已知（可能不准）：画面=%s；使用场景=%s"
             % (e.get("caption", ""), e.get("situation", ""))) if e else "（该条没有已知信息）"
    return (
        "你是群聊语义助手。机器人刚在群里发了一张自定义表情，并问了：\n"
        "「%s」\n"
        "现在有位群友回复：\n「%s」\n\n"
        "关于这张表情的已有信息：%s\n\n"
        "请判断这条回复**是不是对这张表情的解释**，并只输出下面这个 JSON（不要多余文字）：\n"
        '{"is_answer": true/false, '
        '"is_joke": true/false, '
        '"caption": "用一句话说明这张表情表达什么（不是解释就不填）", '
        '"situation": "什么时候该用它（不是解释就不填）", '
        '"moods": ["情绪1","情绪2"], '
        '"reason": "为什么这么判断（20字内）"}\n\n'
        "判定要点：\n"
        "1. **玩笑/反讽/纯玩梗不算有效解释**（`is_joke=true` ⇒ 视为无效，`is_answer=false`）；\n"
        "2. 与表情无关的闲聊、对机器人的吐槽、答非所问 ⇒ `is_answer=false`；\n"
        "3. 只有**确实说清了这张图什么意思**才算 `is_answer=true`；\n"
        "4. 不确定 ⇒ 一律 `false`（**宁可不学，也不学错**）。"
        % (asker_text or question_text(emoji_id), str(answer)[:300], known))


def parse_verdict(raw: str) -> dict:
    """解析模型的 JSON 判定 ⇒ `{ok, is_answer, is_joke, caption, situation, moods, reason}`。

    ⛔ **拿不到合法 JSON ⇒ `ok=False`**（调用方据此"当作没学到"），不许猜。
    """
    out = {"ok": False, "is_answer": False, "is_joke": False,
           "caption": "", "situation": "", "moods": [], "reason": ""}
    s = str(raw or "").strip()
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        s = s[i:j + 1]
    try:
        d = json.loads(s)
    except Exception:
        return out
    if not isinstance(d, dict):
        return out
    out["ok"] = True
    out["is_answer"] = bool(d.get("is_answer"))
    out["is_joke"] = bool(d.get("is_joke"))
    out["caption"] = str(d.get("caption") or "").strip()
    out["situation"] = str(d.get("situation") or "").strip()
    m = d.get("moods")
    out["moods"] = [str(x).strip() for x in m if str(x).strip()] if isinstance(m, list) else []
    out["reason"] = str(d.get("reason") or "")[:60]
    return out


def apply_verdict(emoji_id: int, verdict: dict) -> dict:
    """按判定回填索引 ⇒ `{learned, why}`。

    **只有** `is_answer=True` 且 `is_joke=False` 且**有 caption** 才写库。
    """
    v = verdict or {}
    if not v.get("ok"):
        return {"learned": False, "why": "判定不是合法 JSON ⇒ 不学"}
    if not v.get("is_answer"):
        return {"learned": False, "why": "判定为『不是解释』⇒ 不学（原因：%s）" % v.get("reason", "")}
    if v.get("is_joke"):
        return {"learned": False, "why": "判定为『玩笑/反讽』⇒ 不学（原因：%s）" % v.get("reason", "")}
    if not v.get("caption"):
        return {"learned": False, "why": "没有给出可用说明 ⇒ 不学"}
    from . import emoji_index as ei
    ok = ei.learn(emoji_id, caption=v["caption"], situation=v.get("situation") or "",
                  moods=v.get("moods") or None, confidence="high",
                  meme="（由群友在对话中解释后回填）")
    # 清待答状态
    st = _load()
    st["pending"] = None
    _save(st)
    return {"learned": bool(ok), "why": "已回填并升为 high" if ok else "回填失败（编号找不到）"}


def stats() -> dict:
    from . import emoji_index as ei
    st = _load()
    return {"low_total": len(ei.need_web()),
            "pending": bool(st.get("pending")),
            "asked_count": len(st.get("asked_at") or {})}
