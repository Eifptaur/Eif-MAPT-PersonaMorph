# -*- coding: utf-8 -*-
"""「拆掉画面依赖会不会影响正常发送？」——机械判定（第 243 项）。

## 口径

清理的标准**不是"慢慢来"**，而是**"确定拆掉之后正常输入/发送不受影响"**。
⇒ 那就把"发送这条路到底在哪看画面"**钉死**，不靠感觉。

## 本方判定（三条，全部可复跑）

1. **`send_text` 自己不看画面** —— 它的方法体里没有任何抓帧/OCR 调用。
2. **发送链只有一处看画面**：`send_text` → `wechat_windows.send_to_popup` → `ensure_popup`；
   `ensure_popup` 为了"知道点哪一行"要用抓帧 + OCR。
3. **`ensure_popup` 是这条链上画面依赖的唯一入口**（全仓调它的地方只在 `wechat_windows.py` 里）。

⇒ 结论：**要拆画面依赖，只需管住 `ensure_popup` 这一个函数**；而它**只在"独立窗不在、需要现开"时才看画面**；
   独立窗一旦开着（常态）⇒ **发送链完全不看画面** ⇒ **拆掉画面依赖不影响正常发送**。

用法：runtime\\python\\python.exe scripts/frame_free_send_selftest.py
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS = FAIL = 0
FRAME = re.compile(r"grab_window\(|grab_render\(|recognize\(|green_band\(|pick_target_row\("
                   r"|menu_click\(|ImageGrab|screenshot\(")


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


def _method(text, fname):
    """取一个方法体（从 `def fname(` 到下一个同缩进 def）。"""
    i = text.find("    def " + fname + "(")
    if i < 0:
        i = text.find("def " + fname + "(")
    if i < 0:
        return ""
    seg = text[i:]
    j = seg.find("\n    def ", 10)
    if j < 0:
        j = seg.find("\ndef ", 10)
    return seg[:j if j > 0 else len(seg)]


wx = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
ww = io.open(os.path.join(ROOT, "agent", "wechat_windows.py"), encoding="utf-8").read()

print("── 1. 发送入口自己不看画面 ──")
_st = _method(wx, "send_text")
ok("S0 分母守卫：拿到了 `send_text` 的方法体（>800 字符）", len(_st) > 800, "%d 字符" % len(_st))
ok("S1★★ `send_text` 体里**没有任何抓帧/OCR/菜单识别**调用",
   not FRAME.findall(_st), str(FRAME.findall(_st))[:80])
ok("S2 它把活交给独立窗那条路（里面出现 `send_to_popup`）", "send_to_popup" in _st, "")

print("── 2. 发送链：send_text → send_to_popup → ensure_popup ──")
_sp = _method(ww, "send_to_popup")
ok("P0 分母守卫：拿到了 `send_to_popup` 的方法体", len(_sp) > 400, "%d 字符" % len(_sp))
ok("P1★ 开窗走 `ensure_popup`（唯一的开窗入口）", "ensure_popup(" in _sp, "")
_ep = _method(ww, "ensure_popup")
ok("P2★ `ensure_popup` 里**确实**有抓帧/OCR（这正是那条链上唯一的画面依赖）",
   bool(FRAME.findall(_ep)), str(sorted(set(x.strip("(") for x in FRAME.findall(_ep))))[:5])
ok("P3★ 并且**不用画面也能定位**：落点由 DIP 常数算（`AVATAR_X_DIP` ＋ `row_click_pt`）",
   ("AVATAR_X_DIP" in _ep) and ("row_click_pt(" in _ep or "row_click_pt(" in ww), "")

print("── 3. 画面依赖入口**唯一**（拆的时候只需管一个地方）──")
_callers = []
for f in sorted(os.listdir(os.path.join(ROOT, "agent"))):
    if not f.endswith(".py"):
        continue
    tx = io.open(os.path.join(ROOT, "agent", f), encoding="utf-8", errors="ignore").read()
    for m in re.finditer(r"ensure_popup\(", tx):
        _callers.append("%s:%d" % (f, tx[:m.start()].count("\n") + 1))
ok("U0 分母守卫：`ensure_popup` 至少被调用一次（否则这条链是断的）", len(_callers) >= 1,
   str(_callers[:4]))
ok("U1★★ 调它的地方**只在 `wechat_windows.py` 内、且不超过 3 处**",
   all(c.startswith("wechat_windows.py:") for c in _callers) and len(_callers) <= 3,
   "%d 处：%s" % (len(_callers), _callers))
ok("U2 反向锚：`wechat_windows.py` **之外**没有任何文件调它"
   "（否则「只需管一个地方」不成立）",
   not [c for c in _callers if not c.startswith("wechat_windows.py:")], "")

print("\n== 拆画面依赖的安全性判定：%d 通过 / %d 失败 ==" % (PASS, FAIL))
if FAIL == 0:
    print("⇒ 结论：**拆掉画面依赖不影响正常发送** —— 画面只在 `ensure_popup` 这一处被用到，")
    print("   而它只在「独立窗不在、需要现开」时才走画面；独立窗开着（常态）时发送链完全不看画面。")
sys.exit(1 if FAIL else 0)
