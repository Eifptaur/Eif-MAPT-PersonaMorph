# -*- coding: utf-8 -*-
"""收藏表情语义选择：从收藏列表（文件名=语义）挑最合适的一个序号。
模型判别（离线可缓存）；无模型时用本地规则（按关键词/默认 0）。"""
import os, glob, re
import logging

log = logging.getLogger("persona-morph")

EMOJI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "emojis")


def list_emojis() -> list:
    """收藏夹里的表情清单。

    ⚠️ 两个字段各有用途，别合并：
      · `name`＝**去掉扩展名**的名字 —— 喂给模型挑表情用（`pick()`）、也是 `send_emoji` 的键，
        历史口径，**不许改**（改了模型上下文与工具入参全变）。
      · `file`＝**带扩展名的真文件名** —— 界面按它拼 `/assets/emoji/<file>` 取图。
        原来没有这个字段，界面只能拿 `name` 去拼 ⇒ **少个 .jpg 就被服务端的文件名白名单
        判 404**（`webui.py:706` 要求 `xxx.(jpg|png|gif…)`）⇒ 表情包收藏夹**一张图都显示不出来**。
      · `.thumb.*` 是**缩略图缓存**，不是独立表情 ⇒ 跳过，免得数量虚高、点进去还是同一张。
    """
    try:
        files = sorted(glob.glob(os.path.join(EMOJI_DIR, "*")),
                       key=lambda p: os.path.getmtime(p), reverse=True)
        out = []
        for f in files:
            base = os.path.basename(f)
            if ".thumb." in base: # 缩略图不是表情本体
                continue
            try:
                sz = os.path.getsize(f)
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                sz = 0
            out.append({"index": len(out), "name": os.path.splitext(base)[0],
                        "file": base, "size": sz})
        return out
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
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
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return 0
