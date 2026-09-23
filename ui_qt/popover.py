# -*- coding: utf-8 -*-
"""顶栏下滑 Popover —— 丙-7 #14/#15 的面板容器。

用户点单（原话归纳）：「点一下『有新版本可用』，它从顶栏上面滑下来，铺展开来
变成一个弹窗」。web 真值里 updBar 本是顶栏下方独立一行（console_html.py L760），
丙-6 把它塞进 60px 顶栏 → 三按钮挤成墨块（用户截图实锤）。Qt 侧改为：
顶栏只留胶囊/图标，点开从锚点下方滑出面板。

实现选择（为什么是 Qt.Popup 而不是手写事件过滤器）：
  · Qt.Popup 自带「点外收回」「Esc 收回」「打开时抓鼠」三件事，
    正是工单要的收回语义，不自己再写一遍全局事件过滤。
  · 动画 220ms OutCubic 下滑 + 淡入（对齐工单规格；web 微交互时长纪律
    150-200ms 的近亲，弹层比切换器略长属正常档）。**可打断**：重入时先
    stop 旧动画再起新的，绝不叠两层透明度（丙-5 #9 同款教训）。
  · 面板颜色全部走 Tokens（P0 规则 2：禁硬编码色值）。
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPoint, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from stylekit_qt import Tokens, rgba

DUR_MS = 220          # 工单规格：220ms OutCubic
DROP_PX = 10          # 下滑行程
GAP_PX = 6            # 与锚点的间距


class Popover(QFrame):
    """从锚点下方滑出的浮层面板。内容由调用方塞进 self.body 布局。"""

    def __init__(self, t: Tokens, parent: QWidget | None = None):
        super().__init__(parent)
        self.t = t
        self.setObjectName("Popover")
        # Qt.Popup：点外/Esc 自动收回（工单收回语义三件套的两个白拿）；
        # Frameless + 半透明底 + 置顶：面板浮在内容之上、不带系统边框。
        self.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._anim: QPropertyAnimation | None = None
        self._op_anim: QPropertyAnimation | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)   # 描边画在最外层 QSS，内容再留白
        self.body = QVBoxLayout()
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(8)
        lay.addLayout(self.body)
        self._restyle()

    # ------------------------------------------------------------ 样式

    def paintEvent(self, _e) -> None:  # noqa: N802
        """丙-8 H（用户原话「它们俩都全透明，会和下面的字混在一起」）：
        底色**自绘**，不再依赖 QSS——QFrame#Popover 的 QSS background 在
        WA_TranslucentBackground + Qt.Popup 组合下真机被吃掉（探针
        _c8_popprobe.py 像素实锤：三主题中心 alpha=0）。画法＝圆角 path 内
        先填 bg 再叠 card（半透明 token）→ 与页面卡片完全同观感、纯 token
        派生；圆角外**不画**（保持真透明，四角不露方形底角）。"""
        from PySide6.QtGui import QPainterPath  # noqa: PLC0415
        from PySide6.QtCore import QRectF  # noqa: PLC0415

        t = self.t
        r = t.radius_card
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), r, r)
        p.setPen(QPen(t.q("bd"), 1))
        p.setBrush(t.q("bg"))
        p.drawPath(path)                     # 底：bg 实色（whale=深海底）
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(t.q("card"))              # 叠：card 半透明 → 与页面卡片同观感
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5), r - 1.5, r - 1.5)
        p.end()

    def _restyle(self) -> None:
        t = self.t
        # 底色改由 paintEvent 自绘（见上）；QSS 只管子控件文字与描边外的杂项
        self.setStyleSheet(
            f"QFrame#Popover{{border:1px solid {t.bd};"
            f"border-radius:{t.radius_card}px;}}"
            f"QFrame#Popover QLabel{{color:{t.tx};background:transparent;}}"
        )

    def relayout(self) -> None:
        """主题切换后重刷 token（Shell._rebuild 重建整个顶栏时 Popover 随之重建，
        这里主要服务取证脚本手工换主题的场景）。"""
        self._restyle()

    # ------------------------------------------------------------ 打开/收回

    def toggle_at(self, anchor: QWidget, width: int = 0, align: str = "left") -> None:
        """开 ⇄ 关。开 = 定位到锚点下方并播 220ms 下滑动画。"""
        if self.isVisible():
            self.close()          # Qt.Popup：再点锚点（或点外）→ 收回
            return
        if width:
            self.setFixedWidth(width)
        self.adjustSize()
        g = anchor.mapToGlobal(anchor.rect().bottomLeft())
        x = g.x()
        if align == "right":
            x = g.x() + anchor.width() - self.width()
        # 屏幕内夹取：面板不许出屏（顶栏靠右时左弹，靠左时右弹）
        screen = anchor.screen()
        if screen is not None:
            geo = screen.availableGeometry()
            x = max(geo.left() + 8, min(x, geo.right() - self.width() - 8))
        y = g.y() + GAP_PX
        self.move(x, y - DROP_PX)          # 起始位：终点上方 DROP_PX（下滑进入）
        self.setWindowOpacity(0.0)
        self.show()
        # 可打断动画：重入先停旧（丙-5 #9 教训 —— 绝不叠两层透明度）
        for an in (self._anim, self._op_anim):
            if an is not None:
                an.stop()
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(DUR_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(self.pos() + QPoint(0, DROP_PX))
        self._op_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._op_anim.setDuration(DUR_MS)
        self._op_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._op_anim.setStartValue(0.0)
        self._op_anim.setEndValue(1.0)
        self._anim.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)
        self._op_anim.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def open_at(self, anchor: QWidget, width: int = 0, align: str = "left") -> None:
        """只开不关（已开则重新定位，不闪）。"""
        if self.isVisible():
            return
        self.toggle_at(anchor, width, align)

    def add(self, w: QWidget) -> None:
        self.body.addWidget(w)

    def add_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.body.addLayout(row)
        return row
