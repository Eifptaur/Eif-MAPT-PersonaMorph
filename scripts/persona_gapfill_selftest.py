# -*- coding: utf-8 -*-
"""`agent/persona_gapfill.py` 的正式判据（可被别人重跑，不依赖我的临时脚本）。

覆盖三件事：
  A. **纯函数** —— 调完 `fill_text` 后真库 `cards.json` 逐字节不变；
  B. **不编** —— 动机/价值观/缺点局限一律只能写「待补」，绝不出现别的内容；
     ⛔ 这一条是本模块的**核心承诺**：曾经想"从卡自己的文字推进机"，
        实测推出来的是卡标题、台词舞台说明、`persona_enrich` 追的群聊管家话
        （88 张"推出内容"人工抽查全是垃圾）⇒ 改成「待补」，把活交回给人。
  C. **能拆回** —— 补完的 text 仍能被 `to_fields()` 拆出七栏；
     新插的栏必须落在「## 对话示例」之前（否则提示词里顺序错乱）。

外加 D：缺口报告口径（`gap_report`）与 `persona_card.missing_fields` 必须同源。

用法：`python scripts/persona_gapfill_selftest.py`
"""
import hashlib
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent import persona_card, persona_gapfill  # noqa: E402

CARDS = os.path.join(ROOT, "assets", "persona", "cards.json")

_n = [0, 0]  # [通过, 失败]


def ok(name, cond, extra=""):
    _n[0 if cond else 1] += 1
    print("  %s %s%s" % ("✅" if cond else "⛔", name, ("　" + str(extra)) if extra else ""))
    return cond


def main() -> int:
    print("=" * 72)
    print("persona_gapfill 判据")
    print("=" * 72)

    if not os.path.exists(CARDS):
        ok("真库存在", False, CARDS)
        return 1
    _before = hashlib.sha256(open(CARDS, "rb").read()).hexdigest()
    lib = json.load(open(CARDS, encoding="utf-8"))
    P = lib.get("PERSONAS") or {}
    ok("真库已加载", bool(P), "%d 张" % len(P))

    # ── A. 纯函数 ──
    print("\n── A. 纯函数（不写库）──")
    touched = 0
    for pid, card in P.items():
        _t, rep = persona_gapfill.fill_text(card.get("text", ""))
        if not rep.get("unchanged"):
            touched += 1
    ok("跑完全库（%d 张被处理）" % touched, touched > 0)
    _after = hashlib.sha256(open(CARDS, "rb").read()).hexdigest()
    ok("真库逐字节未变", _before == _after, _before[:12] + " → " + _after[:12])

    # ── B. 不编 ──
    print("\n── B. 不编（机器补不了的三栏只能写「待补」）──")
    bad_fab, by_mark = [], {}
    for pid, card in P.items():
        new_text, rep = persona_gapfill.fill_text(card.get("text", ""))
        if rep.get("unchanged"):
            continue
        for k, v in (rep.get("filled") or {}).items():
            by_mark.setdefault(k, {})
            by_mark[k][v] = by_mark[k].get(v, 0) + 1
        f = persona_card.to_fields(new_text)
        for k in persona_gapfill.HUMAN_ONLY:
            if (rep.get("filled") or {}).get(k) == "待补":
                got = str(f.get(k) or "").strip()
                # 允许 `persona_card._harvest_sentences` 追加的残句（既有行为），
                # 但**开头必须是「待补」** —— 保证没被塞进任何"编出来的"正文。
                if not got.startswith(persona_gapfill.TODO_MARK):
                    bad_fab.append("%s#%s=%r" % (pid, k, got[:40]))
    ok("三栏落盘内容一律以「待补」开头", not bad_fab, bad_fab[:3] if bad_fab else "")
    for k in ("motive", "values", "limits", "rules"):
        ok("统计 %s" % k, True, json.dumps(by_mark.get(k, {}), ensure_ascii=False))
    ok("动机一张都没被机器编内容",
       by_mark.get("motive", {}).get("推导", 0) == 0,
       "推导=%d" % by_mark.get("motive", {}).get("推导", 0))
    ok("价值观一张都没被机器编内容",
       by_mark.get("values", {}).get("推导", 0) == 0,
       "推导=%d" % by_mark.get("values", {}).get("推导", 0))
    ok("缺点局限一张都没被机器编内容",
       by_mark.get("limits", {}).get("推导", 0) == 0,
       "推导=%d" % by_mark.get("limits", {}).get("推导", 0))

    # ── C. 能拆回 ──
    print("\n── C. 补完能拆回七栏、新插的栏在对话示例之前 ──")
    bad_order = []
    for pid, card in P.items():
        new_text, rep = persona_gapfill.fill_text(card.get("text", ""))
        if rep.get("unchanged"):
            continue
        f = persona_card.to_fields(new_text)
        ex = new_text.find("## 对话示例")
        for k in (rep.get("filled") or {}):
            p = new_text.find(persona_gapfill.HEAD[k])
            if p < 0:
                bad_order.append("%s#%s 标题丢" % (pid, k))
            elif ex > 0 and p > ex:
                bad_order.append("%s#%s 插到对话示例之后" % (pid, k))
            if not str(f.get(k) or "").strip():
                bad_order.append("%s#%s 补完仍空" % (pid, k))
    ok("插入位置/拆回无异常", not bad_order, bad_order[:3] if bad_order else "")

    # ── D. 缺口口径同源 ──
    print("\n── D. 缺口报告与 persona_card 同源 ──")
    gr = persona_gapfill.gap_report(P)
    manual = {k: 0 for k in persona_card.FIELDS}
    for _pid, card in P.items():
        for k in persona_card.missing_fields(persona_card.to_fields(card.get("text", "")), strict=True):
            manual[k] += 1
    ok("缺口计数与 missing_fields 逐栏一致", gr["by_field"] == manual,
       "gapfill=%s" % json.dumps(gr["by_field"], ensure_ascii=False))
    ok("「必须人写」卡数 > 0（说明库里确实还有活）", len(gr["need_human"]) > 0,
       "%d 张" % len(gr["need_human"]))

    # ── 收尾 ──
    print("\n" + "=" * 72)
    print("通过 %d / 失败 %d" % (_n[0], _n[1]))
    print("=" * 72)
    return 0 if _n[1] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
