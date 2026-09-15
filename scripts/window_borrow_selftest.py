# -*- coding: utf-8 -*-
"""窗口「借用 → 归还」判据（2026-09-15，用户拍板方案 A 后的验收标准）。

背景：`wechat._limit_wechat_window()` 为了不让驱动库的布局校准失效，每次取 GUI 都把微信主窗钉到
1160×900，把用户手动拉过的尺寸改掉；用户问「不是说要限位吗，为什么我的窗口还是被改了」⇒
拍板方案 A＝**用完还原**。这条判据守方案 A 的六条规矩（**全部用替身，不碰真窗口、不动真微信**）：

  A. 默认开、可关（`ui.restore_window_after_use=False` ⇒ 借了不还，回旧行为）。
  B. 借之前记下原 rect；同一次借用期间**不覆盖**（用户中途改过也不覆盖）。
  C. 归还**只撤销我们自己那一版**：当前 rect ≠ 我们钉的那版 ⇒ 不还（交回用户）。
  D. 归还用 `SWP_NOZORDER|SWP_NOACTIVATE`（不动光标、不抢前台、不改 Z 序）。
  E. 窗口没了 ⇒ 静默清状态，不调用 SetWindowPos。
  F. 空闲到点由看门线程自动归还；活动信号 `touch()` 能把它推后。

用法：`py -3 scripts/window_borrow_selftest.py`（不联网、不碰真窗口）
"""
import ctypes
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import window_borrow as WB        # noqa: E402
from agent import config as CFG              # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✔ %s" % name)
    else:
        FAIL += 1
        print("  ✘ %s %s" % (name, ("— " + str(detail)) if detail else ""))


class FakeU32:
    """user32 替身：记 rect、记 SetWindowPos 调用。"""

    def __init__(self, rect=(100, 50, 1260, 950)):
        self.rect = rect
        self.calls = []
        self.alive = True

    def GetWindowRect(self, hwnd, ref):
        if not self.alive:
            return 0
        arr = ctypes.cast(ref, ctypes.POINTER(ctypes.c_long * 4)).contents
        arr[0], arr[1], arr[2], arr[3] = self.rect
        return 1

    def IsWindow(self, hwnd):
        return 1 if self.alive else 0

    def SetWindowPos(self, hwnd, after, x, y, w, h, flags):
        # ⚠️ hwnd 是 `ctypes.c_void_p`（模块按 HWND 语义传的）⇒ 取 `.value` 再转 int；
        #    直接 `int(c_void_p)` 会抛 "invalid literal for int() with base 10: b'…'"
        _hw = getattr(hwnd, "value", hwnd) or 0
        self.calls.append((int(_hw), int(x), int(y), int(w), int(h), int(flags)))
        self.rect = (int(x), int(y), int(x + w), int(y + h))
        return 1


def fresh(rect=(100, 50, 1260, 950)):
    f = FakeU32(rect)
    WB._test_api = f
    with WB._lock:
        WB._state.update({"borrowed": False, "hwnd": 0, "rect": None, "forced": None,
                          "at": 0.0, "last_touch": 0.0})
    return f


print("\n[一] 开关与借用记账（A/B）")
_orig_cfg = CFG.get_config
ok("默认开（配置里没有这个键时按开处理）", WB.enabled() is True)
ok("空闲阈值是个正整数秒（落在 10~300 的合理区间）", 10 <= WB.IDLE_S <= 300, "IDLE_S=%s" % WB.IDLE_S)

f = fresh()
ok("note_original() 记下原 rect", WB.note_original(777) is True)
snap = WB.snapshot()
ok("snapshot 里 borrowed=True 且 rect 是原值", snap["borrowed"] and snap["rect"] == (100, 50, 1260, 950),
   str(snap.get("rect")))
ok("同一次借用里再 note 不覆盖（返回 False）", WB.note_original(777) is False)
f.rect = (200, 60, 1360, 960)          # 模拟"被我们钉成了别的尺寸"
ok("用户/我们中途改了 rect，也不覆盖原始记账", WB.note_original(777) is False
   and WB.snapshot()["rect"] == (100, 50, 1260, 950))
ok("note_forced() 记下我们钉的那一版", (WB.note_forced((200, 60, 1360, 960)) or True)
   and WB.snapshot()["forced"] == (200, 60, 1360, 960))

print("\n[二] 归还的规矩（C/D/E）")
f = fresh()
WB.note_original(777)
WB.note_forced((200, 60, 1360, 960))
f.rect = (200, 60, 1360, 960)          # 当前正是我们钉的那版 ⇒ 应该还
ok("当前 rect ＝ 我们钉的那版 ⇒ 归还", WB.restore("t1") is True and len(f.calls) == 1,
   "calls=%s snap=%s" % (f.calls, WB.snapshot()))
ok("归还目标＝原始 rect", f.calls[0][1:5] == (100, 50, 1160, 900), str(f.calls[0]))
ok("归还带 SWP_NOZORDER|SWP_NOACTIVATE（不动光标、不抢前台）",
   (f.calls[0][5] & 0x0004) and (f.calls[0][5] & 0x0010), "flags=0x%X" % f.calls[0][5])
ok("归还后状态清空", WB.snapshot()["borrowed"] is False)

f = fresh()
WB.note_original(777)
WB.note_forced((200, 60, 1360, 960))
f.rect = (0, 0, 800, 600)              # 用户在借用期间自己动过窗口
WB.restore("t2")
ok("用户中途自己改过窗口 ⇒ **不还**（不调用 SetWindowPos）", len(f.calls) == 0, str(f.calls))
ok("不还也要清状态（不留悬挂借用）", WB.snapshot()["borrowed"] is False)

f = fresh()
WB.note_original(777)
f.alive = False                        # 窗口没了
WB.restore("t3")
ok("窗口已经没了 ⇒ 静默清状态、不调用 SetWindowPos", len(f.calls) == 0 and WB.snapshot()["borrowed"] is False)

ok("没借过时 restore() 直接返回 False", WB.restore("t4") is False)

print("\n[三] 空闲看门线程（F）")
f = fresh()
_old_idle = WB.IDLE_S
WB.IDLE_S = 0.3
WB.note_original(777)
WB.note_forced((200, 60, 1360, 960))
f.rect = (200, 60, 1360, 960)
WB.touch()
time.sleep(0.15)
ok("touch() 会把归还时间往后推（还没到点）", len(f.calls) == 0, "calls=%s" % f.calls)
deadline = time.time() + 5
while time.time() < deadline and not f.calls:
    time.sleep(0.1)
ok("空闲到点后看门线程自动归还（不用人叫）", len(f.calls) == 1, "calls=%s" % f.calls)
ok("看门线程是 daemon（不会拖住进程退出）",
   any(t.name == "pm-window-borrow" and t.daemon for t in __import__("threading").enumerate()))
WB.IDLE_S = _old_idle

print("\n[四] 可关（A 的另一半）")
f = fresh()
CFG.get_config = lambda: {"ui": {"restore_window_after_use": False}}
ok("关掉之后 enabled() 为 False", WB.enabled() is False)
ok("关掉之后 note_original() 不记账、返回 False", WB.note_original(777) is False
   and WB.snapshot()["borrowed"] is False)
ok("关掉之后 restore() 什么都不做", WB.restore("t5") is False and len(f.calls) == 0)
CFG.get_config = _orig_cfg

print("\n[五] 接线（机械看守：谁该调它、谁不该绕过它）")
_wx = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
_seg = _wx[_wx.index("def _limit_wechat_window"):]
_seg = _seg[:_seg.index("def _install_ui_patches")]
ok("wechat._limit_wechat_window 改窗口前会 note_original", "note_original(" in _seg)
ok("wechat._limit_wechat_window 改完会 note_forced（归还时用来判断是不是我们钉的）",
   "note_forced(" in _seg)
ok("wechat._limit_wechat_window 每次都会 touch（活动信号）", "touch()" in _seg)
_ib = open(os.path.join(ROOT, "agent", "input_backend.py"), encoding="utf-8").read()
ok("input_backend.select_backend 会 touch（每次取后端＝一次输入动作）",
   "window_borrow" in _ib and "touch()" in _ib)
ok("input_backend.active 也 touch（命中缓存的后端也要算活动）",
   _ib.count("_wb.touch()") >= 2, "命中 %d 处" % _ib.count("_wb.touch()"))
_cfg = open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read()
ok("config 默认值里有 restore_window_after_use 且默认 True",
   '"restore_window_after_use": True' in _cfg)
_cn = open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
ok("控制台有这个开关的 UI（不许只存在于 config）",
   "ui.restore_window_after_use" in _cn)
_ex = open(os.path.join(ROOT, "config.example.json"), encoding="utf-8").read()
ok("config.example.json 同步了这个键（示例与默认必须同键）",
   '"restore_window_after_use"' in _ex)

WB._test_api = None
print("\n%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
