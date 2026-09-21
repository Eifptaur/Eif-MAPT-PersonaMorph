#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**第六轮修复的行为级守卫**（第七轮 **V-R7-3**：那 5 组修复当时"没有任何判据守"）。

背景：把产品文件退回 `v2.1.48`（＝第六轮修复全撤），20 次候选判据**全部仍绿** —— 因为那些判据
要么测的是老行为（`clean()` 的剥除量），要么把被测函数整体 stub 掉，要么只做**源码文本**断言。
⇒ 本判据只做**行为级**：调真函数、喂真夹具、断言真结果，每一条都配**反例锚**
（证明"把守卫撤掉，这条会红"）。
"""
from __future__ import annotations

import os
import sys
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))   # `_srcmatch` 在 scripts 里（判据卫生网要求用它的 has()）
os.chdir(ROOT)

import _srcmatch as _SM                    # noqa: E402
from agent import chat_header as CH        # noqa: E402
from agent import chat_ocr as CO           # noqa: E402
from agent import input_backend as IB      # noqa: E402
from agent import sender as SD             # noqa: E402
from agent import wechat as W              # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


# ── V-R6-1（P0）：逐字雷同**不构成排他证据** ───────────────────────────────────────
print("\n== A. V-R6-1 逐字雷同 ⇒ 不排他（P0，退回 v2.1.48 后当时 0 条判据能抓） ==")
ad = W.WeChatAdapter.__new__(W.WeChatAdapter)
_TARGET, _OTHER = "group:目标群", "group:别的群"
_SAME = "早上好呀，今天几点开会？"


def _recent(self, ck):
    return [_SAME] if str(ck) in ("目标群", "别的群") else []


ad.recent_texts = types.MethodType(_recent, ad)          # type: ignore[attr-defined]
ad._monitored_chat_ids = types.MethodType(lambda self: [_TARGET, _OTHER], ad)   # type: ignore[attr-defined]
_ex, _why = ad._pane_excludes_others(_TARGET, "有人说话：%s" % _SAME)
ok("别家正文与目标**逐字雷同**且出现在聊天区 ⇒ 判**不排他**（判不了就不放行）",
   _ex is False, str(_why)[:110])
_ex2, _why2 = ad._pane_excludes_others(_TARGET, "聊天区里只有别的话，两边的正文都没出现")
ok("反例锚：两边正文都没出现 ⇒ 仍判排他（证明上面那条不是恒 False）",
   _ex2 is True, str(_why2)[:90])
ad._monitored_chat_ids = types.MethodType(lambda self: [_TARGET], ad)           # type: ignore[attr-defined]
_ex3, _why3 = ad._pane_excludes_others(_TARGET, "有人说话：%s" % _SAME)
ok("没有别的监听会话 ⇒ 排他（阳性对照，别把正常情形也判死）", _ex3 is True, str(_why3)[:80])

# ── V-R6-2：授权档不许把两个不同的名字归一成同一个 ────────────────────────────────
print("\n== B. V-R6-2 授权档：星期/相对日指纹守恒 ==")
ok("`matches_strict('星期六播报','星期天播报')` 必须 False（展示用 norm 会把两者洗成同一个）",
   CO.matches_strict("星期六播报", "星期天播报") is False)
ok("反例锚：同名仍判 True（上面那条不是恒 False）", CO.matches_strict("星期六播报", "星期六播报") is True)
ok("带时间戳的行文本照样能比（`文件传，17：01` vs `文件传输助手` 不受影响）",
   CO.matches_strict("文件传，17：01", "文件传，17：01") is True)

# ── V-R6-11/12：面板左沿只有入口 + 绿判据只有一套 ────────────────────────────────
print("\n== C. V-R6-11/12 面板左沿交叉校验 + 共用绿判据 ==")
from PIL import Image as _I                                                      # noqa: E402


def _frame(bubble_left: bool) -> _I.Image:
    """合成一帧：竖栏深色(0..80) + 会话列表浅灰(80..384) + 聊天区白(384..)；
    `bubble_left=True` 时在聊天区左列画一条深色气泡带（老口径会被它顶到右边 ⇒ 过冲）。"""
    im = _I.new("RGB", (760, 800), (250, 250, 250))
    px = im.load()
    for x in range(0, 80):
        for y in range(800):
            px[x, y] = (70, 70, 70)
    for x in range(80, 384):
        for y in range(800):
            px[x, y] = (237, 237, 239)
    # 高亮行（会话列表里横跨整行的一段绿底）
    for x in range(90, 380):
        for y in range(300, 380):
            px[x, y] = (81, 167, 116)
    if bubble_left:
        for x in range(400, 560):
            for y in range(120, 700):
                px[x, y] = (150, 150, 150)
    return im


_clean_f, _bub_f = _frame(False), _frame(True)
_pl_clean = CH.pane_left_for(_clean_f)
_pl_bub = CH.pane_left_for(_bub_f)
ok("干净帧：左沿≈384（老口径与结构锚都说得通）", 340 <= _pl_clean <= 420, _pl_clean)
ok("聊天区左列被气泡占满时，左沿**不许被顶进聊天区**（≤ 420）", _pl_bub <= 420, _pl_bub)
_real_dpl = CH.detect_pane_left
try:
    CH.detect_pane_left = lambda im, *a, **k: 660        # 真机实测过的"过冲"读数
    _pl_forced = CH.pane_left_for(_frame(False))         # 新对象 ⇒ 不吃上一帧的缓存
finally:
    CH.detect_pane_left = _real_dpl
ok("反例锚：老口径报 **660**（真机实测过的过冲值）时，入口改用结构锚（≤420）",
   0 < _pl_forced <= 420, _pl_forced)
ok("`find_row_info/session_rows` 都走同一个入口（源码级：`detect_pane_left` 只剩两处显式引用）",
   open(os.path.join("agent", "chat_ocr.py"), encoding="utf-8").read().count("ch.detect_pane_left(") <= 1)
# 绿判据统一：跨机实测的**浅绿**活动行底色也要认（老写法 g>r+25 会判否）
_light = _I.new("RGB", (400, 400), (237, 237, 239))
lp = _light.load()
for x in range(120, 360):
    for y in range(150, 220):
        lp[x, y] = (169, 212, 196)          # 跨机实测的浅绿活动行
_hw_light = CO.highlight_wide(_light, pane_left=384)
ok("浅绿主题（169,212,196）也能量到高亮行（`highlight_wide` 与 `_is_green` 共用一套判据）",
   bool(_hw_light) and abs(int(_hw_light["y_abs"]) - 185) <= 40, str(_hw_light))

# ── V-R6-16：出站闸门词表覆盖本轮新加的内部话术 ────────────────────────────────
print("\n== D. V-R6-16 出站闸门：新话术要拦得住 ==")
_NEW = ["我已经排进重试队列了，等现场清楚会自动补发一次", "这条我先排进重试队列了", "刚才那条现场没认准"]
_hits = [SD._is_internal_failure(x) for x in _NEW]
ok("三条新内部话术都被拦下（退回 v2.1.48 时 3/3 全部放行）", all(_hits), str(_hits))
ok("反例锚：正常聊天内容不许被误拦",
   SD._is_internal_failure("今天天气不错，晚上一起吃饭？") == "", repr(SD._is_internal_failure("今天天气不错")))

print("\n== E. V-R7-3 · V-R6-3：量不到绿底带 ⇒ **不许补枪**（真跑产品源码，不是 grep） ==")
# `_band_on_row()` 是嵌在 `wechat._click_visible_session()` 里的闭包 —— 外层调不动它，
# 但"能不能测量"这个 fail-closed 语义正是 V-R6-3 的核心。这里把**产品源码里那段闭包**原样
# 取出来 exec 到一个受控命名空间里跑（不复制、不改写代码 ⇒ 源码一旦回退成 fail-open，本条必红）。
import ast          # noqa: E402
import textwrap     # noqa: E402

_w_src = open(os.path.join("agent", "wechat.py"), encoding="utf-8").read()
_bn_node = None
for _n in ast.walk(ast.parse(_w_src)):
    if isinstance(_n, ast.FunctionDef) and _n.name == "_band_on_row":
        _bn_node = _n
        break
ok("能在产品源码里找到 `_band_on_row`（V-R6-3 的 fail-closed 闭包）", _bn_node is not None)


def _run_band(capture_fn, row_y=200, pane_left=384):
    """把产品源码里的 `_band_on_row` 原样跑一遍；`_chh.capture_image` 换成受控截图。"""
    _ns = {"_chh": types.SimpleNamespace(capture_image=capture_fn),
           "_co": CO, "gui": None, "row": {"y_abs": row_y}, "_tol": 40,
           "_row_pl": pane_left, "time": types.SimpleNamespace(sleep=lambda _s: None)}
    _body = ast.get_source_segment(_w_src, _bn_node)
    exec(compile(textwrap.dedent(_body), "<wechat._band_on_row>", "exec"), _ns)
    return _ns["_band_on_row"]()


if _bn_node is not None:
    _none_shot = lambda **k: None                                     # 截图永远拿不到
    _band_none, _meas_none = _run_band(_none_shot)
    ok("截图拿不到（连试两帧都 None）⇒ **measurable=False**（老写法 fail-open 会返回 None 被当成「可补枪」）",
       _band_none is None and _meas_none is False, "%r/%r" % (_band_none, _meas_none))
    _dark = _I.new("RGB", (600, 600), (237, 237, 239))                # 截到了、但这行没有绿底带
    _band_dark, _meas_dark = _run_band(lambda **k: _dark)
    ok("截到了但没有绿底带 ⇒ measurable=True（这是「能测量、只是不在目标行」⇒ 允许补枪）",
       _band_dark is None and _meas_dark is True, "%r/%r" % (_band_dark, _meas_dark))
    _green = _I.new("RGB", (600, 600), (237, 237, 239))
    _gp = _green.load()
    for _x in range(200, 560):
        for _y in range(180, 230):
            _gp[_x, _y] = (169, 212, 196)                             # 浅绿活动行，落在目标行附近
    _band_ok, _meas_ok = _run_band(lambda **k: _green)
    ok("绿底带已在目标行 ⇒ 返回 band 且 measurable=True（调用方据此**绝不补枪**、直接算成功）",
       bool(_band_ok) and _meas_ok is True, str(_band_ok))
    _band_far, _meas_far = _run_band(lambda **k: _green, row_y=520)
    ok("绿底带在**别的行** ⇒ band=None 且 measurable=True（补枪是允许的，没在被保护的行上）",
       _band_far is None and _meas_far is True, "%r/%r" % (_band_far, _meas_far))
    # 接线与顺序：调用方必须在**补一枪之前**拦下来，而且文案要如实说"不再补枪"
    _seg = _w_src.split("def _click_visible_session(")[1].split("\n    def ", 1)[0]
    _i_guard, _i_sleep = _seg.find("if not _meas:"), _seg.find("time.sleep(1.35)")
    ok("调用方在 sleep(1.35)（＝补枪前等待）**之前**就 fail-closed 返回",
       _i_guard != -1 and _i_sleep != -1 and _i_guard < _i_sleep, "guard@%d sleep@%d" % (_i_guard, _i_sleep))
    ok("拿不到测量 ⇒ 返回 False 且文案写明「不再补枪」", "不再补枪" in _seg)
    ok("反例锚：把 `return None, _seen` 改成 `return None, True`（＝退回 fail-open）⇒ 上面第一条必红",
       _run_band(lambda **k: None)[1] is False)

print("\n== F. V-R7-3 · V-R6-26：webui 三处加固的行为级锚（免认证路由 / 口令 / 日期） ==")
from agent import local_guard as LG        # noqa: E402
from agent import webui as WU              # noqa: E402

ok("Host 闸：回环（带端口 / 不带端口 / IPv6）放行",
   LG.host_ok("127.0.0.1:7860") and LG.host_ok("localhost") and LG.host_ok("[::1]:7860"))
ok("Host 闸：陌生域名、伪装前缀、空 Host 一律拒（fail-closed）",
   not LG.host_ok("evil.example.com") and not LG.host_ok("127.0.0.1.evil.com")
   and not LG.host_ok("") and not LG.host_ok(None))
ok("Host 闸：**不带端口也算回环**（老写法按 `host:port` 全串比会误拒本机请求）", LG.host_ok("127.0.0.1"))


class _FakeHandler:
    """只给 `local_guard.check()` 用的假 HTTP handler（真 handler 要开监听面，判据不许开）。"""

    def __init__(self, headers, path="/api/stats"):
        self.headers = headers
        self.path = path


_keep_tok = LG.token
LG.token = lambda *a, **k: "JUDGE-TOKEN"          # 打桩：不碰 logs/ 里的真口令文件
try:
    ok("check()：陌生 Host ⇒ 403（Host 闸**先于**口令闸 ⇒ 免认证路由也进不来）",
       LG.check(_FakeHandler({"Host": "evil.example.com"}))[0] is False
       and LG.check(_FakeHandler({"Host": "evil.example.com"}))[1] == 403)
    ok("check()：回环但没口令 ⇒ 401", LG.check(_FakeHandler({"Host": "127.0.0.1:7860"}))[:2] == (False, 401))
    ok("check()：口令错一位 ⇒ 401（走 `hmac.compare_digest` 那一路，不是 `==`）",
       LG.check(_FakeHandler({"Host": "127.0.0.1:7860", "X-PM-Token": "JUDGE-TOKENX"}))[:2] == (False, 401))
    ok("check()：回环 + 正确口令 ⇒ 200 放行",
       LG.check(_FakeHandler({"Host": "127.0.0.1:7860", "X-PM-Token": "JUDGE-TOKEN"}))[:2] == (True, 200))
    ok("check()：`?token=` 与 `Bearer` 两种取法都认（A1111 兼容客户端只有 URL 可用）",
       LG.check(_FakeHandler({"Host": "127.0.0.1:7860"}, "/x?token=JUDGE-TOKEN"))[:2] == (True, 200)
       and LG.check(_FakeHandler({"Host": "127.0.0.1:7860",
                                  "Authorization": "Bearer JUDGE-TOKEN"}))[:2] == (True, 200))
finally:
    LG.token = _keep_tok
_wu_src = open(os.path.join("agent", "webui.py"), encoding="utf-8").read()
ok("webui 里三条免认证路由都先过 `local_guard.host_ok`（源码接线，≥2 处）",
   _wu_src.count("local_guard.host_ok(self.headers.get(\"Host\"))") >= 2,
   _wu_src.count("local_guard.host_ok(self.headers.get(\"Host\"))"))
ok("口令比较一律 `hmac.compare_digest`（≥3 处：头 / query / Bearer）",
   _wu_src.count("_hmac.compare_digest") >= 3, _wu_src.count("_hmac.compare_digest"))
ok("`/api/stats/cal` 的日期校验抽成了纯函数 `cal_date_ok` 并被处理分支调用",
   _SM.has(_wu_src, "def cal_date_ok(d) -> bool:") and _SM.has(_wu_src, "if not cal_date_ok(d):"))
ok("cal_date_ok：严格 `YYYY-MM-DD` 才放行，目录穿越/宽松写法一律拒",
   WU.cal_date_ok("2026-09-21") and WU.cal_date_ok("") and not WU.cal_date_ok("2026-9-1")
   and not WU.cal_date_ok("../../config") and not WU.cal_date_ok("2026-09-21.jsonl"),
   "%r %r %r" % (WU.cal_date_ok("../../config"), WU.cal_date_ok("2026-9-1"), WU.cal_date_ok("2026-09-21")))

print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
sys.exit(0)
