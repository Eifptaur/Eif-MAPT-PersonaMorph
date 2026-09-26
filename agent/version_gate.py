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
import time

log = logging.getLogger("persona-morph")

_allowed = set()
_lock = threading.Lock()
_ver_cache = {"at": 0.0, "wechat": ""} # current_wechat_version() 的小缓存（见它的注释）


def current_wechat_version(ttl: float = 60.0) -> str:
    """当前微信版本号（读不到返回空串）。**给所有调用方兜底用**。

    为什么要在这里自己查。
    三处**发送**入口都老老实实传了 `wechat=wx_version_for_gate()`，但**展示侧**——控制台
    `status()`、随包 `collect_report.py`——调的是 `check()` **不带参数**，而 `check()` 内部原来
    是 `w = wechat or "unknown"` ⇒ 面板/报告永远显示「微信 unknown × 适配层 x」并说
    「读不到微信版本（微信没在跑？）」——微信明明在跑、版本对也明明是实测过的。
    ⇒ 现在 `check()` 自己兜底查一次；带 60 秒缓存，免得每次轮询都去枚举进程。
    """
    now = time.time()
    if _ver_cache["wechat"] and (now - float(_ver_cache["at"] or 0)) < ttl:
        return str(_ver_cache["wechat"])
    v = ""
    try:
        from .wechat import wx_version_for_gate
        v = str(wx_version_for_gate() or "")
    except Exception:
        v = ""
    _ver_cache.update({"at": now, "wechat": v})
    return v


def wechat_running() -> bool:
    """微信主窗在不在——用来把「微信没在跑」和「跑着但读不到版本号」分开说。"""
    try:
        from .input_backend import find_main_window
        return bool(find_main_window())
    except Exception:
        return False


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


# ── 「发送被门拦下」的记账───────────────────────────────────────
# 起因（报「能识别群，但发不了消息；概览多了 3 条累计会话，两个群都没收到」）：
#   他的微信是 **4.1.15.9**（我们只实测到 4.1.15.8）⇒ 版本门按"未实测"**暂停了每一次自动发送**。
#   门的行为本身是对的（fail-closed 是红线），错在**用户看不见**：他只看到"机器人不回话"。
#   ⇒ 每一次被拦都要记账 + 给一句能照做的话，并让控制台能一眼看到（横幅 + 一键放行）。
_blocked = {"count": 0, "last_reason": "", "last_at": 0.0, "capability": ""}
# 「实测发不出去」的记账（与"被拦下"分开记：这一档**并不拦**，只是要让用户看得见）
_no_measured = {"count": 0, "last_reason": "", "last_at": 0.0, "caps": []}
# 本机自证：连续失败到阈值 ⇒ 把"本机实测发不出去"落盘（换机器/换版本仍在，直到有一次成功）
_LOCAL_FAIL_NEED = 3
_local_fail = {"key": "", "n": 0}


def note_no_measured(caps, reason: str = "") -> None:  # noqa: ANN001
    """记一笔"这一版对面实测发不出去，但我们**照发**了"。

    为什么要单独记：这一档的口径是「能发就发」（不拦），但**不能当没这回事** ——
    控制台横幅与报告要读得出"你现在跑的是一个已知发不出去的组合"。
    """
    try:
        with _lock:
            _no_measured["count"] = int(_no_measured.get("count") or 0) + 1
            _no_measured["caps"] = [str(c) for c in (caps or [])][:6]
            _no_measured["last_reason"] = str(reason or "")[:200]
            _no_measured["last_at"] = time.time()
    except Exception:
        pass


def no_measured_stat() -> dict:
    with _lock:
        return dict(_no_measured)


def _local_path() -> str:
    from .store import DATA_DIR
    import os as _os

    return _os.path.join(DATA_DIR, "version_local.json")


def _local_all() -> dict:
    try:
        from .persist import load_checked

        # ⛔ `load_checked` 返回的是 **(值, 可覆盖)** 两元组（不是三元组）—— 解包错就被下面的
        #    except 吞掉 ⇒ 自证结论**读不出来**（落盘了也当没有）。形状以 persist 的实现为准。
        data, _writable = load_checked(_local_path(), {})
        return dict(data or {})
    except Exception:
        return {}


def local_state(wechat: str = "", adapter: str = "") -> dict:
    """本机对"当前这对版本"的自证结论：`{}` = 没结论；否则 `{status:'no', at, why, fails}`。"""
    w = wechat or current_wechat_version() or "unknown"
    try:
        from . import version_matrix as vm

        a = adapter or vm.adapter_version()
    except Exception:
        a = str(adapter or "")
    return dict(_local_all().get("%s|%s" % (w, a)) or {})


def _local_save(key: str, rec: dict) -> None:
    try:
        import os as _os

        from .persist import atomic_write_json

        data = _local_all()
        data[key] = rec
        path = _local_path()
        # ⛔ 目录不存在时必须先建：`data/` 还没被创建过（全新安装/自检临时目录）时，
        #    写盘会抛 ⇒ 被本函数的 except 吞掉 ⇒ **自证结论永远落不了盘**（静默失效）。
        try:
            _os.makedirs(_os.path.dirname(path), exist_ok=True)
        except Exception:
            pass
        atomic_write_json(path, data, indent=1)
    except Exception:
        pass


#: 这些原因**不算"版本对发不出去"**（是身份/环境/用户态的问题，换版本也没用）
_NOT_CAPABILITY = ("identity_unconfirmed", "halted", "busy", "filtered", "no_interactive_desktop",
                   "no_capture", "db_unreadable", "access_denied", "auth_rejected", "key_missing")


def note_send_result(ok: bool, why: str = "", wechat: str = "", adapter: str = "") -> None:
    """把一次发送的真实结果喂回来 —— 这是"不能完全不管"的那一半。

    连续 `_LOCAL_FAIL_NEED` 次**能力类**失败 ⇒ 把"本机实测发不出去"落盘；之后门会以
    `no_measured_local` 档提示（**仍然照发**，但会把可照做的动作写在原因里）。
    任何一次成功都清零 —— 结论跟着现场走，不靠一次观测定终身。
    """
    try:
        w = wechat or current_wechat_version() or "unknown"
        from . import version_matrix as vm

        a = adapter or vm.adapter_version()
        key = "%s|%s" % (w, a)
        if ok:
            with _lock:
                _local_fail["key"], _local_fail["n"] = key, 0
            if _local_all().get(key):
                data = _local_all()
                data.pop(key, None)
                try:
                    from .persist import atomic_write_json

                    atomic_write_json(_local_path(), data, indent=1)
                except Exception:
                    pass
            return
        code = ""
        try:
            from . import reason_codes as _rc

            code = str(_rc.classify(why) or "")
        except Exception:
            code = ""
        if code in _NOT_CAPABILITY or not str(why or "").strip():
            return # 不是"版本对发不出去"那类 ⇒ 不喂给这条自证（别把身份问题记成版本问题）
        with _lock:
            if _local_fail.get("key") != key:
                _local_fail["key"], _local_fail["n"] = key, 0
            _local_fail["n"] = int(_local_fail.get("n") or 0) + 1
            n = _local_fail["n"]
        if n >= _LOCAL_FAIL_NEED:
            _local_save(key, {"status": "no", "at": time.time(), "fails": n,
                              "why": str(why or "")[:200], "code": code})
            try:
                from .util import get_logger
                get_logger().warning(
                    "本机连续 %d 次发不出去（%s × %s）⇒ 记为本机实测发不出去：%s",
                    n, w, a, str(why or "")[:160])
            except Exception:
                pass
    except Exception:
        pass


def note_blocked(capability: str, reason: str) -> None:
    """记一笔"因为版本门没发出去"（控制台横幅与报告都读它）。"""
    try:
        with _lock:
            _blocked["count"] = int(_blocked.get("count") or 0) + 1
            _blocked["last_reason"] = str(reason or "")[:200]
            _blocked["last_at"] = time.time()
            _blocked["capability"] = str(capability or "")
    except Exception:
        pass
    try:
        from .util import get_logger
        get_logger().warning("版本门拦下一次「%s」：%s", capability, reason)
    except Exception:
        pass


def blocked_stat() -> dict:
    with _lock:
        return dict(_blocked)


def blocked_reset() -> None:
    with _lock:
        _blocked.update({"count": 0, "last_reason": "", "last_at": 0.0, "capability": ""})


def _strict() -> bool:
    """是否要"未实测版本就暂停发送"。**默认 False**。

    🔴 
      现场①「一直卡在【未通过 会话投递失败】，聊天记录生成了就是发不出去」（截图里那行就是版本门：
      `微信版本读不到（微信没在跑）⇒ 按未验证处理，已暂停自动发送`）；现场②「昨天把微信删了重下，
      它找到消息库了，但是一直不回复」——**重装后版本号/矩阵对不上 ⇒ 版本门把每一次发送都拦掉**。
      版本门的本意是"版本变了要出横幅、别静默降级"，但它把**产品的核心功能（能发出去）**给掐死了，
      代价明显大于收益。⇒ 新口径：**读不到版本＝环境态，不拦**；**未实测版本＝告警但照发**
      （控制台出横幅 + 记台账），要严格拦的用户自己开 `config.version_gate.strict=true`。
    """
    try:
        from .config import get_config
        return bool(((get_config() or {}).get("version_gate") or {}).get("strict"))
    except Exception:
        return False


def _gate_facts(vm, adapter: str) -> str:
    """给版本门算**事实键**（主窗类名 / DPI 感知 / 适配层）；算不出来就返回空串 ⇒ 退回版本键。

    ⛔ （兼容性落地第 ⑦ 项）：判定依据从"按版本号查表"改成**优先按这台机器的事实查**
    （同一个版本号的 UI 可能已经变了；而版本号本身也会撒谎）。只读探测、绝不抛。
    """
    try:
        return str(vm.fact_key(adapter) or "")
    except Exception:
        return ""


def check(capability: str = "send", wechat: str = "", adapter: str = "") -> dict:
    """发送前查一次。返回 {level, allow, reason, wechat, adapter}。

    ⚠️ 起**默认不拦发送**（见 `_strict()` 的说明）：只有用户显式开了 `version_gate.strict`
    才会因为"未实测版本/读不到版本"暂停自动发送。
    """
    try:
        from . import version_matrix as vm
        data = vm.load()
        w = wechat or current_wechat_version() or "unknown"
        a = adapter or vm.adapter_version()
        g = vm.gate(data, w, a, facts=_gate_facts(vm, a))
        _basis = str(g.get("basis") or "")
        _bnote = str(g.get("basis_note") or "")
        if g.get("measured"):
            _no = [str(c) for c in (g.get("no_caps") or [])]
            _loc = local_state(w, a)
            if str(_loc.get("status") or "") == "no":
                # 本机自证档：**仍然照发**（能发就发），但把"已知发不出去"与可照做的动作说清楚
                return {"level": "no_measured_local", "allow": True, "wechat": w, "adapter": a,
                        "basis": _basis, "no_caps": _no,
                        "reason": ("**本机实测这对版本发不出去**（%s × %s，连续 %s 次失败：%s）⇒ "
                                   "仍照发；建议控制台「版本」面板点「升级适配层」，"
                                   "或改用真鼠标档再试（输入档可切换）。"
                                   % (w, a, _loc.get("fails") or "?", str(_loc.get("why") or "")[:80]))}
            if _no:
                # 对面实测 `no`（以前这一档被判成绿灯、什么都不说）⇒ 现在**照发但记账 + 说清楚**
                note_no_measured(_no, "实测发不出去：%s" % "、".join(_no))
                return {"level": "no_measured", "allow": True, "wechat": w, "adapter": a,
                        "basis": _basis, "no_caps": _no,
                        "reason": ("微信 %s × 适配层 %s 的实测结论里「%s」**这一版发不出去** ⇒ "
                                   "按「能发就发」照发（已记账）；本机连发 3 次不出去会自动改判为"
                                   "「本机实测」并给处理动作%s"
                                   % (w, a, "、".join(_no), ("（%s）" % _bnote) if _bnote else ""))}
            return {"level": "ok", "allow": True, "wechat": w, "adapter": a, "basis": _basis,
                    "reason": "版本对已实测（%s × %s）%s" % (w, a, ("；" + _bnote) if _bnote else "")}
        if not _strict():
            if not w or w == "unknown":
                return {"level": "warn", "allow": True, "wechat": "unknown", "adapter": a,
                        "reason": "读不到微信版本（%s）⇒ **不拦发送**，照常发（读不到版本是环境态，"
                                  "不等于版本不兼容）；发不出去请把日志尾部反馈给我们"
                                  % ("微信没在跑" if not wechat_running() else "微信在跑但没读到版本号")}
            return {"level": "warn", "allow": True, "wechat": w, "adapter": a, "basis": _basis,
                    "reason": "微信 %s × 适配层 %s 没有实测记录 ⇒ **照常发送**（按未知版本处理），"
                              "若某条能力不好用请反馈；想改成「没实测就停手」可在配置里开 version_gate.strict" % (w, a)}
        if is_allowed():
            return {"level": "warn", "allow": True, "wechat": w, "adapter": a,
                    "reason": "版本对未实测（或读不到微信版本），但已在本次会话中放行"}
        if not w or w == "unknown":
            # ⚠️ 别再把两种原因混成一句「微信没在跑？」：微信跑着但读不到版本号，
            #    和微信根本没开，是完全不同的处置（前者点「重新检测」、后者去登录微信）。
            return {"level": "warn", "allow": False, "wechat": "unknown", "adapter": a,
                    "reason": "版本门拦下了（version_gate.strict 开着）：微信版本读不到（%s）⇒ 已暂停自动发送；"
                              "可在控制台点「本次允许发送」临时放行，或点「重新检测」再试"
                              % ("微信没在跑" if not wechat_running() else "微信在跑但没读到版本号")}
        return {"level": "warn", "allow": False, "wechat": w, "adapter": a,
                "reason": ("微信 %s × 适配层 %s 没有实测记录（version_gate.strict 开着）：已暂停自动发送；"
                           "控制台点「本次允许发送」可临时放行（重启后重新拦）" % (w, a))}
    except Exception as e:
        # 版本门自己出错时**不许拦发送**（fail-open）：它是提示性的，不该成为新的故障点
        return {"level": "warn", "allow": True, "wechat": wechat, "adapter": adapter,
                "reason": "版本门检查异常 ⇒ 不拦发送（版本门只是提示）：%s" % e}


def status() -> dict:
    st = check()
    st["allowed_session"] = is_allowed()
    st["blocked"] = blocked_stat() # 被拦了几次 + 最后一次为什么（控制台横幅读它）
    st["no_measured"] = no_measured_stat() # "实测发不出去但我们照发了"几次（横幅也读它）
    st["local"] = local_state() # 本机自证结论（空 = 没结论）
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
# 既有口径：：「弹窗按你推荐的做」+「把弹窗切出来的那一秒，就应该立刻让它到后台」。
# 落点：门判「未实测」时**开一张待决单**（`agent/pending_decisions.py`，落 data/pending_decisions.json）；
#   控制台据此弹四选一模态（一键升级适配层/更新本体/仅本次允许/微信本身要处理，✕＝什么都不做），
#   用户表态后由 `decide()` 落台账 + 写回能力矩阵 + 执行本进程内的副作用。
# **同一对版本只问一次**：开单是幂等的（已开过/已表过态的版本对直接复用旧条目）。
def _pop_ui(item: dict) -> dict:
    """新开一张单子时，**Persona Morph 自己把弹窗切出来**。

    三条纪律：①只在"新开单"时弹（同一对版本只问一次，所以不会反复弹）；
    ②**不打扰你（可能短暂置前约 1~3 秒后自动还回）**（`notify_ui` 抬起后立刻把前台还给原窗口）；
    ③**绝不能挡住调用方** —— 这个函数会开子进程/等窗口，最坏几秒，所以在后台线程里做，
    失败只写日志（弹不出来不影响开单、更不影响发送闸门）。
    """
    def _run():
        try:
            from . import notify_ui as _nu
            rep = _nu.pop_decision_ui()
            # 兜底（⑦d）：控制台**开不出来**时（WebView2 起不来、没桌面会话、被策略挡住），
            # 至少让任务栏气泡说一句 —— 不打扰你（可能短暂置前约 1~3 秒后自动还回）、不弹窗，点气泡才去开控制台。
            if not rep.get("ok"):
                try:
                    from . import tray as _tray
                    rep["tray"] = _tray.notify(str(item.get("title") or "有件事要你拍板"),
                                               str(item.get("reason") or "")[:180])
                except Exception as _te: # noqa: BLE001
                    rep["tray"] = {"ok": False, "why": str(_te)}
            log.info("待决单已弹窗：%s", _nu.brief(rep))
        except Exception as e: # noqa: BLE001
            log.warning("待决单弹窗失败（不影响开单）：%s", e)

    # ⛔ 自检/无人值守必须能关掉弹窗：我第一次跑 ⑦c 自检时，`pending()` 走了
    #   created=True 分支，**真的把控制台往屏幕上弹了一次**（开子进程）。自检不许动用户的屏幕
    #   ⇒ 环境变量一关，`pop` 只回报"被关掉"，其它行为不变。
    if os.environ.get("WX_NO_UI_POP") == "1":
        return {"ok": True, "skipped": "已按 WX_NO_UI_POP=1 关掉弹窗（判据/无人值守模式）"}
    try:
        t = threading.Thread(target=_run, daemon=True, name="decide-pop")
        t.start()
        return {"ok": True, "async": True}
    except Exception as e: # noqa: BLE001
        log.warning("待决单弹窗线程起不来：%s", e)
        return {"ok": False, "why": str(e)}


def pending(capability: str = "send", wechat: str = "", adapter: str = "") -> dict:
    """返回 `{needed, item, created, status}`：门没过就保证有一张待决单（幂等）。

    **新开单**时顺手让 Persona Morph 自己把弹窗切出来（不打扰你（可能短暂置前约 1~3 秒后自动还回），见 `_pop_ui`）。
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
    # ⛔ `cmd` **只由这里（`action_cmd` 唯一实现）填** —— 原来 `pending_decisions`
    #   自己硬写了一句「跑根目录那个版本检查 bat」，而那个文件包里不存在（死指引）。
    #   `pending_decisions` 那边已改成留空，命令一律在这里按真实路径生成。
    try:
        act["cmd"] = action_cmd(str(item.get("choice") or "")) or act.get("cmd") or ""
    except Exception: # noqa: BLE001
        pass
    w = str(item.get("wechat") or wechat or "unknown")
    a = str(item.get("adapter") or adapter or vm.adapter_version())
    try:
        vm.note_decision(w, a, str(item.get("choice") or "none"),
                         note=str(act.get("message") or note), path=matrix_path)
    except Exception as e: # noqa: BLE001
        act["writeback_error"] = str(e)
    if act.get("action") == "allow_session":
        allow_session(str(act.get("message") or ""))
    return {"item": item, "action": act, "wechat": w, "adapter": a}
