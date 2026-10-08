# -*- coding: utf-8 -*-
"""「事实深抽取（模型层）」判据 —— **不联网、不调真模型**（走注入的假函数）。

守的是第 118 项（M1 模型深抽取层）的三条纪律：
  · **纪律 ①：不覆盖、只并集** —— 规则层的结果必须先算好，模型层只做补，最后去重合并；
  · **纪律 ②：降级** —— 调不通 / JSON 不合法 / 字段越界 ⇒ **原样退回规则层结果**
    （⛔ 不许因为"想抽得更细"把已经抽到的弄丢）；
  · **纪律 ③：不许凭空想象** —— 模型给的 `what` 必须能在**原文**里找到依据
    （字符重叠校验，**分母守卫**：原文为空 ⇒ 一律不通过）。

用法：runtime\\python\\python.exe scripts\\facts_deep_selftest.py
"""
import io
import os
import re
import sys

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
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


import _srcslice as _ss  # noqa: E402

MEM_SRC = io.open(os.path.join(ROOT, "agent", "memory.py"), encoding="utf-8").read()
CFG_SRC = io.open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read()

print("── A. 配置面（默认关 · 可调模型 · 有上限）──")
ok("A1 分母守卫：拿到 `agent/memory.py` 正文（>20000 字符）", len(MEM_SRC) > 20000, "%d 字符" % len(MEM_SRC))
ok("A2 ⛔ 默认**关**（`deep_extract_enabled: False`）—— 它要额外调模型，不能默认花用户的钱",
   re.search(r'"deep_extract_enabled":\s*False', CFG_SRC) is not None, "")
ok("A3 可选**便宜档模型**（`deep_extract_model` 留空＝沿用主模型）",
   '"deep_extract_model"' in CFG_SRC, "")
ok("A4 有**喂进去的条数上限**与**最短长度门槛**（防提示词被塞爆 / 防抽空话）",
   '"deep_extract_max_msgs"' in CFG_SRC and '"deep_extract_min_chars"' in CFG_SRC, "")
ok("A5 配置示例与默认同键（`config.example.json` 也要有这 4 个键）",
   all(('"%s"' % k) in io.open(os.path.join(ROOT, "config.example.json"),
                               encoding="utf-8").read()
       for k in ("deep_extract_enabled", "deep_extract_model",
                 "deep_extract_max_msgs", "deep_extract_min_chars")), "")

print("── B. 纪律 ①：不覆盖、只并集 ──")
from agent import memory as M  # noqa: E402

MSGS = [{"sender_name": "小王", "text": "下周三一起吃饭"},
        {"sender_name": "小李", "text": "她生日是 3 月 2 日"},
        {"sender_name": "小张", "text": "嗯"},
        {"sender_name": "小赵", "text": "她不吃香菜也不吃葱"}]
_BASE = M.extract_facts(MSGS)
ok("B1 分母守卫：规则层能抽出东西（否则下面并集断言没意义）", len(_BASE) >= 2, "%d 条" % len(_BASE))
ok("B2 规则层**没认出**「她不吃香菜」（没命中 `_FOREVER_HINTS` 里的词）—— 这正是模型层要补的漏",
   all("香菜" not in str(x.get("what")) for x in _BASE), "")

_REAL_CFG = M.get_config


def _cfg(on, **kw):
    def _f():
        return {"memory": dict({"deep_extract_enabled": on}, **kw)}
    return _f


# 假模型：一条**有依据**的（补规则层的漏）＋ 一条**凭空编的**（必须被校验丢掉）
def _fake(p):
    return ('```json\n[{"who":"小赵","what":"她不吃香菜也不吃葱","kind":"memory","expires_at":""},'
            '{"who":"小钱","what":"他有一艘游艇停在月球上","kind":"memory","expires_at":""}]\n```')


M.get_config = _cfg(True)
try:
    _deep = M.extract_facts_deep(MSGS, llm_fn=_fake)
finally:
    M.get_config = _REAL_CFG
ok("B3 ⭐ 模型层补上了规则层漏掉的「她不吃香菜」", any("香菜" in str(x.get("what")) for x in _deep),
   "%d 条" % len(_deep))
ok("B4 ⭐ 并集：规则层原有 2 条**一条不少**（模型层是补、不是替换）",
   all(any(x.get("what") == y.get("what") for x in _deep) for y in _BASE), "")
ok("B5 ⛔ 模型**凭空编的**那条（月球游艇）**被丢掉**（原文里找不到依据）",
   not any("游艇" in str(x.get("what")) for x in _deep), "")
ok("B6 去重：同一个 `(who, what)` 只出一条",
   len({(x.get("who"), x.get("what")) for x in _deep}) == len(_deep), "%d 条" % len(_deep))

print("── C. 纪律 ②：降级（调不通 / JSON 不合法 ⇒ 退回规则层，不丢东西）──")


def _boom(p):
    raise RuntimeError("判据故意造的：模型调不通")


M.get_config = _cfg(True)
try:
    _r1 = M.extract_facts_deep(MSGS, llm_fn=_boom)
finally:
    M.get_config = _REAL_CFG
ok("C1 ⭐ 模型**抛异常** ⇒ 结果 == 规则层结果（一模一样，不丢不多）",
   _r1 == _BASE, "%d vs %d 条" % (len(_r1), len(_BASE)))

for _bad, _desc in (("这不是 JSON", "根本不是 JSON"),
                    ("[]", "空数组"),
                    ('{"a":1}', "不是数组（是对象）"),
                    ("", "空串"),
                    ("[1, 2, 3]", "数组里不是对象")):
    M.get_config = _cfg(True)
    try:
        _rb = M.extract_facts_deep(MSGS, llm_fn=(lambda p, _v=_bad: _v))
    finally:
        M.get_config = _REAL_CFG
    ok("C2 ⭐ 模型回「%s」⇒ 原样退回规则层结果" % _desc, _rb == _BASE, "%d 条" % len(_rb))

print("── D. 纪律 ③：不许凭空想象（机械校验 + 分母守卫）──")
ok("D1 ★反向锚：把 `_deep_supported` 的阈值抹成 0（恒真）⇒ 月球那条就该**混进来**"
   "（证明 B5 的判据确实在校验，不是摆设）",
   M._deep_supported("他有一艘游艇停在月球上", "她不吃香菜 下周三一起吃饭") is False, "")
ok("D2 校验放行**改了标点/补了主语**的合理改写（不算编）",
   M._deep_supported("小王约了下周三一起吃饭", "小王：下周三一起吃饭") is True, "")
ok("D3 ⛔ 分母守卫：原文为空 ⇒ **一律不通过**（无从校验就当没过）",
   M._deep_supported("随便什么", "") is False and M._deep_supported("", "随便什么") is False, "")

print("── E. 接线面：模型层挂在 `extract_facts` 之上（不是另起一套）──")
ok("E1 分母守卫：拿到 `extract_facts_deep` 函数体（>1500 字符）",
   len(_ss.func_src(MEM_SRC, "extract_facts_deep")) > 1500,
   "%d 字符" % len(_ss.func_src(MEM_SRC, "extract_facts_deep")))
ok("E2 ⭐ 它的第一件事就是**先算规则层**（`base = extract_facts(msgs)`）",
   "base = extract_facts(msgs)" in _ss.func_src(MEM_SRC, "extract_facts_deep"), "")
ok("E3 ⛔ 本模块**不直接依赖 llm**（`llm_fn` 由调用方注入 ⇒ 判据里一导入不会要求配 API）",
   "llm_fn=None" in _ss.func_src(MEM_SRC, "extract_facts_deep")
   and ("import llm" not in MEM_SRC.split("def extract_facts_deep")[0]), "")
ok("E4 ⛔ 没开开关 ⇒ **直接返回规则层**（连模型都不碰）",
   all(x in _ss.func_src(MEM_SRC, "extract_facts_deep")
       for x in ("deep_extract_enabled", "return base")), "")

M.get_config = _cfg(False)
try:
    _off = M.extract_facts_deep(MSGS, llm_fn=_boom)  # 关着的时候连 _boom 都不该被调到
finally:
    M.get_config = _REAL_CFG
ok("E5 ⭐ 开关关着 + 模型会炸 ⇒ 仍然正常返回规则层结果（**证明它压根没调模型**）",
   _off == _BASE, "%d 条" % len(_off))

print("── F. 喂进去的取舍（门槛 · 条数上限 · 不看短句）──")
ok("F1 分母守卫：`_deep_pick_msgs` 存在", callable(getattr(M, "_deep_pick_msgs", None)), "")
_rows = M._deep_pick_msgs([{"sender_name": "a", "text": "嗯"}, {"sender_name": "b", "text": "好啊"}],
                          6, 40)
ok("F2 ⛔ 短句（<6 字符）**不喂**（「嗯」「好啊」这类没有可抽的事实，喂了只会让模型瞎猜）",
   _rows == [], "%d 条" % len(_rows))
_rows2 = M._deep_pick_msgs([{"sender_name": "a", "text": "第 %d 句有内容" % i} for i in range(60)],
                           6, 10)
ok("F3 ⛔ 条数上限生效（要 10 条 ⇒ 只留**最近的 10 条**）", len(_rows2) == 10, "%d 条" % len(_rows2))
ok("F4 留下的是**最近**的那些（不是最早的）", _rows2[-1]["text"].startswith("第 59 句"),
   _rows2[-1]["text"])
_rows3 = M._deep_pick_msgs([{"sender_name": "a", "text": "x" * 300}], 6, 40)
ok("F5 ⛔ 超长（>200 字符）**不喂**（多半不是「一句事实」）", _rows3 == [], "%d 条" % len(_rows3))

print("── G. 本项原始验收（喂一段对话 ⇒ 抽两条 · 前者 30 天过期 · 后者永不过期）──")
_ACC = [{"sender_name": "阿明", "text": "下周三一起吃饭"},
        {"sender_name": "阿明", "text": "她生日是 3 月 2 日"}]
_g = M.extract_facts(_ACC)
ok("G1 ⭐ 抽出 2 条（分母守卫：条数正好）", len(_g) == 2, "%d 条" % len(_g))
_fu = [x for x in _g if x.get("kind") == "followup"]
_mm = [x for x in _g if x.get("kind") == "memory"]
ok("G2 ⭐ 「下周三一起吃饭」⇒ `followup` 且**带 30 天后的过期日**",
   len(_fu) == 1 and re.match(r"^\d{4}-\d{2}-\d{2}$", str(_fu[0].get("expires_at"))),
   str(_fu[0].get("expires_at")) if _fu else "没抽到")
ok("G3 ⭐ 「她生日是 3 月 2 日」⇒ `memory` 且 `expires_at` 为空（**永不淡出**）",
   len(_mm) == 1 and str(_mm[0].get("expires_at")) == "",
   repr(_mm[0].get("expires_at")) if _mm else "没抽到")

print("")
print("事实深抽取判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
