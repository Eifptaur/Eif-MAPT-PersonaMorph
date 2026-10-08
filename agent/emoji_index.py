# -*- coding: utf-8 -*-
"""自定义表情的**语义索引**机制（`data/emoji_index.json`）。

## 为什么必须有它
自定义表情（动图/静态图）是**像素**，模型**读不出含义** —— 它既不会"随机选"，
也不可能"看着图选"。**模型唯一的依据就是一行文字语义（caption）。**
⇒ 所以本模块的职责是：**把图片翻译成文字**，并维护这份翻译表。

## "让模型有根据"的完整机制
```
① 入库：表情文件进 data/emoticons/（收藏导出 / 群友发来的自动落盘）
② 打标（**自动兜底**）：
     · 若微信库自带 caption ⇒ 直接用；
     · 否则 ⇒ 由**多模态模型看图生成**一句 caption + 适用情绪
       （本模块负责登记"谁还没打标" `pending()`，上层拿去喂模型）
③ 选择：模型只收到 `id + caption + moods`（**纯文字，不含图**）⇒ 回一个 id
④ 发送：`resolve(id)` 拿到本地文件 ⇒ 走独立窗盲粘贴发出
```
⇒ 「自动」的含义：**入库触发 + 增量补标**，人不需要一张张手工写。

## 与 `emoji_lib.py` 的分工（重要，别混）
- `emoji_lib.py`：朋友版那套 —— 靠**微信表情面板网格坐标** `row=index//5` 去点选；
- **本模块**：**直接发本地文件**，不碰面板网格（我们已有独立窗盲粘贴通路）。
"""
from __future__ import annotations

import glob
import json
import os
import time

from . import persist  # 换档必须走唯一实现点（带退避重试）

import logging
log = logging.getLogger("persona-morph")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_PATH = os.path.join(_ROOT, "data", "emoji_index.json")
EMO_DIR = os.path.join(_ROOT, "data", "emoticons")


def _load() -> dict:
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("emojis"), list):
            return d
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    return {"emojis": [], "total": 0}


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    d["total"] = len(d.get("emojis") or [])
    d["built_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp = persist.tmp_for(INDEX_PATH)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    persist.replace_into(tmp, INDEX_PATH)


# ── 使用频次──────────────────────────────────────────────
#   为什么：群里"发哪个表情"该有据可依 —— **群友常用的优先**（而不是每次挑第一个）。
#   与 `INDEX_PATH` 同目录、同写法（tmp + persist.replace_into，保证原子替换）。
USAGE_PATH = os.path.join(_ROOT, "data", "emoji_usage.json")


def _load_usage() -> dict:
    try:
        with open(USAGE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    return {}


def bump_usage(key: str, n: int = 1) -> bool:
    """记一次使用。`key` ＝ 表情的 md5 或索引里的编号（与 `INDEX_PATH` 条目对得上）。"""
    k = str(key or "").strip()
    if not k:
        return False
    try:
        d = _load_usage()
        d[k] = int(d.get(k) or 0) + max(1, int(n))
        os.makedirs(os.path.dirname(USAGE_PATH), exist_ok=True)
        tmp = persist.tmp_for(USAGE_PATH)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        persist.replace_into(tmp, USAGE_PATH)
        return True
    except Exception: # noqa: BLE001
        return False


def top_used(limit: int = 10) -> list:
    """最常用的前 N 个 ⇒ `[(key, 次数), ...]`（降序）。**读不到就返回空，不编。**"""
    try:
        d = _load_usage()
        items = [(str(k), int(v)) for k, v in d.items() if int(v or 0) > 0]
        items.sort(key=lambda kv: -kv[1])
        return items[: max(1, int(limit or 10))]
    except Exception: # noqa: BLE001
        return []


def pick_by_usage(candidates) -> str:
    """**按使用频次从候选里挑一个**（常用的优先）；都不在频次表里 ⇒ **返回第一个**。

    为什么要它：以前的"挑一个"永远取**候选的第一个** ⇒ 表现出来就是「**只会发呲牙，不会发别的**」。
    现在：**用过次数多的优先**；都没用过 ⇒ **保持原顺序**（行为可预期，不会乱跳）。
    ⛔ 不编造候选：返回的一定是**传进来的某个**（传入为空 ⇒ 返回空串）。
    """
    try:
        cands = [str(c) for c in (candidates or []) if str(c or "").strip()]
    except Exception: # noqa: BLE001
        return ""
    if not cands:
        return ""
    try:
        used = dict(top_used(50))
    except Exception: # noqa: BLE001
        used = {}
    if not used:
        return cands[0]                       # 还没记过 ⇒ 保持原顺序（可预期）
    return sorted(cands, key=lambda c: -int(used.get(c) or 0))[0]


# ── 微信「方括号表情码」──────────────────────────────────
#   微信**自带**这套码：消息里写 `[呲牙]`，**微信发送时自己会渲染成表情** ⇒
#   这是最省的"发表情"路径：**不用鼠标、不用找坐标、也不管版本里表情的排列顺序**。
import re as _re  # noqa: E402
import logging


_CODE_RE = _re.compile(r"\[([\u4e00-\u9fa5A-Za-z0-9]{1,8})\]")


def parse_codes(text: str) -> list:
    """把文本里的表情码解出来 ⇒ `[(名称, 起, 止), ...]`（位置是**原串下标**）。

    ⛔ 只认「中文/字母/数字、1–8 字」这种白名单形态；**认不出就返回空列表** ——
      **绝不猜**（否则会把普通方括号文字当表情发出去，那是"改了但闹笑话"）。
    ⛔ 本函数**只解析、不发送**（发送由调用方决定），也不改原文本。
    """
    try:
        return [(m.group(1), m.start(), m.end()) for m in _CODE_RE.finditer(str(text or ""))]
    except Exception: # noqa: BLE001
        return []


def split_codes(text: str) -> list:
    """把文本切成「文字段 / 表情段」⇒ `[("text", 内容) | ("emoji", 名称), ...]`。

    给发送链用：**文字段照常打字，表情段走表情码** ⇒ 一条消息里可以"文字 + 表情"混排。
    纯文字 ⇒ 返回 `[("text", 原文)]`（**调用方按老路走，行为不变**）。
    """
    s = str(text or "")
    parts, pos = [], 0
    for name, a, b in parse_codes(s):
        if a > pos:
            parts.append(("text", s[pos:a]))
        parts.append(("emoji", name))
        pos = b
    if pos < len(s):
        parts.append(("text", s[pos:]))
    return parts or [("text", s)]


def _kind_of(path: str) -> str:
    return "动图" if os.path.splitext(path)[1].lower() == ".gif" else "静态"


def refresh(captions: dict | None = None) -> dict:
    """**扫描目录 → 增量合并索引**（不覆盖已有 caption，除非 `captions` 显式给了新的）。

    `captions`：`{md5: (caption, [moods])}` —— 由"模型看图生成"的批次结果传进来。
    返回 `{added, kept, missing_caption}`（`missing_caption` 是**还没打标**的 md5 列表，
    上层应拿去喂模型补标 —— 这就是"自动"的挂钩点）。
    """
    doc = _load()
    by_md5 = {str(e.get("md5")): e for e in (doc.get("emojis") or [])}
    # ⛔ **排除下划线开头的辅助文件**（如 `_sheet_for_user.png` 这类我们自己生成的对照图）——
    #    否则它们会被当成表情收进索引（实测踩过：33 条变 34 条）。
    files = [p for p in sorted(glob.glob(os.path.join(EMO_DIR, "*")))
             if not os.path.basename(p).startswith("_")]
    added, kept = 0, 0
    for f in files:
        stem = os.path.splitext(os.path.basename(f))[0]
        cur = by_md5.get(stem)
        if cur is None:
            cur = {"id": 0, "md5": stem,
                   "path": os.path.relpath(f, _ROOT).replace("\\", "/"),
                   "kind": _kind_of(f), "caption": "", "situation": "",
                   "meme": "", "moods": [], "confidence": "low", "needs_web": True}
            doc.setdefault("emojis", []).append(cur)
            by_md5[stem] = cur
            added += 1
        else:
            cur["path"] = os.path.relpath(f, _ROOT).replace("\\", "/")
            cur["kind"] = _kind_of(f)
            kept += 1
        if captions and stem in captions:
            cap, moods = captions[stem]
            if cap:
                cur["caption"] = str(cap)
                cur["moods"] = list(moods or [])
    # 重排 id（稳定：按 md5 排序）
    doc["emojis"].sort(key=lambda e: str(e.get("md5")))
    for i, e in enumerate(doc["emojis"]):
        e["id"] = i
    _save(doc)
    missing = [e["md5"] for e in doc["emojis"] if not e.get("caption")]
    return {"added": added, "kept": kept, "total": len(doc["emojis"]),
            "missing_caption": missing}


def pending() -> list:
    """**还没打标**的表情（md5 列表）⇒ 上层拿去喂多模态模型生成 caption。"""
    return [e["md5"] for e in (_load().get("emojis") or []) if not e.get("caption")]


def apply_captions(mapping: dict) -> int:
    """把"模型看出来的"语义写回索引：`{md5: (caption, [moods])}` ⇒ 返回更新条数。"""
    doc = _load()
    n = 0
    for e in (doc.get("emojis") or []):
        v = mapping.get(str(e.get("md5")))
        if v:
            cap, moods = (v if isinstance(v, (tuple, list)) else (v, []))
            if cap:
                e["caption"] = str(cap)
                e["moods"] = list(moods or [])
                n += 1
    _save(doc)
    return n


def list_for_model(*, only_mood: str = "", limit: int = 0,
                   include_low_confidence: bool = True) -> list:
    """★ **给模型的清单**：`id / caption / situation / kind / moods / confidence`（**不含图**）。

    ★★ 为什么要有 `situation` 与 `confidence`：
      · 很多表情**不是表达情绪**，而是**应付某个场面** ⇒ 只给 `moods`，模型会选错；
        ⇒ 必须给 **`situation`（什么时候该用它）**。
      · 梗图（meme）的含义**不在画面里**（要靠外部知识）⇒ 看图给的语义**可能根本是错的**；
        ⇒ 必须把 **`confidence` 一并交给模型**，让它知道"这条靠不靠得住"，
          而不是把低把握的当确定语义用。

    `only_mood` 非空时只给含该情绪的表情（缩小选择面）。
    `include_low_confidence=False` 时**只给把握高的**（宁缺勿错）。
    """
    out = []
    for e in (_load().get("emojis") or []):
        if not e.get("caption"):
            continue
        if not include_low_confidence and str(e.get("confidence") or "") == "low":
            continue
        if only_mood and only_mood not in (e.get("moods") or []):
            continue
        out.append({"id": e["id"],
                    "caption": e["caption"],
                    "situation": e.get("situation") or "",
                    "kind": e.get("kind"),
                    "moods": e.get("moods") or [],
                    "confidence": e.get("confidence") or "low"})
    return out[:limit] if limit else out


def need_web() -> list:
    """**必须外部资料才能定死**的表情（梗图）⇒ 返回 `{id, caption, meme}`。

    用途：这些条目的语义**不能只靠看图**。要么去查（新闻/剧集/流行语），
    要么**在实际使用中被确认/修正**（见 `learn()`）。
    """
    return [{"id": e["id"], "caption": e.get("caption"),
             "meme": e.get("meme")}
            for e in (_load().get("emojis") or []) if e.get("needs_web")]


def low_confidence() -> list:
    """把握不足的条目（疑似梗）id 列表 —— 发给模型时应**标注其不可靠**。"""
    return [e["id"] for e in (_load().get("emojis") or [])
            if str(e.get("confidence") or "") == "low"]


def learn(id_or_md5, *, caption: str = None, situation: str = None,
          moods=None, meme: str = None, confidence: str = None) -> bool:
    """★ **在使用中学习**：把"实际用出来才知道"的语义写回索引。

    ⛔ 为什么需要它：梗图/时新梗的语义，**看图猜不准、联网也不一定搜得到**
      （往往没有可搜的关键词）。真正可靠的来源是**它在真实对话里被怎么用**。
      ⇒ 每次真实使用后（或用户/群友点明），把确认下来的语义回填，条目**越用越准**。
    返回是否命中并写入。
    """
    key = str(id_or_md5).strip()
    doc = _load()
    for e in (doc.get("emojis") or []):
        if key == str(e.get("id")) or key == str(e.get("md5")):
            if caption is not None:
                e["caption"] = str(caption)
            if situation is not None:
                e["situation"] = str(situation)
            if moods is not None:
                e["moods"] = list(moods or [])
            if meme is not None:
                e["meme"] = str(meme)
            if confidence is not None:
                e["confidence"] = str(confidence)
                if str(confidence) == "high":
                    e["needs_web"] = False
            _save(doc)
            return True
    return False


def resolve(id_or_md5) -> str:
    """把模型的**选择**还原成本地文件绝对路径（发送用）。取不到给空串。"""
    key = str(id_or_md5).strip()
    for e in (_load().get("emojis") or []):
        if key == str(e.get("id")) or key == str(e.get("md5")):
            p = os.path.join(_ROOT, str(e.get("path") or ""))
            return p if os.path.isfile(p) else ""
    return ""


def search_queries(id_or_md5, *, platforms=("bilibili.com", "douyin.com",
                                            "xiaohongshu.com", "weibo.com")) -> list:
    """★ **给模型的"定向检索词"**。

    ⛔ 为什么需要它（实测教训）：
      梗图的语义只靠看图读不出；而"搜不到"的真实原因往往是**检索词不对** ——
      · 配文被 OCR **读错**（实测：把『高高在上』读成『低低在下』⇒ 全场搜不到）；
      · 只搜配文、**不带画面主体**（实测：加上"蓝色章鱼"后才出现有效线索）；
      · 没**定向平台**（自制表情包大多在 **B站/抖音/小红书/微博**，而不是百科类站点）。
    ⇒ 所以这里把"该怎么搜"固化成一组 query：**画面主体 × 配文（多种读法）× 平台**。

    返回可直接交给检索器的 query 列表（**同一批 query 应全部试一遍**，别只试第一条）。
    """
    e = None
    key = str(id_or_md5).strip()
    for x in (_load().get("emojis") or []):
        if key == str(x.get("id")) or key == str(x.get("md5")):
            e = x
            break
    if not e:
        return []
    cap = str(e.get("caption") or "")
    # 从 caption 里抽"配文"（书名号/引号里的部分）与"主体"（其余）
    import re
    quoted = re.findall(r"[「『\"']([^」』\"']+)[」』\"']", cap)
    body = re.sub(r"[「『\"'][^」』\"']*[」』\"']", " ", cap)
    body = re.sub(r"[（(][^）)]*[）)]", " ", body).strip()
    out = []
    caps = quoted or [""]
    for c in caps:
        if c:
            out.append("%s 表情包 出处" % c)
            out.append("%s 什么梗" % c)
        if body:
            out.append("%s %s 表情包" % (body[:20], c) if c else "%s 表情包" % body[:20])
    for p in platforms:
        if caps and caps[0]:
            out.append('site:%s %s 表情包' % (p, caps[0]))
    # 去重保序
    seen, uniq = set(), []
    for q in out:
        q = " ".join(str(q).split())
        if q and q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq


def need_search() -> list:
    """需要**定向检索**的条目 ⇒ `[{id, caption, queries}]`（按 `confidence` 低的优先）。"""
    out = []
    for e in (_load().get("emojis") or []):
        if str(e.get("confidence") or "") != "low":
            continue
        out.append({"id": e["id"], "caption": e.get("caption"),
                    "queries": search_queries(e["id"])})
    return out


def extract_marker(text: str):
    """★ **解析模型回复里的表情标记** ⇒ `(去掉标记的文本, id 或 None, 说明)`。

    标记格式：`[[表情:12]]`（也容忍全角冒号/空格/中文括号变体）。
    ⇒ 程序据此取本地文件发出去；**模型只管写标记，不碰发送**。

    ⛔ 为什么要"去掉标记再发文本"：标记本身**不能发给群友**（发出去就成了乱码）。
    """
    import re
    s = str(text or "")
    pat = re.compile(r"[\[【]{2}\s*表情\s*[:：]\s*(\d+)\s*[\]】]{2}")
    m = pat.search(s)
    if not m:
        return s, None, ""
    sid = int(m.group(1))
    clean = pat.sub("", s).strip()
    path = resolve(sid)
    if not path:
        return clean, None, "标记里的编号 %d 取不到文件 ⇒ 不发" % sid
    # 顺带查一下这条是不是"把握不足"的，便于调用方决定要不要谨慎
    conf = ""
    for e in (_load().get("emojis") or []):
        if str(e.get("id")) == str(sid):
            conf = str(e.get("confidence") or "")
            break
    return clean, sid, ("编号 %d（把握=%s）" % (sid, conf or "?"))


def stats() -> dict:
    """索引概况：总数 / 有标注数 / 动图数 / 静态数。"""
    es = _load().get("emojis") or []
    return {"total": len(es),
            "with_caption": sum(1 for e in es if e.get("caption")),
            "animated": sum(1 for e in es if e.get("kind") == "动图"),
            "static": sum(1 for e in es if e.get("kind") == "静态")}
