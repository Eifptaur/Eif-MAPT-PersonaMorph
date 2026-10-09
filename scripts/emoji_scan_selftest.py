# -*- coding: utf-8 -*-
"""「收藏表情批量占位登记」判据 —— 守第 131 项。

起因：用户**早就**在微信里收藏了一堆表情，但机器人从没登记过 index ⇒ 那些表情一张也用不了。

核心几条：
  · **扫到几格就登记几格**（`count<=0` ⇒ 如实返回 0，⛔ 不编造"扫到了几张"）；
  · **占位不等于知情** —— `is_scanned()` 只说明"格子在"，`has_real_paths()` 才说明"见过图"；
  · **选法诚实** —— 无真实路径 ⇒ `how="random"`；有真实路径 ⇒ `how="context"`；
    ⛔ **「随机选」绝不许被说成「按语境选」**（用户会以为机器人认得每张图）；
  · **不冲掉真货** —— 已有真实 path 的条目不因再次扫描被覆盖。

用法：runtime\\python\\python.exe scripts\\emoji_scan_selftest.py
"""
import io
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    tag = "OK  " if cond else "FAIL"
    print("  {} {}{}".format(tag, name, "  [{}]".format(detail) if detail else ""))


import _srcslice as _ss  # noqa: E402
from agent import emoji_lib as E  # noqa: E402

TOOLS_SRC = io.open(os.path.join(ROOT, "agent", "tools.py"), encoding="utf-8").read()
LIB_SRC = io.open(os.path.join(ROOT, "agent", "emoji_lib.py"), encoding="utf-8").read()

# ⛔ 判据**不许碰真索引文件**（`data/emoji_index.json` 是用户资产）：全程用临时目录换掉路径
_TMP = tempfile.mkdtemp(prefix="emoji_scan_")
E.INDEX_PATH = os.path.join(_TMP, "emoji_index.json")


def _reset(items=None):
    E._save({"emojis": items or [], "total": len(items or [])})


print("── A. 模块形态（能力都在 · 一处实现）──")
for _n in ("register_scanned", "is_scanned", "has_real_paths", "pick_random", "choose"):
    ok("A. 有 `%s`" % _n, callable(getattr(E, _n, None)))
ok("A1 占位概述前缀常量为「收藏表情」", E.PLACEHOLDER_PREFIX == "收藏表情",
   E.PLACEHOLDER_PREFIX)
ok("A2 反向锚：判据自己**没**碰产品索引（用的是临时路径）",
   "emoji_scan_" in E.INDEX_PATH, E.INDEX_PATH)

print("── B. 登记：扫到几格就登记几格 ──")
_reset()
ok("B1 空索引 ⇒ is_scanned 为假（还没扫过）", E.is_scanned() is False)
_r0 = E.register_scanned(0)
ok("B2 `count=0` ⇒ 一条都不登记（如实返回 0，不编造）",
   _r0["added"] == 0 and E.is_scanned() is False, str(_r0))
ok("B3 `count=-3` ⇒ 同上（负数是坏数据，不当成「扫到 -3 格」）",
   E.register_scanned(-3)["added"] == 0)
_r = E.register_scanned(13)
ok("B4 扫到 13 格 ⇒ 登记 13 条", _r["added"] == 13 and _r["total"] == 13, str(_r))
ok("B5 格序号是 0..12 连续（点选要按序号算行列，跳号会点偏）",
   [s["index"] for s in E.list_summaries()] == list(range(13)))
ok("B6 登记后 is_scanned 为真", E.is_scanned() is True)
ok("B7 占位条目的 path **必须**为空（我们没真存图，不许假装存了）",
   all(not s.get("path") for s in E.list_summaries()))
ok("B8 `has_real_paths` 仍为假（占位 ≠ 见过图）", E.has_real_paths() is False)

print("── C. 重扫：格数变了以新扫描为准，但**不许冲掉真货** ──")
_reset([{"index": 0, "summary": "猫祟祟", "path": "/tmp/cat.png"},
        {"index": 1, "summary": "收藏表情1", "path": ""}])
_r2 = E.register_scanned(8)
# ⚠️ `list_summaries()` 只给 {index, summary}（那是报给模型看的）⇒ 判 path 要走 `_load()`
_raw = E._load()["emojis"]
ok("C1 重扫 8 格 ⇒ 真实路径那条被保住",
   any(s.get("path") == "/tmp/cat.png" for s in _raw), str(_r2))
ok("C2 真实条目计入 kept_real（上层能看到保住了几条）", _r2["kept_real"] == 1, str(_r2))
ok("C3 纯占位条目被新扫描对齐（旧的 1 条不再重复留）",
   len(_raw) == 1 + 8, "%d 条" % len(_raw))
ok("C4 有真实路径 ⇒ has_real_paths 变真", E.has_real_paths() is True)
ok("C5 反向锚：`keep_existing=False` 时真实条目**会**被清掉（证明 C1 是这条在起作用）",
   (lambda: (E.register_scanned(3, keep_existing=False),
             E.has_real_paths() is False)[1])())

print("── D. 选法诚实（本项的验收点）──")
_reset([{"index": 0, "summary": "收藏表情1", "path": ""},
        {"index": 1, "summary": "收藏表情2", "path": ""},
        {"index": 2, "summary": "收藏表情3", "path": ""}])
_i, _h = E.choose("今天好开心啊")
ok("D1 只有占位 ⇒ `how` 必须是 `random`（不许说成按语境）", _h == "random", "%s / %s" % (_i, _h))
ok("D2 随机落在已登记的格号里（不是编一个）", _i in (0, 1, 2), str(_i))
_p = E.pick_random(seed=1)
ok("D3 `pick_random(seed=...)` 可复现（判据要能钉住随机）", E.pick_random(seed=1) == _p, str(_p))
_reset([{"index": 0, "summary": "猫祟祟", "path": "/tmp/cat.png"},
        {"index": 1, "summary": "哈哈", "path": "/tmp/haha.png"}])
_i2, _h2 = E.choose("今天好开心啊")
ok("D4 有真实路径 ⇒ `how` = `context`（这时才敢按语境选）", _h2 == "context", str(_h2))
_reset([])
ok("D5 完全没登记 ⇒ `(-1, \"\")`（不编格号，也不假装知道怎么选）",
   E.choose("随便") == (-1, ""), str(E.choose("随便")))

print("── E. 接线：发送链真的走这条分叉（写了没人用＝假开关）──")
_SEND = _ss.func_src(TOOLS_SRC, "_exec_send_emoji")
ok("E1 分母守卫：拿到 `_exec_send_emoji` 函数体（>2000 字符）", len(_SEND) > 2000,
   "%d 字符" % len(_SEND))
ok("E2 发送链调 `_el.choose(...)`（唯一选格入口）", "_el.choose(" in _SEND)
ok("E3 反向锚：**不再**直接调 `_el.pick(` 选格（旧路绕过诚实分叉）",
   "_el.pick(" not in _SEND, "")
ok("E4 `how` 变量在函数里被接住（不是调了丢掉）", "idx, how = " in _SEND)
ok("E5 占位登记时随发送回执**带出** `how`（上层据此措辞）", '"how": how' in _SEND)
ok("E6 随机选时回执里有「随机」字样（否则用户以为它懂了语境）",
   "随机" in _SEND and "how == \"random\"" in _SEND, "")
ok("E7 分母守卫：两处成功返回（面板优先 / 面板兜底）都带 `how`",
   _SEND.count('"how": how') >= 2, "%d 处" % _SEND.count('"how": how'))

print("── F. 窗口侧接口（扫描动作本身要真机，接口先钉住）──")
WX_SRC = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
ok("F1 行高常量在（按像素算格位的前提）", "EMOJI_ROW_PX" in WX_SRC)
ok("F2 列数常量在（5 列；行列换算要用）", "COLS = 5" in LIB_SRC, "")
ok("F3 ⚠️ 扫描动作（真去点开收藏面板数格）**本轮没做** —— 它是真机动作，"
   "不在纯函数可判范围内（按「没测成」记，不写成「测失败」）",
   "register_scanned" in LIB_SRC, "")

print("")
print("收藏表情占位登记判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
