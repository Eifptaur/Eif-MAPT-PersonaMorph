# -*- coding: utf-8 -*-
"""**向群友请教**：低置信度表情的语义，靠"问"补全。

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
import logging

log = logging.getLogger("persona-morph")

_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "emoji_ask_state.json")

#: 应答窗口（秒）：超过这么久才回的，**不算**对这次提问的回答。
#: ⚠️ 它是**兜底默认**（配置读不到时用它）——生效值请看 `cfg()` 的 `answer_window_s`。
ANSWER_WINDOW_S = 120.0
#: 同一条表情的提问冷却（秒）：避免反复问同一张。同上，它是兜底默认，生效值在 `cfg()`。
ASK_COOLDOWN_S = 6 * 3600.0


def cfg() -> dict:
    """「向群友请教」的生效配置（`emoji.curiosity`，键与默认值见 `config.py::DEFAULT_CONFIG`）。

    返回 `{enabled, probability, cooldown_s, answer_window_s}`。

    ⛔ 为什么保留上面两个模块常量而不是直接删：配置模块异常/读不到时要有**确定的兜底**
    （退回加配置之前的行为），而不是"读不到就完全不问"或抛异常。
    ⛔ `probability` 的兜底是 **0.0**（最保守：配置缺失时一次都不问）——它没有历史常量可退，
    而"多问一句"的代价（像刷屏）比"少问一句"大得多。
    """
    try:
        from .config import get_config
        c = ((get_config() or {}).get("emoji") or {}).get("curiosity") or {}
    except Exception: # noqa: BLE001
        c = {}

    def _f(key, dflt):
        v = c.get(key, dflt)
        try:
            return float(dflt) if v is None else float(v)
        except Exception: # noqa: BLE001
            return float(dflt)

    return {"enabled": bool(c.get("enabled", False)),
            "probability": _f("probability", 0.0),
            "cooldown_s": _f("cooldown_s", ASK_COOLDOWN_S),
            "answer_window_s": _f("answer_window_s", ANSWER_WINDOW_S)}


def _load() -> dict:
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return {}


def _save(d: dict) -> None:
    try:
        # 走**唯一收口**：临时名带 pid + 随机后缀（原来固定 `_STATE_PATH + ".tmp"` ——
        # 两个写者会打开同一个临时档、内容互相穿插），同时拿到 `fsync` 与 `os.replace` 的退避重试。
        persist.atomic_write_json(_STATE_PATH, d, indent=1)
    except Exception:
        pass


def pick_low(exclude=None) -> int | None:
    """挑一条**低置信度、且不在冷却期**的表情用于提问 ⇒ 返回 id；没有则 None。

    ⛔ 总开关关着 ⇒ 一律返回 None（**在最小面拦住**，不依赖每个调用方自己记得先查配置）。
    """
    from . import emoji_index as ei
    c = cfg()
    if not c["enabled"]:
        return None
    st = _load()
    asked = st.get("asked_at") or {}
    now = time.time()
    exclude = set(exclude or [])
    for e in ei.need_web():
        i = e["id"]
        if i in exclude:
            continue
        if now - float(asked.get(str(i), 0) or 0) < c["cooldown_s"]:
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
    if now - float(p.get("asked_at") or 0) > cfg()["answer_window_s"]:
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
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
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
    # 清待答状态；学到就顺手留一条"回填记录"（面板要显示「从群友那儿学到了什么」）
    st = _load()
    st["pending"] = None
    if ok:
        st.setdefault("learned", []).append(
            {"at": time.time(), "id": int(emoji_id),
             "caption": str(v["caption"])[:60],
             "situation": str(v.get("situation") or "")[:40]})
        st["learned"] = st["learned"][-20:] # 只留最近 20 条，防状态文件越滚越长
    _save(st)
    return {"learned": bool(ok), "why": "已回填并升为 high" if ok else "回填失败（编号找不到）"}


def stats() -> dict:
    """面板要看的全量口径：生效配置 ＋ 待答状态 ＋ 已问数 ＋ 最近学到的几条。"""
    from . import emoji_index as ei
    st = _load()
    c = cfg()
    pend = st.get("pending") or None
    return {"enabled": c["enabled"],
            "probability": c["probability"],
            "cooldown_s": c["cooldown_s"],
            "answer_window_s": c["answer_window_s"],
            "low_total": len(ei.need_web()),
            "pending": bool(pend),
            "pending_detail": pend,
            "asked_count": len(st.get("asked_at") or {}),
            "learned": list(st.get("learned") or [])[-5:]}
