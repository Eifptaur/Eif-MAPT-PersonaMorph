# -*- coding: utf-8 -*-
"""「按名字在整张列表里挑行」判据 —— **离线**（OCR 结果注入，不抓帧、不碰微信）。

守的是这条口径（人工给的排除法）：
  · **不靠绿底行**：目标会话不一定在绿底行，而**绿底行名字读不出是常态**
    （单字名字 OCR 天生不稳；列表窄时名字还带省略号）；
  · **按"读数 vs DB 里的会话名"的字符相似度打分**（前缀 / 最长公共子串），
    **取最高分且唯一**；"**有几个字符的相似度就够了，除非还有其他相符程度更高的**"；
  · ⛔ **不许只认汉字** —— 群名可能是英文/数字/混排（`KC Group`）⇒ 只认汉字会把它们全判 0 分；
  · 其他行都不像 ⇒ **退回绿底行**（`highlight_fallback`）；两条都没有 ⇒ `ok=False`（调用方走搜索）。

用法：runtime\\python\\python.exe scripts/pick_row_selftest.py
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


from agent import chat_ocr as CO  # noqa: E402

print("── A. 打分（读数 vs 目标名；不分中英）──")
ok("A1 分母守卫：`_row_score` 在（且可调用）", callable(getattr(CO, "_row_score", None)), "")
ok("A2 完全相等 ⇒ 1.0/exact", CO._row_score("文件传输助手", "文件传输助手") == (1.0, "exact"))
ok("A3 读数被截断（中文）⇒ ≤0.9/prefix", CO._row_score("文件", "文件传输助手")[1] == "prefix")
ok("A4 ★★ **英文/混排**也必须命中（`KCGroup12:30` vs `KC Group`）"
   "—— 这条锁住「不许退化成只认汉字」",
   CO._row_score("KCGroup12:30", "KC Group")[1] == "prefix"
   and CO._row_score("KCGroup12:30", "KC Group")[0] >= 0.9,
   str(CO._row_score("KCGroup12:30", "KC Group")))
ok("A5 带尾巴噪声（名字＋时间/未读黏一串）⇒ 仍有分（≥0.4）",
   CO._row_score("文件“，23．“", "文件传输助手")[0] >= 0.4,
   str(CO._row_score("文件“，23．“", "文件传输助手")))
ok("A6 ★反例锚：不相关的行必须 0 分（不许乱认）",
   CO._row_score("海绵“，19．“", "文件传输助手")[0] == 0.0
   and CO._row_score("03", "文件传输助手")[0] == 0.0, "")
ok("A7 空读数/空目标 ⇒ 0 分（不抛）", CO._row_score("", "X")[0] == 0.0, "")

print("── B. 挑行行为（注入式：不抓帧）──")
_keep_rows, _keep_hl = CO.session_rows, CO.highlight
try:
    # ① 唯一命中 ⇒ name_unique
    CO.session_rows = lambda *a, **k: [{"name": "海绵“，19．“", "y_abs": 437},
                                      {"name": "文件“，23．“", "y_abs": 337},
                                      {"name": "微信团队", "y_abs": 242}]
    CO.highlight = lambda img: {"y_abs": 242, "name": "微信团队", "score": 0.9}
    r1 = CO.pick_target_row(object(), "文件传输助手")
    ok("B1 唯一最高分 ⇒ `name_unique` 且 y 就是那一行（337）",
       r1["ok"] and r1["how"] == "name_unique" and r1["y_abs"] == 337, str(r1["how"]) + str(r1["y_abs"]))
    # ② 都不像 ⇒ 退回绿底行
    r2 = CO.pick_target_row(object(), "不存在的会话名")
    ok("B2 ★都不像 ⇒ 退回**绿底行**（`highlight_fallback`）",
       r2["ok"] and r2["how"] == "highlight_fallback" and r2["y_abs"] == 242, str(r2["how"]))
    # ③ 两行同分 ⇒ 不唯一 ⇒ 不许硬挑
    CO.session_rows = lambda *a, **k: [{"name": "KC Group A", "y_abs": 100},
                                      {"name": "KC Group B", "y_abs": 200}]
    CO.highlight = lambda img: None
    r3 = CO.pick_target_row(object(), "KC")
    ok("B3 ★两行同分（不唯一）⇒ 不许硬挑一个（`ok=False`）",
       (not r3["ok"]) and r3["how"] == "none", str(r3["how"]))
    # ④ 绿底行都没有 ⇒ none
    r4 = CO.pick_target_row(object(), "还是没有")
    ok("B4 两条路都没有 ⇒ `ok=False`（调用方走搜索）", (not r4["ok"]) and r4["how"] == "none", "")

    # ⑤★ **单字目标不许走纠错档** —— 真机实测过的坑：
    #   目标 `E`（一个字母）时，`best_candidate` 的护栏是"编辑距离 ≤1"，而**任何单字符读数
    #   距离都是 1** ⇒ 8 行里每一行都被纠成 `E`、全部报 0.85 ⇒ "哪一行都像目标"，
    #   最后挑到**聊天区**里那个 `宀`（落点 y=828，跑到列表外）。
    #   判据：行列表里**只有**真正的 `E` 该命中；聊天区串进来的 `宀` 必须 0 分。
    CO.session_rows = lambda *a, **k: [{"name": "宀", "y_abs": 828},
                                      {"name": "文件“，23．“", "y_abs": 240},
                                      {"name": "E", "y_abs": 168}]
    CO.highlight = lambda img: {"y_abs": 168, "name": "", "score": 0.9}
    _r5 = CO.pick_target_row(object(), "E")
    _r5_other = [x for x in (_r5.get("rows") or []) if x["name"] == "宀"]
    ok("B5★ 单字目标只认**完全相等**：真 `E` 命中、聊天区的 `宀` 判 0 分",
       _r5["ok"] and _r5["y_abs"] == 168 and _r5["name"] == "E"
       and (not _r5_other or _r5_other[0]["score"] == 0.0),
       "挑到 %r y=%s" % (_r5.get("name"), _r5.get("y_abs")))
    # ⑥ 反向锚：**多字**目标仍必须走纠错/相似度档（别把这条修成"一律只认完全相等"）
    CO.session_rows = lambda *a, **k: [{"name": "文件“，23．“", "y_abs": 240}]
    CO.highlight = lambda img: None
    _r6 = CO.pick_target_row(object(), "文件传输助手")
    ok("B6★ 反向锚：多字目标仍走相似度档（不许把单字那条修成全局「只认完全相等」）",
       _r6["ok"] and _r6["how"] == "name_unique" and _r6["y_abs"] == 240,
       "how=%s y=%s" % (_r6.get("how"), _r6.get("y_abs")))
    # ⑦ 单字目标 + 没有完全相等行 ⇒ 不许硬挑（宁可退回绿底行）
    CO.session_rows = lambda *a, **k: [{"name": "巷", "y_abs": 100},
                                      {"name": "群里的人", "y_abs": 200}]
    CO.highlight = lambda img: {"y_abs": 200, "name": "", "score": 0.8}
    _r7 = CO.pick_target_row(object(), "E")
    ok("B7★ 单字目标、无完全相等行 ⇒ 不许硬挑（退绿底行或 none，绝不挑 `巷`）",
       _r7.get("y_abs") != 100,
       "挑到 y=%s how=%s" % (_r7.get("y_abs"), _r7.get("how")))
finally:
    CO.session_rows, CO.highlight = _keep_rows, _keep_hl

print("\n== 挑行判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
