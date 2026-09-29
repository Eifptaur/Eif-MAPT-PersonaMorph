# -*- coding: utf-8 -*-
"""**真值库 + 主动学习队列**：把"素材"变成"真值"，并且**尽量只让人确认不确定的那些**。

## 为什么要有它（现状的瓶颈）
真值现在**硬编码在 `scripts/ocr_bench_web.py` 的 `CASES` 里**（靠人改代码）。
素材再多也不会自动变成能力 —— 缺的是这条流水线：**自动提候选 → 标不确定性 → 人只审不确定的 → 入库**。

## 三层来源（与 OCR 基准的 tier 口径一致，不许混）
  · `human`      —— **人核过**（看原图确认）。**只有这一层能进"硬指标"**。
  · `auto:<机制>` —— 机制自动确认（如 `auto:dual_agree`＝两引擎读数一致）。
  · `proposed`   —— **待审候选**（还没人看）。
⛔ **防自证纪律（本模块最重要的一条）**：每条真值都记 `produced_by`（是哪个引擎/版本产出的），
   并且 `truths()` 默认**只返回人核过的**、`hard_only=True` 显式排除自动确认项 ——
   否则就成了"拿模型自己的输出考自己"（我们已经在 tier-2 上踩过一次）。

## 主动学习（"只让人确认不确定的"）
候选带 `uncertainty`（0~1，越大越不确定）。`pending()` **按不确定性降序**出队；
`review_saving()` 如实报"审 K 条能定多少条"，让"省了多少人工"可量化，而不是感觉。
"""
from __future__ import annotations

import io
import json
import logging
import os
import threading
import time

from . import persist # noqa: E402 原子写（固定名 .tmp 的坑踩过，统一走它）

log = logging.getLogger("persona-morph")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "truth_store.json")
#: 每类真值最多留这么多条（有界：真值库不能无限涨）
MAX_ITEMS = 4000
_LOCK = threading.Lock()

#: 来源分层（越靠前越可信）
SRC_HUMAN = "human"
SRC_PROPOSED = "proposed"


def _load() -> dict:
    try:
        with io.open(PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception as e:  # noqa: BLE001
        log.debug("真值库读不出来（按空库起）：%s", e)
        return {}


def _save(db: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        return bool(persist.atomic_write_json(PATH, db, indent=1))
    except Exception as e:  # noqa: BLE001
        log.warning("真值库落盘失败（本次不落，内存里还在）：%s", e)
        return False


def _key(kind: str, ref: str, value: str) -> str:
    return "%s|%s|%s" % (kind, ref, value)


def add(kind: str, ref: str, value: str, *, source: str = SRC_PROPOSED,
        uncertainty: float = 1.0, produced_by: str = "", note: str = "",
        box=None, src: str = "") -> dict:
    """加一条真值（或候选）。**`produced_by` 必须写明是谁产出的**（防自证）。

    `kind`：`ocr_text`（图上的字）/ `target`（某个命名目标在某坐标）。
    `ref`：定位（ocr_text 用图片文件名；target 用 环境键|目标名）。
    `source`：`human` / `auto:<机制>` / `proposed`。
    """
    if source != SRC_HUMAN and not produced_by:
        # ⛔ 机器产出的真值**必须**记下是谁产出的：否则以后没法把"自己考自己"排掉
        return {"ok": False, "why": "非人核真值必须写明 produced_by（防自证）"}
    k = _key(kind, ref, value)
    with _LOCK:
        db = _load()
        it = db.get(k) if isinstance(db.get(k), dict) else {}
        it.update({"kind": kind, "ref": ref, "value": value, "source": source,
                   "uncertainty": round(float(uncertainty), 3),
                   "produced_by": produced_by, "note": str(note)[:200],
                   "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        if box:
            # ⛔ 审核要能**看见那块像素**：只给「哪张图 + 哪个字串」是审不了的
            try:
                it["box"] = [int(box[0]), int(box[1]), int(box[2]), int(box[3])]
            except Exception as e:  # noqa: BLE001
                log.debug("候选 box 形状不对（忽略）：%s", e)
        if src:
            it["src"] = str(src)      # 原图**完整路径**（`ref` 只是文件名，用来做键）
        db[k] = it
        if len(db) > MAX_ITEMS:
            # 淘汰：先丢"待审里最确定的"（信息量最少）；**人核过的永不淘汰**
            _cand = [kk for kk, vv in db.items()
                     if isinstance(vv, dict) and vv.get("source") == SRC_PROPOSED]
            _cand.sort(key=lambda kk: (db[kk].get("uncertainty") or 0))
            for kk in _cand[:max(1, len(db) - MAX_ITEMS)]:
                db.pop(kk, None)
        _save(db)
    return {"ok": True, "key": k}


def confirm(kind: str, ref: str, value: str, note: str = "") -> dict:
    """人核过：把候选升为 `human`（**只有这里能进硬指标**）。"""
    k = _key(kind, ref, value)
    with _LOCK:
        db = _load()
        it = db.get(k)
        if not isinstance(it, dict):
            it = {"kind": kind, "ref": ref, "value": value}
        it.update({"source": SRC_HUMAN, "uncertainty": 0.0,
                   "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        if note:
            it["note"] = str(note)[:200]
        db[k] = it
        _save(db)
    return {"ok": True, "key": k}


def reject(kind: str, ref: str, value: str, why: str = "") -> dict:
    """人否决（这条不是真值）：删掉并留痕。"""
    k = _key(kind, ref, value)
    with _LOCK:
        db = _load()
        it = db.pop(k, None)
        if isinstance(it, dict):
            db.setdefault("_rejected", {})[k] = {"why": str(why)[:160],
                                                 "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        _save(db)
    return {"ok": True, "removed": bool(it)}


def truths(kind: str = None, ref: str = None, human_only: bool = True) -> list:
    """取真值。**默认只给人核过的**（`human_only=True`）—— 硬指标只许用这一层。"""
    with _LOCK:
        db = _load()
    out = []
    for k, v in (db or {}).items():
        if k.startswith("_") or not isinstance(v, dict):
            continue
        if kind and v.get("kind") != kind:
            continue
        if ref and v.get("ref") != ref:
            continue
        if human_only and v.get("source") != SRC_HUMAN:
            continue
        out.append(dict(v))
    return out


def pending(limit: int = 20, kind: str = None) -> list:
    """**待审队列**：按不确定性**降序**（越不确定越先让人看）。"""
    with _LOCK:
        db = _load()
    q = [dict(v) for k, v in (db or {}).items()
         if isinstance(v, dict) and not k.startswith("_") and v.get("source") == SRC_PROPOSED]
    if kind:
        q = [v for v in q if v.get("kind") == kind]
    q.sort(key=lambda v: -(v.get("uncertainty") or 0))
    return q[:max(1, int(limit))]


def stats() -> dict:
    with _LOCK:
        db = _load()
    n_h = n_a = n_p = 0
    for k, v in (db or {}).items():
        if k.startswith("_") or not isinstance(v, dict):
            continue
        s = str(v.get("source") or "")
        if s == SRC_HUMAN:
            n_h += 1
        elif s == SRC_PROPOSED:
            n_p += 1
        else:
            n_a += 1
    return {"human": n_h, "auto": n_a, "proposed": n_p, "total": n_h + n_a + n_p,
            "auto_ratio": (n_a / float(max(1, n_h + n_a))) if (n_h + n_a) else None}


#: **逐条人看**的门槛：不确定性 ≥ 它的候选进"必须人看"清单（默认 0.7＝另一引擎没有相近读数）。
#: 低于门槛的不是"真值"、只是"低风险候选"——**它们仍然不进硬指标**（只有 `human` 才进）。
REVIEW_ABOVE = 0.7


def review_saving(review_above: float = None) -> dict:
    """如实算"主动学习到底省了多少逐条人工"。

    ⛔ 口径必须写清（否则很容易吹）：能"免于逐条人看"的＝
       ① 两引擎一致的（`auto:*`，可直接接受）；② 不确定性**低于**门槛的（低风险候选，机器判断）。
       真正**必须逐条看**的只有 ≥ 门槛那一档。
       ⚠️ ① ② 都**不是人核真值**：硬指标仍然只能用 `truths(human_only=True)` 取到的东西。
    """
    thr = REVIEW_ABOVE if review_above is None else float(review_above)
    st = stats()
    with _LOCK:
        db = _load()
    props = [v for k, v in (db or {}).items()
             if isinstance(v, dict) and not k.startswith("_") and v.get("source") == SRC_PROPOSED]
    must = sum(1 for v in props if (v.get("uncertainty") or 0) >= thr)
    total = st["total"]
    return {"total": total, "auto_ok": st["auto"], "proposed": st["proposed"],
            "must_review": must, "threshold": thr,
            "saved_ratio": (1.0 - must / float(total)) if total else None,
            "pending_top": [(v.get("ref"), v.get("value", "")[:14], v.get("uncertainty"))
                            for v in pending(limit=3)]}


def crop_for(item: dict, out_dir: str, name: str = "") -> str:
    """把候选所在的**那块像素**裁出来存成小图（供人工审核）。返回路径；裁不了返回空。

    ⛔ 为什么要它：审核的输入必须是**像素**，不是一串字。只给「哪张图的哪个字串」，
       人没法判断"这字到底是不是这么写的"（这类候选往往就是小字/形近字）。
    """
    try:
        box = item.get("box")
        src = item.get("src")
        if not box or not src or not os.path.exists(src):
            return ""
        from PIL import Image
        os.makedirs(out_dir, exist_ok=True)
        x, y, w, h = [int(v) for v in box[:4]]
        _pad = 6                                   # 四周留点边，人眼更好评
        im = Image.open(src).convert("RGB")
        W, H = im.size
        l, t = max(0, x - _pad), max(0, y - _pad)
        r, b = min(W, x + max(1, w) + _pad), min(H, y + max(1, h) + _pad)
        if r <= l or b <= t:
            return ""
        out = os.path.join(out_dir, (name or "crop") + ".png")
        # 放大 3 倍：这类候选往往就是小字，原尺寸人眼也难判
        im.crop((l, t, r, b)).resize(((r - l) * 3, (b - t) * 3), 1).save(out)
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("裁候选小图失败（该项审核时看不到图）：%s", e)
        return ""


def export_pending(out_dir: str, limit: int = 200, kind: str = None) -> dict:
    """把待审队列导出成**人能直接审**的一包：`待审清单.md` + 每条的放大裁剪图。

    这是"只让人确认不确定的"真正落地的那一步 —— 没有它，队列只是数据库里的一堆行。
    """
    items = pending(limit=max(1, int(limit)), kind=kind)
    os.makedirs(out_dir, exist_ok=True)
    lines = ["# 待审真值清单", "",
             "来源：`data/truth_store.json` 里 `source=proposed` 的候选，按**不确定性降序**。",
             "每条的 `crops/pN.png` 是那一块的放大裁剪（3 倍）——**看图逐条判**，别只看字串。", "",
             "| # | 可疑度 | 字串 | 来源图 | 坐标 | 为什么可疑 |",
             "|---|---|---|---|---|---|"]
    n_crop = 0
    for i, it in enumerate(items, 1):
        _c = crop_for(it, os.path.join(out_dir, "crops"), "p%d" % i)
        n_crop += 1 if _c else 0
        _b = it.get("box") or []
        lines.append("| %d | %s | `%s` | %s | %s | %s |" % (
            i, it.get("uncertainty"), str(it.get("value"))[:24],
            os.path.basename(str(it.get("src") or "")) or str(it.get("ref")),
            ("%s,%s,%s,%s" % tuple(_b[:4])) if len(_b) >= 4 else "-",
            str(it.get("note") or "")[:40]))
    lines += ["", "## 怎么处理", "",
              "· 确认对：`truth ok <序号>`（pmctl 或控制台「真值审核」面板）；",
              "· 确认错：`truth no <序号>`（从库里删掉并留痕，不会被当成负面样本乱用）。"]
    p_md = os.path.join(out_dir, "待审清单.md")
    try:
        with io.open(p_md, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:  # noqa: BLE001
        log.warning("写待审清单失败：%s", e)
        p_md = ""
    return {"ok": True, "out_dir": out_dir, "count": len(items), "crops": n_crop, "list": p_md}


def clear(reason: str = "") -> int:
    with _LOCK:
        db = _load()
        n = len([k for k in db if not k.startswith("_")])
        try:
            if os.path.exists(PATH):
                os.remove(PATH)
        except Exception as e:  # noqa: BLE001
            log.warning("真值库清空失败：%s", e)
    log.info("真值库已清空（%d 条）%s", n, ("：%s" % reason) if reason else "")
    return n
