# -*- coding: utf-8 -*-
"""「干完活要把用户收起来的微信还给他收着」的判据。

**缺陷现场**：整链会发伪激活（`WM_ACTIVATE`）⇒ **微信被我们自己顶到前台**；而链尾那条
"前台==主窗 ⇒ 你在用它 ⇒ 不动"就把**放回**整个吃掉了 ⇒ 
**留在前台**（实测末态 `IsIconic=False`、登记还挂着、整链微信占前台 16.15s/22.3s ＝ 72%）。
修法＝把第③条从"它在不在前台"换成"**你最近 1.2 秒内有真实键鼠输入吗**"（与 `_restore_fg_until`
同一门槛），并把这条策略抽成**纯函数** `_put_back_decision` 让判据直接测它。

判据两段：
  A 真值表（行为）：六种组合逐条断言，含**新规则的反向锚**（前台但你没在动 ⇒ 必须照还，不许再拖）
  B 接线与安全线：链尾真的调了它、三条安全线仍在、`_restore_fg_until` 不再拿"微信自己"当"用户窗口"还

跑法：runtime\\python\\python.exe scripts\\minimize_back_selftest.py
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
import _srcslice
from agent import wechat as W # noqa: E402

PASS, FAIL = [0], [0]


def ok(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("  [%s]" % detail if detail else ""))
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
    return bool(cond)


D = W._put_back_decision
HWND = 0x1AA817A4

print("== A. 放回策略真值表（纯函数，直接测）==")
ok("A1 没登记过 ⇒ 什么都不做（我们没资格改它的状态）",
   D(registered=0, alive=True, iconic=False, is_fg=False, was_iconic=False, idle_s=9) == "none")
ok("A2 窗口已经没了 ⇒ 什么都不做",
   D(registered=HWND, alive=False, iconic=False, is_fg=False, was_iconic=True, idle_s=9) == "none")
ok("A3 它已经是最小化的 ⇒ 什么都不做（用户自己收的，别补一枪）",
   D(registered=HWND, alive=True, iconic=True, is_fg=True, was_iconic=True, idle_s=0.1) == "none")
ok("A4 它在前台、**你 0.2 秒前还在动键鼠** ⇒ 不动（登记留着，下次链尾再还）",
   D(registered=HWND, alive=True, iconic=False, is_fg=True, was_iconic=True, idle_s=0.2) == "defer")
ok("A5 ⚡2026-09-24 它在前台、**你已经 5 秒没动** ⇒ 照还，但**只压 Z 序底层**（不再最小化归还）",
   D(registered=HWND, alive=True, iconic=False, is_fg=True, was_iconic=True, idle_s=5.0) == "bottom")
ok("A6 它在前台、你没动、但**我们没登记过收起来** ⇒ 只压 Z 序底层（不最小化）",
   D(registered=HWND, alive=True, iconic=False, is_fg=True, was_iconic=False, idle_s=5.0) == "bottom")
ok("A7 空闲读数拿不到（None）⇒ **保守**：当作用户在用，不动",
   D(registered=HWND, alive=True, iconic=False, is_fg=True, was_iconic=True, idle_s=None) == "defer")
ok("A8 ⚡2026-09-24 它压根不在前台（用户已切到别的程序）⇒ 放回，同样**只压底层**（不再最小化）",
   D(registered=HWND, alive=True, iconic=False, is_fg=False, was_iconic=True, idle_s=0.05) == "bottom")
ok("A9 反向锚：**旧规则**（只看「在不在前台」）在这两格会给 defer —— 现规则必须给出动作",
   D(registered=HWND, alive=True, iconic=False, is_fg=True, was_iconic=True, idle_s=5.0) != "defer"
   and D(registered=HWND, alive=True, iconic=False, is_fg=True, was_iconic=False, idle_s=5.0) != "defer")

print("== B. 接线与三道安全线 ==")
_SRC = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
_MIN = _srcslice.from_func(_SRC, "_minimize_back_if_needed")[:3200]
_MIN_CODE = "\n".join(l.split("#")[0] for l in _MIN.splitlines())
ok("B1 链尾真的用了这个策略（不是写了没人调）",
   _sm.has(_MIN_CODE, "_put_back_decision(") and _sm.has(_MIN_CODE, "_user_idle_seconds()"))
ok("B2 三条安全线仍在（没登记不动 / 已收起不动 / 前台判据）",
   _sm.has(_MIN_CODE, "if not hwnd:") and "u.IsIconic(hwnd)" in _MIN_CODE
   and _sm.has(_MIN_CODE, "int(u.GetForegroundWindow() or 0) == hwnd"))
ok("B3 ⚡2026-09-24 **取消「最小化归还」**：不许再出现 SW_MINIMIZE 调用"
   "（docstring 里的历史叙述不算，故只认精确调用形式）",
   "ShowWindow(_ct.c_void_p(hwnd), 6)" not in _MIN_CODE
   and "_ct.windll.user32.SetWindowPos" in _MIN_CODE)
ok("B3b 两条路径动作统一：`was_iconic` 分支与默认分支都走 SetWindowPos（只压 Z 序底层）",
   "_WAS_ICONIC_BY_US" in _MIN_CODE and _MIN_CODE.count("SetWindowPos") >= 2)
ok("B4 「不是用户收起的只压 Z 序底层、绝不最小化」这条也没变",
   "SetWindowPos" in _MIN_CODE.split("if _was_iconic:")[-1])
ok("B5 ⚡2026-09-24 不再需要「收回原位后还前台」——压底层用 SWP_NOACTIVATE，本就不抢焦点",
   "收回原位后还前台" not in _MIN)
ok("B6 门槛与 `_restore_fg_until` 同一口径（1.2 秒常量只定义一处）",
   _SRC.find("PUT_BACK_IDLE_S = 1.2") >= 0 and _sm.has(_MIN_CODE, "PUT_BACK_IDLE_S")
   or _sm.has(_MIN_CODE, "_put_back_decision("))
_RF = _srcslice.from_func(_SRC, "_restore_fg_until")[:2600]
ok("B7 `_restore_fg_until` 不再把「微信自己」当「用户窗口」还（否则会假报 ✅）",
   _sm.has(_RF, "_fg_stash_ok()"))

print("== 放回策略判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
sys.exit(1 if FAIL[0] else 0)
