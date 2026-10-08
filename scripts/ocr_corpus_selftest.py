# -*- coding: utf-8 -*-
"""网图素材库 + OCR 标准判据。

**为什么单独立一条**：素材库是"识别率"这个数字的**分母**。分母不可信（错真值 / 混进非界面图），
后面所有优化都是自欺 —— 本轮就是这么栽过一次：30 张里 10 张不合格、真值约 30% 记错，
把 96.6% 低报成 48.3%/65.5%。所以素材库/真值本身必须有机器门禁。

四轴：
  A. 素材：张数达标、**全部过自动体检**（内置引擎 ≥3 行）、内容去重（md5 唯一）
  B. 真值：分两档且**口径写清** ——
     tier-1「核过」：看原图（四象限原分辨率）逐条读出来的，硬指标 / 棘轮用它；
     tier-2「一致性」：多引擎对同一条**互相印证**才收的自动真值，只用于**引擎对比**（口径注明，不当硬指标）
  C. 标准：`TARGET_RECALL=0.99`（用户把标准提到 99%）＋ 棘轮 `BEST_RECALL` 只许涨不许跌
  D. 反向锚：采集只在素材目录里写、素材不在产品包内（隐私与体积两边都要守住）

跑法：`runtime\\python\\python.exe scripts\\ocr_corpus_selftest.py`
"""
import hashlib
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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


import ocr_bench_web as B # noqa: E402

WEB = B.WEB
EXTS = (".png", ".jpg", ".jpeg")

print("── A. 素材库 ──")
_files = sorted(f for f in os.listdir(WEB) if f.lower().endswith(EXTS)
                and os.path.isfile(os.path.join(WEB, f)))
# ⚠️ 口径（按用户的分阶段要求改）：**当前阶段是「先把小库的识别率冲到并稳住 99-100%，
#    稳定之后再扩库」** ⇒ 这时候「张数没到 100」是**计划内**的，不该判红。
#    改成**棘轮下限**：只许涨不许跌（跌了说明有人删素材），离目标多远如实打印。
CORPUS_FLOOR = 56   # 历史最好张数（只许往上改）
ok("A1 素材张数只许涨（下限 %d 张；目标 %d 张）" % (CORPUS_FLOOR, B.TARGET_IMAGES),
   len(_files) >= CORPUS_FLOOR,
   "现有 %d 张 / 目标 %d 张（还差 %d）" % (len(_files), B.TARGET_IMAGES,
                                          max(0, B.TARGET_IMAGES - len(_files))))
_md5 = {}
_dup = []
for f in _files:
    h = hashlib.md5(io.open(os.path.join(WEB, f), "rb").read()).hexdigest()
    if h in _md5:
        _dup.append("%s＝%s" % (f, _md5[h]))
    _md5[h] = f
ok("A2 没有重复内容（同一张图换个名字再来一遍很常见）", not _dup, "；".join(_dup[:3]))
_src = os.path.join(WEB, "_sources.json")
_db = {}
if os.path.exists(_src):
    try:
        _db = json.load(io.open(_src, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        ok("A3 `_sources.json` 可解析", False, str(e)[:60])
ok("A3 `_sources.json` 可解析且登记了我方素材（可追溯来源）",
   bool(_db) and all(k in _db for k in _files[:5]), "登记 %d 条" % len(_db))
_no_src = [f for f in _files if f not in _db]
ok("A4 每张素材都能查到来源（repo/path/url）—— **可追溯**才敢用",
   len(_no_src) <= 0, "无来源 %d 张：%s" % (len(_no_src), _no_src[:3]))

print("\n── B. 真值两档（口径必须写清）──")
_t1 = list(B.CASES)
ok("B1 tier-1「核过」真值存在且非空", bool(_t1) and all(len(c) >= 2 for c in _t1),
   "%d 张 / %d 条" % (len(_t1), sum(len(c) - 1 for c in _t1)))
_bad_truth = []
for c in _t1:
    for t in c[1:]:
        if not str(t).strip() or len(str(t)) > 60:
            _bad_truth.append(str(t)[:30])
ok("B2 tier-1 真值形状正常（无空串、无超长段）", not _bad_truth, "；".join(_bad_truth[:3]))
ok("B3 tier-1 真值对应的图**真的在素材库**里",
   all(os.path.exists(os.path.join(WEB, c[0])) for c in _t1),
   "缺图：%s" % [c[0] for c in _t1 if not os.path.exists(os.path.join(WEB, c[0]))][:2])
_src_bench = io.open(os.path.join(ROOT, "scripts", "ocr_bench_web.py"), encoding="utf-8").read()
ok("B4 ★ 口径写明：tier-1 必须「看原图核过」、tier-2 是「多引擎一致性」（自动，只用于对比）",
   _sm.has(_src_bench, "看原图逐条核过") and _sm.has(_src_bench, "一致性"))
ok("B5 tier-2 一致性真值**不当硬指标**（只看定级 AC 时不许引用它）",
   _sm.has(_src_bench, "只用于") or _sm.has(_src_bench, "仅用于"))
# ── B6/B7：★ 修「假尺子」的结构性根因 —— tier-1 分母过去只来自写死的 `CASES`（6 张 / 31 条），
#    `truth_store` 里人核过的条目**从来没接回基准** ⇒ 人看图核完也没用。
#    现在 `_all_cases()` 把 `human` 层并进 `CASES`：人核过多少，硬指标就拿多少量。
ok("B6 ★ tier-1 分母**接到真值库**（人核过的会自动进基准，不再是写死的 6 张）",
   _sm.has(_src_bench, "def _all_cases(") and _sm.has(_src_bench, "_human_from_store(")
   and _sm.has(_src_bench, "human_only=True"),
   "看 `_all_cases()` / `_human_from_store()`")
ok("B7 ★ 量召回走的是合并后的用例集（不是裸 `CASES`，否则桥白接）",
   _sm.has(_src_bench, "for name, *truths in _all_cases()"),
   "看 `measure()` 与 `main()` 的迭代对象")

print("\n── C. 标准（99%）与棘轮 ──")
ok("C1 标准＝99%（项目约定）", abs(float(getattr(B, "TARGET_RECALL", 0)) - 0.99) < 1e-9,
   "TARGET_RECALL=%s" % getattr(B, "TARGET_RECALL", None))
ok("C2 棘轮存在（记录历史最好读数，只许涨不许跌）",
   isinstance(getattr(B, "BEST_RECALL", None), (int, float)) and getattr(B, "BEST_RECALL") > 0,
   "BEST_RECALL=%s" % getattr(B, "BEST_RECALL", None))

print("\n── D. 反向锚 ──")
ok("D1 ★ 素材**不进产品包**（在 dev-workspace 下，打包清单扫不到）",
   os.path.abspath(ROOT) not in os.path.abspath(WEB)
   and "dev-workspace" in os.path.abspath(WEB).replace("\\", "/"), os.path.abspath(WEB)[-60:])
_pack = io.open(os.path.join(ROOT, "scripts", "pack_online.py"), encoding="utf-8").read()
ok("D2 打包脚本没有把 ocr-samples 收进去", not _sm.has(_pack, "ocr-samples"))
_h = io.open(os.path.join(ROOT, "scripts", "ocr_corpus_harvest.py"), encoding="utf-8").read()
ok("D3 采集脚本：有内容去重 + 每张都体检 + 只在素材目录里写",
   _sm.has(_h, "md5") and _sm.has(_h, "def probe(") and _sm.has(_h, "WEB"))
ok("D4 采集脚本不碰用户素材（不读用户的微信数据目录）",
   not _sm.has(_h, "WeChat Files") and not _sm.has(_h, "wxid_"))
# ── D5/D6：修「配额耗尽静默空转」真缺陷（旧版任何异常都 `return []`，
#    配额用光时与"仓库没图"不可区分 ⇒ 跑 25 分钟一张没收到、无任何提示）。
ok("D5 ★ 列树能区分「配额耗尽」与「仓库没图」（403/429 回报 None）",
   _sm.has(_h, "urllib.error") and _sm.has(_h, "HTTPError")
   and _sm.has(_h, "return None"), "看 `tree()` 的 HTTPError 分支")
ok("D6 ★ 配额耗尽时**立即停机并告知**（不再静默 continue 整份清单）",
   _sm.has(_h, "paths is None") and _sm.has(_h, "配额已耗尽"),
   "看 main() 里对 None 的处理")

print("\n素材库/标准判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
