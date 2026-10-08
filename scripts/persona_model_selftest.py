# -*- coding: utf-8 -*-
"""「模型评分 / 模型补足」判据。

。

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
import ast
import io
import os
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
# ⚠️ 按 **AST 取跨度**，不要用"起点往后 N 个字符"的窗口：函数一旦在文件里换位置或变长变短，
#    窗口会盖到别的代码上 ⇒ 断言可能因为别处的文字而**假绿**（或反过来假红）。
_src_lines = SRC.split("\n")
_en_node = next(n for n in ast.walk(ast.parse(SRC))
                if isinstance(n, ast.FunctionDef) and n.name == "persona_ai_enrich_fn")
_enrich_blk = "\n".join(_src_lines[_en_node.lineno - 1:_en_node.end_lineno])
ok("补足先检索该角色的语录/访谈原文", "经典语录 名言" in _enrich_blk and "访谈 原话 言论" in _enrich_blk)
ok("prompt 里声明「真实资料＝唯一事实来源」", "作为唯一事实来源" in _enrich_blk)
ok("禁止自编台词冒充原话（不确定要标注（拟））",
   "禁止自编台词冒充原话" in _enrich_blk and "（拟）" in _enrich_blk)
ok("明确禁当代网络梗（V我50/6/草/yyds…）", "V我50" in _enrich_blk and "任何流行语都不行" in _enrich_blk)
ok("目标是「就是本人！」而不是「分数好看」", "就是本人" in _enrich_blk and "不要围绕夸奖/评分" in _enrich_blk)
ok("补足只许加固辨识度：已确认的真实台词/口癖必须保留（不许替换成自造内容）",
   "已经确认的真实台词/口癖/称呼方式都必须保留原样" in _enrich_blk and "指纹" in _enrich_blk)
ok("补足后有「盖住名字还认得出是谁吗」的自检", "把名字盖住" in _enrich_blk)
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
        from agent.webui import WHALE_DICT # noqa: F401  (顺手确认 webui 可导入)
        import importlib.util
        spec = importlib.util.spec_from_file_location("pm_mod", os.path.join(ROOT, "scripts", "persona_morph.py"))
        pm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pm) # 只加载模块级定义（main 不会被调用）
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
print("== E. 接线（活体）：控制台点「使用」必须把人设回写进那两个控件 ==")
# 🔴 真机 bug 的反回退锚：点「使用」后人设名不变、评分/补足仍作用到旧（默认）人设。
#    根因＝`_apply_persona` 只把值 POST 给后端、**没回写** `personaName` /
#    `personaRoleText` 两个控件，而评分/补足/联网检索读的正是控件的当前值。
#    这条锚钉住三件事：① 有回写钩子；② 钩子同时写名字**和**文本；③ 两个控件都要被赋值。
try:
    _pc = io.open(os.path.join(ROOT, "ui_qt", "panels_custom.py"), encoding="utf-8").read()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))) # scripts/ 自身（放 `_src*`）
    import _srcslice as _ss, _srcmatch as _sm # noqa: E402
    _sync = _ss.func_src(_pc, "_apply_persona")
    _do = _ss.func_src(_pc, "_do_sync_persona_ctrls")
    ok("E1 `_apply_persona` 的写盘成功分支调用了**控件回写**（不是只回显一句话）",
       _sm.has(_sync, "_sync_persona_ctrls"), "应调 `_sync_persona_ctrls(name, text)`")
    ok("E2 回写钩子**同时**写人设名与角色文本（漏一个就会复现旧 bug）",
       _sm.has(_do, "name_ctrl.setText") and _sm.has(_do, "role_text_ctrl.setPlainText"),
       "`name_ctrl.setText` + `role_text_ctrl.setPlainText`")
    ok("E3 回写钩子在控件建好之后被填充（`_sync_box[\"fn\"] = ...`）",
       _sm.has(_pc, '_sync_box["fn"] = _do_sync_persona_ctrls'),
       "两侧建桥：控件在前面定义、钩子在后填充")
    ok("E4 旧 bug 的现象写进了注释（防后人又删回写）",
       _sm.has(_pc, "点「使用」后") or _sm.has(_pc, "人设名不变"),
       "注释说明「不回写 ⇒ 名字不变/评分仍作用旧人设」")
except Exception as e: # noqa: BLE001
    skip("E. 接线（活体）", "%s: %s" % (type(e).__name__, str(e)[:60]))

# ── F. 第 112 项 ② 那半：**"当前人设"只能有一个解析口径** ─────────────────────
#   ⛔ 为什么补这一段：E 段只守住了 Qt 侧"点「使用」要把控件回写"（＝112 的 ①）；
#      而 112 的 ② 是"**各模块取的是默认人设而不是当前人设**" —— 那要守的是**解析口径的唯一性**：
#      · "取当前角色卡正文"只有 `prompt.role_text_of` 一处（含"空 ⇒ 回落内置小鲸鱼卡"）；
#      · 回复链**真的**调它，而不是自己再读一遍键；
#      · web 控制台的「使用」写的是**同一对键**（两个实现面同批）。
print("")
print("== F. 当前人设的解析口径唯一（第 112 项 ②）==")
# ⛔ 这一段是**纯静态**检查（只读源码/AST）⇒ ⛔ **不许用 `skip` 兜底**：
#   我第一次写成 `sect(...)`（这个文件根本没有这个 helper）⇒ 整段被 `except` 吞成
#   "SKIP"，而汇总行显示"23 通过 / 0 失败 / 2 跳过"**看起来是绿的** ——
#   跳过比空过更安静（空过至少还数在"通过"里，跳过连数都不算）。
#   ⇒ 静态段出错必须**红**：这里的 `except` 走 `ok(..., False)`，不是 `skip`。
try:
    import _srcslice as _ss2, _srcmatch as _sm2 # noqa: E402
    _pr = io.open(os.path.join(ROOT, "agent", "prompt.py"), encoding="utf-8").read()
    _rto = _ss2.func_src(_pr, "role_text_of")
    _bsp = _ss2.func_src(_pr, "build_system_prompt")
    ok("F1 系统提示词里取角色卡正文走**唯一实现** `role_text_of`（不是又读一遍键）",
       _sm2.has(_bsp, "role_text_of("), "`build_system_prompt` 应调 `role_text_of(cfg)`")
    ok("F2 `role_text_of` 读的就是**「使用」写入的那个键** `role_text`",
       _sm2.has(_rto, 'get("role_text")'), '`c.get("role_text")`')
    ok("F3 **回落内置卡**只有这一处（全仓 `xiaojingyu` 只出现在 `role_text_of` 里）",
       _pr.count("xiaojingyu") == 1
       and sum(io.open(os.path.join(ROOT, "agent", f), encoding="utf-8").read().count("xiaojingyu")
               for f in os.listdir(os.path.join(ROOT, "agent")) if f.endswith(".py")) == 1,
       "agent/ 下 `xiaojingyu` 出现次数")
    # ⚠️ 这两条**故意分开**（不是重复）：`_is_whale` 问的是「**用户有没有动过角色设置**」
    #   （它把"只改了名字"也算动过 ⇒ 不再注入风格参考），而 `role_text_of` 问的是
    #   「**取哪段正文**」（文本为空就用内置卡，与名字无关）⇒ 两者在"改了名字但没填自定义文本"
    #   这个组合上**本来就该给不同答案**。⇒ 下面把两者的口径各自钉住，防后人「看着像重复」就合并。
    ok("F4 `_is_whale`（要不要注入风格参考）把「只改了名字」也算作动过设置 —— 这是**设计**，别合并掉",
       _sm2.has(_pr, '_pc.get("bot_name")') and "小鲸鱼" in _pr,
       "口径：role_text 空 **且** bot_name 是空/小鲸鱼 ⇒ 才算内置")
    ok("F5 `_is_whale` 的注释写明了它的口径（含「改了名字也算动过」）—— 后人看注释就知道不是笔误",
       _sm2.has(_pr, "改了名字") or _sm2.has(_pr, "动过角色设置"),
       "注释应含「改了名字 / 填了自定义角色文本 / 选了别的卡」三者")
    _idx = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
    ok("F6 web 控制台的「使用」写的是**同一对键**（`persona.bot_name` + `persona.role_text`）",
       _sm2.has(_idx, "cfg.persona.bot_name = p.name") and _sm2.has(_idx, "persona.role_text"),
       "两个实现面同批：web 直接写 config 对象，Qt 走 /api/config + 控件回写")
except Exception as e: # noqa: BLE001
    # ⛔ **报红而不是跳过**（理由见上面那段说明）：这一段是静态检查，跑不起来就是**判据坏了**，
    #    不是"环境不满足" ⇒ 必须让人看见。
    ok("F0 段本身跑得起来（静态检查不许被吞成 SKIP）", False,
       "%s: %s" % (type(e).__name__, str(e)[:70]))

print("")
print("模型评分/补足判据：%d 通过 / %d 失败 / %d 跳过%s" % (PASS, FAIL, SKIP, "（含真机）" if LIVE else ""))
sys.exit(1 if FAIL else 0)
