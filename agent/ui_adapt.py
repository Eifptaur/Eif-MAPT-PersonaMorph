# -*- coding: utf-8 -*-
"""界面适配层：让 Persona Morph 的屏幕操作在各种机器/DPI/显示器下都能正常点击。

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
import logging
import time
from ctypes import wintypes

from .config import get_config

# 与发送链同一个 logger 名 ⇒ 日志在主日志里能连着看
log = logging.getLogger("persona-morph")

_user32 = ctypes.windll.user32

# 系统叠层窗口的类名（全屏置顶、吃点击，需要清理）
_OVERLAY_CLASSES = (
    "ShellHandwritingCanvas", # Windows 手写输入画布（TabTip 宿主）
    "Windows.UI.Core.CoreWindow", # ⚠️ 这是**一大类**（见下），不能只按类名关
)

#: `Windows.UI.Core.CoreWindow` 里我们**只认**这些标题（小写包含匹配）。
#   ⛔ 该类名下住着用户的**「设置」/「照片」/「计算器」**…… 只按类名 `WM_CLOSE` + 隐藏
#   ＝会把用户正开着的窗口关掉（用户视角＝"我的设置被莫名其妙关了"，而且它可能正在被用）。
#   要清的只有中文输入法的「输入体验」（词条/标点候选面板），按标题白名单收窄。
_OVERLAY_TITLES = ("windows input experience", "windows 输入体验", "输入体验", "input experience")

# 永远不动的窗口类
_SKIP_CLASSES = ("Progman", "WorkerW", "Shell_TrayWnd", "MSCTFIME UI", "IME",
                 "kugou_ui")

_cache = {"scale": None}
#: 坐标换算系数异常只警告一次（避免刷屏）
_SCALE_WARNED = [False]


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
    """把「截图/OCR 空间」坐标换算为「鼠标空间」坐标。

    ⚠️ 本机实测这两个空间是 **1:1**（`_lock_dpi()` 把进程锁成 dpi_aware 后 `detect_scale()`
    恒 1.0，这里的除算等于恒等）；但若 DPI 锁失败、`detect_scale()` 又检测到 1.5，
    这个除算会让**所有点击整体点偏**（按 1.5 缩放）。
    不擅自改公式（真机上无法离线复现那条组合，盲改风险更大），改为**把潜伏变显性**：
    系数不为 1 时落一条 WARNING，现场就能看见。
    """
    s = coord_scale(scale)
    if abs(float(s) - 1.0) > 1e-9 and not _SCALE_WARNED[0]:
        _SCALE_WARNED[0] = True
        log.warning("坐标换算系数 = %s（≠1）⇒ 所有点击会按该系数缩放；"
                    "若点击位置系统性偏移，先查 DPI 感知（_lock_dpi）是否生效", s)
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
        root = _user32.GetAncestor(h, 2) # GA_ROOT
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
        if cls not in _OVERLAY_CLASSES:
            continue
        # ⛔ `Windows.UI.Core.CoreWindow` 是 Windows 的一大类（设置/照片/计算器都在里面）——
        #    只按类名关窗会把用户正开着的窗口 `WM_CLOSE` 掉。按标题白名单收窄到"输入体验"。
        if cls == "Windows.UI.Core.CoreWindow":
            _t = str(title or "").strip().lower()
            if not any(k in _t for k in _OVERLAY_TITLES):
                continue
        handled.append(("overlay", cls, title[:50], pid))
        try:
            _user32.PostMessageW(h, 0x0010, 0, 0) # WM_CLOSE
        except Exception:
            pass  # 清理型：静默合法
        try:
            _user32.ShowWindow(h, 0) # SW_HIDE
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
                continue # 微信自身不动
            if pid in (0,) or not title.strip():
                continue # 系统无标题窗口（如桌面相关的空壳）不动
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
                    # 🔴 （报「那个控制台有时候会强制锁定在最上面，点其他
                    #    窗口也不会显示其他的」）：这里的第二实参原来写的是 **-1**，
                    #    而 -1 是 **HWND_TOPMOST**（HWND_BOTTOM 才是 1）⇒ 注释说"置于下层"，
                    #    实际把这个"挡路的窗口"**永久钉在了最上层**；挡路的那个又常常就是我们的
                    #    控制台窗口（它跟微信重叠时）⇒ 用户怎么点别的窗口都压不下去。
                    _user32.SetWindowPos(h, 1, 0, 0, 0, 0, 0x0001 | 0x0002) # 1 = HWND_BOTTOM
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
        # ⚠️ 抢前台＝打扰用户（最高目标禁止项）⇒ 只有显式打开 ui.allow_foreground 才做
        try:
            if not _cfg_bool("allow_foreground", False):
                return False, "点击点被「%s」窗口遮挡；「全程后台」档不打扰你（可能短暂置前约 1~3 秒后自动还回）（要用前台请在界面里打开 ui.allow_foreground）" % (
                    (cover[1] or "?"))
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


def real_guard(x: int, y: int, gui=None, extra_hwnds: tuple = ()) -> tuple:
    """**真鼠标动作前的最后一道闸**：先确认 (x, y) 这点真属于微信，再把光标移过去。

    为什么必须有它（用户当面问
    为什么还会划我的控制台」）：
      · **投递档**（`PostMessageW` 把消息发进微信自己的消息队列）**永远不会**点到别的窗口
        —— 他这句判断是对的，那 3 条全投递路径确实不碰光标；
      · 但**真鼠标档**是 `SetCursorPos` + `mouse_event`：`mouse_event` 是**全局输入**，
        系统把它派给「光标当前所在 / 最上面的那个窗口」，**它根本不知道微信窗口在哪**；
      · 而 `SetCursorPos` 会**静默失败**（返回 0、`GetLastError()`＝0；本机实测，
        `wechat.py:1334` 早有记录）—— **用户自己正在动鼠标时最容易失败**；
      · 老代码（7 处裸调用）**两件事都不检查**：光标没到位也照发 `mouse_event`
        ⇒ 点击/滚轮落到光标**真正**所在的地方 —— 也就是用户的控制台/正在用的窗口。
    ⇒ 规定：真鼠标动作**一律先过这里**。`ensure_point` 用 `WindowFromPoint` 验归属，
      失败就**不发那一枪**（宁可不做，也不打扰用户）。
    返回 `(ok, 说明)`；`ok=True` 时**光标已经在 (x, y)**，调用方直接发 `mouse_event` 即可。
    """
    try:
        hwnds = []
        if gui is not None:
            for attr in ("main_hwnd", "render_hwnd"):
                h = int(getattr(gui, attr, 0) or 0)
                if h:
                    hwnds.append(h)
        hwnds.extend(int(h) for h in tuple(extra_hwnds) if h)
        if not hwnds:
            try:
                from . import input_backend as _ib
                _m = int(_ib.find_main_window() or 0)
                if _m:
                    hwnds.append(_m)
            except Exception:
                pass
        if not hwnds:
            return False, "拿不到微信窗口句柄 ⇒ 没法确认这一枪会打到谁，按「不打」处理"
        x, y = int(x), int(y)
        ok, why = ensure_point(x, y, tuple(hwnds), gui=gui)
        if not ok:
            return False, why
        if not _user32.SetCursorPos(x, y):
            _e = ctypes.windll.kernel32.GetLastError()
            if int(_e) == 5:
                # ERROR_ACCESS_DENIED：**UIPI** —— 最前面的窗口属于更高完整性级别（提权）进程时，
                # 系统不允许我们挪光标。
                return False, ("系统不让挪光标（ACCESS_DENIED，最前面的窗口是管理员权限的——"
                               "多半是我们的控制台或任务管理器）⇒ 已放弃这一枪")
            return False, ("SetCursorPos(%d,%d) 返回 0，光标没到位（多半是你正在用鼠标，err=%s）"
                           "⇒ 已放弃这一枪，不打扰你" % (x, y, _e))
        return True, ""
    except Exception as e:
        return False, "real_guard 异常（按「不打」处理）：%s" % str(e)[:80]


def click_real_at(sx: int, sy: int, *, gui=None, right: bool = False, hold_ms: int = 120,
                  settle_ms: int = 0, extra_hwnds: tuple = (), restore: bool = True,
                  recheck: bool = True) -> tuple:
    """在**屏幕坐标** (sx, sy) 打一枪真鼠标 —— 真鼠标档的**唯一原语**。返回 `(ok, 说明)`。

    工序（顺序有意义，别省任何一步）：
      ① `restore=True` 时先记下当前光标位置；
      ② `real_guard`：确认这一点真属于微信（用户正在动鼠标导致 `SetCursorPos` 静默失败 ⇒ **不打**）；
      ③ `settle_ms` 之后**再确一次**（`recheck`）：这几百毫秒里用户点到别的窗口就会把落点抢走，
         而 `mouse_event` 是**全局输入**（它打给"开枪那一刻最上面那个窗口"，根本不知道微信在哪）
         ⇒ 落点变了这一枪就**不打**；
      ④ 按下 → 按住 `hold_ms` → 抬起；
      ⑤ `restore=True` 时把光标放回①记下的位置（"用完必须还回去"是硬口径）。

    ⛔ 为什么要有这个唯一原语：这条工序以前在 `wechat` 里被手抄了 **4 处**，而手抄版**全都漏了第③步**
      （只过闸就开枪）—— 漏掉的后果是"点歪到用户的控制台/浏览器"。收口后所有真鼠标点击都走这里，
      要改节奏/加重试只改这一处。

    ⚠️ 与 `click()` 的分工：`click()` 走 `gui.wx_click`（库自己的节奏，够用于绝大多数控件）；
      本函数用于"落点稳定性/按住时长会影响成败"的那几枪。
    """
    old = None
    if restore:
        try:
            pt = wintypes.POINT()
            if _user32.GetCursorPos(ctypes.byref(pt)):
                old = (int(pt.x), int(pt.y))
        except Exception: # noqa: BLE001
            old = None
    try:
        sx, sy = int(sx), int(sy)
        ok, why = real_guard(sx, sy, gui=gui, extra_hwnds=tuple(extra_hwnds))
        if not ok:
            return False, why
        if settle_ms > 0:
            time.sleep(int(settle_ms) / 1000.0)
            if recheck:
                ok2, why2 = real_guard(sx, sy, gui=gui, extra_hwnds=tuple(extra_hwnds))
                if not ok2:
                    return False, ("开枪前落点已经变了（%s）⇒ 这一枪没打（多半是你这几百毫秒里点/切到了别的窗口）"
                                   % str(why2)[:70])
        down, up = (0x0008, 0x0010) if right else (0x0002, 0x0004)
        _user32.mouse_event(down, 0, 0, 0, 0)
        if hold_ms > 0:
            time.sleep(int(hold_ms) / 1000.0)
        _user32.mouse_event(up, 0, 0, 0, 0)
        return True, "真点 (%d,%d)（按住 %dms）" % (sx, sy, int(hold_ms))
    except Exception as e: # noqa: BLE001
        return False, "click_real_at 异常：%s" % str(e)[:80]
    finally:
        if restore and old:
            try:
                _user32.SetCursorPos(int(old[0]), int(old[1]))
            except Exception: # noqa: BLE001
                pass


def wheel_real_at(cx: int, cy: int, notches: int, *, delta: int = -120, gap_ms: int = 120,
                  gui=None, extra_hwnds: tuple = (), settle_ms: int = 0, stop=None) -> tuple:
    """在**屏幕坐标** (cx, cy) 滚 `notches` 格 —— 真鼠标档的**滚轮原语**。返回 `(ok, 说明)`。

    ⛔ 滚轮和点击一样是**全局输入**：光标没到位时，滚轮会滚到用户当前真正指着的那个窗口
      （他的控制台）上 ⇒ 必须同样先过 `real_guard`（它会把光标挪到落点并确认归属）。

    `delta`：一格 `120`（Windows 的 `WHEEL_DELTA`）；个别老链用的是"一格塞 900"的口径 ⇒ 保留可传。
    `settle_ms`：过闸之后先等一会儿（刚切过去的窗口需要时间接受滚轮）。
    `stop`：可选**无参回调**，返回真值就提前收手（滚动链要能被打断）——收手时的已滚格数会写进说明里。

    ⚠️ 与点击原语一样：**不还原光标**（调用方那条链自己管；`real_guard` 已经把光标放在落点上）。
    """
    cx, cy = int(cx), int(cy)
    n = max(0, int(notches))
    try:
        ok, why = real_guard(cx, cy, gui=gui, extra_hwnds=tuple(extra_hwnds))
        if not ok:
            return False, why
        if settle_ms > 0:
            time.sleep(int(settle_ms) / 1000.0)
        done = 0
        for _ in range(n):
            if stop is not None:
                try:
                    if stop():
                        return True, "已停止（已滚动 %d 格）" % done
                except Exception: # noqa: BLE001
                    pass
            _user32.mouse_event(0x0800, 0, 0, int(delta), 0)
            done += 1
            if gap_ms > 0:
                time.sleep(int(gap_ms) / 1000.0)
        return True, "已滚动 %d 格" % done
    except Exception as e: # noqa: BLE001
        return False, "wheel_real_at 异常：%s" % str(e)[:80]


def click_real_hold(gui, x: int, y: int, right: bool = False, settle_ms: int = 180,
                    hold_ms: int = 120, extra_hwnds: tuple = ()) -> tuple:
    """真鼠标**按住一会儿再松开**（给"只认真点"的自绘控件用），且**点完把光标放回原处**。

    为什么单独一个：微信输入区那两个控件——"进录音态的圆圈"和
    "录音态里的绿色发送"——**对投递点击只出悬停高亮**（四种投递变体实测都不进录音态），
    只能真点；而"不动用户鼠标"是硬口径 ⇒ 这一枪必须：①先过 `real_guard`（确认这点真属于微信；
    用户正在动鼠标导致 `SetCursorPos` 失败就**不打**）；②自己控节奏（移到位→等 `settle_ms`→按下→
    按住 `hold_ms`→松开），实测 180ms/120ms 才被这两个控件吃下；③**前后 `GetCursorPos` 一致**。

    与 `click()` 的分工：`click()` 走 `gui.wx_click`（库自己的节奏，够用于绝大多数控件）；
    本函数用于"按住时长/落点稳定性会影响成败"的那几个。x/y 同 `click()`＝**渲染区相对**坐标。

    实现在 `click_real_at()`（屏幕坐标版）：本函数只多做一步"渲染相对 → 屏幕"的换算。
    """
    try:
        sx, sy = to_click(int(x) + int(gui.origin_x), int(y) + int(gui.origin_y))
        return click_real_at(sx, sy, gui=gui, right=right, hold_ms=hold_ms,
                             settle_ms=settle_ms, extra_hwnds=extra_hwnds, restore=True)
    except Exception as e: # noqa: BLE001
        return False, "click_real_hold 异常：%s" % str(e)[:80]


def _restore_wechat_window(gui) -> bool:
    """按进程枚举找「微信」主窗并恢复（窗口最小化/隐藏/移出屏时自愈）。

    只有显式打开 `ui.allow_foreground` 才做；默认关时返回 False，让调用方如实报"需要前台的路径已跳过"。
    """
    if not _cfg_bool("allow_foreground", False):
        return False
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


def _cfg_bool(key: str, default: bool = False) -> bool:
    try:
        _cfg = __import__("agent.config", fromlist=["get_config"]).get_config()
        v = (_cfg.get("ui") or {}).get(key, default)
        return bool(default if v is None else v)
    except Exception:
        return bool(default)


def _force_geometry(gui) -> None:
    """把微信主窗移到固定位置/大小（**默认开**：`ui.lock_window_pos`
    「你把限位设成默认吧…也能防止点错」；**关掉就一行都不碰用户的窗口**）。

    用户实测——真凶就是这里原来那句
    `ShowWindow(hwnd, 9)`（SW_RESTORE：**把最小化的微信强行弹出来**）＋ 一对自相矛盾的
    `SWP_SHOWWINDOW|SWP_HIDEWINDOW`。原实现的坐标其实被 `SWP_NOMOVE|SWP_NOSIZE` 抵消掉了，
    所以它的实际效果只剩"把用户的微信窗口抬出来"——正是最高目标（不打扰）明令禁止的事。
    现在：默认不调用；真要摆窗口时**只挪不动前台、不还原最小化、不激活**。
    """
    try:
        _cfg = __import__("agent.config", fromlist=["get_config"]).get_config()
        if (_cfg.get("ui") or {}).get("lock_window_pos", False) is not True:
            return
        hwnd = getattr(gui, "main_hwnd", 0)
        if not hwnd:
            return
        # 最小化时**不动**（还原别人的窗口＝打扰）
        try:
            if _user32.IsIconic(int(hwnd)):
                return
        except Exception:
            pass
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
        # ⛔ 读配置（`ui.lock_window_w/h`）。原来写死 1250×1100×DPI ⇒ 每次限位都把窗口摆成
        #    "贼大、屏幕中间"（用户连报三次）。实测：只改 `wechat._limit_wechat_window` 没用，
        #    因为**这一处才是真正在改窗口的那个**。
        try:
            _uic = (__import__("agent.config", fromlist=["get_config"]).get_config().get("ui") or {})
            _w = min(int(_uic.get("lock_window_w") or 1113), int(_sw * 0.92))
            _h = min(int(_uic.get("lock_window_h") or 909), int(_sh * 0.92))
        except Exception:
            _w, _h = min(1113, int(_sw * 0.92)), min(909, int(_sh * 0.92))
        _x = min(int(120 * _scale), max(10, _sw - _w - 40))
        _y = min(int(80 * _scale), max(10, _sh - _h - 60))
        # SWP_NOZORDER | SWP_NOACTIVATE：挪位置但**不打扰你（可能短暂置前约 1~3 秒后自动还回）、不改 Z 序**
        _user32.SetWindowPos(hwnd, 0, _x, _y, _w, _h, 0x0004 | 0x0010)
        time.sleep(0.15)
        gui._update_render_rect()
    except Exception:
        pass


# ══ 置前/置顶的**唯一闸门**══════════════
#
# 把微信**顶到了所有窗口之上**——链路是
#   `_send_poke_locate` → `gui.get_input_box()`（库 guia.py:1367）
#   → 探针连失 6 次 → `calibrate_layout()`（:1388→617）→ **`bring_to_front()`（:641）**
#   → `SetWindowPos(HWND_TOPMOST)`（:807）+ SetForegroundWindow/SetActiveWindow/SetFocus，
#     还先把系统前台锁 `SPI_SETFOREGROUNDLOCKTIMEOUT` 清成 0。
# 走的是**置顶**，所以用户拿浏览器"盖住"根本盖不住；而他做这个产品的最高目标就是
# ⇒ 从此：凡"置前/置顶/最小化别人的窗"的调用，全部走这道闸；投递档（默认）一律拒绝。
_FG_REFUSED = {"n": 0, "why": "", "who": ""}


def fg_allowed() -> tuple:
    """现在允许把窗口**置前/置顶**吗？返回 `(ok, 原因)`。

    只有"明确要走真鼠标"的档位才允许（`wechat.background_only=False` **且**
    `input.allow_real_fallback=True`）；缺键、读配置失败一律按**拒绝**处理（fail-closed）。
    实现在 `guards.foreground()`（与真鼠标谓词放在一起，并钉了"这一问严格强于真鼠标那一问"）。
    """
    from .guards import foreground

    return foreground()


def fg_refused() -> dict:
    """被闸门拒掉的置前调用次数/最后一条原因（供自检与汇报用）。"""
    return dict(_FG_REFUSED)


def _guarded(name: str, fail_value):
    """造一个"带闸门的替身"：闸不过就留日志并返回 `fail_value`（**不抛异常**）。"""
    def _make(fn):
        import functools

        @functools.wraps(fn)
        def _w(*a, **k):
            ok, why = fg_allowed()
            if not ok:
                _FG_REFUSED["n"] += 1
                _FG_REFUSED["why"] = why
                _FG_REFUSED["who"] = name
                if _FG_REFUSED["n"] <= 5: # 只打前几条，避免刷屏
                    try:
                        from .wechat import log as _log
                    except Exception:
                        import logging as _log
                    _log.warning("⛔ 拒绝置前/置顶：%s() 被调用（%s）—— 投递链不需要前台，"
                                 "而置顶会压过用户用来遮挡的窗口", name, why)
                return fail_value
            return fn(*a, **k)
        return _w
    return _make


_HARDEN_TARGETS = (("bring_to_front", False), ("calibrate_layout", False),
                   ("ensure_visible", False), ("_minimize_blockers", None),
                   ("restore_zorder", None),
                   # ⭐ **逐步前台追踪抓到的真凶**：`_get_uia()` 一旦被调，就会
                   #   `WeChatUIA() → ensure_window() → _activate(w)` ⇒ `ShowWindow(SW_RESTORE/SW_SHOW)`
                   #   + **`SetForegroundWindow`**（`wechatauto/uia_driver.py:651-670`）——**把微信顶到最前**。
                   #   它由 `_uia_target_row_rect` 触发（拍一拍/引用定位都会调）⇒ 这就是"上完类闸后
                   #   还有一次置前"的那一跳。而且物化 UIA 要**往 Weixin.dll 写 gate 字节**（红线项，
                   ("_get_uia", None))


def harden_gui_class(cls=None) -> bool:
    """把库里那几个"会置前/置顶/最小化别人窗口"的方法**在类上**换成带闸门的版本。

    ⛔ 为什么必须上在**类**上：
      `WeChatGUI.__init__` 里就有 `if calibrate: self.calibrate_layout()`（guia.py:417），
      而 `_load_layout()` 在**窗口尺寸与上次校准差 >15% 时拒绝采用**（guia.py:705）——
      限位一改尺寸就会触发 ⇒ **每次新进程构造 GUI 都可能直接在 `__init__` 里走到
      `calibrate_layout() → bring_to_front()`（HWND_TOPMOST + SetForegroundWindow）**。
      那一刻**实例级 `harden_gui` 还没装上** ⇒ 只包实例挡不住它（这就是"又"的原因）。
      ⇒ 必须在**构造之前**把类方法换掉：`_get_gui()` 里 `patch_driver_quirks()` 之后、`WeChatGUI()` 之前。
    """
    if cls is None:
        try:
            from wechatauto.guia import WeChatGUI as cls
        except Exception:
            return False
    if getattr(cls, "_pm_fg_hardened_class", False):
        return True
    for name, fail_value in _HARDEN_TARGETS:
        fn = getattr(cls, name, None)
        if not callable(fn):
            continue
        try:
            setattr(cls, name, _guarded(name, fail_value)(fn))
        except Exception:
            pass
    try:
        cls._pm_fg_hardened_class = True
    except Exception:
        pass
    return True


def harden_gui(gui):
    """在**实例上**再兜一层（万一有实例在 `harden_gui_class` 之前就建出来了）。

    被拒时**返回假值并留一条日志**，不抛异常（调用方按"没做到"处理即可）。
    """
    if gui is None or getattr(gui, "_pm_fg_hardened", False):
        return gui
    for name, fail_value in _HARDEN_TARGETS:
        fn = getattr(gui, name, None)
        if not callable(fn):
            continue
        try:
            setattr(gui, name, _guarded(name, fail_value)(fn))
        except Exception:
            pass
    try:
        gui._pm_fg_hardened = True
    except Exception:
        pass
    return gui


def prepare_screen(gui) -> bool:
    """点击操作前的整备：把微信置前 + 清理叠加层/遮挡窗口 + 窗口出屏自动还原。

    ⚠️ 用户实测＋「它还会导致微信卡死，
    我操作都操作不了」——本函数原来那几段 `ShowWindow(hwnd, 9)`（SW_RESTORE）＋
    `gui.bring_to_front(keep_topmost=True)`（**把微信钉到最上层**）就是真凶：
    最小化的微信被强行弹出来，而且置顶窗口会一直压在所有窗口之上（用户当然点不动自己其它窗口）。
    ⇒ 现在只做"零打扰"的那一半（清遮挡、探活），**除非显式打开 `ui.allow_foreground`
    否则绝不挪窗口/不还原最小化/不打扰你（可能短暂置前约 1~3 秒后自动还回）/不置顶**。
    """
    if not _cfg_bool("allow_foreground", False):
        try:
            dismiss_overlays((getattr(gui, "main_hwnd", 0), getattr(gui, "render_hwnd", 0)))
            gui._update_render_rect()
        except Exception:
            pass
        return bool(getattr(gui, "is_alive", lambda: True)())
    try:
        # ① 不再强制 SetWindowPos（每次动窗口会把表情弹出菜单"刷掉"——这是"点完笑脸菜单消失"的真凶）
        #   仅当窗口被移到极小/出屏时才自愈，正常流程绝不碰窗口几何。
        # 窗口位置/尺寸**明显偏离**基准（拖动超过阈值）→ 拉回标准位置（按 DPI 换算+限屏）
        try:
            hwnd = getattr(gui, "main_hwnd", 0)
            if hwnd:
                cfg = __import__("agent.config", fromlist=["get_config"]).get_config()
                # ⚠️ 这里的默认值原来写成 `True`（＝键缺失时按"要限位"办），
                #    与 `ui.lock_window_pos` 的配置默认值 `False`（＝不动用户的窗口）**打架**——
                #    同一个开关两处默认相反，读代码的人会得到完全不同的结论。统一成 False。
                if (cfg.get("ui") or {}).get("lock_window_pos", False) is not False:
                    try:
                        _scale = max(1.0, _user32.GetDpiForWindow(hwnd) / 96.0)
                    except Exception:
                        _scale = 1.25
                    try:
                        _sw = int(_user32.GetSystemMetrics(0))
                        _sh = int(_user32.GetSystemMetrics(1))
                    except Exception:
                        _sw, _sh = 1920, 1080
                    # ⛔ 原来这里写死 1250×1100（再乘 DPI 缩放）⇒ 每次限位都把用户的窗口摆成
                    #    "贼大、屏幕中间"（用户反馈的正是这个）；而且与 `wechat._limit_wechat_window`
                    #    是**两套口径**。⇒ 统一读同一份配置 `ui.lock_window_w/h`。
                    _ui_cfg = (cfg.get("ui") or {}) if isinstance(cfg, dict) else {}
                    try:
                        _tw, _th = int(_ui_cfg.get("lock_window_w") or 1113), int(_ui_cfg.get("lock_window_h") or 909)
                    except Exception:
                        _tw, _th = 1113, 909
                    _tw = min(_tw, int(_sw * 0.92))
                    _th = min(_th, int(_sh * 0.92))
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
                    _user32.ShowWindow(hwnd, 9) # SW_RESTORE
                    _user32.SetWindowPos(hwnd, 0, 90, 90, 0, 0, 0x0001 | 0x0020 | 0x0040)
                    time.sleep(0.6)
                    gui._update_render_rect()
                # 已停用（原来在这里「恢复标准尺寸」）：阈值写死 1500x1000，而用户把窗口调成
                # 977x976 时每次都命中它 => 被恢复成 1250x1100xDPI（实测 1875x1472）并摆到
                # (90,90) —— 这就是「我调小它就变大、还跑到那个位置」的真凶。尺寸现在由限位
                # （ui.lock_window_w/h）统一管，这里不再插手。
                elif False and ((r.right - r.left) < 1500 or (r.bottom - r.top) < 1000):
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
        for flag in (0x0004, 0x0010, 0x0040): # LEFTUP / RIGHTUP / MIDDLEUP
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


def _cursor_now():
    """当前光标位置（取不到就 None）。"""
    try:
        pt = wintypes.POINT()
        if _user32.GetCursorPos(ctypes.byref(pt)):
            return (int(pt.x), int(pt.y))
    except Exception: # noqa: BLE001
        pass
    return None


def _cursor_restore(pos, why: str = "") -> bool:
    """把光标放回 `pos`（真鼠标档用完**必须**还回去）。失败只记日志。"""
    if not pos:
        return False
    try:
        _user32.SetCursorPos(int(pos[0]), int(pos[1]))
        time.sleep(0.05)
        back = _cursor_now()
        if back != pos:
            log.warning("真鼠标档：光标没能还原（%s → %s）%s", pos, back, why)
            return False
        return True
    except Exception as e: # noqa: BLE001
        try:
            log.warning("真鼠标档：还原光标异常（%s）：%s", why, e)
        except Exception: # noqa: BLE001
            pass  # 清理型：静默合法
        return False


def _real_mouse_allowed() -> bool:
    """这一枪允许用**真鼠标**吗（默认**不允许**）。

    实现在 `guards.real_mouse()`。⚠️ 与 `WeChatAdapter._real_fallback_allowed()` 是**同一个问题**，
    所以它们共用那一份实现（这两处原来各抄了一遍，逐字相同）。
    """
    from .guards import real_mouse

    return real_mouse()


def click(gui, x: int, y: int, right: bool = False, scale=None, extra_hwnds: tuple = (), heal: bool = True) -> tuple:
    """统一点击入口（wx_click 的适配层）。

    ⛔ （**真机四项复测第一枪就抓到的红线违例**）：这里原来是**无条件** `gui.wx_click()`
    —— 那是库自己的真实鼠标（`SetCursorPos` + `mouse_event`），于是两件事同时错：
      ① 它**绕过了输入档位**（`input.backend` / `input.allow_real_fallback` / `WXAGENT_REAL_FALLBACK`）
         —— 而 `wechat.py` 自己的切会话分支是**认这道闸**的（日志
         路径（会动你的光标）⇒ 按最高目标拒绝」）⇒ 同一份红线、两个入口两套标准；
      ② 点完**不还原光标**（现场原始读数：打开表情面板把光标从 (233,1599) 移到 **(1564,1144)** 并留在那儿；
         落点日志 `笑脸落点 (374,994)` + 渲染原点 (1190,150) 正好等于那个光标位置）。
    ⇒ 现在按档位走：**投递优先**（默认档、完全不动光标）；投递没成且没显式开真鼠标兜底 ⇒ **这一枪不发**
    （宁可少做一个动作，也不动用户的鼠标）；真鼠标档必须在 `input.allow_real_fallback` 显式打开，
    且**用完把光标放回原处**（前后一致 + 留痕）。

    x/y 为微信渲染窗口相对坐标（截图/OCR 空间）。会：
      换算鼠标空间（CPI 缩放）→ 归属校验（确保点是微信）→ **按输入档位**点。
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
        # ① 先看档位：auto/message ⇒ 投递点击（不动光标）
        backend = None
        try:
            from . import input_backend as _ib
            backend = _ib.select_backend(gui=gui)
        except Exception as _e: # noqa: BLE001
            log.info("取输入档位失败（按严口径继续判）：%s", _e)
        if backend is not None and not bool(getattr(backend, "touches_cursor", True)):
            try:
                _ok2, _why2 = backend.click(int(getattr(gui, "main_hwnd", 0) or 0), (sx, sy), right=right)
            except Exception as _e2: # noqa: BLE001
                _ok2, _why2 = False, str(_e2)
            if _ok2:
                return True, ""
            if not _real_mouse_allowed():
                return False, ("投递点击没成（%s）⇒ **不发这一枪**："
                               "不动你的鼠标是硬口径，而 `input.allow_real_fallback` 没开"
                               % str(_why2)[:80])
        # ② 真鼠标档：必须显式允许；用完光标还原 + 留痕
        if not _real_mouse_allowed():
            return False, ("当前输入档是真鼠标，而 `input.allow_real_fallback` 没开 ⇒ **不发这一枪**"
                           "（不动你的鼠标是硬口径）")
        _cur = _cursor_now()
        # 真鼠标动作**必须先过 `real_guard`**（它就是为了"`SetCursorPos` 会静默失败"
        # 而设的：光标没到位就发 `mouse_event` 会点到用户正在用的窗口）。
        try:
            _okg, _whyg = real_guard(sx, sy, gui=gui, extra_hwnds=tuple(extra_hwnds))
        except Exception as _e3: # noqa: BLE001
            _okg, _whyg = False, str(_e3)
        if not _okg:
            _cursor_restore(_cur, why="ui_adapt.click 真鼠标档（守卫拒绝）")
            return False, "真鼠标档守卫拒绝：%s" % str(_whyg)[:90]
        try:
            gui.wx_click(sx, sy, right=right)
            return True, ""
        finally:
            _cursor_restore(_cur, why="ui_adapt.click 真鼠标档")
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
