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
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import chat_header as ch  # noqa: E402
from PIL import Image, ImageDraw, ImageFont   # noqa: E402

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
fb = ch.fingerprint(fake_window("群deepseek"))
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
    img = fake_window("群deepseek")
    ch.remember("filehelper", ch.fingerprint(img), path=p)
    ck("S5 参照＝当前 ⇒ 匹配", ch.match(ch.reference("filehelper", p), ch.fingerprint(img)) is True)
    ch.remember("filehelper", fa, path=p)   # 参照换成"文件传输助手"，当前图是"群deepseek"
    ck("S6 参照≠当前 ⇒ 不匹配（这就是防发错会话的那道闸）",
       ch.match(ch.reference("filehelper", p), ch.fingerprint(img)) is False)
    # —— 分尺寸记忆（2026-09-13 实测：指纹**不可跨窗口尺寸复用**，微信会按尺寸重排表头）——
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
# ⛔ V-R7-14：这两族以前**零覆盖**——变异 `chat_header.py:264`（摘掉形状闸）与 `:305`
#   （`if is_blank(fp):` → `if False:`）之后，本判据仍 19 通过 / 0 失败。
#   ① 两个**不同的短群名**在幅度指标上能拿到 0.98（真机实测 0.948 ≥ 0.90）⇒ 只看相似度会把
#      它们判成「同一个会话」（假阳性）；形状闸（`pattern_score >= DEFAULT_COSINE`）必须拦下。
#   ② `remember()` 的 `is_blank` 闸：空白帧学进参照库会让该尺寸档**永远判不匹配**
#      ⇒ 打好的回复被整条丢掉（用户报「机器人有时不回话」的真因链）。
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
    _cap0 = ch.capture_image                     # 用合成帧当"当前画面"，把 check() 的判分分支钉住
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
    # —— 隔离测试（V-R7-14 的"该补什么"要的是能变红）：`is_blank` 与 `degenerate_reason` 是**两道**
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
    # —— V-R8-2 的那道缝：真造帧「标题带里只有两条竖线、没有字」（审计的 S05/S12/S19/S20 那族）——
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
    ck("P8 反例锚（V-R8-2）：S05 那类帧**必须进不去参照库**（进库＝该尺寸档此后一直判 mismatch＝漏发）",
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
    ck("P9 后果锚：库里若真有这类参照 ⇒ 同尺寸的正常帧判 **mismatch**（这就是 V-R8-2 的「长期漏发」）",
       _r9.get("status") == "mismatch" and float(_r9.get("sim") or 0) < ch.DEFAULT_THRESHOLD,
       "status=%s · %s" % (_r9.get("status"), _r9.get("note")))

print("[T] 自绘标题条上方的自适应文字带（第九轮 V-R9-3：固定 y0 压在标题条上 ⇒ 指纹退化 ⇒ 该尺寸档永远学不到参照）")


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
import json as _json                                                            # noqa: E402
with open(_tp, "w", encoding="utf-8") as _f:                                    # 旧版参照（旧带子）
    _json.dump({"filehelper": {"sizes": {"1076x1046": {"fp": _new_fp2, "band": 1}}}}, _f)
ck("T6 **带子算法变了 ⇒ 旧参照当「没有」**（no_ref 不拦发送，而不是 mismatch 去拦）",
   ch.reference("filehelper", path=_tp, size="1076x1046", strict=True) == [])

# ══════════════════════════════════════════════════════════════════════════════
# W/X/Y/Z 段：第十轮（V-R10-1 / V-R10-5 / V-R10-6 / V-R10-7）
#   全部**离线合成帧** —— 不起 GUI、不碰真实微信/鼠标（真机读数写在各条说明里，见审计第十轮）。
# ══════════════════════════════════════════════════════════════════════════════


def _font28():
    try:
        return ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 28)
    except Exception:
        return ImageFont.load_default()


def _old_pane_scan(img, lo_rel=0.15, hi_rel=0.60, need=24):
    """**老口径**（V-R10-1 之前）的左沿扫描：下界 ＝ `int(w × lo_rel)`（纯相对、随宽度线性放大）。

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
    """老口径（V-R9-6 版 `_frame_ok`）唯一的"有没有结构"读数：逐行 std 的最大值。"""
    small = img.convert("L").resize((64, 64))
    px = small.load()
    best = 0.0
    for y in range(64):
        row = [px[x, y] for x in range(64)]
        m = sum(row) / 64.0
        best = max(best, (sum((a - m) ** 2 for a in row) / 64.0) ** 0.5)
    return best


def fake_wide(text="文件传输助手", size=(2538, 1589), pl=331, white_to=380):
    """**真机最大化那一帧**的模型（本机 2560×1600 工作区 ⇒ 渲染 2538×1589，审计第十轮实测）：
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
    return len([i for i, x in enumerate(v) if x != 0])      # 有墨（非零）的列数，同 `remember()` 的口径


print("[W] 窗口几何：**最大化/宽窗**也要测得出左沿并出指纹（第十轮 V-R10-1·P1）")
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
#    现场＝第六轮 V-R6-11：聊天区左列被消息气泡占满 ⇒ 老口径返回 0，而"竖栏右沿 + 列表固定宽"
#    的结构锚仍认得出左沿。这一帧的真左沿 360；名字写在 660（只落在结构锚的带子里）。
_bub = Image.new("RGB", (1139, 890), (245, 245, 245))
_bd = ImageDraw.Draw(_bub)
_bd.rectangle((0, 0, 60, 890), fill=(40, 40, 40))                   # 深色竖导航栏
_bd.rectangle((60, 0, 360, 890), fill=(245, 245, 245))              # 会话列表（固定 300 宽）
_bd.rectangle((360, 0, 911, 890), fill=(230, 230, 230))             # 气泡带（非白 ⇒ 老口径扫不出白列）
_bd.rectangle((911, 0, 1139, 890), fill=(255, 255, 255))
_bd.text((660, 46), "文件传输助手", fill=(20, 20, 20), font=_font28())
ck("W1f 老口径在这帧上就是 0（V-R6-11 现场），结构锚给 360 ⇒ `pane_left_for` 必须取到 360",
   ch.detect_pane_left(_bub) == 0 and ch.pane_left_for(_bub) == 360,
   "老口径=%d · 结构锚=%d · pane_left_for=%d"
   % (ch.detect_pane_left(_bub), ch.detect_pane_left_alt(_bub), ch.pane_left_for(_bub)))
_bfp = ch.fingerprint(_bub)
ck("W1g **`fingerprint` 必须走 `pane_left_for`**（名字只落在结构锚那条带子里 ⇒ 非空；"
   "退回老口径那一层就掉进 `0.26×宽` 兜底 ⇒ 空指纹）",
   bool(_bfp) and ch.fingerprint(_bub, pane_left_rel=ch.PANE_LEFT_REL) == [],
   "生产路径 %d 维 · 反例锚（0.26w 兜底）%s"
   % (len(_bfp), ch.fingerprint(_bub, pane_left_rel=ch.PANE_LEFT_REL) or "[]"))

print("[X] 自适应带子的**钳位边界**（第十轮 V-R10-5·P2：`max_y0=72`/`max_scan=96` 卡死 ⇒ V-R9-3 原症状复现）")
_xa = _bar_frame(bar=(38, 50), text_y=54)
_xb = _bar_frame(bar=(96, 103), text_y=112, size=(1076, 1046))      # 条上边 ≥96：超出老 max_scan
_xc = _bar_frame(bar=(70, 103), text_y=112, size=(1076, 890))       # 条底边 103：被老 max_y0=72 钳住


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
   "（逐字复现 V-R9-3/V-R10-5 的症状：该尺寸档永远学不到参照）",
   _xcy > int(ch.BAND_PX[1]) and not ch.degenerate_reason(ch.fingerprint(_xc))
   and ch.degenerate_reason(_fp_old_band(_xc, _xco)) != "",
   "新 y0=%d（有墨列=%d）· 老 y0=%d ⇒ 老带子 %r"
   % (_xcy, _deg_short(ch.fingerprint(_xc)), _xco, ch.degenerate_reason(_fp_old_band(_xc, _xco))))

print("[Y] 帧质量与遮挡的缝（第十轮 V-R10-6·P3）")
_half_lr = Image.new("RGB", (400, 300), (255, 255, 255))
_hd2 = ImageDraw.Draw(_half_lr)
_hd2.rectangle((0, 0, 200, 300), fill=(0, 0, 0))          # **左半深、右半浅**（每行都不平）
ck("Y1 左右两大色块拼起来的帧 ⇒ **不是好帧**（每列都是平的 ⇒ 一个方向没有结构）",
   ch._frame_ok(_half_lr) is False)
ck("Y1b 反例锚：老口径（只看**逐行**结构）在这帧上读数 = %.1f（≥3 ⇒ 它会判「好帧」，这就是那道缝）"
   % _best_row_std(_half_lr),
   _best_row_std(_half_lr) >= 3.0, "best_row=%.1f" % _best_row_std(_half_lr))
ck("Y1c 兜底帧准入也必须拒收它（`_mono` 认同一族，否则它会以「兜底帧」身份回到链上）",
   ch._mono(_half_lr) is True)
_goodY = Image.new("RGB", (400, 300), (250, 250, 250))    # 像"聊天区"：白底 + 几块文字/头像
_gdY = ImageDraw.Draw(_goodY)
for _iy in range(6):
    _gdY.rectangle((20, 20 + _iy * 40, 200 + _iy * 20, 44 + _iy * 40), fill=(40, 40, 40))
ck("Y1d 阳性对照：真画面（文字块/头像那样的结构）仍是好帧、也不算 mono",
   ch._frame_ok(_goodY) is True and ch._mono(_goodY) is False)
ck("Y2 **退化渲染矩形**（<8px）⇒ 按**未知＝遮挡**处理（老写法 `return False` 会退回抓屏）",
   ch._region_occluded((0, 0, 4, 4), 1234) is True)
ck("Y2b 正常矩形照旧走采样（不因为上面那条变成「永远判遮挡」）",
   ch._occlusion_verdict([(100, True)] * 4, 100) is False)

print("[Z] 单字会话名的**既定代价**（第十轮 V-R10-7·P3：学不到参照；钉住「不拦发送」这一侧）")
_zE = ch.fingerprint(_name_frame("E"))
_zI = ch.fingerprint(_name_frame("I"))
print("    INFO 合成帧读数：E ⇒ 有墨（≥110）列 %s、非零列 %d、退化=%r；I ⇒ 有墨列 %s、退化=%r"
      % ([i for i, v in enumerate(_zE) if v >= 110], _deg_short(_zE), ch.degenerate_reason(_zE) or "",
         [i for i, v in enumerate(_zI) if v >= 110], ch.degenerate_reason(_zI) or ""))
print("    INFO 真机（审计第十轮）：「E」在 9 档尺寸下都是 `有墨的列只有 1 个（不是一行字）` ⇒ 学不到参照")
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

print("[F] 帧质量闸（第九轮 V-R9-6：半黑半白 / 纯黑帧以前会被当好帧）")
_blk = Image.new("RGB", (400, 300), (0, 0, 0))
_half = Image.new("RGB", (400, 300), (255, 255, 255))
_hd = ImageDraw.Draw(_half)
_hd.rectangle((0, 150, 400, 300), fill=(0, 0, 0))                  # 半黑半白（mean/std 都像好帧）
ck("F1 纯黑帧 ⇒ 不是好帧", ch._frame_ok(_blk) is False)
ck("F2 **半黑半白色块帧 ⇒ 不是好帧**（每行都是平的：真画面总有有结构的那几行）",
   ch._frame_ok(_half) is False)
_good = Image.new("RGB", (400, 300), (250, 250, 250))              # 像"聊天区"：白底 + 几块文字/头像
_gd = ImageDraw.Draw(_good)
for _i in range(6):
    _gd.rectangle((20, 20 + _i * 40, 200 + _i * 20, 44 + _i * 40), fill=(40, 40, 40))
ck("F3 阳性对照：有内容（文字块/头像那样的结构）的画面 ⇒ 仍是好帧", ch._frame_ok(_good) is True,
   "mean/std/极差 都在阈上）")
ck("F4 纯黑帧的指纹**必须是空**（旧口径会给 [255]*64 这种看着有依据的假指纹）",
   ch.fingerprint(_blk, band_px=ch.BAND_PX) == [],
   "指纹 %s" % (ch.fingerprint(_blk, band_px=ch.BAND_PX) or "[]"))
_old_mean, _old_std = 140.0, 126.9        # 侦察线实测的"半黑半白"帧读数
ck("F5 反例锚：老口径（只要 mean>25 且 std>12）**放行**这张帧",
   (_old_mean > 25) and (_old_std > 12), "mean=%s std=%s" % (_old_mean, _old_std))

print("[L] 实机（抓不到不算失败）")
try:
    fp1 = ch.capture()
except Exception as _e_l:               # ⚠️ 微信没在运行时 `capture()` 是**抛异常**，不是"抓不到"
    print("  INFO 微信此刻没在运行 / 主窗不可见（%s）—— 这一段只是 INFO，不算失败" % str(_e_l)[:60])
    fp1 = []
if fp1:
    time.sleep(1.5)
    fp2 = ch.capture()
    s = ch.similarity(fp1, fp2) if fp2 else 0.0
    print("  INFO 当前会话头指纹 %d 维；两次抓取相似度 %.3f（稳定性）" % (len(fp1), s))
    other = ch.fingerprint(fake_window("群deepseek"))
    print("  INFO 与合成「群deepseek」相似度 %.3f（应明显低于阈值 %.2f）"
          % (ch.similarity(fp1, other), ch.DEFAULT_THRESHOLD))
else:
    print("  INFO 这次没抓到（微信窗口不可见/未开）——不算失败")

print("\n[结论] %d 通过 / %d 失败" % (len(OK), len(BAD)))
if BAD:
    print("失败项：%s" % BAD)
sys.exit(1 if BAD else 0)
