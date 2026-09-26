# -*- coding: utf-8 -*-
"""人设模板库的**加载器**（数据真身在 `assets/persona/cards.json`）。

为什么数据不在这个文件里：
  · 253 张角色卡是**内容**不是逻辑 —— 它以前以 dict 字面量形式铺满 **10084 行**，
    把"改一张卡"变成"在大文件里改一处字面量"，也让任何"扫内容"的工具（隐私审计/文案检查）
    不得不按 `.py` 语法去解析它；
  · 外置成 JSON 之后，内容就是**数据文件**：可以逐条 diff、可以单独扫描、不会因为一个引号写错
    导致整个模块 import 失败。

口径（**外置不许改语义**）：
  · `PERSONAS` / `PERSONA_CATS` 两个名字**原样保留** ⇒ 14 个导入点一行都不用改；
  · 值全是字符串、**顺序原样**（JSON 保序；导出时已逐字节对比过改前基线，含顺序）；
  · 文件末尾那次 `enrich_all(PERSONAS)` 的位置与语义不变（就地补全卡片字段，各调用方照旧能读到补全后的内容）。
"""
from __future__ import annotations

import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CARDS = os.path.join(_ROOT, "assets", "persona", "cards.json")

with open(_CARDS, encoding="utf-8") as _fh:
    _data = json.load(_fh)

PERSONAS: dict = _data["PERSONAS"]
PERSONA_CATS: dict = _data["PERSONA_CATS"]

from .persona_enrich import enrich_all  # noqa: E402

enrich_all(PERSONAS)
