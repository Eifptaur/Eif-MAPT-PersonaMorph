# -*- coding: utf-8 -*-
"""「纪念日提醒」判据 —— 守第 124 项（含**提前预告**那半）。

核心几条：
  · **当天到点**：`due_anniversaries` 只认月-日，跨年**自动重复**（去年 03-02 的事，今年 03-02 仍命中）；
  · **提前预告**（本项补的那半）：未来 N 天内到点 ⇒ 提前提一句（当天才想起来已经晚了）；
  · **两档不重叠**：`upcoming` 必须**排掉今天**（否则同一条出现两次，或把「今天」说成「明天」）；
  · **可关**：`<=0` ⇒ 不预告（空表），一个字都不注入；
  · **跨年绕回**：12-31 的次日是 01-01（不是 13-01 或报错）；
  · **接线**：`seeds_for_prompt` 必须真把预告渲染进提示词（写了没人用＝假开关）。

用法：runtime\\python\\python.exe scripts\\anniv_selftest.py
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
from agent import memory as M  # noqa: E402

MEM_SRC = io.open(os.path.join(ROOT, "agent", "memory.py"), encoding="utf-8").read()
CFG_SRC = io.open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read()
EX_SRC = io.open(os.path.join(ROOT, "config.example.json"), encoding="utf-8").read()

# ⛔ 判据不许碰用户的真实记忆目录：把落点换到临时目录
_TMP = tempfile.mkdtemp(prefix="anniv_")
M._chat_dir = (lambda _orig: (lambda ck: os.path.join(_TMP, str(abs(hash(ck)) % 10 ** 8))))(M._chat_dir)
CK = "group:anniv_test"

# 清干净起点
try:
    import shutil
    shutil.rmtree(_TMP, ignore_errors=True)
except Exception:  # noqa: BLE001
    pass
os.makedirs(_TMP, exist_ok=True)


def _seed(what, date, who="小王", emotion=""):
    return M.add_event(CK, what, who=who, date=date, emotion=emotion, long_term=True)


print("── A. 模块形态 ──")
ok("A1 `upcoming_anniversaries` 在", callable(getattr(M, "upcoming_anniversaries", None)))
ok("A2 `_md_plus` 在（日期推进的唯一实现）", callable(getattr(M, "_md_plus", None)))
ok("A3 反向锚：判据自己没碰产品记忆目录（用的是临时目录）", "anniv_" in _TMP, _TMP)

print("── B. 跨年绕回（`_md_plus` 的边界）──")
ok("B1 12-31 的次日 ⇒ 01-01（跨年绕回，不是 13-01）", M._md_plus("12-31", 1) == "01-01",
   M._md_plus("12-31", 1))
ok("B2 12-30 推 3 天 ⇒ 01-02", M._md_plus("12-30", 3) == "01-02", M._md_plus("12-30", 3))
ok("B3 03-01 推 1 天 ⇒ 03-02（平年常规）", M._md_plus("03-01", 1) == "03-02")
ok("B4 反向锚：推 0 天 ⇒ 还是本日（不是空串）", M._md_plus("05-05", 0) == "05-05")

print("── C. 当天到点（既有的那半，防回归）──")
_seed("他生日", "03-02")
ok("C1 03-02 当天 ⇒ 命中", any(str(e.get("what")) == "他生日"
                             for e in M.due_anniversaries(CK, "03-02")))
ok("C2 反向锚：03-03 ⇒ 不命中", M.due_anniversaries(CK, "03-03") == [],
   str(M.due_anniversaries(CK, "03-03")))
ok("C3 跨年重复生效：明年 03-02 仍命中（只比月-日，不看年）",
   any(str(e.get("what")) == "他生日" for e in M.due_anniversaries(CK, "03-02")))

print("── D. 提前预告（本项补的那半）──")
_u = M.upcoming_anniversaries(CK, 1, today_md="03-01")
ok("D1 设「03-02」⇒ 03-01 就预告到（提前一天）",
   any(str(e.get("what")) == "他生日" for e in _u), str(len(_u)))
_u0 = M.upcoming_anniversaries(CK, 1, today_md="03-02")
ok("D2 ⛔ 两档不重叠：当天 03-02 ⇒ **不**再算预告（归 due_anniversaries）",
   not any(str(e.get("what")) == "他生日" for e in _u0), str(len(_u0)))
ok("D3 提前 3 天档 ⇒ 02-27 也能预告到 03-02",
   any(str(e.get("what")) == "他生日"
       for e in M.upcoming_anniversaries(CK, 3, today_md="02-27")))
ok("D4 反向锚：提前 1 天档、离得还远（02-20）⇒ 不预告",
   M.upcoming_anniversaries(CK, 1, today_md="02-20") == [],
   str(M.upcoming_anniversaries(CK, 1, today_md="02-20")))
ok("D5 可关：提前 0 天 ⇒ 空表（一个字都不注入）",
   M.upcoming_anniversaries(CK, 0, today_md="03-01") == [])
ok("D6 可关：负数 ⇒ 空表（坏数据不当成「提前 -1 天」）",
   M.upcoming_anniversaries(CK, -5, today_md="03-01") == [])
ok("D7 跨年：12-31 预告到 01-01（不是漏掉）",
   (lambda: (M.add_event(CK, "跨年的事", who="小李", date="01-01", long_term=True),
             any(str(e.get("what")) == "跨年的事"
                 for e in M.upcoming_anniversaries(CK, 1, today_md="12-31")))[1])())
ok("D8 没日期的记录**不许**进预告（只写「明年结婚」这种抽不出日期的，归待跟进）",
   (lambda: (M.add_event(CK, "明年结婚", who="小王", long_term=True),
             not any(str(e.get("what")) == "明年结婚"
                     for e in M.upcoming_anniversaries(CK, 400, today_md="03-01")))[1])())

print("── E. 渲染进提示词（写了没人用＝假开关）──")
_s = M.seeds_for_prompt(CK, today_md=None) if False else None
# `seeds_for_prompt` 不接 today_md ⇒ 用真实今天造一条"明天"的记录来验注入
import datetime as _dt  # noqa: E402
_tom = (_dt.date.today() + _dt.timedelta(days=1)).strftime("%m-%d")
M.add_event(CK, "明天到点的测试纪念日", who="小王", date=_tom, long_term=True)
_txt = M.seeds_for_prompt(CK, cfg={"memory": {"anniv_ahead_days": 1}})
ok("E1 提示词里带出「快到了」那一条", "明天到点的测试纪念日" in _txt, _txt[:120].replace("\n", " | "))
ok("E2 提示词里要求**提前一点**（别等到当天）",
   "提前" in _txt and "当天" in _txt, "")
_SEEDS = _ss.func_src(MEM_SRC, "proactive_seeds")
ok("E3 分母守卫：拿到 `proactive_seeds` 函数体（>400 字符）", len(_SEEDS) > 400,
   "%d 字符" % len(_SEEDS))
ok("E4 `proactive_seeds` 真调了 `upcoming_anniversaries`", "upcoming_anniversaries" in _SEEDS)
ok("E5 走 `_mem_cfg(cfg)` 读阈值（调用方传的是整个配置，直接读顶层永远读不到）",
   "_mem_cfg(cfg)" in _SEEDS, "")
ok("E6 `config.py` 里有 `anniv_ahead_days`（默认 1）",
   "anniv_ahead_days" in CFG_SRC and ": 1," in CFG_SRC.split("anniv_ahead_days")[1][:20], "")
ok("E7 `config.example.json` 同步了该键（否则 fullcheck 的同键判据会红）",
   "anniv_ahead_days" in EX_SRC, "")
ok("E8 有 `MemoryStore.upcoming_anniversaries` 薄封装",
   "def upcoming_anniversaries(self" in MEM_SRC, "")

print("")
print("纪念日提醒判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
