# -*- coding: utf-8 -*-
"""中键滚轮模式 —— web `PM_WHEEL`（agent/console_html.py L2994-3093，2026-09-17 用户
亲自点单实现）的 Qt 复刻。唯一真值 = 那段源码，参数与语义逐条照搬：

  · 常量   BASE=3.4 px/帧（≈200px/s）· GAIN=0.26 · MAXV=44 · DEAD=10；
           SPIN_K=2.4（转速=|v|×K 度/帧）· SPIN_CAP=24 · tick 16ms（≈rAF）
  · 进入   中键按下＝进滚轮模式，锚点=按下点，**一按就匀速下滚**（不是等鼠标动）
  · 调速   鼠标相对锚点垂直偏移：死区内保持基础速度；出死区 GAIN 线性、可反向、封顶 MAXV
  · 鱼转   相位累加（不用动画曲线：改 duration 会让相位跳，速度不好跟）→ 按 360/24
           分帧驱动**光标上那只鱼**（用户点单原话："滚的是光标上那只鱼"）；
           徽标里**不画第二条鱼**（用户同日点单）
  · 徽标   58px 圆盘（描边/底/上下三角）只标锚点+方向，跟锚点走
  · 目标   scrollerAt：命中点下逐层向上找第一个真可滚 QAbstractScrollArea
           （verticalScrollBar().maximum()>0），找不到回退主页面滚动区
  · 退出   再按中键 / 其它鼠标键 / 滚真实滚轮 / Esc / 应用失焦；退出光标 restore 回底图
  · 边界   只在鲸鱼光标开着时接管（enabled 才 toggle）；光标关了不拦

丙-6 #12 裁决（总调度）：中键**直接进滚轮模式**，不播旧版"原地转一圈"
（web 两个 listener 并存有打架嫌疑，Qt 取干净语义）；旧 spin() 保留为兜底。

事件接入：WhaleCursor.eventFilter（app 级）把 MiddleButton / MouseMove / Wheel /
Esc / ApplicationDeactivate 委托进来 —— 本模块自己不再装 filter，单点不双跑。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parent))   # 嵌入式 runtime._pth 不放脚本目录
from stylekit_qt import Tokens, rgba  # noqa: E402

BASE = 3.4          # web PM_WHEEL BASE（console_html.py L3005）：px/帧，≈200px/s
GAIN = 0.26         # web GAIN：出死区后的线性增益
MAXV = 44.0         # web MAXV：速度上限（px/帧）
DEAD = 10.0         # web DEAD：死区半径（px）
SPIN_K = 2.4        # web SPIN_K：转速 = |v| × K（度/帧）
SPIN_CAP = 24.0     # web SPIN_CAP：转速封顶（度/帧）
TICK_MS = 16        # web requestAnimationFrame ≈ 16ms
BADGE = 58          # web 徽标 58px 圆盘
RING_ALPHA = 128    # 描边 alpha（web rgba(148,196,255,.5) → token 化取整）
FILL_ALPHA = 87     # 底 alpha（web rgba(10,20,40,.34)）
TRI_ALPHA = 230     # 三角 alpha（web rgba(190,220,255,.9)）


def velocity_for(offset: float) -> float:
    """web tick() 的速度纯函数（L3050-3053）：可反向、死区、线性增益、封顶。"""
    if offset > DEAD:
        return min(MAXV, BASE + (offset - DEAD) * GAIN)
    if offset < -DEAD:
        return max(-MAXV, -BASE + (offset + DEAD) * GAIN)
    return BASE


def spin_speed(v: float) -> float:
    """web tick() 的鱼速纯函数（L3057）：min(SPIN_CAP, |v|×SPIN_K)。"""
    return min(SPIN_CAP, abs(v) * SPIN_K)


class WheelBadge(QWidget):
    """58px 滚轮徽标：锚点圆盘 + 上下三角（web show() 同款；不画第二条鱼）。

    独立顶层小窗（ToolTip 旗标 + 透明背景 + 不抢焦点 + 穿透鼠标），
    钉在屏幕锚点上不随内容滚动 —— 对齐 web 的 position:fixed。
    """

    def __init__(self, t: Tokens):
        super().__init__(
            None,
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.t = t
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(BADGE, BADGE)

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 颜色全部 token 派生（禁硬编码色值）：描边/三角 = blue 系，底 = bg 压暗
        ring = rgba(self.t.q("blue"), RING_ALPHA)
        fill = rgba(self.t.q("bg"), FILL_ALPHA)
        tri = rgba(self.t.q("blue2"), TRI_ALPHA)
        r = QRectF(0.5, 0.5, BADGE - 1, BADGE - 1)
        p.setBrush(QBrush(fill))
        p.setPen(QPen(ring, 1))
        p.drawEllipse(r)
        p.setBrush(QBrush(tri))
        p.setPen(Qt.PenStyle.NoPen)
        c = BADGE / 2.0
        # 上三角（web i.u：top 5px、宽 10 高 7）
        p.drawPolygon(QPolygonF([
            QPointF(c - 5, 5 + 7), QPointF(c + 5, 5 + 7), QPointF(c, 5),
        ]))
        # 下三角（web i.d：bottom 5px、宽 10 高 7）
        b = BADGE - 5
        p.drawPolygon(QPolygonF([
            QPointF(c - 5, b - 7), QPointF(c + 5, b - 7), QPointF(c, b),
        ]))

    def show_at(self, gx: int, gy: int) -> None:
        """锚点（全局屏幕坐标）居中钉住（web margin:-29 同款）。"""
        self.move(int(gx - BADGE / 2), int(gy - BADGE / 2))
        self.show()
        self.update()

    def hide_badge(self) -> None:
        self.hide()


class WheelMode(QObject):
    """滚轮模式状态机：toggle 进出，QTimer 16ms tick 驱动滚动+鱼转。

    cursor 参数 = WhaleCursor 实例（用它的 spin_to / restore / stop_transient /
    enabled —— 鸭子类型，不 import cursor_fx 防环）。fallback 参数 =
    可选回调，返回"主页面滚动区"（Shell 接线注入当前页 QScrollArea）。
    """

    def __init__(self, cursor, t: Tokens, fallback=None, parent: QObject | None = None):
        super().__init__(parent)
        self._cursor = cursor
        self._t = t
        self._fallback = fallback
        self.on = False
        self._ax = 0            # 锚点（全局 y 基准；x 只用来找滚动目标）
        self._ay = 0
        self._my = 0            # 鼠标当前 y（mousemove 全程跟踪，web L3080）
        self._phase = 0.0       # 鱼的相位（度，累加）
        self._acc = 0.0         # 滚动小数累计（3.4px/帧不能整丢）
        self._target = None     # 滚动目标（QAbstractScrollArea）
        self._badge = WheelBadge(t)
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------ 进出

    def toggle(self, gx: int, gy: int) -> None:
        """中键按下（web mousedown button===1）：开着就退出，否则进入。"""
        if self.on:
            self.stop()
            return
        if not getattr(self._cursor, "enabled", False):
            return                              # 光标关着 ⇒ 不接管（web L3083 边界）
        self.start(gx, gy)

    def start(self, gx: int, gy: int) -> None:
        self.stop()                             # 幂等清场（web start 首行 stop 同款）
        getattr(self._cursor, "stop_transient", lambda: None)()   # 点头等瞬时特效让位
        self._ax, self._ay, self._my = gx, gy, gy
        self._phase = 0.0
        self._acc = 0.0
        self._target = self._pick_scroller(gx, gy)
        self.on = True
        self._badge.show_at(gx, gy)
        self._timer.start()

    def stop(self) -> None:
        if not self.on and not self._timer.isActive():
            return
        self.on = False
        self._timer.stop()
        self._badge.hide_badge()
        self._target = None
        try:
            self._cursor.restore()              # 退出光标回底图（web stop 同款，不然停在半帧）
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 事件喂入口（cursor_fx.eventFilter 委托）

    def on_move(self, gy: int) -> None:
        if self.on:
            self._my = gy

    def on_wheel(self) -> bool:
        """滚真实滚轮 = 退出（web wheel listener）。返回是否消费（只观察不吃）。"""
        if self.on:
            self.stop()
            return True
        return False

    def on_esc(self) -> bool:
        if self.on:
            self.stop()
            return True
        return False

    def on_blur(self) -> None:
        if self.on:
            self.stop()

    # ------------------------------------------------------------ 主循环

    def _tick(self) -> None:
        if not self.on:
            return
        v = velocity_for(self._my - self._ay)
        self._phase = (self._phase + spin_speed(v)) % 360.0
        try:
            self._cursor.spin_to(self._phase)
        except Exception:  # noqa: BLE001
            pass
        self._scroll(v)

    def _scroll(self, v: float) -> None:
        """target.scrollTop += v 的 Qt 等价：小数累计 + 整步 setValue。"""
        if self._target is None:
            return
        try:
            bar = self._target.verticalScrollBar()
            self._acc += v
            step = int(self._acc)
            if step:
                self._acc -= step
                bar.setValue(bar.value() + step)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 滚动目标（web scrollerAt）

    def _pick_scroller(self, gx: int, gy: int):
        """命中点下找第一个真可滚祖先（maximum>0）；找不到回退 fallback（主页面）。"""
        try:
            w = QApplication.widgetAt(gx, gy)
            seen = 0
            while w is not None and seen < 32:      # 防御：异常层级不失控
                seen += 1
                if isinstance(w, QAbstractScrollArea) and w.verticalScrollBar().maximum() > 0:
                    return w
                w = w.parentWidget()
        except Exception:  # noqa: BLE001
            pass
        try:
            fb = self._fallback() if callable(self._fallback) else None
            if isinstance(fb, QAbstractScrollArea) and fb.verticalScrollBar().maximum() > 0:
                return fb
        except Exception:  # noqa: BLE001
            pass
        return None


def _selftest() -> list[tuple[str, bool, str]]:
    """模块自检：web 真值参数锁、速度纯函数（含死区/反向/封顶）、相位分帧。"""
    out: list[tuple[str, bool, str]] = []

    def ck(name: str, cond: bool, extra: str = "") -> None:
        out.append((name, bool(cond), extra))

    # 参数与 web 真值逐一对照（console_html.py L3005-3006）
    ck("wheel: BASE=3.4", BASE == 3.4)
    ck("wheel: GAIN=0.26", GAIN == 0.26)
    ck("wheel: MAXV=44", MAXV == 44.0)
    ck("wheel: DEAD=10", DEAD == 10.0)
    ck("wheel: SPIN_K=2.4", SPIN_K == 2.4)
    ck("wheel: SPIN_CAP=24", SPIN_CAP == 24.0)

    # 速度纯函数：死区内=基础速度；正向线性；反向；封顶
    ck("wheel: 死区内保持基础速度（一按就滚）", velocity_for(0) == BASE and velocity_for(5) == BASE)
    ck("wheel: 出死区线性增益", abs(velocity_for(DEAD + 10) - (BASE + 10 * GAIN)) < 1e-9)
    ck("wheel: 反向", velocity_for(-(DEAD + 10)) < 0)
    ck("wheel: 正向封顶 MAXV", velocity_for(10000) == MAXV)
    ck("wheel: 反向封顶 -MAXV", velocity_for(-10000) == -MAXV)
    ck("wheel: 鱼速=|v|×K 且封顶", spin_speed(10) == 24.0 and spin_speed(3) == 3 * 2.4)

    # 相位分帧语义在 cursor_fx.spin_to（帧变才换）；这里锁 360/24 分帧的索引公式。
    # 满速 24 度/帧走 24 步：24 与 15 的公倍数是 120 ⇒ 每 5 步相位回绕一次，
    # 24 步（1.6 圈）恰好铺到 15 个不同帧位（数学上可精确算出，不是估的）。
    deg = 0.0
    idxs = []
    for _ in range(24):
        idxs.append(int((((deg % 360.0) + 360.0) % 360.0) // 15.0) % 24)
        deg += 24.0                          # 满速 24 度/帧
    ck("wheel: 相位→24 帧索引公式", len(set(idxs)) == 15 and idxs[0] == 0,
       "unique=%d" % len(set(idxs)))

    return out


if __name__ == "__main__":
    import sys

    fails = 0
    for name, ok, extra in _selftest():
        print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""))
        fails += 0 if ok else 1
    raise SystemExit(1 if fails else 0)
