# -*- coding: utf-8 -*-
"""Qt 原生设计系统 —— 从 launcher-src/stylekit.cs 的 StyleKit 直译。

为什么要这份文件：
  项目已经有一套 C# 原生设计语言（`launcher-src/stylekit.cs`，433 行），
  是 `一键启动.exe` / `一键关闭.exe` 弹窗族的单点来源（README 硬规矩第 3 条：
  "外观只有一处来源"）。做 Qt 原型时**必须沿用同一套 token**，
  否则一启动就会看到"两个软件的拼凑感"。

与 stylekit.cs 的对应关系（逐个 token 一一对得上）：
  Bg        #F7F9FC   ← StyleKit.Bg
  Card      #FFFFFF   ← StyleKit.Card
  Ink       #1A263D   ← StyleKit.Ink
  Sub       #6C7A91   ← StyleKit.Sub
  Accent    #3484F7   ← StyleKit.Accent
  Line      #DEE6F1   ← StyleKit.Line
  Ok        #34965A   ← StyleKit.Ok
  AccentSoft#E8F0FE   ← StyleKit.AccentSoft
  字体      Microsoft YaHei UI  ← StyleKit.Ui()

三主题（与 web 控制台对齐）：
  whale  鲸落 = 深蓝海 + 玻璃质感（默认，沿用现有语言）
  light  浅色 = Meta 界面语言（干净白底、克制分隔、清晰字级）
  dark   深色 = Linear 界面语言（近黑底、细腻描边、高对比低饱和）

⚠️ 本文件是**原型验证件**，不进打包、不进 offline/wheels。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase


# ---------------------------------------------------------------- 颜色工具


_RGBA_RE = re.compile(
    r"^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*([\d.]+)\s*(%?)\s*\)$"
)


def _c(hexstr: str) -> QColor:
    """`#RRGGBB` / `#AARRGGBB` / `rgba(...)` 统一 QColor。

    坑⑥：QColor 不认 CSS 的 rgba(r,g,b,a) 串
    （valid=False，后续 .name() 静默出 #000000 纯黑）——
    「读取中」徽章深底黑字、whale 导航图标发灰，根子都是它。
    在这里手动拆 rgba()，token 保持与 web CSS 变量同音的原文，两侧消费都安全。

    ⛔ 九位十六进制按 **`#AARRGGBB`**（透明度最前）拆 —— 与 Qt 样式表、
    QColor.name(HexArgb) 同一序（ 实测钉死； 的注解曾写反成 RRGGBBAA，
    token 十六进制化之后再过 _c() 会被二次搅乱：「读取中」徽章字色 α 跑到
    245、RGB 变灰蓝，就是这处把 AARRGGBB 的 token 当 RRGGBBAA 读了一轮）。
    """
    s = hexstr.strip()
    if s.startswith("#") and len(s) == 9:
        a, r, g, b = (int(s[i : i + 2], 16) for i in (1, 3, 5, 7))
        return QColor(r, g, b, a)
    m = _RGBA_RE.match(s)
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        a = float(m.group(4))
        if m.group(5): # "78%" 百分比写法
            a /= 100.0
        return QColor(r, g, b, round(a * 255))
    return QColor(s)


def mix(a: QColor, b: QColor, t: float) -> QColor:
    """线性混合，t=0 取 a，t=1 取 b。用于 hover / 半透明叠色。"""
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def rgba(c: QColor, alpha: int) -> QColor:
    o = QColor(c)
    o.setAlpha(alpha)
    return o


def qss(c) -> str:
    """把颜色落成 **Qt 样式表解析器认得的** 字面量。

    ⛔ Qt 样式表的八位十六进制是 **`#AARRGGBB`（透明度在最前）** —— 和 CSS 的
    `#RRGGBBAA` 相反，与 `QColor.name(HexArgb)` 同序。这是 像素实测：
    `border:1px solid #aad7ff42`（本意=淡蓝 #aad7ff、α=26%）被 Qt 读成
    「α=0xaa(67%) + RGB=#d7ff42」⇒ 渲染出 `#dcf77b` 一圈**黄绿描边**、
    半透明次级文字全部泛绿。
    （ 的注解把序写反了，就是那轮引入的污染 —— 本注解以实测证据为准。）

    另外不要把 `rgba(r,g,b,0.26)` 这类 CSS 原文直接插 QSS：小数透明度的解析
    行为不可靠（ 实测过整条规则不生效、回落系统默认皮肤）。统一走本函数
    落成定值十六进制。参数兼顾 QColor 与 `#RRGGBB` / `#AARRGGBB` / `rgba(...)`
    三种来源。
    """
    c = c if isinstance(c, QColor) else _c(str(c))
    if not c.isValid():
        return "transparent"
    if c.alpha() >= 255:
        return "#%02x%02x%02x" % (c.red(), c.green(), c.blue())
    # 八位 = AARRGGBB：alpha 在最前（与 QColor.name(HexArgb) 同序，可直接替代）
    return "#%02x%02x%02x%02x" % (c.alpha(), c.red(), c.green(), c.blue())


def _qssify_tokens() -> None:
    """把 Tokens 上所有颜色字段就地改写成 Qt 合法字面量（幂等）。

    为什么在**工具层**做而不是逐个调用点：token 是与 web CSS 变量同音的原文
    （`rgba(170,215,255,0.26)`），一处对齐、两侧共用是这套设计系统的根基，
    不该为了 Qt 的解析器把 token 本身改丑。而消费侧有 140+ 处把 `{t.bd}` 这类
    直接插进 QSS —— 逐点包一层 `qss()` 既容易漏、又会把调用点淹掉。

    `_c()` 已经能解析全部三种来源，这里只是把结果固化回字符串。所有引用
    `t.xxx` 的地方自动拿到合法值；走 `t.q('xxx')` 的路径本来就回 QColor，不受影响。
    """
    for theme in (WHALE, LIGHT, DARK):
        for f in theme.__dataclass_fields__:
            v = getattr(theme, f, None)
            if not isinstance(v, str):
                continue
            s = v.strip()
            if not (s.startswith("#") or s.startswith("rgb")):
                continue
            col = _c(s)
            if col.isValid():
                setattr(theme, f, qss(col))


# ---------------------------------------------------------------- 主题 token


@dataclass
class Tokens:
    """一套主题的全部设计令牌。

    字段名刻意和 web 控制台的 CSS 变量保持同音（bg/card/bd/tx/tx2/tx3），
    这样"三套主题差异在设计思路层面拉开"这件事在两边的口径是一致的。
    """

    key: str
    label: str

    # 基础面
    bg: str
    card: str
    bd: str
    tx: str
    tx2: str
    tx3: str

    # 强调
    blue: str
    blue2: str
    blue_soft: str
    # web --blue-line（assets/console/index.html L32/68/95 三主题各值）——侧栏状态框描边、
    # chips 描边等「蓝系弱描边」专用。此前 Qt 缺这个 token，shell 拿 blue 当边框 ⇒
    # 状态框一圈亮蓝，的复刻走样其一。
    blue_line: str

    # 语义（状态徽章六态里用到的四态）
    ok: str
    warn: str
    err: str
    info: str

    # 结构
    # 圆角分**五类**，每类对应一种「元件形态」，不再一个值到处复用：
    #   radius_card  —— 卡片/大面板（整块容器）
    #   radius_btn   —— 行级小控件（按钮、下拉、输入行；高度 ~32px）
    #   radius_pill  —— 胶囊（按钮/搜索框/徽章/chip/开关；999 = 全圆头）
    #   radius_field —— **大块输入区/列表/表格**（高度 190~300px）
    #   radius_tile  —— 中块面板片（统计格、分区块的背景，高度 40~90px）
    # 旧版本的病：只有前三个，于是 QPlainTextEdit(300px 高) / QListWidget(220px 高)
    # 全拿 radius_btn=10px —— 10px 圆角摊在 300px 高的框上，视觉上就是个方框
    # 。按高度分档后，每类有自己的形态。
    #
    # 分档定量口径（圆角/高度比 r/h）：
    #   · 行级（h≈32）：r/h ≈ 0.31~0.38 —— 明显圆头，但不到胶囊
    #   · 中块（h≈40~90）：r/h ≈ 0.15~0.3
    #   · 大块（h≈190~300）：**r 必须够大**才有「圆」的观感；实测 r/h < 0.09
    #     时人眼只看成直角框（12px/300px = 0.04）。大块取 22~26px ⇒ r/h ≈ 0.08~0.13，
    #     四角弧线明显、又不至于把 300px 的框啃成椭圆。
    radius_card: int
    radius_btn: int
    radius_pill: int
    radius_field: int
    radius_tile: int
    card_border: int # 卡片描边宽度（whale 0 / light 1 / dark 1）
    shadow: str # QSS 不支持 box-shadow，这里只做记账，实际用 QGraphicsDropShadowEffect

    # 字体级差（三套主题的"语气"差异，靠字号/字距/字重体现）
    font_family: str
    h2_size: int
    h2_weight: int
    grp_size: int
    grp_spacing: float
    body_size: int
    body_weight: int
    nav_size: int
    nav_weight: int
    nav_active_weight: int
    letter_extra: float # 整窗字距微调（dark 走负值更"紧"）

    # 主题专属外观开关
    glass: bool = False # whale：玻璃质感（半透明卡片 + 发光）
    glow: str = "" # whale：强调色外发光
    scrollbar_alpha: int = 60
    # ⚠️ err_tx ≠ err：危险按钮/错误**文字**用的亮色档（web 侧 --err-tx，三主题各有其值）。
    # dark 上拿主 err 色(#E5484D)当字色，深红沉进深底 ⇒ 用户看到"黑色跟背景混在一起"。
    #   （放字段表末尾：dataclass 有默认值的字段后不能再挂无默认字段。）
    err_tx: str = ""

    def q(self, name: str) -> QColor:
        return _c(getattr(self, name))


# ---------------------------------------------------------------- 三套主题

# ── whale 鲸落（默认；沿用现有 web 控制台的设计语言，只做一致性整理）──
# 色相锁在 200~215° 的蓝，靠明度与透明度分层；卡片是"半透明的海面"。
WHALE = Tokens(
    key="whale",
    label="鲸落（默认）",
    bg="#0A1B2E",
    # 对齐 web 真值 —— 画卷（ocean1.jpg+tint）透出后，
    # 玻璃卡必须用 web 的浅蓝调才读得对（assets/console/index.html L35 --card / L36 --bd）。
    # 旧值 rgba(255,255,255,.055) 在纯深底上够用，垫在实拍海浪上会发灰。
    # I：波浪动效砍掉后卡片仍偏透→ .10 → .14
    # R9：.14 在实拍海浪上「和底混一起」⇒ 改**深色毛玻璃**：
    #    底色 66% 不透明 —— 透一点光、又不和底混；白字对比也保得住。
    card="rgba(10,27,46,0.66)",
    bd="rgba(170,215,255,0.26)", # web --bd:rgba(170,215,255,.26)
    tx="#E8F3FF",
    tx2="rgba(200,224,245,0.78)",
    tx3="rgba(169,209,236,0.72)",
    blue="#6FCFFF",
    blue2="#3FA9E8",
    blue_soft="rgba(111,207,255,0.14)",
    blue_line="rgba(120,190,255,0.32)", # web --blue-line（默认深色主题）
    ok="#5FE0A8",
    warn="#FFC773",
    err="#FF8A8A",
    err_tx="#FFB0B0", # web --err-tx：危险按钮字色（比主 err 亮一档，深底可读）
    info="#6FCFFF",
    radius_card=18, # 控制台 .card{border-radius:18px}
    # 按钮走胶囊档：≥ 控件半高 ⇒ 画出来是完整胶囊。
    # ⚠️ radius_btn 是**行级小控件**档（按钮/下拉/输入行，高 ~32px）。
    #    大块容器不再复用它 —— 见 radius_field / radius_tile 两档。
    radius_btn=12, # 12/32 ≈ 0.375，一眼是圆角矩形而非方框（旧值 10 在小钮上仍方正）
    radius_pill=999,
    radius_field=26, # 文本区/列表/表格：160px 高的框上 16px 圆角才看得出「是圆角」
    radius_tile=18, # 统计格/分区块
    card_border=0,
    shadow="0 8px 24px rgba(0,10,25,0.45)",
    font_family="Microsoft YaHei UI",
    h2_size=15, # 控制台 .card h2{font-size:15px}
    h2_weight=600,
    grp_size=11,
    grp_spacing=0.10,
    body_size=14, # 控制台 body{font:14px/1.6}
    body_weight=400,
    nav_size=14, # whale 未覆盖 nav 字号，继承 body 14
    nav_weight=400,
    nav_active_weight=600,
    letter_extra=0.0,
    glass=True,
    glow="rgba(111,207,255,0.35)",
    scrollbar_alpha=52,
)

# ── light 浅色（Meta 界面语言：白底、克制、字级分明）──
# 设计取舍：把"装饰"降到最低 —— 去掉玻璃与发光，用 1px 实线描边 + 轻阴影
# 建立层级；强调色只在真正需要引导视线的地方出现（主按钮 / 选中态 / 徽章）。
LIGHT = Tokens(
    key="light",
    label="浅色 · Meta",
    bg="#F7F8FA",
    card="#FFFFFF",
    bd="#E4E6EB",
    tx="#1C1E21",
    tx2="#565A5F", # G：#65676B 真机图标/次文字看不清 → 加深一档（对比度达标）
    tx3="#5F6368", # G：组名/弱文字 #8A8D91 太浅 → 加深到 Google 灰档
    blue="#0866FF",
    blue2="#0455D6",
    blue_soft="#E7F0FF",
    blue_line="#D6E4FF", # web --blue-line（light）
    ok="#00A36C",
    warn="#E5A100",
    err="#E41E3F",
    err_tx="#B3122C", # web --err-tx（light）
    info="#0866FF",
    radius_card=12, # 控制台 light --radius-card:12px
    radius_btn=10, # 行级小控件（see WHALE 的说明：不再是"通用圆角"）
    radius_pill=999,
    radius_field=22,
    radius_tile=16,
    card_border=1,
    shadow="0 1px 2px rgba(0,0,0,0.06)",
    font_family="Microsoft YaHei UI",
    h2_size=15, # 控制台 .card h2{font-size:15px}（light 未覆盖）
    h2_weight=600,
    grp_size=11,
    grp_spacing=0.02,
    body_size=14, # 控制台 body{font:14px}（light 未覆盖）
    body_weight=400,
    nav_size=14, # 控制台 light .nav a{font-size:14px}
    nav_weight=400,
    nav_active_weight=600,
    letter_extra=0.0,
    glass=False,
    scrollbar_alpha=70,
)

# ── dark 深色（Linear 界面语言：近黑、细描边、高对比低饱和）──
# 设计取舍：底不要纯黑（#08090A 留一点呼吸），层级靠"亮度阶梯 + 1px 描边"
# 而不是阴影（近黑底上阴影看不见）；字号压小、字距拉开做"精密仪器"感。
DARK = Tokens(
    key="dark",
    label="深色 · Linear",
    bg="#08090A",
    card="#101113",
    bd="#1D1F23",
    tx="#F7F8F8",
    tx2="#8A8F98",
    tx3="#5A5E66",
    blue="#8B93FF",
    blue2="#6E78E8",
    blue_soft="rgba(139,147,255,0.12)",
    blue_line="#262A44", # web --blue-line（dark/Linear）
    ok="#4CC38A",
    warn="#D9A054",
    err="#E5484D",
    err_tx="#F08A8A", # web --err-tx（dark）：亮粉，深底可读（主 err 太暗会沉进背景）
    info="#8B93FF",
    radius_card=10, # 控制台 dark --radius-card:10px
    radius_btn=9, # 行级小控件（Linear 语言本身方一点，但不至于像方框）
    radius_pill=999,
    radius_field=18,
    radius_tile=14,
    card_border=1,
    shadow="none",
    font_family="Microsoft YaHei UI",
    h2_size=15, # 控制台 .card h2{font-size:15px}（dark 未覆盖）
    h2_weight=600,
    grp_size=10,
    grp_spacing=0.09,
    body_size=14, # 控制台 body{font:14px}（dark 未覆盖）
    body_weight=400,
    nav_size=14, # 控制台 dark .nav a{13.5px}；字号档位表无 13.5，取 14
    nav_weight=400,
    nav_active_weight=500,
    letter_extra=0.1,
    glass=False,
    scrollbar_alpha=64,
)

THEMES: dict[str, Tokens] = {"whale": WHALE, "light": LIGHT, "dark": DARK}

# token 落成 QSS 合法字面量（见 _qssify_tokens 的说明：一次性覆盖全部 140+ 注入点）
_qssify_tokens()


# ---------------------------------------------------------------- 形状档
#
# 。
# 形状在这里定义成**语义档**，调用点按「这颗按钮是干什么的」选档，
# 而不是按"我想要多圆"随手填数字。
#
# ⚠️ 为什么不能直接写 `radius_pill=999`：Qt 对 `border-radius` 有**硬上限**
#    `min(width, height) / 2`，**超过一个像素就整条值被丢弃**，圆角退回直角
#    （实测：120×80 的控件 rad=40 是圆角、rad=41 与 999 全是方角）。
#    ⇒ 「胶囊」不能靠一个"很大"的数字，只能按**运行时真实尺寸**算。
#
# 各档的几何语义：
#   pill   —— 胶囊 = 完整半圆头，r 取控件半高。搜索框、主/次操作按钮、
#             导航 chip。这是「一类按钮一种形状」里的**主形状**。
#   soft   —— 圆角矩形 r = min(半高, 10)。行内小控件（表格里的操作、
#             数字步进器）——它们贴着单元格，做胶囊会跟表格线打架。
#   tile   —— 方中带圆 r = min(半高, 6)。**工具条图标钮**（顶栏外观、
#             收起侧栏）与开关：需要"可点区域方正、但不刺眼"。
#             顶栏钮实测 8px 圆角被用户看成方框，收到 6 也没救——所以
#             它靠**圆形 hover 底**而不是圆角来表明形状（见 shell.py）。
#   circle —— 正圆（r = 半宽）。纯图标按钮：删除、关闭、展开。
#             正方形元素上用，圆形与旁边的胶囊/圆角矩形一眼可分。

SHAPE_PILL = "pill"
SHAPE_SOFT = "soft"
SHAPE_TILE = "tile"
SHAPE_CIRCLE = "circle"

# 各档的半径上限（胶囊档无上限 —— 它本来就是半高）
_SHAPE_CAP = {SHAPE_SOFT: 10, SHAPE_TILE: 6}


def radius_for(shape: str, w: int, h: int) -> int:
    """按**控件真实尺寸**算出一个 Qt 一定认的半径。

    `w`/`h` 传控件的实际像素尺寸（`QWidget.width()`/`height()`）。尺寸还没
    上版式（<2px）时退回 0，由调用方在 resize 后再刷一次。
    """
    if w < 2 or h < 2:
        return 0
    if shape == SHAPE_CIRCLE:
        # 正圆：直径取**短边**（长边会让圆被画成椭圆/枣核）。再 //2 保不越界。
        return max(0, min(w, h) // 2)
    if shape == SHAPE_PILL:
        # 半高正好是 Qt 的上限，`//2` 保证不越界（越一像素就全丢）。
        # ⚠️ 高必须同时被宽钳住：h 远大于 w 时半高会超过 min(w,h)/2 而被 Qt 丢弃，
        #    胶囊半径退化成 0（画成方框）。
        return max(0, min(h, w) // 2)
    return max(0, min(h // 2, _SHAPE_CAP.get(shape, 10)))


def pill(h: int) -> int:
    """胶囊半径 —— 给定**控件高度**回一个 Qt 一定认的值。

    给那些「尺寸在 QSS 里写死、构造时不持有控件引用」的场景用（输入框、
    分段控件、chip）：`f"border-radius:{pill(32)}px"` ⇒ 16。

    ⚠️ 传进来的必须是控件**真实高度**。传大了（比如习惯性写 999）会被 Qt
    整条丢弃、圆角直接退回直角 —— 这是「一行 `border-radius:999px` 把半个
    界面的按钮全变成方框」的机械原因，不是样式优先级问题。
    """
    return max(0, int(h) // 2)


# ---------------------------------------------------------------- 状态徽章


STATUS_LEVELS = ("ok", "idle", "warn", "err", "info")

# 六态里去掉 idle 走中性灰，其余四态用主题语义色。
# 注意：idle 刻意**不用** ok 色 —— 原型要验证的第一条纪律就是
# "拿不到数据一律写『读取中 / 读不到』，绝不写成『正常』"（web 侧同口径）。
def status_colors(t: Tokens, level: str) -> tuple[QColor, QColor, QColor]:
    """返回 (底色, 描边色, 文字/圆点色)。"""
    if level == "ok":
        c = t.q("ok")
    elif level == "warn":
        c = t.q("warn")
    elif level == "err":
        c = t.q("err")
    elif level == "info":
        c = t.q("info")
    else: # idle
        c = t.q("tx2")
    bg = rgba(c, 26 if not t.glass else 34)
    bd = rgba(c, 70 if not t.glass else 90)
    return bg, bd, c


# ---------------------------------------------------------------- 字体


_FONT_SIZES = {8, 9, 10, 10.5, 11, 11.5, 12, 13, 14, 15, 16, 17, 18, 20, 22, 24}


# ---- 项目双字体----
#
# 两个 TTF 已复制进 assets/fonts/（桌面原件不动，复制非移动）。
#   - 朝華標題A family 名 = ZhaohuaMinA（TTF name 表实探，Qt 探测一致）
#   - 屏显臻宋 name 表完好（"Clear Han Serif" / "屏显臻宋"），但 Qt 探针
#     曾把 family 名报成 '????' ⇒ **不硬编码**，一律从
#     applicationFontFamilies() 动态取 Qt 自己报的名，注册成啥用啥。
#   - Segoe UI Emoji（系统自带）挂回退链第二位，管 🐋 这类 emoji 字形
#     （offscreen 取证环境扫不到系统字体，必须显式注册，同 shoot.py 纪律）。

#   emoji 兜底路径走 WINDIR（兼容性审计·丙8：别假设系统盘是 C:；
#   非标准盘上 _fam_of 返回空串 → 只影响 🐋 字形，不崩 —— 但一行就能不假设）。
_FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
_FONT_DISPLAY_FILE = _FONT_DIR / "ZhaoHuaBiaoTiA.ttf"
_FONT_BODY_FILE = _FONT_DIR / "PingXianZhenSong.ttf"
_FONT_EMOJI_FILE = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "seguiemj.ttf"

_DISPLAY_FAMILY = ""
_BODY_FAMILY = ""
_EMOJI_FAMILY = ""
_FONTS_READY = False

#: 字体文件解不出 family 名时的**替代名**（key = 文件名）。
#:
#: 背景：某些 TTF 的 `name` 表在部分 Qt 构建上解不出（返回 `"????"`），但表里
#: 确有多条 family 记录 —— 包括一条**纯英文**的（pid=3 / eid=1 / lang=0x0409）。
#: Qt 认得这条英文名（它同样是本字体文件的 family），所以拿它当替代名即可让
#: 「字体真的生效」，而不是退到系统字体。**只在这里登记已实测过的对应关系**，
#: 不做猜测性映射 —— 没登记的文件解不出就老实返回空串、走 t.font_family 回退。
_ALT_FAMILY = {
    "PingXianZhenSong.ttf": "Clear Han Serif",
}


def ensure_fonts() -> tuple[str, str, str]:
    """注册项目双字体 + emoji 兜底，返回 (display, body, emoji) 三个 family 名。

    ⚠️ 必须在 QApplication 之后调用（`available_ui_families()` 记的硬崩坑同族）。
    幂等：注册过直接返回。没建 app / 找不到文件时返回空串，调用方回退 t.font_family。
    """
    global _FONTS_READY, _DISPLAY_FAMILY, _BODY_FAMILY, _EMOJI_FAMILY
    if _FONTS_READY:
        return _DISPLAY_FAMILY, _BODY_FAMILY, _EMOJI_FAMILY
    from PySide6.QtWidgets import QApplication # noqa: PLC0415

    if QApplication.instance() is None:
        return "", "", ""

    def _fam_of(p: Path) -> str:
        """从字体文件取一个**可用**的 family 名。

        ⚠️ 不能只取 `applicationFontFamilies(fid)[0]`：字体 name 表里可有多条
        family 记录，Qt 会把**解不出的那条**原样报成 `"????"`。屏显臻宋实测就是
        `families == ['????']`（name 表里 pid=3/eid=1 的 'Clear Han Serif' 与
        lang=0x0804 的 '屏显臻宋' 都在，是 Qt 的解码取不到）。若把 `"????"` 塞进
        QFont，等于要一个不存在的 family ⇒ 全部正文控件静默走系统兜底字体
        （症状：每个 QLabel 的 `font().family()` 都是 `????`；中文靠系统默认字体
        才画得出来，换台机器/换个 offscreen 后端就出现「标签只画第一个字」）。
        自检里 `bool(body_family)` 是真值 ⇒ `????` 也能过，「注册成功」是假绿。

        ⚠️ 取到的名字必须**同进程内可用**：`addApplicationFont` 之后如果发现
        `families` 里只有问号串，就把 **TTF 里真正存在的英文 family**（`Clear Han
        Serif`）拿来做替代 —— 它同样指向这份字体文件，Qt 也认得（见 `name` 表
        pid=3/eid=1/lang=0x0409）。磁盘上解不出任何可用名时返回空串，交调用方
        回退 `t.font_family`（宁可显式降级，也不塞问号串）。
        """
        if not p.exists():
            return ""
        try:
            fid = QFontDatabase.addApplicationFont(str(p))
        except Exception:
            return ""
        if fid < 0:
            return ""
        fams = [f for f in QFontDatabase.applicationFontFamilies(fid) if f]
        for f in fams:
            if "?" not in f:
                return f
        # 全是问号串 ⇒ 用该字体自带的英文 family 替代（同名表，Qt 认）
        alt = _ALT_FAMILY.get(p.name, "")
        return alt
    

    _DISPLAY_FAMILY = _fam_of(_FONT_DISPLAY_FILE)
    _BODY_FAMILY = _fam_of(_FONT_BODY_FILE)
    _EMOJI_FAMILY = _fam_of(_FONT_EMOJI_FILE)
    _FONTS_READY = True
    return _DISPLAY_FAMILY, _BODY_FAMILY, _EMOJI_FAMILY


#: 浮层（弹窗 / 菜单 / 盖在正文之上的卡片）的**实心底色**（玻璃主题用）。
#:
#: 为什么要有这个函数：玻璃主题（whale）下 `t.card` 是 `rgba(10,27,46,0.66)` —— **34% 透**。
#: 页面内的卡片透出来是设计（底下是海面）；但**浮在正文之上的浮层**透出来，就是把下面的字
#: 叠进弹窗里，观感就是「发虚、看不清」（用户实测截图：引导弹窗半透 + 字发虚）。
#: ⇒ 浮层的底色必须**实心**：玻璃主题固定用海底实色 `#0E2136`，其余主题照旧 `t.card`。
#: ⚠️ 这个口径**只此一处**：`confirm._apply_shell` 与 `panels_custom` 的一处各自手抄过同一句话，
#:    结果 `onboarding` 那份漏抄了 —— 浮层就它一个还透着。所以收敛到这里，别再手抄。
_SURFACE_SOLID_GLASS = "#0E2136"


def surface_bg(t: Tokens) -> str:
    """**浮层**底色：玻璃主题给实色（`#0E2136`），其余主题照旧 `t.card`。

    判据是"这块底**盖在别的内容之上**吗"：盖着就要实心（弹窗、菜单、下拉列表、浮层卡片）；
    页面里平铺的卡片不算，那些照旧用 `t.card`（玻璃是设计）。
    """
    return _SURFACE_SOLID_GLASS if getattr(t, "glass", False) else t.card


def qfont(t: Tokens, size: float, weight: int = 400, extra_spacing: float | None = None,
          display: bool = False) -> QFont:
    """造一个字体。Qt 的 QFont 不认 10.5 这类半点字号（会取整），这里显式保留。

    display=True → 标题字体；
    默认 → 正文字体（屏显臻宋，"小字用后者"）。没注册成功时回退 t.font_family。
    回退链第二位挂 emoji 字体 —— 🐋 这类字形正文里没有，Qt 逐字形回退去取。
    """
    if not _FONTS_READY:
        ensure_fonts() # 没注册过就试一次；app 未建时优雅返回空，走回退
    # 标题字体**取不到时退到正文字体**（同为中文字形），而不是直接掉到主题的 generic
    # family —— 少一个字体文件不该让标题换一副面孔。标题字体存在时行为与原来完全一致。
    primary = ((_DISPLAY_FAMILY or _BODY_FAMILY) if display else _BODY_FAMILY) or t.font_family
    chain = [x for x in (primary, _EMOJI_FAMILY, t.font_family) if x]
    f = QFont()
    f.setFamilies(chain or [t.font_family])
    f.setPixelSize(int(round(size)))
    f.setWeight(QFont.Weight(weight) if weight in (100, 200, 300, 400, 500, 600, 700, 800, 900) else QFont.Weight.Normal)
    sp = t.letter_extra if extra_spacing is None else extra_spacing
    if sp:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, sp)
    return f


def apply_font_to_app(app, t: Tokens) -> None:
    """全局兜底字体 —— 只影响没被显式 setFont 的零碎控件（菜单、工具提示）。"""
    if not _FONTS_READY:
        ensure_fonts()
    f = QFont()
    chain = [x for x in (_BODY_FAMILY, _EMOJI_FAMILY, t.font_family) if x]
    f.setFamilies(chain or [t.font_family])
    f.setPixelSize(int(t.body_size))
    app.setFont(f)


def available_ui_families() -> list[str]:
    """本机字体探测 —— 用来验证 'Microsoft YaHei UI' 到底在不在。

    ⚠️ **必须先有 QApplication**。实测：在任何 QApplication 之前
       调 `QFontDatabase.families()` 会让进程**硬崩且无 traceback**
       （rc=127、stdout/stderr 双空，看起来像"命令找不到"）。
       ⇒ 这个坑在 GUI 里会让"字体降级"这条逻辑整个不生效，而且在无 GUI 的
         CI/sandbox 里表现为"脚本神秘消失"。必须在这里挡住，不能靠调用方自觉。
    """
    try:
        from PySide6.QtWidgets import QApplication # noqa: PLC0415
        if QApplication.instance() is None:
            return []
    except Exception:
        return []
    return sorted(QFontDatabase.families())


def resolve_family(want: str, fallbacks: tuple[str, ...] = ("Microsoft YaHei UI", "微软雅黑", "Microsoft YaHei", "Segoe UI", "SimHei")) -> str:
    """挑一个真实存在的 UI 字体。XP 上没有 YaHei UI 时自动降级。"""
    fams = set(available_ui_families())
    if want in fams:
        return want
    for f in fallbacks:
        if f in fams:
            return f
    return want
