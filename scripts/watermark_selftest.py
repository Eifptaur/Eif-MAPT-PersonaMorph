"""W2 判据：监听水位的落盘 / 成功才推进 / 失败重试留痕 / 每会话串行（不需要微信在跑）。

跑法：  py -3 scripts/watermark_selftest.py      退出码 0=全过 / 1=有失败
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import listener_watermark as lw  # noqa: E402
import _srcmatch as _sm                      # noqa: E402  空白容忍的源码断言（V-R4-13 第三条）

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def items(*seqs):
    return [{"mid": "m%d" % s, "sort_seq": s, "text": "t%d" % s} for s in seqs]


def main():
    tmp = tempfile.mkdtemp(prefix="wm-judge-")
    wm_path = os.path.join(tmp, "listener_watermark.json")
    dl_path = os.path.join(tmp, "listener_failed.jsonl")
    logs = []
    log = lambda lvl, fmt, *a: logs.append((lvl, fmt % a if a else fmt))

    print("== A. Watermark: 落盘 / 原子写 / 只前进 ==")
    wm = lw.Watermark(wm_path)
    ok("初始为空", wm.get("group:a") == 0)
    wm.set("group:a", 100)
    ok("flush 后文件存在且是合法 JSON", wm.flush() and os.path.exists(wm_path) and isinstance(json.load(open(wm_path, encoding="utf-8")), dict))
    ok("原子写不残留 .tmp", not os.path.exists(wm_path + ".tmp"))
    ok("重启后读回旧值（不跳 latest）", lw.Watermark(wm_path).get("group:a") == 100)
    wm.set("group:a", 50)
    ok("默认只前进、不许回退（防重复处理）", wm.get("group:a") == 100)
    wm.set("group:a", 50, forward_only=False)
    ok("显式 reset 才能回退", wm.get("group:a") == 50)

    print("== B. process_batch: 成功才推进 ==")
    wm2 = lw.Watermark(wm_path)
    wm2.set("group:b", 0, forward_only=False)
    seen = []

    def ok_handler(it):
        seen.append(it["sort_seq"])
        return {"id": it["sort_seq"]}          # 非空 dict ＝ 成功（对齐 store.append_incoming 的返回）

    st = lw.process_batch("group:b", items(1, 2, 3), ok_handler, wm2, log=log, deadletter_path=dl_path, sleep=lambda s: None)
    ok("三条全成功 ⇒ processed=3 且水位推进到最后一条", st["processed"] == 3 and st["advanced_to"] == 3, st)
    ok("每条都落盘（处理完文件里就是最新水位）", lw.Watermark(wm_path).get("group:b") == 3)

    print("== C. 失败重试 3 次：中途转成功 ==")
    logs.clear()
    calls = {"n": 0}

    def flaky(it):
        calls["n"] += 1
        if it["sort_seq"] == 2 and calls["n"] < 3:
            raise RuntimeError("落库失败")
        return {"ok": True}

    st = lw.process_batch("group:c", items(1, 2), flaky, wm2, log=log, deadletter_path=dl_path, sleep=lambda s: None)
    ok("重试后成功 ⇒ processed=2、retried>=1", st["processed"] == 2 and st["retried"] >= 1, st)
    ok("重试有留痕（日志里带 第 n/3 次 与原因）", any("重试" in m or "次处理失败" in m for _, m in logs), logs[:1])

    print("== D. 永久失败：dead-letter + 越过毒消息（不卡队列） ==")
    logs.clear()
    if os.path.exists(dl_path):
        os.remove(dl_path)
    wm3 = lw.Watermark(wm_path)
    wm3.set("group:d", 0, forward_only=False)

    def poison(it):
        if it["sort_seq"] == 2:
            raise RuntimeError("永远失败的毒消息")
        return {"ok": True}

    st = lw.process_batch("group:d", items(1, 2, 3), poison, wm3, log=log, deadletter_path=dl_path, sleep=lambda s: None)
    ok("毒消息计 failed=1，其余照样处理", st["failed"] == 1 and st["processed"] == 2, st)
    ok("水位越过毒消息（advanced_to=3，不会永远卡在第 2 条）", st["advanced_to"] == 3)
    ok("dead-letter 落了这条（含 mid/seq/attempts/error）", os.path.exists(dl_path) and json.loads(open(dl_path, encoding="utf-8").readline())["mid"] == "m2")
    ok("日志明确写了『已越过』", any("越过" in m for _, m in logs))

    print("== E. 重放保护 + 异常不打断整批 ==")
    wm4 = lw.Watermark(wm_path)
    wm4.set("group:e", 5, forward_only=False)
    ran = []
    st = lw.process_batch("group:e", items(4, 5, 6), lambda it: ran.append(it["sort_seq"]) or True, wm4, log=log, sleep=lambda s: None)
    ok("水位之前的消息被跳过（不重放）", st["skipped"] == 2 and ran == [6], (st, ran))

    print("== F. 每会话串行（并发调用者排队） ==")
    wm5 = lw.Watermark(wm_path)
    wm5.set("group:f", 0, forward_only=False)
    cur, peak = {"n": 0}, {"n": 0}
    guard = threading.Lock()

    def slow(it):
        with guard:
            cur["n"] += 1
            peak["n"] = max(peak["n"], cur["n"])
        time.sleep(0.05)
        with guard:
            cur["n"] -= 1
        return True

    ts = [threading.Thread(target=lw.process_batch, args=("group:f", items(1, 2, 3), slow, wm5), kwargs={"sleep": lambda s: None}) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ok("同一会话同一时刻只有一个处理者（peak=1）", peak["n"] == 1, "peak=%d" % peak["n"])
    ok("并发下水位仍单调到最后", wm5.get("group:f") == 3, wm5.get("group:f"))

    # == G. 「自己发的/系统消息」这类被丢弃的行必须能推过水位（2026-09-18 现场：机器人每两分钟自己念一句）==
    #    根因：`poll_new_messages` 把归一化阶段被丢掉的行**直接排除在批次外** ⇒ 水位推不过它
    #    ⇒ 同一行每 1.5 秒被重读（台账实测同一条连着 24 行 echo=True keep=False），
    #    等它超过 120 秒回声窗就被当成"别人的话"⇒ 机器人回自己。
    print("== G. 被丢弃的行（skip 标记）也要能推过水位 ==")
    _pm = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "scripts", "persona_morph.py"), encoding="utf-8").read()
    _wx = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "agent", "wechat.py"), encoding="utf-8").read()
    ok("poll_new_messages 会给被丢弃的行带一个只含 seq 的 skip 标记（否则水位推不过去）",
       '{"mid": None, "sort_seq": _sq' in _wx and '"skip": "归一化阶段丢弃' in _wx)
    ok("监听侧把 skip 标记算「已处理」（返回真值 ⇒ 水位前进）",
       'if nm.get("skip"):' in _pm and 'return {"dropped": nm.get("skip")}' in _pm)
    wm6 = lw.Watermark(wm_path)
    wm6.set("group:g", 0, forward_only=False)
    _seen = []

    def _skip_aware(it):
        # 照 `persona_morph._handle_one` 的口径：skip 标记也返回真值（＝已处理）
        _seen.append(it.get("sort_seq"))
        return {"dropped": it.get("skip")} if it.get("skip") else {"ok": True}

    _batch = [{"mid": "m1", "sort_seq": 1, "text": "别人的话"},
              {"mid": None, "sort_seq": 2, "skip": "归一化阶段丢弃（自己发的/系统/空内容）"}]
    st_g = lw.process_batch("group:g", _batch, _skip_aware, wm6, log=log, sleep=lambda s: None)
    ok("末尾那条是被丢弃的行 ⇒ 水位仍推到 2（不再卡住重读）",
       wm6.get("group:g") == 2 and st_g.get("processed") == 2, (wm6.get("group:g"), st_g))

    # ── 2026-09-17（用户问「我把聊天记录清空了，它会不会学不会、从而不发」）──
    #    水位只前进不回退是对的（防重复处理），但**微信清空记录后序号可能回落/换库** ⇒ 新消息会被
    #    判成"处理过"而永远跳过，而且**重启也救不回**（水位是从文件读回来的）⇒ 监听循环里必须有自愈。
    print("\n-- G. 记录被清空后的水位自愈（源码级，防以后被顺手删掉） --")
    _pm = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona_morph.py"),
               encoding="utf-8").read()
    ok("监听循环里有自愈限频表 `_wm_heal`", "_wm_heal = {}" in _pm and "_wm_heal.get(wxid" in _pm)
    ok("判据是「最新序号**低于**水位」（正常运行时不会成立 ⇒ 不误触发）",
       "_latest < _cur" in _pm and "latest_seq_ex(wxid)" in _pm)
    ok("回退走显式 `forward_only=False`（默认只前进，不许悄悄退）",
       "wm.set(chat_key, _latest, forward_only=False)" in _pm)
    ok("自愈要落盘 + 留日志（否则用户永远不知道为什么它不回）；"
       "**落盘看返回值**（V-R10-30：`wm.flush()` 裸调用一处都不许剩）",
       _sm.has(_pm, "flush_checked(wm") and not _sm.has(_pm, "wm.flush()")
       and "记录像是被清过" in _pm)
    # ── 用户拍板（2026-09-17）：「不要让用户担风险啊，还要删这删那的、还要试这试那的，不行」 ──
    #    ⇒ 老办法"删 data\listener_watermark.json"不许留给用户，必须变成控制台上的一个按钮。
    print("\n-- H. 用户零操作：控制台一键「重新对齐监听水位」（不删文件、不重启） --")
    ok("主程序里有 _reset_watermark（按当前最新对齐、显式 forward_only=False）",
       "def _reset_watermark()" in _pm and 'wm.set("group:" + wxid, seq, forward_only=False)' in _pm)
    ok("它接进了 WebUI（watermark_reset_fn=_reset_watermark）",
       "watermark_reset_fn=_reset_watermark" in _pm)
    _ui = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent", "webui.py"),
               encoding="utf-8").read()
    ok("WebUI 有 POST /api/watermark/reset 这条路",
       'elif path == "/api/watermark/reset":' in _ui and "parent.watermark_reset_fn()" in _ui)
    _ch = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent", "console_html.py"),
               encoding="utf-8").read()
    ok("控制台有按钮并打这个接口（含二次确认）",
       'id="wmReset"' in _ch and "getJSON('/api/watermark/reset'" in _ch and "uiConfirm('重新对齐监听水位？" in _ch)

    # ── 第四轮审计 V-R4-12a / V-R4-7（P1）：**"读失败"与"0 / 没消息"必须分开** ──
    #   实测现场：`latest_seq` 读失败返回 0，而监听侧把它当起点写进水位 ⇒ 水位 0 ⇒ **从最旧历史重放**
    #   （审计实测群 A 441 条、首捞竟是 14.1 天前那 50 条），而且 0 水位**永不自愈**；
    #   `poll_new_messages` 读失败返回 []，与"确实没有新消息"不可区分 ⇒ `if not new: continue`
    #   无声空转（用户看到的就是"它不回话、日志什么都没有"）。
    print("\n-- I. 读失败 ≠ 0 / 没消息（V-R4-12a / V-R4-7） --")
    from agent import wechat as _wx                                              # noqa: E402

    class _BoomDB(object):
        def get_messages(self, *a, **k):
            raise RuntimeError("消息库读不出来（夹具）")

        def get_new_messages(self, *a, **k):
            raise RuntimeError("消息库读不出来（夹具）")

    class _OkDB(object):
        def get_messages(self, *a, **k):
            return [{"sort_seq": 4242}]

        def get_new_messages(self, *a, **k):
            return []

    _ad_boom = _wx.WeChatAdapter.__new__(_wx.WeChatAdapter)
    _ad_boom._db = _BoomDB()
    _ad_boom._cap = {}
    _ad_boom._ls_warn_at = 0
    _ad_boom._pn_warn_at = 0
    _okb, _seqb, _whyb = _ad_boom.latest_seq_ex("g")
    ok("① 读失败 ⇒ `latest_seq_ex` 回 ok=False（**不许**让调用方拿它当水位）",
       _okb is False and _seqb == 0 and bool(_whyb), (_okb, _seqb, _whyb[:40]))
    ok("① 读失败也**留下原因**（`_cap`，点击测试/日志看得见）",
       str(_ad_boom._cap.get("messages") or "").startswith("fail:"), _ad_boom._cap.get("messages"))
    ok("① 兼容接口 `latest_seq` 仍回 0（历史行为，展示类调用点依赖它）",
       _ad_boom.latest_seq("g") == 0)
    ok("② 读失败 ⇒ `poll_new_messages` 回 **None**（不是 []＝「没有新消息」）",
       _ad_boom.poll_new_messages("g", 0) is None)

    _ad_ok = _wx.WeChatAdapter.__new__(_wx.WeChatAdapter)
    _ad_ok._db = _OkDB()
    _ad_ok._cap = {}
    _okc, _seqc, _whyc = _ad_ok.latest_seq_ex("g")
    ok("③ 正常读 ⇒ ok=True + 真序号（阳性对照，别把正常路也堵了）",
       _okc is True and _seqc == 4242 and _whyc == "", (_okc, _seqc))
    ok("③ 正常读但没有新消息 ⇒ `poll_new_messages` 回空列表（与 None 分开）",
       _ad_ok.poll_new_messages("g", 0) == [])

    ok("④ 三处「定起点」都走 `latest_seq_ex`（源码），且全文件**没有**把 0 写进水位",
       "wm.set(_key0, _seq0)" in _pm and "wm.set(_k, _seqn)" in _pm
       and "wm.set(chat_key, _seqp)" in _pm
       and "wm.set(_key0, 0)" not in _pm and "wm.set(_k, 0)" not in _pm)
    ok("⑤ 监听循环里有「水位 0 ⇒ 对齐到最新 / 这一轮跳过」的自愈",
       "的水位是 0（起点没定下来）" in _pm and "水位是 0 且现在读不出最新序号" in _pm)
    ok("⑥ 监听循环对 `None` 有显式分支（读失败不再被当成「没有新消息」）",
       "if new is None:" in _pm and "读新消息失败（**不是**" in _pm)
    ok("⑥ 回退自愈也看 `ok`（读失败不许当成「库里最新是 0」）",
       "_okH and _latest and _cur and _latest < _cur" in _pm)
    # 反例锚：老写法必须被同一组判据判不合格（证明上面这些断言有灵敏度）
    _OLD_PM = ('    for g in targets:\n'
               '        if wm.get("group:" + g["wxid"], 0) <= 0:\n'
               '            try:\n'
               '                wm.set("group:" + g["wxid"], wechat.latest_seq(g["wxid"]))\n'
               '            except Exception:\n'
               '                wm.set("group:" + g["wxid"], 0)\n'
               '    new = wechat.poll_new_messages(wxid, wm.get(chat_key, 0), limit=50)\n'
               '    if not new:\n'
               '        continue\n')
    _old_bad = ("latest_seq_ex" not in _OLD_PM and "if new is None:" not in _OLD_PM
                and "wm.set(\"group:\" + g[\"wxid\"], 0)" in _OLD_PM)
    ok("⑦ 反例锚：老写法（吞成 0 当起点 + [] 当没消息）**确实**会被判不合格", _old_bad is True)

    # ── 第十轮 V-R10-30（P2）：**水位表账号维** · **flush 看返回值** · 切号放掉旧句柄 ──
    #   症状：切号后两号水位互相污染（A 号推到 900 ⇒ B 号 1~900 被判"处理过了"⇒ 静默不回）；
    #   `persist.atomic_write_json` 在目标被占用/真并发时必然 WinError 5，而 8 个调用点全丢返回值。
    print("\n-- J. 账号维 / flush 返回值 / 切号释放旧句柄（V-R10-30） --")
    _p_acct = os.path.join(tmp, "wm_acct.json")
    _wa = lw.Watermark(_p_acct, "acctA")
    _wa.set("group:x", 900)
    _ok_f1 = _wa.flush()
    _wb = lw.Watermark(_p_acct, "acctB")
    ok("① 换账号 ⇒ **读不到**另一个号的同一群水位（不再互相污染）",
       _ok_f1 is True and _wa.get("group:x") == 900 and _wb.get("group:x") == 0,
       (_wa.get("group:x"), _wb.get("group:x")))
    _wb.set("group:x", 7)
    _wb.flush()
    _wa2 = lw.Watermark(_p_acct, "acctA")
    _wb2 = lw.Watermark(_p_acct, "acctB")
    ok("② 两个账号的格子**同时留在文件里**，切回来各读各的",
       _wa2.get("group:x") == 900 and _wb2.get("group:x") == 7,
       (_wa2.get("group:x"), _wb2.get("group:x")))
    ok("③ 空账号（认不出账号）⇒ **沿用老键名**，单号机器行为一字不变",
       lw.Watermark(_p_acct).data.get("group:x") is None
       and lw.Watermark(_p_acct, "acctB")._ns("group:x") == "acctB|group:x"
       and lw.Watermark(_p_acct)._ns("group:x") == "group:x")
    _wa2.set_account("")
    ok("④ `set_account('')` 回到老命名空间（降级路径可回退）",
       _wa2.get("group:x") == 0 and _wa2._ns("k") == "k")
    _wa2.set_account("acctA")
    ok("④ 切回去仍读得到（set_account 只换命名空间、不丢数据）", _wa2.get("group:x") == 900)
    # flush 真失败：打桩 atomic_write_json ⇒ 必须回 False 并留痕（老写法丢返回值 ⇒ 静默）
    _p_bad = os.path.join(tmp, "wm_bad.json")
    _wbad = lw.Watermark(_p_bad, "acctA")
    _wbad.set("group:y", 5)
    _orig_awj = lw.persist.atomic_write_json
    _warned = []
    try:
        lw.persist.atomic_write_json = lambda *a, **k: False
        ok("⑤ 落盘失败 ⇒ `flush()` 回 **False**（不许静默说成功）", _wbad.flush() is False)
        ok("⑤ 失败留痕：`fail_count` 累加 + `last_error` 有话说",
           _wbad.fail_count == 1 and bool(_wbad.last_error), (_wbad.fail_count, _wbad.last_error))
        ok("⑤ `flush_checked` 回 False 且**不抛**（主循环不许被打断）",
           lw.flush_checked(_wbad, log=lambda lvl, fmt, *a: _warned.append(fmt % a if a else fmt),
                            why="判据") is False)
        ok("⑤ 且**告警一次**（用户/日志看得见，不许悄悄丢水位）",
           len(_warned) == 1 and "没写进磁盘" in _warned[0], _warned[:1])
        _w2bad = lw.Watermark(_p_bad, "acctA")
        _n_before = len(_warned)
        lw.persist.atomic_write_json = lambda *a, **k: True
        _w2bad.set("group:y", 5)
        ok("⑤ 阳性对照：改成写成功 ⇒ `flush_checked` 回 True 且**不告警**（别把正常路判失败）",
           lw.flush_checked(_w2bad, log=lambda lvl, fmt, *a: _warned.append(fmt % a if a else fmt),
                            why="判据") is True and len(_warned) == _n_before)
    finally:
        lw.persist.atomic_write_json = _orig_awj
    _pf = os.path.join(tmp, "wm_refuse.json")
    _wr = lw.Watermark(_pf, "acctA")
    _wr.set("group:z", 3)
    _wr._refuse_overwrite = True
    ok("⑥ 坏档留证失败 ⇒ flush 拒写也**计入 fail_count**（与真失败同一口径）",
       _wr.flush() is False and _wr.fail_count == 1 and "拒绝覆盖" in _wr.last_error)
    # 源码锚：三条接入路径只有 _adopt_wc 是唯一收口 ⇒ 它必须做全三件（释放旧句柄 / 切账号维 / 看返回值）
    _seg_adopt = _pm[_pm.index("def _adopt_wc(_wc_new"):]
    _seg_adopt = _seg_adopt[:_seg_adopt.index("while not orch.stopped")]
    ok("⑦ `_adopt_wc` 释放旧 adapter（含旧账号解密缓存）",
       _sm.has(_pm, "_release_adapter(_wc_old)") and _sm.has(_pm, "_clear_decrypted_cache"))
    ok("⑦ `_adopt_wc` 切水位表的账号命名空间（切号后两号不再共用一个格子）",
       _sm.has(_seg_adopt, "wm.set_account(_wm_account_of(_wc_new))"))
    ok("⑦ `_adopt_wc` 落盘看返回值、失败记进 `_ATTACH`",
       _sm.has(_seg_adopt, "listener_watermark.flush_checked(wm") and _sm.has(_seg_adopt, "_ATTACH["))
    ok("⑦ 启动那一刻就把水位表绑到当前账号（不是切号时才想起来）",
       _sm.has(_pm, "Watermark(_wm_path, _wm_account_of(wechat_box[0]))"))
    ok("⑦ 两路运行期接入（微信晚接入 / 切号跟随）都走 `_adopt_wc` 这一个收口",
       _pm.count("_adopt_wc(_wc_new)") >= 1 and _pm.count('_adopt_wc(_wc_sw, "切号跟随")') == 1)
    # 反例锚：老写法（无账号维 + 裸 flush）必须被上面这组判据判不合格
    _OLD_WM = ('class Watermark:\n'
               '    def __init__(self, path):\n'
               '        self.data = {}\n'
               '\n'
               '    def get(self, chat_key):\n'
               '        return self.data.get(str(chat_key), 0)\n'
               '\n'
               '    def flush(self):\n'
               '        persist.atomic_write_json(self.path, self.data)\n')
    _old_bad2 = ("self.account" not in _OLD_WM and "self._ns(" not in _OLD_WM
                 and "def flush(self):\n        persist" in _OLD_WM.replace("\r", ""))
    ok("⑧ 反例锚：老写法（无账号维 + flush 不看返回值）**确实**会被判不合格", _old_bad2 is True)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n== W2 水位判据：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
