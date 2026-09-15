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

from .config import get_config
from . import replica_adapter  # W1：驱动库（wechatauto-replica）私有 API 的唯一收口点 + 版本守卫
from . import recall as recall_mod  # 第 14 条：撤回事件识别（用于把已进上下文的消息剔除）

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


def _restore_fg(hwnd: int = 0, note: str = "", keep: bool = False) -> None:
    """把前台还回"**打开对话框之前**那一个"（优先级：传入的 hwnd → `_FG_STASH` → 当前前台）。

    **为什么必须有这一步**：用户原话——「你老是把微信切到前台，然后发文件，这不能后台做吗…那个发
    文件框本身也可以被放在后台的，它不是锁定前台的」。第一次量下来（2026-09-16 探针）：
      ① 投递点 📁（档位 `message`，`touches_cursor=False`）→ 前台**没变** ✅；
      ② 「选择文件」`#32770` 弹出时——**有时不抢前台**（前台仍是用户那个窗口，和他的观察一致），
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
    return ok


# —— 「选择文件」对话框：**不进前台**地写文件名 / 点打开（2026-09-16 用户当面定位后加）——
# 用户原话：「点击『文件』按钮没有到前台，打开文件窗口也没有到前台，但是**当你粘贴输入那一串字符的
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
        u.PostMessageW(ctypes.c_void_p(int(hwnd)), 0x0010, 0, 0)      # WM_CLOSE
        return True
    except Exception:
        return False


def _close_file_dialog(hwnd: int) -> None:
    """关掉系统「选择文件」对话框（`#32770`）——异常路径的兜底，**绝不留模态框在用户屏幕上**。"""
    try:
        import win32con
        import win32gui
        if hwnd and win32gui.IsWindow(int(hwnd)):
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
        self._send_recent = deque(maxlen=50)    # 发送去重 (chat_id, text, ts)，防回车重试发两遍
        self._poke_back_cd: dict = {}           # wxid -> 上次「系统回拍」时间戳（30 分钟冷却，防连环拍）
        self._poke_playful: dict = {}           # 日期(yyyy-mm-dd) -> [ts...] 主动皮一下记录（按天限频）
        self._init_db()

    # ── 初始化 ───────────────────────────────────────────────────────────

    def _newest_wal_mtime(self) -> float:
        """4.x 账号目录里最新的 `message_*.db-wal` 的 mtime（找不到返回 0）。

        路径：`%USERPROFILE%\\xwechat_files\\<账号目录>\\db_storage\\message\\`（也兼容 `Documents\\xwechat_files\\`）。
        """
        best = 0.0
        try:
            bases = [os.path.join(os.path.expanduser("~"), "xwechat_files"),
                     os.path.join(os.path.expanduser("~"), "Documents", "xwechat_files")]
            for b in bases:
                if not os.path.isdir(b):
                    continue
                for acc in os.listdir(b):
                    d = os.path.join(b, acc, "db_storage", "message")
                    if not os.path.isdir(d):
                        continue
                    for f in os.listdir(d):
                        if f.startswith("message_") and f.endswith(".db-wal"):
                            m = os.path.getmtime(os.path.join(d, f))
                            if m > best:
                                best = m
        except Exception:
            pass
        return best

    def db_alive(self, chat_id: str = "filehelper") -> tuple:
        """**判据可用性自检**：这个 DB 回读通道现在还能不能信？返回 `(alive, why)`。

        为什么必须（2026-09-14 由新机器 4.1.13.65 的测机报告推动）：`WeChatDB.master_key is None` 时
        `get_messages()` **不报错**、**静默返回旧数据**（filehelper 反复给出同一条 09-13 的老消息，
        轮询 63 秒不变），而同一分钟 **4.x 活库**的 `message_1.db-wal` 明明被写过 ⇒
        "投递成功"被回读判成"没发出去"（假失败）；反过来还可能"误判成功"（旧库里恰好有相似行）。
        ⇒ `alive=False` 时，调用方**必须把结论降级成「未证实」**（`V_UNVERIFIED`），不许写"发送失败"。
        """
        try:
            mk = getattr(self._db, "master_key", "?")
            if mk is None:
                return False, "master_key=None（读不到 4.x 主密钥 ⇒ 回读拿到的是旧副本/旧数据）"
            rows = self._db.get_messages(chat_id, limit=1) or []
            if not rows:
                return True, "能读该会话（当前无消息）"
            newest = float(rows[0].get("create_time") or 0)
            wal = self._newest_wal_mtime()
            if wal and newest and (wal - newest) > 300:
                return False, ("回读落后于活库：最新一行 %s / 活库 -wal %s（差 %d 秒 > 300）"
                               % (time.strftime("%H:%M:%S", time.localtime(newest)),
                                  time.strftime("%H:%M:%S", time.localtime(wal)), int(wal - newest)))
            return True, "回读通道看起来是活的"
        except Exception as e:
            return False, "判据可用性自检异常：%s: %s" % (type(e).__name__, e)

    def _init_db(self):
        try:
            from wechatauto import WeChatDB, MediaDownloader
        except ImportError as e:
            raise WeChatError("未安装 wechatauto：请先安装依赖（pip install -r requirements.txt）。%s" % e)
        db_dir = str(self.cfg.get("wechat", {}).get("db_dir") or "") or None
        self._db = WeChatDB(db_dir=db_dir) if db_dir else WeChatDB()
        info = self._db.get_self_info() or {}
        self._self_wxid = str(info.get("username") or "")
        self._self_nickname = str(info.get("nick_name") or "")
        self._nick_map = self._load_nicknames()
        self._groups = self._load_groups()
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

    # ── 读取 ─────────────────────────────────────────────────────────────

    @property
    def self_wxid(self) -> str:
        return self._self_wxid

    @property
    def self_nickname(self) -> str:
        return self._self_nickname

    def list_groups(self) -> list:
        return list(self._groups)

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
        """返回 sort_seq > since_seq 的新消息（升序），归一化后。"""
        try:
            raws = self._db.get_new_messages(wxid, since_seq, limit)
        except Exception:
            return []
        out = []
        for raw in raws:
            norm = self.normalize(raw, wxid)
            if norm:
                out.append(norm)
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

    def _is_self_echo(self, text: str) -> bool:
        """判断一条消息是不是自己刚发的（数据库回读回声）。

        微信 UIA 发出的消息会写回本地库，且群聊里 sender_id 不可靠，
        所以用"文本完全一致 + 时间窗口 30 秒"来兜底过滤，避免自问自答死循环。
        """
        t = str(text or "").strip()
        if not t:
            return False
        now = time.time()
        for sent_text, sent_ts in self._recent_sent:
            if sent_text == t and (now - sent_ts) < 30:
                return True
        return False

    def normalize(self, raw: dict, chat_id: str | None = None):
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
        #   用户报障正是这个：「我每次都是把你的话复制到微信发过去，随后你就不太能正常识别了」——
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

        # 自己发的消息跳过（避免自问自答）
        # 微信 4.x 群聊里 real_sender_id 不可靠：实测"自己"是 3，别人是 7 等（真实 wxid 在内容前缀里）
        if str(sender_id) in ("2", "3"):
            return None
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
            return None

        sender_name = self._nick_map.get(sender_wxid, sender_wxid) if sender_wxid else (
            self._nick_map.get(str(sender_id), str(sender_id)) if sender_id else "群成员")

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
        }

    # ── 发送 ─────────────────────────────────────────────────────────────

    def _get_gui(self):
        if self._gui is None:
            from wechatauto.guia import WeChatGUI
            try:
                self._gui = WeChatGUI()
            except Exception:
                # 主窗不可见（最小化/隐藏）→ 按进程枚举恢复「微信」主窗后重试一次
                try:
                    import ctypes
                    from ctypes import wintypes
                    u = ctypes.windll.user32
                    pid = ctypes.c_ulong()
                    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
                    found = []

                    def cb(h, l):
                        t = ctypes.create_unicode_buffer(256)
                        u.GetWindowTextW(h, t, 256)
                        if t.value == "微信":
                            found.append(h)
                            return False
                        return True

                    ref = CB(cb)
                    u.EnumWindows(ref, 0)
                    if found:
                        # ⛔ 2026-09-15 改：原来这里是 `ShowWindow(hwnd, 9)`（SW_RESTORE，**会激活窗口**）
                        #    + `SetForegroundWindow`（**抢前台**）——正是用户报障过的"一打开就把我的微信切出来"。
                        #    改成**不激活地**还原（不动光标、不抢前台），与三条投递链同一套实现。
                        self._ensure_main_visible(None, int(found[0]))
                        time.sleep(0.3)
                    self._gui = WeChatGUI()
                except Exception:
                    raise WeChatError("不可用：微信主窗口不可见（恢复失败）。请打开电脑微信后重试。")
            try:
                self._limit_wechat_window(self._gui)
            except Exception:
                pass
            try:
                if self._gui.desktop_available():
                    self._gui.calibrate_layout(save=True)
            except Exception:
                pass
            self._install_ui_patches(self._gui)
        return self._gui

    def _limit_wechat_window(self, gui) -> None:
        """微信窗口强制限位：把主窗 MoveWindow 到目标尺寸（1160×780，小屏自适应），
        避免 wechatauto 因"当前尺寸与校准差异过大(>15%)"而忽略布局（坐标漂移根源）。

        ⚠️ **借来的窗口要用完还**（2026-09-15 用户拍板方案 A）：这里改的是**用户的窗口**，
        以前改了就再也不还（他手动拉过的尺寸会被我们钉死，他问过"不是说要限位吗，为什么我的
        窗口还是被改了"）⇒ 现在改之前把原 rect 交给 `window_borrow` 记账，空闲一会儿自动还原；
        窗口如果被用户中途自己动过，以他为准、不还。可关：`ui.restore_window_after_use=False`。
        """
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
            if r.right - r.left != tw or r.bottom - r.top != th:
                _wb.note_original(hwnd, (r.left, r.top, r.right, r.bottom))
                _ny = max(40, r.top)
                u.MoveWindow(hwnd, r.left, _ny, tw, th, True)
                _wb.note_forced((r.left, _ny, r.left + tw, _ny + th))
                time.sleep(0.4)
                try:
                    gui.refresh()
                except Exception:
                    pass
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
        # 3) 兜底：恢复失败（受前台锁/窗口已关）→ 微信放到 Z 序底层，再不行最小化
        fg = int(user32.GetForegroundWindow() or 0)
        if fg in (gui.main_hwnd, gui.render_hwnd):
            user32.SetWindowPos(gui.main_hwnd, 1, 0, 0, 0, 0, 0x0002 | 0x0001)  # HWND_BOTTOM
            time.sleep(0.2)
            if int(user32.GetForegroundWindow() or 0) in (gui.main_hwnd, gui.render_hwnd):
                user32.ShowWindow(gui.main_hwnd, 6)  # SW_MINIMIZE

    def _send_with_foreground(self, fn, *args, **kwargs):
        """发送包装：输入全程后台（UIA SetValue 直写），需要点击/回车的
        阶段才瞬时置前，发送完成后立即把微信窗口放回后台。

        与 fg_hold 配合：批量发送时只置前一次、整批发完才恢复；
        单条发送则每条发完立即恢复。恢复失败有三级兜底：
        取消置顶 → 恢复原前台窗口（AttachThreadInput 提权）→ 放底/最小化。
        """
        self._fg_enter()
        cur0 = _cursor_pos()      # 最高目标硬判据②：L0 真实路径"用完必须把光标还回去"
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
        (1763,586)`）。这与最高目标②（不抢鼠标）③（不抢前台）直接冲突，而且是在用户机器上由**只读体检**
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
        直接走 `send_text_posted`（不动光标、不抢前台、不要求窗口可见）；
        否则（`mismatch`/`no_ref`/抓不到）**退回真实路径**——真实路径会先按名字打开会话，
        顺便把这个尺寸下的会话头学到手，于是**下一次就能走投递**。
        """
        # OCR 总时间窗（测机手册 ④）：这一笔发送链允许花在 OCR 上的总时间（超时按"判据不可用"处理）
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
                                # 授权依据＝上面的 OCR 名字确认（绿底高亮行 + 名字比对，独立于指纹闸）
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

    def chat_is_open(self, chat_id: str, gui=None, name: str = None):
        """只读：当前打开的会话是不是 chat_id。返回 (bool, 说明)。

        两个独立信号取或：①**OCR 名字**（会话列表绿底行，能答"现在是谁"但抓图偶发拿不到帧）
        ②**会话头指纹闸**（`chat_header.check`，快、稳，但需要该尺寸的参照）。
        OCR 拿不到帧时**不要直接判否**（否则会把"其实开着"误判成"没开"⇒ 白白退回真鼠标路径）。
        """
        want = name or self.display_name(chat_id) or chat_id
        got, why = self.current_chat_name(gui=gui)
        try:
            from . import chat_ocr as _co
            if got and _co.matches(got, want):
                return True, "当前会话 OCR=%r（目标 %r）· %s" % (got, want, why)
        except Exception:
            pass
        try:                                   # OCR 不可用/没抓到帧时的第二信号
            from . import chat_header as _ch
            st = _ch.check(chat_id, gui=gui)
            if st.get("status") == "ok":
                return True, "会话头指纹判 ok（OCR 这次给的是 %r：%s）" % (got, str(why)[:40])
        except Exception as _e:
            pass
        # ③ 第三条独立证据：**当前高亮行的时间**（屏幕 × DB）。
        #    ⚠️ 2026-09-16 加：高亮行是**白字绿底**，名字 OCR 读不出（E 被读成「巷」）⇒ ①②都可能拿不到
        #    正面证据，而会话**确实开着**（实测：搜索框路线把 E 切过来了、这一步却报 False，
        #    `send_to_e.py` 的闸门就此判否）。这条与 `chat_identity_ok` 的那一档同源、同口径。
        try:
            _ok3, _why3 = self._active_row_time_ok(chat_id, gui=gui)
            if _ok3:
                return True, _why3
        except Exception:
            pass
        return False, "当前会话 OCR=%r（目标 %r）· %s" % (got, want, why)

    def _ensure_main_visible(self, gui, main: int) -> bool:
        """主窗被最小化时**不激活地**还原（不动光标、不抢前台），让"抓图类判据"能工作。

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
            log.info("微信主窗原来是最小化：已**不激活**还原（不动光标、不抢前台）后继续")
            return True
        except Exception as e:
            log.warning("无激活还原最小化窗口失败：%s", e)
            return False

    def switch_chat_posted(self, chat_id: str, gui=None, name: str = None, confirm_s: float = 8.0):
        """**投递版切会话**：库的**只读** OCR 定位会话行 → **投递点击**那一行 → **OCR 按名字确认**已打开。

        为什么需要：投递（L5）不切会话；而真实路径（L0）会真点真敲、被别的窗口挡住就失败，实测 `open_chat`
        三次重试全败、`send_text` 耗 140.7s 才返回失败（2026-09-13）。这条链**全程不碰真实鼠标、不抢前台**。

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
                return False, "会话列表里（只读截图 + OCR%s）没定位到「%s」（%s%s）%s" % (
                    "，含平滑下滚 6 轮" if _scroll_fn is not None else "；**本轮没有滚动**",
                    name, flog, ("；" + _scroll_note) if _scroll_note else "",
                    ("｜现场已存 %s" % _d) if _d else "")
            pos = info["pos"]
            clicked_y = int(info["y_abs"])
            # ⛔ 一次切会话**最多一枪**：冷却期内只复核、不补点（用户口径：连点两下会把聊天框关掉）
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
            #    y 点下去就**点空**（"点击已发出但该行没变绿底"）。⇒ 点前等列表停住（纯像素判据、不调 OCR）。
            _settled = self._list_settled(gui)
            flog = str(flog) + ("｜点前列表已停稳" if _settled else "｜⚠️点前列表没等到停稳")
            # 点前的聊天区文字（用来判"切会话到底发没发生"——绿底那项判据会被帧质量骗）
            _pane0 = _co.pane_text(_chh.capture_image(gui=gui))
            # ⚠️ 会话行必须用**慢节奏**点击（2026-09-13 A/B：快节奏投渲染子窗高亮不动；
            #   悬停 300ms + 按住 150ms 高亮立刻跳到目标行）——见 input_backend.click 的注释
            ok, why = backend.click(tgt, (ox + int(pos[0]), oy + int(pos[1])), hover_ms=300, press_ms=150)
            if not ok:
                return False, "投递点击会话行失败：%s" % why
            self._pick_last[str(chat_id)] = (time.time(), clicked_y)
            # 复核（自洽证据）：**我们按名字点的那一行**现在是不是高亮行（相对判据，抗帧质量抖动）
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
            return False, ("投递点击已发出，但既没看到该行变绿底、也没能 OCR 确认「%s」（%s；最后一帧：%s）"
                           % (name, flog, str(last)[:60]))
        except Exception as e:
            return False, "投递切会话异常：%s" % e

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
            cands = []

            def _cb(h, _):
                try:
                    if win32process.GetWindowThreadProcessId(h)[1] != pid or int(h) == int(main):
                        return
                    if not win32gui.IsWindowVisible(h):
                        return
                    if win32gui.GetClassName(h) != 'Qt51514QWindowToolSaveBits':
                        return
                    x0, y0, x1, y1 = win32gui.GetWindowRect(h)
                    if x1 - x0 > 4 and y1 - y0 > 4:
                        cands.append((int(h), (int(x0), int(y0), int(x1), int(y1))))
                except Exception:
                    pass

            win32gui.EnumWindows(_cb, None)
            for h, rect in cands:
                im = _chh.shot_window(h)
                hit, why = _co.looks_like_search_popover(im)
                if hit:
                    return h, rect, im, why
                log.info("浮层候选 hwnd=%s %s 不像搜索浮层：%s", h, rect, why)
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
                return False, ("找不到搜索入口：标题带里既没读到「搜索」字样，也没识别出放大镜图标"
                               "（窗口尺寸/主题/微信版本不同？⇒ 把当前微信窗口截图发我，或改用会话列表点击）")
            variant = ent.get("variant")
            if variant == "icon":
                # —— 图标形态（本机 4.1.15.8）：点开后**搜索框是一个独立顶层窗（浮层）**，
                #    主窗渲染区里**看不到它**（实测：主窗像素只多了图标 hover 态，band_diff≈0.05）
                #    ⇒ 必须找到那个窗口、抓它自己的画面、往它投字与点击。判据不许再用"主窗像素变了"。
                #    点之前先看有没有已经开着的浮层（有就直接用，避免把自己点关）。
                pop = self._find_search_popover(main)
                if not pop:
                    backend.click(main, (ox + int(ent["x"]), oy + int(ent["y"])))
                    for _i in range(12):
                        time.sleep(0.25)
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
                    for _i in range(5):
                        time.sleep(0.6)
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
                backend.click(int(pop_hwnd), (int(prect[0]) + int(row["x"]), int(prect[1]) + int(row["y"])))
                time.sleep(1.0)
                idn, idn_why = self.chat_identity_ok(chat_id, gui=gui)
                if idn is True:
                    return True, ("搜索浮层路线成功（浮层 hwnd=%s，%s，%s，落点 %s）：%s"
                                  % (pop_hwnd, pwhy, row.get("why"), (row["x"], row["y"]), idn_why))
                _d = self._dump_fail_shot("search_identity", _chh.capture_image(gui=gui), {
                    "name": name, "row_why": row.get("why"), "落点": [row["x"], row["y"]],
                    "idn": str(idn), "idn_why": str(idn_why)[:400]})
                _closed = _close_search_popover(int(pop_hwnd))         # 复核没过也要关掉浮层
                return False, ("点了搜索浮层的「%s」行（%s，落点 %s），但内容级复核没过：%s%s"
                               % (name, row.get("why"), (row["x"], row["y"]), idn_why,
                                  ("｜现场已存 %s" % _d) if _d else "") +
                               ("｜浮层已关掉" if _closed else "｜浮层**没关掉**"))
            # —— box 形态（另一台机 / 老 UI：搜索框直接摆着）：点它 → 主窗打字 → 结果行在主窗里找
            backend.click(main, (ox + int(ent["x"]), oy + int(ent["y"])))
            time.sleep(0.45)
            ok_t, why_t = backend.send_text(main, name)
            if not ok_t:
                return False, "搜索框打字失败：%s" % why_t
            # 结果里找名字匹配的行（多抓几帧）
            info = None
            for _i in range(4):
                time.sleep(0.5)
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
            backend.click(main, (ox + int(info["pos"][0]), oy + int(info["pos"][1])))
            time.sleep(0.9)
            # 内容级复核：认得出目标会话最近的内容才算成功
            idn, idn_why = self.chat_identity_ok(chat_id, gui=gui)
            if idn is True:
                return True, "搜索框路线成功（点的是 OCR「%s」那行）：%s" % (str(info.get("name"))[:10], idn_why)
            return bool(idn is None), "点过搜索结果了，但内容复核={} （{}）".format(idn, idn_why)
        except Exception as e:
            return False, "搜索框切会话异常：%s" % e
        finally:
            # ⚠️ 这条链会抢前台（开浮层/投字/点行，实测浮层 1.63s + 主窗 8.96s 且不还）⇒ 出去一律还回去。
            #    成功路径也照还：切完会话不需要占着前台。
            try:
                _restore_fg_until("切会话·搜索路线", timeout=2.5, keep=False)
            except Exception:
                pass

    def send_text_posted(self, text: str, chat_id: str = "filehelper", wait_s: float = 15.0,
                         allow_no_ref: bool = False):
        """**投递发送**（L5，2026-09-13 实测过的那条链）：投递 WM_CHAR 打字 + 投递点「发送」按钮。

        与 `send_text` 的区别（也是它的适用边界）：
          · 全程**不动光标、不抢前台、不要求窗口可见**；`GetCursorPos` 前后不变；
          · **前提＝目标会话已经打开** —— 投递不负责切会话（切会话的投递版仍未取证）。
            调用方要么自己确认会话已打开，要么先用别的方式切过去。
        成功判据：**只认 DB 回读**（轮询到新行且内容含本段文本），不信 GUI 返回值。

        参考实测：投递打字 + 投递点「发送」（3/3、DB 回读命中）。
        """
        # OCR 总时间窗（测机手册 ④）：这一笔发送链的 OCR 总预算（超时按"判据不可用"处理）
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
            self._ensure_main_visible(gui, main)   # 最小化 ⇒ 先不激活地还原（发送链的会话头判据要抓图）
            if not gui.render_rect:
                gui._update_render_rect()
            # 会话头校验（防发错会话）：投递**不会切会话** ⇒ 必须有"当前会话＝目标会话"的**正面证据**才准发。
            # ⛔ 2026-09-13 实测事故（检验包自测暴露）：no_ref（当前尺寸没参照）时照发 ⇒ 文本被打进
            #    **当时打开的另一个会话**并真的发了出去（发给了联系人 E），而 DB 里查目标会话自然查不到，
            #    表观症状只是"发送未生效"，**发错会话这件事被完全掩盖**。
            # ⇒ 现在：mismatch / no_ref / no_capture **一律拒绝投递**；`allow_no_ref=True` 只在调用方
            #    自己已经确认过"目标会话就是当前打开的"时使用（例如已用真实路径打开过）。
            try:
                from . import chat_header as _ch
                _st = _ch.check(chat_id)
                if _st["status"] == "mismatch":
                    return False, "会话头不匹配，拒绝投递（防发错会话）：%s" % _st["note"]
                if _st["status"] in ("no_ref", "no_capture") and not allow_no_ref:
                    return False, ("当前尺寸没有目标会话的参照（%s）⇒ 无法确认打开的会话就是目标会话，"
                                   "拒绝投递（先按名字打开一次该会话学会参照，或走 send_text 的真实路径兜底）"
                                   % _st["status"])
                if _st["status"] in ("no_ref", "no_capture"):
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
            ok, why = backend.send_text(main, text)
            if not ok:
                return False, "投递打字失败：%s" % why
            time.sleep(0.45)
            # 「发送」按钮：渲染区比例 (0.932, 0.945)（1160×900 实测；按钮不用焦点，回车才要）
            send_pt = (int(r[0]) + int(rw * 0.932), int(r[1]) + int(rh * 0.945))
            ok2, why2 = backend.click(main, send_pt)
            if not ok2:
                return False, "投递点发送失败：%s" % why2
            deadline = time.time() + max(3.0, float(wait_s))
            while time.time() < deadline:
                time.sleep(0.8)
                rows = _rows()
                if rows and str(rows[0].get("local_id")) != base_sig:
                    head = rows[0]
                    if str(text)[:20] in str(head.get("content") or ""):
                        # DB 回读确认成功 ⇒ 自动补一条"当前窗口尺寸"下的会话头参照
                        # （尺寸变了以后不用人工重标；下次同尺寸就能真正校验）
                        self._learn_chat_header(chat_id, gui=gui)
                        return V_OK, "投递发送成功（DB 回读 local_id=%s type=%s）" % (
                            head.get("local_id"), head.get("type"))
                    return V_OK, "DB 有新行但内容与本次不一致（local_id=%s，可能上一条刚写库）" % head.get("local_id")
            # ⚠️ 判据不可用 ≠ 发送失败（2026-09-14 测机报告：4.1.13.65 上投递其实发出去了，但回读通道失效）
            _alive, _why_alive = self.db_alive(chat_id)
            if not _alive:
                return V_UNVERIFIED, ("已投递，但**判据不可用**、无法证实是否发出：%s（投递链路本身没报错）"
                                      % _why_alive)
            return V_NOT_SENT, "已投递但 %ds 内 DB 没等到新行（发送未生效）" % int(wait_s)
        except Exception as e:
            return False, str(e)

    def send_image_posted(self, chat_id: str, local_path: str, wait_s: float = 60.0):
        """**投递发图**（L5）：剪贴板放图（CF_DIB）→ 投递 **Ctrl+V 组合键给渲染子窗** → 投递点「发送」→ **DB 回读认图片**。

        实测（2026-09-13）：①**必须投给渲染子窗 `MMUIRenderSubWindowHW`**（投主窗完全无效）；
        ②图片消息的 **DB 落库延迟可达 30~60s**（文本只要 2~3s）⇒ 轮询窗口默认给到 60s，
        否则会把"其实发出去了"误判成"没生效"（本轮就因此把一次成功误判成失败）。
        全程**不动光标、不抢前台**；成功判据**只认 DB 回读**。
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
            child = int(getattr(gui, "render_hwnd", 0) or 0) or main      # 粘贴/按键要打渲染子窗
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

            ok_cb, why_cb = _cb.set_image(local_path)
            if not ok_cb:
                return False, "放剪贴板失败：%s" % why_cb
            ok_p, why_p = backend.keys(child, [ib.VK_CONTROL, ib.VK_V])
            if not ok_p:
                return False, "投递 Ctrl+V 失败：%s" % why_p
            time.sleep(1.4)                       # 等缩略图渲染进输入框
            send_pt = (int(r[0]) + int(rw * 0.932), int(r[1]) + int(rh * 0.945))
            ok_c, why_c = backend.click(main, send_pt)
            if not ok_c:
                return False, "投递点发送失败：%s" % why_c
            deadline = time.time() + max(10.0, float(wait_s))
            while time.time() < deadline:
                time.sleep(1.2)
                rows = _rows()
                if rows:
                    top = rows[0]
                    try:
                        new_id = int(top.get("local_id") or 0)
                    except Exception:
                        new_id = 0
                    if new_id > base_id:
                        return V_OK, "投递发图成功（DB 回读 local_id=%s type=%s）" % (
                            top.get("local_id"), top.get("type_name") or top.get("type"))
            _alive2, _why_alive2 = self.db_alive(chat_id)
            if not _alive2:
                return V_UNVERIFIED, ("已投递粘贴并点了发送，但**判据不可用**、无法证实：%s" % _why_alive2)
            return V_NOT_SENT, "已投递粘贴并点了发送，但 %ds 内 DB 没等到新行（发图未生效）" % int(wait_s)
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
                return False, _g["reason"]
        except Exception:
            pass
        # OCR 总时间窗（测机手册 ④）：这一笔发送链的 OCR 总预算（超时按"判据不可用"处理）
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
                # 人工确认通道：**只在用户当面确认「当前开着的就是目标会话」时用**
                # （会话标题是浅灰细字、本机 OCR 读不出，E 这种会话自动闸门可能永远判不过）
                log.warning("发文件：用户当面确认当前会话＝目标会话，跳过自动会话闸（自动判据：%s）", why_open)
            # ⛔ 内容级身份闸（2026-09-13 发错会话事故后加）：**名字判据会骗人**——
            #    群聊行的预览带发言人前缀（`E: 提交信息…`），会被当成"会话名＝E"从而点进那个群。
            idn, idn_why = self.chat_identity_ok(chat_id, gui=gui)
            if idn is False:
                # ⛔ 默认 fail-closed；但 `confirm_open=True`（用户在当面确认"当前开着的就是目标会话"）
                #   必须能压过**内容级**的否定 —— 这正是这个参数存在的理由（2026-09-14 实测：E 的会话
                #   明明开着，可它最近几条都是文件卡、我们的针（短 token）不在视口里 ⇒ 内容档一路落空、
                #   返回 False ⇒ 老写法**无条件拒绝**，人工确认通道根本走不到，等于形同虚设）。
                #   放行时**留痕**：日志 + 返回值里带上判据原文，事后能问责。
                if not confirm_open:
                    return False, "⛔ 内容核对不通过，拒绝发送（防发错会话）：%s" % idn_why
                log.warning("发文件：内容级核对判否，但用户当面确认当前会话＝目标会话 ⇒ 按人工确认放行"
                            "（判据原文：%s）", idn_why)
                idn_why = "%s（已按用户当面确认放行）" % idn_why
            if idn is None and not confirm_open:
                return False, "拿不到内容级证据，拒绝发送：%s（确已人工确认可用 confirm_open）" % idn_why
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
            #    ②**从实测图标行取坐标**（用户报"投递是成功的，但老点错位置，不是收藏就是截图"
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
                            try:                      # 记账只在**DB 回读确认**之后
                                self._repeat_guard(chat_id, local_path, note=True)
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

    def chat_identity_ok(self, chat_id: str, gui=None):
        """**内容级**身份核对：当前聊天区里应看得到目标会话最近若干条文本里的**任意一条**。

        返回 `(True/False/None, 说明)`；`None`＝拿不到可比对的内容（调用方按"没有正面证据"处理）。
        ⚠️ 为什么不能只信名字（2026-09-13 发错会话事故）：群聊行的预览带**发言人前缀**（`E: 提交信息…`），
        被当成"会话名＝E"后点进了那个群 ⇒ 发送前的最后一道闸必须是**内容**，不是名字。
        ⚠️ 为什么要多条指纹：见 `recent_texts()` 的注释（单条指纹会因为"最新那条文本不在视口里"误判）。
        """
        needles = self.recent_texts(chat_id)
        if not needles:
            return None, "目标会话最近几条里没有可用作文本的比对内容（都是图片/文件？）"
        try:
            from . import chat_ocr as _co
            _tok = _co.begin_window(_co.SEND_WINDOW_S)     # 内容级核对整段共用 OCR 总预算（④）
            pane = _co.pane_text(_co.capture_best(gui=gui or self._get_gui(), frames=3), limit=400)
            if _co.budget_out(_tok):
                # OCR 预算用尽 ⇒ 这一条**判据不可用**：按"拿不到证据"返回（调用方 fail-closed，不发）
                return None, "OCR 预算用尽（判据不可用）：内容级核对没跑完，按「拿不到证据」处理"
            pane_n = _co.norm_alnum(pane)
            # ⛔ 2026-09-14 修（⑤ 重发实测）：**聊天区一个字都读不到**时，老实现一路走到最后返回 `False`
            #   ＝"核对不通过：当前开着的很可能不是目标会话" —— 可我们**根本没拿到证据**，这是把
            #   "判据不可用"说成了"证据说不是"（同 ④ 的教训）。后果很实在：`send_file_posted` 里
            #   `idn is False` 是**无条件拒绝**的（连 `confirm_open` 人工确认通道都走不到）⇒ 屏幕一
            #   读不出字，用户当面确认"就是 E 的会话"也发不出去。⇒ 读不到就如实返回 `None`（判据不可用）。
            if not pane_n:
                return None, ("聊天区一个字都没读到（判据不可用，不是「不是这个会话」）："
                              "抓图可能有遮挡/在滚动中，或这一屏确实没有文字；用户当面确认可走 confirm_open")
            for nd in needles:
                nn = _co.norm_alnum(nd)
                if len(nn) >= 6:
                    if _co.content_match(pane, nd):
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
        import ctypes
        u = ctypes.windll.user32
        u.SetCursorPos(int(x), int(y))
        u.mouse_event(0x0002, 0, 0, 0, 0)
        u.mouse_event(0x0004, 0, 0, 0, 0)

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

    # ── 朋友圈：投递档基础设施（不动光标 / 不抢前台 / 不要求可见）────────────
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
            return 1.0     # 量不出来 ⇒ 按"判据不可用"处理（调用方会拒绝动手）

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
            #   用户原话：「我看我点了那个程序鼠标测试，我点了朋友圈，它在乱点我的头像、联系人、收藏，
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
        """打开朋友圈：**投递档优先**（不动光标、不抢前台、不要求可见）。

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
        """确保朋友圈窗口在前台（枚举到即激活）。"""
        from . import wechat_ui
        gui = self._get_gui()
        for hwnd, title, rect in wechat_ui._wechat_subwindows(gui.main_hwnd):
            if "朋友圈" in title or title == "Weixin":
                import ctypes
                ctypes.windll.user32.SetForegroundWindow(int(hwnd))
                return rect
        return None

    def moments_scroll(self, direction: int = 1, times: int = 1) -> tuple:
        """滚动朋友圈：**投递档优先**（不动光标、不抢前台）；失败才退回真鼠标档并写明。"""
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
            user32.SetCursorPos(cx, cy)
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
        try:
            cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
            ctypes.windll.user32.SetCursorPos(cx, cy)
            for _ in range(8):
                ctypes.windll.user32.mouse_event(0x0800, 0, 0, 900, 0)
                time.sleep(0.12)
            time.sleep(0.8)
        except Exception:
            pass
        bx, by = int(rect[0] + (rect[2] - rect[0]) * 0.89), int(rect[1] + (rect[3] - rect[1]) * 0.938)
        subs = tuple(int(h) for h, t, r in _wu._wechat_subwindows(gui.main_hwnd))
        # 单击蓝点（heal=False：不抖动；绝不再点第二下，否则菜单取消）
        ctypes.windll.user32.SetCursorPos(bx, by)
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
            # 相机位置：朋友圈窗口左上角图标排（🔔 铃铛 | 📷 相机 | 🔄 刷新）。
            # 实测弹窗(682×979)：铃铛≈(57,38)、相机≈(105,38)、刷新≈(153,38)。
            # 多形态自适应：弹窗=左上角；内嵌（主窗右侧内容区）=顶部右侧工具栏；多次位置候选 + OCR 确认输入区出现。
            _w, _h = max(1, int(rect[2] - rect[0])), max(1, int(rect[3] - rect[1]))
            user32 = ctypes.windll.user32
            _cam_candidates = [(0.154, 0.039), (0.86, 0.045), (0.72, 0.04), (0.60, 0.04)]
            _cam_hit = False
            for _px, _py in _cam_candidates:
                cam_x, cam_y = int(rect[0] + _w * _px), int(rect[1] + _h * _py)
                user32.SetCursorPos(cam_x, cam_y)
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
                    user32.SetCursorPos(int(rect[0] + _w * 0.5), int(rect[1] + _h * 0.5))
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
            user32.SetCursorPos(_cx, _cy)
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

        不用 gui.ensure_visible()：它的"桌面可用"检测数白色像素占比，
        深色主题下永远返回 False（实测误报"锁屏/不可见"）。
        用 agent.ui_adapt：DPI 感知、TabTip 手写画布等系统叠加层、普通遮挡窗，
        各种电脑（不同缩放/多显示器）都能保持一致。
        """
        try:
            from . import ui_adapt
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

    def _click(self, gui, rel_x: int, rel_y: int, right: bool = False) -> tuple:
        """统一点击入口：换 DPI 空间 + 校验点击点属于微信 + wx_click。

        返回 (ok, 消息)。
        """
        try:
            from . import ui_adapt
            return ui_adapt.click(gui, int(rel_x), int(rel_y), right=right)
        except Exception as e:
            return False, str(e)

    def _right_click_menu(self, gui, rel_x: int, rel_y: int, label: str, delay: float = 0.7) -> bool:
        """在相对坐标 (rel_x, rel_y) 处右键，OCR 弹出菜单，点含 label 的项。

        优先 UIA 菜单树（微信 4.x 右键菜单热激活后物化为 mmui::XMenuView，
        用 Invoke 点击最可靠、无坐标漂移）；OCR 兜底并做「真菜单」过滤：
        菜单项是小字条（高 < 46）、位于光标右下方附近——防止把聊天文本里
        的「拍一拍」误当成菜单项。
        """
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

    def _find_avatar_center(self, box):
        """运行时定位头像：在给定屏幕像素矩形内找「彩色饱和像素斑块」中心。

        真人头像是有颜色的图片，气泡/名字/背景都是灰白/黑（低饱和度），
        用 max(R,G,B)-min(R,G,B) > 28 筛彩色像素完全能区分（深浅色主题通用）。
        返回屏幕坐标 (x, y) 或 None。
        """
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab(bbox=box)
            px = img.convert("RGB").load()
            w, h = img.size
            xs, ys = [], []
            step_y = max(1, h // 120)
            for y in range(0, h, step_y):
                for x in range(w):
                    r, g, b = px[x, y]
                    if max(r, g, b) - min(r, g, b) > 28:  # 彩色饱和像素
                        xs.append(x)
                        ys.append(y)
            if len(xs) < 40:  # 太少视为误检（如气泡彩字/残影）
                return None
            xs.sort()
            ys.sort()
            return box[0] + int(xs[len(xs) // 2]), box[1] + int(ys[len(ys) // 2])
        except Exception:
            return None

    def _send_poke_locate(self, gui, target_name: str, db_text: str, scroll: bool = True, self_side: bool = False):
        """定位目标头像（渲染相对坐标），返回 (ax, ay, score) 或 None。
        self_side=True：要找的是"自己发的消息"（撤回/删自己），它在**右侧**，不剔除右侧项。

        路径优先级：① UIA 行匹配（精确/模糊）→ 彩色头像检测；② UIA 行固定偏移；
        ③ OCR 相似度匹配 → 彩色头像检测；④ 左侧消息块兜底。
        返回第三位 score 供日志说明路径（1.0=UIA 精确行 / 0.8=彩色检测 / 0.0=兜底）。
        scroll=False 只在当前视口找（先滚到最新再调用，用于引用定位避免翻页漂移）。
        """
        # 1) UIA 行匹配（可向上翻页查找）+ 彩色头像检测
        row = self._uia_target_row_rect(gui, db_text, scroll=scroll)
        if row:
            av = self._find_avatar_center((row[0], row[1], row[0] + 130, row[3]))
            if av:
                return av[0] - gui.origin_x, av[1] - gui.origin_y, 0.8
            return row[0] + 54 - gui.origin_x, row[1] + 48 - gui.origin_y, 1.0
        # 2) OCR 相似度匹配
        items = []
        try:
            box = gui.get_input_box()
            top = max(80, box[1] - 620) if box else 80
            items = gui.ocr((gui.right_pane_left, top, gui.render_w, box[1]))
        except Exception:
            return None
        mid_x = (gui.right_pane_left + gui.render_w) // 2
        pane_w = max(1, gui.render_w - gui.right_pane_left)
        # 头像列中心 ≈ 会话区左缘 + 18.5% 会话区宽（实测：深色 197px、浅色 201px，取 0.185；头像 45~50px，容差 ±10px）
        ax = gui.right_pane_left + int(pane_w * 0.185)

        # 剔除垃圾项（侧栏碎片/小残片）；普通定位找左侧（对方），self_side 撤回自己在右侧
        if self_side:
            items = [it for it in items if it[3] > 30 and mid_x < it[1] < gui.render_w]
        else:
            items = [it for it in items if it[3] > 30 and (gui.right_pane_left + 60) < it[1] < mid_x]

        db_norm = self._norm_ocr(db_text)
        best = None
        best_score = 0.0
        for t, x, y, w, h in items:
            tn = self._norm_ocr(t)
            if not tn:
                continue
            score = _seq_ratio(tn, db_norm[:120] if db_norm else "")
            if score > 0.5 and score > best_score:
                best_score = score
                best = (x, y, w, h)
        if best and db_norm:
            # 彩色头像检测：在行带上找（行带取气泡左缘向左 130px、首行上下 60px）
            bx, by, bw, bh = best
            av = self._find_avatar_center((gui.origin_x + max(gui.right_pane_left + 40, bx - 140),
                                           gui.origin_y + by - 55,
                                           gui.origin_x + bx, gui.origin_y + by + 75))
            if av:
                return av[0] - gui.origin_x, av[1] - gui.origin_y, 0.6
            return ax, best[1] - 32, best_score

        # 3) 兜底：左侧可见消息的最后一条（文本块第一行）
        if items:
            items.sort(key=lambda b: b[1])
            last = items[-1]
            first_y = last[1]
            for i in range(len(items) - 1, 0, -1):
                if last[1] - items[i - 1][1] > 36:
                    first_y = items[i][1]
                    break
            else:
                first_y = items[0][1]
            return ax, first_y - 32, 0.0
        return None

    def send_poke(self, chat_id: str, target_name: str, target_id: str = "", dbg: list | None = None):
        """拍一拍某位成员（串行锁内执行）：右键头像 → 菜单选「拍一拍」；头像未显示时改走气泡菜单。
        返回 (ok, message)；验证失败如实返回，不假报。"""
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
        # 后台能力矩阵：拍一拍＝真鼠标档（要右键头像/气泡再点菜单）。开了「只走后台」就跳过。
        if self._background_only():
            from . import bg_status as _bg
            return False, _bg.background_only_reason("拍一拍")
        def _d(msg):
            if dbg is not None:
                dbg.append(msg)
        try:
            gui = self._get_gui()
            rec = gui.render_rect
            _d("1) 微信窗口：%s 可见=%s" % (
                rec, _user32_is_visible(gui.main_hwnd)))
            if not self._ensure_foreground(gui):
                return False, "微信窗口未找到或已退出，无法操作"
            _d("2) 已清理遮挡层并把微信置前")
            if not gui.open_chat(group := self.group_name(chat_id)):
                return False, "打开会话失败"
            _d("3) 已打开会话「%s」" % group)
            time.sleep(0.9)
            base_seq = self.latest_seq(chat_id)
            db_text = self._last_target_text(chat_id, target_id) if target_id else ""
            _d("4) 目标最近消息（数据库后 60 条内匹配）：%r" % (db_text[:40] or "(未找到，用空文本)"))
            located = self._send_poke_locate(gui, target_name, db_text)
            if not located:
                _d("5) ✘ 定位失败：未找到「%s」的头像位置（UIA 行匹配/OCR 相似度/左侧消息兜底都失败）" % target_name)
                return False, ("未在可见消息里定位到「%s」的头像；让对方先发条消息再试" % target_name)
            ax, ay, score = located
            if score >= 1.0:
                path = "UIA 行 + 固定偏移"
            elif score >= 0.8:
                path = "UIA 行 + 彩色头像检测"
            elif score >= 0.6:
                path = "OCR 匹配 + 彩色头像检测"
            elif score > 0.0:
                path = "OCR 相似度匹配"
            else:
                path = "左侧消息兜底"
            _d("5) 头像位置：渲染坐标 (%d,%d)，定位方式：%s" % (ax, ay, path))
            # 头像未显示（连续消息折叠 / 无彩色斑块）→ 改走「气泡」路径：
            # 消息右键菜单同样含「拍一拍」，拍的是该消息的发送者（安全）
            if score < 0.6:
                px, py = self._bubble_point(gui, ax, ay, db_text if db_text else target_name)
                _d("   → 未检测到彩色头像（可能是连续消息未显示头像），改为右键气泡 (%d,%d) 里的「拍一拍」" % (px, py))
                menu_hit = self._right_click_menu(gui, px, py, "拍一拍")
            else:
                _d("6) 移动到 (%d,%d) 并右键…（光标位置与命中窗口将在成功/失败时回读）" % (
                    gui.origin_x + ax, gui.origin_y + ay))
                menu_hit = self._right_click_menu(gui, ax, ay, "拍一拍")
            _d("   光标最终位置：%s（右键后）" % (_cursor_pos(),))
            self._scroll_to_bottom(gui)  # 翻过页的话把聊天滚回最新，不影响用户
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
            if not self._ensure_foreground(gui):
                return False, "微信窗口未找到或已退出，无法操作"
            if not gui.open_chat(self.group_name(chat_id)):
                return False, "打开会话失败"
            time.sleep(0.9)
            db_text = self._last_target_text(chat_id, target_id) if target_id else ""
            located = self._send_poke_locate(gui, target_name, db_text)
            if not located:
                _d("✘ 定位失败：未找到「%s」的头像位置" % target_name)
                return False, "未定位到头像，请让对方先发条消息"
            ax, ay, score = located
            _d("头像位置：渲染坐标 (%d,%d)" % (ax, ay))
            # 头像未显示（连续消息折叠）→ 改右键气泡（消息菜单同样含「拍一拍」）
            if score < 0.6:
                px, py = self._bubble_point(gui, ax, ay, db_text if db_text else target_name)
                _d("   → 未检测到彩色头像（连续消息折叠），改右键气泡 (%d,%d)" % (px, py))
                ax, ay = px, py
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
                    if "拍拍" in txt:
                        return True, "已拍一拍「%s」（已验证：数据库中新增拍一拍事件）" % target_name
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
                try:
                    items = gui.ocr_zoomed(region, scale=2)
                except Exception:
                    items = gui.ocr(region)
                for text, *_ in items:
                    tn = self._norm_ocr(text)
                    if "拍了拍" in tn or ("拍拍" in tn and ("你" in tn or "我" in tn[:4])):
                        return True, "已拍一拍「%s」（已验证：界面出现「你拍了拍…」提示）" % target_name
        except Exception:
            pass
        return False, "已点「拍一拍」但数据库与界面都未验证到（可能没点中/没拍到，如实告诉对方这次没拍上，稍后再试）"

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
        # 后台能力矩阵：引用＝真鼠标档（右键气泡 → 菜单「引用」）。开了「只走后台」就跳过。
        if self._background_only():
            from . import bg_status as _bg
            return False, _bg.background_only_reason("引用消息")
        try:
            gui = self._get_gui()
            group = self.group_name(chat_id)
            if not self._ensure_foreground(gui):
                return False, "微信窗口未找到或已退出，无法操作"
            if not gui.open_chat(group):
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
            # 回退 1：固定矩形 fast 路径
            box = (gui.right_pane_left + 4, max(60, gui.render_h - 250),
                   gui.render_w - 4, gui.render_h - 60)
            ok_in = gui.input_text(text, box=box, fast=True)
            if not ok_in:
                ok_in = gui.input_text(text)  # 回退完整探测路径
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

    def message_menu(self, chat_id: str, text: str, sender_name: str = "", label: str = "收藏") -> tuple:
        """对一条消息执行右键菜单操作。text/sender_name 用于定位（数据库归一化消息）。
        label: 收藏 / 撤回 / 删除 / 置顶 / 转发 / 多选…（失败返回 (False, 原因)，绝不乱点）。"""
        with self._send_lock:
            try:
                gui = self._get_gui()
                if not chat_id:
                    return False, "未指定会话（chat_id 为空），不执行任何操作（防止误点搜索框/其它会话）"
                group = self.group_name(chat_id) or chat_id
                if not self._ensure_foreground(gui):
                    return False, "微信窗口未找到或已退出，无法操作"
                if not gui.open_chat(group):
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
                # 安全关闭未选中的菜单
                try:
                    uia = gui._get_uia()
                    if uia is not None and uia._uia_find_menu_item("转发") is not None:
                        gui._input.key(0x1B)
                except Exception:
                    pass
                self._scroll_to_bottom(gui)
                return False, "右键菜单里没找到「%s」（目标消息可能不在可见区或已是自己刚发的）" % label
            except Exception as e:
                return False, str(e)

    def collect_message(self, chat_id: str, text: str = "", sender_name: str = "") -> tuple:
        """收藏一条消息。"""
        return self.message_menu(chat_id, text, sender_name, "收藏")

    def recall_message(self, chat_id: str, text: str = "", sender_name: str = "") -> tuple:
        """撤回自己最近发的一条消息（需 2 分钟内）。"""
        return self.message_menu(chat_id, text, sender_name, "撤回")

    def collect_emoji_native(self, chat_id: str, text: str = "", sender_name: str = "") -> tuple:
        """真实路径收藏：右键表情气泡 → 菜单「添加到表情」→ 存入微信表情库。
        与 collect_emoji（本地截图收藏夹）并存：本方法走真微信操作。"""
        return self.message_menu(chat_id, text, sender_name, "添加到表情")

    # ── 微信表情面板（真实路径发收藏表情）────────────────────────────
    # 路径：点输入栏左下角「笑脸」→ 弹出面板 → 底部右侧「爱心」（收藏的表情）
    # → 点目标表情 → 发送。全部用相对输入栏坐标 + ui_adapt（DPI 无关）+ 验证。

    def _search_group(self, gui, name: str) -> bool:
        """搜索群聊进群：官方 open_chat（UIA 优先+侧栏 OCR 点击+搜索兜底），重试 2 次。"""
        _st0 = time.time()
        for i in range(1):                     # 只试 1 次进群（之前重试 2 次，open_chat 每次可能 5~15s，重试白白翻倍）
            try:
                if gui.open_chat(name):
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
        """
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

    def emoji_panel_open(self, group_name: str = "") -> tuple:
        """点笑脸打开表情面板（恒定方案：先搜索群名进入会话——不管画面空不空）。返回 (ok, msg)。"""
        try:
            gui = self._get_gui()
            from . import ui_adapt
            if not ui_adapt.prepare_screen(gui):
                return False, "屏幕预检失败"
            # ① 恒用「搜索群名进入」（wechatauto open_chat 内部即搜索点击；画面空/非空都走）
            if group_name:
                # 进群=手写搜索（点搜索框→粘贴群名→点弹出的群聊）；失败才兜底 wechatauto open_chat
                if not self._search_group(gui, group_name):
                    try:
                        gui.open_chat(group_name)
                    except Exception:
                        pass
                time.sleep(0.25)   # 进群后立刻移向笑脸（原 0.40 压缩；仍够会话切稳）
            else:
                # 无会话名：自动开第一个群（只探测一次，避免 UIA 连续失败重试）
                try:
                    for g in self.list_groups():
                        if g.get("name"):
                            gui.open_chat(g["name"])
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

    def emoji_panel_send(self, index: int = 0) -> tuple:
        """发送收藏表情：布局自适应 —— 旧 UI：点爱心（面板标签行）→ 点第 index 格；
        新 UI：面板默认即收藏视图（底栏工具栏），直接点第 index 格。
        网格几何按面板自身矩形推算（5 列），随 DPI/分辨率/面板尺寸自适应。"""
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
            # ① 旧 UI 才点爱心；新 UI（底部有工具栏图标）默认即收藏视图
            if not self._emoji_bottom_bar(px0, py0, pw, ph):
                old_x = px0 + int(pw * 0.171)
                old_y = py0 + int(ph * 0.804 - 13)
                ui_adapt.click(gui, old_x - rx, old_y - ry, heal=False)
                time.sleep(0.6)
            # ② 网格（5 列，相对面板）：第一列中心 0.10W，列距 0.19W；行距 0.14H
            cols = 5
            col = index % cols
            row = index // cols
            ROWS = 5   # 一屏完整行（面板约 6~7 行，预留）
            if row >= ROWS:
                return False, "收藏较多（%d 个）超出面板首屏，请先发送靠前的收藏" % (index + 1)
            grid_x = px0 + int(pw * (0.10 + col * 0.19))
            grid_y = py0 + int(ph * (0.085 + row * 0.14))
            ok, why = ui_adapt.click(gui, grid_x - rx, grid_y - ry, heal=False)
            if not ok:
                return False, "点表情失败：%s" % why
            time.sleep(1.0)
            return True, "已点击第 %d 个收藏表情（点击即发送）" % (index + 1)
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
        for drive in ("C:\\", "D:\\", "E:\\", "F:\\", "M:\\"):
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


def dep_check():
    """关键依赖版本检查。返回 (rows, all_ok)。rows: [(pkg, installed, required, ok)]"""
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
    return rows, all(r[3] for r in rows)
