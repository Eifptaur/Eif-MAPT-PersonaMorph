# -*- coding: utf-8 -*-
"""降级守卫 —— **局部降级不成立时必须整体降级**（P0-2）。

**要解决的病**：某个精确判据**只在某一条渲染路径下成立**，却被带到了另一条路径上。
最典型的后果是"**自己拍到自己**"式的假通过：

    绿底带判据在「浅色主题 + 100% 缩放」下量得很准 ⇒ 拿它去判「深色主题 + 150% 缩放」
    ⇒ 深色下绿底带的色值完全不同 ⇒ 判据恒判"没高亮" ⇒
    程序"确认过"当前会话后**实际发到了另一个会话**。

**核心机制**（三件事，缺一不可）：

1. **显式矩阵** `(渲染路径, 主题, DPI 档) → 允许使用的判据集合`。
   矩阵里**没有的组合**一律 **fail-closed**（判"不能操作"）——
   **不沿用**任何其它路径的判据。
2. **判据必须报"我在哪条路径下成立"**：判据本身带 `path` 标签，调用方按标签查矩阵，
   而不是"看着像就拿来用"。
3. **降级要降干净**：从"绿底带"退到"像素跨度"时，**所有依赖前一条判据的后续步骤
   必须一并降掉** —— 只换判据、不换后续步骤，等于用一个弱的判据去喂一个强的结论。

**为什么用"路径"而不是"版本号"做键**：版本一变，往往整批路径一起变；
但真正决定"这条判据成不成立"的是**渲染条件**（主题/缩放/窗口尺寸档）——
同一版本下换个主题就够让它失效了。所以键取渲染条件，版本号只作为一个附加分量。
"""
from __future__ import annotations

import logging

log = logging.getLogger("persona-morph")

#: 判据标识 —— 与 `chat_header` / `chat_ocr` 里真实用到的判据名对齐（不是另起一套）。
CRIT_GREEN_BAND = "green_band"      # 会话列表绿底高亮带（色值敏感，对主题/缩放敏感）
CRIT_PIXEL_SPAN = "pixel_span"      # 像素跨度（比绿底带弱，但主题无关）
CRIT_HEADER_TEXT = "header_text"    # 会话头文字带 OCR
CRIT_ROW_TIME = "row_time"          # 高亮行时间 × 库（屏幕与库两个独立来源）

#: **降级链**：强判据不成立时，允许退到哪些更弱的判据（顺序＝优先级）。
#: ⛔ 只列"退过去仍然成立"的那些 —— 例如绿底带在深色主题下不成立时，
#:    **不能**退到"还是靠颜色"的判据（换个色值的绿底带同样不成立），只能退到**与颜色无关**的。
_DOWNGRADE_CHAIN = {
    CRIT_GREEN_BAND: [CRIT_ROW_TIME, CRIT_HEADER_TEXT, CRIT_PIXEL_SPAN],
    CRIT_ROW_TIME: [CRIT_HEADER_TEXT, CRIT_PIXEL_SPAN],
    CRIT_HEADER_TEXT: [CRIT_PIXEL_SPAN],
    CRIT_PIXEL_SPAN: [],
}

#: ★ 矩阵本体：`(渲染路径, 主题) → 允许使用的判据集合`。
#:
#: 渲染路径目前两档（与 `chat_header` / `chat_ocr` 的实际取帧路径对应）：
#:   · `"row"` —— 从**会话列表**取帧（绿底带在这里可见）；
#:   · `"header"` —— 从**会话头**取帧（看不到列表，绿底带判据此处根本不适用）。
#:
#: **为什么 DPI 不单独做键**：本项目已有运行时尺子（`ui_adapt.detect_scale()`
#: ＋ 图内绿底带高度），任何非标 DPI 都算得出比例。把 DPI 单列成键 ⇒ 需要一张
#: "各 DPI 的常量表"，而常量表在非标缩放下必然缺格 ⇒ 反而制造 fail-closed 假阳性。
#: ⇒ 这里只对**已知会翻转判据的**两个维度（路径、主题）建键；DPI 交给运行时尺子。
_MATRIX = {
    ("row", "light"): {CRIT_GREEN_BAND, CRIT_ROW_TIME, CRIT_HEADER_TEXT, CRIT_PIXEL_SPAN},
    ("row", "dark"): {CRIT_ROW_TIME, CRIT_HEADER_TEXT, CRIT_PIXEL_SPAN},
    ("row", "whale"): {CRIT_GREEN_BAND, CRIT_ROW_TIME, CRIT_HEADER_TEXT, CRIT_PIXEL_SPAN},
    ("header", "light"): {CRIT_HEADER_TEXT, CRIT_PIXEL_SPAN},
    ("header", "dark"): {CRIT_HEADER_TEXT, CRIT_PIXEL_SPAN},
    ("header", "whale"): {CRIT_HEADER_TEXT, CRIT_PIXEL_SPAN},
}


def allowed(path: str, theme: str) -> set:
    """这张 `(路径, 主题)` 组合**允许使用**的判据集合。

    ⛔ 组合不在矩阵里 ⇒ 返回**空集**（不是"默认全允许"）。
       空集是 fail-closed 的实现形态：调用方拿着空集，任何一个判据都查不过 ⇒
       判"不能操作"。**这正是要的效果** —— 不认识的渲染条件下宁可不动手，
       也不要拿一个可能不成立的判据去"确认"。
    """
    return set(_MATRIX.get((str(path or ""), str(theme or "")), set()))


def is_allowed(crit: str, path: str, theme: str) -> bool:
    """这条判据在这条路径下允许用吗？"""
    return str(crit or "") in allowed(path, theme)


def downgrade_chain(crit: str) -> list:
    """从 `crit` 出发，允许退到哪些更弱的判据（按优先级）。"""
    return list(_DOWNGRADE_CHAIN.get(str(crit or ""), []))


def pick(crit: str, path: str, theme: str):
    """★ 调用点主入口：要判"当前会话是谁"，在这条路径下**该用哪条判据**？

    返回 `(usable_crit, reason)`：
    - 首选判据本身允许用 ⇒ 直接返回它（`reason` 为空）；
    - 不允许 ⇒ **沿降级链往下找**第一条也在矩阵里的；找到返回它（`reason` 说明降级原因）；
    - 一条都不允许 ⇒ 返回 `(None, 原因)` ⇒ **调用方必须 fail-closed**（判"不能操作"），
      ⛔ **绝不允许**"自己挑一条顺手的用"。

    **为什么降级要返回"换了哪一条"而不是只返回"行不行"**：调用方拿到的判据**变了**，
    依赖它的后续步骤（例如"判据成立后要读哪个区域"）也**必须跟着换** ——
    只告诉它"行"、不告诉它"换成哪条了"，它就会拿旧判据的后续步骤去接新判据的结论。
    """
    _p = str(path or "")
    _t = str(theme or "")
    _allow = allowed(_p, _t)
    _c = str(crit or "")
    if _c in _allow:
        return _c, ""
    for _alt in downgrade_chain(_c):
        if _alt in _allow:
            return _alt, "判据「%s」在（%s／%s）下不适用 ⇒ 降级到「%s」" % (_c, _p, _t, _alt)
    return None, ("渲染条件（路径 %s／主题 %s）下没有任何可用判据 ⇒ 不能操作"
                  "（不沿用其它路径的判据）" % (_p or "?", _t or "?"))


def describe() -> dict:
    """把矩阵摊平成可读结构（供自检界面/诊断展示，避免"矩阵藏在代码里没人看得见"）。"""
    return {
        "criteria": [CRIT_GREEN_BAND, CRIT_PIXEL_SPAN, CRIT_HEADER_TEXT, CRIT_ROW_TIME],
        "chain": {k: list(v) for k, v in _DOWNGRADE_CHAIN.items()},
        "matrix": {"%s/%s" % (k[0], k[1]): sorted(v) for k, v in _MATRIX.items()},
    }


__all__ = ["CRIT_GREEN_BAND", "CRIT_PIXEL_SPAN", "CRIT_HEADER_TEXT", "CRIT_ROW_TIME",
           "allowed", "is_allowed", "downgrade_chain", "pick", "describe"]
