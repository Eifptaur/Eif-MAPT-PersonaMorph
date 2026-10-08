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

import logging
import os
import re
from collections import Counter

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
