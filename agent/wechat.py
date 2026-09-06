# -*- coding: utf-8 -*-
"""wechatauto 适配层：读消息 / 发消息 / 下载图片。

对 wechatauto 的 WeChatDB / WeChatGUI / MediaDownloader 做统一封装，
把微信原始消息归一化成 wx-agent 内部结构，屏蔽底层差异。
"""
from __future__ import annotations

import base64
import html
import os
import re
import threading
import time
from collections import deque

from .config import get_config

# 微信消息类型标签 → 内部占位文本
TYPE_LABEL = {
    "文本": "text",
    "图片": "image",
    "动画表情": "emoji",
    "语音": "voice",
    "视频": "video",
    "位置": "location",
    "文件/链接/卡片": "file",
    "红包": "redpacket",
    "系统消息": "system",
}

_SENDER_RE = re.compile(r"^(wxid_[0-9a-zA-Z_-]+|.*@chatroom):\s*")


def _seq_ratio(a: str, b: str) -> float:
    """文本相似度 0~1（difflib，OCR 与数据库文本比对用）。"""
    try:
        import difflib
        return difflib.SequenceMatcher(None, str(a or ""), str(b or "")).ratio()
    except Exception:
        return 0.0


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
                pm = re.search(r"「([^」]+)」拍拍", title)
                if pm:
                    poker = pm.group(1)
                return {"text": "[拍一拍]" + ("（%s）" % poker if poker else ""),
                        "media": [], "sender_wxid": "", "poke": True, "poker": poker}

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
            text = "[表情]"
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
        }

    # ── 发送 ─────────────────────────────────────────────────────────────

    def _get_gui(self):
        if self._gui is None:
            try:
                from wechatauto.guia import WeChatGUI
            except ImportError as e:
                raise WeChatError("wechatauto.guia 不可用：%s" % e)
            self._gui = WeChatGUI()
            try:
                if self._gui.desktop_available():
                    self._gui.calibrate_layout(save=True)
            except Exception:
                pass
            self._install_ui_patches(self._gui)
        return self._gui

    def _install_ui_patches(self, gui):
        """给 GUI 实例装「界面适配」补丁（每个实例只装一次）。

        把 wechatauto 内部的 wx_click / ensure_visible 换成适配层版本，
        让所有内部点击（@、发图、引用、发送按钮回退、open_chat 回退等）
        都自动：换算 DPI 缩放 + 清理遮挡层/系统叠加层 + 校验点击点属于微信。
        这样换电脑（带缩放/多显示器/触屏手写画布）也不用改 wechatauto。
        """
        if getattr(gui, "_wx_agent_ui_ok", False):
            return
        try:
            from . import ui_adapt
            orig_ensure = gui.ensure_visible
            orig_click = gui.wx_click
            adapter = self

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

            gui.ensure_visible = ensure_visible
            gui.wx_click = wx_click
            gui._wx_agent_ui_ok = True
        except Exception:
            pass

    def send_text(self, chat_id: str, text: str):
        """发送文本到群。返回 (ok, message)。"""
        name = self.group_name(chat_id)
        try:
            gui = self._get_gui()
            r = gui.send_msg(text, who=name, verify=False)
            ok = bool(getattr(r, "is_success", False))
            if ok:
                self._mark_sent(text)
            return ok, str(getattr(r, "message", "") or "")
        except Exception as e:
            return False, str(e)

    def send_text_at(self, chat_id: str, member_name: str, text: str):
        """在群里 @ 成员并发送文本。返回 (ok, message)。"""
        name = self.group_name(chat_id)
        try:
            gui = self._get_gui()
            r = gui.at_member(member_name, text, who=name, verify=False)
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
            gui = self._get_gui()
            r = gui.send_image(local_path, who=name, verify=False)
            return bool(getattr(r, "is_success", False)), str(getattr(r, "message", "") or "")
        except Exception as e:
            return False, str(e)

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

        菜单文字较小，优先放大 3 倍 OCR（识别更稳），失败再退回原尺寸。
        右键走 ui_adapt.click（DPI/遮挡适配），菜单点击后再回读确认可见。
        """
        ok, why = self._click(gui, rel_x, rel_y, right=True)
        if not ok:
            return False
        time.sleep(delay)
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
                # 命中菜单文字后，点击也用适配层（先校验归属）
                ok2, _ = self._click(gui, x + w // 2, y + h // 2, right=False)
                if not ok2:
                    return False
                return True
        return False

    def _last_target_text(self, chat_id: str, wxid: str) -> str:
        """从数据库找目标最近一条消息的文本（用于 OCR 模糊匹配定位）。"""
        try:
            raws = self._db.get_messages(chat_id, limit=60)
            for raw in reversed(raws):
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

    def _uia_target_row_rect(self, gui, db_text: str):
        """用 UIA 消息列表精确匹配目标最近一条消息的行矩形（屏幕坐标）。

        微信 4.x 的消息列表在 UIA 树里是 chat_message_list（mmui::RecyclerListView），
        每行 mmui::ChatTextItemView 的 Name 就是消息原文——比 OCR 可靠得多。
        返回 (left, top, right, bottom) 或 None。
        """
        try:
            uia = gui._get_uia()
            if uia is None:
                return None
            lst = uia._message_list()
            if lst is None:
                return None
            target = self._norm_ocr(db_text)
            if not target:
                return None
            best = None
            for ch in list(lst.GetChildren()):
                try:
                    if ch.ClassName != "mmui::ChatTextItemView":
                        continue
                    nm = self._norm_ocr(ch.Name or "")
                except Exception:
                    continue
                if nm and (nm == target or (len(nm) >= 6 and target.startswith(nm))):
                    best = ch  # 取最后一个（最新）
            if best is None:
                return None
            r = best.BoundingRectangle
            return (r.left, r.top, r.right, r.bottom)
        except Exception:
            return None

    def _send_poke_locate(self, gui, target_name: str, db_text: str):
        """定位目标头像（渲染相对坐标），返回 (ax, ay, score) 或 None。

        优先 UIA 消息行矩形（精确），其次 OCR 相似度匹配，最后左侧消息块兜底。
        头像中心 ≈ 行内 (+54, +48)（实测校准：头像 45~50px，行左缘 +54 即头像中心）。
        """
        # 1) UIA 行精确匹配
        row = self._uia_target_row_rect(gui, db_text)
        if row:
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

        # 剔除垃圾项（侧栏碎片/小残片）与右侧（机器人自己的消息）
        items = [it for it in items
                 if it[3] > 30 and (gui.right_pane_left + 60) < it[1] < mid_x]

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

    def send_poke(self, chat_id: str, target_name: str, target_id: str = ""):
        """拍一拍某位成员：右键对方头像 → 菜单选「拍一拍」。靠 UIA/OCR 定位 + 数据库验证。

        返回 (ok, message)。对方最近发过言、名字在可见消息区里才比较容易成功。
        验证失败会如实返回，不会假报成功。
        """
        try:
            gui = self._get_gui()
            group = self.group_name(chat_id)
            if not self._ensure_foreground(gui):
                return False, "微信窗口未找到或已退出，无法操作"
            if not gui.open_chat(group):
                return False, "打开会话失败"
            time.sleep(0.9)
            base_seq = self.latest_seq(chat_id)
            db_text = self._last_target_text(chat_id, target_id) if target_id else ""
            located = self._send_poke_locate(gui, target_name, db_text)
            if located:
                ax, ay, score = located
                if self._right_click_menu(gui, ax, ay, "拍一拍"):
                    return self._verify_poke(chat_id, target_name, base_seq)
                return False, "右键菜单里没找到「拍一拍」（头像点 (%d,%d) 可能没点中）" % (ax, ay)
            return False, ("未在可见消息里定位到「%s」的头像；让对方先发条消息再试" % target_name)
        except Exception as e:
            return False, str(e)

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
        """拍完后回读数据库，确认真的出现了**新的**拍拍事件。绝不假报成功。

        从 base_seq 之后新出现的消息里找含「拍拍/拍了拍」的（可能是 zstd appmsg，
        也可能是普通系统文本），找到才算成功。
        """
        try:
            time.sleep(1.5)  # 等微信落库
            raws = self._db.get_new_messages(chat_id, base_seq, 10)
            for row in raws:
                txt = self._row_inner_text(row)
                if "拍拍" in txt:
                    return True, "已拍一拍「%s」（已验证：群里出现新的拍一拍事件）" % target_name
            return False, "已点「拍一拍」但群里没出现新的拍一拍事件（可能没点中/没拍到，如实告诉对方这次没拍上，稍后再试）"
        except Exception as e:
            return False, "已点「拍一拍」但无法验证（%s），不能保证拍到" % e

    def reply_quote(self, chat_id: str, text: str):
        """引用最近一条消息并发送文字：右键最近消息 → 菜单选「引用」→ 输入 → 发送。"""
        try:
            gui = self._get_gui()
            group = self.group_name(chat_id)
            if not self._ensure_foreground(gui):
                return False, "微信窗口未找到或已退出，无法操作"
            if not gui.open_chat(group):
                return False, "打开会话失败"
            time.sleep(0.8)
            y = gui._last_message_y()
            if y is None:
                return False, "未检测到消息区"
            rel_x = (gui.render_w + gui.right_pane_left) // 2
            if not self._right_click_menu(gui, rel_x, y, "引用"):
                return False, "右键菜单里没找到「引用」"
            time.sleep(0.5)
            if not gui.input_text(text):
                return False, "输入文字失败"
            if not gui.click_send():
                return False, "发送失败"
            self._mark_sent(text)
            return True, "已引用并发送"
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
