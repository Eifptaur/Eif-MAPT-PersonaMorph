# -*- coding: utf-8 -*-
"""控制台勾选框（显示层自研）判据 —— 不需要微信、不需要起服务。

用户 2026-09-15 原话：「**勾选框你可以做设计吗，现在还是那个白色的勾选框，我希望能好看一点**」。
口径（与项目「显示层自研」同源）：不用浏览器默认控件外观、不用字体字形/图片/emoji 当勾，
三态齐、颜色全走主题变量、换尺寸不移位。

守七条：
  A 不再依赖浏览器默认外观（`appearance:none`），也不再把 `accent-color` 当唯一手段
  B 勾是 **CSS 画的**（`::after` + border + rotate），不是字形/图片/emoji
  C 三态齐：悬停 / 选中 / 禁用，且有键盘焦点环
  D 勾与横杠用**百分比定位**（控件 16px 或 20px 都居中）
  E 颜色只引用主题变量（不写死颜色），三套主题自动跟随
  F 样式块花括号配平（历史上 `.chip` 孤儿声明行就是这么坏掉的）
  G 页面里真的有一堆勾选框（这条 CSS 有用武之地；防"CSS 写了但没人用"的假绿）
"""
from __future__ import annotations

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name, (" · " + extra) if extra else ""))


src = io.open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
# 只取勾选框那一族规则（从自研注释到 focus-visible 之后）
_i = src.find("自研勾选框")
# ⚠️ 取样窗口必须**收到本块结尾**：第一版写死 +2600 字符，把隔壁 `.pri` 的渐变色也吃进来了
#    ⇒ E1 假红（自检自己错，不是样式错）。末尾锚点＝本块最后一条规则。
_end = src.find("input[type=checkbox]:disabled", _i)
_end = (src.find("}", _end) + 1) if _end > 0 else (_i + 1200)
_SEG = src[_i:_end] if _i > 0 else ""

print("[A] 不用浏览器默认外观")
ck("A0 取样窗口够长且落在本块内（判据自己没瞎）",
   _i > 0 and 600 <= len(_SEG) <= 1600, "段长 %d" % len(_SEG))
ck("A1 有自研注释标记（后来人能看懂为什么写这段）", _i > 0)
ck("A2 appearance:none 到位（否则默认白方块还在）", "appearance:none" in _SEG)
ck("A3 不再把 accent-color 当 checkbox 的唯一手段",
   "input[type=checkbox],input[type=radio]){accent-color" not in src
   and ":where(input[type=radio]){accent-color:var(--blue)}" in src)

print("\n[B] 勾是 CSS 画的")
ck("B1 选中态用 ::after 生成勾", ":checked)::after" in _SEG and 'content:""' in _SEG)
ck("B2 勾用 border + rotate 画（不是字形/图片）",
   "border-width:0 2px 2px 0" in _SEG and "rotate(45deg)" in _SEG)
ck("B3 没有图片/矢量字体来当勾",
   "background-image:url" not in _SEG and "font-family" not in _SEG)
_emo = re.findall(r"[\U0001F300-\U0001FAFF\u2705\u2714\u2716\u2611\u2610]", _SEG)
ck("B4 勾选框这段里没有 emoji/对勾字符", not _emo, str(_emo[:5]))

print("\n[C] 三态与可达性")
for st, cn in ((":hover", "悬停"), (":checked", "选中"), (":disabled", "禁用"),
               (":focus-visible", "键盘焦点")):
    ck("C %s（%s）有独立样式" % (st, cn), ("input[type=checkbox]%s)" % st) in _SEG)

print("\n[D] 换尺寸不移位")
ck("D1 勾用百分比定位", "width:28%" in _SEG and "height:56%" in _SEG)
ck("D2 勾用 translate(-50%,…) 居中（不写死像素左边距）",
   "translate(-50%,-58%)" in _SEG)
ck("D3 半选态（indeterminate）也画了", ":indeterminate" in _SEG)

print("\n[E] 颜色只引用主题变量")
_colors = re.findall(r"#[0-9A-Fa-f]{3,6}", _SEG)
ck("E1 没有写死十六进制颜色（除白色勾 #fff）",
   all(c.lower() in ("#fff", "#ffffff") for c in _colors), str(_colors[:6]))
ck("E2 用到了主题变量（--blue/--blue2/--blue-soft/--input-bg/--input-bd）",
   all(v in _SEG for v in ("var(--blue)", "var(--blue2)", "var(--blue-soft)",
                           "var(--input-bg)", "var(--input-bd)")))

print("\n[F] 样式块配平（历史事故：.chip 孤儿声明行）")
ck("F1 花括号配平（整个控制台）", src.count("{") == src.count("}"),
   "{%d }%d" % (src.count("{"), src.count("}")))
ck("F2 全文没有裸的孤儿声明行（规则外出现 `属性:值;`）",
   not re.search(r"\n\}\s*\n\s*[a-z-]+:\s*[^;{\n]+;\s*\n\s*[.#:a-zA-Z]", src))

print("\n[G] 页面里真的用得上")
_cks = len(re.findall(r'type="checkbox"', src))
ck("G1 勾选框数量 ≥ 30（防『写了 CSS 没人用』的假绿）", _cks >= 30, "%d 个" % _cks)
_n_cfg = len(re.findall(r'type="checkbox"[^>]*data-cfg=', src))
ck("G2 data-cfg 型勾选框也在其中（配置项与它同族）", _n_cfg >= 10, "%d 个" % _n_cfg)

print("\n==== 勾选框判据：%d 通过 / %d 失败 ====" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAIL " + b)
sys.exit(1 if BAD else 0)
