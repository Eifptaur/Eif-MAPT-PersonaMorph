# -*- coding: utf-8 -*-
"""内心判断留痕（**低成本**）：把"看了但决定不回 / 回了什么 / 为什么"落一行 JSONL。

为什么要有它（2026-09-15 对照审计，用户问"思考未入记忆我们有没有"）：
  我们原本只有"发出去的话"会进记忆整理，**模型与闸门的内部判断一条都不进**——
  海龟汤这类"想了很多、只发一句"的玩法，推理与取舍全丢。
  打开模型思考（`api.thinking`）能拿到推理文本，但它**按输出价计费**（大头上限），
  为存档而开思考不划算 ⇒ 走这条路：把**我们自己就有的判断**（档位 reason、
  "未触发"的结论、推理片段、最终发了什么）落成廉价的本地痕迹，再喂给记忆整理。

三条纪律：
  ① 只写本地 `logs/thoughts.jsonl`（永不出网、永不进群）；
  ② 单文件到 2 MB 就轮转一份 `.1`，不留无界增长；
  ③ 写失败一律静默（留痕不许影响聊天主流程）。
"""
import io
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, "logs", "thoughts.jsonl")
MAX_BYTES = 2 * 1024 * 1024


def _rotate():
    try:
        if os.path.exists(FILE) and os.path.getsize(FILE) > MAX_BYTES:
            bak = FILE + ".1"
            if os.path.exists(bak):
                os.remove(bak)
            os.replace(FILE, bak)
    except Exception:
        pass


def note(chat_key: str, kind: str, **fields) -> dict:
    """记一条内心判断。返回落盘的条目（写失败也返回条目，只是没落盘）。"""
    item = {"ts": int(time.time() * 1000), "chat": str(chat_key or ""), "kind": str(kind or "")}
    for k, v in (fields or {}).items():
        if v is None or v == "":
            continue
        item[str(k)] = v if isinstance(v, (int, float, bool)) else str(v)[:600]
    try:
        os.makedirs(os.path.dirname(FILE), exist_ok=True)
        _rotate()
        with io.open(FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return item


def recent(chat_key: str = "", limit: int = 20) -> list:
    """读回最近 N 条（按 chat 过滤；读不到返回空表，不抛）。"""
    out = []
    try:
        if not os.path.exists(FILE):
            return []
        with io.open(FILE, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    it = json.loads(line)
                except Exception:
                    continue
                if chat_key and str(it.get("chat") or "") != str(chat_key):
                    continue
                out.append(it)
    except Exception:
        return []
    return out[-max(1, int(limit)):]


def format_for_memory(chat_key: str, limit: int = 12) -> str:
    """给记忆整理用的一段文本（"机器人的内心判断"）——没有就返回空串。"""
    rows = recent(chat_key, limit)
    if not rows:
        return ""
    kind_cn = {"tier": "要不要回应", "turn_end": "这一轮的结果", "reasoning": "推理片段"}
    lines = []
    for it in rows:
        k = kind_cn.get(str(it.get("kind")), str(it.get("kind")))
        bits = []
        if it.get("should") is not None:
            bits.append("回应=%s" % ("是" if it.get("should") else "否"))
        for key, label in (("why", "原因"), ("tier", "档"), ("src", "档位来源"),
                           ("snippet", "触发内容"), ("sent", "发出"), ("think", "内心")):
            if it.get(key):
                bits.append("%s：%s" % (label, str(it[key])[:160]))
        if bits:
            lines.append("- [%s] %s" % (k, "；".join(bits)))
    return "\n".join(lines)
