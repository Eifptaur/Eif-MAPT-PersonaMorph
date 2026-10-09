# -*- coding: utf-8 -*-
"""人设卡：**七字段模型 ＋ 分层渲染 ＋ 导出/导入 ＋ 两版模板**（第 101·102·104·105·106·108·109·111 项）。

为什么要独立成模块（而不是改 `assets/persona/cards.json` 的 schema）：
  · 那 244 张卡的 `text` 是**一整段 markdown**，而它是**内容**不是结构；
    直接改 schema ＝ 让 244 条内容一起迁移，任何一处写坏都会让整个库读不出来（229 条判据全红）。
  · ⇒ 这里做**只读投影 ＋ 渲染**：`to_fields()` 把整段**尽力**拆进七字段（拆不出的整段落到
    `background`，**一个字都不丢**）；新写的卡可以直接给七字段。
  · 迁移方向是**渐进的**：内容补写是人工/模型的事，代码这层只要保证"两种形态都能读、都能渲染"。

七字段（顺序按调研报告 4.2 的交付物）：
  `background` 身份背景 · `personality` 性格 · `speech` 说话风格 · `motive` 动机 ·
  `values` 价值观 · `rules` 行为规则 · `limits` 缺点局限
⛔ 另外两个**不属于七字段**但必须有的层：
  · `disclosure` —— **AI 身份披露段**：扮演**真人/已有人物**时**强制存在且不可删**（合规）。
    ⭐ **但它是"写给用户看"的、不进模型上下文**（2026-10-10 用户口径）：
      - 卡里必须留着它（`import_card` 对真人卡仍强校验）⇒ 尽到**对用户的提醒义务**；
      - **不拼进模型提示词**（`render_prompt` 默认不注入）⇒ 不削弱扮演、不影响体验；
      - 模型侧的合规改走**一行轻量指令**（"被直接问到时如实说是 AI；平时别主动提"）。
  · `examples` —— **示例对话**：单独一层（报告 2.2：示例是语气锚定的第一杠杆）。
"""
from __future__ import annotations

import json
import logging
import os
import re

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(ROOT, "assets", "persona_templates")

#: 七字段（键 → 中文名）。顺序＝**渲染顺序**（报告 4.2：身份→性格→语言→动机→价值观→规则→局限）。
FIELDS = ("background", "personality", "speech", "motive", "values", "rules", "limits")
FIELD_CN = {"background": "身份背景", "personality": "性格", "speech": "说话风格",
            "motive": "动机", "values": "价值观", "rules": "行为规则", "limits": "缺点局限"}

#: **引导补全的顺序**（报告 4.3 / 第 104 项）：先写"那一个矛盾"，再回头校验自洽。
#   ⛔ 它是**引导顺序**，不是渲染顺序 —— 两者别混。
GUIDE_ORDER = ("personality", "motive", "background", "values", "rules", "speech", "limits")

#: 卡片包的 schema 版本号（导出/导入靠它判兼容；不匹配**明确报错**，不静默错读）
CARD_SCHEMA = 2

#: 整段 markdown 里的小标题 → 字段（尽力抽取；抽不到就落到 `background`）
_HEAD_MAP = (
    ("你是谁", "background"), ("角色介绍", "background"), ("身份", "background"),
    ("记忆与形象", "background"), ("背景", "background"),
    # ⚠️ 顺序＝匹配优先级（`key in head` 取第一个命中的）：更具体的写前面。
    #   实测：全库有 **94 处** `口头禅 / 标志台词` 与 **21 处** `必要对话扩展` 原本**没人认领**
    #   ⇒ 补进来之后 `speech` 的覆盖一下就上去了（这是机器能吃掉的部分，别丢给人）。
    ("口头禅", "speech"), ("标志台词", "speech"), ("口头", "speech"),
    ("节奏", "speech"), ("说话铁律", "rules"), ("铁律", "rules"),
    ("说话风格", "speech"), ("语言", "speech"), ("语气", "speech"),
    ("性格", "personality"), ("脾气", "personality"),
    ("动机", "motive"), ("为什么", "motive"),
    ("价值观", "values"), ("在意", "values"),
    ("行为规则", "rules"), ("说话规则", "rules"), ("规则", "rules"),
    ("AI 味黑名单", "rules"), ("黑名单", "rules"), ("群聊自觉", "rules"),
    ("缺点", "limits"), ("局限", "limits"),
    ("必要对话扩展", "examples"), ("对话示例", "examples"), ("示例", "examples"),
)

#: **句子级**线索词：原卡常常把"性格/说话风格"写在正文里而**没有立小标题**
#  （实测 244 张卡**全部**缺 5 个字段 —— 不是内容没写，是没分段）⇒ 按线索把整句搬过去。
#  ⛔ 只**搬**不改：搬走的句子必须能在 `_raw` 里**逐字找到**（判据钉住这条）；
#     搬错了也只是归类错，原文一个字没少。
_SENT_HINTS = (
    ("speech", ("说话", "语气", "口吻", "口癖", "口头禅", "话不多", "话很少", "话极少",
                "喜欢说", "爱说", "从不", "称呼", "敬语", "短句", "长句", "语速",
                "生硬", "冷淡地", "轻声", "嗓音", "口头")),
    ("personality", ("性格", "脾气", "表面", "其实", "内心", "看似", "冷漠", "热情",
                     "温和", "倔", "固执", "傲", "懒", "细心", "急躁", "安静", "开朗")),
    ("motive", ("想要", "为了", "目标", "动机", "执念", "渴望", "追求", "不愿", "害怕失去",
                "之所以", "就是为了")),
    ("values", ("在意", "重视", "认为", "价值观", "底线", "坚持", "看重", "信念", "原则")),
    ("limits", ("不擅长", "不会", "记性", "缺点", "弱点", "做不到", "容易", "总是忘",
                "怕麻烦", "不会表达", "不习惯")),
)

#: 一个"句子"的分界（中英文句号、换行、分号、破折号）
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;\n])")


def _harvest_sentences(text: str, out: dict, hits: dict) -> None:
    """按**线索词**把整句搬到对应字段（**只搬不改**）。

    一条句子最多认领**一个**字段（先到先得，按 `_SENT_HINTS` 的顺序）；
    ⛔ **已经在别的字段里出现过的句子不重复搬**（否则同一句会既在 `background`、又在 `personality`，
      提示词里就重了 —— 判据里有"不许重复"这条）。
    ⛔ 搬过去的必须与原文**逐字相同**（去首尾空白后）—— 这是"可判定"的关键：
      判据会逐句在 `_raw` 里找回，找不到就说明实现偷偷改写了内容（那是内容活，不该由代码做）。
    """
    if not text:
        return
    _seen = set()
    for _k in FIELDS:
        for _ln in str(out.get(_k) or "").splitlines():
            if _ln.strip():
                _seen.add(_ln.strip())
    for raw_sent in _SENT_SPLIT.split(text):
        s = raw_sent.strip()
        if len(s) < 6 or s.startswith("#") or s in _seen:
            continue
        for field, keys in _SENT_HINTS:
            if any(k in s for k in keys):
                out[field] = (out[field] + "\n" + s).strip() if out[field] else s
                hits[field] = int(hits.get(field, 0)) + 1
                _seen.add(s)
                break



def _as_text(v) -> str:
    return str(v or "").strip()


def _split_sections(text: str) -> list:
    """把整段按 markdown 小标题切成 `[(标题, 正文)]`（没有小标题 ⇒ 一整块、标题为空）。"""
    lines = str(text or "").splitlines()
    out, cur_h, buf = [], "", []
    for ln in lines:
        m = re.match(r"^\s*#{2,4}\s*(.+?)\s*$", ln)
        if m:
            if buf or cur_h:
                out.append((cur_h, "\n".join(buf).strip()))
            cur_h, buf = m.group(1), []
        else:
            buf.append(ln)
    if buf or cur_h:
        out.append((cur_h, "\n".join(buf).strip()))
    return [(h, b) for h, b in out if b or h]


def to_fields(card) -> dict:
    """把一张卡（`{name, text}` 或已经是七字段的 dict）投影成七字段 ＋ `examples` ＋ 原文。

    ⛔ **一个字都不丢**：没被任何小标题认领的正文，统一落到 `background`；
      同时保留 `_raw`（原整段）供"导出/回看"用。
    """
    if isinstance(card, dict) and all(k in card for k in ("name",)):
        name = _as_text(card.get("name"))
    else:
        name = ""
    raw = ""
    if isinstance(card, str):
        raw = card
    elif isinstance(card, dict):
        raw = _as_text(card.get("text"))

    out = {k: "" for k in FIELDS}
    out["examples"] = ""
    out["disclosure"] = _as_text(card.get("disclosure")) if isinstance(card, dict) else ""
    out["name"] = name or (_as_text(card.get("name")) if isinstance(card, dict) else "")
    out["_raw"] = raw
    out["_excerpt"] = {}

    # 已经是结构化七字段的：直接采纳（缺的留空，**不猜**）
    if isinstance(card, dict) and any(_as_text(card.get(k)) for k in FIELDS):
        for k in FIELDS + ("examples",):
            if _as_text(card.get(k)):
                out[k] = _as_text(card.get(k))
        _rest = [v for v in out.values() if v]
        if out["_raw"] and out["_raw"] not in _rest:
            out["background"] = (out["background"] + "\n\n" + out["_raw"]).strip()
        return out

    # 从整段里按小标题尽力抽
    leftover = []
    for head, body in _split_sections(raw):
        hit = ""
        for key, field in _HEAD_MAP:
            if key in head:
                hit = field
                break
        if hit:
            out[hit] = (out[hit] + "\n\n" + body).strip() if out[hit] else body
        else:
            leftover.append((("## " + head + "\n") if head else "") + body)
    if leftover:
        _tail = "\n\n".join(leftover).strip()
        out["background"] = (out["background"] + "\n\n" + _tail).strip() if out["background"] else _tail
    # ⭐ 再从**整段原文**里按线索词捞整句（不只是 leftover）：有些卡把"性格/说话风格"写进了
    #   `## 角色介绍` 那种大段里 ⇒ 只捞 leftover 会漏。已在某字段出现的句子会被跳过（不重复）。
    _harvest_sentences(raw, out, out["_excerpt"])
    return out


def missing_fields(fields: dict, *, strict: bool = False) -> list:
    """还没写的字段（**引导顺序**输出）；`strict=True` 时连"太短"也算缺（一句敷衍不算写完）。"""
    lack = []
    for k in GUIDE_ORDER:
        v = _as_text((fields or {}).get(k))
        if not v or (strict and len(v) < 12):
            lack.append(k)
    return lack


def _persona_cfg(cfg=None) -> dict:
    try:
        if cfg is None:
            from .config import get_config
            cfg = get_config()
        return dict((cfg or {}).get("persona") or {})
    except Exception:  # noqa: BLE001
        return {}


def disclosure_text(name: str = "") -> str:
    """**AI 身份披露段**（第 108 项）—— 扮演真人/已有人物时**必须**带上，且**不可关闭**。

    ⭐ 用途＝**给用户看**（尽提醒义务），**不进模型提示词**（见模块头与 `render_prompt`）。
    界面/导出件展示这一段，让用户知道"这是在扮演"；模型侧不念它（改走轻量合规行）。
    """
    who = ("「%s」" % _as_text(name)) if _as_text(name) else "这个角色"
    return ("【AI 身份披露（不可省略）】我在群里是以 AI 身份出现的；%s 是**扮演**，"
            "不是本人。被直接问到时如实说明我是 AI；引用资料时写明来源，"
            "并区分「有据」与「我的推断」。编造本人的话（名言、承诺、私事）**不做**。" % who)


def disclosure_for_user(card_or_fields) -> str:
    """取一张卡的**披露段原文**（给界面/导出展示用；**不进模型上下文**）。

    真人卡没写 ⇒ 回落 `disclosure_text()` 的默认文案；自创卡没写 ⇒ 返回空串。
    """
    try:
        f = card_or_fields if isinstance(card_or_fields, dict) else {}
        mode = str(f.get("_mode") or f.get("mode") or "").strip().lower()
        d = _as_text(f.get("disclosure"))
        if d:
            return d
        if mode == "real":
            return disclosure_text(_as_text(f.get("name")))
    except Exception:  # noqa: BLE001
        pass
    return ""


def render_prompt(card_or_fields, *, mode: str = "invented", round_no: int = 0, cfg=None) -> str:
    """按报告 4.2/4.3 的**分层注入**渲染人设提示词。

    分层与顺序（越靠前影响力越大 —— 报告 2.1）：
      ① 身份（背景）→ ② 性格 → ③ 语言 → ④ 动机 → ⑤ 价值观 → ⑥ 规则 → ⑦ 局限
      → **示例对话（独立一层）** → **轻量合规行（真人扮演时）** → **防漂移提醒（每 N 轮）**

    ⭐ **披露段不再进模型上下文**（2026-10-10 用户口径）：披露段是**写给用户看**的
      （`disclosure_for_user()` / 卡里 `disclosure` 字段），**不拼进提示词** ——
      整段念会削弱扮演；模型侧的合规改走一行**轻量指令**（被直接问到时才承认）。

    参数
      `mode`     —— `invented`（自创角色）/ `real`（真人或已有人物）。真实体只影响
                    **是否加轻量合规行**；不管哪种模式，**都不注入披露段原文**。
      `round_no` —— 当前对话轮次；`persona.drift_remind_every`（默认 12）轮注入一次轻提醒。
    """
    f = to_fields(card_or_fields) if not isinstance(card_or_fields, dict) or "text" in (card_or_fields or {}) \
        else dict(card_or_fields)
    for k in FIELDS + ("examples", "disclosure", "name"):
        f.setdefault(k, "")
    c = _persona_cfg(cfg)
    lines = []
    nm = _as_text(f.get("name"))
    if nm:
        lines.append("【你是】%s" % nm)
    for k in FIELDS:
        v = _as_text(f.get(k))
        if v:
            lines.append("【%s】\n%s" % (FIELD_CN[k], v))
    ex = _as_text(f.get("examples"))
    if ex:
        lines.append("【示例对话（照这个语气，不要照抄内容）】\n%s" % ex)
    # ⭐ 合规改走**轻量一行**：扮演真人时加"被问到时如实说"；**不注入披露段原文**
    #   （原文留给用户看；整段念进模型会让它反复自曝身份、把扮演带垮）。
    if str(mode).lower() == "real":
        lines.append("【底线】有人直接问你是不是 AI、或问你是不是%s本人时，如实说明你是 AI；"
                     "平时别主动提这茬，像%s那样说话就行。" % (nm or "那位", nm or "本人"))
    # 防漂移：每 N 轮一次（`0` ⇒ 关；正常轮次**不注入**）
    try:
        every = int(c.get("drift_remind_every") if c.get("drift_remind_every") is not None else 12)
    except Exception:  # noqa: BLE001
        every = 12
    if every > 0 and int(round_no or 0) > 0 and int(round_no) % every == 0:
        lines.append("【提醒（每 %d 轮一次）】你是「%s」；别用助手口吻、别总结、别自称模型。"
                     "出戏了就用你自己的语气把话拉回来。" % (every, nm or "这个角色"))
    return "\n\n".join(lines)


# ── 多槽位（第 102 项）───────────────────────────────────────────────
def slot_card(cfg=None, *, active: str = "") -> dict:
    """取**当前生效**的槽位人设 ⇒ `{active, name, text, ...}`；没有槽位 ⇒ 空 dict。

    `persona.active` 选槽位；**留空的项不覆盖**（报告口径：不逼用户一次填完）。
    """
    c = _persona_cfg(cfg)
    slots = c.get("slots") or {}
    if not isinstance(slots, dict) or not slots:
        return {}
    name = _as_text(active) or _as_text(c.get("active"))
    if not name or name not in slots:
        return {}
    v = slots.get(name)
    if not isinstance(v, dict):
        return {}
    out = {k: _as_text(s) for k, s in v.items() if _as_text(s)}
    out["slot"] = name
    out.setdefault("name", _as_text(v.get("bot_name")) or name)
    return out


# ── 导出 / 导入（第 109 项）────────────────────────────────────────────
def export_card(card_or_fields, *, pid: str = "") -> dict:
    """导出一张人设卡 ⇒ 带 **schema 版本号**的 JSON 友好 dict（可直接发给别人）。"""
    f = to_fields(card_or_fields)
    body = {k: _as_text(f.get(k)) for k in FIELDS}
    return {"schema": CARD_SCHEMA, "kind": "persona_card", "id": _as_text(pid),
            "name": _as_text(f.get("name")), "mode": _as_text(f.get("_mode")) or "invented",
            "fields": body, "examples": _as_text(f.get("examples")),
            "disclosure": _as_text(f.get("disclosure"))}


def import_card(data, *, require_disclosure_for_real: bool = True) -> dict:
    """导入一张人设卡 ⇒ 七字段 dict。**版本不匹配 ⇒ 抛 ValueError**（明确报错，不静默错读）。

    ⛔ 真人/已有人物（`mode="real"`）**不带披露段 ⇒ 拒收**（第 108 项：披露槽位不可删）。
    """
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict):
        raise ValueError("人设卡不是 JSON 对象")
    ver = data.get("schema")
    if ver is None:
        raise ValueError("人设卡没有 schema 版本号（旧包/手改过的文件）⇒ 不敢猜，请用本版重新导出")
    try:
        ver = int(ver)
    except Exception:  # noqa: BLE001
        raise ValueError("人设卡 schema 版本号不是整数：%r" % (data.get("schema"),))
    if ver != CARD_SCHEMA:
        raise ValueError("人设卡 schema 版本不匹配（文件 %s / 本版 %s）⇒ "
                         "请用对应版本导出，或先升级该文件" % (ver, CARD_SCHEMA))
    fields = data.get("fields") or {}
    if not isinstance(fields, dict):
        raise ValueError("人设卡 fields 不是对象")
    out = {k: _as_text(fields.get(k)) for k in FIELDS}
    out["examples"] = _as_text(data.get("examples"))
    out["disclosure"] = _as_text(data.get("disclosure"))
    out["name"] = _as_text(data.get("name"))
    out["_mode"] = _as_text(data.get("mode")) or "invented"
    if require_disclosure_for_real and out["_mode"] == "real" and not out["disclosure"]:
        raise ValueError("真人/已有人物的人设卡**必须**带 AI 身份披露段（第 108 项，不可删）")
    return out


# ── 两版模板（第 111 项）───────────────────────────────────────────────
def templates() -> dict:
    """出厂两版模板（`assets/persona_templates/*.json`）；读不到 ⇒ 空 dict（不抛）。"""
    out = {}
    try:
        for fn in sorted(os.listdir(TEMPLATE_DIR)):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(TEMPLATE_DIR, fn), encoding="utf-8") as fh:
                d = json.load(fh)
            key = _as_text(d.get("mode")) or fn[:-5]
            out[key] = d
    except Exception:  # noqa: BLE001
        return {}
    return out


def new_from_template(mode: str = "invented") -> dict:
    """用模板新建一张卡 ⇒ 七字段 dict（模板本身要能过 `import_card`）。"""
    t = (templates() or {}).get(str(mode or "invented"))
    if not t:
        raise ValueError("没有这个模板：%s" % mode)
    return import_card(t)
