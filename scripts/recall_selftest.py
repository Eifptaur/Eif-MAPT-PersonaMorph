# -*- coding: utf-8 -*-
"""第 14 条判据：撤回后剔除上下文（存档门 / 记忆清理 / 审计留痕 / 接线）——不需要微信在跑。

跑法： py -3 scripts\\recall_selftest.py      退出码 0=全过 / 1=有失败
设计：正例 + **负例**（不能误判的普通聊天、窗口外、不猜、fail-closed）+ 阴性对照
（不调 purge 时那条仍在上下文里 ⇒ 证明效果确实来自这条链）。
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

from agent import recall as rc            # noqa: E402
from agent import store as store_mod      # noqa: E402
from agent.store import ChatStore         # noqa: E402

PASS, FAIL = [], []

XML_PEER = ('<sysmsg type="revokemsg"><revokemsg><session>wxid_aaa</session>'
            '<newmsgid>7788990011223344</newmsgid>'
            '<replacemsg><![CDATA["E" 撤回了一条消息]]></replacemsg></revokemsg></sysmsg>')
XML_SELF = ('<sysmsg type="revokemsg"><revokemsg><session>wxid_aaa</session>'
            '<newmsgid>7788990011223399</newmsgid>'
            '<replacemsg><![CDATA[你撤回了一条消息]]></replacemsg></revokemsg></sysmsg>')


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


class FakeMemory:
    def __init__(self):
        self.calls = []

    def remove(self, chat_key, category, user_id="", target="", content=""):
        self.calls.append({"chat": chat_key, "cat": category, "user_id": user_id,
                           "target": target, "content": content})
        return True


def main():
    tmp = tempfile.mkdtemp(prefix="recall-judge-")
    store_mod.MESSAGES_DIR = os.path.join(tmp, "messages")
    rc.LOG_PATH = os.path.join(tmp, "recall_log.jsonl")
    rc.STATS_PATH = os.path.join(tmp, "recall_stats.json")
    rc.UNMATCHED_PATH = os.path.join(tmp, "recall_unmatched.jsonl")
    log_lines = []
    log = lambda lvl, fmt, *a: log_lines.append(fmt % a if a else fmt)   # noqa: E731

    print("== A. 形态识别（认得出，且不误判）==")
    a1 = rc.parse_recall(XML_PEER)
    ok("4.x XML 撤回报文认出来", bool(a1) and a1["form"] == "xml", a1)
    ok("newmsgid 解析正确", a1 and a1["svrid"] == 7788990011223344, a1 and a1["svrid"])
    ok("replacemsg 里的撤回者名字取到", a1 and a1["who"] == "E", a1 and a1["who"])
    a2 = rc.parse_recall(XML_SELF)
    ok("自己撤回（你撤回了一条消息）判成 self", a2 and a2["self"] is True and a2["svrid"] == 7788990011223399)
    a3 = rc.parse_recall('“海绵宝宝” 撤回了一条消息')
    ok("纯文本形态（中文引号）也认", a3 and a3["who"] == "海绵宝宝" and a3["self"] is False, a3)
    a4 = rc.parse_recall('你撤回了一条消息')
    ok("纯文本「你撤回了一条消息」判成 self", a4 and a4["self"] is True)
    a5 = rc.parse_recall('"Mike" recalled a message')
    ok("英文形态认得出", a5 and a5["who"] == "Mike")
    a6 = rc.parse_recall(XML_PEER.encode("utf-8"))
    ok("bytes 形态（库给的是 bytes）也认", bool(a6) and a6["svrid"] == 7788990011223344)
    ok("「拍一拍」系统消息不被认成撤回", rc.parse_recall('"E" 拍了拍 "群deepseek"') is None)
    ok("普通聊天里出现「撤回」两字不误判", rc.parse_recall("我发错了，已经撤回了") is None)
    ok("普通聊天里出现「撤回了一条消息」但不是整句也不误判",
       rc.parse_recall("他昨天说 撤回了一条消息 之后就没说话了") is None)
    ok("空内容返回 None", rc.parse_recall("") is None and rc.parse_recall(None) is None)

    print("== B. 定位被撤的那条（不猜）==")
    msgs = [
        {"id": 1, "mid": 11, "ts": 1000, "sender_name": "E", "text": "第一条", "self": False},
        {"id": 2, "mid": 12, "ts": 2000, "sender_name": "E", "text": "第二条", "self": False},
        {"id": 3, "mid": 13, "ts": 3000, "sender_name": "我", "text": "我发的", "self": True},
    ]
    t = rc.pick_target(msgs, who="E", before_ts=4000, window_ms=100000)
    ok("按发送者挑窗口内最近一条", t and t["id"] == 2, t and t["id"])
    ok("窗口外不挑（宁可不删）",
       rc.pick_target(msgs, who="E", before_ts=200000, window_ms=180000) is None)
    ok("撤回时间之前的才算（事件之后的更晚消息不挑）",
       rc.pick_target(msgs, who="E", before_ts=1500, window_ms=100000)["id"] == 1)
    ok("自己撤回只挑自己那条", (rc.pick_target(msgs, self_only=True, before_ts=4000) or {}).get("id") == 3)
    ok("发送者都不知道时返回 None（不猜）", rc.pick_target(msgs, before_ts=4000) is None)
    marked = [dict(m, recalled=True) for m in msgs]
    ok("已经是 recalled 的不会再被挑中", rc.pick_target(marked, who="E", before_ts=4000) is None)

    print("== C. 存档门：recent / 未读 / 落盘（一处生效、处处生效）==")
    st = ChatStore()
    st.append_incoming("group:g1", 101, 5000, "wxid_e", "E", "要撤的那句")
    st.append_incoming("group:g1", 102, 6000, "wxid_f", "F", "别人正常说的话")
    ok("未撤回时 recent 看得到", len(st.recent("group:g1", limit=10)) == 2)
    st.mark_recalled("group:g1", 1, {"who": "E", "note": "E 撤回了一条消息", "ts": 7000})
    r = st.recent("group:g1", limit=10)
    ok("打标记后 recent 默认不再返回它", len(r) == 1 and r[0]["text"] == "别人正常说的话")
    ok("include_recalled=True 仍能取到（审计用）",
       any(m.get("recalled") for m in st.recent("group:g1", limit=10, include_recalled=True)))
    ok("find_by_mid 仍找得到（可追溯撤回的是哪条）",
       (st.find_by_mid("group:g1", 101) or {}).get("recalled") is True)
    ok("撤回的那条不再算未读/不再触发回复",
       st.unread_count("group:g1") == 1 and len(st.drain_unread("group:g1")) == 1)
    ok("落盘后重载仍是 recalled（不是只在内存里）",
       (ChatStore().find_by_mid("group:g1", 101) or {}).get("recalled") is True)
    ok("mark_recalled 找不到 id 时返回 None（不谎报成功）",
       st.mark_recalled("group:g1", 99999, {}) is None)
    st2 = ChatStore()
    st2.append_incoming("group:g2", 201, 8000, "wxid_e", "E", "未被撤回的")
    ok("阴性对照：没调 purge 的会话照旧能看到",
       len(st2.recent("group:g2", limit=10)) == 1)

    print("== D. purge 端到端：精确路径 / 兜底 / 审计 ==")
    st3 = ChatStore()
    st3.append_incoming("group:g3", 301, 9000, "wxid_e", "E", "被撤的正文")
    st3.append_incoming("group:g3", 302, 9500, "wxid_e", "E", "更晚的一条")
    res = rc.purge(st3, "group:g3", {"svrid": 301, "who": "E", "ts": 10000, "note": "E 撤回了一条消息"},
                   resolver=lambda sv: sv, log=log)
    ok("newmsgid 路径精确命中（how=svrid）", res["ok"] and res["how"] == "svrid", res)
    ok("删掉的是对的那一条（mid=301）", res["mid"] == 301 and res["text"] == "被撤的正文", res["text"])
    ok("更晚的那条没被牵连", st3.find_by_mid("group:g3", 302).get("recalled") is not True)

    st4 = ChatStore()
    st4.append_incoming("group:g4", 401, 9000, "wxid_e", "E", "兜底要撤的")
    mem = FakeMemory()
    res4 = rc.purge(st4, "group:g4", {"svrid": None, "who": "E", "ts": 9500, "note": "E 撤回了一条消息"},
                    resolver=None, memory=mem, window_ms=180000, log=log)
    ok("拿不到 newmsgid 时按发送者兜底（how=heuristic）", res4["ok"] and res4["how"] == "heuristic", res4)
    ok("兜底命中的也真从上下文里消失", len(st4.recent("group:g4", limit=10)) == 0)
    ok("长期印象里相同内容被清掉（带上 user_id / 原文）",
       any(c["user_id"] == "wxid_e" and c["content"] == "兜底要撤的" for c in mem.calls), mem.calls)
    st5 = ChatStore()
    st5.append_incoming("group:g5", 501, 9000, "wxid_e", "E", "不许误删")
    res5 = rc.purge(st5, "group:g5", {"svrid": None, "who": "E", "ts": 9500}, heuristic=False, log=log)
    ok("关掉兜底 + 没有 newmsgid ⇒ 删 0 条（宁可不删）",
       res5["removed"] == 0 and res5["how"] == "none" and len(st5.recent("group:g5", limit=10)) == 1)
    res5b = rc.purge(st5, "group:g5", {"svrid": 501, "who": "E", "ts": 9500},
                     resolver=lambda sv: None, heuristic=False, log=log)
    ok("resolver 查不到该 svrid ⇒ 也不误删", res5b["removed"] == 0)
    res5c = rc.purge(st5, "group:g5", {"svrid": 501, "who": "E", "ts": 9500},
                     resolver=lambda sv: (_ for _ in ()).throw(RuntimeError("db 炸了")), heuristic=False, log=log)
    ok("resolver 抛异常时 purge 不炸、如实记 0 条", res5c["removed"] == 0 and res5c["how"] == "none")
    ok("purge 永远返回 dict（水位才能推进）", all(isinstance(x, dict) for x in (res, res4, res5, res5b, res5c)))

    ok("审计文件追加了记录（撤了什么可查）",
       os.path.exists(rc.LOG_PATH) and len(open(rc.LOG_PATH, encoding="utf-8").read().strip().splitlines()) >= 3)
    ok("控制台读数：count 与最近一条都在", rc.summary()["count"] >= 2 and rc.summary()["last"]["text"] == "兜底要撤的")
    ok("未命中时也留审计（不静默）",
       any(r.get("removed") == 0 for r in
           [json.loads(l) for l in open(rc.LOG_PATH, encoding="utf-8").read().strip().splitlines()]))

    rc.note_unmatched("系统提示：有人撤回了一条消息（认不出的形态）", {"chat": "group:x"})
    rc.note_unmatched("正常系统消息：E 加入了群聊")
    un = open(rc.UNMATCHED_PATH, encoding="utf-8").read().strip().splitlines() if os.path.exists(rc.UNMATCHED_PATH) else []
    ok("疑似撤回答不出的形态会留证（认得出的不写）", len(un) == 1 and "认不出的形态" in un[0], len(un))

    print("== D2. 撤回核对 sweep：微信「原地改写那一行」的兜底 ==")
    now_ms = int(time.time() * 1000)
    st6 = ChatStore()
    st6.append_incoming("group:g6", 601, now_ms - 60000, "wxid_e", "E", "正常的一句话")
    st6.append_incoming("group:g6", 602, now_ms - 60000, "wxid_f", "F", "后来被撤的那句")
    st6.append_incoming("group:g6", None, now_ms - 60000, "wxid_g", "G", "没有 mid 的老消息")
    st6.append_incoming("group:g6", 604, now_ms - 900000, "wxid_h", "H", "五分钟以前的老消息")
    reads = []

    def _fetch(mid):
        reads.append(mid)
        if mid == 602:
            return XML_PEER
        return "这是一条正常消息"

    state = {}
    sw = rc.sweep(st6, "group:g6", _fetch, state=state, window_ms=300000, interval_ms=15000, log=log)
    ok("原地改写的那条被核对出来并剔除", sw["removed"] == 1 and sw["checked"] >= 2, sw)
    ok("存档里被改写的条目已打 recalled 标记", st6.find_by_mid("group:g6", 602).get("recalled") is True)
    ok("正常的那条没被牵连", st6.find_by_mid("group:g6", 601).get("recalled") is not True)
    ok("没有 mid 的老消息不读库（无从核对）", None not in reads and "None" not in [str(x) for x in reads])
    ok("超过时间窗的老消息不读库", 604 not in reads, reads)
    ok("命中写进了审计（how=rowcheck）",
       any(r.get("how") == "rowcheck" for r in
           [json.loads(l) for l in open(rc.LOG_PATH, encoding="utf-8").read().strip().splitlines()]))
    n1 = len(reads)
    sw2 = rc.sweep(st6, "group:g6", _fetch, state=state, window_ms=300000, interval_ms=15000, log=log)
    ok("限频：间隔内不重复扫", sw2["skipped"] == "interval" and len(reads) == n1, sw2)
    state["last"] = 0
    sw3 = rc.sweep(st6, "group:g6", _fetch, state=state, window_ms=300000, interval_ms=15000, log=log)
    ok("已核对过的不再重复读库（每条只读一次）", len(reads) == n1 and sw3["removed"] == 0, len(reads))
    st7 = ChatStore()
    st7.append_incoming("group:g7", 701, 200000, "wxid_e", "E", "读不到行的那条")
    sw4 = rc.sweep(st7, "group:g7", lambda mid: (_ for _ in ()).throw(RuntimeError("db 炸了")),
                   state={}, window_ms=300000, interval_ms=0, log=log)
    ok("读库抛异常时不炸、也不动存档",
       sw4["removed"] == 0 and len(st7.recent("group:g7", limit=10)) == 1)
    sw5 = rc.sweep(st7, "group:g7", lambda mid: None, state={}, window_ms=300000, interval_ms=0, log=log)
    ok("行已被删（读不到）⇒ 一律不动（宁漏不误删）",
       sw5["removed"] == 0 and len(st7.recent("group:g7", limit=10)) == 1)
    st8 = ChatStore()
    st8.append_incoming("group:g8", 801, now_ms - 60000, "wxid_x", "张三", "群友恰好打出的那句话")
    sw6 = rc.sweep(st8, "group:g8", lambda mid: 'wxid_x:\n张三 撤回了一条消息',
                   state={}, window_ms=300000, interval_ms=0, log=log)
    ok("群友恰好打出「X 撤回了一条消息」不被误判成撤回（只认 revokemsg 报文）",
       sw6["removed"] == 0 and len(st8.recent("group:g8", limit=10)) == 1, sw6)

    print("== E. 接线断言（源码级：不能只写模块不接上）==")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    wx = open(os.path.join(root, "agent", "wechat.py"), encoding="utf-8").read()
    pm = open(os.path.join(root, "scripts", "persona_morph.py"), encoding="utf-8").read()
    cfg = open(os.path.join(root, "agent", "config.py"), encoding="utf-8").read()
    html = open(os.path.join(root, "agent", "console_html.py"), encoding="utf-8").read()
    ok("normalize 里认撤回", "recall_mod.parse_recall" in wx)
    ok("认撤回在「自己发的跳过」之前（自己撤回才不会被丢）",
       wx.find("recall_mod.parse_recall") < wx.find('if str(sender_id) in ("2", "3")'))
    ok("认不出的疑似撤回留证", "recall_mod.note_unmatched" in wx)
    ok("纯文本撤回只在系统消息类型里认（防群友打出这句话被误判）",
       'form") == "xml" or mtype == "系统消息"' in wx)
    ok("有 server_id→local_id 的收口方法", "def local_id_by_server_id" in wx)
    ok("有只读的「读某条当前内容」方法（核对原地改写用）", "def message_content" in wx)
    ok("监听循环里做撤回核对（原地改写兜底）", "recall.sweep(" in pm)
    ok("监听循环里调 purge", "recall.purge(" in pm)
    ok("撤回事件不触发回复（直接 return，不到 on_incoming）",
       "recall.purge(" in pm and pm.find("recall.purge(") < pm.find("orch.on_incoming(_chat_key)"))
    ok("配置默认段有 store.recall.enabled", '"enabled": True' in cfg and '"recall"' in cfg)
    ok("控制台有开关行与读数行", 'data-cfg="store.recall.enabled"' in html and 'id="recallStat"' in html)
    ok("控制台读数来自 /api/status 的 recall 段", "s.recall" in html)

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
