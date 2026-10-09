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
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPixmap, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from icons import CHEVRON, svg_pixmap
from stylekit_qt import (
    FIELD_CHEVRON_W,
    Tokens,
    bevel,
    bevel_edges,
    field_qss,
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

    ：「所有弹窗都要可挪动，按住任意位置（除文字显示区域）都能挪」。

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

    ⛔ **不许给 Card 挂 `QGraphicsDropShadowEffect`**（2026-10-09 实测撤掉）：
      挂上它之后 Qt 必须把**该控件及其整棵子树**渲进离屏 buffer 再合成 —— 页面上
      有 N 张卡就是 N 次离屏合成，且每建一张卡都要重建一次 FBO。实测（同一台机、
      同一份 overview 页，只改这一处）：
        · overview 页构建 **2704 ms → 67 ms**（40 倍）
        · wechat  页构建 **4551 ms → 67 ms**（68 倍）
      用户看到的现象就是「从概览切到微信要卡好几秒」。
      而**视觉上几乎看不出差别**：阴影只是卡边外几像素一层极淡的蓝晕，在深色海底
      底图上贡献接近于零（A/B 截图逐像素比对过）。花 40 倍时间换一层看不见的晕，
      这笔账不划算 ⇒ 改成**靠 QSS 描边提亮**表达层级（下面 `_card_qss`）。
      ⚠️ 弹窗/浮层（`_card_dialog` 那类）仍可用 effect：一页至多一两个，
         不在"页面构建"的热路径上；但**列表/网格里成片出现的控件一律不许挂**。
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
        self.setStyleSheet(_card_qss(t))


def _card_qss(t: Tokens) -> str:
    """卡片的 QSS（三主题各自一套，**不用 QGraphicsEffect**）。

    · whale：玻璃。描边**提亮一档**（原来 .16 透明度太淡，远看是一片糊）
      —— 让"边"负责划界。原先靠外发光补层级，那条光现在由描边承担。
    · light / dark：实底 + 1px 描边（dark 刻意不加任何"阴影感"）。
    """
    if t.glass:
        edge = rgba(t.q("blue"), 52).name(QColor.NameFormat.HexArgb)
        return (f"#Card{{background:{t.card};border:1px solid {edge};"
                f"border-radius:{t.radius_card}px;}}")
    return (f"#Card{{background:{t.card};border:{t.card_border}px solid {t.bd};"
            f"border-radius:{t.radius_card}px;}}")


# ---------------------------------------------------------------- 主按钮


#: 可点目标最小高度的兜底值（读不到配置时用）。34 ＝桌面行级标准高度。
_TAP_MIN_DEFAULT = 34
#: 触摸可用的通行下限。用户把 `ui.tap_min` 调大于它也没有额外收益（再大就开始撑散布局）。
_TAP_MIN_MAX = 44


def _tap_min_from_config() -> int:
    """从配置读"可点目标最小高度"（`ui.tap_min`），**任何异常都回退到默认**。

    ⚠️ 这里必须 fail-safe：按钮构造是界面初始化的必经之路，
    配置读不到（首次启动/档被占）绝不能让它抛出去 —— 那会整个界面起不来。
    越界值一律夹到 `[默认, 上限]`，防止一个 999 把界面撑散。
    """
    try:
        from agent import config as _cfg
        v = int((_cfg.get_config().get("ui") or {}).get("tap_min") or _TAP_MIN_DEFAULT)
    except Exception: # noqa: BLE001
        return _TAP_MIN_DEFAULT
    return max(_TAP_MIN_DEFAULT, min(_TAP_MIN_MAX, v))


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
        ink: str = "",
        tiny: bool = False,
    ):
        super().__init__(text, parent)
        self.t = t
        self.role = role
        self.shape = shape
        # 非空 = **深色文字档**：给"浅色底"上的按钮用（whale 的顶栏底色是浅蓝）。
        # 底色 / 描边 / hover / 按下全部由 `ink` 派生 —— 不能读 `t.tx`，
        # 否则浅字压在浅底上读不出来（顶栏那排按钮就是这个场景）。
        self._ink = ink
        # tiny 档（web `button.tiny{padding:3px 10px;font-size:12px}`）：
        # 卡头右上角那一排工具按钮用的规格 —— 它们是"次要操作"，用常规尺寸会把卡片
        # 标题行撑得很重（web 侧同样是 tiny，不是普通 ghost）。
        self._tiny = bool(tiny)
        self._rad = 0
        self._fixed_w = 0   # set_button_size 登记的显式尺寸（0 = 未登记，走布局）
        self._fixed_h = 0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # 屏幕阅读器读的是**可访问名**，不是控件上画的那串字（图标按钮更要靠它）。
        # 有文字时用文字兜底，保证"没显式设过"的按钮至少有一个能读的名字。
        try:
            if text:
                self.setAccessibleName(str(text))
        except Exception: # noqa: BLE001
            pass  # 无障碍信息缺失不影响功能，静默即可
        self.setFont(qfont(t, 12 if self._tiny else t.body_size,
                           500 if role != "primary" else 600))
        # 高度：胶囊要 ≥2×半径才画得出半圆头。标准高＝`ui.tap_min`（默认 34，与下拉/输入行齐平；
        # 触摸设备可调到 44）；tiny 档固定压到 24 —— 它要跟卡片标题同处一行，
        # 那一行的高度由标题定，按钮再高就把卡片头撑起来（这一档不改，理由见类 docstring）。
        _tap = _tap_min_from_config()
        self.setMinimumHeight(24 if self._tiny else max(34, _tap))
        # ⚠️ 宽度策略关乎**文字会不会被压烂**：
        #    · 横向 Minimum ⇒ 允许被父布局压到 minimumSizeHint 以下，文字直接溢出/
        #      叠字。人设卡那排按钮挤在小卡片里就是这样糊掉的。
        #    · Preferred   ⇒ 布局**优先给 sizeHint（= 文字宽 + 内边距）**，压不动
        #      就换行/撑开容器。宁可让容器变大，也不让字看不清。
        #    纵向仍 Fixed：按钮高度不参与拉伸。
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        if text:
            self.setMinimumWidth(self._text_width_hint())
        self._sync_radius()
        # ── 实体感（一）：外投影 ──
        # QSS 没有 box-shadow，只能走 QGraphicsEffect。它作用在**渲染后的 pixmap** 上，
        # 所以样式表画的圆角轮廓会被原样投出来（不会投成方角）。
        # 主按钮用**强调色的光晕**（"它在发光" ⇒ 视觉上最靠前），其余用中性投影。
        _sh = QGraphicsDropShadowEffect(self)
        _sh.setColor(rgba(t.q("blue"), 96) if role == "primary" else rgba(QColor(0, 0, 0), 105))
        _sh.setOffset(0, 2)
        _sh.setBlurRadius(8 if role != "primary" else 14)
        self._shadow = _sh
        self.setGraphicsEffect(_sh)

        # ── 特效：hover 浮起 / 按下沉下 / 松开回弹 ──
        # QSS 没有 transform，位移只能靠**投影的变化**来"演"：
        # 投影变大变远 = 它离开了纸面（浮起）；变小贴近 = 被按下去。
        # 这是"这按钮按得动"最直接的反馈 —— 只换底色是不够的。
        self._sh_rest = (8.0, 2.0) if role != "primary" else (14.0, 2.0)
        self._sh_hover = (14.0, 4.0) if role != "primary" else (20.0, 5.0)
        self._sh_down = (3.0, 1.0)
        self._anim_blur = QPropertyAnimation(_sh, b"blurRadius", self)
        self._anim_off = QPropertyAnimation(_sh, b"offset", self)
        for _a in (self._anim_blur, self._anim_off):
            _a.setDuration(130)
            _a.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.pressed.connect(lambda: self._fx(self._sh_down))
        # 松开时鼠标通常还在按钮上 ⇒ 回到 hover 档；真移出去了由 leaveEvent 兜底
        self.released.connect(lambda: self._fx(self._sh_hover))

    def setToolTip(self, tip: str) -> None: # noqa: N802 (Qt 的命名)
        """**同一个字符串也当可访问名** —— 图标按钮（`shape="circle"` 的 ×、−、+）没有文字，
        屏幕阅读器**只读 `accessibleName`、不读 tooltip** ⇒ 不同步的话它在读屏里就是个"按钮"。

        ⛔ 只对**没有文字**的按钮补名（有文字的按钮在 `__init__` 里已经用文字当名字了），
        也不覆盖显式设过的名字。
        """
        super().setToolTip(tip)
        try:
            if tip and not self.text():
                self.setAccessibleName(str(tip))
        except Exception: # noqa: BLE001
            pass  # 无障碍信息缺失不影响功能，静默即可

    def _fx(self, target: tuple[float, float]) -> None:
        """把投影动画到目标档位（常态 / hover / 按下）。"""
        blur, dy = target
        self._anim_blur.stop()
        self._anim_blur.setStartValue(float(self._shadow.blurRadius()))
        self._anim_blur.setEndValue(float(blur))
        self._anim_blur.start()
        self._anim_off.stop()
        self._anim_off.setStartValue(self._shadow.offset())
        self._anim_off.setEndValue(QPointF(0.0, float(dy)))
        self._anim_off.start()

    def enterEvent(self, e): # noqa: N802
        self._fx(self._sh_hover)
        super().enterEvent(e)

    def leaveEvent(self, e): # noqa: N802
        self._fx(self._sh_rest)
        super().leaveEvent(e)

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
        """四态（常态/hover/pressed/disabled），对齐 web 侧按钮手感规格（assets/console/index.html L532-557）：

        · hover  → 浮起（主按钮换强调色底 / ghost 染描边+染字 / danger 加深红底）
        · pressed→ **按进去**：QSS 没有 transform，用 padding 上下 +1px 把文字压下去 1px，
                   底色再走一档（web: translateY(1px) scale(.975) 的 Qt 等价物）
        · danger 字色用 err_tx（亮色档）—— dark 上拿主 err 当字色会沉进背景

        ⚠️ 所有颜色一律过 `qss()` 转成 Qt 字面量，**不能**拿 `QColor` 的 rgba()
        CSS 原文直接插 —— Qt 样式表八位十六进制是 `#AARRGGBB`（透明度最前），
        小数透明度的 rgba() 函数式写法解析行为也不可靠（ 曾让整条规则不生效
        回落默认皮肤； 又曾把序写反成 RRGGBBAA ⇒ 半透明色全变黄绿， 以
        像素实测钉死：唯一正确的序就是 AARRGGBB）。
        """
        t, r = self.t, self.role
        # 半径按**真实尺寸**算（见 _sync_radius）—— 不写死数字：Qt 的上限是
        # min(w,h)/2，超一像素整条 border-radius 被丢弃退回直角。
        rad = self._rad or radius_for(self.shape, 120, 34)
        # L：按压态要「一眼可辨」——底色往字色轴压一档（亮主题=变深、
        # 暗主题=提亮一档，都是暗色 UI 的标准按压惯例），描边同步加深。
        # web 侧 translateY(1px) scale(.975) 在 QSS 里没有 transform 等价物，
        # 压字 1px（padding 上+1 下-1）保持，靠底/边双深化补足可辨度。
        # ── 实体感（二）：垂直渐变 ──
        # QSS 没有 box-shadow / inset-shadow，做厚度只有两条路：渐变与分段边框。
        # 这里把"纯色底"换成**上亮下暗**的垂直渐变 —— 上方受光、下方背光，
        # 一眼就有"这是一块有厚度的东西"。三套主题各按自己的底色推导，不各配一套色。
        def _vgrad(base: QColor, k_up: float = 0.17, k_dn: float = 0.13,
                   invert: bool = False) -> str:
            """纯色 → 垂直渐变。`invert=True` ⇒ **上暗下亮**。

            ★ 反向渐变 ＝ **凹陷**：常态是"上亮下暗"（光从上方来、上沿受光），
              按下时把明暗翻过来（受光跑到下沿）⇒ 读起来就是"这块被压进去了"。
              **只把底色调深是不够的** —— 那只是"变暗"，不是"凹下去"；
              凹陷的本质是**受光面翻转**。
            """
            hi = mix(base, QColor(255, 255, 255), k_up)
            lo = mix(base, QColor(0, 0, 0), k_dn)
            if invert:
                hi, lo = lo, hi
            return ("qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 %s,stop:1 %s)"
                    % (qss(hi), qss(lo)))

        def _vgrad2(top: QColor, bottom: QColor) -> str:
            """两点垂直渐变（上 → 下）。给"显式指定两端色"的场景用。

            与 `_vgrad` 的分工：`_vgrad` 从**一个基色**推两端（快，但两端都被
            基色约束）；这里直接给两端，用于必须照搬 web 渐变配方的地方
            （web `.pri` = `linear-gradient(160deg,var(--blue),var(--blue2))`）。
            """
            return ("qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 %s,stop:1 %s)"
                    % (qss(top), qss(bottom)))

        def _rel_lum(*cols: QColor) -> float:
            """一组取色里的**最高** WCAG 相对亮度（供对比度判定用）。"""
            best = 0.0
            for c in cols:
                f = []
                for v in (c.redF(), c.greenF(), c.blueF()):
                    f.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
                best = max(best, 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2])
            return best

        def _contrast(fg: QColor, bg_col: QColor) -> float:
            """WCAG 对比度 (L1+0.05)/(L2+0.05)。"""
            a, b = _rel_lum(fg), _rel_lum(bg_col)
            if a < b:
                a, b = b, a
            return (a + 0.05) / (b + 0.05)

        press_border = ""
        if r == "primary":
            _edge = t.q("blue")
            # ⛔ 渐变**必须向下走暗**（强调色 → blue2），不许向上提亮。
            #    web `.pri` 是 `linear-gradient(160deg,var(--blue),var(--blue2))`，
            #    blue2 就是 blue 的**更暗一档**；此前这里用 `mix(blue, 白, .17)`
            #    把上沿提亮 17%，方向与 web 相反 —— 上沿因此比 blue 还亮，
            #    压在其上的字色对比度被吃掉一截（实测 dark 上沿 #949AF6 ≈ 2.3:1）。
            #    向下走暗的另一个好处：两端的对比度里，"更暗"那端只会更安全。
            bg = _vgrad2(t.q("blue"), t.q("blue2"))
            bg_h = _vgrad2(mix(t.q("blue"), QColor(255, 255, 255), 0.08), t.q("blue2"))
            # ★ 主按钮的字色**用两种候选各算一次对比度、取高的那个**，
            #   不按主题名硬编码、也不拿亮度阈值猜。
            #   三套主题的实测：whale 亮青蓝(#6FCFFF) 深字 6.66 / 白字 1.74；
            #   dark 中亮度紫蓝(#8B93FF) 深字 5.17 / 白字 2.72；
            #   light 深蓝(#0866FF) 白字 4.82 / 深字 2.4x ⇒ 只有 light 该用白字。
            #   渐变两端各判一次，**取更差的那端**当结论（字要压在整个渐变上）。
            _white = QColor(255, 255, 255)
            _dark_ink = QColor("#0A1B2E") if t.key == "whale" else QColor("#0A0B0C")
            _ends = (t.q("blue"), t.q("blue2"))
            _white_worst = min(_contrast(_white, c) for c in _ends)
            _ink_worst = min(_contrast(_dark_ink, c) for c in _ends)
            fg = qss(_dark_ink) if _ink_worst > _white_worst else qss(_white)
            # 浅底上的主按钮：**实心、自己的颜色**（web `.pri` 同义），
            # 外加一圈白色高光边 —— web `.pri` 就有
            # `border:1px solid rgba(255,255,255,.72)`，深底上看不出、浅底上是关键分层。
            bd = qss(rgba(QColor(255, 255, 255), 170)) if self._ink else "transparent"
            bg_p = _vgrad2(mix(t.q("blue2"), QColor(0, 0, 0), 0.16), mix(t.q("blue2"), QColor(0, 0, 0), 0.28))
            press_border = qss(mix(t.q("blue2"), t.q("tx"), 0.45))
        elif r == "danger":
            # ⚠️ rgba() 返回的是 QColor 对象 —— 直接插进 QSS f-string 会变成
            #    "background:<PySide6.QtGui.QColor object at 0x…>" 垃圾值，
            #    整条 QPushButton 规则解析失败 → 回落默认样式（黑字沉底，
            # 这才是"黑色跟深色背景混在一起"的真根因，取证实锤）。
            #    QSS 只认字符串 ⇒ 一律 .name(HexArgb) 落成 #AARRGGBB。
            if self._ink:
                # 浅底上的危险钮 = **实心暗红块 + 白字**。
                # 红必须压得住浅蓝底：低 alpha 的浅红混进蓝里会变浊紫
                # （既不像红、也不像"危险"）⇒ 底色取 `mix(err, 黑, .5)` 的暗红，
                # 再上高 alpha；白字压在其上 ≈ 6.4:1。
                _edge = t.q("err")
                _dk = mix(t.q("err"), QColor(0, 0, 0), 0.42)
                bg = _vgrad(_dk)
                bg_h = _vgrad(mix(_dk, QColor(255, 255, 255), 0.16))
                bg_p = _vgrad(mix(_dk, QColor(0, 0, 0), 0.22), invert=True)
                fg = "#FFFFFF"
                bd = qss(rgba(_dk, 255))
                press_border = qss(rgba(mix(t.q("err"), QColor(0, 0, 0), 0.70), 255))
            else:
                _edge = t.q("err")
                fg = (getattr(t, "err_tx", "") or t.err)
                bg = qss(rgba(t.q("err"), 20))
                bg_h = qss(rgba(t.q("err"), 34))
                bg_p = qss(rgba(t.q("err"), 46))
                bd = qss(rgba(t.q("err"), 110))
                press_border = qss(rgba(t.q("err"), 170))
        elif self._ink: # ghost·浅底档 —— 与顶栏胶囊同一套：**更深的一层 + 白字**
            # web 侧 `button.ghost{background:var(--card)}` **本来就有底色**，
            # 不是"描边框住字"；原来这里给 `rgba(ink, 0)`（全透）⇒ 只剩一圈线。
            # 按钮层用**半透深色**：在彩色浅底（天蓝顶栏）上，深块与底的明度差
            # 比白块大得多（实测 2.6:1 vs 1.3:1）⇒ 按钮才像"独立的一块"；
            # 字随之走白（`ink` 由调用方传白），对比度 8:1 以上。
            # ⚠️ 这一档只在 whale 的顶栏用（浅色底）⇒ 底可以取**固定实色**：
            #   半透在这层是反效果（"实体感"要求它不透）。取比顶栏胶囊再深一档，
            #   于是"顶栏 ＜ 胶囊 ＜ 按钮"三层由浅到深，一眼分得开。
            _edge = QColor("#1E4460")
            bg = _vgrad(QColor("#2E6183"))
            bg_h = _vgrad(QColor("#3A6F92"))
            bg_p = _vgrad(QColor("#1A3A55"), invert=True)
            fg = self._ink
            bd = qss(rgba(QColor(8, 24, 40), 118))
            press_border = qss(rgba(QColor(8, 24, 40), 190))
        else: # ghost —— web `.ghost{background:var(--card)}`：**有实心底**，不是透明框
            # 三层色差：面板底 ＜ 按钮层 ＞ 字(t.tx)。
            # 原来给 `mix(bg,tx,.05)`（5% 白）—— 在深面板上几乎看不出，
            # 按钮和它所在的卡片就糊成一片、只剩描边。
            _edge = mix(t.q("bg"), t.q("tx"), 0.12)
            bg = _vgrad(_edge, 0.12, 0.18)
            bg_h = _vgrad(mix(t.q("bg"), t.q("tx"), 0.19), 0.12, 0.18)
            fg = t.tx
            bd = t.bd
            bg_p = t.blue_soft
            press_border = qss(mix(t.q("blue"), t.q("tx"), 0.30))
        hover_extra = ""
        pressed_extra = ""
        if r == "ghost":
            if self._ink:
                # 深块档：hover 让块再沉一档（白边压在彩色浅底上会"溢出来"）
                hover_extra = f"border:1px solid {qss(rgba(QColor(8, 24, 40), 158))};"
                pressed_extra = f"color:{self._ink};border:1px solid {press_border};"
            else:
                hover_extra = (f"color:{t.blue};border:1px solid {t.blue};"
                                f"border-top:1px solid {qss(mix(t.q('blue'), QColor(255, 255, 255), 0.45))};"
                                f"border-bottom:1px solid {qss(mix(t.q('blue'), QColor(0, 0, 0), 0.32))};")
                pressed_extra = f"color:{t.blue};border:1px solid {press_border};"
        elif r == "primary":
            pressed_extra = f"border:1px solid {press_border};"
        else:
            pressed_extra = f"border:1px solid {press_border};"
        # ── 实体感（三）：受光面 / 背光面 ──
        # 四边同一条线，平面设计里那是"贴纸"；把**顶边提亮、底边压暗**才有厚度
        # （等价于"光从上方来"）。QSS 的 `border-top/bottom` 写在 `border` 之后会覆盖它。
        _up = qss(mix(_edge, QColor(255, 255, 255), 0.45))
        _dn = qss(mix(_edge, QColor(0, 0, 0), 0.32))
        _box = (f"border:1px solid {bd};"
                f"border-top:1px solid {_up};"
                f"border-bottom:1px solid {_dn};")
        return (
            f"QPushButton{{background:{bg};color:{fg};{_box}"
            f"border-radius:{rad}px;padding:{'3px 10px' if self._tiny else '7px 16px'};}}"
            f"QPushButton:hover{{background:{bg_h};{hover_extra}}}"
            f"QPushButton:pressed{{background:{bg_p};"
            f"padding-top:{'4px' if self._tiny else '8px'};"
            f"padding-bottom:{'2px' if self._tiny else '6px'};{pressed_extra}"
            # 受光面翻转：常态顶亮底暗，按下时**顶暗底亮** —— 与背景的反向渐变同向，
            # 两者一致才读得出"这块凹进去了"。写在 pressed_extra 之后以覆盖它的整圈 border。
            f"border-top:1px solid {_dn};border-bottom:1px solid {_up};}}"
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
        # 立体感走公共实现（`stylekit.bevel*`）—— 与胶囊、按钮**同一支明暗规则**。
        # ⚠️ `keep_alpha=True` 必须开：状态色是**带透明度**的（半透底叠在顶栏天蓝上），
        #    当实心处理会把六态的颜色整个变掉。
        # ⚠️ `floor=96` 是给边框抬的底：半透底若边框也跟着半透，棱就看不见了。
        _e_up, _e_dn = bevel_edges(bg, 0.55, 0.42, floor=96)
        self.setStyleSheet(
            f"QLabel{{background:{bevel(bg, 0.30, 0.22, keep_alpha=True)};"
            f"color:{fg.name()};"
            f"border:1px solid {bd.name(QColor.NameFormat.HexArgb)};"
            f"border-top:1px solid {_e_up};border-bottom:1px solid {_e_dn};"
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


class NavMgRow(QWidget):
    """**合并行**（IA-4）—— 一行里收着 1~N 个功能项，成员收在二级、默认收起。

    对应 web 的 `.nav-mg`（`.nav-mg-hd` 行头 + `.nav-mg-bd` 二级体）。
    为什么是"收"而不是"删"：web 侧 `ui_arch_selftest` 有一条用户投诉驱动的硬判据 ——
    导航链接的出现序列必须与 `<section>` 的出现序列逐位相同（"划着划着导航乱跳"那条）。
    Qt 侧同理（`ui_qt/selftest.py` 与 web 全等比对）⇒ **成员的入口一个都不能删**，
    只是默认收起来。用户看到的是 **17 行**，展开才见全部 28 项。

    `.mg-n` 那枚 "+N" 是必要的：收起来的项若没有任何痕迹，等于消失。
    """

    toggled = Signal(bool)

    def __init__(self, t: Tokens, head: "NavItem", n_members: int,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.t = t
        self.expanded = False
        self.member_secs: list[str] = []
        self.n_members = n_members # `+N` 角标要报的数（展开后转纯数字，见 _sync_badge）

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.head = head
        # 「+N」挂到行头右侧：它是"这一行还藏着几个"的唯一提示。
        self.badge = QLabel("+%d" % n_members, head)
        self.badge.setFont(qfont(t, max(8.5, t.grp_size - 1.5), 700))
        self.badge.setStyleSheet(
            f"color:{t.tx3};background:transparent;padding:0 4px;")
        # 行头是 QPushButton（自带布局不可用）⇒ 靠 resizeEvent 把它贴到右缘。
        self.head.installEventFilter(self)
        self.badge.show()
        root.addWidget(head)

        self.body = QWidget()
        self.bl = QVBoxLayout(self.body)
        self.bl.setContentsMargins(0, 0, 0, 2)
        self.bl.setSpacing(1)
        self.body.setVisible(False)          # 默认收起（对齐 web `.nav-mg-bd{display:none}`）
        root.addWidget(self.body)

        # 行头点击 = 切换展开（**同时保留它自己跳转**：行头本身就是主项的入口，
        # 所以这里只在"点在 +N 上"时切换 —— 否则用户点行头想跳转却被展开挡了）。
        head.clicked.connect(self._on_head_clicked)
        self._tight = False

    def eventFilter(self, obj, ev): # noqa: N802
        if obj is self.head and ev.type() == ev.Type.Resize:
            self.badge.move(max(0, self.head.width() - self.badge.width() - 26),
                            max(0, (self.head.height() - self.badge.height()) // 2))
        return super().eventFilter(obj, ev)

    def _on_head_clicked(self) -> None:
        """点行头：**主项跳转照旧**，只在窄栏之外顺便展开这一行。

        为什么顺手展开：用户点一个大项多半就是想用里面的东西 ——
        收起态下二级看不见，"我点了它怎么没反应"比"多点一下"更糟。
        再点一次可以收起（`toggle()`/`set_expanded()` 都走这里之外的口子）。
        """
        if not self.expanded:
            self.set_expanded(True)

    def add(self, w: "NavItem") -> None:
        self.bl.addWidget(w)
        self.member_secs.append(getattr(w, "icon_key", ""))

    def set_expanded(self, on: bool) -> None:
        if on == self.expanded:
            return
        self.expanded = on
        self.body.setVisible(on)
        self._sync_badge()
        self.toggled.emit(on)

    def _sync_badge(self) -> None:
        """`+N` 角标只报"里面还藏着几个"，不兼作折叠箭头。

        折叠指示器是**组头**那一枚 chevron（`chevron_pixmap`，与 web 同一条 path）；
        行头这枚是"数量"语义 —— 展开后把 `+` 去掉、露出纯数字，收起时回 `+N`。
        ⛔ 别在这里塞文字三角箭头：全项目口径是"折叠一律用矢量 chevron，
        不用文字箭头"（判据 `selftest.t_nav` 会整文件搜那个三角字符并报红）。
        """
        self.badge.setText(("+%d" if not self.expanded else "%d") % self.n_members)

    def toggle(self) -> None:
        self.set_expanded(not self.expanded)

    def set_tight(self, tight: bool) -> None:
        """窄栏只剩图标 ⇒ 二级项也得露出来（对齐 web `.side.tight .nav-mg-bd{display:block}`），
        否则窄栏里那些功能项点不到；那枚 "+N" 在窄栏里没有位置，藏掉。"""
        self._tight = tight
        self.badge.setVisible(not tight and self.member_secs != [])
        if tight:
            self.body.setVisible(True)
        else:
            self.body.setVisible(self.expanded)


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
        self._indent = False # 二级项（合并行里的成员）标记，见 set_indent
        self._label = text # 收起清文字前的原文（还原用）
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(False)
        self.setFixedHeight(30)
        from PySide6.QtCore import QSize # noqa: PLC0415

        self.setIconSize(QSize(16, 16))

        # ── 选中时"立起来"（平时是平的）──
        # ⚠️ 投影**常驻但默认禁用**：侧栏有二十多个导航项，各挂一个活跃 effect
        #    会让每次重绘都多走一遍离屏渲染。只在选中时 `setEnabled(True)`，
        #    取消时**先动画降到 0 再禁用** —— 「立体 → 平面」那一段过渡正是靠它保住的，
        #    先禁用会让选中态"啪"地掉回平面、没有过程。
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setOffset(0, 2)
        self._shadow.setBlurRadius(0) # 0 = 看不见（常态是平的）
        self._shadow.setColor(rgba(QColor(0, 0, 0), 135))
        self._shadow.setEnabled(False)
        self.setGraphicsEffect(self._shadow)
        self._nav_anim = QPropertyAnimation(self._shadow, b"blurRadius", self)
        self._nav_anim.setDuration(170)
        self._nav_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

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

    def set_indent(self, on: bool = True) -> None:
        """标记成**二级项**（合并行里的成员）：行高矮一档、左边留出缩进，
        并在左缘画一道短竖线（web `.nav-mg-it::before` 的等价物）——
        一眼能看出"这些是上面那一行里的，不是同级的大项"。

        只改**外观**，不动入口本身：sec 仍是自己的，点击仍走自己的 `_go`。
        """
        self._indent = bool(on)
        if on:
            self.setFixedHeight(28)
            self.setContentsMargins(30, 0, 8, 0)
            self.update()

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
            self.setFixedHeight(28 if getattr(self, "_indent", False) else 30)
        self._tight = tight

    def _lift(self, blur: float) -> None:
        """把投影动画到指定虚化半径（选中＝抬起，非选中＝压平）。"""
        self._nav_anim.stop()
        self._nav_anim.setStartValue(float(self._shadow.blurRadius()))
        self._nav_anim.setEndValue(float(blur))
        self._nav_anim.start()

    def _settle_flat(self) -> None:
        """动画落定后收起 effect（仍处于非选中才收；防快速切换时误收）。"""
        if not self.active:
            self._shadow.setEnabled(False)
        try:
            self._nav_anim.finished.disconnect(self._settle_flat)
        except (RuntimeError, TypeError):
            pass

    def _lower(self) -> None:
        """非选中：先动画压平，落定后再禁用 effect。"""
        if not self._shadow.isEnabled():
            return
        try:
            self._nav_anim.finished.connect(self._settle_flat)
        except (RuntimeError, TypeError):
            pass
        self._lift(0.0)

    def set_active(self, on: bool) -> None:
        self.active = on
        self._restyle()

    def paintEvent(self, ev): # noqa: N802
        """选中时在左缘画一截**短**色条（web `.nav a.on::before` 的原样复刻）。

        ⚠️ 不能用 `border-left` 代替：边框**贯穿整个高度**，顶边/底边是硬切的直角，
           看上去就是"一块被截断的矩形"—— 而 web 那条是 `top:12px;bottom:12px`
           的一段短条，圆头、上下都收口。
        """
        super().paintEvent(ev)
        # 二级项（合并行里的成员）：左缘一道短竖线（web `.nav-mg-it::before` 的等价物）。
        # 画在**选中条之外**的左侧，两者不重叠 —— 一个在 x=0（选中）、一个在 x≈17（归属）。
        if getattr(self, "_indent", False) and not self._tight:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self.t.bd if hasattr(self.t, "bd") else self.t.tx3))
            p.setOpacity(0.5)
            p.drawRoundedRect(QRectF(17, (self.height() - 14) / 2.0, 1.5, 14), 0.75, 0.75)
            p.setOpacity(1.0)
        if not self.active:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.t.blue))
        _pad = 12 if self.height() >= 32 else 8 # 窄栏行高 34、常规 30，留白同比缩
        p.drawRoundedRect(QRectF(0, _pad, 3, max(4, self.height() - 2 * _pad)), 1.5, 1.5)

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
            # 选中＝"一块浮起来的板"：垂直渐变（上亮下暗）＋ 顶受光/底背光边 ＋ 投影。
            # 与胶囊、按钮同一套做法 —— 立体的规则在整个界面上是**同一支**，
            # 换个控件就换一套明暗关系，用户会读成"两种材质"。
            _base = t.q("blue")
            _grad = ("qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 %s,stop:1 %s)"
                     % (qss(rgba(_base, 54)), qss(rgba(_base, 26))))
            fg = t.tx if t.key == "whale" else t.blue
            self.setFont(qfont(t, t.nav_size, t.nav_active_weight))
            self.setStyleSheet(
                f"QPushButton{{background:{_grad};color:{fg};"
                f"border:none;"
                f"border-top:1px solid {qss(rgba(QColor(255, 255, 255), 48))};"
                f"border-bottom:1px solid {qss(rgba(QColor(0, 0, 0), 62))};"
                # 四角同半径 ＋ 左右内缩：让选中项是一块**四边都收口**的小板。
                # ⚠️ 别再退回"左直角 + 右圆角"：左右不对称，左边像是被切掉的；
                #    也别退回 web 那种撑满全宽（`.nav a.on` 没有圆角）——
                #    在 Qt 侧那看着就是"一块矩形从左右两边断掉"。
                f"border-radius:6px;margin:0 6px;"
                f"text-align:left;padding:0 10px 0 15px;}}"
            )
            self._shadow.setEnabled(True) # 抬起：见 __init__ 的说明
            self._lift(13.0)
        else:
            self.setFont(qfont(t, t.nav_size, t.nav_weight))
            self.setStyleSheet(
                f"QPushButton{{background:transparent;color:{t.tx2};"
                f"border:none;"
                # 与选中态同一套几何（圆角 6 / 左右 6）：hover 时块不会突然变宽
                f"border-radius:6px;margin:0 6px;"
                f"text-align:left;padding:0 10px 0 15px;}}"
                f"QPushButton:hover{{background:{rgba(t.q('tx'), 14).name(QColor.NameFormat.HexArgb)};color:{t.tx};}}"
                f"QPushButton:pressed{{background:{rgba(t.q('tx'), 26).name(QColor.NameFormat.HexArgb)};color:{t.tx};}}" # L 按压加深
            )
            self._lower() # 压平（带过渡，落定后才禁用 effect）


# ---------------------------------------------------------------- 设置行


class Field(QWidget):
    """一张设置卡里的一行 —— 左标签（+说明），右侧控件。

    可用性要点（原任务里点名要的三条之一）：
      · 每行有**一句话说明**，不用术语
      · 右侧控件永远有**可见的当前值**，不存在"点了没反应"

    宽度自适应（对齐 web 真值 `.row{flex-wrap:wrap}` + `.row .grow{flex:1;min-width:220px}`
    + `.row input{width:100%}`）：
      · **右侧控件撑满剩余宽度**（web `flex:1` + `width:100%` 的等价物）——
        这就是的「圆角胶囊太短、文字显示不全」：Qt 旧版给输入框
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
        # 旧实现给左侧 stretch=1，Qt 会在两列间平分 ⇒ 标签列被撑宽、输入框反而变短，
        # 正是的一条来源。
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
        # 全族统一外观，只把圆角换成胶囊（搜索框这一处**有意**与输入槽不同形）
        self.setStyleSheet(field_qss(t, radius=pill(32)))


def style_field(w, t: Tokens, shape: str = "line", accent: str = "", radius: int = None):
    """给任意输入控件套上"槽"的统一外观（QSS + 字号 + 单行高度），返回控件本身。

    用途：那些直接 `QLineEdit()` / `QPlainTextEdit()` 建出来的地方 —— 它们原本没有样式，
    在深色主题上就是一排系统白底。外观只此一处，见 `stylekit.field_qss`。
    """
    w.setStyleSheet(field_qss(t, shape, accent, radius))
    if shape == "area":
        w.setFont(qfont(t, t.body_size - 0.5))
    else:
        w.setFont(qfont(t, t.body_size))
        w.setFixedHeight(32)
    return w


class FieldCombo(QComboBox):
    """输入/选单"槽"里的下拉 —— 外观走 `stylekit.field_qss`，箭头**自绘矢量**。

    为什么要子类：`field_qss` 关掉了系统箭头（QSS 的 `image:` 只能指文件，与"图标一律走
    `icons` 的矢量 path"这条纪律冲突），不补一笔指示符，用户就看不出"这里能展开"。
    箭头颜色取主题弱文字色，尺寸 12px，列宽与 QSS 的 `::drop-down` 同源（`FIELD_CHEVRON_W`）。
    """

    def __init__(self, t: Tokens, options=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.t = t
        self.setFont(qfont(t, t.body_size))
        self.setFixedHeight(32)
        self.setMinimumWidth(120)
        self.setStyleSheet(field_qss(t))
        if options:
            for v, label in options:
                self.addItem(label, v)
        self._chev_for = None
        self._chev_pm = None

    def set_theme(self, t: Tokens) -> None:
        """换主题（页重建时也会重造控件；这个口留给不重建的场合）。"""
        self.t = t
        self.setFont(qfont(t, t.body_size))
        self.setStyleSheet(field_qss(t))
        self._chev_for = None
        self.update()

    def _chevron(self):
        # 按色缓存：paintEvent 里每帧重建 SVG 位图没必要
        if self._chev_pm is None or self._chev_for != self.t.tx3:
            self._chev_pm = svg_pixmap(CHEVRON, self.t.tx3, 12)
            self._chev_for = self.t.tx3
        return self._chev_pm

    def paintEvent(self, e):
        super().paintEvent(e)
        pm = self._chevron()
        sz = pm.deviceIndependentSize()
        x = self.width() - FIELD_CHEVRON_W + (FIELD_CHEVRON_W - sz.width()) / 2
        y = (self.height() - sz.height()) / 2
        p = QPainter(self)
        p.drawPixmap(int(round(x)), int(round(y)), pm)
        p.end()


def emboss(lab: QLabel, alpha: int = 115, blur: float = 1.6, on_light: bool = False) -> QLabel:
    """给文字加一圈**描边式**暗边（或亮边），让它从底上"浮"起来（文字的立体感）。

    ⚠️ QSS **没有** `text-shadow`（写了会被静默丢弃）⇒ 只能走 `QGraphicsDropShadowEffect`。
    ⚠️ **0 偏移 ＋ 小 blur** 才是描边：四周一圈均匀暗边把笔画从背景里"垫"出来；
       带偏移就变成投影了，那是另一回事。
    ⚠️ `on_light=True` 用于**深色字压在浅色底**上（如侧栏天蓝卡片里的深蓝标题）：
       这时暗边只会把字糊进底里，得反过来描**亮边**才对。

    ⛔ **嵌套限制（务必先确认父链）**：Qt 里父控件若已有 graphics effect，
       子控件再挂 effect **不生效** —— 整棵子树被并进父的那一遍离屏合成。
       所以**卡片内的文字加不了**（whale 的 `Card` 自己就挂了投影）。
       调用点只应是"父链上没有 effect"的位置：顶栏标题、侧栏状态卡标题。
    """
    eff = QGraphicsDropShadowEffect(lab)
    eff.setBlurRadius(float(blur))
    eff.setOffset(0.0, 0.0)
    eff.setColor(rgba(QColor(255, 255, 255) if on_light else QColor(0, 0, 0), alpha))
    lab.setGraphicsEffect(eff)
    return lab


def h2(t: Tokens, text: str, badge: Badge | None = None,
       extra: QWidget | None = None) -> QWidget:
    """面板头 —— 标题 + 状态徽章同一行（对齐 web 侧 `.sec-hd`）。

    `extra` 是**右端附加区**：承载 web 侧 `.ov-tools`（概览卡右上角那排工具按钮，
    `position:absolute;top:14px;right:16px`）。Qt 没有绝对定位，
    用"标题 → 徽章 → 弹簧 → extra"落到同一视觉位置。

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
    lay.addStretch(1) # 弹簧：把后面的 extra 顶到最右
    if extra is not None:
        lay.addWidget(extra, 0, Qt.AlignmentFlag.AlignVCenter)
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
    # 标记：面板装配层靠它认出"哪一项是说明行"，从而把组锚点条插在它之后
    # （不靠下标猜 —— 说明行有无由 sec.desc 决定）。
    lb.setProperty("c8_desc", True)
    return _wrap_capable(lb)


def row_label(t: Tokens, text: str, min_w: int = 120) -> QWidget:
    """行首标签 —— 要放进 QHBoxLayout 与输入框并排的那一种。

    ⛔ 为什么不能拿 `desc()` 直接塞行首再 `setMinimumWidth()`：desc 走
    `_wrap_capable`（水平策略 **Ignored**），而 Qt 布局对 Ignored 策略**完全无视
    显式 minimumWidth**（qSmartMinSize 对 Ignored 直接给 0 宽）⇒ 布局以为标签占
    0px、把输入框排在 x+spacing，标签本体却被 minimumWidth 钳在 120px 宽 ——
    两者叠字（实测费用计算器「每[200]消息数」「厂[Anthropic]商」、拍一拍
    「目标群」、视频通路四行同病，离屏探针量出 2700px² 相交）。
    修法与 Field 同款：标签包进**普通策略**的容器，容器的 setMinimumWidth 才会被
    布局真正尊重；标签在容器里照旧可换行、不撑页面最小宽。
    """
    box = QWidget()
    box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    box.setStyleSheet("background:transparent;")
    box.setMinimumWidth(int(min_w))
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    lay.addWidget(desc(t, text))
    lay.addStretch(1)
    return box


# ---------------------------------------------------------------- 顶栏两件


class IconBtn(QAbstractButton):
    """无边框自绘图标按钮。

    然后稍微粗一点」。旧形态是 Btn/Btn ghost 方块（带边框带底）；
    新形态对齐微信/系统惯例：
      · 最小化 = 一条粗横线
      · 最大化（未最大化态）= 一个直角方框
      · 还原（最大化态）   = 双直角框交叠
      · 常态无边框无底色，hover 出浅底圆角；笔画 ~2px、圆头；
    只管绘制 —— WM_NCHITTEST 拖拽/resize 分支一行不动。
    """

    def __init__(self, t: Tokens, kind: str, parent: QWidget | None = None, ink: str = ""):
        super().__init__(parent)
        self.t = t
        self.kind = kind # "min" | "max"（max 的画法随 isMaximized 态切换）
        # 非空 = 指定笔画色：给"浅色底"用（whale 的顶栏底色是浅蓝，浅笔画看不清）；
        # 空则照旧随主题的两级字色。
        self._ink_ov = ink
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
        if self._ink_ov:
            return QColor(self._ink_ov)
        return self.t.q("tx") if self._hover else self.t.q("tx2")

    def paintEvent(self, _e): # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if self._hover:
            # hover 底也做成"一块有厚度的板"：渐变 ＋ 顶受光/底背光边，
            # 与界面其它控件同一支明暗规则（自绘控件直接用 QLinearGradient，不必绕 QSS）。
            _base = QColor(self._ink_ov) if self._ink_ov else self.t.q("tx")
            _r = QRectF(2, 1, w - 4, h - 2)
            _g = QLinearGradient(_r.topLeft(), _r.bottomLeft())
            _g.setColorAt(0.0, rgba(mix(_base, QColor(255, 255, 255), 0.32), 42))
            _g.setColorAt(1.0, rgba(mix(_base, QColor(0, 0, 0), 0.45), 24))
            path = QPainterPath()
            path.addRoundedRect(_r, 7, 7)
            p.fillPath(path, QBrush(_g))
            p.save()
            p.setPen(QPen(rgba(mix(_base, QColor(255, 255, 255), 0.58), 72), 1.0))
            p.drawLine(int(_r.left()) + 3, int(_r.top()), int(_r.right()) - 3, int(_r.top()))
            p.setPen(QPen(rgba(mix(_base, QColor(0, 0, 0), 0.48), 72), 1.0))
            p.drawLine(int(_r.left()) + 3, int(_r.bottom()), int(_r.right()) - 3, int(_r.bottom()))
            p.restore()
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

    交互（web 控制台既有彩蛋的真值复刻，真源 assets/console/index.html 4907-5214，实现全在 whale_anim.py）：
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

        # 徽章方块从顶栏上"浮"起来（三层里最外面那一层）：QSS 没有 box-shadow，
        # 走 QGraphicsEffect —— 它作用在**渲染后的 pixmap** 上，所以 paintEvent 画的
        # 圆角轮廓会被原样投出来，不会投成方角。
        # ⚠️ 只作用在本体：拖拽时的浮层是**独立控件**（挂在 Shell 窗口层），不受它影响。
        _sh = QGraphicsDropShadowEffect(self)
        _sh.setBlurRadius(14)
        _sh.setOffset(0, 3)
        _sh.setColor(rgba(QColor(0, 0, 0), 135))
        self.setGraphicsEffect(_sh)

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
        # ── **三层分明**：顶栏（底）＜ 徽章方块（浮起）＜ 鲸鱼（再浮起）──
        # ① 徽章底：纯黑 → **垂直渐变**。纯色是一块"贴纸"，上亮下暗才有厚度
        #    （与按钮/胶囊同一支规则：光从上方来）。
        _r = QRectF(0, 0, 52, 52)
        _g = QLinearGradient(_r.topLeft(), _r.bottomLeft())
        _g.setColorAt(0.0, QColor("#232A33"))
        _g.setColorAt(1.0, QColor("#050608"))
        path = QPainterPath()
        path.addRoundedRect(_r, 13, 13)
        p.fillPath(path, QBrush(_g))
        # ② 受光棱：沿**圆角矩形的顶边**画一条高光（只画直线段，两端让开圆角 ∓13）。
        #    ⚠️ 别用 `drawArc` 画圆：圆的曲率与 13px 圆角的矩形不一致，弧会**悬在方块内部**，
        #       看着是"里面浮着一根线"而不是"上沿有一道棱"。
        p.save()
        p.setPen(QPen(QColor(255, 255, 255, 58), 1.2))
        p.drawLine(QPointF(13.0, 0.8), QPointF(39.0, 0.8))
        p.restore()
        if self._pm.isNull():
            return
        # ③ 鲸鱼**原样画**，不加任何"围着它"的层。
        #    这张图本身是 3D 渲染的（白身 ＋ 灰蓝腹部渐变 ＋ 眼睛高光 ＋ 尾鳍浅蓝轮廓），
        #    自己的层次已经够了。
        # ⚠️ 别想着"先铺一圈放大一点的白影当外发光"：图片上下各有 **20% 透明带**
        #    （非透明 bbox 是 y 52..204 / 256），按矩形放大等于**连透明带一起描边** ——
        #    看上去就是"把那张图片凸显出来了"，而且那层白影压在鱼身上会把鱼糊掉。
        #    要加轮廓类的效果，只能用 alpha 当遮罩，绝不能用"图片矩形"。
        p.save()
        p.translate(26, 46)
        sy, sx = self._pose
        p.scale(sx, sy)
        p.setOpacity(self._hero_op)
        p.drawPixmap(-18, -36, 36, 36, self._pm) # 本体：36×36 @ (8,10)
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

        from PySide6.QtCore import QRect # noqa: PLC0415
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
