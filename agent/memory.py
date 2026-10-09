# -*- coding: utf-8 -*-
"""群友印象记忆：每个会话一个文件夹，每个群友一个以 wxid 命名的 JSON 文件。

目录结构：
  data/memory/group_<wxid>/<wxid>.json
  data/memory/group_<wxid>/_meta.json
每个成员文件：{userId, name, impressions: [{content, createdAt}], updatedAt, lastConsolidatedAt}
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from . import persist
from .config import DATA_DIR, get_config

log = logging.getLogger(__name__)

MEMORY_DIR = os.path.join(DATA_DIR, "memory")

#: 覆盖审计面（对标 mem0 的 history 表；来源与理由见 `research\对标-mem0.md` §五 L1）。
#  ⛔ 故意**不放在** `MEMORY_DIR` 下面：`_chat_keys()` 把 `MEMORY_DIR` 的每个子目录都当"一个群"
#  （跨群互通的枚举口径），往里塞一个 `_history` 就等于凭空多出一个群。
HISTORY_DIR = os.path.join(DATA_DIR, "memory_history")


#: 目录名长度上限（字符）。Windows 单个文件名的字节上限是 255，而中文/emoji 在 UTF-8 里
#  一个字占 3~4 字节 ⇒ 取 80 字符能保证最坏情况（全 4 字节）也只有 320 字节……所以再压到 60：
#  60×4=240 < 255，安全。超长的一律截断 + 挂 8 位哈希尾巴（唯一性由哈希保证，见 `_chat_dir_name`）。
_DIR_NAME_MAX = 60


def _chat_dir_name(chat_key: str) -> str:
    """会话 key → 目录名（**转义 + 定长 + 短哈希**）。

    ⛔ 两代缺陷叠在这一个函数上：
    ① 最早只做转义、不截断 ⇒ 群名一长就炸（Windows 单文件名上限 255 **字节**，中文/emoji
       一个字占 3~4 字节 ⇒ ~85 字的中文群名就让 `os.makedirs` 抛 `OSError: WinError 123`），
       记忆整个写不进去；
    ② 修① 时只在**超长**时挂哈希，短 key 只做转义 ⇒ **不同 chat_key 会撞同一个目录**：
       `group:wxid_a-b` 与 `group:wxid_a_b` 都归一成 `group_wxid_a_b` ⇒ 两个群的印象/记忆
       写进同一份文件（用户视角＝"两个群的记忆串了"，与 `store.chat_file()` 当年那个
       "两个会话共用一个档案、互相覆盖"是同一个病）。
    ⇒ 现在**一律**挂 `_key_hash(chat_key)` 的 8 位尾巴（与 `store.chat_file()` 同口径）：
       归一化只用来"给人看"，唯一性由哈希保证。老目录靠 `_chat_dir()` 的向后兼容继续用。
    """
    from .keys import safe_name as _safe_name_impl
    safe = _safe_name_impl(chat_key)
    if len(safe) > _DIR_NAME_MAX:
        safe = safe[:_DIR_NAME_MAX]
    return "%s_%s" % (safe, _key_hash(chat_key))


def _chat_dir_name_legacy(chat_key: str) -> str:
    """**只有超长才挂哈希**的老命名（向后兼容用，不写新数据）。"""
    safe = re.sub(r"[^a-z0-9_]", "_", str(chat_key), flags=re.IGNORECASE)
    if len(safe) <= _DIR_NAME_MAX:
        return safe
    return "%s_%s" % (safe[:_DIR_NAME_MAX], _key_hash(chat_key))


def _key_hash(chat_key: str) -> str:
    from .keys import key_hash

    return key_hash(chat_key)


def _member_file_name(user_id: str, name: str = "") -> str:
    """成员档名。**必须定长**：wxid 通常很短，但异常输入（或含中文/emoji 的 uid）
    一样会撞上 Windows 单文件名 255 字节上限 ⇒ 超过 `_DIR_NAME_MAX` 的截断 + 挂哈希尾巴。
    """
    if str(user_id or "").strip():
        uid = str(user_id).strip()
        if re.match(r"^[\w-]+$", uid) and len(uid) <= _DIR_NAME_MAX:
            return uid + ".json"
        safe = re.sub(r"[^a-z0-9_]", "_", uid, flags=re.IGNORECASE)
        if len(safe) <= _DIR_NAME_MAX:
            return "u_" + safe + ".json"
        return "u_%s_%s.json" % (safe[:_DIR_NAME_MAX], _key_hash(uid))
    safe = re.sub(r"[^a-z0-9_\u4e00-\u9fa5]", "_", str(name or "unknown"), flags=re.IGNORECASE)[:40]
    return "_n_" + (safe or "unknown") + ".json"


def _read_json(file, fallback):
    try:
        with open(file, "r", encoding="utf-8-sig") as f:
            parsed = json.load(f)
        return parsed if isinstance(parsed, dict) else fallback
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return fallback


def _write_json(file, value):
    """原子写（`persist.atomic_write_json`：tmp 名带 pid + 随机后缀 + `os.replace`），失败**抛异常**。

    老写法共用 `<file>.tmp` 这个名字 ⇒ 并发写同一个成员档（巡检线程/多开实例）会
    **打开同一个临时文件**互相穿插出半截 JSON（审计实测 200 次并发写坏 46 个），而读侧 `_read_json`
    又静默回 fallback ⇒ 印象全没了。失败仍然抛（不吞），调用方的既有语义不变。
    """
    if not persist.atomic_write_json(file, value, indent=1):
        raise OSError("记忆落盘失败（原档未动）：%s" % file)


def _chat_dir(chat_key: str) -> str:
    """会话的记忆目录：**老目录优先沿用**，没有才用新命名建。

    ⛔ 为什么必须认老目录：目录名换新命名（一律挂哈希）之后，已有用户的旧目录若不认，
    读侧会看到空目录 ⇒ 用户视角＝"印象/记忆全没了"。所以这里先看老命名是否已经在磁盘上，
    在就继续用它（读写闭环不变、零迁移风险）；新会话才落新命名（唯一性由哈希保证，
    从此不会再出现"两个群共用一份记忆"）。
    """
    legacy = os.path.join(MEMORY_DIR, _chat_dir_name_legacy(chat_key))
    if os.path.isdir(legacy):
        return legacy
    return os.path.join(MEMORY_DIR, _chat_dir_name(chat_key))


def _history_file(chat_key: str) -> str:
    """会话的历史档（`.jsonl`）：与 `_chat_dir()` 同口径 —— 老文件优先沿用。"""
    legacy = os.path.join(HISTORY_DIR, "%s.jsonl" % _chat_dir_name_legacy(chat_key))
    if os.path.exists(legacy):
        return legacy
    return os.path.join(HISTORY_DIR, "%s.jsonl" % _chat_dir_name(chat_key))


def audit_overwrite(chat_key: str, user_id: str, before, after) -> dict:
    """整份覆盖**之前**记一笔（只追加）：丢了哪些旧印象、多了哪些新印象。

    为什么要有：`replace_member()` 是**整份覆盖**，
    唯一守门是调用方的长度比较（`len(新) <= len(旧)`）⇒ 模型整理时"顺手少写一条"就是**静默丢**：
    旧条目直接没了，没有任何痕迹、也捞不回来。这里只做**机械集合差**（逐字比对，
    不做语义猜测、不改任何存储行为）；审计本身失败绝不打断主流程。
    """
    b = [str(x) for x in (before or [])]
    a = [str(x) for x in (after or [])]
    rec = {"at": int(time.time() * 1000), "userId": str(user_id or ""),
           "before": len(b), "after": len(a),
           "dropped": [x for x in b if x not in a], "added": [x for x in a if x not in b]}
    if not (rec["dropped"] or rec["added"]):
        return rec
    try:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        with open(_history_file(chat_key), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning("记忆覆盖审计没写下去（主流程不受影响）：%s", e)
    return rec


# ── 事实抽取────────────────────────────────────────────
#   目标：把聊天里的「谁喜欢什么、约过什么事」抽成**结构化条目**，每条带**时效**：
#   待跟进类到点过期、情感与重要节点**永不淡出**（这正是"养久了也不失忆"的地基）。
#   ⛔ 本函数**只做规则解析、不调模型**（免得这里变成隐性网络调用，也与"闲时跑批"解耦）；
#     抽不出就返回空，**绝不编**。调模型做深抽取是上层的事。
FACT_TTL_DAYS = 30
_PROMISE_HINTS = ("明天", "后天", "下周", "下个月", "几点", "记得", "约好", "说好", "要交", "要发")
_FOREVER_HINTS = ("生日", "纪念日", "喜欢", "讨厌", "忌口", "过敏", "习惯")


def extract_facts(msgs) -> list:
    """从消息里抽**结构化事实** ⇒ `[{"who", "what", "kind", "expires_at"}, ...]`。

    `kind`：`followup`（**待跟进**，`FACT_TTL_DAYS` 天后过期）｜`memory`（情感/重要节点，**不设过期**）。
    `expires_at`：`"YYYY-MM-DD"`；`memory` 类为 `""`（**永不淡出**）。

    ⛔ 规则保守：**认不出就返回空列表**（不编）。判断依据只是关键词命中，宁缺毋滥 ——
      宁可漏掉一条事实，也不能凭空造一条"它以为你说过"的话。
    """
    import time as _t
    out = []
    try:
        _rows = list(msgs or [])
    except Exception: # noqa: BLE001
        return []
    for m in _rows:
        try:
            if isinstance(m, dict):
                _who = str(m.get("sender_name") or m.get("who") or "").strip()
                _txt = str(m.get("text") or m.get("content") or "")
            else:
                _who, _txt = "", str(m or "")
        except Exception: # noqa: BLE001
            continue
        _t2 = _txt.strip()
        if not _t2 or len(_t2) > 200:      # 太长的多半不是"一句事实"，跳过
            continue
        _kind = ""
        if any(h in _t2 for h in _FOREVER_HINTS):
            _kind = "memory"
        elif any(h in _t2 for h in _PROMISE_HINTS):
            _kind = "followup"
        if not _kind:
            continue
        _exp = "" if _kind == "memory" else _t.strftime(
            "%Y-%m-%d", _t.localtime(_t.time() + FACT_TTL_DAYS * 86400))
        out.append({"who": _who, "what": _t2, "kind": _kind, "expires_at": _exp})
    return out


# ── 事实深抽取（**模型层**，在规则层之上补漏）────────────────────
#   规则层只认关键词（`_FOREVER_HINTS` / `_PROMISE_HINTS`），换个说法就漏 ——
#   比如"她不吃香菜"（没命中"讨厌"）/"周五老地方见"（没命中"约好"）。
#   模型层就是补这一类的：把最近若干条消息喂给模型，让它按**固定 JSON schema**吐出事实。
#
#   ⛔ 三条纪律（缺一条都会让这个功能变成负资产）：
#     ① **不覆盖、只并集**：规则层的结果先算好，模型层只做"补"，最后按 `(who, what)` 去重合并；
#     ② **调不通 / JSON 不合法 / 字段越界 ⇒ 原样退回规则层结果**（不许因为"想抽得更细"把已有的弄丢）；
#     ③ **模型不许凭空想象**：提示词里明确"只抽消息里明确说过的"，且返回的 `what`
#        **必须能在原文里找到依据**（用字符重叠做机械校验，通不过就丢掉那一条）。
_DEEP_SCHEMA_HINT = (
    "只输出 JSON 数组，不要任何解释、不要 markdown 代码块。每个元素形如："
    '{"who":"说话人名字（拿不准就空串）","what":"事实原文","kind":"followup 或 memory",'
    '"expires_at":"YYYY-MM-DD 或空串"}。'
    "followup＝有明确时间的待办/约定；memory＝喜好、生日、忌讳、习惯这类长期的事。"
    "⛔ 只抽消息里**明确说过**的，没说的不要编；一条都抽不出就输出 []。"
)


def _deep_pick_msgs(msgs, min_chars: int, max_msgs: int) -> list:
    """挑出**值得喂给模型**的消息（去短句、按最近的若干条）。"""
    rows = []
    for m in list(msgs or []):
        try:
            if isinstance(m, dict):
                who = str(m.get("sender_name") or m.get("who") or "").strip()
                txt = str(m.get("text") or m.get("content") or "").strip()
            else:
                who, txt = "", str(m or "").strip()
        except Exception:  # noqa: BLE001
            continue
        if len(txt) < int(min_chars or 0) or len(txt) > 200:
            continue
        rows.append({"who": who, "text": txt})
    if max_msgs and max_msgs > 0:
        rows = rows[-int(max_msgs):]
    return rows


def _deep_supported(what: str, src_text: str) -> bool:
    """机械校验：抽出来的 `what` 要能在**原文**里找到依据（防模型编）。

    判据是**字符重叠**（去空白后取较长的一侧做分母）—— 宽松到允许模型改标点/补主语，
    但严到"整条凭空造"必然过不了。**分母守卫**：原文为空 ⇒ 一律不通过（无从校验就当没过）。
    """
    a = "".join(str(what or "").split())
    b = "".join(str(src_text or "").split())
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    common = sum(1 for ch in set(a) if ch in b)
    return (common / float(len(set(a)))) >= 0.6


def extract_facts_deep(msgs, llm_fn=None) -> list:
    """**规则层 + 模型层**取并集的事实抽取（第 118 项）。

    `llm_fn(prompt) -> str` 由调用方注入（默认走 `agent.llm.chat_completion`）——
    ⛔ 这样本模块**不直接依赖 llm**（省得判据里一导入就要求配 API），也让判据能传假函数。

    返回与 `extract_facts` 同构的列表。**降级纪律**：只要模型这条路有任何异常、
    或它给的东西过不了 `_deep_supported` 校验，就**退回规则层的结果**——绝不返回更少的东西。
    """
    base = extract_facts(msgs)
    cfg = {}
    try:
        cfg = dict((get_config() or {}).get("memory") or {})
    except Exception as e:  # noqa: BLE001
        log.debug("读 memory 配置失败（按默认关处理）：%s", e, exc_info=True)
    if not cfg.get("deep_extract_enabled"):
        return base
    rows = _deep_pick_msgs(msgs, cfg.get("deep_extract_min_chars") or 6,
                           cfg.get("deep_extract_max_msgs") or 40)
    if not rows:
        return base

    _lines = "\n".join("%s：%s" % (r["who"] or "某人", r["text"]) for r in rows)
    prompt = ("下面是一段群聊记录。请从中抽出**结构化事实**。\n%s\n\n%s"
              % (_DEEP_SCHEMA_HINT, _lines))
    try:
        if llm_fn is None:
            from . import llm as _llm

            def llm_fn(p):
                _model = str(cfg.get("deep_extract_model") or "").strip()
                ret = _llm.chat_completion(
                    [{"role": "user", "content": p}],
                    _models=([_model] if _model else None))
                return ((ret or {}).get("message") or {}).get("content") or ""
        raw = str(llm_fn(prompt) or "")
    except Exception as e:  # noqa: BLE001
        log.warning("事实深抽取调模型失败 ⇒ 退回规则层结果（不丢东西）：%s", e)
        return base

    items = _deep_parse(raw)
    if not items:
        return base
    # 原文全文：校验 `what` 有没有依据（**分母守卫**：原文为空 ⇒ 全不通过）
    _src_all = "\n".join(r["text"] for r in rows)
    seen = {(str(x.get("who") or ""), str(x.get("what") or "")) for x in base}
    out = list(base)
    import time as _t
    for it in items:
        who = str(it.get("who") or "").strip()
        what = str(it.get("what") or "").strip()
        if not what or not _deep_supported(what, _src_all):
            continue
        kind = "memory" if str(it.get("kind") or "").strip() == "memory" else "followup"
        key = (who, what)
        if key in seen:
            continue
        seen.add(key)
        exp = str(it.get("expires_at") or "").strip()
        if kind == "memory":
            exp = ""
        elif not re.match(r"^\d{4}-\d{2}-\d{2}$", exp):
            exp = _t.strftime("%Y-%m-%d", _t.localtime(_t.time() + FACT_TTL_DAYS * 86400))
        out.append({"who": who, "what": what, "kind": kind, "expires_at": exp})
    return out


def _deep_parse(raw: str) -> list:
    """从模型输出里**宽容地**抠出 JSON 数组（去掉 ```json 围栏、截取首个 `[` 到末个 `]`）。

    ⛔ 抠不出来 ⇒ 返回空列表（由调用方退回规则层结果），**不抛**。
    """
    s = str(raw or "").strip()
    if not s:
        return []
    if "```" in s:                      # 去掉 markdown 围栏
        s = re.sub(r"```[a-zA-Z]*", "", s).replace("```", "").strip()
    i, j = s.find("["), s.rfind("]")
    if i < 0 or j <= i:
        return []
    try:
        data = json.loads(s[i:j + 1])
    except Exception:  # noqa: BLE001
        return []
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def add_event(chat_key: str, what: str, *, who: str = "", kind: str = "note",
              emotion: str = "", long_term: bool = False, date: str = "") -> dict:
    """往「事件簿」追加一条（**append-only**，JSONL 一行一条）。返回落库的那条。

    事件簿的用途：把"这个群里发生过什么"沉淀下来（谁的事、什么时候），
    上层据此做"关系脉络总结"与"用户勾选保留/遗忘"（W3 的两半）。

    ⛔ **写不进去就抛** —— 调用方需要知道"没记上"，不许静默当成记上了。

    四个可选字段（第 114 项起）：
      · `emotion`   —— `good` / `bad` / 空。**记好也记仇**：难过的事回话时**先共情、别说教**；
      · `long_term` —— 重要的**永不过期**（不填就按 `memory.event_expire_days` 淡出）；
      · `date`      —— `YYYY-MM-DD` 或 `MM-DD` ⇒ 供"**纪念日到点**"用（跨年自动重复生效）。
      ⛔ 老行没有这几个字段 ⇒ 读的时候按"空 / False"处理（**旧数据照旧能读**，无需迁移）。
    """
    import time as _t
    rec = {"ts": _t.time(), "when": _t.strftime("%Y-%m-%d %H:%M:%S"),
           "who": str(who or ""), "kind": str(kind or "note"), "what": str(what or "").strip()}
    _em = str(emotion or "").strip().lower()
    if _em not in ("", "good", "bad"):
        _em = ""
    if _em:
        rec["emotion"] = _em
    if long_term:
        rec["long_term"] = True
    _d = str(date or "").strip()
    if _d:
        rec["date"] = _d
    if not rec["what"]:
        raise ValueError("事件内容为空")
    p = _events_file(chat_key)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def list_events(chat_key: str, limit: int = 50) -> list:
    """读事件簿（**新→旧**）。读不出来 ⇒ 空表（不抛）。"""
    out = []
    try:
        with open(_events_file(chat_key), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception: # noqa: BLE001
                    continue
    except Exception: # noqa: BLE001
        return []
    return list(reversed(out))[: max(1, int(limit or 50))]


def _events_file(chat_key: str) -> str:
    """事件簿的落盘路径（与历史审计同目录，便于一起备份/导出）。"""
    return os.path.join(_chat_dir(chat_key), "events.jsonl")


# ══════════════════════════════════════════════════════════════════════
# 时效 / 纪念日 / 待跟进（第 113·114·121·125 项）
#
# 这几件事**共用一份口径**，所以放在一起：
#   · 一条记录**要么会淡出、要么永不过期**（`long_term`）；
#     ⛔ 「淡出」不等于「删」—— 文件是 append-only，淡出只是**不再往提示词里塞**（历史留着能查）。
#   · 过期天数 `<=0` ＝ **全部不过期**（有人就想要"全都记得"）。
#   · **纪念日按 `MM-DD` 比**（`date` 允许写 `YYYY-MM-DD` 或 `MM-DD`）⇒ 跨年自动重复生效。
#   · 「待跟进」比事件多两件事：**问过几次**、**答没答**（第 125 项：问过没答才再提，答过就不提）。
# ══════════════════════════════════════════════════════════════════════

#: 没配 `memory.event_expire_days` / `followup_expire_days` 时的兜底天数
_DEFAULT_EVENT_EXPIRE_DAYS = 60
_DEFAULT_FOLLOWUP_EXPIRE_DAYS = 30
_DEFAULT_FOLLOWUP_SHOW_MAX = 3


def _mem_cfg(cfg=None) -> dict:
    """取 `memory` 段配置；拿不到 ⇒ 空 dict（调用方一律走默认值，不抛）。"""
    try:
        if cfg is None:
            from .config import get_config
            cfg = get_config()
        return dict((cfg or {}).get("memory") or {})
    except Exception:  # noqa: BLE001
        return {}


#: 「账号形状」的兜底显示名 —— 判不出昵称时**只给这个**，绝不把 uid 写进提示词
_NO_NAME = "某位群友"


def _safe_name(who: str, fallback: str = _NO_NAME) -> str:
    """把一个"可能是 wxid、也可能是昵称"的标识转成**能进提示词**的显示名。

    ⛔ 红线：**模型输入里不许出现真实账号（wxid）**，只能出现昵称。
      而 `who` 在很多链路里拿到的就是 `sender_id`（＝wxid）⇒ 这里统一挡一道：
      看起来像账号形状的（`wxid_…` / 长串纯 ascii 且无空格）⇒ **换成占位名**，不是删掉整行。
    ⛔ 别写成"删掉这个字段"：那样模型就不知道"这是另一个人"了（会当成同一个人在说）。
    """
    s = str(who or "").strip()
    if not s:
        return fallback
    low = s.lower()
    if low.startswith("wxid_") or low.startswith("wxid-"):
        return fallback
    if " " not in s and len(s) >= 12 and all(c.isascii() for c in s):
        return fallback       # 长串无空格的纯 ascii：账号/哈希形状
    return s


def _int_or(cfg: dict, key: str, default: int) -> int:
    try:
        v = cfg.get(key)
        return int(default if v is None else v)
    except Exception:  # noqa: BLE001
        return int(default)


def _mmdd(s: str) -> str:
    """从 `YYYY-MM-DD` / `M-D` / `MM-DD` 里取「月-日」（补齐两位）⇒ 取不出 ⇒ 空串。"""
    t = str(s or "").strip()
    if not t:
        return ""
    parts = [p for p in re.split(r"[^0-9]+", t) if p]
    if len(parts) >= 3:
        mm, dd = parts[-2], parts[-1]
    elif len(parts) == 2:
        mm, dd = parts[0], parts[1]
    else:
        return ""
    try:
        return "%02d-%02d" % (int(mm), int(dd))
    except Exception:  # noqa: BLE001
        return ""


def _age_days(ts, now=None) -> float:
    try:
        t = float(ts or 0)
    except Exception:  # noqa: BLE001
        return 0.0
    if t > 1e11:      # 毫秒
        t = t / 1000.0
    if t <= 0:
        return 0.0
    n = float(now if now is not None else __import__("time").time())
    return max(0.0, (n - t) / 86400.0)


def pending_events(chat_key: str, limit: int = 8, *, cfg=None, now=None) -> list:
    """还在时效内的情感/事件记录（**新→旧**）⇒ 供提示词注入。

    `long_term=True` 的**永不过期**；否则从记录时间起超过 `memory.event_expire_days`
    （**≤0 ⇒ 全部不过期**）就**不再出现**（文件里那条仍在，只是不进提示词）。
    """
    c = _mem_cfg(cfg)
    _days = _int_or(c, "event_expire_days", _DEFAULT_EVENT_EXPIRE_DAYS)
    out = []
    for rec in list_events(chat_key, limit=500):
        if not isinstance(rec, dict):
            continue
        if bool(rec.get("long_term")):
            out.append(rec)
            continue
        if _days <= 0 or _age_days(rec.get("ts"), now) <= _days:
            out.append(rec)
    return out[: max(1, int(limit or 8))]


def due_anniversaries(chat_key: str, today_md: str = "", *, limit: int = 5) -> list:
    """**今天到点的纪念日**（`date` 的月-日 ＝ 今天）⇒ 新→旧。

    ⛔ `date` 里**必须**有月日才可能命中（只写"明年结婚"这种抽不出日期的，不进这一档，
      它属于"待跟进/事件"）。跨年**自动重复生效**（只比 `MM-DD`）。
    """
    want = _mmdd(today_md) or __import__("time").strftime("%m-%d")
    out = []
    for rec in list_events(chat_key, limit=1000):
        if not isinstance(rec, dict):
            continue
        if _mmdd(rec.get("date")) == want:
            out.append(rec)
    return out[: max(1, int(limit or 5))]


def _md_plus(today_md: str, days: int) -> str:
    """把 `MM-DD` 往后推 `days` 天 ⇒ `MM-DD`（跨年自动绕回，闰年由 date 兜）。"""
    mm, dd = _mmdd(today_md).split("-")
    import datetime as _dt
    y = _dt.date.today().year
    try:
        d = _dt.date(y, int(mm), int(dd)) + _dt.timedelta(days=int(days))
    except Exception:  # noqa: BLE001  闰年 02-29 等边界 ⇒ 退回不推
        return _mmdd(today_md)
    return "%02d-%02d" % (d.month, d.day)


def upcoming_anniversaries(chat_key: str, days_ahead: int = 1, *, today_md: str = "",
                           limit: int = 5) -> list:
    """**快到了的**纪念日（第 124 项）：未来 `days_ahead` 天内到点 ⇒ 新→旧。

    为什么要它：`due_anniversaries()` 只答"**今天**是不是纪念日"—— 那时才想起来**已经晚了**
    （没时间准备、当天说"我记着呢"也显得假）。提前一天预告才用得着：
    「明天是他生日」⇒ 这一轮可以自然地提一句或先把话说好。
    ⛔ `days_ahead <= 0` ⇒ **空表**（＝关掉预告，只留当天那一档，不抛）。
    ⛔ 走 `_md_plus` 只比月日 ⇒ **跨年照样生效**（12-31 的次日是 01-01）。
    """
    try:
        n = int(days_ahead or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return []
    base = _mmdd(today_md) or __import__("time").strftime("%m-%d")
    today = _mmdd(base)
    want = set()
    for k in range(1, n + 1):
        want.add(_md_plus(today, k))
    out = []
    for rec in list_events(chat_key, limit=1000):
        if not isinstance(rec, dict):
            continue
        md = _mmdd(rec.get("date"))
        # ⛔ 排掉"今天就是"的那些 —— 它们归 `due_anniversaries()`，两档不许重叠
        #   （重叠会让同一条在提示词里出现两次，或让"今天"被说成"明天"）。
        if md and md in want and md != today:
            out.append(rec)
    return out[: max(1, int(limit or 5))]


def _followups_file(chat_key: str) -> str:
    return os.path.join(_chat_dir(chat_key), "followups.jsonl")


def _append_followup_raw(chat_key: str, rec: dict) -> dict:
    """追加一条（**append-only**）—— 写不进去就抛（调用方需要知道"没记上"）。"""
    p = _followups_file(chat_key)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def append_followup(chat_key: str, what: str, *, who: str = "", due: str = "",
                    long_term: bool = False) -> dict:
    """记一件"以后要问问"的事（短期的如「明天考试」；长期的如「明年结婚」标 `long_term`）。"""
    import time as _t
    body = str(what or "").strip()
    if not body:
        raise ValueError("待跟进内容为空")
    return _append_followup_raw(chat_key, {
        "op": "add", "ts": _t.time(), "what": body, "who": str(who or ""),
        "due": str(due or ""), "long_term": bool(long_term)})


def resolve_followup(chat_key: str, what: str, *, answered: bool = True) -> dict:
    """给一条待跟进**记状态**：`answered=True` ＝问到了答案（**以后不再提**）；
    `answered=False` ＝**这次问了、对方没答**（第 125 项：过一阵可以自然地再提一次）。

    ⛔ 用"再追加一条状态行"而不是改原行：文件保持 append-only（谁/什么时候问的留痕，
      与"记忆覆盖要留审计"同一条口径）。读的时候折叠（`_fold_followups`）。
    """
    import time as _t
    body = str(what or "").strip()
    if not body:
        raise ValueError("待跟进内容为空")
    return _append_followup_raw(chat_key, {
        "op": ("answered" if answered else "asked"), "ts": _t.time(), "what": body})


def _read_followups(chat_key: str) -> list:
    out = []
    try:
        with open(_followups_file(chat_key), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        return []
    return out


def _fold_followups(rows) -> list:
    """把 append-only 的行**折叠**成条目（`add` 建条目，`asked`/`answered` 累状态）。"""
    book, order = {}, []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        what = str(r.get("what") or "").strip()
        if not what:
            continue
        key = what
        if r.get("op") == "add":
            if key not in book:
                order.append(key)
                book[key] = {"what": what, "who": str(r.get("who") or ""),
                             "due": str(r.get("due") or ""), "long_term": bool(r.get("long_term")),
                             "ts": r.get("ts") or 0, "asked_count": 0, "last_asked": 0,
                             "answered": False, "answered_at": 0}
            continue
        it = book.get(key)
        if it is None:      # 没有 add 行（手改/截断）⇒ 不凭空造条目
            continue
        if r.get("op") == "asked":
            it["asked_count"] = int(it.get("asked_count") or 0) + 1
            it["last_asked"] = r.get("ts") or 0
        elif r.get("op") == "answered":
            it["answered"] = True
            it["answered_at"] = r.get("ts") or 0
    return [book[k] for k in order]


def pending_followups(chat_key: str, user_ids=None, *, limit=None, cfg=None, now=None) -> list:
    """**该问还没问成**的待跟进（新→旧）⇒ 供提示词注入 / 主动私聊挑话题。

    三条筛法：
      · **答过的** ⇒ 不再出现（第 125 项：「对方答过 ⇒ 不再提」）；
      · **过期**的 ⇒ 不再出现（`memory.followup_expire_days`，**≤0 ⇒ 全部不过期**；
        `long_term=True` 的**永不过期** —— 「真人两三年后都记得」）；
      · `user_ids` 给了 ⇒ 只要这些人的（`who` 匹配）。
    `limit` 不给 ⇒ 用 `memory.followup_show_max`（防提示词被塞爆）。
    """
    c = _mem_cfg(cfg)
    days = _int_or(c, "followup_expire_days", _DEFAULT_FOLLOWUP_EXPIRE_DAYS)
    if limit is None:
        limit = _int_or(c, "followup_show_max", _DEFAULT_FOLLOWUP_SHOW_MAX)
    want = {str(x) for x in (user_ids or []) if str(x or "").strip()}
    out = []
    for it in _fold_followups(_read_followups(chat_key)):
        if it.get("answered"):
            continue
        if not bool(it.get("long_term")) and days > 0 and _age_days(it.get("ts"), now) > days:
            continue
        if want and str(it.get("who") or "") and str(it.get("who")) not in want:
            continue
        out.append(it)
    out.sort(key=lambda x: -(x.get("ts") or 0))
    return out[: max(0, int(limit or 0))] if limit else out


def proactive_seeds(chat_key: str, user_ids=None, *, cfg=None, now=None) -> dict:
    """**主动/自然提起要用的数据源**（第 121 项）⇒ `{followups, anniversaries, events}`。

    为什么要有这一个入口：以前这三样各有各的读法，谁想用谁自己拼（于是没人拼）。
    ⇒ 收成一个函数：**到点纪念日** ＋ **该问还没问的** ＋ **还在时效内的事件**，
    提示词注入与（将来的）主动私聊都从这里取（第 124 项的"纪念日提醒"也接这里）。
    """
    # 第 124 项：提前几天预告（`memory.anniv_ahead_days`，默认 **1**；`<=0` ⇒ 不预告，
    #   只留"当天到点"那一档）。⛔ 走 `_mem_cfg()`：调用方传进来的可能是**整个配置**。
    try:
        _n_ahead = int(_mem_cfg(cfg).get("anniv_ahead_days")
                       if _mem_cfg(cfg).get("anniv_ahead_days") is not None else 1)
    except Exception:  # noqa: BLE001
        _n_ahead = 1
    _today_md = __import__("time").strftime("%m-%d")
    return {"followups": pending_followups(chat_key, user_ids, cfg=cfg, now=now),
            "anniversaries": due_anniversaries(chat_key),
            # 第 124 项：**提前预告**（默认提前 1 天）—— 当天才想起来已经晚了一步。
            #   ⛔ 与 `anniversaries` 严格不重叠（`upcoming_anniversaries` 排掉了今天）。
            "upcoming": upcoming_anniversaries(chat_key, _n_ahead, today_md=_today_md),
            "events": pending_events(chat_key, limit=6, cfg=cfg, now=now)}


def seeds_for_prompt(chat_key: str, user_ids=None, *, cfg=None, now=None) -> str:
    """把 `proactive_seeds()` 渲染成**一段提示词**（没有内容 ⇒ 空串，绝不硬凑）。"""
    s = proactive_seeds(chat_key, user_ids, cfg=cfg, now=now)
    lines = []
    for a in s.get("anniversaries") or []:
        lines.append("· 今天是「%s」（%s 的事）——要提就自然提一句，别硬念日期"
                     % (str(a.get("what") or "").strip()[:60], _safe_name(a.get("who"), "某人")))
    # 第 124 项：**快到了**（默认明天）—— 当天才想起来已经晚了：提前给一句"可以先准备"。
    for a in s.get("upcoming") or []:
        _d = _mmdd(a.get("date"))
        lines.append("· 「%s」（%s 的事）**%s** 就到 —— 想说什么可以提前一点，别等到当天才提"
                     % (str(a.get("what") or "").strip()[:60], _safe_name(a.get("who"), "某人"),
                        ("%s 那天" % _d) if _d else "快"))
    for f in s.get("followups") or []:
        _n = int(f.get("asked_count") or 0)
        _who = _safe_name(f.get("who"), "")
        _hint = ("（问过 %d 次还没答：这次换个说法自然提，别催）" % _n) if _n else "（还没问过）"
        lines.append("· 有件事该问一下：%s%s %s"
                     % (str(f.get("what") or "")[:60],
                        ("（关于 %s）" % _who) if _who else "", _hint))
    for e in s.get("events") or []:
        _em = {"good": "（他高兴的事）", "bad": "（他难过的事，先共情、别说教）"}.get(
            str(e.get("emotion") or ""), "")
        lines.append("· 记着：%s %s" % (str(e.get("what") or "")[:60], _em))
    if not lines:
        return ""
    return "【你记得的事】\n" + "\n".join(lines[:10])


# ── 空窗延续（第 127 项）──────────────────────────────────────────
#   问题：隔一天以上重新聊，机器人**像失忆重启** —— 明明记忆里有"上次聊过什么"，
#         提示词却不说"这是隔了一阵子重新开口"，模型自然就当成全新对话来答。
#   ⛔ 判据是**"距上一次非本轮互动有多久"**（纯读库、零 token），不是"距上一条消息"——
#     后者在本轮触发批里恒为 0，永远测不出空窗。
#   ⛔ 拿不到时间（没有库 / 读不出）⇒ **返回空串**（宁可不说，也不要编一个"好久不见"）。

def gap_hint(chat_key: str, store=None, exclude_ids=None, *, cfg=None, now=None) -> str:
    """隔久了重新开口 ⇒ 给模型一段"**别像失忆重启**"的提示（不需要就回空串）。

    取的是"**本轮之前**最后一次非自己的发言"距现在的小时数（要排掉本轮触发批，
    否则本轮那几条正是"现在要答的"，一算就恒为 0）。阈值＝`memory.gap_hours`
    （**<=0 ⇒ 关**，一个字都不注入）。
    """
    if store is None:
        return ""
    try:
        # ⛔ 走 `_mem_cfg()` 而不是直接读顶层：调用方传进来的可能是**整个配置**
        #   （`prompt.build_user_prompt` 传的就是 `get_config()`）⇒ 直接读顶层 key **永远读不到**。
        _c = _mem_cfg(cfg)
        _hrs = float(_c.get("gap_hours") if _c.get("gap_hours") is not None else 20)
    except Exception:  # noqa: BLE001
        _hrs = 20.0
    if _hrs <= 0:
        return ""
    try:
        import time as _t
        _now_ms = int(float(now) * 1000) if now else int(_t.time() * 1000)
        msgs = store.recent(chat_key, limit=200)
        ex = {str(x) for x in (exclude_ids or [])}
        last = None
        for m in reversed(list(msgs or [])):
            if m.get("self") or str(m.get("id")) in ex:
                continue
            last = m
            break
        if not last:
            return ""
        _ts = int(last.get("ts") or 0)
        if _ts <= 0:
            return ""
        hours = (_now_ms - _ts) / 3600000.0
        if hours < _hrs:
            return ""
    except Exception as e:  # noqa: BLE001
        log.debug("算空窗时长失败（按「不算空窗」处理）：%s", e, exc_info=True)
        return ""
    # 说人话的时长（与 `_rel_time` 同口径，但这里要的是"多久没聊"的粗粒度）
    if hours < 48:
        _ago = "差不多一天"
    elif hours < 24 * 7:
        _ago = "%d 天" % int(round(hours / 24.0))
    elif hours < 24 * 30:
        _ago = "一两周"
    else:
        _ago = "挺长一段时间"
    _who = _safe_name(last.get("sender_name"), "对方")
    _txt = " ".join(str(last.get("text") or "").split())[:40]
    _tail = "上次 TA 说的是「%s」" % _txt if _txt else "上次 TA 发的是图片/表情"
    return ("【隔了有一阵子】距上次聊天已经过了 %s（%s 最后开口：%s）。\n"
            "· 这次重新开口要**自然接上次的话头**，别像第一次见面那样从头寒暄；\n"
            "· 但也**别提「好久不见」**这种把间隔说破的话 —— 就像昨天刚聊过一样接着聊；\n"
            "· 有【你记得的事】里还没问成的，就在这一轮自然带一句。"
            % (_ago, _who, _tail))


# ── 看脸色 / 情绪感知（第 128 项）──────────────────────────────────
#   问题：机器人**不看脸色** —— 对方在难过，它照样给建议；对方烦着，它照样长篇大论；
#         对方只回一个「哦」，它还要凑三段话。根因是提示词里**没有任何一处描述对方当下的状态**。
#   数据源＝**对方最近几条消息本身**（`store.recent`，纯读库、零 token、零出网），
#         不看表情图片（那需要 OCR/视觉，代价大且不准）：只按**文字形状**判三档语气。
#   ⛔ 判不出 / 没数据 ⇒ **返回空串**（宁可不说，也不要瞎猜一个"他很难过"）——
#     猜错比不说更糟：模型会照着一个不存在的情绪去演。
#   ⛔ 三档之间**不许同时成立**（按优先级取一档），否则两个相反的指令会一起进提示词。

#: 低落/难过的字面线索（对方自己说的"我现在不好"）
_TONE_LOW_HINTS = (
    "难过", "不开心", "心情不好", "郁闷", "沮丧", "崩溃", "想哭", "哭了", "委屈", "难受",
    "压力", "焦虑", "烦死", "好累", "累死", "疲惫", "失眠", "失恋", "分手", "被骂",
    "唉", "哎", "算了吧", "没意思", "不想说", "别问了",
)
#: 烦躁/忙的字面线索（"现在别烦我"）
_TONE_BUSY_HINTS = (
    "忙", "赶时间", "赶工", "开会", "在干活", "加班", "赶due", "顾不上", "等会再说",
    "待会再说", "晚点说", "回头说", "先这样", "别烦", "没空", "没时间",
)
#: 「敷衍」的形状线索：短到几乎没信息量的回复
_TONE_DRY_TEXTS = ("哦", "嗯", "啊", "哦哦", "嗯嗯", "行", "好", "好的", "呵", "哈哈",
                   "。。。", "……", "?", "？", "!", "！", "额", "呃")


def _tone_of(msgs) -> str:
    """按对方最近几条消息判一档语气 ⇒ `low` / `busy` / `dry` / `""`（判不出）。

    ⛔ **只吃非自己的消息**（自己发的不能当"脸色"）；⛔ 只取最近 3 条有字的。
    ⛔ 顺序口径与 `ChatStore.recent()` 一致：**入参是旧→新**，所以取尾部。
    """
    texts = []
    for m in list(msgs or []):
        if m.get("self"):
            continue
        t = " ".join(str(m.get("text") or "").split())
        if t:
            texts.append(t)
        if len(texts) >= 3:
            break
    # ⚠️ `recent()` 返回旧→新 ⇒ 取**最后** 3 条才是"最近"。若调用方给的是新→旧，
    #   这里也只会取到较旧的三条 —— 仍能判出语气（只是略钝），不会判错档。
    texts = texts[-3:]
    if not texts:
        return ""
    joined = " ".join(texts)
    if any(h in joined for h in _TONE_LOW_HINTS):
        return "low"
    if any(h in joined for h in _TONE_BUSY_HINTS):
        return "busy"
    # 「敷衍」＝**最近一条**很干且**总共很短**（多说几句就不算敷衍了）
    last = texts[-1]
    if last in _TONE_DRY_TEXTS and sum(len(x) for x in texts) <= 8:
        return "dry"
    return ""


def tone_hint(chat_key: str = "", store=None, exclude_ids=None, *, cfg=None, now=None) -> str:
    """看脸色（第 128 项）：给对方当前语气配一段"回话注意"（不需要就空串）。

    分三档，**互斥**（按 `low` → `busy` → `dry` 优先级取一档）：
      · `low`  —— 先共情安慰，**别急着给建议**、别讲道理；
      · `busy` —— **简短**，一两句就够，别追问、别展开；
      · `dry`  —— 对方在敷衍 ⇒ **别长篇大论硬凑**，也**别追问"怎么了"**。
    阈值/词表都在本模块（`_TONE_*`）—— ⛔ 不做配置项：这是**判定口径**，交给用户调只会调坏
    （与 `gap_hours` 不同，那个是"多久算隔久了"的主观阈值，这个是"什么字算难过"的客观词表）。
    """
    if store is None:
        return ""
    try:
        msgs = store.recent(chat_key, limit=30)
    except Exception as e:  # noqa: BLE001
        log.debug("取最近消息判语气失败（按「判不出」处理）：%s", e, exc_info=True)
        return ""
    try:
        ex = {str(x) for x in (exclude_ids or [])}
        if ex:
            msgs = [m for m in (msgs or []) if str(m.get("id")) not in ex]
        tone = _tone_of(msgs)
    except Exception as e:  # noqa: BLE001
        log.debug("判语气失败（按「判不出」处理）：%s", e, exc_info=True)
        return ""
    if tone == "low":
        return ("【对方现在的状态】他这几句读着**情绪低落**。\n"
                "· 先**接住情绪**（一句共情、顺着他说），**别急着安慰式说教、别讲道理、别给建议**；\n"
                "· 话别多 —— 这一轮**两三句以内**，重点是他被听见了；\n"
                "· 他要是不想细说，就别追问。")
    if tone == "busy":
        return ("【对方现在的状态】他这几句读着**在忙、有点烦**。\n"
                "· **简短**：一两句、说重点，**别展开、别追问、别连着发好几条**；\n"
                "· 能一句话办完的就一句话，剩下的等他空下来再说。")
    if tone == "dry":
        return ("【对方现在的状态】他最近只回了很短的一句（像在敷衍）。\n"
                "· ⛔ **别长篇大论硬凑**：这一轮**就一两句**，别把气氛硬撑起来；\n"
                "· ⛔ **别追问「怎么了」「你是不是不高兴」**（那会把敷衍变成压力）；\n"
                "· 他要是不接，就自然停住。")
    return ""


# ── 亲近度 / 活跃时段（第 115·116 项）──────────────────────────────
#   数据源＝成员档里那串印象记录（`impressions: [{content, createdAt(ms)}]`），
#   它**本来就在落盘**（`_append_raw`）⇒ 这两个量不需要新采集，算出来即可。
#   ⛔ 两者都**只读**、**不抛**（拿不到数据 ⇒ 返回 0 / 空表，不许编）。

def _member_impressions(chat_key: str, user_id: str = "", name: str = "") -> list:
    """该成员在**这个群**里的印象记录（`createdAt` 已归一成**秒**；读不出 ⇒ 空表）。"""
    out = []
    try:
        d = _chat_dir(chat_key)
        want_id, want_nm = str(user_id or ""), str(name or "")
        for fn in os.listdir(d):
            if not fn.endswith(".json") or fn == "_meta.json":
                continue
            raw = _read_json(os.path.join(d, fn), None)
            if not isinstance(raw, dict):
                continue
            if want_id and str(raw.get("userId") or "") != want_id:
                continue
            if not want_id and want_nm and str(raw.get("name") or "") != want_nm:
                continue
            for it in raw.get("impressions") or []:
                try:
                    ts = float(it.get("createdAt") or 0)
                except Exception:  # noqa: BLE001
                    continue
                if ts > 1e11:
                    ts = ts / 1000.0
                if ts > 0:
                    out.append(ts)
    except Exception:  # noqa: BLE001
        return []
    return sorted(out)


def closeness(chat_key: str, user_id: str = "", *, name: str = "", now=None,
              msgs_for_close: int = 50, half_life_days: float = 7.0) -> float:
    """**亲近度** 0~1 ＝ `频率×0.6 + 最近活跃衰减×0.4`（第 115 项）。

    · 频率：印象条数 / `msgs_for_close`（默认 50 条算"熟"，**封顶 1**）；
    · 最近活跃：`exp(-距今天数 / half_life_days)`（半衰期 7 天）。
    ⛔ 没有记录 ⇒ **0.0**（不当成"不熟但也没疏远"的中位数去编）。
    """
    ts = _member_impressions(chat_key, user_id, name)
    if not ts:
        return 0.0
    n = float(len(ts))
    freq = min(1.0, n / float(max(1, int(msgs_for_close))))
    last_age = _age_days(ts[-1], now)
    fresh = 2.718281828459045 ** (-(last_age / max(0.1, float(half_life_days))))
    try:
        v = float(freq) * 0.6 + float(fresh) * 0.4
    except Exception:  # noqa: BLE001
        return 0.0
    return round(max(0.0, min(1.0, v)), 4)


def active_hours(chat_key: str, user_id: str = "", *, name: str = "", now=None) -> list:
    """**活跃时段直方图**（第 116 项）⇒ `[0..23]` 每个小时的出现次数（本地时区）。

    用途：主动私聊"挑对方常在的时段"（第 122 项的 `time_aware`）。
    ⛔ 读不到 ⇒ 全 0（**不编**）；这里是"什么时候说过话"的**原始计数**，
      要不要按样本量归一由调用方决定（判据只认"晚上活跃的样本 ⇒ 晚上计数高"）。
    """
    buckets = [0] * 24
    for ts in _member_impressions(chat_key, user_id, name):
        try:
            buckets[int(__import__("time").localtime(ts).tm_hour) % 24] += 1
        except Exception:  # noqa: BLE001
            continue
    return buckets


def busiest_hours(chat_key: str, user_id: str = "", *, name: str = "", n: int = 3) -> list:
    """最活跃的几个小时（**降序**，并列时取早的）⇒ 主动排程直接可用。"""
    b = active_hours(chat_key, user_id, name=name)
    pairs = sorted(range(24), key=lambda h: (-b[h], h))
    return [h for h in pairs[: max(1, int(n or 3))] if b[h] > 0]


def _fold_same_name(rows: list) -> list:
    """折叠「同名两套键」（第 117 项）：**按 wxid 的档** vs **按名字的 `_n_` 档**。

    为什么会两套：`_n_` 那一档来自"关机总结"这类**拿不到精确 userId** 的路径
    ⇒ 同一个人在两处各留一份档案 ⇒ 面板上**显示两行**。

    ⛔ **只在"分得清是同一个人"时才折**（与「『唯一』这类最强结论要配最强证据」同一条口径）：
      · 名字在整表里**只出现两次**（一条有 wxid、一条没有）⇒ 折成一条（**保留 wxid 那条**，
        印象**并集**去重、更新时间取较新）；
      · 名字出现**三次以上**（可能真是两个同名的人）⇒ **不折**，只给这些行标
        `ambiguous_name=True` —— **宁可多显示一行，也不把两个人的记忆串到一起**。
    """
    by_name = {}
    for r in rows or []:
        nm = str(r.get("name") or "").strip()
        if nm:
            by_name.setdefault(nm, []).append(r)
    drop = set()
    for nm, group in by_name.items():
        if len(group) != 2:
            continue
        with_id = [r for r in group if str(r.get("userId") or "")]
        no_id = [r for r in group if not str(r.get("userId") or "")]
        if len(with_id) != 1 or len(no_id) != 1:
            continue
        keep, merge = with_id[0], no_id[0]
        seen = {str(e.get("content") or "") for e in (keep.get("impressions") or [])}
        for e in (merge.get("impressions") or []):
            c = str(e.get("content") or "")
            if c and c not in seen:
                keep.setdefault("impressions", []).append(e)
                seen.add(c)
        keep["updatedAt"] = max(int(keep.get("updatedAt") or 0), int(merge.get("updatedAt") or 0))
        keep["folded_from_name_key"] = True
        drop.add(id(merge))
    out = []
    for r in rows or []:
        if id(r) in drop:
            continue
        out.append(r)
    # ⛔ 这一步**与"折没折成"无关**：折过之后如果同一个名字还剩多行 ⇒ 如实标"分不清"，
    #   不装作已经干净（旧写法把它放在 `if not drop: return` 后面 ⇒ **一行都没折时它永远不跑**）。
    _cnt = {}
    for r in out:
        _cnt[str(r.get("name") or "")] = _cnt.get(str(r.get("name") or ""), 0) + 1
    for r in out:
        if _cnt.get(str(r.get("name") or ""), 0) > 1:
            r["ambiguous_name"] = True
    return out


# ── 热词统计────────────────────────────────────────────
#   群梗词典的第一步就是"最近大家老在说什么词"。
#   ⛔ **不引分词库**（装不装 jieba 都能跑）：按**连续的汉字/字母数字串**切片，
#      再用停用词表滤掉"的/了/是"这类噪声 ⇒ 零依赖、可判定、结果可复算。
_STOP = {"的", "了", "是", "在", "我", "你", "他", "她", "它", "们", "和", "就", "都", "也",
         "有", "没有", "这个", "那个", "什么", "怎么", "为什么", "但是", "然后", "所以",
         "一下", "一个", "可以", "不是", "还是", "因为", "如果", "可能", "自己", "觉得"}


def hot_words(chat_key: str, limit: int = 30, min_len: int = 2, top_scan: int = 2000) -> list:
    """这个群的**热词** ⇒ `[(词, 次数), ...]`（降序）。

    `min_len`：短于这个长度的词不算（默认 2 ⇒ 滤掉单个字）；`top_scan`：最多看多少条消息。
    ⛔ **样本为空 ⇒ 返回空**（不编）；结果**可复算**（同样的历史同一次序）。
    """
    import re as _re
    from collections import Counter as _C
    cnt = _C()
    try:
        rows = history(chat_key, limit=int(top_scan or 2000)) or []
    except Exception: # noqa: BLE001
        return []
    for m in rows:
        _t = str((m or {}).get("text") or (m or {}).get("content") or "")
        if not _t:
            continue
        for seg in _re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]+", _t):
            # ⛔ **不能把整段当一个词** —— 那会把「今天的考试好难啊」整句算成"热词"（实测踩过）。
            #    改用 **2–4 字滑窗**取 n-gram（零依赖、可复算）：真正高频的短串自然浮上来，
            #    再由调用方按频次取前 N。
            _L = len(seg)
            for _n in (2, 3, 4):
                if _L < _n:
                    continue
                for _i in range(_L - _n + 1):
                    _w = seg[_i:_i + _n]
                    if _w in _STOP or len(_w) < max(1, int(min_len or 2)):
                        continue
                    cnt[_w] += 1
    if not cnt:
        return []
    return cnt.most_common(max(1, int(limit or 30)))


def search(chat_key: str, keyword: str, limit: int = 20) -> list:
    """在这个群的历史里**按关键词搜**（大小写不敏感）⇒ 命中的消息列表（新→旧）。

    ⛔ 两条边界：**`keyword` 为空 ⇒ 返回空**（不许"空串匹配所有"）；搜不到 ⇒ 空列表（**不编**）。
    """
    kw = str(keyword or "").strip().lower()
    if not kw:
        return []
    out = []
    try:
        for m in (history(chat_key, limit=2000) or []):
            _t = str((m or {}).get("text") or (m or {}).get("content") or "")
            if kw in _t.lower():
                out.append(m)
                if len(out) >= max(1, int(limit or 20)):
                    break
    except Exception: # noqa: BLE001
        return []
    return out


def history(chat_key: str, limit: int = 50) -> list:
    """读回这个群的覆盖审计（旧 → 新，最多 `limit` 条）。读不出来就返回空表，不抛。"""
    out = []
    try:
        with open(_history_file(chat_key), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception as _e:
                    log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except FileNotFoundError:
        return []
    except Exception as e:
        log.warning("记忆覆盖审计读不出来：%s", e)
        return []
    return out[-max(1, int(limit)):]


def _data_dir() -> str:
    """记忆根目录（互通池枚举用）。"""
    return MEMORY_DIR


def chat_keys_on_disk() -> list:
    """磁盘上**实际有印象数据**的会话 chat_key（不依赖消息库）。

    ⛔ 为什么要有：记忆落盘在 `data/memory/`，与消息档案库（`data/messages/`）是**两套独立存储**。
    上层若只用 `store.list_chats()` 枚举记忆页的群下拉，那么"消息库读不到 / 某群只收过消息没被
    监听落档 / 手动清过 messages 目录"时，用户**明明有印象却看不到、选不中、删不掉** —— 数据在，
    界面说"没有"。所以这里直接以记忆目录为准枚举。

    目录名是 `_chat_dir_name()` 的**单向转义**（`group:x@chatroom` → `group_x_chatroom`），
    无法反推 ⇒ 优先取 `_meta.json` 里回写的原始 `chat_key`；老档案没有这一项时退回
    「文件名归一半还原」（`group_x_chatroom` → `group:x_chatroom`），够用于展示与选择
    （`_chat_dir` 再转义回去仍是同一个目录 ⇒ 读写闭环成立）。
    """
    out = []
    seen = set()
    try:
        names = sorted(os.listdir(MEMORY_DIR))
    except OSError:
        return []
    for fn in names:
        p = os.path.join(MEMORY_DIR, fn)
        if not os.path.isdir(p) or fn in (".", ".."):
            continue
        # 这个目录里是否有真印象档（空壳目录不算一个"群"）
        has_data = False
        meta = {}
        try:
            for sub in os.listdir(p):
                if sub == "_meta.json":
                    meta = _read_json(os.path.join(p, sub), {}) or {}
                    continue
                if not sub.endswith(".json"):
                    continue
                raw = _read_json(os.path.join(p, sub), None)
                if raw and (raw.get("impressions") or raw.get("userId") or raw.get("name")):
                    has_data = True
        except OSError:
            continue
        if not has_data:
            continue
        ck = str(meta.get("chat_key") or "").strip()
        if not ck:
            # 老档案没回写 chat_key ⇒ 从目录名归半还原。⛔ 目录名可能是**截断+哈希**的
            #   （见 `_chat_dir_name`），这种还原不回来 ⇒ 如实跳过（不编一个假 key，
            #   编了会让"选中它"落到别的目录）。新写入路径都会回写，故只影响老档案。
            base = fn
            m = re.match(r"^(group|private)_(.+)_([0-9a-f]{8})$", base)
            if m and _key_hash("%s:%s" % (m.group(1), m.group(2))) == m.group(3):
                # 尾巴真是我们挂的哈希 ⇒ 原名被截断过，反推不回来
                log.debug("记忆目录 %s 是截断名且无 _meta.chat_key，跳过（不编假 key）", fn)
                continue
            m = re.match(r"^(group|private)_(.+)$", base)
            ck = "%s:%s" % (m.group(1), m.group(2)) if m else fn
        if ck and ck not in seen:
            seen.add(ck)
            out.append(ck)
    return out


def _member_file(chat_key: str, user_id: str, name: str = "") -> str:
    return os.path.join(_chat_dir(chat_key), _member_file_name(user_id, name))


def _meta_file(chat_key: str) -> str:
    return os.path.join(_chat_dir(chat_key), "_meta.json")


class MemoryStore:
    def __init__(self):
        self.cache: dict = {} # chat_key -> {user_id: member}
        self._lock = threading.Lock()

    def _share_pool(self) -> bool:
        """记忆互通开关：true=所有群共享一个记忆池（跨群可见）。"""
        try:
            return bool(get_config().get("memory", {}).get("share_across_groups") is True)
        except Exception:
            return False

    def _shared_groups(self) -> list:
        """勾选的共享群（memory.shared_groups，存群名/chat_key）；空=未勾选。"""
        try:
            return [str(x) for x in (get_config().get("memory", {}).get("shared_groups") or []) if str(x)]
        except Exception:
            return []

    def _chat_keys(self, chat_key: str) -> list:
        """互通时返回的群：勾选了 shared_groups → 只在这些群间互通（本群在内才生效）；
        否则 share_across_groups=true 全部互通；false 仅本群。

        ⛔ 全互通档原来直接把**目录名**当 key 返回（`group_x_chatroom`）—— 目录名是
        `_chat_dir_name()` 的单向转义，与真实 chat_key（`group:x@chatroom`）**不相等**；
        于是 `members()` / `remove()` 拿这个假 key 去 `_chat_dir()` 再转义一次，
        虽然大多数情况还能碰巧对回同一个目录，但凡是转义后不同的 key（含中文群名、大写 wxid、
        点号/连字符 wxid）就会**读到空 / 删错档**。⇒ 统一用 `chat_keys_on_disk()` 拿回真实
        chat_key（读 `_meta.json` 回写项 + 文件名归半还原），与 `members()` 的读取口径一致。
        """
        groups = self._shared_groups()
        if groups:
            if not self._share_pool():
                # 未开总开关但勾了群：视为"勾选群间互通"
                pass
            keys = [g for g in groups if g]
            if not keys:
                return [chat_key]
            # 本群名匹配（chat_key 或群名）尽量宽
            base = os.path.basename(chat_key)
            match = [g for g in keys if g == chat_key or g == base or chat_key.endswith(g)]
            return keys if match else [chat_key]
        if not self._share_pool():
            return [chat_key]
        keys = chat_keys_on_disk()
        return keys or [chat_key]

    def _ensure_chat(self, chat_key: str) -> dict:
        if chat_key not in self.cache:
            m = {}
            cdir = _chat_dir(chat_key)
            try:
                for fn in os.listdir(cdir):
                    if not fn.endswith(".json") or fn == "_meta.json":
                        continue
                    raw = _read_json(os.path.join(cdir, fn), None)
                    if not raw:
                        continue
                    key = str(raw.get("userId")) if raw.get("userId") else "_n_" + fn
                    m[key] = {
                        "userId": str(raw.get("userId") or ""),
                        "name": str(raw.get("name") or ""),
                        "impressions": raw.get("impressions") if isinstance(raw.get("impressions"), list) else [],
                        "updatedAt": int(raw.get("updatedAt") or 0),
                        "lastConsolidatedAt": int(raw.get("lastConsolidatedAt") or 0),
                    }
            except OSError:
                # 目录不在（首次）／被换成文件／权限被收 —— 都是"这次读不到"，按空档处理，
                # 绝不把异常抛给上层（上层只是要一份成员列表，不该因存储故障整页炸掉）。
                pass
            # 顺手把原始 chat_key 补写回 `_meta.json`：老档案（没有这一项）从此可被
            # `chat_keys_on_disk()` 精确还原成真实会话，而不是退化成转义目录名。
            if m:
                try:
                    mp = _meta_file(chat_key)
                    meta = _read_json(mp, {}) or {}
                    if not str(meta.get("chat_key") or "").strip():
                        os.makedirs(cdir, exist_ok=True)
                        _write_json(mp, {**meta, "chat_key": str(chat_key)})
                except Exception as _e:
                    log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                    pass
            self.cache[chat_key] = m
        return self.cache[chat_key]

    def _append_raw(self, chat_key: str, user_id: str, name: str, content: str, created_at=None):
        m = self._ensure_chat(chat_key)
        key = str(user_id) if user_id else "_n_" + _member_file_name("", name)
        member = m.get(key) or {"userId": str(user_id or ""), "name": str(name or ""),
                                "impressions": [], "updatedAt": 0, "lastConsolidatedAt": 0}
        entry = {"content": str(content or "")[:300], "createdAt": int(created_at or __import__("time").time() * 1000)}
        if not any(e.get("content") == entry["content"] for e in member["impressions"]):
            member["impressions"].append(entry)
        member["userId"] = str(user_id or member.get("userId") or "")
        member["name"] = str(name or member.get("name") or "")
        member["updatedAt"] = int(__import__("time").time() * 1000)
        _write_json(_member_file(chat_key, user_id, name), member)
        m[key] = member
        return entry

    def collect_member_texts(self, user_id: str, name: str = "") -> list:
        """跨所有群收集该成员的全部印象文本（真实记录，供深度提炼）。"""
        texts = []
        seen = set()
        root = _data_dir()
        try:
            for ck in os.listdir(root):
                cdir = os.path.join(root, ck)
                if not os.path.isdir(cdir):
                    continue
                for fn in os.listdir(cdir):
                    if not fn.endswith(".json") or fn == "_meta.json":
                        continue
                    try:
                        raw = _read_json(os.path.join(cdir, fn), None)
                    except Exception as _e:
                        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                        continue
                    if not raw:
                        continue
                    if str(raw.get("userId") or "") != str(user_id) and not (name and str(raw.get("name") or "") == name):
                        continue
                    for it in raw.get("impressions") or []:
                        c = str(it.get("content") or "").strip()
                        if c and len(c) >= 4 and c not in seen:
                            seen.add(c)
                            texts.append(c)
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass
        return texts

    def append(self, chat_key: str, category: str, content: str, extra: dict | None = None):
        if category != "memberImpression":
            return None
        extra = extra or {}
        user_id = str(extra.get("userId") or "").strip()
        target = str(extra.get("target") or "").strip()[:60]
        if not user_id and not target:
            return None
        return self._append_raw(chat_key, user_id, target or user_id, content)

    def query(self, chat_key: str, category: str = ""):
        if category and category != "memberImpression":
            return {category: []}
        m = self._ensure_chat(chat_key)
        out = []
        for mem in m.values():
            for e in mem["impressions"]:
                out.append({
                    "userId": str(mem.get("userId") or ""),
                    "target": str(mem.get("name") or mem.get("userId") or "某人"),
                    "content": e["content"],
                    "createdAt": e["createdAt"],
                })
        out.sort(key=lambda x: -x["createdAt"])
        return {"memberImpression": out}

    def members(self, chat_key: str):
        """列出成员档案。互通时合并所有群（同 wxid 合并印象）；隔离时仅本群。"""
        merged: dict = {}
        for key in self._chat_keys(chat_key):
            for mem in self._ensure_chat(key).values():
                if not mem["impressions"]:
                    continue
                uid = str(mem.get("userId") or "")
                ident = uid or str(mem.get("name") or "")
                if not ident:
                    continue
                if ident not in merged:
                    merged[ident] = {
                        "userId": mem.get("userId") or "",
                        # ⛔ `name` **不许**回落成 `userId`（那是 wxid）：这个 dict 会一路走到
                        #   `format_for_prompt` ⇒ 回落等于把真实账号喂给模型。没昵称就留空，
                        #   由消费方用 `_safe_name()` 兜一个占位名；要看 uid 的地方用 `userId`。
                        "name": str(mem.get("name") or ""),
                        "impressions": list(mem.get("impressions") or []),
                        "updatedAt": mem.get("updatedAt") or 0,
                        "lastConsolidatedAt": mem.get("lastConsolidatedAt") or 0,
                    }
                else:
                    merged[ident]["impressions"] = list(mem.get("impressions") or [])
                    merged[ident]["updatedAt"] = max(merged[ident]["updatedAt"], mem.get("updatedAt") or 0)
        out = [dict(v, impressions=[dict(e) for e in v["impressions"]]) for v in merged.values()]
        out.sort(key=lambda x: -(x["updatedAt"] or 0))
        return _fold_same_name(out)

    def list_members(self, chat_key: str):
        """同上（`members()` 的别名，面板/工具两处都在用）。"""
        return self.members(chat_key)

    def chat_keys(self) -> list:
        """本机有记忆的会话列表（记忆图谱等"总览"用途）。

        ⛔ 用**磁盘事实**而不是缓存：缓存只在读过之后才有，图谱要在建页首帧就能枚举。
          隔离/互通与否不影响这里 —— 图谱画的是"本机有什么"，不是一个群的视角。
        """
        return list(chat_keys_on_disk() or [])

    def remove(self, chat_key: str, category: str, user_id="", target="", content="", scope="all"):
        """删成员印象。`scope`＝**删除范围**：

        · `all` （默认）＝按 `_chat_keys()` 把**互通范围内的每一份都删掉** —— 与 `members()`
          的读取口径一致；
        · `this`＝**只删 `chat_key` 这一个群**里那一份（别的群还留着 ⇒ 合并视图里仍会显示，
          所以调用方必须把这件事**如实告诉用户**，见 `elsewhere()`）。
        """
        if category != "memberImpression":
            return False
        keys = [chat_key] if str(scope) == "this" else list(self._chat_keys(chat_key))
        removed = False
        # ⛔ （已知现象）：
        #   根因是**口径不对称** —— `members()`（列表）在"互通"时是 `for key in self._chat_keys(chat_key)`
        #   **把所有群合并**后展示的，而这里原来只删 `chat_key` **一个群**的那一份 ⇒ 同一个人在别的群
        #   （或共享池）还留着一份 ⇒ **界面上删了、一刷新又合并出来**。
        #   ⇒ 默认档必须与读取**同一口径**：按 `_chat_keys()` 把每一份都删掉（`scope="all"`）。
        for key in keys:
            m = self._ensure_chat(key)
            if not m:
                continue
            for mk, mem in list(m.items()):
                hit = False
                if user_id:
                    hit = str(mem.get("userId")) == str(user_id)
                elif target:
                    hit = str(mem.get("name") or mem.get("userId")) == str(target).strip()
                if not hit:
                    continue
                if content:
                    before = len(mem["impressions"])
                    mem["impressions"] = [e for e in mem["impressions"] if e["content"] != content]
                    removed = removed or len(mem["impressions"]) != before
                else:
                    removed = True
                    mem["impressions"] = []
                if not mem["impressions"]:
                    m.pop(mk, None)
                    try:
                        os.remove(_member_file(key, mem.get("userId"), mem.get("name")))
                    except Exception as _e:
                        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                        pass  # 清理型：静默合法
                else:
                    mem["updatedAt"] = int(__import__("time").time() * 1000)
                    _write_json(_member_file(key, mem.get("userId"), mem.get("name")), mem)
        return removed

    def elsewhere(self, chat_key: str, user_id="", target="") -> int:
        """除 `chat_key` 之外，还有几个群留着这个人的印象（给「只删本群」档做**如实提示**用）。

        为什么要它：`members()` 展示的是**互通范围内的合并视图**，所以用 `scope="this"` 只删本群时，
        界面上**那条记忆还会在**（别的群那一份还在）—— 不说清楚，用户就会以为"删了没用"（他 
        报的那条 bug 就是这个观感）。⇒ 只读计数，不做任何写入。
        """
        n = 0
        try:
            for key in self._chat_keys(chat_key):
                if key == chat_key:
                    continue
                for _mk, mem in (self._ensure_chat(key) or {}).items():
                    if not mem.get("impressions"):
                        continue
                    if user_id:
                        hit = str(mem.get("userId")) == str(user_id)
                    elif target:
                        hit = str(mem.get("name") or mem.get("userId")) == str(target).strip()
                    else:
                        hit = False
                    if hit:
                        n += 1
                        break
        except Exception:
            return 0
        return n

    def replace_member(self, chat_key: str, user_id: str, name: str, contents):
        uid = str(user_id or "").strip()
        if not uid:
            raise ValueError("userId 不能为空")
        m = self._ensure_chat(chat_key)
        old = m.get(uid) or {"userId": uid, "name": name, "impressions": [], "updatedAt": 0, "lastConsolidatedAt": 0}
        final_name = str(name or "").strip()[:60] or str(old.get("name") or "").strip() or uid
        now = int(__import__("time").time() * 1000)
        impressions = [{"content": str(s or "").strip()[:300], "createdAt": now}
                       for s in contents if str(s or "").strip()][:20]
        if old.get("impressions"):
            # 整份覆盖前先记一笔（丢了什么、多了什么）——静默丢印象正是这条链的老毛病
            audit_overwrite(chat_key, uid,
                            [str(e.get("content") or "") for e in (old.get("impressions") or [])],
                            [e["content"] for e in impressions])
        member = {"userId": uid, "name": final_name, "impressions": impressions,
                  "updatedAt": now, "lastConsolidatedAt": old.get("lastConsolidatedAt") or 0}
        _write_json(_member_file(chat_key, uid, final_name), member)
        m[uid] = member
        return member

    def overwrite_history(self, chat_key: str, limit: int = 20) -> list:
        """这个群"整份覆盖过哪些印象"的审计（旧 → 新，最多 limit 条；读不出来就是空表）。

        实现是模块函数 `history()`；单独留一个方法是因为主流程（记忆整理）与记忆页都按
        `orch.memory.xxx` 的写法调用。
        """
        return history(chat_key, limit)

    @staticmethod
    def _rel_time(ts_ms: int) -> str:
        """把时间戳说成人话（给"上次聊过"用）。"""
        try:
            import time as _t
            mins = int((_t.time() * 1000 - int(ts_ms or 0)) / 60000)
        except Exception:
            return ""
        if mins < 1:
            return "刚刚"
        if mins < 60:
            return "%d 分钟前" % mins
        if mins < 60 * 24:
            return "%d 小时前" % int(round(mins / 60.0))
        return "%d 天前" % int(round(mins / 1440.0))

    def _last_talk(self, member, store, chat_key: str, exclude_ids=None):
        """这位群友**本轮之前**的最后一次发言 ⇒ 给"上次聊过"用（纯读库，不花 token）。

        为什么要排除本轮触发批：本轮那几条正是"现在要回答的"，不能拿它当"上次"。
        找不到（第一次来 / 只发过图且无文字）就返回 None —— 不编内容。
        """
        if store is None:
            return None
        uid = str(member.get("userId") or "")
        name = str(member.get("name") or "")
        if not uid and not name:
            return None
        try:
            msgs = store.recent(chat_key, limit=200)
        except Exception:
            return None
        ex = {str(x) for x in (exclude_ids or [])}
        hit = None
        for m in reversed(list(msgs or [])):
            if m.get("self"):
                continue
            if str(m.get("id")) in ex:
                continue
            sid = str(m.get("sender_id") or "")
            snm = str(m.get("sender_name") or "")
            if (uid and sid == uid) or ((not uid) and name and snm == name):
                hit = m
                break
        if not hit:
            return None
        txt = " ".join(str(hit.get("text") or "").split())[:30]
        if not txt:
            txt = "（发过图片/表情）" if hit.get("media") else ""
        if not txt:
            return None
        return {"text": txt, "ts": int(hit.get("ts") or 0)}

    def format_for_prompt(self, chat_key: str, user_ids=None, store=None, exclude_ids=None) -> str:
        """给提示词用的一段"对群友的印象"。

        store/exclude_ids（可选）：传进来时，额外给每位群友带一行「上次聊过「…」（X 前）」——
        这条**只从消息库现读**（不花 token、不凭空编）；没传 store 时行为与以前完全一致。
        """
        notes = get_config().get("member_notes") or {}
        all_members = self.members(chat_key)
        if not all_members:
            return ""
        if user_ids:
            filt = {str(u) for u in user_ids}
            picked = [m for m in all_members if not m["userId"] or str(m["userId"]) in filt]
        else:
            picked = all_members[:15]
        if not picked:
            return ""
        lines = ["【对群友的印象】"]
        for m in picked:
            # ⛔ 显示名**只从备注/昵称来**，并且过一道 `_safe_name`（防上游某处又把 uid 塞进 name）
            who = _safe_name(notes.get(str(m["userId"])) or m.get("name"))
            for e in m["impressions"][-3:]:
                lines.append("- %s：%s" % (who, e["content"]))
            lt = self._last_talk(m, store, chat_key, exclude_ids=exclude_ids)
            if lt:
                ago = self._rel_time(lt["ts"])
                lines.append("- %s：上次聊过「%s」%s" % (who, lt["text"], ("（%s）" % ago) if ago else ""))
        return "\n".join(lines)

    # ── 时效 / 纪念日 / 待跟进 / 亲近度 / 活跃时段（第 113·114·115·116·121·125 项）──
    #   ⛔ **薄封装**：实现只有模块级那一份，这里只是让 `ctx["memory"].xxx()` 能直接调
    #     （提示词注入走的就是这个入口 —— 别在类里再写一份，"两处实现"必然漂）。

    def pending_followups(self, chat_key: str, user_ids=None, *, limit=None, cfg=None, now=None):
        return pending_followups(chat_key, user_ids, limit=limit, cfg=cfg, now=now)

    def pending_events(self, chat_key: str, limit: int = 8, *, cfg=None, now=None):
        return pending_events(chat_key, limit, cfg=cfg, now=now)

    def due_anniversaries(self, chat_key: str, today_md: str = "", *, limit: int = 5):
        return due_anniversaries(chat_key, today_md, limit=limit)

    def upcoming_anniversaries(self, chat_key: str, days_ahead: int = 1, *,
                               today_md: str = "", limit: int = 5):
        """纪念日提前预告（第 124 项）：未来 N 天内到点 ⇒ 新→旧；`<=0` ⇒ 空表。"""
        return upcoming_anniversaries(chat_key, days_ahead, today_md=today_md, limit=limit)

    def proactive_seeds(self, chat_key: str, user_ids=None, *, cfg=None, now=None):
        return proactive_seeds(chat_key, user_ids, cfg=cfg, now=now)

    def seeds_for_prompt(self, chat_key: str, user_ids=None, *, cfg=None, now=None):
        return seeds_for_prompt(chat_key, user_ids, cfg=cfg, now=now)

    def gap_hint(self, chat_key: str, store=None, exclude_ids=None, *, cfg=None, now=None):
        """空窗延续（第 127 项）：隔久了 ⇒ 一段"别像失忆重启"的提示；不需要就空串。"""
        return gap_hint(chat_key, store, exclude_ids, cfg=cfg, now=now)

    def tone_hint(self, chat_key: str = "", store=None, exclude_ids=None, *, cfg=None, now=None):
        """看脸色（第 128 项）：对方语气（低落/忙/敷衍）⇒ 一段"回话注意"；判不出就空串。"""
        return tone_hint(chat_key, store, exclude_ids, cfg=cfg, now=now)

    def closeness(self, chat_key: str, user_id: str = "", *, name: str = "", **kw):
        return closeness(chat_key, user_id, name=name, **kw)

    def active_hours(self, chat_key: str, user_id: str = "", *, name: str = ""):
        return active_hours(chat_key, user_id, name=name)

    # ── 自动整理 ─────────────────────────────────────────────────────────

    def consolidation_state(self, chat_key: str):
        m = self._ensure_chat(chat_key)
        total = 0
        last = 0
        members = []
        for mem in m.values():
            total += len(mem["impressions"])
            last = max(last, mem.get("lastConsolidatedAt") or 0)
            members.append({"userId": str(mem.get("userId") or ""), "name": str(mem.get("name") or ""),
                            "count": len(mem["impressions"]),
                            "lastConsolidatedAt": mem.get("lastConsolidatedAt") or 0})
        meta = _read_json(_meta_file(chat_key), {})
        return {"lastConsolidatedAt": meta.get("lastConsolidatedAt") or last,
                "counts": {"memberImpression": total}, "members": members}

    def mark_consolidated(self, chat_key: str, at=None, user_ids=None):
        at = int(at or __import__("time").time() * 1000)
        os.makedirs(_chat_dir(chat_key), exist_ok=True)
        prev = _read_json(_meta_file(chat_key), {})
        # 记下原始 chat_key：目录名是单向转义、无法反推，靠它才能把记忆目录对回真实会话
        _write_json(_meta_file(chat_key), {**prev, "lastConsolidatedAt": at, "chat_key": str(chat_key)})
        m = self._ensure_chat(chat_key)
        for uid in (user_ids or []):
            key = str(uid or "").strip()
            mem = m.get(key)
            if not mem:
                continue
            mem["lastConsolidatedAt"] = at
            _write_json(_member_file(chat_key, mem.get("userId"), mem.get("name")), mem)

    def clear_all(self) -> int:
        """抹掉**全部**成员印象档（所有群、含互通池），返回清掉的成员档个数。

        口径与 `chat_keys_on_disk()` 一致：**以记忆目录为准**逐个群清，不依赖消息库 ——
        否则"消息库读不到 / 只有印象没有消息"的群会被漏掉（用户以为清干净了，其实还留着）。
        删除逐个成员档；`_meta.json` 只清 `lastConsolidatedAt`（保留 `chat_key` 回写项，
        否则那份原始会话标识就没了，记忆页的群名会退化成转义目录名）。
        读不动的档**不删、跳过并计数**，绝不静默当成"清掉了"。
        """
        removed = 0
        failed = 0
        tgt = list(self.cache.keys())
        try:
            for fn in os.listdir(MEMORY_DIR):
                p = os.path.join(MEMORY_DIR, fn)
                if os.path.isdir(p) and fn not in (".", ".."):
                    tgt.append(fn)
        except OSError:
            pass
        for key in tgt:
            cdir = _chat_dir(key) if key in self.cache else os.path.join(MEMORY_DIR, key)
            try:
                files = list(os.listdir(cdir))
            except OSError:
                continue
            for fn in files:
                if not fn.endswith(".json") or fn == "_meta.json":
                    continue
                fp = os.path.join(cdir, fn)
                if _read_json(fp, None) is None:
                    # 读不动（权限/被占用/坏 JSON）⇒ 不动它，如实记账
                    failed += 1
                    continue
                try:
                    os.remove(fp)
                    removed += 1
                except OSError:
                    failed += 1
            try:
                mp = os.path.join(cdir, "_meta.json")
                meta = _read_json(mp, {})
                if meta:
                    meta.pop("lastConsolidatedAt", None)
                    _write_json(mp, meta)
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                pass
        self.cache.clear()
        if failed:
            raise OSError("有 %d 个印象档读不动、没敢删（其余 %d 个已清）" % (failed, removed))
        return removed
