# -*- coding: utf-8 -*-
"""只读 UIA 探针：微信 4.x 的控件树到底可不可用 + 按版本的 UI 指纹（W4）。

回答三个问题（全部只读，不点击/不输入/不发消息）：
  1. **UIA 树有没有"物化"** —— 能不能按类名找到 `mmui::*` 控件？找不到 ⇒ L2 这一档不可用，
     必须明确降级（本机 2026-09-13 实测：微信 4.1.15.8 只有 2 个节点、`mmui::*` 一个都没有）。
  2. **UI 指纹** —— 每个微信版本产一份 `wechatauto_logs/ui_probe/<版本>.json`；启动时比对，
     变了就"整轮重连 + 告知用户"，**不原地修补、不静默假定行为一致**（照 MAA 那条教训）。
  3. **控件通讯录** —— `WECHAT_UI_MAP` 是把 wxauto4 攒出来的 `mmui::*` 类名/AutomationId 取成数据，
     将来 UiaBackend 直接用，不必再反查一遍。

设计成纯函数 + 一层薄 I/O：`analyze_nodes()` / `fingerprint()` / `compare_fingerprint()` 都能
拿假控件树单测，不需要微信在跑（判据见 `scripts/uia_probe_selftest.py`）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time

from .config import ROOT, get_config

log = logging.getLogger("persona-morph")

PROBE_DIR = os.path.join(ROOT, "wechatauto_logs", "ui_probe")

# ── 微信 4.x 控件通讯录（来源：wxauto4 / 张苹果工具的换皮分支，逐层扒出来后取成数据）──
# 说明：UIA 的 ClassName 是应用自己的控件类名；AutomationId 是控件内部 id（可含点分路径）。
WECHAT_UI_MAP = {
    "main_window": {"uia_class": "mmui::MainWindow", "win32_class": "Qt51514QWindowIcon"},
    "sub_window": {"uia_class": "mmui::FramelessMainWindow", "win32_class": "Qt51514QWindowIcon",
                   "note": "独立聊天窗口；wxauto4 原话：通过子窗口发送不会切换主窗口"},
    "main_tabbar": {"uia_class": "mmui::MainTabBar", "automation_id": "main_tabbar"},
    "chat_master": {"uia_class": "mmui::ChatMasterView"},
    "chat_page": {"uia_class": "mmui::ChatMessagePage"},
    "splitter": {"uia_class": "mmui::XSplitterView"},
    "session_list": {"uia_class": "mmui::ChatSessionList"},
    "session_table": {"uia_class": "mmui::XTableView"},
    "search_box": {"uia_class": "mmui::XSearchField"},
    "search_popover": {"uia_class": "mmui::SearchContentPopover"},
    "message_list": {"uia_class": "mmui::MessageView"},
    "input_box": {"uia_class": "mmui::ChatInputField", "note": "输入框（wxauto4 用 EditControl 定位，读值走 ValuePattern）"},
    "send_button": {"name": "发送(S)", "note": "wxauto4 用 ButtonControl(Name='发送(S)') 点它发送"},
    "chat_info": {"uia_class": "mmui::ChatInfoView"},
    "bubble_item": {"uia_class": "mmui::ChatBubbleItemView"},
    "bubble_text": {"uia_class": "mmui::ChatTextItemView"},
    "bubble_voice": {"uia_class": "mmui::ChatVoiceItemView"},
    "bubble_card": {"uia_class": "mmui::ChatPersonalCardItemView"},
    "bubble_refer": {"uia_class": "mmui::ChatBubbleReferItemView"},
    "chat_info_path": {
        "automation_id": ("top_content_h_view.top_spacing_v_view.top_left_info_v_view."
                          "big_title_line_h_view.current_chat_name_label"),
        "note": "群名/会话名；同级还有 current_chat_count_label（群人数）、current_chat_openim_name"},
}
# 判定"树物化了"时要找的关键类名（有几个命中就算物化）
KEY_CLASSES = ("mmui::ChatInputField", "mmui::MessageView", "mmui::ChatMessagePage",
               "mmui::ChatSessionList", "mmui::MainWindow", "mmui::FramelessMainWindow")


# ── 纯函数（可单测）────────────────────────────────────────────────────────
def analyze_nodes(nodes, key_classes=KEY_CLASSES) -> dict:
    """给一串控件描述（dict: type/class/name/aid），算出"树有没有物化"。"""
    nodes = list(nodes or [])
    classes = {}
    for n in nodes:
        c = str((n or {}).get("class") or "")
        if c:
            classes[c] = classes.get(c, 0) + 1
    hits = sorted([c for c in classes if c.startswith("mmui::")])
    key_hits = sorted([c for c in hits if c in key_classes])
    return {
        "nodes": len(nodes),
        "classes": classes,
        "mmui_hits": hits,
        "key_hits": key_hits,
        "materialized": bool(key_hits),
    }


def fingerprint(nodes, window_info=None) -> str:
    """按"关键类名集合 + 控件类型分布 + 窗口类名"做指纹。

    刻意排除**易变字段**：窗口句柄（每次重启都变）、窗口标题（跟着当前会话变）、
    节点总数与运行时 id —— 这些每次都不同，放进去会把指纹抖成"每次都说 UI 变了"
    （假阳性来源，和风险闸门那条教训同源）。
    """
    info = analyze_nodes(nodes)
    volatile = {"hwnd", "name", "title", "pid"}
    win = {k: str(v) for k, v in sorted((window_info or {}).items()) if k not in volatile}
    shape = {
        "classes": sorted(info["mmui_hits"]),
        "types": sorted((info["classes"] or {}).keys()),
        "win": win,
    }
    raw = json.dumps(shape, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def compare_fingerprint(old: dict, new: dict) -> dict:
    """比对指纹 ⇒ ok / changed / new（没有历史就是 new；都缺失就是 unknown）。"""
    if not new or not new.get("fingerprint"):
        return {"status": "unknown", "changed": [], "note": "这次没拿到指纹（树不可用/微信没开）"}
    old_fp = str((old or {}).get("fingerprint") or "")
    if not old_fp:
        return {"status": "new", "changed": [], "note": "该微信版本第一次记录，无可比对历史"}
    if old_fp == str(new.get("fingerprint")):
        return {"status": "ok", "changed": [], "note": "指纹一致"}
    old_cls = set((old or {}).get("mmui_hits") or [])
    new_cls = set(new.get("mmui_hits") or [])
    return {"status": "changed",
            "changed": sorted(old_cls ^ new_cls),
            "note": "UI 指纹变了：不原地修补，按整轮重连处理并告知用户（可能需重新校准）"}


# ── 薄 I/O（没有微信/没有 uiautomation 时返回 ok=False，绝不抛）────────────────
def _collect_nodes(max_nodes=800, max_depth=8) -> dict:
    """遍历微信主窗的控件树，返回 {ok, nodes, window, error}。"""
    out = {"ok": False, "nodes": [], "window": {}, "error": ""}
    try:
        import uiautomation as auto
    except Exception as e:
        out["error"] = "uiautomation 不可用：%s" % e
        return out
    hwnd = None
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        for cls in (WECHAT_UI_MAP["main_window"]["win32_class"],):
            hwnd = int(u32.FindWindowW(cls, None) or 0)
            if hwnd:
                break
    except Exception:
        hwnd = None
    if not hwnd:
        out["error"] = "没找到微信主窗（win32 类名 %s）" % WECHAT_UI_MAP["main_window"]["win32_class"]
        return out
    try:
        root = auto.ControlFromHandle(int(hwnd))
        out["window"] = {"hwnd": hwnd, "name": str(getattr(root, "Name", "") or ""),
                         "class": str(getattr(root, "ClassName", "") or "")}
        nodes = []
        stack = [(root, 0)]
        while stack and len(nodes) < max_nodes:
            ctrl, depth = stack.pop()
            if depth >= max_depth:
                continue
            try:
                children = ctrl.GetChildren()
            except Exception:
                continue
            for c in children or []:
                try:
                    nodes.append({"type": str(getattr(c, "ControlTypeName", "") or ""),
                                  "class": str(getattr(c, "ClassName", "") or ""),
                                  "name": str(getattr(c, "Name", "") or "")[:40],
                                  "aid": str(getattr(c, "AutomationId", "") or "")})
                except Exception:
                    continue
                stack.append((c, depth + 1))
        out["nodes"] = nodes
        out["ok"] = True
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
    return out


def wechat_version() -> str:
    try:
        from .wechat import wechat_version_info
        return str((wechat_version_info() or {}).get("version") or "unknown")
    except Exception:
        return "unknown"


def probe(save: bool = True) -> dict:
    """跑一次探针：结果 + 指纹；save=True 时落盘到 ui_probe/<版本>.json。"""
    col = _collect_nodes()
    info = analyze_nodes(col["nodes"])
    fp = fingerprint(col["nodes"], col.get("window"))
    ver = wechat_version()
    res = {
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "wechat_version": ver,
        "ok": bool(col["ok"]),
        "error": col.get("error") or "",
        "nodes": info["nodes"],
        "mmui_hits": info["mmui_hits"],
        "materialized": info["materialized"],
        "class_count": len(info["classes"]),
        "window": col.get("window") or {},
        "fingerprint": fp,
        "key_classes": list(KEY_CLASSES),
        "map_version": "wxauto4-2026-09-13",
    }
    path = os.path.join(PROBE_DIR, "%s.json" % str(ver).replace("/", "_"))
    res["path"] = path
    if save:
        try:
            os.makedirs(PROBE_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=1)
        except Exception as e:
            log.warning("UI 探针落盘失败：%s", e)
    return res


def version_gate(save: bool = True, cur: dict = None) -> dict:
    """启动时的版本门：拿当前指纹与该版本**上次记录**比对 ⇒ ok/new/changed/unknown + 建议动作。

    注意顺序：**先读旧记录、再跑探针**（反过来的话 compare 永远等于"自己跟自己比"，恒为 ok
    —— 这个 bug 是自测抓出来的）。`cur` 仅供单测注入。
    """
    ver = (cur or {}).get("wechat_version") or wechat_version()
    path = os.path.join(PROBE_DIR, "%s.json" % str(ver).replace("/", "_"))
    old = {}
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                old = json.load(f) or {}
    except Exception:
        old = {}
    new = cur if cur else probe(save=save)
    cmp_ = compare_fingerprint(old, new)
    advice = {
        "ok": "",
        "new": "该微信版本第一次记录指纹；若发送/读取出现异常，先怀疑版本差异",
        "changed": "UI 指纹变了：按整轮重连处理（不原地修补），并提示用户可能在 UI 校准失效",
        "unknown": "这次没拿到 UI 指纹（UIA 树不可用）：L2 不可用，按 L5/L0 降级",
    }.get(cmp_["status"], "")
    return {"status": cmp_["status"], "advice": advice, "compare": cmp_, "probe": new,
            "old_fingerprint": str(old.get("fingerprint") or ""),
            "uia_usable": bool(new.get("materialized"))}


def brief() -> str:
    """给控制台/日志的一行摘要。"""
    g = version_gate(save=False)
    p = g["probe"]
    return "微信 %s · UIA %s（节点 %d，mmui 命中 %d）· 指纹 %s · 版本门 %s" % (
        p["wechat_version"], "可用" if g["uia_usable"] else "不可用",
        p["nodes"], len(p["mmui_hits"]), p["fingerprint"], g["status"])
