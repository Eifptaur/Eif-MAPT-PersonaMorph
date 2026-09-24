# -*- coding: utf-8 -*-
"""丙-12 取证：sec_meta 行解析 修复前 / 修复后 对比。

用法：
    runtime/python/python.exe ui_qt/_c12_secmeta_check.py

对比口径（都用**真解析逻辑**、都读 web 真值 agent/console_html.py）：
  · 修复前 = 旧前瞻截断写法（就地复刻，本脚本自带，不依赖历史版本）
  · 修复后 = 现 sec_meta.secs()
指标：每 sec 的 rows 条数、kind 分布、buttons 档 row 数与其按钮文案。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sec_meta  # noqa: E402


def _old_parse(key: str) -> list[sec_meta.Row]:
    """修复前的写法（前瞻截断），原样复刻用于对比。"""
    text = sec_meta.WEB_PATH.read_text(encoding="utf-8")
    m = re.search(r'<section id="sec-%s" class="card" data-sec>(.*?)</section>' % re.escape(key),
                  text, re.S)
    if m is None:
        return []
    body = m.group(1)
    rows: list[sec_meta.Row] = []
    for rm in re.finditer(
        r'<div class="row"[^>]*><label>([^<]+)</label>(.*?)(?=<div class="row"|<div class="btns"|</section>|<div class="mid"|<hr)',
        body, re.S,
    ):
        rows.append(sec_meta._parse_row(rm.group(2), sec_meta._clean(rm.group(1))))
    return rows


def _btns(rows: list[sec_meta.Row]) -> list[str]:
    out = []
    for r in rows:
        if r.kind == "buttons":
            out.append("%s→%d枚%s" % (r.label, len(r.actions), [a[0] for a in r.actions]))
    return out


def _kinds(rows: list[sec_meta.Row]) -> dict:
    k: dict = {}
    for r in rows:
        k[r.kind] = k.get(r.kind, 0) + 1
    return k


def main() -> int:
    secs = sec_meta.secs()
    print("== 修复前 / 修复后 对比（web 真值：%s） ==" % sec_meta.WEB_PATH.name)
    tot_old = tot_new = 0
    tot_old_btn = tot_new_btn = 0
    for key in ("persona", "send", "advanced"):
        old = _old_parse(key)
        new = secs[key].rows
        tot_old += len(old)
        tot_new += len(new)
        ob = [r for r in old if r.kind == "buttons"]
        nb = [r for r in new if r.kind == "buttons"]
        tot_old_btn += sum(len(r.actions) for r in ob)
        tot_new_btn += sum(len(r.actions) for r in nb)
        print("\n-- sec-%s --" % key)
        print("  rows:      %d -> %d" % (len(old), len(new)))
        print("  kind分布:  %s" % _kinds(old))
        print("             %s" % _kinds(new))
        print("  buttons行: %d -> %d" % (len(ob), len(nb)))
        print("    修复前: %s" % (_btns(old) or "（无）"))
        print("    修复后: %s" % (_btns(new) or "（无）"))

    print("\n== 三个 sec 合计 ==")
    print("  rows:        %d -> %d" % (tot_old, tot_new))
    print("  buttons按钮: %d -> %d" % (tot_old_btn, tot_new_btn))

    # 全 sec 汇总（只看修复后，确认无 sec 解析成空）
    print("\n== 全部 27 sec 的 rows / buttons ==")
    empty = []
    tot = 0
    for key, s in secs.items():
        nb = sum(len(r.actions) for r in s.rows if r.kind == "buttons")
        tot += len(s.rows)
        if not s.rows:
            empty.append(key)
    print("  27 sec 合计 rows = %d ；空 rows 的 sec = %s" % (tot, empty or "（无）"))

    # persona「选单」行：修复后必须列出排序/恢复按钮
    print("\n== persona「选单」行明细（修复后） ==")
    for r in secs["persona"].rows:
        if r.label == "人设选单":
            print("  kind=%s sub=%s group=%r indent=%d" % (r.kind, r.sub, r.group, r.indent))
            for t, a in r.actions:
                print("    · %s  [%s]" % (t, a))

    # mid 子行标记抽查
    print("\n== mid 子行标记（修复后） ==")
    for key in ("persona", "send", "advanced"):
        subs = [r for r in secs[key].rows if r.sub]
        print("  sec-%s: 子行 %d 条；样例=%s"
              % (key, len(subs), [(r.label, r.group) for r in subs[:3]]))

    # ── 判定 ──
    print("\n== 判定 ==")
    ok = True
    p = secs["persona"]
    # 「人设选单」行 + 紧随其后的 buttons 兄弟行（解析层按行内 .btns 组单独产出一行）
    idx = [i for i, r in enumerate(p.rows) if r.label == "人设选单"]
    acts: list[str] = []
    if idx:
        i0 = idx[0]
        for r in p.rows[i0:i0 + 4]:
            if r.label == "人设选单" and r.kind == "buttons":
                acts += [a[0] for a in r.actions]
            elif r.kind == "buttons" and r.label == "人设选单":
                acts += [a[0] for a in r.actions]
    want = ("排序", "恢复默认顺序", "恢复上个人设")
    hit = [w for w in want if any(w in x for x in acts)]
    print("  %s  人设选单按钮 = %s" % ("OK  " if len(hit) == 3 else "FAIL", acts))
    ok = ok and len(hit) == 3
    for key in ("send", "advanced"):
        n = len(secs[key].rows)
        o = len(_old_parse(key))
        good = n >= o
        print("  %s  sec-%s rows %d -> %d（不降级）" % ("OK  " if good else "FAIL", key, o, n))
        ok = ok and good
    print("\n结论：%s" % ("全部通过" if ok else "存在失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
