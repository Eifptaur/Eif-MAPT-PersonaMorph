# -*- coding: utf-8 -*-
"""右下角鲸鱼挂件 —— 上游 DeepSeek-Balance-Whale-Widget 的 Qt 等价物。

web 侧挂件是 whale-widget/client/widget.js（在浏览器 DOM 里跑，宿主 agent/whale.py
按 /dsh-whale/* 接口供数）。Qt 无 DOM，这里用无边框置顶悬浮窗重做核心体验
（产品自检清单第 9 项验收口径：余额 / 今日已用 / 每轮消耗，数据变化、点击刷新、可拖拽）。

数据面直接复用现成接口（agent/whale.py）：
  GET /dsh-whale/balance.json    {ok,totalBalance,currency,todayUsage,isPeak}
  GET /dsh-whale/last-turn.json  {ok,seq,turn,amount,tokens}
轮询 30s（对齐清单 loadBalance 30s 口径）；拖动位置记 QSettings，重开还原。

与 web 挂件的明示差异：静态鲸鱼图 + 数据（不搬 gif/音效/果冻动画等浏览器特效）；
挂件显示真实数据，不受顶栏「余额显示」模式（隐藏/改数字）影响（web 同款：两者独立）。
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QPoint, QSettings, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, # noqa: PLC0415
                               QVBoxLayout, QWidget)

ROOT = Path(__file__).resolve().parent.parent
_ASSET = ROOT / "whale-widget" / "assets" / "DSniang1.png"
_SET = ("WXAgent", "persona-morph-ui")


class WhaleWidget(QWidget):
    """无边框置顶小窗：鲸鱼图 + 余额/今日已用/每轮消耗；点击刷新、拖拽移动。"""

    W, H = 208, 116

    def __init__(self, t, parent=None): # noqa: ANN001
        super().__init__(parent)
        self.setObjectName("WhaleWidget")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self.W, self.H)
        self._drag0: QPoint | None = None
        self._win0: QPoint | None = None
        self._moved = False
        self._busy = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame()
        self.card.setObjectName("WhaleCard")
        outer.addWidget(self.card)
        h = QHBoxLayout(self.card)
        h.setContentsMargins(10, 8, 12, 8)
        h.setSpacing(9)
        self.pic = QLabel()
        self.pic.setFixedSize(64, 64)
        try:
            pm = QPixmap(str(_ASSET))
            if not pm.isNull():
                self.pic.setPixmap(pm.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation))
        except Exception: # noqa: BLE001 — 素材缺失时留空图，数据照常显示
            pass
        h.addWidget(self.pic)
        v = QVBoxLayout()
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        self.bal_lb = QLabel("余额 …")
        self.today_lb = QLabel("今日已用 …")
        self.last_lb = QLabel("每轮消耗 …")
        self.peak_lb = QLabel("")
        for lb in (self.bal_lb, self.today_lb, self.last_lb, self.peak_lb):
            lb.setWordWrap(True)
        self.bal_lb.setToolTip("点击挂件刷新；拖动可换位置")
        self.pic.setToolTip("点击刷新余额；按住拖动换位置")
        v.addWidget(self.bal_lb)
        v.addWidget(self.today_lb)
        v.addWidget(self.last_lb)
        v.addWidget(self.peak_lb)
        h.addLayout(v, 1)

        self.restyle(t)

        # 位置：上次拖到哪就还在哪；没有记录落右下角
        pos = QSettings(*_SET).value("whale_pos")
        moved = False
        if isinstance(pos, list) and len(pos) == 2:
            try:
                self.move(int(pos[0]), int(pos[1]))
                moved = True
            except (TypeError, ValueError):
                moved = False
        if not moved:
            self._move_default()

        self._timer = QTimer(self)
        self._timer.setInterval(30000) # 清单 loadBalance 30s 口径
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        QTimer.singleShot(600, self.refresh) # 进窗即拉一次

    # ------------------------------------------------------------ 外观

    def restyle(self, t) -> None: # noqa: ANN001
        """主题切换时刷新样式（挂件是独立顶层窗，不走 Shell 的 QSS 重建）。"""
        from stylekit_qt import qfont # noqa: PLC0415

        self.t = t
        self.card.setStyleSheet(
            f"#WhaleCard{{background:{t.card};border:1px solid {t.bd};border-radius:14px;}}")
        self.bal_lb.setFont(qfont(t, 15, 600))
        self.bal_lb.setStyleSheet(f"color:{t.tx};background:transparent;")
        for lb in (self.today_lb, self.last_lb):
            lb.setFont(qfont(t, 11))
            lb.setStyleSheet(f"color:{t.tx2};background:transparent;")
        self.peak_lb.setFont(qfont(t, 10.5))
        self.peak_lb.setStyleSheet(f"color:{t.warn};background:transparent;")

    # ------------------------------------------------------------ 数据

    def refresh(self) -> None:
        """拉一次 /dsh-whale/*（后台线程 + QTimer 回主线程同工位 _async 模式）。"""
        if self._busy:
            return
        self._busy = True
        self.bal_lb.setText("余额 刷新中…")
        bx: dict = {"done": False, "bal": None, "lt": None, "err": None}

        def _work() -> None:
            import config_io # noqa: PLC0415

            try:
                bx["bal"] = config_io.get_json("/dsh-whale/balance.json", timeout=15.0)
                try:
                    bx["lt"] = config_io.get_json("/dsh-whale/last-turn.json", timeout=8.0)
                except Exception: # noqa: BLE001 — 上轮消耗拿不到不影响余额
                    bx["lt"] = None
            except Exception as e: # noqa: BLE001
                bx["err"] = str(e)
            bx["done"] = True

        threading.Thread(target=_work, daemon=True, name="whale-fetch").start()

        def _apply() -> None:
            if not bx["done"]:
                QTimer.singleShot(300, _apply)
                return
            self._busy = False
            self._apply_data(bx.get("bal"), bx.get("lt"), bx.get("err"))

        QTimer.singleShot(300, _apply)

    def _apply_data(self, bal, lt, err) -> None: # noqa: ANN001
        sym = "¥"
        if isinstance(bal, dict) and bal.get("ok"):
            sym = "$" if str(bal.get("currency") or "CNY").upper() == "USD" else "¥"
            try:
                total = float(bal.get("totalBalance") or 0)
            except (TypeError, ValueError):
                total = 0.0
            self.bal_lb.setText("%s%s" % (sym, format(total, ",.2f")))
            try:
                today = float(bal.get("todayUsage") or 0)
            except (TypeError, ValueError):
                today = 0.0
            self.today_lb.setText("今日已用 %s%.4f" % (sym, today))
            self.peak_lb.setText("高峰时段（价贵）" if bal.get("isPeak") else "")
        elif isinstance(bal, dict) and bal.get("ok") is False:
            self.bal_lb.setText("余额 未取到")
            self.today_lb.setText(str(bal.get("error") or "没配模型 Key")[:22])
            self.peak_lb.setText("")
        else:
            self.bal_lb.setText("余额 —")
            self.today_lb.setText((str(err) if err else "后台没连上")[:22])
            self.peak_lb.setText("")
        if isinstance(lt, dict) and lt.get("turn") is not None:
            try:
                amt = float(lt.get("amount") or 0)
            except (TypeError, ValueError):
                amt = 0.0
            tok = lt.get("tokens")
            self.last_lb.setText("上轮 %s%.4f%s" % (sym, amt, (" · %s tok" % tok) if tok else ""))
        else:
            self.last_lb.setText("还没跑过一轮")

    # ------------------------------------------------------------ 交互（点击刷新 / 拖拽移动）

    def mousePressEvent(self, ev) -> None: # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag0 = ev.globalPosition().toPoint()
            self._win0 = self.pos()
            self._moved = False

    def mouseMoveEvent(self, ev) -> None: # noqa: N802
        if self._drag0 is None or self._win0 is None:
            return
        d = ev.globalPosition().toPoint() - self._drag0
        if not self._moved and (abs(d.x()) + abs(d.y())) > 5:
            self._moved = True # 位移阈值：小于它算「点击」而非拖拽
        if self._moved:
            self.move(self._win0 + d)

    def mouseReleaseEvent(self, ev) -> None: # noqa: N802
        if self._drag0 is not None:
            if self._moved:
                QSettings(*_SET).setValue("whale_pos", [self.x(), self.y()])
            else:
                self.refresh() # 点击（没拖动）= 刷新
        self._drag0 = None
        self._win0 = None

    def _move_default(self) -> None:
        try:
            scr = QApplication.primaryScreen()
            geo = scr.availableGeometry() if scr else None
            if geo is not None:
                self.move(geo.right() - self.W - 18, geo.bottom() - self.H - 18)
        except Exception: # noqa: BLE001
            pass

    def reset_position(self) -> None:
        """回到右下角（位置记忆清掉）——留给「找不到挂件了」的救援路径。"""
        QSettings(*_SET).setValue("whale_pos", "")
        self._move_default()
