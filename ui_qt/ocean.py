# -*- coding: utf-8 -*-
"""鲸落视觉本体 —— 海底画卷（壁纸 + tint）与三层海浪动画。

对齐 web 控制台的海洋动态背景（agent/console_html.py，**web 源码是唯一真值**）：
  · 底图    assets/wallpaper/ocean1.jpg cover 铺满（web body 默认 --bgimg）
  · tint    web --bg 的 160° 三站深蓝渐变（console_html.py L33）罩在底图上
  · 波浪    `.ocean-wave` 三层 SVG（L729-731 路径逐值抄入；L316-321 动画参数）：
            w1 9s 正向 / w2 14s 逆向 · 整层 opacity .7 / w3 20s 正向 · opacity .45；
            高度 40vh、整体 opacity .95；w1 另有浪尖高光描边（白 .9、宽 5）。
  · 帧率 QTimer 33ms ≈ 30fps 上限；只在 whale 主题启用。

Qt 实现与 web 的唯一行为差别（有意为之，写进回执）：
  web 的 waveMove 平移 25%（=半周期）后瞬移回 0 —— 波形点对称所以每轮有一次
  竖直镜像跳变；这里改为**相同线速度连续平移、按整周期平铺循环**（路径周期边界
  C1 连续：起点=终点、切线相同），视觉速度与 web 一致且天然无缝。
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)

# 三层波浪参数 —— 逐值抄自 console_html.py L729-731（path）与 L318-320（动画）。
# 绘制顺序 = web DOM 顺序（w3 最先 = 最底层）。元组：
# (baseline_y, ctrl1_y, ctrl2_y, fill_rgba, layer_opacity, duration_s, reverse)
_WAVE_LAYERS: tuple[tuple[int, int, int, tuple[int, int, int, int], float, float, bool], ...] = (
    (230, 150, 290, (120, 200, 255, 102), 0.45, 20.0, False), # w3 后排 fill rgba(120,200,255,.40)
    (200, 120, 280, (160, 222, 255, 140), 0.70, 14.0, True), # w2 中排 fill rgba(160,222,255,.55)
    (160, 80, 240, (235, 250, 255, 204), 1.00, 9.0, False), # w1 前排 fill rgba(235,250,255,.80)
)
_VIEW_W, _VIEW_H = 1440.0, 320.0 # web svg viewBox
_PERIOD_U = 720.0 # 波形周期：1440 里恰好两轮 ⇒ 一个周期 = 可视视口宽
_FRAME_MS = 33
_WAVE_OPACITY = 0.95 # web .ocean-wave{opacity:.95}


def tint_gradient(w: float, h: float) -> QLinearGradient:
    """web --bg 的 160° 深蓝 tint（console_html.py L33 逐值抄入）。

    CSS `linear-gradient(160deg, A, B 45%, C)`：160° ⇒ 渐变方向朝下略偏右
    （屏幕坐标 y 向下：方向 = (sin160°, -cos160°) = (0.342, 0.940)）。
    """
    dx, dy = 0.342, 0.940
    line = abs(dx) * w + abs(dy) * h # CSS 渐变线长度
    cx, cy = w / 2.0, h / 2.0
    g = QLinearGradient(
        cx - dx * line / 2.0, cy - dy * line / 2.0,
        cx + dx * line / 2.0, cy + dy * line / 2.0,
    )
    g.setColorAt(0.00, QColor(8, 30, 58, 158)) # rgba(8,30,58,.62)
    g.setColorAt(0.45, QColor(12, 44, 84, 115)) # rgba(12,44,84,.45)
    g.setColorAt(1.00, QColor(18, 48, 96, 140)) # rgba(18,48,96,.55)
    return g


def cover_pixmap(src: QPixmap, w: int, h: int, dpr: float) -> QPixmap:
    """cover 铺满缩放（web background-size:cover 等价）：等比放大到盖满 (w,h)。"""
    if src.isNull() or w <= 0 or h <= 0:
        return QPixmap()
    s = max(w / src.width(), h / src.height())
    out = src.scaled(
        max(1, round(src.width() * s * dpr)),
        max(1, round(src.height() * s * dpr)),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    out.setDevicePixelRatio(dpr)
    return out


class OceanWaves(QObject):
    """三层海浪动画驱动 —— 组合进 Shell（Shell.paintEvent 调 paint()）。

    不是独立 QWidget：壁纸/tint/波浪全画在 Shell 自己的 paintEvent 里，
    内容卡片等子控件带各自背景压在上面 —— 与 web 的 z-index 层级一致。
    """

    def __init__(self, shell):
        super().__init__() # 不挂 QObject 父：shell 侧持引用保活即可
        self._shell = shell
        self._timer = QTimer(self)
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self._t0 = 0.0
        self._tiles: tuple = () # (QPixmap, period_px, reverse, speed_px_s)
        self._tiles_for = (0, 0, 0.0) # (w, h, dpr) 重建判据

    # ------------------------------------------------------------ 生命周期

    def set_active(self, on: bool) -> None:
        """whale 主题 + 窗口可见才跑计时器（CPU 纪律：不画就不转）。"""
        if on and not self._timer.isActive():
            self._t0 = time.monotonic()
            self._timer.start()
        elif not on and self._timer.isActive():
            self._timer.stop()

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    def invalidate(self) -> None:
        """尺寸/DPR 变了 ⇒ 下一帧重建瓦片。"""
        self._tiles_for = (0, 0, 0.0)

    def _tick(self) -> None:
        """只重绘底部 40vh 波浪区（静态壁纸/tint 不动 —— CPU 纪律：画的区域最小化）。"""
        sh = self._shell
        h = max(1, round(sh.height() * 0.40)) + 2
        sh.update(0, sh.height() - h, sh.width(), h)

    # ------------------------------------------------------------ 瓦片

    def _build_tiles(self, w: int, h: int, dpr: float) -> None:
        """预渲染三层瓦片：每层一个周期（= 视口宽）无缝平铺。

        坐标换算与 web 完全一致：svg 宽 200% ⇒ 1440 单位铺满两倍视口，
        即 720 单位（一个波形周期）= 一个视口宽；y 方向 320 单位 = 40vh。
        """
        wave_h = max(1.0, h * 0.40) # .ocean-wave{height:40vh}
        sx, sy = w / _PERIOD_U, wave_h / _VIEW_H # x/y 各自拉伸（preserveAspectRatio=none）
        pw, ph = max(2, round(w * dpr)), max(2, round(wave_h * dpr))
        tiles = []
        for base_y, c1_y, c2_y, (fr, fg, fb, fa), lop, dur, rev in _WAVE_LAYERS:
            pm = QPixmap(pw, ph)
            pm.fill(Qt.GlobalColor.transparent)
            g = QPainter(pm)
            g.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            g.scale(dpr, dpr)
            path = QPainterPath()
            path.moveTo(0.0, base_y * sy)
            path.cubicTo(240 * sx, c1_y * sy, 480 * sx, c2_y * sy, _PERIOD_U * sx, base_y * sy)
            fill = path
            fill.lineTo(_PERIOD_U * sx, _VIEW_H * sy)
            fill.lineTo(0.0, _VIEW_H * sy)
            fill.closeSubpath()
            g.fillPath(fill, QColor(fr, fg, fb, round(fa * lop)))
            if base_y == 160: # w1 浪尖高光线（web L731 第二条 path）
                pen_w = max(2.0, min(6.0, 5.0 * sy))
                g.strokePath(path, _pen_rgba(255, 255, 255, round(255 * 0.9), pen_w))
            g.end()
            tiles.append((pm, float(w), rev, (0.5 * w) / dur)) # web：半个周期/时长 的线速度
        self._tiles = tuple(tiles)
        self._tiles_for = (w, h, dpr)

    # ------------------------------------------------------------ 绘制

    def paint(self, p: QPainter, w: int, h: int, dpr: float) -> None:
        if w < 8 or h < 8:
            return
        if self._tiles_for != (w, h, dpr):
            self._build_tiles(w, h, dpr)
        wave_h = h * 0.40
        y0 = h - wave_h
        p.setOpacity(_WAVE_OPACITY)
        t = time.monotonic() - self._t0
        for pm, period, rev, speed in self._tiles:
            off = (speed * t) % period
            x0 = -((period - off) % period) if rev else -off
            p.drawPixmap(QPointF(x0, y0), pm)
            p.drawPixmap(QPointF(x0 + period, y0), pm)
        p.setOpacity(1.0)


def _pen_rgba(r: int, g_: int, b: int, a: int, width: float):
    from PySide6.QtGui import QPen # noqa: PLC0415

    pen = QPen(QColor(r, g_, b, a))
    pen.setWidthF(width)
    return pen


def paint_backdrop(p: QPainter, w: int, h: int, wp: QPixmap) -> None:
    """海底画卷（静底版）—— 底图 → tint（Shell.paintEvent 专用）。

     I：**三层波浪动画砍掉**，只留 ocean.jpg 静底图。
    旧签名里的 `ocean: OceanWaves` 参数与末尾的 `ocean.paint(...)` 一并移除——
    调用点只剩本函数，波浪从此不进产品渲染路径。"""
    if not wp.isNull():
        dw = wp.width() / (wp.devicePixelRatio() or 1.0)
        dh = wp.height() / (wp.devicePixelRatio() or 1.0)
        p.drawPixmap(QRectF((w - dw) / 2.0, (h - dh) / 2.0, dw, dh), wp, QRectF(0, 0, wp.width(), wp.height()))
    p.fillRect(QRectF(0, 0, w, h), tint_gradient(w, h))


def _selftest() -> list[tuple[str, bool, str]]:
    """模块自检：参数对齐 web 真值 + 瓦片渲染 + 渐变。"""
    out: list[tuple[str, bool, str]] = []

    def ck(name: str, cond: bool, extra: str = "") -> None:
        out.append((name, bool(cond), extra))

    ck("ocean: 三层波浪（w3/w2/w1）", len(_WAVE_LAYERS) == 3)
    durs = [lay[5] for lay in _WAVE_LAYERS]
    ck("ocean: 周期与 web 一致（20/14/9s）", durs == [20.0, 14.0, 9.0], str(durs))
    ck("ocean: w2 逆向、w1/w3 正向", [lay[6] for lay in _WAVE_LAYERS] == [False, True, False])
    ck("ocean: 层透明度 (.45/.70/1.0)", [lay[4] for lay in _WAVE_LAYERS] == [0.45, 0.70, 1.00])
    ck("ocean: 帧率上限 33ms（30fps）", _FRAME_MS == 33)
    ck("ocean: 波高 40vh、周期=视口宽", _VIEW_H == 320.0 and _PERIOD_U == 720.0)

    g = tint_gradient(100, 100)
    stops = [(pos, col.alpha()) for pos, col in g.stops()]
    ck("ocean: tint 渐变三站",
       [s[1] for s in stops] == [158, 115, 140] and abs(stops[1][0] - 0.45) < 1e-6, str(stops))

    # 瓦片渲染（离屏：直接喂假 shell —— 只用到 update()）
    class _FakeShell:
        def update(self):
            pass

    ow = OceanWaves(_FakeShell())
    pm = QPixmap(1120 * 2, 720 * 2)
    pm.fill(0)
    ow._build_tiles(1120, 720, 2.0)
    ck("ocean: 三块瓦片已渲染", len(ow._tiles) == 3)
    ck("ocean: 瓦片非空且周期=宽", all(not t[0].isNull() and t[1] == 1120 for t in ow._tiles))
    img = ow._tiles[2][0].toImage() # w1（前排）
    ck("ocean: 瓦片有像素（非全透明）",
       img.pixelColor(100, 500).alpha() > 0 and img.pixelColor(1120, 550).alpha() > 0,
       f"a={img.pixelColor(100, 500).alpha()} b={img.pixelColor(1120, 550).alpha()}")

    ow2 = OceanWaves(_FakeShell())
    pm2 = QPixmap(4, 4)
    pm2.fill(0)
    p = QPainter(pm2)
    try:
        ow2.paint(p, 320, 200, 1.0)
        ck("ocean: paint() 冷启动自建瓦片", len(ow2._tiles) == 3 and ow2._tiles_for == (320, 200, 1.0))
    finally:
        p.end()
    return out


if __name__ == "__main__":
    import sys

    from PySide6.QtWidgets import QApplication

    _app = QApplication(sys.argv)
    fails = 0
    for name, ok, extra in _selftest():
        print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""))
        fails += 0 if ok else 1
    raise SystemExit(1 if fails else 0)
