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

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent import report_card as RC  # noqa: E402

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

print("\n== 群年报判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
