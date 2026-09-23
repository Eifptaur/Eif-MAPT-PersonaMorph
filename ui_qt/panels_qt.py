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
import time

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

import config_io
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
        for v, label in options:
            self.addItem(label, v)      # userData=option 的真 value（web data-cfg 口径）
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


# 该行不参与保存（info 说明行 / 数字留空或格式不对）—— 与"没填就不改"的 web 口径一致
_SKIP = object()


def _ctrl_value(r: "sec_meta.Row", ctrl: QWidget | None):
    """按行类型从控件取值（web 保存前同款类型化：数字 parseFloat、开关 bool）。"""
    if ctrl is None or not r.cfg:
        return _SKIP
    if r.kind == "checkbox":
        return bool(ctrl.isChecked())
    if r.kind in ("number", "range"):
        s = ctrl.text().strip()
        if not s:
            return _SKIP                      # 留空 = 不改这一项
        try:
            f = float(s)
        except ValueError:
            return _SKIP                      # 格式不对不改，保存反馈里如实说
        return int(f) if f == int(f) else f
    if r.kind == "select":
        return str(ctrl.currentData())
    if r.kind == "textarea":
        return ctrl.toPlainText()
    return ctrl.text()


# ---------------------------------------------------------------- 行生成


def _row(t: Tokens, r: "sec_meta.Row", card: Card, binds: list | None = None) -> QWidget | None:
    """一行元数据 → 一行 Field。键类型 → 控件类型的唯一映射点。

    初值口径（丙-4 接线）：**真 config 当前值优先**（同进程 get_config，网页侧
    改过即拿到新值），键缺失才回 config.example.json 默认（sec_meta 解析层）。
    """
    cur = config_io.read_path(r.cfg) if r.cfg else None
    default = r.default if cur is None else cur
    c: QWidget | None = None
    if r.kind == "checkbox":
        c = Switch(t, bool(default))
    elif r.kind == "select":
        c = Combo(t, r.options, default)
    elif r.kind == "textarea":
        c = _area(t, _as_text(default), rows=3, placeholder=r.placeholder)
    elif r.kind in ("text", "password", "number", "range"):
        c = _line(t, _as_text(default), password=r.kind == "password", placeholder=r.placeholder)
    # info / chips → 纯说明行（不伪造一个接不了后台的控件）
    f = Field(t, r.label, r.hint, c, card)
    if c is not None and binds is not None:
        binds.append((r, c))
    return f


def _cursor_extras(t: Tokens, on_save) -> Card:
    """光标面板的「上传自定义图 / 重置默认」卡（web 同款文件语义）。"""
    import shutil  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

    root = Path(__file__).resolve().parents[1]
    card = Card(t)
    fb = QLabel("")
    fb.setFont(qfont(t, 12.5))
    fb.setStyleSheet(f"color:{t.tx3};background:transparent;")
    fb.setWordWrap(True)

    def _after(ok: bool, msg: str) -> None:
        fb.setText(("已生效：" if ok else "没生效：") + msg)
        fb.setStyleSheet(f"color:{t.ok if ok else t.err};background:transparent;")
        if ok and callable(on_save):
            try:
                on_save()
            except Exception:  # noqa: BLE001
                pass

    def _upload() -> None:
        p, _flt = QFileDialog.getOpenFileName(card, "选一张光标图", "", "图片 (*.png *.jpg *.jpeg)")
        if not p:
            return
        try:
            if Path(p).stat().st_size > 8 * 1024 * 1024:
                _after(False, "图片超过 8MB（web 同款上限），换小一点的")
                return
            shutil.copyfile(p, root / "assets" / "custom-cursor.png")
            ok, msg = config_io.write_patch({"ui.cursor_image": "custom", "ui.whale_cursor": True})
            _after(ok, msg + "（新光标立即生效，点头/中键旋转同样可用）")
        except Exception as e:  # noqa: BLE001
            _after(False, str(e))

    def _reset() -> None:
        try:
            for name in ("custom-cursor.png", "custom-cursor-nod.png"):
                (root / "assets" / name).unlink(missing_ok=True)
            ok, msg = config_io.write_patch({"ui.cursor_image": ""})
            _after(ok, (msg + "，已重置为默认鲸鱼") if ok else msg)
        except Exception as e:  # noqa: BLE001
            _after(False, str(e))

    row = QHBoxLayout()
    b_up = Btn("上传自定义光标图…", t, "ghost")
    b_up.clicked.connect(_upload)
    b_rs = Btn("重置为默认鲸鱼", t, "ghost")
    b_rs.clicked.connect(_reset)
    row.addWidget(b_up)
    row.addWidget(b_rs)
    row.addStretch(1)
    card.body.addWidget(h2(t, "自定义图片"))
    card.body.addLayout(row)
    card.body.addWidget(fb)
    return card


def _cfg_panel(t: Tokens, s: "sec_meta.Sec", on_save=None) -> QWidget:
    """配置型面板：头部 + 一张设置卡（行 × 分隔线）+ 保存行。

    保存口径（丙-4 接线）：收集本卡全部可编辑行的 {点路径: 值}，走
    `config_io.write_patch`（与网页控制台 /api/config 同款深合并落盘），
    成败都在行内如实反馈；on_save 供 Shell 做即时联动（光标/主题/画卷）。
    """
    page = QWidget()
    page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    lay = QVBoxLayout(page)
    lay.setContentsMargins(28, 24, 28, 24)
    lay.setSpacing(14)
    lay.addWidget(h2(t, s.title, Badge(t, "idle", "读取中")))
    if s.desc:
        lay.addWidget(desc(t, s.desc))

    card = Card(t)
    binds: list[tuple["sec_meta.Row", QWidget]] = []
    for i, r in enumerate(s.rows):
        if i:
            card.body.addWidget(_divider(t))
        f = _row(t, r, card, binds)
        if f is not None:
            card.body.addWidget(f)
    lay.addWidget(card)

    # 光标面板专属（对齐 web /api/cursor/upload · /api/cursor/reset）：
    # 上传把图拷成 assets/custom-cursor.png 并把 ui.cursor_image 记成 custom；
    # 重置删掉自定义文件并清空配置键 —— 与 step-1 光标本体的回退链正好咬合。
    if s.key == "cursor":
        lay.addWidget(_cursor_extras(t, on_save))

    brow = QHBoxLayout()
    btn = Btn("保存设置", t, "primary")
    note = QLabel("改完点保存 → 写入 config.json（与网页控制台同一份）")
    note.setFont(qfont(t, 12.5))
    note.setStyleSheet(f"color:{t.tx3};background:transparent;")

    def _do_save() -> None:
        patch: dict[str, object] = {}
        skipped = 0
        for r, ctrl in binds:
            v = _ctrl_value(r, ctrl)
            if v is _SKIP:
                if ctrl is not None:
                    skipped += 1          # 有控件但留空/格式不对 → 计数如实说
                continue
            patch[r.cfg] = v
        ok, msg = config_io.write_patch(patch)
        if ok and skipped:
            msg += f"（{skipped} 行留空或格式不对，未改动）"
        note.setText(("已保存：" if ok else "没保存成：") + msg + f"  ·  {time.strftime('%H:%M:%S')}")
        note.setStyleSheet(f"color:{t.ok if ok else t.err};background:transparent;")
        if ok and callable(on_save):
            try:
                on_save()
            except Exception:  # noqa: BLE001
                pass

    btn.clicked.connect(_do_save)
    brow.addWidget(btn)
    brow.addWidget(note, 0, Qt.AlignmentFlag.AlignVCenter)
    brow.addStretch(1)
    lay.addLayout(brow)
    lay.addStretch(1)
    return page


# ---------------------------------------------------------------- 入口


def build_panel(t: Tokens, sec: str, on_save=None) -> QWidget:
    """sec 键 → 面板 QWidget（内容包在透明滚动区里，长面板可滚）。

    分发：web 侧非配置行形态的 5 个 sec（概览/体检/明细/日志/配置格式）
    走 panels_custom.MANUAL 手写件；其余全部元数据驱动生成。
    on_save：保存成功后的联动回调（Shell 传入，做光标/主题/画卷即时生效）。
    """
    fn = panels_custom.MANUAL.get(sec)
    if fn is not None:
        inner = fn(t, on_save) if sec == "json" else fn(t)
    else:
        inner = _cfg_panel(t, sec_meta.get(sec), on_save)
    wrap = QScrollArea()
    wrap.setWidgetResizable(True)
    wrap.setFrameShape(QFrame.Shape.NoFrame)
    wrap.setStyleSheet("QScrollArea{background:transparent;border:none;}")
    wrap.setWidget(inner)
    return wrap
