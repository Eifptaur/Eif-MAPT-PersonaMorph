# -*- coding: utf-8 -*-
"""OCR **同图缓存**判据：缓存最容易在"正确性"上骗人，所以这里先钉正确性，再看收益。

四类锚：
  A. **一致性**：同图两次 ⇒ 逐条完全一致；小块变化 ⇒ 与"不缓存直接读"逐条一致。
  B. ★ **不许出现已被删掉的文字**（把某处文字抹白后再读，缓存不许把旧文字吐回来）——最关键的一条。
  C. **语义纪律**：空结果不入缓存（fail-closed / 读空不许被钉死）；未命中面积过半走整图读；
     有界（LRU 上限生效）；线程安全（并发读同一图不炸、结果一致）。
  D. **可见性**：命中率/条目数必须能读出来（全静默的"省时间"没法验证）。

跑法：`runtime\\python\\python.exe scripts\\ocr_cache_selftest.py`
"""
import io
import os
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
os.environ.setdefault("PM_NO_WINDOW_TOUCH", "1")
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
from agent import ocr_cache as C # noqa: E402
import ocr_bench_web as B # noqa: E402


def lines(items):
    return [str(x[0]) for x in items]


#: 用**真图**（transmission 那张：尺寸小、字清楚、有明确的几块文字）
SRC = os.path.join(B.WEB, "gh-024-transmission-WI-main-screen.png")
IMG = Image.open(SRC).convert("RGB")
READER_CALLS = {"n": 0}
READER_AREA = {"px": 0}


def reader(im):
    """计数版读取器（走内置引擎，快；缓存逻辑与引擎无关）。

    ⛔ 只数"调用次数"会误判：整图是 1 次调用，**一条横带也是 1 次** ⇒ 必须同时累计**像素面积**，
      才能说明"只重读了一部分"（第一版就是只数次数，导致 A4 误红）。
    """
    READER_CALLS["n"] += 1
    try:
        READER_AREA["px"] += int(im.size[0]) * int(im.size[1])
    except Exception as e:  # noqa: BLE001
        print("   （累计读取面积失败：%s）" % str(e)[:40])
    return O.recognize(im)


print("── A. 一致性与收益（同图两次 / 小块变化）──")
# ⛔ 这里**故意不传 tile**：要测的就是**默认配置**（横带）。上一版判据自己硬写了方块尺寸
#    (64,96) ⇒ 复现了"方块切断文字行 ⇒ 块缓存 0 命中"那个坑，A4/A5 当场红（判据自己也会写错，
#    所以默认值本身也要被钉住：见 A6）
cache = C.OcrCache(max_images=8, max_tiles=512)
READER_CALLS["n"] = 0
READER_AREA["px"] = 0
r1 = C.cached_recognize(IMG, reader, cache)
c1 = READER_CALLS["n"]
a1 = READER_AREA["px"]
t0 = time.time(); r2 = C.cached_recognize(IMG, reader, cache); dt2 = time.time() - t0
ok("A1 同图第二次：结果**逐条一致**，且**一次 OCR 都没调**（整图精确命中）",
   lines(r1) == lines(r2) and READER_CALLS["n"] == c1,
   "第一次 %d 次调用 · 第二次新增 %d 次 · 复用时 %.3fs" % (c1, READER_CALLS["n"] - c1, dt2))
ok("A2 第二次**明显更快**（不做 OCR，只搬缓存）", dt2 < 0.05, "%.3fs" % dt2)

# 小块变化：在左下角画一个矩形（模拟"某一行变了"）
IMG2 = IMG.copy()
ImageDraw.Draw(IMG2).rectangle([8, IMG.height - 30, 240, IMG.height - 8], fill=(250, 250, 250))
_no_cache = lines(reader(IMG2))
READER_CALLS["n"] = 0
READER_AREA["px"] = 0
_cached = lines(C.cached_recognize(IMG2, reader, cache))
a2 = READER_AREA["px"]
ok("A3 小块变化后：缓存路径的结果与**不缓存直接读**逐条一致（不漏、不多）",
   sorted(_cached) == sorted(_no_cache),
   "缓存 %d 行 / 直读 %d 行 · 差集 %s" % (len(_cached), len(_no_cache),
                                        sorted(set(_no_cache) ^ set(_cached))[:3]))
ok("A4 ★ 小块变化**只重读了很小一块**（按**像素面积**比：不是整图重读）",
   a2 > 0 and a2 <= a1 * 0.5, "重读 %d px / 首次 %d px（%.0f%%）· 调用 %d 次"
   % (a2, a1, 100.0 * a2 / max(1, a1), READER_CALLS["n"]))
ok("A5 ★ **块级命中真的发生了**（`hits_tile > 0`）—— 挡「块切得不贴合内容，复用等于没有」这个坑",
   cache.stats()["hits_tile"] > 0, str(cache.stats())[:80])

print("\n── B. ★ 不许出现已被删掉的文字（缓存最危险的一类错）──")
# 找一条确实读出来的文字，把它所在区域抹白，然后要求缓存**不再**返回它
_cache2 = C.OcrCache(max_images=4, max_tiles=256)
_first = C.cached_recognize(IMG, reader, _cache2)
_target = None
for txt, x, y, w, h in _first:
    if len(str(txt).strip()) >= 6 and x > 4 and y > 4:
        _target = (str(txt), x, y, w, h)
        break
if not _target:
    ok("B1（找不到可抹白的文字，跳过后面的抹白锚）", False, "真图上没读到足够长的文字")
else:
    txt, x, y, w, h = _target
    IMG3 = IMG.copy()
    ImageDraw.Draw(IMG3).rectangle([max(0, x - 3), max(0, y - 3), x + w + 3, y + h + 3],
                                  fill=(255, 255, 255))
    _after = lines(C.cached_recognize(IMG3, reader, _cache2))
    ok("B1 ★ 抹掉的文字**不许**从缓存里再吐出来",
       not any(O.norm(txt) in O.norm(x) for x in _after),
       "抹掉的是 %r；抹后仍出现：%s" % (txt[:18], [x for x in _after if O.norm(txt) in O.norm(x)][:2]))
    _direct = lines(reader(IMG3))
    ok("B2 抹白后缓存路径与直读**一致**（既不能多吐旧字，也不该少读新字）",
       sorted(_after) == sorted(_direct),
       "缓存 %d 行 / 直读 %d 行 · 差集 %s" % (len(_after), len(_direct),
                                             sorted(set(_direct) ^ set(_after))[:3]))

print("\n── C. 语义纪律（空结果 / 整图优先 / 有界 / 线程安全）──")
class _Empty:
    def __init__(self):
        self.n = 0

    def __call__(self, im):
        self.n += 1
        return []

_e = _Empty()
_cache3 = C.OcrCache()
C.cached_recognize(IMG, _e, _cache3)
C.cached_recognize(IMG, _e, _cache3)
ok("C1 ★ 读空的结果**不入缓存**（fail-closed 与「这次没读到」不许被钉死）",
   _e.n >= 2 and _cache3.get_full(IMG) is None, "读取器被调用 %d 次" % _e.n)
ok("A6 ★ 默认块是**横带**（宽 0＝整宽）：界面文字又宽又扁，方块会把行切断 ⇒ 复用等于没有",
   C.DEFAULT_TILE[0] == 0 and C.DEFAULT_TILE[1] >= 32, "DEFAULT_TILE=%s" % (C.DEFAULT_TILE,))
ok("C2 统计可见（命中率/条目数能读出来）", set(["hit_rate", "hits_full", "hits_tile",
                                              "misses_tile", "images", "tiles"]).issubset(
                                                  _cache3.stats().keys()), str(_cache3.stats())[:70])
# 有界
_small = C.OcrCache(max_images=2, max_tiles=64)
for i in range(12):
    _im = Image.new("RGB", (80, 120), (i * 7 % 255, 30, 200))
    _small.put_full(_im, [("x%d" % i, 0, 0, 5, 5)])
    for j in range(20):
        _small.put_tile("h%d-%d" % (i, j), [("t", 0, 0, 1, 1)])
st = _small.stats()
ok("C3 有界：整图 ≤ max_images、块 ≤ max_tiles（不会无限涨）",
   st["images"] <= 2 and st["tiles"] <= 64, "images=%d tiles=%d" % (st["images"], st["tiles"]))
# 线程安全
_cache4 = C.OcrCache()
_errors = []
def _worker():
    try:
        for _ in range(6):
            C.cached_recognize(IMG, reader, _cache4)
    except Exception as e:  # noqa: BLE001
        _errors.append(str(e)[:60])
_ths = [threading.Thread(target=_worker) for _ in range(6)]
[t.start() for t in _ths]
[t.join() for t in _ths]
ok("C4 线程安全：6 线程并发读同一图不炸、结果一致",
   not _errors and lines(C.cached_recognize(IMG, reader, _cache4)) == lines(r1),
   "错误 %s · 命中率 %.2f" % (_errors[:2], _cache4.stats()["hit_rate"]))

print("\n── E. 多帧场景的收益（缓存最真实的用法：同一窗口连续读几帧）──")
# 预热：把引擎冷启与"首次整图"排除掉，只比"第二帧以后"的代价
C.cached_recognize(IMG, reader, C.OcrCache())
_frames = []
for _i in range(6):
    _im = IMG.copy()
    ImageDraw.Draw(_im).rectangle([8, IMG.height - 30, 240, IMG.height - 8], fill=(250, 250, 250))
    _frames.append(_im)


def _seq(use_cache: bool) -> float:
    _c = C.OcrCache()
    _t = time.time()
    for _im in _frames:
        if use_cache:
            C.cached_recognize(_im, reader, _c)
        else:
            reader(_im)
    return time.time() - _t


_t_no, _t_yes = _seq(False), _seq(True)
ok("E1 ★ 连续 6 帧（每帧只动一条带）用缓存明显更快（用相对阈值，防慢机器误红）",
   _t_yes * 2 <= _t_no, "无缓存 %.2fs / 有缓存 %.2fs（%.1f×）"
   % (_t_no, _t_yes, _t_no / max(1e-6, _t_yes)))
_k = C.OcrCache()
_c_last = sorted(set(lines(C.cached_recognize(_frames[-1], reader, _k))))
_d_last = sorted(set(lines(reader(_frames[-1]))))
ok("E2 缓存路径的**最后一帧**与直读逐条一致（换个帧再验一次一致性）",
   _c_last == _d_last, "直读 %d 行 / 缓存 %d 行 · 差集 %s"
   % (len(_d_last), len(_c_last), sorted(set(_c_last) ^ set(_d_last))[:3]))

print("\n── D. 源码纪律（口径写明白，防后人踩）──")
_src = io.open(os.path.join(ROOT, "agent", "ocr_cache.py"), encoding="utf-8").read()
ok("D1 文档写明「只缓存成功读数」（空结果不入缓存）", _sm.has(_src, "只缓存"))
ok("D2 文档写明「键是内容不是时间」（所以不需要 TTL）", _sm.has(_src, "键是内容，不是时间"))
ok("D3 代码里确实拦了空结果（两处 put 都先判空）",
   _sm.has(_src, "if not items:") and _sm.has(_src, "空结果不入缓存"))
ok("D4 未命中面积过半走整图读（分块不划算的场景有兜底）", _sm.has(_src, "miss_area * 2 >= w * h"))
ok("D5 有界 LRU（两处都有条目上限淘汰）", _sm.has(_src, "max_images") and _sm.has(_src, "max_tiles")
   and _sm.has(_src, "popitem(last=False)"))

print("\n缓存判据：%d 通过 / %d 失败" % (PASS, FAIL))
print("（命中统计示例：%s）" % _cache4.stats())
sys.exit(1 if FAIL else 0)
