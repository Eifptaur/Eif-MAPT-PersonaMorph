# -*- coding: utf-8 -*-
"""wechat_windows.py —— **按窗口标题定位会话窗口**（绕过像素定位的另一条路）

★为什么有它（2026-10-01 用户提出 + 当场实测验证）
  用户："**微信的群聊双击可以直接把那个群变为一个单一弹窗**……这个**对所有在会话栏里的东西都生效，
  包括私聊**"。他双击一次后，我方 `EnumWindows` 立刻看到：
```
微信(pid=9336) 可见的 Qt 顶层窗口 = 2 个：
  hwnd=150667342  标题=「群deepseek」     687x602   ← 主窗
  hwnd=41885108   标题=「aaa偷啃使用者」   613x647   ← ★双击后新出现的独立窗口
```
  ⇒ 三点全部证实：① 双击会话 ⇒ **出现新的独立顶层窗口**；② **该窗口标题 = 会话名**；
     ③ 句柄独立、可被 `EnumWindows` 找到。

★这条路的价值（**对"单群 / 少群"场景可能是最优解**）
  · **"当前打开的是哪个会话"** ⇒ 读窗口标题（一次 `GetWindowText`，不需要截图/OCR/比对）
  · **"切到目标群"**        ⇒ `EnumWindows` 找标题匹配的窗口 ⇒ 激活（**不需要点会话列表**）
  ⇒ **完全没有"像素精度"问题**（窗口边界由系统给），也**不依赖 wxocr**（零第三方、零灰色操作）。

⚠️ 边界（实测/未实测，如实标注）
  · **依赖用户先把目标会话双击成独立窗口**（配置成本；可做成一次性引导）；
  · **主窗标题也 = 当前会话名**（实测 `群deepseek`）⇒ 所以"标题匹配"要**同时考虑主窗与独立窗**；
  · **微信重启后独立窗口是否保留 —— 未实测**（若不留，需要"自动重开"，但"双击列表行"比"精确定位某行"宽容得多）；
  · **独立窗内发送是否照常 —— 未实测**（输入框/发送键应在，但需真机确认）。
"""
from __future__ import annotations

import ctypes as _ct
import ctypes.wintypes as _wt

_user32 = _ct.windll.user32

#: 微信主窗 + 独立会话窗的类名（实测 4.1.15.8 都是它）
WECHAT_QT_CLASS = "Qt51514QWindowIcon"

_WNDENUMPROC = _ct.WINFUNCTYPE(_ct.c_bool, _ct.c_int, _ct.c_int)


class _RECT(_ct.Structure):
    _fields_ = [("left", _ct.c_long), ("top", _ct.c_long),
                ("right", _ct.c_long), ("bottom", _ct.c_long)]


def list_wechat_windows(image_name: str = "Weixin.exe") -> list:
    """枚举**所有微信进程的可见顶层窗口** ⇒ `[{hwnd, pid, cls, title, w, h}]`。

    只返回**可见**窗口（隐藏的系统消息窗、托盘窗一律排除 —— 实测它们标题为空或类名不是 Qt）。
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
        title = _ct.create_unicode_buffer(512)
        _user32.GetWindowTextW(hwnd, title, 512)
        r = _RECT()
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
    import subprocess
    try:
        p = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % image_name, "/FO", "CSV"],
                           capture_output=True)
        txt = (p.stdout or b"").decode("gbk", "ignore")
    except Exception:
        return []
    pids = []
    for line in txt.splitlines()[1:]:
        cols = [c.strip('"') for c in line.split('","')]
        if len(cols) >= 2 and cols[1].isdigit():
            pids.append(int(cols[1]))
    return pids


def find_chat_window(name: str, exact: bool = True) -> dict:
    """按**会话名**找窗口 ⇒ `{hwnd, title, exact, candidates}`；找不到给 `hwnd=0`。

    ⚠️ 主窗的标题**也是**"当前打开的那个会话名"（实测 `群deepseek`）⇒
       所以本函数会**同时命中主窗与独立窗**；调用方按需选（想"不影响主窗"就优先取非主窗）。
    """
    wins = [w for w in list_wechat_windows() if w["title"].strip()]
    if exact:
        hit = [w for w in wins if w["title"].strip() == (name or "").strip()]
    else:
        n = (name or "").strip().lower()
        hit = [w for w in wins if n and n in w["title"].strip().lower()]
    if not hit:
        return {"hwnd": 0, "title": "", "exact": bool(exact),
                "candidates": [w["title"] for w in wins]}
    # 优先"独立窗"（尺寸较小者通常是双击出来的会话窗），主窗兜底
    hit.sort(key=lambda w: (w["w"] * w["h"]))
    return {"hwnd": hit[0]["hwnd"], "title": hit[0]["title"], "exact": bool(exact),
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
