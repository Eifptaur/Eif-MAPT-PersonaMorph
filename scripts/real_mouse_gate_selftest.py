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
from agent import config as cfg_mod # noqa: E402

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
    """跑一次 click()，返回 (结果, 假gui, 假投递后端, SetCursorPos 调用表)。

    ⛔ **打桩要打在"真源"上**：真鼠标那两问的实现已收口到 `agent/guards.py`，
    它读的是 `agent.config.get_config`（唯一真源）。只打 `ui_adapt.get_config` 是**打不到**的 ——
    闸看不见这个桩，会按真实配置（默认关）拒绝，于是"开了兜底也不走真鼠标"（假红）。
    """
    g = FakeGui()
    p = FakePosted(posted_ok)
    real = IB.RealInputBackend(gui=g)
    _sv = (UA.ensure_point, UA.get_config, UA._user32, IB.select_backend,
           os.environ.get("WXAGENT_REAL_FALLBACK"), cfg_mod.get_config)
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
        _fake = lambda: {"input": {"allow_real_fallback": bool(cfg)}, # noqa: E731
                         "wechat": {}, "ui": {}}
        UA.get_config = _fake
        cfg_mod.get_config = _fake # ⛔ 闸读的是这一处（唯一真源），只打上面那个等于没打
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
        cfg_mod.get_config = _sv[5]
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

print("== F. 反向锚：`_scroll_to_bottom` 不许再退回裸真鼠标（★安全红线，桌面清空事故路径）==")
_WSRC = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
# 取 `_scroll_to_bottom` 的函数正文（按 AST，别用 find——函数一搬家就切错）
try:
    import _srcslice as _ss
    _sb = _ss.func_src(_WSRC, "_scroll_to_bottom")
except Exception as e:  # noqa: BLE001
    _sb = ""
    print("（取 `_scroll_to_bottom` 失败：%s）" % str(e)[:60])


def _code_only(seg: str) -> str:
    """去掉函数里的 **docstring 与 `#` 注释**，只留可执行代码。

    为什么 F1 需要它：`_scroll_to_bottom` 的 docstring **故意写明了**老实现用的
    `_set_cursor`/`_mouse_wheel`（讲清为什么改、防后人改回去）—— 直接对整段做
    `has("_set_cursor")` 会被**注释自证**为红（判据太钝）。判"有没有真调用"必须看代码。

    ⛔ 这里踩过一个坑，记下来防后人再踩：`_srcslice.func_src()` 返回的段落**本身带缩进、
    且**第一行就是 `def` 行**（它是某个类里的方法，原文缩进 4 格）。若再"整体缩进 4 格 +
    套一层 `def _f():`"去解析，得到的是**嵌套函数** —— 外层 `_f` 的 `body[0]` 是内层
    `_scroll_to_bottom`（`FunctionDef`），**不是 docstring**，于是剥 docstring 那步空转，
    `unparse` 出来照样带着 `_set_cursor`/`_mouse_wheel` 两个词 ⇒ F1 恒红、纯属误报。
    正解：**把段落当成一个模块直接 `ast.parse`**（带 4 格缩进的 `def` 会触发
    `IndentationError`，先按 `textwrap.dedent` 归一），再在**真正的函数节点**上剥 docstring。
    """
    import ast as _ast
    import textwrap as _tw
    try:
        _mod = _ast.parse(_tw.dedent(seg))
    except Exception:  # noqa: BLE001
        return seg
    _fn = None
    for _n in _ast.walk(_mod):
        if isinstance(_n, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                           _ast.ClassDef)):
            _fn = _n # 段落就是单个函数/类：取那个定义节点
            break
    if _fn is None:
        return _ast.unparse(_mod)
    # 丢掉首句 docstring（要剥的是**函数自己**的，不是外层包装的）
    if (_fn.body and isinstance(_fn.body[0], _ast.Expr)
            and isinstance(getattr(_fn.body[0], "value", None), _ast.Constant)
            and isinstance(_fn.body[0].value.value, str)):
        _fn.body = _fn.body[1:]
    return _ast.unparse(_fn)


_sb_code = _code_only(_sb)
ok("F1 ★ `_scroll_to_bottom` 的**代码里**不再调用裸真鼠标原语（`_set_cursor`/`_mouse_wheel`）",
   _sb and not _sm.has(_sb_code, "_set_cursor") and not _sm.has(_sb_code, "_mouse_wheel"),
   "看 `agent/wechat.py::_scroll_to_bottom` 的代码（docstring 里提到老实现是正常的）")
ok("F2 ★ 它走**投递**版（复用 `_scroll_chat`，发 WM_MOUSEWHEEL，不动光标）",
   _sm.has(_sb, "_scroll_chat"), "应调 `WeChatAdapter._scroll_chat(...)`")
ok("F3 ★ 类名引用正确（写成 `WeChatAgent` 会抛 NameError 被 except 吞成静默滚不动）",
   _sm.has(_sb, "WeChatAdapter._scroll_chat") and not _sm.has(_sb, "WeChatAgent._scroll_chat"),
   "类名是 WeChatAdapter")
ok("F4 改动理由（屏外窗口→坐标被夹到 (0,0)→框选桌面文件）写进了 docstring，防后人改回去",
   _sm.has(_sb, "静默夹到") and _sm.has(_sb, "框选桌面文件"))

print("== G. 同一条事故路径的**第二处**：`_uia_target_row_rect` 的翻页也不许动真鼠标 ==")
# 它 `scroll=True` 时老代码是 `uia._set_cursor(cx,cy)` + 两次 `uia._mouse_wheel(delta)`
#   —— 与 `_scroll_to_bottom` **同一个事故路径**（屏外窗口 → 坐标被夹到 (0,0) → 框选桌面）。
try:
    _ur = _ss.func_src(_WSRC, "_uia_target_row_rect")
except Exception as e:  # noqa: BLE001
    _ur = ""
    print("（取 `_uia_target_row_rect` 失败：%s）" % str(e)[:60])
_ur_code = _code_only(_ur)
ok("G1 ★ `_uia_target_row_rect` 的**代码里**不再调用裸真鼠标原语",
   _ur and not _sm.has(_ur_code, "_set_cursor") and not _sm.has(_ur_code, "_mouse_wheel"),
   "看代码（docstring 里提到老实现是正常的）")
ok("G2 ★ 它的翻页改走**投递**版（`WeChatAdapter._scroll_chat`）",
   _sm.has(_ur, "WeChatAdapter._scroll_chat"), "应调 `_scroll_chat(gui, up=..., ticks=2)`")
ok("G3 事故路径的说明写进了注释（防后人改回裸鼠标）",
   _sm.has(_ur, "框选桌面文件") or _sm.has(_ur, "安全红线"))

print("== 点一下也不许动鼠标判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
sys.exit(1 if FAIL[0] else 0)
