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
import threading
import time

log = logging.getLogger("persona-morph")

#: `WM_CLOSE`（关窗消息）。放成常量，免得各处再写 `0x0010` 这种魔数。
WM_CLOSE = 0x0010

# ══════════════════════════════════════════════════════════════════════════════
# 幂等关闭（P1-5）：同一个句柄被**多处**收尾时只关一次
#
# 病：`close()` 的调用点很多（切会话收尾、失败分支、超时兜底…），同一个浮层句柄
#   可能被两条路径各关一次。第二次 `WM_CLOSE` 本身通常无害，但有一个更坏的情形：
#   **窗口已经销毁、句柄值被系统回收给一个新窗** —— 这时"再关一次同一个数字"
#   会朝一个**完全无关的新窗口**发 `WM_CLOSE`（轻则误关，重则关掉不该关的东西）。
#
# 口径：关成功过的句柄**记一段时间**（`_IDEMPOTENT_TTL_S`），期间再关一律跳过并留日志。
#   · 只记"我们真的发过 WM_CLOSE 且当时窗口还在"的句柄；
#   · TTL 有界 ⇒ 句柄被回收成新窗时，过了 TTL 仍可正常关（不会永久挡死）；
#   · 只影响 `close()` 这一条入口 ⇒ 其它判据/行为不变。
# ══════════════════════════════════════════════════════════════════════════════
_IDEMPOTENT_TTL_S = 30.0
_closed_at: dict = {}
_closed_lock = threading.Lock()


def _seen_recently(h: int) -> bool:
    """这个句柄近期是不是已经被我们关过了（且还在 TTL 内）。"""
    now = time.time()
    with _closed_lock:
        t = _closed_at.get(int(h))
        if t is None:
            return False
        if now - float(t) > _IDEMPOTENT_TTL_S:
            _closed_at.pop(int(h), None) # 过期 ⇒ 允许再关（防句柄回收后永久挡死）
            return False
        return True


def _mark_closed(h: int) -> None:
    with _closed_lock:
        _closed_at[int(h)] = time.time()
        if len(_closed_at) > 512: # 有界：只留最近的一批，防长跑内存涨
            for k in sorted(_closed_at, key=lambda k: _closed_at[k])[:len(_closed_at) - 256]:
                _closed_at.pop(k, None)


def reset_idempotent() -> None:
    """清掉"已关过"的记忆（判据/收尾用）。"""
    with _closed_lock:
        _closed_at.clear()


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

    ★ P1-5 **幂等**：同一个句柄在 TTL 内被多处收尾 ⇒ 只关一次。第二次直接返回 True
      （"它已经被关过了" —— 对调用方而言结果一致），并留一行 debug 日志。
      为什么必须这样：句柄值会被系统**回收**给新窗，重复关同一串数字可能误伤无关的新窗。
    """
    try:
        h = int(hwnd or 0)
        if not h:
            return False
        if _seen_recently(h):
            log.debug("幂等关闭：hwnd=%d 近期已关过（%s）⇒ 跳过", h, why or "未注明")
            return True
        if not ok_to_close(h, why):
            return False
        import ctypes

        ctypes.windll.user32.PostMessageW(ctypes.c_void_p(h), WM_CLOSE, 0, 0)
        _mark_closed(h)
        return True
    except Exception: # noqa: BLE001
        return False
