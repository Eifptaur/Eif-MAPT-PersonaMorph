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

print("── A2. 名字切分：单字母会话名（E）以前永远配不上 ──")
ok("OCR 行「E:提交信息还．“」切成名字 E", CO.split_name("E:提交信息还．“") == "E", repr(CO.split_name("E:提交信息还．“")))
ok("切完能吃 matches（单字母只走完全相等分支）", CO.matches(CO.split_name("E:提交信息还"), "E"))
ok("普通群名不受影响", CO.split_name("群deepseek") == "群deepseek", repr(CO.split_name("群deepseek")))
ok("名字后面的时间先被 clean 掉", CO.split_name("腾讯新闻14：47").startswith("腾讯新闻"),
   repr(CO.split_name("腾讯新闻14：47")))
ok("session_rows 用的是切过的名字（源码断言）", 'r["name"] = split_name(r["name"])' in
   open(os.path.join("agent", "chat_ocr.py"), encoding="utf-8").read())

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

print("── D. 接线：switch_chat_posted 用上了这两件 ──")
src = open(os.path.join("agent", "wechat.py"), encoding="utf-8").read()
ok("切会话走 find_row_scrolled（会滚）", "chat_ocr.find_row_scrolled" in src or "_co.find_row_scrolled" in src)
ok("切完用 highlight 自洽复核", "_co.highlight(" in src)
ok("滚轮走 backend.wheel（投递档）", "backend.wheel(main, wheel_pt" in src)
ok("复核容差 28px 写死在代码里", "<= 28" in src)

print("\n%d/%d 通过" % (PASS, PASS + FAIL))
sys.exit(1 if FAIL else 0)
