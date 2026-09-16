# -*- coding: utf-8 -*-
"""第 10 条判据：按会话 / 按条屏蔽存档消息（不需要微信、不联网、不花 token）。

跑法： py -3 scripts\\archive_block_selftest.py      退出码 0=全过 / 1=有失败
A 按会话屏蔽（名单解析/命中判定/两种写法）· B 按条屏蔽（与撤回共用同一道门、可解除、不删）·
C 按条清除（**必须点名 id**，空 ids 一律拒绝 = 不做"顺手清空"）· D 接线与文案 + 阴性对照。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import agent.config as cfgmod                      # noqa: E402
from agent import archive_filter as af             # noqa: E402
from agent import store as store_mod               # noqa: E402
from agent.store import ChatStore                  # noqa: E402

PASS, FAIL = [], []
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def main():
    tmp = tempfile.mkdtemp(prefix="arc-judge-")
    store_mod.MESSAGES_DIR = os.path.join(tmp, "messages")
    real_get = cfgmod.get_config

    def use(block_list):
        cfgmod.get_config = lambda: {"store": {"archive_block_chats": block_list}}

    try:
        print("== A. 按会话屏蔽（名单与命中判定）==")
        use([])
        ok("名单为空 ⇒ 谁都不算命中", not af.is_blocked(chat_key="group:wxid_a", group_name="某群"))
        use(["某广告群", "group:wxid_b"])
        ok("按群名命中", af.is_blocked(chat_key="group:wxid_c", group_name="某广告群"))
        ok("按 chat_key 命中", af.is_blocked(chat_key="group:wxid_b", group_name="别的群"))
        ok("都不在名单 ⇒ 不命中", not af.is_blocked(chat_key="group:wxid_z", group_name="正常群"))
        ok("忽略大小写与空格", af.is_blocked(chat_key=" GROUP:WXID_B ", group_name=""))
        use("某广告群，group:wxid_b\n又一个群")
        ok("config 里写成字符串（逗号/换行/中文逗号）也能解析",
           af.blocked_list() == ["某广告群", "group:wxid_b", "又一个群"], af.blocked_list())
        ok("字符串形态照样能命中", af.is_blocked(group_name="又一个群"))

        print("== B. 按条屏蔽（与撤回同一道门：留着但不再进上下文）==")
        st = ChatStore()
        st.append_incoming("group:g1", 101, 1000, "wxid_x", "小明", "这句要屏蔽")
        st.append_incoming("group:g1", 102, 2000, "wxid_y", "小红", "这句正常")
        ok("未屏蔽前 recent 看得到两条", len(st.recent("group:g1", limit=10)) == 2)
        r = af.block(st, "group:g1", [1], reason="面板操作")
        ok("屏蔽成功且报告改动条数", r["ok"] and r["changed"] == 1, r)
        left = st.recent("group:g1", limit=10)
        ok("屏蔽后默认 recent 不再返回它", len(left) == 1 and left[0]["text"] == "这句正常")
        ok("条目仍然留在存档里（可追溯）",
           len(st.list_entries("group:g1", limit=10)) == 2 and st.find_by_id("group:g1", 1) is not None)
        ok("带上 blocked 标记与原因",
           (st.find_by_id("group:g1", 1) or {}).get("blocked") is True
           and "面板操作" in str((st.find_by_id("group:g1", 1) or {}).get("block_reason")))
        ok("不再算未读（不会触发回复）", st.unread_count("group:g1") == 1)
        ok("include_blocked=True 仍能取到（面板要看得见）",
           any(m.get("blocked") for m in st.recent("group:g1", limit=10, include_blocked=True)))
        ok("落盘后重载仍是 blocked", (ChatStore().find_by_id("group:g1", 1) or {}).get("blocked") is True)
        r2 = af.unblock(st, "group:g1", [1])
        ok("可解除，解除后重新进上下文",
           r2["changed"] == 1 and len(st.recent("group:g1", limit=10)) == 2)
        ok("未点名 id ⇒ 改动 0 条（不瞎动）", af.block(st, "group:g1", [])["changed"] == 0)

        print("== C. 按条清除（必须点名，绝不做顺手清空）==")
        bad = af.delete(st, "group:g1", [])
        ok("空 ids 一律拒绝（本功能不做「清空」）", bad["ok"] is False and bad["changed"] == 0, bad)
        ok("拒绝后一条都没少", len(st.list_entries("group:g1", limit=10)) == 2)
        d = af.delete(st, "group:g1", [2])
        ok("点名删除生效", d["ok"] and d["changed"] == 1 and len(st.list_entries("group:g1", limit=10)) == 1)
        ok("删的是点名那条", st.find_by_id("group:g1", 2) is None and st.find_by_id("group:g1", 1) is not None)
        ok("删不存在的 id ⇒ 改动 0（不谎报）", af.delete(st, "group:g1", [999])["changed"] == 0)

        print("== D. 面板数据与接线 ==")
        use(["某广告群"])
        snap = af.snapshot(st)
        ok("快照报名单与已屏蔽条数", snap["chats"] == ["某广告群"] and "blocked_entries" in snap)
        st.append_incoming("group:g2", 201, 3000, "wxid_z", "路人", "要屏蔽的一条")
        st.block_entries("group:g2", [1])
        ok("快照能数出存档里被屏蔽的条目", af.snapshot(st)["blocked_entries"] == 1)
        lst = af.list_chat(st, "group:g1", limit=5)
        ok("面板列表带 recalled/blocked 标记与文本", lst["ok"] and lst["items"]
           and {"id", "text", "blocked", "recalled"} <= set(lst["items"][0].keys()))
        use(["某广告群"])
        opts = af.chat_options(st)
        ok("会话下拉：存档里的会话 + 只在名单里的会话都列出来",
           any(o["chat_key"] == "group:g1" for o in opts)
           and any(o.get("only_in_blocklist") for o in opts), [o["chat_key"] for o in opts])

        wui = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
        html = open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
        pm = open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
        cfg_py = open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read()
        cex = open(os.path.join(ROOT, "config.example.json"), encoding="utf-8").read()
        ok("监听循环里按会话屏蔽（不落库、不触发）",
           'archive_filter.is_blocked(chat_key=_chat_key, group_name=_g["name"])' in pm
           and '{"dropped": "archive-block"}' in pm)
        ok("WebUI 拿到 store 句柄（否则面板没法动存档）",
           "store=None" in wui and "self.store = store" in wui and "store=orch.store" in pm)
        ok("四条接口都在（列/屏蔽/解除/清除）",
           '"/api/archive"' in wui and '"/api/archive/block"' in wui
           and '"/api/archive/unblock"' in wui and '"/api/archive/delete"' in wui)
        ok("/api/status 暴露 archive 段", 'st["archive"] = _af2.snapshot' in wui)
        ok("控制台有会话名单输入框 + 按条操作的四个控件",
           'data-cfg="store.archive_block_chats"' in html and 'id="arcLoad"' in html
           and 'id="arcList"' in html and 'id="arcReload"' in html and 'id="arcChat"' in html)
        ok("清除走二次确认（不可恢复的操作不能一点就删）", "uiConfirm('真删第 #" in html)
        ok("中文文案写明「屏蔽＝留着但不再进上下文，清除＝真删」与「必须点名条目」",
           "留着但不再进上下文/记忆" in html and "必须点名条目" in html)
        ok("配置默认段 + 示例同步",
           '"archive_block_chats": []' in cfg_py and '"archive_block_chats"' in cex)

        print("== E. 阴性对照 ==")
        use([])
        st2 = ChatStore()
        st2.append_incoming("group:g9", 901, 9000, "wxid_q", "小刚", "名单为空时照旧存档")
        ok("阴性对照：名单为空 ⇒ 会话照旧（不被屏蔽）",
           not af.is_blocked(chat_key="group:g9", group_name="任意群")
           and len(st2.recent("group:g9", limit=5)) == 1)
        ok("阴性对照：解除屏蔽后 recent 条数真的回来了（不是恒 0）",
           (af.block(st2, "group:g9", [1]), len(st2.recent("group:g9", limit=5)) == 0,
            af.unblock(st2, "group:g9", [1]), len(st2.recent("group:g9", limit=5)) == 1)[3] == 1)
    finally:
        cfgmod.get_config = real_get

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
