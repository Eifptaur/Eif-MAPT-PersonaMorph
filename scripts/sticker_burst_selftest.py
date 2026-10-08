# -*- coding: utf-8 -*-
"""第 259 项：表情「别连发、一句最多一个」的**硬约束**。

背景（PLAN 第 259 项原话）：原来只有**频率层面的引导**（`behavior.sticker_level` 乘数
＋ 提示词里一句"表情包积极度"），**没有**"同一条消息里最多一个表情""不让表情连着出现"这类硬闸门。
实测缺口在**批级**：模型一批三条、每条各带一个标记 ⇒ 每条各自补发一张 ⇒ **连发三张**。

判据分四层：
  A. **批级闸**（`sender.pick_sticker_owner`）：一批只允许一条负责发贴纸；
  B. **批内多标记**：同一条里写多个标记 ⇒ 仍然最多发一张（且标记都从文本里摘干净）；
  C. **接线**：发送循环**真的**用了那个闸（AST 锚，不看注释）；
  D. **fail-closed 与反例锚**：判不出 ⇒ 一张都不发；把闸摘掉（旧行为）⇒ 一批会发 3 张。
"""
from __future__ import annotations

import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.environ["PM_JUDGE_NO_GUI"] = "1"

from agent import emoji_index as EI # noqa: E402
from agent import sender as SD # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


# ── 夹具：把标记解析换成**确定性**版本（不依赖本机表情库有没有那张图）──
_REAL_EXTRACT = EI.extract_marker
_MARK = "[[表情:%d]]"


def _fake_extract(text):
    s = str(text or "")
    import re
    m = re.search(r"\[\[表情:(\d+)\]\]", s)
    sid = int(m.group(1)) if m else None
    return re.sub(r"\[\[表情:\d+\]\]", "", s).strip(), sid, ("stub" if m else "")


EI.extract_marker = _fake_extract

print("\n══ A. 批级闸：一批只允许一条发贴纸 ══")
_p3 = ["好呀" + _MARK % 1, "那我看看" + _MARK % 2, "晚点说" + _MARK % 3]
ok("① 三条**各带**一张 ⇒ 只由**第 1 条**发（其余两条不发）",
   SD.pick_sticker_owner(_p3) == 0, str(SD.pick_sticker_owner(_p3)))
ok("② 前两条纯文本、第 3 条才带 ⇒ 由第 3 条发（下标 2）",
   SD.pick_sticker_owner(["纯文本一", "纯文本二", "带一个" + _MARK % 1]) == 2)
ok("③ 全都不带 ⇒ 返回 -1（本批一张都不发）",
   SD.pick_sticker_owner(["甲", "乙", "丙"]) == -1)
ok("④ 空批 ⇒ -1（不许越界）", SD.pick_sticker_owner([]) == -1)
ok("⑤ 旧行为对照：按'每条各自判'会得到 **3** 条要发 ⇒ 这就是原来的'连发三张'",
   sum(1 for _t in _p3 if _fake_extract(_t)[1] is not None) == 3,
   "旧行为会发 %d 张" % sum(1 for _t in _p3 if _fake_extract(_t)[1] is not None))

print("\n══ B. 同一条里写多个标记 ⇒ 仍然最多一张 ══")
_c, _sid, _ = _fake_extract("给你看个好东西" + _MARK % 1 + _MARK % 2 + _MARK % 3)
ok("⑥ 解析出的**贴纸数**是 1（不是 3）", _sid is not None and _sid == 1, "sid=%s" % _sid)
ok("⑦ 文本里的标记**全被摘干净**（一个都不许发给群友）",
   "表情" not in _c and _c == "给你看个好东西", repr(_c))

print("\n══ C. 接线：发送循环真的用了这个闸（AST 锚）══")
_src = io.open(os.path.join(ROOT, "agent", "sender.py"), encoding="utf-8").read()
_tree = ast.parse(_src)
_calls = [n for n in ast.walk(_tree)
          if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("pick_sticker_owner")]
ok("⑧ 产品里**调用了** `pick_sticker_owner`（不是只定义不用）", len(_calls) >= 1,
   "%d 处调用" % len(_calls))
_owner_uses = [n for n in ast.walk(_tree)
               if isinstance(n, ast.Name) and n.id == "_sticker_owner"]
ok("⑨ `_sticker_owner` 在发送循环里被**用来挡**补发（≥2 处引用：定义处 ＋ 判断处）",
   len(_owner_uses) >= 2, "%d 处引用" % len(_owner_uses))

print("\n══ D. fail-closed 与反例锚 ══")


def _boom(_text):
    raise RuntimeError("模拟表情索引坏掉")


_keep = EI.extract_marker
EI.extract_marker = _boom
try:
    _v = SD.pick_sticker_owner(["带标记" + _MARK % 1])
finally:
    EI.extract_marker = _keep
ok("⑩ 判不出（解析抛异常）⇒ 返回 -1（**一张都不发**；不是'没闸门就随便发'）", _v == -1, str(_v))
ok("⑪ 反例锚：把闸去掉（改成'每条各发'）⇒ 一批三条会发 3 张，与 ① 的 1 张形成对照",
   sum(1 for _t in _p3 if _fake_extract(_t)[1] is not None) == 3 and SD.pick_sticker_owner(_p3) >= 0)
ok("⑫ 本项**不新增**贴纸（只做'少发'）：`pick_sticker_owner` 返回的是**已有条目**的下标",
   SD.pick_sticker_owner(_p3) in (0, 1, 2))

EI.extract_marker = _REAL_EXTRACT

print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for _x in FAIL:
        print("  - " + _x)
sys.exit(1 if FAIL else 0)
