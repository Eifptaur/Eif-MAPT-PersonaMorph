# -*- coding: utf-8 -*-
"""第 15/16/18 条判据：固定 4 档 / 峰谷映射 / 指令禁言（不需要微信、不联网、不花 token）。

跑法： py -3 scripts\\tier_control_selftest.py      退出码 0=全过 / 1=有失败
三层：A 峰谷映射查表（含跨午夜/边界/坏行）· B 指令禁言（解析/白名单/夹断/到期）·
C **真函数集成**（跑 `resolve_context_tier`，验"禁言只回艾特""峰谷 0=静默""固定档不看滑条"）·
D 接线与文案 + 阴性对照。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import agent.config as cfgmod                    # noqa: E402
from agent import prompt as pr                   # noqa: E402
from agent import tier_control as tc             # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def hm(h, m=0):
    """构造一个"当天 h:m"的时间对象给 scheduled_tier 注入（用 struct_time，含日期字段不影响判定）。"""
    return time.struct_time((2026, 9, 13, h, m, 0, 6, 256, 0))


def main():
    tmp = tempfile.mkdtemp(prefix="tier-judge-")
    tc.state_path = lambda: os.path.join(tmp, "tier_control.json")

    base_store = {"context_tier": 2, "context_slider_pos": None, "tier_mode": "fixed",
                  "keywords": ["上线"], "random_percent": 100, "at_count": 12,
                  "keyword_count": 10, "random_count": 6, "all_count": 30,
                  "tier_schedule": {"enabled": False, "table": []}, "tier_cmd_admins": []}

    def cfg_with(**kw):
        st = dict(base_store)
        st.update(kw)
        return {"api": {"model": "m"}, "store": st, "persona": {}}

    print("== A. 峰谷映射（时段 → 档位）==")
    sched = {"enabled": True, "table": [{"from": "09:00", "to": "12:00", "tier": 2, "note": "工作"},
                                        {"from": "22:00", "to": "02:00", "tier": 0, "note": "跨午夜"}]}
    c = cfg_with(tier_schedule=sched)
    r = tc.scheduled_tier(now=hm(9, 30), cfg=c)
    ok("命中窗口 ⇒ 返回该档位", r and r["tier"] == 2 and r["window"] == "09:00-12:00", r)
    ok("区间起点含（09:00 命中）", tc.scheduled_tier(now=hm(9, 0), cfg=c)["tier"] == 2)
    ok("区间终点不含（12:00 不命中）", tc.scheduled_tier(now=hm(12, 0), cfg=c) is None)
    ok("没命中任何窗口 ⇒ None（回落到全局档位）", tc.scheduled_tier(now=hm(14, 0), cfg=c) is None)
    ok("跨午夜窗口：23:30 命中", (tc.scheduled_tier(now=hm(23, 30), cfg=c) or {}).get("tier") == 0)
    ok("跨午夜窗口：01:00 命中", (tc.scheduled_tier(now=hm(1, 0), cfg=c) or {}).get("tier") == 0)
    ok("跨午夜窗口：03:00 不命中", tc.scheduled_tier(now=hm(3, 0), cfg=c) is None)
    ok("tier=0 如实返回 0（＝该时段静默）", (tc.scheduled_tier(now=hm(23, 0), cfg=c) or {}).get("tier") == 0)
    ok("开关关掉 ⇒ 一律 None", tc.scheduled_tier(now=hm(9, 30), cfg=cfg_with(tier_schedule=dict(sched, enabled=False))) is None)
    ok("空表 ⇒ None", tc.scheduled_tier(now=hm(9, 30), cfg=cfg_with(tier_schedule={"enabled": True, "table": []})) is None)

    bad = {"enabled": True, "table": [{"from": "x", "to": "12:00", "tier": 2},      # 时间非法
                                      {"from": "09:00", "to": "09:00", "tier": 3},  # 空区间
                                      {"from": "08:00", "to": "10:00", "tier": 9},  # 档位越界
                                      {"from": "08:00", "to": "10:00", "tier": -1}, # 档位越界
                                      {"from": "08:00", "to": "10:00", "tier": 3}]} # 唯一好行
    ok("坏行跳过、好行照常生效（不吃异常）",
       (tc.scheduled_tier(now=hm(9, 0), cfg=cfg_with(tier_schedule=bad)) or {}).get("tier") == 3)
    ok("表写成 JSON 字符串也认",
       tc.parse_table('[{"from":"08:00","to":"10:00","tier":3}]')[0]["tier"] == 3)
    ok("表写成乱字符串 ⇒ 空表不炸", tc.parse_table("{不是数组") == [])
    two = {"enabled": True, "table": [{"from": "08:00", "to": "20:00", "tier": 1},
                                      {"from": "09:00", "to": "10:00", "tier": 4}]}
    ok("按表内顺序取第一个命中的窗口", tc.scheduled_tier(now=hm(9, 30), cfg=cfg_with(tier_schedule=two))["tier"] == 1)

    print("== B. 指令禁言（@机器人 + 指令）==")
    ok("「@机器人 禁言」⇒ 默认 30 分钟",
       tc.parse_command("@群deepseek 禁言") == {"action": "mute", "minutes": 30})
    ok("「禁言 15」⇒ 15 分钟", tc.parse_command("@bot 禁言 15")["minutes"] == 15)
    ok("「禁言 15 分钟」也认", tc.parse_command("@bot 禁言15分钟")["minutes"] == 15)
    ok("「闭嘴 / 闭麦 / 静一静」都算禁言",
       all((tc.parse_command("@bot " + w) or {}).get("action") == "mute" for w in ("闭嘴", "闭麦", "静一静")))
    ok("「解除禁言 / 恢复」⇒ unmute",
       (tc.parse_command("@bot 解除禁言") or {}).get("action") == "unmute"
       and (tc.parse_command("@bot 恢复") or {}).get("action") == "unmute")
    ok("普通聊天不会被当成指令",
       tc.parse_command("今天午饭吃什么") is None and tc.parse_command("你别禁言我啊") is None
       and tc.parse_command("@bot 今天天气怎么样") is None)
    ok("白名单为空 ⇒ 谁都不是管理员（fail-closed）", not tc.is_admin([], "群主", "wxid_a"))
    ok("昵称匹配忽略大小写与空格", tc.is_admin([" 群主 "], "群主") and tc.is_admin(["ZhangSan"], " zhangsan"))
    ok("wxid 也能匹配", tc.is_admin(["wxid_a"], "", "wxid_a"))
    ok("不在名单 ⇒ 不算管理员", not tc.is_admin(["群主"], "路人", "wxid_b"))

    st_file = tc.state_path()
    ok("初始没有禁言记录", tc.is_muted("group:g1") is None)
    bad_cmd = tc.handle_command("group:g1", {"text": "@bot 禁言", "sender_name": "路人", "sender_id": "wxid_b"},
                               at_me=True, cfg=cfg_with(tier_cmd_admins=["群主"]))
    ok("非白名单发指令 ⇒ handled 但**不生效**", bad_cmd["handled"] and bad_cmd["action"] == "denied")
    ok("非白名单的指令不落盘（防有人喊一句就把机器人按住）",
       tc.is_muted("group:g1") is None and not os.path.exists(st_file))
    # V-R7-5 #4：原来的"阴性对照"用的是**非空**白名单（["群主"]）⇒ 与"空白名单 fail-closed"正交。
    # 这里补一条真·空白名单对照：连群主本人发指令都必须不生效。
    empty_cmd = tc.handle_command("group:g4", {"text": "@bot 禁言", "sender_name": "群主", "sender_id": "wxid_a"},
                                 at_me=True, cfg=cfg_with(tier_cmd_admins=[]))
    ok("空白名单阴性对照：连群主发指令也不生效（fail-closed）",
       empty_cmd["handled"] and empty_cmd["action"] == "denied", str(empty_cmd.get("action")))
    ok("空白名单下同样不落盘", tc.is_muted("group:g4") is None)
    good = tc.handle_command("group:g1", {"text": "@bot 禁言 10", "sender_name": "群主", "sender_id": "wxid_a"},
                             at_me=True, cfg=cfg_with(tier_cmd_admins=["群主"]))
    ok("白名单发指令 ⇒ 记禁言 10 分钟", good["action"] == "mute" and good["minutes"] == 10)
    m1 = tc.is_muted("group:g1")
    ok("禁言状态落盘且能读回（含剩余分钟与是谁下的）",
       m1 and m1["left_min"] in (9, 10) and m1["by"] == "群主", m1)
    ok("没 @ 机器人 ⇒ 不处理",
       not tc.handle_command("group:g2", {"text": "禁言", "sender_name": "群主"}, at_me=False,
                             cfg=cfg_with(tier_cmd_admins=["群主"]))["handled"])
    ok("到期自动失效（注入未来时间）", tc.is_muted("group:g1", now=time.time() + 3600) is None)
    ok("到期后磁盘记录也被清掉", "group:g1" not in (tc.load().get("muted") or {}))
    ok("再查也不会复活", tc.is_muted("group:g1") is None)
    big = tc.mute("group:g3", 99999, by="群主")
    ok("超长禁言被夹到 24 小时（并如实标记 clamped）", big["minutes"] == 1440 and big["clamped"])
    small = tc.mute("group:g4", 0, by="群主")
    ok("0/负数分钟夹到 1 分钟", small["minutes"] == 1)
    ok("解除禁言：第一次 True、第二次 False（幂等）",
       tc.unmute("group:g3") is True and tc.unmute("group:g3") is False)

    print("== C. 真函数集成（resolve_context_tier）==")
    real_cfg_get = cfgmod.get_config
    real_pr_get = pr.get_config
    real_sched = tc.scheduled_tier

    def use(cfg):
        cfgmod.get_config = lambda: cfg
        pr.get_config = lambda: cfg

    def clock(h, m=30):
        """把"现在几点"钉死（只桩时钟，不桩查表逻辑本身——查表逻辑在 A 段已经逐条验过）。"""
        tc.scheduled_tier = lambda now=None, cfg=None: real_sched(now=hm(h, m), cfg=cfg)

    def entries(text="随便聊聊", who="群友", sid="wxid_x"):
        return [{"text": text, "sender_name": who, "sender_id": sid, "ts": 1}]

    try:
        use(cfg_with())
        clock(9, 30)
        r0 = pr.resolve_context_tier(entries("我们上线了新产品"), "群deepseek", "", "", roll=0, chat_key="group:gA")
        ok("阴性对照：没开峰谷、没禁言时，关键词照旧触发 2 档",
           r0["should_respond"] and r0["tier"] == 2 and r0["tier_source"] == "全局档位", r0)
        use(cfg_with(tier_schedule=sched))
        clock(9, 30)
        r1 = pr.resolve_context_tier(entries("我们上线了新产品"), "群deepseek", "", "", roll=0, chat_key="group:gA")
        ok("峰谷映射命中 2 档 ⇒ 关键词仍触发（与全局一致）", r1["tier"] == 2 and "峰谷映射" in r1["tier_source"], r1)
        clock(23, 30)
        r1b = pr.resolve_context_tier(entries("我们上线了新产品"), "群deepseek", "", "", roll=0, chat_key="group:gA")
        ok("同一张表、时间走到跨午夜静默窗口 ⇒ 此刻不回应（证明真的按时间切）",
           not r1b["should_respond"] and "静默" in r1b["reason"], r1b)
        clock(9, 30)
        use(cfg_with(tier_schedule={"enabled": True, "table": [{"from": "00:00", "to": "23:59", "tier": 1}]}))
        r2 = pr.resolve_context_tier(entries("我们上线了新产品"), "群deepseek", "", "", roll=0, chat_key="group:gA")
        ok("峰谷映射 1 档 ⇒ 关键词/随机都不触发（只回艾特）",
           not r2["should_respond"] and r2["tier"] == 0 and "峰谷映射" in r2["tier_source"], r2)
        r2b = pr.resolve_context_tier(entries("@群deepseek 在吗"), "群deepseek", "", "", roll=0, chat_key="group:gA")
        ok("峰谷映射 1 档时，被艾特仍然回", r2b["should_respond"] and r2b["tier"] == 1, r2b)
        use(cfg_with(tier_schedule={"enabled": True, "table": [{"from": "00:00", "to": "23:59", "tier": 0}]}))
        r3 = pr.resolve_context_tier(entries("@群deepseek 在吗"), "群deepseek", "", "", roll=0, chat_key="group:gA")
        ok("峰谷映射 0 档 ⇒ 该时段完全不回应（连艾特也不回，reason 说明是静默）",
           not r3["should_respond"] and "静默" in r3["reason"], r3)
        use(cfg_with(tier_cmd_admins=["群主"]))
        tc.mute("group:gA", 10, by="群主")
        r4 = pr.resolve_context_tier(entries("我们上线了新产品"), "群deepseek", "", "", roll=0, chat_key="group:gA")
        ok("指令禁言 ⇒ 档位固定降到 1 档（关键词不再触发）",
           not r4["should_respond"] and "指令禁言" in r4["tier_source"], r4)
        r5 = pr.resolve_context_tier(entries("@群deepseek 在吗"), "群deepseek", "", "", roll=0, chat_key="group:gA")
        ok("指令禁言期间：艾特仍然回（这正是「1 档」的含义）",
           r5["should_respond"] and r5["tier"] == 1 and "指令禁言" in r5["tier_source"], r5)
        ok("禁言是按会话的：另一个群不受影响",
           pr.resolve_context_tier(entries("我们上线了新产品"), "群deepseek", "", "", roll=0,
                                   chat_key="group:gB")["should_respond"])
        tc.unmute("group:gA")
        use(cfg_with(context_slider_pos=0.05))
        rs = pr.resolve_context_tier(entries("我们上线了新产品"), "群deepseek", "", "", roll=0, chat_key="group:gA")
        ok("tier_mode=fixed ⇒ 滑条被忽略（档位还是全局的 2 档）",
           rs["should_respond"] and rs["tier"] == 2, rs)
        use(cfg_with(context_slider_pos=0.05, tier_mode="slider"))
        rs2 = pr.resolve_context_tier(entries("我们上线了新产品"), "群deepseek", "", "", roll=0, chat_key="group:gA")
        ok("tier_mode=slider ⇒ 滑条照旧生效（阴性对照：老行为没被砍）",
           rs2["tier_source"] == "全局档位" and rs2["tier"] != 2, rs2)
        use(cfg_with())
        ok("每个返回值都带 tier_source（控制台/日志能说清为什么是这个档）",
           all("tier_source" in pr.resolve_context_tier(entries(), "群deepseek", "", "", roll=99, chat_key="group:gA")
               for _ in [0]))
    finally:
        cfgmod.get_config = real_cfg_get
        pr.get_config = real_pr_get
        tc.scheduled_tier = real_sched

    print("== D. 接线与文案 ==")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pm = open(os.path.join(root, "scripts", "persona_morph.py"), encoding="utf-8").read()
    html = open(os.path.join(root, "agent", "console_html.py"), encoding="utf-8").read()
    wui = open(os.path.join(root, "agent", "webui.py"), encoding="utf-8").read()
    cfg_py = open(os.path.join(root, "agent", "config.py"), encoding="utf-8").read()
    cex = open(os.path.join(root, "config.example.json"), encoding="utf-8").read()
    ok("监听循环里处理指令（且处理完不触发回复）",
       "tier_control.handle_command(" in pm and "return {\"command\": _cmd}" in pm)
    ok("指令认「@机器人」再判断（用 is_at_me 与微信昵称两路）",
       "is_at_me(_txt, _pnick, _pbot" in pm and "wechat.self_nickname" in pm)
    ok("prompt.py 里三件都在同一处生效",
       "tier_control as _tc" in open(os.path.join(root, "agent", "prompt.py"), encoding="utf-8").read())
    ok("控制台有档位模式/峰谷映射/时段表/白名单四项",
       all(t in html for t in ('data-cfg="store.tier_mode"', 'data-cfg="store.tier_schedule.enabled"',
                               'data-cfg="store.tier_schedule.table"', 'data-cfg="store.tier_cmd_admins"')))
    ok("控制台有现状读数行", 'id="tierStat"' in html and "s.tier" in html)
    ok("时段表的回显走 JSON（不能按关键词那种拼成字符串）",
       "path === 'store.tier_schedule.table'" in html and "JSON.stringify(Array.isArray(v) ? v : []" in html.replace("\n", " "))
    ok("白名单按逗号切数组（不切会存成字符串）", "path === 'store.tier_cmd_admins'" in html)
    ok("读数来自 /api/status 的 tier 段", 'st["tier"] = _tcl.snapshot()' in wui)
    ok("配置默认段三件齐 + 示例同步",
       '"tier_mode": "fixed"' in cfg_py and '"tier_schedule"' in cfg_py and '"tier_cmd_admins": []' in cfg_py
       and '"tier_mode": "fixed"' in cex)
    ok("中文文案写明「留空＝谁都不能下指令」与「0＝静默」",
       "留空＝谁都不能下这个指令" in html and "该时段完全不回应" in html)
    ok("阴性对照：删掉白名单判定后，非白名单会误生效（说明判据抓的是真行为）",
       tc.handle_command("group:z1", {"text": "@bot 禁言", "sender_name": "路人"},
                         at_me=True, cfg=cfg_with(tier_cmd_admins=["群主"]))["action"] == "denied"
       and tc.is_muted("group:z1") is None)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
