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
from PySide6.QtGui import QColor
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

    def _restyle(self) -> None:
        t = self.t
        # 面板底 = card 实底（whale 玻璃下 card 是半透明 → 垫一层 bg 保可读，
        # 同 _Combo 下拉列表的做法：whale 用深蓝实底，不透出内容层）
        base = t.card if not t.glass else "#0E2136"
        self.setStyleSheet(
            f"QFrame#Popover{{background:{base};border:1px solid {t.bd};"
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
