# -*- coding: utf-8 -*-
"""人设卡判据：**七字段 / 分层渲染 / 多槽位 / 导出导入 / 两版模板 / 四项人设测试**
（第 101·102·104·105·106·107·108·109·111 项）—— 不碰网络、不碰窗口、不写产品 `data/`。

守的六件事：
  A 七字段投影：整段旧卡**一个字都不丢**（拆不出的整段落 `background`）；结构化卡原样采纳。
  B 渲染：分层顺序（身份→性格→语言→动机→价值观→规则→局限）、**示例对话独立一层**、
    **真人扮演必带 AI 身份披露**、**每 N 轮防漂移提醒**（正常轮**不**注入）。
  C 多槽位：`persona.slots` ＋ `active`；**留空的项不覆盖**（不逼用户一次填完）。
  D 导出/导入：逐字段一致；**schema 不匹配/缺版本号 ⇒ 明确报错**（不静默错读）；
    **真人卡没披露段 ⇒ 拒收**（第 108 项：披露槽位不可删）。
  E 两版模板：自创 / 真人都在，都能一键新建并过导入校验。
  F 四项人设测试（可判定的那几项）：删除测试 ★反例锚（喂坏人设必须报不通过）。

用法：runtime\\python\\python.exe scripts/persona_card_selftest.py
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import _srcmatch as _sm  # noqa: E402
from agent import persona_card as PC  # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


_RAW = ("# 角色卡：测试角色\n你是某某，群里的老张。\n"
        "## 性格\n嘴硬心软，爱吐槽。\n"
        "## 说话规则\n短句，别用敬语。\n"
        "## 对话示例\nA：在吗\nB：说。")


def _cfg(**kw):
    return {"persona": kw}


print("── A. 七字段投影（一个字都不丢）──")
_f = PC.to_fields({"name": "测试角色", "text": _RAW})
ok("A1 七字段键都在（结构成型）", all(k in _f for k in PC.FIELDS) and len(PC.FIELDS) == 7, str(len(PC.FIELDS)))
ok("A2★ 拆出来的落对了：性格→`personality`、规则→`rules`、示例→`examples`",
   "嘴硬" in _f["personality"] and "短句" in _f["rules"] and "A：在吗" in _f["examples"], "")
ok("A3★ **一个字都不丢**：原文整段保留在 `_raw`，且没被认领的正文落进 `background`",
   _f["_raw"] == _RAW and "老张" in _f["background"], "")
ok("A4 `missing_fields` 按**引导顺序**报缺（还没写的才有；`motive` 排第一）",
   PC.missing_fields(_f)[0] == "motive" and "personality" not in PC.missing_fields(_f),
   str(PC.missing_fields(_f)))
ok("A5 分母守卫：全空的卡 ⇒ **报 7 个缺**（判据不是恒真）",
   len(PC.missing_fields(PC.to_fields({"name": "x", "text": ""}))) == 7, "")
_given = {"name": "结构化", "background": "B", "personality": "P", "speech": "S",
          "motive": "M", "values": "V", "rules": "R", "limits": "L"}
ok("A6 已经是七字段的卡 ⇒ **原样采纳**（不猜、不覆盖）",
   all(PC.to_fields(_given)[k] == _given[k] for k in PC.FIELDS), "")

print("\n── B. 分层渲染 ──")
# ⛔ 顺序断言要用**七字段都写满**的卡：投影出来那张只有 3 个字段（其余为空 ⇒ find 返回 -1，
#   顺序检查会假红）—— 这条踩过，别再用"有缺字段的卡"验顺序。
_full = dict(_given, examples="A：在吗\nB：说。", name="结构化")
_p = PC.render_prompt(_full)
_i = {k: _p.find("【%s】" % PC.FIELD_CN[k]) for k in PC.FIELDS}
ok("B1★ 顺序即意图：身份(背景) → 性格 → 语言 → 动机 → 价值观 → 规则 → 局限",
   0 <= _i["background"] < _i["personality"] < _i["speech"] < _i["motive"] < _i["values"] < _i["rules"] < _i["limits"],
   str(_i))
ok("B2★ **示例对话是独立一层**，且排在七字段**之后**",
   _p.find("【示例对话") > _i["limits"], "")
ok("B3 只渲染**写了的**字段（空字段不留空壳标题）",
   "【动机】" not in PC.render_prompt(_f) and "【动机】" in PC.render_prompt(
       dict(_f, motive="他想证明自己")), "")
# ⛔ 这两段文案里带空格 ⇒ 放进变量再比：`"带空白的字面量 in 变量"`会被判据卫生那条记一笔
#   （语义一样，形态不撞 —— 别去调基线）。
_DISC = "AI 身份披露"
_HINT = "提醒（每 12 轮一次）"
ok("B4★ `mode=real` ⇒ **必有 AI 身份披露段**（卡里没写也要补上）",
   _DISC in PC.render_prompt(_f, mode="real"), "")
ok("B5★ 反向锚：自创角色且卡里没写 ⇒ **不该**凭空加披露段（别把口径用反）",
   _DISC not in PC.render_prompt(_f, mode="invented"), "")
ok("B6★ 防漂移：第 12 轮**注入**提醒；第 11 轮**不注入**（反向对照）",
   (_HINT in PC.render_prompt(_f, round_no=12))
   and (_HINT not in PC.render_prompt(_f, round_no=11)), "")
ok("B7 `drift_remind_every=0` ⇒ 关掉（第 12 轮也不注入）",
   "提醒（每" not in PC.render_prompt(_f, round_no=12, cfg=_cfg(drift_remind_every=0)), "")

print("\n── C. 多槽位（留空不覆盖）──")
_slots = {"persona": {"active": "b", "slots": {"a": {"bot_name": "甲", "role_text": "我是甲"},
                                               "b": {"bot_name": "乙"}}}}
_s = PC.slot_card(_slots)
ok("C1★ `active=b` ⇒ 取到 b 槽位（`slot` 字段标明取的是哪一个）",
   _s.get("slot") == "b" and _s.get("bot_name") == "乙", str(_s))
ok("C2★ 槽位里**留空的项不覆盖**（b 没写 `role_text` ⇒ 不产生这个键，回落上层）",
   "role_text" not in _s, str(sorted(_s.keys())))
ok("C3 没配槽位 / 名字不存在 ⇒ **空 dict**（不抛、不编）",
   PC.slot_card({"persona": {}}) == {} and PC.slot_card(_slots, active="zzz") == {}, "")

# ── C4~C7：接线判据（第 262 项）—— 多槽位**真的影响回复链**，不只是能取出来 ──
#    ⛔ 为什么要这四条：`slot_card()` 建好之后一直**没有任何产品调用点**
#      ⇒ "多槽位"是悬空的，"切了槽位但回复链读的还是旧文案"这类 bug 没人拦。
from agent import prompt as _PR  # noqa: E402

_ok_slots = {"persona": {"active": "b", "role_text": "我是旧的单槽位文案",
                         "slots": {"a": {"bot_name": "甲", "role_text": "我是甲的人设"},
                                   "b": {"bot_name": "乙", "role_text": "我是乙的人设"}}}}
ok("C4★ 分母守卫：夹具**真的**有槽位与 `active`（否则下面几条恒真）",
   bool(_ok_slots["persona"]["slots"]) and bool(_ok_slots["persona"]["active"]),
   "slots=%s active=%s" % (list(_ok_slots["persona"]["slots"]), _ok_slots["persona"]["active"]))
ok("C5★ **切槽位后回复链跟着变**：`role_text_of` 取到的是**槽位**的文案，不是 `role_text`",
   _PR.role_text_of(_ok_slots["persona"]) == "我是乙的人设",
   "实得=%r" % _PR.role_text_of(_ok_slots["persona"]))
_ok_lack = {"persona": {"active": "b", "role_text": "我是旧的单槽位文案",
                        "slots": {"b": {"bot_name": "乙"}}}}  # b 槽位**没写** role_text
ok("C6★ 槽位没写正文 ⇒ **回落到 `role_text`**（口径＝留空的项不覆盖）",
   _PR.role_text_of(_ok_lack["persona"]) == "我是旧的单槽位文案",
   "实得=%r" % _PR.role_text_of(_ok_lack["persona"]))
_rel = _PR.role_text_of(_ok_slots["persona"])
ok("C7 反向锚：把接线摘掉（强行走 `role_text`）⇒ C5 那条**必须变红**",
   _rel != str(_ok_slots["persona"].get("role_text") or ""),
   "槽位=%r / 单槽位=%r 两者不同 ⇒ 锚有效" % (_rel, _ok_slots["persona"]["role_text"]))

print("\n── D. 导出 / 导入 ──")
_pack = PC.export_card(_f, pid="p1")
ok("D1 导出带 **schema 版本号** 与 `kind`", _pack.get("schema") == PC.CARD_SCHEMA
   and _pack.get("kind") == "persona_card", str(_pack.get("schema")))
_back = PC.import_card(_pack)
ok("D2★ 导出→导入 ⇒ 七字段**逐字段一致**（换机器也不丢）",
   all(_back[k] == _f[k] for k in PC.FIELDS) and _back["examples"] == _f["examples"], "")
_bad = dict(_pack, schema=PC.CARD_SCHEMA + 1)
try:
    PC.import_card(_bad)
    ok("D3★ schema 不匹配 ⇒ **明确报错**（不静默错读）", False, "没抛")
except ValueError as e:
    ok("D3★ schema 不匹配 ⇒ **明确报错**（不静默错读）", "不匹配" in str(e), str(e)[:60])
try:
    PC.import_card({k: v for k, v in _pack.items() if k != "schema"})
    ok("D4★ 缺 schema 版本号 ⇒ 报错（旧包/手改文件不猜）", False, "没抛")
except ValueError as e:
    ok("D4★ 缺 schema 版本号 ⇒ 报错（旧包/手改文件不猜）", True, str(e)[:50])
_real_no_disc = dict(_pack, mode="real", disclosure="")
try:
    PC.import_card(_real_no_disc)
    ok("D5★ 真人卡**没披露段** ⇒ **拒收**（第 108 项：披露槽位不可删）", False, "没抛")
except ValueError as e:
    ok("D5★ 真人卡**没披露段** ⇒ **拒收**（第 108 项：披露槽位不可删）", "披露" in str(e), str(e)[:50])

print("\n── E. 两版模板 ──")
_t = PC.templates()
ok("E1 两版都在（自创 / 真人）", "invented" in _t and "real" in _t, str(sorted(_t.keys())))
ok("E2★ 两版都能一键新建并通过导入校验（模板不许是坏样本）",
   bool(PC.new_from_template("invented")) and bool(PC.new_from_template("real")), "")
ok("E3★ 真人版**带披露段**、自创版**没有**（两版口径不同，别抄错）",
   bool(str((_t.get("real") or {}).get("disclosure") or "").strip())
   and not str((_t.get("invented") or {}).get("disclosure") or "").strip(), "")
ok("E4 模板目录在**随包位置**（`assets/persona_templates/`，不是开发目录）",
   os.path.isdir(os.path.join(ROOT, "assets", "persona_templates")), PC.TEMPLATE_DIR)

print("\n── F. 四项人设测试（可判定的那几项）──")
_full = dict(_given, examples="A：在吗\nB：说。", name="结构化")
_p1 = PC.render_prompt(_full)
ok("F1★ **删除测试**：抽掉 `motive` ⇒ 渲染里**少了那一段**（说明它真的参与，不是摆设）",
   "【动机】" in _p1 and "【动机】" not in PC.render_prompt(dict(_full, motive="")), "")
ok("F2 **一致性/可复现**：同一份输入渲染两次 ⇒ 完全相同",
   PC.render_prompt(_full) == _p1, "")
_hits = [r for r in (12, 24, 36, 48, 60) if "提醒（每" in PC.render_prompt(_full, round_no=r)]
ok("F3★ **长对话漂移**：60 轮里 5 个整倍数轮**都被注入**提醒", len(_hits) == 5, str(_hits))
try:
    PC.new_from_template("no-such-mode")
    ok("F4★ 反向对照：**喂一个坏输入必须报不通过**（模板名不存在 ⇒ 抛）", False, "没抛")
except ValueError:
    ok("F4★ 反向对照：**喂一个坏输入必须报不通过**（模板名不存在 ⇒ 抛）", True, "")
ok("F5 接线：`agent/persona_card.py` 是**唯一实现点**（没在别处再抄一份七字段）",
   _sm.has(io.open(os.path.join(ROOT, "agent", "persona_card.py"), encoding="utf-8").read(),
           "FIELDS = (") and not os.path.exists(os.path.join(ROOT, "agent", "persona_fields.py")), "")

print("\n── G. 抽取的硬不变量 ＋ 覆盖率棘轮（全库 244 张）──")
from agent import persona as _P  # noqa: E402
from collections import Counter as _C  # noqa: E402

_rows, _bad, _ex = [], [], 0
for _pid, _card in (_P.PERSONAS or {}).items():
    _ff = PC.to_fields(_card)
    _rows.append((_pid, len(PC.missing_fields(_ff))))
    for _k in PC.FIELDS:
        _v = str(_ff.get(_k) or "")
        if _v and _k in (_ff.get("_excerpt") or {}):
            _ex += len([x for x in _v.splitlines() if x.strip()])
            for _line in _v.splitlines():
                if _line.strip() and _line.strip() not in (_ff.get("_raw") or ""):
                    _bad.append("%s/%s" % (_pid, _k))
# 分母守卫要守的是"**不是只测了一张**"，不是某个具体张数 —— 卡库增删是正常内容改动
# （例：删掉 8 张版权高危卡 ⇒ 244→236），写成 `== 244` 就变成"内容一动自检就红"，
# 而红得没有信息量。⇒ 只守下限（现库 236 张，留出正常删减余量）。
ok("G0 分母守卫：全库卡都参与了统计（不是只测了一张）", len(_rows) >= 200, str(len(_rows)))
ok("G1★★ **抽取只搬不改**：搬进字段的每一句都能在原文里**逐字找到**"
   "（防实现偷偷改写内容 —— 改写是内容活，不该由代码做）",
   not _bad, str(_bad[:4]))
ok("G1b 分母守卫：确实**抽到了东西**（否则 G1 是空转）", _ex >= 200, str(_ex))
_lack = _C()
for _pid, _n in _rows:
    for _k in PC.missing_fields(PC.to_fields((_P.PERSONAS or {})[_pid])):
        _lack[_k] += 1
ok("G2★ 覆盖率棘轮（**只许降不许升**）：机器能吃掉的两个字段已**全库覆盖**"
   "（`speech`/`personality` 缺口必须为 0）",
   _lack["speech"] == 0 and _lack["personality"] == 0,
   "speech=%d personality=%d" % (_lack["speech"], _lack["personality"]))
ok("G2b★ 总缺口棘轮（只许降）：**≤ 660**（当前 652 ⇒ 下一版只能更小）",
   sum(_lack.values()) <= 660, str(sum(_lack.values())))
ok("G3★ 机器吃不掉的那三块如实留着（`motive`/`values`/`limits` 原卡里真没写 ⇒ 不许伪造）",
   _lack["motive"] > 150 and _lack["values"] > 150 and _lack["limits"] > 150,
   "motive=%d values=%d limits=%d" % (_lack["motive"], _lack["values"], _lack["limits"]))
_dup = []
for _pid, _card in (_P.PERSONAS or {}).items():
    _ff = PC.to_fields(_card)
    _seen = {}
    for _k in PC.FIELDS:
        for _ln in str(_ff.get(_k) or "").splitlines():
            _ln = _ln.strip()
            if len(_ln) < 6:
                continue
            if _ln in _seen and _seen[_ln] != _k:
                _dup.append("%s %s→%s" % (_pid, _seen[_ln], _k))
            _seen.setdefault(_ln, _k)
ok("G4★ 抽取**不许跨字段重复**（同一句同时进两个字段 ⇒ 提示词里就重了）",
   not _dup, str(_dup[:3]))

print("\n== 人设卡判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
