# -*- coding: utf-8 -*-
"""判据：**按行底色判"有没有置顶、置顶几行"**（`chat_header.list_rows_by_shade`）。

⛔ 为什么需要它（两条独立的现实理由）：
  ① **DB 判不出置顶**——第二台机复核实测：`sort_ts` 压根不改（真值 9 个置顶只命中 1 个）；
     收起/展开在 5 个字段里**零痕迹**。⇒ 想知道置顶，**只能看屏幕**。
  ② **屏幕顺序 ≠ DB 顺序**——屏幕是「置顶组 ＋ 折叠条 ＋ 非置顶组」，DB 是一条全局时间轴；
     实测最大错位 **70 行 ≈ 6860px** ⇒ 按 DB 行号点会点到别的会话。

⛔ 依据的版式事实（用户给的，真机两条独立吻合）：
  · **无置顶** ⇒ 会话栏底色**均一**（≈238）；
  · **有置顶** ⇒ **搜索行 ＋ 置顶行**一起变深（≈230），与普通行形成对比；
  · 无论怎样都比**侧栏/顶栏（≈224）**浅。
  实测（置顶「E」后的 1091×899 帧）：第1行(搜索行)=230 · 第2行(置顶 E)=230 · 第3行起=238。

⚠️ 本判据**用合成图**测（自己画几条灰带），**不抓屏、不碰任何真窗口**。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))   # `_srcmatch` 在 scripts/ 下（不补这条会 ModuleNotFoundError）
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.environ["PM_JUDGE_NO_GUI"] = "1"          # 第 261 项的闸：本判据不碰微信真 GUI

import _srcmatch as _sm # noqa: E402
from PIL import Image, ImageDraw # noqa: E402
from agent import chat_header as CH # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def _frame(shades: list) -> Image.Image:
    """合成一张"列表帧"：左侧侧栏(224) · 中间列表（按 shades 逐行涂色）· 右侧聊天区(250)。

    `shades` ＝ 列表**第一会话行开始**每行想要的底色；第 1 行（搜索行）自动取 `shades[0]`（同色）。
    """
    W, H = 1000, 900
    im = Image.new("RGB", (W, H), (250, 250, 250))     # 聊天区＝白
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 220, H], fill=(224, 224, 224))  # 侧栏/顶栏＝更深灰（不参与本判据）
    top, row_h = int(H * 0.055), 100
    # ⛔ 要把**整列**（侧栏右沿 → pane_left）涂成该行底色：判定采样的是"列表右侧那条空带"
    #   （`pane_left-110 .. pane_left-6`）⇒ 只涂一小条会让它采到聊天区的白（夹具自己骗自己）。
    for i in range(len(shades) + 1):                   # +1：搜索行 + 各行
        y0 = top + i * row_h
        if y0 >= H:
            break
        c = shades[0] if i == 0 else shades[i - 1]     # 搜索行与"第一个会话行"同色（实测如此）
        d.rectangle([221, y0, 340, min(H, y0 + row_h)], fill=(c, c, c))
    return im


print("\n══ A. 有置顶（搜索行＋置顶行更深）══")
_f1 = _frame([230, 238, 238, 238, 238, 238])   # 第1个会话行=置顶(230)
_r1 = CH.list_rows_by_shade(_f1, pane_left=340, row_h=100)
ok("A1 判出**有置顶**", _r1["has_pinned"] is True, str(_r1["why"])[:70])
ok("A2 置顶行数 = 1（只第一会话行更深）", _r1["pinned_rows"] == 1, str(_r1["rows"]))
ok("A3 首行（搜索行）底色被读到（= 置顶档）", _r1["search_row_bg"] == 230, str(_r1["search_row_bg"]))

_f2 = _frame([230, 230, 230, 238, 238, 238])   # 前3个会话行=置顶
_r2 = CH.list_rows_by_shade(_f2, pane_left=340, row_h=100)
ok("A4 置顶 **3 行** ⇒ 数得出来（置顶块边界＝连续更暗的那些行）", _r2["pinned_rows"] == 3, str(_r2["rows"]))

print("\n══ B. 无置顶（底色均一）══")
_f3 = _frame([238, 238, 238, 238, 238, 238])
_r3 = CH.list_rows_by_shade(_f3, pane_left=340, row_h=100)
ok("B1 判出**无置顶**", _r3["has_pinned"] is False, str(_r3["why"])[:70])
ok("B2 置顶行数 = 0", _r3["pinned_rows"] == 0, str(_r3["rows"]))
ok("B3 普通档 = 238（没把「均一」误判成「更深」）", _r3["normal_bg"] == 238, str(_r3["normal_bg"]))

print("\n══ C. 反例锚（灵敏度）+ 结构性（不许写死像素/阈值）══")
_f4 = _frame([238, 238, 238, 238])          # 全部同一档 ⇒ **任何"更深"的判定都该是 False**
_r4 = CH.list_rows_by_shade(_f4, pane_left=340, row_h=100)
ok("C1 同色 4 行 ⇒ 不许凭空报出置顶（否则判据恒真）",
   _r4["has_pinned"] is False and _r4["pinned_rows"] == 0, str(_r4["why"])[:60])
_src = open(os.path.join(ROOT, "agent", "chat_header.py"), encoding="utf-8").read()
ok("C2 是**结构判据**：门槛由「同帧两簇」现算，不是写死一个亮度值（源码里没有 `== 230`/`== 238` 这类比）",
   _sm.has(_src, "def list_rows_by_shade(") and "==230" not in _src.replace(" ", "") and "==238" not in _src.replace(" ", ""))
ok("C3 行高不写死：默认按帧高现算（`row_h` 可传、缺省自算）",
   _sm.has(_src, "row_h = max(40") or _sm.has(_src, "row_h: int = 0"))
# ⛔ 现场实测值记档（防后人把「实测」当「魔数」再删）：
ok("C4 现场实测口径写在注释里（230/238/224 三个档的来历可追溯）",
   "230" in _src and "238" in _src and "224" in _src)

print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for _x in FAIL:
        print("  - " + _x)
sys.exit(1 if FAIL else 0)
