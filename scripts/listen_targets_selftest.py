#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据：监听目标**按 wxid 认群**，同名群不许"勾一个监听两个"（第四轮审计候选 W-1）。

跑法： runtime\\python\\python.exe scripts\\listen_targets_selftest.py   退出码 0=全过 / 1=有失败

为什么（审计给的现场）：勾选框（`console_html.py`）存的是 `g.name`、选群（`persona_morph.py`）比的是
`g["name"] in _wl`、运行明细只写 `group_name(chat_id)` ⇒ **两个同名群「勾一个＝监听两个」**，
明细里两间群同名 ⇒「这条回复挂在群 X 名下」无法唯一确定是哪一间（网友的串群报障卡在这一层）。

判据守五件事：
  ① 白名单写 wxid ⇒ 精确命中（唯一身份）；
  ② 白名单写**群名**（老配置）⇒ 只有**唯一**同名群才命中，行为与升级前一致；
  ③ **同名群 + 名字** ⇒ 跳过（fail-closed）并报出来 + 给出可复制的 wxid；反例锚＝老写法会选中两个；
  ④ deny 名单在 wxid 与名字两种写法下都生效；白名单空＝监听所有群（老口径不变）；
  ⑤ 收口（前端后端一体）：三处调用点都走这一个解析器、控制台勾选存 wxid、明细带唯一身份。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import listen_targets as LT                                          # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def _ids(res):
    return [g["wxid"] for g in res["groups"]]


# 两个**同名**群（审计点名的场景）+ 一个正常群
GROUPS = [
    {"name": "测试", "wxid": "111@chatroom"},
    {"name": "测试", "wxid": "222@chatroom"},
    {"name": "家人", "wxid": "333@chatroom"},
]


def main():
    print("── A. 白名单写 wxid（新口径）：唯一身份，精确命中 ──")
    r = LT.resolve_groups(GROUPS, ["111@chatroom"])
    ok("A1 写 wxid ⇒ 只命中那一个（同名群里也分得清）", _ids(r) == ["111@chatroom"], _ids(r))
    ok("A2 命中路径不该被记成「靠名字对上的」", r["used_name"] == [] and not r["ambiguous"], r)
    r = LT.resolve_groups(GROUPS, ["111@chatroom", "222@chatroom"])
    ok("A3 同名两间都写 wxid ⇒ 两间都监听（这是用户真想要的）", _ids(r) == ["111@chatroom", "222@chatroom"], _ids(r))
    r = LT.resolve_groups(GROUPS, ["111@chatroom", "111@chatroom"])
    ok("A4 重复写同一个 ⇒ 去重，不重复监听", _ids(r) == ["111@chatroom"], _ids(r))

    print("── B. 白名单写**群名**（老配置）：唯一同名群才命中（行为与升级前一致）──")
    r = LT.resolve_groups(GROUPS, ["家人"])
    ok("B1 名字唯一 ⇒ 照旧命中（老配置升级后不会突然不监听）", _ids(r) == ["333@chatroom"], _ids(r))
    ok("B2 靠名字对上的会被标出来（好提示用户重勾成 wxid）", r["used_name"] == ["家人"], r["used_name"])
    r2 = LT.resolve_groups(GROUPS, ["家人", "111@chatroom"])
    ok("B3 老名字与新 wxid 混着写也成立", _ids(r2) == ["333@chatroom", "111@chatroom"], _ids(r2))

    print("── C. 同名群 + 白名单写名字 ⇒ **跳过并报出来**（fail-closed，别替用户猜）──")
    r = LT.resolve_groups(GROUPS, ["测试"])
    ok("C1 同名群 + 名字 ⇒ 一个都不监听（不猜是哪一间）", _ids(r) == [], _ids(r))
    ok("C2 如实报出「哪几个群同名、各是谁」", len(r["ambiguous"]) == 1
       and r["ambiguous"][0]["name"] == "测试"
       and sorted(r["ambiguous"][0]["wxids"]) == ["111@chatroom", "222@chatroom"], r["ambiguous"])
    ok("C3 同名那一条**不算 missing**（原因不同，别混成一句）", r["missing"] == [], r["missing"])
    _d = LT.describe(r["groups"], r)
    ok("C4 给人看的那行里点了名 + 给了下一步（去控制台重勾）", "测试" in _d and "重新勾" in _d, _d[:120])
    # 反例锚：老写法（`g["name"] in _wl`）在这批数据上会**选中两个** —— 这就是"勾一个监听两个"
    _old = [g for g in GROUPS if g["name"] in ["测试"]]
    ok("C5 反例锚：老写法 `g[\"name\"] in wl` 实测选中 **2** 个（判据确实在盯这件事）",
       len(_old) == 2 and _ids(r) == [], len(_old))
    r = LT.resolve_groups(GROUPS, ["测试", "111@chatroom"])
    ok("C6 同名 + 但另外用 wxid 点名了其中一间 ⇒ 那一间照常监听（跳过只针对「名字」那条）",
       _ids(r) == ["111@chatroom"], _ids(r))

    print("── D. deny / 空白名单 / 没匹配上 ──")
    ok("D1 白名单为空 ⇒ 监听所有群（老口径不变）", _ids(LT.resolve_groups(GROUPS, [])) ==
       ["111@chatroom", "222@chatroom", "333@chatroom"])
    ok("D2 deny 写 wxid 生效", _ids(LT.resolve_groups(GROUPS, [], ["111@chatroom"])) ==
       ["222@chatroom", "333@chatroom"])
    ok("D3 deny 写群名生效（老配置）", _ids(LT.resolve_groups(GROUPS, [], ["家人"])) ==
       ["111@chatroom", "222@chatroom"])
    ok("D4 deny 生效时**不会**把它算成「没匹配上」",
       LT.resolve_groups(GROUPS, ["111@chatroom"], ["111@chatroom"])["missing"] == [])
    r = LT.resolve_groups(GROUPS, ["不存在的群"])
    ok("D5 名字/wxid 都对不上 ⇒ 记进 missing（明确说「没匹配到」而不是静默）",
       _ids(r) == [] and r["missing"] == ["不存在的群"], r)
    ok("D6 群列表为空/脏数据不抛", LT.resolve_groups([], ["x"])["groups"] == []
       and LT.resolve_groups([None, {"name": "没有wxid"}], ["x"])["groups"] == [])
    ok("D7 群参数写成字符串列表也不抛（脏数据）", LT.resolve_groups(["abc"], [])["groups"] == [])

    print("── E. 收口：三处调用点都走这一个解析器（前端后端一体）──")
    PM = open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
    CT = open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
    ok("E1 启动时的选群走 `listen_targets.resolve_groups`", "listen_targets.resolve_groups(groups, whitelist, deny)" in PM)
    ok("E2 保存配置后重算也走同一个解析器（两处不许各写一份匹配）",
       PM.count("listen_targets.resolve_groups(") >= 2, PM.count("listen_targets.resolve_groups("))
    _pm_lines = [ln for ln in PM.splitlines() if 'g["name"] in _wl' in ln or "g['name'] in _wl" in ln]
    ok("E3 反例锚：老写法 `g[\"name\"] in _wl` **一处都不许留**", not _pm_lines, _pm_lines[:2])
    ok("E4 控制台勾选**按 wxid 存**（`pick.has(key)` / `pick.add(key)`）",
       "pick.has(key)" in CT and "pick.add(key)" in CT and "inp.checked=pick.has(g.name)" not in CT)
    ok("E5 控制台把白名单里的 wxid 显示成群名（chips 不显示裸 wxid）",
       "function wlLabel(" in CT and "s.textContent=wlLabel(g)" in CT)
    ok("E6 明细里带唯一身份（`chat_wxid`）：同名群也分得清是哪一间",
       PM.count('"chat_wxid"') >= 3 and '"chat_wxid": chat_id' in PM, PM.count('"chat_wxid"'))
    ok("E7 同名群在勾选列表上被标出来（用户看得见「有同名群」）", "有同名群" in CT)
    _pr = open(os.path.join(ROOT, "agent", "prompt.py"), encoding="utf-8").read()
    ok("E8 每群档位两把都认（群名 或 wxid）——同名群下按名字找档位会串到另一间",
       "_gwxid = chat_key.split" in _pr and "for _gk in (" in _pr)

    print("── F. 同名群在**档位 / 屏蔽名单 / 归因文案**三条路上的残留（第五轮回执 V-R5B-6 / V-R5B-9）──")
    from agent import prompt as _P                                                  # noqa: E402
    _cfg_store = {"store": {"unified_tier": False, "group_tier": {"测试": 1, "KC1": 4}}}
    _keep_gc = _P.get_config
    _P.get_config = lambda: _cfg_store
    try:
        _r_t = _P.resolve_context_tier([{"text": "@我 在吗", "self": False}], wechat_nickname="我",
                                       chat_key="group:KC1", group_name="测试")
    finally:
        _P.get_config = _keep_gc
    ok("F1 同名群的两把档位键同时存在时，**wxid 键优先**（否则给两间同名群各设档位永远只能生效第一间）",
       int(_r_t.get("tier") or 0) == 4, _r_t)
    # ⛔ 2026-09-21 修（第六轮 **V-R6-31**）：这条原来是 `ok(..., 1 != 4)` —— 一句**常真话**，
    #   等于没验。改成**真按老口径算一遍**：老写法（群名键在前）取到的是 1，与 wxid 键取到的 4 不同。
    _old_pick = lambda _g: int((_cfg_store["store"]["group_tier"] or {}).get(_g) or 0)   # noqa: E731
    ok("F2 反例锚：老写法（群名键在前）会取到 1 ⇒ 与本判据取到的 4 **确实不同**（这条判据正是盯这个）",
       _old_pick("测试") == 1 and int(_r_t.get("tier") or 0) == 4 and _old_pick("测试") != int(_r_t.get("tier") or 0),
       "老=%s 新=%s" % (_old_pick("测试"), _r_t.get("tier")))
    _pm_txt = open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
    ok("F3 群列表读失败时，那行说明**不许**再写「改名/退群了？」（归因要跟同一份日志一致）",
       "read_failed=(_groups_read_failed or \"\")" in _pm_txt and "_groups_read_failed" in _pm_txt)
    _d9 = LT.describe([], {"missing": ["某个群"], "ambiguous": [], "used_name": []},
                      read_failed="TimeoutError: 超时")
    ok("F4 `describe(read_failed=…)` 把结论写成「群列表这次没读到（不是改名/退群）」",
       ("没读到" in _d9) and ("改名/退群了？" not in _d9), _d9)
    _d9b = LT.describe([], {"missing": ["某个群"], "ambiguous": [], "used_name": []})
    ok("F5 对照：没有 read_failed 时仍照旧说「改名/退群了？」（别把正常路径也改了）",
       "改名" in _d9b, _d9b)
    _pr_txt = open(os.path.join(ROOT, "agent", "prompt.py"), encoding="utf-8").read()
    ok("F6 屏蔽名单也读 wxid 键（同名群里才能只屏蔽指定那间的人）",
       'blist.get(_key)' in _pr_txt or ('_gwxid' in _pr_txt and 'group_blocklist' in _pr_txt))

    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
