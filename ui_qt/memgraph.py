# -*- coding: utf-8 -*-
"""记忆图谱 —— Qt 端**自绘**的力导向关系图（web `mgCanvas` 的等价物）。

为什么要 Qt 自绘：web 那边用 `<canvas>` + JS 画（`assets/console/index.html` 的 ⑧ 段）；
Qt 面板是从同一份 HTML 解析生成的，**canvas 不会被解析成控件**（`sec_meta` 只认
input/select/textarea/button）⇒ Qt 侧必须自己画一份，否则用户在桌面端只看到一行文字。

取数与构图全在后端 `agent/memory_graph.py`（本机生成、零 token）；本模块**只负责画**，
数据从 `/api/memory/graph` 拿。交互与 web 对齐：拖节点、滚轮缩放、空白拖动平移。

与既有自绘件（`pm_wheel` / `confirm` / `popover`）同一套做法：QWidget + paintEvent，
力导向用 QTimer 迭代（不引第三方布局引擎）。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

sys.path.insert(0, str(Path(__file__).resolve().parent)) # 嵌入式 runtime._pth 不放脚本目录
from stylekit_qt import Tokens # noqa: E402

#: 三类节点的颜色（与 web 的 MG_COLOR 同一套色：成员蓝 / 会话绿 / 话题黄）
COLORS = {"person": "#4FB3F2", "chat": "#35F0C0", "topic": "#FFD166"}
_FALLBACK = "#8899A6"

_REP = 5200.0    # 点与点的排斥强度（与 web mgStep 同量级）
_K = 0.045       # 边的弹簧系数
_DAMP = 0.86     # 速度阻尼
_TICKS = 240     # 收敛用帧数上限（跑满即停，不无限烧 CPU）


class MemoryGraphView(QWidget):
    """力导向关系图。`set_data(payload)` 灌 `{nodes, links}`；空数据画一行说明。"""

    def __init__(self, t: Tokens, parent=None) -> None:
        super().__init__(parent)
        self.t = t
        self._nodes: list = []
        self._links: list = []
        self._by_id: dict = {}
        self._scale = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._drag = None
        self._pan_start = None
        self._tick_n = 0
        self._empty_note = "还没画 —— 点上面「画关系图」"
        self.setMinimumHeight(360)
        self.setMouseTracking(True)
        self.setCursor(Qt.OpenHandCursor)
        self._timer = QTimer(self)
        self._timer.setInterval(16) # ≈ rAF
        self._timer.timeout.connect(self._step)

    # ── 数据 ──────────────────────────────────────────────
    def set_data(self, payload: dict) -> None:
        """灌一份 `/api/memory/graph` 的返回。空图 ⇒ 如实显示说明，不画假骨架。"""
        d = payload if isinstance(payload, dict) else {}
        nodes = d.get("nodes") if isinstance(d.get("nodes"), list) else []
        links = d.get("links") if isinstance(d.get("links"), list) else []
        self._nodes = [dict(n) for n in nodes if isinstance(n, dict)]
        self._links = [dict(l) for l in links if isinstance(l, dict)]
        self._by_id = {n.get("id"): n for n in self._nodes}
        self._empty_note = str(d.get("note") or "还没有任何记忆 —— 等它跟群里聊几轮再来看")
        if not self._nodes:
            self._timer.stop()
            self.update()
            return
        self._init_positions()
        self._tick_n = 0
        self._timer.start()

    def _init_positions(self) -> None:
        """初始位置：按类型摊在不同半径的圈上（比全从原点散开收敛快得多）。"""
        n = max(1, len(self._nodes))
        w = max(400.0, float(self.width() or 900))
        h = max(280.0, float(self.height() or 480))
        for i, d in enumerate(self._nodes):
            ring = 0.62 if d.get("type") == "chat" else (0.85 if d.get("type") == "person" else 1.12)
            a = (i / n) * math.pi * 2
            d["x"] = w / 2 + math.cos(a) * ring * w * 0.33
            d["y"] = h / 2 + math.sin(a) * ring * h * 0.33
            d["vx"] = 0.0
            d["vy"] = 0.0
            try:
                imp = float(d.get("importance") or 0)
            except Exception: # noqa: BLE001
                imp = 0.0
            d["r"] = 6.0 + max(0.0, min(1.0, imp)) * 22.0

    # ── 力导向迭代 ────────────────────────────────────────
    def _step(self) -> None:
        nodes, links = self._nodes, self._links
        if not nodes:
            self._timer.stop()
            return
        w = max(400.0, float(self.width() or 900))
        h = max(280.0, float(self.height() or 480))
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                a, b = nodes[i], nodes[j]
                dx = b["x"] - a["x"]
                dy = b["y"] - a["y"]
                d2 = dx * dx + dy * dy
                if d2 < 0.01:
                    d2 = 0.01
                d = math.sqrt(d2)
                f = _REP / d2
                ux, uy = dx / d, dy / d
                a["vx"] -= ux * f
                a["vy"] -= uy * f
                b["vx"] += ux * f
                b["vy"] += uy * f
        for l in links:
            a = self._by_id.get(l.get("source"))
            b = self._by_id.get(l.get("target"))
            if not a or not b:
                continue
            dx = b["x"] - a["x"]
            dy = b["y"] - a["y"]
            d = math.sqrt(dx * dx + dy * dy) or 0.01
            try:
                wt = float(l.get("weight") or 1.0)
            except Exception: # noqa: BLE001
                wt = 1.0
            want = 130.0 + 60.0 * (1.0 - min(1.0, wt) / 3.0)
            f = (d - want) * _K * 0.5
            ux, uy = dx / d, dy / d
            a["vx"] += ux * f
            a["vy"] += uy * f
            b["vx"] -= ux * f
            b["vy"] -= uy * f
        for d in nodes:
            d["vx"] += (w / 2 - d["x"]) * 0.0035
            d["vy"] += (h / 2 - d["y"]) * 0.0035
            d["vx"] *= _DAMP
            d["vy"] *= _DAMP
            d["x"] += max(-18.0, min(18.0, d["vx"]))
            d["y"] += max(-18.0, min(18.0, d["vy"]))
        self._tick_n += 1
        if self._tick_n >= _TICKS:
            self._timer.stop() # 收敛即停：不无限烧 CPU
        self.update()

    # ── 交互 ──────────────────────────────────────────────
    def _to_world(self, pos) -> QPointF:
        return QPointF((pos.x() - self._pan.x()) / self._scale,
                       (pos.y() - self._pan.y()) / self._scale)

    def _hit(self, world: QPointF):
        for d in reversed(self._nodes):
            dx = d["x"] - world.x()
            dy = d["y"] - world.y()
            r = d["r"] + 4
            if dx * dx + dy * dy <= r * r:
                return d
        return None

    def mousePressEvent(self, ev) -> None: # noqa: N802
        if ev.button() != Qt.LeftButton:
            return super().mousePressEvent(ev)
        hit = self._hit(self._to_world(ev.position()))
        if hit is not None:
            self._drag = hit
            self.setCursor(Qt.ClosedHandCursor)
        else:
            self._pan_start = (ev.position(), QPointF(self._pan))
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, ev) -> None: # noqa: N802
        if self._drag is not None:
            p = self._to_world(ev.position())
            self._drag["x"] = p.x()
            self._drag["y"] = p.y()
            self._drag["vx"] = 0.0
            self._drag["vy"] = 0.0
            self.update()
        elif self._pan_start is not None:
            start, base = self._pan_start
            self._pan = QPointF(base.x() + (ev.position().x() - start.x()),
                                base.y() + (ev.position().y() - start.y()))
            self.update()

    def mouseReleaseEvent(self, ev) -> None: # noqa: N802
        self._drag = None
        self._pan_start = None
        self.setCursor(Qt.OpenHandCursor)

    def wheelEvent(self, ev) -> None: # noqa: N802
        step = 1.1 if ev.angleDelta().y() > 0 else 0.9
        self._scale = max(0.3, min(3.0, self._scale * step))
        self.update()

    # ── 绘制 ──────────────────────────────────────────────
    def paintEvent(self, _ev) -> None: # noqa: N802
        t = self.t
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        rect = QRectF(self.rect())
        p.fillRect(rect, QColor(t.card))
        if not self._nodes:
            p.setPen(QPen(QColor(t.tx3)))
            p.setFont(QFont(getattr(t, "font_family", ""), 10))
            p.drawText(rect, Qt.AlignCenter, self._empty_note or "还没有记忆")
            return
        p.translate(self._pan)
        p.scale(self._scale, self._scale)
        # 边
        for l in self._links:
            a = self._by_id.get(l.get("source"))
            b = self._by_id.get(l.get("target"))
            if not a or not b:
                continue
            same = (l.get("relation") == "同群")
            pen = QPen(QColor(t.tx3 if same else t.bd))
            try:
                w = float(l.get("weight") or 1.0)
            except Exception: # noqa: BLE001
                w = 1.0
            pen.setWidthF(max(0.6, min(3.0, w * 0.8)))
            if same:
                pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(a["x"], a["y"]), QPointF(b["x"], b["y"]))
            if l.get("relation"):
                p.setPen(QPen(QColor(t.tx3)))
                f = QFont()
                f.setPointSize(7)
                p.setFont(f)
                midx = (a["x"] + b["x"]) / 2
                midy = (a["y"] + b["y"]) / 2
                p.drawText(QRectF(midx - 30, midy - 9, 60, 14),
                           Qt.AlignCenter, str(l.get("relation")))
        # 点 + 名字
        f = QFont()
        f.setPointSize(9)
        p.setFont(f)
        for d in self._nodes:
            col = QColor(COLORS.get(d.get("type"), _FALLBACK))
            p.setBrush(QBrush(col))
            p.setPen(QPen(QColor(t.tx3), 1.4))
            r = float(d["r"])
            p.drawEllipse(QPointF(d["x"], d["y"]), r, r)
            p.setPen(QPen(QColor(t.tx)))
            nm = str(d.get("name") or "")
            if len(nm) > 8:
                nm = nm[:8] + "…"
            p.drawText(QRectF(d["x"] - 45, d["y"] + r + 1, 90, 14),
                       Qt.AlignHCenter, nm)
