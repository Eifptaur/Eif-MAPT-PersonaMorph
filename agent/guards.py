# -*- coding: utf-8 -*-
"""发送链的守卫 —— **唯一实现点**。

为什么要有这个文件
------------------
这一族的判据是"**一处错会不会处处错**"，实测两种形态：

1. **同一段判断被抄成几份**（改一处漏一处就是分叉，两个方向后果都不小）：
   · **真鼠标谓词**：`ui_adapt._real_mouse_allowed()` 与 `WeChatAdapter._real_fallback_allowed()`
     是**逐字相同的两份**（口径：环境变量强制关 → 看 `input.allow_real_fallback` → 异常即拒绝）。
     分叉的后果是"要么动了用户光标，要么该发的不发"。
   · **版本门**：发送链上**同一段八行**（查门 → 记账 → 早退 → 返回原因）抄了**三遍**
     （文本链 / 表情链 / 图片链），而三处的早退收尾还**不完全一样**（只有文本链做了
     "把微信放回后台"）。分叉的后果是"要么漏记账（用户在控制台看不到为什么没发），
     要么早退时把微信留在了前台"。

2. **两个不同的问题长着相似的名字**（这一条最容易改错 ⇒ 见下）。

⛔ 真鼠标有**两问**，别把合并方向搞反
-----------------------------------
  · `real_mouse()` —— "这一枪能不能用**真鼠标**"（会动光标）。
    看 `input.allow_real_fallback`；`WXAGENT_REAL_FALLBACK=0` 可**强制关**（自检/诊断路径用它兜底，无视配置）。
  · `foreground()` —— "能不能把窗口**置前/置顶**"（会压过用户用来遮挡的窗口）。
    在 `real_mouse()` 的条件之上**多一条**：`wechat.background_only` 必须为 False。

  关系是**单向**的：`foreground()` 为真 ⇒ `real_mouse()` 必为真，**反之不成立**。
  ⇒ 把两问合并成一个是**放宽**：在 `background_only=True`（默认）而 `allow_real_fallback=True` 时，
    原来问"能不能置前"会**拒**，改成问真鼠标就**放行** —— 那正是"抢用户窗口 / 放开真鼠标"。
  ⇒ 所以这两问**保持两个函数**，并用闸门钉死"前者严格强于后者"（真值表，见 `t_guard_family_guard`）。

已知的两处语义分歧（**本文件不偷偷改**，登记在计划的待改语义清单里，单独一轮）
----------------------------------------------------------------------------
  · `wechat._real_mouse_allowed()` 走的是 `foreground()`（更严），而 `real_mouse()` 更松 ⇒
    同一台机器上"能不能回真鼠标"会因为**问的是哪一问**而不同。
  · `version()` 收口的那段，原写法整块包在 `except Exception: pass` 里 ⇒
    **收尾（记账/放回后台）若抛异常，版本门的"拒发"会被吞掉、消息照发**。
    本文件**原样保留**这个口径（零语义变化），fail-closed 那一轮再单独改。

约定
----
全部 **fail-closed**：读配置失败、键缺失一律按**拒绝**处理。三个键的默认值本身就是安全的一侧
（`wechat.background_only=True` / `input.allow_real_fallback=False`）。
"""
from __future__ import annotations

import os

#: 环境变量：置 `0` 就**强制关**真鼠标兜底（自检/诊断路径用它兜底，无视配置）。
ENV_REAL_FALLBACK = "WXAGENT_REAL_FALLBACK"


def real_mouse() -> bool:
    """这一枪能不能用**真鼠标**（默认**不许**）。

    口径（两处旧实现逐字相同，本函数是它们合出来的唯一一份）：
      · `WXAGENT_REAL_FALLBACK=0` ⇒ 强制关（无视配置）；
      · 否则看 `input.allow_real_fallback`（默认 False）；
      · 读配置出任何问题 ⇒ **False**（按拒绝处理）。
    """
    try:
        if str(os.environ.get(ENV_REAL_FALLBACK, "")).strip() == "0":
            return False
    except Exception: # noqa: BLE001
        pass
    try:
        from .config import get_config

        cfg = get_config() or {}
        return bool((cfg.get("input") or {}).get("allow_real_fallback", False))
    except Exception: # noqa: BLE001
        return False


def foreground() -> tuple:
    """能不能把窗口**置前/置顶**（默认**不许**）。返回 `(ok, 原因)`。

    = `real_mouse()` 的条件 **再加一条** `wechat.background_only` 必须为 False。
    第二条判据调 `real_mouse()`（而不是再抄一遍配置读取）—— 这样真鼠标口径只有一处实现，
    以后改它，置前闸自动跟着变。

    两条判定分开写是为了**保住既有的拒绝文案**：`ui_adapt.fg_refused()` 把原因直接摆到控制台，
    用户要能看出"是哪个开关拦的"。
    """
    try:
        from .config import get_config

        cfg = get_config() or {}
    except Exception as e: # noqa: BLE001
        return False, "读配置失败（按拒绝处理）：%s" % str(e)[:40]
    if bool((cfg.get("wechat") or {}).get("background_only", True)):
        return False, "wechat.background_only=开（默认：全程后台）"
    if not real_mouse():
        return False, "input.allow_real_fallback=关（默认：不许真鼠标兜底）"
    return True, ""


def version(method: str = "send", cleanup=None) -> tuple: # noqa: ANN001
    """发送前问**版本门**：返回 `(能否发, 原因)`。

    收口的是原来抄了三遍的那八行：

        查门 → 不能发就**记账**（控制台横幅与日志都要看得见）→ 调用方自己的早退收尾 → 返回原因

    `cleanup` 是调用方自己的收尾（如"把微信放回后台"）—— 三条链原来只有一条做，**各自传各自的**，
    这里不替它们统一（统一 = 改行为）。

    ⛔ 与三处原写法**同口径**：整块包一个 try，任何异常（含 `cleanup` 抛异常）都按**放行**处理
    （原来是 `except Exception: pass` ⇒ 拒绝会被吞掉、消息照发）。属已知的"该 fail-closed 却 fail-open"，
    不在本轮改。
    """
    try:
        from . import version_gate as vg
        from .wechat import wx_version_for_gate

        g = vg.check(method, wechat=wx_version_for_gate())
        if not g["allow"]:
            vg.note_blocked(method, g["reason"])
            if cleanup is not None:
                cleanup()
            return False, g["reason"]
        return True, ""
    except Exception: # noqa: BLE001
        return True, ""
