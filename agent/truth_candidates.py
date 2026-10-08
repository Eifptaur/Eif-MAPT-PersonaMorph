# -*- coding: utf-8 -*-
"""**候选生成**：从素材（截图）自动提出"可能的真值"，并**标出不确定性**。

## 它做什么、不做什么
  ✅ 提候选：OCR 读出哪些字、哪个命名目标在哪个位置；
  ✅ 标不确定性：给主动学习排队用（人只审不确定的）；
  ⛔ **不直接当真值**：产出全部落在 `truth_store` 的 `proposed` / `auto:<机制>` 两档；
     **只有人核过（`human`）才进硬指标**（`truth_store.truths(human_only=True)` 默认就这口径）。

## 不确定性怎么来（两条都不靠"感觉"，而且**是分级的**）
  ① **OCR 文本**（实测：10 张真实界面图上两引擎一致率只有 21% ⇒ 二值判据省不了多少人工，
     所以按"另一个引擎有没有读出**相似**的东西"分四档）：
       · 两引擎都读到同一串 ⇒ `0`    （`auto:dual_agree`，可自动接受）
       · 另一引擎读到**高度相似**的串（可能是形近误读）⇒ `0.3`（大概率对，值得快速确认）
       · 另一引擎读到**部分相似**的串 ⇒ `0.5`
       · 另一引擎**完全读不到**对应串 ⇒ `0.7`（最该人看的一类：很可能就是我们的读数错了）
  ② **命名目标**：用**最近邻余量**（本目标最优 vs 别的目标最优之差，见 `ui_fingerprint.NEAR_MARGIN`）
     —— 余量越大越确定；余量不足（或没有别的目标可对比）⇒ 不确定性高，进人工队列。

## 实测口径（10 张真实界面图，）
  合计 496 条字串：两引擎一致 103 条（21%，可自动接受）、其余 393 条进人审队列。
  ⇒ 这条流水线的价值**主要不是"省 79% 人工"**，而是**把 496 条按可疑度排队**：
    人只看最可疑的那一撮（`0.7` 那档＝很可能读错），其余按低不确定性快速处理。
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("persona-morph")


def _norm(s: str) -> str:
    from . import chat_ocr as _o
    return _o.norm(str(s or ""))


def from_image(path: str, produced_by: str = "") -> dict:
    """跑两个引擎，把读到的字串提成候选（含不确定性）。返回 `{"auto": [...], "proposed": [...]}`。"""
    from . import chat_ocr as O
    from . import truth_store as TS
    out = {"auto": [], "proposed": [], "why": ""}
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
    except Exception as e:  # noqa: BLE001
        return {"auto": [], "proposed": [], "why": "图打不开：%s" % str(e)[:60]}
    ref = os.path.basename(path)
    try:
        _ra = list(O.recognize(im))
    except Exception as e:  # noqa: BLE001
        log.debug("第一引擎读图失败（%s）：%s", ref, e)
        _ra = []
    try:
        _rb = list(O._rapid_recognize(im))
    except Exception as e:  # noqa: BLE001
        log.debug("第二引擎读图失败（%s）：%s", ref, e)
        _rb = []
    a = [str(x[0]) for x in _ra]
    b = [str(x[0]) for x in _rb]
    # 每个字串记住它**在哪儿**（审核时要能看见那块像素）：优先第一引擎的框
    box_of = {}
    for _it in list(_ra) + list(_rb):
        try:
            box_of.setdefault(_norm(_it[0]), [int(_it[1]), int(_it[2]), int(_it[3]), int(_it[4])])
        except Exception as e:  # noqa: BLE001
            log.debug("候选框读不出（该条审核时看不到图）：%s", e)
    if not a and not b:
        return {"auto": [], "proposed": [], "why": "两个引擎都没读到（不产候选）"}
    na, nb = {_norm(x) for x in a if _norm(x)}, {_norm(x) for x in b if _norm(x)}
    both = na & nb
    only = (na ^ nb)
    _prod = produced_by or ("winrt+rapid" if (a and b) else ("winrt" if a else "rapid"))
    for s in sorted(both):
        TS.add("ocr_text", ref, s, source="auto:dual_agree", uncertainty=0.0,
               produced_by=_prod, note="两引擎读数一致", box=box_of.get(s), src=path)
        out["auto"].append(s)
    for s in sorted(only):
        # ⭐ 分级：另一个引擎**有没有读出相似的东西**，决定这条有多可疑
        _other = nb if s in na else na
        u, note = _grade(s, _other)
        TS.add("ocr_text", ref, s, source=TS.SRC_PROPOSED, uncertainty=u,
               produced_by=_prod, note=note, box=box_of.get(s), src=path)
        out["proposed"].append(s)
    return out


def similar_ratio(a: str, b: str) -> float:
    """归一化相似度 0~1（difflib 编辑相似度）。

    ⛔ 这里**为什么不复用生产里的 `loose_matches`**（第一版就踩了）：它是**字符集 Jaccard + need=3**，
       在**短串**上太粗 —— `通讯录` vs `通讯簿`（3 个字差 1 个）Jaccard 只剩 0.5，永远够不到阈值
       ⇒ 分级失效（实测 T6a 当场红）。给"可疑度"打分要的是**逐字符对齐**的相似度，difflib 正合适。
       生产的名字匹配口径**不动**：那边要的是"宽容但保守"，这边要的是"能分档"。
    """
    try:
        import difflib
        return float(difflib.SequenceMatcher(None, str(a or ""), str(b or "")).ratio())
    except Exception as e:  # noqa: BLE001
        log.debug("相似度计算失败（按不像处理）：%s", e)
        return 0.0


def _grade(s: str, other: set) -> tuple:
    """给"只有一个引擎读到"的串打分：与另一引擎的读数越像 ⇒ 越不可疑。

    返回 `(uncertainty, note)`。三档：很像(≥0.6) 0.3 / 部分像(≥0.4) 0.5 / 不像 0.7。
    """
    if not other:
        return 0.7, "另一引擎什么都没读到 ⇒ 需人看（很可能我们的读数不对）"
    _best, _best_o = 0.0, ""
    for o in other:
        r = similar_ratio(s, o)
        if r > _best:
            _best, _best_o = r, str(o)
    if _best >= 0.6:
        return 0.3, "另一引擎读到高度相似的「%s」（相似度 %.2f）⇒ 大概率对，快速确认即可" % (
            _best_o[:16], _best)
    if _best >= 0.4:
        return 0.5, "另一引擎读到部分相似的「%s」（相似度 %.2f）⇒ 需人看" % (_best_o[:16], _best)
    return 0.7, "另一引擎没有相近读数（最高相似度 %.2f）⇒ 需人看（很可能我们的读数不对）" % _best


def from_targets(gui=None, produced_by: str = "") -> dict:
    """把**命名目标**的现状提成候选：不确定性＝1 − 最近邻余量/NEAR_MARGIN。"""
    from . import truth_store as TS
    from . import ui_fingerprint as F
    out = {"auto": [], "proposed": [], "why": ""}
    try:
        k = F.key(gui)
        bucket = (F.load().get(k) or {})
    except Exception as e:  # noqa: BLE001
        return {"auto": [], "proposed": [], "why": "读指纹库失败：%s" % str(e)[:60]}
    names = [n for n, r in bucket.items() if isinstance(r, dict) and F.variants_of(r)]
    if not names:
        return {"auto": [], "proposed": [], "why": "本环境还没有目标指纹（先取一次指纹）"}
    _prod = produced_by or "fingerprint"
    for nm in names:
        rec = bucket.get(nm) or {}
        _self = rec.get("hash") or (F.variants_of(rec)[0].get("h") if F.variants_of(rec) else "")
        d_other = None
        for onm in names:
            if onm == nm:
                continue
            for v in F.variants_of(bucket.get(onm) or {}):
                d = F.dist(_self, v.get("h", ""))
                if d_other is None or d < d_other:
                    d_other = d
        if d_other is None:
            u, note = 0.3, "本环境没有别的目标可比（认错风险无法评估）⇒ 建议人核一次"
        else:
            margin = d_other - 0          # 本目标对"自己"的距离是 0（拿自己的指纹比自己）
            u = 0.0 if margin >= F.NEAR_MARGIN else max(0.0, 1.0 - margin / float(F.NEAR_MARGIN))
            note = "与最近的其他目标距离 %d（余量门槛 %d）" % (d_other, F.NEAR_MARGIN)
        ref = "%s|%s" % (k, nm)
        src = "auto:fingerprint_margin" if u <= 0.0 else TS.SRC_PROPOSED
        TS.add("target", ref, _self, source=src, uncertainty=u, produced_by=_prod, note=note)
        (out["auto"] if u <= 0.0 else out["proposed"]).append(nm)
    return out
