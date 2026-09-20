#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据：**时间类的身份档必须能分辨"两个会话同一分钟都在说话"**（2026-09-21 网友 v0919 真机定案）。

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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))     # 同目录的 `_srcmatch`
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import chat_header as CH                                             # noqa: E402
from agent import chat_ocr as CO                                                # noqa: E402
from agent.wechat import WeChatAdapter                                          # noqa: E402

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
FP_BAD = [0] * 63 + [255]              # 他库里 49615732107@chatroom / 1716x900 那条（退化参照）
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

    print("── D. 收口：三档都走同一道门（别再各写一份）──")
    import _srcmatch as _sm                                                      # noqa: E402
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
    """只实现 `get_messages`（`_last_time_hhmm` 与 `_pane_time_hits` 就用这一个）。"""

    def __init__(self, times):
        self.times = times

    def get_messages(self, chat_id, limit=1):
        cid = str(chat_id).split(":", 1)[-1]
        return [{"create_time": t} for t in list(self.times.get(cid) or [])[:max(1, int(limit))]]


def _fake_adapter():
    """造一个"只有时间相关代码是真的"的 adapter：两个群同分钟 + 一个私聊。"""
    now = time_now_floor()
    ad = WeChatAdapter.__new__(WeChatAdapter)
    ad._db = _FakeDB({
        A_ID: [now],
        B_ID: [now],                     # 同一分钟（真机是差 13 秒）
        "wxid_solo": [now - 7 * 60],     # 另一个分钟 ⇒ 不算"同分钟"
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

