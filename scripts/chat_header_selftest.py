# -*- coding: utf-8 -*-
"""会话头指纹自检：不需要微信（用真字体合成"同会话/异会话"两种情形）。

判据：
  H 基本：区域框在窗口内且避开会话列表、维数固定、同图相似度=1.0、异图低于阈值、空指纹不匹配
  S 存储：没有参照时必须拒绝（不许默认放行）、覆盖式写入、原子写不留 .tmp、参照≠当前必须不匹配
  L 实机：当前会话头能否抓出来 + 抓两次是否稳定（抓不到不算失败，只报 INFO）

用法：py -3 scripts\\chat_header_selftest.py   （非零退出＝有失败）
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))) # 同目录的 `_srcslice`
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

from agent import chat_header as ch # noqa: E402
import _srcmatch as _sm # noqa: E402 空白容忍的源码断言
from PIL import Image, ImageDraw, ImageFont # noqa: E402

OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name, (" · " + extra) if extra else ""))


def fake_window(text, size=(1139, 890), offset=0):
    """造一张像微信主窗的图：**左侧会话列表浅灰 + 右侧聊天区纯白**（好让 detect_pane_left 有边界可找），
    文字带里用真字体写会话名（贴合实机 28px 字号）。"""
    img = Image.new("RGB", size, (245, 245, 245))
    d = ImageDraw.Draw(img)
    pl = int(size[0] * ch.PANE_LEFT_REL)
    d.rectangle((pl, 0, size[0], size[1]), fill=(255, 255, 255))
    box = ch.crop_box(size, pane_left_px=pl)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 28)
    except Exception:
        font = ImageFont.load_default()
    d.text((box[0] + 6 + offset, box[1] + 8), text, fill=(20, 20, 20), font=font)
    return img


print("[H] 基本判据")
size = (1139, 890)
box = ch.crop_box(size)
ck("H1 区域框在窗口内且非空",
   0 <= box[0] < box[2] <= size[0] and 0 <= box[1] < box[3] <= size[1], str(box))
ck("H2 区域避开左侧会话列表（left >= 0.26w）", box[0] >= int(size[0] * 0.26), "left=%d" % box[0])
pl_det = ch.detect_pane_left(fake_window("文件传输助手"))
ck("H2b detect_pane_left 能测出面板左沿（≈0.26w ± 24px）",
   abs(pl_det - int(size[0] * ch.PANE_LEFT_REL)) <= 24, "测得 %d（期望 %d）"
   % (pl_det, int(size[0] * ch.PANE_LEFT_REL)))
ck("H2c 面板左沿在不同窗口宽度下也能测（会话列表是固定像素宽的实机场景见 §三实测）",
   ch.detect_pane_left(fake_window("文件传输助手", size=(900, 680))) > 0,
   "测得 %d" % ch.detect_pane_left(fake_window("文件传输助手", size=(900, 680))))

fa = ch.fingerprint(fake_window("文件传输助手"))
fb = ch.fingerprint(fake_window("示例群"))
ck("H3 指纹维数固定＝BINS", len(fa) == ch.BINS == len(fb), "%d 维" % len(fa))
ck("H4 同一会话（同图）相似度＝1.0",
   abs(ch.similarity(fa, ch.fingerprint(fake_window("文件传输助手"))) - 1.0) < 1e-9)
sim_ab = ch.similarity(fa, fb)
ck("H5 不同会话相似度低于阈值", sim_ab < ch.DEFAULT_THRESHOLD,
   "相似度 %.3f < 阈值 %.2f" % (sim_ab, ch.DEFAULT_THRESHOLD))
ck("H6 match() 与阈值一致", ch.match(fa, fb) is False and ch.match(fa, fa) is True)
ck("H7 空指纹不许被当成匹配", ch.match([], []) is False and ch.similarity([], []) == 0.0)
ck("H8 窗口尺寸不同也能算出同维指纹",
   len(ch.fingerprint(fake_window("文件传输助手", size=(800, 600)))) == ch.BINS)

print("[S] 存储与「没参照不放行」")
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "hdr.json")
    ok0, why0 = ch.verify("filehelper", render=(0, 0, 10, 10), path=p)
    ck("S1 没有参照时 verify ⇒ False（默认拒绝）", ok0 is False, why0)
    ch.remember("filehelper", fa, note="seed", path=p)
    ck("S2 落盘可回读且维数一致", ch.reference("filehelper", p) == fa)
    ck("S3 原子写不留 .tmp", not os.path.exists(p + ".tmp"))
    ch.remember("filehelper", fb, note="覆盖测试", path=p)
    ck("S4 同会话重复 remember 是覆盖（不累积）",
       ch.reference("filehelper", p) == fb and len(ch.load(p)) == 1)
    img = fake_window("示例群")
    ch.remember("filehelper", ch.fingerprint(img), path=p)
    ck("S5 参照＝当前 ⇒ 匹配", ch.match(ch.reference("filehelper", p), ch.fingerprint(img)) is True)
    ch.remember("filehelper", fa, path=p) # 参照换成"文件传输助手"，当前图是"示例群"
    ck("S6 参照≠当前 ⇒ 不匹配（这就是防发错会话的那道闸）",
       ch.match(ch.reference("filehelper", p), ch.fingerprint(img)) is False)
    # —— 分尺寸记忆——
    ck("S7 size_key 形如 宽x高", ch.size_key((1139, 890)) == "1139x890")
    ch.remember("filehelper", fa, path=p, size="1139x890")
    ch.remember("filehelper", fb, path=p, size="900x680")
    ck("S8 同一会话可存多个尺寸，且按尺寸取回",
       ch.reference("filehelper", p, size="900x680") == fb
       and ch.reference("filehelper", p, size="1139x890") == fa,
       "已有尺寸：%s" % ch.ref_sizes("filehelper", p))
    ck("S9 严格取参照时，该尺寸没有 ⇒ 返回空（check() 会判 no_ref ⇒ 不拦只留痕，而不是拿别的尺寸误拦）",
       ch.reference("filehelper", p, size="777x555", strict=True) == []
       and ch.reference("filehelper", p, size="1139x890", strict=True) == fa)

print("[P] 短名假阳性（分数像、形状不像）与「学歪的参照」")
# ⛔ 这两族以前**零覆盖**——变异 `chat_header.py:264`（摘掉形状闸）与 `:305`
#   （`if is_blank(fp):` → `if False:`）之后，本判据仍 19 通过 / 0 失败。
#   ① 两个**不同的短群名**在幅度指标上能拿到 0.98（真机实测 0.948 ≥ 0.90）⇒ 只看相似度会把
#      它们判成「同一个会话」（假阳性）；形状闸（`pattern_score >= DEFAULT_COSINE`）必须拦下。
#   ② `remember()` 的 `is_blank` 闸：空白帧学进参照库会让该尺寸档**永远判不匹配**
_ia, _ib = fake_window("abc"), fake_window("abd")
_pa, _pb = ch.fingerprint(_ia), ch.fingerprint(_ib)
_sim_p, _cos_p = ch.similarity(_pa, _pb), ch.pattern_score(_pa, _pb)
ck("P1 两个不同短群名：相似度 ≥ 0.90 但形状闸拦下 ⇒ match() 为 False（相似度那条闸有守备）",
   _sim_p >= ch.DEFAULT_THRESHOLD and _cos_p < ch.DEFAULT_COSINE and ch.match(_pa, _pb) is False,
   "相似度 %.3f（阈值 %.2f）· 形状 %.3f（阈值 %.2f）"
   % (_sim_p, ch.DEFAULT_THRESHOLD, _cos_p, ch.DEFAULT_COSINE))
with tempfile.TemporaryDirectory() as td_p:
    _pp = os.path.join(td_p, "hdr.json")
    ch.remember("shortname", _pa, path=_pp, size=ch.size_key(_ia))
    _cap0 = ch.capture_image # 用合成帧当"当前画面"，把 check() 的判分分支钉住
    try:
        ch.capture_image = lambda gui=None, render=None: _ib
        _r = ch.check("shortname", path=_pp)
    finally:
        ch.capture_image = _cap0
    ck("P2 check() 的 mismatch 分支：分数像但形状不像 ⇒ 判 mismatch（不许当 ok 放行）",
       (_r.get("status") == "mismatch" and float(_r.get("sim") or 0) >= ch.DEFAULT_THRESHOLD
        and "形状" in (_r.get("note") or "")),
       "status=%s · %s" % (_r.get("status"), _r.get("note")))
    ch.remember("blank", [255] * 64, path=_pp)
    ck("P3 空白帧（全 255）一律不进参照库 ⇒ reference 为空",
       ch.reference("blank", _pp) == [], "reference=%s" % (ch.reference("blank", _pp) or "[]"))
    ch.remember("degen", [0] * 63 + [255], path=_pp)
    ck("P4 退化帧（[0]*63+[255]）一律不进参照库 ⇒ reference 为空",
       ch.reference("degen", _pp) == [], "reference=%s" % (ch.reference("degen", _pp) or "[]"))
    # —— 隔离测试：`is_blank` 与 `degenerate_reason` 是**两道**
    #    闸，下游会替上游挡住同一类帧 ⇒ 把下游打桩成"看不出问题"，被守的那一道才会单独现形。
    _deg0 = ch.degenerate_reason
    try:
        ch.degenerate_reason = lambda fp, bins=ch.BINS: ""
        ch.remember("blank2", [255] * 64, path=_pp)
        ck("P5 is_blank 是**独立**那道闸（下游打桩后仍拒绝空白帧）",
           ch.reference("blank2", _pp) == [], "reference=%s" % (ch.reference("blank2", _pp) or "[]"))
    finally:
        ch.degenerate_reason = _deg0
    _blank0 = ch.is_blank
    try:
        ch.is_blank = lambda fp: False
        ch.remember("degen2", [0] * 63 + [255], path=_pp)
        ck("P6 degenerate_reason 是**独立**那道闸（上游打桩后仍拒绝退化帧）",
           ch.reference("degen2", _pp) == [], "reference=%s" % (ch.reference("degen2", _pp) or "[]"))
    finally:
        ch.is_blank = _blank0
    # —— 的那道缝：真造帧「标题带里只有两条竖线、没有字」（审计的 S05/S12/S19/S20 那族）——
    #    指纹语义（见 `chat_header.py` 的 `degenerate_reason` docstring）：逐列暗点密度，
    #    `0`＝这一列没有字、`255`＝满墨。两条竖线 ⇒ **只有 2 个有墨的列**。
    #   审计实测（20 个现场真跑三家函数）：这类帧 `is_blank` 判否、`degenerate_reason` 判否
    #    ⇒ `remember()` 照收 ⇒ 该尺寸档此后跟正常帧比只有 sim≈0.71 ⇒ **长期漏发**。
    #    ⇒ 收口落在**入库入口**（`remember()` 的「有墨的列 < 4」），下面 P7~P9 把这条守备钉住。
    _fp_s05 = [0] * 20 + [145, 150] + [0] * (ch.BINS - 22)
    ck("P7 缝的来历（先说清「两道闸都放行」，否则 P8 会被当成「随手加的一条」）：这类帧 "
       "is_blank / degenerate_reason **都不拦**",
       ch.is_blank(_fp_s05) is False and ch.degenerate_reason(_fp_s05) == "",
       "is_blank=%s · 退化闸=%r" % (ch.is_blank(_fp_s05), ch.degenerate_reason(_fp_s05)))
    ch.remember("s05", _fp_s05, path=_pp, size="1139x890")
    ck("P8 反例锚：S05 那类帧**必须进不去参照库**（进库＝该尺寸档此后一直判 mismatch＝漏发）",
       ch.reference("s05", _pp) == [], "reference=%s" % (ch.reference("s05", _pp) or "[]"))
    ch.remember("s05good", _pa, path=_pp, size="1139x890")
    ck("P8b 阳性对照：正常帧（有墨的列远多于 4）照旧入库 ⇒ 上面那条不是「什么都拒」",
       ch.reference("s05good", _pp) == _pa,
       "有墨列=%d" % len([1 for _x in _pa if _x]))
    # P9：把这类帧**绕过 remember 直接落盘**（＝缝存在时的老行为）⇒ 同尺寸档的正常帧判 mismatch。
    #     这条不是为了"留个旧行为"，而是把 P8 防住的**后果**钉在判据里（防将来有人把入库闸拆了还觉得没事）。
    ch.save({"s05old": {"sizes": {"1139x890": {"fp": list(_fp_s05), "when": "",
                                               "band": ch.BAND_VERSION}}}}, _pp)
    _cap1 = ch.capture_image
    try:
        ch.capture_image = lambda gui=None, render=None: fake_window("文件传输助手")
        _r9 = ch.check("s05old", path=_pp)
    finally:
        ch.capture_image = _cap1
    ck("P9 后果锚：库里若真有这类参照 ⇒ 同尺寸的正常帧判 **mismatch**",
       _r9.get("status") == "mismatch" and float(_r9.get("sim") or 0) < ch.DEFAULT_THRESHOLD,
       "status=%s · %s" % (_r9.get("status"), _r9.get("note")))

print("[T] 自绘标题条上方的自适应文字带")


def _fake_with_titlebar(text, size=(1076, 1046), bar=(38, 50), text_y=54):
    """造一张**带自绘深色标题条**的渲染区图（真机几何：条子在 y≈38~50，标题文字在其下方）。"""
    img = Image.new("RGB", size, (245, 245, 245))
    d = ImageDraw.Draw(img)
    pl = int(size[0] * ch.PANE_LEFT_REL)
    d.rectangle((pl, 0, size[0], size[1]), fill=(255, 255, 255))
    d.rectangle((pl, bar[0], size[0], bar[1]), fill=(38, 38, 38))
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 28)
    except Exception:
        font = ImageFont.load_default()
    d.text((pl + 16, text_y), text, fill=(20, 20, 20), font=font)
    return img


_timg = _fake_with_titlebar("E")
_tbox = ch.crop_box(_timg.size)
_ty0 = ch.detect_band_y0(_timg, _tbox[0], _tbox[2])
ck("T1 自适应把带子挪到标题条**下方**（> 兜底的 %d）" % ch.BAND_PX[1],
   _ty0 > int(ch.BAND_PX[1]), "测出 y0=%d（兜底 %d）" % (_ty0, ch.BAND_PX[1]))
_old_fp = ch.fingerprint(_timg, band_px=ch.BAND_PX)
_new_fp = ch.fingerprint(_timg)
_old_ink = len([v for v in _old_fp if v])
_new_ink = len([v for v in _new_fp if v])
ck("T2 **反例锚**：旧固定带压在标题条上 ⇒ 退化（几乎每列都有墨）",
   ch.degenerate_reason(_old_fp) != "" or _old_ink >= 40,
   "旧带墨列=%d · %s" % (_old_ink, ch.degenerate_reason(_old_fp) or "未判退化"))
ck("T3 自适应带 ⇒ **非退化**（墨列集中，不是满屏）",
   (not ch.degenerate_reason(_new_fp)) and _new_ink < 40,
   "新带墨列=%d · %s" % (_new_ink, ch.degenerate_reason(_new_fp) or "未判退化"))
_plain = fake_window("E", size=(1076, 1046))
_pbox = ch.crop_box(_plain.size)
ck("T4 没有那条横条 ⇒ 不误挪（保持兜底 y0）",
   ch.detect_band_y0(_plain, _pbox[0], _pbox[2]) == int(ch.BAND_PX[1]),
   "y0=%d" % ch.detect_band_y0(_plain, _pbox[0], _pbox[2]))
_tp = os.path.join(tempfile.mkdtemp(prefix="pm-ch-"), "chat_headers.json")
_timg2 = _fake_with_titlebar("文件传输助手")
_new_fp2 = ch.fingerprint(_timg2)
ck("T5a 自适应带上的正常标题**能过入库闸**（有墨列 ≥ 4）",
   len([v for v in _new_fp2 if v]) >= 4, "有墨列=%d" % len([v for v in _new_fp2 if v]))
ch.remember("filehelper", _new_fp2, path=_tp, size="1076x1046")
ck("T5 新学的参照带当前带子版本 ⇒ 取得到",
   len(ch.reference("filehelper", path=_tp, size="1076x1046", strict=True)) == len(_new_fp2),
   "reference=%d 维" % len(ch.reference("filehelper", path=_tp, size="1076x1046", strict=True)))
import json as _json # noqa: E402
with open(_tp, "w", encoding="utf-8") as _f: # 旧版参照（旧带子）
    _json.dump({"filehelper": {"sizes": {"1076x1046": {"fp": _new_fp2, "band": 1}}}}, _f)
ck("T6 **带子算法变了 ⇒ 旧参照当「没有」**（no_ref 不拦发送，而不是 mismatch 去拦）",
   ch.reference("filehelper", path=_tp, size="1076x1046", strict=True) == [])

print("[V2] 会话头带子**同源**")
try:
    from agent import chat_ocr as _co # noqa: E402
    _vimg = _fake_with_titlebar("演示（3）")
    _vb_ch = tuple(ch.band_box(_vimg))
    _vb_ocr = tuple(_co.header_box(_vimg))
    ck("V2a 指纹侧的带子被推到标题条**下方**（y0 > 兜底 %d）" % ch.BAND_PX[1],
       _vb_ch[1] > int(ch.BAND_PX[1]), "band_box y0=%d" % _vb_ch[1])
    ck("V2b **OCR 侧与指纹侧是同一块带子**（老写法固定 y0=38 ⇒ 这条必红）",
       _vb_ocr == _vb_ch, "ocr=%s · header=%s" % (_vb_ocr, _vb_ch))
    ck("V2c 反例锚：固定 y0 确实压在标题条（38~50）里 —— 这就是当时 OCR 读到标题条的原因",
       int(ch.BAND_PX[1]) < 50, "固定 y0=%d" % int(ch.BAND_PX[1]))
    ck("V2d 没有那条横条时两边都不动（不会把老版微信的带子弄歪）",
       tuple(_co.header_box(_plain)) == tuple(ch.band_box(_plain)),
       "ocr=%s · header=%s" % (tuple(_co.header_box(_plain)), tuple(ch.band_box(_plain))))
    ck("V2e `chat_ocr.header_box` 真的**调** `band_box`（源码级锚：防以后又被抄成第二份实现）",
       "band_box(" in open(os.path.join(ROOT, "agent", "chat_ocr.py"), encoding="utf-8").read())
except Exception as _e:
    ck("V2 同源锚可跑（chat_ocr 能 import）", False, str(_e)[:100])

# ══════════════════════════════════════════════════════════════════════════════
# W/X/Y/Z 段：
# 全部**离线合成帧** —— 不起 GUI、不碰真实微信/鼠标。
# ══════════════════════════════════════════════════════════════════════════════


def _font28():
    try:
        return ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 28)
    except Exception:
        return ImageFont.load_default()


def _old_pane_scan(img, lo_rel=0.15, hi_rel=0.60, need=24):
    """**老口径**的左沿扫描：下界 ＝ `int(w × lo_rel)`（纯相对、随宽度线性放大）。

    判据用它当**反例锚**：同一帧按老代码扫一遍 ⇒ 宽窗下返回 0（真机 2538 宽实测也是 0）。
    """
    g = img.convert("L")
    w, h = g.size
    px = g.load()
    y0, y1 = int(h * 0.25), int(h * 0.75)
    step = max(1, (y1 - y0) // 12)
    run, start = 0, 0
    for x in range(int(w * lo_rel), min(w, int(w * hi_rel))):
        vals = [px[x, y] for y in range(y0, y1, step)]
        if not vals:
            continue
        if sum(vals) / len(vals) >= ch.WHITE:
            if run == 0:
                start = x
            run += 1
            if run >= need:
                return start
        else:
            run = 0
    return 0


def _best_row_std(img):
    """老口径唯一的"有没有结构"读数：逐行 std 的最大值。"""
    small = img.convert("L").resize((64, 64))
    px = small.load()
    best = 0.0
    for y in range(64):
        row = [px[x, y] for x in range(64)]
        m = sum(row) / 64.0
        best = max(best, (sum((a - m) ** 2 for a in row) / 64.0) ** 0.5)
    return best


def fake_wide(text="文件传输助手", size=(2538, 1589), pl=331, white_to=380):
    """**真机最大化那一帧**的模型：
    会话列表**固定像素宽**（左沿 331）、紧跟一条白边到 380、再往右是浅灰「气泡带」到 0.60w，
    最后又白 —— 复刻真机读数：**从 `0.15×宽`（380）起扫，找不到连续 24 列近纯白**。
    """
    img = Image.new("RGB", size, (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.rectangle((pl, 0, white_to, size[1]), fill=(255, 255, 255))
    d.rectangle((white_to, 0, int(size[0] * 0.60), size[1]), fill=(246, 246, 246))
    d.rectangle((int(size[0] * 0.60), 0, size[0], size[1]), fill=(255, 255, 255))
    d.text((pl + 10, 46), text, fill=(20, 20, 20), font=_font28())
    return img


def _bar_frame(text="文件传输助手", size=(1076, 1046), bar=(96, 103), text_y=112, pl=330):
    """顶部那条**自绘深色横条**（`bar` = 上边/下边）+ 条子下方的会话名（真机几何见 `detect_band_y0`）。"""
    img = Image.new("RGB", size, (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.rectangle((pl, 0, size[0], size[1]), fill=(255, 255, 255))
    d.rectangle((pl, bar[0], size[0], bar[1]), fill=(38, 38, 38))
    d.text((pl + 12, text_y), text, fill=(20, 20, 20), font=_font28())
    return img


def _name_frame(name, size=(1139, 890), pl=331, y=46):
    """真机那一档几何（会话列表 331 宽）下的会话头帧，文字写在带子里。"""
    img = Image.new("RGB", size, (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.rectangle((pl, 0, size[0], size[1]), fill=(255, 255, 255))
    d.text((pl + 12, y), name, fill=(20, 20, 20), font=_font28())
    return img


def _deg_short(fp):
    v = [int(x) for x in (fp or [])]
    return len([i for i, x in enumerate(v) if x != 0]) # 有墨（非零）的列数，同 `remember()` 的口径


print("[W] 窗口几何：**最大化/宽窗**也要测得出左沿并出指纹")
print("    真机实测（审计）：渲染 2538×1589 时 `detect_pane_left=0`、`fingerprint` 空、`check=no_capture`；"
      "同一帧只把下界改小到 ≤331 就恢复（0.12→331 / 0.10→331 / 0.04→331）。")
for _wsz in ((1178, 738), (2178, 1190), (2538, 1589)):
    _wi = fake_wide(size=_wsz)
    ck("W1 宽窗 %s：量出固定像素左沿 331（会话列表不随宽度缩放）" % (_wsz,),
       abs(ch.detect_pane_left(_wi) - 331) <= 8,
       "测得 %d · pane_left_for=%d" % (ch.detect_pane_left(_wi), ch.pane_left_for(_wi)))
_wimg = fake_wide()
_wh = _wimg.size[0]
ck("W1b 反例锚：同一帧按**老口径**（下界 = 0.15×%d = %d，越过真左沿 331）扫 ⇒ **0**"
   % (_wh, int(_wh * 0.15)),
   _old_pane_scan(_wimg) == 0 and int(_wh * 0.15) > 331,
   "老口径扫描=%d（新口径=%d）" % (_old_pane_scan(_wimg), ch.detect_pane_left(_wimg)))
_wfp = ch.fingerprint(_wimg)
ck("W1c 本帧必须出**非空、非退化**指纹（老代码在最大化档恒 `no_capture`：整条会话头闸失效）",
   bool(_wfp) and not ch.degenerate_reason(_wfp),
   "%d 维 · 有墨列=%d · 退化=%r" % (len(_wfp), _deg_short(_wfp), ch.degenerate_reason(_wfp) or ""))
ck("W1d 反例锚：退回 `0.26×宽` 兜底比例（老代码在 pl=0 时就走这条）⇒ 带子落进气泡带 ⇒ **空指纹**",
   ch.fingerprint(_wimg, pane_left_rel=ch.PANE_LEFT_REL) == [],
   "指纹 %s" % (ch.fingerprint(_wimg, pane_left_rel=ch.PANE_LEFT_REL) or "[]"))
ck("W1e 窄窗照旧：900×680 / 1139×890 也能量出左沿（不是「只顾宽窗」）",
   ch.detect_pane_left(fake_wide(size=(900, 680), pl=280, white_to=320)) > 0
   and ch.detect_pane_left(fake_window("文件传输助手")) > 0,
   "%d / %d" % (ch.detect_pane_left(fake_wide(size=(900, 680), pl=280, white_to=320)),
                ch.detect_pane_left(fake_window("文件传输助手"))))
# —— W1f：**锚点必须走 `pane_left_for` 这个唯一入口**（老口径扫不到白列时不能掉进比例兜底）——
# 现场＝ 聊天区左列被消息气泡占满 ⇒ 老口径返回 0，而"竖栏右沿 + 列表固定宽"
#    的结构锚仍认得出左沿。这一帧的真左沿 360；名字写在 660（只落在结构锚的带子里）。
_bub = Image.new("RGB", (1139, 890), (245, 245, 245))
_bd = ImageDraw.Draw(_bub)
_bd.rectangle((0, 0, 60, 890), fill=(40, 40, 40)) # 深色竖导航栏
_bd.rectangle((60, 0, 360, 890), fill=(245, 245, 245)) # 会话列表（固定 300 宽）
_bd.rectangle((360, 0, 911, 890), fill=(230, 230, 230)) # 气泡带（非白 ⇒ 老口径扫不出白列）
_bd.rectangle((911, 0, 1139, 890), fill=(255, 255, 255))
_bd.text((660, 46), "文件传输助手", fill=(20, 20, 20), font=_font28())
ck("W1f 老口径在这帧上就是 0，结构锚给 360 ⇒ `pane_left_for` 必须取到 360",
   ch.detect_pane_left(_bub) == 0 and ch.pane_left_for(_bub) == 360,
   "老口径=%d · 结构锚=%d · pane_left_for=%d"
   % (ch.detect_pane_left(_bub), ch.detect_pane_left_alt(_bub), ch.pane_left_for(_bub)))
_bfp = ch.fingerprint(_bub)
ck("W1g **`fingerprint` 必须走 `pane_left_for`**（名字只落在结构锚那条带子里 ⇒ 非空；"
   "退回老口径那一层就掉进 `0.26×宽` 兜底 ⇒ 空指纹）",
   bool(_bfp) and ch.fingerprint(_bub, pane_left_rel=ch.PANE_LEFT_REL) == [],
   "生产路径 %d 维 · 反例锚（0.26w 兜底）%s"
   % (len(_bfp), ch.fingerprint(_bub, pane_left_rel=ch.PANE_LEFT_REL) or "[]"))

print("[X] 自适应带子的**钳位边界**")
_xa = _bar_frame(bar=(38, 50), text_y=54)
_xb = _bar_frame(bar=(96, 103), text_y=112, size=(1076, 1046)) # 条上边 ≥96：超出老 max_scan
_xc = _bar_frame(bar=(70, 103), text_y=112, size=(1076, 890)) # 条底边 103：被老 max_y0=72 钳住


def _y0_pair(im):
    box = ch.crop_box(im.size, pane_left_px=ch.pane_left_for(im))
    return (ch.detect_band_y0(im, box[0], box[2]),
            ch.detect_band_y0(im, box[0], box[2], max_scan=96, max_y0=72))


def _fp_old_band(im, y0):
    return ch.fingerprint(im, band_px=(ch.BAND_PX[0], y0, ch.BAND_PX[2], ch.BAND_PX[3]),
                          pane_left_px=ch.pane_left_for(im))


_xay, _xao = _y0_pair(_xa)
ck("X1 阳性对照：正常几何（条 38~50）新老口径都把带子挪到条下方（没改坏）",
   _xay == _xao == 53 and not ch.degenerate_reason(ch.fingerprint(_xa)),
   "新=%d 老=%d" % (_xay, _xao))
_xby, _xbo = _y0_pair(_xb)
ck("X2 **条上边 ≥96（老 max_scan 之外）**：新口径把带子推下去且指纹干净；老口径退回 38 ⇒ 空指纹",
   _xby > int(ch.BAND_PX[1]) and _xbo == int(ch.BAND_PX[1])
   and (not ch.fingerprint(_xb) or not ch.degenerate_reason(ch.fingerprint(_xb)))
   and _fp_old_band(_xb, _xbo) == [],
   "新 y0=%d（有墨列=%d）· 老 y0=%d（老带子指纹=%s）"
   % (_xby, _deg_short(ch.fingerprint(_xb)), _xbo, _fp_old_band(_xb, _xbo) or "[]"))
_xcy, _xco = _y0_pair(_xc)
ck("X3 **条底边 103（被老 max_y0=72 钳住）**：新口径指纹可入库；老带子 ⇒ 「几乎每一列都有墨」的退化指纹"
   "…",
   _xcy > int(ch.BAND_PX[1]) and not ch.degenerate_reason(ch.fingerprint(_xc))
   and ch.degenerate_reason(_fp_old_band(_xc, _xco)) != "",
   "新 y0=%d（有墨列=%d）· 老 y0=%d ⇒ 老带子 %r"
   % (_xcy, _deg_short(ch.fingerprint(_xc)), _xco, ch.degenerate_reason(_fp_old_band(_xc, _xco))))

print("[Y] 帧质量与遮挡的缝")
_half_lr = Image.new("RGB", (400, 300), (255, 255, 255))
_hd2 = ImageDraw.Draw(_half_lr)
_hd2.rectangle((0, 0, 200, 300), fill=(0, 0, 0)) # **左半深、右半浅**（每行都不平）
ck("Y1 左右两大色块拼起来的帧 ⇒ **不是好帧**（每列都是平的 ⇒ 一个方向没有结构）",
   ch._frame_ok(_half_lr) is False)
ck("Y1b 反例锚：老口径（只看**逐行**结构）在这帧上读数 = %.1f（≥3 ⇒ 它会判「好帧」，这就是那道缝）"
   % _best_row_std(_half_lr),
   _best_row_std(_half_lr) >= 3.0, "best_row=%.1f" % _best_row_std(_half_lr))
ck("Y1c 兜底帧准入也必须拒收它（`_mono` 认同一族，否则它会以「兜底帧」身份回到链上）",
   ch._mono(_half_lr) is True)
_goodY = Image.new("RGB", (400, 300), (250, 250, 250)) # 像"聊天区"：白底 + 几块文字/头像
_gdY = ImageDraw.Draw(_goodY)
for _iy in range(6):
    _gdY.rectangle((20, 20 + _iy * 40, 200 + _iy * 20, 44 + _iy * 40), fill=(40, 40, 40))
ck("Y1d 阳性对照：真画面（文字块/头像那样的结构）仍是好帧、也不算 mono",
   ch._frame_ok(_goodY) is True and ch._mono(_goodY) is False)
ck("Y2 **退化渲染矩形**（<8px）⇒ 按**未知＝遮挡**处理（老写法 `return False` 会退回抓屏）",
   ch._region_occluded((0, 0, 4, 4), 1234) is True)
ck("Y2b 正常矩形照旧走采样（不因为上面那条变成「永远判遮挡」）",
   ch._occlusion_verdict([(100, True)] * 4, 100) is False)

print("[Z] 单字会话名的**既定代价**")
_zE = ch.fingerprint(_name_frame("E"))
_zI = ch.fingerprint(_name_frame("I"))
print("    INFO 合成帧读数：E ⇒ 有墨（≥110）列 %s、非零列 %d、退化=%r；I ⇒ 有墨列 %s、退化=%r"
      % ([i for i, v in enumerate(_zE) if v >= 110], _deg_short(_zE), ch.degenerate_reason(_zE) or "",
         [i for i, v in enumerate(_zI) if v >= 110], ch.degenerate_reason(_zI) or ""))
print("INFO 真机：「E」在 9 档尺寸下都是 `有墨的列只有 1 个（不是一行字）` ⇒ 学不到参照")
ck("Z1 单字名的帧要么判退化、要么有墨列 < 4（两条入库闸至少命中一条）",
   bool(ch.degenerate_reason(_zI)) or _deg_short(_zI) < 4,
   "I: %r · 非零列=%d" % (ch.degenerate_reason(_zI) or "", _deg_short(_zI)))
with tempfile.TemporaryDirectory() as _tdZ:
    _pZ = os.path.join(_tdZ, "hdr.json")
    ch.remember("singleE", _zE, path=_pZ, size="1139x890")
    ch.remember("singleI", _zI, path=_pZ, size="1139x890")
    ck("Z2 单字会话名的帧**进不了参照库**（remember 两道闸拦下 ⇒ reference 为空）",
       ch.reference("singleE", _pZ, size="1139x890", strict=True) == []
       and ch.reference("singleI", _pZ, size="1139x890", strict=True) == [],
       "E 非零列=%d · I 非零列=%d" % (_deg_short(_zE), _deg_short(_zI)))
    _capZ = ch.capture_image
    try:
        ch.capture_image = lambda gui=None, render=None: _name_frame("E")
        _rz = ch.check("singleE", path=_pZ)
    finally:
        ch.capture_image = _capZ
    ck("Z3 **后果锚**：学不到参照 ⇒ `check()` 判 **no_ref（不拦发送）**，"
       "**不许**判 mismatch（那才是「这个会话永久漏发」）",
       _rz.get("status") == "no_ref", "status=%s · %s" % (_rz.get("status"), _rz.get("note")))
    _nameZ = ch.fingerprint(_name_frame("文件传输助手"))
    ch.remember("multi", _nameZ, path=_pZ, size="1139x890")
    ck("Z4 阳性对照：多字名字同尺寸 ⇒ 正常入库（不是「什么都拒」）",
       len(ch.reference("multi", _pZ, size="1139x890", strict=True)) == len(_nameZ),
       "有墨列=%d" % _deg_short(_nameZ))

print("[F] 帧质量闸")
_blk = Image.new("RGB", (400, 300), (0, 0, 0))
_half = Image.new("RGB", (400, 300), (255, 255, 255))
_hd = ImageDraw.Draw(_half)
_hd.rectangle((0, 150, 400, 300), fill=(0, 0, 0)) # 半黑半白（mean/std 都像好帧）
ck("F1 纯黑帧 ⇒ 不是好帧", ch._frame_ok(_blk) is False)
ck("F2 **半黑半白色块帧 ⇒ 不是好帧**（每行都是平的：真画面总有有结构的那几行）",
   ch._frame_ok(_half) is False)
_good = Image.new("RGB", (400, 300), (250, 250, 250)) # 像"聊天区"：白底 + 几块文字/头像
_gd = ImageDraw.Draw(_good)
for _i in range(6):
    _gd.rectangle((20, 20 + _i * 40, 200 + _i * 20, 44 + _i * 40), fill=(40, 40, 40))
ck("F3 阳性对照：有内容（文字块/头像那样的结构）的画面 ⇒ 仍是好帧", ch._frame_ok(_good) is True,
   "mean/std/极差 都在阈上）")
ck("F4 纯黑帧的指纹**必须是空**（旧口径会给 [255]*64 这种看着有依据的假指纹）",
   ch.fingerprint(_blk, band_px=ch.BAND_PX) == [],
   "指纹 %s" % (ch.fingerprint(_blk, band_px=ch.BAND_PX) or "[]"))
_old_mean, _old_std = 140.0, 126.9 # 侦察线实测的"半黑半白"帧读数
ck("F5 反例锚：老口径（只要 mean>25 且 std>12）**放行**这张帧",
   (_old_mean > 25) and (_old_std > 12), "mean=%s std=%s" % (_old_mean, _old_std))

print("[L] 实机（抓不到不算失败）")
try:
    fp1 = ch.capture()
except Exception as _e_l: # ⚠️ 微信没在运行时 `capture()` 是**抛异常**，不是"抓不到"
    print("  INFO 微信此刻没在运行 / 主窗不可见（%s）—— 这一段只是 INFO，不算失败" % str(_e_l)[:60])
    fp1 = []
if fp1:
    time.sleep(1.5)
    fp2 = ch.capture()
    s = ch.similarity(fp1, fp2) if fp2 else 0.0
    print("  INFO 当前会话头指纹 %d 维；两次抓取相似度 %.3f（稳定性）" % (len(fp1), s))
    other = ch.fingerprint(fake_window("示例群"))
    print("  INFO 与合成「示例群」相似度 %.3f（应明显低于阈值 %.2f）"
          % (ch.similarity(fp1, other), ch.DEFAULT_THRESHOLD))
else:
    print("  INFO 这次没抓到（微信窗口不可见/未开）——不算失败")

print("\n[K] 深色主题适配（2026-09-30 修根因）")
# 起因：`fingerprint` 只数**暗点**（浅底深字假设）⇒ 深色主题下剖面平坦 ⇒ 空指纹
#   ⇒ `check()` 报 `no_capture`（文案却写"抓不到窗口画面（最小化/收进托盘/被遮挡）"，
#     把人一路带偏到"最小化"上）⇒ 投递档永远确认不了目标会话 ⇒ **发不出去**。
# 修法：浅色数暗点 / 深色数亮点；
#   且 `detect_band_y0` 在深色下**不做**"找全宽深色横条"的扫描 —— 那条横条是**浅色主题特有**的，
#   深色下"整行都是深色"恒真 ⇒ 扫描必然给出错误 y0（实测偏到 y=143，真值 ≈90）。


def _mono(bg, fg, size=(1139, 890)):
    im = Image.new("L", size, bg)
    d = ImageDraw.Draw(im)
    d.rectangle([int(size[0] * 0.30), 44, int(size[0] * 0.72), 92], fill=fg) # 模拟一段"文字块"
    return im


_dark_m, _light_m = _mono(26, 235), _mono(246, 24)
ck("K1 深底浅字 ⇒ detect_polarity 判 dark",
   ch.detect_polarity(_dark_m) == "dark", ch.detect_polarity(_dark_m))
ck("K2 浅底深字 ⇒ detect_polarity 判 light",
   ch.detect_polarity(_light_m) == "light", ch.detect_polarity(_light_m))
_fd = ch.fingerprint(_dark_m)
ck("K3 ★深色图**能**提出指纹（改前恒 0 条 ⇒ check 报 no_capture ⇒ 发不出去）",
   len(_fd) > 0, "条数=%d" % len(_fd))
ck("K4 回归锚：浅色窗口的指纹照旧能提出（浅色路径不许被改坏）",
   len(ch.fingerprint(fake_window("文件传输助手"))) > 0, "")
_src_h = open(os.path.join(ROOT, "agent", "chat_header.py"), encoding="utf-8").read()
ck("K5 源码锚：`detect_band_y0` 带 `pol` 参数，且深色档**直接早退**（不做「找深色横条」扫描）",
   'max_y0: int = None, pol: str = "light"' in _src_h and 'if pol == "dark":' in _src_h, "")
ck("K6 源码锚：`fingerprint` 把极性传进 `_raw`（两处调用都传）",
   "_raw(box, _pol)" in _src_h and "_raw(_box_wide, _pol)" in _src_h, "")

print("\n[L] 面板左沿的第三来源：绿底行右边界（2026-09-30，由用户提示）")
# 起因：`pane_left_for` 原来只覆盖"过冲"这一侧（老口径 > 结构锚 + 80 ⇒ 改用结构锚），
#   但它也会**偏小** —— 实测深色窄窗 1121x1091：老口径给 291，而绿底行右边界是 443（差 152px）。
# 判断面板左沿的起始点"。


def _frame_with_green(has_green=True):
    im = Image.new("RGB", (1200, 800), (35, 35, 36))          # 深色底（模拟深色主题）
    d = ImageDraw.Draw(im)
    d.rectangle([300, 0, 1200, 800], fill=(245, 245, 245))    # 右侧浅色（让老口径有白列可找）
    if has_green:
        d.rectangle([160, 120, 400, 217], fill=(77, 163, 110)) # 实测绿底的颜色
    return im


_g = _frame_with_green(True)
_r = ch.detect_green_right(_g)
ck("L1 能测出绿底行的右边界（合成帧真值 400，容差 6px）", abs(_r - 400) <= 6, "测得 %d" % _r)
ck("L2 没有绿底行 ⇒ 返回 0（不瞎猜）", ch.detect_green_right(_frame_with_green(False)) == 0,
   str(ch.detect_green_right(_frame_with_green(False))))
_src_l = open(os.path.join(ROOT, "agent", "chat_header.py"), encoding="utf-8").read()
from _srcslice import func_src as _fsrc # noqa: E402
# ⚠ 用 AST 锚定的 `func_src` 取函数体，**不要**用「按 def 行做文本切片」的写法 ——
#   文本切片会被"注释里提到同名函数"骗到（本文件 T21 那次就踩过：docstring 里
#   写了 `_pending_enabled()` ⇒ 首个匹配命中注释 ⇒ 判据假红）。
_seg = _fsrc(_src_l, "detect_green_right")
ck("L3 ★性能锚：`detect_green_right` **不许**调 `chat_ocr.green_bands`"
   "（实测它逐像素扫全图 ⇒ 单帧 5.7s，而这是热路径；改成粗采样后 0.005s）",
   "green_bands(" not in _seg, "")
ck("L4 源码锚：绿带纠偏带 **>60px 阈值**（不是无条件覆盖 —— 实测 1886x1421 两者只差 2px 时不许动）",
   "abs(int(val) - _gx) > 60" in _src_l, "")
ck("L5 源码锚：`detect_green_right` 有帧级缓存（同一帧被十几个函数各调一遍）",
   "_pm_green_right" in _seg, "")

# ── L6/L7 越界钳位（自己引入又修掉的边界 bug）──
# 症状：某帧的面板左沿贴近图宽 ⇒ 调用方 `crop(pl+10, …)` 抛 "right < left" ⇒ 整条链崩。
# 修法：① `detect_green_right` 右边界 ≥ 80% 宽时不采信（那多半是聊天区里的绿色气泡/图片）；
#       ② `pane_left_for` 收尾把结果钳在 ≤85% 宽（保证任何下游 +dx 都不越界）。
def _wide_green_frame():
    im = Image.new("RGB", (1000, 700), (35, 35, 36))
    d = ImageDraw.Draw(im)
    d.rectangle([60, 150, 950, 240], fill=(77, 163, 110))   # 绿色几乎铺满整幅宽（模拟气泡/图片）
    return im


_pw = _frame_with_green(False)
ck("L6 ★越界锚：`pane_left_for` 的结果**不许超过整幅宽的 85%**"
   "（实测踩过：某帧 pl 贴近图宽 ⇒ 下游 crop 抛 right<left ⇒ 链崩）",
   ch.pane_left_for(_wide_green_frame()) <= int(1000 * 0.85),
   "pl=%d" % ch.pane_left_for(_wide_green_frame()))
ck("L7 源码锚：两处钳位都在（绿带 80% 不采信 ＋ 收尾 85% 回退比例兜底）",
   "int(_w * 0.80)" in _src_l and "int(_w2 * 0.85)" in _src_l, "")

print("\n[M] 布局自检：`layout_state` —— 会话列表是否被微信隐藏（2026-10-01）")
# 起因（用户给出规则 + 两张同机"临界点"对照图）：窗口宽度不足以同时放下
# 「最小会话列表 + 最小聊天区」时，微信会**自己把会话列表隐藏**（只剩侧栏+聊天区）。
# ⚠️ 而那两张图**总宽只差 1px（875 vs 876）却状态相反** ⇒ 宽度**不能**用来判断
#    （全量 130 帧里"可见/不可见"的宽度也重叠）⇒ 必须当帧看布局特征。
# ⇒ 判据：`list_visible ⇔ detect_green_right(img) != 0`（会话列表显示时，当前会话行必有绿底）。


def _frame_with_list(visible=True, w=1200, h=800):
    """合成帧：`visible=True` 时画一条**从左侧起铺**的绿底行（模拟会话列表的选中行）。"""
    im = Image.new("RGB", (w, h), (245, 245, 245))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, int(w * 0.38), h], fill=(235, 235, 235))   # 会话列表底（灰）
    d.rectangle([int(w * 0.38), 0, w, h], fill=(250, 250, 250))   # 聊天区（更白）
    if visible:
        d.rectangle([20, 150, int(w * 0.36), 240], fill=(77, 163, 110))   # 绿底行（从左起铺）
    return im


_st_v = ch.layout_state(_frame_with_list(True))
_st_h = ch.layout_state(_frame_with_list(False))
ck("M1 有绿底行（从左侧起铺）⇒ 判「列表可见」", _st_v["list_visible"] is True, str(_st_v["green_right"]))
ck("M2 无绿底行 ⇒ 判「列表不可见」（窄窗隐藏 / 或没打开会话）",
   _st_h["list_visible"] is False, str(_st_h["green_right"]))
# ⛔ 这条原来写的是「列表不可见 ⇒ `can_switch_chat=False`」—— 把"没有绿底行"直接等同于"不能切"。
#   用户报的 bug 正是这一条：**列表还在、只是搜索框退化成放大镜**时也判成不能切 ⇒ 搜索路整条废掉。
#   ⇒ 改**按状态**断言：可见 ⇒ True；**只有 `hidden` 才 False**（`narrowing` 必须 True，见 M3b）。
ck("M3 推论一致：可见 ⇒ True；`hidden` ⇒ False；**「没有绿底行」≠「不能切」**",
   _st_v["can_switch_chat"] is True
   and (_st_h["state"] != "hidden" or _st_h["can_switch_chat"] is False),
   "fixture 状态=%s" % _st_h["state"])
# ⛔ M3b（用户报的 bug）：`narrowing` 的定义是「**列表还在**、只是搜索框退化成放大镜」——
#   放大镜**就是**搜索入口、点得开 ⇒ 这一态必须**允许切会话**。
#   原来 `can_switch_chat = _vis`（只认绿底行）⇒ 把 `narrowing` 也拦死 ⇒ 搜索路整条废掉。
ck("M3b ⭐ `narrowing`（列表还在/框退化成图标）**也允许切会话**（只有 `hidden` 才不许）",
   __import__("_srcmatch").has(
       open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "agent", "chat_header.py"), encoding="utf-8").read(),
       'can_switch_chat": bool(_state in ("full", "narrowing"))'), "")
# ⛔ M3c：**色值佐证**（用户给的版式事实：侧栏/顶栏≈224 · 会话栏≈238 · 聊天区≈250 白）。
#   真界限的**左侧那一带必须是浅灰**；左侧也是白的 ⇒ 咬到了"聊天区里的白空隙"
#   （已记录的真实失败：返回 507、真值≈345）⇒ 必须**继续往后扫**，不许返回。
_CHSP = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "agent", "chat_header.py"), encoding="utf-8").read()
ck("M3c ⭐ 界限判定带**色值佐证**（左侧必须是会话栏的浅灰，否则继续扫）",
   __import__("_srcmatch").has(_CHSP, "def _list_side_is_light_gray(") and __import__("_srcmatch").has(_CHSP, "_list_side_is_light_gray(px, start"), "")
ck("M4 推论一致：无论列表是否可见，`can_send_if_open` 都为 True（发送只依赖聊天区）",
   _st_v["can_send_if_open"] is True and _st_h["can_send_if_open"] is True, "")
# ⭐ 右边界判据 **改（T21）**：**不再用"占窗口比例"**，改用**结构性判据**。
#
# ⛔ 旧判据**误杀了合法帧**，实测证据（全量 112 帧）：
#     · `frame-027`（1121w）与 `frame-028`（1760w）的绿段**是同一段 `90~1087`**
#       ⇒ 占窗口 0.970w / 0.618w ⇒ **旧判据把后者误杀**（列表明明可见、绿底完整）。
#     ★ 真因：**会话列表宽度是固定像素（≈1000px），不随窗口等比缩放**
#       ⇒ 用"占窗口比例"判"绝对固定宽度的列表"，**窗口一宽必然误杀**（方法错，非阈值没调好）。
# ✅ 新判据（结构）：**绿底右端紧邻的须是"列表外的纯色背景"**；
#    若右端之后**还接着明显绿色**（＝绿块铺到图右缘、左端还不靠左）⇒ 才判"聊天区绿元素"。
_im_ok2 = Image.new("RGB", (1000, 400), (245, 245, 245))
_d2b = ImageDraw.Draw(_im_ok2)
_d2b.rectangle([10, 100, 550, 190], fill=(77, 163, 110))      # 0.55w（真绿底可到 0.559）⇒ 应保留
ck("M5a 绿带右端 0.55w（列表被拉宽）⇒ **仍要保留**（别误杀正常帧）",
   ch.detect_green_right(_im_ok2) == 550, "实得 %s" % ch.detect_green_right(_im_ok2))
# ★ 新 M5b：**右端 0.62w ＋ 其后是纯色背景** ⇒ **必须认**（这是 T21 抓到的真实误杀场景：
#   frame-028 就是 1760×1358 下绿段 90~1087、占 0.618w、右侧纯深灰，列表明明可见）
#   旧判据在这里返回 0 ⇒ **本行是 T21 的回归锚**：谁把比例上界加回来，这行立刻变红。
_im_062 = Image.new("RGB", (1000, 400), (245, 245, 245))
_d62 = ImageDraw.Draw(_im_062)
_d62.rectangle([90, 100, 620, 190], fill=(77, 163, 110))      # 0.62w，**右侧是纯背景** ⇒ 应认（＝列表外沿）
ck("M5b★ 绿带右端 0.62w 但**右侧是纯色背景** ⇒ **认**（T21 真实误杀场景，旧判据在此返回 0）",
   ch.detect_green_right(_im_062) == 620, "实得 %s" % ch.detect_green_right(_im_062))
# ★ 新 M5c：**绿块一直铺到图像右缘**（右侧没有纯色背景）⇒ 这才是"绿底延伸到聊天区"的特征 ⇒ 判 0
_im_edge = Image.new("RGB", (1000, 400), (245, 245, 245))
_de = ImageDraw.Draw(_im_edge)
_de.rectangle([600, 100, 999, 190], fill=(77, 163, 110))      # 左端 0.60w（不靠左）+ 铺到右缘 ⇒ 聊天区绿元素
ck("M5c★ 绿带**铺到图像右缘**且左端不靠左 ⇒ 返回 0（拦聊天区绿元素）",
   ch.detect_green_right(_im_edge) == 0, "实得 %s" % ch.detect_green_right(_im_edge))
_im_ok = Image.new("RGB", (1000, 400), (245, 245, 245))
_dw2 = ImageDraw.Draw(_im_ok)
_dw2.rectangle([10, 100, 380, 190], fill=(77, 163, 110))      # 0.38w（典型列表宽度）⇒ 应保留
ck("M6 绿带右边界在典型列表宽度内（0.38w）⇒ 正常返回",
   ch.detect_green_right(_im_ok) == 380, "实得 %s" % ch.detect_green_right(_im_ok))
# ⭐ 左端约束：从更右侧起铺的绿段 ⇒ 判为"聊天区绿元素"（实测 0.740 / 0.518 那两帧）
_im_right = Image.new("RGB", (1000, 400), (245, 245, 245))
_dw3 = ImageDraw.Draw(_im_right)
_dw3.rectangle([600, 100, 900, 190], fill=(77, 163, 110))     # 左端 0.60w ⇒ 不从左侧起铺
ck("M7 绿段左端 0.60w（不从左侧起铺）⇒ 返回 0（拦聊天区绿元素）",
   ch.detect_green_right(_im_right) == 0, "实得 %s" % ch.detect_green_right(_im_right))

# ⭐ M8：**绿带右端之后是"聊天区内容"⇒ 不许认**
#
# ⛔ 上面 M5c 只挡住了"右端之后**还是绿**"。而**聊天区里的内容**（气泡文字/图片/头像）右端之后
#    **不是绿**（是白的、有墨的）⇒ 于是"一小块绿元素 + 右边一片带内容的聊天区"能整条通过。
#    第 23 项 ② 记的那个数（一帧返回 857 ≈ 0.999w）就是这一类。
# ★ 判据（结构量，**不是占比**）：右端之后取一整块区域，看它的**颜色离散度**（标准差）。
#    空白聊天区 ⇒ 同一色 ⇒ 离散度 ≈0；内容区（气泡/头像/文字深浅交错）⇒ 离散度大。
#   ⛔ 这里**绝不能**写成"右端超过 X% 宽就判 0" —— T21 已用真机帧证过那是错法（M5b 是回归锚）。
#   ⛔ 也**不要**改成"逐列跟中位数比"（试过：内容一多，中位数自己就是内容色 ⇒ 永远判干净）。
# ⚠️ 阈值 28 是**真机标定**：合法列表帧实测离散度 ≈0.0（右端之后整片同一色，如 250,250,250）。
_im_dirty = Image.new("RGB", (1000, 400), (250, 250, 250))
_dd = ImageDraw.Draw(_im_dirty)
_dd.rectangle([20, 100, 500, 190], fill=(77, 163, 110))        # 从左侧起铺的绿块（像绿底）
# 右端之后画**真实感**的聊天内容：气泡只占带内一段高度 + 深色文字行（不是整带同高）
for _bx in range(515, 980, 120):
    _dd.rectangle([_bx, 110, _bx + 95, 150], fill=(205, 225, 252))   # 气泡
    _dd.rectangle([_bx + 10, 160, _bx + 80, 175], fill=(80, 80, 80)) # 文字行
ck("M8★ 绿带右侧是**真实感的聊天区内容**（离散度大）⇒ 返回 0"
   "（挡「绿色元素 + 右侧聊天区内容」这一类，第 23 项 ②）",
   ch.detect_green_right(_im_dirty) == 0, "实得 %s" % ch.detect_green_right(_im_dirty))
# 反向控制：真的列表外沿（右侧**干净背景**）**必须**仍然认 —— 防 M8 把正常帧一起砍掉
_im_clean = Image.new("RGB", (1000, 400), (250, 250, 250))
_dc2 = ImageDraw.Draw(_im_clean)
_dc2.rectangle([20, 100, 500, 190], fill=(77, 163, 110))
_dc2.rectangle([502, 100, 503, 190], fill=(210, 210, 210))     # 只一条分隔线（真实列表就是这样的）
ck("M8b（M8 的反向控制）右侧只有一条**分隔线**、其余干净 ⇒ 仍要认（别把正常列表砍了）",
   ch.detect_green_right(_im_clean) == 500, "实得 %s" % ch.detect_green_right(_im_clean))
# ★★ M8c：**真机帧回归** —— 真机"列表外沿合法"的帧**一张都不许被新约束杀掉 / 改动**
#    （这是本判据最硬的一条：合成图会骗人，真机帧不会）
for _nm, _exp in (("20261004-130046_search_entry", 386),
                  ("20261004-130313_search_entry", 0),
                  ("20260927-165014_search_box_row", 0)):
    _p8 = os.path.join(ROOT, "wechatauto_logs", "fail", _nm, "shot.png")
    if not os.path.exists(_p8):
        continue
    _i8 = Image.open(_p8).convert("RGB")
    _gr8 = ch.detect_green_right(_i8)
    ck("M8c★ 真机帧 %s 的绿带判据**不许被新约束改动**（期望 %s）" % (_nm[:24], _exp),
       _gr8 == _exp, "实得 %s" % _gr8)

# ══════════════════════════════════════════════════════════════════════════════
# [M+] 第 189 项接线锚：`layout_state` 必须被 `wechat._click_visible_session` **调用**，
#      而且必须**排在 `find_row_info` 之前**（＝顺序即意图：省掉最贵的那段）。
# ⛔ 为什么用源锚而不是只测行为：这条接线的价值就在"**早**"（早返回省下 find_row_info
#    ＋ 再抓 3 帧）；光测"判不出会往下走"测不到顺序 ⇒ 必须锚源码里的**相对位置**。
# ══════════════════════════════════════════════════════════════════════════════
print("\n[M+] 第 189 项接线锚：`layout_state` 接进 `_click_visible_session`（顺序＝意图）")


def _func_body(src: str, name: str) -> str:
    """取 `def name(...)` 的函数体（含签名行）。取不到返回空串。

    ⚠️ 反向控制：不存在的函数名必须返回空串（否则"锚"会退化成"永远命中"）。
    """
    import re as _re
    m = _re.search(r"^[ \t]*def[ \t]+%s[ \t]*\(" % _re.escape(name), src, _re.M)
    if not m:
        return ""
    lines = src[m.start():].split("\n")
    ind = len(lines[0]) - len(lines[0].lstrip())
    out = [lines[0]]
    for ln in lines[1:]:
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= ind:
            break
        out.append(ln)
    return "\n".join(out)


_src_wx = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
_cvs = _func_body(_src_wx, "_click_visible_session")
ck("M+0（反向控制）`_func_body` 取不存在的函数 ⇒ 返回空串（防锚退化成永远命中）",
   _func_body(_src_wx, "_definitely_not_a_function_xyz") == "", "")
ck("M+1 `_click_visible_session` 里**调用**了 `layout_state`（接线在）",
   "layout_state(" in _cvs and _cvs != "", "")
_i_lay = _cvs.find("layout_state(")
_i_row = _cvs.find("find_row_info(")
ck("M+2 ★**顺序即意图**：`layout_state` 必须排在 `find_row_info` **之前**"
   "（早返回才省得掉 find_row_info ＋ 再抓 3 帧）",
   _i_lay >= 0 and _i_row >= 0 and _i_lay < _i_row,
   "layout@%s row@%s" % (_i_lay, _i_row))
ck("M+3 只做「提前放弃」：判出不可切会话 ⇒ **当场 return False**（不许继续点）",
   'if not _lay.get("can_switch_chat")' in _cvs and "return False" in _cvs, "")
ck("M+4 判不出（异常）⇒ **照旧往下走**（零回归：不许因判据异常就整条失败）",
   "except Exception" in _cvs and "log.debug" in _cvs, "")
ck("M+5 让位文案可解释：说明「列表被藏 ⇒ 让位给搜索路线」",
   "会话列表被微信藏起来" in _cvs and "搜索路线" in _cvs, "")

# ══════════════════════════════════════════════════════════════════════════════
# [M++] 第 23 项 ①：`full`/`narrowing` 的**结构性分界**＝搜索入口的**形态**
#      （`box` 白框 ⇒ 列表还够宽；`icon` 只剩放大镜 ⇒ 已窄到微信退化）
# ⛔ 这条**必须是结构量**，不许退回"宽度阈值"（临界两图只差 1px 却状态相反）。
# ⚠️ 桩：monkeypatch `chat_ocr.find_search_entry` 的返回值，并**还原**（含反向控制）。
# ══════════════════════════════════════════════════════════════════════════════
print("\n[M++] 第 23 项 ①：「`full`/`narrowing` 分界」＝搜索入口**形态**（结构性）")

from agent import chat_ocr as _co2 # noqa: E402

_src_ch = open(os.path.join(ROOT, "agent", "chat_header.py"), encoding="utf-8").read()
_ls = _func_body(_src_ch, "layout_state")
_ls_code = "\n".join(l for l in _ls.split("\n") if not l.strip().startswith("#"))
ck("M++1 `layout_state` 里**读形态**（`variant`）作为分界依据",
   "variant" in _ls_code, "")
ck("M++2 形态判据**延迟导入** `chat_ocr`（防循环导入：`chat_ocr` 顶层 import 本模块）",
   "import chat_ocr as _co" in _ls_code and "延迟导入" in _ls, "")
ck("M++3 `box` ⇒ `narrowing`（框还在 ⇒ 列表够宽）",
   '"box"' in _ls_code and '"narrowing"' in _ls_code, "")
ck("M++4 兜底仍在：形态测不出 ⇒ 退回 `0.20` 比例兜底（＝行为与接线前一致）",
   "0.20" in _ls_code, "")
ck("M++5（反向控制）形态判据**不许越权**：`_vis` 为真时必须 `full`"
   "（有绿底行就轮不到形态说话）",
   "if _vis:\n        _state = \"full\"" in _ls, "看 _ls 里 _vis 分支")

_orig_fse = getattr(_co2, "find_search_entry", None)


def _with_variant(v):
    def _stub(img, left=None, zoom=2):
        return {"variant": v, "x": 100, "y": 30, "why": "桩"}
    return _stub


try:
    # 桩 A：形态=box ⇒ 即使 pane_left 很窄，也判 narrowing（结构量优先）
    _co2.find_search_entry = _with_variant("box")
    _st_box = ch.layout_state(_frame_with_list(False))
    ck("M++6 桩：形态=`box` ⇒ `narrowing`（**不看**宽度比例）",
       _st_box["state"] == "narrowing", str(_st_box["state"]))
    # 桩 B：形态=icon ＋ pane_left **确实很窄** ⇒ hidden
    #    ⚠️ 必须造"面板左沿真的窄"的帧：`_frame_with_list(False)` 的 pane_left/w＝0.38
    #       （≥0.20 ⇒ 按口径该判 narrowing）⇒ 不能拿它来测 hidden。
    _co2.find_search_entry = _with_variant("icon")
    _narrow = Image.new("RGB", (1000, 700), (250, 250, 250))
    _dn = ImageDraw.Draw(_narrow)
    _dn.rectangle([0, 0, 120, 700], fill=(235, 235, 235))       # 侧栏；面板左沿 ≈0.12w < 0.20
    _st_icon = ch.layout_state(_narrow)
    ck("M++7 桩：形态=`icon` 且面板左沿**确实窄**（%.2fw < 0.20）⇒ `hidden`"
       % (ch.pane_left_for(_narrow) / 1000.0),
       _st_icon["state"] == "hidden", str(_st_icon["state"]))
    # 桩 C：测不出（找不到入口）⇒ 兜底
    _co2.find_search_entry = _with_variant("")
    _st_non = ch.layout_state(_frame_with_list(False))
    ck("M++8 桩：形态**测不出** ⇒ 走 0.20w 兜底（与接线前一致）",
       _st_non["state"] in ("narrowing", "hidden"), str(_st_non["state"]))
finally:
    if _orig_fse is not None:
        _co2.find_search_entry = _orig_fse
ck("M++9（桩还原的反向控制）桩已拆掉，`find_search_entry` 是原函数",
   getattr(_co2, "find_search_entry", None) is _orig_fse, "")

# ── N. `layout_ok`：**非标准布局的帧不许用来量坐标**（PLAN 第 28 项）──
# "左侧会话列表 + 右侧聊天区"**不是每帧都成立**（客服提示页 / 聊天区占满 / 调试面板）——
# 那几类帧里 `pane_left` 与各种"按比例取带"的量**都给出看着正常但含义不同的数**
# ⇒ 拿它们标定坐标，标出来的值是错的、且**不报错**。
print("\n── N. `layout_ok`（这一帧能不能用来量坐标）──")
_im900 = Image.new("RGB", (900, 900), (240, 240, 240))
_o_gx, _o_pl, _o_fse = ch.detect_green_right, ch.pane_left_for, _co2.find_search_entry
try:
    _co2.find_search_entry = lambda img, *a, **k: None      # 免得对合成图跑真 OCR
    # ① 标准布局：有绿底行 + 面板左沿合理（0.367w、聊天区 0.633w）
    ch.detect_green_right = lambda img, *a, **k: 330
    ch.pane_left_for = lambda img, *a, **k: 330
    _stN = ch.layout_state(_im900)
    ck("N1 标准布局（有绿底行 + 面板左沿 0.367w）⇒ `layout_ok=True`",
       _stN.get("layout_ok") is True, str(_stN.get("layout_ok")))
    # ② 没有绿底行 ⇒ 列表不可见 / 非标准页 ⇒ False
    ch.detect_green_right = lambda img, *a, **k: 0
    _stN2 = ch.layout_state(_im900)
    ck("N2 **没有绿底行**（列表隐藏 / 客服提示页 / 调试面板）⇒ `layout_ok=False`",
       _stN2.get("layout_ok") is False, str(_stN2.get("layout_ok")))
    # ③ 面板左沿占比过大（聊天区被压没）⇒ False
    ch.detect_green_right = lambda img, *a, **k: 330
    ch.pane_left_for = lambda img, *a, **k: 800
    _stN3 = ch.layout_state(_im900)
    ck("N3 面板左沿 0.889w（聊天区只剩 0.111w）⇒ `layout_ok=False`",
       _stN3.get("layout_ok") is False, str(_stN3.get("layout_ok")))
    # ④ ★反向锚：`layout_ok=False` **只否决"量坐标"，不否决发送**
    ck("N4 ★反向锚：`layout_ok=False` 时 `can_send_if_open` 仍为 True"
       "（会话已打开 ⇒ 照样能发，别把用户挡在门外）",
       _stN3.get("can_send_if_open") is True, str(_stN3.get("can_send_if_open")))
    # ⑤ 原因串里要能看出"为什么不标准"（排障要一眼看到）
    ck("N5 不标准时 `reason` 里写明原因（否则排障只能猜）",
       "不标准" in str(_stN3.get("reason")), str(_stN3.get("reason"))[:60])
finally:
    ch.detect_green_right, ch.pane_left_for = _o_gx, _o_pl
    _co2.find_search_entry = _o_fse
ck("N6（桩还原的反向控制）`detect_green_right` / `pane_left_for` 已还原",
   ch.detect_green_right is _o_gx and ch.pane_left_for is _o_pl, "")

# ── N7. 调用方**必须真的用** （否则布局闸等于没接）──
# 真机取证：主窗拉窄到 868×1343（state=hidden）时，「find_search_entry」会把**导航栏的绿色气泡图标**
# 当成搜索入口（x=55），按记住的间距推出的落点（x=31）落在**灰底空白**上 ⇒ 点下去就是点错。
# ⇒ 「open_chat_by_search」必须先问一句 can_switch_chat，不显示列表就如实拒绝。
_wxsrc2 = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "agent", "wechat.py"), encoding="utf-8").read()
ck("N7 ★真机取证：「open_chat_by_search」的布局闸**只拦 `hidden`**"
   "（`narrowing` 必须放行 —— 放大镜就是搜索入口，拦了整条搜索路就废了）",
   __import__("_srcmatch").has(_wxsrc2, '== "hidden"') and "被藏起来了" in _wxsrc2, "")

print("\n[O] 抓帧失败留证：`dump_scene`（原图 ＋ 判据中间量落到失败现场）")
# 为什么这条判据重要：抓帧这条路上，**三种完全不同的病因表观症状一模一样** ——
#   ① 压根没抓到画面（窗口不可见/被遮挡，PrintWindow 与抓屏都不给帧）
#   ② 抓到了，但文字带取在了空白处（`detect_band_y0` 落点偏了）
#   ③ 抓到了、带子也对，但锚点算错 ⇒ 裁到了会话列表
# 它们的日志都只是"指纹是空的"。⇒ 判据要守的是"**图必须被留下**"，而不是"日志里写了什么"。
import json as _js2
import tempfile as _tf2
_ch_tmp = _tf2.mkdtemp(prefix="chscene-")
_o_root, _o_seen = ch.ROOT, dict(ch._SCENE_SEEN)
try:
    ch.ROOT = _ch_tmp
    ch._SCENE_SEEN.clear()
    _imO = Image.new("RGB", (300, 200), (250, 250, 250))
    _dO = ch.dump_scene(_imO, "o1tag", {"why": "单测"})
    ck("O1 存到失败现场目录并返回目录路径", bool(_dO) and os.path.isdir(_dO), _dO)
    ck("O1b 目录里有**原图** shot.png", os.path.exists(os.path.join(_dO, "shot.png")), "")
    _pjO = os.path.join(_dO, "probe.json")
    _okO = False
    if os.path.exists(_pjO):
        with open(_pjO, encoding="utf-8") as _fO:
            _jO = _js2.load(_fO)
        _okO = (list(_jO.get("size") or []) == [300, 200]) and (_jO.get("extra") or {}).get("why") == "单测"
    ck("O1c probe.json 里带**尺寸**与判据中间量（不看日志也能判断病因）", _okO, "")
    _dO2 = ch.dump_scene(None, "o2tag", {"why": "没抓到画面"})
    ck("O2 `img=None` 也能留证（只写 json，**绝不抛**）",
       bool(_dO2) and os.path.exists(os.path.join(_dO2, "probe.json"))
       and not os.path.exists(os.path.join(_dO2, "shot.png")), _dO2)
    ch._SCENE_SEEN.clear()
    _cnt = sum(1 for _ in range(6) if ch.dump_scene(None, "o3tag"))
    ck("O3 同一 tag 在**本进程内**最多存 %d 份（抓帧失败会被高频调用，不加闸会一秒建一个目录）"
       % ch.SCENE_MAX_PER_PROC, _cnt == ch.SCENE_MAX_PER_PROC, "实得 %d" % _cnt)
    _baseO = os.path.join(_ch_tmp, "wechatauto_logs", "fail")
    for _iO in range(7):
        os.makedirs(os.path.join(_baseO, "0000000%d-000000_o4tag" % _iO), exist_ok=True)
    ch._SCENE_SEEN.clear()
    ch.dump_scene(None, "o4tag")
    _leftO = [n for n in os.listdir(_baseO) if n.endswith("_o4tag")]
    ck("O4 磁盘上同一 tag 只留最近 keep=%d 份（失败现场比日志更占地方）" % ch.SCENE_KEEP,
       len(_leftO) <= ch.SCENE_KEEP, "实得 %d 份" % len(_leftO))
finally:
    ch.ROOT = _o_root
    ch._SCENE_SEEN.clear()
    ch._SCENE_SEEN.update(_o_seen)
ck("O5（桩还原的反向控制）`ch.ROOT` 与进程内限量表都已还原",
   ch.ROOT == _o_root and ch._SCENE_SEEN == _o_seen, "")
# ── O6. 静态锚：两条真实失败路径**必须真的调它**（否则留证只是摆设）──
_HERE3 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_HERE3, "agent", "wechat.py"), encoding="utf-8") as _fA:
    _wsrc3 = _fA.read()
with open(os.path.join(_HERE3, "agent", "chat_header.py"), encoding="utf-8") as _fB:
    _chsrc3 = _fB.read()
ck("O6★ `_learn_chat_header` 在入库闸拦下时**存了原图**（不是只写一行日志）",
   ("header_blank" in _wsrc3) and ("header_no_capture" in _wsrc3) and ("dump_scene" in _wsrc3), "")
ck("O6b★ `grab_render` 的三条放弃路径都留证（被遮挡 / 拿不到 PID / 抛异常）",
   all(_t in _chsrc3 for _t in ("render_occluded", "render_no_pid", "render_error")), "")

print("\n[P] 渲染子窗的尺寸必须与渲染区一致（跨机错位那个洞）")
# 现场：跨机实测里子窗是 1095×900、渲染区是 1210×900，而代码里的注释写着
#   "渲染子窗的图就是渲染区，零换算"（那句是在**另一台机器**上量出来的 1139×890）
#   ⇒ 图被当成渲染区用 ⇒ 所有按渲染区算的坐标整体错位，而**图本身有内容**，质量闸拦不住。
ck("P1 尺寸一致 ⇒ 认（渲染区 1210×900 vs 子窗 1210×900）",
   ch.child_matches_render((1210, 900), (42, 88, 1252, 988)) is True, "")
ck("P2★ 尺寸不符 ⇒ **不认**（跨机实测那一组：子窗 1095×900 vs 渲染区 1210×900）",
   ch.child_matches_render((1095, 900), (42, 88, 1252, 988)) is False, "")
ck("P3 容差是**绝对像素 ±%d**（不是比例）—— 1202 差 8 认、1193 差 17 不认"
   % ch.CHILD_SIZE_TOL,
   ch.child_matches_render((1202, 900), (42, 88, 1252, 988)) is True
   and ch.child_matches_render((1193, 900), (42, 88, 1252, 988)) is False, "")
ck("P4 渲染区未知/非法 ⇒ 认（无从校验，维持旧行为，别把老路径堵死）",
   ch.child_matches_render((100, 100), None) is True
   and ch.child_matches_render((100, 100), (0, 0, 0, 0)) is True, "")

# ── P5/P6 行为测：grab_render **真的**按这道闸改道（不是只写了个函数没人用）──
from agent import input_backend as _ibm  # noqa: E402
import tempfile as _tf3


class _GStub: # 假 GUI：只提供 grab_render 要的两个字段
    main_hwnd = 4242
    render_rect = (42, 88, 1252, 988)


def _noise_img(w, h):
    """有内容（std 够大）的合成帧：**随机噪点**。

    ⛔ 别用"整行条纹"当夹具：那种图**每一行都是平的**，会被 `_blocky()` 判成"两个方向里有一个整片是平的"
      ⇒ `_frame_ok` 直接拒收 ⇒ 判据变成空转（实测踩过一次）。
    """
    return Image.frombytes("L", (w, h), os.urandom(int(w) * int(h))).convert("RGB")


_FREAL, _CREAL = ch._print_window, ch._crop_render
_FRREAL = getattr(_ibm, "find_render_child", None)
_ROOT_REAL = ch.ROOT
_tmpP = _tf3.mkdtemp(prefix="chgrab-")
try:
    ch.ROOT = _tmpP
    _ibm.find_render_child = lambda h: 999
    ch._crop_render = lambda img, main, render: _noise_img(1210, 900)
    # ① 子窗尺寸**不符** ⇒ 不许返回它，要改走主窗裁剪那条路
    ch._print_window = lambda hwnd, *a, **k: _noise_img(1095, 900)
    ch.LAST_GRAB.clear()
    _got = ch.grab_render(gui=_GStub(), tries=1)
    ck("P5★ 子窗尺寸不符 ⇒ **不返回子窗图**，改走主窗裁剪（返回 1210×900）",
       _got is not None and _got.size == (1210, 900) and ch.LAST_GRAB.get("win") == "main_crop",
       "%s · LAST_GRAB.win=%s" % (getattr(_got, "size", None), ch.LAST_GRAB.get("win")))
    ck("P5b 判决过程留痕（记下子窗实际尺寸，供回传定位）",
       (ch.LAST_GRAB.get("child_size") == [1095, 900])
       or ("render_child_mismatch" in str(ch.LAST_GRAB)), str(ch.LAST_GRAB)[:120])
    # ③ 逻辑尺寸抓出来不符、**物理尺寸**抓出来对上 ⇒ 用它（`_print_window` 那个已知缺陷的自愈）
    ch._print_window = (lambda hwnd, *a, **k:
                        _noise_img(1210, 900) if k.get("physical") else _noise_img(1095, 900))
    ch._crop_render = lambda img, main, render: _noise_img(7, 7) # 哨兵：走裁剪就露馅
    ch.LAST_GRAB.clear()
    _got3 = ch.grab_render(gui=_GStub(), tries=1)
    ck("P6b★ 逻辑尺寸不符、物理尺寸对上 ⇒ 用物理那份（DPI 非 100% 时位图尺寸口径那个坑的自愈）",
       _got3 is not None and _got3.size == (1210, 900)
       and ch.LAST_GRAB.get("win") == "child_physical",
       "%s · win=%s" % (getattr(_got3, "size", None), ch.LAST_GRAB.get("win")))
    # ② 子窗尺寸**相符** ⇒ 直接用它，且**不许再裁一刀**（裁了就是整体错位）
    ch._print_window = lambda hwnd, *a, **k: _noise_img(1210, 900)
    ch._crop_render = lambda img, main, render: _noise_img(7, 7) # 哨兵：一旦被调用就看得出来
    ch.LAST_GRAB.clear()
    _got2 = ch.grab_render(gui=_GStub(), tries=1)
    ck("P6★ 子窗尺寸相符 ⇒ 直接用子窗图、**零换算**（哨兵证明没走裁剪）",
       _got2 is not None and _got2.size == (1210, 900) and ch.LAST_GRAB.get("win") == "child",
       "%s · LAST_GRAB.win=%s" % (getattr(_got2, "size", None), ch.LAST_GRAB.get("win")))
finally:
    ch._print_window, ch._crop_render, ch.ROOT = _FREAL, _CREAL, _ROOT_REAL
    if _FRREAL is not None:
        _ibm.find_render_child = _FRREAL
ck("P7（桩还原的反向控制）`_print_window`/`_crop_render`/`find_render_child`/`ROOT` 都已还原",
   ch._print_window is _FREAL and ch._crop_render is _CREAL and ch.ROOT == _ROOT_REAL, "")
_chsrcP = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "agent", "chat_header.py"), encoding="utf-8").read()
ck("P8 静态锚：`grab_render` 在返回子窗图**之前**调了这道闸",
   _sm.has(_chsrcP, "child_matches_render(img.size, render)"), "")
ck("P9 静态锚：尺寸不符时**先试物理尺寸再抓一次**（`physical=True` 的自愈路径）",
   _sm.has(_chsrcP, "_print_window(hwnd, physical=True)"), "")

print("\n[结论] %d 通过 / %d 失败" % (len(OK), len(BAD)))
if BAD:
    print("失败项：%s" % BAD)
sys.exit(1 if BAD else 0)
