# -*- coding: utf-8 -*-
"""wechat_windows.py —— **按窗口标题定位会话窗口**（绕过像素定位的另一条路）

★为什么有它
  微信群聊（及私聊）的**会话行双击**会把它**变成一个单一独立弹窗**。
  实测：双击一次后，`EnumWindows` 立刻看到多出一个 Qt 顶层窗：
```
微信(pid=9336) 可见的 Qt 顶层窗口 = 2 个：
  hwnd=150667342  标题=「某群」     687x602   ← 主窗
  hwnd=41885108   标题=「某会话」   613x647   ← ★双击后新出现的独立窗口
```
  ⇒ 三点全部证实：① 双击会话 ⇒ **出现新的独立顶层窗口**；② **该窗口标题 = 会话名**；
     ③ 句柄独立、可被 `EnumWindows` 找到。

★这条路的价值（**对"单群 / 少群"场景可能是最优解**）
  · **"当前打开的是哪个会话"** ⇒ 读窗口标题（一次 `GetWindowText`，不需要截图/OCR/比对）
  · **"切到目标群"**        ⇒ `EnumWindows` 找标题匹配的窗口 ⇒ 激活（**不需要点会话列表**）
  ⇒ **完全没有"像素精度"问题**（窗口边界由系统给），也**不依赖 wxocr**（零第三方、零灰色操作）。

⚠️ 边界（实测/未实测，如实标注）
  · **依赖用户先把目标会话双击成独立窗口**（配置成本；可做成一次性引导）；
  · **主窗标题也 = 当前会话名**（实测 `某群`）⇒ 所以"标题匹配"要**同时考虑主窗与独立窗**；
  · **微信重启后独立窗会消失**（重启 ⇒ 关窗 ⇒ 回主窗，须再双击）⇒ **已实现"自愈重开"**：
    找不到独立窗（或只命中主窗）时 `send_to_popup` 现场走 `ensure_popup`（认会话 → 双击列表行 → 开窗），
    见本文件 `send_to_popup` 第 ① 步；⛔ 调用方**必须把 `adapter`/`row_order` 透传下去**，
    否则自愈会死在 `step="locate"`。
    ⛔ **任何窗口句柄都不缓存，每次按标题实时查找**（实测跨重启 `150667342 → 5251730`）。
    离线判据：`scripts/session_order_selftest.py` / `scripts/frame_free_send_selftest.py`；
    **"重启微信后再发能否自动重开"要真机窗口才能验 ⇒ 未复跑**（④ 档，等对面一次）。
  · **独立窗内发送**走投递链，全链路 `step` 可观测（`send_to_popup` 的返回结构）；真机复跑同样待对面。
"""
from __future__ import annotations

import ctypes as _ct
import ctypes.wintypes as _wt
import threading as _threading
import logging

log = logging.getLogger("persona-morph")

_user32 = _ct.windll.user32
_G32 = _ct.windll.gdi32

#: 微信主窗 + 独立会话窗的类名（实测 4.1.15.8 都是它）
WECHAT_QT_CLASS = "Qt51514QWindowIcon"

#: ⛔ **非会话窗黑名单**（类名**含**任一片段 ⇒ 一律排除）。
#:
#: 为什么必须有它：微信进程里的 `WxTrayIconMessageWindow`
#: （类名 `Qt51514WxTrayIconMessageWindowClass`）**同时满足**
#: 「属于微信进程」「`IsWindowVisible=1`」「类名含 Qt」「标题非空」
#: ⇒ 仅靠"进程 + 可见"两道闸**挡不住它**，会被当成会话窗返回。
#: 另有 `IME` / `MSCTF` / `*SystemMessage` / `DisplayICC` / `Base_Power`
#: 等同族的系统窗也长在微信进程里。（与 `scripts/collect_report.py` 的 `_OS_CLS` 同源。）
_SYS_WIN_KEYS = ("WxTrayIcon", "IME", "MSCTF", "SystemMessage",
                 "DisplayICC", "Base_Power")

_WNDENUMPROC = _ct.WINFUNCTYPE(_ct.c_bool, _ct.c_int, _ct.c_int)

# ⛔ 实测修 bug（`grab_window` 偶发崩溃）：
#   `gdi32.SelectObject` / `CreateCompatibleDC` / `CreateCompatibleBitmap` / `GetWindowDC`
#   都返回/接收 **64 位句柄**，但 `ct.windll` 默认按 `c_int`(32 位) 传参 ⇒ 句柄值一旦超过 2^31
#   就抛 `ctypes.ArgumentError: OverflowError: int too long to convert`。
#   现象：同一段代码平时好使、**偶发**在某次抓图时崩（这次抓主窗 `hwnd=5251730` 崩了，
#   而此前抓独立窗一直好使）。⇒ 必须显式声明 argtypes/restype。
#   （与 `input_backend.py` 里 `PostMessageW` 的注释同一个坑，那边也栽过。）
_HANDLE = _ct.c_void_p
_user32.GetWindowDC.argtypes = [_HANDLE]
_user32.GetWindowDC.restype = _HANDLE
_user32.PrintWindow.argtypes = [_HANDLE, _HANDLE, _ct.c_uint]
_user32.PrintWindow.restype = _ct.c_bool
_G32.CreateCompatibleDC.argtypes = [_HANDLE]
_G32.CreateCompatibleDC.restype = _HANDLE
_G32.CreateCompatibleBitmap.argtypes = [_HANDLE, _ct.c_int, _ct.c_int]
_G32.CreateCompatibleBitmap.restype = _HANDLE
_G32.SelectObject.argtypes = [_HANDLE, _HANDLE]
_G32.SelectObject.restype = _HANDLE
_G32.GetDIBits.argtypes = [_HANDLE, _HANDLE, _ct.c_uint, _ct.c_uint,
                           _ct.c_void_p, _ct.c_void_p, _ct.c_uint]
_G32.GetDIBits.restype = _ct.c_int
_G32.DeleteObject.argtypes = [_HANDLE]
_G32.DeleteObject.restype = _ct.c_bool
_G32.DeleteDC.argtypes = [_HANDLE]
_G32.DeleteDC.restype = _ct.c_bool
# ⛔ 同上：`ReleaseDC` 第 1 参是 HWND、第 2 参是 HDC，**都是 64 位句柄**，
#   不声明就会 `OverflowError`（实测：抓第二帧时崩在 `ReleaseDC(hwnd, hdc)`）。
_user32.ReleaseDC.argtypes = [_HANDLE, _HANDLE]
_user32.ReleaseDC.restype = _ct.c_int


class _RECT(_ct.Structure):
    _fields_ = [("left", _ct.c_long), ("top", _ct.c_long),
                ("right", _ct.c_long), ("bottom", _ct.c_long)]


def list_wechat_windows(image_name: str = "Weixin.exe") -> list:
    """枚举**所有微信进程的会话顶层窗口** ⇒ `[{hwnd, pid, cls, title, w, h}]`。

    三道闸（缺一不可）：
      ① 属于微信进程；
      ② **可见**；
      ③ **类名以 `Qt` 开头、且不含 `_SYS_WIN_KEYS` 里的片段**
         （托盘提示窗 / 输入法 / 系统消息窗等都不是会话窗 ——
          它们同样长在微信进程里、同样可见、类名同样含 `Qt`，
          **只靠 ①② 挡不住**，必须按类名排除）。
    """
    # 目标 pid 集合：按进程名收集（避免把别的进程窗口混进来）
    pids = set()
    for pid in _enum_pids(image_name):
        pids.add(pid)
    if not pids:
        return []
    out = []

    def _cb(hwnd, _lp):
        pid = _wt.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, _ct.byref(pid))
        if pid.value not in pids:
            return True
        if not _user32.IsWindowVisible(hwnd):
            return True
        cls = _ct.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, cls, 256)
        # ⛔ 闸③：会话窗一律是 Qt 窗；非 Qt 的、以及命中了系统窗黑名单的，全排除。
        #   （`WxTrayIconMessageWindow` 的类名含 Qt 且可见 ⇒ 只靠这里的前两闸挡不住。）
        if not cls.value.startswith("Qt"):
            return True
        if any(k in cls.value for k in _SYS_WIN_KEYS):
            return True
        title = _ct.create_unicode_buffer(512)
        _user32.GetWindowTextW(hwnd, title, 512)
        r = _wt.RECT()
        _user32.GetWindowRect(hwnd, _ct.byref(r))
        out.append({
            "hwnd": int(hwnd), "pid": int(pid.value), "cls": cls.value,
            "title": title.value, "w": int(r.right - r.left), "h": int(r.bottom - r.top),
        })
        return True

    _user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    return out


def _enum_pids(image_name: str) -> list:
    """用 `tasklist` 取该进程名的所有 pid（兼容多开/多账号）。

    ⚠️ `tasklist` 在中文 Windows 上输出是 **GBK**（不是 UTF-8）⇒ 必须显式解码，
    否则会 `UnicodeDecodeError`（实测踩过）。
    """
    import os
    import subprocess
    try:
        p = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % image_name, "/FO", "CSV"],
                           capture_output=True,
                           creationflags=(0x08000000 if os.name == "nt" else 0))
        txt = (p.stdout or b"").decode("gbk", "ignore")
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return []
    pids = []
    for line in txt.splitlines()[1:]:
        cols = [c.strip('"') for c in line.split('","')]
        if len(cols) >= 2 and cols[1].isdigit():
            pids.append(int(cols[1]))
    return pids


def strip_unread_suffix(title: str) -> str:
    """剥掉标题尾部的未读计数（实测独立窗标题形如 `某会话 (3)`）。

    ⛔ 为什么必须有它：独立会话窗的标题**带未读数** ——
       `GetWindowText` 拿到的是 `某会话 (3)` 而不是 `某会话`。
       而主窗标题**不带**（实测 `某群`）。⇒ 直接精确比对会**匹配失败**。
    判据：只剥**末尾**的 ` (纯数字)`（会话名本身可能含括号 ⇒ 不能全局替换）。
    """
    import re as _re
    return _re.sub(r"\s*[（(]\s*\d+\s*[)）]\s*$", "", (title or "").strip())


def find_chat_window(name: str, exact: bool = True) -> dict:
    """按**会话名**找窗口 ⇒ `{hwnd, title, exact, candidates, is_popup}`；找不到给 `hwnd=0`。

    ⛔⛔ **旧注释是错的（实测钉死）**：
      · 原来写"主窗的标题**也是**当前打开的会话名" —— **不成立**。
        实测 `GetWindowTextW(主窗)` 恒为 **"微信"**（不是会话名）。
      · 原来"优先取面积较小者当独立窗" —— **不可靠**：
        实测独立窗 `1113×910` 可能**比主窗 `987×1016` 面积更大**。

    ⇒ 现在**优先走 `find_popup`**（它按 `_is_main_window` **显式排除主窗**，判据可靠）；
      只有 `find_popup` 没命中时，才退化为"按名匹配 + 面积最小"，并**如实标注** `is_popup=False`
      （调用方据此判断"这是不是真独立窗"）。
    """
    wins = [w for w in list_wechat_windows() if w["title"].strip()]
    _want = (name or "").strip()
    # ① 先按"独立窗"找（可靠）
    pop = find_popup(_want)
    if pop.get("hwnd"):
        return {"hwnd": int(pop["hwnd"]), "title": pop.get("title"), "exact": True,
                "is_popup": True, "candidates": pop.get("candidates")}
    # ② 退化：按名匹配（可能是主窗、或标题带后缀）
    if exact:
        hit = [w for w in wins if strip_unread_suffix(w["title"]) == _want]
    else:
        n = _want.lower()
        hit = [w for w in wins if n and n in strip_unread_suffix(w["title"]).lower()]
    if not hit:
        return {"hwnd": 0, "title": "", "exact": bool(exact), "is_popup": False,
                "candidates": [w["title"] for w in wins]}
    hit.sort(key=lambda w: (w["w"] * w["h"]))
    return {"hwnd": hit[0]["hwnd"], "title": hit[0]["title"], "exact": bool(exact),
            "is_popup": not _is_main_window(int(hit[0]["hwnd"])),
            "candidates": [w["title"] for w in wins]}


def main():
    """自检/演示：列出当前所有微信窗口 + 试按标题找。"""
    import sys
    wins = list_wechat_windows()
    print("微信可见顶层窗口 %d 个：" % len(wins))
    for w in wins:
        print("  hwnd=%-12d %-32s %-28s %dx%d"
              % (w["hwnd"], w["cls"][:30], w["title"][:26], w["w"], w["h"]))
    if len(sys.argv) > 1:
        r = find_chat_window(sys.argv[1])
        print("\n按标题找 %r ⇒ %s" % (sys.argv[1], r))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())


# ─────────────────────────────────────────────────────────────────────────────
# 抓图（**必须按 DPI 倍率建位图** —— 这是踩过一个大坑后定下来的）
# ─────────────────────────────────────────────────────────────────────────────

#: **本进程是否已设 DPI 感知**。
#:
#: ⭐⭐ **唯一权威来源是 `input_backend.DPI_MODE`**：
#:   `input_backend` 在**它自己的模块级**（import 时）就调 `_lock_dpi()` 并且**检查返回值**
#:   —— 那才是"真的锁住了"。本模块**不要再自己维护一套标志**（会谎报，踩过两次坑）：
#:     · `set_dpi_aware()` 在 import 后调用会静默失败（API 返回成功但不生效）；
#:     · `GetThreadDpiAwarenessContext` 显示 PER_MONITOR_AWARE 也不代表 `GetWindowRect` 给物理。
#:   ⇒ **本模块在 import 时就 `import input_backend`**（确保 DPI 已锁），
#:      并把 `_DPI_AWARE` 直接取自它。
_DPI_AWARE = False
try:
    from agent import input_backend as _ib_for_dpi
    _DPI_AWARE = (str(getattr(_ib_for_dpi, "DPI_MODE", "none")) != "none")
except Exception:
    _DPI_AWARE = False


def dpi_mode() -> str:
    """当前 DPI 锁定方式（`input_backend.DPI_MODE`；`none` = 没锁上）。"""
    try:
        from agent import input_backend as _ib
        return str(getattr(_ib, "DPI_MODE", "none"))
    except Exception:
        return "none"


#: （`_DPI_AWARE` 的来源见文件上方：直接取自 `input_backend.DPI_MODE`，不再自维护）


def set_dpi_aware() -> bool:
    """**兼容旧调用**：确保 DPI 已锁（现在是 `input_backend` 在 import 时锁的）。

    ⛔ 历史教训：本函数原来"进程已初始化 GUI 后调 `SetProcessDpiAwarenessContext`"
      ⇒ **静默失败**（API 看不出来），却把 `_DPI_AWARE` 置 True ⇒ 谎报 ⇒ 抓图尺寸错、
      落点全线错位（这就是"点不准"反复复发的总根源之一）。
    ⇒ 现在**返回真实状态**（`input_backend.DPI_MODE != none`），不做任何自欺。
      若返回 False，说明进程启动太晚、锁不上 —— 这时**调用方必须走"未感知"分支**
      （`GetWindowRect` 是逻辑值，抓图要乘 `window_scale`），而不是假装已感知。
    """
    global _DPI_AWARE
    try:
        from agent import input_backend as _ib
        _DPI_AWARE = (str(getattr(_ib, "DPI_MODE", "none")) != "none")
    except Exception:
        pass
    return bool(_DPI_AWARE)



def dpi_aware() -> bool:
    """当前进程是否已设 DPI 感知（决定"GetWindowRect 是物理还是逻辑"）。"""
    return bool(_DPI_AWARE)


def window_scale(hwnd: int) -> float:
    """窗口的 DPI 缩放倍率（`GetDpiForWindow/96`）；取不到则退回 1.0。

    ⛔ **为什么必须按它建位图**（实测，报告 §52）：
      `GetWindowRect` 返回的是**逻辑像素**（已按 DPI 折算），而窗口**按物理像素渲染**。
      用逻辑尺寸的位图去接 `PrintWindow` ⇒ **只画得出左上角约 1/scale² 的区域**，其余留底色。
      实测（本机 DPI=144 ⇒ scale=1.5）：
        · 逐字节地看"内容像素占比"（＝除背景色以外的像素比例）
        独立窗 逻辑尺寸位图 → **1.98%**；物理尺寸位图 → **11.12%**（正常窗口约 8~20%）
        · 主窗因内容多（37%）**掩盖了这个 bug** —— 所以"主窗一直能用"不等于"抓法是对的"。
      ⇒ **凡抓图，位图一律用 `逻辑尺寸 × window_scale()`**。
    """
    try:
        dpi = _user32.GetDpiForWindow(hwnd)
        if dpi and dpi > 0:
            return float(dpi) / 96.0
    except Exception:
        pass
    return 1.0


def process_is_dpi_aware() -> bool:
    """`GetWindowRect` 是否返回**物理**像素 —— **以 `input_backend.DPI_MODE` 为准**。

    ⛔⛔ **不要再用"推断"的办法判**（两次踩坑的结论）：
      · `GetThreadDpiAwarenessContext` → 显示 PER_MONITOR_AWARE(3)，
        **但 `GetWindowRect` 仍然给逻辑值**（线程覆盖上下文 ≠ 进程清单）⇒ 谎报；
      · `GetWindowRect` vs `GetClientRect` 边框差 → 前台窗口未必是微信 ⇒ 算歪。
      · `set_dpi_aware()` 事后调用 → API 静默失败 ⇒ 谎报（且旧实现只记标志位）。

    ⇒ **唯一权威**：`input_backend` 在**它自己的模块级**（import 时）调 `_lock_dpi()`
      并且**检查返回值** ⇒ `DPI_MODE != "none"` 才是真的锁上了。
      本模块已经在文件顶部 `import input_backend` ⇒ 到这里 DPI 必然已锁（或确实锁不上）。
    """
    return bool(_DPI_AWARE)


def grab_window(hwnd: int, physical: bool = True, flag: int = 2):
    """抓某窗口 ⇒ `(PIL.Image | None, meta)`；**按 DPI 倍率建位图**（见 `window_scale`）。

    `meta` 含 `logical`(GetWindowRect 的逻辑尺寸) / `scale` / `physical_size`，
    供调用方把"图上像素"换算回"逻辑/客户区坐标"（`逻辑 = 图上 / scale`）。
    """
    try:
        from PIL import Image  # noqa: F401
    except Exception:
        return None, {"err": "PIL 不可用"}
    # ⛔ 最小化窗口**抓不到有效画面**，但 `GetWindowRect` 仍返回最小化时的占位矩形
    #   （本机实测 `-32000,-32000,-31763,-31961` ⇒ 图 237×39）。若不拦住，调用方拿到的是
    #   **一张尺寸离谱的"图"**（不是 None），照它量行高/找绿带就会得出没意义的结果。
    #   ⇒ 这里如实返回 None ＋ 原因，让调用方走各自的"读不到"分支。
    try:
        if _user32.IsIconic(hwnd):
            return None, {"err": "窗口已最小化（最小化时没有有效画面）", "minimized": True}
    except Exception: # noqa: BLE001
        pass
    _wt_rect = _wt.RECT()
    _user32.GetWindowRect(hwnd, _ct.byref(_wt_rect))
    r = _wt_rect
    lw, lh = int(r.right - r.left), int(r.bottom - r.top)
    if lw <= 0 or lh <= 0:
        return None, {"err": "窗口尺寸异常", "logical": (lw, lh)}
    # ⭐ **语义：保证图是"屏幕物理像素"，与 `window_rect` 同一坐标系**。
    #   ⛔⛔ 不要再乘 `window_scale`（三重铁证推翻）：
    #     `input_backend` 在 import 时已锁 PerMonitorV2（且检查返回值）⇒
    #     `GetWindowRect` **本来就返回物理像素** ⇒ 再乘 1.5 = **双重放大**。
    #     实测：按 987×1016 抓图内容占比 48.29%（铺满）；按 ×1.5 的 1480×1524 抓反而 44.46%
    #     ⇒ 987×1016 **就是真物理**。详见 `window_rect_physical` 的 docstring。
    #   ⇒ 除显式要"逻辑图"（`physical=False`）外，位图一律按 `GetWindowRect` 原尺寸建。
    #
    #   ⚠️ 这里的 `sc=1.0` **不是"写死的缩放倍率"**，而是"图的尺寸 = 窗口物理尺寸"这个**恒等式**：
    #      `GetWindowRect` 在本进程（锁了 PerMonitorV2）已经是**物理像素**，
    #      所以"图上像素 : 屏幕物理像素"就是 **1 : 1**，与用户的缩放档**无关**
    #      （100%/150%/225% 都一样 ⇒ 这正是本函数的正确性所在）。
    #      ⇒ 若哪天要按"逻辑尺寸"建图（`physical=False`），才需要在这里除以实测倍率
    #        `display_metrics.scale()`；**切忌**无脑乘一个 digit，那是 已推翻的错。
    _px_per_window_unit = 1.0          # 屏幕物理像素 / GetWindowRect 单位（锁 DPI 后恒为 1）
    sc = _px_per_window_unit
    w, h = max(1, int(lw * sc)), max(1, int(lh * sc))
    hdc = _user32.GetWindowDC(hwnd)
    mdc = _G32.CreateCompatibleDC(hdc)
    bmp = _G32.CreateCompatibleBitmap(hdc, w, h)
    _G32.SelectObject(mdc, bmp)
    ok = _user32.PrintWindow(hwnd, mdc, flag)

    class _BMIH(_ct.Structure):
        _fields_ = [("biSize", _ct.c_uint32), ("biWidth", _ct.c_long), ("biHeight", _ct.c_long),
                    ("biPlanes", _ct.c_ushort), ("biBitCount", _ct.c_ushort),
                    ("biCompression", _ct.c_uint32), ("biSizeImage", _ct.c_uint32),
                    ("biXPelsPerMeter", _ct.c_long), ("biYPelsPerMeter", _ct.c_long),
                    ("biClrUsed", _ct.c_uint32), ("biClrImportant", _ct.c_uint32)]
    bi = _BMIH()
    bi.biSize = _ct.sizeof(_BMIH)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = _ct.create_string_buffer(w * h * 4)
    _G32.GetDIBits(mdc, bmp, 0, h, buf, _ct.byref(bi), 0)
    from PIL import Image as _I
    img = _I.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    _G32.DeleteObject(bmp)
    _G32.DeleteDC(mdc)
    _user32.ReleaseDC(hwnd, hdc)
    return img, {"ok": bool(ok), "logical": (lw, lh), "scale": sc, "physical_size": (w, h),
                 "dpi_aware": bool(_DPI_AWARE)}


def content_ratio(img) -> tuple:
    """图上"除背景以外"的像素占比（%）+ 主色 + 主色占比 —— **抓图质量体检**。

    用途：实测"逻辑尺寸位图"会掉到 1~2%、"物理尺寸位图"回到 8~20% ⇒
     ⇒ **任何抓图改动都该用这个数验收**，而不是"看起来有图就行"。
    """
    from collections import Counter
    px = img.load()
    w, h = img.size
    c = Counter()
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b = px[x, y][:3]
            c[(r // 8 * 8, g // 8 * 8, b // 8 * 8)] += 1
    tot = sum(c.values()) or 1
    bg, bgn = c.most_common(1)[0]
    keep = n = 0
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            r, g, b = px[x, y][:3]
            n += 1
            if not (abs(r - bg[0]) <= 14 and abs(g - bg[1]) <= 14 and abs(b - bg[2]) <= 14):
                keep += 1
    return 100.0 * keep / max(1, n), bg, 100.0 * bgn / tot


# ─────────────────────────────────────────────────────────────────────────────
# 独立弹窗内的两个关键控件（**比例坐标**，跨尺寸稳定）
# ─────────────────────────────────────────────────────────────────────────────

#: 「发送」按钮中心的**比例位置**（实测标定，图 1182×970；见报告 §56）
SEND_BTN_REL = (0.938, 0.948)
#: 输入框内可点的位置（避开工具栏与发送键）：中点偏上
INPUT_REL = (0.50, 0.85)
#: 输入框矩形（比例）
INPUT_BOX_REL = (0.019, 0.789, 0.981, 0.974)

# ── ★★ 独立窗"输入框右键"的候选落点（实测标定） ────────────────────
#: 每项 = `(x 比例, y 比例【**从窗口底边往上量**】)`，**按序试，谁先弹出菜单谁生效**。
#:
#: ⛔⛔ **为什么必须"从底边量"、且必须能扫**：
#:   旧写法把落点写死成 `(0.50, 0.88)`（从**顶边**量）⇒ 实测**踩到输入框之外**，
#:   右键**弹不出菜单**（日志：`右键之后没出现菜单窗（粘贴）`）⇒ 整条"独立窗发图"死掉。
#:   **根因**：独立窗的**聊天区高度是可伸缩的**，输入框+工具栏高度基本固定
#:   ⇒ 输入框在窗口里的**比例位置随窗口高度剧烈漂移**：
#:     · 窗口 562×551：输入框 ≈ y 338~492（顶边比例 0.61~0.89）
#:     · 窗口 562×677：输入框 ≈ y 433~582+（顶边比例 0.64~0.86 还能中，但边界已不稳）
#:   而**离底边的距离一直很稳**（≈ 输入框底 = 底边 - 0.10H）。
#:   ✅ 实测（`某群` 独立窗，两种窗口尺寸都验过）：
#:      从底边 0.16H 处右键 ⇒ **一枪弹出菜单**，内容 = `粘贴 / 边写边译`
#:      ⇒ 点「粘贴」→ 回车 ⇒ **DB 出现 `[动画表情]`**（动图按表情收，动效保住）。
POPUP_INBOX_REL = ((0.50, 0.16), (0.50, 0.22), (0.30, 0.16), (0.70, 0.22),
                   (0.50, 0.28), (0.50, 0.10), (0.30, 0.24), (0.70, 0.30))


class _ResetStub:
    """给「退出多选」那一枪用的**最小适配器** —— 只提供 `_click_posted` 这一个口。

    ⚠️ 为什么不用真的 `WeChatAdapter`：`wechat.py` 与 `wechat_windows.py` **互相 import**
      （本模块顶部就 `from agent import wechat`），在函数里现造一个适配器会把两条链绑死、
      还会顺带触发一堆不必要的东西（建 GUI、校准布局…）。而复位只需要"投一枪点击"。
    ⛔ 这里**不做** `_click_posted` 那套"点后自检有没有冒出不该出现的新窗"：
      ✕ 那一枪点完**本来就不该有新窗**，但工具条消失的过程中微小时序会误判 ⇒ 反而停手。
      复位是否成功由**复探输入框**决定（`_composer_reset_multiselect` 内），那才是真判据。
    """

    def _click_posted(self, backend, hwnd, pt, tag: str = "", right: bool = False, **kw):
        try:
            ok, why = backend.click(hwnd, pt, right=right)
            return bool(ok), str(why)
        except Exception as e:  # noqa: BLE001
            return False, "%s 点击异常：%s" % (tag, e)


def popup_input_points(rect) -> list:
    """由窗口矩形算出**独立窗输入框**的候选屏幕落点（按序试）。

    `rect = (left, top, right, bottom)`（屏幕物理像素）。
    y 一律**从底边往上量**（见 `POPUP_INBOX_REL` 的说明）。
    """
    l, t, r, b = (int(v) for v in tuple(rect)[:4])
    W_ = max(1, r - l)
    H_ = max(1, b - t)
    return [(int(l + W_ * fx), int(b - H_ * fy)) for fx, fy in POPUP_INBOX_REL]


def is_green_px(p, d: int = 20) -> bool:
    """这个像素是不是"绿"（用于判**发送键是否激活**）。"""
    try:
        r, g, b = p[0], p[1], p[2]
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return False
    return (g > r + d) and (g > b + d)


#: 「发送」按钮的**采样矩形**（比例，围绕按钮中心稍放大，确保覆盖"键"而不越界）
SEND_BTN_BOX_REL = (0.895, 0.925, 0.980, 0.970)


def send_button_green(img, box=SEND_BTN_BOX_REL, min_ratio: float = 0.30) -> bool:
    """**「发送」键是否已激活（变绿）** ⇒ 等价于"输入框里确实有字"。

    ⛔ 为什么用它（实测已证）：
      输入框里有文字时，「发送」键**变绿、字变白**；空着时两处都是灰、字只比键深一点。
      实测（未输入）：按钮区域 `(214,214,214)`（字） / `(236,236,236)`（键）⇒ 都灰；
      绿色参照（绿气泡）= `(175,239,165)`。

    ⚠️ **为什么不是"取中心像素的中位色"**：
      激活态是「**绿底 + 白字**」⇒ 中心像素可能正好落在**白字**上 ⇒ 中位色判绿会**漏判**。
      ⇒ 改成 **统计矩形内"绿像素"的占比**（`>= min_ratio` 才算激活）。
      这样"绿底白字"里绿仍占多数，"灰底灰字"里几乎没有绿 ⇒ 两侧都稳。

    ★这是**投递键盘"是否真的打进去了"的可靠确认**（比 OCR 回读输入框更快更稳：
      禁用态"发送"二字对比度极低、`pane_text` 实测返回空，见报告 §56.3）。
    ⇒ **用法**：投递输入后**轮询本函数**，变绿才算成功；不变绿就重试或改真实键盘。
    """
    try:
        w, h = img.size
        x0, y0 = int(w * box[0]), int(h * box[1])
        x1, y1 = int(w * box[2]), int(h * box[3])
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, max(x1, x0 + 1)), min(h, max(y1, y0 + 1))
        n = green = 0
        step = max(1, (x1 - x0) // 24)
        for yy in range(y0, y1, step):
            for xx in range(x0, x1, step):
                n += 1
                if is_green_px(img.getpixel((xx, yy))[:3]):
                    green += 1
        return bool(n) and (float(green) / n >= float(min_ratio))
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return False


def send_button_green_ratio(img, box=SEND_BTN_BOX_REL) -> float:
    """返回采样矩形内的**绿像素占比**（诊断用；正常态≈0，激活态应显著>0）。"""
    try:
        w, h = img.size
        x0, y0 = max(0, int(w * box[0])), max(0, int(h * box[1]))
        x1, y1 = min(w, int(w * box[2])), min(h, int(h * box[3]))
        n = green = 0
        step = max(1, (x1 - x0) // 24)
        for yy in range(y0, y1, step):
            for xx in range(x0, x1, step):
                n += 1
                if is_green_px(img.getpixel((xx, yy))[:3]):
                    green += 1
        return (float(green) / n) if n else 0.0
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return 0.0


def rel_to_px(img_or_size, rel):
    """比例 ⇒ 图上像素（供换算到客户区/屏幕坐标用）。"""
    try:
        w, h = img_or_size.size
    except AttributeError:
        w, h = img_or_size
    return int(w * rel[0]), int(h * rel[1])


def popup_controls(scale: float = 1.0) -> dict:
    """独立弹窗两个控件的**逻辑坐标**比例说明（供点击用）。

    `grab_window(physical=True)` 给的是**物理像素图** ⇒ 图上坐标除以 `scale` 才是**逻辑坐标**，
    再加窗口客户区在屏幕上的原点，即得**屏幕坐标**（注意 `GetWindowRect` 也是逻辑像素，两者自洽）。
    """
    return {
        "send_btn_rel": SEND_BTN_REL,
        "input_rel": INPUT_REL,
        "input_box_rel": INPUT_BOX_REL,
        "scale": float(scale),
        "note": "图上坐标 / scale = 逻辑坐标；逻辑坐标 + 窗口左上角(逻辑) = 屏幕坐标",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 顶层：向"独立弹窗"发一条消息（6 步链路，每步都有可观测验收）
# ─────────────────────────────────────────────────────────────────────────────

def send_to_popup(chat_name: str, text: str, *, timeout_s: float = 6.0,
                  clear_first: bool = True, dry_run: bool = False,
                  adapter=None, row_order=None) -> dict:
    """向 `chat_name` 对应的**独立弹窗**发一条消息 ⇒ `{ok, step, detail, ...}`。

    ⭐ **设计要点（都是踩坑换来的，见报告 §56~§64）**
      ① **只用 `send_text`（`WM_CHAR`），不用剪贴板** —— `WM_CHAR` 不碰剪贴板，
         不会与打架；`WM_PASTE` 那条路会覆盖用户剪贴板，**不进主路**。
      ② **发送前必须确认"字真进去了"**：靠 `send_button_green()`（绿占比 ≥0.30）
         —— 实测"API 返回 (True,'') 但字没进去"，**不能只看返回值**（§59）。
      ③ **目标窗口必须是"独立弹窗"**（有 `标题=会话名`）；主窗里当前会话恰好同名时会误命中，
         故若命中的是"主窗且其当前会话＝该名"，也照样可用（行为等价）。
      ④ **失败即停**：任一步不过就返回，**绝不盲目点发送**。
      ⑤ **全程不动光标**（`MessageBackend.touches_cursor=False`）—— 注意口径：投递链的伪激活
         可能让微信**短暂置前约 1~3 秒后自动还回** ⇒ 对外文案一律写成这个口径，
         **不宣称"全程不占前台"**（过头话，见 `scripts/background_selftest.py` F1~F6）。

    参数
      `clear_first` —— 若输入框已有字（绿占比≥0.30），先清空（End+退格）再投。
      `dry_run`     —— 只做到"字已进入输入框"这一步就**清空并返回**，**不点发送**（用于验收）。
      `adapter`     —— ⭐ **新增（修致命缺陷）**：自愈开窗要走
                       `ensure_popup` 的"DB 认序号"分支，**必须给 adapter**；
                       旧代码**没往下传** ⇒ 自愈开窗**必然**在 `step="locate"` 失败
                       （实测：`没有独立窗，且未给 adapter/row_order ⇒ 无法定位会话行`）。
      `row_order`   —— 已知 DB 行号时直接给（跳过 adapter 读库），与 `ensure_popup` 同义。
    """
    out = {"ok": False, "step": "", "detail": "", "hwnd": 0, "chat": chat_name}
    # ① 定位（★ **自愈**：万一独立窗被关掉/不在，就现场把它重新打出来）
    hit = find_chat_window(chat_name)
    hwnd = int(hit.get("hwnd") or 0)
    if not hwnd or not hit.get("is_popup"):
        # 不是独立窗（没窗 / 命中主窗）⇒ 走 ensure_popup（认会话→双击→开窗）
        # ⛔ 必须把 `adapter`/`row_order` 透传下去，否则 ensure_popup 走到
        #    "未给 adapter/row_order ⇒ 无法定位会话行" 就死在那儿（修）。
        ep = ensure_popup(chat_name, timeout_s=timeout_s,
                          adapter=adapter, row_order=row_order)
        out["ensure_popup"] = {k: ep.get(k) for k in ("ok", "how", "step", "detail", "click_pt")}
        if ep.get("ok") and ep.get("hwnd"):
            hwnd = int(ep["hwnd"])
            hit = {"title": chat_name, "is_popup": True}
        else:
            out.update(step="locate",
                       detail="没有独立窗；自愈开窗也失败（%s）⇒ 不盲投。候选=%s"
                              % (ep.get("detail") or ep.get("step"), hit.get("candidates")))
            return out
    out["hwnd"] = hwnd
    out["title"] = hit.get("title")
    try:
        from agent import input_backend as IB
        be = IB.select_backend()
    except Exception as e:
        out.update(step="backend", detail="取输入后端失败：%s" % str(e)[:80])
        return out

    # ⛔ **第 223 项：目标独立窗停在「多选 / 转发」模式 ⇒ 先复位再打。**
    #   该模式下输入框整条不存在（底部换成工具条）⇒ 打字打不进、发送键恒灰
    #   ⇒ 表观症状是"发了没反应"。这里有**正面证据**（底部那排工具条），
    #   所以允许据此**动手复位**；复位成功后照常发，**绝不据此拒发**（第 215 项定调）。
    #   ⛔ 复位手法**不许用 ESC**（实测会把独立窗整个关掉，退多选变成关窗）。
    #   ⚠️ 帧必须来自**这个独立窗**（`grab_window(hwnd)`）——`gui` 那套抓的是主窗，
    #      拿主窗的帧去判独立窗的底部，必然判不出来。
    try:
        from agent import wechat as _wxm
        _im_fb, _m_fb = grab_window(hwnd, physical=False)
        _fb = _wxm._probe_forward_bar_img(_im_fb, gui=None) if _im_fb is not None else None
    except Exception:
        _fb = None
    if _fb:
        log.info("独立窗停在「多选/转发」模式（底部工具条 %s）⇒ 先复位再打字", _fb)
        try:
            from agent import input_backend as _ib2
            _be2 = _ib2.select_backend()
            _stub = _ResetStub()
            _rok, _rwhy = _wxm._composer_reset_multiselect(
                _stub, _be2, int(hwnd), tag="发送前退出多选", img=_im_fb)
        except Exception as _eRS:  # noqa: BLE001
            _rok, _rwhy = False, "复位自身出错：%s" % str(_eRS)[:80]
        out["multiselect_reset"] = {"ok": bool(_rok), "why": str(_rwhy)[:140]}
        log.info("退出多选：%s", _rwhy)
        if not _rok:
            out.update(step="multiselect",
                       detail=("目标窗停在「多选/转发」模式，且**没能复位**（%s）——"
                               "这种窗口没有输入框，打不进字；按纪律不硬打、也不假报成功"
                               % str(_rwhy)[:90]))
            return out
        import time as _t3
        _t3.sleep(0.3)

    def _ratio():
        im, _m = grab_window(hwnd, physical=False)
        return (send_button_green_ratio(im) if im else -1.0), im

    # ⭐⭐ **新增：最小化/看不到画面 ⇒ 自动降级到"盲投 + DB 回读"**
    #   用户终极目标口径：
    #     「它最小化了，也只是最小化了这个窗口，**这个窗口还能发信，还能发表情**……
    #       我平时把窗口全部最小化，然后该做啥事儿做啥事儿，最小化就能发信」
    #   ⛔ 为什么必须分流：本函数后面的判据全是**像素**（`send_button_green_ratio`），
    #     而窗口最小化时 `grab_window` 只返回任务栏缩略图（实测 237×39、内容全黑）
    #     ⇒ 绿占比恒 0 ⇒ **必然误判成"字没进去"**、白白中止。
    #   ✅ 出路：走 `send_to_popup_blind`（判据换成 **DB 回读**，不依赖画面）。
    if _is_iconic(hwnd):
        out["degraded"] = "iconic"
        return send_to_popup_blind(chat_name, text, adapter=adapter,
                                   timeout_s=timeout_s)

    # ② 基线与可选清空
    r0, _im = _ratio()
    if r0 < 0:
        # 抓得到窗口对象、但画面无效（尺寸异常小）⇒ 同样当作"看不到画面"降级
        im_t, _mt = grab_window(hwnd, physical=False)
        if im_t is None or min(im_t.size) < 60:
            out["degraded"] = "no_capture(size=%s)" % (im_t.size if im_t else None)
            return send_to_popup_blind(chat_name, text, adapter=adapter,
                                       timeout_s=timeout_s)
        out.update(step="grab", detail="抓图失败")
        return out
    if r0 >= 0.30:
        if not clear_first:
            out.update(step="precheck", detail="输入框里已有字（绿占比 %.3f），且 clear_first=False" % r0)
            return out
        try:
            be.keys(hwnd, [0x23])          # VK_END
            be.keys(hwnd, [0x08] * 40)     # 连续退格（投递不携带修饰键 ⇒ 不能用 Ctrl+A）
        except Exception:
            pass
        import time as _t
        _t.sleep(0.4)
        r0b, _ = _ratio()
        if r0b >= 0.30:
            out.update(step="clear", detail="清空失败（绿占比仍 %.3f）" % r0b)
            return out
        out["cleared"] = True

    # ③ 投递（只用 WM_CHAR）
    try:
        res = be.send_text(hwnd, text)
    except Exception as e:
        out.update(step="send_text", detail="投递异常：%s" % str(e)[:80])
        return out
    out["send_text_ret"] = str(res)
    # ④ 轮询确认"字已进去"
    import time as _t
    t0 = _t.time()
    r1 = -1.0
    while _t.time() - t0 < float(timeout_s):
        r1, _ = _ratio()
        if r1 >= 0.30:
            break
        _t.sleep(0.25)
    out["green_ratio_after_type"] = round(r1, 4)
    if r1 < 0.30:
        out.update(step="verify_typed", detail="投递后发送键未变绿（绿占比 %.3f）⇒ 字没进去，已中止，未点发送" % r1)
        return out
    out["ok"] = True
    out["step"] = "typed"

    if dry_run:
        try:
            be.keys(hwnd, [0x23]); be.keys(hwnd, [0x08] * 40)
        except Exception:
            pass
        out["detail"] = "dry_run：字已成功进入输入框，已清空，**未点发送**"
        return out

    # ⑤ 点「发送」（图 = 整窗 ⇒ 屏幕坐标 = 窗口原点 + 图坐标；图用 physical=False 与逻辑一致）
    try:
        import ctypes as _ct
        import ctypes.wintypes as _wt
        _im, _meta = grab_window(hwnd, physical=False)
        rct = _wt.RECT()
        _user32.GetWindowRect(hwnd, _ct.byref(rct))
        sx = int(rct.left + _im.size[0] * SEND_BTN_REL[0])
        sy = int(rct.top + _im.size[1] * SEND_BTN_REL[1])
        cret = be.click(hwnd, (sx, sy), press_ms=120)
        out["click_ret"] = str(cret)
        out["click_pt"] = (sx, sy)
    except Exception as e:
        out.update(step="click_send", detail="点发送异常：%s" % str(e)[:80])
        return out
    _t.sleep(1.2)
    r2, _ = _ratio()
    out["green_ratio_after_send"] = round(r2, 4)
    if r2 >= 0.30:
        out.update(ok=False, step="verify_sent",
                   detail="点发送后输入框仍有字（绿占比 %.3f）⇒ 可能没发出去" % r2)
        return out
    out.update(step="sent", detail="已投递并点发送；输入框已清空（绿占比 %.3f）" % r2)
    return out


def is_popup_alive(hwnd: int) -> bool:
    """独立窗句柄是不是还活着（哪怕**已最小化**）？

    ⭐ 独立窗被最小化后，句柄仍然有效、仍可投递消息
    ⇒ **最小化 ≠ 不可用**。所以本函数**只判"窗口还在不在"**，不判可见性、不判最小化。
    """
    try:
        import ctypes as _ct
        u = _ct.windll.user32
        if not hwnd or not u.IsWindow(int(hwnd)):
            return False
        # 有标题 = 还是那个独立窗（防句柄被复用）
        buf = _ct.create_unicode_buffer(512)
        u.GetWindowTextW(int(hwnd), buf, 512)
        return bool((buf.value or "").strip())
    except Exception:
        return False


def send_to_popup_blind(chat_name: str, text: str, *, adapter=None,
                        timeout_s: float = 6.0, press_ms: int = 60) -> dict:
    """★ **最小化/不可见状态下的独立窗直发**（实测通过）。

    ⭐⭐ **为什么要有这条通路**：独立窗"一窗一会话"，标题即会话名，
      所以不需要任何"当前会话"判据；而最小化不影响消息投递。

    ⛔ **为什么不能复用 `send_to_popup`**：那条通路靠**像素判据**
      （`send_button_green_ratio` 看"字进没进输入框"）——
      而窗口**最小化时抓不到画面**（实测 `grab_window` 只返回 `237×39` 的任务栏缩略图，
      内容全黑 ⇒ 绿占比恒 0）⇒ 像素判据**必然误判成"字没进去"**。

    ✅ **本通路的判据是 DB 回读（唯一铁证，不依赖画面）**：
      ① 投 `WM_CHAR` 打字 → ② 投回车（`VK_RETURN`）真发
      → ③ 从**微信数据库回读**该会话最后几条，看有没有本条文本。
      实测（本机、`某群`、窗口 `IsIconic=1`）：
        `send_text → (True,'')`、`keys(ENTER) → (True,'')`，
        3 秒后 `_db_messages` 里出现该文本 ⇒ **最小化下发信成立**。

    ⚠️ **前提**：`hwnd` 必须是**独立弹窗**（一窗一会话 ⇒ 投错也不可能投到别人）。
      `adapter` 只用于 DB 回读校验；不给就跳过校验（此时**只报"已投递"，不报"已确认"**）。

    参数
      `press_ms` —— 每个键按住的毫秒（投递不等前台，默认 60 够）。
    返回
      `{ok, step, detail, hwnd, chat, db_verified, db_hit}`
    """
    out = {"ok": False, "step": "", "detail": "", "hwnd": 0, "chat": chat_name,
           "db_verified": False, "db_hit": ""}
    # ① 定位（用 find_popup：**只认非主窗**，主窗同名也不算）
    #   ⛔ 窗口标题只有**显示名** ⇒ username 要先解析（否则永远匹配不上）。
    hit = find_popup(resolve_title_name(chat_name, adapter))
    hwnd = int(hit.get("hwnd") or 0)
    if not hwnd:
        out.update(step="locate", detail="没有 %r 的独立窗（本通路不自动开窗；"
                                         "请先 ensure_popup）" % chat_name)
        return out
    out["hwnd"] = hwnd
    out["title"] = hit.get("title")
    out["iconic"] = int(_is_iconic(hwnd))

    # ★ **发之前的"最新序号"** ＝ "刚发的那一条"的定义（下面的回读判据用它划窗口）。
    #   ⛔ 必须在**投字之前**取：发完再取就成了"它的后排"，判据反而会把我们自己那一行排除掉。
    _base, _chat_id = 0, ""
    if adapter is not None:
        try:
            _chat_id = _resolve_chat_id(adapter, chat_name)
            # ⛔ **单位＝`local_id`**（与 `_db_tail_has` 的过滤口径一致）：
            #   原来这里取的是 `latest_seq()`（`sort_seq` 毫秒时间戳）⇒ 量纲不符 ⇒ 判据恒不成立。
            if _chat_id:
                _base = _db_latest_local_id(adapter, _chat_id)
        except Exception:  # noqa: BLE001
            _base = 0
    out["base_seq"] = _base

    try:
        from agent import input_backend as IB
        be = IB.select_backend()
    except Exception as e:  # noqa: BLE001
        out.update(step="backend", detail="取输入后端失败：%s" % str(e)[:80])
        return out

    # ② 先清空（End + 一串退格）—— 防上一次残留
    try:
        be.keys(hwnd, [0x23])                  # VK_END
        be.keys(hwnd, [0x08] * 60)             # VK_BACK ×60
    except Exception:
        pass
    import time as _t
    _t.sleep(0.2)

    # ③ 投字
    # ⛔ **这里只能用逐字投递（`send_text`），不许"顺手优化"成剪贴板粘贴** ——
    #    实测（本机、最小化档）：`be.paste_text(...)` 返回 `ok=True` 且剪贴板还原正常，
    #    但**回车后 8 秒库里没有新行**（`WM_PASTE` 在最小化窗上**送不进去**）；
    #    而同一档的逐字 `WM_CHAR` 能用（本链多次实测发出）。
    #    ⇒ 中文投递的剪贴板那条路只在**有画面/前台**的场景（如搜索浮层）成立。
    #    判据：`clipboard_selftest` 的 E 段盯着这条边界。
    try:
        r1 = be.send_text(hwnd, text)
    except Exception as e:  # noqa: BLE001
        out.update(step="send_text", detail="投递异常：%s" % str(e)[:80])
        return out
    out["send_text_ret"] = str(r1)
    _t.sleep(0.3)

    # ④ 投回车（真发）
    try:
        r2 = be.keys(hwnd, [0x0D])             # VK_RETURN
    except Exception as e:  # noqa: BLE001
        out.update(step="enter", detail="投回车异常：%s" % str(e)[:80])
        return out
    out["enter_ret"] = str(r2)
    out.update(ok=True, step="sent_blind", detail="已盲投（打字+回车）；未做画面校验")

    # ⑤ DB 回读（唯一可靠判据）
    if adapter is None:
        out["detail"] = "已盲投（打字+回车）⇒ 成功；**未给 adapter ⇒ 跳过 DB 回读校验**"
        return out
    try:
        chat_id = _chat_id or _resolve_chat_id(adapter, chat_name)
        if not chat_id:
            out["detail"] = "已盲投 ⇒ 成功；但解析不出 chat_id ⇒ 跳过 DB 回读"
            return out
        # ⚡ **两级判据（事件 + 标量）**：先让 `wait_new_message` 用 `filewatch` 盯 `message_*.db-wal`
        #    ⛔ **必须把"发前记的基线"传进去**（`base=`）：不传 ⇒ 它自己现取 ⇒ 若我们的行**已经落地**，
        #       它等的是"**下一条**" ⇒ 白等满整个 `timeout_s`（实测 6~9 秒），再靠下面的重试救回来。
        #       传了 ⇒ 我们的行一落库事件立刻命中（实测 ~0.4s）。
        #    ——「一有写入就醒」，比"固定 sleep 0.5s 一拍"**既快又不费**（空闲时零开销）；
        #    醒来后再做一次**语义确认**（本条文本在不在）⇒ 语义与接线前完全一致。
        #    ⛔ 老桩/异常 ⇒ 自动退回原来的轮询（行为与接线前一致，不静默变严也不变松）。
        _hit, _waited, _mode = False, None, None
        _w = getattr(adapter, "wait_new_message", None)
        if _w is not None:
            try:
                _ok2, _why2, _inf2 = _w(chat_id, base=(_base or None), timeout=float(timeout_s))
                _waited = (_inf2 or {}).get("waited")
                _mode = (_inf2 or {}).get("mode")
                # 留证：事件那一步的读数（waited/mode）——与图·表情链同一口径，排障时不用再猜
                out["wait_evt"] = {"ok": bool(_ok2), "waited": _waited, "mode": _mode,
                                   "base": _base, "why": str(_why2)[:120]}
                _hit = bool(_ok2) and _db_tail_has(adapter, chat_id, text, since_id=_base)
            except Exception:  # noqa: BLE001
                _hit = False
        # ⛔⛔ **重试必须有它自己的截止时间**：`wait_new_message` 自己就会吃掉整个 `timeout_s`，
        #   若拿"它之前"记的 `t0` 去算剩余时间 ⇒ 这段重试**一次都不会跑**
        #   （而"写事件先到、行还没可见"是常态 ⇒ 直接报假阴性：明明发出去了却说没看到该文本）。
        if not _hit:
            _deadline = _t.time() + max(1.5, float(timeout_s) * 0.5)
            while _t.time() < _deadline:
                _t.sleep(0.25)
                if _db_tail_has(adapter, chat_id, text, since_id=_base):
                    _hit = True
                    break
        if _hit:
            out.update(db_verified=True, db_hit=text,
                       detail="★ 已发送并**DB 回读确认**（最小化/后台状态下也成立；判据＝事件+标量%s）"
                              % ("，%.2fs/%s" % (_waited, _mode) if _waited is not None else ""))
            return out
        out["detail"] = ("已盲投但 **DB 回读 %ds 内没看到该文本**（可能被去重/过滤/网络慢）；"
                         "不宣称已送达" % int(timeout_s))
        return out
    except Exception as e:  # noqa: BLE001
        out["detail"] = "已盲投 ⇒ 成功；DB 回读异常：%s" % str(e)[:80]
        return out


def popup_right_click_menu(hwnd: int, screen_pt, label: str, *, delay: float = 1.0,
                           extra_points=None) -> dict:
    """★ **在独立窗上投递右键 → 点含 `label` 的菜单项** ⇒ `{ok, detail, menus, point}`。

    ⭐⭐ **为什么不能复用 `wechat._right_click_menu_posted`**：
      那条是**为主窗写的** —— 它右键投 `gui.main_hwnd`，坐标相对 `gui.origin_x/origin_y`
      （主窗原点）。而独立窗是**另一个顶层窗**（自己的 hwnd、自己的原点）
      ⇒ 对着主窗的坐标右键，**菜单根本不在独立窗上弹**。

    ✅ 本函数：右键投**独立窗自己**，坐标就是**屏幕物理坐标**（`backend.click` 收的就是屏幕坐标）。

    ⛔⛔ **两个必须踩过的坑（都实测过）**：
      1. **别让 `click()` 把目标偷换成主窗** —— `MessageBackend.click(right=True)` 原本
         无条件把目标换成 `find_main_window()`（那是**主窗**的特例）。后果实测：
         往独立窗输入框投右键，**菜单却弹在主窗的会话列表上**（OCR 读到
         `标为未读/消息免打扰/独立窗口显示/不显示/删除`）——即"右键点到主窗去了"。
         ⇒ 必须传 `right_keep_target=True`。
      2. **落点会踩空** —— 输入框在窗口里的**比例位置随窗口高度漂移**（聊天区可伸缩）。
         写死一个比例 ⇒ **右键弹不出菜单**（日志 `右键之后没出现菜单窗`）。
         ⇒ 必须**按序试多个候选落点**（`extra_points`，见 `popup_input_points`），
            **谁先弹出菜单谁生效**。

    ⚠️ **伪激活开/关都行**（实测：`activate=False` 与 `True` **都弹出菜单**，
       菜单窗同一位置）——所以这里**不再强行设 `activate`**，保持调用方的设定。

    ✅ 实测成功链（`某群` 独立窗）：
       右键（落点 = 底边上方 0.16H）→ 菜单 `粘贴 / 边写边译` → 点「粘贴」
       → 输入框出现缩略图、**发送键变绿** → 回车 → **DB 出现 `[动画表情]`**。
    """
    out = {"ok": False, "detail": "", "menus": [], "point": None, "tries": []}
    try:
        from agent import input_backend as IB
        import win32process
        import time as _t
        be = IB.select_backend()

        hwnd = int(hwnd)
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]

        pts = []
        if screen_pt:
            pts.append((int(screen_pt[0]), int(screen_pt[1])))
        for p in (extra_points or []):
            q = (int(p[0]), int(p[1]))
            if q not in pts:
                pts.append(q)

        for p in pts:
            before = IB.menu_new_windows(pid, ())
            ok, why = be.click(hwnd, p, right=True, hover_ms=60, press_ms=80,
                               right_keep_target=True)   # ★ 别被 click() 偷偷换成主窗！
            if not ok:
                out["tries"].append({"pt": p, "ok": False, "why": str(why)[:60]})
                continue
            _t.sleep(max(0.4, float(delay)))
            menus = IB.menu_new_windows(pid, before)
            out["tries"].append({"pt": p, "menus": list(menus or [])})
            if not menus:
                continue
            out["menus"] = list(menus or [])
            out["point"] = p
            ok2, why2 = IB.menu_click(menus[0], label)
            out["ok"] = bool(ok2)
            out["detail"] = "菜单点击：%s ｜ 落点 %s（试了 %d 个）" % (why2, p, len(out["tries"]))
            return out
        out["detail"] = ("试了 %d 个候选落点都没弹出菜单（%s）"
                         "⇒ 独立窗右键菜单这条路当前不可用" % (len(out["tries"]), label))
        return out
    except Exception as e:  # noqa: BLE001
        out["detail"] = "独立窗右键菜单异常：%s" % str(e)[:90]
        return out


def close_menu_windows(pid: int, menus) -> int:
    """关掉残留的菜单浮层（**别在用户屏幕上留浮层**）。返回关掉几个。

    ⚠️ `input_backend` 只提供了 `menu_new_windows` / `menu_click`，**没有 `menu_close`**
      ⇒ 这里走 `agent.winops.close`（项目既有的关窗原语）。
    """
    n = 0
    for mh in (menus or []):
        try:
            from agent.winops import close as _wc
            _wc(int(mh), "关残留菜单")
            n += 1
        except Exception:
            pass
    return n


def _is_iconic(hwnd: int) -> bool:
    try:
        import ctypes as _ct
        return bool(_ct.windll.user32.IsIconic(int(hwnd)))
    except Exception:
        return False


def window_rect_any_state(hwnd: int) -> tuple:
    """★ **任何窗口状态下都给出"真实矩形"**（屏幕物理像素 `(l,t,r,b)`）。

    ⭐⭐ **为什么必须有它（实测钉死）**：
      窗口**最小化**时，`GetWindowRect` / `DwmGetWindowAttribute` **都返回离屏哨兵**
      `(-32000, -32000, ...)`（实测 `(-32000,-32000,-31763,-31961)`）——
      用它算出来的落点全是 `-31881, -31965` 这种**荒谬坐标**，
      投递点击等于**打到虚空**（甚至打到任务栏/别的窗口）。
      ⛔ 这正是我早先"把微信点到最小化"的同一个坑的另一面。

    ✅ **出路：`GetWindowPlacement()` 的 `rcNormalPosition`** ——
      实测（本机、独立窗、`showCmd=2`＝最小化）：
        · `GetWindowRect`        ⇒ `(-32000, -32000, -31763, -31961)`   ✗ 废值
        · `rcNormalPosition`     ⇒ `(1378, 347, 2491, 1257)`           ✓ 与可见时**完全一致**

    判据：`GetWindowRect` 若给出"离屏哨兵"（x 或 y ≤ -10000），就改用 `rcNormalPosition`。
    """
    import ctypes as _ct
    import ctypes.wintypes as _wt

    class _WP(_ct.Structure):
        _fields_ = [("length", _ct.c_uint), ("flags", _ct.c_uint), ("showCmd", _ct.c_uint),
                    ("ptMinPosition", _wt.POINT), ("ptMaxPosition", _wt.POINT),
                    ("rcNormalPosition", _wt.RECT)]

    u = _ct.windll.user32
    hwnd = int(hwnd)
    r = _wt.RECT()
    got = (0, 0, 0, 0)
    try:
        u.GetWindowRect(hwnd, _ct.byref(r))
        got = (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        got = (0, 0, 0, 0)

    def _ok(t):
        return t[0] > -10000 and t[1] > -10000 and t[2] > t[0] and t[3] > t[1]

    if _ok(got):
        return got                                  # ① 正常状态，直接用
    try:                                            # ② 最小化/离屏 ⇒ 取"最小化前的位置"
        wp = _WP()
        wp.length = _ct.sizeof(wp)
        if u.GetWindowPlacement(hwnd, _ct.byref(wp)):
            n = wp.rcNormalPosition
            cand = (int(n.left), int(n.top), int(n.right), int(n.bottom))
            if _ok(cand):
                return cand
    except Exception:
        pass
    return got


def _vscreen() -> tuple:
    """虚拟屏（**含多显示器**）⇒ `(x, y, w, h)`；取不到退主屏。"""
    try:
        import ctypes as _ct
        u = _ct.windll.user32
        x, y = int(u.GetSystemMetrics(76)), int(u.GetSystemMetrics(77))
        w, h = int(u.GetSystemMetrics(78)), int(u.GetSystemMetrics(79))
        if w > 0 and h > 0:
            return x, y, w, h
        return 0, 0, int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
    except Exception:  # noqa: BLE001
        return 0, 0, 1920, 1080


def rect_outside_screen(rc) -> bool:
    """窗口矩形与**虚拟屏**无交集 ⇒ True（用户一个像素都看不到）。

    ⛔ 与 `voice_strip._rect_off_screen` 的关系：那个比的是**主屏**。判"用户看不见"必须用
      **虚拟屏** —— 否则多显示器时"主屏之外"仍可能落在**另一块屏**上，被误判成"看不见"。
    """
    if not rc:
        return False
    vx, vy, vw, vh = _vscreen()
    l, t, r, b = rc
    return bool(r <= vx or l >= vx + vw or b <= vy or t >= vy + vh)


def _free_spot_for(w: int, h: int) -> tuple:
    """给"要救回屏内的窗口"挑一个落点 ⇒ `(x, y)`。

    ⛔⛔ **不许写死坐标**（原实现默认 `(200,120)`）：那会把**用户的窗口**搬到我们随便挑的
      一个角落，而且**会被写进 `rcNormalPosition`** ⇒ 用户以后从任务栏还原永远回到那个角落
      （写死落点还会写进 `rcNormalPosition` ⇒ 用户以后从任务栏还原总是回到我们挑的那个角落）。
    ✅ 改成**按主窗的位置推导**：优先"贴着主窗右边/左边、不重叠"，放不下就"上/下"，
      再放不下就**主屏居中**。全程只用"主窗矩形 + 屏幕矩形"这两个可观测量，不含任何魔法数。
    """
    vx, vy, vw, vh = _vscreen()
    try:
        from agent import input_backend as _ib
        mr = _tw_rect(int(_ib.find_main_window() or 0))
    except Exception:  # noqa: BLE001
        mr = None
    _gap = 16
    cands = []
    if mr:
        cands += [(mr[2] + _gap, mr[1]),            # 主窗右侧
                  (mr[0] - _gap - w, mr[1]),        # 主窗左侧
                  (mr[0], mr[3] + _gap),            # 主窗下方
                  (mr[0], mr[1] - _gap - h)]        # 主窗上方
    cands.append(((vx + vw - w) // 2, vy + max(0, (vh - h) // 3)))   # 主屏居中偏上
    for x, y in cands:
        if w <= vw and h <= vh and vx <= x and vy <= y and x + w <= vx + vw and y + h <= vy + vh:
            return int(x), int(y)
    return int(vx + 40), int(vy + 40)


def _tw_rect(hwnd: int):
    """窗口矩形（容错版，拿不到 ⇒ None）。"""
    if not int(hwnd or 0):
        return None
    try:
        import ctypes as _ct
        import ctypes.wintypes as _wt
        r = _wt.RECT()
        _ct.windll.user32.GetWindowRect(_ct.c_void_p(int(hwnd)), _ct.byref(r))
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:  # noqa: BLE001
        return None


def unstick_window_to_screen(hwnd: int, *, x: int = 0, y: int = 0,
                             minimize: bool = True) -> tuple:
    """★ 把**跑到屏幕外/被隐藏**的窗口救回可用状态（**不激活**）。

    ⚠️ 为什么需要（两种离屏都会把用户卡死）：
      · **离屏哨兵**（`GetWindowRect = (-32000,-32000,...)`）；
      · **"屏外显示"那套手法**（坐标是 `-(宽+200), -(高+200)`，实测 `(-1119,-1171)`）。
      两种都"自己不会回来" —— 用户看到"窗口不见了"。

    ⛔⛔ **判据必须用"与虚拟屏无交集"**，不许用"坐标 ≤ -10000"那种哨兵判据：
      实测"屏外显示"给的坐标是 `-1119/-1171`，**过不了哨兵判据** ⇒ 救援工具形同不存在
      （用户报"只能擦掉重新开"就是这么来的）。
    ⛔ **`rcNormalPosition` 必须一起修**：它是"从任务栏还原"的目标 —— 只改当前 rect 的话，
      从任务栏一点，窗口**又飞回屏幕外**（＝`rcNormalPosition` 本身也是离屏值）。
    ⛔ **收尾一律"收成最小化"**（`minimize=True`）：死态本来就是我们把它"显示到屏外"造成的，
      正确结局是**别让用户看见**（而不是弹到屏幕中间盖住他正在做的事）。
      收起来之后用户点任务栏就能正常拿到它（因为 `rcNormalPosition` 已经修到屏内）。
      ⛔ 用 `SW_SHOWMINNOACTIVE`(7)，**不用** `SW_MINIMIZE`(6)：6 会"最小化**并激活**
        Z 序里的下一个顶层窗"（独立窗的邻居正是微信主窗 ⇒ 把主窗顶到前台）。
    ⛔ **尺寸不许"顺手放大"**：离屏时当前 rect 可能是个残值（最小化动画中实测 613×647），
      拿它当尺寸会**改变用户的窗口大小**。⇒ 尺寸取 `当前 rect → rcNormalPosition → 600×400`
      里第一个合理的（宽度 ≥300 且高度 ≥200）。
    ⛔ **已经最小化的窗口不挪**（它本来就不上屏）；但它的 `rcNormalPosition` 若离屏，
      **仍然要修**（否则用户一还原就飞出去）。

    `x` / `y` **默认 0 ＝ 自动挑**（`_free_spot_for`：按主窗位置推导，不写死角落）。
    返回 `(ok, 修前矩形, 修后矩形)`。**只在确认有问题时才动手**（正常窗口一律不碰）。
    """
    import ctypes as _ct
    import ctypes.wintypes as _wt
    import time as _t3
    u = _ct.windll.user32
    SWP_NOZORDER, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0004, 0x0010, 0x0040
    SW_SHOWNOACTIVATE, SW_SHOWMINNOACTIVE = 4, 7
    hwnd = int(hwnd)
    r = _wt.RECT()
    try:
        u.GetWindowRect(hwnd, _ct.byref(r))
    except Exception:  # noqa: BLE001
        return False, (0, 0, 0, 0), (0, 0, 0, 0)
    before = (int(r.left), int(r.top), int(r.right), int(r.bottom))

    class _WP(_ct.Structure):
        _fields_ = [("length", _ct.c_uint), ("flags", _ct.c_uint), ("showCmd", _ct.c_uint),
                    ("ptMinPosition", _wt.POINT), ("ptMaxPosition", _wt.POINT),
                    ("rcNormalPosition", _wt.RECT)]

    def _place():
        wp = _WP()
        wp.length = _ct.sizeof(_WP)
        if not u.GetWindowPlacement(_ct.c_void_p(hwnd), _ct.byref(wp)):
            return None, None
        n = wp.rcNormalPosition
        return wp, (int(wp.showCmd), (n.left, n.top, n.right, n.bottom))

    _, pl0 = _place()
    nor = pl0[1] if pl0 else (0, 0, 0, 0)
    iconic = bool(u.IsIconic(_ct.c_void_p(hwnd)))
    cur_bad = (not iconic) and bool(u.IsWindowVisible(_ct.c_void_p(hwnd))) \
        and rect_outside_screen(before)
    nor_bad = rect_outside_screen(nor)
    if not (cur_bad or nor_bad):
        return True, before, before                  # 正常窗 ⇒ 不动它
    W_ = H_ = 0
    for _c in ((before[2] - before[0], before[3] - before[1]),
               (nor[2] - nor[0], nor[3] - nor[1])):
        if _c[0] >= 300 and _c[1] >= 200:
            W_, H_ = int(_c[0]), int(_c[1])
            break
    if W_ <= 0:
        W_, H_ = 600, 400
    if not (int(x) or int(y)):
        x, y = _free_spot_for(W_, H_)      # ⛔ 不写死坐标：按主窗位置推导（见 `_free_spot_for`）
    x, y = int(x), int(y)
    try:
        if cur_bad and minimize:
            u.ShowWindow(_ct.c_void_p(hwnd), SW_SHOWMINNOACTIVE)
            _t3.sleep(0.15)
            if not u.IsIconic(_ct.c_void_p(hwnd)):
                u.SetWindowPos(_ct.c_void_p(hwnd), 0, int(x), int(y), W_, H_,
                               SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)
                u.ShowWindow(_ct.c_void_p(hwnd), SW_SHOWNOACTIVATE)
                _t3.sleep(0.2)
        elif cur_bad:
            u.SetWindowPos(_ct.c_void_p(hwnd), 0, int(x), int(y), W_, H_,
                           SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)
            u.ShowWindow(_ct.c_void_p(hwnd), SW_SHOWNOACTIVATE)
            _t3.sleep(0.25)
        # ⭐ 还原目标也要修（否则从任务栏还原又飞出去）：**保持原 showCmd**（最小化的继续最小化）
        if nor_bad:
            wp, _cur = _place()
            if wp is not None:
                wp.rcNormalPosition.left, wp.rcNormalPosition.top = int(x), int(y)
                wp.rcNormalPosition.right, wp.rcNormalPosition.bottom = int(x) + W_, int(y) + H_
                u.SetWindowPlacement(_ct.c_void_p(hwnd), _ct.byref(wp))
                _t3.sleep(0.2)
        u.GetWindowRect(hwnd, _ct.byref(r))
        after = (int(r.left), int(r.top), int(r.right), int(r.bottom))
        _, pl2 = _place()
        nor2 = pl2[1] if pl2 else (0, 0, 0, 0)
        ok = (bool(u.IsIconic(_ct.c_void_p(hwnd))) or not rect_outside_screen(after)) \
            and not rect_outside_screen(nor2)
        return bool(ok), before, after
    except Exception:  # noqa: BLE001
        return False, before, before


def _resolve_chat_id(adapter, chat_name: str) -> str:
    """把会话名翻成 DB 用的 `chat_id`（username）。找不到返回 ''。"""
    try:
        from agent import session_order as _so
        s = _so.find_session(adapter, chat_name)
        if s and s.get("username"):
            return str(s["username"])
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    try:
        from agent import session_order as _so
        o = _so.order_of(adapter, chat_name)
        if o is not None:
            for it in _so.list_sessions(adapter):
                if int(it.get("order")) == int(o):
                    return str(it.get("username") or "")
    except Exception:
        pass
    return ""


def _db_latest_local_id(adapter, chat_id: str) -> int:
    """该会话**最新一行的 `local_id`**（读不出来 ⇒ 0）。

    ⛔ **必须是 `local_id`，不能拿 `latest_seq()` 的返回值**：那是 **`sort_seq`（毫秒时间戳）**，
      与 `local_id`（会话内自增小整数）**不是一个量纲** ⇒ 拿去当 `since_id` 会让
      "比它新的行"这个判据**恒不成立**（实测踩过：741 vs 1791237462000，所有行都被跳过 ⇒ 假阴性）。
    """
    try:
        rows = adapter._db_messages(chat_id, limit=1) or []
        if rows:
            return int(rows[0].get("local_id") or 0)
    except Exception:  # noqa: BLE001
        pass
    return 0


def _db_tail_has(adapter, chat_id: str, needle: str, *, limit: int = 8, since_id: int = 0) -> bool:
    """DB 里该会话**最近若干条**里有没有 `needle` ⇒ `True/False`。

    匹配口径（两条都要）：
      · **忽略所有空白**（原文与库里那行**都**去掉空白再比）：库里可能把首尾空白/换行吃掉，
        拿原文裸比就会出现"明明发出去了，却说没看到该文本"。
      · 给了 `since_id` 时**只看 `local_id` 比它大的行** —— 这才是"我们刚发的那一条"的定义。
        ⛔ **单位是 `local_id`（会话内自增小整数）**，不是 `sort_seq`（毫秒时间戳）——
          拿错量纲会让这个过滤**恒成立/恒不成立**（实测踩过）。取值见 `_db_latest_local_id`。
        ⛔ **不许只翻"最后几行"**：会话里只要再来几条（语音/图片/别人说话），刚发的那条就被挤出窗口
        ⇒ 判据凭空变假。实测：文本落在 `local_id=731`，而"最后 8 行"已经排到 `733` 之后 ⇒ 读不到。
        ⇒ 缺席 `since_id` 时才退回"最后 `limit` 行"，并把窗口放宽到 40 行。
    """
    def _ns(s) -> str:
        return "".join(str(s).split())

    _want = _ns(needle)
    if not _want:
        return False
    try:
        rows = adapter._db_messages(chat_id, limit=(40 if since_id else max(int(limit), 1)))
    except Exception:
        return False
    for m in (rows or []):
        try:
            if since_id and int(m.get("local_id") or 0) <= int(since_id):
                continue
            t = str(m.get("text") or m.get("content") or m.get("msg") or "")
        except Exception:
            t = ""
        if _want in _ns(t):
            return True
    return False


def _db_new_image(adapter, chat_id: str, *, since_id: int = 0, limit: int = 6) -> bool:
    """DB 里该会话最近几条里有没有**比 `since_id` 更新的图片/表情消息**。

    `local_type`：实测 **3 = 图片**、**47 = 动画表情**（动图走 CF_HDROP 时微信按表情收）。
    """
    try:
        rows = adapter._db_messages(chat_id, limit=limit)
    except Exception:
        return False
    for m in (rows or []):
        try:
            lid = int(m.get("local_id") or 0)
            lt = int(m.get("local_type") or m.get("type") or 0)
            if lid > int(since_id) and lt in (3, 47):
                return True
            if lid > int(since_id) and lt not in (1,):
                # 其它非文本类型（视频/文件/引用…）也算"发出去了一条"
                return True
        except Exception:
            continue
    return False


#: 独立窗**「有内容」的绿占比阈值**（判"粘贴有没有真的把东西放进输入框"）。
#:
#: ⛔⛔ **不能照抄主窗的 `0.30`**（实测标定）：
#:   · 独立窗 **空框 = 0.0000** ／ **有内容（图片缩略图已粘入）= 0.2000**
#:   `SEND_BTN_REL` 的采样方框在独立窗上比主窗小 ⇒ 占比天然更低。
#:   曾用 0.30 当阈值 ⇒ **把本来能成功的粘贴判成失败**，12 次重试全废、一条图都没发出去。
#:   ⇒ 取中点 **0.10**。
POPUP_GREEN_HAS_CONTENT = 0.10

#: 发图片/动态表情后，等 DB 回读确认的默认秒数。
#: 另：**"已受理"的 B 级补位**（事件驱动问"库有写入吗"）最长等这么久 ——
#: 实测"一有写入立刻醒"，这个值只是**上限**（拿不到画面时用它给出秒级结论）。
ACCEPT_EVENT_WAIT_S = 3.0
#: 图片/表情要等客户端把资源上传完才写库，实测 30~180s，上传慢时会叠加到更久
#: ⇒ 取 300s 以免"已送达却判成未确认"。调用方可用 `timeout_s` 覆盖。
IMAGE_CONFIRM_TIMEOUT_S = 300.0


# ── ★ 让独立窗"人看不见"但**保留坐标 / 消息 / 自检**（实测可行） ──
#:
#: 目标：让独立窗**人看不见**，但坐标 / 消息 / 自检全部保留。
#:
#: ⛔ **`DwmSetWindowAttribute(DWMWA_CLOAK)`（DWM 隐身）跨进程不可用** ——
#:    实测返回 `0x80070005`（拒绝访问）：DWM 隐身只能由**窗口自己的进程**设置。
#: ✅ 可行手段 = **`WS_EX_LAYERED` + `SetLayeredWindowAttributes(alpha=0)`（全透明）**。
#:    实测：
#:      · 全屏抓图 **那块区域已变成后面的窗口** ⇒ **人在屏幕上看不见它** ✅
#:      · `GetWindowRect` **仍是 `(140,100,702,777)`** ⇒ 落点照算 ✅
#:      · **`PrintWindow` 仍能抓到它自己的完整内容** ⇒ **自检照做**（这是白捡的大收益：
#:        透明态的"发送键变绿/变灰"就是**秒级**判据，不用再等 DB 落库 30~180s）✅
#:    ⚠️ 但它**仍然会被"投鼠标消息"抢前台**（前台被抢与可见性无关）⇒
#:       必须配 `restore_foreground()` **立刻还回去**（实测 `SetForegroundWindow` 在我们进程里能成功）。
WS_EX_LAYERED = 0x00080000
GWL_EXSTYLE = -20
LWA_ALPHA = 0x00000002


def set_popup_invisible(hwnd: int, on: bool = True, *, alpha: int = 0) -> dict:
    """让独立窗**人看不见**（全透明）——**保留真实坐标 / 消息收发 / PrintWindow 自检**。

    `on=True` ⇒ 加 `WS_EX_LAYERED` + alpha=0（全透明）；`on=False` ⇒ 还原可见并去掉该扩展样式。
    返回 `{ok, detail, exstyle_before, exstyle_after}`。**失败也如实返回，不假装成功。**
    """
    out = {"ok": False, "detail": "", "exstyle_before": 0, "exstyle_after": 0}
    hwnd = int(hwnd or 0)
    if not hwnd:
        out["detail"] = "窗口句柄为空"
        return out
    try:
        import ctypes as _ct
        u32 = _ct.windll.user32
        ex0 = u32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        out["exstyle_before"] = ex0
        want = (ex0 | WS_EX_LAYERED) if on else (ex0 & ~WS_EX_LAYERED)
        u32.SetWindowLongW(hwnd, GWL_EXSTYLE, want)
        r = u32.SetLayeredWindowAttributes(hwnd, 0, (0 if on else 255), LWA_ALPHA)
        ex1 = u32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        out["exstyle_after"] = ex1
        layered_ok = bool(ex1 & WS_EX_LAYERED) == bool(on)
        # ⚠️ 还原时 `SetLayeredWindowAttributes` 会返回 0（扩展样式位已经去掉）——
        #    那是**正常现象**，不能当成失败。所以 `on=True` 才要求它返回真。
        out["ok"] = bool(layered_ok and (r if on else True))
        out["detail"] = ("已%s（EXSTYLE 0x%08X → 0x%08X，SetLayeredWindowAttributes=%s%s）"
                         % ("隐身（全透明）" if on else "还原可见", ex0, ex1, r,
                            "" if on else "（还原时返回 0 属正常）"))
        return out
    except Exception as e:  # noqa: BLE001
        out["detail"] = "设置隐身失败：%s: %s" % (type(e).__name__, str(e)[:80])
        return out


def get_foreground() -> int:
    """当前前台窗句柄（用于"借完前台立刻还"）。"""
    try:
        import ctypes as _ct
        return int(_ct.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def restore_foreground(prev: int) -> bool:
    """把前台**还回** `prev`（发图时会被微信抢走，这里还回去）。

    ✅ 实测**在我们进程里能成功**（`SetForegroundWindow` 一次到位）。
    ⚠️ 但它是"事后补救"，**抢的那一瞬间仍然会让别的窗口短暂失焦**
      （全屏游戏可能被踢出全屏）⇒ 只能把影响压到最短，不能消除。
    """
    prev = int(prev or 0)
    if not prev:
        return False
    try:
        import ctypes as _ct
        u32 = _ct.windll.user32
        if int(u32.GetForegroundWindow() or 0) == prev:
            return True
        return bool(u32.SetForegroundWindow(prev))
    except Exception:
        return False


def popup_composer_green(hwnd: int) -> float:
    """独立窗**「发送」键的变绿程度**（0~1）；`-1` = 抓不到有效画面。

    ⭐ 用途：**判断"粘贴到底有没有把东西放进输入框"**。
      实测：输入框**有内容**（文字或图片缩略图）时发送键变绿，
      空框时是灰的 ⇒ **绿占比 ≥ 0.30 就是"有内容"**（与 `send_to_popup` 同一判据）。

    ⛔ **为什么必须查这个**：独立窗当时不是前台时，**第一次左键会被"激活窗口"吃掉**，
      输入框拿不到焦点 ⇒ 右键菜单照样弹、也点了「粘贴」，但**框里什么也没有**。
      实测后果：动图那一枪**看着全绿（ok=True）却没发出去**（DB 无新行）。
      ⇒ 发图流程必须"粘完先自检，没内容就换落点重来"。
    """
    try:
        im, _meta = grab_window(int(hwnd), physical=False)
        if im is None or min(im.size) < 60:
            return -1.0
        return float(send_button_green_ratio(im))
    except Exception:
        return -1.0


def resolve_title_name(chat_name: str, adapter=None) -> str:
    """把 **username 或显示名** 统一解析成「**窗口标题用的显示名**」。

    ⛔ 为什么需要：独立窗标题只有**显示名**，而调用方常传 **username**
      （如 `filehelper`）⇒ 直接拿去比标题**永远匹配不上**（窗已开出却报找不到）。
    解析不出（没有 adapter / 查不到）时**原样返回**，不猜。
    """
    s = str(chat_name or "").strip()
    if not s or adapter is None:
        return s
    try:
        from agent import session_order as _so
        r = _so.find_session(adapter, s)
        if r and str(r.get("name") or "").strip():
            return str(r["name"]).strip()
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    return s


def _borrow_foreground_now(target_hwnd: int) -> dict:
    """**临时借前台**（`AttachThreadInput` 绕过后台进程限制）⇒ `{ok, prev, ms, why}`。

    ⭐ 为什么需要：盲粘贴那一下真 Ctrl **只会被前台线程收走**；
       而本进程是后台进程，直接 `SetForegroundWindow` **会被系统拒绝**（实测返回 False）。
       用 `AttachThreadInput(本线程, 目标线程)` 临时连接输入队列后即可成功。

    ✅ 实测（本机，目标=最小化的微信独立窗）：
       · 借到 **6ms**；
       · **窗口保持最小化、不会被显示出来**；
       · 该线程 `GetGUIThreadInfo().hwndFocus == None`
         ⇒ **用户的按键不会落进那个窗口**。
    ⇒ 借的这一瞬对用户无感；用完**必须** `_restore_foreground_now` 还回去。
    """
    import time as _t
    out = {"ok": False, "prev": 0, "ms": 0, "why": ""}
    tgt = int(target_hwnd or 0)
    if not tgt:
        out["why"] = "目标 hwnd 为空"
        return out
    try:
        import ctypes as _ct
        import ctypes.wintypes as _wt
        u32 = _ct.windll.user32
        k32 = _ct.windll.kernel32
        u32.GetForegroundWindow.restype = _wt.HWND
        prev = int(u32.GetForegroundWindow() or 0)
        out["prev"] = prev
        if prev == tgt:
            out.update(ok=True, why="本来就是前台")
            return out
        mine = int(k32.GetCurrentThreadId())
        tgt_tid = int(u32.GetWindowThreadProcessId(_wt.HWND(tgt), None) or 0)
        if not tgt_tid:
            out["why"] = "取不到目标线程"
            return out
        t0 = _t.perf_counter()
        u32.AttachThreadInput(mine, tgt_tid, True)
        try:
            try:
                u32.BringWindowToTop(_wt.HWND(tgt))
            except Exception:
                pass
            r = bool(u32.SetForegroundWindow(_wt.HWND(tgt)))
        finally:
            u32.AttachThreadInput(mine, tgt_tid, False)
        out["ms"] = round((_t.perf_counter() - t0) * 1000.0, 1)
        cur = int(u32.GetForegroundWindow() or 0)
        out["ok"] = bool(r or cur == tgt)
        if not out["ok"]:
            out["why"] = "SetForegroundWindow 未生效（返回 %s，前台=%d）" % (r, cur)
        return out
    except Exception as e:  # noqa: BLE001
        out["why"] = "%s: %s" % (type(e).__name__, str(e)[:60])
        return out


def _restore_foreground_now(prev_hwnd: int, *, tries: int = 4) -> bool:
    """把前台**还回** `prev_hwnd`（同样要用 `AttachThreadInput` 绕过后台限制）。

    ⚠️ **必须"重试 + 确认"**：微信收到真 Ctrl 后自己会抢前台，而它抢的**时刻**晚于
      我们抬手的那一瞬 ⇒ 单次还回可能在"它抢之前"就返回（那时前台还是 prev，看着像成功），
      随后又被它抢走 ⇒ 表现为"没还回去"。⇒ 这里**连续确认**，一旦发现又跑掉就再还一次。
    """
    prev = int(prev_hwnd or 0)
    if not prev:
        return False
    import time as _t
    try:
        import ctypes as _ct
        import ctypes.wintypes as _wt
        u32 = _ct.windll.user32
        k32 = _ct.windll.kernel32
        u32.GetForegroundWindow.restype = _wt.HWND
        mine = int(k32.GetCurrentThreadId())
        p_tid = int(u32.GetWindowThreadProcessId(_wt.HWND(prev), None) or 0)
        stable = 0
        for _i in range(max(1, int(tries))):
            cur = int(u32.GetForegroundWindow() or 0)
            if cur == prev:
                stable += 1
                if stable >= 2:            # 连续两次都在 prev ⇒ 认它稳住了
                    return True
                _t.sleep(0.04)
                continue
            stable = 0
            if p_tid:
                u32.AttachThreadInput(mine, p_tid, True)
                try:
                    u32.SetForegroundWindow(_wt.HWND(prev))
                finally:
                    u32.AttachThreadInput(mine, p_tid, False)
            else:
                u32.SetForegroundWindow(_wt.HWND(prev))
            _t.sleep(0.04)
        return int(u32.GetForegroundWindow() or 0) == prev
    except Exception:
        return False


class _FgStash:
    """**「还前台」的唯一收口**：块内登记原前台 ⇒ 出块时（正常 / 抛异常 / 提前 return）一定还。

    为什么要有它（收编 Akasha 时对照出来的我方缺口）：
      这一族原先靠"调用方记得配对"，而真实写法是**借在某处、还在另一处的分支里** ——
      `popup_paste_blind` / `popup_fire_paste_send` 的**异常出口都没有还**
      （中间任何一步抛异常 ⇒ 用户的焦点就留在微信窗口上）。Akasha 的原版正是
      「`finally` 里只解挂 `AttachThreadInput`、不还原原前台」⇒ 焦点被永久夺走。
      包成 `with` 之后**结构上不可能漏还**：所有出口都会过 `__exit__`。

    用法：
        with fg_stash(out) as st:   # out 可选；还的结果写进它的 `key`
            st.note(prev_hwnd)      # 登记（0 = 不还；可在块内任意时刻调用）
            ...

    ⚠️ 只在「**真的会夺走前台**的那一刻之前」登记：没触发夺焦就登记，出块时会多做一次
       `SetForegroundWindow`，把本该在前台的窗口又置一次（无害但多余，且会掩盖真问题）。
    """

    def __init__(self, sink: dict | None = None, key: str = "fg_restored"):
        self.prev = 0
        self.restored: bool | None = None
        self._sink = sink
        self._key = key

    def note(self, prev_hwnd) -> None:
        self.prev = int(prev_hwnd or 0)

    def __enter__(self) -> "_FgStash":
        return self

    def __exit__(self, *_exc) -> bool:
        if self.prev:
            try:
                self.restored = bool(_restore_foreground_now(self.prev))
            except Exception: # noqa: BLE001
                self.restored = False
            if isinstance(self._sink, dict):
                self._sink[self._key] = self.restored
        return False


def fg_stash(sink: dict | None = None, key: str = "fg_restored") -> _FgStash:
    """建一个「还前台」的收口对象（来历与用法见 `_FgStash`）。"""
    return _FgStash(sink, key)


def popup_paste_blind(hwnd: int, *, key_gap: float = 0.06,
                      use_real_ctrl: bool = True) -> dict:
    """★★★ **"真按 Ctrl + 投递 V" 触发微信粘贴** —— **零坐标 ⇒ 最小化档也成立**。

    ⭐ 原理（从 `Weixin.dll` 导入表 + 实测一起锁死）：
      微信判定 Ctrl+V 读的是**真实键态**（`GetKeyState`）。投递的消息**不携带修饰键态**
      ⇒ 只投 Ctrl+V 会**退化成字面字母 `v`**
      （项目早先踩到 `vv` 污染群聊，由此把 Ctrl+V 整条路禁用 —— **但那个结论只对"纯投递"成立**）。
      ✅ 正确做法：**用 `keybd_event` 真按一下 Ctrl**（只在系统键态里置位），
        再把 `V` **投递**给目标窗 —— 微信 `GetKeyState` 看到 Ctrl 按下
        ⇒ 认成 Ctrl+V ⇒ **执行粘贴**。
      ⭐ **投递的 `V` 不带坐标** ⇒ **最小化档也成立**（与 `WM_CHAR` 同性质）。

    ✅ 实测（`某群` 独立窗）：
      · 前台档：绿占比 `0 → 0.2000`（图进输入框）
      · **最小化档**：`iconic=1` 下投递 ⇒ 回车 ⇒ DB `local_id=11 [动画表情]` / `12 [图片]`
      · 阳性对照：真实 Ctrl+V 也成功（说明快捷键确实存在）
      · 反例：投递 `Shift+F10` 唤菜单 **无效**；`WM_CONTEXTMENU`/`VK_APPS`/`WM_DROPFILES` 全无效

    ⭐⭐ **对用户的两条判据 —— 实测钉死（忙等采样 35433 次）**：
      · **不影响观看** ✅ **完全达标** —— 窗口**一次都没上屏**
        （"非最小化 且 非屏外"的采样 = **0**，累计露出 **0.000 ms**）。
        本函数**不调** `ShowWindow` / 任何改可见性的 API ⇒ 用户看不到窗口跳出来。
        （一致："我把它粘出去的时候，我完全没有看见窗口跳出来"。）
      · **不影响操作** ⚠️ **近乎达标，但不是 0** —— 实测抓到 **3 次**前台变化、
        每次 **约 0.1 毫秒**（1057700 / 87600 / 95200 ns），随即被 `fg_stash` 还回原窗口；
        全程**不动光标、不点鼠标、不注入字符**。用户不可能感知 0.1ms ⇒ 体感无影响。
      · ⭐ **前台为什么会被夺一下（已定位到确切那一步）**：
        **不是 `keybd_event` 按 Ctrl 造成的**（单测：只按 Ctrl，前台**0/481 次**变化）；
        **是投递那个 `V`** —— 微信收到后会**自己把独立窗置前**
        （`Weixin.dll` 导入表里有 `SetForegroundWindow`）。单测：只投 `V`、不按 Ctrl，
        前台**立刻变成该独立窗**且**不会自己还**（末态仍是它）
        ⇒ 所以本函数**必须**用 `fg_stash` 兜底还前台（现有实现已是如此）。
      · ⇒ **与语音条投递路的差别**：语音条走 `PostMessage` 点控件 ⇒ **微信不激活窗口**
        （实测两条判据都 0）；本路靠"真按 Ctrl + 投 V"⇒ 必然带那一次 ~0.1ms 激活。
        若要把它也压成 0，得换"纯投递点控件"的路子（如右键菜单/按钮投递）。

    ⚠️ **若要"连那一下真 Ctrl 也不要有"**：需把 `use_real_ctrl=False`，
      走 `AttachThreadInput` + `SetKeyboardState` 的方向，**尚未验证**（实测**骗不过微信**，
      投递的 V 会退化成字面字母）⇒ 目前 `use_real_ctrl=False` 会直接返回不支持，避免悄悄换实现。

    参数
      `key_gap` —— Ctrl 按下 → 投 V → 抬起之间的间隔（秒）。
    """
    out = {"ok": False, "detail": "", "channel": "ctrl_v"}
    hwnd = int(hwnd or 0)
    if not hwnd:
        out["detail"] = "窗口句柄为空"
        return out
    if not use_real_ctrl:
        out["detail"] = ("`use_real_ctrl=False`（无真实按键的版本）**已试过、不成立**："
                         "`AttachThreadInput` + `SetKeyboardState` 骗不过微信 —— "
                         "实测投递的 V 仍退化成**字面字母**（抓图见 `_v2_A2.png`/`_v2_A3.png`）")
        return out
    # ⛔⛔⛔ **安全闸门（必须！）**：那一下真 Ctrl 只会被**前台窗口所属线程**收走。
    #   若微信不在前台 ⇒ 微信的 `GetKeyState` 看不到 Ctrl ⇒ 投递的 `V`
    #   **退化成字面字母 `v`**，随后回车就把 `v` 发了出去。
    #   ⇒ 这里**必须拒绝**：**宁可不发，也绝不发垃圾**。
    #
    # ⭐ 前台不在微信线程时的正解 = **临时借一次前台**（`_borrow_foreground_now`）：
    #   实测（本机）：借到 **6ms**、**窗口保持最小化不被显示**、
    #   且该线程 `GetGUIThreadInfo().hwndFocus == None`
    #   ⇒ **用户按键不会落进那个窗口**，借的这一瞬对他无感。
    #   借完**立即还回原前台**（`_restore_foreground_now`），全程约 10ms。
    _borrowed_prev = 0
    try:
        import ctypes as _ct
        import win32process as _wp

        u32 = _ct.windll.user32          # ⛔⛔ **必须在调用前赋值**（见下）
        fg = int(u32.GetForegroundWindow())
        # ⛔⛔⛔ 这里的 `fg` **绝不能是 0**：
        #   `_wp.GetWindowThreadProcessId(0)[0]` 在 pywin32 上**不报错，返回 0**
        #   ⇒ 于是 `fg_tid(0) != my_tid` 恒成立 ⇒ **会走进"拒绝执行"分支**，
        #     把"前台取不到"误报成"微信不是前台"（症状一样、原因不同，极易误判）。
        if not fg:
            out["blocked"] = "no_foreground"
            out["detail"] = ("取不到当前前台窗（`GetForegroundWindow()` 返回 0，"
                             "常见于会话已断开/锁屏）⇒ **拒绝执行**")
            return out
        fg_tid = _wp.GetWindowThreadProcessId(fg)[0]
        my_tid = _wp.GetWindowThreadProcessId(hwnd)[0]
        out["foreground"] = fg
        out["fg_same_thread"] = bool(fg_tid == my_tid)
        # ⭐ 前台不在微信线程时**不必主动借**：实测「真按 Ctrl + 投递 V」会让
        #   **微信自己抢前台**（它的 `Weixin.dll` 导入表里有 `SetForegroundWindow`）
        #   ⇒ 前台会自行变成目标窗，粘贴照常生效，且**比主动借更快**
        #     （实测：不借也能成，按+投约 80ms）。
        #   ⇒ 只记下原前台，**发完立刻还回**。
        if fg_tid != my_tid:
            _borrowed_prev = fg          # 借位记原前台，用于"还"
            out["fg_was_other"] = True
        out["fg_prev"] = _borrowed_prev
    except Exception as e:  # noqa: BLE001
        out["detail"] = "前台判定失败：%s: %s" % (type(e).__name__, str(e)[:60])
        return out
    try:
        import ctypes as _ct
        u32 = _ct.windll.user32
        VK_CONTROL, VK_V = 0x11, 0x56
        KEYEVENTF_KEYUP = 0x0002
        try:
            from agent import input_backend as _IB
            be = _IB.select_backend()
        except Exception:
            be = None
        if be is None:
            out["detail"] = "取不到输入后端"
            return out
        gap = max(0.02, float(key_gap))
        # ★ 从"真按 Ctrl"这一刻起，微信就会把前台抢过去（实测：它收到真 Ctrl 后自己抢）
        #   ⇒ 这之后**必须**还回原前台。包进收口（`fg_stash`）后，中途任何一步抛异常
        #     也会还 —— 原先"还"写在正常路径里，异常出口直接 return ⇒ 用户的焦点被留在微信窗口上。
        with fg_stash(out, "restored") as _fg:
            _fg.note(_borrowed_prev)
            u32.keybd_event(int(VK_CONTROL), 0, 0, 0)      # ★ 真按 Ctrl（置全局键态）
            import time as _t
            _t.sleep(gap)
            be.keys(hwnd, [VK_V])                           # ★ V 走**投递**（不带坐标）
            _t.sleep(gap)
            u32.keybd_event(int(VK_CONTROL), 0, KEYEVENTF_KEYUP, 0)
            out["ok"] = True
            out["detail"] = ("已「真按 Ctrl + 投递 V」⇒ 微信应执行粘贴"
                             "（唯一副作用：那一下 Ctrl 落在当时的前台窗上）")
        return out
    except Exception as e:  # noqa: BLE001
        out["detail"] = "盲粘贴异常：%s: %s" % (type(e).__name__, str(e)[:80])
        return out


def _clipboard_safe(fn):
    """（旧名保留）**唯一实现已收口到** `agent/clipboard.py::clipboard_safe`。

    ⛔ 为什么收口：这个装饰器原来只在本模块里，而"会写剪贴板的函数"不止这里一个
      （`wechat.send_image_posted` 也在写）⇒ 两处各写一份必然漂移，且**漏掉的那一处
      会吃掉用户正在复制的内容**。⇒ 实现搬到 `clipboard` 模块，谁写剪贴板谁都能用同一个。
    """
    from agent.clipboard import clipboard_safe as _impl
    return _impl(fn)


def popup_prepare_send(chat_name: str, *, image: str = None, adapter=None,
                       rect_hint=None) -> dict:
    """★ **预备态**（找窗 / 记原前台 / 放剪贴板 / 记 DB 基线）⇒ 返回票据。

    为什么要把"预备"和"发"拆开：预备部分（找窗、放剪贴板、读 DB 基线）**慢但不紧急**，
    可以在"确定要发"之前就先做好；真正**抢时间**的只有"借前台→粘贴→提交→还"那一小段
    （实测 fire 段 ~百毫秒级）。⇒ 拆开后，**用户感受到的延迟只由 fire 段决定**。

    用法：`p = popup_prepare_send(名, image=路径, adapter=ad)` ⇒ `popup_fire_paste_send(p)`。
    """
    out = {"ok": False, "step": "", "detail": "", "chat": chat_name,
           "hwnd": 0, "clipboard": None, "chat_id": "", "base_id": 0,
           "prev_fg": 0, "image": image, "anim": False}
    name = str(chat_name or "").strip()
    if not name:
        out.update(step="arg", detail="没给会话名")
        return out
    title = resolve_title_name(name, adapter)
    hit = find_popup(title)
    hwnd = int(hit.get("hwnd") or 0)
    if not hwnd:
        out.update(step="locate", detail="没有 %r 的独立窗（请先 ensure_popup）" % name)
        return out
    out["hwnd"] = hwnd
    out["title"] = hit.get("title")

    # ① 原前台（fire 段结束后要还回去）
    try:
        import ctypes as _ct
        import ctypes.wintypes as _wt
        _u = _ct.windll.user32
        _u.GetForegroundWindow.restype = _wt.HWND
        out["prev_fg"] = int(_u.GetForegroundWindow() or 0)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        out["prev_fg"] = 0

    # ② DB 基线（只用于"事后核实"，不影响发送）
    if adapter is not None:
        try:
            out["chat_id"] = _resolve_chat_id(adapter, name) or ""
            if out["chat_id"]:
                rows = adapter._db_messages(out["chat_id"], limit=5)
                out["base_id"] = int((rows[0].get("local_id") or 0)) if rows else 0
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass

    # ③ 放剪贴板（动图走 CF_HDROP，静态走 CF_DIB）
    if image:
        try:
            from agent import clipboard as _cb
            from agent import emoticon as _emo
            out["anim"] = bool(_emo.is_animated_file(image))
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            from agent import clipboard as _cb
            out["anim"] = bool(str(image).lower().endswith(".gif"))
        try:
            if out["anim"]:
                ok_cb, why_cb = _cb.set_files([image])
            else:
                ok_cb, why_cb = _cb.set_image(image)
        except Exception as e:  # noqa: BLE001
            out.update(step="clipboard", detail="放剪贴板异常：%s" % str(e)[:90])
            return out
        out["clipboard"] = {"ok": bool(ok_cb), "why": str(why_cb),
                            "channel": "CF_HDROP(动图)" if out["anim"] else "CF_DIB(静态)"}
        if not ok_cb:
            out.update(step="clipboard", detail="放剪贴板失败：%s" % why_cb)
            return out
    out.update(ok=True, step="prepared", detail="预备完成（窗=%d，剪贴板已放）" % hwnd)
    return out


def popup_fire_paste_send(prep: dict, *, key_gap: float = 0.03,
                          wait_after_paste: float = None) -> dict:
    """★ **瞬发段**：盲粘贴（真 Ctrl + 投递 V）→ 三枪提交 → **立刻还回前台**。

    ⚠️ 这一段是**唯一抢时间**的部分：会让前台短暂被微信拿走（实测 ~百毫秒级），
       结束立即 `_restore_foreground_now` 还回去。
    `wait_after_paste` 默认按档位给（最小化 0.5s / 可见 1.6s）。
    """
    import time as _t
    out = {"ok": False, "step": "", "detail": ""}
    hwnd = int((prep or {}).get("hwnd") or 0)
    if not hwnd:
        out.update(step="arg", detail="票据里没有 hwnd")
        return out
    prev_fg = int((prep or {}).get("prev_fg") or 0)
    try:
        from agent import input_backend as _IB
        be = _IB.select_backend()
    except Exception as e:  # noqa: BLE001
        out.update(step="backend", detail="取输入后端失败：%s" % str(e)[:70])
        return out

    t0 = _t.perf_counter()
    # ★ 从盲粘贴那一下起微信就会抢前台 ⇒ 整段包进收口：**成功 / 失败 / 抛异常三种出口都会还**。
    with fg_stash(out, "fg_restored") as _fg:
        _fg.note(prev_fg)
        blind = popup_paste_blind(hwnd, key_gap=key_gap)
        out["blind_paste"] = blind
        if not blind.get("ok"):
            out.update(step="paste", detail="盲粘贴未成：%s" % str(blind.get("detail"))[:90])
            return out

        _is_min = (popup_tier(hwnd) == TIER_MINIMIZED)
        # ⏱ 时间都花在"等"上，而**最小化档的"等"大多是白等**：
        #   · 等渲染（1.6s）：只是为了让"看画面"能读到缩略图；最小化档**看不到画面** ⇒ 不必等；
        #   · 回车之间的间隔：微信收到回车即发，**不需要 0.25s**，0.12s 足够（实测仍通）。
        if _is_min:
            _t.sleep(0.15 if wait_after_paste is None else float(wait_after_paste))
            _gap = 0.12
        else:
            _t.sleep(1.6 if wait_after_paste is None else float(wait_after_paste))
            _gap = 0.25

        # 提交（盲粘贴路没有几何落点 ⇒ 只投回车；第二枪是兜底）
        shots = []
        for tag in ("enter1", "enter2"):
            try:
                be.keys(hwnd, [0x0D])
                shots.append(tag)
            except Exception:
                pass
            _t.sleep(_gap)
        out["shots"] = shots

        # 还前台由收口在出块时做（时机放在提交之后：微信收到真 Ctrl 后自己会抢，
        # 抢的时机晚于抬手那一瞬 ⇒ 只在粘贴后还一次可能被它随后抢走）。
        out["fire_ms"] = round((_t.perf_counter() - t0) * 1000.0, 1)
        out.update(ok=True, step="sent",
                   detail="已投递（盲粘贴 + %s），fire 段 %.0fms"
                          % ("+".join(shots), out["fire_ms"]))
    return out


def wait_for_gap(hwnd: int, *, timeout_s: float = 30.0, poll: float = 0.02,
                 idle_need: float = 1.2, self_pids=None) -> dict:
    """★ **等"用户停手"这个边沿，然后零延迟放行** —— **不是"等一段空档"**。

    ⛔⛔ **口径（不许改）**：语义是 **边沿触发 + 立刻起跑**，不是"睡一会儿再发"。
      理由：**等待本身就是风险**——多等 1 秒，用户就可能在此刻重新开始操作，
        而这 1 秒足够 fire 段（≤0.3s）跑完。
      ⇒ `idle >= idle_need` **一成立就立刻返回 `ok=True, gap_ms=...`**，
        调用方拿到后**必须马上 `popup_fire_paste_send`，中间不许再插任何等待**。

    ⚠️ 为什么必须有这道等：那一下 `keybd_event(Ctrl)` 是**真实按键**，
      只会被**当时的前台线程**收走 ⇒ 若用户此刻正在键鼠操作，那一下会落进他正在用的窗口。
      `idle` 只解决"他此刻在不在动键鼠"；**"他能不能被借走前台"由 G1/G2/G3 安全门管**（调用方负责）。

    参数
      `hwnd`      —— 目标独立窗（仅用于日志/未来扩展；本函数**不改动任何窗口**）
      `timeout_s` —— 最长等多久；**超时返回 `ok=False`（＝放弃本次），绝不"等满了硬发"**
      `poll`      —— 轮询间隔。**默认 20ms**：保证不漏边沿（用户停手后我们 20ms 内就起跑）
      `idle_need` —— `idle` 阈值。默认 **1.2s**（= 项目既有 `PUT_BACK_IDLE_S`，**不许为了"稳"加大**：
                    加大 = 人为延长"用户停手后我们还在等"的时间 = **正好撞上用户回来**）

    返回 `{ok, gap_ms, idle_at_gap, waited_ms, why}`
    """
    import time as _t
    out = {"ok": False, "gap_ms": None, "idle_at_gap": None,
           "waited_ms": 0.0, "why": ""}
    try:
        from agent import wechat as _wx
        _idle = _wx._user_idle_seconds
    except Exception as e:  # noqa: BLE001
        # 取不到 idle（非 Windows / 导入失败）⇒ **当成"用户没在动"直接放行**，
        # 绝不把机器人卡死（与 `_user_idle_seconds` 取不到时返回 1e9 同口径）。
        out.update(ok=True, why="取不到 idle ⇒ 直接放行（绝不卡死）：%s" % str(e)[:60])
        return out

    t0 = _t.perf_counter()
    _poll = max(0.005, float(poll))
    _need = max(0.05, float(idle_need))
    while True:
        try:
            idle = float(_idle())
        except Exception:
            out.update(ok=True, waited_ms=round((_t.perf_counter() - t0) * 1000, 1),
                       why="idle 读取异常 ⇒ 直接放行（绝不卡死）")
            return out
        if idle >= _need:
            # ★★ **就这一行是发令枪**：成立 ⇒ 立刻返回，**下游零等待起跑**。
            out.update(ok=True, waited_ms=round((_t.perf_counter() - t0) * 1000, 1),
                       idle_at_gap=round(idle, 3),
                       why="用户已停手 %.2fs ⇒ 立即放行（不再多等一帧）" % idle)
            return out
        if (_t.perf_counter() - t0) >= max(0.0, float(timeout_s)):
            out.update(ok=False, waited_ms=round((_t.perf_counter() - t0) * 1000, 1),
                       idle_at_gap=round(idle, 3),
                       why="等了 %.0fs 用户仍在操作 ⇒ **放弃本次**（不硬发，绝不等满就抢）"
                           % float(timeout_s))
            return out
        _t.sleep(_poll)


@_clipboard_safe # ★ 剪贴板生命期＝prepare→fire **整段** ⇒ 保护挂在拥有整段生命期的这个入口上
def popup_send_image_stealth(chat_name: str, image: str = None, *, adapter=None,
                             gap_timeout_s: float = 30.0, key_gap: float = 0.03,
                             need_gap: bool = True) -> dict:
    """★ **隐形瞬发编排**：prepare →（等用户停手）→ fire → 立即返回。

    ⛔ 编排纪律（用户明口径）：
      · `wait_for_gap` 返回后 **立刻 `fire`**，**中间不许插任何"再确认一下"**
        （那正是"等 1 秒"的来源，也正是实测最慢的一处）。
      · 最小化档不走透明档（透明档已否）；**判据以 DB 回读为准**（调用方异步核实）。
      · `need_gap=False` 时跳过等待（G1/G2/G3 已满足且调用方确认用户没在操作时用）。

    返回 `{ok, step, detail, prep, gap, fire}`
    """
    out = {"ok": False, "step": "", "detail": "", "prep": None, "gap": None, "fire": None}
    try:
        prep = popup_prepare_send(chat_name, image=image, adapter=adapter)
    except Exception as e:  # noqa: BLE001
        out.update(step="prepare", detail="预备阶段异常：%s" % str(e)[:90])
        return out
    out["prep"] = prep
    if not (prep or {}).get("ok"):
        out.update(step="prepare", detail="预备失败：%s" % str((prep or {}).get("detail"))[:90])
        return out

    hwnd = int((prep or {}).get("hwnd") or 0)
    if need_gap:
        gap = wait_for_gap(hwnd, timeout_s=gap_timeout_s)
        out["gap"] = gap
        if not gap.get("ok"):
            out.update(step="gap_timeout", detail=gap.get("why") or "等用户停手超时 ⇒ 放弃本次")
            return out
    # ★★ 就这里起跑 —— 上一行返回后**不许有任何 sleep / 再确认**。
    fire = popup_fire_paste_send(prep, key_gap=key_gap)
    out["fire"] = fire
    if not fire.get("ok"):
        out.update(step="fire", detail="瞬发失败：%s" % str(fire.get("detail"))[:90])
        return out
    out.update(ok=True, step="sent",
               detail="已投递（等空档 %sms ⇒ fire %sms）"
                      % ((out["gap"] or {}).get("waited_ms"), fire.get("fire_ms")))
    return out


@_clipboard_safe
def send_image_to_popup(chat_name: str, local_path: str, *, adapter=None,
                        timeout_s: float = 300.0, is_image: bool = True,
                        rect_hint=None, allow_menu_fallback: bool = True,
                        confirm_delivered: bool = True,
                        gap_timeout_s: float = 30.0,
                        need_gap: bool = True) -> dict:
    """★ **向独立窗直发图片/动态表情**（**一窗一会话 ⇒ 不需要再确认会话**）。

    ⭐⭐ **为什么要有它**：独立窗**一窗一会话**（标题即会话名），
      有消息直接往对应那个窗发即可，**不需要再确认"当前是哪个会话"**；
      且窗口最小化时句柄仍有效、仍能投递。

    ⛔ **为什么不能直接用 `WeChatAdapter.send_image_posted`**：
      它开头就 `chat_is_open(chat_id)` —— 那要靠**主窗**的绿带高亮行 OCR + 会话头
      ⇒ 窗口最小化/被遮挡时**必然判否**（实测：`投递发图要求目标会话已打开：当前会话 OCR=''···
        没找到绿底高亮行`）。可**独立窗自己就说明了"这是哪个会话"**（标题即会话名），
      不需要再去主窗找证据。

    ✅ **本通路的做法**：
      ① **文件类型**：动图 ⇒ 走"文件"通道（`CF_HDROP`，微信按**动画表情**收，动效保住）；
         静态图 ⇒ 走位图通道（`CF_DIB`）。
      ② **粘贴**：在**输入框几何落点**上右键 → 「粘贴」菜单项（不依赖画面判据）。
      ③ **提交**：三枪（回车 → 点发送 → 回车兜底），落点用**窗口比例**（`SEND_BTN_REL`）。
      ④ **成败判据 = DB 回读**（唯一可靠；最小化也成立）。
         ⚠️ **图片/表情落库很慢**：实测 30~180s（`[图片]`/`[动画表情]` 都要等客户端把
           资源上传完才写库）⇒ 默认 `timeout_s` 给到 **300s**；
           曾用 90s ⇒ **实际已送达却判成"未确认"**；再放宽到 300s 以覆盖
           "上传慢 + 落库慢"叠加的极端情形。

    ⚠️ **前提**：`chat_name` 有**独立窗**（`find_popup` 命中）。没有 ⇒ 返回失败，**不盲投主窗**。
    参数
      `is_image` —— `True`（默认）＝图片/表情：通道由"这个文件**会不会动**"决定
        （动图走 `CF_HDROP`、静态图走 `CF_DIB`）。
        `False` ＝**任意文件**（`.zip`/`.txt`/`.pdf`…）：**强制走 `CF_HDROP` 文件通道**
        —— 若照"会不会动"去判，`.zip` 会被判成静态 ⇒ 走 `set_image()`（位图通道）
        ⇒ 必然放不进剪贴板、或放进去也不是那个文件。
    """
    out = {"ok": False, "step": "", "detail": "", "hwnd": 0, "chat": chat_name}
    # ⛔ **暂停/停止闸**：独立窗直发原先**不查 control** ⇒
    #   暂停期间图/表情照样发得出去。堵在入口（与文本链同口径）。
    try:
        from agent import control as _ctl1
        _halt1 = _ctl1.halt_reason()
        if _halt1:
            out.update(step="halted", detail=_halt1)
            return out
    except Exception:
        pass
    # ⛔ 窗口标题只有**显示名** ⇒ username 要先解析。
    hit = find_popup(resolve_title_name(chat_name, adapter))
    hwnd = int(hit.get("hwnd") or 0)
    if not hwnd:
        out.update(step="locate", detail="没有 %r 的独立窗 ⇒ 不盲投主窗（请先 ensure_popup）" % chat_name)
        return out
    if not local_path:
        out.update(step="arg", detail="没给图片路径")
        return out
    out["hwnd"] = hwnd
    out["title"] = hit.get("title")
    out["iconic"] = int(_is_iconic(hwnd))

    try:
        from agent import input_backend as IB
        from agent import clipboard as _cb
        be = IB.select_backend()
    except Exception as e:  # noqa: BLE001
        out.update(step="backend", detail="取后端/剪贴板失败：%s" % str(e)[:90])
        return out

    import time as _t

    # ① 基线（DB 最新 local_id）—— 用于回读判"有没有新图片"
    chat_id = _resolve_chat_id(adapter, chat_name) if adapter is not None else ""
    base_id = 0
    if chat_id:
        try:
            rows = adapter._db_messages(chat_id, limit=5)
            base_id = int((rows[0].get("local_id") or 0)) if rows else 0
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            base_id = 0
    out["chat_id"] = chat_id

    # ② 放剪贴板（动图走 CF_HDROP，静态走 CF_DIB）
    #   `is_image=False` ⇒ **强制文件通道**：调用方要发的是"文件"而不是图，
    #   此时"会不会动"这条判据无意义（`.zip` 必然判成静态 ⇒ 走位图通道 ⇒ 发错/发不出）。
    _anim = False
    if not is_image:
        _anim = True
    else:
        try:
            from agent import emoticon as _emo
            _anim = bool(_emo.is_animated_file(local_path))
        except Exception:
            _anim = bool(str(local_path).lower().endswith(".gif"))
    try:
        if _anim:
            ok_cb, why_cb = _cb.set_files([local_path])
        else:
            ok_cb, why_cb = _cb.set_image(local_path)
    except Exception as e:  # noqa: BLE001
        out.update(step="clipboard", detail="放剪贴板异常：%s" % str(e)[:90])
        return out
    out["clipboard"] = {"ok": bool(ok_cb), "why": str(why_cb),
                        "channel": ("CF_HDROP(文件)" if not is_image
                                    else ("CF_HDROP(动图)" if _anim else "CF_DIB(静态)"))}
    if not ok_cb:
        out.update(step="clipboard", detail="放剪贴板失败：%s" % why_cb)
        return out

    # ③ 几何落点（**全用窗口比例，不依赖画面**）
    #   ⛔⛔ **必须用 `window_rect_any_state`**：
    #     窗口最小化时 `window_rect_physical` 给的是离屏哨兵 `(-32000,-32000,...)`
    #     ⇒ 算出的落点是 `(-31881,-31965)` 这种**荒谬坐标**，点击等于打虚空。
    #   ⚠️⚠️ **更狠的事实（二次实测）**：窗口**真正最小化**（`showCmd=2`）时，
    #     **连 `rcNormalPosition` 也是哨兵** ⇒ `window_rect_any_state` **也救不回来**。
    #     ⇒ 唯一可靠办法：**最小化之前把矩形存下来**，最小化后当 `rect_hint` 传进来。
    #       （调用方在窗口可见时先 `window_rect_any_state()` 存一份即可。）
    # ★★★ ③ **首选通路：盲粘贴 —— "真按 Ctrl + 投递 V"**（零坐标 ⇒ 最小化/后台档都成立）
    #   实测：**最小化档**下 DB 落到 `[图片]` / `[动画表情]`。
    #   ⚠️ 忙等实测**更正**（35433 次采样）：**窗口零像素上屏**（用户看不到）；
    #      但**投递的那个 `V` 会让微信自己把独立窗置前**（`Weixin.dll` 导入表含
    #      `SetForegroundWindow`）⇒ 前台被抓到 **3 次、每次约 0.1 毫秒**（0.1057 / 0.0876 /
    #      0.0952 秒的千分之一量级），随即被 `fg_stash` 还回。**不是** `keybd_event` 那个 Ctrl
    #      造成的（单测：只按 Ctrl ⇒ 前台 0/481 次变化）。
    #      ⇒ 旧写法"不抢前台"是**过头话**；准确说法＝**不动光标 + 窗口不上屏 + 前台可能被
    #        抢 0.1ms 后立刻还回**（人眼绝对看不到）。原理与副作用见 `popup_paste_blind` 的 docstring。
    #
    # ★★★ ③.0 **等用户停手（边沿触发 + 零延迟起跑）** —— 接入主链
    #   为什么必须在这一步：盲粘贴里的**真 Ctrl** 会落在**当时的前台窗口**（这是全流程
    #   唯一"会进系统输入队列"的动作）⇒ 用户正在打字时发图，那个 Ctrl 可能被他"吃掉"
    #   （或反过来，我们抢先打断他）。⇒ **开火前先等他一停手**。
    #   语义（用户明口径）：**不是"睡一会儿再发"**，而是 `idle >= idle_need` **一成立就
    #   立刻开火**；放行之后**不许再插任何 sleep / 再确认**（那正是"等 1 秒"的来源）。
    #   `need_gap=False` 时跳过（调用方已确认用户没在操作时用，如批处理/回放）。
    if need_gap:
        _gap = wait_for_gap(hwnd, timeout_s=gap_timeout_s)
        out["gap"] = _gap
        if not _gap.get("ok"):
            out.update(ok=False, step="gap_timeout",
                       detail="等用户停手超时 ⇒ **放弃本次**（%s）" % str(_gap.get("why"))[:80])
            return out
    # ★★ 就这里起跑 —— 上一行返回后**不许有任何 sleep / 再确认**。
    _pasted = False
    _blind = popup_paste_blind(hwnd)
    out["blind_paste"] = _blind
    # ⏱ 粘贴后的等待**按档位给**：
    #   · 可见/透明档：要看画面判"进没进框" ⇒ 等渲染（1.6s）；
    #   · **最小化档：抓不到画面（PrintWindow 给纯白帧）** ⇒ 等画面没意义，
    #     只需让投递的 V 落地 ⇒ **0.5s** 足够（实测仍稳），省下 1.1s。
    _is_min = (popup_tier(hwnd) == TIER_MINIMIZED)
    _t.sleep(0.5 if _is_min else 1.6)
    if _blind.get("ok"):
        _g = popup_composer_green(hwnd)
        out["green_after_blind"] = round(_g, 3)
        # 看不到画面（最小化档 ⇒ `_g < 0`）时只能信它，最终由 DB 回读兜底
        if _g < 0 or _g >= POPUP_GREEN_HAS_CONTENT:
            _pasted = True
            out["paste_channel"] = "ctrl_v_blind"
            out["paste_green"] = round(_g, 3)
    else:
        out["blind_why"] = str(_blind.get("detail"))[:80]

    send_pt = None
    if not _pasted:
        # ⛔ 调用方可以禁止"退回几何右键路"——那条**会抢前台**（盲粘贴不会）。
        if not allow_menu_fallback:
            out.update(ok=False, step="paste_blocked",
                       detail="盲粘贴没成，且调用方不允许退回「几何落点 + 右键菜单」路"
                              "（那条会**抢前台**）⇒ 不发任何枪")
            return out
        # ── ④ 备选通路：几何落点 + 右键「粘贴」（需要**真实矩形** ⇒ 最小化档到此为止）──
        r = None
        if rect_hint:
            try:
                h_ = tuple(int(v) for v in rect_hint)
                if len(h_) == 4 and h_[2] > h_[0] and h_[3] > h_[1]:
                    r = h_
                    out["rect_source"] = "rect_hint(最小化前存的)"
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                r = None
        if r is None:
            try:
                r = window_rect_any_state(hwnd)
                out["rect_source"] = "window_rect_any_state"
            except Exception as e:  # noqa: BLE001
                out.update(step="rect", detail="取窗口矩形失败：%s" % str(e)[:80])
                return out
        out["rect_used"] = r
        out["rect_is_normal"] = not (int(r[0]) <= -10000 or int(r[1]) <= -10000)
        if not out["rect_is_normal"]:
            out.update(step="rect_offscreen",
                       detail="盲粘贴没成，而窗口矩形又是离屏哨兵 %s ⇒ **拿不到落点，拒绝盲投**"
                              "（⛔ 最小化下投坐标点击会把窗口打进离屏僵死态，绝不硬试）" % (r,))
            return out
        #   ⛔⛔ 落点一律**从底边往上量**（`popup_input_points`）—— 旧写法 `0.88 * H`（从顶边量）
        #     实测**踩到输入框之外**，右键弹不出菜单 ⇒ 整条发图路死掉。见 `POPUP_INBOX_REL`。
        W_ = max(1, int(r[2] - r[0]))
        H_ = max(1, int(r[3] - r[1]))
        _pts = popup_input_points(r)
        in_x, in_y = _pts[0]
        send_pt = (int(r[0] + W_ * SEND_BTN_REL[0]), int(r[1] + H_ * SEND_BTN_REL[1]))
        out["input_pt"] = (in_x, in_y)
        out["input_pt_candidates"] = _pts
        out["send_pt"] = send_pt

        #   ⭐ **为什么要"自检重试"**（实测踩到）：
        #     独立窗当时**不是前台**时，**第一次左键会被"激活窗口"吃掉** ⇒ 输入框没拿到焦点
        #     ⇒ 右键菜单照样弹、也点了「粘贴」，但**框里什么都没有**。
        #     实测后果：`ok=True, step=sent` 全绿，**DB 却一行新消息都没有**。
        #     ✅ 判据 = **发送键是否变绿**（`popup_composer_green`）；不绿 ⇒ 换下一个落点重来。
        _try_pts = list(_pts) + list(_pts[:4])
        _tries = []
        ok_paste = False
        for _p in _try_pts:
            try:
                be.click(hwnd, _p, hover_ms=60, press_ms=60)      # 聚焦输入框
                _t.sleep(0.35)
            except Exception as e:  # noqa: BLE001
                _tries.append({"pt": _p, "stage": "focus", "err": str(e)[:50]})
                continue
            _m = popup_right_click_menu(hwnd, _p, "粘贴", delay=1.2)
            _rec = {"pt": _p, "menu": bool(_m.get("ok")),
                    "detail": str(_m.get("detail"))[:70]}
            if not _m.get("ok"):
                try:
                    close_menu_windows(0, _m.get("menus"))
                except Exception:
                    pass
                _tries.append(_rec)
                continue
            _t.sleep(1.4)                                          # 等缩略图渲染
            _g = popup_composer_green(hwnd)
            _rec["green"] = round(_g, 3)
            _tries.append(_rec)
            if _g < 0 or _g >= POPUP_GREEN_HAS_CONTENT:
                ok_paste = True
                out["paste_point"] = _p
                out["paste_green"] = round(_g, 3)
                break
            # 没粘进去 ⇒ 框里是空的，接着试下一个落点
            try:
                be.keys(hwnd, [0x23]); be.keys(hwnd, [0x08] * 20)
            except Exception:
                pass
        out["paste_tries"] = _tries
        out["paste_attempts"] = len(_tries)
        if not ok_paste:
            out.update(ok=False, step="paste",
                       detail="盲粘贴没成，几何路径试了 %d 次也没能把内容放进输入框（%s）"
                              "⇒ **不发任何枪**"
                              % (len(_tries), _tries[-1] if _tries else {}))
            return out
        out["paste_channel"] = "right_click_menu"

    # ⑤ 三枪提交（回车 → 发送 → 回车）
    shots = []
    try:
        be.keys(hwnd, [0x0D])
        shots.append("enter1")
    except Exception:
        pass
    _t.sleep(0.4)
    try:
        if send_pt:                       # 盲粘贴路没有几何落点 ⇒ 跳过"点发送键"
            be.click(hwnd, send_pt, hover_ms=60, press_ms=120)
            shots.append("btn")
    except Exception:
        pass
    _t.sleep(0.4)
    try:
        be.keys(hwnd, [0x0D])
        shots.append("enter2")
    except Exception:
        pass
    out["shots"] = shots

    # ★★ ⑤.2 **把前台还回去**（放在提交之后：微信收到真 Ctrl 后自己会抢前台，
    #   抢的时机晚于抬手那一瞬 ⇒ 只在粘贴后还一次可能被它随后抢走）。
    #   ⇒ 这里在"动作都做完"之后再还一次，并**确认稳定**。
    _fprev = int((out.get("blind_paste") or {}).get("fg_prev") or 0)
    if _fprev:
        out["fg_restored"] = bool(_restore_foreground_now(_fprev))

    # ★★ ⑤.5 **秒级"已受理"判据**（新增，解决"DB 落库太慢"）
    #   原理：微信一"受理"发送，就**立刻把输入框清空** ⇒ 发送键**从绿变灰**。
    #     实测：从投回车到变灰 **0.36s**；而 DB 出现新行要 **12~180s**
    #     （图片/表情还要先上传完才写库）⇒ **"已受理"比"已落库"快两个数量级**。
    #   ⚠️ 前提：能抓到画面。**全透明隐身（`set_popup_invisible`）下 `PrintWindow` 照常工作**
    #     ⇒ 透明态也能用。抓不到画面（真正最小化）时 `_g < 0`，此时只能交给 DB 兜底。
    _g_after = popup_composer_green(hwnd)
    out["green_after_send"] = round(_g_after, 3)
    if _g_after is not None and 0 <= _g_after < POPUP_GREEN_HAS_CONTENT:
        out["accepted"] = True
        out["accepted_detail"] = "输入框已清空（发送键变灰，绿占比 %.3f）⇒ 微信**已受理**" % _g_after
    else:
        # ⚡ **B 级补位（事件驱动；真最小化也能用）**：抓不到画面（`_g is None` / 看不出清空）时，
        #   以前只能说"交给 DB 回读兜底"——而调用方默认**不等** DB ⇒ 用户拿不到确定结论。
        #   ⇒ 这里用 `wait_new_message` 的**事件**部分（`filewatch` 盯 `message_*.db-wal`）
        #     **当场**问一句"库有动静了吗"：一有写入立刻返回（比固定轮询快、空闲零开销）。
        #   ⛔ 只当"**已受理**"用，**不当"送达"**（语义/类型确认仍由 DB 那一步负责）。
        _acc2 = None
        try:
            _cid = _resolve_chat_id(adapter, chat_name) if adapter is not None else ""
            _w2 = getattr(adapter, "wait_new_message", None)
            if _cid and _w2 is not None:
                _o2, _wh2, _i2 = _w2(_cid, timeout=ACCEPT_EVENT_WAIT_S)
                if _o2:
                    _acc2 = ("库已有写入（事件驱动 %.2fs/%s）"
                             % ((_i2 or {}).get("waited") or 0, (_i2 or {}).get("mode")))
        except Exception:  # noqa: BLE001
            _acc2 = None
        out["accepted"] = True if _acc2 else None
        out["accepted_detail"] = ((_acc2 + "（画面看不出输入框清空：绿占比 %s）" % _g_after) if _acc2
                                  else "看不出输入框是否清空（绿占比 %s）⇒ 交给 DB 回读兜底" % _g_after)

    # ⑥ DB 回读（**唯一能证明"真的送达"的判据**；很慢，默认**不等**）
    out.update(ok=True, step="sent",
               detail="已投递（通道=%s；%s）；%s"
                      % (out.get("paste_channel", "?"), "+".join(shots),
                         out["accepted_detail"]))
    if not confirm_delivered:
        out["db_verified"] = None
        out["detail"] += "；**未等 DB**（`confirm_delivered=False`）"
        return out
    if adapter is None or not chat_id:
        out["detail"] = "已投递 ⇒ 成功；**无 adapter/chat_id ⇒ 跳过 DB 回读**"
        return out
    # ⚡ 同上一处：**先用事件驱动等"库有写入"，再做语义确认**；老桩/异常退回原轮询。
    _hit, _w = False, getattr(adapter, "wait_new_message", None)
    if _w is not None:
        try:
            _o4, _wh4, _i4 = _w(chat_id, base=(base_id or None), timeout=float(timeout_s))
            _hit = bool(_o4) and _db_new_image(adapter, chat_id, since_id=base_id)
            out["wait_evt"] = {"waited": (_i4 or {}).get("waited"), "mode": (_i4 or {}).get("mode")}
        except Exception:  # noqa: BLE001
            _hit = False
    if not _hit:
        _deadline = _t.time() + max(2.0, float(timeout_s) * 0.5)
        while _t.time() < _deadline:
            _t.sleep(1.0)
            if _db_new_image(adapter, chat_id, since_id=base_id):
                _hit = True
                break
    if _hit:
        out.update(db_verified=True, detail="★ 已发送并 **DB 回读确认**（图片/表情新行已入库%s）"
                                            % ("，事件驱动 %.2fs/%s" % (out["wait_evt"]["waited"],
                                                                       out["wait_evt"]["mode"])
                                               if out.get("wait_evt", {}).get("waited") is not None else ""))
        return out
    out["db_verified"] = False
    out["detail"] = ("已投递（%s）但 **%ds 内 DB 没看到新的图片/表情行**"
                     "（图片落库实测可慢到 30~180s）；不宣称已送达"
                     % (out.get("accepted_detail", ""), int(timeout_s)))
    return out


def send_file_blind(chat_name: str, local_path: str, *, adapter=None,
                    timeout_s: float = IMAGE_CONFIRM_TIMEOUT_S,
                    confirm_delivered: bool = False, **kw) -> dict:
    """★ **向独立窗直发任意文件**（最小化/后台档成立）—— `send_image_to_popup` 的**文件口径**封装。

    ⭐ 为什么需要这一层（而不是复用主链那条发文件路）：
      `wechat.send_file_posted` 走的是「投递点工具栏『文件』图标 → 系统『选择文件』对话框
      → UIA 填名 → 发送」，它**要求主窗可见**（会话闸靠主窗 OCR 判"目标会话已打开"），
      而且**会弹出系统文件对话框**（打断用户）⇒ 窗口最小化时**必然走不通**。
      而独立窗"一窗一会话、标题即会话名" ⇒ **不需要任何会话判据**；
      配上"剪贴板放文件列表（`CF_HDROP`）＋ 盲粘贴（真按 Ctrl ＋ 投递 V，零坐标）"
      ⇒ 最小化档同样成立（该机制在**动图**分支上已实测：DB 落到 `[动画表情]`）。

    ⚠️ **发出去的是什么，取决于微信对 `CF_HDROP` 的归类**（尚未真机标定）：
      `.zip`/`.txt` 粘贴后微信可能收成**文件卡**，也可能收成别的类型。
      DB 回读（`_db_new_image` 里"非文本类型也算发出去一条"已罩住文件卡）
      **只证明"发出了一条非文本消息"，不证明"发出去的正是这个文件"**
      ⇒ 首次真机验收必须**人眼看一眼会话里的卡片类型**，再决定是否接默认路。

    ⛔ 判据口径与 `send_image_to_popup` 一致：**不依赖画面**（最小化抓不到帧），
      只用 DB 回读；`confirm_delivered=False`（默认）时不等 DB（文件落库同样慢，实测 30~180s）。
    """
    _r = send_image_to_popup(chat_name, local_path, adapter=adapter,
                             timeout_s=timeout_s, is_image=False,
                             confirm_delivered=confirm_delivered, **kw)
    try:
        if isinstance(_r, dict):
            _r["as_file"] = True
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    return _r


def send_file_to_popup(chat_name: str, paths, *, is_image: bool = False,
                       timeout_s: float = 8.0, dry_run: bool = False) -> dict:
    """⚠️ **【已停用 / 重复实现】发图片/文件请改用项目既有链** —— 保留仅为记录踩坑过程。

    ⛔ **为什么停用（报告 §68）**：
      1. **重复实现**：项目里早有成品，功能比这里完整得多 ——
         `wechat.send_image()` / `send_image_posted()` / `send_file_posted()`，
         以及表情链 `tools._exec_send_emoji()` + `emoticon.py`。
         它们已经解决：剪贴板放置（静态图走 CF_DIB、**动图走 CF_HDROP**）、
         **右键菜单「粘贴」**、投递点发送、**DB 回读**、失败退回真实路径等。
      2. **用了已判死的通道**：本函数走 `input_backend.paste()`（`WM_PASTE`），
         而项目 `wechat.py` 里早已用现场实测**把这条路判死**：
            "投递的消息**不携带修饰键状态** ⇒ 组合键退化成字面字母 v（实测冒出 `vvaavv`；五种变体全败）
             ⇒ 改走**微信自己的动作路径：输入框右键 →「粘贴」菜单项**（全程投递，不需修饰键）
             ⇒ 实测这条**真能把图放进输入框**（缩略图出现、发送按钮变绿）。"
         ⇒ 本函数实测"卡片没进输入框"，**与该结论完全一致**。

    ✅ **正确用法**：要发图片/文件，**调用 `wechat.send_image()` / `send_file_posted()`**；
       "独立弹窗"这条线**只需负责"把目标定位/确认到独立弹窗"**（读窗口标题 → 拿 hwnd），
       **发送动作本身复用既有链**。

    以下为原始实现（仅作记录，勿在新代码中调用）——
    """
    """向独立弹窗发**图片/文件** ⇒ `{ok, step, detail, clipboard, ...}`。

    ⭐ 通道选择（已查清，报告 §67）
      微信是 Qt 应用 ⇒ 用 **OLE 拖放（`IDropTarget`）**，**不认老式 `WM_DROPFILES`**
      ⇒ 投 `WM_DROPFILES` 会被**静默丢弃**（实测"返回 True 却无效果"）
      ⇒ 所以**文件/图片走 `剪贴板 + WM_PASTE`** —— 这也正是项目原本的设计
        （`clipboard.py` 文件头写的就是这条）。

    ⛔ **必须用 `clipboard_guard` 包住**：写剪贴板会**覆盖用户正在复制的内容**。
       当天已实际发生过一次冲突（用户粘贴出来的成了我们的测试文字，§64）。
       ⇒ 发完**立刻还回去**（`guard` 在退出时自动恢复；异常也恢复）。

    `is_image=True` ⇒ 走 `set_image`（CF_DIB）；否则走 `set_files`（CF_HDROP）。
    `dry_run=True` ⇒ 只把东西贴进输入框、**不点发送**，随后清空（用于验收）。
    """
    out = {"ok": False, "step": "", "detail": "", "hwnd": 0, "chat": chat_name}
    if isinstance(paths, str):
        paths = [paths]
    paths = [str(p) for p in (paths or [])]
    if not paths:
        out.update(step="args", detail="没有文件路径")
        return out

    hit = find_chat_window(chat_name)
    hwnd = int(hit.get("hwnd") or 0)
    if not hwnd:
        out.update(step="locate", detail="按标题找不到窗口；候选=%s" % hit.get("candidates"))
        return out
    out["hwnd"] = hwnd
    out["title"] = hit.get("title")

    try:
        from agent import input_backend as IB
        from agent import clipboard as CB
        be = IB.select_backend()
    except Exception as e:
        out.update(step="imports", detail="依赖导入失败：%s" % str(e)[:80])
        return out

    def _content():
        im, _m = grab_window(hwnd, physical=False)
        return (content_ratio(im)[0] if im else -1.0), im

    base, _im0 = _content()
    if base < 0:
        out.update(step="grab", detail="抓图失败")
        return out
    out["content_before"] = round(base, 2)

    # ★ 备份用户剪贴板 → 写我们的内容 → 粘贴 → 恢复
    guard = CB.clipboard_guard()
    guard.__enter__()
    try:
        w = CB.set_image(paths[0]) if is_image else CB.set_files(paths)
        out["clipboard_set"] = str(w)
        if not (w and w[0]):
            out.update(step="clipboard", detail="写剪贴板失败：%s" % str(w))
            return out
        try:
            pr = be.paste(hwnd)          # WM_PASTE（内部自带 _wake）
        except Exception as e:
            out.update(step="paste", detail="投递 WM_PASTE 异常：%s" % str(e)[:80])
            return out
        out["paste_ret"] = str(pr)
        import time as _t
        t0 = _t.time()
        after = base
        while _t.time() - t0 < float(timeout_s):
            after, _imA = _content()
            if abs(after - base) > 0.3:      # 卡片出现 ⇒ 内容占比变化
                break
            _t.sleep(0.3)
        out["content_after"] = round(after, 2)
        if abs(after - base) <= 0.3:
            out.update(step="verify_pasted",
                       detail="粘贴后内容占比未变（%.2f→%.2f）⇒ 卡片没进输入框" % (base, after))
            return out
        out["ok"] = True
        out["step"] = "pasted"

        if dry_run:
            # 清空卡片（退格/Delete）—— 文件卡片用退格可删
            try:
                for _ in range(3):
                    be.keys(hwnd, [0x23]); _t.sleep(0.12)
                    be.keys(hwnd, [0x08] * 20); _t.sleep(0.18)
                    be.keys(hwnd, [0x2E]); _t.sleep(0.12)
            except Exception:
                pass
            out["detail"] = "dry_run：卡片已进入输入框，已清空，**未点发送**"
            return out

        # 点「发送」（比例位置）
        try:
            import ctypes as _ct
            import ctypes.wintypes as _wt
            rct = _wt.RECT()
            _user32.GetWindowRect(hwnd, _ct.byref(rct))
            _im, _m2 = grab_window(hwnd, physical=False)
            sx = int(rct.left + _im.size[0] * SEND_BTN_REL[0])
            sy = int(rct.top + _im.size[1] * SEND_BTN_REL[1])
            out["click_ret"] = str(be.click(hwnd, (sx, sy), press_ms=120))
            out["click_pt"] = (sx, sy)
        except Exception as e:
            out.update(step="click_send", detail="点发送异常：%s" % str(e)[:80])
            return out
        _t.sleep(1.5)
        fin, _ = _content()
        out["content_final"] = round(fin, 2)
        out.update(step="sent", detail="已粘贴并点发送；内容占比 %.2f→%.2f" % (base, after))
        return out
    finally:
        # ★ 无论如何都还用户剪贴板
        guard.__exit__(None, None, None)
        out["clipboard"] = guard.result[1] if isinstance(guard.result, tuple) else str(guard.result)


# ─────────────────────────────────────────────────────────────────────────────
# ★ 顶层：**保证"目标会话的独立弹窗"存在**（认会话 → 双击成窗 → 自愈）
# ─────────────────────────────────────────────────────────────────────────────

#: 会话名在行内的**横向落点比例**（相对**列表右沿 pane**；点名字，不点头像/时间）
ROW_X_REL = 0.60
#: 兜底行高（**仅在动态测量失败时**使用；⚠️ 实测会随【窗口+字号】变，优先动态量）
ROW_PITCH_FALLBACK = 65

#: 双击两枪的间隔（秒）—— **人手实测真值**（本机全局鼠标钩子录得 DOWN 相邻间隔
#  172/179/179ms，落点差 ≤1px；36 下点击全不漏）。
#  ⚠️ 这是"双击 / 两次独立单击 / 拖拽"三者的分水岭：**间隔拉长 ⇒ 微信结算成两次独立单击
#    ⇒ 高亮被取消、开不出窗**；两枪挨太近又可能被按"位置+间隔"判成拖拽。改它之前先重录人手间隔。
#  ⛔ **真值只此一份**：`ensure_popup` 与 `wechat.py::open_chat_by_search` 的双击共用它，
#    不要在别处另写一个数字（两处漂移会导致"一处能开窗、一处开不出"）。
DOUBLE_GAP_S = 0.175
#: 单枪"按下→抬起"时长（毫秒）—— 人手实测 40~80ms，取偏小值以贴近快双击。
PRESS_MS_DOUBLE = 40
#: 兜底"列表右沿 pane"（**物理像素**；仅在 `chat_header.pane_left_for` 也量不到时用）
#: ⚠️ 实测本机 pane=256（那**偏小**）⇒ 落点 x 会被安全带限制；真值约 350~420。
ROW_PANE_FALLBACK = 350
#: 兜底"名字区左沿"（**物理像素**；头像方块右沿之后）。实测本机 ≈ 180。
ROW_NAME_LEFT_FALLBACK = 180
#: 兜底首行中心 y（**物理像素**，相对主窗顶部；⚠️ 同上，优先动态量）
#: 实测（本机、默认字号、窗口 1356×1078）：**列表顶 y0 = 119 物理**，
#:   首行中心 ≈ 119 + 96/2 ≈ 167 物理。旧值 112 是**逻辑**且偏小 ⇒ 落点会落到顶栏上。
ROW0_Y_FALLBACK = 167

# ⛔⛔ **落点常量一律不许写死**：
#   ① **行高**会随窗口尺寸与字号变：同一台机器，窗口 1113×909 时 97 物理；字号 +3 档后 119 物理。
#   ② **首行 y** 同理（列表顶 = 搜索框下沿往下，实测 119 物理）。
#      ⛔ 旧兜底 112（且被当成逻辑值用）会让落点**落到顶栏/搜索框上**
#      ⇒ **双击顶栏 = 微信全屏**（不是打开会话窗）。
#   ⇒ 本模块一律**现量**：
#        `measure_row_geometry()` 给行高（走 `row_geometry.green_band` 唯一实现），
#        `measure_list_top()` 给列表顶（搜索框下沿往下）。
#      两个都量不到才退兜底，且**必须如实标注** `geo_fallback`。


def measure_row_geometry(main_hwnd: int) -> dict:
    """★ **现量"会话列表行几何"** ⇒ `{ok, row_pitch, first_row_y, scale, how, ...}`（**逻辑像素**）。

    ⛔⛔ **本函数原来自己写了一套绿底判据，已废弃（实测证伪）**：
        旧判据 `(g > r + 20) & (g > b + 20)`，取样窗取 `lint*0.05 ~ lint*0.22`
        —— 实测那一段**正好罩住左侧图标栏**（图标栏的绿色图标 x 41..167 命中），
        真正的会话高亮块在 `x ≈ 0.30w` 之后 ⇒ **判据恒假或误判**。
      ⇒ 现在一律走**项目正规入口**，不再自造轮子：
        · 行高 & 绿带位置 → `row_geometry.green_band()`（颜色定值 `chat_ocr.GREEN` + score 排序，
          是全项目唯一实现；实测 315px~2526px 宽、浅深两主题，绿带高 96~97px 极差 1px）
        · 列表右沿 pane → `chat_header.pane_left_for()`

    ⚠️ **为什么行高必须现量**（实测铁证 同一台机器）：
      · 窗口 1113×909、默认字号：绿带高 **97 物理**（=64.67 逻辑）
      · 窗口 1356×1078、**字号调大三档**：绿带高 **119 物理**（=79.3 逻辑）
      ⇒ 行高随【窗口尺寸 + 字号】变，**写死任何常量都会点偏行**。

    ⚠️ **本函数只量"行高"和"绿带那一行的 y"**，**不假设列表滚到顶**：
      `first_row_y` 字段只在"能确定绿带行序号"时才有意义（由 `find_current_session_order`
      读出绿带行名字→反查 DB 序号后，调用方自己算）；否则返回 `first_row_y=None`。

    ★ 三种 `how`（按可信度降序）：
      · `green_band`   —— 有绿带 ⇒ 行高＝绿带高（**实测**，最准，并会**落盘记住**）
      · `cached_row_h` —— 无绿带但**记住过行高**（首次打开时实测落盘的值）⇒ `row_pitch` 仍可用，
                          **但绿带行身份未知**（`green_y=None`）
      · `no_green`     —— 无绿带且没记住过 ⇒ 量不到，如实返回（调用方可先点第一行造高亮）
    """
    out = {"ok": False, "how": "", "row_pitch": 0, "first_row_y": None,
           "scale": 1.0, "green_y": None, "pane": 0}
    try:
        im, meta = grab_window(int(main_hwnd), physical=True)
        if im is None:
            return out
        scale = float(meta.get("scale") or 1.5)
        out["scale"] = scale
        W, H = im.size

        # 列表右沿（结构量，跨字号稳定）
        pane = 0
        try:
            from agent import chat_header as _ch
            pane = int(_ch.pane_left_for(im) or 0)
        except Exception:
            pane = 0
        out["pane"] = pane

        # ① 行高 & 绿带（**唯一实现**：row_geometry.green_band）
        try:
            from agent import row_geometry as _rg
            band = _rg.green_band(im) or {}
        except Exception:
            band = {}
        if band:
            y0, y1 = int(band["y0"]), int(band["y1"])
            out.update(ok=True, how="green_band",
                       row_pitch=(y1 - y0) / scale,
                       green_y=(y0, y1),
                       green_center=(y0 + y1) / 2.0 / scale,
                       row_y_range=(y0 / scale, y1 / scale),
                       green_score=band.get("score"))
            return out

        # ② 无绿带（列表里没有高亮 / 高亮被滚出可见区）
        #    ★ 先用**记住的行高**（`row_geometry.cached_row_h`）：
        #      行高只跟"微信的字号设置"走，用户几乎不改 ⇒ 属**慢变量**。
        #      第一次在有绿带时量到就落盘，之后每帧都可直接用 ⇒ 不必为了"量行高"而
        #      强行点一行造高亮（那会切会话、有副作用）。
        #      ⚠️ 缓存只给"行高"；**绿带那一行的身份仍未知** ⇒ `green_y=None`、
        #         `first_row_y=None` 照旧如实返回，由调用方决定要不要点第一行。
        try:
            from agent import row_geometry as _rg2
            row_h_phys = int(_rg2.cached_row_h() or 0)
        except Exception:
            row_h_phys = 0
        if row_h_phys > 0:
            out.update(ok=True, how="cached_row_h",
                       row_pitch=row_h_phys / scale,
                       green_y=None,
                       note=("本帧无绿带 ⇒ 行高用**记住的值**（首次打开时实测落盘；"
                             "用户改字号才会变）。绿带行身份仍未知。"))
            return out

        # ③ 缓存也没有 ⇒ **如实返回量不到**，不猜。
        #    由调用方决定：要么"点第一行造出高亮再量"，要么走 allow_ocr_fallback 降级。
        out["how"] = "no_green"
        out["note"] = ("本帧没有绿带（当前会话高亮不在可见区/未选中）"
                       "且**没有记住的行高**"
                       "⇒ 行高无法现量。调用方可先点第一行造高亮（量到后会记住）。")
        return out
    except Exception as e:  # noqa: BLE001
        out["err"] = str(e)[:80]
        return out


def ensure_highlight(*, main_hwnd: int = 0, dry_run: bool = False) -> dict:
    """★ **保证"会话列表里有一行是高亮的"** ⇒ `{ok, how, click_pt, reason}`。

    场景：列表里**没有任何一行被选中**时，`row_geometry.green_band()` 量不到绿带
      ⇒ 行高没了、当前会话身份也没了 ⇒ `ensure_popup` 会退到兜底常量（危险）。
    做法：**点一下列表"第一行"的名字区**，微信就会把它选中（变绿带）
      ⇒ 之后 `measure_row_geometry()` / `find_current_session_order()` 全部可用。

    ⚠️ 为什么点第一行是安全的：
      · 单击会话行 = 切换会话（打开它的聊天），**不会弹独立窗**（双击才会）⇒ 无副作用；
      · 且**不动光标**（用 `input_backend` 的后台投递，`touches_cursor=False`）。

    ⚠️ 第一行 y 从哪来（**不写死**）：
      · `chat_header.pane_left_for()` 给列表右沿；
      · 第一行中心 y 用 `ROW0_Y_FALLBACK`（逻辑）当**起点试探**——这是**唯一一处**允许用兜底常量的场景
        （因为此刻恰恰量不到任何图内尺子，只能先试；试完立刻用真实绿带重新校准）。
    """
    out = {"ok": False, "how": "", "click_pt": None, "reason": ""}
    try:
        from agent import input_backend as _ib
        m = int(main_hwnd or _ib.find_main_window() or 0)
        if not m:
            out["reason"] = "找不到主窗"
            return out
        im, meta = grab_window(m, physical=True)
        if im is None:
            out["reason"] = "抓图失败"
            return out
        scale = float(meta.get("scale") or 1.5)
        pane = 0
        try:
            from agent import chat_header as _ch
            pane = int(_ch.pane_left_for(im) or 0)
        except Exception:
            pane = 0
        # 已经高亮 ⇒ 什么都不用做
        try:
            from agent import row_geometry as _rg
            if _rg.green_band(im):
                out.update(ok=True, how="already", reason="列表已有高亮行")
                return out
        except Exception:
            pass
        r = window_rect_physical(m)          # ★★ **必须是物理**（见 window_rect_physical 的说明）
        # ★★ 口径统一（修）：**全流程一律物理像素**
        #   · `pane_left_for(im)` 是在**物理图**上算的 ⇒ `pane` 已是物理
        #   · `window_rect_physical` 返回的也是物理
        #   ⇒ ⛔ 一律**不再除 scale**（旧代码 `pane/scale`、`first_pt_phys/scale`
        #      是双重缩放 ⇒ x 从 186 跳到 55 就是这么来的）
        x = int(r[0] + pane * 0.55) if pane else int(r[0] + im.size[0] * 0.30)
        # ★ 第一行 y = **实测的列表顶** + 半个兜底行高（点"行中心"，不点行上边界）
        lt = measure_list_top(m, pane=pane, scale=scale)
        if lt.get("ok") and lt.get("list_top_y"):
            list_top_phys = int(lt["list_top_y"])          # 物理
        else:
            list_top_phys = int(ROW0_Y_FALLBACK)           # 兜底（已是物理）
            out["list_top_fallback"] = True
        # ⚠️ 此刻**必然量不到行高**（正因为没高亮才进来）⇒ 用兜底行高算"第一行中心"。
        #    物理兜底行高 = ROW_PITCH_FALLBACK × 1.5（约 97 物理，实测默认字号）。
        row_pitch_fallback_phys = float(ROW_PITCH_DIP) * win_scale(main)
        first_center_phys = list_top_phys + row_pitch_fallback_phys / 2.0
        y = int(r[1] + first_center_phys)
        out["click_pt"] = (x, y)
        out["click_pt_rel_phys"] = (int(x - r[0]), int(first_center_phys))
        out["list_top"] = lt
        # ⛔ **安全阀**：造高亮的落点也**不许落在列表顶之上**（顶栏/搜索框）。
        #   单击顶栏虽不会全屏，但会点开搜索/工具条，同样是不该有的副作用。
        if first_center_phys < float(list_top_phys):
            out["reason"] = ("落点 y=%d(物理) 在列表顶 %d 之上 ⇒ 拒绝点击（会点到顶栏/搜索框）"
                             % (int(first_center_phys), int(list_top_phys)))
            out["blocked"] = True
            return out
        if dry_run:
            out.update(ok=True, how="dry_run", reason="将单击第一行 (%d,%d) 造高亮" % (x, y))
            return out
        # ★ 造高亮 = **只允许单击一次**
        #   ⇒ 两次同点会被微信判成"双击"（→ 弹独立窗 / 高亮反而消失），必须单枪。
        #   ⛔ 不切前台（投递不需要前台）。
        import time as _t
        be = _ib.select_backend()
        ok_click = False
        for tgt in _ib.row_click_targets(m):
            kind = _ib.win_kind(m, tgt)
            be.click(tgt, (x, y), press_ms=150)      # ⚠️ 慢节奏（hover300+press150）微信才认
            _t.sleep(0.6)
            im2, _m2 = grab_window(m, physical=True)
            try:
                from agent import row_geometry as _rg2
                band = _rg2.green_band(im2) if im2 is not None else None
            except Exception:
                band = None
            if band:
                out.update(ok=True, how="clicked", via=kind,
                           reason="已单击第一行并形成高亮（经 %s）" % kind,
                           green_y=(int(band["y0"]), int(band["y1"])))
                ok_click = True
                break
        if not ok_click:
            out["reason"] = "单击第一行后仍未见高亮（已试 %s）" % [
                _ib.win_kind(m, t) for t in _ib.row_click_targets(m)]
        return out
    except Exception as e:  # noqa: BLE001
        out["reason"] = "异常：%s" % str(e)[:80]
        return out


def _borrow_foreground(hwnd: int) -> dict:
    """⛔⛔ **已废弃：禁止切前台**。

    红线：**任何"把微信顶到前台"的动作都不许做**。
      · 用户会**有意**把微信压在后台（同时在别的窗口里干活），
        把微信顶到前台 = 打断他正在做的事。
      · 投递点击靠 `PostMessage`，**本来就不需要前台**
        （`MessageBackend.touches_cursor=False`）。
    ⇒ 本函数**保留为占位（永远返回 not-ok）**，只做记录，**不做任何窗口置前动作**。
       旧实现（`AttachThreadInput` + `SetForegroundWindow`）已删除，不要再捡回来。
    """
    return {"ok": False, "prev": 0, "hwnd": int(hwnd or 0),
            "disabled": True,
            "reason": "禁止切前台（投递不需要前台；不许打扰当前前台窗口）"}


def _give_back_foreground(prev: int) -> None:
    """⛔ **已废弃**：配合 `_borrow_foreground` 的"还前台"。

    既然**从不切前台**，就没有"还"这回事。保留为空函数只是防止旧调用点报错。
    """
    return


def _screen_img_diff(a, b) -> float:
    """两帧的**像素变化率**（0~1）。返回 -1 表示算不了。"""
    try:
        import numpy as _np
        x = _np.asarray(a.convert("L")).astype(_np.int16)
        y = _np.asarray(b.convert("L")).astype(_np.int16)
        if x.shape != y.shape:
            return -1.0
        return float((_np.abs(x - y) > 8).mean())
    except Exception:
        return -1.0


def measure_list_top(main_hwnd: int, *, pane: int = 0, scale: float = 0.0) -> dict:
    """★ **实测"会话列表从哪一行开始"**（= 第一行的 y0，**物理像素**，相对主窗顶部）。

    必须把「顶栏」、「搜索框」、「会话列表」三者的分界量出来，才能算落点。

    结构（本机实测，物理 y，主窗 987×1016）：
      ```
      y   0~ 45  219.8  顶栏（工具条：头像/搜索图标那一排）
      y  48~ 62  239.9  ← 搜索框上沿起（整幅最亮的白）
      y  64~103  234~250  搜索框内部 + 下沿留白
      y 120~...         ← **★ 列表第一行从这儿开始**（实测 120）
      ```
    ⇒ 判据：找**搜索框那段亮段（≥238）的下沿**，再 +16 物理留白 = 列表顶。

    ⛔⛔ **修：不再"只看一列"**（原来 `x = pane*0.45`，实测 `pane=256` 时
      `x=115` 落在**左侧头像/图标栏**（深色区），`runs=[]` ⇒ 恒返回 `ok=False`）。
      实测各列可用性：`x=40..115` 全空；`x=130..300` 都有干净的 `(64,103)` 亮段。
      ⇒ 改为**扫多列（分布在头像栏右侧到 pane 之间），取"出现次数最多的一致亮段"**。
      这样即使 pane 量小/量大，只要有**任意一列**看得到搜索框就能量出来。

    ⚠️ **不写死**：搜索框高度随字号/窗口变，所以每次现量。
    """
    out = {"ok": False, "list_top_y": None, "search_bottom_y": None}
    try:
        import numpy as _np
        im, meta = grab_window(int(main_hwnd), physical=True)
        if im is None:
            return out
        sc = float(scale or 1.0)
        if not pane:
            try:
                from agent import chat_header as _ch
                pane = int(_ch.pane_left_for(im) or 0)
            except Exception:
                pane = 0
        a = _np.asarray(im.convert("L")).astype(int)
        H, Wd = a.shape

        # ── 候选取样列：从"头像栏右侧"到"pane 之间"均匀布点 ──────────────────
        # 头像/图标栏宽约 120 物理（实测 x=40..115 全空），所以起点放 130。
        pane_eff = int(pane or 0)
        if pane_eff <= 130 or pane_eff > Wd:
            pane_eff = int(Wd * 0.55)           # pane 不可信 ⇒ 用整幅 55% 当右界
        xs = []
        lo, hi = 130, max(140, min(pane_eff, Wd - 2))
        step = max(1, (hi - lo) // 8)
        for x in range(lo, hi, step):
            xs.append(int(x))
        if not xs:
            xs = [max(1, int(Wd * 0.2))]

        def _runs(col, y0, y1, thr, minlen):
            rs, res = None, []
            for y in range(y0, min(y1, len(col))):
                b = col[y] >= thr
                if b and rs is None:
                    rs = y
                if (not b) and rs is not None:
                    if y - rs >= minlen:
                        res.append((rs, y - 1))
                    rs = None
            if rs is not None and min(y1, len(col)) - rs >= minlen:
                res.append((rs, min(y1, len(col)) - 1))
            return res

        # ── 路 A：逐列找"搜索框亮段"（每列第一段 ≥238、长 20~80）──────────────
        votes = {}
        for x in xs:
            rr = _runs(a[:, x], 10, 400, 238, 20)
            for (r0, r1) in rr:
                ln = r1 - r0 + 1
                if 20 <= ln <= 80:              # 搜索框高度量级（实测 40 物理）
                    votes.setdefault(r1, []).append(x)
                    break                        # 每列只投第一段
        best_bottom, best_xs = None, []
        for b in sorted(votes):
            if best_bottom is None or len(votes[b]) > len(best_xs):
                best_bottom, best_xs = b, votes[b]
        out["votes"] = {int(k): len(v) for k, v in sorted(votes.items())}

        # ⛔ **票数门槛**：路 A 靠"≥238 的亮段"，可这个判定**依赖主题/字号** ——
        #   实测（whale 主题、窗口 1201×971）：搜索框整片只有 **224~230**（够不到 238），
        #   于是路 A 找不到搜索框，**反而把会话卡片之间的亮缝（238+）当成了它**
        #   ⇒ 列表顶被量成 254，而**真值约 118** ⇒ 下游"落点必须 ≥ 列表顶"那道安全阀
        #   把**好行全拒了**（保守失败：不会乱点，但功能整个不可用）。
        #   ⇒ 票数太少（凑不够 1/3 列）就**不采信路 A**，改走路 B。
        _min_votes = max(2, len(xs) // 3)
        _a_ok = bool(best_bottom is not None and len(best_xs) >= _min_votes)

        # ── 路 B（稳健）：找"顶部亮区 → 列表暗区"的**亮度台阶** ──────────────
        #   本窗口结构（实测）：y 0~115 是顶栏+搜索框（亮，中位 224~230），
        #   y≈118 起会话列表（暗，中位 ~120）。台阶比"绝对亮度"稳得多：
        #   它不依赖主题把搜索框画多白，只看"哪儿开始明显变暗"。
        _step_votes = {}
        for x in xs:
            col = a[:, x]
            for y in range(20, min(400, len(col) - 4)):
                _hi = int(_np.median(col[max(0, y - 24):y]))         # 台阶上方 24px 的中位
                _lo = int(_np.median(col[y + 4:y + 28]))             # 台阶下方 24px 的中位
                if _hi - _lo >= 60 and _lo < 190:                    # 明显跌暗、且下方确实是暗底
                    _step_votes.setdefault(y, []).append(x)
                    break
        _step_y, _step_xs = None, []
        for y in sorted(_step_votes):
            if _step_y is None or len(_step_votes[y]) > len(_step_xs):
                _step_y, _step_xs = y, _step_votes[y]
        out["step_y"] = int(_step_y) if _step_y is not None else None
        out["step_votes_n"] = len(_step_xs)

        if _a_ok:
            out["search_bottom_y"] = int(best_bottom)
            out["vote_cols"] = list(best_xs)[:8]
            out["how"] = "searchbar_bright"
            # +16 物理留白（实测 104→120）
            out["list_top_y"] = int(round(best_bottom + 16))
            # ★ 与路 B 交叉核对（**只记不改**）：两路差得远 ⇒ 如实标注，供诊断
            if _step_y is not None:
                out["step_agrees"] = abs(int(round(best_bottom + 16)) - int(_step_y)) <= 40
            out["ok"] = True
            return out

        if _step_y is not None:
            out["search_bottom_y"] = None
            out["how"] = "brightness_step"
            out["list_top_y"] = int(_step_y)
            out["ok"] = True
            return out

        out["note"] = ("两路都量不到列表顶（路 A 亮段票数 %s < 门槛 %s；路 B 无亮度台阶）"
                       "⇒ 候选列 %s" % (len(best_xs), _min_votes, xs[:4]))
        out["cols"] = xs
        return out
        out["list_top_logical"] = round(out["list_top_y"] / sc, 2)
        out["pane"] = int(pane or 0)
        return out
    except Exception as e:  # noqa: BLE001
        out["err"] = str(e)[:80]
        return out


def measure_name_left(main_hwnd: int, *, list_top: int = 0, row_pitch: float = 0.0) -> int:
    """★ **实测"会话名字列的左沿 x"**（物理像素，主窗内）。

    ⛔⛔ **为什么需要它**：旧落点算法 `x = pane * 0.60`。本机 `pane=256` ⇒ `x=153`，
      而**头像方块占 x≈120~177** ⇒ **红圈正好压在会话头像上**（叠加图里可见）。
      ⇒ 必须知道"头像右沿在哪"，才能把 x 放到**真正的名字文字区**。

    判据（实测横向灰度剖面，`某群` 行）：
      ```
      x   0~110   220 左侧图标栏（含图标暗点）
      x 120~177  154~188  ← **头像方块**（深色，均值明显低）
      x 180~425  237      ← **★ 名字/预览文字区**（亮底 + 文字暗点）
      x 430+     250     右侧聊天区
      ```
    ⇒ **名字区左沿 = 头像方块右沿之后，灰度回到"亮底(≥232)"的那一列**。

    做法：在**若于行**（list_top 之下的若干行）横向扫，找"从暗块转回亮底"的第一个稳定位置。
    量不到就返回 0（调用方退 `ROW_NAME_LEFT_FALLBACK`）。
    """
    try:
        import numpy as _np
        im, _meta = grab_window(int(main_hwnd), physical=True)
        if im is None:
            return 0
        a = _np.asarray(im.convert("L")).astype(int)
        H, Wd = a.shape
        y0 = int(list_top or 0) or 119
        # 取 3 个行带（第 1/2/3 行）的横向剖面，投票
        pitch = float(row_pitch or 0) or 97.0
        cands = []
        for i in range(3):
            yc = int(y0 + pitch * (i + 0.6))
            if yc + 12 >= H:
                break
            band = a[max(0, yc - 12):yc + 12, :]
            cm = band.mean(axis=0)
            # 从 x=100 往右：跳过图标栏/头像（暗），找"灰度≥232 且连续 20 列都亮"的第一处
            run = 0
            found = 0
            for x in range(100, min(Wd, 420)):
                if cm[x] >= 232:
                    run += 1
                    if run >= 20:
                        found = x - run + 1
                        break
                else:
                    run = 0
            if found:
                cands.append(found)
        if not cands:
            return 0
        cands.sort()
        return int(cands[len(cands) // 2])       # 取中位数（抗单帧噪声）
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return 0


def find_current_session_order(adapter, *, main_hwnd: int = 0, geo: dict | None = None) -> dict:
    """★ **读"绿带那一行是谁" ⇒ 反查它在 DB 里的序号** ⇒ `{ok, order, name, raw, reason}`。

    这是**消掉"滚动偏移"的关键**：绿带行在屏幕上的位置取决于列表滚到哪儿，
      不知道滚了多少，就无法把"会话名"映射回"DB 序号"。

    原理：**绿带高亮行 = 主窗当前打开的会话**（微信的行为，唯一一行）。
      ① 裁出绿带那一行（用 `row_geometry.green_band` 给的 y 范围）；
      ② 只裁**名字文字区**（右侧留出 pane，左侧跳过头像），做 OCR；
      ③ 读出名字 ⇒ `session_order.find_session` 反查 ⇒ 拿到 **DB 序号**；
      ④ 于是 `首行 y = 绿带 y0 − 序号 × 行高`，**不假设列表滚到顶**。

    ⚠️ 实测：
      · 绿带行 OCR **能读出名字**（「某群」四种预处理全读对，含原图直读）
      · **不要**指望主窗标题：实测主窗标题恒为 **"微信"**（不是会话名）⇒ 那条路是断的
    """
    out = {"ok": False, "order": None, "name": "", "raw": "", "reason": ""}
    try:
        from agent import input_backend as _ib
        m = int(main_hwnd or _ib.find_main_window() or 0)
        if not m:
            out["reason"] = "找不到主窗"
            return out
        im, _meta = grab_window(m, physical=True)
        if im is None:
            out["reason"] = "抓图失败"
            return out
        geo = geo or measure_row_geometry(m)
        # ⛔⛔ **绿带范围必须在本帧现算**，不能信传入的 `geo["green_y"]`：
        #   实测（本机）传入 geo 给的是 `413..509`，而本帧真实绿带在 `120..217`
        #   ⇒ 裁到空白 ⇒ OCR 读不出 ⇒ 整条"绿带锚定"失效。
        #   （裁对范围后，同一张图直接读出会话名。）
        gy = None
        try:
            from agent import row_geometry as _rg0
            _band = _rg0.green_band(im)
            if _band:
                gy = (int(_band["y0"]), int(_band["y1"]))
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            gy = None
        if not gy:
            gy = geo.get("green_y")
        if not gy:
            out["reason"] = "本帧没有绿带 ⇒ 读不到当前会话"
            return out
        y0, y1 = int(gy[0]), int(gy[1])
        # ⛔⛔ **绿带 y 与 pane 都取自当前这张图** ⇒ 裁名字区也必须用**同一张图**。
        #   本函数的 `im` 来自 `grab_window`（整窗物理图），而渲染区口径的
        #   `pane_left_for(im)` 期望的是**渲染区图** ⇒ 两者混用会裁到错位、OCR 只吐 `0`。
        #   ⇒ 这里**另抓一张渲染区图**专供 OCR（落点计算仍用整窗图，互不影响）。
        _im_ocr = im
        try:
            from agent import chat_header as _ch_ocr
            _gui_o = _ch_ocr._gui()
            _tmp = _ch_ocr.capture_image(gui=_gui_o)
            if _tmp is not None and _tmp.size[0] > 0:
                _im_ocr = _tmp
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            _im_ocr = im
        _h_ocr = int(_im_ocr.size[1])
        # 若两张图高度差得离谱 ⇒ 说明口径不同，按比例把绿带 y 换算过去
        if _h_ocr != int(im.size[1]) and int(im.size[1]) > 0:
            _k = float(_h_ocr) / float(im.size[1])
            _y0o, _y1o = int(round(y0 * _k)), int(round(y1 * _k))
        else:
            _y0o, _y1o = y0, y1
        _y0o = max(0, min(_y0o, _h_ocr - 1))
        _y1o = max(_y0o + 1, min(_y1o, _h_ocr))
        pane = 0
        try:
            from agent import chat_header as _ch0
            pane = int(_ch0.pane_left_for(_im_ocr) or 0)
        except Exception:
            pane = 0
        if pane <= 0:
            pane = int(geo.get("pane") or 0)
        if pane <= 0:
            pane = int(_im_ocr.size[0] * 0.42)
        # 名字区：跳过左侧头像/图标栏（≈pane 的左 30%），**右沿收紧**——
        # ⛔ 实测：取到 pane*0.98 会把右侧**时间戳**（如 `14:24`）一起裁进来，
        #   OCR 输出 `文亻牛传输助手14：24`（名字与时间连成一串）⇒ DB 匹配不上 ⇒ 反查失败。
        #   ⇒ 名字只占行的左侧一段，右沿收到 pane*0.72（时间戳在最右）。
        lx = int(pane * 0.22)
        rx = int(pane * 0.72)
        row = _im_ocr.crop((lx, _y0o, rx, _y1o))
        # 放大 3x 提升小字识别率（实测原图也能读，放大更稳）
        row3 = row.resize((row.size[0] * 3, row.size[1] * 3))
        try:
            from agent import chat_ocr as _co
            items = _co.recognize(row3, timeout=40) or []
        except Exception:
            items = []
        # 取最左的那个框当名字（时间戳/未读数都在右侧）；并**剥掉时间戳样式后缀**
        cand = []
        for (t, x, y, w, h) in items:
            t = str(t).strip()
            if not t:
                continue
            cand.append((x, -h, t))
        cand.sort()
        raw = ""
        for _x, _nh, t in cand:
            t2 = _strip_time_like(t)          # 去掉 `14：24` / `昨天 20:46` 之类
            if t2:
                raw = t2
                break
        out["raw"] = raw
        if not raw:
            out["reason"] = "绿带行没 OCR 出可用文字（名字区裁 %s）" % ((lx, y0, rx, y1),)
            return out
        if adapter is None:
            out.update(ok=True, order=None, name=raw,
                       reason="读到了名字但没给 adapter，无法反查序号")
            return out
        from agent import session_order as _so
        s = _so.find_session(adapter, raw)
        if s is None:
            # 兜底：OCR 小字易错字（实测 `文亻牛传输助手` 是 `文件传输助手` 的误识）
            #   ⇒ 用"去空白 + 后缀包含"再试一轮（只在唯一命中时才接受，避免误中）
            s = _fuzzy_session(adapter, raw)
        if s is None:
            out["name"] = raw
            out["reason"] = "绿带行名字 %r 在 DB 里匹配不到会话" % raw
            return out
        out.update(ok=True, order=int(s["order"]), name=s.get("name") or raw)
        return out
    except Exception as e:  # noqa: BLE001
        out["reason"] = "异常：%s" % str(e)[:80]
        return out


def _strip_time_like(t: str) -> str:
    """去掉 OCR 串里混进来的**时间戳/计数**尾部（实证：`文亻牛传输助手14：24`）。

    规则：截断在第一个"时间样式"起点（`\\d{1,2}[:：]\\d{2}` / `昨天` / `星期` / `\\d+月\\d+日`）。
    """
    import re
    s = str(t or "").strip()
    for pat in (r"\d{1,2}[:：]\d{2}", r"昨天", r"星期[一二三四五六日天]",
                r"\d{1,2}月\d{1,2}日", r"\d{4}[/-]\d{1,2}[/-]\d{1,2}"):
        m = re.search(pat, s)
        if m and m.start() > 0:
            s = s[:m.start()]
    return s.strip()


def _fuzzy_session(adapter, raw: str):
    """OCR 名字的**模糊兜底**：只在"唯一命中"时返回，否则 `None`（绝不猜）。

    实测误识样本：`文亻牛传输助手` ← `文件传输助手`（`件`被拆成`亻牛`）。
    做法：把名字与库里每个 `name` 都去掉空白/标点后比，
      ① 一方包含另一方（长度 ≥3 才允许，防"E"这种单字乱中）；或
      ② 公共子序列占比 ≥0.75。
      命中数必须**恰为 1**，否则视为不确定 ⇒ None。
    """
    import re
    def _norm(x):
        return re.sub(r"[\s\W_]+", "", str(x or ""))
    q = _norm(raw)
    if len(q) < 2:
        return None
    try:
        from agent import session_order as _so
        sessions = _so.list_sessions(adapter)
    except Exception:
        return None
    hits = []
    for s in sessions:
        nm = _norm(s.get("name"))
        if not nm:
            continue
        if len(nm) >= 3 and (nm in q or q in nm):
            hits.append(s)
            continue
        # 公共子序列占比
        import difflib
        ratio = difflib.SequenceMatcher(None, q, nm).ratio()
        if ratio >= 0.75:
            hits.append(s)
    uniq = []
    for s in hits:
        if s["username"] not in [u["username"] for u in uniq]:
            uniq.append(s)
    return uniq[0] if len(uniq) == 1 else None


# ═══════════════════════════════════════════════════════════════════════════════
# ★★ 「按名字相似度找行」—— 不猜行号、不依赖行序一致
# ═══════════════════════════════════════════════════════════════════════════════
#
# 思路：DB 里有**每个会话的正确名称** ⇒ 把"屏幕上哪一行"的问题，转成
#      "这一行 OCR 出来的字，与 DB 里哪个名字最像"。
#   · 逐行 OCR 按**固定行高**切（不靠聚类 ⇒ 不会漏行）；
#   · 每行与 DB 全部名字算相似度，取最高者作为该行身份；
#   · 目标所在的那一行 ⇒ 就是要点的那一行。
# ⇒ 不依赖"DB 序号 == 屏幕行号"这个假设（实测不成立）。

def _ocr_row_name(im, y_center_phys: int, row_pitch_phys: float,
                  pane_phys: int) -> str:
    """OCR 读**某一行**的名字（裁名字区 → 放大 3x → 剥时间戳）。读不出给空串。

    裁剪口径：左跳过头像、右避开时间戳（与 `find_current_session_order` 一致）。
    """
    try:
        from agent import chat_ocr as _co
        half = max(2, int(round(float(row_pitch_phys) * 0.42)))
        y0 = max(0, int(y_center_phys) - half)
        y1 = min(int(im.size[1]), int(y_center_phys) + half)
        lx = max(0, int(pane_phys * 0.22))
        rx = max(lx + 8, int(pane_phys * 0.72))
        if y1 <= y0 or rx <= lx:
            return ""
        row = im.crop((lx, y0, rx, y1))
        if row.size[0] <= 0 or row.size[1] <= 0:
            return ""
        row3 = row.resize((row.size[0] * 3, row.size[1] * 3))
        items = _co.recognize(row3, timeout=40) or []
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return ""
    cand = []
    for it in items:
        try:
            t = str(it[0]).strip()
            x, h = it[1], it[3]
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            continue
        if t:
            cand.append((x, -h, t))
    cand.sort()
    for _x, _nh, t in cand:
        t2 = _strip_time_like(t)
        if t2:
            return t2
    return ""


def _name_sim(a: str, b: str) -> float:
    """两个名字的相似度（去空白标点后比；一方包含另一方时给高下限）。"""
    import difflib
    import re

    def _n(x):
        return re.sub(r"[\s\W_]+", "", str(x or ""))

    x, y = _n(a), _n(b)
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    r = difflib.SequenceMatcher(None, x, y).ratio()
    if len(x) >= 2 and len(y) >= 2 and (x in y or y in x):
        r = max(r, 0.85)
    return float(r)


def find_row_by_name(adapter, target_name: str, *, main_hwnd: int = 0,
                     min_sim: float = 0.5, max_rows: int = 40) -> dict:
    """在屏幕上按**名字相似度**找目标会话所在行 ⇒ `{ok, row, y, ocr, sim, table, reason}`。

    `target_name` ＝ 目标的**显示名**（DB 里那一列）。
    `min_sim`     ＝ 认定为"就是它"的最低相似度（默认 0.5）。
    """
    out = {"ok": False, "row": -1, "y": 0, "ocr": "", "sim": 0.0,
           "table": [], "reason": ""}
    try:
        from agent import chat_header as _ch
        from agent import input_backend as _ib
        m = int(main_hwnd or _ib.find_main_window() or 0)
        if not m:
            out["reason"] = "找不到主窗"
            return out
        geo = measure_row_geometry(m)
        pitch = float(geo.get("row_pitch") or 0)
        lt = measure_list_top(m)
        list_top = int(lt.get("list_top_y") or ROW0_Y_FALLBACK)
        im, _meta = grab_window(m, physical=True)
        if im is None:
            out["reason"] = "抓图失败"
            return out
        # ⛔ 行高**不能只靠绿带**：无高亮时 `measure_row_geometry` 量不到。
        #   ⇒ 兜底顺序：① session_rows 相邻行的间距中位数（实测稳）；
        #                ② `ROW_PITCH_FALLBACK`（本机按 150% DPI 的物理值）。
        if pitch <= 1:
            try:
                from agent import chat_ocr as _co2
                _sr = _co2.session_rows(im)
                _ys = [int(r.get("y_abs") or 0) for r in _sr]
                _ds = [b - a for a, b in zip(_ys, _ys[1:]) if 40 <= (b - a) <= 400]
                if _ds:
                    _ds.sort()
                    pitch = float(_ds[len(_ds) // 2])
                    out["pitch_src"] = "session_rows 间距中位数"
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                pass
        if pitch <= 1:
            # ⛔ 兜底也**不许写死缩放**（旧写法 `* 1.5` 只在 150% 的机器上对）
            #   ⇒ 逻辑行高 × 这台机器窗口的真实缩放。
            pitch = float(ROW_PITCH_DIP) * win_scale(main_hwnd or 0)
            out["pitch_src"] = "ROW_PITCH_DIP×win_scale"
        pane = int(lt.get("pane") or geo.get("pane") or 0)
        out["pitch"] = round(pitch, 1)
        out["list_top"] = list_top
        if pane <= 0:
            pane = int(im.size[0] * 0.42)

        # 逐行（固定行高）OCR，**不依赖聚类 ⇒ 不漏行**
        i, y, h = 0, float(list_top) + pitch / 2.0, int(im.size[1])
        while y + pitch / 2.0 <= h and i < max_rows:
            yc = int(round(y))
            nm = _ocr_row_name(im, yc, pitch, pane)
            out["table"].append({"row": i, "y": yc, "ocr": nm,
                                 "sim": round(_name_sim(nm, target_name), 3)})
            if nm and not out["ok"] and _name_sim(nm, target_name) >= min_sim:
                out.update(ok=True, row=i, y=yc, ocr=nm,
                           sim=round(_name_sim(nm, target_name), 3),
                           reason="第 %d 行 OCR=%r 与目标名相似度 %.3f" % (i, nm,
                                                                        _name_sim(nm, target_name)))
            i += 1
            y += pitch
        if not out["ok"]:
            best = max(out["table"], key=lambda r: r["sim"]) if out["table"] else None
            out["reason"] = ("逐行 OCR（%d 行）里没有相似度 ≥ %.2f 的行；最像的是 %s"
                             % (len(out["table"]), min_sim, best))
        return out
    except Exception as e:  # noqa: BLE001
        out["reason"] = "按名字找行异常：%s: %s" % (type(e).__name__, str(e)[:80])
        return out


def _is_main_window(hwnd: int) -> bool:
    """这个窗是不是微信**主窗**（用 input_backend 的判据，拿不到就保守判 False）。"""
    try:
        from agent import input_backend as IB
        return int(hwnd) == int(IB.find_main_window() or 0)
    except Exception:
        return False


def _current_chat_name() -> str:
    """主窗标题的原始读法（已实测：**恒为"微信"，不是会话名**）。

    ⛔⛔ **这条路是断的（实测钉死）**：
      · `GetWindowTextW(主窗)` 返回的是 **"微信"**，不是当前会话名；
      · 枚举主窗全部子控件 → 只有 `Weixin` / `MMUIRenderSubWindowHW` 两个类，
        **没有任何控件承载"当前会话名"**（微信是自绘，标题不落到 Win32 控件上）。
      ⇒ 所以"靠主窗标题拿当前会话名"**永远拿不到**，本函数**仅供诊断/日志**使用。
      ⇒ **要拿"当前会话是谁"，请用 `find_current_session_order()`**（读绿带行 OCR）。
    """
    try:
        from agent import input_backend as _ib
        m = int(_ib.find_main_window() or 0)
        if not m:
            return ""
        import ctypes as _c
        buf = _c.create_unicode_buffer(512)
        _c.windll.user32.GetWindowTextW(m, buf, 512)
        return strip_unread_suffix(buf.value or "")
    except Exception:
        return ""


def find_popup(chat_name: str) -> dict:
    """找 `chat_name` 的**独立弹窗**（**排除主窗**）⇒ `{hwnd, title}`；找不到 `hwnd=0`。

    ⚠️ 为什么必须排除主窗：**主窗标题恒为"微信"**（用 `pick_main_window` 的
      标题判据钉死）⇒ 主窗不会与"会话名"撞名。但**旧写法**是靠
      `_is_main_window()` 逐个候选去调 `IB.find_main_window()` 判，
      而 `find_main_window()` **每次都要枚举全部顶层窗 + 检查子窗类名**（`MMUIRenderSubWindowHW`）
      ⇒ **O(n²) 的窗口枚举**，实测 `find_popup` 单次 **0.87s**（是 `ensure_popup` 5s 的主因）。

    ✅ 现在：**只取一次主窗句柄**，循环里比整数（常数级）。
    """
    wins = [w for w in list_wechat_windows() if w["title"].strip()]
    want = (chat_name or "").strip()
    try:
        from agent import input_backend as IB
        _main = int(IB.find_main_window() or 0)
    except Exception:
        _main = 0
    hit = []
    for w in wins:
        if strip_unread_suffix(w["title"]) != want:
            continue
        if _main and int(w["hwnd"]) == _main:
            continue
        hit.append(w)
    if not hit:
        return {"hwnd": 0, "title": "", "candidates": [w["title"] for w in wins]}
    hit.sort(key=lambda w: (w["w"] * w["h"]))
    _hw = int(hit[0]["hwnd"])
    # ⭐⭐ **自愈：独立窗卡在屏外 ⇒ 顺手救回来**。
    #   为什么会卡：最小化档要用"屏外显示"才能投得进鼠标消息，而那一套的收尾若没跑到
    #     （进程被杀 / 异常出口），窗口就停在 `-(宽+200), -(高+200)` 且 `iconic=False`
    #     ⇒ 表现是"窗口不见了，从任务栏点它还往外飞"。
    #   为什么放在这里：`find_popup` 是"每次要用独立窗"的必经点 ⇒ 一次调用就顺手修好，
    #     不必等用户报故障。修法见 `unstick_window_to_screen`（收成最小化 + 把
    #     `rcNormalPosition` 修到屏内）。⛔ 正常窗口不碰（它内部先判"与虚拟屏无交集"）。
    _healed = False
    try:
        if rect_outside_screen(window_rect_any_state(_hw)):
            _ok, _b4, _af = unstick_window_to_screen(_hw)
            _healed = bool(_ok)
    except Exception:  # noqa: BLE001
        pass
    return {"hwnd": _hw, "title": hit[0]["title"], "healed": _healed,
            "candidates": [w["title"] for w in wins]}


def _row_y_by_db(order: int, *, row_pitch: float, first_row_y: float,
                 list_top: int = 0) -> int:
    """由"DB 序号"算"会话行中心 y"（主窗内逻辑像素，相对主窗顶部）。

    ⚠️ `row_pitch` / `first_row_y` **必须由 `measure_row_geometry` 现量供给**
      （实测：同一机器窗口尺寸一变行高就变）——⛔ 不许用写死常量。

    ⛔ **不许拿它当"点会话行"的落点**（本函数保留只作"算几何"用，与 `session_order.row_y` 同一条禁令）。
      会话列表的排序键是 `sort_timestamp` —— **来一条新消息就重排**；
      「读顺序 → 算 y → 投点击」这三步之间必然有窗口期，点中的就是**下一行**（= 发错人，不可逆）。
      ⇒ 要点某一行的唯一安全做法：**当场抓一帧、按名字认出那一行、在同一帧上算 y 再点**
        （`chat_ocr.pick_target_row` / `find_row_info` ⇒ 见 `ensure_popup`，
         它在"名字挑不到"时**拒绝点击**而不是退回序号算出来的落点）。
      本函数目前**没有任何调用方**，请保持这样。
    """
    return int(round(int(list_top) + float(first_row_y) + int(order) * float(row_pitch)))


def window_rect_physical(hwnd: int) -> tuple:
    """窗口矩形 `(l, t, r, b)`，**屏幕物理像素** —— 投递点击用这个。

    ⭐⭐ **本函数就是 `input_backend.window_rect` 的直通（不做任何缩放）**。
      名字保留 `_physical` 是为了标明语义："这是**屏幕物理坐标**，可以直接投递"。

    ⛔⛔ **不要再乘 `window_scale`**（用三重铁证推翻了我自己的错误"修复"）：
      我一度以为 `GetWindowRect` 给的是"逻辑值"，于是乘 1.5 —— **完全错了**。
      铁证（本机 Win11 / 150% / 2560×1600 / 微信主窗）：
        ① **覆盖度**：按 `GetWindowRect` 的 987×1016 抓图，内容占比 **48.29%**（铺满）；
           按 ×1.5 的 1480×1524 抓，反而是 **44.46%** ⇒ 987×1016 **就是真物理**。
        ② **`WindowFromPoint`**：窗口"右下角内 5px"点 `(2482,1311)`
           **命中的就是主窗本身** ⇒ 该点**在窗口内** ⇒ 窗口物理就是 987×1016。
           （若真是 1480×1524，这个点早跑到窗口外了。）
        ③ **屏幕只有 2560×1600**：×1.5 之后右下角会是 `(3990,1819)`，**超出屏幕** ⇒ 荒谬。
      ⇒ 根因：`input_backend` **在 import 时就把 DPI 锁成 PerMonitorV2**（并且检查返回值），
        所以 `GetWindowRect` **本来就返回物理像素**，再乘一次就是**双重放大**
        ⇒ 落点整体放大 1.5 倍、飘到窗口外/顶栏 ⇒ **"点不准" + "双击顶栏变全屏"**。
    """
    from agent import input_backend as _ib
    r = _ib.window_rect(int(hwnd))
    return (int(r[0]), int(r[1]), int(r[2]), int(r[3]))


def _window_is_clickable(hwnd: int) -> bool:
    """窗口是否"能接受屏幕坐标点击"（**不是最小化、不在屏外**）。

    ⛔ 为什么要这个判据（血的教训）：
      微信**最小化时** `window_rect` 返回 `(-32000, -32000, ...)` 这种离屏坐标。
      此时若按屏幕坐标投递点击 ⇒ 落点变成离屏点 ⇒ 打到**任务栏/最小化按钮**
      ⇒ **把用户的微信又摁回最小化**（用户两次抓到我干这事）。

    判据（**两条任一命中即视为不可点**）：
      ① `IsIconic(hwnd)` == 1（已最小化）
      ② 矩形整块落在屏幕外（x0 或 y0 ≤ -10000；Windows 最小化窗口的经典离屏坐标）
    """
    try:
        import ctypes as _ct
        u = _ct.windll.user32
        if not u.IsWindow(int(hwnd)):
            return False
        if u.IsIconic(int(hwnd)):
            return False
        from agent import input_backend as _ib
        r = _ib.window_rect(int(hwnd))
        if int(r[0]) <= -10000 or int(r[1]) <= -10000:
            return False
        if int(r[2]) - int(r[0]) <= 0 or int(r[3]) - int(r[1]) <= 0:
            return False
        return True
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return False


def _open_popup_via_search(chat_name: str, adapter, gui=None) -> tuple:
    """**目标行不在可见区时的兜底**：搜索定位 ⇒ 双击列表里的高亮行 ⇒ 开独立窗。

    为什么不滚列表：滚动会让**用户眼前的列表动**，比开搜索窗更打扰。

    链路（两步都已真机实测）：
      ① **搜索定位**：`adapter.open_chat_by_search` 把**完整会话名**打进搜索框、
         在浮层里按名字匹配到结果行、**单击**它 ⇒ 切到该会话（本机 3/3 成功）；
      ② **双击高亮行**：切过去后该会话就是列表里那条**绿色高亮行** ⇒
         直接读出它的 y（`row_geometry.green_band`）⇒ 两枪双击 ⇒ 开出独立窗（**3/3 成功**）。

    ⛔ **不要用 `open_chat_by_search(double_click_result=True)`**：实测**双击搜索结果行不开窗**
       （只切会话）⇒ 必须"先切过去、再双击列表里的高亮行"。

    ⚠️ 为什么这条路比"算行号"稳：高亮行的 y 是**读出来的**，不做 DB 序号换算 ⇒
      天然不受**置顶会话**与**DB 落库延迟**影响（那两者会破坏"DB 顺序 = 屏幕顺序"这个前提）。

    返回 `(hwnd:int, why:str)`；开不出来时 `hwnd=0` 且 `why` 写清卡在哪一步。
    """
    import time as _t
    if adapter is None:
        return 0, "没有 adapter ⇒ 走不了搜索（会话名翻不出 username）"
    # ── ① 搜索定位（单击结果行 ⇒ 切到目标会话）──────────────────
    try:
        _ok, _why = adapter.open_chat_by_search(chat_name, name=chat_name, gui=gui)
    except Exception as e:  # noqa: BLE001
        return 0, "搜索定位抛异常：%s" % str(e)[:80]
    if not _ok:
        return 0, "搜索定位没成：%s" % str(_why)[:120]

    # ── ② 读高亮行的 y，双击它 ─────────────────────────────────
    try:
        from agent import chat_header as _ch
        from agent import input_backend as _ib
        from agent import row_geometry as _rg
    except Exception as e:  # noqa: BLE001
        return 0, "切过去了，但依赖导入失败：%s" % str(e)[:60]
    if gui is None:
        try:
            gui = adapter._get_gui()
        except Exception:  # noqa: BLE001
            gui = None
    if gui is None:
        return 0, "切过去了，但取不到 GUI ⇒ 读不到高亮行"
    main = int(getattr(gui, "main_hwnd", 0) or 0) or _ib.find_main_window()
    if not main:
        return 0, "切过去了，但找不到主窗"
    img, _meta = grab_window(main, physical=True)
    if img is None:
        return 0, "切过去了，但抓不到主窗画面 ⇒ 读不到高亮行"
    band = _rg.green_band(img)
    if not band:
        return 0, "切过去了，但列表里**没有高亮行** ⇒ 定位不到（不猜，不点）"
    ox, oy = int(getattr(gui, "origin_x", 0)), int(getattr(gui, "origin_y", 0))
    try:
        pane = int(_ch.detect_pane_left(img) or 0)
    except Exception:  # noqa: BLE001
        pane = 0
    x = ox + max(60, int(pane * 0.4))
    y = oy + int((int(band["y0"]) + int(band["y1"])) / 2)
    try:
        be = _ib.select_backend(gui=gui)
    except Exception as e:  # noqa: BLE001
        return 0, "取输入后端失败：%s" % str(e)[:60]
    for _i in range(2):                      # 两枪 ⇒ 双击（间隔取手速真值）
        try:
            adapter._click_posted(be, main, (x, y), "高亮行·第%d枪" % (_i + 1), allow_new=True)
        except Exception as e:  # noqa: BLE001
            return 0, "双击高亮行异常：%s" % str(e)[:60]
        if _i == 0:
            _t.sleep(DOUBLE_GAP_S)

    # ── ③ 等新窗 + 按标题确认 ──────────────────────────────────
    title = resolve_title_name(chat_name, adapter) or chat_name
    t0 = _t.time()
    while _t.time() - t0 < 6.0:
        _t.sleep(0.3)
        h = find_popup(title)
        if h.get("hwnd"):
            return int(h["hwnd"]), ("搜索定位 ⇒ 双击高亮行(第%d行) ⇒ 开出独立窗（标题 %r）"
                                    % (int(band["y0"]), h.get("title")))
    return 0, "双击高亮行后 6s 内没等到 %r 的独立窗（落点 (%d,%d)）" % (title, x, y)


def park_popup(hwnd: int, *, why: str = "") -> dict:
    """把**我们刚弹出来的**独立窗立刻收成最小化 —— 免得它闪到用户眼前。

    ⛔ **只对"我们自己开出来的窗"调用**（`how` ∈ `opened` / `opened_retry` /
      `search_then_highlight`）；`how="exists"`（用户自己开着的那个窗）**绝不动** ——
      动它就是打扰（同"放回窗口"那条纪律的意图）。
    ⚠️ 与"放回窗口一律不用 `ShowWindow(6)`"不冲突：那条管的是**用户的窗口**（收起来还得还、
      用户白看一次闪）；这里收的是**刚被我们弹出来的新窗**，收起来正是为了"别在用户眼前出现"。
      而**最小化档本来就是发消息的正路**（判据以 **DB 回读**为准，不依赖任何画面）。
    ⛔⛔ **常量必须是 `SW_SHOWMINNOACTIVE`(7)，不许用 `SW_MINIMIZE`(6)**：
      `SW_MINIMIZE` 的文档语义是「最小化**并激活 Z 序里的下一个顶层窗**」；
      独立窗是主窗的 **owned window** ⇒ Z 序里紧挨着的下一个就是**微信主窗**
      ⇒ 收一次独立窗就把**用户的主窗顶到前台一次**（他什么都没做，看着像"机器人抢我屏幕"）。
      `SW_SHOWMINNOACTIVE` ＝「显示成最小化，**不激活**」，也不激活别人。
    返回 `{ok, why}`；`ok=False` **不算发送失败**（照走，只记一笔）。
    """
    try:
        import ctypes as _ct
        import time as _t2          # ⚠️ 本模块的 `import time as _t` 是**函数内局部**的（多处各写一行）
                                    #    ⇒ 模块级函数必须自己 import，否则 `NameError`（实测踩过：
                                    #    最小化其实成功了，但返回里报 NameError，看起来像失败）
        from ctypes import wintypes as _wt
        u = _ct.windll.user32
        u.ShowWindow(_wt.HWND(int(hwnd)), 7)   # SW_SHOWMINNOACTIVE（不激活；见 docstring）
        _t2.sleep(0.1)
        ok = bool(u.IsIconic(_wt.HWND(int(hwnd))))
        return {"ok": ok, "why": why or ("已收成最小化" if ok else "调用后仍不是最小化")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, str(e)[:60])}


#: 会话行右键菜单里"把会话开成独立窗口"那一项的文字。
#  为什么按**子串**匹配而不全等：菜单项 OCR 常带多余字，全等会漏判正确项。
#  ⚠️ 换微信版本时用词可能变 ⇒ 留几个候选；**全部匹配不上时不点**（宁可如实失败，也不误点别的项）。
#  ⚠️ 该项是**勾选式**：会话已是"独立窗口显示"时再点会**取消**它 ⇒ 只在双击失败后才走这一级，
#     且以"独立窗口是否真的出现"为最终判据。
POPUP_MENU_LABELS = ("独立窗口显示", "在新窗口打开", "独立窗口", "单独窗口", "在单独窗口中打开")

#: 「置顶该聊天」的**候选菜单词**（用词随微信版本可能变 ⇒ 多候选，全匹配不上就不点）。
#  为什么只给这几个：置顶是**显式命令**，改的是用户自己的会话列表顺序 ⇒
#  ⛔ 宁可如实报"找不到菜单项"，也**绝不**退到"点最上面那一项"（那会点到别的命令）。
PIN_MENU_LABELS = ("置顶该聊天", "置顶聊天", "置顶该会话", "置顶")

#: `send.pin_target_first` 开关的**读取口** —— 就是 `wechat.WeChat._pin_target_first()`
#  （**同一个类**：`adapter` 就是 `WeChat` 实例 ⇒ 直接用它的方法，不另建第二个读取点，
#   否则"同键两处读"会漂）。下面这个薄封装只是为了让调用点读起来短、且**异常一律回 False**。
def pin_target_first_enabled(adapter) -> bool:
    """读 `send.pin_target_first`（**默认 False**）。adapter 为空/无该方法/异常 ⇒ 一律 False。

    ⇒ **唯一的判定实现仍在 `wechat.WeChat._pin_target_first()`**（判据 `pin_target_selftest.py`
      D 段钉的就是"它被真调用"这件事）。
    """
    try:
        _fn = getattr(adapter, "_pin_target_first", None)
        return bool(_fn()) if callable(_fn) else False
    except Exception:  # noqa: BLE001
        return False


def pin_session_via_menu(adapter, chat_name: str, *, row_y_phys: int | None = None) -> tuple:
    """**对目标会话投右键 → 菜单里选「置顶该聊天」** ⇒ `(ok, why)`。

    ⚠️ **本函数只在 `send.pin_target_first` 开着时才会被调用**（调用点自己判）。
    它**会改动用户自己的会话列表顺序**（置顶项跑到最上面）——这就是这个开关**必须默认关**的原因。

    前提（都由调用方保证）：
      · 目标会话行**当前在列表里可见**（`row_y_phys` 给了行中心 y；不给则回退到按名字挑行）；
      · 列表左沿/头像列可用（x 用 `AVATAR_X_DIP × 缩放` 换算，与开窗落点同源 ⇒ 同一套坐标系）。

    ⛔ 三条纪律（与右键开窗那条路同源）：
      ① 菜单项**多候选**匹配，**匹配不到就不点**（绝不退到"点最上面那项"）；
      ② 点完**不假设成功** —— 由调用方**回读 DB**（`pinned_how == "sort_gt_last"`）确认；
         本函数只负责"把那条命令投出去"，**不宣称已生效**；
      ③ 失败**如实返回原因**，不静默。
    """
    if adapter is None:
        return False, "没有 adapter ⇒ 投不了右键"
    # ⛔ **必须走 `input_backend.find_main_window()`，不许写成裸名**（真机事故 P0，见
    #   PLAN 第 270 项 C-2）：本模块**没有模块级的 `find_main_window`**（别处一律
    #   `IB.find_main_window()` / `_ib.find_main_window()`，且 `input_backend` 是在每个函数内部才 import）
    #   ⇒ 写裸名会 `NameError: name 'find_main_window' is not defined`，
    #     从本函数直接抛到 `ensure_popup`，把整条发送链带崩（开关一开必现）。
    try:
        from agent import input_backend as _ib
        _main = int(_ib.find_main_window() or 0)
    except Exception as _eM:  # noqa: BLE001
        return False, "取主窗失败（%s）⇒ 投不了右键" % str(_eM)[:60]
    if not _main:
        return False, "找不到主窗 ⇒ 投不了右键"
    # 行 y：调用方给了就用；没给就现挑（按名字）
    if row_y_phys is None:
        try:
            from agent import chat_ocr as _co
            _im, _ = grab_window(_main, physical=True)
            _pk = _co.pick_target_row(_im, chat_name) if _im is not None else None
            if _pk and _pk.get("ok"):
                row_y_phys = int(_pk.get("y_abs") or 0) or None
        except Exception as _eP:  # noqa: BLE001
            log.debug("置顶路挑行没成：%s", str(_eP)[:80])
    if not row_y_phys:
        return False, "量不到目标行的 y ⇒ 不投右键（不猜）"
    # x：与开窗落点**同一套 DIP 换算**（头像列）⇒ 保证投在会话行内
    try:
        _sc = win_scale(_main)
    except Exception:  # noqa: BLE001
        _sc = 1.0
    _x_phys = int(round(AVATAR_X_DIP * _sc))
    # ⛔ **GUI 入口是 `adapter._get_gui()`，不许写 `getattr(adapter, "gui", None)`**
    #   （真机事故 P0-2，见 PLAN 第 270 项 C-2）：`WeChat` 把 GUI 存在 `self._gui`
    #   （初始化在 `self._gui = None`、赋值在 `WeChatGUI()` 那两处），**没有 `self.gui` 属性**
    #   ⇒ 老写法恒为 None ⇒ 置顶永远"取不到 GUI ⇒ 投不了右键"，一次都投不出去。
    _gui = None
    try:
        _gget = getattr(adapter, "_get_gui", None)
        _gui = _gget() if callable(_gget) else None
    except Exception as _eG:  # noqa: BLE001
        log.info("置顶路取 GUI 异常：%s", str(_eG)[:60])
        _gui = None
    if _gui is None:
        return False, "取不到 GUI ⇒ 投不了右键"
    _tried = []
    for _lb in PIN_MENU_LABELS:
        try:
            _hit = bool(adapter._right_click_menu_posted(
                _gui, _x_phys, int(row_y_phys), _lb, delay=1.0))
        except Exception as _eRC:  # noqa: BLE001
            _hit = False
            log.info("置顶路·右键异常（%s）：%s", _lb, str(_eRC)[:80])
        _tried.append(_lb)
        if _hit:
            return True, "已投右键并选中菜单项「%s」（⛔ 是否真生效要回读 DB 确认）" % _lb
    return False, "右键菜单里找不到置顶项（试过 %s）⇒ 不点" % ("、".join(_tried),)


#: 开窗失败时给前端展示的一条"请人工处理"信息：模块级只存**最近一条**，带 TTL。
#  状态接口每几秒读一次 ⇒ 前端弹一次窗（同一条只弹一次，不反复打扰）；
#  再次失败会**覆盖**它（要展示的是最新那条）。
_HELP_LOCK = _threading.Lock()
_HELP_LAST: dict = {}
#: 求助信息保留多久（秒）：够用户看到就行，别一直挂着
HELP_TTL_S = 900.0


def note_popup_help(chat_name: str, user_action: str, *, detail: str = "") -> dict:
    """记一条"开窗失败、需要人工处理"的信息 ⇒ `{at, chat, title, text, whale}`（给状态接口读）。

    ⛔ 只在失败时记；成功**不清空**（那条求助可能还没被看到，过期自然消失）。
    ⛔ 文案在 `whale_text` 里组装（要跟界面文案风格一致，鲸语模式下也得是鲸语），
      这里只负责填群名 —— 不许在这里再写一份文案。
    """
    import time as _t
    _whale = False
    try:
        from .config import get_config
        _whale = str(((get_config() or {}).get("ui") or {}).get("text_style") or "") == "whale"
    except Exception:  # noqa: BLE001
        _whale = False
    _title, _body = "打不开这个会话的独立窗口", str(user_action or "")
    try:
        from .whale_text import popup_help_text
        _d = popup_help_text(str(chat_name or ""), whale=_whale)
        _title = str(_d.get("title") or _title)
        # 鲸语版自带那句"你动动小手…"；正常档也把 user_action 那句人话接在后面（它写得更具体）
        _body = str(_d.get("body") or _body) if _whale else (str(_d.get("body") or "") + "\n\n" + str(user_action or ""))
    except Exception as _eT:  # noqa: BLE001
        log.debug("取求助文案失败（退回 user_action）：%s", str(_eT)[:60])
    item = {"at": _t.time(), "chat": str(chat_name or ""), "title": _title, "text": _body,
            "whale": bool(_whale), "detail": str(detail or "")[:200]}
    with _HELP_LOCK:
        _HELP_LAST.clear()
        _HELP_LAST.update(item)
    return item


def popup_help_now(max_age_s: float = HELP_TTL_S) -> dict:
    """现在要展示的"求助"⇒ `{}`（没有/过期）或 `{at, chat, text}`。**只读，不改状态**。"""
    import time as _t
    with _HELP_LOCK:
        if not _HELP_LAST:
            return {}
        if (_t.time() - float(_HELP_LAST.get("at") or 0)) > float(max_age_s):
            return {}
        return dict(_HELP_LAST)


def row_click_x(name_left: int, pane: int, win_w: int = 0) -> tuple:
    """按"现场量到的两个读数"算会话行落点 x（物理像素，主窗内）⇒ `(x, how, why)`。

    ⚠️ 这两个读数的**单位不一致**（一个偏逻辑、一个偏物理），换台机器就可能**互相矛盾**。
      旧写法在矛盾时会把 `pane` **撑大**（`name_left + 200`）⇒ x 跑到右侧聊天区
      ⇒ 双击/右键都不会生效。⇒ 本函数在矛盾时**退回保守对**，绝不撑大。
    ⛔ 落点决策**不用本函数的返回值**（见 `ensure_popup`：落点按 DIP 常数算），
      它只作为**交叉校验**（两边差异过大说明口径有问题）。

    规则（按可靠性排序）：
      ① 两个读数**不矛盾**（`name_left + 20 < pane`）⇒ 取名字区中点；
      ② **矛盾**（`name_left >= pane - 20`）⇒ ⛔ **不许撑大 pane**，退回保守对
         `(ROW_X_FALLBACK_LO, ROW_X_FALLBACK_HI)` 的中点 ⇒ `how="conflict_fallback"`；
      ③ 量不到名字左沿 ⇒ 用保守左沿 + 可信右沿；
      ④ 给了窗口宽就再夹一道：`x` 不超过窗口宽的 22%（防"两个数一起量歪"）。
    `why` 会写进调用方的返回体（**可观测**，不是静默）。
    """
    _lo_ok = int(name_left or 0)
    _hi_ok = int(pane or 0)
    if _lo_ok > 0 and _hi_ok > 0 and (_lo_ok + 20) < _hi_ok:
        x, how, why = int((_lo_ok + _hi_ok) / 2.0), "measured", "名字左沿与列表右沿都量到了"
    elif _lo_ok == 0 and _hi_ok > 0 and _hi_ok > ROW_X_FALLBACK_LO + 20:
        x, how, why = int((ROW_X_FALLBACK_LO + _hi_ok) / 2.0), "lo_fallback", "只量到列表右沿"
    elif _hi_ok == 0 and _lo_ok > 0:
        x, how, why = int((_lo_ok + ROW_X_FALLBACK_HI) / 2.0), "hi_fallback", "只量到名字左沿"
    else:
        # ② 矛盾（或两边都没量到）：**两数都不信**，退回保守对 —— 关键是不许把 pane 撑大
        x, how, why = int((ROW_X_FALLBACK_LO + ROW_X_FALLBACK_HI) / 2.0), "conflict_fallback", (
            "两个读数矛盾或都不可信（名字左沿=%d、列表右沿=%d，后者应明显更大）⇒ 退回保守对 %d~%d"
            % (_lo_ok, _hi_ok, ROW_X_FALLBACK_LO, ROW_X_FALLBACK_HI))
    if win_w > 0:
        _cap = int(win_w * 0.22)
        if _cap > 0 and x > _cap:
            x, why = _cap, why + "；再按窗口宽 22%% 夹到 %d" % _cap
    return x, how, why


#: 会话行落点 x 的**保守取值域**（物理像素）：两个来源互相矛盾时退回它就对了。
#  为什么取这两个数：实测头像方块 x≈120~177、名字/预览区 x≈180~425、聊天区 430+
#  （见 `measure_name_left` 的剖面）⇒ 180~350 一定落在名字区里。
ROW_X_FALLBACK_LO = 180
ROW_X_FALLBACK_HI = 350


def win_scale(hwnd: int) -> float:
    """**这个窗口所在显示器的真实缩放**（`GetDpiForWindow / 96`）⇒ 1.0 / 1.25 / 1.5 / 2.0…

    为什么必须现读、**不许写死一个数**：窗口内所有元素都是"逻辑尺寸 × 当前显示器缩放"
      （列表 256 逻辑宽、行高 64 逻辑…）；写死 ⇒ 换机器或改显示设置后全错。
    ⚠️ 别拿 `ui_adapt.detect_scale()` 代替：那个是**进程 DPI 感知下的比值**，与"窗口内容缩放"
      不是一回事（两者可能相差一倍以上）。落点必须用本函数。
    读不到 DPI ⇒ 退回 1.0（按 100% 处理，不猜其他的）。
    """
    try:
        import ctypes as _c
        u = _c.windll.user32
        dpi = int(u.GetDpiForWindow(int(hwnd)) or 0)
        if dpi > 0:
            return max(0.5, min(4.0, dpi / 96.0))
    except Exception as e:  # noqa: BLE001
        log.debug("读窗口 DPI 失败（按 1.0 处理）：%s", str(e)[:60])
    return 1.0


#: 微信**会话列表的逻辑宽度**（DIP；微信自己的固定布局，不随窗口拉伸）
LIST_W_DIP = 256
#: 会话行落点 x 的**逻辑坐标**（DIP）＝会话**头像中心**。
#  为什么取头像而不是"名字区中点"：头像位置**与列表宽度无关**（列表再窄也有头像），
#  而名字区中点依赖"列表宽 256 DIP"这个前提，而列表宽是可变的。
#  ⚠️ 前提：会话列表**可见**；列表被隐藏/收起时没有行可点（那种情况本函数不适用）。
AVATAR_X_DIP = 98
#: 会话行落点 y 的**逻辑坐标**：首行中心 111 DIP（本机 167 物理 ÷ 1.5）+ 行号 × 64 DIP。
ROW0_DIP = 111
ROW_PITCH_DIP = 64


def row_click_pt(win_rect, row_index: int, scale: float) -> tuple:
    """会话行的落点（屏幕物理像素）⇒ `(x, y, why)`。**纯函数，不依赖任何画面测量。**

    式子：`屏幕点位 = 窗口左上角 + 逻辑坐标 × 缩放`。
    逻辑坐标是微信固定布局（头像中心 98 / 首行中心 111 / 行高 64），
    唯一变量是显示器的真实缩放（`win_scale()`）。
    ⛔ 换成"现场量名字左沿与列表右沿再取中点"是同一个量的间接测法，且两个读数单位不一致
      时会互相矛盾（见 `row_click_x`）⇒ 不作为落点来源，只做交叉校验。
    """
    _r = tuple(win_rect or ())
    if len(_r) < 4:
        return 0, 0, "窗口矩形无效"
    _s = float(scale or 1.0)
    x = int(round(int(_r[0]) + AVATAR_X_DIP * _s))
    y = int(round(int(_r[1]) + (ROW0_DIP + int(row_index) * ROW_PITCH_DIP) * _s))
    return x, y, ("窗口原点(%d,%d) + 逻辑(头像x=%d, y=%d+%d×%d)×%.2f"
                  % (int(_r[0]), int(_r[1]), AVATAR_X_DIP, ROW0_DIP, row_index, ROW_PITCH_DIP, _s))


def _drop_click_pt(out: dict) -> dict:
    """把"已经算出来、但被安全阀拒绝"的落点从返回体里去掉。

    为什么必须去掉：调用方常拿 `click_pt` 是否存在来判断"这次能不能点"。拒绝时若还留着它，
    调用方（尤其是一次性脚本）会拿着一个**不该用的坐标**去点 ⇒ 正是这种用法把落点打到了列表以外。
    """
    out.pop("click_pt", None)
    out.pop("click_pt_rel_phys", None)
    return out


def ensure_popup(chat_name: str, *, adapter=None, timeout_s: float = 6.0,
                 row_order: int | None = None, double_click: bool = True,
                 dry_run: bool = False, allow_fallback_click: bool = False) -> dict:
    """★ **保证"`chat_name` 的独立弹窗"存在** ⇒ `{ok, hwnd, how, step, detail}`。

    目标链路：**认会话 → 把它的独立弹窗打出来 → 之后只操作那个独立窗**；
      并且要**自愈**：独立窗被关掉/不在时，能重新识别并把窗口再打出来。

    判据链（**先判"有没有窗"，再谈"怎么开"** —— 顺序不能反）：
      ① **已有**目标独立窗 ⇒ **直接用它**（`how="exists"`）—— 不点任何东西、零风险。
         ⛔ **不要拿"当前是否已在目标会话"当"不用做事"的理由**：
           本函数的目标是"**存在目标独立窗**"，而"已在目标会话"只说明主窗打开着它，
           **独立窗可能仍然没有** —— 两件事（项目架构：发送只走独立窗，不走"当前会话"）。
           ⇒ 判据只用"**有没有那个窗口**"，不用"当前在哪个会话"。
      ② 没有 ⇒ **必须开出来**（列表可见性只决定"**用哪种点法**"）：
         · 列表里**看得见**目标行 ⇒ `agent.session_order` 翻 DB 序号 ⇒ 算那一行 y ⇒ **双击**
           （两枪间隔取手速真值 `DOUBLE_GAP_S`；太近会被判成拖拽、把聊天框关掉）
         · 列表里**看不见**（被滚出屏幕外）⇒ **只报"需要先滚动"算失败**
           （滚动会让用户眼前的列表动，比开搜索窗更打扰）⇒ 应改走第 ④ 级：
           **搜索定位（单击结果行切过去）⇒ 再双击列表里的绿底高亮行**
           ⛔ 不要用 `open_chat_by_search(double_click_result=True)` —— 那条开不出窗（见上）
      ③ **自愈**：②若没等到（窗一闪即没/被叉掉）⇒ **自动重试一轮**（`how="opened_retry"`）
      ③′ ★ **开出来的窗立刻收起**（`park_popup`）：一旦是我们自己弹出来的（`how` ∈ opened*），
         就**马上收成最小化** ⇒ **不让它闪到用户眼前**（而"最小化档"本来就是发消息的正路：
         判据以 **DB 回读**为准，不依赖画面）。⛔ `how="exists"`（用户自己开着的窗）**绝不动**。
      ④ 仍失败 ⇒ `ok=False` ＋ 如实写清卡在哪步（**绝不假装成功**）
         ＋ **给用户一条可照做的引导**（`out["user_action"]`）：请他自己把那一行拖成独立窗 ——
           这一步**用户一定做得到**，而它是整条链里**最难自动化**的一环（详见该字段的注释）。

    参数
      `adapter`      —— `agent.wechat.WeChatAdapter`；给了才能走②的"DB 认序号"。
      `row_order`    —— 已知行号时直接给（跳过 DB 读）。
      `double_click` —— False 时只单击（探测/降级用；双击才是"开独立窗"的正道）。
      `dry_run`      —— 只算"该点哪、窗在不在"，**不真点**。

    ⚠️ **硬边界（实测，别忘）**
      · **独立窗重启后不保留** ⇒ 每次开机/重启后第一次必走②。
      · **hwnd 不可缓存**（窗一关句柄就废）⇒ **每次现查**。
      · 独立窗标题**带未读数**（如 `某会话 (3)`）⇒ 比对一律先 `strip_unread_suffix`。
    """
    out = {"ok": False, "hwnd": 0, "how": "", "step": "", "detail": "",
           "chat": chat_name, "retried": False}

    # ⛔ `chat_name` 可能是 **username**（如 `filehelper`）也可能是**显示名**；
    #   而**窗口标题只有显示名** ⇒ 不解析就永远匹配不上（实测：窗已开出却报 open_failed）。
    title_name = resolve_title_name(chat_name, adapter)
    if title_name != str(chat_name or "").strip():
        out["title_name"] = True

    # ① 已有独立窗？
    hit = find_popup(title_name)
    if hit.get("hwnd"):
        out.update(ok=True, hwnd=int(hit["hwnd"]), how="exists", step="found",
                   detail="已有独立窗（标题 %r）" % hit.get("title"))
        return out

    # ② 找那一行的序号
    order = row_order
    if order is None:
        if adapter is None:
            out.update(step="locate",
                       detail="没有独立窗，且未给 adapter/row_order ⇒ 无法定位会话行")
            return out
        try:
            from agent import session_order as _so
            order = _so.order_of(adapter, chat_name)
            if order is None or int(order) < 0:
                # username 查不到 ⇒ 再试"按显示名/微信号找"（可能就是昵称）
                s = _so.find_session(adapter, chat_name)
                if s is None:
                    out.update(step="locate",
                               detail="DB 里找不到会话 %r（既非 username，也非显示名/微信号）"
                                      % chat_name)
                    return out
                order = int(s["order"])
                out["resolved"] = s.get("name")
        except Exception as e:  # noqa: BLE001
            out.update(step="locate", detail="查会话序号失败：%s" % str(e)[:80])
            return out

    out["row_order"] = int(order)

    # 算落点（主窗内逻辑坐标）
    try:
        from agent import input_backend as IB
        main = int(IB.find_main_window() or 0)
    except Exception as e:  # noqa: BLE001
        out.update(step="main", detail="找不到微信主窗：%s" % str(e)[:80])
        return out
    if not main:
        out.update(step="main", detail="微信主窗未找到（微信没开？）")
        return out

    # ⛔⛔ **根因防护：最小化 / 离屏的窗口，一律不许投递点击**（血的教训）
    #   ⛔ 对**已最小化**的窗口按屏幕坐标投点击是无效的（坐标不在屏幕上）⇒
    #     落点变成 `(-32000, -31951)` 这种离屏坐标 ⇒ 打到任务栏/最小化按钮
    #     ⇒ **把正在使用的微信又摁回最小化**（这样的误击已经发生过）。
    #   判据（两条任一命中即拒绝）：
    #     ① `IsIconic(hwnd)` == 1（最小化）
    #     ② `window_rect` 落在屏幕外（x 或 y ≤ -10000，即 Windows 的离屏最小化坐标）
    if not _window_is_clickable(main):
        out.update(step="not_visible",
                   detail="微信主窗当前不可点击（已最小化或被移到屏外）⇒ "
                          "**拒绝投递点击**（盲投会把窗口状态搞乱）。"
                          "请先把微信正常显示出来。")
        return out

    try:
        r = window_rect_physical(main)       # ★★ **物理**（投递点击必用）
        w_main = int(r[2] - r[0])
        top = int(r[1])
    except Exception as e:  # noqa: BLE001
        out.update(step="rect", detail="取主窗矩形失败：%s" % str(e)[:80])
        return out

    # ★ 现量行几何（行高会随【窗口尺寸 + 字号】变 ⇒ 每次现量，不写死）
    geo = measure_row_geometry(main)
    # ── 无高亮 ⇒ **先点第一行造出高亮**（否则量不到行高与当前会话身份）
    # ⛔⛔ **dry_run 绝不真点**：`ensure_highlight()` 会真实投递单击，
    #   旧代码在这里**无条件**调 `ensure_highlight()`（它会真实投递单击），
    #   即使 `dry_run=True` 也点 ⇒ 探针/预演脚本照样把用户的微信点了。
    if not geo.get("ok"):
        if dry_run:
            hl = ensure_highlight(main_hwnd=main, dry_run=True)
            out["highlight"] = hl
            out["note_no_highlight"] = ("当前无高亮行 ⇒ 需先单击第一行；dry_run 只预演落点，不真点")
        else:
            hl = ensure_highlight(main_hwnd=main)
            out["highlight"] = hl
            if hl.get("ok") and hl.get("how") == "clicked":
                geo = measure_row_geometry(main)      # 重新现量（现在有绿带了）
    out["geometry"] = {k: geo.get(k) for k in ("ok", "how", "row_pitch", "row_y_range", "green_y")}

    # ★ 列表顶（物理像素）：搜索框下沿往下实测 —— **不再用拍死的常量**
    lt = measure_list_top(main)
    out["list_top"] = lt
    if lt.get("ok") and lt.get("list_top_y"):
        list_top_phys = int(lt["list_top_y"])
    else:
        list_top_phys = int(ROW0_Y_FALLBACK)

    if geo.get("ok") and geo.get("row_pitch"):
        row_pitch_phys = float(geo["row_pitch"]) * 1.0      # row_geometry 给的是"物理/scale"？→ 见下
        # ⚠️ `row_geometry.green_band` 返回的是**物理像素** y；`measure_row_geometry`
        #   里做了 `row_pitch=(y1-y0)/scale`（逻辑）⇒ 这里要**统一到物理**再算。
        #   · 有绿带 ⇒ 直接用绿带的物理范围反推行高（物理），最准；
        #   · **无绿带但有记住的行高**（how="cached_row_h"）⇒ 绿带物理范围不存在，
        #     ⛔ **不许**退成 `ROW_PITCH_FALLBACK`（那等于把刚记下来的行高白扔了）⇒
        #     用 `geo["row_pitch"] × scale` 换回物理（与 green_y 口径一致）。
        gy = geo.get("green_y")
        if gy:
            row_pitch_phys = float(gy[1] - gy[0])
        elif geo.get("row_pitch"):
            row_pitch_phys = float(geo["row_pitch"]) * float(geo.get("scale") or 1.0)
        else:
            row_pitch_phys = float(ROW_PITCH_DIP) * win_scale(main)
        # 消滚动偏移：绿带行序号 → 首行 y（物理）
        base_order = None
        cur = find_current_session_order(adapter, main_hwnd=main, geo=geo)
        out["green_session"] = {"ok": cur.get("ok"), "name": cur.get("name"),
                                "order": cur.get("order"), "raw": cur.get("raw"),
                                "reason": cur.get("reason")}
        if cur.get("ok") and cur.get("order") is not None:
            base_order = int(cur["order"])
            out["geo_base_order"] = base_order
            out["geo_base_name"] = cur.get("name")
            first_row_center_phys = float(gy[0]) + row_pitch_phys / 2.0 - base_order * row_pitch_phys
        else:
            base_order = 0
            first_row_center_phys = list_top_phys + row_pitch_phys / 2.0
            out["geo_fallback"] = True
            out["geo_fallback_reason"] = cur.get("reason") or "绿带行身份未知"
    else:
        # ⛔ **不写死 1.5**：行高兜底值是 96DPI 逻辑值 ⇒ 按**本机实测缩放**折成物理。
        #   缩放由 `display_metrics` 现测（开机预热一次后读记下的值），跨机器才成立。
        try:
            from . import display_metrics as _dm
            _sc = float(_dm.scale(main) or 1.0)
        except Exception:  # noqa: BLE001
            _sc = 1.0
        row_pitch_phys = float(ROW_PITCH_FALLBACK) * _sc
        first_row_center_phys = float(list_top_phys) + row_pitch_phys / 2.0
        out["geo_fallback"] = True
        out["geo_fallback_reason"] = geo.get("note") or geo.get("how") or "量不到行高"

    # ★★ **先按名字在整张列表里挑行**（**不依赖绿底行身份**）—— 覆盖两种原来会失败的场景：
    #    ① 绿带行身份**读不出**（单字名字如 `E` 的 OCR 天生不稳；列表窄时名字还带省略号
    #       「文件…」）⇒ 原来会在下面被"换不出屏幕行号"那条安全阀判死；
    #    ② 目标行**不在可见区** ⇒ 原来要改走搜索（而开搜索窗会闪到用户）。
    #    挑到就用它的**行坐标**当落点（不再走"DB 序号 × 行高"换算）。
    #    兜底口径照人工那套：**其他行都不像**时才退回绿底行（`how="highlight_fallback"`）。
    _pk_y = None
    if True:      # ⚠️ **dry_run 时也要挑行**（挑行是**只读**的：抓一帧 + 读名字）
                  #    ⇒ 这样 `dry_run=True` 预演出来的落点才是**真实会用的那个**。
        try:
            from agent import chat_ocr as _co3
            _imP, _ = grab_window(main, physical=True)
            _pk = (_co3.pick_target_row(_imP, chat_name, pane_left=int(lt.get("pane") or 0))
                   if _imP is not None else None)
            if _pk and _pk.get("ok"):
                out["row_source"] = {"how": _pk.get("how"), "name": _pk.get("name"),
                                     "score": _pk.get("score"), "rows": _pk.get("rows")}
                _pk_y = int(_pk.get("y_abs") or 0) or None
            elif _pk:
                # ⛔ **没挑到也要记下来**（真机事故，PLAN 第 269 项）：原来这里只有 `ok` 分支
                #   ⇒ "全行 0 分"时 `row_source` 缺字段、下面又照旧用"首行中心＋序号×行高"算落点，
                #   相当于**悄悄退回那条最不可信的换算**。现在如实落档，供下面那条闸门判。
                out["row_source"] = {"how": _pk.get("how") or "none", "name": _pk.get("name") or "",
                                     "score": _pk.get("score") or 0.0,
                                     "rows": _pk.get("rows"), "note": _pk.get("note")}
        except Exception as _eP2:  # noqa: BLE001
            log.debug("按名字挑行没成（继续走原路）：%s", str(_eP2)[:80])

    # ⛔⛔ **挑行不可信时的闸门**（真机事故，PLAN 第 269 项）：
    #   现场：8 行 OCR 全乱码、每行相似度 `score=0.0` ⇒ `pick_target_row` 退到
    #   `how="highlight_fallback"`（＝"其他行都不像，用绿底行"）⇒ 调用方拿绿底行坐标**直接双击**
    #   ⇒ **真把无关的「腾讯新闻」开成了一个独立窗**，而函数自己报 `ok=False`
    #   —— "报失败"却已经动了用户的界面。根因＝**把"不算匹配"的落点当匹配用**。
    #   ⇒ 现规矩：只有在**判据可信**时才许用挑行落点；不可信 ⇒ **不许双击**，改走搜索（搜索按名字搜，不依赖列表读数）。
    _rs = out.get("row_source") or {}
    _rs_how = str(_rs.get("how") or "")
    _rs_sc = float(_rs.get("score") or 0.0)
    #   ① `highlight_fallback` + `score<=0` ＝ 一个行都没匹配上（最坏一档）⇒ 落点**整段作废**；
    #   ② `highlight_fallback` + `score>0` ＝ 有弱相关行 ⇒ **必须再核一次身份**（读那一行名字跟目标比）。
    if _rs_how == "highlight_fallback" and _rs_sc <= 0:
        out["row_source_trust"] = {"ok": False, "how": _rs_how, "score": _rs_sc,
                                   "why": "挑行全部 0 分（OCR 与目标无一相关）⇒ 落点作废"}
        _pk_y = None
        log.warning("挑行全部 0 分 ⇒ 弃用挑行落点（不双击，改走搜索）")
    elif _rs_how == "highlight_fallback":
        # 弱相关 ⇒ 核身份：用**挑行时那个同一个打分器**（`_row_score`）再打一次分，
        #   ⛔ 口径必须一致 —— 换个打分器会出现"挑行说像、复核说不像"的自相矛盾。
        try:
            from agent import chat_ocr as _co4
            _nm_got = str(_rs.get("name") or "")
            _sc_recheck, _how_recheck = _co4._row_score(_nm_got, chat_name)
            _nm_ok = float(_sc_recheck or 0.0) >= 1.0          # 只有"完全相等"才放行
        except Exception:  # noqa: BLE001
            _nm_ok = False
            _how_recheck = "recheck_error"
        out["row_source_trust"] = {"ok": _nm_ok, "how": _rs_how, "score": _rs_sc,
                                   "got": str(_rs.get("name") or ""),
                                   "recheck_score": float(_sc_recheck if isinstance(_sc_recheck, (int, float)) else 0.0),
                                   "why": "绿底行兜底档 ⇒ 已用同一打分器复核（须完全相等才放行）"}
        if not _nm_ok:
            _pk_y = None
            log.warning("绿底行兜底档复核不过（读数 %r 与目标 %r 不相等）⇒ 弃用挑行落点（不双击，改走搜索）",
                        str(_rs.get("name") or "")[:40], str(chat_name)[:40])

    # ★ 落点：**物理像素**（window_rect / grab_window 都是物理口径）
    #    · 有"按名字挑到的行"（`_pk_y`）⇒ **以它为准**（这是最直接的量：那一行就在那儿）；
    #    · 否则仍用"首行中心 + 序号 × 行高" —— ⛔ **但这条换算是全链最不可信的一环**
    #      （它假设"DB 序号 == 屏幕行号"，列表一滚就错），所以只在**它自己有依据**时才用。
    _trust = out.get("row_source_trust") or {}
    _y_from_pick = _pk_y is not None
    y_phys = int(_pk_y) if _y_from_pick else int(round(first_row_center_phys + int(order) * row_pitch_phys))
    out["y_source"] = {"how": "pick_row" if _y_from_pick else "order_x_pitch",
                       "y_phys": int(y_phys),
                       "trust_ok": bool(_trust.get("ok")) if _trust else None}
    # ⭐ x：**用 DIP 常数 × 窗口真实缩放**，不再"现场量两个互相矛盾的值再取中点"。
    #   为什么不"现场量名字左沿与列表右沿再取中点"：这两个读数**单位不一致**
    #   （一个偏物理、一个是目测值），换台机器就可能互相矛盾；旧写法在矛盾时会把 `pane` 撑大，
    #   落点于是跑到右侧聊天区 ⇒ 双击/右键都不会生效。
    #   DIP 口径下这些量本来就是固定的：列表宽 256 DIP、头像右沿 ≈120 DIP、行高 64 DIP
    #   ⇒ 落点 = 窗口原点 + DIP × 缩放，只跟显示器缩放这一个变量走。
    _sc = win_scale(main)
    x_phys = int(round(AVATAR_X_DIP * _sc))
    out["scale_used"] = _sc
    out["x_source"] = {"how": "dip_scaled", "scale": _sc, "x_dip": AVATAR_X_DIP,
                       "reason": "按 DIP 常数换算（落点在头像上：与列表宽无关）", "x": x_phys}
    # ★ **交叉校验**（不改行为）：把"现场量出来的那两个读数"也算一遍，只用来**发现口径不一致**。
    #   ⛔ **只在 `dry_run` 时做**：量一次要**抓一整帧 + 逐列剖面**（约 1 秒），而它**不参与**
    #      落点决策 ⇒ 放进真实发送路径就是白付一秒。诊断/标定才需要它。
    if dry_run:
        try:
            _pane_m = int(lt.get("pane") or 0) or int(geo.get("pane") or 0) or int(ROW_PANE_FALLBACK)
            _x_meas, _how_m, _why_m = row_click_x(int(measure_name_left(main) or 0), _pane_m,
                                                   int(r[2] - r[0]))
            out["x_source"]["cross_check"] = {
                "x_measured": _x_meas, "how": _how_m,
                "agree": abs(_x_meas - x_phys) <= max(40, int(0.25 * _pane_m)), "note": _why_m}
        except Exception as _eX:  # noqa: BLE001
            out["x_source"]["cross_check"] = {"error": str(_eX)[:60]}
    # ⛔ **x 安全阀**（与 y 的安全阀对称）：落点必须落在**会话行内** ——
    #   越过列表右沿 ⇒ 落进聊天区（双击/右键都无效）；落到左侧图标栏 ⇒ 点到图标。
    #   越界就**拒绝点击并说清**（宁可不点）。⚠️ 上限按 **列表宽 256 DIP × 缩放** 算，
    #   **不**拿现场测出来的 pane（那正是上次"把 pane 撑大到 541"的错源）。
    _pane_cap = int(round(LIST_W_DIP * _sc))
    if x_phys < 60 or x_phys > _pane_cap:
        out.update(step="guard_x",
                   detail="落点 x=%d(物理) 不在会话行内（按 DIP 换算上限 %d=256DIP×%.2f）⇒ 拒绝点击"
                          "（越界会落进聊天区或左侧图标栏，双击/右键都不会生效）"
                          % (x_phys, _pane_cap, _sc))
        return _drop_click_pt(out)
    x = int(r[0] + x_phys)
    y = int(r[1] + y_phys)
    out["click_pt"] = (x, y)
    out["click_pt_rel_phys"] = (x_phys, y_phys)
    out["main_hwnd"] = main
    out["row_pitch_used"] = round(row_pitch_phys, 2)
    out["first_row_y_used"] = round(first_row_center_phys, 2)

    # ⛔⛔ **安全阀：不许点到"列表以上"（顶栏 / 搜索框）**
    #   根因：首行 y 若用了偏小的兜底 ⇒ 落点落在顶栏 ⇒ 双击 = 微信**全屏**（不是开独立窗）。
    #   ⇒ 此处**硬拦**：落点必须落在列表顶之下（≥ list_top_phys），且不能超出窗口高度。
    if y_phys < int(list_top_phys):
        out.update(step="guard",
                   detail="落点 y=%d(物理) 在列表顶 y=%d 之上 ⇒ 会点到顶栏/搜索框"
                          "（双击=微信全屏）⇒ 拒绝点击" % (y_phys, int(list_top_phys)))
        return _drop_click_pt(out)
    if y_phys > (r[3] - r[1]):
        # ⚠️ 目标行**不在可见区**（被滚出屏幕外）⇒ 点列表这条路走不通。
        #   **不滚列表**：滚动会让**用户眼前的列表动**，比开搜索窗更打扰 ⇒ 改走**搜索定位**：
        #     搜索（完整名）⇒ 单击结果行 ⇒ 切到该会话 ⇒（它成为列表里的高亮行）⇒ 双击高亮行 ⇒ 开窗。
        #   两步都已真机实测（见 `_open_popup_via_search` 的说明）。
        # ⛔ **可选：先置顶目标会话**（`send.pin_target_first`，**默认关**）
        #   为什么放在"改走搜索"**之前**：置顶之后目标恒在最上方 ⇒ 后面那条
        #   "读高亮行 y、双击它"会**稳得多**（不受列表滚动影响）。
        #   但它**会改动用户自己的会话列表顺序** ⇒ 默认关，且开的时候调用方必须是用户显式勾的。
        if pin_target_first_enabled(adapter):
            _po, _pwhy = pin_session_via_menu(adapter, chat_name,
                                              row_y_phys=int(round(first_row_center_phys)))
            out["pin_attempt"] = {"ok": bool(_po), "why": str(_pwhy)[:160],
                                  "at": "not_visible", "order": int(order)}
            log.info("置顶路（目标行不在可见区）：%s", str(_pwhy)[:120])
        _sh, _swhy = _open_popup_via_search(chat_name, adapter)
        if _sh:
            out.update(ok=True, hwnd=int(_sh), how="search_then_highlight", step="opened",
                       detail="目标行不在可见区 ⇒ 改走搜索：%s" % _swhy)
            out["parked"] = park_popup(int(_sh), why="搜索路开出的窗也立刻收起")
            return out
        out.update(step="not_visible_search_failed",
                   detail="目标行不在可见区（落点 y=%d 超出窗口高 %d）⇒ 改走搜索也没成：%s"
                          % (y_phys, r[3] - r[1], _swhy))
        return _drop_click_pt(out)

    # ⛔⛔ **兜底不许点**（安全阀，防"点到别的会话"）：
    #   定位靠"绿带行身份"把 DB 序号换算成屏幕行号；那一步失败时会退到
    #   "假设绿带行在第 0 行"的兜底 ⇒ **DB 序号被直接当成屏幕行号**
    #   ⇒ 一旦两者不等（列表滚动 / DB 里存在屏幕不显示的会话），就会点到**别的会话**。
    #   ⇒ 默认**拒绝点击**（宁可不发/不开，也不动用户的会话）。
    #     要强行继续必须显式传 `allow_fallback_click=True`。
    #   ⚠️ 例外：调用方**显式给了 `row_order`**（＝屏幕行号，由调用方保证）时，
    #      序号不来自 DB ⇒ 兜底无关紧要 ⇒ 不拒绝。
    if (out.get("geo_fallback") and not dry_run and not allow_fallback_click
            and row_order is None and _pk_y is None):
        # ⚠️ 走到这里＝**连"按名字挑行"也没挑到**（两条路都没有可信行号）⇒ 才谈拒绝。
        # ⭐ **退路：改走搜索**（不是直接放弃）。
        #   为什么：这条拒绝的**唯一**理由是"DB 序号 → 屏幕行号换不出来"（怕点到别的会话）；
        #   而**搜索路根本不依赖绿带行身份**（它靠"搜到名字 → 单击结果行 → 双击高亮行"）
        #   ⇒ 这里**应当**像"目标行不在可见区"那一支一样改走搜索，而不是把整条链判死
        #     ——"没有独立窗就自己把它点出来"是本项目的既定口径，读不出身份不该成为终点。
        # ⛔ **可选：先置顶目标会话**（`send.pin_target_first`，**默认关**）—— 同上面的说明。
        #   这条路是"连按名字挑行也没挑到"⇒ 先用置顶把目标顶到最上面，再试搜索。
        if pin_target_first_enabled(adapter):
            _po2, _pwhy2 = pin_session_via_menu(adapter, chat_name,
                                                row_y_phys=int(round(first_row_center_phys)))
            out["pin_attempt"] = {"ok": bool(_po2), "why": str(_pwhy2)[:160],
                                  "at": "id_unknown", "order": int(order)}
            log.info("置顶路（读不出行身份）：%s", str(_pwhy2)[:120])
        _sh2, _swhy2 = _open_popup_via_search(chat_name, adapter)
        if _sh2:
            out.update(ok=True, hwnd=int(_sh2), how="search_then_highlight", step="opened",
                       detail="读不出绿带行身份（%s）⇒ 改走搜索成功：%s"
                              % (str(out.get("geo_fallback_reason"))[:60], _swhy2))
            out["parked"] = park_popup(int(_sh2), why="搜索路开出的窗也立刻收起")
            return out
        out.update(step="unsafe_fallback",
                   detail="读不出绿带行身份 ⇒ 无法把 DB 序号换算成屏幕行号"
                          "（列表滚动 / 存在屏幕不显示的会话时会错位）⇒ **拒绝点击**"
                          "（不许点到别的会话）；改走搜索也没成：%s；"
                          "要强行继续须传 allow_fallback_click=True。原因：%s"
                          % (str(_swhy2)[:80], str(out.get("geo_fallback_reason"))[:100]))
        return out

    if dry_run:
        out.update(ok=True, how="dry_run", step="planned",
                   detail="将点 (%d,%d)（第 %d 行，%s击）—— dry_run 未真点"
                          % (x, y, int(order), "双" if double_click else "单"))
        return out

    # ③ 开独立窗：
    #   ⭐⭐ **决定性实验结论**：
    #      · 目标行**已选中**（绿带压在该行）⇒ **双击开不出窗**（实测 find_popup=0）
    #      · 目标行**未选中**（绿带在别行 / 全灰）⇒ 双击**开出窗**（实测 find_popup≠0）
    #   机制：第一枪落在"已选中行"上 ⇒ 微信判成**取消选中** ⇒ 双击被拆散 ⇒ 不开窗。
    #   ⇒ **正确做法：先把选中态挪走（或确认目标行未选中），再一把双击**。
    #
    #   人的手势（实测本机微信）：
    #     · 单击            ⇒ 选中该行 / 若已选中则取消
    #     · 单击后停顿       ⇒ 微信按"两次独立单击"结算 ⇒ 高亮被取消
    #     · **连点两下（间隔 ~175ms）** ⇒ 弹独立窗
    #   ⛔ 旧写法"单击选中 → 停 0.6s → 双击"**是错的**：那 0.6s 停顿 = 取消选中 ⇒ 第二下等于白点。
    #   ⛔ 不切前台（投递不需要前台）。
    try:
        from agent import input_backend as IB3
        be = IB3.select_backend()
    except Exception as e:  # noqa: BLE001
        out.update(step="backend", detail="取输入后端失败：%s" % str(e)[:80])
        return out

    import time as _t
    before = {int(w["hwnd"]) for w in list_wechat_windows()}

    # ── ★★★ 节拍常量：**来自真机实测的真值**（36 下点击，全不漏）──
    #   人手手势（经全局鼠标钩子录得）：
    #     · 单击             ⇒ 会话变高亮
    #     · 单击后停顿       ⇒ 高亮取消
    #     · **连点两下（间隔 ~175ms）** ⇒ 弹出独立窗
    #   实测连点样本（DOWN 相邻间隔，落点差 ≤1px）：
    #     179ms / 179ms / 172ms   ← 这就是"人手的双击"
    #   ⇒ **双击 = 两枪，间隔 ~175ms，不加任何多余单击、不加长停顿**。
    #   ⛔ 旧写法"单击选中 → 停 0.6s → 双击"是**错的**：
    #      那 0.6s 停顿在微信里 = "取消选中" ⇒ 第二枪等于白点。
    #   ⚠️ 间隔/按下时长取**模块级那两个真值**（`DOUBLE_GAP_S` / `PRESS_MS_DOUBLE`），
    #      此处不再另写数字 —— 与 `wechat.py::open_chat_by_search` 的双击共用同一份。
    press_ms = PRESS_MS_DOUBLE

    def _one_click(_tgt):
        try:
            be.click(_tgt, (x, y), hover_ms=60, press_ms=press_ms)
        except TypeError:
            be.click(_tgt, (x, y), press_ms=press_ms)
        except Exception as e:  # noqa: BLE001
            return "异常：%s" % str(e)[:60]
        return ""

    def _open_by_human_rhythm(_tgt):
        """**纯双击**（两枪，间隔 ~175ms）。返回 `(ok, why)`。

        ⚠️ **不再做"先单击选中再双击"** —— 实测用户就是这么双击的，
          中间不夹单击、不夹长停顿。
        """
        err = _one_click(_tgt)               # 双击第一枪
        if err:
            return False, err
        if not double_click:
            return True, "单click（double_click=False）"
        _t.sleep(DOUBLE_GAP_S)               # ★ 175ms（实测人手间隔）
        err = _one_click(_tgt)               # 双击第二枪
        if err:
            return False, err
        return True, ""

    def _target_row_is_selected():
        """目标行是不是**当前高亮行**？（决定"双击能不能开窗"）"""
        try:
            im2 = grab_window(main, physical=True)
            im2 = im2[0] if isinstance(im2, tuple) else im2
            b2 = None
            from agent import row_geometry as _rg2
            b2 = _rg2.green_band(im2) if im2 is not None else None
            if not b2:
                return False, False            # 无高亮 ⇒ 目标行必未选中
            gy2 = (int(b2["y0"]), int(b2["y1"]))
            # 高亮行中心 vs 目标行中心（都物理）
            band_c = (gy2[0] + gy2[1]) / 2.0
            same = abs(band_c - float(y_phys)) <= (gy2[1] - gy2[0]) * 0.6
            return True, bool(same)
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            return False, False

    def _deselect_target_row():
        """把"选中态"从目标行挪走（**挪到另一行**，不是"原地取消"）。

        ⭐ 依据（实测已证）：
          · 目标行**已选中**（高亮压在它身上）⇒ 双击**开不出窗**（实测 find_popup=0）
          · 目标行**未选中**（高亮在别行）  ⇒ 双击**开出窗**  （实测 find_popup=非0）
        机制：第一枪落在"已选中行"上时，微信判成"刷新/重选该会话"而**不是取消选中**
              ⇒ 双击被拆散 ⇒ 不认成双击。

        ⛔ **为什么不"原地单击取消"**：实测（`_ds2`）单击已选中行**第一次不会取消高亮**
          （反而是刷新该会话），**要两次才取消** ⇒ 两枪会和后续双击混在一起，更乱。
        ⇒ 采用"**单击另一行**"把选中挪走（实测 B 场景一次就成）。
        ⚠️ 副作用：主窗当前会话会被切到那一行 —— 该副作用可接受
          （**要操作的只是那个独立窗，主窗不必保持原状态**）。

        返回 `(ok, note)`。
        """
        has, same = _target_row_is_selected()
        if not has or not same:
            return True, "目标行未选中（无需去选中）"
        # 选一个"不是目标行"的邻居：优先第一行；若目标就是第一行，则用第二行
        alt_order = 0 if int(order) != 0 else 1
        alt_y = int(round(first_row_center_phys + alt_order * row_pitch_phys))
        if alt_y < int(list_top_phys) or alt_y > (r[3] - r[1]):
            return True, "找不到可用的替代行（不改动，直接双击试试）"
        be.click(main, (x, int(r[1] + alt_y)), hover_ms=60, press_ms=60)
        _t.sleep(0.6)
        has2, same2 = _target_row_is_selected()
        if same2:
            return False, "已单击第 %d 行但目标行仍被选中（去选中失败）" % (alt_order + 1)
        return True, "已把选中挪到第 %d 行（目标行现为未选中）" % (alt_order + 1)

    if double_click:
        _ds_ok, _ds_note = _deselect_target_row()
        out["deselect"] = {"ok": bool(_ds_ok), "note": _ds_note}
        if not _ds_ok:
            # 去选中失败不致命：照旧双击（可能仍成），但如实记下
            out["deselect"]["warn"] = "去选中未成功，仍按双击流程继续"

    ok2, why2 = _open_by_human_rhythm(main)
    out["click_target"] = IB3.win_kind(main, main)
    out["rhythm"] = {"double_gap_s": DOUBLE_GAP_S, "press_ms": press_ms,
                     "src": "真机录制的鼠标节奏真值"}
    if not ok2:
        out.update(step="click", detail=why2)
        return out

    # ④ 等新独立窗 + 按标题确认
    t0 = _t.time()
    # ⚡ **前 1 秒用 0.05s 的密采样**：新窗一出现就立刻收起（`park_popup`）⇒ 尽量少闪。
    #    1 秒后仍没等到才放宽到 0.3s（省 CPU；那时的"没等到"多半是真没开出来）。
    while _t.time() - t0 < float(timeout_s):
        _t.sleep(0.05 if (_t.time() - t0) < 1.0 else 0.3)
        h2 = find_popup(title_name)
        if h2.get("hwnd"):
            after = {int(w["hwnd"]) for w in list_wechat_windows()}
            out.update(ok=True, hwnd=int(h2["hwnd"]), how="opened", step="opened",
                       detail="双击后出现独立窗（标题 %r）；新增顶层窗 %d 个"
                              % (h2.get("title"), len(after - before)))
            out["parked"] = park_popup(int(h2["hwnd"]), why="开窗后立刻收起（别闪到用户）")
            return out

    # ⑤ 自愈：再试一轮（第一次可能被叉掉 / 一闪即没）
    out["retried"] = True
    ok3, _why3 = _open_by_human_rhythm(main)
    if ok3:
        t0 = _t.time()
        while _t.time() - t0 < float(timeout_s):
            _t.sleep(0.05 if (_t.time() - t0) < 1.0 else 0.3)
            h3 = find_popup(title_name)
            if h3.get("hwnd"):
                out.update(ok=True, hwnd=int(h3["hwnd"]), how="opened_retry", step="opened",
                           detail="第一轮未成（疑似被叉掉/一闪即没）⇒ 重试后出现独立窗")
                out["parked"] = park_popup(int(h3["hwnd"]), why="重试开出的窗也立刻收起")
                return out
    # ③′ ★★ **右键退路**（第 225 项：「**双击 → 右键**」两级开窗里的第二级）
    #   为什么要有它：开独立窗是本项目**唯一**的发送入口（发送只走独立窗，不切会话）。
    #   双击靠**手势判定**（受列表滚动、选中态、窗口可见性影响），而**右键菜单是显式命令**
    #   ⇒ 双击开不出来时，右键菜单那条路更确定（它是显式命令，不依赖手势判定）。
    #   ⛔ 三条纪律：
    #     ① **只在双击（含一轮重试）都失败之后**才走 ⇒ 不影响现有成功路径；
    #     ② 菜单项**多候选**匹配，**匹配不到就不点**（不许学"兜底点最上面那项"——那会点到别的命令）；
    #     ③ 候选词见 `POPUP_MENU_LABELS`（用词随微信版本可能变，全匹配不上时不点）。
    if adapter is not None:
        # ⛔ **GUI 入口是 `adapter._get_gui()`**（同 P0-2）：`WeChat` 把 GUI 存在 `self._gui`，
        #   **没有 `self.gui` 属性** ⇒ 老写法 `getattr(adapter, "gui", None)` 恒为 None
        #   ⇒ 这条右键退路**整段是死的**（一次都走不到）。
        _gui_rc = None
        try:
            _gg = getattr(adapter, "_get_gui", None)
            _gui_rc = _gg() if callable(_gg) else None
        except Exception:  # noqa: BLE001
            _gui_rc = None
        if _gui_rc is not None:
            for _lb in POPUP_MENU_LABELS[:2]:      # 最多试两个候选（每个要点一次右键＋等菜单，别无限试）
                try:
                    _hit = bool(adapter._right_click_menu_posted(
                        _gui_rc, int(x - int(r[0])), int(y - int(r[1])), _lb, delay=1.0))
                except Exception as _eRC:  # noqa: BLE001
                    _hit = False
                    log.info("开独立窗·右键退路异常（%s）：%s", _lb, str(_eRC)[:80])
                if not _hit:
                    continue
                t0 = _t.time()
                while _t.time() - t0 < float(timeout_s):
                    _t.sleep(0.05 if (_t.time() - t0) < 1.0 else 0.3)
                    h4 = find_popup(title_name)
                    if h4.get("hwnd"):
                        out.update(ok=True, hwnd=int(h4["hwnd"]), how="opened_right_click", step="opened",
                                   detail="双击（含重试）没开出来 ⇒ 右键菜单「%s」开出来了" % _lb)
                        out["parked"] = park_popup(int(h4["hwnd"]), why="右键路开出的窗也立刻收起")
                        return out
                out["right_click_tried"] = str(out.get("right_click_tried") or "") + ("菜单项「%s」点了但没等到窗；" % _lb)

    out.update(step="open_failed",
               detail="双击后 %ds 内没等到 %r 的独立窗（已重试一轮）；当前有标题的窗=%s"
                      % (int(timeout_s), chat_name,
                         [w["title"] for w in list_wechat_windows() if w["title"].strip()][:6]))
    # ④ 仍失败 ⇒ **给用户一条可照做的引导**（这是整条链里唯一"用户一定做得到、程序最难做"的一步）。
    #   为什么值得单独给：开独立窗是全部自动化的**唯一入口**（发送只走独立窗），
    #   而它靠"双击列表行"这条**手势**路 —— 受窗口可见性、滚动位置、微信手势判定影响。
    #   拖/双击**一次就成**（他自己就是这么开的），比程序反复试更省事、也不动他的光标。
    #   ⇒ 把这一步显式交给他，而不是让程序卡在那儿一遍遍重试。
    out["user_action"] = (
        "请手动开一次：在微信会话列表里找到「%s」，把那一行**拖出来**（或双击它）——"
        "它会变成一个独立窗口。之后对这一会话的发送就都走那个窗口了，"
        "不用再点列表、也不会受列表滚动/置顶影响。" % chat_name)
    out["detail"] = str(out.get("detail") or "") + "｜" + out["user_action"]
    # ★★ **这一步要"让用户看得见"**：开独立窗是唯一的发送入口，卡在这一步＝机器人什么都不发了。
    #   只写进返回值没人看得到（调用方在后台循环里）⇒ 记一条"求助"，由状态接口带给前端弹窗。
    try:
        note_popup_help(chat_name, out["user_action"], detail=str(out.get("detail") or ""))
    except Exception as _eH:  # noqa: BLE001
        log.debug("记「开窗求助」失败（不影响返回）：%s", str(_eH)[:60])
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# ★★ 三档能力模型 + 「全走独立窗」的统一发送入口
# ═══════════════════════════════════════════════════════════════════════════════
#
# 架构硬规则：
#   ① **发送永远只针对某一个独立窗 `hwnd`** —— 独立窗 = 一个会话、标题即会话名，
#      `find_popup` 能精确认出"哪个是哪个" ⇒ **结构上就不可能发错**。
#   ② **绝不"降级到主窗发送"** —— 主窗要先在会话列表里选中目标行，
#      每多一步就多一次发错机会；那是**风险**，不是退路。
#      独立窗不在 ⇒ `ensure_popup` 去点出来；点不出来 ⇒ **明确失败**，交上层决定。
#   ③ 按**档位**决定能做什么；做不到的能力**如实拒绝**，不偷偷换通路。

TIER_MINIMIZED = "minimized"
TIER_BACKGROUND = "background"
TIER_FOREGROUND = "foreground"
#: ★★ "**透明档**"：窗口**全透明看不见**，但仍保持"能发图"的状态。
#:    本质 = **前台档 + 全透明隐身**（`WS_EX_LAYERED` + alpha=0）。
#:    ⇒ 这是**推荐的发图工作态**：人看不见窗口，发完把前台还回去。
TIER_TRANSPARENT = "transparent"
TIER_NONE = "none"


def popup_is_invisible(hwnd: int) -> bool:
    """独立窗当前是不是"**人看不见**"（我们给它加过 `WS_EX_LAYERED` 全透明）。

    ⚠️ 只能查扩展样式位（Windows **没有**读 alpha 的 API）⇒ 我们只在"设成全透明"时
      加这个位，所以"有这个位"≈"我们设过隐身"。不保证外部程序没动过。
    """
    try:
        import ctypes as _ct
        ex = _ct.windll.user32.GetWindowLongW(int(hwnd), GWL_EXSTYLE) & 0xFFFFFFFF
        return bool(ex & WS_EX_LAYERED)
    except Exception:
        return False

#: 每档能做哪些事（**实测标定**；明细见 `报告/独立窗发送-三档能力矩阵_20261002.md`）
#:
#: ★★ **两轮实测后的最终结论**（第二轮的更正很重要，别只看第一轮）：
#:   · **发文字**：三档全通（`WM_CHAR` + 回车，零坐标，最小化也成立）。
#:   · **发图/发表情**：**本质上要求"微信窗口是当前前台窗"**。
#:     通路 = `popup_paste_blind`（真按 Ctrl + 投递 V）。
#:     ⛔ **为什么必须前台**：微信判 Ctrl 靠 `GetKeyState`，而真按的 Ctrl
#:       只会被**前台窗口所属线程**收走；微信不在前台 ⇒ 它看不到 Ctrl
#:       ⇒ 投递的 `V` **退化成字面字母 v** ⇒ 回车就把 v 发出去了（实测污染过群）。
#:     ⛔ 试过并**判死**的"不需要前台"方案：`AttachThreadInput` + `SetKeyboardState`
#:       （实测仍退化成字面字母）、`WM_PASTE`、`WM_CONTEXTMENU`、`VK_APPS`、`WM_DROPFILES`。
#:   · 可见（非最小化）时，还有**备选**：几何落点 + 右键「粘贴」菜单 —— 但它**会抢前台**。
TIER_MATRIX = {
    TIER_MINIMIZED: {
        "text": True,
        "image": "only_if_foreground",
        "image_channel": "ctrl_v_blind（需微信是前台；否则拒绝）",
        "steals_foreground": False,
        "note_channel": ("最小化 + **仍是前台**（如脚本 `ShowWindow(SW_MINIMIZE)` 之后）⇒ 可发图；"
                         "最小化 + 非前台 ⇒ **拒绝**（几何路也走不通：矩形是哨兵，"
                         "且投坐标点击会把窗口打进**离屏僵死态**）"),
    },
    TIER_BACKGROUND: {
        "text": True,
        "image": "via_menu_steals_foreground",
        "image_channel": "① ctrl_v_blind（被闸门拒绝，因为不是前台）→ ② 几何落点+右键菜单（**会抢前台**）",
        "steals_foreground": True,
        "note_channel": ("想要**零打扰**就只能发文字；要发图**必须接受一次抢前台**"
                         "（调用方传 `allow_foreground_steal=True`）"),
    },
    TIER_FOREGROUND: {
        "text": True,
        "image": True,
        "image_channel": "ctrl_v_blind（首选，零坐标、不抢）／右键菜单（备选）",
        "steals_foreground": False,
        "note_channel": "**唯一「零打扰还能发图」的档**",
    },
    TIER_TRANSPARENT: {
        "text": True,
        "image": True,
        "image_channel": "ctrl_v_blind（零坐标）",
        "steals_foreground": False,
        "note_channel": ("★ **推荐的发图工作态**：全透明隐身（人看不见）+ 仍是前台线程"
                         "⇒ 发图零打扰；且 `PrintWindow` 照常能抓 ⇒ **秒级自检也保得住**"),
    },
    TIER_NONE: {
        "text": False, "image": False, "steals_foreground": False,
        "why_image": "没有独立窗（请先 ensure_popup）",
        "why_text": "没有独立窗（请先 ensure_popup）",
    },
}


def popup_tier(hwnd: int) -> str:
    """判定独立窗当前处在**哪个档** ⇒ `minimized` / `background` / `foreground` / `none`。

    判据（按序）：
      ① 句柄无效 / 窗已不在           ⇒ `none`
      ② `IsIconic` 为真               ⇒ `minimized`
      ③ 矩形是离屏哨兵 `≤ -10000`     ⇒ `minimized`（"看着没最小化、其实在屏外"的僵死态）
      ④ 就是 `GetForegroundWindow()`  ⇒ `foreground`
      ⑤ 其余（可见、有真实矩形、非前台）⇒ `background`
    """
    hwnd = int(hwnd or 0)
    if not hwnd:
        return TIER_NONE
    try:
        if not is_popup_alive(hwnd):
            return TIER_NONE
    except Exception:
        return TIER_NONE
    try:
        if _is_iconic(hwnd):
            return TIER_MINIMIZED
    except Exception:
        pass
    try:
        r = window_rect_any_state(hwnd)
        if int(r[0]) <= -10000 or int(r[1]) <= -10000:
            return TIER_MINIMIZED
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return TIER_MINIMIZED
    try:
        import ctypes as _ct
        if int(_ct.windll.user32.GetForegroundWindow()) == hwnd:
            # ★ 前台 + 全透明 ⇒ 单独认成"透明档"
            return TIER_TRANSPARENT if popup_is_invisible(hwnd) else TIER_FOREGROUND
    except Exception:
        pass
    return TIER_BACKGROUND


def send_to_chat(chat_name: str, *, text: str = None, image: str = None,
                 ensure: bool = True, adapter=None,
                 allow_foreground_steal: bool = False,
                 timeout_s: float = None, rect_hint=None,
                 confirm_delivered: bool = False,
                 gap_timeout_s: float = 30.0) -> dict:
    """★★ **统一发送入口 —— 只走独立窗，永不碰主窗** ⇒ 结果字典。

    ⭐ **为什么要这个入口**：独立窗"一窗一会话"、标题就是会话名
      ⇒ **认准 hwnd 就不可能发错**；而主窗发送要先在会话列表里选行，
      **每多一步都是一次风险** ⇒ 所以本入口**在结构上抹掉了"发错会话"的可能**。

    ⛔ **不会发生的事**：
      · 找不到独立窗时**不会**退回主窗发送 —— 只返回失败（`step="locate"`）。
      · **最小化档发图直接拒绝**（`image_blocked="tier_minimized"`），不硬试
        （硬试会把窗口打进离屏僵死态）。
      · **后台档发图默认拒绝**（`image_blocked="foreground_steal"`），
        因为发图必然抢前台；要发必须显式传 `allow_foreground_steal=True`。

    参数
      `text` / `image` —— 要发的内容（至少给一个）。
      `ensure` —— 没有独立窗时是否自动去点出来（`ensure_popup`）。
      `allow_foreground_steal` —— 允许"为了发图而抢前台"（默认 `False` = 宁可不发）。
        ⚠️ 说明（攻关后）：发图**首选"盲粘贴"**（真按 Ctrl + 投递 V，零坐标、
        **窗口不上屏、不动光标**，最小化档也成立；代价是前台可能被抢约 0.1ms 后立刻还回，
        见 `popup_paste_blind` docstring）；只有盲粘贴失败时才需要退回"几何落点 + 右键菜单"
        那条**会抢前台**的路 —— 本开关控制的就是**要不要允许这个退回**。

    返回（要点）
      `ok` / `tier` / `hwnd` / `capabilities` / `text_ok` / `image_ok` /
      `image_blocked` / `image_why` / `text_result` / `image_result`

    ★★ **判据分两级**（`confirm_delivered` 控制第二级）：
      ① **即时（主）**：发图后看**发送键由绿转灰** ⇒ `accepted`
         （＝ 微信把输入框清空了 ⇒ **已受理**，实测 ~0.36s）⇒ **据此返回成功**；
      ② **后续（次）**：**DB 回读**出现 `[图片]`/`[动画表情]` 新行 ⇒ `db_verified`
         （图片落库实测 30s~3min）。
      ⇒ 默认 **`confirm_delivered=False`**：**不等 DB**，受理即返回（发送路径亚秒级）；
        需要"进一步核实"时显式传 `True`（会等到 `timeout_s`，默认 300s）。
    """
    out = {"ok": False, "step": "", "detail": "", "chat": chat_name,
           "popup_only": True, "tier": TIER_NONE, "hwnd": 0,
           "text_ok": None, "image_ok": None}
    # ⛔ **暂停/停止闸**：本函数是"独立窗直发"的**总入口**，
    #   原先**没有任何 control 检查** ⇒ `send_image` 里那条老路径被暂停闸拦住时，
    #   走这里的图/表情**照样发得出去**，让"暂停"变成假的。⇒ 在**最上游**堵住。
    try:
        from agent import control as _ctl0
        _halt0 = _ctl0.halt_reason()
        if _halt0:
            out.update(step="halted", detail=_halt0)
            return out
    except Exception:
        pass
    name = (chat_name or "").strip()
    if not name:
        out.update(step="arg", detail="没给会话名")
        return out
    if not text and not image:
        out.update(step="arg", detail="text / image 至少要有一个")
        return out

    # ⛔ 窗口标题只有**显示名** ⇒ 传 username 时必须先解析，否则 `find_popup` 匹配不上。
    title_name = resolve_title_name(name, adapter)

    # ① 定位独立窗（**绝不碰主窗发送**）
    hit = find_popup(title_name)
    hwnd = int(hit.get("hwnd") or 0)
    if (not hwnd) and ensure:
        ep = ensure_popup(name, adapter=adapter,
                          timeout_s=float(timeout_s or 8.0))
        out["ensure_popup"] = {k: ep.get(k) for k in ("ok", "how", "step", "detail")}
        hwnd = int(ep.get("hwnd") or 0)
    if not hwnd:
        out.update(step="locate",
                   detail="没有 %r 的独立窗 ⇒ **拒绝发送**"
                          "（本入口原则上不走主窗：在主窗里先选会话行再发，"
                          "多一步就多一次**发错**的机会）" % name)
        return out
    out["hwnd"] = hwnd
    out["title"] = hit.get("title") or name

    tier = popup_tier(hwnd)
    out["tier"] = tier
    out["capabilities"] = dict(TIER_MATRIX.get(tier) or {})
    if tier == TIER_NONE:
        out.update(step="locate", detail="独立窗句柄无效（窗可能已被关掉）")
        return out

    # ② 文字（**三档都行**：零坐标 + DB 回读，最小化也成立）
    if text:
        t = send_to_popup(name, text, adapter=adapter,
                          timeout_s=float(timeout_s or 20.0))
        out["text_ok"] = bool(t.get("ok") and (t.get("db_verified")
                                               or t.get("step") == "sent_blind"))
        out["text_result"] = {k: t.get(k) for k in
                              ("ok", "step", "detail", "db_verified", "degraded")}

    # ③ 图片/表情（**三档都做得到** —— 首选"盲粘贴"，零坐标、窗口不上屏、不动光标）
    if image:
        cap = TIER_MATRIX.get(tier) or {}
        if not cap.get("image"):
            out["image_ok"] = False
            out["image_blocked"] = "tier_%s" % tier
            out["image_why"] = cap.get("why_image")
        else:
            # ⚠️ 图片/表情落库慢（实测 30~180s，极端情况更久）⇒ 不给就取本入口的宽松默认
            #    （曾在此写死 90s ⇒ **实际已送达却判成"未确认"**；后改为不透传 180s，
            #     实测仍偏紧 ⇒ 统一放宽到 300s）
            im = send_image_to_popup(name, image, adapter=adapter,
                                     timeout_s=float(timeout_s or IMAGE_CONFIRM_TIMEOUT_S),
                                     rect_hint=rect_hint,
                                     confirm_delivered=bool(confirm_delivered),
                                     allow_menu_fallback=bool(allow_foreground_steal),
                                     gap_timeout_s=float(gap_timeout_s))
            # ★★ 判据分两级（**最小化档下"绿转灰"读不到画面 ⇒ 只有 DB 一条**）：
            #   ① `accepted`（发送键**由绿转灰**）—— **窗口可见/透明档**才有（最小化档抓不到画面）；
            #   ② `db_verified`（DB 回读出现新行）—— **任何档位都可用，但慢**（30s~3min）。
            #   ⇒ **发送本身已成功**（`im["ok"]` = 粘贴已投递 + 三枪已发）时**立即返回成功**，
            #     不让调用方等 DB；要"进一步核实"就显式传 `confirm_delivered=True`
            #     （同步等），或由调用方**稍后**自己查 `_db_messages`（推荐，不阻塞）。
            _accepted = bool(im.get("accepted"))
            _dbv = bool(im.get("db_verified"))
            out["image_ok"] = bool(im.get("ok") and (_accepted or _dbv or not confirm_delivered))
            out["image_accepted"] = _accepted
            out["image_db_verified"] = _dbv
            out["image_verdict"] = ("受理(绿转灰)" if _accepted
                                    else ("DB核实" if _dbv
                                          else ("已投递(未核实)" if not confirm_delivered
                                                else "未确认")))
            out["image_result"] = {k: im.get(k) for k in
                                   ("ok", "step", "detail", "db_verified", "accepted",
                                    "accepted_detail", "green_after_send",
                                    "paste_channel", "paste_point", "paste_green",
                                    "paste_attempts", "green_after_blind")}
            # 剪贴板"备份/放回"的结果透传到统一入口（否则调用方看不到）
            out["clipboard_restored"] = im.get("clipboard_restored")
            out["clipboard_restore_why"] = im.get("clipboard_restore_why")

    out["ok"] = bool((out["text_ok"] or out["image_ok"])
                     and out["text_ok"] is not False
                     and out["image_ok"] is not False)
    out["step"] = "done"
    out["detail"] = "档位=%s ｜ 文字=%s ｜ 图片=%s%s" % (
        tier, out.get("text_ok"), out.get("image_ok"),
        ("（图片被拦：%s）" % out["image_blocked"]) if out.get("image_blocked") else "")
    return out

