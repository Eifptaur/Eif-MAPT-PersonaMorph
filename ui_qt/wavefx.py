# -*- coding: utf-8 -*-
"""丙-10 P0-5：鼠标水光波纹（Qt 原生近似实现）。

## 为什么另起一个模块（取证结论，写进回执）

取证（`_c10_wave_probe.py`）实锤两件事：
  1. **Qt 侧从未实现过波纹** —— 全 `ui_qt/` 搜 `wave_fx`/`ripple`/「波纹」只有
     导航项文案（shell.py:147）与配置键（config_io.py:143），**没有任何绘制代码**。
     ⇒ 用户说的「完全失效」不是「坏了」，是**从来没有**。
  2. **不是丙-8 I 误伤** —— a14b228 那笔只把 `OceanWaves.set_active` 从「按主题启停」
     改成恒 `False`；`ocean.py` 的 `OceanWaves` 只管背景三层海浪（类内无 ripple 相关
     方法），与鼠标波纹没有共用 Timer 或标志位。工单的首要怀疑**方向不成立**。

## web 真值 vs Qt 能力边界（如实说清）

web 侧是浏览器 SVG 滤镜：`feTurbulence` + `feDisplacementMap`（console_html.py L720
与 L6549+），用噪声位移把**光标所在模块的像素**扭出水波折射。**Qt 没有等价滤镜**
（QGraphicsEffect 无 displacement map，逐像素重采样代价不可接受）。
⇒ 用户拍板「Qt 原生近似实现」：用 QPainter 画**同心波纹环**（径向扩张 + 距离衰减 +
鼠标速度联动），语义对齐 web（投石入水、模块内衰减、参数即时生效），
视觉是 Qt 原生近似而非滤镜扭曲 —— 这一点在 `使用说明`/回执里如实标注，不冒充 1:1。

## 解耦纪律（P0-5 验收硬要求）

本模块**不碰** `OceanWaves`：自己的 `QTimer`、自己的 `set_enabled`、自己的绘制层。
背景海浪（ocean.py）与鼠标波纹（本模块）互不牵连 —— 关海浪不影响波纹，关波纹不影响海浪。
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QObject, QPointF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen

_FRAME_MS = 33          # ≈30fps，与 ocean.py 同档（丙-4 工单钉死的帧率上限）

# 能量地板（丙-10 P0-5）：**静止也有波纹**（web 是常驻透镜，不要求手在动）。
# 取 0.35 而非 0（初始态）——0 时 alpha 系数 0.4 太淡，人眼在浅色玻璃上几乎看不见。
_E_MIN = 0.35


class WaveFX(QObject):
    """鼠标波纹驱动器 —— 组合进 Shell（Shell.paintEvent 调 `paint()`）。

    与 `OceanWaves` 的关系：**并列的两个独立对象**，各自持 Timer、各自判活。
    `set_enabled(False)` 只停自己，不触碰背景海浪。
    """

    def __init__(self, shell):
        super().__init__()              # 不挂 QObject 父：shell 侧持引用保活即可
        self._shell = shell
        self._timer = QTimer(self)
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self._enabled = False
        self._pos = QPointF(0.0, 0.0)   # 当前波纹中心（光标处）
        self._t0 = time.monotonic()     # 本波纹诞生时刻（波阵面用）
        self._energy = 0.0              # 0..1，拖动越快越强（web mouse_gain 语义）
        # ⛔ 丙-10 P0-5 实锤（_c10_wave_diag.py）：初值 0.0 会让**第一次**移动的
        #   dt = now - 0.0 ≈ 5.5e5 秒 ⇒ speed≈0 ⇒ energy 被地板锁在 0.25，
        #   且此后因 early-return 从不更新 ⇒ 永远 0.25（人眼分不出强弱）。
        #   ⇒ 用**当前时刻**做初值，第一次移动的 dt 才是真实的帧间隔。
        self._last_move_t = time.monotonic()
        self._cfg = self._read_cfg()

    # ------------------------------------------------------------ 配置

    def _read_cfg(self) -> dict:
        """读 `ui.wave_fx.*`（与 web 同一份 config 键；缺键用 web 同款默认）。"""
        d = {"enabled": True, "scale": 16, "speed": 6, "mouse_gain": 0.03,
             "max_gain": 8, "radius": 260, "falloff": 4, "rings": 2, "ring_speed": 0.4}
        try:
            import config_io  # noqa: PLC0415

            for k in list(d.keys()):
                v = config_io.read_path("ui.wave_fx.%s" % k, None)
                if v is not None:
                    d[k] = v
        except Exception:  # noqa: BLE001
            pass
        return d

    def reload(self) -> None:
        """参数即时生效（web 侧「应用水光波纹设置」同款语义）。"""
        self._cfg = self._read_cfg()
        self.set_enabled(bool(self._cfg.get("enabled", True)))
        self._shell.update()

    # ------------------------------------------------------------ 生命周期（只动自己）

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        if self._enabled and not self._timer.isActive():
            self._timer.start()
        elif not self._enabled and self._timer.isActive():
            self._timer.stop()
        self._shell.update()

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    def refresh_from_config(self) -> None:
        """外部（web 面板）改了 `ui.wave_fx` ⇒ 跟上（Shell._watch_config 4 秒一跳）。"""
        c = self._read_cfg()
        if c != self._cfg:
            self._cfg = c
            self.set_enabled(bool(c.get("enabled", True)))

    # ------------------------------------------------------------ 输入

    def on_mouse_move(self, gp: QPointF) -> None:
        """Shell 的鼠标事件转进来：更新波纹中心 + 能量（移动越快越强）。

        ⛔ 丙-10 P0-5 实测（_c10_wave_strength.py / _c10_wave_diag.py）：
          ① `_last_move_t` 初值 0.0 ⇒ 第一次移动 dt≈5.5e5 秒 ⇒ speed≈0 ⇒ 能量恒锁地板；
          ② 地板 0.25 与满档 1.0 在 alpha 上只差 1.82 倍（人眼分不出）。
        修：初值取当前时刻（见 __init__）；并把能量映射从「地板 0.25」放宽到
        `E_MIN=0.35 → 1.0`，同时**在 alpha 公式里给基础可见度兜底**（见 paint）。
        """
        if not self._enabled:
            return
        now = time.monotonic()
        dt = max(1e-3, now - self._last_move_t)
        self._last_move_t = now
        dist = math.hypot(gp.x() - self._pos.x(), gp.y() - self._pos.y())
        speed = dist / dt                       # px/s
        gain = float(self._cfg.get("mouse_gain", 0.03))
        cap = float(self._cfg.get("max_gain", 8))
        self._energy = min(1.0, max(_E_MIN, min(cap, speed * gain) / max(1e-6, cap)))
        self._pos = QPointF(gp)
        self._t0 = now                          # 光标一动，重新投石（web 同款）
        self._shell.update()

    # ------------------------------------------------------------ 绘制

    def _tick(self) -> None:
        """只重绘波纹所在的正方形区域（CPU 纪律：画的区域最小化）。"""
        if not self._enabled:
            return
        r = float(self._cfg.get("radius", 260))
        c = self._pos
        self._shell.update(int(c.x() - r), int(c.y() - r), int(r * 2), int(r * 2))

    def paint(self, p: QPainter, w: int, h: int, dpr: float) -> None:
        """在 Shell.paintEvent 里调用 —— 在**内容之上**画同心波纹环。

        与 `OceanWaves.paint`（画在壁纸层、内容之下）**层级不同、对象不同**，
        所以两者完全不牵连（P0-5 验收：绘制路径解耦）。
        """
        if not self._enabled or w < 8 or h < 8:
            return
        cfg = self._cfg
        scale = float(cfg.get("scale", 16))
        if scale <= 0:
            return
        rings = max(1, min(3, int(cfg.get("rings", 2))))
        ring_speed = max(0.05, float(cfg.get("ring_speed", 0.4)))
        falloff = max(1.0, float(cfg.get("falloff", 4)))
        radius = float(cfg.get("radius", 260))
        base_speed = max(1.0, float(cfg.get("speed", 6)))

        # ⛔ 丙-10 P0-5 关键修正（_c10_verify2.py F5 实锤：多数帧「非透明采样=0」）：
        #   原 `age = now - _t0`，而 `_t0` 每次鼠标移动都被重置 ⇒ age≈0 ⇒ 半径 r≈0
        #   ⇒ **刚停下时环还没长出来**，要等一整圈（≈2.5s）才可见。
        #   web 真值（console_html.py L6605）用的是**绝对时间**：
        #     `phase = ((now/1000) * W.ring_speed) % 1` —— 与「何时移动」无关，
        #   环永远在某个相位上（常驻波场），光标只决定**圆心**。
        #   ⇒ 对齐 web：用绝对时间做相位，`_t0` 只留作「投石时刻」的语义记录。
        phase0 = ((time.monotonic() * ring_speed)) % 1.0
        cx, cy = self._pos.x(), self._pos.y()

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setClipRect(0, 0, w, h)
        for i in range(rings):
            ph = (phase0 + i / float(rings)) % 1.0     # 各环错相位（绝对时间驱动）
            r = 24.0 + ph * (radius - 24.0)            # 从 24px 起（不等 0，立刻可见）
            # 距离衰减（web falloff 语义）：越远越弱，模块外几乎无影响
            fade = max(0.0, 1.0 - (r / max(1.0, radius)) ** (falloff / 4.0))
            # 波前内部的频闪（web 基础波速语义）：sin 让环亮度起伏。
            # ⛔ 丙-10 P0-5：原 `0.5 + 0.5*sin` 会**整帧压到 0**（sin=-1 时全暗）
            #   ⇒ 环在暗相时肉眼当它「没画」。改为 0.6~1.0 区间起伏（永不为 0）。
            shimmer = 0.6 + 0.4 * math.sin(time.monotonic() * base_speed + i * 1.7)
            # 基础可见度：energy 只做**增强**（0.62→1.0 系数），不参与「有没有」
            # ——web 的波纹是常驻透镜，静止时也在（见 config.py 注释「控制台可调」）。
            vis = 0.62 + 0.38 * self._energy
            a = int(max(0, min(190, scale * 5.2 * fade * shimmer * vis)))
            if a <= 0:
                continue
            pen = QPen(QColor(255, 255, 255, a))
            # 线宽随能量略增（web 位移幅度也随 mouseSpeed 变），下界 1.6px 保可见
            pen.setWidthF(max(1.6, min(9.0, scale / 3.4 * (0.8 + 0.5 * self._energy))))
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            # 径向环（略扁 = 水面透视）；再叠一层内环增厚波前（投石入水的手感）
            p.drawEllipse(QPointF(cx, cy), r, r * 0.62)
            if r > 18:
                pen2 = QPen(QColor(255, 255, 255, max(0, int(a * 0.5))))
                pen2.setWidthF(max(1.0, pen.widthF() * 0.45))
                p.setPen(pen2)
                p.drawEllipse(QPointF(cx, cy), r * 0.88, r * 0.88 * 0.62)
        p.restore()
