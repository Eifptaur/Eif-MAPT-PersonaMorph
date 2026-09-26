# -*- coding: utf-8 -*-
"""窗口操作原语 —— **关微信窗口的唯一入口**。

为什么要有这个文件
------------------
"关掉一个窗口"在本项目是一条**踩过两次事故**的路径：

  · 第一次：微信主窗被 Qt 重建、`hwnd` 变了 ⇒ 新句柄不在任何白名单里 ⇒ 被当成"菜单浮层"
    `WM_CLOSE` 掉 ⇒ **用户的微信主窗口整个消失**；
  · 第二次同型：`_reattach_if_floating()` 靠"标题含会话名 + 类名 Qt 开头"挑窗，只跳过**当时记下的**
    主窗句柄 ⇒ 主窗一被重建就被当"浮动聊天窗"关掉。

⇒ 规矩（与"不动用户键鼠"同级）：**任何 `WM_CLOSE` 之前，当场重新问一次"这个句柄是不是微信主窗 /
渲染子窗"**，命中就拒绝并留日志 —— 宁可留一个浮层在屏幕上，也绝不许关用户的窗。

⛔ 为什么原语要放在这里、而不是留在 `wechat.py` 里：
  判据本身只依赖 `input_backend.find_main_window()`，但**调用方散在多个模块**
  （`wechat` / `wechat_ui`）。留在 `wechat` 里就要 `wechat_ui → wechat` 的反向依赖（循环），
  于是历史上 `wechat_ui` 那处**直接裸关窗绕过判据**。收到这里之后，判据只有一份、谁都能问。

⚠️ **不适用**的两类（它们关的不是微信的窗，留在各自模块里）：
  · `ui_adapt._clean_overlays()`：关的是**系统输入叠加层**（TouchKeyboard 那一族），
    收窄手段是"类名 + 标题白名单"，目标是别人的窗；
  · `tray.py`：关的是**我们自己的**托盘窗。
"""
from __future__ import annotations

import logging

log = logging.getLogger("persona-morph")

#: `WM_CLOSE`（关窗消息）。放成常量，免得各处再写 `0x0010` 这种魔数。
WM_CLOSE = 0x0010


def ok_to_close(hwnd: int, why: str = "") -> bool:
    """这个句柄**允不允许**发 `WM_CLOSE`？返回 False = 它是微信主窗/渲染子窗（绝不关）。

    判据（每次现算，不吃缓存 —— 缓存正是第一次事故的成因）：**当场**取一次主窗与渲染子窗，
    命中就拒绝并记 warning。拿不到句柄/判不出来一律**拒绝**（fail-closed）。
    """
    try:
        h = int(hwnd or 0)
        if not h:
            return False
        try:
            import win32gui

            if not win32gui.IsWindow(h):
                return False
        except Exception: # noqa: BLE001 — 没有 pywin32 时靠下面的归属判据
            pass
        main = 0
        render = 0
        try:
            from . import input_backend as _ib

            main = int(_ib.find_main_window() or 0)
            if main:
                try:
                    render = int(_ib.find_render_child(main) or 0)
                except Exception: # noqa: BLE001
                    render = 0
        except Exception: # noqa: BLE001
            pass
        if h in (main, render):
            log.warning("⛔ 拒绝 WM_CLOSE：目标是**微信主窗/渲染子窗**（%s，hwnd=%d，main=%d）"
                        "—— 宁可留浮层，绝不关用户的窗", why or "未注明", h, main)
            return False
        return True
    except Exception: # noqa: BLE001
        return False


def close(hwnd: int, why: str = "") -> bool:
    """关掉**某个微信子窗**（菜单浮层 / 文件对话框 / 浮动聊天窗 / 搜索浮层）。

    先过 `ok_to_close()`：不通过就**不发这一枪**并返回 False（调用方据此如实回话）。
    ⚠️ 返回 True **只代表消息发出去了**，不代表窗已经没了 —— `WM_CLOSE` 是**异步**的
    （踩过：关完睡 0.25s 就还前台，结果前台还挂在那个正在消失的窗上）。
    要等它真消失，调用方自己轮询 `IsWindow()`（`wechat._wait_dialog_gone` 就是这个用法）。
    """
    try:
        if not ok_to_close(hwnd, why):
            return False
        import ctypes

        ctypes.windll.user32.PostMessageW(ctypes.c_void_p(int(hwnd)), WM_CLOSE, 0, 0)
        return True
    except Exception: # noqa: BLE001
        return False
