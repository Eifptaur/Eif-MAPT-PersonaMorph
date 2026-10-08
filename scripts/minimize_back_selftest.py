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

# ── B8/B9：**全 agent 反向锚：收窗口一律不许用 `SW_MINIMIZE`(6)** ─────────────────
#   为什么：`SW_MINIMIZE` 的文档语义是**「最小化**并激活 Z 序里的下一个顶层窗**」**。
#   本项目每个独立窗都是主窗的 **owned window** ⇒ Z 序里紧挨着的下一个就是**微信主窗**
#   ⇒ 每收一次独立窗，**用户的主窗就被顶到前台一次**（他什么都没做）。
#   ✅ 只用 `SW_SHOWMINNOACTIVE`(7)：「显示成最小化，**不激活**」（也不激活别人）。
#   ⚠️ 判据按 **AST 调用形式**判，不按文本包含判 —— 注释/docstring 里的历史叙述不算
#     （`wechat.py` 里就有好几处"以前这里 ShowWindow(hwnd, 6)…"的说明，不能当违规）。
import ast as _ast
import glob as _glob


def _minimize_calls(src):
    """源码里"最小化"类的**调用形式** ⇒ `[(行号, 说明)]`（纯函数，注释/docstring 免疫）。"""
    out = []
    try:
        tree = _ast.parse(src)
    except Exception:  # noqa: BLE001
        return out
    for n in _ast.walk(tree):
        if not isinstance(n, _ast.Call):
            continue
        f = n.func
        nm = f.attr if isinstance(f, _ast.Attribute) else (f.id if isinstance(f, _ast.Name) else "")
        if nm in ("ShowWindow", "ShowWindowAsync"):
            if any(isinstance(a, _ast.Constant) and a.value == 6 for a in n.args):
                out.append((n.lineno, "ShowWindow(...,6)=SW_MINIMIZE"))
        elif nm == "SW_MINIMIZE":
            out.append((n.lineno, "SW_MINIMIZE"))
    return out


_BAD_MIN = []
for _p in sorted(_glob.glob(os.path.join(ROOT, "agent", "*.py"))):
    _t = io.open(_p, encoding="utf-8", errors="ignore").read()
    for _ln, _w in _minimize_calls(_t):
        _BAD_MIN.append("%s:%d %s" % (os.path.basename(_p), _ln, _w))
ok("B8 ⛔ **agent/ 里不许出现 `SW_MINIMIZE`(6) 的调用**（它会把独立窗的 owner＝微信主窗顶到前台）",
   not _BAD_MIN, "; ".join(_BAD_MIN[:4]))
_VS = io.open(os.path.join(ROOT, "agent", "voice_strip.py"), encoding="utf-8").read()
_WW = io.open(os.path.join(ROOT, "agent", "wechat_windows.py"), encoding="utf-8").read()
_OFF = _VS.split("class _OffscreenShown")[1].split("def _get_window_placement")[0]
ok("B9 收独立窗的三处都改成「不激活的最小化」"
   "（`park_popup` 用 `7`；`_OffscreenShown._restore` 两处用 `_SW_SHOWMINNOACTIVE`）",
   "ShowWindow(_wt.HWND(int(hwnd)), 7)" in _WW
   and _OFF.count("_SW_SHOWMINNOACTIVE") >= 2, "_OFF×%d" % _OFF.count("_SW_SHOWMINNOACTIVE"))

print("== C. 救援与自愈：窗口不许被留在屏外 ==")
# 现场：最小化档要用"屏外显示"才投得进鼠标消息，收尾若没跑到（进程被杀/异常出口），
#   窗口就停在屏外且 `iconic=False` ⇒ 表现是"窗口不见了、从任务栏点它还往外飞"，只能关掉重开。
_WW_SRC = io.open(os.path.join(ROOT, "agent", "wechat_windows.py"), encoding="utf-8").read()
_VS_SRC = io.open(os.path.join(ROOT, "agent", "voice_strip.py"), encoding="utf-8").read()
# ⛔ 断言只认**代码**：docstring 里会**引用**被禁的写法做反面说明（"-10000 那种哨兵判据不行"），
#    拿整份函数体做子串匹配会把"叙述"当成"违规"（本项目踩过多次）。
import re as _re2
_UN = _re2.sub(r'""".*?"""', "", _srcslice.func_src(_WW_SRC, "unstick_window_to_screen"),
               flags=_re2.S)
ok("C1 ⛔ 救援判据＝「与**虚拟屏**无交集」（`rect_outside_screen`）；不许再用哨兵判据 `-10000` "
   "（实测「屏外显示」给的坐标是 -1119/-1171，**过不了**哨兵判据 ⇒ 工具形同不存在）",
   "rect_outside_screen(" in _UN and "-10000" not in _UN)
ok("C2 ★ 同时修 `rcNormalPosition`（它是「从任务栏还原」的目标；只挪当前 rect ⇒ 用户一点又飞出去）",
   "rcNormalPosition" in _UN and "SetWindowPlacement" in _UN)
ok("C3 收窗口只用 `SW_SHOWMINNOACTIVE`(7)；⛔ 不许出现 `SW_MINIMIZE`"
   "（6 会最小化**并激活** Z 序下一个顶层窗 ⇒ 独立窗的邻居正是主窗 ⇒ 主窗被顶到前台）",
   "SW_SHOWMINNOACTIVE" in _UN and "SW_MINIMIZE" not in _UN)
ok("C4 尺寸取「当前 rect → rcNormalPosition → 600×400」里第一个合理的（不顺手放大用户的窗）",
   ">= 300 and" in _UN and ">= 200" in _UN)
_HEAL = _srcslice.func_src(_WW_SRC, "find_popup")
ok("C5 `find_popup` 里有**自愈**：一发现独立窗在屏外就顺手救回来（不必等用户报故障）",
   "rect_outside_screen(" in _HEAL and "unstick_window_to_screen(" in _HEAL)
ok("C6 `_OffscreenShown` 有**退出兜底**：进程在还原前退出，`atexit` 也会把窗口还回去",
   "_LIVE_BOXES" in _VS_SRC and "atexit" in _VS_SRC and "_restore_live_boxes" in _VS_SRC)
ok("C7 屏外位置按**虚拟屏原点**算（多显示器机器上才不会停到「另一块屏」上 ⇒ 判据①才成立）",
   "_virtual_screen(u)" in _VS_SRC.split("class _OffscreenShown")[1]
   .split("def _get_window_placement")[0])

print("== 放回策略判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
sys.exit(1 if FAIL[0] else 0)
