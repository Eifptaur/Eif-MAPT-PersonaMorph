# -*- coding: utf-8 -*-
"""右下角鲸鱼挂件 —— 上游 DeepSeek-Balance-Whale-Widget 的**原版复刻**。

用户裁定（2026-09-25 截图）：此前 Qt 版做成了「胶囊卡片」（圆角卡 + 余额/今日已用/
每轮消耗四行灰字）——不是原版。原版形态（whale-widget/client/widget.js :245-272
:10293-10297）＝
  · 透明方块（无边框无底色，默认边长 min(250px, 视口短边×0.28)）；
  · 右下角鲸鱼图（边长的 59.45%）；
  · 左上一只**对话气泡**（SVG：白底圆角泡 + 两粒渐小的泡，描边 #203170）；
  · 气泡内文字（色 #536ba9、水平垂直居中）：余额大字 + 说明小字。
气泡用**原版 SVG 原文**经 QSvgRenderer 渲染（同一份 path/ellipse，不手转弧线）；
QtSvg 缺席时退化成圆角矩形泡（数据照常）。数据面照旧复用 /dsh-whale/*（余额/今日
已用/上轮消耗/高峰提示），30s 轮询、点击刷新、拖拽换位、位置记 QSettings 全保留。
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPixmap, QColor
from PySide6.QtWidgets import QApplication, QWidget

ROOT = Path(__file__).resolve().parent.parent
_ASSET = ROOT / "whale-widget" / "assets" / "DSniang1.png"
_SET = ("WXAgent", "persona-morph-ui")

# 原版气泡 SVG 原文（widget.js :10293-10297 逐字；viewBox 1026×700）——
# 白底泡体 + 拖尾两粒小泡，描边 #203170、线宽 18。
_BUBBLE_SVG = (
    '<svg viewBox="0 0 1026 700" preserveAspectRatio="xMidYMid meet" '
    'xmlns="http://www.w3.org/2000/svg">'
    '<path fill="#FFFFFF" stroke="#203170" stroke-width="18" stroke-linejoin="round" '
    'stroke-linecap="round" d="M 827 248 A 373 232 0 1 0 81 246 A 373 232 0 0 0 301 465 '
    'A 57 32 10 0 0 413 484 A 373 232 0 0 0 827 248 Z"/>'
    '<ellipse cx="352" cy="561" rx="37.5" ry="26" fill="#FFFFFF" stroke="#203170" '
    'stroke-width="18"/>'
    '<ellipse cx="442" cy="646" rx="24.5" ry="18" fill="#FFFFFF" stroke="#203170" '
    'stroke-width="18"/>'
    '</svg>')
_INK = QColor("#536ba9")
_VBW, _VBH = 1026.0, 700.0 # 气泡 viewBox（aspect 1026/700）
_WHALE_RATIO = 0.5945 # 鲸图边长占比（widget.js :253）


class WhaleWidget(QWidget):
    """无边框置顶小窗（原版复刻）：透明方块 + 气泡 + 鲸鱼；点击刷新、拖拽移动。"""

    W, H = 250, 250 # 原版默认边长（widget.js :245 clamp 的桌面上限值）

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

        # 气泡内文字（原版口径：余额大字 + 说明小字，居中，#536ba9）
        self.bal = "余额 …"
        self.hint = ""

        try:
            pm = QPixmap(str(_ASSET))
            self._whale = pm if not pm.isNull() else QPixmap()
        except Exception: # noqa: BLE001 — 素材缺失时只有气泡，数据照常显示
            self._whale = QPixmap()

        # 气泡渲染器：原版 SVG 原文（QtSvg 缺席 ⇒ None，paintEvent 走圆角矩形退化）
        self._bub = None
        try:
            from PySide6.QtSvg import QSvgRenderer # noqa: PLC0415

            r = QSvgRenderer(QByteArray(_BUBBLE_SVG.encode("utf-8")))
            self._bub = r if r.isValid() else None
        except Exception: # noqa: BLE001
            self._bub = None

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
        """主题切换时刷新字号（挂件是独立顶层窗，不走 Shell 的 QSS 重建）。"""
        self.t = t

    def paintEvent(self, _e) -> None: # noqa: N802
        """原版复刻：先画气泡（原版 SVG），再画右下鲸鱼图，最后画气泡内文字。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # ① 气泡：占满窗口宽，高按 viewBox 比例（1026/700）
        bw = float(self.W)
        bh = bw * _VBH / _VBW
        if self._bub is not None:
            self._bub.render(p, QRectF(0, 0, bw, bh))
        else: # 退化：QtSvg 缺席时给个白底圆角泡（描边同色），文字照常
            p.setPen(QColor("#203170"))
            p.setBrush(QColor("#FFFFFF"))
            p.drawRoundedRect(QRectF(6, 6, bw - 12, bh - 12), 40, 40)

        # ② 鲸鱼图：右下角，边长 59.45%（原版 object-fit contain、右下对齐）
        if self._whale is not None and not self._whale.isNull():
            w = self.W * _WHALE_RATIO
            pm = self._whale.scaled(int(w), int(w), Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(self.W - pm.width(), self.H - pm.height(), pm)

        # ③ 气泡内文字：中心 (44.25%, 36%)，区域宽 66% 高 64%（widget.js :272）
        cx, cy = self.W * 0.4425, self.H * 0.36
        tw = self.W * 0.66
        th = self.H * 0.64
        area = QRectF(cx - tw / 2, cy - th / 2, tw, th)
        f_amt = QFont(self.font())
        f_amt.setPixelSize(max(15, int(self.W * 0.088)))
        f_amt.setBold(True)
        f_small = QFont(self.font())
        f_small.setPixelSize(max(10, int(self.W * 0.044)))
        p.setPen(_INK)
        fm_a = QFontMetrics(f_amt)
        fm_s = QFontMetrics(f_small)
        # 行：余额大字 / hint 小字（今日已用 · 上轮 · 高峰），最多三行
        rows = [(f_amt, self.bal)]
        rows += [(f_small, x) for x in [y for y in (self.hint or "").split("\n") if y][:2]]
        total_h = sum(fm.height() for f, _t in rows) + 6 * (len(rows) - 1)
        y = area.top() + (area.height() - total_h) / 2
        for f, txt in rows:
            fm = QFontMetrics(f)
            p.setFont(f)
            elided = fm.elidedText(txt, Qt.ElideRight, int(tw))
            p.drawText(QRectF(area.left(), y, area.width(), fm.height()),
                       int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), elided)
            y += fm.height() + 6

    # ------------------------------------------------------------ 数据

    def refresh(self) -> None:
        """拉一次 /dsh-whale/*（后台线程 + QTimer 回主线程同工位 _async 模式）。"""
        if self._busy:
            return
        self._busy = True
        self.bal = "余额 刷新中…"
        self.update()
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
        hint1 = hint2 = ""
        if isinstance(bal, dict) and bal.get("ok"):
            sym = "$" if str(bal.get("currency") or "CNY").upper() == "USD" else "¥"
            try:
                total = float(bal.get("totalBalance") or 0)
            except (TypeError, ValueError):
                total = 0.0
            self.bal = "%s%s" % (sym, format(total, ",.2f"))
            try:
                today = float(bal.get("todayUsage") or 0)
            except (TypeError, ValueError):
                today = 0.0
            hint1 = "今日已用 %s%.4f" % (sym, today)
            hint2 = "高峰时段（价贵）" if bal.get("isPeak") else ""
        elif isinstance(bal, dict) and bal.get("ok") is False:
            self.bal = "余额 未取到"
            hint1 = str(bal.get("error") or "没配模型 Key")[:22]
        else:
            self.bal = "余额 —"
            hint1 = (str(err) if err else "后台没连上")[:22]
        if isinstance(lt, dict) and lt.get("turn") is not None:
            try:
                amt = float(lt.get("amount") or 0)
            except (TypeError, ValueError):
                amt = 0.0
            tok = lt.get("tokens")
            hint2 = " ".join([x for x in (hint2, "上轮 %s%.4f%s" % (
                sym, amt, (" · %s tok" % tok) if tok else "")) if x]).strip()
        self.hint = "\n".join([x for x in (hint1, hint2) if x])
        self.update()

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
