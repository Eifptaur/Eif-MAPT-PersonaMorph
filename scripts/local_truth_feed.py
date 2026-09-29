# -*- coding: utf-8 -*-
"""把**本机真实截图**喂进真值流水线（`local-real` 语料 → 待审候选 → 人工确认 → 进基准）。

## 这一步补的是哪个缺口
`ocr_corpus_local.py` 只负责"**把真实截图收进语料目录**"（采进来、体检、标隐私风险）。
但"收进来"≠"能当基准的尺子"：基准用的是**核过真值**（`ocr_bench_web.CASES`），
而真值只有两个合法来源：① 人看图核过；② 多引擎一致性（tier-2，只做引擎对比）。
⇒ 本脚本把每张真实截图过一遍 `truth_candidates.from_image()`，产出**待审候选**
   （写进 `truth_store`，`source="proposed"`），再交给 `pmctl truth` 导出待审包让人看图确认。
   确认后经 `truths(human_only=True)` 才是**核过真值**，才能进基准分母。

## 为什么不能"自动转正"
`from_image` 的 `auto` 项是"两引擎读数一致"——一致性**不是**正确性（两个引擎能一起错）。
⇒ 本脚本**只产候选，不转正**；转正只能靠人看图（`pmctl truth ok <序号>`）。
   这条纪律与 `ocr_bench_web` 的 tier-1/tier-2 划分是同一套，不另立标准。

## 隐私处理（与 `ocr_corpus_local.py` 的 `eligible` 标记配套）
`sources[i]["eligible"] == False` 的图（扫出手机号/邮箱/身份证等）**默认跳过**，
除非显式 `--include-flagged`。跳过会在报告里列出，不会静默漏掉。

## 用法
    runtime\\python\\python.exe scripts\\local_truth_feed.py                # 全部 eligible 的真实截图
    runtime\\python\\python.exe scripts\\local_truth_feed.py --limit 5       # 先试 5 张
    runtime\\python\\python.exe scripts\\local_truth_feed.py --include-flagged
    runtime\\python\\python.exe scripts\\local_truth_feed.py --report       # 只看流水线现状，不喂
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.chdir(ROOT)
os.environ.setdefault("PM_NO_WINDOW_TOUCH", "1")

LOCAL = os.path.abspath(os.path.join(
    ROOT, "..", "dev-workspace", "persona-morph", "ocr-samples", "local"))
SRC = os.path.join(LOCAL, "_sources.json")


def _load() -> dict:
    try:
        return json.load(io.open(SRC, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print("读不了 %s：%s" % (SRC, str(e)[:60]))
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="最多喂几张（0=不限）")
    ap.add_argument("--include-flagged", action="store_true", help="连隐私标记的也喂（默认跳过）")
    ap.add_argument("--report", action="store_true", help="只看流水线现状，不喂")
    a = ap.parse_args()

    from agent import truth_store as TS
    if a.report:
        s = TS.stats()
        print("真值库现状：总数 %s（人核 %s · 一致性 %s · 待议 %s）· 自动占比 %s"
              % (s.get("total"), s.get("human"), s.get("auto"), s.get("proposed"),
                 ("%.1f%%" % (100 * s["auto_ratio"])) if s.get("auto_ratio") is not None else "—"))
        print("   （详细待审清单：`pmctl truth list`；导出看图：`pmctl truth export`）")
        return 0

    db = _load()
    if not db:
        print("语料清单是空的 ⇒ 先跑 `scripts/ocr_corpus_local.py` 把真实截图收进来")
        return 1

    todo, skipped_pii = [], []
    for fn, meta in sorted(db.items()):
        p = os.path.join(LOCAL, fn)
        if not os.path.exists(p):
            continue
        if (meta or {}).get("pii_risk") and not a.include_flagged:
            skipped_pii.append(fn)
            continue
        todo.append((fn, p))
    if a.limit > 0:
        todo = todo[:a.limit]

    print("本机真实截图：待喂 %d 张 · 因隐私标记跳过 %d 张%s"
          % (len(todo), len(skipped_pii),
             "（加 --include-flagged 可一并喂）" if skipped_pii else ""))
    for fn in skipped_pii:
        print("   跳过（隐私标记）：%s" % fn)

    if not todo:
        print("没有要喂的图。")
        return 0

    from agent import truth_candidates as TC
    n_auto = n_prop = 0
    for i, (fn, p) in enumerate(todo, 1):
        r = TC.from_image(p, produced_by="local_truth_feed")
        _a, _pr = len(r.get("auto") or []), len(r.get("proposed") or [])
        n_auto += _a
        n_prop += _pr
        print("  [%2d/%2d] %-38s 一致 %3d · 待议 %3d%s"
              % (i, len(todo), fn[:38], _a, _pr,
                 ("  ⚠️ " + r["why"]) if r.get("why") else ""))

    s = TS.stats()
    print("\n完成：喂入 %d 张 → 一致性候选 %d 条 + 待议候选 %d 条" % (len(todo), n_auto, n_prop))
    print("   真值库：总数 %s（人核 %s · 一致性 %s · 待议 %s）"
          % (s.get("total"), s.get("human"), s.get("auto"), s.get("proposed")))
    print("   下一步（**要人看图**）：`pmctl truth export` → 打开 crops\\pN.png 逐张核 → "
          "`pmctl truth ok <序号>` / `truth no <序号>`")
    print("   ⛔ 一致性候选（auto）只做引擎对比，**不进基准分母**；只有人核过的才算真值。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
