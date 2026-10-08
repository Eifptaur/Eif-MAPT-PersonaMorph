# -*- coding: utf-8 -*-
"""辟谣解析（第 132 项，**减法版**）：给一条转发/链接 ⇒ 一篇**带结论、带来源、带免责**的核查。

## 减法版减掉了什么

⛔ **砍掉视频抽帧自研**：链路上那部分（下载 + 抽帧 + 视觉模型）由既有的 `video_read` 承担；
本模块**只做文本路** —— 抓正文 → 检索佐证 → 出结论。少了这一大块，实现面小了、可判性高了。

## 六档结论（**互斥，且判不出就老实说"信息不足"**）

    真实 / 大概率真实 / 存疑 / 大概率不实 / 已证实谣言 / 信息不足

⭐ **为什么必须有"信息不足"这一档**：没有它，模型只有"真/假"两个出口，
遇到查不到的东西就会**倒向一边**（而倒向哪边都是编）。留一个"不知道"的出口，
是让这个功能**敢说不知道**的前提。

## 三条硬规矩

1. **强制结构化**：模型只准输出 JSON（`{verdict, confidence, summary, points, sources}`）。
   解析失败 ⇒ **走原文兜底**（把模型原始输出原样交回，让上层自己判断），**不许硬套一个结论**。
2. **每条结论强制附免责**：`DISCLAIMER` 固定一句话，由本模块**贴在结果上**（不指望模型记得写）。
3. ⭐ **私聊共情优于当众打脸**：群发辟谣最伤的场景是"把转发的人当众打成传谣的"。
   `tone_of(where)` 给出措辞建议 —— 群里用"我查了一下…"，私聊才可以说"这条可能不太准"。
   ⛔ 这是**措辞口径**，不是"要不要管"的判断（管不管由上层/用户定）。
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger("persona-morph")

#: 六档结论（顺序＝由"真"到"假"，`信息不足` 独立在最后）
VERDICTS = ("真实", "大概率真实", "存疑", "大概率不实", "已证实谣言", "信息不足")

#: 固定免责声明（**由本模块贴**，不指望模型记得写）
DISCLAIMER = ("这条结论是**基于公开来源的自动核查**，仅供参考；"
              "它不能替代你自己去看原始信源 —— 尤其是涉及健康、法律、钱的事，请以官方发布为准。")

#: 正文抓取上限（字符）—— 太长会把上下文吃满，且尾部多是评论/推荐位
BODY_MAX = 6000
#: 取几条来源交给模型
SRC_MAX = 6


def _ok_verdict(v: str) -> str:
    """把模型给的档位归一：**不在六档里 ⇒ 归"信息不足"**（⛔ 不许自创一档）。"""
    t = re.sub(r"\s+", "", str(v or ""))
    for x in VERDICTS:
        if t == x:
            return x
    return "信息不足"


def parse_verdict(raw: str) -> dict:
    """把模型输出**强制**解析成 `{verdict, confidence, summary, points, sources, parsed}`。

    ⛔ **解析失败 ⇒ `parsed=False` 且原样带回**（走原文兜底），**不硬套一个结论**。
      "解析失败却给个默认结论"等于凭空造了一个判定 —— 比什么都不给更糟。
    """
    s = str(raw or "").strip()
    if not s:
        return {"parsed": False, "verdict": "信息不足", "confidence": 0.0,
                "summary": "", "points": [], "sources": [], "raw": ""}
    # 允许 ```json 围栏 / 前后有解释文字 ⇒ 抠出**第一个平衡的 JSON 对象**
    body = s
    m = re.search(r"\{[\s\S]*\}", s)
    if m:
        body = m.group(0)
    try:
        d = json.loads(body)
    except Exception:  # noqa: BLE001
        return {"parsed": False, "verdict": "信息不足", "confidence": 0.0,
                "summary": "", "points": [], "sources": [], "raw": s[:2000]}
    if not isinstance(d, dict):
        return {"parsed": False, "verdict": "信息不足", "confidence": 0.0,
                "summary": "", "points": [], "sources": [], "raw": s[:2000]}
    try:
        conf = float(d.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    pts = d.get("points")
    src = d.get("sources")
    return {
        "parsed": True,
        "verdict": _ok_verdict(d.get("verdict")),
        "confidence": max(0.0, min(1.0, conf)),
        "summary": str(d.get("summary") or "").strip()[:600],
        "points": [str(x).strip()[:200] for x in (pts if isinstance(pts, list) else [])][:8],
        "sources": [str(x).strip()[:300] for x in (src if isinstance(src, list) else [])][:SRC_MAX],
        "raw": "",
    }


def build_prompt(url: str = "", text: str = "", *, body: str = "",
                 results=None, where: str = "group") -> str:
    """拼**给模型的核查指令**（把"只输出 JSON"与六档写死在这里）。

    `where`：`group`（群里）/ `private`（私聊）⇒ 措辞口径不同（见模块头第 3 条）。
    """
    parts = []
    parts.append("请核查下面这条信息的真实性，并**只输出一个 JSON 对象**（不要任何解释文字、不要 Markdown 围栏）：")
    parts.append('{"verdict": "<六档之一>", "confidence": 0~1, "summary": "一句话结论", '
                 '"points": ["核对点1", "核对点2"], "sources": ["来源标题或链接"]}')
    parts.append("**六档只能取**：%s。" % " / ".join(VERDICTS))
    parts.append("⛔ 查不到、说不准 ⇒ **必须**填「信息不足」——**不许倒向「真」或「假」任何一边**。")
    parts.append("⛔ `sources` 只写你**真的看到过**的来源；一个都没看到就留空数组，**绝不编来源**。")
    parts.append("待核查内容：")
    if text:
        parts.append("【原文】%s" % str(text)[:2000])
    if body:
        parts.append("【正文摘录】%s" % str(body)[:BODY_MAX])
    if url:
        parts.append("【链接】%s" % url)
    if results:
        parts.append("【检索到的来源（供你核对，不是我方的结论）】")
        for r in results[:SRC_MAX]:
            if isinstance(r, dict):
                parts.append("- %s | %s | %s" % (str(r.get("title") or "")[:120],
                                                 str(r.get("url") or "")[:200],
                                                 str(r.get("snippet") or "")[:300]))
    if str(where) == "private":
        parts.append("（这是**一对一私聊**：可以说得直接一点，但仍要就事论事，别评价转发的人。）")
    else:
        parts.append("（这是**群聊**：⭐ 措辞要留面子 —— 用「我刚查了一下…」这种口吻，"
                     "**别把转发的人当众打成传谣的**；点到为止，别长篇大论。）")
    return "\n".join(parts)


def finalize(parsed: dict, *, results=None) -> dict:
    """把解析结果收成对外的 `{ok, verdict, confidence, summary, points, sources, disclaimer, note}`。

    ⛔ **每条结论都附免责**（由这里贴，不靠模型）；⛔ `parsed=False` ⇒ `ok=False` 且走原文兜底。
    """
    p = parsed if isinstance(parsed, dict) else {}
    if not p.get("parsed"):
        return {"ok": False, "verdict": "信息不足", "confidence": 0.0,
                "summary": "", "points": [], "sources": [],
                "disclaimer": DISCLAIMER, "raw": str(p.get("raw") or "")[:2000],
                "note": "模型的输出不是合法规格的 JSON ⇒ **没有下结论**（上面是它的原始输出，"
                        "你自己判断）。⛔ 不许因为解析失败就套一个默认结论。"}
    src = list(p.get("sources") or [])
    if not src and results:
        # 模型没写来源，但确实检索到了 ⇒ 把检索结果作来源附上（**如实标"未采用"**）
        src = [str((r or {}).get("url") or (r or {}).get("title") or "")[:300]
               for r in results[:SRC_MAX] if isinstance(r, dict)]
    out = {"ok": True, "verdict": str(p.get("verdict") or "信息不足"),
           "confidence": float(p.get("confidence") or 0.0),
           "summary": str(p.get("summary") or ""),
           "points": list(p.get("points") or []),
           "sources": src,
           "disclaimer": DISCLAIMER,
           "note": "来源是**检索到的原始链接**，不是我方结论；结论请你自己再核一遍。"}
    if not src:
        out["note"] += " ⚠️ 这次**一个来源都没检索到** ⇒ 结论可信度低，多半会落在「信息不足」。"
    return out


def check(content: str = "", *, url: str = "", llm=None, where: str = "group",
          cfg=None) -> dict:
    """**完整链路**：抓正文（有链接就抓）→ 检索佐证 → 让 `llm` 出 JSON → 解析 → 收口。

    `llm(prompt) -> str`：由调用方注入（本模块**不自己建 HTTP 客户端**，判据才能喂替身）。
    ⛔ `llm` 没给 ⇒ 只能回"抓到了什么"，**不假装核查过**。
    """
    body = ""
    fetch_note = ""
    if url:
        try:
            from .web_search import web_fetch as _wf
            r = _wf(str(url)) or {}
            body = str(r.get("body") or "")[:BODY_MAX]
            if not body:
                fetch_note = "抓不到正文（%s）" % str(r.get("why") or "空内容")[:60]
        except Exception as e:  # noqa: BLE001
            fetch_note = "抓正文失败：%s" % str(e)[:60]
    results = []
    try:
        from .web_search import web_search as _ws
        q = (str(content or "")[:60] + " 是真的吗 辟谣").strip()
        rs = _ws(q) or {}
        results = [x for x in (rs.get("results") or []) if isinstance(x, dict)][:SRC_MAX]
    except Exception as e:  # noqa: BLE001
        log.debug("辟谣检索失败（按无来源处理）：%s", e, exc_info=True)
    prompt = build_prompt(url, content, body=body, results=results, where=where)
    if not callable(llm):
        return {"ok": False, "verdict": "信息不足", "confidence": 0.0, "summary": "",
                "points": [], "sources": [],
                "disclaimer": DISCLAIMER,
                "note": "没有可用的模型 ⇒ **没有核查**，不假装核查过"
                        + ("（%s）" % fetch_note if fetch_note else ""),
                "prompt": prompt}
    try:
        raw = llm(prompt)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "verdict": "信息不足", "confidence": 0.0, "summary": "",
                "points": [], "sources": [], "disclaimer": DISCLAIMER,
                "note": "核查调用失败：%s（**没有下结论**）" % str(e)[:80]}
    out = finalize(parse_verdict(raw), results=results)
    if fetch_note:
        out["note"] = (fetch_note + "；" + str(out.get("note") or ""))
    return out


def tone_of(where: str = "group") -> str:
    """措辞口径（⭐ 私聊共情优于当众打脸）。"""
    if str(where) == "private":
        return ("一对一私聊：可以直接一点（「这条可能不太准，我查了下…」），"
                "但仍**就事论事、别评价对方**。")
    return ("群里：⭐ **别当众打脸** —— 用「我刚查了一下…」这种口吻把信息补上就好，"
            "**别点名、别说「你这是谣言」**；点到为止，一两句。")
