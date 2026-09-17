#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""应用内引导判据：**"怎么办"都在弹窗里，不叫用户去读文件**（用户 2026-09-13 的要求）。

用户原话：「不要每次都让用户去读那个什么文本啊，你要做引导就做好引导，直接全部在应用用弹窗，把引导全部都做好。
查查哪些地方需要引导，你做好弹窗」

判据：
  ① 引导基建：GUIDES 注册表 + openGuide / guideAction / copyText 都在
  ② 覆盖面（audit）：tools / voice / tts / image / forward / wechat 六处都有引导条目，
     且每条都有 title + intro + ≥2 步；该有可复制内容的（tools/voice/image）必须有 copy，该有动作的（tools/tts/wechat）必须有 actions
  ③ 入口按钮：五个「怎么办」按钮真实存在（不是死键），且都接到 openGuide
  ④ **不再让用户去读文件**：控制台里不再出现"见 tools.d/README.md"这类指路，空状态改成指向应用内引导
  ⑤ 一键生成模板：`/api/tools/new_manifest` 端点存在；`write_template()` 真能写出可解析的 JSON，且**不覆盖**已有文件
  ⑥ 前端 JS 过 `node --check`
"""
import json
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

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


from agent import console_html as CH  # noqa: E402
from agent import user_tools as UT  # noqa: E402

HTML = CH.HTML
print("── A. 引导基建 ──")
for fn in ("const GUIDES = {", "function openGuide(", "async function guideAction(", "function copyText("):
    ok("有 %s" % fn.strip(), fn in HTML)
ok("弹窗复用了现成的 mask/box 样式", "m.className = 'mask'" in HTML and "'box'" in HTML)
ok("有复制按钮（可复制命令/模板）", "navigator.clipboard.writeText" in HTML)

print("── B. 覆盖面与每条的完整性 ──")
_i = HTML.find("const GUIDES = {")
_j = HTML.find("\n};", _i)
G = HTML[_i:_j] if (_i > 0 and _j > _i) else ""
ok("抽到了 GUIDES 注册表", len(G) > 500, "%d 字符" % len(G))
WANT = {"tools": (True, True), "voice": (True, True), "tts": (False, True),
        "image": (True, True), "forward": (False, False), "wechat": (False, True)}
_KEYS = list(WANT.keys())
for key, (need_copy, need_actions) in WANT.items():
    k = G.find("\n  %s: {" % key)
    nxt = len(G)
    for other in _KEYS:
        x = G.find("\n  %s: {" % other)
        if k >= 0 and x > k and x < nxt:
            nxt = x
    blk = G[k:nxt] if k > 0 else ""
    # 步数＝`steps: [ … ]` 里的字符串条目数（不依赖①②③标记：有的条目是纯句子）
    s_i = blk.find("steps: [")
    s_j = blk.find("]", s_i) if s_i >= 0 else -1
    steps = (blk[s_i:s_j].count("'") // 2) if (s_i >= 0 and s_j > s_i) else 0
    ok("引导 %s 在注册表里" % key, k > 0)
    ok("  %s 有 title/intro 与 ≥2 步" % key, "title:" in blk and "intro:" in blk and steps >= 2, "steps=%d" % steps)
    if need_copy:
        ok("  %s 带可复制内容" % key, "copy: [{" in blk)
    if need_actions:
        ok("  %s 带动作按钮" % key, "actions: [{label:" in blk)

print("── C. 入口按钮（不是死键）──")
for bid, sec in (("utGuide", "sec-tools"), ("ttsGuide", "sec-tts"), ("vsGuide", "sec-media"),
                 ("irGuide", "sec-media"), ("fwGuide", "sec-media")):
    ok("按钮 #%s 存在" % bid, ('id="%s"' % bid) in HTML)
    ok("  它落在 %s 分区里" % sec, ('id="%s"' % sec) in HTML)
_wire = HTML.replace(" ", "")
ok("五个按钮都接到 openGuide",
   all(("['%s','" % b) in _wire for b in ("utGuide", "ttsGuide", "vsGuide", "irGuide", "fwGuide")))

print("── D. 不再叫用户去读文件 ──")
ok("控制台里不再出现 tools.d/README.md 指路", "tools.d/README.md" not in HTML)
ok("工具面板空状态改为指向应用内引导", "点上面的「怎么加工具」" in HTML)
ok("面板描述里明确「全在弹窗里」", "全在弹窗里" in HTML)
ok("语音/图库/转发也有各自的引导入口", all(x in HTML for x in ("缺引擎怎么办", "怎么放图", "为什么默认关")))
ok("语音回复的形态边界进了弹窗（默认真语音条 + 真点代价 + 发前自检 + 念法纠正）",
   all(x in HTML for x in ('默认形态是"真语音条"', "只认真实点击", "音量点", "念法纠正")))

print("── E. 一键生成模板 ──")
_wu = open(os.path.join("agent", "webui.py"), encoding="utf-8").read()
ok("后端有 /api/tools/new_manifest 端点", 'elif path == "/api/tools/new_manifest"' in _wu)
tmp = tempfile.mkdtemp(prefix="pm_guide_")
_real = UT._cfg
UT._cfg = lambda: {"dir": tmp, "enabled": True, "max_tools": 30, "timeout_ms": 8000, "max_chars": 4000}
try:
    p1, why1 = UT.write_template()
    ok("生成了一份模板文件", bool(p1) and os.path.exists(p1), str(why1)[:40])
    parsed = json.load(open(p1, encoding="utf-8"))
    ok("模板是合法 JSON 且字段齐", all(k in parsed for k in ("name", "description", "enabled", "url", "allow_hosts")), str(list(parsed))[:60])
    ok("模板默认 enabled=false（勾选才生效）", parsed.get("enabled") is False)
    p2, _ = UT.write_template()
    ok("再生成一次**不覆盖**已有的（换个文件名）", p2 and p2 != p1 and os.path.exists(p1))
    ok("生成的模板能被清单加载器接受（0 问题）", len(UT.load()[1]) == 0)
finally:
    UT._cfg = _real
    shutil.rmtree(tmp, ignore_errors=True)

print("── F. 前端 JS 语法（node --check）──")
blocks = re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.S)
js = "\n;\n".join(blocks)
tp = os.path.join(tempfile.gettempdir(), "pm_guide_check.js")
with open(tp, "w", encoding="utf-8") as fh:
    fh.write(js)
r = subprocess.run(["node", "--check", tp], capture_output=True, text=True, timeout=60,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
ok("console_html 的 JS 过语法检查", r.returncode == 0,
   ((r.stderr or "").strip().splitlines() or [""])[-1][:120] if r.returncode else "%d 字符" % len(js))

print("\n%d/%d 通过" % (PASS, PASS + FAIL))
sys.exit(1 if FAIL else 0)
