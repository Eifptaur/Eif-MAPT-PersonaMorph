# -*- coding: utf-8 -*-
"""人设卡「评分体检」：覆盖 + 区分度 + 与客观标记的交叉表。

为什么要有它（2026-09-15 用户问「检查模型评分和模型补正是真的按贴合原人设做的」）：
  细则 `agent/persona_rating.py` 自己写着「严禁把每张卡都塞进 85~92 的窄带，好的就是好、差的要敢打 5x/6x」。
  ⇒ 光看"有没有分"不够，要能一眼看出**这把尺子有没有区分度**，否则它只是"看起来在评分"。
判定口径：
  · 覆盖率：有 model 分的卡占比（缺分就补：`scripts/persona_score_batch.py`）；
  · 区分度：标准差 + 最大单档占比（一项超 60% 就该怀疑评分趋中）；
  · 交叉表：拿两个**客观标记**对撞——①卡里标了「按其口吻/（拟）」＝没有一手出处；
    ②有没有「关系与执念」段。若两组的分数没有可见差异，说明尺子没在按"贴合"区分。

用法：py -3 scripts/persona_score_health.py
"""
import io
import json
import os
import statistics as st
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent.persona import PERSONAS, PERSONA_CATS  # noqa: E402

RATINGS = os.path.join(ROOT, "data", "persona_ratings.json")
BUCKETS = [(0, 60, "<60"), (60, 70, "60~69"), (70, 80, "70~79"),
           (80, 88, "80~87"), (88, 101, ">=88")]


def body(v):
    return v.get("text", "") if isinstance(v, dict) else (v or "")


try:
    R = json.loads(io.open(RATINGS, encoding="utf-8").read() or "{}")
except Exception as e:
    print("读不到 %s：%s" % (RATINGS, e))
    sys.exit(2)

scores = {}
for k in PERSONAS:
    r = R.get(k) or {}
    for f in ("model", "score", "total"):
        if isinstance(r.get(f), (int, float)):
            scores[k] = float(r[f])
            break

total = len(PERSONAS)
have = len(scores)
print("覆盖率：%d / %d 张有模型分（缺 %d）—— 缺分用 `py -3 scripts/persona_score_batch.py` 补"
      % (have, total, total - have))
if not scores:
    sys.exit(2)

vals = sorted(scores.values())
n = len(vals)
print("分布：min=%.1f p25=%.1f 中位=%.1f p75=%.1f max=%.1f 均值=%.1f 标准差=%.2f"
      % (vals[0], vals[n // 4], vals[n // 2], vals[3 * n // 4], vals[-1],
         sum(vals) / n, st.pstdev(vals)))

cnt = Counter()
for v in vals:
    for lo, hi, name in BUCKETS:
        if lo <= v < hi:
            cnt[name] += 1
            break
print("分档：" + " ／ ".join("%s %d(%.0f%%)" % (name, cnt[name], 100.0 * cnt[name] / n)
                            for _, _, name in BUCKETS))
_top = max(cnt.values())
print("最大单档占比：%.0f%%%s" % (100.0 * _top / n,
                              "  ⚠️ 超过 60% ⇒ 评分可能趋中（细则明令禁止塞窄带）" if _top > 0.6 * n else "  （可接受）"))

print("\n交叉表（用客观标记验「尺子有没有在按贴合区分」）：")
for label, pred in (("标了「按其口吻/（拟）」（无一手出处）",
                     lambda t: ("按其口吻" in t or "（拟）" in t or "(拟)" in t)),
                    ("有关系与执念段", lambda t: "关系与执念" in t)):
    a = [scores[k] for k in scores if pred(body(PERSONAS[k]))]
    b = [scores[k] for k in scores if not pred(body(PERSONAS[k]))]
    sa = "n=%d 中位=%.1f 均值=%.1f" % (len(a), st.median(a), sum(a) / len(a)) if a else "n=0"
    sb = "n=%d 中位=%.1f 均值=%.1f" % (len(b), st.median(b), sum(b) / len(b)) if b else "n=0"
    print("  %-32s 有：%s ／ 无：%s" % (label, sa, sb))

print("\n分区覆盖：" + " ／ ".join("%s %d" % (c, n2) for c, n2 in
                                  Counter(PERSONA_CATS.get(k, "?") for k in PERSONAS).most_common()))
low = sorted(scores.items(), key=lambda kv: kv[1])[:8]
print("分数最低的 8 张（值得人工看评分理由）：" + " ／ ".join("%s=%.1f" % (k, v) for k, v in low))
