# -*- coding: utf-8 -*-
"""会话键 → 展示名 的解析判据：`group:<wxid>` 形态必须解析出**群名**（不是把 key 原样当名字）。

为什么要有它：产品里到处用 `"group:" + wxid` 当 chat_key（`persona_morph` 的会话键就这么构造），
而群表 `_group_by_wxid` 与昵称表 `_nick_map` 是**按裸 id 索引**的 ⇒ 不剥前缀就查不到，
`group_name()` 会**原样把 key 返回**。后果不是"显示难看"，而是**功能坏掉**：
`switch_chat_posted` / `open_chat_by_search` 拿这个"名字"去搜索会话、并拿它做 OCR 名字比对
⇒ 永远匹配不上 ⇒ 群消息在"还没学过会话头参照"时**无法自助切会话**（真发实测在日志里复现过：
搜索用的是 `group:58471307405@chatroom`）。

本判据用**桩对象**直接调真实方法：不连微信、不读数据、零副作用。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [0], [0]


def ok(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("  [%s]" % detail if detail else ""))
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
    return bool(cond)


def main():
    from agent.wechat import WeChatAdapter as W

    WXID = "58471307405@chatroom"
    NAME = "aaa偷啃使用者"

    class Stub(W):
        """轻量桩：**继承真类**只填两张表 ⇒ 所有方法（含 `_bare_key`）都自然可用，
        但不走真构造（不连微信、不读库、零副作用）。"""

        def __init__(self):
            self._group_by_wxid = {WXID: {"name": NAME, "wxid": WXID}}
            self._nick_map = {"friendA": "阿甲"}

    stub = Stub()
    gn = stub.group_name
    dn = stub.display_name

    print("== A. 带 `group:` 前缀的 chat_key（产品的构造形态）==")
    ok("`group:<wxid>` 解析出群名（★本轮修的那条）", gn("group:" + WXID) == NAME, gn("group:" + WXID))
    ok("`group:<wxid>` 在 display_name 里也解析出群名", dn("group:" + WXID) == NAME, dn("group:" + WXID))

    print("== B. 不回归：裸 id 与其它形态照旧 ==")
    ok("裸 wxid 仍解析出群名", gn(WXID) == NAME, gn(WXID))
    ok("联系人的昵称仍解析得出", dn("friendA") == "阿甲", dn("friendA"))
    ok("`filehelper` 仍是「文件传输助手」", dn("filehelper") == "文件传输助手", dn("filehelper"))

    print("== C. 不误伤：查不到的仍原样返回（不能凭空造名字）==")
    ok("未知群 key 原样返回（不剥成空串、不编名字）",
       gn("group:999@chatroom") == "group:999@chatroom", gn("group:999@chatroom"))
    ok("未知裸 id 原样返回", gn("999@chatroom") == "999@chatroom", gn("999@chatroom"))
    ok("前缀后面是空的（只有一个 `group:`）不崩、原样返回",
       gn("group:") == "group:", gn("group:"))

    print("== D. 反向控制：剥前缀不能剥掉真名字 ==")
    ok("_bare_key 只剥**已登记前缀**，不碰普通 id",
       stub._bare_key("abc@chatroom") == "abc@chatroom"
       and stub._bare_key("group:abc@chatroom") == "abc@chatroom", "")

    print("== E. 「发错会话」当场自检：目标会话自己必须被排除（同一类键形态问题）==")
    import time as _t
    TEXT2 = "【链路自检】消息"

    class _Db:
        """假库：按裸 wxid 给几行。"""

        def get_messages(self, cid, limit=4):
            return [{"content": TEXT2, "create_time": int(_t.time())}] if cid == WXID else []

    s2 = Stub()
    s2._groups = [{"wxid": WXID, "name": NAME}]   # 真类在 __init__ 里就会填它，桩必须照做
    s2._privates = []
    s2._db = _Db()
    #  归一化后必须是**同一形态**，否则自己发的正确那条会被误报成"发错会话"
    ok("目标是该群本身 ⇒ 自己发的这条**不算**『发错会话』",
       s2._text_landed_in_other_chat(TEXT2, "group:" + WXID) is None,
       str(s2._text_landed_in_other_chat(TEXT2, "group:" + WXID)))
    OTHER = "12345678901@chatroom"
    got = s2._text_landed_in_other_chat(TEXT2, "group:" + OTHER)
    ok("反向控制：目标是**别的**群 ⇒ 仍必须报出『文字落到了 X 里』（兜底不能失效）",
       bool(got) and got[0] == WXID, str(got))

    print("== 会话键→展示名判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
    return 0 if FAIL[0] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
