# -*- coding: utf-8 -*-
"""prob_normalize_selftest.py —— 「概率配置归一化」判据（PLAN 第 13 项）

⛔ 为什么要有它（**这是真实缺陷，不是洁癖**）：
  界面上"概率"那一格是**自由文本框** —— `ui_qt/panels_custom.py` 用
  `v_prob.setText(str(cur.get("probability", "")))` 把配置值**原样显示**，用户可以随便填。
  用户看到"概率"二字，很自然就填 `90`。而代码原先写的是
  `prob = float(cfg.get("reply_probability", 0.9))` ⇒ 拿到 `90.0`
  ⇒ 再与 `random.random()`（恒 `<1`）比较 ⇒ **"90% 概率"变成"每次必做"**（与意图相反）。
  同类写法 `max(0.0, min(1.0, x))` 也不好：填 `90` 会被截成 `1.0`，**同样是恒真**。

本判据守两件事：
  ① `normalize_prob` 的**边界口径**：`>1` 按百分数 ÷100；**合法 `0` 不许被抬成 default**；
  ② **反向锚**：四处调用点都不许再出现 `float(...probability...)` 直转
     （若有人改回去，本段必红）。

用法：./runtime/python/python.exe -u scripts/prob_normalize_selftest.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent.config import normalize_prob  # noqa: E402

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


print("── A. 边界口径：填 90 必须变成 0.9，填 0 必须还是 0 ──")
_CASES = [
    # (输入, 期望, 说明)
    (90, 0.9, "用户在框里填 90（想表达 90%）"),
    (100, 1.0, "填 100 ⇒ 一定触发"),
    (0.9, 0.9, "原本就用 0~1 口径 ⇒ 不变"),
    (1, 1.0, "1 就是 100%，不许再除一次"),
    (1.5, 0.015, "1.5 按 1.5% 理解（与 UI 的 0~100 口径一致）"),
    (0, 0.0, "★ 填 0 就是『永不』—— 不许把它当『没配』抬成默认值"),
    (0.0, 0.0, "同上（浮点 0）"),
    (-1, 0.0, "负数按 0"),
    ("90", 0.9, "字符串（配置里可能是文本）"),
    ("0.5", 0.5, "字符串小数"),
    ("abc", 0.7, "非数值 ⇒ 用 default"),
    (None, 0.7, "None ⇒ 用 default"),
    ("", 0.7, "空串 ⇒ 用 default"),
    (float("nan"), 0.0, "NaN ⇒ 0（不许让 NaN 混进比较）"),
]
for _v, _exp, _why in _CASES:
    _got = normalize_prob(_v, 0.7)
    ok("%-14r ⇒ %-6s（%s）" % (_v, _got, _why), abs(_got - _exp) < 1e-9,
       "期望 %s" % _exp)

print("── B. 反向锚：四处调用点不许再直接 float() 强转 ──")
_SRC_WX = open(os.path.join(_ROOT, "agent", "wechat.py"), encoding="utf-8").read()
_SRC_SD = open(os.path.join(_ROOT, "agent", "sender.py"), encoding="utf-8").read()
_SRC_BH = open(os.path.join(_ROOT, "agent", "behavior.py"), encoding="utf-8").read()

ok("wechat：回拍概率已过 normalize_prob", "normalize_prob(cfg.get(\"reply_probability\"" in _SRC_WX)
ok("wechat：主动拍概率已过 normalize_prob", "normalize_prob(cfg.get(\"active_probability\"" in _SRC_WX)
ok("sender：引用回复概率已过 normalize_prob",
   "normalize_prob(cfg.get(\"quote_reply_probability\"" in _SRC_SD)
ok("behavior：人性化触发概率已过 normalize_prob",
   "normalize_prob(spec.get(\"probability\"" in _SRC_BH)

_BAD = [
    ("wechat.py", _SRC_WX, "float(cfg.get(\"reply_probability\""),
    ("wechat.py", _SRC_WX, "float(cfg.get(\"active_probability\""),
    ("sender.py", _SRC_SD, "float(cfg.get(\"quote_reply_probability\""),
    ("behavior.py", _SRC_BH, "float(spec.get(\"probability\""),
]
for _f, _src, _needle in _BAD:
    ok("%s 不再有 `%s`" % (_f, _needle[:34]), _needle not in _src,
       "有人改回了直转 ⇒ 『填 90 ⇒ 恒真』的缺陷会复活")

ok("`normalize_prob` 是 config 模块里的公开函数（不是各处自己抄一份）",
   "def normalize_prob(" in open(os.path.join(_ROOT, "agent", "config.py"),
                                 encoding="utf-8").read())

print()
print("== 汇总：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
