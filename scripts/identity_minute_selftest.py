#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据：**时间类的身份档必须能分辨"两个会话同一分钟都在说话"**。

跑法： runtime\\python\\python.exe scripts\\identity_minute_selftest.py   退出码 0=全过 / 1=有失败

起因（三个真机文件：`persona_morph.log` / `chat_headers.json` / `listener_watermark.json`）：
  ① 他两个群（**「KC」和「测试」**）的最后一条消息相差 **13 秒**（watermark 1789879968000 /
     1789879981000）⇒ **同一分钟**；而"用时间认身份"的三档（`_row_time_conflict` 负判据 /
     `_active_row_time_ok` 正判据 / `_pane_time_hits`）全是**分钟级**的 ⇒ 失去区分力；
     其中正判据的"第二道证据（聊天区里出现同一 HH:MM）"属于**当前开着的那个会话**⇒ 循环论证。
  ② 他两个群的会话头指纹 `similarity` = **0.9480 ≥ 0.90**（同一会话是 1.0000，跟 filehelper 是
     0.8783）⇒ 0.90 这个阈值对**短群名**没有安全间隔；而形状（余弦）把这三者拉开成
     1.0000 / 0.8635 / 0.6471。
  ③ 他库里 `10000000001@chatroom` 的 `1716x900` 参照 = `[0]*63 + [255]`（**只有最右边一维有值**），
     同一会话其它四个尺寸逐字节相同且正常 ⇒ 那是**退化参照**，`is_blank` 抓不住它（极差 255、最暗列 0）。

本判据守四件事：
  A 指纹：两个条件（幅度相似 ≥0.90 且 形状一致 ≥0.95）；同一会话（含 ±噪声）仍要过；
  B 参照：退化指纹**不许入库**、也不许拿来判（真机那条形状当夹具）；
  C 时间档：同一分钟有别人 ⇒ 三档**一律不给正面结论**（fail-closed）；只有它一个 ⇒ 行为不变；
  D 反例锚：把老行为（不查同分钟 / 只看 similarity）摆出来，必须被判不合格。
"""
from __future__ import annotations

import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))) # 同目录的 `_srcmatch`
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

from agent import chat_header as CH # noqa: E402
from agent import chat_ocr as CO # noqa: E402
from agent.wechat import WeChatAdapter # noqa: E402
from _srcslice import func_src as _fsrc # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


# ── 真机夹具：网友 v0919 的 `chat_headers.json` 原文（1160x900 那一档）──────────────
FP_A = [0, 0, 146, 243, 182, 243, 162, 255, 109, 101, 121, 182, 101,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
FP_B = [0, 0, 255, 173, 207, 80, 27, 191, 143, 93, 175, 80, 186,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
FP_BAD = [0] * 63 + [255] # 他库里 10000000001@chatroom / 1716x900 那条（退化参照）
FP_HELPER = [0, 0, 102, 162, 133, 112, 110, 173, 82, 178, 143, 82, 136, 255, 173, 144, 163, 153,
             92, 102, 143, 61, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
             0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

A_ID, B_ID = "10000000001@chatroom", "10000000002@chatroom"


def main():
    print("── A. 指纹：两个条件（幅度 + 形状）──")
    s_ab, c_ab = CH.similarity(FP_A, FP_B), CH.pattern_score(FP_A, FP_B)
    ok("A1 真机实测值复现：两个短群名 similarity≈0.9480、形状≈0.8635",
       abs(s_ab - 0.9480) < 0.002 and abs(c_ab - 0.8635) < 0.002, "sim=%.4f cos=%.4f" % (s_ab, c_ab))
    ok("A2 这两个群**不许**被判成同一个会话", CH.match(FP_A, FP_B) is False, "sim=%.4f cos=%.4f" % (s_ab, c_ab))
    ok("A3 反例锚：老判据（只看 similarity ≥ 0.90）**会**把它们判成同一个（＝这条判据有灵敏度）",
       CH.similarity(FP_A, FP_B) >= CH.DEFAULT_THRESHOLD,
       "老阈值为 0.90，实测 %.4f" % s_ab)
    ok("A4 同一会话（逐字节相同）仍然匹配", CH.match(FP_A, list(FP_A)) is True,
       "sim=%.4f cos=%.4f" % (CH.similarity(FP_A, FP_A), CH.pattern_score(FP_A, FP_A)))
    _noisy = [max(0, min(255, x + (3 if i % 2 else -3))) for i, x in enumerate(FP_A)]
    ok("A5 轻微噪声（±3，模拟再次抓帧）也仍然匹配 —— 别把正常路堵了",
       CH.match(FP_A, _noisy) is True,
       "sim=%.4f cos=%.4f" % (CH.similarity(FP_A, _noisy), CH.pattern_score(FP_A, _noisy)))
    ok("A6 filehelper 与这两个群都不匹配", CH.match(FP_HELPER, FP_A) is False and CH.match(FP_HELPER, FP_B) is False,
       "cos(A)=%.4f" % CH.pattern_score(FP_HELPER, FP_A))

    print("\n── B. 参照：退化指纹不许入库、不许拿来判 ──")
    ok("B1 真机那条 `[0]*63+[255]` 必须被判退化", bool(CH.degenerate_reason(FP_BAD)),
       CH.degenerate_reason(FP_BAD))
    ok("B2 反例锚：`is_blank` **抓不住**它（极差 255、最暗列 0 都过）——这就是它当年进库的原因",
       CH.is_blank(FP_BAD) is False, "is_blank=%s" % CH.is_blank(FP_BAD))
    ok("B3 正常参照（真机 1160x900 那两条）不许被误判成退化",
       CH.degenerate_reason(FP_A) == "" and CH.degenerate_reason(FP_B) == "",
       (CH.degenerate_reason(FP_A), CH.degenerate_reason(FP_B)))
    ok("B4 最小化那种全 255 也算退化（有起伏判据抓不到它，但这条能）",
       bool(CH.degenerate_reason([255] * 64)), CH.degenerate_reason([255] * 64))
    _tmp = tempfile.mkdtemp(prefix="pm-hdr-")
    _p = os.path.join(_tmp, "chat_headers.json")
    CH.remember(A_ID, FP_BAD, path=_p, size="1716x900")
    _d = CH.load(_p)
    ok("B5 `remember()` 拒绝写入退化指纹（库里不该出现那条）",
       not (((_d.get(A_ID) or {}).get("sizes") or {}).get("1716x900")),
       str(list(((_d.get(A_ID) or {}).get("sizes") or {}).keys())))
    CH.remember(A_ID, FP_A, path=_p, size="1160x900")
    ok("B6 正常指纹照常能写入（门口没堵死）",
       bool(((( CH.load(_p).get(A_ID) or {}).get("sizes") or {}).get("1160x900") or {}).get("fp")))
    ok("B7 退化判据对同一个会话的其它尺寸不误伤（真机那四条逐字节相同的参照都正常）",
       all(CH.degenerate_reason(FP_A) == "" for _ in range(1)) and CH.degenerate_reason(FP_A) == "")

    print("\n── C. 时间档：同一分钟有别人 ⇒ 一律不给正面结论 ──")
    ad = _fake_adapter()
    H_MINE = "16:52"
    _rivals_ab = ad._same_minute_rivals("group:" + A_ID, H_MINE)
    ok("C1 两个会话同一分钟（真机差 13 秒的那个现场）⇒ 认出那个「别人」",
       bool(_rivals_ab), _rivals_ab)
    ok("C2 只有目标一个会话今天有消息 ⇒ 没有别人（正常路不受影响）",
       ad._same_minute_rivals("group:" + A_ID, "09:00") == [], ad._same_minute_rivals("group:" + A_ID, "09:00"))
    with _stub_ocr(pane=""):
        _okt, _whyt = ad._active_row_time_ok("group:" + A_ID, gui=object())
    ok("C3 有别人同分钟 ⇒ 活动行时间档**不给正面结论**（False + 说清是谁在抢）",
       _okt is False and "同一分钟" in str(_whyt), _whyt)
    with _stub_ocr(pane=""):
        _cf, _cfwhy, _cdec, _ccmp = ad._row_time_conflict("group:" + A_ID, gui=object())
    ok("C4 有别人同分钟 ⇒ 负判据也**给不出结论**（decided=False，调用方按判不了处理）",
       _cf is False and _cdec is False and "同一分钟" in str(_cfwhy), (_cf, _cdec, _cfwhy))
    with _stub_ocr(pane="16:52"):
        _okp, _whyp, _hits = ad._pane_time_hits("group:" + A_ID, "16:52")
    ok("C5 有别人同分钟 ⇒ 聊天区时间档同样不给结论（那点时间属于**当前开着的**会话）",
       _okp is False and "同一分钟" in str(_whyp), _whyp)
    # 反例锚：把"同分钟核对"摘掉（＝老行为）⇒ 同一夹具下这三档又都成立
    _keep = ad._same_minute_rivals
    ad._same_minute_rivals = lambda *a, **k: []
    try:
        with _stub_ocr(pane="16:52"):
            _okp2, _whyp2, _ = ad._pane_time_hits("group:" + A_ID, "16:52")
        with _stub_ocr(pane=""):
            _okt2, _whyt2 = ad._active_row_time_ok("group:" + A_ID, gui=object())
    finally:
        ad._same_minute_rivals = _keep
    ok("C6 反例锚：**把这道门摘掉**，同一夹具下时间档又放行了（证明 False 是这道门给的，不是别的原因）",
       _okp2 is True and _okt2 is True, (_okt2, _whyt2, _okp2, _whyp2))

    print("\n── H. 同分钟冲突：用「绿底行」做屏幕侧收敛（2026-09-30 新增）──")
    # 起因：C 段那三档**只查 DB 时间、完全不看屏幕** ⇒ 同分钟冲突只能整档弃权。
    # 但屏幕上**只有一个绿底行**（当前打开的那个，实测占比 0.90~1.00 vs 其余 0.00），
    # 而"机器人刚发完消息、会话还开着" ⇒ 绿底行就是它刚操作的那个会话。
    # 约束：**只做收敛，不做放行依据**（授权口径独立，不许因本条放宽）。
    _ad3 = _fake_adapter()
    # ⚠️ `display_name` 收的是**裸 wxid**（`_same_minute_rivals` 内部 `ck.split(":",1)[-1]`），
    #    不是 `group:` 前缀的完整 chat_key —— 夹具要按真实口径写（踩过：写错前缀 ⇒ 名字取空）。
    _nm3 = {A_ID: "测试", B_ID: "KC"}
    _ad3.display_name = lambda cid, *a, **k: _nm3.get(str(cid).split(":", 1)[-1], "")
    _rivals3 = _ad3._same_minute_rivals("group:" + A_ID, H_MINE)
    ok("H0 先确认夹具确有冲突、且 rival **带上了名字**（不然 H1/H2 是空跑）",
       len(_rivals3) == 1 and "KC" in str(_rivals3[0]) and B_ID in str(_rivals3[0]), _rivals3)

    # ① 绿底行＝目标 ⇒ 冲突消失（返回 []）
    _hl_target = {"y_abs": 100, "score": 0.97, "name": "测试"}
    _keep_hl = CO.highlight
    try:
        CO.highlight = lambda img: _hl_target
        _r = _ad3._same_minute_rivals("group:" + A_ID, H_MINE, img=object(), name="测试")
    finally:
        CO.highlight = _keep_hl
    ok("H1 绿底行名字＝目标 ⇒ 收敛成「无竞争」（`[]`，时间档可给结论）", _r == [], _r)

    # ② 绿底行＝某个 rival（不是目标）⇒ 收敛到只剩它（判否，且点名更准）
    try:
        CO.highlight = lambda img: {"y_abs": 100, "score": 0.97, "name": "KC"}
        _r2 = _ad3._same_minute_rivals("group:" + A_ID, H_MINE, img=object(), name="测试")
    finally:
        CO.highlight = _keep_hl
    ok("H2 绿底行名字＝某个 rival ⇒ 收敛到只剩它（仍判否，但点名更准）",
       len(_r2) == 1 and "KC" in str(_r2[0]), _r2)

    # ②b 绿底行名字＝rival 但 rivals 里**没有名字**（只有裸 key）⇒ 不收敛（无名字无法做身份匹配）
    try:
        CO.highlight = lambda img: {"y_abs": 100, "score": 0.97, "name": "KC"}
        _r2b = _ad3._converge_by_highlight(object(), "group:" + A_ID, "测试",
                                            ["group:" + B_ID])
    finally:
        CO.highlight = _keep_hl
    ok("H2b rival 只有裸 key（无名字）⇒ **不收敛**（无名字不许拿 key 去猜）", _r2b is None, _r2b)

    # ③ 绿底行名字读不出 ⇒ **原样返回**（fail-closed 不变）
    for _tag, _hl in (("读不出（空名）", {"y_abs": 100, "score": 0.97, "name": ""}),
                      ("绿底太弱（帧糊）", {"y_abs": 100, "score": 0.12, "name": "测试"}),
                      ("量不到绿底行", None),
                      ("名字是聊天区垃圾", {"y_abs": 100, "score": 0.97,
                                     "name": "article/intertextuality-and-parallelomania"})):
        try:
            CO.highlight = (lambda img, _v=_hl: _v)
            _r3 = _ad3._same_minute_rivals("group:" + A_ID, H_MINE, img=object(), name="测试")
        finally:
            CO.highlight = _keep_hl
        ok("H3 %s ⇒ 不收敛，原样返回 %d 个 rival（fail-closed 不变）" % (_tag, len(_rivals3)),
           _r3 == _rivals3, _r3)

    # ④ 不传 img（老调用方式）⇒ 行为与改动前**逐字一致**（不许因为新参数改变默认）
    _r4 = _ad3._same_minute_rivals("group:" + A_ID, H_MINE)
    ok("H4 不传 `img` ⇒ 与改动前逐字一致（新参数不改变默认行为）", _r4 == _rivals3, _r4)

    # ⑤ 反例锚：收敛结果**不许**被当成"授权放行"（`_same_minute_rivals` 返回 [] 只表示"没有别人在抢"，
    #    "能不能发"仍要 matches_strict/matches_fuzzy —— 这两件事不许混）。
    _WT2 = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
    ok("H5 源码锚：收敛只改 `_same_minute_rivals` 的返回值，**不动**授权档",
       "def _converge_by_highlight(" in _WT2 and "只做收敛、不做放行依据" in _WT2, "")

    # ⑥ ★★放宽方向的反例（收紧「匹配」口径）：绿底行只是**包含**目标名 ⇒ **不许**返回 []
    #   老实现 `_hit` 是 `matches_strict(...) 或 matches(...)`，而 `matches` 是"互相包含即算"
    #   （它的设计用途是"找行"）⇒ 这四个场景**全部**被误判成"绿底行＝目标" ⇒ 返回 `[]`
    #   ＝"没有 rival 在抢" ⇒ 把竞争告警**丢掉** ⇒ 下游给 `decided=True`「确定是它」⇒ 可能发错。
    #   实测（scripts/probe_converge_overwide.py）：改前 10 通过 / **4 失败**（就是下面这四条），
    #   改后 10/0 ⇒ 把这四条固化进回归，别再退回去。
    for _tag6, _name6, _got6 in (
            ("名字含目标（测试机器人）", "测试", "测试机器人"),
            ("撞名群（测试2）", "测试", "测试2"),
            ("目标名是子串（公测测试组）", "测试", "公测测试组"),
            ("单字目标「E」+「Editor」", "E", "Editor")):
        try:
            CO.highlight = (lambda img, _v=_got6: {"y_abs": 100, "score": 0.97, "name": _v})
            _r6 = _ad3._converge_by_highlight(object(), "group:" + A_ID, _name6,
                                              ["KC（group:%s）" % B_ID])
        finally:
            CO.highlight = _keep_hl
        ok("H6 ★反例：绿底行「%s」≠ 目标「%s」（只是包含）⇒ **不许**返回 []"
           "（丢掉 rival 告警 ＝ 放宽方向）" % (_got6, _name6), _r6 != [], _r6)

    # ⑦ 与 H6 对称：绿底行**完全相等**时**必须**仍能收敛成"无竞争"（不许因收紧而全盘失效）
    try:
        CO.highlight = lambda img: {"y_abs": 100, "score": 0.97, "name": "测试"}
        _r7 = _ad3._converge_by_highlight(object(), "group:" + A_ID, "测试",
                                          ["KC（group:%s）" % B_ID])
    finally:
        CO.highlight = _keep_hl
    ok("H7 ★正例锚：绿底行**完全相等**于目标 ⇒ 仍收敛成 []（收紧只砍「模糊包含」那一支，"
       "不许把正常的也砍掉）", _r7 == [], _r7)

    # ⑧ 源码锚：`_hit` 只认授权档口径
    ok("H8 源码锚：`_converge_by_highlight` 的匹配只用 `matches_strict`（授权档），不再用 `matches`",
       "return bool(_co.matches_strict(got, t))" in _WT2, "")

    print("\n── I. B2 修正：候选集扩到**全量会话** + 「唯一」只在全量时才算数（2026-09-30 新增）──")
    # 起因：`_monitored_chat_ids()` 只给"白名单∩群列表＋私聊目标" ⇒ **名单外的会话看不见**。
    # 实测（scripts/probe_b2_realdata.py，本机真库）：碰撞窗口里含"名单外"会话占 **99%~100%**。
    # ⇒ 修法二（扩集）治本、修法三（空集不许当"唯一"）治标。
    _C_ID = "66000000111@chatroom"   # ← **不在白名单里**的群（B2 的关键角色）
    _ad4 = _fake_adapter()
    _nm4 = {A_ID: "测试", B_ID: "KC", _C_ID: "野群"}
    _ad4.display_name = lambda cid, *a, **k: _nm4.get(str(cid).split(":", 1)[-1], "")
    # 全量侧：只有"野群"与本分钟同时间（目标 A 也在本分钟，但它自己不算 rival）
    _ad4._all_chat_last_hhmm = lambda: {_C_ID: H_MINE}

    # I1 扩集**生效**：名单外的"野群"被补进候选（老实现会漏掉它）
    _r4 = _ad4._same_minute_rivals("group:" + A_ID, H_MINE)
    ok("I1 扩集生效：**名单外**的同分钟会话被补进候选（老实现只查白名单 ⇒ 漏检）",
       any(_C_ID in str(x) for x in _r4) and len(_r4) >= 2, _r4)

    # I2 扩集**不重复**：白名单侧的 rival（KC）不会被全量侧再加一遍
    _dup = [str(x) for x in _r4]
    ok("I2 扩集不重复：白名单侧已有的 rival 不被全量侧再加一遍",
       len(_dup) == len(set(_dup)), _dup)

    # I3 全量侧**拿不到**（DB 读失败）⇒ 退化成老行为（仍是 fail-closed，不许崩）
    _ad5 = _fake_adapter()
    _ad5.display_name = lambda cid, *a, **k: _nm4.get(str(cid).split(":", 1)[-1], "")
    _ad5._all_chat_last_hhmm = lambda: {}
    _r5 = _ad5._same_minute_rivals("group:" + A_ID, H_MINE)
    ok("I3 全量侧拿不到 ⇒ 退化成「只看白名单」，且仍是 fail-closed（不崩、不误放）",
       len(_r5) == 1 and B_ID in str(_r5[0]), _r5)

    # I4 ★修法三：**全量扩集成功** + 本分钟**确实只有目标** ⇒ `decided=True`（这回是诚实的）
    #    ⚠️ 夹具要点：必须让"白名单侧"和"全量侧"**都没有**同分钟会话 —— 否则 is4 会因
    #       "真有 rival" 而返回 decided=False（那是正确行为，不是 bug）。踩过这个坑。
    _ad6 = WeChatAdapter.__new__(WeChatAdapter)
    _ad6._db = _FakeDB({A_ID: [time_now_floor()], "wxid_solo": [time_now_floor() - 7 * 60]})
    _ad6.cfg = {"wechat": {"group_name_white_list": ["测试"]}}
    _ad6._mon_cache = None
    _ad6.list_groups = lambda: [{"name": "测试", "wxid": A_ID}]
    _ad6.list_private_targets = lambda: [{"name": "独聊", "wxid": "wxid_solo"}]
    _ad6.display_name = lambda cid, *a, **k: _nm4.get(str(cid).split(":", 1)[-1], "")
    _ad6._all_chat_last_hhmm = lambda: {A_ID: H_MINE, "wxid_else": "16:00"}  # 别人都不在本分钟
    # ⚠️ 夹具必须给 `_gui` + 桩掉 `_get_gui`：草稿行兜底分支会调 `_get_gui()`，
    #    不桩就会去构造**真 GUI**（踩过：日志里冒出真 WeChatGUI 初始化）
    _ad6._gui = None
    _ad6._get_gui = lambda *a, **k: None
    with _stub_ocr(pane=""):
        _cf, _why, _dec, _cmp = _ad6._row_time_conflict("group:" + A_ID)
    ok("I4 全量扩集**成功** + 本分钟只有目标 ⇒ `decided=True`（「确定是它」这回是诚实的）",
       _dec is True, "%s / %s" % (_dec, _why))

    # I5 ★修法三的反面：**全量扩集拿不到** ⇒ 不许说"确定是它"，只能 decided=False
    _ad7 = WeChatAdapter.__new__(WeChatAdapter)
    _ad7._db = _FakeDB({A_ID: [time_now_floor()], "wxid_solo": [time_now_floor() - 7 * 60]})
    _ad7.cfg = {"wechat": {"group_name_white_list": ["测试"]}}
    _ad7._mon_cache = None
    _ad7.list_groups = lambda: [{"name": "测试", "wxid": A_ID}]
    _ad7.list_private_targets = lambda: [{"name": "独聊", "wxid": "wxid_solo"}]
    _ad7.display_name = lambda cid, *a, **k: _nm4.get(str(cid).split(":", 1)[-1], "")
    _ad7._all_chat_last_hhmm = lambda: {}   # ← 扩集失败（白名单侧也无冲突）
    _ad7._gui = None
    _ad7._get_gui = lambda *a, **k: None
    with _stub_ocr(pane=""):
        _cf7, _why7, _dec7, _cmp7 = _ad7._row_time_conflict("group:" + A_ID)
    ok("I5 ★扩集**拿不到** ⇒ 空 rivals 不许当「唯一」，`decided=False`（fail-closed 收紧）",
       _dec7 is False, "%s / %s" % (_dec7, _why7))

    # I6 行为验证：`_all_chat_last_hhmm` 真的走 DB（不是空壳）——源码锚
    ok("I6 源码锚：`_all_chat_last_hhmm` 逐表 `MAX(create_time)`（不碰大 BLOB）",
       "def _all_chat_last_hhmm(" in _WT2 and "MAX(create_time)" in _WT2, "")
    ok("I7 源码锚：全量侧**拿不到裸 id 就跳过**（不拿 md5 冒充会话）",
       "反查不到 ⇒ 不猜" in _WT2, "")
    ok("I8 源码锚：结果缓存 60s（与 `_monitored_chat_ids` 同口径）",
       "_allch_cache" in _WT2, "")

    print("\n── J. 三档都享受屏幕侧收敛（`img` 全链路透传，2026-09-30 新增）──")
    # 起因：`_pane_time_hits` / `_row_time_conflict` 调 `_same_minute_rivals` 时**不传帧**
    # ⇒ 三档里只有它们拿不到"绿底行"这条证据 ⇒ 同一个判据在三处强度不一致。
    ok("J1 `_pane_time_hits` 声明了 `img` 参数",
       "def _pane_time_hits(self, chat_id: str, pane: str, limit: int = 20, img=None)" in _WT2, "")
    ok("J2 `_pane_time_hits` 把 `img` 透传给 `_same_minute_rivals`",
       "_same_minute_rivals(chat_id, time.strftime(\"%H:%M\", time.localtime(_t0))," in _WT2
       and "img=img)" in _WT2, "")
    ok("J3 `_row_time_conflict` 声明了 `img` 参数",
       "def _row_time_conflict(self, chat_id: str, gui=None, img=None)" in _WT2, "")
    ok("J4 `_row_time_conflict` 把 `img` 透传给 `_same_minute_rivals`",
       "_same_minute_rivals(chat_id, _lt, img=img)" in _WT2, "")
    ok("J5 `chat_identity_ok` **保留了帧变量**（不再只留 OCR 文字）",
       "_pane_img = _co.capture_best(" in _WT2 and "pane = _co.pane_text(_pane_img" in _WT2, "")
    ok("J6 `chat_identity_ok` 两处调用都带上 `_pane_img`",
       _WT2.count("img=_pane_img") >= 2, "img=_pane_img ×%d" % _WT2.count("img=_pane_img"))

    # ⚠️ J1~J6 **全是源码锚**（字符串匹配）—— 源码里"写了 img=img"不等于"行为真的收敛了"。
    #    按本项目的判据纪律，源码锚必须配**行为验证 + 反例锚**。下面 J7~J9 就是那条腿。
    # 夹具：KC 与目标同分钟（真 rival），绿底行名字＝**目标**⇒ 应收敛成「无竞争」。
    _ad9 = _fake_adapter()
    _nm9 = {A_ID: "测试", B_ID: "KC"}
    _ad9.display_name = lambda cid, *a, **k: _nm9.get(str(cid).split(":", 1)[-1], "")
    _keep_hl9 = CO.highlight
    try:
        CO.highlight = lambda img: {"y_abs": 100, "score": 0.97, "name": "测试"}
        with _stub_ocr(pane=H_MINE):
            # 带帧（真实调用形态：`chat_identity_ok` 现在传 `_pane_img`）
            _ok9, _w9, _h9 = _ad9._pane_time_hits("group:" + A_ID, H_MINE, img=object())
            # 不带帧（老调用形态）—— 同一夹具、同一帧来源，只差这一个参数
            _ok9n, _w9n, _h9n = _ad9._pane_time_hits("group:" + A_ID, H_MINE)
    finally:
        CO.highlight = _keep_hl9
    ok("J7 ★行为：传帧+名字 ⇒ 绿底行＝目标 ⇒ 收敛成「无竞争」⇒ **这一档真能给结论**",
       _ok9 is True, "%s / %s" % (_ok9, _w9))
    ok("J8 ★反例锚：**同一夹具不传帧** ⇒ 收敛不生效 ⇒ 这一档拿不到结论"
       "（证明 J7 的 True 是透传带来的，不是夹具本来就通）",
       _ok9n is False and _ok9 is True, "%s / %s" % (_ok9n, _w9n))
    ok("J9 源码锚：`_pane_time_hits` 调 `_same_minute_rivals` 时**同时**传了 `name`"
       "（只传 img 的话收敛器走不到「绿底行＝目标 ⇒ 无竞争」那一条，收益减半）",
       "img=img, name=self.display_name(chat_id))" in _WT2, "")

    # J10~J11 ★★ 三态：把「读不到」与「对不上」分开 —— 这一档**从不单独定论「不是这个会话」**，
    #   它只回答"这一档有没有给出正面证据"。**一个时间戳都没读到 ⇒ 判不了（`None`）**，
    #   不许折成 `False`（那是把「看不见」说成「对不上」）。
    _adT = _fake_adapter()
    _adT.display_name = lambda *a, **k: "测试"
    _adT._same_minute_rivals = lambda *a, **k: []   # 摘掉「同分钟竞争」这道门，只测三态本身
    with _stub_ocr(pane=H_MINE):
        _oT, _wT, _hT = _adT._pane_time_hits("group:" + A_ID, H_MINE)
    with _stub_ocr(pane="今天聊得挺开心"):
        _oT0, _wT0, _hT0 = _adT._pane_time_hits("group:" + A_ID, "今天聊得挺开心")
    with _stub_ocr(pane="15：00"):
        _oT1, _wT1, _hT1 = _adT._pane_time_hits("group:" + A_ID, "15：00")
    ok("J10 ★三态：读到匹配时间 ⇒ True；**一个时间戳都没读到** ⇒ `None`（判不了）；"
       "读到但对不上 ⇒ `False`（没给正面证据，不是判否）",
       _oT is True and _oT0 is None and _oT1 is False, "%r / %r / %r" % (_oT, _oT0, _oT1))
    ok("J11 源码锚：两种「没命中」分开返回（`_n_ts == 0` ⇒ `None`）",
       "_n_ts == 0:" in _WT2 and "一个时间戳都没读到" in _WT2, "")

    print("\n── G. 内容排他：时间档给不出结论时，靠「内容排他」决定发不发（本次新增的放行依据）──")
    # 现场（真机）：两个群同一分钟都有消息 ⇒ 时间档分不出；但两个群的**正文不同** ⇒ 内容能分。
    # ⚠️ 夹具前提（收紧后新增）：判「排他」**必须先确认门槛** ——
    #   `_pane_excludes_others` 现在要求 `_all_chat_last_hhmm()` **非空**（＝名单外查过了）
    #   才敢说"没有别人"。老夹具没桩它 ⇒ 扩集拿不到 ⇒ 一律不排他 ⇒ G1 曾经 FAIL。
    #   ⇒ 这里显式桩成"今天只有目标有消息"（最简的"名单外确实没人"情形）。
    _ad2 = _fake_adapter()
    _ad2._all_chat_last_hhmm = lambda: {A_ID: H_MINE}
    _ex_a, _why_a = _ad2._pane_excludes_others(
        "group:" + A_ID, "甲群刚才说的那句话内容")
    ok("G1 聊天区只有目标群的正文 ⇒ **排他**（这一支是「这就能发」的依据）",
       _ex_a is True, _why_a)
    # G1b ★★对称锚：**同一夹具，只把扩集桩成空** ⇒ 不许判「排他」
    #   （「没有别人」是最强结论 ⇒ 名单外没查过就不配说；与 `_row_time_conflict`
    #    的空 rivals 口径是同一条原则）
    _ad2b = _fake_adapter()
    _ad2b._all_chat_last_hhmm = lambda: {}
    _ex_ab, _why_ab = _ad2b._pane_excludes_others(
        "group:" + A_ID, "甲群刚才说的那句话内容")
    ok("G1b ★扩集拿不到 ⇒ **不许**判「排他」（同一夹具只差扩集 ⇒ 证明 G1 的 True 是"
       "「名单外查过了」换来的）", _ex_ab is False and _ex_a is True, _why_ab)
    # G1c ★★名单外缺口：野群（**不在白名单**）的正文出现在聊天区 ⇒ 不许判「排他」
    _WILD_ID = "66000000111@chatroom"
    _ad2c = _fake_adapter()
    _ad2c._all_chat_last_hhmm = lambda: {A_ID: H_MINE, _WILD_ID: H_MINE}
    _base_rt = _ad2c.recent_texts

    def _rt_wild(cid, *a, **k):
        if str(cid).split(":", 1)[-1] == _WILD_ID:
            return ["野群说的完全不相干的一句"]
        return _base_rt(str(cid).split(":", 1)[-1])

    _ad2c.recent_texts = _rt_wild
    _ex_ac, _why_ac = _ad2c._pane_excludes_others(
        "group:" + A_ID, "甲群刚才说的那句话内容\n野群说的完全不相干的一句")
    ok("G1c ★名单外缺口：**野群**（不在白名单）的正文出现在聊天区 ⇒ 不许判「排他」"
       "（老实现参照集只有白名单 ⇒ 看不见它 ⇒ 会误判排他 ⇒ 放行）",
       _ex_ac is False and _WILD_ID in str(_why_ac), _why_ac)
    _ex_b, _why_b = _ad2._pane_excludes_others(
        "group:" + A_ID, "另外这里是 乙群说的是完全不同的一句")
    ok("G2 聊天区里出现**别的监听会话**的正文 ⇒ **不排他**（判否，宁可漏发）",
       _ex_b is False and "别的会话" in str(_why_b), _why_b)
    _ex_c, _why_c = _ad2._pane_excludes_others("group:" + A_ID, "")
    ok("G3 聊天区读不到字 ⇒ 不排他（不许把「没读到」当「干净」）", _ex_c is False, _why_c)
    _WT = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
    ok("G4 时间档给不出结论时**必须**过排他性核对（两处：长针档 + 短指纹档）",
       _WT.count("_pane_excludes_others(chat_id, pane)") >= 2,
       _WT.count("_pane_excludes_others(chat_id, pane)"))
    ok("G5 反例锚：旧写法（时间档判不了就一律判否）会让「用户想发也发不出去」 —— 断言它已经不在",
       "if _ccmp and not _cdec:\n                                return False" not in _WT)

    print("── D. 收口：三档都走同一道门（别再各写一份）──")
    import _srcmatch as _sm # noqa: E402
    _TEXT_W = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
    ok("D1 `_same_minute_rivals` 有单一实现", _sm.has(_TEXT_W, "def _same_minute_rivals("))
    ok("D2 三处时间档都调用了它", _TEXT_W.count("self._same_minute_rivals(") >= 3,
       _TEXT_W.count("self._same_minute_rivals("))
    ok("D3 监听集合来自选群那一个实现（不另写匹配）",
       _sm.has(_TEXT_W, "from . import listen_targets as _lt", "resolve_groups("))
    ok("D4 文案点了「同一分钟」并说清是谁在抢", _sm.has(_TEXT_W, "同一分钟还有别的会话也有消息"))
    _TEXT_CH = open(os.path.join(ROOT, "agent", "chat_header.py"), encoding="utf-8").read()
    ok("D5 指纹第二条件与退化判据都有单一实现",
       _sm.has(_TEXT_CH, "def pattern_score(", "def degenerate_reason("))
    ok("D6 `check` 同时报 幅度 与 形状（日志里能看出是哪一条拦的）",
       _sm.has(_TEXT_CH, '"cos": cos', "形状一致性"))

    # ── D7 ★★「共享瓶颈」收口锚──
    # 为什么单列：`_monitored_chat_ids()`（＝白名单∩群列表＋私聊目标）是**一处病根、多处病症** ——
    #   §11 修了 `_same_minute_rivals` 的 rival 候选集、§14 修了 `_pane_excludes_others`
    #   的排他参照集，**两处都是"参照集只有白名单"** ⇒ 同一个函数当参照集。
    #   所以修完这两处之后，必须**把它的所有调用点扫一遍**确认清干净。
    #   第三个调用点是 `_known_chat_names`（撞名源，4 个调用点）—— 审查结论：**它本来就是全量**，
    #   取的 `_db_chat_names()` ＝ `_groups`（微信库**全量**群）∪ `_nick_map`（全量昵称）
    #   ∪ 监听会话展示名 ⇒ 白名单只是**补一小部分**、不是主体 ⇒ **无盲区、不需要修**。
    #   ⇒ 这条断言把"它不走白名单"锁住：将来若有人把它改成 `_monitored_chat_ids()` 打头，
    #     撞名源就会出现和 §11/§14 一样的盲区（名字撞车检不出来 ⇒ `matches_strict` 放宽）。
    _adD = WeChatAdapter.__new__(WeChatAdapter)
    _adD._groups = [{"name": "野群名X", "wxid": "66000000111@chatroom"}]
    _adD._nick_map = {"wxid_p": "野友Y"}
    _adD._seen_names = {"屏幕见过的野名Z": 1}
    _adD._monitored_chat_ids = lambda: []        # ← **白名单故意留空**
    _adD.display_name = lambda *a, **k: ""
    try:
        _names_d = _adD._known_chat_names()
    except Exception as _eD:
        _names_d = ["<_known_chat_names 异常: %s>" % _eD]
    _hit_all = all(x in (_names_d or []) for x in ("野群名X", "野友Y", "屏幕见过的野名Z"))
    ok("D7 ★收口锚：**白名单为空**时 `_known_chat_names` 仍含全量群名 ∪ 昵称 ∪ 屏幕见过的名"
       "（⇒ 撞名源没有 §11/§14 那种「名单外看不见」的盲区，无需修）", _hit_all, _names_d)
    # ⚠ 用 AST 锚定的 `func_src` 取函数体，**不要**用「按 def 行做文本切片」的写法 ——
    #   文本切片会被"docstring/注释里提到同名函数"骗到（真踩过）。
    _dbnames = _fsrc(_WT2, "_db_chat_names")
    ok("D7b 源码锚：`_db_chat_names` 的主体是 `_groups` / `_nick_map`，"
       "`_monitored_chat_ids()` 只作补充",
       "_groups" in _dbnames and "_nick_map" in _dbnames and "_monitored_chat_ids()" in _dbnames, "")

    print("\n== E 段：内容级复核的『针』必须能在屏幕上出现 ==")
    # 起因：某机器上库里 `content` 回的是 `wxid_<序号>` / `5640103193` /
    # 同类**内部 ID**（不是消息正文）⇒ 被当成"内容级核对的针"去聊天区找 ⇒ **永远找不到**
    # ⇒ 复核判"不是这个会话" ⇒ **整批拒发、sent 始终 0**（现场："它试的 6 条全是 wxid 串"）。
    try:
        from agent.wechat import _looks_like_internal_id as _lid
        from agent.wechat import WeChatAdapter as _WCA

        _S1, _S2 = "wxid_" + "sample0004", "wxid_" + "sample0005"   # 运行时拼：字面量会被打包闸当成真账号
        for _s, _want in ((_S1, True), ("5640103193", True), (_S2, True),
                          ("4489***@chatroom", True), ("d41d8cd98f00b204e9800998ecf8427e", True)):
            ok("E1 内部 ID 应被剔出（不当针）：%r" % _s, _lid(_s) is _want, "实得 %s" % _lid(_s))
        for _s in ("PM-putback-2592", "@蓝色大肥鱼 你是", "在的 别刷了", "[文件/链接/卡片]",
                   "2qqwq", "W0verify-75482", "下午3点开会", "hello world"):
            ok("E2 真文本**不许**被误剔：%r" % _s, _lid(_s) is False, "实得 %s" % _lid(_s))

        # ⛔ **类完整性锚**（防"在类内部插入 0 缩进函数把类截断"这类事故 —— 实测踩过：
        #   helper 用 0 缩进插在 class 内 ⇒ 该类后续方法全变成模块级函数 ⇒
        #   `WeChatAdapter` 上直接没有 `recent_texts` ⇒ 只有跑真机才暴露）
        for _m in ("recent_texts", "chat_identity_ok", "send_text", "switch_chat_posted", "_db_messages"):
            ok("E3 类完整性：`WeChatAdapter.%s` 必须存在（防类被截断）" % _m, hasattr(_WCA, _m))

        import re as _re
        _src_w = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
        # 判据：helper 必须是**模块级**（行首零缩进）。⚠️ 不要求"在第一个 class 之前" ——
        #   实测它在第二个 class 之前也是合法的模块级定义（功能正常）；
        #   真正要防的是"缩进在类内"（那才会截断类）。
        _m = _re.search(r"(?m)^def _looks_like_internal_id\(", _src_w)
        _indented = _re.search(r"(?m)^\s+def _looks_like_internal_id\(", _src_w)
        ok("E4 源码锚：`_looks_like_internal_id` 必须是**模块级**（行首零缩进）"
           "—— 缩进在类内且非方法写法会截断类",
           bool(_m) and not _indented, "行首定义=%s 缩进定义=%s" % (bool(_m), bool(_indented)))
    except Exception as _eE:
        ok("E 段执行", False, "%s: %s" % (type(_eE).__name__, str(_eE)[:80]))

    print("\n── J. 时间档**不许单独**下结论：名字侧能把它否掉（2026-10-08）──")
    # 起因（现场）：连着几次对不同目标都返回「目标会话已经是当前打开的会话（高亮行 y=… 时间 18:04
    #   ＝目标会话最后一条消息时间，且该时刻唯一）」，而那个会话**未必真开着**
    #   （同一分钟里刚切到过别的会话）。
    #   ⚠️ 时间的最小刻度是**分钟** ⇒ 这一档单独成立时会**跳过真正的切换**
    #   ⇒ 后面可能把消息发进另一个会话（＝"发错人"级）。
    #   ⇒ 收紧：`_uniq`（该时刻在列表里唯一）单撑时，再读一次**高亮行的名字**；
    #     读出**另一个已知会话** ⇒ 这一档不采信（判否，并点名是它）。
    #   ⛔ 纯收紧：读不出 / 认成目标 / 认不出是谁 ⇒ **保持原判**（不许把正常路判死）。
    _adJ = _fake_adapter()
    _nmJ = {A_ID: "测试", B_ID: "KC"}
    _adJ.display_name = lambda cid, *a, **k: _nmJ.get(str(cid).split(":", 1)[-1], "")
    _adJ._same_minute_rivals = lambda *a, **k: []     # 本段只测"名字侧"这道门，绕开同分钟那道
    _adJ._known_chat_names = lambda: ["测试", "KC"]
    _keep_hlJ = CO.highlight
    _outJ = {}
    for _tagJ, _hlJ in (("名字＝目标（测试）", {"y_abs": 100, "score": 0.97, "name": "测试"}),
                        ("名字＝另一个会话（KC）", {"y_abs": 100, "score": 0.97, "name": "KC"}),
                        ("名字读不出", {"y_abs": 100, "score": 0.97, "name": ""}),
                        ("名字是别的行（y 对不上）", {"y_abs": 500, "score": 0.97, "name": "KC"}),
                        ("名字＝候选集外的第三个名字", {"y_abs": 100, "score": 0.97, "name": "没见过的名字"})):
        try:
            CO.highlight = (lambda img, _v=_hlJ: _v)
            with _stub_ocr(pane=""):
                _outJ[_tagJ] = _adJ._active_row_time_ok("group:" + A_ID, gui=object())
        finally:
            CO.highlight = _keep_hlJ
    ok("J1 名字侧＝目标 ⇒ 照旧放行（收紧不许把正常路判死）",
       _outJ["名字＝目标（测试）"][0] is True, str(_outJ["名字＝目标（测试）"])[:120])
    ok("J2 ⛔ 名字侧＝**另一个已知会话** ⇒ 这一档不采信（False，且说明里点名是它）",
       _outJ["名字＝另一个会话（KC）"][0] is False and "名字侧" in str(_outJ["名字＝另一个会话（KC）"][1]),
       str(_outJ["名字＝另一个会话（KC）"][1])[:140])
    ok("J3 名字读不出 ⇒ **保持原判**（读不出不是判否，别把正常路判死）",
       _outJ["名字读不出"][0] is True, str(_outJ["名字读不出"])[:120])
    ok("J4 两处量到的高亮行 **y 对不上** ⇒ 弃权（不许拿别的行的名字去否目标）",
       _outJ["名字是别的行（y 对不上）"][0] is True, str(_outJ["名字是别的行（y 对不上）"])[:120])
    ok("J5 名字认不出是谁（不在候选集里）⇒ 保持原判（认不出 ⇒ 不动）",
       _outJ["名字＝候选集外的第三个名字"][0] is True,
       str(_outJ["名字＝候选集外的第三个名字"])[:120])
    # J6 反例锚：候选集里**没有**第二个会话 ⇒ 那道门认不出「它是谁」⇒ 也保持原判
    #    （证明 J2 的 False 是「认出了另一个会话」给的，不是别的原因）
    _keep_poolJ = _adJ._known_chat_names
    _adJ._known_chat_names = lambda: ["测试"]
    try:
        CO.highlight = lambda img: {"y_abs": 100, "score": 0.97, "name": "KC"}
        with _stub_ocr(pane=""):
            _o6J = _adJ._active_row_time_ok("group:" + A_ID, gui=object())
    finally:
        CO.highlight = _keep_hlJ
        _adJ._known_chat_names = _keep_poolJ
    ok("J6 反例锚：候选集里没有第二个会话 ⇒ 认不出「KC 是谁」⇒ 保持原判"
       "（证明 J2 那条 False 是这道门给的）", _o6J[0] is True, str(_o6J)[:120])
    # J7b 结构锚（源码锚用 `_sm.has`，见判据纪律）：这道门**只在最弱那一档**做
    #   （不在「聊天区也出现同一时刻」那条强档上白花成本），且它**只能否掉**、不许放行。
    _seg_aruJ = _fsrc(open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read(),
                      "_active_row_time_ok")
    _seg_aruJ = _seg_aruJ[:_seg_aruJ.find("\n    def ", 10)]
    _GATE = "if _uniq and not _pane_hit"   # 变量承载针，避免"源码脆断言"（同一行的常量比较）
    _crossJ = _seg_aruJ.split(_GATE)[1].split("if _pane_hit or _uniq:")[0] if _sm.has(_seg_aruJ, _GATE) else ""
    ok("J7b 结构锚：名字侧交叉验证**只在 `_uniq` 那一档**跑，且只返回 False（纯收紧）",
       bool(_crossJ) and _sm.has(_crossJ, "名字侧指向另一个会话")
       and not _sm.has(_crossJ, "return True"), "")

    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


class _FakeDB:
    """只实现 `get_messages`（`_last_time_hhmm` / `_pane_time_hits` / `recent_texts` 都用它）。"""


    def __init__(self, times, texts=None):
        self.times = times
        self.texts = texts or {}

    def get_messages(self, chat_id, limit=1):
        cid = str(chat_id).split(":", 1)[-1]
        _ts = list(self.times.get(cid) or [])
        _tx = list(self.texts.get(cid) or [])
        _n = max(1, int(limit))
        out = []
        for i in range(min(max(len(_ts), len(_tx)), _n)):
            _r = {"create_time": (_ts[i] if i < len(_ts) else (_ts[-1] if _ts else 0))}
            if i < len(_tx):
                _r["text"] = _tx[i]
                _r["content"] = _tx[i]
            out.append(_r)
        return out


def _fake_adapter():
    """造一个"只有时间相关代码是真的"的 adapter：两个群同分钟 + 一个私聊。"""
    now = time_now_floor()
    ad = WeChatAdapter.__new__(WeChatAdapter)
    ad._db = _FakeDB({
        A_ID: [now],
        B_ID: [now], # 同一分钟（真机是差 13 秒）
        "wxid_solo": [now - 7 * 60], # 另一个分钟 ⇒ 不算"同分钟"
    }, texts={
        A_ID: ["甲群刚才说的那句话内容"],
        B_ID: ["乙群说的是完全不同的一句"],
        "wxid_solo": ["独聊说的话又是另一句"],
    })
    ad.cfg = {"wechat": {"group_name_white_list": ["KC", "测试"]}}
    ad._mon_cache = None
    ad.list_groups = lambda: [{"name": "测试", "wxid": A_ID}, {"name": "KC", "wxid": B_ID}]
    ad.list_private_targets = lambda: [{"name": "独聊", "wxid": "wxid_solo"}]
    ad.display_name = lambda *a, **k: ""
    return ad


def time_now_floor():
    """今天 `16:52` 那一刻（**秒**，与 `_last_time_hhmm` 读 `create_time` 的口径一致）。"""
    import time as _t
    lt = _t.localtime()
    return int(_t.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 16, 52, 0, 0, 0, -1)))


class _stub_ocr:
    """把截图/OCR 打桩：高亮行 16:52、会话列表一行命中、聊天区给指定文本。"""

    def __init__(self, pane=""):
        self.pane = pane

    def __enter__(self):
        self._c = (CO.capture_best, CO.highlight_time, CO.session_rows, CO.pane_text)
        CO.capture_best = lambda *a, **k: object()
        CO.highlight_time = lambda img: ("16:52", 100)
        CO.session_rows = lambda img: [{"full": "测试 16:52", "name": "测试", "y_abs": 100}]
        CO.pane_text = lambda img, limit=400: self.pane
        return self

    def __exit__(self, *a):
        CO.capture_best, CO.highlight_time, CO.session_rows, CO.pane_text = self._c
        return False


if __name__ == "__main__":
    sys.exit(main())

