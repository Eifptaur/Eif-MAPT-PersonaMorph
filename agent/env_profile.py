# -*- coding: utf-8 -*-
"""环境自适应：**探测 → 策略 → 逐机记忆**（探测只读；**改行为默认关闭**）。

## 为什么要它

用户的原话：*"自动探测用户的电脑环境，调整需要调整的东西从而做到兼容性——本身问题都只是适配。"*
现状是：环境读数**已经有了**（`compat.fingerprint()` 的 11 轴、启动体检、逐机的 `click_pref`），
但它们只被"记录/展示"，**没有被用来"选一条更可能成的路"**。这一层就是把这条闭环补上。

## 口径（照 Windows AppCompat 的形，不发明新概念）

| AppCompat 的做法 | 本模块对应物 |
| --- | --- |
| 匹配条件（exe 名/大小/校验和/版本）→ 命中一条记录 | **机器指纹**（OS build × DPI × 微信版本 × 适配层 × 显示器数 × 输入档位） |
| 记录指向一组小补丁（shims） | **策略包**：每条策略都写清"**依据哪条探测**"，可审计 |
| 用户级覆盖（`HKCU\\…\\AppCompatFlags\\Layers`） | `manual` 段：控制台可禁用某条策略，**优先级最高** |
| AppHelp：启动前先提示"已知有问题" | `strategies()` 里的 `risk` 字段：做不到的事提前说 |
| 定期审计、随更新下发 | 记忆带时间戳；本模块只读探测，随包更新 |

## 边界（红线，写进代码 + 判据）

1. **只调"路线"，不调"约束"**：能改的是"**先试哪条路**"（切会话路线/点击目标/OCR 档位），
   **绝不动**安全闸门（不动光标、不抢前台、不群发、不越版本门）——那些是 `ui_adapt` / `guards` / `version_gate` 的事。
2. **默认不自动改**：`env_profile.auto_apply` 默认 **False** ⇒ 本模块只**探测、给建议、记事实**；
   打开后也只对**白名单里的路线族**生效（见 `ROUTE_FAMILIES`）。
3. **授权永远来自现场自检**：记忆只影响"先试哪个"，不影响"这一枪算不算数"（与 `click_pref` 同款）。
4. **连败即丢弃**：某个族记住的成功路线若连续失败到阈值 ⇒ 丢掉记忆、退回默认顺序（防"记错了"锁死）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from .config import DATA_DIR
from . import persist

log = logging.getLogger("persona-morph")

PATH = os.path.join(DATA_DIR, "env_profile.json")
_LOCK = threading.RLock()
PROBE_TTL_S = 600.0      # 探测缓存（10 分钟；探测是只读的，但没必要每次动作都跑）
MAX_FAIL = 2             # 某个族的路线连败到这个数 ⇒ 丢弃记忆
#: **允许自适应调整的路线族白名单**（红线：只有这些能被记忆影响"先试哪个"）
ROUTE_FAMILIES = ("switch_route", "click_target", "ocr_engine")

_LAST_PROBE = {"at": 0.0, "data": None}


# ── 探测 ─────────────────────────────────────────────────────────────
def _op_axes() -> dict:
    """**操作面**的现测事实（`compat.fingerprint()` 管环境面，这里补"能不能操作、走哪条路"）。"""
    out = {}
    # ① 输入后端档位：投递（不动鼠标）还是真鼠标回退
    try:
        from . import input_backend as ib
        b = ib.select_backend(gui=None)
        out["input_backend"] = {"name": getattr(b, "name", "?"),
                                "message": b.__class__.__name__.lower().startswith("message")}
    except Exception as e: # noqa: BLE001
        out["input_backend"] = {"error": str(e)[:80]}
    # ② 前台闸门 + 借用配额余量（A 轮的「学会停手」读数）
    try:
        from . import ui_adapt as ua
        ok, why = ua.fg_allowed()
        out["foreground"] = {"allowed": bool(ok), "why": str(why)[:80]}
        try:
            from . import backoff as bo
            out["foreground"]["quota"] = bo.foreground().status()
        except Exception as e: # noqa: BLE001
            out["foreground"]["quota_error"] = str(e)[:60]
    except Exception as e: # noqa: BLE001
        out["foreground"] = {"error": str(e)[:80]}
    # ③ 切会话台账（最近失败的原因码式说明，取条数与最后一条）
    try:
        from . import wechat as W
        fl = W.recent_switch_fails(3) or []
        out["switch_fails"] = {"recent": len(fl),
                               "last": (fl[-1].get("where") if fl else "") or ""}
    except Exception as e: # noqa: BLE001
        out["switch_fails"] = {"error": str(e)[:80]}
    # ④ 会话行点击目标：**复用既有的逐机偏好**（唯一实现点，不另起一套）
    try:
        from . import click_pref as cp
        st = cp.stats() or {}
        out["click_target"] = {"learned_keys": len(st.get("keys") or {}), "max_fail": cp.MAX_FAIL}
    except Exception as e: # noqa: BLE001
        out["click_target"] = {"error": str(e)[:80]}
    # ⑤ OCR 引擎档位（WinRT 常备；RapidOCR 装了就更准）
    try:
        from . import chat_ocr as co
        rs = co.rapidocr_status() or {}
        out["ocr"] = {"engine_ready": bool(co.available()),
                      "rapidocr": bool(rs.get("installed")),
                      "zoom_target": [co.ZOOM_TARGET_LO, co.ZOOM_TARGET_HI]}
    except Exception as e: # noqa: BLE001
        out["ocr"] = {"error": str(e)[:80]}
    # ⑥ 卡住的 OCR / 熔断状态（用来解释"为什么这台机器突然不认人"）
    try:
        from . import chat_ocr as co2
        out["ocr"]["blocked"] = str(co2.blocked() or "")[:80]
        out["ocr"]["health"] = {k: v for k, v in (co2.health() or {}).items()
                                if k in ("calls", "consecutive", "timeouts")}
    except Exception as e: # noqa: BLE001
        out["ocr"] = {"error2": str(e)[:80]}
    return out


def probe(force: bool = False) -> dict:
    """这台机器的**只读快照**：环境面（复用 `compat.fingerprint`）+ **操作面**（本模块补）。

    10 分钟缓存（`force=True` 绕过）。任何一节失败都只影响它自己。
    """
    now = time.monotonic()
    if not force and _LAST_PROBE["data"] is not None and (now - _LAST_PROBE["at"]) < PROBE_TTL_S:
        return _LAST_PROBE["data"]
    out = {"at": time.time(), "at_text": time.strftime("%m-%d %H:%M:%S"),
           "env": {}, "op": {}}
    try:
        from . import compat as C
        out["env"] = C.fingerprint() or {}
    except Exception as e: # noqa: BLE001
        out["env"] = {"error": str(e)[:100]}
    try:
        out["op"] = _op_axes()
    except Exception as e: # noqa: BLE001
        out["op"] = {"error": str(e)[:100]}
    out["machine"] = machine_id(out)
    _LAST_PROBE["at"], _LAST_PROBE["data"] = now, out
    return out


def machine_id(p: dict = None) -> str:
    """机器指纹（**用于键控记忆**，不含任何隐私：只用 OS build / DPI / 微信类名 / 显示器数 / 适配层）。"""
    d = p if isinstance(p, dict) else probe()
    e = d.get("env") or {}
    w = e.get("windows") or {}
    dd = e.get("display") or {}
    c = e.get("wechat_window") or {}
    g = e.get("data_dir") or {}
    return "%s|%s|%s|%s|%s" % (w.get("build") or "?", dd.get("scale") or "?",
                               (c.get("class") or "?")[:24], dd.get("monitors") or "?",
                               g.get("patched_lib") or "?")


# ── 策略推导（每条都写清"依据哪条探测"）────────────────────────────────
def strategies(p: dict = None) -> list:
    """从探测结果推出**建议**（不执行）。每条：`{id, title, why, evidence, risk, family}`。"""
    d = p if isinstance(p, dict) else probe()
    op = d.get("op") or {}
    env = d.get("env") or {}
    dd = env.get("display") or {}
    g = env.get("data_dir") or {}
    out = []

    ib = op.get("input_backend") or {}
    if ib.get("message") is True:
        out.append({"id": "route_message_backend", "family": "switch_route",
                    "title": "切会话/发送优先走**投递档**（不动鼠标）",
                    "why": "本机投递自检通过 ⇒ 不需要真鼠标就不碰鼠标",
                    "evidence": "input_backend=%s" % ib.get("name"), "risk": "低"})
    else:
        out.append({"id": "route_real_fallback", "family": "switch_route",
                    "title": "投递档不可用 ⇒ 考虑打开真鼠标回退（会动鼠标，需你同意）",
                    "why": "投递自检没通过；真鼠标是最后手段",
                    "evidence": "input_backend=%s" % ib.get("name"), "risk": "中（动鼠标）"})

    fg = op.get("foreground") or {}
    if fg.get("allowed") is False:
        out.append({"id": "fg_stay_background", "family": "switch_route",
                    "title": "保持**全程后台**（不借用前台）",
                    "why": "闸门当前拒绝置前 ⇒ 别指望前台",
                    "evidence": str(fg.get("why") or "")[:60], "risk": "低"})

    ct = op.get("click_target") or {}
    out.append({"id": "click_target_learned" if ct.get("learned_keys") else "click_target_default",
                "family": "click_target",
                "title": ("会话行点击目标：用**本机学到**的那个（成过就排最前）"
                          if ct.get("learned_keys") else "会话行点击目标：用默认顺序，成功一次就记住"),
                "why": "`click_pref` 按机器指纹记「哪一枪真生效」，比写死的版本表可靠",
                "evidence": "learned_keys=%s" % ct.get("learned_keys"), "risk": "低"})

    ocr = op.get("ocr") or {}
    zt = ocr.get("zoom_target") or [45, 90]
    if ocr.get("rapidocr"):
        out.append({"id": "ocr_rapid", "family": "ocr_engine",
                    "title": "OCR：本机已装 RapidOCR ⇒ 读空时自动补读（更准）",
                    "why": "调研实测 RapidOCR 中文 ~98.7%、CPU 0.28s/图、无需显卡",
                    "evidence": "rapidocr=已装", "risk": "低"})
    else:
        out.append({"id": "ocr_winrt_only", "family": "ocr_engine",
                    "title": "OCR：目前只有 Windows 内置引擎（中文约 75~83%）",
                    "why": "没装 RapidOCR；装它可显著提准（可选依赖，纯 CPU）",
                    "evidence": "rapidocr=未装", "risk": "低（想更准可装）"})
    out.append({"id": "ocr_zoom_target", "family": "ocr_engine",
                "title": "OCR：放大到**字高 %d~%dpx** 再识别（本机实测的甜点，不是文献值）" % (zt[0], zt[1]),
                "why": "本机扫描：字高 20~35px 反而差，50~90px 命中率最高",
                "evidence": "zoom_target=%s" % zt, "risk": "低"})
    if ocr.get("blocked"):
        out.append({"id": "ocr_blocked", "family": "ocr_engine",
                    "title": "OCR 目前被熔断/预算挡住（这期间的会话判定会 fail-closed）",
                    "why": "连续超时或本笔预算用尽", "evidence": str(ocr.get("blocked"))[:60], "risk": "中"})

    try:
        sc = (dd.get("scale") or "").strip()
        if sc and sc not in ("100%", "1", "1.0"):
            out.append({"id": "dpi_scaled", "family": "ocr_engine",
                        "title": "显示器缩放 %s ⇒ 坐标换算与截图尺寸都按它走（已在用）" % sc,
                        "why": "缩放非 100%% 时物理像素与逻辑像素不同", "evidence": "scale=%s" % sc,
                        "risk": "低"})
    except Exception as e: # noqa: BLE001
        log.debug("DPI 建议推导跳过：%s", e)

    if g.get("page1") and "密文" in str(g.get("page1")):
        out.append({"id": "db_encrypted", "family": "switch_route",
                    "title": "消息库是**全加密**页 1 ⇒ 读库走解密链（已在用）",
                    "why": "明文头缺失意味着要取密钥后再读", "evidence": str(g.get("page1"))[:40],
                    "risk": "低"})
    return out


# ── 落盘 / 记忆 ───────────────────────────────────────────────────────
def _load() -> dict:
    try:
        if not os.path.exists(PATH):
            return {"schema": 1, "machine": "", "axes": {}, "manual": {}, "memory": {}}
        with open(PATH, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        for k, v in (("axes", {}), ("manual", {}), ("memory", {})):
            if not isinstance(d.get(k), dict):
                d[k] = v
        return d
    except Exception as e: # noqa: BLE001
        log.debug("环境档案读失败（按空处理）：%s", e)
        return {"schema": 1, "machine": "", "axes": {}, "manual": {}, "memory": {}}


def _save(d: dict) -> None:
    """原子写（**永不抛**：档案坏了不能挡住任何动作）。

    ⛔ 写法照 `click_pref._save`（唯一规范来源）：先写 `.tmp` + `fsync`，再 `persist.replace_into(tmp, PATH)`。
       我第一版直接 `persist.replace_into(PATH, json字符串)` —— 那个签名是"把**临时档**换过去"，
       于是写盘每次都抛、被兜底吞掉 ⇒ **记忆永远为空**（判据 C4 当场抓到）。
    """
    try:
        os.makedirs(os.path.dirname(PATH) or ".", exist_ok=True)
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        persist.replace_into(tmp, PATH)
    except Exception as e: # noqa: BLE001
        log.warning("环境档案写失败（忽略，不影响运行）：%s", e)


def auto_apply_enabled() -> bool:
    """`env_profile.auto_apply`（**默认 False**）：打开后记忆才允许影响"先试哪条路"。"""
    try:
        from .config import get_config
        ep = (get_config() or {}).get("env_profile") or {}
        return bool(ep.get("auto_apply", False))
    except Exception as e: # noqa: BLE001
        # 读配置失败就按"关"处理（安全侧），但要留一行痕（本仓静默点棘轮只许降）
        log.debug("env_profile.auto_apply 读不到（按关闭处理）：%s", e)
        return False


def record_probe(p: dict = None) -> dict:
    """把这次探测的**结论**（轴 + 建议）落盘（按机器指纹分组）。**只记事实，不改行为**。"""
    d = p if isinstance(p, dict) else probe()
    with _LOCK:
        st = _load()
        st["machine"] = d.get("machine") or st.get("machine") or ""
        st["axes"] = {"at": d.get("at"), "at_text": d.get("at_text"),
                      "env": d.get("env"), "op": d.get("op")}
        st["strategies"] = strategies(d)
        st["auto_apply"] = auto_apply_enabled()
        _save(st)
        return st


def memory(family: str) -> dict:
    """某个路线族的记忆（`{route: {ok, fail, last_ok}}`）。"""
    with _LOCK:
        st = _load()
        got = (st.get("memory") or {}).get(family)
        return got if isinstance(got, dict) else {}


def remember(family: str, route: str, ok: bool) -> None:
    """记一枪：成功后该路线会被**排到最前**；连败到 `MAX_FAIL` 就整族丢弃（退回默认顺序）。"""
    if family not in ROUTE_FAMILIES or not route:
        return
    with _LOCK:
        st = _load()
        mem = st.setdefault("memory", {})
        fam = mem.setdefault(family, {})
        it = fam.setdefault(str(route), {"ok": 0, "fail": 0, "last_ok": 0})
        if ok:
            it["ok"] = int(it.get("ok") or 0) + 1
            it["fail"] = 0
            it["last_ok"] = time.time()
        else:
            it["fail"] = int(it.get("fail") or 0) + 1
        if int(it.get("fail") or 0) >= MAX_FAIL:
            it["disabled"] = True      # 连败 ⇒ 标记失效（`route_order` 不再使用它）
        _save(st)


def route_order(family: str, base) -> list:
    """按记忆把"成功过的路线"提到最前 → 新顺序（**`auto_apply` 关着就原样返回**）。

    ⛔ 只影响**顺序**，不增不减候选，也绝不越过任何闸门；`manual` 里被禁用的路线会被移到**最后**
    （不是删掉——真到那一步还得靠它兜底，但要排在最后试）。
    """
    items = list(base or [])
    if family not in ROUTE_FAMILIES:
        return items
    mem = memory(family)
    manual = manual_state()
    dis = set((manual.get(family) or {}).get("disabled") or [])

    def _rank(name: str):
        it = mem.get(name) or {}
        used = 0 if it.get("disabled") else int(it.get("last_ok") or 0)
        return (1 if name in dis else 0, -used)

    if not auto_apply_enabled():
        return items          # ⛔ 默认：只建议、不改变顺序
    try:
        return sorted(items, key=lambda x: _rank(str(x)))
    except Exception as e: # noqa: BLE001
        log.debug("路线排序失败（按原顺序）：%s", e)
        return items


def manual_state() -> dict:
    with _LOCK:
        st = _load()
        m = st.get("manual")
        return m if isinstance(m, dict) else {}


def set_manual(family: str, route: str, enabled: bool = False) -> dict:
    """用户级覆盖（对齐 AppCompat 的 `Layers`）：把某条路线**禁用/恢复**。返回新状态。"""
    if family not in ROUTE_FAMILIES:
        return manual_state()
    with _LOCK:
        st = _load()
        man = st.setdefault("manual", {})
        fam = man.setdefault(family, {})
        dis = [x for x in (fam.get("disabled") or []) if x != route]
        if not enabled:
            dis.append(route)
        fam["disabled"] = dis
        _save(st)
        return man


def reset(what: str = "all") -> dict:
    """复位：`memory`（忘掉学到的）/ `manual`（清掉人工覆盖）/ `all`。"""
    with _LOCK:
        st = _load()
        if what in ("memory", "all"):
            st["memory"] = {}
        if what in ("manual", "all"):
            st["manual"] = {}
        _save(st)
        return st


def status_text() -> list:
    """给控制台/检验报告看的一眼读数（本模块**只显示**，不改行为）。"""
    st = _load()
    out = ["环境自适应：自动应用=%s（默认关）· 机器指纹=%s"
           % ("开" if auto_apply_enabled() else "关", (st.get("machine") or "未探测")[:60])]
    ax = st.get("axes") or {}
    if ax.get("at_text"):
        out.append("最近探测：%s" % ax.get("at_text"))
    for s in (st.get("strategies") or [])[:8]:
        out.append("· [%s] %s（依据：%s；风险：%s）"
                   % (s.get("family"), s.get("title"), str(s.get("evidence"))[:40], s.get("risk")))
    mem = st.get("memory") or {}
    for fam, routes in mem.items():
        for r, it in (routes or {}).items():
            out.append("· 记忆[%s] %s：成 %s / 败 %s%s"
                       % (fam, r, it.get("ok"), it.get("fail"),
                          "（已失效）" if it.get("disabled") else ""))
    return out
