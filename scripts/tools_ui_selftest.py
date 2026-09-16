#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「工具与插件」控制台面板判据（能力 D 的第三块）：清单能看、能勾、坏清单能看见。

判据（不需要微信、不出网）：
  ① 分区与导航：`#sec-tools` + `data-sec`，且在 `sec-wechat` 之前闭合
  ② 设置项：user_tools.enabled / dir / max_tools / timeout_ms / max_chars 都在分区内 + 保存按钮
  ③ 文案三类：边界（只发 HTTP、不执行本地代码、内网永远拒）· 空状态（还没有自定义工具时说什么）·
     坏清单会被逐条列出（不静默）
  ④ 渲染与交互：JS 统一填 #utGlobals / #utList / #utProblems；勾选框打到 `/api/tools/toggle`；
     「重新加载清单」打到 `/api/tools/reload`
  ⑤ 后端接线：`/api/status` 带 `user_tools` 段；两个端点都在
  ⑥ **行为**：`set_enabled()` 真能改清单文件里的 enabled（临时目录演练；名字不存在时拒绝）
  ⑦ 前端 JS 过 `node --check`
"""
import json
import os
import shutil
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
print("── A. 分区与导航 ──")
ok("导航有 #sec-tools 链接", 'href="#sec-tools"' in HTML and "工具与插件" in HTML)
ok("有 sec-tools 分区且带 data-sec", 'id="sec-tools" class="card" data-sec' in HTML)
_i, _j = HTML.find('id="sec-tools"'), HTML.find('id="sec-wechat"')
_seg = HTML[_i:_j] if (_i > 0 and _j > _i) else ""
ok("分区在 sec-wechat 之前且已闭合", bool(_seg) and "</section>" in _seg)

print("── B. 设置项 ──")
for key in ("user_tools.enabled", "user_tools.dir", "user_tools.max_tools",
            "user_tools.timeout_ms", "user_tools.max_chars"):
    ok("设置项 %s 在分区里" % key, ('data-cfg="%s"' % key) in _seg)
ok("有保存按钮（工具与插件）", "保存设置（工具与插件）" in _seg)
ok("有「重新加载清单」按钮", "utReload" in _seg and "重新加载清单" in _seg)

print("── C. 三类文案 ──")
ok("边界：只发 HTTP", "只发 HTTP" in _seg)
ok("边界：不执行任何本地代码", "不执行任何本地代码" in _seg)
ok("边界：内网/本机永远拒绝", "内网/本机地址永远拒绝" in _seg)
ok("空状态说清怎么开始", "还没有自定义工具" in HTML)
ok("坏清单会被列出来（不静默）", "坏清单会在下面逐条列出来" in _seg)
ok("说明调用次数来自唯一分发点", "唯一分发点" in _seg)

print("── D. 渲染与交互 ──")
for el in ("utGlobals", "utList", "utProblems"):
    ok("JS 会填 #%s" % el, ("$('%s')" % el) in HTML)
ok("勾选框打到 /api/tools/toggle", "/api/tools/toggle" in HTML)
ok("重新加载打到 /api/tools/reload", "/api/tools/reload" in HTML)

print("── E. 后端接线 ──")
_wu = open(os.path.join("agent", "webui.py"), encoding="utf-8").read()
ok("/api/status 带 user_tools 段", 'st["user_tools"] = _ut2.snapshot()' in _wu)
ok("/api/tools/reload 端点存在", 'elif path == "/api/tools/reload"' in _wu)
ok("/api/tools/toggle 端点存在", 'elif path == "/api/tools/toggle"' in _wu)

print("── F. 行为：勾选真的写进清单文件 ──")
tmp = tempfile.mkdtemp(prefix="pm_toolsui_")
mf = {"name": "demo_tool", "description": "演示用工具（判据里临时造的）", "enabled": False,
      "method": "GET", "url": "https://api.example.com/x", "allow_hosts": ["api.example.com"],
      "params": {"type": "object", "properties": {}, "required": []}}
p = os.path.join(tmp, "demo.json")
with open(p, "w", encoding="utf-8") as fh:
    json.dump(mf, fh, ensure_ascii=False)
_real = UT._cfg
UT._cfg = lambda: {"dir": tmp, "enabled": True, "max_tools": 30, "timeout_ms": 8000, "max_chars": 4000}
try:
    ok("初始是关闭的", UT.load()[0][0]["enabled"] is False)
    ok_t, why = UT.set_enabled("demo_tool", True)
    after = json.load(open(p, encoding="utf-8"))
    ok("勾选后文件里 enabled=true", ok_t and after.get("enabled") is True, why)
    ok("清单重新加载也认它是开的", UT.load()[0][0]["enabled"] is True)
    ok_t2, why2 = UT.set_enabled("demo_tool", False)
    ok("取消勾选也写回去了", ok_t2 and json.load(open(p, encoding="utf-8")).get("enabled") is False)
    ok_t3, why3 = UT.set_enabled("not_exist", True)
    ok("名字不存在 ⇒ 拒绝并说明", ok_t3 is False and "没有名为" in why3, why3[:30])
    snap = UT.snapshot()
    ok("快照里有这个工具与来源标记", snap["tools"] and snap["tools"][0]["source"] == "第三方")
finally:
    UT._cfg = _real
    shutil.rmtree(tmp, ignore_errors=True)

print("── G. 前端 JS 语法（node --check）──")
import re  # noqa: E402
import subprocess  # noqa: E402
blocks = re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.S)
js = "\n;\n".join(blocks)
tp = os.path.join(tempfile.gettempdir(), "pm_console_tools_check.js")
with open(tp, "w", encoding="utf-8") as fh:
    fh.write(js)
r = subprocess.run(["node", "--check", tp], capture_output=True, text=True, timeout=60,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
ok("console_html 的 JS 过语法检查", r.returncode == 0,
   ((r.stderr or "").strip().splitlines() or [""])[-1][:100] if r.returncode else "%d 字符" % len(js))

print("\n%d/%d 通过" % (PASS, PASS + FAIL))
sys.exit(1 if FAIL else 0)
