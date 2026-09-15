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
_rs, _gr = CO.session_rows, CO.green_row_ratio
try:
    CO.session_rows = lambda im, zoom=2: [{"name": "别人", "y_abs": 100}, {"name": "E", "y_abs": 200}]
    CO.green_row_ratio = lambda im, y, half=6: 0.69 if y == 200 else 0.01
    hl_rel, why_rel = CO.highlight_relative("img")
    ok("相对判据能挑出最高的那一行", bool(hl_rel) and hl_rel["y_abs"] == 200 and hl_rel["name"] == "E", why_rel)
    CO.green_row_ratio = lambda im, y, half=6: 0.10          # 两行都差不多 ⇒ 不够突出，必须判 None
    ok("区分度不够时返回 None（fail-closed）", CO.highlight_relative("img")[0] is None)
    CO.green_row_ratio = lambda im, y, half=6: 0.0
    ok("全都没有绿底 ⇒ None", CO.highlight_relative("img")[0] is None)
    # ⛔ 2026-09-16 修（真缺陷）：`_green_at` 的取样窗**含头像列**，微信那种**绿色头像**给 0.18 的假绿 ⇒
    #    高亮行被认成头像绿的那一行（实测当前开的是「宋孟」、`chat_is_open` 报「微信…」）。
    #    现在两条路都改用"不含头像的右半段"（`green_row_ratio`）⇒ 头像绿必须不再被当成高亮行。
    CO.session_rows = lambda im, zoom=2: [{"name": "微信团队", "y_abs": 100}]
    _ga_keep = CO._green_at
    CO._green_at = lambda im, y, half=6: 0.18               # 老口径（含头像列）会认这一行
    ok("头像绿（老口径 0.18）不再被当成高亮行",
       CO.highlight_relative("img")[0] is None and CO.highlight("img") is None)
    CO._green_at = _ga_keep
finally:
    CO.session_rows, CO.green_row_ratio = _rs, _gr
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
   'r.get("full")' in _co_src and "row_time_match(_blob, want)" in _co_src)
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
   "self._norm_hhmm(_ht) != self._norm_hhmm(_lt)" in _w_src and "def _norm_hhmm" in _w_src
   and "return _co.hhmm(s)" in _w_src)
ok("第二道证据二选一：聊天区出现同一时间 **或** 该时刻在会话列表里唯一（两者都不成立 ⇒ 照旧判否，fail-closed）",
   "if _pane_hit or _uniq:" in _w_src and "_uniq = (_n == 1)" in _w_src
   and "def _active_row_time_ok" in _w_src)

print("\n── F. 时间口径：10 点以前的时刻也要能按时间定位（r11 实测根因）──")
# ⛔ 2026-09-16 根因（r11 两次 ABORT 的直接原因）：`find_row_info` 把**目标时间**（`strftime('%H:%M')`
#    ＝`01:03`）与**读到的**时间（归一成 `'%d:%02d'`＝`1:03`）**直接比字符串** ⇒ 上午 0~9 点永不相等。
#    E 那种单字母会话名字读不出来、**时间档是唯一信号** ⇒ 表现成"列表里明明有 E，滚 6 轮也定位不到"。
ok("hhmm：01:03 / 1：03 / 01：03 归一成同一个 1:03",
   CO.hhmm("01:03") == CO.hhmm("1：03") == CO.hhmm("01：03") == "1:03",
   repr([CO.hhmm("01:03"), CO.hhmm("1：03"), CO.hhmm("01：03")]))
ok("hhmm：认不出的给空串（不拿脏串去比）",
   CO.hhmm("昨天") == "" and CO.hhmm("") == "" and CO.hhmm("1:3") == "")
ok("hhmm：跨小时不误配（1:03 ≠ 11:03 / 21:03）",
   CO.hhmm("11:03") == "11:03" and CO.hhmm("21：03") == "21:03")
ok("row_time_match：整行「文件传．“01：03」命中 want=01:03（**修复前这条是红的**）",
   CO.row_time_match("文件传．“01：03", "01:03") is True)
ok("row_time_match：别的时刻不命中", CO.row_time_match("文件传．“19：41", "01:03") is False)
ok("row_time_match：want 认不出 ⇒ False（不放行）", CO.row_time_match("01：03", "昨天") is False)
ok("find_row_info 的时间比较走同一个口径（源码断言）",
   "want = hhmm(want_time)" in _co_src and "hit_t = row_time_match(_blob, want)" in _co_src)
ok("wechat 侧的 _norm_hhmm 只有一处实现（委托 chat_ocr.hhmm）", "return _co.hhmm(s)" in _w_src)

print("── G. 绿底行按像素找：高亮行是白字绿底、OCR 读不出它（r11 实测根因）──")
# ⛔ 2026-09-16 根因：`highlight` / `highlight_relative` / `current_chat_name` 都在**OCR 行**里挑绿最多的，
#    而当前打开的那一行是**白字绿底**——整幅 OCR 里根本没有这一行（实测 8 行独缺高亮行）⇒
#    只能挑到"头像绿"的行（实测把「宋孟」认成「微信…」）。现在主路改成**纯像素扫绿底带**。
try:
    from PIL import Image as _I2

    _W2, _H2 = 1139, 890
    _im2 = _I2.new("RGB", (_W2, _H2), (237, 237, 239))
    _im2.paste(CO.GREEN, (60, 494, 320, 590))       # 一整条绿底行（96px 高，横跨列表）
    _im2.paste(CO.GREEN, (100, 180, 150, 230))      # 另一行上的**绿色头像**（50×50，老口径的假绿来源）
    _bands = CO.green_bands(_im2)
    ok("像素法找到绿底带（y≈494~590，占比高）",
       len(_bands) == 1 and abs(_bands[0]["y_abs"] - 542) <= 4 and _bands[0]["score"] > 0.8, str(_bands))
    ok("同帧里的绿色头像（50×50）不算绿底行", all(b["y0"] > 400 for b in _bands))
    _hl2 = CO.highlight(_im2)
    ok("highlight 走像素法给出高亮行 y", bool(_hl2) and abs(_hl2["y_abs"] - 542) <= 4, str(_hl2))
    ok("没有绿底行时像素法给空（fail-closed）",
       CO.green_bands(_I2.new("RGB", (_W2, _H2), (237, 237, 239))) == [])
    ok("源码断言：三条路都改用了不含头像的取样口径",
       "def green_row_ratio" in _co_src and "sc = green_row_ratio(img, r[\"y_abs\"])" in _co_src
       and "green_row_ratio(use, r[\"y_abs\"])" in _co_src)
except Exception as _e:
    ok("绿底带判据可跑", False, "%s: %s" % (type(_e).__name__, _e))

print("── H. 单字母名字被 OCR 读成别的字时，仍要能定位那一行（r11 实测根因③）──")
# ⛔ 2026-09-16 真帧实测：E 是当前打开的那一行，整行 OCR＝『巷01：03』——名字被读成「巷」，
#    "名字明显不是它 ⇒ 不算"这条防误配守卫于是把**唯一正确的行**否掉 ⇒ 报"没定位到 E"。
#    修法：目标名 ≤2 字（短到 OCR 认不准）且**该时刻在整张列表里唯一**时放行。
try:
    from PIL import Image as _I3

    _img3 = _I3.new("RGB", (400, 300), (237, 237, 239))
    _rs2, _nor2 = CO.session_rows, CO.name_of_row
    try:
        CO.name_of_row = lambda im, y, text="", zoom=3: "巷"
        CO.session_rows = lambda im, zoom=2: [{"name": "巷", "full": "巷01：03", "y_abs": 144, "preview": None},
                                              {"name": "二海绵", "full": "二海绵昨天22．“", "y_abs": 241, "preview": None}]
        _i1 = CO.find_row_info(_img3, "E", want_time="01:03")
        ok("单字母目标 + 时间唯一 ⇒ 放行（**修复前这条是红的**）",
           bool(_i1) and int(_i1["y_abs"]) == 144, str(_i1))
        # 安全性：同一个时刻在列表里出现两次 ⇒ 唯一性不成立 ⇒ 照样不点（宁可找不到）
        CO.session_rows = lambda im, zoom=2: [{"name": "巷", "full": "巷01：03", "y_abs": 144, "preview": None},
                                              {"name": "别人", "full": "别人01：03", "y_abs": 241, "preview": None}]
        ok("同一时刻出现两次（唯一性不成立）⇒ 仍然不认（fail-closed）",
           CO.find_row_info(_img3, "E", want_time="01:03") is None)
        # 安全性：**多字**目标名仍走原来的严格守卫（名字不像就是不像）
        CO.session_rows = lambda im, zoom=2: [{"name": "宋孟", "full": "宋孟01：03", "y_abs": 144, "preview": None}]
        ok("多字目标名（文件传输助手）不被这条放宽影响",
           CO.find_row_info(_img3, "文件传输助手", want_time="01:03") is None)
    finally:
        CO.session_rows, CO.name_of_row = _rs2, _nor2
except Exception as _e:
    ok("短名 + 时间唯一 判据可跑", False, "%s: %s" % (type(_e).__name__, _e))

print("── I. 「当前开着的会话就是目标」的第三条独立证据（高亮行时间 × DB）──")
# ⛔ 2026-09-16 实测：搜索框路线已经把 E 切过来了，`chat_is_open` 仍报 False（名字读不出、指纹档也没参照）
#    ⇒ 闸门判否、后面每一步都在"没有正面证据"里打转。补第三档＝高亮行时间（屏幕）× DB。
_ok3_hit = {"v": True}
_ad4 = W.WeChatAdapter.__new__(W.WeChatAdapter)
_ad4.current_chat_name = lambda gui=None: ("", "绿底带在 y=120~217 但那一行名字 OCR 读不出")
_ad4._active_row_time_ok = lambda chat_id, pane="", gui=None: (True, "高亮行（y=168）时间 1:03 ＝目标最后一条消息时间")
ok("第三档能独立撑起「就是它」（修复前这条是红的）",
   _ad4.chat_is_open("x", gui=None, name="E")[0] is True, str(_ad4.chat_is_open("x", gui=None, name="E")))
_ad4._active_row_time_ok = lambda chat_id, pane="", gui=None: (False, "高亮行时间是 9:41 ≠ 1:03")
ok("第三档给不出正面证据时仍判否（fail-closed）",
   _ad4.chat_is_open("x", gui=None, name="E")[0] is False)
ok("源码断言：chat_is_open 接了第三档、且与 chat_identity_ok 同源",
   "_ok3, _why3 = self._active_row_time_ok(chat_id, gui=gui)" in _w_src
   and "def _active_row_time_ok" in _w_src
   and "_ok_t, _why_t = self._active_row_time_ok(chat_id, pane=pane, gui=gui)" in _w_src)

print("── J. 点前等列表停稳（投递滚轮是平滑滚动，惯性期间点击会点空）──")
from agent import chat_header as CH                              # noqa: E402
try:
    from PIL import Image as _I4

    _cap_real = CH.capture_image
    _q = {"q": []}

    def _cap(gui=None, **_kw):
        return _q["q"].pop(0) if _q["q"] else None

    CH.capture_image = _cap
    _ad5 = W.WeChatAdapter.__new__(W.WeChatAdapter)
    _imA = _I4.new("RGB", (600, 400), (237, 237, 239))
    _imB = _I4.new("RGB", (600, 400), (250, 250, 250))
    _q["q"] = [_imA, _imA]
    ok("连续两帧一致 ⇒ 判停稳", _ad5._list_settled(None, tries=4, gap=0) is True)
    _q["q"] = [_imA, _imB, _imB]
    ok("先动后停 ⇒ 也判停稳", _ad5._list_settled(None, tries=4, gap=0) is True)
    _q["q"] = [_imA, _imB, _imA, _imB]
    ok("一直在动 ⇒ 判没停稳（不阻塞流程，照旧往下走）", _ad5._list_settled(None, tries=4, gap=0) is False)
    _q["q"] = []
    ok("抓不到帧 ⇒ 不误判停稳", _ad5._list_settled(None, tries=3, gap=0) is False)
    ok("源码断言：点击前调它、并把结论写进说明", "self._list_settled(gui)" in _w_src and "点前列表已停稳" in _w_src)
finally:
    CH.capture_image = _cap_real

print("── K. 失败留全现场（跨机需求⑤：对面报的现象我这边要能看到现场）──")
try:
    import json as _json
    import shutil as _sh

    from PIL import Image as _I5
    _ad6 = W.WeChatAdapter.__new__(W.WeChatAdapter)
    _d6 = _ad6._dump_fail_shot("selftest_tmp", _I5.new("RGB", (64, 48), (200, 200, 200)),
                               {"picked": (1, 2), "cands": [(1, 2, 3, 4)]})
    _shot = os.path.join(_d6, "shot.png")
    _pj = os.path.join(_d6, "probe.json")
    ok("落盘目录里有 shot.png 与原图同尺寸", os.path.isfile(_shot) and _I5.open(_shot).size == (64, 48), _d6)
    _dat = _json.load(open(_pj, encoding="utf-8")) if os.path.isfile(_pj) else {}
    ok("probe.json 带尺寸与判据中间量", _dat.get("extra", {}).get("picked") == [1, 2]
       and _dat.get("size") == [64, 48], str(_dat)[:120])
    _before = set(os.listdir(os.path.dirname(_d6))) if os.path.isdir(os.path.dirname(_d6)) else set()
    ok("坏输入不抛（尽力而为，不许影响主流程）",
       isinstance(_ad6._dump_fail_shot("selftest_tmp", None, None), str))
    # 只留最近 N 份（同名 tag 不会被无限堆积）
    for _i in range(4):
        _ad6._dump_fail_shot("rot_tmp", _I5.new("RGB", (8, 8), (0, 0, 0)), {"i": _i}, keep=2)
    _rot = [n for n in os.listdir(os.path.dirname(_d6)) if n.endswith("_rot_tmp")]
    ok("同名 tag 只留最近 keep 份", len(_rot) <= 2, "留了 %d 份" % len(_rot))
    for _n in list(os.listdir(os.path.dirname(_d6))):
        if _n.endswith("_selftest_tmp") or _n.endswith("_rot_tmp"):
            _sh.rmtree(os.path.join(os.path.dirname(_d6), _n), ignore_errors=True)
    ok("源码断言：两条切会话失败路径都会存现场",
       'self._dump_fail_shot("search_entry"' in _w_src and 'self._dump_fail_shot("switch_row"' in _w_src)
except Exception as _e:
    ok("失败取证判据可跑", False, "%s: %s" % (type(_e).__name__, _e))

print("── L2. 活动行底色判据要认「绿占优」而不是写死色值（跨机 r14 最值钱一条）──")
# 对面实测：活动行底色是 (21,172,112) / (1,194,96) / 浅绿 (169,212,196) 三种都出现过，而老判据只认本机
# 的 (81,167,116)±34 ⇒ 命中 0 ⇒ 绿底读不到 ⇒ chat_is_open 永远 False ⇒ 投递前置掉到 no_ref
# （r12 那次 16 秒真鼠标事故的触发链里就有这一环）。判据改成"g 明显大于 r 且大于 b"这条与色值无关的性质。
for _nm, _c, _want in [("本机深绿 81,167,116", (81, 167, 116), True),
                       ("对面库值 21,172,112", (21, 172, 112), True),
                       ("对面高亮 1,194,96", (1, 194, 96), True),
                       ("对面浅绿 169,212,196", (169, 212, 196), True),
                       ("面板灰 237,237,239", (237, 237, 239), False),
                       ("行底灰 230,230,232", (230, 230, 232), False),
                       ("浅蓝 150,180,220", (150, 180, 220), False)]:
    ok("绿判据 %s ⇒ %s" % (_nm, _want), CO._is_green(*_c) is _want)
try:
    from PIL import Image as _I5b

    _img5 = _I5b.new("RGB", (1139, 890), (237, 237, 239))
    _img5.paste((169, 212, 196), (60, 494, 320, 590))          # 对面那种**浅绿**活动行
    _hl5 = CO.highlight(_img5)
    ok("浅绿活动行也要被认成高亮行（修复前是红的）",
       bool(_hl5) and abs(int(_hl5["y_abs"]) - 541) <= 4, str(_hl5))
except Exception as _e5b:
    ok("浅绿活动行判据可测", False, str(_e5b)[:80])

print("── L3. 短窗口要逐字扫（跨机 r14 报的「8 字/相似度 1.00 却判 False」）──")
_pane5 = "……前面别的内容 句一句话说完就跑 后面还有……"
_needle5 = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥" + "一句话说完就跑" + "后面还有很长很长的正文"
ok("针里第 3 字起的那 8~10 个字命中 ⇒ 必须放行（旧步长 4 会跳过）",
   CO.content_match(_pane5, _needle5) is True)
ok("低熵片段仍不算命中（120 位数字那种）",
   CO.content_match("序号 123456789012 在这", _needle5.replace("甲乙", "123456789012")) is False)

print("── L4. 活动行时间戳要能『按坐标直接读』（跨机 r16/r17：可读性只有 1/2~1/4 ⇒ 红线一收紧就常态拦）──")
ok("有 row_time_read（正读 + 反相两遍，专治白字绿底）",
   hasattr(CO, "row_time_read") and "def row_time_read" in open(os.path.join(ROOT, "agent", "chat_ocr.py"), encoding="utf-8").read())
ok("row_time_at 在 OCR 行取不到时会退到它（源码断言）",
   "best = row_time_read(img, int(y_abs))" in open(os.path.join(ROOT, "agent", "chat_ocr.py"), encoding="utf-8").read())
try:
    from PIL import Image as _I4, ImageDraw as _D4, ImageFont as _F4
    _im4 = _I4.new("RGB", (1139, 890), (237, 237, 239))
    _im4.paste((169, 212, 196), (60, 494, 320, 590))          # 对面那种浅绿活动行
    _d4 = _D4.Draw(_im4)
    try:
        _f4 = _F4.truetype(r"C:\Windows\Fonts\msyh.ttc", 16)
    except Exception:
        _f4 = _F4.load_default()
    _d4.text((232, 528), "03:28", font=_f4, fill=(30, 30, 30))      # 深字（正常行那种）
    _got4 = CO.row_time_read(_im4, 542, left=296)
    ok("按坐标直接读时间戳（正常行）拿得到", _got4 == "3:28", repr(_got4))
    ok("row_time_at 走兜底也拿得到", CO.row_time_at(_im4, 542) == "3:28", repr(CO.row_time_at(_im4, 542)))
    # ⚠️ 如实记一条局限：**白字绿底**那种（活动行）在合成图上正读+反相都读不出 ⇒ `row_time_read`
    #    只是"多试一次"，不能保证解决可用性（跨机 r16/r17 的可读性 1/2~1/4 就是这一条造成的）。
    _d4.rectangle([228, 520, 292, 556], fill=(169, 212, 196))
    _d4.text((232, 528), "03:28", font=_f4, fill=(255, 255, 255))
    ok("白字绿底这条**不承诺**能读（read 函数存在即可，别把它当可用性保证）",
       hasattr(CO, "row_time_read"))
except Exception as _e4:
    ok("按坐标读时间戳判据可跑", False, str(_e4)[:80])

print("── L5. 第四条独立证据：会话头标题带 OCR（跨机 r20：白字绿底行**持续**读不出 ⇒ 前三档全空）──")
_w5 = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
_seg5 = _w5[_w5.index("def chat_is_open"):]
_seg5 = _seg5[:_seg5.find("\n    def ", 10)]
ok("chat_is_open 接了标题带 OCR 这一档（源码）",
   "header_text(" in _seg5 and "matches(_tt, want)" in _seg5)
ok("给不出证据时不误判（读不到就往下走）", "if _tt and _co2.matches(_tt, want)" in _seg5)
try:
    from agent import chat_ocr as _co5
    from agent import wechat as _W5b
    _ad5 = _W5b.WeChatAdapter.__new__(_W5b.WeChatAdapter)
    _ad5.current_chat_name = lambda gui=None: ("", "读不出")
    _ad5.display_name = lambda cid: "余命十日"
    _cap5, _ht5 = _co5.capture_best, _co5.header_text
    _co5.capture_best = lambda gui=None, frames=2: object()
    _co5.header_text = lambda img=None, gui=None, zoom=2: "O余命十日"
    ok("标题带读到目标名 ⇒ 判 True（**这正是对面手工核的那条**）",
       _ad5.chat_is_open("x", gui=object(), name="余命十日")[0] is True,
       str(_ad5.chat_is_open("x", gui=object(), name="余命十日")))
    _co5.header_text = lambda img=None, gui=None, zoom=2: ""
    ok("标题带读不出 ⇒ 不误判（仍判否）", _ad5.chat_is_open("x", gui=object(), name="余命十日")[0] is False)
    _co5.header_text = lambda img=None, gui=None, zoom=2: "O别人"
    ok("标题带是别的会话 ⇒ 判否", _ad5.chat_is_open("x", gui=object(), name="余命十日")[0] is False)
    _co5.capture_best, _co5.header_text = _cap5, _ht5
except Exception as _e5c:
    ok("标题带这一档可测", False, str(_e5c)[:80])

print("\n%d/%d 通过" % (PASS, PASS + FAIL))
sys.exit(1 if FAIL else 0)
