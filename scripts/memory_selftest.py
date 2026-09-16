# -*- coding: utf-8 -*-
"""记忆删除判据（离线，不需要微信、不需要真数据）。

起因（用户反馈 2026-09-16）：「**记忆那里也是删除了还能读取**」——
根因是**口径不对称**：`MemoryStore.members()`（列表 / 读取）在"互通"时是
`for key in self._chat_keys(chat_key)` **把所有相关群合并**后展示的，而 `remove()` 原来
只删 `chat_key` **一个群**的那一份 ⇒ 同一个人在别的群（共享池）还留着一份
⇒ 界面上删了、一刷新又合并出来。

本判据钉住「**删除与读取必须同一口径**」，并核对删除的收尾动作：
  ① `members` 用 `_chat_keys` 展开（读取口径）
  ② `remove` 也用 `_chat_keys` 展开（不许只删一个群）
  ③ `remove` 只处理 `memberImpression`（别的 category 直接 False，不误删）
  ④ 删空之后**删文件**（不留空档案）；还有剩则**写回**
  ⑤ UI 文案要说清"删的是那个人的**全部**印象"（别让人以为只删本群那份）

为什么用源码级而不是行为级：行为级要在真实 `data/` 目录上造多群档案，会污染产品数据；
这条 bug 的本质是"两处口径不一致"，源码级断言正好直击，且不碰任何数据。
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   %s%s" % (name, ("  [" + str(detail) + "]") if detail else ""))
    else:
        FAIL += 1
        print("  FAIL %s%s" % (name, ("  [" + str(detail) + "]") if detail else ""))


print("记忆删除判据")
print("")

mem_src = io.open(os.path.join(ROOT, "agent", "memory.py"), encoding="utf-8").read()
page = io.open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()

seg_members = mem_src.split("def members(")[1].split("def remove(")[0]
seg_remove = mem_src.split("def remove(")[1].split("def replace_member(")[0]

print("── A. 读取与删除必须同一口径 ──")
ok("members 用 _chat_keys 展开（互通时合并所有群）", "_chat_keys(chat_key)" in seg_members)
ok("remove 也用 _chat_keys 展开（2026-09-16 修复点）", "_chat_keys(chat_key)" in seg_remove)
ok("remove 不再只对单一 chat_key 取档案（那正是「删了还能读到」的根因）",
   "_ensure_chat(chat_key)" not in seg_remove)
ok("remove 里按 _chat_keys 逐个群处理", "for key in list(self._chat_keys(chat_key))" in seg_remove)

print("\n── B. 删除的边界与收尾 ──")
ok("只处理 memberImpression（别的 category 直接 False，不误删）",
   'if category != "memberImpression":' in seg_remove)
ok("支持按 user_id 命中", 'str(mem.get("userId")) == str(user_id)' in seg_remove)
ok("支持按 target（名字）命中", 'str(mem.get("name") or mem.get("userId"))' in seg_remove)
ok("删空之后删文件（不留空档案）", "os.remove(_member_file(" in seg_remove)
ok("还有剩则写回", "_write_json(_member_file(" in seg_remove)

print("\n── C. 用户看得懂（别让人以为只删本群那份）──")
ok("UI 文案点明「全部印象」", "的全部印象" in page)
ok("删除按钮在记忆面板里", "loadMemory(sel.value)" in page)

print("")
print("记忆删除判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
