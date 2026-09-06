# -*- coding: utf-8 -*-
"""wechatauto 适配层：读消息 / 发消息 / 下载图片。

对 wechatauto 的 WeChatDB / WeChatGUI / MediaDownloader 做统一封装，
把微信原始消息归一化成 wx-agent 内部结构，屏蔽底层差异。
"""
from __future__ import annotations

import base64
import os
import re
import threading
import time

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
            norm = self.normalize(raw)
            if norm:
                out.append(norm)
        return out

    def normalize(self, raw: dict):
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
        if sender_id == 2:
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
            text = "[文件/链接/卡片]"
        elif mtype == "红包":
            text = "[红包]"
        else:
            text = "[%s]" % mtype

        if not text.strip():
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
        return self._gui

    def send_text(self, chat_id: str, text: str):
        """发送文本到群。返回 (ok, message)。"""
        name = self.group_name(chat_id)
        try:
            gui = self._get_gui()
            r = gui.send_msg(text, who=name, verify=False)
            return bool(getattr(r, "is_success", False)), str(getattr(r, "message", "") or "")
        except Exception as e:
            return False, str(e)

    def send_text_at(self, chat_id: str, member_name: str, text: str):
        """在群里 @ 成员并发送文本。返回 (ok, message)。"""
        name = self.group_name(chat_id)
        try:
            gui = self._get_gui()
            r = gui.at_member(member_name, text, who=name, verify=False)
            return bool(getattr(r, "is_success", False)), str(getattr(r, "message", "") or "")
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

    def _right_click_menu(self, gui, rel_x: int, rel_y: int, label: str) -> bool:
        """在相对坐标 (rel_x, rel_y) 处右键，OCR 弹出菜单，点含 label 的项。"""
        try:
            gui.wx_click(int(gui.origin_x + rel_x), int(gui.origin_y + rel_y), right=True)
        except Exception:
            return False
        time.sleep(0.7)
        top = max(0, rel_y - 140)
        bottom = min(gui.render_h, rel_y + 240)
        try:
            items = gui.ocr((gui.right_pane_left, top, gui.render_w, bottom))
        except Exception:
            return False
        for text, x, y, w, h in items:
            if label and label in text:
                try:
                    gui.wx_click(int(gui.origin_x + x + w // 2), int(gui.origin_y + y + h // 2))
                except Exception:
                    return False
                return True
        return False

    def send_poke(self, chat_id: str, target_name: str):
        """拍一拍某位成员：右键对方头像 → 菜单选「拍一拍」。实验性，靠 OCR 定位。

        返回 (ok, message)。对方最近发过言、名字在可见消息区里才比较容易成功。
        """
        try:
            gui = self._get_gui()
            group = self.group_name(chat_id)
            if not gui.ensure_visible():
                return False, "微信窗口不可见（可能锁屏或最小化）"
            if not gui.open_chat(group):
                return False, "打开会话失败"
            time.sleep(0.8)
            box = gui.get_input_box()
            if not box:
                return False, "未检测到输入框，无法定位消息区"
            top = max(80, box[1] - 600)
            items = gui.ocr((gui.right_pane_left, top, gui.render_w, box[1]))
            hit = None
            for text, x, y, w, h in items:
                if target_name and target_name in text:
                    hit = (x, y, w, h)
                    break
            if not hit:
                return False, "未在可见消息里找到「%s」，可让对方先发一条消息、或先往上翻到他的消息" % target_name
            # 头像在消息区最左边缘、与名字同一行 → 右键头像
            ax = gui.right_pane_left + 28
            ay = hit[1] + hit[3] // 2
            if self._right_click_menu(gui, ax, ay, "拍一拍"):
                return self._verify_poke(chat_id, target_name)
            return False, "右键菜单里没找到「拍一拍」"
        except Exception as e:
            return False, str(e)

    def _verify_poke(self, chat_id: str, target_name: str):
        """拍完后回读数据库，确认是否真的出现了「拍了拍」。"""
        try:
            time.sleep(1.2)  # 等微信落库
            msgs = self._db.get_messages(chat_id, limit=6)
            for m in msgs:
                content = str(m.get("content") or "")
                if "拍了拍" not in content:
                    continue
                # 自己发起的拍一拍：sender_id==2；或内容里同时含目标名
                if m.get("sender_id") == 2 or target_name in content:
                    return True, "已拍一拍「%s」（已验证）" % target_name
            return False, "已点「拍一拍」但未验证到结果（可能没点中，或微信还没落库）"
        except Exception:
            return True, "已拍一拍「%s」（点击成功，验证跳过）" % target_name

    def reply_quote(self, chat_id: str, text: str):
        """引用最近一条消息并发送文字：右键最近消息 → 菜单选「引用」→ 输入 → 发送。"""
        try:
            gui = self._get_gui()
            group = self.group_name(chat_id)
            if not gui.ensure_visible():
                return False, "微信窗口不可见（可能锁屏或最小化）"
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
