#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`send_text_posted` **三枪发送循环**的离线行为测试（不需要微信、不碰鼠标、不弹任何窗）。

为什么要有它：`background_selftest.py` 对这条链只做**源码结构**断言（"有没有那个 for / 那句字符串"），
"第一枪就成了会不会还多打两枪""三枪都打出去了但都没生效，到底判 未证实 还是 失败""三枪一次都没
打出去时说什么"这类**行为**它一句都看不见。这里用**假后端 + 假库 + 假时钟**把整段循环真跑一遍。

纪律（沿袭本项目既有口径）：
  · 成功判据**只有 DB 回读** —— 端点返回 True 不算数（S3 就是拿这个当反面证据的）；
  · 判据不可用（`db_alive` 假）⇒ `sent_unverified`，**绝不当成功**；
  · 全程零副作用：不 import 真窗口、不 sleep 真时间（假时钟）、不写盘、不碰用户的微信。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import wechat as W                    # noqa: E402
from agent import input_backend as ib            # noqa: E402
from agent import chat_header as ch              # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


# ── 假时钟：`wechat.time` 换掉 ⇒ sleep 推进假时间，不真等 15 秒 ────────────────
class _Clock(object):
    def __init__(self):
        self.t = 1_000_000.0

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += float(s)


# ── 剧本：一次"发送动作"取一条 (投递调用成功吗, 消息真的落库了吗) ──
class _Scenario(object):
    def __init__(self, script, alive=(True, "读得到（假库）")):
        self.script = list(script)      # [("keys"|"sendbtn", post_ok, lands), ...]
        self.i = 0
        self.alive = alive
        self.calls = []                 # 后端调用顺序（含聚焦点击与打字）
        self.kinds = []                 # 只记发送动作的种类
        self.rows = [{"local_id": 100, "content": "旧消息", "type": "文本"}]
        self.text = "SELFTEST-TOKEN-三枪"
        self.fg_restores = 0
        self.learned = 0
        self.ibox = (101, 690, 1240, 900)      # 假"实测输入框"（渲染相对），上半部分够放落点

    def _mk_row(self):
        self.rows = [{"local_id": 100 + len(self.rows) + 1, "content": self.text, "type": "文本"}] + self.rows

    def send_attempt(self, kind, hwnd):
        """返回 (post_ok, why)；`kind` = 'keys'（回车）| 'sendbtn'（点发送按钮）。"""
        self.kinds.append(kind)
        if self.i >= len(self.script):
            # 剧本用完了还来第 4 枪 ⇒ 记下来（"最多少枪"本身就是要断言的事）
            self.i += 1
            return False, "剧本外"
        _k, post_ok, lands = self.script[self.i]
        self.i += 1
        if post_ok and lands:
            self._mk_row()
        return (True, "") if post_ok else (False, "假后端：这一枪没打出去")


class _FakeBackend(ib.MessageBackend):
    """投递档的替身：只记录调用 + 走剧本，绝不碰 ctypes/窗口。"""

    def __init__(self, scn):
        super().__init__(press_ms=0, activate=False)
        self.scn = scn

    def _wake(self, hwnd):          # 真实现会发 WM_ACTIVATE（这步在本测试里没有意义）
        return None

    def click(self, hwnd, screen_pt, right=False, hover_ms=0, press_ms=None):
        pt = tuple(int(v) for v in screen_pt)
        self.scn.calls.append(("click", pt))
        # 这一行（渲染区 0.945·h）上有两个点：靠左 0.45 是"聚焦输入栏"，靠右 0.932 是"发送按钮"
        if pt[0] > self.scn.focus_pt[0] + 200:
            return self.scn.send_attempt("sendbtn", hwnd)
        return True, ""

    def send_text(self, hwnd, text):
        self.scn.calls.append(("send_text", text))
        return True, ""

    def keys(self, hwnd, vks, hold_ms=30):
        self.scn.calls.append(("keys", tuple(int(v) for v in vks)))
        return self.scn.send_attempt("keys", hwnd)


class _FakeDB(object):
    def __init__(self, scn):
        self.scn = scn

    def get_messages(self, chat_id, limit=12):
        return list(self.scn.rows)


class _FakeGui(object):
    main_hwnd = 4242
    render_rect = (101, 90, 1240, 980)     # 1139×890，本机 150% DPI 的实测值

    def _update_render_rect(self):
        return None


class _Stub(object):
    """`send_text_posted` 只需要这些 `self.` 成员 ⇒ 不构造真的 WeChatAdapter（那会去连微信）。"""

    def __init__(self, scn):
        self._scn = scn
        self._db = _FakeDB(scn)
        self._gui = _FakeGui()

    def _get_gui(self):
        return self._gui

    def _ensure_main_visible(self, gui, main):
        return None

    def chat_is_open(self, chat_id, gui=None, name=None, allow_weak=False):
        return True, "假证据：不需要（本测试的会话头校验已被打桩为 ok）"

    def chat_identity_ok(self, chat_id, gui=None, name=""):
        return True, "假证据：内容级判据（本测试打桩）"

    # ── 2026-09-18 新增契约：点击咽喉点（真方法借过来用，判据要能过）──
    _wx_toplevel_windows = lambda self, *a, **k: {}
    _click_posted = W.WeChatAdapter._click_posted
    # ⚡ 2026-09-18 深夜："用户在忙（全屏游戏/演示）⇒ 不动窗"这条闸也要打桩：
    #   桩对象永远比产品少方法（本判据因此整段崩过一次：rc=1 / None/None），
    #   主路径新增调用一律先探测再调用，判据这边同步补桩。
    _busy_reason = lambda self, *a, **k: ""

    def _learn_chat_header(self, chat_id, gui=None):
        self._scn.learned += 1
        return "假参照（本测试不写盘）"

    def db_alive(self, chat_id):
        return self._scn.alive


def run(script, alive=(True, "读得到（假库）"), mode="measured", ink=None, halted=False):
    """跑一遍真函数，返回 (result, why, scn)。

    `mode`（2026-09-18 起聚焦落点改为**运行时现算**，见 `_input_top_band`）：
      · `measured`＝量得到输入框（正常）；· `nobody`＝量不到（拿不到窗口自身画面）；
      · `toolbar`＝量出来的落点掉进工具栏带（0.92·h 那排图标，含 ✂ 截图）。
    `ink`＝阳性对照读到的深色点数（None ⇒ 用 12＝正常；给 0 ⇒ 演"字没进框"）。
    `halted`＝盘上有 `data/stopped.flag` 时的行为（**必须打桩**：这条判据原来会读**真机器**上的
      停止标记 ⇒ 只要机器人是停着的，本判据就整段假红，2026-09-18 实测就是它把发布门卡住的）。
    """
    scn = _Scenario(script, alive=alive)
    # ⛔ 2026-09-17：聚焦落点从 `0.945·h`（输入框下沿再往下那排工具图标，含 ✂ 截图）抬进正文区；
    # 🔴 2026-09-18：再改成**现算输入框上半部分**（`_input_top_band`）—— 按比例猜点在"引用长消息"
    #   时正好落到引用条/✕ 上。⇒ 本测试改成**打桩那个现算函数**，judge 只钉"顺序 + 不许猜"。
    scn.focus_pt = (int(101 + 1139 * 0.45), 780)                       # (613, 780) 上半部分
    if mode == "toolbar":
        scn.focus_pt = (int(101 + 1139 * 0.45), int(90 + 890 * 0.95))  # 掉进工具栏带
    stub = _Stub(scn)
    backend = _FakeBackend(scn)

    saved = (W.time, ib.select_backend, ch.check, W._stash_fg,
             W._restore_fg_until, W._minimize_back_if_needed,
             W._input_top_band, W._probe_input_box_frame, W._input_ink,
             W._control_halt)
    W.time = _Clock()
    # ⛔ 暂停/停止标记**必须打桩**：真跑时它读的是盘上的 `data/stopped.flag`（本机确实存在）⇒
    #    不打桩的话，只要机器人是停着的，本判据整段假红（2026-09-18 实测：它把发布门卡住了）。
    W._control_halt = (lambda: "机器人已停止 ⇒ 这条不发") if halted else (lambda: "")

    def _restore(tag, timeout=2.5, keep=False):
        scn.fg_restores += 1
        return None

    ib.select_backend = lambda cfg=None, gui=None: backend
    ch.check = lambda chat_id: {"status": "ok", "note": "假参照（本测试打桩）"}
    W._stash_fg = lambda: None
    W._restore_fg_until = _restore
    W._minimize_back_if_needed = lambda tag: None
    W._input_top_band = ((lambda gui, band_px=20: (None, None)) if mode == "nobody"
                         else (lambda gui, band_px=20: (scn.ibox, scn.focus_pt)))
    W._probe_input_box_frame = lambda gui: scn.ibox
    W._input_ink = (lambda gui, box, strip=80: ink) if ink is not None else (lambda gui, box, strip=80: 12)
    try:
        res, why = W.WeChatAdapter.send_text_posted(
            stub, scn.text, chat_id="filehelper", wait_s=15.0)
    finally:
        (W.time, ib.select_backend, ch.check, W._stash_fg,
         W._restore_fg_until, W._minimize_back_if_needed,
         W._input_top_band, W._probe_input_box_frame, W._input_ink,
         W._control_halt) = saved
    return res, why, scn


# ══════════════════════════════════════════════════════════════════════════
print("── A. 前三步顺序：先投递点输入栏聚焦 → 投递打字 → 才谈提交 ──")
_s1 = [("keys", True, True)]
_r1, _w1, _c1 = run(_s1)
_front = [c[0] for c in _c1.calls[:3]]
ok("顺序＝click(聚焦) → send_text(打字) → keys(回车)",
   _front == ["click", "send_text", "keys"], str(_front))
ok("聚焦点＝**现算出来的**输入框上半部分（不是按比例猜）",
   _c1.calls[0][1] == _c1.focus_pt, "%s vs %s" % (_c1.calls[0][1], _c1.focus_pt))
ok("聚焦落点**必须在工具栏带（0.92·h）之上**（那排图标里有 ✂ 截图，2026-09-17 的事故点）",
   _c1.calls[0][1][1] < int(90 + 890 * 0.92),
   "y=%s 上限=%d" % (_c1.calls[0][1][1], int(90 + 890 * 0.92)))
ok("打字用的就是本次文本", _c1.calls[1][1] == _c1.text, str(_c1.calls[1][1])[:24])

print("── A2. 量不到输入框 ⇒ **一枪都不下**（作者口径：不许按比例猜点），发送照走 ──")
_rA, _wA, _cA = run(_s1, mode="nobody")
ok("量不到 ⇒ 直接打字，没有那一次 click",
   [c[0] for c in _cA.calls[:2]] == ["send_text", "keys"], str([c[0] for c in _cA.calls[:2]]))
ok("仍然判成功（靠打字 + 回车，DB 回读照跑）", _rA == W.V_OK, "%r" % (str(_rA),))

print("── A3. 现算落点掉进工具栏带（✂ 那一排）⇒ 按红线跳过这一枪 ──")
_rB, _wB, _cB = run(_s1, mode="toolbar")
ok("落点在工具栏带 ⇒ 同样不下这一枪",
   [c[0] for c in _cB.calls[:2]] == ["send_text", "keys"], str([c[0] for c in _cB.calls[:2]]))

print("── A4. 阳性对照：打完字输入框上沿带深色点 = 0 ⇒ 如实判失败，不许再猜点补一枪 ──")
_rC, _wC, _cC = run(_s1, ink=0)
ok("判失败（不是「未生效」也不是成功）", not bool(_rC), "%r" % (str(_rC),))
ok("文案点明「输入框没吃到字」+「不许按比例猜点」",
   ("没吃到字" in _wC) and ("猜点" in _wC), _wC)
ok("失败后**没有**再补一枪（枪序只有 click + send_text）",
   [c[0] for c in _cC.calls] == ["click", "send_text"], str([c[0] for c in _cC.calls]))

print("── B. 第一枪（回车）就成功：判成功、且**不许**再多打枪 ──")
ok("判 V_OK", _r1 == W.V_OK, "result=%r" % (str(_r1),))
ok("说明里写清是第几枪、哪一枪打的", ("第 1 枪" in _w1) and ("投递回车" in _w1), _w1)
ok("发送动作只发生 1 次（不多打）", len(_c1.kinds) == 1, str(_c1.kinds))
ok("成功说明带 DB 回读证据（local_id / type）", ("local_id" in _w1) and ("type" in _w1), _w1)
ok("成功后补学了该尺寸的会话头参照", _c1.learned >= 1, "learned=%d" % _c1.learned)
ok("每枪之后都还了一次前台", _c1.fg_restores == 1, "fg_restores=%d" % _c1.fg_restores)

print("── C. 前两枪不生效、第三枪（回车）成功：三枪都走投递回车（兜底不再点按钮） ──")
# ⛔ 2026-09-17：兜底那枪**不再点「发送」按钮**（那一排里有 ✂ 截图，用户报过"微信自己弹截图"）
_s2 = [("keys", True, False), ("keys", True, False), ("keys", True, True)]
_r2, _w2, _c2 = run(_s2)
ok("判 V_OK", _r2 == W.V_OK, "result=%r" % (str(_r2),))
ok("命中在第 3 枪", "第 3 枪" in _w2, _w2)
ok("枪序＝三枪都是投递回车（**兜底也绝不点发送按钮**）",
   _c2.kinds == ["keys", "keys", "keys"], str(_c2.kinds))
ok("每枪之后都还了一次前台（3 枪 ⇒ 3 次）", _c2.fg_restores == 3, "fg_restores=%d" % _c2.fg_restores)

print("── D. 三枪都打出去了、但 DB 一直没有新行 ⇒ 判「未生效」并点名文字可能还在输入框里 ──")
_s3 = [("keys", True, False), ("keys", True, False), ("keys", True, False)]
_r3, _w3, _c3 = run(_s3)
ok("**端点都返回成功也不算成功**（唯一判据是 DB 回读）", str(_r3) == "not_sent", "result=%r" % (str(_r3),))
ok("文案点名「文字可能还留在输入框里」并让人去看那个会话",
   ("文字可能还留在输入框里" in _w3) and ("微信里看" in _w3), _w3)
ok("说明里写了打了几枪", "3 枪" in _w3, _w3)
ok("枪数不多不少正好 3 次", len(_c3.kinds) == 3, str(_c3.kinds))

print("── E. 三枪都打出去了、但**判据不可用** ⇒ 判「未证实」，绝不判失败也绝不判成功 ──")
_r4, _w4, _c4 = run(_s3, alive=(False, "既没拿到主密钥、也没有任何能过页1校验的缓存密钥"))
ok("判 sent_unverified（不是 ok、也不是 not_sent）", str(_r4) == "sent_unverified", "result=%r" % (str(_r4),))
ok("文案写明「判据不可用」+ 具体原因", ("判据不可用" in _w4) and ("主密钥" in _w4), _w4)
ok("未证实**不是成功**（`bool()` 为假 ⇒ 调用方的 `if ok:` 不会误当成功）", not bool(_r4))

print("── F. 三枪一次都没打出去（投递调用本身失败）⇒ 判失败并说清打不出去 ──")
_s5 = [("keys", False, False), ("sendbtn", False, False), ("keys", False, False)]
_r5, _w5, _c5 = run(_s5)
ok("判失败（falsy）", not bool(_r5), "result=%r" % (str(_r5),))
ok("文案＝三枪都没打出去（不是含糊的「未生效」）", "三枪都没打出去" in _w5, _w5)
ok("失败的每枪原因不会被悄悄丢掉（返回里点到具体哪一枪）", "回车" in _w5, _w5)

print("── G. 假库没被误用：DB 回读读的是**目标会话** ──")
_seen = {}


class _SpyDB(object):
    def __init__(self, scn):
        self.scn = scn

    def get_messages(self, chat_id, limit=12):
        _seen["chat_id"] = chat_id
        return list(self.scn.rows)


_scn7 = _Scenario([("keys", True, True)])
_scn7.focus_pt = (613, 931)
_stub7 = _Stub(_scn7)
_stub7._db = _SpyDB(_scn7)
_saved = (W.time, ib.select_backend, ch.check, W._stash_fg, W._restore_fg_until,
          W._minimize_back_if_needed, W._control_halt)
W.time = _Clock()
ib.select_backend = lambda cfg=None, gui=None: _FakeBackend(_scn7)
ch.check = lambda chat_id: {"status": "ok", "note": "假"}
W._stash_fg = lambda: None
W._restore_fg_until = lambda tag, timeout=2.5, keep=False: None
W._minimize_back_if_needed = lambda tag: None
W._control_halt = lambda: ""
try:
    W.WeChatAdapter.send_text_posted(_stub7, _scn7.text, chat_id="filehelper", wait_s=15.0)
finally:
    (W.time, ib.select_backend, ch.check, W._stash_fg, W._restore_fg_until,
     W._minimize_back_if_needed, W._control_halt) = _saved
ok("回读打的就是传进来的 chat_id", _seen.get("chat_id") == "filehelper", str(_seen))

print("── H. 盘上有停止标记（真跑时的 `data/stopped.flag`）⇒ **一枪都不下**、如实说已停止 ──")
# 这条是 2026-09-18 补的：本判据原来会读**真机器**上的停止标记（本机确实有 `data/stopped.flag`）
# ⇒ 只要机器人是停着的，A 段整段假红（calls=[]），把"发布前全绿"这道门卡死。现在标记一律打桩，
# 并**把这条行为本身写成断言**（作者口径：暂停/停止必须是硬冻结，任何路径都不许再动手）。
_rH, _wH, _cH = run(_s1, halted=True)
ok("停止中 ⇒ 一枪都不下", _cH.calls == [], str(_cH.calls))
ok("判失败且说明写清是「已停止」", (not bool(_rH)) and ("已停止" in _wH), "%r %s" % (str(_rH), str(_wH)[:90]))

print("── I. 快路径：输入那一跳压到最短、输完立刻回后台（作者 2026-09-19 口径）──")
_src_w = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "agent", "wechat.py"), encoding="utf-8").read()
ok("`_send_text_fast` 存在", "def _send_text_fast(" in _src_w)
ok("它排在「聚焦输入栏」那一枪**之前**（能省掉点框）",
   _src_w.index("self._send_text_fast(") < _src_w.index("投递聚焦输入栏"))
_seg_f = _src_w.split("def _send_text_fast(")[1]
_seg_f = _seg_f[:_seg_f.find("\n    def ", 10)]
ok("**全程不点任何东西**（不点输入框、不点发送按钮）",
   "backend.click(" not in _seg_f and "_click_posted(" not in _seg_f)
ok("顺序＝投字 → 80ms → 投回车 → **立刻还前台**",
   "time.sleep(0.08)" in _seg_f and "VK_RETURN" in _seg_f and "快路径（打完立刻还）" in _seg_f)
ok("成功**只认 DB 回读**，没等到就 `return None`（回退老链，不谎报）",
   "get_messages(chat_id, limit=3)" in _seg_f and "return None" in _seg_f)
ok("快路径失败后**原样回退老链**（多枪兜底还在）", "回退老链" in _src_w)

print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
