# -*- coding: utf-8 -*-
"""wechatauto 适配层：读消息 / 发消息 / 下载图片。

对 wechatauto 的 WeChatDB / WeChatGUI / MediaDownloader 做统一封装，
把微信原始消息归一化成 Persona Morph 内部结构，屏蔽底层差异。
"""
from __future__ import annotations

import base64
import ctypes          # ⚠️ 必须是**模块级**：`_wait_dialog_gone` / `_restore_fg` / `_fg_now` / 对话框
                       #    消息驱动（WM_SETTEXT/BM_CLICK）都在模块级函数里用它。原来只有函数内局部
                       #    `import ctypes as _ct`，于是这些函数**每次都在 except 里静默失败**
                       #    （2026-09-16 实测抓到：`name 'ctypes' is not defined`）——"还前台从来没生效过"
                       #    的真因就是这一行缺失，与 Windows 的前台锁无关。
import html
import logging
import os
import random
import re
import threading
import time
from collections import deque

from .config import ROOT, get_config
from . import replica_adapter  # W1：驱动库（wechatauto-replica）私有 API 的唯一收口点 + 版本守卫
from . import recall as recall_mod  # 第 14 条：撤回事件识别（用于把已进上下文的消息剔除）


_LEDGER = deque(maxlen=200)


def message_ledger(n: int = 30) -> list:
    """最近 n 条"消息判定台账"（每条：会话/发送者/判为谁/为什么/原文片段）。"""
    try:
        return list(_LEDGER)[-max(1, int(n)):]
    except Exception:
        return []


def _self_local_note(obj, chat_id, local_id, create_time=None) -> None:
    """登记「这条库行是我发的」——**模块级安全入口**：对象没这能力/记账出错都不影响发送。

    为什么要这层：登记只是记账（监听侧判自己用），而三个调用点都在**发送成功链**里 ——
    记账出意外（假适配器没有这个方法、盘满、权限）绝不能把一次已经发成功的消息判成失败
    （`send_loop_behavior_selftest` 的假适配器当场暴露过这一点）。
    """
    try:
        fn = getattr(obj, "remember_self_local", None)
        if callable(fn):
            fn(chat_id, local_id, create_time)
    except Exception:
        pass


def _mask_id(s: str) -> str:
    """掩码显示 wxid（日志与控制台都不回显完整账号）。"""
    t = str(s or "")
    if len(t) <= 8:
        return t[:2] + "***"
    return t[:4] + "***" + t[-3:]

# 模块级 logger（2026-09-13 修）：本文件里多处 `log.info(...)` 一直没定义 `log` ⇒
# 只要走到"投递前置不满足 / 学会话头"这些分支就抛 NameError，被外层 except 吞掉后表现为
# **整条发送静默失败（send_text 返回 False 且什么都没发）**。教训：只测"绿灯路"会漏掉日志分支。
log = logging.getLogger("persona-morph")

# 调试开关：WX_DEBUG=1 时输出分步计时/调参日志（平时完全静默，不写本地草稿目录）
_DEBUG = str(os.environ.get("WX_DEBUG") or "").strip() in ("1", "true", "yes")

# 微信消息类型标签 → 内部占位文本
TYPE_LABEL = {    "文本": "text",
    "图片": "image",
    "动画表情": "emoji",
    "语音": "voice",
    "视频": "video",
    "位置": "location",
    "文件/链接/卡片": "file",
    "红包": "redpacket",
    "系统消息": "system",
}


_FG_STASH = {"hwnd": 0, "at": 0.0}          # 打开对话框**之前**那一个前台窗口（见 `_stash_fg`）


def _fg_now() -> int:
    """当前前台窗口句柄（**优先 win32gui**）。

    ⚠️ 2026-09-16 实测踩坑：`ctypes.windll.user32.GetForegroundWindow()` 在没声明 `restype` 时
    返回的是被当成 32 位 int 处理的句柄，实测**同一进程同一瞬间**它给 0、而 win32gui 给 134730
    ⇒ `_stash_fg()` 记了个 0，"还前台"自然无从谈起。⇒ 统一走 win32gui，ctypes 只当兜底。
    """
    try:
        import win32gui
        return int(win32gui.GetForegroundWindow() or 0)
    except Exception:
        try:
            u = ctypes.windll.user32
            u.GetForegroundWindow.restype = ctypes.c_void_p
            return int(u.GetForegroundWindow() or 0)
        except Exception:
            return 0


def _stash_fg() -> int:
    """在**点 📁 打开对话框之前**记下当时的前台窗口。

    ⚠️ 为什么不能在"关之前"才记（2026-09-16 第二次实测才看明白）：对话框有时**自己会抢前台**，
    于是"关之前的前台"就是**那个对话框**本身 ⇒ 关掉之后那个 hwnd 已经死了，还原等于没还
    （实测：还原后前台落在微信主窗上）。必须在**打开之前**把用户当时的前台记下来。
    """
    h = _fg_now()
    _FG_STASH.update({"hwnd": h, "at": time.time()})
    return h


def _fg_before_close() -> int:
    """（兜底用）当前前台窗口；`_stash_fg()` 没被调用过时的退路。"""
    return _fg_now()


def _wait_dialog_gone(hwnd: int, timeout: float = 1.5) -> bool:
    """等某个对话框**真的消失**（最多 `timeout` 秒）；返回是否真的没了。

    ⚠️ 为什么（2026-09-16 实测）：`PostMessage(WM_CLOSE)` 是**异步**的。原来关完睡 0.25s 就还前台，
    可那时框往往还没死、Windows 随后又把它的 owner（微信）激活 ⇒ 我们那一枪白还（实测两次都这样）。
    ⚠️ 返回值（2026-09-16 加）：调用方要靠它决定"框没了就立刻还前台"还是"先补一枪回车再还"。
    """
    try:
        u = ctypes.windll.user32
        dl = time.time() + max(0.1, float(timeout))
        while time.time() < dl:
            if not u.IsWindow(ctypes.c_void_p(int(hwnd))):
                return True
            time.sleep(0.05)
        return not bool(u.IsWindow(ctypes.c_void_p(int(hwnd))))
    except Exception:
        return False



def _control_halt() -> str:
    """机器人被**暂停/停止**时，长链的每一步都该**立刻停手**（2026-09-18 加）。

    为什么需要它：作者现场「**说机器已暂停的那一刻，后面一秒他又引用了一下我的消息**」——
    日志实证 `机器人已暂停` 之后 **54 秒**它仍跑完一整轮（引用→菜单→回车→退普通发送）。真因＝
    暂停原来只在内存里（`orch.paused`），`wechat` 侧读不到 ⇒ 已开工的链中途不检查。现在读文件标记
    （`agent/control.py`，由 `orch.set_paused()` 与脚本接口共同维护）。返回空串＝可以继续。
    """
    try:
        from . import control as _ctl
        return _ctl.halt_reason()
    except Exception:
        return ""


# ── 输入框几何：**一律现算 + 只取上沿**（2026-09-18 作者现场口径）──────────────────
# 作者原话：「**输入栏不是固定大小的。当你引用一条比较长的信息时，输入栏会变高**。那么此刻，
# 如果你还是按原来输入栏的位置去点的话，中间点有可能正好就是引用的那条消息的尾部。这样就导致
# 你输入不了，就一直在点那条引用，或者你偶然间点到了那个叉号，就把引用点掉」「引用条事实上正处于
# **工具栏的上面，而不是输入栏的上面**……最好是点**输入孔上沿**，这样会比较保险，**因为上面没有
# 什么东西**」。
#
# 实测（本机 1193×891，`_scratch/probe_rows.py` 在**窗口自身画面**上逐行量）：
#   消息区…y 400..690 ｜ y 691＝1px 浅灰分界线 ｜ **输入框 y 692..827（高 136）** ｜ 工具栏灰带 828..851
#   ⇒ 输入框**下部**（比例 ≈0.87，屏幕 y≈931）正是引用条所在的那一带 —— 老实现那一枪就点在那里。
#
# ⚠️ 不用驱动库那两件的原因（都实测过）：
#   · `gui._probe_input_box()`（诚实版）要求框高 **≥150px**（`guia.py:1444`），本机只有 136px
#     ⇒ **在这台机器上永远探不到**（探不到时 `get_input_box()` 会**静默返回按比例猜的矩形**
#     `guia.py:1396-1399`，调用方分不出"量的"还是"猜的"）。
#   ⇒ 本实现只认画面证据，量不到就返回 None，调用方**不许猜**。


def _probe_input_box_frame(gui):
    """从**窗口自身画面**（PrintWindow 优先，被别的窗口盖住也能量）量输入框矩形（渲染相对）。

    做法与库里同款（底部找"全宽近白"行 → 沿中心列上下扩到边界 → 顶上 1~4px 浅灰分界线佐证），
    只去掉那条把本机排除掉的门槛（≥150px）。量不到返回 None。
    """
    try:
        from . import chat_header as _ch
        img = _ch.capture_image(gui=gui)
    except Exception:
        img = None
    if img is None:
        return None
    try:
        px = img.load()
        W, H = img.size
        x0 = int(getattr(gui, "right_pane_left", 0) or 0)
        if x0 <= 0 or x0 > W * 0.6:
            x0 = int(W * 0.22)
        cx = min(W - 1, (x0 + W) // 2)
        for off in (150, 120, 200, 100, 250, 90, 300):
            y = int(H) - off
            if y <= H * 0.45 or y >= H - 2:
                continue
            tot = white = 0
            for x in range(x0, W, 2):
                r, g, b = px[x, y][:3]
                tot += 1
                if min(r, g, b) >= 241:
                    white += 1
            if tot <= 0 or white / tot < 0.8:
                continue                      # 这一行不在输入框里（消息区/工具栏/状态条）
            top = y
            while top > 1 and min(px[cx, top - 1][:3]) >= 241:
                top -= 1
            bot = y
            while bot < H - 2 and min(px[cx, bot + 1][:3]) >= 241:
                bot += 1
            if bot - top < 60:                # 太薄：不像输入框
                continue
            d, g2 = 0, top - 1                # 顶上应是 1~4px 的浅灰分界线（佐证）
            while g2 >= 0 and min(px[cx, g2][:3]) < 241:
                d += 1
                g2 -= 1
            if not (1 <= d <= 6):
                continue
            return (x0, top, W, bot)
    except Exception:
        return None
    return None


def _input_top_band(gui, band_px: int = 20):
    """返回 `(上半部分矩形(渲染相对), 落点(屏幕))`；量不到 ⇒ `(None, None)`，调用方**不许猜点**。

    ⚠️ 作者 2026-09-18 澄清：「**我的意思是上半部分**」——不是贴着上边的一条线。所以：
      · 落点 = 框顶往下 **1/4 高度**处（＝上半部分的中线偏上，离上面的分界线和下面的引用条都远）；
      · 带 = 框的**上半部分**（给阳性对照取样用，条带高一些才能稳定吃到第一行文字）。
    """
    box = _probe_input_box_frame(gui)
    if not box:
        return None, None
    x0, top, x1, bot = box
    h = max(1, bot - top)
    half = max(8, h // 2)
    band = (x0, top, x1, top + half)
    dy = max(8, min(int(h * 0.25), max(8, half - 4)))     # 上半部分的中线偏上
    pt = (int(getattr(gui, "origin_x", 0) or 0) + (band[0] + band[2]) // 2,
          int(getattr(gui, "origin_y", 0) or 0) + top + dy)
    return band, pt


def _input_ink(gui, box, strip: int = 80) -> int:
    """输入框**上沿条带**里的深色点数 —— 阳性对照用（字到底进没进框）。拿不到帧返回 -1。

    空输入框只有浅灰占位符（min(RGB)≈200），所以阈值取 <150 不会把占位符算进去。
    """
    try:
        from . import chat_header as _ch
        img = _ch.capture_image(gui=gui)
        if img is None:
            return -1
        x0, top, x1, bot = box
        sub = img.crop((x0 + 12, top + 2, max(x0 + 14, x1 - 12), min(bot, top + strip)))
        px = sub.load()
        return sum(1 for y in range(0, sub.size[1], 2) for x in range(0, sub.size[0], 2)
                   if min(px[x, y][:3]) < 150)
    except Exception:
        return -1


def _restore_fg(hwnd: int = 0, note: str = "", keep: bool = False) -> None:
    """把前台还回"**打开对话框之前**那一个"（优先级：传入的 hwnd → `_FG_STASH` → 当前前台）。

    **为什么必须有这一步**：用户原话——「你老是把微信切到前台，然后发文件，这不能后台做吗…那个发
    文件框本身也可以被放在后台的，它不是锁定前台的」。第一次量下来（2026-09-16 探针）：
      ① 投递点 📁（档位 `message`，`touches_cursor=False`）→ 前台**没变** ✅；
      ② 「选择文件」`#32770` 弹出时——**有时前台不变、有时它自己就跳到前台**（2026-09-16 跨机 r15/r16 实测：伪激活会让它短暂置前约 0.6~3 秒，之后由 `_restore_fg_until` 还回）（前台仍是用户那个窗口，和他的观察一致），
         **有时它自己就成前台**（两次实测各见一次，行为不稳定）；
      ③ **关掉对话框之后**前台会落到微信主窗 ✗ —— 当时以为"微信被切到前台"的主因是这一步。

    ⚠️ **用户 2026-09-16 当面看屏幕定位到了真正抢前台的那一步**（原话：「点击『文件』按钮没有到前台，
    打开文件窗口也没有到前台，但是**当你粘贴输入那一串字符的时候，它到前台了**。看来你只需要让它
    粘贴完，**立马缩回后台**就行」）⇒ 处置从"关完再还"扩成"**写完文件名就立刻还一次**"（`keep=True`，
    保留 stash 给后面几步用）＋ 关完再还一次（清 stash）。「打开」走 UIA `Invoke`，后台即可，不需要前台。

    `keep=True`（2026-09-16 加）＝**不只还前台、还留着 stash**：一笔发文件要走"写名字 → 点打开 →
    等框关"三段，每段都可能把前台抢走，所以这段流程里每一处都还得还一次；只有最后那一次清 stash。
    """
    try:
        u = ctypes.windll.user32
        h = int(hwnd or _FG_STASH.get("hwnd") or 0) or _fg_before_close()
        if not h or not u.IsWindow(ctypes.c_void_p(h)):
            return
        try:
            import win32gui
            if win32gui.GetClassName(int(h)) == "#32770":       # 不把前台还给对话框
                return
        except Exception:
            pass
        cur = _fg_now()
        if cur == h:
            return
        # ⚠️ **后台进程直接调 `SetForegroundWindow` 会被系统忽略**（实测：关掉对话框后前台落在
        #    微信主窗上，还回去那一枪返回 False、前台没变）⇒ 正规绕法：先把本线程的输入队列
        #    挂到"当前前台窗口所在线程"上，再 SetForegroundWindow，最后解挂。
        ok_ret = False
        try:
            k32 = ctypes.windll.kernel32
            cur_tid = int(u.GetWindowThreadProcessId(ctypes.c_void_p(cur), None) or 0)
            our_tid = int(k32.GetCurrentThreadId())
            attached = False
            if cur_tid and our_tid and cur_tid != our_tid:
                attached = bool(u.AttachThreadInput(our_tid, cur_tid, True))
            ok_ret = bool(u.SetForegroundWindow(ctypes.c_void_p(h)))
            if attached:
                u.AttachThreadInput(our_tid, cur_tid, False)
        except Exception:
            pass
        log.info("还前台（%s）：%s → %s（结果=%s，AttachThreadInput 绕法）",
                 note or "未注明", cur, h, ok_ret)
    except Exception:
        pass
    finally:
        if not keep:                      # ⚠️ keep=True ⇒ 留着 stash（这一笔还没走完，后面几步还要用）
            try:
                _FG_STASH.update({"hwnd": 0, "at": 0.0})
            except Exception:
                pass


# —— 最小化状态还原（2026-09-16 r22 验收 FAIL 项）——
# 现象（对面那台机器实测）：微信收在任务栏时，我们**不激活地**把它还原出来干活
# （`_ensure_main_visible`），但干完只还了前台、**没把它放回收起状态** ⇒ 用户的微信
# 从"收在任务栏"变成"摊在桌面上"，还得自己再收一次。既有口径：是「不打扰用户」⇒
# **谁动的谁收拾**：还原过就必须放回。
_MINIMIZED_BY_US = 0        # 为了干活而还原出来的那个主窗（0 = 本轮没动过它的收起状态）
# ⚡ 2026-09-18 晚（网友反馈：「游戏无论全不全屏，只要把它最小化后，它要发消息时都会被激活到最上面」）：
#   **用户自己收起过的窗，链尾要收回原位**。与作者 2026-09-18「不要最小化，就置于底层」不冲突——
#   那条说的是**链中间不许一收一放**（现场「他还在不停地缩小…又把微信切出来」）；这里是**链尾一次**
#   把"我们为了抓图而还原出来"的窗恢复成它原来的状态（用户收着的，就还他收着）。
_WAS_ICONIC_BY_US = 0


def _minimize_back_if_needed(note: str = "") -> None:
    """把"为干活还原出来的"主窗**压回 Z 序底层**（⚠️ 不再最小化，作者 2026-09-18 定；三条安全线都不许少）。

    ①**没登记过就不动**（用户本来就没最小化，我们没资格改它的状态）；
    ②**它已经是最小化了就不动**（用户自己收的，别再补一枪）；
    ③**它现在是前台就不动**（用户正在用它干活 —— 这时候去最小化就是抢用户的窗口）。
    """
    global _MINIMIZED_BY_US
    hwnd = int(_MINIMIZED_BY_US or 0)
    if not hwnd:
        return
    _MINIMIZED_BY_US = 0
    _was_iconic = int(_WAS_ICONIC_BY_US or 0) == hwnd
    globals()["_WAS_ICONIC_BY_US"] = 0
    try:
        import ctypes as _ct
        u = _ct.windll.user32
        if not u.IsWindow(hwnd):
            return
        if u.IsIconic(hwnd):
            return
        if int(u.GetForegroundWindow() or 0) == hwnd:
            return
        if _was_iconic:
            # ⚡ 2026-09-18 晚：**它是用户自己收起来的** ⇒ 链尾还他收着（非前台窗口最小化不会激活别人）。
            #   为什么必须还（网友反馈原文）：「游戏无论全不全屏，只要把它最小化后，它要发消息时都会被
            #   激活到最上面」——我们为抓图还原出来，干完却不收回去，用户屏幕上就多出一个微信窗。
            u.ShowWindow(_ct.c_void_p(hwnd), 6)               # SW_MINIMIZE（它本来就不是前台，不会激活谁）
            time.sleep(0.15)
            log.info("收回原位（%s）：微信主窗是**用户自己收起来的** ⇒ 恢复成最小化", note or "未注明")
            return
        # 🔴 2026-09-18 改（作者原话：「**为什么非要最小化呢？不要最小化呀，就置于底层**」）：
        #   以前这里 `ShowWindow(hwnd, 6)` ＝ SW_MINIMIZE，把"为干活还原出来的"主窗**重新最小化**。
        #   现场后果：用户没开「最小化时自己还原」时，窗口被收进任务栏、后续整条链没反应；
        #   而且"缩下去又弹出来"本身就是打扰。⇒ 现在只把它**压到 Z 序底层**：
        #   `HWND_BOTTOM` + `SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE` —— 不改可见性、不激活、不动几何。
        _ct.windll.user32.SetWindowPos(_ct.c_void_p(hwnd), _ct.c_void_p(1), 0, 0, 0, 0,
                                       0x0002 | 0x0001 | 0x0010)
        log.info("置于底层（%s）：微信主窗压回 Z 序底层（不最小化；那是我们为干活还原出来的）",
                 note or "未注明")
    except Exception as e:
        log.warning("放回最小化失败：%s", e)


def _user_idle_seconds() -> float:
    """**距用户最后一次真实输入（键盘/鼠标）过了多少秒**（用 `GetLastInputInfo`）。

    用途（2026-09-18）：`_restore_fg_until` 抢回前台之前先问一句——最近有输入就说明**用户自己在
    操作**（他点了微信/别的窗口出来），此时我们**不抢**，让用户做主；否则才按原口径把前台还回去。
    拿不到时返回一个很大的值（＝"当成用户没在动"，行为退回原口径，不会因此少还）。
    """
    try:
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return 1e9
        tick = ctypes.windll.kernel32.GetTickCount()
        return max(0.0, (tick - int(lii.dwTime)) / 1000.0)
    except Exception:
        return 1e9


def _restore_fg_until(note: str = "", timeout: float = 2.5, keep: bool = True,
                      gap: float = 0.18) -> bool:
    """在 `timeout` 秒内**反复**把前台还回 stash 那一个，直到真的回到它为止。

    ⚠️ 为什么不能只还一枪（2026-09-16 实测时间线）：对话框的激活是**异步**的——`SetValue` 写文件名的
    那一刻前台还没变，**约 0.3~0.5 秒之后**那个 `#32770` 才跳到前台（我们那条 0.1s 采样的时间线
    10.39s 才看到它）。⇒ "写完立刻还"那一枪必然打空（实测日志里连一条"还前台"都没有：函数的
    `cur == h` 提前返回了）。用户口径是「**粘贴完，立马缩回后台**」⇒ 这里改成**盯着还**：
    只要前台不是 stash 那一个就再还一次，回到它就停（最多 `timeout` 秒）。
    """
    h = int(_FG_STASH.get("hwnd") or 0)
    if not h:
        return False
    # 🔴 2026-09-18 加闸（用户现场：「**不是你刚刚把窗口收起了，我把窗口点出来了**」）：
    #   这条链会 **主动 `SetForegroundWindow` 去抢回**"进入时记下的那个窗口"。如果**用户在中途自己
    #   点了微信（或别的窗口）出来**，我们这一枪就会把他刚点出来的窗口压回去 —— 那是"不打扰用户"的红线。
    #   ⇒ 还之前先问一句：**最近 1.2 秒内有真实键盘/鼠标输入吗？**（`GetLastInputInfo`）
    #      有 ⇒ 是用户自己在操作，**这一轮不抢**（让人做主）；没有 ⇒ 才按原口径还。
    try:
        if _user_idle_seconds() < 1.2:
            log.info("还前台跳过（%s）：最近 %.2fs 内有用户输入 ⇒ 不抢用户刚切过去的窗口",
                     note or "未注明", _user_idle_seconds())
            return False
    except Exception:
        pass
    deadline = time.time() + max(0.2, float(timeout))
    tries = 0
    while time.time() < deadline:
        if int(_fg_now() or 0) == h:
            break
        tries += 1
        _restore_fg(h, note, keep=keep)
        time.sleep(gap)
    ok = (int(_fg_now() or 0) == h)
    log.info("还前台收尾（%s）：试了 %d 次，最终前台=%s %s",
             note or "未注明", tries, _fg_now(), "✅ 已回到用户窗口" if ok else "✗ 没能回到")
    # ⛔ 这里**不再**顺手 `_minimize_back_if_needed`（2026-09-18 挪走）：
    #   现场现象「他还在不停地缩小，就是把微信最小化，然后又把微信切出来」。
    #   根因：`_restore_fg_until` 在**一条发送链里会被调很多次**（切会话·搜索路线 / 投递发送后 /
    #   写完文件名 / 对话框关闭后 / 补回车后 …），而放回收起状态**只该在整条链收尾时做一次**。
    #   以前每次都放回 ⇒ 链中间那一次就把微信收进任务栏，下一个动作又要 `_ensure_main_visible`
    #   把它还原出来 ⇒ 一收一放，用户看到的就是"微信在抽风"。
    #   ⇒ 放回动作**只在链收尾**两处调用：`_minimize_back_if_needed("投递文本链收尾")`
    #     与 `_minimize_back_if_needed("投递文件链收尾")`（`scripts/background_selftest.py` F 段看守）。
    return ok


# —— 「选择文件」对话框：**不进前台**地写文件名 / 点打开（2026-09-16 用户当面定位后加）——
# 已知现象：「点击『文件』按钮没有到前台，打开文件窗口也没有到前台，但是**当你粘贴输入那一串字符的
# 时候，它到前台了**。看来你只需要让它**粘贴完，立马缩回后台**就行」。
# ⚠️ 但"还回去"这条路**走不通**：后台进程调 `SetForegroundWindow` 会被系统直接拒（2026-09-16 实测
#    连 AttachThreadInput 绕法也 False，3 秒盯着还也没用）⇒ 正解是**根本不让它到前台**：
#    文件对话框的文件名框是标准 Edit（控件 id 1148）、「打开」是标准按钮（id 1=IDOK），
#    `WM_SETTEXT` / `BM_CLICK` 都是**消息**，投递即可，不改变前台、不抢焦点。
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
BM_CLICK = 0x00F5
DLG_ID_FILENAME = 1148      # Vista+ 通用「打开/选择文件」对话框的文件名编辑框
DLG_ID_OK = 1               # 「打开」按钮（IDOK）


def _dlg_child(hwnd: int, cid: int) -> int:
    """对话框里某个控件 id 的子窗口句柄（拿不到给 0）。"""
    try:
        u = ctypes.windll.user32
        u.GetDlgItem.restype = ctypes.c_void_p
        return int(u.GetDlgItem(ctypes.c_void_p(int(hwnd)), int(cid)) or 0)
    except Exception:
        return 0


def _fill_dialog_name(hwnd: int, path: str) -> tuple:
    """把路径**投递**进文件对话框的文件名框（`WM_SETTEXT`）并回读确认。返回 `(ok, 说明)`。

    不做的事：不 `SetFocus`、不 `SetForegroundWindow`、不动光标、不发键击 —— 所以**前台不会变**。
    控件 id 不是 1148（老版本 / 别的实现）或写不进去时返回 False，由调用方退回 UIA 老路。
    """
    try:
        u = ctypes.windll.user32
        e = _dlg_child(int(hwnd), DLG_ID_FILENAME)
        if not e:
            return False, "文件名框（id=%d）没找到" % DLG_ID_FILENAME
        want = str(path)
        u.SendMessageW.restype = ctypes.c_void_p
        u.SendMessageW(ctypes.c_void_p(e), WM_SETTEXT, 0, ctypes.c_wchar_p(want))
        buf = ctypes.create_unicode_buffer(len(want) + 8)
        u.SendMessageW(ctypes.c_void_p(e), WM_GETTEXT, len(buf), buf)
        got = str(buf.value or "")
        if got.strip().lower() != want.strip().lower():
            return False, "写进去回读不一致（读到 %r）" % got[:60]
        return True, "WM_SETTEXT 写文件名（不进前台）"
    except Exception as e:
        return False, "WM_SETTEXT 写文件名异常：%s" % e


def _click_dialog_open(hwnd: int) -> tuple:
    """**投递**点文件对话框的「打开」（`BM_CLICK`，id=1）。返回 `(ok, 说明)`；同样不进前台。"""
    try:
        u = ctypes.windll.user32
        b = _dlg_child(int(hwnd), DLG_ID_OK)
        if not b:
            return False, "「打开」按钮（id=%d）没找到" % DLG_ID_OK
        u.SendMessageW.restype = ctypes.c_void_p
        u.SendMessageW(ctypes.c_void_p(b), BM_CLICK, 0, 0)
        return True, "BM_CLICK 点「打开」（不进前台）"
    except Exception as e:
        return False, "BM_CLICK 点「打开」异常：%s" % e


def _wm_close_safe(hwnd: int, why: str = "") -> bool:
    """投递 `WM_CLOSE` 前的**唯一咽喉点**：**绝不关微信主窗 / 渲染子窗**。

    ⛔ 为什么必须有它（**同型事故第二次**，2026-09-18 作者原话：
      「直接把它收回任务栏了…应该算是那种直接点击『叉号』级别的收回…就在你右键点击到我头像的那一刻的下一刻」）：
      · 2026-09-16 已经出过一次：「主窗被 Qt 重建、hwnd 变了 ⇒ 被当成菜单窗 WM_CLOSE 掉
        ⇒ 用户的微信主窗口整个消失」；
      · 本轮嫌疑最大的是 `_reattach_if_floating()`：它靠"标题含会话名 + 类名 Qt 开头"挑窗、
        只跳过**当时记下的** `main` 句柄 ⇒ **主窗一被 Qt 重建，新 hwnd 就不在白名单里**，
        于是主窗被当"浮动聊天窗"关掉（正是"叉号级别收进任务栏"）。
    ⇒ 规矩：任何 `WM_CLOSE` 之前**当场重新取一次主窗与渲染子窗**（`find_main_window()` 不看可见性），
      命中就**拒绝并留日志**——宁可留一个浮层在屏幕上，也绝不许关用户的窗。
    """
    try:
        h = int(hwnd or 0)
        if not h:
            return False
        try:
            import win32gui
            if not win32gui.IsWindow(h):
                return False
        except Exception:
            pass
        main = 0
        render = 0
        try:
            from . import input_backend as _ib
            main = int(_ib.find_main_window() or 0)
            if main:
                try:
                    render = int(_ib.find_render_child(main) or 0)
                except Exception:
                    render = 0
        except Exception:
            pass
        if h in (main, render):
            log.warning("⛔ 拒绝 WM_CLOSE：目标是**微信主窗/渲染子窗**（%s，hwnd=%d，main=%d）"
                        "—— 宁可留浮层，绝不关用户的窗", why or "未注明", h, main)
            return False
        return True
    except Exception:
        return False


# ⚡ 2026-09-18 晚（现场图取证，作者原话「你怎么点出来个搜索聊天记录啊」）：微信 4.x 的
#   「搜索聊天记录」是**独立顶层窗**（带标题栏、标题就是「搜索聊天记录」），**不是**
#   `Qt51514QWindowToolSaveBits` 那种无边框浮层 ⇒ 旧口径（只认类名）既**找不到它**（于是又去点
#   搜索入口、还把浮层留在屏幕上），也**关不掉它**。⇒ 判据补一条**按窗口标题**（最稳，主窗标题里
#   没有「搜索」二字，不会误伤）。
SEARCH_WINDOW_TITLE_KEYS = ("搜索聊天记录", "搜索", "Search")


def is_search_window(cls: str, title: str) -> bool:
    """窗口是不是「搜索类窗口」（纯函数，可单测）：无边框浮层（ToolSave）或独立搜索窗（按标题）。"""
    cls = str(cls or "")
    title = str(title or "")
    if "ToolSave" in cls:                       # 微信自己的无边框浮层：搜索浮层 / 表情面板
        return True
    if cls.startswith("Qt") and any(k in title for k in SEARCH_WINDOW_TITLE_KEYS):
        return True
    return False


def _close_search_popover(hwnd: int) -> bool:
    """投递 `WM_CLOSE` 关掉搜索浮层（对面 r12 实测：一枪就关，关掉后前台自动回微信主窗）。

    ⚠️ 为什么必须自己关（2026-09-16 跨机 r12 报的）：搜索浮层**失败后留在屏幕上**——既挡屏又**占着前台**
    （对面那次复现的起点就是这个残留浮层）。⇒ 失败分支自己收尾，别把浮层留给用户。
    """
    try:
        u = ctypes.windll.user32
        if not hwnd or not u.IsWindow(ctypes.c_void_p(int(hwnd))):
            return False
        u.PostMessageW.restype = ctypes.c_void_p
        if not _wm_close_safe(hwnd, "关搜索浮层"):
            return False
        u.PostMessageW(ctypes.c_void_p(int(hwnd)), 0x0010, 0, 0)      # WM_CLOSE
        return True
    except Exception:
        return False


def _close_file_dialog(hwnd: int) -> None:
    """关掉系统「选择文件」对话框（`#32770`）——异常路径的兜底，**绝不留模态框在用户屏幕上**。"""
    try:
        import win32con
        import win32gui
        if hwnd and win32gui.IsWindow(int(hwnd)) and _wm_close_safe(hwnd, "关文件对话框"):
            win32gui.PostMessage(int(hwnd), win32con.WM_CLOSE, 0, 0)
            _wait_dialog_gone(int(hwnd))      # ⚠️ 必须等它**真消失**再还前台（见 _wait_dialog_gone）
            _restore_fg(0, "单框")
            time.sleep(0.35)
            _restore_fg(0, "单框·二次")        # 兜第二枪：有时框死了还会再激活一次 owner
    except Exception:
        pass


def _close_stale_file_dialogs() -> int:
    """清掉**残留**的「选择文件」对话框（上一次异常留在屏幕上的），返回关掉几个。

    为什么要在发文件之前先做这一步：那个模态框会挡住微信的输入区，之后的发送/切会话全部无效，
    而且看起来像"发文件功能坏了"（2026-09-13 实测踩到：UIA 抛错后框留在屏幕上）。
    """
    n = 0
    try:
        import win32con
        import win32gui
        hits = []

        def _cb(h, _):
            try:
                if win32gui.GetClassName(h) == "#32770" and win32gui.IsWindowVisible(h):
                    t = win32gui.GetWindowText(h)
                    if "选择文件" in t or "打开" in t:
                        hits.append(h)
            except Exception:
                pass

        win32gui.EnumWindows(_cb, None)
        for h in hits:
            if not _wm_close_safe(h, "清残留文件对话框"):
                continue
            win32gui.PostMessage(int(h), win32con.WM_CLOSE, 0, 0)
            n += 1
        if n:
            for _h in hits:
                _wait_dialog_gone(_h)
            _restore_fg(0, "批量 %d 个" % n)
            time.sleep(0.35)
            _restore_fg(0, "批量·二次")
    except Exception:
        pass
    return n


def wx_version_for_gate() -> str:
    """给**版本门**用的当前微信版本号（读不到就返回空串 ⇒ 门自己按"未验证"处理）。

    ⚠️ 为什么要有这个函数（2026-09-13 实测 bug）：三处发送入口调 `version_gate.check("send")` 时
    **都没把版本传进去**，而 `check()` 内部是 `w = wechat or "unknown"` ⇒ 门**永远**回
    "读不到微信版本（微信没在跑？）"、**每一次自动发送都被拦**（微信明明在跑）。
    """
    try:
        return str((wechat_version_info() or {}).get("version") or "")
    except Exception:
        return ""


def _resp_msg(r) -> str:
    """从驱动库的返回值里取"人话原因"。

    ⚠️ `WxResponse` 是 **dict 子类**（`{'status','message','data'}`），不是普通对象：
    用 `getattr(r, "message", "")` 永远拿到空串 ⇒ 失败原因全丢（2026-09-13 实测：
    "发送失败"时我们报给上层的原因一直是空的，白查了一轮）。
    """
    try:
        if isinstance(r, dict) and r.get("message"):
            return str(r.get("message"))
        return str(getattr(r, "message", "") or "")
    except Exception:
        return ""


def _force_foreground(user32, hwnd: int) -> bool:
    """把指定窗口带到前台（AttachThreadInput 提权，绕开 Windows 前台锁）。

    仅靠 SetForegroundWindow 常被系统拒绝（正是"时灵时不灵"的原因之一）：
    当前台属于其他进程时，调用方进程不能直接抢前台。先 AttachThreadInput
    把「前台线程」与「目标窗口线程」接上再设置即可成功；结束后解绑。
    """
    try:
        import ctypes as _ct
        fg = int(user32.GetForegroundWindow() or 0)
        tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        tid_target = user32.GetWindowThreadProcessId(hwnd, None)
        if tid_fg and tid_target and tid_fg != tid_target:
            user32.AttachThreadInput(tid_fg, tid_target, True)
        try:
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
            user32.BringWindowToTop(hwnd)
        finally:
            if tid_fg and tid_target and tid_fg != tid_target:
                user32.AttachThreadInput(tid_fg, tid_target, False)
        return bool(user32.GetForegroundWindow() == hwnd)
    except Exception:
        return False

_SENDER_RE = re.compile(r"^(wxid_[0-9a-zA-Z_-]+|.*@chatroom):\s*")


# 认人（拍一拍/引用定位）时**绝不许拿来当锚点**的"系统提示"行——它们**居中、没有头像**，
# 拿它们配头像方块必然配错（2026-09-18 现场实测：`「E」拍拍「群deepseek」` 与锚点
# `群deepseek说话！` 的模糊相似度 0.593 > 0.5 阈值 ⇒ 被当成"E 的消息行"，
# 结果离最近的头像方块 105px、被 90px 闸拦下 ⇒ 定位失败。闸救了一次，但根因要在这儿堵）。
_SYS_NOTICE_JUNK = ("拍拍", "拍一拍", "撤回了一条消息", "撤回", "加入了", "邀请",
                    "退出了", "以上是", "开启了朋友验证", "你已添加", "领取了", "修改群名")


def poke_text_is_mine(txt: str, target: str = "", my_names=None) -> bool:
    """这条"拍拍"文案是不是**我发起**的？（方向判据，`_verify_poke` 用）

    2026-09-18 真机取证 + 作者口径（原话：「可以写，如果有"我拍拍"这个部分的，就可以算是自己拍的，
    **因为有些人可能自定义拍一拍信息**」）：
      · 我发起（DB 原文）：`我拍拍「E」`（**没有「了」**）· 界面文案：`你拍了拍…`
      · 别人拍我（DB 原文）：`「E」拍拍「群deepseek」`
    ⇒ **方向词优先**：`我拍拍` / `我拍了拍` / `你拍了拍` ⇒ 是我。
    ⇒ 拿不准时才退回"**主语是我**"这一条（我的名字出现在**前半段**）——因为拍一拍文案
      **可被用户自定义**（"拍了拍我的腹肌"之类），不能靠"拍了拍"这个固定串去认。
    """
    t = str(txt or "")
    if not t:
        return False
    if "我拍拍" in t or "我拍了拍" in t or "你拍了拍" in t:
        return True
    # 拿不准时**认主语**：文案结构是「<主语> 拍了拍 <对象>[自定义后缀]」
    # ⇒ 取**第一个「拍」字之前**那一段当主语区，只有主语是"我"才算我拍的。
    #   ⛔ 别用"我的名字出现在前半段"这种松判据——实测（本文件自带的 8 个用例）它会把
    #     `「E」拍拍「群deepseek」`（**别人拍我**）判成"我拍的" ⇒ **假成功**。
    _i = t.find("拍")
    _subj = t[:_i] if _i > 0 else t
    if my_names and any(nm and (nm in _subj) for nm in my_names):
        return True
    return False


def poke_event_is_ours(nm: dict, self_wxid: str) -> bool:
    """这条归一化后的 `[拍一拍]` 事件，是不是**我们自己拍出去**的回执（回声）？

    ⛔ 为什么要有它（2026-09-18 现场，作者抓的：「他回了一句『谁拍我』，这证明你"拍一拍"那一段
      判断自己的逻辑写的有问题」）：
      · 解析侧（`wechat.py` 的 appmsg type=62）：
          别人拍我：title=`「E」拍拍「群deepseek」` ⇒ 正则 `「([^」]+)」拍拍` 抠出 poker ⇒ 文本 `[拍一拍]（E）`
          **我拍别人**：title=`我拍拍「E」`        ⇒ **抠不出名字** ⇒ 文本光秃秃 `[拍一拍]`
      · 而监听分支里**任何 `[拍一拍]` 都会 `orch.on_incoming()`** ⇒ **我们自己的回拍回执被当成
        "别人拍我"喂给了模型** ⇒ 模型看不到主语，只能反问「谁拍我」。
      · 原来那道"自己拍的"判断只写在 `_schedule_poke_back` 里（只防回拍），**没拦"喂模型"**。
    ⇒ 判据（两条，任一成立即"是我们的"）：① `poker_wxid` 就是自己；② **名字抠不出**（＝"我拍别人"的形态；
      而"别人拍我"即使文案被自定义过，`patinfo.fromusername` 仍有值）。
    🔴 2026-09-18 二修（录制现场第二次漏，作者当场问"那个 3 不会是人吧"）：**②不能再要求 wxid 也抠不出** ——
      4.x 群聊里 `real_sender_id` 是**会话内本地槽位号、不可靠**（本文件 :1553 早就写着），
      我们自己那条回执在 DB 里带的是 **`sender_id=3`（数字槽位）**而不是空 ⇒ 旧条件 `(not name) and (not wid)`
      当场失效 ⇒ 回执被当成"成员 3 拍了我"喂给模型 ⇒ 它答「3你也拍，今天集体失眠是吧」（"3"其实是它自己）。
      现场证据（`data/message_ledger.jsonl` 07:43:27）：`sid=3 / echo=False / keep=True / text=[拍一拍]`，
      而同槽位的**文本**回复全部 `echo_hit=True`（文本回声窗管用，拍一拍这条没有文本可比对 ⇒ 漏）。
    ⇒ 新形态：**名字抠不出 + wxid 是空或纯数字槽位** ⇒ 判自家；wxid 形如真账号 ⇒ 保守当"别人拍我"
      （宁可少回拍一次，也不许把别人当成自己 —— 反过来正是 09-17 那个"把别人拍我判成我拍的"假成功的镜像）。
    """
    try:
        wid = str((nm or {}).get("poker_wxid") or "")
        if wid and self_wxid and wid == str(self_wxid):
            return True
        name = ""
        try:
            name = _poke_name_of_text(str((nm or {}).get("text") or ""))
        except Exception:
            name = ""
        if name:
            return False                      # 有名字 ⇒ "别人拍我"的形态
        return not (wid and not wid.isdigit())     # 空/数字槽位 ⇒ 自家回执；真账号 ⇒ 当别人
    except Exception:
        return False


def _poke_name_of_text(t: str) -> str:
    """从 `[拍一拍]（名字）` 里取名字（空串＝没有名字）。"""
    m = re.search(r"（([^）]+)）", str(t or ""))
    return m.group(1).strip() if m else ""


def _seq_ratio(a: str, b: str) -> float:
    """文本相似度 0~1（difflib，OCR 与数据库文本比对用）。"""
    try:
        import difflib
        return difflib.SequenceMatcher(None, str(a or ""), str(b or "")).ratio()
    except Exception:
        return 0.0


def _user32_is_visible(hwnd) -> bool:
    """查询窗口可见性（IsWindowVisible）。"""
    try:
        import ctypes
        return bool(ctypes.windll.user32.IsWindowVisible(int(hwnd)))
    except Exception:
        return False


def _cursor_pos() -> tuple:
    """当前光标位置（屏幕坐标）。"""
    try:
        import ctypes
        from ctypes import wintypes
        pt = wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return (pt.x, pt.y)
    except Exception:
        pass
    return (0, 0)


class WeChatError(Exception):
    pass


class Verdict(str):
    """发送类接口的**三态**判定结果：`ok` / `sent_unverified` / `not_sent`。

    为什么是 str 子类（2026-09-14 定）：调用点多处按 `ok, why = f(...)` 解包并 `if ok:` 判断，
    改成三元组会**破坏所有调用点**（测机上的跑器脚本就是这么被我坑过一次）。
    这里让 `__bool__` 只在 `ok == "ok"` 时为真 ⇒
      · `if ok:` 语义不变：**"未证实"绝不当成功**（fail-closed 照旧）
      · 同时 `ok == "sent_unverified"` / `str(ok)` 可机器判读三态
    背景：新机器 4.1.13.65 上投递其实发出去了，但 DB 回读通道失效（`master_key=None` ⇒ 静默返回旧数据），
    "发送成功"被判成"发送失败"；把两者写成一个 False 会误导版本矩阵与用户。
    """
    __slots__ = ()

    def __bool__(self) -> bool:
        return str(self) == "ok"


V_OK = Verdict("ok")
V_UNVERIFIED = Verdict("sent_unverified")
V_NOT_SENT = Verdict("not_sent")


class WeChatAdapter:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or get_config()
        # ⛔ 2026-09-18（用户反馈原文：「他回我之前自定义的地址里去看文件了」）：
        #   控制台保存配置走的是 `config.set_config(新对象)`，**换的是新对象**；把这个 adapter
        #   启动时拿到的那份抱在怀里 ⇒ 它手里的 `wechat.db_dir` 永远是旧值，改配置对它无效。
        #   ⇒ 分两种情况：调用方**显式传进来**的那份归调用方管（自检/夹具要能钉住输入）；
        #     我们自己从 `get_config()` 拿的那份是**共享对象**，每次接入前重取一次（`refresh_cfg`）。
        self._cfg_local = cfg is not None
        self._db_dir_info: dict = {}
        self._db = None
        self._gui = None
        self._md = None
        self._nick_map: dict = {}
        self._groups: list = []
        self._group_by_wxid: dict = {}
        self._self_wxid = ""
        self._self_nickname = ""
        self._img_key_ready = False
        self._send_lock = threading.Lock()
        self._recent_sent = deque(maxlen=200)   # 最近自己发过的消息文本 (text, ts)，用于过滤回声
        # 「哪条库行是我自己发的」：chat_id -> [(local_id, 库行 create_time(秒), 记录时刻), …]
        # 见 `remember_self_local` / `is_self_local` 的注释（用户反馈「他有时候还是会把自己识别成别人」）
        self._self_local: dict = {}
        self._self_local_loaded = False
        self._send_recent = deque(maxlen=50)    # 发送去重 (chat_id, text, ts)，防回车重试发两遍
        self._poke_back_cd: dict = {}           # wxid -> 上次「系统回拍」时间戳（30 分钟冷却，防连环拍）
        self._poke_playful: dict = {}           # 日期(yyyy-mm-dd) -> [ts...] 主动皮一下记录（按天限频）
        self._init_db()

    # ── 初始化 ───────────────────────────────────────────────────────────

    def _newest_wal_mtime(self) -> float:
        """消息库最近的 `message_*.db-wal` 的 mtime（找不到返回 0）。

        ⚠️ 2026-09-16 修（P11 第二层）：原来只硬编码 `%USERPROFILE%\\xwechat_files`，
        但**不同电脑的微信数据位置不同**——本机那个目录**根本不存在** ⇒ 永远返回 0
        ⇒ `db_alive()` 第二道闸写作 `if wal and newest and ...`，0.0 是假值 ⇒ **静默跳过，
        那道「回读落后活库」的闸从来没生效过**。
        现在**优先问库自己**：`WeChatDB.account_dir` 是它已经定位好的账号目录
        （`<账号>/db_storage/<库>`），`db_dir` 是账号目录的父目录；老路径只作兜底。
        """
        dirs = []
        try:
            adir = str(getattr(self._db, "account_dir", "") or "")
            if adir:
                dirs.append(os.path.join(adir, "db_storage", "message"))
        except Exception:
            pass
        try:
            ddir = str(getattr(self._db, "db_dir", "") or "")
            if ddir and os.path.isdir(ddir):
                for acc in os.listdir(ddir):
                    dirs.append(os.path.join(ddir, acc, "db_storage", "message"))
        except Exception:
            pass
        for base in (os.path.join(os.path.expanduser("~"), "xwechat_files"),
                     os.path.join(os.path.expanduser("~"), "Documents", "xwechat_files")):
            if not os.path.isdir(base):
                continue
            try:
                for acc in os.listdir(base):
                    dirs.append(os.path.join(base, acc, "db_storage", "message"))
            except Exception:
                pass
        best = 0.0
        for d in dirs:
            if not os.path.isdir(d):
                continue
            try:
                for f in os.listdir(d):
                    if f.startswith("message_") and f.endswith(".db-wal"):
                        m = os.path.getmtime(os.path.join(d, f))
                        if m > best:
                            best = m
            except Exception:
                continue
        return best

    def _usable_key_count(self) -> int:
        """有几把缓存密钥**能过页1 HMAC 校验**（过不了的不算）。

        2026-09-16 立：`master_key=None` 是**常态**——库（`wechatauto/db.py::_load_or_extract_keys`）
        是五层优先级（显式 key → 本地缓存 keys.json → Config.Cipher 内存扫描 → cfg 提取 → 最终回退），
        **只有内存扫描那层成功才给 `self.master_key` 赋值**；走缓存密钥时它一直是 None，
        而缓存密钥逐把过 SQLCipher4 页1 强校验 ⇒ 能解密就说明密钥是对的。
        """
        db = getattr(self, "_db", None)
        keys = getattr(db, "_keys", None) or {}
        n = 0
        for rel in list(keys):
            try:
                if db._key_works(rel):
                    n += 1
            except Exception:
                continue
        return n

    def db_alive(self, chat_id: str = "filehelper") -> tuple:
        """**判据可用性自检**：这个 DB 回读通道现在还能不能信？返回 `(alive, why)`。

        为什么必须（2026-09-14 由新机器 4.1.13.65 的测机报告推动）：`WeChatDB.master_key is None` 时
        `get_messages()` **不报错**、**静默返回旧数据**（filehelper 反复给出同一条 09-13 的老消息，
        轮询 63 秒不变），而同一分钟 **4.x 活库**的 `message_1.db-wal` 明明被写过 ⇒
        "投递成功"被回读判成"没发出去"（假失败）；反过来还可能"误判成功"（旧库里恰好有相似行）。
        ⇒ `alive=False` 时，调用方**必须把结论降级成「未证实」**（`V_UNVERIFIED`），不许写"发送失败"。
        """
        try:
            # 2026-09-16 改：原自检是 `if mk is None: return False`——把"没走主密钥那条路"当成
            # "通道坏了"。但 `master_key=None` 是**常态**（见 `_usable_key_count` 的注释），
            # 本机实测：master_key=None + 20 把缓存密钥全过页1校验 + 真读到最新消息 ⇒ 通道是好的。
            # 老自检的后果是**每台机器都永远判"不可用"**，"没等到新行"一律被降级成"未证实"
            # （对面 r22 核心②就是这么来的）。新自检：既没主密钥、又没有一把可用缓存密钥，才算不可信。
            mk = getattr(self._db, "master_key", "?")
            if mk is None and self._usable_key_count() <= 0:
                return False, ("既没拿到主密钥、也没有任何能过页1校验的缓存密钥 ⇒ 回读通道不可信"
                               "（拿到的可能是旧副本）")
            rows = self._db.get_messages(chat_id, limit=1) or []
            wal = self._newest_wal_mtime()
            # 第二道闸：**活库现在还在写吗**。这道闸只在"刚发完、没等到新行"的上下文里被问
            # （三个调用点都是发送链），所以"库在动、而我们读不到这个会话的新行"才是可疑信号。
            # ⚠️ 2026-09-16 修（P11 第三层）：老写法拿"该会话最新消息时间"跟 -wal 比
            #    （`wal - newest > 300`），对**空闲会话**必然判落后——本机 filehelper 实测：
            #    消息 03:07 / -wal 05:37 ⇒ 差 8958 秒，而那 8958 秒是别的会话在写库，语义是错的。
            #    何况它依赖 `_newest_wal_mtime()`，那个函数以前永远返回 0 ⇒ 这道闸从来没生效过。
            now = time.time()
            if wal and (now - wal) < 60:
                return False, ("活库刚刚还在写（-wal %s，%d 秒前），但这个会话读不到新行"
                               "⇒ 可能消息进了库却读不到（未证实，不判真失败）"
                               % (time.strftime("%H:%M:%S", time.localtime(wal)), int(now - wal)))
            if not rows:
                return True, "能读该会话（当前无消息）"
            return True, "回读通道看起来是活的（活库最近没有新写入）"
        except Exception as e:
            return False, "判据可用性自检异常：%s: %s" % (type(e).__name__, e)

    def refresh_cfg(self) -> dict:
        """接入/重连前**重取当前配置**：别再拿启动那一刻的旧快照。

        2026-09-18 用户反馈原文：「他回我之前自定义的地址里去看文件了」——"旧值继续生效"的一处
        就在这里：控制台保存配置走 `config.set_config(新对象)`（换的是新对象），而 adapter 抱着
        启动时那份不放 ⇒ 它眼里的 `wechat.db_dir` 永远是旧的自定义路径。
        调用方**显式传进来**的那份 cfg 归调用方管（自检/夹具要能钉住输入），不动它。
        """
        if self._cfg_local:
            return self.cfg
        try:
            _c = get_config()
            if _c:
                self.cfg = _c
        except Exception:
            pass
        return self.cfg

    def _init_db(self):
        try:
            from wechatauto import WeChatDB, MediaDownloader
        except ImportError as e:
            raise WeChatError("未安装 wechatauto：请先安装依赖（pip install -r requirements.txt）。%s" % e)
        self.refresh_cfg()                      # 现取当前配置（不拿启动时的旧快照）
        _dd = str(self.cfg.get("wechat", {}).get("db_dir") or "").strip()
        # ⛔ 2026-09-18（用户反馈：「自己自定义的地址他检测不到」「他回我之前自定义的地址里去看文件了」）：
        #    填进来的目录**先过一遍校验**（存在 + 有 db_storage 或库文件），不过就立刻说出来。
        #    为什么不能只靠"开库失败"来兜：旧目录留在盘上、里面有旧副本时，驱动库会**成功地**
        #    打开它 ⇒ 用户看到的是"监听后没反应"，而日志里一个字都没有（静默用旧值）。
        _dchk = {}
        if _dd:
            try:
                from . import wechat_dir as _wd0
                _dchk = _wd0.check(_dd)
            except Exception:
                _dchk = {}
        if _dd and _dchk and not _dchk.get("ok"):
            try:
                log.warning("「数据库目录」里填的 %s 不可用（%s）⇒ 按「配置 → 扫盘 → 驱动库自探测」"
                            "回落到能用的那个；控制台「微信数据目录」那一行显示的是**实际在读**的目录",
                            _dd, _dchk.get("why") or "用不了")
            except Exception:
                pass
        # ⛔ 2026-09-17（网友那份检验报告：侧栏「微信未连接·原因未知」+ 报告里「会话头检查失败:
        #    未找到任何已登录账号的数据库」，而**同一进程的逐步诊断六步全过**）：
        #    两条路只差**回退链**——老写法只在"扫盘那条"失败时才退，配置里填错一条就直接抛
        #    （`_src == "config"` ⇒ re-raise）⇒ 用户明明有能用的库，却被控制台那个输入框按死。
        #    ⇒ 三档依次试（配置 → 扫盘 → 驱动库自探测），全失败才真失败。
        self._db, _how, _errs = open_db(_dd)
        self._db_how = dict(_how or {})
        if self._db is None:
            _why = "；".join("「%s」%s" % (d or "驱动库自探测", e) for d, _s, e in _errs)
            _e0 = _errs[0][2] if _errs else "未知原因"
            raise WeChatError("打不开消息库：%s（试过 %d 条路：%s）%s"
                              % (_e0, len(_errs), _why, _db_open_verdict(_probe_db_dirs(_dd), _errs)))
        _picked = str(_how.get("dir") or "")
        _src = str(_how.get("src") or "")
        if _src == "scanned":
            # 2026-09-16（网友 B 的诊断截图）：**不写用户的 config，但必须留痕** ——
            # 否则用户会以为"我什么都没配它就好了"，下次换台机器/换个目录又要重新踩一遍。
            try:
                log.warning("「数据库目录」没配 ⇒ 自动用了扫盘探到的 %s"
                            "（建议在控制台「数据库目录」里保存它，免得下次又靠扫盘）", _picked)
            except Exception:
                pass
        elif _dd and _src != "config":
            # 配置里填了、但那条路用不了 ⇒ **必须说出来**（静默改用别的目录会让用户
            # 以为"我填的那个生效了"，下次换机器又踩）。不替他改配置，只给能照着做的动作。
            try:
                log.warning("「数据库目录」里填的 %s 用不了（%s）⇒ 已自动改用 %s；"
                            "建议把那个框改成这个目录，或在控制台清空它让它自动探测",
                            _dd, (_errs[0][2] if _errs else "开不了"), _picked or "驱动库自探测到的目录")
            except Exception:
                pass
        # 「当前**实际**在读哪个目录」的唯一落点 —— 检验报告 / 控制台 / `/api/status` 都读它，
        # 而不是去读配置值（用户那句"报告显示他回我之前自定义的地址里去看文件了"就是从这儿丢的）。
        self._db_dir_info = {}
        try:
            from . import wechat_dir as _wd1
            self._db_dir_info = _wd1.status(how=self._db_how, explicit=_dd)
            if self._db_dir_info.get("note"):
                log.warning("消息库目录：%s", self._db_dir_info["note"])
            else:
                log.info("消息库目录：%s（来源=%s）",
                         self._db_dir_info.get("now"), self._db_dir_info.get("src"))
        except Exception as _e:
            log.debug("算「当前实际在读哪个目录」失败（继续）：%s", _e)
        # 2026-09-16（网友 A 的报告：`KeyError: 'message\media 1.db'`）：微信会**懒创建**新分片，
        # 而驱动库的密钥表是它 init 时的快照 ⇒ 新分片没密钥 ⇒ 读消息/会话头/投递回读全断。
        # 接入时补一次（刷新分片表 + 补齐密钥），并把"仍缺密钥的分片"记下来给诊断/控制台看。
        self._db_missing_shards = []
        self._db_dropped_shards = []
        try:
            from . import replica_adapter as _ra
            _rep = _ra.refresh_shards(self._db)
            self._db_missing_shards = list(_rep.get("missing") or [])
            self._db_dropped_shards = list(_rep.get("dropped") or [])
            if self._db_missing_shards:
                log.warning("有 %d 个库分片拿不到密钥（例：%s）⇒ 这些库读不了",
                            len(self._db_missing_shards), self._db_missing_shards[0])
            elif _rep.get("added"):
                log.info("补到 %d 个新分片的密钥：%s", len(_rep["added"]), _rep["added"][:3])
            if self._db_dropped_shards:
                # 摘掉是**有代价的补救**（那几个分片的内容读不到），必须留痕、不许静默
                log.warning("已跳过 %d 个补不到密钥的分片（例：%s）⇒ 那几个库的内容读不到，其余照常",
                            len(self._db_dropped_shards), self._db_dropped_shards[0])
        except Exception as _e:
            log.debug("刷新分片密钥失败（继续）：%s", _e)
        info = self._db.get_self_info() or {}
        self._self_wxid = str(info.get("username") or "")
        self._self_nickname = str(info.get("nick_name") or "")
        # ⛔ 2026-09-16（已知现象：「一直有个问题 无法识别大号用户 就是无法识别我的账号」）：
        #   "自己是谁"**只有这一个来源**——驱动库的 `get_self_info()`。它**在某些账号 / 微信版本下会返回空**
        #   ⇒ `_self_wxid` 为空 ⇒ 下面所有"这条是不是我发的"判断（`:875`、`:4652`、`recall`）
        #   **静默变假**：表现为机器人可能**回你自己**、@ 你自己不响应、撤回自己的消息失灵，
        #   而且以前**既没有日志、也没有界面提示**，用户只能看到"怪怪的"。
        #   ⇒ 现在：拿不到就明确记一行警告；控制台「微信」面板也会如实显示"没认出来"（`self_identity()`）。
        self._self_ident_ok = bool(self._self_wxid)
        # ⭐ 2026-09-18：先读回**以前从回声里学到的**自己（它比猜的可靠）——
        #   `get_self_info()` 返回空时，这一条就是唯一能把"我"认出来的东西（详见 learn_self_from_echo）。
        self._self_wxid_src = "api" if self._self_wxid else ""
        try:
            self.load_self_identity()
        except Exception:
            pass
        self._self_ident_ok = bool(self._self_wxid)
        # 「我的其他账号（大号）」登记表（2026-09-16 既有口径：）—— 见 _load_owner_accounts 的注释
        self._load_owner_accounts()
        if not self._self_wxid:
            try:
                import logging as _lg
                _lg.getLogger("persona-morph").warning(
                    "没能识别出你自己的微信账号（get_self_info 返回空，也还没学到）⇒ "
                    "先靠文本回声兜底：**你拿机器人号手打的字会被当成陌生人的话**（它会回你）。"
                    "机器人每成功发一次消息都会写回库，下一次就能从回声里学会「我是谁」并落盘。")
            except Exception:
                pass
        self._nick_map = self._load_nicknames()
        self._groups = self._load_groups()
        self._privates = self._load_privates()      # 2026-09-16：私聊目标（「大号跟小号对谈」）
        self._group_by_wxid = {g["wxid"]: g for g in self._groups}
        # 图片解密密钥（惰性）
        try:
            self._md = MediaDownloader(self._db)
            if self._md._load_persisted_key():
                self._img_key_ready = True
        except Exception:
            self._md = None
            self._img_key_ready = False

    def _load_nicknames(self) -> dict:
        # W1：contact 整表映射走适配层（原来直接摸 _db_files/_open 两个私有接口）
        try:
            return replica_adapter.load_nickname_map(self._db)
        except Exception:
            return {}

    def _load_groups(self) -> list:
        # W1：优先用公开 get_groups()，拿不到才回退（回退路径也在适配层里，调用点不再碰私有接口）
        try:
            return replica_adapter.load_groups(self._db)
        except Exception:
            return []

    def _load_privates(self) -> list:
        """私聊联系人（非群、非系统号）。2026-09-16 加：支持「大号跟小号对谈」。"""
        try:
            return replica_adapter.load_privates(self._db)
        except Exception:
            return []

    # ── 读取 ─────────────────────────────────────────────────────────────

    @property
    def self_wxid(self) -> str:
        return self._self_wxid

    @property
    def self_nickname(self) -> str:
        return self._self_nickname

    def self_identity(self) -> dict:
        """给控制台用的「当前识别到的自己」（2026-09-16，用户反馈「无法识别我的账号」）。

        `ok=False` ＝没能从驱动库拿到自己的账号 ⇒ 界面要**如实写"没认出来"**，不许装没事。
        """
        w = self._self_wxid or ""
        return {"ok": bool(w),
                "wxidMasked": (w[:3] + "***") if len(w) > 6 else "",
                "nickname": self._self_nickname or ""}

    def _load_owner_accounts(self):
        """加载「我的其他账号（大号）」登记表与反应档位。

        用户 2026-09-16 反馈：「这个是用的我的小号 他无法识别我的大号 之前版本也有这个问题」。
        登记项可以是 **wxid**（最准、优先）或 **昵称**（兜底，控制台会把匹配结果列出来让你核对）。
        反应档位（映射到控制台 UI 让用户自己选）：off / skip / know。
        """
        w = (self.cfg.get("wechat", {}) or {})
        raw = w.get("owner_accounts") or []
        if isinstance(raw, str):
            raw = [x for x in raw.replace("，", ",").replace("\n", ",").split(",")]
        elif isinstance(raw, (list, tuple)):
            pass
        else:
            raw = []
        self._owner_ids = {str(x).strip().lower() for x in raw if str(x).strip()}
        self._owner_mode = str(w.get("owner_mode") or "know").strip().lower()
        # 四档（既有口径：机制要映射到 UI 上让他自己选）：
        #   off            = 不做这个识别（登记了也不认）
        #   skip           = 完全不回复我自己的号
        #   owner_at_only  = **只在群里 @ 我或引用我的时候才回**（其余不回；私聊不受影响）—— 2026-09-16 新增
        #   know           = 照常回，但打 owner 标记让模型知道"这是主人"
        if self._owner_mode not in ("off", "skip", "owner_at_only", "know"):
            self._owner_mode = "know"

    def is_owner(self, sender_id: str = "", sender_name: str = "") -> bool:
        """这条消息是不是**主人自己另一个号**发的（wxid 精确优先，昵称兜底 —— 用户口径「哪个准就用哪个」）。"""
        if str(getattr(self, "_owner_mode", "know")) == "off":
            return False
        ids = getattr(self, "_owner_ids", None) or set()
        if not ids:
            return False
        sid = str(sender_id or "").strip().lower()
        snm = str(sender_name or "").strip().lower()
        return bool((sid and sid in ids) or (snm and snm in ids))

    def owner_status(self) -> dict:
        """给控制台：登记了几项、其中 wxid 几项、昵称几项、昵称**真在群成员里匹配上**的有哪些。

        为什么要把匹配结果摆出来：昵称可能撞名（认错人），用户一眼能发现并改成 wxid。
        """
        ids = sorted(getattr(self, "_owner_ids", None) or [])
        mode = str(getattr(self, "_owner_mode", "know"))
        nick = set()
        try:
            nick = {str(v).strip().lower() for v in (self._nick_map or {}).values() if str(v).strip()}
        except Exception:
            pass
        by_id = [x for x in ids if x.startswith("wxid_")]
        by_name = [x for x in ids if not x.startswith("wxid_")]
        return {"mode": mode,
                "count": len(ids),
                "byId": len(by_id),
                "byName": len(by_name),
                "nameMatched": sorted([x for x in by_name if x in nick]),
                "nameUnmatched": sorted([x for x in by_name if x not in nick])}

    def list_groups(self) -> list:
        return list(self._groups)

    def list_privates(self) -> list:
        """私聊联系人列表（2026-09-16 加，供监听目标发现用）。"""
        return list(getattr(self, "_privates", []) or [])

    def list_private_targets(self) -> list:
        """按 `wechat.private_chat` 档位给出**该监听的私聊**：

        · `off`       ⇒ 空（不监听私聊）
        · `owner_only`⇒ 只给「我的其他账号（大号）」命中的人（用户口径：「大号跟小号对谈」）
        · `all`       ⇒ 全部私聊联系人

        ⚠️ `owner_only` 下若**没登记任何账号** ⇒ 返回空并**如实写在返回里**（由调用方记日志），
        不静默变成"监听所有人"。
        """
        mode = str((self.cfg.get("wechat", {}) or {}).get("private_chat") or "owner_only").strip().lower()
        if mode not in ("off", "owner_only", "all"):
            mode = "owner_only"
        if mode == "off":
            return []
        priv = self.list_privates()
        if mode == "all":
            return priv
        out = []
        for c in priv:
            try:
                if self.is_owner(str(c.get("wxid") or ""), str(c.get("name") or "")):
                    out.append(c)
            except Exception:
                continue
        return out

    def group_name(self, wxid: str) -> str:
        g = self._group_by_wxid.get(wxid)
        return g["name"] if g else wxid

    def member_name(self, chat_id: str, wxid: str) -> str:
        """解析成员展示名（用于 @）。"""
        if not wxid:
            return ""
        return self._nick_map.get(str(wxid), str(wxid))

    def latest_seq(self, wxid: str) -> int:
        try:
            msgs = self._db.get_messages(wxid, limit=1)
            return int(msgs[0]["sort_seq"]) if msgs else 0
        except Exception:
            return 0

    def poll_new_messages(self, wxid: str, since_seq: int, limit: int = 50) -> list:
        """返回 sort_seq > since_seq 的新消息（升序），归一化后。

        🔴 2026-09-18 修（**"机器人每两分钟自己念一句"的根因**）：归一化阶段被丢掉的行
        （自己发的 / 系统消息 / 空内容）**原来直接不进批次** ⇒ 监听那边"成功才推进水位"就永远
        推不过这条 ⇒ **同一行每 1.5 秒被重读一次**（现场台账：同一条连着 24 行 `echo=True keep=False`，
        水位停在机器人自己那条消息的时间上）；而它一旦**超过回声窗（120 秒）**，就不再被判成"自己"
        ⇒ 当成别人的话 ⇒ **机器人回自己**（现场：02:27:10 发出的话，02:29:12 被当别人回了个「？」）。
        ⇒ 现在丢掉的行也**带一个只含 seq 的"跳过标记"**出来，让水位能推过去（调用方照旧拿 `mid` 用，
        只是遇到 `skip` 标记时直接算"已处理"）。
        """
        try:
            raws = self._db.get_new_messages(wxid, since_seq, limit)
        except Exception:
            return []
        out = []
        for raw in raws:
            norm = self.normalize(raw, wxid)
            if norm:
                out.append(norm)
                continue
            try:
                _sq = int(raw.get("sort_seq") or 0)
            except Exception:
                _sq = 0
            if _sq:
                out.append({"mid": None, "sort_seq": _sq, "ts": 0, "sender_id": "",
                            "sender_name": "", "text": "", "media": [],
                            "self": True, "skip": "归一化阶段丢弃（自己发的/系统/空内容）"})
        return out

    def local_id_by_server_id(self, chat_id: str, server_id: int):
        """server_id（消息 svrid / 撤回报文里的 newmsgid）→ local_id；查不到返回 None。

        撤回事件用它把「被撤的那条」与存档条目**精确对上**（比按发送者+时间猜可靠）。
        """
        try:
            return replica_adapter.find_server_id_local_id(self._db, chat_id, int(server_id))
        except Exception:
            return None

    def message_content(self, chat_id: str, local_id):
        """只读：读某条消息**当前**在库里的原始内容（撤回核对用）。

        为什么需要：微信可能不新增系统行，而是把被撤的那一行**原地改写**成撤回报文；
        只认"新出现的系统行"会漏 ⇒ 由 `agent/recall.py::sweep()` 定期回读核对。
        读不到（行被删/查询异常）返回 None —— 调用方一律按"不动"处理。
        """
        try:
            row = self._db.get_message_row(chat_id, int(local_id))
        except Exception:
            return None
        if not row:
            return None
        c = row.get("content")
        if isinstance(c, bytes):
            c = c.decode("utf-8", "ignore")
        return str(c or "")

    def _parse_quote(self, chat_id: str, local_id):
        """解析「引用 / 拍一拍」这类 zstd 压缩的 appmsg 消息，提取正文与被引用图片。"""
        try:
            row = self._db.get_message_row(chat_id, int(local_id))
            if not row:
                return None
            content = row.get("content")
            if not isinstance(content, bytes) or not content.startswith(b"\x28\xb5\x2f\xfd"):
                return None
            import zstandard
            dctx = zstandard.ZstdDecompressor()
            txt = dctx.decompress(content, max_output_size=200000).decode("utf-8", "ignore")

            title_m = re.search(r"<title>(.*?)</title>", txt, re.S)
            title = html.unescape(title_m.group(1)).strip() if title_m else ""

            # ── 拍一拍事件（appmsg type=62，标题形如「E」拍拍「群deepseek」）──
            type_m = re.search(r"<type>(\d+)</type>", txt)
            if type_m and type_m.group(1) == "62":
                poker = ""
                poker_wxid = ""
                pm = re.search(r"「([^」]+)」拍拍", title)
                if pm:
                    poker = pm.group(1)
                # 拍的人 wxid（patinfo.fromusername），用于「拍回去」
                pm2 = re.search(r"<patinfo>.*?<fromusername>([^<]+)</fromusername>", txt, re.S)
                if pm2:
                    poker_wxid = pm2.group(1)
                return {"text": "[拍一拍]" + ("（%s）" % poker if poker else ""),
                        "media": [], "sender_wxid": "", "poke": True, "poker": poker,
                        "poker_wxid": poker_wxid}

            # ── 引用消息（type 57，有 <refermsg>）──
            if "<refermsg>" not in txt:
                return None
            sender_wxid = ""
            sm = _SENDER_RE.match(txt)
            if sm:
                sender_wxid = sm.group(1)
            media = []
            ref_type = re.search(r"<refermsg>.*?<type>(\d+)</type>", txt, re.S)
            svrid_m = re.search(r"<svrid>(\d+)</svrid>", txt)
            if ref_type and ref_type.group(1) == "3" and svrid_m:
                try:
                    # W1：1.2.2 起 _msg_conn 只返回第一个命中分片 ⇒ 改为跨全部分片查（适配层负责遍历与关闭）
                    local_id = replica_adapter.find_server_id_local_id(self._db, chat_id, int(svrid_m.group(1)))
                    if local_id is not None:
                        media = [{"kind": "image", "local_id": local_id}]
                except Exception:
                    pass
            return {"text": title or "[引用消息]", "media": media, "sender_wxid": sender_wxid}
        except Exception:
            return None

    def _mark_sent(self, text: str):
        """记录一条自己刚发出去的消息文本（用于过滤数据库回读的"回声"）。"""
        t = str(text or "").strip()
        if t:
            self._recent_sent.append((t, time.time()))

    # ── 「这条库行是不是我自己发的」：按 **行号** 判自己（2026-09-18 加）────────────
    # 为什么需要（用户两次反馈：「他有时候还是会把自己识别成别人」）：判自己原来只有三档证据 ——
    #   ① `self_wxid` 命中 ② 昵称一致 + 我刚发过 ③ 文本回声窗（120 秒）。三条**都不认库行号**，于是
    #   两处必然漏判：ⓐ 发图/发表情/发文件回读出来的 text 是 `[图片]`/`[表情]`/`[文件/链接/卡片]`
    #   —— 这类"无语义文本"在回声窗里对不上任何东西，**永远判不出自己**；ⓑ 手打一句（用户拿机器人号
    #   自己打的话不在我们的发送台账里）或任何超出回声窗的回读 ⇒ 被当成别人的话 ⇒ 机器人回自己。
    # 现在补**最强的一档**：我们自己发出的消息，**发送成功的那一刻就已经从 DB 回读到了它的 local_id**
    #   （send_text_posted / send_image_posted / send_file_posted 的那几个回读点）⇒ 把 (会话, 行号) 记下来，
    #   监听侧只要行号命中就判自己 —— 不依赖文本、不依赖回声时间窗。
    # ⚠️ 两条防误判（红线：宁可漏判一次回声，也**绝不许把别人的话**丢掉）：
    #   ① **行号 + 时间一起比**：用户「清空聊天记录」会把该会话的消息表整张删掉、`local_id` 从 1 重新开始
    #      （现场事故"机器人跟自己吵 8 条"的根因链之一）⇒ 只比行号时，将来某条**别人的**新消息会撞上
    #      我们记下的老行号。所以记录里带上那条库行的 `create_time`，命中时**还要求时间接近**（±600 秒）。
    #   ② **只留 24 小时**：过期行号定期清掉，缩小撞号面。
    _SELF_LOCAL_WIN_S = 600.0            # 行号命中时，允许的 create_time 偏差
    _SELF_LOCAL_KEEP_S = 24 * 3600.0     # 记录保留时长

    def _self_local_file(self) -> str:
        return os.path.join(ROOT, "data", "self_local_ids.json")

    @staticmethod
    def _ct_s(create_time) -> int:
        """库里的 create_time 统一成**秒**（微信 4.x 存秒，别的地方出现过毫秒）。"""
        try:
            v = int(create_time or 0)
        except Exception:
            return 0
        return int(v / 1000) if v >= 1_000_000_000_000 else v

    def _load_self_local(self) -> None:
        if getattr(self, "_self_local_loaded", False):
            return
        self._self_local_loaded = True
        if not isinstance(getattr(self, "_self_local", None), dict):
            self._self_local = {}
        try:
            import json as _json
            with open(self._self_local_file(), encoding="utf-8") as fh:
                d = _json.load(fh) or {}
        except Exception:
            return
        if not isinstance(d, dict):
            return
        now = time.time()
        for k, v in d.items():
            rows = []
            for it in (v or []):
                try:
                    lid, ct, ts = int(it[0]), int(it[1]), float(it[2])
                except Exception:
                    continue
                if lid and now - ts <= self._SELF_LOCAL_KEEP_S:
                    rows.append((lid, ct, ts))
            if rows:
                self._self_local[str(k)] = rows[-200:]

    def _save_self_local(self) -> None:
        try:
            import json as _json
            p = self._self_local_file()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                _json.dump({k: [[i, c, int(t)] for i, c, t in v] for k, v in self._self_local.items()},
                           fh, ensure_ascii=False)
            os.replace(tmp, p)
        except Exception as e:
            log.debug("自我行号落盘失败（本次进程内仍然生效）：%s", e)

    def remember_self_local(self, chat_id, local_id, create_time=None) -> None:
        """记下「这条库行是我自己刚发出去的」。

        ⛔ 调用时机**只允许在 DB 回读确认成功之后** —— 没确认就记账＝把别人的消息登记成自己的，
        后果是那条消息被静默丢掉（漏回），比漏判回声更糟。
        """
        try:
            lid = int(local_id or 0)
        except Exception:
            return
        if not lid:
            return
        self._load_self_local()
        key = str(chat_id or "")
        ct = self._ct_s(create_time)
        if not isinstance(getattr(self, "_self_local", None), dict):
            self._self_local = {}
        lst = [r for r in self._self_local.get(key, []) if r[0] != lid]
        lst.append((lid, ct, time.time()))
        self._self_local[key] = lst[-200:]
        self._save_self_local()

    def is_self_local(self, chat_id, local_id, create_time=None) -> bool:
        """这条库行是不是我们自己发的？**没有记录就是不判自己**（绝不影响别人）。"""
        try:
            lid = int(local_id or 0)
        except Exception:
            return False
        if not lid:
            return False
        self._load_self_local()
        now = time.time()
        ct = self._ct_s(create_time)
        for r_lid, r_ct, r_ts in (getattr(self, "_self_local", {}) or {}).get(str(chat_id or ""), []):
            if r_lid != lid:
                continue
            if now - r_ts > self._SELF_LOCAL_KEEP_S:
                continue
            if r_ct and ct and abs(r_ct - ct) > self._SELF_LOCAL_WIN_S:
                # 行号撞上了、时间对不上 ⇒ 多半是「清空聊天记录」后行号重排 ⇒ 按**别人**处理并留痕
                try:
                    log.warning("自我行号撞号但时间不符（local_id=%s：记录 %s / 实到 %s）"
                                "⇒ 不判自己，按别人的消息处理", lid, r_ct, ct)
                except Exception:
                    pass
                continue
            return True
        return False

    def _self_id_file(self) -> str:
        return os.path.join(ROOT, "data", "self_identity.json")

    def load_self_identity(self) -> dict:
        """启动时读回"自己是谁"（学到的优先于猜的）。返回 `{wxid, nickname, from}`。"""
        try:
            import json as _json
            with open(self._self_id_file(), encoding="utf-8") as fh:
                d = _json.load(fh) or {}
        except Exception:
            d = {}
        if not isinstance(d, dict):
            d = {}
        w = str(d.get("wxid") or "").strip()
        if w and not self._self_wxid:
            self._self_wxid = w
            self._self_wxid_src = str(d.get("from") or "saved")
            try:
                log.info("已读回「自己是谁」：%s（来源 %s）", _mask_id(w), self._self_wxid_src)
            except Exception:
                pass
        return d

    def learn_self_from_echo(self, sender_wxid: str, sample: str = "") -> bool:
        """**从自己消息的库回读里学会"我"是谁**（2026-09-18）。

        为什么需要它：原来"自己是谁"只有 `get_self_info()` 一个来源，而它在某些账号/微信版本上
        **返回空** ⇒ 下面那串"这条是不是我发的"判断全部失效 ⇒ 表现就是**机器人回自己**（尤其当
        用户拿机器人号手打一句话时：那句不在我们的发送台账里，回声窗认不出）。
        现在把"我们刚发出去、又读回来的那条"当成最可靠样本：它的 sender_wxid 必然是我。
        """
        w = str(sender_wxid or "").strip()
        if not w or w.startswith("gh_"):                 # 公众号等不是人，别学
            return False
        if w == self._self_wxid:
            return False
        self._self_wxid = w
        self._self_wxid_src = "echo"
        try:
            import json as _json
            p = self._self_id_file()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                _json.dump({"wxid": w, "nickname": self._self_nickname or "",
                            "from": "echo", "sample": str(sample or "")[:40],
                            "at": int(time.time() * 1000)}, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, p)
        except Exception as e:
            log.warning("自我识别落盘失败（本次进程内仍然生效）：%s", e)
        try:
            log.info("学会了「自己是谁」：%s（从自己发出消息的库回读里认出，样本 %r）",
                     _mask_id(w), str(sample or "")[:20])
        except Exception:
            pass
        return True

    def _reattach_if_floating(self, name: str) -> str:
        """把被"双击"独立出去的那个聊天窗**收回来**（独立窗口就是这个群/人）。

        为什么会独立：微信 4.x 双击会话列表的一行 ⇒ 把该聊天拖成独立浮动窗（用户 2026-09-18 截图反馈
        「他会把那个群给拖出窗口化」）。我们的切会话若因复核不过而重复点同一位置，就会被 Qt 判成双击。
        两步处置：①**预防**＝会话行点击加全局最小间隔（见 `switch_chat_posted` 里的 `_row_click_last`）；
        ②**收尾**＝万一已经独立出去了，把那个浮动窗关掉（微信会把它放回主窗），并留一行日志。
        返回说明串（空串＝没发现浮动窗）。**只关"标题含目标名 + 类名是微信 Qt 窗"的窗**，避免误关别的。
        """
        try:
            import win32gui
        except Exception:
            return ""
        main = int(getattr(self._get_gui(), "main_hwnd", 0) or 0)
        hits = []

        def _cb(h, _l):
            try:
                if not win32gui.IsWindowVisible(h):
                    return True
                if main and int(h) == main:
                    return True
                t = str(win32gui.GetWindowText(h) or "")
                if not t or not name or str(name) not in t:
                    return True
                cls = str(win32gui.GetClassName(h) or "")
                if not cls.startswith("Qt"):            # 只认微信自己的 Qt 窗（别关别人的窗）
                    return True
                hits.append((int(h), t))
            except Exception:
                pass
            return True

        try:
            win32gui.EnumWindows(_cb, None)
        except Exception:
            return ""
        if not hits:
            return ""
        done = 0
        for h, t in hits[:2]:
            try:
                # ⛔ 咽喉点：**决不许把关掉主窗**（Qt 重建过 hwnd 时，老 main 就不在白名单里了
                #   ——2026-09-16 就是这么把用户的微信主窗 WM_CLOSE 掉的）
                if not _wm_close_safe(h, "收回浮动聊天窗「%s」" % str(t)[:16]):
                    continue
                win32gui.PostMessage(h, 0x0010, 0, 0)   # WM_CLOSE ⇒ 独立窗关掉、聊天回主窗
                done += 1
                log.warning("发现聊天被独立成了浮动窗口「%s」⇒ 已收回（双击会话行的后果，已加防双击闸）",
                            t[:24])
            except Exception as e:
                log.warning("收回浮动聊天窗失败（%s）：%s", t[:20], e)
        if done:
            time.sleep(0.4)
        return "已收回被独立出去的聊天窗 %d 个" % done if done else ""

    def _echo_window(self) -> float:
        """回声判定时间窗（秒）。默认 **120 秒**（2026-09-18 由 30s 放宽）。

        为什么放宽（现场事故）：机器人**跟自己吵了 8 条**（"来了 别催了"/"？发图干嘛"/
        "图呢 我没看到"…）。根因链是"user 每次拍完清空聊天记录 ⇒ 消息表整张没了 ⇒ 自认回读失效"，
        而 30 秒的窗**太短**——一条消息从"发出"到"监听器从库里回读出来"中间可能夹着
        发图/切会话/OCR，实测窗口一过就判成别人的话 ⇒ 回自己。
        可配：`config.json → wechat.echo_window_s`。
        """
        try:
            v = float((self.cfg.get("wechat", {}) or {}).get("echo_window_s") or 0) or 0.0
        except Exception:
            v = 0.0
        return v if v > 0 else 120.0

    @staticmethod
    def _echo_norm(s: str) -> str:
        """回声比对用的归一化：去空白/零宽字符、统一全角括号引号。

        为什么需要（同一事故）：发出的是 `来了 别催了`，回读到的可能因为富文本包装
        变成 `来了  别催了`（多一个空格）或带上零宽字符 ⇒ 严格 `==` 就漏判。
        """
        t = str(s or "")
        for ch in ("\u200b", "\u200c", "\u200d", "\ufeff", "\u00a0"):
            t = t.replace(ch, "")
        t = "".join(t.split())
        for a, b in (("（", "("), ("）", ")"), ("，", ","), ("。", "."), ("！", "!"),
                     ("？", "?"), ("：", ":"), ("；", ";"), ("“", '"'), ("”", '"'),
                     ("‘", "'"), ("’", "'")):
            t = t.replace(a, b)
        return t

    def _is_self_echo(self, text: str) -> bool:
        """判断一条消息是不是自己刚发的（数据库回读回声）。

        微信发出的消息会写回本地库，且群聊里 sender_id 不可靠，所以用"文本对得上 + 时间窗"
        兜底过滤，避免自问自答死循环。三档判据（宽→严）：
        ① **归一化后完全一致**（去空白/零宽/统一标点）；
        ② **包含关系**：一方完整包含另一方，且短的那条 ≥4 字（治"我发整段、库里回来的是半句"）；
        ③ 老口径的严格 `==` 仍在（①的退化情形）。
        """
        t = str(text or "").strip()
        if not t:
            return False
        now = time.time()
        win = self._echo_window()
        nt = self._echo_norm(t)
        for sent_text, sent_ts in list(self._recent_sent or []):
            if (now - float(sent_ts)) >= win:
                continue
            if sent_text == t:
                return True
            ns = self._echo_norm(sent_text)
            if not ns:
                continue
            if ns == nt:
                return True
            short, long_ = (ns, nt) if len(ns) <= len(nt) else (nt, ns)
            if len(short) >= 4 and short in long_:
                return True
        return False

    def _note_ledger(self, chat_id, raw: dict, out) -> None:
        """**消息判定台账**（2026-09-18 加）：每条进来的消息都记一笔"判为谁 + 为什么"。

        为什么需要（现场事故）：机器人**跟自己吵了 8 条**（把自己发的话当成别人的话），
        而光看现象无法定位是哪一步错了 —— 可能是 `self_wxid` 没命中、可能是回声窗没过、
        也可能那条是"系统/空内容"。⇒ 每次判定都落一条（内存 + `data/message_ledger.jsonl`），
        下次再出现就能一眼看出是哪一步，不用再猜。
        """
        try:
            import json as _json
            content = raw.get("content") or ""
            if isinstance(content, bytes):
                content = content.decode("utf-8", "ignore")
            content = str(content)
            m = _SENDER_RE.match(content)
            sender_wxid = (m.group(1) if m else "") or ""
            text = str((out or {}).get("text") or (content[m.end():].strip() if m else content)).strip()
            self_hit = bool(self._self_wxid and sender_wxid and sender_wxid == self._self_wxid)
            echo_hit = bool(text) and self._is_self_echo(text)
            # 自家行号命中（2026-09-18 加）：判自己的**最强**一档，不依赖文本（发图/表情/文件的回读
            # 文本是 `[图片]`/`[表情]`，靠文本判永远认不出自己）。台账里单列一项，方便下次一眼定位。
            try:
                sl_hit = self.is_self_local(chat_id, raw.get("local_id"), raw.get("create_time"))
            except Exception:
                sl_hit = False
            if self_hit:
                why = "判为自己：self_wxid 命中"
            elif sl_hit:
                why = "判为自己：自家行号命中（我们自己发出去并回读确认过的库行）"
            elif echo_hit:
                why = "判为自己：文本回声窗命中"
            elif out:
                why = "保留（当别人的消息处理）"
            else:
                why = "跳过（系统/空内容/其它过滤）"
            _LEDGER.append({
                "ts": int(time.time() * 1000), "chat": str(chat_id or ""),
                "local_id": raw.get("local_id"), "mtype": str(raw.get("type") or ""),
                "sender_id": raw.get("sender_id"), "sender_wxid": _mask_id(sender_wxid),
                "self_wxid_hit": self_hit, "echo_hit": echo_hit, "self_local_hit": bool(sl_hit),
                "keep": bool(out), "why": why, "text": text[:60],
            })
            try:
                p = os.path.join(ROOT, "data", "message_ledger.jsonl")
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "a", encoding="utf-8") as fh:
                    fh.write(_json.dumps(_LEDGER[-1], ensure_ascii=False) + "\n")
            except Exception:
                pass
        except Exception:
            pass

    def normalize(self, raw: dict, chat_id: str | None = None):
        """把 wechatauto 原始消息归一化（外面包一层台账，见 `_note_ledger`）。返回 None＝应跳过。"""
        out = self._normalize_impl(raw, chat_id)
        try:
            self._note_ledger(chat_id, raw, out)
        except Exception:
            pass
        return out

    def _normalize_impl(self, raw: dict, chat_id: str | None = None):
        """把 wechatauto 原始消息归一化。返回 None 表示应跳过（自己/系统）。"""
        mtype = str(raw.get("type") or "")
        local_id = raw.get("local_id")
        create_time = raw.get("create_time") or 0
        sort_seq = raw.get("sort_seq") or 0
        sender_id = raw.get("sender_id")
        content = raw.get("content") or ""
        if isinstance(content, bytes):
            content = content.decode("utf-8", "ignore")
        # ⛔ 正文还原（2026-09-14，实测）：微信 4.x 把**长文本与文件卡**的 content **zstd 压缩**存库，
        #   而库的友好化读法遇到压缩体只给**类型标签**（`[文本]` / `[文件/链接/卡片]`）⇒ 正文整条丢掉。
        #   既有口径：障正是这个：「我每次都是把你的话复制到微信发过去，随后你就不太能正常识别了」——
        #   他粘过来的是长段落 ⇒ 全部读成 `[文本]`、机器人根本没看见。这里在**最前面**解一次，
        #   后面所有分支（撤回解析/引用解析/文本归一）都吃到真正文。解不出来就保持原样（绝不猜）。
        try:
            if isinstance(content, str) and re.match(r"^\[[^\[\]]{1,16}\]$", content.strip()):
                content = replica_adapter.fill_text(self._db, chat_id or "", local_id, content) or content
        except Exception:
            pass

        # ── 撤回事件（第三方 v0.4 对账清单第 14 条）────────────────────────
        # 必须放在"自己发的消息跳过"**之前**：自己撤回时 sender_id 同样是 2/3，
        # 若先跳过就会把撤回事件静默丢掉（原来正是如此 ⇒ 已进上下文的撤回消息没人清）。
        _ts_ms = int(create_time) * 1000 if create_time and create_time < 1e12 else int(create_time or 0)
        _rec = None
        if isinstance(content, str) and content.strip():
            try:
                _cand = recall_mod.parse_recall(content, self._self_wxid)
            except Exception as e:
                log.debug("撤回识别异常：%s", e)
                _cand = None
            # 防误判：纯文本形态只在**系统消息**里认 —— 否则群友恰好打出一句
            # 「张三 撤回了一条消息」就会被当成撤回事件、把上一条别人说的话删掉。
            # XML（`<sysmsg type="revokemsg">`）形态本身无歧义，不看类型。
            if _cand and (_cand.get("form") == "xml" or mtype == "系统消息"):
                _rec = _cand
        if _rec:
            _rec = dict(_rec, ts=_ts_ms or int(time.time() * 1000))
            return {
                "mid": local_id,
                "ts": _rec["ts"],
                "sort_seq": sort_seq,
                "sender_id": "",          # 撤回事件的 sender_id 不可靠（自己/别人都是 2/3）
                "sender_name": "",
                "text": "",
                "media": [],
                "mtype": "系统消息",
                "recall": _rec,
            }

        # ── 🔴 2026-09-18 加：**自家行号判自己**（最强的一档证据，见 `is_self_local`）──────────
        #    位置有讲究：**必须放在撤回分支之后**（自己撤回的事件要照旧处理），而在下面所有文本类判据
        #    之前 —— 行号是唯一**不依赖文本**的证据：发图/发表情/发文件回读出来的 text 是
        #    `[图片]`/`[表情]`，文本类判据永远认不出自己（用户反馈「他有时候还是会把自己识别成别人」）。
        #    不命中就照旧往下走（没有记录＝不判自己），所以这条**只会减少"回自己"，不会丢别人的话**。
        try:
            if self.is_self_local(chat_id, local_id, create_time):
                return None
        except Exception as e:
            log.debug("自我行号判定异常（按未命中继续）：%s", e)

        # 自己发的消息跳过（避免自问自答）
        # 🔴 2026-09-17 修（用户「佬」实测反馈：「**别人说话它没反应；机器人自己发一句它就有反应，
        #    还回了自己一句** 好这玩意差点酿成大祸」）：这里原来写死
        #       `if str(sender_id) in ("2", "3"): return None`
        #    注释自己就写着"微信 4.x 群聊里 real_sender_id 不可靠" —— 它是**每个会话内部的本地序号**
        #    （不同机器/不同会话都可能不同）。在那台机器上**别人的 sender_id 正好是 2/3** ⇒
        #    别人的消息被整片丢光、只剩机器人自己的消息能进来 ⇒ 表现就是"只认自己说的话 + 回自己"。
        #    ⇒ 现在**只按证据判自己**（下面三档），绝不按写死的 id 判：
        #      ① `self_wxid` 命中（最强证据）② 昵称一致 + 我 30 秒内确实发过 ③ 文本回声窗口。
        #    宁可漏判一次回声（③ 还兜着），也不能把**别人的话**丢掉。
        _eid = str(sender_id or "").strip()
        _drop_by_id = False                        # 保留这个变量只为把老行为"记下来"，不再据此跳过
        if _eid in ("2", "3"):
            try:
                _drop_by_id = True
                if not getattr(self, "_id_note_done", False):
                    self._id_note_done = True
                    log.warning("注：这条消息的 sender_id=%s（老版本会据此当成「自己」直接丢掉）——"
                                "现在只按 self_wxid/昵称/回声判自己，不再按这个号丢消息", _eid)
            except Exception:
                pass
        # 系统消息：只保留「拍一拍」事件，其余（撤回/进群/邀请等）跳过
        if mtype in ("系统消息",):
            raw_text = str(content or "")
            if "拍了拍" in raw_text or "拍一拍" in raw_text:
                m = re.search(r"([\u4e00-\u9fa5A-Za-z0-9_@\-\s]{1,24})拍了拍", raw_text)
                poker = m.group(1).strip() if m else ""
                ts = int(create_time) * 1000 if create_time and create_time < 1e12 else int(create_time or 0)
                return {
                    "mid": local_id,
                    "ts": ts or int(time.time() * 1000),
                    "sort_seq": sort_seq,
                    "sender_id": "",
                    "sender_name": poker or "某人",
                    "text": "[拍一拍]" + ("（%s）" % poker if poker else ""),
                    "media": [],
                    "mtype": "系统消息",
                }
            # 认不出的系统消息：疑似撤回就留证（下次照它把解析器补齐，不猜、也不静默）
            try:
                recall_mod.note_unmatched(raw_text, {"chat": chat_id, "mtype": mtype,
                                                     "create_time": create_time})
            except Exception:
                pass
            return None

        ts = int(create_time) * 1000 if create_time and create_time < 1e12 else int(create_time or 0)

        sender_wxid = ""
        text = ""
        media = []
        parsed = None  # 「文件/链接/卡片」解析结果（引用/拍一拍），其他分支不涉及
        if mtype == "文本":
            m = _SENDER_RE.match(content)
            if m:
                sender_wxid = m.group(1)
                text = content[m.end():].strip()
            else:
                text = content.strip()
            # 自己发的消息：发送者 wxid 是机器人自己 → 跳过（群聊 sender_id 不可靠，用 wxid 兜底）
            if self._self_wxid and sender_wxid and sender_wxid == self._self_wxid:
                return None
            # ⛔ 2026-09-16 兜底（已知现象：「无法识别大号用户 / 无法识别我的账号」）：
            #   认不出自己的 wxid 时，退一步用**昵称 + 我刚发过东西**双重条件来判断 ——
            #   单看昵称会误伤同名群友（把别人的话丢掉＝漏回），所以再加一条"我确实在 30 秒内发过"。
            #   宁可少数漏判（回声还有 `_is_self_echo` 的文本+时间窗兜着），也不要因为认不出自己而**回自己**。
            if (not self._self_wxid) and self._self_nickname and sender_wxid:
                try:
                    _sn = str((self._nick_map or {}).get(sender_wxid) or "").strip()
                    _now = time.time()
                    _recent = any((_now - float(_ts)) < self._echo_window()
                                  for _t, _ts in (self._recent_sent or []))
                    if _sn and _sn == self._self_nickname.strip() and _recent:
                        return None
                except Exception:
                    pass
        elif mtype == "图片":
            sender_wxid = str(sender_id or "") if sender_id not in (0, 2, None) else ""
            text = "[图片]"
            media = [{"kind": "image", "local_id": local_id}]
        elif mtype == "动画表情":
            # media 携带 local_id 供「收藏表情包」工具截图入收藏夹
            text = "[表情]"
            media = [{"kind": "emoji", "local_id": local_id}]
        elif mtype == "语音":
            # media 携带 local_id 供「语音转文字」工具按需下载+本地识别（agent/voice.py）
            text = "[语音]"
            media = [{"kind": "voice", "local_id": local_id}]
        elif mtype == "视频":
            text = "[视频]"
            media = [{"kind": "video", "local_id": local_id}]
        elif mtype == "位置":
            text = "[位置]"
        elif mtype == "文件/链接/卡片":
            # 可能是「引用图片/文本」的引用消息：尝试解压解析出被引用内容
            parsed = None
            if chat_id:
                parsed = self._parse_quote(chat_id, local_id)
            if parsed:
                text = parsed.get("text") or "[引用消息]"
                media = parsed.get("media") or []
                if parsed.get("sender_wxid"):
                    sender_wxid = parsed["sender_wxid"]
            else:
                # 合并转发/多条目卡片：解析子消息摘要
                fc = self.parse_forward_card(chat_id, local_id)
                if fc.get("kind") == "merge":
                    items = fc.get("items") or []
                    brief = " / ".join(i["title"][:30] for i in items[:4])
                    text = "[合并转发] %s" % (brief if brief else "查看聊天记录")
                    media = [{"kind": "merge", "local_id": local_id}]
                else:
                    text = "[文件/链接/卡片]"
                    media = [{"kind": "file", "local_id": local_id}]
        elif mtype == "红包":
            text = "[红包]"
        else:
            text = "[%s]" % mtype

        if not text.strip():
            return None

        # 回声过滤：这条消息是自己刚发出去的（文本完全一致）→ 跳过，避免自问自答死循环
        if text and self._is_self_echo(text):
            # ⭐ 2026-09-18 加：**从回声里学会"我"是谁**。
            #   起因（用户「佬」追问：「我们都有@自己的方法了，为什么他还回自己的话？」）：
            #   我们发出去的消息会写回数据库，**那条记录里的 sender_wxid 就是机器人自己** ⇒
            #   这里正好是"最可靠的自我识别样本"。学会之后，任何人（包括用户**手打**用机器人号
            #   发的消息——它不在我们的发送台账里、回声窗认不出）用那个号发言都会被正确跳过，
            #   而不再依赖有时拿不到的 `get_self_info()`。
            self.learn_self_from_echo(sender_wxid, text)
            return None

        sender_name = self._nick_map.get(sender_wxid, sender_wxid) if sender_wxid else (
            self._nick_map.get(str(sender_id), str(sender_id)) if sender_id else "群成员")

        # ⛔ 2026-09-16（已知现象：「这个是用的我的小号 他无法识别我的大号 之前版本也有这个问题」）：
        #   机器人跑在**小号**上，而主人的**大号**在群里说话 ⇒ 程序原先把大号当**普通群友**
        #   （于是会去回你自己的话）。这里按登记表认一次（wxid 优先、昵称兜底）：
        #     mode=skip ⇒ 直接跳过（完全不回复）；mode=know ⇒ 照常回，但打 owner 标记让模型知道"这是主人"。
        _is_owner = False
        try:
            _is_owner = bool(self.is_owner(sender_wxid or str(sender_id or ""), sender_name))
        except Exception:
            _is_owner = False
        if _is_owner and str(getattr(self, "_owner_mode", "know")) == "skip":
            return None

        return {
            "mid": local_id,
            "ts": ts or int(__import__("time").time() * 1000),
            "sort_seq": sort_seq,
            "sender_id": sender_wxid or str(sender_id or ""),
            "sender_name": sender_name,
            "text": text,
            "media": media,
            "mtype": mtype,
            "poker_wxid": (parsed.get("poker_wxid") if parsed else ""),
            "owner": _is_owner,
        }

    # ── 发送 ─────────────────────────────────────────────────────────────

    def _get_gui(self):
        if self._gui is None:
            # ⚠️ 2026-09-18：先把驱动库的已知缺名字补上（`guia.py` 漏 import threading ⇒
            #   一旦触发"输入框探测连续失败 → 自动重新校准布局"就必然崩在那行、校准永远不生效）。
            #   必须在建 WeChatGUI 之前打（校准发生在 GUI 内部）。幂等，重复调用无害。
            try:
                from . import replica_adapter as _ra
                _ra.patch_driver_quirks()
            except Exception:
                pass
            # 🔴 2026-09-18（第二次被骂「你又在那儿把窗口往前放」后补的**真闸**）：
            #   **必须在构造之前把闸上到类上**——`WeChatGUI.__init__` 里就有
            #   `if calibrate: self.calibrate_layout()`，而 `_load_layout()` 在"窗口尺寸与上次校准
            #   差 >15%"时**拒绝采用**（限位改过尺寸就会触发）⇒ 每次新进程构造 GUI 都可能直接在
            #   `__init__` 里走到 `calibrate_layout() → bring_to_front()`（HWND_TOPMOST + 抢前台）。
            #   那时**实例级 `harden_gui` 还没装上** ⇒ 只包实例挡不住它。⇒ 先上类闸，再造实例。
            try:
                from . import ui_adapt as _ua
                _ua.harden_gui_class()
            except Exception:
                pass
            from wechatauto.guia import WeChatGUI
            try:
                self._gui = WeChatGUI()
            except Exception:
                # 主窗不可见（最小化/隐藏/被收进托盘）→ **按进程 + 窗口类**把它不激活地找回来，再重试一次。
                # ⛔ 2026-09-18 修（真 bug，现场踩到）：老代码这里匹配的是 **窗口标题 == "微信"**，
                #   而微信 4.x 的窗口标题显示的是**当前会话/昵称**（本机实测是「群deepseek」= 机器人在
                #   微信里的昵称）⇒ 这个兜底**永远找不到主窗**、永远救不回来（现象：`未找到微信主窗口` →
                #   `WeChatError: 微信主窗口不可见（恢复失败）`）。改成按 **Weixin.exe/WeChat.exe 进程下的
                #   `Qt51514QWindowIcon` 大类窗口**（宽度 > 600）来认定主窗——判据与标题无关。
                try:
                    import ctypes
                    from ctypes import wintypes
                    u = ctypes.windll.user32
                    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
                    found = []

                    def _has_render_child(h) -> bool:
                        """主窗的判定特征：带一个 `MMUIRenderSubWindowHW` 子窗（微信专属，见 AGENTS §2 的
                        "不能用 FindWindow(类名) 找主窗"那条坑——同名类还有朋友圈编辑窗）。"""
                        hit = []

                        def _cc(ch, _l):
                            cn2 = ctypes.create_unicode_buffer(256)
                            u.GetClassNameW(ch, cn2, 256)
                            if str(cn2.value) == "MMUIRenderSubWindowHW":
                                hit.append(1)
                                return False
                            return True

                        try:
                            u.EnumChildWindows(int(h), CB(_cc), 0)
                        except Exception:
                            return False
                        return bool(hit)

                    def cb(h, l):
                        try:
                            cn = ctypes.create_unicode_buffer(256)
                            u.GetClassNameW(h, cn, 256)
                            if not str(cn.value).startswith("Qt51514QWindowIcon"):
                                return True
                            r = wintypes.RECT()
                            u.GetWindowRect(h, ctypes.byref(r))
                            if (int(r.right) - int(r.left)) > 600 and _has_render_child(h):
                                found.append(h)
                        except Exception:
                            pass
                        return True

                    _ref = CB(cb)
                    u.EnumWindows(_ref, 0)
                    if found:
                        # ⛔ 2026-09-15 改：原来这里是 `ShowWindow(hwnd, 9)`（SW_RESTORE，**会激活窗口**）
                        #    + `SetForegroundWindow`（**抢前台**）——正是既有口径：障过的"一打开就把我的微信切出来"。
                        #    改成**不激活地**还原（不动光标），与三条投递链同一套实现。
                        log.info("微信主窗被隐藏/最小化 ⇒ 按进程+窗口类找回并**不激活地**还原：hwnd=%s", found[0])
                        self._ensure_main_visible(None, int(found[0]))
                        time.sleep(0.3)
                    else:
                        log.info("主窗兜底枚举没找到候选（微信进程/窗口类都没命中）")
                    self._gui = WeChatGUI()
                except Exception:
                    raise WeChatError("不可用：微信主窗口不可见（恢复失败）。请打开电脑微信后重试。")
            # 🔴 2026-09-18（作者发火后立）：**GUI 一建好立刻上闸**——因为下面那次校准、
            #   以及库里 `get_input_box()` 探针失败后的自动重校准，都会走
            #   `bring_to_front()`（`SetWindowPos(HWND_TOPMOST)` + SetForegroundWindow）
            #   **把微信顶到所有窗口之上**（连用户用来遮挡的浏览器都压得住）。
            try:
                from . import ui_adapt as _ua2
                _ua2.harden_gui(self._gui)
            except Exception:
                pass
            try:
                self._limit_wechat_window(self._gui)
            except Exception:
                pass
            try:
                if self._gui.desktop_available():
                    from . import ui_adapt as _ua3
                    _fg_ok, _fg_why = _ua3.fg_allowed()
                    if _fg_ok:
                        self._gui.calibrate_layout(save=True)
                    else:
                        log.info("跳过启动布局校准（%s）—— 它内部 bring_to_front 会置顶，"
                                 "投递档不需要前台", _fg_why)
            except Exception:
                pass
            self._install_ui_patches(self._gui)
        return self._gui

    def _limit_wechat_window(self, gui) -> None:
        """微信窗口限位（**默认不做**）：把主窗 MoveWindow 到 1160×900，
        避免 wechatauto 因"当前尺寸与校准差异过大(>15%)"而忽略布局（坐标漂移根源）。

        ⛔ 2026-09-16 修（用户报「他都找不到微信，还得我切出来」）：**这个函数从来不看
        `ui.lock_window_pos`** —— 那个开关的界面文案是「固定微信窗口位置」、注释写着
        「默认关：不动用户的窗口」，可代码**每次取 GUI 都把用户的微信钉到 1160×900、
        还把窗口下移到 y≥40**，等于开关名不副实（他 config.json 里就是 `false`，窗口照样被改）。
        ⇒ 现在按开关走：**只有 `ui.lock_window_pos=True` 才限位**（**2026-09-16 用户把它改成默认开**：
        「你在后台都不在意这个，而且也能防止点错」；关掉＝一行都不碰用户的窗口，尺寸不合就靠紧跟其后的
        `calibrate_layout(save=True)` 按当前尺寸重新校准）。限位时仍是"借 → 空闲自动还"（见 `agent/window_borrow.py`）。
        """
        try:
            from .config import get_config as _gc
            if (_gc().get("ui") or {}).get("lock_window_pos", False) is not True:
                return                  # 默认：不动用户的窗口（读不到配置也按"不动"处理）
        except Exception:
            return
        try:
            import ctypes
            from ctypes import wintypes
            from . import window_borrow as _wb
            hwnd = int(gui.main_hwnd)
            u = ctypes.windll.user32
            r = wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            sw = int(u.GetSystemMetrics(0))
            sh = int(u.GetSystemMetrics(1))
            # 目标尺寸：宽 1160；高 900（微信 PC 侧栏在高度不足时会把部分图标收进"…"省略号，
            # 导致"发现/朋友圈"等不可见——900 高保证全部侧栏图标常显；小屏按比例收缩但不低于 820）
            tw = 1160 if sw >= 1366 else int(sw * 0.82)
            th = min(900, max(820, sh - 100))
            _wb.touch()
            # 2026-09-17 用户投诉「为什么老是把我的窗口改得那么大」⇒ 加档位：默认**只缩不放**。
            #   off         ＝完全不碰
            #   shrink_only ＝只在当前窗口**大于**标定尺寸时缩到标定；用户调小的窗口**不动**
            #   force       ＝双向强制拉到标定（老行为）
            _mode = str((self.cfg.get("wechat", {}) or {}).get("limit_window") or "shrink_only").strip().lower()
            if _mode not in ("off", "shrink_only", "force"):
                _mode = "shrink_only"
            _cw, _ch = int(r.right - r.left), int(r.bottom - r.top)
            _need = (_cw != tw or _ch != th) if _mode == "force" else (_cw > tw or _ch > th)
            if _mode == "off":
                _need = False
            if _need:
                _wb.note_original(hwnd, (r.left, r.top, r.right, r.bottom))
                _ny = max(40, r.top)
                _nw = tw if _mode == "force" else min(_cw, tw)
                _nh = th if _mode == "force" else min(_ch, th)
                # ⛔ 2026-09-18 修（**作者原话：「我一直在把浏览器往上放」**——他往上放、我往上顶）：
                #   原来是 `u.MoveWindow(hwnd, ..., True)`。**`MoveWindow` 对顶层窗会"激活"它**
                #   （等价于不带 `SWP_NOACTIVATE` 的 `SetWindowPos`）⇒ 每次取 GUI 限位都**把微信顶到
                #   浏览器前面**。而本函数在**每次 `_get_gui()`** 都会跑（`ui.lock_window_pos=True` 时），
                #   于是"每起一个探针/每个会话"都顶一次。他看见的就是"窗口一直在到前台"。
                #   ⇒ 改成 `SetWindowPos(...SWP_NOZORDER|SWP_NOACTIVATE)`：**只改几何，绝不置前、不改 Z 序**。
                try:
                    _sp = u.SetWindowPos
                    try:
                        _sp.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_uint]
                        _sp.restype = wintypes.BOOL
                    except Exception:
                        pass          # 挂 argtypes 失败也必须照发，别把限位整个吞掉
                    _ok = bool(_sp(int(hwnd), 0, int(r.left), int(_ny), int(_nw), int(_nh),
                                   0x0004 | 0x0010))     # SWP_NOZORDER | SWP_NOACTIVATE
                    if not _ok:
                        log.info("限位：SetWindowPos 没成功（不重试、不置前）")
                except Exception as e:
                    log.info("限位：SetWindowPos 异常（按不动处理）：%s", e)
                _wb.note_forced((r.left, _ny, r.left + _nw, _ny + _nh))
                time.sleep(0.4)
                try:
                    gui.refresh()
                except Exception:
                    pass
            elif _mode == "shrink_only" and (_cw < tw or _ch < th):
                log.info("窗口规整：只缩不放 —— 你的窗口 %dx%d 比标定 %dx%d 小，保持你的尺寸不动它",
                         _cw, _ch, tw, th)
        except Exception:
            pass

    def _install_ui_patches(self, gui):
        """给 GUI 实例装「界面适配」补丁（每个实例只装一次）。

        把 wechatauto 内部的 wx_click / ensure_visible / 回车发送 换成适配层版本：
          · wx_click / ensure_visible → DPI 缩放 + 清理遮挡层/系统叠加层 + 点击归属校验；
          · 回车（VK_RETURN）加 1.5 秒冷却 → 防「发送后输入框未及时清空 → 重试回车」
            把同一条消息发两遍（库内部的重试也会被拦住；分条连发间隔 3~5 秒不受影响）。
        这样换电脑（带缩放/多显示器/触屏手写画布）也不用改 wechatauto。
        """
        if getattr(gui, "_persona_morph_ui_ok", False):
            return
        try:
            from . import ui_adapt
            orig_ensure = gui.ensure_visible
            orig_click = gui.wx_click
            orig_key = getattr(gui._input, "key", None)
            adapter = self
            _last_enter = [0.0]

            def ensure_visible(*a, **kw):
                try:
                    if ui_adapt.prepare_screen(gui):
                        return True
                except Exception:
                    pass
                try:
                    return orig_ensure(*a, **kw)
                except Exception:
                    return False

            def wx_click(x, y, right=False):
                sx, sy = ui_adapt.to_click(x, y)
                ok, why = ui_adapt.ensure_point(sx, sy, (gui.main_hwnd, gui.render_hwnd))
                if not ok:
                    raise WeChatError("点击被拦截：%s" % why)
                orig_click(sx, sy, right=right)

            def key(vk, ctrl=False, shift=False):
                if int(vk) == 0x0D:  # VK_RETURN
                    now = time.time()
                    if now - _last_enter[0] < 1.5:
                        return  # 1.5 秒内补按的回车 = 重复发送竞态，拦掉
                    _last_enter[0] = now
                return orig_key(vk, ctrl=ctrl, shift=shift)

            gui.ensure_visible = ensure_visible
            gui.wx_click = wx_click
            if orig_key is not None:
                gui._input.key = key
            gui._persona_morph_ui_ok = True
        except Exception:
            pass

    def _dedup_send(self, chat_id: str, text: str) -> bool:
        """3 秒内对同一会话发送完全相同的文本 → 视为重复点击重试，直接跳过。

        微信 UIA 发送的「输入框未及时清空 → 重试回车」会把同一条发两遍，
        这里做硬拦截（正常没人会在 3 秒内发两条一模一样的）。
        """
        t = str(text or "").strip()
        if not t:
            return True
        now = time.time()
        key = (chat_id, t)
        while self._send_recent and now - self._send_recent[0][2] > 30:
            self._send_recent.popleft()
        for ck, ct, ts in self._send_recent:
            if ck == key[0] and ct == key[1] and (now - ts) < 3.0:
                return False
        self._send_recent.append((key[0], key[1], now))
        return True

    # ── 前台管控：整批发送只置前一次，全部发完才恢复用户窗口 ─────────────
    _fg_depth = 0
    _fg_before = None  # 用户窗口（发送前的前台，非微信）

    def fg_hold(self):
        """上下文管理器：批量发送期间「持有前台权」。

        进入：depth=0 时记录用户当前前台窗口（若用户正在看微信则不记录别动）；
        退出：depth 归零时恢复用户窗口 + 取消微信置顶 + 兜底放底/最小化。
        中途任何一条发送成功/失败都会走到退出（finally）。
        """
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            try:
                self._fg_enter()
                yield
            finally:
                self._fg_exit()
        return _ctx()

    def _fg_enter(self):
        if self._fg_depth == 0:
            try:
                gui = self._get_gui()
                import ctypes as _ct
                fg = int(_ct.windll.user32.GetForegroundWindow() or 0)
                self._fg_before = None if (fg and fg in (gui.main_hwnd, gui.render_hwnd)) else fg
            except Exception:
                self._fg_before = None
        self._fg_depth += 1

    def _fg_exit(self):
        self._fg_depth = max(0, self._fg_depth - 1)
        if self._fg_depth > 0:
            return
        before = self._fg_before
        self._fg_before = None
        try:
            self._restore_after_send(before)
        except Exception:
            pass

    def _restore_after_send(self, before_fg):
        """发送完成后把微信送回后台：取消置顶 → 恢复用户窗口 → 兜底放底/最小化。"""
        gui = self._get_gui()
        import ctypes as _ct
        user32 = _ct.windll.user32
        wechat_ok = (int(user32.GetForegroundWindow() or 0) in (gui.main_hwnd, gui.render_hwnd))
        # 1) 取消置顶（prepare_screen 用了 keep_topmost=True，微信会一直压在顶上）
        try:
            gui.restore_zorder()
        except Exception:
            pass
        time.sleep(0.15)
        # 2) 微信仍是前台（发送成功的通常情况）→ 恢复用户之前用的窗口
        fg = int(user32.GetForegroundWindow() or 0)
        if fg in (gui.main_hwnd, gui.render_hwnd) and before_fg:
            _force_foreground(user32, before_fg)
            time.sleep(0.25)
        # 3) 兜底：恢复失败（受前台锁/窗口已关）→ 微信放到 Z 序底层即可。
        #    ⛔ 这里**不许再裸 `SW_MINIMIZE`**（2026-09-18 删）：现场现象「他还在不停地缩小，
        #       就是把微信最小化，然后又把微信切出来」——那一枪**没有安全线**（不像
        #       `_minimize_back_if_needed`：没登记过不动 / 已最小化不动 / 是前台就不动），
        #       它会把**用户自己正在用的微信**直接收进任务栏，下一次投递又得还原 ⇒ 一来一回就是"抽风"。
        #       Z 序放底已经足够（"不挡着用户"这个目的达到），且完全可逆、用户看不见。
        fg = int(user32.GetForegroundWindow() or 0)
        if fg in (gui.main_hwnd, gui.render_hwnd):
            user32.SetWindowPos(gui.main_hwnd, 1, 0, 0, 0, 0, 0x0002 | 0x0001)  # HWND_BOTTOM
            time.sleep(0.2)
            if int(user32.GetForegroundWindow() or 0) in (gui.main_hwnd, gui.render_hwnd):
                log.info("还后台：微信仍在最前且用户窗口没能抢回 ⇒ 先放到 Z 序底层")
        # 4) **带安全线的**"谁动的谁收拾"：只有"确实是为干活被我们还原出来的"（`_MINIMIZED_BY_US`
        #    登记过）才会被收回归位，且它已经最小化 / 正在被用户使用时都不动（三条安全线见
        #    `_minimize_back_if_needed`）。这一步替代了上面那条裸最小化，供 `_fg_enter/_fg_exit`
        #    这类"借了前台/还原了窗口"的链收尾（拍一拍 / 引用 / 朋友圈 / 发图…）。
        try:
            _minimize_back_if_needed("发送后收尾（_restore_after_send）")
        except Exception as _e:
            log.debug("放回收起状态失败（忽略）：%s", _e)

    def _open_chat_guarded(self, name: str, chat_id: str = "") -> bool:
        """把「确保目标会话就是当前会话」做成**投递优先**（2026-09-18 修；这是拍一拍/引用/表情的**唯一咽喉点**）。

        ⛔ 原实现**只做真鼠标闸**：真鼠标兜底关着（默认）时直接 `return False` ⇒ 这一个点上的
        7 处调用**全部失败**，而它们里面就有拍一拍（`_send_poke_inner`）、拍一拍自检、引用
        （`_reply_quote_inner`）、消息菜单（`message_menu`）、表情面板（`emoji_panel_open`）。
        现场症状（2026-09-18 拍摄）：`演示 → 回拍「E」：打开会话失败`，而**同一刻**日志明明写着
        「投递切会话：True —— 会话头标题带 OCR='演示（3）' 与目标 '演示' 匹配」。
        用户原话：「**拍一拍等等这些本身就可以右键投递吧**」。

        现在三级（能不动真鼠标就不动）：
          ① 传了 `chat_id` 且**强档证据**说"当前开着的就是目标会话" ⇒ 什么都不用做，直接 True；
          ② 否则走**投递切会话** `switch_chat_posted`（投递点击会话行 + 库只读确认）；
          ③ 两者都不成、且真鼠标**被显式允许** ⇒ 才退回真鼠标 `open_chat`；否则留日志拒绝（原口径）。

        不传 `chat_id` 的老调用点行为不变（仍只走真鼠标闸）——但**新代码一律传**。
        """
        if chat_id:
            try:
                gui = self._get_gui()
                _ok, _why = self.chat_is_open(chat_id, gui=gui)
                if _ok:
                    log.info("切会话不需要：强档证据说当前就是目标会话（%s）", str(_why)[:90])
                    return True
                _sw_ok, _sw_msg = self.switch_chat_posted(chat_id, gui=gui)
                log.info("投递切会话（拍一拍/引用/表情链）：%s —— %s", _sw_ok, str(_sw_msg)[:130])
                if _sw_ok:
                    return True
            except Exception as _e:
                log.info("投递切会话不可用（按原口径回退）：%s", _e)
        if not self._real_fallback_allowed():
            log.info("不切会话：open_chat 是真鼠标路径（会动你的光标）⇒ 按最高目标拒绝（目标=%s）", name)
            return False
        try:
            return bool(self._get_gui().open_chat(name))
        except Exception as e:
            log.info("open_chat 失败：%s", e)
            return False

    def _send_with_foreground(self, fn, *args, **kwargs):
        """发送包装：输入全程后台（UIA SetValue 直写），需要点击/回车的
        阶段才瞬时置前，发送完成后立即把微信窗口放回后台。

        与 fg_hold 配合：批量发送时只置前一次、整批发完才恢复；
        单条发送则每条发完立即恢复。恢复失败有三级兜底：
        取消置顶 → 恢复原前台窗口（AttachThreadInput 提权）→ 放底/最小化。
        """
        # ⛔ 2026-09-17 红线收口（用户实测报障：「他输入文字的时候，直接把我光标拉到那儿去了」）：
        #    **真鼠标闸放到唯一咽喉点**。以前只有 `send_text` 自己过闸，而
        #    `send_text_at`（@某人）与 `send_image`（发图）**直接落到这里** ⇒ 在
        #    `background_only=true` 的承诺下**照样动用户光标**（库的 `send_msg/at_member/send_image`
        #    本来就是 SetCursorPos + mouse_event 的真鼠标实现）。
        #    闸门语义见 `_real_fallback_allowed()`：默认关，要真鼠标必须显式打开配置。
        if not self._real_fallback_allowed():
            return {"status": "blocked",
                    "message": ("按最高目标**不退回真鼠标**（这条发送路径会动你的光标）；"
                                "要允许请打开 input.allow_real_fallback，或先让投递档确认目标会话"),
                    "data": {}}
        self._fg_enter()
        cur0 = _cursor_pos()      # 最高目标硬自检②：L0 真实路径"用完必须把光标还回去"
        try:
            result = fn(*args, **kwargs)
            return result
        finally:
            self._fg_exit()
            try:
                if cur0 and cur0 != (0, 0) and _cursor_pos() != cur0:
                    import ctypes as _ct
                    ok_cur = _ct.windll.user32.SetCursorPos(int(cur0[0]), int(cur0[1]))
                    if ok_cur:
                        log.info("真实路径结束后已把光标还原到 %s", cur0)
                    else:
                        # 实测：本机 SetCursorPos 会返回 0（失败）且 GetLastError=0 —— 此时**不要谎报已还原**，
                        # 如实记一行，方便下次定位"为什么光标没回来"（2026-09-13 记录）
                        log.warning("光标还原失败：SetCursorPos 返回 0（目标 %s，当前位置 %s）", cur0, _cursor_pos())
            except Exception as _e:
                log.info("光标还原失败（不影响发送）：%s", _e)

    def _learn_chat_header(self, chat_id: str, gui=None) -> bool:
        """DB 回读确认发送成功后，补一条"当前窗口尺寸"下的会话头参照。

        这样**真实路径发过一次之后，下一次就能走投递**（否则 no_ref 永远退回真实路径）。
        """
        try:
            from . import chat_header as _ch
            img = _ch.capture_image(gui=gui)
            if img is None:
                return False
            # 只有"面板左沿能探到"才学 —— 探不到说明这张图的口径可疑（遮挡/异形布局），
            # 学进去就会变成坏参照（2026-09-13 实测踩过：一条坏参照让同一会话长期判 0.653）
            pl = _ch.detect_pane_left(img)
            if not pl:
                log.info("自动学会话头跳过：这次没探到聊天面板左沿（口径可疑）")
                return False
            _ch.remember(chat_id, _ch.fingerprint(img),
                         note="auto-learned after DB-confirmed send", size=_ch.size_key(img))
            return True
        except Exception as e:
            log.info("补会话头参照失败（不影响发送）：%s", e)
            return False

    def _posted_preferred(self) -> bool:
        """当前配置下是否允许"投递优先"（config.input.backend = auto/message）。"""
        try:
            from . import input_backend as _ib
            return isinstance(_ib.select_backend(gui=self._get_gui()), _ib.MessageBackend)
        except Exception:
            return False

    def _real_fallback_allowed(self) -> bool:
        """投递档确认不了目标会话时，**允不允许退回真鼠标/真键盘（L0）**——默认 **False**。

        ⚠️ 2026-09-16 跨机 r12 事故（对面那台）：跑我们自己的 `一键检验（生成报告）`——那是**自检/诊断**
        工具——投递切会话失败后自动退回真实路径 ⇒ **动了 16 秒光标**（日志：`真实路径结束后已把光标还原到
        (1763,586)`）。这与最高目标②（不抢鼠标）③（不打扰你（可能短暂置前约 1~3 秒后自动还回））直接冲突，而且是在用户机器上由**只读体检**
        触发的。⇒ 三条：
          ① 默认 **False**（不退回），要真鼠标必须在 config 里显式打开 `input.allow_real_fallback=true`；
          ② 环境变量 `WXAGENT_REAL_FALLBACK=0` 可以**强制关**（自检/诊断路径用它兜底，无视 config）；
          ③ 不退回时**要说清为什么**（返回消息里带"投递档确认不了 + 没开真鼠标兜底"），不许静默失败。
        """
        try:
            if str(os.environ.get("WXAGENT_REAL_FALLBACK", "")).strip() == "0":
                return False
        except Exception:
            pass
        try:
            cfg = get_config() or {}
            return bool((cfg.get("input") or {}).get("allow_real_fallback", False))
        except Exception:
            return False

    def send_text(self, chat_id: str, text: str):
        """发送文本到群。返回 (ok, message)。

        **投递优先**（2026-09-13）：若会话头三态闸判 `ok`（确认目标会话就是当前打开的），
        直接走 `send_text_posted`（不动光标（伪激活可能短暂置前约 1~3 秒后自动还回）、不要求窗口可见）；
        否则（`mismatch`/`no_ref`/抓不到）**退回真实路径**——真实路径会先按名字打开会话，
        顺便把这个尺寸下的会话头学到手，于是**下一次就能走投递**。
        """
        # OCR 总时间窗（测机手册 ④）：这一笔发送链允许花在 OCR 上的总时间（超时按"自检不可用"处理）
        try:
            from . import chat_ocr as _co
            _co.begin_window(_co.SEND_WINDOW_S)
        except Exception:
            pass
        # W7 版本门：没实测过的版本对默认暂停自动发送（控制台可临时放行）
        try:
            from . import version_gate as _vg
            _g = _vg.check("send", wechat=wx_version_for_gate())
            if not _g["allow"]:
                _vg.note_blocked("send", _g["reason"])      # 记账：控制台横幅与日志都要看得见
                return False, _g["reason"]
        except Exception:
            pass
        if not self._dedup_send(chat_id, text):
            return True, "重复发送已拦截（3 秒内同一文本）"
        name = self.group_name(chat_id)
        _st_status = "没走投递档"
        try:
            with self._send_lock:  # 所有碰微信窗口的操作统一串行（发消息/引用/拍一拍/回拍不打架）
                gui = self._get_gui()
                if self._posted_preferred():
                    try:
                        from . import chat_header as _ch
                        st = _ch.check(chat_id, gui=gui)
                        _st_status = str(st.get("status"))
                        if st["status"] == "ok":
                            ok, msg = self.send_text_posted(text, chat_id)
                            if ok:
                                self._mark_sent(text)
                                return True, "%s（投递档 L5）" % msg
                            log.info("投递发送失败，退回真实路径：%s", msg)
                        else:
                            # 投递不切会话，但"切会话"这一步本身也能投递（投递点击会话行 + 库只读确认）。
                            # 切成功后"当前会话＝目标会话"有正面证据 ⇒ 允许 allow_no_ref=True 投递发送。
                            sw_ok, sw_msg = self.switch_chat_posted(chat_id, gui=gui)
                            log.info("投递切会话：%s —— %s", sw_ok, sw_msg)
                            if sw_ok:
                                try:
                                    self._learn_chat_header(chat_id, gui=gui)   # 顺手把该尺寸的会话头参照学到手
                                except Exception as _e:
                                    log.info("学会话头参照失败（不影响发送）：%s", _e)
                                # 🔴 2026-09-17 修（用户「佬」实测：「**点到了下面一个联系人，然后回复了在
                                #    群里的那条消息，发给了别人**」）：`switch_chat_posted` 说"切成功"**不等于**
                                #    此刻开着的就是目标会话（它自己的证据是"绿底高亮行 + 名字 OCR"，点错行时
                                #    同样可能成立）⇒ 发送前**必须再要一次内容级正面证据**。
                                #    拿不到就不发：宁可漏发一条，**绝不发错人**（发错会话是对外可见的事故）。
                                _idn, _idwhy = self.chat_identity_ok(chat_id, gui=gui)
                                if _idn is not True and _idn is not None:
                                    # 证据**说不是** ⇒ 坚决不发（防发错人）
                                    log.warning("切会话后内容级复核判否（%s）⇒ 这条不发（防发错人）", _idwhy)
                                    return False, ("切完会话后**内容级复核说不是这个会话**（%s）⇒ 这条不发 —— "
                                                   "宁可漏发，绝不发错人。" % str(_idwhy)[:60])
                                if _idn is None:
                                    # ⚠️ 2026-09-18 修（拍摄现场实测：「他点对了会话，然后待在那啥也不干、不发图」）：
                                    #   原来这里把"拿不到证据"也当成拒发 ⇒ **最近消息全是图/表情/语音的群永远发不出去**
                                    #   （演示群实测：可比对文本 0 条）。⇒ 拿不到文本证据时**退回名字这一档**：
                                    #   要求"绿底高亮行 + 名字与目标名精确命中"（这已是名字类里最强的一档），
                                    #   过了就放行并**记账留痕**；不过仍然拒发。
                                    _nm_ok, _nm_why = self.chat_is_open(chat_id, gui=gui, name=name)
                                    if not _nm_ok:
                                        log.warning("无可比对文本，且名字档也没确认（%s）⇒ 这条不发", _nm_why)
                                        return False, ("这个会话里没有能比对的文字（%s），名字档也没确认（%s）⇒ "
                                                       "这条不发。把目标会话在微信里点开、或让它有一条文字消息，再重试。"
                                                       % (str(_idwhy)[:50], str(_nm_why)[:50]))
                                    log.info("该会话没有可比对文本（%s）⇒ 按**名字精确命中**放行（记账）：%s",
                                             str(_idwhy)[:40], str(_nm_why)[:40])
                                # 授权依据＝上面的 OCR 名字确认（绿底高亮行 + 名字比对，独立于指纹闸）
                                #   ＋ 这里刚补的**内容级复核**（两道独立证据）
                                ok, msg = self.send_text_posted(text, chat_id, allow_no_ref=True)
                                if ok:
                                    self._mark_sent(text)
                                    return True, "%s（投递档 L5 · 先投递切会话 + OCR 确认）" % msg
                                log.info("投递切会话后发送失败：%s", msg)
                            log.info("投递前置未满足（%s）且投递切会话未成功", st["status"])
                    except Exception as e:
                        log.info("投递优先判定异常：%s", e)
                        _st_status = "异常:%s" % type(e).__name__
                # ⛔ 退回真鼠标/真键盘前必须过这道闸（默认关；见 `_real_fallback_allowed` 注释里的事故）
                if not self._real_fallback_allowed():
                    return False, ("投递档确认不了目标会话（会话头三态=%s，投递切会话也没成）⇒ 按最高目标"
                                   "**不退回真鼠标**（`input.allow_real_fallback` 默认关，真鼠标会动你的光标）；"
                                   "要允许请显式打开它，或先把目标会话在微信里点开" % _st_status)
                # 真实路径要真点真敲 ⇒ 先做一次遮挡预检：被别的窗口挡住时，库会重试到 40~70 秒
                # 才抛"点击被拦截"（2026-09-13 实测：被资源管理器挡住时 open_chat 花了 47.5s + UIA 探测 15.5s）。
                # 这里提前把原因说清楚，用户不用白等（预检自己会尝试把微信置前一次）。
                try:
                    from . import ui_adapt as _ua
                    rr = gui.render_rect or (0, 0, 0, 0)
                    rw_, rh_ = int(rr[2] - rr[0]), int(rr[3] - rr[1])
                    if rw_ > 0 and rh_ > 0:
                        ok_pt, why_pt = _ua.ensure_point(int(rr[0] + rw_ * 0.55), int(rr[1] + rh_ * 0.5), gui=gui)
                        if not ok_pt:
                            return False, "真实路径前置检查未过：%s" % why_pt
                except Exception as e:
                    log.info("遮挡预检跳过（不影响发送）：%s", e)
                r = self._send_with_foreground(
                    lambda g=gui: g.send_msg(text, who=name, verify=False))
                ok = bool(getattr(r, "is_success", False))
                if ok:
                    self._mark_sent(text)
                    self._learn_chat_header(chat_id, gui=gui)   # 让下次能走投递
                return ok, _resp_msg(r)
        except Exception as e:
            return False, str(e)

    def send_text_at(self, chat_id: str, member_name: str, text: str):
        """在群里 @ 成员并发送文本。返回 (ok, message)。"""
        if not self._dedup_send(chat_id, text):
            return True, "重复发送已拦截（3 秒内同一文本）"
        name = self.group_name(chat_id)
        try:
            with self._send_lock:
                gui = self._get_gui()
                r = self._send_with_foreground(
                    lambda g=gui: g.at_member(member_name, text, who=name, verify=False))
                ok = bool(getattr(r, "is_success", False))
                if ok:
                    self._mark_sent(text)
                return ok, _resp_msg(r)
        except Exception as e:
            return False, str(e)

    def display_name(self, chat_id: str) -> str:
        """会话展示名：群名 → 联系人昵称（含 filehelper→文件传输助手）→ 兜底 wxid。

        ⚠️ `group_name()` 只认**群**，对 `filehelper` / 联系人会原样返回 wxid
        （2026-09-13 实测：拿 "filehelper" 去会话列表找行必然找不到）。
        """
        try:
            g = self._group_by_wxid.get(chat_id)
            if g and g.get("name"):
                return str(g["name"])
        except Exception:
            pass
        try:
            n = (self._nick_map or {}).get(str(chat_id))
            if n:
                return str(n)
        except Exception:
            pass
        if str(chat_id) == "filehelper":
            return "文件传输助手"
        return self.group_name(chat_id)

    def current_chat_name(self, gui=None):
        """只读：OCR 判"当前打开的会话是谁"，返回 (名字, 依据)。读不到就返回 ("", 原因)。

        判据＝会话列表的**绿底高亮行**（实测微信 4.1.15.8 色值 (81,167,116)）+ 该行名字的 OCR。
        为什么不用库的 `_chat_is_open`：它实测**不稳定**（同一次调用内外结果不一致）；
        为什么不用窗口标题：标题是**浅灰细字，OCR 读不出来**（同一张图里会话列表都读得出）。
        """
        try:
            from . import chat_ocr as _co
            return _co.current_chat_name(gui=gui or self._get_gui())
        except Exception as e:
            return "", "OCR 判当前会话异常：%s" % e

    def _wx_toplevel_windows(self) -> dict:
        """微信进程当前**可见的顶层窗** `{hwnd: 标题}`（只读，用于"点完有没有冒出不该出现的窗"）。

        ⚠️ **小块窗口（悬停提示气泡 / tooltip）一律不算**：Qt 的 tooltip 也是顶层窗，鼠标一停就可能冒出来，
        把它当成"点偏了"会让正常发送被误拦。判据＝窗口**两条边都 < 200px** 就不算"面板级窗口"
        （表情面板 771×771、群信息栏、被拖出去的独立聊天窗都是几百 px 起步；tooltip 一般 100×30 上下）。
        """
        out = {}
        try:
            import win32gui
            import win32process
            main = int(getattr(self._get_gui(), "main_hwnd", 0) or 0)
            pid = 0
            if main:
                try:
                    pid = int(win32process.GetWindowThreadProcessId(main)[1])
                except Exception:
                    pid = 0

            def _cb(h, _l):
                try:
                    if not win32gui.IsWindowVisible(h):
                        return True
                    if not str(win32gui.GetClassName(h) or "").startswith("Qt"):
                        return True
                    if pid:
                        try:
                            if int(win32process.GetWindowThreadProcessId(h)[1]) != pid:
                                return True
                        except Exception:
                            return True
                    try:
                        x0, y0, x1, y1 = win32gui.GetWindowRect(h)
                        if (int(x1) - int(x0)) < 200 and (int(y1) - int(y0)) < 200:
                            return True          # tooltip 级的小窗：不算"面板"
                    except Exception:
                        pass
                    out[int(h)] = str(win32gui.GetWindowText(h) or "")[:24]
                except Exception:
                    pass
                return True

            win32gui.EnumWindows(_cb, None)
        except Exception:
            pass
        return out

    def _close_stray_window(self, hwnd: int, why: str = "") -> bool:
        """把"点偏之后冒出来的窗"安全关掉（走 `_wm_close_safe` 咽喉点：**绝不关微信主窗**）。"""
        try:
            import win32gui
            if not _wm_close_safe(int(hwnd), why):
                return False
            win32gui.PostMessage(int(hwnd), 0x0010, 0, 0)      # WM_CLOSE
            log.warning("已关掉点偏后冒出的窗口 hwnd=%s（%s）", hwnd, why)
            time.sleep(0.4)
            return True
        except Exception as e:
            log.warning("关掉冒出的窗口失败（hwnd=%s）：%s", hwnd, e)
            return False

    def _click_posted(self, backend, hwnd, pt, tag: str = "", right: bool = False,
                      allow_new: bool = False, **kw):
        """**投递点击的统一咽喉点**（会话链 / 发送链专用）。返回 `(ok, 说明)`。

        两条职责（2026-09-18 加）：
          ① **点后自检：有没有冒出不该出现的窗** —— 用户反馈「**发消息的时候总是莫名其妙打开群成员栏**」，
             最可能就是某一枪落点偏了、点到了会话头（那一下就会弹出群信息/群成员栏），而旧代码**照旧往下走**
             （于是越走越乱、还卡）。现在点完立刻比一次微信顶层窗：多出新的（本次没预期的）窗
             ⇒ 记下**是哪一枪（tag + 落点）**、存现场照片、用安全关窗关掉它、返回失败让整条链**立刻停手**；
          ② 点击收敛到一处 ⇒ 落点与结果可留痕，下次现场能直接对账。
        ⚠️ 预期会出现新窗的那几枪必须显式写 `allow_new=True`（搜索浮层 / 表情面板 / 会话行双击独立窗）。
        """
        before = None if allow_new else self._wx_toplevel_windows()
        try:
            if right:
                ok, why = backend.click(hwnd, pt, right=True, **kw)
            else:
                ok, why = backend.click(hwnd, pt, **kw)
        except Exception as e:
            return False, "%s 点击异常：%s" % (tag, e)
        if allow_new or before is None:
            return bool(ok), why
        try:
            after = self._wx_toplevel_windows()
            new = [h for h in after if h not in before]
        except Exception:
            return bool(ok), why
        if not new:
            return bool(ok), why
        info = "、".join("%s（hwnd=%s）" % (str(after.get(h))[:14] or "(无标题)", h) for h in new[:3])
        log.error("❗点击「%s」（落点 %s）之后冒出了新窗口：%s —— 疑似点偏（点到会话头/别的面板）⇒ 停手",
                  tag, tuple(pt), info)
        try:
            from . import chat_header as _ch
            # ⚠️ 2026-09-18 晚：取图**单独 try**——原来取图一失败（微信没在跑/窗口不见了）整条留证
            #    都被跳过，判据 `click_guard_selftest` 的"留了现场"当场假红（现场：微信关着跑全套）。
            #    现场的价值主要在 `probe.json`（tag/落点/新窗清单），没有帧也要留。
            _img = None
            try:
                _img = _ch.capture_image(gui=self._get_gui())
            except Exception as _e_cap:
                log.debug("点偏现场取图失败（probe.json 照留）：%s", _e_cap)
            self._dump_fail_shot("stray_%s" % (re.sub(r"[^0-9A-Za-z_]+", "_", tag) or "click"),
                                 _img,
                                 {"tag": tag, "落点": list(pt), "新的窗": info,
                                  "expect": "这一枪点完不该出现新窗口"})
        except Exception as _e:
            log.debug("点偏现场留证失败：%s", _e)
        for h in new[:2]:
            self._close_stray_window(int(h), "点击「%s」后冒出的窗口" % tag)
        return False, ("❗点击「%s」（落点 %s）之后屏幕上多出了新窗口：%s —— 这一枪**点偏了**，"
                       "已把冒出来的窗关掉并停手（不在多了个面板的状态下继续投）" % (tag, tuple(pt), info))

    def chat_is_open(self, chat_id: str, gui=None, name: str = None, allow_weak: bool = False):
        """只读：当前打开的会话是不是 chat_id。返回 (bool, 说明)。

        ⚠️ 2026-09-16 r25（对面实测）：**档位分强弱，分界＝有没有区分力**。
          · **强档**（能回答"现在是谁"）：① 名字 OCR ② **会话头标题带 OCR** ③ 高亮行时间×DB；
          · **弱档**：④ 会话头指纹 —— 对面**原样复现了它的假阳性**：当前明明开着「E」，
            `chat_is_open("filehelper")` 也返回 **True**（两个会话同时 True）。
          ⇒ **指纹档单独不成立**：只在 `allow_weak=True`（只读探针、辅助判断）时才采信；
            **授权写动作的最后一道闸绝不接它**（默认 `allow_weak=False`）。
          ⇒ 顺序也据此改：先问有区分力的三档，指纹档放最后且默认不采信。
        （原注保留）OCR 拿不到帧时**不要直接判否**（否则会把"其实开着"误判成"没开"⇒ 白白退回真鼠标路径）。
        """
        want = name or self.display_name(chat_id) or chat_id
        got, why = self.current_chat_name(gui=gui)
        try:
            from . import chat_ocr as _co
            if got and _co.matches(got, want):
                return True, "当前会话 OCR=%r（目标 %r）· %s" % (got, want, why)
        except Exception:
            pass
        # ② **会话头标题带 OCR**（提到第二位）—— 对面 r25 实测它有区分力
        #    （'OE' vs 'O文亻牛传输助手'，一次就能判定当前是谁），可当首选档用；
        #    r24 那次"四档兜底放行"命中的也正是这一档。
        try:
            from . import chat_ocr as _co2
            _im4 = _co2.capture_best(gui=gui or self._get_gui(), frames=2)
            _tt = _co2.header_text(_im4) if _im4 is not None else ""
            if _tt and _co2.matches(_tt, want):
                return True, ("会话头标题带 OCR=%r 与目标 %r 匹配（不依赖活动行时间/指纹参照）"
                              % (_tt[:16], want))
        except Exception:
            pass
        # ③ 高亮行时间 × DB（有区分力：那个时刻在会话列表里必须唯一）
        try:
            _ok3, _why3 = self._active_row_time_ok(chat_id, gui=gui)
            if _ok3:
                return True, _why3
        except Exception:
            pass
        # ④ **弱档：会话头指纹**（会假阳性 ⇒ 默认不采信）
        try:
            from . import chat_header as _ch
            st = _ch.check(chat_id, gui=gui)
            if st.get("status") == "ok":
                if allow_weak:
                    return True, "会话头指纹判 ok（**弱档**：对面 r25 实测它对不同会话也会判 True）"
                return False, ("只有会话头指纹档成立（**弱档、会假阳性**：对面 r25 实测两个不同会话"
                               "同时判 True）⇒ 不足以确认当前会话，按**未确认**处理")
        except Exception:
            pass
        return False, "当前会话 OCR=%r（目标 %r）· %s" % (got, want, why)

    def _ensure_main_visible(self, gui, main: int) -> bool:
        """主窗被最小化时**不激活地**还原（不动光标（伪激活可能短暂置前约 1~3 秒后自动还回）），让"抓图类判据"能工作。

        2026-09-15 实测（`_scratch/restore_probe.py`）：`ShowWindow(SW_SHOWNOACTIVATE)`
        + `SetWindowPos(…SWP_NOMOVE|NOSIZE|NOZORDER|NOACTIVATE|SHOWWINDOW)` 能把最小化的微信
        还原成可抓图状态（抓图立刻恢复 `ok (1139,890)`），且**前台 134730→134730、光标未动**。
        ⛔ 不用 `SW_RESTORE`（会激活窗口）配 `SetForegroundWindow`（抢前台）——那是 `_get_gui()`
        自愈分支的老毛病，正是用户报障过的"一打开就把我的微信切出来"。
        关掉 `wechat.restore_minimized` 就退回"如实拒绝"。
        """
        try:
            import ctypes as _ct
            u = _ct.windll.user32
            if not u.IsIconic(int(main)):
                return False
            from .config import get_config
            cfg = (get_config() or {}).get("wechat", {}) or {}
            if not bool(cfg.get("restore_minimized", True)):
                # 2026-09-15 接线：`wechat.minimize_warning` 原来是**死键**（config 里有、控制台有、
                # 没有任何业务代码读它，所以勾了没用）。现在它管的就是这一条：**因为你把「最小化时
                # 自己还原」关了，微信最小化时我干不了活**。开着提醒就把话说清楚（日志/控制台可见），
                # 关掉就只留一行说明——不假装做成、也不反复唠叨。
                if bool(cfg.get("minimize_warning", True)):
                    log.warning("微信主窗现在是最小化的，而「最小化时自己还原」是关的 ⇒ 这一次只能如实停下"
                                "（不假装做成）。想让它自己接着干，就把「最小化时自己还原」打开；"
                                "不想再看到这条提醒，就把「最小化提醒」关掉。")
                else:
                    log.info("微信主窗最小化且未开自动还原 ⇒ 如实停下（已按设置不提醒）")
                return False
            u.ShowWindow(int(main), 4)                                   # SW_SHOWNOACTIVATE
            time.sleep(0.4)
            u.SetWindowPos(int(main), 0, 0, 0, 0, 0,
                           0x0002 | 0x0001 | 0x0004 | 0x0010 | 0x0040)   # NOMOVE|NOSIZE|NOZORDER|NOACTIVATE|SHOWWINDOW
            time.sleep(0.6)
            try:
                if gui is not None:
                    gui._update_render_rect()
            except Exception:
                pass
            global _MINIMIZED_BY_US, _WAS_ICONIC_BY_US
            _MINIMIZED_BY_US = int(main)      # 登记：干完活由 `_restore_fg_until` 放回收起状态
            _WAS_ICONIC_BY_US = int(main)     # 记下"是用户自己收起来的" ⇒ 链尾要还他收着（见 _minimize_back_if_needed）
            log.info("微信主窗原来是最小化：已**不激活**还原（不动光标（伪激活可能短暂置前约 1~3 秒后自动还回））后继续")
            return True
        except Exception as e:
            log.warning("无激活还原最小化窗口失败：%s", e)
            return False

    def close_search_popovers(self, gui=None, only_new=None) -> int:
        """把屏幕上残留的**搜索窗口**关掉（返回关掉几个）。失败静默、**绝不关微信主窗**。

        为什么要它（作者 2026-09-18 现场：「你怎么点出来个搜索聊天记录啊」）：切会话的搜索路线会
        打开搜索浮层并打字；一旦它失败（搜索入口识别到假图标 / 内容级复核没过），浮层就**留在屏幕上**
        —— 用户看到的就是微信弹着"搜索聊天记录"。⇒ 任何失败路径都要收尾：关掉浮层，不留残余。

        `only_new`＝本次动作**开始前**就已存在的 hwnd 集合（这些**不动**：可能是用户自己开的搜索窗）。
        """
        try:
            only_new = set(int(h) for h in (only_new or ()))
            n = 0
            for h, _rect, _cls, _ttl in self._search_window_hwnds(gui=gui):
                if h in only_new:
                    continue
                try:
                    if _close_search_popover(h):
                        n += 1
                except Exception:
                    pass
            if n:
                log.info("收尾：关掉残留的搜索窗口 %d 个", n)
            return n
        except Exception as e:                                     # noqa: BLE001
            log.debug("关搜索窗口收尾异常：%s", e)
            return 0

    def _search_window_hwnds(self, main: int = None, gui=None):
        """屏幕上**可见的搜索类窗口**清单 → `[(hwnd, rect, cls, title)]`（独立搜索窗 + 无边框浮层）。

        ⚡ 2026-09-18 晚加：旧口径只认类名 `Qt51514QWindowToolSaveBits` ⇒ 微信的**独立「搜索聊天记录」
        窗**（带标题栏、标题就是它）既**用不上**（于是又去点搜索入口、还把窗留在屏幕上）、也**关不掉**
        ——作者看到的那句「你怎么点出来个搜索聊天记录啊」就是它。
        """
        out = []
        try:
            import win32gui
            import win32process
            gui = gui or self._get_gui()
            main = int(main or getattr(gui, "main_hwnd", 0) or 0)
            pid = win32process.GetWindowThreadProcessId(main)[1] if main else 0

            def _cb(h, _l):
                try:
                    if not win32gui.IsWindowVisible(h):
                        return True
                    if main and int(h) == int(main):
                        return True
                    cls = str(win32gui.GetClassName(h) or "")
                    ttl = str(win32gui.GetWindowText(h) or "")
                    if not is_search_window(cls, ttl):
                        return True
                    if pid and win32process.GetWindowThreadProcessId(h)[1] != pid:
                        return True
                    x0, y0, x1, y1 = win32gui.GetWindowRect(h)
                    if x1 - x0 > 4 and y1 - y0 > 4:
                        out.append((int(h), (int(x0), int(y0), int(x1), int(y1)), cls, ttl))
                except Exception:
                    pass
                return True

            win32gui.EnumWindows(_cb, None)
        except Exception as e:                                     # noqa: BLE001
            log.debug("枚举搜索窗口失败：%s", e)
        return out

    # ⚡ 2026-09-18 深夜：**按键走格切会话**（作者问「有没有啥办法是不跳前台就可以选对的」，
    #   实测发现的零坐标路子，见 `_switch_by_keys` 的说明）
    _VK_UP, _VK_DOWN = 0x26, 0x28
    _KEYS_WALK_BUDGET = 12        # 单向最多按几格（每格都读会话头确认；两向合计 ≤24 格）

    def _header_now(self, gui) -> str:
        """当前会话头文本（**自己抓帧 + 自己 OCR**）。

        ⛔ 不借 `chat_is_open` 的说明文本：它只在**命中**时才把 OCR 结果写进 why，失败时写的是空串
        （2026-09-18 实测踩到：拿它当"当前是谁"的读数，读出来全是 `''`，把方向判断带偏）。
        """
        try:
            from . import chat_header as _ch
            from . import chat_ocr as _co
            im = _ch.capture_image(gui=gui)
            if im is None:
                return ""
            w, h = im.size
            crop = im.crop((int(w * 0.26), int(h * 0.03), int(w * 0.66), int(h * 0.14)))
            txt = [str(t) for t, *_ in _co.recognize(crop)]
            return "|".join(txt[:3])
        except Exception:
            return ""

    def _last_msg_ts(self, chat_id: str) -> int:
        """某会话**最后一条消息的时间戳**（读不到给 0）。列表按这个倒序排 ⇒ 用它定"往上还是往下"。"""
        if not chat_id:
            return 0
        try:
            rows = self._db.get_messages(chat_id, limit=1) or []
            return int(rows[0].get("create_time") or 0)
        except Exception:
            return 0

    def _chat_id_by_header(self, hdr: str) -> str:
        """把会话头 OCR 到的名字映射回 chat_id（尽力而为；认不出给空串）。"""
        h = str(hdr or "").split("|")
        cands = [x for x in h if x]
        if not cands:
            return ""
        try:
            from . import chat_ocr as _co
            pairs = []
            try:
                for g in (self.list_groups() or []):
                    if g.get("name") and g.get("wxid"):
                        pairs.append((str(g["name"]), str(g["wxid"])))
                    elif g.get("name") and g.get("id"):
                        pairs.append((str(g["name"]), str(g["id"])))
            except Exception:
                pass
            pairs.append(("文件传输助手", "filehelper"))
            for nm, cid in pairs:
                for c in cands:
                    try:
                        if _co.matches(c, nm):
                            return cid
                    except Exception:
                        continue
        except Exception:
            pass
        return ""

    def _walk_dir(self, chat_id: str, header: str) -> int:
        """按键走格的**方向**（数据给的）：+1 往下 / -1 往上 / 0 数据给不出（调用方默认往下）。"""
        t_want = self._last_msg_ts(chat_id)
        cid = self._chat_id_by_header(header)
        t_cur = self._last_msg_ts(cid) if cid else 0
        if t_want and t_cur and t_want != t_cur:
            return 1 if t_want < t_cur else -1      # 目标更旧 ⇒ 在列表更下面
        return 0

    def _switch_by_keys(self, chat_id: str, name: str, gui, main: int, budget: int = None):
        """**按键走格**切会话：零坐标、零滚动、零搜索窗、零像素匹配 → `(ok, why)`。

        ⚡ 2026-09-18 深夜实测（作者：「有没有啥办法是不跳前台就可以选对的」）：
          · 投递 `Ctrl+F` ⇒ 微信**不理**（浮层没出来）；`session/session.db` 在驱动库里**没有密钥**
            （拿不到列表顺序）；⇒ 试到 **投递方向键** 才成立：
            `MessageBackend(activate=True).keys(main, [VK_DOWN])` **真的换了会话**（现场：`演示（3）` →
            `海绵宝宝吸课堂（13）`），**没动光标、没开任何窗、没点鼠标**。
          · ⛔ **必须带伪激活**（`activate=True`）：不带时同一枪毫无反应（实测三变体对照）。
          · 方向由**数据**定（各会话最后消息时间倒序；目标更旧 ⇒ 往下），**每按一格都读会话头确认**，
            走过头就换方向再走 —— 所以"点不准"这件事在这条路上根本不存在（没有任何坐标）。
        """
        try:
            from . import input_backend as ib
            from . import chat_ocr as _co
            main = int(main or 0)
            if not main:
                return False, "找不到微信主窗"
            _hdr0 = self._header_now(gui)
            if not _hdr0:
                # ⛔ fail-closed（2026-09-18 深夜）：读不到会话头就**不许按键**——否则等于"闭着眼往下走"，
                #   走过头了也不知道（微信最小化/被挡住时就会这样）。交给后面的点列表/搜索路线。
                return False, "读不到会话头（窗口最小化/被遮挡？）⇒ 不按键（免得闭眼乱走）"
            d = self._walk_dir(chat_id, _hdr0) or 1
            be = ib.MessageBackend(activate=True)          # ⛔ 必须带伪激活（不带时微信不理投递的方向键）
            budget = int(budget or self._KEYS_WALK_BUDGET)
            steps = 0
            for _phase in (0, 1):
                if _phase == 1:
                    d = -d                                 # 反向再找一遍（自纠偏；数据定得准时一相就中）
                vk = self._VK_DOWN if d > 0 else self._VK_UP
                for _i in range(budget):
                    ok_k, _why_k = be.keys(main, [vk])
                    if not ok_k:
                        return False, "投递方向键失败：%s" % str(_why_k)[:60]
                    steps += 1
                    time.sleep(0.22)
                    hdr = self._header_now(gui)
                    if hdr and _co.matches(hdr, name):
                        return True, ("按键走格成功：%s %d 格（会话头读到 %r）；零坐标、没开搜索窗"
                                      % ("↓" if vk == self._VK_DOWN else "↑", steps, hdr[:22]))
                    if _i == 0:
                        _d2 = self._walk_dir(chat_id, hdr)
                        if _d2 and _d2 != d:           # 第一格就发现方向反了 ⇒ 立刻翻向
                            d = _d2
                            vk = self._VK_DOWN if d > 0 else self._VK_UP
            return False, "按键走格 %d 格都没走到「%s」（每格都读过会话头确认）" % (steps, name)
        except Exception as e:                                         # noqa: BLE001
            return False, "按键走格异常：%s" % str(e)[:90]

    def _click_visible_session(self, chat_id: str, name: str, gui, main: int):
        """在**当前可见的会话列表**里找目标行并投递点击（**不滚列表、不开搜索窗**）→ `(ok, why)`。

        ⚡ 2026-09-18 晚加（作者口径：「只要点到对的会话就行…**已经在的群聊不要切，不在群聊才需要切，
        切又可以分为搜索还有点击两种**」＋网友反馈「它要发消息时都会被激活到最上面…总感觉它的窗口
        跳出来，原因就是这个搜索框」）：
        搜索路线必然**开一个独立的搜索窗**（微信自己会把它摆到屏幕上，实测整条链 3.25s 窗口都是
        还原状态），而"点列表里那一行"只是往列表投一枪 —— **不开窗、不换前台**。
        ⇒ 切会话的顺序改成：①已在目标会话 ⇒ 什么都不做（`switch_chat_posted` 开头那条）；
        ②列表里**直接看得见**目标行 ⇒ 投递点它（本方法）；③看不见才走搜索路线。
        **不滚列表**：滚动会让用户眼前的列表动（他早就说过"它在划列表"），比开搜索窗更打扰。
        """
        try:
            from . import input_backend as ib
            from . import chat_ocr as _co
            from . import chat_header as _chh
            backend = ib.select_backend(gui=gui)
            if not isinstance(backend, ib.MessageBackend):
                return False, "当前输入后端不是投递档（config.input.backend=%s）" % backend.name
            img = _chh.capture_image(gui=gui)
            if img is None:
                return False, "抓不到画面（窗口不可见？）"
            try:
                row = _co.find_row_info(img, name)
            except Exception:
                row = None
            if not row:
                return False, "列表里没看到「%s」那一行（不滚列表——滚你屏幕比开搜索窗更打扰）" % name
            ox, oy = int(getattr(gui, "origin_x", 0)), int(getattr(gui, "origin_y", 0))
            tgt = ib.find_render_child(main) or main
            _ok, _why = self._click_posted(backend, tgt,
                                           (ox + int(row["pos"][0]), oy + int(row["pos"][1])),
                                           "会话行（列表·免搜索）")
            if not _ok:
                return False, _why
            time.sleep(0.6)
            _op, _opwhy = self.chat_is_open(chat_id, gui=gui, name=name)
            if _op:
                return True, "投递点会话行（列表·免搜索，强档证据：%s）" % str(_opwhy)[:80]
            _idn, _idnwhy = self.chat_identity_ok(chat_id, gui=gui)
            if _idn is True:
                return True, "投递点会话行（列表·免搜索，内容级复核过）"
            return False, ("点了列表里「%s」那一行，但没拿到『当前就是它』的正面证据：%s"
                           % (name, str(_opwhy)[:70]))
        except Exception as e:                                     # noqa: BLE001
            return False, "列表点击切会话异常：%s" % str(e)[:90]

    def switch_chat_posted(self, chat_id: str, gui=None, name: str = None, confirm_s: float = 8.0):
        """**投递版切会话**：库的**只读** OCR 定位会话行 → **投递点击**那一行 → **OCR 按名字确认**已打开。

        为什么需要：投递（L5）不切会话；而真实路径（L0）会真点真敲、被别的窗口挡住就失败，实测 `open_chat`
        三次重试全败、`send_text` 耗 140.7s 才返回失败（2026-09-13）。这条链**全程不碰真实鼠标、不打扰你（可能短暂置前约 1~3 秒后自动还回）**。

        确认判据＝`self.chat_is_open()`（会话列表**绿底高亮行** + 该行名字 OCR，与目标名做容忍比对）。
        ⛔ **不用**库的 `_chat_is_open`：它实测**不稳定**（同一次调用里 True、紧接着外面查却是 False），
        拿它当授权证据就等于回到"no_ref 照发"的误发模式。返回 (ok, 说明)；ok=True 时"当前会话＝目标会话"
        才有真正的正面证据（此时发送方可 `allow_no_ref=True`）。
        """
        from . import input_backend as ib
        try:
            gui = gui or self._get_gui()
            name = name or self.display_name(chat_id) or chat_id
            backend = ib.select_backend(gui=gui)
            if not isinstance(backend, ib.MessageBackend):
                return False, "当前输入后端不是投递档（config.input.backend=%s）" % backend.name
            main = int(getattr(gui, "main_hwnd", 0) or 0) or ib.find_main_window()
            if not main:
                return False, "找不到微信主窗"
            self._ensure_main_visible(gui, main)   # 最小化 ⇒ 先不激活地还原（否则下面抓图必失败）
            try:
                gui._update_render_rect()          # 只读：重算渲染区原点（坐标全靠它）
            except Exception:
                pass
            already, why0 = self.chat_is_open(chat_id, gui=gui, name=name)
            if already:
                return True, "目标会话已经是当前打开的会话（%s）" % why0
            # ⛔ 2026-09-16 用户明确要求（原话：「他老是想找会话列表那一条究竟在哪儿，
            #   **他不能直接点击输搜索框输入吗**」）⇒ **搜索框优先**：
            #   搜索入口是**固定位置**（两套 UI 都认，`open_chat_by_search` 已是实测通路），
            #   不用在会滚动的会话列表里找那一行、也不用滚轮、更不怕列表被别的窗口盖住。
            #   搜索没成才退回下面的「找行 + 滚轮」老路（保留，不删）。
            _sok, _swhy = False, ""
            _pre_sw = set()                      # 动手前已有的搜索窗口（收尾时不动它们）
            try:
                _pre_sw = set(h for h, *_r in self._search_window_hwnds(main=main, gui=gui))
            except Exception:
                pass
            # ② 先试**按键走格**（零坐标、零滚动、零搜索窗；作者问「有没有啥办法是不跳前台就可以选对的」）
            try:
                _kok, _kwhy = self._switch_by_keys(chat_id, name=name, gui=gui, main=main)
            except Exception as _e_k:                                  # noqa: BLE001
                _kok, _kwhy = False, "按键走格异常：%s" % str(_e_k)[:70]
            if _kok:
                return True, "切会话成功（%s）" % str(_kwhy)[:90]
            log.info("切会话：按键走格没成（%s）⇒ 试点列表", str(_kwhy)[:100])
            # ③ 再试**列表里直接点**（不开搜索窗；作者口径：切会话分"搜索"和"点击"两种，别一上来就搜索）
            try:
                _vok, _vwhy = self._click_visible_session(chat_id, name=name, gui=gui, main=main)
            except Exception as _e_v:                                  # noqa: BLE001
                _vok, _vwhy = False, "列表点击异常：%s" % str(_e_v)[:70]
            if _vok:
                return True, "切会话成功（%s）" % str(_vwhy)[:80]
            log.info("切会话：直接点列表没成（%s）⇒ 走搜索路线", str(_vwhy)[:100])
            try:
                _sok, _swhy = self.open_chat_by_search(chat_id, name=name, gui=gui)
                if _sok:
                    return True, "搜索框切会话成功（%s）" % str(_swhy)[:60]
                log.info("切会话：搜索框路线没成（%s）", str(_swhy)[:80])
            except Exception as _e:                          # noqa: BLE001
                _swhy = "异常：%s" % str(_e)[:80]
                log.warning("切会话：搜索框路线异常（%s）", str(_e)[:80])
            # ⛔ 2026-09-16（已知现象：「他点了一下搜索框，又不点，又搁那划会话列表」）：
            #   **搜索没成就停手，默认不再回退去滚会话列表**。为什么：
            #   老路的滚轮虽然走投递（**不动光标**），但**会话列表会在用户眼前滚**——他看到的
            #   "它在划列表"就是它；而搜索路线已经覆盖了绝大多数情况。
            #   ⇒ 做成开关 `wechat.scroll_list_fallback`（默认 False＝不回退），要成功率优先可打开。
            # ⛔ 2026-09-18：搜索路线**失败也要收尾** —— 关掉留在屏幕上的搜索浮层
            #   （作者现场：「你怎么点出来个搜索聊天记录啊」；失败留浮层＝把用户界面弄乱）
            try:
                if not _sok:
                    self.close_search_popovers(gui=gui, only_new=_pre_sw)
                    # 2026-09-18 作者问「你搜索的时候怎么跳前台呀」——查证：我们没主动置前（闸门还挡掉一次
                    # calibrate_layout）；前台是微信自己在搜索浮层弹出时抢的（实测投递点击入口后 +0.26s）。
                    # 真正的毛病：成功路径已点完即还，失败路径原来只等整链末尾才还 ⇒ 失败也立刻还。
                    _restore_fg_until("切会话·搜索路线（失败即还）", timeout=1.2, keep=False)
            except Exception as _e2:
                log.debug("关搜索浮层失败（不影响流程）：%s", _e2)
            if not self._scroll_list_fallback():
                return False, ("搜索框路线没成（%s）⇒ 按当前设置**不退回会滚你会话列表的老路**，本回合不切会话。"
                               "（想允许它回退：控制台「微信」面板打开「搜索失败时扫会话列表」；"
                               "或先把微信窗口露出来、或手动点到目标会话再让我发）" % str(_swhy)[:80])
            log.info("切会话：按设置允许回退 ⇒ 去会话列表里找行（列表会在屏幕上滚动）")
            # ⚠️ 用我们自己的**读图**找行（chat_ocr.find_row），不用库的 find_session：
            #    后者找不到时会用**真实鼠标**悬停/滚动会话列表（实测光标会动），违反"不动鼠标"。
            from . import chat_ocr as _co
            from . import chat_header as _chh
            ox, oy = int(getattr(gui, "origin_x", 0)), int(getattr(gui, "origin_y", 0))
            if not ox and not oy:
                return False, "渲染区原点未知（窗口不可见？）"
            r = getattr(gui, "render_rect", None) or (0, 0, 0, 0)
            rw, rh = int(r[2] - r[0]), int(r[3] - r[1])
            if rw <= 0 or rh <= 0:
                return False, "渲染区未知（窗口不可见？）"
            try:
                pane = int(gui.detect_pane_left()) or int(rw * _chh.PANE_LEFT_REL)
            except Exception:
                pane = int(rw * _chh.PANE_LEFT_REL)
            # 会话列表列中心（滚轮落点）：列表在面板左沿往左约 240px 的那一列
            wheel_pt = (ox + max(30, pane - 130), oy + int(rh * 0.55))

            # ⚠️ 会话行的点击与滚轮都要投给**渲染子窗**（`MMUIRenderSubWindowHW`）——2026-09-13 实测对比：
            #    同一枪投主窗：点完当前会话没变（绿底仍在原来那行）；投渲染子窗：点完聊天区内容确实变了。
            #    键盘与「发送」按钮投主窗仍然有效，别一起改。
            tgt = ib.find_render_child(main) or main

            def _scroll(times: int) -> bool:
                ok_s, _why_s = backend.wheel(tgt, wheel_pt, -120, times=max(1, int(times)), gap_ms=70)
                return bool(ok_s)

            # ⛔ 先看"滚轮那一点归谁"：微信被别的窗口压住时，滚轮事件**到不了微信**（2026-09-13 实测：
            #    会话列表那块被 Chrome 盖着 ⇒ 投递滚轮与真滚轮都没让列表动一行）。被盖住就别白滚，
            #    直接在结果里说清"看不见列表所以没滚"，把选择权交回用户（或走搜索框那条不依赖滚动的路）。
            _scroll_fn, _scroll_note = _scroll, ""
            try:
                import win32gui
                import win32process
                _under = win32gui.WindowFromPoint((int(wheel_pt[0]), int(wheel_pt[1])))
                _pid_under = win32process.GetWindowThreadProcessId(_under)[1]
                _pid_wechat = win32process.GetWindowThreadProcessId(int(main))[1]
                if _under and _pid_under and _pid_wechat and _pid_under != _pid_wechat:
                    _scroll_fn = None
                    _scroll_note = ("会话列表那块被别的窗口盖着（点下窗口 pid=%s ≠ 微信 pid=%s）⇒ 滚轮到不了微信，本回合不滚。"
                                    % (_pid_under, _pid_wechat))
                    log.info("切会话：%s", _scroll_note)
            except Exception:
                pass

            # 先把会话列表滚到**顶**（目标也可能在当前视野**上方**——只往下扫会永远找不到）
            if _scroll_fn is not None:
                try:
                    backend.wheel(tgt, wheel_pt, 120, times=8, gap_ms=70)
                    time.sleep(0.5)
                except Exception:
                    pass
            # 目标会话"最后一条消息"的显示时间（HH:MM）——给行定位当**不依赖名字**的第二条信号：
            # 名字只有一个字母的会话（E）靠 OCR 认不稳，而行右侧的时间戳读得准（实测）。
            _want_time = ""
            try:
                _last = self._db.get_messages(chat_id, limit=1) or []
                if _last:
                    _lt = time.localtime(int(_last[0].get("create_time") or 0))
                    if _lt.tm_yday == time.localtime().tm_yday:      # 今天的消息 ⇒ 行上显示 HH:MM
                        _want_time = time.strftime("%H:%M", _lt)
            except Exception:
                _want_time = ""
            def _find(img):
                """先按"最后一条消息时间"认（不依赖名字，E 这种单字母名字的兜底），
                认不到再退回**按名字**认。

                ⛔ 2026-09-14 修（⑤ 重发实测）：原来只走时间这一条 —— 而 E 那一行的 OCR 文本
                只有 `[草稿]EE`（草稿行不显示时间戳）⇒ 时间永远配不上 ⇒ 列表里**明明有**这一行、
                `find_row_info` 却返回 None，表现成"没定位到 E"（白滚 6 轮）。
                补名字这条路的同时不动安全边界：点完之后的**内容级身份闸**仍是发不发的最后一道闸。
                """
                if img is None:
                    return None
                return (_co.find_row_info(img, name, want_time=_want_time)
                        or _co.find_row_info(img, name))

            info, flog = _co.find_row_scrolled(
                capture_fn=lambda: _chh.capture_image(gui=gui),
                find_fn=_find,
                scroll_fn=_scroll_fn, max_steps=6, per_step=3, settle_s=0.45,
                tries_per_step=3, gap_s=0.35)      # 抓图偶发只读到 2~4 行 ⇒ 每一步多抓几帧再判
            if not info:
                _d = self._dump_fail_shot("switch_row", _chh.capture_image(gui=gui),
                                          {"name": name, "want_time": _want_time, "flog": str(flog),
                                           "scroll_note": _scroll_note})
                # ⚠️ 2026-09-16 r19（跨机 r18 实测）：**这条分支才是"把列表滚走"的主力**——它为找行
                #    最多下滚 6 步 ×3 = 18 格，失败后原来完全不还原（对面 live：目标名不存在 ⇒
                #    列表停在滚动后的位置）。上一轮我只在 verify 分支加了还原 ✗。⇒ 两条失败分支都还原。
                _scrolled_back = self._scroll_list_to_top(backend, tgt, wheel_pt, _scroll_fn is not None)
                return False, "会话列表里（只读截图 + OCR%s）没定位到「%s」（%s%s）%s%s" % (
                    "，含平滑下滚 6 轮" if _scroll_fn is not None else "；**本轮没有滚动**",
                    name, flog, ("；" + _scroll_note) if _scroll_note else "",
                    ("｜现场已存 %s" % _d) if _d else "",
                    "｜已把列表滚回顶部" if _scrolled_back else "")
            pos = info["pos"]
            clicked_y = int(info["y_abs"])
            # ⛔ 一次切会话**最多一枪**：冷却期内只复核、不补点（既有口径：连点两下会把聊天框关掉）
            self._pick_last = getattr(self, "_pick_last", {})
            _last_ts, _last_y = self._pick_last.get(str(chat_id), (0.0, -1))
            _allow, _why_cd = _co.click_allowed(_last_ts, time.time())
            if not _allow:
                img0 = _co.capture_best(gui=gui, frames=2)
                hl0 = _co.highlight(img0) if img0 is not None else None
                if hl0 and abs(int(hl0["y_abs"]) - int(_last_y)) <= 28:
                    return True, "上一枪已生效（%.1fs 前点的第 %d 行，现在正是绿底高亮行 %.2f）" % (
                        time.time() - _last_ts, int(_last_y), hl0["score"])
                return False, "会话行在（OCR「%s」），但%s ⇒ **不补点**。若确需重试请稍后再调。" % (
                    str(info.get("name"))[:12], _why_cd)
            # ⚠️ 会话行的点击要投给**渲染子窗**（`MMUIRenderSubWindowHW`）——2026-09-13 实测对比：
            #    同一枪投主窗：点完当前会话没变（绿底仍在原来那行）；
            #    投渲染子窗：点完聊天区内容确实变了（切过去了）。键盘/发送按钮投主窗仍然有效，别一起改。
            tgt = ib.find_render_child(main) or main
            tgt = ib.find_render_child(main) or main
            # ⚠️ 2026-09-16 修（真缺陷·r11 两次实测）：滚轮是**平滑滚动**，惯性没停行还在动 ⇒ 照算出来的
            #    y 点下去就**点空**（"点击已发出但该行没变绿底"）。⇒ 点前等列表停住（纯像素自检、不调 OCR）。
            _settled = self._list_settled(gui)
            flog = str(flog) + ("｜点前列表已停稳" if _settled else "｜⚠️点前列表没等到停稳")
            # 点前的聊天区文字（用来判"切会话到底发没发生"——绿底那项自检会被帧质量骗）
            _pane0 = _co.pane_text(_chh.capture_image(gui=gui))
            # ⛔ 2026-09-18 加（用户新反馈：「他会把那个群给拖出窗口化」）：
            #   微信 4.x 是 **Qt 自绘**，双击判定由 Qt 按"两次点击的间隔 + 位置"自己算 —— 我们
            #   **没发** `WM_LBUTTONDBLCLK` 也没用。切会话第一枪点偏、复核不过、再点相邻那一行时，
            #   两次点击间隔短、位置又近（<40px）⇒ 微信把这次聊天**独立成一个浮动窗口**（截图里那个）。
            #   ⇒ 会话行点击再加一条**全局**最小间隔（不看 chat_id），并记下落点做位置判。
            _pl = getattr(self, "_row_click_last", None)
            _cx, _cy = ox + int(pos[0]), oy + int(pos[1])
            if _pl:
                _dt = time.time() - float(_pl[0])
                _dx, _dy = abs(int(_pl[1]) - _cx), abs(int(_pl[2]) - _cy)
                if _dt < 1.2 and _dx <= 40 and _dy <= 40:
                    return False, ("距上一次会话行点击只有 %.2fs（位置相差 %d,%d px）⇒ **不补第二枪**："
                                   "微信按这个间隔会判成双击、把聊天独立成一个窗口。稍等一两秒再试，"
                                   "或先在微信里点开目标会话。" % (_dt, _dx, _dy))
            # 会话行必须用**慢节奏**点击（2026-09-13 A/B：快节奏投渲染子窗高亮不动；
            #   悬停 300ms + 按住 150ms 高亮立刻跳到目标行）——见 input_backend.click 的注释
            ok, why = self._click_posted(backend, tgt, (_cx, _cy), "会话行（切会话）",
                                         allow_new=True, hover_ms=300, press_ms=150)
            if not ok:
                return False, "投递点击会话行失败：%s" % why
            self._row_click_last = (time.time(), _cx, _cy)
            self._pick_last[str(chat_id)] = (time.time(), clicked_y)
            # 点完顺手把"可能被独立出去的聊天窗"收回来（见 _reattach_if_floating 的注释）
            _rt = self._reattach_if_floating(name)
            if _rt:
                flog = str(flog) + "｜" + _rt
            # 复核（自洽证据）：**我们按名字点的那一行**现在是不是高亮行（相对自检，抗帧质量抖动）
            deadline = time.time() + max(1.0, float(confirm_s))
            last = ""
            while time.time() < deadline:
                time.sleep(0.35)
                img2 = _co.capture_best(gui=gui, frames=3)
                hl = None
                if img2 is not None:
                    hl, _hlwhy = _co.highlight_relative(img2)
                    if hl is None:
                        hl = _co.highlight(img2)
                if hl and abs(int(hl["y_abs"]) - clicked_y) <= 28:
                    return True, ("投递点击第 %d 行（OCR「%s」）后该行成高亮行（占比 %.2f）｜%s"
                                  % (clicked_y, str(info.get("name"))[:12], hl["score"], flog))
                hit, last = self.chat_is_open(chat_id, gui=gui, name=name)
                if hit:
                    return True, "投递点击会话行后 OCR 已确认打开「%s」（%s）" % (name, last)
                # 第三条证据：点了那一行、**聊天区内容确实变了** ⇒ 切换发生了（拿它防止"绿底读不到"误判失败）
                _pane1 = _co.pane_text(img2) if img2 is not None else ""
                if _pane0 and _pane1 and _pane0 != _pane1:
                    return True, ("投递点击第 %d 行（OCR「%s」）后聊天区内容已变化 ⇒ 已切到该会话"
                                  "（绿底这次读不稳：%s）" % (clicked_y, str(info.get("name"))[:12], flog))
            # ⚠️ 失败留现场（2026-09-16 r17 跨机报"fail\ 0 条目"）：**"点了但复核没过"这条分支最容易发生**
            #    （红线收紧之后尤其），原来这里没有 dump ⇒ 对面拿不到现场。补上。
            _d = self._dump_fail_shot("switch_row_verify", _chh.capture_image(gui=gui),
                                      {"name": name, "clicked_y": clicked_y,
                                       "want_time": _want_time, "flog": str(flog),
                                       "last": str(last)[:300]})
            # ⚠️ 失败后**把会话列表滚回顶部**（2026-09-16 r17/r18 跨机报的污染）：见 `_scroll_list_to_top`
            self._scroll_list_to_top(backend, tgt, wheel_pt, _scroll_fn is not None)
            return False, ("投递点击已发出，但既没看到该行变绿底、也没能 OCR 确认「%s」（%s；最后一帧：%s%s）"
                           % (name, flog, str(last)[:60], ("｜现场已存 %s" % _d) if _d else ""))
        except Exception as e:
            return False, "投递切会话异常：%s" % e

    def _scroll_list_to_top(self, backend, tgt, wheel_pt, can_scroll: bool = True) -> bool:
        """把会话列表**滚回顶部**（失败路径的收尾）。返回是否真的滚了。

        ⚠️ 为什么需要（2026-09-16 跨机 r17/r18）：切会话为了找行会下滚最多 18 格，**失败后如果不还原**，
        用户的会话列表位置就被弄乱了（对面连续三次失败后偏离原位，最后靠搜索浮层才带回来）。
        ⚠️ 用**产品自己那条已验证有效的滚轮形状**（`times=8, gap_ms=70`，和"先把列表滚到顶"那一步一致）：
        对面手搓的"一枪 +120×40"对他们那台**无效**（可见行逐字不变），所以别自己换参数。
        """
        if not can_scroll:
            return False
        try:
            for _ in range(3):                      # 多给两轮，防消息丢
                backend.wheel(tgt, wheel_pt, 120, times=8, gap_ms=70)
                time.sleep(0.2)
            return True
        except Exception:
            return False

    def _dump_fail_shot(self, tag: str, img, extra: dict = None, keep: int = 5) -> str:
        """失败当时的**画面 + 判据中间量**落到 `wechatauto_logs/fail/<时间戳>_<tag>/`（尽力而为，绝不抛）。

        口径照 AGENTS.md §2.2（MAA 的反面教材）：别人拿到这个目录，**不看日志**就能判断
        "是没找到还是找错了" ⇒ 放 `shot.png`（原图）+ `probe.json`（尺寸/候选块/判据与结论）。
        为什么加（2026-09-16 跨机需求⑤）：对面报的"点到「＋」/ 浮层 205×205 / 认不出绿底行"
        我这边**看不到现场**、只能猜；从本轮起失败必有现场，对面把目录打包发回即可。
        """
        try:
            import json as _json
            base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "wechatauto_logs", "fail")
            stamp = time.strftime("%Y%m%d-%H%M%S")
            d = os.path.join(base, "%s_%s" % (stamp, tag))
            os.makedirs(d, exist_ok=True)
            if img is not None:
                try:
                    img.save(os.path.join(d, "shot.png"))
                except Exception:
                    pass
            with open(os.path.join(d, "probe.json"), "w", encoding="utf-8") as f:
                _json.dump({"tag": tag, "when": stamp,
                            "size": (list(img.size) if img is not None else None),
                            "extra": extra or {}}, f, ensure_ascii=False, indent=1)
            try:                                       # 只留最近 keep 份（同一 tag）
                olds = sorted(n for n in os.listdir(base) if n.endswith("_" + tag))
                for n in olds[:-int(keep)]:
                    import shutil
                    shutil.rmtree(os.path.join(base, n), ignore_errors=True)
            except Exception:
                pass
            return d
        except Exception:
            return ""

    def _list_settled(self, gui, tries: int = 8, gap: float = 0.2) -> bool:
        """等会话列表**停止滚动**：连续两帧"列表区域"像素一致就返回 True（超时返回 False）。

        为什么必须有（2026-09-16 实测·真缺陷）：投递滚轮触发的是**平滑滚动**，惯性期间行还在动——
        `switch_chat_posted` 滚到顶后**立刻**算坐标点击，行已经移走 ⇒ 点空（表观症状是
        "投递点击已发出，但该行没变绿底"，r11 两次都卡在这里）。判据只用像素差、不调 OCR（快）。
        ⚠️ 返回 False **不阻塞流程**（照旧往下走），只是调用方要如实把它写进结果说明里。
        """
        try:
            from . import chat_header as _chh
            from . import chat_ocr as _co
            from PIL import ImageChops
            prev = None
            for _i in range(max(2, int(tries))):
                im = _chh.capture_image(gui=gui)
                if im is None:
                    time.sleep(gap)
                    continue
                x0, x1 = _co._green_x(im)
                if x1 - x0 < 40:
                    time.sleep(gap)
                    continue
                crop = im.crop((x0, 20, x1, im.size[1]))
                if prev is not None and crop.size == prev.size:
                    d = ImageChops.difference(prev, crop).getbbox()
                    if d is None:
                        return True
                    if (d[3] - d[1]) <= 3 and (d[2] - d[0]) <= 6:      # 只剩零星抗锯齿差 ⇒ 当停稳
                        return True
                prev = crop
                time.sleep(gap)
            return False
        except Exception:
            return False

    def _find_search_popover(self, main: int = None):
        """找微信的**搜索浮层**并抓下它的画面：返回 `(hwnd, rect, img)` 或 None。

        实测口径（2026-09-13，本机 4.1.15.8）：独立顶层窗、class＝`Qt51514QWindowToolSaveBits`、
        标题 `Weixin`、浮在主窗上方（(264,126)-(816,464) 那一带）；**输入前 552×338、出结果后
        552×891**（同一个窗口会变高）⇒ **不许按尺寸/宽高比筛**（它和表情面板同类名，按形状筛
        会误判：曾把高浮层当表情面板排除、误报"浮层没弹出来"）。判据改成**画面内容**
        （`chat_ocr.looks_like_search_popover`：最近在搜 / 联系人 / 群聊 / 聊天记录…）。
        """
        try:
            import win32gui
            import win32process
            from . import chat_ocr as _co
            from . import chat_header as _chh
            pid = win32process.GetWindowThreadProcessId(int(main))[1]
            for h, rect, _cls, _ttl in self._search_window_hwnds(main=main):
                im = _chh.shot_window(h)
                hit, why = _co.looks_like_search_popover(im)
                if hit:
                    return h, rect, im, why
                log.info("浮层候选 hwnd=%s %s（%s，pid=%s）不像搜索浮层：%s",
                         h, rect, _cls or _ttl, pid, why)
            return None
        except Exception as e:
            log.warning("找搜索浮层失败：%s", e)
            return None

    def open_chat_by_search(self, chat_id: str, name: str = "", gui=None):
        """**投递版「搜索框找会话」**：不依赖滚动（会话列表被遮挡/目标在折叠线外时用）。

        链路：投递点搜索框（左上角读到「搜索」字样的那一行）→ 投递 `WM_CHAR` 打字 → 等结果 →
        OCR 结果行找名字匹配的那行 → 投递点它 → **内容级复核**（`chat_identity_ok`）。
        返回 `(ok, 说明)`；拿不到内容级正面证据就不算成功（fail-closed）。
        """
        from . import input_backend as ib
        # ⚠️ 2026-09-16 晚（时间线实测）：**这条链会抢前台**——开搜索浮层、往浮层投字、点结果行，
        #    实测把微信顶到前台**而且不还**（0.1s 采样：浮层 1.63s + 主窗 8.96s，全程没还过）。
        #    ⇒ 进这条路先把用户当时的前台记下来，出去时（含异常路径，见函数末尾的 finally）一律还回去。
        #    这也很关键：调用方（发文件）随后会自己 `_stash_fg()` 记"用户窗口"——这里先还回去，
        #    它记到的才是**用户的窗口**，而不是被这条链顶到前面的微信。
        _stash_fg()
        # ⚡ 2026-09-18 晚：记下**动手前**屏幕上已有的搜索窗口（收尾时不动它们——可能是用户自己开的）；
        #    本次新出现的（我们点开的浮层 / 独立搜索窗）一律在 finally 里关掉。
        _pre_sw = set()
        try:
            _pre_sw = set(h for h, *_r in self._search_window_hwnds(gui=gui))
        except Exception:
            pass
        try:
            gui = gui or self._get_gui()
            name = name or self.display_name(chat_id) or chat_id
            backend = ib.select_backend(gui=gui)
            if not isinstance(backend, ib.MessageBackend):
                return False, "当前输入后端不是投递档（config.input.backend=%s）" % backend.name
            main = int(getattr(gui, "main_hwnd", 0) or 0) or ib.find_main_window()
            if not main:
                return False, "找不到微信主窗"
            tgt = ib.find_render_child(main) or main
            self._ensure_main_visible(gui, main)   # 最小化 ⇒ 先不激活地还原，否则抓不到搜索浮层
            try:
                gui._update_render_rect()
            except Exception:
                pass
            ox, oy = int(getattr(gui, "origin_x", 0)), int(getattr(gui, "origin_y", 0))
            r = getattr(gui, "render_rect", None) or (0, 0, 0, 0)
            rw = int(r[2] - r[0])
            if rw <= 0:
                return False, "渲染区未知（窗口不可见？）"
            from . import chat_ocr as _co
            from . import chat_header as _chh
            try:
                pane = int(gui.detect_pane_left())
            except Exception:
                pane = int(rw * _chh.PANE_LEFT_REL)
            # ① 搜索入口：**两套 UI 都要认**（用户 2026-09-13 口径：本机"没有搜索框了，只有搜索的
            #    一个图标，摁了之后才有搜索框"；另一台电脑"是有搜索框的"⇒ 两种形态都必须是正路，
            #    不许假设其中一种）。认字优先（读到「搜索」＝搜索框形态，点文字最稳），
            #    读不到就认图标（标题带里最靠左的那个 ≈30px 深色块＝放大镜；「＋」在它右边，别点错）。
            img = _chh.capture_image(gui=gui)
            if img is None:
                return False, "抓不到画面（窗口不可见？）"
            ent = _co.find_search_entry(img, left=pane or None)
            if not ent:
                # ⚡ 2026-09-18 晚：这条分支原来是**裸返回**（现场什么都不留）。加最小边长判据后，本机
                #    在"抓到的是兄弟窗画面"那类情况下就会走到这里 ⇒ 必须留现场（"没找到入口"也要能复盘）。
                _d = self._dump_fail_shot("search_entry_missing", img, {
                    "variant": "none", "pane_left": pane, "img_size": list(img.size)})
                return False, ("找不到搜索入口：标题带里既没读到「搜索」字样，也没识别出放大镜图标"
                               "（窗口尺寸/主题/微信版本不同？⇒ 把当前微信窗口截图发我，或改用会话列表点击）"
                               "%s" % (("｜现场已存 %s" % _d) if _d else ""))
            variant = ent.get("variant")
            if variant == "icon":
                # —— 图标形态（本机 4.1.15.8）：点开后**搜索框是一个独立顶层窗（浮层）**，
                #    主窗渲染区里**看不到它**（实测：主窗像素只多了图标 hover 态，band_diff≈0.05）
                #    ⇒ 必须找到那个窗口、抓它自己的画面、往它投字与点击。自检不许再用"主窗像素变了"。
                #    点之前先看有没有已经开着的浮层（有就直接用，避免把自己点关）。
                pop = self._find_search_popover(main)
                if not pop:
                    self._click_posted(backend, main, (ox + int(ent["x"]), oy + int(ent["y"])),
                                 "搜索入口", allow_new=True)[0]
                    for _i in range(30):          # 总预算仍是 ~3.0s，粒度 0.1s：命中立刻 break
                        time.sleep(0.1)
                        pop = self._find_search_popover(main)
                        if pop:
                            break
                if not pop:
                    # 失败留全现场（2026-09-16）：把这一帧 + 候选块清单落盘，对面打包发回即可定位
                    # "是没找到入口还是点错入口"（跨机需求⑤：对面报"点到顶部「＋」"就是这一类）。
                    _d = self._dump_fail_shot("search_entry", img, {
                        "variant": variant, "picked": (int(ent["x"]), int(ent["y"])),
                        "why": ent.get("why"), "cands": ent.get("cands"), "pane_left": pane})
                    return False, ("点了搜索图标（依据：%s；候选块 %s）但没看到搜索浮层弹出来 ⇒ 不往下打字"
                                   "（fail-closed）%s"
                                   % (ent.get("why"), ent.get("cand_txt") or ent.get("cands"),
                                      ("｜现场已存 %s" % _d) if _d else ""))
                pop_hwnd, prect, pimg, pwhy = pop
                row, shot_size = None, None
                # ⚠️ 浮层会**保留上次的查询词**（实测：再次打开时它还高着开、词还在）——这时再打字会变成
                #    「E」+「E」⇒ 查不到任何联系人、浮层里也就没有「联系人」段（本轮就是这么失败的：
                #    "搜索浮层的画面里没认出「E」那一行"）。⇒ 先拿现成画面找一次；找不到再**清空**（投 8 个
                #    退格，输入框空着时是无害的）重新打字。
                if pimg is not None:
                    shot_size = pimg.size
                    row = _co.find_popover_row(pimg, name)
                if not row:
                    try:
                        backend.send_text(int(pop_hwnd), "\b" * 8)
                        time.sleep(0.3)
                    except Exception:
                        pass
                    ok_t, why_t = backend.send_text(int(pop_hwnd), name)
                    if not ok_t:
                        return False, "往搜索浮层投字失败：%s" % why_t
                    for _i in range(15):          # 总预算仍是 ~3.0s（原来 5×0.6）
                        time.sleep(0.2)
                        im3 = _chh.shot_window(int(pop_hwnd))
                        if im3 is None:
                            continue
                        shot_size = im3.size
                        row = _co.find_popover_row(im3, name)
                        if row:
                            break
                if not row:
                    # 失败留全现场（2026-09-16 晚加：跨机 r11 报"fail 目录是空的"——这条分支原来没 dump）
                    _d = self._dump_fail_shot("search_popover_row", pimg, {
                        "name": name, "popover_hwnd": int(pop_hwnd), "popover_rect": list(prect),
                        "shot_size": list(shot_size) if shot_size else None, "popover_why": pwhy,
                        "variant": variant, "entry": ent.get("why"), "cand_txt": ent.get("cand_txt")})
                    _closed = _close_search_popover(int(pop_hwnd))     # 别把浮层留在用户屏幕上（它还占前台）
                    return False, ("搜索浮层的画面里没认出「%s」那一行（浮层截图 %s%s；浮层%s）"
                                   % (name, shot_size, ("｜现场已存 %s" % _d) if _d else "",
                                      "已关掉" if _closed else "**没关掉**"))
                _cok, _cwhy = self._click_posted(backend, int(pop_hwnd),
                                                 (int(prect[0]) + int(row["x"]), int(prect[1]) + int(row["y"])),
                                                 "搜索浮层结果行")
                if not _cok:
                    return False, _cwhy
                time.sleep(1.0)
                # ⚡ 2026-09-18 晚（作者现场：「**你发消息搜索的时候就搜了两遍**」的根因）：
                #   这一格原来只认**内容级复核**（`chat_identity_ok` 读聊天区最近几条）——
                #   而点完结果行之后**搜索浮层还盖着聊天区**（实测日志：22:06:57「点了『演示』行…
                #   聊天区里没有目标会话最近的任何一条文本（试过 6 条）」），于是 5 秒后又搜了一遍
                #   （22:07:02），第二次才由**会话头标题带 OCR='演示（3）'** 认出"其实已经切过去了"。
                #   ⇒ 口径改成**分档**（与 box 路线一致）：①先看强档证据（会话头/活动行，不依赖聊天区
                #   内容、浮层盖着也读得到）②再关掉浮层做内容级复核 ③判据**不可用**（读不出）时按弱证据
                #   计切成功——真正发消息仍要另过发送闸（内容 × 活动行时间），这里放宽的只是"切"这一步。
                _op, _opwhy, idn, idn_why = False, "", None, ""
                for _i in range(4):            # 总预算 ~1.8s：**先等强档证据出现**（原来 1.0s 打一枪就判否）
                    time.sleep(0.35)
                    _op, _opwhy = self.chat_is_open(chat_id, gui=gui, name=name)
                    if _op:
                        break
                if _op:
                    _closed = _close_search_popover(int(pop_hwnd))     # 切成了就把浮层收掉（别留屏幕上）
                    return True, ("搜索浮层路线成功（强档证据：%s；%s；浮层%s）"
                                  % (str(_opwhy)[:80], row.get("why"),
                                     "已关掉" if _closed else "**没关掉**"))
                _closed = _close_search_popover(int(pop_hwnd))         # 先关浮层：它盖着聊天区，不关读不到内容
                time.sleep(0.4)
                idn, idn_why = self.chat_identity_ok(chat_id, gui=gui)
                if idn is True:
                    return True, ("搜索浮层路线成功（浮层 hwnd=%s，%s，%s，落点 %s）：%s"
                                  % (pop_hwnd, pwhy, row.get("why"), (row["x"], row["y"]), idn_why))
                _why_s2 = str(idn_why)
                if idn is None or ("读不出" in _why_s2):
                    return True, ("搜索浮层路线：内容级判据这次不可用（%s），但点的是浮层里名字匹配「%s」的"
                                  "结果行 ⇒ 按弱证据计切成功（发送闸仍要另过内容×活动行时间）"
                                  % (_why_s2[:70], name[:10]))
                _d = self._dump_fail_shot("search_identity", _chh.capture_image(gui=gui), {
                    "name": name, "row_why": row.get("why"), "落点": [row["x"], row["y"]],
                    "idn": str(idn), "idn_why": str(idn_why)[:400], "strong": str(_opwhy)[:200]})
                return False, ("点了搜索浮层的「%s」行（%s，落点 %s），但内容级复核没过：%s%s"
                               % (name, row.get("why"), (row["x"], row["y"]), idn_why,
                                  ("｜现场已存 %s" % _d) if _d else "") +
                               ("｜浮层已关掉" if _closed else "｜浮层**没关掉**"))
            # —— box 形态（另一台机 / 老 UI：搜索框直接摆着）：点它 → 主窗打字 → 结果行在主窗里找
            self._click_posted(backend, main, (ox + int(ent["x"]), oy + int(ent["y"])),
                                 "搜索入口", allow_new=True)[0]
            time.sleep(0.25)                   # 2026-09-18：0.45 → 0.25
            ok_t, why_t = backend.send_text(main, name)
            if not ok_t:
                return False, "搜索框打字失败：%s" % why_t
            # ⚠️ 2026-09-16 r19（跨机 r18 的**最值钱发现**）：搜索框形态的**结果往往也是独立浮层**
            #    （对面实测：`_find_search_popover` 命中 hwnd 1836290 / rect (154,98,614,921) / 460×823、
            #    画面含「搜索网络结果」，`find_popover_row` 一次命中；而那台走"主窗里找结果行"**3 次全没认出**）。
            #    ⇒ box 路线**先按浮层试一遍**（与 icon 路线同一套），不行再退回"主窗里找行"。
            #    这条一通，切会话就不必再依赖"绿底 + 活动行时间"那一档 ⇒ 缓解可用性缺口。
            _pop, _prow = None, None
            for _i in range(16):              # 总预算仍是 ~2.4s（原来 6×0.4）
                time.sleep(0.15)
                _pop = self._find_search_popover(main)
                if _pop:
                    _prow = _co.find_popover_row(_pop[2], name)
                    if _prow:
                        break
            if _pop and _prow:
                _ph, _prect, _pimg, _pwhy = _pop
                _cok2, _cwhy2 = self._click_posted(backend, int(_ph),
                                                   (int(_prect[0]) + int(_prow["x"]), int(_prect[1]) + int(_prow["y"])),
                                                   "搜索浮层结果行（box 路线）")
                if not _cok2:
                    return False, _cwhy2
# ⚡ 2026-09-18 实测：点搜索入口后**微信自己**会把搜索浮层激活到前台（+2.77s），
                #   而我们原来只等到整条链结束才还（+5.44s）⇒ 白占 ~2.7s。点完结果行立刻还
                #   （后面的内容级复核走 PrintWindow 取窗口自身画面，不需要前台）。
                _restore_fg_until("切会话·搜索路线（点完即还）", timeout=1.2, keep=False)
                time.sleep(0.5)                # 2026-09-18：1.0 → 0.5（后面紧接内容级复核，测得早没关系）
                _idn2, _idn2_why = self.chat_identity_ok(chat_id, gui=gui)
                if _idn2 is True:
                    return True, ("搜索框路线（结果在独立浮层里）成功：浮层 hwnd=%s，%s，%s"
                                  % (_ph, _pwhy, _idn2_why))
                if _idn2 is None or ("读不出" in str(_idn2_why)):
                    return True, ("搜索框路线（浮层）：内容级判据这次不可用（%s），但点的是浮层里名字匹配「%s」"
                                  "的结果行 ⇒ 按弱证据计切成功（发送闸仍要另过内容×活动行时间）"
                                  % (str(_idn2_why)[:70], name[:10]))
                _close_search_popover(int(_ph))     # 判否 ⇒ 关掉浮层，再退回"主窗里找行"
            # 结果里找名字匹配的行（多抓几帧）
            info = None
            for _i in range(10):              # 总预算仍是 ~2.0s（原来 4×0.5）
                time.sleep(0.2)
                im2 = _chh.capture_image(gui=gui)
                info = _co.find_row_info(im2, name) if im2 is not None else None
                if info:
                    break
            if not info:
                _d = self._dump_fail_shot("search_box_row", im2, {
                    "name": name, "variant": variant, "entry": ent.get("why"),
                    "cand_txt": ent.get("cand_txt")})
                return False, ("搜索结果里没认出「%s」（可能没有这条会话，或结果区 OCR 读不出）%s"
                               % (name, ("｜现场已存 %s" % _d) if _d else ""))
            _cok3, _cwhy3 = self._click_posted(backend, main,
                                               (ox + int(info["pos"][0]), oy + int(info["pos"][1])),
                                               "主窗搜索结果行")
            if not _cok3:
                return False, _cwhy3
# ⚡ 2026-09-18 实测：点搜索入口后**微信自己**会把搜索浮层激活到前台（+2.77s），
            #   而我们原来只等到整条链结束才还（+5.44s）⇒ 白占 ~2.7s。点完结果行立刻还
            #   （后面的内容级复核走 PrintWindow 取窗口自身画面，不需要前台）。
            _restore_fg_until("切会话·搜索路线（点完即还）", timeout=1.2, keep=False)
            time.sleep(0.5)                    # 2026-09-18：0.9 → 0.5
            # 内容级复核：认得出目标会话最近的内容才算成功
            idn, idn_why = self.chat_identity_ok(chat_id, gui=gui)
            if idn is True:
                return True, "搜索框路线成功（点的是 OCR「%s」那行）：%s" % (str(info.get("name"))[:10], idn_why)
            # ⛔ 2026-09-16 加（已知现象：「他点了一下搜索框，又不点，又搁那划会话列表」）：
            #   实测日志：点了搜索结果里**名字匹配**的目标行，但 `chat_identity_ok` 因为
            #   **「活动行时间戳读不出」** 判否（wechat.py:3047）⇒ 这里 `idn is False` ⇒ 整条搜索路线判失败
            #   ⇒ 回退到「找会话行 + 滚轮」老路 ⇒ 用户看到的就是"它在划会话列表"。
            #   ⇒ 但「读不出」按同文件 :3022-3024 的既有口径本该是**自检不可用**（None），不是「证据说不是」。
            #     在**搜索路线**这个上下文里（点的行是名字匹配出来的、不是猜位置）按**弱证据**计切成功。
            #   ⚠️ 只放宽"切换"这一步，**发送闸一个字不动** —— 真正发消息仍要另过「内容 × 活动行时间」。
            #   ⚠️ 这里靠文案判断（"读不出"）是有意留的窄口子；改 3047 的文案必须同步这里。
            _why_s = str(idn_why)
            if idn is None or ("读不出" in _why_s):
                return True, ("搜索框路线：内容级判据这次不可用 / 活动行时间戳读不出（%s），"
                              "但点的是名字匹配「%s」的结果行 ⇒ 按弱证据计切成功"
                              "（发送闸仍要另过内容×活动行时间）"
                              % (_why_s[:80], str(info.get("name"))[:10]))
            return False, "点过搜索结果了，但内容复核={} （{}）".format(idn, _why_s[:120])
        except Exception as e:
            return False, "搜索框切会话异常：%s" % e
        finally:
            # ⚠️ 这条链会抢前台（开浮层/投字/点行，实测浮层 1.63s + 主窗 8.96s 且不还）⇒ 出去一律还回去。
            #    成功路径也照还：切完会话不需要占着前台。
            try:
                # ⚡ 2026-09-18 晚：**本次新开的**搜索窗口一律关掉（含独立「搜索聊天记录」窗）——
                #    作者现场「你怎么点出来个搜索聊天记录啊」就是失败路径把窗留在屏幕上造成的。
                _closed = self.close_search_popovers(gui=gui, only_new=_pre_sw)
                if _closed:
                    log.info("切会话·搜索路线收尾：关掉本次打开的搜索窗口 %d 个", _closed)
            except Exception:
                pass
            try:
                _restore_fg_until("切会话·搜索路线", timeout=2.5, keep=False)
            except Exception:
                pass

    def send_text_posted(self, text: str, chat_id: str = "filehelper", wait_s: float = 15.0,
                         allow_no_ref: bool = False):
        """**投递发送**（L5，2026-09-13 实测过的那条链）：投递 WM_CHAR 打字 + 投递点「发送」按钮。

        与 `send_text` 的区别（也是它的适用边界）：
          · 全程**不动光标（伪激活可能短暂置前约 1~3 秒后自动还回）、不要求窗口可见**；`GetCursorPos` 前后不变；
          · **前提＝目标会话已经打开** —— 投递不负责切会话（切会话的投递版仍未取证）。
            调用方要么自己确认会话已打开，要么先用别的方式切过去。
        成功判据：**只认 DB 回读**（轮询到新行且内容含本段文本），不信 GUI 返回值。

        参考实测：投递打字 + 投递点「发送」（3/3、DB 回读命中）。
        """
        _halt = _control_halt()      # 暂停/停止闸：**已开工的链也要停**（2026-09-18）
        if _halt:
            return False, _halt
        # OCR 总时间窗（测机手册 ④）：这一笔发送链的 OCR 总预算（超时按"自检不可用"处理）
        from . import chat_ocr as _co
        _co.begin_window(_co.SEND_WINDOW_S)
        from . import input_backend as ib
        try:
            gui = self._get_gui()
            backend = ib.select_backend(gui=gui)
            if not isinstance(backend, ib.MessageBackend):
                return False, "当前输入后端不是投递档（config.input.backend=%s）" % backend.name
            main = int(getattr(gui, "main_hwnd", 0) or 0) or ib.find_main_window()
            if not main:
                return False, "找不到微信主窗"
            self._ensure_main_visible(gui, main)   # 最小化 ⇒ 先不激活地还原（发送链的会话头自检要抓图）
            if not gui.render_rect:
                gui._update_render_rect()
            _stash_fg()      # ⚠️ 投递链的伪激活会让微信**短暂真占前台**（跨机 r15 实测 1.8s）⇒ 记下用户窗口，
                             #    发完（下面点完「发送」）立刻还用 `_restore_fg_until`，把这段压到最短。
            # 会话头校验（防发错会话）：投递**不会切会话** ⇒ 必须有"当前会话＝目标会话"的**正面证据**才准发。
            # ⛔ 2026-09-13 实测事故（检验包自测暴露）：no_ref（当前尺寸没参照）时照发 ⇒ 文本被打进
            #    **当时打开的另一个会话**并真的发了出去（发给了联系人 E），而 DB 里查目标会话自然查不到，
            #    表观症状只是"发送未生效"，**发错会话这件事被完全掩盖**。
            # ⇒ 现在：mismatch / no_ref / no_capture **一律拒绝投递**；`allow_no_ref=True` 只在调用方
            #    自己已经确认过"目标会话就是当前打开的"时使用（例如已用真实路径打开过）。
            try:
                from . import chat_header as _ch
                _st = _ch.check(chat_id)
                # ⛔ 2026-09-18 加（用户追问：「用 OCR 这种，它有时候读一读就不匹配，导致本来能发对的发错，
                #   不发错的会发错，对的会发错」）：**指纹是弱档，不许单独授权发消息**。
                #   `chat_is_open` 里早就写明"对面 r25 实测两个不同会话同时判 True，授权写动作的最后一道闸
                #   绝不接它"—— 但这条发送链一直拿 `_ch.check`（纯指纹）的 ok 当放行依据 ⇒ 自相矛盾，
                #   一假阳性就把消息发进另一个会话（＝"对的会发错"）。
                #   ⇒ 指纹 ok 时**再要一档有区分力的证据**（名字 OCR / 会话头标题带 OCR / 活动行时间×DB）；
                #   拿不到就**先按名字切一次会话**（按名字选行，比"当前开着的恰好是它"可靠得多），
                #   切成了照样发；两条都不成才拒（并在说明里点明缺哪一档，不静默）。
                if _st["status"] == "ok":
                    _strong, _strong_why = self.chat_is_open(chat_id, gui=gui, name=name)
                    if not _strong:
                        log.warning("指纹档 ok 但**强档给不出**（%s）⇒ 不直接发，先按名字切会话再试",
                                    str(_strong_why)[:90])
                        _sw2_ok, _sw2_why = self.switch_chat_posted(chat_id, gui=gui, name=name)
                        if not _sw2_ok:
                            return False, ("只有会话头指纹这一档成立（**弱档、会假阳性**），"
                                           "按名字切会话也没成（%s）⇒ 这条不发：宁可漏发，绝不发错会话。"
                                           "（想让它发：把目标会话在微信里点开，或让它在会话列表里能被认出来）"
                                           % str(_sw2_why)[:70])
                        log.info("按名字切会话成功 ⇒ 继续发（%s）", str(_sw2_why)[:70])
                        _st = {"status": "ok", "sim": 1.0,
                               "note": "按名字切会话后重来（%s）" % str(_sw2_why)[:50]}
                if _st["status"] == "mismatch":
                    # 🔴 2026-09-18 修（拍摄现场 02:09：三句文字回复全被拒发、群里只有图没有话）：
                    #   日志原文「投递切会话后发送失败：会话头不匹配，拒绝投递（防发错会话）：相似度 0.530 < 0.90」。
                    #   **指纹是弱档**（对面 r25 实测它对不同会话也判 True＝会假阳性；同理参照过期/空白帧会
                    #   假阴性），它不该有单独否决**强档**证据的权力 —— 而同一刻 `chat_is_open` 的强档里
                    #   「会话头标题带 OCR='演示（3）'」明明是命中的。⇒ 判 mismatch 时**先问强档**
                    #   （名字 OCR / 会话头标题带 / 高亮行时间×DB，都是能回答"现在是谁"的证据）：
                    #   强档成立 ⇒ 放行并**把这个尺寸的参照重学一遍**（旧参照已经不可信）；强档也给不出才拒。
                    #   ⚠️ 红线没有放宽：强档给不出证据时，这里仍然拒发。
                    _ok_strong, _why_strong = self.chat_is_open(chat_id, gui=gui)
                    if not _ok_strong:
                        return False, "会话头不匹配，拒绝投递（防发错会话）：%s" % _st["note"]
                    log.warning("会话头指纹判 mismatch（%s），但**强档证据成立** ⇒ 放行并重学参照：%s",
                                str(_st["note"])[:70], str(_why_strong)[:130])
                    try:
                        log.info("重学参照（旧参照已不可信）：%s", self._learn_chat_header(chat_id, gui=gui))
                    except Exception as _e:
                        log.debug("重学参照失败（不影响本次放行）：%s", _e)
                if _st["status"] in ("no_ref", "no_capture") and not allow_no_ref:
                    # ⛔ 死锁修复（2026-09-16 对面 r23 现场）：老代码在这里**直接拒**，而参照只在
                    #    "发送成功之后"才学 ⇒ `no_ref` 一旦成立就永远拒、永远学不到 —— 最小化与
                    #    D 轴那两格就是这么被堵在门口的（对面实测：全日志里"自动补参照"一次都没
                    #    执行过）。⇒ 指纹档给不出结论时**改问四档证据**（`chat_is_open`：OCR 名字 /
                    #    指纹 / 活动行时间×DB / 会话头标题带 OCR，四者取或）；四档里任何一档给出
                    #    独立屏幕证据就放行，全都给不出才拒。**红线没有放宽**：`mismatch` 仍直接拒。
                    _ok_any, _why_any = self.chat_is_open(chat_id, gui=gui)
                    if not _ok_any:
                        return False, ("当前尺寸没有目标会话的参照（%s），四档证据也都给不出"
                                       "⇒ 无法确认打开的会话就是目标会话，拒绝投递（%s）"
                                       % (_st["status"], str(_why_any)[:120]))
                    log.info("会话头未校验（%s），但四档证据成立 ⇒ 放行投递：%s",
                             _st["status"], str(_why_any)[:140])
                    # 顺手把该尺寸的参照学到手 —— 这一步就是破死锁的钥匙
                    log.info("放行时补参照：%s", self._learn_chat_header(chat_id, gui=gui))
                if _st["status"] in ("no_ref", "no_capture") and allow_no_ref:
                    log.info("会话头未校验（调用方显式允许，%s）：%s", _st["status"], _st["note"])
            except Exception as _e:
                log.warning("会话头校验跳过：%s", _e)
            r = gui.render_rect or (0, 0, 0, 0)
            rw, rh = int(r[2] - r[0]), int(r[3] - r[1])
            if rw <= 0 or rh <= 0:
                return False, "渲染区未知（窗口不可见？）"

            def _rows():
                try:
                    return list(self._db.get_messages(chat_id, limit=12) or [])
                except Exception:
                    return []

            base = _rows()
            base_sig = str(base[0].get("local_id")) if base else ""
            # ⛔ 2026-09-16 r24 对面现场：**最小化还原之后投递打字不生效**。
            #    他的对照很干净：同一会话、同一轮里，可见态两枪（A1/A2）回读都成功（local_id 27/28），
            #    只有最小化那一枪读不到新行，而且那个 token 在「文件传输助手」与「E」里
            #    **两处都搜不到** ⇒ 没发错会话、也不是自检误判 ⇒ 就是"字没进输入框"。
            #    机制：这条链**从来不点输入框**（2026-09-13 实测"不点也能发 3/3"——那是**正常可见态**
            #    下靠默认焦点）；最小化被还原后焦点不在输入框上，`WM_CHAR` 被丢，随后点「发送」发了个空。
            #    （发文件那条链不受影响：走 `WM_SETTEXT` 直写对话框，不依赖输入框焦点。）
            #    ⇒ 打字前先投递点一次输入栏把焦点给它。坐标与「发送」按钮**同源**（渲染区比例、同一行
            #    靠左的输入区），正常态下这一步是幂等的；点不到也不拦（继续尝试打字，失败时行为同旧版）。
            try:
                # ⛔ 2026-09-17 **红线修复（微信截图被按开的真因就是这一行）**：
                #    原来写的是 `rh * 0.945` —— 那**不是输入框**，而是输入框下沿**再往下那排工具图标
                #    （😊 表情 / 📁 文件 / ✂ **截图** / 🎤 语音）所在的高度。投递这一枪落到 `✂` 上，
                #    微信截图工具当场开起来：**整屏压暗 5~6 秒**，用户连报四次「机器人发消息那一刻
                #    微信自己弹了截图」。
                #    取证（三条互相咬合）：①进程内输入审计 `data/input_audit.log` 记到该次
                #    `PostMessageW(client 163,1023) ← wechat.py:2362`，而 **1023 = 0.945 × 1083**；
                #    ②录屏逐帧亮度扫描（`signalstats YAVG`）在每次发送的同一秒测到整屏压暗
                #    （172.8 → 120.3）；③从帧上量：输入框白色区底边在相对高度 **0.938**，
                #    0.945 恰好落在它下面 ⇒ 那一枪**本来就没点到输入框**。
                #    ⚠️ 真正提交发送走的是投递 **Enter**（审计里 `WM_KEYDOWN VK=13`），所以这个
                #    "聚焦"click 是**可以少打一枪**的；它唯一的作用是「最小化还原后焦点不在输入框」
                #    那个已知场景（2026-09-16 r24）⇒ 保留动作、**只把落点抬进正文区**。
                # 🔴 2026-09-18 重写（作者现场口径，见 `_input_top_band` 上方的注释）：
                #    老实现按**渲染区比例 0.87** 点 —— 那不是输入框上沿，而是输入框**下部**；
                #    引用一条长消息后输入栏变高，这一枪正好落在**引用条**上（点不动/一直点引用），
                #    甚至点到 ✕ 把引用取消。⇒ 改成"现算 + 只取上沿带"；量不到就**不下这一枪**。
                _ibox, focus_pt = None, None
                _ibox, focus_pt = _input_top_band(gui)
                if not _ibox:
                    log.info("投递聚焦输入栏：**量不到输入框（拿不到窗口自身画面）⇒ 不下这一枪**"
                             "（按作者口径不许按比例猜点；下面靠打字后的阳性对照兜）")
                elif focus_pt[1] > int(r[1] + rh * 0.92):
                    log.info("跳过聚焦点击（上沿带落点 %s 已进工具栏带，按红线不点）", focus_pt)
                else:
                    log.info("投递聚焦输入栏：点 %s（实测输入框 %s，只取上沿带）", focus_pt, _ibox)
                    _fok, _fwhy = self._click_posted(backend, main, focus_pt, "聚焦输入栏")
                    if not _fok:
                        return False, _fwhy
                    time.sleep(0.3)
            except Exception as _e:
                log.info("投递聚焦输入栏失败（继续尝试打字）：%s", _e)
            ok, why = backend.send_text(main, text)
            if not ok:
                return False, "投递打字失败：%s" % why
            time.sleep(0.45)
            # 阳性对照（2026-09-18 加，作者口径"点完必须证明字真进框了"）：空输入框只有浅灰占位符，
            # 打完字上沿条带必须出现深色点。拿不到帧（-1）不算失败，只留痕；**判定为 0 就如实报失败**，
            # 不许再退回"按比例猜个位置再点一次"那条老路。
            _box2 = _probe_input_box_frame(gui)
            if _box2:
                _ink = _input_ink(gui, _box2)
                if _ink == 0:
                    return False, ("输入框没吃到字（上沿条带深色点实测 0）——聚焦那枪落空；"
                                   "按作者口径不许按比例猜点，这一条如实失败")
                log.info("输入框阳性对照：上沿条带深色点 %d（≥1 ⇒ 字确实进框了）", _ink)
            # 「发送」按钮：渲染区比例 (0.932, 0.945)（1160×900 实测）。
            send_pt = (int(r[0]) + int(rw * 0.932), int(r[1]) + int(rh * 0.945))

            def _new_row():
                """DB 回读：出现新行就返回它 —— ⚠️ 这仍然是我们**唯一的成功判据**。"""
                rows = _rows()
                if rows and str(rows[0].get("local_id")) != base_sig:
                    return rows[0]
                return None

            def _one_shot(idx: int):
                """第 idx 枪：**奇数枪＝投递回车（首选），偶数枪＝投递点「发送」按钮（兜底）**。

                ⛔ 2026-09-16 修（用户反馈「不会发消息了：**写在文本框，但是不发送**」）：
                  老实现**只点一枪按钮**、然后干等 DB —— 那一枪只要没生效，**就没人补第二枪**，
                  字就一直留在输入框里（而且下一轮可能把残留连新字一起发出去）。
                  口径照抄上游 `wechatauto/guia.py::click_send()`（它有三条防护：发送前确认框里有字 /
                  发送后确认框已清空 / 最多 3 枪），本轮先抄"**多枪 + 回车优先**"这一条：
                  上游原话「输入框刚粘贴完必已聚焦，**回车最可靠**」，点按钮只在回车之后仍没发出去时用。
                  我们这条链**打字前已经投递点过输入栏**（见上）⇒ 打完字输入框必然聚焦 ⇒ 回车可行。
                """
                if idx % 2 == 1:
                    ok_k, why_k = backend.keys(main, [ib.VK_RETURN])
                    return bool(ok_k), "投递回车", why_k
                # ⛔ 2026-09-17 **不再点「发送」按钮**（用户连报四次"微信截图被按开"之后的口径）：
                #   审计（`data/input_audit.log`）证明这条链里那次点击落在 `rh*0.945` 的**工具栏带**上，
                #   而 `send_pt` 用的是**同一个渲染区 r** —— 实测 `0.45×rw = 163` ⇒ `rw ≈ 362`
                #   （只有侧栏那么宽，明显不对）⇒ 它算出的 `0.932×rw` 同样会落回**左边那排图标
                #   （😊 📁 ✂ 🎤）**，也就是同一发事故。上游 `guia.py::click_send()` 自己说「回车最可靠」，
                #   我们这条链打字前已经点过输入栏（见上）⇒ 回车可行。
                #   **取舍是刻意的**：宁可字留在输入框里（用户看得见、无害、下一轮会重发），
                #   也绝不再往那排按钮上打枪。
                ok_k2, why_k2 = backend.keys(main, [ib.VK_RETURN])
                return bool(ok_k2), "投递回车（兜底也走回车，不再点按钮）", why_k2

            _fired, _tried = 0, []
            # 🔴 2026-09-18 修（现场：机器人**回了自己刚发的两条**——「这图我看不了」→「你学我干嘛」）：
            #   回声表原来是在**DB 回读成功之后**才记（`send_text` 的成功分支里）——可微信是**先落库**、
            #   我们才回读到，中间那几秒（回读轮询 1.2s 一跳）正好落进监听器的下一次轮询窗口 ⇒
            #   监听器把"我们刚发的话"读成"别人的话"⇒ 唤醒机器人 ⇒ 回自己。
            #   ⇒ 口径改成：**开枪那一刻就记进回声表**（不等回读）；回读成功后再记一次也无害（幂等）。
            try:
                self._mark_sent(text)
            except Exception as _e:
                log.debug("开枪前记回声失败（继续）：%s", _e)
            for _i in range(1, 4):                      # 最多 3 枪（与上游 click_send 的重试次数同口径）
                _ok_s, _how, _why_s = _one_shot(_i)
                _tried.append(_how if _ok_s else "%s(没打出去)" % _how)
                if _ok_s:
                    _fired += 1
                else:
                    log.warning("投递发送第 %d 枪没打出去（%s）：%s", _i, _how, str(_why_s)[:80])
                # ⚠️ 2026-09-16 晚（跨机 r15 实测）：投递链的**伪激活**（`WM_ACTIVATE`）会让微信**短暂真占前台**
                #    ⇒ 每一枪之后都立刻盯一次还前台，把"用户窗口丢前台"的时长压到最短。
                _restore_fg_until("投递发送后", timeout=2.5, keep=False)
                # 2026-09-18 提速（实测：微信写库 ~1.6s，第一枪窗口开 5s 纯属干等）：
                #   第一枪给 2.6s，之后每枪给 3.5s——**只影响"等多久补下一枪"，不影响成功判据**
                #   （成功判据仍是"在目标会话里回读到本次内容"）。
                _deadline = time.time() + (2.6 if _i == 1 else 3.5)
                while time.time() < _deadline:
                    time.sleep(0.35)               # 2026-09-18：0.8 → 0.35（写库 ~1.6s，早发现早收工）
                    head = _new_row()
                    if not head:
                        continue
                    if str(text)[:20] in str(head.get("content") or ""):
                        # 记下「这条库行是我发的」：监听侧以后按**行号**判自己，不再只靠文本回声窗
                        # （见 `is_self_local` 的注释；必须在这一刻记 —— 这是**唯一**能确定行号归属的地方）
                        _self_local_note(self, chat_id, head.get("local_id"), head.get("create_time"))
                        # DB 回读确认成功 ⇒ 自动补一条"当前窗口尺寸"下的会话头参照
                        # （尺寸变了以后不用人工重标；下次同尺寸就能真正校验）
                        self._learn_chat_header(chat_id, gui=gui)
                        return V_OK, "投递发送成功（第 %d 枪：%s；DB 回读 local_id=%s type=%s）" % (
                            _i, _how, head.get("local_id"), head.get("type"))
                    # 2026-09-16：这条"宽松成功"以前**不学参照** ⇒ 对面 r23 实测：A 枪走这条分支
                    # 返回 ok，紧接着同一尺寸再发**仍报 no_ref**。能走到这里说明身份闸已放行 ⇒ 学参照安全。
                    self._learn_chat_header(chat_id, gui=gui)
                    return V_OK, "DB 有新行但内容与本次不一致（第 %d 枪：%s；local_id=%s，可能上一条刚写库）" % (
                        _i, _how, head.get("local_id"))
                # ── 🔴 发错会话的**当场自检**（2026-09-18 加，用户反馈「他把我在实验群发的消息回到大群了」）──
                #    目标会话回读不到 ⇒ 在下一次开枪**之前**先问一句："这句是不是落到别的会话里了？"
                #    命中就**立刻停手**：① 把"发错会话"这件事从"被完全掩盖"变成当场可见；
                #    ② 免得后面两枪再往那个错会话**重复发**同一句话（那是对外可见的事故）。
                if _fired:
                    try:
                        _mis = self._text_landed_in_other_chat(text, chat_id)
                    except Exception as _e:
                        _mis = None
                        log.debug("发错会话自检异常（按未命中继续）：%s", _e)
                    if _mis:
                        log.error("❗发错会话：这条文字出现在了「%s」（目标会话不是它）—— 已立刻停止重试",
                                  _mis[1])
                        return False, ("❗**发错会话**：这条文字出现在「%s」里，不是目标会话 —— "
                                       "已立刻停止重试（免得往错的会话连发多次）。根因＝投递不切会话，"
                                       "而当时打开的那个会话被误判成了目标会话。" % _mis[1])
            if not _fired:
                return False, "投递发送失败：三枪都没打出去（%s）" % "→".join(_tried)
            # ⚠️ 自检不可用 ≠ 发送失败（2026-09-14 测机报告：4.1.13.65 上投递其实发出去了，但回读通道失效）
            _alive, _why_alive = self.db_alive(chat_id)
            if not _alive:
                return V_UNVERIFIED, ("已投递 %d 枪（%s），但**判据不可用**、无法证实是否发出：%s"
                                      "（投递链路本身没报错）" % (_fired, "→".join(_tried), _why_alive))
            return V_NOT_SENT, ("已投递 %d 枪（%s）但 %ds 内 DB 没等到新行（发送未生效）；"
                                "**文字可能还留在输入框里**——请到微信里看一眼那个会话"
                                % (_fired, "→".join(_tried), int(wait_s)))
        except Exception as e:
            return False, str(e)
        finally:
            # 早退路径也要放回收起状态（2026-09-16 对面 r23 反馈：被 no_ref 拒发时"1.5s 后查
            # IsIconic=False"——拒发是早退，以前不走 `_restore_fg_until` ⇒ 放回被漏掉）
            _minimize_back_if_needed("投递文本链收尾")

    def send_image_posted(self, chat_id: str, local_path: str, wait_s: float = 60.0):
        """**投递发图**（L5）：剪贴板放图（CF_DIB）→ 投递 **Ctrl+V 组合键给渲染子窗** → 投递点「发送」→ **DB 回读认图片**。

        实测（2026-09-13）：①**必须投给渲染子窗 `MMUIRenderSubWindowHW`**（投主窗完全无效）；
        ②图片消息的 **DB 落库延迟可达 30~60s**（文本只要 2~3s）⇒ 轮询窗口默认给到 60s，
        否则会把"其实发出去了"误判成"没生效"（本轮就因此把一次成功误判成失败）。
        2026-09-18 换了两处（现场「图片他拿到了，但是又没有发给我」）：
          ③ **粘贴改走"输入框右键 → 「粘贴」菜单项"** —— 老的"投递 Ctrl+V"在微信上不成立
             （投递消息不带修饰键状态，退化成字面字母 v，实拍见输入框冒出 `vvaavv`）；
          ④ 粘贴后加**发送按钮颜色自检**（空框=灰/有内容=绿，屏幕实拍判定），
             没进框就**一枪都不打**（避免把空消息或框里原有文字发出去）。
        提交仍是**三枪**（回车 → 点「发送」→ 回车兜底，图片只粘贴一次）。
        全程**不动光标（伪激活可能短暂置前约 1~3 秒后自动还回）**；成功判据**只认 DB 回读**。
        """
        from . import input_backend as ib
        from . import clipboard as _cb
        try:
            gui = self._get_gui()
            backend = ib.select_backend(gui=gui)
            if not isinstance(backend, ib.MessageBackend):
                return False, "当前输入后端不是投递档（config.input.backend=%s）" % backend.name
            main = int(getattr(gui, "main_hwnd", 0) or 0) or ib.find_main_window()
            if not main:
                return False, "找不到微信主窗"
            # 2026-09-18：原 `child = render_hwnd`（"粘贴/按键要打渲染子窗"）随 Ctrl+V 一起作废 ——
            #   现在粘贴走"输入框右键 → 粘贴菜单项"，不再需要这个句柄（留着就是死代码）。
            # ⛔99 发送前**大图自动压缩**（对账清单第 22 条）：压不动/不必压 ⇒ 原样发，说明进回执。
            _cnote = ""
            try:
                from . import img_compress as _ic
                local_path, _cnote = _ic.compress_if_needed(local_path)
            except Exception as _e:
                _cnote = "压缩环节异常（%s），原样发送" % type(_e).__name__
            ok_open, why_open = self.chat_is_open(chat_id, gui=gui)
            if not ok_open:
                return False, "投递发图要求目标会话已打开：%s" % why_open
            try:
                gui._update_render_rect()
            except Exception:
                pass
            r = gui.render_rect or (0, 0, 0, 0)
            rw, rh = int(r[2] - r[0]), int(r[3] - r[1])
            if rw <= 0 or rh <= 0:
                return False, "渲染区未知（窗口不可见？）"

            def _rows():
                try:
                    return list(self._db.get_messages(chat_id, limit=8) or [])
                except Exception:
                    return []

            base = _rows()
            base_id = int(base[0].get("local_id") or 0) if base else 0

            # ⛔ 2026-09-18 加：**粘贴前先投递聚焦输入栏**（与发文字同一条教训，见 :2708 的 `_FOCUS_Y` 注释）。
            #   粘贴 = "给**当前焦点**发 Ctrl+V"，焦点不在输入框时（刚切完会话 / 刚清空聊天记录 /
            #   刚被还原出来）**静默无效**，后面那一枪「发送」自然什么都发不出去 ——
            #   现场现象正是「**图片他拿到了，但是又没有发给我**」。发文字那条链早就补了这一枪（r24），
            #   发图这条一直没有。落点用**正文区比例 0.87**（0.92 是工具栏带，会点到按钮上）。
            # 🔴 2026-09-18 重写：老实现与发文字那条链同一个毛病 —— 按**渲染区比例 0.87** 点，
            #    那是输入框**下部**（引用一条长消息时，引用条正好在那儿 ⇒ 一直点引用/点到 ✕）。
            #    改成实测上沿带；量不到就不点（见 `_input_top_band` 的注释），并且**不猜落点**。
            _img_box = None
            _img_pt = None
            try:
                _img_box, _img_pt = _input_top_band(gui)
                if _img_box is None:
                    log.info("发图·投递聚焦输入栏：**量不到输入框 ⇒ 不下这一枪**（按作者口径不许按比例猜点）")
                elif _img_pt[1] > int(r[1] + rh * 0.92):
                    log.info("发图·跳过聚焦点击（上沿带落点 %s 已进工具栏带，按红线不点）", _img_pt)
                    _img_pt = None
                else:
                    log.info("发图·投递聚焦输入栏：点 %s（实测输入框 %s，只取上沿带）", _img_pt, _img_box)
                    _iok, _iwhy = self._click_posted(backend, main, _img_pt, "发图聚焦输入栏")
                    if not _iok:
                        return False, _iwhy
                    time.sleep(0.3)
            except Exception as _e:
                log.info("发图·投递聚焦输入栏失败（继续尝试粘贴）：%s", _e)

            ok_cb, why_cb = _cb.set_image(local_path)
            if not ok_cb:
                return False, "放剪贴板失败：%s" % why_cb
            # 🔴 2026-09-18 换通路（现场实测把老路判死）：**不再用投递 Ctrl+V** ——
            #   投递的消息**不携带修饰键状态**（真键盘走硬件输入队列才会设置它），微信收到键时
            #   回头问系统"Ctrl 按住没"⇒ 永远答"没按" ⇒ 组合键**退化成字面字母 v**（实测输入框里
            #   冒出 `vvaavv`；五种变体都试过：主窗/渲染子窗/带扫描码/同步 SendMessage/左 Ctrl）。
            #   ⇒ 改走**微信自己的动作路径**：输入框右键 → 「粘贴」菜单项（全程投递，不需要修饰键）。
            #   实测（2026-09-18）：这条**真能把图放进输入框**（屏幕实拍：缩略图出现、发送按钮变绿）。
            if _img_pt is None:
                return False, ("量不到输入框（拿不到窗口自身画面）⇒ **不猜落点、不右键粘贴**"
                               "（老实现按比例 0.87 点，引用长消息时会落到引用条甚至 ✕ 上）")
            _RX, _RY = _img_pt[0] - int(r[0]), _img_pt[1] - int(r[1])
            _paste_ok = False
            try:
                _paste_ok = bool(self._right_click_menu_posted(gui, _RX, _RY, "粘贴", delay=1.0))
            except Exception as _e:
                log.info("发图·右键「粘贴」异常：%s", _e)
            if not _paste_ok:
                # 菜单可能还开着 ⇒ 关掉它（别在用户屏幕上留一个浮层）
                try:
                    import win32process as _wp
                    _pid = _wp.GetWindowThreadProcessId(int(main))[1]
                    for _mh in (ib.menu_new_windows(_pid, ()) or []):
                        try:
                            import ctypes as _ct
                            if not _wm_close_safe(_mh, "关发图残留菜单"):
                                continue
                            _ct.windll.user32.PostMessageW(_ct.c_void_p(int(_mh)), 0x0010, 0, 0)
                        except Exception:
                            pass
                except Exception:
                    pass
                return False, ("发图失败：右键菜单里没点到「粘贴」（投递组合键在微信上不成立，"
                               "只剩这条后台通路；本次**没有打任何发送枪**）")
            time.sleep(1.4)                       # 等缩略图渲染进输入框
            # 自检：图真的进输入框了吗？判据＝「发送」按钮的颜色（空框＝灰、有内容＝绿）。
            # 为什么要它：只有"图确实进了框"才该打发送枪 —— 否则那几枪会把**空消息**或
            # 框里原有的文字发出去（比"没发出去"更糟）。
            _has, _has_why = self._input_has_content(gui, r)
            if not _has:
                return V_NOT_SENT, ("右键「粘贴」之后输入框里没看到内容（%s）⇒ 不发，"
                                    "也没打任何发送枪" % _has_why)
            log.info("发图·粘贴自检通过：%s", _has_why)
            send_pt = (int(r[0]) + int(rw * 0.932), int(r[1]) + int(rh * 0.945))
            # ⛔ 2026-09-18 改：**三枪**（与发文字同一口径，见 :2733 `_one_shot`）——
            #   老实现只点**一枪**「发送」然后干等 DB；那一枪没生效（伪激活后焦点/命中点有偏差）
            #   就整条判"没发出去"，日志里留下的正是「已投递粘贴并点了发送，但 60s 内 DB 没等到新行」。
            #   ⇒ 1＝**回车优先**（上游口径：输入框刚粘贴完必已聚焦，回车最可靠）、2＝点「发送」按钮、
            #     3＝回车兜底。图片**只粘贴一次**：内容在输入框里，多打几枪不会重复发送
            #     （真发出去之后输入框就空了，后续枪等于空放）。
            _deadline = time.time() + max(10.0, float(wait_s))
            _shots = (("回车", lambda: backend.keys(main, [ib.VK_RETURN])),
                      ("点「发送」", lambda: self._click_posted(backend, main, send_pt, "点「发送」")[0]),
                      ("回车（兜底）", lambda: backend.keys(main, [ib.VK_RETURN])))
            _fired = []
            # 🔴 2026-09-18 同一条修（发图版）：**开枪前就把「[图片]」记进回声表** —— 否则我们发出去的图
            #   会在监听器眼里是"别人发的图"，机器人就会去"看"自己发的图（现场实录：机器人回了
            #   自己刚发的图「这图我看不了」，接着又回自己那句话「你学我干嘛」）。
            try:
                self._mark_sent("[图片]")
            except Exception as _e:
                log.debug("发图·开枪前记回声失败（继续）：%s", _e)
            for _i, (_lbl, _act) in enumerate(_shots, 1):
                try:
                    _act()
                    _fired.append(_lbl)
                except Exception as _e:
                    log.info("发图·第 %d 枪（%s）异常：%s", _i, _lbl, _e)
                _due = min(_deadline, time.time() + max(6.0, float(wait_s) / 3.0))
                while time.time() < _due:
                    time.sleep(1.2)
                    rows = _rows()
                    if rows:
                        top = rows[0]
                        try:
                            new_id = int(top.get("local_id") or 0)
                        except Exception:
                            new_id = 0
                        if new_id > base_id:
                            # 只有在"回读到的这行确实是图片"时才登记成自己发的 —— 登记错了会把
                            # **别人的话**丢掉（漏回），比漏判回声更糟。不是图片类 ⇒ 照旧报成功、不登记。
                            if self._looks_like_img_msg(top):
                                _self_local_note(self, chat_id, top.get("local_id"),
                                                     top.get("create_time"))
                            return V_OK, ("投递发图成功（第 %d 枪 %s · DB 回读 local_id=%s type=%s）"
                                          % (_i, _lbl, top.get("local_id"),
                                             top.get("type_name") or top.get("type")))
                if time.time() >= _deadline:
                    break
            # 🔴 2026-09-18 加：**屏幕兜底确认**（DB 回读看不见时别急着判失败）。
            #   现场（01:59）：图明明发出去了（群里看得见），可 DB 回读**看不到新行**（用户清空过聊天记录，
            #   这个会话的消息表被重建/还没落位）⇒ 判「判据不可用、无法证实」⇒ 退回真鼠标路径被拒
            #   ⇒ 调用方拿到 False，接着又重试，行为很乱。
            #   判据：**发送按钮从绿变灰＝输入框已清空＝内容确实离手**（与进框自检同一把尺子，
            #   都走屏幕实拍）。它不依赖数据库，正好补上"清空过聊天记录的会话读不到新行"这个洞。
            #   ⚠️ 口径：这是**屏幕证据**，不是 DB 回读 —— 回执里必须把两种判据分别写清，不许混为一谈。
            _still, _still_why = self._input_has_content(gui, r)
            if not _still:
                return V_OK, ("投递发图：**输入框已清空**（%s）⇒ 按屏幕证据判已发出"
                              "（DB 回读没等到新行；打了 %d 枪 %s）"
                              % (_still_why, len(_fired), "→".join(_fired) if _fired else "0"))
            _alive2, _why_alive2 = self.db_alive(chat_id)
            if not _alive2:
                return V_UNVERIFIED, ("已投递粘贴并打了 %d 枪%s，但**判据不可用**、无法证实：%s"
                                      % (len(_fired), ("（%s）" % "→".join(_fired)) if _fired else "", _why_alive2))
            return V_NOT_SENT, ("已投递粘贴并打了 %d 枪%s，但 %ds 内 DB 没等到新行（发图未生效；"
                                "文字可能还留在输入框里）"
                                % (len(_fired), ("（%s）" % "→".join(_fired)) if _fired else "",
                                   int(wait_s)))
        except Exception as e:
            return False, "投递发图异常：%s" % e

    # ── 发文件（消息驱动；2026-09-13 实测打通）─────────────────────────────
    @staticmethod
    def _input_bar_row(gray, band_px=200, gray_thr=None, gap=None, pane_left=0):
        """（薄壳）输入栏图标行 ⇒ `(y_abs, [工具栏那一组的中心x…])`；找不到 ⇒ `(0, [])`。

        ⚠️ **唯一实现在 `agent/input_bar.py`**（2026-09-16 立的规矩）：跨机 r7 实测，
        同一台机器同一屏，产品数出 4 簇、探针数出 5 簇（漏了最左那个 😊）⇒ 产品按"第 3 簇"取
        就取到 ✂️截图（全档错位）。两份实现必然各测各的 ⇒ 产品与探针都只许 import 那一份。
        """
        from . import input_bar as _ib
        y, cl, _total = _ib.best_row(gray, band_px=band_px)
        run = _ib.toolbar_run(cl, pane_left=pane_left)
        return (y, [c[0] for c in run]) if run else (0, [])

    @staticmethod
    def _input_bar_state(gui=None, img=None):
        """当前视图里**输入栏工具栏在不在** ⇒ (状态, 簇个数, 说明)；状态 True/False/None。

        判据＝`agent/input_bar.py` 里那套"底部有小簇排"的检测；画面不可信（最小化/纯色假帧
        std<3）⇒ 返回 **None ⇒ 上层不判断、不拦**（启发式必须 fail-open：宁可如实失败也不误拦）。
        """
        try:
            from . import input_bar as _ib
            from PIL import ImageStat
            if img is None:
                from . import chat_header as _ch
                img = _ch.capture_image(gui=gui)
            if img is None:
                return None, 0, "抓不到渲染区画面"
            g = img.convert("L")
            band = g.crop((0, max(0, g.size[1] - _ib.BAND_PX), g.size[0], g.size[1]))
            st = ImageStat.Stat(band)
            if st.stddev[0] < 3:
                return None, 0, "画面是纯色假帧（最小化/被遮挡？）std=%.1f" % st.stddev[0]
            _y, centers = WeChatAdapter._input_bar_row(g)
            if len(centers) >= 3:
                return True, len(centers), "找到 %d 个图标簇" % len(centers)
            return (False, len(centers),
                    "工具栏那排只认出 %d 簇（聊天视图应约 5 个：表情·收藏·文件·截图·语音）" % len(centers))
        except Exception as e:
            return None, 0, "判不了：%s" % str(e)[:80]

    @classmethod
    def _file_panel_point_live(cls, render_rect, pane_left, gray=None):
        """**优先用实测图标行**定位「文件」图标（工具栏第 3 个），拿不到才退回常量偏移。

        为什么要这样（用户 2026-09-15 原话：「实测投递是成功的，但是他老是点错位置，不是点到截图、
        就是点到收藏、还有点到语音，很难调」）：`_file_panel_point()` 用的是**固定物理像素偏移**
        （pane+43/97/151/205/280，本机 150% 下标的）。换台机器/换 DPI，间距就变（125% 下 ≈45px）
        ⇒ 固定偏移会**整档错位**。正解＝按顺序取工具栏那一组的第 3 个（表情·收藏·**文件**·截图·语音），
        并且**把该行的簇全列出来**（落点自证：漏了第 1 簇这种事不许再靠用户目击发现）。
        返回 `((x, y), 说明)`；说明写清"实测第 3 簇（该行几簇→工具栏几簇、间距、底往上多少）"
        还是"退回常量"，好在日志/失败信息里追责。
        """
        if gray is not None:
            from . import input_bar as _ib
            pt, why = _ib.file_point(gray, render_rect, pane_left=int(pane_left or 0))
            if pt:
                return pt, why
            return (cls._file_panel_point(render_rect, pane_left), "退回常量偏移（%s）" % why)
        return (cls._file_panel_point(render_rect, pane_left),
                "退回常量偏移（没拿到图标行：pane+151 / 渲染底−50）")

    @staticmethod
    def _file_panel_point(render_rect, pane_left: int):
        """微信输入区工具栏「文件」（文件夹图标）的位置。

        实测（2026-09-13，微信 4.1.15.8 / 150% DPI）：工具栏图标行在**渲染区底部往上 50px**，
        图标 x 是**相对聊天面板左沿的固定偏移**（库的注释：输入框与底部工具栏高度固定，窗口缩放只改变消息区高度）：
        😊表情=+43 · 📦收藏=+97 · 📁文件=+151 · ✂️截图=+205 · 🎤语音=+280。
        ⛔ 别用窗口宽度比例推算（那会把"收藏"当成"文件"——已踩过：点 428 弹出的是收藏选择窗）。
        """
        r = render_rect or (0, 0, 0, 0)
        rh = int(r[3] - r[1])
        return (int(r[0]) + int(pane_left) + 151, int(r[1]) + rh - 50)

    @staticmethod
    def _looks_like_file_msg(row) -> bool:
        """DB 回读的行是不是"文件类"消息（本机实测文件消息 type='文件/链接/卡片'）。"""
        if not row:
            return False
        t = str(row.get("type_name") or row.get("type") or "")
        return ("文件" in t) or ("链接" in t) or ("卡片" in t)

    @staticmethod
    def _looks_like_img_msg(row) -> bool:
        """DB 回读的行是不是"图片类"消息（本机实测图片消息 type='图片'）。"""
        if not row:
            return False
        t = str(row.get("type_name") or row.get("type") or "")
        return ("图片" in t) or ("image" in t.lower())

    @staticmethod
    def _sent_file_log_path() -> str:
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sent_files.json")

    def _repeat_guard(self, chat_id: str, path: str, window_s: int = 600, note: bool = False):
        """防重复发送：同一会话 + 同一文件（绝对路径+大小+mtime）在 window_s 内发过 ⇒ 拒绝。

        为什么加（2026-09-13 用户当场发现"你发了两个文件给我，一模一样的"）：我先跑脚本探针、
        又跑产品函数各发一次同一个 zip ⇒ 同一个会话里出现两份一模一样的文件。
        验证"这条路可行"只需要**一次**实发；重复实发既浪费又让用户困惑 ⇒ 加这道闸。
        返回 (ok, why)；守卫自身出错时**放行**（它只是安全网，不该挡住正常发送），但会记一行日志。

        ⚠️ **默认只读、不记账**（2026-09-13 实测 bug）：原实现"检查时就写台账"，于是一次因为
        **别的原因**失败（名字闸/内容闸拦下）的尝试也会把台账写脏 ⇒ 之后 10 分钟内的真重试全被判
        "已经发过了"，而且每次失败重试还会把时间戳刷新，**永远发不出去**。记账改到**发送成功
        （DB 回读确认新文件行）之后**由 `note=True` 那次调用完成。
        """
        import json as _json
        try:
            fp = self._sent_file_log_path()
            st = os.stat(os.path.abspath(path))
            key = "%s|%d|%d" % (os.path.abspath(path), st.st_size, int(st.st_mtime))
            full = chat_id + "|" + key
            data = {}
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = _json.load(f) or {}
            except Exception:
                data = {}
            now = time.time()
            prev = float(data.get(full) or 0)
            if prev and (now - prev) < window_s and not note:
                return False, "同一文件在 %d 秒内已经发给这个会话了（%s）——拒绝重复发送；确实要再发请显式传 allow_repeat=True" % (
                    int(now - prev), os.path.basename(path))
            # ⚠️ 只有 note=True（＝调用方已用 DB 回读确认发出去了）才写台账：默认的"检查"必须**只读**，
            #    否则一次因为别的原因（身份闸/名字闸）失败的尝试也会留下记录，把之后 10 分钟的真重试全堵死。
            if not note:
                return True, ""
            data[full] = now
            for k in list(data.keys()):
                try:
                    if now - float(data[k] or 0) > 7 * 86400:
                        data.pop(k, None)
                except Exception:
                    data.pop(k, None)
            try:
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                tmp = fp + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    _json.dump(data, f, ensure_ascii=False)
                os.replace(tmp, fp)
            except Exception as e:
                log.warning("发文件去重台账写盘失败（不影响本次发送）：%s", e)
            return True, ""
        except Exception as e:
            try:
                log.warning("发文件去重守卫异常（放行）：%s", e)
            except Exception:
                pass
            return True, ""

    def send_file_posted(self, chat_id: str, local_path: str, wait_s: float = 90.0, allow_repeat: bool = False,
                         confirm_open: bool = False):
        """**消息驱动发文件**（全程不动鼠标；会短暂弹出「选择文件」对话框）。

        链路（2026-09-13 在文件传输助手实测成功，DB 回读 `local_id=567 type=文件/链接/卡片`）：
          ① 会话闸：目标会话必须已打开且被 OCR 正面确认（拿不到证据就**拒绝**，防误发）；
          ② **投递点击**微信工具栏的「文件」图标 → 弹出系统「选择文件」对话框；
          ③ **UIA（不碰鼠标）**：文件名框 `SetValue(路径)` → 点「打开」（没找到按钮就对该框回车）；
          ④ **投递点击**「发送」（微信此时只是把文件挂成草稿，必须再点一次发送）；
          ⑤ **只认 DB 回读**：出现"文件/链接/卡片"类型的新行才算成功。

        ⚠️ 已知代价：第②步会弹出一个**系统文件对话框**（会短暂抢前台）——所以这条不是"纯后台"，
        是"不抢鼠标 + 会闪一个系统对话框"；产品侧要做成显式开关。剪贴板那条（CF_HDROP+投递 Ctrl+V）
        实测**完全无效**（微信 4.1.15.8 不收），别再往那条路上试。
        """
        # W7 版本门：没实测过的版本对默认暂停自动发送（控制台可临时放行）
        try:
            from . import version_gate as _vg
            _g = _vg.check("send", wechat=wx_version_for_gate())
            if not _g["allow"]:
                _vg.note_blocked("send", _g["reason"])      # 记账：控制台横幅与日志都要看得见
                return False, _g["reason"]
        except Exception:
            pass
        # OCR 总时间窗（测机手册 ④）：这一笔发送链的 OCR 总预算（超时按"自检不可用"处理）
        from . import chat_ocr as _co
        _co.begin_window(_co.SEND_WINDOW_S)
        from . import input_backend as ib
        try:
            import uiautomation as auto
        except Exception as e:
            return False, "发文件需要 uiautomation（UIA 驱动系统对话框）：%s" % e
        import win32gui
        try:
            if not allow_repeat:
                ok_rep, why_rep = self._repeat_guard(chat_id, local_path)
                if not ok_rep:
                    return False, why_rep
            gui = self._get_gui()
            backend = ib.select_backend(gui=gui)
            if not isinstance(backend, ib.MessageBackend):
                return False, "当前输入后端不是投递档（config.input.backend=%s）" % backend.name
            ok_open, why_open = self.chat_is_open(chat_id, gui=gui)
            if not ok_open and not confirm_open:
                # ⚠️ 名字闸判不过时，**内容级正面证据可以顶替**（2026-09-13 实测）：E 这种会话的
                #    绿底行名字本机 OCR 读不出（`current_chat_name` 返回空），但聊天区里能认出
                #    目标会话独有的短指纹（`2qqwq`）⇒ 后者是更硬的证据（项目口径：最后一道闸是内容）。
                _idn0, _why0 = self.chat_identity_ok(chat_id, gui=gui)
                if _idn0 is not True:
                    return False, "发文件要求目标会话已打开且被确认：%s" % why_open
                ok_open, why_open = True, "内容级证据顶替名字闸：%s" % _why0
            if not ok_open:
                # ⚠️ 措辞（2026-09-16 r26 对面指出）：**这是"调用方声明"，不是"真人当面确认"**——
                #    写"用户当面确认"会让读日志的人以为有人点过头。下面所有文案统一改成
                #    "调用方声明确认（confirm_open=True）"。
                log.warning("发文件：**调用方声明确认**（confirm_open=True）当前会话＝目标会话，"
                            "跳过自动会话闸（自动判据：%s）", why_open)
            # ⛔ 内容级身份闸（2026-09-13 发错会话事故后加）：**名字自检会骗人**——
            #    群聊行的预览带发言人前缀（`E: 提交信息…`），会被当成"会话名＝E"从而点进那个群。
            idn, idn_why = self.chat_identity_ok(chat_id, gui=gui)
            if idn is False:
                # ⛔ 默认 fail-closed；但 `confirm_open=True`（**调用方声明**"当前开着的就是目标会话"）
                #   必须能压过**内容级**的否定 —— 这正是这个参数存在的理由（2026-09-14 实测：E 的会话
                #   明明开着，可它最近几条都是文件卡、我们的针（短 token）不在视口里 ⇒ 内容档一路落空、
                #   返回 False ⇒ 老写法**无条件拒绝**，这条通道根本走不到，等于形同虚设）。
                #   放行时**留痕**：日志 + 返回值里带上自检原文，事后能问责。
                if not confirm_open:
                    return False, "⛔ 内容核对不通过，拒绝发送（防发错会话）：%s" % idn_why
                log.warning("发文件：内容级核对判否，但**调用方声明确认**（confirm_open=True）"
                            "⇒ 按声明放行（判据原文：%s）", idn_why)
                idn_why = "%s（已按调用方声明确认放行）" % idn_why
            if idn is None and not confirm_open:
                # ⛔ 2026-09-18 改（拍摄现场：「**图片他拿到了，但是又没有发给我**」）：
                #   清空过聊天记录的会话 ⇒ 内容级证据永远拿不到（`None`）⇒ 老写法**无条件拒绝发文件**
                #   ⇒ 用户看着机器人把图下载好了却不发。而**名字档上面已经在 :3068 过了一遍**
                #   （`ok_open`，含"绿底高亮行 + 名字"与"会话头标题带"两档强证据）——
                #   名字档说"是它"、内容档说"没证据"，再拒发就是自相矛盾。
                #   ⇒ 与**发文字**那条链的既有口径对齐（那边正是这么做的，见 :1841-1854）：名字档过了就放行，
                #     但**必须记账留痕**（日志 + 返回值里带上判据原文），事后能问责。
                if ok_open:
                    log.warning("发文件：拿不到内容级证据（%s），但**名字档已确认**（%s）⇒ 按名字档放行（记账）",
                                str(idn_why)[:60], str(why_open)[:60])
                    idn_why = "%s（已按名字档放行：%s）" % (str(idn_why)[:60], str(why_open)[:60])
                else:
                    return False, ("拿不到内容级证据，拒绝发送：%s"
                                   "（若确已确认当前会话就是目标，可显式传 confirm_open=True）" % idn_why)
            main_hwnd = int(getattr(gui, "main_hwnd", 0) or 0) or ib.find_main_window()
            if not main_hwnd:
                return False, "找不到微信主窗"
            try:
                gui._update_render_rect()
            except Exception:
                pass
            r = gui.render_rect or (0, 0, 0, 0)
            rw, rh = int(r[2] - r[0]), int(r[3] - r[1])
            if rw <= 0 or rh <= 0:
                return False, "渲染区未知（窗口不可见？）"
            try:
                pane = int(gui.detect_pane_left())
            except Exception:
                pane = 331

            def _rows():
                try:
                    return list(self._db.get_messages(chat_id, limit=6) or [])
                except Exception:
                    return []

            base = _rows()
            base_id = int(base[0].get("local_id") or 0) if base else 0

            # ② 打开系统文件对话框
            _stale = _close_stale_file_dialogs()
            if _stale:
                log.warning("发文件前清掉了 %d 个残留的「选择文件」对话框（上一次异常留下的）", _stale)
            # ⚠️ 点之前**先抓一次画面**，用它做两件事（2026-09-15）：
            #    ①判「输入栏在不在」＝视图对不对（P15：不在聊天视图时那一枪必然落空）；
            #    ②**从实测图标行取坐标**（既有口径："投递是成功的，但老点错位置，不是收藏就是截图"
            #      ——固定偏移换机器/换 DPI 会整档错位，见 `_file_panel_point_live`）。
            _img, _gray = None, None
            try:
                from . import chat_header as _ch
                _img = _ch.capture_image(gui=gui)
                _gray = _img.convert("L") if _img is not None else None
            except Exception as _e:                          # noqa: BLE001
                log.warning("发文件：抓渲染区画面失败（忽略，退回常量坐标）：%s", str(_e)[:80])
            _bar, _bar_n, _bar_why = self._input_bar_state(img=_img)
            if _bar is False:
                log.warning("发文件：输入栏图标行没找到（%s）⇒ 当前可能不是聊天视图，先试着切回目标会话",
                            _bar_why)
                try:
                    _nm = str((self._nick_map or {}).get(chat_id) or "")
                    if _nm:
                        _sok, _swhy = self.open_chat_by_search(chat_id, name=_nm)
                        log.info("发文件：切回目标会话 -> %s（%s）", _sok, str(_swhy)[:80])
                        time.sleep(0.4)
                        try:
                            _img = _ch.capture_image(gui=gui)
                            _gray = _img.convert("L") if _img is not None else None
                        except Exception:
                            _gray = None
                        _bar, _bar_n, _bar_why = self._input_bar_state(img=_img)
                except Exception as _e:                      # noqa: BLE001
                    log.warning("发文件：切回目标会话失败（忽略）：%s", str(_e)[:80])
            _bar_hint = "" if _bar is not False else "（可能因为当前不是聊天视图：%s）" % _bar_why
            pt, _pt_why = self._file_panel_point_live(r, pane, gray=_gray)
            log.info("发文件：点「文件」图标 %s（%s）", pt, _pt_why)
            _stash_fg()   # ⚠️ 打开对话框**之前**记下用户当时的前台：关框后要还回去（见 _restore_fg）
            ok_c, why_c = backend.click(main_hwnd, pt)
            if not ok_c:
                return False, "投递点「文件」图标失败：%s" % why_c

            def _find_dlg(timeout=10.0):
                import time as _t
                dl = _t.time() + timeout
                while _t.time() < dl:
                    hits = []

                    def cb(h, _):
                        try:
                            if win32gui.GetClassName(h) == "#32770" and win32gui.IsWindowVisible(h):
                                t = win32gui.GetWindowText(h)
                                if "选择文件" in t or "打开" in t:
                                    hits.append(h)
                        except Exception:
                            pass
                    win32gui.EnumWindows(cb, None)
                    if hits:
                        return hits[0]
                    _t.sleep(0.3)
                return 0

            hwnd = _find_dlg(10.0)
            if not hwnd:
                return False, "没等到「选择文件」对话框（微信版本/主题不同可能按钮位置变了）%s" % _bar_hint
            # ⚠️ UIA 偶发抛 COM 错（实测 2026-09-13：`(-2147220991, '事件无法调用任何订户')`）——
            #    原来的写法一抛就 return，**把「选择文件」这个模态框留在屏幕上**，之后微信/发送全被它挡住。
            #    ⇒ 这里一律 try 住：出错就把对话框关掉再返回（绝不留模态框）。
            try:
                dlg = auto.ControlFromHandle(hwnd)
                edits, buttons = [], []
                for c, _d in auto.WalkControl(dlg, maxDepth=14):
                    try:
                        if c.ControlTypeName == "EditControl" and c.IsEnabled:
                            edits.append(c)
                        elif c.ControlTypeName == "ButtonControl" and c.IsEnabled:
                            buttons.append(c)
                    except Exception:
                        continue
            except Exception as e:
                _close_file_dialog(int(hwnd))
                return False, "驱动「选择文件」对话框时出错（已把对话框关掉，不会留在你屏幕上）：%s" % e
            target = None
            for c in edits:
                if str(c.AutomationId) == "1148":
                    target = c
                    break
            if target is None and edits:
                target = edits[-1]
            if target is None:
                try:
                    dlg.GetPattern(auto.PatternId.WindowPattern).Close()
                except Exception:
                    pass
                return False, "文件对话框里找不到文件名输入框"
            try:
                _okw, _whyw = _fill_dialog_name(int(hwnd), os.path.abspath(local_path))
                if _okw:
                    log.info("发文件：%s", _whyw)
                    # 进了这条就不需要"还前台"——前台压根没变（下面那两枪留着兜底）
                else:
                    log.info("发文件：%s ⇒ 退回 UIA 写文件名（写完盯着还前台）", _whyw)
                    target.GetValuePattern().SetValue(os.path.abspath(local_path))
                    time.sleep(0.15)
                    _restore_fg_until("写完文件名（粘贴那一步）", timeout=2.5, keep=True)
            except Exception as e:
                return False, "写文件名失败：%s" % e
            btn = None
            for c in buttons:
                if (c.Name or "").strip().startswith("打开"):
                    btn = c
                    break
            try:
                _oko, _whyo = _click_dialog_open(int(hwnd))
                if _oko:
                    log.info("发文件：%s", _whyo)
                elif btn is not None:
                    btn.GetInvokePattern().Invoke()
                else:
                    target.SendKeys("{Enter}")
            except Exception:
                try:
                    target.SendKeys("{Enter}")
                except Exception as e2:
                    return False, "点「打开」失败：%s" % e2
            # ⚠️ 顺序很重要（2026-09-16 时间线实测）：原来是"先 sleep 2.0 再看框关没关、最后才还前台"
            #    ⇒ 用户的窗口要多丢**约 2 秒**前台。改成：**框一消失就立刻还**（`_wait_dialog_gone` 是
            #    0.05s 轮询），实在没关（老版本要点一次回车）才走补回车那条路。
            if _wait_dialog_gone(int(hwnd), 4.0):
                _restore_fg_until("对话框关闭后", timeout=2.5, keep=False)
                time.sleep(0.6)
            else:
                try:
                    target.SendKeys("{Enter}")      # 有些版本要点一次回车才关
                except Exception:
                    pass
                _wait_dialog_gone(int(hwnd), 2.0)
                _restore_fg_until("补回车后", timeout=2.5, keep=False)
                time.sleep(0.4)

            # ④ 补一次「发送」（文件此时挂在输入框里当草稿）
            send_pt = (int(r[0]) + int(rw * 0.932), int(r[1]) + int(rh * 0.945))
            backend.click(main_hwnd, send_pt)

            # ⑤ 只认 DB 回读
            deadline = time.time() + max(15.0, float(wait_s))
            while time.time() < deadline:
                time.sleep(2.0)
                rows = _rows()
                if rows:
                    try:
                        nid = int(rows[0].get("local_id") or 0)
                    except Exception:
                        nid = 0
                    if nid > base_id:
                        top = rows[0]
                        if self._looks_like_file_msg(top):
                            # 登记「这条库行是我发的」（发文件回读的 text 是 `[文件/链接/卡片]`，
                            # 文本回声窗对它永远无效 ⇒ 只能靠行号，见 `is_self_local`）
                            _self_local_note(self, chat_id, top.get("local_id"), top.get("create_time"))
                            try:                      # 记账只在**DB 回读确认**之后
                                self._repeat_guard(chat_id, local_path, note=True)
                            except Exception:
                                pass
                            # ⚠️ 2026-09-16 r20：**顺手把这个尺寸的会话头参照学到手**——活动行时间戳读不出时，
                            #    会话头指纹（tier ②）是唯一还能用的独立证据；这条原来只在 send_text 那条链里学，
                            #    结果我这次给 E 发文件时"该尺寸没参照 ⇒ 闸门判否 ⇒ 只能走人工确认通道"。
                            try:
                                self._learn_chat_header(chat_id, gui=gui)
                            except Exception:
                                pass
                            return V_OK, "投递发文件成功（DB 回读 local_id=%s type=%s）" % (
                                top.get("local_id"), top.get("type_name") or top.get("type"))
                        return V_NOT_SENT, "发出了新消息但不是文件类（local_id=%s type=%s）" % (
                            top.get("local_id"), top.get("type_name") or top.get("type"))
            _alive3, _why_alive3 = self.db_alive(chat_id)
            if not _alive3:
                return V_UNVERIFIED, ("已走完对话框与发送，但**判据不可用**、无法证实：%s" % _why_alive3)
            return V_NOT_SENT, "已走完对话框与发送，但 %ds 内 DB 没等到新行（发文件未生效）" % int(wait_s)
        except Exception as e:
            # ⚠️ 外层异常也会**留下「选择文件」模态框**（实测：UIA 抛 COM 错时不一定落在内层 try 里，
            #    2026-09-13 一次真实发送失败后框就留在屏幕上挡住了微信）⇒ 这里再兜一次。
            _n = _close_stale_file_dialogs()
            return False, "投递发文件异常：%s%s" % (e, ("（已清掉 %d 个残留对话框）" % _n) if _n else "")
        finally:
            # 早退路径（no_ref 拒发、异常）也要放回收起状态（2026-09-16 对面 r23 反馈）
            _minimize_back_if_needed("投递文件链收尾")

    def send_image(self, chat_id: str, local_path: str):
        """发送本地图片。返回 (ok, message)。

        **投递优先**（2026-09-13）：真鼠标那条（`gui.send_image`）实测**会谎报成功**——返回
        "图片已发送：…" 但 DB 里没有任何新图片、耗时 96s、还抢前台 ⇒ 改为：目标会话已确认
        （OCR 名字判据）就走投递（剪贴板放图 + 投递 `WM_PASTE` + 投递点发送 + **DB 回读**），
        拿不到正面证据才退回真实路径（那条会用真鼠标，用完保管光标）。
        """
        # W7 版本门：没实测过的版本对默认暂停自动发送（控制台可临时放行）
        try:
            from . import version_gate as _vg
            _g = _vg.check("send", wechat=wx_version_for_gate())
            if not _g["allow"]:
                _vg.note_blocked("send", _g["reason"])      # 记账：控制台横幅与日志都要看得见
                return False, _g["reason"]
        except Exception:
            pass
        name = self.display_name(chat_id)
        try:
            with self._send_lock:
                gui = self._get_gui()
                if self._posted_preferred():
                    try:
                        ok_open, why_open = self.chat_is_open(chat_id, gui=gui)
                        if ok_open:
                            ok, msg = self.send_image_posted(chat_id, local_path)
                            if ok:
                                self._mark_sent("[图片]")
                                return True, "%s（投递档 L5）" % msg
                            log.info("投递发图失败，退回真实路径：%s", msg)
                        else:
                            log.info("投递发图前置未满足（%s）：改走真实路径", why_open)
                    except Exception as e:
                        log.info("投递发图判定异常，退回真实路径：%s", e)
                r = self._send_with_foreground(
                    lambda g=gui: g.send_image(local_path, who=name))
                ok = bool(getattr(r, "is_success", False))
                if ok:
                    self._mark_sent("[图片]")
                return ok, _resp_msg(r)
        except Exception as e:
            return False, str(e)

    # ── 表情包（收藏 / 发送）───────────────────────────────────────────
    # 微信表情原图在消息库中为加密数据（md5+len 索引），wechatauto 提供
    # 「截取最新一条表情消息气泡」的方案（EmojiMessage.capture()）——
    # 收到表情时它即会话最新一条，可截取为 PNG 存进收藏夹 data/emojis/。

    EMOJI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "emojis")

    def last_text_of(self, chat_id: str, limit: int = 8) -> str:
        """取目标会话最近一条**文本**内容（给内容级身份核对当指纹用）。"""
        try:
            for r in (self._db.get_messages(chat_id, limit=limit) or []):
                c = str(r.get("content") or "").strip()
                if len(c) >= 6 and not c.startswith("<msg"):
                    return c
        except Exception:
            pass
        return ""

    def recent_texts(self, chat_id: str, limit: int = 12, keep: int = 6, min_len: int = 4) -> list:
        """取目标会话最近的若干条**文本**内容（内容级身份核对用**多条**指纹）。

        为什么多条（2026-09-13 实测）：E 那个会话最近几条是**文件/链接**（`<msg><appmsg…` 开头），
        而 `last_text_of` 只回"最新的那条文本"（实测＝「检验91000」）—— 那条在**当前视口之外**
        ⇒ 会话明明已经打开，闸门却判"不是目标会话"，于是正确的切换被判失败、文件发不出去。

        为什么 `min_len=4`（而不是原来的 6）：E 会话里**唯一能区分它和文件传输助手**的文本指纹恰好
        是 5 个字母数字的短 token（实测：`2qqwq` 只出现在 E 的最近 120 条里、`aavv` 只出现在文件助手
        里，两边的**文件卡标题却完全一样**）⇒ 短文本要收；但**文件卡 `<title>` 一律不进指纹**
        （实测拿它当指纹时 filehelper 的重合率 42% > E 的 39%，根本分不开）。
        """
        out = []
        try:
            for r in (self._db.get_messages(chat_id, limit=max(int(limit), 1)) or []):
                c = str(r.get("content") or "").strip()
                # ⚠️ 图片/文件/引用这类消息的 content 是**原始报文**：有的以 `<msg` 开头，有的以
                #    `<?xml version="1.0"?>` 开头（2026-09-13 实测：只判 `<msg` 会漏掉后者 ⇒ 指纹表
                #    被 6 条 XML 塞满、把「2qqwq」这类真文本挤出 keep 名额 ⇒ 身份闸对**正确**的会话也判否）。
                if c.startswith("<msg") or c.startswith("<?xml") or "<msg" in c[:200]:
                    continue          # 报文一律不当指纹（标题在不同会话里会重复）
                # ⛔ 2026-09-14 修：**方括号类型占位符不是内容**。库对"文件/链接/卡片"这类消息回的是
                #   `[文件/链接/卡片]`、对文本回 `[文本]`（实测 E 的最近三条就是这两个）⇒ 老实现把它
                #   当成了"针"（`文件链接卡片` 长度够），拿它去聊天区里找**永远找不到**，而真正的短 token
                #   （`w0verify-75482` 这种）反被挤出 keep 名额 ⇒ 身份闸对**开着**的会话也判否、文件发不出去。
                #   进一步（同日实测）：这类占位符背后**很可能有真正文**——长文本/文件卡在库里是 zstd
                #   压缩存的 ⇒ 先试着从原始行解出来（解出来它反而是最好的针），解不出来才跳过。
                if re.match(r"^\[[^\[\]]{1,16}\]$", c):
                    try:
                        c = (replica_adapter.fill_text(self._db, chat_id, r.get("local_id"), c) or "").strip()
                    except Exception:
                        c = ""
                    if not c or re.match(r"^\[[^\[\]]{1,16}\]$", c):
                        continue
                if c.startswith("<msg") or c.startswith("<?xml") or "<msg" in c[:200]:
                    continue          # 报文一律不当指纹（标题在不同会话里会重复）
                alnum = "".join(ch for ch in c if ch.isalnum())
                if len(alnum) < max(4, int(min_len)):
                    continue
                if c not in out:
                    out.append(c)
                if len(out) >= max(1, int(keep)):
                    break
        except Exception:
            pass
        return out

    def recent_file_title(self, chat_id: str, limit: int = 6) -> str:
        """目标会话最近若干条里最后一条**文件/链接卡**的标题（取不到返回空串）。"""
        try:
            for r in (self._db.get_messages(chat_id, limit=max(int(limit), 1)) or []):
                c = str(r.get("content") or "")
                if c.startswith("<msg") and "<title>" in c:
                    m = re.findall(r"<title>(.*?)</title>", c, re.S)
                    if m and str(m[0]).strip():
                        return str(m[0]).strip()
        except Exception:
            pass
        return ""

    def pane_file_card_ok(self, chat_id: str, pane: str, peers=("filehelper",)):
        """**文件卡指纹**：会话最近一条文件卡的标题**在别的会话里没出现过**，且聊天区里认得出它。

        为什么要这一档（2026-09-13 实测）：E 这种会话最近几条全是文件卡，而"≥6 字文本"指纹
        全在视口之上 ⇒ 明明开着的就是 E，闸门也判否。文件名单独用不够（实测 368 那份包在 E 和
        文件助手里都有），但**"这个名字只在一个会话里出现过"＋"聊天区认得出它"**两条合起来就有
        区分度了。拿不到唯一性就返回 None（不许当证据）。
        """
        title = self.recent_file_title(chat_id)
        if not title:
            return None, "目标会话最近没有文件卡可当指纹"
        for p in peers:
            try:
                for r in (self._db.get_messages(p, limit=20) or []):
                    if title in str(r.get("content") or ""):
                        return None, "同一文件名在 %s 的最近记录里也出现过 ⇒ 不具区分度" % p
            except Exception:
                pass
        a = "".join(ch for ch in title if ch.isalnum())
        b = "".join(ch for ch in str(pane or "") if ch.isalnum())
        frag = a[-12:] if len(a) >= 12 else a
        if len(frag) >= 8 and frag in b:
            return True, ("聊天区里认出了目标会话最近那条文件卡（%r…；该文件名在同机其它会话的最近记录里没有出现）"
                          % title[:18])
        return None, "文件卡标题的显著片段没出现在聊天区（%r）" % title[:18]

    def _row_time_conflict(self, chat_id: str, gui=None):
        """**活动行时间**与目标会话"最后一条消息时间"是否**对不上** ⇒ `(conflict, 说明, decided)`。

        ⚠️ 为什么必须有（跨机 r14 的硬证据）：那台机器上 filehelper 与「E」的**首条内容逐字相同**
        （用户确实把同一批东西转进了两个会话）⇒ 内容级闸**同时放行两个目标**（r13 实测：同一屏
        `filehelper=True` 且 `E=True`）✗。而**活动行（绿底行）只有一个**——用"活动行显示的时间
        是否等于目标最后一条消息时间"就能把两者分开（那台实测：活动行 02:33＝E、filehelper 是 02:11）。

        ⚠️ **第三个返回值 `decided` 是 2026-09-16 r16 跨机报告逼出来的**：那台的活动行时间戳 OCR
        **时好时坏**（同一行 y=141，r15 读到过 2:52、r16 的 B 批读不到）⇒ 读不到时这条判据**给不出结论**，
        而如果此时还按"内容像就放行"，**双放行会原样复现**（他们实测 B 批：读不到 ⇒ filehelper=True 且
        E=True ✗）。⇒ `decided=False` 表示"没法判"，由调用方决定要不要 fail-closed
        （正对"发错会话"的历史事故面，所以内容级那一条**必须**要求 decided）。
        """
        try:
            from . import chat_ocr as _co
            _lt = self._last_time_hhmm(chat_id)
            if not _lt:
                return False, "目标最后一条消息不是今天的（列表那行不显示 HH:MM，无从比对）", False, False
            img = _co.capture_best(gui=gui or self._get_gui(), frames=2)
            _ht, _hy = _co.highlight_time(img) if img is not None else ("", None)
            if not _ht:
                # ⚠️ 2026-09-16 r20（跨机 r19 的 live 现场）：活动行时间戳是**间歇**可读的（对面实测 1/2~1/4），
                #    单帧读不出就判"自检不可用"⇒ fail-closed 常态化（他们那轮 zip 就是因为这一刻读不出而发不出去）。
                #    ⇒ **连试几帧**再下结论：捕获是新的、帧质量会变，重试成本只在失败路径上（几次 OCR，约 1~2s）。
                for _i in range(4):
                    time.sleep(0.35)
                    img = _co.capture_best(gui=gui or self._get_gui(), frames=2)
                    _ht, _hy = _co.highlight_time(img) if img is not None else ("", None)
                    if _ht:
                        break
            if not _ht:
                # ⚠️ 2026-09-16 r20：**草稿行不显示时间戳**（实测：`[草稿]…` 那一行没有时间）⇒ 这种情况下
                #    时间"本来就没有可比的"（与"目标是昨天"同类），不能算"自检不可用 ⇒ 判否"，
                #    否则那条会话**永远发不出去**（本轮我自己就卡在这儿：E 那行有草稿 ⇒ 连试 5 帧都读不出）。
                _draft_row = False
                try:
                    _im = _co.capture_best(gui=gui or self._get_gui(), frames=2)
                    if _im is not None:
                        _hl = _co.highlight(_im)
                        _hy = int((_hl or {}).get("y_abs") or 0)
                        for _r in _co.session_rows(_im):
                            if _hy and abs(int(_r.get("y_abs") or 0) - _hy) <= 40:
                                if "草稿" in str(_r.get("full") or ""):
                                    _draft_row = True
                                break
                except Exception:
                    _draft_row = False
                if _draft_row:
                    return False, "活动行是**草稿行**（不显示时间戳）⇒ 无从比对（按「本来就没有可比时间」处理）", False, False
                return False, "活动行时间戳 OCR 连试 5 帧都没读出来（判据不可用）", False, True
            if self._norm_hhmm(_ht) != self._norm_hhmm(_lt):
                return True, "活动行（y=%s）时间 %s ≠ 目标最后一条消息时间 %s" % (_hy, _ht, _lt), True, True
            return False, "", True, True
        except Exception:
            return False, "判据异常", False, False

    def _screen_only_identity(self, chat_id: str, gui=None, name: str = ""):
        """**纯屏幕证据**：会话头标题带 OCR 与目标会话名对得上 ⇒ 认"当前开着的就是它"。

        为什么必须单开这一档（2026-09-18 拍摄现场）：用户**每次拍完都用微信「清空聊天记录」**
        ⇒ 该会话的 `Msg_<md5>` 表**整张消失** ⇒ `recent_texts()` 返回空 ⇒ `chat_identity_ok`
        在**最前面**那条守卫就 `return None`（"拿不到可比对内容"）⇒ 而发文件那条链把
        `idn is None` 当**无条件拒绝** ⇒ 用户看到的现象就是「**图片他拿到了，但是又没有发给我**」。
        这一档**不依赖数据库**，只依赖"打开着的这个会话脑门上写的名字"（OCR 会话头标题带）。
        ⚠️ 强度口径：这是**强档**（能回答"现在是谁"），且是"打开着的会话的头部"——
        与会话列表那一行不是一回事（2026-09-13 误发事故的元凶是**列表预览**被当成会话名）。
        ⚠️ 单字/单字母名字必须**完全相等**：`matches()` 对单字是"以它开头"，会把 `E班群` 当成 `E`。
        顺序上它排在所有"要库里有行"的证据之后，只在那类证据全部落空时才用。
        """
        try:
            from . import chat_ocr as _co
            nm = str(name or self.display_name(chat_id) or "").strip()
            if not nm:
                # `display_name` 只认群/昵称映射，读不出时退一步用当前会话名（绿底高亮行 OCR）
                try:
                    nm = str((self.current_chat_name(gui=gui) or ("", ""))[0] or "").strip()
                except Exception:
                    nm = ""
            hdr = _co.header_text(gui=gui or self._get_gui())
            if not (nm and hdr):
                return False, "会话头标题带读不到（OCR 拿不到名字：nm=%r hdr=%r）" % (nm[:12], str(hdr)[:12])
            _a, _b = _co.norm(hdr), _co.norm(nm)
            _hit = (_a == _b) if len(_b) <= 1 else _co.matches(hdr, nm)
            if _hit:
                return True, ("会话头标题带 OCR=%r 与目标 %r 匹配（纯屏幕证据，不依赖消息行；"
                              "常见于用户清空过该会话的聊天记录）" % (str(hdr)[:20], nm[:16]))
            return False, "会话头标题带 OCR=%r 与目标 %r 不匹配" % (str(hdr)[:20], nm[:16])
        except Exception as e:
            return False, "会话头标题带比对异常：%s" % str(e)[:40]

    def chat_identity_ok(self, chat_id: str, gui=None, name: str = ""):
        """**内容级**身份核对：当前聊天区里应看得到目标会话最近若干条文本里的**任意一条**。

        返回 `(True/False/None, 说明)`；`None`＝拿不到可比对的内容（调用方按"没有正面证据"处理）。
        ⚠️ 为什么不能只信名字（2026-09-13 发错会话事故）：群聊行的预览带**发言人前缀**（`E: 提交信息…`），
        被当成"会话名＝E"后点进了那个群 ⇒ 发送前的最后一道闸必须是**内容**，不是名字。
        ⚠️ 为什么要多条指纹：见 `recent_texts()` 的注释（单条指纹会因为"最新那条文本不在视口里"误判）。
        """
        needles = self.recent_texts(chat_id)
        if not needles:
            # 🔴 2026-09-18：**清空过聊天记录**的会话会走到这里（`Msg_<md5>` 表整张没了）
            #   ⇒ 老实现直接 `return None` ⇒ 发文件链 `idn is None`＝无条件拒绝
            #   ⇒ 现场现象「图片他拿到了，但是又没有发给我」。⇒ 先试**纯屏幕证据**（不依赖数据库）。
            _ok_so, _why_so = self._screen_only_identity(chat_id, gui=gui, name=name)
            if _ok_so:
                log.info("目标会话库里没有可比对内容（清空过聊天记录？）⇒ 按纯屏幕证据放行：%s", _why_so)
                return True, _why_so
            return None, ("目标会话最近几条里没有可用作文本的比对内容（都是图片/文件，或"
                          "用户清空过聊天记录）；纯屏幕兜底也没过：%s" % _why_so)
        try:
            from . import chat_ocr as _co
            _tok = _co.begin_window(_co.SEND_WINDOW_S)     # 内容级核对整段共用 OCR 总预算（④）
            pane = _co.pane_text(_co.capture_best(gui=gui or self._get_gui(), frames=3), limit=400)
            if _co.budget_out(_tok):
                # OCR 预算用尽 ⇒ 这一条**自检不可用**：按"拿不到证据"返回（调用方 fail-closed，不发）
                return None, "OCR 预算用尽（判据不可用）：内容级核对没跑完，按「拿不到证据」处理"
            pane_n = _co.norm_alnum(pane)
            # ⛔ 2026-09-14 修（⑤ 重发实测）：**聊天区一个字都读不到**时，老实现一路走到最后返回 `False`
            #   ＝"核对不通过：当前开着的很可能不是目标会话" —— 可我们**根本没拿到证据**，这是把
            #   "自检不可用"说成了"证据说不是"（同 ④ 的教训）。后果很实在：`send_file_posted` 里
            #   `idn is False` 是**无条件拒绝**的（连 `confirm_open` 这条声明通道都走不到）⇒ 屏幕一
            #   读不出字，调用方声明"就是 E 的会话"也发不出去。⇒ 读不到就如实返回 `None`（自检不可用）。
            if not pane_n:
                # 同一条思路：**聊天区一个字都读不到**（清空过、或这一屏全图）时也先用纯屏幕证据，
                # 过了就放行；没过仍如实返回 None（＝自检不可用），不把"没证据"说成"证据说不是"。
                _ok_so2, _why_so2 = self._screen_only_identity(chat_id, gui=gui, name=name)
                if _ok_so2:
                    log.info("聊天区读不到字（清空过聊天记录/这一屏全图）⇒ 按纯屏幕证据放行：%s", _why_so2)
                    return True, _why_so2
                return None, ("聊天区一个字都没读到（判据不可用，不是「不是这个会话」）："
                              "抓图可能有遮挡/在滚动中，或这一屏确实没有文字；"
                              "纯屏幕兜底也没过：%s；"
                              "确已确认当前会话是目标时可显式传 confirm_open=True" % _why_so2)
            for nd in needles:
                nn = _co.norm_alnum(nd)
                if len(nn) >= 6:
                    if _co.content_match(pane, nd):
                        # 🔴 2026-09-18 加：**双档互证 ⇒ 直接放行，不让"活动行时间"翻案**
                        #   现场（拍摄 01:32）：会话头标题带 OCR='演示（3）'明明命中了（切会话那一步
                        #   就是靠它判成功的），可紧接着这里因为"活动行时间戳读不出"把**正确的会话**
                        #   判成 False ⇒ 文字回复连着三次全被拒发、机器人一条都没发出去。
                        #   根因：当前打开的那一行是**白字绿底**（高亮行），它的时间戳跟名字一样
                        #   读不出来 ⇒ 那条"判不了就不放行"的规则每次都误伤当前会话。
                        #   口径（写死）：**两个互不依赖的强档同时命中**时，第三档"读不出"只能算
                        #   "判据不可用"，不许翻成"不是这个会话"。会话头标题带就是那第二个档。
                        _so_ok, _so_why = self._screen_only_identity(chat_id, gui=gui, name=name)
                        if _so_ok:
                            return True, ("聊天区内容像目标（%r…）＋ %s ⇒ 双档互证，放行"
                                          % (nd[:16], str(_so_why)[:80]))
                        # ⚠️ 内容像还不够：**活动行时间必须与目标最后一条消息时间一致**（跨机 r14 的硬证据：
                        #    两个会话内容逐字相同时，内容闸会同时放行两个目标 ⇒ 用"只有一个活动行"把它分开）。
                        _cf, _cfwhy, _cdec, _ccmp = self._row_time_conflict(chat_id, gui=gui)
                        if _cf:
                            return False, ("聊天区内容像目标（%r…），但**活动行时间对不上**（%s）⇒ 判否："
                                           "同屏两个会话内容雷同时，以活动行为准" % (nd[:16], _cfwhy))
                        if _ccmp and not _cdec:
                            # ⛔ 2026-09-16 r16：**判不了就不放行**——但只在"本该判得了"的时候。
                            #    跨机实测：那台的活动行时间戳时好时坏，读不到时"内容像"对同屏两个会话
                            #    同时成立 ⇒ 双放行复现（正对"发错会话"的事故面）⇒ **目标最后一条是今天的
                            #    消息**（列表那行本该显示 HH:MM）时，读不出就等于判不了 ⇒ 判否。
                            #    若目标最后一条**不是今天**（行上显示"昨天18:xx"），本来就没有 HH:MM 可对，
                            #    这是**已知局限**（日期标签还没做比对），此时不因它拦，交给其它档。
                            if self._last_time_hhmm(chat_id):
                                return False, ("聊天区内容像目标（%r…），但**活动行时间戳读不出**（%s）⇒ 判否："
                                               "同屏内容雷同时内容这一条没有区分力，必须由活动行时间定论"
                                               % (nd[:16], _cfwhy))
                        return True, "聊天区里认出了目标会话最近的内容（%r…）" % nd[:16]
                elif nn and nn in pane_n and not _co.low_entropy(nn) and len(nn) >= 4:
                    # ⚠️ 短指纹档也要两道下界（2026-09-16 跨机 r10 的 fail-open 教训）：
                    #   低熵（纯数字）不算证据；太短（<4）也不算 —— 否则"1"这种字符都能放行。
                    return True, "聊天区里认出了目标会话的短指纹 %r（严格子串）" % nd[:12]
            # 最后一档：文件卡指纹（会话最近几条全是文件卡时，上面两档会全部落空）
            f_ok, f_why = self.pane_file_card_ok(chat_id, pane)
            if f_ok is True:
                return True, f_why
            # 再一档：**我们自己发给过这个会话的文件名**出现在聊天区（屏幕 OCR × 本机发送台账，两个独立来源）
            #   实测为什么必须有这一档（2026-09-14）：DB 里 content 是压缩占位符、会话行 OCR 只剩 `[图片]`
            #   ⇒ 文本档/文件卡档/时间档**同时**失效，而聊天区里明明就摆着我们发过去的文件卡。
            try:
                _pane_norm = _co.norm_alnum(pane)
                for _nm, _ts in self._sent_file_names(chat_id):
                    for _fp in self._file_fingerprints(_nm):
                        if _fp and _fp in _pane_norm:
                            return True, ("聊天区里能看到我们发给该会话的文件（%s）—— 版本指纹 %s 命中"
                                          % (_nm, _fp))
                # 追加（2026-09-14 实测）：**刚发完**一个文件时，微信把它显示成文件卡，长名字会被截断
                #   （实测 `兼容性矩阵.md` 显示成 `[文件]兼容性…`），而这类文件名里没有版本号 ⇒ 上面那条
                #   指纹提不出来。此时用"文件名开头 3 个字 + 刚刚发过（10 分钟内）"这一组合来认：
                #   会话是从台账里按 chat_id 取的，加上"就在刚刚"，实际指向非常具体。
                _recent = self._sent_file_names(chat_id, limit=1)
                if _recent:
                    _nm0, _ts0 = _recent[0]
                    if (time.time() - float(_ts0 or 0)) < 600:
                        _stem = os.path.splitext(_nm0)[0]
                        _head = "".join(ch for ch in _stem if ch.isalnum())[:3]
                        # ⚠️ 必须带上"文件卡前缀"一起匹配：微信把文件显示成 `[文件]<名字>…`，
                        #   只匹配名字开头会**误吞普通聊天文字**（实测：一条提到《…清单.md》的消息就把
                        #   「另一台…」三个字凑出来了 ⇒ 那是会发错会话的假阳性）。要求 `文件+开头` 同时出现，
                        #   等于限定"这是一张文件卡"，而不是"谁在文字里提过这个名字"。
                        if len(_head) >= 3 and ("文件" + _head) in _pane_norm:
                            return True, ("聊天区里能看到刚发给该会话的文件卡（%s）—— 按「文件+名字开头 %r」命中"
                                          % (_nm0, _head))
                        # 再补一手（2026-09-14 实测）：**聊天区可能停在别的滚动位置**（比如用户把一段长文本
                        #   贴在会话里、视口正好停在它上面），这时文件卡不在可见区 ⇒ 上面那条匹配不到。
                        #   但**会话列表里那一行（绿底高亮行＝当前打开的会话）本身就显示着这张文件卡**
                        #   （实测高亮行 OCR = `[文件]兼容性．“`）⇒ 把这一行的文字也纳入同一约束（同样要求
                        #   `文件`+名字开头）—— 屏幕（列表行）× 本机台账，仍然是两个独立来源。
                        if len(_head) >= 3:
                            try:
                                _himg2 = _co.capture_best(gui=gui or self._get_gui(), frames=2)
                                _hl2 = _co.highlight(_himg2) if _himg2 is not None else None
                                _row_txt = str((_hl2 or {}).get("name") or "")
                            except Exception:
                                _row_txt = ""
                            if _row_txt and ("文件" + _head) in _co.norm_alnum(_row_txt):
                                return True, ("当前会话那一行显示着刚发给该会话的文件卡（%s）—— 行文字 %r"
                                              % (_nm0, _row_txt[:24]))
            except Exception:
                pass
            # 再一档：**高亮行时间**（单字母名字读不出时唯一还读得准的信号）——高亮行＝当前打开的会话，
            # 它的时间戳对上目标会话最后一条消息的时间，且聊天区里也出现同一时间 ⇒ 认它。
            # 实测依据（2026-09-13）：E 那一行名字 OCR=''，但高亮行时间 21:41 == 370 文件卡那一刻，
            # 聊天区 OCR 里也确实有 '21：41'。
            _ok_t, _why_t = self._active_row_time_ok(chat_id, pane=pane, gui=gui)
            if _ok_t:
                return True, _why_t
            # 再一档：**聊天区里的消息时间 × DB 里那条消息的时间**（2026-09-18 拍摄现场实测后加）
            #   现场：对方会话最近全是图片/表情 ⇒ 可视区里**只有时间戳、没有文字**（实测只读到
            #   `昨天20：17昨天20：36`），文字早滚出视口 ⇒ 上面所有"文字类"证据全部落空 ⇒
            #   明明点对了会话却 fail-closed **拒发**（用户看到的就是"点对了却不发图"）。
            #   而**时间戳读得到** ⇒ 拿它做"屏幕 × DB"两个独立来源的比对，只支持相对日与今天的裸 HH:MM。
            _ok_pt, _why_pt, _pt_hits = self._pane_time_hits(chat_id, pane)
            if _ok_pt:
                return True, _why_pt
            # 🔴 最后一档＝**纯屏幕证据**（2026-09-18 加，治"用户清空过聊天记录"这个现实）：
            #   上面所有"要库里有行"的证据（文本针 / 文件卡 / 时间档）全部落空时，用会话头标题带兜底
            #   （它是"这个会话自己脑门上写的名字"，不依赖数据库）。
            _ok_so3, _why_so3 = self._screen_only_identity(chat_id, gui=gui, name=name)
            if _ok_so3:
                log.info("会话里没有可比对的消息行 ⇒ 按纯屏幕证据放行：%s", _why_so3)
                return True, _why_so3
            # ⚠️ 失败信息里**必须带观测量**（2026-09-16 跨机需求②「内容级闸的 OCR 口径」）：
            #    只报"没有目标会话的任何一条文本"分不清 **"根本没有信号"** 与 **"信号被阈值判掉"**
            #    （前者该判否，后者说明阈值/归一化有问题）⇒ 把聊天区读到多少字、每条针的最好匹配
            #    （最长命中片 + 相似度）全打出来，对面把那行发回来就能定位。
            try:
                from . import chat_ocr as _co2
                _obs = "；".join(
                    "%r→最长命中 %d 字/相似度 %.2f" % (str(_nd)[:10], *_co2.best_partial(pane, str(_nd))[:2])
                    for _nd in needles[:3])
            except Exception:
                _obs = "（观测量算不出来）"
            return False, ("聊天区里**没有**目标会话最近的任何一条文本（试过 %d 条，如 %r…；文件卡档：%s）"
                           "｜观测：聊天区读到 %d 字（前 24 字 %r）· %s ⇒ 当前开着的很可能不是目标会话"
                           % (len(needles), needles[0][:16], f_why, len(pane), pane[:24], _obs))
        except Exception as e:
            return None, "内容核对异常：%s" % type(e).__name__

    def _active_row_time_ok(self, chat_id: str, pane: str = "", gui=None):
        """**当前高亮行的时间** ＝ 目标会话最后一条消息的时间（**屏幕 × DB 两个独立来源**）。

        为什么单列成一条档（2026-09-16）：当前打开的那一行是**白字绿底**，名字 OCR 读不准
        （实测 E 被读成「巷」）⇒ 名字档、会话头指纹档都可能给不出正面证据，而会话**确实开着**
        （实测：搜索框路线已经切到 E，`chat_is_open` 却报 False ⇒ 后面每一步都在"没有正面证据"里打转）。
        第二道证据**二选一**：①聊天区里也出现同一时刻 ②该时刻在会话列表里**唯一**（＝就是那一行）。
        返回 `(True/False, 说明)`。
        """
        from . import chat_ocr as _co
        _lt = self._last_time_hhmm(chat_id)
        if not _lt:
            return False, "目标会话最后一条消息不是今天的（会话列表那行不显示 HH:MM）"
        try:
            _himg = _co.capture_best(gui=gui or self._get_gui(), frames=2)
            _ht, _hy = _co.highlight_time(_himg) if _himg is not None else ("", None)
        except Exception:
            _ht, _hy = "", None
        if not _ht:
            return False, "没读到高亮行的时间戳"
        if self._norm_hhmm(_ht) != self._norm_hhmm(_lt):
            return False, "高亮行时间是 %s ≠ 目标会话最后一条消息时间 %s" % (_ht, _lt)
        _pane_hit = self._norm_hhmm(_lt) in self._norm_times(pane or "")
        _uniq, _n = False, 0
        try:
            _rows = _co.session_rows(_himg)
            _hits = [r for r in _rows
                     if self._norm_hhmm(_lt) in self._norm_times(
                         str(r.get("full") or "") + " " + str(r.get("name") or ""))]
            _n = len(_hits)
            _uniq = (_n == 1)
        except Exception:
            _uniq = False
        if _pane_hit or _uniq:
            return True, ("高亮行（y=%s）时间 %s ＝目标会话最后一条消息时间%s"
                          % (_hy, _ht, "，聊天区里也出现同一时间" if _pane_hit
                             else "，且该时刻在会话列表里唯一（只有一个会话是它）"))
        return False, ("高亮行时间 %s 与目标一致，但聊天区里没有同一时刻、该时刻在列表里也不唯一（%d 行）"
                       % (_ht, _n))

    def _pane_time_hits(self, chat_id: str, pane: str, limit: int = 20) -> tuple:
        """聊天区里的**消息时间**对得上目标会话最近 N 条吗？→ `(ok, 说明, 命中列表)`。

        为什么单列一档（2026-09-18 拍摄现场实测）：内容级复核要求"聊天区里出现目标会话最近的**文本**"，
        可那个会话最近全是图片/表情 —— 可视区里**只有时间戳**（实测只读到 `昨天20：17昨天20：36`），
        文字早滚出视口 ⇒ 文字类证据全落空 ⇒ 明明点对了会话却 fail-closed 拒发。
        ⇒ 用**时间**当独立证据：屏幕上的时间戳 × 库里那条消息的时间，两个来源对得上就算。
        只认相对日（今天/昨天/前天）与今天的裸 `HH:MM`；显式日期（9月17日）**不猜** —— 宁可给不出证据。
        """
        import datetime as _dt
        import re as _re
        _pane = str(pane or "")
        if not _pane:
            return False, "聊天区没读到任何文字", []
        try:
            rows = self._db.get_messages(chat_id, limit=max(int(limit), 1)) or []
        except Exception as e:
            return False, "读不到目标会话的消息（%s）" % str(e)[:40], []
        want = set()
        for r in rows:
            try:
                t = float(r.get("create_time") or 0)
                if t > 1e12:
                    t = t / 1000.0
                lt = time.localtime(t)
            except Exception:
                continue
            want.add((lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, lt.tm_min))
        if not want:
            return False, "目标会话最近 %d 条没有可用时间" % len(rows), []
        now = time.localtime()
        today = _dt.date(now.tm_year, now.tm_mon, now.tm_mday)
        hits, seen = [], set()
        for m in _re.finditer(r"(昨天|今天|前天)?\s*(\d{1,2})\s*[:：]\s*(\d{2})", _pane):
            word = m.group(1) or "今天"
            hh, mm = int(m.group(2)), int(m.group(3))
            if hh > 23 or mm > 59:
                continue
            d = today - _dt.timedelta(days={"昨天": 1, "前天": 2}.get(word, 0))
            key = (d.year, d.month, d.day, hh, mm)
            if key in want and key not in seen:
                seen.add(key)
                hits.append((key, m.group(0)))
        if not hits:
            _n_ts = len(_re.findall(r"\d{1,2}\s*[:：]\s*\d{2}", _pane))
            return False, ("聊天区里的时间戳（%d 个）都对不上目标会话最近 %d 条的时间"
                           % (_n_ts, len(want))), []
        sample = "、".join(str(h[1]).strip() for h in hits[:3])
        return True, ("聊天区里的消息时间与目标会话最近的消息一致（%s）⇒ 判是这个会话"
                      "（时间档证据：屏幕时间戳 × DB 时间，两个独立来源）" % sample), hits

    def _last_time_hhmm(self, chat_id: str) -> str:
        """目标会话**最后一条消息**在会话列表里显示的时间（`H:MM`）；不是今天的消息就返回 ''。

        会话列表对"今天的消息"显示 HH:MM、更早的显示日期 ⇒ 只有今天才可用（这也是判据的一部分：
        拿不到时间就退回别的指纹，不许瞎凑）。
        """
        try:
            rows = self._db.get_messages(chat_id, limit=1) or []
            if not rows:
                return ""
            lt = time.localtime(int(rows[0].get("create_time") or 0))
            if lt.tm_yday != time.localtime().tm_yday:
                return ""
            return time.strftime("%H:%M", lt)
        except Exception:
            return ""

    @staticmethod
    def _file_fingerprints(name: str) -> list:
        """从文件名里取出"版本号那一串数字"当指纹（例：Agent启动器-2026.09.14.383.zip ⇒ 20260914383）。

        为什么不整名匹配（2026-09-14 实测）：会话行/文件卡的 OCR 会把汉字读错（实测 "Agent启动器"
        被读成 "器一"），但**日期+构建号那串数字几乎不会被读错**，而它对这个会话足够独特。
        同时给出"去前导零"的变体，防 OCR 把 09 读成 9（或反之）。
        """
        out = []
        try:
            m = re.search(r"(\d{4})[.\-_](\d{1,2})[.\-_](\d{1,2})[.\-_](\d{1,6})", str(name or ""))
            if m:
                out.append("".join(m.groups()))
                out.append("".join((g.lstrip("0") or "0") for g in m.groups()))
        except Exception:
            pass
        return [x for x in out if len(x) >= 8]

    def _sent_file_names(self, chat_id: str, limit: int = 8) -> list:
        """本机发送台账里"发给过这个会话"的文件名（按时间倒序）——给身份闸当"屏幕 × 台账"证据。

        为什么需要（2026-09-14 实测）：E 那个会话最近几条是图片/文件卡，DB 里 content 是压缩占位符
        （`[图片]` / `[文件/链接/卡片]`）⇒ 文本档、文件卡档都拿不到指纹；而会话列表那一行在"图片预览"
        布局下整行 OCR 只剩 `[图片]`（名字是单字母、时间也没读出来）⇒ 时间档也失效。
        此时唯一还准的信号是：**聊天区里能看到我们曾发给这个会话的文件名**。
        ⚠️ 局限：若同一个文件也发给过别的会话、而那个会话正开着，这一档可能误认（台账按会话分键，
        外加只取最近 limit 条、指纹是版本级数字串，实际风险很小）。
        """
        out = []
        try:
            import json as _json
            with open(self._sent_file_log_path(), "r", encoding="utf-8") as f:
                data = _json.load(f) or {}
            for k, ts in data.items():
                parts = str(k).split("|")
                if len(parts) < 2 or parts[0] != chat_id:
                    continue
                out.append((os.path.basename(parts[1]), float(ts or 0)))
        except Exception:
            return out
        out.sort(key=lambda x: x[1], reverse=True)
        return out[:max(1, int(limit))]

    @staticmethod
    def _norm_hhmm(s: str) -> str:
        """把 `'01:35'` / `'1：35'` 统一成 `'1:35'`（**实现只有一处**：`chat_ocr.hhmm`）。

        为什么必须归一化（2026-09-14 实测）：`chat_ocr.highlight_time()` 读到的是 `1:35`（会话列表不补前导零），
        而 `_last_time_hhmm()` 是 `strftime('%H:%M')` ⇒ `01:35`；两个字符串**不相等**，
        于是"高亮行时间档"对 10 点以前的时刻**永远不成立**（单字母会话名读不出时，这是唯一还准的信号）。
        ⚠️ 2026-09-16：同一个坑在 `chat_ocr.find_row_info` 的 `want_time` 比较里**又犯了一次**
        （它自己写了一遍 `'%d:%02d'`）⇒ 现在两边都走 `chat_ocr.hhmm`，不许再各归一一次。
        """
        from . import chat_ocr as _co
        return _co.hhmm(s)

    @staticmethod
    def _norm_times(text: str) -> str:
        """把一段文字里所有 `H:MM` / `HH:MM` 都去前导零、统一冒号 —— 用于"在整行/整屏里找同一个时刻"。"""
        t = re.sub(r"(?<!\d)0(\d)\s*[:：]\s*(\d{2})", r"\1:\2", str(text or ""))
        return t.replace("：", ":")

    @staticmethod
    def _bigrams(s: str) -> set:
        t = "".join(ch for ch in str(s or "") if ch.isalnum())
        return {t[i:i + 2] for i in range(max(0, len(t) - 1))}

    def recent_blob(self, chat_id: str, limit: int = 10) -> str:
        """目标会话最近若干条消息的**全部文字**（含文件卡的 `<title>`）——给弱指纹用。"""
        out = []
        try:
            for r in (self._db.get_messages(chat_id, limit=max(1, int(limit))) or []):
                c = str(r.get("content") or "")
                if c.startswith("<msg"):
                    for m in re.findall(r"<title>(.*?)</title>", c, re.S):
                        out.append(m)
                else:
                    out.append(c)
        except Exception:
            pass
        return " ".join(out)

    def pane_tail_matches(self, chat_id: str, pane: str, limit: int = 10,
                          min_ratio: float = 0.45, min_hits: int = 8):
        """**弱指纹**：聊天区可见文字是不是"这个会话"的（返回 (bool, 说明)）。

        为什么需要：`chat_identity_ok` 用的是"≥6 字文本"指纹，遇上**最近几条都是文件卡 + 短文本**的
        会话就一条都对不上（实测 E：最新 8 条里 3 条是 `<msg><appmsg…>` 文件卡、最新文本是 5 字的
        `2qqwq`）⇒ 会出现"会话其实开着、闸门却说不是目标会话"，把正确的切换判死、文件发不出去。
        这一层改判**内容归属**：目标会话最近 10 条（含文件标题）的二字组集合，与聊天区可见文字的
        二字组求交——命中率 ≥0.45 且命中数 ≥8 才算过（两个下界都是为了压"短文本偶合"）。
        """
        try:
            blob = self.recent_blob(chat_id, limit=limit)
            a, b = self._bigrams(blob), self._bigrams(pane)
            if len(b) < 6:
                return False, "聊天区文字太少（%d 个二字组），不判" % len(b)
            hit = len(a & b)
            ratio = hit / float(len(b))
            ok = ratio >= float(min_ratio) and hit >= int(min_hits)
            return ok, ("聊天区二字组 %d 个，落在目标会话最近 %d 条里的有 %d 个（%.0f%%，门槛 %.0f%%/%d）"
                        % (len(b), limit, hit, ratio * 100, min_ratio * 100, min_hits))
        except Exception as e:
            return False, "弱指纹异常：%s" % type(e).__name__

    def _chat_obj(self, chat_id: str):
        """构造 wechatauto Chat（复用当前 db/gui），用于表情截图。"""
        from wechatauto import WeChat  # 提供 ChatWith 等（实际以 Chat 为主）
        from wechatauto.wx import Chat
        name = self.group_name(chat_id) or chat_id
        gui = self._get_gui()
        return Chat(who=name, gui=gui, db=self._db)

    def collect_emoji(self, chat_id: str, local_id: int) -> str | None:
        """收藏一条消息到本地收藏夹 data/emojis/（返回路径；失败 None）。

        动画表情(47) → UIA 精确定位截图；图片(3) → 原图下载（更清晰）；
        返回值统一为收藏夹内文件路径，供 send_emoji/list_emojis 使用。
        """
        try:
            row = self._db.get_message_row(chat_id, int(local_id))
            if not row:
                return None
            lt = (row.get("local_type") or 0) & 0xFF
            os.makedirs(self.EMOJI_DIR, exist_ok=True)
            # ⭐ 2026-09-18：动画表情**优先离线解出原图**（`agent/emoticon.py`，AES-128-CBC 解本机
            #   表情文件）——老实现只会 UIA 截图（存下来是"聊天区截图"，发出去是图片还会抢鼠标）。
            #   拿不到 key / 文件不在本地才回退截图（诚实降级，不静默）。
            if lt == 47:
                try:
                    from . import emoticon as _emo
                    _p = _emo.sticker_image(self._db, chat_id, int(local_id))
                    if _p and os.path.exists(_p):
                        log.info("收藏表情：离线解出原图 %s", os.path.basename(_p))
                        return _p
                except Exception as _e:
                    log.info("收藏表情：离线解密不可用（%s）⇒ 回退截图", str(_e)[:60])
            if lt == 3:
                # 图片：原图下载
                if self._md is None:
                    return None
                path = self._md.download_image(chat_id, int(local_id), save_dir=self.EMOJI_DIR)
                if path and os.path.exists(path):
                    return path
                return None
            if lt != 47:
                return None
            chat = self._chat_obj(chat_id)
            msg = chat.GetMessageById(int(local_id))
            if msg is None:
                return None
            path = msg.capture(save_dir=self.EMOJI_DIR)
            if path and os.path.exists(path):
                return path
            return None
        except Exception:
            return None

    def list_emojis(self) -> list:
        """收藏夹表情列表：[{path, name, size}]。"""
        try:
            out = []
            if os.path.isdir(self.EMOJI_DIR):
                for f in sorted(os.listdir(self.EMOJI_DIR)):
                    p = f
                    if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                        fp = os.path.join(self.EMOJI_DIR, f)
                        out.append({"path": fp, "name": f, "size": os.path.getsize(fp)})
            return out
        except Exception:
            return []

    def send_emoji(self, chat_id: str, emoji_path: str) -> tuple:
        """从收藏夹发送表情包（转 send_image）。返回 (ok, msg)。"""
        return self.send_image(chat_id, emoji_path)

    # ── 朋友圈（模拟点击 + OCR；入口=侧栏相机图标）────────────────────────
    # 微信 PC 4.1 侧栏第 4 个图标（相机）= 朋友圈。坐标经 DPI 适配层换算，
    # 不同电脑用 ui_adapt.prepare_screen + 相对缩放推算（兜底 OCR 识别「朋友圈」文字）。

    # ── 朋友圈（PC 版有入口：侧栏第 4 图标=朋友圈，窗口标题"朋友圈"；实测确认）──
    # 操作链：点图标 → 验证「朋友圈」窗口出现 → 干活 → 关闭窗口（点右上角叉号，落败兜底）。

    def _moments_shot_ocr(self, rect, scale=2):
        """截图指定屏幕区域并 OCR → [(text, 区域内x, y, w, h)]。"""
        try:
            from PIL import ImageGrab
            from wechatauto import ScreenOCR
            img = ImageGrab.grab(rect)
            res = ScreenOCR.recognize(img)
            out = []
            for item in (res or []):
                if isinstance(item, dict):
                    t = str(item.get("text") or ""); x = int(item.get("x") or 0)
                    y = int(item.get("y") or 0); w = int(item.get("w") or 0); h = int(item.get("h") or 0)
                else:
                    t, x, y, w, h = (list(item) + [0, 0, 0, 0])[:5]
                out.append((str(t), int(x), int(y), int(w), int(h)))
            return out
        except Exception:
            return []

    def _click_screen(self, x, y):
        """真鼠标点击（屏幕坐标）。⛔ 必须先过 `ui_adapt.real_guard`：
        `mouse_event` 是全局输入，系统把它给"光标所在/最上面的窗口"——**不校验就会点到用户的控制台**
        （2026-09-16 用户报「为什么还会划我的控制台」的机制本身就是这条）。"""
        import ctypes
        from . import ui_adapt
        try:
            gui = self._get_gui()
        except Exception:
            gui = None
        ok, why = ui_adapt.real_guard(int(x), int(y), gui=gui)
        if not ok:
            log.warning("真鼠标点击已拦下（不打给别的窗口）：%s", why)
            return False
        u = ctypes.windll.user32
        u.mouse_event(0x0002, 0, 0, 0, 0)
        u.mouse_event(0x0004, 0, 0, 0, 0)
        return True

    def _find_green_discover(self, gui):
        """运行时颜色定位（不依赖标定）：侧栏绿色圆＝「发现」图标（仅选中态变绿；
        未选中为灰色，此时走图标列聚类）。返回 (屏幕x, 屏幕y)；未找到返回 None。"""
        try:
            import ctypes
            from ctypes import wintypes
            from PIL import ImageGrab
            u = ctypes.windll.user32
            r = wintypes.RECT()
            u.GetWindowRect(int(gui.main_hwnd), ctypes.byref(r))
            l, t, rt, b = r.left, r.top, r.right, r.bottom
            W, H = rt - l, b - t
            img = ImageGrab.grab((l, t, rt, b)).convert("RGB")
            px = img.load()
            pts = []
            for y in range(int(H * 0.55), int(H * 0.99), 2):
                for x in range(2, max(3, int(W * 0.10)), 2):
                    rr, gg, bb = px[x, y]
                    if gg > 110 and gg - rr > 45 and gg - bb > 45:
                        pts.append((x, y))
            if len(pts) < 12:
                return None
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            return (l + int(cx), t + int(cy))
        except Exception:
            return None

    def _detect_sidebar_icons(self, gui):
        """运行时图标列聚类（不依赖标定）：侧栏带纵向暗像素分段 → 各图标中心 y（渲染相对）。
        返回有序 y 列表（含消息/通讯录/发现/设置等；设置=最后一枚，永远跳过）。"""
        try:
            import ctypes
            from ctypes import wintypes
            from PIL import ImageGrab
            u = ctypes.windll.user32
            r = wintypes.RECT()
            u.GetWindowRect(int(gui.main_hwnd), ctypes.byref(r))
            l, t, rt, b = r.left, r.top, r.right, r.bottom
            W, H = rt - l, b - t
            img = ImageGrab.grab((l, t, rt, b)).convert("L")
            px = img.load()
            x0, x1 = 3, max(4, int(W * 0.085))
            items = []
            cur = None
            for y in range(int(H * 0.10), int(H * 0.99)):
                dark = 0
                for x in range(x0, x1, 2):
                    if px[x, y] < 190:
                        dark += 1
                if dark >= 2:
                    if cur is None:
                        cur = [y, y]
                    else:
                        cur[1] = y
                else:
                    if cur is not None:
                        items.append((cur[0] + cur[1]) // 2)
                        cur = None
            if cur is not None:
                items.append((cur[0] + cur[1]) // 2)
            # 合并过近段（图标内微小分离）
            merged = []
            for y in items:
                if merged and y - merged[-1] < int(H * 0.012):
                    merged[-1] = (merged[-1] + y) // 2
                else:
                    merged.append(y)
            return merged
        except Exception:
            return []

    # ── 朋友圈：投递档基础设施（不动光标 / 不打扰你（可能短暂置前约 1~3 秒后自动还回） / 不要求可见）────────────
    def _bg_backend(self):
        """取当前输入后端；**不是投递档就返回 (None, 原因)**。

        为什么要单独一步：真鼠标档 `touches_cursor=True`、投递档 `False`。
        任何"以为在投递、其实在动鼠标"的实现都会被这里挡住（最高目标里"不许悄悄降级"）。
        """
        from . import input_backend as _ib
        try:
            b = _ib.select_backend()
        except Exception as e:
            return None, "取输入后端失败：%s" % e
        if getattr(b, "touches_cursor", True):
            return None, "当前输入后端是真鼠标档（config.input.backend=%s）" % getattr(b, "name", "?")
        return b, ""

    def _posted_hwnd(self, gui) -> int:
        """投递目标窗：优先 `find_main_window()`（带渲染子窗的那个主窗），否则用 gui 的主窗。

        为什么不直接用 `gui.main_hwnd`：朋友圈纯文字编辑窗**同类名**，`FindWindow` 会抓到它
        ⇒ 投递全打到编辑窗上（2026-09-13 实测踩过，且不报错）。
        """
        from . import input_backend as _ib
        try:
            h = int(_ib.find_main_window() or 0)
        except Exception:
            h = 0
        return h or int(getattr(gui, "main_hwnd", 0) or 0)

    def _background_only(self) -> bool:
        """「只走后台」开关（config.wechat.background_only）：真鼠标路径一律跳过、如实说明。"""
        try:
            from .config import get_config as _gc
            return bool((_gc().get("wechat") or {}).get("background_only", False))
        except Exception:
            return False

    def _scroll_list_fallback(self) -> bool:
        """「搜索失败时扫会话列表」开关（config.wechat.scroll_list_fallback，**默认 False＝不回退**）。

        为什么默认不回退（2026-09-16 用户报「他点了一下搜索框，又不点，又搁那划会话列表」）：
        回退档虽然走投递滚轮（**不动光标**），但**会话列表会在用户眼前滚** —— 他看到的"它在划"
        就是这个动作。⇒ 默认停手并如实说明；要"成功率优先、列表动几下无所谓"的用户可以打开。
        开关缺省/读不到配置 ⇒ False（安全的一侧）。
        """
        try:
            from .config import get_config as _gc
            return bool((_gc().get("wechat") or {}).get("scroll_list_fallback", False))
        except Exception:
            return False

    def _moments_gray_thumb(self, rect, scale=(64, 48), gui=None):
        """缩略灰度：**不依赖 OCR** 的第二判据（点前点后比"界面真变了"）。

        取图优先走 `chat_header.grab_render(gui)`（`PrintWindow(PW_RENDERFULLCONTENT)`）——
        微信被别的窗口**遮挡**时也拿得到它自己的画面；`PrintWindow` 拿不到（例如被最小化）
        才退回抓屏，此时抓到的可能是别人的窗口 ⇒ 判据只能当"没变"，**如实报判不了**，
        绝不因此假报"投递成功"。
        """
        try:
            from PIL import Image
            img = None
            if gui is not None:
                try:
                    from . import chat_header as _ch
                    img = _ch.grab_render(gui)
                except Exception:
                    img = None
            if img is None:
                from PIL import ImageGrab
                l, t, r, b = [int(v) for v in rect]
                img = ImageGrab.grab((l, t, r, b))
            return list(img.convert("L").resize(scale).getdata())
        except Exception:
            return None

    @staticmethod
    def _gray_diff(before, now) -> float:
        """两张缩略灰度的差异占比（0~1）。阈值**不再写死**——见 `_gray_thresholds`。"""
        try:
            if not before or not now or len(before) != len(now):
                return 0.0
            return sum(1 for a, b2 in zip(before, now) if abs(a - b2) > 28) / max(1, len(before))
        except Exception:
            return 0.0

    _GRAY_NOISE_MAX = 0.08

    _UNSTABLE_MSG = ("判据不稳：**一枪都没投**的情况下连拍三张，画面自己就差了 %.3f"
                     "（抓图本身在抖）⇒ 这时候「画面变了」证明不了点中了，按「判不了就不动手」处理。"
                     "把微信窗口留在屏幕上（不必在前台，别被完全盖住）通常就好。")

    def _gray_noise_floor(self, rect, gui=None, shots=3) -> float:
        """**零动作对照**：不点任何东西，连拍 `shots` 张，取相邻两张最大差异 ⇒ 判据噪声地板。

        为什么必须有它：老代码把阈值写死成 0.15 / 0.01，而**抓图本身会抖**——实测
        `chat_header.capture_image()` 零动作连拍两张差 **0.142**，远超 0.01 ⇒ 那条"界面变了"
        完全可能是噪声骗出来的（这条判据自己错过一次）。钉死 `grab_render`（PrintWindow）后
        零动作差值是 0.000，但退回抓屏时又会抖 ⇒ 阈值只能现场量。
        """
        try:
            n = max(2, int(shots))
            seq = [self._moments_gray_thumb(rect, gui=gui) for _ in range(n)]
            return max(self._gray_diff(seq[i], seq[i + 1]) for i in range(len(seq) - 1))
        except Exception:
            return 1.0     # 量不出来 ⇒ 按"自检不可用"处理（调用方会拒绝动手）

    def _gray_thresholds(self, rect, gui=None):
        """把绝对阈值换成本地化阈值 ⇒ `(强阈值, 弱阈值, 地板)`；地板太高时前两个为 None。

        强阈值＝"发现页变了"（老 0.15，避免小抖动误判成切页），弱阈值＝"信息流动了"（老 0.01）。
        地板过高 ⇒ 返回 None：**判据不可用时不动手**，而不是拿噪声当"界面变了"。
        """
        noise = self._gray_noise_floor(rect, gui=gui)
        if noise >= self._GRAY_NOISE_MAX:
            return None, None, noise
        thr = max(0.01, noise * 3.0 + 0.01)
        return max(0.15, thr), thr, noise

    _BLIND_JUDGE_MSG = ("微信当前被最小化：投递点击本身也许仍会生效，但**判据抓不到画面**"
                        "（无法确认点没点中、内容有没有动）⇒ 按「判不了就不动手」处理。"
                        "最好别最小化微信窗口——把它留在屏幕上就行（不必在前台、被别的窗口盖住也可以）。")

    def _moments_judge_blind(self, hwnd) -> bool:
        """判据是否"瞎"：窗口被最小化时抓不到画面 ⇒ 朋友圈这类"靠画面判成功"的路径只能如实拒绝。

        为什么不硬点：点了也可能"其实成了"，而判据一律判失败——那会让用户看到"没点中"的假象，
        更糟的是它可能真的动了界面而我们不知道。诚实报"判不了"是唯一站得住的做法。
        （2026-09-14 实测：本机微信最小化时 `IsIconic=True`、rect=(-32000,-32000)。）
        """
        try:
            import ctypes
            return bool(ctypes.windll.user32.IsIconic(int(hwnd)))
        except Exception:
            return False

    def _moments_rect(self, hwnd):
        """窗口屏幕矩形 (l, t, r, b)。"""
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(int(hwnd), ctypes.byref(r))
        return (r.left, r.top, r.right, r.bottom)

    def moments_open_posted(self) -> tuple:
        """**投递档**打开朋友圈：侧栏「发现」→「朋友圈」两步都用 WM_* 投递。

        判据与真鼠标路径同一套（发现页 OCR / 主窗缩略灰度差），所以"投递没生效"会被判成
        "没点中"并**停手**，不会继续试、也不会假报成功。
        """
        try:
            from . import ui_adapt
            gui = self._get_gui()
            b, why = self._bg_backend()
            if not b:
                return False, why
            hwnd = self._posted_hwnd(gui)
            if not hwnd:
                return False, "找不到微信主窗句柄（投递档需要它）"
            if not ui_adapt.prepare_screen(gui):
                return False, "屏幕预检失败"
            if self._moments_judge_blind(hwnd):
                return False, self._BLIND_JUDGE_MSG
            l, t, r, bt = self._moments_rect(hwnd)
            W, H = r - l, bt - t
            if W < 300 or H < 300:
                return False, "微信主窗过小"

            def _discover_visible():
                try:
                    for txt, _x, _y, _w, _h in self._moments_shot_ocr((l, t, r, bt)):
                        if "搜一搜" in txt or "小程序" in txt or "游戏" in txt:
                            return True
                except Exception:
                    pass
                return False

            pos = self._find_green_discover(gui)
            if not pos:
                return False, ("没有自证到的「发现」图标（投递档只点自证的：请手动点一下左侧"
                               "「发现」，选中后图标变绿，程序就认得它）")
            thr_strong, thr_step, noise = self._gray_thresholds((l, t, r, bt), gui=gui)
            if thr_strong is None:
                return False, self._UNSTABLE_MSG % noise
            before = self._moments_gray_thumb((l, t, r, bt), gui=gui)
            ok1, m1 = b.click(hwnd, (int(pos[0]), int(pos[1])))
            if not ok1:
                return False, "投递打开「发现」失败：%s" % m1
            time.sleep(1.2)
            if not _discover_visible() and self._gray_diff(before, self._moments_gray_thumb((l, t, r, bt), gui=gui)) <= thr_strong:
                return False, ("投递点了「发现」但界面没变（投递档没生效，已停手、不再乱点；"
                               "判据地板 %.3f）" % noise)
            # 点「朋友圈」：OCR 定位左侧列表项，未识别按发现页首项相对位置兜底（与真鼠标路径同一处）
            tgt = None
            try:
                for txt, x, y, w, h in self._moments_shot_ocr((l, t, r, bt)):
                    if "朋友圈" in txt and x < W * 0.55:
                        tgt = (x, y, w, h)
                        break
            except Exception:
                tgt = None
            if tgt is None:
                tgt = (int(W * 0.22), int(H * 0.112), int(W * 0.10), int(H * 0.03))
            pt = (l + tgt[0] + tgt[2] // 2, t + tgt[1] + tgt[3] // 2)
            before2 = self._moments_gray_thumb((l, t, r, bt), gui=gui)
            ok2, m2 = b.click(hwnd, pt)
            if not ok2:
                return False, "投递点「朋友圈」失败：%s" % m2
            time.sleep(1.5)
            if self._gray_diff(before2, self._moments_gray_thumb((l, t, r, bt), gui=gui)) <= thr_step:
                return False, "投递点了「朋友圈」但界面没变（投递档没生效，已停手；判据地板 %.3f）" % noise
            return True, "朋友圈已打开（投递档：全程未动光标、未改前台）"
        except Exception as e:
            return False, str(e)

    def moments_scroll_posted(self, direction: int = 1, times: int = 1) -> tuple:
        """**投递档**刷朋友圈：投递 `WM_MOUSEWHEEL`（多格 + 间隔，像人滚）。

        判据：滚完主窗缩略灰度必须变（实测下滚 6 格差值 0.389、反向精确回位 0.000）——
        不变量就当"没生效"如实返回，而不是假报滚过了。
        """
        try:
            from . import wechat_ui as _wu
            gui = self._get_gui()
            if _wu.stop_requested():
                return False, "已停止"
            b, why = self._bg_backend()
            if not b:
                return False, why
            hwnd = self._posted_hwnd(gui)
            if not hwnd:
                return False, "找不到微信主窗句柄（投递档需要它）"
            if self._moments_judge_blind(hwnd):
                return False, self._BLIND_JUDGE_MSG
            l, t, r, bt = self._moments_rect(hwnd)
            # 落点：右缘内侧一条细带（避开头像/蓝点/输入框），与真鼠标路径取同一处
            pt = (r - 60, (t + bt) // 2)
            _thr_strong, thr_step, noise = self._gray_thresholds((l, t, r, bt), gui=gui)
            if thr_step is None:
                return False, self._UNSTABLE_MSG % noise
            before = self._moments_gray_thumb((l, t, r, bt), gui=gui)
            delta = -120 if direction > 0 else 120
            n = max(1, int(times)) * 8
            ok, msg = b.wheel(hwnd, pt, delta=delta, times=n, gap_ms=60)
            if not ok:
                return False, "投递滚轮失败：%s" % msg
            time.sleep(0.5)
            diff = self._gray_diff(before, self._moments_gray_thumb((l, t, r, bt), gui=gui))
            if diff <= thr_step:
                return False, ("投递滚轮后朋友圈界面没有变化（投了 %d 格、差值 %.3f、判据地板 %.3f）"
                               "⇒ 按没生效处理，不假报已滚动" % (n, diff, noise))
            return True, "已滚动朋友圈（投递档，%d 格，界面变化 %.3f，未动光标/未改前台）" % (n, diff)
        except Exception as e:
            return False, str(e)

    def _moments_open_discover(self, gui):
        """新 UI（4.1 发现页）路径：置顶微信 → 点「发现」：
        ① 绿圆颜色定位（已选中时）→ ② 侧栏图标列聚类（从后往前试，最后一枚=设置必跳过）
        → ③ 相对多候选兜底；每点均验证发现页出现（OCR「搜一搜/小程序/游戏」）。
        然后 OCR 定位「朋友圈」点击（未识别按发现页首项相对位置兜底）。"""
        try:
            import ctypes
            from ctypes import wintypes
            from . import ui_adapt
            u = ctypes.windll.user32
            if not ui_adapt.prepare_screen(gui):
                return False, "屏幕预检失败"
            r = wintypes.RECT()
            u.GetWindowRect(int(gui.main_hwnd), ctypes.byref(r))
            l, t, rt, b = r.left, r.top, r.right, r.bottom
            W, H = rt - l, b - t
            if W < 300 or H < 300:
                return False, "微信主窗过小"

            def _ocr_find(text, x_max=None):
                items = self._moments_shot_ocr((l, t, rt, b))
                for txt, x, y, w, h in items:
                    if text in txt and (x_max is None or x < x_max):
                        return (x, y, w, h)
                return None

            def _discover_visible():
                return bool(_ocr_find("搜一搜") or _ocr_find("小程序") or _ocr_find("游戏"))

            def _shot_small():
                """主窗缩略灰度（64×48）——OCR 之外的第二判据用。"""
                return self._moments_gray_thumb((l, t, rt, b), gui=gui)

            def _content_changed(before):
                """点击前后主窗中部是否明显变了（发现页会整块替换掉聊天区）。
                为什么要它（2026-09-14 用户实测反馈）：原来只认 OCR「搜一搜/小程序/游戏」，
                点对了但 OCR 认不出来时会被判成"没点中" ⇒ 继续到处乱点（用户看到的就是
                "它在乱点我的头像、联系人、收藏，唯独没点发现"）。多一条不依赖 OCR 的判据，
                点对的那一下就认得出来，也就不会继续试。"""
                return self._gray_diff(before, _shot_small()) > 0.15

            def _try_click(px, py):
                b0 = _shot_small()
                self._click_screen(px, py)
                time.sleep(1.2)
                if _discover_visible():
                    return True
                if _content_changed(b0):
                    # 视图确实换了，但 OCR 没读出发现页文案 —— 按"点中了"收手，不再继续试
                    return True
                return False

            got_discover = False
            # 「乱点」闸门（2026-09-14 用户实测反馈后立）：
            #   已知现象：「我看我点了那个程序鼠标测试，我点了朋友圈，它在乱点我的头像、联系人、收藏，
            #             但是唯独没有点朋友圈里的『发现』」
            #   根因＝②③两段是**盲试**（按图标列/比例猜位置，猜一个就真点一下）。默认关掉盲试：
            #   只走"自证得到的发现"（绿点＝已选中态）。盲试要用必须显式打开
            #   `wechat.allow_click_hunting`（界面里也有开关），否则宁可不点也不乱点。
            _hunt = False
            try:
                from .config import get_config as _gc
                _hunt = bool((_gc().get("wechat") or {}).get("allow_click_hunting", False))
            except Exception:
                _hunt = False
            # ① 绿圆（发现已选中/打开过）——自证，任何时候都允许
            pos = self._find_green_discover(gui)
            if pos and not _discover_visible():
                got_discover = _try_click(pos[0], pos[1])
            elif pos and _discover_visible():
                got_discover = True
            # ② 图标列聚类：**排除最后一枚（设置）**，从倒数第二（发现）开始往前试【盲试，默认关】
            if not got_discover and _hunt:
                ys = self._detect_sidebar_icons(gui)
                cands = list(reversed(ys[-3:-1])) if len(ys) >= 2 else []
                for yi in cands:
                    if got_discover:
                        break
                    got_discover = _try_click(l + int(W * 0.043), t + yi)
            # ③ 相对多候选兜底（只在下半部中上区域，不接近设置/三条杠）【盲试，默认关】
            if not got_discover and _hunt:
                for y_ratio in (0.70, 0.76, 0.82):
                    if _try_click(l + int(W * 0.043), t + int(H * y_ratio)):
                        got_discover = True
                        break
            if not got_discover:
                if not _hunt:
                    return False, ("没有自证到的「发现」图标，已按「不乱点」设置停止盲试："
                                   "请手动点一下左侧「发现」（选中后图标变绿，程序就能认得它），"
                                   "或在控制台「微信」面板打开「允许盲试点击」")
                return False, "发现页未出现（点侧栏「发现」图标失败）"
            # 点「朋友圈」：OCR 优先（左侧列表区），未识别按首项相对位置兜底
            tgt = _ocr_find("朋友圈", x_max=W * 0.55)
            if tgt is None:
                tgt = (int(W * 0.22), int(H * 0.112), int(W * 0.10), int(H * 0.03))
            self._click_screen(l + tgt[0] + tgt[2] // 2, t + tgt[1] + tgt[3] // 2)
            time.sleep(1.5)
            return True, "已点击「发现 → 朋友圈」"
        except Exception as e:
            return False, str(e)

    def moments_open(self) -> tuple:
        """打开朋友圈：**投递档优先**（不动光标（伪激活可能短暂置前约 1~3 秒后自动还回）、不要求可见）。

        投递档失败才退回真鼠标路径，并在返回消息里写明"投递失败后走了真鼠标档"——
        最高目标里那句"不许悄悄降级"，落点就是这里。
        """
        try:
            from . import input_backend as _ib
            if not _ib.select_backend().touches_cursor:
                ok, msg = self.moments_open_posted()
                if ok:
                    return True, msg
                log.warning("朋友圈投递打开失败（%s）；按口径改用真鼠标路径", msg)
                if self._background_only():
                    return False, "%s（「只走后台」已开启 ⇒ 不退回真鼠标档，本次跳过）" % msg
                ok2, msg2 = self._moments_open_real()
                if ok2:
                    return True, "%s（投递档失败后退回**真鼠标档**：%s）" % (msg2, msg)
                return False, "%s；退回真鼠标档也失败：%s" % (msg, msg2)
        except Exception as e:
            log.warning("朋友圈投递档入口异常（%s）；按口径改用真鼠标路径", e)
        if self._background_only():
            return False, "「只走后台」已开启：打开朋友圈目前需要真鼠标档，本次跳过"
        return self._moments_open_real()

    def _moments_open_real(self) -> tuple:
        """（真鼠标档）打开朋友圈（UI 自适应）：① 新 UI（发现→朋友圈，OCR 定位文字）
        ② 旧 UI（侧栏相机图标）兜底。验证两种形态：独立「朋友圈」子窗口（弹窗）/
        微信主窗内嵌（右侧内容区 OCR 识别「朋友圈」标题，位置过滤防误判发现页）。"""
        try:
            from . import wechat_ui
            gui = self._get_gui()
            ok, msg = self._moments_open_discover(gui)
            if not ok:
                ok, msg = wechat_ui.hit("sidebar.moments", gui, retries=3)
                if not ok:
                    return False, "朋友圈打开失败：%s" % msg
            # 验证：弹窗（子窗口）或内嵌（主窗右侧 OCR「朋友圈」标题）
            for attempt in range(3):
                deadline = time.time() + 3.0
                while time.time() < deadline:
                    try:
                        for hwnd, title, rect in wechat_ui._wechat_subwindows(gui.main_hwnd):
                            if "朋友圈" in title:
                                return True, "朋友圈已打开（独立窗口）"
                    except Exception:
                        pass
                    try:
                        import ctypes
                        from ctypes import wintypes
                        u = ctypes.windll.user32
                        r = wintypes.RECT()
                        u.GetWindowRect(int(gui.main_hwnd), ctypes.byref(r))
                        W = r.right - r.left
                        items = self._moments_shot_ocr((r.left, r.top, r.right, r.bottom))
                        for txt, x, y, w, h in items:
                            # 内嵌标题在右侧内容区（x > 30% 宽），发现页列表项在左侧不算
                            if "朋友圈" in txt and x > W * 0.30 and y < (r.bottom - r.top) * 0.10:
                                return True, "朋友圈已打开（微信内嵌）"
                    except Exception:
                        pass
                    time.sleep(0.5)
                if attempt < 2:
                    wechat_ui.close_leftover_windows(gui)
                    time.sleep(0.5)
                    ok2, msg2 = self._moments_open_discover(gui)
                    if not ok2:
                        wechat_ui.hit("sidebar.moments", gui, retries=2)
            return True, "已点击朋友圈（窗口待加载）"
        except Exception as e:
            return False, str(e)

    def moments_close(self) -> tuple:
        """关闭朋友圈等残留子窗口（点右上角叉号；兜底 Alt+F4/WM_CLOSE）。"""
        try:
            from . import wechat_ui
            gui = self._get_gui()
            closed = wechat_ui.close_leftover_windows(gui)
            return True, "已关闭残留子窗口%d个：%s" % (len(closed), "、".join(closed) or "无")
        except Exception as e:
            return False, str(e)

    # ── 朋友圈完整鼠标操作（UI 实测：相机长按发表纯文字 / 蓝点赞评论 / 滚动刷）──

    def _moments_focus(self):
        """确保朋友圈窗口在前台（枚举到即激活）—— **激活要过闸**（2026-09-18 加）。

        ⛔ 为什么补这道闸（作者口径：「会不会有本来可以全后台的代码，结果由于某些疏忽，
          在某一环把窗口带到前台」）：这个函数原来**无条件** `SetForegroundWindow`。它的调用方里，
          点赞/评论/发表/滚动都在前面过了"只走后台"的闸，但 **`moments_screenshot` 与 `moments_ocr`
          没有** ⇒ 全程后台模式下，模型想看一眼朋友圈就会把微信顶到最前。
        ⇒ 闸收到**唯一真源** `ui_adapt.fg_allowed()`：不允许时**只枚举、不激活**
          （截图/OCR 拿窗口自身画面就够，本来也不需要前台）。
        """
        allow, why = False, "闸门不可用"
        try:
            from . import ui_adapt
            allow, why = ui_adapt.fg_allowed()
        except Exception as e:                                   # noqa: BLE001
            allow, why = False, "读闸门失败（按拒绝）：%s" % str(e)[:40]
        from . import wechat_ui
        gui = self._get_gui()
        for hwnd, title, rect in wechat_ui._wechat_subwindows(gui.main_hwnd):
            if "朋友圈" in title or title == "Weixin":
                if allow:
                    import ctypes
                    ctypes.windll.user32.SetForegroundWindow(int(hwnd))
                else:
                    log.info("朋友圈窗口找到了但**不激活**（%s）—— 截图/OCR 不需要前台", str(why)[:60])
                return rect
        return None

    def moments_scroll(self, direction: int = 1, times: int = 1) -> tuple:
        """滚动朋友圈：**投递档优先**（不动光标（伪激活可能短暂置前约 1~3 秒后自动还回））；失败才退回真鼠标档并写明。"""
        try:
            from . import input_backend as _ib
            if not _ib.select_backend().touches_cursor:
                ok, msg = self.moments_scroll_posted(direction=direction, times=times)
                if ok:
                    return True, msg
                log.warning("朋友圈投递滚动失败（%s）；按口径改用真鼠标路径", msg)
                if self._background_only():
                    return False, "%s（「只走后台」已开启 ⇒ 不退回真鼠标档，本次跳过）" % msg
                ok2, msg2 = self._moments_scroll_real(direction=direction, times=times)
                if ok2:
                    return True, "%s（投递档失败后退回**真鼠标档**：%s）" % (msg2, msg)
                return False, "%s；退回真鼠标档也失败：%s" % (msg, msg2)
        except Exception as e:
            log.warning("朋友圈投递滚动入口异常（%s）；按口径改用真鼠标路径", e)
        if self._background_only():
            return False, "「只走后台」已开启：刷朋友圈目前需要真鼠标档，本次跳过"
        return self._moments_scroll_real(direction=direction, times=times)

    def _moments_scroll_real(self, direction: int = 1, times: int = 1) -> tuple:
        """（真鼠标档）滚动朋友圈：先置前台焦点 → 光标移到窗口内（避开图片/按钮，用右侧空白带）
        → 每次 -120 步进（微信滚轮标准刻度）×N 次×times，滚后验证窗口仍在前台。"""
        try:
            import ctypes
            gui = self._get_gui()
            from . import wechat_ui as _wu
            if _wu.stop_requested():
                return False, "已停止"
            rect = self._moments_focus()
            if not rect:
                return False, "朋友圈窗口未打开"
            h = int(self._moments_hwnd() or gui.main_hwnd)
            ctypes.windll.user32.SetForegroundWindow(h)
            time.sleep(0.5)
            user32 = ctypes.windll.user32
            # 滚动带：窗口右缘内侧一条细带（避开头像/蓝点/输入框）
            cx = rect[2] - 30
            cy = (rect[1] + rect[3]) // 2
            # ⛔ 滚轮也是全局输入：光标没到位就会滚到用户别的窗口（控制台）上 ⇒ 先过闸
            from . import ui_adapt as _ua_g
            _gok, _gwhy = _ua_g.real_guard(cx, cy, gui=gui, extra_hwnds=(h,))
            if not _gok:
                return False, "滚动朋友圈已拦下（不动鼠标、不打扰你）：%s" % _gwhy
            time.sleep(0.25)
            step = -120 if direction > 0 else 120
            total = 0
            for _ in range(times):
                for _ in range(8):
                    if _wu.stop_requested():
                        return True, "已停止（已滚动 %d 格）" % total
                    user32.mouse_event(0x0800, 0, 0, step, 0)
                    time.sleep(0.12)
                    total += 1
                time.sleep(0.4)
            return True, "已滚动朋友圈（%d×%d 格）" % (times, total)
        except Exception as e:
            return False, str(e)

    def moments_screenshot(self) -> list:
        """截图朋友圈当前视口（刷时给模型看；省 token：最多 2 屏）。"""
        try:
            gui = self._get_gui()
            rect = self._moments_focus()
            if not rect:
                rect = (gui.origin_x, gui.origin_y,
                        gui.origin_x + gui.render_w, gui.origin_y + gui.render_h)
            img = gui._grab_screen(rect)
            from PIL import Image
            import io, base64
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=62)
            return [{"type": "image_url",
                     "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}}]
        except Exception:
            return []

    # ── 朋友圈交互（实测布局：打开=第一条动态详情；蓝点·(0.89W,0.938H)→弹「赞|评论」菜单）──

    def _moments_bluedot(self, gui, rect):
        """详情页蓝点位置（相对窗口比例；不同窗口大小自适应）。"""
        return int(rect[0] + (rect[2] - rect[0]) * 0.89), int(rect[1] + (rect[3] - rect[1]) * 0.938)

    def _moments_menu_item(self, gui, rect, label):
        """OCR 找菜单里「赞/评论/发送」文字（窗口内坐标→屏幕→ui_adapt 点击）。"""
        from . import wechat_ui
        hit = self._moments_menu_item2(gui, rect, label)
        if not hit:
            return False
        from . import ui_adapt
        t2, x, y, ww, hh = hit
        sx, sy = rect[0] + x + ww // 2, rect[1] + y + hh // 2
        ok, _ = ui_adapt.click(gui, sx - gui.origin_x, sy - gui.origin_y,
                               extra_hwnds=tuple(int(h) for h, t, r in wechat_ui._wechat_subwindows(gui.main_hwnd)))
        return ok

    def _moments_menu_item2(self, gui, rect, label):
        """只 OCR 查找菜单项，返回 (text,x,y,w,h) 或 None（不点击，防止取消菜单）。"""
        for t2, x, y, ww, hh in self.moments_ocr():
            if label in t2.strip():
                return (t2, x, y, ww, hh)
        return None

    def _moments_hover_menu(self, gui, rect, label):
        """蓝点操作：滚回顶 → **单击**蓝点（菜单持久出现，不能双击）→ OCR 找菜单项并点击。"""
        import ctypes
        from . import ui_adapt, wechat_ui as _wu
        # 滚回顶（朋友圈会记住上次视口；不滚回顶蓝点坐标错位）
        # ⛔ 滚回顶与点蓝点都是**全局输入**（SetCursorPos + mouse_event）⇒ 一律先过 `real_guard`：
        #    否则光标没到位时，滚轮/点击会落到用户当前真正指着的窗口（他的控制台）上。
        _mh = int(self._moments_hwnd() or 0)
        try:
            cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
            _ok1, _why1 = ui_adapt.real_guard(cx, cy, gui=gui,
                                              extra_hwnds=tuple(x for x in (_mh,) if x))
            if not _ok1:
                return False
            for _ in range(8):
                ctypes.windll.user32.mouse_event(0x0800, 0, 0, 900, 0)
                time.sleep(0.12)
            time.sleep(0.8)
        except Exception:
            pass
        bx, by = int(rect[0] + (rect[2] - rect[0]) * 0.89), int(rect[1] + (rect[3] - rect[1]) * 0.938)
        subs = tuple(int(h) for h, t, r in _wu._wechat_subwindows(gui.main_hwnd))
        # 单击蓝点（heal=False：不抖动；绝不再点第二下，否则菜单取消）
        _ok2, _why2 = ui_adapt.real_guard(bx, by, gui=gui,
                                          extra_hwnds=tuple(subs) + tuple(x for x in (_mh,) if x))
        if not _ok2:
            return False
        time.sleep(0.25)
        ui_adapt.click(gui, bx - gui.origin_x, by - gui.origin_y, extra_hwnds=subs, heal=False)
        for _ in range(3):
            if _wu.stop_requested():
                return "STOPPED"
            time.sleep(0.9)
            hit = self._moments_menu_item2(gui, rect, label)
            if hit:
                t2, x, y, ww, hh = hit
                sx, sy = rect[0] + x + ww // 2, rect[1] + y + hh // 2
                ok, _ = ui_adapt.click(gui, sx - gui.origin_x, sy - gui.origin_y,
                                       extra_hwnds=subs, heal=False)
                return ok
        return False

    def moments_like(self, index: int = 0) -> tuple:
        """点赞（检验版：蓝点悬停出「赞」菜单即视为可点赞，**不实际点击赞**；true 点赞走行为引擎）。"""
        # 后台能力矩阵：点赞/评论走右键菜单＝真鼠标档。开了「只走后台」就跳过。
        if self._background_only():
            from . import bg_status as _bg
            return False, _bg.background_only_reason("朋友圈点赞")
        try:
            from . import ui_adapt
            gui = self._get_gui()
            ui_adapt.prepare_screen(gui)
            ok_open, msg_open = self.moments_open()
            if not ok_open:
                return False, msg_open
            time.sleep(1.2)
            rect = self._moments_focus() or gui.render_rect
            if not rect:
                self.moments_close()
                return False, "朋友圈窗口未找到（未点赞）"
            if not self._moments_hover_menu(gui, rect, "赞"):
                self.moments_close()
                return False, "蓝点悬停后未出「赞」菜单（可手动把朋友圈窗口置前再试）"
            time.sleep(0.5)
            self.moments_close()
            return True, "已到「可点赞菜单」（检验未实际点赞），窗口已关"
        except Exception as e:
            return False, str(e)

    def _moments_hwnd(self):
        from . import wechat_ui
        gui = self._get_gui()
        for hwnd, title, rect in wechat_ui._wechat_subwindows(gui.main_hwnd):
            if "朋友圈" in title or title == "Weixin":
                return hwnd
        return None

    def _type_into_focused(self, text: str) -> bool:
        """把文本打进**当前聚焦窗口**（朋友圈等独立窗）：剪贴板 + Ctrl+V + 回车。
        不要用 gui.input_text（那是主窗输入框！）。"""
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            k = ctypes.windll.user32
            # 剪贴板 UTF-8
            data = text.encode("utf-16-le")
            hglob = ctypes.windll.kernel32.GlobalAlloc(0x0042, len(data) + 2)
            p = ctypes.windll.kernel32.GlobalLock(hglob)
            ctypes.memmove(p, data, len(data))
            ctypes.windll.kernel32.GlobalUnlock(hglob)
            u.OpenClipboard(0)
            u.EmptyClipboard()
            u.SetClipboardData(13, hglob)   # CF_UNICODETEXT
            u.CloseClipboard()
            time.sleep(0.2)
            # Ctrl+V
            u.keybd_event(0x11, 0, 0, 0)      # CTRL down
            u.keybd_event(0x56, 0, 0, 0)      # V
            u.keybd_event(0x56, 0, 0x0002, 0)
            u.keybd_event(0x11, 0, 0x0002, 0)
            time.sleep(0.3)
            return True
        except Exception:
            return False

    def moments_comment(self, index: int = 0, text: str = "", dry: bool = False) -> tuple:
        """评论：蓝点 →「评论」→ 输入框（朋友圈窗口内）→ 粘贴 → 关窗。
        dry=True：只验证到「输入框可输入」即关窗，**不点发送**（用于检验，避免真发评论打扰）。"""
        # 后台能力矩阵：评论走右键菜单＝真鼠标档。开了「只走后台」就跳过。
        if self._background_only():
            from . import bg_status as _bg
            return False, _bg.background_only_reason("朋友圈评论")
        try:
            from . import ui_adapt
            gui = self._get_gui()
            if not text.strip():
                return False, "评论内容不能为空"
            ui_adapt.prepare_screen(gui)
            ok_open, msg_open = self.moments_open()
            if not ok_open:
                return False, msg_open
            time.sleep(1.2)
            rect = self._moments_focus() or gui.render_rect
            if not rect:
                self.moments_close()
                return False, "朋友圈窗口未找到（未评论）"
            if not self._moments_hover_menu(gui, rect, "评论"):
                self.moments_close()
                return False, "蓝点后未出「评论」菜单（可手动把朋友圈窗口置前再试）"
            time.sleep(1.4)
            # 输入评论：点一下输入框（确保光标在内）→ 剪贴板粘贴（重试 3 次）
            import ctypes
            ctypes.windll.user32.SetForegroundWindow(int(self._moments_hwnd() or gui.main_hwnd))
            time.sleep(0.5)
            try:
                inx, iny = rect[0] + int((rect[2] - rect[0]) * 0.5), rect[1] + int((rect[3] - rect[1]) * 0.965)
                ui_adapt.click(gui, inx - gui.origin_x, iny - gui.origin_y, extra_hwnds=subs, heal=False)
                time.sleep(0.6)
            except Exception:
                pass
            ok_in = False
            for _ in range(3):
                if self._type_into_focused(text):
                    ok_in = True
                    break
                time.sleep(0.4)
            if not ok_in:
                return False, "评论输入失败（输入框已打开，请手动输入后发送；窗口保留）"
            time.sleep(0.6)
            if dry:
                self.moments_close()
                return True, "已到「评论输入」可输入（未实际发送，dry 模式）"
            # 发送：OCR「发送」（窗口内）→ 点击；找不到则回车
            sent = self._moments_menu_item(gui, rect, "发送")
            if not sent:
                try:
                    gui._input.key(0x0D)
                except Exception:
                    pass
            time.sleep(1.2)
            self.moments_close()
            return True, "已评论（%s…），窗口已关" % text[:10]
        except Exception as e:
            return False, str(e)


    def moments_publish_text(self, text: str, dry: bool = False, shots: bool = False) -> tuple:
        """长按左上角相机 ~2 秒 → 纯文字输入栏 → 输入 → 点「发表」（变绿后）→ 关窗。
        dry=True：验证到「输入栏可输入」即止（不点发表、不回车），用于检验避免真的发朋友圈。
        shots=True：dry 模式下每步截图到 logs/dry_shots/moments_*.png（逐屏存证，UI 检验用）。"""
        # 后台能力矩阵：草稿能投递，但**「发表」那一下没有投递取证** ⇒ 不冒充后台：
        # 「只走后台」时直接跳过（诚实说跳过，而不是悄悄用真鼠标替你发出去）。
        if self._background_only():
            from . import bg_status as _bg
            return False, _bg.background_only_reason("发朋友圈（纯文字）")
        _shot = None
        if shots:
            import os as _os
            _sdir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "logs", "dry_shots")
            _os.makedirs(_sdir, exist_ok=True)
            def _shot(name):
                try:
                    from PIL import ImageGrab
                    rect = self._moments_focus() or self._get_gui().render_rect
                    ImageGrab.grab((rect[0], rect[1], rect[2], rect[3])).save(_os.path.join(_sdir, name))
                except Exception:
                    pass
        try:
            import ctypes
            from . import ui_adapt
            gui = self._get_gui()
            ui_adapt.prepare_screen(gui)
            ok_open, msg_open = self.moments_open()
            if not ok_open:
                return False, msg_open
            time.sleep(1.0)
            if _shot: _shot("moments_1_open.png")
            rect = self._moments_focus() or gui.render_rect
            if not rect:
                self.moments_close()
                return False, "朋友圈窗口未找到（未发布）"
            # ⛔ 这条链全是真鼠标（长按相机 / 点输入区 / ESC）⇒ 每枪前都过 `real_guard`。
            #    朋友圈可能是独立窗 ⇒ 把它一并算作"我们的窗口"，否则会被误判成遮挡。
            _mhx = tuple(x for x in (int(self._moments_hwnd() or 0),) if x)
            # 相机位置：朋友圈窗口左上角图标排（🔔 铃铛 | 📷 相机 | 🔄 刷新）。
            # 实测弹窗(682×979)：铃铛≈(57,38)、相机≈(105,38)、刷新≈(153,38)。
            # 多形态自适应：弹窗=左上角；内嵌（主窗右侧内容区）=顶部右侧工具栏；多次位置候选 + OCR 确认输入区出现。
            _w, _h = max(1, int(rect[2] - rect[0])), max(1, int(rect[3] - rect[1]))
            user32 = ctypes.windll.user32
            _cam_candidates = [(0.154, 0.039), (0.86, 0.045), (0.72, 0.04), (0.60, 0.04)]
            _cam_hit = False
            for _px, _py in _cam_candidates:
                cam_x, cam_y = int(rect[0] + _w * _px), int(rect[1] + _h * _py)
                _cok, _cwhy = ui_adapt.real_guard(cam_x, cam_y, gui=gui, extra_hwnds=_mhx)
                if not _cok:
                    log.warning("长按相机已拦下（不动鼠标）：%s", _cwhy)
                    continue
                user32.mouse_event(0x0002, 0, 0, 0, 0)
                time.sleep(2.0)
                user32.mouse_event(0x0004, 0, 0, 0, 0)
                time.sleep(1.2)
                if _shot:
                    _shot("moments_2_cam_popup.png")
                # OCR 确认"这一刻的想法"输入区出现（长按相机成功的标志）
                try:
                    _items = self._moments_shot_ocr((int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])))
                    if any(("这一刻" in (t or "")) or ("想法" in (t or "")) for t, *_x in _items):
                        _cam_hit = True
                        break
                except Exception:
                    pass
                # 未命中：悬停校验无输入区 → 恢复指针到内容区中部并按 ESC 关闭误触弹层
                try:
                    _mok, _mwhy = ui_adapt.real_guard(int(rect[0] + _w * 0.5), int(rect[1] + _h * 0.5),
                                                      gui=gui, extra_hwnds=_mhx)
                    # ESC 发给"当前前台窗口"：前台不是微信就不发（否则会把用户正用着的窗口切走/关掉）
                    _fg = int(user32.GetForegroundWindow() or 0)
                    _ours = tuple(x for x in (int(gui.main_hwnd or 0), int(gui.render_hwnd or 0)) + _mhx if x)
                    if _mok and _fg in _ours:
                        user32.keybd_event(0x1B, 0, 0, 0)  # ESC
                        user32.keybd_event(0x1B, 0, 2, 0)
                    time.sleep(0.8)
                except Exception:
                    pass
            if not _cam_hit:
                self.moments_close()
                return False, "未定位到朋友圈相机（弹窗/内嵌均未命中），未发布"
            # 纯文字输入栏出现：「这一刻的想法…」输入区是弹窗内的独立输入框——
            # 不能走 gui.input_text（它探测主窗口输入框，会把文字打到聊天栏）。
            # 点进弹窗输入区（实测弹窗内输入区约：x 0.147w~0.821w，y 0.215h~0.337h）
            # → 剪贴板 + Ctrl+A/Ctrl+V 直贴。
            _iw, _ih = max(1, int(rect[2] - rect[0])), max(1, int(rect[3] - rect[1]))
            ix0, iy0 = int(rect[0] + _iw * 0.147), int(rect[1] + _ih * 0.215)
            ix1, iy1 = int(rect[0] + _iw * 0.821), int(rect[1] + _ih * 0.337)
            _cx, _cy = (ix0 + ix1) // 2, iy0 + int((iy1 - iy0) * 0.30)
            _iok, _iwhy = ui_adapt.real_guard(_cx, _cy, gui=gui, extra_hwnds=_mhx)
            if not _iok:
                self.moments_close()
                return False, "点朋友圈输入区被拦下（不动鼠标、未发布）：%s" % _iwhy
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.8)
            try:
                gui.set_clipboard(text)          # pyperclip 剪贴板
            except Exception:
                import pyperclip
                pyperclip.copy(text)
            gui._input.key(0x41, ctrl=True)      # Ctrl+A 清空占位
            time.sleep(0.15)
            gui._input.key(0x56, ctrl=True)      # Ctrl+V 粘贴
            time.sleep(0.8)
            # 验证：输入区是否有深色文字（占位符是浅灰 ~(200,200,200)，正文字体近黑）
            def _popup_has_text():
                try:
                    from PIL import ImageGrab as _IG2
                    img = _IG2.grab((ix0, iy0, ix1, iy1)).convert("L")
                    dark = sum(1 for p in img.getdata() if p < 120)
                    return dark >= 30
                except Exception:
                    return False
            if not _popup_has_text():
                # 兜底：走 input_text（可能探测到弹窗输入框的某些版本）
                if not gui.input_text(text, fast=False):
                    self.moments_close()
                    return False, "朋友圈输入栏未找到或输入未生效（未发布，窗口已关）"
            time.sleep(0.6)
            if _shot: _shot("moments_3_typed.png")
            if dry:
                if _shot: _shot("moments_4_dry_end.png")
                self.moments_close()
                return True, "已到朋友圈输入框并输入文字（dry 模式，未点发表，未真发）"
            # 点「发表」（变绿后）：OCR 找「发表」；找不到再按回车兜底前先找
            published = False
            for t2, x, y, ww, hh in self.moments_ocr():
                if t2.strip() == "发表":
                    ui_adapt.click(gui, x + ww // 2, y + hh // 2)
                    published = True
                    break
            if not published:
                try:
                    gui._input.key(0x0D)  # 回车兜底
                    published = True
                except Exception:
                    pass
            time.sleep(1.5)
            self.moments_close()
            return True, "朋友圈已发布（%s…），窗口已关" % text[:12] if published else ("发布未确认（%s…），窗口已关" % text[:12])
        except Exception as e:
            return False, str(e)


    def moments_ocr(self) -> list:
        """对**朋友圈窗口**（独立子窗）截图 OCR——返回 [(text, 窗口内x, y, w, h)]。"""
        try:
            rect = self._moments_focus()
            if not rect:
                return []
            from PIL import ImageGrab
            img = ImageGrab.grab((rect[0], rect[1], rect[2], rect[3]))
            from wechatauto import ScreenOCR
            res = ScreenOCR.recognize(img)
            out = []
            for item in (res or []):
                # ScreenOCR 返回结构兼容处理：tuple(text,x,y,w,h) 或 dict
                if isinstance(item, dict):
                    t = str(item.get("text") or ""); x = int(item.get("x") or 0)
                    y = int(item.get("y") or 0); w = int(item.get("w") or 0); h = int(item.get("h") or 0)
                else:
                    t, x, y, w, h = (list(item) + [0, 0, 0, 0])[:5]
                out.append((t, int(x), int(y), int(w), int(h)))
            return out
        except Exception:
            return []


    def parse_forward_card(self, chat_id: str, local_id: int) -> dict:
        """解析「文件/链接/卡片」里的合并转发/多条目内容。
        返回 {kind, title, items:[{title, desc}], raw}；非合转返回 {kind:"other"}。"""
        try:
            row = self._db.get_message_row(chat_id, int(local_id))
            if not row:
                return {"kind": "other"}
            content = row.get("content")
            if not isinstance(content, bytes) or not content.startswith(b"\x28\xb5\x2f\xfd"):
                return {"kind": "other"}
            import zstandard
            txt = zstandard.ZstdDecompressor().decompress(content, max_output_size=400000).decode("utf-8", "ignore")
            # 类型与多条目特征
            t_m = re.search(r"<type>(\d+)</type>", txt)
            t = t_m.group(1) if t_m else ""
            titles = [re.sub(r"<[^>]+>", "", x).strip() for x in re.findall(r"<title>(.*?)</title>", txt, re.S)]
            titles = [x for x in titles if x]
            # 合并转发的子消息块：<record>、<msgchunk>、多个 <appmsg>；含 "聊天记录" 等特征
            is_merge = (t == "57" and len(titles) >= 2) or ("record" in txt.lower()) or ("多条" in txt) or ("聊天记录" in txt)
            if is_merge:
                items = []
                for i, tl in enumerate(titles[:12]):
                    items.append({"title": tl, "desc": ""})
                return {"kind": "merge", "title": titles[0] if titles else "合并转发",
                        "items": items, "raw": txt[:500]}
            return {"kind": "card", "title": titles[0] if titles else "", "raw": txt[:300]}
        except Exception:
            return {"kind": "other"}

    # ── 右键菜单操作（拍一拍 / 引用）──────────────────────────────────

    def _ensure_foreground(self, gui) -> bool:
        """把微信窗口带到前台并清理一切挡点击的东西（系统叠加层/遮挡窗口）。

        ⛔ 2026-09-18 加闸（fail-closed）：**这是"会置顶"的路，只允许真鼠标档走**。
        投递链一处都不需要前台，而置顶会压过用户用来遮挡的窗口（作者当场发火那次就是这个）。
        闸不放行 ⇒ 直接返回 False（调用方按"做不到"处理），**绝不悄悄置顶**。

        不用 gui.ensure_visible()：它的"桌面可用"检测数白色像素占比，
        深色主题下永远返回 False（实测误报"锁屏/不可见"）。
        用 agent.ui_adapt：DPI 感知、TabTip 手写画布等系统叠加层、普通遮挡窗，
        各种电脑（不同缩放/多显示器）都能保持一致。
        """
        from . import ui_adapt
        _fg_ok, _fg_why = ui_adapt.fg_allowed()
        if not _fg_ok:
            log.info("拒绝置前（%s）—— 只有真鼠标档才需要前台，投递档不动窗口", _fg_why)
            return False
        try:
            return ui_adapt.prepare_screen(gui)
        except Exception:
            try:
                gui._minimize_blockers()
                time.sleep(0.5)
                gui.bring_to_front(keep_topmost=True)
                time.sleep(0.5)
                gui._update_render_rect()
                return gui.is_alive()
            except Exception:
                return False

    def _prepare_for_capture(self, gui) -> bool:
        """按当前档位准备画面（2026-09-16）：

        · 要动真鼠标（`_real_mouse_allowed()`）⇒ `_ensure_foreground`：**置前 + 清遮挡**（真点击必须）；
        · 只走投递 ⇒ 只做**不激活**的最小化还原（`_ensure_main_visible`）⇒ **全程只做无激活还原，不主动把微信拉到前台**。

        为什么要分开：这些路（拍一拍/引用/点赞评论）以前一进门就 `_ensure_foreground`（＝把微信
        怼到前台），在"全程后台"口径下是多余的打扰；而投递链只需要窗口**能抓画面**，不需要在前台。
        """
        if self._real_mouse_allowed():
            return self._ensure_foreground(gui)
        try:
            main = int(getattr(gui, "main_hwnd", 0) or 0)
            if main:
                self._ensure_main_visible(gui, main)
        except Exception:
            pass
        try:
            gui._update_render_rect()
        except Exception:
            pass
        return True

    def _click(self, gui, rel_x: int, rel_y: int, right: bool = False) -> tuple:
        """统一点击入口：换 DPI 空间 + 校验点击点属于微信 + wx_click。

        返回 (ok, 消息)。
        """
        try:
            from . import ui_adapt
            return ui_adapt.click(gui, int(rel_x), int(rel_y), right=right)
        except Exception as e:
            return False, str(e)

    def _input_has_content(self, gui, render_rect) -> tuple:
        """输入框里有没有内容：判据＝「发送」按钮的颜色（**空框＝灰、有内容＝绿**）。

        为什么要它（2026-09-18）：发图链在"粘贴"之后要打几枪提交 —— 只有**图确实进了输入框**
        才该打；否则那几枪会把空消息、或者把框里原有的文字发出去（比"没发出去"更糟）。
        为什么用颜色而不是 OCR：按钮位置固定（渲染区比例 0.932, 0.945，就是我们要点的那个点），
        颜色判据零依赖、零误读；而且**必须用屏幕实拍**（`ImageGrab`）——`capture_image()` 在微信
        不重绘时返回的是**缓存帧**（实测连续多次截图 sha256 完全相同），拿它当判据会一直看到旧画面。

        返回 `(True/False, 说明)`；**量不出来时按 True 放行**（这只是个前置自检，最终仍以 DB 回读为准）。
        """
        try:
            from PIL import ImageGrab
            r = render_rect or (0, 0, 0, 0)
            rw, rh = int(r[2] - r[0]), int(r[3] - r[1])
            if rw <= 0 or rh <= 0:
                return True, "渲染区未知 ⇒ 跳过颜色自检"
            cx = int(r[0] + rw * 0.932)
            cy = int(r[1] + rh * 0.945)
            img = ImageGrab.grab(bbox=(cx - 7, cy - 5, cx + 7, cy + 5), all_screens=True).convert("RGB")
            px = list(img.getdata())
            n = float(len(px) or 1)
            green = sum(1 for (rr, gg, bb) in px if gg > rr + 25 and gg > bb + 25) / n
            mean = tuple(int(sum(p[i] for p in px) / n) for i in range(3))
            if green >= 0.35:
                return True, "发送按钮=绿色（输入框有内容）绿色占比=%.2f" % green
            return False, "发送按钮=灰色（输入框看着是空的）绿色占比=%.2f 均值=%s" % (green, mean)
        except Exception as e:
            return True, "颜色自检不可用（%s）⇒ 按有内容继续" % str(e)[:40]

    def _right_click_menu_posted(self, gui, rel_x: int, rel_y: int, label: str, delay: float = 0.7) -> bool:
        """**投递版**右键菜单：投递右键（投主窗）→ 差分找菜单窗 → 投递点击含 label 的项。

        2026-09-16 实测（`_scratch/rclick_avatar.py` + `_scratch/rck_menu.py`，两靶点各有真实右键阳性对照）：
          · 投递右键**投渲染子窗不弹菜单**、**投主窗才弹**（`Qt51514QWindowToolSaveBits`）；
          · 菜单弹出后，**投递左键点菜单项能命中** —— 判据＝剪贴板被写成那条消息的正文（点「复制」那一枪）。
        ⇒ 这条路**全程不动光标（微信可能被短暂置前约 1~3 秒后自动还回）**，所以拍一拍/引用/点赞评论不再是"只能真鼠标"。

        返回 True ＝**已经点到了那个菜单项**；False ＝这条链没成（调用方按两个开关决定是否回真鼠标）。
        """
        try:
            from . import input_backend as ib
            backend = ib.select_backend(gui=gui)
            if not isinstance(backend, ib.MessageBackend):
                return False                      # 当前不是投递档 ⇒ 交给原实现
            # ⭐ 2026-09-18 实测（作者口径「右键不行，就是没点准」之后我做的对照实验）：
            #   **伪激活会让这条链不弹菜单**——同一落点（头像方块中心）、同一帧：
            #     `activate=True`  ⇒ 新菜单窗 **0 个**
            #     `activate=False` ⇒ 菜单窗出现，且截出来就是「拍一拍」（`_scratch/menu-shot/`）
            #   而伪激活（`WM_ACTIVATE/WM_NCACTIVATE`）的用处只是"让目标自认为被激活"，
            #   投递根本不需要它，它反而**会把微信拉前台**（跨机 r15 实测 0.6~1.8s）。
            #   ⇒ 后台档一律关掉伪激活；真鼠标档保持原样。
            try:
                from . import ui_adapt as _ua_fg
                _fg_ok = bool(_ua_fg.fg_allowed()[0])
            except Exception:
                _fg_ok = False
            if not _fg_ok and getattr(backend, "activate", False):
                backend.activate = False
                log.info("投递右键：后台档**关掉伪激活**（实测带伪激活反而不弹菜单，且它是置前的来源）")
            main = int(getattr(gui, "main_hwnd", 0) or 0) or ib.find_main_window()
            if not main:
                return False
            import win32process
            pid = win32process.GetWindowThreadProcessId(int(main))[1]
            before = ib.menu_new_windows(pid, ())          # 传空快照 ⇒ 得到"当前所有菜单类窗"当基线
            screen_pt = (int(getattr(gui, "origin_x", 0)) + int(rel_x),
                         int(getattr(gui, "origin_y", 0)) + int(rel_y))
            ok, why = backend.click(main, screen_pt, right=True)
            if not ok:
                log.info("投递右键没发出去：%s", why)
                return False
            time.sleep(max(0.4, float(delay)))
            menus = ib.menu_new_windows(pid, before)
            if not menus:
                log.info("投递右键之后没出现菜单窗（%s）", label)
                return False
            ok2, why2 = ib.menu_click(menus[0], label)
            log.info("投递右键菜单：%s ｜ %s", why2, why)
            return bool(ok2)
        except Exception as e:
            log.info("投递右键菜单异常（交给原实现）：%s", e)
            return False

    def _real_mouse_allowed(self) -> bool:
        """现在允许回真鼠标吗？（**口径唯一事实源＝`ui_adapt.fg_allowed()`**）

        2026-09-18 改：原来这里自己读一遍配置、`ui_adapt` 那边再读一遍，两处口径可能分叉
        （置前/置顶就吃了这个亏）⇒ 统一问 `fg_allowed()`：两开关都默认安全，任一不满足
        或读配置失败 ⇒ 一律 False（如实拒绝，不悄悄降级）。
        """
        try:
            from . import ui_adapt as _ua
            return bool(_ua.fg_allowed()[0])
        except Exception:
            return False

    def _right_click_menu(self, gui, rel_x: int, rel_y: int, label: str, delay: float = 0.7) -> bool:
        """在相对坐标 (rel_x, rel_y) 处右键，OCR 弹出菜单，点含 label 的项。

        2026-09-16 起**先走投递**（`_right_click_menu_posted`：不动光标（可能短暂置前约 1~3 秒后自动还回）），
        投递这条链不成立时才按两个开关决定是否回落到下面的真鼠标实现。

        真鼠标实现的原档（保留）：优先 UIA 菜单树（微信 4.x 右键菜单热激活后物化为
        mmui::XMenuView，用 Invoke 点击最可靠、无坐标漂移）；OCR 兜底并做「真菜单」过滤：
        菜单项是小字条（高 < 46）、位于光标右下方附近——防止把聊天文本里
        的「拍一拍」误当成菜单项。
        """
        if self._right_click_menu_posted(gui, rel_x, rel_y, label, delay):
            return True
        if not self._real_mouse_allowed():
            log.info("投递右键菜单不成立，且未允许真鼠标兜底 ⇒ 不动光标（%s）", label)
            return False
        ok, why = self._click(gui, rel_x, rel_y, right=True)
        if not ok:
            return False
        time.sleep(delay)
        # 1) UIA 菜单树优先（第一次右键可能只完成窗口聚焦而不弹菜单 → 重试一次）
        for _attempt in range(2):
            try:
                uia = gui._get_uia()
                if uia is not None:
                    mi = uia._uia_find_menu_item(label)
                    if mi is not None:
                        if uia._uia_click_menu_item(mi):
                            return True
            except Exception:
                pass
            if _attempt == 0:
                ok, why = self._click(gui, rel_x, rel_y, right=True)
                if not ok:
                    return False
                time.sleep(delay)
        # 2) OCR 兜底（放大 3 倍），带真菜单过滤
        top = max(0, rel_y - 220)
        bottom = min(gui.render_h, rel_y + 320)
        items = None
        try:
            items = gui.ocr_zoomed((gui.right_pane_left, top, gui.render_w, bottom), scale=3)
        except Exception:
            try:
                items = gui.ocr((gui.right_pane_left, top, gui.render_w, bottom))
            except Exception:
                return False
        for text, x, y, w, h in items:
            if label and label in text:
                # 真菜单过滤：菜单是贴光标右下方的紧凑小字条（高 < 46），
                # 距离限制在光标附近 ±320px，防止把聊天文本里的「拍一拍」误当菜单项
                if not (y > rel_y - 30 and rel_x - 120 < x < rel_x + 320 and h < 46):
                    continue
                ok2, _ = self._click(gui, x + w // 2, y + h // 2, right=False)
                if not ok2:
                    return False
                return True
        return False

    def _latest_friend(self, chat_id: str) -> tuple:
        """从微信数据库找该群最近一条「非机器人」消息的 (名字, wxid)。

        不依赖控制台存档——任何群只要有群友说过话即可（诊断选群用）。
        """
        try:
            for raw in self._db.get_messages(chat_id, limit=60):
                norm = self.normalize(raw, chat_id)
                if not norm:
                    continue
                sid = str(norm.get("sender_id") or "")
                if sid.startswith("wxid_"):
                    return (str(norm.get("sender_name") or sid), sid)
        except Exception:
            pass
        return ("", "")

    def _last_target_text(self, chat_id: str, wxid: str) -> str:
        """从数据库找目标**最近**一条消息的文本（用于 UIA/OCR 定位）。

        注意 wechatauto.get_messages 是 ORDER BY sort_seq DESC（最新在前），
        按序取第一条匹配即最新；曾误用 reversed() 取到最旧——已修。
        """
        try:
            raws = self._db.get_messages(chat_id, limit=60)
            for raw in raws:  # 最新在前，第一条匹配即最新
                norm = self.normalize(raw, chat_id)
                if norm and str(norm.get("sender_id") or "") == str(wxid):
                    txt = str(norm.get("text") or "").strip()
                    if txt and not txt.startswith("["):
                        return txt
        except Exception:
            pass
        return ""

    def _target_recent_texts(self, chat_id: str, wxid: str, n: int = 8) -> list:
        """目标**最近 n 条**消息文本（最新在前）——认人用的锚点。

        为什么不能只认"最后一条"（2026-09-18 实测）：作者拍机器人那轮，E 的最后一条是
        `。。。`（纯标点，OCR 根本读不出），于是按单条锚点永远对不上、明明人在视口里也拍不上。
        而 E 上一条 `@群deepseek 说话！` 就在视口里、OCR 读得出 ⇒ 拿**一组**锚点去配，
        命中任意一条即可锁定"这是 TA 的消息行"（仍然是"文本真的对上"，不是猜）。
        """
        out = []
        try:
            for raw in self._db.get_messages(chat_id, limit=60):
                norm = self.normalize(raw, chat_id)
                if not norm or str(norm.get("sender_id") or "") != str(wxid):
                    continue
                txt = str(norm.get("text") or "").strip()
                if txt and not txt.startswith("[") and txt not in out:
                    out.append(txt)
                if len(out) >= max(1, int(n)):
                    break
        except Exception:
            pass
        return out

    @staticmethod
    def _norm_ocr(s: str) -> str:
        """OCR 行 vs 数据库文本的归一化：去空白，@/# 与"群"互换等 OCR 常见误读。"""
        s = re.sub(r"[\s\u00a0]+", "", str(s or ""))
        s = s.replace("#", "群").replace("＃", "群")
        s = s.replace("@", "").replace("@", "")
        return s

    def _uia_target_row_rect(self, gui, db_text: str, scroll: bool = False):
        """用 UIA 消息列表匹配目标最近一条消息的行矩形（屏幕坐标）。

        微信 4.x 的消息列表在 UIA 树里是 chat_message_list（mmui::RecyclerListView），
        每行 mmui::ChatTextItemView 的 Name 就是消息原文（可能被截断）——
        先精确匹配，再按前 24 字做相似度匹配（防截断/OCR 噪声）。
        scroll=True 且可视区没有时，会用滚轮向上翻页查找（最多 12 屏），
        解决「消息多、目标消息滚出可见区」的情况。
        返回 (left, top, right, bottom) 或 None。
        """
        try:
            uia = gui._get_uia()
            if uia is None:
                return None
            target = self._norm_ocr(db_text)
            if not target:
                return None

            def _match(nm: str) -> bool:
                nm = self._norm_ocr(nm)
                return bool(nm) and (nm == target or _seq_ratio(nm[:24], target[:24]) > 0.7)

            # 1) 可视区先找：精确命中立即返回；模糊命中阈值 0.7（防止把相似旧消息当目标）
            try:
                lst = uia._message_list()
                if lst is not None:
                    best = None
                    best_score = 0.0
                    for ch in list(lst.GetChildren()):
                        try:
                            if ch.ClassName != "mmui::ChatTextItemView":
                                continue
                            nm = self._norm_ocr(ch.Name or "")
                        except Exception:
                            continue
                        if nm == target:
                            best = ch
                            break
                        sc = _seq_ratio(nm[:24], target[:24]) if nm else 0.0
                        if sc > 0.7 and sc > best_score:
                            best_score = sc
                            best = ch
                    if best is not None:
                        r = best.BoundingRectangle
                        return (r.left, r.top, r.right, r.bottom)
            except Exception:
                pass
            if not scroll:
                return None
            # 2) 滚轮向上翻页查找（最多 20 屏），找到后把目标行滚到视野中部再返回
            res = uia.find_in_message_list(
                lambda cn, nm: cn == "mmui::ChatTextItemView" and _match(nm),
                match_last=False, max_scrolls=20)
            if res:
                r = res[2]
                try:
                    lst = uia._message_list()
                    lr = lst.BoundingRectangle
                    for _ in range(3):  # 最多再滚 3 次让该行摆脱窗口边缘
                        cur = None
                        for ch_ in list(lst.GetChildren()):
                            try:
                                if ch_.ClassName != "mmui::ChatTextItemView":
                                    continue
                                nm = self._norm_ocr(ch_.Name or "")
                            except Exception:
                                continue
                            if nm and (nm == target or _seq_ratio(nm[:24], target[:24]) > 0.5):
                                cur = ch_
                                break
                        if cur is None:
                            break
                        rr = cur.BoundingRectangle
                        if rr.top > lr.top + 60 and rr.bottom < lr.bottom - 60:
                            return (rr.left, rr.top, rr.right, rr.bottom)
                        cx = (lr.left + lr.right) // 2
                        cy = (lr.top + lr.bottom) // 2
                        uia._set_cursor(cx, cy)
                        # 行偏上（目标在顶部边缘）→ 向「历史」滚（+120），让行下移到视野中部；
                        # 行偏下 → 向「最新」滚（-120）。注意 -120=最新（scroll_to_bottom 同向）。
                        delta = 120 if rr.top <= lr.top + 60 else -120
                        uia._mouse_wheel(delta)
                        time.sleep(0.15)
                        uia._mouse_wheel(delta)
                        time.sleep(0.25)
                except Exception:
                    pass
                return (r.left, r.top, r.right, r.bottom)
            return None
        except Exception:
            return None

    @staticmethod
    def _scroll_chat(gui, up: bool = True, ticks: int = 6) -> bool:
        """**投递**滚轮翻聊天记录（不动光标、不置前）：`up=True` 往上翻（看更早的消息）。

        为什么必须有它（2026-09-18 现场）：老代码那句 `_scroll_to_bottom(gui)` 走的是 **UIA**
        （`gui._get_uia()`），而本机微信 4.x 的 UIA 不物化 ⇒ 它**一直是空操作**，
        "定位失败先滚到最新再找一遍"这句注释描述的兜底其实从没生效过。
        而拍一拍要在**对方的消息行**上找头像，对方的消息很容易被后来的消息顶出视口
        （实测：满屏都是机器人自己的回复 ⇒「可见范围里没找到 TA 的消息行」）。
        """
        try:
            from . import input_backend as _ib
            backend = _ib.select_backend(gui=gui)
            main = int(getattr(gui, "main_hwnd", 0) or 0) or _ib.find_main_window()
            if not main:
                return False
            rw = int(getattr(gui, "render_w", 0) or 0)
            rh = int(getattr(gui, "render_h", 0) or 0)
            ox, oy = int(getattr(gui, "origin_x", 0) or 0), int(getattr(gui, "origin_y", 0) or 0)
            pl = int(getattr(gui, "right_pane_left", 0) or 0)
            pt = (ox + (pl + rw) // 2, oy + int(rh * 0.45))       # 落点在会话区中部
            ok, _why = backend.wheel(main, pt, 120 if up else -120,
                                     times=max(1, int(ticks)), gap_ms=70)
            return bool(ok)
        except Exception:
            return False

    @staticmethod
    def _scroll_to_bottom(gui):
        """把消息列表滚回最新（底部）。"""
        try:
            uia = gui._get_uia()
            if uia is None:
                return
            lst = uia._message_list()
            if lst is None:
                return
            lr = lst.BoundingRectangle
            cx = (lr.left + lr.right) // 2
            cy = (lr.top + lr.bottom) // 2
            uia._set_cursor(cx, cy)
            for _ in range(6):
                uia._mouse_wheel(-120)
                time.sleep(0.12)
        except Exception:
            pass

    @staticmethod
    def _avatar_blocks(img, pane_left: int, diff: int = 45):
        """在**渲染帧**里找头像方块，返回渲染相对 bbox 列表 [(x0,y0,x1,y1), ...]。

        为什么换掉老判据 `_find_avatar_center`（2026-09-18 现场 + 作者口径，记忆 0mu60w7k）：
          · 老判据用「彩色饱和像素中位数」（`max-min>28` 的像素取中位）。对方头像常是
            **深色低饱和**照片 —— 本机实拍 E 的头像每行只有 6~11 个饱和点，判不出来；
            代码于是回落到公式 `right_pane_left + 0.185×pane`，实测算出 **434**，
            而真头像方块是 x 360..413 ⇒ **落进气泡** ⇒ 右键弹的是消息菜单
            （实测读到 撤销/放大阅读/翻译/转发/收藏，**没有「拍一拍」**）⇒ 表现为"一直拍不上"。
          · 新判据＝**与聊天底色的差异**（整幅里出现次数最多的颜色＝聊天背景）：
            逐行取最左连续段，竖着聚成 30~76px 高、26~80px 宽的方块。
            实测：头像行 44~62 点/行、非头像行 0~9 点/行；diff 取 30/45/60/80
            **四个值结果完全一致**（54×54，中心 (386,220)/(386,419)），且不依赖明暗主题。
          · ⛔ 作者口径：**不许把任何一张图量出的坐标写死成常量**（窗口/DPI/分辨率各机不同）
            ⇒ 每次都从当前帧现量；量不到由上层如实失败（不猜、不落公式）。
        """
        try:
            from collections import Counter
            rgb = img.convert("RGB")
            px = rgb.load()
            W, H = rgb.size
            lo = max(0, min(int(pane_left or 0), max(0, W - 10)))
            cnt = Counter()
            for y in range(0, H, 4):
                for x in range(lo, W, 4):
                    cnt[px[x, y]] += 1
            if not cnt:
                return []
            bg = cnt.most_common(1)[0][0]
            # 逐行取候选段（宽度 26~80 粗筛：气泡太宽、昵称太窄，都会被滤掉）
            rows_runs = []
            for y in range(H):
                runs, cur = [], None
                for x in range(lo, W):
                    r, g, b = px[x, y]
                    if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > diff:
                        if cur is None:
                            cur = [x, x]
                        cur[1] = x
                    elif cur is not None:
                        runs.append(tuple(cur))
                        cur = None
                if cur is not None:
                    runs.append(tuple(cur))
                rows_runs.append([r for r in runs if 26 <= (r[1] - r[0] + 1) <= 80])
            # ⚠️ 竖着聚类时**不能只跟"最左那一段"**：`pane_left` 万一给的是过期值（本机实测
            #   库值 262 vs 真值 331），会话列表那条绿行会成为某些行的最左段、把头像方块**顶部切掉**
            #   （实测中心从 (386,220) 变成 (386,232)）。⇒ 改成"每行的每一段都去认领自己的簇"：
            #   x 起点相差 ≤8px 且与上一行相邻（间隔 ≤3 行）才并入同一簇。
            clusters = []                      # [x0, y0, y1, last_y, [runs]]
            for y, runs in enumerate(rows_runs):
                for r in runs:
                    hit = None
                    for c in clusters:
                        if abs(r[0] - c[0]) <= 8 and (y - c[3]) <= 3:
                            hit = c
                            break
                    if hit is None:
                        clusters.append([r[0], y, y, y, [r]])
                    else:
                        hit[2] = y
                        hit[3] = y
                        hit[4].append(r)
            out = []
            for _x0, y0, y1, _last, rs in clusters:
                if not (30 <= (y1 - y0 + 1) <= 76):
                    continue
                x0 = min(r[0] for r in rs)
                x1 = max(r[1] for r in rs)
                if not (26 <= (x1 - x0 + 1) <= 80):
                    continue
                out.append((int(x0), int(y0), int(x1), int(y1)))
            return out
        except Exception:
            return []

    # 媒体消息（气泡里没有可读文本）的**归一化占位文本**：库里这类消息的 text 就是这几个字，
    # 用它们判"要不要走几何定位"（见 `_media_bubble_locate`）。
    _MEDIA_PLACEHOLDERS = ("[表情]", "[动画表情]", "[图片]", "[视频]", "[文件]", "[链接]", "[语音]")

    def _media_bubble_locate(self, gui, chat_id: str = "", want_lid=None):
        """**几何定位**媒体消息（动画表情/图片：气泡里没有可读文本）的气泡点 → `(x, y, why)`；失败 `(None, None, 原因)`。

        为什么需要（2026-09-18 晚，收藏表情连着失败 · 作者：「看看能不能收藏成功来」）：
        `message_menu` 定位消息行靠 **OCR 文本匹配**（`_send_poke_locate(db_text=…)`），而动画表情气泡
        **根本没有文本**（库里归一化成 `[表情]`）⇒ 永远匹配不上 ⇒ 最后落到
        「右键菜单里没找到『添加到表情』」这条**假失败**（实测：`GetCursorPos` 前后一模一样 ＝ **一枪都没点**）。
        ⇒ 补一条只看几何的定位：**最下面那个头像方块**（＝最新那条消息的发送者）右边、头像那条 y 带里的
        **最宽连续块**就是媒体气泡。三条硬规矩：
        ① **先核 DB**——目标必须就是**最新那一条**且类型是媒体（想定别的消息 ⇒ 拒，避免点在别的消息上）；
        ② 量不到就如实失败（**不猜点、不落公式**）；
        ③ 只用**头像那条 y 带**，不往输入框方向延伸（那里会被当成一块"宽块"⇒ 落点掉进输入框）。
        """
        try:
            from collections import Counter
            from . import chat_header as _ch
            rows = self._db.get_messages(chat_id, limit=3) if chat_id else []
            if not rows:
                return None, None, "读不到会话消息 ⇒ 不猜点"
            newest = rows[0]
            ntype = str(newest.get("type") or "")
            nlid = str(newest.get("local_id"))
            if want_lid not in (None, "") and str(want_lid) != nlid:
                return None, None, ("目标不是最新一条（最新 local_id=%s，目标=%s）⇒ 几何定位不敢用"
                                    "（会点在别的消息上）" % (nlid, want_lid))
            if ntype not in ("动画表情", "图片"):
                return None, None, "最新一条是「%s」不是表情/图片 ⇒ 不猜点" % (ntype or "?")
            img = _ch.grab_render(gui)
            if img is None:
                return None, None, "抓不到微信画面（PrintWindow 失败）⇒ 量不到"
            rgb = img.convert("RGB")
            px = rgb.load()
            W, H = rgb.size
            pane_left = 0
            try:
                pane_left = int(_ch.detect_pane_left(img)) or 0
            except Exception:
                pane_left = 0
            if not pane_left:
                pane_left = int(getattr(gui, "right_pane_left", 0) or 0)
            blocks = self._avatar_blocks(img, pane_left)
            if not blocks:
                return None, None, "这一帧没检测到头像方块 ⇒ 量不到，不猜点"
            # ⚠️ 2026-09-18 晚（实测：`_avatar_blocks` 会**在表情图里面**也报一个 36×35 的方块，
            #   于是"最下面那个"选中的是表情内部的碎片、它右边根本没有气泡 ⇒ 判"没量到宽块"）。
            #   真头像**尺寸一致**（本机实测全是 54×54），碎片明显小 ⇒ 只保留"≥ 最大方块 0.8 倍"的那些。
            _mw = max(b[2] - b[0] + 1 for b in blocks)
            _mh = max(b[3] - b[1] + 1 for b in blocks)
            _real = [b for b in blocks
                     if (b[2] - b[0] + 1) >= 0.8 * _mw and (b[3] - b[1] + 1) >= 0.8 * _mh
                     and 0.7 <= (b[2] - b[0] + 1) / float(max(1, b[3] - b[1] + 1)) <= 1.4]
            if not _real:
                return None, None, "这一帧的头像方块尺寸不齐（最大 %dx%d）⇒ 认不出真头像，不猜点" % (_mw, _mh)
            b = max(_real, key=lambda t: t[3])                   # 最下面那个＝最新那条消息的发送者
            x_lo = max(int(pane_left), int(b[2]) + 4)
            x_hi = min(W - 6, int(getattr(gui, "render_w", 0) or W) - 6)
            y_lo, y_hi = max(0, int(b[1]) - 2), min(H - 4, int(b[3]) + 26)
            if x_hi - x_lo < 30 or y_hi - y_lo < 8:
                return None, None, "头像右边的可用区域太小（%s）⇒ 不猜点" % ((x_lo, y_lo, x_hi, y_hi),)
            cnt = Counter()
            for y in range(y_lo, y_hi, 2):
                for x in range(x_lo, x_hi, 3):
                    cnt[px[x, y]] += 1
            bg = cnt.most_common(1)[0][0] if cnt else (255, 255, 255)
            bbox = None
            for y in range(y_lo, y_hi):
                best, cur = None, None
                for x in range(x_lo, x_hi):
                    r, g, bl = px[x, y]
                    if abs(r - bg[0]) + abs(g - bg[1]) + abs(bl - bg[2]) > 45:
                        if cur is None:
                            cur = [x, x]
                        cur[1] = x
                    elif cur is not None:
                        if cur[1] - cur[0] + 1 >= 40 and (best is None or (cur[1] - cur[0]) > (best[1] - best[0])):
                            best = cur
                        cur = None
                if cur is not None and cur[1] - cur[0] + 1 >= 40 \
                        and (best is None or (cur[1] - cur[0]) > (best[1] - best[0])):
                    best = cur
                if best is None:
                    continue
                if bbox is None:
                    bbox = [best[0], y, best[1], y]
                else:
                    bbox[0] = min(bbox[0], best[0])
                    bbox[2] = max(bbox[2], best[1])
                    bbox[3] = y
            if bbox is None:
                return None, None, "头像那条 y 带里没量到 ≥40px 的宽块 ⇒ 不猜点"
            w, hh = bbox[2] - bbox[0] + 1, bbox[3] - bbox[1] + 1
            if w < 40:
                return None, None, "量到的媒体块太窄 %dx%d ⇒ 不猜点" % (w, hh)
            cx = (bbox[0] + bbox[2]) // 2
            cy = (bbox[1] + bbox[3]) // 2
            # 落点自检：这一点必须**确实不是背景**（在气泡里），否则宁可失败（防点在空白处/输入框）
            try:
                _c = px[cx, cy]
                if abs(_c[0] - bg[0]) + abs(_c[1] - bg[1]) + abs(_c[2] - bg[2]) <= 25:
                    return None, None, ("算出的落点 (%d,%d) 与背景同色（不是气泡）⇒ 不猜点" % (cx, cy))
            except Exception:
                return None, None, "落点取色失败 ⇒ 不猜点"
            return (int(cx), int(cy),
                    "几何定位：最新是「%s」(local_id=%s)，最下面真头像 %s ⇒ 媒体块 %dx%d，落点 (%d,%d)"
                    % (ntype, nlid, tuple(int(v) for v in b), w, hh, cx, cy))
        except Exception as e:                                     # noqa: BLE001
            return None, None, "媒体气泡定位异常：%s" % e

    def _send_poke_locate(self, gui, target_name: str, db_text: str, scroll: bool = True, self_side: bool = False):
        """定位目标头像（渲染相对坐标），返回 (ax, ay, score) 或 None。

        失败原因一律写进 `self._poke_locate_why`（上层要如实告诉用户，不许含糊）。

        2026-09-18 重做（现场：回拍连失败，落点 (434,423) 落进气泡弹了消息菜单）：
          ① **落点的唯一来源＝运行时检测到的头像方块**（`_avatar_blocks`；帧走
             `chat_header.grab_render`＝PrintWindow，微信被遮挡也拿得到）。取方块中心，
             返回前还要过**归属校验**（落点在方块内缩 4px 内）——算出来的点不等于点在控件上；
          ② **行号在"已经抓到的那一帧"上用 `chat_ocr.recognize` 读**（不能用 `gui.ocr`：
             它是抓屏，微信被遮挡时读到别人的像素、直接返回空 —— 现场那 3 次"定位不到"就是它）；
          ③ 再在方块里挑离该行最近的那个；最近距离 >90px 就放弃（怕拍到别人）；
          ④ 全不成立 ⇒ **None**。**绝不退回公式猜点**——作者口径：「量不到就如实失败，不猜点」。
        """
        def _fail(why):
            self._poke_locate_why = why
            return None

        self._poke_locate_why = ""
        try:
            from . import chat_header as _ch
            img = _ch.grab_render(gui)
            if img is None:
                return _fail("抓不到微信画面（PrintWindow 失败）⇒ 量不到头像，不猜点")
            iw, ih = img.size
            rw = int(getattr(gui, "render_w", 0) or 0) or iw
            # 🔴 2026-09-18（现场：连续两枪"找不到 TA 的消息行"，一查发现整屏 OCR 行的 x 全在 600+）：
            #   **pane_left 必须现量**——库值 `right_pane_left` 会过期（本机实测 262 vs 真值 331），
            #   而 `gui.detect_pane_left()` 在 WeChatGUI 上**根本不存在**（AttributeError，一直静默走库值）。
            #   ⇒ 改用**我们自己**的帧内检测 `chat_header.detect_pane_left(img)`；量不到才退回库值。
            pane_left = 0
            try:
                pane_left = int(_ch.detect_pane_left(img)) or 0
            except Exception:
                pane_left = 0
            if not pane_left:
                pane_left = int(getattr(gui, "right_pane_left", 0) or 0)
            # 左右分界＝**会话区中点**（不是整幅中点）：别人的消息在会话区左半、自己的在右半。
            #   用整幅中点（rw//2）在"会话区很宽"时会把**别人的行**判成自己的 ⇒ 过滤带空 ⇒ 定位失败。
            mid = (int(pane_left) + int(rw)) // 2 if pane_left else int(rw) // 2
            blocks = self._avatar_blocks(img, pane_left)
            if self_side:
                side = [b for b in blocks if (b[0] + b[2]) // 2 > mid]
            else:
                side = [b for b in blocks if (b[0] + b[2]) // 2 <= mid]
            if not side:
                return _fail("这一帧没检测到%s的头像方块（整帧共 %d 个）⇒ 不猜点"
                             % ("右侧（自己）" if self_side else "左侧（对方）", len(blocks)))

            got = None
            # ② **帧内 OCR**：在"已经抓到的那一帧"上找目标的消息行。
            #
            # ⛔ 2026-09-18 这里修的是**现场那 3 次「未在可见消息里定位到头像」的真凶**：
            #   老代码用 `gui.ocr`（它内部是 `self._grab_screen(...)`＝**抓屏**，见
            #   `wechatauto/guia.py:967`）——微信被别的窗口盖住时读到的是**别人的像素**，
            #   OCR 直接返回 `[]`（本机实测：同一窗口，抓屏读昵称带 4 种区域全 `[]`，
            #   而 PrintWindow 那一帧读同一行能读出 `@#deepseek说话！`）。
            #   ⇒ 一律改成 OCR `chat_header.grab_render` 拿到的那一帧（遮挡也能拿），
            #     并走项目自己的加固层 `chat_ocr.recognize`（硬超时/预算/健康，`gui.ocr` 是绕开的）。
            # 另：**昵称配对这条路不做**——本机实测单字昵称（「E」）WinRT OCR 读不出来
            #   （40px 高的昵称带 4 种取法全空），留着只会拿别的文本乱配、增加拍错人的风险。
            row_y = row_h = None
            try:
                row = self._uia_target_row_rect(
                    gui, (db_text[0] if isinstance(db_text, (list, tuple)) and db_text
                          else (db_text or "")), scroll=scroll)
            except Exception:
                row = None
            if row:
                _oy = int(getattr(gui, "origin_y", 0) or 0)       # UIA 行是**屏幕**坐标
                row_y = int(row[1]) - _oy
                row_h = max(8, int(row[3]) - int(row[1]))
            else:
                items = []
                try:
                    # ⛔ 2026-09-18 拆掉 `gui.get_input_box()`（**作者当场发火的那一跳**）：
                    #   它探针连失 6 次后会走 `calibrate_layout() → bring_to_front()`
                    #   ＝`SetWindowPos(HWND_TOPMOST)` + `SetForegroundWindow` + `SetFocus`，
                    #   把微信**顶到所有窗口之上**（用户拿浏览器盖都盖不住）。
                    #   这条链只该"读画面"——消息区下边界**直接用已抓到的那一帧算**：
                    #   多框一点输入框区域只是多几条待过滤的文本，命中判据（相似度>0.5）不受影响；
                    #   而少框会切掉消息行。⇒ 取下沿，宁多勿少。
                    bottom = max(240, ih - 6)
                    top = max(80, bottom - 720)
                    crop = (int(pane_left), int(top), int(rw), int(bottom))
                    from . import chat_ocr as _co
                    if _co.blocked():
                        return _fail("OCR 暂时不可用（%s）⇒ 量不到，不猜点" % _co.blocked())
                    for t, x, y, w, h in _co.recognize(img.crop(crop)):
                        items.append((t, crop[0] + x, crop[1] + y, w, h))
                except Exception as e:
                    return _fail("读取可见消息行失败：%s" % e)
                # ⚠️ 过筛阈值为什么是 w≥6 / h≥8（2026-09-18 实测修正）：
                #   库的 `ScreenOCR.recognize` 返回的 (w,h) 是 **`line.words[0]`（第一个词）**
                #   的框（`wechatauto/guia.py`：`r = line.words[0].bounding_rect`），**不是整行**。
                #   老代码那句 `it[3] > 30` 因此把绝大多数正常行都滤掉了
                #   （本机实测：同一帧里 5 条合法左侧行只剩 1 条）⇒ 阈值按"首个词的框"来定。
                if self_side:
                    items = [it for it in items if it[3] >= 6 and it[4] >= 8 and mid < it[1] < rw]
                else:
                    items = [it for it in items
                             if it[3] >= 6 and it[4] >= 8 and (pane_left + 60) < it[1] < mid]
                # 锚点可以是**一组**文本（`_target_recent_texts`）：命中任意一条即可
                # （只认最后一条时，最后一条若是 `。。。` 这种 OCR 读不出的，就永远配不上）。
                _raw = list(db_text) if isinstance(db_text, (list, tuple)) else (
                    [db_text] if db_text else [])
                _needles = [n for n in (self._norm_ocr(x)[:120] for x in _raw) if n]
                _bb, _bsc = None, 0.0
                for t, x, y, w, h in items:
                    tn = self._norm_ocr(t)
                    if not tn or not _needles:
                        continue
                    if any(j in tn for j in _SYS_NOTICE_JUNK):
                        continue          # 系统提示居中、**没有头像** ⇒ 绝不拿来认人
                    sc = max(_seq_ratio(tn, nd) for nd in _needles)
                    if sc > 0.5 and sc > _bsc:
                        _bsc, _bb = sc, (x, y, w, h)
                # ⛔ 2026-09-18 删掉"没对上就取最下面那条左侧文本"的兜底：
                #   那是**在猜"这条消息是谁发的"**——实测给个不存在的名字 + 对不上的文本，
                #   它照样返回一个点（会拍到别人）。作者口径：「识别器认不出的东西必须显式报
                #   认不出，不许悄悄退回猜」。⇒ 认人**只认"文本真的对上"**；对不上就如实失败。
                if _bb is None:
                    _tip = " / ".join(_raw[:3])[:36] if _raw else "(库里没取到 TA 的文本)"
                    return _fail("可见范围里没找到「%s」的消息行（TA 最近几条: %r）⇒ "
                                 "不敢猜是谁，这次不拍" % (target_name, _tip))
                row_y, row_h = int(_bb[1]), max(8, int(_bb[3]))
            # ③ 行已定 ⇒ 在头像方块里挑离该行最近的那个（必须够近，否则不敢点）
            _cy = row_y + row_h // 2
            _pick, _dist = None, 10 ** 9
            for b in side:
                d = abs((b[1] + b[3]) // 2 - _cy)
                if d < _dist:
                    _pick, _dist = b, d
            if _pick is None:
                return _fail("没有可用的头像方块")
            if _dist > 90:
                return _fail("离「%s」的消息行最近的头像方块也在 %dpx 外（>90）⇒ "
                             "不敢点（怕拍到别人）" % (target_name, _dist))
            got = ((_pick[0] + _pick[2]) // 2, (_pick[1] + _pick[3]) // 2, 0.8)

            if got is None:
                return _fail("定位不到「%s」的头像" % target_name)
            ax, ay, score = got
            # ④ 归属校验：落点必须在某个检测到的方块内缩 4px 内
            _hit = next((b for b in side
                         if b[0] + 4 <= ax <= b[2] - 4 and b[1] + 4 <= ay <= b[3] - 4), None)
            if _hit is None:
                return _fail("落点 (%d,%d) 不在任何头像方块内 ⇒ 不点" % (ax, ay))
            self._poke_block = _hit          # 供右键重试用：候选点必须仍落在这个方块内
            return int(ax), int(ay), float(score)
        except Exception as e:
            return _fail("定位异常：%s" % e)

    def send_poke(self, chat_id: str, target_name: str, target_id: str = "", dbg: list | None = None):
        """拍一拍某位成员（串行锁内执行）：右键**头像方块**（运行时检测）→ 菜单选「拍一拍」。
        返回 (ok, message)；验证失败如实返回，不假报。
        （2026-09-18：删掉"改右键气泡"那条路——微信 4.1.15.8 的**消息菜单里没有「拍一拍」**。）"""
        with self._send_lock:
            return self._send_poke_inner(chat_id, target_name, target_id, dbg)

    @staticmethod
    def _bubble_point(gui, ax: int, ay: int, db_text: str = "") -> tuple:
        """气泡点击点（渲染相对）：取「气泡中部」而非左缘，容错更高。

        标定事实（2026-09-06）：
        - y：头像中心在行内偏上，气泡中心在其下约 30~40px，必须用 OCR 文本行中心；
        - x：气泡左缘 ≈ 头像中心+70（左对齐恒定）；往气泡中带移动更安全
          （左缘有圆角/内边距，点中带内几乎必然触发右键菜单）。
        返回 (x, y)：x = 头像中心+70 再往右移 60（长气泡中部）；名字匹配不到
        文本行时回退 头像中心+70、ay+30。
        """
        _norm = WeChatAdapter._norm_ocr
        base_x = ax + 130  # 气泡中带（左缘 +60 容错）
        try:
            items = gui.ocr_zoomed((gui.right_pane_left, max(0, ay - 90),
                                    gui.render_w, min(gui.render_h, ay + 90)), scale=3)
            needle = ""
            if db_text:
                need = _norm(db_text[:12])
                if need:
                    needle = need
            best = None
            for t, x, y, w, h in items:
                tn = _norm(t or "")
                if not tn or len(tn) > 120:
                    continue
                if needle:
                    if needle in tn or tn[:12] in needle:
                        best = (x + w // 2, y + h // 2)      # 自己消息在右、对方在左，用行中心自动区分
                        break
                else:
                    yc = y + h // 2
                    if 6 < h < 60 and abs(yc - ay) < 90:
                        if best is None or abs(yc - ay) < abs(best[1] - ay):
                            best = (x + w // 2, yc)
            if best is not None:
                return best
        except Exception:
            pass
        return ax + 70, ay + 30

    def _send_poke_inner(self, chat_id: str, target_name: str, target_id: str = "", dbg: list | None = None):
        """拍一拍某位成员：右键对方头像 → 菜单选「拍一拍」。靠 UIA/OCR 定位 + 数据库验证。

        返回 (ok, message)。对方最近发过言、名字在可见消息区里才比较容易成功。
        验证失败会如实返回，不会假报成功。
        dbg 传入列表时，每一步的中间结果会追加进去（供控制台「拍一拍诊断」展示）。
        """
        _halt = _control_halt()      # 暂停/停止闸：**已开工的链也要停**（2026-09-18）
        if _halt:
            return False, _halt
        # 2026-09-16 改口径（投递右键打通后）：这几条路**先试投递**（`_right_click_menu` 内部
        # 投递优先、不动光标（可能短暂置前约 1~3 秒后自动还回）），投递不成才由 `_real_mouse_allowed()` 决定是否回真鼠标。
        # 原来这里是「只走后台 ⇒ 直接跳过」——那是右键还没打通投递时的保守做法，现在属于**误拦**。
        def _d(msg):
            if dbg is not None:
                dbg.append(msg)
        try:
            gui = self._get_gui()
            rec = gui.render_rect
            _d("1) 微信窗口：%s 可见=%s" % (
                rec, _user32_is_visible(gui.main_hwnd)))
            if not self._prepare_for_capture(gui):
                return False, "微信窗口未找到或已退出，无法操作"
            _d("2) 已清理遮挡层并把微信置前")
            if not self._open_chat_guarded(group := self.group_name(chat_id), chat_id):
                return False, "打开会话失败"
            _d("3) 已打开会话「%s」" % group)
            time.sleep(0.9)
            base_seq = self.latest_seq(chat_id)
            db_text = self._target_recent_texts(chat_id, target_id) if target_id else []
            _d("4) 目标最近消息（数据库后 60 条内匹配）：%r" % (db_text[:40] or "(未找到，用空文本)"))
            located = self._send_poke_locate(gui, target_name, db_text)
            # 🔴 2026-09-18 加（现场两次回拍失败后）：**先滚到最新再找一遍**。
            #   `_send_poke_locate` 的 OCR 路径**只在当前视口里找、且只保留左侧（对方）的行**，
            #   `items` 一空就直接 `return None`；而 `scroll` 参数只喂给 UIA 那一支（我们环境 UIA 本就不通）
            #   ⇒ 对方消息只要不在视口里（或记录被清空），就永远判"未定位到"。
            #   ⇒ 失败时**滚到最新**再找一次（最新一条通常正是刚才那条拍拍前后的消息）。
            if not located:
                try:
                    self._scroll_to_bottom(gui)
                except Exception:
                    pass
                self._scroll_chat(gui, up=False, ticks=9)     # 投递版回到底（UIA 那版是空操作）
                time.sleep(0.8)
                located = self._send_poke_locate(gui, target_name, db_text)
                if located:
                    _d("5) 第一次定位失败，**滚到最新后**再找成功")
            # 🔴 2026-09-18 加：视口里没有对方的消息行 ⇒ **往上翻页找**（别人的消息会被顶出视口）
            _turn = 0
            while not located and _turn < 2:
                _turn += 1
                _d("5c) 视口里没有「%s」的消息行 ⇒ **往上翻一页**再找（第 %d 次）" % (target_name, _turn))
                self._scroll_chat(gui, up=True, ticks=6)
                time.sleep(0.9)
                located = self._send_poke_locate(gui, target_name, db_text)
                if located:
                    _d("5d) 上翻第 %d 页后找到" % _turn)
            # ⛔ **不能**在这里滚回最新：滚回去之后上面那个落点就过期了（右键会点到别的行）。
            #   收尾统一在下面 `self._scroll_to_bottom(gui)` 那一步做（右键完再滚）。
            if not located:
                _why = getattr(self, "_poke_locate_why", "") or "未找到「%s」的头像位置" % target_name
                _d("5) ✘ 定位失败：%s" % _why)
                return False, "定位不到「%s」的头像：%s" % (target_name, _why)
            ax, ay, score = located
            # 🔴 2026-09-18 加闸（用户现场原话：「**他好像是点了会话列表，但不是点的我的头像，因为我看到他
            #   右键出来什么"置顶"之类的东西**」）：**落点必须落在聊天面板里**。
            #   左边那条（`x < right_pane_left`）是**会话列表**——在那个区域右键弹出的是会话行菜单
            #   （置顶 / 标为未读 / 删除），等于对"会话"动手，而不是拍人。⇒ 越界就**直接失败、绝不右键**。
            _rpl = int(getattr(gui, "right_pane_left", 0) or 0)
            _rw = int(getattr(gui, "render_w", 0) or 0)
            if _rpl and (ax < _rpl + 4 or (_rw and ax > _rw - 4)):
                _d("5b) ✘ 落点越界：头像点 (%d,%d) 不在聊天面板内（right_pane_left=%d, render_w=%d）⇒ 不右键"
                   % (ax, ay, _rpl, _rw))
                return False, ("定位到的落点 (%d,%d) 在聊天面板之外（很可能是会话列表）⇒ **不右键**、"
                               "这次不拍（防对会话列表动手）" % (ax, ay))
            # 定位方式只用来说明"这个点是怎么来的"（落点本身已被 `_send_poke_locate` 的归属校验锁死）
            path = {0.9: "昵称配头像方块", 0.8: "消息行配最近的头像方块"}.get(
                round(float(score), 1), "头像方块")
            _d("5) 头像位置：渲染坐标 (%d,%d)，定位方式：%s" % (ax, ay, path))
            _bbox = getattr(self, "_poke_block", None)
            # 🔴 2026-09-18 加（现场：`投递右键之后没出现菜单窗（拍一拍）` ⇒ 一次落空就放弃）
            #   **在候选点上重试**：自绘头像中心可能差十几个像素。候选点＝主点 → 方块内上下微移；
            #   ⛔ 候选点**必须仍落在同一个头像方块内**（以前这里有一条 `right_pane_left+0.185×pane`
            #   的公式候选，实测就是那个 (434,423)：点在气泡上、弹消息菜单，必然失败 ⇒ 已删）。
            #   每一枪都要求"菜单窗真的出现且含目标项"，任何一枪成立即停（`_right_click_menu` 内部已校验）。
            def _poke_menu_with_retry(primary, bbox=None):
                _px, _py = int(primary[0]), int(primary[1])
                _cands = [(_px, _py), (_px, _py - 12), (_px, _py + 12)]
                if bbox:
                    _cands = [(x, y) for (x, y) in _cands
                              if bbox[0] + 4 <= x <= bbox[2] - 4 and bbox[1] + 4 <= y <= bbox[3] - 4]
                    if not _cands:                       # 方块太小 ⇒ 只用中心，绝不外扩
                        _cands = [((bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2)]
                _seen = []
                for _c in _cands:
                    if _rpl and (_c[0] < _rpl + 4 or (_rw and _c[0] > _rw - 4)):
                        continue
                    _seen.append(_c)
                    _hit = self._right_click_menu(gui, int(_c[0]), int(_c[1]), "拍一拍")
                    if _hit:
                        if _c != primary:
                            _d("6b) 主落点没弹出菜单 ⇒ 候选点 %s 命中" % (_c,))
                        return True
                _d("6c) 候选点都试过了（%s），右键菜单始终没出现 ⇒ 如实说没拍上" % (_seen,))
                return False

            # ⛔ 2026-09-18 删掉"改右键气泡"这条路：本机实测（微信 4.1.15.8，`data/runtime.log`）
            #   消息右键菜单＝撤销/放大阅读/翻译/转发/收藏，**没有「拍一拍」**（只有**头像菜单**里有）
            #   ⇒ 那条路是**死的**：只会在屏幕上多点一次右键、留下一个菜单，不可能拍上。
            _d("6) 移动到 (%d,%d) 并右键…（头像方块 %s 内；光标位置与命中窗口将在成功/失败时回读）"
               % (gui.origin_x + ax, gui.origin_y + ay, _bbox or "?"))
            menu_hit = _poke_menu_with_retry((ax, ay), _bbox)
            _d("   光标最终位置：%s（右键后）" % (_cursor_pos(),))
            # 翻过页的话把聊天滚回最新（不影响用户看到的位置）；UIA 那版是空操作，所以用投递版
            self._scroll_to_bottom(gui)
            self._scroll_chat(gui, up=False, ticks=9)
            if menu_hit:
                _d("7) ✔ 右键菜单里找到了「拍一拍」并已点击")
                ok, msg = self._verify_poke(chat_id, target_name, base_seq)
                _d("8) 验证结果：%s" % msg)
                return ok, msg
            # 说明为什么没找到（把菜单区域 OCR 抓回来，提示可读性）
            try:
                items = gui.ocr((gui.right_pane_left, max(0, ay - 220),
                                 gui.render_w, min(gui.render_h, ay + 320)))
                texts = [t for t, *_ in items if t][:10]
            except Exception:
                texts = []
            _d("7) ✘ 右键没有出现「拍一拍」菜单（弹窗区域 OCR：%s）" % (" / ".join(texts) or "无内容"))
            return False, "右键菜单里没找到「拍一拍」（头像点 (%d,%d) 可能没点中）" % (ax, ay)
        except Exception as e:
            _d("✘ 异常：%s" % e)
            return False, str(e)

    # ── 拍一拍概率门控（回拍 90% / 主动皮一下低频）────────────────────────

    @staticmethod
    def _poke_cfg():
        return get_config().get("poke") or {}

    def try_send_poke_back(self, chat_id: str, target_name: str, target_id: str = ""):
        """「对方拍了拍我」→ 回拍：概率（默认 90%）+ 每人 30 分钟冷却。

        系统级回拍（不依赖模型自觉），返回 (ok, msg)；被冷却/概率拦下时如实返回
        （ok=False），日志可查，绝不假报拍到了。
        """
        cfg = self._poke_cfg()
        prob = float(cfg.get("reply_probability", 0.9))
        cooldown = float(cfg.get("cooldown_seconds", 1800))
        if target_id and target_id == self._self_wxid:
            return False, "不能拍自己"
        if target_id:
            last = self._poke_back_cd.get(target_id, 0)
            if time.time() - last < cooldown:
                return False, "30 分钟内已经拍过 TA（冷却中），这次不拍了"
        if random.random() > prob:
            return False, "回拍概率未触发（当前 %.0f%%），这次不回拍" % (prob * 100)
        ok, msg = self.send_poke(chat_id, target_name, target_id)
        if ok and target_id:
            self._poke_back_cd[target_id] = time.time()
        return ok, msg

    def try_send_poke_active(self, chat_id: str, target_name: str, target_id: str = ""):
        """主动/皮一下拍人：低频门控（默认 10% 概率 + 每天最多 3 次）。

        群友明确要求（request）不走这里；只有模型「偶尔皮一下」才经过此门控。
        """
        cfg = self._poke_cfg()
        prob = float(cfg.get("active_probability", 0.1))
        daily = int(cfg.get("active_daily_limit", 3))
        today = time.strftime("%Y-%m-%d")
        recs = self._poke_playful.setdefault(today, [])
        if len(recs) >= daily:
            return False, "今天主动拍一拍次数已用完（%d 次），不拍了" % daily
        if random.random() > prob:
            return False, "这次皮一下被概率拦下了（主动拍一拍概率 %.0f%%），不拍了" % (prob * 100)
        ok, msg = self.send_poke(chat_id, target_name, target_id)
        if ok:
            recs.append(time.time())
        return ok, msg

    def poke_diag(self, chat_id: str, target_name: str, target_id: str = "",
                  verify_only: bool = False) -> dict:
        """控制台「拍一拍诊断」：跑一遍完整流程并返回分步结果。

        verify_only=True：仅验证「右键头像能弹出拍一拍菜单」，不点击、不实际拍——
        （防止识别偏差误拍其他群友）。
        """
        steps: list = []
        if verify_only:
            ok, msg = self._verify_poke_menu(chat_id, target_name, target_id, dbg=steps)
        else:
            ok, msg = self.send_poke(chat_id, target_name, target_id, dbg=steps)
        return {"ok": ok, "message": msg, "steps": steps}

    def _verify_poke_menu(self, chat_id: str, target_name: str, target_id: str = "",
                          dbg: list | None = None) -> tuple:
        """简易拍一拍检测（串行锁内执行）：只确认「定位 → 右键能弹出含拍一拍的菜单」。

        不点菜单项（Esc 关闭），确保不会误拍任何群友。返回 (ok, message)。
        """
        with self._send_lock:
            return self._verify_poke_menu_inner(chat_id, target_name, target_id, dbg)

    def _verify_poke_menu_inner(self, chat_id: str, target_name: str, target_id: str = "",
                                dbg: list | None = None) -> tuple:
        """简易拍一拍检测：只确认「定位到头像 → 右键能弹出含拍一拍的菜单」。

        不点菜单项（Esc 关闭），确保不会误拍任何群友。
        返回 (ok, message)。
        """
        def _d(msg):
            if dbg is not None:
                dbg.append(msg)
        try:
            gui = self._get_gui()
            if not self._prepare_for_capture(gui):
                return False, "微信窗口未找到或已退出，无法操作"
            if not self._open_chat_guarded(self.group_name(chat_id), chat_id):
                return False, "打开会话失败"
            time.sleep(0.9)
            db_text = self._target_recent_texts(chat_id, target_id) if target_id else []
            located = self._send_poke_locate(gui, target_name, db_text)
            if not located:
                _why = getattr(self, "_poke_locate_why", "") or "未找到「%s」的头像位置" % target_name
                _d("✘ 定位失败：%s" % _why)
                return False, "定位不到「%s」的头像：%s" % (target_name, _why)
            ax, ay, _score = located
            _d("头像位置：渲染坐标 (%d,%d)（已过归属校验：落在检测到的头像方块内）" % (ax, ay))
            # ⛔ 2026-09-18 删掉"改右键气泡"这条路：微信 4.1.15.8 的**消息菜单里没有「拍一拍」**
            #   （只有**头像菜单**里有），实测日志读到的是 撤销/放大阅读/翻译/转发/收藏 ⇒ 那条路是死的。
            ok, why = self._click(gui, ax, ay, right=True)
            if not ok:
                _d("✘ 右键被拦截：%s" % why)
                return False, "右键被拦截：%s" % why
            time.sleep(0.9)
            uia = gui._get_uia()
            menu = None
            if uia is not None:
                for label in ("拍一拍", "引用", "回复", "转发"):
                    try:
                        if uia._uia_find_menu_item(label) is not None:
                            menu = label
                            break
                    except Exception:
                        continue
            if menu is None:
                # OCR 兜底（只判断，不点击）；严格「真菜单」过滤：
                # 聊天文本（如「@E 第二条：拍一拍拍不上…」）含有 @、长于 12 字或行高
                # 远超菜单字条 h<46 · scale=3 还原后 → 全部剔除，杜绝误报成功
                try:
                    items = gui.ocr_zoomed((gui.right_pane_left, max(0, ay - 40),
                                            gui.render_w, min(gui.render_h, ay + 360)), scale=3)
                except Exception:
                    items = []
                for text, x, y, w, h in items:
                    t = (text or "").strip()
                    if not t or "@" in t or len(t) > 12 or h > 46:
                        continue
                    if t in ("拍一拍", "引用", "回复", "转发"):
                        menu = t
                        break
            # 关闭菜单：只有 UIA 确认到菜单节点才按 Esc（防误关聊天窗）
            if uia is not None and menu is not None and (menu in ("拍一拍", "引用", "回复", "转发")):
                try:
                    gui._input.key(0x1B)
                except Exception:
                    pass
            self._scroll_to_bottom(gui)
            if menu:
                return True, "✅ 菜单可弹出（识别到「%s」）——仅验证，未执行拍一拍" % menu
            return False, "✘ 右键后未识别到菜单（未执行任何点击，未拍任何人）"
        except Exception as e:
            return False, "异常：%s" % e

    def click_self_test(self) -> dict:
        """真实点击自检（安全版）：不切换会话、不搜索、不翻页——
        只验证「当前前台会话」头像定位+右键菜单可弹（同拍一拍链路，但绝不动会话列表/搜索框）。
        """
        try:
            # 直接用当前已打开的会话（微信前台那个）验证菜单链路；不 open_chat、不搜索
            gui = self._get_gui()
            from . import ui_adapt
            from .ui_adapt import click as _click
            if not ui_adapt.prepare_screen(gui):
                return {"ok": False, "detail": "屏幕预检失败（微信窗口不可见）"}
            box = gui.get_input_box()
            if not box:
                return {"ok": False, "detail": "当前没有打开的会话（请先打开任意群聊再体检）"}
            # 在消息区找一条群友消息做右键验证：限定当前视口（不滚动、不搜索）
            items = []
            try:
                top = max(80, box[1] - 520)
                items = gui.ocr((gui.right_pane_left, top, gui.render_w, box[1]))
            except Exception:
                pass
            mid_x = (gui.right_pane_left + gui.render_w) // 2
            items = [it for it in items if it[3] > 30 and (gui.right_pane_left + 60) < it[1] < mid_x]
            if not items:
                return {"ok": False, "detail": "当前会话没有可见文字消息（换到一个聊过的群再试）"}
            items.sort(key=lambda b: b[1])
            row = items[-1]  # 视口内最后一条可见消息
            ax = gui.right_pane_left + int((gui.render_w - gui.right_pane_left) * 0.185)
            ay = row[1] - 32
            # 候选点右键，验证「拍一拍」菜单（仅验证不点击菜单项）
            for cx, cy in [(ax + 130, ay + 30), (ax + 80, ay + 30), (ax + 40, ay + 40)]:
                if self._right_click_menu(gui, cx, cy, "拍一拍"):
                    return {"ok": True, "detail": "菜单可弹出（识别到「拍一拍」）——仅验证，未执行拍一拍"}
            return {"ok": False, "detail": "当前会话右键未出「拍一拍」菜单（换一个聊过的群试试）"}
        except Exception as e:
            return {"ok": False, "detail": "异常：%s" % e}

    @staticmethod
    def _uia_quote_finish(gui, text: str) -> bool:
        """UIA 直进输入框：写入（SetValue 后台直写，失败回退粘贴）→ 回车 → 读回验证。

        引用模式的输入框仍是同一个 UIA Edit 控件（位置/高度变化不影响），
        因此比像素探测稳定得多。返回 False 时调用方回退坐标路径。
        """
        try:
            uia = gui._get_uia()
            if uia is None:
                return False
            e = uia._chat_input()
            if e is None:
                return False
            # 优先 SetValue 后台直写（不点输入框/不抢焦点）；控件不认则回退粘贴
            if not uia._set_value_into(e, text, clear=True):
                uia._paste_into(e, text, clear=True)
            time.sleep(0.4)
            for _ in range(2):
                e.SendKeys("{Enter}", waitTime=0.05)
                time.sleep(0.7)
                try:
                    cur = str(e.GetValuePattern().Value or "")
                except Exception:
                    cur = str(getattr(e, "Value", "") or "")
                if text[:16].replace("\r", "").replace("\n", "") not in cur.replace("\r", "").replace("\n", ""):
                    return True
            return False
        except Exception:
            return False

    def _row_inner_text(self, row: dict) -> str:
        """取消息行里的真实文本；若是 zstd 压缩的 appmsg 则解压（用于验证拍拍事件）。"""
        content = row.get("content")
        if isinstance(content, bytes):
            if content.startswith(b"\x28\xb5\x2f\xfd"):
                try:
                    import zstandard
                    return zstandard.ZstdDecompressor().decompress(content, max_output_size=200000).decode("utf-8", "ignore")
                except Exception:
                    return ""
            return content.decode("utf-8", "ignore")
        return str(content or "")

    def _verify_poke(self, chat_id: str, target_name: str, base_seq: int = 0):
        """拍完后确认真的出现了**新的**拍拍提示。绝不假报成功。

        双重验证：
          ① 数据库轮询 5 秒：微信落库有延迟，找 base_seq 之后「新出现」的
             （zstd appmsg 或普通系统文本）含「拍拍/拍了拍」的行；
          ② 界面 OCR：自己发起的「你拍了拍…」提示可能不落库（实测），改为
             截图聊天区底部 180px（新提示总在最下面）找「拍了拍」——只认它，
             预防旧提示误报。
        """
        def _my_names() -> list:
            try:
                _cfg = get_config() or {}
                _ns = [str((_cfg.get("wechat") or {}).get("bot_nickname") or ""),
                       str((_cfg.get("persona") or {}).get("self_nickname") or ""),
                       str(getattr(self, "self_nickname", "") or "")]
            except Exception:
                _ns = []
            return [n.strip() for n in _ns if n and n.strip()]

        def _poke_is_mine(txt: str, target: str) -> bool:
            """这段文本是不是"**我**拍了 TA"？——`_verify_poke` 的唯一判据（逻辑在模块级，可单测）。"""
            return poke_text_is_mine(txt, target, _my_names())

        try:
            for _ in range(5):
                time.sleep(1.0)
                raws = self._db.get_new_messages(chat_id, base_seq, 10)
                for row in raws:
                    # get_new_messages 的 content 已被友好化（zstd→"[文件/链接/卡片]"），
                    # 必须用 get_message_row 取原始字节再解压才看得到「拍拍」
                    try:
                        raw_row = self._db.get_message_row(chat_id, int(row.get("local_id") or 0))
                    except Exception:
                        raw_row = None
                    txt = self._row_inner_text(raw_row or row)
                    if _poke_is_mine(txt, target_name):
                        return True, "已拍一拍「%s」（已验证：数据库里是**我发起**的拍拍）" % target_name
        except Exception as e:
            pass
        # 界面 OCR 验证（自己拍的提示不落库时用）
        try:
            gui = self._get_gui()
            box = gui.get_input_box()
            bottom = box[1] if box else gui.render_h - 60
            for _ in range(4):
                time.sleep(0.8)
                region = (gui.right_pane_left, max(80, bottom - 185), gui.render_w, bottom + 10)
                # ⚠️ 2026-09-18：这里也不能用抓屏 OCR（`gui.ocr_zoomed`/`gui.ocr` 都是抓屏，
                #   微信被别的窗口盖住时读到的是**别人的像素** ⇒ 明明拍上了却报"没拍上"）。
                #   改成 OCR 我们自己抓的那一帧（PrintWindow），并走加固层 `chat_ocr`。
                items = []
                try:
                    from . import chat_header as _ch2
                    from . import chat_ocr as _co2
                    _im = _ch2.grab_render(gui)
                    if _im is not None and not _co2.blocked():
                        _c = (int(region[0]), int(region[1]), int(region[2]), int(region[3]))
                        items = [(t, _c[0] + x, _c[1] + y, w, h)
                                 for t, x, y, w, h in _co2.recognize(_im.crop(_c))]
                except Exception:
                    items = []
                for text, *_ in items:
                    tn = self._norm_ocr(text)
                    if "你拍了拍" in tn:      # ⛔ 只认"我发起"的文案；不再接受泛泛的「拍了拍」
                        return True, "已拍一拍「%s」（已验证：界面出现「你拍了拍…」）" % target_name
        except Exception:
            pass
        return False, ("已点「拍一拍」但数据库与界面都未验证到**我发起**的拍拍"
                       "（可能没点中/没拍到，如实告诉对方这次没拍上）")

    def reply_quote(self, chat_id: str, text: str, target_text: str = "", target_sender_name: str = ""):
        """引用一条消息并发送文字（串行锁内执行）。"""
        with self._send_lock:
            return self._reply_quote_inner(chat_id, text, target_text, target_sender_name)

    def _reply_quote_inner(self, chat_id: str, text: str, target_text: str = "",
                           target_sender_name: str = ""):
        """引用一条消息并发送文字：定位「对方头像」→ 右键头像右侧的气泡起点 → 菜单「引用」→ 输入 → 发送。

        为什么不用「整行中央」：微信 4.x UIA 的 ChatTextItemView 矩形是**全宽行**，
        行中央往往是空白，右键不弹菜单（这就是之前「不会引用了 / 点击实测失败」的根因）。
        这里改用与拍一拍完全相同的头像定位（_send_poke_locate），再以 OCR 找气泡左端做
        命中测试；多个候选点逐一试右键，任一出菜单即点「引用」。
        target_text 空 = 引用「数据库最新一条群友消息」（近似）。
        """
        _halt = _control_halt()      # 暂停/停止闸：**已开工的链也要停**（2026-09-18）
        if _halt:
            return False, _halt
        # 2026-09-16 改口径：引用也**先试投递**（菜单那一跳走投递：不动光标（可能短暂置前约 1~3 秒后自动还回））；
        # 投递不成才由 `_real_mouse_allowed()` 决定是否回真鼠标。原来的"只走后台 ⇒ 跳过"已删。
        try:
            gui = self._get_gui()
            group = self.group_name(chat_id)
            if not self._prepare_for_capture(gui):
                return False, "微信窗口未找到或已退出，无法操作"
            if not self._open_chat_guarded(group, chat_id):
                return False, "打开会话失败"
            time.sleep(0.8)

            if not target_text.strip():
                # 引用「最近一条群友消息」：从数据库取最新文本做定位
                try:
                    for raw in self._db.get_messages(chat_id, limit=20):
                        norm = self.normalize(raw, chat_id)
                        if norm and str(norm.get("sender_id") or "").startswith("wxid_") \
                                and str(norm.get("text") or "").strip():
                            target_text = str(norm["text"])
                            target_sender_name = str(norm.get("sender_name") or "")
                            break
                except Exception:
                    pass

            hit = False
            for _round in range(2):
                located = None
                if target_text.strip():
                    # 翻页查找目标行并居中（找不到就继续上翻，绝不落到"乱点"兜底）
                    located = self._send_poke_locate(gui, target_sender_name or "", target_text, scroll=True)
                if located:
                    ax, ay, _score = located
                    # 候选点：① 气泡中带（OCR 行中心 y + 左缘+60，标定最优）② 中带偏右
                    # ③ 左缘（短气泡）④ 左缘偏右 —— 多点依次试，弹菜单即成功
                    px, py = self._bubble_point(gui, ax, ay, target_text)
                    points = [(px, py), (ax + 100, ay + 30)]
                else:
                    # 没有定位到目标行：不做任何"乱点兜底"（防止点到侧栏群名称/空白）。
                    # 滚动搜索交给 _send_poke_locate(scroll=True)，这里直接失败并提示。
                    break
                for cx, cy in points:
                    if self._right_click_menu(gui, cx, cy, "引用"):
                        hit = True
                        break
                if hit:
                    break
                # 一轮都没出菜单：滚回最新消息再重新定位（滚动位置/行位置可能已漂移）
                try:
                    self._scroll_to_bottom(gui)
                    time.sleep(1.0)
                except Exception:
                    pass
            if not hit:
                # 若菜单确实弹出过但没找到「引用」，安全关闭（UIA 确认到菜单节点才按 Esc）
                try:
                    uia = gui._get_uia()
                    if uia is not None and uia._uia_find_menu_item("转发") is not None:
                        gui._input.key(0x1B)
                except Exception:
                    pass
                self._scroll_to_bottom(gui)
                return False, "右键菜单里没找到「引用」（已按头像/气泡起点多次尝试；目标可能是自己最近发的消息或不在可见区——让对方说句话再试）"
            time.sleep(0.5)
            # 优先 UIA 直进输入框（粘贴+回车+读回验证），不依赖像素探测——
            # 引用模式下输入框探测（全宽白区+分界线）常失败，这正是「引用发送失败」的根因
            if self._uia_quote_finish(gui, text):
                self._mark_sent(text)
                self._scroll_to_bottom(gui)
                return True, "已引用并发送"
            # 🔴 2026-09-18 换掉这条回退（两个毛病，作者现场都撞上了）：
            #   ①它是**真鼠标**通路（`gui.input_text → focus_input → wx_click → real_click`），
            #      违反"任何路径不许动真鼠标"；②它的落点来自 `get_input_box()`，探不到时是**按比例
            #      猜的矩形**（`guia.py:1396-1399`），引用长消息时会落到引用条甚至 ✕ 上（作者口径见
            #      `_input_top_band` 上方注释）。⇒ 先走我们自己的**投递发送**（现算上沿带 + WM_CHAR +
            #      回车/发送按钮 + DB 回读，全程不动光标）；只有 `_real_mouse_allowed()` 为真才回退库那条。
            #      `allow_no_ref=True` 的依据：**本函数进门刚用 `_open_chat_guarded` 验过身份**
            #      （强档证据），这里不该再被弱档指纹挡一次。
            _p_ok, _p_why = self.send_text_posted(text, chat_id, allow_no_ref=True)
            if _p_ok:
                self._mark_sent(text)
                self._scroll_to_bottom(gui)
                return True, "已引用并发送"
            log.info("引用·投递发送未成（%s）", str(_p_why)[:110])
            if not self._real_mouse_allowed():
                self._scroll_to_bottom(gui)
                return False, ("引用已插入，但投递发送没成（%s）⇒ 按最高目标不退回真鼠标"
                               "（库那条会动光标，落点也是猜的）" % str(_p_why)[:90])
            # 回退（仅真鼠标档）：把**实测上沿带**交给驱动库 fast 路径（它内部点上沿，不点中心）
            _band, _ = _input_top_band(gui)
            if _band is None:
                self._scroll_to_bottom(gui)
                return False, "引用已插入，但输入框量不到 ⇒ 不猜落点（真鼠标档也不猜）"
            ok_in = gui.input_text(text, box=tuple(_band), fast=True)
            if not ok_in:
                self._scroll_to_bottom(gui)
                return False, "输入文字失败"
            ok_send = gui.click_send(fast=True)
            if not ok_send:
                ok_send = gui.click_send()
            if not ok_send:
                self._scroll_to_bottom(gui)
                return False, "发送失败"
            self._mark_sent(text)
            self._scroll_to_bottom(gui)
            return True, "已引用并发送"
        except Exception as e:
            return False, str(e)

    # ── 消息菜单操作（收藏 / 撤回 / 删除 / 置顶 / 多选转发等）─────────────
    # 复用引用链路的「头像定位 + 气泡起点右键」：对指定消息弹右键菜单点菜单项。
    # 微信 4.x 菜单项：复制 / 收藏 / 转发 / 引用(对方) / 撤回 / 删除 / 置顶 / 多选…

    def message_menu(self, chat_id: str, text: str, sender_name: str = "", label: str = "收藏",
                     media: bool = False, want_lid=None) -> tuple:
        """对一条消息执行右键菜单操作。text/sender_name 用于定位（数据库归一化消息）。
        label: 收藏 / 撤回 / 删除 / 置顶 / 转发 / 多选…（失败返回 (False, 原因)，绝不乱点）。
        media: 目标是**媒体消息**（动画表情/图片，气泡没有可读文本）⇒ OCR 文本定位必然失败，
               改用几何定位 `_media_bubble_locate`（见那里的取证）。"""
        with self._send_lock:
            try:
                gui = self._get_gui()
                if not chat_id:
                    return False, "未指定会话（chat_id 为空），不执行任何操作（防止误点搜索框/其它会话）"
                group = self.group_name(chat_id) or chat_id
                if not self._prepare_for_capture(gui):
                    return False, "微信窗口未找到或已退出，无法操作"
                if not self._open_chat_guarded(group, chat_id):
                    return False, "打开会话失败"
                time.sleep(0.8)
                if not text.strip():
                    # 未指定文本 → 取数据库最近一条群友消息
                    for raw in self._db.get_messages(chat_id, limit=20):
                        norm = self.normalize(raw, chat_id)
                        if norm and str(norm.get("sender_id") or "").startswith("wxid_") \
                                and str(norm.get("text") or "").strip():
                            text = str(norm["text"])
                            sender_name = str(norm.get("sender_name") or "")
                            break
                if not text.strip():
                    return False, "没有可定位的消息文本"
                # 只滚到底一次 + 当前视口查找（不翻页循环，避免屏幕来回滚动）
                self._scroll_to_bottom(gui)
                time.sleep(0.8)
                located = self._send_poke_locate(gui, sender_name or "", text, scroll=False, self_side=(label == "撤回"))
                if located:
                    ax, ay, _ = located
                    px, py = self._bubble_point(gui, ax, ay, text)
                    for cx, cy in [(px, py), (ax + 130, ay + 30), (ax + 70, py)]:
                        if self._right_click_menu(gui, cx, cy, label):
                            self._scroll_to_bottom(gui)
                            return True, "已%s" % label
                # ⚡ 2026-09-18 晚：**媒体消息**（动画表情/图片）没有可读文本 ⇒ 上面的 OCR 文本定位必然
                #   匹配不上（实测：4.0s 后报"右键菜单里没找到"，而光标一动不动＝**一枪都没点**）。
                #   ⇒ 改走几何定位（只在"目标就是最新那条"时可用，判据在 `_media_bubble_locate` 里）。
                _mw = ""
                if media or str(text).strip() in self._MEDIA_PLACEHOLDERS:
                    mx, my, _mwhy = self._media_bubble_locate(gui, chat_id=chat_id, want_lid=want_lid)
                    _mw = str(_mwhy)
                    if mx is not None and self._right_click_menu(gui, mx, my, label):
                        self._scroll_to_bottom(gui)
                        return True, "已%s（%s）" % (label, _mwhy)
                # 安全关闭未选中的菜单
                try:
                    uia = gui._get_uia()
                    if uia is not None and uia._uia_find_menu_item("转发") is not None:
                        gui._input.key(0x1B)
                except Exception:
                    pass
                self._scroll_to_bottom(gui)
                _extra = ("；媒体几何定位也没成：%s" % _mw) if _mw else ""
                return False, ("没定位到目标消息（OCR 文本匹配%s都没成）⇒ 没点右键，绝不乱点"
                               % ("与媒体几何定位" if _mw else "及占位文本都") + _extra)
            except Exception as e:
                return False, str(e)

    def collect_message(self, chat_id: str, text: str = "", sender_name: str = "") -> tuple:
        """收藏一条消息。"""
        return self.message_menu(chat_id, text, sender_name, "收藏")

    def recall_message(self, chat_id: str, text: str = "", sender_name: str = "") -> tuple:
        """撤回自己最近发的一条消息（需 2 分钟内）。"""
        return self.message_menu(chat_id, text, sender_name, "撤回")

    def collect_emoji_native(self, chat_id: str, text: str = "", sender_name: str = "",
                             local_id=None) -> tuple:
        """真实路径收藏：右键表情气泡 → 菜单「添加到表情」→ 存入微信表情库。
        与 collect_emoji（本地截图收藏夹）并存：本方法走真微信操作。
        `local_id`：目标消息的库内 id（媒体消息**必须给**——几何定位要靠它核对"目标就是最新那一条"）。"""
        return self.message_menu(chat_id, text, sender_name, "添加到表情",
                                 media=True, want_lid=local_id)

    # ── 微信表情面板（真实路径发收藏表情）────────────────────────────
    # 路径：点输入栏左下角「笑脸」→ 弹出面板 → 底部右侧「爱心」（收藏的表情）
    # → 点目标表情 → 发送。全部用相对输入栏坐标 + ui_adapt（DPI 无关）+ 验证。

    def _search_group(self, gui, name: str) -> bool:
        """搜索群聊进群：官方 open_chat（UIA 优先+侧栏 OCR 点击+搜索兜底），重试 2 次。"""
        _st0 = time.time()
        for i in range(1):                     # 只试 1 次进群（之前重试 2 次，open_chat 每次可能 5~15s，重试白白翻倍）
            try:
                if self._open_chat_guarded(name):
                    if _DEBUG: print("[emoji-search] open_chat 成功 用时 %.2f s" % (time.time() - _st0), flush=True)
                    return True
            except Exception as e:
                if _DEBUG: print("[emoji-search] open_chat 异常 %s 用时 %.2f s" % (str(e)[:40], time.time() - _st0), flush=True)
        if _DEBUG: print("[emoji-search] 失败 总用时 %.2f s" % (time.time() - _st0), flush=True)
        return False

    def _emoji_btn_pos(self, gui):
        """笑脸按钮（输入框左下方工具栏第一个）——**渲染相对坐标**（调用方 ui_adapt.click 会加原点）。

        2026-09-13 修（用"投递鼠标消息打开表情面板"的 A/B 实验测出来的真缺陷）：
        老实现拿 `get_input_box()` 的**渲染相对** box 又减了一次渲染原点（`box[0] - rx`），
        等于双重换算 ⇒ 算出的点偏左约 100px、偏下约 40px，**点不开表情面板**。
        实测（1160×900 窗口）：笑脸在渲染区 `(0.324·w, h-50)`（屏幕 (470,930)，渲染相对 (369,840)）；
        box 可用时只用它做**细校正**（相差超过 8% 就不用，避免再次被错坐标带偏）。

        ⚡ 2026-09-18 晚再修（**现场：窗口改成 947×972 之后，表情面板一次都开不出来**）：
        比例法算出 `0.324×947 = 306`，而笑脸实际在 379 —— 306 落在输入框**左边**的空白处，
        点了等于没点（日志那轮：面板窗从头到尾没出现过）。根因＝**笑脸与输入框左沿的距离基本恒定
        （本机 331→379 ≈ 48px），而"占整幅的比例"会随窗口宽高比变**。⇒ 改成**先在帧里现量**
        （`chat_ocr.toolbar_first_icon`：工具栏行最左边那个字形），量不到才退回比例法。
        """
        try:
            from . import chat_header as _ch
            from . import chat_ocr as _co
            img = _ch.grab_render(gui)
            pane = 0
            try:
                pane = int(_ch.detect_pane_left(img)) or 0 if img is not None else 0
            except Exception:
                pane = 0
            if not pane:
                pane = int(getattr(gui, "right_pane_left", 0) or 0)
            hit = _co.toolbar_first_icon(img, pane_left=pane)
            if hit:
                log.info("笑脸落点：帧内现量 (w=%s, pane_left=%s) → (%d,%d)",
                         None if img is None else img.size[0], pane, hit[0], hit[1])
                return (int(hit[0]), int(hit[1]))
        except Exception as e:                                     # noqa: BLE001
            log.debug("笑脸帧内现量失败（退回比例法）：%s", e)
        try:
            r = gui.render_rect or gui._update_render_rect() or (0, 0, 0, 0)
            w = int(getattr(gui, "render_w", 0) or (int(r[2]) - int(r[0])))
            h = int(getattr(gui, "render_h", 0) or (int(r[3]) - int(r[1])))
            x, y = int(w * 0.324), int(h - 50)
            box = gui.get_input_box()
            if box:
                bx = int(box[0] + (box[2] - box[0]) * 0.132)   # box 本身是渲染相对，直接用
                if abs(bx - x) <= max(20, int(w * 0.08)):
                    x = bx
            return (x, y)
        except Exception:
            return None

    def _panel_rect(self, gui):
        """动态定位表情面板矩形（屏幕坐标）。
        优先 SiteBridge/PopupWindow（旧 UI 独立窗）；新版（WinUI 内嵌弹层）按微信子窗尺寸特征找；
        都找不到时按主窗右下区域（输入框上方）估算，保证布局推理有基准。"""
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(int(gui.main_hwnd), ctypes.byref(pid))
            wx_pid = pid.value
            CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            found = [None]

            def cb(h, l):
                if h == int(gui.main_hwnd) or not user32.IsWindowVisible(h):
                    return True
                p2 = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(h, ctypes.byref(p2))
                if p2.value != wx_pid:
                    return True
                buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(h, buf, 256)
                cls = buf.value or ""
                r = wintypes.RECT(); user32.GetWindowRect(h, ctypes.byref(r))
                w, hh = r.right - r.left, r.bottom - r.top
                if "SiteBridge" in cls or cls.startswith("PopupWindow"):
                    if 380 <= w <= 1300 and 360 <= hh <= 950:
                        found[0] = (r.left, r.top, r.right, r.bottom)
                        return False
                elif w < 200 or hh < 200 or w > 1300 or hh > 950:
                    return True
                # 新 UI 内嵌/独立弹层：尺寸像面板的微信子窗（位于主窗下半部）
                found[0] = (r.left, r.top, r.right, r.bottom)
                return False
            ref = CB(cb); user32.EnumWindows(ref, 0)
            if found[0]:
                return found[0]
        except Exception:
            pass
        # 兜底：主窗右下面板估算（输入栏上方）
        try:
            r = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(int(gui.main_hwnd), ctypes.byref(r))
            l, t, rt, b = r.left, r.top, r.right, r.bottom
            W, H = rt - l, b - t
            return (l + int(W * 0.30), t + int(H * 0.20), max(l + int(W * 0.30) + 460, l + W - 40), t + H - 130)
        except Exception:
            return None

    def _panel_visible(self, gui, sx, sy, sw, sh) -> bool:
        """表情面板在吗？判定：面板标签行（下部 0.78~0.84sh、左 0.02~0.24sw）有图标暗点。"""
        try:
            from PIL import ImageGrab as _IG
            img = _IG.grab((sx, sy, sx + sw, sy + sh)).convert("L")
            px = img.load()
            dark = 0
            for yy in range(int(sh * 0.78), int(sh * 0.84), 2):
                for xx in range(int(sw * 0.02), int(sw * 0.24), 2):
                    if px[xx, yy] < 210:
                        dark += 1
            return dark >= 6
        except Exception:
            return False

    def emoji_panel_open(self, group_name: str = "", chat_id: str = "") -> tuple:
        """点笑脸打开表情面板（恒定方案：先搜索群名进入会话——不管画面空不空）。返回 (ok, msg)。

        🔴 2026-09-18 修（拍摄现场：**面板开了、表情一个没发出去、日志一个字都没有**）：
          ① 原来这里调 `_open_chat_guarded(group_name)` **连返回值都不看** ⇒ 切会话失败也照样
             往下点笑脸，面板就开在"当前碰巧打开的那个会话"上（等于在未知会话上开面板）；
          ② 现在：先要一次"当前会话＝目标会话"的确认（传了 `chat_id` 时走投递优先的
             `_open_chat_guarded`），**确认不了就直接失败返回**，并说明原因（绝不静默）。
        """
        try:
            gui = self._get_gui()
            from . import ui_adapt
            if not ui_adapt.prepare_screen(gui):
                return False, "屏幕预检失败"
            # ① 先看"当前会话是不是就是目标"——**是就绝不搜索**。
            #   ⛔ 2026-09-18 现场（用户原话）：「我刚刚 Ctrl 踩在顶层的时候，**它似乎想要搜索的时候，把微信
            #   窗口置顶了**，因为我看到微信窗口从控制台的后面跳到前面」。老代码这里**无条件先
            #   `_search_group()`**（点搜索框→粘群名→点结果行），哪怕当前已经就在目标会话里 ⇒ 白搜一趟，
            #   而搜索路线会碰微信搜索框、把微信带到前台（日志同族的账：「还前台收尾（投递发送后）：
            #   试了 14 次，最终前台=133638 ✗ 没能回到」）。⇒ 先用强档证据确认，确认到了就**什么都不做**。
            if group_name:
                _already = False
                if chat_id:
                    try:
                        _already, _why_already = self.chat_is_open(chat_id, gui=gui)
                        if _already:
                            log.info("表情面板：当前会话已是目标（%s）⇒ **不搜索、不切会话**",
                                     str(_why_already)[:80])
                    except Exception:
                        _already = False
                # 只有"确认不了当前会话"时才去搜索进群；搜索路线失败再兜底（兜底也是投递优先）
                if not _already and not self._search_group(gui, group_name):
                    if not self._open_chat_guarded(group_name, chat_id):
                        return False, ("确保目标会话失败（投递切会话与真鼠标都没成）⇒ "
                                       "**不在未知会话上开表情面板**（防把表情发到别的群）")
                time.sleep(0.25)   # 进群后立刻移向笑脸（原 0.40 压缩；仍够会话切稳）
            else:
                # 无会话名：自动开第一个群（只探测一次，避免 UIA 连续失败重试）
                try:
                    for g in self.list_groups():
                        if g.get("name"):
                            self._open_chat_guarded(g["name"])
                            time.sleep(1.0)
                            break
                except Exception:
                    pass
            # ② 点笑脸（去掉此前"点消息区空白关面板"的冗余动作——它会先把光标移到窗口中间偏右悬停 0.3s，
            #    浪费大量时间；表情面板不会遮挡输入栏笑脸，直接点即可。保留 heal=False 防取消菜单）
            render = gui.render_rect or gui._update_render_rect() or (0, 0, 0, 0)
            sx, sy, sw, sh = render
            pos = self._emoji_btn_pos(gui)
            if not pos:
                return False, "输入栏定位失败（会话未打开？）"
            ok, why = ui_adapt.click(gui, pos[0], pos[1], heal=False)   # 点表情菜单必须 heal=False（heal 抖动会取消菜单）
            if not ok:
                return False, "点笑脸失败：%s" % why
            time.sleep(0.72)   # 等表情面板弹出（0.80→0.72 略压缩；再短会未弹稳→点偏格）
            return True, "表情面板已打开（只点一下，绝不重复点击）"
        except Exception as e:
            return False, str(e)

    # 表情格为固定物理尺寸：表情图≈105×107px、行距≈132px（不随窗口 sh 缩放）。
    # 因此用「窗口比例」算点击中心会在 sh 变化后漂移偏上。用「视口固定 top 完整行中心 base
    # + 行距 132×click_row」定位点击点，精确落在格正中心、自适应任意 DPI/窗口。
    EMOJI_ROW_PX = 132          # 行距（物理px，用户实测 表情图107+间隙25≈130~140）

    def _emoji_base_center(self, gui):
        """检测表情面板「第一列最顶部完整表情行」的中心 y（渲染相对坐标）= 视口 row0 中心。
        限定 y 范围排除顶栏搜索区与底部 爱心/输入栏 干扰；失败返回 None。"""
        try:
            from PIL import ImageGrab
            if not gui.render_rect:
                gui._update_render_rect()            # 复用已算好的 render（避免反复 UIA 拉窗口矩形——大头）
            sx, sy, sw, sh = gui.render_rect
            if not sw or not sh:
                return None
            cx0 = int(sw * 0.092)
            x0, x1 = max(0, cx0 - 52), min(sw, cx0 + 52)
            y_lo, y_hi = int(sh * 0.20), int(sh * 0.92)   # 排除顶栏 / 底部栏
            # 只截「第一列小竖条」(约 100px 宽)——ImageGrab 比整窗快一个量级；隔行扫描 + 首完整行早停
            img = ImageGrab.grab((sx + x0, sy + y_lo, sx + x1, sy + y_hi)).convert("RGB")
            W2, H2 = img.size
            px = img.load()
            step = 3
            cur = None
            for y in range(0, H2, step):
                c = 0
                for x in range(0, W2):
                    r, g, b = px[x, y]
                    if max(r, g, b) - min(r, g, b) > 45 or max(r, g, b) < 195:
                        c += 1
                if c > 22:
                    if cur is None: cur = [y, y]
                    else: cur[1] = y
                else:
                    if cur is not None:
                        b = tuple(cur); cur = None
                        if 90 <= (b[1] - b[0]) <= 130 and b[0] >= 20:
                            return y_lo + (b[0] + b[1]) // 2      # 首个完整行中心（早停，转回渲染相对 y）
            return None
        except Exception:
            return None

    def _emoji_bottom_center(self, gui):
        """检测表情面板「第一列最底部完整表情行」中心 y（渲染相对）。用于「最后一行/滚到底」
        场景：此时目标行=最底部完整行（顶部露半行、下方4行完整可点）。失败返回 None。"""
        try:
            from PIL import ImageGrab
            if not gui.render_rect:
                gui._update_render_rect()            # 复用已算好的 render
            sx, sy, sw, sh = gui.render_rect
            if not sw or not sh:
                return None
            cx0 = int(sw * 0.092)
            x0, x1 = max(0, cx0 - 52), min(sw, cx0 + 52)
            y_lo, y_hi = int(sh * 0.20), int(sh * 0.92)
            # 只截第一列小竖条 + 从底向上倒扫 + 完整行早停
            img = ImageGrab.grab((sx + x0, sy + y_lo, sx + x1, sy + y_hi)).convert("RGB")
            W2, H2 = img.size
            px = img.load()
            step = 3
            cur = None
            for y in range(H2 - 1, 0, -step):
                c = 0
                for x in range(0, W2):
                    r, g, b = px[x, y]
                    if max(r, g, b) - min(r, g, b) > 45 or max(r, g, b) < 195:
                        c += 1
                if c > 22:
                    if cur is None: cur = [y, y]
                    else: cur[0] = y
                else:
                    if cur is not None:
                        b = tuple(cur); cur = None
                        if 90 <= (b[1] - b[0]) <= 130:
                            return y_lo + (b[0] + b[1]) // 2      # 最底部完整行中心（早停，转回渲染相对 y）
            return None
        except Exception:
            return None

    def _emoji_bottom_bar(self, sx, sy, sw, sh) -> bool:
        """面板底部 0.92~0.985 高度带是否有图标暗点 → 新 UI 底部工具栏（面板默认即收藏视图）。"""
        try:
            from PIL import ImageGrab as _IG
            img = _IG.grab((sx, sy, sx + sw, sy + sh)).convert("L")
            px = img.load()
            dark = 0
            for yy in range(int(sh * 0.92), int(sh * 0.985), 2):
                for xx in range(int(sw * 0.04), int(sw * 0.60), 2):
                    if px[xx % max(1, sw), yy % max(1, sh)] < 210:
                        dark += 1
            return dark >= 8
        except Exception:
            return False

    def emoji_panel_send(self, index: int = 0, chat_id: str = "") -> tuple:
        """发送收藏表情：布局自适应 —— 旧 UI：点爱心（面板标签行）→ 点第 index 格；
        新 UI：面板默认即收藏视图（底栏工具栏），直接点第 index 格。
        网格几何按面板自身矩形推算（5 列），随 DPI/分辨率/面板尺寸自适应。

        🔴 2026-09-18 修：**点完必须回读确认**。原来点一下 `ui_adapt.click` 就返回
        "已点击第 N 个收藏表情（点击即发送）"——而我们自己早测出的教训是**"面板刚弹出就立刻点会丢"**，
        现场后果是：面板开了、用户看到面板、**表情一个没发出去、代码却报成功、日志一字不留**。
        现在：点完轮询数据库（`latest_seq` 前后比对）确认真出了新行（表情/图片类），
        确认不到就**换下一格重试一次**，仍确认不到 ⇒ **如实返回失败**（绝不假报已发）。
        """
        import ctypes
        try:
            gui = self._get_gui()
            from . import ui_adapt
            panel = self._panel_rect(gui)
            if not panel:
                return False, "找不到表情面板"
            px0, py0, px1, py1 = panel
            pw, ph = px1 - px0, py1 - py0
            render = gui.render_rect or gui._update_render_rect() or (0, 0, 0, 0)
            rx, ry = int(render[0]), int(render[1])
            _base = None
            if chat_id:
                try:
                    _base = self.latest_seq(chat_id)
                except Exception:
                    _base = None
            # ① ♡「收藏的表情」标签：**一律先点**（2026-09-18 修）。
            #   ⛔ 老代码靠 `_emoji_bottom_bar()` 猜"新 UI 默认就是收藏视图"⇒ 直接跳过这一步；
            #   用户现场看到的正是"**他压根没点爱心，只把面板点开了**"。我们**没有**任何可靠的
            #   "现在已经在收藏视图"判据，所以一律点一下（点错顶多是把视图切回收藏，代价一次点击）。
            #   坐标＝我们自己实测的值（`_scratch/sticker_g_ab.py::HEART_REL`：面板相对 (0.314, 0.918)）。
            _heart = (px0 + int(pw * 0.314), py0 + int(ph * 0.918))
            _old_heart = (px0 + int(pw * 0.171), py0 + int(ph * 0.804 - 13))
            ui_adapt.click(gui, _heart[0] - rx, _heart[1] - ry, heal=False)
            log.info("表情链：点 ♡ 收藏标签（面板相对 0.314/0.918 → 屏幕 %s）", _heart)
            time.sleep(0.7)
            # 兜底：老 UI 的爱心在另一处 —— 只有在新坐标这一下之后库里仍无动静时才补一次（见下面重试）
            cols = 5
            ROWS = 5   # 一屏完整行（面板约 6~7 行，预留）

            def _cell_pos(i: int, variant: str = "measured"):
                """格子中心。**主用实测常量**（`sticker_h_send.py`：第 3 行第 3 列 = 0.5227/0.4903，
                反推第一格 0.183/0.166、步长 0.170/0.162 —— 与 memory 里 0.182/0.167/0.170/0.162 互证）。
                ⛔ 老代码用的是 `0.10+0.19c / 0.085+0.14r`：按它算第 3 行第 3 列是 (0.48, 0.365)，
                **纵向偏上约 96px** ⇒ 点在格子缝里，这就是"面板开了、表情没发出去"的直接原因之一。"""
                _col = i % cols
                _row = i // cols
                if variant == "measured":
                    return (px0 + int(pw * (0.183 + _col * 0.170)),
                            py0 + int(ph * (0.166 + _row * 0.162)))
                return (px0 + int(pw * (0.10 + _col * 0.19)),
                        py0 + int(ph * (0.085 + _row * 0.14)))

            def _click_cell(i: int, variant: str = "measured"):
                _row = i // cols
                if _row >= ROWS:
                    return False, "收藏较多（%d 个）超出面板首屏，请先发送靠前的收藏" % (i + 1)
                gx, gy = _cell_pos(i, variant)
                ok, why = ui_adapt.click(gui, gx - rx, gy - ry, heal=False)
                if not ok:
                    return False, "点表情失败：%s" % why
                log.info("表情链：点第 %d 格（%s 常量 → 屏幕 (%d,%d)）", i + 1, variant, gx, gy)
                return True, ""

            def _confirmed() -> bool:
                """点完看库里有没有新行（确认不到就返回 False，由调用方决定重试）。"""
                if _base is None:
                    return True          # 没给 chat_id ⇒ 无法回读，按老口径（调用方自己交代）
                for _ in range(6):       # 最多约 3 秒
                    time.sleep(0.5)
                    try:
                        if self.latest_seq(chat_id) != _base:
                            return True
                    except Exception:
                        pass
                return False

            _last = ""
            # 候选顺序（每一发都**以库里出现新行为准**，不是"我点过了"）：
            #   ① 实测常量 × 要求的格 → ② 实测常量 × 下一格 → ③ 老常量 × 要求的格（防这版 UI 是老的）
            #      → ④ 老 UI 爱心位置 + 老常量 × 要求的格（把"点错视图/点错位置"两种可能都覆盖掉）
            _plan = ((int(index), "measured"), (int(index) + 1, "measured"),
                     (int(index), "legacy"), (int(index), "legacy_after_old_heart"))
            for _i, _variant in _plan:
                if _variant == "legacy_after_old_heart":
                    ui_adapt.click(gui, _old_heart[0] - rx, _old_heart[1] - ry, heal=False)
                    log.info("表情链：改用老 UI 爱心位置再试一次（屏幕 %s）", _old_heart)
                    time.sleep(0.6)
                    ok, why = _click_cell(_i, "legacy")
                else:
                    ok, why = _click_cell(_i, _variant)
                if not ok:
                    _last = why
                    continue
                time.sleep(1.0)
                if _confirmed():
                    return True, ("已发送第 %d 个收藏表情（%s，已回读确认新行）"
                                  % (_i + 1, "实测坐标" if _variant == "measured" else "老坐标"))
                _last = ("点了第 %d 格（%s）但**库里没出现新行**" % (_i + 1, _variant))
            return False, ("点了表情格但都没能确认发出（%s）——**如实说没发出去**，稍后可重试" % _last)
        except Exception as e:
            return False, str(e)

    # ── 图片下载 ─────────────────────────────────────────────────────────

    def _ensure_img_key(self):
        if self._img_key_ready or self._md is None:
            return self._img_key_ready
        try:
            if self._md._load_persisted_key():
                self._img_key_ready = True
                return True
            self._md.detect_image_key(refresh=True)
            if self._md._load_persisted_key():
                self._img_key_ready = True
        except Exception:
            pass
        return self._img_key_ready

    def decode_emoji(self, chat_id: str, local_id, out_dir: str = "", allow_scan: bool = True) -> str:
        """把一条「动画表情」消息**离线解成视觉能看的图**（拿不到就返回 `""`）。

        为什么要它（2026-09-18 落地，算法来自开源项目 CN-Grace/Wechat-Emoticon-Parser）：
        微信 4.x 的表情在库里是加密数据、驱动库只认 3/34/43/49 ⇒ 47 号动画表情一直下不来，
        之前只能"截最新一条消息的图"兜（还必须是最新那条）。现在按 md5 找到**原文件**、
        用 `AES-128-CBC(key=IV=md5(f"{seed}{wxid}EMOTICON")[:16])` 解开 ⇒ **原图直出**，
        不再受"是不是最新一条"和"窗口可不可见"的限制。
        ⚠️ 拿不到 key / 找不到文件 / 解出来不是图 ⇒ 返回空串，调用方**退回截图路线**（绝不猜）。
        """
        try:
            from . import emoticon as _em
            return _em.sticker_image(self._db, chat_id, local_id, out_dir=out_dir,
                                     allow_scan=allow_scan)
        except Exception as e:                                   # noqa: BLE001
            log.debug("表情离线解密不可用（退回截图）：%s", e)
            return ""

    def download_image(self, chat_id: str, local_id) -> str | None:
        """下载并解密群内图片，返回本地路径；失败返回 None。"""
        if self._md is None:
            return None
        self._ensure_img_key()
        media_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 str(self.cfg.get("wechat", {}).get("media_dir") or "media"))
        os.makedirs(media_dir, exist_ok=True)
        try:
            return self._md.download_image(chat_id, int(local_id), save_dir=media_dir)
        except Exception:
            return None

    def capture_newest_message_image(self, chat_id: str, tag: str = "") -> str | None:
        """把「最新一条消息那一带」截下来落盘，返回 PNG 路径（失败 None）。

        **为什么需要它**（用户反馈「识别不了表情包」「原本有的功能没了」）：微信 4.x 的表情消息
        content 在库里是**加密数据**（驱动库作者原话，见 `wechatauto/demo_emoji_capture.py`；
        本机实测：表情缓存在 `cache\\<月>\\Emoticon\\<md5[:2]>\\<md5>`，文件头无任何已知魔数、
        也不是图片那套 AES-ECB——用图片密钥解不出来），驱动库 `MediaDownloader` 又只认
        `local_type ∈ {3,34,43,49}` ⇒ **47 号"动画表情"一个都下不来** ⇒ 机器人只看到 `[表情]`
        两个字，只能如实说"看不到图"。唯一可行的取图方式＝**截图**（库作者就是这么建议的）。

        **怎么截**：不碰前台、不点鼠标 —— `chat_header.capture_image()` 走
        `PrintWindow(PW_RENDERFULLCONTENT)`，拿的是**窗口自己的画面**（被遮挡也拿得到），
        再按聊天面板几何裁出最新消息所在的一带。

        ⛔ 两道闸（防"把别的会话的图当成本会话的消息喂给模型"）：
          ① **会话头指纹必须 ok**（证明当前打开的**就是**这个会话；`no_ref`/`mismatch` 一律不截）；
          ② 抓不到画面（微信最小化/收进托盘）⇒ 不截。
        两条都是"宁可不给图，也不给错图"——看不到就如实说看不到（这是本项目的既有口径）。
        """
        try:
            from . import chat_header as _ch
            gui = self._get_gui()
            st = {}
            try:
                st = _ch.check(chat_id, gui=gui)
            except Exception as e:
                log.debug("表情截图：会话头校验异常：%s", e)
            if str((st or {}).get("status")) != "ok":
                log.info("表情/图片截图跳过：会话头未确认（%s）—— 绝不猜画面归属",
                         (st or {}).get("status"))
                return None
            img = _ch.capture_image(gui=gui)
            if img is None:
                log.info("表情/图片截图跳过：抓不到窗口画面（微信最小化/收进托盘时 PrintWindow 取不到）")
                return None
            w, h = img.size
            top, bot = int(h * 0.50), int(h * 0.88)
            if bot - top < 40 or w < 64:
                log.info("表情/图片截图跳过：渲染区太小（%sx%s）", w, h)
                return None
            crop = img.crop((int(w * 0.26), top, w, bot))
            d = getattr(self, "EMOJI_SHOT_DIR", None) or os.path.join(ROOT, "media", "emoji")
            os.makedirs(d, exist_ok=True)
            safe_tag = re.sub(r"[^0-9A-Za-z_\-]+", "_", str(tag or ""))[:16] or str(int(time.time()))
            safe_chat = abs(hash(str(chat_id))) % (10 ** 8)
            out = os.path.join(d, "shot_%s_%s.png" % (safe_chat, safe_tag))
            crop.save(out, "PNG")
            log.info("表情/图片截图已存：%s（会话头 ok sim=%.3f，%dx%d）",
                     os.path.basename(out), float((st or {}).get("sim") or 0.0), crop.size[0], crop.size[1])
            return out
        except Exception as e:
            log.warning("表情/图片截图失败（如实说看不到就好）：%s", e)
            return None

    def _text_landed_in_other_chat(self, text: str, chat_id: str, window_s: float = 120.0):
        """这条文字是不是**落到别的会话里**了？返回 `(chat_id, 名字)`；没有 ⇒ `None`。

        **为什么需要**（用户反馈里最严重的一条：「**他把我在实验群发的消息回到大群了**」）：
          `send_text_posted` **不会切会话** —— 它靠会话头指纹/四档屏幕证据证明"当前打开的就是目标"。
          证据一旦误判（指纹假阳性、OCR 读错名字、活动行时间恰好撞上），文字就会被打进**当时打开的
          另一个会话**并且真的发出去；而我们的成功判据是"在**目标会话**里回读到新行"，查不到 ⇒
          表观症状只是"发送未生效"，**发错会话这件事被完全掩盖**（这条风险 2026-09-13 就写在
          `send_text_posted` 的注释里，但一直没有第二道网兜它）。更糟的是**重试**：每多打一枪，
          就往那个错会话**再发一遍**同一句话。
        做法（全离线、只读库、不碰窗口/光标）：把已知会话（群 + 私聊）最近几行扫一遍，归一化后
          对上这条文字、时间在 `window_s` 内、且**不是**目标会话 ⇒ 返回那个会话。
        ⚠️ 只在**目标会话回读失败**时才调用它（正常发送根本不走这条）——所以它不会给正常路径加开销。
        """
        t = self._echo_norm(str(text or ""))
        if len(t) < 2:
            return None
        cands = []
        for src in (getattr(self, "_groups", None) or [], getattr(self, "_privates", None) or []):
            for it in (src or []):
                if isinstance(it, dict):
                    cid = str(it.get("wxid") or it.get("chat_id") or it.get("username")
                              or it.get("user") or "")
                    nm = str(it.get("nickname") or it.get("name") or it.get("remark") or "")
                else:
                    cid, nm = str(it or ""), ""
                if cid and cid != str(chat_id):
                    cands.append((cid, nm))
        now = int(time.time())
        for cid, nm in cands:
            try:
                rows = list(self._db.get_messages(cid, limit=4) or [])
            except Exception:
                continue
            for row in rows:
                try:
                    ct = int(row.get("create_time") or 0)
                except Exception:
                    continue
                if ct and ct < 1e12:
                    ct = ct
                if ct and abs(now - ct) > float(window_s):
                    continue
                got = self._echo_norm(str(row.get("content") or ""))
                if not got:
                    continue
                short, long_ = (t, got) if len(t) <= len(got) else (got, t)
                if t == got or (len(short) >= 2 and short in long_):
                    name = nm or (self.group_name(cid) if hasattr(self, "group_name") else "") or cid
                    return (cid, str(name))
        return None

    def download_media(self, chat_id: str, local_id, kind: str) -> str | None:
        """下载语音 / 视频 / 文件到 `media/<kind>/`，返回本地路径；失败返回 None。

        kind = `voice` | `video` | `file`（分别对应驱动库的 download_voice / download_video / download_file）。
        语音落盘是 `.silk`（微信那套 `\\x02#!SILK_V3` 帧），转文字见 `agent/voice.py`。
        """
        if self._md is None:
            return None
        k = str(kind or "").strip().lower()
        fn = {"voice": "download_voice", "video": "download_video", "file": "download_file"}.get(k)
        if not fn:
            return None
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            str(self.cfg.get("wechat", {}).get("media_dir") or "media"))
        sub = os.path.join(base, k)
        os.makedirs(sub, exist_ok=True)
        try:
            return getattr(self._md, fn)(chat_id, int(local_id), save_dir=sub)
        except Exception:
            return None

    @staticmethod
    def image_to_base64(path: str, max_side: int = 1000) -> str | None:
        """本地图片 → data URL（jpeg，压缩尺寸）。"""
        try:
            from PIL import Image
            import io
            img = Image.open(path)
            img = img.convert("RGB")
            w, h = img.size
            if max(w, h) > max_side:
                r = max_side / float(max(w, h))
                img = img.resize((int(w * r), int(h * r)))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=82)
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return None

# ── 微信版本自动检测（启动/自检/控制台/检查脚本共用）────────────────

def wechat_install_state(proc_found=False, proc_path=""):
    r"""微信"装没装"三态检测（2026-09-13 用户要求：没装微信要带他去装）。

    只看**只读**来源，三条任一命中即算装了：
      ① 注册表卸载项（HKCU/HKLM 的 ...\Uninstall\* 里 DisplayName 含 微信/WeChat/Weixin）
      ② 常见安装路径（含非默认盘：Program Files、D 盘/M 盘等根目录下的 Tencent/Weixin）
      ③ 开始菜单快捷方式（*.lnk 名含 微信/WeChat）
    返回 dict：
      state         missing / installed_not_running / running
      installed     是否装了（注册表/路径/快捷方式任一命中）
      path          找到的安装路径（可能为空）
      sources       命中的来源（给用户看"我凭什么说装了"）
      official_url  官网下载页（没装时给用户点）
      detail        给用户看的一句话
      action        建议动作：install / start / none
    """
    import glob as _glob
    out = {"state": "missing", "installed": False, "path": str(proc_path or ""),
           "sources": [], "official_url": "https://weixin.qq.com/", "detail": "", "action": "install"}
    if proc_found:
        out["state"] = "running"
        out["installed"] = True
        out["sources"].append("进程在跑")
        out["detail"] = "微信正在运行"
        out["action"] = "none"
        return out
    # ① 注册表卸载项
    try:
        import winreg
        keys = [(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")]
        for hive, sub in keys:
            try:
                k = winreg.OpenKey(hive, sub)
            except Exception:
                continue
            try:
                n = winreg.QueryInfoKey(k)[0]
                for i in range(min(n, 400)):
                    try:
                        name = winreg.EnumKey(k, i)
                        sk = winreg.OpenKey(k, name)
                        try:
                            dn = str(winreg.QueryValueEx(sk, "DisplayName")[0])
                        except Exception:
                            dn = ""
                        low = dn.lower()
                        if ("微信" in dn) or ("wechat" in low) or ("weixin" in low):
                            out["installed"] = True
                            out["sources"].append("注册表：" + dn)
                            try:
                                loc = str(winreg.QueryValueEx(sk, "InstallLocation")[0])
                                if loc and not out["path"]:
                                    out["path"] = loc
                            except Exception:
                                pass
                    except Exception:
                        continue
            finally:
                try:
                    winreg.CloseKey(k)
                except Exception:
                    pass
    except Exception:
        pass
    # ② 常见安装路径（含非默认盘）
    if not out["installed"]:
        cands = []
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                cands += [os.path.join(base, "Tencent", "WeChat", "WeChat.exe"),
                          os.path.join(base, "Tencent", "Weixin", "Weixin.exe")]
        for drive in tuple("%s:\\" % _c for _c in "CDEFGHIJKLMNOPQRSTUVWXYZ"):   # 全盘符枚举（不特指某一台机器）
            cands += [drive + "WX\\Weixin\\Weixin.exe", drive + "Tencent\\Weixin\\Weixin.exe",
                      drive + "Program Files\\Tencent\\Weixin\\Weixin.exe"]
        for c in cands:
            try:
                if os.path.isfile(c):
                    out["installed"] = True
                    out["path"] = c
                    out["sources"].append("安装路径：" + c)
                    break
            except Exception:
                continue
    # ③ 开始菜单快捷方式
    if not out["installed"]:
        try:
            ap = os.environ.get("APPDATA") or ""
            for pat in (ap + r"\Microsoft\Windows\Start Menu\Programs\**\*微信*.lnk",
                        ap + r"\Microsoft\Windows\Start Menu\Programs\**\*WeChat*.lnk",
                        ap + r"\Microsoft\Windows\Start Menu\Programs\**\*Weixin*.lnk",
                        r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\**\*微信*.lnk"):
                hits = _glob.glob(pat, recursive=True)
                if hits:
                    out["installed"] = True
                    out["sources"].append("开始菜单：" + os.path.basename(hits[0]))
                    break
        except Exception:
            pass
    if out["installed"]:
        out["state"] = "installed_not_running"
        out["detail"] = "微信已安装但没在运行（登录后本工具才能读到消息；不用重装）"
        out["action"] = "start"
    else:
        out["state"] = "missing"
        out["detail"] = ("本机没检测到微信。本工具需要**你自己的**微信客户端在本机登录后才能读消息、发消息；"
                         "我们不会替你静默安装（要下安装包 + 管理员权限），请点「打开官网下载」装好并登录，"
                         "再点「我装好了，重新检测」。")
        out["action"] = "install"
    return out

def wechat_version_info():
    """检测微信进程版本（Weixin.exe / WeChat.exe）与适配层 wechatauto 版本。

    返回 dict：
      found     是否检测到微信进程
      path      微信主程序路径
      version   微信版本号（如 4.1.13.63；读不到为空）
      adapter   wechatauto-replica 适配层版本
      supported 微信主版本是否 >= 4（4.x 全系走 UIA 无注入，均可运行）
      detail    给用户看的说明（含"微信官方更新面后异常怎么办"提示）
    """
    out = {"found": False, "path": "", "version": "", "adapter": "",
           "supported": True, "detail": ""}
    try:
        import psutil
        for p in psutil.process_iter(["name", "exe"]):
            try:
                n = str(p.info.get("name") or "").lower()
                if n in ("weixin.exe", "wechat.exe"):
                    out["found"] = True
                    out["path"] = str(p.info.get("exe") or "")
                    break
            except Exception:
                continue
    except Exception:
        pass
    if out["found"] and out["path"]:
        try:
            import win32api
            inf = win32api.GetFileVersionInfo(out["path"], "\\")
            ms, ls = win32api.GetFileVersionInfo(
                out["path"], "\\VarFileInfo\\Translation")[0]
            out["version"] = win32api.GetFileVersionInfo(
                out["path"], "\\StringFileInfo\\%04x%04x\\FileVersion" % (ms, ls))
        except Exception:
            out["version"] = ""
    try:
        import importlib.metadata as _md
        out["adapter"] = _md.version("wechatauto-replica")
    except Exception:
        out["adapter"] = ""
    v = str(out["version"]).strip()
    if out["found"] and v:
        try:
            out["supported"] = int(v.split(".")[0] or 0) >= 4
        except Exception:
            pass
    try:
        _ins = wechat_install_state(proc_found=bool(out["found"]), proc_path=out.get("path") or "")
        out["state"] = _ins["state"]
        out["installed"] = _ins["installed"]
        out["install"] = _ins
        if _ins["state"] in ("missing", "installed_not_running"):
            out["detail"] = _ins["detail"]
    except Exception as _e:
        out["state"] = "running" if out["found"] else "unknown"
        out["installed"] = bool(out["found"])
        out["install"] = None
    if not out["found"] and out.get("state") not in ("missing", "installed_not_running"):
        out["detail"] = "未检测到微信进程（微信未启动或已退出）"
    elif not v:
        out["detail"] = "微信在运行但读不到版本号（不影响使用；需要核对时查看任务管理器）"
    elif out["supported"]:
        out["detail"] = ("微信 %s · 适配层 %s。微信官方更新界面后，若发消息/引用/拍一拍"
                         "出现异常，先运行根目录「检查微信版本.bat」查看并升级适配层（不需要重装微信）。"
                         % (v, out["adapter"] or "?"))
    else:
        out["detail"] = "检测到微信版本 %s（低于 4.0），本项目只支持微信 4.x，请升级微信" % v
    return out

# ---- 「微信连不上」的逐步诊断（控制台 + 反馈诊断包共用）----

def _db_dir_hint(db) -> str:
    """库自己报的目录（账号目录优先）—— 给用户看"我在读哪个目录"。"""
    for k in ("account_dir", "db_dir"):
        try:
            v = str(getattr(db, k, "") or "")
        except Exception:
            v = ""
        if v:
            return v
    return ""


def _known_docs() -> str:
    """系统**真正的**「文档」目录 —— 不能假设是 `~\\Documents`。

    为什么（2026-09-16 用户朋友那份环境检验报告）：很多机器把「文档」重定向到 OneDrive
    （`~\\OneDrive\\Documents`）或改到别的盘，而微信默认就把聊天文件放在文档下 ⇒ 只知道
    `~\\Documents\\xwechat_files` 的人（我们）永远找不到。
    """
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        # CSIDL_PERSONAL=0x0005 · SHGFP_TYPE_CURRENT=0
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0005, None, 0, buf) == 0:
            return str(buf.value or "")
    except Exception:
        pass
    return ""


def _fixed_drives() -> list:
    """本机**固定盘**（跳过网络盘/光驱/软驱）—— 扫盘只扫这些，免得诊断被网络盘拖死。"""
    out = []
    try:
        import ctypes
        for c in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            root = c + ":\\"
            try:
                if int(ctypes.windll.kernel32.GetDriveTypeW(root)) == 3:   # DRIVE_FIXED
                    out.append(root)
            except Exception:
                continue
    except Exception:
        pass
    return out


def _db_dir_candidates(extra: str = "") -> list:
    """要找的候选目录（顺序＝优先级）：用户填的 → 家目录几处 → 系统真文档目录 → 各固定盘根。"""
    home = os.path.expanduser("~")
    cands = []
    if str(extra or "").strip():
        cands.append(_expand_path(extra))
    cands += [os.path.join(home, "xwechat_files"),
              os.path.join(home, "Documents", "xwechat_files"),
              os.path.join(home, "OneDrive", "Documents", "xwechat_files")]
    _docs = _known_docs()
    if _docs:
        cands.append(os.path.join(_docs, "xwechat_files"))
    for d in _fixed_drives():
        cands.append(os.path.join(d, "xwechat_files"))
        cands.append(os.path.join(d, "WeChat", "xwechat_files"))
    out = []
    for p in cands:
        if p and p not in out:
            out.append(p)
    return out


def _probe_db_dirs(extra: str = "") -> dict:
    """**只读**在磁盘上找微信 4.x 的数据目录（不碰驱动库、不碰窗口）。

    为什么要有它（2026-09-16 用户追问「白名单那个问题，真的只是微信版本没匹配上吗」+ 他朋友那份
    「未找到微信数据库目录」的环境检验报告）：驱动库打不开消息库时，我们只能报一句「打不开消息库」
    ——**这句话分辨不出解法完全不同的三种情况**：①目录不在默认位置（用户改了微信文件保存位置、
    或文档被重定向到 OneDrive）②目录在、但结构与驱动库对不上（版本差）③目录与库文件都在
    （权限/占用）。⇒ 独立探一遍盘，把档分开，并把**探过哪些目录**如实报出来。
    """
    tried = _db_dir_candidates(extra)
    found, hit, accounts, dbs = [], [], 0, 0
    for p in tried:
        if not os.path.isdir(p):
            continue
        found.append(p)
        _here = 0
        try:
            for acc in sorted(os.listdir(p)):
                ds = os.path.join(p, acc, "db_storage")
                if not os.path.isdir(ds):
                    continue
                accounts += 1
                for _r, _d, _f in os.walk(ds):
                    _here += sum(1 for f in _f if f.lower().endswith(".db"))
        except Exception:
            pass
        dbs += _here
        if _here:
            hit.append(p)
    return {"tried": tried, "found": found, "hit": hit, "accounts": accounts, "dbs": dbs}


def resolve_db_dir(explicit: str = "") -> tuple:
    """**决定消息库用哪个目录**：配置里填的 → 扫盘探到的（含 .db 的第一个候选）→ 空。

    为什么要有它（2026-09-16 网友 B 那份诊断截图）：他的微信把聊天文件放在 `E:\\xwechat_files`
    （我们**扫到了 43 个 .db**），可驱动库的自探测只认默认位置 ⇒ 一直"未找到微信数据库目录"、
    会话头与投递发送全不可用 —— **我们明明已经找到那个目录了，却没拿来用**。
    返回 `(目录, 来源)`，来源 ∈ `"config"` / `"scanned"` / `""`。
    """
    if str(explicit or "").strip():
        return str(explicit).strip(), "config"
    try:
        p = _probe_db_dirs("")
        if p.get("hit"):
            return str(p["hit"][0]), "scanned"
    except Exception:
        pass
    return "", ""


def _expand_path(p: str) -> str:
    """把"人填进来的路径"**展开**再用：支持 `%USERPROFILE%` 这类环境变量与 `~`，并去掉成对引号。

    为什么（2026-09-17 网友那份报告）：他在控制台「数据库目录」里填的是
    `%USERPROFILE%\\.wechatauto\\xwechat_files` —— 很多人是从文档/别的窗口里**连变量一起复制**的，
    而 Python 的 `os.path` 系列**不会**展开 `%VAR%`（那是 cmd 的语法）⇒ 这条路永远失败、只能靠
    自探测兜住，报告里就写成「来源=auto」，用户看着像"我明明填了却不生效"。展开这一步不花什么，
    却让"照着提示填进去"真的管用。只对**路径**做，不动别的配置。
    """
    s = str(p or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1].strip()
    if not s:
        return ""
    for _f in (os.path.expandvars, os.path.expanduser):
        try:
            s = _f(s)
        except Exception:
            pass
    return s


def _db_dir_variants(p: str) -> list:
    """把一个路径**规范化成驱动库能认的几种形态**（按优先级）。

    起因（2026-09-17 用户「佬」的报告，两个失败模式里的第一个）：
      他在控制台填的是 `…\<账号目录>\db_storage` ⇒ 驱动库 `_pick_account()`
      只在 `db_dir` 底下找"带 db_storage 子目录的账号目录"，填到 db_storage 这一层就**一个都找不到**
      ⇒ `RuntimeError: 未找到任何已登录账号的数据库`（磁盘上 73 个 .db 明明都在）。
    ⇒ 用户填哪一层都算对：`db_storage` ⇒ 退回它的**账号目录**，再退回**账号目录的上一级**
      （`xwechat_files`，驱动库从那儿能扫到账号）。**配置只是"我建议你用哪个"，不是"只许用哪个"**。
    """
    s = _expand_path(p)
    if not s:
        return []
    out = [s]
    try:
        base = os.path.basename(os.path.normpath(s)).lower()
        if base == "db_storage":
            acct = os.path.dirname(os.path.normpath(s))          # …\wxid_xxx_482e
            for c in (acct, os.path.dirname(acct)):              # 账号目录 → xwechat_files
                if c and c not in out:
                    out.append(c)
        elif os.path.isdir(os.path.join(s, "db_storage")):
            out.append(os.path.dirname(os.path.normpath(s)))     # 账号目录 ⇒ 再给一级上级
    except Exception:
        pass
    return [x for x in out if x]


def db_open_tries(explicit: str = "") -> list:
    """开消息库要**依次试**的 `(目录, 来源)`：配置填的 → 扫盘探到的 → 驱动库自探测（`""`）。

    为什么要有它（2026-09-17 网友那份环境检验报告）：现场是「侧栏写『微信未连接·原因未知』、
    报告里写『会话头检查失败: 未找到任何已登录账号的数据库』，可**同一个进程的逐步诊断六步全过**」。
    两条路只差**回退链**：老 `_init_db` 只在"扫盘那条"失败时才退，配置里填错一条就直接抛
    （`_src == "config"` ⇒ re-raise）⇒ 用户明明有能用的库，却被那个输入框按死。
    ⇒ 口径定死：**配置只是"我建议你用哪个"，不是"只许用哪个"**；三条路全失败才真失败，
    且每条路各自的原因都要能报出来（只报第一条会让人照着错的去查）。
    """
    out = []
    for _v in _db_dir_variants(explicit):
        out.append((_v, "config" if not out else "config↑"))
    try:
        _p, _s = resolve_db_dir("")            # 只走"扫盘"那条（不看配置）
        if _p and _p not in [d for d, _ in out]:
            out.append((_p, "scanned"))
    except Exception:
        pass
    out.append(("", "auto"))
    return out


_SAFE_DB_CACHE = {}


def _db_class():
    """拿"抗缺密钥"的 `WeChatDB` 子类（**唯一实现**，别在别处又 new 一遍原类）。

    ⚠️ 缓存**按基类**存（不是"只造一次"）：判据/诊断会把 `wechatauto.WeChatDB` 换成桩件，
    如果只记第一个结果，换过桩之后拿到的还是旧类（2026-09-18 全套判据里 attach_diagnosis
    那 8 条红就是这么来的）。
    """
    from wechatauto import WeChatDB as _Base
    if not isinstance(_Base, type):
        # 驱动库给出来的不是类（旧版本、或被判据/探针打桩成函数）⇒ **不做包装**，直接用它。
        # 包装是**增强**不是必需品；拿不到类就退让，别让增强把主路弄坏。
        try:
            log.warning("WeChatDB 不是类（%s）⇒ 跳过「抗缺密钥」包装，按原样使用",
                        type(_Base).__name__)
        except Exception:
            pass
        return _Base
    got = _SAFE_DB_CACHE.get(_Base)
    if got is not None:
        return got

    class _SafeDB(_Base):
        def _open(self, rel):
            keys = getattr(self, "_keys", None) or {}
            if rel not in keys:
                if not getattr(self, "_pm_refreshing", False):
                    self._pm_refreshing = True
                    try:
                        self._load_or_extract_keys()
                    except Exception:
                        pass
                    finally:
                        self._pm_refreshing = False
                    keys = getattr(self, "_keys", None) or {}
                if rel not in keys:
                    try:
                        f = getattr(self, "_db_files", None)
                        if isinstance(f, dict) and rel in f:
                            del f[rel]
                    except Exception:
                        pass
                    raise RuntimeError("这个库分片没有密钥，已跳过：%s" % rel)
            return _Base._open(self, rel)

    _SAFE_DB_CACHE[_Base] = _SafeDB
    return _SafeDB


def open_db(explicit: str = ""):
    """按 `db_open_tries` 依次开消息库。返回 `(db, how, errors)`。

    `db is None` ＝ 三条路全失败；`how = {"dir","src","account_dir"}`（成功那条的信息）；
    `errors = [(目录, 来源, "异常名: 信息")]`（**按试的先后**，含失败的那些）。
    唯一实现：`_init_db`（产品接入）与 `attach_diagnosis`（诊断）都走这里，别再各写一套。
    """
    errors = []
    for _d, _src in db_open_tries(explicit):
        try:
            # 放在 try 里：驱动库没装也要**照实记成一条原因**
            cls = _db_class()                       # 抗缺密钥的子类（唯一实现）
            db = cls(db_dir=_d) if _d else cls()
            return db, {"dir": _d, "src": _src,
                        "account_dir": str(getattr(db, "account_dir", "") or "")}, errors
        except Exception as e:                  # ImportError 也走这里（诊断要照实说"驱动库没装上"）
            errors.append((_d, _src, "%s: %s" % (type(e).__name__, str(e)[:140])))
    return None, {}, errors


def _db_open_verdict(p: dict, errors=None) -> str:
    """把「打不开消息库」分成三档，每档给一句**能照着做**的结论。

    ⚠️ 2026-09-17 加 `errors`：原来第三档**一律**说"那是权限或占用"。可用户「佬」那次报的是
    `KeyError: 'message\\message_1.db'`（**密钥缺口**），照着"权限"去查是白费功夫 —— 原因必须分得清。
    """
    tried = "、".join(p.get("tried") or []) or "（没探任何目录）"
    how = "在微信里看「设置 → 文件管理 → 微信文件默认保存位置」，把那个目录填进下面「数据库目录」"
    if not p.get("found"):
        return ("；磁盘上也没找到微信的数据目录（探过 %s）⇒ %s；若那儿也是空的，多半是这台微信"
                "还没登录过 / 还没收到过消息（目录还没生成）" % (tried, how))
    if not p.get("dbs"):
        return ("；磁盘上找到了数据目录（%s），但**一个 .db 都没有** ⇒ 目录结构对不上"
                "（驱动库不认识这个微信版本的数据结构），不是权限问题；也可以 %s"
                % ("、".join(p["found"]), how))
    _et = " ".join(str(e[2]) for e in (errors or []))
    if "没有密钥" in _et or "KeyError" in _et:
        return ("；磁盘上 %d 个 .db 都在（%s）⇒ 文件没问题，卡在**密钥**：微信懒创建/升级后新增的"
                "分片不在密钥表里（本程序会跳掉那些分片、其余照常读）。若整片都读不了："
                "微信必须**正在运行且已登录**（密钥从它进程内存里读），再点重试；"
                "也确认本程序与微信**同一权限级别**（微信是管理员，本程序也要是）"
                % (p.get("dbs") or 0, "、".join(p["found"])))
    return ("；磁盘上 %d 个 .db 都在（%s）⇒ 目录和文件都没问题，那是**权限或占用**："
            "微信若以管理员运行，本程序也要同样权限；也可先点重试"
            % (p.get("dbs") or 0, "、".join(p["found"])))


def _db_key_state(db) -> tuple:
    """返回 (能过页1校验的缓存密钥把数, 有主密钥吗)。

    口径与 `WeChatAdapter._usable_key_count` 一致：`master_key=None` 是常态（走缓存密钥那条路），
    真正的判据是"有没有密钥能过 SQLCipher4 页1 校验"。
    """
    n = 0
    try:
        keys = getattr(db, "_keys", None) or {}
    except Exception:
        keys = {}
    for rel in list(keys):
        try:
            if db._key_works(rel):
                n += 1
        except Exception:
            continue
    try:
        mk = bool(getattr(db, "master_key", None))
    except Exception:
        mk = False
    return n, mk


def attach_diagnosis(adapter=None, err="", db=None) -> dict:
    """「微信连不上」到底卡在哪一步 —— **只读、不动鼠标、不弹窗、不写盘**。

    为什么要有它（2026-09-16 用户反馈「微信连接不上」）：产品以前对"连不上"只有**一个是/否**
    （`wechat is not None`）⇒ 用户看到"微信未连接"却不知道原因，我们也只能靠猜、来回问。
    这里把接入拆成逐步的只读检查，每一步都给**证据**与**下一步动作**。

    步骤（能过就往下走，第一个不过的就是卡点）：
      deps 依赖装齐了吗（我们自己这半边）→ process 微信进程在跑吗 → install 装了没（仅在进程不在时）
      → version 微信版本够 4.x 吗 → db_open 消息库打得开吗 → key 数据库密钥可用吗 → self 认得出你自己的账号吗

    参数：`adapter` 能传就传（复用它的 `_db`，不重复开库）；没有就自己开一次（只读）——
    所以**只该在"接入失败/用户主动查"时调用，别放进每几秒一次的状态轮询里**。
    返回：{ok, step, reason, action, steps[{key,name,ok,detail}], err}
    """
    steps = []
    # ── 第 0 步：依赖装齐没（2026-09-16 用户问「是不是有人的微信连不上就是因为没装齐」后加）──
    #    为什么放最前：驱动库/依赖缺了，后面几步的报错会被**误读成"打不开消息库"**（异常文本里其实
    #    写着 No module named 'wechatauto'，而侧栏短原因只显示"打不开消息库"）⇒ 用户照着目录/权限
    #    查一圈白折腾。依赖是"我们自己这半边齐不齐"，先过这关再谈微信。
    _miss, _imp_err, _dep_n = [], "", 0
    try:
        import wechatauto  # noqa: F401
    except Exception as e:
        _imp_err = "%s: %s" % (type(e).__name__, str(e)[:100])
    try:
        _drows, _dok = dep_check("all")
        _dep_n = len(_drows)
        _miss = [r[0] for r in _drows if not r[3]]
    except Exception as e:
        _dok = True
        log.debug("依赖自检异常：%s", e)
    if _imp_err:
        _ddetail = ("驱动库**没装上**（%s）⇒ 这不是微信的问题：双击「一键启动」（或「一键检验」）"
                    "把依赖装齐再试" % _imp_err)
    elif _miss:
        _ddetail = ("缺 %d/%d 项依赖：%s ⇒ 双击「一键启动」（或「一键检验」）自动安装，已装的会跳过"
                    % (len(_miss), _dep_n, "、".join(_miss[:6])))
    else:
        _ddetail = "依赖齐：驱动库可导入，%d 项版本全部达标" % _dep_n
    steps.append({"key": "deps", "name": "依赖装齐",
                  "ok": bool(_dok) and not _imp_err, "detail": _ddetail})
    try:
        vi = wechat_version_info() or {}
    except Exception as e:
        vi = {"found": False, "version": "", "path": "", "detail": "版本检测异常：%s" % e}
    found = bool(vi.get("found"))
    ver = str(vi.get("version") or "")
    steps.append({"key": "process", "name": "微信进程", "ok": found,
                  "detail": ("微信在运行：%s%s" % (vi.get("path") or "（路径读不到）",
                                                  "（版本 %s）" % ver if ver else "")) if found
                            else "没找到 Weixin.exe / WeChat.exe 进程 ⇒ 微信没开（或没登录）"})
    if not found:
        try:
            _ins = vi.get("install") or wechat_install_state(False, "")
        except Exception as e:
            _ins = {"installed": False, "detail": "安装状态检测异常：%s" % e}
        steps.append({"key": "install", "name": "微信装没装", "ok": bool(_ins.get("installed")),
                      "detail": str(_ins.get("detail") or "本机没检测到微信")})
    # ── 版本步（2026-09-16 既有口径：后加）：老版本微信的**数据目录结构与 4.x 完全不同**，
    #    驱动库找不到库 ⇒ 症状恰好是"打不开消息库"，但用户看到的短原因**不会提"你的微信太老"**，
    #    于是报障只能来一句"打不开消息库"，我们这边也猜。⇒ 单独列一步，给**可执行**的结论。
    #    位置在 db_open **之前**：第一个不过的步就是卡点，版本不对时应当先说版本。
    if found:
        _sup = bool(vi.get("supported", True))
        steps.append({"key": "version", "name": "微信版本", "ok": _sup,
                      "detail": ("微信 %s，主版本 ≥4，按 4.x 的库结构找消息库" % (ver or "未知"))
                                if _sup else
                                ("检测到微信 %s（低于 4.0）：本项目只按**微信 4.x** 的数据库结构找消息库，"
                                 "3.x 的目录/库结构不同 ⇒ **这就是「打不开消息库」的原因**。"
                                 "请把微信升级到 4.x 再来（升级不影响聊天记录）。" % (ver or "未知"))})
    _db = db if db is not None else getattr(adapter, "_db", None)
    if _db is None:
        _d = ""
        try:
            _d = str((get_config().get("wechat") or {}).get("db_dir") or "")
        except Exception:
            _d = ""
        # 2026-09-16（网友 B 截图）：配置没填时，**把扫盘扫到的那个目录拿来用**。
        # 2026-09-17（网友那份检验报告）改成**三档统一走 `open_db`**：配置填错时也照样往下退
        #   ——老写法里诊断会退到自探测、产品 `_init_db` 不会 ⇒ 现场「诊断六步全过、产品连不上」。
        _db, _how, _errs = open_db(_d)
        if _db is not None:
            _src = str(_how.get("src") or "")
            _ok_how = "消息库已打开：" + (_db_dir_hint(_db) or "（库没报目录）")
            # 两句都**要能同时出现**：配置填错（改用别的）与"用的是扫盘探到的"是两件事，
            # 之前写成 if/elif ⇒ 配置填错时那句"你填的用不了"会被吞掉（自测当场抓到）。
            _bits = []
            if _d and _src != "config":
                _bits.append("**你填的目录 %s 用不了**：%s ⇒ 已自动改用 %s（来源=%s）"
                             % (_d, (_errs[0][2] if _errs else "开不了"),
                                _how.get("dir") or "驱动库自探测", _src))
            if _src == "scanned":
                _bits.append("**自动用了扫盘探到的目录** %s；建议把它填进下面「数据库目录」"
                             % _how.get("dir"))
            if _bits:
                _ok_how += "（" + "；".join(_bits) + "）"
            steps.append({"key": "db_open", "name": "打开消息库", "ok": True, "detail": _ok_how})
        else:
            _e0 = _errs[0][2] if _errs else "未知原因"
            _all = "；".join("「%s」%s" % (dd or "驱动库自探测", ee) for dd, _s, ee in _errs)
            if ("ImportError" in _e0) or ("ModuleNotFoundError" in _e0):
                # 依赖缺了 ⇒ 别把它说成"消息库打不开"（这是"没装齐"最容易被误判的一条路）
                _dd = ("驱动库没装上：%s ⇒ 这不是消息库的问题，双击「一键启动」把依赖装齐再试"
                       % _e0[:140])
            else:
                _dd = ("打不开消息库：%s（试过 %d 条路：%s）%s"
                       % (_e0[:140], len(_errs), _all, _db_open_verdict(_probe_db_dirs(_d))))
            steps.append({"key": "db_open", "name": "打开消息库", "ok": False, "detail": _dd})
            _db = None
    else:
        steps.append({"key": "db_open", "name": "打开消息库", "ok": True,
                      "detail": "消息库已打开：" + (_db_dir_hint(_db) or "（库没报目录）")})
    if _db is not None:
        n, mk = _db_key_state(_db)
        okk = bool(mk) or n > 0
        _miss_sh = []
        try:
            from . import replica_adapter as _ra2
            _miss_sh = _ra2.missing_key_shards(_db)
        except Exception:
            _miss_sh = []
        _dropped_sh = []
        try:
            # 只**读**适配层已经记下的"已跳过分片"，不在这里动分片表（诊断必须只读）
            _dropped_sh = list(getattr(adapter, "_db_dropped_shards", None) or []) if adapter is not None else []
        except Exception:
            _dropped_sh = []
        _sh_note = ("；另有 %d 个分片拿不到密钥（例：%s）⇒ 这些库读不了，会拖累读消息/会话头"
                    % (len(_miss_sh), _miss_sh[0])) if _miss_sh else ""
        _drop_note = ("；已跳过 %d 个补不到密钥的分片（例：%s）⇒ 那几个库的内容读不到，其余照常"
                      % (len(_dropped_sh), _dropped_sh[0])) if _dropped_sh else ""
        steps.append({"key": "key", "name": "数据库密钥", "ok": okk,
                      "detail": (("主密钥已取到" if mk else
                                  "主密钥为空，但有 %d 把缓存密钥能过页1校验 ⇒ 可用" % n) if okk
                                 else "拿不到能用的数据库密钥（读不到微信进程里的密钥）⇒ 消息库读不出来")
                                + _sh_note + _drop_note})
    if _db is not None:
        uid, nick = "", ""
        try:
            info = _db.get_self_info() or {}
            uid = str(info.get("username") or "")
            nick = str(info.get("nick_name") or "")
        except Exception as e:
            uid, nick = "", ""
            log.debug("诊断读 self 信息失败：%s", e)
        # 🔴 2026-09-18 改（网友报障截图：这一步显示「[卡住] 读不出你自己的账号」而其实能用）：
        #   判自己早就不止"认识自己"这一档了 —— **自家消息行号**（发送回读时登记，跟 self 信息无关）、
        #   文本回声窗、昵称+刚发过，三档都能用；而且我们发过一条之后
        #   `learn_self_from_echo()` 会从库回读里把"自己是谁"学回来。
        #   ⇒ self 读不出**不算阻塞**（否则用户只看到"卡住"就来报障），改成如实说清"现在靠什么判自己"。
        _n_local, _learned, _echo_win = 0, "", 120
        try:
            _n_local = sum(len(v or []) for v in
                           (getattr(adapter, "_self_local", None) or {}).values())
        except Exception:
            _n_local = 0
        _learned = str(getattr(adapter, "_self_wxid_src", "") or "")
        try:
            _ew = getattr(adapter, "_echo_window", None)
            _echo_win = int(_ew()) if callable(_ew) else 120
        except Exception:
            _echo_win = 120
        if uid:
            _self_detail = "认出来了：%s%s" % (nick or uid, "（%s）" % uid if nick else "")
        else:
            _self_detail = ("读不出你自己的账号（库能读但 self 信息为空）—— **不影响使用**：判自己现在走"
                            "「自家消息行号」（已记 %d 条）+「文本回声窗」（%d 秒）+「昵称 + 我刚发过」三档，"
                            "都不依赖这条 self 信息；我们自己发过一条之后还会自动从库回读里把"
                            "「自己是谁」学回来（当前来源=%s）。"
                            % (_n_local, _echo_win, _learned or "还没学到"))
        steps.append({"key": "self", "name": "认出你自己的账号",
                      "ok": True,                                    # 两态都算通过：不再阻塞
                      "detail": _self_detail})
    bad = [s for s in steps if not s["ok"]]
    step_key = bad[0]["key"] if bad else ""
    action = {"deps": "install_deps", "process": "start_wechat", "install": "install",
              "version": "upgrade_wechat",
              "db_open": "retry", "key": "relogin", "self": "retry"}.get(step_key, "none")
    # 「微信没在跑」里还要分两种：装了的＝叫他开微信；没装的＝叫他去装（别让他白找一圈）
    if step_key == "process" and any(s["key"] == "install" and not s["ok"] for s in steps):
        action = "install"
    reason = bad[0]["detail"] if bad else ("这几步都过：微信在跑、消息库打得开、密钥可用；判自己用「行号 + 回声 + 昵称」三档，不依赖 get_self_info")
    if err:
        reason = "%s（接入时抛的错：%s）" % (reason, str(err)[:140])
    return {"ok": not bad, "step": step_key, "reason": reason, "action": action,
            "steps": steps, "err": str(err or "")}


# 卡点的**短标签**：控制台侧栏一行放得下（长句放 title 提示里）。
ATTACH_STEP_LABEL = {
    "deps": "依赖没装齐",
    "process": "微信没在运行",
    "install": "没检测到微信",
    "version": "微信版本太旧（要 4.x）",
    "db_open": "打不开消息库",
    "key": "拿不到数据库密钥",
    "self": "读不出你的账号",
    "unknown": "原因未知",
}


def attach_short_reason(diag) -> str:
    """把诊断压成一行短原因（给控制台侧栏用）；拿不到/认不出的卡点一律说「原因未知」。"""
    try:
        _k = str((diag or {}).get("step") or "")
    except Exception:
        _k = ""
    return ATTACH_STEP_LABEL.get(_k) or "原因未知"


# ---- 关键依赖最低版本校验（自检/检查脚本共用）----

MIN_VER = {
    "wechatauto-replica": replica_adapter.MIN_VERSION,   # W1：最低版本由适配层定义（不再钉死某个具体版本）
    "psutil": "5.9.0",
    "uiautomation": "2.0.18",
    "comtypes": "1.4.0",
    "pywin32": "305",
    "zstandard": "0.25.0",
    "Pillow": "9.0.0",
    "requests": "2.28.2",
    "urllib3": "1.26.0",
    "cryptography": "41.0.0",
    "pyperclip": "1.8.2",
    "colorama": "0.4.6",
    "winsdk": "1.0.0b10",
    "imageio-ffmpeg": "0.4.9",
}


def _ver_tuple(v):
    import re as _re
    return tuple(int(x) for x in _re.findall(r"\d+", str(v or ""))[:4]) or (0,)


def _cmp_ver(inst, op, want) -> bool:
    """版本比较（只支持 requirements 里会出现的几种写法；认不出的一律按 '装了就算过'）。"""
    if not inst:
        return False
    if not want:
        return True
    a, b = _ver_tuple(inst), _ver_tuple(want)
    if not a or not b:
        return True
    if op == "==":
        return a == b
    if op == ">":
        return a > b
    if op == "~=":                 # 兼容版本：按 >= 处理（够用，且不会误拦）
        return a >= b
    return a >= b


def _req_rows():
    """把 `requirements.txt` 变成检查项 —— **单一事实源，别再手抄两份**（2026-09-16）。

    为什么：以前检查表是手写在 `MIN_VER` 里的 **14 项**，而 requirements.txt 有 **25 条**
    ⇒ numpy / opencv / PyAutoGUI / edge-tts / pypinyin 这些**装了没装没人管**（用户问
    「一键启动是不是真把所有要用的都装齐了」时暴露的）。现在：**MIN_VER 里的照旧**（那些是
    刻意放宽的规则，例如驱动库故意不做严格等值），**其余一律由 requirements.txt 驱动**。
    解析不出的行（注释、`-r`、环境标记）一律跳过，不猜。
    """
    out = []
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "requirements.txt")
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except Exception:
        return out
    import re as _re
    skip = set(k.lower().replace("_", "-") for k in MIN_VER)
    for ln in lines:
        s = ln.split("#")[0].strip()
        if not s or s.startswith("-"):
            continue
        m = _re.match(r"^([A-Za-z0-9_.\-]+)\s*(==|>=|~=|>)?\s*([0-9][0-9A-Za-z.\-]*)?$", s)
        if not m:
            continue
        name = m.group(1)
        if name.lower().replace("_", "-") in skip:
            continue
        out.append((name, m.group(2) or ">=", m.group(3) or ""))
    return out


def dep_check(scope="key"):
    """关键依赖版本检查。返回 (rows, all_ok)。rows: [(pkg, installed, required, ok)]

    `scope`：
      · `"key"`（**默认，原行为不变**）＝只查 `MIN_VER` 手写的那 14 项（机器级自检用）；
      · `"all"` ＝再并上 `requirements.txt` 里其余每一条 ⇒ **"东西到底装齐了没"的全量口径**
        （`setup_deps` 与接入诊断用；2026-09-16 之前这里只有 14 项、而 requirements 有 25 条
        ⇒ numpy/opencv/PyAutoGUI/edge-tts/pypinyin 装了没装没人管）。
    """
    import importlib.metadata as md
    rows = []
    for pkg, req in MIN_VER.items():
        try:
            inst = md.version(pkg)
        except Exception:
            inst = ""
        # W1：不再对 wechatauto-replica 做"严格等值"（那会把驱动库钉死在一个版本上）。
        # 统一按"≥ 最低要求"判定；高于适配层实测版本时只由适配层给"未实测"提示，不阻断。
        ok = bool(inst) and _ver_tuple(inst) >= _ver_tuple(req)
        rows.append((pkg, inst or "", req, ok))
    if scope == "all":
        for name, op, ver in _req_rows():
            try:
                inst = md.version(name)
            except Exception:
                inst = ""
            rows.append((name, inst or "", ("%s%s" % (op, ver)) if ver else op,
                         _cmp_ver(inst, op, ver)))
    return rows, all(r[3] for r in rows)
