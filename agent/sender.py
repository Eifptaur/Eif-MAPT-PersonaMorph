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
import random
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

    def _should_auto_quote(self, chat_key: str):
        """「新一段对话」开始时，大概率引用对方最近一句话（默认 70%）。

        判定：
        - send.quote_on_new_talk 未关闭；
        - 概率 send.quote_reply_probability（默认 0.7）命中；
        - 机器人在该群**上一条消息已超过 quote_new_talk_gap_s（默认 300 秒）**——
          即对话已冷场、本轮算是"重新开始的一段对话"；
        - 找到最近一条「别人」发的文本消息。
        返回 (text, sender_name) 或 None（调用方传了显式 reply_to_message_id 时以显式为准）。
        """
        try:
            cfg = get_config().get("send", {})
            if cfg.get("quote_on_new_talk") is False:
                return None
            prob = float(cfg.get("quote_reply_probability", 0.7))
            gap_s = max(0, int(cfg.get("quote_new_talk_gap_s", 300)))
            msgs = self.store.recent(chat_key, limit=60)
            my_last = max((int(m.get("ts") or 0) for m in msgs if m.get("self")), default=0)
            if my_last and (time.time() * 1000 - my_last) < gap_s * 1000:
                return None  # 还在连续对话中，不重复开引用
            if random.random() >= prob:
                return None  # 概率未触发
            for m in reversed(msgs):
                txt = str(m.get("text") or "").strip()
                sid = str(m.get("sender_id") or "")
                if not m.get("self") and sid.startswith("wxid_") and txt and not txt.startswith("["):
                    return (txt[:200], str(m.get("sender_name") or ""))
        except Exception:
            pass
        return None

    def send_text_batch(self, chat_key: str, messages, reply_to_mid=None, at_user_id=None, reply_text="",
                        reply_sender_name=""):
        """发送一批文本。返回 {sent, failed}。reply_text 为被引用消息的原文（定位用），
        reply_sender_name 为被引用消息的发送者（头像定位用，缺省靠文本匹配）。

        引用规则（程序级，不依赖模型自觉）：
        - 模型显式传 reply_to_message_id → 按模型的引用；
        - 否则若「新一段对话开始」（机器人上条消息超 300s + 概率 70%）→ 自动引用
          对方最近一句话（send.quote_on_new_talk / quote_reply_probability / quote_new_talk_gap_s 可调）。
        """
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
        # 程序级自动引用：新一段对话开始 + 概率命中 → 引用对方最近一句话
        auto_quote = None
        if not reply_to_mid:
            auto_quote = self._should_auto_quote(chat_key)
            if auto_quote:
                reply_text = auto_quote[0]
                reply_sender_name = auto_quote[1] or reply_sender_name
                log.info("新对话开始，自动引用对方最近一句：%s", reply_text[:40])
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
                    use_quote = (reply_to_mid or auto_quote) if is_first else None
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
