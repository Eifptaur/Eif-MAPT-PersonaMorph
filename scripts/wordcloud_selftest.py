# -*- coding: utf-8 -*-
"""「热词词云」判据（第 136 项）—— 不联网、不写产品 `data/`（图像落临时目录）。

守四件事：
  A **排版**：词**不重叠**、**不出血**（都在画布内）、**字号随频次单调**（高频不小于低频）。
  B **出图**：PNG 真生成、**不是白图**（颜色要够多）、且**内容要铺得满**
    （出图按内容裁边 ⇒ 若只占中间一小团，最终图会又小又糊，实测踩过）。
  C **诚实**：样本为空 ⇒ **明确说没数据**（不出空白图）；**找不到中文字体 ⇒ 如实报错**
    （不许画一堆方块还说成功）。
  D **接线**：路由两处声明 ＋ **两个实现面（web／Qt）都有入口**。

用法：runtime\\python\\python.exe scripts/wordcloud_selftest.py
"""
import io
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import _srcmatch as _sm  # noqa: E402
from agent import wordcloud as W  # noqa: E402

PASS = FAIL = 0
TD = tempfile.mkdtemp(prefix="wc-")
WORDS = [("在吗", 37), ("下周", 22), ("考试", 18), ("吃饭", 15), ("游戏", 12), ("加班", 9),
         ("天气", 7), ("电影", 6), ("睡觉", 5), ("咖啡", 4), ("论文", 3), ("房租", 2)]


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


print("── A. 排版 ──")
lay = W.layout(WORDS)
ok("A0 分母守卫：排下的词数与输入一致（否则下面的检查是空转）", len(lay) == len(WORDS), "%d/%d" % (len(lay), len(WORDS)))
_ov = []
for i in range(len(lay)):
    for j in range(i + 1, len(lay)):
        a, b = lay[i], lay[j]
        if not (a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"]
                or a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"]):
            _ov.append((a["word"], b["word"]))
ok("A1★ **两两不重叠**（挤在一起还不如不出图）", not _ov, str(_ov[:3]))
_off = [p["word"] for p in lay if p["x"] < 0 or p["y"] < 0 or p["x"] + p["w"] > W.W or p["y"] + p["h"] > W.H]
ok("A2★ **不出血**：每个词都在画布内（裁掉半个字看着就是坏的）", not _off, str(_off[:3]))
_pairs = sorted(lay, key=lambda p: -p["count"])
ok("A3★ **字号随频次单调**（高频不小于低频）",
   all(_pairs[i]["size"] >= _pairs[i + 1]["size"] for i in range(len(_pairs) - 1)),
   str([(p["word"], p["count"], p["size"]) for p in _pairs[:3]]))
ok("A4 尊重上限（给 60 个词也只排 `MAX_WORDS` 个）",
   len(W.layout([("w%02d" % i, 60 - i) for i in range(60)])) <= W.MAX_WORDS, "")

print("\n── B. 出图 ──")
r = W.render_png(WORDS, os.path.join(TD, "a.png"))
ok("B1★ PNG 真生成且非空", r["ok"] and os.path.getsize(r["path"]) > 3000,
   "%d 字节" % (os.path.getsize(r["path"]) if r["ok"] else 0))
try:
    from PIL import Image
    _im = Image.open(r["path"]).convert("RGB")
    _w, _h = _im.size
    _cols = len(set(_im.getdata()))
except Exception as e:  # noqa: BLE001
    _w = _h = _cols = 0
ok("B2★ **不是白图**：颜色数要够多（纯白＝什么都没画上去）", _cols > 3, "%d 色 %dx%d" % (_cols, _w, _h))
ok("B3★ **铺得满**：内容宽度接近画布宽（按内容裁边，若只占中间一小团，最终图会又小又糊）",
   _w >= int(W.W * 0.6), "%d / %d" % (_w, W.W))
_big = W.render_png([(w, 400 - i * 7) for i, w in enumerate(
    ["在吗", "下周", "考试", "吃饭", "游戏", "加班", "天气", "电影", "睡觉", "咖啡", "论文", "房租",
     "面试", "地铁", "老板", "会议", "客户", "代码", "服务器", "上线"])], os.path.join(TD, "b.png"))
ok("B4 词多的时候也排得下（20 个词全进图）", _big["ok"] and _big["words"] == 20, str(_big.get("words")))

print("\n── C. 诚实 ＋ 清洗 ──")
ok("C1★ 样本为空 ⇒ **如实说没数据**（不出空白图）",
   (not W.render_png([], os.path.join(TD, "e.png"))["ok"]) and "没排下" in W.render_png([], os.path.join(TD, "e.png"))["note"], "")
_orig_ff = W.find_font
try:
    W.find_font = lambda: ""                      # 模拟"本机没中文字体"
    _no = W.render_png(WORDS, os.path.join(TD, "nofont.png"))
finally:
    W.find_font = _orig_ff
ok("C2★ 找不到中文字体 ⇒ **如实报错**，绝不画方块还说成功",
   (not _no["ok"]) and "字体" in _no["note"], _no["note"][:30])
ok("C3 清洗：空白/换行/控制字符被去掉（用户真的会发这些）",
   W._clean(" 在\n吗\x07 ") == "在吗" and W._clean("a" * 50) == "a" * W.WORD_MAX, "")
ok("C4 `_clean` 不把词变没（去噪不做「艺术加工」）", W._clean("好好聊") == "好好聊", "")

print("\n── D. 接线（三个面都别缺）──")
_r = io.open(os.path.join(ROOT, "agent", "routes.py"), encoding="utf-8").read()
ok("D1 路由**两处都声明**（ROUTES 允许方法 ＋ HANDLERS 处理函数）",
   _sm.has(_r, '"/api/wordcloud": ("POST",)') and _sm.has(_r, '"_rapi_wordcloud"'), "")
ok("D2 处理器在位（调 `wordcloud.export`）",
   _sm.has(io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read(), "_wc.export("), "")
_web = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
ok("D3★ web 有入口（按钮 ＋ 真调 `/api/wordcloud`，并且出完**顺手打开**）",
   _sm.has(_web, 'id="memWordcloud"') and _sm.has(_web, "'/api/wordcloud'") and _sm.has(_web, "'/api/open-path'"), "")
_qt = io.open(os.path.join(ROOT, "ui_qt", "panels_custom.py"), encoding="utf-8").read()
ok("D4★★ **两个实现面同批**：Qt 也有入口（同一个路由，不另写一套算法）",
   _sm.has(_qt, 'Btn("出热词词云"') and _sm.has(_qt, '"/api/wordcloud"'), "")
ok("D5★ 采样来源是既有的热词统计（**不另写一份 n-gram**）",
   _sm.has(io.open(os.path.join(ROOT, "agent", "wordcloud.py"), encoding="utf-8").read(),
           "from .memory import hot_words"), "")

print("\n== 词云判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
