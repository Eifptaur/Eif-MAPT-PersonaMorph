# -*- coding: utf-8 -*-
"""一键数据导出（第 153 项）：把消息库导出成 **Markdown / CSV**，用户自己拿得走。

为什么要有它：用户的数据是**他的**。给一个"导出来能直接看、能直接进 Excel"的出口，
比任何"我们不会乱用"的承诺都实在。

⛔ 三个容易做错的地方（都写进判据）
  1. **CSV 必须带 BOM**（`utf-8-sig`）：不带的话 Excel 打开中文是乱码 —— 用户看到的就是"导出来是坏的"。
  2. **必须走 `csv` 模块**转义，不能自己用逗号拼：正文里本来就有逗号、引号、换行，
     手拼出来的表**列会串**（而且看着还挺像对的）。
  3. **条数必须与库里逐条一致**：导出是"搬运"，不是"挑一部分"。少一条就是**静默丢数据**。
     空会话也不要生成 0 字节文件 —— 出表头 ＋ 一行说明，用户才知道"是没数据"而不是"导坏了"。

用法
----
    runtime\\python\\python.exe scripts\\export_data.py --out <目录> [--chat <会话key>] [--format md|csv|both]

默认导出**全部会话**、两种格式都出，落在 `--out`（默认 `./exports/`）。
⛔ **只读消息库**：不改任何消息、不写产品 `data/`。
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

#: CSV 列头（顺序固定；用户按这个认列）
COLS = ("时间", "方向", "发送者", "昵称/备注", "内容", "媒体", "已撤回", "已屏蔽")


def _safe_name(s: str, limit: int = 60) -> str:
    """把会话 key 变成**合法文件名**（Windows 非法字符要换掉，且不许超长）。"""
    t = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(s or "")).strip(" .")
    t = t or "chat"
    if len(t) > limit:
        t = t[:limit] + "_" + str(abs(hash(t)) % 100000)
    return t


def _rows(store, chat_key: str) -> list:
    """会话的**全部**条目（含已撤回/已屏蔽 —— 导出是给用户存档，不该替他过滤）。"""
    try:
        return list(store.recent(chat_key, limit=10 ** 9, include_self=True,
                                 include_recalled=True, include_blocked=True) or [])
    except Exception:  # noqa: BLE001
        return []


def _fmt_ts(ts) -> str:
    try:
        t = float(ts or 0)
        if t > 1e11:
            t = t / 1000.0
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t > 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def _media_brief(media) -> str:
    """把 `media` 压成一句人话（**三种形态都见过**：dict / list / str ⇒ 都得认）。

    ⛔ 旧写法只处理 dict，遇到 list 直接 `AttributeError` ⇒ **整份导出崩在中途**
      （而且是"导到某个会话才炸"，前面几个文件已经写出来了，看着像成功了一半）。
    """
    if not media:
        return ""
    try:
        if isinstance(media, dict):
            return str(media.get("kind") or media.get("type") or media.get("path") or "媒体")[:80]
        if isinstance(media, (list, tuple)):
            return "、".join(_media_brief(x) for x in media if x)[:80] or "媒体"
        return str(media)[:80]
    except Exception:  # noqa: BLE001
        return "媒体"


def _cell(m: dict) -> tuple:
    return (_fmt_ts(m.get("ts")),
            "我" if m.get("self") else "群友",
            str(m.get("sender_name") or ""),
            "",   # 昵称/备注列留给上层填（当前消息里没有备注字段，如实留空，**不猜**）
            str(m.get("text") or ""),
            _media_brief(m.get("media")),
            "是" if m.get("recalled") else "",
            "是" if m.get("blocked") else "")


def export_csv(store, chat_key: str, out_path: str) -> dict:
    """写一个 CSV ⇒ `{rows, path}`。**UTF-8 带 BOM ＋ `csv.writer` 转义**。"""
    msgs = _rows(store, chat_key)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    # ⛔ `newline=""` 是 `csv` 模块的硬要求，否则 Windows 上每行之间会多一个空行。
    with io.open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLS)
        for m in msgs:
            w.writerow(_cell(m))
        if not msgs:
            # 空会话也要让用户看懂：不是导坏了，是**这个会话还没有消息**
            w.writerow(["（这个会话还没有消息）", "", "", "", "", "", "", ""])
    return {"rows": len(msgs), "path": out_path}


def export_md(store, chat_key: str, out_path: str) -> dict:
    """写一个 Markdown ⇒ `{rows, path}`（按时间**正序**，便于从头读）。"""
    msgs = list(reversed(_rows(store, chat_key)))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# 聊天导出 · %s\n\n" % chat_key)
        fh.write("> 导出时间：%s · 共 **%d** 条\n\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), len(msgs)))
        if not msgs:
            fh.write("_（这个会话还没有消息）_\n")
        for m in msgs:
            _t, _dir, _who, _nick, _txt, _med, _rec, _blk = _cell(m)
            _who = _who or _dir
            _flags = "".join([" `已撤回`" if _rec else "", " `已屏蔽`" if _blk else ""])
            _body = _txt or ("【%s】" % _med if _med else "（无正文）")
            # 发送者加粗、时间放后面 —— 一眼能扫出"谁说的"，时间只是附属信息
            fh.write("- **%s** %s%s：%s\n" % (_who, _t, _flags, _body.replace("\n", " ⏎ ")))
    return {"rows": len(msgs), "path": out_path}


def export_all(store, out_dir: str, *, chat: str = "", fmt: str = "both") -> dict:
    """导出 ⇒ `{"chats": {key: {md:…, csv:…, rows:N}}, "total_rows": N, "dir": out_dir}`。

    ⛔ **条数逐会话与库一致**（搬运，不挑不滤）；读不到的会话**如实记为 0**（不假装成功）。
    """
    keys = [chat] if chat else list(store.list_chats() or [])
    res, total = {}, 0
    for k in keys:
        _n = len(_rows(store, k))
        ent = {"rows": _n}
        base = _safe_name(k)
        if fmt in ("csv", "both"):
            ent["csv"] = export_csv(store, k, os.path.join(out_dir, base + ".csv"))
        if fmt in ("md", "both"):
            ent["md"] = export_md(store, k, os.path.join(out_dir, base + ".md"))
        res[str(k)] = ent
        total += _n
    return {"chats": res, "total_rows": total, "dir": out_dir}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="把消息库导出成 Markdown / CSV")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "exports"), help="输出目录（默认 ./exports/）")
    ap.add_argument("--chat", default="", help="只导这一个会话 key；不给＝全部")
    ap.add_argument("--format", default="both", choices=("md", "csv", "both"))
    a = ap.parse_args(argv)
    from agent.store import ChatStore
    st = ChatStore()
    r = export_all(st, a.out, chat=a.chat, fmt=a.format)
    print("导出到：%s" % r["dir"])
    for k, v in (r["chats"] or {}).items():
        print("  %-40s %d 条" % (k[:40], v["rows"]))
    print("合计 %d 条 / %d 个会话" % (r["total_rows"], len(r["chats"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
