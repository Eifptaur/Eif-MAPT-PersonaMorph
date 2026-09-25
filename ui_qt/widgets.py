# -*- coding: utf-8 -*-
"""Qt 原生控件族 —— 对应 launcher-src 里 `RoundButton` / `RoundBar` / `StepList` 的角色。

设计纪律（照搬 launcher-src/README.md 的硬规矩）：
  1. 外观只有一处来源 —— 所有颜色都从 `Tokens` 拿，控件内部**不许**出现硬编码色值。
  2. 按钮的"主按钮身份"必须在覆盖背景色**之前**判定（RoundButton 踩过的坑）。
  3. 文字装不下时的取舍，**按位置分两类**（原「截断必须为 0」的绝对条文已修订）：
     · 「完整优先」位：正文、标题、说明、表单值 —— 必须完整可见，宁可换行或加宽；
     · 「一行共享」位：列表条目摘要、右侧挤着按钮的行、横向 chip 行 —— 换行会撑高
       行（60 字折 5 行 = 190px，列表里只看得见一条）、加宽会挤掉右侧控件，
       所以走 `ElideLabel`（单行 + 尾部省略号 + tooltip 全文）。
     判据一句话：**换行不改变整行高度的，就换行；会改变行高的，就省略号。**

本文件负责：按钮、状态徽章、卡片、分组导航树、开关、输入行。
"""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QBoxLayout,
    QCheckBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from stylekit_qt import (
    SHAPE_CIRCLE,
    SHAPE_PILL,
    SHAPE_SOFT,
    SHAPE_TILE,
    Tokens,
    mix,
    pill,
    qfont,
    qss,
    radius_for,
    rgba,
    status_colors,
)


# ---------------------------------------------------------------- 弹窗拖拽


class DraggableDialog:
    """给无边框弹窗加「按住任意非交互处即可拖动」的混入。

    用户点单：「所有弹窗都要可挪动，按住任意位置（除文字显示区域）都能挪」。

    做法（**不是**在顶栏加一条拖拽带 —— 那样只能拖顶栏，且要改每个弹窗的布局）：
      · 在 dialog 上装事件过滤器，拦截落在**背景**上的按下/移动；
      · 判据是「按到的那个控件是不是"可交互"」——按钮/输入框/下拉/滚动条/列表
        放行（它们有自己的按下语义），纯文字 QLabel 与空白区则拿来拖窗；
      · 位移 > 4px 才真正移动（否则会把单击当拖拽，按钮 hover 感会丢）。

    ⚠️ **MRO 顺序必须是 (DraggableDialog, QDialog)，不能反**：
    PySide6 的 `QObject`/`QWidget` 自带 `eventFilter`（Python 侧可见），
    写成 `(QDialog, DraggableDialog)` 时 MRO 里 QDialog 在前 ⇒ 本类的
    eventFilter **被永久影子化**，一个事件都收不到（本趟实测踩过：
    `_D.eventFilter is QDialog.eventFilter == True`）。用 `_drag_dialog_cls()`
    构造即可避免这个坑。
    """

    #: 拖拽阈值（px）—— 小于它视为点击，不移动窗口
    _DRAG_SLOP = 4

    def enable_drag(self) -> None: # noqa: ANN201
        """在 `self`（一个 QDialog）上挂拖拽。构造末尾调用一次即可。"""
        self._drag_origin = None
        self._drag_win = None
        self._drag_moved = False
        self.installEventFilter(self)
        # 递归给已经建好的子控件也装上（后建的靠 _drag_childInit 兜底没必要 ——
        # Qt 的事件过滤装在 dialog 上，子控件的鼠标事件会**冒泡**到 dialog，
        # 所以只装一次即可；见 _drag_isInteractive 的「是否放行」判据）。
        return self

    # -- 命中的控件是不是"要自己处理鼠标"的？是则放行，不拖窗 --------------
    @staticmethod
    def _drag_isInteractive(w) -> bool: # noqa: ANN001
        from PySide6.QtWidgets import ( # noqa: PLC0415
            QAbstractButton, QAbstractItemView, QAbstractSlider, QComboBox,
            QLineEdit, QScrollBar, QTextEdit,
        )

        if w is None:
            return False
        if isinstance(w, (QAbstractButton, QLineEdit, QComboBox, QAbstractSlider,
                          QAbstractItemView, QScrollBar, QTextEdit)):
            return True
        # 可选中文本的 QLabel：用户要能划选 ⇒ 放行（不吃掉它的选择语义）
        if isinstance(w, QLabel) and (w.textInteractionFlags()
                                      & Qt.TextInteractionFlag.TextSelectableByMouse):
            return True
        return False

    def _drag_target(self, obj): # noqa: ANN001
        """从命中的子控件往上找，直到挂在 dialog 直接子级上 —— 沿路看有没有交互件。"""
        w = obj
        while w is not None and w is not self:
            if self._drag_isInteractive(w):
                return None
            w = w.parent()
        return self if w is self else None

    def _drag_child_at(self, gp): # noqa: ANN001
        """在全局点 `gp` 命中的**最深**子控件（排除自身与滚动条内部件）。

        ⚠️ 为什么不能只信 `_drag_target(obj)` 的 obj：Qt 的鼠标事件会**沿父链
        上抛**——用户在按钮上按下时，`notify()` 会先给按钮、再给 dialog 各发一次，
        过滤器两趟都收得到。第二趟的 `obj is self` ⇒ `_drag_target` 直接判「可拖」
        ⇒ 明明按的是按钮却能把窗拖走。所以必须再问一次「这一点落在谁身上」。
        """
        child = self.childAt(self.mapFromGlobal(gp))
        return child

    def eventFilter(self, obj, ev): # noqa: ANN001, N802
        from PySide6.QtCore import QEvent # noqa: PLC0415

        et = ev.type()
        if et == QEvent.Type.MouseButtonPress:
            # 两重判据（缺一会把「按按钮」也当拖窗，见 _drag_child_at 注解）：
            #   ① 事件上抛到本 dialog 的那一趟（obj is self）也要看命中的子控件；
            #   ② 命中点是交互件 ⇒ 放行，不记 origin。
            gp = ev.globalPosition().toPoint()
            hit = self._drag_child_at(gp)
            if (ev.button() == Qt.MouseButton.LeftButton
                    and self._drag_target(hit) is self
                    and self._drag_target(obj) is self):
                self._drag_origin = gp
                self._drag_win = self.pos()
                self._drag_moved = False
            return False # 不吞事件：让下层照常收到（按钮 hover 等）
        if et == QEvent.Type.MouseMove:
            # Note：这里**不再重判**命中件 —— 拖动过程中指针必然滑出原来那块背景、
            # 压到按钮/输入框上；中途改判会让拖拽一顿一顿（甚至半路停死）。
            # 起点已在 Press 时定死，Move 只认这一趟拖拽。
            if self._drag_origin is not None:
                gp = ev.globalPosition().toPoint()
                d = gp - self._drag_origin
                if not self._drag_moved and (abs(d.x()) + abs(d.y())) < self._DRAG_SLOP:
                    return False
                self._drag_moved = True
                if self._drag_win is not None:
                    self.move(self._drag_win + d)
                return True # 已进入拖拽 ⇒ 吞掉，避免下层误判为划选
        elif et in (QEvent.Type.MouseButtonRelease,
                    QEvent.Type.WindowDeactivate, QEvent.Type.Hide):
            self._drag_origin = None
            self._drag_moved = False
        return False


def drag_dialog_cls(name: str = "DragDialog"):
    """造一个「可拖动的 QDialog」子类，**基类顺序已摆正**。

    ⚠️ 别自己写 `class X(QDialog, DraggableDialog)` —— MRO 会让 QDialog 的
    eventFilter 影子化本混入（实测一个事件都收不到）。这里统一 `(DraggableDialog,
    QDialog)`。用 `type(name, (DraggableDialog, QDialog), {})` 需要 QDialog 已在
    作用域内，故延迟到调用时导入。
    """
    from PySide6.QtWidgets import QDialog # noqa: PLC0415

    return type(name, (DraggableDialog, QDialog), {})


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

    **形状**（`shape`）与**角色**（`role`）正交：角色管颜色，形状管轮廓。
    · pill  （默认）—— 胶囊，完整半圆头。页面级操作、表单提交、弹窗底部的
      「取消/确定」。这是"一类按钮一种形状"里的主形状。
    · soft  —— 小圆角矩形。**挤在行内/表格里**的操作（每行一个的那种），
      贴单元格用胶囊会顶到分隔线，这一档更稳。
    · circle—— 正圆。纯图标按钮（×、‑、+），在正方形上画圆，与旁边一眼可分。
    · tile  —— 方中带圆。开关/工具条小方块。

    ⚠️ 半径**不写死数字**：Qt 对 `border-radius` 有硬上限 `min(w,h)/2`，
      超过一像素整条值被丢弃、圆角退回直角（"所有按钮都是方的"的真凶之一）。
      这里在 resize 时按真实尺寸重算（见 `_sync_radius`）。
    """

    def __init__(
        self,
        text: str,
        t: Tokens,
        role: str = "ghost",
        parent: QWidget | None = None,
        shape: str = "pill",
    ):
        super().__init__(text, parent)
        self.t = t
        self.role = role
        self.shape = shape
        self._rad = 0
        self._fixed_w = 0   # set_button_size 登记的显式尺寸（0 = 未登记，走布局）
        self._fixed_h = 0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(qfont(t, t.body_size, 500 if role != "primary" else 600))
        # 高度：胶囊要 ≥2×半径才画得出半圆头，34 是行级标准高度（与下拉/输入行齐平）
        self.setMinimumHeight(34)
        # ⚠️ 宽度策略关乎**文字会不会被压烂**（用户实报：人设卡四个按钮的字挤成一团）：
        #    · 横向 Minimum ⇒ 允许被父布局压到 minimumSizeHint 以下，文字直接溢出/
        #      叠字。人设卡那排按钮挤在小卡片里就是这样糊掉的。
        #    · Preferred   ⇒ 布局**优先给 sizeHint（= 文字宽 + 内边距）**，压不动
        #      就换行/撑开容器。宁可让容器变大，也不让字看不清。
        #    纵向仍 Fixed：按钮高度不参与拉伸。
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        if text:
            self.setMinimumWidth(self._text_width_hint())
        self._sync_radius()
        if role == "primary" and t.glass:
            g = QGraphicsDropShadowEffect(self)
            g.setBlurRadius(18)
            g.setOffset(0, 3)
            g.setColor(rgba(t.q("blue"), 90))
            self.setGraphicsEffect(g)

    def set_shape(self, shape: str) -> None:
        """换形状并重算半径。

        ⛔ 不许直接赋值 `btn.shape = X` 就完事：`_sync_radius` 只在尺寸变化时
        被触发，改完 shape 半径还是旧档的值（实测漏刷时正圆钮留着一个 240px
        的超界半径 ⇒ 被 Qt 丢弃 ⇒ 又变回方框）。统一走这个入口。
        """
        self.shape = shape
        self._sync_radius()

    def _text_width_hint(self) -> int:
        """文字完整显示所需的最小宽度（= 文字宽 + QSS 的左右 padding 16×2 + 边框 2）。"""
        return self.fontMetrics().horizontalAdvance(self.text()) + 34

    def _sync_radius(self) -> None:
        """按当前真实尺寸重算半径；变了才刷样式表（避免每次 resize 都重解析 QSS）。

        ⛔ **口径前提：调用方必须在控件尺寸是"它真正会显示的大小"时才依赖这个值。**
        `setFixedSize()` 之后、控件尚未上版式之前，`width()`/`height()` 仍是 Qt 的
        默认值（实测 640×480），拿它算出的半径没有意义。

        所以这里取「控件自身尺寸」与「sizeHint」中的**较小者**：上版式后 width()
        是权威值；未上版式时 sizeHint 兜住了那些"按内容定尺寸"的控件。
        **正方形/固定尺寸的钮（圆、胶囊）请用 `set_button_size()`** —— 它会把
        尺寸显式记下来供半径计算，不再依赖布局时序。
        """
        w, h = self.width(), self.height()
        if self._fixed_w and self._fixed_h:
            # 调用方显式指定过尺寸（set_button_size）：以它为准，与布局时序无关
            w, h = self._fixed_w, self._fixed_h
        else:
            sh = self.sizeHint()
            w = min(w, max(sh.width(), self.minimumWidth(), 1))
            h = min(h, max(sh.height(), self.minimumHeight(), 1))
        rad = radius_for(self.shape, w, h)
        if rad != self._rad:
            self._rad = rad
            self.setStyleSheet(self._qss())

    def set_button_size(self, w: int, h: int) -> None:
        """定尺寸按钮的推荐入口（正圆/定宽胶囊）。

        `setFixedSize` 只管布局，不会告诉半径计算"我到底多大" —— 在控件上版式
        之前算半径就必然拿到默认尺寸。这里把尺寸显式登记下来，`_sync_radius`
        直接用它，**与布局时序无关**（圆形钮画成方框的坑就出在时序上）。
        """
        self._fixed_w, self._fixed_h = int(w), int(h)
        self.setFixedSize(int(w), int(h))
        self._sync_radius()

    def resizeEvent(self, ev) -> None: # noqa: N802
        super().resizeEvent(ev)
        self._sync_radius()

    def setText(self, text: str) -> None: # noqa: N802
        super().setText(text)
        if text:
            self.setMinimumWidth(self._text_width_hint())
        self._sync_radius()

    def _qss(self) -> str:
        """四态（常态/hover/pressed/disabled），对齐 web 侧按钮手感规格（console_html.py L532-557）：

        · hover  → 浮起（主按钮换强调色底 / ghost 染描边+染字 / danger 加深红底）
        · pressed→ **按进去**：QSS 没有 transform，用 padding 上下 +1px 把文字压下去 1px，
                   底色再走一档（web: translateY(1px) scale(.975) 的 Qt 等价物）
        · danger 字色用 err_tx（亮色档）—— dark 上拿主 err 当字色会沉进背景（用户实报的对比度缺陷）

        ⚠️ 所有颜色一律过 `qss()` 转成 Qt 字面量，**不能**直接插 `QColor.name(HexArgb)`
        ——那是 `#AARRGGBB`，Qt 的八位是 `#RRGGBBAA`，通道序反了同样让整条规则解析
        失败回落默认皮肤（实锤：ghost 的 pressed 描边曾写成 `#ff93daff`）。
        """
        t, r = self.t, self.role
        # 半径按**真实尺寸**算（见 _sync_radius）—— 不写死数字：Qt 的上限是
        # min(w,h)/2，超一像素整条 border-radius 被丢弃退回直角。
        rad = self._rad or radius_for(self.shape, 120, 34)
        # L：按压态要「一眼可辨」——底色往字色轴压一档（亮主题=变深、
        # 暗主题=提亮一档，都是暗色 UI 的标准按压惯例），描边同步加深。
        # web 侧 translateY(1px) scale(.975) 在 QSS 里没有 transform 等价物，
        # 压字 1px（padding 上+1 下-1）保持，靠底/边双深化补足可辨度。
        press_border = ""
        if r == "primary":
            bg, bg_h = t.blue, t.blue2
            fg = "#0A1B2E" if t.key == "whale" else "#FFFFFF"
            bd = "transparent"
            bg_p = qss(mix(t.q("blue2"), t.q("tx"), 0.22))
            press_border = qss(mix(t.q("blue2"), t.q("tx"), 0.45))
        elif r == "danger":
            # ⚠️ rgba() 返回的是 QColor 对象 —— 直接插进 QSS f-string 会变成
            #    "background:<PySide6.QtGui.QColor object at 0x…>" 垃圾值，
            #    整条 QPushButton 规则解析失败 → 回落默认样式（黑字沉底，
            # 这才是用户实报"黑色跟深色背景混在一起"的真根因，取证实锤）。
            #    QSS 只认字符串 ⇒ 一律 .name(HexArgb) 落成 #AARRGGBB。
            fg = (getattr(t, "err_tx", "") or t.err)
            bg = qss(rgba(t.q("err"), 20))
            bg_h = qss(rgba(t.q("err"), 34))
            bg_p = qss(rgba(t.q("err"), 46))
            bd = qss(rgba(t.q("err"), 110))
            press_border = qss(rgba(t.q("err"), 170))
        else: # ghost —— web: hover 染描边+染字+hover-bg；active 落 blue_soft
            bg = qss(mix(t.q("bg"), t.q("tx"), 0.05))
            bg_h = qss(mix(t.q("bg"), t.q("tx"), 0.10))
            fg = t.tx
            bd = t.bd
            bg_p = t.blue_soft
            press_border = qss(mix(t.q("blue"), t.q("tx"), 0.30))
        hover_extra = ""
        pressed_extra = ""
        if r == "ghost":
            hover_extra = f"color:{t.blue};border:1px solid {t.blue};"
            pressed_extra = f"color:{t.blue};border:1px solid {press_border};"
        elif r == "primary":
            pressed_extra = f"border:1px solid {press_border};"
        else:
            pressed_extra = f"border:1px solid {press_border};"
        return (
            f"QPushButton{{background:{bg};color:{fg};border:1px solid {bd};"
            f"border-radius:{rad}px;padding:7px 16px;}}"
            f"QPushButton:hover{{background:{bg_h};{hover_extra}}}"
            f"QPushButton:pressed{{background:{bg_p};"
            f"padding-top:8px;padding-bottom:6px;{pressed_extra}}}"
            f"QPushButton:disabled{{color:{t.tx3};background:transparent;}}"
        )


# ---------------------------------------------------------------- 流式换行容器


class _FlowLayout(QLayout):
    """一行排不下就自动折到下一行的布局（Qt 官方的 FlowLayout 精简版）。

    `QHBoxLayout` 在一行放不下时的行为是**压缩子控件到最小宽**——按钮文字先被
    压出控件边界、几个 chip 就会叠在一起（实测：`＋ 新建/添加` 压到 `女神异闻录`
    上面）。`QGridLayout` 又要手工算列数。这个布局按「子控件 sizeHint 宽 + 间距」
    累积换行：放不下就开新行，行高取该行最大 sizeHint 高。
    宽度不足时仍会压缩（这是期望行为：优先保证能看见，而不是撑破容器）。
    """

    def __init__(self, parent=None, margin: int = 0, spacing: int = 6): # noqa: ANN001
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(margin, margin, margin, margin)

    # -- QLayout 必需接口 --
    def addItem(self, item) -> None: # noqa: ANN001, N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int): # noqa: ANN201, N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int): # noqa: ANN201, N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self): # noqa: ANN201, N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool: # noqa: ANN201, N802
        return True

    def heightForWidth(self, w: int) -> int: # noqa: ANN201, N802
        return self._do_layout(QRectF(0, 0, w, 0), test_only=True)

    def setGeometry(self, rect) -> None: # noqa: ANN001, N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self): # noqa: ANN201, N802
        return self.minimumSize()

    def minimumSize(self): # noqa: ANN201, N802
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    # -- 核心排布 --
    def _do_layout(self, rect, test_only: bool) -> int: # noqa: ANN001
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = eff.x(), eff.y(), 0
        right = eff.right()
        for it in self._items:
            w = it.sizeHint().width()
            h = it.sizeHint().height()
            if x + w > right and line_h > 0: # 换行
                x = eff.x()
                y += line_h + self._spacing
                line_h = 0
            if not test_only:
                it.setGeometry(QRectF(x, y, w, h).toRect())
            x += w + self._spacing
            line_h = max(line_h, h)
        return y + line_h - rect.y() + m.bottom()


class FlowBox(QWidget):
    """按需换行的控件容器（chip 行、按钮组、标签云的通用底座）。

    用法：`fb = FlowBox(t); fb.add(Btn(...)); fb.add(Btn(...))`；
    重建整行时 `fb.clear()`（会把旧控件 deleteLater）。
    """

    def __init__(self, t: Tokens, spacing: int = 6, parent: QWidget | None = None):
        super().__init__(parent)
        self.t = t
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background:transparent;")
        self._lay = _FlowLayout(self, margin=0, spacing=spacing)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

    def add(self, w: QWidget) -> None: # noqa: ANN201
        self._lay.addWidget(w)
        return w

    def addLayout(self, lay) -> None: # noqa: ANN001
        """把一个（通常只有一两件控件的）子布局整体当成一格加进来。"""
        holder = QWidget()
        holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        holder.setStyleSheet("background:transparent;")
        lay.setContentsMargins(0, 0, 0, 0)
        holder.setLayout(lay)
        self.add(holder)

    def clear(self) -> None: # noqa: ANN201
        while self._lay.count():
            it = self._lay.takeAt(0)
            w = it.widget() if it is not None else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def widgets(self) -> list: # noqa: ANN201
        return [self._lay.itemAt(i).widget() for i in range(self._lay.count())
                if self._lay.itemAt(i) is not None]

    def count(self) -> int:
        return self._lay.count()

    def heightForWidth(self, w: int) -> int: # noqa: ANN201, N802
        return self._lay.heightForWidth(w)

    def hasHeightForWidth(self) -> bool: # noqa: ANN201, N802
        return True


# ---------------------------------------------------------------- 状态徽章


class ElideLabel(QLabel):
    """定宽/可压缩的单行文字标签，装不下时**在尾部加省略号**，并把全文放进 tooltip。

    为什么需要它（与文件头第 3 条纪律的关系）：那条纪律针对的是「**不该**被截的
    正文」，要求换行/加宽。但在**一行里要和别的控件分宽度**的位置（列表条目的摘要、
    右侧挤着按钮的行、分区 chip 行），换行会把行高撑成多行（实测：60 字摘要折成
    5 行 = 190px，一条吃掉整个 220px 列表 ⇒ 用户看到「列表下面全空」），加宽又会
    把右侧按钮挤出视口。这类位置**唯一**成立的解法是单行 + 省略号 + tooltip 兜底。

    · `setFullText(s)`：设全文（tooltip 自动同步），按当前宽度算省略号；
    · 宽度变化时（resizeEvent）自动重算 —— 布局压缩会即时反映，不必手工 refit；
    · `elided()` 返回当前实际显示的省略文本，供自检断言。
    """

    def __init__(self, text: str = "", parent: QWidget | None = None,
                 mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight):
        super().__init__(parent)
        self._full = str(text or "")
        self._mode = mode
        self.setText(self._full)

    def setFullText(self, s: str) -> None: # noqa: N802
        self._full = str(s or "")
        self.setToolTip(self._full)
        self._re_elide()

    def fullText(self) -> str: # noqa: N802
        return self._full

    def elided(self) -> str:
        # 惰性重算：`resize()` 对**尚未 show** 的控件不派发 resizeEvent（Qt 只在
        # 可见时才投递），而列表/卡片普遍在 show 之前构建布局 ⇒ 只靠事件会在
        # 首帧之前一直挂着全文（真机表现＝文字越界撑破卡片）。取值时按当前宽度
        # 现算一次，等价于「随时反映真实版式」。
        self._re_elide()
        return super().text()

    def _re_elide(self) -> None:
        w = self.width()
        if w <= 1: # 还没上版式：先放全文，等 resizeEvent 再算
            return
        fm = self.fontMetrics()
        # 控件宽度 ≠ 可用文字宽：QLabel 有 QSS padding/边框，用全宽会算出
        # 「刚好放得下」而漏掉一次截断。扣掉内容边距再 elide。
        m = self.contentsMargins()
        avail = w - m.left() - m.right()
        if avail <= 1:
            avail = w
        super().setText(fm.elidedText(self._full, self._mode, avail))

    def resizeEvent(self, ev) -> None: # noqa: ANN001, N802
        super().resizeEvent(ev)
        self._re_elide()

    def showEvent(self, ev) -> None: # noqa: ANN001, N802
        super().showEvent(ev)
        self._re_elide()


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
        self.level = level # P0-A⑤：探针/selftest 可断言当前态
        self.setText(text)
        self.setToolTip(tip or text)
        self.setStyleSheet(
            f"QLabel{{background:{bg.name(QColor.NameFormat.HexArgb)};"
            f"color:{fg.name()};border:1px solid {bd.name(QColor.NameFormat.HexArgb)};"
            f"border-radius:{pill(22)}px;padding:2px 11px;}}"
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
        import icons as _icons # noqa: PLC0415（本地模块，避免顶层循环依赖）

        self._icons = _icons
        self.gc = QLabel()
        self.gc.setFixedSize(12, 12)
        self.gc.setStyleSheet("background:transparent;")
        self.gc.setPixmap(_icons.chevron_pixmap(t.tx3, collapsed=False))
        self.gc.setScaledContents(True)
        hl.addWidget(self.gc)
        self.hd.mousePressEvent = lambda _e: self.toggle() # 整行可点，对齐 web 的 button.hd

        root.addWidget(self.hd)

        self.body = QWidget()
        self.bl = QVBoxLayout(self.body)
        self.bl.setContentsMargins(0, 0, 0, 6)
        self.bl.setSpacing(1)
        root.addWidget(self.body)

    def add(self, w: QWidget) -> None:
        self.bl.addWidget(w)

    def set_tight(self, tight: bool) -> None:
        """窄栏缩略 —— E 用户收起态**不留首字**，组头整行隐藏，
        侧栏只留导航项的图标；展开还原整行组名。"""
        self.hd.setVisible(not tight)
        self.hd.setToolTip(self.title if tight else "")
        # 「间隔也稍微变大一点」——窄栏下行距放宽
        self.bl.setSpacing(4 if tight else 1)

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
        self._tight = False # E：窄栏纯图标态
        self._label = text # 收起清文字前的原文（还原用）
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(False)
        self.setFixedHeight(30)
        from PySide6.QtCore import QSize # noqa: PLC0415

        self.setIconSize(QSize(16, 16))
        if hint:
            self.setToolTip(hint)
        self._restyle()

    # -- 图标着色（web currentColor 等价）--

    def _icon_color(self) -> str:
        t = self.t
        if self.active:
            return t.tx if t.key == "whale" else t.blue
        # G：常态直接全亮 —— ，
        # tx2 在浅色底上对比不足；hover/选中再靠底色与色相区分
        return t.tx

    def _refresh_icon(self) -> None:
        import icons as _icons # noqa: PLC0415

        if self.icon_key:
            self.setIcon(_icons.nav_icon(self.icon_key, self._icon_color(), 16))

    def set_tight(self, tight: bool) -> None:
        """窄栏缩略——
        清文字只留图标，图标放大一档 16→20，行距加高；展开时全部还原。
        文本存 _label，_restyle/set_active 重绘不影响还原。"""
        if tight and not self._tight:
            self._label = self.text()
            self.setText("")
            self.setIconSize(QSize(20, 20))
            self.setFixedHeight(34)
        elif not tight and self._tight:
            self.setText(getattr(self, "_label", "") or self.text())
            self.setIconSize(QSize(16, 16))
            self.setFixedHeight(30)
        self._tight = tight

    def set_active(self, on: bool) -> None:
        self.active = on
        self._restyle()

    def enterEvent(self, e): # noqa: N802
        self._hover = True
        self._refresh_icon()
        super().enterEvent(e)

    def leaveEvent(self, e): # noqa: N802
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
                f"QPushButton:hover{{background:{rgba(t.q('tx'), 14).name(QColor.NameFormat.HexArgb)};color:{t.tx};}}"
                f"QPushButton:pressed{{background:{rgba(t.q('tx'), 26).name(QColor.NameFormat.HexArgb)};color:{t.tx};}}" # L 按压加深
            )


# ---------------------------------------------------------------- 设置行


class Field(QWidget):
    """一张设置卡里的一行 —— 左标签（+说明），右侧控件。

    可用性要点（原任务里点名要的三条之一）：
      · 每行有**一句话说明**，不用术语（"不改后端，改完立刻生效"）
      · 右侧控件永远有**可见的当前值**，不存在"点了没反应"

    宽度自适应（对齐 web 真值 `.row{flex-wrap:wrap}` + `.row .grow{flex:1;min-width:220px}`
    + `.row input{width:100%}`）：
      · **右侧控件撑满剩余宽度**（web `flex:1` + `width:100%` 的等价物）——
        这就是用户实锤的「圆角胶囊太短、文字显示不全」：Qt 旧版给输入框
        固定 220~120px 的死宽，值一变长就顶字；web 一直是撑满的。
      · 标签/说明这一侧可压窄换行（`_wrap_capable`）；
      · 本行可用宽 ≤ `_STACK_AT` 时整行改成**上下两段**（标签在上、控件在下满宽）
        —— 并排放不下时并排的结果是控件只剩几十像素，换上下反而看得全。
    """

    #: 窄于此宽度（本行可用宽）就改成上下排列
    _STACK_AT = 430
    #: 右侧控件允许被压到的最小宽（再窄就该走上下排列了）
    _CTRL_MIN = 120
    #: 左侧标签区的最小宽 —— **必须给**：标签走 `_wrap_capable`（水平策略 Ignored，
    #: 意思是"多窄都行，我不撑"），若左侧不设下限、右侧又是 Expanding，Qt 分宽度时
    #: 会把左侧压到 1 个字符宽（实测费用计算器整列标签只剩「厂」「每」「时」），
    #: 文字虽在换行但行高已被布局钉死 ⇒ 只看得见第一个字。
    _LEFT_MIN = 88

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
        self.control = control # 接线：保存时要按行取值，控件引用挂在行上
        self._stacked = False
        self._root = QHBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(16)

        self._left_w = QWidget()
        self._left_w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._left_w.setStyleSheet("background:transparent;")
        self._left_w.setMinimumWidth(self._LEFT_MIN) # 见 _LEFT_MIN 注解：不给下限会被压成竖排单字
        left = QVBoxLayout(self._left_w)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(2)
        self.lb = QLabel(label)
        self.lb.setFont(qfont(t, t.body_size, 500))
        self.lb.setStyleSheet(f"color:{t.tx};background:transparent;")
        _wrap_capable(self.lb) # 标签本身也可能很长（如「检查间隔(毫秒)」+ 括注）
        left.addWidget(self.lb)
        if desc:
            d = QLabel(desc)
            d.setFont(qfont(t, t.body_size - 1.5))
            d.setStyleSheet(f"color:{t.tx3};background:transparent;")
            _wrap_capable(d) # 说明行可压窄换行，不顶住页面最小宽（见 _wrap_capable）
            left.addWidget(d)
        # 左侧标签区**不吃 stretch**（0）：它的宽由内容/`_LEFT_MIN` 决定，
        # 多出来的宽全部给右侧控件（web 侧 `label` 是行内宽、`.grow` 才是 flex:1）。
        # 旧写法给左侧 stretch=1，Qt 会在两列间平分 ⇒ 标签列被撑宽、输入框反而变短，
        # 正是用户「胶囊太短」的一条来源。
        self._root.addWidget(self._left_w, 0)
        # #7：最小行高按 QFontMetrics 实测 —— 标签+说明永不重叠。
        # （根治在页面级：Shell._wrap_scroll 给每页套了滚动容器，压缩不再发生；
        # 这里是行级兜底，就算哪天又有人把行塞进不可滚的固定高容器也不会叠。）
        from PySide6.QtGui import QFontMetrics # noqa: PLC0415

        fm_lb = QFontMetrics(qfont(t, t.body_size, 500))
        fm_ds = QFontMetrics(qfont(t, t.body_size - 1.5, 400))
        need = fm_lb.height() + (2 + fm_ds.height() if desc else 0) + 6
        self._min_h_side = max(34, need)
        self.setMinimumHeight(self._min_h_side)
        self._ctrl_slot = None
        if control is not None:
            # 控件宽度交给布局伸张（web `.grow{flex:1}` + `input{width:100%}`）：
            # 只留一个「还能看清值」的最小宽，其余全部拉伸填满行尾空白。
            from PySide6.QtWidgets import QSizePolicy as _SP # noqa: PLC0415

            control.setMinimumWidth(self._CTRL_MIN)
            control.setSizePolicy(_SP.Policy.Expanding, _SP.Policy.Fixed)
            self._ctrl_slot = control
            self._root.addWidget(control, 1, Qt.AlignmentFlag.AlignVCenter)

    # -- 窄窗时改成上下排列 ------------------------------------------------
    def resizeEvent(self, ev) -> None: # noqa: ANN001, N802
        super().resizeEvent(ev)
        self._apply_responsive(ev.size().width())

    def _apply_responsive(self, w: int) -> None:
        want_stack = w < self._STACK_AT and self._ctrl_slot is not None
        if want_stack == self._stacked:
            return
        self._stacked = want_stack
        self._root.removeWidget(self._left_w)
        if self._ctrl_slot is not None:
            self._root.removeWidget(self._ctrl_slot)
        # 清空后重建方向
        while self._root.count():
            self._root.takeAt(0)
        self._root.setDirection(
            QBoxLayout.Direction.TopToBottom if want_stack
            else QBoxLayout.Direction.LeftToRight)
        self._root.setSpacing(6 if want_stack else 16)
        self._root.addWidget(self._left_w, 0) # 左侧不吃 stretch（见 __init__ 注解）
        if self._ctrl_slot is not None:
            # 并排：控件也吃 stretch（撑满剩余宽，见 __init__ 的 web 对照）；
            # 上下：控件满宽，靠 stretch 交给它。
            self._root.addWidget(
                self._ctrl_slot, 1,
                Qt.AlignmentFlag.AlignLeft if want_stack
                else Qt.AlignmentFlag.AlignVCenter)
        self.setMinimumHeight(self._min_h_side if not want_stack
                              else self._min_h_side + self._ctrl_slot.sizeHint().height() + 6)
        self.updateGeometry()


class Switch(QCheckBox):
    """开关 —— 自绘，对齐 web 侧的"改完即生效"手感（无系统控件皮肤）。"""

    def __init__(self, t: Tokens, on: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.t = t
        self.setChecked(on)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 22)
        self.stateChanged.connect(lambda _: self.update())

    def paintEvent(self, _e): # noqa: N802
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
            f"QLineEdit{{background:{rgba(t.q('tx'), 16).name(QColor.NameFormat.HexArgb)};"
            f"color:{t.tx};border:1px solid {t.bd};border-radius:{pill(32)}px;"
            f"padding:0 10px;}}"
            f"QLineEdit:focus{{border:1px solid {t.blue};}}"
        )


def h2(t: Tokens, text: str, badge: Badge | None = None) -> QWidget:
    """面板头 —— 标题 + 状态徽章同一行（对齐 web 侧 `.sec-hd`）。

    ⚠️ 玻璃卡坑：
       whale 卡挂了 QGraphicsDropShadowEffect，整棵卡子树走**离屏合成**；
       裸 QWidget 包裹层在合成路径下会把底下那一条（自身高度 19px）
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


def _wrap_capable(lb: QLabel, min_w: int = 0) -> QLabel:
    """让一个 wordWrap QLabel 真正**允许被压窄**。

    ⛔ Qt 的坑：`setWordWrap(True)` 只让控件能画成多行，**不降低它的
    `minimumSizeHint().width()`** —— 那个值仍是「整句话不换行」的宽度。
    布局在算容器最小宽时用 minimumSizeHint ⇒ 一句 40 字的说明会把整页
    最小宽顶到 800+ px，窗口就再也缩不到更窄（实测：人设页最小宽 853px，
    缩窗到 620 时列表视口纹丝不动）。
    必须显式把「最小宽 + 水平 sizePolicy」松掉，压缩才会真的传导下来。
    """
    lb.setWordWrap(True)
    lb.setMinimumWidth(min_w)
    from PySide6.QtWidgets import QSizePolicy as _SP # noqa: PLC0415

    lb.setSizePolicy(_SP.Policy.Ignored, _SP.Policy.Minimum)
    return lb


def desc(t: Tokens, text: str) -> QLabel:
    """一句话说明 —— 面板头下面那行（web 侧 `.desc`）。

    宽度自适应：说明文字可以压窄换行（`_wrap_capable`），但不许把页面最小宽顶住。
    """
    lb = QLabel(text)
    lb.setFont(qfont(t, t.body_size - 0.5))
    lb.setStyleSheet(f"color:{t.tx3};background:transparent;")
    return _wrap_capable(lb)


# ---------------------------------------------------------------- 顶栏两件


class IconBtn(QAbstractButton):
    """无边框自绘图标按钮。

    然后稍微粗一点」。旧形态是 Btn("—")/Btn("□") ghost 方块（带边框带底）；
    新形态对齐微信/系统惯例：
      · 最小化 = 一条粗横线
      · 最大化（未最大化态）= 一个直角方框
      · 还原（最大化态）   = 双直角框交叠
      · 常态无边框无底色，hover 出浅底圆角；笔画 ~2px、圆头；
    只管绘制 —— WM_NCHITTEST 拖拽/resize 分支一行不动。
    """

    def __init__(self, t: Tokens, kind: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.t = t
        self.kind = kind # "min" | "max"（max 的画法随 isMaximized 态切换）
        self._hover = False
        self.setFixedSize(44, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, e): # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e): # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def _ink(self) -> QColor:
        return self.t.q("tx") if self._hover else self.t.q("tx2")

    def paintEvent(self, _e): # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if self._hover:
            path = QPainterPath()
            path.addRoundedRect(QRectF(2, 1, w - 4, h - 2), 7, 7)
            p.fillPath(path, rgba(self.t.q("tx"), 16))
        ink = self._ink()
        pen = QPen(ink)
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        cy = h / 2.0
        if self.kind == "min":
            # 一条粗横线（几何居中，宽 20）
            p.drawLine(int(w / 2 - 10), int(cy), int(w / 2 + 10), int(cy))
        elif self.kind == "max":
            win = self.window()
            maximized = bool(win is not None and win.isMaximized())
            if not maximized:
                # 最大化：单个直角方框
                p.drawRect(int(w / 2 - 9), int(cy - 8), 18, 16)
            else:
                # 还原：双直角框交叠（后框画右上缺角 L，前框整框）
                p.drawLine(w // 2 - 1, int(cy) - 10, w // 2 + 9, int(cy) - 10)
                p.drawLine(w // 2 + 9, int(cy) - 10, w // 2 + 9, int(cy))
                p.drawRect(w // 2 - 9, int(cy) - 6, 18, 16)
        else:
            p.drawRect(int(w / 2 - 9), int(cy - 8), 18, 16)
        p.end()


class WhaleBadge(QLabel):
    """顶栏鲸鱼徽章 —— 复刻 web 侧 `.whale-badge`：52×52 圆角 13 **近黑实心底**（#14161a），
    真图 `assets/icon-whale.png` 36×36 @ (left 8 / bottom 6)（几何已锚定，别再手画鲸鱼）。

    。
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
        from pathlib import Path # noqa: PLC0415

        root = Path(__file__).resolve().parents[1] # ui_qt → 项目根（落位自 _scratch/qt_proto，层级浅一级）
        img = root / "assets" / "icon-whale.png"
        self._pm = QPixmap(str(img)) if img.exists() else QPixmap()

        # ── 交互状态（web 闭包的对应物；动画实现都在 whale_anim.py，本类只存帧与接线）──
        self._seed = seed
        self._an = None # WhaleAnimator，懒建：首次按下才挂浮层
        self._pose = (1.0, 1.0) # hero 当前 (sy, sx) —— 果冻/回弹帧
        self._hero_op = 1.0 # 按下后变淡到 0.12
        self._jstep = 6 # 果冻当前帧索引（末帧 = 归位）
        self._dragging = False
        self._jelly_anim = None
        self._drag_timer = QTimer(self)
        self._drag_timer.setInterval(30)
        self._drag_timer.timeout.connect(self._on_drag_tick)
        self._home_timer = QTimer(self) # 回弹后 120ms 归位（web setTimeout 同款）
        self._home_timer.setSingleShot(True)
        self._home_timer.setInterval(120)
        self._home_timer.timeout.connect(lambda: self._set_jstep(6))
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    # -- 悬停果冻：QPropertyAnimation 逐帧驱动 jelly_step（7 帧真值见 whale_anim.JELLY_POSES）--
    def _get_jstep(self) -> int:
        return self._jstep

    def _set_jstep(self, v: int) -> None:
        from whale_anim import JELLY_POSES # noqa: PLC0415

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
        from whale_anim import JELLY_KEYFRAMES, JELLY_TOTAL_MS # noqa: PLC0415

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
            from whale_anim import WhaleAnimator # noqa: PLC0415

            t = getattr(self.window(), "t", None) # Shell 带 Tokens —— 气泡色随主题蓝
            self._an = WhaleAnimator(self, seed=self._seed,
                                     bubble_color=(t.blue if t is not None else "#6FCFFF"))
        return self._an

    # -- 拖拽（测试/取证也走这三个入口；参数均为窗口局部坐标）--
    def drag_begin(self, pos) -> bool:
        if self._busy():
            return False
        from whale_anim import HERO_OPACITY, JELLY_FRAMES # noqa: PLC0415

        self._dragging = True
        if self._jelly_anim is not None and \
                self._jelly_anim.state() == QPropertyAnimation.State.Running:
            self._jelly_anim.stop()
        self._set_jstep(JELLY_FRAMES - 1) # 果冻归位（web: transition none + transform 清空）
        self._hero_op = HERO_OPACITY
        self.update()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self._animator().begin_drag(pos, self._pm)
        self._drag_timer.start()
        return True

    def drag_move(self, pos) -> None:
        if self._dragging:
            self._an.move_drag(pos)

    def drag_end(self, mode: int | None = None) -> "int | None": # noqa: F821
        if not self._dragging:
            return None
        self._dragging = False
        self._drag_timer.stop()
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        return self._an.end_drag(mode) # mode 由种子化 rng 三选一

    def _on_drag_tick(self) -> None:
        if self._dragging and self._an is not None:
            self._an.tick_drag() # 挣扎摆尾：幅度随按住时长衰减（QTimer 驱动）

    # -- mouse 事件 → 拖拽入口（Qt 按下即隐式抓鼠，窗口内出界照收 move）--
    def mousePressEvent(self, e): # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton or not self.drag_begin(
                self.mapTo(self.window(), e.position().toPoint())):
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e): # noqa: N802
        if not self.drag_move(self.mapTo(self.window(), e.position().toPoint())):
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e): # noqa: N802
        if self.drag_end() is None:
            super().mouseReleaseEvent(e)

    def enterEvent(self, e): # noqa: N802
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

    def paintEvent(self, _e): # noqa: N802
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

    ⇒
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

        from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect # noqa: PLC0415
        from PySide6.QtGui import QFontMetrics # noqa: PLC0415

        # #7（真机问题③「鲸落高亮框裁一半」）：
        #   老写法 fixed 高 28 / knob 高 24 / 宽按 500 字重 + 30 —— 但选中态
        #   按钮是 **600 字重**（更宽），且高 DPI 下 point 字体行高变大，
        #   固定像素必裁。全部改按 QFontMetrics 实测：
        #   · 宽 = 600 字重实测 advance + 随行高的余量
        #   · 高 = 实测行高 + 呼吸余量（DPI 越高自动越高）
        #   · knob 高 = 容器高 - 4（不再写死 24）
        self._f = qfont(t, 12.5, 500)
        fm_sel = QFontMetrics(qfont(t, 12.5, 600)) # 高亮框里装的是选中态字重
        pad = max(30, round(fm_sel.height() * 1.1))
        self._w = max(56, max(fm_sel.horizontalAdvance(lb) for _k, lb in options) + pad)
        self.setFixedHeight(max(28, fm_sel.height() + 12))
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
            b.setFont(self._f)
            b.setFlat(True)
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            self._buttons.append(b)
            lay.addWidget(b)

        self._QRect = QRect
        self._place_knob(instant=True)
        self._restyle()

    # -- 布局 --

    def _target(self) -> "QRect": # noqa: F821
        idx = self._keys.index(self._value) if self._value in self._keys else 0
        # knob 高随容器走
        return self._QRect(2 + idx * self._w, 2, self._w, self.height() - 4)

    def _place_knob(self, instant: bool = False) -> None:
        r = self._target()
        if instant:
            self._knob.setGeometry(r)
            return
        from PySide6.QtCore import QEasingCurve, QPropertyAnimation # noqa: PLC0415

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
            f"border:1px solid {t.bd};border-radius:{pill(28)}px;}}"
            f"QWidget#SegKnob{{background:{knob_bg};border:1px solid {knob_bd};"
            f"border-radius:{pill(25)}px;}}"
            "QPushButton{background:transparent;border:none;color:" + off + ";padding:0;}"
            f"QPushButton:hover{{color:{t.tx};}}"
            f"QPushButton:pressed{{color:{t.blue};}}" # L 按压给色反馈
        )
        for b, (key, _lb) in zip(self._buttons, self.options):
            b.setFont(qfont(t, 12.5, 600 if key == self._value else 500))
            b.setStyleSheet(
                "QPushButton{background:transparent;border:none;color:"
                + (on if key == self._value else off)
                + ";padding:0;}"
                f"QPushButton:hover{{color:{t.tx};}}"
                f"QPushButton:pressed{{color:{t.blue};}}" # L 按压给色反馈
            )
