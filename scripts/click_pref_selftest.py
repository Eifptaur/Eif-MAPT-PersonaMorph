#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据：会话行点击目标的**学习型偏好**（`agent/click_pref.py`）——不需要微信、不出网。

要守住的三件（2026-09-21 定，起因＝"版本→方法"写死表在版本一变时静默失效）：
  ① 偏好**只改顺序、不改授权**：它只能决定"先试哪个窗"，发不发仍由每枪之后的现场复核说了算；
  ② 记错了要能自愈：**连续失败 ≥2 次就丢掉偏好**，退回默认顺序（主窗优先）；
  ③ 坏数据不许挡路：文件坏了/不存在/字段缺失 ⇒ 一律按"没有偏好"处理，**永不抛**。
"""
import json
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import click_pref as CP          # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


TMP = os.path.join(tempfile.gettempdir(), "_click_pref_selftest.json")
_KEEP = CP.PATH
CP.PATH = TMP
if os.path.exists(TMP):
    os.remove(TMP)

M, R = 777, 888
NAMED = {"main": M, "render": R}

print("── A. 键：五轴（微信版本 × 适配层 × 渲染尺寸 × DPI × 后端）──")
ok("五个轴都进键", CP.key("4.1.15.8", "1.2.2.2", 2561, 1599, "PerMonitorV2", "message")
   == "4.1.15.8|1.2.2.2|2561x1599|PerMonitorV2|message")
ok("缺项用 unknown / 0 兜底，不抛", "unknown|unknown|0x0||" in CP.key())
ok("同机同尺寸 ⇒ 键相同", CP.key("4.1.15.8", "1.2.2.2", 2561, 1599, "d", "message")
   == CP.key("4.1.15.8", "1.2.2.2", 2561, 1599, "d", "message"))
ok("换尺寸 ⇒ 键不同（尺寸会重排 UI，实测过）",
   CP.key("4.1.15.8", "1.2.2.2", 2561, 1599, "d", "m") != CP.key("4.1.15.8", "1.2.2.2", 900, 680, "d", "m"))
ok("换微信版本 ⇒ 键不同", CP.key("4.1.15.8", "1.2.2.2", 1, 1, "d", "m") != CP.key("4.1.16.0", "1.2.2.2", 1, 1, "d", "m"))

print("── B. 排序：没有偏好就原样，记过谁谁先 ──")
ok("没有记录 ⇒ 默认顺序（主窗优先）", CP.order_named([M, R], "k0", NAMED) == [M, R])
CP.record_ok("k0", "render")
ok("记过 render ⇒ render 提到最前", CP.order_named([M, R], "k0", NAMED) == [R, M])
CP.record_ok("k1", "main")
ok("记过 main ⇒ main 留在最前", CP.order_named([M, R], "k1", NAMED) == [M, R])
CP.record_ok("k2", "天外飞仙")
ok("记录了不认识的目标 ⇒ 不硬塞、回默认顺序", CP.order_named([M, R], "k2", NAMED) == [M, R])
ok("named 里没有那个目标 ⇒ 回默认顺序",
   CP.order_named([M], "k1", {"main": M}) == [M] and CP.order_named([R], "k0", {"render": R}) == [R])
ok("base 为空 ⇒ 空（不抛）", CP.order_named([], "k0", NAMED) == [])
ok("默认顺序本身是 [主窗, 渲染子窗]（与 row_click_targets 一致）",
   CP.order_named([M, R], "k-none", NAMED)[0] == M)

print("── C. 自愈：连续失败 2 次丢掉偏好 ──")
CP.record_fail("k0")
ok("失败 1 次 ⇒ 偏好还在（先别急着翻脸）",
   CP.order_named([M, R], "k0", NAMED) == [R, M] and CP.peek("k0").get("failN") == 1)
CP.record_fail("k0")
ok("失败 2 次 ⇒ 丢掉偏好、退回主窗优先",
   CP.order_named([M, R], "k0", NAMED) == [M, R] and not CP.peek("k0").get("ok"))
CP.record_ok("k3", "render")
CP.record_ok("k3", "render")
ok("成功会清失败计数（okN 累加、failN 归零）",
   CP.peek("k3").get("okN") == 2 and CP.peek("k3").get("failN") == 0)
ok("别的键不受影响（k1 还是 main）",
   CP.order_named([M, R], "k1", NAMED) == [M, R] and CP.peek("k1").get("ok") == "main")

print("── D. 坏数据不许挡路（永不抛）──")
open(TMP, "w", encoding="utf-8").write("{不是 JSON")
ok("整篇坏 JSON ⇒ 按空处理", CP.order_named([M, R], "k0", NAMED) == [M, R] and CP.peek("k0") == {})
open(TMP, "w", encoding="utf-8").write('{"schema":1,"keys":[]}')
ok("keys 类型不对 ⇒ 按空处理", CP.order_named([M, R], "k0", NAMED) == [M, R])
open(TMP, "w", encoding="utf-8").write(json.dumps({"schema": 1, "keys": {"k0": "不是字典"}}))
ok("某条记录不是字典 ⇒ 不抛", CP.order_named([M, R], "k0", NAMED) == [M, R])
os.remove(TMP)
ok("文件不存在 ⇒ 默认顺序", CP.order_named([M, R], "k0", NAMED) == [M, R])
ok("stats() 不抛且带路径", isinstance(CP.stats().get("path"), str))
CP.record_ok(None, "main")                      # 故意塞个 None 键
ok("键传 None ⇒ 不抛（记了也无所谓）", isinstance(CP.stats().get("keys"), dict))

print("── E. 原子写 + 上限 ──")
CP.PATH = TMP
CP.record_ok("kok", "main")
ok("落盘的是合法 JSON", isinstance(json.load(open(TMP, encoding="utf-8")).get("keys"), dict))
ok("没有残留 .tmp（temp + os.replace）", not os.path.exists(TMP + ".tmp"))
for i in range(CP.MAX_KEYS + 6):
    CP.record_ok("kk%d" % i, "main")
ok("记录条数有上限（防文件长胖）", len(CP.stats().get("keys") or {}) <= CP.MAX_KEYS,
   "n=%s" % len(CP.stats().get("keys") or {}))

print("── F. 接线：只改顺序、不改授权（源码断言）──")
SRC = open(os.path.join("agent", "wechat.py"), encoding="utf-8").read()
BODY = SRC.split("def _click_visible_session(")[1].split("\n    def ")[0]
ok("会话行点击接了偏好（order_named）", "order_named(" in BODY and "click_pref" in BODY)
ok("成功/失败都回写（record_ok / record_fail）", "record_ok(" in BODY and "record_fail(" in BODY)
ok("**复核仍在**（授权不交给偏好）",
   "self.chat_is_open(" in BODY and "self.chat_identity_ok(" in BODY)
ok("record_ok 只出现在复核之后（拿不到正面证据不记成功）",
   BODY.index("chat_is_open(") < BODY.index("record_ok(")
   and BODY.index("chat_identity_ok(") < BODY.rindex("record_ok("))
ok("偏好读失败也只降级、不抛（except 里退回默认顺序）", "_e_cp" in BODY)
CP.PATH = _KEEP
try:
    if os.path.exists(TMP):
        os.remove(TMP)
except Exception:
    pass
shutil.rmtree(os.path.join(tempfile.gettempdir(), "_click_pref_selftest"), ignore_errors=True)

print("\n%d/%d 通过" % (PASS, PASS + FAIL))
sys.exit(1 if FAIL else 0)
