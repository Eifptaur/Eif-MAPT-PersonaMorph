#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""后台"选到正确的会话"判据：**平滑下滚找行 + 点击后用绿底高亮自洽复核**（不需要微信、不出网）。

用户 2026-09-13 反馈：「你滚得太不顺滑了，**一下一下地滚，导致没有看到**」+「**先保证后台它能选到正确的会话**，
一切的问题都要解决，原则还是那个**全程后台、低风险**」⇒ 这条判据守住三件事：
  ① 找行会**下滚重试**（截图只覆盖露出来的几行，目标在下面时以前永远找不到）
  ② 点击后的复核是**自洽证据**：我们按名字点的那一行，现在是不是绿底高亮行（不依赖读出会话标题）
  ③ 滚轮是**投递**的（`WM_MOUSEWHEEL`，不碰真实鼠标）——判据直接检查它投出去的消息长什么样
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


from agent import chat_ocr as CO  # noqa: E402
from agent import input_backend as IB  # noqa: E402
from agent import wechat as W  # noqa: E402

print("── A2. 名字切分：单字母会话名（E）以前永远配不上 ──")
ok("OCR 行「E:提交信息还．“」切成名字 E", CO.split_name("E:提交信息还．“") == "E", repr(CO.split_name("E:提交信息还．“")))
ok("切完能吃 matches（单字母只走完全相等分支）", CO.matches(CO.split_name("E:提交信息还"), "E"))
ok("普通群名不受影响", CO.split_name("群deepseek") == "群deepseek", repr(CO.split_name("群deepseek")))
ok("名字后面的时间先被 clean 掉", CO.split_name("腾讯新闻14：47").startswith("腾讯新闻"),
   repr(CO.split_name("腾讯新闻14：47")))
ok("session_rows 用的是切过的名字（源码断言）", 'r["name"] = split_name(r["name"])' in
   open(os.path.join("agent", "chat_ocr.py"), encoding="utf-8").read())

print("── A3. 一次切会话最多一枪（连点两下会把聊天框关掉）──")
ok("刚点过（0.5s）⇒ 不许再点", CO.click_allowed(100.0, 100.5, 3.0)[0] is False, CO.click_allowed(100.0, 100.5)[1])
ok("超过冷却（3.1s）⇒ 允许", CO.click_allowed(100.0, 103.1, 3.0)[0] is True)
ok("从没点过（last=0）⇒ 允许", CO.click_allowed(0.0, 100.0, 3.0)[0] is True)
ok("异常输入不炸（允许）", CO.click_allowed(None, "x", 3.0)[0] is True)
_src_w = open(os.path.join("agent", "wechat.py"), encoding="utf-8").read()
ok("切会话里接了点击冷却", "_co.click_allowed(" in _src_w)
ok("冷却期内只复核、明确「不补点」", "不补点" in _src_w)
ok("点完记账（_pick_last 记时间与行 y）", "_pick_last[str(chat_id)] = (time.time(), clicked_y)" in _src_w)

print("── A. find_row_scrolled：找不到就平滑下滚再找 ──")
calls = {"n": 0, "scrolls": []}


def mk(hit_at):
    calls["n"] = 0
    calls["scrolls"] = []

    def cap():
        calls["n"] += 1
        return "img%d" % calls["n"]

    def find(img):
        return {"pos": (10, 200), "y_abs": 180, "name": "E"} if calls["n"] == hit_at else None

    def scroll(times):
        calls["scrolls"].append(times)
        return True

    res, log = CO.find_row_scrolled(cap, find, scroll_fn=scroll, max_steps=6, per_step=3, settle_s=0)
    return res, log, calls


info, _log, c = mk(1)
ok("第一眼就命中 ⇒ 不滚动", info is not None and c["scrolls"] == [], str(c["scrolls"]))
info, _log, c = mk(3)
ok("第三眼才命中 ⇒ 滚了 2 次", info is not None and c["scrolls"] == [3, 3], str(c["scrolls"]))
info, _log, c = mk(99)
ok("一直找不到 ⇒ 滚满 max_steps 就停", info is None and len(c["scrolls"]) == 6,
   "scrolls=%d info=%s" % (len(c["scrolls"]), info))
ok("找不到时给出的说明含滚动过程", "下滚" in CO.find_row_scrolled(lambda: "x", lambda i: None,
                                                scroll_fn=lambda n: True, max_steps=2, settle_s=0)[1])


def _boom(_n):
    raise RuntimeError("backend 没有 wheel")


info, log = CO.find_row_scrolled(lambda: "x", lambda i: None, scroll_fn=_boom, max_steps=2, settle_s=0)
ok("滚轮报错不炸、如实记下来", info is None and "滚轮异常" in log, log[:40])
info, log = CO.find_row_scrolled(lambda: None, lambda i: None, scroll_fn=None, max_steps=2, settle_s=0)
ok("抓不到画面 ⇒ 返回 None + 说明", info is None and ("捕获" in log or "命中" in log), log[:40])

print("── B. highlight：绿底高亮行（点击后的自洽证据）──")
try:
    from PIL import Image as _Im

    img_plain = _Im.new("RGB", (400, 300), (250, 250, 250))
    img_green = _Im.new("RGB", (400, 300), (250, 250, 250))
    for yy in range(150, 172):                      # 画一条 22px 高的"绿底行"
        for xx in range(20, 380):
            img_green.putpixel((xx, yy), CO.GREEN)
    _real_rows = CO.session_rows
    CO.session_rows = lambda im, zoom=2: [{"name": "E", "preview": None, "y_abs": 160},
                                          {"name": "别人", "preview": None, "y_abs": 224}]
    hl = CO.highlight(img_green)
    ok("有绿底行 ⇒ 认出来并给出 y 与占比", bool(hl) and abs(hl["y_abs"] - 160) <= 2 and hl["score"] > 0.5,
       str(hl))
    ok("没有绿底 ⇒ 返回 None（fail-closed）", CO.highlight(img_plain) is None)
    ok("相似度/命中判据：我们点的行与高亮行差 ≤28px 才算同一行",
       abs(160 - 160) <= 28 and abs(224 - 160) > 28)
    CO.session_rows = _real_rows
except Exception as e:
    ok("highlight 判据可跑", False, "%s: %s" % (type(e).__name__, e))

print("── C. 滚轮是投递的（不碰真实鼠标）──")
posted = []
_real_post, _real_toclient = IB._post, IB.to_client
try:
    IB.to_client = lambda hwnd, pt: (int(pt[0]), int(pt[1]))     # 脱机：不查真实窗口
    IB._post = lambda hwnd, msg, wp, lp: posted.append((msg, wp, lp))
    be = IB.MessageBackend(activate=False)
    ok("MessageBackend 有 wheel 方法", hasattr(be, "wheel"))
    okv, whyv = be.wheel(1234, (500, 400), delta=-120, times=3, gap_ms=0)
    wheels = [p for p in posted if p[0] == IB.WM_MOUSEWHEEL]
    ok("一次发 3 格 ⇒ 投了 3 条 WM_MOUSEWHEEL", okv and len(wheels) == 3, "%d 条" % len(wheels))
    want_wp = ((int(-120) & 0xFFFF) << 16)
    ok("wParam 高位＝-120（向下滚）", all(w[1] == want_wp for w in wheels), hex(wheels[0][1]) if wheels else "-")
    ok("lParam 用屏幕坐标（不是客户区）", all(w[2] == IB.pack_lparam(500, 400) for w in wheels),
       str(wheels[0][2]) if wheels else "-")
    ok("投递前先发 WM_MOUSEMOVE 定位", any(p[0] == IB.WM_MOUSEMOVE for p in posted))
    ok("全程没有真鼠标 API", "mouse_event" not in open(os.path.join("agent", "input_backend.py"), encoding="utf-8").read().split("def wheel")[1][:1200])
finally:
    IB._post, IB.to_client = _real_post, _real_toclient

print("── D. 接线：switch_chat_posted 用上了这些 ──")
_src = open(os.path.join("agent", "wechat.py"), encoding="utf-8").read()
ok("切会话走 find_row_scrolled（会滚）", "_co.find_row_scrolled" in _src)
ok("切完用 highlight 自洽复核", "_co.highlight(" in _src or "_co.highlight_relative(" in _src)
ok("复核用相对判据（抗帧质量抖动）", "_co.highlight_relative(" in _src)
ok("滚轮走 backend.wheel（投递档）", "backend.wheel(" in _src and "wheel_pt" in _src)
ok("复核容差 28px 写死在代码里", "<= 28" in _src)
ok("**会话行点击／滚轮投渲染子窗**（2026-09-13 实测：投主窗点不动）", "ib.find_render_child(main) or main" in _src)
ok("聊天区内容变化也当切换证据", "_co.pane_text(" in _src and "聊天区内容已变化" in _src)

print("── E. 相对高亮判据 + 聊天区摘要（脱机）──")
ok("input_backend 有 find_render_child", hasattr(IB, "find_render_child"))
ok("find_render_child(0) 安全返回 0", IB.find_render_child(0) == 0)
_rs, _ga = CO.session_rows, CO._green_at
try:
    CO.session_rows = lambda im, zoom=2: [{"name": "别人", "y_abs": 100}, {"name": "E", "y_abs": 200}]
    CO._green_at = lambda im, y, half=6: 0.69 if y == 200 else 0.01
    hl_rel, why_rel = CO.highlight_relative("img")
    ok("相对判据能挑出最高的那一行", bool(hl_rel) and hl_rel["y_abs"] == 200 and hl_rel["name"] == "E", why_rel)
    CO._green_at = lambda im, y, half=6: 0.10          # 两行都差不多 ⇒ 不够突出，必须判 None
    ok("区分度不够时返回 None（fail-closed）", CO.highlight_relative("img")[0] is None)
    CO._green_at = lambda im, y, half=6: 0.0
    ok("全都没有绿底 ⇒ None", CO.highlight_relative("img")[0] is None)
finally:
    CO.session_rows, CO._green_at = _rs, _ga
ok("chat_ocr 有 pane_text", hasattr(CO, "pane_text"))

print("── E2. 内容级身份核对（名字会骗人：群聊预览带「发言人:」前缀）──")
ok("content_match：认出同一段内容", CO.content_match("……前面的废话 这是一条独特内容 abcdef 后面的", "这是一条独特内容 abcdef"))
ok("content_match：内容不同 ⇒ False", CO.content_match("完全不相干的一段话在这里", "这是一条独特内容 abcdef") is False)
ok("content_match：太短不给结论（fail-closed）", CO.content_match("abc", "abcd") is False)
ok("content_match：容忍 OCR 吞字（多尺度片段）",
   CO.content_match("这是一条独特内 abcdef", "这是一条独特内容 abcdef") is True)
_real_cb, _real_pt = CO.capture_best, CO.pane_text
try:
    class _FakeDB:
        @staticmethod
        def get_messages(chat_id, limit=8):
            return [{"content": "这是一条独特内容 abcdef"}]

    ad2 = W.WeChatAdapter.__new__(W.WeChatAdapter)
    ad2._db = _FakeDB()
    CO.capture_best = lambda gui=None, frames=3: object()
    CO.pane_text = lambda img, limit=200: "……这是一条独特内容 abcdef……"
    ok("内容一致 ⇒ 判 True（可以发）", ad2.chat_identity_ok("x", gui=object())[0] is True)
    CO.pane_text = lambda img, limit=200: "完全是别的会话的内容在这里"
    ok("内容不符 ⇒ 判 False（拒绝发送）", ad2.chat_identity_ok("x", gui=object())[0] is False)
    _FakeDB.get_messages = staticmethod(lambda chat_id, limit=8: [{"content": "<msg>图片</msg>"}])
    ok("目标没有可比文本 ⇒ None（当「没有正面证据」处理）", ad2.chat_identity_ok("x", gui=object())[0] is None)
finally:
    CO.capture_best, CO.pane_text = _real_cb, _real_pt
ok("send_file_posted 接了内容级闸", "chat_identity_ok(chat_id, gui=gui)" in _src)

# ⛔ 单字母名字的会话行（2026-09-13 实测 bug）：E 的行 OCR 成 `[草稿]EE`（草稿标记＋名字＋草稿内容），
#    老 matches 要求 len(name)>=2 才走包含判断 ⇒ 名字一个字母的会话永远定位不到。
ok("单字母名字：`[草稿]EE` 能匹配上 E", CO.matches("[草稿]EE", "E") is True, str(CO.matches("[草稿]EE", "E")))
ok("单字母名字：纯 `E` 也能匹配", CO.matches("E", "E") is True)
ok("单字母名字：不误配到别的会话（`群里的人`）", CO.matches("群里的人", "E") is False)
ok("单字母名字：`[草稿]` 之外的前缀也会被剥掉（Draft）", CO.matches("[Draft]EE", "E") is True)
ok("多字名字不受影响（仍是包含判断）", CO.matches("海绵宝宝课堂19：29", "海绵宝宝") is True)

# ⛔ 2026-09-14 修（⑤ 重发时实测出来）：`find_row_info` 对单字名字的**名字行复核**原来写的是
#    `norm(got) == norm(name)` **裸全等**，而 E 那种行的名字行会被 OCR 成 `[草稿]EE`
#    （草稿标记＋名字＋草稿内容）⇒ 裸全等永远不等 ⇒ 列表里明明有 `[草稿]EE`，
#    `find_row_info` 却全否、`switch_chat_posted` 报"没定位到 E"（白滚 6 轮）。
#    现在复核改用与 `matches()` 同一套口径；下面两条一正一反钉住"修好了"且"没放宽成误配"。
_fr_src = open(os.path.join(ROOT, "agent", "chat_ocr.py"), encoding="utf-8").read()
ok("单字复核走 matches（不再裸全等，草稿标记会剥掉）", "if not matches(got, name):" in _fr_src)

# ⛔ 2026-09-14 用户报"明明一直是 E 的会话，你却扫不到，这是不是个 bug" ⇒ 查实是**四个**真缺陷，逐条钉住：
#   ① 身份闸的"针"里混进了**类型占位符**（`[文件/链接/卡片]`/`[文本]`）—— 拿它去聊天区找永远找不到，
#      还会把真短 token 挤出名额 ⇒ 针必须是真内容；
#   ② 聊天区**一个字都读不到**时老实现返回 False（"证据说不是"）—— 该说"判据不可用"（None）；
#   ③ `pane_text` 单帧原尺寸读，文件卡多的那一屏读成空串 ⇒ 要放大 + 分段重读；
#   ④ `send_file_posted` 对内容档的 False **无条件拒绝** ⇒ `confirm_open`（用户当面确认这条明路）形同虚设。
ok("① 针里不再收方括号类型占位符", 're.match(r"^\\[[^\\[\\]]{1,16}\\]$", c)' in _src)
ok("② 聊天区读不到 ⇒ 返回 None（判据不可用），不是 False",
   "if not pane_n:" in _src and "判据不可用，不是「不是这个会话」" in _src)
ok("③ pane_text 放大 + 分段重读", "def pane_text(img, limit: int = 200, zoom: int = 2)" in _fr_src
   and "分三段" in _fr_src)
ok("④ 用户当面确认能压过内容档的否定（默认仍 fail-closed）",
   "if not confirm_open:" in _src and "按人工确认放行" in _src)
ok("放宽的只是草稿标记形态（名字行是别的名字仍然否）",
   CO.matches("[草稿]EE", "E") is True and CO.matches("宋孟", "E") is False)

# ⛔ 按"最后一条消息的时间"定位行（2026-09-13 实测：E 的名字行 OCR 给空串 ⇒ 名字这条路根本走不通；
#    而 21：41 这种时间戳 OCR 读得准，实测按时间一次命中 y=246 那一行）
_co_src = open(os.path.join(ROOT, "agent", "chat_ocr.py"), encoding="utf-8").read()
_w_src = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
ok("find_row_info 支持 want_time（按时间定位）", "def find_row_info(img, name: str, zoom: int = 2, want_time: str = \"\")" in _co_src)
ok("时间是从整行文本 full 里找的（只看 name 永远找不到时间）",
   'r.get("full")' in _co_src and "_time_re.finditer(_blob)" in _co_src)
ok("时间命中但名字明显是别的会话 ⇒ 不算（宁可不点）",
   "if got and not matches(got, name):" in _co_src)
ok("switch_chat_posted 会把目标会话的最后消息时间传进去",
   "want_time=_want_time" in _w_src and "_want_time = time.strftime(\"%H:%M\", _lt)" in _w_src)

# ⛔ 会话行必须用**慢节奏**点击（2026-09-13 A/B：快节奏投渲染子窗高亮不动；悬停 300 + 按住 150 高亮立刻跳）
_ib_src = open(os.path.join(ROOT, "agent", "input_backend.py"), encoding="utf-8").read()
ok("MessageBackend.click 支持 hover_ms / press_ms",
   "def click(self, hwnd: int, screen_pt, right: bool = False, hover_ms: int = 0, press_ms: int = None)" in _ib_src)
ok("会话行点击用的是慢节奏（hover_ms=300, press_ms=150）",
   "hover_ms=300, press_ms=150" in _w_src)
ok("身份闸有「高亮行时间」这一档", "highlight_time" in _w_src and "def highlight_time(img)" in _co_src)
# [2026-09-14 改向] 原来这里要求"聊天区里必须也出现同一时间"。实测：E 最近一条（01:35）的时刻**在聊天区里没渲染出来**
#   （新消息不带时间分隔）⇒ 会话明明开着，闸门仍判否、文件发不出去。改成：
#   两个独立来源＝**屏幕（高亮行时间 OCR）× DB（目标会话最后一条消息时间）**；第二道证据二选一——
#   ①聊天区里也出现同一时刻 ②该时刻在会话列表里**唯一**（只有这一个会话是它）。
ok("高亮行时间档：屏幕×DB 两个独立来源 + 时间格式归一化（列表的 1:35 与 DB 的 01:35 视为同一时刻）",
   "self._norm_hhmm(_ht) == self._norm_hhmm(_lt)" in _w_src and "def _norm_hhmm" in _w_src)
ok("第二道证据二选一：聊天区出现同一时间 **或** 该时刻在会话列表里唯一（两者都不成立 ⇒ 照旧判否，fail-closed）",
   "_pane_hit or _uniq" in _w_src and "_uniq = (len(_hits) == 1)" in _w_src)

print("\n%d/%d 通过" % (PASS, PASS + FAIL))
sys.exit(1 if FAIL else 0)
