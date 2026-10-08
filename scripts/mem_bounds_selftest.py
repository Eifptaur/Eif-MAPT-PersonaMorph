# -*- coding: utf-8 -*-
"""第 161 项（降低运存占用）第一部分：**长期缓存不许无界**。

判据分三层：
  ① **容器本身**（`agent/util.py::BoundedDict`）：有上限 · 超限会淘汰 · 读过的不被先淘汰（LRU）
    · `get`/`[]`/`in`/`clear` 语义与 dict 一致（调用方不必改写法）；
  ② **接线**：审计出的"真会随用户数据增长"的两处必须**是** BoundedDict；
  ③ **审计留痕**：其余模块级缓存**各有各的有界依据**（固定小键空间 / 自带 MAX / 单键）——
    写成断言钉住，免得以后有人把其中某个改成裸 `{}` 而没人发现。

⛔ 反例锚：裸 `dict` 必须**判不出界**（否则本判据只是"恒真"）。
"""
from __future__ import annotations

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.environ["PM_JUDGE_NO_GUI"] = "1"

from agent.util import BoundedDict # noqa: E402
from agent import voice as V # noqa: E402
from agent import wordcloud as W # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


print("\n══ A. 容器本身（`util.BoundedDict`）══")
_d = BoundedDict(16)
for _i in range(100):
    _d["k%d" % _i] = _i
ok("① 塞 100 个（上限 16）⇒ 长度不超过上限", len(_d) <= 16, "len=%d" % len(_d))
ok("② 上限是真的（不是把 maxsize 当装饰）", len(_d) == 16, "len=%d" % len(_d))
ok("③ 最新的那个还在（淘汰的是最老的）", "k99" in _d)
ok("④ 最老的已经被淘汰了", "k0" not in _d)
_d2 = BoundedDict(8)
for _i in range(8):
    _d2["x%d" % _i] = _i
_ = _d2["x0"]                                   # 读一次 ⇒ 视为"用过"
for _i in range(8, 12):
    _d2["x%d" % _i] = _i
ok("⑤ LRU：**读过**的键不该被先淘汰（`x0` 读过 ⇒ 留下）", "x0" in _d2, "keys=%s" % list(_d2)[:4])
ok("⑥ dict 语义不变：`in` / `get` 默认值 / `clear` 都照旧",
   ("k99" in _d) and (_d.get("不存在的键") is None) and (_d.get("nope", 7) == 7)
   and (lambda: (_d.clear(), len(_d) == 0)[1])())
ok("⑦ 反例锚：裸 `dict` 塞 100 个**不会**有上限（证明上面几条不是恒真）",
   len({("k%d" % i): i for i in range(100)}) == 100
   and not isinstance({}, BoundedDict))
ok("⑧ maxsize 非法值被钳到 ≥1（不许出现'上限 0 = 永远空'这种坑）",
   BoundedDict(0).maxsize >= 1 and BoundedDict(-5).maxsize >= 1)

print("\n══ B. 接线：真会增长的两处必须是有界的 ══")
ok("⑨ `wordcloud._font_cache` 是 BoundedDict（键是字号，值是持句柄的字体对象）",
   isinstance(W._font_cache, BoundedDict), type(W._font_cache).__name__)
ok("⑩ `voice._probe_cache` 是 BoundedDict（原先是裸 dict）",
   isinstance(V._probe_cache, BoundedDict), type(V._probe_cache).__name__)
ok("⑪ 两处的上限都写成了**模块级可见的数**（能被人一眼看到，不是藏在调用里）",
   W._font_cache.maxsize >= 16 and V._probe_cache.maxsize >= 8,
   "font=%s probe=%s" % (W._font_cache.maxsize, V._probe_cache.maxsize))

print("\n══ C. 审计留痕：其余模块级缓存**各有各的有界依据** ══")
# ⛔ 这条的目的不是"再验一遍它们"，而是**钉住"当初判过"这件事**：
#    以后有人把其中某个改成裸 `{}`（或拿掉了它那个 MAX/TTL），这条会提醒他"这里有结论"。
_AUDIT = [
    ("agent/meme_search.py", "_CACHE", r"CACHE_MAX\s*=\s*\d+",
     "自带 CACHE_MAX + 超限丢最老 1/4"),
    ("agent/notify_ui.py", "_console_url_cache", r"_CONSOLE_URL_TTL_S\s*=\s*[\d.]+",
     "单键（键就是字面 'v'）+ TTL 3s"),
    ("agent/voice.py", "_probe_cache", r"BoundedDict",
     "已加 BoundedDict"),
    ("agent/dep_heal.py", "_OFFLINE_CACHE", r"^\s*key\s*=\s*exe\s*$",
     "键＝被探测的可执行文件路径（`key = exe`：进程内就那么几个）"),
    ("agent/ffmpeg_bin.py", "_CACHE", r'_CACHE\["p"\]',
     "键是**字面单键**（`_CACHE[\"p\"]`，只有'解析出来的路径'一份）"),
    ("agent/wechat.py", "_SAFE_DB_CACHE", r"class _SafeDB|def _safe_db",
     "键是消息库文件基名（本机 40 个，随库数量级而非消息数）"),
    ("agent/chat_header.py", "_SCENE_SEEN", r"SCENE_MAX_PER_PROC\s*=\s*\d+",
     "键是场景 tag（源码里固定几个）+ 自带 SCENE_MAX_PER_PROC"),
]
_src_cache = {}


def _src(p):
    if p not in _src_cache:
        _src_cache[p] = io.open(os.path.join(ROOT, p), encoding="utf-8", errors="ignore").read()
    return _src_cache[p]


for _p, _nm, _rx, _why in _AUDIT:
    _s = _src(_p)
    _has_cache = re.search(r"^%s\s*(?::[^=]+)?=" % re.escape(_nm), _s, re.M) is not None
    ok("⑫ %-34s ⇒ %s" % (_nm, _why),
       _has_cache and (re.search(_rx, _s, re.M) is not None),
       "cache 在=%s 依据在=%s" % (_has_cache, bool(re.search(_rx, _s, re.M))))

print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for _x in FAIL:
        print("  - " + _x)
sys.exit(1 if FAIL else 0)
