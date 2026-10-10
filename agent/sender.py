# -*- coding: utf-8 -*-
"""出站发送队列：所有对微信的出站消息都经过这里。

- 全局串行（微信窗口只有一个输入框，必须串行发送）
- 真人化间隔（随机区间 + 按字数附加）
- 分钟/小时限频（超限拒绝，工具把错误告诉模型）
- Markdown → 纯文本、超长切分
- 发出的每一条记进 ChatStore（self=True）
"""
from __future__ import annotations

import logging
import random
import re
import threading
import time

from .config import get_config, normalize_prob
from .util import format_clock_time, md_to_plain, rand_int, split_for_wx

log = logging.getLogger("persona-morph")

# 同会话"短窗去重"窗口（秒）：这段时间内已经发过的同一句话，不再重复发（防"连发两次"）。
_DEDUP_WINDOW_S = 20.0
# 第 1 层（内存回望）**取多少条历史来比** —— 旧值是 12，在活跃群里盖不住这 20 秒：
#   群里聊得越热闹，自己刚发的那条越容易被群友的话挤出这 12 条窗口 ⇒ 去重形同虚设。
#   ⛔ 别往下调。取值＝`store.recent` 的默认口径（80），那是"一倍屏幕的量"，够用且不慢。
_DEDUP_MEM_LIMIT = 80
# 第 2 层（**消息库回读**）的时间窗（秒）。与 `wechat.already_sent_recently` 的默认值一致：
#   那一层专门兜"重试间隔超过 20 秒"与"跨进程/重启后重发"（第 1 层看不见这两种）。
_DEDUP_DB_WINDOW_S = 120.0


def _dedup_in_batch(parts: list, part_src: list) -> tuple:
    """**第 0 层：本批内部去重** ⇒ `(parts, part_src, dropped)` —— 同一批里出现两次一样的文本，
    只留**第一次**。

    ⭐ 为什么必须单独一层（2026-10-11 用户报障：「重复发好几条同样消息」）：
      下面两层比的都是"与**历史**比"（内存回望 / 消息库回读），
      **本批里自带的重复谁也拦不住** —— 模型一轮里给出「哈哈哈／哈哈哈」这种，会原样发两条出去。

    ⚠️ 从 `send_text_batch` 里抽出来的（那函数已 300+ 行，结构棘轮盯着）——
      **逻辑一字未改**，只是把体量挪个地方。
    """
    dropped = []
    seen = set()
    keep, keep_src = [], []
    for t, src in zip(parts, part_src):
        k = str(t or "").strip()
        if k and k in seen:
            log.warning("跳过本批内的重复条（同一批里出现了两次）：%s", t[:60])
            dropped.append(t)
            continue
        if k:
            seen.add(k)
        keep.append(t)
        keep_src.append(src)
    return keep, keep_src, dropped

# ── 被引用对象是**媒体消息**时，跳过引用、直接普通发送（F8） ──────────────────
# 为什么：引用定位靠"在可见画面里 OCR 找到那条消息的文字"（`wechat.reply_quote` 的
#   `target_text` 匹配）。而语音/图片/视频/文件在屏幕上是**气泡/缩略图，不是文字** ⇒
#   **永远定位不到**，只会白等几秒再退回普通发送。
#   （朋友那边当天日志实证：3 次引用失败**全是**媒体消息。）
# ⇒ 与其白试，不如**一开始就不试**。
_QUOTE_SKIP_PREFIXES = ("[语音]", "[图片]", "[视频]", "[表情]", "[动画表情]", "[文件]", "[链接]")


def _is_media_placeholder(text) -> bool:
    """这条被引用的内容是不是"媒体占位符"（不是可 OCR 的文字）。见上方注释。"""
    t = str(text or "").strip()
    return bool(t) and t.startswith(_QUOTE_SKIP_PREFIXES)

# 内部故障话术（**只许留在本机控制台与日志**）。命中即拦下、不发群。
# 词表取自真机现场（机器人真发进群的句子）与既有红线（prompt.py 第 7 条）。
_INTERNAL_FAIL_PHRASES = (
    "会话投递失败", "投递失败", "本轮未发言", "未能发言", "没能发言",
    "发送失败了", "发送失败", "发送没成功", "没发成功", "没发出去", "未发出",
    "发不出去", "发不出来", "会话没对上", "没对上会话",
    "这轮先不说", "本轮先不说", "这轮不说了", "本轮不说了",
    "工具报错", "工具调用失败", "接口报错", "系统错误", "内部错误",
    "没有拿到会话", "抓不到会话", "获取会话失败",
    # ⛔ `send_retry` 接线修好后，工具回执里会出现
    #   "我已经排进重试队列 / 自动补发一次 / 现场没认准" 这类**内部机制话术**，模型一旦转述就进群。
    #   实测原来 3/3 放行（只有带"没发出去"的那种才被拦）⇒ 补进词表。
    "重试队列", "排进队列", "排进重试", "补发", "没认准", "现场没认准",
)
# 结构性判据（现场新变体「（发送没成功，这轮先不说了）」靠词表漏了）：
#   一句**被括号整体包起来**（或很短）的话，同时带 A 组（动作/系统词）和 B 组（失败态词）
#   ⇒ 判为内部故障话术。词表只能覆盖见过的写法，这条兜"没见过的写法"。
_FAIL_A = ("发送", "发出", "投递", "回复", "发言", "会话", "本机", "系统", "工具", "接口", "链路")
_FAIL_B = ("失败", "没成功", "不成功", "没发", "未发", "发不出", "报错", "异常", "超时",
           "拦下", "拦截", "卡住", "先不说", "不说了", "没能")
_FAIL_WRAP_RE = re.compile(r"^[\s（(【\[]+.*[\s）)】\]]+$")
_blocked_internal: list = [] # 最近被拦下的内部故障话术（诊断用，控制台可读）


def _is_internal_failure(text: str) -> str:
    """这句是不是"内部故障话术"？命中返回原因（词或 "结构性"），否则空串。

    ⚠️ 只筛**我们自己要发出去的内容**（出站），不筛收到的群消息。
    """
    t = str(text or "").strip()
    if not t:
        return ""
    for p in _INTERNAL_FAIL_PHRASES:
        if p in t:
            return p
    core = t.strip("（）()【】[]“”\"\' \n\t")
    if len(core) <= 24 and _FAIL_WRAP_RE.match(t):
        if any(a in core for a in _FAIL_A) and any(b in core for b in _FAIL_B):
            return "结构性（括号内的状态汇报句）"
    return ""


def outbound_gate_status() -> dict:
    """给控制台读的出站闸门读数（**前端后端一体**：闸门拦了什么，用户要看得见）。"""
    return {"blocked_internal": len(_blocked_internal),
            "last": (_blocked_internal[-1] if _blocked_internal else None),
            "dedup_window_s": _DEDUP_WINDOW_S}


# ─────────────────────────────────────────────────────────────────────────────
# P0-10 第二条闸：**内心独白** 与 **原始 XML**（工具调用格式泄漏）
#   来源：`报告/完整计划-20260930/...v2.md` §7 P0-10（朋友版 `sender.py` 有这两条拦截）
#   为什么要它：两类内容都不该出现在群里 ——
#     · **内心独白**：模型把"思考过程"写进括号/标签，被当成消息发出去（社死级）；
#     · **原始 XML**：模型的**原生工具调用格式**（`<tool_call>` / `<function=…>` / `<invoke …>`）
#       泄漏进消息正文 —— 用户看到的是机器码，且暴露内部实现。
#   ✅ 策略＝**保守**：只拦"**整段就是**"这类（如整句被 `（心想：…）` 包住、整段以 `<tool_call>` 开头）；
#      正文里**提到**"思考"/尖括号**不拦**（否则会误杀正常内容）。
# ─────────────────────────────────────────────────────────────────────────────

#: 内心独白的**标记词**（出现在句首/括号开头才算）
#: ⚠️ 只收**明确是独白腔**的：像"让我想想""我需要先…"这类在正常聊天里也会出现，
#:   收进来会**误杀正常回复** —— 出站闸宁可漏一点，也不能把正常话拦掉。
#: ⚠️ **裸"想法"已从词表移除**：它在句首是正常话，
#:   而它想拦的独白形态由"我的想法是"覆盖 ⇒ 留着只会误杀。
_THOUGHT_MARKERS = ("心想", "内心独白", "内心的想法", "我的想法是", "思考过程",
                    "thinking:", "thinking：", "reasoning:", "reasoning：", "<thinking>",
                    "作为AI", "作为一个AI", "我作为AI", "<analysis>", "analysis:",
                    "思考：", "分析：")
#: 整段"只有一个括号包裹的独白"或"整段以标记词开头"。
#: ⚠️ 由 `_THOUGHT_MARKERS` **构建**（不另抄一份词表）：两处各抄一份必然漂移 ——
#:   改词表的人只改一处，另一处就永远不生效（本项目在"同一张表抄两遍"上栽过）。
_THOUGHT_LEAD_RE = re.compile(
    r"^[\s（(【\[]*(?:我)?(?:" + "|".join(re.escape(str(_m)) for _m in _THOUGHT_MARKERS) +
    r")\s*[:：]?", re.I)
_THOUGHT_TAG_RE = re.compile(r"^\s*<\s*(?:thinking|reasoning|thought)\b[^>]*>", re.I)
#: 原始 XML / 工具调用格式（整段以这些标签开头即判泄漏）
_RAW_XML_RE = re.compile(
    # ⚠️ `\?xml`（XML 声明 `<?xml version=…?>`）与 `msg`/`message`（协议报文的常见根标签）
    #   必须在这里 —— 模型整段吐出报文头时就是这类开头；只列 `tool_*` 一族会漏掉它们。
    r"^\s*<\s*(?:\?xml|xml|msg|message|response|tool_call|tool_calls|tool_use|tool_result|"
    r"function|function_call|functions|invoke|parameter|antml|result|output)\b", re.I)
#: 模型特殊 token 形态（如 `<|im_start|>` / `<|tool▁calls▁begin|>`）
_RAW_TOKEN_RE = re.compile(r"^\s*<\|[A-Za-z_\u2581\s]{2,40}\|>")

# ─────────────────────────────────────────────────────────────────────────────
# 第三类（2026-10-10 真机反馈驱动）：**括号包裹的"行动旁白 / 收尾语"**
#
# ⛔ 现场（用户转来的聊天记录，AI 自己说漏嘴）：
#      `(这轮就这一条，收尾了。)`   ← **真的发到群里了**
#      `(等对方回，先不追着发。)`、`（已回，结束）` 同类。
#
# ⛔ 为什么原来那两条闸拦不住：`_THOUGHT_MARKERS` 只收"**带标记词**的独白"
#    （心想 / 内心独白 / thinking: …）。上面这几句**一个标记词都没有** ——
#    它们是"**括号里的自我交代**"：讲的是"我这一轮打算怎么处理 / 要不要回 / 结束了"。
#    这正是模型最常写的一类元叙述，词表里**根本没有这一类** ⇒ 全被放行（实测：三句漏网）。
#
# ⚠️ **绝不许一律拦"括号包裹"**：正常回复里大量存在 `（笑）`、`（小声）`、`（挠头）`
#    —— 那是人设该有的**动作描写**，拦了就把正常话废掉。
#    ⇒ 判据必须**两件同时成立**才拦：
#      ① 整段就是**一对括号**包住的短句（外面没有别的话）；
#      ② 括号里的内容含**"元叙述"词**——说的是"这一轮的动作/流程/是否回复/结束"。
#    两条叠加之后：`（笑）` 不含元叙述词 ⇒ 不拦；`(这轮就这一条，收尾了。)` 含 ⇒ 拦。
# ─────────────────────────────────────────────────────────────────────────────

#: "元叙述"词：出现即说明这句在讲**"我这一轮怎么处理/要不要回/结束了"**，不是对群友说的话。
#: ⚠️ 只收**明确是流程交代**的；像"结束""先不"这类单独看会误伤正常话（"我不结束了"），
#:   所以下面还要**同时**要求"整段被一对括号包住"——两道叠加才拦。
#: ⛔ 踩过的坑（2026-10-10）：`这条`/`这轮`/`不追` **不能进表** ——
#:   它们在正常聊天里到处都是（`（这条消息有点长）`/`（这轮比赛真精彩）`/`（不追星）`），
#:   加进来立刻误杀 3 句。词条必须是**流程专有**的（"这条先不回"里的"先不回"才是特征，
#:   单一个"这条"不是）⇒ 所以下面凡"这条/这轮"一律**带上动词**才算数。
_META_ACTION_WORDS = (
    "收尾", "已回", "已回复", "先不", "不催",
    "不回了", "先观察", "保留", "打住", "到此", "收工",
    "等对方", "等他回", "等她回", "看情况再说", "暂不",
    # ⚡ 只收"动词在句里"或"流程专有"的形态；下面的补词同理
    "这轮就", "这条就", "本轮就", "下轮再", "下次再回", "先这样", "就先这样",
    "暂时不回", "回完了", "不等了", "看看他", "看他怎么", "让他自己想",
    "静观", "不接话", "不搭话", "占不做", "留到下次",
    # ⛔ 下面这几个**必须带动词**（单一个"结束/不追/放"会误杀正常话）：
    "本轮结束", "这条结束", "这轮结束", "不追了", "先放一放", "再说吧",
)

# ── 结构性判据：**整段被括号包住 + 讲的是"回复这个动作本身"** ────────────────────
#: ⛔ 2026-10-10 实测（为什么光靠上面的词表不够）：
#:   我造了 8 句**同类但没进词表**的变体去撞闸，**6 句全放行**
#:   （`（先看看他怎么说）`/`（回完了）`/`(就先这样吧)`/`（我不再追问）`/`（暂时不回）`/
#:    `（让他自己想想）`）。用户说"老是会发这些" —— 这就是词表法**永远差几个词**的必然后果。
#: ⇒ 必须补一条**与词表无关**的路。试过并**否掉**的写法（见 git 历史）：
#:   用"第一人称+动作+收束语气"三件套 ⇒ **误杀 15 句正常话**
#:   （`（我先撤了，明天见）`/`（这就来）`/`（别催我）`/`（等你消息）`…）——
#:   因为"看/撤/来/约/定/见"这些是**生活动作**，聊天里大量存在。
#: ✅ 真正的分界：这句讲的是不是"**回复/发消息这个行为本身**"。
#:   元动作词（回/发/答/追问/接话/搭话/理他…）**只在讲"我们俩在互相发消息"时**才出现；
#:   生活动作词（看/撤/来/约/见/定…）在**聊正事**时到处都是。
#:   ⇒ 判据 = 整段一对括号 + 含**元动作词** + 带**收束/否定/将来**语气。
_META_REPLY_VERBS = (
    "接话", "搭话", "回话", "回消息", "回复", "回他", "回她", "回这条",
    "回这轮", "回一句", "发消息", "发这条", "发这轮", "接着回", "继续回",
    "理他", "理她", "搭理", "再回", "追着", "追发", "催他", "催她", "催发", "多回",
    "连发", "刷屏", "接这句", "接这条",
)
#: 「追问」类词**单独不算**（`（不再追问了，抱歉）` 是正常道歉），
#: 必须与"不再/别再/先不"这类**明确的收束否定**同现，**且整段不含对话内容**才算元叙述。
_META_REPLY_SOFT = ("追问", "追着问", "接着问", "继续问")
#: 道歉/致意类词 —— 出现即说明这句**在跟人说话**，不是自我交代 ⇒ 直接放行
_META_POLITE = ("抱歉", "对不起", "不好意思", "谢谢", "多谢", "麻烦", "请你", "请问",
                "不好意思", "感谢", "辛苦", "你好", "您", "不好意思")
_META_HARD_STOP = ("不再", "别再", "先不", "暂不", "不追", "不催", "不接", "不搭", "到此",
                   "为止", "打住", "收尾", "结束", "收工", "停", "静观")
#: 收束 / 否定 / 将来语气（元叙述的口气）—— 与上面的元动作词**同现**才判
_META_TONE = ("不", "别", "先", "暂", "就", "算了", "罢", "到此", "为止", "结束",
              "收尾", "打住", "停", "放", "留", "等", "看看", "再说", "回头",
              "下轮", "下次", "这轮", "本轮", "这条", "完了", "好了")


def _is_paren_aside(text: str) -> str:
    """这句是不是**括号包裹的"行动旁白 / 收尾语"**（讲自己这一轮怎么回复，不是对群友说的话）？

    命中返回原因，否则空串。
    ⛔ 判据（**两道叠加**，缺一不拦）：
      ① 整段就是**一对**括号包住的短句（外面没有别的话、里面不嵌括号、不超 30 字）；
      ② 括号里的内容是"**元叙述**"，命中任一条：
          (a) 含 `_META_ACTION_WORDS` 的**明确流程词**（词表，快而准）；
          (b) 含 `_META_REPLY_VERBS` 的**元动作词**（讲"回复这个动作本身"）＋ `_META_TONE` 语气
              —— 这条兜"词表没见过的写法"，**且不会误杀生活话**
              （`（我先撤了，明天见）` 没有元动作词 ⇒ 放行）。
    ⛔ 为什么两条都要：单看括号会误杀 `（笑）`；单看词会把正文里的正常话也拦掉。
    """
    t = str(text or "").strip()
    if not t:
        return ""
    # ① 整段必须就是**一对**括号包住（括号里至少 1 个字 ⇒ 整段至少 3 字符）
    _pairs = (("（", "）"), ("(", ")"), ("【", "】"), ("[", "]"))
    core = None
    for _l, _r in _pairs:
        if t.startswith(_l) and t.endswith(_r) and len(t) >= 3:
            core = t[1:-1].strip()
            break
    if not core:
        return ""
    # 括号里**不许再嵌一层括号**（`（笑（小声））` 这类不是本类）
    if any(_l in core or _r in core for _l, _r in _pairs):
        return ""
    # ⛔ 太长的括号 = 正常话（引用、旁白式说明），不按"收尾语"处理
    if len(core) > 30:
        return ""
    # ②a 词表：明确流程词
    for _w in _META_ACTION_WORDS:
        if _w in core:
            return "行动旁白（括号包裹 · 含「%s」）" % _w
    # ②b 元动作词 ＋ 收束语气（不依赖词表，兜"没见过的写法"）
    _hit_v = [w for w in _META_REPLY_VERBS if w in core]
    if _hit_v and any(w in core for w in _META_TONE):
        return "行动旁白（结构性 · 讲回复行为＋收束语气：「%s」）" % _hit_v[0]
    # ②c 软词（追问类）：必须与**明确的收束否定**同现，且整段不含致意/对话内容
    #    （`（不再追问了，抱歉）` 是正常道歉 ⇒ 放行；`（我不再追问）` 才是自我交代 ⇒ 拦）
    _hit_s = [w for w in _META_REPLY_SOFT if w in core]
    if _hit_s and any(w in core for w in _META_HARD_STOP) \
            and not any(w in core for w in _META_POLITE):
        return "行动旁白（结构性 · 收束回复行为：「%s」）" % _hit_s[0]
    return ""


def pick_sticker_owner(parts) -> int:
    """★ 第 259 项硬闸：**一批消息里表情最多发 1 张**（"别连发"）⇒ 返回"由第几条负责发贴纸"。

    返回：
      · `>= 0` —— 由该下标那条发（本批里**第一条**能取到贴纸的）；
      · `-1`   —— 本批**一张都不发**（没有任何一条带贴纸；或预扫描失败）。

    ⛔ 为什么必须有它：原来**每条各自**解析标记、各自补发一张 ⇒ 一批三条各带一张就是"连发三张"，
    而提示词里只有"表情包积极度"这种**频率引导**，**拦不住**（第 259 项原话："只有频率层面的引导，
    没有硬闸门"）。
    ⛔ 只做"**少发**"、不做"多发"：本条**不新增**任何贴纸，文本那部分照旧由 `extract_marker` 摘标记。
    ⛔ 判不出就**不发**（预扫描异常 ⇒ `-1`）：与"宁可少发一张、也别连发"同向（fail-closed）。
    """
    try:
        from . import emoji_index as _ei0
    except Exception as _e: # noqa: BLE001
        log.info("表情连发闸：拿不到表情索引（按'本批不发贴纸'处理）：%s", str(_e)[:60])
        return -1
    try:
        for _i0, _t0 in enumerate(list(parts or [])):
            _sid0 = _ei0.extract_marker(_t0)[1]
            if _sid0 is not None:
                return _i0
    except Exception as _e: # noqa: BLE001
        log.info("表情连发闸：预扫描失败（按'本批不发贴纸'处理）：%s", str(_e)[:60])
        return -1
    return -1


def _is_thought_leak(text: str) -> str:
    """这句是不是**内心独白泄漏**？命中返回原因，否则空串。"""
    t = str(text or "").strip()
    if not t:
        return ""
    if _THOUGHT_TAG_RE.match(t):
        return "内心独白（thinking 标签）"
    if _THOUGHT_LEAD_RE.match(t):
        # 只在"整段较短、且没被当成正常陈述"时拦：以标记词**开头**即认为整段是独白
        return "内心独白（标记词开头）"
    # 整段被一对括号包住、且括号内以标记词开头 ⇒ 独白
    core = t.strip("（）()【】[]“”\"' \n\t")
    if core != t and core:
        for m in _THOUGHT_MARKERS:
            if core.lower().startswith(str(m).lower()):
                return "内心独白（括号包裹）"
    return ""


def _is_raw_xml(text: str) -> str:
    """这句是不是**原始 XML / 工具调用格式泄漏**？命中返回原因，否则空串。"""
    t = str(text or "").strip()
    if not t:
        return ""
    if _RAW_TOKEN_RE.match(t):
        return "原始特殊 token"
    m = _RAW_XML_RE.match(t)
    if m:
        return "原始 XML（%s）" % m.group(0).strip()[:24]
    return ""


def outbound_leak_reason(text: str) -> str:
    """**出站泄漏总闸**（内部故障话术 / 内心独白 / 行动旁白 / 原始 XML 四合一）。

    ⇒ 统一入口，`send_text_batch` 只调这一个，避免以后再加一类时漏接线。
    """
    r = _is_internal_failure(text)
    if r:
        return "内部故障话术：" + r
    r = _is_thought_leak(text)
    if r:
        return r
    # ⚡ 第三类（2026-10-10 补）：括号包裹的行动旁白 —— 前两类只认"带标记词的独白"，
    #   拦不住 `(这轮就这一条，收尾了。)` 这种**无标记**的自我交代（真机漏网过）。
    r = _is_paren_aside(text)
    if r:
        return r
    r = _is_raw_xml(text)
    if r:
        return r
    return ""


class SendQueue:
    def __init__(self, wechat, store, on_sent=None):
        self.wechat = wechat
        self.store = store
        self.on_sent = on_sent
        self._lock = threading.Lock()
        self.minute_times: dict = {} # chatKey -> [ts]
        self.hour_times: dict = {}
        # P0-4 弱谓词的"记账上下文代次"：**发送开始那一刻**从 wechat 侧抄一份。
        # 发送过程中若发生"切走再切回"，wechat 侧代次会 +1 ⇒ 与本字段不等 ⇒
        # 强谓词判"代次已过期"（那次发送的上下文确实不是原来那个了）。
        # ⛔ 只为兼容直连构造（旧测试夹具）而在构造时同步一次；
        #    生产链路由 `send_text_batch` 每次发送前重新对齐（见那里）。
        self._ctx_generation = getattr(wechat, "_chat_generation", 0)

    def _check_rate(self, chat_key: str) -> None:
        cfg = get_config().get("send", {})
        now = time.time()
        minute = [t for t in self.minute_times.get(chat_key, []) if now - t < 60]
        hour = [t for t in self.hour_times.get(chat_key, []) if now - t < 3600]
        max_min = max(1, int(cfg.get("max_per_minute") or 20))
        # 「微信」段那个「每分钟限发」以前是**假开关**：控制台里有输入框，后端从来没读过它
        # ⇒ 用户把它调小以为踩了刹车，实际完全没生效。这里并进来，两个入口**取更严的那个**
        # （谁填得小谁生效）：只会更保守，不会把任何一侧的限制放开。填 0/空 = 该处不限。
        try:
            _wx_min = int((get_config().get("wechat") or {}).get("rate_limit_per_minute") or 0)
        except Exception:  # noqa: BLE001 — 配置形状不对时不改变既有行为
            _wx_min = 0
        if _wx_min > 0:
            max_min = min(max_min, _wx_min)
        max_hour = max(1, int(cfg.get("max_per_hour") or 500))
        if len(minute) >= max_min:
            raise RuntimeError("发送频率超限（每分钟最多 %d 条），请等一会再发" % max_min)
        if len(hour) >= max_hour:
            raise RuntimeError("发送频率超限（每小时最多 %d 条）" % max_hour)
        minute.append(now)
        hour.append(now)
        self.minute_times[chat_key] = minute
        self.hour_times[chat_key] = hour

    def _gap(self, text: str, is_last: bool) -> float:
        cfg = get_config().get("send", {})
        if is_last:
            return 0.0
        min_gap = max(200, int(cfg.get("min_gap_ms") or 1000))
        max_gap = max(min_gap, int(cfg.get("max_gap_ms") or 3000))
        by_len = min(8000, len(text or "") * int(cfg.get("by_length_ms") or 20))
        return min(15000, max(min_gap, rand_int(min_gap, max_gap) * 0.5 + by_len * 0.5)) / 1000.0

    def _should_auto_quote(self, chat_key: str):
        """「新一段对话」开始时，大概率引用对方最近一句话（默认 70%）。

        判定：
        - send.quote_on_new_talk 未关闭；
        - 概率 send.quote_reply_probability（默认 0.7）命中；
        - 机器人在该群**上一条消息已超过 quote_new_talk_gap_s（默认 300 秒）**——
          即对话已冷场、本轮算是"重新开始的一段对话"；
        - 找到最近一条「别人」发的文本消息。
        返回 (text, sender_name) 或 None（调用方传了显式 reply_to_message_id 时以显式为准）。
        """
        try:
            cfg = get_config().get("send", {})
            if cfg.get("quote_on_new_talk") is False:
                return None
            # ⚠️ 概率过 `normalize_prob`：界面上可填 `90`（百分比）⇒ 直转会得到 90.0
            #    ⇒ `random.random() >= 90.0` 永不成立 ⇒ "70% 概率"变成"每次必发"。
            prob = normalize_prob(cfg.get("quote_reply_probability", 0.7), 0.7)
            gap_s = max(0, int(cfg.get("quote_new_talk_gap_s", 300)))
            msgs = self.store.recent(chat_key, limit=60)
            my_last = max((int(m.get("ts") or 0) for m in msgs if m.get("self")), default=0)
            if my_last and (time.time() * 1000 - my_last) < gap_s * 1000:
                return None # 还在连续对话中，不重复开引用
            if random.random() >= prob:
                return None # 概率未触发
            for m in reversed(msgs):
                txt = str(m.get("text") or "").strip()
                sid = str(m.get("sender_id") or "")
                if not m.get("self") and sid.startswith("wxid_") and txt and not txt.startswith("["):
                    return (txt[:200], str(m.get("sender_name") or ""))
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass
        return None

    def _dedup_recent_memory(self, chat_key: str, parts: list, part_src: list) -> tuple:
        """**第 1 层：同会话短窗去重（内存回望）** ⇒ `(parts, part_src, dropped)`。

        （用户现场截图：同一条消息「早上好呀！」连发两次）
        同一段文本在过去 `_DEDUP_WINDOW_S` 秒内已经给自己发过，就不再发一遍；
        兜的是"同一轮被重跑/重试后重复发送"这类路径（模型自己的 send 与兜底补发都走这里）。

        ⚠️ 取样条数用 `_DEDUP_MEM_LIMIT`（= `store.recent` 的默认口径），**不再是 12** ——
          12 条在活跃群里连 20 秒都盖不住（自己发的那条会被群友的话挤出窗口），
          实测这正是"用着用着就开始重复"的来路。
        ⚠️ 从 `send_text_batch` 里抽出来的（那函数已 300+ 行，结构棘轮盯着）——**逻辑一字未改**。
        """
        _dedup_since = time.time() - _DEDUP_WINDOW_S
        try:
            _recent_self = [str((m or {}).get("text") or "") for m in
                            (self.store.recent(chat_key, limit=_DEDUP_MEM_LIMIT, include_self=True) or [])
                            if (m or {}).get("self") and int(str((m or {}).get("ts") or "0")) / 1000.0 >= _dedup_since]
        except Exception:
            _recent_self = []
        if not _recent_self:
            return parts, part_src, []
        dropped, keep, keep_src = [], [], []
        for t, src in zip(parts, part_src):
            if t in _recent_self:
                log.warning("跳过重复发送（%.0fs 内已发过同一句）：%s", _DEDUP_WINDOW_S, t[:60])
                dropped.append(t)
                continue
            keep.append(t)
            keep_src.append(src)
        return keep, keep_src, dropped

    def send_text_batch(self, chat_key: str, messages, reply_to_mid=None, at_user_id=None, reply_text="",
                        reply_sender_name=""):
        """发送一批文本。返回 {sent, failed}。reply_text 为被引用消息的原文（定位用），
        reply_sender_name 为被引用消息的发送者（头像定位用，缺省靠文本匹配）。

        引用规则（程序级，不依赖模型自觉）：
        - 模型显式传 reply_to_message_id → 按模型的引用；
        - 否则若「新一段对话开始」（机器人上条消息超 300s + 概率 70%）→ 自动引用
          对方最近一句话（send.quote_on_new_talk / quote_reply_probability / quote_new_talk_gap_s 可调）。
        """
        kind, chat_id = self._parse_key(chat_key)
        list_msgs = list(messages) if isinstance(messages, (list, tuple)) else [messages]
        if not list_msgs:
            raise RuntimeError("消息列表为空")
        # ★ P0-4：**发送开始那一刻**把会话上下文代次抄到手，整批共用这一份。
        #   为什么抄快照而不是每条现查：整个批次是"一次操作"，若中途用户切走又切回
        #   （代次 +2），逐条现查会让后半批"看起来仍然有效" —— 而事实上这次操作
        #   的上下文已经被打断过了。快照让整批**同命运**：要么都有效、要么都过期。
        self._ctx_generation = getattr(self.wechat, "_chat_generation", 0)
        hard_split = int(get_config().get("send", {}).get("hard_split_at") or 0)
        parts = []
        # ⛔ `parts` 是"过 md_to_plain + 可能被 hard_split"之后的产物，
        #   **下标与调用方的 `messages` 不对应**（还有内部话术闸/去重会删条目）⇒ 失败回执里必须带上
        #   **原始那条文本**，否则调用方只能拿前 20 字去 `messages` 里盲找（会入队"已发成功"那条 ⇒ 补发＝重复发）。
        part_src = []
        for m in list_msgs:
            _orig = str(m or "")
            plain = md_to_plain(_orig)
            if not plain:
                continue
            if hard_split > 0 and len(plain) > hard_split:
                _sp = split_for_wx(plain, hard_split)
                parts.extend(_sp)
                part_src.extend([_orig] * len(_sp))
            else:
                parts.append(plain)
                part_src.append(_orig)
        if not parts:
            raise RuntimeError("消息内容为空")

        # ★ 「本轮回累计条数上限」的**第二处**（也是最后一道）：`tools.py::_exec_send_message`
        #   已在**调用前**按 `session["sent"]` 截过一轮，但那条路只管模型显式调 `send_message` 的情形 ——
        #   兜底补发、别的入口（转发/图片/语音）若也走到这里，就会绕过那道闸 ⇒ **这里必须再兜一次**
        #   （否则"只补一处 ⇒ 半残"，正是报告里点明的那条）。
        #   ⛔ 只**截断**、不报错（末条合并）：宁可少发一条，也不让"本来能发出去的"整批失败。
        _cap = int(((get_config().get("send") or {}).get("max_parts_per_reply")
                    or (get_config().get("send") or {}).get("max_total_per_reply") or 0) or 0)
        _truncated_to = 0
        if _cap > 0 and len(parts) > _cap:
            log.warning("本轮回条数上限 %d，本批 %d 条 ⇒ 截断到 %d 条（末尾合并，不报错）",
                        _cap, len(parts), _cap)
            _truncated_to = _cap
            # **末尾合并**：把超出配额的尾部并成一条（`\n` 连），而不是简单丢弃 ——
            # 丢弃会丢内容，合并只是"少一个气泡"。
            _head, _tail = parts[:_cap - 1], parts[_cap - 1:]
            _merged = "\n".join(_tail)
            parts = _head + [_merged]
            part_src = part_src[:_cap - 1] + ["\n".join(part_src[_cap - 1:])]

        # 🔴 （用户现场截图：群里出现了「（会话投递失败，本轮未发言。）」这类内部话术）：
        #   **内部故障话术的机械拦网** —— 以前只写在提示词里（`prompt.py` 第 7 条），模型不听话时照样发进群。
        #   这是项目红线（故障只许出现在本机控制台与日志）⇒ 在**发之前**逐条筛掉（走统一入口 `outbound_leak_reason`）。
        _kept = []
        _kept_src = []
        for _t, _src in zip(parts, part_src):
            _why = outbound_leak_reason(_t)
            if _why:
                log.warning("拦下出站泄漏（%s）：%s", _why, _t[:60])
                _blocked_internal.append({"why": _why, "text": _t[:120]})
                continue
            _kept.append(_t)
            _kept_src.append(_src)
        parts = _kept
        part_src = _kept_src
        if not parts:
            raise RuntimeError("这一批全被内部故障话术闸拦下（故障只留本机日志，不发群）")

        # 🔴 第 0 层：**本批内部去重** —— 同一批里出现两次一样的文本，只留第一次。
        #   （为什么单列一层、以及"左键可放心补点"那条依据，见 `_dedup_in_batch` 的 docstring）
        parts, part_src, _d0 = _dedup_in_batch(parts, part_src)
        _deduped = list(_d0)

        # 🔴 第 1 层（用户现场截图：同一条消息「早上好呀！」连发两次）：
        #   **同会话短窗去重**（内存回望）—— 理由见 `_dedup_recent_memory` 的 docstring。
        parts, part_src, _d1 = self._dedup_recent_memory(chat_key, parts, part_src)
        _deduped.extend(_d1)

        # 🔴 第 2 层：**从消息库回读**（`wechat.already_sent_recently`，三态）。
        #   第 1 层只看内存、只回望 `_DEDUP_WINDOW_S` ＝ 20 秒 ⇒ 它兜不住两件事：
        #     ① **重试间隔超过 20 秒**才发生的重发；
        #     ② **跨进程/重启后**的重发 —— 重启后内存里"自己发过什么"是空的，可库里那条还在。
        #   本层读的是**消息库**（`_db_recent_rows`）⇒ 重启也认得出，这才是"根除"那一层。
        #   ⛔ 三态语义（`already_sent_recently` 的 docstring 定死，不许简化成两态）：
        #     `True`＝确实已发过 ⇒ **拦**；`False`＝确实没发过 ⇒ 放行；
        #     **`None`＝读不出来** ⇒ 按"不知道"处理、**照常发** ——
        #     不能因为"这次读不到库"就让整条发送链停摆（库故障时什么都不发是更糟的结果）。
        if self.wechat is not None and hasattr(self.wechat, "already_sent_recently"):
            _kept3, _kept3_src = [], []
            for _t, _src in zip(parts, part_src):
                try:
                    _dup = self.wechat.already_sent_recently(chat_key, _t,
                                                             window_s=_DEDUP_DB_WINDOW_S)
                except Exception:  # noqa: BLE001
                    _dup = None
                if _dup is True:
                    log.warning("跳过重复发送（消息库里 %.0fs 内已发过同一句）：%s",
                                _DEDUP_DB_WINDOW_S, _t[:60])
                    _deduped.append(_t)
                    continue
                _kept3.append(_t)
                _kept3_src.append(_src)
            parts, part_src = _kept3, _kept3_src

        # 幂等语义：**整批都是重复**时不算失败、也不抛 —— 那些内容确实已经发出去了，
        # 对调用方而言"要发的已经发过"。走与 `wechat.send_text` 拦截分支同口径的
        # **幂等成功**，并按 `deduped` 分账（调用方据此**不计新增发送数、不入重试队列**，
        # 但 `append_self` 仍照旧 —— "我说过什么"的上下文应保持一致）。
        # 抛异常会把"其实已发"记成失败，是反的。
        if not parts:
            log.info("这一批全部命中去重（已发过）⇒ 按幂等成功返回（不再抛）")
            return {"sent": [], "failed": [], "deduped": _deduped}

        # 风险闸门：**发之前**判（拦下的提示只在本机/控制台出现，绝不往微信侧发）
        from . import risk as _risk
        for _t in parts:
            _v = _risk.check(chat_key, _t)
            if not _v.allowed:
                log.warning("风险闸门拦下出站消息（%s/%s）：%s", _v.level, _v.code, _v.message)
                raise _risk.RiskBlocked(_v)

        # 拟人节奏：**发之前**按真人时间分布等一等（**只慢，不拦**）。
        # ⛔ 顺序很关键：先过 `risk`（判"发不发"）再走本层（判"多慢发"）——
        #    反过来的话，被拦下的消息也白等了一次间隔。
        # ⛔ 本层**不改变"消息最终会发出去"**这个事实，所以默认开；
        #    `risk` 的频率阈值默认 0（用户自控"发不发"）——两者口径不冲突，
        #    见 `agent/rhythm.py` 模块 docstring。
        try:
            from . import rhythm as _rhythm
            _waited = _rhythm.gate("send_text")
            if _waited >= 1.0:
                log.info("拟人节奏：出站前等待 %.1fs（%s）", _waited, _rhythm.profile().name)
        except Exception as _e:   # noqa: BLE001 节奏层出任何问题都不许挡住发送
            log.debug("拟人节奏层跳过（%s）", _e)

        sent = []
        failed = []
        # 程序级自动引用：新一段对话开始 + 概率命中 → 引用对方最近一句话
        auto_quote = None
        if not reply_to_mid:
            auto_quote = self._should_auto_quote(chat_key)
            if auto_quote:
                reply_text = auto_quote[0]
                reply_sender_name = auto_quote[1] or reply_sender_name
                log.info("新对话开始，自动引用对方最近一句：%s", reply_text[:40])
        # ★ 被引用的是媒体消息 ⇒ 跳过引用走普通发送（F8）。
        #   上面那条是"自动引用"，这里也要挡：自动引用挑到的"对方最近一句"很可能就是一条语音/图片。
        if _is_media_placeholder(reply_text):
            log.info("跳过引用走普通发送：被引用的是媒体消息（%s），OCR 定位不到气泡里的内容",
                     str(reply_text)[:20])
            auto_quote = None
            reply_to_mid = None
            reply_text = ""
            reply_sender_name = ""
        with self._lock, self.wechat.fg_hold():
            # ★ 第 259 项硬闸：**先定"本批由谁发贴纸"**（最多一张）——
            #   放在循环外，是因为"连发"是**批级**现象：每条自己判"我该不该发"永远判不出来。
            _sticker_owner = pick_sticker_owner(parts)
            if _sticker_owner >= 0:
                log.info("表情连发闸：本批贴纸由第 %d 条负责发（其余只发文本）", _sticker_owner + 1)
            # fg_hold：整批只置前一次，全部发完才把微信送回后台（否则分条发送
            # 会「发一条切后台→下一条又置前」地闪来闪去，且每条都各自恢复易受前台锁影响）
            for i, text in enumerate(parts):
                is_first = i == 0
                is_last = i == len(parts) - 1
                # ★★ **表情标记解析**（E5 接线，）：
                #   模型按提示词里的清单写 `[[表情:12]]` ⇒ 这里**先把标记摘掉**，
                #   文本照常发（标记**绝不发给群友**），**发完文本再补发那张表情**。
                #   取不到文件/编号越界 ⇒ `_sid=None`，只在日志里记一句，**不影响文本发送**。
                _sticker_id = None
                try:
                    from . import emoji_index as _ei
                    _clean, _sticker_id, _swhy = _ei.extract_marker(text)
                    if _sticker_id is not None and _clean != text:
                        log.info("解析到表情标记：%s", _swhy)
                        text = _clean
                        if not text:                     # 整条只有标记 ⇒ 至少别发空串
                            text = ""
                except Exception as _e:
                    log.info("表情标记解析异常（忽略）：%s", str(_e)[:80])
                gap = self._gap(text, is_last)
                try:
                    self._check_rate(chat_key)
                    if gap > 0:
                        time.sleep(gap)
                    # 引用 / @ 只在第一条上生效；引用是「引用最近一条消息」（近似），失败退回普通发送
                    use_at = at_user_id if is_first else None
                    use_quote = (reply_to_mid or auto_quote) if is_first else None
                    _dd: list = [] # 接 wechat 侧的 3 秒去重标记（命中⇒这条按幂等、分账进 deduped）
                    if use_quote:
                        ok, msg = self.wechat.reply_quote(chat_id, text, target_text=reply_text,
                                                          target_sender_name=reply_sender_name)
                        if not ok:
                            log.warning("引用发送失败（第%d条），退回普通发送：%s", i + 1, msg)
                            ok, msg = self.wechat.send_text(chat_id, text, dedup_out=_dd)
                    elif use_at:
                        name = self.wechat.member_name(chat_id, use_at)
                        if name and not str(name).startswith("wxid_"):
                            ok, msg = self.wechat.send_text_at(chat_id, name, text, dedup_out=_dd)
                        else:
                            ok, msg = self.wechat.send_text(chat_id, text, dedup_out=_dd)
                    else:
                        ok, msg = self.wechat.send_text(chat_id, text, dedup_out=_dd)
                    if not ok:
                        raise RuntimeError(msg or "发送失败")
                    # ★ 文本发成功后，**补发**模型选中的那张自定义表情
                    #   （走 `send_image` ⇒ 独立窗盲粘贴；失败只记日志，不回滚已发出的文本）
                    if _sticker_id is not None and i != _sticker_owner:
                        # ★ 第 259 项硬闸：**本批已定由别人发** ⇒ 这条只发文本（标记早已被摘掉）
                        log.info("表情连发闸：第 %d 条也带贴纸，但本批由第 %s 条发 ⇒ 这条不补发",
                                 i + 1, (_sticker_owner + 1) if _sticker_owner >= 0 else "无")
                        _sticker_id = None
                    if _sticker_id is not None:
                        try:
                            from . import emoji_index as _ei2
                            _sp = _ei2.resolve(_sticker_id)
                            if _sp:
                                _sok, _smsg = self.wechat.send_image(chat_id, _sp)
                                log.info("补发表情 id=%s ⇒ %s（%s）",
                                         _sticker_id, _sok, str(_smsg)[:60])
                            else:
                                log.info("表情 id=%s 取不到本地文件 ⇒ 不发", _sticker_id)
                        except Exception as _e:
                            log.info("补发表情失败（不影响文本）：%s", str(_e)[:80])
                    ts = int(time.time() * 1000)
                    # ★ P0-4：**两个谓词分开判**（数据有效性 vs 界面可更新性）。
                    #   实测场景：消息发出去的那一瞬间用户切到了别的会话 ——
                    #     · `persist`（强谓词：账号+会话+代次+上下文四项全对）过了 ⇒ **记录照写**；
                    #       不写的话下次没人知道这条已发过 ⇒ 重试会**重复发**（"怕复活"却复活了）。
                    #     · `visible`（弱谓词：此刻还看着这个会话吗）没过 ⇒ **界面不跟**；
                    #       此刻用户看的是另一个会话，把这条"已发送"画上去就是**串台**。
                    #   ⛔ 顺序：先算再写 —— 两侧的判定必须基于**同一个时刻**的快照，
                    #      分开算两次会读到不同的"当前会话"（中间用户可能又切了一次）。
                    _vd = self._verdict_for(chat_key)
                    # 分账（幂等集中在一处）：命中 wechat 侧 3 秒去重 ⇒ 这条**没真发出去**，
                    #   但内容确实在几秒前发过：`append_self` 照旧（上下文一致），
                    #   风险窗计数**不加**（note_sent 不调），`sent` 不计、改记 `deduped`。
                    if _dd:
                        log.info("第%d条命中 3 秒去重（幂等，不重复发）：%s", i + 1, text[:40])
                        _deduped.append(text)
                        if _vd["persist"]:
                            self.store.append_self(chat_key, text, ts=ts)
                        else:
                            log.warning("去重条**不记**发送台账（%s）：%s", _vd["persist_reason"], text[:40])
                        self._notify_sent(_vd, chat_key, text)
                        continue
                    if _vd["persist"]:
                        self.store.append_self(chat_key, text, ts=ts)
                    else:
                        # 强谓词没过 ⇒ **不写台账**。这条消息确实出去了（调用方会在 `sent` 里看到），
                        # 但"记账"这一步的上下文已经失效 —— 写到错误的会话名下比不写更糟。
                        log.warning("上下文已失效，**不记**发送台账（%s）：%s",
                                    _vd["persist_reason"], text[:40])
                    _risk.note_sent(chat_key, text) # 记账（闸门的窗口计数只认机器人出站路径）
                    self._notify_sent(_vd, chat_key, text)
                    # 反应评分：记录这条 reaction（群友后续回应会在 on_incoming 里加分）
                    try:
                        from .scoring import note_reaction
                        note_reaction(text, chat_key)
                    except Exception:
                        pass
                    sent.append({"text": text, "at": format_clock_time(ts)})
                except Exception as e:
                    failed.append({"index": i, "text": text, "error": str(e),
                                   "src": (part_src[i] if i < len(part_src) else text)})
        if failed and not sent:
            _why = "；".join("第%d条「%s」：%s" % (f["index"] + 1, str(f["text"])[:20], f["error"])
                            for f in failed)
            # 喂给版本门的"本机自证"：整批一条都没出去 ⇒ 记一次能力类失败（连续到阈值就改判）
            try:
                from . import version_gate as _vg2

                _vg2.note_send_result(False, _why)
            except Exception: # noqa: BLE001 — 喂自证失败不影响发送本身
                pass
            raise RuntimeError(_why)
        if failed:
            print("[sender] 部分发送失败（%d/%d）：%s" % (len(failed), len(parts),
                  "；".join("第%d条「%s」：%s" % (f["index"] + 1, str(f["text"])[:20], f["error"])
                            for f in failed)))
        # 只要有任意一条真的出去了 ⇒ 这对版本"能发"（自证计数清零）
        if sent:
            try:
                from . import version_gate as _vg3

                _vg3.note_send_result(True)
            except Exception: # noqa: BLE001
                pass
        return {"sent": sent, "failed": failed, "deduped": _deduped}

    def _verdict_for(self, chat_key: str) -> dict:
        """给 `chat_key` 算一次 P0-4 两个谓词的结论（发送记录点每次调用前算一遍）。

        **为什么每次重算、不缓存**：两个谓词问的都是"**此刻**"，缓存等于拿旧答案
        回答新问题 —— 那正是"发错人"要防的东西。成本是一次属性读取 + 一次 OCR（后者
        只在需要时走），相对发送本身的秒级开销可忽略。

        ⛔ **任何一侧取不到值都必须走 fail-closed**（判无效 / 判不更新）：
        本函数把取不到的情形交给 `verdict` 自己判空 —— 见那两个函数的 docstring。
        """
        from . import verdict as _vd
        # 账号：库自报的账号目录名（`db_account` 是已有属性，不是为本功能新加的）。
        try:
            _acct = str(getattr(self.wechat, "db_account", "") or "")
        except Exception:  # noqa: BLE001
            _acct = ""
        # 代次：`wechat` 侧维护的"会话上下文代次"。取不到时给 `None` ⇒ 强谓词判 invalid。
        _gen = getattr(self.wechat, "_chat_generation", None)
        _ctx_gen = getattr(self, "_ctx_generation", None)
        # "此刻看着谁"：走已有的 `current_chat_name()`（会话列表绿底行 + OCR）。
        #   ⛔ 不新写一套读法 —— 那会与 `chat_header` / `chat_ocr` 的口径分叉。
        #   ★ P0-3：读到的身份**过一遍 `scope.scope_of()`** ——
        #     读到 ⇒ 已解析 scope；**读不到 ⇒ 新建一次性隔离桶**。
        #     为什么不让它保持空串：空串在不少地方会被 `or` 兜底成别的值（"读不到"变成"不过是那个"），
        #     而隔离桶是个**显式对象** —— 它与任何身份都不相等，下游想误用也误用不了。
        _vis = ""
        try:
            _vis, _ = self.wechat.current_chat_name()
        except Exception:  # noqa: BLE001
            _vis = ""
        try:
            from . import scope as _scope
            _vis = _scope.scope_of(_vis)
        except Exception:  # noqa: BLE001 — 隔离件取不到时保持原值，行为同历史（不放行新的更新）
            pass
        return _vd.decide(
            account=_acct, chat_key=chat_key, generation=_gen,
            ctx_account=_acct, ctx_chat_key=chat_key, ctx_generation=_ctx_gen,
            visible_chat=_vis, visible_account=_acct,
        )

    def _notify_sent(self, vd: dict, chat_key: str, text: str) -> None:
        """按弱谓词的结论决定**要不要回调界面层**。

        `on_sent` 的消费者是界面/日志。用户在发送完成前
        切走会话时，把它画到当前界面上就是串台 ⇒ 只在 `visible` 为真时回调。
        **记录（持久层）在上游已按强谓词写过，与本函数无关** —— 两者独立。
        """
        if self.on_sent and vd.get("visible"):
            self.on_sent(chat_key, text)
        elif self.on_sent:
            log.info("界面不更新（%s）：%s", vd.get("visible_reason") or "", str(text)[:40])

    def send_image(self, chat_key: str, local_path: str):
        """发送一张本地图片（微信剪贴板粘贴）。"""
        kind, chat_id = self._parse_key(chat_key)
        from . import risk as _risk
        _v = _risk.check(chat_key, "[图片]")
        if not _v.allowed:
            raise _risk.RiskBlocked(_v)
        # 拟人节奏：**只慢，不拦**（顺序：先 risk 判"发不发"，再本层判"多慢发"）
        try:
            from . import rhythm as _rhythm
            _waited = _rhythm.gate("send_image")
            if _waited >= 1.0:
                log.info("拟人节奏：出站前等待 %.1fs（图片，%s）", _waited, _rhythm.profile().name)
        except Exception as _e:   # noqa: BLE001
            log.debug("拟人节奏层跳过（%s）", _e)
        with self._lock, self.wechat.fg_hold():
            self._check_rate(chat_key)
            time.sleep(rand_int(600, 1500) / 1000.0)
            ok, msg = self.wechat.send_image(chat_id, local_path)
            if not ok:
                raise RuntimeError(msg or "图片发送失败")
            ts = int(time.time() * 1000)
            self.store.append_self(chat_key, "[图片]", ts=ts)
            _risk.note_sent(chat_key, "[图片]")
            if self.on_sent:
                self.on_sent(chat_key, "[图片]")
            return {"sent": True}

    @staticmethod
    def _parse_key(chat_key: str):
        kind, _, chat_id = str(chat_key).partition(":")
        if kind not in ("group", "private"):
            raise RuntimeError("非法会话 key：%s" % chat_key)
        return kind, chat_id
