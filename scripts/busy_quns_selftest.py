# -*- coding: utf-8 -*-
"""忙检测「看用户在不在动」判据（D2 根治，重做）。

背景：朋友版因 `SHQueryUserNotificationState` 在本机**误报「全屏演示/游戏」**
      导致机器人**一条消息都不发**，遂整段禁用忙检测。
      我方第一版根治想的是"按全屏档分级 + 浏览器/播放器豁免"，**方向被否**。

⚡ 口径：**全屏本身不是拦截理由** —— 关键看**人在不在动**（鼠标动 / 键盘有输入）。

⇒ 判据从「是不是全屏」换成「**用户此刻手在不在动**」：
  · 主判据＝`GetLastInputInfo`（`_user_active_seconds()`）：
      最近 `_USER_ACTIVE_S` 秒内有键盘/鼠标动作 ⇒ 用户在操作 ⇒ 先等输入空档（等不到就不动窗）。
      为什么是它：伪激活唯一的实际危害是"**前台一变，用户正在按的键就落进微信**"，
      而这一条吃住的正是"鼠标在动/键盘有输入"。
  · 唯一保留的"全屏"档＝**D3D 独占全屏游戏**（`_d3d_fullscreen()`）：
      伪激活会把它**踢出全屏**＝**不可逆破坏**（不是"打扰一下"），与用户手在不在动无关，故仍拦。
  · 取不到输入空闲度 ⇒ 当成"用户没在动"（与既有 `_user_idle_seconds` 同口径，绝不因此把机器人卡死）。

本判据全部为**行为级**：直接调 `agent.wechat._user_active_seconds()` /
`agent.wechat._d3d_fullscreen()`，把「QUNS 返回值」与「输入空闲秒数」打成桩，
断言**返回空串 / 非空串 / 数值区间**。不碰真实桌面（纪律：判据环境隔离）。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ROWS = []


def ck(name, cond, extra=""):
    ROWS.append((bool(cond), name, extra))


def _src(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


SRC = _src(os.path.join("agent", "wechat.py"))


def _code_only(text):
    """剥掉注释与文档字符串，只留可执行代码 —— 用于"某函数是否已被删除"这类静态判据
    （否则函数名出现在**说明它已删除的注释里**会把判据弄成假红）。"""
    import ast
    out = []
    try:
        tree = ast.parse(text)
    except Exception:                                     # noqa: BLE001
        return text
    # 收集所有字符串常量（文档字符串）与注释行号 ⇒ 从源码里剔除
    doc_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = getattr(node, "body", None)
            if d and isinstance(d[0], ast.Expr) and isinstance(d[0].value, ast.Constant)                     and isinstance(d[0].value.value, str):
                for ln in range(d[0].lineno, (d[0].end_lineno or d[0].lineno) + 1):
                    doc_lines.add(ln)
    for i, ln in enumerate(text.splitlines(), 1):
        if i in doc_lines:
            continue
        code = ln.split("#", 1)[0]
        if code.strip():
            out.append(code)
    return "\n".join(out)


CODE = _code_only(SRC)

# ── A 段：静态形态（改前必红：这些名字在旧版里不存在）──────────────────────────
ck("A1 有「用户在动」主判据 `_user_active_seconds`（＝GetLastInputInfo 那条）",
   "_user_active_seconds" in SRC and "_user_idle_seconds" in SRC)
ck("A2 有「用户活跃阈值」常量 `_USER_ACTIVE_S`（最近这么多秒有输入＝在操作）",
   "_USER_ACTIVE_S" in SRC)
ck("A3 有「D3D 独占全屏」判据 `_d3d_fullscreen`（唯一保留的全屏拦阻档）",
   "_d3d_fullscreen" in SRC)
ck("A4 有 `SHQueryUserNotificationState` 调用（D3D 档靠它取）",
   "SHQueryUserNotificationState" in SRC)
ck("A5 ⛔ 旧判据 `_fullscreen_busy` **已删除**（判据换的是「看用户手在不在动」，不是全屏分级）",
   "_fullscreen_busy" not in CODE)
ck("A6 ⛔ 旧残件 `_wait_fullscreen_clear` **已删除**（它只会等全屏档消失，已无用途）",
   "_wait_fullscreen_clear" not in CODE)
ck("A7 `_busy_reason` 里**先**判「用户在动」、**再**判 D3D（顺序不能反）",
   SRC.index("_user_active_seconds()") < SRC.index("_d3d_fullscreen()"))

# ── B 段：行为级 —— 打桩 QUNS 返回值，看 `_d3d_fullscreen()` 的答案 ────────────
try:
    from agent import wechat as _w
except Exception as e:                                    # noqa: BLE001
    _w = None
    ck("B0 能 import agent.wechat", False, "import 失败：%s" % str(e)[:80])


class _Byref:
    """冒充 ctypes.byref() 的返回值：记住目标 c_int 对象，让被桩的 API 能写回。"""

    def __init__(self, obj):
        self._obj = obj


def _patch(quns_val, idle_s):
    """把 QUNS 返回值与「输入空闲秒数」打成桩。返回一个 restore 回调。

    手段：替换 `ctypes.windll.shell32`（给 `_d3d_fullscreen`）与
    `agent.wechat._user_idle_seconds`（给 `_user_active_seconds`）。
    `_d3d_fullscreen` 里 `ctypes.windll` 取到的是同一模块对象 ⇒ 桩生效。
    只在**本判据进程**里动，用完即恢复。
    """
    import ctypes as _ct

    _old_windll = getattr(_ct, "windll", None)
    _old_byref = getattr(_ct, "byref", None)

    def _byref(obj):
        return _Byref(obj)

    class _Shell32:
        @staticmethod
        def SHQueryUserNotificationState(ref):
            obj = getattr(ref, "_obj", None)
            if obj is not None:
                try:
                    obj.value = quns_val
                except Exception:
                    pass
            return 0

    class _Windll:
        shell32 = _Shell32()

    _ct.windll = _Windll()
    _ct.byref = _byref

    _old_idle = None
    if _w is not None:
        _old_idle = getattr(_w, "_user_idle_seconds", None)
        _w._user_idle_seconds = (lambda: float(idle_s))

    def _restore():
        if _old_windll is not None:
            _ct.windll = _old_windll
        if _old_byref is not None:
            _ct.byref = _old_byref
        if _w is not None and _old_idle is not None:
            _w._user_idle_seconds = _old_idle

    return _restore


def _d3d_with(quns_val):
    """桩住 QUNS，直接问 `_d3d_fullscreen()`。"""
    if _w is None:
        return "<no module>"
    restore = _patch(quns_val, 1e9)
    try:
        return _w._d3d_fullscreen()
    finally:
        restore()


def _active_with(idle_s):
    """桩住输入空闲秒数，问 `_user_active_seconds()`。"""
    if _w is None:
        return -1.0
    restore = _patch(5, idle_s)
    try:
        return _w._user_active_seconds()
    finally:
        restore()


# QUNS 值：1=不在场 2=全屏应用 3=D3D独占 4=演示模式 5=正常 6=静默时段 7=Store应用全屏
ck("B1 D3D 独占全屏档 ⇒ `_d3d_fullscreen()` 非空（唯一保留的全屏拦阻档）",
   _d3d_with(3) != "")
ck("B2 全屏应用档（QUNS=2，旧判据的误报现场）⇒ `_d3d_fullscreen()` **为空**（不再拦）",
   _d3d_with(2) == "")
ck("B3 Store 应用全屏档（QUNS=7，朋友版误报的另一种形态）⇒ 为空（不再拦）",
   _d3d_with(7) == "")
ck("B4 演示模式档（QUNS=4）⇒ 为空（不再拦）",
   _d3d_with(4) == "")
ck("B5 静默时段档（QUNS=6）⇒ 为空（不再拦）",
   _d3d_with(6) == "")
ck("B6 不在场档（QUNS=1，可能只是全屏看视频）⇒ 为空（不再拦）",
   _d3d_with(1) == "")
ck("B7 正常状态（QUNS=5）⇒ 为空（回归：别把正常态判成忙）",
   _d3d_with(5) == "")

ck("B8 `_user_active_seconds()` 取到的就是输入空闲秒数（0.3s ⇒ 约 0.3）",
   abs(_active_with(0.3) - 0.3) < 1e-6)
ck("B9 用户刚动过（idle=0.2 < 阈值）⇒ 判「在操作」（值 < `_USER_ACTIVE_S`）",
   _active_with(0.2) < _w._USER_ACTIVE_S)
ck("B10 用户手停了很久（idle=30 > 阈值）⇒ 判「没在动」（值 >= `_USER_ACTIVE_S`）",
   _active_with(30.0) >= _w._USER_ACTIVE_S)

# ── C 段：反向锚 —— 证明判据没有退化成"永远放行" ────────────────────────────────
ck("C1 反向锚：D3D 独占档**真的拦得住**（不是永远放行）", _d3d_with(3) != "")
_n_block = sum(1 for v in (3,) if _d3d_with(v) != "")
_n_free = sum(1 for v in (1, 2, 4, 5, 6, 7) if _d3d_with(v) == "")
ck("C2 反向锚：除 D3D 外**六个档一律放行**（旧判据正是全拦 ⇒ 朋友版完全不发消息的现场）",
   _n_free == 6, "放行 %d/6" % _n_free)
ck("C3 反向锚：`_busy_reason` 源码里**确有**「用户在动 ⇒ 等空档」这条（不是只留 D3D）",
   "_wait_user_pause" in SRC and "在操作" in SRC)

# ── D 段：喂饱 `_busy_reason` 的两个分支，端到端断言 ─────────────────────────────
# 造一个只带 `_busy_reason` 所需外部依赖的最小对象，直接调被桩住的 `_busy_reason`。
class _FakeSelf:
    """`_busy_reason` 只用到 `self` 以外的模块级函数 ⇒ 空壳即可。"""


def _busy_reason_with(idle_s, quns_val, wait_s=0.0):
    if _w is None:
        return "<no module>"
    restore = _patch(quns_val, idle_s)
    try:
        return _w.WeChatAdapter._busy_reason(_FakeSelf(), "判据", wait_s)
    except AttributeError:
        # 名字不是 Agent / 不是实例方法 ⇒ 退回直接取模块级（若被拆出来）
        try:
            return _w._busy_reason(_FakeSelf(), "判据", wait_s)
        except Exception as e:                            # noqa: BLE001
            return "<err:%s>" % str(e)[:60]
    except Exception as e:                                # noqa: BLE001
        return "<err:%s>" % str(e)[:60]
    finally:
        restore()


ck("D1 手没在动 + 非 D3D ⇒ `_busy_reason` 返回空（**放行**，朋友误报现场不再停）",
   _busy_reason_with(30.0, 2) == "")
ck("D2 手没在动 + D3D 独占 ⇒ `_busy_reason` 返回非空（拦，保护不可逆破坏）",
   _busy_reason_with(30.0, 3) != "")
ck("D3 用户一直在操作（等不到空档）⇒ `_busy_reason` 返回非空且**说明是「在操作」**",
   _busy_reason_with(0.0, 5) != "" and "操作" in _busy_reason_with(0.0, 5))

# ── 输出 ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ok = sum(1 for r in ROWS if r[0])
    bad = [r for r in ROWS if not r[0]]
    for _ok, _nm, _ex in ROWS:
        if not _ok:
            print("FAIL: %s%s" % (_nm, ("  ← " + _ex) if _ex else ""))
    print("忙检测（看用户在不在动）判据：%d 通过 / %d 失败" % (ok, len(bad)))
    sys.exit(0 if not bad else 1)
