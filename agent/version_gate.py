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
import logging
import os
import threading

log = logging.getLogger("persona-morph")

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


# ── 四选一里"真能一键做"的两个动作（⑦ 的"接进依赖自愈 / 更新链"）────────────────
# 口径：只有这两条会**真动手**，而且是后台作业（`agent/jobs.py`，分钟级、不阻塞控制台）：
#   · upgrade_adapter → `scripts/wechat_check.py --update`（实测非交互：直接 pip install -U 适配层与关键依赖）
#   · update_host     → `dep_heal.plan()` 给的第一条安装命令（离线优先 --no-index，其次镜像）
# 「微信本身要处理」永远只给指引；「仅本次允许」只动本进程内存。**一律不装/不降级微信本体。**
ACTION_JOBS = {"upgrade_adapter": "upgrade_adapter", "update_host": "dep_heal"}


def action_cmd(choice: str) -> str:
    """这条选择要跑什么命令（空串＝不用跑）。给控制台/日志/判据共用，避免各处各写一份。"""
    ch = str(choice or "")
    if ch == "upgrade_adapter":
        try:
            from . import dep_heal as _dh
            py = _dh.runtime_python()
        except Exception:
            import sys
            py = sys.executable
        return '"%s" "%s" --update' % (py, os.path.join(ROOT_DIR(), "scripts", "wechat_check.py"))
    if ch == "update_host":
        try:
            from . import dep_heal as _dh
            p = _dh.plan()
            # ⚠️ 只有在**真缺依赖**时才拿安装命令：`dep_heal.plan()` 无论缺不缺都会在 steps 末尾
            #   塞一条"装完复查"（`scripts/selftest.py`）⇒ 直接取 steps[0] 会在依赖齐全的机器上
            #   把整套自检当"自愈"跑起来（重、还碰微信）。所以先看 diagnose 有没有非 ok 的。
            if not [d for d in (p.get("diagnose") or []) if str(d.get("status")) != "ok"]:
                return ""
            steps = p.get("steps") or []
            return str(steps[0]["cmd"]) if steps else ""
        except Exception as e:
            log.warning("依赖自愈计划失败：%s", e)
            return ""
    return ""


def ROOT_DIR() -> str:
    try:
        from .config import ROOT
        return ROOT
    except Exception:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_action(choice: str, decision_id: str = "") -> dict:
    """把一条选择**执行**掉（不重复落台账）：返回 `{ok, ran, job, message}`。

    · `allow_once` → 本进程放行（内存标记）
    · `upgrade_adapter` / `update_host` → 起后台作业（同名只允许一个在跑）
    · `wechat_side` / 空（✕）→ 什么都不做，如实回报
    """
    ch = str(choice or "")
    if ch in ("", "none"):
        return {"ok": True, "ran": False, "message": "按「什么都不做」处理：没有执行任何动作"}
    if ch == "allow_once":
        allow_session("console 四选一")
        return {"ok": True, "ran": True, "message": "已放行本次运行（只对本进程有效，重启重新拦）"}
    if ch == "wechat_side":
        return {"ok": True, "ran": False,
                "message": "这条要在微信那边处理：先确认适配层有没有对应版本，我们不装也不降级微信"}
    if ch in ACTION_JOBS:
        cmd = action_cmd(ch)
        if not cmd:
            # 依赖齐全时 update_host 没有可跑的命令 —— 如实说"不用动"，**不许**拿"复查自检"顶替
            return {"ok": True, "ran": False, "cmd": "",
                    "message": ("依赖都满足，不用动" if ch == "update_host" else "拿不到要跑的命令")}
        from . import jobs as _jobs
        r = _jobs.start(ACTION_JOBS[ch], cmd)
        return {"ok": bool(r.get("ok")), "ran": bool(r.get("ok")), "job": r.get("job"),
                "cmd": cmd, "message": ("已开始：%s" % ch) if r.get("ok") else str(r.get("why") or "起不来")}
    return {"ok": False, "ran": False, "message": "不认识的选项：%s" % ch}


# ── 版本不匹配：待决单（四选一）─────────────────────────────────────────────
# 用户口径（2026-09-14）：「弹窗按你推荐的做」+「把弹窗切出来的那一秒，就应该立刻让它到后台」。
# 落点：门判「未实测」时**开一张待决单**（`agent/pending_decisions.py`，落 data/pending_decisions.json）；
#   控制台据此弹四选一模态（一键升级适配层/更新本体/仅本次允许/微信本身要处理，✕＝什么都不做），
#   用户表态后由 `decide()` 落台账 + 写回能力矩阵 + 执行本进程内的副作用。
# **同一对版本只问一次**：开单是幂等的（已开过/已表过态的版本对直接复用旧条目）。
def _pop_ui(item: dict) -> dict:
    """新开一张单子时，**Persona Morph 自己把弹窗切出来**（⑦ 用户口径）。

    三条纪律：①只在"新开单"时弹（同一对版本只问一次，所以不会反复弹）；
    ②**不抢前台**（`notify_ui` 抬起后立刻把前台还给原窗口）；
    ③**绝不能挡住调用方** —— 这个函数会开子进程/等窗口，最坏几秒，所以在后台线程里做，
    失败只写日志（弹不出来不影响开单、更不影响发送闸门）。
    """
    def _run():
        try:
            from . import notify_ui as _nu
            rep = _nu.pop_decision_ui()
            log.info("待决单已弹窗：%s", _nu.brief(rep))
        except Exception as e:                                     # noqa: BLE001
            log.warning("待决单弹窗失败（不影响开单）：%s", e)

    # ⛔ 判据/无人值守必须能关掉弹窗（2026-09-14 教训）：我第一次跑 ⑦c 自检时，`pending()` 走了
    #   created=True 分支，**真的把控制台往屏幕上弹了一次**（开子进程）。判据不许动用户的屏幕
    #   ⇒ 环境变量一关，`pop` 只回报"被关掉"，其它行为不变。
    if os.environ.get("WX_NO_UI_POP") == "1":
        return {"ok": True, "skipped": "已按 WX_NO_UI_POP=1 关掉弹窗（判据/无人值守模式）"}
    try:
        t = threading.Thread(target=_run, daemon=True, name="decide-pop")
        t.start()
        return {"ok": True, "async": True}
    except Exception as e:                                         # noqa: BLE001
        log.warning("待决单弹窗线程起不来：%s", e)
        return {"ok": False, "why": str(e)}


def pending(capability: str = "send", wechat: str = "", adapter: str = "") -> dict:
    """返回 `{needed, item, created, status}`：门没过就保证有一张待决单（幂等）。

    **新开单**时顺手让 Persona Morph 自己把弹窗切出来（不抢前台，见 `_pop_ui`）。
    """
    st = check(capability, wechat=wechat, adapter=adapter)
    if st.get("level") == "ok" and st.get("allow"):
        return {"needed": False, "item": None, "created": False, "status": st}
    try:
        from . import pending_decisions as pd
        item, created = pd.ensure_version_decision(st.get("wechat"), st.get("adapter"),
                                                   reason=str(st.get("reason") or ""))
        pop = _pop_ui(item) if created else {"ok": True, "skipped": "已经问过这一对版本，不再弹"}
        return {"needed": True, "item": item, "created": created, "status": st, "pop": pop}
    except Exception as e:
        return {"needed": True, "item": None, "created": False, "status": st, "error": str(e)}


def decide(decision_id: str, choice: str, note: str = "",
           wechat: str = "", adapter: str = "",
           decisions_path: str = None, matrix_path: str = None) -> dict:
    """用户对一张待决单表态：落台账 → 写回能力矩阵 → 执行本进程内允许的副作用。

    返回 `{item, action, wechat, adapter}`；`action` 里带给人看的一句话（控制台拿去 toast）。
    ⚠️ 只有「仅本次允许」是**真的立刻生效**的动作；「升级适配层 / 更新本体」只给命令与说明，
    由控制台/脚本去跑；「微信本身要处理」**只给指引，不装也不降级微信**。
    `decisions_path`/`matrix_path` 只给判据注入临时文件用（生产留空＝用默认位置）。
    """
    from . import pending_decisions as pd
    from . import version_matrix as vm
    item = pd.resolve(decision_id, choice, note=note, p=decisions_path)
    act = pd.apply_choice(item)
    w = str(item.get("wechat") or wechat or "unknown")
    a = str(item.get("adapter") or adapter or vm.adapter_version())
    try:
        vm.note_decision(w, a, str(item.get("choice") or "none"),
                         note=str(act.get("message") or note), path=matrix_path)
    except Exception as e:                                     # noqa: BLE001
        act["writeback_error"] = str(e)
    if act.get("action") == "allow_session":
        allow_session(str(act.get("message") or ""))
    return {"item": item, "action": act, "wechat": w, "adapter": a}
