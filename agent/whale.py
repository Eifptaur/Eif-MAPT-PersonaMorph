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

import base64
import json
import os
import re
import threading
import time

from .llm import query_balance
from .model_prices import ( # noqa: F401  （价目表唯一来源，见该模块抬头）
    BASE_PRICE, PRO_PRICE, PRICING, PEAK_HOURS,
    price_for, is_peak_time, cost_of, usage_parts, billable_output,
)

# ── 峰谷定价 / 计费口径 ─────────────────────────────────────────────────────
# ⛔ 价目表**不再写在本文件**，全部来自 `agent/model_prices.py`（照抄上游
#    dsh-whale-widget@0.3.5）。本文件只保留导入，避免"三份表互相打架"（同一个 usage 在控制台
#    的「今日已用」与统计里算出两个数）。上游 0.3.5 的两处变更都在那个模块的抬头里写明了：
# ①Flash 系列 降价（0.05/1.5/4.5 → 0.02/1/4）；②reasoning ⊆ output ⇒ 输出只算一次。


# 自定义角色/素材的图片 dataURL（只认 base64 的 image/*；前端送 png 或 gif）
_RE_IMG_DATAURL = re.compile(r"^data:(image/[A-Za-z0-9.+-]+);base64,(.+)$", re.S)
_RE_AUDIO_DATAURL = re.compile(r"^data:(audio/[A-Za-z0-9.+-]+);base64,(.+)$", re.S)


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
                data.setdefault("roles", [])
                data.setdefault("assets", {})
                data.setdefault("audioGroups", [])
                return data
        except Exception:
            pass
        return {"size": {}, "days": {}, "lastTurn": {}, "roles": [],
                "assets": {}, "audioGroups": []}

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

        ⛔ 公式搬到 `agent/model_prices.py::cost_of`（**唯一实现**，与上游 0.3.5 一致）：
        输出侧只按 completion 计费 —— `reasoningTokens ⊆ outputTokens`，旧实现
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

    # ── 上游 0.3.x 新增接口──────────
    # 分工：**能落地的落地**（泡泡配置 / 音频设置＝纯配置，写我们自己的 state 就行），
    # 其余（角色库 / 泡泡图片上传 / 录音包 / 多厂商余额 / 账本校正 / Codex 模式）
    # 上游是宿主侧（Node/DSH 事件系统）才有的能力，本移植版**如实回"不支持"** ——
    # 不回假 `ok:true`（那会让界面显示假数据），客户端对 `ok:false` 一律保留默认值（实测代码：
    # `if (d && d.ok && d.config)` / `if (!d || !d.ok …) return`），所以界面是**干净降级**。
    # 上游有、本移植版**确实没有**的接口（如实返回"不支持"，客户端保留默认值）。
    # 注意：随着能力补齐，条目要从这里**删掉** —— 留在表里等于把已实现的功能继续报成"不支持"。
    _UNSUPPORTED = ("api-models.json", "usage-records.json", "usage-settings.json",
                    "balance-adjustments.json", 
                    "sound/")
    _CFG_KEYS = {"bubble.json": ("bubble", "config"), "audio.json": ("audio", "settings")}
    _CFG_MAX = 256 * 1024 # 配置上限（防一个前端 bug 把 state 撑爆）

    def unsupported(self, name: str) -> dict:
        """上游 0.3.x 有、本移植版没有的那批接口 —— 如实说不支持（客户端会保留默认值）。"""
        return {"ok": False, "why": "本移植版（群相控制台）暂无此能力：%s。"
                                    "该功能依赖上游宿主侧能力，当前版本暂未提供"
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

    # ── 素材库（角色图 / 气泡图 / 音频片段共用一套）────────────────────────
    #
    # 三种"用户自己导入的东西"形状完全一样：上传一个 dataURL → 得到 id → 能列、能删、能按 id
    # 取字节。所以共用一份存储：索引进状态文件的 `assets.<kind>`，字节落 `<数据目录>/<子目录>/`。
    # kind ∈ {"role","bubble","audio"}。

    _ASSET_DIRS = {"role": "roleimg", "bubble": "bubbleimg", "audio": "audiofrag"}
    _ASSET_MAX = 3 * 1024 * 1024

    def _assets_dir(self, kind: str) -> str:
        d = os.path.join(self._data_dir, self._ASSET_DIRS.get(kind, "assets"))
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return d

    def _assets(self, kind: str) -> list:
        try:
            got = (self._state.get("assets") or {}).get(kind)
        except Exception:  # noqa: BLE001
            got = None
        return list(got) if isinstance(got, list) else []

    def _assets_set(self, kind: str, items: list) -> None:
        assets = dict(self._state.get("assets") or {})
        assets[kind] = items
        self._state["assets"] = assets

    def asset_save(self, kind: str, name: str, data_url: str,
                   extra: dict | None = None, limit: int = 60) -> dict:
        """存一份素材。失败一律给原因（不写半个垃圾文件）。"""
        m = _RE_IMG_DATAURL.match(str(data_url or "")) or _RE_AUDIO_DATAURL.match(str(data_url or ""))
        if not m:
            return {"ok": False, "error": "素材格式不支持（需要 base64 的 dataURL）"}
        try:
            raw = base64.b64decode(m.group(2), validate=False)
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "素材解码失败"}
        if not raw:
            return {"ok": False, "error": "素材为空"}
        if len(raw) > self._ASSET_MAX:
            return {"ok": False, "error": "素材过大（上限 %d MB）"
                    % (self._ASSET_MAX // 1024 // 1024)}
        with self._lock:
            items = self._assets(kind)
            if len(items) >= limit:
                return {"ok": False, "error": "数量已达上限（%d）" % limit}
            aid = "%s%s" % (kind[0], format(int(time.time() * 1000), "x"))
            ext = {"image/png": ".png", "image/gif": ".gif", "image/jpeg": ".jpg",
                   "image/webp": ".webp", "audio/mpeg": ".mp3", "audio/wav": ".wav",
                   "audio/x-wav": ".wav", "audio/mp4": ".m4a", "audio/ogg": ".ogg"}.get(
                       m.group(1).lower(), ".bin")
            try:
                with open(os.path.join(self._assets_dir(kind), aid + ext), "wb") as f:
                    f.write(raw)
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": "素材写入失败：%s" % str(e)[:60]}
            item = {"id": aid, "name": str(name or aid)[:40], "file": aid + ext,
                    "createdAt": int(time.time() * 1000), "size": len(raw),
                    "mime": m.group(1).lower()}
            if isinstance(extra, dict):
                item.update(extra)
            items.append(item)
            self._assets_set(kind, items)
            self._save_state()
            return {"ok": True, "item": item, "items": items}

    def asset_delete(self, kind: str, aid: str) -> bool:
        aid = str(aid or "")
        if not aid:
            return False
        with self._lock:
            keep, gone = [], None
            for it in self._assets(kind):
                if isinstance(it, dict) and str(it.get("id")) == aid:
                    gone = it
                else:
                    keep.append(it)
            self._assets_set(kind, keep)
            self._save_state()
            if isinstance(gone, dict) and gone.get("file"):
                try:
                    os.remove(os.path.join(self._assets_dir(kind),
                                           os.path.basename(str(gone["file"]))))
                except Exception:  # noqa: BLE001
                    pass
            return gone is not None

    def asset_store_bytes(self, kind: str, aid: str) -> bytes | None:
        aid = os.path.basename(str(aid or ""))
        if not aid:
            return None
        for it in self._assets(kind):
            if isinstance(it, dict) and str(it.get("id")) == aid:
                fn = os.path.basename(str(it.get("file") or ""))
                if not fn:
                    return None
                try:
                    with open(os.path.join(self._assets_dir(kind), fn), "rb") as f:
                        return f.read()
                except Exception:  # noqa: BLE001
                    return None
        return None

    # ── 角色（自定义形象）──────────────────────────────────────────────────
    #
    # 前端契约（widget.js 实测调用）：
    #   GET  roles.json                    → {"ok":true,"roles":[{"id","name","pinned","createdAt"}]}
    #   POST roles.json      {name,image}  → {"ok":true,"roles":[…]}   导入（image 是 dataURL）
    #   POST role-pin.json   {id,pinned}   → {"ok":true,"roles":[…]}   置顶
    #   POST role-delete.json{id}          → {"ok":true,"roles":[…]}   删除
    #   GET  role-image.png?id=X           → 图片字节
    # ⛔ 列表里**必须**含内置的 default（前端按 `r.id === 'default'` 判定不可删），
    #    且每项带 `createdAt`（前端导入后按它挑"最新的那个"自动切换）。

    _ROLE_MAX = 40

    def _roles_list(self) -> list:
        out = [{"id": "default", "name": "小鲸鱼", "pinned": True, "createdAt": 0}]
        for r in self._assets("role"):
            if not isinstance(r, dict):
                continue
            rid = str(r.get("id") or "")
            if not rid:
                continue
            out.append({"id": rid, "name": str(r.get("name") or rid)[:20],
                        "pinned": bool(r.get("pinned")),
                        "createdAt": int(r.get("createdAt") or 0)})
        return out

    def roles_payload(self) -> dict:
        with self._lock:
            return {"ok": True, "roles": self._roles_list()}

    def save_role(self, obj) -> dict:
        if not isinstance(obj, dict):
            return {"ok": False, "error": "missing body"}
        name = str(obj.get("name") or "").strip()[:20] or "自定义角色"
        got = self.asset_save("role", name, obj.get("image"), limit=self._ROLE_MAX)
        if not got.get("ok"):
            return {"ok": False, "error": got.get("error")}
        with self._lock:
            return {"ok": True, "roles": self._roles_list()}

    def pin_role(self, obj) -> dict:
        if not isinstance(obj, dict):
            return {"ok": False, "error": "missing body"}
        rid = str(obj.get("id") or "")
        with self._lock:
            items = self._assets("role")
            for it in items:
                if isinstance(it, dict) and str(it.get("id")) == rid:
                    it["pinned"] = bool(obj.get("pinned"))
                    break
            self._assets_set("role", items)
            self._save_state()
            return {"ok": True, "roles": self._roles_list()}

    def delete_role(self, obj) -> dict:
        if not isinstance(obj, dict):
            return {"ok": False, "error": "missing body"}
        rid = str(obj.get("id") or "")
        if not rid or rid == "default":
            return {"ok": False, "error": "内置角色不可删除"}
        self.asset_delete("role", rid)
        with self._lock:
            return {"ok": True, "roles": self._roles_list()}

    def role_image_bytes(self, role_id: str) -> bytes | None:
        return self.asset_store_bytes("role", role_id)

    # ── 气泡图库 ───────────────────────────────────────────────────────────
    #
    #   GET  bubble-imgs.json                    → {"ok":true,"images":[{"id","name"}]}
    #   POST bubble-img-upload.json {action:'upload',name,data} → {"ok":true,"images":[…]}
    #   POST bubble-img-upload.json {action:'delete',id}        → {"ok":true,"images":[…]}
    #   GET  bubble-img.png?id=X                 → 图片字节

    def bubble_imgs_payload(self) -> dict:
        with self._lock:
            imgs = [{"id": str(x.get("id")), "name": str(x.get("name") or "")}
                    for x in self._assets("bubble") if isinstance(x, dict) and x.get("id")]
            return {"ok": True, "images": imgs}

    def bubble_img_action(self, obj) -> dict:
        if not isinstance(obj, dict):
            return {"ok": False, "error": "missing body"}
        action = str(obj.get("action") or "upload")
        if action == "delete":
            self.asset_delete("bubble", obj.get("id"))
            return self.bubble_imgs_payload()
        got = self.asset_save("bubble", obj.get("name") or "", obj.get("data"))
        if not got.get("ok"):
            return {"ok": False, "error": got.get("error")}
        return self.bubble_imgs_payload()

    def bubble_img_bytes(self, img_id: str) -> bytes | None:
        return self.asset_store_bytes("bubble", img_id)

    # ── 音效（组 + 片段）───────────────────────────────────────────────────
    #
    #   GET  audio.json → {"ok":true,"groups":[{id,name,press,release,preset,pinned}],
    #                       "fragments":[{id,name,preset}]}
    #   POST audio.json {action:'save-group', id?, name, press, release} → {"ok":true,"groups":[…]}
    #   POST audio.json {action:'pin-group',     id, pinned}             → {"ok":true,"groups":[…]}
    #   POST audio.json {action:'delete-group',  id}                     → {"ok":true,"groups":[…]}
    #   GET  audio-fragment.wav?id=X → 片段字节（槽位存的是片段 id，或用户上传的 'frag:xxx'）
    # ⛔ 内置两组（duck / fx1）不可删（前端也按 preset 判定）；用户组的上传片段走素材库。

    _AUDIO_PRESETS = (
        {"id": "duck", "name": "小黄鸭", "press": "", "release": "", "preset": True, "pinned": True},
        {"id": "fx1", "name": "音效1", "press": "", "release": "", "preset": True, "pinned": False},
    )

    def _audio_groups(self) -> list:
        out = [dict(g) for g in self._AUDIO_PRESETS]
        for g in (self._state.get("audioGroups") or []):
            if not isinstance(g, dict) or not g.get("id"):
                continue
            if str(g.get("id")) in ("duck", "fx1"):
                continue
            out.append({"id": str(g.get("id")), "name": str(g.get("name") or g.get("id"))[:20],
                        "press": str(g.get("press") or ""), "release": str(g.get("release") or ""),
                        "preset": False, "pinned": bool(g.get("pinned"))})
        return out

    def audio_payload(self) -> dict:
        with self._lock:
            frags = [{"id": str(x.get("id")), "name": str(x.get("name") or ""), "preset": False}
                     for x in self._assets("audio") if isinstance(x, dict) and x.get("id")]
            return {"ok": True, "groups": self._audio_groups(), "fragments": frags}

    def audio_action(self, obj) -> dict:
        if not isinstance(obj, dict):
            return {"ok": False, "error": "missing body"}
        action = str(obj.get("action") or "")
        if action == "save-group":
            name = str(obj.get("name") or "").strip()[:20]
            if not name:
                return {"ok": False, "error": "组名不能为空"}
            with self._lock:
                groups = [g for g in (self._state.get("audioGroups") or [])
                          if isinstance(g, dict)]
                gid = str(obj.get("id") or "") or ("g%s" % format(int(time.time() * 1000), "x"))
                found = None
                for g in groups:
                    if str(g.get("id")) == gid:
                        found = g
                        break
                payload = {"id": gid, "name": name,
                           "press": str(obj.get("press") or ""),
                           "release": str(obj.get("release") or "")}
                if found is None:
                    payload["pinned"] = False
                    payload["createdAt"] = int(time.time() * 1000)
                    groups.append(payload)
                else:
                    payload["pinned"] = bool(found.get("pinned"))
                    payload["createdAt"] = int(found.get("createdAt") or 0)
                    found.update(payload)
                self._state["audioGroups"] = groups
                self._save_state()
            return self.audio_payload()
        if action == "pin-group":
            gid = str(obj.get("id") or "")
            with self._lock:
                for g in (self._state.get("audioGroups") or []):
                    if isinstance(g, dict) and str(g.get("id")) == gid:
                        g["pinned"] = bool(obj.get("pinned"))
                        break
                self._save_state()
            return self.audio_payload()
        if action == "delete-group":
            gid = str(obj.get("id") or "")
            if gid in ("duck", "fx1"):
                return {"ok": False, "error": "内置音效组不可删除"}
            with self._lock:
                self._state["audioGroups"] = [
                    g for g in (self._state.get("audioGroups") or [])
                    if not (isinstance(g, dict) and str(g.get("id")) == gid)]
                self._save_state()
            return self.audio_payload()
        if action == "upload-fragment":
            # 前端送的是 {name, audio: dataURL}（注意字段名是 audio，不是 data）
            got = self.asset_save("audio", obj.get("name") or "", obj.get("audio"))
            if not got.get("ok"):
                return {"ok": False, "error": got.get("error")}
            return self.audio_payload()
        if action == "delete-fragment":
            self.asset_delete("audio", obj.get("id"))
            return self.audio_payload()
        return {"ok": False, "error": "未知动作：%s" % action[:20]}

    def audio_fragment_bytes(self, frag_id: str) -> bytes | None:
        """片段的字节：用户上传的（id 形如 `frag:xxx`）走素材库；预设片段走随包素材。"""
        fid = os.path.basename(str(frag_id or ""))
        if not fid:
            return None
        if fid.startswith("frag:"):
            return self.asset_store_bytes("audio", fid[5:])
        got = self.asset_store_bytes("audio", fid) # 兼容"不带前缀的素材库 id"
        if got is not None:
            return got
        preset = {"ya1": "Ya1.mp3", "ya2": "Ya2.mp3", "d1": "D1.mp3", "d2": "D2.mp3"}
        return self.asset_bytes(preset.get(fid.lower(), ""))

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
        """按压/松手音效。

        ⛔ 顺序：**先看用户自定义组的槽位**（值是该组的片段 id，空串 = 显式静音），
        取不到再回落内置两组（duck→Ya1/Ya2，fx1→D1/D2）。前端 v752 之后首选"片段路由"
        （`audio-fragment.wav`），老路由是它的兜底 —— 两条都得通，声音才不会时有时无。
        """
        gid = str(sound_set or "duck")
        for g in self._audio_groups():
            if g.get("id") == gid and not g.get("preset"):
                slot = str(g.get(kind) or "")
                if slot == "":
                    return None # 显式留空 = 该事件静音
                return self.audio_fragment_bytes(slot)
        table = {
            ("press", "duck"): "Ya1.mp3",
            ("release", "duck"): "Ya2.mp3",
            ("press", "fx1"): "D1.mp3",
            ("release", "fx1"): "D2.mp3",
        }
        return self.asset_bytes(table.get((kind, gid if gid == "fx1" else "duck"), ""))
