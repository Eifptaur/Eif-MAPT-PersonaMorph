# -*- coding: utf-8 -*-
"""输入后端（最高目标「全程后台、不抢鼠标」的落地模块）：**投递优先，真实输入兜底**。

为什么要有这一层：项目里原先到处直接 `mouse_event`（`wechat.py` 21 处 / `wechat_ui.py` 7 处），
"不动鼠标"根本无从保证。本模块把 UI 动作收成两个原语（`click` / `send_text`），
按 L5 → L0 顺序取用，并把「当前用的是哪一档」暴露给控制台。

两档实现（判据全部来自本项目实测，不是外推）：
  · `MessageBackend`（L5，默认）：`PostMessage` 发 `WM_ACTIVATE`/`WM_NCACTIVATE` 伪激活 + 鼠标/键盘消息。
    实测（2026-09-13）：①投递 `WM_CHAR` + 投递点「发送」按钮 ⇒ **3/3 DB 回读命中**，光标与前台未变；
    ②投递点笑脸 ⇒ 表情面板弹出（新顶层窗 `Qt51514QWindowToolSaveBits`）；③投递点面板里的收藏格
    ⇒ **DB 回读 `type=动画表情`**（`local_id 550→551`）⇒ 发送与表情两条链都能纯后台走完。
  · `RealInputBackend`（L0，兜底）：走 `ui_adapt.click` 的真鼠标路径；用完 `heal_input()` 恢复光标。

三条硬纪律（写进代码而不是只写文档）：
  1. **本模块 import 时就锁 PerMonitorV2**——实测同一进程里不锁 DPI 时 `GetWindowRect` 给的是
     虚拟坐标（面板 514×514），而微信是物理坐标（771×771，差 1.5 倍）⇒ 投递坐标落到窗口外、点了等于没点。
  2. **不置顶、不 `SetForegroundWindow`、不动光标**：投递就是投递，绝不靠"把窗口怼到用户脸上"换成功率。
  3. **投递点击要发给"真正接收该点击的那个窗口"**：主窗用于点笑脸，面板弹出后要发给面板窗
     （已实测：面板 0 个子窗，`WindowFromPoint` 归属就是面板本身）。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from .config import get_config

# ── ① DPI：import 即锁，任何坐标运算之前 ──────────────────────────────────
_user32 = ctypes.windll.user32


def _lock_dpi() -> str:
    """锁 DPI 上下文 —— **必须检查返回值**：这些 API 失败时只返回 0、不抛异常
    （2026-09-13 实测：老写法"以为锁上了"，实际进程仍读虚拟坐标）。"""
    try:
        if _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):   # PerMonitorV2
            return "PerMonitorV2"
    except Exception:
        pass
    try:
        if int(ctypes.windll.shcore.SetProcessDpiAwareness(2)) == 0:     # S_OK
            return "shcore/2"
    except Exception:
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
# 右键（2026-09-16 起可用：投递右键有效，但**必须投主窗**，见 `MessageBackend.click`）
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
# 微信的菜单/弹层顶层窗类名（投递右键弹出的菜单就是它；表情面板也用同一个类名 ⇒ 必须差分）
MENU_CLASS = "Qt51514QWindowToolSaveBits"
WM_CHAR, WM_KEYDOWN, WM_KEYUP = 0x0102, 0x0100, 0x0101
WM_PASTE, WM_DROPFILES, WM_MOUSEWHEEL = 0x0302, 0x0233, 0x020A
VK_CONTROL, VK_V, VK_RETURN = 0x11, 0x56, 0x0D

MAIN_CLASS = "Qt51514QWindowIcon"        # 微信主窗
PANEL_CLASS = "Qt51514QWindowToolSaveBits"   # 表情面板等弹层
LEVEL_MESSAGE = "message"                # L5
LEVEL_REAL = "real"                      # L0

_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_ssize_t, ctypes.c_ssize_t]
_user32.PostMessageW.restype = wintypes.BOOL
_post = _user32.PostMessageW             # 单点可替换，供自检做无微信单测
# ⚠️ 上面两行必须声明 argtypes/restype：不声明时 64 位下的 WPARAM/LPARAM 会被当 32 位，
#    传 HDROP 句柄（64 位整数）直接 `OverflowError: int too long to convert`（2026-09-13 实测踩过）。
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


RENDER_CHILD = "MMUIRenderSubWindowHW"     # 主窗特有的渲染子窗


def pick_main_window(cands) -> int:
    """从候选里挑主窗（**纯函数，可单测**）。

    cands: [(hwnd, has_render_child, area, visible)]
    规则：①优先"带渲染子窗"的（朋友圈编辑窗也同类名，但它没有渲染子窗）②同分取面积最大
    ③**不看可见性** —— 窗口被隐藏/最小化时也要认得出主窗，否则 auto 会静默降级成真鼠标档
    （2026-09-13 实测就是这个降级：窗口一 SW_HIDE，投递档就悄悄换成 L0 了）。
    """
    best, best_key = 0, None
    for hwnd, has_child, area, _vis in (cands or []):
        key = (1 if has_child else 0, int(area or 0))
        if best_key is None or key > best_key:
            best, best_key = int(hwnd), key
    return best


def find_render_child(main_hwnd: int) -> int:
    """主窗里的**渲染子窗**（`MMUIRenderSubWindowHW`）：Qt 自绘界面真正绘制/接收鼠标的那一层。

    为什么需要：键盘、点「发送」按钮投给**主窗**是有效的；但会话列表行的点击可能归这一层处理
    （2026-09-13 实测：投主窗点了会话行之后，绿底高亮没有转到目标行 ⇒ 怀疑投错了窗口）。
    """
    if not main_hwnd:
        return 0
    import win32gui
    hits = []

    def cb(h, _l):
        try:
            cls = win32gui.GetClassName(h)
        except Exception:
            return True
        if "MMUIRender" in cls or "RenderSubWindow" in cls:
            hits.append(int(h))
        return True

    try:
        win32gui.EnumChildWindows(int(main_hwnd), cb, None)
    except Exception:
        return 0
    return hits[0] if hits else 0


def find_main_window() -> int:
    """微信**主窗**（投递键盘消息、点笑脸都发它）。

    ⚠️ 不能用 `FindWindow(类名)`：表情面板之外的**朋友圈纯文字编辑窗也是
    `Qt51514QWindowIcon`**，`FindWindow` 返回的是第一个命中的那个（2026-09-13 实测踩到——
    投递全打到编辑窗上，发送自然失败）。
    ⚠️ **也不能只认"可见"的窗口**：主窗被收进托盘/隐藏时，按可见性过滤会返回 0，
    于是 `auto` 档悄悄降级成真鼠标档（会动用户光标）——这正是最高目标里"不许悄悄降级"的场景。
    """
    cands = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _l):
        if _class_of(h) == MAIN_CLASS:
            r = window_rect(h)
            area = max(0, (r[2] - r[0])) * max(0, (r[3] - r[1]))
            cands.append((int(h), RENDER_CHILD in _child_classes(h), area,
                          bool(_user32.IsWindowVisible(h))))
        return True

    _user32.EnumWindows(CB(cb), 0)
    return pick_main_window(cands)


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
        """伪激活：让目标自认为被激活（不等于改前台窗口）。"""
        if not self.activate:
            return
        _post(int(hwnd), WM_ACTIVATE, 1, 0)
        _post(int(hwnd), WM_NCACTIVATE, 1, 0)
        time.sleep(0.08)

    def click(self, hwnd: int, screen_pt, right: bool = False, hover_ms: int = 0, press_ms: int = None) -> tuple:
        """投递点击。`hover_ms`＝按下前**先悬停**多久（默认 0 ⇒ 沿用 50ms 落点稳定），
        `press_ms`＝按住多久（默认 self.press_ms）。

        ⚠️ 微信**会话列表行**必须要"慢节奏"才认（2026-09-13 A/B 实测，判据＝高亮行 y 有没有移到我点的那一行）：
          ① 只投主窗 ✗；② 投渲染子窗但用快节奏（悬停 50ms + 按住 60ms）**✗ 高亮不动**；
          ③ 投渲染子窗 + **悬停 300ms + 按住 150ms** ✓ 高亮立刻跳到目标行。⇒ 会话行点击一律用慢节奏。
        """
        if not hwnd:
            return False, "窗口句柄为空"
        # ⚠️ 2026-09-16 实测（两靶点各有真实右键阳性对照，见 `_scratch/rclick_avatar.py`）：
        #   **右键投渲染子窗不弹菜单（新顶层窗 0 / 像素差 0.000），投「主窗」才弹**
        #   （弹出 `Qt51514QWindowToolSaveBits`，像素差 0.026~0.035）；
        #   而**左键**点会话行恰恰相反、必须投渲染子窗。⇒ 右键一律**自动换成主窗**，
        #   调用方不用关心这个差异（这里替换掉了原先"右键直接拒绝"的写法）。
        tgt = int(hwnd)
        if right:
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

        为什么带 `times` 和间隔：用户 2026-09-13 反馈「你滚得太不顺滑了，**一下一下地滚，导致没有看到**」
        －－单发一格、中间不歇，自绘列表容易处理不过来或只滚一点点；连续多格 + 每格 60ms 才像人滚。
        ⚠️ `WM_MOUSEWHEEL` 的 lParam 是**屏幕坐标**（与 `WM_LBUTTONDOWN` 用客户区坐标不同）。
        """
        if not hwnd:
            return False, "窗口句柄为空"
        try:
            x, y = int(screen_pt[0]), int(screen_pt[1])
        except Exception:
            return False, "滚轮落点无效"
        self._wake(hwnd)
        _post(int(hwnd), WM_MOUSEMOVE, 0, pack_lparam(*to_client(hwnd, (x, y))))
        n = max(1, int(times))
        for _ in range(n):
            _post(int(hwnd), WM_MOUSEWHEEL, ((int(delta) & 0xFFFF) << 16), pack_lparam(x, y))
            time.sleep(max(0, int(gap_ms)) / 1000.0)
        return True, ""

    def send_text(self, hwnd: int, text: str) -> tuple:
        """逐字投递 `WM_CHAR`。实测：不点输入框、不预设焦点也能进框（提交由调用方点发送按钮）。"""
        if not hwnd:
            return False, "窗口句柄为空"
        if not text:
            return False, "文本为空"
        for ch in str(text):
            _post(int(hwnd), WM_CHAR, ord(ch), 1)
        return True, ""

    def paste(self, hwnd: int) -> tuple:
        """投递 `WM_PASTE`（配合剪贴板里的图片/文件）。**只在剪贴板已放好东西时调用。**"""
        if not hwnd:
            return False, "窗口句柄为空"
        self._wake(hwnd)
        _post(int(hwnd), WM_PASTE, 0, 0)
        return True, ""

    def keys(self, hwnd: int, vks, hold_ms: int = 30) -> tuple:
        """投递一组**组合键**（例：`[VK_CONTROL, VK_V]` ＝ Ctrl+V）。

        ⚠️ 实测（2026-09-13）：**发图要投给渲染子窗 `MMUIRenderSubWindowHW`** —— 投给主窗完全无效；
        且正因如此，"剪贴板 + 投递 Ctrl+V" 成了**纯后台发图**的通路（不动光标、不打扰你（可能短暂置前约 1~3 秒后自动还回））。
        """
        if not hwnd:
            return False, "窗口句柄为空"
        vks = [int(v) for v in (vks or [])]
        if not vks:
            return False, "没有按键"
        self._wake(hwnd)
        for vk in vks:
            _post(int(hwnd), WM_KEYDOWN, vk, 0)
            time.sleep(max(0, hold_ms) / 1000.0)
        for vk in reversed(vks):
            _post(int(hwnd), WM_KEYUP, vk, 0)
            time.sleep(max(0, hold_ms) / 1000.0)
        return True, ""

    def drop_files(self, hwnd: int, paths) -> tuple:
        """投递 `WM_DROPFILES`（把文件"拖"进目标窗口）——`WM_PASTE` 不吃文件时的备选。

        HDROP 结构：DROPFILES 头 + 双 NUL 结尾的路径列表（全路径、多路径用 \\0 分隔）。
        分配在**本进程**的全局内存里即可（消息是异步投递的，调用方需保证内存活到消息被处理完）。
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
            buf = bytes(hdr) + raw
            GMEM_MOVEABLE, GMEM_ZEROINIT = 0x0002, 0x0040
            _k32 = _ct.windll.kernel32
            _k32.GlobalAlloc.restype = _wt.HGLOBAL
            h = _k32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, len(buf))
            if not h:
                return False, "GlobalAlloc 失败"
            p = _k32.GlobalLock(h)
            if not p:
                _k32.GlobalFree(h)
                return False, "GlobalLock 失败"
            _ct.memmove(p, buf, len(buf))
            _k32.GlobalUnlock(h)
            self._wake(hwnd)
            _post(int(hwnd), WM_DROPFILES, int(h), 0)
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
        except Exception:
            pass

    win32gui.EnumWindows(cb, None)
    return out


def menu_click(hwnd_menu: int, item_text: str, zoom: int = 2, allow_top_fallback: bool = False) -> tuple:
    """在菜单窗里找含 `item_text` 的项，并**投递左键**点它。返回 `(ok, 说明)`。

    ⚠️ 实测两处坑（2026-09-16）：
      ① **OCR 会把「复制」读成「軀制」**（第一版按精确匹配 ⇒ 匹配不到、白跑一轮）⇒ 先用
         `chat_ocr.matches` 模糊匹配；
      ② 菜单是**独立顶层窗**、客户区坐标＝窗口矩形坐标（实测投递 (102,68) 命中）⇒ 直接投。

    ⛔ 2026-09-18 改（真缺陷，现场抓到）：原来"匹配不到就**点最上面那一项**"（给「复制」兜底用的），
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
    if not items:
        return False, "菜单 OCR 读不到任何项（判据不可用，不点）"
    hit, how = None, ""
    for (t, x, y, w, h) in items:
        try:
            if _co.matches(str(t), str(item_text)) or str(item_text) in str(t):
                hit, how = (int(x + w / 2), int(y + h / 2)), "模糊匹配「%s」" % t
                break
        except Exception:
            continue
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

    `config.input.press_ms` 与 `config.input.activate` 也在这里生效（2026-09-15 接线）：
    这两个键以前是**死键**——`MessageBackend(press_ms=60, activate=True)` 把默认值写死在
    构造函数里，全仓没有一处读配置 ⇒ 用户在 config.json 里改它们完全没用。现在从这里读进去，
    默认值仍是 60 / True（不改默认行为）。
    """
    c = cfg if cfg is not None else get_config()
    try:                       # 活动信号：每次取后端＝马上要做一次输入动作（窗口借用会因此延后归还）
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
    _press = max(1, min(2000, _press))                      # 夹在合理区间：1ms~2s
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
    try:                       # 活动信号（与 select_backend 同一目的：有输入动作就别急着还窗口）
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
