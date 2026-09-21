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
    saved = (W._control_halt, W._main_iconic_hint, ch.check, vg.check, co.begin_window)
    W._control_halt = lambda: ""
    W._main_iconic_hint = lambda gui: scn.iconic_hint
    vg.check = lambda kind, **kw: {"allow": True, "reason": ""}
    co.begin_window = lambda s: None

    def _check(chat_id, gui=None, path=None, threshold=None):
        scn.events.append("check")
        return dict(scn.header)

    ch.check = _check
    try:
        return W.WeChatAdapter.send_text(_Stub(scn), "filehelper", text)
    finally:
        (W._control_halt, W._main_iconic_hint, ch.check, vg.check, co.begin_window) = saved


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

print("\n==== 发送前准备画面判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
