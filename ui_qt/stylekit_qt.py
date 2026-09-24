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
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase


# ---------------------------------------------------------------- 颜色工具


_RGBA_RE = re.compile(
    r"^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*([\d.]+)\s*(%?)\s*\)$"
)


def _c(hexstr: str) -> QColor:
    """`#RRGGBB` / `#RRGGBBAA` / `rgba(...)` 统一 QColor。

    坑⑥（2026-09-23 实锤）：QColor 不认 CSS 的 rgba(r,g,b,a) 串
    （valid=False，后续 .name() 静默出 #000000 纯黑）——
    「读取中」徽章深底黑字、whale 导航图标发灰，根子都是它。
    而 QSS 原串路径里 rgba() 能被 Qt 样式表自己解析，只有走 QColor
    的消费点会炸，症状"看地方发病"。
    在这里手动拆 rgba()，token 保持与 web CSS 变量同音的原文，两侧消费都安全。
    """
    s = hexstr.strip()
    if s.startswith("#") and len(s) == 9:
        # #RRGGBBAA → Qt 的 #AARRGGBB 不吃，手动拆
        r, g, b, a = (int(s[i : i + 2], 16) for i in (1, 3, 5, 7))
        return QColor(r, g, b, a)
    m = _RGBA_RE.match(s)
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        a = float(m.group(4))
        if m.group(5):  # "78%" 百分比写法
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
    # 丙-31：web --blue-line（console_html.py L32/68/95 三主题各值）——侧栏状态框描边、
    # chips 描边等「蓝系弱描边」专用。此前 Qt 缺这个 token，shell 拿 blue 当边框 ⇒
    # 状态框一圈亮蓝，作者批「丑陋、绿字」的复刻走样其一。
    blue_line: str

    # 语义（状态徽章六态里用到的四态）
    ok: str
    warn: str
    err: str
    info: str

    # 结构
    radius_card: int
    radius_btn: int
    radius_pill: int
    card_border: int          # 卡片描边宽度（whale 0 / light 1 / dark 1）
    shadow: str               # QSS 不支持 box-shadow，这里只做记账，实际用 QGraphicsDropShadowEffect

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
    letter_extra: float       # 整窗字距微调（dark 走负值更"紧"）

    # 主题专属外观开关
    glass: bool = False       # whale：玻璃质感（半透明卡片 + 发光）
    glow: str = ""            # whale：强调色外发光
    scrollbar_alpha: int = 60
    # ⚠️ err_tx ≠ err：危险按钮/错误**文字**用的亮色档（web 侧 --err-tx，三主题各有其值）。
    #   2026-09-23 实锤：dark 上拿主 err 色(#E5484D)当字色，深红沉进深底 ⇒ 用户看到"黑色跟背景混在一起"。
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
    # 2026-09-23（丙-4 视觉本体）：对齐 web 真值 —— 画卷（ocean1.jpg+tint）透出后，
    # 玻璃卡必须用 web 的浅蓝调才读得对（console_html.py L35 --card / L36 --bd）。
    # 旧值 rgba(255,255,255,.055) 在纯深底上够用，垫在实拍海浪上会发灰。
    # 丙-8 I：波浪动效砍掉后卡片仍偏透（用户原话「稍微提高一点点」）→ .10 → .14
    card="rgba(150,206,255,0.14)",          # web --card:rgba(150,206,255,.10) 的 Qt 增档
    bd="rgba(170,215,255,0.26)",            # web --bd:rgba(170,215,255,.26)
    tx="#E8F3FF",
    tx2="rgba(200,224,245,0.78)",
    tx3="rgba(169,209,236,0.72)",
    blue="#6FCFFF",
    blue2="#3FA9E8",
    blue_soft="rgba(111,207,255,0.14)",
    blue_line="rgba(120,190,255,0.32)",     # web --blue-line（默认深色主题）
    ok="#5FE0A8",
    warn="#FFC773",
    err="#FF8A8A",
    err_tx="#FFB0B0",   # web --err-tx：危险按钮字色（比主 err 亮一档，深底可读）
    info="#6FCFFF",
    radius_card=18,   # 控制台 .card{border-radius:18px}
    radius_btn=10,
    radius_pill=999,
    card_border=0,
    shadow="0 8px 24px rgba(0,10,25,0.45)",
    font_family="Microsoft YaHei UI",
    h2_size=15,       # 控制台 .card h2{font-size:15px}
    h2_weight=600,
    grp_size=11,
    grp_spacing=0.10,
    body_size=14,     # 控制台 body{font:14px/1.6}
    body_weight=400,
    nav_size=14,      # whale 未覆盖 nav 字号，继承 body 14
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
    tx2="#565A5F",   # 丙-8 G：#65676B 真机图标/次文字看不清 → 加深一档（对比度达标）
    tx3="#5F6368",   # 丙-8 G：组名/弱文字 #8A8D91 太浅 → 加深到 Google 灰档
    blue="#0866FF",
    blue2="#0455D6",
    blue_soft="#E7F0FF",
    blue_line="#D6E4FF",                    # web --blue-line（light）
    ok="#00A36C",
    warn="#E5A100",
    err="#E41E3F",
    err_tx="#B3122C",   # web --err-tx（light）
    info="#0866FF",
    radius_card=12,   # 控制台 light --radius-card:12px
    radius_btn=8,
    radius_pill=999,
    card_border=1,
    shadow="0 1px 2px rgba(0,0,0,0.06)",
    font_family="Microsoft YaHei UI",
    h2_size=15,       # 控制台 .card h2{font-size:15px}（light 未覆盖）
    h2_weight=600,
    grp_size=11,
    grp_spacing=0.02,
    body_size=14,     # 控制台 body{font:14px}（light 未覆盖）
    body_weight=400,
    nav_size=14,      # 控制台 light .nav a{font-size:14px}
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
    blue_line="#262A44",                    # web --blue-line（dark/Linear）
    ok="#4CC38A",
    warn="#D9A054",
    err="#E5484D",
    err_tx="#F08A8A",   # web --err-tx（dark）：亮粉，深底可读（主 err 太暗会沉进背景）
    info="#8B93FF",
    radius_card=10,   # 控制台 dark --radius-card:10px
    radius_btn=7,
    radius_pill=999,
    card_border=1,
    shadow="none",
    font_family="Microsoft YaHei UI",
    h2_size=15,       # 控制台 .card h2{font-size:15px}（dark 未覆盖）
    h2_weight=600,
    grp_size=10,
    grp_spacing=0.09,
    body_size=14,     # 控制台 body{font:14px}（dark 未覆盖）
    body_weight=400,
    nav_size=14,      # 控制台 dark .nav a{13.5px}；字号档位表无 13.5，取 14
    nav_weight=400,
    nav_active_weight=500,
    letter_extra=0.1,
    glass=False,
    scrollbar_alpha=64,
)

THEMES: dict[str, Tokens] = {"whale": WHALE, "light": LIGHT, "dark": DARK}


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
    else:  # idle
        c = t.q("tx2")
    bg = rgba(c, 26 if not t.glass else 34)
    bd = rgba(c, 70 if not t.glass else 90)
    return bg, bd, c


# ---------------------------------------------------------------- 字体


_FONT_SIZES = {8, 9, 10, 10.5, 11, 11.5, 12, 13, 14, 15, 16, 17, 18, 20, 22, 24}


# ---- 项目双字体（2026-09-23 用户拍板：大字=朝華標題A，小字=屏显臻宋）----
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


def ensure_fonts() -> tuple[str, str, str]:
    """注册项目双字体 + emoji 兜底，返回 (display, body, emoji) 三个 family 名。

    ⚠️ 必须在 QApplication 之后调用（`available_ui_families()` 记的硬崩坑同族）。
    幂等：注册过直接返回。没建 app / 找不到文件时返回空串，调用方回退 t.font_family。
    """
    global _FONTS_READY, _DISPLAY_FAMILY, _BODY_FAMILY, _EMOJI_FAMILY
    if _FONTS_READY:
        return _DISPLAY_FAMILY, _BODY_FAMILY, _EMOJI_FAMILY
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    if QApplication.instance() is None:
        return "", "", ""

    def _fam_of(p: Path) -> str:
        if not p.exists():
            return ""
        try:
            fid = QFontDatabase.addApplicationFont(str(p))
        except Exception:
            return ""
        if fid < 0:
            return ""
        fams = QFontDatabase.applicationFontFamilies(fid)
        return fams[0] if fams else ""

    _DISPLAY_FAMILY = _fam_of(_FONT_DISPLAY_FILE)
    _BODY_FAMILY = _fam_of(_FONT_BODY_FILE)
    _EMOJI_FAMILY = _fam_of(_FONT_EMOJI_FILE)
    _FONTS_READY = True
    return _DISPLAY_FAMILY, _BODY_FAMILY, _EMOJI_FAMILY


def qfont(t: Tokens, size: float, weight: int = 400, extra_spacing: float | None = None,
          display: bool = False) -> QFont:
    """造一个字体。Qt 的 QFont 不认 10.5 这类半点字号（会取整），这里显式保留。

    display=True → 标题字体（朝華標題A，用户拍板"大字用前者"）；
    默认 → 正文字体（屏显臻宋，"小字用后者"）。没注册成功时回退 t.font_family。
    回退链第二位挂 emoji 字体 —— 🐋 这类字形正文里没有，Qt 逐字形回退去取。
    """
    if not _FONTS_READY:
        ensure_fonts()  # 没注册过就试一次；app 未建时优雅返回空，走回退
    primary = (_DISPLAY_FAMILY if display else _BODY_FAMILY) or t.font_family
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

    ⚠️ **必须先有 QApplication**。实测（2026-09-22）：在任何 QApplication 之前
       调 `QFontDatabase.families()` 会让进程**硬崩且无 traceback**
       （rc=127、stdout/stderr 双空，看起来像"命令找不到"）。
       ⇒ 这个坑在 GUI 里会让"字体降级"这条逻辑整个不生效，而且在无 GUI 的
         CI/sandbox 里表现为"脚本神秘消失"。必须在这里挡住，不能靠调用方自觉。
    """
    try:
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415
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
