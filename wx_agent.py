# -*- coding: utf-8 -*-
"""wx-agent 主程序 —— 微信智能机器人。

整合自两个项目：
  - 智能大脑：qq-agent（无状态会话 / 响应档位 / 工具集 / 记忆 / 联网搜索 / 人设）
  - 微信接入：wechat-deepseek-bot（wechatauto UIA 无注入读消息 + 屏幕自动化发送）

运行：python wx_agent.py   （或双击 scripts/启动机器人.bat）
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import signal
import sys
import threading
import time
import webbrowser
from collections import deque
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None
sys.stderr.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stderr, "reconfigure") else None

from agent.config import get_config, save_config
from agent.llm import (add_usage, chat_completion, chat_completion_with_retry,
                       empty_usage, estimate_cost, is_retryable_error, query_balance)
from agent.memory import MemoryStore
from agent.prompt import build_system_prompt, build_user_prompt, resolve_context_tier
from agent.sender import SendQueue
from agent.store import ChatStore
from agent.tools import build_tool_defs, execute_tool, to_openai_tools
from agent.wechat import WeChatAdapter, WeChatError
from agent.whale import WhaleWidget
from agent.webui import WebUI

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(LOG_DIR, "wx_agent.log"), encoding="utf-8"),
    ],
)
log = logging.getLogger("wx-agent")

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
_ring.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger().addHandler(_ring)


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
        self.stats = {"sessions": 0, "tokens": 0, "sent": 0, "calls": 0, "cost": 0.0}
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
            if not self.paused and not self.stopped:
                if self.store.unread_count(chat_key) > 0:
                    self.schedule_wake(chat_key, get_config().get("drain_delay_ms", 1200))
                self._maybe_consolidate(chat_key)

    # ── 核心循环 ─────────────────────────────────────────────────────────

    def wake(self, chat_key: str):
        cfg = get_config()
        api = cfg.get("api", {})
        if not str(api.get("base_url") or "").strip() or not str(api.get("model") or "").strip():
            return  # 模型未配置：消息保留未读，不产生报错会话

        pending = self.store.peek_unread(chat_key, 200)
        if not pending:
            return

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

        tier_result = resolve_context_tier(pending, self_nickname, bot_name, self_id)
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
        else:
            # 处理成功：记录触发指纹，供 5 分钟去重判断
            try:
                self._last_trigger[chat_key] = (self._last_trigger.get(chat_key, ((), 0, False))[0],
                                                time.time(), True)
            except Exception:
                pass

    def run_agent(self, chat_key: str, trigger, tier_result, session: dict):
        cfg = get_config()
        kind, chat_id = chat_key.split(":", 1)
        chat_name = self.wechat.group_name(chat_id) if kind == "group" else chat_id
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
            "context_limit": tier_result["count"], "session": session,
        })
        session["prompt_chars"] = len(system_prompt) + len(user_prompt)
        session["model"] = cfg.get("api", {}).get("model")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        vision_enabled = cfg.get("api", {}).get("vision", True) is not False
        search_enabled = cfg.get("web_search", {}).get("enabled", True) is not False
        tool_defs = [d for d in self.tool_defs if not (
            (not vision_enabled and d["name"] == "get_message_images")
            or (not search_enabled and d["name"] in ("web_search", "web_fetch"))
        )]
        openai_tools = to_openai_tools(tool_defs)

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
            session["usage"]["calls"] += 1
            try:
                self.whale.note_call(session["model"], response.get("usage"))
            except Exception:
                pass
            session["activity"] = ""

            msg = response["message"]
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
                break  # 模型结束思考（文本不会发送）

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
        try:
            self.stats["cost"] += float(estimate_cost(session["usage"], session["model"])["cost"])
        except Exception:
            pass
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
            imp = self._extract_impressions(
                "你是聊天机器人的记忆模块，负责从聊天记录里提炼对某一位群友的长期印象。"
                "只提炼\"以后跟这个人打交道用得上\"的稳定特征，严格依据给定的发言，不要编造。"
                "输出必须是严格的 JSON 对象，格式：{\"impressions\":[\"…\"]}。每条不超过 120 字，宁少勿错。",
                "\n".join(sample) if sample else "（没有抓到该群友的发言）", max_keep)
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


def main():
    try:
        os.system("chcp 65001 >nul 2>&1")
    except Exception:
        pass

    cfg = get_config()
    log.info("===== wx-agent 启动 =====")
    problems = _check_prerequisites(cfg)
    if problems:
        log.warning("就绪度体检未通过：%s（机器人会读消息但不调用模型，配置好 config.json 后重启）", "；".join(problems))

    # 初始化微信接入（微信未登录时自动重试）
    wechat = None
    while wechat is None:
        try:
            wechat = WeChatAdapter(cfg)
        except Exception as e:
            log.warning("微信接入初始化失败：%s（微信可能尚未登录，10 秒后重试）", e)
            time.sleep(10)

    groups = wechat.list_groups()
    log.info("发现群聊 %d 个", len(groups))
    whitelist = cfg.get("wechat", {}).get("group_name_white_list") or []
    deny = set(cfg.get("deny", {}).get("groups") or [])
    targets = [g for g in groups if (not whitelist or g["name"] in whitelist) and g["name"] not in deny]
    log.info("目标群 %d 个：%s", len(targets), ", ".join(g["name"] for g in targets[:15]) if targets else "（白名单未匹配到任何群）")

    store = ChatStore(int(cfg.get("store", {}).get("max_messages_per_chat") or 0))
    memory = MemoryStore()
    sender = SendQueue(wechat, store)
    orch = Orchestrator(store, memory, sender, wechat)

    # ── Web 控制台 ─────────────────────────────────────────────────────
    target_wxids = {g["wxid"] for g in targets}

    def status_provider():
        gs = [{"name": g["name"], "wxid": g["wxid"], "target": g["wxid"] in target_wxids} for g in groups]
        return {
            "paused": orch.paused,
            "wechat_connected": wechat is not None,
            "model": cfg.get("api", {}).get("model", ""),
            "groups": gs,
            "running_chats": sorted(orch.running_chats),
            "stats": dict(orch.stats),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def test_api_fn():
        start = time.time()
        resp = chat_completion([{"role": "user", "content": "ping，请只回复 pong"}])
        latency = int((time.time() - start) * 1000)
        return {"ok": True, "latency_ms": latency, "model": resp.get("model"),
                "reply": str(resp.get("message", {}).get("content", ""))[:200]}

    def balance_fn():
        return query_balance()

    def selfcheck_fn():
        """一键体检：把「换电脑容易踩的坑」做成可自助检查的清单。

        只读检查（不动微信、不发消息）；点击类项会移动光标做命中测试。
        每项返回 ok/warn/fail + 说明 + 建议。
        """
        checks = []

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
                    "打开电脑微信并登录小号，别最小化")
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
            if gui is not None:
                # 真实点击自检：与拍一拍完全相同「移动+右键」逻辑，右键一条消息看菜单是否弹出
                try:
                    cr = wechat.click_self_test()
                    add("适配·点击实测(拍一拍同链路)", "ok" if cr.get("ok") else "fail",
                        cr.get("detail", ""),
                        "" if cr.get("ok") else "点击投递异常：检查是否在真实桌面启动(scripts\\启动机器人.bat)、"
                        "是否打开了群聊、机器是否卡顿/有拦截软件")
                except Exception as e:
                    add("适配·点击实测", "fail", str(e))
        except Exception as e:
            add("界面适配检查", "fail", str(e))

        add("拍一拍", "info", "请用「拍一拍诊断」按钮实测（定位/右键/验证一步一报告）")
        add("发送防重复", "info", "已启用 3 秒重复发送拦截（回车重试竞态防护）")

        ok_n = sum(1 for c in checks if c["status"] == "ok")
        warn_n = sum(1 for c in checks if c["status"] == "warn")
        fail_n = sum(1 for c in checks if c["status"] == "fail")
        return {"ok": fail_n == 0, "checks": checks,
                "summary": "通过 %d 项 / 注意 %d 项 / 失败 %d 项" % (ok_n, warn_n, fail_n)}

    def poke_test_fn():
        # 拍一拍诊断：找「最近有群友发过言」的目标群 + 该群最近发言的非机器人，
        # 完整跑一遍流程并输出分步结果
        try:
            if wechat is None or not targets:
                return {"ok": False, "error": "微信未就绪或没有目标群"}
            best = None  # (chat_key, wxid, 群名, 消息)
            for g in targets:
                chat_key = "group:%s" % g["wxid"]
                msgs = [m for m in orch.store.recent(chat_key, limit=100) if not m.get("self")]
                if msgs:
                    cand = (chat_key, g["wxid"], g["name"], msgs[-1])
                    if best is None or cand[3].get("ts", 0) > best[3].get("ts", 0):
                        best = cand
            if best is None:
                return {"ok": False, "error": "所有目标群存档里都没有群友消息，请先在群里@机器人说句话"}
            chat_key, wxid, gname, m = best
            result = wechat.poke_diag(wxid, m.get("sender_name") or m.get("sender_id"),
                                      m.get("sender_id") or "")
            result["target"] = {"name": m.get("sender_name"), "id": m.get("sender_id")}
            result["group"] = gname
            return result
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def shutdown_fn():
        log.info("收到停止指令，正在停止机器人…")
        try:
            orch.shutdown()
        except Exception:
            pass
        # 强制退出进程，确保完全停止
        threading.Timer(1.0, lambda: os._exit(0)).start()

    # ── 控制台访问口令：留空则启动时自动生成一串随机口令，避免和别人撞端口/被猜到 ──
    server_cfg = cfg.get("server", {})
    if not str(server_cfg.get("token") or "").strip():
        server_cfg["token"] = secrets.token_hex(24)  # 48 位随机十六进制
        save_config(cfg)
        log.info("已自动生成控制台访问口令（保存在 config.json 的 server.token）")

    webui = WebUI(status_provider, log_buffer, test_api_fn=test_api_fn, balance_fn=balance_fn,
                  pause_fn=lambda: orch.set_paused(True), resume_fn=lambda: orch.set_paused(False),
                  shutdown_fn=shutdown_fn, whale=orch.whale,
                  poke_test_fn=poke_test_fn, selfcheck_fn=selfcheck_fn)
    try:
        port = webui.start()
        if port:
            token = str(server_cfg.get("token") or "").strip()
            url = "http://127.0.0.1:%d" % port + (("/?token=" + token) if token else "")
            log.info("Web 控制台：%s", url)
            if server_cfg.get("auto_open_browser", True) is not False:
                try:
                    webbrowser.open(url)
                    log.info("已在默认浏览器打开控制台")
                except Exception:
                    pass
    except Exception as e:
        log.warning("Web 控制台启动失败：%s", e)

    # 初始化轮询游标（只处理启动之后的新消息，不重放历史）
    since_seq = {}
    for g in targets:
        since_seq[g["wxid"]] = wechat.latest_seq(g["wxid"])

    def _stop(signum=None, frame=None):
        log.info("收到退出信号，正在停止…")
        orch.shutdown()

    try:
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
    except (ValueError, OSError):
        pass

    poll_interval = max(1.0, float(cfg.get("wechat", {}).get("poll_interval") or 3))
    log.info("开始监听群消息（目标群 %d 个，轮询 %.1fs）… Ctrl+C 退出", len(targets), poll_interval)

    while not orch.stopped:
        try:
            for g in targets:
                if orch.paused:
                    continue
                wxid = g["wxid"]
                chat_key = "group:" + wxid
                try:
                    new = wechat.poll_new_messages(wxid, since_seq.get(wxid, 0), limit=50)
                except Exception as e:
                    log.debug("读取群[%s]异常：%s", g["name"], e)
                    continue
                if not new:
                    continue
                max_seq = since_seq.get(wxid, 0)
                for nm in new:
                    max_seq = max(max_seq, nm["sort_seq"])
                    store.append_incoming(chat_key, nm["mid"], nm["ts"], nm["sender_id"],
                                          nm["sender_name"], nm["text"], media=nm["media"])
                since_seq[wxid] = max_seq
                orch.on_incoming(chat_key)
        except Exception as e:
            log.error("轮询循环异常：%s", e)
        time.sleep(poll_interval)

    log.info("机器人已退出")


if __name__ == "__main__":
    sys.exit(main() or 0)
