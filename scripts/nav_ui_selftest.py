#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""左侧导航判据（用户 2026-09-13 定稿方向 B 的四条要求）。

用户原话：「方案 B · 左导航 + 右工作区（密度最高），**左边的导航每个图标都要自己设计一遍**，
而且**也要能滚动**，**像 DeepSeek 一样，可以展开看到全部名字，或者收起那些名字**。
导航的名字尽量起得简短，**4 个字或者 5 个字内**，做完这个，准备交接吧，115 轮了」

判据：①每个导航项都是自绘 inline SVG（不是 emoji、不是字体图标、不是图片）；
②名字 ≤5 字且没有 emoji；③导航可滚动（CSS overflow-y:auto + max-height）；
④有收起/展开按钮且状态持久化（localStorage）；
⑤JS 过语法检查（node --check）。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import agent.console_html as H  # noqa: E402

HTML = H.HTML
PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


# 取出 <nav ...>…</nav> 那一段
m = re.search(r'<nav class="nav" id="nav">(.*?)</nav>', HTML, re.S)
nav = m.group(1) if m else ""
items = re.findall(r'<a href="(#sec-[a-z0-9-]+)"[^>]*>(.*?)</a>', nav, re.S)
print("① 导航项与自绘图标（共 %d 项）" % len(items))
ok("导航项 ≥ 20（原来 23 项）", len(items) >= 20, str(len(items)))
ok("每一项都有 inline SVG", all("<svg" in body and "</svg>" in body for _h, body in items),
   str(sum(1 for _h, b in items if "<svg" in b)))
ok("每一项都有 viewBox（可缩放、不靠位图）", all('viewBox="0 0 16 16"' in body for _h, body in items))
ok("图标用 currentColor 描边（跟随主题色）", all("currentColor" in body for _h, body in items))
ok("没有用 emoji 当图标", not re.search(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", nav), "无 emoji")

print("② 名字简短（≤5 字）")
labels = [re.search(r'<span class="lb">(.*?)</span>', b).group(1) for _h, b in items
          if re.search(r'<span class="lb">(.*?)</span>', b)]
ok("每一项都有名字", len(labels) == len(items), "%d/%d" % (len(labels), len(items)))
over = [x for x in labels if len(x) > 5]
ok("所有名字 ≤5 字", over == [], "超长：" + "、".join(over) if over else "最长 " + max(labels, key=len))
ok("没有 emoji 混进名字", not any(re.search(r"[\U0001F300-\U0001FAFF]", x) for x in labels))

print("③ 可滚动")
ok("导航容器 overflow-y:auto", re.search(r"\.side \.nav\{[^}]*overflow-y:auto", HTML) is not None)
ok("有高度上限（max-height）才会真出滚动条", re.search(r"\.side \.nav\{[^}]*max-height:calc\(", HTML) is not None)
ok("细滚动条样式（与整体观感一致）", ".side .nav::-webkit-scrollbar" in HTML)

print("④ 收起/展开（像 DeepSeek）")
ok("有收起/展开按钮", 'id="navToggle"' in HTML and "nav-tg" in HTML)
ok("收起时隐藏名字（.side.tight .nav .lb{display:none}）",
   re.search(r"\.side\.tight \.nav a \.lb\{display:none\}", HTML) is not None)
ok("收起时导航变窄（.side.tight{width:...}）", re.search(r"\.side\.tight\{width:", HTML) is not None)
ok("状态持久化（localStorage）", "localStorage.setItem('navTight'" in HTML and "localStorage.getItem('navTight')" in HTML)
# 口径沿革（改这节前先看）：2026-09-14 要求「右收起、靠近功能栏」⇒ 贴右缘小把手（88px）；
# **2026-09-15 用户当面又点了两条**（「收起位置错——收起后导航要贴住功能栏，不许留空档」＋
# 「收起按钮太小，在导航里单开一栏写收起/展开、整行可点」）⇒ 小把手被换成**整行按钮**，
# 栅格列宽也跟着收（.shell.tight 64px）。下面三条按**新口径**写，旧的 88px / absolute 断言已作废。
ok("收起按钮是整行（不是贴右缘的小把手）", ".side .nav-tg{position:static;width:100%" in HTML)
ok("收起/展开文案会跟着切换", "tgEl.textContent = on ? '› 展开' : '‹ 收起'" in HTML)
ok("收起态：栅格列跟着收（否则中间白留一截）+ 图标与行距不变",
   ".shell.tight{grid-template-columns:64px 1fr;gap:8px}" in HTML
   and ".side.tight{width:100%}" in HTML
   and ".side.tight .nav a svg{width:22px" in HTML
   and ".side.tight .nav a{justify-content:center;gap:0;padding:14px 0}" in HTML)

print("⑤ 前端 JS 语法（node --check）")
node = shutil.which("node")
if not node:
    ok("node 可用（拿它做语法检查）", False, "找不到 node")
else:
    scripts = re.findall(r"<script>(.*?)</script>", HTML, re.S)
    ok("页面里有脚本块", len(scripts) > 0, str(len(scripts)))
    bad = []
    for i, js in enumerate(scripts):
        p = os.path.join(tempfile.gettempdir(), "navjs_%d.js" % i)
        with open(p, "w", encoding="utf-8") as f:
            f.write(js)
        r = subprocess.run([node, "--check", p], capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            bad.append("script[%d]: %s" % (i, (r.stderr or "").strip().splitlines()[-1:]))
    ok("所有脚本块语法正确", bad == [], "；".join(bad)[:160])

print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
