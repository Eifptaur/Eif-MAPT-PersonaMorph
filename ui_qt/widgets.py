# -*- coding: utf-8 -*-
"""Qt 原生控件族 —— 对应 launcher-src 里 `RoundButton` / `RoundBar` / `StepList` 的角色。

设计纪律（照搬 launcher-src/README.md 的硬规矩）：
  1. 外观只有一处来源 —— 所有颜色都从 `Tokens` 拿，控件内部**不许**出现硬编码色值。
  2. 按钮的"主按钮身份"必须在覆盖背景色**之前**判定（RoundButton 踩过的坑）。
  3. 文字截断必须为 0 —— 宁可换行/加宽，不许出现省略号（自检项）。

本文件负责：按钮、状态徽章、卡片、分组导航树、开关、输入行。
"""

from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from stylekit_qt import Tokens, mix, qfont, rgba, status_colors


# ---------------------------------------------------------------- 基础卡片


class Card(QFrame):
    """设计系统里的"卡片"。

    whale 走**玻璃**（半透明白叠在深海底 + 发光边），light 走**实底 + 1px 描边**，
    dark 走**实底 + 1px 描边 + 无阴影**（近黑底上阴影看不见，是错的装饰）。
    ⇒ 这正是"三套主题差异要在设计思路层面拉开"的落点，不是换几个色值。
    """

    def __init__(self, t: Tokens, pad: int = 18, parent: QWidget | None = None):
        super().__init__(parent)
        self.t = t
        self._pad = pad
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(pad, pad, pad, pad)
        lay.setSpacing(12)
        self.body = lay

        if t.glass:
            # 玻璃：靠中性描边 + 外发光，不用阴影（阴影在深色海面上会脏）
            # ⚠️ 截图（whale-1）反馈：原来的 --bd 只有 .16 透明度，卡片边几乎看不见
            #    ⇒ 层级全靠"发光"撑，远看是一片糊。这里**在玻璃形态内部**把描边提亮一档，
            #      并把发光收一点 —— 让"边"负责划界、"光"只负责气质。
            #      注意：这是**在 whale 这一套主题内部**的修正，不是三套趋同。
            edge = rgba(t.q("blue"), 52).name(QColor.NameFormat.HexArgb)
            self.setStyleSheet(
                f"#Card{{background:{t.card};border:1px solid {edge};"
                f"border-radius:{t.radius_card}px;}}"
            )
            self._glow = QGraphicsDropShadowEffect(self)
            self._glow.setBlurRadius(22)
            self._glow.setOffset(0, 5)
            self._glow.setColor(rgba(t.q("blue"), 26))
            self.setGraphicsEffect(self._glow)
        else:
            self.setStyleSheet(
                f"#Card{{background:{t.card};border:{t.card_border}px solid {t.bd};"
                f"border-radius:{t.radius_card}px;}}"
            )
            if t.key != "dark":
                sh = QGraphicsDropShadowEffect(self)
                sh.setBlurRadius(10)
                sh.setOffset(0, 1)
                sh.setColor(QColor(0, 0, 0, 18))
                self.setGraphicsEffect(sh)
            # dark：刻意不加阴影 —— 近黑底上的阴影等于噪声


# ---------------------------------------------------------------- 主按钮


class Btn(QPushButton):
    """`RoundButton` 的 Qt 版。

    三种角色：primary（强调色填充）/ ghost（描边）/ danger（危险红，二次确认用）。
    主按钮身份在构造时就定死（`role`），不靠"背景色是不是强调色"反推 ——
    C# 那边是靠 `Restyle` 事后判定的，Qt 侧有构造参数就不必再绕。
    """

    def __init__(
        self,
        text: str,
        t: Tokens,
        role: str = "ghost",
        parent: QWidget | None = None,
    ):
        super().__init__(text, parent)
        self.t = t
        self.role = role
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(qfont(t, t.body_size, 500 if role != "primary" else 600))
        self.setMinimumHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(self._qss())
        if role == "primary" and t.glass:
            g = QGraphicsDropShadowEffect(self)
            g.setBlurRadius(18)
            g.setOffset(0, 3)
            g.setColor(rgba(t.q("blue"), 90))
            self.setGraphicsEffect(g)

    def _qss(self) -> str:
        """四态（常态/hover/pressed/disabled），对齐 web 侧按钮手感规格（console_html.py L532-557）：

        · hover  → 浮起（主按钮换强调色底 / ghost 染描边+染字 / danger 加深红底）
        · pressed→ **按进去**：QSS 没有 transform，用 padding 上下 +1px 把文字压下去 1px，
                   底色再走一档（web: translateY(1px) scale(.975) 的 Qt 等价物）
        · danger 字色用 err_tx（亮色档）—— dark 上拿主 err 当字色会沉进背景（用户实报的对比度缺陷）
        """
        t, r = self.t, self.role
        rad = t.radius_btn
        if r == "primary":
            bg, bg_h = t.blue, t.blue2
            fg = "#0A1B2E" if t.key == "whale" else "#FFFFFF"
            bd = "transparent"
            bg_p = t.blue2
        elif r == "danger":
            # ⚠️ rgba() 返回的是 QColor 对象 —— 直接插进 QSS f-string 会变成
            #    "background:<PySide6.QtGui.QColor object at 0x…>" 垃圾值，
            #    整条 QPushButton 规则解析失败 → 回落默认样式（黑字沉底，
            #    这才是用户实报"黑色跟深色背景混在一起"的真根因，2026-09-23 取证实锤）。
            #    QSS 只认字符串 ⇒ 一律 .name(HexArgb) 落成 #AARRGGBB。
            fg = (getattr(t, "err_tx", "") or t.err)
            bg = rgba(t.q("err"), 20).name(QColor.NameFormat.HexArgb)
            bg_h = rgba(t.q("err"), 34).name(QColor.NameFormat.HexArgb)
            bg_p = rgba(t.q("err"), 46).name(QColor.NameFormat.HexArgb)
            bd = rgba(t.q("err"), 110).name(QColor.NameFormat.HexArgb)
        else:  # ghost —— web: hover 染描边+染字+hover-bg；active 落 blue_soft
            bg = mix(t.q("bg"), t.q("tx"), 0.05).name(QColor.NameFormat.HexArgb)
            bg_h = mix(t.q("bg"), t.q("tx"), 0.10).name(QColor.NameFormat.HexArgb)
            fg = t.tx
            bd = t.bd
            bg_p = t.blue_soft
        hover_extra = ""
        pressed_extra = ""
        if r == "ghost":
            hover_extra = f"color:{t.blue};border:1px solid {t.blue};"
            pressed_extra = f"color:{t.blue};"
        return (
            f"QPushButton{{background:{bg};color:{fg};border:1px solid {bd};"
            f"border-radius:{rad}px;padding:7px 16px;}}"
            f"QPushButton:hover{{background:{bg_h};{hover_extra}}}"
            f"QPushButton:pressed{{background:{bg_p};"
            f"padding-top:8px;padding-bottom:6px;{pressed_extra}}}"
            f"QPushButton:disabled{{color:{t.tx3};background:transparent;}}"
        )


# ---------------------------------------------------------------- 状态徽章


class Badge(QLabel):
    """状态徽章 —— web 侧 `.st` 的原生版。

    六态口径（**这是原型要验的第一条可用性纪律**）：
      ok「已就绪」/ warn「待处理」/ err「没连上」/ info「在跑」/ idle「读取中」
    拿不到数据时**必须**落 idle，**不许**默认写 ok ——
    否则用户会以为"一切正常"，而这正是当初 `ERR_CONNECTION_REFUSED` 最难查的原因。
    """

    def __init__(self, t: Tokens, level: str = "idle", text: str = "读取中", parent: QWidget | None = None):
        super().__init__(parent)
        self.t = t
        self.setFont(qfont(t, 11.5, 500))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(22)
        self.set(level, text)

    def set(self, level: str, text: str, tip: str = "") -> None:
        bg, bd, fg = status_colors(self.t, level)
        self.setText(text)
        self.setToolTip(tip or text)
        self.setStyleSheet(
            f"QLabel{{background:{bg.name(QColor.NameFormat.HexArgb)};"
            f"color:{fg.name()};border:1px solid {bd.name(QColor.NameFormat.HexArgb)};"
            f"border-radius:{self.t.radius_pill}px;padding:2px 11px;}}"
        )
        self.adjustSize()
        self.setFixedHeight(22)


# ---------------------------------------------------------------- 导航树


class NavGroup(QWidget):
    """导航分组 —— 左上角那棵树的"一组"。

    对应 web 侧新加的 `.nav-grp`（组头 + 可折叠体）。
    组头形态对齐 web：**组名在左、折叠 chevron 在右**（`.nav-grp-hd` space-between）。
    chevron 用 icons.CHEVRON（与 web 同一条 path），折叠时旋转 -90° 朝右。
    平台差异（Qt 红利）：折叠用 `QPropertyAnimation` 做真实高度动画，
    不需要 web 侧那套 `max-height` 估算 + 滚动条重算。
    """

    toggled = Signal(bool)

    def __init__(self, t: Tokens, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.t = t
        self.title = title
        self.collapsed = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        self.hd = QFrame()
        self.hd.setCursor(Qt.CursorShape.PointingHandCursor)
        hl = QHBoxLayout(self.hd)
        hl.setContentsMargins(10, 9, 10, 5)
        hl.setSpacing(6)
        self.hd_lb = QLabel(title)
        self.hd_lb.setFont(qfont(t, t.grp_size, 600, t.grp_spacing))
        self.hd_lb.setStyleSheet(f"color:{t.tx3};background:transparent;")
        hl.addWidget(self.hd_lb)
        hl.addStretch(1)
        import icons as _icons  # noqa: PLC0415（本地模块，避免顶层循环依赖）

        self._icons = _icons
        self.gc = QLabel()
        self.gc.setFixedSize(12, 12)
        self.gc.setStyleSheet("background:transparent;")
        self.gc.setPixmap(_icons.chevron_pixmap(t.tx3, collapsed=False))
        self.gc.setScaledContents(True)
        hl.addWidget(self.gc)
        self.hd.mousePressEvent = lambda _e: self.toggle()  # 整行可点，对齐 web 的 button.hd

        root.addWidget(self.hd)

        self.body = QWidget()
        self.bl = QVBoxLayout(self.body)
        self.bl.setContentsMargins(0, 0, 0, 6)
        self.bl.setSpacing(1)
        root.addWidget(self.body)

    def add(self, w: QWidget) -> None:
        self.bl.addWidget(w)

    def toggle(self) -> None:
        self.collapsed = not self.collapsed
        self.body.setVisible(not self.collapsed)
        self.gc.setPixmap(
            self._icons.chevron_pixmap(self.t.tx3, collapsed=self.collapsed)
        )
        self.toggled.emit(self.collapsed)


class NavItem(QPushButton):
    """导航项 —— 支持图标 + "当前选中"高亮（左侧 2px 指示条，对齐 web 侧 `.nav a.on`）。

    图标来自 icons.py（与 web 同一份 SVG path），颜色跟状态走：
    常态 tx2 / 选中与悬停 = 高亮字色（web 侧 currentColor 的等价物）。
    """

    def __init__(
        self,
        t: Tokens,
        text: str,
        hint: str = "",
        icon_key: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(text, parent)
        self.t = t
        self.hint = hint
        self.icon_key = icon_key
        self.active = False
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(False)
        self.setFixedHeight(30)
        from PySide6.QtCore import QSize  # noqa: PLC0415

        self.setIconSize(QSize(16, 16))
        if hint:
            self.setToolTip(hint)
        self._restyle()

    # -- 图标着色（web currentColor 等价）--

    def _icon_color(self) -> str:
        t = self.t
        if self.active:
            return t.tx if t.key == "whale" else t.blue
        return t.tx if self._hover else t.tx2

    def _refresh_icon(self) -> None:
        import icons as _icons  # noqa: PLC0415

        if self.icon_key:
            self.setIcon(_icons.nav_icon(self.icon_key, self._icon_color(), 16))

    def set_active(self, on: bool) -> None:
        self.active = on
        self._restyle()

    def enterEvent(self, e):  # noqa: N802
        self._hover = True
        self._refresh_icon()
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        self._hover = False
        self._refresh_icon()
        super().leaveEvent(e)

    def _restyle(self) -> None:
        t = self.t
        self._refresh_icon()
        if self.active:
            bg = t.blue_soft if t.key != "whale" else rgba(t.q("blue"), 30)
            fg = t.blue if t.key != "whale" else t.tx
            self.setFont(qfont(t, t.nav_size, t.nav_active_weight))
            self.setStyleSheet(
                f"QPushButton{{background:{bg if isinstance(bg,str) else bg.name(QColor.NameFormat.HexArgb)};"
                f"color:{fg};border:none;border-left:2px solid {t.blue};"
                f"border-radius:0 {t.radius_btn}px {t.radius_btn}px 0;"
                f"text-align:left;padding:0 10px 0 12px;}}"
            )
        else:
            self.setFont(qfont(t, t.nav_size, t.nav_weight))
            self.setStyleSheet(
                f"QPushButton{{background:transparent;color:{t.tx2};"
                f"border:none;border-left:2px solid transparent;"
                f"border-radius:0 {t.radius_btn}px {t.radius_btn}px 0;"
                f"text-align:left;padding:0 10px 0 12px;}}"
                f"QPushButton:hover{{background:{rgba(t.q('tx'), 0 if t.glass else 14).name(QColor.NameFormat.HexArgb)};color:{t.tx};}}"
            )


# ---------------------------------------------------------------- 设置行


class Field(QWidget):
    """一张设置卡里的一行 —— 左标签（+说明），右侧控件。

    可用性要点（原任务里点名要的三条之一）：
      · 每行有**一句话说明**，不用术语（"不改后端，改完立刻生效"）
      · 右侧控件永远有**可见的当前值**，不存在"点了没反应"
    """

    def __init__(
        self,
        t: Tokens,
        label: str,
        desc: str = "",
        control: QWidget | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.t = t
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(2)
        self.lb = QLabel(label)
        self.lb.setFont(qfont(t, t.body_size, 500))
        self.lb.setStyleSheet(f"color:{t.tx};background:transparent;")
        left.addWidget(self.lb)
        if desc:
            d = QLabel(desc)
            d.setFont(qfont(t, t.body_size - 1.5))
            d.setStyleSheet(f"color:{t.tx3};background:transparent;")
            d.setWordWrap(True)
            left.addWidget(d)
        root.addLayout(left, 1)
        if control is not None:
            root.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


class Switch(QCheckBox):
    """开关 —— 自绘，对齐 web 侧的"改完即生效"手感（无系统控件皮肤）。"""

    def __init__(self, t: Tokens, on: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.t = t
        self.setChecked(on)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 22)
        self.stateChanged.connect(lambda _: self.update())

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self.t
        track = QRectF(0, 1, 40, 20)
        path = QPainterPath()
        path.addRoundedRect(track, 10, 10)
        if self.isChecked():
            p.fillPath(path, t.q("blue"))
            p.setBrush(QColor("#0A1B2E") if t.key == "whale" else QColor("#FFFFFF"))
            knob_x = 21
        else:
            p.fillPath(path, mix(t.q("bg"), t.q("tx"), 0.14 if t.glass else 0.12))
            p.setBrush(t.q("tx2"))
            knob_x = 3
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(knob_x, 4, 14, 14))
        p.end()


class SearchBox(QLineEdit):
    """导航搜索框 —— 对齐 web 侧新加的 `#navFind`（'找功能…'，3 秒定位）。"""

    def __init__(self, t: Tokens, parent: QWidget | None = None):
        super().__init__(parent)
        self.t = t
        self.setPlaceholderText("找功能…（如：发消息 / 换模型）")
        self.setFont(qfont(t, t.body_size))
        self.setFixedHeight(32)
        self.setClearButtonEnabled(True)
        self.setStyleSheet(
            f"QLineEdit{{background:{rgba(t.q('tx'), 0 if t.glass else 16).name(QColor.NameFormat.HexArgb)};"
            f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;"
            f"padding:0 10px;}}"
            f"QLineEdit:focus{{border:1px solid {t.blue};}}"
        )


def h2(t: Tokens, text: str, badge: Badge | None = None) -> QWidget:
    """面板头 —— 标题 + 状态徽章同一行（对齐 web 侧 `.sec-hd`）。

    ⚠️ 玻璃卡坑（2026-09-23 四变体对照实锤，见 _probe_bar2.py）：
       whale 卡挂了 QGraphicsDropShadowEffect，整棵卡子树走**离屏合成**；
       裸 QWidget 包裹层在合成路径下会把底下那一条（自身高度 19px）
       的半透明卡底挤掉、露出纯海底 —— 肉眼即用户报的
       「深色槽直接横贯整个屏幕」。横条色 RGB(10,27,46) = 海底 #0A1B2E，
       正常卡底 RGB(24,40,58) = 0.055 白叠海底，像素分段扫描证实。
       显式声明 WA_StyledBackground + 透明底后回归正常（h2styled 变体验证）。
    """
    w = QWidget()
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    w.setStyleSheet("background:transparent;")
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)
    lb = QLabel(text)
    lb.setFont(qfont(t, t.h2_size, t.h2_weight, display=True))
    lb.setStyleSheet(f"color:{t.tx};background:transparent;")
    lay.addWidget(lb)
    if badge is not None:
        lay.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
    lay.addStretch(1)
    return w


def desc(t: Tokens, text: str) -> QLabel:
    """一句话说明 —— 面板头下面那行（web 侧 `.desc`）。"""
    lb = QLabel(text)
    lb.setFont(qfont(t, t.body_size - 0.5))
    lb.setStyleSheet(f"color:{t.tx3};background:transparent;")
    lb.setWordWrap(True)
    return lb


# ---------------------------------------------------------------- 顶栏两件


class WhaleBadge(QLabel):
    """顶栏鲸鱼徽章 —— 复刻 web 侧 `.whale-badge`：52×52 圆角 13 **近黑实心底**（#14161a），
    真图 `assets/icon-whale.png` 36×36 @ (left 8 / bottom 6)（几何已锚定，别再手画鲸鱼）。

    用户观察（2026-09-23）：「控制台左上角的图标不对，把那只黑底鲸鱼找出来，直接用上就行」。
    ⚠️ 银灰白鲸图**必须配近黑底** —— 浅底上会糊成一片（MEMORY 已记，light 稿踩过）。

    交互（web 控制台既有彩蛋的真值复刻，真源 agent/console_html.py 4907-5214，实现全在 whale_anim.py）：
      · 悬停果冻     —— 7 帧逐帧弹跳（QPropertyAnimation 驱动 jelly_step，帧序列=JELLY_POSES）
      · 按下拖动     —— 本体变淡到 0.12，浮层克隆跟手（速度拉伸 + 挣扎摆尾，QTimer 驱动衰减）
      · 松手三选一   —— 蠕动 / 纸飞机 / 扎地，种子化随机（seed 参数 → 测试可复现）
      · 入框回弹     —— 140ms 超弹 + 7 颗气泡；本体过冲 (1.12, 0.9) 后归位
    ⚠️ 本体永远留在顶栏布局原槽：拖动与返回全由浮层承担（挂在 Shell 窗口层）。
    """

    def __init__(self, parent: QWidget | None = None, seed: int | None = None):
        super().__init__(parent)
        self.setFixedSize(52, 52)
        from pathlib import Path  # noqa: PLC0415

        root = Path(__file__).resolve().parents[1]  # ui_qt → 项目根（落位自 _scratch/qt_proto，层级浅一级）
        img = root / "assets" / "icon-whale.png"
        self._pm = QPixmap(str(img)) if img.exists() else QPixmap()

        # ── 交互状态（web 闭包的对应物；动画实现都在 whale_anim.py，本类只存帧与接线）──
        self._seed = seed
        self._an = None                  # WhaleAnimator，懒建：首次按下才挂浮层
        self._pose = (1.0, 1.0)          # hero 当前 (sy, sx) —— 果冻/回弹帧
        self._hero_op = 1.0              # 按下后变淡到 0.12
        self._jstep = 6                  # 果冻当前帧索引（末帧 = 归位）
        self._dragging = False
        self._jelly_anim = None
        self._drag_timer = QTimer(self)
        self._drag_timer.setInterval(30)
        self._drag_timer.timeout.connect(self._on_drag_tick)
        self._home_timer = QTimer(self)  # 回弹后 120ms 归位（web setTimeout 同款）
        self._home_timer.setSingleShot(True)
        self._home_timer.setInterval(120)
        self._home_timer.timeout.connect(lambda: self._set_jstep(6))
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    # -- 悬停果冻：QPropertyAnimation 逐帧驱动 jelly_step（7 帧真值见 whale_anim.JELLY_POSES）--
    def _get_jstep(self) -> int:
        return self._jstep

    def _set_jstep(self, v: int) -> None:
        from whale_anim import JELLY_POSES  # noqa: PLC0415

        self._jstep = max(0, min(len(JELLY_POSES) - 1, int(v)))
        self._pose = JELLY_POSES[self._jstep]
        self.update()

    jelly_step = Property(int, _get_jstep, _set_jstep)

    def _start_jelly(self) -> None:
        """web: mouseenter → jelly(0)，播完自动归位；拖拽/飞行中不播（web: if(!idleJelly||FLY) return）。"""
        if self._dragging or self._busy():
            return
        if self._jelly_anim is not None:
            if self._jelly_anim.state() == QPropertyAnimation.State.Running:
                return
            self._jelly_anim.deleteLater()
        from whale_anim import JELLY_KEYFRAMES, JELLY_TOTAL_MS  # noqa: PLC0415

        anim = QPropertyAnimation(self, b"jelly_step", self)
        anim.setDuration(JELLY_TOTAL_MS)
        for frac, step in zip(JELLY_KEYFRAMES, range(len(JELLY_KEYFRAMES))):
            anim.setKeyValueAt(frac, step)
        self._jelly_anim = anim
        anim.start()

    def _busy(self) -> bool:
        return self._an is not None and self._an.active

    def _animator(self):
        if self._an is None:
            from whale_anim import WhaleAnimator  # noqa: PLC0415

            t = getattr(self.window(), "t", None)    # Shell 带 Tokens —— 气泡色随主题蓝
            self._an = WhaleAnimator(self, seed=self._seed,
                                     bubble_color=(t.blue if t is not None else "#6FCFFF"))
        return self._an

    # -- 拖拽（测试/取证也走这三个入口；参数均为窗口局部坐标）--
    def drag_begin(self, pos) -> bool:
        if self._busy():
            return False
        from whale_anim import HERO_OPACITY, JELLY_FRAMES  # noqa: PLC0415

        self._dragging = True
        if self._jelly_anim is not None and \
                self._jelly_anim.state() == QPropertyAnimation.State.Running:
            self._jelly_anim.stop()
        self._set_jstep(JELLY_FRAMES - 1)    # 果冻归位（web: transition none + transform 清空）
        self._hero_op = HERO_OPACITY
        self.update()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self._animator().begin_drag(pos, self._pm)
        self._drag_timer.start()
        return True

    def drag_move(self, pos) -> None:
        if self._dragging:
            self._an.move_drag(pos)

    def drag_end(self, mode: int | None = None) -> "int | None":  # noqa: F821
        if not self._dragging:
            return None
        self._dragging = False
        self._drag_timer.stop()
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        return self._an.end_drag(mode)       # mode 由种子化 rng 三选一

    def _on_drag_tick(self) -> None:
        if self._dragging and self._an is not None:
            self._an.tick_drag()             # 挣扎摆尾：幅度随按住时长衰减（QTimer 驱动）

    # -- mouse 事件 → 拖拽入口（Qt 按下即隐式抓鼠，窗口内出界照收 move）--
    def mousePressEvent(self, e):  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton or not self.drag_begin(
                self.mapTo(self.window(), e.position().toPoint())):
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):  # noqa: N802
        if not self.drag_move(self.mapTo(self.window(), e.position().toPoint())):
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self.drag_end() is None:
            super().mouseReleaseEvent(e)

    def enterEvent(self, e):  # noqa: N802
        self._start_jelly()
        super().enterEvent(e)

    # -- WhaleAnimator 回调 --
    def on_return_pop(self) -> None:
        """finishReturn：本体恢复可见 + 过冲回弹（web hero scaleY(1.12) scaleX(0.9)，120ms 后清）。"""
        self._hero_op = 1.0
        self._pose = (1.12, 0.9)
        self.update()
        self._home_timer.start()

    def on_drag_cancelled(self) -> None:
        self._hero_op = 1.0
        self._set_jstep(6)
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, 52, 52), 13, 13)
        p.fillPath(path, QColor("#14161a"))
        if self._pm.isNull():
            return
        # hero：36×36 @ (8,10)，transform-origin bottom-center —— 果冻/回弹只作用在鲸图上
        p.save()
        p.setOpacity(self._hero_op)
        p.translate(26, 46)
        sy, sx = self._pose
        p.scale(sx, sy)
        p.drawPixmap(-18, -36, 36, 36, self._pm)
        p.restore()


class Segmented(QWidget):
    """滑槽式分段切换器 —— 主题 / 文案风格两轴共用的切换形态。

    用户观察（2026-09-23）：「这个切换太方了，这个滑槽做一下设计」⇒
    原来三个并排 ghost 方按钮换成一整条带**滑动指示块**的分段控件：
    点击选项 → 指示块 160ms ease-out 滑过去（对齐 web 微交互时长纪律 150-200ms）。
    构造期直接定位（不动画）⇒ 离屏取证永远拍到落定状态，不拍半路帧。
    """

    changed = Signal(str)

    def __init__(
        self,
        t: Tokens,
        options: list[tuple[str, str]],
        value: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.t = t
        self.options = options
        self._value = value or (options[0][0] if options else "")
        self._keys = [k for k, _lb in options]
        self._buttons: list[QPushButton] = []
        self._anim = None

        from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect  # noqa: PLC0415
        from PySide6.QtGui import QFontMetrics  # noqa: PLC0415

        fm = QFontMetrics(qfont(t, 12.5, 500))
        self._w = max(56, max(fm.horizontalAdvance(lb) for _k, lb in options) + 30)
        self.setFixedHeight(28)
        self.setFixedWidth(self._w * len(options) + 4)
        self.setObjectName("Seg")

        self._knob = QWidget(self)
        self._knob.setObjectName("SegKnob")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        for key, label in options:
            b = QPushButton(label, self)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedWidth(self._w)
            b.setFlat(True)
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            self._buttons.append(b)
            lay.addWidget(b)

        self._QRect = QRect
        self._place_knob(instant=True)
        self._restyle()

    # -- 布局 --

    def _target(self) -> "QRect":  # noqa: F821
        idx = self._keys.index(self._value) if self._value in self._keys else 0
        return self._QRect(2 + idx * self._w, 2, self._w, 24)

    def _place_knob(self, instant: bool = False) -> None:
        r = self._target()
        if instant:
            self._knob.setGeometry(r)
            return
        from PySide6.QtCore import QEasingCurve, QPropertyAnimation  # noqa: PLC0415

        if self._anim is None:
            self._anim = QPropertyAnimation(self._knob, b"geometry", self)
            self._anim.setDuration(160)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.stop()
        self._anim.setStartValue(self._knob.geometry())
        self._anim.setEndValue(r)
        self._anim.start()

    # -- 状态 --

    def _pick(self, key: str) -> None:
        if key == self._value:
            return
        self._value = key
        self._place_knob()
        self._restyle()
        self.changed.emit(key)

    def set_value(self, key: str) -> None:
        """编程式设值（切主题重建时回填），不发 changed。"""
        self._value = key
        self._place_knob(instant=True)
        self._restyle()

    def value(self) -> str:
        return self._value

    def _restyle(self) -> None:
        t = self.t
        if t.glass:
            knob_bg = "rgba(255,255,255,0.13)"
            knob_bd = "rgba(255,255,255,0.10)"
        elif t.key == "light":
            knob_bg = "#FFFFFF"
            knob_bd = "#DADDE1"
        else:
            knob_bg = "#2A2C31"
            knob_bd = "#3A3D44"
        on = t.blue
        off = t.tx2
        self.setStyleSheet(
            f"QWidget#Seg{{background:{mix(t.q('bg'), t.q('tx'), 0.08).name(QColor.NameFormat.HexArgb)};"
            f"border:1px solid {t.bd};border-radius:{t.radius_btn}px;}}"
            f"QWidget#SegKnob{{background:{knob_bg};border:1px solid {knob_bd};"
            f"border-radius:{max(2, t.radius_btn - 2)}px;}}"
            "QPushButton{background:transparent;border:none;color:" + off + ";padding:0;}"
            f"QPushButton:hover{{color:{t.tx};}}"
        )
        for b, (key, _lb) in zip(self._buttons, self.options):
            b.setFont(qfont(t, 12.5, 600 if key == self._value else 500))
            b.setStyleSheet(
                "QPushButton{background:transparent;border:none;color:"
                + (on if key == self._value else off)
                + ";padding:0;}"
                f"QPushButton:hover{{color:{t.tx};}}"
            )
