# -*- coding: utf-8 -*-
"""`agent/session_order.py::pinned_state` 的判据 —— **不碰微信、不读真库**。

守的东西：**"置顶"这个结论必须带依据等级**。
`sort_timestamp > last_timestamp` 是**正面证据**；而"曾观察到置顶"的名单只能证明**曾经**，
当前 `sort == last` 时分不出"置顶后又来了消息"与"已取消置顶" ⇒ 那种 True 是**可能假阳性**的，
需要确定性的消费方必须看 `how`，不能只看 `pinned`。

用法：runtime\\python\\python.exe scripts/session_order_selftest.py
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 同目录的 `_srcmatch`
import _srcmatch as _sm # noqa: E402 空白容忍的源码断言
import _srcslice  # noqa: E402  AST 锚点取函数正文（别用文本位置切）

from agent import session_order as SO  # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


SRC = io.open(os.path.join(ROOT, "agent", "session_order.py"), encoding="utf-8").read()

print("── A. 四态判定（真调 pinned_state）──")
ok("A1 正面证据：sort > last ⇒ (True, sort_gt_last)",
   SO.pinned_state(1791118137, 1791217611, False) == (True, "sort_gt_last"))
ok("A2 ★名单里但 sort == last ⇒ (True, **seen_only**) —— 不许报成 sort_gt_last",
   SO.pinned_state(1791220042, 1791220042, True) == (True, "seen_only"))
ok("A3 都不满足 ⇒ (False, no_evidence)", SO.pinned_state(100, 100, False) == (False, "no_evidence"))
ok("A4 表里没这列 ⇒ (None, **no_column**)（测不了 ≠ 没置顶）",
   SO.pinned_state(100, 100, True, has_last=False) == (None, "no_column"))
ok("A5 名单里的会话**缺列**时也必须报 no_column（不许拿名单顶）",
   SO.pinned_state(0, 0, True, has_last=False)[1] == "no_column")
ok("A6 last 为 0/缺失 ⇒ 不算正面证据（`0 > x` 恒假，别把 0 当时间戳）",
   SO.pinned_state(0, 1791217611, False) == (False, "no_evidence"))

print("── B. 接线：list_sessions 真的把依据等级发出去 ──")
ok("B1 返回的每条带 `pinned_how` 与 `pinned_seen`",
   '"pinned_how": _how' in SRC and '"pinned_seen": bool(u in _seen_pinned)' in SRC)
ok("B2 判定走唯一实现点（`_pinned, _how = pinned_state(...)`）",
   "_pinned, _how = pinned_state(_last_ts, ts, u in _seen_pinned, _has_last)" in SRC)
# ★ 反向锚：老的"名单直接当结论"写法必须已经从源码里消失
ok("B3 ★反向锚：老写法 `or (u in _seen_pinned)` 必须已删除（那正是无法证实的 True 的来源）",
   "or (u in _seen_pinned)" not in SRC)
ok("B4 ★只把**正面证据**记进名单（名单的 True 不许自我加固）",
   'if x.get("pinned_how") == "sort_gt_last" and x["username"] not in _cur:' in SRC)
ok("B5 名单那段注释写明了「名单只证明曾经」与两种成因",
   "名单只证明" in SRC and "已取消置顶" in SRC and "假阳性" in SRC)

print("── C. 「按 DB 顺序算屏幕 y」**不许拿去点会话行**（红线棘轮）──")
# 现场：跨机实测里"一条消息被发进了真实群"——表观原因是"点会话行时行序变了"。
# 根因是结构性的：列表排序键 `sort_timestamp` **来一条新消息就重排** ⇒
# 「读顺序 → 算 y → 投点击」这三步之间必然有窗口期，点中的就是下一行 ⇒ **发错人**（不可逆）。
# ⇒ 正确做法是"当场抓一帧、按名字认出那一行、在同一帧上算 y 再点"。
_so_src = open(os.path.join(ROOT, "agent", "session_order.py"), encoding="utf-8").read()
ok("C1 `row_y` 的 docstring 写明禁令（禁直接点行、并给出正确做法）",
   "不许拿它直接去点会话行" in _so_src and "当场抓一帧" in _so_src)
# ⛔ 全仓扫"调用方"。用 **AST** 找真调用 —— 不拿文本匹（否则注释/docstring 里为了解释禁令
#    而写的 `session_order.row_y(order)` 会被判成调用 ⇒ 假红）。
import ast as _ast
import logging

log = logging.getLogger("persona-morph")


def _roy_y_calls(text: str, name: str = "row_y") -> list:
    """这份源码里**真的调用了** `name(...)` 的行号（注释与字符串里提到的不算）。"""
    out = []
    try:
        tree = _ast.parse(text)
    except Exception:
        return out
    for n in _ast.walk(tree):
        if not isinstance(n, _ast.Call):
            continue
        f = n.func
        nm = f.id if isinstance(f, _ast.Name) else (getattr(f, "attr", "") if isinstance(f, _ast.Attribute) else "")
        if nm == name:
            out.append(getattr(n, "lineno", 0))
    return out


_scan_dirs = [os.path.join(ROOT, "agent"), os.path.join(ROOT, "scripts")]
_callers = []
for _d in _scan_dirs:
    for _dp, _dn, _fns in os.walk(_d):
        _dn[:] = [x for x in _dn if x != "__pycache__"]
        for _fn in _fns:
            if not _fn.endswith(".py"):
                continue
            _p = os.path.join(_dp, _fn)
            try:
                _t = open(_p, encoding="utf-8").read()
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                continue
            for _i in _roy_y_calls(_t):
                if _fn != os.path.basename(__file__): # 本文件里那句只是反向控制用的字符串
                    _callers.append("%s:%d" % (os.path.relpath(_p, ROOT), _i))
ok("C2 ★全仓**零调用方**（要按位置点行，必须先按名字确认那一行）",
   not _callers, "出现调用：%s" % _callers[:5])
# 反向控制：扫描器本身要抓得住"真的加了调用"（否则 C2 是空转）
ok("C3 反向控制：扫描器抓得住「加了一处调用」（且不误报注释里的同名字样）",
   _roy_y_calls("y = SO.row_y(sessions, 3, 100, 98)") == [1]
   and _roy_y_calls("# 解释：不许用 row_y(order) 去点行\n") == [])
# ── C4~C6：**第二个同族入口**（`_row_y_by_db`）也要一并钉住 ──
#   为什么要盯它：它是"由 DB 序号算行 y"的**另一份实现**（在 `wechat_windows`），
#   而"点会话行"的实际落点在 `ensure_popup`。只钉住 `session_order.row_y` 不够 ——
#   真要复发，多半是从这一份复发。
_callers2 = []
for _d in _scan_dirs:
    for _dp, _dn, _fns in os.walk(_d):
        _dn[:] = [x for x in _dn if x != "__pycache__"]
        for _fn in _fns:
            if not _fn.endswith(".py"):
                continue
            _p = os.path.join(_dp, _fn)
            try:
                _t = open(_p, encoding="utf-8").read()
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                continue
            for _i in _roy_y_calls(_t, name="_row_y_by_db"):
                if _fn != os.path.basename(__file__):
                    _callers2.append("%s:%d" % (os.path.relpath(_p, ROOT), _i))
ok("C4 ★`_row_y_by_db`（同族的第二份实现）也是**零调用方**",
   not _callers2, "出现调用：%s" % _callers2[:5])
_ww_src = open(os.path.join(ROOT, "agent", "wechat_windows.py"), encoding="utf-8").read()
ok("C4b `_row_y_by_db` 的 docstring 也写明了同一条禁令（两份实现口径一致）",
   _sm.has(_ww_src, "不许拿它当"), )
# ── C5：真正动手的那一处（`ensure_popup`）必须是"名字优先 + 挑不到就拒点" ──
# ⛔ 换成 AST 锚点：原来按"函数在文件里的**文本位置**"切 ＋ 拼换行再按下一个 `def` 截断 ——
#   函数一搬位置就崩/切错。（说明故意不照抄旧语法：照抄会被同文件的"可机械改写形态"普查
#   当成新写的一处。）
_ep = _srcslice.func_src(_ww_src, "ensure_popup")
ok("C5★ `ensure_popup` **先按名字挑行**（`pick_target_row`），挑到就用它的行坐标当落点",
   _sm.has(_ep, "pick_target_row(") and _sm.has(_ep, "以它为准"), "")
_i_pk = _ep.find("_pk_y = int(_pk.get")
_i_fb = _ep.find("first_row_center_phys + int(order) * row_pitch_phys")
ok("C5b★ 顺序即意图：名字挑到的行**必须排在**系统兜底落点之前（顺序反了等于没接）",
   0 <= _i_pk < _i_fb, "名字挑行@%d 兜底落点@%d" % (_i_pk, _i_fb))
ok("C5c★ 名字也没挑到时**拒绝点击**（`unsafe_fallback`），不许拿序号算出来的落点硬点",
   _sm.has(_ep, "unsafe_fallback") and _sm.has(_ep, "拒绝点击"), "")
ok("C5d 那一步还配了退路（改走搜索），不是把整条链判死",
   _sm.has(_ep, "_open_popup_via_search"), "")
ok("C5e 反向锚：把两句顺序对调 ⇒ C5b 的位置检测必须报红",
   not (0 <= _i_fb < _i_pk), "对调后 名字挑行@%d 兜底落点@%d" % (_i_fb, _i_pk))

print("\n== 置顶判定判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
