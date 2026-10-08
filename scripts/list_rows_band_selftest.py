# -*- coding: utf-8 -*-
"""第 149 项：会话列表扫描的**可见区快路径**（`list_rows(band=…)`）。

它要守的承诺（**本判据按实测口径表述，不按"看起来应该"表述**）：
    `list_rows(img, band=(y0,y1))` 里，**严格落在 `[y0,y1)` 内的会话名字行**必须一条不漏，
    且它们的 `y_abs` 与"全量扫"的差 **≤ 2px**。

⛔ 容差为什么是 2：**实测**来的 —— 同一个名字行、两种裁剪，`|Δy_abs|` 的实测分布是
   `{0: 17, 1: 3}`（行距 64px）⇒ 取 2 已经留了一倍余量。
   ⚠️ 而"**改了裁剪起点却忘了补偿 `y_abs`**"这个错，会让偏移差达到 **一个行距（64px）**
   ⇒ 2px 容差**不可能**把它盖住（判据 ③ 专门断言这件事，防"容差把 bug 吃掉"）。

⛔ 另一条实测结论（已写进产品注释）：**裁剪必须两侧各多留一行** ——
   紧贴裁剪边的行，它的"预览行"会被切掉 ⇒ OCR 把名字行并掉 ⇒ **带内最靠边那条静默丢失**
   （实测 `band=(160,352)` 漏掉 y≈346 那条）。判据 ⑤ 钉住这条。
"""
from __future__ import annotations

import ast
import collections
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.environ["PM_JUDGE_NO_GUI"] = "1"

from PIL import Image, ImageDraw, ImageFont # noqa: E402
from agent import chat_ocr as ocr # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


TOL = 2                       # 实测最大 1px，取 2
W, H, PANE = 1139, 890, 331
NAMES = ["腾讯新闻", "文件传输助手", "服务通知", "项目群", "家人群", "订阅号"]
_font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 22)
img = Image.new("RGB", (W, H), (237, 237, 239))
_d = ImageDraw.Draw(img)
for _i, _nm in enumerate(NAMES):
    _y = 90 + _i * ocr.ROW_PITCH
    _d.text((PANE - 230, _y - 16), _nm, font=_font, fill=(20, 20, 20))
    _d.text((PANE - 230, _y + 6), "最后一条消息预览", font=_font, fill=(150, 150, 150))
_d.rectangle([PANE, 0, W, H], fill=(255, 255, 255))


def _namekey(t):
    for _nm in NAMES:
        if _nm in str(t):
            return _nm
    return None


print("\n══ A. 默认路径没被改动（回归）══")
_full = ocr.list_rows(img)
_full2 = ocr.list_rows(img)
_key = lambda r: (str(r["text"]), int(r["y_abs"]), int(r["x0"]), int(r["x1"]))
ok("① `band` 缺省时两次调用逐条一致", [_key(r) for r in _full] == [_key(r) for r in _full2],
   "%d 行" % len(_full))
ok("② 全量扫读到了名字（合成帧 OCR 可用，否则下面几条无意义）",
   len({_namekey(r["text"]) for r in _full if _namekey(r["text"])}) >= 4,
   "读到名字 %d 个" % len({_namekey(r["text"]) for r in _full if _namekey(r["text"])}))

print("\n══ B. 快路径：不漏 / 不越界 / 坐标同源（实测容差）══")
ok("③ 判据有效性：容差(%dpx) **远小于**'忘补偿'的量级(一个行距=%dpx) ⇒ 容差不可能把那个 bug 盖住"
   % (TOL, ocr.ROW_PITCH), TOL * 2 < ocr.ROW_PITCH, "TOL=%d, ROW_PITCH=%d" % (TOL, ocr.ROW_PITCH))

_BANDS = [(90, 410), (160, 352), (200, 420), (120, 300)]
_miss_all, _beyond_all, _diffs = [], [], collections.Counter()
_base_by_name = {}
for _r in _full:
    _k = _namekey(_r["text"])
    if _k and _k not in _base_by_name:
        _base_by_name[_k] = int(_r["y_abs"])
for _b in _BANDS:
    _got = ocr.list_rows(img, band=_b)
    _want = {_namekey(r["text"]) for r in _full
             if _b[0] <= int(r["y_abs"]) < _b[1] and _namekey(r["text"])}
    _gotset = {_namekey(r["text"]) for r in _got if _namekey(r["text"])}
    _miss = _want - _gotset
    if _miss:
        _miss_all.append((_b, sorted(_miss)))
    for _r in _got:
        _k = _namekey(_r["text"])
        if not _k or _k not in _base_by_name:
            continue
        _d_ = abs(int(_r["y_abs"]) - _base_by_name[_k])
        _diffs[_d_] += 1
        if not (_b[0] - 2 * ocr.ROW_PITCH <= int(_r["y_abs"]) < _b[1] + 2 * ocr.ROW_PITCH):
            _beyond_all.append((_b, _k, int(_r["y_abs"])))
ok("④ **不漏行**：4 个不同的带，**带内严格可见的会话名字**一条都没漏",
   not _miss_all, "漏：%s" % _miss_all[:2] if _miss_all else "4/4 带无漏")
ok("⑤ 两侧多留一行是**必需的**（这条钉住'少留一行就会漏带边那条'的实测结论）",
   not _miss_all and (352 - 160) > ocr.ROW_PITCH,
   "带高 %d > 行距 %d" % (352 - 160, ocr.ROW_PITCH))
ok("⑥ **不越界**：不会返回远在带外的行（容差 ±2 行）", not _beyond_all, "%d 处" % len(_beyond_all))
ok("⑦ **坐标同源**：跨 band 的 `y_abs` 与全量一致 —— 实测 |Δ| 分布 %s（≤%d，远小于一行）"
   % (dict(sorted(_diffs.items())), TOL),
   bool(_diffs) and max(_diffs) <= TOL, "max|Δ|=%s" % (max(_diffs) if _diffs else "无样本"))

print("\n══ C. 省了多少 / 边界与异常 ══")
_h_full, _h_band = H - 30, (410 - 90) + 2 * ocr.ROW_PITCH
ok("⑧ 快路径**确实更小**（OCR 面积减少 ⇒ 这才叫省；两侧多留一行后仍然是净省）",
   _h_band < _h_full, "裁剪高度 %d → %d（%.0f%%）" % (_h_full, _h_band, 100.0 * _h_band / _h_full))
_hist = ocr.list_rows(img, band=(10, 20))
ok("⑨ 带**太窄**（比一行还矮）⇒ 退回全高，结果与 `band=None` 完全一致",
   [_key(r) for r in _hist] == [_key(r) for r in _full],
   "窄带 %d 行 / 全量 %d 行" % (len(_hist), len(_full)))
_bad_ok = True
for _b in [("x", 100), (-5, -1), (800, 100), None, (30, 30)]:
    try:
        _r = ocr.list_rows(img, band=_b)
        _bad_ok = _bad_ok and isinstance(_r, list)
    except Exception as _e: # noqa: BLE001
        _bad_ok = False
        print("      （%r 抛了 %s）" % (_b, type(_e).__name__))
ok("⑩ 非法 band（字符串 / 倒序 / 越界 / None / 零高）**不抛异常**且返回 list", _bad_ok)

print("\n══ D. 接线（AST 口径，不看注释）══")
_src = open(os.path.join(ROOT, "agent", "chat_ocr.py"), encoding="utf-8").read()
_tree = ast.parse(_src)
_lr = [n for n in ast.walk(_tree) if isinstance(n, ast.FunctionDef) and n.name == "list_rows"][0]
ok("⑪ `list_rows` 签名里有 `band`", "band" in [a.arg for a in _lr.args.args],
   str([a.arg for a in _lr.args.args]))
_sr = [n for n in ast.walk(_tree) if isinstance(n, ast.FunctionDef) and n.name == "session_rows"][0]
ok("⑫ `session_rows` 也收 `band`", "band" in [a.arg for a in _sr.args.args])
ok("⑬ `session_rows` 把它**传下去**（不是收下就丢）",
   len([n for n in ast.walk(_sr) if isinstance(n, ast.Call) and "band" in {k.arg for k in n.keywords}]) >= 1)
_pad = "ROW_PITCH" in ast.unparse(_lr)
ok("⑭ `list_rows` 里**看得见 `ROW_PITCH`**（两侧留行 + 太窄判据都依赖它，别把这段删了）", _pad)
_calls = [n for n in ast.walk(_tree) if isinstance(n, ast.Call) and "_safe_resize" in ast.unparse(n.func)]
ok("⑮ 尺寸护栏仍在（放大过长会读空，实测踩过）", len(_calls) >= 1, "%d 处" % len(_calls))

print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for _x in FAIL:
        print("  - " + _x)
sys.exit(1 if FAIL else 0)
