# -*- coding: utf-8 -*-
"""右下角鲸鱼挂件 —— **原版照搬**（WebView2 承载上游 widget.js 原文）。

为什么要这一层：上游挂件是 636KB 的纯浏览器脚本（582 处 createElement、35 处 fetch、
6 处 AudioContext、19 处 localStorage、27 处 canvas，外加 11 行设置菜单、角色导入、
音效、自定义泡泡、用量记录、资源管理）。用 Python 逐行重写**不可能一致** —— 那等于
重写一个前端应用。唯一能真正 1:1 的路子就是**把它原样交给浏览器内核跑**：

    · 数据面：宿主已有 `/dsh-whale/*` 全套端点（agent/whale.py + agent/webui.py），
      脚本原样引入时只需给每条 URL 补 token，与 web 页面的做法**完全相同**
      （agent/webui.py 的 `_whale_js_injected`）；
    · 渲染面：本模块用 WebView2 起一个**独立的浏览器实例**，把挂件页内联进去，
      再把它的窗口贴成 Qt 顶层窗的背景；
    · 依赖面：**零新增** —— 程序集（lib/Microsoft.Web.WebView2.*.dll、
      WebView2Loader.dll）产品本来就带（启动器的控制台窗口在用），
      comtypes / pywin32 也在 requirements 里（微信驱动依赖）。

关于「为什么不用 Qt WebEngine」：精简安装的 PySide6_Essentials **不含**
QtWebEngineWidgets（只有 .pyi 存根），装完整版要多 234MB；而 WebView2 运行库本机
已有、程序集已有、启动器已验证可用，是成本最低且**唯一能照搬**的路径。

三个必须做对的点（此前探针踩过的坑，都在这里收掉）：
  ① **独立 user data folder** —— 与产品控制台共用一个目录会互抢锁、浏览器进程会崩
     （实测：共目录时 `msedgewebview2` 直接 "has stopped working"）；
  ② **退出必调 `Close()`** —— 不关控制器会留孤儿浏览器进程，父进程死了也不退；
  ③ **消息泵** —— WebView2 的回调全走 STA 消息队列，Qt 的事件循环本身就是消息泵，
     所以这里**不需要自己抽**（探针里手抽是因为没有事件循环）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# WebView2 的用户数据目录 —— 独立于产品控制台，避免目录锁冲突。
_USER_DATA = Path(os.environ.get("LOCALAPPDATA", str(ROOT))) / "PersonaMorph" / "WhaleHost" / "WebView2"


def _loader_dll() -> str:
    """WebView2Loader.dll 的绝对路径（产品根目录，与启动器同一份）。"""
    return str(ROOT / "WebView2Loader.dll")


class WhaleHostWebView:
    """WebView2 宿主（非 Qt 控件）—— 只管「起环境 → 建控制器 → 加载页面」三步。

    刻意不继承 QWidget：Qt 侧的窗口由 `WhaleWidget` 负责，本类只做浏览器侧的事，
    这样「窗口/拖拽/位置记忆」的既有逻辑一行不用改。

    ⛔ **异步是硬要求，不能同步等**：WebView2 的完成回调走 STA 消息队列投递，
    而 Qt 的 `processEvents()` **不处理 COM 的跨线程 RPC 消息**（实测：在
    `__init__` 里忙等 `processEvents` 会让回调永远不到，探针卡死到超时）。
    所以这里只发起请求，回调到了再由回调里的 Qt 定时器接着走下一棒。
    """

    def __init__(self, hwnd: int, width: int, height: int) -> None:
        self.hwnd = int(hwnd)
        self.width = int(width)
        self.height = int(height)
        self._env = None
        self._ctrl = None
        self._ctrl2 = None
        self._wv = None
        self._ok = False
        self._err = ""
        self._done = False
        self._on_ready = None
        # 回调对象必须持引用：comtypes 的 COMObject 被 GC 后指针即失效。
        self._keep: list = []
        self._init()

    # ---------------------------------------------------------------- 建链

    def _init(self) -> None:
        """发起环境创建（异步）。建成后自动续建控制器，最后置 `_ok`。"""
        try:
            self._create_env()
        except Exception as e:  # noqa: BLE001 — 任何一步失败都降级，不拖垮主界面
            self._err = "%s: %s" % (type(e).__name__, e)
            self._ok = False
            self._done = True

    def _create_env(self) -> None:
        import ctypes
        from ctypes import POINTER, c_void_p

        from comtypes import COMObject

        from whale_wv2_iid import CLS_ENV_HANDLER, ICoreWebView2Environment

        dll = ctypes.WinDLL(_loader_dll())
        self._create_env_fn = dll.CreateCoreWebView2EnvironmentWithOptions
        self._create_env_fn.restype = ctypes.c_long
        self._create_env_fn.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p,
                                        c_void_p, c_void_p]

        self._env_holder: dict = {"env": None, "hr": None}
        self_ref = self

        class _EnvCb(COMObject):
            _com_interfaces_ = [CLS_ENV_HANDLER]

            def Invoke(self, this, hr, env):  # noqa: N802, ANN001
                self_ref._env_holder["hr"] = hr
                if hr == 0 and env:
                    self_ref._env_holder["env"] = ctypes.cast(
                        env, POINTER(ICoreWebView2Environment))
                # 回调里不能直接跑重活 —— 转回 Qt 事件循环下一拍再续
                self_ref._schedule(self_ref._after_env)
                return 0

        cb = _EnvCb()
        self._keep.append(cb)
        # 独立用户数据目录：不指定会落到默认位置，与别的 WebView2 实例抢锁（实测会崩）
        _USER_DATA.mkdir(parents=True, exist_ok=True)
        # 第三个参数恒为 NULL（不设环境选项）—— 见下方 _no_proxy_for_loopback 的说明。
        hr = self._create_env_fn(None, str(_USER_DATA), None, c_void_p(_comobj_ptr(cb)))
        if hr != 0:
            raise OSError("环境创建失败 hr=0x%08X" % (hr & 0xFFFFFFFF))

    def _after_env(self) -> None:
        try:
            env = self._env_holder.get("env")
            if env is None:
                raise TimeoutError("环境回调未返回（hr=%s）" % self._env_holder.get("hr"))
            self._env = env
            self._create_ctrl()
        except Exception as e:  # noqa: BLE001
            self._err = "%s: %s" % (type(e).__name__, e)
            self._finish()

    def _create_ctrl(self) -> None:
        import ctypes
        from ctypes import POINTER, c_void_p

        from comtypes import COMObject

        from whale_wv2_iid import CLS_CTRL_HANDLER, ICoreWebView2Controller

        self._ctrl_holder: dict = {"ctrl": None, "hr": None}
        self_ref = self

        class _CtrlCb(COMObject):
            _com_interfaces_ = [CLS_CTRL_HANDLER]

            def Invoke(self, this, hr, ctrl):  # noqa: N802, ANN001
                self_ref._ctrl_holder["hr"] = hr
                if hr == 0 and ctrl:
                    self_ref._ctrl_holder["ctrl"] = ctypes.cast(
                        ctrl, POINTER(ICoreWebView2Controller))
                self_ref._schedule(self_ref._after_ctrl)
                return 0

        cb = _CtrlCb()
        self._keep.append(cb)
        hr = self._env.CreateCoreWebView2Controller(
            self.hwnd, c_void_p(_comobj_ptr(cb)))
        if hr != 0:
            raise OSError("控制器创建失败 hr=0x%08X" % (hr & 0xFFFFFFFF))

    def _after_ctrl(self) -> None:
        try:
            ctrl = self._ctrl_holder.get("ctrl")
            if ctrl is None:
                raise TimeoutError("控制器回调未返回（hr=%s）" % self._ctrl_holder.get("hr"))
            self._ctrl = ctrl
            ctrl.put_IsVisible(1)
            self._apply_bounds()
            self._enable_transparency()
            self._wv = ctrl.get_CoreWebView2()
            self._ok = True
        except Exception as e:  # noqa: BLE001
            self._err = "%s: %s" % (type(e).__name__, e)
        self._finish()

    def _enable_transparency(self) -> None:
        """把 WebView 的默认背景置为**全透明**（A=0）。

        为什么必须做：挂件是贴在主界面右下角的悬浮件，宿主窗带 alpha（Qt 的
        `WA_TranslucentBackground`）。而 WebView2 的画面是**窗口合成**出来的 ——
        官方文档明确「HwndHost 派生控件不能显示在 AllowsTransparency 的窗口里」，
        不设这一项时内核图层合不上，表现就是**窗口透明、鲸鱼不出现**。

        设成透明色后内核改走 DirectComposition 合成，透明窗才真正可用。
        两个硬约束：**alpha 只接受 0 或 255**（中间值 E_INVALIDARG）；
        接口要 `QueryInterface` 到 `ICoreWebView2Controller2` 才有这个方法。

        这里走**裸 vtable 调用**而不是 comtypes 的 `QueryInterface` 封装：
        后者对 `ctypes` cast 出来的 POINTER 对象行为不确定，而 QueryInterface
        本来就是 IUnknown 的第 0 项，直接按 vtable 位置调最稳（实证脚本同法）。

        拿不到 Controller2（旧运行库）不算致命 —— 退化成不透明底，挂件照常出来，
        只是背景不透。所以这里**不抛异常**、不因此判建链失败。

        ⚠️ 但也不能**静默**：透明失败与"页面没渲染"在用户眼里都是「一个黑块」，
        不分清就没法排查。失败原因记进 `self._trans_err`，由上层决定是否展示。
        """
        self._ctrl2 = None
        self._trans_err = ""
        try:
            import ctypes
            from ctypes import POINTER, c_void_p

            from whale_wv2_iid import ICoreWebView2Controller2

            ptr = ctypes.cast(self._ctrl, c_void_p).value

            class _GUID(ctypes.Structure):
                _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16),
                            ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

            g = _GUID()
            if ctypes.windll.ole32.CLSIDFromString(
                    str(ICoreWebView2Controller2._iid_), ctypes.byref(g)) != 0:
                self._trans_err = "IID 解析失败"
                return
            vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
            _qi = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(_GUID),
                                     POINTER(c_void_p))(vtbl[0])
            out = c_void_p()
            if _qi(ptr, ctypes.byref(g), ctypes.byref(out)) != 0 or not out.value:
                self._trans_err = "QueryInterface 未拿到 Controller2"
                return
            c2 = ctypes.cast(out, POINTER(ICoreWebView2Controller2))
            # COREWEBVIEW2_COLOR 按值传 uint32（A 在高位）；只要 A=0，RGB 无所谓
            c2.put_DefaultBackgroundColor(0)
            self._ctrl2 = c2
        except Exception as e:  # noqa: BLE001 — 降级为不透明底，不影响挂件本身
            self._ctrl2 = None
            self._trans_err = "%s: %s" % (type(e).__name__, e)

    def _apply_bounds(self) -> None:
        import ctypes
        from ctypes import c_void_p

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_int32), ("top", ctypes.c_int32),
                        ("right", ctypes.c_int32), ("bottom", ctypes.c_int32)]

        self._ctrl.put_Bounds(
            ctypes.cast(ctypes.byref(RECT(0, 0, self.width, self.height)), c_void_p))

    def _schedule(self, fn) -> None:  # noqa: ANN001
        """把续跑任务交给 Qt 事件循环（回调线程里不能直接跑重活/建下一环）。"""
        try:
            from PySide6.QtCore import QTimer  # noqa: PLC0415

            QTimer.singleShot(0, fn)
        except Exception:  # noqa: BLE001 — 无 Qt 时退化（离线探针自己抽消息）
            fn()

    def _finish(self) -> None:
        self._done = True
        cb = self._on_ready
        self._on_ready = None
        if cb is not None:
            try:
                cb()
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- 能力

    @property
    def ok(self) -> bool:
        return self._ok

    @property
    def error(self) -> str:
        return self._err

    @property
    def ready(self) -> bool:
        """建链流程是否已结束（无论成功失败）。"""
        return self._done

    def when_ready(self, cb) -> None:  # noqa: ANN001
        """建链结束时回调（已结束则立即调）。"""
        if self._done:
            cb()
        else:
            self._on_ready = cb

    def navigate_to_string(self, html: str) -> bool:
        """内联页面（避免依赖磁盘临时文件；脚本用 file:// 引本地 js 会撞来源限制）。"""
        if not self._ok or self._wv is None:
            return False
        try:
            self._wv.NavigateToString(html)
            return True
        except Exception as e:  # noqa: BLE001
            self._err = "NavigateToString: %s" % e
            return False

    def execute_script(self, js: str, handler: int = 0) -> bool:
        """跑一段 JS。`handler` 传 0 表示不关心结果（省一个回调对象）。"""
        if not self._ok or self._wv is None:
            return False
        try:
            import ctypes
            from ctypes import c_void_p

            self._wv.ExecuteScript(js, c_void_p(int(handler)))
            return True
        except Exception as e:  # noqa: BLE001
            self._err = "ExecuteScript: %s" % e
            return False

    def hide(self) -> None:
        """把 WebView 视图收起来（控制器保留）。

        用途单一：**页面没渲染成功时，把铺满窗口的子窗口让开**，否则它会把鼠标
        消息全吃掉、Qt 层收不到 `mouseMoveEvent`，用户怎么拖挂件都不动
        （web 原版是页内浮层，没有这个子窗口问题）。
        """
        try:
            if self._ctrl is not None:
                self._ctrl.put_IsVisible(0)
        except Exception:  # noqa: BLE001
            pass

    def pass_mouse_through(self) -> bool:
        """让 WebView 的**画布子窗口把鼠标消息透出去**（挂件才拖得动）。

        为什么需要：WebView2 的画面是一个铺满宿主窗的真实子 HWND。Windows 的
        命中测试规则是「子窗口优先」，鼠标落在挂件上的消息全被子窗吃掉，
        **不会冒泡给父 HWND 的 Qt 窗口过程** ⇒ `mousePressEvent` 一次都不触发，
        表现就是「挂件看得见、但怎么拖都不动」。web 原版是页面内浮层，没有
        第二层 HWND，所以原版没这个问题。

        ⛔ **样式必须加在子窗口上，不是父窗口**：子窗才是吃事件的那一层。
        加在父窗（Qt 顶层窗）上会让父窗在命中测试里被跳过 ⇒ 消息直接**穿过整个
        挂件落到桌面**，Qt 反而更收不到，是反向效果。所以这里先枚举父窗的子窗，
        找到 WebView2 的画布窗（类名含 `Chrome_WidgetWin` / `WebView`），
        对它设 `WS_EX_TRANSPARENT | WS_EX_LAYERED`。

        代价（如实记账）：透传之后 WebView **收不到页内鼠标**，原版挂件的
        「点本体出菜单」会一并失效。**拖动位置记忆**是本产品的硬需求、页内菜单
        不是必需能力，所以这里选择保拖动。

        返回是否设置成功；失败只记 `_err`，不抛（拖动是尽力而为的能力）。
        """
        try:
            import ctypes

            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_LAYERED = 0x00080000
            user32 = ctypes.windll.user32
            user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                 ctypes.c_ssize_t]
            user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t

            targets = self._canvas_hwnds()
            if not targets:
                self._trans_err = self._trans_err or "未找到画布子窗"
                return False
            SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER = 0x0002, 0x0001, 0x0004
            SWP_FRAMECHANGED, SWP_NOACTIVATE = 0x0020, 0x0010
            for h in targets:
                cur = user32.GetWindowLongPtrW(ctypes.c_void_p(h), GWL_EXSTYLE)
                want = cur | WS_EX_TRANSPARENT | WS_EX_LAYERED
                if want != cur:
                    user32.SetWindowLongPtrW(ctypes.c_void_p(h), GWL_EXSTYLE, want)
                # 改扩展样式后要让它重新合成一次，否则新样式到下次重绘才生效
                user32.SetWindowPos(
                    ctypes.c_void_p(h), None, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED
                    | SWP_NOACTIVATE)
            return True
        except Exception as e:  # noqa: BLE001
            self._err = "pass_mouse_through: %s" % e
            return False

    def _canvas_hwnds(self) -> list[int]:
        """列出宿主窗下所有属于 WebView2 的子窗口句柄（命中测试要改的那一层）。

        `hint` 一家不可靠：不同运行库版本类名有 `Chrome_WidgetWin_0`、
        `Chrome_RenderWidgetHostHWND`、`msedgewebview2` 等多种写法。所以这里
        **不按类名白名单硬筛**，改为：凡宿主窗的**直接/间接子窗**都是候选 ——
        挂件窗里除了 WebView2 没有别的子窗，全给上样式即可（幂等、无副作用）。
        """
        import ctypes

        user32 = ctypes.windll.user32
        found: list[int] = []
        EnumChildProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                           ctypes.c_void_p)

        def _cb(h, _l):  # noqa: ANN001
            found.append(int(h))
            return True

        try:
            user32.EnumChildWindows(ctypes.c_void_p(int(self.hwnd)),
                                    EnumChildProc(_cb), None)
        except Exception:  # noqa: BLE001
            return []
        return found

    def resize(self, width: int, height: int) -> None:
        """跟随宿主窗口改尺寸（挂件是定长的，但窗口缩放时不该留白边）。"""
        self.width, self.height = int(width), int(height)
        if self._ok and self._ctrl is not None:
            try:
                self._apply_bounds()
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        """关控制器 —— **必须调**，否则浏览器进程会变成杀不掉的孤儿。"""
        if self._ctrl is not None:
            try:
                self._ctrl.Close()
            except Exception:  # noqa: BLE001
                pass
            self._ctrl = None
        self._ctrl2 = None
        self._trans_err = ""
        self._wv = None
        self._env = None
        self._ok = False
        self._keep = []


def _comobj_ptr(obj) -> int:  # noqa: ANN001
    """取 comtypes COMObject 的 COM 指针（指向 vtable 的指针）整数值。

    `_com_pointers_` 是 `{GUID: LP_LP_Vtbl}`，值是 pointer(pointer(vtbl))。
    任取一个即可 —— 同一个 COM 对象的各接口指针身份相同，靠 QueryInterface 区分。
    """
    import ctypes

    for ptr in obj._com_pointers_.values():  # noqa: SLF001
        return ctypes.cast(ptr, ctypes.c_void_p).value or 0
    return 0


def build_host_html(port: int, token: str) -> str:
    """挂件宿主页 —— 把原版 widget.js 原样内联进一个最小页面。

    ⛔ 页面里**必须**放一个 composer 假体：原版 `dshwIsChatRoot()` 在检测到
    `textarea` / `[contenteditable]` / `[data-composer-input]` 之前**一行 DOM 都不碰**
    （widget.js 开头原话「检测到 composer 之前不碰 DOM、不注册全局监听」）。
    web 端之所以能直接跑，是因为控制台页面本身有输入框；这个宿主页没有，
    所以补一个不可见的假体把它叫醒。

    脚本一律走 http（`/dsh-whale/widget.js`），不用 file:// —— 后者在 WebView2 里
    会被当成不透明来源，脚本里的 fetch 全会失败。
    """
    base = "http://127.0.0.1:%d" % port
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;background:transparent;"
        "overflow:hidden;width:100%;height:100%}"
        "#dshw-composer-seat{position:fixed;left:-9999px;top:-9999px;"
        "width:1px;height:1px}"
        "</style></head><body>"
        # 叫醒原版挂件：它只在检测到 composer 后才启动（属性名取原版认的几种之一）
        "<div id='dshw-composer-seat'><textarea data-composer-input=''></textarea></div>"
        "<script>window.__PM_WHALE_BASE=" + repr(base) + ";</script>"
        "<script defer src='" + base + "/dsh-whale/widget.js?token=" + token + "'></script>"
        "</body></html>"
    )
