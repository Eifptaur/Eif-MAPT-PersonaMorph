# -*- coding: utf-8 -*-
"""原生二次确认弹窗 —— 替换 web 侧的 `confirm()`。

=====================================================================
为什么这个弹窗是"原生壳"最值钱的一小块
=====================================================================
现在的危险操作确认走的是 web 侧的 `confirm()`，在 WebView2 里的实际观感是：

  · 一张浏览器样式的白框，带 "127.0.0.1:8760 显示："
  · 按钮是系统英文 "OK / Cancel"（或系统中文，但风格和整个控制台不搭）
  · **如果哪天用了自绘的 modal，弹窗会被限制在 WebView2 视口内** ——
    窗口被拖小、或页面滚到下面时，弹窗跟着跑、甚至被裁掉
  · 最要命的一条：它长得像"浏览器在警告你"，用户会本能地点掉，
    而不会读后果 —— 这正好和"危险操作要写清后果"的目标相反

原生弹窗解决上面全部四条，且**能拿到真正的模态**：
  · 顶层窗口，居中于父窗口（不跟着页面滚）
  · 遮罩压在父窗口上（而不是页面里）
  · 按钮用项目自己的样式（对齐 `StyleKit.RoundButton`）
  · 后果写成**带条目的清单**，而不是一句话

⚠️ 纪律：`launcher-src/README.md` 硬规矩第 1 条是「0 系统 MessageBox」。
   所以这个文件**不许**出现 `QMessageBox` —— 全部自绘。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from stylekit_qt import Tokens, field_qss, qfont, surface_bg
from widgets import Btn, DraggableDialog


# ---------------------------------------------------------------- 文案小工具
#
# ⚠️ 两个都是**被截图逼出来的**（whale-3 首版）：
#   ① `**不能撤销**` 原样显示成了星号 —— 这是从 web 文案直接搬过来留下的尾巴，
#      Qt 里没有 Markdown 解析。界面文案**不许出现 Markdown 记号**，
#      要靠字重/颜色表达强调（这也正是 web 侧 `console_copy_selftest` 的同口径）。
#   ② 中文没有空格断行，QLabel 的 wordWrap 在自动尺寸下会把两行挤成一坨。
#      用字符数估高度虽然粗糙，但**比重叠好**，而且锚定得足够稳。

def _clean(s: str) -> str:
    """去掉 Markdown 记号 —— 强调交给字重与颜色，不交给星号。"""
    return s.replace("**", "")


def _wrap_h(text: str, size: float, width_px: int) -> int:
    """按"每行能放几个字"估一个够用的最小高度。

    中文按 1 个字符宽度 ≈ 字号；英文/数字按 0.55 估。留 4px 行距余量。
    """
    if not text:
        return 0
    per = max(1, int(width_px / max(1.0, size)))
    # 粗略地把 ASCII 折半计数
    units = sum(0.55 if ord(ch) < 128 else 1.0 for ch in text)
    lines = max(1, int(units / per) + (1 if units % per else 0))
    return int(lines * (size + 5) + 2)


class Overlay(QDialog):
    """自绘遮罩层 —— 半透明覆盖父窗口，让"现在必须做决定"这件事一眼可见。"""

    def __init__(self, parent: QWidget, t: Tokens):
        super().__init__(parent)
        self.t = t
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)

    def paintEvent(self, _e): # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110 if self.t.key != "dark" else 150))
        p.end()


class ConfirmDialog(DraggableDialog, QDialog):
    """危险操作二次确认。

    可用性要点（原任务点名要的三条之一：'危险操作保留二次确认并写清后果'）：
      1. **后果是一条一条列出来的**，不是一段话 —— 用户扫一眼就知道要付什么代价；
      2. **危险按钮在右、默认焦点在"取消"** —— 手快连按回车不会误伤；
      3. 需要更谨慎时开 `typed_word` 门槛（照 GitHub 删仓库的做法）：
         必须把词打对才能点确认，防"肌肉记忆式确认"。

    弹窗可拖动（DraggableDialog）：按住任意非交互处即可挪，按钮仍是按钮。
    """

    decided = Signal(bool)

    def __init__(
        self,
        t: Tokens,
        parent: QWidget,
        title: str,
        intro: str,
        consequences: list[str],
        confirm_label: str = "确定",
        cancel_label: str = "取消",
        dangerous: bool = True,
        typed_word: str = "",
    ):
        super().__init__(parent)
        self.t = t
        self.result_ok = False
        self._typed_word = typed_word

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        shell = QFrame()
        self._apply_shell(shell)
        outer.addWidget(shell)

        box = QVBoxLayout(shell)
        box.setContentsMargins(24, 22, 24, 18)
        box.setSpacing(14)

        # 标题
        tl = QLabel(title)
        tl.setFont(qfont(t, 16, 600))
        tl.setStyleSheet(f"color:{t.tx};background:transparent;")
        box.addWidget(tl)

        # 引子
        if intro:
            il = QLabel(intro)
            il.setFont(qfont(t, t.body_size))
            il.setWordWrap(True)
            il.setStyleSheet(f"color:{t.tx2};background:transparent;")
            # ⚠️ 实测（whale-3 截图）：不给最小高度时，QLabel 的 wordWrap 在**自动
            #    adjustSize 之后**会被父布局压扁 ⇒ 两行文字重叠成一坨。
            #    这里显式按字数估算高度，宁可多留一行也不许重叠。
            il.setMinimumHeight(_wrap_h(intro, t.body_size, 300))
            box.addWidget(il)

        # 后果清单 —— 一条一条，带符号前缀，扫一眼能读完
        if consequences:
            lst = QVBoxLayout()
            lst.setContentsMargins(0, 2, 0, 2)
            lst.setSpacing(7)
            for c in consequences:
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(9)
                dot = QLabel("·")
                dot.setFont(qfont(t, t.body_size, 700))
                dot.setFixedWidth(8)
                dot.setStyleSheet(
                    f"color:{t.err if dangerous else t.blue};background:transparent;"
                )
                row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
                cl = QLabel(_clean(c))
                cl.setFont(qfont(t, t.body_size))
                cl.setWordWrap(True)
                cl.setStyleSheet(f"color:{t.tx2};background:transparent;")
                cl.setMinimumHeight(_wrap_h(c, t.body_size, 290))
                # 该条要强调的后果（原写法里的 `**…**`）改用**加粗**表达 ——
                # 见 `_clean()`：界面文案里不许出现 Markdown 星号（原型一开始就踩了）
                if "不能撤销" in c or "不可撤销" in c:
                    cl.setFont(qfont(t, t.body_size, 600))
                    cl.setStyleSheet(f"color:{t.err};background:transparent;")
                row.addWidget(cl, 1)
                lst.addLayout(row)
            box.addLayout(lst)

        # 打字确认（可选门槛）
        if typed_word:
            from PySide6.QtWidgets import QLineEdit # noqa: PLC0415

            hint = QLabel(f"如果确定，请把下面这个词打一遍：{typed_word}")
            hint.setFont(qfont(t, t.body_size - 1))
            hint.setStyleSheet(f"color:{t.tx3};background:transparent;")
            box.addWidget(hint)
            self.typed = QLineEdit()
            self.typed.setPlaceholderText(typed_word)
            self.typed.setFont(qfont(t, t.body_size))
            self.typed.setFixedHeight(32)
            self.typed.setStyleSheet(field_qss(t, accent="err"))
            self.typed.textChanged.connect(self._on_typed)
            box.addWidget(self.typed)

        # 按钮行：危险在右，取消在左
        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 0)
        row.setSpacing(10)
        row.addStretch(1)
        self.btn_cancel = Btn(cancel_label, t, "ghost")
        self.btn_cancel.clicked.connect(self._cancel)
        row.addWidget(self.btn_cancel)
        self.btn_ok = Btn(confirm_label, t, "danger" if dangerous else "primary")
        self.btn_ok.clicked.connect(self._ok)
        row.addWidget(self.btn_ok)
        box.addLayout(row)

        if typed_word:
            self.btn_ok.setEnabled(False)

        self._center_on(parent)

        # 默认焦点给"取消" —— 手快点两下回车不会误确认
        self.btn_cancel.setFocus()

    # ------------------------------------------------------------ 外观

    def _apply_shell(self, f: QFrame) -> None:
        # 底色统一走 stylekit_qt.surface_bg —— 浮层实心这一口径只留一个实现点，
        # 别在这里再手抄一遍（此前手抄散了两份，`onboarding` 就漏了）。
        t = self.t
        f.setObjectName("Shell")
        f.setStyleSheet(
            f"#Shell{{background:{surface_bg(t)};border:1px solid {t.bd};"
            f"border-radius:{t.radius_card + 2}px;}}"
        )

    def _center_on(self, parent: QWidget) -> None:
        self.adjustSize()
        if parent is not None and parent.isVisible():
            g = parent.frameGeometry()
            self.move(g.center() - self.rect().center())
        else:
            self.move(320, 260)

    def showEvent(self, e): # noqa: N802
        # 拖拽在**第一次显示后**才启用：构造期 adjustSize/_center_on 会改几何，
        # 此时挂上没意义；显示后一句话装上（DraggableDialog 的 installEventFilter）。
        super().showEvent(e)
        self.enable_drag()

    # ------------------------------------------------------------ 行为

    def _on_typed(self, s: str) -> None:
        self.btn_ok.setEnabled(s.strip() == self._typed_word)

    def _ok(self) -> None:
        self.result_ok = True
        self.decided.emit(True)
        self.accept()

    def _cancel(self) -> None:
        self.result_ok = False
        self.decided.emit(False)
        self.reject()

    def keyPressEvent(self, e): # noqa: N802
        # Esc 一定是取消 —— 危险操作不许用 Esc 确认
        if e.key() == Qt.Key.Key_Escape:
            self._cancel()
            return
        super().keyPressEvent(e)


class InfoDialog(DraggableDialog, QDialog):
    """**单按钮原生提示** —— 替掉系统 `QMessageBox`（硬规矩第 1 条：0 系统 MessageBox）。

    与 `ConfirmDialog` 同一套外壳与排版（自绘圆角卡 + 可拖动 + 居中于父窗），
    区别只是"没有要不要"这一问：只有一颗「知道了」。
    """

    def __init__(self, t: Tokens, parent: QWidget, title: str, body: str,
                 ok_label: str = "知道了"):
        super().__init__(parent)
        self.t = t
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        shell = QFrame()
        self._apply_shell(shell)
        outer.addWidget(shell)

        box = QVBoxLayout(shell)
        box.setContentsMargins(24, 22, 24, 18)
        box.setSpacing(12)

        tl = QLabel(title)
        tl.setFont(qfont(t, 16, 600))
        tl.setStyleSheet(f"color:{t.tx};background:transparent;")
        box.addWidget(tl)

        bl = QLabel(_clean(body))
        bl.setFont(qfont(t, t.body_size))
        bl.setWordWrap(True)
        # 同 `ConfirmDialog`：wordWrap 在自动尺寸下会被压扁 ⇒ 按字数估高，宁可多留一行
        bl.setMinimumHeight(_wrap_h(_clean(body), t.body_size, 320))
        bl.setStyleSheet(f"color:{t.tx2};background:transparent;")
        box.addWidget(bl)

        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 0)
        row.addStretch(1)
        self.btn_ok = Btn(ok_label, t, "primary")
        self.btn_ok.clicked.connect(self.accept)
        row.addWidget(self.btn_ok)
        box.addLayout(row)

        self._center_on(parent)
        self.btn_ok.setFocus()

    _apply_shell = ConfirmDialog._apply_shell
    _center_on = ConfirmDialog._center_on

    def showEvent(self, e): # noqa: N802
        super().showEvent(e)
        self.enable_drag()


class ChoiceDialog(DraggableDialog, QDialog):
    """多选一「拍板」弹窗（web `choiceBox` 的 Qt 版）—— 版本不匹配时的四选一就用它。

    口径与 web 一致：每个选项一颗按钮（`label` 为主、`detail` 是小字说明），
    底部一颗「先不决定」。选中把选项 `key` 放进 `self.choice`（空串＝没选/关掉）。

    ⛔ 为什么值得单独做一个：这件事**以前只能去网页控制台做**（Qt 侧只给了一个"去网页"的入口）
    —— 用户口径是"网页能做的都得在 Qt 做"，所以四选一必须落在原生弹窗里。
    """

    decided = Signal(str)

    def __init__(
        self,
        t: Tokens,
        parent: QWidget,
        title: str,
        lines: list[str],
        options: list,
        cancel_label: str = "先不决定",
    ):
        super().__init__(parent)
        self.t = t
        self.choice = ""
        # 选项 key 清单（判据按它断言"弹出来的是台账里那几个选项"，不靠 OCR/文案）
        self.choice_keys = [str(o.get("key")) for o in (options or [])
                            if isinstance(o, dict) and str(o.get("key") or "")]

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        shell = QFrame()
        self._apply_shell(shell)
        outer.addWidget(shell)

        box = QVBoxLayout(shell)
        box.setContentsMargins(24, 22, 24, 18)
        box.setSpacing(10)

        tl = QLabel(_clean(title))
        tl.setFont(qfont(t, 16, 600))
        tl.setStyleSheet(f"color:{t.tx};background:transparent;")
        box.addWidget(tl)

        for ln in (lines or []):
            il = QLabel(_clean(str(ln)))
            il.setFont(qfont(t, t.body_size))
            il.setWordWrap(True)
            il.setMinimumHeight(_wrap_h(_clean(str(ln)), t.body_size, 320))
            il.setStyleSheet(f"color:{t.tx2};background:transparent;")
            box.addWidget(il)

        box.addSpacing(4)
        for i, opt in enumerate(options or []):
            try:
                key = str(opt.get("key") or "")
                label = str(opt.get("label") or key)
                detail = str(opt.get("detail") or "")
            except Exception: # noqa: BLE001
                continue
            if not key:
                continue
            b = Btn(label, t, "primary" if i == 0 else "ghost")
            b.setObjectName("choice_%s" % key)
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            box.addWidget(b)
            if detail:
                dl = QLabel(_clean(detail))
                dl.setFont(qfont(t, t.body_size - 1.5))
                dl.setWordWrap(True)
                dl.setMinimumHeight(_wrap_h(_clean(detail), t.body_size - 1.5, 330))
                dl.setStyleSheet(f"color:{t.tx3};background:transparent;")
                box.addWidget(dl)

        box.addSpacing(2)
        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 0)
        row.addStretch(1)
        self.btn_cancel = Btn(cancel_label, t, "ghost")
        self.btn_cancel.clicked.connect(self._cancel)
        row.addWidget(self.btn_cancel)
        box.addLayout(row)

        self._center_on(parent)
        self.btn_cancel.setFocus() # 默认焦点在"先不决定"：连按回车不会误拍板

    _apply_shell = ConfirmDialog._apply_shell
    _center_on = ConfirmDialog._center_on

    def _pick(self, key: str) -> None:
        self.choice = str(key)
        self.decided.emit(self.choice)
        self.accept()

    def _cancel(self) -> None:
        self.choice = ""
        self.decided.emit("")
        self.reject()

    def keyPressEvent(self, e): # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self._cancel()
            return
        super().keyPressEvent(e)

    def showEvent(self, e): # noqa: N802
        super().showEvent(e)
        self.enable_drag()


# ---------------------------------------------------------------- 自检


def _selftest() -> list[tuple[str, bool, str]]:
    """不需要真窗口的行为自检 —— 只验我们**写在代码里的纪律**是否还在。

    真弹窗的观感要靠截图（本目录的 `shoot.py`），这里只防"纪律被改掉"。
    """
    import inspect # noqa: PLC0415

    from stylekit_qt import THEMES # noqa: PLC0415

    src = inspect.getsource(inspect.getmodule(_selftest))
    rows: list[tuple[str, bool, str]] = []

    # 1) 硬规矩第 1 条：0 系统 MessageBox
    #    这条必须守住 —— 一旦混进 QMessageBox，弹窗族的外观就有了两个来源
    mod = inspect.getsource(__import__(__name__))
    rows.append(("没有用系统 MessageBox（硬规矩 1）", "QMessageBox" not in mod, ""))

    # 2) 默认焦点必须是"取消"
    rows.append(("默认焦点在『取消』而不是确认", "self.btn_cancel.setFocus()" in mod, ""))

    # 3) Esc 必须是取消
    rows.append(("Esc 键走取消路径", "Key_Escape" in mod and "_cancel()" in mod, ""))

    # 4) 三套主题都要能建弹窗（外观 token 齐全）
    for k, t in THEMES.items():
        need = ["bg", "card", "bd", "tx", "tx2", "err", "glass", "radius_card", "radius_btn"]
        missing = [n for n in need if not hasattr(t, n)]
        rows.append((f"主题 {k} 的弹窗 token 齐全", not missing, ",".join(missing)))

    # 5) whale 之外不许有玻璃（三套差异要真的拉开，不能都玻璃）
    glassy = [k for k, t in THEMES.items() if t.glass]
    rows.append(("只有 whale 走玻璃质感", glassy == ["whale"], ",".join(glassy)))

    return rows


if __name__ == "__main__":
    rows = _selftest()
    bad = [r for r in rows if not r[1]]
    for name, ok, extra in rows:
        print(("  OK  " if ok else "  FAIL") + "  " + name + (("   [" + extra + "]") if extra else ""))
    print()
    print(f"{len(rows) - len(bad)} 通过 / {len(bad)} 失败")
    raise SystemExit(1 if bad else 0)
