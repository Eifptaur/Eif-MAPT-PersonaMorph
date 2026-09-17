# -*- coding: utf-8 -*-
"""Persona Morph 主程序 —— 微信智能机器人。

整合自两个项目：
  - 智能大脑：qq-agent（无状态会话 / 响应档位 / 工具集 / 记忆 / 联网搜索 / 人设）
  - 微信接入：wechat-deepseek-bot（wechatauto UIA 无注入读消息 + 屏幕自动化发送）

运行：双击 启动机器人.vbs（无窗口推荐）或 python persona_morph.py --foreground（调试看日志）
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import random
import webbrowser
from collections import deque
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None
sys.stderr.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stderr, "reconfigure") else None

# 内置绿色版 Python（embed）由 python*. _pth 固定搜索路径（不含程序目录），
# 必须把本文件目录手动加入 sys.path，否则 import agent 失败（watchdog 拉起即崩）。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent import listener_watermark          # W2：持久化水位（成功才推进 / 重试留痕 / 每会话串行）
from agent import recall                       # 第 14 条：撤回后把已进上下文的那条剔除
from agent import tier_control                 # 第 15/16/18 条：固定 4 档 / 峰谷映射 / 指令禁言
from agent import timers                       # 第 12 条：计时提醒（只对当前会话 + 过风险闸门 + 条数上限）
from agent import holidays                     # 第 13 条：节假日问候（默认只在提示词里提一句）
from agent import archive_filter               # 第 10 条：按会话/按条屏蔽存档消息
from agent.config import DATA_DIR
from agent.config import get_config, save_config
from agent.llm import (add_usage, chat_completion, chat_completion_with_retry,
                       empty_usage, estimate_cost, is_retryable_error, query_balance)
from agent.memory import MemoryStore
from agent.prompt import build_system_prompt, build_user_prompt, resolve_context_tier, is_at_me
from agent.sender import SendQueue
from agent.session_log import SessionLog
from agent.stats import UsageStats
from agent.store import ChatStore
from agent.tools import build_tool_defs, execute_tool, to_openai_tools
from agent.wechat import (WeChatAdapter, WeChatError, wechat_version_info,
                          attach_diagnosis, attach_short_reason, _user32_is_visible)
from agent.whale import WhaleWidget
from agent.webui import WebUI
from agent.util import mask_url_token, pick_browser, redact_secrets
# 输入审计（**只记录、不改行为**，且只在 WXAGENT_INPUT_AUDIT=1 时安装；生产默认零开销）——
# 2026-09-17 为定位「机器人发消息那一刻微信自己弹截图」而挂：它把 SendInput/keybd_event/mouse_event/
# SetCursorPos/PostMessageW 的每次调用连同**发起方的文件:行号**写进 data/input_audit.log。
try:
    from agent import input_audit as _input_audit
    _input_audit.install()
except Exception:
    pass

# 内部自检开关：WX_IMPORT_CHECK=1 时仅验证模块导入后退出（绿色版/无微信场景验证用）
if os.environ.get("WX_IMPORT_CHECK") == "1":
    print("WX_AGENT IMPORT OK")
    sys.exit(0)


class _SecretFormatter(logging.Formatter):
    """日志格式化时统一脱敏（sk-***），防止 Key 写进日志/控制台。"""

    def format(self, record):
        try:
            return redact_secrets(super().format(record))
        except Exception:
            return super().format(record)

LOG_DIR = os.path.join(ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

class _FlushFileHandler(logging.handlers.RotatingFileHandler):
    """按体积轮转 + 每行立即落盘。

    · 轮转：单文件 5MB、保留 3 份 `.1/.2/.3`（此前是**没有任何上限的普通 FileHandler**，
      挂着跑会一直涨 —— 2026-09-13 体检发现）；
    · 每行 flush：pythonw 进程被强杀时缓冲不丢，日志文件始终完整。
    """

    def __init__(self, filename, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"):
        logging.handlers.RotatingFileHandler.__init__(
            self, filename, mode="a", maxBytes=maxBytes, backupCount=backupCount,
            encoding=encoding, delay=True)

    def emit(self, record):
        try:
            super().emit(record)
            self.flush()
        except Exception:
            try:
                self.handleError(record)
            except Exception:
                pass


logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        _FlushFileHandler(os.path.join(LOG_DIR, "persona_morph.log"), encoding="utf-8"),
    ],
)
_fmt = _SecretFormatter("%(asctime)s [%(levelname)s] %(message)s")
for _h in logging.getLogger().handlers:
    _h.setFormatter(_fmt)
log = logging.getLogger("persona-morph")
# 进程真启动时刻（控制台「启动于」用这个；以前那里写的是"每次状态刷新的当前时间"，被误解成启动时间）
_BOOT_AT = time.strftime("%Y-%m-%d %H:%M:%S")

# Web 控制台的日志环形缓冲（近 500 条）
log_buffer = deque(maxlen=500)


class _RingHandler(logging.Handler):
    def __init__(self, buffer):
        super().__init__()
        self.buffer = buffer

    def emit(self, record):
        try:
            self.buffer.append(self.format(record))
        except Exception:
            pass


_ring = _RingHandler(log_buffer)
_ring.setFormatter(_SecretFormatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger().addHandler(_ring)

# 启动时做一次日志体积治理（主日志轮转在 handler 里，这里处理"只追加"的那几份）
try:
    from agent import log_housekeeping as _lh
    _lh.sweep(ROOT, log=log)
except Exception as _e:      # 治理失败绝不能挡住启动
    log.warning("日志治理跳过：%s", _e)


# 兜底自动补发的过滤（2026-09-15 用户点头「对用户有好处就加」）：
# 不加过滤时，模型把"内心分析"写进最终文本就会被**原样发进群**（提示词警告过，但不能只靠它自觉）。
# 口径：默认只补发 ≤ `send.fallback_max_chars` 的短话，并拦掉自我指涉（那是内心判断，不是群发言）；
#      过滤本身出异常 ⇒ **按不发处理**（fail-closed：宁可沉默，也不乱发）。
_FALLBACK_SELF_REF = ("我不打算", "不打算回", "不回复", "不打算说", "没什么可说", "没什么好说",
                      "保持沉默", "不发言", "就不说话", "不用回", "不必回", "无需回",
                      "我在看", "我只是看", "内心", "分析一下", "从记录看", "从上下文看",
                      "评估下来", "判断下来", "本轮不需要", "这轮不回", "沉默更好", "安静就好")


def _fallback_send_ok(text: str, cfg=None):
    """兜底补发要不要发 ⇒ `(能不能发, 原因)`。"""
    try:
        c = cfg if isinstance(cfg, dict) else (get_config() or {})
        s = c.get("send") or {}
        if s.get("fallback_autosend", True) is False:
            return False, "兜底补发被关掉（send.fallback_autosend=false）"
        mx = int(s.get("fallback_max_chars", 60) or 0)
        if mx > 0 and len(text) > mx:
            return False, "太长（%d 字 > 上限 %d）：长文多半是分析，不是群聊发言" % (len(text), mx)
        if s.get("fallback_block_selfref", True) is not False:
            for k in _FALLBACK_SELF_REF:
                if k in text:
                    return False, "含自我指涉（%s）：那是内心判断，不该发进群" % k
        return True, ""
    except Exception as e:
        return False, "过滤器异常，按不发处理：%s" % str(e)[:40]


def _parse_inline_calls(text: str) -> list:
    """兼容少数模型把工具调用写成文本而非原生 tool_calls 的情况。"""
    calls = []
    if not text:
        return calls
    # 形式一：<invoke name="xxx">{"a":1}</invoke> / <invoke name="xxx"/>
    for m in re.finditer(r"<invoke[^>]*name=[\"']([^\"']+)[\"'][^>]*>(.*?)</invoke>", text, re.S):
        name = m.group(1).strip()
        body = m.group(2).strip()
        args = {}
        if body:
            try:
                args = json.loads(body)
            except Exception:
                args = {}
        calls.append({"name": name, "args": args})
    if calls:
        return calls
    # 形式二：{"name": "...", "arguments": {...}} 独立 JSON 对象
    for m in re.finditer(r"\{\s*[\"']name[\"']\s*:\s*[\"']([^\"']+)[\"']\s*,\s*[\"']arguments[\"']\s*:", text):
        name = m.group(1)
        start = m.start()
        depth = 0
        end = start
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        try:
            obj = json.loads(text[start:end])
            calls.append({"name": name, "args": obj.get("arguments") or {}})
        except Exception:
            pass
    return calls


class Orchestrator:
    """事件驱动的"无状态运行"核心（移植自 qq-agent src/orchestrator.js）。"""

    MEMBER_MIN_MESSAGES = 3
    MEMBER_MIN_IMPRESSIONS = 1

    def __init__(self, store: ChatStore, memory: MemoryStore, sender: SendQueue, wechat: WeChatAdapter):
        self.store = store
        self.memory = memory
        self.sender = sender
        self.wechat = wechat
        self.tool_defs = build_tool_defs()
        self.paused = False
        self.stopped = False
        self._lock = threading.Lock()
        self.wake_timers: dict = {}      # chatKey -> threading.Timer
        self.running_chats: set = set()
        self._consolidating: set = set()
        self._executor = ThreadPoolExecutor(max_workers=max(1, int(get_config().get("max_concurrent_runs") or 2)))
        self._proactive_timer = None
        self._last_trigger: dict = {}  # chat_key -> (触发消息指纹, 时间戳, 上次是否成功)
        # 用量统计（持久化 + 按周期重置）与运行明细（思考/token/工具）
        self.stats_store = UsageStats(os.path.join(ROOT, "data"),
                                      str(get_config().get("stats", {}).get("period") or "weekly"))
        self.session_log = SessionLog(os.path.join(ROOT, "data"))
        _t = self.stats_store.snapshot().get("total") or {}
        self.stats = {"sessions": int(_t.get("sessions") or 0), "tokens": int(_t.get("tokens") or 0),
                      "sent": int(_t.get("sent") or 0), "calls": int(_t.get("calls") or 0),
                      "cost": float(_t.get("cost") or 0.0)}
        # 小鲸鱼余额挂件：服务端记账（每次调用计成本，会话结束结算“每轮消耗”）
        self.whale = WhaleWidget(os.path.join(ROOT, "data"))

    # ── 入站 ─────────────────────────────────────────────────────────────

    def on_incoming(self, chat_key: str):
        if self.paused or self.stopped:
            return
        with self._lock:
            if chat_key in self.running_chats:
                return
        self.schedule_wake(chat_key)

    def schedule_wake(self, chat_key: str, delay=None):
        cfg = get_config()
        ms = cfg.get("wake_delay_ms", 2000) if delay is None else delay
        ms = max(0, int(ms or 0)) / 1000.0
        with self._lock:
            t = self.wake_timers.get(chat_key)
            if t is not None:
                t.cancel()
            timer = threading.Timer(ms, self._on_wake_timer, args=(chat_key,))
            timer.daemon = True
            self.wake_timers[chat_key] = timer
            timer.start()

    def _on_wake_timer(self, chat_key: str):
        with self._lock:
            self.wake_timers.pop(chat_key, None)
            if self.paused or self.stopped:
                return
            if chat_key in self.running_chats:
                return
            self.running_chats.add(chat_key)
        self._executor.submit(self._run_and_release, chat_key)

    def _run_and_release(self, chat_key: str):
        try:
            self.wake(chat_key)
        except Exception as e:
            log.error("运行 %s 出错: %s", chat_key, e)
        finally:
            with self._lock:
                self.running_chats.discard(chat_key)
            # 反应评分：为本次发言打分（群友有回应=正分；孤立=负分）
            try:
                self._score_feedback(chat_key)
            except Exception:
                pass
            if not self.paused and not self.stopped:
                if self.store.unread_count(chat_key) > 0:
                    self.schedule_wake(chat_key, get_config().get("drain_delay_ms", 1200))
                self._maybe_consolidate(chat_key)

    def _score_feedback(self, chat_key: str):
        """给机器人最近一条 reaction 记录正/负反馈：
        若该消息后 10 分钟内有人群友回话 → 加分；否则孤立 → 减分（权重由回复热议度）。"""
        try:
            from agent.scoring import note_feedback
        except Exception:
            from scoring import note_feedback
        try:
            msgs = self.store.recent(chat_key, limit=20)
            self_idx = None
            for i in range(len(msgs) - 1, -1, -1):
                if msgs[i].get("self"):
                    self_idx = i
                    break
            if self_idx is None:
                return
            mine = msgs[self_idx]
            my_ts = int(mine.get("ts") or 0)
            replies = [m for m in msgs[self_idx + 1:]
                       if not m.get("self") and str(m.get("text") or "").strip()]
            replies = [m for m in replies if abs(int(m.get("ts") or 0) - my_ts) <= 10 * 60000]
            text = str(mine.get("text") or "").strip()
            if not text:
                return
            if replies:
                note_feedback(text, positive=True, weight=min(2.0, 0.5 + len(replies) * 0.5))
            else:
                note_feedback(text, positive=False, weight=0.5)
        except Exception:
            pass

    # ── 主动开话题（proactive，默认关）──────────────────────────────────

    def start_proactive_loop(self):
        """启动主动话题循环（定时+概率，从候选群里选一个冷场群开话题）。"""
        try:
            self._proactive_timer.cancel()
        except Exception:
            pass
        if get_config().get("proactive", {}).get("enabled") is not True:
            self._proactive_timer = None
            return

        def tick():
            cfg = get_config().get("proactive", {})
            try:
                self._run_proactive_tick(cfg)
            except Exception as e:
                log.warning("主动话题 tick 异常：%s", e)
            # 下一轮调度（无论本轮结果，只要仍启用就排下一轮）
            try:
                if self.stopped:
                    return
                if get_config().get("proactive", {}).get("enabled") is not True:
                    self._proactive_timer = None
                    return
                lo = max(60000, int(cfg.get("check_interval_min_ms") or 1800000))
                hi = max(lo, int(cfg.get("check_interval_max_ms") or 5400000))
                nxt = random.randint(lo, hi) / 1000.0
                self._proactive_timer = threading.Timer(nxt, tick)
                self._proactive_timer.daemon = True
                self._proactive_timer.start()
            except Exception:
                pass

        tick()

    def _run_proactive_tick(self, cfg):
        if self.paused or self.stopped:
            return
        if random.random() > max(0.0, min(1.0, float(cfg.get("probability") or 0.25))):
            return
        idle_ms = max(300000, int(cfg.get("idle_threshold_ms") or 1800000))
        now = time.time()
        candidates = []
        try:
            for g in self.wechat.list_groups():
                chat_key = "group:" + g["wxid"]
                # 冷场判定：群最后一条消息距今超过阈值
                recent = self.store.recent(chat_key, limit=5) or []
                if recent:
                    last_ts = max(int(m.get("ts") or 0) for m in recent)
                    if (now * 1000 - last_ts) < idle_ms:
                        continue
                candidates.append((chat_key, g["name"]))
        except Exception as e:
            log.warning("候选群获取异常：%s", e)
        if not candidates:
            return
        chat_key, name = random.choice(candidates)
        log.info("主动话题：群[%s]（冷场）", name)
        # 主动话题用 wake 带空触发（模型在提示词里看到「主动开话题」场景）
        self._schedule_proactive_wake(chat_key)

    def _schedule_proactive_wake(self, chat_key: str):
        def _run():
            try:
                if self.paused or self.stopped:
                    return
                self._executor.submit(self._run_proactive_agent, chat_key)
            except Exception:
                pass
        t = threading.Timer(random.uniform(1.0, 3.0), _run)
        t.daemon = True
        t.start()

    def _run_proactive_agent(self, chat_key: str):
        """主动开话题的一次运行：无未读触发，走 run_agent 但触发批为空（proactive 标记）。"""
        try:
            if self.paused or self.stopped:
                return
            self_nickname = get_config().get("persona", {}).get("self_nickname") or get_config().get("wechat", {}).get("bot_nickname") or ""
            bot_name = get_config().get("persona", {}).get("bot_name") or ""
            cfg = get_config()
            if not str(cfg.get("api", {}).get("base_url") or "").strip() or not str(cfg.get("api", {}).get("model") or "").strip():
                return
            session = {"chat_key": chat_key, "sent": [], "usage": empty_usage(), "past_state_count": 0,
                       "feedbacks": [], "finish_reason": None, "web_search_count": 0, "activity": "",
                       "model": cfg.get("api", {}).get("model"), "prompt_chars": 0}
            # 主动话题 = 无触发批；档位按 4 档处理（能说话就说话）
            tier_result = {"tier": 4, "count": 0, "reason": "主动话题", "should_respond": True}
            self._last_trigger[chat_key] = (tuple(), time.time(), True)
            try:
                self.run_agent(chat_key, [], tier_result, session, proactive=True)
            except Exception as e:
                log.warning("主动话题运行异常：%s", e)
        except Exception as e:
            log.warning("主动话题异常：%s", e)

    def _maybe_human_behaviors(self, chat_key: str, pending):
        """人性化行为（纯本地规则，零 token）：
        · 收到 [表情]/[图片] → 用户明确要求（艾特文本含「收藏/发同款」）→ 强制收藏；
          否则按概率收藏（collect_emoji 截图/下载）
        · 群里有表情且收藏夹非空 → 概率回发一个（send_emoji）
        · 新对话时机 → 概率 @ 活跃成员（at_member）
        频率由 behavior.* 配置 + 人设 participation/sticker_level 系数决定；
        自定义角色卡不额外改频率（角色卡管"怎么说"，这里管"做不做"）。"""
        try:
            import random
            from . import behavior as bh
            kind, chat_id = chat_key.split(":", 1)
            # 0) 用户明确指令关键词（艾特文本里出现 → 本次强制执行该行为）
            force_collect = force_send = False
            for m in pending:
                t = str(m.get("text") or "")
                if "收藏" in t or "收下" in t or "加表情" in t:
                    force_collect = True
                if "发表情" in t or "发出来" in t or "一样发" in t or "发同款" in t or "一起发" in t:
                    force_send = True
            # 1) 收藏表情/图片
            media_entries = [m for m in pending
                             if any((mm.get("kind") in ("emoji", "image")) for mm in (m.get("media") or []))]
            if media_entries and bh.decider.should("collect_emoji", {"force": force_collect}):
                e = media_entries[0]
                lid = next((mm.get("local_id") for mm in (e.get("media") or []) if mm.get("local_id")), None)
                if lid:
                    path = self.wechat.collect_emoji(chat_id, lid)
                    log.info("%s 人性化动作：收藏表情 %s", chat_key, path or "(失败)")
                    try:
                        self.session_log.append({
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "chat_key": chat_key,
                            "chat_name": self.wechat.group_name(chat_id) or chat_id,
                            "trigger": "人性化:收藏表情", "reasoning": "", "tools": [{"name": "collect_emoji", "args": {"path": path or "失败"}}],
                            "status": "ok" if path else "error", "ok": bool(path)})
                    except Exception:
                        pass
                    if path and force_collect:
                        try:
                            self.wechat.send_text(chat_id, "收进我的表情库了，这就发～")
                        except Exception:
                            pass
            # 2) 回发表情（对方发了表情，收藏夹有货才回）
            if media_entries and bh.decider.should("send_emoji", {"force": force_send}):
                emojis = self.wechat.list_emojis()
                if emojis:
                    pick = random.choice(emojis)
                    try:
                        self.wechat.send_emoji(chat_id, pick["path"])
                        log.info("%s 人性化动作：回发表情 %s", chat_key, pick["name"])
                        try:
                            self.session_log.append({
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "chat_key": chat_key,
                                "chat_name": self.wechat.group_name(chat_id) or chat_id,
                                "trigger": "人性化:回发表情", "reasoning": "", "tools": [{"name": "send_emoji", "args": {"name": pick["name"]}}],
                                "status": "ok", "ok": True})
                        except Exception:
                            pass
                    except Exception as e:
                        log.debug("回发表情失败：%s", e)
            # 3) 概率 @ 活跃成员（放在消息多、可能开新话题时；低频）
            if len(pending) >= 2 and bh.decider.should("at_member"):
                try:
                    members = self.store.active_members(chat_key, 10)
                    if members and any(m.get("user_id") and m.get("user_id") != self.wechat.self_wxid for m in members):
                        pass  # @ 的具体内容交给模型（在提示词里给 hint），这里不直接发
                except Exception:
                    pass
        except Exception as e:
            log.debug("人性化行为决策异常：%s", e)

    # ── 核心循环 ─────────────────────────────────────────────────────────

    def wake(self, chat_key: str):
        cfg = get_config()
        api = cfg.get("api", {})
        if not str(api.get("base_url") or "").strip() or not str(api.get("model") or "").strip():
            return  # 模型未配置：消息保留未读，不产生报错会话

        pending = self.store.peek_unread(chat_key, 200)
        if not pending:
            return

        # ── 人性化行为决策（省 token 规则引擎，不调 LLM）──────────────
        # 收到表情 → 概率收藏；群里有表情时 → 概率回发收藏的表情；新话题 → 概率 @ 活跃成员
        try:
            self._maybe_human_behaviors(chat_key, pending)
        except Exception:
            pass

        # 触发去重：5 分钟内同一批未读消息（上次处理成功过）→ 跳过，防补发唤醒重复发言
        try:
            fp = tuple(str(m.get("mid") or m.get("id") or str(m.get("text") or "")[:24]) for m in pending)
            now = time.time()
            last = self._last_trigger.get(chat_key)
            if last and last[0] == fp and last[2] and (now - last[1]) < 300:
                marked = self.store.mark_all_read(chat_key)
                log.info("%s 同一批消息 5 分钟内已处理过，跳过重复唤醒（标记 %d 条已读）", chat_key, marked)
                return
            self._last_trigger[chat_key] = (fp, now, False)
        except Exception:
            pass

        self_nickname = cfg.get("persona", {}).get("self_nickname") or cfg.get("wechat", {}).get("bot_nickname") or ""
        bot_name = cfg.get("persona", {}).get("bot_name") or ""
        self_id = self.wechat.self_wxid
        # 微信实际昵称（数据库读取，群里 @ 的一般是它）——防止自设自我昵称后漏识别
        try:
            wechat_nick = self.wechat.self_nickname
        except Exception:
            wechat_nick = ""

        # ⚠️ 2026-09-15 修真 bug：这里原来**没传 chat_key/group_name** ⇒ 每群独立档位、
        #   群屏蔽名单、指令禁言在生产路径里从来没生效（自检自己在测试里传了 chat_key，所以一直全绿）。
        try:
            _group_name = self.wechat.group_name(chat_key)
        except Exception:
            _group_name = ""
        tier_result = resolve_context_tier(pending, self_nickname, bot_name, self_id,
                                           wechat_nickname=wechat_nick, chat_key=chat_key,
                                           group_name=_group_name, store=self.store)
        # 内心判断留痕（低成本）：**没回也要留**——"看了但决定不回 + 为什么"正是海龟汤那类玩法最值钱的信息
        try:
            from agent import thought_trace as _tt
            _snip = ""
            try:
                _snip = str((pending[-1] or {}).get("text") or "")[:80]
            except Exception:
                _snip = ""
            _tt.note(chat_key, "tier", tier=tier_result.get("tier"),
                     should=bool(tier_result.get("should_respond")),
                     why=tier_result.get("reason"), src=tier_result.get("tier_source"), snippet=_snip)
        except Exception:
            pass
        if not tier_result["should_respond"]:
            marked = self.store.mark_all_read(chat_key)
            if marked:
                log.info("%s %d 条未命中触发条件（档位 %s），已标记已读、不响应", chat_key, marked, tier_result["reason"])
            return

        trigger = self.store.drain_unread(chat_key)
        if not trigger:
            return

        # 会话级重试：只在一次都没发出过消息时才重试（避免重复发言）
        session = None
        last_error = None
        for attempt in range(3):
            session = {"chat_key": chat_key, "sent": [], "usage": empty_usage(), "past_state_count": 0,
                       "feedbacks": [], "finish_reason": None, "web_search_count": 0, "activity": "",
                       "model": api.get("model"), "prompt_chars": 0}
            try:
                self.run_agent(chat_key, trigger, tier_result, session)
                last_error = None
                break
            except Exception as e:
                last_error = e
                can_retry = attempt < 2 and is_retryable_error(e) and not session["sent"] and not self.stopped
                if not can_retry:
                    break
                wait = 1.0 * (2 ** attempt)
                log.warning("会话 %s 第 %d 次失败（未发出任何消息），%.0fms 后重试：%s", chat_key, attempt + 1, wait * 1000, getattr(e, "message", e))
                time.sleep(wait)
        if last_error:
            log.error("运行 %s 出错: %s", chat_key, getattr(last_error, "message", last_error))
            try:
                _cn = self.wechat.group_name(chat_key.split(":", 1)[1]) if ":" in chat_key else chat_key
                self.session_log.append({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "chat_key": chat_key, "chat_name": _cn,
                    "trigger": "", "reasoning": "", "tools": [], "status": "error", "ok": False,
                    "error": str(getattr(last_error, "message", last_error))[:300]})
            except Exception:
                pass
        else:
            # 处理成功：记录触发指纹，供 5 分钟去重判断
            try:
                self._last_trigger[chat_key] = (self._last_trigger.get(chat_key, ((), 0, False))[0],
                                                time.time(), True)
            except Exception:
                pass

    def run_agent(self, chat_key: str, trigger, tier_result, session: dict, proactive: bool = False):
        cfg = get_config()
        kind, chat_id = chat_key.split(":", 1)
        chat_name = self.wechat.group_name(chat_id) if kind == "group" else chat_id
        # 运行明细：思考过程 / token / 工具调用（控制台「运行明细」）
        _t0 = time.time()
        _entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "chat_key": chat_key, "chat_name": chat_name,
                  "trigger": ("[主动话题]" if proactive else "\n".join(str(t.get("text") or "")[:120] for t in (trigger or []))),
                  "reasoning": "", "tools": [], "status": "running", "ok": None}
        persona = cfg.get("persona", {})
        self_nickname = persona.get("self_nickname") or cfg.get("wechat", {}).get("bot_nickname") or persona.get("bot_name")
        bot_name = persona.get("bot_name")
        self_id = self.wechat.self_wxid

        now = time.time() * 1000
        ten_min_ago = now - 600000
        recent_200 = self.store.recent(chat_key, limit=200)
        recent_count = sum(1 for m in recent_200 if m["ts"] >= ten_min_ago)
        my_msgs = [m for m in self.store.recent(chat_key, limit=100) if m["self"]]
        self_last_message_at = my_msgs[-1]["ts"] if my_msgs else 0
        last_all = self.store.recent(chat_key, limit=10)
        last_message_at = last_all[-1]["ts"] if last_all else now

        system_prompt = build_system_prompt()
        user_prompt = build_user_prompt({
            "chat_key": chat_key, "kind": kind, "chat_id": chat_id, "chat_name": chat_name,
            "trigger_entries": trigger, "store": self.store, "memory": self.memory,
            "self_nickname": self_nickname, "self_last_message_at": self_last_message_at,
            "last_message_at": last_message_at, "recent_count": recent_count,
            "run_seq": 1, "more_unread_during_run": self.store.unread_count(chat_key) > 0,
            "context_limit": tier_result["count"], "session": session, "proactive": proactive,
        })
        session["prompt_chars"] = len(system_prompt) + len(user_prompt)
        session["model"] = cfg.get("api", {}).get("model")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # 工具表按能力裁剪（2026-09-15 省 token：37 个工具 ≈ 7324 token/请求，是最大单块）——
        # 只裁"当前配置/场景下调用必然失败"的，规则与工具内部的可用性判定同源（agent/tools.py）。
        from agent.tools import visible_defs as _visible_defs
        tool_defs, _dropped_tools = _visible_defs(self.tool_defs, cfg, kind=kind)
        openai_tools = to_openai_tools(tool_defs)
        session["tools_dropped"] = _dropped_tools
        if _dropped_tools:
            _entry["tools_dropped"] = [x["name"] for x in _dropped_tools]
            log.info("[%s] 工具表按能力省去 %d 个（给模型 %d 个）：%s", chat_key,
                     len(_dropped_tools), len(tool_defs),
                     "、".join("%s(%s)" % (x["name"], x["why"]) for x in _dropped_tools))

        ctx = {
            "chat_key": chat_key, "kind": kind, "chat_id": chat_id,
            "self_id": self_id, "self_nickname": self_nickname, "bot_name": bot_name,
            "store": self.store, "memory": self.memory, "sender": self.sender,
            "wechat": self.wechat, "session": session,
            "emit": self._emit,
        }

        max_rounds = max(1, int(cfg.get("api", {}).get("max_rounds") or 12))
        finish = False
        for round_no in range(max_rounds):
            if self.stopped:
                break
            session["activity"] = "正在思考…"
            response = chat_completion_with_retry({"messages": messages, "tools": openai_tools})
            session["model"] = response.get("model") or session["model"]
            add_usage(session["usage"], response.get("usage"))
            # ⚠️ 不要在这里再 +1：add_usage() 内部已经 target["calls"] += 1（agent/llm.py:338）。
            #    2026-09-15 修（token 调研第 1 轮 C9）：这一行让 calls 长期虚高一倍
            #    ⇒ usage_stats.json 的 "calls": 248 实际只有 ≈124 步，控制台"调用次数"看着是双倍。
            #    自检：scripts/whale_selftest.py 的「calls 单点来源」（连调两次 add_usage ⇒ calls==2，
            #    且本文件里不得再出现 usage["calls"] += 1）。
            try:
                self.whale.note_call(session["model"], response.get("usage"))
            except Exception:
                pass
            session["activity"] = ""

            msg = response["message"]
            # 记录思考过程（兼容 reasoning_content / reasoning / thinking 字段）
            _reason = (msg.get("reasoning_content") or msg.get("reasoning") or msg.get("thinking") or "")
            if _reason:
                _entry["reasoning"] = (_entry["reasoning"] + "\n" + str(_reason))[:8000]
                # 推理片段也进内心留痕（只在模型真给了推理时；默认省 token 关闭思考 ⇒ 平时没有这段）
                try:
                    from agent import thought_trace as _tt2
                    _tt2.note(chat_key, "reasoning", think=str(_reason)[:400])
                except Exception:
                    pass
            assistant_entry = {"role": "assistant", "content": msg.get("content")}
            if msg.get("tool_calls"):
                assistant_entry["tool_calls"] = msg["tool_calls"]
            messages.append(assistant_entry)
            tool_calls = assistant_entry.get("tool_calls") or []
            content = msg.get("content")

            # 兼容：文本形式的内联工具调用
            if not tool_calls and isinstance(content, str) and content.strip():
                inline = _parse_inline_calls(content)
                if inline:
                    tool_calls = [{"id": "inline_%d_%d" % (round_no, i), "type": "function",
                                   "function": {"name": c["name"], "arguments": json.dumps(c.get("args") or {}, ensure_ascii=False)}}
                                  for i, c in enumerate(inline)]
                    assistant_entry["content"] = None
                    assistant_entry["tool_calls"] = tool_calls

            if not tool_calls:
                _final = str(content or "").strip()
                if _final and not session["sent"]:
                    # 兜底：模型决定「说完就结束」但没调发送工具 → 把最终文本当作回复自动发出，
                    # 避免「想好了却没发出去」的沉默（noreply；曾实测：模型写完回复就结束）。
                    # ⚠️ **必须过滤**：不过滤时"内心分析"会被原样发进群（2026-09-15 加，见 _fallback_send_ok）
                    _ok_fb, _why_fb = _fallback_send_ok(_final, cfg)
                    if not _ok_fb:
                        log.info("[%s] 兜底没发（%s）：%s", chat_key, _why_fb, _final[:60])
                        session["fallback_blocked"] = _why_fb
                        _entry["fallback_blocked"] = _why_fb
                    else:
                        try:
                            res = self.sender.send_text_batch(chat_key, _final)
                            session["sent"].extend(res.get("sent") or [])
                            if res.get("sent"):
                                log.info("[%s] 模型未调发送工具，按最终文本自动补发 %d 条", chat_key, len(res["sent"]))
                        except Exception as e:
                            log.warning("自动补发最终文本失败：%s", e)
                break  # 模型结束思考（不会再调工具）

            tool_results = []
            image_user_msgs = []
            for call in tool_calls:
                fn = call.get("function") or {}
                name = str(fn.get("name") or "")
                args_raw = fn.get("arguments") or "{}"
                if name in ("web_search", "web_fetch"):
                    session["web_search_count"] += 1
                session["activity"] = "正在调用 %s…" % name
                result = execute_tool(tool_defs, ctx, name, args_raw)
                session["activity"] = ""
                _entry["tools"].append({"name": name, "args": str(args_raw)[:180]})

                content_str = ""
                images = []
                rc = result.get("content")
                if isinstance(rc, list):
                    content_str = "\n".join(p["text"] for p in rc if p.get("type") == "text")
                    images = [p for p in rc if p.get("type") == "image_url"]
                else:
                    content_str = str(rc)
                tool_results.append({"role": "tool", "tool_call_id": call.get("id"), "content": content_str})
                if images:
                    image_user_msgs.append({
                        "role": "user",
                        "content": [{"type": "text", "text": "[系统：以下是工具 %s 返回的 %d 张图片，请直接\"看图\"回应]" % (name, len(images))}] + images,
                    })
                if name == "finish":
                    finish = True

            messages.extend(tool_results)
            messages.extend(image_user_msgs)

        status = "done" if session["sent"] else "noreply"
        self.stats["sessions"] += 1
        self.stats["tokens"] += int(session["usage"]["total_tokens"])
        self.stats["sent"] += len(session["sent"])
        self.stats["calls"] += int(session["usage"]["calls"])
        _cost = 0.0
        try:
            _cost = float(estimate_cost(session["usage"], session["model"])["cost"])
            self.stats["cost"] += _cost
        except Exception:
            pass
        # 持久化用量（累计保留 + 按周期重置）与运行明细
        # 三分口径（2026-09-15）：`fresh`＝未命中（全价）、`cached`＝缓存读（1/30 价）、`output`＝输出。
        # 只记总量会把"省 token"和"省钱"混为一谈（两者相差 30~90 倍），控制台要能分开看。
        _u = session["usage"]
        _cached = int(_u.get("cached_tokens") or 0)
        _fresh = max(0, int(_u.get("prompt_tokens") or 0) - _cached)
        _output = int(_u.get("completion_tokens") or 0) + int(_u.get("reasoning_tokens") or 0)
        self.stats_store.record(sessions=1, calls=int(_u["calls"]),
                                tokens=int(_u["total_tokens"]),
                                sent=len(session["sent"]), cost=_cost,
                                fresh=_fresh, cached=_cached, output=_output)
        _entry.update({
            "status": status, "ok": status == "done",
            "latency_ms": int((time.time() - _t0) * 1000),
            "tokens": int(session["usage"]["total_tokens"]),
            "reasoning_tokens": int(session["usage"].get("reasoning_tokens") or 0),
            "cost": round(_cost, 6), "calls": int(session["usage"]["calls"]),
            "model": session.get("model") or "",
            "reply": "\n".join(str(x.get("text") or "")[:200] for x in session["sent"])[:1200],
        })
        self.session_log.append(_entry)
        try:
            self.whale.note_turn_done()
        except Exception:
            pass
        log.info("[%s] 结束（%s）：发 %d 条 / 工具 %d 轮 / 联网 %d 次 / token=%s",
                 chat_name or chat_key, status, len(session["sent"]),
                 session["usage"]["calls"], session["web_search_count"],
                 session["usage"]["total_tokens"])

    def _emit(self, event_type: str, payload):
        if event_type == "feedback":
            log.warning("[feedback][%s] %s: %s", payload.get("chat_key"), payload.get("level"), payload.get("message"))

    # ── 记忆自动整理 ─────────────────────────────────────────────────────

    def _maybe_consolidate(self, chat_key: str):
        try:
            cfg = get_config()
            mem_cfg = cfg.get("memory", {})
            if mem_cfg.get("consolidate_enabled") is False or self.paused or self.stopped:
                return
            if not (cfg.get("api", {}).get("model") and cfg.get("api", {}).get("base_url")):
                return
            if chat_key in self._consolidating:
                return
            state = self.memory.consolidation_state(chat_key)
            min_impressions = max(1, int(mem_cfg.get("consolidate_min_impressions") or 4))
            max_per_member = max(2, int(mem_cfg.get("max_impressions_per_member") or 5))
            overloaded = any(m["count"] > max_per_member for m in state["members"])
            if state["counts"]["memberImpression"] <= min_impressions and not overloaded:
                return
            min_interval = max(30 * 60 * 1000, int(mem_cfg.get("consolidate_min_interval_ms") or 6 * 60 * 60 * 1000))
            if (time.time() * 1000) - (state.get("lastConsolidatedAt") or 0) < min_interval:
                return
            self._consolidating.add(chat_key)
            self._executor.submit(self._consolidate_worker, chat_key)
        except Exception as e:
            log.debug("记忆整理判定异常（不影响聊天）: %s", e)

    def _consolidate_worker(self, chat_key: str):
        try:
            self.consolidate_chat(chat_key)
        except Exception as e:
            log.error("整理 %s 失败: %s", chat_key, e)
        finally:
            self._consolidating.discard(chat_key)

    def consolidate_chat(self, chat_key: str):
        """整理群友印象（合并重复、删过时；发现活跃但零印象的新人）。"""
        cfg = get_config()
        max_keep = int(cfg.get("memory", {}).get("max_impressions_per_member") or 5)
        members = self.memory.members(chat_key)
        known = {str(m["userId"]) for m in members if m["userId"]}
        # 发现新人
        discover_min = max(1, int(cfg.get("memory", {}).get("discover_min_messages") or 20))
        discover_max = max(1, int(cfg.get("memory", {}).get("discover_max_members") or 3))
        msg_count = {}
        name_map = {}
        for m in self.store.recent(chat_key, limit=2000):
            if m["self"] or not m.get("sender_id"):
                continue
            uid = str(m["sender_id"])
            msg_count[uid] = msg_count.get(uid, 0) + 1
            if m.get("sender_name"):
                name_map.setdefault(uid, m["sender_name"])
        discovered = [uid for uid, n in sorted(msg_count.items(), key=lambda kv: -kv[1])
                      if n >= discover_min and uid not in known][:discover_max]

        for uid in discovered:
            sample = [str(m["text"])[:200] for m in self.store.recent(chat_key, limit=2000)
                      if (not m["self"]) and str(m.get("sender_id")) == uid and m.get("text")][-40:]
            _think = ""
            try:
                from agent import thought_trace as _tt3
                _think = _tt3.format_for_memory(chat_key, 12)
            except Exception:
                _think = ""
            imp = self._extract_impressions(
                "你是聊天机器人的记忆模块，负责从聊天记录里提炼对某一位群友的长期印象。"
                "只提炼\"以后跟这个人打交道用得上\"的稳定特征，严格依据给定的发言，不要编造。"
                "输出必须是严格的 JSON 对象，格式：{\"impressions\":[\"…\"]}。每条不超过 120 字，宁少勿错。",
                ("\n".join(sample) if sample else "（没有抓到该群友的发言）")
                + (("\n\n【机器人的内心判断（没发出去的也算，别当成群友发言）】\n" + _think) if _think else ""),
                max_keep)
            if imp:
                self.memory.replace_member(chat_key, uid, name_map.get(uid, ""), imp)

        for mem in members:
            if not mem["userId"]:
                continue
            lines = ["群友 wxid：%s" % mem["userId"], "当前名字：%s" % mem["name"]]
            for e in mem["impressions"]:
                lines.append("- %s" % e["content"])
            imp = self._extract_impressions(
                "你是聊天机器人的记忆整理模块，负责整理对某一位群友的长期印象。你只做合并、改写与删除，绝不发明任何新事实。"
                "输出必须是严格的 JSON 对象，格式：{\"impressions\":[\"…\"]}。最多保留 %d 条，每条不超过 120 字，宁少勿错。" % max_keep,
                "\n".join(lines), max_keep)
            if imp is not None and len(imp) <= len(mem["impressions"]):
                self.memory.replace_member(chat_key, mem["userId"], mem["name"], imp)
        self.memory.mark_consolidated(chat_key)
        log.info("[%s] 记忆整理完成：%d 位成员 + 发现 %d 位新人", chat_key, len(members), len(discovered))

    def _extract_impressions(self, system: str, user: str, max_keep: int):
        try:
            resp = chat_completion([{"role": "system", "content": system},
                                    {"role": "user", "content": user}], temperature=0.2)
            content = str(resp.get("message", {}).get("content") or "")
            m = re.search(r"\{[\s\S]*\}", content)
            obj = json.loads(m.group(0)) if m else None
            if not obj:
                return None
            raw = obj.get("impressions") or []
            if not isinstance(raw, list):
                return None
            return [str(s or "").strip()[:120] for s in raw if str(s or "").strip()][:max_keep]
        except Exception as e:
            log.warning("记忆整理调用失败：%s", e)
            return None

    # ── 控制 ─────────────────────────────────────────────────────────────

    def set_paused(self, paused: bool):
        self.paused = bool(paused)
        log.info("机器人已%s", "暂停" if self.paused else "恢复")

    def shutdown(self):
        self.stopped = True
        with self._lock:
            for t in self.wake_timers.values():
                t.cancel()
            self.wake_timers.clear()
        try:
            if self._proactive_timer:
                self._proactive_timer.cancel()
                self._proactive_timer = None
        except Exception:
            pass
        self._executor.shutdown(wait=False, cancel_futures=True)


def _check_prerequisites(cfg) -> list:
    problems = []
    api = cfg.get("api", {})
    if not str(api.get("base_url") or "").strip():
        problems.append("未配置 api.base_url")
    if not str(api.get("model") or "").strip():
        problems.append("未配置 api.model")
    key = str(api.get("api_key") or "").strip()
    if not key or "在这里填" in key:
        problems.append("未配置有效的 api.api_key（还是占位符）")
    return problems


def _kill_watchdog():
    """结束看门狗（data 目录下 watchdog.pid 优先，wmic 按命令行回退）。

    控制台「停止/重启」必须连看门狗一起处理：否则机器人退出 5 秒后会被看门狗重新拉起，
    表现为「点了停止却又弹出一个新控制台」。
    """
    wp = os.path.join(ROOT, "data", "watchdog.pid")
    try:
        if os.path.exists(wp):
            with open(wp, "r", encoding="utf-8") as f:
                pid = int(f.read().strip())
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           creationflags=0x08000000, timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                os.remove(wp)
            except Exception:
                pass
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["wmic", "process", "where",
             "name like 'python%.exe' and commandline like '%watchdog.py%'",
             "get", "processid", "/value"],
            capture_output=True, creationflags=0x08000000, timeout=15)
        for line in r.stdout.decode("utf-8", "ignore").splitlines():
            line = line.strip()
            if line.startswith("ProcessId="):
                v = line.split("=", 1)[1].strip()
                if v.isdigit() and int(v) > 0:
                    try:
                        subprocess.run(["taskkill", "/F", "/T", "/PID", v],
                                       creationflags=0x08000000, timeout=10,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except Exception:
                        pass
    except Exception:
        pass


def _spawn_watchdog(delay: int = 0):
    """隐藏拉起新看门狗（pythonw 运行 scripts/watchdog.py，等价 启动机器人.vbs）。

    `delay`（秒）＝**让新看门狗晚一点再开机器人**。重启那一跳必须给（用 6 秒）：本进程还要 2 秒
    才退，新机器人要是立刻起来就会撞**单实例锁**当场 `exit 3`（用户报「从来没见过它再起一个」）。
    ⚠️ 别想着"用 Timer 等 6 秒再拉看门狗"——本进程 2 秒后就 `os._exit` 了，**那个 Timer 永远不会触发**
    （2026-09-17 我第一版就是这么错的，活体自检当场抓到"旧退了、新没接上"）。
    """
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        pyw = exe[:-10] + "pythonw.exe"
        if os.path.exists(pyw):
            exe = pyw
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED|CREATE_NEW_PROCESS_GROUP|CREATE_NO_WINDOW
    cmd = [exe, os.path.join(ROOT, "scripts", "watchdog.py")]
    if int(delay or 0) > 0:
        cmd.append("--delay=%d" % int(delay))
    subprocess.Popen(cmd, cwd=ROOT, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _poke_name_of(nm: dict) -> str:
    """从 [拍一拍]（名字）文本里取出拍者名字。"""
    t = str(nm.get("text") or "")
    m = re.search(r"（([^）]+)）", t)
    return m.group(1).strip() if m else ""


def _schedule_poke_back(wechat, store, chat_key: str, chat_id: str, group_name: str, nm: dict):
    """别人拍了机器人 → 延迟 ~18 秒后触发回拍。

    延迟是为了让模型先自然回应一句，并保证拍一拍事件先落库；
    概率（默认 90%）与冷却（默认 30 分钟）由 try_send_poke_back 内统一判定，
    这里只负责延迟调度；结果只写日志（poker 自己能看到被回拍），不对群发言。
    """
    try:
        delay_s = max(1.0, float(get_config().get("poke", {}).get("delay_seconds", 18.0) or 18.0))
        poker_name = _poke_name_of(nm)
        poker_wxid = str(nm.get("poker_wxid") or "")
        if not poker_wxid:
            # 系统消息路径拿不到 wxid：按名字在活跃成员里反查
            for m in store.active_members(chat_key, 20):
                if m.get("name") and m["name"] == poker_name:
                    poker_wxid = m["user_id"]
                    break
        # 自己拍的（回读回声）不回拍
        if poker_wxid and poker_wxid == wechat._self_wxid:
            return
        if poker_name and wechat._self_nickname and poker_name == wechat._self_nickname:
            return
        if not poker_wxid:
            log.debug("拍者 %r 未找到 wxid，跳过回拍", poker_name)
            return

        def _do():
            try:
                ok, msg = wechat.try_send_poke_back(chat_id, poker_name, poker_wxid)
                log.info("%s → 回拍「%s」：%s", group_name, poker_name, msg)
            except Exception as e:
                log.warning("回拍异常：%s", e)

        t = threading.Timer(delay_s, _do)
        t.daemon = True
        t.start()
    except Exception as e:
        log.debug("调度回拍失败：%s", e)


def _version_issues() -> list:
    """启动体检：返回"版本不匹配"的问题清单（空 = 全部匹配）。

    覆盖：微信本体（需 4.x）、适配层 wechatauto-replica（需 ≥1.1.5.1，本仓库实测 1.2.2.2）、关键依赖最低版本。
    """
    issues = []
    try:
        from agent.wechat import dep_check, wechat_version_info
        info = wechat_version_info()
        if not info.get("supported"):
            issues.append("微信版本 %s 不受支持（需要微信 4.x）" % (info.get("version") or "未检测到"))
        rows, _ = dep_check()
        bad = [r for r in rows if not r[3]]
        if any(r[0] == "wechatauto-replica" for r in bad):
            issues.append("适配层 wechatauto-replica 需 ≥1.1.5.1（本仓库实测 1.2.2.2；当前 %s）" % (info.get("adapter") or "未安装"))
        for r in bad:
            if r[0] == "wechatauto-replica":
                continue
            issues.append("%s 版本偏旧（已装 %s，需 ≥ %s）" % (r[0], r[1] or "未安装", r[2]))
    except Exception:
        pass
    return issues


def _maybe_auto_fix():
    """启动时自动版本体检：只记录日志（不弹窗——pythonw 下 MessageBox 会卡死启动，
    用户可能看不到；升级走控制台/说明文档，控制台永远先弹出来）。"""
    try:
        issues = _version_issues()
        if not issues:
            return
        tip = "\n".join("- " + s for s in issues)
        log.warning("启动体检发现版本不匹配（不影响启动，可稍后升级）：%s", tip.replace("\n", "；"))
    except Exception as e:
        log.warning("自动版本体检失效（不影响启动）：%s", e)


# ── 「微信连不上」的原因要看得见（2026-09-16 已知现象：「微信连接不上」）───────────────
#   老实现只有一个是/否（`wechat is not None`）：用户看到"微信未连接"却不知道**为什么**，
#   我们也只能来回猜、来回问。⇒ 每次接入（成功或失败）都把**逐步诊断**记在这里，
#   控制台侧栏显示一行短原因（悬停看全文），反馈诊断包带上完整 steps。
_ATTACH = {"tries": 0, "at": 0.0, "err": "", "diag": None}


def _attach_wechat(cfg):
    """接入微信；失败**不抛**，把原因与逐步诊断记进 `_ATTACH` 并返回 None。

    可重复调用（启动一次；监听循环里没接上时每 10 秒再试一次）——这就是老代码注释里
    承诺、而实际**从未实现**的那句"监听循环内每 10 秒由主线程重试接入"。
    """
    _ATTACH["tries"] += 1
    _ATTACH["at"] = time.time()
    try:
        if os.environ.get("WXAGENT_NO_WECHAT") == "1":
            raise RuntimeError("WXAGENT_NO_WECHAT（测试开关）")
        wc = WeChatAdapter(cfg)
        _ATTACH["err"] = ""
        _ATTACH["diag"] = None
        return wc
    except BaseException as e:
        _ATTACH["err"] = "%s【%s】" % (str(e)[:120], type(e).__name__)
        try:
            _ATTACH["diag"] = attach_diagnosis(err=_ATTACH["err"])
        except Exception as _e2:
            _ATTACH["diag"] = {"ok": False, "step": "unknown",
                               "reason": "诊断本身失败：%s" % _e2, "action": "retry", "steps": []}
        return None


def wechat_attach_status() -> dict:
    """给控制台/诊断包用的接入状态（纯读内存里的 `_ATTACH`，不做任何探测）。"""
    d = _ATTACH.get("diag") or {}
    return {"tries": int(_ATTACH.get("tries") or 0),
            "err": str(_ATTACH.get("err") or ""),
            "reason": str(d.get("reason") or ""),
            "short": attach_short_reason(d) if d else "",
            "step": str(d.get("step") or ""),
            "action": str(d.get("action") or ""),
            "steps": list(d.get("steps") or [])}


def _wechat_watchdog(get_wc):
    """微信守护线程：每 30 秒检测微信主窗口是否响应。

    连续 2 次检测无响应（SendMessageTimeout 超时）＝判定卡死：
      自动 taskkill 微信 → 2 秒后重新拉起微信程序 → 弹窗叫用户重新登录 → 写日志。
    微信恢复后机器人轮询自动继续（不重启机器人本体）。
    ⚠️ 句柄**每跳现取**（`get_wc` 可以是 callable）：启动那一刻微信没开时这里是 None，
    传死了 None 会让守护**永远空转**（老代码就是这么写的）。
    """
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    SMTO_ABORTIFHUNG, WM_NULL = 0x0002, 0x0000
    hung = 0
    while True:
        time.sleep(30)
        try:
            wechat = get_wc() if callable(get_wc) else get_wc
            if wechat is None:
                continue
            gui = wechat._get_gui()
            hwnd = int(getattr(gui, "main_hwnd", 0) or 0) if gui else 0
            if not hwnd or not _user32_is_visible(hwnd):
                hung = 0  # 窗口不可见/未登录属于正常态，不判卡死
                continue
            # ⛔ 2026-09-17 **红线修复**：必须声明 argtypes 并传第 7 个参数 `lpdwResult`。
            #   原来只传 6 个参数（少一个出参指针），也没声明 argtypes ⇒ ctypes 把 HWND/LPARAM
            #   当 32 位 int 传、把出参指针当 NULL 传。实测后果：机器人在**启动后第 31 秒**
            #   （＝本守护第一跳）**原生崩溃退出**，退出码 **0xC0000409**
            #   （STATUS_STACK_BUFFER_OVERRUN / __fastfail），日志里一句话都没有 ⇒ 看门狗
            #   每 37 秒重拉一次、每拉一次多开一个「群相 控制台」窗口，控制台越堆越多。
            user32.SendMessageTimeoutW.argtypes = [
                wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                wintypes.UINT, wintypes.UINT, ctypes.POINTER(wintypes.DWORD)]
            user32.SendMessageTimeoutW.restype = wintypes.LPARAM
            _out = wintypes.DWORD(0)
            res = user32.SendMessageTimeoutW(hwnd, WM_NULL, 0, 0, SMTO_ABORTIFHUNG, 500,
                                             ctypes.byref(_out))
            if res != 0:
                hung = 0
                continue
            hung += 1
            if hung < 2:
                continue
            hung = 0
            log.error("微信主窗口无响应（疑似卡死），自动关闭并重启微信 …")
            try:
                subprocess.run(["taskkill", "/F", "/IM", "Weixin.exe"],
                               creationflags=0x08000000, timeout=15,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
            time.sleep(2)
            try:
                exe = wechat_version_info().get("path")
                if exe:
                    flags = 0x00000008 | 0x00000200 | 0x08000000 if os.name == "nt" else 0
                    subprocess.Popen([exe], creationflags=flags)
            except Exception as e:
                log.warning("重启微信失败：%s", e)
            try:
                user32.MessageBoxW(None,
                                   "检测到微信卡死，已自动关闭并重新启动微信程序。\n\n"
                                   "请到微信窗口重新登录（扫码/确认登录）——机器人会自动恢复连接。",
                                   "Persona Morph 微信守护", 0x30)
            except Exception:
                pass
        except Exception as e:
            log.debug("微信守护异常：%s", e)


#: 记账钩子：main() 启动时装上真实的 _tool_llm_count；模块级评分函数不依赖 main 的局部变量
_LLM_COUNT_HOOK = [lambda usage, system="": None]


def persona_llm_score(card, name=""):
    """统一严格评分器（唯一权威细则 RULES_TEXT，含贴合度判定/网梗重罚/精度铁律/从严基线/缺陷压分）。
    返回 {ok, score, dims, reason, content} 或 {ok:False, error}。"""
    try:
        from agent.persona_rating import WEIGHTS as _W2, RULES_TEXT, compute as _compute
        from agent.llm import chat_completion
        # 联网检索真实资料（评分参考：贴合度应以真实言论/事迹为准，严禁以编造内容评价为贴合）
        _wn = ""
        try:
            from agent import web_search as _ws2
            _its = []
            try:
                _r = _ws2.web_search(str(name) + " 经典语录 名言")
                _its += (_r or {}).get("results") or (_r or {}).get("items") or []
            except Exception:
                pass
            _ls = []
            for _it in _its[:8]:
                _seg = str(_it.get("snippet") or "").strip()
                if _seg:
                    _ls.append(_seg[:160])
            if _ls:
                _wn = "\n".join("· " + l for l in _ls[:8])
        except Exception:
            pass
        prompt = (
            "你是角色设定严格评审员。按细则给分（细则如下），每维 0~100.00（精确 0.01）。\n"
            + RULES_TEXT +
            "\n以下是从网络检索到的该角色真实资料（权威事实来源）：\n"
            + (_wn or "（未检索到第一手资料——贴合度按卡片自洽与口吻判断，严禁把编造内容当贴合）")
            + "\n「贴合度」评分必须以真实资料比对（卡里出现真实资料之外的编造台词/事迹 → 贴合度≤30）；真实资料里有的细节卡里缺失 → 按细则正常压分。\n"
            "\n第一行输出：{\"dims\":{\"style\":<估>,\"fit\":<估>,\"coher\":<估>,\"natural\":<估>,\"usable\":<估>}}"
            "（0.01 精度，如 84.37）\n"
            "第二行输出：{\"reason\":\"一句话指出人设层面最大缺点（必须针对该卡）\"}\n"
            "重要：数值必须根据卡片内容独立评估（0.01 精度），禁止整十/整五整分，禁止抄示例；"
            "评语指出的缺陷必须如实压分。\n\n"
            "角色名：%s\n角色设定卡(节选 2600 字)：\n%s" % (name or "（未署名）", str(card or "")[:2600])
        )
        r = chat_completion([{"role": "user", "content": prompt}])
        _LLM_COUNT_HOOK[0](r.get("usage"))
        content = (r.get("message") or {}).get("content") or ""
        import json as _json
        import re as _re
        m = _re.search(r'"dims"\s*:\s*\{([^}]*)\}', content, _re.S)
        rm = _re.search(r'"reason"\s*:\s*"([^"]*)"', content, _re.S)
        if not m:
            return {"ok": False, "error": "模型未输出维度分：" + content[:120]}
        d = {}
        for pair in _re.findall(r'"(\w+)"\s*:\s*([\d.]+)', m.group(1)):
            d[pair[0]] = float(pair[1])
        if not d:
            return {"ok": False, "error": "模型未输出维度分：" + content[:120]}
        score = round(_compute(d), 2)
        return {"ok": True, "score": score,
                "dims": {k: round(float(d.get(k, 0)), 2) for k in _W2},
                "reason": (rm.group(1) if rm else "（模型未给出原因）")[:200],
                "content": content}
    except Exception as e:
        return {"ok": False, "error": str(e)[:150]}


def main():
    # 注意：不要在 pythonw 下调用 os.system("chcp")——会弹出控制台窗口（闪窗）。
    # 代码已用 UTF-8 模式运行（-X utf8 / 编码头），无需 chcp。

    # ── 单实例锁：防止旧进程/多实例并存（根治「旧版本界面/接口 not found」）──
    #    自检＝命名互斥体（进程一死由内核释放 ⇒ 不怕 pid 复用、不怕残留文件、不怕同时启动）；
    #    data\bot.lock 降级为「给人看 + 旧读取方兼容」的证据，内容仍是纯 pid。
    #    拿不到锁就**拒启动**（旧实现整段 try/except pass ⇒ 出错时静默没有锁）。
    import atexit
    from agent.single_instance import InstanceLock, legacy_holder
    _bot_lock_path = os.path.join(ROOT, "data", "bot.lock")
    # ⛔ 2026-09-17（用户报「更新后的重启，从来没见过它再起一个新的」）：给"接手"留一点时间 ——
    #   更新/重启那一跳里旧实例可能**正在退出**（单实例锁与 bot.lock 都还没放开），老实现一撞上就
    #   `sys.exit(3)`，于是看门狗每 5 秒起一次、次次报"已有实例在运行"，永远换不上新的那一版。
    #   现在最多等 12 秒：真有人正经在跑，也只是多等这一下再报冲突（照旧拒启动，不会双开）。
    _gate_t0 = time.time()
    _legacy_pid = legacy_holder(_bot_lock_path)
    while _legacy_pid and (time.time() - _gate_t0) < 12.0:
        log.warning("旧实例（pid=%s）像是正在退出，等它放开锁再接手…", _legacy_pid)
        time.sleep(1.5)
        _legacy_pid = legacy_holder(_bot_lock_path)
    if _legacy_pid:
        log.error("已有 Persona Morph 实例在运行（pid=%s；旧版实例只写 pid 文件、没有互斥体）。"
                  "为避免旧版本/接口冲突，本实例退出；请先「停止机器人」再启动。"
                  "若确认没有实例在跑，删掉 data\\bot.lock 后重试。", _legacy_pid)
        print("已有 Persona Morph 实例在运行（pid=%s）。本实例退出；请先停止旧实例再启动。" % _legacy_pid)
        sys.exit(3)
    _bot_lock = InstanceLock(lock_path=_bot_lock_path)
    _lock_res = _bot_lock.acquire()
    while (not _lock_res.ok) and (time.time() - _gate_t0) < 12.0:
        log.warning("单实例锁还被占着（%s），等它放开再接手…", _lock_res.reason)
        time.sleep(1.5)
        _lock_res = _bot_lock.acquire()
    if not _lock_res.ok:
        _who = ("pid=%s" % _lock_res.holder_pid) if _lock_res.holder_pid else "pid 未知"
        log.error("已有 Persona Morph 实例在运行（%s）。为避免旧版本/接口冲突，本实例退出；请先「停止机器人」再启动。（%s）",
                  _who, _lock_res.reason)
        print("已有 Persona Morph 实例在运行（%s）。本实例退出；请先停止旧实例再启动。" % _who)
        sys.exit(3)
    if _lock_res.note:
        log.warning("单实例锁：%s", _lock_res.note)

    # ── 磁盘收尾：清系统临时目录里我们自己的残留 + 收 TTS 产物（用户 2026-09-15 问
    #    「下载下来不占用户存储空间吗？有没有做好删除措施」后落地；只清 `pm-` 前缀，
    #    十分钟内的文件一律不动，删了几个/回收多少 MB 都记日志）。失败不影响启动。──
    try:
        from agent import housekeeping as _hk
        _rep = _hk.tick()
        log.info(_hk.brief(_rep))
    except Exception as _e:
        log.warning("磁盘收尾跳过（不影响启动）：%s", str(_e)[:80])
    atexit.register(_bot_lock.release)

    # 启动自动体检：版本不匹配（微信/适配层/依赖）→ 弹窗询问是否立即修正
    _maybe_auto_fix()

    # 写入 PID 文件，供「停止机器人」按 PID 无窗口结束（零 PowerShell 依赖）
    _pid_file = os.path.join(ROOT, "data", "bot.pid")
    try:
        os.makedirs(os.path.dirname(_pid_file), exist_ok=True)
        with open(_pid_file, "w", encoding="utf-8") as _f:
            _f.write(str(os.getpid()))
        import atexit
        atexit.register(lambda: os.path.exists(_pid_file) and os.remove(_pid_file))
    except Exception:
        pass

    cfg = get_config()
    log.info("===== Persona Morph 启动 =====")
    log.info("[checkpoint] 配置与就绪检查…")
    problems = _check_prerequisites(cfg)
    if problems:
        log.warning("就绪度体检未通过：%s（机器人会读消息但不调用模型，配置好 config.json 后重启）", "；".join(problems))


    # 微信接入：主线程同步构造（微信在线=0 秒；同线程使用保证 UIA/COM 不锁）。
    # 失败则仅警告继续（控制台先行）；监听循环内每 10 秒由主线程重试接入。
    wechat_box = [None]
    wechat_box[0] = _attach_wechat(cfg)
    if wechat_box[0] is not None:
        log.info("微信接入成功（主线程同步）")
    else:
        log.warning("微信暂未接入（控制台仍打开；监听循环内每 10 秒重试接入）：%s"
                    "｜诊断：%s", _ATTACH["err"], wechat_attach_status()["reason"])
    wechat = wechat_box[0]
    log.info("[checkpoint] 微信段:结束(零等待) wechat=%s", bool(wechat))

    try:
        _wv = wechat_version_info()
        log.info("微信版本：%s", _wv["detail"])
    except Exception:
        pass

    try:
        groups = wechat.list_groups() if wechat else []
    except Exception as e:
        log.warning("list_groups 失败：%s", e)
        groups = []
    # 微信卡死守护：无响应自动关闭重启 + 弹窗叫用户重新登录
    try:
        threading.Thread(target=_wechat_watchdog, args=(lambda: wechat_box[0],), daemon=True).start()
        log.info("微信守护已启动（30 秒心跳，卡死自动重启并提醒登录）")
    except Exception as e:
        log.warning("微信守护启动失败：%s", e)
    whitelist = cfg.get("wechat", {}).get("group_name_white_list") or []
    deny = set(cfg.get("deny", {}).get("groups") or [])
    targets = [g for g in groups if (not whitelist or g["name"] in whitelist) and g["name"] not in deny]
    log.info("目标群 %d 个：%s", len(targets), ", ".join(g["name"] for g in targets[:15]) if targets else "（白名单未匹配到任何群）")
    # 私聊目标（2026-09-16 用户：「大号跟小号对谈，相当于借一个智能体进来跟自己聊天」）：
    # 群那份逻辑一个字不动，这里是**追加**；档位见 config 的 wechat.private_chat。
    _pt = []
    try:
        if wechat is not None:
            _pt = wechat.list_private_targets()
    except Exception as e:
        log.warning("私聊目标发现失败（不影响群）：%s", e)
    _pmode = str((cfg.get("wechat", {}) or {}).get("private_chat") or "owner_only")
    if _pt:
        targets += [{"name": c.get("name") or c.get("wxid"), "wxid": c.get("wxid")} for c in _pt]
        log.info("私聊目标 %d 个（档位 %s）：%s", len(_pt), _pmode,
                 ", ".join((c.get("name") or c.get("wxid") or "") for c in _pt[:10]))
    elif _pmode == "owner_only":
        log.info("私聊档位=owner_only，但没登记「我的其他账号」或没匹配到 ⇒ 不监听任何私聊"
                 "（在控制台「微信」面板登记你的大号即可）")
    elif _pmode == "off":
        log.info("私聊档位=off ⇒ 不监听私聊")
    log.info("监听目标合计 %d 个（群 %d + 私聊 %d）", len(targets), len(targets) - len(_pt), len(_pt))

    def _collect_targets(wc):
        """按**当前配置**重算 (全部群, 监听目标)。启动、晚接入、配置保存后共用一份，避免三处漂移。

        ⚠️ 2026-09-17 修（网友报「我把群勾选了…概览的状态改变不了」）：这里原来读的是**启动时**的
        闭包变量 `whitelist` / `deny` / `_pmode` ⇒ 保存配置后即使重算也还是老口径（等于白算）。
        现在一律现读配置 —— 改了勾选就能立刻生效，不用重启。
        """
        try:
            _cfg_now = get_config() or {}
        except Exception:
            _cfg_now = {}
        _wl = (_cfg_now.get("wechat") or {}).get("group_name_white_list") or []
        _deny = set((_cfg_now.get("deny") or {}).get("groups") or [])
        _mode = str((_cfg_now.get("wechat") or {}).get("private_chat") or "owner_only")
        try:
            _gs = wc.list_groups() or []
        except Exception as _e:
            log.warning("取群列表失败：%s", _e)
            _gs = []
        _t = [g for g in _gs if (not _wl or g["name"] in _wl) and g["name"] not in _deny]
        try:
            _p2 = [] if _mode == "off" else (wc.list_private_targets() or [])
        except Exception as _e:
            log.warning("私聊目标发现失败（不影响群）：%s", _e)
            _p2 = []
        if _p2:
            _t += [{"name": c.get("name") or c.get("wxid"), "wxid": c.get("wxid")} for c in _p2]
        return _gs, _t

    def _refresh_targets(why=""):
        """按当前配置**就地刷新**监听目标（配置保存后也走这里）。

        为什么（2026-09-17 网友报「改了群勾选，概览那个状态改变不了」）：`status_provider` 是按
        `groups` + `target_wxids` 算「这个群是不是监听目标」的，而这两样原来只在启动/晚接入时算过
        ⇒ 保存新白名单后，概览与推送目标都还是老的（看着像"锁在那个状态"）。
        """
        nonlocal groups, targets
        _wc = wechat_box[0] if wechat_box else None
        if _wc is None:
            return
        try:
            groups, targets = _collect_targets(_wc)
            target_wxids.clear()
            target_wxids.update(g["wxid"] for g in targets if g.get("wxid"))
            log.info("监听目标已按新配置重算（%s）：群 %d · 合计 %d", why or "配置变更", len(groups), len(targets))
        except Exception as e:
            log.warning("重算监听目标失败（%s）：%s", why, e)

    store = ChatStore(int(cfg.get("store", {}).get("max_messages_per_chat") or 0))
    memory = MemoryStore()
    sender = SendQueue(None if wechat is None else wechat, store)
    orch = Orchestrator(store, memory, sender, wechat)

    # 启动后默认暂停：不监听群消息，控制台点「恢复」才工作（防一开机就刷群/回应积压旧消息）
    if (cfg.get("wechat") or {}).get("start_paused", True) is not False:
        orch.set_paused(True)
        log.info("启动后默认暂停：请在控制台点「恢复」开始监听群消息（wechat.start_paused=false 可改为自动运行）")

    # ── Web 控制台 ─────────────────────────────────────────────────────
    target_wxids = {g["wxid"] for g in targets}

    def _tool_llm_count(usage, system=""):
        """把工具类 LLM 调用（评分/补足/总结/测试等任一 LLM 调用）记入 tool_usage.json（其它工具消耗）。"""
        try:
            import json as _json
            u = usage or {}
            toks = int(u.get("total_tokens") or u.get("prompt_tokens") or 0)
            if not toks:
                return
            cost = 0.0
            try:
                ptok = int(u.get("prompt_tokens") or 0)
                ctok = int(u.get("completion_tokens") or 0)
                cost = (ptok * 0.000002 + ctok * 0.000008)
            except Exception:
                pass
            p = os.path.join(ROOT, "data", "tool_usage.json")
            try:
                with open(p, "r", encoding="utf-8") as f:
                    d = _json.load(f)
            except Exception:
                d = {}
            if not d.get("base_cost"):
                # 首次记账（本进程启动后第一条）→ 基准=这条之前的值（0）
                d["base_cost"] = 0.0
                d["base_tokens"] = 0
            d["tokens"] = int(d.get("tokens") or 0) + toks
            d["cost"] = round(float(d.get("cost") or 0) + cost, 4)
            d["n"] = int(d.get("n") or 0) + 1
            d["last"] = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(p, "w", encoding="utf-8") as f:
                _json.dump(d, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    # 注册全局用量记账：任何 LLM 调用（评分/补足/摘要/测试…）都进"其它工具消耗"（本次=本进程启动以来）
    from agent.llm import set_usage_hook as _set_hook
    _set_hook(_tool_llm_count)
    log.info("工具类消耗记账已启用（其它工具成本）")

    def status_provider():
        _cfg_live = get_config()
        gs = [{"name": g["name"], "wxid": g["wxid"], "target": g["wxid"] in target_wxids} for g in groups]
        if not gs:
            # 微信未接入时回退：白名单/存档群名（保证群名区可显示）
            try:
                wl = _cfg_live.get("wechat", {}).get("group_name_white_list") or []
                gs = [{"name": str(n), "wxid": "", "target": True} for n in wl]
            except Exception:
                pass
        st = dict(orch.stats)
        # 成本明细：最近 5 条 / 平均每条（从会话记录真实计算；无记录=0）
        try:
            import glob as _glob
            import json as _json
            costs = []
            for fp in _glob.glob(os.path.join(ROOT, "data", "sessions", "*.jsonl")):
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            try:
                                row = _json.loads(line)
                                c = float(row.get("cost") or row.get("tokens_cost") or 0)
                                t = int(row.get("tokens") or 0)
                                if c > 0 or t > 0:
                                    costs.append((c, t))
                            except Exception:
                                pass
                except Exception:
                    pass
            recent = costs[-5:]
            st["recent5_cost"] = round(sum(c[0] for c in recent), 4)
            st["recent5_tokens"] = sum(c[1] for c in recent)
            st["avg_cost"] = round(sum(c[0] for c in costs) / len(costs), 4) if costs else 0.0
            st["sessions_n"] = len(costs)
            st["extra_cost"] = round(max(0.0, st.get("cost", 0.0) - sum(c[0] for c in costs)), 4)
        except Exception:
            st.setdefault("recent5_cost", 0.0)
            st.setdefault("avg_cost", 0.0)
            st.setdefault("extra_cost", 0.0)
        # 其它工具消耗：本次（本进程启动以来）+ 累计（tool_usage.json）
        try:
            import json as _json
            tu = {}
            try:
                with open(os.path.join(ROOT, "data", "tool_usage.json"), "r", encoding="utf-8") as f:
                    tu = _json.load(f)
            except Exception:
                tu = {}
            total_cost = float(tu.get("cost") or 0)
            total_tok = int(tu.get("tokens") or 0)
            base_cost = float(tu.get("base_cost") or 0)
            base_tok = int(tu.get("base_tokens") or 0)
            st["extra_now_cost"] = round(max(0.0, total_cost - base_cost), 4)
            st["extra_now_tokens"] = max(0, total_tok - base_tok)
            st["extra_total_cost"] = round(total_cost, 4)
            st["extra_total_tokens"] = total_tok
        except Exception:
            pass
        return {
            "paused": orch.paused,
            "wechat_connected": wechat is not None,
            # 「连不上」要说清卡在哪一步（已知现象：「微信连接不通」）：纯读内存里的诊断，
            # 不做任何探测 ⇒ 可以每几秒跟着状态一起下发。
            "wechat_attach": wechat_attach_status(),
            "wechat_version": wechat_version_info(),
            "dep_ok": len(_version_issues()) == 0,
            "model": _cfg_live.get("api", {}).get("model", ""),
            "groups": gs,
            # ── 顶栏状态行要的三样（2026-09-16 待拍板三件之一：做成"看得见"，不替他拍板）──
            #   既有口径：机制/状态要映射到界面。只读统计，不产生任何动作。
            "listen": {"groups": sum(1 for _g in gs if _g.get("target")),
                       "privates": (len(wechat.list_private_targets()) if wechat is not None else 0)},
            "running_chats": sorted(orch.running_chats),
            "stats": st,
            "usage": orch.stats_store.snapshot(),
            "tools": _tools_status(),
            # 2026-09-16 修（用户看到「启动于 21:13」其实是**状态刷新时间**，被当成进程启动时间误解）：
            # 用模块导入时记下的真启动时刻。
            "started_at": _BOOT_AT,
        }

    def _tools_status():
        """当前会给模型的工具表 + 按能力省掉了哪几个（省 token 要看得见）。

        用户口径（2026-09-15）：省 token 不只是我的事，也是群相的事 ⇒ 用户要能看见
        "给了我几个工具、省了哪些、凭什么省"，才谈得上自己把控开销。
        """
        try:
            from agent.tools import visible_defs as _vd, to_openai_tools as _oai
            defs = list(getattr(orch, "tool_defs", None) or [])
            kept, dropped = _vd(defs, get_config())
            return {"total": len(defs), "given": len(kept), "dropped": dropped,
                    "kept_chars": len(json.dumps(_oai(kept), ensure_ascii=False))}
        except Exception as e:
            return {"error": str(e)[:120]}

    def test_api_fn():
        start = time.time()
        resp = chat_completion([{"role": "user", "content": "ping，请只回复 pong"}])
        latency = int((time.time() - start) * 1000)
        return {"ok": True, "latency_ms": latency, "model": resp.get("model"),
                "reply": str(resp.get("message", {}).get("content", ""))[:200]}

    def balance_fn():
        """余额查询（叠一层**显示伪装**）。

        2026-09-16 用户要求：「有没有一键隐藏剩余金额功能或者一键修改剩余金额功能…可以在界面显示上
        把金额改掉，但是实际上还是那么多」⇒ 只改返回给界面的数字（`ui.balance_display`：
        real / hide / fake），**真实余额与账目一律不动**。逻辑在 `agent/balance_view.py`（可单测）。
        """
        try:
            _b = query_balance()
        except Exception as _e:
            return {"error": str(_e)[:120]}
        try:
            from agent.balance_view import mask as _mask
            _ui = (get_config() or {}).get("ui") or {}
            return _mask(_b, _ui.get("balance_display") or "real", _ui.get("balance_fake") or "")
        except Exception as _e2:
            log.debug("余额显示伪装失败（照实返回）：%s", _e2)
            return _b

    # 一键体检取消标志（前端「停止检测」设置；体检循环每步检查）
    _selfcheck_cancel = [False]

    def selfcheck_stop_fn():
        _selfcheck_cancel[0] = True


    def persona_scores_fn():
        """角色评分表：系统自动贴合分（scripts/persona_check 同算法）+ 用户已打分。"""
        try:
            from agent.persona import PERSONAS
            import sys as _sys
            _sys.path.insert(0, ROOT)
            from scripts import persona_check  # 保证算法单一来源
            ratings = {}
            try:
                import json as _json
                with open(os.path.join(ROOT, "data", "persona_ratings.json"), "r", encoding="utf-8") as f:
                    ratings = _json.load(f)
            except Exception:
                ratings = {}
            rows = []
            for k, c in PERSONAS.items():
                r = persona_check.evaluate(k, c)
                u = ratings.get(k) or {}
                rows.append({"key": k, "name": c.get("name") or k,
                             "sys": r["score"], "silent": r["silent"],
                             "user": u.get("score"), "model": u.get("model"),
                             "model_reason": u.get("model_reason", ""),
                             "note": u.get("note", "")})
            # 自定义卡（编辑区/自定义人设）的模型分在评分表中同步显示（模型评分按钮写入 __custom__）
            cu = ratings.get("__custom__") or {}
            if cu.get("model") is not None:
                rows.append({"key": "__custom__", "name": "自定义卡（编辑区）",
                             "sys": 0, "silent": False,
                             "user": cu.get("score"), "model": cu.get("model"),
                             "model_reason": cu.get("model_reason", ""), "note": cu.get("note", "")})
            rows.sort(key=lambda x: -x["sys"])
            return {"ok": True, "rows": rows, "total": len(rows)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def persona_rate_fn(key, score, note=""):
        """用户打分（1-5）落盘 data/persona_ratings.json。"""
        try:
            import json as _json
            p = os.path.join(ROOT, "data", "persona_ratings.json")
            try:
                with open(p, "r", encoding="utf-8") as f:
                    ratings = _json.load(f)
            except Exception:
                ratings = {}
            try:
                s = int(score) if score not in (None, "") else None
                s = s if s is None else max(1, min(5, s))
            except Exception:
                s = None
            ratings[key] = {"score": s, "note": str(note or "")[:200],
                            "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            with open(p, "w", encoding="utf-8") as f:
                _json.dump(ratings, f, ensure_ascii=False, indent=1)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _persona_llm_score(card, name=""):
        """与「模型评分」按钮同一把尺子——实现已提到模块级 persona_llm_score（判据直接调真身）。"""
        return persona_llm_score(card, name)

    def persona_score_custom_fn(text, llm=False):
        """自定义角色卡评分：默认本地（零 token）；llm=True 时交给模型结合角色设定评分。
        分数全部来自对卡文本的实际分析（口头禅/口吻/AI 腔/占位符等），非凭空。"""
        try:
            if not (text or "").strip():
                return {"ok": False, "error": "角色文本为空"}
            if llm:
                sc = _persona_llm_score(text)
                if not sc.get("ok"):
                    return {"ok": False, "error": sc.get("error", "评分失败")}
                score = sc["score"]
                reason = sc["reason"]
                # 写回评分表（模型分独立字段，UI 卡片显示）
                try:
                    import json as _j2
                    _rp = os.path.join(ROOT, "data", "persona_ratings.json")
                    try:
                        with open(_rp, "r", encoding="utf-8") as f:
                            _rts = _j2.load(f)
                    except Exception:
                        _rts = {}
                    _cur = dict(_rts.get("__custom__") or {})
                    _cur["model"] = score
                    _cur["model_reason"] = reason
                    _rts["__custom__"] = _cur
                    with open(_rp, "w", encoding="utf-8") as f:
                        _j2.dump(_rts, f, ensure_ascii=False, indent=1)
                except Exception:
                    pass
                return {"ok": True, "score": score, "reason": reason,
                        "dims": sc["dims"], "via": "llm"}
            from scripts import persona_check
            r = persona_check.score_text(text)
            return {"ok": True, "score": r["score"], "reason": persona_check.fit_desc(r), "via": "local",
                    "detail": {"chars": r["chars"], "quotes": r["quote"],
                               "silent": r["silent"]}}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def persona_ai_enrich_fn(name, text="", rounds=1):
        """模型补足（轮数可调）：每轮=「先确认角色本人 + 引用角色真实原话」→按人设重写→严格评分→分升则下一轮。
        评分与"模型评分"按钮同一把尺子（RULES_TEXT 唯一权威细则，含网梗重罚/精度铁律/从严基线/缺陷压分）；
        最终文本再做 3 次严格复评取中位（抑制忽高忽低），分数永不虚高。"""
        try:
            if not (name or "").strip():
                return {"ok": False, "error": "请先填角色名"}
            from agent.llm import chat_completion
            cur = (text or "").strip()
            # 联网检索该角色第一手真实资料（语录/访谈/事迹摘要）；补足与评分都以它为唯一事实来源
            web_notes = ""
            try:
                from agent import web_search as _ws
                _items = []
                for _q in (name + " 经典语录 名言", name + " 访谈 原话 言论"):
                    try:
                        _r = _ws.web_search(_q)
                        _items += (_r or {}).get("results") or (_r or {}).get("items") or []
                    except Exception:
                        pass
                _lines = []
                for _it in _items[:12]:
                    for _f in ("snippet", "title"):
                        _seg = str(_it.get(_f) or "").strip()
                        if _seg:
                            _lines.append(_seg[:180])
                _lines = _lines[:12]
                if _lines:
                    web_notes = "\n".join("· " + l for l in _lines)
            except Exception:
                pass
            web_block = ("\n【真实资料（联网检索结果原文摘录，作为唯一事实来源）】\n"
                         + (web_notes or "（未检索到该角色第一手资料——补足时严禁编造台词/事迹，只能按口吻写并标注（拟））")
                         + "\n补足与评分只基于以上真实资料与本角色卡；超出真实资料的台词/事迹一律视为编造 → 0 分处理。\n")
            last_score = None
            trace = []
            for rnd in range(1, max(1, min(3, int(rounds or 1))) + 1):
                prompt = (
                    "你是角色塑造专家。请让下面的机器人角色卡**更像角色本人脱口而出**——最高标准是「就是本人！」\n"
                    + web_block +
                    "【第一步·先认识本人】凭你对该角色的真实认知（游戏/动画/小说/影视原台词），先写出："
                    "1) 他是谁（作品+身份）；2) 他最要说出口的【真实台词/口头禅 3~5 条】——必须是他在原作品里说过的原话或原话样式（例：「Rules are made to be broken… like buildings!」），"
                    "**禁止自编台词冒充原话**；若你不确定原话，写「按其口吻」并直接按该角色语气造，但标注（拟）。\n"
                    "【第二步·重写卡片】只做三件事：\n"
                    "① 口头禅/台词改用【第一步的原话】为骨架（能精确引用就精确引用，含翻译+原语）；\n"
                    "② 按该角色的说话习惯重写「说话规则」（短句/分条/被@必回/不用Markdown）；\n"
                    "③ 重写 3 个对话示例（群友在吗/今天好累/再来一句），每句像本人原话口吻。\n"
                    # 2026-09-14 已知现象：「补正…除非你确定补正是完全按照贴合人设的方向去走的，
                    # 而且你也要尽量把这个功能导向那个方向」⇒ 补足只许"加固辨识度"，不许把角色改成通用人格。
                    "【第三步·守住原卡的辨识度（硬要求）】当前卡里**任何已经确认的真实台词/口癖/称呼方式都必须保留原样**"
                    "（那是这个角色的指纹）；补充可以，替换成自造内容不行。改完之后逐条自检："
                    "把名字盖住，还能认出是谁吗？认不出说明被改成了通用人格，重做。\n"
                    "禁止：不要围绕夸奖/评分/逐条打分做优化；不要把角色改得不像本人以迎合任何标准；"
                    "不要写通用套话；不要给古装/名著/严肃/沉重角色塞当代网络梗（V我50/6/草/yyds/退钱/先吃饭 等任何流行语都不行）。直接输出完整新角色卡（纯文本，含 # 角色卡：<名>）。\n\n"
                    "角色名：%s\n当前卡：\n%s" % (name, cur[:2200])
                )
                r = chat_completion([{"role": "user", "content": prompt}])
                _tool_llm_count(r.get("usage"))
                card = ((r.get("message") or {}).get("content") or "").strip()
                if len(card) < 120:
                    break
                # 去掉模型输出的"第一步/第二步"脚手架，只保留最终角色卡
                if "第一步" in card or "第二步" in card:
                    import re as _re
                    _idx = card.rfind("# 角色卡：")
                    if _idx > 0:
                        card = card[_idx:].strip()
                    else:
                        _m2 = _re.search(r"(?ms)(?:第二步[^\n]*)\n{1,3}", card)
                        if _m2:
                            card = card[_m2.end():].strip()
                from agent.persona_enrich import enrich as _enrich
                card = _enrich(card)
                # 补足轮次评分：与「模型评分」同一严格尺子（网梗/模板/占位缺陷必须如实压分）
                sc = _persona_llm_score(card, name)
                score = sc.get("score") if sc.get("ok") else None
                trace.append({"round": rnd, "score": score, "chars": len(card)})
                if score is not None and last_score is not None and score <= last_score:
                    # 分数未升 → 保留上一轮结果，停止
                    break
                last_score = score
                cur = card
            # 最终复评（3 次中位）：抑制单次评分波动（忽高忽低）
            med = []
            for _ in range(3):
                sc = _persona_llm_score(cur, name)
                if sc.get("ok"):
                    med.append(sc)
            final_score = None
            if med:
                med.sort(key=lambda x: x["score"])
                final_score = med[len(med) // 2]
                last_score = final_score["score"]
            return {"ok": True, "text": cur,
                    "score": last_score,
                    "reason": final_score["reason"] if final_score else "（暂无）",
                    "rounds_done": len(trace), "trace": trace,
                    "note": "（最终分=3次严格复评中位，不含虚高）" if final_score else ""}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def selfcheck_fn(mode="full"):
        """一键体检：mode="full" = 环境/配置/点击 + 程序鼠标操作检验（约 40~70 秒）；
        mode="code" = 只做代码/依赖/接入级检查（不动鼠标、秒级完成；首次向导用）。

        只读检查（不动微信、不发消息）；点击类项会移动光标做命中测试。
        每项返回 ok/warn/fail + 说明 + 建议。
        """
        checks = []
        _selfcheck_cancel[0] = False   # 重新开始体检：清除「停止」标记

        def add(name, status, detail, hint=""):
            checks.append({"name": name, "status": status, "detail": detail, "hint": hint})

        # 1) 配置
        try:
            cfg = get_config()
            key = str(cfg.get("api", {}).get("api_key") or "")
            model = str(cfg.get("api", {}).get("model") or "")
            base = str(cfg.get("api", {}).get("base_url") or "")
            add("配置·API Key", "ok" if key and "在这里填" not in key and key != "******" else "fail",
                "Key 已填" if key else "未填", "在「模型 API」卡填你的 DeepSeek Key")
            add("配置·模型/地址", "ok" if base and model else "fail",
                "%s / %s" % (base or "?", model or "?"), "填 Base URL 与模型名")
        except Exception as e:
            add("配置读取", "fail", str(e))

        # 2) 微信连接
        gui = None
        try:
            if wechat is not None:
                gui = wechat._get_gui()
                alive = gui.is_alive()
                import ctypes
                vis = bool(ctypes.windll.user32.IsWindowVisible(gui.main_hwnd))
                add("微信·窗口", "ok" if (alive and vis) else "fail",
                    "进程在，窗口可见" if (alive and vis) else ("窗口不可见（可能最小化/退出）" if alive else "未找到微信窗口"),
                    "打开电脑微信并登录小号，最好别最小化")
            else:
                add("微信·窗口", "fail", "微信适配器未初始化（微信可能没开）", "打开电脑微信再重启机器人")
        except Exception as e:
            add("微信·窗口", "fail", str(e), "打开电脑微信后重试")

        # 3) 微信数据/群
        try:
            groups = wechat.list_groups() if wechat is not None else []
            add("微信·目标群", "ok" if targets else "warn",
                "发现 %d 个群，目标 %d 个：%s" % (len(groups), len(targets),
                                               "、".join(g["name"] for g in targets) or "(空)"),
                "在配置 wechat.group_name_white_list 里加群名，留空=所有群")
            if targets:
                seq = wechat.latest_seq(targets[0]["wxid"])
                add("微信·消息库可读", "ok" if seq else "fail",
                    "目标群 %s 最新序号=%s" % (targets[0]["name"], seq),
                    "若读数是 0 且群里已说话，可能是微信数据库位置不对（wechat.db_dir）")
        except Exception as e:
            add("微信·数据", "fail", str(e))

        # 4) 界面适配/点击
        try:
            from agent import ui_adapt
            scale = ui_adapt.coord_scale()
            add("适配·显示缩放", "ok" if abs(scale - 1.0) < 0.01 else "warn",
                "检测到 %.2fx%s" % (scale, "" if abs(scale - 1.0) < 0.01 else "（自动换算点击坐标）"),
                "若点击异常可在控制台「显示缩放」手动指定档位")
            try:
                import ctypes
                from ctypes import wintypes
                u = ctypes.windll.user32
                overlays = {"n": 0}
                CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

                def cb(h, l):
                    if u.IsWindowVisible(h):
                        cls = ctypes.create_unicode_buffer(256)
                        u.GetClassNameW(h, cls, 256)
                        if cls.value in ("ShellHandwritingCanvas", "Windows.UI.Core.CoreWindow"):
                            overlays["n"] += 1
                    return True

                ref = CB(cb)
                u.EnumWindows(ref, 0)
            except Exception:
                overlays["n"] = 0
            add("适配·系统叠加层", "warn" if overlays["n"] else "ok",
                ("发现 %d 个输入叠加层（手写画布/输入体验）" % overlays["n"]) if overlays["n"] else "无（正常）",
                "点击前会自动清理；若反复出现请关闭触控键盘（Win+Ctrl+O）")
            if gui is not None and mode != "code":
                # 真实点击自检：与拍一拍完全相同「移动+右键」逻辑，右键一条消息看菜单是否弹出
                # 仅报告，不写状态（不再自动进入低功率——曾导致拍一拍被长期禁用）
                try:
                    cr = wechat.click_self_test()
                    add("适配·点击实测(拍一拍同链路)", "ok" if cr.get("ok") else "fail",
                        cr.get("detail", ""),
                        "" if cr.get("ok") else "点击投递异常：检查是否在真实桌面启动(scripts\\启动机器人.vbs)、"
                        "是否打开了群聊、机器是否卡顿/有拦截软件")
                except Exception as e:
                    add("适配·点击实测", "fail", str(e))
        except Exception as e:
            add("界面适配检查", "fail", str(e))

        # 4.5) OCR 引擎健康度（会话名/身份自检全靠它；卡住时发送链会按「自检不可用」放弃）
        #      测机手册 ④：本机曾出现 OCR 一次卡 8 分 19 秒 ⇒ 现在单次硬上限 25 秒 + 连续超时熔断。
        try:
            from agent import chat_ocr as _co
            _st, _dt, _hint = _co.health_line()
            add("识别·OCR 卡顿", _st, _dt, _hint)
        except Exception as e:
            add("识别·OCR 卡顿", "info", "读不到 OCR 健康度：%s" % e)

        add("拍一拍", "info", "请用「拍一拍诊断」按钮实测（定位/右键/验证一步一报告）")
        add("发送防重复", "info", "已启用 3 秒重复发送拦截（回车重试竞态防护）")


        ok_n = sum(1 for c in checks if c["status"] == "ok")
        warn_n = sum(1 for c in checks if c["status"] == "warn")
        fail_n = sum(1 for c in checks if c["status"] == "fail")
        return {"ok": fail_n == 0, "checks": checks,
                "summary": "通过 %d 项 / 注意 %d 项 / 失败 %d 项" % (ok_n, warn_n, fail_n),
                "cancelled": _selfcheck_cancel[0]}

    def poke_test_fn(group_wxid="", verify_only=False):
        # 拍一拍诊断：目标直接从微信数据库取（不依赖控制台存档，任何群有人说过话即可）；
        # 手动诊断不受白名单限制（自动拍一拍仍只拍监控中的群）；
        # group_wxid 指定群（空=自动找最近有群友发言的群）；verify_only=只验菜单可弹
        try:
            if wechat is None:
                return {"ok": False, "error": "微信未就绪"}
            all_groups = list(groups)
            if not all_groups:
                return {"ok": False, "error": "没有发现任何群聊"}
            gs = [g for g in all_groups if g["wxid"] == group_wxid] if group_wxid else all_groups
            if not gs:
                return {"ok": False, "error": "没找到这个群（可能微信会话列表里已不存在）"}
            best = None
            for g in gs:
                name, sid = wechat._latest_friend(g["wxid"])
                if sid:
                    cand = (g["wxid"], g["name"], name, sid)
                    if best is None:
                        best = cand
                    # 取 sort_seq 最新的群：用 DB 末尾比较
                    try:
                        if wechat.latest_seq(g["wxid"]) > wechat.latest_seq(best[0]):
                            best = cand
                    except Exception:
                        pass
            if best is None:
                return {"ok": False, "error": "这些群里暂时没有群友的消息记录（让对方在群里说句话即可，不需要存档）"}
            wxid, gname, name, sid = best
            result = wechat.poke_diag(wxid, name, sid, verify_only=verify_only)
            result["target"] = {"name": name, "id": sid}
            result["group"] = gname
            result["verify_only"] = bool(verify_only)
            return result
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def groups_fn():
        # 群聊列表（控制台「检测群聊并勾选」用）
        # 2026-09-16（已知现象：「选了群、点保存之后显示读取会话失败」）：以前**微信没接上也返回 ok:True +
        # 空列表** ⇒ 控制台只会显示"检测到 0 个群"，真正的原因（微信未接入）被吞掉。
        # 现在：接不上就把**原因**如实带回去（原因由 `wechat_attach_status()` 提供 = 卡在哪一步）。
        if wechat is None:
            try:
                _why = wechat_attach_status().get("reason") or "微信未接入"
            except Exception:
                _why = "微信未接入"
            return {"ok": False, "error": "微信还没接上 ⇒ 读不到群列表。原因：%s" % _why, "groups": []}
        try:
            gs = [{"name": g.get("name"), "wxid": g.get("wxid")}
                  for g in (wechat.list_groups() or [])]
        except Exception as e:
            return {"ok": False, "error": "读群列表失败：%s" % str(e)[:120], "groups": []}
        return {"ok": True, "groups": gs}

    def memory_fn(action, chat_key="", user_id="", name="", contents=None, scope="all"):
        # 记忆页面：list（各群成员印象） / delete（删某成员印象） / update（编辑成员印象）
        # scope＝删除范围（既有口径：不替他二选一）：「all」＝互通范围内的每一份都删（与列表口径一致，
        #   默认）；「this」＝只删本群那一份，此时必须**如实说明**别的群还剩几份（否则用户以为没删掉）。
        try:
            if action == "list":
                chats = []
                try:
                    for ck in orch.store.list_chats():
                        mems = orch.memory.members(ck) if hasattr(orch.memory, "members") else []
                        if not mems:
                            continue
                        gname = ck
                        try:
                            if ":" in ck:
                                gname = wechat.group_name(ck.split(":", 1)[1])
                        except Exception:
                            pass
                        chats.append({"chat_key": ck, "name": str(gname), "count": len(mems)})
                except Exception:
                    pass
                members = []
                if chat_key:
                    try:
                        members = orch.memory.members(chat_key) or []
                    except Exception:
                        members = []
                return {"ok": True, "chats": chats, "chat_key": chat_key, "members": members}
            if action == "clear_all":
                # 清除全部记忆：所有群的成员印象 + 共享记忆 + 会话日志（运行明细/对话历史）
                try:
                    for ck in list(orch.store.list_chats()):
                        try:
                            for mem_id in [str(m.get("id") or m.get("memberId") or m.get("userId") or "")
                                           for m in (orch.memory.members(ck) or [])]:
                                if mem_id:
                                    orch.memory.remove(ck, "memberImpression", user_id=mem_id)
                        except Exception:
                            pass
                    orch.memory.clear_all()
                    # 会话日志（运行明细 JSONL）
                    import glob as _glob
                    removed = 0
                    for fp in _glob.glob(os.path.join(ROOT, "data", "sessions", "*.jsonl")):
                        try:
                            os.remove(fp); removed += 1
                        except Exception:
                            pass
                    try:
                        _cl = os.path.join(ROOT, "data", "session_log.jsonl")
                        if os.path.exists(_cl):
                            os.remove(_cl); removed += 1
                    except Exception:
                        pass
                    return {"ok": True, "note": "会员印象+共享记忆+会话日志已清除（%d 个文件）" % removed}
                except Exception as e:
                    return {"ok": False, "error": str(e)}
            if action == "clear_sessions":
                # 仅清除会话日志（运行明细/对话历史），不碰记忆
                import glob as _glob
                removed = 0
                for fp in _glob.glob(os.path.join(ROOT, "data", "sessions", "*.jsonl")):
                    try:
                        os.remove(fp); removed += 1
                    except Exception:
                        pass
                try:
                    _cl = os.path.join(ROOT, "data", "session_log.jsonl")
                    if os.path.exists(_cl):
                        os.remove(_cl); removed += 1
                except Exception:
                    pass
                return {"ok": True, "note": "已清除 %d 个会话日志文件" % removed}
            if action == "delete":
                ok = orch.memory.remove(chat_key, "memberImpression", user_id=user_id, scope=scope)
                out = {"ok": bool(ok), "scope": str(scope)}
                if str(scope) == "this":
                    # 「只删本群」必须说清后果：列表是合并视图，别的群那份还在 ⇒ 界面上仍看得到
                    try:
                        left = int(orch.memory.elsewhere(chat_key, user_id=user_id))
                    except Exception:
                        left = 0
                    out["left_elsewhere"] = left
                    if left:
                        out["note"] = ("已删本群那一份；这条资料在另外 %d 个群还留着，"
                                       "所以列表里仍会看到它（要一起删就选「所有群一起删」）" % left)
                    else:
                        out["note"] = "已删本群那一份（其它群没有它的资料）"
                return out
            if action == "update":
                # 手动编辑成员印象（replace_member）
                try:
                    member = orch.memory.replace_member(chat_key, user_id, name or "", list(contents or []))
                    return {"ok": True, "member": member}
                except ValueError as ve:
                    return {"ok": False, "error": str(ve)}
                except Exception as e:
                    return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": False, "error": "未知操作"}

    summarize_msg = ["<#系统> 你正在与被呼叫的机器人对话，请用一句话描述自己或提问。"]

    def _summarize_on_exit():
        """关闭时把本次会话的群友发言总结成印象（只在此刻调模型一次；平时绝不调）。"""
        try:
            cfg = get_config()
            if not (cfg.get("memory", {}).get("summarize_on_exit", True)):
                return
            # 取最近的群友发言（本进程存活期间新增）；记录 群→(发言者,发言) 便于写回对应群
            recent = []
            by_group = {}
            try:
                for g in (orch.wechat.list_groups() if orch.wechat else [])[:3]:
                    wxid = g.get("wxid") or g.get("id") or ""
                    if not wxid:
                        continue
                    ck = "group:" + str(wxid)
                    for raw in orch.wechat._db.get_messages(wxid, limit=40):
                        n = orch.wechat.normalize(raw, wxid)
                        if n and str(n.get("sender_id") or "").startswith("wxid_") and str(n.get("text") or "").strip():
                            nm = str(n.get("sender_name") or "?")
                            txt = str(n["text"])[:60]
                            recent.append((nm, txt))
                            by_group.setdefault(ck, []).append(nm)
            except Exception:
                recent, by_group = [], {}
            if not recent:
                log.info("本次无可总结的对话，跳过关机印象")
                return
            from agent.llm import chat_completion
            text = "\n".join("%s：%s" % (a, b) for a, b in recent[-60:])
            prompt = (
                "下面是本次微信群里机器人的群友发言（已去重）。请总结每位群友的**人物印象**，"
                "输出 JSON 数组：[{\"name\":\"群友名\",\"impressions\":[\"性格/偏好/黑话/语气，每条一句话\"]}]。"
                "只输出 JSON；信息不足的名字可以不出现在结果里。\n\n" + text
            )
            r = chat_completion([{"role": "user", "content": prompt}])
            content = ((r.get("message") or {}).get("content") or "").strip()
            import re as _re
            m = _re.search(r"\[.*\]", content, _re.S)
            if not m:
                log.info("关机总结解析失败，跳过")
                return
            import json as _json
            rows = _json.loads(m.group(0))
            n_ok = 0
            for row in rows:
                name = str(row.get("name") or "").strip()
                imps = [str(i).strip() for i in (row.get("impressions") or []) if str(i).strip()]
                if not name or not imps:
                    continue
                # 写入该成员发言过的群（无精确 userId 时以名字为 target 记印象）
                groups = [ck for ck, names in by_group.items() if any(x == name for x in names)]
                if not groups:
                    continue
                for ck in groups:
                    try:
                        orch.memory.append(ck, "memberImpression", " ".join(imps),
                                           extra={"userId": "", "target": name})
                        n_ok += 1
                    except Exception:
                        pass
            log.info("关机总结完成：更新 %d 条群友印象" % n_ok)
        except Exception as e:
            log.info("关机总结失败：%s" % e)

    def shutdown_fn():
        log.info("收到停止指令，正在停止机器人…")
        # 先写「停止」标记 + 杀看门狗：否则 5 秒后被自动拉起，会「停止后又弹出新控制台」
        try:
            with open(os.path.join(ROOT, "data", "stopped.flag"), "w", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass
        try:
            _kill_watchdog()
        except Exception:
            pass
        # 立即强退（os._exit 不走 atexit，主动清理 PID 文件）。
        # 关键：os._exit 放最前——orch.shutdown()（微信登出）可能一直阻塞，挡住 os._exit 导致"停止关不掉"。
        try:
            _p = os.path.join(ROOT, "data", "bot.pid")
            if os.path.exists(_p):
                os.remove(_p)
        except Exception:
            pass
        # 关机总结印象（可选）：最多等 5 秒——绝不阻塞"停止"；被强杀则本次跳过
        try:
            log.info("关机总结印象中（最多 5 秒）…")
            _sm = threading.Thread(target=_summarize_on_exit, daemon=True)
            _sm.start()
            _sm.join(5)
        except Exception:
            pass
        try:
            threading.Timer(0.1, lambda: (orch.shutdown() if orch else None)).start()   # 后台尽力登出，不阻塞退出
        except Exception:
            pass
        os._exit(0)

    def restart_fn():
        # 后台无窗口重启：**先把强退装上**，再杀旧看门狗、再拉新的，本进程退出。
        # ⛔ 2026-09-17 修（用户报「你更新后的重启又关不掉自己了；不是说会再起一个新的吗，我从来没见过
        #   这个再起一个；之前杀掉就没了，现在更是杀都杀不掉」）——老顺序是：
        #     先 `_spawn_watchdog()`（新看门狗**立刻** Popen 新 bot）→ 再**同步** `orch.shutdown()`
        #     （下面那句注释自己写着"微信登出可能一直阻塞"）→ 最后才装 1.5 秒强退 Timer。两个后果：
        #     ① `orch.shutdown()` 一阻塞，**那行 Timer 永远执行不到** ⇒ 本进程永不退出（"关不掉自己"）；
        #        再从任务管理器杀掉，5 秒内被旧看门狗拉起来 ⇒ 看着就是"杀都杀不掉"。
        #     ② 新 bot 起来时旧实例还活着、端口与**单实例锁**都没放开 ⇒ 新实例在启动闸门处 `sys.exit(3)`，
        #        看门狗每 5 秒重试一次、次次撞锁 ⇒ 永远看不到"新的那个"。
        #   与 `shutdown_fn` 同一口径（那条 2026-09-16 已经这么修过）：**先保命退出，再做慢活**。
        log.info("收到重启指令，正在后台拉起新实例…")

        def _exit_now():
            try:
                _p = os.path.join(ROOT, "data", "bot.pid")
                if os.path.exists(_p):
                    os.remove(_p)
            except Exception:
                pass
            os._exit(0)

        # ① 先装强退：2 秒后无论如何都退（后面几件慢活挡不住它）
        threading.Timer(2.0, _exit_now).start()
        try:
            _kill_watchdog()                      # ② 先杀旧看门狗，免得它把本进程复活/双开
        except Exception:
            pass
        try:
            # ③ **立刻**拉起新看门狗（它自己等 6 秒再开机器人）——必须在 `os._exit` 之前拉，
            #    否则进程一退就没人接替了（第一版"用 Timer 等 6 秒再拉"永远等不到：进程 2 秒就退了）
            _spawn_watchdog(delay=6)
            log.info("已拉起新看门狗（它会在 6 秒后接管机器人）")
        except Exception as e:
            log.error("重启拉起看门狗失败：%s", e)
        try:
            # ④ 尽力登出，但**放进守护线程**：它再慢也不再挡住退出
            threading.Thread(target=lambda: (orch.shutdown() if orch else None), daemon=True).start()
        except Exception:
            pass

    # ── 控制台访问口令：空/过短（<16 位易被猜）→ 启动时自动生成强随机口令 ──
    server_cfg = cfg.get("server", {})
    _tok = str(server_cfg.get("token") or "").strip()
    if len(_tok) < 16:
        server_cfg["token"] = secrets.token_urlsafe(24)  # 32 位强随机（字母数字-_）
        save_config(cfg)
        log.info("已自动生成控制台访问口令（%d 位，保存在 config.json 的 server.token）", len(server_cfg["token"]))

    def community_export_fn(kind="holyshits"):
        """导出：金句/意见/聊天记录 → 本地文件（export_dir 可配）。"""
        try:
            from agent.scoring import seed_library, stats as _scoring_stats
        except Exception:
            seed_library = lambda: []
        try:
            cfg = get_config().get("community", {}) or {}
            out_dir = os.path.join(ROOT, str(cfg.get("export_dir") or "exports"))
            os.makedirs(out_dir, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            if kind == "holyshits":
                lines = seed_library()
                path = os.path.join(out_dir, "holyshits-%s.txt" % ts)
                with open(path, "w", encoding="utf-8") as f:
                    f.write("# 金句（种子库，含内置+导入）\n" + "\n".join(lines))
                return {"ok": True, "path": path, "count": len(lines)}
            if kind == "feedback":
                # 意见反馈导出（feedback 复述存到 sessions.jsonl 里，简化：导出会话里的反馈）
                path = os.path.join(out_dir, "feedback-%s.json" % ts)
                rows = []
                try:
                    import glob
                    for fp in glob.glob(os.path.join(ROOT, "data", "sessions", "*.jsonl")):
                        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                            for line in f:
                                try:
                                    j = json.loads(line)
                                except Exception:
                                    continue
                                if j.get("feedbacks"):
                                    rows.append({"ts": j.get("ts"), "chat": j.get("chat_name"),
                                                 "feedbacks": j.get("feedbacks")})
                except Exception:
                    pass
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(rows, f, ensure_ascii=False, indent=1)
                return {"ok": True, "path": path, "count": len(rows)}
            if kind == "persona_ratings":
                # 角色评分表导出（系统分 + 用户分；可再上传到社区）
                try:
                    with open(os.path.join(ROOT, "data", "persona_ratings.json"), "r", encoding="utf-8") as f:
                        ratings = json.load(f)
                except Exception:
                    ratings = {}
                try:
                    from scripts import persona_check
                    from agent.persona import PERSONAS
                    rows = []
                    for k, c in PERSONAS.items():
                        r = persona_check.evaluate(k, c)
                        u = ratings.get(k) or {}
                        rows.append({"key": k, "name": c.get("name") or k, "sys": r["score"],
                                     "user": u.get("score"), "note": u.get("note", "")})
                except Exception:
                    rows = []
                path = os.path.join(out_dir, "persona_ratings-%s.json" % ts)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(rows, f, ensure_ascii=False, indent=1)
                return {"ok": True, "path": path, "count": len(rows)}
            # 聊天记录导出
            path = os.path.join(out_dir, "messages-%s.json" % ts)
            export_chats = {}
            try:
                import glob
                for fp in glob.glob(os.path.join(ROOT, "data", "messages", "*.json")):
                    try:
                        with open(fp, "r", encoding="utf-8") as f:
                            d = json.load(f)
                        if isinstance(d, dict) and d.get("messages"):
                            export_chats[os.path.basename(fp)] = d.get("messages")
                    except Exception:
                        continue
            except Exception:
                pass
            with open(path, "w", encoding="utf-8") as f:
                json.dump(export_chats, f, ensure_ascii=False, indent=1)
            return {"ok": True, "path": path, "count": sum(len(v) for v in export_chats.values())}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def community_upload_fn(data):
        """社区分享：POST 到可配置 URL（upload_enabled + 对应 URL，默认关）→ 仅提示未配置。"""
        try:
            cfg = get_config().get("community", {}) or {}
            if not cfg.get("upload_enabled"):
                return {"ok": False, "error": "社区上传未开启（community.upload_enabled=false）"}
            kind = str(data.get("kind") or "")
            url = str(cfg.get("holyshits_upload_url") or "") if kind == "holyshits" else str(cfg.get("feedback_upload_url") or "")
            if not url:
                return {"ok": False, "error": "未配置 %s 上传 URL" % kind}
            import requests as _req
            payload = data.get("payload") or {}
            r = _req.post(url, json=payload, timeout=10)
            r.raise_for_status()
            return {"ok": True, "resp": r.text[:200]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def scoring_import_fn(text):
        """导入金句进评分种子库。"""
        try:
            from agent.scoring import import_seeds
            n = import_seeds(text)
            return {"ok": True, "imported": n}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _recalibrate_ui(orch):
        """控制台「重新标定」：接管鼠标一次，自动检测微信侧栏图标序列并写 ui_layout.json。"""
        try:
            from agent import wechat_ui
            gui = orch.wechat._get_gui()
            lay = wechat_ui.calibrate_ui(gui)
            if lay.get("sidebar_items"):
                return {"ok": True, "count": len(lay["sidebar_items"])}
            return {"ok": False, "error": "标定未检测到侧栏图标（微信窗口可见？请确保微信在前台后重试）"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _open_export_path(path):
        """打开导出文件/文件夹所在位置（不存在时创建目录；绝对/相对均支持）。"""
        try:
            import os
            import subprocess
            p = str(path or "").strip() or "exports"
            if not os.path.isabs(p):
                p = os.path.join(ROOT, p)
            if os.path.isdir(p):
                os.makedirs(p, exist_ok=True)
                os.startfile(p)
                return {"ok": True}
            d = os.path.dirname(p)
            if not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            if os.path.exists(p):
                subprocess.Popen(["explorer", "/select,", os.path.abspath(p)],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                os.startfile(d)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    try:
        _LLM_COUNT_HOOK[0] = _tool_llm_count
    except Exception:
        pass
    webui = WebUI(status_provider, log_buffer, test_api_fn=test_api_fn, balance_fn=balance_fn,
                  # 保存配置后就地重算监听目标（否则改了群勾选只有重启才生效，概览也一直是老的）
                  on_save=lambda _new_cfg: _refresh_targets("配置已保存"),
                  pause_fn=lambda: orch.set_paused(True), resume_fn=lambda: orch.set_paused(False),
                  shutdown_fn=shutdown_fn, whale=orch.whale,
                  poke_test_fn=poke_test_fn, selfcheck_fn=selfcheck_fn, restart_fn=restart_fn,
                  groups_fn=groups_fn, memory_fn=memory_fn,
                  sessions_fn=lambda limit: orch.session_log.recent(limit),
  store=orch.store,
                  community_export_fn=community_export_fn,
                  community_upload_fn=community_upload_fn,
                  scoring_import_fn=scoring_import_fn,
                  emojis_fn=lambda: (orch.wechat.list_emojis() if getattr(orch, "wechat", None) else []),
                  recalibrate_fn=lambda: _recalibrate_ui(orch),
                  open_path_fn=lambda path: _open_export_path(path),
                  selfcheck_stop_fn=lambda: selfcheck_stop_fn(),
                  persona_scores_fn=persona_scores_fn,
                  persona_rate_fn=persona_rate_fn,
                  persona_score_custom_fn=persona_score_custom_fn,
                  persona_ai_enrich_fn=persona_ai_enrich_fn)
    try:
        # 控制台永远先启动（微信 UIA 几何调整可能因校准耗时/卡住，不能挡在它前面）
        port = webui.start()
        if port:
            token = str(server_cfg.get("token") or "").strip()
            url = "http://127.0.0.1:%d" % port + (("/?token=" + token) if token else "")
            log.info("Web 控制台：%s", mask_url_token(url))
            if server_cfg.get("auto_open_browser", True) is not False:
                # 开窗只走一处（`agent/notify_ui.open_console`）：优先级＝自家 WebView2 窗口 → 浏览器，
                # 并且**所有入口共用同一把锁**（logs/browser_opened.lock）。
                # 2026-09-14 修"自家窗口 + 浏览器同时弹"：原先这里自己拿锁开浏览器、启动器那边另拿一次，
                # 两个入口各开一个 ⇒ 双窗。
                try:
                    from agent.notify_ui import open_console as _open_console
                    _rep = _open_console(url, browser_path=str(server_cfg.get("browser_path") or ""))
                    _how = str(_rep.get("how") or "")
                    if _how == "webview":
                        log.info("已在我们自己的窗口里打开控制台（不依赖浏览器）")
                    elif _how == "browser":
                        log.info("自家控制台窗口不可用（%s）⇒ 已回退浏览器", _rep.get("why") or "原因未知")
                    elif _how == "skip":
                        log.info("%s", _rep.get("why") or "本次不重复打开")
                    else:
                        log.warning("打开控制台失败：%s", _rep.get("why") or "")
                except Exception as e:
                    log.warning("打开控制台异常（请手动访问 %s）：%s", mask_url_token(url), e)
    except Exception as e:
        log.warning("Web 控制台启动失败：%s", e)

    # 首步：把微信窗口移到固定位置+标准大小（几何恒定，坐标只按 DPI 换算；
    # 放在控制台之后——UIA 校准可能耗数十秒甚至卡住，不能拖累控制台）
    try:
        if wechat is not None:
            from agent.ui_adapt import _force_geometry as _fg
            _fg(wechat._get_gui())
    except Exception:
        pass

    # 初始化轮询游标（W2）：**优先读持久化水位** —— 重启后从上次的位置继续（停机期间的新消息会补上），
    # 只有在第一次运行（没有水位文件）时才用 latest_seq 当起点（不重放历史）。
    _wm_path = os.path.join(DATA_DIR, "listener_watermark.json")
    _dl_path = os.path.join(DATA_DIR, "listener_failed.jsonl")
    wm = listener_watermark.Watermark(_wm_path)
    _wm_log = listener_watermark.make_log(log)
    for g in targets:
        _key0 = "group:" + g["wxid"]
        if wm.get(_key0, 0) <= 0:
            try:
                wm.set(_key0, (wechat_box[0] or wechat).latest_seq(g["wxid"]))
            except Exception:
                wm.set(_key0, 0)
    wm.flush()
    log.info("监听水位已载入：%s（重启不丢、不重放）", _wm_path)

    def _stop(signum=None, frame=None):
        log.info("收到退出信号，正在停止…")
        orch.shutdown()

    try:
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
    except (ValueError, OSError):
        pass

    log.info("开始监听群消息（目标群 %d 个）… Ctrl+C 退出（轮询间隔在控制台修改保存即生效）", len(targets))
    _recall_state = {}   # 第 14 条：每会话的撤回核对去重/限频状态（内存即可，重启重扫一遍无害）
    # 主动开话题（默认关；控制台开启后循环启动，暂停/停止时跳 tick）
    try:
        orch.start_proactive_loop()
    except Exception as e:
        log.warning("主动话题循环启动失败：%s", e)
    # 计时提醒 + 节假日问候巡检（第 12/13 条）：20 秒一跳；暂停中一条都不发（恢复后补发）
    _sched = _start_timer_holiday_loop(orch, lambda: wechat_box[0])

    # 水位自愈的限频表（每个群最多 10 秒查一次"最新序号"，别每跳都多打一次库）
    _wm_heal = {}
    while not orch.stopped:
        poll_interval = max(1.0, float(get_config().get("wechat", {}).get("poll_interval") or 3))
        # ── 微信接入重试（2026-09-16：老代码注释里承诺过、实际**从未实现**的那一句）──────
        #   症状（已知现象：「微信连接不上」）：启动那一刻微信没开（或还没登录）⇒ `wechat is None`
        #   ⇒ 目标群为空 ⇒ 整个监听循环什么都不做，而且**没有任何重试** ⇒ 控制台永远显示
        #   "微信未连接"，用户只能重启。这里：每 10 秒再试一次；接上以后把群/目标/发送队列/
        #   orchestrator 的句柄都补上，并把新群的监听水位推到当前（不补历史积压）。
        if wechat_box[0] is None and (time.time() - float(_ATTACH.get("at") or 0)) >= 10:
            _wc_new = _attach_wechat(get_config())
            if _wc_new is not None:
                wechat_box[0] = _wc_new
                wechat = _wc_new
                sender.wechat = _wc_new
                orch.wechat = _wc_new
                groups, targets = _collect_targets(_wc_new)
                target_wxids.clear()
                target_wxids.update(g["wxid"] for g in targets)
                for g in targets:
                    _k = "group:" + g["wxid"]
                    if wm.get(_k, 0) <= 0:
                        try:
                            wm.set(_k, _wc_new.latest_seq(g["wxid"]))
                        except Exception:
                            wm.set(_k, 0)
                wm.flush()
                log.info("微信已接入（第 %d 次尝试）：监听目标 %d 个", _ATTACH["tries"], len(targets))
            else:
                log.info("微信仍未接入（第 %d 次尝试）：%s", _ATTACH["tries"],
                         wechat_attach_status()["reason"])
        try:
            for g in targets:
                wxid = g["wxid"]
                chat_key = "group:" + wxid
                if orch.paused:
                    # 暂停期间不响应。水位怎么处理**做成开关**（`wechat.replay_on_resume`）：
                    #   默认（False）＝把水位推到最新**并落盘** ⇒ 恢复时**不重放**暂停期间的积压
                    #     （否则恢复瞬间会把暂停期间几十条旧消息逐批触发，表现为"每条都回"）；
                    #   打开（True）＝**不推进水位** ⇒ 恢复后按水位把积压补上（长暂停会集中回一阵）。
                    try:
                        _replay = bool((get_config().get("wechat") or {}).get("replay_on_resume", False))
                    except Exception:
                        _replay = False
                    if not _replay:
                        try:
                            wm.set(chat_key, wechat.latest_seq(wxid))
                            wm.flush()
                        except Exception:
                            pass
                    continue
                # ⛔ 2026-09-17（用户问「我把聊天记录清空了，它会不会学不会、从而不发」）：
                #   水位**只前进不回退**（防重复处理），而**微信清空聊天记录后序号可能回落 / 换库**
                #   ⇒ 新消息的 sort_seq 小于旧水位 ⇒ 全被判成"处理过了" ⇒ **它真的不回**（而且重启
                #   也救不回来：水位是从文件读回来的）。这里自愈：库里的**最新序号低于水位** ⇒ 这个
                #   会话的记录被清过，把水位对齐到当前最新（正常运行时 latest 只会 ≥ 水位，不误触发）。
                try:
                    _now2 = time.time()
                    if (_now2 - float(_wm_heal.get(wxid, 0) or 0)) >= 10.0:
                        _wm_heal[wxid] = _now2
                        _latest = int(wechat.latest_seq(wxid) or 0)
                        _cur = int(wm.get(chat_key, 0) or 0)
                        if _latest and _cur and _latest < _cur:
                            log.warning("群[%s] 的消息序号回落到 %d（原水位 %d）⇒ 记录像是被清过，"
                                        "把水位对齐到最新；否则新消息会被当成旧消息跳过、它就不回了",
                                        g["name"], _latest, _cur)
                            wm.set(chat_key, _latest, forward_only=False)
                            wm.flush()
                except Exception as e:
                    log.debug("水位自愈检查失败（不影响监听）：%s", e)
                try:
                    new = wechat.poll_new_messages(wxid, wm.get(chat_key, 0), limit=50)
                except Exception as e:
                    log.debug("读取群[%s]异常：%s", g["name"], e)
                    continue
                # ── 撤回核对（第 14 条兜底）：微信可能把被撤的那行**原地改写**（不新增系统行），
                #    只认"新出现的系统行"会漏 ⇒ 定期回读近期消息在库里的当前内容核对。
                #    限频 15 秒、只查 5 分钟内的、每条只读一次（记在 _recall_state 里）。
                try:
                    _rcfg2 = get_config().get("store", {}).get("recall") or {}
                    if bool(_rcfg2.get("enabled", True)):
                        recall.sweep(store, chat_key,
                                     lambda mid, _w=wxid: wechat.message_content(_w, mid),
                                     memory=memory,
                                     state=_recall_state.setdefault(chat_key, {}),
                                     interval_ms=15000, window_ms=300000, log=log)
                except Exception as e:
                    log.debug("群[%s]撤回核对异常：%s", g["name"], e)
                if not new:
                    continue
                # 屏蔽名单（按群）：{群名: [昵称/wxid...]}——命中的不存档、不触发（但仍算"已处理"，水位可推进）
                blist = (get_config().get("store", {}).get("group_blocklist") or {}).get(g["name"]) or []
                blocked = {str(b).strip().lower() for b in blist if str(b).strip()}

                def _handle_one(nm, _chat_key=chat_key, _g=g, _wxid=wxid, _blocked=blocked):
                    """W2：返回真值＝这条已被下游接受（append_incoming 落盘成功后返回 entry）。"""
                    # ── 屏蔽存档的会话（第 10 条）：不落库、不触发、不进记忆；水位照推 ──
                    if archive_filter.is_blocked(chat_key=_chat_key, group_name=_g["name"]):
                        return {"dropped": "archive-block"}
                    # ── 撤回事件（第 14 条）：把已进上下文/存档的那条剔除，**不触发回复** ──
                    _rc = nm.get("recall")
                    if _rc:
                        _rcfg = get_config().get("store", {}).get("recall") or {}
                        if not bool(_rcfg.get("enabled", True)):
                            log.info("群[%s]收到撤回事件，但「撤回后剔除上下文」已关闭（只记日志）", _g["name"])
                            return {"recall": {"disabled": True}}
                        try:
                            _res = recall.purge(
                                store, _chat_key, _rc,
                                resolver=lambda sv: wechat.local_id_by_server_id(_wxid, sv),
                                memory=memory,
                                window_ms=int(_rcfg.get("window_sec") or 180) * 1000,
                                heuristic=bool(_rcfg.get("heuristic", True)), log=log)
                        except Exception as e:
                            log.warning("群[%s]撤回处理异常：%s", _g["name"], e)
                            return {"recall": {"error": str(e)[:80]}}
                        if not _res.get("removed"):
                            log.info("群[%s]撤回事件没匹配到存档条目（谁=%s 方式=%s）——已写审计，未动任何存档",
                                     _g["name"], _res.get("who") or "?", _res.get("how"))
                        return {"recall": _res}
                    who = str(nm.get("sender_name") or "").strip().lower()
                    wid = str(nm.get("sender_id") or "").strip().lower()
                    if _blocked and (who in _blocked or wid in _blocked):
                        log.info("群[%s]屏蔽用户消息已丢弃（%s/%s）", _g["name"], who or wid, who or wid)
                        return {"dropped": "blocklist"}
                    # ── 指令禁言（第 18 条）：@机器人 + 指令 ⇒ 本会话档位固定降到 1 档 ──
                    #    只认白名单（store.tier_cmd_admins；留空＝谁都不能下指令），且**不回复**（只在日志/控制台可见）
                    try:
                        _txt = str(nm.get("text") or "")
                        _pcfg = get_config().get("persona") or {}
                        _pnick = str(_pcfg.get("self_nickname") or "")
                        _pbot = str(_pcfg.get("bot_name") or "")
                        _at = is_at_me(_txt, _pnick, _pbot, wechat.self_wxid) \
                            or bool(wechat.self_nickname and is_at_me(_txt, wechat.self_nickname, "", ""))
                        _cmd = tier_control.handle_command(_chat_key, nm, bool(_at))
                    except Exception as e:
                        _cmd = {"handled": False}
                        log.debug("群[%s]指令解析异常：%s", _g["name"], e)
                    if _cmd.get("handled"):
                        if _cmd.get("action") == "denied":
                            log.info("群[%s]收到禁言指令但发送者不在白名单（%s）——未生效",
                                     _g["name"], _cmd.get("who") or "?")
                        else:
                            log.info("群[%s]指令禁言：%s ⇒ %s", _g["name"], _cmd.get("action"), _cmd.get("note"))
                        return {"command": _cmd}
                    entry = store.append_incoming(_chat_key, nm["mid"], nm["ts"], nm["sender_id"],
                                                  nm["sender_name"], nm["text"], media=nm["media"])
                    if not entry:
                        return None                     # 没落库 ⇒ 判失败，交给 process_batch 重试
                    # ── 系统自动回拍：别人拍一拍机器人 → 延迟 ~18 秒后按概率回拍（90%）──
                    if str(nm.get("text") or "").startswith("[拍一拍]"):
                        _schedule_poke_back(wechat, store, _chat_key, _wxid, _g["name"], nm)
                    orch.on_incoming(_chat_key)
                    return entry

                st = listener_watermark.process_batch(chat_key, new, _handle_one, wm,
                                                      log=_wm_log, deadletter_path=_dl_path)
                if st.get("failed"):
                    log.warning("群[%s]本批 %d 条失败（已记 dead-letter 并越过）· 成功 %d · 水位 %s",
                                g["name"], st["failed"], st["processed"], st["advanced_to"])
        except Exception as e:
            log.error("轮询循环异常：%s", e)
        time.sleep(poll_interval)
    try:
        wm.flush()
    except Exception:
        pass

    log.info("机器人已退出")


def _start_timer_holiday_loop(orch, get_wc, interval_s: float = 20.0):
    """计时提醒 + 节假日问候的巡检（第 12/13 条）。

    为什么单独一个循环、而不是并进主动话题循环：两者的周期差两个数量级（提醒要准、话题是小时级），
    而且**到点必须照发**——所以这里只做两件小事，任何异常都吞掉并留日志，绝不让它拖垮主循环。
    暂停中一条都不发（`timers.run_once(paused=True)` 直接返回、也不推进状态 ⇒ 恢复后补发）。
    """
    state = {"timer": None}

    def tick():
        try:
            cfg = get_config()
            paused = bool(getattr(orch, "paused", False) or getattr(orch, "stopped", False))
            if (cfg.get("timers") or {}).get("enabled") is not False:
                def _send(chat_key, text):
                    try:
                        r = orch.sender.send_text_batch(chat_key, text)
                        return bool((r or {}).get("sent"))
                    except Exception as e:
                        log.warning("定时提醒发送被拦/失败：%s", e)
                        return False
                st = timers.run_once(_send, paused=paused,
                                     muted=lambda ck: bool(tier_control.is_muted(ck)), log=log)
                if st.get("sent") or st.get("failed"):
                    log.info("定时巡检：到期 %s · 发出 %s · 失败 %s", st.get("due"), st.get("sent"), st.get("failed"))
            if not paused:
                # ⚠️ 每次都**现取**句柄：启动那一刻微信没开时这里是 None（晚接入后要能自己接上），
                #    传死了 None 就永远做不了节日问候（老代码就是这么写的）。
                wc = get_wc() if callable(get_wc) else get_wc
                if wc is None:
                    return
                try:
                    for g in holidays.due_greetings(cfg, wc.list_groups()):
                        try:
                            r = orch.sender.send_text_batch(g["chat_key"], g["text"])
                        except Exception as e:
                            log.warning("节日问候发送失败（%s）：%s", g.get("name"), e)
                            continue
                        if (r or {}).get("sent"):
                            holidays.mark_greeted(g["day"], g["chat_key"])
                            log.info("节日问候已发出：%s（%s）", g.get("name"), g.get("festival"))
                except Exception as e:
                    log.debug("节日问候巡检异常：%s", e)
        except Exception as e:
            log.warning("定时巡检异常：%s", e)
        finally:
            try:
                if not getattr(orch, "stopped", False):
                    t = threading.Timer(max(5.0, float(interval_s)), tick)
                    t.daemon = True
                    state["timer"] = t
                    t.start()
            except Exception:
                pass

    t0 = threading.Timer(3.0, tick)
    t0.daemon = True
    state["timer"] = t0
    t0.start()
    log.info("定时巡检已启动（计时提醒 + 节假日问候，%s 秒一跳）", interval_s)
    return state


def _auto_pythonw():
    """用 python.exe 直接启动时自动改由 pythonw 无窗口运行。

    · 双击 persona_morph.py / 命令行 python persona_morph.py：默认都不再常驻黑框（自动转 pythonw）；
    · 调试需要看窗口：加 --foreground 参数或设环境变量 WXAGENT_FOREGROUND=1；
    · 一键启动（启动机器人.vbs / watchdog.py）本来就走 pythonw，不受影响。
    """
    if "--foreground" in sys.argv or os.environ.get("WXAGENT_FOREGROUND") == "1":
        return
    if os.name != "nt" or getattr(sys, "frozen", False):
        return
    exe = sys.executable or ""
    if not exe.lower().endswith("python.exe"):
        return  # 已是 pythonw / pyw / 其他解释器
    try:
        pyw = exe[:-10] + "pythonw.exe"
        if os.path.exists(pyw):
            flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED|CREATE_NEW_PROCESS_GROUP|CREATE_NO_WINDOW
            subprocess.Popen([pyw, os.path.abspath(__file__)] + sys.argv[1:],
                             creationflags=flags,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            sys.exit(0)
    except Exception:
        pass


if __name__ == "__main__":
    _auto_pythonw()  # 无窗口兜底（调试用 --foreground 保留窗口）
    sys.exit(main() or 0)
