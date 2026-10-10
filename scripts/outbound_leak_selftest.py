# -*- coding: utf-8 -*-
"""outbound_leak_selftest.py —— 出站泄漏闸门判据（P0-10）

覆盖四类**只许留在本机、不许发进群**的内容：
  ① 内部故障话术（既有，回归锚）
  ② **内心独白** —— 模型把思考写进括号/标签，被当消息发出
  ③ **原始 XML / 工具调用格式** —— 原生工具调用格式泄漏进正文
  ④ **行动旁白**（2026-10-10 补）—— `(这轮就这一条，收尾了。)` 这类**无标记词**的
     自我交代（括号包裹 + 含"这轮/收尾/已回/先不"等元叙述词），真机漏网过

⛔ **反向锚**（本项目的硬纪律：判据必须能抓住"坏代码"）：
  · 断言 `sender.send_text_batch` **确实调用**统一入口 `outbound_leak_reason`
    —— 若有人把调用改回只调 `_is_internal_failure`（＝丢掉三条新拦截），本段必红；
  · 断言四类判据函数**各自独立可判**（删掉任一条，对应用例必红）。

用法：./runtime/python/python.exe -u scripts/outbound_leak_selftest.py
"""
import logging
import os

import sys

logging.disable(logging.INFO)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent import sender as S  # noqa: E402

FAILS = []
OKS = []


def chk(name, cond, extra=""):
    (OKS if cond else FAILS).append(name)
    print("  %s %s%s" % ("OK  " if cond else "FAIL", name, ("  ← " + extra) if (extra and not cond) else ""))


def main():
    print("== A. 应拦（四类泄漏） ==")
    must_block = [
        ("（心想：这次应该回得温柔一点）", "内心独白"),
        ("心想：他是不是在生气", "内心独白"),
        ("内心独白：我有点紧张", "内心独白"),
        ("<thinking>let me think</thinking>", "内心独白"),
        ("<reasoning>abc</reasoning>", "内心独白"),
        ("<tool_call>{\"name\":\"send\"}</tool_call>", "原始 XML"),
        ("<function=send_text>hello", "原始 XML"),
        ("<invoke name=\"x\">", "原始 XML"),
        ("<|im_start|>assistant", "特殊 token"),
        ("（发送没成功，这轮先不说了）", "内部故障话术"),
        ("会话投递失败", "内部故障话术"),
        # 本轮补的词表用例（原来漏的形态）
        ('<?xml version="1.0" encoding="utf-8"?>', "原始 XML"),
        ("<msg><content>hi</content></msg>", "原始 XML"),
        ('<message role="user">', "原始 XML"),
        ("作为AI，我觉得这事应该这样看", "内心独白"),
        ("作为一个AI助手，我无法回答这个", "内心独白"),
        ("思考：他现在可能有点急", "内心独白"),
        ("分析：这句话要回得短一点", "内心独白"),
        ("<analysis>用户想要的是…</analysis>", "内心独白"),
        # ⚡ 第四类：真机漏网的行动旁白（无标记词，整段一对括号 + 元叙述词）
        #    来源＝用户转述的群友实测：模型把"我这一轮怎么处理"当消息发出去了
        ("(这轮就这一条，收尾了。)", "行动旁白"),
        ("(等对方回，先不追着发。)", "行动旁白"),
        ("（已回，结束）", "行动旁白"),
        ("(收工)", "行动旁白"),          # 4 字符 —— 曾因 len>4 漏网
        ("（先不回了）", "行动旁白"),
        ("[本轮就到这里]", "行动旁白"),   # 方括号变体也要认
        ("(本轮不再追发，等他回)", "行动旁白"),
    ]
    for t, kind in must_block:
        r = S.outbound_leak_reason(t)
        chk("拦·%s｜%s" % (kind, t[:28]), bool(r), "没拦住")

    print("== B. 应放行（不许误杀） ==")
    must_pass = [
        "我今天心情很好，想了想还是发个表情吧",
        "这个 < 符号是数学里的",
        "<3 比心",
        "早上好呀！",
        "你说的那个我想想",
        "内心强大的人不会在意这些",
        "我开始思考人生了",
        "工具挺好用的",
        # 本轮补的防误杀用例（每一条都对应一个"差一点就被词表拦住"的正常话）
        "想法不错，就这么办",          # 裸"想法"已从词表移除（它在句首是正常话）
        "你的想法挺好的",
        "这事的分析：我觉得可行",      # "分析："不在句首 ⇒ 不该拦
        "作为朋友我支持你",            # "作为"≠"作为AI"
        "我需要先走了，回头聊",        # 明确不该被"我需要先…"这类泛词误杀
        "这事得从多个角度分析：利弊都有",
        # ⚡ 第四类的防误杀：括号包裹的**正常口语**绝不能被拦（这是新闸最大的风险）
        "（笑）",                      # 只有括号没元叙述词 ⇒ 放行
        "（小声嘀咕）",
        "(挠头)",
        "（突然想到）那我们去吃火锅吧",   # 括号在句首但不包整段 ⇒ 放行
        "结束了记得喊我",              # 含"结束"但没括号 ⇒ 放行
        "我先不回家了",                # 含"先不"但没括号 ⇒ 放行
        "（群主）你好",                # 括号在句首，包的是称呼不是行动
        "（笑）（其实我有点紧张）",      # 两个独立括号 ⇒ 不算"一对包整段"
    ]
    for t in must_pass:
        r = S.outbound_leak_reason(t)
        chk("放·%s" % t[:30], not r, "被误拦：%s" % r)

    print("== C. 四类判据各自独立（删一条必红） ==")
    chk("内部故障话术判据独立", bool(S._is_internal_failure("会话投递失败")))
    chk("内心独白判据独立", bool(S._is_thought_leak("（心想：x）")))
    chk("原始XML判据独立", bool(S._is_raw_xml("<tool_call>")))
    chk("行动旁白判据独立", bool(S._is_paren_aside("(收工)")))
    chk("四类对无关文本都不误报",
        not (S._is_internal_failure("早上好") or S._is_thought_leak("早上好")
             or S._is_raw_xml("早上好") or S._is_paren_aside("早上好")))
    # ⚡ 行动旁白是"两道叠加"判据：单看括号、单看词都不该命中 ⇒ 各测一次
    chk("只有括号没元叙述词 ⇒ 不命中", not S._is_paren_aside("（笑）"))
    chk("只有元叙述词没括号 ⇒ 不命中", not S._is_paren_aside("这轮就这一条，收尾了。"))

    print("== D. 反向锚：统一入口确实接在发送链上 ==")
    src = open(os.path.join(_ROOT, "agent", "sender.py"), encoding="utf-8").read()
    chk("send_text_batch 调用 outbound_leak_reason", "outbound_leak_reason(_t)" in src,
        "调用点被改回了？")
    chk("闸门函数仍存在", all(n in src for n in
                              ("def _is_internal_failure", "def _is_thought_leak",
                               "def _is_raw_xml", "def _is_paren_aside")),
        "有函数被删")
    # ⚡ 反向锚：新增的第三类必须**真接在统一入口上**，不能只是"写了个函数没人调"
    chk("行动旁白已接进 outbound_leak_reason",
        "_is_paren_aside(text)" in src or "_is_paren_aside(_t)" in src,
        "函数写了但没接进闸门 ⇒ 等于没做")
    chk("原内部故障调用点已收拢（不再单独调 _is_internal_failure(_t)）",
        "_is_internal_failure(_t)" not in src,
        "还留着旧调用点 ⇒ 两条新拦截会被绕过")
    chk("控制台读数仍可用", isinstance(S.outbound_gate_status(), dict))

    print()
    print("== 汇总：%d 通过 / %d 失败 ==" % (len(OKS), len(FAILS)))
    if FAILS:
        for f in FAILS:
            print("   FAIL:", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
