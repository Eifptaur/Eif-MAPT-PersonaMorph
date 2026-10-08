# -*- coding: utf-8 -*-
"""视频气泡「下载/暂停/播放图标」定位 + 三态判别的判据。

背景（`报告/外部项目考察-20261001/视频下载图标定位法与短文本修正.md`）：
  后提出：「它中间是有一个图标的，就是一个下载图标，这个能不能用」
  现状 `_media_bubble_locate` 用「最下面那个**头像**」当锚 ⇒ 被迫"必须是最新一条"，
  且落点是"气泡块中心" ⇒ **竖版视频会点偏**（图标在气泡正中偏上）。

⛔⛔ 用户口述的三态模型（**这是判据的目标**）：
  · **下载前**：**细白圆环** + 环心**白色下箭头（↓）**  ⇒ **该点**（点一下＝触发下载）
  · **下载中**：**粗灰圆环**（＝进度条，按顺时针被填白） + 环心 **⏸ 双竖杠** ⇒ **不该点**
      （下载中再点一下会**暂停**、再点才继续 —— 只会更慢）
  · **下载后**：**细白圆环** + 环心 **▶ 三角** ⇒ **不该点**
      （读文件即可；把视频点开是给人看的，不适合模型）
  ⇒ 三态**共有圆环**；靠**环的粗细**（前/后细、中粗）与**环心图案**区分。
  ⇒ `_find_video_dl_icon` **只在 todo 时返回落点**；busy/done 一律 None。

⛔ 纪律 11：**样本必须自造**（`PIL` 画环/箭头/暂停/三角）。
  用户截屏里的白圈是**微信自己画的**，可当"真实样本冒烟核对"（见 F 段：从桌面读真图），
  但主判据以合成样本打底（真图分辨率低、界面噪声多）。

跑法：py scripts/video_dl_icon_selftest.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from PIL import Image, ImageDraw  # noqa: E402

from agent import wechat as W  # noqa: E402

PASS, FAIL = [0], [0]


def ok(name, cond, extra=""):
    if cond:
        PASS[0] += 1
        print("  PASS %s" % name)
    else:
        FAIL[0] += 1
        print("  FAIL %s  %s" % (name, extra))


def _canvas(w=420, h=520, bg=(245, 245, 245)):
    """⛔ 背景必须用**浅色**（真图实测聊天底是白的）。

    上一版用深色 (52,52,58) ⇒ 亮度 ~54 < 90 ⇒ **整幅画布都被当成"深色气泡"**
    （实测 `bubbles: [[0,419,0,516]]`）⇒ 检测器进的是"全图找环"而不是"气泡里找环"
    ⇒ 合成样本与真图走的不是同一条路径，判据失去意义。改浅底后 A2/A3 立刻转绿。
    """
    im = Image.new("RGB", (w, h), bg)
    return im, ImageDraw.Draw(im)


def _bubble(im, d, x0=120, y0=60, x1=330, y1=470):
    """画一个深色视频气泡（大深色方块）——检测器靠它把搜索收进气泡内。

    ⛔ 宽必须 ≥180px（检测器的气泡下限）——D 段曾因只画 160px 宽而被判"没有气泡"。
    """
    d.rectangle([x0, y0, x1, y1], fill=(30, 31, 45))


def _ring(d, cx, cy, r=26, ring=(250, 250, 250), ink=(250, 250, 250), glyph="arrow", lw=3):
    """画"圆环 + 环心图案"的图标。

    glyph: "arrow"（↓ 未下载）/ "pause"（⏸ 下载中）/ "play"（▶ 已下好）/
           "thick"（粗环 + ⏸，模拟下载中的进度环）

    ⛔ 箭头必须按**真图实测比例**画（逐像素打印 1580×1244 真图得到）：
      真图 ↓ 的环心（r=26）是——**竖柄只 2~4px 宽、约占上半 14 行；三角最宽 ~18px、占下半 11 行**
      ⇒ up/dn ≈ **46/109**（≈2.4 倍）。上一版画成"宽柄 + 三角起点就在圆心"⇒ up/dn ≈ 161/141
      ⇒ 判不出"下宽"（合成样本 A1/A1c 红）。**这是合成样本的问题，不是检测器的**。
    """
    if glyph == "thick":
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(150, 150, 150), width=7)
        # 左半圈填白（进度）
        d.arc([cx - r, cy - r, cx + r, cy + r], 90, 270, fill=(250, 250, 250), width=7)
        # ⏸ 双竖杠
        d.rectangle([cx - r * 0.34, cy - r * 0.44, cx - r * 0.12, cy + r * 0.44], fill=ink)
        d.rectangle([cx + r * 0.12, cy - r * 0.44, cx + r * 0.34, cy + r * 0.44], fill=ink)
        return
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ring, width=lw)
    if glyph == "arrow":
        # 竖柄：细（±1px）、从 cy-0.45r 到 cy-0.05r
        d.rectangle([cx - 1, cy - r * 0.45, cx + 1, cy - r * 0.05], fill=ink)
        # 底部三角：底边齐 cy-0.05r、宽 ±0.38r、顶点到 cy+0.58r（**完全落在下半**）
        d.polygon([(cx - r * 0.38, cy - r * 0.05), (cx + r * 0.38, cy - r * 0.05),
                   (cx, cy + r * 0.58)], fill=ink)
    elif glyph == "pause":
        d.rectangle([cx - r * 0.34, cy - r * 0.44, cx - r * 0.12, cy + r * 0.44], fill=ink)
        d.rectangle([cx + r * 0.12, cy - r * 0.44, cx + r * 0.34, cy + r * 0.44], fill=ink)
    elif glyph == "play":
        # ▶：左边缘齐平、向右收尖
        d.polygon([(cx - r * 0.42, cy - r * 0.52), (cx - r * 0.42, cy + r * 0.52),
                   (cx + r * 0.52, cy)], fill=ink)


_find = W.WeChatAdapter._find_video_dl_icon

print("== A. 三态判别：只有「未下载 ↓」才给落点 ==")

# A1 下载前 ↓（细白环 + 白箭头）⇒ 该点
im, d = _canvas()
_bubble(im, d)
_ring(d, 225, 260, r=26, glyph="arrow")
cx, cy, why = _find(im, pane_left=0)
ok("A1 下载前 ↓ ⇒ 认出并给落点", cx is not None, why)
if cx is not None:
    ok("A1b 落点误差 ≤ 5px（图标中心 225,260）",
       abs(cx - 225) <= 5 and abs(cy - 260) <= 5, "落点=(%s,%s)" % (cx, cy))
ok("A1c 判为 todo（该点）", "未下载" in why and "该点" in why, why)

# A2 下载中 ⏸（粗灰环 + ⏸）⇒ 不该点
im2, d2 = _canvas()
_bubble(im2, d2)
_ring(d2, 225, 260, r=26, glyph="thick")
cx2, cy2, why2 = _find(im2, pane_left=0)
ok("A2 下载中 ⏸（粗环）⇒ **不给落点**", cx2 is None, "却给了 (%s,%s)" % (cx2, cy2))
ok("A2b 且理由说的是「粗环」/下载中", ("粗环" in why2) or ("busy" in why2), why2)

# A3 下载后 ▶（细白环 + ▶）⇒ 不该点
im3, d3 = _canvas()
_bubble(im3, d3)
_ring(d3, 225, 260, r=26, glyph="play")
cx3, cy3, why3 = _find(im3, pane_left=0)
ok("A3 下载后 ▶ ⇒ **不给落点**", cx3 is None, "却给了 (%s,%s)" % (cx3, cy3))
ok("A3b 且理由说的是 ▶/已下好", ("▶" in why3) or ("已下好" in why3), why3)

print("\n== B. 反例：不许误判 ==")

# B1 纯白矩形条（不是圆，也不在气泡里）
im4, d4 = _canvas()
d4.rectangle([120, 200, 320, 250], fill=(250, 250, 250))
cx4, cy4, why4 = _find(im4, pane_left=0)
ok("B1 无气泡 ⇒ 如实 None", cx4 is None, "却报 (%s,%s) %s" % (cx4, cy4, why4))

# B2 气泡里没有图标（纯深色方块）
im5, d5 = _canvas()
_bubble(im5, d5)
cx5, cy5, why5 = _find(im5, pane_left=0)
ok("B2 气泡里没有图标 ⇒ 如实 None", cx5 is None, "却报 (%s,%s) %s" % (cx5, cy5, why5))
# ⛔ B2b：理由**必须**是"没量到环"，不许是侥幸的"正在下载"——
#   先红后绿实测：把 `thick>=5` 去掉后，没有图标的气泡会量出 r=54、厚 29px 的**大假环**，
#   靠厚判据兜住（结论对、理由错）⇒ 一旦厚判据改动就漏。`max_size` 收到 72 后此条稳定。
ok("B2b 且理由是「没量到环」（不是侥幸的「正在下载」）",
   cy5 is None and ("没量到" in why5), why5)

# B3 空白画面
im6, _d6 = _canvas()
cx6, cy6, why6 = _find(im6, pane_left=0)
ok("B3 空白画面 ⇒ 如实 None + 原因", cx6 is None and bool(why6), "why=%r" % (why6,))

# B4 气泡里的白字（窄条，非环）
im7, d7 = _canvas()
_bubble(im7, d7)
for i in range(6):
    d7.rectangle([150 + i * 20, 255, 150 + i * 20 + 12, 269], fill=(250, 250, 250))
cx7, cy7, why7 = _find(im7, pane_left=0)
ok("B4 气泡里的白字（非环）⇒ 不误判", cx7 is None, "却报 (%s,%s) %s" % (cx7, cy7, why7))

print("\n== C. 多个气泡：取最下面的 ==")

im8, d8 = _canvas(h=700)
_bubble(im8, d8, 120, 40, 330, 300)
_bubble(im8, d8, 120, 380, 330, 660)
_ring(d8, 225, 170, r=26, glyph="play")   # 上面那个：已下好（不点）
_ring(d8, 225, 520, r=26, glyph="arrow")  # 下面那个：未下载（该点）
cx8, cy8, why8 = _find(im8, pane_left=0)
ok("C1 两气泡：取**最下面**那个，且它是 ↓ ⇒ 给出落点（y≈520）",
   cx8 is not None and abs(cy8 - 520) <= 8, "落点=(%s,%s) %s" % (cx8, cy8, why8))

print("\n== D. pane_left 生效（不扫会话列表）==")

im9, d9 = _canvas(w=520, h=520)
# ⛔ 两侧气泡都必须 ≥180px 宽（检测器下限）；pane_left=210 把左边整块排除
_bubble(im9, d9, 10, 60, 200, 470)      # 在 pane_left 左侧（应被排除）——190px 宽
_bubble(im9, d9, 230, 60, 430, 470)     # 右侧（应命中）——200px 宽
_ring(d9, 105, 260, r=26, glyph="arrow")   # 左侧的（排除）
_ring(d9, 330, 200, r=26, glyph="arrow")   # 右侧的（命中）
cx9, cy9, why9 = _find(im9, pane_left=210)
ok("D1 pane_left 左侧的气泡被排除、只认右侧",
   cx9 is not None and abs(cx9 - 330) <= 8, "落点=(%s,%s) %s" % (cx9, cy9, why9))

print("\n== E. 接线：_media_bubble_locate 优先用图标、失败退回几何 ==")

import ast  # noqa: E402
_src = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
_tree = ast.parse(_src)
_found = {"loc": False, "icon_first": False, "fallback": False}
for _n in ast.walk(_tree):
    if isinstance(_n, ast.FunctionDef) and _n.name == "_media_bubble_locate":
        _found["loc"] = True
        _seg = ast.get_source_segment(_src, _n) or ""
        _found["icon_first"] = "_find_video_dl_icon" in _seg
        _found["fallback"] = ("头像" in _seg) or ("_avatar_blocks" in _seg)
ok("E1 `_media_bubble_locate` 存在", _found["loc"], "")
ok("E2 它内部调用图标定位 `_find_video_dl_icon`（先试新的）",
   _found["icon_first"], "没有接线 ⇒ 图标法白写")
ok("E3 老几何法保留（图标没命中时退回）——零回归",
   _found["fallback"], "老路径被删 ⇒ 有回归风险")

print("\n== F. 真截屏冒烟核对（桌面上的三态图，**只读**；没有就跳过）==")

_REAL = [
    (r"%USERPROFILE%\Desktop\ScreenShot_2026-10-02_114546_464.png", "下载前↓", "todo"),
    (r"%USERPROFILE%\Desktop\ScreenShot_2026-10-02_114556_964.png", "下载中⏸", "busy"),
    (r"%USERPROFILE%\Desktop\ScreenShot_2026-10-02_114610_019.png", "下载后▶", "done"),
]
_have = all(os.path.exists(p) for p, _, _ in _REAL)
if not _have:
    print("  SKIP 桌面三态截图不全 ⇒ 跳过冒烟核对（判据仍以合成样本为准）")
else:
    for _p, _lbl, _want in _REAL:
        try:
            _im = Image.open(_p).convert("RGB")
            _cx, _cy, _why = _find(_im, pane_left=464)
            if _want == "todo":
                ok("F %s ⇒ 给出落点（该点）" % _lbl, _cx is not None, _why)
                if _cx is not None:
                    ok("F %s 落点 ≈ (672,794)" % _lbl,
                       abs(_cx - 672) <= 12 and abs(_cy - 794) <= 12, "落点=(%s,%s)" % (_cx, _cy))
            else:
                ok("F %s ⇒ **不给落点**（不点）" % _lbl, _cx is None, "却给了 (%s,%s)" % (_cx, _cy))
        except Exception as _e:
            ok("F %s 读取异常" % _lbl, False, str(_e))

print("\n== 视频下载图标判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
sys.exit(1 if FAIL[0] else 0)
