# -*- coding: utf-8 -*-
"""「不抢前台的自动弹窗」判据。

本判据守四条：
  ① **窗口 API 都带 argtypes**（不声明时 HWND 会按 32 位 int 传 ⇒ `SetWindowPos` 报 1400 无效句柄）；
  ② **抬起 ≠ 抢前台**：真拿一个真窗口抬起来，事后 `GetForegroundWindow()` 必须与抬起前**完全一致**；
  ③ **弹不出来要如实报**：没有控制台窗口时返回 ok=False + 原因，绝不抛异常、绝不留假成功；
  ④ **判据不许动用户的屏幕**：`WX_NO_UI_POP=1` 必须能关掉弹窗（我第一次跑自检时真弹了一次，记在案）。

用法：py -3 scripts/notify_ui_selftest.py
"""
import ctypes
import os
import sys

import logging
log = logging.getLogger("persona-morph")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))) # 同目录的 `_srcslice`
sys.path.insert(0, ROOT)
os.chdir(ROOT)

os.environ["WX_NO_UI_POP"] = "1" # 本自检全程不许弹任何窗口（后面还会单独验这条）

from agent import notify_ui as NU # noqa: E402
from agent import version_gate as VG # noqa: E402
from _srcslice import func_src as _fsrc # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


def skip(name, detail=""):
    global SKIP
    SKIP += 1
    print("  SKIP {}{}".format(name, "  [{}]".format(detail) if detail else ""))


SRC = open(os.path.join(ROOT, "agent", "notify_ui.py"), encoding="utf-8").read()
GSRC = open(os.path.join(ROOT, "agent", "version_gate.py"), encoding="utf-8").read()

print("── A. 静态：argtypes 齐 + 只做「看得见不打断」的事 ──")
for fn in ("EnumWindows", "GetForegroundWindow", "SetForegroundWindow", "ShowWindow", "SetWindowPos",
           "FlashWindowEx", "GetWindowThreadProcessId"):
    ok("%s 声明了 argtypes" % fn, ("%s.argtypes" % fn) in SRC)
ok("用的都是不激活的旗标（SW_SHOWNOACTIVATE + SWP_NOACTIVATE）",
   "SW_SHOWNOACTIVATE = 4" in SRC and "SWP_NOACTIVATE" in SRC)
ok("有任务栏闪烁（FlashWindowEx + FLASHW_TIMERNOFG）",
   "FlashWindowEx" in SRC and "FLASHW_TIMERNOFG" in SRC)
ok("不弹系统 MessageBox、不动鼠标键盘",
   all(k not in SRC for k in ("MessageBox", "mouse_event", "SetCursorPos", "SendInput")))
ok("不装包/不改配置", all(k not in SRC for k in ("pip install", "SaveKey", "uninstall")))

print("── A2. 静态：不留 NameError 类暗雷（每个 LOAD_GLOBAL 都能解析）──")
#   `find_console_window()` 里把回调类型写成了 `_WNDUMPROC`，而本模块只定义了 `_WNDENUMPROC`
#   ⇒ `NameError` 被紧邻的 `except Exception` 吃掉 ⇒ **函数永远返回 0**，
#   `open_console()` 的"复用已开的窗口"分支从没生效过。**拼写错误的全局名必须能被静态抓到**。
import builtins # noqa: E402
import dis # noqa: E402


def _code_objs(code):
    yield code
    for c in code.co_consts:
        if hasattr(c, "co_code"):
            for x in _code_objs(c):
                yield x


_bad = []
for _c in _code_objs(compile(SRC, "notify_ui.py", "exec")):
    for _ins in dis.get_instructions(_c):
        if _ins.opname in ("LOAD_GLOBAL", "LOAD_NAME", "STORE_GLOBAL"):
            _n = _ins.argval
            if not hasattr(NU, _n) and not hasattr(builtins, _n):
                _bad.append("%s:%s" % (_c.co_name, _n))
ok("所有全局名都可解析（拼错名字会在这里变红）", not _bad, "、".join(sorted(set(_bad)))[:120] or "全部可解析")

print("── B. 真跑：抬起一个真窗口，前台必须原样还回去 ──")
fg0 = NU.foreground()
ok("拿得到前台窗口（有桌面会话）", fg0 > 0, "hwnd=%s" % fg0)
try:
    hw = NU.find_console_window()
    ok("找控制台窗口不抛异常", isinstance(hw, int), "hwnd=%s" % hw)
except Exception as e:
    ok("找控制台窗口不抛异常", False, "%s: %s" % (type(e).__name__, e))

# B2. **真的找得到**：用一手 ctypes 枚举独立数一遍「群相 控制台」窗口，
#     函数的返回值必须落在这一堆里（否则就是"永远返回 0"那类静默失效）。
_live = []
_cb_t = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def _enum_cb(h, _l):
    try:
        if ctypes.windll.user32.IsWindowVisible(h):
            _n = int(ctypes.windll.user32.GetWindowTextLengthW(h))
            if _n:
                _b = ctypes.create_unicode_buffer(_n + 2)
                ctypes.windll.user32.GetWindowTextW(h, _b, _n + 2)
                if "控制台" in _b.value and "群相" in _b.value:
                    _live.append(int(h))
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    return True


ctypes.windll.user32.EnumWindows(_cb_t(_enum_cb), 0)
if _live:
    ok("开着的控制台窗口能被找到（不是永远返回 0）", int(hw) in _live,
       "找到=%s 在开着的=%s" % (hw, _live))
else:
    skip("开着的控制台窗口能被找到", "本机当前没有开着的控制台窗口")

# ⚠️ `raise_without_stealing(0)` 的语义是「**自己去找**控制台窗口」（`hwnd or find(...)`），
#   不是"没有窗口"。这条判据原先是**靠 bug 才绿的**（当时 find 永远返回 0）。要测"没有窗口"必须显式钉住它。
gw = int(ctypes.windll.kernel32.GetConsoleWindow() or 0) # 自检进程自己的控制台窗（真窗口）
_keep_find = NU.find_console_window
try:
    NU.find_console_window = lambda: 0
    r0 = NU.raise_without_stealing(0)
finally:
    NU.find_console_window = _keep_find
ok("没有窗口时如实报失败（ok=False + 原因）",
   r0.get("ok") is False and bool(r0.get("why")), str(r0.get("why"))[:46])
ok("失败路径也没有改前台", NU.foreground() == fg0)
if gw:
    # `hwnd=0` ＝「自己去找控制台窗口」，找得到就必须用找到的那个（不许当成"没窗口"）
    try:
        NU.find_console_window = lambda: gw
        r_auto = NU.raise_without_stealing(0)
    finally:
        NU.find_console_window = _keep_find
    ok("传 0 的语义＝自己去找控制台窗口（找到就用它）", r_auto.get("hwnd") == gw, "hwnd=%s" % r_auto.get("hwnd"))

gw = int(ctypes.windll.kernel32.GetConsoleWindow() or 0) # 自检进程自己的控制台窗（真窗口）
if gw:
    before = NU.foreground()
    r = NU.raise_without_stealing(gw)
    after = NU.foreground()
    ok("抬起真窗口成功", bool(r.get("ok")), str(r.get("why"))[:46])
    ok("确实抬起来了", bool(r.get("raised")))
    ok("**前台原样还回去**（抬起的下一秒就还）", r.get("restored") is True, "restored=%s" % r.get("restored"))
    ok("抬起前后前台窗口完全一致（不抢前台）", before == after, "%s → %s" % (before, after))
    ok("闪烁任务栏被调用且不报错", NU.flash(gw) in (True, False))
else:
    skip("抬起真窗口", "本会话没有控制台窗口（无桌面/无控制台）")

print("── B3. 窗口判据：只有「群相 控制台」才算控制台──")
# ⛔ 起因：点一下「一键启动」没打开控制台、**还得再点一下**。
#   根因＝旧判据按**标题子串**认窗（"群相" / "控制台" / 裸 "一键启动"），而启动器自己的窗口就叫
#   「群相 一键启动」「群相 启动完成」，且**控制台窗与启动器窗是同一个 exe**（一键启动.exe）
#   ⇒ 机器人侧判"已经开着 ⇒ 复用"（不开）、2 秒后启动器侧判"机器人侧已打开"（也不开）⇒ **两边都不开**。
#   下面这几条样本＋两扇真窗，就是钉住这个回归的判据。窗一律建在**屏幕外**（不打扰用户）。
_CASES = [
    ("群相 控制台", "一键启动.exe", True),
    ("群相 控制台 - Google Chrome", "chrome.exe", True), # 回退浏览器打开时是标签页
    ("群相 一键启动", "一键启动.exe", False),
    ("群相 启动完成", "一键启动.exe", False),
    ("群相 正在启动", "一键启动.exe", False),
    ("群相 已就绪", "一键启动.exe", False),
    ("接手群相拍一拍落点修复 — DeepSeek Harness - Google Chrome", "chrome.exe", False), # 只是标题里有"群相"
    ("命令提示符", "cmd.exe", False),
    ("", "一键启动.exe", False),
]
_bad = [("%s/%s→%s(期望%s)" % (_t[:14], _e, _g, "算" if _w else "不算"))
        for _t, _e, _w in _CASES for _g in [NU.classify_console_window(_t, _e)] if bool(_g) != _w]
ok("9 个标题样本判对", not _bad, "；".join(_bad) if _bad else "全对")
ok("自家 exe 的控制台窗排在同名浏览器页之前（EXE_HINTS 只做排序加分、不再能单独成立）",
   NU.classify_console_window("群相 控制台", "一键启动.exe") > NU.classify_console_window("群相 控制台", "chrome.exe"))
ok("裸「群相」不再能单独认成控制台（浏览器标签页/文档窗会撞）",
   NU.classify_console_window("群相 — 开发笔记", "notepad.exe") == 0)

_u2 = ctypes.windll.user32
_u2.CreateWindowExW.restype = ctypes.c_void_p
_u2.CreateWindowExW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
_u2.DestroyWindow.argtypes = [ctypes.c_void_p]
_WS_VISIBLE = 0x10000000


def _fake_window(title):
    """屏幕外建一扇真窗（WS_VISIBLE ⇒ IsWindowVisible 为真、EnumWindows 能枚举，但看不见）。"""
    return int(_u2.CreateWindowExW(0, "STATIC", title, _WS_VISIBLE, -4000, -4000, 320, 140, 0, 0, 0, 0) or 0)


_h_lau = _fake_window("群相 一键启动")
try:
    _g1 = int(NU.find_console_window() or 0)
    ok("真建出来的「群相 一键启动」窗不会被认成控制台", _g1 != _h_lau,
       "find=%s 假启动器窗=%s" % (_g1, _h_lau))
finally:
    if _h_lau:
        _u2.DestroyWindow(ctypes.c_void_p(_h_lau))

_h_con = _fake_window("群相 控制台")
try:
    if _live:
        # 屏幕上本来就有真控制台（自家 exe，分更高）⇒ 不跟它抢，只钉判据本身
        ok("离屏的「群相 控制台」窗被判成控制台（判据本身没被收得过紧）",
           NU.classify_console_window("群相 控制台", "python.exe") >= 4)
    else:
        _g2 = int(NU.find_console_window() or 0)
        ok("离屏建出的「群相 控制台」窗能被 find 认出来（正向）", _g2 == _h_con,
           "find=%s 假控制台窗=%s" % (_g2, _h_con))
finally:
    if _h_con:
        _u2.DestroyWindow(ctypes.c_void_p(_h_con))

print("── C. 弹窗流程：能弹/不重复弹/弹不出来也要如实报 ──")
# ⚠️ 这一段的"抬起"分支**必须用真窗口**：`raise_without_stealing` 会用 `IsWindow` 验句柄，
#    拿假句柄（4242）会被它挡回来（这正是它该做的事）⇒ 用自检进程自己的控制台窗当"控制台窗口"。
_real_find, _real_fg, _real_open = NU.find_console_window, NU.foreground, NU.open_console
try:
    NU.find_console_window = lambda: 4242
    NU.foreground = lambda: 4242 # 控制台已经在前台
    r1 = NU.pop_decision_ui(wait_s=0.1)
    ok("控制台已在前台 ⇒ 不再弹（skip-visible）", r1.get("action") == "skip-visible", str(r1.get("action")))

    if gw:
        NU.find_console_window = lambda: gw
        NU.foreground = lambda: 999 # 用户在别处 ⇒ 要弹
        r2 = NU.pop_decision_ui(wait_s=0.1)
        # ⚠️ 只锚"试过闪烁"（确定），不锚 FlashWindowEx 的回值：窗口已经在闪 / 被系统接管时它给 FALSE
        ok("用户在别处 ⇒ 走抬起+闪烁", r2.get("action") == "raised" and r2.get("flash_tried") is True,
           "%s / flash_tried=%s / 回值=%s" % (r2.get("action"), r2.get("flash_tried"), r2.get("flashed")))

        calls = {"open": 0}
        _state = {"hwnd": 0}

        def _fake_open(url=""):
            calls["open"] += 1
            _state["hwnd"] = gw # 开完之后窗口就有了
            return {"ok": True, "how": "假窗口"}

        NU.open_console = _fake_open
        NU.find_console_window = lambda: _state["hwnd"]
        r3 = NU.pop_decision_ui(wait_s=1.0)
        ok("没有窗口 ⇒ 自己开一个再抬起（opened+raised）",
           r3.get("action") == "opened+raised" and calls["open"] == 1 and bool(r3.get("ok")),
           "%s / open=%d / ok=%s" % (r3.get("action"), calls["open"], r3.get("ok")))
    else:
        skip("抬起/自动开窗两条分支", "本会话没有真窗口可用")

    NU.open_console = lambda url="": {"ok": False, "why": "假失败：开不出来"}
    NU.find_console_window = lambda: 0
    r4 = NU.pop_decision_ui(wait_s=0.2)
    ok("开不出来 ⇒ ok=False + 原因（不留假成功）",
       r4.get("ok") is False and bool(r4.get("why")), str(r4.get("why"))[:40])
    ok("整个流程不抛异常（返回报告）", isinstance(r4, dict))
finally:
    NU.find_console_window, NU.foreground, NU.open_console = _real_find, _real_fg, _real_open

print("── D. 版本门弹窗：**已整体停用**（能跨版本运行 ⇒ 弹窗是纯风险源）──")
# 背景：用户决策 —— "**如果能跨版本运行，这个弹窗直接删掉**"。
#   `check()` 四条返回 allow **全为 True** ⇒ 不点单子照常发 ⇒ 弹窗不拦发送，
#   而现场反馈"上面的选项都有问题、点了程序没法正常跑" ⇒ 弹窗＝纯风险源。
ok("★ 反向锚：`_pending_enabled` 还在（被删掉 ⇒ 本行必红）",
   "def _pending_enabled()" in GSRC)
# ⚠ 取函数体一律用 **AST 锚定的 `func_src`**，不要用「按 def 行做文本切片」的写法 ——
#   文本切片会在"docstring/注释里提到同名函数"时命中注释（本文件 T21 那次就踩过：
#   `pending()` 的 docstring 里写了 `_pending_enabled()` ⇒ 首个匹配在注释上 ⇒ 判据假红）。
_pen_src = _fsrc(GSRC, "_pending_enabled")          # ⟵ 已含 docstring
_pen_code = _pen_src.split('"""', 2)[-1] if '"""' in _pen_src else _pen_src
ok("★ 默认是**停用**（函数体里 `return False`，不是 True）",
   "return False" in _pen_code)
_pnd_src = _fsrc(GSRC, "pending")
ok("pending() 里真的调了它（不是写了没人用）", "_pending_enabled()" in _pnd_src)
_pbody = _pnd_src
# ⚠ 必须**剥掉 docstring**再比：docstring 里也写着 `_pending_enabled()`，
#   不剥的话首个匹配命中注释，判据会**假红**（本行第一次就踩了这个坑）。
# ⚠ 找不到时必须**记 FAIL**，不许抛 ValueError —— 判据崩掉不进计数，
#   在回归里会被当成"没跑到"，等于把红灯藏起来（反向锚第 2 次就踩了这个坑）。
_pcode = _pbody.split('"""', 2)[-1]


def _after_check(txt):
    try:
        return txt.index("_pending_enabled()") > txt.index("st = check(capability")
    except ValueError:
        return False


ok("停用是**在 check() 之后**才返回的 ⇒ 告警/台账不受影响", _after_check(_pcode))
# 旧行为（弹窗在后台线程跑 / WX_NO_UI_POP 能关）的静态判据保留：代码不能删，
# 因为 `_pop_ui` 仍可能被「配置恢复」「手动打开」调用。
ok("弹窗代码仍在（恢复开关一开就能用）：后台线程跑", "threading.Thread(target=_run, daemon=True" in GSRC)
ok("弹窗代码仍在：失败只写日志、不影响开单", "待决单弹窗失败（不影响开单）" in GSRC)
ok("弹窗代码仍在：WX_NO_UI_POP=1 仍能关掉", 'os.environ.get("WX_NO_UI_POP") == "1"' in GSRC)
import tempfile # noqa: E402
from agent import pending_decisions as PD # noqa: E402
import logging

_tmp = tempfile.mkdtemp(prefix="nu-judge-")
try:
    PD.DECISIONS_PATH = os.path.join(_tmp, "pd.json")
    r5 = VG.pending(wechat="9.9.9", adapter="1.1.1") # 未实测版本 × 未实测版本
    ok("未实测版本 ⇒ **不开单、不弹窗**（created=False）", r5.get("created") is False, str(r5.get("created")))
    ok("未实测版本 ⇒ **既不弹窗也不报错**（没有 pop / error 字段）",
       "pop" not in r5 and "error" not in r5, "pop=%r error=%r" % (r5.get("pop"), r5.get("error")))
    ok("停用时如实说明原因（可追溯，不是静默）",
       "停用" in str(r5.get("disabled") or ""), str(r5.get("disabled"))[:48])
    ok("★ 最要紧的一条：**停用后 check() 仍 allow=True ⇒ 发送不受影响**",
       bool((r5.get("status") or {}).get("allow")) is True,
       "level=%s allow=%s" % ((r5.get("status") or {}).get("level"), (r5.get("status") or {}).get("allow")))
    r6 = VG.pending(wechat="9.9.9", adapter="1.1.1") # 第二遍：结果必须与第一遍一致
    ok("同一对版本第二遍仍不开单、行为稳定",
       r6.get("created") is False and bool(r6.get("disabled")), str(r6.get("disabled"))[:48])
    import json as _json # noqa: E402
    _empty = (not os.path.exists(PD.DECISIONS_PATH)) or (_json.load(open(PD.DECISIONS_PATH, encoding="utf-8")) in ({}, []))
    ok("★ 停用后**台账里一张单子都没写**（真没开单，不是开了不弹）", _empty)
finally:
    import shutil # noqa: E402
    shutil.rmtree(_tmp, ignore_errors=True)

print("\n通过 %d / 失败 %d（跳过 %d）" % (PASS, FAIL, SKIP))
sys.exit(1 if FAIL else 0)
