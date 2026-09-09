# -*- coding: utf-8 -*-
"""界面适配层：让 wx-agent 的屏幕操作在各种机器/DPI/显示器下都能正常点击。

背景：不同电脑的差异点
  · Windows 显示缩放（100/125/150/200%…）：GetWindowRect/截图/OCR 的坐标空间
    和鼠标输入（SetCursorPos/mouse_event）的空间在不同 DPI 感知下可能不一致，
    差分 1.5x 就会全部点偏。
  · 多显示器（尤其镜像/不同缩放）：命中测试可能落到另一个显示器（桌面/镜像层）。
  · 系统叠加层：Windows 触控键盘/手写输入（TabTip / ShellHandwritingCanvas 全屏
    置顶分层窗口）打开时会截走全部点击——微信还能看见，但怎么点都没反应。
  · 普通遮挡窗口（浏览器等）：点击会落到遮挡层（wechatauto 的 _minimize_blockers
    只处理部分窗口，需要更彻底的清理）。

本模块提供：
  detect_scale()          自动检测显示缩放（EnumDisplaySettings.dmLogPixels，不依赖 DPI 感知）
  to_click(x, y)          把「截图/OCR 空间」坐标换算成「鼠标空间」坐标（按 config.ui.coord_scale）
  find_cover(x, y)        查询 (x,y) 当前由哪个顶层窗口接收点击
  ensure_point(x, y)      确保点击点属于微信窗口（清理遮挡层/叠加层/重试），返回 (ok, 诊断信息)
  prepare_screen()        点击操作前的整备：清系统叠加层 + 最小化遮挡窗口
  click(gui, x, y, right) 统一点击入口：换算 + 归属校验 + wx_click，带 DPI 缩放

config.json 里的相关设置（均在 Web 控制台「界面适配」卡片可改）：
  ui.coord_scale     "auto"（默认，自动检测）或 数字（1.0/1.25/1.5/2.0…）
  ui.clean_overlays  点击前是否自动清理系统叠加层与遮挡窗口（默认 true）
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes

from .config import get_config

_user32 = ctypes.windll.user32

# 系统叠层窗口的类名（全屏置顶、吃点击，需要清理）
_OVERLAY_CLASSES = (
    "ShellHandwritingCanvas",   # Windows 手写输入画布（TabTip 宿主）
    "Windows.UI.Core.CoreWindow",  # Windows 输入体验
)

# 永远不动的窗口类
_SKIP_CLASSES = ("Progman", "WorkerW", "Shell_TrayWnd", "MSCTFIME UI", "IME",
                 "kugou_ui")

_cache = {"scale": None}


def _config_ui() -> dict:
    return dict(get_config().get("ui") or {})


def detect_scale() -> float:
    """检测显示缩放系数（鼠标空间 / 截图空间）。

    原理：EnumDisplaySettings 的物理分辨率（dmPelsWidth，不被进程 DPI 感知
    虚拟化）÷ 进程当前感知的屏幕宽度（GetSystemMetrics）。
      · DPI-AWARE 进程（wechatauto 引入 winsdk 后）：感知 = 物理 → 1.0，无需换算；
      · DPI-UNAWARE 进程：感知是虚拟化后的逻辑宽度（如 2560 物理 → 1707 感知）→ 1.5；
    这样无论进程 DPI 感知状态、无论机器缩放多少，都得到正确的换算系数。
    """
    try:
        class DEVMODE(ctypes.Structure):
            _fields_ = [("dmDeviceName", ctypes.c_wchar * 32),
                        ("dmSpecVersion", ctypes.c_ushort),
                        ("dmDriverVersion", ctypes.c_ushort),
                        ("dmSize", ctypes.c_ushort),
                        ("dmDriverExtra", ctypes.c_ushort),
                        ("dmFields", ctypes.c_ulong),
                        ("dmPosition", wintypes.POINT),
                        ("dmDisplayOrientation", ctypes.c_ulong),
                        ("dmDisplayFixedOutput", ctypes.c_ulong),
                        ("dmColor", ctypes.c_short),
                        ("dmDuplex", ctypes.c_short),
                        ("dmYResolution", ctypes.c_short),
                        ("dmTTOption", ctypes.c_short),
                        ("dmCollate", ctypes.c_short),
                        ("dmFormName", ctypes.c_wchar * 32),
                        ("dmLogPixels", ctypes.c_ushort),
                        ("dmBitsPerPel", ctypes.c_ulong),
                        ("dmPelsWidth", ctypes.c_ulong),
                        ("dmPelsHeight", ctypes.c_ulong)]
        dm = DEVMODE()
        dm.dmSize = ctypes.sizeof(DEVMODE)
        if _user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm)):
            phys_w = int(dm.dmPelsWidth or 0)
            perceive_w = int(_user32.GetSystemMetrics(0) or 0)
            if phys_w > 0 and perceive_w > 0:
                return round(phys_w / perceive_w, 3)
    except Exception:
        pass
    return 1.0


def coord_scale(override=None) -> float:
    """当前坐标缩放：config.ui.coord_scale 优先，否则自动检测。"""
    if override is not None:
        try:
            return max(0.5, min(4.0, float(override)))
        except (TypeError, ValueError):
            pass
    cfg = _config_ui().get("coord_scale", "auto")
    if isinstance(cfg, (int, float)) and not isinstance(cfg, bool) and float(cfg) > 0:
        return max(0.5, min(4.0, float(cfg)))
    return detect_scale()


def to_click(x: float | int, y: float | int, scale=None) -> tuple:
    """把「截图/OCR 空间」坐标换算为「鼠标空间」坐标。"""
    s = coord_scale(scale)
    return int(round(float(x) / s)), int(round(float(y) / s))


def _window_info(hwnd: int) -> tuple:
    """(class, title, pid, rect)”"""
    try:
        cls = ctypes.create_unicode_buffer(256)
        title = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, cls, 256)
        _user32.GetWindowTextW(hwnd, title, 256)
        pid = ctypes.c_ulong()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        r = wintypes.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(r))
        return (cls.value, title.value, pid.value, (r.left, r.top, r.right, r.bottom))
    except Exception:
        return ("", "", 0, (0, 0, 0, 0))


def find_cover(x: int, y: int, wechat_hwnds: tuple = ()):
    """查询 (x, y) 处当前接点击的顶层窗口；返回 (hwnd, cls, title, pid) 或 None。"""
    try:
        h = _user32.WindowFromPoint(int(x), int(y))
        if not h:
            return None
        root = _user32.GetAncestor(h, 2)  # GA_ROOT
        if root and wechat_hwnds and root in wechat_hwnds:
            return None
        cls, title, pid, rect = _window_info(root or h)
        if cls in _SKIP_CLASSES:
            return None
        # 微信 UI 弹出层（表情面板/右键菜单等 WinUI Popup）不视为遮挡——允许点击穿透
        if "SiteBridge" in (cls or "") or cls.startswith("PopupWindow"):
            return None
        return (root or h, cls, title, pid, rect)
    except Exception:
        return None


def dismiss_overlays(wechat_hwnds: tuple = ()) -> list:
    """清理全屏置顶的系统输入叠加层（手写画布/输入体验）与占位顶层窗。

    返回被处理的窗口描述列表（供日志）。
    """
    handled = []
    cleaned = 0

    def _walk_top_level():
        out = []
        CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def cb(h, l):
            if not _user32.IsWindowVisible(h):
                return True
            cls, title, pid, rect = _window_info(h)
            out.append((h, cls, title, pid, rect))
            return True

        ref = CB(cb)
        _user32.EnumWindows(ref, 0)
        return out

    try:
        wins = _walk_top_level()
    except Exception:
        return handled

    # 1) 系统输入叠加层：先 WM_CLOSE + 隐藏；TabTip 宿主再 taskkill 兜底
    for h, cls, title, pid, rect in wins:
        if cls in _OVERLAY_CLASSES:
            handled.append(("overlay", cls, title[:50], pid))
            try:
                _user32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE
            except Exception:
                pass
            try:
                _user32.ShowWindow(h, 0)  # SW_HIDE
            except Exception:
                pass
            cleaned += 1
            time.sleep(0.2)
    if cleaned:
        time.sleep(0.6)
        # 注意：不 taskkill TabTip/TextInputHost——那是触控键盘宿主，
        # 强杀会让触屏机器的指针/拖拽状态异常（桌面图标拖不动的一类根因）；
        # 只隐藏画布窗口本身（Win 输入体验会自动回收）。

    if not get_config().get("ui", {}).get("clean_overlays", True):
        return handled

    # 2) 与微信窗口重叠的普通顶层窗口（浏览器等）：最小化
    try:
        wx_pid = 0
        for h, cls, title, pid, rect in wins:
            if wechat_hwnds and h in wechat_hwnds:
                wx_pid = pid
                break
        for h, cls, title, pid, rect in wins:
            if cls in _SKIP_CLASSES or cls in _OVERLAY_CLASSES:
                continue
            if pid == wx_pid:
                continue  # 微信自身不动
            if pid in (0,) or not title.strip():
                continue  # 系统无标题窗口（如桌面相关的空壳）不动
            # 浏览器窗口绝不碰（用户正在用浏览器/控制台；最小化会误以为被关掉）
            _cls_l = (cls or "").lower()
            _tit_l = (title or "").lower()
            if ("chrome" in _cls_l or "msedge" in _cls_l or "firefox" in _cls_l
                    or "qqbrowser" in _cls_l or "360se" in _cls_l or "iexplore" in _cls_l
                    or "chrome" in _tit_l or "edge" in _tit_l or "firefox" in _tit_l
                    or "qq浏览器" in _tit_l or "360" in _tit_l):
                continue
            # 检查与微信窗口是否重叠
            overlap = False
            for wrect in (w[4] for w in wins if w[0] in wechat_hwnds):
                if (rect[2] > wrect[0] and rect[0] < wrect[2]
                        and rect[3] > wrect[1] and rect[1] < wrect[3]):
                    overlap = True
                    break
            if overlap:
                try:
                    # 置于下层（HWND_BOTTOM）而不是最小化：最小化会把用户窗口"收起"（体验突兀）
                    _user32.SetWindowPos(h, -1, 0, 0, 0, 0, 0x0001 | 0x0002)
                    handled.append(("window", cls, title[:50], pid))
                except Exception:
                    pass
    except Exception:
        pass
    return handled


def ensure_point(x: int, y: int, wechat_hwnds: tuple = (), retries: int = 3, gui=None) -> tuple:
    """确保点击点 (x, y)（鼠标空间）当前由微信窗口接收。

    返回 (ok, 描述)：ok=True 可直接点击；False 时描述里写明挡路窗口。
    每轮失败都会先「把微信置前」再重试（浏览器/其它窗口挡住时自动拯救，
    只有重试后仍被挡才报错并把原因说清楚）。
    """
    cover = None
    for i in range(max(1, retries)):
        cover = find_cover(x, y, wechat_hwnds)
        if cover is None:
            return True, "点击点属于微信窗口"
        # 有遮挡才拯救：把【微信主窗】置前（不用 wechatauto bring_to_front——它可能顶起渲染子窗盖住面板）
        try:
            if gui is not None and hasattr(gui, "main_hwnd"):
                _user32.SetForegroundWindow(int(gui.main_hwnd))
            elif wechat_hwnds:
                _user32.SetForegroundWindow(int(wechat_hwnds[0]))
            time.sleep(0.25)
        except Exception:
            pass
        if i == 0:
            dismiss_overlays(wechat_hwnds)
            time.sleep(0.3)
    return False, "点击坐标被「%s / %s」窗口遮挡（pid=%d，区域 %s）——已自动尝试把微信置前仍失败，请切到微信窗口或关闭遮挡窗口后重试" % (
        cover[1] or "?", cover[2][:60] or "?", cover[3], cover[4])


def _restore_wechat_window(gui) -> bool:
    """按进程枚举找「微信」主窗并恢复（窗口最小化/隐藏/移出屏时自愈）。"""
    try:
        pid = ctypes.c_ulong()
        _user32.GetWindowThreadProcessId(int(gui.main_hwnd), ctypes.byref(pid))
        wx_pid = pid.value

        def _walk():
            out = []
            CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

            def cb(h, l):
                p2 = ctypes.c_ulong()
                _user32.GetWindowThreadProcessId(h, ctypes.byref(p2))
                if p2.value == wx_pid:
                    t = ctypes.create_unicode_buffer(256)
                    _user32.GetWindowTextW(h, t, 256)
                    if "微信" in t.value:
                        out.append(h)
                        return False
                return True

            ref = CB(cb)
            _user32.EnumWindows(ref, 0)
            return out

        wins = _walk()
        if wins:
            h = wins[0]
            _user32.ShowWindow(int(h), 9)
            time.sleep(0.4)
            _user32.SetForegroundWindow(int(h))
            time.sleep(0.3)
            gui._update_render_rect()
            return True
    except Exception:
        pass
    return False


def _force_geometry(gui) -> None:
    """强制微信主窗恒定位+大小（用户方案：相对位置恒定，仅按 DPI 换算坐标）。
    拖动窗口后下一次点击前自动拉回（ui.lock_window_pos 可关）。"""
    try:
        _cfg = __import__("agent.config", fromlist=["get_config"]).get_config()
        if (_cfg.get("ui") or {}).get("lock_window_pos", True) is False:
            return
        hwnd = getattr(gui, "main_hwnd", 0)
        if not hwnd:
            return
        try:
            _scale = max(1.0, _user32.GetDpiForWindow(hwnd) / 96.0)
        except Exception:
            _scale = 1.25
        # 目标尺寸按 DPI 换算，但限制在屏幕内（小分辨率屏幕不超出；避免窗口出屏）
        try:
            _sw = int(_user32.GetSystemMetrics(0))
            _sh = int(_user32.GetSystemMetrics(1))
        except Exception:
            _sw, _sh = 1920, 1080
        _w = min(int(1250 * _scale), int(_sw * 0.92))
        _h = min(int(1100 * _scale), int(_sh * 0.92))
        _x = min(int(120 * _scale), max(10, _sw - _w - 40))
        _y = min(int(80 * _scale), max(10, _sh - _h - 60))
        _user32.ShowWindow(hwnd, 9)
        _user32.SetWindowPos(hwnd, 0, _x, _y, _w, _h, 0x0001 | 0x0002 | 0x0020 | 0x0040)
        time.sleep(0.15)
        gui._update_render_rect()
    except Exception:
        pass


def prepare_screen(gui) -> bool:
    """点击操作前的整备：把微信置前 + 清理叠加层/遮挡窗口 + 窗口出屏自动还原。"""
    try:
        # ① 不再强制 SetWindowPos（每次动窗口会把表情弹出菜单"刷掉"——这是"点完笑脸菜单消失"的真凶）
        #   仅当窗口被移到极小/出屏时才自愈，正常流程绝不碰窗口几何。
        # 窗口位置/尺寸**明显偏离**基准（拖动超过阈值）→ 拉回标准位置（按 DPI 换算+限屏）
        try:
            hwnd = getattr(gui, "main_hwnd", 0)
            if hwnd:
                cfg = __import__("agent.config", fromlist=["get_config"]).get_config()
                if (cfg.get("ui") or {}).get("lock_window_pos", True) is not False:
                    try:
                        _scale = max(1.0, _user32.GetDpiForWindow(hwnd) / 96.0)
                    except Exception:
                        _scale = 1.25
                    try:
                        _sw = int(_user32.GetSystemMetrics(0))
                        _sh = int(_user32.GetSystemMetrics(1))
                    except Exception:
                        _sw, _sh = 1920, 1080
                    _tw = min(int(1250 * _scale), int(_sw * 0.92))
                    _th = min(int(1100 * _scale), int(_sh * 0.92))
                    _tx = min(int(120 * _scale), max(10, _sw - _tw - 40))
                    _ty = min(int(80 * _scale), max(10, _sh - _th - 60))
                    r = wintypes.RECT()
                    _user32.GetWindowRect(hwnd, ctypes.byref(r))
                    _need = (abs(r.left - _tx) > 150 or abs(r.top - _ty) > 150
                             or abs((r.right - r.left) - _tw) > _tw * 0.12
                             or abs((r.bottom - r.top) - _th) > _th * 0.12)
                    if _need:
                        _user32.ShowWindow(hwnd, 9)
                        _user32.SetWindowPos(hwnd, 0, _tx, _ty, _tw, _th,
                                             0x0001 | 0x0002 | 0x0020 | 0x0040)
                        time.sleep(0.2)
        except Exception:
            pass
        # 窗口被移出屏幕（多屏切换/DPI 变化常见）→ 自动还原到可见区
        try:
            hwnd = getattr(gui, "main_hwnd", 0)
            if hwnd:
                r = wintypes.RECT()
                _user32.GetWindowRect(hwnd, ctypes.byref(r))
                vw = int(_user32.GetSystemMetrics(0))
                vh = int(_user32.GetSystemMetrics(1))
                if r.left > vw - 60 or r.top > vh - 60 or r.right < 20 or r.bottom < 20:
                    _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    _user32.SetWindowPos(hwnd, 0, 90, 90, 0, 0, 0x0001 | 0x0020 | 0x0040)
                    time.sleep(0.6)
                    gui._update_render_rect()
                elif (r.right - r.left) < 1500 or (r.bottom - r.top) < 1000:
                    # 窗口被缩得很小（物理像素判定，兼容高 DPI：1.8x 屏 1250 逻辑=2250 物理）
                    # → 按 DPI 恢复标准尺寸（逻辑 1250×1100 → 物理换算）
                    try:
                        _scale = max(1.0, _user32.GetDpiForWindow(hwnd) / 96.0)
                    except Exception:
                        _scale = 1.25
                    _user32.ShowWindow(hwnd, 9)
                    _user32.SetWindowPos(hwnd, 0, 90, 90,
                                         int(1250 * _scale), int(1100 * _scale),
                                         0x0001 | 0x0020 | 0x0040)
                    time.sleep(0.6)
                    gui._update_render_rect()
        except Exception:
            pass
        # 先确保前台可见（多实例/最小化自愈）
        _restore_wechat_window(gui)
        # 注意：不调 gui._minimize_blockers()——它会把「微信」主窗误判成遮挡窗最小化
        gui.bring_to_front(keep_topmost=True)
        time.sleep(0.4)
        gui._update_render_rect()
        dismiss_overlays((gui.main_hwnd, gui.render_hwnd))
        gui.bring_to_front(keep_topmost=True)
        time.sleep(0.3)
        if not gui.is_alive():
            # 仍不可见：再恢复一次（可能是把微信误最小化了）
            _restore_wechat_window(gui)
            time.sleep(0.4)
        return True
    except Exception:
        return _restore_wechat_window(gui)


def heal_input():
    """输入状态自愈：释放所有鼠标按键 + 轻微移动光标。

    高频合成点击后偶发「拖动无效/桌面图标拖不动」，多为输入队列残留：
    多余的 up 事件无害，若有丢失的 up 会在此补上。
    注意：不做 WM_CANCELMODE 广播（会让无辜窗口闪动）。
    """
    try:
        for flag in (0x0004, 0x0010, 0x0040):  # LEFTUP / RIGHTUP / MIDDLEUP
            _user32.mouse_event(flag, 0, 0, 0, 0)
            time.sleep(0.05)
        pt = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(pt))
        x, y = int(pt.x), int(pt.y)
        _user32.SetCursorPos(x + 4, y + 2)
        time.sleep(0.05)
        _user32.SetCursorPos(x, y)
    except Exception:
        pass


def click(gui, x: int, y: int, right: bool = False, scale=None, extra_hwnds: tuple = (), heal: bool = True) -> tuple:
    """统一点击入口（wx_click 的适配层）。

    x/y 为微信渲染窗口相对坐标（截图/OCR 空间）。会：
      换算鼠标空间（CPI 缩放）→ 归属校验（确保点是微信）→ wx_click。
    extra_hwnds：额外认作「微信窗口」的句柄（如朋友圈/视频号等独立子窗），
    保证在子窗口上点击不被 ensure_point 误判为"别家窗口"而拒绝。
    heal=False：点击后不做光标自愈移动（悬停出菜单场景必须禁用，
    否则 +4px 抖动会取消菜单）。
    返回 (ok, 消息)。
    """
    try:
        sx, sy = to_click(x + gui.origin_x, y + gui.origin_y, scale)
        hwnds = ((gui.main_hwnd, gui.render_hwnd) + tuple(extra_hwnds))
        ok, why = ensure_point(sx, sy, hwnds, gui=gui)
        if not ok:
            return False, why
        try:
            gui.wx_click(sx, sy, right=right)
            return True, ""
        finally:
            if heal:
                heal_input()
    except Exception as e:
        return False, str(e)


# ── 控制台自检接口 ──────────────────────────────────────────────────────

def self_test(gui=None, point=None) -> dict:
    """「鼠标点击自检」：把光标移到目标点、回读位置与命中窗口，判断可点性。"""
    result = {
        "scale_auto": detect_scale(),
        "scale_used": coord_scale(),
        "clean_overlays": bool(get_config().get("ui", {}).get("clean_overlays", True)),
    }
    try:
        import ctypes as _ct
        pt = wintypes.POINT()
        _user32.GetCursorPos(_ct.byref(pt))
        before = (pt.x, pt.y)
    except Exception:
        before = None
    result["cursor_before"] = before
    try:
        if point is None and gui is not None:
            box = gui.get_input_box()
            pt_x = gui.origin_x + (gui.right_pane_left + gui.render_w) // 2
            pt_y = gui.origin_y + ((box[1] + box[3]) // 2 if box else gui.render_h // 2)
        elif point is not None:
            pt_x, pt_y = point
        else:
            pt_x, pt_y = 400, 400
        sx, sy = to_click(pt_x, pt_y)
        _user32.SetCursorPos(int(sx), int(sy))
        time.sleep(0.3)
        _user32.GetCursorPos(_ct.byref(pt))
        moved = (pt.x, pt.y)
        # 光标「停留」检查：再等 0.5s 回读，若被弹回说明有鼠标锁定/拦截软件
        time.sleep(0.5)
        _user32.GetCursorPos(_ct.byref(pt))
        stayed = (pt.x, pt.y) == moved
        h = _user32.WindowFromPoint(int(sx), int(sy))
        root = _user32.GetAncestor(h, 2)
        cls, title, pid, rect = _window_info(root or h)

        # 全局命中测试对照：再采样「前台窗口」和屏幕中心两点，
        # 若所有窗口（含其他应用如浏览器）都命中桌面/Progman，说明
        # 当前进程的窗口命中测试被会话沙箱/输入隔离虚拟化——不是机器问题
        probes = []
        for (px, py) in [(sx, sy), (int(_user32.GetSystemMetrics(0) / 2), int(_user32.GetSystemMetrics(1) / 2))]:
            hh = _user32.WindowFromPoint(int(px), int(py))
            root2 = _user32.GetAncestor(hh, 2)
            c2, _, _, _ = _window_info(root2 or hh)
            probes.append({"point": (int(px), int(py)), "class": c2,
                           "is_desktop": c2 in ("Progman", "WorkerW")})
        all_desktop = bool(probes) and all(p["is_desktop"] for p in probes)
        susceptible = "会话输入被隔离/虚拟化（沙箱或远程会话）：所有窗口的命中测试都返回桌面——请用正常桌面启动机器人（不要用托管调试会话），并重跑本自检" if all_desktop else ""

        result.update({
            "point_logical": (pt_x, pt_y),
            "point_click": (sx, sy),
            "cursor_after": moved,
            "mouse_moved": moved != before,
            "cursor_stayed": stayed,
            "cursor_snapback_hint": "光标被弹回（移动后又回到原位）：可能有鼠标锁定/拦截软件（游戏加加/Razer/按键精灵类）在抢光标，请关闭或加白名单",
            "hit_hwnd": (h & 0xFFFFFFFF),
            "hit_root": hex(root & 0xFFFFFFFF),
            "hit_class": cls,
            "hit_title": title[:80],
            "hit_pid": pid,
            "hit_rect": list(rect),
            "is_wechat": bool(gui and (root in (gui.main_hwnd, gui.render_hwnd) or
                                       h in (gui.main_hwnd, gui.render_hwnd))),
            "ctrl_probes": probes,
            "input_isolated_suspect": susceptible,
        })
    except Exception as e:
        result["error"] = str(e)
    return result
