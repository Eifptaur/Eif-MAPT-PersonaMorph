# -*- coding: utf-8 -*-
"""风险闸门：决定「这条消息现在能不能发」。

口径（`AGENTS.md §3.2`）：**闸门只管行为特征**——频率、时段、重复度、内容可疑度；
判据里**不出现**"是否后台""是否动鼠标"（那是实现手段，不是风险）。

四层（L0 最严 → L3 只记录）：
    L0  停机开关        `risk.paused`（控制台大红开关；`escalate_after>0` 时连续被拦会自动升级）
    L1  可选节奏旋钮   每分钟/每小时/每天总量、夜间静默 —— **默认全部 0/关**，只给"想自己掐"的用户
    L2  内容与任务层   ★**现在的重心**：群发特征（同一内容短时间发给多个不同会话）· 同会话重复内容
    L3  内容可疑度     链接过多 / 命中观察词 ⇒ **放行但记录**（控制台可见）；禁止词 ⇒ 拦

**为什么节奏默认不限（用户 2026-09-13 定，原话）**：
    "L1 全局节奏…L2 会话节奏没必要限制得这么死，只要限制发送的内容，或者某些任务不做就行，
     就是之前说的那些红线。只在内容层上做筛选。"
    "这个全局节奏、会话节奏应该可以交给用户自定义，让他们自己把控账号的风险。"
    "到时候我会在介绍里面声明，让他们自己把控账号，账号封了也别怪我。"
⇒ 我们的默认把关点是**内容 / 任务**（营销群发、群控加粉这类能力本来就不提供），
  节奏类阈值全部作为**可配置旋钮**（config.json 的 `risk.*`，控制台面板待接）。

**误报来源与防误报规则（用户必问的三件套之一，写在这里备查）**
    · 系统休眠/挂起后恢复、系统时间被改 → 窗口统计用 `time.time()` 单调推进，遇到
      "未来时间戳"（超前 > 60s，说明时钟被回拨）**丢弃该条**而不是永久卡住闸门。
    · 多开实例/多进程同时发送 → 状态文件里记 `owner_pid`；文件被别人改过时**重新载入**
      再判定（不缓存太久）；同一进程内用 RLock 串行。
    · 人工从控制台测试发送被计入配额 → 计数**只记机器人出站路径**（sender 调用
      `note_sent`），控制台"测试发送"走 `check(..., manual=True)` 只记录不计数。
    · 夜间静默误伤白天 → 静默时段默认**关闭**（`quiet_hours: []`），启用时按本机本地时间、
      半开区间 `[start, end)`，跨零点写成 `[22, 7]` 形式。
    · 把"冷场后的一次回复"当成刷屏 → 会话间隔只约束**同一会话的连续发送**，且
      `min_gap_seconds` 默认 3 秒（远小于真人对话节奏）。

**用户可见面（第二件套）**：①控制台顶部状态条（`snapshot()` 供控制台渲染）②被拦时
返回给模型/控制台的中文原因（`Verdict.message`）③`data/risk_events.jsonl` 逐条留痕
④**绝不往微信侧发任何停机/拦截提示**（只在本人机器与控制台出现）。

**弹窗/提示文案（第三件套，逐字）**：
    停机开关：　「已暂停所有自动发送」/「原因：连续 %d 次被风险闸门拦下」/「恢复发送」
    频率拦截：　「发送过快，已拦下这条（每分钟上限 %d 条），约 %d 秒后可再发」
    夜间静默：　「现在是静默时段（%02d:00-%02d:00），已拦下这条；可在控制台『风险闸门』里关闭」
    重复内容：　「和刚发过的内容几乎一样，已拦下（防刷屏）」
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from . import persist
from .config import DATA_DIR, get_config

log = logging.getLogger("persona-morph")

STATE_PATH = os.path.join(DATA_DIR, "risk_state.json")
EVENT_PATH = os.path.join(DATA_DIR, "risk_events.jsonl")

# 闸门自己的默认值（config.json 的 risk 段缺哪项就用这里的；不在 config 里也能跑）
#
# ⚠️ 口径（用户 2026-09-13 定，**改变闸门重心**）：原话——
#    "L1 全局节奏…L2 会话节奏没必要限制得这么死，只要限制发送的内容，或者某些任务不做就行，
#     就是之前说的那些红线。只在内容层上做筛选。"
#    "这个全局节奏、会话节奏应该可以交给用户自定义，让他们自己把控账号的风险。"
#    "到时候我会在介绍里面声明，让他们自己把控账号，账号封了也别怪我。"
# ⇒ **频率/节奏类阈值默认全部为 0（＝不限）**，只作为"用户想自己掐时"的旋钮（config.json / 控制台）；
#   闸门默认真正把关的是**内容层与任务层**：群发特征（同一内容短时间发给多个会话）、链接堆积、
#   禁止词（用户自己填）；"某些任务不做"落在功能层（群控/加粉/营销群发这些能力我们本来就不提供）。
DEFAULTS = {
    "enabled": True,
    "paused": False,
    "per_minute": 0,             # 0 = 不限（节奏交给用户自己把控账号风险）
    "per_hour": 0,
    "per_day": 0,
    "per_chat_per_hour": 0,
    "min_gap_seconds": 0,
    "quiet_hours": [],           # 例：[22, 7]；空 = 不启用夜间静默（默认关）
    "max_links": 3,              # 单条链接超限 ⇒ 记录（L3），不拦
    "watch_keywords": [],        # 命中只记录（L3）
    "block_keywords": [],        # 命中直接拦（默认空，用户按自己的红线填）
    "broadcast_chats": 3,        # 同一内容在窗口内发给 ≥N 个不同会话 ⇒ 判为群发（任务层红线）
    "broadcast_window_seconds": 300,
    "escalate_after": 0,         # 0 = 不自动暂停（频率不限时"连续被拦"没有意义）
    "dup_window_seconds": 120,   # 重复内容判定窗口
    "dup_min_len": 8,            # 多短算"同一句"
}

_LINK_RE = re.compile(r"https?://|www\.", re.I)


class RiskBlocked(RuntimeError):
    """被闸门拦下（带用户可见原因，调用方直接把 message 转给模型/控制台）。"""

    def __init__(self, verdict):
        super().__init__(verdict.message)
        self.verdict = verdict


class Verdict(object):
    __slots__ = ("allowed", "level", "code", "message", "retry_after", "detail")

    def __init__(self, allowed, level, code, message="", retry_after=0, detail=None):
        self.allowed = allowed
        self.level = level          # L0 / L1 / L2 / L3
        self.code = code            # 机器可读：paused / per_minute / quiet_hours / dup / link ...
        self.message = message      # 中文用户可见原因
        self.retry_after = retry_after
        self.detail = detail or {}

    def to_dict(self):
        return {"allowed": self.allowed, "level": self.level, "code": self.code,
                "message": self.message, "retry_after": self.retry_after,
                "detail": self.detail}

    def __repr__(self):
        return "<Verdict %s %s %s>" % ("OK" if self.allowed else "BLOCK",
                                       self.level, self.code)


def _as_list(v, sep=r"[,，;；\s]+"):
    """把"逗号分隔字符串"也当列表用（用户手改 config.json 或控制台存成字符串时防炸）。

    背景：风险闸门早期版本直接 `for kw in cfg['block_keywords']`，若该值是字符串，
    就会**逐字符**当关键词 ⇒ 满屏误拦。这里做一次归一化。
    """
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        return [s for s in re.split(sep, v.strip()) if s]
    return [str(v)]


def _cfg() -> dict:
    raw = {}
    try:
        raw = dict(get_config().get("risk") or {})
    except Exception:
        raw = {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in raw.items() if v is not None})
    out["watch_keywords"] = _as_list(out.get("watch_keywords"))
    out["block_keywords"] = _as_list(out.get("block_keywords"))
    out["quiet_hours"] = _as_list(out.get("quiet_hours"))
    return out


class RiskGate(object):
    """有状态的闸门：窗口计数 + 停机开关 + 事件留痕（状态原子落盘）。"""

    def __init__(self, path: str = STATE_PATH, event_path: str = EVENT_PATH):
        self.path = path
        self.event_path = event_path
        self._lock = threading.RLock()
        self._refuse_overwrite = False       # V-R10-23：坏档留证失败 ⇒ 拒绝覆盖原档
        self._flag_seen = None               # V-R10-24：控制台『暂停/恢复』标记的上次值
        self._st = {"paused": False, "paused_reason": "", "blocks": 0,
                    "min": [], "hour": [], "day": [], "day_key": "",
                    "chats": {}, "events": [], "recent": [],
                    # ⛔ 第十一轮 V-R11-7：①这次暂停**是不是坏档 fail-closed 造成的**
                    #   （顶栏『恢复』能解开它，但必须说清解的是什么）②被操作者解开的次数
                    #   （留痕：能区分"正常恢复"与"有人把坏档锁顶开了"）。
                    "fail_closed": False, "recovered_by_operator": 0,
                    # ⛔ 第十二轮 V-R12-5：一键**暂停**方向也要留痕（与恢复方向对偶）
                    "paused_by_operator": 0}
        self._load()

    # ── 状态读写 ────────────────────────────────────────────────────────
    # ⛔ V-R9-18（审计第九轮，本轮修）：原 `_load` 是 `except: log.warning` ⇒ **fail-open**——
    #    `risk_state.json` 坏掉/读不出来时 `paused` 掉回默认的 False，**停机开关静默解除**，
    #    机器人接着往外发。读不出"停止开关"绝不能等价于"没有暂停" ⇒ 改成 **fail-closed**：
    #    坏档照 `persist.quarantine` 改名留证，内存里置 paused=True + 写清原因，
    #    用户确认后在控制台点『恢复发送』即可（坏档没丢，还能人工修回来）。
    # ⛔ V-R10-25（第十轮）：`_load` 原来只对"读不出来"fail-closed，**形状洞是 fail-open**——
    #    顶层是 list / str / None / `{}` 时 `isinstance(d, dict)` 不成立 ⇒ 既不留证、也不暂停，
    #    `paused` 落回 False（实测 `allowed=true` 闸门放行）。与 timers/holidays/watermark/config
    #    四处同口径：**形状不对 = 坏档**（留证 + fail-closed）。
    #    为什么 `{}` 也算：闸门的状态**永远不是空的**（paused/blocks/day_key/chats… 全是键），
    #    空 dict 只可能来自"被清空/写坏"，绝不该等价于"没暂停"。
    _KEYS = ("paused", "paused_reason", "blocks", "min", "hour", "day", "day_key",
             "chats", "events", "recent")

    @classmethod
    def _shape_ok(cls, d) -> bool:
        """顶层必须是对象，且 10 个键的类型必须与内存初值一致（少键可以，错型不行）。"""
        if not isinstance(d, dict) or not d:
            return False
        ref = {"paused": False, "paused_reason": "", "blocks": 0, "min": [], "hour": [],
               "day": [], "day_key": "", "chats": {}, "events": [], "recent": []}
        for k, v in d.items():
            if k not in ref:
                continue
            if k == "blocks":
                if not isinstance(v, int) or isinstance(v, bool):
                    return False
            elif not isinstance(v, type(ref[k])):
                return False
        return True

    def _load(self):
        if not os.path.exists(self.path):
            return                       # 从来没落过状态 ⇒ 空状态起步（这不是坏档）
        _BAD = object()                  # 哨兵：读到了什么 / 走的默认值，用 `is` 分得清
        d, ok_overwrite = persist.load_checked(self.path, _BAD)
        # V-R10-23：原档读不出来且留证也失败 ⇒ 原档还在原地。这里**不写盘**（`_save` 拒写），
        # 否则那一枪就把用户的停机开关/计数盖掉了。
        self._refuse_overwrite = not ok_overwrite
        if d is _BAD:
            self._fail_closed("风险状态文件读不出来，已按最保守处理成暂停")
            return
        if not self._shape_ok(d):
            # ⚠️ `null` 也要走这里：`json.load` 对字面量 `null` 是**成功**返回 `None`（不是异常）
            # ⇒ 上面那个分支抓不到它，而它就是审计点名的"顶层 null ⇒ fail-open"那一种。
            kept = persist.quarantine(self.path)
            if not kept:
                self._refuse_overwrite = True
            self._fail_closed("风险状态文件形状不对（顶层不是合法状态对象），已按最保守处理成暂停")
            log.warning("风险闸门状态形状不对（类型 %s）⇒ %s",
                        type(d).__name__,
                        ("已按坏档留证：%s" % kept) if kept else "**留证失败 ⇒ 原档保持原样、禁止覆盖**")
            return
        self._st.update({k: v for k, v in d.items() if k in self._st})
        # 档**这次读得动且形状对** ⇒ 之前那次 fail-closed 已经不成立了（别让它跨重启粘住）
        self._st["fail_closed"] = False

    def _fail_closed(self, reason: str):
        self._st["paused"] = True
        self._st["paused_reason"] = reason
        self._st["fail_closed"] = True          # V-R11-7：标记"这次暂停是坏档引起的"
        log.warning("风险闸门 %s —— 确认后可点控制台『恢复发送』：%s", reason, self.path)

    def _save(self):
        # V-R9-22：临时名带 pid + 随机后缀 + os.replace（老写法共用 `path + ".tmp"`）
        if self._refuse_overwrite:
            # V-R10-23：原档读不出来且留证失败 ⇒ 它还在原地；写出去就是把它整体覆盖。
            log.warning("风险闸门状态档读不出来且留证失败 ⇒ **拒绝覆盖**（本次不落盘）：%s", self.path)
            return
        if not persist.atomic_write_json(self.path, self._st, indent=None):
            log.warning("风险闸门状态落盘失败 ⇒ 停机开关重启后可能丢失（原档未动）：%s", self.path)

    def _event_external(self, code: str, msg: str) -> None:
        """把一条事实写进**事件台账**（`data/risk_events.jsonl`）—— 状态档写不进去时用它。

        ⛔ 第十二轮 **V-R12-7**：坏档留证失败 ⇒ 状态档**拒写**（保住原档），但"操作者解开过这次暂停"
        这件事不能跟着丢 —— 这台机器上唯一还能追加写的档就是事件台账（不受 `_refuse_overwrite` 管）。
        """
        try:
            rec = {"ts": int(time.time() * 1000), "level": 3, "code": str(code)[:40],
                   "chat": "", "allowed": True, "msg": str(msg)[:200], "text": ""}
            os.makedirs(os.path.dirname(self.event_path), exist_ok=True)
            with open(self.event_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _event(self, verdict, chat_key, text=""):
        rec = {"ts": int(time.time() * 1000), "level": verdict.level, "code": verdict.code,
               "chat": str(chat_key or "")[:60], "allowed": bool(verdict.allowed),
               "msg": (verdict.message or "")[:120], "text": str(text or "")[:60]}
        self._st["events"] = (self._st.get("events") or [])[-49:] + [rec]
        try:
            os.makedirs(os.path.dirname(self.event_path), exist_ok=True)
            with open(self.event_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ── 停机开关 ────────────────────────────────────────────────────────
    def _sync_operator(self):
        """把控制台那个『暂停所有发送』勾选框 / 顶栏『暂停』按钮的**文件级真相**并进来。

        V-R10-24（审计第十轮）：产品里其实有**两套互不相通的停机开关**，用户会被锁死——
          ①`risk.paused`（落 `data/risk_state.json`，跨重启粘住；`POST /api/risk` 是唯一能恢复的
            入口，而**全仓没有任何前端调用它**）；
          ②`data/paused.flag`（顶栏『暂停/恢复』按钮、`agent/control.is_paused()`，每个发送链
            每一步都查它）。
        ⇒ 只被 ① 锁住时，用户按遍界面上的按钮都解不开（它改的是 ②）。
        这里把 ② 的**跳变**当作操作者的显式指令：标记出现 ⇒ 跟着暂停；标记消失 ⇒ 跟着恢复。

        为什么只看"跳变"：配置里的 `risk.paused` 与 `_load` 的 fail-closed 是两个独立来源，
        按"当前值"同步会在每次首查就把 fail-closed 的暂停抹掉。跳变则专指"刚有人按了按钮"。
        只在**默认状态档**上生效（判据各自用 `tempfile` 建独立实例，不受本机 data/ 影响）。

        ⛔ 第十一轮 **V-R11-11 第 3 条**：老签名 `_sync_operator(force=False)` 的 `force=True`
        **全仓无调用者**（死参）⇒ 删掉，行为不变（`force` 只跳过"首次只记基线"与"值没变就返回"）。
        """
        if os.path.abspath(self.path) != os.path.abspath(STATE_PATH):
            return
        try:
            from . import control as _ctl
            now = bool(_ctl.is_paused())
        except Exception:
            return
        prev, self._flag_seen = self._flag_seen, now
        if prev is None:
            return                                  # 首次：只记基线，不做动作
        if now == prev:
            return
        if now and not self._st.get("paused"):
            # ⛔ 2026-09-22 修（第十二轮 **V-R12-5** · P2）：**暂停方向也要落盘** —— 第十一轮只给
            #   "恢复"方向补了 `_save()`，于是"操作者按了暂停"只活在内存里：盘上还是 `paused:false`，
            #   别的读者（verifiers 读快照 / 重启后的 `_load`）看到的是旧值，flag 一被清就无声恢复。
            self._st["paused"] = True
            self._st["paused_reason"] = "控制台按了「暂停所有发送」"
            self._st["paused_by_operator"] = int(self._st.get("paused_by_operator") or 0) + 1
            try:
                self._save()
            except Exception as _e_sv2:
                log.warning("风险闸门：跟随『暂停』时落盘失败（重启后可能不再暂停）：%s", _e_sv2)
            log.warning("风险闸门：跟随控制台『暂停』标记 ⇒ 暂停自动发送（一键暂停路径 · 第 %d 次）",
                        self._st["paused_by_operator"])
        elif (not now) and self._st.get("paused"):
            # 一键恢复：用户在界面上点『恢复』（标记消失）⇒ 闸门跟着解，不用去碰没有前端入口的 API
            # ⛔ 2026-09-21 修（第十一轮 **V-R11-7** · P2）：这条"出口"保留（把用户锁死更糟），
            #   但补三件：①**落盘**（老写法只改内存 ⇒ 重启又粘上暂停，用户看到"恢复了又自己停了"
            #   却查不出原因）②**留痕**（这次解的是不是坏档 fail-closed 的锁，写进状态与日志）
            #   ③坏档本身**不被抹掉**（`_refuse_overwrite` / 留证文件仍在原地，人工还能查）。
            _was_flc = bool(self._st.get("fail_closed"))
            self._st["paused"] = False
            self._st["paused_reason"] = ""
            self._st["blocks"] = 0
            self._st["fail_closed"] = False
            self._st["recovered_by_operator"] = int(self._st.get("recovered_by_operator") or 0) + 1
            try:
                self._save()
            except Exception as _e_sv:
                log.warning("风险闸门：跟随『恢复』时落盘失败（重启后可能又粘上暂停）：%s", _e_sv)
            if self._refuse_overwrite:
                # ⛔ 2026-09-22 加（第十二轮 **V-R12-7** · P3）：坏档**留证也失败**时 `_save` 是**拒写**的
                #   ⇒ 内存里锁解开了、盘上一个字节没变，重启后 fail-closed 暂停又回来，连
                #   `recovered_by_operator` 也一起归零（事后无从知道"有人解开过"）。
                #   那就把这件事实**写到另一个不受保护的档**（事件台账，追加热写）。
                self._event_external("operator_recover_refused_persist",
                                     "坏档留证失败 ⇒ 状态档拒写；这次由操作者解开的暂停**只活在内存**"
                                     "（重启会回到 fail-closed 暂停）")
            log.warning("风险闸门：跟随控制台『恢复』标记 ⇒ 已恢复自动发送（一键恢复路径 · 第 %d 次）%s",
                        self._st["recovered_by_operator"],
                        "——⚠️ 这次的暂停原本是**坏档 fail-closed** 造成的：锁已按你的操作解开，"
                        "坏档留证仍在 data/ 下（要恢复旧状态就人工看它）" if _was_flc else "")

    def pause(self, reason: str = ""):
        with self._lock:
            self._st["paused"] = True
            self._st["paused_reason"] = str(reason or "")[:120]
            self._save()
        # 两套开关互相同步：闸门暂停 ⇒ 也让 `control.is_paused()` 为真（发送链每一步都查它）
        try:
            from . import control as _ctl
            _ctl.set_paused_flag(True)
        except Exception:
            pass

    def recover(self) -> dict:
        """**一键恢复**（V-R10-24）：清掉闸门暂停 + 清掉控制台的暂停标记 ⇒ 立刻能发。

        为什么要有它：`risk.paused` 是**落盘**的（跨重启粘住），而原先唯一的恢复入口
        `POST /api/risk`（`agent/webui.py:1447`）**全仓没有前端调用者**——坏档 fail-closed
        或被拦升级之后，用户按遍界面都解不开。控制台的可点入口是顶栏『暂停/恢复』
        （`/api/pause`|`/api/resume` → `orch.set_paused()` + `data/paused.flag`）；
        本方法把**两套开关一起清干净**，让"再点一次恢复"或调用本方法都能真的恢复。
        """
        self.resume()
        try:
            from . import control as _ctl
            _ctl.set_paused_flag(False)
        except Exception:
            pass
        return self.snapshot()

    def resume(self):
        with self._lock:
            self._st["paused"] = False
            self._st["paused_reason"] = ""
            self._st["blocks"] = 0
            self._st["fail_closed"] = False      # V-R11-7：显式恢复 ⇒ 坏档锁也算解开了
            self._save()
        try:
            from . import control as _ctl
            _ctl.set_paused_flag(False)
        except Exception:
            pass

    def is_paused(self) -> bool:
        # V-R10-24：控制台顶栏『暂停/恢复』改的是 `data/paused.flag`；它一变，这里要跟着变
        # （否则界面上显示"运行中"、闸门却在拦，用户找不到原因）。
        with self._lock:
            self._sync_operator()
            return bool(self._st.get("paused"))

    # ── 主判定 ──────────────────────────────────────────────────────────
    def check(self, chat_key: str, text: str = "", now: float = None,
              manual: bool = False) -> Verdict:
        cfg = _cfg()
        now = float(now if now is not None else time.time())
        text = str(text or "")

        def verdict(allowed, level, code, message="", retry_after=0, detail=None):
            v = Verdict(allowed, level, code, message, retry_after, detail)
            self._event(v, chat_key, text)
            if not allowed and not manual:
                self._st["blocks"] = int(self._st.get("blocks") or 0) + 1
                if cfg.get("escalate_after") and self._st["blocks"] >= int(cfg["escalate_after"]):
                    self._st["paused"] = True
                    self._st["paused_reason"] = "连续 %d 次被风险闸门拦下" % self._st["blocks"]
                    log.warning("风险闸门：连续被拦 %d 次，已自动暂停自动发送", self._st["blocks"])
            elif allowed:
                self._st["blocks"] = 0
            self._save()
            return v

        if not cfg.get("enabled", True):
            return Verdict(True, "L3", "disabled", "")

        with self._lock:
            # V-R10-24：先并一次"操作者刚按的那个开关"（控制台勾选框 / 顶栏暂停按钮）
            self._sync_operator()

            # L0 停机开关
            if self._st.get("paused"):
                why = self._st.get("paused_reason") or "手动暂停"
                return verdict(False, "L0", "paused",
                               "已暂停所有自动发送（原因：%s）；在控制台『风险闸门』里点『恢复发送』即可" % why)

            self._prune(cfg, now)

            # L1 夜间静默
            qh = cfg.get("quiet_hours") or []
            if isinstance(qh, (list, tuple)) and len(qh) == 2:
                try:
                    hs, he = int(qh[0]), int(qh[1])
                    h = time.localtime(now).tm_hour
                    in_quiet = (hs <= h < he) if hs < he else (h >= hs or h < he)
                    if in_quiet:
                        return verdict(False, "L1", "quiet_hours",
                                       "现在是静默时段（%02d:00-%02d:00），已拦下这条；"
                                       "可在控制台『风险闸门』里关闭静默" % (hs, he))
                except Exception:
                    pass

            # L1 全局总量
            for key, cap, win, label in (("min", "per_minute", 60, "每分钟"),
                                         ("hour", "per_hour", 3600, "每小时"),
                                         ("day", "per_day", 86400, "每天")):
                cap = int(cfg.get(cap) or 0)
                if cap > 0 and len(self._st[key]) >= cap:
                    oldest = min(self._st[key]) if self._st[key] else now
                    return verdict(False, "L1", "global_" + key,
                                   "发送过快，已拦下这条（%s上限 %d 条），约 %d 秒后可再发"
                                   % (label, cap, max(1, int(oldest + win - now))),
                                   retry_after=max(1, int(oldest + win - now)))

            # L2 会话节奏
            ch = self._st["chats"].setdefault(str(chat_key), {"hour": [], "last_ts": 0.0, "last_texts": []})
            cap_ch = int(cfg.get("per_chat_per_hour") or 0)
            if cap_ch > 0 and len(ch.get("hour") or []) >= cap_ch:
                return verdict(False, "L2", "chat_hour",
                               "这个会话一小时内已经发了 %d 条，先缓一缓" % cap_ch)
            gap = float(cfg.get("min_gap_seconds") or 0)
            if gap > 0 and ch.get("last_ts") and (now - float(ch["last_ts"])) < gap:
                wait = int(gap - (now - float(ch["last_ts"]))) + 1
                return verdict(False, "L2", "chat_gap",
                               "同一会话发送间隔太短（最小 %s 秒），约 %d 秒后再发" % (gap, wait),
                               retry_after=wait)

            # L2 重复内容
            dup_win = float(cfg.get("dup_window_seconds") or 0)
            dup_min = int(cfg.get("dup_min_len") or 8)
            norm = re.sub(r"\s+", "", text)[:200]
            if dup_win > 0 and len(norm) >= dup_min:
                for prev, ts in (ch.get("last_texts") or []):
                    if prev == norm and (now - float(ts)) < dup_win:
                        return verdict(False, "L2", "duplicate",
                                       "和刚发过的内容几乎一样，已拦下（防刷屏）")

            # ★ 任务层红线（闸门现在的重心）：群发特征 —— 同一内容短时间发给多个不同会话
            bc = int(cfg.get("broadcast_chats") or 0)
            bw = float(cfg.get("broadcast_window_seconds") or 0)
            if bc > 0 and bw > 0 and len(norm) >= max(4, dup_min):
                peers = {str(c) for s, c, ts in (self._st.get("recent") or [])
                         if s == norm and (now - float(ts)) < bw}
                peers.add(str(chat_key))
                if len(peers) >= bc:
                    return verdict(False, "L2", "broadcast",
                                   "同一内容将发给 %d 个不同会话（%d 秒内 ≥%d 个即判群发），已拦下"
                                   "——营销群发是我们不做的任务" % (len(peers), int(bw), bc),
                                   detail={"chats": sorted(peers)[:6]})

            # L3 内容可疑度（放行，只记录）
            links = len(_LINK_RE.findall(text))
            max_links = int(cfg.get("max_links") or 0)
            for kw in (cfg.get("block_keywords") or []):
                if kw and str(kw) in text:
                    return verdict(False, "L3", "block_keyword", "内容命中禁止词『%s』，已拦下" % kw)
            if max_links and links > max_links:
                return verdict(True, "L3", "many_links",
                               "这条里有 %d 个链接（上限 %d），已放行但记录在案" % (links, max_links),
                               detail={"links": links})
            for kw in (cfg.get("watch_keywords") or []):
                if kw and str(kw) in text:
                    return verdict(True, "L3", "watch_keyword",
                                   "内容命中观察词『%s』，已放行但记录在案" % kw,
                                   detail={"keyword": kw})
            return verdict(True, "L2", "ok")

    # ── 发送成功后记账（只记机器人出站路径）────────────────────────────
    def note_sent(self, chat_key: str, text: str = "", now: float = None):
        cfg = _cfg()
        now = float(now if now is not None else time.time())
        with self._lock:
            self._prune(cfg, now)
            self._st["min"].append(now)
            self._st["hour"].append(now)
            self._st["day"].append(now)
            ch = self._st["chats"].setdefault(str(chat_key), {"hour": [], "last_ts": 0.0,
                                                              "last_texts": []})
            ch["hour"].append(now)
            ch["last_ts"] = now
            norm = re.sub(r"\s+", "", str(text or ""))[:200]
            if norm:
                ch["last_texts"] = (ch.get("last_texts") or [])[-4:] + [[norm, now]]
                # 跨会话留一条"最近发过什么"（群发特征检测用；不记明文全文，只留归一化片段）
                self._st["recent"] = (self._st.get("recent") or [])[-49:] + [[norm, str(chat_key), now]]
            self._save()

    def _prune(self, cfg: dict, now: float):
        """清掉过期窗口；**未来时间戳**（时钟被回拨）直接丢弃，别让闸门永久卡住。"""
        def keep(ts, win):
            try:
                ts = float(ts)
            except Exception:
                return False
            if ts - now > 60:
                return False
            return (now - ts) < win
        self._st["min"] = [t for t in (self._st.get("min") or []) if keep(t, 60)]
        self._st["hour"] = [t for t in (self._st.get("hour") or []) if keep(t, 3600)]
        day_key = time.strftime("%Y-%m-%d", time.localtime(now))
        if self._st.get("day_key") != day_key:
            self._st["day_key"] = day_key
            self._st["day"] = []
        self._st["day"] = [t for t in (self._st.get("day") or []) if keep(t, 86400)]
        for ch in (self._st.get("chats") or {}).values():
            ch["hour"] = [t for t in (ch.get("hour") or []) if keep(t, 3600)]
            ch["last_texts"] = [[s, t] for s, t in (ch.get("last_texts") or [])
                                if keep(t, max(60.0, float(cfg.get("dup_window_seconds") or 120)))]
            # last_ts 是标量，同样要挡「未来时间戳」（系统时钟被回拨时会把闸门永久卡死）
            try:
                if float(ch.get("last_ts") or 0) - now > 60:
                    ch["last_ts"] = 0.0
            except Exception:
                ch["last_ts"] = 0.0
        # 跨会话近期发送记录（群发特征检测）
        self._st["recent"] = [[s, c, t] for s, c, t in (self._st.get("recent") or [])
                              if keep(t, max(60.0, float(cfg.get("broadcast_window_seconds") or 300)))]

    # ── 给控制台 ────────────────────────────────────────────────────────
    def snapshot(self) -> dict:
        cfg = _cfg()
        with self._lock:
            now = time.time()
            self._prune(cfg, now)
            return {
                "enabled": bool(cfg.get("enabled", True)),
                "paused": bool(self._st.get("paused")),
                "paused_reason": self._st.get("paused_reason") or "",
                # ⛔ V-R11-7：这两项给控制台/检验器看 —— ①这次暂停是不是坏档引起的
                #   ②有没有人用顶栏『恢复』把坏档锁顶开过（留痕，别让"证据"随恢复消失）
                "fail_closed": bool(self._st.get("fail_closed")),
                "recovered_by_operator": int(self._st.get("recovered_by_operator") or 0),
                "blocks": int(self._st.get("blocks") or 0),
                "minute": len(self._st["min"]), "minute_cap": int(cfg.get("per_minute") or 0),
                "hour": len(self._st["hour"]), "hour_cap": int(cfg.get("per_hour") or 0),
                "day": len(self._st["day"]), "day_cap": int(cfg.get("per_day") or 0),
                "quiet_hours": list(cfg.get("quiet_hours") or []),
                "events": list(self._st.get("events") or [])[-10:],
            }


_gate = None
_gate_lock = threading.Lock()


def gate() -> RiskGate:
    """全局单例（懒加载；测试可传 path 造独立实例）。"""
    global _gate
    if _gate is None:
        with _gate_lock:
            if _gate is None:
                _gate = RiskGate()
    return _gate


def check(chat_key: str, text: str = "", **kw) -> Verdict:
    return gate().check(chat_key, text, **kw)


def note_sent(chat_key: str, text: str = "", **kw):
    gate().note_sent(chat_key, text, **kw)


def snapshot() -> dict:
    return gate().snapshot()


def pause(reason: str = ""):
    gate().pause(reason)


def resume():
    gate().resume()


def recover() -> dict:
    """**一键恢复**（V-R10-24）：闸门暂停 + 控制台暂停标记一起清，立刻能发（详见 `RiskGate.recover`）。"""
    return gate().recover()
