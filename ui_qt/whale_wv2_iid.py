# -*- coding: utf-8 -*-
"""WebView2 最小接口声明（comtypes）—— 只声明本模块用到的那些成员。

为什么手写而不用官方 nuget：产品要**零新增依赖**，而 WebView2 的 .NET 程序集
（lib/Microsoft.Web.WebView2.*.dll）是托管程序集，Python 直接吃不了；能吃的只有
`WebView2Loader.dll` 那个 C 导出。comtypes 需要接口的 vtable 布局，
官方没有 Python 定义，所以这里按 SDK 头文件的**声明顺序**逐条列出来。

⚠️ vtable 序 = `_methods_` 的顺序，**一个字都不能错位**。改动前先看
   `WebView2.h` 里对应接口的声明序；漏一个方法，后面全体错位且不报错（静默错调）。

comtypes 的两个约定（踩过）：
  · 属性用 `["propget"]` / `["propput"]` 标；没标就只是普通方法，得自己 `get_Xxx()`；
  · `["out"]` 参数是**返回值**，不占实参位 —— 所以调用 `add_Event(h)` 只传一个参。
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, c_int32, c_uint32, c_void_p
from ctypes.wintypes import BOOL, HWND, LPCWSTR, LPWSTR, POINT

from comtypes import COMMETHOD, GUID, HRESULT, IUnknown


class ICoreWebView2Environment(IUnknown):
    _iid_ = GUID("{B96D755E-0319-4E92-A296-23436F46A1FC}")
    _methods_ = [
        COMMETHOD([], HRESULT, "CreateCoreWebView2Controller",
                  (["in"], HWND, "parentWindow"),
                  (["in"], c_void_p, "handler")),
        COMMETHOD([], HRESULT, "CreateWebResourceResponse",
                  (["in"], c_void_p, "content"), (["in"], c_int32, "statusCode"),
                  (["in"], LPCWSTR, "reasonPhrase"), (["in"], LPCWSTR, "headers"),
                  (["out", "retval"], POINTER(c_void_p), "response")),
        COMMETHOD([], HRESULT, "get_BrowserVersionString",
                  (["out", "retval"], POINTER(LPWSTR), "versionInfo")),
        COMMETHOD([], HRESULT, "add_NewBrowserVersionAvailable",
                  (["in"], c_void_p, "eventHandler"),
                  (["out", "retval"], POINTER(c_void_p), "token")),
        COMMETHOD([], HRESULT, "remove_NewBrowserVersionAvailable",
                  (["in"], c_void_p, "token")),
    ]


class ICoreWebView2Settings(IUnknown):
    _iid_ = GUID("{E562E4F0-D7FA-43AC-8D71-C05150499F00}")
    _methods_ = [
        COMMETHOD([], HRESULT, "get_IsScriptEnabled", (["out", "retval"], POINTER(BOOL), "v")),
        COMMETHOD([], HRESULT, "put_IsScriptEnabled", (["in"], BOOL, "v")),
        COMMETHOD([], HRESULT, "get_IsWebMessageEnabled", (["out", "retval"], POINTER(BOOL), "v")),
        COMMETHOD([], HRESULT, "put_IsWebMessageEnabled", (["in"], BOOL, "v")),
        COMMETHOD([], HRESULT, "get_AreDefaultScriptDialogsEnabled",
                  (["out", "retval"], POINTER(BOOL), "v")),
        COMMETHOD([], HRESULT, "put_AreDefaultScriptDialogsEnabled", (["in"], BOOL, "v")),
        COMMETHOD([], HRESULT, "get_IsStatusBarEnabled", (["out", "retval"], POINTER(BOOL), "v")),
        COMMETHOD([], HRESULT, "put_IsStatusBarEnabled", (["in"], BOOL, "v")),
        COMMETHOD([], HRESULT, "get_AreDevToolsEnabled", (["out", "retval"], POINTER(BOOL), "v")),
        COMMETHOD([], HRESULT, "put_AreDevToolsEnabled", (["in"], BOOL, "v")),
        COMMETHOD([], HRESULT, "get_AreDefaultContextMenusEnabled",
                  (["out", "retval"], POINTER(BOOL), "v")),
        COMMETHOD([], HRESULT, "put_AreDefaultContextMenusEnabled", (["in"], BOOL, "v")),
        COMMETHOD([], HRESULT, "get_IsZoomControlEnabled", (["out", "retval"], POINTER(BOOL), "v")),
        COMMETHOD([], HRESULT, "put_IsZoomControlEnabled", (["in"], BOOL, "v")),
        COMMETHOD([], HRESULT, "get_IsBuiltInErrorPageEnabled",
                  (["out", "retval"], POINTER(BOOL), "v")),
        COMMETHOD([], HRESULT, "put_IsBuiltInErrorPageEnabled", (["in"], BOOL, "v")),
    ]


class ICoreWebView2(IUnknown):
    _iid_ = GUID("{76eceacb-0462-4d94-ac83-423a6793775e}")
    _methods_ = [
        COMMETHOD([], HRESULT, "get_Settings",
                  (["out", "retval"], POINTER(ICoreWebView2Settings), "settings")),
        COMMETHOD([], HRESULT, "get_Source", (["out", "retval"], POINTER(LPWSTR), "uri")),
        COMMETHOD([], HRESULT, "Navigate", (["in"], LPCWSTR, "uri")),
        COMMETHOD([], HRESULT, "NavigateToString", (["in"], LPCWSTR, "html")),
        COMMETHOD([], HRESULT, "add_NavigationStarting",
                  (["in"], c_void_p, "h"), (["out", "retval"], POINTER(c_void_p), "t")),
        COMMETHOD([], HRESULT, "remove_NavigationStarting", (["in"], c_void_p, "t")),
        COMMETHOD([], HRESULT, "add_ContentLoading",
                  (["in"], c_void_p, "h"), (["out", "retval"], POINTER(c_void_p), "t")),
        COMMETHOD([], HRESULT, "remove_ContentLoading", (["in"], c_void_p, "t")),
        COMMETHOD([], HRESULT, "add_SourceChanged",
                  (["in"], c_void_p, "h"), (["out", "retval"], POINTER(c_void_p), "t")),
        COMMETHOD([], HRESULT, "remove_SourceChanged", (["in"], c_void_p, "t")),
        COMMETHOD([], HRESULT, "add_HistoryChanged",
                  (["in"], c_void_p, "h"), (["out", "retval"], POINTER(c_void_p), "t")),
        COMMETHOD([], HRESULT, "remove_HistoryChanged", (["in"], c_void_p, "t")),
        COMMETHOD([], HRESULT, "add_NavigationCompleted",
                  (["in"], c_void_p, "h"), (["out", "retval"], POINTER(c_void_p), "t")),
        COMMETHOD([], HRESULT, "remove_NavigationCompleted", (["in"], c_void_p, "t")),
        COMMETHOD([], HRESULT, "add_FrameNavigationStarting",
                  (["in"], c_void_p, "h"), (["out", "retval"], POINTER(c_void_p), "t")),
        COMMETHOD([], HRESULT, "remove_FrameNavigationStarting", (["in"], c_void_p, "t")),
        COMMETHOD([], HRESULT, "add_FrameNavigationCompleted",
                  (["in"], c_void_p, "h"), (["out", "retval"], POINTER(c_void_p), "t")),
        COMMETHOD([], HRESULT, "remove_FrameNavigationCompleted", (["in"], c_void_p, "t")),
        COMMETHOD([], HRESULT, "add_ScriptDialogOpening",
                  (["in"], c_void_p, "h"), (["out", "retval"], POINTER(c_void_p), "t")),
        COMMETHOD([], HRESULT, "remove_ScriptDialogOpening", (["in"], c_void_p, "t")),
        COMMETHOD([], HRESULT, "add_PermissionRequested",
                  (["in"], c_void_p, "h"), (["out", "retval"], POINTER(c_void_p), "t")),
        COMMETHOD([], HRESULT, "remove_PermissionRequested", (["in"], c_void_p, "t")),
        COMMETHOD([], HRESULT, "add_ProcessFailed",
                  (["in"], c_void_p, "h"), (["out", "retval"], POINTER(c_void_p), "t")),
        COMMETHOD([], HRESULT, "remove_ProcessFailed", (["in"], c_void_p, "t")),
        COMMETHOD([], HRESULT, "AddScriptToExecuteOnDocumentCreated",
                  (["in"], LPCWSTR, "js"), (["in"], c_void_p, "h")),
        COMMETHOD([], HRESULT, "RemoveScriptToExecuteOnDocumentCreated",
                  (["in"], LPCWSTR, "id")),
        COMMETHOD([], HRESULT, "ExecuteScript",
                  (["in"], LPCWSTR, "js"), (["in"], c_void_p, "handler")),
        COMMETHOD([], HRESULT, "CapturePreview",
                  (["in"], c_int32, "format"), (["in"], c_void_p, "imageStream"),
                  (["in"], c_void_p, "handler")),
        COMMETHOD([], HRESULT, "Reload"),
        COMMETHOD([], HRESULT, "PostWebMessageAsJson", (["in"], LPCWSTR, "json")),
        COMMETHOD([], HRESULT, "PostWebMessageAsString", (["in"], LPCWSTR, "msg")),
        COMMETHOD([], HRESULT, "add_WebMessageReceived",
                  (["in"], c_void_p, "h"), (["out", "retval"], POINTER(c_void_p), "t")),
        COMMETHOD([], HRESULT, "remove_WebMessageReceived", (["in"], c_void_p, "t")),
    ]


class ICoreWebView2Controller(IUnknown):
    _iid_ = GUID("{4d00c0d1-9434-4eb6-8078-8697a560334f}")
    _methods_ = [
        COMMETHOD([], HRESULT, "get_IsVisible", (["out", "retval"], POINTER(BOOL), "isVisible")),
        COMMETHOD([], HRESULT, "put_IsVisible", (["in"], BOOL, "isVisible")),
        COMMETHOD([], HRESULT, "get_Bounds", (["out", "retval"], POINTER(c_void_p), "bounds")),
        COMMETHOD([], HRESULT, "put_Bounds", (["in"], c_void_p, "bounds")),
        COMMETHOD([], HRESULT, "get_ZoomFactor",
                  (["out", "retval"], POINTER(ctypes.c_double), "zoom")),
        COMMETHOD([], HRESULT, "put_ZoomFactor", (["in"], ctypes.c_double, "zoom")),
        COMMETHOD([], HRESULT, "add_ZoomFactorChanged",
                  (["in"], c_void_p, "h"), (["out", "retval"], POINTER(c_void_p), "t")),
        COMMETHOD([], HRESULT, "remove_ZoomFactorChanged", (["in"], c_void_p, "t")),
        COMMETHOD([], HRESULT, "SetBoundsAndZoomFactor",
                  (["in"], c_void_p, "bounds"), (["in"], ctypes.c_double, "zoom")),
        COMMETHOD([], HRESULT, "MoveFocus", (["in"], c_int32, "reason")),
        COMMETHOD([], HRESULT, "add_MoveFocusRequested",
                  (["in"], c_void_p, "h"), (["out", "retval"], POINTER(c_void_p), "t")),
        COMMETHOD([], HRESULT, "remove_MoveFocusRequested", (["in"], c_void_p, "t")),
        COMMETHOD([], HRESULT, "add_GotFocus",
                  (["in"], c_void_p, "h"), (["out", "retval"], POINTER(c_void_p), "t")),
        COMMETHOD([], HRESULT, "remove_GotFocus", (["in"], c_void_p, "t")),
        COMMETHOD([], HRESULT, "add_LostFocus",
                  (["in"], c_void_p, "h"), (["out", "retval"], POINTER(c_void_p), "t")),
        COMMETHOD([], HRESULT, "remove_LostFocus", (["in"], c_void_p, "t")),
        COMMETHOD([], HRESULT, "add_AcceleratorKeyPressed",
                  (["in"], c_void_p, "h"), (["out", "retval"], POINTER(c_void_p), "t")),
        COMMETHOD([], HRESULT, "remove_AcceleratorKeyPressed", (["in"], c_void_p, "t")),
        COMMETHOD([], HRESULT, "get_ParentWindow", (["out", "retval"], POINTER(HWND), "hwnd")),
        COMMETHOD([], HRESULT, "put_ParentWindow", (["in"], HWND, "hwnd")),
        COMMETHOD([], HRESULT, "NotifyParentWindowPositionChanged"),
        COMMETHOD([], HRESULT, "Close"),
        COMMETHOD([], HRESULT, "get_CoreWebView2",
                  (["out", "retval"], POINTER(POINTER(ICoreWebView2)), "wv")),
    ]



class ICoreWebView2Controller2(ICoreWebView2Controller):
    """Controller 的续版：多一个 `DefaultBackgroundColor`（挂件能透明的关键）。

    为什么必须用它：WebView2 的画面是**窗口合成**出来的，宿主窗一旦带 alpha，
    内核图层就合不上（官方文档原话：HwndHost 派生控件不能显示在 AllowsTransparency
    的窗口里）。真正让「透出下层」生效的唯一官方途径就是把
    `DefaultBackgroundColor` 设成 A=0 的透明色 —— 此时内核走 DirectComposition
    合成，窗口透明才有意义。**alpha 只接受 0 或 255**，中间值 E_INVALIDARG。

    vtable 序 = Controller 的 23 个方法 + get_DefaultBackgroundColor(23)
    + put_DefaultBackgroundColor(24)。漏一个就静默错调。
    """

    _iid_ = GUID("{C979903E-D4CA-4228-92EB-47EE3FA96EAB}")
    _methods_ = ICoreWebView2Controller._methods_ + [
        COMMETHOD([], HRESULT, "get_DefaultBackgroundColor",
                  (["out", "retval"], POINTER(c_uint32), "color")),
        COMMETHOD([], HRESULT, "put_DefaultBackgroundColor", (["in"], c_uint32, "color")),
    ]


# ---- 完成回调的接口（WebView2 会用这些 IID 去 QueryInterface 我们的实现对象）----
class ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler(IUnknown):
    _iid_ = GUID("{41F3632B-5EF4-404F-AD82-2D606C5A9A21}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Invoke",
                  (["in"], HRESULT, "errorCode"),
                  (["in"], POINTER(ICoreWebView2Environment), "createdEnvironment")),
    ]


class ICoreWebView2CreateCoreWebView2ControllerCompletedHandler(IUnknown):
    _iid_ = GUID("{6C4819F3-C9B7-4260-8127-C9F5BDE7F68C}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Invoke",
                  (["in"], HRESULT, "errorCode"),
                  (["in"], POINTER(ICoreWebView2Controller), "createdController")),
    ]


class ICoreWebView2WebMessageReceivedEventArgs(IUnknown):
    """`WebMessageReceived` 事件的参数 —— 取页面 `postMessage` 过来的正文。

    挂件用它做**拖动转发**：WebView2 的画布是个铺满宿主窗的子 HWND，鼠标消息
    被子窗吃掉、不冒泡给父窗的 Qt 窗口过程；给子窗加 `WS_EX_TRANSPARENT`
    会连页内控件一起收不到点击，所以采用页面转发——页面监听拖动、把位移
    `postMessage` 出来，宿主收到再挪窗口。点按钮（click）与拖窗口（move）
    各归各的，互不牺牲。

    只声明用到的一个成员；vtable 序按 SDK 头文件声明序，**不能错位**。
    """
    _iid_ = GUID("{0F99A40C-E962-4207-9E92-E3D542EFF849}")
    _methods_ = [
        COMMETHOD([], HRESULT, "get_Source", (["out", "retval"], POINTER(LPWSTR), "uri")),
        COMMETHOD([], HRESULT, "get_WebMessageAsJson",
                  (["out", "retval"], POINTER(LPWSTR), "json")),
        COMMETHOD([], HRESULT, "TryGetWebMessageAsString",
                  (["out", "retval"], POINTER(LPWSTR), "msg")),
    ]


class ICoreWebView2WebMessageReceivedEventHandler(IUnknown):
    _iid_ = GUID("{57213F19-00E6-49FA-8E07-898EA01ECBD2}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Invoke",
                  (["in"], POINTER(ICoreWebView2), "sender"),
                  (["in"], POINTER(ICoreWebView2WebMessageReceivedEventArgs), "args")),
    ]


class ICoreWebView2Environment2(ICoreWebView2Environment):
    _iid_ = GUID("{20944379-6DCF-41D6-A0A0-ABC0FC50DE0D}")
    _methods_ = [
        COMMETHOD([], HRESULT, "CreateWebResourceRequest",
                  (["in"], LPCWSTR, "uri"), (["in"], LPCWSTR, "method"),
                  (["in"], LPCWSTR, "content"), (["in"], LPCWSTR, "headers"),
                  (["out", "retval"], POINTER(c_void_p), "request")),
    ]


class ICoreWebView2Environment3(ICoreWebView2Environment2):
    _iid_ = GUID("{80A22AE3-BE7C-4CE2-AFE1-5A50056CDEEB}")
    _methods_ = [
        COMMETHOD([], HRESULT, "CreateCoreWebView2CompositionController",
                  (["in"], HWND, "parentWindow"),
                  (["in"], c_void_p, "handler")),
        COMMETHOD([], HRESULT, "CreateCoreWebView2PointerInfo",
                  (["out", "retval"], POINTER(c_void_p), "value")),
    ]


class ICoreWebView2CompositionController(IUnknown):
    """合成承载控制器 —— 无子窗、WebView 画面渲染进宿主的 DComp 视觉树。

    窗口化承载在本机上与半透明分层窗不兼容（画布回退不透明黑块）；合成承载
    是官方唯一给真透明的路径。vtable = Controller 全部方法 + 本接口自有方法
    （序按 SDK 头文件，勿动）。输入不走子窗 —— 宿主收鼠标后用
    `SendMouseInput` 显式注入。
    """

    _iid_ = GUID("{3DF9B733-B9AE-4A15-86B4-EB9EE9826469}")
    # ⛔ 官方头文件实锤：本接口**直接继承 IUnknown**（不继承 Controller！），
    #    vtable = QI/AddRef/Release + 自有方法（get_RootVisualTarget=槽3）。
    #    IsVisible/Bounds/CoreWebView2 从同一对象 QueryInterface 取。
    _methods_ = [
        COMMETHOD([], HRESULT, "get_RootVisualTarget",
                  (["out", "retval"], POINTER(c_void_p), "target")),
        COMMETHOD([], HRESULT, "put_RootVisualTarget",
                  (["in"], c_void_p, "target")),
        COMMETHOD([], HRESULT, "SendMouseInput",
                  (["in"], c_uint32, "eventKind"),
                  (["in"], c_uint32, "virtualKeys"),
                  (["in"], c_uint32, "mouseData"),
                  (["in"], POINTER(POINT), "point")),
    ]


class ICoreWebView2CreateCoreWebView2CompositionControllerCompletedHandler(IUnknown):
    _iid_ = GUID("{02FAB84B-1428-4FB7-AD45-1B2E64736184}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Invoke",
                  (["in"], HRESULT, "errorCode"),
                  (["in"], POINTER(ICoreWebView2CompositionController),
                   "createdController")),
    ]


# ---- DirectComposition（dcomp.h；IID 与方法序按系统头文件原文）----
class IDCompositionTarget(IUnknown):
    _iid_ = GUID("{EACDD04C-117E-4E17-88F4-D1B12B0E3D89}")
    _methods_ = [
        COMMETHOD([], HRESULT, "SetRoot", (["in"], c_void_p, "visual")),
    ]


class IDCompositionVisual(IUnknown):
    _iid_ = GUID("{4D93059D-097B-4651-9A60-F0F25116E2F3}")
    _methods_ = [
        COMMETHOD([], HRESULT, "SetOffsetX", (["in"], c_void_p, "v")),
        COMMETHOD([], HRESULT, "SetOffsetXAnim", (["in"], c_void_p, "v")),
        COMMETHOD([], HRESULT, "SetOffsetY", (["in"], c_void_p, "v")),
        COMMETHOD([], HRESULT, "SetOffsetYAnim", (["in"], c_void_p, "v")),
        COMMETHOD([], HRESULT, "SetTransform", (["in"], c_void_p, "v")),
        COMMETHOD([], HRESULT, "SetTransformAnim", (["in"], c_void_p, "v")),
        COMMETHOD([], HRESULT, "SetTransformParent", (["in"], c_void_p, "v")),
        COMMETHOD([], HRESULT, "SetEffect", (["in"], c_void_p, "v")),
        COMMETHOD([], HRESULT, "SetBitmapInterpolationMode", (["in"], c_uint32, "v")),
        COMMETHOD([], HRESULT, "SetBorderMode", (["in"], c_uint32, "v")),
        COMMETHOD([], HRESULT, "SetClip", (["in"], c_void_p, "v")),
        COMMETHOD([], HRESULT, "SetClipRect", (["in"], c_void_p, "v")),
        COMMETHOD([], HRESULT, "SetContent", (["in"], c_void_p, "v")),
        COMMETHOD([], HRESULT, "AddVisual",
                  (["in"], c_void_p, "visual"), (["in"], BOOL, "insertAbove"),
                  (["in"], c_void_p, "referenceVisual")),
    ]


class IDCompositionDevice(IUnknown):
    _iid_ = GUID("{C37EA93A-E7AA-450D-B16F-9746CB0407F3}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Commit"),
        COMMETHOD([], HRESULT, "WaitForCommitCompletion"),
        COMMETHOD([], HRESULT, "GetFrameStatistics",
                  (["out"], c_void_p, "statistics")),
        COMMETHOD([], HRESULT, "CreateTargetForHwnd",
                  (["in"], HWND, "hwnd"), (["in"], BOOL, "topmost"),
                  (["out", "retval"], POINTER(c_void_p), "target")),
        COMMETHOD([], HRESULT, "CreateVisual",
                  (["out", "retval"], POINTER(c_void_p), "visual")),
    ]


CLS_COMPOSITION_CTRL_HANDLER = (
    ICoreWebView2CreateCoreWebView2CompositionControllerCompletedHandler)


CLS_ENV_HANDLER = ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler
CLS_CTRL_HANDLER = ICoreWebView2CreateCoreWebView2ControllerCompletedHandler
CLS_WEBMSG_HANDLER = ICoreWebView2WebMessageReceivedEventHandler
