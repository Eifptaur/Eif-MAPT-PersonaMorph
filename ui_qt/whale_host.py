# -*- coding: utf-8 -*-
"""鲸鱼挂件的 WebView2 宿主：起环境 → 建控制器 → 加载挂件页，三步。

页面内容见 `build_host_html`；窗口与交互见 `whale_widget.py`。

三个必须做对的边界：
  ① **独立 user data folder** —— 与产品控制台共用一个目录会互抢目录锁，
     浏览器进程会崩（共目录时 `msedgewebview2` 直接 "has stopped working"）；
  ② **退出必调 `Close()`** —— 不关控制器会留孤儿浏览器进程，父进程死了也不退；
  ③ **消息泵** —— WebView2 的回调走 STA 消息队列，Qt 的事件循环本身就是
     消息泵，回调里只做转交（`_schedule`），重活回到事件循环再跑。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# WebView2 的用户数据目录 —— 独立于产品控制台，避免目录锁冲突。
_USER_DATA = Path(os.environ.get("LOCALAPPDATA", str(ROOT))) / "PersonaMorph" / "WhaleHost" / "WebView2"


def _loader_dll() -> str:
    """WebView2Loader.dll 的绝对路径（产品根目录）。"""
    return str(ROOT / "WebView2Loader.dll")


def _ptr_val(x) -> int:
    """comtypes 对 void* 出参的返回形态不稳定（int / bytes / POINTER 都出现过）
    —— 统一折算成地址整数，调用方不再关心形态。"""
    import ctypes
    from ctypes import c_void_p

    if x is None:
        return 0
    if isinstance(x, int):
        return x
    if isinstance(x, (bytes, bytearray)):
        import traceback
        traceback.print_stack(file=sys.stderr)
        print("_ptr_val bytes:", bytes(x)[:8], file=sys.stderr, flush=True)
        return int.from_bytes(bytes(x)[:8], "little")
    try:
        return ctypes.cast(x, c_void_p).value or 0
    except Exception:  # noqa: BLE001
        return 0


def _qi_raw(ptr, iid_str: str, itype):
    """手写 QueryInterface：返回按 itype 包装的接口指针。

    不用 comtypes 的 QueryInterface —— 它对回调借出再 AddRef 的指针会返回
    错误类型的包装（实测）。vtbl[0] = QI，与官方 C 调用同构。
    """
    import ctypes
    from ctypes import POINTER, c_void_p

    addr = _ptr_val(ptr)
    if not addr:
        raise OSError("QI 空指针")

    class _GUID(ctypes.Structure):
        _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16),
                    ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

    g = _GUID()
    if ctypes.windll.ole32.CLSIDFromString(iid_str, ctypes.byref(g)) != 0:
        raise OSError("IID 解析失败: %s" % iid_str)
    vtbl = ctypes.cast(addr, POINTER(POINTER(c_void_p))).contents
    qi = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(_GUID),
                            POINTER(c_void_p))(vtbl[0])
    out = c_void_p()
    hr = qi(addr, ctypes.byref(g), ctypes.byref(out))
    if hr != 0 or not out.value:
        raise OSError("QI %s 失败 hr=0x%08X" % (iid_str, hr & 0xFFFFFFFF))
    return ctypes.cast(out, POINTER(itype))


def _addref_com(ptr) -> None:
    """对 COM 指针手动 AddRef 一次（回调借出的引用要跨拍使用时必须）。"""
    import ctypes
    from ctypes import c_void_p, POINTER

    addr = ctypes.cast(ptr, c_void_p).value
    if not addr:
        return
    vtbl = ctypes.cast(addr, POINTER(POINTER(c_void_p))).contents
    addref = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vtbl[1])
    addref(addr)


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
        # width/height 是**逻辑像素**（= Qt 的窗口尺寸）。真正的物理像素由下面
        # `_bounds_factor` 决定：`put_Bounds` 到底按逻辑像素还是物理像素解释，
        # 随运行库版本/DPI 设置而异，所以**用页面视口自校准**（见 `_calibrate_bounds`），
        # 不靠猜。
        self.hwnd = int(hwnd)
        self.width = int(width)
        self.height = int(height)
        self._bounds_factor = 1.0 # 1.0 = 按逻辑像素传；校准后可能变成页面缩放
        self._calibrated = False
        self._env = None
        self._ctrl = None
        self._ctrl2 = None
        self._wv = None
        self._ok = False
        self._err = ""
        self._done = False
        self._on_ready = None
        self._composition = False # 窗口化承载默认；建链时按运行库能力切换
        self._cc = None # 合成控制器（仅合成承载）
        # 拖动转发回调：`fn(dx, dy, ended)`，由上层（Qt 侧）设置。
        self._on_drag = None
        # 启动回报回调：`fn(ok)` —— 页面轮询鲸鱼本体的结论（True=渲染出来了）。
        self._on_boot = None
        # 可命中区回调：`fn([[x, y, w, h], ...])` —— 页面报告的「本体 + 可见弹层」外接矩形
        # （CSS 像素，与 Qt 逻辑坐标同刻度）。
        self._on_rects = None
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

        from whale_wv2_iid import (CLS_COMPOSITION_CTRL_HANDLER,
                                   CLS_CTRL_HANDLER,
                                   ICoreWebView2CompositionController,
                                   ICoreWebView2Controller,
                                   ICoreWebView2Environment3)

        # 合成承载（真透明 + 宿主显式注入输入）目前为**实验开关**：
        # PM_WHALE_COMPOSITION=1 时启用；默认走窗口化承载（鲸鱼可见、底色不透明）。
        # 合成链的运行库对象多接口 QI 还有一个访问违例待解（见 trans_err 诊断）。
        want_comp = os.environ.get("PM_WHALE_COMPOSITION") == "1"
        env3 = None
        if want_comp:
            try:
                env3 = self._env.QueryInterface(ICoreWebView2Environment3)
            except Exception:  # noqa: BLE001
                env3 = None
        self._composition = env3 is not None
        self._cc = None

        if self._composition:
            self._ctrl_holder: dict = {"ctrl": None, "hr": None}
            self_ref = self

            class _CCCb(COMObject):
                _com_interfaces_ = [CLS_COMPOSITION_CTRL_HANDLER]

                def Invoke(self, this, hr, ctrl):  # noqa: N802, ANN001
                    self_ref._ctrl_holder["hr"] = hr
                    if hr == 0 and ctrl:
                        # ⛔ 回调给出的引用**只保证 Invoke 期间有效**（后续我们
                        #   调度到事件循环下一拍才使用）⇒ 必须先 AddRef 占住，
                        #   否则控制器在两拍之间被释放 = use-after-free。
                        _addref_com(ctrl)
                        self_ref._ctrl_holder["ctrl"] = ctrl
                    self_ref._schedule(self_ref._after_ctrl)
                    return 0

            cb = _CCCb()
            self._keep.append(cb)
            hr = env3.CreateCoreWebView2CompositionController(
                self.hwnd, c_void_p(_comobj_ptr(cb)))
            if hr != 0:
                raise OSError("合成控制器创建失败 hr=0x%08X" % (hr & 0xFFFFFFFF))
            return

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
            if self._composition:
                from whale_wv2_iid import (ICoreWebView2CompositionController,
                                           ICoreWebView2Controller,
                                           ICoreWebView2Controller2,
                                           ICoreWebView2)
                self._cc = ctrl  # 合成回调给出的就是 CompositionController
                cctl = _qi_raw(self._cc,
                               "{4D00C0D1-9434-4EB6-8078-8697A560334F}",
                               ICoreWebView2Controller)
                self._ctrl2 = _qi_raw(self._cc,
                                      "{C979903E-D4CA-4228-92EB-47EE3FA96EAB}",
                                      ICoreWebView2Controller2)
                self._wv = cctl.get_CoreWebView2() # 官方顺序：先拿 WebView 再建树
                self._setup_dcomp()
                cctl.put_IsVisible(1)
                self._apply_bounds_on(cctl)
                # ⛔ put_DefaultBackgroundColor 在合成承载下必崩（comtypes/裸调皆崩，
                #    对照不透明白也崩）⇒ 跳过；合成内容自带 alpha，直接看默认效果。
            else:
                ctrl.put_IsVisible(1)
                self._apply_bounds()
                self._wv = ctrl.get_CoreWebView2()
                # 画布底色 = 鲸落深色（不透明）——与挂件主题一致，鲸鱼浮在其上。
                # （A=0 透明在本机窗口化承载下会整管线 alpha=0 = 全不可见，禁用。）
                self._enable_transparency()
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
        页内 window 捕获段的 pointerdown），这里收到后转成回调交给 Qt 侧跟光标挪窗。

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
        """解析页面发来的消息，转交对应回调。

        消息形如 `{"pm":"drag","dx":12,"dy":-3}` / `{"pm":"dragend"}` /
        `{"pm":"boot","ok":true|false}`（页面轮询 `.dshwv-root` 的结论，
        false = 脚本加载了但鲸鱼本体始终没渲染 —— 上层据此降级，不给全透明空窗）。
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
        try:
            if kind == "dragbegin":
                cb = self._on_drag
                if cb is not None:
                    cb("begin")
            elif kind == "dragend":
                cb = self._on_drag
                if cb is not None:
                    cb("end")
            elif kind == "rects":
                # 页面报告的「本体 + 当前可见弹层」外接矩形：宿主据此铺可命中底
                # （半透明窗按像素 alpha 做命中测试，alpha=0 的地方鼠标会穿到桌面）。
                # 一并带上的视口尺寸用于**自校准 Bounds 口径**（见 _calibrate_bounds）。
                self._calibrate_bounds(msg.get("vw"), msg.get("dpr"))
                cb = self._on_rects
                if cb is not None:
                    rs = msg.get("rs")
                    if isinstance(rs, list):
                        clean = [[int(v) for v in r[:4]] for r in rs
                                 if isinstance(r, (list, tuple)) and len(r) >= 4]
                        cb(clean)
            elif kind == "boot":
                cb = self._on_boot
                if cb is not None:
                    cb(bool(msg.get("ok")))
        except Exception:  # noqa: BLE001
            pass

    def _setup_dcomp(self) -> None:
        """建 DComp 设备/目标/视觉，把 WebView 画面挂进本窗口的合成树。

        顺序（文档约定）：CreateTargetForHwnd → CreateVisual → target.SetRoot
        → 控制器 put_RootVisualTarget → Commit。任一步失败抛给上层降级。
        """
        import ctypes
        from ctypes import POINTER, c_void_p

        from whale_wv2_iid import (IDCompositionDevice, IDCompositionTarget,
                                   IDCompositionVisual)

        dcomp = ctypes.WinDLL("dcomp.dll")
        # ⛔ 必须用 **DCompositionCreateDevice2**（v2 API，内部自建 D3D 渲染设备）
        #    —— v1 的 DCompositionCreateDevice 是 GDI 后端，承载不了 WebView2 的
        #    D3D 内容（官方样例同款，动态加载避免静态依赖）。
        dcomp.DCompositionCreateDevice2.restype = ctypes.c_long
        dcomp.DCompositionCreateDevice2.argtypes = [c_void_p, c_void_p, c_void_p]

        class _GUID(ctypes.Structure):
            _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16),
                        ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

        g = _GUID()
        if ctypes.windll.ole32.CLSIDFromString(
                str(IDCompositionDevice._iid_), ctypes.byref(g)) != 0:
            raise OSError("DComp IID 解析失败")
        dev = c_void_p()
        hr = dcomp.DCompositionCreateDevice2(None, ctypes.byref(g), ctypes.byref(dev))
        if hr != 0 or not dev.value:
            raise OSError("DCompositionCreateDevice hr=0x%08X" % (hr & 0xFFFFFFFF))
        self._dcdev = ctypes.cast(dev, POINTER(IDCompositionDevice))

        # ⛔ comtypes 对 void* 出参的返回形态不稳定（int / bytes / POINTER 都出现过）
        #    —— 统一走 _ptr_val 折算地址；后续调用只传**地址整数**，不对包装指针
        #    再做 int()（comtypes 指针的 int() 会把内容字节当字面量）。
        _tgt_addr = _ptr_val(self._dcdev.CreateTargetForHwnd(self.hwnd, True))
        _root_addr = _ptr_val(self._dcdev.CreateVisual())
        _web_addr = _ptr_val(self._dcdev.CreateVisual())
        self._dctarget = ctypes.cast(_tgt_addr, POINTER(IDCompositionTarget))
        # 官方结构（WebView2APISample BuildDCompTreeUsingVisual）：
        #   target.SetRoot(根视觉) → 根视觉.AddVisual(webview子视觉)
        #   → 控制器 put_RootVisualTarget(**子视觉**) → Commit。
        # 单视觉直接挂 target 会被 WebView2 判为树结构不合法（fail-fast）。
        self._dcroot = ctypes.cast(_root_addr, POINTER(IDCompositionVisual))
        self._dcweb = ctypes.cast(_web_addr, POINTER(IDCompositionVisual))
        self._dctarget.SetRoot(c_void_p(_root_addr))
        self._dcroot.AddVisual(c_void_p(_web_addr), True, None)
        self._cc.put_RootVisualTarget(c_void_p(_web_addr))
        self._dcdev.Commit()

    def send_mouse(self, kind: int, x: int, y: int, vkeys: int = 0) -> None:
        """把宿主窗收到的鼠标事件注入 WebView（合成承载没有子窗替我们收）。"""
        import ctypes
        import ctypes.wintypes

        cc = getattr(self, "_cc", None)
        if cc is None:
            return
        try:
            pt = ctypes.wintypes.POINT(int(x), int(y))
            cc.SendMouseInput(int(kind), int(vkeys), 0, pt) # POINT 按值
        except Exception:  # noqa: BLE001 — 输入注入尽力而为
            pass

    def _enable_transparency(self) -> None:
        """窗口化承载的兜底透明：画布默认背景置 A=0。

        仅合成承载不可用（运行库过旧）时才会走到这里；合成承载自身的透明
        在 `_after_ctrl` 里直接设（画布 A=0，逐像素 alpha）。alpha 只接受
        0 或 255（中间值 E_INVALIDARG）。失败不致命 —— 降级不透明底，
        原因记 `_trans_err` 供上层展示。
        """
        self._ctrl2 = None
        self._trans_err = ""
        try:
            import ctypes
            from ctypes import POINTER, c_void_p

            from whale_wv2_iid import ICoreWebView2Controller2

            addr = _ptr_val(self._ctrl)
            c2 = _qi_raw(self._ctrl,
                         "{C979903E-D4CA-4228-92EB-47EE3FA96EAB}",
                         ICoreWebView2Controller2)
            # ⛔ put 也用**裸 vtable 调用**：comtypes 包装的接口指针在本机调用
            #    会 AV（同参数裸调正常），模式与上方 AddRef 完全同构。
            c2_addr = _ptr_val(c2)
            vtbl = ctypes.cast(c2_addr, POINTER(POINTER(c_void_p))).contents
            put = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p,
                                     ctypes.c_uint32)(vtbl[27])
            put(c2_addr, 0) # A=0 → 画布全透明
            self._ctrl2 = c2
        except Exception as e:  # noqa: BLE001
            self._ctrl2 = None
            self._trans_err = "%s: %s" % (type(e).__name__, e)

    def _calibrate_bounds(self, vw, dpr) -> None:  # noqa: ANN001
        """用页面视口**自校准** `put_Bounds` 的口径（只做一次）。

        为什么需要：`put_Bounds` 收的是逻辑像素还是物理像素，随运行库版本/系统缩放
        而异。传错的表现极其隐蔽 —— **画面整块被画到窗口之外**，于是窗口全空：
        本体、菜单、减号一起消失，而控制器、页面、脚本全都正常（用户看到的就是
        「啥都没有」）。

        判据：页面视口（CSS 像素）就是内核认为的可视尺寸。
          · 视口 ≈ 传入值 ⇒ 内核按**逻辑像素**解释 ⇒ 口径正确，不动；
          · 视口明显小于传入值 ⇒ 内核按**物理像素**解释（视口 = 传入值 / 页面缩放）
            ⇒ 画面被缩到窗口一角再往外推 ⇒ 改传「传入值 × 页面缩放」重设一次。
        """
        if self._calibrated or not self._ok or self._ctrl is None:
            return
        try:
            vw = int(vw or 0)
            scale = float(dpr or 0) or 1.0
        except (TypeError, ValueError):
            return
        if vw <= 0 or self.width <= 0:
            return
        self._calibrated = True
        if vw >= self.width - 4:
            return # 口径正确
        self._bounds_factor = scale
        try:
            self._apply_bounds()
        except Exception as e:  # noqa: BLE001 — 校准失败也只是画面不对，不该炸挂件
            self._err = "%s: %s" % (type(e).__name__, e)

    def notify_moved(self) -> None:
        """告诉内核「父窗挪了」。

        窗口化承载下父窗位置变化不会自动通知内核；官方要求在 WM_MOVE/WM_MOVING 时调
        `NotifyParentWindowPositionChanged`（否则辅助功能与部分弹窗会错位）。本产品的
        拖动是**带着窗口连续挪**，更需要这一句。
        """
        try:
            if self._ctrl is not None:
                self._ctrl.NotifyParentWindowPositionChanged()
        except Exception:  # noqa: BLE001 — 通知失败只是显示不刷新，不该炸挂件
            pass

    def _apply_bounds(self) -> None:
        self._apply_bounds_on(self._ctrl)

    def _apply_bounds_on(self, ctrl) -> None:
        import ctypes
        from ctypes import c_void_p

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_int32), ("top", ctypes.c_int32),
                        ("right", ctypes.c_int32), ("bottom", ctypes.c_int32)]

        f = float(self._bounds_factor)
        ctrl.put_Bounds(
            ctypes.cast(ctypes.byref(RECT(0, 0, int(self.width * f),
                                          int(self.height * f))), c_void_p))

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

    def on_boot(self, cb) -> None:  # noqa: ANN001
        """注册启动回报回调 `fn(ok)`。

        页面注入脚本在轮询原版挂件本体（`.dshwv-root`）：出现了发 `ok=True`；
        轮询预算耗尽（约 6 秒）还没出现发 `ok=False` —— 那意味着"页面加载成功
        但鲸鱼没画出来"，上层应降级成提示卡，否则用户对着一个全透明空窗
        （这正是"挂件没看到"的一种真实成因）。
        """
        self._on_boot = cb

    def on_rects(self, cb) -> None:  # noqa: ANN001
        """注册可命中区回调 `fn([[x, y, w, h], ...])`。

        页面把「挂件本体 + 当前可见弹层」的外接矩形报上来（CSS 像素，与 Qt 逻辑
        坐标同刻度）；宿主把这些矩形交给窗口层，窗口层只在这些矩形上铺一层
        **alpha=1 的极淡底** —— 半透明窗按像素 alpha 做命中测试，铺到哪、哪才能
        被点到；没铺到的地方保持全透明，鼠标照旧穿到桌面。
        """
        self._on_rects = cb

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
        """跟随宿主窗口改尺寸（**物理像素**，与 `__init__` 同口径）。

        WebView2 不会自己跟着父窗挪或缩放：窗口一动就得把 Bounds 重设一次，
        否则画面留在原来的位置（挂件越拖越"飘"就是这么来的）。
        """
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

    原版脚本是 vendor 来的**单一真值**（全仓库唯一改动记在 ``），
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
        # ⛔ **必须有 <base>**：本页是 NavigateToString 内联页（不透明来源），
        # widget.js 里的相对请求（/dsh-whale/image.png、role-image.png、音效…）
        # 没有可解析的基准 ⇒ 全部落空。<base> 把基准指到控制台端口，相对路径
        # 与 web 控制台页面完全同解。
        "<base href='" + base + "/'>"
        "<style>html,body{margin:0;padding:0;background:transparent;"
        "overflow:hidden;width:100%;height:100%}"
        "#dshw-composer-seat{position:fixed;left:-9999px;top:-9999px;"
        "width:1px;height:1px}"
        # 减号：贴着原版菜单钮（.dshwv-menu-btn）正下方，同宽同形，纵向并排。
        # ⛔ 用 `position:absolute` 且**挂进原版本体内部**（见 wire()）：原版菜单钮是
        #    `top:calc(40.55% + 4px)`，那是相对**本体**的百分比。减号若留在 body 上按
        #    视口算百分比，窗口一变高（给弹层留余量）它就飘到窗口中间、离开鲸鱼。
        ".pm-min-btn{position:absolute;top:calc(40.55% + 34px);right:4px;"
        "width:26px;height:26px;border:none;border-radius:6px;"
        "background:rgba(32,49,112,.85);color:#fff;cursor:pointer;"
        "font:700 16px/1 'Segoe UI',sans-serif;padding:0;z-index:2147483647;"
        "display:flex;align-items:center;justify-content:center;"
        "pointer-events:auto;transition:background .15s ease}"
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
        "function post(msg){try{"
        "if(window.chrome&&window.chrome.webview)window.chrome.webview.postMessage(msg);"
        "}catch(e){}}"
        "function rootEl(){return document.querySelector('.dshwv-root')}"
        "function setCollapsed(on){"
        "document.body.classList.toggle('pm-collapsed',!!on);"
        "var r=rootEl(); if(r){r.style.display=on?'none':'';}"
        # ⛔ 收起状态**不跨启动保持**：挂件是控制台的卫星窗，收起状态一旦落盘，
        #    下次启动就只剩一个 14px 的小圆点 —— 用户眼里就是「挂件不见了」。
        #    原版 web 端能持久化是因为控制台页面一直在，桌面小窗不能这么干。
        "}"
        "function wire(){"
        "var b=document.querySelector('.pm-min-btn');"
        "var d=document.querySelector('.pm-dot');"
        "var r=rootEl();"
        # 减号挂进本体内部：百分比才相对本体算（见上方 CSS 注解），并跟着本体一起挪/藏
        "if(b&&r&&b.parentNode!==r){try{r.appendChild(b);}catch(e){}}"
        # ⛔ 减号/圆点挂在 document 的**捕获**段，不挂在本体元素上：原版自己的 click 处理
        #    也在 document 捕获段，一旦它的处理里 stopPropagation（气泡/弹层逻辑会这么做），
        #    挂在本体上的监听就轮不到 —— 用户实测「点减号没反应」正是这个形态。
        #    同节点同阶段之间只有 stopImmediatePropagation 拦得住，挂这里最稳。
        "if(!window.__pmBtn){"
        "window.__pmBtn=1;"
        "document.addEventListener('click',function(e){"
        "try{var t=e.target;"
        "if(t&&t.closest&&t.closest('.pm-min-btn')){"
        "e.stopPropagation();e.preventDefault();setCollapsed(true);return;}"
        "if(t&&t.closest&&t.closest('.pm-dot')){"
        "e.stopPropagation();e.preventDefault();setCollapsed(false);}"
        "}catch(err){}},true);"
        "}"
        "}"
        # ── 拖动：只在**指针真的移动**之后接管 ──────────────────────────────
        # ⛔ 两个坑都要绕开：
        #   ① 原版自带「拖动鲸鱼改页内位置」（`express()` 写 `root.style.left/top`）——
        #      按住本体是它在挪本体（现象＝"只能在宿主窗内移动"）。所以必须在**它之前**
        #      （window 捕获段，比它的 document 捕获更早）把这一次拖动拦下。
        #   ② 但**不能一按下就抢**：宿主一抢鼠标捕获（SetCapture），内核就收不到"抬起"，
        #      `click` 事件永远不会产生 ⇒ 原版的"单击本体弹气泡"全废（用户实测）。
        # ⇒ 做法：按下只记录，指针累计移动超过阈值才认定"这是拖动"并接管；单击原样放行。
        "function isInteractive(el){"
        "if(!el||!el.closest)return false;"
        "return !!(el.closest('button')||el.closest('input')||el.closest('select')||"
        "el.closest('a')||el.closest('.dshwv-menu')||el.closest('.dshwv-rolelist')||"
        "el.closest('.dshwv-audiolist')||el.closest('.dshwv-usagepanel')||"
        "el.closest('.pm-min-btn')||el.closest('.pm-dot')||"
        "el.closest('.dshwv-menu-btn')||el.closest('[class*=mask]')||"
        "el.closest('[class*=pop]')||el.closest('[class*=menu]'));"
        "}"
        "window.addEventListener('pointerdown',function(e){"
        "if(e.button!==0)return;"
        "if(isInteractive(e.target))return;"
        "window.__pmPend={x:e.clientX,y:e.clientY};"
        "},true);"
        "window.addEventListener('pointermove',function(e){"
        "var p=window.__pmPend;"
        "if(!p||window.__pmDragOn)return;"
        "if(Math.abs(e.clientX-p.x)+Math.abs(e.clientY-p.y)<=4)return;"
        # 到这里才认定是拖动：拦下原版的页内拖动 + 取消原生拖拽，并请宿主接管
        "window.__pmDragOn=1;"
        "e.stopPropagation();e.preventDefault();"
        "post({pm:'dragbegin'});"
        "},true);"
        "window.addEventListener('pointerup',function(){"
        "window.__pmPend=null;"
        "if(window.__pmDragOn){window.__pmDragOn=0;post({pm:'dragend'});}"
        "},true);"
        "function rectsNow(){"
        "var pad=6,out=[];"
        "function add(el){try{"
        "if(!el)return;var r=el.getBoundingClientRect();"
        "if(r.width<2||r.height<2)return;"
        "var cs=getComputedStyle(el);"
        "if(cs.visibility==='hidden'||cs.display==='none')return;"
        "if(parseFloat(cs.opacity)<=0.02)return;"
        "out.push([Math.floor(r.left)-pad,Math.floor(r.top)-pad,"
        "Math.ceil(r.width)+2*pad,Math.ceil(r.height)+2*pad]);}catch(e){}}"
        "add(rootEl());"
        # 我们自己注入的控件（减号 / 收起小圆点 / 等待卡）也要圈进去 —— 它们不是
        # dshwv- 前缀，靠下面那条通配选择器捞不到；漏了它们就等于"点不动减号"。
        "var mine=document.querySelectorAll('.pm-min-btn,.pm-dot,#pm-wait-card');"
        "for(var k=0;k<mine.length;k++)add(mine[k]);"
        # 弹层不用类名清单硬编码：原版所有浮层都是 dshwv- 前缀 + fixed/absolute
        'var all=document.querySelectorAll(\'[class^="dshwv-"],'
        '[class*=" dshwv-"]\');'
        "for(var i=0;i<all.length && i<240;i++){var el=all[i];"
        "try{var p=getComputedStyle(el).position;"
        "if(p==='fixed'||p==='absolute')add(el);}catch(e){}}"
        "return out;}"
        "function reportRects(){"
        "try{var r=rectsNow();var meta=[window.innerWidth,window.devicePixelRatio];"
        # 视口尺寸一并带上：宿主用它**自校准 Bounds 口径**（传逻辑像素还是物理像素，
        # 随运行库而异；传错会让画面整块画到窗口之外 = 用户"啥都看不到"）。
        # 视口也进签名，窗口尺寸一变就会重报一次。
        "var sig=JSON.stringify([r,meta]);"
        "if(sig===window.__pmRectSig)return;window.__pmRectSig=sig;"
        "post({pm:'rects',rs:r,vw:window.innerWidth,dpr:window.devicePixelRatio});"
        "}catch(e){}}"
        "function rectWatch(){"
        "if(window.__pmRect)return;window.__pmRect=1;"
        "reportRects();"
        "setInterval(reportRects,700);"
        "['mousedown','mouseup','click','keyup'].forEach(function(k){"
        "document.addEventListener(k,function(){setTimeout(reportRects,30);},true);});"
        "}"
        "function boot(){"
        "wire();rectWatch();"
        # 原版挂件是异步建的（脚本 defer + 内部等 composer），root 晚于本脚本
        # 出现——且冷启动可能远超 6 秒。轮询分两段：0~6s 找不到就先发
        # boot(false)（宿主亮出**页内**提示卡，不藏页面），之后继续后台轮询——
        # root 一旦出现（哪怕 30 秒后）就移除提示卡再发 boot(true)。
        # 「慢启动」的鲸鱼本体出现时能自动切回来，不再被永久藏掉。
        "var told=false;var card=false;var n=0;"
        "var t=setInterval(function(){n++;wire();"
        "if(rootEl()){clearInterval(t);"
        "var c=document.getElementById('pm-wait-card');if(c)c.remove();"
        "post({pm:'boot',ok:true});return;}"
        "if(n>=24&&!told){told=true;post({pm:'boot',ok:false});}"
        "if(n>=24&&!card){card=true;wire();"
        "var d=document.createElement('div');d.id='pm-wait-card';"
        "d.style.cssText='position:fixed;left:0;top:0;width:100%;height:100%;"
        "z-index:2147483646;display:flex;flex-direction:column;align-items:center;"
        "justify-content:center;gap:10px;font-size:12px;color:#9fb6cf;"
        "text-align:center;padding:0 18px;box-sizing:border-box;"
        "background:rgba(8,20,40,0.55);border-radius:12px;';"
        "var im=document.createElement('img');"
        "im.src='/dsh-whale/image.png?token=@TOKEN@';"
        "im.style.cssText='width:96px;border-radius:12px;';"
        "var tx=document.createElement('div');"
        "tx.textContent='正在等待挂件脚本渲染… 控制台需保持运行';"
        "d.appendChild(im);d.appendChild(tx);"
        "document.body.appendChild(d);}"
        "},250);"
        "setTimeout(function(){clearInterval(t);},61000);"
        "}"
        "if(document.readyState==='loading')"
        "document.addEventListener('DOMContentLoaded',boot);else boot();"
        "})();</script>"
        "</body></html>"
    ).replace("@TOKEN@", str(token or ""))
