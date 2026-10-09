# -*- coding: utf-8 -*-
"""环境提示（第 150 项）：把"这台机器适不适合"变成**一句人话**，接在新手引导后面。

## 为什么要它

实测：**深色主题会让截图的 OCR 错字率大幅上升**（同一批会话行的相似度从 **0.86 掉到 0.57**）；
字号会改行高 ⇒ 把环境收敛到「**微信浅色 ＋ 微信标准字号**」，整条链的适配面能小一个量级。
⇒ 值得**在开箱时提醒一句**。

## ⛔ 只问用户"微信那一层"的事（2026-10-10 用户当面纠正）

**用户最多只到微信这个层级，再往下他管不了、也不会。**
⇒ 本模块**只有两项**（都落在微信界面里，用户点两下就能改）：

1. **微信界面 → 浅色模式**
2. **微信字号 → 标准字号**

⛔ **绝不出现**这两类：
  · **显示缩放／DPI** —— 改它会把用户**所有软件**的图标和显示全搞坏（不是"微信的事"）；
  · **OCR 引擎／依赖／驱动就绪** —— 那是**程序自己该搞定的活**，不该让用户管
    （缺就程序自己装/自己降级，**不许**把它做成一条"请用户去装"的选项）。

## 三条口径（写进代码，也写进判据）

1. ⛔ **不强制**：只提示，**允许用户不改** —— 不改就走降级路径（更慢、更可能要人工兜底）。
   文案里**必须**出现这一点，否则就成了"逼用户改系统设置"。
2. ⛔ **测不到就说测不到**：主题要**真机截图**才判得出（`chat_header.detect_polarity`）；
   **微信字号**目前没有可靠的读法。
   ⇒ 三态：`warn`（有不达标项）/ `partial`（没发现问题，但**有几项没测到**）/ `ok`（都测了且都达标）。
   **⛔ 不许把"没测到"当成"达标"** —— 那会让提示永远显示"一切正常"。
3. ⛔ **只读**：本模块**不改任何设置**、不动系统、不动微信（改不改是用户自己的事）。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: 三个档位（`warn` 最需要用户看一眼；`partial` 是"没发现问题但没测全"）
LEVELS = ("ok", "partial", "warn")
LEVEL_CN = {"ok": "正常", "partial": "没发现问题（有几项没测到）", "warn": "建议调整"}

#: 「不改也能用」这句**必须**在文案里 —— 判据盯着它（防哪天被删掉，把"提示"变成"强制"）
SOFT_NOTE = "不改也能用，只是识别会差一些（会走降级路径、可能要人工兜底）。"


def check(*, theme=None, font=None, probe: bool = True) -> dict:
    """判这台机器的环境 ⇒ `{level, ok, title, text, items, detail}`。

    ⛔ **只有两项**（微信浅色、微信标准字号），都落在"用户能改"的层级。

    参数都可显式传入（判据与真机都这么调）。
    `theme`：`"light"` / `"dark"` / `None`（未测）。微信主题**必须**靠真机截图判
    （`chat_header.detect_polarity`），本模块不自己截图。
    `font`：微信字号档（目前**没有可靠读法** ⇒ 调用方一般传 `None`）。
    `probe` 仅为签名兼容保留：本模块**不读本机任何东西**（缩放/OCR 已按用户口径删除）。
    """
    items, texts = [], []

    def add(key, level, text, why=""):
        items.append({"key": key, "level": level, "text": text, "why": why})

    # ① 微信界面主题
    _th = str(theme or "").strip().lower()
    if _th == "dark":
        add("theme", "warn", "请把您的微信界面设成浅色模式。",
            "实测：深色界面下截图的识别错误明显变多（相似度 0.86 → 0.57）")
    elif _th == "light":
        add("theme", "ok", "微信界面：浅色")
    else:
        add("theme", "unknown", "微信界面深浅还没测到（要截一张图才判得出）")

    # ② 微信字号
    if font is None:
        add("font", "unknown", "微信字号还没测到（目前没有可靠的读法）")
    elif str(font).strip() in ("", "default", "标准", "0"):
        add("font", "ok", "微信字号：标准")
    else:
        add("font", "warn", "请把您的字号设为标准字号。",
            "字号会改行高 ⇒ 不是标准档，识别要多绕一层")

    _warns = [x for x in items if x["level"] == "warn"]
    _unknown = [x for x in items if x["level"] == "unknown"]
    level = "warn" if _warns else ("partial" if _unknown else "ok")
    for x in _warns:
        texts.append("· " + x["text"])
    if level == "ok":
        text = "环境没问题（微信浅色、字号标准，都达标）。"
    elif level == "partial":
        text = "环境没发现问题；有 %d 项没测到（%s）。" % (
            len(_unknown), "、".join({"theme": "微信界面深浅", "font": "微信字号"}.get(x["key"], x["key"])
                                   for x in _unknown))
    else:
        text = "建议调一下微信，识别会稳很多：\n" + "\n".join(texts) + "\n" + SOFT_NOTE
    return {"level": level, "ok": (level == "ok"), "title": "运行环境", "text": text,
            "items": items, "detail": {"theme": _th or None, "font": font}}
