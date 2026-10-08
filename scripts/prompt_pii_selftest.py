# -*- coding: utf-8 -*-
"""**模型输入不许出现真实账号（wxid）**—— 红线判据。

口径（**两条一起成立才算对**）：
  · **判"是不是同一个人"只看 wxid** —— 昵称重名不影响身份判断；
  · **但喂给模型的输入里只许出现昵称，不许出现 wxid**。
    ⇒ 前者要靠 uid，后者又不能让 uid 溜出去 ⇒ 显示名一律走 `memory._safe_name()` 转一道。

为什么会有这条：判人要靠 uid，而 uid 一旦顺着"显示名回落"溜进提示词，
就等于把**真实账号**发给了外部模型（而且要发很多次）。历史上确实有几处这么漏的：
  · `format_for_prompt` 的 `who` 回落链末尾是 `str(m["userId"])`；
  · `_format_entry` 的 `who` 回落链末尾是 `sender_id`；
  · 「上次聊过」那行的 `sender_name or sender_id`；
  · **成员备注段**把 `（wxid xxx）` **逐个写给模型**（为了让模型能调工具 —— 而工具现在已经收昵称了）。

守八条（都配反向锚）：
  P1~P4 行为：`format_for_prompt` / `seeds_for_prompt` 的输出里**没有** wxid 形状串；
        但**昵称该出现还得出现**、**该有的那一行不许整个删掉**（否则模型会以为没这个人）。
  P5~P7 静态：源码里**没有**回落 uid 的写法、没有 `（wxid …）` 模板、字符串常量里没有 wxid 形状。
  P8 接线：`at_user_id` 的工具说明是**群昵称**（工具侧已能按昵称解析）。

用法：runtime\\python\\python.exe scripts/prompt_pii_selftest.py
"""
import ast
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import _srcmatch as _sm  # noqa: E402
import _iso14  # noqa: E402
_iso14.all_()

from agent import memory as M  # noqa: E402

PASS = FAIL = 0
#: 账号形状（与 `fullcheck` 的「真实 wxid」同一条：合成的也要避开，所以样本用 `wxid_sample…`）
_SHAPE = re.compile(r"wxid_[A-Za-z0-9]{5,}", re.I)
#: 这里用的样本：**故意用 `wxid_sample…` 前缀**（项目里公认的合成前缀），
#  但它对"会不会被写进提示词"这件事**一视同仁** —— 判据只看形状，不看是不是真的。
UID = "wxid_samplefake0001"


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


CK = "pii-%d" % os.getpid()
_s = M.MemoryStore()

print("── P1~P4 行为：渲染出的文本里不许有账号 ──")
# ① 一个**只有 uid、没有昵称**的成员（最容易漏的那种）
_s._append_raw(CK, UID, "", "他最近在备考，情绪一般", created_at=1_700_000_000_000)
_txt = _s.format_for_prompt(CK)
ok("P1★ 成员档**只有 uid 没有昵称**时，印象段里**不出现 wxid**"
   "（旧写法会回落成 uid ⇒ 直接把真实账号发出去）",
   not _SHAPE.search(_txt or ""), (re.findall(_SHAPE, _txt or "") or _txt[:50]))
ok("P2★ 但那一行**还在**（不能靠「整行删掉」来过判据 —— 模型会以为没这个人）",
   "备考" in (_txt or ""), (_txt or "")[:60].replace("\n", "⏎"))
_s._append_raw(CK, "uid_named", "阿明", "喜欢喝美式", created_at=1_700_000_000_000)
_txt2 = _s.format_for_prompt(CK)
ok("P3★ 有昵称时**必须出现昵称**（脱敏不许把有用的信息一起抹掉）",
   "阿明" in (_txt2 or ""), (_txt2 or "")[:60].replace("\n", "⏎"))
M.append_followup(CK, "问问他考试过了没", who=UID)
M.add_event(CK, "他上周拿到 offer 了", who=UID, emotion="good")
_seed = M.seeds_for_prompt(CK)
ok("P4★ 待跟进/事件里 `who` 传的是 uid ⇒ 渲染出来**也不出现 wxid**",
   not _SHAPE.search(_seed or ""), (re.findall(_SHAPE, _seed or "") or _seed[:50]))
ok("P4b 那条待跟进本身**还在**（该提的事不能被脱敏吃掉）",
   "考试" in (_seed or ""), (_seed or "")[:60].replace("\n", "⏎"))

print("\n── P5~P7 静态：源码里不许有 uid 回落 / wxid 模板 ──")
_src = io.open(os.path.join(ROOT, "agent", "prompt.py"), encoding="utf-8").read()
ok("P5★ `prompt.py` 里**没有** `... or sender_id or ...` 这种回落（回落就是把 uid 发出去）",
   not _sm.has(_src, "or sender_id or"), "")
ok("P6★ `prompt.py` 里**没有** `（wxid %s）` 这种模板（成员备注段曾逐个写给模型）",
   not _sm.has(_src, "（wxid %s）") and not _sm.has(_src, "(wxid %s)"), "")
# P7：扫**字符串常量**里有没有 wxid 形状（注释里提"wxid"这个词没关系，账号形状才要命）
_bad_lit = []
_tree = ast.parse(_src)
for _n in ast.walk(_tree):
    if isinstance(_n, ast.Constant) and isinstance(_n.value, str):
        if _SHAPE.search(_n.value):
            _bad_lit.append(_n.value[:60])
ok("P7★ `prompt.py` 的**字符串常量**里没有 wxid 形状的串（注释里提这个词不算）",
   not _bad_lit, str(_bad_lit[:2]))
_tools = io.open(os.path.join(ROOT, "agent", "tools.py"), encoding="utf-8").read()
ok("P8★ `at_user_id` 的工具说明已改成**群昵称**（工具侧能按昵称解析 ⇒ 模型不需要 uid）",
   _sm.has(_tools, "要 @ 的群成员**群昵称**")
   and not _sm.has(_tools, "要 @ 的群成员 wxid"), "")
ok("P8b 反向锚：`tools.py` 里那句会**告诉模型怎么填**的说明不许再出现「填 wxid」",
   not _sm.has(_tools, "要 @ 人就填 wxid"), "")
_mem = io.open(os.path.join(ROOT, "agent", "memory.py"), encoding="utf-8").read()
ok("P9★ `memory.py` 里也有**统一的脱敏入口** `_safe_name()`（唯一实现点，别各写各的）",
   _sm.has(_mem, "def _safe_name("), "")
ok("P9b 它真的挡得住账号形状、又放得过昵称（反向控制）",
   M._safe_name(UID) == "某位群友" and M._safe_name("阿明") == "阿明"
   and M._safe_name("") == "某位群友", "%s / %s" % (M._safe_name(UID), M._safe_name("阿明")))

print("\n== 模型输入红线判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
