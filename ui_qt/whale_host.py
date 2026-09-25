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
        # 拖动转发回调：`fn(dx, dy, ended)`，由上层（Qt 侧）设置。
        self._on_drag = None
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
            # 拖动转发要收页面的 postMessage —— 这一步没开的话页面发的消息会被
            # 内核直接丢弃（默认是开的，但显式确认一次，免得运行库改默认值）。
            try:
                self._wv.get_Settings().put_IsWebMessageEnabled(True)
            except Exception:  # noqa: BLE001 — 拿不到设置不影响页面本身
                pass
            self._wire_web_message()
            self._ok = True
        except Exception as e:  # noqa: BLE001
            self._err = "%s: %s" % (type(e).__name__, e)
        self._finish()

    def _wire_web_message(self) -> None:
        """接页面 `postMessage` —— 拖动转发的接收端。

        页面在用户按住挂件拖动时不停把位移发过来（见 `build_host_html` 里的
        `dragSetup`），这里收到后转成回调交给 Qt 侧挪窗口。

        ⛔ 为什么不直接给子窗加 `WS_EX_TRANSPARENT` 让鼠标穿过去：那样消息确实
        能到 Qt，但**页面里所有控件也一起收不到点击了** —— 减号、原版菜单全部
        失效。用「页面报位移」换来的好处是：**点按钮与拖窗口互不干扰**，
        点菜单是 click、拖空白是 move，各走各的。

        回调对象必须持引用（`self._keep`）：comtypes 的 COMObject 被 GC 后
        指针即失效，回调再进来就是野指针。
        """
        if self._wv is None:
            return
        try:
            import ctypes
            from ctypes import c_void_p

            from comtypes import COMObject

            from whale_wv2_iid import CLS_WEBMSG_HANDLER

            host_ref = self

            class _MsgCb(COMObject):
                _com_interfaces_ = [CLS_WEBMSG_HANDLER]

                def Invoke(self, this, sender, args):  # noqa: N802, ANN001
                    try:
                        raw = args.get_WebMessageAsJson() if args else ""
                    except Exception:  # noqa: BLE001 — 取不到正文就当没这条
                        return 0
                    host_ref._on_web_message(raw)
                    return 0

            cb = _MsgCb()
            self._keep.append(cb)
            self._wv.add_WebMessageReceived(c_void_p(_comobj_ptr(cb)))
        except Exception as e:  # noqa: BLE001 — 拿不到转发能力只是拖不动，不影响显示
            self._trans_err = self._trans_err or ("drag-wire: %s" % e)

    def _on_web_message(self, raw: str) -> None:
        """解析页面发来的消息（只认拖动那两条），转交 `on_drag` 回调。

        消息形如 `{"pm":"drag","dx":12,"dy":-3}` / `{"pm":"dragend"}`。
        解析失败一律静默 —— 这是尽力而为的交互增强，不能让一条脏消息把挂件搞崩。
        """
        try:
            import json

            msg = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:  # noqa: BLE001
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("pm")
        cb = self._on_drag
        if cb is None:
            return
        try:
            if kind == "drag":
                cb(int(msg.get("dx", 0)), int(msg.get("dy", 0)), False)
            elif kind == "dragend":
                cb(0, 0, True)
        except Exception:  # noqa: BLE001
            pass

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

    def on_drag(self, cb) -> None:  # noqa: ANN001
        """注册拖动转发回调 `fn(dx, dy, ended)`。

        页面里按住挂件拖动时调用；`ended=True` 表示这一轮拖拽结束（据此落盘位置）。
        必须在 `when_ready` 之前或之后都行 —— 回调只在收到页面消息时才被读。
        """
        self._on_drag = cb

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

    ## 「最小化」（减号）为什么加在这一层，而不是改 widget.js

    原版脚本是 vendor 来的**单一真值**（全仓库唯一改动记在 `PORT-NOTES.md`），
    改它会立刻与 web 端分叉 —— 那就不是"1:1 照搬"了。而「显示/收起挂件」
    本来就是**宿主窗**的职责（窗口显隐、位置记忆都在 Qt 侧），不是挂件自己的能力。
    所以减号由本页注入：原版脚本照旧跑、照旧画，我们只在它上面浮一个自己的按钮。

    位置对齐：原版菜单钮是 `.dshwv-menu-btn`，CSS 定在
    `top:calc(40.55% + 4px);right:4px;26×26px`。减号放在**它正下方**同宽同形，
    与"菜单调节"纵向并排（用户说的「菜单调节的旁边」）。

    收起状态：点减号 ⇒ 藏掉原版挂件本体（`.dshwv-root`）与减号自身，**原地留一个
    小圆点**。小圆点常驻可见、可点击 ⇒ 再点就放回来。留标记而不是全藏，是为了
    「收起来之后还能找得回来」—— 挂件窗是置顶小窗，全藏等于从用户视野里消失。
    """
    base = "http://127.0.0.1:%d" % port
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;background:transparent;"
        "overflow:hidden;width:100%;height:100%}"
        "#dshw-composer-seat{position:fixed;left:-9999px;top:-9999px;"
        "width:1px;height:1px}"
        # 减号：贴着原版菜单钮（.dshwv-menu-btn）正下方，同宽同形，纵向并排
        ".pm-min-btn{position:fixed;top:calc(40.55% + 34px);right:4px;"
        "width:26px;height:26px;border:none;border-radius:6px;"
        "background:rgba(32,49,112,.85);color:#fff;cursor:pointer;"
        "font:700 16px/1 'Segoe UI',sans-serif;padding:0;z-index:2147483647;"
        "display:flex;align-items:center;justify-content:center;"
        "transition:background .15s ease}"
        ".pm-min-btn:hover{background:#203170}"
        # 收起后的小圆点：常驻，再点放回挂件
        ".pm-dot{position:fixed;right:8px;bottom:8px;width:14px;height:14px;"
        "border-radius:50%;background:rgba(32,49,112,.7);cursor:pointer;"
        "z-index:2147483647;display:none;"
        "box-shadow:0 0 0 1px rgba(255,255,255,.35)}"
        ".pm-dot:hover{background:#203170}"
        "body.pm-collapsed .pm-min-btn{display:none}"
        "body.pm-collapsed .pm-dot{display:block}"
        "</style></head><body>"
        # 叫醒原版挂件：它只在检测到 composer 后才启动（属性名取原版认的几种之一）
        "<div id='dshw-composer-seat'><textarea data-composer-input=''></textarea></div>"
        "<button class='pm-min-btn' type='button' title='收起挂件'>&#8722;</button>"
        "<div class='pm-dot' title='展开挂件'></div>"
        "<script>window.__PM_WHALE_BASE=" + repr(base) + ";</script>"
        "<script defer src='" + base + "/dsh-whale/widget.js?token=" + token + "'></script>"
        # 收起/展开逻辑：藏的是原版挂件本体（.dshwv-root），不是整个页面 ——
        # 页面留着，原版脚本继续跑（余额照样刷新），只是看不见。
        "<script>(function(){"
        "function rootEl(){return document.querySelector('.dshwv-root')}"
        "function setCollapsed(on){"
        "document.body.classList.toggle('pm-collapsed',!!on);"
        "var r=rootEl(); if(r){r.style.display=on?'none':'';}"
        "try{localStorage.setItem('pm-whale-collapsed',on?'1':'0');}catch(e){}"
        "}"
        "function wire(){"
        "var b=document.querySelector('.pm-min-btn');"
        "var d=document.querySelector('.pm-dot');"
        "if(b&&!b.__pm){b.__pm=1;b.addEventListener('click',function(e){"
        "e.stopPropagation();e.preventDefault();setCollapsed(true);});}"
        "if(d&&!d.__pm){d.__pm=1;d.addEventListener('click',function(e){"
        "e.stopPropagation();e.preventDefault();setCollapsed(false);});}"
        "}"
        # ── 拖动转发 ────────────────────────────────────────────────────────
        # 为什么在页面里做：WebView2 的画面是一个铺满宿主窗的**子 HWND**，鼠标
        # 消息被子窗吃掉、不冒泡给父窗 ⇒ Qt 侧收不到 mouseMove，挂件拖不动。
        # 早先的做法是给子窗加 `WS_EX_TRANSPARENT` 让消息穿过去 —— 但那样
        # **页面里所有控件都点不动了**（减号、原版菜单全失效），是拿一个功能换
        # 另一个功能。正解是**页面自己报位移**：在页面内监听拖动，把位移通过
        # `window.chrome.webview.postMessage` 发给宿主，Qt 侧收到再 move 窗口。
        # 这样"页内点击"与"拖动窗口"互不干扰 —— 点按钮是 click，拖空白是 move。
        "function dragSetup(){"
        "if(window.__pmDrag)return;window.__pmDrag=1;"
        "var dragging=false,ox=0,oy=0,moved=false;"
        # 只认「落在挂件本体或页面上、且不是按钮/菜单/弹窗」的按下 —— 那些要留给原版逻辑
        "function isInteractive(el){"
        "if(!el||!el.closest)return false;"
        "return !!(el.closest('button')||el.closest('input')||el.closest('select')||"
        "el.closest('a')||el.closest('.dshwv-menu')||el.closest('.dshwv-rolelist')||"
        "el.closest('.dshwv-audiolist')||el.closest('.dshwv-usagepanel')||"
        "el.closest('.pm-min-btn')||el.closest('.pm-dot')||"
        "el.closest('.dshwv-menu-btn')||el.closest('[class*=mask]')||"
        "el.closest('[class*=pop]')||el.closest('[class*=menu]'));"
        "}"
        "function post(msg){try{"
        "if(window.chrome&&window.chrome.webview)window.chrome.webview.postMessage(msg);"
        "}catch(e){}}"
        "document.addEventListener('mousedown',function(e){"
        "if(e.button!==0)return;"
        "if(isInteractive(e.target))return;"
        "dragging=true;moved=false;ox=e.screenX;oy=e.screenY;"
        "},true);"
        "document.addEventListener('mousemove',function(e){"
        "if(!dragging)return;"
        "var dx=e.screenX-ox,dy=e.screenY-oy;"
        "if(!moved&&(Math.abs(dx)+Math.abs(dy))<=4)return;"
        "moved=true;"
        # 每帧只发增量，累加交给 Qt（绝对坐标会被子窗坐标系差异坑到）
        "post({pm:'drag',dx:dx,dy:dy});"
        "ox=e.screenX;oy=e.screenY;"
        "},true);"
        "document.addEventListener('mouseup',function(){"
        "if(dragging&&moved)post({pm:'dragend'});"
        "dragging=false;moved=false;"
        "},true);"
        "}"
        "function boot(){"
        "wire();dragSetup();"
        "var want=false;"
        "try{want=localStorage.getItem('pm-whale-collapsed')==='1';}catch(e){}"
        "if(want)setCollapsed(true);"
        # 原版挂件是异步建的（脚本 defer + 内部等 composer），root 晚于本脚本出现；
        # 用有上限的轮询等它，避免无界定时器在页面销毁后空转。
        "var n=0;var t=setInterval(function(){n++;wire();"
        "if(n>=40||rootEl())clearInterval(t);},150);"
        "}"
        "if(document.readyState==='loading')"
        "document.addEventListener('DOMContentLoaded',boot);else boot();"
        "})();</script>"
        "</body></html>"
    )
