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
  ③ 他库里 `49615732107@chatroom` 的 `1716x900` 参照 = `[0]*63 + [255]`（**只有最右边一维有值**），
     同一会话其它四个尺寸逐字节相同且正常 ⇒ 那是**退化参照**，`is_blank` 抓不住它（极差 255、最暗列 0）。

本判据守四件事：
  A 指纹：两个条件（幅度相似 ≥0.90 且 形状一致 ≥0.95）；同一会话（含 ±噪声）仍要过；
  B 参照：退化指纹**不许入库**、也不许拿来判（真机那条形状当夹具）；
  C 时间档：同一分钟有别人 ⇒ 三档**一律不给正面结论**（fail-closed）；只有它一个 ⇒ 行为不变；
  D 反例锚：把老行为（不查同分钟 / 只看 similarity）摆出来，必须被判不合格。
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))) # 同目录的 `_srcmatch`
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import chat_header as CH # noqa: E402
from agent import chat_ocr as CO # noqa: E402
from agent.wechat import WeChatAdapter # noqa: E402

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
FP_BAD = [0] * 63 + [255] # 他库里 49615732107@chatroom / 1716x900 那条（退化参照）
FP_HELPER = [0, 0, 102, 162, 133, 112, 110, 173, 82, 178, 143, 82, 136, 255, 173, 144, 163, 153,
             92, 102, 143, 61, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
             0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

A_ID, B_ID = "49615732107@chatroom", "50493777651@chatroom"


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

    # ⑥ ★★放宽方向的反例（2026-09-30 收紧「匹配」口径）：绿底行只是**包含**目标名 ⇒ **不许**返回 []
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

    print("\n── G. 内容排他：时间档给不出结论时，靠「内容排他」决定发不发（本次新增的放行依据）──")
    # 现场（真机）：两个群同一分钟都有消息 ⇒ 时间档分不出；但两个群的**正文不同** ⇒ 内容能分。
    _ad2 = _fake_adapter()
    _ex_a, _why_a = _ad2._pane_excludes_others(
        "group:" + A_ID, "甲群刚才说的那句话内容")
    ok("G1 聊天区只有目标群的正文 ⇒ **排他**（这一支是「这就能发」的依据）",
       _ex_a is True, _why_a)
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

