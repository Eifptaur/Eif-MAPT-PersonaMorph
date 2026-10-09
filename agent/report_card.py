# -*- coding: utf-8 -*-
"""群年报（第 135 项）：把一个群的历史聚合成一份"年报数据"，并出一张 3:4 卡片。

## 口径（先写清楚，因为它决定这张图可不可信）

· **只读**：只读消息库，**不改任何消息**。
· **覆盖范围必须写在报告里**：读了多少条、时间跨度多长、**有没有读到上限**（`truncated`）。
  ⛔ 不要把"读了 3000 条"说成"这一年" —— 那是两件事。
· **没数据就如实说**：`{"ok": False, "note": "这个群没有可统计的消息"}`，不出空白卡片。

## 数据来源

`db.get_messages(user, limit, offset) -> List[dict]`，行字段：
`content` / `create_time`（**秒**时间戳） / `sender_id` / `sender_username` / `type`。
聚合与"取数"**分开**（`annual_from_rows` 是纯函数）⇒ 判据能直接喂合成数据，不必连真机。
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter

from . import persist # noqa: E402 原子换档：撞共享冲突要退避重试（见 persist.replace_into）

log = logging.getLogger(__name__)

#: 默认最多读多少条（越大越慢；**读满要标 truncated**）
DEFAULT_LIMIT = 3000
#: 卡片尺寸（3:4）
CARD_W, CARD_H = 1080, 1440
#: 夜猫子时段（本地时间的小时区间，左闭右开）
NIGHT_HOURS = (0, 5)
#: 消息类型的**兜底**映射：库里 `type` 一般**已经是中文名**（"文本/语音/图片/动画表情/文件/链接/卡片/系统消息/红包"…）
#  ⇒ 优先直接用原值；只有拿到数字时才回退到这张表。⛔ 别把数字表当主口径（会全归到"其他"）。
TYPE_NAMES = {1: "文本", 3: "图片", 34: "语音", 43: "视频", 47: "动画表情", 49: "文件/链接/卡片",
              42: "名片", 48: "位置", 50: "通话", 10000: "系统消息"}


def _type_name(v) -> str:
    """消息类型名：库里给的**字符串直接用**；数字走兜底表；其他归"其他"。"""
    if isinstance(v, str) and v.strip():
        return v.strip()
    try:
        return TYPE_NAMES.get(int(v), "其他")
    except Exception:  # noqa: BLE001
        return "其他"


#: 群消息的 `content` 常以 `wxid_xxx:` 开头（发言人名前缀）⇒ 统计文本前先剥掉它
_PREFIX_RE = re.compile(r"^\s*wxid_[A-Za-z0-9_\-]{4,}\s*[:：]\s*")


def _strip_prefix(content: str) -> str:
    """剥掉群消息头部可能带的 `wxid_xxx:` 前缀（只影响「最长一条」与后续文本统计）。"""
    return _PREFIX_RE.sub("", str(content or ""), count=1).strip()


def _hour_of(ts) -> int:
    """本地小时（0~23）。`ts` 是**秒**；读不出来返回 -1。"""
    try:
        import datetime as _dt
        return int(_dt.datetime.fromtimestamp(int(ts)).hour)
    except Exception:  # noqa: BLE001
        return -1


def annual_from_rows(rows, *, name: str = "", limit: int = DEFAULT_LIMIT,
                     limit_hit: bool = False) -> dict:
    """把消息行聚合成年报 ⇒ `{ok, name, total, span, top_senders, hours, night_owls, types, longest, truncated, note}`。

    **纯函数**（只吃 `rows`，不碰 IO）⇒ 判据可以喂合成数据。
    ⛔ 无数据 ⇒ `ok=False`（调用方据此如实告知，不出空白卡片）。
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return {"ok": False, "name": name, "total": 0, "truncated": bool(limit_hit),
                "note": "这个群还没有可统计的消息"}
    times = []
    by_sender: Counter = Counter()
    by_hour: Counter = Counter()
    night_by_sender: Counter = Counter()
    by_type: Counter = Counter()
    longest = ""
    for r in rows:
        t = r.get("create_time")
        try:
            _ts = int(t)
            times.append(_ts)
        except Exception:  # noqa: BLE001
            _ts = 0
        # 发言人：`sender_username` 可能为空 ⇒ 依次回退 `sender_id`、content 前缀里的 wxid
        who = str(r.get("sender_username") or r.get("sender_id") or "").strip()
        if not who:
            _m = _PREFIX_RE.match(str(r.get("content") or ""))
            who = _m.group(0).strip().rstrip(":：").strip() if _m else "未知"
        by_sender[who] += 1
        h = _hour_of(_ts) if _ts else -1
        if h >= 0:
            by_hour[h] += 1
            if NIGHT_HOURS[0] <= h < NIGHT_HOURS[1]:
                night_by_sender[who] += 1
        by_type[_type_name(r.get("type"))] += 1
        # ⛔ "最长一条"只从**文本类**消息里挑：非文本消息的 `content` 是 XML/JSON 载荷
        #   （真库里图片/链接/引用消息的 content 就是 `<msg>…`）⇒ 挑进去毫无意义。
        if _type_name(r.get("type")) in ("文本", "文字"):
            c = _strip_prefix(r.get("content"))
            if len(c) > len(longest):
                longest = c
    span = {"from": min(times), "to": max(times), "days": 0} if times else {}
    if times:
        span["days"] = max(1, int((max(times) - min(times)) / 86400.0) + 1)
    return {
        "ok": True, "name": name, "total": len(rows),
        "span": span,
        "top_senders": [{"who": w, "n": n} for w, n in by_sender.most_common(5)],
        "hours": {int(h): int(n) for h, n in sorted(by_hour.items())},
        "night_owls": [{"who": w, "n": n} for w, n in night_by_sender.most_common(3)],
        "types": [{"name": str(t), "n": int(n)} for t, n in by_type.most_common(6)],
        "longest": re.sub(r"\s+", " ", longest)[:200],
        "truncated": bool(limit_hit),
        "read_limit": int(limit),
    }


def annual(chat_key: str, *, db=None, limit: int = DEFAULT_LIMIT) -> dict:
    """读消息库并聚合 ⇒ 与 `annual_from_rows` 同构（外加 `coverage` 字段说明读到什么程度）。"""
    if db is None:
        try:
            from .wechat import open_db
            db, _how, _errs = open_db("")
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "name": str(chat_key), "total": 0, "truncated": False,
                    "note": "打不开消息库：%s" % str(e)[:80]}
    if db is None:
        return {"ok": False, "name": str(chat_key), "total": 0, "truncated": False,
                "note": "打不开消息库（没有可用连接）"}
    try:
        rows = db.get_messages(str(chat_key), int(limit)) or []
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "name": str(chat_key), "total": 0, "truncated": False,
                "note": "读消息失败：%s" % str(e)[:80]}
    # 名称尽量取群显示名（取不到就用 chat_key）
    name = str(chat_key)
    try:
        for g in (db.get_groups() or []):
            if isinstance(g, dict) and str(g.get("username")) == str(chat_key):
                name = str(g.get("name") or name)
                break
    except Exception:  # noqa: BLE001
        pass
    rep = annual_from_rows(rows, name=name, limit=limit, limit_hit=(len(rows) >= int(limit)))
    rep["chat_key"] = str(chat_key)
    # ④ 把榜上的 wxid 换成昵称（**只为显示**；解析不到就回退原值，绝不编一个名字）
    _nick: dict = {}

    def _nz(w: str) -> str:
        if w in _nick:
            return _nick[w]
        out = w
        try:
            _v = db.get_nickname(str(w))
            if _v and str(_v).strip():
                out = str(_v).strip()
        except Exception:  # noqa: BLE001
            pass
        _nick[w] = out
        return out

    for _k in ("top_senders", "night_owls"):
        for _it in (rep.get(_k) or []):
            _it["nick"] = _nz(str(_it.get("who") or ""))
    rep["coverage"] = ("读到 %d 条%s；时间跨度 %s 天"
                       % (len(rows), "（**已达读取上限，不是全部**）" if rep.get("truncated") else "",
                          (rep.get("span") or {}).get("days", 0)))
    return rep


def render_card(report: dict, path: str) -> dict:
    """出一张 **3:4 卡片** ⇒ `{ok, path, note}`。

    ⛔ 没数据（`ok=False`）⇒ **不出图**（空白卡片比不出图更糟：用户以为坏了）。
    ⛔ 找不到中文字体 ⇒ 如实报错，不画方块。
    """
    if not (report or {}).get("ok"):
        return {"ok": False, "path": "", "note": str((report or {}).get("note") or "没有可出图的数据")}
    try:
        from .wordcloud import find_font
        font_path = find_font()
    except Exception:  # noqa: BLE001
        font_path = ""
    if not font_path:
        return {"ok": False, "path": "", "note": "本机没找到中文字体，不出图（不画方块糊弄）"}
    try:
        from PIL import Image, ImageDraw, ImageFont

        def _draw(scale: float):
            """按 `scale` 画一遍 ⇒ 返回 (img, 用掉的高度)。字号与行距一起缩放 ⇒ 内容少时自动放大。"""
            img = Image.new("RGB", (CARD_W, CARD_H), (250, 250, 252))
            dr = ImageDraw.Draw(img)
            f = lambda px: ImageFont.truetype(font_path, max(14, int(px * scale)))  # noqa: E731
            ink, dim = (24, 28, 40), (96, 104, 122)
            x0, xr = 70, CARD_W - 70
            y = int(80 * scale)
            dr.text((x0, y), "群年报", font=f(64), fill=ink)
            y += int(96 * scale)
            dr.text((x0, y), str(report.get("name") or ""), font=f(34), fill=dim)
            y += int(64 * scale)
            dr.line((x0, y, xr, y), fill=(226, 230, 238), width=2)
            y += int(44 * scale)
            span = report.get("span") or {}
            dr.text((x0, y), "消息 %d 条　跨度 %s 天" % (report.get("total") or 0, span.get("days", 0)),
                    font=f(38), fill=ink)
            y += int(78 * scale)
            dr.text((x0, y), "发言最多", font=f(26), fill=dim)
            y += int(44 * scale)
            for i, it in enumerate((report.get("top_senders") or [])[:5], 1):
                dr.text((x0 + int(26 * scale), y), "%d. %s" % (i, str(it.get("who") or "")[:22]),
                        font=f(38), fill=ink)
                dr.text((CARD_W - int(210 * scale), y), "%d 条" % int(it.get("n") or 0), font=f(38), fill=dim)
                y += int(56 * scale)
            y += int(24 * scale)
            dr.text((x0, y), "夜猫子（%d~%d 点）" % NIGHT_HOURS, font=f(26), fill=dim)
            y += int(44 * scale)
            dr.text((x0 + int(26 * scale), y),
                    "、".join("%s %d" % (str(o.get("who"))[:12], int(o.get("n") or 0))
                              for o in (report.get("night_owls") or [])) or "—", font=f(38), fill=ink)
            y += int(72 * scale)
            dr.text((x0, y), "内容构成", font=f(26), fill=dim)
            y += int(44 * scale)
            dr.text((x0 + int(26 * scale), y),
                    "　".join("%s %d" % (t2.get("name"), int(t2.get("n") or 0))
                              for t2 in (report.get("types") or [])[:5]) or "—", font=f(38), fill=ink)
            y += int(84 * scale)
            dr.line((x0, y, xr, y), fill=(226, 230, 238), width=2)
            y += int(30 * scale)
            dr.text((x0, y), str(report.get("coverage") or ""), font=f(26), fill=dim)
            y += int(40 * scale)
            return img, y

        # ⛔ 内容少时**别留半张白**：先按 1.0 画一遍量高度，再按 0.9×可用高反推一个放大系数
        #   （上限 2.0 —— 再大就挤到边了）；放大后重画一次。
        _img, _used = _draw(1.0)
        _avail = CARD_H - int(80 * 1.0)
        _scale = 1.0
        if _used > 0:
            _scale = max(1.0, min(2.0, _avail / float(_used)))
        img, _used2 = _draw(_scale)
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        img.save(path, "PNG")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "path": "", "note": "出图失败：%s" % str(e)[:80]}
    return {"ok": True, "path": path, "note": "已出图（3:4，%dx%d）" % (CARD_W, CARD_H)}


def export(chat_key: str, path: str = "", *, db=None, limit: int = DEFAULT_LIMIT) -> dict:
    """对外入口：读库 → 聚合 → 出图 ⇒ `{ok, path, report, note}`。"""
    rep = annual(chat_key, db=db, limit=limit)
    if not rep.get("ok"):
        return {"ok": False, "path": "", "report": rep, "note": rep.get("note") or "没有数据"}
    if not path:
        from .config import DATA_DIR
        safe = re.sub(r'[\\/:*?"<>|\s]+', "_", str(rep.get("name") or chat_key))[:60]
        path = os.path.join(DATA_DIR, "yearbook", safe + ".png")
    r = render_card(rep, path)
    return {"ok": bool(r.get("ok")), "path": r.get("path") or "", "report": rep,
            "note": r.get("note") or ""}


# ══════════════════════════════════════════════════════════════════════════════
# ③ 名场面档案 ＋ 精华簿（第 135 项余下）
#
# 口径（先说清楚，因为它决定存档可不可信）：
# · **只读消息库**：本段所有函数都**不写消息**（要"改"的东西一律写进 `data/` 自己的档）。
# · **挑得出来才叫名场面**：候选来自**可复核的机械信号**（长度 / 深夜 / 表情码 / 被点名），
#   每条都带上"它为什么入选"（`why`）—— 用户能自己判断这条像不像话。
# · **库里没数据 ⇒ 如实说没数据**：`ok=False` + 写清原因，**不硬凑几条**出来。
# ══════════════════════════════════════════════════════════════════════════════

#: 精华簿每条最多存多少字（太长的一律截断，**存的是"名场面"不是全文**）
HIGHLIGHT_MAX = 120
#: 名称最长
NAME_MAX = 24


def highlights_from_rows(rows, *, name: str = "", top: int = 10) -> dict:
    """从消息行里挑「名场面」⇒ `{ok, name, picks:[{id,who,text,ts,why,score}], note}`。

    **纯函数**（只吃 `rows`）⇒ 判据可以喂合成数据，不必连真机。

    ⭐ 打分（可复核的机械信号，**不调模型、不做文义判断**）：
      · **长**：文本越长越像"认真说的一段话"（每 20 字 +1，最多 +5）
      · **深夜**：0~5 点发的（+2）—— 半夜说出来的话天然难忘
      · **有表情码**：带 `[xxx]`（+1）
      · **被点名**：文本里出现 `@`（+1）
      · **不是自己发的**：`self=False`（+1）—— 名场面是**群里**的，不是机器人自己的
    ⛔ 只从**文本类**消息里挑：非文本的 `content` 是 XML/JSON 载荷，挑进去毫无意义。
    ⛔ 同一条消息**只出现一次**；`id` 相同的行按第一条算。
    """
    picks = []
    seen_ids = set()
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        # `store` 的行**没有 `type` 字段**（它的消息都是自己记的文本）⇒ 缺字段当文本；
        # 真库里给了类型、且不是文本类的（图片/链接/引用…）一律排除（content 是 XML/JSON 载荷）。
        _raw_type = r.get("type")
        if _raw_type not in (None, "") and _type_name(_raw_type) not in ("文本", "文字"):
            continue
        _id = str(r.get("id") or r.get("mid") or "")
        if _id and _id in seen_ids:
            continue
        text = _strip_prefix(r.get("content") if r.get("content") is not None else r.get("text"))
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(text) < 6:  # 太短的不是"场面"，是"嗯""好""哈哈"
            continue
        if _id:
            seen_ids.add(_id)
        who = str(r.get("sender_name") or r.get("sender_username") or r.get("sender_id") or "").strip()
        try:
            _ts = int(r.get("ts") or r.get("create_time") or 0)
        except Exception:  # noqa: BLE001
            _ts = 0
        # `store` 里 `ts` 是**毫秒**；`db` 里 `create_time` 是**秒** ⇒ 归一成秒再判小时
        _sec = _ts / 1000.0 if _ts > 1e11 else float(_ts)
        _h = _hour_of(_sec) if _sec else -1
        score, whys = 0, []
        if len(text) >= 40:
            score += min(5, 1 + len(text) // 20)
            whys.append("说了一大段（%d 字）" % len(text))
        if 0 <= _h < NIGHT_HOURS[1]:
            score += 2
            whys.append("%d 点还在说" % _h)
        if re.search(r"\[[^\[\]]{1,6}\]", text):
            score += 1
            whys.append("带了表情")
        if "@" in text:
            score += 1
            whys.append("点名了人")
        if not r.get("self"):
            score += 1
        picks.append({"id": _id, "who": who[:NAME_MAX], "text": text[:HIGHLIGHT_MAX],
                      "ts": _ts, "why": "、".join(whys) or "群里的原话", "score": int(score)})
    if not picks:
        return {"ok": False, "name": name, "picks": [],
                "note": "这个群里没有可当「名场面」的文本消息（太短的不算）"}
    picks.sort(key=lambda x: (-x["score"], -x["ts"]))
    return {"ok": True, "name": name, "picks": picks[:max(1, int(top or 10))],
            "note": ("按「长度/深夜/表情/点名」机械挑的，**不是「最好的十条」**，"
                     "是「最有信息量的十条」；要不要留，你说了算。")}


def highlight_book(chat_key: str, *, db=None, store=None, top: int = 10) -> dict:
    """挑一个群的「名场面」⇒ 与 `highlights_from_rows` 同构（外加 `coverage`）。

    数据源**两级回退**：① 真消息库 `db.get_messages`（`create_time` 单位是**秒**）；
    ② 本地消息档案 `store.recent`（`ts` 单位是**毫秒**）。两级都空 ⇒ 如实说没数据。
    """
    name = str(chat_key)
    rows, src = [], ""
    if db is not None:
        try:
            rows = [dict(r or {}, id=r.get("id") if isinstance(r, dict) else None)
                    for r in (db.get_messages(str(chat_key), 0) or [])]
            src = "消息库"
        except Exception as e:  # noqa: BLE001
            log.debug("名场面读真库失败（回退本机档案）：%s", e, exc_info=True)
            rows, src = [], ""
    if not rows and store is not None:
        try:
            rows = list(store.recent(str(chat_key), limit=500) or [])
            src = "本机档案"
        except Exception as e:  # noqa: BLE001
            log.debug("名场面读本机档案失败：%s", e, exc_info=True)
            rows, src = [], ""
    rep = highlights_from_rows(rows, name=name, top=top)
    rep["chat_key"] = str(chat_key)
    rep["source"] = src
    rep["coverage"] = ("从%s读了 %d 条 ⇒ 挑出 %d 条"
                       % (src or "（没有可用数据源）", len(rows), len(rep.get("picks") or [])))
    return rep


def _book_path() -> str:
    """精华簿的落点（`data/highlights.jsonl`）—— **与消息库是两个文件**，改它不影响消息。"""
    from .config import DATA_DIR
    return os.path.join(DATA_DIR, "highlights.jsonl")


def _load_book() -> list:
    """读精华簿：坏行跳过、不抛（读不出来只该"少几条"，不该让功能瘫掉）。"""
    out = []
    try:
        with open(_book_path(), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except FileNotFoundError:
        return []
    except Exception as e:  # noqa: BLE001
        log.warning("精华簿读不出来：%s", e)
        return []
    return out


def _write_book(rows) -> bool:
    """整份写回精华簿（同目录先写 `.tmp` 再 `persist.replace_into` ⇒ 中途断电不会留半份）。"""
    p = _book_path()
    try:
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for r in (rows or []):
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        persist.replace_into(tmp, p)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("精华簿写不下去：%s", e)
        return False


def save_highlight(chat_key: str, item, *, by: str = "") -> dict:
    """把一条**群友挑的名场面**存进精华簿 ⇒ `{ok, note}`。

    ⭐ **群友改写的年度之最走这里**（第 135 项 ④）：它**只写 `data/highlights.jsonl`**，
      ⛔ **不碰消息库**（消息是原始事实，不能因为"选进了精华"就变样）。
    ⭐ `by` 记**是谁挑的** —— 存档里必须能一眼看出"这条是群友选的，不是统计出来的"，
      否则用户会把它当成年报自动算的结果（那是两回事）。
    """
    item = item if isinstance(item, dict) else {"text": str(item or "")}
    text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
    if not text:
        return {"ok": False, "note": "没有正文，不入簿"}
    rec = {"chat_key": str(chat_key), "id": str(item.get("id") or ""),
           "who": str(item.get("who") or "")[:NAME_MAX],
           "text": text[:HIGHLIGHT_MAX],
           "ts": int(item.get("ts") or 0),
           "by": str(by or "").strip()[:NAME_MAX] or "某位群友",
           "added": int(__import__("time").time())}
    rows = _load_book()
    # 同群同一条（id 相同且非空；否则按正文）只留一份，重复保存＝更新来源人
    _key = (rec["chat_key"], rec["id"] or rec["text"])
    rows = [r for r in rows if (str(r.get("chat_key")), str(r.get("id") or r.get("text"))) != _key]
    rows.append(rec)
    if not _write_book(rows):
        return {"ok": False, "note": "写精华簿失败（磁盘？）"}
    return {"ok": True, "note": "已入精华簿（注明是「%s」挑的）" % rec["by"]}


def book(chat_key: str = "") -> dict:
    """读精华簿 ⇒ `{ok, picks, note}`；`chat_key` 为空则读全部。**标注每条是谁挑的**。"""
    rows = [r for r in _load_book()
            if not chat_key or str(r.get("chat_key")) == str(chat_key)]
    if not rows:
        return {"ok": False, "picks": [],
                "note": "精华簿还是空的 —— 得先有人把某条挑进来（不是自动生成的东西）"}
    return {"ok": True, "picks": rows, "note": "共 %d 条；`by` 是挑它的人" % len(rows)}
