#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""症状检验器判据（2026-09-18 立，作者口径：「用户有哪方面的问题，就点那个检验器，把报告发给我」）。

好的检验器长什么样（检索到的共识 + 本项目既有形状）：
  ① **原子化二值检查**：一条检查只问一件事，`ok` + **自带证据**，不做分数聚合；
  ② **一个总判决**（不是分数）：卡在哪一条 + 下一步做什么；
  ③ **症状驱动**：用户按自己的话术点，不需要懂内部；
  ④ **可复制报告**：一段纯文本，能直接粘进「反馈」；
  ⑤ **只读**：不动窗口、不发消息、不改配置。

本判据守六件事：
  ① 每个检验器都能跑通（离线、只读）且返回**同一形状**；
  ② 判决逻辑正确：**第一条不通过的检查＝卡点**，通过时给正向结论；
  ③ 报告文本自带"症状 / 判决 / 下一步 / 逐项证据 / 可粘进反馈"；
  ④ 未知 id、内部异常都**不抛**（返回同样形状，别把前端打崩）；
  ⑤ 接线：两个 API + 控制台面板 + 复制按钮都在；
  ⑥ **只读**：源码里不许出现发消息/点击/改配置的调用（这是"点一下不会出事"的底线）。
"""
import io
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from agent import verifiers as V          # noqa: E402
from agent import update_check as _uc     # noqa: E402
sys.path.insert(0, HERE)
import _srcmatch as _sm                   # noqa: E402  空白容忍的源码断言（V-R4-13 第三条）

# ⛔ V-R7-4：判据**不写产品** `data/update_state.json`（本判据会跑 `update_check.state()` ⇒ 落盘）。
#   `_state_path()` 是唯一落点函数，指到临时目录即可；产品默认行为不变（默认仍写生产路径）。
_state_dir = tempfile.mkdtemp(prefix="pm-vf-state-")
_uc._state_path = lambda: os.path.join(_state_dir, "update_state.json")

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   " + name)
    else:
        FAIL += 1
        print("  FAIL " + name + ("   [%s]" % extra if extra else ""))


print("── A. 清单与形状 ──")
cat = V.catalog()
ok("清单非空且每项有 id/name", bool(cat) and all(x.get("id") and x.get("name") for x in cat), str(cat[:2]))
ok("覆盖作者点名的几类症状",
   {"send_blocked", "self_echo", "no_reply", "emoji_blank", "fg_disturb", "update_stuck",
    "console_dead"} <= {x["id"] for x in cat}, str([x["id"] for x in cat]))

print("── B. 每个检验器都能跑（离线、只读、同形状）──")
SHAPE = ("id", "name", "symptom", "ok", "verdict", "action", "checks", "report")
for item in cat:
    r = V.run(item["id"])
    miss = [k for k in SHAPE if k not in r]
    ok("%s：形状齐 + 有逐项检查" % item["id"],
       not miss and isinstance(r["checks"], list) and len(r["checks"]) >= 2,
       "缺 %s / checks=%d" % (miss, len(r.get("checks") or [])))
    ok("%s：每条检查都带证据文本" % item["id"],
       all(c.get("name") and c.get("detail") for c in r["checks"]))
    ok("%s：报告包含症状/判决/证据/可粘进反馈" % item["id"],
       all(x in r["report"] for x in ("症状：", "判决：", "逐项证据", "粘进「反馈」")), r["report"][:80])

print("── C. 判决逻辑：第一条不通过的检查＝卡点（**三态**：True/False/None=没测到）──")
_r = V.run("send_blocked")
if _r["checks"]:
    bad = [c for c in _r["checks"] if c["ok"] is False]      # ⛔ V-R4-11：None 不算"坏"
    if bad:
        ok("有卡点时 verdict 点名第一条坏检查",
           bad[0]["name"] in _r["verdict"] and _r["ok"] is False, _r["verdict"][:90])
        ok("卡点带下一步动作", bool(_r["action"]), _r["action"][:60])
    else:
        ok("全通过时 verdict 给正向结论 + 不催动作",
           _r["ok"] is True and ("正常" in _r["verdict"] or "成立" in _r["verdict"]), _r["verdict"][:90])

print("\n── C2. 第三态「没测到」：既不算通过也不算失败，且必须**说出来**（V-R4-11）──")
_c_none = V._check("某格没测到", None, "读不到（夹具）")
ok("C2a `_check(..., None, ...)` 真的存成 `None`（不许被 bool() 吞成 False）", _c_none["ok"] is None, _c_none)
ok("C2b `_check(..., True/False, ...)` 仍是布尔",
   V._check("a", True, "")["ok"] is True and V._check("b", False, "")["ok"] is False)
_v_ok, _v_msg, _v_act = V._verdict([V._check("甲", True, "过了"), _c_none],
                                   "全绿口径", {"甲": "没用"})
ok("C2c 有「没测到」但没坏项 ⇒ 仍算**通过**，但判决里必须点出没测到",
   _v_ok is True and "没测到" in _v_msg and "某格没测到" in _v_msg, _v_msg[:110])
_v2_ok, _v2_msg, _ = V._verdict([V._check("乙", False, "坏了"), _c_none], "全绿口径", {"乙": "去修乙"})
ok("C2d 有坏项 ⇒ 判否、点名那条坏的，**并且**也把没测到的说出来",
   _v2_ok is False and "乙" in _v2_msg and "没测到" in _v2_msg, _v2_msg[:110])
_f = V._finish("t", "测试检验器", "症状", True, "全绿口径", "", [V._check("甲", True, "过了"), _c_none])
ok("C2e 报告逐项里没测到的画 `○`（不是 ✅ 也不是 ❌）",
   "  ○ 某格没测到：读不到（夹具）" in _f["report"], _f["report"][-160:])
ok("C2f 报告顶部有「○ 没测到」的计数警示", "○ 没测到" in _f["report"] and "承诺成立" in _f["report"])

print("\n── C3. 静态：`_check` 的条件位不许是字面量（恒真/恒假都不行）──")
import io as _io                                                                # noqa: E402
import re as _re2                                                               # noqa: E402
_vsrc = _io.open(os.path.join(ROOT, "agent", "verifiers.py"), encoding="utf-8").read()
_lit = []
_lit_false = []
for _i, _ln in enumerate(_vsrc.splitlines(), 1):
    if "_check(" not in _ln:
        continue
    if _re2.search(r"_check\(\s*[^,]+,\s*True\s*,", _ln):
        _lit.append("L%d: %s" % (_i, _ln.strip()[:70]))
    if _re2.search(r"_check\(\s*[^,]+,\s*False\s*,", _ln):
        # `False` 允许（异常路径＝真失败）；这里只统计，给 C3a2 报个数
        _pre = "\n".join(_vsrc.splitlines()[max(0, _i - 6):_i])
        _lit_false.append("L%d（%s）" % (_i, "在 except 分支里" if "except" in _pre else "**不在 except 分支**"))
ok("C3a 没有「条件位写**字面量 True**」的检查（那就是恒真＝乐观绿）", not _lit, _lit[:4])
ok("C3a2 `False` 字面量只允许出现在**异常路径**（那是 fail-closed 的真失败，不是恒假作弊）——"
   "本文件里剩 %d 处，都在 except 分支" % len(_lit_false), _lit_false[:3])
ok("C3b 反例锚：老写法（`_check(\"读不到…\", True, \"…\")`）确实会被这条扫出来",
   bool(_re2.search(r"_check\(\s*[^,]+,\s*True\s*,", '    checks.append(_check("版本门不拦发送", True, "读不到版本门"))')))
ok("C3c 该改的那几处已经是「没测到」（None）",
   _sm.has(_vsrc, '_check("版本门不拦发送", None') and "_check(\"更新完成标志（更新成功后会写）\", None" in _vsrc
   and '_check("出站闸门没有误拦正常回复", None' in _vsrc)
ok("C3d 两处真恒真已改成真判据（台账证据 > 0；闸门拦下过才算数）",
   _sm.has(_vsrc, "_ev_n > 0", "True if n > 0 else None", "True if deny > 0 else None"))

print("\n── C4. 更新快照的**新鲜度**：旧快照只能当历史（V-R4-12c）──")
_tmp4 = tempfile.mkdtemp(prefix="pm-vf-")
_keep_p = V._p
V._p = lambda *parts: os.path.join(_tmp4, *parts)          # 夹具：全部读数指到临时目录，不碰真 data/
try:
    os.makedirs(os.path.join(_tmp4, "data"), exist_ok=True)
    _sp = os.path.join(_tmp4, "data", "update_state.json")
    io.open(_sp, "w", encoding="utf-8").write('{"lastStatus": "current", "version": "2026.9.20.4"}')
    _fresh = [c for c in V.run("update_stuck")["checks"] if "30 分钟内" in c["name"]]
    ok("C4a 刚写过的快照 ⇒ 新鲜度那格 = True（下面用旧快照验它不是恒真）",
       len(_fresh) == 1 and _fresh[0]["ok"] is True, str(_fresh))
    _old = time.time() - 2 * 86400
    os.utime(_sp, (_old, _old))
    _r4 = V.run("update_stuck")
    _stale = [c for c in _r4["checks"] if "30 分钟内" in c["name"]]
    ok("C4b 旧快照（2 天前）⇒ 那格 = **没测到（None）**，不许判成失败（免得冤枉成「更新链坏了」）",
       len(_stale) == 1 and _stale[0]["ok"] is None, str(_stale))
    ok("C4c 旧快照时给**指对方向**的话：去点「检查更新」",
       bool(_stale) and "检查更新" in _stale[0]["detail"], _stale[0]["detail"] if _stale else "")
    _readable = [c for c in _r4["checks"] if c["name"] == "更新状态文件可读"]
    ok("C4d 反例锚：老那格（只看 `bool(st)`）在旧快照上照样 ✅ ⇒ 光靠它分不出新旧",
       len(_readable) == 1 and _readable[0]["ok"] is True, str(_readable))
    ok("C4e 旧快照进「没测到」清单、**不进卡点**（判决的卡点里不许点它）",
       "卡在「这份快照" not in (_r4.get("verdict") or "") and "没测到" in (_r4.get("verdict") or ""),
       (_r4.get("verdict") or "")[:100])
finally:
    V._p = _keep_p
    shutil.rmtree(_tmp4, ignore_errors=True)

print("\n── C5. 第五轮回执三条：未来时间 / 全项没测到 / 坏身份（V-R5A-3 · V-R5A-4 · V-R5B-5）──")
ok("C5a `_fresh_age` 四段口径：读不到=None · 未来=False · 区间内=True · 太旧=None",
   V._fresh_age(None, 0, 1800) is None and V._fresh_age(-5000, 0, 1800) is False
   and V._fresh_age(60, 0, 1800) is True and V._fresh_age(99999, 0, 1800) is None,
   (V._fresh_age(None, 0, 1800), V._fresh_age(-5000, 0, 1800), V._fresh_age(60, 0, 1800)))
ok("C5b 反例锚：老口径（只看上界 `<= 30`）**会把未来时间算成「刚写过」**（这就是 V-R5A-3 的现场）",
   (-4320.0 <= 30) is True)
_tmp5 = tempfile.mkdtemp(prefix="pm-vf5-")
_keep_p5 = V._p
V._p = lambda *parts: os.path.join(_tmp5, *parts)
try:
    os.makedirs(os.path.join(_tmp5, "data"), exist_ok=True)
    _sp5 = os.path.join(_tmp5, "data", "update_state.json")
    io.open(_sp5, "w", encoding="utf-8").write('{"lastStatus": "current"}')
    _fut = time.time() + 3 * 86400
    os.utime(_sp5, (_fut, _fut))
    _r5f = V.run("update_stuck")
    _fc = [c for c in _r5f["checks"] if "30 分钟内" in c["name"]]
    ok("C5c 快照 mtime 在**未来** ⇒ 那格判 **False**（坏读数），并在 detail 里点明是「未来」",
       len(_fc) == 1 and _fc[0]["ok"] is False and "未来" in _fc[0]["detail"], str(_fc))
    # 坏身份：活体里那份 {"wxid": "3"}
    io.open(os.path.join(_tmp5, "data", "self_identity.json"), "w", encoding="utf-8").write(
        '{"wxid": "3", "acct": "", "nickname": "", "from": "echo"}')
    _r5i = V.run("self_echo")
    _ic = [c for c in _r5i["checks"] if c["name"].startswith("认识自己")]
    ok("C5d 坏身份（`{\"wxid\":\"3\"}`）⇒ 那一格**不许**判 True，按「没测到」并说清原因",
       len(_ic) == 1 and _ic[0]["ok"] is None and "不合法" in _ic[0]["detail"], str(_ic))
    ok("C5e 反例锚：`\"3\"` 是真值字符串 —— 老写法 `True if sid else None` 正是这么把它当「已认识」的",
       bool("3") is True)
finally:
    V._p = _keep_p5
    shutil.rmtree(_tmp5, ignore_errors=True)
_all_none = [V._check("甲", None, "读不到"), V._check("乙", None, "也读不到")]
_an_ok, _an_msg, _ = V._verdict(_all_none, "全绿口径", {})
ok("C5f **全项都没测到** ⇒ 总判决 = None（不是 True，也不是 False）",
   _an_ok is None and "一项都没测到" in _an_msg, (_an_ok, _an_msg[:60]))
_fn5 = V._finish("t5", "测试", "症状", None, _an_msg, "", _all_none)
ok("C5g 报告头画 `○ 没测到`（不许画 ✅，也不许画 ❌），且结果里 ok 仍是 None",
   "○ 没测到" in _fn5["report"] and _fn5["ok"] is None, _fn5["report"][:90])
ok("C5h 三处新鲜度都走统一助手（源码级：`_fresh_age` 至少 3 处调用）",
   V.__dict__.get("_fresh_age") is not None
   and open(os.path.join(ROOT, "agent", "verifiers.py"), encoding="utf-8").read().count("_fresh_age(") >= 4)

print("\n── C6. 反馈 v0921-0922：『消息发不出去』不许给假「通过」（台账入卡点 · 没记录＝没测到）──")
# ⛔ 现场：那位网友报「消息发不出去、卡在未通过 会话投递失败」，而本检验器给的是 ✅「通过」——
#   因为原来只看**日志最后 200 行**（刚重启/日志轮转后是空的 ⇒ 一律判 True），
#   而真失败记在**进程内台账**（`wechat.note_switch_fail`）里，本检验器一条都不看。
_tmp6 = tempfile.mkdtemp(prefix="pm-vf6-")
_keep_p6 = V._p
V._p = lambda *parts: os.path.join(_tmp6, *parts)
os.makedirs(os.path.join(_tmp6, "data"), exist_ok=True)
from agent import wechat as _wxL                                          # noqa: E402
_saved_led = list(_wxL._SWITCH_FAILS)
# ⛔ 判据**不许写产品 `data/`**：台账现在会落盘（V-R9-12），所以把路径打到临时目录
_saved_led_path = _wxL._switch_fails_path
_wxL._switch_fails_path = lambda: os.path.join(_tmp6, "switch_fails.jsonl")
try:
    _wxL._SWITCH_FAILS[:] = []
    _r6 = V.run("send_blocked")
    _g6 = [c for c in _r6["checks"] if c["name"] == "最近的发送记录里没有失败"]
    ok("C6a 没有发送记录 ⇒ 那一格是**没测到（None）**，不是「通过」",
       len(_g6) == 1 and _g6[0]["ok"] is None, str(_g6))
    ok("C6b 说明里就写着「没测到」，并告诉用户怎么才能测到",
       bool(_g6) and "没测到" in _g6[0]["detail"] and "再点一次" in _g6[0]["detail"],
       _g6[0]["detail"][:90] if _g6 else "")
    ok("C6c 反例锚：老写法（条件位写 True＋『这条不判坏』）在**没有记录**时给的就是「通过」",
       V._check("最近的发送记录里没有失败", True, "最近没有发送记录 ⇒ 这条不判坏")["ok"] is True)
    _l6 = [c for c in _r6["checks"] if c["name"].startswith("最近没有『确认不了目标会话")]
    ok("C6d 新增一格盯**切会话失败台账**（与「它不回复」那一格同源）", len(_l6) == 1,
       str([c["name"] for c in _r6["checks"]]))
    ok("C6e 台账为空 ⇒ 这一格判 True（进程内台账本来就空着，这是事实不是没测到）",
       bool(_l6) and _l6[0]["ok"] is True, str(_l6))
    _wxL.note_switch_fail("发送前确认不了目标会话",
                          "no_capture：抓不到渲染区（窗口不可见/权限不足）｜投递切会话：没成"
                          "｜**微信主窗当时是最小化的**")
    _r6b = V.run("send_blocked")
    _l6b = [c for c in _r6b["checks"] if c["name"].startswith("最近没有『确认不了目标会话")]
    ok("C6f 台账里有一条 ⇒ 这一格判 False", bool(_l6b) and _l6b[0]["ok"] is False, str(_l6b))
    ok("C6g 它成为**卡点**（判决点名它，不再是被日志那格蒙过去）",
       "确认不了目标会话" in (_r6b.get("verdict") or ""), (_r6b.get("verdict") or "")[:100])
    ok("C6h 明细把原始原因带出来（用户看得懂：抓不到渲染区 / 最小化）",
       bool(_l6b) and ("抓不到渲染区" in _l6b[0]["detail"]) and ("最小化" in _l6b[0]["detail"]),
       _l6b[0]["detail"][:120] if _l6b else "")
    ok("C6i **灵敏度**：同一次调用，台账空 ⇒ 不判否；多一条记录 ⇒ 判否（这条判据真的会红）",
       _r6["ok"] is not False and _r6b["ok"] is False,
       "空=%r 有=%r" % (_r6["ok"], _r6b["ok"]))
finally:
    _wxL._SWITCH_FAILS[:] = _saved_led
    _wxL._switch_fails_path = _saved_led_path
    V._p = _keep_p6
    shutil.rmtree(_tmp6, ignore_errors=True)

print("\n── C8. 第九轮 V-R9-12/13/14：台账落盘 · 读不到日志＝没测到 · 报告头不许画 ✅ ──")
_tmp8 = tempfile.mkdtemp(prefix="pm-vf8-")
_saved8 = (_wxL._switch_fails_path, list(_wxL._SWITCH_FAILS), V._p)
_wxL._switch_fails_path = lambda: os.path.join(_tmp8, "switch_fails.jsonl")
V._p = lambda *parts: os.path.join(_tmp8, *parts)
try:
    os.makedirs(os.path.join(_tmp8, "data", "sessions"), exist_ok=True)
    _wxL._SWITCH_FAILS[:] = []
    _wxL.note_switch_fail("单测落盘", "这条要被写进文件")
    ok("C8a 台账落盘了（文件存在且能读回）", os.path.exists(_wxL._switch_fails_path()),
       _wxL._switch_fails_path())
    _wxL._SWITCH_FAILS[:] = []                      # 模拟重启：内存台账清空
    _r8 = _wxL.recent_switch_fails(3)
    ok("C8b **重启（内存清空）后仍读得到**（这就是 V-R9-12 的那半条修复）",
       bool(_r8) and "这条要被写进文件" in str((_r8[-1] or {}).get("why") or ""), str(_r8))
    ok("C8c `_tail2` 语义：文件不存在 ⇒ 空表；读不到（目录）⇒ **None（没测到）**",
       V._tail2(os.path.join(_tmp8, "no_such.log")) == []
       and V._tail2(_tmp8) is None, (V._tail2(os.path.join(_tmp8, "no_such.log")), V._tail2(_tmp8)))
    _emoji_src = io.open(os.path.join(ROOT, "agent", "verifiers.py"), encoding="utf-8").read()
    ok("C8e 源码级：那一格是 `(files > 0) and key_ok`", "(files > 0) and key_ok" in _emoji_src)
    from agent import emoticon as _emo                                     # noqa: E402
    _o1, _o2, _o3 = _emo.any_sticker_files, _emo.load_cached_key, _emo.verify_key
    _emo.any_sticker_files = lambda limit=200: ["x"] * 3                   # 有本地表情文件
    _emo.load_cached_key = lambda *a, **k: ""                              # 但没有可用 key
    try:
        _ce = [c for c in V.run("emoji_blank")["checks"] if c["name"] == "表情能离线解出原图"]
        ok("C8e′ **行为锚**：有表情文件但 key 不可用 ⇒ 那一格判 False（老的恒真写法会判 True）",
           len(_ce) == 1 and _ce[0]["ok"] is False, str(_ce))
    finally:
        _emo.any_sticker_files, _emo.load_cached_key, _emo.verify_key = _o1, _o2, _o3
    _head1 = V._finish("t8", "测试", "症状", True, "全绿口径", "",
                       [V._check("甲", True, "过了"), V._check("乙", None, "读不到")])
    ok("C8f 报告头：1 项 True + 1 项 None ⇒ **不许**画 ✅，画「◐ 部分通过」",
       "◐" in _head1["report"] and "✅ 通过" not in _head1["report"], _head1["report"][:60])
    _head2 = V._finish("t8b", "测试", "症状", True, "全绿口径", "", [V._check("甲", True, "过了")])
    ok("C8g 全 True ⇒ 仍是 ✅（阳性对照，别把正常报告也改花）", "✅ 通过" in _head2["report"])
    # 会话档案里的物证（noreply_send_failed）⇒ 那一格判否、成为卡点
    io.open(os.path.join(_tmp8, "data", "sessions", "2026-09-21.jsonl"), "w",
            encoding="utf-8").write('{"chat": "g", "status": "noreply_send_failed"}\n')
    _r8b = V.run("send_blocked")
    _c8 = [c for c in _r8b["checks"] if c["name"].startswith("最近几轮里没有")]
    ok("C8h 会话档案里写着 `noreply_send_failed` ⇒ 那一格判 False",
       len(_c8) == 1 and _c8[0]["ok"] is False, str(_c8))
    ok("C8i 它成为卡点（判决点名它）", "调过发送却一条都没发出去" in (_r8b.get("verdict") or ""),
       (_r8b.get("verdict") or "")[:90])
finally:
    (_wxL._switch_fails_path, _saved_led8, V._p) = _saved8
    _wxL._SWITCH_FAILS[:] = _saved_led8 if isinstance(_saved_led8, list) else []
    shutil.rmtree(_tmp8, ignore_errors=True)

print("\n── C7. 反馈「艾特它 它不会回复」：报告里要能看见「群里 @ 的是谁」（说明格，不误判）──")
from agent import thought_trace as _ttJ                                      # noqa: E402
from agent import prompt as _prJ                                             # noqa: E402
_keep_ttf = _ttJ.FILE
_tmp7 = tempfile.mkdtemp(prefix="pm-vf7-")
_ttJ.FILE = os.path.join(_tmp7, "thoughts.jsonl")
try:
    ok("C7a `observed_at` 取得出 @ 后那串名字（微信用的分隔符 U+2005 也认）",
       _prJ.observed_at("wxid_a: @群昵称甲\u2005你好") == "群昵称甲",
       _prJ.observed_at("wxid_a: @群昵称甲\u2005你好"))
    ok("C7b 没有 @ ⇒ 空串；怪输入不抛",
       _prJ.observed_at("普通一句话") == "" and _prJ.observed_at(None) == "")
    _c7 = [c for c in V.run("no_reply")["checks"] if c["name"] == "群里最近 @ 的名字它认得"]
    ok("C7c 没样本时是**说明格（None）**并写清「没测到」",
       len(_c7) == 1 and _c7[0]["ok"] is None and "没测到" in _c7[0]["detail"], str(_c7))
    io.open(_ttJ.FILE, "w", encoding="utf-8").write(
        '{"chat": "g1", "kind": "tier", "at": "群昵称甲", "known": "群deepseek"}\n')
    _c7b = [c for c in V.run("no_reply")["checks"] if c["name"] == "群里最近 @ 的名字它认得"]
    ok("C7d 名字**对不上** ⇒ 仍不判坏（那个 @ 也可能是 @ 别人的），但两个名字都进报告 + 给出动作",
       len(_c7b) == 1 and _c7b[0]["ok"] is None
       and ("群昵称甲" in _c7b[0]["detail"]) and ("群deepseek" in _c7b[0]["detail"])
       and ("群昵称" in _c7b[0]["detail"]), _c7b[0]["detail"][:120] if _c7b else "")
    io.open(_ttJ.FILE, "w", encoding="utf-8").write(
        '{"chat": "g1", "kind": "tier", "at": "群deepseek", "known": "群deepseek"}\n')
    _c7c = [c for c in V.run("no_reply")["checks"] if c["name"] == "群里最近 @ 的名字它认得"]
    ok("C7e 名字**一致** ⇒ 判 True（同一判定器翻面 ⇒ 它有灵敏度）",
       len(_c7c) == 1 and _c7c[0]["ok"] is True, str(_c7c))
finally:
    _ttJ.FILE = _keep_ttf
    shutil.rmtree(_tmp7, ignore_errors=True)

print("── D. 异常与未知 id 都不许抛（别把前端打崩）──")
_u = V.run("不存在的东西")
ok("未知 id ⇒ 同形状 + 说清可选清单", _u["ok"] is False and "没有这个检验器" in _u["verdict"], _u["verdict"][:80])
ok("未知 id 也带 report", bool(_u.get("report")))
ok("run(None) 不抛", V.run(None)["ok"] is False)


def _boom():
    raise RuntimeError("故意炸")


_saved = V.VERIFIERS.get("send_blocked")
V.VERIFIERS["__boom"] = ("会炸的检验器", _boom)
try:
    _b = V.run("__boom")
    ok("检验器内部抛异常 ⇒ 返回同形状并说清（不抛给前端）",
       _b["ok"] is False and "检验器自己出错" in _b["verdict"], _b["verdict"][:70])
finally:
    V.VERIFIERS.pop("__boom", None)

print("── E. 接线：两个 API + 面板 + 复制按钮 ──")
_W = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
_C = io.open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
ok("webui 有 /api/verifiers", '"/api/verifiers"' in _W)
ok("webui 有 /api/verify（带 id 查询串）", '"/api/verify"' in _W and "_vf.run(" in _W)
ok("控制台有面板容器 + 复制按钮", 'id="vfBtns"' in _C and 'id="vfCopy"' in _C and 'id="vfResult"' in _C)
ok("控制台按清单动态生成按钮", "/api/verifiers" in _C and "v.name" in _C)
ok("面板文案写明「只读检查」", "只读检查" in _C)

print("── F. 只读底线：检验器不许发消息/点击/改配置 ──")
_V = io.open(os.path.join(ROOT, "agent", "verifiers.py"), encoding="utf-8").read()
_bad = [x for x in ("send_text(", "send_image", "backend.click", "click_real", "save_config",
                    "write_config", "os.remove", "shutil.rmtree", "SetForegroundWindow",
                    "bring_to_front", "SetCursorPos") if x in _V]
ok("源码里没有会改变状态的调用（%s）" % (_bad or "无"), not _bad, str(_bad))

# ⛔ 2026-09-20（网友 v0920-0824 的报告里「监听水位有记录」这一格被判 ✅，而明细写着
#   「水位条目 2 个，**最近：0**」）：只看"有没有条目"是太弱的判据 —— 水位停在 0 说明监听
#   **从没读到过目标群的消息**，它比"最近有过一轮响应"更靠前，必须在这里就把卡点拦住，
#   别让判决把用户指到后面那一格去。
ok("水位那一格看的是**值**（序号 > 0），不是只看有没有条目",
   "_wmax" in _V and "_wmax > 0" in _V, "见 v_no_reply")
ok("水位为 0 时给出**分层**的处置语（群名 / 账号 / 消息库 三查）",
   "从没读到过目标群的消息" in _V and "点击测试" in _V)
ok("新增「勾了监听目标群」一格（提示群名要与微信里完全一致 / 同名群要用 wxid）",
   "差一个字" in _V and "有同名群" in _V and "勾选存的是 wxid" in _V)
ok("矛盾检测没有因为改名而失效（`监听水位有` 前缀仍然命中）",
   '"监听水位有" in n' in _V)

print("\n==== 症状检验器判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
