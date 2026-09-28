# -*- coding: utf-8 -*-
"""OCR 精度改造判据（候选词表纠错 + 按字号选档 + 多档择优）。

守四件事：
  ① **不许猜**：纠错只在"唯一接近且距离在上限内"时才动；空候选集/歧义/离得远 ⇒ 一律原样；
  ② **按字号选档**：`auto_zoom` 让**放大后**字高落进目标区间（本机实测扫描出来的 45~90px）；
  ③ **多档择优**（行为锚）：注入假引擎，让不同档给出不同读数 ⇒ **必须选像候选的那一档**，
     而不是"第一档非空就收"（后者会把错读当结果收下）；
  ④ **接线**：`current_chat_name` / `header_text` 真把候选集传下去了（不是写了没人用）。

用法：`py -3 scripts/ocr_precision_selftest.py`
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

from PIL import Image, ImageDraw # noqa: E402

import _srcmatch as _sm # noqa: E402  空白容忍的源码包含（脆断言棘轮只许降）
from agent import chat_ocr as O # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


def src(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


#: 夹具会话名：正常中文名（⛔ 不许用 wxid_ 前缀——出包 PII 闸门按真形态拦，实测被拦过）
CANDS = ["演示", "测试(2)", "家人群", "工作汇报", "老张"]

print("── A. 候选词表纠错：三条护栏（不许猜）──")
_b, _s, _w = O.best_candidate("演示", CANDS)
ok("A1 完全相等 ⇒ score=1.0（纠错最常命中的形态）", _b == "演示" and _s >= 1.0, "%s/%.2f" % (_b, _s))
_b, _s, _w = O.best_candidate("演示祥", CANDS)      # 小字单字误读的典型
ok("A2 单字误读 ⇒ 纠到最近候选（0.7<score<1.0）", _b == "演示" and 0.7 <= _s < 1.0, "%s/%.2f" % (_b, _s))
_b, _s, _w = O.best_candidate("演示（3）", CANDS)   # 全角括号/成员数装饰
ok("A3 全角括号 + 成员数装饰 ⇒ 归一化后相等", _b == "演示" and _s >= 1.0, "%s/%.2f" % (_b, _s))
_b, _s, _w = O.best_candidate("这是一句完全无关的话", CANDS)
ok("A4 ★ 离得远 ⇒ **不纠**（原样交给调用方）", _b == "" and "不纠" in _w, _w[:60])
ok("A5 ★ 空候选集 ⇒ 不纠（行为与历史一致）", O.best_candidate("演示祥", [])[0] == "")
ok("A6 ★ 歧义（两个候选一样近）⇒ 不纠（宁可不纠也不猜）",
   O.best_candidate("演示祥", ["演示", "演示祥x"])[0] == "",
   O.best_candidate("演示祥", ["演示", "演示祥x"])[2][:60])
ok("A7 距离上限：长名容忍 2 个字、短名只容忍 1 个字",
   O.best_candidate("工作汇拫", CANDS)[0] == "工作汇报"
   and O.best_candidate("演示xyz", CANDS)[0] == "")
ok("A8 `snap_name` **绝不返回空**（认不出原样返回）", O.snap_name("无关的话", CANDS) == "无关的话")
ok("A9 `snap_name` 空输入不炸", O.snap_name("", CANDS) == "" and O.snap_name("x", None) == "x")

print("\n── B. 按字号选档（auto_zoom / text_height_hint）──")
_blank = Image.new("RGB", (120, 40), (250, 250, 250))
ok("B1 空白图 ⇒ 量不出字高（0）", O.text_height_hint(_blank) == 0 and O.auto_zoom(_blank) == 0)
_ok_z = True
_detail = []
for px in (12, 15, 18, 22):
    im = Image.new("RGB", (px * 8 + 24, px + 16), (247, 247, 247))
    ImageDraw.Draw(im).text((12, 8), "演示群聊", fill=(38, 38, 38))
    z = O.auto_zoom(im)
    h = O.text_height_hint(im)
    after = (h or 0) * max(1, z)
    _detail.append("%dpx→x%d(%dpx)" % (px, z, after))
    if z and h and not (O.ZOOM_TARGET_LO * 0.6 <= after <= O.ZOOM_TARGET_HI * 1.6):
        _ok_z = False
ok("B2 放大后字高落进目标区间（本机扫描出的 %d~%d px，容 ±40%%）"
   % (O.ZOOM_TARGET_LO, O.ZOOM_TARGET_HI), _ok_z, " ".join(_detail))
_big = Image.new("RGB", (400, 140), (247, 247, 247))
ImageDraw.Draw(_big).text((12, 12), "大字不必放大", fill=(38, 38, 38))
ok("B3 字已经够大 ⇒ 不放大（返回 1 或 0）", O.auto_zoom(_big) in (0, 1), str(O.auto_zoom(_big)))
ok("B4 目标区间就是本机实测那档（防有人改回文献里的 20~30）",
   O.ZOOM_TARGET_LO >= 40 and O.ZOOM_TARGET_HI <= 120,
   "%d~%d" % (O.ZOOM_TARGET_LO, O.ZOOM_TARGET_HI))

print("\n── C. 多档择优（行为锚：假引擎给出受控读数）──")
_real_dual = O.recognize_dual
try:
    # zoom=2 给"像但不对"的读数（离候选远），放大到 3 档以上给正确名 ⇒ 择优必须选那一档
    # ⛔ 档位要按**放大后的宽度 / 原宽**算（`preprocess_ink` 是把 crop 放大的；用固定阈值会错判）
    _base_w = 240

    def _fake_dual(c, timeout=None):   # noqa: ANN001
        z = max(1, int(round(c.size[0] / float(_base_w))))
        if z <= 2:
            return [("演示祥祥祥", 0, 0, 10, 10)]
        return [("演示", 0, 0, 10, 10)]
    O.recognize_dual = _fake_dual
    got, z = O._read_band_crop(Image.new("RGB", (_base_w, 60), (250, 250, 250)), 2, cands=["演示", "家人群"])
    ok("C1 ★ 有候选集 ⇒ 不「读到就停」，选了更像候选的那一档", got == "演示" and z >= 3, "%r z=%d" % (got, z))
    got2, z2 = O._read_band_crop(Image.new("RGB", (_base_w, 60), (250, 250, 250)), 2)
    ok("C2 无候选集 ⇒ 保持老行为（第一档非空即返回）", got2 == "演示祥祥祥" and z2 == 2, "%r z=%d" % (got2, z2))

    def _fake_none(c, timeout=None):   # noqa: ANN001
        return []
    O.recognize_dual = _fake_none
    got3, z3 = O._read_band_crop(Image.new("RGB", (240, 60), (250, 250, 250)), 2, cands=["演示"])
    ok("C3 全档读空 ⇒ 返回空（不伪造）", got3 == "" and z3 == 0)
finally:
    O.recognize_dual = _real_dual

print("\n── D. 接线（源码级：候选集真传下去了）──")
_wx = src("agent/wechat.py")
_ocr = src("agent/chat_ocr.py")
ok("D1 `current_chat_name` 把已知会话名当候选集传下去",
   _sm.has(_wx, "cands=_cands") and _sm.has(_wx, "_cands = self._known_chat_names()"))
ok("D2 `current_chat_name` 有纠错薄壳（先实现体、后纠错）+ 出错不影响主判据",
   _sm.has(_ocr, "def _current_chat_name_impl(") and _sm.has(_ocr, "候选词表纠错跳过"))
ok("D3 `header_text` 收 cands 并在四处调用里都传下去",
   _sm.has(_ocr, "def header_text(img=None, gui=None, zoom: int = 2, confirm_frames: int = 2, cands=None)")
   and _ocr.count("_header_read(") >= 5 and _ocr.count("cands=cands") >= 4)
ok("D4 `_header_read` 委托给 `_read_band_crop`（基准量的就是它）",
   _sm.has(_ocr, "return _read_band_crop(crop, zoom, cands)"))

print("\n── E. 反向锚：不许把「认不准」变成「认得出」──")
import _srcslice as _ss # noqa: E402  AST 定位函数体（判据不许拿 `def` 行当文本边界）
ok("E1 纠错只改文本、**不参与授权**（授权判据里不许出现 best_candidate）",
   "best_candidate" not in _ss.func_src(_ocr, "matches_fuzzy")
   and "best_candidate" not in _ss.func_src(_ocr, "matches_strict"))
ok("E2 没有候选集时的函数行为与历史一致（best_candidate 直接返回空）",
   O.best_candidate("演示祥", None)[0] == "" and O.best_candidate("演示祥", [])[0] == "")
ok("E3 基准脚本在（能量化每次改动：`scripts/ocr_bench.py`）",
   os.path.exists(os.path.join(ROOT, "scripts", "ocr_bench.py")))

print("")
print("OCR 精度改造判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
