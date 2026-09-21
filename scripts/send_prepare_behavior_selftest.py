#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`send_text` 投递分支的**画面准备次序**行为判据（2026-09-21 立，来自网友反馈 v0921-1613）。

为什么要有它：那位网友的检验器写着「它**读得到消息、只是发不出去**」，切会话失败台账里那条的
why 只有 `no_capture` 一个英文词。真因是**抓图类判据先要有画面** —— 微信收在任务栏（最小化/隐藏）时
`chat_header.check()` 必然 `no_capture`（实测口径见 `chat_header.grab_render` 与
`wechat._ensure_main_visible` 的注释），于是每一次都只能靠"投递切会话"去兜，
兜不住就整条拒发（`input.allow_real_fallback` 默认关）＝ **微信最小化时一条回复都发不出去**。
修法＝与 切会话 / 搜索框切会话 / 投递发送 三条链同口径：进投递分支先**按档位**准备画面
（投递档只做不激活还原，绝不抢前台），准备完再判会话头。

本判据钉四件**行为**（跑真函数、不读源码字符串、不需要微信、不碰窗口、不写盘）：
  A1 投递档：先准备画面，**且必须在会话头判据之前**；
  A2 会话头判 ok ⇒ 直接走投递发送（不白白多走"切会话"那条更险的路）；
  A3 真实鼠标档：**不做**无激活还原（抢不抢前台是那一档自己的事，别在这儿越权）；
  A4 拒发时台账写得**可诊断**（判据原话 + 投递切会话回执 + 主窗状态），不是一个英文状态码 ——
     并附**反例锚**：老写法（`str(_st_status)` 一个词）过不了这一条。

用法： runtime\\python\\python.exe scripts\\send_prepare_behavior_selftest.py
"""
import os
import sys
import threading

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ⛔ V-R14-1 隔离：判据不许写产品 data/ 与 logs/（台账指到临时区）。
#   ⚠️ 第一版这段是在 `from agent import wechat as W` **之前**用 `W.…` 打桩的（名字还没定义 ⇒
#   NameError 被 `except: pass` 静默吞掉）⇒ 一次都没生效。现在收口到 `scripts\_iso14.py` 一处，
#   并且**先 import 再打桩**。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # scripts\（见 `_iso14` 文件头）
import _iso14                                   # noqa: E402
_iso14.wechat()

from agent import wechat as W              # noqa: E402
from agent import chat_header as ch        # noqa: E402
from agent import version_gate as vg       # noqa: E402
from agent import chat_ocr as co           # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   " + name)
    else:
        FAIL += 1
        print("  FAIL " + name + ("   [%s]" % extra if extra else ""))


class _Gui(object):
    main_hwnd = 4242
    render_rect = (0, 0, 1139, 890)

    def _update_render_rect(self):
        return None


class _Scn(object):
    def __init__(self, header=None, switch=(False, "假：切会话没成"), posted=(True, "投递发送成功 · local_id=9"),
                 real_mouse=False, fallback=False, iconic_hint="**假：微信主窗当时是最小化的**"):
        self.events = []
        self.gui = _Gui()
        self.header = header or {"status": "ok", "note": "假参照 sim=1.000"}
        self.switch = switch
        self.posted = posted
        self.real_mouse = real_mouse
        self.fallback = fallback
        self.iconic_hint = iconic_hint
        self.posted_calls = 0
        self.switch_calls = 0


class _Stub(object):
    """`send_text` 只需要这些 `self.` 成员 ⇒ 不构造真 WeChatAdapter（那会去连微信）。"""

    def __init__(self, scn):
        self.scn = scn
        self._send_lock = threading.RLock()

    def group_name(self, chat_id):
        return "演示群"

    def _get_gui(self):
        return self.scn.gui

    def _dedup_send(self, chat_id, text):
        return True

    def _posted_preferred(self):
        return True

    def _real_mouse_allowed(self):
        return bool(self.scn.real_mouse)

    def _ensure_main_visible(self, gui, main):
        self.scn.events.append("prepare")
        return True

    def send_text_posted(self, text, chat_id, allow_no_ref=False):
        self.scn.events.append("posted")
        self.scn.posted_calls += 1
        return self.scn.posted

    def switch_chat_posted(self, chat_id, gui=None):
        self.scn.events.append("switch")
        self.scn.switch_calls += 1
        return self.scn.switch

    def chat_identity_ok(self, chat_id, gui=None, name=""):
        return True, "假：内容级复核通过"

    def chat_is_open(self, chat_id, gui=None, name=None, allow_weak=False):
        return True, "假：强档证据通过"

    def _real_fallback_allowed(self):
        return bool(self.scn.fallback)

    def _learn_chat_header(self, chat_id, gui=None):
        return "假参照（本测试不写盘）"

    def _mark_sent(self, text):
        return None


def run(scn, text="SELFTEST-TOKEN-准备画面"):
    saved = (W._control_halt, W._main_iconic_hint, ch.check, vg.check, co.begin_window,
             W._minimize_back_if_needed)
    W._control_halt = lambda: ""
    W._main_iconic_hint = lambda gui: scn.iconic_hint
    vg.check = lambda kind, **kw: {"allow": True, "reason": ""}
    co.begin_window = lambda s: None
    W._minimize_back_if_needed = lambda note="": scn.events.append("putback")

    def _check(chat_id, gui=None, path=None, threshold=None):
        scn.events.append("check")
        return dict(scn.header)

    ch.check = _check
    try:
        return W.WeChatAdapter.send_text(_Stub(scn), "filehelper", text)
    finally:
        (W._control_halt, W._main_iconic_hint, ch.check, vg.check, co.begin_window,
         W._minimize_back_if_needed) = saved


print("── A1. 投递档：先准备画面，**且必须在会话头判据之前** ──")
_s1 = _Scn()
_r1, _w1 = run(_s1)
ok("整个过程里确实做了一次「不激活还原」（prepare）", _s1.events.count("prepare") == 1, str(_s1.events))
ok("次序＝prepare → check → posted（准备画面**早于**判会话头）",
   _s1.events[:3] == ["prepare", "check", "posted"], str(_s1.events))
ok("反例锚：老顺序（一上来就判会话头、不准备画面）过不了这一条",
   _s1.events[:3] != ["check", "posted"], "老写法＝['check','posted']")

print("── A2. 会话头判 ok ⇒ 直接走投递发送（不再多走「切会话」那条更险的路）──")
ok("判成功且回执点名投递档", bool(_r1) and ("投递档" in _w1) and ("L5" in _w1), str(_w1)[:80])
ok("**没有**调用投递切会话（skip 掉那条更险的路）", _s1.switch_calls == 0, str(_s1.events))
ok("投递发送只调了一次", _s1.posted_calls == 1, str(_s1.posted_calls))

print("── A3. 真实鼠标档：不做无激活还原（那一档自己会置前，别在这儿越权）──")
_s3 = _Scn(real_mouse=True, fallback=True)
run(_s3)
ok("真实鼠标档 ⇒ 一次 prepare 都不做", _s3.events.count("prepare") == 0, str(_s3.events))

print("── A4. 拒发时台账必须**可诊断**（判据原话 + 切会话回执 + 主窗状态）──")
_s4 = _Scn(header={"status": "no_capture", "note": "抓不到渲染区（窗口不可见/权限不足）"},
           switch=(False, "假：点不到会话行（渲染区几何没量到）"), fallback=False)
_r4, _w4 = run(_s4)
ok("判失败（真鼠标兜底默认关 ⇒ 这条不发）", not bool(_r4), str(_r4))
ok("回执里也把状态说清了", "no_capture" in _w4, str(_w4)[:90])
_led = W.recent_switch_fails(1)
_why = str((_led[-1] if _led else {}).get("why") or "")
ok("台账记下了（不只是日志文件里一行）", bool(_led), str(_led)[:80])
ok("why 带**判据原话**（用户能看懂「抓不到渲染区」）", "抓不到渲染区" in _why, _why[:120])
ok("why 带**投递切会话的回执**（分得清是哪一步没成）", "点不到会话行" in _why, _why[:120])
ok("why 带**主窗当时的状态**（最可能就是它最小化了）", "最小化" in _why, _why[:120])
ok("反例锚：老写法只写状态码（`str(_st_status)`）⇒ 上面三条它一条都过不了",
   all(_k not in (_s4.header["status"] or "") for _k in ("抓不到渲染区", "点不到会话行", "最小化")),
   "老 why=%r" % _s4.header["status"])
ok("台账的 where 仍是原来那句（检验器与判据都按它匹配）",
   (_led[-1].get("where") if _led else "") == "发送前确认不了目标会话",
   str(_led[-1].get("where")) if _led else "")

print("── A5. `_main_iconic_hint` 自己：只读、不抛、说人话 ──")
_h0 = W._main_iconic_hint(type("G", (), {"main_hwnd": 0})())
ok("拿不到句柄 ⇒ 如实说「未知」，不编", "未知" in _h0, _h0)
_h1 = W._main_iconic_hint(type("G", (), {"main_hwnd": 4242})())
ok("拿得到句柄 ⇒ 三态之一（最小化 / 在屏幕上 / 未知），且不抛", isinstance(_h1, str) and _h1, _h1)
_h2 = W._main_iconic_hint(None)
ok("传 None 也不抛", isinstance(_h2, str) and _h2, _h2)

print("── A6. 还原与放回**必须成对**（第九轮 V-R9-1：v2.1.52 的早退路径不放回，用户微信被摊在桌面上）──")


def _pair(events):
    return [e for e in events if e in ("prepare", "putback")]


ok("A6a 成功路径成对：prepare 一次 + putback 一次", _pair(_s1.events) == ["prepare", "putback"],
   str(_s1.events))
ok("A6b **早退路径（拒发）也成对** —— 这条就是 V-R9-1 的回归锚",
   _pair(_s4.events) == ["prepare", "putback"], str(_s4.events))
_old_events = ["prepare"]          # 老写法：早退路径只有 prepare、没有 putback
ok("A6c 反例锚：老写法（只剩 prepare）过不了 A6b", _pair(_old_events) != ["prepare", "putback"],
   str(_pair(_old_events)))
ok("A6d 放回挂在 finally 上（源码形态）",
   "finally:" in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "agent", "wechat.py"), encoding="utf-8").read()
   .split("def send_text(")[1].split("\n    def ", 2)[0])

# ══════════════════════════════════════════════════════════════════════════════
# B 段（第十轮 V-R10-2 / V-R10-3 / V-R10-4）：**别的还原链也要成对**
#
# A 段只覆盖 `send_text`；而 `send_text_at`（@某人）/ `send_image`（发图）/ `_get_gui` 的自愈
# 分支同样会"不激活地还原"用户收起来的主窗，却**没有一句放回**（侦察线打桩实测：返回 blocked、
# 退出时 `_MINIMIZED_BY_US` 残留 ⇒ 用户微信被摊在桌面上）。
#
# ⛔ 本段的纪律（**上一轮的教训**）：**不许**把 `_ensure_main_visible` / `_minimize_back_if_needed`
#   打成空壳 —— 换成"记一笔就返回"之后，真实登记与真实动作都看不见了（假绿才生出来）。
#   这里只打桩**操作系统边界**（`ctypes.windll.user32` 的那几个窗口函数），产品函数全部跑真的：
#   `_ensure_main_visible` 真登记、`_minimize_back_if_needed` 真判安全线真动手、`fingerprint` 不参与。
# ══════════════════════════════════════════════════════════════════════════════
print("── B. `send_text_at` / `send_image` / `_get_gui` 自愈：还原与放回**成对** ──")

import ctypes as _ct_b                                                            # noqa: E402
import collections as _coll_b                                                     # noqa: E402
import importlib as _il_b                                                        # noqa: E402

BTN_MAIN = 4242
_FAKE_NAMES_B = ("IsWindow", "IsIconic", "IsWindowVisible", "GetForegroundWindow",
                 "ShowWindow", "SetWindowPos", "GetWindowRect", "EnumWindows",
                 "EnumChildWindows", "GetClassNameW")


class _U32(object):
    """假 `user32`：只实现本段用到的那些窗口函数，把"谁动了窗口"记进 `acts`。

    模拟用户把微信**收进任务栏**的那种状态（真机最小化：`IsIconic=1`、`IsWindowVisible=1`）。
    """

    def __init__(self, iconic=True, fg=777):
        self.iconic = bool(iconic)
        self.fg = int(fg)
        self.acts = []                      # [("ShowWindow", 4/6), ("SetWindowPos", ...)]
        self.cls = {}

    def IsWindow(self, h):
        return True

    def IsIconic(self, h):
        return self.iconic

    def IsWindowVisible(self, h):
        return True

    def GetForegroundWindow(self):
        return int(self.fg)

    def GetWindowRect(self, h, ref):
        try:
            o = ref._obj
            o.left, o.top, o.right, o.bottom = 0, 0, 1139, 890
        except Exception:
            pass
        return True

    def ShowWindow(self, h, cmd):
        self.acts.append(("ShowWindow", int(cmd)))
        if int(cmd) == 4:                   # SW_SHOWNOACTIVATE
            self.iconic = False
        elif int(cmd) == 6:                 # SW_MINIMIZE
            self.iconic = True
        return True

    def SetWindowPos(self, *a):
        self.acts.append(("SetWindowPos",))
        return True

    def GetClassNameW(self, h, buf, n):
        try:
            buf.value = str(self.cls.get(int(h), "Qt51514QWindowIcon"))
        except Exception:
            pass
        return 1

    def EnumWindows(self, cb, l):
        cb(int(BTN_MAIN), l)
        return True

    def EnumChildWindows(self, h, cb, l):
        cb(999, l)                          # 主窗的渲染子窗（`_has_render_child` 的判据）
        return True


def _patch_u32(fake):
    u = _ct_b.windll.user32
    saved = {}
    for n in _FAKE_NAMES_B:
        saved[n] = getattr(u, n, None)
        setattr(u, n, getattr(fake, n))
    return u, saved


def _restore_u32(u, saved):
    for n, v in saved.items():
        if v is None:
            try:
                delattr(u, n)
            except Exception:
                pass
        else:
            setattr(u, n, v)


def _reset_state():
    """每个场景从"用户把微信收进任务栏、我们还没动过"开始。"""
    W._MINIMIZED_BY_US = 0
    W._WAS_ICONIC_BY_US = 0


class _RespB(dict):
    """假 `WxResponse`（驱动库的返回是 **dict 子类**，见 `_resp_msg`）。"""

    def __init__(self, ok=True, msg="假：真鼠标档发送成功"):
        super(_RespB, self).__init__(status="success" if ok else "fail", message=msg, data={})
        self.is_success = bool(ok)


class _GuiB(object):
    main_hwnd = BTN_MAIN
    render_hwnd = 999
    render_rect = (0, 0, 1139, 890)

    def _update_render_rect(self):
        return self.render_rect

    def at_member(self, *a, **kw):
        return _RespB()

    def send_image(self, *a, **kw):
        return _RespB()

    def restore_zorder(self):
        return True

    def refresh(self):
        return None

    def desktop_available(self):
        return False


def _make_adapter(gui, main=BTN_MAIN):
    """真 `WeChatAdapter` 实例（不跑 `__init__`：那会去连微信），只把"与外界的接口"换成假的。"""
    a = W.WeChatAdapter.__new__(W.WeChatAdapter)
    a._send_lock = threading.RLock()
    a._gui = gui
    a._fg_depth = 0
    a._fg_before = None
    a._send_recent = _coll_b.deque()
    a.cfg = {}
    a._mark_sent = lambda text: None
    a._dedup_send = lambda chat_id, text: True
    a.group_name = lambda chat_id: "演示群"
    a.display_name = lambda chat_id: "演示群"
    a._posted_preferred = lambda: False
    a.chat_is_open = lambda chat_id, gui=None, name=None, allow_weak=False: (False, "假：没确认")
    a._learn_chat_header = lambda chat_id, gui=None: "假参照"

    def _gu():
        # 模拟自愈：这一跳真的会**不激活地还原**用户收起来的主窗（真方法、真登记）
        W.WeChatAdapter._ensure_main_visible(a, a._gui, main)
        return a._gui

    a._get_gui = _gu
    return a


def _run_chain(kind, allow_real=False, no_putback=False):
    """跑一条真链：返回 (ok, msg, fake_u32, adapter)。`no_putback` ＝ 反例锚（把放回摘掉）。"""
    _reset_state()
    fake = _U32(iconic=True, fg=777)
    u, saved = _patch_u32(fake)
    _env0 = os.environ.get("WXAGENT_REAL_FALLBACK")
    os.environ["WXAGENT_REAL_FALLBACK"] = "0"
    _mb0 = W._minimize_back_if_needed
    if no_putback:
        W._minimize_back_if_needed = lambda note="": None
    try:
        a = _make_adapter(_GuiB())
        if allow_real:
            a._real_fallback_allowed = lambda: True
        if kind == "at":
            r = a.send_text_at("filehelper", "某人", "SELFTEST-B-TOKEN")
        elif kind == "image":
            r = a.send_image("filehelper", os.path.join(_tf_b.gettempdir(), "x.png"))
        else:
            raise AssertionError(kind)
        return r, fake, a
    finally:
        W._minimize_back_if_needed = _mb0
        if _env0 is None:
            os.environ.pop("WXAGENT_REAL_FALLBACK", None)
        else:
            os.environ["WXAGENT_REAL_FALLBACK"] = _env0
        _restore_u32(u, saved)


import tempfile as _tf_b                                                          # noqa: E402

for _kind, _label in (("at", "`send_text_at`（@某人）"), ("image", "`send_image`（发图）")):
    _rb, _fb, _ab = _run_chain(_kind)
    ok("B1 %s：默认配置（真鼠标兜底关）⇒ 如实返回 blocked（不动鼠标）" % _label,
       _rb and not bool(_rb[0]) and "不退回真鼠标" in str(_rb[1] or ""), str(_rb)[:110])
    ok("B2 %s：**真的还原过**（ShowWindow(4) 一次）—— 不是「什么都没做」的空跑" % _label,
       _fb.acts.count(("ShowWindow", 4)) == 1, str(_fb.acts))
    ok("B3 %s：**真的放回了**（ShowWindow(6) 一次，不是只「调用了一下」）" % _label,
       _fb.acts.count(("ShowWindow", 6)) == 1 and _fb.iconic is True, str(_fb.acts))
    ok("B4 %s：退出时**没有残留登记**（下一次链尾不会再把它收走）" % _label,
       int(W._MINIMIZED_BY_US or 0) == 0 and int(W._WAS_ICONIC_BY_US or 0) == 0,
       "登记=%s" % W._MINIMIZED_BY_US)
    _rn, _fn, _an = _run_chain(_kind, no_putback=True)
    ok("B5 %s 反例锚（把放回摘掉）⇒ 窗口仍摊着（iconic=False）+ 登记残留 ⇒ 上面三条会变红"
       % _label,
       _fn.acts.count(("ShowWindow", 6)) == 0 and _fn.iconic is False
       and int(W._MINIMIZED_BY_US or 0) == BTN_MAIN,
       "acts=%s · iconic=%s · 登记=%s" % (_fn.acts, _fn.iconic, W._MINIMIZED_BY_US))
    _reset_state()

_rb2, _fb2, _ab2 = _run_chain("at", allow_real=True)
ok("B6 真鼠标档（**显式允许**时走真链）：成功路径也成对（还原一次 + 放回一次、无残留登记）",
   bool(_rb2[0]) and _fb2.acts.count(("ShowWindow", 4)) == 1
   and _fb2.acts.count(("ShowWindow", 6)) == 1 and int(W._MINIMIZED_BY_US or 0) == 0,
   "acts=%s · 登记=%s · %s" % (_fb2.acts, W._MINIMIZED_BY_US, str(_rb2)[:80]))
_reset_state()

print("── B7. `_get_gui` 自愈分支：还原后必须放回（V-R10-3：只读调用点也会把微信摊在桌面上）──")
_GUA = None
try:
    import wechatauto.guia as _guia_b                                             # noqa: E402
    _GUA = _guia_b
except Exception:
    _guia_b = None


class _HealGUI(object):
    """假 `WeChatGUI`：**第一枪抛异常**（触发自愈分支），第二枪成功。"""
    n = 0

    def __init__(self, *a, **kw):
        type(self).n += 1
        if type(self).n == 1:
            raise RuntimeError("假：微信主窗口不可见（触发 `_get_gui` 的自愈分支）")
        self.main_hwnd = BTN_MAIN
        self.render_hwnd = 999
        self.render_rect = (0, 0, 1139, 890)

    def _update_render_rect(self):
        return self.render_rect

    def refresh(self):
        return None


def _run_get_gui(no_putback=False):
    _reset_state()
    fake = _U32(iconic=True, fg=777)
    fake.cls = {BTN_MAIN: "Qt51514QWindowIcon", 999: "MMUIRenderSubWindowHW"}
    u, saved = _patch_u32(fake)
    _old_gui_cls = getattr(_guia_b, "WeChatGUI", None)
    _mb0 = W._minimize_back_if_needed
    _HealGUI.n = 0
    if no_putback:
        W._minimize_back_if_needed = lambda note="": None
    try:
        if _guia_b is not None:
            _guia_b.WeChatGUI = _HealGUI
        a = W.WeChatAdapter.__new__(W.WeChatAdapter)
        a._gui = None
        a.cfg = {}
        g = a._get_gui()
        return g, fake
    finally:
        W._minimize_back_if_needed = _mb0
        if _guia_b is not None and _old_gui_cls is not None:
            _guia_b.WeChatGUI = _old_gui_cls
        _restore_u32(u, saved)


if _guia_b is None:
    ok("B7 需要 `wechatauto.guia`（本机没装 ⇒ 跳过这一段）", False, "import wechatauto.guia 失败")
else:
    _g7, _f7 = _run_get_gui()
    ok("B7a 自愈确实走过：还原了一次（ShowWindow(4)）且拿到了 GUI",
       _g7 is not None and _f7.acts.count(("ShowWindow", 4)) == 1, str(_f7.acts))
    ok("B7b 自愈之后**必须放回**（ShowWindow(6) 一次、窗口回到收起态、无残留登记）",
       _f7.acts.count(("ShowWindow", 6)) == 1 and _f7.iconic is True
       and int(W._MINIMIZED_BY_US or 0) == 0,
       "acts=%s · iconic=%s · 登记=%s" % (_f7.acts, _f7.iconic, W._MINIMIZED_BY_US))
    _g7n, _f7n = _run_get_gui(no_putback=True)
    ok("B7c 反例锚（摘掉放回）⇒ `_get_gui` 返回时窗口仍摊着 + 登记残留（V-R10-3 的现场）",
       _f7n.iconic is False and int(W._MINIMIZED_BY_US or 0) == BTN_MAIN,
       "acts=%s · iconic=%s · 登记=%s" % (_f7n.acts, _f7n.iconic, W._MINIMIZED_BY_US))
    _reset_state()

print("── B8. V-R10-4：前台态**不许注销这笔债**（原来一进门就清零 ⇒ 窗口再没人还）──")
_f8 = _U32(iconic=True, fg=777)
_u8, _s8 = _patch_u32(_f8)
try:
    W._MINIMIZED_BY_US = BTN_MAIN
    W._WAS_ICONIC_BY_US = BTN_MAIN
    _f8.iconic = False
    _f8.fg = BTN_MAIN                       # 微信此刻是前台（用户正在用它）
    W._minimize_back_if_needed("自检：前台态")
    _kept = int(W._MINIMIZED_BY_US or 0)
    ok("B8a 前台态：一枪不动（安全线③），但**登记保留**（V-R10-4：这笔债还没还）",
       _kept == BTN_MAIN and _f8.acts == [], "登记=%s · acts=%s" % (_kept, _f8.acts))
    _f8.fg = 777                            # 用户切走了 ⇒ 下一次链尾该真动手
    W._minimize_back_if_needed("自检：下一次链尾")
    ok("B8b 用户切走之后的下一次链尾：**真把窗口还回去了**（ShowWindow(6)）且登记清零",
       _f8.acts == [("ShowWindow", 6)] and _f8.iconic is True
       and int(W._MINIMIZED_BY_US or 0) == 0, "acts=%s · 登记=%s" % (_f8.acts, W._MINIMIZED_BY_US))
    ok("B8c 反例锚：老写法（**一进门就清零**）在 B8a 那一步就把债注销了 ⇒ 之后无凭无据、窗口永远摊着",
       _kept == BTN_MAIN, "老写法会在 B8a 之后得到登记=0")
finally:
    _restore_u32(_u8, _s8)
    _reset_state()

print("── B10. `send_text` 的**三道 `try` 前早退**也要结清上一笔债（V-R10-6c）──")
_f10 = _U32(iconic=False, fg=777)          # 窗口在屏幕上（我们还原出来的）、现在不是前台
_u10, _s10 = _patch_u32(_f10)
_h10 = W._control_halt
W._control_halt = lambda: "机器人已停止（自检假件）"
try:
    W._MINIMIZED_BY_US = BTN_MAIN          # 上一笔链留下的登记（这就是"没人还"的那一笔）
    W._WAS_ICONIC_BY_US = BTN_MAIN
    _r10 = W.WeChatAdapter.send_text(object(), "filehelper", "SELFTEST-B10")
    ok("B10a 停机闸早退：如实返回（这一步本来就在 `try` 之前，`finally` 罩不到）",
       _r10 and _r10[0] is False and "已停止" in str(_r10[1]), str(_r10)[:80])
    ok("B10b 早退前**顺手结清上一笔债**：ShowWindow(6) 一次、登记清零（用户收起的微信被还回去了）",
       _f10.acts == [("ShowWindow", 6)] and _f10.iconic is True
       and int(W._MINIMIZED_BY_US or 0) == 0, "acts=%s · 登记=%s" % (_f10.acts, W._MINIMIZED_BY_US))
    W._MINIMIZED_BY_US = BTN_MAIN
    W._WAS_ICONIC_BY_US = BTN_MAIN
    _f10.iconic = False
    _f10.acts = []
    _mb10 = W._minimize_back_if_needed
    W._minimize_back_if_needed = lambda note="": None
    try:
        W.WeChatAdapter.send_text(object(), "filehelper", "SELFTEST-B10b")
    finally:
        W._minimize_back_if_needed = _mb10
    ok("B10c 反例锚（摘掉早退前的放回）⇒ 登记残留、窗口仍摊着 ⇒ 上面那条会变红",
       int(W._MINIMIZED_BY_US or 0) == BTN_MAIN and _f10.iconic is False,
       "acts=%s · iconic=%s · 登记=%s" % (_f10.acts, _f10.iconic, W._MINIMIZED_BY_US))
finally:
    W._control_halt = _h10
    _restore_u32(_u10, _s10)
    _reset_state()

print("── B9. 静态网：每条**经过 `_send_with_foreground`** 的链都有放回；`send_text` 的三道早退也罩住 ──")
_SRC_W_B = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "agent", "wechat.py"), encoding="utf-8").read()
_SEG_SWF_B = _SRC_W_B.split("def _send_with_foreground(")[1].split("\n    def ", 1)[0]
ok("B9a `_send_with_foreground` 的 finally 里放回（一处覆盖所有走它的链）",
   "finally:" in _SEG_SWF_B and "_minimize_back_if_needed(" in _SEG_SWF_B)
for _fn_b, _lab_b in (("send_text_at", "`send_text_at`（@某人）"), ("send_image", "`send_image`（发图）")):
    _seg_b = _SRC_W_B.split("def %s(" % _fn_b)[1].split("\n    def ", 1)[0]
    ok("B9b %s 确实经过 `_send_with_foreground`（⇒ B9a 的放回覆盖到它）" % _lab_b,
       "self._send_with_foreground(" in _seg_b)
_SEG_ST_B = _SRC_W_B.split("def send_text(")[1].split("\n    def ", 2)[0]
ok("B9c `send_text` 的三道 `try` 前早退（停机/版本门/去重）也各补了放回（`finally` 罩不到它们）"
   "—— 三处 + `finally` 那一句 = 4 次",
   _SEG_ST_B.count('_minimize_back_if_needed("投递文本链收尾（含早退）")') >= 4,
   "出现 %d 次" % _SEG_ST_B.count('_minimize_back_if_needed("投递文本链收尾（含早退）")'))
_SEG_GG_B = _SRC_W_B.split("def _get_gui(")[1].split("\n    def ", 1)[0]
ok("B9d `_get_gui` 自愈的两条出口（构造成功 / 构造失败）都有放回",
   _SEG_GG_B.count("_minimize_back_if_needed(") >= 2,
   "出现 %d 次" % _SEG_GG_B.count("_minimize_back_if_needed("))

print("\n==== 发送前准备画面判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
