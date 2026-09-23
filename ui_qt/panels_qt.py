# -*- coding: utf-8 -*-
"""通用面板构建器 —— 输入 sec 键，产出对齐 web 版式的 QWidget。

生成思路与 web 侧同源（交接件硬规矩）：
  web 侧控制台的配置行带 `data-cfg="点.path"`、控件类型写在 HTML 标签上；
  本文件不手抄任何配置项 —— 运行时从 `sec_meta.py`（解析 console_html.py）
  拿行元数据，按「键类型 → 控件类型」映射批量生成：
      text/password/number/range → QLineEdit（password 打码）
      checkbox                   → Switch（widgets 自绘开关）
      select                     → Combo（样式化下拉）
      textarea                   → 多行输入
      chips / info               → 说明行（原型不接群检测，如实说明）
  默认值按点路径回填自 config.example.json（web data-cfg 同源）。

非配置型面板（概览/体检/明细/日志/配置格式）在 panels_custom.py 手写，
build_panel 查 MANUAL 表分发。逻辑下沉，shell.py 只做装配。
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import panels_custom
import sec_meta
from stylekit_qt import Tokens, qfont, rgba
from widgets import Badge, Btn, Card, Field, Switch, desc, h2

# 本批要建面板的 26 个 sec（27 减去机器人=主面板），顺序照 web 文档序
BATCH_SECS = sec_meta.SECS_OF_THIS_BATCH


# ---------------------------------------------------------------- 基础控件


def _hex(c) -> str:
    return c.name(QColor.NameFormat.HexArgb)


class Combo(QComboBox):
    """样式化下拉 —— 系统下拉在深色主题下很脏，QSS 全覆盖（shell._Combo 同款）。"""

    def __init__(self, t: Tokens, options: list[tuple[str, str]], default=None, parent=None):
        super().__init__(parent)
        self.t = t
        for _v, label in options:
            self.addItem(label)
        values = [v for v, _l in options]
        if default is not None and str(default) in values:
            self.setCurrentIndex(values.index(str(default)))
        self.setFixedHeight(32)
        self.setMinimumWidth(200)
        self.setFont(qfont(t, t.body_size))
        self.setStyleSheet(
            f"QComboBox{{background:{_hex(rgba(t.q('tx'), 0 if t.glass else 16))};"
            f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:0 10px;}}"
            f"QComboBox::drop-down{{border:none;width:22px;}}"
            f"QComboBox QAbstractItemView{{background:{'#0E2136' if t.glass else t.card};"
            f"color:{t.tx};border:1px solid {t.bd};selection-background-color:{t.blue_soft};}}"
        )


def _line(t: Tokens, text: str = "", password: bool = False, placeholder: str = "") -> QLineEdit:
    e = QLineEdit(text)
    e.setPlaceholderText(placeholder)
    e.setFont(qfont(t, t.body_size))
    e.setFixedHeight(32)
    e.setMinimumWidth(220)
    if password:
        e.setEchoMode(QLineEdit.EchoMode.Password)
    e.setStyleSheet(
        f"QLineEdit{{background:{_hex(rgba(t.q('tx'), 0 if t.glass else 16))};"
        f"color:{t.tx};border:1px solid {t.bd};"
        f"border-radius:{t.radius_btn}px;padding:0 10px;}}"
        f"QLineEdit:focus{{border:1px solid {t.blue};}}"
    )
    return e


def _area(t: Tokens, text: str, rows: int = 2, placeholder: str = "") -> QPlainTextEdit:
    e = QPlainTextEdit(text)
    e.setPlaceholderText(placeholder)
    e.setFont(qfont(t, t.body_size - 0.5))
    e.setFixedHeight(20 * max(2, min(rows, 4)) + 20)
    e.setStyleSheet(
        f"QPlainTextEdit{{background:{_hex(rgba(t.q('tx'), 0 if t.glass else 16))};"
        f"color:{t.tx};border:1px solid {t.bd};border-radius:{t.radius_btn}px;padding:4px 8px;}}"
        f"QPlainTextEdit:focus{{border:1px solid {t.blue};}}"
    )
    return e


def _divider(t: Tokens) -> QFrame:
    d = QFrame()
    d.setFixedHeight(1)
    d.setStyleSheet(f"background:{t.bd};border:none;")
    return d


def _as_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, dict)):
        # 空容器回空串 —— 显示裸 "[]"/"{}" 不如让 placeholder 说明格式
        return json.dumps(v, ensure_ascii=False) if v else ""
    return str(v)


# ---------------------------------------------------------------- 行生成


def _row(t: Tokens, r: "sec_meta.Row", card: Card) -> QWidget:
    """一行元数据 → 一行 Field。键类型 → 控件类型的唯一映射点。"""
    c: QWidget | None = None
    if r.kind == "checkbox":
        c = Switch(t, bool(r.default))
    elif r.kind == "select":
        c = Combo(t, r.options, r.default)
    elif r.kind == "textarea":
        c = _area(t, _as_text(r.default), rows=3, placeholder=r.placeholder)
    elif r.kind in ("text", "password", "number", "range"):
        c = _line(t, _as_text(r.default), password=r.kind == "password", placeholder=r.placeholder)
    # info / chips → 纯说明行（不伪造一个接不了后台的控件）
    return Field(t, r.label, r.hint, c, card)


def _cfg_panel(t: Tokens, s: "sec_meta.Sec") -> QWidget:
    """配置型面板：头部 + 一张设置卡（行 × 分隔线）+ 保存行。"""
    page = QWidget()
    page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    lay = QVBoxLayout(page)
    lay.setContentsMargins(28, 24, 28, 24)
    lay.setSpacing(14)
    lay.addWidget(h2(t, s.title, Badge(t, "idle", "读取中")))
    if s.desc:
        lay.addWidget(desc(t, s.desc))

    card = Card(t)
    for i, r in enumerate(s.rows):
        if i:
            card.body.addWidget(_divider(t))
        card.body.addWidget(_row(t, r, card))
    lay.addWidget(card)

    brow = QHBoxLayout()
    brow.addWidget(Btn("保存设置", t, "primary"))
    note = QLabel("原型不落盘 —— 只演示控件与版式")
    note.setFont(qfont(t, 12.5))
    note.setStyleSheet(f"color:{t.tx3};background:transparent;")
    brow.addWidget(note, 0, Qt.AlignmentFlag.AlignVCenter)
    brow.addStretch(1)
    lay.addLayout(brow)
    lay.addStretch(1)
    return page


# ---------------------------------------------------------------- 入口


def build_panel(t: Tokens, sec: str) -> QWidget:
    """sec 键 → 面板 QWidget（内容包在透明滚动区里，长面板可滚）。

    分发：web 侧非配置行形态的 5 个 sec（概览/体检/明细/日志/配置格式）
    走 panels_custom.MANUAL 手写件；其余全部元数据驱动生成。
    """
    fn = panels_custom.MANUAL.get(sec)
    if fn is not None:
        inner = fn(t)
    else:
        inner = _cfg_panel(t, sec_meta.get(sec))
    wrap = QScrollArea()
    wrap.setWidgetResizable(True)
    wrap.setFrameShape(QFrame.Shape.NoFrame)
    wrap.setStyleSheet("QScrollArea{background:transparent;border:none;}")
    wrap.setWidget(inner)
    return wrap
