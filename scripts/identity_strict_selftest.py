# -*- coding: utf-8 -*-
"""判据：**授权档**的名字判据必须是"完全相等"，不许"互相包含"。

跑法： runtime\\python\\python.exe scripts\\identity_strict_selftest.py   退出码 0=全过 / 1=有失败

为什么要这条判据（2026-09-21，网友 v0919 追加反馈第 1 条「串群」）：
  作者原话：「我监听了2个群，在第一个群触发了之后他的回答在第2个群当中」。
  根因＝`chat_ocr.matches()` 是**互相包含即可**（为的是容忍"名字＋预览＋时间"的整行文本）：
  当前开着「KC测试」而目标是「测试」时 `matches("KC测试","测试")` → True
  ⇒ `chat_is_open` 说"就是它" ⇒ 投递**不切会话**直接把回复发出去 ⇒ 发进了另一个群。
  ⇒ 授权档（能不能发 / 是不是这个会话）只许用 `matches_strict`（归一化后完全相等）；
    `matches` 继续只用于"找行"（那里文本是整行，必须容忍包含）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WECHAT_PY = os.path.join(ROOT, "agent", "wechat.py")
OCR_PY = os.path.join(ROOT, "agent", "chat_ocr.py")

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def func_body(src, name):
    i = src.find("    def %s(" % name)
    if i < 0:
        return ""
    ends = [x for x in (src.find("\n    def ", i + 12), src.find("\n    @staticmethod", i + 12),
                        src.find("\n    # ──", i + 12)) if x > 0]
    return src[i:min(ends)] if ends else src[i:]


def code_of(src, name):
    body = func_body(src, name)
    return "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))


def main():
    from agent import chat_ocr as co

    src_w = open(WECHAT_PY, encoding="utf-8").read()
    src_o = open(OCR_PY, encoding="utf-8").read()

    # ── 副作用证明：老口径**确实**会把互为子串的两个群判成同一个（这条是修复必要性的锚）──
    ok("① 反例成立：宽松档 `matches(\"KC测试\",\"测试\")` 仍为真（找行要靠它）",
       co.matches("KC测试", "测试") is True)
    ok("① 严格档 `matches_strict(\"KC测试\",\"测试\")` 必须为假 ★（本次修的串群口子）",
       co.matches_strict("KC测试", "测试") is False)

    # ── 名字互为子串：两个方向都不许判成同一个 ──
    NESTED = [("测试", "KC测试"), ("KC测试", "测试"), ("KC", "测试"), ("测试测试", "测试"),
              ("测试", "测试测试"), ("KC测试测试", "KC测试"), ("海绵宝宝吸课堂", "海绵宝宝の吸🈲课堂"),
              ("某会话测试", "某会话"), ("O某会话测试", "某会话")]
    bad = ["%s|%s" % (a, b) for a, b in NESTED if co.matches_strict(a, b)]
    ok("② 互为子串/近名一律判**否**（%d 组）" % len(NESTED), not bad, bad)

    # ── 该认的还得认（装饰不是另一个会话）──
    KEEP = [("测试", "测试", "完全相同"), ("测试 12:03", "测试", "行文本带时间"),
            ("演示（3）", "演示", "群名后的成员数"), ("草稿测试", "测试", "草稿标记"),
            (" 测试 ", "测试", "前后空白"), ("Test", "test", "大小写"),
            ("O某会话", "某会话", "一个前导 ASCII 噪声字符（对面 r25 实测的 'OE'）")]
    miss = ["%s|%s(%s)" % (a, b, why) for a, b, why in KEEP if not co.matches_strict(a, b)]
    ok("③ 该认的仍认（时间/成员数/草稿/空白/大小写/1 字噪声，%d 例）" % len(KEEP), not miss, miss)
    # OCR 拆字（`文亻牛`）**严格档不该认**（那是"另一个名字"的形状）⇒ 只能靠 loose 兜底
    ok("③ OCR 拆字不由严格档放行（`O文亻牛传输助手` ≠ `文件传输助手`），留给 loose 兜底",
       co.matches_strict("O文亻牛传输助手", "文件传输助手") is False)

    # ── 空输入一律否（不许"没读到"被当成"就是它"）──
    ok("④ 空输入判否", co.matches_strict("", "测试") is False and co.matches_strict("测试", "") is False
       and co.matches_strict("", "") is False)

    # ── 穷举互斥：严格档为真 ⇔ 归一化后完全相等（**只允许文档里写明的装饰**：草稿标记）──
    def _canon(s):
        t = co.norm(s)
        for _p in ("草稿", "draft"):
            if t.startswith(_p):
                t = t[len(_p):]
        return t

    NAMES = ["测试", "KC测试", "KC", "测试测试", "测试(2)", "演示（3）", "演示", "草稿测试", "测试 12:03"]
    leaked = []
    for a in NAMES:
        for b in NAMES:
            want = (_canon(a) == _canon(b))
            got = co.matches_strict(a, b)
            if got != want:
                leaked.append("%s|%s want=%s got=%s" % (a, b, want, got))
    ok("⑤ 严格档为真 ⇔ 归一化后相等（%d×%d 组合无意外放行，只允许草稿标记这一种装饰）" % (len(NAMES), len(NAMES)),
       not leaked, leaked[:3])

    # ── 兜底档不许把嵌套名放过（`_header_match` 的第二档就是它）──
    ok("⑥ 宽松兜底 `loose_matches(\"KC测试\",\"测试\")` 为假（否则走格仍会走到隔壁群）",
       bool(co.loose_matches("KC测试", "测试")) is False)

    # ── 静态：三个**授权档**调用点必须用严格档，且不许再出现宽松档 ──
    b_open = code_of(src_w, "chat_is_open")
    b_screen = code_of(src_w, "_screen_only_identity")
    b_hdr = code_of(src_w, "_header_match")
    ok("⑦ 切到了三个方法体", all(x for x in (b_open, b_screen, b_hdr)))
    ok("⑦ `chat_is_open`（授权档）用 matches_strict 且不含 `_co.matches(`",
       "matches_strict(" in b_open and "_co.matches(" not in b_open)
    ok("⑦ `_screen_only_identity`（授权档）同上",
       "matches_strict(" in b_screen and "_co.matches(" not in b_screen)
    ok("⑦ `_header_match` 第一档用严格档、第二档仍是 loose 兜底",
       "matches_strict(" in b_hdr and "loose_matches(" in b_hdr and "_co.matches(" not in b_hdr)
    ok("⑦ `chat_ocr` 里两个档都在（找行继续用宽松档）",
       "def matches(" in src_o and "def matches_strict(" in src_o)

    # ── 负例：老口径的写法必须被同一组谓词判不合格 ──
    OLD = ('    def chat_is_open(self):\n'
           '        got, why = self.current_chat_name()\n'
           '        from . import chat_ocr as _co\n'
           '        if got and _co.matches(got, want):\n'
           '            return True, "当前会话就是它"\n')

    def _bad(body):
        t = body or ""
        out = []
        if "matches_strict(" not in t:
            out.append("授权档没用严格档")
        if "_co.matches(" in t:
            out.append("授权档还在用宽松档（互为子串的两个群会被判成同一个）")
        return out

    ok("⑧ 负例：老写法（授权档用 `_co.matches`）被判不合格", len(_bad(OLD)) == 2, _bad(OLD))
    ok("⑧ 正例：现在的 `chat_is_open` 合格", not _bad(b_open), _bad(b_open))

    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
