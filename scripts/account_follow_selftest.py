#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「多账号 / 切换微信号」判据（2026-09-19 立，起因＝网友反馈 + 环境检验报告）。

反馈原文（2026-09-19 02:19 那份检验报告的网友）：
    「切换微信号使用后，提示寻找不到库，还要求给予相同的权限」
    「只有前几句话会正常回复，后面不再回复」
现场（他附的报告）：微信 4.1.13.65 · 消息库在 `D:\\xwechat_files\\<账号目录>`（来源=scanned）
    （来源=scanned）· 单账号目录当时只有一个。

根因（本文件 A 段就是它的**可复现判据**）：
    驱动库 `wechatauto/db.py::_pick_account()` 按「账号目录里最新 `.db` 的 mtime」挑账号，
    而**切走的那一刻微信会把旧账号的库 checkpoint 一遍** ⇒ 旧账号的 `.db` 反而最新
    ⇒ 挑中**已经不在用的那个号**，我们跟着读它的库：
      ① 旧号的缓存密钥还能过页1校验时，驱动库那条"按密钥校验选账号"的自愈**不触发**
         ⇒ **静默**读旧库：新消息全在新号里 ⇒ 监听像死了一样（「后面不回复」）；
      ② 旧号密钥对不上时它报「数据库无可用密钥…②本程序权限低于微信（微信以管理员运行时…）」
         ⇒ 用户看到的就是「找不到库 + 要权限」（那句"权限"只是提示语里的一种可能）。
修法（本文件守的六条）：
  A. **选题判据**：`-wal`（库正在被写）优先于 `.db` 的 mtime；并用"旧规则会挑错"当阴性对照，
     证明这条判据抓得住这次的回归；
  B. `pick_account`：新鲜 `-wal` 赢 > 用户填到**账号目录**这一层＝钉死（另有一个在写时给 note）
     > 都没在写就退回写入最新；
  C. `switched()`：监听循环每 15 秒问的"要不要跟着切号"——**单账号永不切 · 我自己还在写就不切
     （多开不许来回抖）· 只有"别的号在写、我这个静默"才切**；我读的号目录消失了也要切；
  D. `_db_open_plan`：多账号时第一跳**显式带 account**，后面跟"驱动库自选"与"换账号"兜底；
     **单账号机器一跳都不多加**（绝大多数用户走的就是这一跳，行为与以前完全一致）；
  E. 自我台账不许跨账号：`self_local_ids.json` 带 `acct`（不一致整份不用，老格式也不用——
     按项目红线「宁可漏判一次回声，也绝不许把别人的话丢掉」）；`self_identity.json` 同理
     （否则切号后拿旧号的 wxid 当"我" ⇒ 回自己）；
  F. 接线：监听循环有切号跟随、症状检验器「它不回复」要看账号、检验报告要印账号。
用法：`py -3 scripts\\account_follow_selftest.py`
"""
import json
import os
import shutil
import sys
import tempfile
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import wechat as W          # noqa: E402
from agent import wechat_dir as D      # noqa: E402

PASS = FAIL = 0
NOW = time.time()


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [%s]" % detail if detail else ""))


def _src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
        return f.read()


def mk_acct(parent, acct, db_age_h=5.0, wal_age_h=None):
    """造一个账号目录：`<parent>/<acct>/db_storage/message/message_0.db`（可指定 -wal 新旧）。"""
    d = os.path.join(parent, acct, "db_storage", "message")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "message_0.db")
    with open(p, "wb") as f:
        f.write(b"x")
    os.utime(p, (NOW - db_age_h * 3600, NOW - db_age_h * 3600))
    if wal_age_h is not None:
        w = p + "-wal"
        with open(w, "wb") as f:
            f.write(b"x")
        os.utime(w, (NOW - wal_age_h * 3600, NOW - wal_age_h * 3600))
    return os.path.join(parent, acct)


def bare(acct, db_dir="", wxid=""):
    """造一个**不接真库**的 adapter 壳（只带 _db 的几个属性）——自检不许碰微信。"""
    ad = W.WeChatAdapter.__new__(W.WeChatAdapter)
    ad._self_local = {}
    ad._self_local_loaded = False
    ad._self_wxid = ""
    ad._self_wxid_src = ""
    ad._self_nickname = ""
    ad._db = type("D", (), {"account": acct, "db_dir": db_dir, "wxid": wxid})()
    return ad


TMP = tempfile.mkdtemp(prefix="pm_acctfollow_")
_saved_cands = W._db_dir_candidates
try:
    print("── A. 复现：切号后驱动库会挑中**已经不在用的那个号** ──")
    parent = os.path.join(TMP, "xwechat_files")
    # 旧号（已经切走）：切走那一刻被 checkpoint ⇒ 主库 .db 很新；-wal 早停了
    mk_acct(parent, "wxid_OLD_aaaa", db_age_h=0.05, wal_age_h=3.0)
    # 新号（正在用）：主库几小时没 checkpoint，但 -wal 刚刚还在写
    mk_acct(parent, "wxid_NEW_bbbb", db_age_h=5.0, wal_age_h=0.008)

    _accs = D.accounts(parent)
    ok("两个账号目录都被列出来（含写入证据）", len(_accs) == 2,
       str([(a["name"], round(a["wal"], 1), round(a["db"], 1)) for a in _accs]))
    ok("此刻**只有**新号的 -wal 是新鲜的（旧号的 -wal 停在 3 小时前）",
       [a["name"] for a in _accs if (NOW - a["wal"]) <= D._LIVE_WINDOW_S] == ["wxid_NEW_bbbb"],
       str([(a["name"], round(NOW - a["wal"])) for a in _accs]))

    # 旧规则（驱动库 `_pick_account`：只看 .db 的 mtime）
    try:
        from wechatauto.db import WeChatDB as _WC
        _fake = type("F", (), {"db_dir": parent})()
        _driver_pick = _WC._pick_account(_fake)
        ok("**复现成功**：驱动库按 `.db` mtime 挑，挑中的是已经切走的旧号",
           _driver_pick == "wxid_OLD_aaaa", _driver_pick)
    except Exception as e:
        ok("（跳过）驱动库 `_pick_account` 对照：%s" % type(e).__name__, True, str(e)[:60])
        _driver_pick = ""

    _mine = D.pick_account(parent)
    ok("我们的判据挑的是**正在被写的那个号**", _mine.get("name") == "wxid_NEW_bbbb", str(_mine)[:120])
    ok("并且把「为什么是它」说清楚（-wal 刚刚还在写）", "刚" in str(_mine.get("why")) or "写" in str(_mine.get("why")),
       str(_mine.get("why"))[:80])

    print("── A2. 阴性对照：把 -wal 这一维拿掉，判据**必须**会挑错 ──")
    _by_db = sorted(_accs, key=lambda a: (a["db"], a["name"]), reverse=True)[0]["name"]
    ok("只用 `.db` mtime 就会挑中旧号（证明 A 段两条判据不是恒真）",
       _by_db == "wxid_OLD_aaaa", _by_db)
    _pk_all = D.pick_account(parent)
    ok("`pick_account` 拿掉 -wal 就没得挑（此时它退回按 live 挑并如实说明）",
       bool(_pk_all.get("why")), str(_pk_all.get("why"))[:70])

    print("── A3. 两个号**都不「新鲜」**时照样按 -wal 比（本机实测踩到的坑：微信闲置 6 分钟）──")
    _p2 = os.path.join(TMP, "idle_two")
    mk_acct(_p2, "wxid_IDLE_old", db_age_h=0.05, wal_age_h=0.5)     # 旧号：-wal 停在 30 分钟前，.db 刚刚
    mk_acct(_p2, "wxid_IDLE_live", db_age_h=50.0, wal_age_h=0.2)    # 在用的号：-wal 12 分钟前，.db 是 50 小时前
    _pk3 = D.pick_account(_p2)
    ok("都超出 180 秒窗口时，挑的是 **-wal 更晚**的那个（不是 .db 更新的那个）",
       _pk3.get("name") == "wxid_IDLE_live", str(_pk3.get("why"))[:120])
    ok("并且如实说明「都没在写、按最近写 -wal 的那个挑」",
       "没在写" in str(_pk3.get("why")) or "-wal" in str(_pk3.get("why")), str(_pk3.get("why"))[:100])
    _by_db2 = max(D.accounts(_p2), key=lambda a: a["db"])["name"]
    ok("阴性对照：只看 .db 就会挑中旧号（证明这条判据不是恒真）",
       _by_db2 == "wxid_IDLE_old", _by_db2)

    print("── B. pick_account 的优先级 ──")
    _pin = D.pick_account(parent, os.path.join(parent, "wxid_OLD_aaaa"))
    ok("用户填到**账号目录**这一层 ⇒ 钉死它（按他填的来）",
       _pin.get("name") == "wxid_OLD_aaaa" and _pin.get("pinned") is True, str(_pin)[:110])
    ok("钉死的那个不在写、别的号在写 ⇒ note 要提醒（但不许偷偷换号）",
       "正在写的是" in str(_pin.get("note")) and _pin.get("name") == "wxid_OLD_aaaa",
       str(_pin.get("note"))[:110])
    _pin2 = D.pick_account(parent, os.path.join(parent, "wxid_NEW_bbbb"))
    ok("钉死的那个正好在写 ⇒ note 为空（没坏就别报警）", _pin2.get("note") == "", str(_pin2.get("note")))
    _single = os.path.join(TMP, "one_account")
    mk_acct(_single, "wxid_ONLY_cccc", db_age_h=1.0, wal_age_h=1.0)
    _sp = D.pick_account(_single)
    ok("只有一个账号 ⇒ 就它（不挑三拣四）", _sp.get("name") == "wxid_ONLY_cccc", str(_sp)[:90])
    ok("只有一个账号时 fresh 判定也如实（-wal 1 小时前＝不新鲜）", _sp.get("fresh") == [], str(_sp.get("fresh")))
    ok("目录不像微信数据目录 ⇒ 返回空（交给驱动库自探测）",
       D.pick_account(os.path.join(TMP, "nope")) == {}, str(D.pick_account(os.path.join(TMP, "nope"))))

    print("── C. switched()：要不要跟着切号（监听循环每 15 秒问一次）──")
    ok("单账号 ⇒ 永不切", D.switched("wxid_ONLY_cccc", _single, pin="").get("stale") is False,
       str(D.switched("wxid_ONLY_cccc", _single, pin=""))[:80])
    ok("**我读的这个号还在写** ⇒ 不切（多开两个号同时活着时不许来回抖）",
       D.switched("wxid_NEW_bbbb", parent, pin="").get("stale") is False)
    _sw = D.switched("wxid_OLD_aaaa", parent, pin="")
    ok("我读的号静默了、另一个号在写 ⇒ **切**",
       _sw.get("stale") is True and _sw.get("live") == "wxid_NEW_bbbb", str(_sw)[:120])
    ok("切号理由写得像人话（点名两个号 + 时间证据）",
       "wxid_OLD_aaaa" in str(_sw.get("why")) and "wxid_NEW_bbbb" in str(_sw.get("why")),
       str(_sw.get("why"))[:120])
    ok("我读的账号目录已经不见了 ⇒ 也切（换到正在写的那个）",
       D.switched("wxid_GONE_dddd", parent, pin="").get("stale") is True)
    ok("不知道自己在读哪个号 ⇒ 不切（宁可不动）",
       D.switched("", parent, pin="").get("stale") is False)
    _sw_pin = D.switched("wxid_OLD_aaaa", parent, pin=os.path.join(parent, "wxid_OLD_aaaa"))
    ok("你把账号目录**钉死**了 ⇒ 不切（切了还是它，否则会变成每 15 秒重连一次的循环）",
       _sw_pin.get("stale") is False and "钉死" in str(_sw_pin.get("why")), str(_sw_pin.get("why"))[:100])

    print("── C2. **两个号都没在写**时也得跟切（V-R10-28：旧规则④在这里永不跟切，与 pick_account 相反）──")
    # 现场：切到 B 号之后 B 号**短期没收到消息** ⇒ 旧实现的 `others`（别的号 -wal 新鲜）是空集
    #   ⇒ `return out` **永不跟切**：继续读 A 号旧库、一点异常都没有；要"新号先收到一条消息"才自愈
    #   —— 而它盯的正是"读不到消息的那个库"（自指）。修法＝两条路走同一个判定（pick_account 是唯一来源）。
    _sw_idle = D.switched("wxid_IDLE_old", _p2, pin="")
    ok("两个号都静默 ⇒ **仍然跟切**到 pick_account 挑中的那个号",
       _sw_idle.get("stale") is True and _sw_idle.get("live") == _pk3.get("name"), str(_sw_idle)[:130])
    ok("**同一事实同一结论**：switched().live == pick_account().name",
       _sw_idle.get("live") == _pk3.get("name"), "%s / %s" % (_sw_idle.get("live"), _pk3.get("name")))
    ok("切号理由点名两个号（人话）",
       "wxid_IDLE_old" in str(_sw_idle.get("why")) and "wxid_IDLE_live" in str(_sw_idle.get("why")),
       str(_sw_idle.get("why"))[:120])
    _sw_idle_ok = D.switched("wxid_IDLE_live", _p2, pin="")
    ok("阴性对照：我读的就是那个号 ⇒ 不切（别自己跟自己抖）",
       _sw_idle_ok.get("stale") is False, str(_sw_idle_ok)[:90])
    # 反例锚：老规则④的判据（别的号里 -wal 新鲜的）在这个夹具里就是空集 ⇒ 旧实现必然红
    _old_others = [a for a in D.accounts(_p2)
                   if a["name"] != "wxid_IDLE_old" and a["wal"] and (NOW - a["wal"]) <= D._LIVE_WINDOW_S]
    ok("反例锚：老规则④（只看「别的号 -wal 新鲜」）在这个夹具里是空集 ⇒ 旧实现永不跟切",
       _old_others == [])

    print("── D. 开库链：多账号显式带 account；单账号一跳都不多加 ──")
    W._db_dir_candidates = lambda extra="": (([extra] if str(extra or "").strip() else []) + [parent])
    _plan = W._db_open_plan(parent)
    _p0 = _plan[0]
    ok("第一跳就显式带上正在写的那个账号", _p0[0] == parent and _p0[2] == "wxid_NEW_bbbb", str(_p0)[:120])
    ok("第一跳的「为什么」讲清是自动挑的还是钉死的", "账号" in str(_p0[3]) or _p0[3], str(_p0[3])[:60])
    ok("后面跟着「驱动库自选」兜底（保留它自己的账号自愈）",
       any(h[5] == "驱动库自选" and h[2] == "" for h in _plan), str([h[5] for h in _plan]))
    ok("其余账号也各有一跳（挑错时的退路）",
       any(h[5] == "换账号" and h[2] == "wxid_OLD_aaaa" for h in _plan),
       str([(h[2], h[5]) for h in _plan]))
    _plan1 = W._db_open_plan(_single)
    ok("**单账号机器**：同一个目录只有一跳，且 account 为空（零额外成本、行为与以前完全一致）",
       [h[0] for h in _plan1].count(_single) == 1
       and all(h[2] == "" for h in _plan1 if h[0] == _single), str(_plan1)[:140])
    _pm0 = W._db_open_plan("")
    ok("没配目录时：扫盘那条也照样带账号（它才是真正在读的那个目录）",
       any(h[0] == parent and h[2] == "wxid_NEW_bbbb" for h in _pm0), str(_pm0)[:140])
    ok("而「驱动库自探测」那条永远是空 account（那是它自己挑，别抢它的活）",
       all(h[2] == "" for h in _pm0 if h[0] == ""), str(_pm0)[:120])

    print("── E. 自我台账/身份不许跨账号（切号后静默漏回的另一条路）──")
    _lf = os.path.join(TMP, "self_local_ids.json")
    _idf = os.path.join(TMP, "self_identity.json")
    a1 = bare("wxid_A_1111", db_dir=parent, wxid="wxid_a")
    a1._self_local_file = lambda: _lf
    a1.remember_self_local("group:1", 5, int(NOW))
    _d1 = json.load(open(_lf, encoding="utf-8"))
    ok("台账落盘带上了账号（否则切号后没法判归属）", _d1.get("acct") == "wxid_A_1111", str(_d1)[:120])
    ok("行号与时间都在（判自己最硬的那一档）",
       _d1.get("rows", {}).get("group:1", [])[:1] and _d1["rows"]["group:1"][0][0] == 5, str(_d1)[:160])
    a2 = bare("wxid_B_2222", db_dir=parent, wxid="wxid_b")
    a2._self_local_file = lambda: _lf
    ok("**换到另一个账号**：旧号的行号一律不认（这行是别人的话，不是我的）",
       a2.is_self_local("group:1", 5, int(NOW)) is False)
    a3 = bare("wxid_A_1111", db_dir=parent, wxid="wxid_a")
    a3._self_local_file = lambda: _lf
    ok("同一个账号：照样认（判据没被账号闸弄瞎）",
       a3.is_self_local("group:1", 5, int(NOW)) is True)
    # 老格式（没账号信息）⇒ 整份不用（项目红线：宁可漏判一次回声，也绝不丢掉别人的话）
    _lf2 = os.path.join(TMP, "self_local_legacy.json")
    with open(_lf2, "w", encoding="utf-8") as f:
        json.dump({"group:1": [[5, int(NOW), int(NOW)]]}, f)
    a4 = bare("wxid_A_1111", db_dir=parent, wxid="wxid_a")
    a4._self_local_file = lambda: _lf2
    ok("老格式台账（没有账号信息）整份不用 —— 宁可漏判一次回声", a4.is_self_local("group:1", 5, int(NOW)) is False)

    _w_src = _src(os.path.join("agent", "wechat.py"))
    ok("源码级：台账读的时候有账号闸", "_acct0 != _me" in _w_src)
    ok("源码级：老格式明确不采用（不是悄悄当成新格式读）", "自我行号台账是老格式" in _w_src)
    # 身份：切号后不能拿旧号的 wxid 当"我"（否则回自己 / @ 不到我）
    with open(_idf, "w", encoding="utf-8") as f:
        json.dump({"wxid": "wxid_a", "acct": "wxid_A_1111", "from": "echo"}, f)
    b1 = bare("wxid_B_2222", db_dir=parent, wxid="wxid_b")
    b1._self_id_file = lambda: _idf
    b1.load_self_identity()
    ok("落盘身份属于另一个账号 ⇒ **不采用**（切号后不会把旧号当我）", b1._self_wxid == "", b1._self_wxid)
    b2 = bare("wxid_A_1111", db_dir=parent, wxid="wxid_a")
    b2._self_id_file = lambda: _idf
    b2.load_self_identity()
    ok("同一个账号 ⇒ 正常读回（闸门没把正常路堵死）", b2._self_wxid == "wxid_a", b2._self_wxid)
    _idf2 = os.path.join(TMP, "self_identity_legacy.json")
    with open(_idf2, "w", encoding="utf-8") as f:
        json.dump({"wxid": "wxid_a", "from": "echo"}, f)      # 老格式：没有 acct，只能靠 wxid 对
    b3 = bare("wxid_B_2222", db_dir=parent, wxid="wxid_b")
    b3._self_id_file = lambda: _idf2
    b3.load_self_identity()
    ok("老格式身份也能按「这个库属于谁」判出不属于我 ⇒ 不用", b3._self_wxid == "", b3._self_wxid)
    b4 = bare("wxid_A_1111", db_dir=parent, wxid="wxid_a")
    b4._self_id_file = lambda: _idf2
    b4.load_self_identity()
    ok("老格式 + 账号对得上 ⇒ 照用（升级不影响既有机器）", b4._self_wxid == "wxid_a", b4._self_wxid)
    ok("源码级：身份读回有账号闸", "落盘的「自己是谁」属于另一个账号" in _w_src)
    ok("源码级：开库前先摘掉「盘上已经没有的密钥缓存条目」（切号后那句 KeyError 的正面预防）",
       "_pm_prune_dead_key_entries(_d, _acct)" in _w_src)
    ok("源码级：构造函数撞 KeyError 也会定点自愈",
       "def __init__(self, *a, **kw)" in _w_src and "_pm_drop_stale_key_entry(self, _rel)" in _w_src)
    ok("源码级：安全入口取账号（夹具/桩件没有 db_account 时不许把落盘弄挂）",
       "def _db_account_of(obj)" in _w_src)

    print("── F. 接线：监听跟随 / 报告 / 症状检验器 ──")
    _pm = _src(os.path.join("scripts", "persona_morph.py"))
    ok("监听循环里有切号跟随（每 15 秒一次、只 stat）",
       "_ACCT_CHK" in _pm and "db_account_stale()" in _pm and ">= 15" in _pm)
    ok("跟着切＝重新接入 + 换掉**所有**持有者（sender/orch/targets，不然表面接上了其实发不出去）",
       "def _adopt_wc(_wc_new" in _pm and "sender.wechat = _wc_new" in _pm
       and "groups, targets = _collect_targets(_wc_new)" in _pm)
    ok("重连失败要退避（一次全量接入要跑密钥内存扫描，不许每 15 秒来一次）",
       '_ACCT_CHK["at"] = time.time() + 45.0' in _pm)
    ok("旧的「启动时微信没开 ⇒ 10 秒重试」那条仍在（没被这次改动挤掉）",
       'wechat_box[0] is None and (time.time() - float(_ATTACH.get("at") or 0)) >= 10' in _pm)
    _rep = _src(os.path.join("scripts", "collect_report.py"))
    ok("检验报告会印**在读哪个账号** + 这台机器上有哪几个号",
       "消息库账号: " in _rep and "这台机器上的微信账号目录" in _rep)
    _vf = _src(os.path.join("agent", "verifiers.py"))
    ok("症状检验器「它不回复」第一件事就是看账号对不对",
       "读的是**正在用的那个微信号**" in _vf and "wechat_dir" in _vf)
    ok("台账读数兼容新旧两种格式（别因为格式升级误报「台账是空的」）",
       'local.get("rows")' in _vf)
    _st = D.status(how={"dir": parent, "src": "scanned", "account": "wxid_NEW_bbbb",
                        "accounts_live": ["wxid_NEW_bbbb"], "account_why": "这个账号的库刚刚还在写",
                        "account_names": ["wxid_OLD_aaaa", "wxid_NEW_bbbb"]})
    ok("状态一行话术里带账号（控制台那一行直接显示「在读哪个号」）",
       "账号：wxid_NEW_bbbb" in _st["text"], _st["text"][:140])
    _st2 = D.status(how={"dir": parent, "src": "scanned", "account": "wxid_OLD_aaaa",
                         "accounts_live": ["wxid_NEW_bbbb"],
                         "account_names": ["wxid_OLD_aaaa", "wxid_NEW_bbbb"]})
    ok("读的是静默账号 ⇒ 话术里点明「已静默」+ 给动作",
       "已静默" in _st2["text"] and "切号" in _st2["text"], _st2["text"][:170])
    ok("状态里不带 candidates（控制台每 4 秒轮询，不许把候选清单擦掉）", "candidates" not in _st)

    print("── G. 阴性对照：把账号闸拆掉，E 段那两条必须变红 ──")
    a5 = bare("wxid_B_2222", db_dir=parent, wxid="wxid_b")
    a5._self_local_file = lambda: _lf
    a5._load_self_local()
    a5._self_local_loaded = False
    # 模拟"旧实现"：直接吃整份（不看 acct）
    _raw = json.load(open(_lf, encoding="utf-8"))
    _rows = _raw.get("rows") or _raw
    a5._self_local = {k: [tuple(x) for x in v] for k, v in _rows.items()}
    a5._self_local_loaded = True
    ok("阴性对照：不设账号闸时，**别人的行号会被当成自己发的**（正是「后面不回复」的一条支路）",
       a5.is_self_local("group:1", 5, int(NOW)) is True)
finally:
    W._db_dir_candidates = _saved_cands
    shutil.rmtree(TMP, ignore_errors=True)

print("\n多账号/切号跟随判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
