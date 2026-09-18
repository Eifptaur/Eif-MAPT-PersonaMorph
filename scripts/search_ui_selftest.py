#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「搜索入口」判据：**两套 UI 都要认**（用户 2026-09-13 口径）。

用户原话：「没有搜索框了，只有搜索的一个图标，摁了之后才有搜索框」+
        「不同的 UI 可能不一样，我另外一台电脑是有搜索框的，你要把两套 UI 的兼容做好」

判据：
  ① **icon 形态**（本机 4.1.15.8）：标题带里只有放大镜图标 ⇒ 必须点中放大镜，**不许点到「＋」**
  ② **box 形态**（另一台机 / 老 UI）：读到占位文本「搜索」⇒ 点文字，**优先级高于认图标**
  ③ 左侧导航栏那条**通高深色竖条不许被当成图标**（同色阈值下它最宽，靠高度上限挡掉）
  ④ 找不到入口时返回 None（fail-closed，不许瞎点）；空图/None 输入不抛异常
  ⑤ `band_signature/band_diff`：同一带 0.000、变了的带 > 0.01（判"搜索框有没有展开"用）
  ⑥ 抓图路径：`grab_render` 必须**优先渲染子窗 + 重试**（不然遮挡时读到别人家的像素）
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import Image, ImageDraw  # noqa: E402
from agent import chat_ocr as CO  # noqa: E402
from agent import chat_header as CH  # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


# —— 合成一张"看起来像微信"的图：左导航栏（通高深色）+ 会话列表 + 聊天面板 ——
W, H = 1139, 890
RAIL, PANE = 89, 328          # 实测本机比例：导轨 0..89、列表 89..328、聊天区 328..1139
TITLE = 45                    # 实测：PrintWindow 抓到的一些帧**带窗口标题栏**（通宽深灰 y<45），
                              # 有的不带 —— 标题带必须"现算"，不能写死 y=30..135（会被并成一个大块）


def base_img():
    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, W, TITLE - 1), fill=(117, 117, 117))     # 窗口标题栏（通宽深灰）
    d.rectangle((0, TITLE, RAIL - 1, H), fill=(58, 58, 58))     # 导航栏
    d.rectangle((RAIL, TITLE, PANE - 1, H), fill=(247, 247, 247))  # 会话列表
    return im, d


def draw_magnifier(d, cx=242, cy=85, r=11):
    d.ellipse((cx - r, cy - r, cx + r, cy + r - 4), outline=(60, 60, 60), width=3)
    d.line((cx + r - 5, cy + r - 8, cx + r + 3, cy + r + 1), fill=(60, 60, 60), width=3)


def draw_plus(d, cx=289, cy=85, r=13):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(60, 60, 60), width=2)
    d.line((cx - 6, cy, cx + 6, cy), fill=(60, 60, 60), width=2)
    d.line((cx, cy - 6, cx, cy + 6), fill=(60, 60, 60), width=2)


def font(size=30):
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.exists(p):
            try:
                from PIL import ImageFont
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return None


print("① icon 形态（本机：只有放大镜，摁了才有搜索框）")
im, d = base_img()
draw_magnifier(d)
draw_plus(d)
ent = CO.find_search_entry(im)
ok("认出 icon 形态", bool(ent) and ent.get("variant") == "icon", str(ent))
ok("点的是放大镜不是「＋」",
   bool(ent) and abs(int(ent["x"]) - 242) <= 6 and abs(int(ent["y"]) - 85) <= 8,
   "落点 (%s,%s) 期望≈(242,85)" % (ent.get("x"), ent.get("y")) if ent else "None")

print("①b 帧带窗口标题栏时也要认得出来（实测有些 PrintWindow 帧带、有些不带）")
_px = CO._panel_top(im, 92, 322)
ok("_panel_top 跳过标题栏（回到面板上沿）", abs(_px - TITLE) <= 3, "panel_top=%s 期望≈%s" % (_px, TITLE))
_b0, _b1 = CO._search_band(im, 92, 322)
ok("标题带避开标题栏且罩住图标中心(85)", _b0 >= TITLE - 2 and _b0 <= 85 <= _b1, "band=(%d,%d)" % (_b0, _b1))
_blocks = CO._dark_blocks(im, 92, TITLE - 10, 325, 140)   # 故意把范围放宽到标题栏里（模拟"没现算"的写法）
_bad = [b for b in _blocks if b[3] > 60 or b[2] > 60]
ok("放宽范围会出现超宽深块（正是写死 y 范围的坑）", _bad != [], str(_blocks))
ok("但落点不会落在标题栏里（深条没被当入口）", bool(ent) and int(ent["y"]) > TITLE + 5,
   "y=%s（标题栏高 %s）" % ((ent.get("y") if ent else None), TITLE))
ok("放宽范围也仍认得出放大镜", bool(ent) and abs(int(ent["x"]) - 242) <= 6, str(ent.get("x") if ent else None))
_blk = [b for b in CO._dark_blocks(im, 92, _b0, 325, _b1)
        if CO.SEARCH_ICON_W[0] <= b[2] <= CO.SEARCH_ICON_W[1] and CO.SEARCH_ICON_H[0] <= b[3] <= CO.SEARCH_ICON_H[1]]
ok("真实标题带里的图标块落在两个 x 位置上（放大镜 + 加号）", len({b[0] for b in _blk}) == 2, str(_blk))

print("② box 形态（另一台机：搜索框直接摆着 → 认字优先）")
im2, d2 = base_img()
d2.rounded_rectangle((95, 62, 330, 112), radius=8, fill=(236, 236, 236))
f = font(30)
if f:
    d2.text((120, 70), "搜索", fill=(90, 90, 90), font=f)
_orig_rec = CO.recognize
_read = []


def _fake_rec(img, *_a, **_k):
    _read.append(1)
    return [("搜索", 60.0, 32.0, 60.0, 26.0)]


CO.recognize = _fake_rec                       # 白盒：单测"认字优先"的判定顺序
ent2 = CO.find_search_entry(im2)
CO.recognize = _orig_rec
ok("读到占位文本时判 box（优先于图标）", bool(ent2) and ent2.get("variant") == "box", str(ent2))
ok("box 落点落在会话列表列内",
   bool(ent2) and 88 <= int(ent2["x"]) <= 322 and 30 <= int(ent2["y"]) <= 135,
   "落点 %s" % ((ent2.get("x"), ent2.get("y")),) if ent2 else "None")

if f:
    real = _orig_rec(im2.crop((89, 30, 328, 135)).resize((478, 210)))
    txt = " ".join(str(t) for t, *_ in real)
    ok("真字体合成的「搜索」WinRT OCR 读得出来（box 形态可实测）", "搜索" in txt, "OCR=%r" % txt[:40])

print("③ 导航栏通高深色竖条不许当图标")
im3, d3 = base_img()                            # 只留导轨，标题带里没有任何图标
ok("空标题带 ⇒ None（fail-closed）", CO.find_search_entry(im3) is None,
   str(CO.find_search_entry(im3)))

print("④ 输入健壮性")
ok("None 输入不抛异常且返回 None", CO.find_search_entry(None) is None)

print("⑤ 标题带指纹（判「搜索框有没有展开」）")
sig_a = CO.band_signature(im)
sig_a2 = CO.band_signature(im)
d4 = ImageDraw.Draw(im)
d4.rectangle((95, 62, 330, 112), fill=(255, 255, 255))     # 标题带被改了一块
sig_b = CO.band_signature(im)
ok("同一带 diff=0.000", abs(CO.band_diff(sig_a, sig_a2)) < 1e-9, "%.4f" % CO.band_diff(sig_a, sig_a2))
ok("变了的带 diff>0.01", CO.band_diff(sig_a, sig_b) > 0.01, "%.4f" % CO.band_diff(sig_a, sig_b))
ok("指纹长度一致（可比较）", bool(sig_a) and len(sig_a) == len(sig_b), "%s" % (sig_a and len(sig_a)))

print("⑥ 抓图路径：渲染子窗优先 + 重试（遮挡时不许静默退回抓屏就算数）")
src = open(os.path.join(ROOT, "agent", "chat_header.py"), encoding="utf-8").read()
ok("grab_render 会试渲染子窗", "find_render_child" in src and "渲染子窗" in src)
ok("grab_render 有重试（tries）", "tries: int = 12" in src and "for _round in range(max(1, int(tries)))" in src)
ok("帧质量闸（既不太暗也不是纯色帧）", "_frame_ok" in src and "stddev" in src)
ok("退回抓屏前先过遮挡校验", "_region_occluded" in src and src.index("_region_occluded(render, main_pid)") < src.index("ImageGrab.grab"))
ok("退回抓屏只作最后手段（PrintWindow 优先）", src.index("_print_window") < src.index("ImageGrab.grab"))
w_src = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
ok("open_chat_by_search 用 find_search_entry（两套 UI 都走这条路）", "find_search_entry" in w_src)
ok("图标形态下先确认搜索框展开再打字", "不往下打字" in w_src)
ok("打字投主窗（键盘），点图标投渲染子窗（鼠标）", "backend.send_text(main, name)" in w_src)

print("⑦ 图标候选块里挑搜索入口：**形状判据**（跨机 r11 实测：对面那台点到了导航栏那块）")
# 对面 r11 的现场（原样搬来当回归）：
#   cand_txt = "#0(47,76,24x45) · #1(242,71,21x21) · #2(242,71,11x11) · #3(48,136,25x28)"
#   why      = "最上一排最靠左的图标块 24x45（该排 3 块 / 共 4 块，导航栏右沿 0）"
# ⇒ 老口径"最靠左"选中的是**导航栏**那块（24×45 竖长条），真正的放大镜在 (242,71) 21×21。
_their_cands = [(47, 76, 24, 45), (242, 71, 21, 21), (242, 71, 11, 11), (48, 136, 25, 28)]
_our_cands = [(290, 83, 24, 24), (236, 84, 22, 21), (290, 83, 12, 12), (187, 136, 21, 20), (211, 138, 15, 19)]
_p1 = CO.pick_search_icon(_their_cands)
ok("对面那组：挑中 (242,71) 21×21 的放大镜，**不再**挑导航栏的 24×45",
   bool(_p1) and (_p1[0], _p1[1], _p1[2], _p1[3]) == (242, 71, 21, 21), str(_p1))
ok("对面那组：说明里写明「已排除竖长条」", bool(_p1) and "竖长条" in _p1[4], _p1[4] if _p1 else "")
_p2 = CO.pick_search_icon(_our_cands)
ok("本机组：仍是 (236,84) 22×21（我们这边本来就没挑错，别改坏）",
   bool(_p2) and (_p2[0], _p2[1]) == (236, 84), str(_p2))
ok("候选为空 ⇒ None（fail-closed，不瞎点）", CO.pick_search_icon([]) is None and CO.pick_search_icon(None) is None)
_ps = CO.pick_search_icon([(47, 76, 24, 45)])          # 只剩竖长条 ⇒ 退回原口径，不许 None
ok("只剩竖长条时退回原口径（不做成「永远找不到」）", bool(_ps) and _ps[0] == 47, str(_ps))
ok("find_search_entry 用上了这个挑选函数",
   "pick_search_icon(cands)" in open(os.path.join(ROOT, "agent", "chat_ocr.py"), encoding="utf-8").read())
# 跨机 r12 报的潜在坑 D：放大镜与「＋」**同 x**（实测 242,71 上叠 21×21 与 11×11），若连通域给出的
# 顺序颠倒，"最靠左"就会选中 11×11 的碎片 ⇒ 排序键必须是 (x 升序, 面积降序)。
_d1 = CO.pick_search_icon([(47, 76, 24, 45), (242, 71, 11, 11), (242, 71, 21, 21)])
ok("同 x 的两个块**顺序颠倒**时仍取大的（21×21）",
   bool(_d1) and (_d1[2], _d1[3]) == (21, 21), str(_d1))

print("⑧ 切会话·搜索路线**不许把微信留在前台**（时间线实测它以前会）")
# 0.1s 前台时间线实测：这条链开浮层 + 投字 + 点结果行 ⇒ 浮层 1.63s、主窗 8.96s，**全程没还过**；
# 后果不止"打扰"：紧接着 `send_file_posted` 的 `_stash_fg()` 会把**被顶到前面的微信**当成"用户的窗口"，
# 于是后面"还前台"还了个微信（本轮真出现：r12 投递完前台停在微信）。⇒ 进去 stash、出去一律还。
_w_src2 = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
_seg_s = _w_src2[_w_src2.index("def open_chat_by_search"):]
_seg_s = _seg_s[:_seg_s.find("\n    def ", 10)]
ok("进去先 stash 前台（在点搜索入口之前）",
   "_stash_fg()" in _seg_s and _seg_s.index("_stash_fg()") < _seg_s.index("_click_posted("))
ok("出去一律还前台（finally，异常路径也走）",
   "finally:" in _seg_s and '_restore_fg_until("切会话·搜索路线"' in _seg_s)

print("⑨ 几何判据：搜索框要认得出（跨机 r12/r13：对面那台占位文本读成「…」、放大镜是浅灰细线）")
try:
    from PIL import Image as _I9, ImageDraw as _D9, ImageFont as _F9
    im9 = _I9.new("RGB", (1143, 891), (237, 237, 239))
    _d9 = _D9.Draw(im9)
    _d9.rectangle([91, 55, 213, 82], fill=(255, 255, 255), outline=(200, 200, 200))   # 白底搜索框
    _d9.ellipse([103, 62, 115, 74], outline=(150, 150, 150))                          # 浅灰放大镜
    try:
        _f9 = _F9.truetype(r"C:\Windows\Fonts\msyh.ttc", 12)
    except Exception:
        _f9 = _F9.load_default()
    _d9.text((124, 64), "…", font=_f9, fill=(160, 160, 160))                          # 占位文本不是「搜索」
    _d9.rectangle([278, 0, 1143, 891], fill=(255, 255, 255))                          # 右侧聊天区（白）
    _r9 = CO.search_box_rect(im9)
    ok("白底框能被量出来（x≈91~213）", bool(_r9) and 80 <= _r9[0] <= 100 and 205 <= _r9[2] <= 225, str(_r9))
    _e9 = CO.find_search_entry(im9)
    ok("入口判成 box 且落点在框内（不读占位文本）",
       bool(_e9) and _e9.get("variant") == "box" and 100 <= _e9["x"] <= 200, str(_e9))
    _im9b = _I9.new("RGB", (1139, 890), (237, 237, 239))
    _D9.Draw(_im9b).rectangle([330, 0, 1139, 890], fill=(255, 255, 255))
    ok("没有白框时不许判成 box（本机 icon 形态不受影响）", CO.search_box_rect(_im9b) is None)
except Exception as _e9b:
    ok("几何判据可测", False, str(_e9b)[:80])

print("⑩ 「＋」菜单不许被当成搜索浮层（跨机 r12 的 205×205 帧就是它）")
try:
    from PIL import Image as _I10, ImageDraw as _D10, ImageFont as _F10
    im10 = _I10.new("RGB", (205, 205), (255, 255, 255))
    _d10 = _D10.Draw(im10)
    try:
        _f10 = _F10.truetype(r"C:\Windows\Fonts\msyh.ttc", 18)
    except Exception:
        _f10 = _F10.load_default()
    _d10.text((60, 40), "发起群聊", font=_f10, fill=(20, 20, 20))
    _d10.text((60, 90), "添加朋友", font=_f10, fill=(20, 20, 20))
    _d10.text((60, 140), "写笔记", font=_f10, fill=(20, 20, 20))
    _lis = CO.looks_like_search_popover(im10)
    ok("「＋」菜单 ⇒ 判「不是搜索浮层」", _lis[0] is False and "＋" in _lis[1], str(_lis))
except Exception as _e10:
    ok("「＋」菜单判据可测", False, str(_e10)[:80])

print("⑪ 搜索框路线的结果也要走浮层（跨机 r18 最值钱发现：结果常常是独立浮层）")
_w3 = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
_seg_box = _w3[_w3.index("def open_chat_by_search"):]
_seg_box = _seg_box[:_seg_box.find("\n    def ", 10)]
ok("box 路线里也调 _find_search_popover（先按浮层试）", "_find_search_popover(main)" in _seg_box)
ok("浮层里用 find_popover_row 找目标行", "find_popover_row(_pop[2], name)" in _seg_box)
ok("浮层判否时要关掉它再退回主窗那条路", "_close_search_popover(int(_ph))" in _seg_box)
ok("切会话失败要把列表滚回顶部（**两条**失败分支都调同一个实现）",
   _w3.count("self._scroll_list_to_top(") >= 2 and "def _scroll_list_to_top" in _w3)
ok("还原用的是产品已验证的滚轮形状（times=8, gap_ms=70，不自己换参数）",
   "times=8, gap_ms=70" in _w3)

print("⑫ 现场取证（2026-09-18 晚）：抓到兄弟窗画面 / 文字碎片当图标 / 独立「搜索聊天记录」窗")
# 现场：`wechatauto_logs\fail\20260918-220433_search_entry\`（probe.json + shot.png）
#   shot.png 这张号称"主窗渲染区"的帧，画面其实是**微信自己的「搜索聊天记录」独立窗**
#   （带标题栏、搜索框里还留着上次查询「E」）⇒ 成因＝PrintWindow 连失 12 枪后退回 ImageGrab，
#   而旧 `_region_occluded` 只把**别的进程**算遮挡、同进程兄弟窗判"没遮挡"。
#   后果链：候选块全是文字碎片（9×10 那批）→ "最上一排最靠左"挑中 (65,97) → 一枪点到会话行。
_face_cands = [(65, 97, 9, 10), (76, 97, 10, 10), (103, 105, 21, 19), (120, 104, 11, 17),
               (204, 104, 10, 17), (267, 103, 9, 16), (133, 107, 10, 11), (145, 107, 10, 11),
               (158, 109, 11, 16), (180, 107, 10, 11), (192, 107, 10, 11), (65, 109, 9, 9),
               (76, 109, 10, 9), (58, 163, 20, 12), (81, 163, 14, 12), (185, 163, 10, 12),
               (169, 164, 15, 11)]
_pf = CO.pick_search_icon([b for b in _face_cands if b[2] < 14 or b[3] < 14])
ok("本机那一帧的候选**只剩文字碎片时** ⇒ 不挑（不再点到 (65,97) 会话行）", _pf is None, str(_pf))
ok("最小边长常量与判据在同处定义（9~11px 的块一律不是图标）",
   CO.SEARCH_ICON_MIN == 14 and CO.pick_search_icon([(65, 97, 9, 10), (76, 97, 10, 10)]) is None)


def _searchwin_frame():
    """合成"帧本身是搜索窗画面"：顶部标题条写着「搜索聊天记录」，列表列里放一个 21×19 的假块。"""
    im = Image.new("RGB", (1080, 400), (255, 255, 255))
    d = ImageDraw.Draw(im)
    f = font(26)
    if f:
        d.text((420, 8), "搜索聊天记录", fill=(60, 60, 60), font=f)
    d.rectangle((100, 60, 120, 78), fill=(60, 60, 60))          # 诱惑：21×19 的深块（像图标）
    return im


_swf = _searchwin_frame()
_r_sw = CO.looks_like_search_window_frame(_swf)
ok("帧顶部写着「搜索聊天记录」⇒ 判「这是搜索窗画面」（第二道闸）", _r_sw[0] is True, str(_r_sw))
ok("正常主窗帧不会被误判成搜索窗画面", CO.looks_like_search_window_frame(base_img())[0] is False)
ok("**在这张帧里不许找搜索入口**（原来会挑到文字碎片 ⇒ 点到会话行）",
   CO.find_search_entry(_swf, left=310) is None,
   str(CO.find_search_entry(_swf, left=310)))

_h_src = open(os.path.join(ROOT, "agent", "chat_header.py"), encoding="utf-8").read()
ok("遮挡判据含**同进程兄弟窗**（_OCCLUDE_ALLOW + 纯函数 _occlusion_verdict）",
   "_OCCLUDE_ALLOW" in _h_src and "def _occlusion_verdict" in _h_src)
ok("grab_render 抓图前把「允许的自家窗口」告知遮挡校验",
   "_OCCLUDE_ALLOW.update" in _h_src and _h_src.index("_OCCLUDE_ALLOW.update") < _h_src.index("ImageGrab.grab"))
_ov = CH._occlusion_verdict
ok("同进程**兄弟窗**盖住 ⇒ 判遮挡（这是那条缺陷的根因）",
   _ov([(100, False), (100, False), (100, False), (100, False)], 100) is True)
ok("全是自家允许窗口（主窗/渲染子窗）⇒ 不算遮挡",
   _ov([(100, True)] * 4, 100) is False)
ok("别的进程盖住 ⇒ 判遮挡（旧口径不许丢）",
   _ov([(200, False)] * 4, 100) is True)
ok("采样点全落空（pid=0）⇒ 不判遮挡", _ov([(0, False)] * 4, 100) is False)
ok("只有零星盖住（<1/4，9 个采样点里 1 个）⇒ 不判遮挡",
   _ov([(200, False)] + [(100, True)] * 8, 100) is False)
ok("4 个采样点里 1 个被盖住 ⇒ 判遮挡（旧口径如此，不许悄悄改松）",
   _ov([(200, False)] + [(100, True)] * 3, 100) is True)

_w_src2 = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
ok("搜索窗判据是纯函数（含独立「搜索聊天记录」窗）",
   "def is_search_window(" in _w_src2 and "搜索聊天记录" in _w_src2)
ok("独立搜索窗**能被找到**（_find_search_popover 走 _search_window_hwnds）",
   "_search_window_hwnds(main=main)" in _w_src2)
ok("独立搜索窗**能被关掉**（close_search_popovers 也走同一枚举）",
   "for h, _rect, _cls, _ttl in self._search_window_hwnds(gui=gui):" in _w_src2)
ok("收尾只关**本次新开的**（only_new＝动手前的 hwnd 集合，不碰用户自己开的窗）",
   "only_new" in _w_src2 and "_pre_sw" in _w_src2 and "close_search_popovers(gui=gui, only_new=_pre_sw)" in _w_src2)
ok("「找不到搜索入口」也要留现场（原来是裸返回）", "search_entry_missing" in _w_src2)
try:
    sys.path.insert(0, os.path.join(ROOT, "agent"))
    from agent import wechat as W12
    ok("is_search_window：独立搜索窗 ⇒ True",
       W12.is_search_window("Qt51514QWindowIcon", "搜索聊天记录") is True)
    ok("is_search_window：无边框浮层 ⇒ True",
       W12.is_search_window("Qt51514QWindowToolSaveBits", "Weixin") is True)
    ok("is_search_window：微信主窗 ⇒ False（不许把主窗当搜索窗关掉）",
       W12.is_search_window("Qt51514QWindowIcon", "微信") is False
       and W12.is_search_window("Qt51514QWindowIcon", "") is False)
    ok("is_search_window：朋友圈编辑窗 ⇒ False（同类的坑，别误伤）",
       W12.is_search_window("Qt51514QWindowIcon", "朋友圈") is False)
except Exception as _e12:
    ok("is_search_window 可单测", False, str(_e12)[:80])

print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
