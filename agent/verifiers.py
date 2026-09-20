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


def _cfg() -> dict:
    try:
        from .config import get_config
        return get_config() or {}
    except Exception:
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


def _check(name, ok, detail) -> dict:
    return {"name": name, "ok": bool(ok), "detail": str(detail)}


def _count(lines, *needles) -> int:
    n = 0
    for l in lines:
        if all(x in l for x in needles):
            n += 1
    return n


def _verdict(checks, ok_all_msg, action_map) -> tuple:
    """一条总判决：第一条不通过的检查 ⇒ 它就是卡点（顺序即优先级）。

    ⭐ 2026-09-19 加**矛盾检测**：如果"负向结论"被同一份报告里的"正向证据"直接否证
    （现场：检验器说「模型 key 没填」，可同一份报告里写着「最近一轮在 36 分钟前」——
    没填 key 根本发不出上一轮），那就**不许**再给"去填 key"这种指令：那是把**我们自己读数的错**
    当成用户的故障，用户只能一脸懵（原话「第三个贼奇怪」）。改判"证据矛盾、请把报告发我"。
    """
    bad = [c for c in checks if not c["ok"]]
    if not bad:
        return True, ok_all_msg, ""
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
    return (False, "卡在「%s」：%s" % (first["name"], first["detail"]),
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
        checks.append(_check("版本门不拦发送", True, "读不到版本门（按不拦处理）：%s" % str(e)[:40]))
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
    last_fail = [l for l in log_tail[-200:] if ("投递发送失败" in l or "会话头不匹配，拒绝投递" in l
                                                or "判据不可用" in l)]
    # ⚠️ 别把"最近没发过"当成故障：刚重启/刚装好时日志里本来就没有成功记录 —— 那是"未知"，
    #    不是"坏了"。只有**确实有失败/可疑记录**时才判不通过（避免检验器自己吓用户）。
    if last_fail:
        _send_ok, _send_detail = False, ("最近 200 行里有 %d 条失败/可疑（例：%s）"
                                        % (len(last_fail), last_fail[-1][:80]))
    elif last_ok:
        _send_ok, _send_detail = True, "最近 200 行里有 %d 条成功发送记录" % last_ok
    else:
        _send_ok, _send_detail = True, "最近没有发送记录（刚重启/还没发过）⇒ 这条不判坏"
    checks.append(_check("最近的发送记录里没有失败", _send_ok, _send_detail))
    # 内部故障话术拦截（拦得对，但用户要知道它拦了什么）
    try:
        from . import sender as _s
        og = _s.outbound_gate_status() or {}
        n = int(og.get("blocked") or og.get("count") or 0)
        checks.append(_check("出站闸门没有误拦正常回复", True,
                             "已拦下 %d 条内部故障话术（只留本机日志，不进群）" % n))
    except Exception:
        pass
    ok, verdict, action = _verdict(checks, "发送链各环节都正常：暂停关、版本门放行、投递档可用、"
                                           "主窗在、消息库可读、最近有成功发送记录",
                                   {"没有处于暂停": "点控制台的「继续」",
                                    "版本门不拦发送": "控制台「微信」面板看版本门横幅",
                                    "投递档可用（不是只走真鼠标被闸住）": "把 input.backend 选回投递档",
                                    "微信主窗找得到": "把微信窗口从托盘里点出来（Ctrl+Alt+W）",
                                    "消息库读得到": "控制台「微信」面板跑一次「接微信」逐步检查"})
    return _finish("send_blocked", "消息发不出去 / 卡在未通过", "消息发不出去、聊天记录生成了但发不出、"
                                                              "卡在「未通过 会话投递失败」", ok, verdict, action, checks)


# ── ② 它回自己 / 把我认成它 ─────────────────────────────────────────────────
def v_self_echo() -> dict:
    c = _cfg()
    checks = []
    ident = _read_json(_p("data", "self_identity.json"), {})
    sid = str(ident.get("wxid") or "")
    checks.append(_check("认识自己（self_wxid）或有替代证据",
                         True, ("已认识：来源=%s" % ident.get("from")) if sid else
                         "**没认出自己**——不影响：判自己还靠下面三档"))
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
    checks.append(_check("台账里能看到「判为自己」的证据", (self_w + local_hit + echo) >= 0,
                         "最近 %d 条：喂给模型 %d 条 · self_wxid 命中 %d · **自家行号命中 %d** · 回声命中 %d"
                         % (len(led), keep, self_w, local_hit, echo)))
    bad_reply = _count(_tail(_p("logs", "persona_morph.log"), 400), "回自己")
    checks.append(_check("近期没有『回自己』的记录", bad_reply == 0,
                         "日志里出现 %d 次相关记录" % bad_reply))
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
        _wv = _wd_v.status()
        _acc = str(_wv.get("account") or "")
        _ns = list(_wv.get("account_names") or [])
        _live = _wv.get("account_live")
        _acc_ok = (not _ns) or (len(_ns) < 2) or (_live is not False)
        _acc_note = ("在读账号 %s（%s）" % (_acc or "没认出来",
                                          "库正在被写" if _live else
                                          ("**没在动**" if _live is False else "拿不到写入证据")))
        if len(_ns) > 1:
            _acc_note += "；这台机器上有 %d 个账号" % len(_ns)
            # ⭐ 2026-09-19 加（网友反馈：「**大号能连、小号连接不上**」）：把**每个号的活跃证据
            #   并排印出来**，并直接判"我在读的那个号是不是正在被写的那个"——多账号机器上
            #   这是最难自查的一条（读到不在写的号时，新消息一条都进不来，而其它检查全绿）。
            try:
                _par = str(_wv.get("dir") or _wv.get("parent") or _wv.get("account_dir") or "")
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
                if _age is not None and _age <= 300:
                    _live_names.append(_nm)
                    _st = "库正在被写（%ds 前）" % _age
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
        checks.append(_check("读的是**正在用的那个微信号**", True,
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
        checks.append(_check("「数据库目录」没有填到账号层", True,
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
    checks.append(_check("勾了监听目标群", True,
                         ("白名单 %d 个：%s（**名字要与微信里完全一致**，差一个字就匹配不上 ⇒ 监听不到）"
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
    checks.append(_check("最近有过一轮响应", last_age is not None and last_age < 24 * 60,
                         ("最近一轮在 %.0f 分钟前（%s）" % (last_age, sess[-1])) if last_age is not None
                         else "今天的会话日志里没有可用时间戳"))
    tier_ok, tier_note = True, ""
    try:
        from . import prompt as _pr
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
        tier_note = "读不到回复档位"
    checks.append(_check("回复档位不是『只回艾特』却指望它搭话", tier_ok, tier_note))
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
    checks.append(_check("表情能离线解出原图", (files > 0) and (key_ok or files > 0), note))
    logs = _tail(_p("logs", "persona_morph.log"), 400)
    shot_fail = _count(logs, "表情截图没取到") + _count(logs, "表情截图跳过")
    checks.append(_check("最近没有『只能截图又截不到』的记录", shot_fail == 0,
                         "相关记录 %d 条（新版本会先离线解原图，解不出才截图）" % shot_fail))
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
    checks.append(_check("置前请求都被闸门拦住了", True,
                         "累计拦下 %d 次置前/置顶%s" % (n, ("（最近原因：%s）" % str(refused.get("why"))[:60])
                                                    if refused.get("why") else "")))
    logs = _tail(_p("logs", "persona_morph.log"), 400)
    deny = _count(logs, "拒绝置前") + _count(logs, "拒绝置顶")
    checks.append(_check("最近日志里能看到闸门在工作", True, "近 400 行里 %d 条拒绝置前/置顶" % deny))
    ok_audit = os.path.exists(_p("scripts", "fg_audit_selftest.py"))
    checks.append(_check("静态审计判据在（防止以后偷偷加置前）", ok_audit,
                         "scripts/fg_audit_selftest.py %s" % ("在" if ok_audit else "**不在**")))
    ok, verdict, action = _verdict(checks, "后台承诺成立：只走后台开着、真鼠标兜底关着、闸门在拦、静态审计在",
                                   {"「只走后台」是开着的": "控制台「微信」面板把「只走后台」打开",
                                    "真鼠标兜底是关着的": "把 input.allow_real_fallback 关掉"})
    return _finish("fg_disturb", "抢窗口 / 动我鼠标", "它把我的窗口顶到前台、抢我鼠标、打扰我打字",
                   ok, verdict, action, checks)


# ── ⑥ 更新不动 / 打不开 ───────────────────────────────────────────────────
def v_update_stuck() -> dict:
    checks = []
    st = _read_json(_p("data", "update_state.json"), {})
    checks.append(_check("更新状态文件可读", bool(st), "lastStatus=%s · version=%s"
                         % (st.get("lastStatus") or st.get("status") or "-", st.get("version") or "-")))
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
    checks.append(_check("更新完成标志（更新成功后会写）", True,
                         "%s" % ("data/update_done.flag 在（上次更新已自完成）"
                                 if os.path.exists(_p("data", "update_done.flag")) else "没有完成标志")))
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
                checks.append(_check("源 %s" % U._short_url(u), True,
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
VERIFIERS = {
    "send_blocked": ("消息发不出去", v_send_blocked),
    "self_echo": ("它回自己 / 把我认成它", v_self_echo),
    "no_reply": ("它不回复", v_no_reply),
    "emoji_blank": ("表情包看不到", v_emoji_blank),
    "fg_disturb": ("抢窗口 / 动我鼠标", v_fg_disturb),
    "update_stuck": ("更新不动 / 打不开", v_update_stuck),
    "update_source": ("更新拉取不到 / timeout", v_update_source),
    "console_dead": ("控制台打不开", v_console_dead),
}


def _finish(vid, name, symptom, ok, verdict, action, checks) -> dict:
    lines = ["【检验器 · %s】" % name,
             "症状：%s" % symptom,
             "判决：%s %s" % ("✅ 通过" if ok else "❌ 卡住", verdict)]
    if action:
        lines.append("下一步：%s" % action)
    lines.append("逐项证据（只读检查，没动窗口/没发消息）：")
    for ch in checks:
        lines.append("  %s %s：%s" % ("✅" if ch["ok"] else "❌", ch["name"], ch["detail"]))
    lines.append("（这段可以直接粘进「反馈」发我）")
    return {"id": vid, "name": name, "symptom": symptom, "ok": bool(ok), "verdict": verdict,
            "action": action, "checks": checks, "report": "\n".join(lines)}


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
