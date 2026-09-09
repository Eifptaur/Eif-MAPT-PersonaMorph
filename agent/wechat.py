# -*- coding: utf-8 -*-
"""wechatauto 适配层：读消息 / 发消息 / 下载图片。

对 wechatauto 的 WeChatDB / WeChatGUI / MediaDownloader 做统一封装，
把微信原始消息归一化成 wx-agent 内部结构，屏蔽底层差异。
"""
from __future__ import annotations

import base64
import html
import os
import random
import re
import threading
import time
from collections import deque

from .config import get_config

# 调试开关：WX_DEBUG=1 时输出分步计时/调参日志（平时完全静默，不写 _scratch）
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
        mapping = {}
        try:
            for rel, path, _ in self._db._db_files:
                if os.path.basename(path) != "contact.db":
                    continue
                conn = self._db._open(rel)
                try:
                    rows = conn.execute("SELECT username, nick_name, remark FROM contact").fetchall()
                finally:
                    conn.close()
                for r in rows:
                    mapping[str(r["username"])] = str(r["remark"] or r["nick_name"] or r["username"])
                break
        except Exception:
            pass
        return mapping

    def _load_groups(self) -> list:
        groups = []
        try:
            for rel, path, _ in self._db._db_files:
                if os.path.basename(path) != "contact.db":
                    continue
                conn = self._db._open(rel)
                try:
                    rows = conn.execute(
                        "SELECT username, nick_name, remark FROM contact WHERE username LIKE '%@chatroom'").fetchall()
                finally:
                    conn.close()
                for r in rows:
                    groups.append({"name": str(r["remark"] or r["nick_name"] or r["username"]), "wxid": str(r["username"])})
                break
        except Exception:
            pass
        return groups

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
                    conn, table = self._db._msg_conn(chat_id)
                    try:
                        rr = conn.execute(
                            "SELECT local_id FROM %s WHERE server_id=?" % table,
                            (int(svrid_m.group(1)),)).fetchone()
                    finally:
                        conn.close()
                    if rr:
                        media = [{"kind": "image", "local_id": rr[0]}]
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
            text = "[语音]"
        elif mtype == "视频":
            text = "[视频]"
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
                        u.ShowWindow(int(found[0]), 9)
                        time.sleep(0.5)
                        u.SetForegroundWindow(int(found[0]))
                        time.sleep(0.3)
                    self._gui = WeChatGUI()
                except Exception:
                    raise WeChatError("不可用：微信主窗口不可见（恢复失败）。请打开电脑微信后重试。")
            try:
                if self._gui.desktop_available():
                    self._gui.calibrate_layout(save=True)
            except Exception:
                pass
            self._install_ui_patches(self._gui)
        return self._gui

    def _install_ui_patches(self, gui):
        """给 GUI 实例装「界面适配」补丁（每个实例只装一次）。

        把 wechatauto 内部的 wx_click / ensure_visible / 回车发送 换成适配层版本：
          · wx_click / ensure_visible → DPI 缩放 + 清理遮挡层/系统叠加层 + 点击归属校验；
          · 回车（VK_RETURN）加 1.5 秒冷却 → 防「发送后输入框未及时清空 → 重试回车」
            把同一条消息发两遍（库内部的重试也会被拦住；分条连发间隔 3~5 秒不受影响）。
        这样换电脑（带缩放/多显示器/触屏手写画布）也不用改 wechatauto。
        """
        if getattr(gui, "_wx_agent_ui_ok", False):
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
            gui._wx_agent_ui_ok = True
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
        try:
            result = fn(*args, **kwargs)
            return result
        finally:
            self._fg_exit()

    def send_text(self, chat_id: str, text: str):
        """发送文本到群。返回 (ok, message)。"""
        if not self._dedup_send(chat_id, text):
            return True, "重复发送已拦截（3 秒内同一文本）"
        name = self.group_name(chat_id)
        try:
            with self._send_lock:  # 所有碰微信窗口的操作统一串行（发消息/引用/拍一拍/回拍不打架）
                gui = self._get_gui()
                r = self._send_with_foreground(
                    lambda g=gui: g.send_msg(text, who=name, verify=False))
                ok = bool(getattr(r, "is_success", False))
                if ok:
                    self._mark_sent(text)
                return ok, str(getattr(r, "message", "") or "")
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
                return ok, str(getattr(r, "message", "") or "")
        except Exception as e:
            return False, str(e)

    def send_image(self, chat_id: str, local_path: str):
        """发送本地图片。返回 (ok, message)。"""
        name = self.group_name(chat_id)
        try:
            with self._send_lock:
                gui = self._get_gui()
                r = self._send_with_foreground(
                    lambda g=gui: g.send_image(local_path, who=name))
                ok = bool(getattr(r, "is_success", False))
                if ok:
                    self._mark_sent("[图片]")
                return ok, str(getattr(r, "message", "") or "")
        except Exception as e:
            return False, str(e)

    # ── 表情包（收藏 / 发送）───────────────────────────────────────────
    # 微信表情原图在消息库中为加密数据（md5+len 索引），wechatauto 提供
    # 「截取最新一条表情消息气泡」的方案（EmojiMessage.capture()）——
    # 收到表情时它即会话最新一条，可截取为 PNG 存进收藏夹 data/emojis/。

    EMOJI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "emojis")

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

            def _try_click(px, py):
                self._click_screen(px, py)
                time.sleep(1.2)
                return _discover_visible()

            got_discover = False
            # ① 绿圆（发现已选中/打开过）
            pos = self._find_green_discover(gui)
            if pos and not _discover_visible():
                got_discover = _try_click(pos[0], pos[1])
            elif pos and _discover_visible():
                got_discover = True
            # ② 图标列聚类：**排除最后一枚（设置）**，从倒数第二（发现）开始往前试
            if not got_discover:
                ys = self._detect_sidebar_icons(gui)
                cands = list(reversed(ys[-3:-1])) if len(ys) >= 2 else []
                for yi in cands:
                    if got_discover:
                        break
                    got_discover = _try_click(l + int(W * 0.043), t + yi)
            # ③ 相对多候选兜底（只在下半部中上区域，不接近设置/三条杠）
            if not got_discover:
                for y_ratio in (0.70, 0.76, 0.82):
                    if _try_click(l + int(W * 0.043), t + int(H * y_ratio)):
                        got_discover = True
                        break
            if not got_discover:
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
        """打开朋友圈（UI 自适应）：① 新 UI（发现→朋友圈，OCR 定位文字）
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
        """滚动朋友圈：先置前台焦点 → 光标移到窗口内（避开图片/按钮，用右侧空白带）
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
        shots=True：dry 模式下每步截图到 _scratch/shots/moments_*.png（逐屏存证，UI 检验用）。"""
        _shot = None
        if shots:
            import os as _os
            _sdir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "_scratch", "shots")
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
            # 实测抓图(682×979)：铃铛≈(57,38)、相机≈(105,38)、刷新≈(153,38)。
            # 0624 修：之前写死 (66,36) 会点到铃铛（弹「全部互动消息」），改按比例自适应窗口大小。
            _w, _h = max(1, int(rect[2] - rect[0])), max(1, int(rect[3] - rect[1]))
            cam_x, cam_y = int(rect[0] + _w * 0.154), int(rect[1] + _h * 0.039)
            # 长按：down → 2.0s → up
            user32 = ctypes.windll.user32
            user32.SetCursorPos(cam_x, cam_y)
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(2.0)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(1.5)
            if _shot: _shot("moments_2_cam_popup.png")
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
        """笑脸按钮（输入框左下方工具栏第一个）。优先按输入框（get_input_box）动态定位——
        左侧列表宽度变化时输入框位置跟着变，笑脸随输入框走（旧版固定渲染比例会偏）；
        输入框拿不到时回退渲染比例。"""
        box = gui.get_input_box()
        render = gui.render_rect or gui._update_render_rect() or (0, 0, 0, 0)
        if box:
            # box=(x0,y0,x1,y1) 屏幕坐标 → 转渲染相对；笑脸在输入框左下角稍上（实测偏移）
            rx, ry = int(render[0]), int(render[1])
            return (int(box[0] - rx + (box[2] - box[0]) * 0.132),
                    int(box[3] - ry + 63))
        return (int(render[2] * 0.302), int(render[3] * 0.879 - 18))

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
    if not out["found"]:
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
    "wechatauto-replica": "1.1.5.1",
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
        if pkg == "wechatauto-replica":
            ok = bool(inst) and str(inst).strip().lower() == str(req).strip().lower()
        else:
            ok = bool(inst) and _ver_tuple(inst) >= _ver_tuple(req)
        rows.append((pkg, inst or "", req, ok))
    return rows, all(r[3] for r in rows)
