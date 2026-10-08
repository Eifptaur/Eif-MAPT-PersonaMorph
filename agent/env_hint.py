# -*- coding: utf-8 -*-
"""环境提示（第 150 项）：把"这台机器适不适合"变成**一句人话**，接在新手引导后面。

## 为什么要它

实测：**深色主题会让截图的 OCR 错字率大幅上升**（同一批会话行的相似度从 **0.86 掉到 0.57**）；
行高只跟 DPI/显示缩放走 ⇒ 把环境收敛到「**浅色 ＋ 默认字号/100% 缩放**」，
整条链的适配面能小一个量级。⇒ 值得**在开箱时提醒一句**。

## 三条口径（写进代码，也写进判据）

1. ⛔ **不强制**：只提示，**允许用户不改** —— 不改就走降级路径（更慢、更可能要人工兜底）。
   文案里**必须**出现这一点，否则就成了"逼用户改系统设置"。
2. ⛔ **测不到就说测不到**：主题要**真机截图**才判得出（`chat_header.detect_polarity`），
   DPI/缩放能直接读（`ui_adapt.detect_scale`），**微信字号**目前没有可靠的读法。
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

#: 缩放容差（浮点比较别用 ==）
_SCALE_TOL = 0.01

#: 「不改也能用」这句**必须**在文案里 —— 判据盯着它（防哪天被删掉，把"提示"变成"强制"）
SOFT_NOTE = "不改也能用，只是识别会差一些（会走降级路径、可能要人工兜底）。"


def _read_scale():
    """读显示缩放（`ui_adapt.detect_scale()`；读不到 ⇒ `None`，**不猜**）。"""
    try:
        from . import ui_adapt
        v = ui_adapt.detect_scale()
        return float(v) if v else None
    except Exception as e:  # noqa: BLE001
        log.debug("读显示缩放失败（按未测处理）：%s", str(e)[:80])
        return None


def _read_ocr():
    """OCR 引擎是否就绪（读不到 ⇒ `None`）。"""
    try:
        from . import chat_ocr
        return bool(chat_ocr.available())
    except Exception:  # noqa: BLE001
        return None


def check(*, theme=None, scale=None, font=None, ocr_ready=None, probe: bool = True) -> dict:
    """判这台机器的环境 ⇒ `{level, ok, title, text, items, detail}`。

    参数都可以显式传入（判据与真机都这么调）；不传且 `probe=True` ⇒ 用**能真读到的**那几项
    （缩放、OCR 就绪）现读；**读不到的一律记 `None`（未测），⚠️ 绝不猜成"达标"**。

    `theme`：`"light"` / `"dark"` / `None`（未测）。微信主题**必须**靠真机截图判
    （`chat_header.detect_polarity`），本模块不自己截图。
    `scale`：显示缩放（`1.0` ＝ 100%）。
    `font`：微信字号档（目前**没有可靠读法** ⇒ 调用方一般传 `None`）。
    """
    _scale = scale if scale is not None else (_read_scale() if probe else None)
    _ocr = ocr_ready if ocr_ready is not None else (_read_ocr() if probe else None)
    items, texts = [], []

    def add(key, level, text, why=""):
        items.append({"key": key, "level": level, "text": text, "why": why})

    # ① 主题
    _th = str(theme or "").strip().lower()
    if _th == "dark":
        add("theme", "warn", "把微信主题切到**浅色**",
            "实测：深色主题下截图的识别错误明显变多（相似度 0.86 → 0.57）")
    elif _th == "light":
        add("theme", "ok", "微信主题：浅色")
    else:
        add("theme", "unknown", "微信主题没测到（要截一张图才判得出）")

    # ② 显示缩放
    if _scale is None:
        add("scale", "unknown", "显示缩放没测到")
    elif abs(float(_scale) - 1.0) <= _SCALE_TOL:
        add("scale", "ok", "显示缩放：100%")
    else:
        add("scale", "warn", "把 Windows 显示缩放调回 **100%%**（现在是 %d%%）" % round(float(_scale) * 100),
            "行高只跟缩放走 ⇒ 非 100%% 时定位与识别都要多绕一层")

    # ③ 微信字号
    if font is None:
        add("font", "unknown", "微信字号没测到（目前没有可靠的读法）")
    elif str(font).strip() in ("", "default", "标准", "0"):
        add("font", "ok", "微信字号：默认")
    else:
        add("font", "warn", "把微信字号调回**默认**（现在不是默认档）",
            "字号会改行高 ⇒ 非默认档等于换了一套几何")

    # ④ OCR 引擎（读得到才说）
    if _ocr is None:
        add("ocr", "unknown", "OCR 引擎状态没测到")
    elif _ocr:
        add("ocr", "ok", "OCR 引擎：就绪")
    else:
        add("ocr", "warn", "OCR 引擎没就绪（会退到较差的引擎）",
            "装 RapidOCR 会准很多；不装也能跑，只是慢且更容易认错")

    _warns = [x for x in items if x["level"] == "warn"]
    _unknown = [x for x in items if x["level"] == "unknown"]
    level = "warn" if _warns else ("partial" if _unknown else "ok")
    for x in _warns:
        texts.append("· " + x["text"])
    if level == "ok":
        text = "环境没问题（主题、缩放、字号、OCR 都达标）。"
    elif level == "partial":
        text = "环境没发现问题；有 %d 项没测到（%s）。" % (
            len(_unknown), "、".join({"theme": "主题", "scale": "缩放", "font": "字号",
                                      "ocr": "OCR"}.get(x["key"], x["key"]) for x in _unknown))
    else:
        text = "建议调一下运行环境，识别会稳很多：\n" + "\n".join(texts) + "\n" + SOFT_NOTE
    return {"level": level, "ok": (level == "ok"), "title": "运行环境", "text": text,
            "items": items, "detail": {"theme": _th or None, "scale": _scale, "font": font,
                                       "ocr_ready": _ocr}}
