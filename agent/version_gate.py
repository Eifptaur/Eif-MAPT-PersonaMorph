# -*- coding: utf-8 -*-
"""版本门（W7 余项接线）：把「当前 微信版本 × 适配层版本 没实测过」变成发送前的硬检查。

为什么需要：`version_matrix.gate()` 早就给了「没实测记录」的结论，但**没有任何地方问它** ——
用户升级微信后，发送照旧跑，出问题才发现"这版没验证过"。这里把它接到发送入口上。

三级：
  ok      实测过 → 放行
  warn    没实测 → **默认暂停自动发送**（要用户点头），放行需显式 allow_session
  blocked 该版本对里明确标记了 no 的能力 → 由调用方按能力 id 查 capabilities 决定

「临时放行」只作用于**本次进程**（内存里一个标记），重启后重新拦 —— 免得一次点头变成永久放行。
"""
import threading

_allowed = set()
_lock = threading.Lock()


def allow_session(reason: str = "") -> None:
    """本次进程内放行（重启失效）。reason 只用于日志/控制台展示。"""
    with _lock:
        _allowed.add("current")


def is_allowed() -> bool:
    with _lock:
        return "current" in _allowed


def clear_allow() -> None:
    with _lock:
        _allowed.clear()


def check(capability: str = "send", wechat: str = "", adapter: str = "") -> dict:
    """发送前查一次。返回 {level, allow, reason, wechat, adapter}。"""
    try:
        from . import version_matrix as vm
        data = vm.load()
        w = wechat or "unknown"
        a = adapter or vm.adapter_version()
        g = vm.gate(data, w, a)
        if g.get("measured"):
            return {"level": "ok", "allow": True, "wechat": w, "adapter": a,
                    "reason": "版本对已实测（%s × %s）" % (w, a)}
        if is_allowed():
            return {"level": "warn", "allow": True, "wechat": w, "adapter": a,
                    "reason": "版本对未实测（或读不到微信版本），但已在本次会话中放行"}
        if not w or w == "unknown":
            return {"level": "warn", "allow": False, "wechat": w, "adapter": a,
                    "reason": "读不到微信版本（微信没在跑？）：按未验证处理，已暂停自动发送；登录微信后可点重新检测复检，或在控制台点「本次允许发送」临时放行"}
        return {"level": "warn", "allow": False, "wechat": w, "adapter": a,
                "reason": ("微信 %s × 适配层 %s 没有实测记录：发送这类动窗口/动键盘的能力按未验证处理，"
                           "已暂停自动发送；控制台点「本次允许发送」可临时放行（重启后重新拦）" % (w, a))}
    except Exception as e:
        return {"level": "warn", "allow": False, "wechat": wechat, "adapter": adapter,
                "reason": "版本门检查异常（按未验证处理）：%s" % e}


def status() -> dict:
    st = check()
    st["allowed_session"] = is_allowed()
    return st
