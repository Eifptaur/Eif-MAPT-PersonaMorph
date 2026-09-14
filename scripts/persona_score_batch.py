# -*- coding: utf-8 -*-
"""给「没有模型评分」的人设卡补分（用户 2026-09-14：「人设卡没有评分显示的，记得补上啊」）。

口径：
  · 用的是**产品同一把尺子**——模块级 `persona_llm_score()`（RULES_TEXT 唯一权威细则 + 真实资料比对），
    判据/脚本都调真身，不另抄 prompt；
  · 一条一存（每张卡评完立刻写回 `data/persona_ratings.json`），**可中断可续跑**——
    42 张卡要跑十几分钟，中途断了不能全丢；
  · 便宜优先：默认只补"没有 model 分"的卡；`--keys` 指定单卡，`--limit` 限量。

用法：
  py -3 scripts/persona_score_batch.py            # 补所有缺 model 分的卡
  py -3 scripts/persona_score_batch.py --limit 5  # 只补 5 张（先验证链路）
  py -3 scripts/persona_score_batch.py --keys kasumi_bd,arisa_bd
"""
import argparse
import importlib.util
import io
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent.persona import PERSONAS                     # noqa: E402

RATINGS = os.path.join(ROOT, "data", "persona_ratings.json")


def load_ratings() -> dict:
    try:
        with io.open(RATINGS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_ratings(d: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(RATINGS), exist_ok=True)
        tmp = RATINGS + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, RATINGS)
        return True
    except Exception as e:
        print("  写评分文件失败：%s" % e)
        return False


def load_scorer():
    """加载 scripts/persona_morph.py（只取模块级 persona_llm_score，不跑 main）。"""
    spec = importlib.util.spec_from_file_location("pm_score", os.path.join(ROOT, "scripts", "persona_morph.py"))
    pm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pm)
    return pm.persona_llm_score


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--keys", default="")
    ap.add_argument("--sleep", type=float, default=0.8)
    a = ap.parse_args()

    scorer = load_scorer()
    ratings = load_ratings()
    if a.keys:
        todo = [k for k in a.keys.split(",") if k.strip() in PERSONAS]
    else:
        todo = [k for k in PERSONAS if not (ratings.get(k) or {}).get("model")]
    if a.limit:
        todo = todo[:a.limit]
    print("待补分 %d 张" % len(todo))
    done = 0
    for i, key in enumerate(todo, 1):
        card = (PERSONAS.get(key) or {}).get("text") or ""
        name = (PERSONAS.get(key) or {}).get("name") or key
        t0 = time.time()
        r = scorer(card, name)
        if not r.get("ok"):
            print("  [%d/%d] %-16s ✘ %s" % (i, len(todo), key, str(r.get("error"))[:70]))
            continue
        cur = dict(ratings.get(key) or {})
        cur["model"] = r["score"]
        cur["model_reason"] = (r.get("reason") or "")[:200]
        cur["dims"] = r.get("dims") or cur.get("dims") or {}
        tr = list(cur.get("trace") or [])
        tr.append(r["score"])
        cur["trace"] = tr[-5:]
        cur["name"] = name
        cur["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        ratings[key] = cur
        okw = save_ratings(ratings)
        done += 1
        print("  [%d/%d] %-16s %s  (%.0fs)%s" % (i, len(todo), key, r["score"], time.time() - t0,
                                                "" if okw else "  ⚠️未落盘"))
        time.sleep(a.sleep)
    print("完成 %d/%d；文件 %s" % (done, len(todo), RATINGS))


if __name__ == "__main__":
    main()
