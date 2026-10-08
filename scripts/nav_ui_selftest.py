#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""左侧导航判据。

而且**也要能滚动**，**像 DeepSeek 一样，可以展开看到全部名字，或者收起那些名字**。

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

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import agent.console_html as H # noqa: E402

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
# IA-4 已落地：左栏 = **17 个可见行**，其中 9 行是"合并行"（一行收 1~4 个功能项），
# 另外 8 行是单功能项。**链接一个都没删**（28 条 `href="#sec-…"`）——
# 因为 `ui_arch_selftest` 有一条用户投诉驱动的硬判据：导航链接的出现序列必须与
# `<section id="sec-…">` 的出现序列逐位相同（"划着划着导航乱跳"那条）。
# ⇒ 合并行把成员链接收进二级（`.nav-mg-bd` 里的 `.nav-mg-it`），默认收起。
# 所以这里**不能只数 `<a>`**：要分别数"可见行"与"链接总数"。
rows = re.findall(r'<a href="#sec-[a-z0-9-]+"(?: class="nav-mg-hd"[^>]*)?>', nav)
mg_rows = re.findall(r'class="nav-mg-hd"', nav)
plain = len(re.findall(r'<a href="#sec-[a-z0-9-]+">', nav))
print("① 导航项与自绘图标（可见行 %d = 合并行 %d + 单项 %d；链接共 %d 条）"
      % (len(rows), len(mg_rows), plain, len(items)))
ok("可见行 = 17（IA-4 归并后的目标；上界＝左栏滚一屏能到底）", len(rows) == 17, str(len(rows)))
ok("链接总数仍是 28（一个功能项一条，不许因为归并把入口删掉）", len(items) == 28, str(len(items)))
ok("合并行 + 单项 = 可见行（两半对得上，没有漏算）", len(mg_rows) + plain == len(rows),
   "%d + %d = %d" % (len(mg_rows), plain, len(rows)))
# 合并行的二级项必须真的在（行头写了 +N 却没二级项 ⇒ 那 N 个功能项点不到了）
_its = len(re.findall(r'class="nav-mg-it"', nav))
ok("合并行的二级项数 = 链接总数 − 可见行数（收起的是显示、不是入口）",
   _its == len(items) - len(rows), "%d vs %d" % (_its, len(items) - len(rows)))
# 二级项默认收起：`.nav-mg-bd` 基础态 display:none，只有 `.open` 才露。
ok("二级项默认收起（.nav-mg-bd 基础 display:none，.open 才显示）",
   re.search(r"\.side \.nav-mg-bd\{display:none\}", HTML) is not None
   and re.search(r"\.side \.nav-mg\.open \.nav-mg-bd\{display:block\}", HTML) is not None, "")
# 归属映射写在 DOM 上（`data-mg="sec-主项"`），不是 JS 里另抄一张表。
# ⚠️ 数**带该属性的 section 标签**，不是全文 grep `data-mg="sec-`——
#    JS 注释里也写了这个词，全文 grep 会多算一条（实测 12 vs 11）。
_mg_secs = re.findall(r'<section id="sec-[a-z0-9-]+"[^>]*data-mg="sec-[a-z0-9-]+"', HTML)
ok("归属映射来自 DOM（被并入的 section 带 data-mg=\"sec-主项\"）", len(_mg_secs) == 11, str(len(_mg_secs)))
# 收起态只剩图标 ⇒ 二级也得露出来，否则那 11 个功能项在窄栏里点不到。
ok("收起态（.tight）二级项照样显示（否则窄栏里点不到）",
   re.search(r"\.side\.tight \.nav-mg-bd\{display:block\}", HTML) is not None, "")
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
# 口径沿革（改这节前先看）：要求「右收起、靠近功能栏」⇒ 贴右缘小把手（88px）；
# **用户当面又点了两条**（「收起位置错——收起后导航要贴住功能栏，不许留空档」＋
# 「收起按钮太小，在导航里单开一栏写收起/展开、整行可点」）⇒ 小把手被换成**整行按钮**，
# 栅格列宽也跟着收（.shell.tight 64px）。下面三条按**新口径**写，旧的 88px / absolute 断言已作废。
ok("收起按钮是整行（不是贴右缘的小把手）", ".side .nav-tg{position:static;width:100%" in HTML)
ok("收起/展开文案会跟着切换", "tgEl.textContent = on ? '› 展开' : '‹ 收起'" in HTML)
ok("收起态：栅格列跟着收（否则中间白留一截）+ 图标与行距不变",
   ".shell.tight{grid-template-columns:64px 1fr;gap:8px}" in HTML
   and ".side.tight{width:100%}" in HTML
   and ".side.tight .nav a svg{width:22px" in HTML
   and ".side.tight .nav a{justify-content:center;gap:0;padding:14px 0}" in HTML)
# 收起态那颗按钮：整栏只剩图标时，它若仍按 width:100% 铺满，就会成为这一列里最大的一块
# （Qt 侧同一处病灶＝「最小宽压过 44 上限」）。须显式收窄到内容宽并居中。
# 反向控制：基础规则必须仍是 width:100%（否则"覆盖"无从谈起）。
ok("收起按钮基础态是整宽（覆盖的前提）", re.search(r"\.side \.nav-tg\{[^}]*width:100%", HTML) is not None)
ok("收起态：那颗按钮也被显式收窄并居中（不许压满整栏）",
   re.search(r"\.side\.tight \.nav-tg\{[^}]*width:auto", HTML) is not None
   and re.search(r"\.side\.tight \.nav-tg\{[^}]*margin:2px auto", HTML) is not None)

print("\n⑥ 三套索引（搜索 / 分类 / 最近）")
# 三套索引是**三件不同的事**：分类＝按主题找（nav-grp 分组）；搜索＝按名字找（navFind）；
# 最近＝回到刚才那个（navRecentGrp）。任一套缺了，都算这项没收尾。
ok("分类：导航有分组（.nav-grp 且带 data-grp）",
   len(re.findall(r'class="nav-grp" data-grp="', nav)) >= 3,
   "%d 组" % len(re.findall(r'class="nav-grp" data-grp="', nav)))
ok("搜索：有导航内搜索框 + 结果容器",
   'id="navFind"' in HTML and 'id="navFindRes"' in HTML, "")
ok("搜索：空结果有说明（不是静默什么都不显示）",
   "没找到「" in HTML and "换个说法试试" in HTML, "")
ok("★ 最近：有最近组的容器（JS 填，空时整组隐藏）",
   'id="navRecentGrp"' in HTML and 'id="navRecentBd"' in HTML, "")
ok("★ 最近：**故意不混进「分类」那 5 组**（不许用 nav-grp/data-grp —— Qt 侧按 5 组 28 项全等比对）",
   re.search(r'class="nav-recent-grp" id="navRecentGrp"', HTML) is not None
   and re.search(r'<div class="nav-grp" id="navRecentGrp"', HTML) is None, "")
ok("★ 最近：只记功能项（section），不记小项（.sub/.row）—— 记多了没重点",
   "只记**功能项" in HTML or "只记功能项" in HTML, "")
ok("★ 最近：空的时候整组隐藏（不留空壳标题）",
   "display:none" in re.search(r'<div class="nav-recent-grp" id="navRecentGrp"[^>]*>', HTML).group(0),
   "")
ok("★ 最近：状态持久化（localStorage，刷新/换页都保持）",
   "navRecentSeen" in HTML and "localStorage.setItem(RK" in HTML, "")
ok("★ 最近：分组可折叠且折叠态也持久化（与其余分组手感一致）",
   "navRecentOpen" in HTML and "nav-recent-hd" in HTML, "")
ok("★ 最近：剔除已不在本页的功能（配置不同/改名后不许留死链）",
   "secTitle[id] !== undefined" in HTML, "")
ok("★ 最近：图标沿用原导航项那一枚 SVG（不另造一套）",
   "srcA.querySelector(\"svg\")" in HTML or "querySelector('svg')" in HTML, "")
ok("★ 最近：跳转目标就是 section 的 id（点一下真的跳得到）",
   "a.href = '#' + id" in HTML, "")
ok("★ 最近：三套索引互不冒充（注释里写清各是什么）",
   "分类＝按主题找" in HTML and "搜索＝按名字找" in HTML, "")
ok("★「最近」有自己的一枚视觉标记（左侧竖线，与分类分组区分）",
   ".nav-recent::before" in HTML, "")

print("\n⑦ 归并表逐项钉住（IA-4：不许悄悄改并法）")
# 归并表写死在这里（与 `报告/PLAN.md` 第 145 项的「六、归并表」同口径）。
# 为什么逐项钉：合并把"哪个功能在哪一行"变成**隐式**的了 —— 一旦有人把成员挪到别的行，
# "我记得它在那行"这种肌肉记忆就悄悄失效，而页面看起来一切正常。这条把它变成**显式**的。
WANT_ROWS = [
    ("sec-overview", []),
    ("sec-wechat", []),
    ("sec-bot", []),
    ("sec-persona", []),
    ("sec-check", ["sec-vermat"]),
    ("sec-model", ["sec-search"]),
    ("sec-memory", ["sec-memory-set"]),
    ("sec-emoji", ["sec-community"]),
    ("sec-media", []),
    ("sec-tts", []),
    ("sec-imggen", ["sec-videogen"]),
    ("sec-tools", ["sec-poke"]),
    ("sec-sessions", ["sec-feedback"]),
    ("sec-send", []),
    ("sec-log", ["sec-server"]),
    ("sec-advanced", []),
    ("sec-ui", ["sec-json", "sec-cursor", "sec-wavefx"]),
]
# 从导航里**现读**行序。三种 `<a>` 靠 class 区分：
#   合并行头 `.nav-mg-hd` / 二级项 `.nav-mg-it` / 单项（无 class）。
# ⚠️ 不能用 `( class="nav-mg-hd")?` 这种"可选组"来判 —— 二级项与单项都匹配成空串 ⇒ 分不开（实测过一次）。
_rows = re.findall(r'<a href="#(sec-[a-z0-9-]+)"(?:\s+class="([a-z-]*)")?[^>]*>', nav)
_got, _i = [], 0
while _i < len(_rows):
    sid, cls = _rows[_i]
    if cls == "nav-mg-hd":
        mem = []
        _i += 1
        while _i < len(_rows) and _rows[_i][1] == "nav-mg-it":
            mem.append(_rows[_i][0])
            _i += 1
        _got.append((sid, mem))
    else:
        _got.append((sid, []))
        _i += 1
ok("归并表与实现逐项一致（行序 / 主项 / 成员，全等比对）", _got == WANT_ROWS,
   "; ".join("%s: %s≠%s" % (w[0], g[1], w[1])
             for g, w in zip(_got, WANT_ROWS) if g != w)
   or ("行数 %d vs %d" % (len(_got), len(WANT_ROWS))))
_mg_owner = {m: s for s, ms in WANT_ROWS for m in ms}
ok("被并入项都在（11 个，一个不少）", len(_mg_owner) == 11, str(len(_mg_owner)))
# 反向锚：主项**不许**自己也带 data-mg（那说明它被并进了别人，与上表矛盾）
ok("主项都不带 data-mg（只有被并入的才带）",
   not re.search(r'<section id="(sec-overview|sec-wechat|sec-bot|sec-persona|sec-check|sec-model|'
                 r'sec-memory|sec-emoji|sec-media|sec-tts|sec-imggen|sec-tools|sec-sessions|'
                 r'sec-send|sec-log|sec-advanced|sec-ui)"[^>]*data-mg=', HTML), "")

print("\n⑤ 前端 JS 语法（node --check）")
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
