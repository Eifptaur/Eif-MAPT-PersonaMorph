# -*- coding: utf-8 -*-
"""第 12/13 条判据：计时提醒 + 节假日问候（不需要微信、不联网、不花 token）。

跑法： py -3 scripts\\timers_holiday_selftest.py      退出码 0=全过 / 1=有失败
四层：A 计量与上限 · B 到点发送（幂等/重试/暂停/禁言）· C 节日表与问候四道收紧 ·
D 红线（定时只能设当前会话）+ 接线 + 阴性对照。
"""
from __future__ import annotations

import json
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

from agent import holidays                         # noqa: E402
from agent import timers                           # noqa: E402
from agent import tools as T                       # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def ts(y, m, d, hh=10, mm=0):
    return time.mktime((y, m, d, hh, mm, 0, 0, 0, -1))


def main():
    tmp = tempfile.mkdtemp(prefix="timer-judge-")
    timers.path = lambda: os.path.join(tmp, "timers.json")
    holidays.state_path = lambda: os.path.join(tmp, "holiday_state.json")
    holidays.custom_path = lambda: os.path.join(tmp, "holidays.json")
    now = 1_800_000_000.0            # 固定"现在"，避免依赖真实时间

    print("== A. 计时提醒：计量 / 夹断 / 上限 ==")
    r = timers.add("group:g1", "喝水", seconds=300, by="我", now=now)
    ok("正常登记：ok + 编号 + 到点时间=现在+秒数",
       r["ok"] and r["id"] == 1 and abs(r["fire_at"] - (int(now * 1000) + 300000)) < 5, r)
    ok("太短被夹到 30 秒并标记 clamped",
       (lambda x: x["seconds"] == 30 and x["clamped"])(timers.add("group:g1", "太短", seconds=5, now=now)))
    ok("超长被夹到 7 天",
       (lambda x: x["seconds"] == timers.MAX_SECONDS)(timers.add("group:g1", "太长",
                                                                 seconds=99 * 24 * 3600, now=now)))
    ok("不给时间 ⇒ 明确报错", not timers.add("group:g1", "没时间", now=now)["ok"])
    ok("空内容 ⇒ 明确报错", not timers.add("group:g2", "  ", seconds=60, now=now)["ok"])
    r4 = timers.add("group:g1", "第四条", seconds=60, now=now)
    ok("每会话 3 条上限：第 4 条被拒且说清上限", (not r4["ok"]) and "上限" in r4["error"], r4)
    ok("列出只剩本会话的（含剩余秒数）",
       len(timers.list_all("group:g1")) == 3 and timers.list_all("group:g2") == []
       and all(it["left_seconds"] > 0 for it in timers.list_all("group:g1")))
    for i in range(4, 12):
        for _k in range(3):
            timers.add("group:g%d" % i, "铺满", seconds=60, now=now)
    total = len(timers.list_all())
    ok("全局 20 条上限生效（铺到上限后不再增加）", total == timers.MAX_PENDING_TOTAL, total)
    ok("全局满时新请求被拒", not timers.add("group:new", "再来一条", seconds=60, now=now)["ok"])
    ok("取消单条", timers.cancel("group:g1", 1) and all(it["id"] != 1 for it in timers.list_all("group:g1")))
    ok("取消本会话全部", timers.cancel("group:g1") and timers.list_all("group:g1") == [])
    ok("取消不存在的返回 False", not timers.cancel("group:g1", 999))
    timers.add("group:g2", "给 g2 真的加一条", seconds=120, now=now)
    ok("落盘后重载仍在（新读者视角）", any(it["chat"] == "group:g2" for it in timers.list_all()))
    ok("落盘文件的 JSON 合法且含 pending 条目",
       (lambda d: d["items"] and any(x.get("status") == "pending" for x in d["items"]))(
           json.load(open(timers.path(), encoding="utf-8"))))
    timers.add("group:due", "到点了", seconds=30, now=now)
    ok("due() 只挑到点的", len(timers.due(now=now + 31)) == 1 and timers.due(now=now + 5) == [])

    print("== B. 到点发送：幂等 / 重试 / 暂停 / 禁言 ==")
    timers.path = lambda: os.path.join(tmp, "timers2.json")
    sent = []
    timers.add("group:t1", "该发了", seconds=30, now=now)
    st = timers.run_once(lambda ck, tx: (sent.append((ck, tx)), True)[1], now=now + 31, log=None)
    ok("到点发出一次", st["sent"] == 1 and len(sent) == 1 and "该发了" in sent[0][1], st)
    sent2 = []
    st2 = timers.run_once(lambda ck, tx: (sent2.append(tx), True)[1], now=now + 60)
    ok("再跑一遍不会重复发（幂等）", st2["sent"] == 0 and not sent2, st2)
    timers.add("group:t2", "会失败", seconds=30, now=now)
    fails = {"n": 0}

    def bad(ck, tx):
        fails["n"] += 1
        return False

    st3 = timers.run_once(bad, now=now + 31)
    ok("发送失败：记一次尝试、状态仍是 pending（下轮会再试）",
       st3["failed"] == 1 and len(timers.list_all("group:t2")) == 1 and fails["n"] == 1, st3)
    timers.run_once(bad, now=now + 40)
    st4 = timers.run_once(bad, now=now + 50)
    ok("连续失败到上限 ⇒ 放弃并留痕（不再无限重试）",
       fails["n"] == timers.MAX_ATTEMPTS and not timers.list_all("group:t2")
       and any(h.get("status") == "failed" for h in timers.snapshot()["history"]), fails["n"])
    timers.add("group:t3", "暂停中", seconds=30, now=now)
    st5 = timers.run_once(lambda ck, tx: True, now=now + 31, paused=True)
    ok("暂停中一条都不发，状态也不动（恢复后补发）",
       st5["sent"] == 0 and st5["skipped"] == "paused" and len(timers.list_all("group:t3")) == 1, st5)
    st6 = timers.run_once(lambda ck, tx: True, now=now + 40)
    ok("恢复后补发", st6["sent"] == 1, st6)
    timers.add("group:t4", "禁言中", seconds=30, now=now)
    st7 = timers.run_once(lambda ck, tx: True, now=now + 31, muted=lambda ck: ck == "group:t4")
    ok("会话被指令禁言期间不发、也不丢",
       st7["sent"] == 0 and st7["skipped"] == "muted" and len(timers.list_all("group:t4")) == 1, st7)
    st8 = timers.run_once(lambda ck, tx: True, now=now + 40)
    ok("禁言解除后补发（阴性对照：换个 muted 判定就发了）", st8["sent"] == 1, st8)
    ok("快照带条数/最近一条/上限口径",
       timers.snapshot()["limits"]["per_chat"] == timers.MAX_PENDING_PER_CHAT)

    print("== C. 节假日：表 / 提示句 / 主动问候四道收紧 ==")
    ok("公历固定节日认得出", holidays.named("2026-10-01") == "国庆节" and holidays.named("2026-01-01") == "元旦")
    ok("预置农历节日认得出（不猜、写死表）", holidays.named("2026-02-17") == "春节")
    ok("非节日返回 None", holidays.named("2026-09-13") is None)
    ok("提示句：非节日空串、节日带名字",
       holidays.scene_line(now=ts(2026, 9, 13)) == "" and "中秋节" in holidays.scene_line(now=ts(2026, 9, 25)))
    # 用户自定义表：覆盖 + 新增
    with open(holidays.custom_path(), "w", encoding="utf-8") as f:
        json.dump({"2026-09-13": "自家纪念日", "2026-02-17": "农历新年"}, f, ensure_ascii=False)
    ok("用户在 data/holidays.json 里能新增节日", holidays.named("2026-09-13") == "自家纪念日")
    ok("用户自定义优先于内置", holidays.named("2026-02-17") == "农历新年")

    groups = [{"name": "群deepseek", "wxid": "wxid_a"}, {"name": "别的群", "wxid": "wxid_b"}]
    mid_autumn = ts(2026, 9, 25, 10, 0)
    ok("passive（默认）⇒ 一条都不主动发",
       holidays.due_greetings({"holiday": {"mode": "passive", "greet_chats": ["群deepseek"]}}, groups,
                              now=mid_autumn) == [])
    ok("active 但白名单为空 ⇒ 也不发（防节日变群发）",
       holidays.due_greetings({"holiday": {"mode": "active", "greet_chats": []}}, groups, now=mid_autumn) == [])
    due = holidays.due_greetings({"holiday": {"mode": "active", "greet_chats": ["群deepseek"]}}, groups,
                                 now=mid_autumn)
    ok("active + 白名单 + 节日 + 时段内 ⇒ 只给名单里的会话发", len(due) == 1 and due[0]["chat_key"] == "group:wxid_a", due)
    ok("问候文本用上了节日名", "中秋节" in due[0]["text"] and "快乐" in due[0]["text"], due[0]["text"])
    holidays.mark_greeted(due[0]["day"], due[0]["chat_key"])
    ok("同一天同一会话只问候一次（第二次不再出现）",
       holidays.due_greetings({"holiday": {"mode": "active", "greet_chats": ["群deepseek"]}}, groups,
                              now=mid_autumn) == [])
    ok("时段外（23 点）不发",
       holidays.due_greetings({"holiday": {"mode": "active", "greet_chats": ["群deepseek"]}}, groups,
                              now=ts(2026, 9, 26, 23, 0)) == [])
    ok("非节日不发",
       holidays.due_greetings({"holiday": {"mode": "active", "greet_chats": ["群deepseek"]}}, groups,
                              now=ts(2026, 9, 20, 10, 0)) == [])
    ok("起始小时可调（调到 20 点时 10 点不发）",
       holidays.due_greetings({"holiday": {"mode": "active", "greet_chats": ["群deepseek"], "greet_hour": 20}},
                              groups, now=mid_autumn) == [])

    print("== D. 红线与接线 ==")
    defs = {d["name"]: d for d in T._builtin_tool_defs()}
    ok("三个定时工具都注册了", all(n in defs for n in ("set_timer", "list_timers", "cancel_timer")))
    props = set(defs["set_timer"]["parameters"]["properties"].keys())
    ok("红线：set_timer 参数里**没有**任何「发给谁」的入口（只能当前会话）",
       not (props & {"chat", "chat_id", "chat_key", "group", "target", "to", "wxid"}), props)
    ok("红线：工具描述写明只对当前会话与条数上限",
       "当前会话" in defs["set_timer"]["description"] and "3 条" in defs["set_timer"]["description"])
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pm = open(os.path.join(root, "scripts", "persona_morph.py"), encoding="utf-8").read()
    html = open(os.path.join(root, "agent", "console_html.py"), encoding="utf-8").read()
    wui = open(os.path.join(root, "agent", "webui.py"), encoding="utf-8").read()
    cfg_py = open(os.path.join(root, "agent", "config.py"), encoding="utf-8").read()
    cex = open(os.path.join(root, "config.example.json"), encoding="utf-8").read()
    ok("巡检线程在（计时 + 节日一起跑）", "_start_timer_holiday_loop(orch, wechat)" in pm)
    ok("到点发送走 sender（风险闸门照过）", "orch.sender.send_text_batch(chat_key, text)" in pm)
    ok("暂停中不发、禁言中不发、都接上了",
       'paused = bool(getattr(orch, "paused", False) or getattr(orch, "stopped", False))' in pm
       and "tier_control.is_muted(ck)" in pm)
    ok("节日问候发成功才记账（标记 greeted）", "holidays.mark_greeted(g[\"day\"], g[\"chat_key\"])" in pm)
    pr_src = open(os.path.join(root, "agent", "prompt.py"), encoding="utf-8").read()
    sp_src = open(os.path.join(root, "agent", "system_prompt.py"), encoding="utf-8").read()
    ok("提示词里有 set_timer 规则",
       "set_timer(note=" in pr_src)
    ok("节日提示句挂在系统提示词的独立小节（第 11 条给了它开关）",
       "def _holiday_hint_line" in pr_src and "_sp.holiday_hint()" in pr_src
       and "from . import holidays as _hol" in sp_src)
    ok("控制台有四个键 + 现状读数",
       all(t in html for t in ('data-cfg="timers.enabled"', 'data-cfg="holiday.mode"',
                               'data-cfg="holiday.greet_chats"', 'data-cfg="holiday.greet_hour"', 'id="timerStat"')))
    ok("名单类字段按逗号切数组（含节日白名单）", "holiday.greet_chats" in html and "逗号/换行 → 数组" in html)
    ok("读数来自 /api/status 的 timers/holiday 段",
       'st["timers"] = _tmr.snapshot()' in wui and 'st["holiday"] = _hol2.snapshot()' in wui)
    ok("配置默认段 + 示例同步",
       '"timers": {' in cfg_py and '"holiday": {' in cfg_py and '"mode": "passive"' in cfg_py
       and '"timers"' in cex and '"holiday"' in cex)

    print("== E. 端到端：工具 → 落盘 → 巡检真发出 ==")
    timers.path = lambda: os.path.join(tmp, "timers3.json")
    ctx = {"chat_key": "group:e2e", "self_nickname": "群deepseek"}
    out = T._exec_set_timer(ctx, {"note": "开会", "minutes": 5})
    blob = json.dumps(out, ensure_ascii=False)
    ok("工具调用真的写进了待触发表", "已记下" in blob and len(timers.list_all("group:e2e")) == 1, blob[:120])
    ok("工具回话里带上了条数上限（让模型知道有约束）", "最多挂 3 条" in blob)
    fired = []
    st = timers.run_once(lambda ck, tx: (fired.append((ck, tx)), True)[1], now=now + 301)
    ok("到点由巡检发出、内容带「⏰ 提醒：」前缀",
       st["sent"] == 1 and fired and fired[0][0] == "group:e2e" and "⏰ 提醒：开会" == fired[0][1], fired)
    lst = json.dumps(T._exec_list_timers(ctx, {}), ensure_ascii=False)
    ok("发完后 list_timers 报空（状态确实推进了）", "没有待触发" in lst, lst[:80])
    T._exec_set_timer(ctx, {"note": "取消我", "seconds": 600})
    one = timers.list_all("group:e2e")[0]
    cx = json.dumps(T._exec_cancel_timer(ctx, {"timer_id": one["id"]}), ensure_ascii=False)
    ok("cancel_timer 工具真能取消", "已取消" in cx and timers.list_all("group:e2e") == [], cx[:80])
    import agent.config as cfgmod2
    real_get = cfgmod2.get_config
    cfgmod2.get_config = lambda: {"timers": {"enabled": False}}
    try:
        off = json.dumps(T._exec_set_timer(ctx, {"note": "关掉时不该记", "seconds": 60}), ensure_ascii=False)
    finally:
        cfgmod2.get_config = real_get
    ok("关掉开关后工具直接拒绝（timers.enabled=false）",
       "已在控制台关闭" in off and timers.list_all("group:e2e") == [], off[:80])

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
