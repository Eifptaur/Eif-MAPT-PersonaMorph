# -*- coding: utf-8 -*-
"""把外部施工者交回的「人设卡七字段重写」JSON 换进库。

用法：
    python scripts/persona_apply_rewrite.py <交回的.json> [更多.json ...] [--dry]

设计口径（与《任务书-人设卡七字段重写（对外）》第六节一致）：
  1. **只动交上来的那几张**，其余卡一个字节不变；
  2. 替换前**先备份**整库成 `cards.json.bak-<时间戳>`；
  3. `name` 与旧名不一致 ⇒ **只报错不落盘**（防改错卡）；
  4. `sources` / `notes` / `checked` 一起存进卡的 `note` 字段（留出处供复查）；
  5. **格式校验不通过 ⇒ 整批不落盘**（要么全进、要么全不进，不留半成品）；
  6. 写入走 `persist.atomic_write_json`（禁止裸 `os.replace`）。

交回文件支持三种形态：
  A. 单个 JSON 对象（一条卡）；
  B. JSON 数组（多条卡）；
  C. `{"cards": [...]}`（一整批）。

每条卡只要**认得 `id` + `text`** 即可；`name`/`mode`/`sources`/`notes`/`checked` 可缺。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.persona_card import FIELD_CN, missing_fields, to_fields  # noqa: E402
from agent.persist import atomic_write_json  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARDS = os.path.join(ROOT, "assets", "persona", "cards.json")

# 交回的卡里 `text` 必须带全的七个小标题（顺序固定，一字不差）
HEADERS = ["## 身份背景", "## 性格", "## 说话风格", "## 动机",
           "## 价值观", "## 行为规则", "## 缺点局限"]
EX_HDR = "## 对话示例"


def _load_payload(path):
    """把交回文件读成「一条卡的 dict 列表」。"""
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        if isinstance(d.get("cards"), list):
            return d["cards"]
        return [d]
    raise ValueError("认不出交回文件的形态：%s" % path)


def _check_text(cid, name, text):
    """校验 `text` 的机器格式。返回错误说明列表（空 = 通过）。"""
    errs = []
    t = text if isinstance(text, str) else ""
    if not t.strip():
        return ["`text` 是空的"]
    # 1) 七个小标题，一字不差、顺序固定
    pos = -1
    for h in HEADERS:
        i = t.find(h)
        if i < 0:
            errs.append("缺小标题 `%s`" % h)
            continue
        if i < pos:
            errs.append("小标题顺序不对：`%s` 出现在 `%s` 前面" % (h, HEADERS[max(0, pos and 0)]))
        pos = i
    # 2) 对话示例整段
    if t.find(EX_HDR) < 0:
        errs.append("缺 `%s`" % EX_HDR)
    else:
        seg = t[t.find(EX_HDR) + len(EX_HDR):]
        if seg.count("群友：") < 3 or seg.count("角色：") < 3:
            errs.append("`%s` 不够三段（要 3 组「群友：/角色：」）" % EX_HDR)
    # 3) 禁用 Markdown 装饰符（`##` 小标题除外）
    body = t
    for h in HEADERS + [EX_HDR]:
        body = body.replace(h, "")
    if "**" in body:
        errs.append("正文出现 `**`（会原样进微信，须删）")
    for ln in body.split("\n"):
        s = ln.strip()
        if s.startswith("- ") or s.startswith("* "):
            errs.append("正文出现列表符号（`- ` / `* `），须删")
            break
    # 4) 七栏都要在位（缺栏也得写「无」，这里只查标题在不在）
    #    标题缺失已在 1) 抓过 ⇒ 此处只补一条"栏里必须有内容或「无」"
    return errs


def main(argv):
    dry = "--dry" in argv
    files = [a for a in argv if not a.startswith("--")]
    if not files:
        print("用法：python scripts/persona_apply_rewrite.py <交回的.json> [...] [--dry]")
        return 2

    payload = []
    for p in files:
        if not os.path.isfile(p):
            print("× 找不到文件：%s" % p)
            return 2
        try:
            payload.extend(_load_payload(p))
        except Exception as e:  # noqa: BLE001
            print("× 读不了 %s：%s" % (p, e))
            return 2

    with open(CARDS, encoding="utf-8") as fh:
        lib = json.load(fh)
    personas = lib.get("PERSONAS") or {}

    plan, problems = [], []
    for item in payload:
        if not isinstance(item, dict):
            problems.append(("（非对象）", ["这条不是 JSON 对象"]))
            continue
        cid = str(item.get("id") or "").strip()
        lname = str(item.get("name") or "").strip()
        text = item.get("text")
        if not cid:
            problems.append(("（无 id）", ["这条没有 `id`"]))
            continue
        old = personas.get(cid)
        if old is None:
            problems.append((cid, ["库里没这张卡 ⇒ 只准重写、不准新增"]))
            continue
        old_name = str(old.get("name") or "").strip()
        if lname and old_name and lname != old_name:
            problems.append((cid, ["`name` 与库里不一致（库里=%s / 交回=%s）"
                                   "⇒ 防改错卡，请核对后照抄库里的名字" % (old_name, lname)]))
            continue
        errs = _check_text(cid, old_name or lname, text)
        # 拆栏自检：拆出来必须“非空栏数 ≥ 6”，否则说明标题虽在但内容没跟上
        if not errs:
            f = to_fields(text)
            miss = missing_fields(f, strict=True)
            # 严格口径：<12 字算缺（写「无」算写了）
            ok_cnt = 7 - len([m for m in miss])
            if ok_cnt < 6:
                errs.append("拆栏后只有 %d 栏达标（要 ≥6）；缺：%s"
                            % (ok_cnt, "、".join(FIELD_CN[m] for m in miss)))
        if errs:
            problems.append((cid, errs))
            continue
        note = {"checked": str(item.get("checked") or "").strip(),
                "sources": item.get("sources") or [],
                "notes": str(item.get("notes") or "").strip()}
        plan.append((cid, text, note))

    if problems:
        print("✗ 这批**不落盘**（要么全进、要么全不进）。问题如下：\n")
        for cid, errs in problems:
            print("  · %s" % cid)
            for e in errs:
                print("      - %s" % e)
        print("\n修好再交，或把不达标的卡单独挑出来。")
        return 1

    print("准备替换 %d 张卡：" % len(plan))
    for cid, text, _ in plan:
        print("  · %-16s %5d 字" % (cid, len(text)))

    if dry:
        print("\n[--dry] 只校验，未写盘。")
        return 0

    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = "%s.bak-%s" % (CARDS, ts)
    with open(CARDS, "rb") as s, open(bak, "wb") as d:
        d.write(s.read())
    print("\n已备份旧库 → %s" % os.path.basename(bak))

    for cid, text, note in plan:
        old = personas[cid]
        old["text"] = text
        # 出处/备注存进 note（库里卡的第三键），供以后复查
        try:
            old["note"] = json.dumps(note, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            old["note"] = str(note)

    ok = atomic_write_json(CARDS, lib, indent=1)
    if not ok:
        print("✗ 写盘失败 ⇒ 旧库一个字节没动；请稍后重试。")
        return 1
    print("✓ 已替换 %d 张卡进库：%s" % (len(plan), CARDS))
    print("  备份留在：%s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
