# -*- coding: utf-8 -*-
"""「角色代理（W4）：@某个角色名 ⇒ 只有那个人设回话」判据 —— 不碰微信、不出网。守第 110 项。

三条纪律各配机械判据：
  · ① **@ 不到 ⇒ 回落默认**（不是"不回话"）：判不出来时行为必须**与加这个功能之前完全一致**；
  · ② **多个人设共享同一套记忆**：`role_agent` **不碰记忆层**（结构性成立，不靠"记得别分家"）；
  · ③ **名字判定必须严**：`@小明` **不许**命中「小明同学」这个槽位名（全等，不是子串）
    —— 这条照抄 `prompt.is_at_me` 当年踩过的坑（子串判会把"@机器人昵称小助手"判成"@我"）。

另有一层**接线**判据：`prompt.role_text_of(cfg, text=…)` 必须**真的问过** `role_agent`，
且 `build_system_prompt(text=…)` 必须把它传下去（否则能力写了没人用＝假开关）。

用法：runtime\\python\\python.exe scripts/role_agent_selftest.py
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


import _srcslice as _ss  # noqa: E402

RA_SRC = io.open(os.path.join(ROOT, "agent", "role_agent.py"), encoding="utf-8").read()
PROMPT_SRC = io.open(os.path.join(ROOT, "agent", "prompt.py"), encoding="utf-8").read()
from agent import role_agent as RA  # noqa: E402
from agent import prompt as P  # noqa: E402

CFG = {"persona": {"active": "小鲸鱼", "role_text": "全局默认正文", "slots": {
    "林黛玉|黛玉": {"role_text": "黛玉：我原是个爱哭的。"},
    "小鲸鱼": {"role_text": "鲸鱼：嘿嘿，接你一句。"},
}}}
PERSONA = CFG["persona"]

print("── A. 模块形态（纯本地 · 零第三方依赖 · 不碰记忆/网络）──")
ok("A1 分母守卫：拿到 `agent/role_agent.py` 正文（>2000 字符）", len(RA_SRC) > 2000, "%d 字符" % len(RA_SRC))
# ⛔ 只扫**真实的 import 语句**（模块头注释里会**提到** `ChatStore`/`memory` 来**说明**不碰它们，
#    扫全文会把说明文字当成罪证 ⇒ 那是"锚文案不锚意图"）。
_IMPORTS = [ln.strip() for ln in RA_SRC.splitlines()
            if ln.strip().startswith(("import ", "from ")) and " import " in ln or
            ln.strip().startswith("from ")]
_IMPORTS = [ln for ln in _IMPORTS if ln.startswith(("import ", "from "))]
ok("A2 ⛔ 零第三方依赖（只 import 标准库 logging，其余在函数内延迟导入）",
   not any(k in " ".join(_IMPORTS) for k in ("requests", "socket", "win32", "numpy", "pandas")),
   str(_IMPORTS))
ok("A3 ⛔ **不碰记忆层**（**import 行**里不出现 memory / ChatStore / store）—— 纪律② 靠它结构性成立",
   not any(("memory" in ln) or ("ChatStore" in ln) or ("store" in ln) for ln in _IMPORTS),
   str(_IMPORTS))
ok("A4 ⛔ 不出网（import 行里不出现 urllib/requests/socket）",
   not any(k in " ".join(_IMPORTS) for k in ("urllib", "requests", "socket")), "")
# 反向锚：把一条"真 import"塞进去，A3 的判据必须能抓到（证明上面不是恒真）
ok("A5 ★反向锚：塞一行真 `from . import memory` ⇒ A3 的判据**确实**会红",
   any(("memory" in ln) for ln in (_IMPORTS + ["from . import memory"])), "")

print("── B. 纪律③：名字判定必须严（全等，不是子串）──")
ok("B1 分母守卫：`slot_names` 拿得到全部可 @ 名字（含别名）",
   set(RA.slot_names(CFG)) == {"林黛玉", "黛玉", "小鲸鱼"}, str(RA.slot_names(CFG)))
ok("B2 ⭐ @槽位**主名** ⇒ 命中", RA.pick_slot("@林黛玉 你怎么看", CFG) == "林黛玉|黛玉",
   repr(RA.pick_slot("@林黛玉 你怎么看", CFG)))
ok("B3 ⭐ @槽位的**别名** ⇒ 命中同一个槽位（`键|别名` 写法）",
   RA.pick_slot("@黛玉 你怎么看", CFG) == "林黛玉|黛玉", repr(RA.pick_slot("@黛玉 你怎么看", CFG)))
ok("B4 ⛔ 反向锚：`@小明` **不许**命中（有槽位名是「小明同学」时）—— 全等而非子串",
   RA.pick_slot("@小明 你怎么看", CFG) == "", repr(RA.pick_slot("@小明 你怎么看", CFG)))
ok("B5 ⛔ `@小鲸鱼同学` **不许**命中「小鲸鱼」（@ 后面还有字就不是它）",
   RA.pick_slot("@小鲸鱼同学 在吗", CFG) == "", repr(RA.pick_slot("@小鲸鱼同学 在吗", CFG)))
ok("B6 ⭐ 「@小鲸鱼 在吗」**要**命中（@ 后面是空白 ⇒ 到此为止）",
   RA.pick_slot("@小鲸鱼 在吗", CFG) == "小鲸鱼", repr(RA.pick_slot("@小鲸鱼 在吗", CFG)))
ok("B7 ⛔ 没有 @ ⇒ 空串（不许把普通消息判成 @）",
   RA.pick_slot("小鲸鱼在吗", CFG) == "", repr(RA.pick_slot("小鲸鱼在吗", CFG)))
ok("B8 多处 @ ⇒ 命中**先出现的那个**槽位",
   RA.pick_slot("@黛玉 @小鲸鱼 都在吗", CFG) == "林黛玉|黛玉",
   repr(RA.pick_slot("@黛玉 @小鲸鱼 都在吗", CFG)))
ok("B9 零槽位配置 ⇒ 一律空串（不许抛）", RA.pick_slot("@林黛玉", {"persona": {}}) == "", "")
ok("B10 ⛔ 畸形配置（slots 是列表 / 是 None / 根本不是 dict）⇒ 一律空串，**绝不抛**",
   all(RA.pick_slot("@林黛玉", c) == "" for c in
       ({"persona": {"slots": []}}, {"persona": {"slots": None}}, "不是dict")), "")

print("── C. 纪律①：@ 不到 ⇒ 回落默认（行为与加功能前一致）──")
ok("C1 ⛔ **不传 text** ⇒ 行为一字不变（等于老实现）",
   P.role_text_of(PERSONA) == "鲸鱼：嘿嘿，接你一句。", repr(P.role_text_of(PERSONA)))
ok("C2 ⛔ text 里**没有 @** ⇒ 与不传 text 完全一致",
   P.role_text_of(PERSONA, text="你怎么看") == P.role_text_of(PERSONA), "")
ok("C3 ⛔ text 里 @ 的**不是槽位名** ⇒ 与不传 text 完全一致",
   P.role_text_of(PERSONA, text="@小明 你怎么看") == P.role_text_of(PERSONA), "")
ok("C4 ⭐ @ 到槽位 ⇒ 换成**那一个**的正文",
   P.role_text_of(PERSONA, text="@黛玉 你怎么看") == "黛玉：我原是个爱哭的。",
   repr(P.role_text_of(PERSONA, text="@黛玉 你怎么看")))
ok("C5 ⛔ 配了槽位但**没配 @ 名字**的那个槽位永远选不中（只按名字选，不猜）",
   P.role_text_of({"active": "小鲸鱼", "role_text": "全局默认正文",
                   "slots": {"阿飞": {"role_text": "阿飞的话"}}},
                  text="@阿飞 你说呢") == "阿飞的话", "")
ok("C6 ⭐ 槽位卡**只写了七字段、没写 `role_text`** ⇒ 用卡渲染出正文（不回落全局）",
   "【动机】" in P.role_text_of(
       {"active": "小鲸鱼", "role_text": "全局默认正文",
        "slots": {"阿飞": {"bot_name": "阿飞", "motive": "为了找到杀父仇人，改了名字混进城里",
                           "speech": "话少"}}},
       text="@阿飞 你说呢"), "")
ok("C7 ⛔ 完**全没配槽位** ⇒ 回落全局 `role_text`（老路径不被弄坏）",
   P.role_text_of({"active": "", "role_text": "全局默认正文", "slots": {}},
                  text="@阿飞 你说呢") == "全局默认正文", "")

print("── D. 接线面（能力写了必须真有人用）──")
_RTO = _ss.func_src(PROMPT_SRC, "role_text_of")
ok("D1 分母守卫：`role_text_of` 已改成收 `text`（>2 次出现该参数名）",
   _RTO.count("text") >= 3, "%d 次" % _RTO.count("text"))
ok("D2 ⭐ `role_text_of` **真的问了** `role_agent`（不是自己又写一套名字匹配）",
   "role_agent" in _RTO and "pick_slot" in _RTO, "")
ok("D3 ⭐ `build_system_prompt` 把 `text` **传下去了**",
   "def build_system_prompt(persona: dict | None = None, text: str = \"\")" in PROMPT_SRC
   and "role_text_of(cfg, text=text)" in PROMPT_SRC, "")
_PM = io.open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
ok("D4 ⭐ **产品真调用点**（`run_agent`）把**这一轮的触发文本**传进去了",
   "build_system_prompt(text=_trig_text)" in _PM and "_trig_text" in _PM, "")
ok("D5 传的是 `trigger` 里各条的 `text`（与运行明细里的口径同源）",
   'str(t.get("text") or "")' in _PM, "")
ok("D6 ⛔ 老调用点一个字都不用改（`build_system_prompt()` 无参仍可调）",
   "def build_system_prompt(persona: dict | None = None, text: str = \"\")" in PROMPT_SRC, "")

print("")
print("角色代理判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
