# -*- coding: utf-8 -*-
"""「群年报」判据（第 135 项）—— **不接真机、不读真库**（全用合成数据），不写产品 `data/`。

守四件事：
  A **聚合算对**：发言榜 / 小时分布 / 夜猫子 / 内容构成 / 最长一条 / 时间跨度（全用合成行喂进去）。
  B **诚实**：没有数据 ⇒ `ok=False` 且**说清原因**；**读满上限 ⇒ 标 `truncated`**
    （不许把"读了 3000 条"说成"这一年"）。
  C **出图**：PNG 真生成、尺寸 **3:4**、**不是白图**；**没数据不出图**；**没中文字体如实报错**（不画方块）。
  D **只读**：模块里**不许有写库/改消息**的调用（年报只读历史）。

用法：runtime\\python\\python.exe scripts/report_card_selftest.py
"""
import datetime as _dt
import io
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

from agent import report_card as RC  # noqa: E402
import _srcslice as _ss  # noqa: E402

PASS = FAIL = 0
TD = tempfile.mkdtemp(prefix="yb-")


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


def _ts(y, mo, d, h, mi=0):
    """本地时间 → 秒时间戳（**夜猫子要按本地小时判**，所以必须用本地时间构造）。"""
    return int(_dt.datetime(y, mo, d, h, mi).timestamp())


print("── A. 聚合（合成数据，不碰真机）──")
rows = [
    {"sender_username": "u1", "create_time": _ts(2026, 1, 1, 9), "type": 1, "content": "早"},
    {"sender_username": "u2", "create_time": _ts(2026, 1, 1, 2), "type": 1, "content": "还没睡"},
    {"sender_username": "u2", "create_time": _ts(2026, 1, 2, 3), "type": 47, "content": "[表情]"},
    {"sender_username": "u3", "create_time": _ts(2026, 1, 3, 14), "type": 3, "content": "[图]"},
    {"sender_username": "u1", "create_time": _ts(2026, 1, 3, 1), "type": 1,
     "content": "这条是最长的一条消息内容占位占位占位"},
]
rep = RC.annual_from_rows(rows, name="测试群", limit=3000)
ok("A0 分母守卫：聚合成功且条数对得上", rep["ok"] and rep["total"] == 5, str(rep.get("total")))
ok("A1 发言榜按条数降序、并列也稳", [x["who"] for x in rep["top_senders"]][0] == "u1"
   and rep["top_senders"][0]["n"] == 2, str(rep["top_senders"][:3]))
# 构造的数据里 0~5 点有：u2 的两条（2 点/3 点）+ u1 的一条（1 点）⇒ 夜里连"白天也说话的人"也算进来
_owls = {x["who"]: x["n"] for x in rep["night_owls"]}
ok("A2★ 夜猫子只算 0~5 点（u2 两条；u1 那条 1 点的也算）",
   _owls.get("u2") == 2 and _owls.get("u1") == 1 and "u3" not in _owls, str(rep["night_owls"]))
ok("A3 小时分布按本地小时落桶（1/2/3/9/14 各 1 条，1 点那条是 u1 的）",
   rep["hours"].get(2) == 1 and rep["hours"].get(3) == 1 and rep["hours"].get(14) == 1,
   str(rep["hours"]))
ok("A4 内容构成按 type 归类（数字走兜底表 ⇒ 文本/图片/动画表情）",
   {t["name"] for t in rep["types"]} == {"文本", "图片", "动画表情"}, str(rep["types"]))
ok("A4b★ 库里 `type` 是**中文串**时直接用原值（真库里就是中文，别当数字解析）",
   [x["name"] for x in RC.annual_from_rows(
       [{"sender_username": "u", "create_time": _ts(2026, 1, 1, 9), "type": "红包", "content": "x"}],
       name="g")["types"]] == ["红包"], "")
ok("A4c★ 群消息 `content` 的 `wxid_xxx:` 前缀**被剥掉**（否则「最长一条」是前缀）",
   RC._strip_prefix("wxid_abc12345:" + chr(10) + "你好呀") == "你好呀", "")
ok("A5 最长一条挑得对（不是第一条）", rep["longest"].startswith("这条是最长"), rep["longest"][:16])
ok("A5b★ 最长一条**只从文本类消息里挑**（非文本的 content 是 XML 载荷，挑进去没意义）",
   RC.annual_from_rows([
       {"sender_username": "u", "create_time": _ts(2026, 1, 1, 9), "type": "文本", "content": "短"},
       {"sender_username": "u", "create_time": _ts(2026, 1, 1, 10), "type": "图片",
        "content": "<msg><?xml version=\"1.0\"?>" + "x" * 300}], name="g")["longest"] == "短", "")
ok("A6 时间跨度按天算（1/1 → 1/3 = 3 天）", (rep.get("span") or {}).get("days") == 3,
   str(rep.get("span")))

print("\n── B. 诚实 ──")
ok("B1★ 没有数据 ⇒ `ok=False` 且**说清原因**（不出空白卡片）",
   (not RC.annual_from_rows([])["ok"]) and "没有可统计" in RC.annual_from_rows([])["note"], "")
_exact = RC.annual_from_rows(rows, limit=5, limit_hit=True)
ok("B2★ **读满上限 ⇒ 标 `truncated`**（不许把「读了 N 条」说成「这一年」）",
   _exact.get("truncated") is True and _exact.get("read_limit") == 5, str(_exact.get("truncated")))
ok("B3 坏时间戳**不炸**（`_hour_of` 读不出就返回 -1，该条不入小时桶）",
   RC._hour_of("不是时间") == -1 and RC._hour_of(None) == -1, "")
_zero = {"ok": True, "name": "空群", "total": 0, "span": {}, "top_senders": [], "hours": {},
         "night_owls": [], "types": [], "longest": "", "truncated": False}
ok("B4 报告里带上「读了多少条上限」这个口径字段（`read_limit`）"
   "—— `coverage` 那句话在 `annual()` 里组装（要读真库才有）",
   RC.annual_from_rows(rows, limit=1234).get("read_limit") == 1234, "")

print("\n── C. 出图 ──")
_r = RC.render_card(rep, os.path.join(TD, "a.png"))
ok("C1★ PNG 真生成", _r["ok"] and os.path.getsize(_r["path"]) > 8000,
   "%d 字节" % (os.path.getsize(_r["path"]) if _r["ok"] else 0))
try:
    from PIL import Image
    _im = Image.open(_r["path"]).convert("RGB")
    _w, _h = _im.size
    _cols = len(set(_im.getdata()))
except Exception:  # noqa: BLE001
    _w = _h = _cols = 0
ok("C2★ 尺寸是 **3:4**（%d×%d）" % (RC.CARD_W, RC.CARD_H),
   (_w, _h) == (RC.CARD_W, RC.CARD_H) and abs(_w / float(_h) - 0.75) < 1e-6, "%dx%d" % (_w, _h))
ok("C3★ **不是白图**（颜色数要够多）", _cols > 3, "%d 色" % _cols)
ok("C4★ 没数据 ⇒ **不出图**（空白卡片比不出图更糟）",
   RC.render_card({"ok": False, "note": "没有可统计的消息"}, os.path.join(TD, "e.png"))["ok"] is False, "")
_orig = RC.__dict__.get("find_font")
try:
    import agent.wordcloud as _wc
    _keep = _wc.find_font
    _wc.find_font = lambda: ""
    _nf = RC.render_card(rep, os.path.join(TD, "nofont.png"))
finally:
    _wc.find_font = _keep
ok("C5★ 找不到中文字体 ⇒ **如实报错**（绝不画方块还说成功）",
   (not _nf["ok"]) and "字体" in _nf["note"], _nf["note"][:24])

print("\n── D. 只读 ──")
_src = io.open(os.path.join(ROOT, "agent", "report_card.py"), encoding="utf-8").read()
_bad = [w for w in ("INSERT", "UPDATE ", "DELETE", "commit(", "send_text", "send_image")
        if w in _src]
ok("D1★ 模块里**没有写库/发消息**的字样（年报只读历史）", not _bad, str(_bad))
ok("D2 只读入口用的是既有的 `get_messages`（不新写一套库访问）",
   "db.get_messages(" in _src, "")

print("\n── E. 名场面（第 135 项③）──")
_hrows = [
    {"id": 1, "sender_name": "甲", "ts": _ts(2026, 1, 1, 14),
     "text": "今晚的月亮特别大，我站在阳台上看了好久好久好久好久好久", "self": False},
    {"id": 2, "sender_name": "乙", "ts": _ts(2026, 1, 2, 3),
     "text": "睡不着，谁还在？", "self": False},
    {"id": 3, "sender_name": "甲", "ts": _ts(2026, 1, 3, 14), "text": "嗯", "self": False},
    {"id": 4, "sender_name": "图", "ts": _ts(2026, 1, 3, 14), "type": "图片",
     "content": "<msg>" + "x" * 200, "self": False},
]
_h = RC.highlights_from_rows(_hrows, name="测试群")
ok("E0 分母守卫：挑得出、且挑的条数不超过候选数",
   _h["ok"] and 0 < len(_h["picks"]) <= 3, str(len(_h.get("picks") or [])))
ok("E1★ 太短的不算名场面（「嗯」不入榜）",
   all("嗯" != p["text"] for p in _h["picks"]), str([p["text"][:6] for p in _h["picks"]]))
ok("E2★ 非文本消息不入榜（图片的 content 是 XML 载荷）",
   all("msg" not in p["text"] for p in _h["picks"]), "")
ok("E3★ 深夜那条被打上「深夜」理由（0~5 点 +2 分）",
   any("点还在说" in p["why"] for p in _h["picks"]), str([p["why"] for p in _h["picks"]]))
ok("E4★ 每条都带「为什么入选」（用户能自己判断像不像话）",
   all(p.get("why") for p in _h["picks"]), "")
ok("E5★ 库里没数据 ⇒ `ok=False` 且说清原因（不硬凑）",
   (not RC.highlights_from_rows([], name="g")["ok"])
   and "没有可当" in RC.highlights_from_rows([], name="g")["note"], "")
# ⛔ **必须落到变量再比**，不许写成 `f(x) == f(x)`：
#   那种写法左右两边是同一个调用的结果 ⇒ **恒真**（判据卫生的⑥c 会红，而且它确实
#   什么都没验——函数里塞个随机数/时间戳照样"通过"）。落到 `_r1` / `_r2` 才是真跑两遍。
_r1 = RC.highlights_from_rows(_hrows, name="测试群")
_r2 = RC.highlights_from_rows(_hrows, name="测试群")
ok("E6★ 纯函数（不碰 IO）：只吃 rows，两次同一输入结果一致",
   _r1["picks"] == _r2["picks"], "")
_dup_picks = RC.highlights_from_rows(_hrows + [_hrows[0]], name="g")["picks"]
ok("E7 同一条消息只出现一次（id 去重）",
   len({p["id"] for p in _dup_picks}) == len(_dup_picks), "")
ok("E8★ `store` 那种行**没有 type 字段**也当文本（否则真机一条都挑不出来）",
   RC.highlights_from_rows([{"id": 9, "sender_name": "甲", "ts": 1791386610046,
                             "text": "这是一条没有 type 字段的普通文本消息"}])["ok"], "")

print("\n── F. 精华簿（第 135 项④：群友改写年度之最）──")
import agent.report_card as _rc2  # noqa: E402
_bk = os.path.join(TD, "highlights.jsonl")
_rc2._book_path = lambda: _bk
ok("F1★ 空簿 ⇒ `ok=False` 且说清「得先有人挑」",
   (not _rc2.book("g1")["ok"]) and "空的" in _rc2.book("g1")["note"], "")
ok("F2★ 群友挑的一条进簿 ⇒ 注明**是谁挑的**",
   _rc2.save_highlight("g1", {"id": "1", "who": "甲", "text": "今晚的月亮特别大"}, by="老王")["ok"]
   and _rc2.book("g1")["picks"][0]["by"] == "老王", "")
ok("F3★ 没写挑的人 ⇒ 记「某位群友」（**绝不冒充**是统计结果）",
   _rc2.save_highlight("g1", {"id": "2", "text": "另一条"})["ok"]
   and [p for p in _rc2.book("g1")["picks"] if p["id"] == "2"][0]["by"] == "某位群友", "")
ok("F4★ 同一条重复保存 ⇒ 只留一份（后写的来源覆盖前者）",
   len([p for p in _rc2.book("g1")["picks"] if p["id"] == "1"]) == 1, "")
ok("F5★ 空正文 ⇒ 不入簿（如实说没正文）",
   _rc2.save_highlight("g1", {"text": "  "})["ok"] is False, "")
ok("F6★ 簿只落 `data/`：模块里**没有写消息库**的字样（`save_highlight` 不碰消息）",
   not [w for w in ("append_incoming", "append_self", "_save_chat", "delete_entries")
        if w in _ss.func_src(_src, "save_highlight")], "")
ok("F7★ 精华簿路径与消息库**是两个文件**（改簿不影响消息）",
   "highlights.jsonl" in _src and "MESSAGES_DIR" not in _src, "")
ok("F8 按群过滤 `book(chat_key)`（别的群的不串）",
   _rc2.save_highlight("g2", {"id": "3", "text": "另一个群的"} , by="小李")["ok"]
   and all(p["chat_key"] == "g1" for p in _rc2.book("g1")["picks"]), "")

print("\n── G. 接线（工具 + 提示词）──")
_tools_src = io.open(os.path.join(ROOT, "agent", "tools.py"), encoding="utf-8").read()
_prompt_src = io.open(os.path.join(ROOT, "agent", "prompt.py"), encoding="utf-8").read()
ok("G1★ 工具表里有 `yearbook`", '"name": "yearbook"' in _tools_src, "")
ok("G2★ 执行体 `_exec_yearbook` 存在且三动作齐全",
   "_exec_yearbook" in _tools_src and "pick" in _ss.func_src(_tools_src, "_exec_yearbook")
   and "save" in _ss.func_src(_tools_src, "_exec_yearbook")
   and "book" in _ss.func_src(_tools_src, "_exec_yearbook"), "")
ok("G3★ 提示词里点了 `yearbook`（否则模型不知道有这个工具）",
   "yearbook" in _prompt_src, "")

print("\n== 群年报判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
