# -*- coding: utf-8 -*-
"""「点一下」也不许动用户鼠标 —— `ui_adapt.click` 的档位闸门判据。

**为什么要它**：真机四项复测第一枪就抓到一条**红线违例**：打开表情面板时，
`ui_adapt.click()` 里是**无条件** `gui.wx_click()`（库的真实鼠标 = `SetCursorPos` + `mouse_event`）
⇒ 光标从 (233,1599) 被移到 (1564,1144) **并留在那儿**，而且它**绕过了输入档位**
（`wechat.py` 自己的切会话分支是认 `input.allow_real_fallback` 这道闸的 —— 同一份红线两个入口两套标准）。
修法＝`click()` 里按档位走：**投递优先**；投递没成且没显式开真鼠标兜底 ⇒ **这一枪不发**；
真鼠标档必须显式打开、且**用完把光标放回原处**（前后一致）。

判据用**打桩**（不碰真微信、不动真光标）：假 gui + 桩 `ensure_point` + 桩 `SetCursorPos`，
数清"到底走了哪条路"。反向锚：把开关打开 ⇒ 必须真的走真鼠标（否则这条判据只是恒真）。

跑法：runtime\\python\\python.exe scripts\\real_mouse_gate_selftest.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception: # noqa: BLE001
    pass

import _srcmatch as _sm # noqa: E402
from agent import ui_adapt as UA # noqa: E402
from agent import input_backend as IB # noqa: E402

PASS, FAIL = [0], [0]


def ok(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("  [%s]" % detail if detail else ""))
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
    return bool(cond)


class FakeGui(object):
    """假 gui：只记"谁点了我"。"""

    def __init__(self):
        self.main_hwnd = 0x111
        self.render_hwnd = 0x222
        self.origin_x = 0
        self.origin_y = 0
        self.render_rect = (0, 0, 1000, 800)
        self.render_w = 1000
        self.render_h = 800
        self.wx_click_calls = []

    def wx_click(self, x, y, right=False):
        self.wx_click_calls.append((x, y, right))


class FakePosted(object):
    """假投递后端：只记调用、并报告成功/失败。"""

    name = "message"
    touches_cursor = False

    def __init__(self, ok_=True):
        self.calls = []
        self.ok_ = ok_

    def click(self, hwnd, screen_pt, right=False, **kw):
        self.calls.append((hwnd, tuple(screen_pt), right))
        return (bool(self.ok_), "" if self.ok_ else "判据模拟：投递没成")


def _run(cfg, posted_ok=True, env=None):
    """跑一次 click()，返回 (结果, 假gui, 假投递后端, SetCursorPos 调用表)。"""
    g = FakeGui()
    p = FakePosted(posted_ok)
    real = IB.RealInputBackend(gui=g)
    _sv = (UA.ensure_point, UA.get_config, UA._user32, IB.select_backend,
           os.environ.get("WXAGENT_REAL_FALLBACK"))
    moves = []

    class _U(object):
        @staticmethod
        def SetCursorPos(x, y):
            moves.append((int(x), int(y)))
            return 1

        @staticmethod
        def GetCursorPos(ref):
            return 1

    try:
        UA.ensure_point = lambda *a, **k: (True, "判据放行")
        UA.get_config = lambda: {"input": {"allow_real_fallback": bool(cfg)},
                                 "wechat": {}, "ui": {}}
        UA._user32 = _U
        IB.select_backend = lambda cfg=None, gui=None: (p if not cfg else real)
        if env is None:
            os.environ.pop("WXAGENT_REAL_FALLBACK", None)
        else:
            os.environ["WXAGENT_REAL_FALLBACK"] = env
        UA._cursor_now = lambda: (10, 20)
        r = UA.click(g, 100, 200)
    finally:
        (UA.ensure_point, UA.get_config, UA._user32, IB.select_backend) = _sv[:4]
        if _sv[4] is None:
            os.environ.pop("WXAGENT_REAL_FALLBACK", None)
        else:
            os.environ["WXAGENT_REAL_FALLBACK"] = _sv[4]
    return r, g, p, moves


print("== A. 默认档：走投递，绝不碰真鼠标、绝不移光标 ==")
_r, _g, _p, _mv = _run(cfg=False)
ok("A1 默认档下调用的是**投递**后端", len(_p.calls) == 1 and not _g.wx_click_calls,
   "投递=%d 真鼠标=%d" % (len(_p.calls), len(_g.wx_click_calls)))
ok("A2 默认档下**一次 SetCursorPos 都没有**（不动用户鼠标）", not _mv, str(_mv[:3]))
ok("A3 投递成功 ⇒ click 返回 True", _r[0] is True, str(_r)[:60])

print("== B. 投递没成 + 没开真鼠标兜底 ⇒ **这一枪不发**（宁可不做也不动鼠标）==")
_r2, _g2, _p2, _mv2 = _run(cfg=False, posted_ok=False)
ok("B1 拒绝执行（返回 False）且理由说清是「不动鼠标」这条硬口径",
   _r2[0] is False and ("没开" in _r2[1] or "不动" in _r2[1]), str(_r2)[:90])
ok("B2 拒绝时**没有**退回真鼠标、也没有移光标",
   not _g2.wx_click_calls and not _mv2, "真鼠标=%d 移动=%d" % (len(_g2.wx_click_calls), len(_mv2)))

print("== C. 反向锚：显式打开真鼠标兜底 ⇒ 才允许真鼠标，且**用完把光标放回** ==")
_r3, _g3, _p3, _mv3 = _run(cfg=True, posted_ok=False)
ok("C1 开了兜底之后才真的走真鼠标（说明 A 段不是恒真）", len(_g3.wx_click_calls) == 1,
   "真鼠标=%d" % len(_g3.wx_click_calls))
ok("C2 真鼠标档**用完把光标放回原处**（SetCursorPos 收到还原那一次）",
   (10, 20) in _mv3, str(_mv3[:4]))
_r4, _g4, _p4, _mv4 = _run(cfg=True, posted_ok=True)
ok("C3 反向锚的另一半：开了兜底但**投递能成**时，仍然走投递（投递优先不变）",
   len(_p4.calls) == 1 and not _g4.wx_click_calls, "投递=%d 真鼠标=%d" % (len(_p4.calls), len(_g4.wx_click_calls)))

print("== D. 环境变量强制关（自检/诊断路径的兜底）==")
_r5, _g5, _p5, _mv5 = _run(cfg=True, posted_ok=False, env="0")
ok("D1 `WXAGENT_REAL_FALLBACK=0` ⇒ 即使 config 开着也不许真鼠标",
   not _g5.wx_click_calls and _r5[0] is False, str(_r5)[:80])

print("== E. 源码锚（改动面写清了理由，防后人删）==")
_SRC = io.open(os.path.join(ROOT, "agent", "ui_adapt.py"), encoding="utf-8").read()
ok("E1 `click()` 里有档位分支与「真鼠标必须显式允许」的判断",
   _sm.has(_SRC, "touches_cursor") and _sm.has(_SRC, "def _real_mouse_allowed"))
ok("E2 真鼠标档有光标还原帮手（`_cursor_restore`）", _sm.has(_SRC, "def _cursor_restore"))
ok("E3 红线违例的现场写进了注释（不然下一个人会顺手改回去）",
   _sm.has(_SRC, "真机四项复测") and _sm.has(_SRC, "(1564,1144)"))

print("== 点一下也不许动鼠标判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
sys.exit(1 if FAIL[0] else 0)
