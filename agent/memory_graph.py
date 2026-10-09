# -*- coding: utf-8 -*-
"""**记忆图谱**：把「谁在哪个群、记着什么事、这些人之间怎么连」画成一张可交互的图。

README 那条要的是"把印象与会话记忆画成可交互的图谱，**本机生成、零 token**"。
本模块只做**取数与构图**（后端），画布交给前端（web 用 ECharts 力导向图，Qt 用自绘）。

★ 图是从**本机已有的记忆档案**直接推出来的，不调模型、不出网：
  `data/memory/<群>/<成员>.json` 里的 `impressions[].content` 就是边上的事实来源。

节点三类（`type` 供前端上色）：
  · `chat`   —— 会话（群/私聊），重要度＝该群印象总数归一；
  · `person` —— 记过印象的成员，重要度＝印象条数 × 亲近度；
  · `topic`  —— 从印象正文里切出的高频实词（复用 `memory.hot_words` 的零依赖切词口径）。

边三类（`relation` 供前端标关系名）：
  · `在群里`   —— person → chat（该成员在这个会话有印象）；
  · `聊到`     —— person → topic（该成员的印象里出现这个词）；
  · `同群`     —— person — person（同一会话的两个成员，权重＝两人印象条数取小）。

⛔ 纪律：
  · 全本机、**零 token**（不调任何模型）；
  · 空数据 ⇒ 返回空图并**如实说"还没记下什么"**，不画一个假的骨架；
  · 隐私：节点标签只用**本机记忆里已有的名字**（没名字的成员显示占位符，不回落 wxid）。
"""
from __future__ import annotations

import logging
import os

try:  # 同包内直接 import；单测里以 agent 包导入
    from . import memory as _mem
except Exception:  # noqa: BLE001 — 顶层运行时兜底（脚本方式加载）
    import memory as _mem  # type: ignore

log = logging.getLogger(__name__)

#: 一张图最多画多少个节点（力导向图超过这个数量就没法看了）。
MAX_NODES = 120
#: 每个会话最多取几个成员（按印象条数降序）。
MAX_MEMBERS_PER_CHAT = 24
#: 每个成员最多挂几个话题词（按出现次数降序）。
MAX_TOPICS_PER_PERSON = 6
#: 至少出现几次才算一个"话题"（去噪，只冒过一次的词不成节点）。
MIN_TOPIC_HITS = 2
#: 没名字的成员占位（**不回落 wxid** —— 那是真实账号，不许出现在图上）。
_NO_NAME = "（未记名）"


def _display_name(mem: dict) -> str:
    """成员在图上的显示名：有名字用名字，没名字用占位符（绝不回落 userId）。"""
    nm = str(mem.get("name") or "").strip()
    return nm or _NO_NAME


def _member_identity(mem: dict) -> str:
    """成员的稳定标识（节点 id 用）——**哈希过，不带账号原文**。

    ⛔ 隐私红线：id 会随 JSON 进浏览器，若直接用 `wxid_xxx` 就等于把真实账号发出去。
      这里取「wxid / 名字」的哈希（同名同 id ⇒ 连线仍然稳定），
      人读到的永远是 `name` 字段。
    """
    import hashlib

    uid = str(mem.get("userId") or "").strip()
    nm = str(mem.get("name") or "").strip()
    seed = ("u:" + uid) if uid else (("n:" + nm) if nm else ("x:" + _NO_NAME))
    return "p" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def _chat_id(chat_key: str) -> str:
    """会话节点 id —— 同样哈希（chat_key 里的群 id / wxid 也不许出网）。"""
    import hashlib

    return "c" + hashlib.sha1(("c:" + str(chat_key or "")).encode("utf-8")).hexdigest()[:12]


def _topics_of(texts, limit: int = MAX_TOPICS_PER_PERSON) -> list:
    """从一串印象正文里数出高频实词（与 `memory.hot_words` 同口径的零依赖切词）。"""
    counts: dict = {}
    stop = getattr(_mem, "_STOP", set())
    for t in texts or []:
        for tok in _tokens(str(t or "")):
            if len(tok) < 2 or tok in stop:
                continue
            counts[tok] = counts.get(tok, 0) + 1
    rows = [(w, n) for w, n in counts.items() if n >= MIN_TOPIC_HITS]
    rows.sort(key=lambda x: (-x[1], x[0]))
    return rows[: max(1, int(limit or 1))]


def _tokens(s: str) -> list:
    """与 `memory.hot_words` **同口径**的轻量切词：连续汉字/字母数字串 → 2–4 字滑窗。

    ⛔ 不能把整段当一个词（"喜欢夜跑" 整串会被当成一个词 ⇒ 「夜跑」永远浮不上来，
      判据实测就是这条）。滑窗 n-gram 零依赖、可复算，高频短串自然冒头。
      改动这里时 `memory.hot_words` 要一起改（两处是同一套规则）。
    """
    import re

    out: list = []
    for seg in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]+", s):
        n = len(seg)
        for k in (2, 3, 4):
            if n < k:
                continue
            for i in range(n - k + 1):
                out.append(seg[i:i + k])
    return out


def build(chat_key: str = "", *, store=None, max_nodes: int = MAX_NODES) -> dict:
    """构图。`chat_key` 为空 ⇒ 取本机**所有**有记忆的会话（一张总图）。

    返回 `{ok, nodes, links, stats, empty, note}`：
      · `nodes`: `[{id, name, type, importance, meta}]`；
      · `links`: `[{source, target, relation, weight}]`；
      · `empty=True` ⇒ 本机还没记下任何印象（前端据它显示"空图"说明）。
    """
    st = store if store is not None else _mem.MemoryStore()
    if str(chat_key or "").strip():
        keys = [chat_key]
    else:
        # ⛔ 枚举也要走 store：自定义 store（含测试替身）可能只在内存里有数据，
        #    直接扫本机目录会得到"没有群" ⇒ 图上什么都没有（判据实测就是这条）。
        fn = getattr(st, "chat_keys", None)
        if callable(fn):
            try:
                keys = [str(k) for k in (fn() or []) if str(k or "").strip()]
            except Exception as e:  # noqa: BLE001
                log.debug("store.chat_keys() 失败，回落本机扫描：%s", e)
                keys = list(_mem.chat_keys_on_disk() or [])
        else:
            keys = list(_mem.chat_keys_on_disk() or [])

    nodes: dict = {}
    links: list = []
    seen_edge: set = set()

    def _add_node(nid: str, name: str, ntype: str, importance: float, **meta) -> None:
        if nid not in nodes:
            nodes[nid] = {"id": nid, "name": name, "type": ntype,
                          "importance": round(float(importance), 3), "meta": meta}

    def _add_edge(a: str, b: str, rel: str, w: float) -> None:
        if not a or not b or a == b:
            return
        k = (a, b, rel)
        rk = (b, a, rel)
        if k in seen_edge or rk in seen_edge:
            return
        seen_edge.add(k)
        links.append({"source": a, "target": b, "relation": rel,
                      "weight": round(float(w), 3)})

    chat_count = 0
    person_count = 0
    for ck in keys:
        try:
            mems = st.members(ck) or []
        except Exception as e:  # noqa: BLE001
            log.debug("读会话 %s 的成员失败：%s", ck, e)
            continue
        mems = [m for m in mems if (m.get("impressions") or [])]
        if not mems:
            continue
        chat_count += 1
        cid = _chat_id(ck)
        total_imp = sum(len(m.get("impressions") or []) for m in mems)
        # 群名：本机记忆里没存群名 ⇒ 用 chat_key 的可读尾段（不编造一个假群名）
        cname = _chat_label(ck)
        # 群的重要度：人越多、印象越多越重要（只按印象数会被"一个人话多"顶上去）
        chat_imp = min(1.0, (total_imp / 40.0) * 0.5 + (len(mems) / 8.0) * 0.5)
        # ⛔ meta 里**不放 chatKey**（那是群 id/wxid）—— 节点 id 已是它的哈希，够前端连线。
        _add_node(cid, cname, "chat", max(0.05, chat_imp),
                  members=len(mems), impressions=total_imp)

        mems.sort(key=lambda m: -len(m.get("impressions") or []))
        mems = mems[:MAX_MEMBERS_PER_CHAT]
        pids = []
        for m in mems:
            pcount = len(m.get("impressions") or [])
            try:
                close = _mem.closeness(ck, m.get("userId") or "", name=m.get("name") or "")
            except Exception:  # noqa: BLE001
                close = 0.0
            pid = _member_identity(m)
            # ⛔ `meta` 里**不带 userId**：节点 id 内部已用 wxid 做稳定标识（前端只拿来连线，
            #    不显示）；把账号再塞进 meta 只会让它随 JSON 到浏览器（隐私红线，判据钉住）。
            _add_node(pid, _display_name(m), "person",
                      min(1.0, (pcount / 12.0) * 0.6 + close * 0.4),
                      impressions=pcount, closeness=close)
            _add_edge(pid, cid, "在群里", 1.0 + pcount / 10.0)
            pids.append(pid)
            # 话题：该成员印象里反复出现的实词
            for tw, tn in _topics_of([e.get("content") for e in (m.get("impressions") or [])]):
                tid = "t:" + tw
                _add_node(tid, tw, "topic", min(1.0, tn / 6.0), word=tw)
                _add_edge(pid, tid, "聊到", float(tn))
            person_count += 1
        # 同群共现：同群的两个人之间连一条弱边（权重＝两人印象数取小）
        for i in range(len(pids)):
            for j in range(i + 1, len(pids)):
                wi = (nodes.get(pids[i]) or {}).get("meta", {}).get("impressions", 1)
                wj = (nodes.get(pids[j]) or {}).get("meta", {}).get("impressions", 1)
                _add_edge(pids[i], pids[j], "同群", float(min(wi, wj)))

    # 节点超限：按重要度保留前 N，再剔除落单的边。
    # ⛔ 排序要**先看类型再看重要度**：图的主角是"人"，群和话题是承载它的容器；
    #    纯按重要度排会把满屏的群节点挤进来、人反而被裁掉（本机 269 个群实测就是这结果）。
    _rank = {"person": 0, "chat": 1, "topic": 2}
    all_nodes = list(nodes.values())
    if len(all_nodes) > max_nodes:
        all_nodes.sort(key=lambda n: (_rank.get(n.get("type"), 9), -(n.get("importance") or 0)))
        keep = {n["id"] for n in all_nodes[:max_nodes]}
        all_nodes = [n for n in all_nodes if n["id"] in keep]
        links = [l for l in links if l["source"] in keep and l["target"] in keep]

    stats = {"chats": chat_count, "persons": person_count,
             "nodes": len(all_nodes), "links": len(links)}
    empty = len(all_nodes) == 0
    return {
        "ok": True,
        "nodes": all_nodes,
        "links": links,
        "stats": stats,
        "empty": empty,
        "note": ("本机还没记下任何印象 —— 等它跟群里聊几轮，这里就会长出人来。"
                 if empty else "共 %d 个会话、%d 位成员；全部在本机生成，没花一点 token。"
                 % (chat_count, person_count)),
    }


def _chat_label(chat_key: str) -> str:
    """会话的可读标签（群/私聊）。

    ⛔ 隐私红线：`chat_key` 的尾段在私聊里就是**对方的 wxid**、在群里是群 id ⇒
      **不许原样搬上图**。本机记忆里没存群名/昵称（那张表在 persona 卡里，图谱不读），
      所以这里给一个**不带账号信息的占位标签**（私聊 · 一位好友），
      要看具体是谁就去「记忆」页的成员列表（那里按成员展示，本就有名字）。
    """
    ck = str(chat_key or "")
    if ck.startswith("private:"):
        return "私聊"
    tail = ck.split(":")[-1].replace("@chatroom", "")
    # 群 id 常是 `xxx@chatroom`；去后缀后若还像账号/摘要（长且无中文）也不搬上来
    if not tail or (len(tail) > 6 and not any("\u4e00" <= ch <= "\u9fff" for ch in tail)):
        return "一个群"
    return "群 · " + tail[:12]


def to_mermaid(chat_key: str = "", *, store=None) -> str:
    """导出 Mermaid（与 web 图同源，供文档/终端看）。"""
    g = build(chat_key, store=store)
    lines = ["graph LR"]
    id_map = {}
    for i, n in enumerate(g["nodes"]):
        nid = "N%d" % i
        id_map[n["id"]] = nid
        lines.append('  %s["%s<br><%s>"]' % (nid, n["name"], n["type"]))
    for l in g["links"]:
        s, t = id_map.get(l["source"]), id_map.get(l["target"])
        if s and t:
            lines.append("  %s -->|%s| %s" % (s, l["relation"], t))
    return "\n".join(lines)


def _root_has_data() -> bool:
    """本机记忆根目录是否已有数据（供接口决定是否给"空图"说明）。"""
    try:
        d = _mem.MEMORY_DIR
        return bool(d) and os.path.isdir(d) and any(
            os.path.isdir(os.path.join(d, x)) for x in os.listdir(d))
    except Exception:  # noqa: BLE001
        return False
