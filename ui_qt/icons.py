# -*- coding: utf-8 -*-
"""导航图标 —— **与 web 控制台同一份 path 数据**（agent/console_html.py L939-985 抄录）。

单一来源纪律：web 侧的每个导航项都配了一枚 16×16 描边 SVG（stroke="currentColor"），
Qt 侧用 QtSvg 渲染**同一份 path**，渲染前把 currentColor 替换成目标色 ——
同一份数据在任意主题/状态下重着色，不手画、不复刻第二份。
web 侧改图标 ⇒ 改这里同一条 path（反过来也一样）。

现在在预览图里面完全没有看到」 ⇒ 本文件 + widgets.NavItem/NavGroup 接线补齐。
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap, QTransform
from PySide6.QtSvg import QSvgRenderer

#: 16×16 视框下的图标内联 SVG（key = 导航 sec 锚点名，与 shell.NAV 的 sec 一致）
INNER: dict[str, str] = {
    # ── 日常 ──
    "overview": '<circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M8 8l3.1-2.2" stroke="currentColor" stroke-width="1.4" fill="none"/>',
    "wechat": '<path d="M6.2 3.2c-2.6 0-4.7 1.7-4.7 3.9 0 1.2.6 2.3 1.7 3l-.4 1.6 1.8-.9c.5.1 1 .2 1.6.2" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M9.9 6.6c-2.2 0-4 1.5-4 3.4 0 1.9 1.8 3.4 4 3.4.4 0 .9-.1 1.3-.2l1.5.8-.3-1.4c.9-.6 1.5-1.5 1.5-2.6 0-1.9-1.8-3.4-4-3.4z" fill="none" stroke="currentColor" stroke-width="1.3"/>',
    "bot": '<rect x="3.2" y="5" width="9.6" height="7.4" rx="2.2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M8 3v2" stroke="currentColor" stroke-width="1.4"/><circle cx="6.2" cy="8.6" r=".9" fill="currentColor"/><circle cx="9.8" cy="8.6" r=".9" fill="currentColor"/>',
    "persona": '<circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="6" cy="7" r=".9" fill="currentColor"/><circle cx="10" cy="7" r=".9" fill="currentColor"/><path d="M5.6 10.2c1.4 1.1 3.4 1.1 4.8 0" fill="none" stroke="currentColor" stroke-width="1.3"/>',
    "check": '<circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M5 8.2l2.1 2.1L11 6" fill="none" stroke="currentColor" stroke-width="1.5"/>',
    # ── 智能 ──
    "model": '<rect x="4" y="4" width="8" height="8" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M6.5 1.6v2.4M9.5 1.6v2.4M6.5 12v2.4M9.5 12v2.4M1.6 6.5h2.4M1.6 9.5h2.4M12 6.5h2.4M12 9.5h2.4" stroke="currentColor" stroke-width="1.3" fill="none"/>',
    "memory": '<path d="M4 2.4h8v11.2L8 11.4l-4 2.2z" fill="none" stroke="currentColor" stroke-width="1.4"/>',
    "memory-set": '<circle cx="3.6" cy="8" r="1.8" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="12.4" cy="4" r="1.8" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="12.4" cy="12" r="1.8" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M5.2 7.2l5.6-2.4M5.2 8.8l5.6 2.4" stroke="currentColor" stroke-width="1.3" fill="none"/>',
    "search": '<circle cx="7" cy="7" r="4.4" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M10.4 10.4L14 14" stroke="currentColor" stroke-width="1.5" fill="none"/>',
    "community": '<circle cx="5" cy="6" r="2" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="11" cy="6" r="2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M1.6 12.4c.5-1.8 1.9-2.8 3.4-2.8s2.9 1 3.4 2.8M8.6 9.9c.6-.2 1.2-.3 1.8-.3 1.5 0 2.9 1 3.4 2.8" fill="none" stroke="currentColor" stroke-width="1.3"/>',
    # ── 内容 ──
    "media": '<rect x="2" y="3" width="12" height="10" rx="1.8" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="5.6" cy="6.4" r="1.3" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M2.6 11.4l3.4-3 2.7 2.4 2.2-1.9 2.5 2.5" fill="none" stroke="currentColor" stroke-width="1.3"/>',
    "tts": '<path d="M3 6.4v3.2M6 4.2v7.6M9 2.8v10.4M12 5.4v5.2" stroke="currentColor" stroke-width="1.4" fill="none"/>',
    "imggen": '<rect x="2" y="3" width="12" height="10" rx="1.8" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M4.4 10.8l2.6-2.4 2 1.8 1.6-1.4 2.4 2.2" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M11.2 4.4l.6 1.4 1.4.6-1.4.6-.6 1.4-.6-1.4-1.4-.6 1.4-.6z" fill="none" stroke="currentColor" stroke-width="1.1"/>',
    "videogen": '<rect x="2" y="3.4" width="9.2" height="9.2" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M11.2 7.2l2.8-1.8v5.2l-2.8-1.8z" fill="none" stroke="currentColor" stroke-width="1.4"/>',
    "tools": '<rect x="3" y="3" width="7" height="7" rx="1.4" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M10 6.4h1.6a1.6 1.6 0 010 3.2H10" fill="none" stroke="currentColor" stroke-width="1.4"/><rect x="6" y="10" width="7" height="3.4" rx="1.4" fill="none" stroke="currentColor" stroke-width="1.4"/>',
    "poke": '<circle cx="8" cy="7" r="2.4" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M2.6 12.6c.7-2.4 2.9-3.6 5.4-3.6s4.7 1.2 5.4 3.6" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="12.6" cy="3.4" r="1.2" fill="currentColor"/>',
    # ── 运行 ──
    "sessions": '<path d="M3 4.5h10M3 8h10M3 11.5h10" stroke="currentColor" stroke-width="1.4" fill="none"/><circle cx="1.5" cy="4.5" r=".9" fill="currentColor"/><circle cx="1.5" cy="8" r=".9" fill="currentColor"/><circle cx="1.5" cy="11.5" r=".9" fill="currentColor"/>',
    "feedback": '<path d="M2.4 3.6h11.2v7.2H7.2L4.2 13.4V10.8H2.4z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M5.2 6.2h5.6M5.2 8.4h3.6" stroke="currentColor" stroke-width="1.3" fill="none"/>',
    "send": '<path d="M14 2L2 7.4l4.2 1.6L13 4l-4.8 6.6.6 3.4z" fill="none" stroke="currentColor" stroke-width="1.4"/>',
    "log": '<path d="M4 2h5.6L13 5.4V14H4z" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M9.4 2v3.6H13" fill="none" stroke="currentColor" stroke-width="1.2"/>',
    "vermat": '<path d="M8 1.8l5.4 2.7v6.9L8 14.2 2.6 11.4V4.5z" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M2.8 4.6L8 7.3l5.2-2.7M8 7.3v6.8" fill="none" stroke="currentColor" stroke-width="1.2"/>',
    "server": '<rect x="2.4" y="3" width="11.2" height="4.2" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.4"/><rect x="2.4" y="8.8" width="11.2" height="4.2" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="4.8" cy="5.1" r=".8" fill="currentColor"/><circle cx="4.8" cy="10.9" r=".8" fill="currentColor"/>',
    "advanced": '<path d="M2 5h12M2 11h12" stroke="currentColor" stroke-width="1.4" fill="none"/><circle cx="6" cy="5" r="1.9" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="11" cy="11" r="1.9" fill="none" stroke="currentColor" stroke-width="1.4"/>',
    # ── 外观 ──
    "ui": '<rect x="2" y="3" width="12" height="10" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M6 3v10" stroke="currentColor" stroke-width="1.3" fill="none"/>',
    "json": '<path d="M6.4 2.6C4.8 2.6 5 4.4 5 5.6s-.6 1.8-1.6 2.4c1 .6 1.6 1.2 1.6 2.4s-.2 3 1.4 3M9.6 2.6c1.6 0 1.4 1.8 1.4 3s.6 1.8 1.6 2.4c-1 .6-1.6 1.2-1.6 2.4s.2 3-1.4 3" fill="none" stroke="currentColor" stroke-width="1.4"/>',
    "cursor": '<path d="M4 2l8.2 6.1-3.4.5 2 3.6-1.8 1-2-3.7L4.6 12z" fill="none" stroke="currentColor" stroke-width="1.4"/>',
    "wavefx": '<path d="M1.6 9.2c1.6-3.2 3.2-3.2 4.8 0s3.2 3.2 4.8 0 3.2-3.2 4.8 0" fill="none" stroke="currentColor" stroke-width="1.4"/>',
    # 人设收藏星标（描边=未收藏 / 实心=已收藏）——项目字体渲染 ★ 是黑块（真机实锤）
    "star": '<path d="M8 1.9l1.9 3.9 4.3.6-3.1 3 .7 4.2L8 11.6l-3.8 2 .7-4.2-3.1-3 4.3-.6z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>',
    "star-filled": '<path d="M8 1.9l1.9 3.9 4.3.6-3.1 3 .7 4.2L8 11.6l-3.8 2 .7-4.2-3.1-3 4.3-.6z" fill="currentColor"/>',
}

#: 组头折叠 chevron（web 侧 .nav-grp-hd .gc 同一条 path）
CHEVRON = '<path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>'


#: 外观切换图标：三条横向调节滑杆 + 圆点钮（中条圆点偏右
#: —— 经典「调节」语义）。涵盖「文案体系 + 主题」= 外观调节。
#: 笔画 1.4 / 圆钮 r=1.5 —— 用户点单「粗细改细一点，现在都混在一起了」：
#: 原 2.0 描边 + r=1.9 圆钮在 20px 档 ≈2.5px 物理，线把钮吃掉、三条黏成一团墨。
#: 1.4 描边（≈1.75px 物理）与侧栏导航图标同档，线与钮之间留出可见缝隙。
APPEARANCE = (
    '<path d="M2.4 4h11.2M2.4 8h11.2M2.4 12h11.2" fill="none" stroke="currentColor" '
    'stroke-width="1.4" stroke-linecap="round"/>'
    '<circle cx="5.4" cy="4" r="1.5" fill="none" stroke="currentColor" stroke-width="1.4"/>'
    '<circle cx="10.6" cy="8" r="1.5" fill="none" stroke="currentColor" stroke-width="1.4"/>'
    '<circle cx="6.8" cy="12" r="1.5" fill="none" stroke="currentColor" stroke-width="1.4"/>'
)


def appearance_pixmap(color: str, size: int = 20) -> QPixmap:
    """顶栏外观切换图标（20px 档）。"""
    return svg_pixmap(APPEARANCE, color, size)


#: 侧栏收起/展开双箭头
CHEVS_R = ('<path d="M4.2 4.5L8 8l-3.8 3.5M9.2 4.5L13 8l-3.8 3.5" fill="none" '
           'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>')
CHEVS_L = ('<path d="M11.8 4.5L8 8l3.8 3.5M6.8 4.5L3 8l3.8 3.5" fill="none" '
           'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>')


def chevs_pixmap(color: str, collapsed: bool = False, size: int = 14) -> QPixmap:
    """侧栏收起/展开双箭头。

    语义=「点了会发生什么」：收起态（窄栏）显示 »（点了=向外展开），
    展开态显示 «（点了=向内收起）。
    """
    return svg_pixmap(CHEVS_R if collapsed else CHEVS_L, color, size)


def _ink(color: str) -> str:
    """任意可解析色值（QSS 的 rgba() 串 / hex / 色名）→ QtSvg 认的写法。

    ⚠️ QtSvg 只认 SVG 色值（#hex / rgb() / 色名），**不认 CSS 的 rgba()**：
    非法 stroke 色退回 none（描边整体消失），非法 fill 色退回黑（实心点变黑点）。
    whale 主题 tx2/tx3 正是 rgba() 串 —— 直接替换会让整套导航图标"隐形"
    。
    半透明色 → hex + 注入 fill-opacity/stroke-opacity（QtSvg 支持）；
    非法输入退 #808080 —— 坏 token 也不能把图标变没。
    """
    c = QColor(color)
    if not c.isValid():
        c = QColor("#808080")
    hexv = c.name(QColor.NameFormat.HexRgb)
    if c.alphaF() >= 0.999:
        return hexv
    a = f"{c.alphaF():.3f}".rstrip("0").rstrip(".")
    return hexv + '" fill-opacity="' + a + '" stroke-opacity="' + a


def svg_pixmap(inner: str, color: str, size: int = 16, dpr: float = 2.0) -> QPixmap:
    """把一份内联 SVG 渲染成透明底 QPixmap；currentColor → color。

    dpr=2 ⇒ 32px 物理位图按 16px 逻辑尺寸显示，高分屏不发虚。
    """
    body = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
        + inner.replace("currentColor", _ink(color))
        + "</svg>"
    )
    r = QSvgRenderer(QByteArray(body.encode("utf-8")))
    img = QImage(int(size * dpr), int(size * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    r.render(p, QRectF(0, 0, size * dpr, size * dpr))
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


def nav_icon(key: str, color: str, size: int = 16) -> QIcon:
    """导航项图标。key 不在表里就返回空 QIcon（缺图标 ≠ 报错，导航照常能用）。"""
    inner = INNER.get(key)
    if not inner:
        return QIcon()
    ic = QIcon()
    ic.addPixmap(svg_pixmap(inner, color, size))
    return ic


def chevron_pixmap(color: str, collapsed: bool = False, size: int = 12) -> QPixmap:
    """组头折叠箭头：展开朝下，折叠转 90° 朝右（web 侧 .gc rotate(-90deg) 同义）。"""
    pm = svg_pixmap(CHEVRON, color, size)
    if collapsed:
        pm = pm.transformed(QTransform().rotate(-90))
    return pm
