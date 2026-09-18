# -*- coding: utf-8 -*-
"""DSH 小鲸鱼余额挂件（DeepSeek-Balance-Whale-Widget）适配层。

原版是 DSH 插件（whale-widget/lib-index.js 宿主侧走 Node/DSH 事件系统），
这里把它整体迁移进来：
  - 浏览器侧脚本：whale-widget/client/widget.js（原样提取自原项目 WIDGET_JS，零改动）
  - 服务端：本模块按原项目的 /dsh-whale/* 接口约定用 Python 重新实现，
    挂到 Persona Morph Web 控制台（agent/webui.py）下。

接口约定（与原版一致）：
  GET  /dsh-whale/widget.js       浏览器侧脚本（按 token 注入，webui 处理）
  GET  /dsh-whale/image.png       小鲸鱼本体图（assets/DSniang1.png）
  GET  /dsh-whale/rua.gif         随机台词 gif（缺失时前端静默降级）
  GET  /dsh-whale/sound/(press|release).mp3?set=duck|fx1  按压/松手音效
  GET  /dsh-whale/balance.json    {ok,totalBalance,currency,todayUsage,isPeak}
  GET  /dsh-whale/size.json       前端配置（scale/音量/模式/开关…）
  PUT  /dsh-whale/size.json       保存前端配置（webui 的 do_POST 转调）
  GET  /dsh-whale/last-turn.json  {ok,seq,turn,amount,tokens}，seq 递增

与 DSH 版的两处适配：
  1) 「今日已用」不再依赖平台令牌/余额差值，直接用本机每轮 API 调用的真实
     usage 按峰谷定价折算（调用发生时才记账，机器人不跑就不计费，比余额差值更准）。
  2) 「每轮对话消耗」= 一次唤醒里所有 LLM 调用的总成本（会话结束时结算），
     替代 DSH 的 turn/end 事件。
"""
from __future__ import annotations

import json
import os
import threading
import time

from .llm import query_balance
from .model_prices import (          # noqa: F401  （价目表唯一来源，见该模块抬头）
    BASE_PRICE, PRO_PRICE, PRICING, PEAK_HOURS,
    price_for, is_peak_time, cost_of, usage_parts, billable_output,
)

# ── 峰谷定价 / 计费口径 ─────────────────────────────────────────────────────
# ⛔ 2026-09-19：价目表**不再写在本文件**，全部来自 `agent/model_prices.py`（照抄上游
#    dsh-whale-widget@0.3.5）。本文件只保留导入，避免"三份表互相打架"（同一个 usage 在控制台
#    的「今日已用」与统计里算出两个数）。上游 0.3.5 的两处变更都在那个模块的抬头里写明了：
#      ①Flash 系列 2026-09-10 降价（0.05/1.5/4.5 → 0.02/1/4）；②reasoning ⊆ output ⇒ 输出只算一次。


def _today_key() -> str:
    return time.strftime("%Y-%m-%d")


class WhaleWidget:
    """小鲸鱼挂件服务端：记账 + 每轮消耗 + 前端配置持久化。"""

    def __init__(self, data_dir: str, asset_dir: str | None = None):
        self._data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self._state_path = os.path.join(data_dir, "whale-state.json")
        # 静态资源目录（whale-widget/assets）
        self._asset_dir = asset_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "whale-widget", "assets")
        self._lock = threading.Lock()
        self._state = self._load_state()
        # 当前这一轮（一次唤醒）内的累计：{"model","cost","tokens","start_ts"}
        self._cur = None

    # ── 状态持久化 ─────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("size", {})
                data.setdefault("days", {})
                data.setdefault("lastTurn", {})
                return data
        except Exception:
            pass
        return {"size": {}, "days": {}, "lastTurn": {}}

    def _save_state(self):
        try:
            tmp = self._state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._state_path)
        except Exception:
            pass

    def _usage_cost(self, usage: dict, model: str, ts: float) -> tuple:
        """按峰谷定价折算一次调用的 (成本, token 数)。

        ⛔ 2026-09-19：公式搬到 `agent/model_prices.py::cost_of`（**唯一实现**，与上游 0.3.5 一致）：
        输出侧只按 completion 计费 —— `reasoningTokens ⊆ outputTokens`，旧写法
        `(completion + reasoning)` 会把思考**重复计费**（上游 issue #89 / PR #83 实测偏高约一倍）。
        """
        return cost_of(usage or {}, model, ts)

    # ── 记账 API（由 persona_morph 调用）─────────────────────────────────────

    def note_call(self, model: str, usage: dict, ts: float | None = None):
        """每次 LLM 调用成功后计入当前轮的累计（价格按调用时刻的峰谷档位）。"""
        if not usage:
            return
        ts = float(ts or time.time())
        cost, tokens = self._usage_cost(usage, model, ts)
        if cost <= 0 and tokens <= 0:
            return
        with self._lock:
            if self._cur is None:
                self._cur = {"cost": 0.0, "tokens": 0}
            self._cur["cost"] += cost
            self._cur["tokens"] += tokens

    def note_turn_done(self):
        """会话结束：把当前轮累计结算进“最近一轮消耗”并记入今日账本。"""
        with self._lock:
            cur, self._cur = self._cur, None
            if not cur or cur.get("cost", 0) <= 0:
                return None
            lt = self._state.get("lastTurn") or {}
            seq = int(lt.get("seq") or 0) + 1
            turn = int(lt.get("turn") or 0) + 1
            last = {"seq": seq, "turn": turn,
                    "amount": round(cur["cost"], 6), "tokens": int(cur["tokens"]),
                    "ts": int(time.time() * 1000)}
            self._state["lastTurn"] = last
            day = _today_key()
            days = self._state.setdefault("days", {})
            days[day] = round(float(days.get(day) or 0) + cur["cost"], 6)
            # 只保留最近 30 天
            try:
                keep = sorted(k for k in days if k <= day)[-30:]
                self._state["days"] = {k: days[k] for k in keep} if len(days) > 30 else days
            except Exception:
                pass
            self._save_state()
            return last

    def today_usage(self) -> float:
        with self._lock:
            days = self._state.get("days") or {}
            return float(days.get(_today_key()) or 0.0)

    # ── HTTP 接口（webui 转调）─────────────────────────────────────────

    def balance_payload(self) -> dict:
        """GET /dsh-whale/balance.json"""
        try:
            b = query_balance()
        except Exception as e:
            return {"ok": False, "code": "ERROR", "error": str(e)[:200]}
        return {
            "ok": True,
            "totalBalance": float(b.get("total_balance") or 0),
            "currency": str(b.get("currency") or "CNY"),
            "todayUsage": round(self.today_usage(), 6),
            "isPeak": is_peak_time(time.time()),
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def size_payload(self) -> dict:
        """GET /dsh-whale/size.json（返回 {} 时前端用默认值）"""
        with self._lock:
            return dict(self._state.get("size") or {})

    # ── 上游 0.3.x 新增接口（移植范围见 whale-widget/PORT-NOTES.md）──────────
    # 分工：**能落地的落地**（泡泡配置 / 音频设置＝纯配置，写我们自己的 state 就行），
    # 其余（角色库 / 泡泡图片上传 / 录音包 / 多厂商余额 / 账本校正 / Codex 模式）
    # 上游是宿主侧（Node/DSH 事件系统）才有的能力，本移植版**如实回"不支持"** ——
    # 不回假 `ok:true`（那会让界面显示假数据），客户端对 `ok:false` 一律保留默认值（实测代码：
    # `if (d && d.ok && d.config)` / `if (!d || !d.ok …) return`），所以界面是**干净降级**。
    _UNSUPPORTED = ("api-models.json", "usage-records.json", "usage-settings.json",
                    "balance-adjustments.json", "roles.json", "role-image.png",
                    "role-pin.json", "role-delete.json", "bubble-imgs.json",
                    "bubble-img.png", "bubble-img-upload.json", "audio-fragment.wav",
                    "sound/")
    _CFG_KEYS = {"bubble.json": ("bubble", "config"), "audio.json": ("audio", "settings")}
    _CFG_MAX = 256 * 1024          # 配置上限（防一个前端 bug 把 state 撑爆）

    def unsupported(self, name: str) -> dict:
        """上游 0.3.x 有、本移植版没有的那批接口 —— 如实说不支持（客户端会保留默认值）。"""
        return {"ok": False, "why": "本移植版（群相控制台）暂无此能力：%s。"
                                    "该功能依赖上游 DSH 宿主侧，等移植进度见 whale-widget/PORT-NOTES.md"
                                    % name, "unsupported": True}

    def cfg_payload(self, name: str) -> dict:
        """GET 泡泡 / 音频配置：没存过就回 `{"ok": false}`（前端用内置默认值）。"""
        key, field = self._CFG_KEYS[name]
        with self._lock:
            cfg = (self._state.get(key) or {}).get("config")
        if not isinstance(cfg, dict) or not cfg:
            return {"ok": False}
        return {"ok": True, field: cfg}

    def save_cfg(self, name: str, obj) -> dict:
        """POST 泡泡 / 音频配置：原样存（有上限），存完回显一份（前端要拿它刷界面）。"""
        key, field = self._CFG_KEYS[name]
        if not isinstance(obj, dict):
            return {"ok": False, "error": "missing body"}
        try:
            import json as _json
            if len(_json.dumps(obj, ensure_ascii=False)) > self._CFG_MAX:
                return {"ok": False, "error": "配置过大"}
        except Exception:
            return {"ok": False, "error": "配置无法序列化"}
        with self._lock:
            self._state[key] = {"config": obj, "at": int(time.time() * 1000)}
            self._save_state()
        return {"ok": True, field: obj}

    def save_size(self, obj: dict) -> dict:
        """PUT /dsh-whale/size.json"""
        if not isinstance(obj, dict):
            return {"ok": False, "error": "missing body"}
        if not isinstance(obj.get("scale"), (int, float)) or isinstance(obj.get("scale"), bool):
            return {"ok": False, "error": "missing scale"}
        with self._lock:
            size = dict(self._state.get("size") or {})
            size.update(obj)
            self._state["size"] = size
            self._save_state()
        return {"ok": True}

    def last_turn_payload(self) -> dict:
        """GET /dsh-whale/last-turn.json"""
        with self._lock:
            lt = self._state.get("lastTurn") or {}
            if lt:
                return {"ok": True, "seq": int(lt.get("seq") or 0),
                        "turn": lt.get("turn"), "amount": lt.get("amount"),
                        "tokens": lt.get("tokens"), "ts": lt.get("ts")}
        return {"ok": True, "seq": 0, "turn": None, "amount": None, "tokens": None, "ts": None}

    # ── 静态资源 ───────────────────────────────────────────────────────

    def asset_bytes(self, name: str) -> bytes | None:
        """读取 whale-widget/assets 下的静态文件。"""
        safe = os.path.basename(name)
        path = os.path.join(self._asset_dir, safe)
        try:
            with open(path, "rb") as f:
                return f.read()
        except Exception:
            return None

    def sound_bytes(self, kind: str, sound_set: str) -> bytes | None:
        """按压/松手音效：press→Ya1/D1，release→Ya2/D2。"""
        table = {
            ("press", "duck"): "Ya1.mp3",
            ("release", "duck"): "Ya2.mp3",
            ("press", "fx1"): "D1.mp3",
            ("release", "fx1"): "D2.mp3",
        }
        return self.asset_bytes(table.get((kind, sound_set if sound_set == "fx1" else "duck"), ""))
