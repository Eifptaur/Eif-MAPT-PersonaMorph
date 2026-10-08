# -*- coding: utf-8 -*-
"""**硬读档**（`chat_ocr.recognize_boost`）与 99%/100% 标准的判据。

## 为什么必须单独立一条
"识别率冲到 99~100%"这件事**最容易作弊**，作弊手法都很隐蔽：
  · 把读不到的真值悄悄删掉（分母变小、分数变好看）；
  · 把"宽容匹配"当严格数报（口径悄悄放宽）；
  · 只报硬读档的成绩、不提它 20s 一张的代价（把成本藏起来）；
  · 让真值本身就是从 OCR 输出抄来的（自己考自己）。
⇒ 所以这条判据把**四个反作弊锚**都钉死：
  A. **真值条数只许涨不许降**（删真值立刻红）
  B. **单调性**：硬读档 ⊇ 基础档（加档只会更多，不会更少 —— 少了就是实现有 bug）
  C. **坐标映回原尺度**：放大档读到的框必须落在**原图**坐标系里（否则上层定位整体偏移）
  D. **口径与代价都要写明白**：文档串里必须有"不进每笔发送链"和"耗时≈档数倍"

跑法：`runtime\\python\\python.exe scripts\\ocr_boost_selftest.py`
"""
import io
import os
import sys


sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
import _srcmatch as _sm # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


from PIL import Image, ImageDraw # noqa: E402
from agent import chat_ocr as O # noqa: E402
import ocr_bench_web as B # noqa: E402

print("── A. 反作弊：真值条数只许涨（删真值＝假提升）──")
_n_truths = sum(len(c) - 1 for c in B.CASES)
ok("A1 真值条数 ≥ 31（历史最好口径：31 条；删真值会立刻红）", _n_truths >= 31, "当前 %d 条" % _n_truths)
ok("A2 每张 tier-1 图的真值都 ≥3 条（稀稀拉拉几张图提不动分，也说明没认真核）",
   all(len(c) >= 4 for c in B.CASES), "最少那张 %d 条" % min(len(c) - 1 for c in B.CASES))
ok("A3 棘轮：硬读档 100% / 基础档有基线，且 硬读 ≥ 基础",
   abs(B.BEST_RECALL - 1.0) < 1e-9 and B.BEST_RECALL_BASE > 0
   and B.BEST_RECALL >= B.BEST_RECALL_BASE,
   "硬读 %.3f / 基础 %.3f" % (B.BEST_RECALL, B.BEST_RECALL_BASE))
ok("A4 目标与口径常量化可复现（TARGET_RECALL / BOOST_SCALES / BOOST_INVERT / BOOST_DESC）",
   abs(B.TARGET_RECALL - 0.99) < 1e-9 and max(B.BOOST_SCALES) <= 3
   and isinstance(B.BOOST_INVERT, bool) and bool(B.BOOST_DESC))

print("\n── B/C. 单调性 + 坐标映回原尺度（合成图，真值确定）──")
im = Image.new("RGB", (900, 220), (252, 252, 250))
d = ImageDraw.Draw(im)
d.text((30, 40), "Uploaded:  12.5 MB", fill=(90, 90, 92))
d.text((30, 120), "Remaining Time: 3h 12m", fill=(96, 96, 98))
_base = O.recognize_dual(im)
_boost = O.recognize_boost(im, scales=(1, 2), invert=True)
_bn = {O.norm(str(x[0])) for x in _base if str(x[0]).strip()}
_on = {O.norm(str(x[0])) for x in _boost if str(x[0]).strip()}
ok("B1 单调性：基础档读到的，硬读档**必须也读到**（加档不许丢东西）",
   _bn <= _on, "基础 %d 条 · 硬读 %d 条 · 少的：%s" % (len(_bn), len(_on), list(_bn - _on)[:2]))
ok("B2 合成小字场景：硬读档至少不比基础档差（这条是「加档真的有用」的行为锚）",
   len(_on) >= len(_bn), "硬读 %d / 基础 %d" % (len(_on), len(_bn)))
_w = h = None
_ge = [(int(x[1]), int(x[2]), int(x[3]), int(x[4])) for x in _boost if len(x) >= 5]
ok("C1 硬读档返回的框坐标落在**原图**范围内（放大档要除回原尺度，否则上层定位整体偏移）",
   all(x >= 0 and y >= 0 and x + w <= 900 + 2 and y + h <= 220 + 2 for x, y, w, h in _ge) and bool(_ge),
   "框样例 %s" % (_ge[:2],))
ok("C2 硬读档返回的形状与 `recognize` 同构（5 元组；否则 list_rows 分组全错）",
   all(len(x) == 5 for x in _boost) and (not _boost or isinstance(_boost[0][0], str)))

print("\n── D. 口径与代价必须写明白（不许把成本藏起来）──")
_src = io.open(os.path.join(ROOT, "agent", "chat_ocr.py"), encoding="utf-8").read()
ok("D1 文档串写明它**不进每笔发送链**（20s 一张的代价不能默认扛）",
   _sm.has(_src, "不进每笔发送链"))
ok("D2 文档串写明**代价**（耗时的来源与量级，调用方能预估）",
   (_sm.has(_src, "是耗时的全部来源") or _sm.has(_src, "固定开销"))
   and (_sm.has(_src, "≈20.5s") or _sm.has(_src, "20.5s")))
ok("D3 倍率有上限（≤3：再大只让第二引擎更慢，实测收益趋零）",
   _sm.has(_src, "1 <= int(s) <= 3") and _sm.has(_src, "max(1"))
ok("D5 ★ 反回退锚：**默认不给放大档用第二引擎**（它是耗时的全部来源；要用必须显式 heavy=True）",
   _sm.has(_src, "heavy: bool = False") and _sm.has(_src, "if heavy and s > 1:"))
ok("D7 两档 profile 可跑且语义分明（fast＝纯内置 / full＝加第二引擎）",
   _sm.has(_src, 'profile: str = ""') and _sm.has(_src, 'if _prof == "fast"')
   and _sm.has(_src, 'elif _prof == "full"'))
ok("D8 ★ 快档**真的不叫第二引擎**（源码锚：只有 union_base 为真时才走 recognize_union）",
   _sm.has(_src, "if s == 1 and union_base:"))
ok("D9 文档串记下三档实测数字（基础 23/31·快速 26/31·全对 31/31），便于后人判断「更慢值不值」",
   _sm.has(_src, "23/31") and _sm.has(_src, "26/31") and _sm.has(_src, "31/31"))
ok("D6 基准里的快档参数就是「默认档」（BOOST_HEAVY=False），别偷偷开全开档",
   hasattr(B, "BOOST_HEAVY") and B.BOOST_HEAVY is False and B.BOOST_SCALES == (1, 2))
ok("D4 基准里**两档都量**（基础档数字必须同时列出来，不许只报好看的那档）",
   _sm.has(io.open(os.path.join(ROOT, "scripts", "ocr_bench_web.py"), encoding="utf-8").read(),
           "基础档（recognize_dual）"))

print("\n── E. 反向锚：真值不是从 OCR 输出抄来的 ──")
ok("E1 基准文档串里保留「不许再从缩略图抄真值」的口径（防再犯抄错真值那类事故）",
   _sm.has(io.open(os.path.join(ROOT, "scripts", "ocr_bench_web.py"), encoding="utf-8").read(),
           "不许再从缩略图抄真值"))
ok("E2 tier-1 真值**不参与** `consensus_truths()` 的生成（自证式真值必须与硬指标分开）",
   "consensus_truths" in dir(B) and "CASES" not in  (B.consensus_truths.__doc__ or ""))

print("\n硬读档判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
