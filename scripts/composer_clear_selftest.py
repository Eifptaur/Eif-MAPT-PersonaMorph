#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""输入框"清残留"这一族动作的判据（行为 + 源码双路）。

守的是两条**已被现场证实**的缺陷：
  ① **投递档下 `Ctrl+A` 不生效**：投递的消息不带修饰键状态，微信收到 `A` 时问系统"Ctrl 按住没"
     永远答"没按" ⇒ 组合键退化成**字面字母 `a`**。于是"全选删除"不但清不掉东西，反而**往框里
     又打进一个字符**（输入框越清越脏、搜索词越清越长，都是这个）。
  ② **附件粘贴/挂上去了但没发出去时，草稿留在输入框里**：调用方拿到失败会重试，而这期间用户
     **随手一回车**就把上一轮的图片/文件发了出去。

判据分四段：
  A. `_composer_state`：两路证据（PrintWindow 墨迹 / 发送按钮颜色）谁硬信谁，未知必须报 unknown
     （**不许**把 unknown 当 empty —— 那会让人以为清干净了，而残留仍在框里）。
  B. `_clear_composer_posted`：只用**不需修饰键**的键、**闭环**（复探到空才停）、附件卡片有第二档、
     清不掉**如实报失败**。
  C. `_draft_cleanup_note`：框里有东西才撤；**粘贴前框里本来就有用户内容时不许撤**（只提示）。
  D. 源码层：三个调用点（发文字残留 / 发图失败 / 发文件失败）都接上了这套件，且全文件再无
     `Ctrl+A` 清空。

跑法： runtime\\python\\python.exe scripts\\composer_clear_selftest.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import _srcmatch as _sm # noqa: E402
import _srcslice as _ss # noqa: E402
import agent.wechat as W # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


SRC = open(os.path.join(ROOT, "agent", "wechat.py"), "r", encoding="utf-8", errors="replace").read()


class FakeGui:
    origin_x = 1000
    origin_y = 200
    render_rect = (101, 90, 1240, 980)


class FakeBackend:
    def __init__(self):
        self.vks = []

    def keys(self, hwnd, vks, hold_ms=30):
        self.vks.append(tuple(int(v) for v in (vks or ())))
        return True, ""


class FakeAdapter:
    def __init__(self, has=True, on_click=None, raise_has=False):
        self._has = has
        self._on_click = on_click
        self._raise_has = raise_has
        self.clicks = []

    def _input_has_content(self, gui, r):
        if self._raise_has:
            raise RuntimeError("stub: 颜色判据不可用")
        return self._has, "stub 按钮颜色"

    def _click_posted(self, backend, hwnd, pt, tag="", **kw):
        self.clicks.append((tuple(pt), tag))
        if self._on_click:
            self._on_click()
        return True, ""


# ── 打桩：把"量画面"两件换成可控的（被测代码只认这两件的返回值）──
_orig = (W._probe_input_box_frame, W._input_ink, W._composer_card_points)
_box = (0, 0, 200, 120)


def _install(box=_box, ink=0, cards=None):
    """装桩：`box`＝量的输入框（None＝量不到）、`ink`＝上沿条带深色点数（可给 callable/序列）。"""
    W._probe_input_box_frame = (lambda gui, img=None: box)
    if callable(ink):
        W._input_ink = (lambda gui, b, strip=80, img=None: ink())
    else:
        W._input_ink = (lambda gui, b, strip=80, img=None: ink)
    W._composer_card_points = (lambda gui: list(cards or []))


def _restore():
    W._probe_input_box_frame, W._input_ink, W._composer_card_points = _orig


print("── A. `_composer_state`：两路证据 + 未知不许当空 ──")
try:
    _install(box=None, ink=0)
    ad_has = FakeAdapter(has=True)
    st, why = W._composer_state(ad_has, FakeGui(), FakeGui.render_rect)
    ok("PrintWindow 量不到框 ⇒ 落到按钮颜色兜底（说 has）", st == "has" and "按钮颜色" in why, why)
    st2, why2 = W._composer_state(FakeAdapter(has=False), FakeGui(), FakeGui.render_rect)
    ok("按钮颜色说空 ⇒ empty", st2 == "empty" and "按钮颜色" in why2, why2)
    st3, why3 = W._composer_state(FakeAdapter(raise_has=True), FakeGui(), FakeGui.render_rect)
    ok("两路都不可用 ⇒ **unknown**（不许当 empty）", st3 == "unknown" and "量不到" in why3, why3)
    _install(box=_box, ink=7)
    st4, why4 = W._composer_state(FakeAdapter(has=False), FakeGui(), FakeGui.render_rect)
    ok("PrintWindow 有墨点 ⇒ 直接判 has（比屏幕实拍硬，不被遮挡影响）",
       st4 == "has" and "PrintWindow" in why4, why4)
    _install(box=_box, ink=0)
    st5, _why5 = W._composer_state(FakeAdapter(has=True), FakeGui(), FakeGui.render_rect)
    ok("PrintWindow 条带 0 点 ⇒ empty（不再去屏幕实拍）", st5 == "empty")
finally:
    _restore()

print("── B. `_clear_composer_posted`：不用修饰键 + 闭环 + 卡片第二档 ──")
print("   B1 复探到空就停（不猜该按几下）")
try:
    seq = [9, 9, 0]
    _install(box=_box, ink=lambda: (seq.pop(0) if seq else 0))
    b, ad = FakeBackend(), FakeAdapter()
    ok_flag, why = W._clear_composer_posted(ad, b, 12345, FakeGui(), FakeGui.render_rect,
                                            tag="t1", probe_every=10)
    n_back = sum(1 for v in b.vks if v == (0x08,))
    ok("返回成功", ok_flag is True, why)
    ok("首键是 End（光标顶到文末，退格才会从最右边开始删）", b.vks and b.vks[0] == (0x23,), str(b.vks[:2]))
    ok("复探到空就停：第 30 次退格后收手（不是一路按满 60）", n_back == 30, "退格 %d 次" % n_back)
    ok("**全程不用修饰键组合**（每个键都是单键投递）", all(len(v) == 1 for v in b.vks), str(set(b.vks)))
    ok("说明里写明清空手法与次数", "已清空" in why and "退格" in why, why)

    print("   B2 附件卡片：退格删不掉的走「点卡片 → Delete」，点中一档就停")
    seq2 = []
    _install(box=_box, ink=lambda: (seq2.pop(0) if seq2 else 9), cards=[(1, 2)]) # 恒有内容 ⇒ 退格清不掉
    state = {"n": 0}


    def _hit():
        state["n"] += 1
        if state["n"] == 1: # 第一档点中之后复探为空
            seq2.append(0)
    b2 = FakeBackend()
    ad2 = FakeAdapter(on_click=_hit)
    ok2, why2 = W._clear_composer_posted(ad2, b2, 12345, FakeGui(), FakeGui.render_rect,
                                         tag="t2", max_back=20, probe_every=10)
    ok("卡片档救回：返回成功", ok2 is True, why2)
    ok("确实点了卡片落点（只点一档就停）", len(ad2.clicks) == 1, str(ad2.clicks))
    ok("点完之后投了 Delete", any(v == (0x2E,) for v in b2.vks), str(set(b2.vks)))
    ok("说明里写明是卡片那一档救的", "点卡片第 1 档" in why2, why2)

    print("   B3 清不掉必须**如实报失败**（不许假装清过）")
    _install(box=_box, ink=9, cards=[(1, 2), (3, 4)])
    b3 = FakeBackend()
    ad3 = FakeAdapter()
    ok3, why3 = W._clear_composer_posted(ad3, b3, 12345, FakeGui(), FakeGui.render_rect,
                                         tag="t3", max_back=20, probe_every=10)
    ok("返回失败", ok3 is False)
    ok("说明里明说「没能」并让人去手动清", "没能" in why3 and "手动" in why3, why3)
    ok("两档卡片都试过了才放弃", len(ad3.clicks) == 2, str(len(ad3.clicks)))

    print("   B4 量不到输入框 ⇒ 不许报成功")
    _install(box=None, ink=0)
    b4 = FakeBackend()
    ad4 = FakeAdapter(raise_has=True)
    ok4, why4 = W._clear_composer_posted(ad4, b4, 12345, FakeGui(), FakeGui.render_rect,
                                         tag="t4", max_back=10, probe_every=10)
    ok("两路都量不到 ⇒ 返回失败（不假称清干净）", ok4 is False and "量不到" in why4, why4)
    ok("量不到框时**不去猜卡片落点**（一个点击都不发）", ad4.clicks == [], str(ad4.clicks))
finally:
    _restore()

print("── C. `_draft_cleanup_note`：撤草稿，但绝不撤用户自己的东西 ──")
_called = {"n": 0}
_orig_clear = W._clear_composer_posted
try:
    W._clear_composer_posted = (lambda *a, **k: (_called.__setitem__("n", _called["n"] + 1),
                                                 (True, "stub 已清空"))[1])
    _install(box=_box, ink=0)
    note = W._draft_cleanup_note(FakeAdapter(), FakeBackend(), 1, FakeGui(), FakeGui.render_rect,
                                 False, "", what="这张图")
    ok("框里没东西 ⇒ 返回空串（无处置）", note == "", repr(note))
    ok("框里没东西 ⇒ 不去动清理", _called["n"] == 0, str(_called))

    _install(box=_box, ink=6)
    note2 = W._draft_cleanup_note(FakeAdapter(), FakeBackend(), 1, FakeGui(), FakeGui.render_rect,
                                  True, "PrintWindow 实测 6 个深色点", what="这张图")
    ok("**粘贴前框里本来就有用户内容 ⇒ 不撤**（只提示人工）",
       _called["n"] == 0 and "没有" in note2 and "这张图" in note2, note2)

    note3 = W._draft_cleanup_note(FakeAdapter(), FakeBackend(), 1, FakeGui(), FakeGui.render_rect,
                                  False, "", what="这份文件")
    ok("基线干净 ⇒ 真去撤，并把结果贴进回执", _called["n"] == 1 and "残留清理" in note3, note3)
finally:
    W._clear_composer_posted = _orig_clear
    _restore()

print("── D. 源码层：三个调用点都接上了，且全文件再无「Ctrl+A 清空」──")
_seg_text = _ss.func_src(SRC, "send_text_posted")
ok("发文字链：打字前清残留走共用件", _sm.has(_seg_text, "_clear_composer_posted(self, backend, main, gui, r"))
ok("发文字链：不再有 Ctrl+A（0x41）", _sm.count(_seg_text, "0x41") == 0,
   "命中 %d 次" % _sm.count(_seg_text, "0x41"))
ok("发文字链：清不掉**不阻塞打字**（只记日志，不 return 失败）",
   _sm.has(_seg_text, "log.warning(\"%s（继续打字，但可能与残留串成一条）\"") and
   _sm.count(_seg_text, "return False, _cwhy0") == 0)
_seg_img = _ss.func_src(SRC, "send_image_posted")
ok("发图链：粘贴**前**记基线（用于判断能不能撤）", _sm.has(_seg_img, "_pre_st, _pre_why = _composer_state(self, gui, r)"))
ok("发图链：失败收尾撤草稿（图还挂在框里时）",
   _sm.has(_seg_img, "_draft_cleanup_note(self, backend, main, gui, r") and
   _sm.count(_seg_img, "_clr_note") == 3, "引用 %d 次" % _sm.count(_seg_img, "_clr_note"))
_seg_file = _ss.func_src(SRC, "send_file_posted")
ok("发文件链：开对话框前记基线", _sm.has(_seg_file, "_pre_st, _pre_why = _composer_state(self, gui, r)"))
ok("发文件链：三处失败返回都带上「撤草稿」",
   _sm.count(_seg_file, "_clr(") == 3, "命中 %d 处" % _sm.count(_seg_file, "_clr("))
_seg_search = _ss.func_src(SRC, "_clear_search_input")
ok("搜索框清空：不再用 Ctrl+A", _sm.count(_seg_search, "0x41") == 0,
   "命中 %d 次" % _sm.count(_seg_search, "0x41"))
ok("搜索框清空：Home + Delete（不用退格——空框退格会退出搜索）",
   _sm.has(_seg_search, "backend.keys(int(hwnd), (0x24,)") and
   _sm.has(_seg_search, "backend.keys(int(hwnd), (0x2E,)"))
ok("全文件再无 `VK_CONTROL`（投递档下它只会退化成字面字母）", _sm.count(SRC, "VK_CONTROL") == 0,
   "命中 %d 次" % _sm.count(SRC, "VK_CONTROL"))
ok("`_probe_input_box_frame` / `_input_ink` 支持复用现成帧（省一次抓帧）",
   _sm.has(SRC, "def _probe_input_box_frame(gui, img=None):") and
   _sm.has(SRC, "def _input_ink(gui, box, strip: int = 80, img=None) -> int:"))

print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
