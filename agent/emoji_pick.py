# -*- coding: utf-8 -*-
"""收藏表情语义选择：从收藏列表（文件名=语义）挑最合适的一个序号。
模型判别（离线可缓存）；无模型时用本地规则（按关键词/默认 0）。"""
import os, glob, json, re

EMOJI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "emojis")


def list_emojis() -> list:
    try:
        files = sorted(glob.glob(os.path.join(EMOJI_DIR, "*")),
                       key=lambda p: os.path.getmtime(p), reverse=True)
        out = []
        for i, f in enumerate(files):
            try:
                sz = os.path.getsize(f)
            except Exception:
                sz = 0
            out.append({"index": i, "name": os.path.splitext(os.path.basename(f))[0], "size": sz})
        return out
    except Exception:
        return []


def pick(context: str = "") -> int:
    """模型判别：给收藏文件名清单+语境，选最合适序号（0 起）。失败→0。"""
    try:
        lids = list_emojis()
        if not lids:
            return 0
        if len(lids) == 1:
            return 0
        names = "；".join("%d. %s" % (x["index"] + 1, x["name"]) for x in lids[:30])
        from agent.llm import chat_completion
        r = chat_completion([{"role": "user", "content":
            "你是表情包选择器。下面是微信收藏的表情包清单（序号.文件名）：\n" + names +
            "\n当前语境：%s\n只输出一个整数=最合适的表情包序号（1 起）。没有合适就输出 0。" % (context or "(无语境)")}])
        m = re.search(r"(?<!\d)(\d{1,2})(?!\d)", (r.get("message") or {}).get("content") or "")
        if m:
            v = int(m.group(1))
            return max(0, min(len(lids) - 1, v - 1))
        return 0
    except Exception:
        return 0
