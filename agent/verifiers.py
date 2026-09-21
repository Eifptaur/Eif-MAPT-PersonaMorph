# -*- coding: utf-8 -*-
"""**症状检验器**（2026-09-18 落地，作者口径：「用户有哪方面的问题，就点那个检验器，把报告发给我」）。

设计口径（来自检索到的共识 + 本项目 `attach_diagnosis` 的既有形状）：
  ① **原子化二值检查**：一条检查只问一件事，`ok=True/False` + **自带证据**（数字 / 文件 / 行号），不做分数聚合；
  ② **一个总判决**：不是分数，而是「卡在哪一条 + 下一步做什么」；
  ③ **症状驱动**：用户点他的**话术**（"发不出去""它回自己""看不到表情"…），不需要懂内部结构；
  ④ **可复制**：报告是一段纯文本，用户粘进「反馈」就能让我直接定位；
  ⑤ **只读**：全部检查只读配置/文件/日志/端口，**不动窗口、不发消息、不改配置**。

返回结构（与 `attach_diagnosis` 同形，便于控制台复用渲染）：
    {"id", "name", "symptom", "ok", "verdict", "action", "checks": [{"name","ok","detail"}], "report"}
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import socket
import time

log = logging.getLogger("persona-morph")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 小工具（全部只读、全部兜异常）──────────────────────────────────────────
def _p(*parts) -> str:
    return os.path.join(ROOT, *parts)


def _read_json(path, default=None):
    try:
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default if default is not None else {}


def _tail(path, n: int = 400) -> list:
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()[-n:]
    except Exception:
        return []


def _tail2(path, n: int = 400):
    """与 `_tail` 同，但**读不到就返回 None**（＝没测到），而不是吞成空表。

    ⛔ 2026-09-21 加（第九轮 **V-R9-13** · P1）：`_tail` 把"文件不存在 / 权限被拒 / 编码炸"
    一律变成 `[]` ⇒ 调用方拿到空表就判"最近 0 次相关记录" ⇒ **报成 ✅**。审计用真 `icacls /deny`
    造出的现场正是这样：日志里明明有两行失败，检验器却给"通过"。凡"最近有没有 X 的记录"这类
    检查，**读不到日志就是没测到**（None），不是"没有记录"。
    """
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()[-n:]
    except FileNotFoundError:
        return []                      # 文件还没生成 ⇒ 确实"没有记录"（刚装/刚重启）
    except Exception:
        return None                    # 读不到（权限/编码/盘）⇒ 没测到


_CFG_ERR = ""          # 配置读不到时记下原因（V-R9-14：用了内置默认值的那几格**不算结论**）
_RUNTIME_HOW: dict = {}   # 运行中实例的 `_db_how`（webui 每次跑检验器前喂，见 V-R9-11）
_RUNTIME_CAP: dict = {}   # 运行中实例的 `_cap`（各张表的读写结果；V-R10-8 起"消息库读不到"要用）


def set_runtime_how(how, cap=None) -> None:
    """把**运行中实例**的 `_db_how`（与 `_cap`）喂进来（`webui` 的 `/api/verify` 每次调一次）。**绝不抛**。

    ⛔ 2026-09-21 加（第九轮 **V-R9-11**）：`wechat_dir.status()` **不带 `how`** 时拿不到
    "我在读哪个账号"这一维（`account`/`account_names`/`account_live` 全是空），而检验器原来据此判
    **✅**（报告里明明写着"在读账号 没认出来"）⇒ 假绿。`cap` 是同一原则的延伸：**哪张表读失败了**
    只有运行中实例知道（"消息库读不到"这条症状的定位就靠它）。
    """
    global _RUNTIME_HOW, _RUNTIME_CAP
    try:
        _RUNTIME_HOW = dict(how or {})
    except Exception:
        _RUNTIME_HOW = {}
    try:
        _RUNTIME_CAP = dict(cap or {})
    except Exception:
        _RUNTIME_CAP = {}


def _cfg() -> dict:
    global _CFG_ERR
    try:
        from .config import get_config
        cfg = get_config() or {}
        _CFG_ERR = "" if cfg else "配置是空的"
        return cfg
    except Exception as e:
        _CFG_ERR = str(e)[:60] or type(e).__name__
        return {}


def _port() -> int:
    try:
        return int((_cfg().get("server") or {}).get("port") or 3210)
    except Exception:
        return 3210


def _port_open(port: int = 0, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port or _port())), timeout=timeout):
            return True
    except Exception:
        return False


def _check(name, ok, detail, info: bool = False) -> dict:
    """一格检查。`ok` 是**三态**：True 通过 / False 未通过 / **None ＝ 没测到**。

    ⛔ 2026-09-21 修（第四轮审计 **V-R4-11，P2**）：原来只允许布尔，于是"读不到就按乐观处理"
    的那几处直接写了 `True` ⇒ 报告画 ✅，而结论可能与事实相反（审计点名：用户点「抢窗口」时
    报告写「✅ 后台承诺成立」）。⇒ 现在"没测到"有自己的一档：**既不算通过、也不算失败**，
    并在判决与逐项里单独标出来（**不许拿"没测到"冒充"承诺成立"**）。

    `info=True` ＝ **说明格**（第十轮 **V-R10-10**）：它读出来的是"顺便告诉你一件事"，**不是判据**
    （例：出站闸门拦了几条 / 有没有 `update_done.flag` / 最近看到的 @ 名字是谁）。这类格用
    `ok=None` 只是为了画 `○`，**不许算进"没测到"的计数** —— 否则报告头会**恒 ◐、永不给 ✅**。
    """
    return {"name": name, "ok": (None if ok is None else bool(ok)), "detail": str(detail),
            "info": bool(info) or None}


def _count(lines, *needles) -> int:
    n = 0
    for l in lines:
        if all(x in l for x in needles):
            n += 1
    return n


def _fresh_age(secs, lo: float, hi: float):
    """**统一的新鲜度助手**（三处共用）：`secs` 是不是落在 `[lo, hi]` 秒这个"刚写过"的区间里。

    返回 `True` / `False` / `None(=读不到，没测到)`。

    ⛔ 2026-09-21（第五轮回执 **V-R5A-3 + U3**）：三处"新鲜度"以前各写各的，而且**只看上界** ⇒
    **未来时间**（系统时钟错乱、文件被改过、挂载时钟偏移）会被算成"刚刚写过"：
    实测 `update_state.json` 的 mtime 设成 3 天后 ⇒ 判"最近 30 分钟内写的"，detail 里还打出
    `-4320 分钟前`。⇒ 口径统一成三段（与项目惯例一致）：
      · 读不到 ⇒ **None**（没测到）；
      · **下界都不满足（含负数＝未来）⇒ False**（坏读数：这不是"新鲜"，也不是"旧"）；
      · 区间内 ⇒ **True**；**超过上界 ⇒ None**（东西就是旧的＝这次没有新证据，不是"故障"）。
    """
    if secs is None:
        return None
    try:
        s = float(secs)
    except Exception:
        return None
    if s < float(lo):
        return False
    if s <= float(hi):
        return True
    return None


def _verdict(checks, ok_all_msg, action_map) -> tuple:
    """一条总判决：第一条不通过的检查 ⇒ 它就是卡点（顺序即优先级）。

    ⭐ 2026-09-19 加**矛盾检测**：如果"负向结论"被同一份报告里的"正向证据"直接否证
    （现场：检验器说「模型 key 没填」，可同一份报告里写着「最近一轮在 36 分钟前」——
    没填 key 根本发不出上一轮），那就**不许**再给"去填 key"这种指令：那是把**我们自己读数的错**
    当成用户的故障，用户只能一脸懵（原话「第三个贼奇怪」）。改判"证据矛盾、请把报告发我"。
    """
    bad = [c for c in checks if c["ok"] is False]
    unknown = [c for c in checks if c["ok"] is None]

    def _caveat(msg):
        """把"没测到"的项**明说出来**（V-R4-11：不许拿没测到冒充承诺成立）。"""
        if not unknown:
            return msg
        _names = "、".join((c.get("name") or "?") for c in unknown[:4])
        return ("%s（另有 %d 项**没测到**：%s%s —— 这些既不算通过也不算失败，别当成「承诺成立」）"
                % (msg, len(unknown), _names, "…" if len(unknown) > 4 else ""))

    if not bad:
        # ⛔ 2026-09-21（第五轮回执 **V-R5A-4**）：一项都没测到（全是 None）时**不许给 ✅** ——
        #   以前只改了措辞（"另有 N 项没测到"），判决却仍是"通过"，用户看到的就是 ✅。
        #   ⇒ 全空 ⇒ `ok=None`（没测到），由 `_finish` 渲染成"没测到"，调用方/面板都不把它当通过。
        if checks and all(c["ok"] is None for c in checks):
            return (None, "**一项都没测到**：%s（这不是「通过」，是这次没能取到判据所需的现场；"
                          "把这段报告发我，或按下面几格补齐现场后重跑）"
                    % "、".join((c.get("name") or "?") for c in checks[:4]), "")
        return True, _caveat(ok_all_msg), ""
    first = bad[0]
    _pos = {c.get("name") or "" for c in checks if c.get("ok")}
    # ⚠️ 2026-09-20：水位那一格已改名（`监听水位有记录` → **`监听水位有推进`**，并改成看"序号>0"），
    #   这里的字符串匹配要跟着改——两边都列上，免得改名把"矛盾检测"悄悄失效（那是假绿）。
    _alive = any(("最近有过一轮响应" in n or "监听水位有" in n) for n in _pos)
    if _alive and ("模型 key" in (first.get("name") or "")):
        return (False,
                "证据互相矛盾：这份报告说「%s」，可同一份报告里「最近有过一轮响应 / 监听水位有推进」"
                "是**通过**的 —— 没填 key 不可能发出上一轮 ⇒ **这是检验器的读数与运行时不一致，"
                "不是你的配置有问题**。" % first.get("detail"),
                "把这段报告直接粘进「反馈」发我（我照这条修检验器），**先不用改配置**")
    # 没在 action_map 里写明的检查，也要给一句可执行的下一步
    # （否则用户看到"卡住"却不知道干什么 —— 判据里专门钉了这一条）
    return (False, _caveat("卡在「%s」：%s" % (first["name"], first["detail"])),
            action_map.get(first["name"]) or "把这段报告粘进「反馈」发我，我照着这条定位")


# ── ① 消息发不出去 ─────────────────────────────────────────────────────────
def v_send_blocked() -> dict:
    c = _cfg()
    checks = []
    log_tail = _tail(_p("logs", "persona_morph.log"), 600)
    # 暂停开关（硬冻结时确实一条都发不出）
    paused = os.path.exists(_p("data", "paused.flag"))
    try:
        from . import bg_status as _bg
        st = _bg.status() or {}
        paused = bool(st.get("paused"))
    except Exception:
        st = {}
    checks.append(_check("没有处于暂停", not paused,
                         "未暂停" if not paused else "当前是**暂停**状态 ⇒ 点「继续」后才会发"))
    # 版本门（2026-09-18 起默认放行）
    try:
        from . import version_gate as vg
        v = vg.status() or {}
        gate = v.get("gate") or v
        lvl = str(gate.get("level") or gate.get("status") or "?")
        allow = gate.get("allow", gate.get("ok"))
        checks.append(_check("版本门不拦发送", allow is not False,
                             "level=%s allow=%s" % (lvl, allow)))
    except Exception as e:
        checks.append(_check("版本门不拦发送", None,
                             "说明（不是判据）：读不到版本门 ⇒ 没测到，按不拦处理：%s" % str(e)[:40]))
    # 投递后端 + 后台档
    ib = (c.get("input") or {})
    bo = bool((c.get("wechat") or {}).get("background_only", True))
    checks.append(_check("投递档可用（不是只走真鼠标被闸住）",
                         str(ib.get("backend") or "auto") != "real",
                         "input.backend=%s · background_only=%s · allow_real_fallback=%s"
                         % (ib.get("backend") or "auto", bo, ib.get("allow_real_fallback"))))
    # 微信主窗在不在（只读，构造最小）
    try:
        from . import input_backend as _ib
        main = _ib.find_main_window()
        checks.append(_check("微信主窗找得到", bool(main), "hwnd=%s" % (main or "没找到")))
    except Exception as e:
        checks.append(_check("微信主窗找得到", False, "探测失败：%s" % str(e)[:40]))
    # 消息库读得到
    try:
        from wechatauto import WeChatDB
        db = WeChatDB()
        chats = db.list_message_chats() or []
        checks.append(_check("消息库读得到", bool(chats), "会话数=%d · 目录=%s"
                             % (len(chats), os.path.basename(str(getattr(db, 'account_dir', '')) or '-'))))
    except Exception as e:
        checks.append(_check("消息库读得到", False, "打不开消息库：%s" % str(e)[:60]))
    # 最近一次投递发送的结果
    last_ok = _count(log_tail[-200:], "投递发送成功")
    # ⛔ 2026-09-21 加（V-R9-12）：**针要够宽** —— 原来的三根（投递发送失败 / 会话头不匹配 /
    #   判据不可用）认不出 `send_text` 拒发时真正写进日志的那两句（"投递档确认不了目标会话"、
    #   "内容级复核说不是这个会话"）⇒ 审计的 C 线夹具里摆着真失败、判决仍是 ✅。
    last_fail = [l for l in log_tail[-200:] if ("投递发送失败" in l or "会话头不匹配，拒绝投递" in l
                                                or "判据不可用" in l
                                                or "投递档确认不了目标会话" in l
                                                or "内容级复核说不是这个会话" in l
                                                or "名字档也没确认" in l)]
    # ⚠️ 别把"最近没发过"当成故障：刚重启/刚装好时日志里本来就没有成功记录 —— 那是"未知"，
    #    不是"坏了"。只有**确实有失败/可疑记录**时才判不通过（避免检验器自己吓用户）。
    if last_fail:
        _send_ok, _send_detail = False, ("最近 200 行里有 %d 条失败/可疑（例：%s）"
                                        % (len(last_fail), last_fail[-1][:80]))
    elif last_ok:
        _send_ok, _send_detail = True, "最近 200 行里有 %d 条成功发送记录" % last_ok
    else:
        # ⛔ 2026-09-21 修（网友 v0921-0922 报「消息发不出去」而本检验器给了 ✅「通过」）：
        #   "日志里没有发送记录"＝**没测到**，不是"通过"（刚重启 / 日志轮转后这里本来就是空的）。
        #   判据只能拿它当"未知"，让用户知道**这一格没给他任何保证**。
        _send_ok, _send_detail = None, ("**没测到**：日志最近 200 行里没有任何发送记录"
                                       "（刚重启/还没发过）—— 这既不算通过也不算失败；"
                                       "在群里回它一句，再点一次这个检验器就能测到")
    checks.append(_check("最近的发送记录里没有失败", _send_ok, _send_detail))
    # ⛔ 2026-09-21 加（V-R9-12）：**第三份物证**＝按天落盘的会话档案 `data\sessions\*.jsonl` 里的
    #   `status`（`noreply_send_failed`＝"这一轮调过发送工具、却一条都没发出去"）。日志会轮转、
    #   进程内台账会随重启清空，而这份是落档的 ⇒ 三个来源互相独立，缺一个还有别的。
    try:
        _sd = _p("data", "sessions")
        _sess = sorted(f for f in (os.listdir(_sd) if os.path.isdir(_sd) else [])
                       if f.endswith(".jsonl"))[-3:]
        if not _sess:
            checks.append(_check("最近几轮里没有『调过发送却一条都没发出去』的记录", None,
                                 "还没有会话档案（刚装/刚重启）⇒ **没测到**"))
        else:
            _nfail = sum(_count((_tail2(os.path.join(_sd, _fn), 400) or []), "noreply_send_failed")
                         for _fn in _sess)
            # ⛔ 2026-09-21 修（第十轮 **V-R10-8**）：这里原来用 `_tail` —— **读不到 ⇒ 空表 ⇒
            #   `_nfail=0` ⇒ 判 ✅**（审计用真 `icacls /deny` 造出"读不到、但文件里真有两行失败"的现场，
            #   报告照样写「✅ 最近几轮里没有这条记录」）。⇒ 改 `_tail2`：读不到就是**没测到**。
            _sess_unread = any(_tail2(os.path.join(_sd, _fn), 400) is None for _fn in _sess)
            checks.append(_check("最近几轮里没有『调过发送却一条都没发出去』的记录",
                                 None if _sess_unread else (_nfail == 0),
                                 ("**会话档案读不到**（权限/占用）⇒ 这一格**没测到**"
                                  "（不是「没有失败记录」）") if _sess_unread else
                                 (("最近 %d 份会话档案里有 %d 条 `noreply_send_failed`"
                                   "（＝那几轮调过发送、却一条都没发出去）" % (len(_sess), _nfail))
                                  if _nfail else
                                  "最近 %d 份会话档案里没有这条状态" % len(_sess))))
    except Exception as _e_sess:
        checks.append(_check("最近几轮里没有『调过发送却一条都没发出去』的记录", None,
                             "读不到会话档案（不影响其它判断）：%s" % str(_e_sess)[:40]))
    # ⛔ 2026-09-21 加（第十轮 **V-R10-12**）：台账**写不进磁盘**时必须自己说出来 ——
    #   否则上面这几格的"最近没有失败记录"读的是**旧台账**，界面依旧一片 ✅
    #   （第九轮那个假绿就是顺着这条路回来的）。写失败会在 `wechat._note_ledger_write_fail`
    #   留痕、之后任何一次写成功都会清掉 ⇒ 这里读到的"有错"就是**刚刚真的写不进去**。
    try:
        from . import wechat as _wx4
        _lwe = _wx4.ledger_write_error()
    except Exception as _e_lwe:
        _lwe = {"err": "读不到台账状态：%s" % str(_e_lwe)[:40], "n": 0}
    _lwe_ok = not _lwe.get("err")
    checks.append(_check("判定台账能落盘（写不进去时上面几格只能看旧记录）",
                         _lwe_ok,
                         ("台账最近一次写盘是成功的（%s）"
                          % (str(_lwe.get("path") or "") or "data\\message_ledger.jsonl")) if _lwe_ok else
                         ("台账已连续写失败 %d 次（%s）⇒ 上面「没有失败记录」很可能只是"
                          "**旧台账**：先看磁盘空间与 `data\\` 权限，重启后这一格才会更新"
                          % (int(_lwe.get("n") or 0), str(_lwe.get("err"))[:120]))))
    # ⛔ 2026-09-21 加（同一个反馈的第二半）：真失败记在**台账**里
    #   （`wechat.note_switch_fail`，与「它不回复」那一格同源，**已落盘** ⇒ 重启也看得见）。
    try:
        from . import wechat as _wx3
        _sf2 = _wx3.recent_switch_fails(5)
    except Exception as _e_sf2:
        _sf2 = None
    if _sf2 is None:
        # 读不到台账 ≠ 没有失败（V-R9-13 的同一条口径）
        checks.append(_check("最近没有『确认不了目标会话 ⇒ 发不出去』的记录", None,
                             "读不到这份台账（不影响其它判断）：%s" % str(_e_sf2)[:40]))
    else:
        _last2 = _sf2[-1] if _sf2 else {}
        checks.append(_check("最近没有『确认不了目标会话 ⇒ 发不出去』的记录", not _sf2,
                             ("已有 %d 次没能把回复发出去（最后一条 %s · %s：%s）⇒ 消息读得到、"
                              "只是发不出去：先把目标会话在微信里点开再重试（最后一条里若写着"
                              "「最小化」，把微信从任务栏点出来即可）。"
                              % (len(_sf2), _last2.get("t", "?"), _last2.get("where", "?"),
                                 str(_last2.get("why", ""))[:150]))
                             if _sf2 else "台账里没有『确认不了目标会话』的失败记录"))
    # 内部故障话术拦截（拦得对，但用户要知道它拦了什么）
    try:
        from . import sender as _s
        og = _s.outbound_gate_status() or {}
        n = int(og.get("blocked") or og.get("count") or 0)
        checks.append(_check("出站闸门没有误拦正常回复", None,
                             "说明（不是判据）：已拦下 %d 条内部故障话术（只留本机日志，不进群）" % n,
                             info=True))
    except Exception:
        pass
    ok, verdict, action = _verdict(checks, "发送链各环节都正常：暂停关、版本门放行、投递档可用、"
                                           "主窗在、消息库可读，最近的发送记录里没有失败"
                                           "（该格若为「没测到」＝这一项没给保证，别当承诺成立）",
                                   {"没有处于暂停": "点控制台的「继续」",
                                    "版本门不拦发送": "控制台「微信」面板看版本门横幅",
                                    "投递档可用（不是只走真鼠标被闸住）": "把 input.backend 选回投递档",
                                    "微信主窗找得到": "把微信窗口从托盘里点出来（Ctrl+Alt+W）",
                                    "消息库读得到": "控制台「微信」面板跑一次「接微信」逐步检查",
                                    "最近的发送记录里没有失败": "把日志尾部那份文件一起反馈（控制台「反馈」可带附件）",
                                    "最近没有『确认不了目标会话 ⇒ 发不出去』的记录":
                                        "把目标会话在微信里点开再重试；最后一条若写着「最小化」，"
                                        "把微信从任务栏点出来（或打开「最小化时自己还原」）"})
    return _finish("send_blocked", "消息发不出去 / 卡在未通过", "消息发不出去、聊天记录生成了但发不出、"
                                                              "卡在「未通过 会话投递失败」", ok, verdict, action, checks)


# ── ② 它回自己 / 把我认成它 ─────────────────────────────────────────────────
def v_self_echo() -> dict:
    c = _cfg()
    checks = []
    ident = _read_json(_p("data", "self_identity.json"), {})
    sid = str(ident.get("wxid") or "")
    # ⛔ 2026-09-21（第五轮回执 **V-R5B-5**）：坏身份（活体里是 `{"wxid":"3"}`）以前只在 wechat 的
    #   两个读点净化，检验器这里**照样判「✅ 已认识」** ⇒ 用户点"它回自己"看到的是"通过"。
    #   ⇒ 这里也走**同一个** `wechat._valid_self_id`（唯一实现），不合法就按"没测到"记账并说清原因。
    _sid_bad = ""
    if sid:
        try:
            from .wechat import _valid_self_id as _vsid
            if not _vsid(sid):
                _sid_bad, sid = sid, ""
        except Exception:
            pass
    checks.append(_check("认识自己（self_wxid）或有替代证据",
                         True if sid else None,
                         ("已认识：来源=%s" % ident.get("from")) if sid else
                         ("**学到的身份不合法（%r）**：那不是账号形状（纯数字/太短）⇒ 判定为「没测到」；"
                          "它会在下次发送成功回读时被重学（也可删 data/self_identity.json 后重启）"
                          % _sid_bad if _sid_bad else
                          "**没认出自己**（不影响：判自己还靠下面三档，所以这里记「没测到」而不是判坏）")))
    local = _read_json(_p("data", "self_local_ids.json"), {})
    # 台账格式（2026-09-19 起带账号）：{"acct": "wxid_…", "rows": {chat: [[lid,ct,ts],…]}}；
    # 老格式就是 {chat: […] }。两种都读得出来，别让检验器因为格式升级而误报"台账是空的"。
    _rows = local.get("rows") if isinstance(local.get("rows"), dict) else (local or {})
    n_local = sum(len(v or []) for v in _rows.values() if isinstance(v, list))
    _acct0 = str((local or {}).get("acct") or "") if isinstance(local, dict) else ""
    checks.append(_check("自家消息行号表在工作", n_local > 0,
                         "已登记 %d 条（发送成功回读时记的，判自己最硬的一档）%s"
                         % (n_local, ("，账号 %s" % _acct0) if _acct0 else "")))
    try:
        ew = int((c.get("wechat") or {}).get("echo_window_s") or 0) or 120
    except Exception:
        ew = 120
    checks.append(_check("文本回声窗开着", ew > 0, "回声窗 %d 秒" % ew))
    led = _tail(_p("data", "message_ledger.jsonl"), 400)
    keep = _count(led, '"keep": true')
    self_w = _count(led, '"self_wxid_hit": true')
    local_hit = _count(led, '"self_local_hit": true')
    echo = _count(led, '"echo_hit": true')
    # ⛔ V-R4-11：这一格原来是 `(self_w + local_hit + echo) >= 0` —— **恒真**（计数不可能为负）。
    #   台账空的时候（刚装/还没发过）也判不了，那属于"没测到"，不是"通过"。
    _ev_n = self_w + local_hit + echo
    checks.append(_check("台账里能看到「判为自己」的证据",
                         None if not led else (_ev_n > 0),
                         "最近 %d 条：喂给模型 %d 条 · self_wxid 命中 %d · **自家行号命中 %d** · 回声命中 %d%s"
                         % (len(led), keep, self_w, local_hit, echo,
                            "" if led else "（台账还是空的 ⇒ 没测到）")))
    _bad_tail = _tail2(_p("logs", "persona_morph.log"), 400)
    bad_reply = _count(_bad_tail or [], "回自己")
    checks.append(_check("近期没有『回自己』的记录",
                         None if _bad_tail is None else (bad_reply == 0),
                         "日志读不到 ⇒ **没测到**（不是「没有记录」）" if _bad_tail is None
                         else ("日志里出现 %d 次相关记录" % bad_reply)))
    ok, verdict, action = _verdict(checks, "判自己四档齐备：self_wxid/自家行号/回声窗/昵称，台账里也在正常命中",
                                   {"自家消息行号表在工作": "先成功发一条消息，行号表就会开始记（或看下载/权限是否挡住 data 目录写入）"})
    return _finish("self_echo", "它回自己 / 把我认成它", "机器人回自己刚发的消息、把我发的话当成别人说的",
                   ok, verdict, action, checks)


# ── ③ 它不回复 ────────────────────────────────────────────────────────────
def v_no_reply() -> dict:
    c = _cfg()
    checks = []
    paused = os.path.exists(_p("data", "paused.flag"))
    checks.append(_check("没有处于暂停", not paused, "暂停标志：%s" % ("有" if paused else "没有")))
    # ── 账号这一维（2026-09-19 加，网友反馈：「切换微信号使用后…只有前几句话会正常回复，
    #    后面不再回复」）：多账号机器上"我们在读哪个号"是**看不见的第一杀手**——读到旧号时
    #    新消息一条都进不来，而暂停/水位/key 全都是好的，用户只能报"它不回了"。
    try:
        from . import wechat_dir as _wd_v
        # ⛔ 2026-09-21 修（第九轮 **V-R9-11** · P2）：`status()` **不带 `how`** 时拿不到账号这一维
        #   （`account` / `account_names` / `account_live` / `dir` 全是空）—— 而 `_acc_ok` 在
        #   "没有账号列表"时判 **True** ⇒ **假绿**（报告里写着"在读账号 没认出来"、判决却是 ✅）；
        #   下面那段"每个号的活跃证据"也因为 `_par` 恒空而**从来没跑过**（死代码）。
        #   ⇒ 现在：优先用**运行中实例**那份 `_db_how`（`webui` 每次跑检验器前会喂进来，
        #     见 `set_runtime_how`）；拿不到就**如实判「没测到」**，不再给假 ✅。
        _how = dict(_RUNTIME_HOW or {})
        _wv = _wd_v.status(how=_how) if _how else _wd_v.status()
        _acc = str(_wv.get("account") or (_how.get("account") or ""))
        _ns = list(_wv.get("account_names") or (_how.get("account_names") or []))
        _live = _wv.get("account_live")
        if _live is None and _acc:
            _als = list(_how.get("accounts_live") or [])
            _live = (_acc in _als) if _als else None
        _acc_ok = (not _ns) or (len(_ns) < 2) or (_live is not False)
        _acc_note = ("在读账号 %s（%s）" % (_acc or "没认出来",
                                          "库正在被写" if _live else
                                          ("**没在动**" if _live is False else "拿不到写入证据")))
        if not _acc:
            _acc_ok = None            # **没测到**：不装"这个号没问题"
            _acc_note = ("拿不到运行中的实例（机器人没在跑 / 旧版本）⇒ 这一格**没测到**："
                         "它本来用来回答「我读的是不是正在写的那个号」，没有实例就没有铁证。"
                         "跑着机器人的话点一次「接微信」再点这个检验器")
        if len(_ns) > 1:
            _acc_note += "；这台机器上有 %d 个账号" % len(_ns)
            # ⭐ 2026-09-19 加（网友反馈：「**大号能连、小号连接不上**」）：把**每个号的活跃证据
            #   并排印出来**，并直接判"我在读的那个号是不是正在被写的那个"——多账号机器上
            #   这是最难自查的一条（读到不在写的号时，新消息一条都进不来，而其它检查全绿）。
            try:
                _par = str(_how.get("dir") or _wv.get("dir") or _wv.get("effective") or "")
                _alist = _wd_v.accounts(_par) if _par else []
            except Exception:
                _alist = []
            _now = time.time()
            _rows, _live_names = [], []
            for _a in (_alist or [])[:6]:
                _nm = str(_a.get("name") or "?")
                _age = None
                try:
                    if _a.get("live"):
                        _age = int(_now - float(_a["live"]))
                except Exception:
                    _age = None
                # ⛔ V-R5A-3/U3：走统一助手（**未来时间**不算"刚写过"）
                if _fresh_age(_age, 0, 300) is True:
                    _live_names.append(_nm)
                    _st = "库正在被写（%ds 前）" % _age
                elif _age is not None and _age < 0:
                    _st = "**时间戳在未来**（%ds）⇒ 这个读数不可信（时钟/文件被改过？）" % _age
                elif _age is not None:
                    _st = "**没在动**（%s 前写过）" % (("%d 分钟" % (_age // 60)) if _age >= 90 else ("%ds" % _age))
                else:
                    _st = "拿不到写入证据"
                _rows.append("%s（%s）%s" % (_nm, _st, "←**我现在读的是它**" if _nm == _acc else ""))
            if _rows:
                _acc_note += "：" + "；".join(_rows)
            if _live_names and _acc and (_acc not in _live_names):
                _acc_ok = False
                _acc_note += (" ⇒ **现在在写的是 %s，而我在读 %s**：这正是「大号能连、小号连不上」的错法（"
                              "读不在写的那个号 ⇒ 新消息一条都进不来）。新版会在 15 秒内自己跟着切过去；"
                              "若一直不切，检查控制台「微信」面板的**数据库目录**是不是填到了某个账号那一层"
                              "（那样等于钉死那个号，请填它的**上一级**）"
                              % ("、".join(_live_names), _acc))
        if not _acc_ok:
            _acc_note += " ⇒ **微信像是切号了，而我们还读着旧号**：新版 15 秒内会自动跟着切，" \
                         "旧版本重启一次机器人即可"
        checks.append(_check("读的是**正在用的那个微信号**", _acc_ok, _acc_note))
    except Exception as e:
        checks.append(_check("读的是**正在用的那个微信号**", None,
                             "读不到账号信息（不影响其它判断）：%s" % str(e)[:40]))
    # ⭐ 2026-09-19 加（网友反馈「**大号能连、小号连接不上**」的行业口径：库目录 vs 选中的账号）：
    #    「数据库目录」填到**账号层**＝把某个号钉死（`wechat_dir.pick_account` 的 pinned 那条），
    #    切号之后它照样读那一个号 ⇒ 症状正是"另一个号连不上"。**与"现在读的是哪个号"分开判**：
    #    哪怕此刻读的号很新鲜，只要目录填到了账号层，下一个号一定会出问题。
    try:
        from . import wechat_dir as _wd_h
        _cfgd = str(_wd_h.configured_path() or "")
        _hint = _wd_h.account_hint(_cfgd) if _cfgd else ""
        checks.append(_check("「数据库目录」没有填到账号层", not _hint,
                             _hint or ("填的是账号目录的上一级，切号会自动跟随" if _cfgd
                                       else "没填自定义目录，按自动检测走")))
    except Exception as _e2:
        checks.append(_check("「数据库目录」没有填到账号层", None,
                             "判不了（不影响其它判断）：%s" % str(_e2)[:40]))
    # ⚠️ 2026-09-19 修：原来读 `api.key`——**配置里根本没有这个字段**（真字段是 `api.api_key`，
    # 且运行时会先看 `providers[].api_key`）⇒ 所有用户的检验器都恒报「api.key 没填」的假卡点。
    # 现在与运行时同源：走 `config.resolve_api_key()`（它在 llm.py 里就是发请求前的那一步）。
    try:
        from .config import resolve_api_key as _resolve_api_key
        key = str(_resolve_api_key(c) or "").strip()
        _key_src = "按运行时同源解析"
    except Exception as _e:
        _api_d = c.get("api") or {}
        key = str(_api_d.get("api_key") or _api_d.get("key") or "").strip()
        _key_src = "解析函数不可用，退回直读 api_key（%s）" % str(_e)[:24]
    checks.append(_check("模型 key 已填", bool(key), "模型 key %s（%s）" % ("已填" if key else "**没填**", _key_src)))
    try:
        _wl = [str(x) for x in ((c.get("wechat") or {}).get("group_name_white_list") or [])]
    except Exception:
        _wl = []
    # ⛔ V-R4-11：原来是硬编码 `True`（恒真）。白名单**留空＝监听所有群**是合法配置（判过）；
    #   填了名字能不能真对上，由下面「监听水位有推进」那一格定论 ⇒ 这里只给"没测到 + 说明"。
    checks.append(_check("勾了监听目标群", (True if not _wl else None),
                         ("白名单 %d 个：%s（**勾选存的是 wxid，群名要与微信里完全一致**——"
                          "老配置写的是群名，名字差一个字或**有同名群**都会匹配不上 ⇒ 监听不到；"
                          "能不能对上由下面「监听水位有推进」那格定论）"
                          % (len(_wl), "、".join(_wl[:5]))) if _wl else "白名单留空 ＝ 监听所有群"))
    wm = _read_json(_p("data", "listener_watermark.json"), {})
    # ⛔ 2026-09-20 修（网友 v0920-0824 的报告里这一格被判 ✅：「水位条目 2 个，**最近：0**」）：
    #   只看到"有条目"就判过，是**太弱**的判据 —— 水位停在 0 说明**监听从来没读到过目标群的消息**
    #   （最常见：白名单群名与微信里不一致 / 那个群不在当前登录的号里 / 消息库读不出来）。
    #   它比"最近有过一轮响应"更靠前，所以必须在这里就把卡点拦住，别让判决指到后面去。
    try:
        _wvals = [int(v) for v in (wm or {}).values()]
    except Exception:
        _wvals = []
    _wmax = max(_wvals) if _wvals else 0
    _wm_detail = ("水位条目 %d 个，最高序号 %d" % (len(wm), _wmax)) if wm else "还没有水位记录（监听没在跑）"
    if wm and _wmax <= 0:
        _wm_detail += ("：**从没读到过目标群的消息** ⇒ 依次查 ①白名单群名与微信里完全一致 "
                       "②那个群在当前登录的号里 ③「点击测试」里「微信·消息库可读」那一格")
    checks.append(_check("监听水位有推进（说明真的读到了目标群的消息）", bool(wm) and _wmax > 0, _wm_detail))
    sess = sorted([f for f in (os.listdir(_p("data", "sessions")) if os.path.isdir(_p("data", "sessions")) else [])
                   if f.endswith(".jsonl")])
    last_age = None
    if sess:
        try:
            lines = _tail(_p("data", "sessions", sess[-1]), 1)
            d = json.loads(lines[0]) if lines else {}
            ts = str(d.get("ts") or "")
            if ts:
                t0 = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
                last_age = (time.time() - t0) / 60.0
        except Exception:
            pass
    checks.append(_check("最近有过一轮响应", _fresh_age(last_age * 60 if last_age is not None else None,
                                                        0, 24 * 3600),
                         ("最近一轮在 %.0f 分钟前（%s）" % (last_age, sess[-1])) if last_age is not None
                         else "今天的会话日志里没有可用时间戳"))
    # ⛔ 2026-09-21 加（网友 v0919 追加反馈③：「聊了一会儿之后就不回话了」，而运行日志只有三行
    #   checkpoint）：「消息进来了、回复却一条都发不出去」这一类过去只写 log.info ⇒ 用户对着界面
    #   完全看不出毛病在哪。台账见 `wechat.note_switch_fail`。
    try:
        from . import wechat as _wx2
        _sf = _wx2.recent_switch_fails(5)
    except Exception:
        _sf = []
    _last = _sf[-1] if _sf else {}
    checks.append(_check("最近没有『切不到会话 ⇒ 回复发不出去』的记录", not _sf,
                         ("本次运行已有 %d 次没能把回复发出去（最后一条 %s · %s：%s）⇒ 它**读得到消息、只是发不出去**："
                          "把目标会话在微信里点开，或让它在会话列表/搜索里能被认出来，再试一次"
                          % (len(_sf), _last.get("t", "?"), _last.get("where", "?"), str(_last.get("why", ""))[:90]))
                         if _sf else "本次运行到现在没有这类失败"))
    tier_ok, tier_note = True, ""
    if _CFG_ERR:
        # ⛔ 2026-09-21 修（第十轮 **G406**）：配置读不到时，档位是从**内置默认值**里读的 ⇒ 这一格
        #   给出的"档位正常"**不算结论**（审计变异实测：把"没测到 ⇒ None"改回"⇒ True"，判据照样全绿）。
        tier_ok, tier_note = None, "配置读不到（%s）⇒ 档位是按**内置默认值**判的，**这一格没测到**" % _CFG_ERR
    try:
        from . import prompt as _pr
        if _CFG_ERR:
            raise RuntimeError("__cfg_unreadable__")
        # ⚠️ 2026-09-19 修：原来读 `c["reply"]["tier"]`（配置里没有 `reply` 段 ⇒ 恒 None）。
        # 而且**光看全局档位不够**——真正生效的档位有四层覆盖，优先级从高到低：
        #   ①指令禁言（tier_control.json，强制降到 1 档，最长 24h）②峰谷映射表（按时间自动切档）
        #   ③每群独立档位（unified_tier=false + group_tier[群名]）④滑条（tier_mode!=fixed 时接管）
        #   ⇒ 只改全局档位却被上面任一层盖住，就是"我明明调了档位它还是不搭话"的真因。
        st = c.get("store") or {}
        cfg_tier = st.get("context_tier")
        _mode = str(st.get("tier_mode") or "fixed")
        _slider = st.get("context_slider_pos")
        _unified = st.get("unified_tier", True)
        _gtier = st.get("group_tier") or {}
        _sch2 = st.get("tier_schedule") or {}
        _parts = ["配置档位=%s" % cfg_tier]
        _over, _eff = [], None
        try:
            from . import tier_control as _tc2
            _live_mute = []
            for _k in list(((_tc2.load() or {}).get("muted") or {}).keys()):
                try:
                    _mi = _tc2.is_muted(_k)
                except Exception:
                    _mi = None
                if _mi:
                    _live_mute.append("%s（剩 %d 分钟%s）" % (
                        _k, int(_mi.get("left_min") or 0),
                        ("，%s 按的" % _mi.get("by")) if _mi.get("by") else ""))
            if _live_mute:
                _over.append("**临时禁言中**：%s ⇒ 该会话被强制降到 1 档" % "；".join(_live_mute))
                _eff = 1
            if _sch2.get("enabled"):
                try:
                    _mt = _tc2.scheduled_tier(cfg=c)
                except Exception:
                    _mt = None
                if _mt:
                    _over.append("**峰谷映射已开**：当前时段命中档位 %s（tier=0 表示完全不回应）" % _mt.get("tier"))
                    if _eff is None:
                        _eff = _mt.get("tier")
                else:
                    _over.append("峰谷映射已开，但当前时段没命中任何窗口")
        except Exception as _e2:
            _parts.append("禁言/时段状态读不到（%s）" % str(_e2)[:24])
        if _mode != "fixed" and _slider is not None:
            _over.append("**滑条接管**：模式=%s 且滑条位置=%s（此时全局档位不参与）" % (_mode, _slider))
        if not _unified:
            _over.append("**每群独立档位已开**：%d 个群有单独设置%s（全局档位对它们无效）"
                         % (len(_gtier), ("：" + "、".join(list(_gtier)[:4])) if _gtier else ""))
        if _eff is None:
            _eff = cfg_tier
        try:
            _eff_n = int(float(_eff)) if _eff is not None else None
        except Exception:
            _eff_n = None
        _note = " · ".join(_parts + (["⚠️ " + "；".join(_over)] if _over else []))
        if _eff_n == 1:
            tier_ok, tier_note = False, (_note + " ⇒ **生效档位是 1（只回艾特），群里说话它不会搭话**；"
                                                 "要它主动说话就把档位调到 2（+关键词）或 3（+随机），"
                                                 "并确认上面没有覆盖项在生效")
        else:
            tier_ok, tier_note = True, (_note + "（1=只回艾特 / 2=+关键词 / 3=+随机 / 4=全读）")
    except Exception:
        # ⛔ 2026-09-21 修（V-R9-14）：**读不到档位 ⇒ 没测到**，不许留 `tier_ok=True`（那会判 ✅）。
        tier_ok = None
        tier_note = tier_note or "读不到回复档位（配置读不到）⇒ 这一格**没测到**"
    checks.append(_check("回复档位不是『只回艾特』却指望它搭话", tier_ok, tier_note))
    # ⛔ 2026-09-21 加（B站评论「**艾特它 它不会回复**」）：把"群里最近 @ 的那个名字"摆到报告里 ——
    #   微信 @ 用的是**群昵称**，可能既不是配置里的机器人昵称、也不是库里的账号昵称 ⇒
    #   `is_at_me` 认不出"这是 @ 我"（档位 2/3 下就等于**没被唤醒**，用户看到的就是"艾特它也不回"）。
    #   这是**说明格（None）**：不判好坏（那个 @ 也可能是 @ 别人的），只让报告带上"它看到的是谁的名字"，
    #   名字对不上时给出可执行的一步。留痕见 `persona_morph.py` 的 `_tt.note(..., at=, known=)`。
    try:
        from . import thought_trace as _tt2
        _at_last, _known_last = "", ""
        for _it in reversed(_tt2.recent("", 50)):
            if str(_it.get("at") or ""):
                _at_last = str(_it.get("at"))
                _known_last = str(_it.get("known") or "")
                break
        if _at_last:
            _hit = any(_at_last == _x for _x in _known_last.split("、") if _x)
            checks.append(_check("群里最近 @ 的名字它认得", True if _hit else None,
                                 ("最近一次看到的 @ 名字是「%s」，与它以为的名字一致（%s）"
                                  % (_at_last, _known_last)) if _hit else
                                 ("最近一次看到的 @ 名字是「**%s**」，而它以为自己的名字是「%s」 ⇒ "
                                  "**名字不一致时 @ 唤不醒它**：到微信里把这个号的群昵称改成与「机器人昵称」"
                                  "一致，或反过来把「%s」填进机器人昵称。"
                                  % (_at_last, _known_last or "（这次没记到）", _at_last)),
                                 info=True))
        else:
            checks.append(_check("群里最近 @ 的名字它认得", None,
                                 "运行记录里还没有「有人 @ 它」的样本 ⇒ **没测到**"
                                 "（这一格什么都不保证）；让人在群里 @ 它一次再看",
                                 info=True))
    except Exception as _e_at:
        checks.append(_check("群里最近 @ 的名字它认得", None,
                             "读不到运行记录（不影响其它判断）：%s" % str(_e_at)[:40],
                             info=True))
    ok, verdict, action = _verdict(checks, "监听在跑、key 已填、最近有响应记录 ⇒ 不回复多半是**档位/触发条件**"
                                           "（档位 1 只回艾特、档位 2 要关键词）",
                                   {"没有处于暂停": "点「继续」", "模型 key 已填": "控制台「模型」面板填 key",
                                    "读的是**正在用的那个微信号**":
                                        "重启一次机器人（旧版本）；新版会在 15 秒内自动跟着切过去",
                                    "监听水位有推进（说明真的读到了目标群的消息）":
                                        "点「一键启动」并看「微信」面板的逐步检查；"
                                        "重点核白名单里的群名与微信里是否**完全一致**"})
    return _finish("no_reply", "它不回复", "群里说话它不理、只有艾特才有反应、整天没动静",
                   ok, verdict, action, checks)


# ── ④ 表情包看不到 ────────────────────────────────────────────────────────
def v_emoji_blank() -> dict:
    c = _cfg()
    checks = []
    vis = bool((c.get("api") or {}).get("vision", True))
    checks.append(_check("看图（视觉）是开着的", vis, "api.vision=%s" % vis))
    files, key_ok, note = 0, False, ""
    try:
        from . import emoticon as em
        files = len(em.any_sticker_files(limit=200))
        kf = _read_json(_p("data", "emoticon_key.json"), {})
        key = em.load_cached_key(em.account_wxid())
        key_ok = bool(key) and em.verify_key(key)
        note = "本地表情文件 %d 个 · key %s（seed=%s）" % (
            files, "已缓存且校验通过" if key_ok else "**没有可用 key**", kf.get("seed") or "-")
    except Exception as e:
        note = "表情模块不可用：%s" % str(e)[:50]
    # ⛔ 2026-09-21 修（第九轮 **V-R9-13**）：原来是 `(files > 0) and (key_ok or files > 0)`
    #   —— **等价于 `files > 0`**（恒真伪装）。现场后果：key 明明没有，明细写"**没有可用 key**"、
    #   总判决却写"key 可用"，自相矛盾。⇒ 两个条件都要真成立才算过。
    checks.append(_check("表情能离线解出原图", (files > 0) and key_ok, note))
    logs = _tail2(_p("logs", "persona_morph.log"), 400)
    shot_fail = _count(logs or [], "表情截图没取到") + _count(logs or [], "表情截图跳过")
    checks.append(_check("最近没有『只能截图又截不到』的记录",
                         None if logs is None else (shot_fail == 0),
                         "日志读不到 ⇒ **没测到**（不是「没有记录」）" if logs is None
                         else ("相关记录 %d 条（新版本会先离线解原图，解不出才截图）" % shot_fail)))
    ok, verdict, action = _verdict(checks, "表情模块正常：视觉开关在、本地有表情文件、key 可用",
                                   {"看图（视觉）是开着的": "控制台「模型」面板勾上「视觉(看图)」",
                                    "表情能离线解出原图": "先让群里发一个表情（要本机有那个表情文件），再点这个检验器"})
    return _finish("emoji_blank", "表情包看不到", "它说看不到表情、只能读到 [表情]",
                   ok, verdict, action, checks)


# ── ⑤ 抢窗口 / 动鼠标 ─────────────────────────────────────────────────────
def v_fg_disturb() -> dict:
    c = _cfg()
    checks = []
    bo = bool((c.get("wechat") or {}).get("background_only", True))
    ar = bool((c.get("input") or {}).get("allow_real_fallback", False))
    checks.append(_check("「只走后台」是开着的", bo, "wechat.background_only=%s" % bo))
    checks.append(_check("真鼠标兜底是关着的", not ar, "input.allow_real_fallback=%s" % ar))
    refused = {}
    try:
        from . import ui_adapt
        refused = ui_adapt.fg_refused() or {}
    except Exception:
        pass
    n = int(refused.get("count") or 0)
    # ⛔ V-R4-11：这两格原来是硬编码 `True`（恒真）⇒ 报告画 ✅「闸门在拦」，而"没拦过"也画 ✅。
    #   口径改成：**拦下过（n>0）才算有证据**；一次没拦 = **没测到**（不是承诺成立）。
    checks.append(_check("置前请求都被闸门拦住了（拦下过才算数）",
                         True if n > 0 else None,
                         "累计拦下 %d 次置前/置顶%s%s"
                         % (n, ("（最近原因：%s）" % str(refused.get("why"))[:60])
                            if refused.get("why") else "",
                            "" if n > 0 else " ⇒ 没拦下过 = 没测到（不能据此说闸门一定在工作）")))
    logs = _tail(_p("logs", "persona_morph.log"), 400)
    deny = _count(logs, "拒绝置前") + _count(logs, "拒绝置顶")
    checks.append(_check("最近日志里能看到闸门在工作", True if deny > 0 else None,
                         "近 400 行里 %d 条拒绝置前/置顶%s"
                         % (deny, "" if deny > 0 else " ⇒ 没有证据 ⇒ 没测到（不是通过）")))
    ok_audit = os.path.exists(_p("scripts", "fg_audit_selftest.py"))
    checks.append(_check("静态审计判据在（防止以后偷偷加置前）", ok_audit,
                         "scripts/fg_audit_selftest.py %s" % ("在" if ok_audit else "**不在**")))
    ok, verdict, action = _verdict(checks, "只走后台开着、真鼠标兜底关着、静态审计判据在位"
                                            "（闸门**有没有真的拦过**看上面那两格：拦下过才算证据）",
                                   {"「只走后台」是开着的": "控制台「微信」面板把「只走后台」打开",
                                    "真鼠标兜底是关着的": "把 input.allow_real_fallback 关掉"})
    return _finish("fg_disturb", "抢窗口 / 动我鼠标", "它把我的窗口顶到前台、抢我鼠标、打扰我打字",
                   ok, verdict, action, checks)


# ── ⑥ 更新不动 / 打不开 ───────────────────────────────────────────────────
def v_update_stuck() -> dict:
    checks = []
    st = _read_json(_p("data", "update_state.json"), {})
    # ⛔ V-R4-12c：这份快照是**上次点「检查更新」时**写的 ⇒ 旧快照**不能**当成"现在的更新结论"
    #   （尤其 `_write_state` 写失败时，用户看到的其实是更早那份）。⇒ 把写入时间摆出来当证据，
    #   新鲜度单独一格判：新鲜=true、旧=**没测到**（第三态，不是通过）。
    _age_min = None
    _age_txt = "读不到写入时间"
    try:
        _age_min = (time.time() - os.path.getmtime(_p("data", "update_state.json"))) / 60.0
        _age_txt = ("%.0f 分钟前写的" % _age_min) if _age_min < 1440 else ("%.1f 天前写的" % (_age_min / 1440.0))
    except Exception:
        pass
    checks.append(_check("更新状态文件可读", bool(st), "lastStatus=%s · version=%s · 快照是 %s"
                         % (st.get("lastStatus") or st.get("status") or "-", st.get("version") or "-", _age_txt)))
    _fresh = _fresh_age((_age_min * 60) if _age_min is not None else None, 0, 30 * 60)
    # ⛔ V-R5A-3：未来 mtime ⇒ `_fresh_age` 回 False（坏读数），detail 要写清是"未来"而不是"旧"。
    if _fresh is False and _age_min is not None and _age_min < 0:
        _age_txt = "写入时间在**未来**（%.0f 分钟）⇒ 这个快照不可信（时钟或文件被改过）" % _age_min
    checks.append(_check("这份快照是最近 30 分钟内写的（旧快照不算「现在的结论」）", _fresh,
                         "%s%s" % (_age_txt,
                                   "" if _fresh is True else
                                   " ⇒ 太旧了：控制台点一次「检查更新」再看这格（旧快照只能当历史）")))
    pid_txt = ""
    try:
        pid_txt = io.open(_p("data", "watchdog.pid"), encoding="utf-8", errors="replace").read()[:40]
    except Exception:
        pid_txt = ""
    pid = ""
    m = re.search(r"\d+", pid_txt or "")
    pid = m.group(0) if m else ""
    checks.append(_check("看门狗 pid 可解析", bool(pid), "watchdog.pid=%r ⇒ pid=%s" % (pid_txt.strip()[:20], pid or "读不出")))
    port = _port()
    checks.append(_check("控制台端口在听", _port_open(port), "127.0.0.1:%d %s" % (port, "可连" if _port_open(port) else "连不上")))
    url = ""
    try:
        url = io.open(_p("logs", "console.url"), encoding="utf-8", errors="replace").read().strip()
    except Exception:
        pass
    ok_url = bool(url) and (":%d" % port) in url
    checks.append(_check("console.url 与控制台端口一致", ok_url,
                         "%s（端口应为 %d）" % ((url[:60] + "…") if url else "没有这个文件", port)))
    checks.append(_check("更新完成标志（更新成功后会写）", None,
                         "%s" % ("data/update_done.flag 在（上次更新已自完成）"
                                 if os.path.exists(_p("data", "update_done.flag")) else "没有完成标志"),
                         info=True))
    ok, verdict, action = _verdict(checks, "更新链三件都在：状态文件、看门狗 pid、控制台端口与 console.url 对得上",
                                   {"看门狗 pid 可解析": "点「一键关闭」再「一键启动」（新版会自动清理残留 pid）",
                                    "控制台端口在听": "点「一键启动」，再打开控制台",
                                    "console.url 与控制台端口一致": "删掉 logs/console.url 后重新「一键启动」"})
    return _finish("update_stuck", "更新不动 / 更新完打不开", "点更新一直转圈、更新完窗口关了打不开、一键启动没窗口",
                   ok, verdict, action, checks)


# ── ⑦ 控制台打不开 ────────────────────────────────────────────────────────
def v_console_dead() -> dict:
    checks = []
    port = _port()
    open_now = _port_open(port)
    checks.append(_check("端口在听", open_now, "127.0.0.1:%d %s" % (port, "可连" if open_now else "没人听")))
    tok = str((( _cfg().get("server") or {}).get("token") or "")).strip()
    checks.append(_check("控制台口令已设置（空口令会被拒）", bool(tok),
                         "server.token %s（进控制台要用带 ?token= 的完整网址）" % ("已设置" if tok else "**是空的**")))
    url = ""
    try:
        url = io.open(_p("logs", "console.url"), encoding="utf-8", errors="replace").read().strip()
    except Exception:
        pass
    if url and "token=" in url:
        try:
            import urllib.request
            code = 0
            with urllib.request.urlopen(url, timeout=6) as r:
                code = getattr(r, "status", 200)
            checks.append(_check("用 console.url 能打开控制台", code == 200, "HTTP %s" % code))
        except Exception as e:
            checks.append(_check("用 console.url 能打开控制台", False, "打不开：%s" % str(e)[:60]))
    else:
        checks.append(_check("console.url 里有带口令的完整网址", False,
                             "%s" % ("文件里没有 token" if url else "没有 logs/console.url")))
    ok, verdict, action = _verdict(checks, "控制台正常：端口在听、口令已设、console.url 能打开",
                                   {"端口在听": "点「一键启动」把控制台起来",
                                    "控制台口令已设置（空口令会被拒）": "在 config.json 的 server.token 填一个口令",
                                    "console.url 里有带口令的完整网址": "重新「一键启动」生成 logs/console.url"})
    return _finish("console_dead", "控制台打不开", "控制台一屏 unauthorized / 无法访问此网站 / 地址打不开",
                   ok, verdict, action, checks)


# ── ⑧ 更新拉取不到（源探测）──────────────────────────────────────────────
def v_update_source() -> dict:
    """把每个更新源挨个探一遍，出"哪条通 / 延迟 / 哪条不通 / 错什么"的表。

    为什么单列（2026-09-18 作者另一台机器：「一直显示 timeout，拉取不到更新源，试几次都不行」）：
    这条链是**网络**问题，只有"在那台机器上实际探一遍"才能定性；用户点一下、把表粘给我，
    就不用再来回问"你那边能上网吗"。
    """
    checks = []
    urls = []
    try:
        from . import update_check as U
        urls = list(getattr(U, "DEFAULT_URLS", ()) or ())
    except Exception as e:                                     # noqa: BLE001
        checks.append(_check("能读到更新源清单", False, "读不到 update_check：%s" % str(e)[:50]))
    okn, bad = 0, []
    for u in urls:
        try:
            t0 = time.time()
            man, why = U.fetch(u, 8.0)
            dt = time.time() - t0
            if man:
                okn += 1
                checks.append(_check("源 %s" % U._short_url(u), bool(man),
                                     "通（%.1fs，远端版本 %s）"
                                     % (dt, (man.get("base") or {}).get("version") or "?")))
            else:
                bad.append(U._short_url(u))
                checks.append(_check("源 %s" % U._short_url(u), False,
                                     "不通（%.1fs）：%s" % (dt, str(why)[:70])))
        except Exception as e:                                 # noqa: BLE001
            bad.append(U._short_url(u))
            checks.append(_check("源 %s" % U._short_url(u), False, "异常：%s" % str(e)[:60]))
    try:
        st = U.state(_cfg(), timeout=8.0) or {}
        checks.append(_check("「检查更新」现在的结论", str(st.get("status")) in ("current", "newer"),
                             "status=%s · %s" % (st.get("status"),
                                                 str(st.get("why") or st.get("url") or "")[:70])))
    except Exception as e:                                     # noqa: BLE001
        checks.append(_check("「检查更新」现在的结论", False, "跑不动：%s" % str(e)[:60]))
    ok = okn > 0
    verdict = (("有 %d 条源能通（不通的：%s）⇒ 点「立即更新」就行"
                % (okn, "、".join(bad) if bad else "无")) if ok else
               "**所有源都拉不到** ⇒ 是那台机器的网络到不了这些地址（不是你的操作问题）")
    action = ("" if ok else "把上面这张表发我（我按你那边的实际情况加/换源）；"
                            "临时办法：换一个网络（比如手机热点）再点一次「立即更新」")
    return _finish("update_source", "更新拉取不到 / 一直 timeout",
                   "点更新一直转圈、提示拉取不到更新源、试几次都不行", ok, verdict, action, checks)


# ── 注册与渲染 ─────────────────────────────────────────────────────────────
# ── ⑨ 消息库读不到（作者 2026-09-21 点名要"能精准到哪一环"）────────────────────
def v_db_unreadable() -> dict:
    """症状：控制台写「消息库读不到 / 打不开消息库 / 找不到群聊」。**按链路走，一环一格**。

    为什么单独开这个入口（作者原话：「因为现在还是有些问题在反复出现，比如消息库读不到」＋
    「能不能精准地检验到到底是哪一环出了问题」）：库原来的 8 个检验器里**没有这个症状的入口** ——
    "读不到库"散在「消息发不出去」的一格与「它不回复」的水位格里，用户点哪个都只能看到半条链，
    于是**只能猜**。本检验器把链路摊平：①微信接没接上 → ②数据目录是怎么定的 → ③开库那一步的
    结果（`_cap`）→ ④读的是不是**正在写**的那个号 → ⑤目录有没有填到**账号层** →
    ⑥最硬的一格：库能不能真出会话（`list_message_chats`）。
    **每格只有一个来源**（同一事实不许两处各算一份——第十轮 V-R10-28 就是"同一事实两条路相反"）。
    """
    c = _cfg()
    checks = []
    # ① 有没有"运行中的实例"（这是整条链的起点：没实例 ⇒ 后面几格全是"没测到"，别装读到了）
    _how = dict(_RUNTIME_HOW or {})
    checks.append(_check("机器人接上了微信（有运行中的实例）", True if _how else None,
                         "运行中实例在读：%s" % (str(_how.get("dir") or "?")[:60] if _how else
                                            "拿不到运行中实例（机器人没在跑 / 旧版本）⇒ 这一格**没测到**")))
    # ② 数据目录是怎么定的（唯一来源：wechat_dir.status）
    try:
        from . import wechat_dir as _wd_d
        _wv = _wd_d.status(how=_how) if _how else _wd_d.status()
    except Exception as _e1:
        _wv = {}
        checks.append(_check("数据目录状态读得到", None,
                             "读不到（不影响其它判断）：%s" % str(_e1)[:40]))
    if _wv:
        _eff_raw = str(_wv.get("effective") or "")
        _eff = str(_wv.get("now") or _eff_raw or "")
        _src = str(_wv.get("source_text") or _wv.get("src") or "")
        _okd = bool(_wv.get("ok"))
        if not _how or not _eff_raw:
            # ⛔ 别把"机器人没在跑"说成"目录不对"（这正是本轮要治的归因矛盾）：
            #   没有运行中实例时，`effective` 是空的 ⇒ 这一格只能**没测到**。
            checks.append(_check("读的是**实际在用**的数据目录", None,
                                 "拿不到运行中实例 ⇒ 这一格**没测到**（按配置/自动检测它*打算*用：%s）"
                                 % (_eff or "还没定")))
        else:
            checks.append(_check("读的是**实际在用**的数据目录",
                                 True if _okd else False,
                                 "现在读：%s（%s）%s" % (_eff or "没认出来", _src or "来源未知",
                                                       ("；" + str(_wv.get("note") or "")) if _wv.get("note") else "")))
    else:
        # ⛔ 2026-09-21 修（第十一轮 **V-R11-6** · P2）：`status()` **返回空对象**时（不抛异常、
        #   只是什么都没读回来）原来这一环**整格消失** —— 报告里"少了一环"这件事本身看不见，
        #   用户看到的是一个"环数不确定"的清单。现在空对象也必须**留下一格**（如实标"没测到"）。
        checks.append(_check("读的是**实际在用**的数据目录", None,
                             "数据目录状态是**空对象**（拿不回任何读数：没有运行中实例 / 旧版本 / "
                             "status 内部提前返回）⇒ 这一格**没测到**（不是「目录没问题」）"))
    # ③ 开库那一步的结果（唯一来源：运行中实例的 `_cap`）
    _cap = dict(_RUNTIME_CAP or {})
    if _cap:
        _bad = ["%s：%s" % (k, str(v)[5:]) for k, v in _cap.items()
                if str(v).startswith("fail")]
        checks.append(_check("开库/读表这一步没有报错", not _bad,
                             "、".join(_bad)[:180] if _bad else
                             "各张表都读到了（%s）" % "、".join(sorted(_cap.keys())[:6])))
    else:
        checks.append(_check("开库/读表这一步没有报错", None,
                             "拿不到运行中实例的读数（机器人没在跑 / 旧版本）⇒ 这一格**没测到**"))
    # ④ 读的是不是"正在被写"的那个号（唯一来源：同一份 status 的账号维）
    _acc = str(_wv.get("account") or (_how.get("account") or ""))
    _live = _wv.get("account_live")
    if _live is None and _acc:
        _als = list(_how.get("accounts_live") or [])
        _live = (_acc in _als) if _als else None
    _ns = list(_wv.get("account_names") or (_how.get("account_names") or []))
    if not _acc:
        checks.append(_check("读的是**正在写的那个号**", None,
                             "拿不到账号维（没有运行中实例）⇒ 这一格**没测到**"))
    else:
        # ⛔ 2026-09-21 修（第十一轮 **V-R11-4** · P2）：**多账号 + 拿不到写入证据 ⇒ 没测到** ——
        #   老写法 `_aok = True if (len(_ns) < 2 or _live is not False) else False` 在这一档给 ✅
        #   （detail 自己都写着"拿不到写入证据"）。单账号机器无害（就一个号，不需要比），
        #   但**多账号机器**上"在读的到底是不是正在写的那个号"正是原始症状，不许拿 ✅ 冒充。
        if len(_ns) > 1 and _live is None:
            _aok = None
        else:
            _aok = True if (len(_ns) < 2 or _live is not False) else False
        checks.append(_check("读的是**正在写的那个号**", _aok,
                             "在读 %s%s%s" % (_acc,
                                              ("；这台机器有 %d 个账号" % len(_ns)) if len(_ns) > 1 else "",
                                              "" if _live else
                                              ("（**没在动** ⇒ 很可能读的是另一个号）" if _live is False
                                               else "（拿不到写入证据 ⇒ 多账号下**这一格没测到**）"))))
    # ⑤ 目录有没有填到账号层（唯一来源：wechat_dir.account_hint）
    try:
        from . import wechat_dir as _wd_h2
        _cfgd = str(_wd_h2.configured_path() or "")
        _hint = _wd_h2.account_hint(_cfgd) if _cfgd else ""
        checks.append(_check("「数据库目录」没有填到账号层", not _hint,
                             _hint or ("填的是账号目录的上一级，切号会自动跟随" if _cfgd
                                       else "没填自定义目录，按自动检测走")))
    except Exception as _e5:
        checks.append(_check("「数据库目录」没有填到账号层", None,
                             "判不了（不影响其它判断）：%s" % str(_e5)[:40]))
    # ⑥ 最硬的一格：库能不能真出会话（唯一来源：驱动库自己）
    try:
        from wechatauto import WeChatDB
        _db = WeChatDB()
        _chats = _db.list_message_chats() or []
        checks.append(_check("消息库读得到会话（最硬的一格）", bool(_chats),
                             "会话 %d 个 · 目录 %s" % (len(_chats),
                                                     os.path.basename(str(getattr(_db, "account_dir", "")) or "-"))))
    except Exception as _e6:
        checks.append(_check("消息库读得到会话（最硬的一格）", False,
                             "**打开消息库就失败了**：%s【%s】⇒ 把这一行连同上面的目录/账号一起发我"
                             % (str(_e6)[:90], type(_e6).__name__)))
    ok, verdict, action = _verdict(checks,
                                   "这一份走下来：微信在、目录对、号对、库里能出会话 ⇒ 现在读得到",
                                   {"数据目录状态读得到": "重启一次机器人，让「接微信」重新走一遍",
                                    "读的是**实际在用**的数据目录": "控制台「微信」面板看「数据库目录」那行怎么写的",
                                    "开库/读表这一步没有报错": "点控制台的「接微信」，按它的逐步检查再走一遍",
                                    "读的是**正在写的那个号**":
                                        "把「数据库目录」改成账号目录的**上一级**（别钉到某个号）；"
                                        "机器人会在 15 秒内自己跟着切",
                                    "「数据库目录」没有填到账号层": "改填账号目录的上一级",
                                    "消息库读得到会话（最硬的一格）":
                                        "把这段报告发我（附日志尾部）—— 打开库失败的原因就在上面那一行里"})
    return _finish("db_unreadable", "消息库读不到 / 打不开消息库",
                   "控制台写「消息库读不到 / 打不开消息库 / 找不到群聊」，读不到聊天记录",
                   ok, verdict, action, checks)


VERIFIERS = {
    "send_blocked": ("消息发不出去", v_send_blocked),
    "db_unreadable": ("消息库读不到", v_db_unreadable),
    "self_echo": ("它回自己 / 把我认成它", v_self_echo),
    "no_reply": ("它不回复", v_no_reply),
    "emoji_blank": ("表情包看不到", v_emoji_blank),
    "fg_disturb": ("抢窗口 / 动我鼠标", v_fg_disturb),
    "update_stuck": ("更新不动 / 打不开", v_update_stuck),
    "update_source": ("更新拉取不到 / timeout", v_update_source),
    "console_dead": ("控制台打不开", v_console_dead),
}


def _finish(vid, name, symptom, ok, verdict, action, checks) -> dict:
    # ⛔ 2026-09-21 修（第十轮 **V-R10-10**）：**说明格不算"没测到"** —— 它们本来就不是判据，
    #   算进去会让 `v_send_blocked` / `v_update_stuck` **恒 ◐、永不给 ✅**（审计场景 E 实测）。
    _unk = [c for c in checks if c.get("ok") is None and not c.get("info")]
    _n_unk = len(_unk)
    # ⛔ V-R5A-4：`ok=None`（全项没测到）要**如实画成「○ 没测到」**，不许落进 `if ok` 的假值分支
    #   画成 ❌（那是"证据说不是"），也不许画 ✅（那是"承诺成立"）。
    # ⛔ 2026-09-21 加（第九轮 **V-R9-14**）：**有没测到的项时，报告头也不许打 ✅** ——
    #   现场是「一格 True + 其余 None」被画成 ✅，用户拿去当"没问题"（而那一格根本什么都没保证）。
    if ok is True:
        _head = "✅ 通过" if not _n_unk else ("◐ 部分通过（%d 项没测到）" % _n_unk)
    elif ok is False:
        _head = "❌ 卡住"
    else:
        _head = "○ 没测到"
    lines = ["【检验器 · %s】" % name,
             "症状：%s" % symptom,
             "判决：%s %s" % (_head, verdict)]
    if _CFG_ERR:
        lines.append("⚠️ 配置没读到（%s）：下面依赖配置的那几格用的是**内置默认值** ⇒ "
                     "它们的结论不算数（去控制台确认配置能正常打开）" % _CFG_ERR)
    if _n_unk:
        lines.append("（本页有 %d 项 **○ 没测到**：不算通过也不算失败 —— 别把「没测到」当「承诺成立」）" % _n_unk)
    if action:
        lines.append("下一步：%s" % action)
    lines.append("逐项证据（只读检查，没动窗口/没发消息）：")
    for ch in checks:
        _m = "✅" if ch.get("ok") is True else ("❌" if ch.get("ok") is False else "○")
        lines.append("  %s %s：%s" % (_m, ch["name"], ch["detail"]))
    lines.append("（这段可以直接粘进「反馈」发我）")
    # ⛔ 2026-09-21 加（第十轮 **V-R10-10**）：把"部分通过"这件事**同时**放进结果字段 ——
    #   原来只有 `report` 的头部文字变了、`ok` 仍是 True ⇒ 控制台/调用方看到的还是"通过"，
    #   两处不同源。现在 `partial` 与报告头**同源**（都来自同一次 `_n_unk` 计算）。
    return {"id": vid, "name": name, "symptom": symptom, "ok": (None if ok is None else bool(ok)),
            "partial": bool(ok is True and _n_unk),
            "verdict": verdict, "action": action, "checks": checks, "report": "\n".join(lines)}


def catalog() -> list:
    """给控制台用的清单：`[{id, name}]`（顺序＝严重程度）。"""
    return [{"id": k, "name": v[0]} for k, v in VERIFIERS.items()]


def run(vid: str) -> dict:
    """跑一个检验器。未知 id / 内部报错 ⇒ 返回同样形状的结果（绝不抛给前端）。"""
    vid = str(vid or "").strip()
    if vid not in VERIFIERS:
        return _finish(vid or "unknown", "未知检验器", vid, False,
                       "没有这个检验器（可选：%s）" % "、".join(VERIFIERS), "", [])
    name, fn = VERIFIERS[vid]
    t0 = time.time()
    try:
        out = fn()
    except Exception as e:                                     # noqa: BLE001
        out = _finish(vid, name, name, False, "检验器自己出错了：%s" % str(e)[:80],
                      "把这段报告发我（我照着修检验器）", [])
    out["ms"] = int((time.time() - t0) * 1000)
    return out
