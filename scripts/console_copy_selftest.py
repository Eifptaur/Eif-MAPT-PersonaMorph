# -*- coding: utf-8 -*-
"""④ 界面文案判据（2026-09-15，任务书 ④「界面文案通俗化」）

三条要守住的（都从"用户看得见的那一面"出发，不看源码注释、不看 JS 逻辑）：
  A. **无 emoji**：去掉 `<script>` / `<style>` 之后，可见文本与 title/placeholder/alt 属性里
     一个表情符号都不许有 —— 口径来自用户（显示层自研，图标手绘 SVG，不用 emoji）。
  B. **不甩假数据**：可见文本里不许出现 `undefined` / `NaN` / `null`
     （"未配置"这种如实说法才合格 —— 顶栏「余额 ¥undefined」就是这条判出来的）。
  C. **术语通俗化**：可见文本里不许出现没配中文说法的英文术语
     （Base URL / API Key / token / JSON / WebView2 / SAPI / base64 / DPI / OCR / UIA）。

判据自己起控制台、自己抓页面（与 `whale_selftest.py` D 段同一套路），不联网、不碰微信。
用法：py -3 scripts/console_copy_selftest.py
"""
import json
import os
import re
import socket
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS = 0
FAIL = 0
SKIP_N = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


def skip(name, why=""):
    global SKIP_N
    SKIP_N += 1
    print("  SKIP {}  [{}]".format(name, why))


EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")
#: 术语黑名单：**只列"裸英文术语"**——地址类（http/https/localhost）与文件名（config.json）要留给用户看，
#: 所以先归一化掉再判（见下方 normalize）。
JARGON = ["Base URL", "base url", "API Key", "api key", "Token", "token", "JSON", "WebView2",
          "SAPI", "base64", "DPI", "OCR", "UIA"]
FAKE = ["undefined", "NaN", "null"]


def normalize(s):
    """把"该留下的技术串"从扫描面里摘掉：文件名、地址、端口、网址参数。
    理由：这些是**用户必须逐字认出来的东西**（要照着改配置文件、要复制网址），
    不算"没解释的英文术语"——术语那条判的是"应当有中文说法的词"。"""
    s = s.replace("config.json", "配置文件").replace("CONFIG.JSON", "配置文件")
    s = re.sub(r"https?://[^\s\"'<>）)】]*", " ", s)
    s = re.sub(r"127\.0\.0\.1:\d+", " ", s)
    s = s.replace("?token=", "网址口令参数").replace("&token=", "网址口令参数")
    return s

print("== ④ 界面文案判据 ==")

_page = None
try:
    from agent import webui as W

    _orig_cfg = W.get_config
    _s = socket.socket()
    _s.bind(("127.0.0.1", 0))
    _free = _s.getsockname()[1]
    _s.close()
    base = dict(W.get_config() or {})
    base["server"] = {"enabled": True, "host": "127.0.0.1", "port": _free,
                      "token": "copy-judge", "auto_open_browser": False}
    W.get_config = lambda: base
    w = W.WebUI(lambda: {}, [])
    port = w.start()
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/?token=copy-judge" % port, timeout=8) as r:
            _page = r.read().decode("utf-8", "replace")
    finally:
        w.stop()
except Exception as e:
    skip("起控制台抓页面", "起不了：%s" % e)
finally:
    try:
        W.get_config = _orig_cfg
    except Exception:
        pass

if _page is None:
    print("")
    print("界面文案判据：%d 通过 / %d 失败 / %d 跳过（页面没抓到，其余判定跳过）" % (PASS, FAIL, SKIP_N))
    sys.exit(3)

# 只看"用户看得见的那一面"：去掉脚本与样式，再取文本与常见可见属性
body = re.sub(r"<script\b.*?</script>", " ", _page, flags=re.S | re.I)
body = re.sub(r"<style\b.*?</style>", " ", body, flags=re.S | re.I)
vis_text = re.sub(r"<[^>]+>", " ", body)
attrs = re.findall(r'(?:title|placeholder|alt|aria-label)="([^"]*)"', body, flags=re.I)
visible = normalize(vis_text + " \u0001 " + " \u0001 ".join(attrs))
print("  可见文本 %d 字 · 可见属性 %d 条" % (len(vis_text.strip()), len(attrs)))

print("\n── A. 无 emoji（显示层自研口径）──")
hits = sorted(set(EMOJI.findall(visible)))
ok("可见区域一个 emoji 都没有", not hits, "命中 %s" % ("".join(hits)[:24] if hits else ""))
if hits:
    for m in list(EMOJI.finditer(visible))[:12]:
        seg = visible[max(0, m.start() - 26):m.start() + 26].replace("\n", " ")
        print("      样本：…%s…" % seg.strip())

print("\n── B. 不甩假数据（undefined / NaN / null）──")
fake_hits = []
for w0 in FAKE:
    for m in re.finditer(re.escape(w0), visible):
        seg = visible[max(0, m.start() - 30):m.start() + 30].replace("\n", " ")
        fake_hits.append((w0, seg.strip()))
ok("可见文本里没有 undefined / NaN / null", not fake_hits,
   "%d 处" % len(fake_hits) if fake_hits else "")
for w0, seg in fake_hits[:8]:
    print("      %s：…%s…" % (w0, seg))

print("\n── D. 不许对一般用户说「开发黑话」（模块名 / 判据名 / 下划线键名）──")
# 用户 2026-09-15 原话：「注意你新加的这些功能文案，尽量能让一般用户也能看懂啊」
# 起因：我给 edge 音色写的说明里出现了 voice_models.EDGE_VOICES / edge_tts_selftest.py 这类
# 只有开发者看得懂的东西（已改成「晓晓、云希…挑一个顺耳的」）。
DEV_WORDS = ["voice_models", "EDGE_VOICES", "edge_tts_selftest", "_selftest", ".py 会",
             "判据", "backends", "fallback", "webhook", "JSON", "API Key 的字段"]
DEV_RE = [r"\b[a-z_]+\.(?:py|json|db)\b", r"\b[a-z]+_[a-z_]+\b(?=[^`]*?\b开关\b)"]
dev_hits = []
for w0 in DEV_WORDS:
    for m in re.finditer(re.escape(w0), visible):
        seg = visible[max(0, m.start() - 30):m.start() + 30].replace("\n", " ")
        dev_hits.append((w0, seg.strip()))
ok("可见文案里没有开发黑话（模块名/判据名/下划线键名）", not dev_hits,
   "命中 %s（%d 处）" % (sorted({h[0] for h in dev_hits})[:6], len(dev_hits)) if dev_hits else "")
for w0, seg in dev_hits[:8]:
    print("      %s：…%s…" % (w0, seg))

print("\n── C. 术语通俗化（不许裸英文术语）──")
jar_hits = []
for w0 in JARGON:
    for m in re.finditer(re.escape(w0), visible):
        seg = visible[max(0, m.start() - 30):m.start() + 30].replace("\n", " ")
        jar_hits.append((w0, seg.strip()))
kinds = sorted({h[0] for h in jar_hits})
ok("可见文本里没有裸英文术语", not jar_hits, "命中 %s（%d 处）" % (kinds[:8], len(jar_hits)) if jar_hits else "")
for w0, seg in jar_hits[:10]:
    print("      %s：…%s…" % (w0, seg))

print("")
print("界面文案判据：%d 通过 / %d 失败 / %d 跳过" % (PASS, FAIL, SKIP_N))
sys.exit(1 if FAIL else 0)
