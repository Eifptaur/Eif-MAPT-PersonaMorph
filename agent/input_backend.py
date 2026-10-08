# -*- coding: utf-8 -*-
"""输入后端（最高目标「全程后台、不抢鼠标」的落地模块）：**投递优先，真实输入兜底**。

为什么要有这一层：项目里原先到处直接 `mouse_event`（`wechat.py` 21 处 / `wechat_ui.py` 7 处），
"不动鼠标"根本无从保证。本模块把 UI 动作收成两个原语（`click` / `send_text`），
按 L5 → L0 顺序取用，并把「当前用的是哪一档」暴露给控制台。

两档实现（判据全部来自本项目实测，不是外推）：
  · `MessageBackend`（L5，默认）：`PostMessage` 发 `WM_ACTIVATE`/`WM_NCACTIVATE` 伪激活 + 鼠标/键盘消息。
    实测：①投递 `WM_CHAR` + 投递点「发送」按钮 ⇒ **3/3 DB 回读命中**，光标与前台未变；
    ②投递点笑脸 ⇒ 表情面板弹出（新顶层窗 `Qt51514QWindowToolSaveBits`）；③投递点面板里的收藏格
    ⇒ **DB 回读 `type=动画表情`**（`local_id 550→551`）⇒ 发送与表情两条链都能纯后台走完。
  · `RealInputBackend`（L0，兜底）：走 `ui_adapt.click` 的真鼠标路径；用完 `heal_input()` 恢复光标。

三条硬纪律（写进代码而不是只写文档）：
  1. **本模块 import 时就锁 PerMonitorV2**——实测同一进程里不锁 DPI 时 `GetWindowRect` 给的是
     虚拟坐标（面板 514×514），而微信是物理坐标（771×771，差 1.5 倍）⇒ 投递坐标落到窗口外、点了等于没点。
  2. **不置顶、不 `SetForegroundWindow`、不动光标**：投递就是投递，绝不靠"把窗口怼到用户脸上"换成功率。
  3. **主窗只用来"找会话"，之后一律在独立窗做完**；**主窗不许最小化**（最小化也是动它），
     要"让开"就压到后台：`ShowWindow(SW_SHOWNOACTIVATE)` ＋
     `SetWindowPos(HWND_BOTTOM, SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE)` ⇒ **前台不受影响**。
     ⛔ 更不许"放出来看一眼、再放/缩回去"——那个来回本身就是放缩，就是干扰用户。
     （唯一未确认的例外：自己发朋友圈那一段是不是前台档，待真机确认；点朋友圈双击也能开独立窗。）
  3. **投递点击要发给"真正接收该点击的那个窗口"**：主窗用于点笑脸，面板弹出后要发给面板窗
     （已实测：面板 0 个子窗，`WindowFromPoint` 归属就是面板本身）。
"""
from __future__ import annotations

import ctypes
import re
import time
from ctypes import wintypes

from .config import get_config

# ⚠️ **这个模块一直在用 `log` 却从没定义过它**。
#   为什么以前没炸：唯一的调用位于 `menu_click` 的"items 非空"分支，
#   而修复前菜单 OCR **永远读不出 items**（原尺寸读不出的那个真因）⇒ 那行**从没被执行到**。
#   我的"原尺寸读不出就放大重读"修复让它第一次执行 ⇒ `NameError: name 'log' is not defined`
#   ⇒ 整条投递右键链抛异常（现象＝**菜单明明弹了、就是拍不上**）。日志原文：
#   `投递右键菜单异常（交给原实现）：name 'log' is not defined`。
import logging

log = logging.getLogger("persona-morph")

# ── ① DPI：import 即锁，任何坐标运算之前 ──────────────────────────────────
_user32 = ctypes.windll.user32


def _lock_dpi() -> str:
    """锁 DPI 上下文 —— **必须检查返回值**：这些 API 失败时只返回 0、不抛异常
    。"""
    try:
        if _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)): # PerMonitorV2
            return "PerMonitorV2"
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    try:
        if int(ctypes.windll.shcore.SetProcessDpiAwareness(2)) == 0: # S_OK
            return "shcore/2"
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    try:
        _user32.SetProcessDPIAware()
        return "user32/legacy"
    except Exception:
        return "none"


DPI_MODE = _lock_dpi()

_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
_user32.WindowFromPoint.argtypes = [wintypes.POINT]
_user32.WindowFromPoint.restype = wintypes.HWND

# ── 消息常量（投递序列用到的全部）────────────────────────────────────────
WM_ACTIVATE, WM_NCACTIVATE, WM_MOUSEACTIVATE = 0x0006, 0x0086, 0x0021
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
# 右键
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
# 微信的菜单/弹层顶层窗类名（投递右键弹出的菜单就是它；表情面板也用同一个类名 ⇒ 必须差分）
MENU_CLASS = "Qt51514QWindowToolSaveBits"
WM_CHAR, WM_KEYDOWN, WM_KEYUP = 0x0102, 0x0100, 0x0101
WM_PASTE, WM_DROPFILES, WM_MOUSEWHEEL = 0x0302, 0x0233, 0x020A
VK_CONTROL, VK_V, VK_RETURN = 0x11, 0x56, 0x0D

MAIN_CLASS = "Qt51514QWindowIcon" # 微信主窗
PANEL_CLASS = "Qt51514QWindowToolSaveBits" # 表情面板等弹层
LEVEL_MESSAGE = "message" # L5
LEVEL_REAL = "real" # L0

_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_ssize_t, ctypes.c_ssize_t]
_user32.PostMessageW.restype = wintypes.BOOL
_post = _user32.PostMessageW # 单点可替换，供自检做无微信单测
# ⚠️ 上面两行必须声明 argtypes/restype：不声明时 64 位下的 WPARAM/LPARAM 会被当 32 位，
# 传 HDROP 句柄（64 位整数）直接 `OverflowError: int too long to convert`。
#    同类坑还有 clipboard.py 里的 GlobalLock —— 那边更狠，会直接崩进程。


def pack_lparam(x: int, y: int) -> int:
    """把客户区坐标打包成 lParam（低 16 位 x，高 16 位 y；各限 16 位）。"""
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def to_client(hwnd: int, screen_pt) -> tuple:
    """屏幕坐标 → 目标窗口客户区坐标（同一进程里必须已是物理像素口径）。"""
    p = wintypes.POINT(int(screen_pt[0]), int(screen_pt[1]))
    _user32.ScreenToClient(int(hwnd), ctypes.byref(p))
    return (int(p.x), int(p.y))


def screen_point(rect, rel) -> tuple:
    """渲染区相对坐标 → 屏幕坐标。rect = (left, top, right, bottom)。"""
    return (int(rect[0]) + int(rel[0]), int(rect[1]) + int(rel[1]))


def window_rect(hwnd: int) -> tuple:
    r = wintypes.RECT()
    _user32.GetWindowRect(int(hwnd), ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def _class_of(hwnd: int) -> str:
    b = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(int(hwnd), b, 256)
    return b.value


def _pid_of(hwnd: int) -> int:
    p = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(p))
    return int(p.value)


def _child_classes(hwnd: int) -> set:
    out = set()
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _l):
        out.add(_class_of(h))
        return True

    _user32.EnumChildWindows(int(hwnd), CB(cb), 0)
    return out


RENDER_CHILD = "MMUIRenderSubWindowHW" # 主窗特有的渲染子窗

#: 主窗的**标题定值**（实测钉死）——这是**唯一能把主窗与独立聊天窗分开**的可靠特征。
#: ⛔⛔ 事故：`pick_main_window` 原判据是
#:     「①优先带渲染子窗 ②同分取面积最大」——**独立聊天窗两条都满足**：
#:       · 实测独立窗 `1113×910`（有 `MMUIRenderSubWindowHW`）
#:       · 实测主窗  `987×1016`（也有此子窗）
#:     ⇒ 独立窗**面积更大** ⇒ `find_main_window()` **返回了独立窗** ⇒
#:       落点算在"独立窗坐标系"里、`_is_main_window` 把独立窗当主窗 ⇒
#:       `find_popup` 永远查不到独立窗（自己把自己排除了）⇒ **开窗永远判失败**。
#:   ⇒ 用**标题**兜底：主窗恒为 `微信`，独立窗是**会话名**（实测：腾讯新闻/某群/…）。
MAIN_TITLE = "微信"


def pick_main_window(cands) -> int:
    """从候选里挑主窗（**纯函数，可单测**）。

    cands: `[(hwnd, has_render_child, area, visible[, title])]`
      （`title` 为**可选**第 5 项；不给则退化为旧行为，兼容既有单测）

    规则（**按优先级**）：
      ① **标题 == `微信`** 的优先 —— 这是**唯一可靠**区分主窗/独立窗的特征
         （独立窗标题 = 会话名，且**面积可能比主窗更大** ⇒ 不能靠面积）
      ② 其次"带渲染子窗"的（朋友圈编辑窗也同类名，但它没有渲染子窗）
      ③ 再按面积最大
      ④ **不看可见性** —— 窗口被隐藏/最小化时也要认得出主窗，
         否则 auto 会静默降级成真鼠标档。
    """
    best, best_key = 0, None
    for cand in (cands or []):
        hwnd, has_child, area, _vis = cand[0], cand[1], cand[2], cand[3]
        title = (cand[4] if len(cand) > 4 else "") or ""
        key = (1 if title.strip() == MAIN_TITLE else 0,
               1 if has_child else 0,
               int(area or 0))
        if best_key is None or key > best_key:
            best, best_key = int(hwnd), key
    return best


def find_render_child(main_hwnd: int) -> int:
    """主窗里的**渲染子窗**（`MMUIRenderSubWindowHW`）：Qt 自绘界面真正绘制的那一层。

    ⚠️ 它**能不能收投递鼠标消息，跟着微信版本变**，所以**别再拿它当会话行点击的唯一目标**：
      · （微信 4.1.15.8）：投渲染子窗能切换会话、投主窗不行；
      · A/B 同落点同节奏）：**反过来了** —— 投主窗 5/5 生效（绿底高亮带
        落到目标行、聊天区像素变化），投渲染子窗 0/5（毫无反应）。
    ⇒ 会话行点击一律用 `row_click_targets()` **按序试 + 每枪自检**。
    """
    if not main_hwnd:
        return 0
    import win32gui
    hits = []

    def cb(h, _l):
        try:
            cls = win32gui.GetClassName(h)
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            return True
        if "MMUIRender" in cls or "RenderSubWindow" in cls:
            hits.append(int(h))
        return True

    try:
        win32gui.EnumChildWindows(int(main_hwnd), cb, None)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return 0
    return hits[0] if hits else 0


def row_click_targets(main_hwnd: int) -> list:
    """**会话行点击**的目标窗候选顺序（按序试，每枪自检是否生效）。

    为什么不再硬编码单一目标：同一个落点、同一套节奏，
    **投主窗 5/5 生效**（绿底高亮带移到目标行、聊天区像素变化率 0.03~0.18），
    **投渲染子窗 0/5**（毫无反应）——而实测结论**正好相反**。
    这就是"哪个窗收鼠标消息"随微信版本变（Qt 自绘层的命中测试实现变过），
    ⇒ 顺序＝**主窗优先**（当前版本有效），不成再换渲染子窗（老版本有效）。

    ⚠️ 调用方**必须**保证同一落点的两枪间隔 >1.2s：微信按"间隔 + 位置"自己判双击，
    两次挨太近会把会话**拖成浮动窗**甚至直接关掉（`AGENTS.md`：连点两下会把聊天框关掉）。
    """
    out = []
    if main_hwnd:
        out.append(int(main_hwnd))
    try:
        r = find_render_child(int(main_hwnd)) if main_hwnd else 0
    except Exception:
        r = 0
    if r and int(r) not in out:
        out.append(int(r))
    return out


def win_kind(main_hwnd: int, hwnd: int) -> str:
    """给日志/说明用：这个点击目标是「主窗」还是「渲染子窗」（认不出来给句柄）。"""
    try:
        if int(hwnd) == int(main_hwnd):
            return "主窗"
        if int(hwnd) == int(find_render_child(int(main_hwnd)) or 0):
            return "渲染子窗"
    except Exception:
        pass
    return "其它窗(%s)" % hwnd


#: `find_main_window` 的短 TTL 缓存（秒）。
#: ⛔ 为什么需要：它每次都要 `EnumWindows` + 对每个候选 `_child_classes()`（枚举子窗）
#:   ⇒ 实测单次 **≈0.4~0.9s**。而 `wechat_windows.find_popup()` 每找一次就要问它一次
#:   ⇒ `ensure_popup` 的一个开窗循环里被调 **9 次**，白烧 **7.6s**。
#: ✅ 主窗句柄在**秒级内不会变**（变了也有 TTL 兜住），故缓存 1.5s；
#:   窗口关闭/重启后 `IsWindow` 会失效 ⇒ 取缓存前先验一次。
_MAIN_TTL_S = 1.5
_main_cache = {"hwnd": 0, "t": 0.0}


def find_main_window(*, use_cache: bool = True) -> int:
    """微信**主窗**（投递键盘消息、点笑脸都发它）。

    ⚠️ 不能用 `FindWindow(类名)`：表情面板之外的**朋友圈纯文字编辑窗也是
    `Qt51514QWindowIcon`**，`FindWindow` 返回的是第一个命中的那个（踩到——
    投递全打到编辑窗上，发送自然失败）。
    ⚠️ **也不能只认"可见"的窗口**：主窗被收进托盘/隐藏时，按可见性过滤会返回 0，
    于是 `auto` 档悄悄降级成真鼠标档（会动用户光标）——这正是最高目标里"不许悄悄降级"的场景。

    ⭐ **短 TTL 缓存**（`use_cache=True`，默认）：见 `_MAIN_TTL_S` 的注释。
      要高精度"现查"，传 `use_cache=False`（诊断/断言用）。
    """
    import time as _time
    if use_cache:
        c = _main_cache
        if c["hwnd"] and (_time.time() - c["t"]) < _MAIN_TTL_S:
            if _user32.IsWindow(int(c["hwnd"])):     # 句柄还有效 ⇒ 直接给
                return int(c["hwnd"])
    cands = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _l):
        if _class_of(h) == MAIN_CLASS:
            r = window_rect(h)
            area = max(0, (r[2] - r[0])) * max(0, (r[3] - r[1]))
            # ★ 第 5 项：**标题**（区分主窗/独立窗的唯一可靠特征，见 pick_main_window）
            tbuf = ctypes.create_unicode_buffer(512)
            _user32.GetWindowTextW(int(h), tbuf, 512)
            cands.append((int(h), RENDER_CHILD in _child_classes(h), area,
                          bool(_user32.IsWindowVisible(h)), tbuf.value or ""))
        return True

    _user32.EnumWindows(CB(cb), 0)
    got = pick_main_window(cands)
    if got:
        _main_cache["hwnd"] = int(got)
        _main_cache["t"] = _time.time()
    return int(got)


def find_panel_window(main_hwnd: int = 0) -> int:
    """微信弹层窗（表情面板）：按类名找，同进程优先。找不到返回 0。"""
    hit = [0]
    want_pid = _pid_of(main_hwnd) if main_hwnd else 0
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _l):
        if _user32.IsWindowVisible(h) and _class_of(h) == PANEL_CLASS:
            if not want_pid or _pid_of(h) == want_pid:
                hit[0] = int(h)
                return False
        return True

    _user32.EnumWindows(CB(cb), 0)
    return hit[0]


def refresh_main_window() -> int:
    """**重取**微信主窗句柄（绕过缓存，现查一次）。

    P1-6 用：某些失败是"句柄过期"造成的（微信主窗被重建/重启，旧 hwnd 随之失效）。
    这类失败**重连一次就好** —— 但只许**一次**（真·窗口关了就去关，重试到天荒地老没意义）。
    """
    try:
        _main_cache["hwnd"] = 0
        _main_cache["t"] = 0.0
        return int(find_main_window(use_cache=False) or 0)
    except Exception:  # noqa: BLE001
        return 0


def with_reconnect_once(fn, *, what: str = ""):
    """**已知错误码自愈**：先跑 `fn(hwnd)`；失败若像"句柄过期" ⇒ 重取句柄再跑**一次**。

    参数：
      · `fn`：`fn(hwnd) -> (ok, why)`；`ok` 为假时看 `why` 判是否属于"句柄过期"。
      · `what`：日志用的一句话。

    返回 `(ok, why, retried)`：`retried=True` 表示走过了"重取句柄再试一次"。

    ⛔ **只重连一次**（不做无限重试）—— 真·窗口不可用（微信关了/权限不足）时，
      重试再多次也不会变好，只会把真因埋在重试噪声里。
    """
    try:
        hwnd = int(find_main_window() or 0)
    except Exception:  # noqa: BLE001
        hwnd = 0
    try:
        ok, why = fn(hwnd)
    except Exception as e:  # noqa: BLE001
        ok, why = False, "%s: %s" % (type(e).__name__, e)
    if ok:
        return True, why, False
    if not stale_handle_like(why):
        return False, why, False
    # 像"句柄过期" ⇒ 重取一次再试（**仅此一次**）
    _new = refresh_main_window()
    if not _new or _new == hwnd:
        # 句柄没变（或取不到）⇒ 重试没有意义（问题不在句柄）⇒ 如实回原因为止
        return False, why, False
    try:
        ok2, why2 = fn(_new)
    except Exception as e:  # noqa: BLE001
        ok2, why2 = False, "%s: %s" % (type(e).__name__, e)
    log.warning("已知错误自愈：重取主窗句柄 %s ⇒ %s 后重试一次：%s",
                hwnd, _new, "成功" if ok2 else "仍失败")
    return bool(ok2), why2, True


#: "像句柄过期"的**已知**措辞（白名单，不猜同义词）。
#: 命中 ⇒ 值得"重取句柄再试一次"；不命中 ⇒ 一次都不重试。
_STALE_HANDLE_HINTS = (
    "句柄失效", "句柄无效", "invalid handle", "窗口句柄为空", "hwnd 失效",
    "主窗变了", "窗口不存在", "IsWindow", "窗口已关闭", "窗口找不到了",
)


def stale_handle_like(why) -> bool:
    """这段失败原因**像不像**"句柄过期"（只认已知措辞；拿不准一律 False）。"""
    s = str(why or "").lower()
    if not s:
        return False
    for k in _STALE_HANDLE_HINTS:
        if k.lower() in s:
            return True
    return False


# ── 后端 ────────────────────────────────────────────────────────────────
class InputBackend:
    """输入后端基类：两个原语 + 一档名字。"""

    name = "base"
    touches_cursor = False

    def click(self, hwnd: int, screen_pt, right: bool = False) -> tuple:
        raise NotImplementedError

    def send_text(self, hwnd: int, text: str) -> tuple:
        raise NotImplementedError

    def status(self) -> dict:
        return {"backend": self.name, "level": "?", "touches_cursor": self.touches_cursor}


class MessageBackend(InputBackend):
    """L5：投递消息。不动光标、不打扰你（可能短暂置前约 1~3 秒后自动还回）、不需要目标窗口在前台。"""

    name = LEVEL_MESSAGE
    touches_cursor = False
    level = "L5"

    def __init__(self, press_ms: int = 60, activate: bool = True):
        self.press_ms = int(press_ms)
        self.activate = bool(activate)

    def _wake(self, hwnd: int) -> None:
        """伪激活：让目标自认为被激活（不等于改前台窗口）。

        ⚡ 提速：`time.sleep(0.08)` 已**删除**。
        为什么能删（**真机实验，不是推断**）：
          · 这两条 `PostMessage` 本身只要 **0.02ms**，那 0.08 秒（实测 93ms）是**纯白等**；
          · 投递是**异步但有序**的 —— 后续 `WM_CHAR` / `WM_KEYDOWN` 排在**同一个消息队列**里，
            微信处理激活消息与后续按键的**顺序天然有保证**，不需要靠 sleep 来"隔开"；
          · **实测（本机微信 4.1.15.13，5 轮）**：删掉 sleep 后
            「投字后发送键绿占比 0.444 → 回车后 0.000」**5/5 全部真发出**，
            与保留 sleep 的对照组**结果完全一致**，而单次耗时从 **201.7ms → 18.4ms**。
        ⇒ 判据是二值的（真发出 / 没发出），不是像素差估计，所以这个"删得掉"是**确定的**。
        """
        if not self.activate:
            return
        _post(int(hwnd), WM_ACTIVATE, 1, 0)
        _post(int(hwnd), WM_NCACTIVATE, 1, 0)

    def click(self, hwnd: int, screen_pt, right: bool = False, hover_ms: int = 0,
              press_ms: int = None, right_keep_target: bool = False) -> tuple:
        """投递点击。`hover_ms`＝按下前**先悬停**多久（默认 0 ⇒ 沿用 50ms 落点稳定），
        `press_ms`＝按住多久（默认 self.press_ms）。

        ⚠️ 微信**会话列表行**必须要"慢节奏"才认：
          ① 只投主窗 ✗；② 投渲染子窗但用快节奏（悬停 50ms + 按住 60ms）**✗ 高亮不动**；
          ③ 投渲染子窗 + **悬停 300ms + 按住 150ms** ✓ 高亮立刻跳到目标行。⇒ 会话行点击一律用慢节奏。

        ⛔⛔ **`right_keep_target`（新增，修一个害人的隐式行为）**：
          原本 `right=True` 时**无条件**把目标换成 `find_main_window()` —— 那是为
          **主窗会话列表**写的特例（右键必须投主窗才弹菜单）。但它**不问调用方是谁**，
          于是**对独立窗投右键也会被偷偷改成主窗** ⇒ 实测后果：
          往「某群」独立窗的输入框投右键，菜单却**弹在主窗的会话列表上**
          （OCR 读到"标为未读 / 消息免打扰 / 独立窗口显示 / 不显示 / 删除"），
          用户屏幕上亲眼看到"右键点到主窗去了"。
          ⇒ 独立窗（自己的顶层窗）场景必须传 `right_keep_target=True`，让右键投**它自己**。
          默认 `False` ＝ 保持旧行为，绝不影响既有主窗调用方。
        """
        if not hwnd:
            return False, "窗口句柄为空"
        # ⚠️ （两靶点各有真实右键阳性对照，见 `_scratch/rclick_avatar.py`）：
        #   **右键投渲染子窗不弹菜单（新顶层窗 0 / 像素差 0.000），投「主窗」才弹**
        #   （弹出 `Qt51514QWindowToolSaveBits`，像素差 0.026~0.035）；
        #   而**左键**点会话行恰恰相反、必须投渲染子窗。⇒ 右键一律**自动换成主窗**，
        #   调用方不用关心这个差异（这里替换掉了原先"右键直接拒绝"的写法）。
        #   ※ 上面这条只对**主窗**成立；独立窗请传 right_keep_target=True（见 docstring）。
        tgt = int(hwnd)
        if right and not right_keep_target:
            try:
                m = find_main_window()
                if m:
                    tgt = int(m)
            except Exception:
                pass
        cx, cy = to_client(tgt, screen_pt)
        self._wake(tgt)
        down, up = (WM_RBUTTONDOWN, WM_RBUTTONUP) if right else (WM_LBUTTONDOWN, WM_LBUTTONUP)
        wdown, wup = (2, 0) if right else (1, 0)
        _post(tgt, WM_MOUSEMOVE, 0, pack_lparam(cx, cy))
        time.sleep(0.05 if not hover_ms else max(0.05, int(hover_ms) / 1000.0))
        _post(tgt, down, wdown, pack_lparam(cx, cy))
        time.sleep((self.press_ms if press_ms is None else int(press_ms)) / 1000.0)
        _post(tgt, up, wup, pack_lparam(cx, cy))
        return True, ""

    def wheel(self, hwnd: int, screen_pt, delta: int = -120, times: int = 1, gap_ms: int = 60) -> tuple:
        """投递滚轮（`WM_MOUSEWHEEL`）：`delta` 一格＝±120（负＝向下滚），`times` 可一次发多格。

        为什么带 `times` 和间隔：
        －－单发一格、中间不歇，自绘列表容易处理不过来或只滚一点点；连续多格 + 每格 60ms 才像人滚。
        ⚠️ `WM_MOUSEWHEEL` 的 lParam 是**屏幕坐标**（与 `WM_LBUTTONDOWN` 用客户区坐标不同）。
        """
        if not hwnd:
            return False, "窗口句柄为空"
        try:
            x, y = int(screen_pt[0]), int(screen_pt[1])
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            return False, "滚轮落点无效"
        self._wake(hwnd)
        _post(int(hwnd), WM_MOUSEMOVE, 0, pack_lparam(*to_client(hwnd, (x, y))))
        n = max(1, int(times))
        for _ in range(n):
            _post(int(hwnd), WM_MOUSEWHEEL, ((int(delta) & 0xFFFF) << 16), pack_lparam(x, y))
            time.sleep(max(0, int(gap_ms)) / 1000.0)
        return True, ""

    def send_text(self, hwnd: int, text: str) -> tuple:
        """逐字投递 `WM_CHAR`（提交由调用方点发送按钮）。

        ⛔ **必须先 `_wake`**（实测，报告 §62）：
          递方式应该差不多" ⇒ 顺着查发现 `click()` / `wheel()` 都调了
          `self._wake()`，**只有 `send_text()` 漏了**。
          独立窗（双击会话出来的单窗）上实测四组合：
            · 顶层窗 + `_wake` 后投 `WM_CHAR`      ⇒ **绿占比 0.59／0.63 ⇒ 字进去了 ✅**
            · 顶层窗（**不 `_wake`**）投 `WM_CHAR`  ⇒ 绿占比 0.0000 ⇒ **字没进去** ❌
            · 渲染子窗（`find_render_child`）+ `_wake` ⇒ 0.0000 ⇒ 不过
            · 渲染子窗 + 单独投 `WM_ACTIVATE`       ⇒ 0.0000 ⇒ 不过
          ⇒ **结论：目标＝顶层窗；但发字符前必须先伪激活**。
          （主窗上"不唤醒也能进"是既有实测，与"独立窗需要唤醒"并不矛盾 ——
            独立窗创建后可能从未被激活过，故必须补这一步。）
        """
        if not hwnd:
            return False, "窗口句柄为空"
        if not text:
            return False, "文本为空"
        self._wake(int(hwnd))
        for ch in str(text):
            _post(int(hwnd), WM_CHAR, ord(ch), 1)
        return True, ""

    def _paste_target(self, hwnd: int) -> tuple:
        """`WM_PASTE` 该投给**哪个**窗口 ⇒ `(句柄, 说明)`；拿不到就退回传入的那个。

        ⛔⛔ **不能投顶层窗**（实测）：投给顶层窗的 `WM_PASTE` **一点效果都没有** ——
          可见档、最小化档都试过，输入框"发送键绿占比"恒 `0.0`（＝字没进去）。
          这与项目在"发图"那条路量到的是同一件事（`keys()` 的 docstring：
          「发图要投给渲染子窗 `MMUIRenderSubWindowHW` —— 投给主窗完全无效」）。
        ⇒ 顺序：① **`Edit` 类子窗**（最标准的输入控件，有就往那儿投）
          → ② **渲染子窗**（`find_render_child`，Qt 自绘界面真正绘制/收消息的那一层）
          → ③ 都找不到 ⇒ 退回顶层窗（保持老行为，**如实说明**别让人以为一定成功）。
        """
        try:
            import ctypes as _ct
            from ctypes import wintypes as _wt
            u = _ct.windll.user32
            hit = {"edit": 0}
            buf = _ct.create_unicode_buffer(256)

            @_ct.WINFUNCTYPE(_ct.c_bool, _wt.HWND, _wt.LPARAM)
            def cb(ch, _l):
                try:
                    u.GetClassNameW(ch, buf, 256)
                    if buf.value == "Edit" and not hit["edit"]:
                        hit["edit"] = int(ch)
                except Exception: # noqa: BLE001
                    pass
                return True

            u.EnumChildWindows(_ct.c_void_p(int(hwnd)), cb, 0)
            if hit["edit"]:
                return hit["edit"], "Edit 子窗"
        except Exception: # noqa: BLE001
            pass
        try:
            rc = find_render_child(int(hwnd))
            if rc:
                return int(rc), "渲染子窗"
        except Exception: # noqa: BLE001
            pass
        return int(hwnd), "顶层窗（没找到输入子窗，成功率不保证）"

    def paste_text(self, hwnd: int, text: str, restore_ms: int = 350) -> tuple:
        """**用「粘贴」代替逐字投递** —— 中文经 `WM_CHAR` 会被 IME 串一个字母。

        ⛔ 为什么必须换：实测（朋友版踩过、我方同源）**微软拼音开着时**，
        `WM_CHAR` 直接塞进去的字符会在**首个汉字前串一个 `a`** ⇒ 对方/搜索框收到的是 `a测试`。
        粘贴不走 IME 的「直接塞字符」路径，因此不串字母。

        做法（三步，顺序不能变）：
          ① **先快照剪贴板**（剪贴板不是我们的资源，用完必须还原）——
             用 `clipboard.snapshot()`（它连 `CF_HDROP` 文件列表一起备份），
             ⛔ **不许只存文本**：用户剪贴板里常是"复制的文件"，只存文本＝**把人家复制的文件弄丢**；
          ② 写入待发文本（`clipboard.set_text`）⇒ 投 `WM_PASTE`；
          ③ **等 `restore_ms` 再 `clipboard.restore()`** —— `WM_PASTE` 是**异步**的，
             立刻还原会把**原文**贴进输入框。
        ⛔ 失败一律**如实返回原因**，不静默（调用方据此退回 `send_text`）。
        ⛔ **本函数只能用 `agent.clipboard` 里真实存在的 API**：曾经写成 `read_text`/`write_text`
          （那两个函数**不存在**）⇒ `hasattr` 恒假 ⇒ 剪贴板永远不还原、且整条粘贴路
          **每次都抛 AttributeError 被上层吞掉** ⇒ 静默退回逐字投递（IME 串字母的现场就是这么来的）。
          判据 `clipboard_selftest` 有一条"用到的名字必须都存在"盯着这类死引用。
        """
        if not hwnd:
            return False, "窗口句柄为空"
        if not text:
            return False, "文本为空"
        try:
            from agent import clipboard as _cb
        except Exception as e: # noqa: BLE001
            return False, "剪贴板模块不可用：%s" % type(e).__name__
        try:
            self._wake(int(hwnd))
            # ⭐ 剪贴板保护走**项目自己的收口 `clipboard_guard`**（进入时快照、**出块必还原**，含异常出口）：
            #   ⛔ 不许自己手写 `snapshot()/restore()` 配对 —— 那样**异常路径会漏还原**，
            #     而且判据 `background_selftest` B28b 只认这一个收口（"每个写剪贴板的函数都要有保护"）。
            #   ⛔ `restore_ms` 的等待必须留**在块内**：`WM_PASTE` 是**异步**的，
            #     立刻还原会把剪贴板里**原来的内容**贴进输入框。
            with _cb.clipboard_guard() as _g:
                _w_ok, _w_why = _cb.set_text(str(text))
                if not _w_ok:
                    return False, "写剪贴板失败：%s" % str(_w_why)[:60]
                _tgt, _how = self._paste_target(int(hwnd))
                # ⭐ **先给目标窗"键盘焦点"，再投 `WM_PASTE`** —— 实测没有这一步，`WM_PASTE` 毫无效果
                #   （可见档也一样：输入框"发送键绿占比"恒 0.0）。
                #   ⛔ `SetFocus` 在这里只影响**那个线程内部的焦点**，**不改前台、不抢全局键盘焦点**
                #     （前台线程不是它 ⇒ 用户的击键仍然进他自己的窗口），所以不违反"不打扰用户"。
                try:
                    import ctypes as _ct2
                    _u2 = _ct2.windll.user32
                    _u2.SetFocus.argtypes = [_ct2.c_void_p]
                    _u2.SetFocus(_ct2.c_void_p(int(_tgt)))
                except Exception: # noqa: BLE001
                    pass
                _post(int(_tgt), WM_PASTE, 0, 0)
                time.sleep(max(0, int(restore_ms)) / 1000.0)   # ⛔ 必须等，见 docstring ③
            _r_ok, _r_why = getattr(_g, "result", (True, ""))
            return True, ("投给 %s" % _how) + ("" if _r_ok else "（⚠️ 剪贴板未还原：%s）" % str(_r_why)[:60])
        except Exception as e: # noqa: BLE001
            return False, "%s: %s" % (type(e).__name__, str(e)[:60])

    def paste(self, hwnd: int) -> tuple:
        """投递 `WM_PASTE`（配合剪贴板里的图片/文件）。**只在剪贴板已放好东西时调用。**"""
        if not hwnd:
            return False, "窗口句柄为空"
        self._wake(hwnd)
        _post(int(hwnd), WM_PASTE, 0, 0)
        return True, ""

    def keys(self, hwnd: int, vks, hold_ms: int = 5, wake: bool = True) -> tuple:
        """投递一组**组合键**（例：`[VK_CONTROL, VK_V]` ＝ Ctrl+V）。

        ⚠️ 实测：**发图要投给渲染子窗 `MMUIRenderSubWindowHW`** —— 投给主窗完全无效；
        且正因如此，"剪贴板 + 投递 Ctrl+V" 成了**纯后台发图**的通路（不动光标、不打扰你（可能短暂置前约 1~3 秒后自动还回））。

        ⚡ 两处提速（均已真机验证）：
          · **`hold_ms` 30 → 5**：`hold_ms` 是 down/up **之间各睡多久**，30 会让**单个键多睡 60ms**。
            真机 5 轮实测：`hold_ms` = 30 / 5 / 0 **三档「真发出」都是 5/5**
            （判据＝投字后发送键绿 0.444 → 回车后 0.000）⇒ 取 **5**（保守留一点余量，不取 0）。
          · **新增 `wake=False`**：同一条发送链里，`send_text()` 已经伪激活过一次，
            紧接着的 `keys()` **不必再激活一遍**（原来一次"打字 + 回车"白等**两遍**）。
            实测「回车不再唤醒」同样 **5/5 真发出**，而耗时 201.7ms → 113.9ms。
            ⛔ 默认仍是 `wake=True`（**独立调用 keys 时必须自唤醒**，那个前提不能省）；
               只有"同一链内刚刚 `send_text`/`paste` 过"的调用方，才显式传 `wake=False`。
        """
        if not hwnd:
            return False, "窗口句柄为空"
        vks = [int(v) for v in (vks or [])]
        if not vks:
            return False, "没有按键"
        if wake:
            self._wake(hwnd)
        for vk in vks:
            _post(int(hwnd), WM_KEYDOWN, vk, 0)
            time.sleep(max(0, hold_ms) / 1000.0)
        for vk in reversed(vks):
            _post(int(hwnd), WM_KEYUP, vk, 0)
            time.sleep(max(0, hold_ms) / 1000.0)
        return True, ""

    def drop_files(self, hwnd: int, paths, client_pt=None) -> tuple:
        """投递 `WM_DROPFILES`（把文件"拖"进目标窗口）——`WM_PASTE` 不吃文件时的备选。

        HDROP 结构：DROPFILES 头 + 双 NUL 结尾的路径列表（全路径、多路径用 \\0 分隔）。

        ⭐ **两处补强**（配合"最小化档能不能发图"的攻关）：
          ① **内存加 `GMEM_DDESHARE`** —— 旧写法只有 `GMEM_MOVEABLE|GMEM_ZEROINIT`，
             而 `WM_DROPFILES` 是**跨进程**把它交给微信（另一个进程）去 `GlobalLock`
             ⇒ 不加 `GMEM_DDESHARE` 时对方很可能锁不到、直接把消息丢掉。
          ② **`client_pt`** —— 旧写法 `pt` 恒为 `(0,0)`（= 窗口左上角 = 标题栏），
             用途不对。现在可指定**目标窗口客户区坐标**，让微信把它当成
             "拖到输入框上" ⇒ 加进**待发列表**，而不是拖到聊天区**直接发出去**。
        """
        if not hwnd:
            return False, "窗口句柄为空"
        if isinstance(paths, str):
            paths = [paths]
        paths = [str(p) for p in (paths or []) if p]
        if not paths:
            return False, "没有要拖入的文件"
        try:
            import ctypes as _ct
            from ctypes import wintypes as _wt

            class DROPFILES(_ct.Structure):
                _fields_ = [("pFiles", _wt.DWORD), ("pt", _wt.POINT),
                            ("fNC", _wt.BOOL), ("fWide", _wt.BOOL)]

            data = "\0".join(paths) + "\0\0"
            raw = data.encode("utf-16-le")
            hdr = DROPFILES()
            hdr.pFiles = _ct.sizeof(DROPFILES)
            hdr.fWide = True
            hdr.fNC = False                 # False ⇒ pt 是**客户区**坐标
            if client_pt:
                # 落点：让微信把它当成"拖到输入框上"⇒ 进待发列表，而不是直接发出去
                hdr.pt = _wt.POINT(int(client_pt[0]), int(client_pt[1]))
            buf = bytes(hdr) + raw
            GMEM_MOVEABLE, GMEM_ZEROINIT = 0x0002, 0x0040
            GMEM_DDESHARE = 0x2000          # ★ 跨进程共享（旧写法漏了，对方可能锁不到）
            _k32 = _ct.windll.kernel32
            # ⭐ 必须显式声明 argtypes/restype（照 clipboard.py 的既有正确写法）：
            #   只写 restype 而不写 argtypes 时，flags/size 会按默认 c_int 解释，
            #   返回值也可能是被截断的 32 位值（实测见过 668401672 = 0x27d70008），
            #   于是把"残缺句柄"投给 WM_DROPFILES，目标进程拿不到有效 HDROP。
            #   另：restype = c_void_p 时返回值本身就是 int（不是 c_void_p 对象），
            #   所以 _post 直接传 int(h) 即可，不要 ct.cast(h, c_void_p)
            #   （那会 ArgumentError: argument 1: TypeError，实测踩过）。
            _k32.GlobalAlloc.argtypes = [_ct.c_uint, _ct.c_size_t]
            _k32.GlobalAlloc.restype = _ct.c_void_p
            _k32.GlobalLock.argtypes = [_ct.c_void_p]
            _k32.GlobalLock.restype = _ct.c_void_p
            _k32.GlobalUnlock.argtypes = [_ct.c_void_p]
            _k32.GlobalFree.argtypes = [_ct.c_void_p]
            h = _k32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT | GMEM_DDESHARE, len(buf))
            if not h:
                return False, "GlobalAlloc 失败"
            p = _k32.GlobalLock(h)
            if not p:
                _k32.GlobalFree(h)
                return False, "GlobalLock 失败"
            _ct.memmove(p, buf, len(buf))
            _k32.GlobalUnlock(h)
            self._wake(hwnd)
            # h 已是 int（见上面 restype 的说明）⇒ 直接传；不要 cast。
            _post(int(hwnd), WM_DROPFILES, int(h or 0), 0)
            return True, ""
        except Exception as e:
            return False, "投递 WM_DROPFILES 失败：%s: %s" % (type(e).__name__, e)


def menu_new_windows(pid: int, before_ids) -> list:
    """返回微信进程里**新出现**的菜单/弹层顶层窗（类名 `Qt51514QWindowToolSaveBits`）。

    投递右键弹出来的菜单是**独立顶层窗**（不在主窗客户区里）⇒ 要点它得先找到它自己。
    调用方**先快照再差分**（这个类名也被表情面板用，不能只按类名认菜单）。
    """
    try:
        import win32gui
        import win32process
    except Exception:
        return []
    before = set(int(x) for x in (before_ids or []))
    out = []

    def cb(h, _):
        try:
            if int(h) in before:
                return
            if win32process.GetWindowThreadProcessId(h)[1] != int(pid):
                return
            if not win32gui.IsWindowVisible(h):
                return
            if win32gui.GetClassName(h) != MENU_CLASS:
                return
            out.append(int(h))
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass

    win32gui.EnumWindows(cb, None)
    return out


def menu_item_score(read: str, want: str) -> float:
    """菜单项匹配打分（0 / 0.8 / 1.0）——**容忍 OCR 噪声**。

    为什么要有它：
      微信自绘菜单项的 OCR 常被噪声字符污染，实测：
        「引用」→ **`@引`** · 「转发」→ `转发．．．` · 「删除」→ `U删除` · 「撤销」→ `軀制` · 「拍一拍」→ `拍一拍`
      原来的判据只做 `chat_ocr.matches()` 与"原文包含"，遇到 `@引` 就判不中 ⇒ **不点** ⇒ 换候选点重试
      （屏幕上就是**反复弹菜单**）⇒ `sender.py` 最后只能退回普通发送。
    ⇒ 先把**非汉字/字母/数字**的噪声剥掉再比；再允许**前缀/后缀包含**（`@引`→`引` 是「引用」的前缀）。
    返回：1.0＝剥噪后完全相等；0.8＝前缀/后缀包含；0.0＝不匹配（调用方拿 0.8 当阈值）。
    """
    try:
        a = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", str(read or ""))
        b = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", str(want or ""))
    except Exception:
        return 0.0
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a.startswith(b) or a.endswith(b) or b.startswith(a) or b.endswith(a):
        return 0.8
    return 0.0


def _menu_alive(hwnd: int) -> bool:
    """菜单窗是不是**还活着、且仍然可见**。

    ⛔ 为什么必须每一轮都查一次（而不是"抓到图就点"）：菜单是**短命浮层** ——
      用户一动鼠标、或我们自己上一轮的投递让它失去焦点，它就会自己关掉。
      此时若继续"截图 → 投递点击"，那一枪就落在**菜单原来的位置**
      （也就是菜单背后那个正常窗口上）⇒ 等于**在用户界面上乱点**。
    """
    try:
        import ctypes as _ct
        u = _ct.windll.user32
        h = int(hwnd or 0)
        if not h or not u.IsWindow(h):
            return False
        return bool(u.IsWindowVisible(h))
    except Exception:
        return False


def menu_click(hwnd_menu: int, item_text: str, zoom: int = 2, allow_top_fallback: bool = False,
               rounds: int = 4) -> tuple:
    """在菜单窗里找含 `item_text` 的项，并**投递左键**点它。返回 `(ok, 说明)`。

    ★ 三条容错（`rounds` 控制轮数，默认 4）—— 治的是**三类长得一样的失败**：
      ① **多轮重截**：菜单刚弹出那一刻可能还没画完 ⇒ 单次截图会读到残缺的项。
         每轮之间 `sleep(0.35)`，且**一命中立刻收手**（不是固定等满）。
      ② **`_menu_alive` 守卫**：菜单已关 ⇒ **不再截图、不再投递**
         （否则那一枪落在菜单背后那个窗口上）。
      ③ **「读到部分项但漏掉目标」也放大重读**：判据不是"读到几项"，而是"**目标命中了没有**"——
         因为"读到 3 项、恰好漏掉目标那一项"与"菜单里根本没有目标"在单次读数里**长得一模一样**，
         原先只在"一项都读不出"时才放大 ⇒ 会把前者误判成"菜单里没有"（见 `_menu_click_once`）。

    单轮的打分 / 放大 / 兜底细节在 `_menu_click_once` 里。
    """
    if not hwnd_menu:
        return False, "菜单窗为空"
    _n = max(1, int(rounds or 1))
    _why = "菜单窗为空"
    for _rnd in range(_n):
        if not _menu_alive(hwnd_menu):
            if _rnd == 0:
                return False, "菜单窗为空或已关闭"
            break
        _ok, _why = _menu_click_once(hwnd_menu, item_text, zoom=zoom,
                                     allow_top_fallback=allow_top_fallback)
        if _ok:
            return _ok, _why
        if _rnd < _n - 1:
            time.sleep(0.35)   # ① 给菜单一点时间画完，再读一轮
    return False, "%s（试了 %d 轮）" % (_why, _n)


def _menu_click_once(hwnd_menu: int, item_text: str, zoom: int = 2,
                     allow_top_fallback: bool = False) -> tuple:
    """**单轮**：在菜单窗里找含 `item_text` 的项，并**投递左键**点它。返回 `(ok, 说明)`。

    ⚠️ 实测两处坑：
      ① **OCR 会把「复制」读成「軀制」**（第一版按精确匹配 ⇒ 匹配不到、白跑一轮）⇒ 先用
         `chat_ocr.matches` 模糊匹配；
      ② 菜单是**独立顶层窗**、客户区坐标＝窗口矩形坐标（实测投递 (102,68) 命中）⇒ 直接投。

    ⛔ 现场抓到）：原来"匹配不到就**点最上面那一项**"（给「复制」兜底用的），
    但对「拍一拍」「引用」这类**必须精确命中**的菜单项，这一动作会**去点一个我们根本不知道是什么的项**
    —— 现场日志原文：`投递右键菜单：已投递点击菜单项（兜底取最上面一项「ek.」，落点 299,47）`，
    而且随后还被 `_verify_poke` 的假验证判成"已拍成功"。⇒ **兜底改成显式 opt-in（默认关）**：
    默认匹配不到就**不点**、如实返回失败；确实需要"复制"那种兜底的老调用点自己传 `allow_top_fallback=True`。
    """
    if not hwnd_menu:
        return False, "菜单窗为空"
    try:
        from PIL import ImageGrab
        import win32gui
        from . import chat_ocr as _co
    except Exception as e:
        return False, "缺依赖：%s" % e

    def _pick(_its):
        """在读到的一批项里挑最像 `item_text` 的那一项 ⇒ (落点, 说明, 得分)。

        得分口径沿用原文：`menu_item_score` 优先；≤0 时退回 `chat_ocr.matches` 的 0.8。
        """
        _b, _bs, _bh = None, 0.0, ""
        for (t, x, y, w, h) in _its:
            try:
                sc = menu_item_score(t, item_text)
                if sc <= 0:
                    try:
                        sc = 0.8 if _co.matches(str(t), str(item_text)) else 0.0
                    except Exception:
                        sc = 0.0
                if sc > _bs:
                    _bs, _b, _bh = sc, (int(x + w / 2), int(y + h / 2)), "匹配「%s」" % t
            except Exception:
                continue
        return _b, _bh, _bs

    try:
        L, T, R, B = win32gui.GetWindowRect(int(hwnd_menu))
        if R - L <= 0 or B - T <= 0:
            return False, "菜单窗几何无效"
        img = ImageGrab.grab(bbox=(int(L), int(T), int(R), int(B)), all_screens=True)
    except Exception as e:
        return False, "菜单截图失败：%s" % str(e)[:60]
    items = []
    try:
        items = _co.recognize(img, timeout=4.0) or []
    except Exception as e:
        return False, "菜单 OCR 失败：%s" % str(e)[:60]
    # ⭐ **"拍不上"的最后一环**：微信这个自绘菜单**原尺寸 OCR 读不出任何项**
    #   （离线实测同一张图：`ScreenOCR.recognize(原图)=[]`，**放大 2x/3x 后 = `[('拍一拍',...)]`**）。
    #   原代码只读原尺寸 ⇒ 永远 `items=[]` ⇒ 永远"判据不可用，不点" ⇒ 表现为"菜单弹了但拍不上"。
    #   ⇒ 原尺寸读不出就**放大再读一次**，坐标按比例还原（与项目里 `ocr_zoomed` 同一套经验：
    #     微信 4.x 的小字号/自绘文字，放大后才识别得出来）。
    # ★ 放大的条件是"**目标还没命中**"，不是"一项都没读到" ——
    #   "读到几项、恰好漏掉目标那一项"与"菜单里根本没有目标"在单次读数里**长得一样**，
    #   只看"读没读到"会把前者判成"菜单里没有"（这是"菜单弹了却拍不上"的残留成因）。
    if _pick(items)[2] < 0.8:
        _z = max(3, int(zoom or 2))
        try:
            from PIL import Image as _PILImage # ⚠️ 本模块 PIL 是懒加载的，别用裸 `Image`
            _big = img.resize((img.width * _z, img.height * _z), _PILImage.LANCZOS)
            _it2 = _co.recognize(_big, timeout=6.0) or []
            _items2 = [(t, int(x) // _z, int(y) // _z, int(w) // _z, int(h) // _z)
                       for t, x, y, w, h in _it2]
            # 只有"放大版**更接近目标**"才替换 —— 放大也会引入误读，别把好的一批换掉。
            if _items2 and _pick(_items2)[2] > _pick(items)[2]:
                log.info("菜单 OCR：原尺寸没命中「%s」⇒ **放大 %dx 后更接近（读到 %d 项）**",
                         item_text, _z, len(_items2))
                items = _items2
        except Exception as e:
            log.info("菜单 OCR 放大重读失败：%s", e)
    if not items:
        return False, "菜单 OCR 读不到任何项（原尺寸与放大后都读不出，判据不可用，不点）"
    # 🔴 （悬停在"引用"那一栏了。你是不是只把工具栏往上调了
    #   一点点？」）：**把菜单里读到的每一项都写进日志**（文本 + 中心 y），这样"菜单里到底有什么、我们点的是
    #   哪一项、纵向差多少"一眼可见，不用再靠猜。自绘菜单的项是等高的，y 就是判"点没点偏"的唯一依据。
    try:
        _listed = sorted(items, key=lambda it: it[2])
        log.info("菜单 OCR 明细（%d 项）：%s", len(_listed),
                 " ｜ ".join("%s@y=%d" % (str(it[0])[:8], int(it[2] + it[4] / 2)) for it in _listed[:10]))
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    hit, how = None, ""
    # 打分逻辑已收进 `_pick`：放大前后**用同一套口径**（否则两处打分容易漂移）
    _best, _best_how, _best_sc = _pick(items)
    if _best is not None and _best_sc >= 0.8:
        hit, how = _best, _best_how
    if hit is None:
        if not allow_top_fallback:
            return False, ("菜单里没找到「%s」（读到的是：%s）——**不点**（防点到不知道是什么的项）"
                           % (item_text, "、".join(str(it[0])[:8] for it in items[:5])))
        _t, _x, _y, _w, _h = sorted(items, key=lambda it: it[2])[0]
        hit, how = (int(_x + _w / 2), int(_y + _h / 2)), "兜底取最上面一项「%s」" % _t
    mx, my = hit
    for msg, wp in ((WM_MOUSEMOVE, 0), (WM_LBUTTONDOWN, 1), (WM_LBUTTONUP, 0)):
        _post(int(hwnd_menu), msg, wp, pack_lparam(mx, my))
        time.sleep(0.16)
    return True, "已投递点击菜单项（%s，落点 %d,%d）" % (how, mx, my)


class RealInputBackend(InputBackend):
    """L0：真鼠标路径（`ui_adapt.click`）。**用完必须还原光标**——`ui_adapt.heal_input()` 负责。"""
    name = LEVEL_REAL
    touches_cursor = True
    level = "L0"

    def __init__(self, gui=None):
        self._gui = gui

    def bind(self, gui) -> None:
        self._gui = gui

    def click(self, hwnd: int, screen_pt, right: bool = False) -> tuple:
        try:
            from . import ui_adapt
            gui = self._gui
            if gui is None:
                return False, "真实后端未绑定 gui"
            # ui_adapt.click 收「渲染相对」坐标，这里把屏幕坐标换回去
            rel_x = int(screen_pt[0]) - int(gui.origin_x)
            rel_y = int(screen_pt[1]) - int(gui.origin_y)
            return ui_adapt.click(gui, rel_x, rel_y, right=right)
        except Exception as e:
            return False, str(e)

    def send_text(self, hwnd: int, text: str) -> tuple:
        return False, "真实后端不实现投递打字（请用 MessageBackend 或平台的 paste 路径）"


def select_backend(cfg: dict | None = None, gui=None) -> InputBackend:
    """按 `config.input.backend` 选档：auto（默认，有主窗就用投递）/ message / real。

    `config.input.press_ms` 与 `config.input.activate` 也在这里生效：
    这两个键以前是**死键**——`MessageBackend(press_ms=60, activate=True)` 把默认值写死在
    构造函数里，全仓没有一处读配置 ⇒ 用户在 config.json 里改它们完全没用。现在从这里读进去，
    默认值仍是 60 / True（不改默认行为）。
    """
    c = cfg if cfg is not None else get_config()
    try: # 活动信号：每次取后端＝马上要做一次输入动作（窗口借用会因此延后归还）
        from . import window_borrow as _wb
        _wb.touch()
    except Exception:
        pass
    icfg = c.get("input") or {}
    want = str(icfg.get("backend", "auto")).lower()
    try:
        _press = int(icfg.get("press_ms", 60))
    except Exception:
        _press = 60
    _press = max(1, min(2000, _press)) # 夹在合理区间：1ms~2s
    _activate = bool(icfg.get("activate", True))
    if want == LEVEL_REAL:
        return RealInputBackend(gui)
    if want == LEVEL_MESSAGE:
        return MessageBackend(press_ms=_press, activate=_activate)
    # auto：有微信主窗（**不论是否可见/最小化**）就走投递；一个窗口都找不到才退回真鼠标。
    # ⚠️ 绝不能因为"窗口不可见"就退回 L0 —— 那会在用户毫无察觉时动他的光标。
    return (MessageBackend(press_ms=_press, activate=_activate)
            if find_main_window() else RealInputBackend(gui))


_BACKEND: InputBackend | None = None


def active(gui=None, refresh: bool = False) -> InputBackend:
    """取当前生效的后端（进程内缓存；控制台改配置后传 refresh=True 重选）。"""
    global _BACKEND
    try: # 活动信号（与 select_backend 同一目的：有输入动作就别急着还窗口）
        from . import window_borrow as _wb
        _wb.touch()
    except Exception:
        pass
    if _BACKEND is None or refresh or isinstance(_BACKEND, RealInputBackend):
        _BACKEND = select_backend(gui=gui)
        if isinstance(_BACKEND, RealInputBackend):
            _BACKEND.bind(gui) if gui is not None else None
    return _BACKEND


def status(gui=None) -> dict:
    """给控制台/日志用的现状快照（含"当前用的是哪一档"，AGENTS §2.1 第 4 条要求）。"""
    b = active(gui)
    main = find_main_window()
    return {
        "backend": b.name,
        "level": getattr(b, "level", "?"),
        "touches_cursor": b.touches_cursor,
        "dpi_mode": DPI_MODE,
        "wechat_main_hwnd": main,
        "wechat_pid": _pid_of(main) if main else 0,
        "note": "投递档不动光标、不打扰你（可能短暂置前约 1~3 秒后自动还回）；真实档用完由 ui_adapt.heal_input() 还原光标",
    }
