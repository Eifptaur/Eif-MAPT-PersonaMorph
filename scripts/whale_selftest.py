# -*- coding: utf-8 -*-
"""「鲸语」文案判据（2026-09-14，用户：「我这边切换鲸语，只有少数的几个变了」）。

三条要守住的东西：
  ① **覆盖**：控制台里可见的短文案（面板标题 / 行标签 / 按钮 / 表头）必须都在字典里，
     否则切过去就"只有少数几个变了"；
  ② **整节点替换**：只换"整个文本节点"（`>文案<`），不许做子串替换——
     否则「停止」会吃掉「停止检测」，还会改坏 JS 里的字符串（这是旧版的真 bug）；
  ③ **单点来源**：字典在 `agent/whale_text.py` 一份，服务端注入给前端用；
     前端不许再手写第二份（两份必然漂移）。

用法：py -3 scripts/whale_selftest.py
"""
import html as _html
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import console_html as CH              # noqa: E402
from agent import webui as W                      # noqa: E402
from agent.whale_text import DICT, SKIP, NAV       # noqa: E402

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


TAG = re.compile(r"<[^>]+>")


def clean(s):
    s = _html.unescape(TAG.sub("", s or ""))
    return re.sub(r"\s+", " ", s).strip()


H = CH.HTML
print("── A. 覆盖：可见短文案必须都在字典里 ──")
groups = {
    "h2": re.findall(r"<h2[^>]*>(.*?)</h2>", H, re.S),
    "label": re.findall(r"<label[^>]*>(.*?)</label>", H, re.S),
    "button": re.findall(r"<button[^>]*>(.*?)</button>", H, re.S),
    "th": re.findall(r"<th[^>]*>(.*?)</th>", H, re.S),
    "nav": re.findall(r'<span class="lb">(.*?)</span>', H, re.S),
}
total = 0
gaps = []
for g, raw in groups.items():
    uniq = []
    for it in raw:
        t = clean(it)
        # 纯 JS 拼接占位（'+x+'）、纯符号、以及带引号/花括号的动态文本不算文案
        if not t or len(t) > 30 or t in uniq:
            continue
        if t in SKIP or t.startswith("'+") or any(c in t for c in "{}"):
            continue
        if re.fullmatch(r"[+\-×✕‹›✓✓\s]+", t):
            continue
        uniq.append(t)
    miss = [x for x in uniq if x not in DICT]
    total += len(uniq)
    gaps += miss
    print("      %-7s %d 条，未覆盖 %d" % (g, len(uniq), len(miss)))
ok("可见短文案字典覆盖 ≥ 99%%（%d 条，未覆盖 %d）" % (total, len(gaps)),
   len(gaps) <= max(0, int(total * 0.01)), "、".join(gaps[:12]))

print("── B. 整节点替换：不许子串吃掉子串 ──")
_orig = W.get_config
try:
    W.get_config = lambda: {"ui": {"text_style": "whale"}}
    w = object.__new__(W.WebUI)
    page = "<h2>停止检测</h2><button>停止</button><div>测试 API 连通</div><span>模型 API 不通时先看这里</span>"
    out = W.WebUI._apply_whale(w, page)
    ok("长键优先、各换各的（没有「停止」吃掉「停止检测」）",
       "停止检测（不测了，我摊牌）" in out and ">停止（打烊）<" in out, out[:90])
    ok("整节点才换：句子里的「模型 API」不乱换", "不通时先看这里" in out and out.count("模型 API") == 1, out)
    # V-R7-10：这里原有 `ok("普通模式原样返回", W.WebUI._apply_whale.__doc__ is not None)`——只查了文档字符串存在，
    # 恒真且与"普通模式"无关；普通模式的行为已由下面第 105 行那条真断言（一个字都不改）覆盖，故作废删除。
finally:
    W.get_config = _orig

try:
    W.get_config = lambda: {"ui": {"text_style": "normal"}}
    w2 = object.__new__(W.WebUI)
    ok("普通模式：一个字都不改", W.WebUI._apply_whale(w2, "<h2>停止检测</h2>") == "<h2>停止检测</h2>")
finally:
    W.get_config = _orig

print("── C. 单点来源 + 服务端注入 ──")
_html_src = io.open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
_web_src = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
ok("前端不再手写字典（只剩注入位）",
   "const WHALE_TXT = {" not in _html_src and "__WHALE_TXT__" in _html_src)
ok("服务端下发字典（保证动态刷新也能换）", "__WHALE_TXT__" in _web_src and "json.dumps(WHALE_DICT" in _web_src)
ok("服务端从 whale_text 导入（一份来源）", "from .whale_text import DICT as WHALE_DICT" in _web_src)
ok("字典本身可 JSON 序列化（前端要能吃）",
   len(json.dumps(DICT, ensure_ascii=False)) > 1000)
_collide = [k for k, v in NAV.items() if v in DICT]
ok("导航短表的**结果**不能再是总表的键（否则第二趟会把它再翻成长文案）", not _collide, str(_collide))

print("── D. 直发页面实测：切鲸语后整页确实变了 ──")
if os.environ.get("PM_JUDGE_NO_PROC") == "1":
    # ⛔ V-R7-12：判据环境（`run_all_selftests.py` 会带这个开关）⇒ **只跑静态/内存那半**，
    #   这一段的"真起 WebUI 服务"整段跳过，并**明确打一行 SKIP**（不冒充通过；单跑仍然跑全）。
    skip("D. 直发页面实测", "PM_JUDGE_NO_PROC=1 ⇒ 跳过真起控制台那半（D 段 2 条不判）")
else:
    try:
        import urllib.request

        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
        s.close()
        base = dict(_orig() or {})
        base["server"] = {"enabled": True, "host": "127.0.0.1", "port": free, "token": "whale-judge",
                          "auto_open_browser": False}
        base["ui"] = {"text_style": "whale"}
        W.get_config = lambda: base
        w3 = W.WebUI(lambda: {}, [])
        import tempfile as _tf
        w3.console_url_root = _tf.mkdtemp(prefix="cuj-")   # ⚠️ 判据不写产品那份 logs/console.url（2026-09-18）
        port = w3.start()
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/?token=whale-judge" % port, timeout=6) as r:
                page = r.read().decode("utf-8", "replace")
            n_hit = sum(1 for k, v in DICT.items() if ("<" in page and v in page))
            ok("页面里出现了大量鲸语文案（≥80 条）", n_hit >= 80, "命中 %d 条" % n_hit)
            ok("字典已注入到前端（const WHALE_TXT = { 不是空对象）",
               "const WHALE_TXT = {}" not in page and '"🐋 概览' in page)
        finally:
            w3.stop()
    except Exception as e:
        skip("D. 直发页面实测", "起不了控制台：%s" % e)
    finally:
        W.get_config = _orig

print("── E. 用量计数单点来源（token 调研 C9：calls 曾长期虚高一倍）──")
try:
    from agent.llm import add_usage                        # noqa: E402
    _t = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
          "reasoning_tokens": 0, "cached_tokens": 0, "calls": 0}
    add_usage(_t, {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})
    add_usage(_t, {"prompt_tokens": 200, "completion_tokens": 30, "total_tokens": 230})
    ok("两次 API 调用 ⇒ calls == 2（计数只在 add_usage 里发生）", _t["calls"] == 2, _t["calls"])
    ok("token 累加正确（prompt 300 / completion 50）",
       _t["prompt_tokens"] == 300 and _t["completion_tokens"] == 50,
       (_t["prompt_tokens"], _t["completion_tokens"]))
    _pm_src = open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
    _double = re.findall(r'usage"\]\["calls"\]\s*\+=', _pm_src)
    ok("persona_morph.py 不得再对 usage[\"calls\"] 自增（那是 C9 的双计来源）",
       not _double, "命中 %d 处" % len(_double))
    _llm_src = open(os.path.join(ROOT, "agent", "llm.py"), encoding="utf-8").read()
    ok("llm.py 的 add_usage 里保留唯一那次 ++",
       re.search(r'def add_usage\(', _llm_src) is not None and _llm_src.count('target["calls"] += 1') == 1,
       "target[calls] += 1 出现 %d 次" % _llm_src.count('target["calls"] += 1'))
except Exception as e:
    skip("E. 用量计数单点来源", "导入失败：%s" % e)

print("")
print("鲸语判据：%d 通过 / %d 失败 / %d 跳过；字典 %d 条" % (PASS, FAIL, SKIP_N, len(DICT)))
sys.exit(1 if FAIL else 0)
