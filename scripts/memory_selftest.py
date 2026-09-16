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
seg_elsewhere = mem_src.split("def elsewhere(")[1].split("\n    def ")[0] if "def elsewhere(" in mem_src else ""
seg_remove = mem_src.split("def remove(")[1].split("def elsewhere(")[0] \
    if "def elsewhere(" in mem_src else mem_src.split("def remove(")[1].split("def replace_member(")[0]

print("── A. 读取与删除必须同一口径 ──")
ok("members 用 _chat_keys 展开（互通时合并所有群）", "_chat_keys(chat_key)" in seg_members)
ok("remove 也用 _chat_keys 展开（2026-09-16 修复点）", "_chat_keys(chat_key)" in seg_remove)
ok("remove 不再只对单一 chat_key 取档案（那正是「删了还能读到」的根因）",
   "_ensure_chat(chat_key)" not in seg_remove)
ok("remove 的默认档＝对所有相关群逐个处理（与读取口径一致）",
   'keys = [chat_key] if str(scope) == "this" else list(self._chat_keys(chat_key))' in seg_remove
   and "for key in keys:" in seg_remove)

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

print("\n── D. 删除范围做成可选档位（用户口径：不替他二选一，映射到 UI 上）──")
pm = io.open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
wu = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
ok("remove 有 scope 参数且默认 all（默认＝与读取口径一致的安全侧）",
   'scope="all"' in mem_src)
ok("「只删本群」档真的只删本群（[chat_key] 分支）", "[chat_key] if str(scope)" in seg_remove)
ok("elsewhere() 存在（给「只删本群」做如实提示）", "def elsewhere(" in mem_src)
ok("elsewhere() 是只读的（不写文件、不删文件）",
   "_write_json(" not in seg_elsewhere and "os.remove(" not in seg_elsewhere)
ok("「只删本群」时如实回报还剩几个群", "还留着" in pm and "left_elsewhere" in pm)
ok("memory_fn 接受 scope", 'scope="all"' in pm)
ok("webui 透传 scope", 'scope=str(data.get("scope")' in wu)
ok("控制台有「删除范围」两档且 POST 里带 scope",
   'id="memScope"' in page and "scope:_sc" in page)
ok("UI 说清合并视图的后果（列表里仍会看到）", "列表里仍会看到它" in page)

print("")
print("记忆删除判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
