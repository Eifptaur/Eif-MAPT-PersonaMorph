# -*- coding: utf-8 -*-
"""出站发送队列：所有对微信的出站消息都经过这里。

- 全局串行（微信窗口只有一个输入框，必须串行发送）
- 真人化间隔（随机区间 + 按字数附加）
- 分钟/小时限频（超限拒绝，工具把错误告诉模型）
- Markdown → 纯文本、超长切分
- 发出的每一条记进 ChatStore（self=True）
"""
from __future__ import annotations

import logging
import threading
import time

from .config import get_config
from .util import format_clock_time, md_to_plain, rand_int, sleep, split_for_wx

log = logging.getLogger("wx-agent")


class SendQueue:
    def __init__(self, wechat, store, on_sent=None):
        self.wechat = wechat
        self.store = store
        self.on_sent = on_sent
        self._lock = threading.Lock()
        self.minute_times: dict = {}   # chatKey -> [ts]
        self.hour_times: dict = {}

    def _check_rate(self, chat_key: str) -> None:
        cfg = get_config().get("send", {})
        now = time.time()
        minute = [t for t in self.minute_times.get(chat_key, []) if now - t < 60]
        hour = [t for t in self.hour_times.get(chat_key, []) if now - t < 3600]
        max_min = max(1, int(cfg.get("max_per_minute") or 20))
        max_hour = max(1, int(cfg.get("max_per_hour") or 500))
        if len(minute) >= max_min:
            raise RuntimeError("发送频率超限（每分钟最多 %d 条），请等一会再发" % max_min)
        if len(hour) >= max_hour:
            raise RuntimeError("发送频率超限（每小时最多 %d 条）" % max_hour)
        minute.append(now)
        hour.append(now)
        self.minute_times[chat_key] = minute
        self.hour_times[chat_key] = hour

    def _gap(self, text: str, is_last: bool) -> float:
        cfg = get_config().get("send", {})
        if is_last:
            return 0.0
        min_gap = max(200, int(cfg.get("min_gap_ms") or 1000))
        max_gap = max(min_gap, int(cfg.get("max_gap_ms") or 3000))
        by_len = min(8000, len(text or "") * int(cfg.get("by_length_ms") or 20))
        return min(15000, max(min_gap, rand_int(min_gap, max_gap) * 0.5 + by_len * 0.5)) / 1000.0

    def send_text_batch(self, chat_key: str, messages, reply_to_mid=None, at_user_id=None, reply_text="",
                        reply_sender_name=""):
        """发送一批文本。返回 {sent, failed}。reply_text 为被引用消息的原文（定位用），
        reply_sender_name 为被引用消息的发送者（头像定位用，缺省靠文本匹配）。"""
        kind, chat_id = self._parse_key(chat_key)
        list_msgs = list(messages) if isinstance(messages, (list, tuple)) else [messages]
        if not list_msgs:
            raise RuntimeError("消息列表为空")
        hard_split = int(get_config().get("send", {}).get("hard_split_at") or 0)
        parts = []
        for m in list_msgs:
            plain = md_to_plain(str(m or ""))
            if not plain:
                continue
            if hard_split > 0 and len(plain) > hard_split:
                parts.extend(split_for_wx(plain, hard_split))
            else:
                parts.append(plain)
        if not parts:
            raise RuntimeError("消息内容为空")

        sent = []
        failed = []
        with self._lock:
            for i, text in enumerate(parts):
                is_first = i == 0
                is_last = i == len(parts) - 1
                gap = self._gap(text, is_last)
                try:
                    self._check_rate(chat_key)
                    if gap > 0:
                        time.sleep(gap)
                    # 引用 / @ 只在第一条上生效；引用是「引用最近一条消息」（近似），失败退回普通发送
                    use_at = at_user_id if is_first else None
                    use_quote = reply_to_mid if is_first else None
                    if use_quote:
                        ok, msg = self.wechat.reply_quote(chat_id, text, target_text=reply_text,
                                                          target_sender_name=reply_sender_name)
                        if not ok:
                            log.warning("引用发送失败（第%d条），退回普通发送：%s", i + 1, msg)
                            ok, msg = self.wechat.send_text(chat_id, text)
                    elif use_at:
                        name = self.wechat.member_name(chat_id, use_at)
                        if name and not str(name).startswith("wxid_"):
                            ok, msg = self.wechat.send_text_at(chat_id, name, text)
                        else:
                            ok, msg = self.wechat.send_text(chat_id, text)
                    else:
                        ok, msg = self.wechat.send_text(chat_id, text)
                    if not ok:
                        raise RuntimeError(msg or "发送失败")
                    ts = int(time.time() * 1000)
                    self.store.append_self(chat_key, text, ts=ts)
                    if self.on_sent:
                        self.on_sent(chat_key, text)
                    sent.append({"text": text, "at": format_clock_time(ts)})
                except Exception as e:
                    failed.append({"index": i, "text": text, "error": str(e)})
        if failed and not sent:
            raise RuntimeError("；".join("第%d条「%s」：%s" % (f["index"] + 1, str(f["text"])[:20], f["error"]) for f in failed))
        if failed:
            print("[sender] 部分发送失败（%d/%d）：%s" % (len(failed), len(parts),
                  "；".join("第%d条「%s」：%s" % (f["index"] + 1, str(f["text"])[:20], f["error"]) for f in failed)))
        return {"sent": sent, "failed": failed}

    def send_image(self, chat_key: str, local_path: str):
        """发送一张本地图片（微信剪贴板粘贴）。"""
        kind, chat_id = self._parse_key(chat_key)
        with self._lock:
            self._check_rate(chat_key)
            time.sleep(rand_int(600, 1500) / 1000.0)
            ok, msg = self.wechat.send_image(chat_id, local_path)
            if not ok:
                raise RuntimeError(msg or "图片发送失败")
            ts = int(time.time() * 1000)
            self.store.append_self(chat_key, "[图片]", ts=ts)
            if self.on_sent:
                self.on_sent(chat_key, "[图片]")
            return {"sent": True}

    @staticmethod
    def _parse_key(chat_key: str):
        kind, _, chat_id = str(chat_key).partition(":")
        if kind not in ("group", "private"):
            raise RuntimeError("非法会话 key：%s" % chat_key)
        return kind, chat_id
