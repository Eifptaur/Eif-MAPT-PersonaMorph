# -*- coding: utf-8 -*-
"""系统提示词编辑（第三方 v0.4 对账清单第 11 条）。

原来的状态：人设可编辑、提示词按模块拼装（`agent/prompt.py`），但**系统提示词本身没开放**。
这一层给三样东西，够用且不危险：

1. **自定义补充**（`system_prompt.custom`）：追加在系统提示词**最末尾**、带显眼标题的自由文本。
   落盘即生效——`build_system_prompt()` 每次都现读配置，改完**下一轮就生效，不用重启**。
2. **三个模块开关**（默认全开）：`scene_rules`（微信场景规则/工具用法）· `memory_rules`（记忆使用规则）·
   `holiday_hint`（「今天是 X 节」那句）。**安全规则与工具协议不给关**——红线，不给用户误伤自己的机会。
3. **预览**：控制台点一下看到"此刻真正送出的系统提示词"（走真 `build_system_prompt()`，不是另写一份），
   以及每个模块的开/关状态。

不做的事（写清楚免得被当成缺口）：不做整段提示词的"全量编辑"——那会让人一句话就把安全段删了，
出问题时无从追责；也不做"提示词市场/模板导入"。要改整段就改 `agent/prompt.py` 里的函数（那是开发行为）。
"""
from __future__ import annotations

import threading

_lock = threading.RLock()

# id, 名称, 一句话说明（控制台直接展示这三列）
MODULES = [
    ("scene_rules", "微信场景规则",
     "回复简短、不用 Markdown、被 @ 才必回、以及各工具的用法提示（看图/语音/联网/定时提醒等）"),
    ("memory_rules", "记忆使用规则", "怎么用「对群友的印象」，以及不要把印象当事实硬背"),
    ("holiday_hint", "节日提示", "「今天是 X 节，若自然可带一句」——不想让它知道就可以关"),
]
# 永远不给关的部分（安全与工具协议）：不是"暂时没做开关"，是红线
ALWAYS_ON = ["security_rules", "tool_protocol"]


def cfg() -> dict:
    try:
        from .config import get_config
        return dict((get_config() or {}).get("system_prompt") or {})
    except Exception:
        return {}


def _truthy(v, default: bool = True) -> bool:
    """配置里的开关可能是真布尔、也可能被前端/手改存成字符串（用户现网就有 "0" 这种）⇒ 统一按语义判。"""
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v).strip().lower()
    if s in ("0", "false", "off", "no", "none", "null", ""):
        return False
    if s in ("1", "true", "on", "yes"):
        return True
    return default


def module_enabled(mid: str) -> bool:
    """模块是否启用（缺省＝启用；写成 "0"/"false"/"off" 也算关）。"""
    c = cfg()
    key = "enable_" + str(mid)
    if key not in c:
        return True
    return _truthy(c.get(key), True)


def custom() -> str:
    return str(cfg().get("custom") or "").strip()


def sections() -> list:
    return [{"id": mid, "name": name, "desc": desc, "enabled": module_enabled(mid)} for mid, name, desc in MODULES]


def holiday_hint() -> str:
    """「今天是 X 节」那一句（关掉就给空串）。"""
    if not module_enabled("holiday_hint"):
        return ""
    try:
        from . import holidays as _hol
        line = _hol.scene_line()
    except Exception:
        line = ""
    if not line:
        return ""
    return "- %s 若对话自然，可以顺口带一句应景的问候；**别硬凑、别无中生有地群发**，也别反复提。" % line


def append_custom(parts: list) -> None:
    """把自定义补充追加到提示词末尾（空则什么都不加）。"""
    text = custom()
    if not text:
        return
    parts.extend(["", "【管理员补充系统提示词（最高优先级，覆盖上面与它冲突的表述）】", text])


def snapshot() -> dict:
    c = cfg()
    return {"custom_chars": len(custom()), "modules": sections(), "always_on": list(ALWAYS_ON),
            "note": "自定义补充追加在系统提示词末尾，保存后下一轮即生效（不需要重启）"}


def preview() -> dict:
    """此刻真正送出的系统提示词（走真 build_system_prompt，不另写一份）。"""
    from .prompt import build_system_prompt
    try:
        text = build_system_prompt()
        err = ""
    except Exception as e:
        text, err = "", "%s: %s" % (type(e).__name__, e)
    return {"system": text, "chars": len(text), "error": err,
            "modules": sections(), "custom_chars": len(custom())}
