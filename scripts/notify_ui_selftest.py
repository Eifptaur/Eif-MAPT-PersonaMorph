# -*- coding: utf-8 -*-
"""「不抢前台的自动弹窗」判据（2026-09-14，测机报告 ⑦ 第三批：前台纪律 + 托盘兜底）。

用户口径原话：「**把弹窗切出来的那一秒，就应该立刻让它到后台**」＋「persona morph 不能自己把弹窗切出来吗」。
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
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

os.environ["WX_NO_UI_POP"] = "1"          # 本自检全程不许弹任何窗口（后面还会单独验这条）

from agent import notify_ui as NU         # noqa: E402
from agent import version_gate as VG      # noqa: E402

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
# ⚠️ 2026-09-17 立这条判据的起因（真事故，用户报「每重启一次就多一个控制台」）：
#   `find_console_window()` 里把回调类型写成了 `_WNDUMPROC`，而本模块只定义了 `_WNDENUMPROC`
#   ⇒ `NameError` 被紧邻的 `except Exception` 吃掉 ⇒ **函数永远返回 0**，
#   `open_console()` 的"复用已开的窗口"分支从没生效过。**拼写错误的全局名必须能被静态抓到**。
import builtins                                             # noqa: E402
import dis                                                  # noqa: E402


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
    except Exception:
        pass
    return True


ctypes.windll.user32.EnumWindows(_cb_t(_enum_cb), 0)
if _live:
    ok("开着的控制台窗口能被找到（不是永远返回 0）", int(hw) in _live,
       "找到=%s 在开着的=%s" % (hw, _live))
else:
    skip("开着的控制台窗口能被找到", "本机当前没有开着的控制台窗口")

# ⚠️ 2026-09-17 更正：`raise_without_stealing(0)` 的语义是「**自己去找**控制台窗口」（`hwnd or find(...)`），
#   不是"没有窗口"。这条判据原先是**靠 bug 才绿的**（当时 find 永远返回 0）。要测"没有窗口"必须显式钉住它。
gw = int(ctypes.windll.kernel32.GetConsoleWindow() or 0)      # 自检进程自己的控制台窗（真窗口）
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

gw = int(ctypes.windll.kernel32.GetConsoleWindow() or 0)      # 自检进程自己的控制台窗（真窗口）
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

print("── C. 弹窗流程：能弹/不重复弹/弹不出来也要如实报 ──")
# ⚠️ 这一段的"抬起"分支**必须用真窗口**：`raise_without_stealing` 会用 `IsWindow` 验句柄，
#    拿假句柄（4242）会被它挡回来（这正是它该做的事）⇒ 用自检进程自己的控制台窗当"控制台窗口"。
_real_find, _real_fg, _real_open = NU.find_console_window, NU.foreground, NU.open_console
try:
    NU.find_console_window = lambda: 4242
    NU.foreground = lambda: 4242                              # 控制台已经在前台
    r1 = NU.pop_decision_ui(wait_s=0.1)
    ok("控制台已在前台 ⇒ 不再弹（skip-visible）", r1.get("action") == "skip-visible", str(r1.get("action")))

    if gw:
        NU.find_console_window = lambda: gw
        NU.foreground = lambda: 999                           # 用户在别处 ⇒ 要弹
        r2 = NU.pop_decision_ui(wait_s=0.1)
        # ⚠️ 只锚"试过闪烁"（确定），不锚 FlashWindowEx 的回值：窗口已经在闪 / 被系统接管时它给 FALSE
        ok("用户在别处 ⇒ 走抬起+闪烁", r2.get("action") == "raised" and r2.get("flash_tried") is True,
           "%s / flash_tried=%s / 回值=%s" % (r2.get("action"), r2.get("flash_tried"), r2.get("flashed")))

        calls = {"open": 0}
        _state = {"hwnd": 0}

        def _fake_open(url=""):
            calls["open"] += 1
            _state["hwnd"] = gw                               # 开完之后窗口就有了
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

print("── D. 接线：新开单才弹 + 判据/无人值守能关掉 ──")
ok("弹窗在后台线程里跑（不挡住 /api/status）",
   "threading.Thread(target=_run, daemon=True" in GSRC)
ok("失败只写日志、不影响开单", "待决单弹窗失败（不影响开单）" in GSRC)
ok("WX_NO_UI_POP=1 能关掉弹窗", 'os.environ.get("WX_NO_UI_POP") == "1"' in GSRC)
import tempfile                                            # noqa: E402
from agent import pending_decisions as PD                  # noqa: E402
_tmp = tempfile.mkdtemp(prefix="nu-judge-")
try:
    PD.DECISIONS_PATH = os.path.join(_tmp, "pd.json")
    r5 = VG.pending(wechat="9.9.9", adapter="1.1.1")       # 新开单（弹窗被 WX_NO_UI_POP 关掉）
    ok("新开单时报「已按 WX_NO_UI_POP 关掉弹窗」",
       isinstance(r5.get("pop"), dict) and "WX_NO_UI_POP" in str(r5["pop"].get("skipped") or ""), str(r5.get("pop")))
    r6 = VG.pending(wechat="9.9.9", adapter="1.1.1")       # 第二遍：同一对版本不再开单、也不弹
    ok("同一对版本第二次不再弹", r6.get("created") is False and bool((r6.get("pop") or {}).get("skipped")),
       str(r6.get("pop")))
finally:
    import shutil                                          # noqa: E402
    shutil.rmtree(_tmp, ignore_errors=True)

print("\n通过 %d / 失败 %d（跳过 %d）" % (PASS, FAIL, SKIP))
sys.exit(1 if FAIL else 0)
