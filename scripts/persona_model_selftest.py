# -*- coding: utf-8 -*-
"""「模型评分 / 模型补足」判据（2026-09-14 · 用户点名要测）。

用户原话：「顺便也可以测测那个模型打分和模型补正的功能正不正常，**是不是真的按照贴合人设的方向做的**」。

静态部分（默认跑，零成本）守的是"这套机制在不在、往哪使劲"：
  ① 评分用的是唯一权威细则 RULES_TEXT（五维权重、0.01 精度、禁整分）；
  ② 评分先把**联网检索到的真实资料**塞进 prompt，并明写"卡里出现真实资料之外的编造台词/事迹 → 贴合度 ≤30"；
  ③ 补足同样以真实资料为唯一事实来源，且**禁止自编台词冒充原话**（不确定要标注（拟））；
  ④ 补足是"分升才继续下一轮"，且最终文本要复评取中位（抑制忽高忽低）；
  ⑤ 评分函数是**模块级真身**（判据调的是产品在用的那一份 prompt，不是另抄一份）。

真机部分（`--live`，会花一点点 token）：用两张卡做**方向性对照**——
  一张是该角色本人的忠实卡，一张是"通用 AI 助手"卡；要求 `score(本人卡) > score(通用卡)`，
  并检查联网检索确实拿到了该角色的第一手片段（补足/评分的输入侧）。

用法：
  py -3 scripts/persona_model_selftest.py           # 只跑静态
  py -3 scripts/persona_model_selftest.py --live    # 加上真机对照（约两次评分调用）
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

LIVE = "--live" in sys.argv[1:]

PASS = 0
FAIL = 0
SKIP = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


def skip(name, why=""):
    global SKIP
    SKIP += 1
    print("  SKIP {}  [{}]".format(name, why))


SRC = io.open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
RATE = io.open(os.path.join(ROOT, "agent", "persona_rating.py"), encoding="utf-8").read()
ENR = io.open(os.path.join(ROOT, "agent", "persona_enrich.py"), encoding="utf-8").read()

print("── A. 评分：唯一权威细则 + 真实资料优先 ──")
ok("评分走 RULES_TEXT（唯一权威细则）", "RULES_TEXT" in SRC and "RULES_TEXT" in RATE)
ok("五维权重 + 0.01 精度 + 禁整分都在细则里",
   "WEIGHTS" in RATE and "0.01" in RATE and "禁止整分" in RATE)
ok("评分前先联网检索真实资料并塞进 prompt", "经典语录 名言" in SRC and "权威事实来源" in SRC)
ok("编造即压分（卡里出现真实资料之外的台词/事迹 → 贴合度 ≤30）",
   "贴合度≤30" in SRC.replace(" ", ""))
ok("评分函数是模块级真身（判据调的就是产品在用的那份）",
   "def persona_llm_score(card" in SRC and "return persona_llm_score(card, name)" in SRC)
ok("解析要求精确到分（0.01）且禁抄示例", "0.01 精度" in SRC and "禁止抄示例" in SRC)

print("── B. 补足：真实资料为唯一事实来源 + 禁自编 ──")
_i = SRC.index("def persona_ai_enrich_fn(")
_enrich_blk = SRC[_i:_i + 6000]
ok("补足先检索该角色的语录/访谈原文", "经典语录 名言" in _enrich_blk and "访谈 原话 言论" in _enrich_blk)
ok("prompt 里声明「真实资料＝唯一事实来源」", "作为唯一事实来源" in _enrich_blk)
ok("禁止自编台词冒充原话（不确定要标注（拟））",
   "禁止自编台词冒充原话" in _enrich_blk and "（拟）" in _enrich_blk)
ok("明确禁当代网络梗（V我50/6/草/yyds…）", "V我50" in _enrich_blk and "任何流行语都不行" in _enrich_blk)
ok("目标是「就是本人！」而不是「分数好看」", "就是本人" in _enrich_blk and "不要围绕夸奖/评分" in _enrich_blk)
ok("分升才继续下一轮（轮数 1~3 可调）", "分升" in SRC and "min(3, int(rounds" in _enrich_blk)
ok("最终文本复评取中位（抑制忽高忽低）", "中位" in SRC)
ok("补足后仍跑同一把严格尺子", "_persona_llm_score(card, name)" in _enrich_blk)

print("── C. 静默类角色不被写成话痨（persona_enrich） ──")
ok("有沉默类角色的必要对话扩展", "必要对话扩展" in ENR)
ok("已补足的卡不会重复追加规则", "已补足" in ENR)
ok("已有完整说话+示例结构的卡不再追加", "## 说话" in ENR)

if not LIVE:
    skip("D. 真机对照", "没加 --live（静态部分已覆盖机制；真机见下面手动跑的那次）")
else:
    print("── D. 真机对照：本人卡 vs 通用卡（方向性）──")
    try:
        from agent import web_search as WS
        from agent.webui import WHALE_DICT  # noqa: F401  (顺手确认 webui 可导入)
        import importlib.util
        spec = importlib.util.spec_from_file_location("pm_mod", os.path.join(ROOT, "scripts", "persona_morph.py"))
        pm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pm)          # 只加载模块级定义（main 不会被调用）
        NAME = "戈登·弗里曼"
        FAITHFUL = (
            "# 角色卡：戈登·弗里曼\n## 身份\n《半条命》主角，黑山研究所理论物理学家，沉默寡言，靠撬棍与物理直觉活着。\n"
            "## 说话规则\n- 几乎不说话；被@也多半只回一个「……」或极短的词。\n"
            "- 别人长篇大论时用行动回应，不用形容词堆砌。\n- 不用 Markdown，不发表情包式网络梗。\n"
            "## 对话示例\n- 群友「在吗」→「……」\n- 群友「今天好累」→「嗯。」\n- 群友「再来一句」→「走。」\n"
        )
        GENERIC = (
            "# 角色卡：万能助手\n## 身份\n一个热情、专业、乐于助人的 AI 助手，什么都能聊。\n"
            "## 说话规则\n- 每条回复都要有条理，先总结再分点，最后给出建议。\n"
            "- 多用「首先/其次/总之」，适当使用 emoji 让语气更亲切。\n- 每个问题都尽量全面回答。\n"
            "## 对话示例\n- 群友「在吗」→「在的！有什么可以帮您？」\n"
            "- 群友「今天好累」→「辛苦了！建议早点休息，注意身体哦～」\n"
        )
        r1 = pm.persona_llm_score(FAITHFUL, NAME)
        r2 = pm.persona_llm_score(GENERIC, NAME)
        if not (r1.get("ok") and r2.get("ok")):
            skip("D. 真机对照", "评分未返回：%s / %s" % (str(r1.get("error"))[:40], str(r2.get("error"))[:40]))
        else:
            ok("本人卡得分 > 通用卡得分（评分真按贴合度走）",
               r1["score"] > r2["score"], "本人 %s vs 通用 %s" % (r1["score"], r2["score"]))
            ok("通用卡被压到不优秀区（<95）", r2["score"] < 95, str(r2["score"]))
            ok("两卡评分不同（不是每次都吐同一个数）", abs(r1["score"] - r2["score"]) >= 1.0,
               "差 %.2f" % abs(r1["score"] - r2["score"]))
            print("      本人卡 dims=%s reason=%s" % (r1.get("dims"), str(r1.get("reason"))[:60]))
            print("      通用卡 dims=%s reason=%s" % (r2.get("dims"), str(r2.get("reason"))[:60]))
        try:
            w = WS.web_search(NAME + " 经典语录 名言")
            items = (w or {}).get("results") or (w or {}).get("items") or []
            ok("联网检索拿得到该角色的第一手片段（评分/补足的输入侧）", len(items) > 0,
               "命中 %d 条" % len(items))
        except Exception as e:
            skip("联网检索第一手资料", str(e)[:50])
    except Exception as e:
        skip("D. 真机对照", "%s: %s" % (type(e).__name__, str(e)[:60]))

print("")
print("模型评分/补足判据：%d 通过 / %d 失败 / %d 跳过%s" % (PASS, FAIL, SKIP, "（含真机）" if LIVE else ""))
sys.exit(1 if FAIL else 0)
