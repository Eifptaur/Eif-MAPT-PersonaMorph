# -*- coding: utf-8 -*-
"""WGC（Windows.Graphics.Capture）**可行性探针**（2026-09-16，跨机建议④）——只读，不点不发。

为什么要它：Python 的 `PrintWindow` 在**窗口被遮挡/最小化**时会给"假帧"（整片纯色），
这让"抓图判据"在那些场景下不可信。WGC 是官方那条路（读 DWM 表面），但**它能不能用取决于
系统版本与 winsdk 的形态**，所以先探一次，别在没验证的平台上改产品代码。

本探针**不改产品行为**，只回答四个问题：
  ① 本机 Windows 版本是多少（WGC 需要 Win10 1803+，`GraphicsCaptureSession.IsSupported` 2004+）；
  ② `winsdk` 装没装、`winsdk.windows.graphics.capture` 导不导得出来；
  ③ `GraphicsCaptureSession.is_supported()` 返回什么（拿不到就如实写"这个 SDK 形态没有这个静态方法"）；
  ④ 能不能从**窗口句柄**建出 `GraphicsCaptureItem`（需要 `IGraphicsCaptureItemInterop` 那层 interop，
     纯 Python 能不能走通是这次要探清的关键 —— 建不出来就只能保留 PrintWindow）。

用法：`runtime\\python\\python.exe scripts\\wgc_probe.py`
"""
import ctypes
import os
import platform
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def main():
    u = ctypes.windll.user32
    u.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    u.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
    u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    build = 0
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", 0,
                           winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0))
        build = int(winreg.QueryValueEx(k, "CurrentBuildNumber")[0])
        winreg.CloseKey(k)
    except Exception as e:
        print("① 系统版本：读注册表失败（%s）" % str(e)[:60])
    print("① 系统：%s build %s（WGC 需 1803+ / IsSupported 需 2004+）" % (platform.system(), build or "?"))

    print("② Python %s · 32 位=%s" % (platform.python_version(), sys.maxsize <= 2 ** 32))
    try:
        import winsdk
        from winsdk.windows.graphics.capture import GraphicsCaptureSession      # noqa: F401
        print("   winsdk 可用，winsdk.windows.graphics.capture 导得出来 ✅")
    except Exception as e:
        print("   ❌ winsdk / capture 命名空间不可用：%s: %s" % (type(e).__name__, str(e)[:80]))
        print("   ⇒ 结论：本机这条路走不通，保留 PrintWindow（并继续用「假帧判据」兜底）")
        return

    ok_supported = None
    try:
        from winsdk.windows.graphics.capture import GraphicsCaptureSession as GCS
        try:
            ok_supported = bool(GCS.is_supported())
        except Exception as e:
            print("③ GraphicsCaptureSession.is_supported() 取不到：%s: %s" % (type(e).__name__, str(e)[:70]))
    except Exception as e:
        print("③ 导入 GraphicsCaptureSession 失败：%s" % str(e)[:70])
    if ok_supported is not None:
        print("③ GraphicsCaptureSession.is_supported() = %s" % ok_supported)

    print("④ 从 HWND 建 GraphicsCaptureItem（需要 IGraphicsCaptureItemInterop）——探一下能不能走通：")
    try:
        from agent.input_backend import find_main_window
        hwnd = find_main_window()
        print("   微信主窗 hwnd = %s" % hwnd)
        from winsdk.windows.graphics.capture import GraphicsCaptureItem
        made = False
        # 纯 Python 侧最常见的两种尝试：直接调（多半没有）与 from_visual（需要 Visual）
        for name in ("create_from_window", "create_for_window", "from_hwnd"):
            fn = getattr(GraphicsCaptureItem, name, None)
            if callable(fn) and hwnd:
                try:
                    item = fn(int(hwnd))
                    print("   ✅ GraphicsCaptureItem.%s(hwnd) 成功：%r" % (name, item))
                    made = True
                    break
                except Exception as e:
                    print("   · %s(hwnd) 失败：%s: %s" % (name, type(e).__name__, str(e)[:70]))
        if not made:
            print("   ❌ 这个 winsdk 形态没有「从 HWND 建 item」的方法 ⇒ 需要 IGraphicsCaptureItemInterop")
            print("      那一层 interop（ctypes/comtypes 手写）≈ 半天到一天工作量，且只能在真机上验。")
            print("   ⇒ 建议：暂不接进产品；先把「被遮挡也能截」这件事用别的方式兜（例如置顶一拍/多帧重试）。")
    except Exception as e:
        print("   探测异常：%s: %s" % (type(e).__name__, str(e)[:80]))


if __name__ == "__main__":
    main()
