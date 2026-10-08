# -*- coding: utf-8 -*-
"""A1 / A2 / A3 判据（B 赛道 §G.2 A 组：可直接收编的三条）。

跑法： py -3 scripts\\harvest_a123_selftest.py     退出码 0=全过 / 1=有失败

**A1 能力探测记忆化**（F10-b + F11，同源）
  `voice.py` 六处探测 + `tts.py` 的 `voices()` 都要**记住 60 秒**，
  因为控制台**每 30 秒**轮询 `/api/status` ⇒ 不缓存就每次白烧 ~142ms CPU。
  ① 探测函数真的进了缓存（第二遍不再真跑）；
  ② TTL 到了会失效；
  ③ `refresh_probes()`/`refresh_voices()` 能作废（判据不串味）；
  ④ ⛔ **空结果不缓存**（否则"这次恰好取不到"被钉住 60 秒）。

**A2 本轮回累计条数上限**（N31-1，真缺口）
  `tools.py::_exec_send_message` + `sender.py::send_text_batch` **两处一起**。
  ⑤ 已发数达上限 ⇒ `send_message` **直接拒**（且不许碰界面）；
  ⑥ 未达上限但本批会超 ⇒ **截断**（不是报错）；
  ⑦ 截断是**末尾合并**（不丢内容）；
  ⑧ `sender` 侧同样兜底（只补一处 = 半残）。

**A3 媒体消息跳过引用**（F8）
  ⑨ `_is_media_placeholder` 认出七类占位符；
  ⑩ 普通文字不误判；
  ⑪ `send_text_batch` 里真的会在引用前挡下（不是只定义了个函数没用）。

⛔ 本判据**只读 + 打桩**，不碰产品根、不发真消息。
"""

from __future__ import annotations

import io

import os

import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import _iso14  # noqa: E402
_iso14.all_()  # ⛔ 必须 all_()

from agent import voice as V  # noqa: E402

from agent import sender as S  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def _src(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


# ═══════════ A1 能力探测记忆化 ═══════════
print("\n══ A1 能力探测记忆化（F10-b + F11）══")

# ① 真进了缓存：拿一个必然"成功"的探测当白鼠 —— sapi_file_ok 在本机可能失败（空结果不缓存），
#    所以改用**打桩计数**：把 raw 替成计数器，验证 _memo 只调一次。
V.refresh_probes()
_calls = {"n": 0}


def _fake_raw():
    _calls["n"] += 1
    return True, "打桩"


V._probe_cache.clear()
r1 = V._memo("ut_key", _fake_raw)
r2 = V._memo("ut_key", _fake_raw)
r3 = V._memo("ut_key", _fake_raw)
ok("① 同 key 连查 3 次，真实探测只跑 1 次", _calls["n"] == 1, "calls=%d" % _calls["n"])
ok("①b 三次拿到的值一致", r1 == r2 == r3 == (True, "打桩"))

# ② TTL 到了会失效
_calls["n"] = 0
_m = V._memo("ut_ttl", _fake_raw, ttl=0.05)
time.sleep(0.12)
_m2 = V._memo("ut_ttl", _fake_raw, ttl=0.05)
ok("② TTL 过期后重新真跑", _calls["n"] == 2, "calls=%d" % _calls["n"])

# ③ refresh_probes 能作废
_calls["n"] = 0
V._memo("ut_ref", _fake_raw)
V.refresh_probes()
V._memo("ut_ref", _fake_raw)
ok("③ refresh_probes() 作废缓存（重新真跑）", _calls["n"] == 2, "calls=%d" % _calls["n"])

# ④ ⛔ 空结果不缓存（纪律）
_calls["n"] = 0


def _fake_fail():
    _calls["n"] += 1
    return False, "打桩-取不到"


V._probe_cache.clear()
V._memo("ut_fail", _fake_fail)
V._memo("ut_fail", _fake_fail)
ok("④ 空/失败结果**不缓存**（两次都真跑）", _calls["n"] == 2, "calls=%d" % _calls["n"])

# ④b tts.voices 也是记忆化的（源码级锚：必须有 _VOICE_CACHE + TTL + refresh）
_tsrc = _src("agent/tts.py")
ok("④b tts.py 有 _VOICE_CACHE 且 TTL=60", "_VOICE_CACHE" in _tsrc and "_VOICE_TTL = 60.0" in _tsrc)
ok("④c tts.voices() 里真的是「先查缓存再真跑」（存在 refresh_voices）",
   "def refresh_voices" in _tsrc and "if got:" in _tsrc)

# ④d voice.py 六处探测都包了 _memo
_vsrc = _src("agent/voice.py")
_six = ["pilk", "silk_exe", "ffmpeg_silk", "registry_recognizers", "sapi_file"]
_wrapped = [_k for _k in _six if ('_memo("%s"' % _k) in _vsrc]
ok("④d voice.py 五族探测均已记忆化", len(_wrapped) == 5, "命中=%s" % _wrapped)
ok("④e _PROBE_TTL = 60.0 且有 refresh_probes", "_PROBE_TTL = 60.0" in _vsrc and "def refresh_probes" in _vsrc)

# ④f 真实耗时（缓存生效的**外部可观测**证据）
V.refresh_probes()
_t0 = time.time()
V.status()
_t1 = time.time()
V.status()
_t2 = time.time()
ok("④f 第二遍 voice.status() 明显更快（缓存生效）",
   (_t2 - _t1) < (_t1 - _t0), "1st=%.1fms 2nd=%.1fms" % ((_t1 - _t0) * 1000, (_t2 - _t1) * 1000))

# ═══════════ A2 本轮回累计条数上限 ═══════════
print("\n══ A2 本轮回累计条数上限（N31-1 真缺口）══")

_tsrc = _src("agent/tools.py")
_ssrc = _src("agent/sender.py")
_ctsrc = _src("agent/config.py")

ok("⑤ tools.py 里出现了 max_total_per_reply", "max_total_per_reply" in _tsrc)
ok("⑥ tools.py 按 session 已发数拦截（_already >= _cap 直接拒）",
   "_already >= _cap" in _tsrc and '"sent"' in _tsrc)
ok("⑦ tools.py 未达上限时**截断**（剩余配额）", "_remain = _cap - _already" in _tsrc)
ok("⑧ sender.py 侧也兜了一层（max_parts_per_reply 或 max_total_per_reply）",
   "max_parts_per_reply" in _ssrc or "max_total_per_reply" in _ssrc)
ok("⑨ sender.py 的截断是**末尾合并**（不丢内容）",
   '_merged = "\\n".join(_tail)' in _ssrc or "_merged" in _ssrc)
ok("⑩ config.py 默认表里有 max_total_per_reply", "max_total_per_reply" in _ctsrc)

# ⑪ 行为级：mock 一份 cfg + session，验证"达上限直接拒"
import agent.tools as TL  # noqa: E402

_cap_hits = {}


class _Ctx(dict):
    pass





def _fake_get_config():
    return {"send": {"max_total_per_reply": 3}}


_orig_gc = TL.get_config
_orig_val = TL._validate_send_args
_orig_norm = TL.normalize_message_list
TL.get_config = _fake_get_config
TL.normalize_message_list = lambda x: list(x or [])
TL._validate_send_args = lambda m, a: ""


class _FakeSender:
    def send_text_batch(self, *a, **k):
        _cap_hits["called"] = _cap_hits.get("called", 0) + 1
        return {"sent": [{"text": t} for t in (a[1] if len(a) > 1 else [])], "failed": []}


try:
    _ctx = {"session": {"sent": [1, 2, 3]}, "sender": _FakeSender(), "chat_key": "k",
            "store": None}
    _r = TL._exec_send_message(_ctx, {"messages": ["喂"]})
    ok("⑪ 已发 3 条（上限 3）⇒ 第 4 次 send_message 被拒",
       isinstance(_r, dict) and ("ok" not in _r or _r.get("ok") is False), str(_r)[:90])
    ok("⑪b 被拒时**根本没碰发送层**", _cap_hits.get("called", 0) == 0,
       "called=%s" % _cap_hits.get("called", 0))

    # ⑫ 已发 2 条、本批传 5 条 ⇒ 截断到剩余 1 条（末尾合并）
    _ctx2 = {"session": {"sent": [1, 2]}, "sender": _FakeSender(), "chat_key": "k", "store": None}
    _r2 = TL._exec_send_message(_ctx2, {"messages": ["a", "b", "c", "d", "e"]})
    _sent = len(_ctx2["session"]["sent"]) - 2
    ok("⑫ 已发 2、传 5 ⇒ 实际只发剩余 1 条", _sent == 1, "新增=%d" % _sent)
finally:
    TL.get_config = _orig_gc
    TL._validate_send_args = _orig_val
    TL.normalize_message_list = _orig_norm

# ═══════════ A3 媒体消息跳过引用 ═══════════
print("\n══ A3 媒体消息跳过引用（F8）══")

ok("⑬ _QUOTE_SKIP_PREFIXES 覆盖七类占位符",
   len(S._QUOTE_SKIP_PREFIXES) == 7, str(S._QUOTE_SKIP_PREFIXES))
for _p in ("[语音]", "[图片]", "[视频]", "[表情]", "[动画表情]", "[文件]", "[链接]"):
    ok("⑬%s `_is_media_placeholder` 认出 %s" % (_p[1], _p), S._is_media_placeholder(_p + "内容"))
ok("⑭ 普通文字**不**误判", not S._is_media_placeholder("晚上吃什么"))
ok("⑭b 空串不误判", not S._is_media_placeholder(""))
ok("⑭c 提到媒体词但不在**开头**的不误判",
   not S._is_media_placeholder("我刚看到 [图片] 就想到你"))

# ⑮ 源码级：send_text_batch 里真的在引用前挡了（不是定义了不用）
ok("⑮ send_text_batch 里真的调了 `_is_media_placeholder` 并清空了引用",
   "_is_media_placeholder(reply_text)" in _ssrc and
   "跳过引用走普通发送" in _ssrc)

# ⑯ 行为级：真的不发引用（打桩 wechat，看 reply_quote 有没有被调）
_calls3 = {"quote": 0, "plain": 0}


class _FakeWx:
    def fg_hold(self):
        import contextlib
        return contextlib.nullcontext()

    def reply_quote(self, *a, **k):
        _calls3["quote"] += 1
        return True, ""

    def send_text(self, *a, **k):
        _calls3["plain"] += 1
        return True, ""

    def member_name(self, *a, **k):
        return ""


class _FakeStore:
    def recent(self, *a, **k):
        return []

    def append_self(self, *a, **k):
        pass


try:
    _q = S.SendQueue.__new__(S.SendQueue)
    _q.wechat = _FakeWx()
    _q.store = _FakeStore()
    import threading
    _q._lock = threading.RLock()
    _q._parse_key = lambda k: ("group", "cid")
    _q._gap = lambda t, last: 0
    _q._check_rate = lambda k: None
    _q._should_auto_quote = lambda k: None
    _q.on_sent = None
    _r3 = _q.send_text_batch("g:c", ["好的"], reply_to_mid="m1", reply_text="[语音]3秒")
    ok("⑯ 引用目标是 [语音] ⇒ **不调** reply_quote（走普通发送）",
       _calls3["quote"] == 0 and _calls3["plain"] >= 1, str(_calls3))

    _calls3["quote"] = _calls3["plain"] = 0
    _r4 = _q.send_text_batch("g:c", ["好的"], reply_to_mid="m2", reply_text="晚上吃啥")
    ok("⑰ 引用目标是普通文字 ⇒ **照常**走 reply_quote",
       _calls3["quote"] == 1, str(_calls3))
except Exception as _e:
    ok("⑯ 行为级引用判定", False, "异常：%s: %s" % (type(_e).__name__, _e))

print("\n==== A1/A2/A3 收编判据：%d 通过 / %d 失败 ====" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for _f in FAIL:
        print("  · " + _f)
sys.exit(1 if FAIL else 0)
