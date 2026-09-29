# -*- coding: utf-8 -*-
"""**素材→真值流水线**判据：自动提候选 + 主动学习排队；重点是**防自证**与"省人工"的口径不许吹。

守这些：
  T1 非人核真值**必须**写明 `produced_by`（防自证：以后要能把"自己考自己"排掉）
  T2 ★ **分层**：`truths()` 默认**只给人核过的**；`human_only=False` 才拿全部
  T3 ★ **防自证锚**：机器自动确认的（`auto:*`）**取不出来**当硬指标
  T4 待审队列**按不确定性降序**（人先看最不确定的）
  T5 候选生成：两引擎一致 ⇒ 自动可接受（u=0）；只有单引擎读到 ⇒ 待审（u ∈ {0.3,0.5,0.7}）
  T6 分级：另一引擎读到**高度相似**的 ⇒ 0.3；**完全不像** ⇒ 0.7
  T7 confirm 升为 `human`、reject 删除并留痕
  T8 有界 + 线程安全 + 落盘；**人核过的永不淘汰**
  T9 `review_saving` 口径算得对（能省多少**逐条人看**）

跑法：`runtime\\python\\python.exe scripts\\truthflow_selftest.py`
"""
import io
import os
import sys
import tempfile
import threading

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
os.environ.setdefault("PM_NO_WINDOW_TOUCH", "1")
import _srcmatch as _sm # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


from agent import truth_candidates as TC # noqa: E402
from agent import truth_store as TS # noqa: E402

_tmp = os.path.join(tempfile.mkdtemp(prefix="truthflow_"), "truth.json")
_real = TS.PATH
try:
    TS.PATH = _tmp
    TS.clear()

    print("── T1~T3：分层与防自证（本模块最重要的一条）──")
    r_bad = TS.add("ocr_text", "a.png", "hello", source="auto:dual_agree", produced_by="")
    ok("T1 非人核真值**必须**写 `produced_by`（否则拒绝入库）", r_bad.get("ok") is False,
       str(r_bad.get("why"))[:40])
    TS.add("ocr_text", "a.png", "设置", source=TS.SRC_HUMAN)
    TS.add("ocr_text", "a.png", "通讯录", source="auto:dual_agree", produced_by="winrt+rapid")
    TS.add("ocr_text", "a.png", "待审串", source=TS.SRC_PROPOSED, uncertainty=0.7,
           produced_by="winrt")
    _h = TS.truths("ocr_text")
    _all = TS.truths("ocr_text", human_only=False)
    ok("T2 ★ `truths()` 默认只给人核过的", len(_h) == 1 and _h[0]["value"] == "设置",
       "人核 %d 条 / 全部 %d 条" % (len(_h), len(_all)))
    ok("T3 ★ 防自证：机器自动确认的取不出来当硬指标（`auto:*` 不在默认结果里）",
       all(v["source"] == TS.SRC_HUMAN for v in _h))

    print("── T4/T9：队列与「省人工」口径 ──")
    for i in range(12):
        TS.add("ocr_text", "b.png", "候选%d" % i, source=TS.SRC_PROPOSED,
               uncertainty=(i % 5) / 4.0, produced_by="winrt")
    _q = TS.pending(limit=5)
    ok("T4 待审队列按不确定性**降序**（先看最不确定的）",
       all((_q[i].get("uncertainty") or 0) >= (_q[i + 1].get("uncertainty") or 0)
           for i in range(len(_q) - 1)), str([v.get("uncertainty") for v in _q]))
    _sv = TS.review_saving()
    _exp_must = sum(1 for k, v in TS._load().items()
                    if isinstance(v, dict) and v.get("source") == TS.SRC_PROPOSED
                    and (v.get("uncertainty") or 0) >= TS.REVIEW_ABOVE)
    ok("T9 `review_saving` 的 must_review / saved_ratio 算得对",
       _sv["must_review"] == _exp_must
       and abs(_sv["saved_ratio"] - (1 - _exp_must / float(_sv["total"]))) < 1e-9,
       "total=%d must=%d saved=%.2f" % (_sv["total"], _sv["must_review"], _sv["saved_ratio"]))

    print("── T5/T6：候选生成与分级（打桩两引擎读数，确定性）──")
    from PIL import Image # noqa: E402
    from agent import chat_ocr as O # noqa: E402
    _p = os.path.join(tempfile.mkdtemp(), "x.png")
    Image.new("RGB", (200, 80), (250, 250, 250)).save(_p)
    _real_rec, _real_rapid = O.recognize, O._rapid_recognize
    try:
        O.recognize = lambda im, **kw: [("发送", 0, 0, 1, 1), ("通讯录", 0, 0, 1, 1),
                                        ("设置面板", 0, 0, 1, 1)]
        O._rapid_recognize = lambda im, **kw: [("发送", 0, 0, 1, 1), ("通讯簿", 0, 0, 1, 1)]
        TS.clear()
        _r = TC.from_image(_p)
        _items = {v["value"]: v for v in TS.truths("ocr_text", human_only=False)}
        ok("T5 两引擎一致的串 ⇒ 自动可接受（u=0，来源 auto:dual_agree）",
           "发送" in _items and _items["发送"]["uncertainty"] == 0
           and str(_items["发送"]["source"]).startswith("auto:"), str(_r["auto"]))
        ok("T6a 另一引擎读到**高度相似**的（通讯录↔通讯簿）⇒ u=0.3（大概率对）",
           _items.get("通讯录", {}).get("uncertainty") == 0.3,
           str(_items.get("通讯录", {}).get("uncertainty")))
        ok("T6b 另一引擎**没有相近读数**的（设置面板）⇒ u=0.7（最该人看）",
           _items.get("设置面板", {}).get("uncertainty") == 0.7,
           str(_items.get("设置面板", {}).get("uncertainty")))
        ok("T6c 候选生成的产出**全部**不是人核真值（不许自己给自己盖章）",
           all(v["source"] != TS.SRC_HUMAN for v in _items.values()))
    finally:
        O.recognize, O._rapid_recognize = _real_rec, _real_rapid

    print("── T7/T8：确认/否决、有界、线程安全、落盘 ──")
    TS.clear()
    TS.add("ocr_text", "c.png", "待确认", source=TS.SRC_PROPOSED, uncertainty=0.7,
           produced_by="winrt")
    TS.confirm("ocr_text", "c.png", "待确认", note="人核过")
    ok("T7a confirm ⇒ 升为 human（这才进得了硬指标）",
       any(v["value"] == "待确认" for v in TS.truths("ocr_text")),
       str([v["value"] for v in TS.truths("ocr_text")]))
    TS.add("ocr_text", "c.png", "误读串", source=TS.SRC_PROPOSED, uncertainty=0.7,
           produced_by="winrt")
    TS.reject("ocr_text", "c.png", "误读串", why="原图没有这个字")
    ok("T7b reject ⇒ 删掉并留痕（`_rejected`）",
       not any(v["value"] == "误读串" for v in TS.truths("ocr_text", human_only=False))
       and bool(TS._load().get("_rejected")), str(list((TS._load().get("_rejected") or {}))[:1]))
    _errs = []
    def _w():
        try:
            for i in range(20):
                TS.add("ocr_text", "d.png", "多线程%d" % i, source=TS.SRC_PROPOSED,
                       uncertainty=0.5, produced_by="winrt")
        except Exception as e:  # noqa: BLE001
            _errs.append(str(e)[:50])
    _ts = [threading.Thread(target=_w) for _ in range(6)]
    [t.start() for t in _ts]
    [t.join() for t in _ts]
    ok("T8a 线程安全：6 线程各写 20 条不炸、库结构没坏",
       not _errs and isinstance(TS._load(), dict), str(_errs[:1]))
    ok("T8b 落盘：文件真的写出来了（跨重启仍在）", os.path.exists(_tmp), _tmp[-36:])
    _before = TS.stats()["human"]
    for i in range(TS.MAX_ITEMS + 50):
        TS.add("ocr_text", "e.png", "灌%d" % i, source=TS.SRC_PROPOSED,
               uncertainty=0.1, produced_by="winrt")
    _st = TS.stats()
    ok("T8c 有界：总量不超过上限，且**人核过的没被淘汰**",
       _st["total"] <= TS.MAX_ITEMS + 4 and TS.stats()["human"] >= _before,
       "total=%d human=%d（灌了 %d 条）" % (_st["total"], _st["human"], TS.MAX_ITEMS + 50))
finally:
    TS.PATH = _real

print("\n── T10：源码纪律 ──")
_src = io.open(os.path.join(ROOT, "agent", "truth_store.py"), encoding="utf-8").read()
_can = io.open(os.path.join(ROOT, "agent", "truth_candidates.py"), encoding="utf-8").read()
ok("T10a 真值库文档写明「只有人核过才进硬指标」", _sm.has(_src, "只有这一层能进"))
ok("T10b 真值库文档写明防自证（produced_by）", _sm.has(_src, "防自证"))
ok("T10c 候选生成文档写明「不直接当真值」", _sm.has(_can, "不直接当真值"))

print("\n真值流水线判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
