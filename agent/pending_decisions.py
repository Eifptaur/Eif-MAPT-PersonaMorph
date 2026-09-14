# -*- coding: utf-8 -*-
"""待决台账：把「要用户拍板的事」落成**可排队、可追溯、不重复问**的数据。

为什么需要它（2026-09-14 用户口径「弹窗按你推荐的做」+「把弹窗切出来的那一秒，就应该立刻让它到后台」）：
  版本不匹配这类事**不能只在屏幕上闪一下**——用户当时可能不在电脑前、控制台可能根本没开；
  **关掉弹窗也不等于这件事没发生**。所以三条：
    ① 每一次要拍板的事都落 `data/pending_decisions.json`（原子写，temp + os.replace）；
    ② **同一件事已经问过、用户已表过态 ⇒ 不再重复问**（`asked()` 查得到）；
    ③ **✕＝什么都不做**：状态记 `dismissed`，条目**留着**（仍然算"问过了"），不是删掉。

本模块只负责"决策这件事"：开单、查单、落定、给动作；**动作由调用方执行**（控制台/版本门），
`apply_choice()` 只返回一个动作描述符——**绝不在这里装包、升版本、改微信**（那要用户显式点头）。
"""
from __future__ import annotations

import json
import logging
import os
import time

from .config import ROOT

log = logging.getLogger("persona-morph")

DECISIONS_PATH = os.path.join(ROOT, "data", "pending_decisions.json")
SCHEMA = 1
KEEP_MAX = 200                      # 台账上限（按时间保留最近的；防无限膨胀）

# ── 四选一：版本不匹配时的固定选项（用户口径：✕＝什么都不做）────────────────
# 为什么把"动作"和"文案"分开存：动作是机器判读用的（控制台据此决定按钮干什么），
# 文案是给人看的（改文案不许动动作语义）。
VERSION_OPTIONS = [
    {"key": "upgrade_adapter", "label": "一键升级适配层",
     "detail": "把微信适配层升到与本机微信版本配套的那个版本，升完重测一遍能力矩阵",
     "action": "upgrade_adapter"},
    {"key": "update_host", "label": "更新本体",
     "detail": "跑一次依赖自愈与版本体检，把本体依赖修到声明要求",
     "action": "heal_deps"},
    {"key": "allow_once", "label": "仅本次允许",
     "detail": "只对本次运行放行发送，重启后重新拦",
     "action": "allow_session"},
    {"key": "wechat_side", "label": "微信本身要处理",
     "detail": "只给排查指引，不装、不降级微信",
     "action": "guidance"},
]

OPTION_KEYS = tuple(o["key"] for o in VERSION_OPTIONS)


def path(p: str | None = None) -> str:
    return p or DECISIONS_PATH


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _empty() -> dict:
    return {"schema": SCHEMA, "items": []}


# ── 薄 I/O（原子写；读坏了不许把整份台账丢掉）──────────────────────────────
def load(p: str | None = None) -> dict:
    fp = path(p)
    if not os.path.exists(fp):
        return _empty()
    try:
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        if not isinstance(data.get("items"), list):
            data["items"] = []
        data.setdefault("schema", SCHEMA)
        return data
    except Exception as e:
        log.warning("待决台账读失败（当作空台账，不删文件）：%s", e)
        return _empty()


def save(data: dict, p: str | None = None) -> str:
    """原子写（temp + fsync + os.replace）——半个 JSON 会让"要用户拍板的事"整批消失。"""
    fp = path(p)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    items = list((data or {}).get("items") or [])
    if len(items) > KEEP_MAX:                       # 只留最近的，防膨胀
        items = sorted(items, key=lambda it: str(it.get("created_at") or ""))[-KEEP_MAX:]
    out = {"schema": SCHEMA, "items": items}
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, fp)
    return fp


# ── 台账基本操作 ───────────────────────────────────────────────────────
def pair_key(wechat: str = "", adapter: str = "") -> str:
    """同一件事的稳定 key：同一对「微信版本 × 适配层版本」只算一件事。"""
    return "%s|%s" % (str(wechat or "unknown"), str(adapter or "unknown"))


def make_id(kind: str, key: str) -> str:
    return "%s|%s" % (str(kind or "decision"), str(key or ""))


def get(decision_id: str, p: str | None = None) -> dict | None:
    for it in load(p).get("items") or []:
        if str(it.get("id")) == str(decision_id):
            return it
    return None


def find(kind: str, key: str, p: str | None = None) -> dict | None:
    return get(make_id(kind, key), p)


def asked(kind: str, key: str, p: str | None = None) -> bool:
    """这件事**问过没有**（不论用户表态是哪一个、哪怕是 ✕ 什么都不做）。"""
    return find(kind, key, p) is not None


def open_items(p: str | None = None) -> list:
    """还等着用户拍板的条目（控制台据此弹模态）。"""
    return [it for it in (load(p).get("items") or []) if str(it.get("status")) == "open"]


def open_item(kind: str, key: str, title: str, reason: str = "", options=None,
              wechat: str = "", adapter: str = "", extra: dict | None = None,
              reuse: bool = True, p: str | None = None) -> tuple:
    """开一张待决单。返回 `(条目, 是否这次新建)`。

    `reuse=True`（默认）时**幂等**：同一件事若已有条目——无论还开着（open）还是已经表过态
    （resolved/dismissed）——都直接返回旧条目、不再问一遍（这就是"同版本对不重复问"的落点）。
    要强行重开一件已经表过态的事，显式传 `reuse=False`。
    """
    did = make_id(kind, key)
    data = load(p)
    if reuse:
        old = get(did, p)
        if old is not None:
            return old, False
    item = {
        "id": did, "kind": str(kind), "key": str(key),
        "created_at": _now(), "title": str(title or ""), "reason": str(reason or ""),
        "wechat": str(wechat or ""), "adapter": str(adapter or ""),
        "options": [dict(o) for o in (options if options is not None else [])],
        "status": "open", "choice": "", "resolved_at": "", "note": "",
    }
    if extra:
        item.update(extra)
    items = [it for it in (data.get("items") or []) if str(it.get("id")) != did]
    items.append(item)
    data["items"] = items
    save(data, p)
    return item, True


def resolve(decision_id: str, choice: str, note: str = "", p: str | None = None) -> dict:
    """用户表态。`choice` 必须是该条目选项里的 key；**空串/`none` ＝ ✕ 什么都不做**。

    ✕ 记 `dismissed`（不是删除）：条目仍在台账里、仍算"问过了"，下次不再追问。
    非法 choice 直接抛 `ValueError`（fail-closed：宁可不落定，也不许写进一个读不懂的状态）。
    """
    data = load(p)
    target = None
    for it in data.get("items") or []:
        if str(it.get("id")) == str(decision_id):
            target = it
            break
    if target is None:
        raise ValueError("待决条目不存在：%s" % decision_id)
    ch = str(choice or "").strip()
    if ch in ("", "none", "cancel", "dismiss"):
        ch = ""
    else:
        keys = [str(o.get("key")) for o in (target.get("options") or [])]
        if ch not in keys:
            raise ValueError("选项不在该条目的选项表里：%r（可选 %s）" % (ch, "、".join(keys) or "无"))
    target["choice"] = ch
    target["status"] = "resolved" if ch else "dismissed"
    target["resolved_at"] = _now()
    target["note"] = str(note or "")
    save(data, p)
    return target


def apply_choice(item: dict | None) -> dict:
    """把用户的选择翻成**一个动作描述符**（真正执行在调用方，本模块不装包不改配置）。

    返回值：`{"action": …, "label": …, "cmd": …, "message": …}`
      · `action="allow_session"` → 调用方放行本会话（`version_gate.allow_session()`）
      · `action="upgrade_adapter"` → 调用方跑适配层升级（命令由调用方给，见 cmd）
      · `action="heal_deps"` → 调用方跑依赖自愈
      · `action="guidance"` → **只给指引**：不装、不降级微信
      · `action="none"` → ✕ 什么都不做
    """
    it = item or {}
    ch = str(it.get("choice") or "")
    if not ch:
        return {"action": "none", "label": "", "cmd": "", "message": "按「什么都不做」处理：不发送、不改配置"}
    opt = next((o for o in (it.get("options") or []) if str(o.get("key")) == ch), {})
    act = str(opt.get("action") or "none")
    cmd = {"upgrade_adapter": "检查微信版本.bat --update", "heal_deps": "检查微信版本.bat"}.get(act, "")
    msg = {
        "allow_session": "已放行本次运行：发送会按未验证版本对继续，重启后重新拦",
        "upgrade_adapter": "去升级适配层：跑一次 检查微信版本.bat --update，升完重测能力矩阵",
        "heal_deps": "去更新本体：跑一次 检查微信版本.bat，按体检结论修依赖",
        "guidance": "这一版要在微信那边处理：先看适配层有没有对应版本，我们不装也不降级微信",
        "none": "按「什么都不做」处理：不发送、不改配置",
    }.get(act, "")
    return {"action": act, "label": str(opt.get("label") or ""), "cmd": cmd, "message": msg}


# ── 版本不匹配：开单/落定（供版本门与控制台调用）──────────────────────────
def ensure_version_decision(wechat: str = "", adapter: str = "", reason: str = "",
                            p: str | None = None, reuse: bool = True) -> tuple:
    """版本对没实测过时开一张四选一单（同一对版本只开一次）。返回 `(条目, 是否新建)`。"""
    w, a = str(wechat or "unknown"), str(adapter or "unknown")
    title = "微信 %s × 适配层 %s 没实测过" % (w, a)
    return open_item("version_mismatch", pair_key(w, a), title,
                     reason=reason or "这一对版本没有实测记录：发送这类动窗口/动键盘的能力按未验证处理",
                     options=VERSION_OPTIONS, wechat=w, adapter=a, p=p, reuse=reuse)


def summary(p: str | None = None) -> str:
    """一行摘要（控制台/日志用）。"""
    data = load(p)
    items = data.get("items") or []
    op = [it for it in items if str(it.get("status")) == "open"]
    n_res = len([it for it in items if str(it.get("status")) == "resolved"])
    n_dis = len([it for it in items if str(it.get("status")) == "dismissed"])
    return "待拍板 %d 件 · 已表态 %d（其中 ✕ 什么都不做 %d）" % (len(op), n_res + n_dis, n_dis)
