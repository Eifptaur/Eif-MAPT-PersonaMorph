#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""媒体与语音控制台的判据（能力 D）：UI 在、文案在、接线在，而且**JS 能过语法检查**。

判据（不需要微信、不出网）：
  ① 导航与分区：有 `#sec-media` 链接 + `data-sec` 分区，且该分区在 `sec-wechat` 之前闭合
  ② 三块能力的设置项都在（voice.enabled/engine/dir/max_seconds、image_reply.enabled/mode/dir/safe_only、send.file_forward_optin）
  ③ 文案三类都在：**未配置/缺失态**（没有可用引擎 + 补齐办法含 `pip install pilk`）· **空状态**（图库是空的）· **红线**（转发会抢一次前台、音频不出网）
  ④ 渲染与交互：JS 会渲染 vsWhy/vsList/irState/fwState，`测试引擎`按钮打到 `/api/voice/test`
  ⑤ 后端接线：`/api/status` 里带 `media` 段；`/api/voice/test` 路由存在
  ⑥ **JS 语法**：把 console_html 里的 `<script>` 全抽出来交给 `node --check`（手写 JS 最容易在这翻车）
"""
import os
import re
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
from agent import webui as WU  # noqa: E402

HTML = CH.HTML
print("── A. 导航与分区 ──")
ok("导航有 #sec-media 链接", 'href="#sec-media"' in HTML and "媒体与语音" in HTML)
ok("有 sec-media 分区且带 data-sec", 'id="sec-media" class="card" data-sec' in HTML)
i_media = HTML.find('id="sec-media"')
i_wechat = HTML.find('id="sec-wechat"')
seg = HTML[i_media:i_wechat] if (i_media > 0 and i_wechat > i_media) else ""
ok("分区在 sec-wechat 之前且已闭合", bool(seg) and "</section>" in seg)

print("── B. 三块能力的设置项 ──")
for key in ("voice.enabled", "voice.engine", "voice.dir", "voice.max_seconds",
            "image_reply.enabled", "image_reply.mode", "image_reply.dir", "image_reply.safe_only",
            "send.file_forward_optin"):
    ok("设置项 %s 在媒体分区里" % key, ('data-cfg="%s"' % key) in seg)
ok("有保存按钮（媒体与语音）", "保存设置（媒体与语音）" in seg)

print("── C. 三类文案 ──")
ok("未配置态：会报「没有可用引擎」", "没有可用引擎" in HTML)
ok("未配置态给了可复制的补齐命令", "pip install pilk" in HTML)
ok("空状态：图库为空时会说清楚", "图库是空的" in HTML)
ok("红线：转发会抢前台", "抢一次前台" in HTML)
ok("红线：音频不出网", "不出网" in HTML)
ok("链接不用下载（纯后台）写在面板里", "不用下载" in HTML)

print("── D. 渲染与交互 ──")
for el in ("vsWhy", "vsList", "irState", "fwState"):
    ok("JS 会填 #%s" % el, ("$('%s')" % el) in HTML or ("getElementById('%s')" % el) in HTML)
ok("测试引擎按钮打到 /api/voice/test", "/api/voice/test" in HTML and "vsTest" in HTML)
ok("按钮会展示每一步结果", "链路可用" in HTML and "链路跑不通" in HTML)

print("── E. 后端接线 ──")
src_wu = open(os.path.join("agent", "webui.py"), encoding="utf-8").read()
ok("/api/status 里带 media 段（走 media_status 快照）", 'st["media"] = _ms.snapshot()' in src_wu)
ok("/api/voice/test 路由存在", 'elif path == "/api/voice/test"' in src_wu)

print("── F. 数据契约（面板读的字段必须真的存在）──")
from agent import media_status as MS  # noqa: E402
snap = MS.snapshot()
ok("快照有 voice / voice_cfg / image / forward 四块",
   all(k in snap for k in ("voice", "voice_cfg", "image", "forward")))
ok("voice 块有 JS 要的 ok/why/decode/recognize",
   all(k in snap["voice"] for k in ("ok", "why", "decode", "recognize")))
ok("decode/recognize 每条有 ok/name/detail",
   all({"ok", "name", "detail"} <= set(x) for x in (snap["voice"]["decode"] + snap["voice"]["recognize"])))
ok("image 块有 enabled/mode/dir/count，且 count 是整数",
   all(k in snap["image"] for k in ("enabled", "mode", "dir", "count")) and isinstance(snap["image"]["count"], int),
   "count=%s" % snap["image"]["count"])
ok("forward.optin 是布尔", isinstance(snap["forward"].get("optin"), bool), str(snap["forward"].get("optin")))
ok("快照不写死可用性（来自现场探测）", snap["voice"].get("ok") in (True, False))

print("── G. JS 语法（node --check）──")
blocks = re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.S)
ok("抽到了 script 块", len(blocks) > 0, "%d 块" % len(blocks))
js_all = "\n;\n".join(blocks)
tmp = os.path.join(tempfile.gettempdir(), "pm_console_check.js")
with open(tmp, "w", encoding="utf-8") as fh:
    fh.write(js_all)
try:
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True, timeout=60)
    ok("console_html 的 JS 过语法检查", r.returncode == 0, (r.stderr or "").strip().splitlines()[-1][:120] if r.returncode else "%d 字符" % len(js_all))
except Exception as e:
    ok("console_html 的 JS 过语法检查", False, "%s: %s" % (type(e).__name__, e))

print("\n%d/%d 通过" % (PASS, PASS + FAIL))
sys.exit(1 if FAIL else 0)
