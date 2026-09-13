# -*- coding: utf-8 -*-
"""角色卡评估机制 v2 —— 唯一最高标准：是否贴合角色原本人设
（"这句话像本人说的"）。不爱说话的（林克/小骑士）不强行话多，
只要"该沉默时像本人、被问时句式贴合"即达标。

贴合度算法（0-100；≥70 合格；本地零 token）：
  A 风格指纹 40 分：专属口头禅/台词足够（≥3 条 + 语气词特色）
  B 人称一致 15 分：角色定位是"本人"（无"助手/客服/机器人"字样）
  C 示例质量 15 分：示例引用角色自己的口头禅/口吻（无"（按你的风格）"占位）
  D 表达纯粹 20 分：无 AI 模板腔（综上所述/作为一个语言模型/希望有帮助…）
  E 少言合理 10 分：沉默类不扣分（人设如此），但必须"被问能答"（必要扩展在卡内）
  扣分：通用套话/模板占位/角色冲突（声音不像本人）
"""
import sys
sys.path.insert(0, r"C:\Users\ptmou\Desktop\WX-chatbot\Persona Morph")
from agent.persona import PERSONAS

AI_TEMPLATE = ["综上所述", "总而言之", "作为一个语言模型", "希望这个回答对你有帮助",
               "有什么可以帮您", "总的来说", "首先、其次", "在当今社会"]
PLACEHOLDER = ["（按你的风格", "（用你的口气", "（摆出你的样子"]


def evaluate(key, card):
    t = card.get("text", "") or ""
    name = card.get("name") or key
    silent = any(s in t for s in ("台词极少", "沉默是金", "全拟声词", "基本只有", "话极少", "极简符号"))
    qs = [q for q in __import__("re").findall(r"[「“\"]([^」”\"]{2,24})[」”\"]", t)]
    score = 0.0
    # 内容量门槛：评分必须基于卡内容本身——空卡=0 分；过短卡片只按内容量给分（无白送）
    if len(t.strip()) < 60:
        score = max(0.0, min(30.0, len(t.strip()) * 0.5))
        return {"key": key, "name": name, "chars": len(t), "silent": silent,
                "quote": len(set(qs)), "score": round(score, 1)}
    # A 风格指纹
    q_unique = len(set(qs))
    score += min(40, 10 + q_unique * 5)
    if "口头禅" in t or "台词" in t or "口吻" in t or "说话" in t:
        score += 2
    # B 人称一致（本人 = 无助手/客服口吻）
    bad_role = any(s in t for s in ("AI助手", "客服", "机器人助手", "为您服务", "很高兴为您"))
    if not bad_role:
        score += 15
    # C 示例质量
    if any(p in t for p in PLACEHOLDER):
        score -= 10               # 示例是占位 = 不具体
    else:
        score += 15
    # D 表达纯粹（无 AI 模板腔；注意黑名单/禁令列表里出现这些词=禁止使用，不扣分）
    if "黑名单" in t or "出现即失败" in t or "不要输出" in t:
        score += 20
    else:
        hits = sum(1 for s in AI_TEMPLATE if s in t)
        score += max(0, 20 - hits * 10)
    # E 少言合理：沉默类保持人设（不扣分）；非沉默类要求句式明确（说话规则在卡内）
    if ("说话规则（群聊通用）" in t) or ("说话铁律" in t) or ("群聊" in t and "规则" in t):
        score += 10
    elif silent:
        score += 8
    return {"key": key, "name": name, "chars": len(t), "silent": silent,
            "quote": len(set(qs)), "score": round(max(0, min(100, score)), 1)}


def fit_desc(r):
    s = r["score"]
    if s >= 90:
        return "极贴合（句句像本人）"
    if s >= 75:
        return "贴合（话都像该角色）"
    if s >= 60:
        return "基本贴合（个别句子偏通用）"
    return "不够贴合（需要补足特色）"


def score_text(text: str, name: str = "自定义", key: str = "custom") -> dict:
    """对任意角色文本评分（本地零 token；输入=卡文本本身，无凭空数字）。
    自定义角色卡也走同一规则：贴合度只看"这段话本身像不像这个角色"。
    """
    return evaluate(key, {"name": name, "text": text})


if __name__ == "__main__":
    base = evaluate("xiaojingyu", PERSONAS.get("xiaojingyu", {}))
    print("基准（默认小鲸鱼）贴合度: %s (%s)" % (base["score"], fit_desc(base)))
    print("=" * 66)
    fails = []
    for k, c in PERSONAS.items():
        r = evaluate(k, c)
        if r["score"] < 70:
            fails.append(r)
        print("%-14s %-10s 字数%4d 口头禅%2d %s | %5s %s" % (
            r["key"][:14], r["name"][:10], r["chars"], r["quote"],
            "⚠沉默" if r["silent"] else "    ",
            r["score"], fit_desc(r)))
    print("=" * 66)
    print("共 %d 张；低于 70（贴合度不足）: %d 张" % (len(PERSONAS), len(fails)))
    for r in fails:
        print("  -", r["name"], r["score"])
