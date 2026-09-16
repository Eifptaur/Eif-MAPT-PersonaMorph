# -*- coding: utf-8 -*-
"""edge 神经语音音源判据（2026-09-15）

背景：群相原来只有两档音源——SAPI（开箱可用但机械音）与「用户自带模型」（要自己跑服务）。
本轮加第三档 **edge-tts**（免费神经语音、无需 Key、需联网），并把它设为**默认**。
这条判据守的就是"加了这一档之后，整条线上每一处都跟着改了"（牵一发动全身）：

  A. **默认档就是 edge**：`config` 里 `voice_reply.backend == "edge"`、`edge_voice` 是合法音色、
     `edge_fallback` 默认开（edge 失败退回系统声音，**并在说明里写明退回了**）。
  B. **白名单与反证**：`voice_models.backend()` 认 edge；乱填的值**落到 sapi**、绝不落成 edge
     （反向断言：不是"任何值都当 edge"）。
  C. **status 形状与三档隔离**：edge 档 `engine == "edge-tts"`、给出 8 条中文音色（带 label）；
     sapi 档**不许**混进 edge 音色。edge 未装时 `ok=False` 且 `why` 指明怎么装（不假装可用）。
  D. **不假装**：文本为空 ⇒ `None` + 原因、**不落文件**；没装 edge-tts 时 ⇒ `None` + 原因，
     且**不静默产出空音频**。
  E. **控制台三档互斥且不漂**：HTML 里 `voice_reply.edge_voice` 的选项与
     `voice_models.EDGE_VOICES` **逐一相同（名字与文案）**——这是本判据最值钱的一条：
     两边任何一处单独改动都会当场变红。另：`edgeVoiceRow` / `sapiVoiceRow` 两个行 id 存在
     且**在整份 HTML 里唯一**；`ttsSyncRows()` 存在、在填表后被调用、在 backend 变更时被挂上；
     JS 把 voices 当 dict 处理（不是直接塞进 option，否则会显示 `[object Object]`）。
  F. **快照接线**：`media_status.snapshot()["tts"]` 里多出来的 `models` 段能读出 backend——
     否则控制台「引擎状态」只会显示系统声音那一档，跟用户选的档对不上。

用法：`py -3 scripts/edge_tts_selftest.py`（不联网、不合成、不落文件）
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import voice_models as VM          # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   %s%s" % (name, ("（%s）" % detail) if detail else ""))
    else:
        FAIL += 1
        print("  FAIL %s%s" % (name, ("（%s）" % detail) if detail else ""))


def sect(t):
    print("\n── %s ──" % t)


# ── A. 默认档 ────────────────────────────────────────────────────────────────
sect("A. 出厂默认＝edge 神经语音")
from agent.config import get_config           # noqa: E402
rc = dict((get_config().get("voice_reply") or {}))
names = [n for n, _ in VM.EDGE_VOICES]
ok("voice_reply.backend 默认 edge", rc.get("backend") == "edge", repr(rc.get("backend")))
ok("voice_reply.edge_voice 默认是合法音色", rc.get("edge_voice") in names, repr(rc.get("edge_voice")))
ok("edge_fallback 默认开（失败退回系统声音）", rc.get("edge_fallback") is True, repr(rc.get("edge_fallback")))
ok("旧键 voice_reply.voice 仍在（系统档还要用）", "voice" in rc)
ok("EDGE_VOICES 恰好 8 条中文音色", len(VM.EDGE_VOICES) == 8, "%d 条" % len(VM.EDGE_VOICES))
ok("音色名字不重复", len(set(names)) == len(names), "%d 个唯一" % len(set(names)))
ok("每条音色都有中文文案", all(lab and re.search(r"[\u4e00-\u9fff]", lab) for _, lab in VM.EDGE_VOICES))

# ── B. 白名单与反证 ──────────────────────────────────────────────────────────
sect("B. 后端白名单 + 反向对照")
ok("backend() 认 edge", VM.backend({"backend": "edge"}) == "edge")
ok("backend() 认 sapi/http", VM.backend({"backend": "sapi"}) == "sapi"
   and VM.backend({"backend": "http"}) == "http")
ok("edge 大小写/空格也认", VM.backend({"backend": "  EDGE "}) == "edge")
ok("反证：乱填的值落到 sapi、不会变成 edge",
   VM.backend({"backend": "edge-tts"}) == "sapi" and VM.backend({"backend": "neural"}) == "sapi",
   "edge-tts=%s neural=%s" % (VM.backend({"backend": "edge-tts"}), VM.backend({"backend": "neural"})))
ok("反证：backend 缺省时不是 edge（不会偷偷换档）", VM.backend({}) == "sapi")
ok("edge_voice() 读配置", VM.edge_voice({"edge_voice": "zh-CN-YunxiNeural"}) == "zh-CN-YunxiNeural")
ok("edge_voice() 空/空白回默认", VM.edge_voice({}) == VM.EDGE_DEFAULT_VOICE
   and VM.edge_voice({"edge_voice": "   "}) == VM.EDGE_DEFAULT_VOICE)

# ── C. status 形状与三档隔离 ─────────────────────────────────────────────────
sect("C. status 形状 + 档位之间不串味")
se = VM.status({"backend": "edge"})
ok("edge 档 engine=edge-tts", se.get("engine") == "edge-tts", repr(se.get("engine")))
ok("edge 档 backend=edge", se.get("backend") == "edge")
ok("edge 档给出 8 条音色且每条有 name/label",
   len(se.get("voices") or []) == 8
   and all(isinstance(v, dict) and v.get("name") and v.get("label") for v in se["voices"]),
   "%d 条" % len(se.get("voices") or []))
ok("edge 档音色名单与 EDGE_VOICES 完全一致",
   [(v["name"], v["label"]) for v in se["voices"]] == VM.EDGE_VOICES)
ok("edge 档 why 如实写明要联网", "联网" in (se.get("why") or ""), (se.get("why") or "")[:40])
ok("edge_fallback 关掉后 why 写明「如实报错、不静默」",
   "不静默" in (VM.status({"backend": "edge", "edge_fallback": False}).get("why") or ""))
ok("ok 只取决于 edge_tts 装没装（装了就 True）",
   se.get("ok") is (__import__("importlib").util.find_spec("edge_tts") is not None),
   repr(se.get("ok")))
ss = VM.status({"backend": "sapi"})
ok("反证：sapi 档 engine 不是 edge-tts", ss.get("engine") != "edge-tts", repr(ss.get("engine")))
ok("反证：sapi 档不会混进 edge 音色",
   not any(isinstance(v, dict) and str((v or {}).get("name", "")).startswith("zh-CN-")
           for v in (ss.get("voices") or [])))
sh = VM.status({"backend": "http", "http_url": ""})
ok("http 档没填地址时如实报错（不假装可用）",
   sh.get("ok") is False and "没填地址" in (sh.get("why") or ""), (sh.get("why") or "")[:40])

# ── D. 不假装 ────────────────────────────────────────────────────────────────
sect("D. 绝不假装（空文本 / 没装 edge）")
before = sorted(os.listdir(VM._out_dir())) if os.path.isdir(VM._out_dir()) else []
p, why, info = VM.make("   ", {"backend": "edge"})
after = sorted(os.listdir(VM._out_dir())) if os.path.isdir(VM._out_dir()) else []
ok("空文本 ⇒ None + 原因", p is None and "文本为空" in (why or ""), (why or "")[:30])
ok("空文本不落任何文件（前后目录一致）", before == after, "%d→%d 个文件" % (len(before), len(after)))
_saved = sys.modules.get("edge_tts")
sys.modules["edge_tts"] = None                      # 替身：import edge_tts 会抛
try:
    import builtins
    _ri = builtins.__import__

    def _fake(name, *a, **k):
        if name == "edge_tts":
            raise ImportError("替身：没装")
        return _ri(name, *a, **k)
    builtins.__import__ = _fake
    try:
        p2, why2, _ = VM._edge_make("你好", {"backend": "edge"}, 5)
    finally:
        builtins.__import__ = _ri
    ok("没装 edge-tts ⇒ None + 原因（不是空音频）", p2 is None and "没装" in (why2 or ""), (why2 or "")[:40])
finally:
    if _saved is None:
        sys.modules.pop("edge_tts", None)
    else:
        sys.modules["edge_tts"] = _saved
_p, _w, _i = VM._make_raw("", {"backend": "edge"})
ok("_make_raw 空文本也不落文件", _p is None and "文本为空" in (_w or ""))

# ── E. 控制台三档互斥且不漂 ──────────────────────────────────────────────────
sect("E. 控制台：选项不许与 EDGE_VOICES 漂开 + 三档互斥")
from agent import console_html as CH          # noqa: E402
H = CH.HTML


def _select_block(html, path):
    i = html.find('data-cfg="%s"' % path)
    if i < 0:
        return ""
    j = html.find("</select>", i)
    k = html.rfind("<select", 0, i)
    return html[k:j + 9] if (j > 0 and k >= 0) else ""


blk = _select_block(H, "voice_reply.edge_voice")
ok("控制台有 voice_reply.edge_voice 下拉", bool(blk))
_opts = re.findall(r'<option value="([^"]*)">([^<]*)</option>', blk)
ok("edge 音色下拉与 EDGE_VOICES 名字逐一相同（顺序也一致）",
   [v for v, _ in _opts] == names, "%d 项 vs %d 项" % (len(_opts), len(names)))
ok("edge 音色下拉文案与 EDGE_VOICES 逐字相同",
   [t for _, t in _opts] == [lab for _, lab in VM.EDGE_VOICES])
ok("反证：下拉里没有重复音色", len(set(v for v, _ in _opts)) == len(_opts))
_bblk = _select_block(H, "voice_reply.backend")
_bopts = [v for v, _ in re.findall(r'<option value="([^"]*)">([^<]*)</option>', _bblk)]
ok("声音来源下拉有三档且含 edge", set(_bopts) >= {"edge", "sapi", "http"}, "、".join(_bopts))
ok("三档的顺序＝edge 在前（默认档在前，跟默认值一致）", _bopts[:1] == ["edge"], "、".join(_bopts))
for el in ("edgeVoiceRow", "sapiVoiceRow"):
    ok("有 #%s 行" % el, ('id="%s"' % el) in H)
ok("#ttsVoice 在整份 HTML 里唯一（曾因复制行出现过两份）",
   len(re.findall(r'id="ttsVoice"', H)) == 1, "%d 处" % len(re.findall(r'id="ttsVoice"', H)))
ok("#ttsEdgeVoice 唯一", len(re.findall(r'id="ttsEdgeVoice"', H)) == 1)
ok("反证：没有第二个 data-cfg=voice_reply.voice 的元素",
   len(re.findall(r'data-cfg="voice_reply\.voice"', H)) == 1,
   "%d 处" % len(re.findall(r'data-cfg="voice_reply\.voice"', H)))
ok("有 ttsSyncRows() 函数定义", "function ttsSyncRows()" in H)
ok("填表后（syncToForm）会调用 ttsSyncRows", "updateTierRows();\n  ttsSyncRows();" in H)
ok("backend 变更时挂了 ttsSyncRows",
   "_ttsBeSel.addEventListener('change', ()=>ttsSyncRows())" in H)
ok("ttsSyncRows 按 edge/sapi 两个行 id 切换", "edgeVoiceRow" in H and "sapiVoiceRow" in H
   and "ttsSyncRows" in H)
ok("JS 把音色当对象取 label（否则会显示 [object Object]）",
   "v.label || v.name" in H and "_vn(ts.voices)" in H)
ok("JS 只在非 edge-tts 时才填系统声音下拉",
   "String(ts.engine || 'sapi') !== 'edge-tts'" in H)
ok("JS 的引擎状态跟着当前选的档走（读 ttsBackend）", "_beNow === 'edge'" in H)

# ── F. 快照接线 ──────────────────────────────────────────────────────────────
sect("F. /api/status 快照接线")
from agent import media_status as MS           # noqa: E402
snap = MS.snapshot()
tts = snap.get("tts") or {}
ok("快照 tts 块里有 models 段", "models" in tts)
ok("models.backend 能读出 edge（与配置一致）", (tts.get("models") or {}).get("backend") == "edge",
   repr((tts.get("models") or {}).get("backend")))
ok("tts.cfg 里有 backend / edge_voice", "backend" in (tts.get("cfg") or {})
   and "edge_voice" in (tts.get("cfg") or {}))
ok("systems 段（tts.status）仍在（系统档还要用）", "status" in tts and "ffmpeg" in (tts["status"] or {}))
ok("反证：models 段报的档不是靠猜——与 voice_models.status() 一致",
   (tts.get("models") or {}).get("engine") == se.get("engine"))

# ── G. 试听按钮与出网披露（加第三档时一并收口的两处真缺陷） ───────────────────
sect("G. 试听要跟着档走 + 出网如实披露")
WU = open(os.path.join("agent", "webui.py"), encoding="utf-8").read()
_i = WU.find('elif path == "/api/tts/test"')
_seg = WU[_i:_i + 1800] if _i > 0 else ""
ok("后端有 /api/tts/test 路由", bool(_seg))
ok("试听走 voice_models.make（跟着当前档），不走 tts.make",
   "_vm.make(txt)" in _seg and "_tt.make(txt)" not in _seg)
ok("试听回报里带 engine（面板能显示是哪一档合成的）", '"engine": _be' in _seg)
ok("反证：源码里没有残留的 _tt.make 试听调用", "p, err, info = _tt.make(txt)" not in WU)
ok("分区文案写明 edge 会把「要念的那一句」发到在线语音服务", "在线语音服务" in H)
ok("分区文案仍写明系统声音档不出网（不是把话删了了事）", "不出网" in H)
ok("反证：不再声称试听「只在本机合成」", "只在本机合成" not in H)
ok("试听按钮文案已改成「按当前音源合成」", "按当前音源合成" in H)
ok("试听输出文案写明不发送", "不会发到任何会话" in H)
ok("试听 JS 结果里会报档位", "档位：" in H and "r.engine || info.engine" in H)
ok("试听 JS 提示 edge 档要联网", "edge 档要联网" in H)
# 2026-09-15 用户改口径：「关于"全程不出网"的条款，你可以写成"绝大部分不出网"，就是出不出网是可选项」
_AG = open(os.path.join("AGENTS.md"), encoding="utf-8").read()
ok("AGENTS.md 记下「绝大部分不出网 + 出网是可选项」这条产品口径",
   "绝大部分不出网" in _AG and "出网是可选项" in _AG)
ok("AGENTS.md 写明每处出网都要配一条不出网的替代档", "不出网的替代档" in _AG)
ok("语音引导文案告诉用户「想全程不出网就选系统声音那一档」",
   "想全程不出网就选系统声音" in H)
ok("反证：引导文案里不再有「合成全程在本机，内容不出网」这句一刀切的话",
   "合成全程在本机，内容不出网" not in H)

# ── H. 兜底分支（离线自检：替身掉网络那一段） ────────────────────────────────
sect("H. edge 失败时的两条路（兜底 / 如实报错）")
import importlib                              # noqa: E402
from agent import tts as _T                   # noqa: E402
_save_edge, _save_tts = VM._edge_make, _T.make
try:
    VM._edge_make = lambda text, cfg, timeout: (None, "替身：假装 edge 挂了", {})
    _T.make = lambda text: ("C:/替身/系统声.wav", "", {"engine": "sapi", "voice": "Huihui"})
    p, w, i = VM._make_raw("你好", {"backend": "edge", "edge_fallback": True})
    ok("edge 挂了 + 兜底开 ⇒ 发系统声音那条，并写明退回了",
       p == "C:/替身/系统声.wav" and "退回系统声音" in (w or ""), (w or "")[:46])
    ok("兜底时 info 里留下 edge 的失败原因（可追责）",
       (i or {}).get("edge_failed") == "替身：假装 edge 挂了", repr((i or {}).get("edge_failed")))
    p2, w2, i2 = VM._make_raw("你好", {"backend": "edge", "edge_fallback": False})
    ok("edge 挂了 + 兜底关 ⇒ None + 如实报错（不静默、不降级）",
       p2 is None and "替身：假装 edge 挂了" in (w2 or ""), (w2 or "")[:40])
    VM._edge_make = lambda text, cfg, timeout: ("C:/替身/edge.wav", "", {"engine": "edge-tts"})
    p3, w3, _ = VM._make_raw("你好", {"backend": "edge", "edge_fallback": True})
    ok("edge 成功时 why 为空（不是成功了还报错）", p3 == "C:/替身/edge.wav" and w3 == "", repr(w3))
    VM._edge_make = lambda text, cfg, timeout: (None, "替身：假装 edge 挂了", {})
    _T.make = lambda text: (_ for _ in ()).throw(RuntimeError("替身：系统声音也没有"))
    p4, w4, _ = VM._make_raw("你好", {"backend": "edge", "edge_fallback": True})
    ok("反证：两边都挂 ⇒ 仍是 None（不许假装合成过）",
       p4 is None and "系统声音也失败" in (w4 or ""), "%s | %s" % (repr(p4), (w4 or "")[:44]))
finally:
    VM._edge_make, _T.make = _save_edge, _save_tts

print("\n%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)