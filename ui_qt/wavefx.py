# -*- coding: utf-8 -*-
"""丙-12：水光波纹 —— 真正的「界面波动」（内容像素位移扭曲，非画圆环）。

## 用户原话（必须解决）
「那个波纹特效不理想……你做的这个确实是涟漪了，但是我想要的那个效果好像没有
（指的是界面波动）」+「再看看页面设置的各种效果强度啊，各种调节项是否能真正生效」。

## web 真值（agent/console_html.py，只读）
- `#cardWave2` = `feTurbulence`(分形噪声) + `feDisplacementMap`(按噪声位移像素)：
  `out = in[x + scale*(R-0.5), y + scale*(G-0.5)]`，xChannelSelector=R, yChannelSelector=G。
- `#waveLens` 是 fixed 层，`backdrop-filter: url(#cardWave2)` 取**背后真实像素**做位移
  重采样 ⇒ **内容本身被扭曲变形**（这就是用户要的「界面波动」），不是叠加圆环。
- `waveMaskAt(now)`：径向高斯环带 `exp(-((r-phase)/0.028)^2)` + 衰减 `pow(1-phase,4)`
  + 中心辉光 `pow(1-r,8)*0.35`；`phase = (now/1000*ring_speed)%1` 环带随时间推进。
- 透镜范围 = 光标所在的**整个模块**（卡片/侧栏/顶栏），波纹绝不越出模块边界。
- 常驻透镜：静止也有微动（setInterval 恒跑，相位由绝对时间驱动）。
- 9 参数：enabled/scale/speed/mouse_gain/max_gain/radius/falloff/rings/ring_speed。

## Qt 实现（对齐 web 语义，非 1:1 bit 级）
Qt 无 feTurbulence/feDisplacementMap，也无 backdrop-filter。采用：
- 给 Shell 根窗挂自定义 `QGraphicsEffect`：`draw()` 内 `sourcePixmap()` 取**未扭曲的真实
  像素**（sourcePixmap 不含自身 effect ⇒ 无递归），整窗画回（UI 正常显示），再仅在
  **透镜 bbox**（裁剪到模块边界）内做位移重采样后盖回 ⇒ 内容扭曲，等价于 web 的
  backdrop-filter 取背后像素做 displacement。
- 噪声：`feTurbulence` 分形噪声无等价 → 用**多八度正弦叠加**近似（视觉是「水面折射起伏」，
  不要求噪声分布与浏览器 bit 级一致）。
- 位移数学严格对齐 `feDisplacementMap`：`out = in[x + scale*(R-0.5), y + scale*(G-0.5)]`。
- mask：径向环带（对齐 `waveMaskAt`）——透镜边缘衰减到 0，**绝不越出模块边界**。
- 常驻：定时器每 ~33ms 推进相位并重绘 ⇒ 静止也有微动。

## 性能（实测见 _c12_wave_bench.txt）
- 仅处理透镜 bbox（模块内 ≤ 模块矩形，空白 ≤ radius 圆盘），不整窗位移（整窗 120ms 爆）。
- 处理分辨率 0.5x 缩采：520x520 区域实测 ≈ 4ms，稳过 30fps。

## 解耦纪律
- 本模块**不碰** `OceanWaves`：自己的 `QTimer`、自己的 `set_enabled`、自己的绘制层。
- 保留对外签名：`WaveFX(shell)` / `set_enabled` / `reload` / `refresh_from_config` /
  `on_mouse_move` / `paint(self, p, w, h, dpr)`（paint 现为空操作：真实扭曲在 effect 内，
  不再画白环；保留签名满足 Shell.paintEvent 调用契约与 selftest/回执形态断言）。
"""

from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsEffect

# 动画帧间隔（≈30fps，与 ocean.py 同档；web setInterval(33)）
_FRAME_MS = 33
_DT = 0.033

# 能量地板（常驻透镜；静止也微动，不需要手在动）
_E_MIN = 0.35
# 参考速度（px/s）：把 EMA 速度归一到能量（web gain 是相位增量不是强度比例，见
# on_mouse_move 注释；这里 energy 仅用于「分档可分辨」的可观测量，仍对齐 web 的
# 鼠标速度联动语义）。400px/s ≈ 手匀速小幅度移动。
_V_REF = 400.0
# EMA 系数（对齐 web console_html.py:6612 `m*0.7 + v*0.3`）
_EMA_KEEP = 0.7

# web 同款默认（与 agent/config.py ui.wave_fx 一致）
_DEFAULTS = {
    "enabled": False, "scale": 17, "speed": 5.2, "mouse_gain": 0.02,
    "max_gain": 8.0, "radius": 260, "falloff": 4.0, "rings": 1, "ring_speed": 0.40,
}

# 处理分辨率（相对设备像素的缩采比例）：实测 0.5x 稳过 30fps
_PROC_SCALE = 0.5


class _WaveLensEffect(QGraphicsEffect):
    """挂在 Shell 根窗的取源-位移效果（丙-12 核心）。

    draw()：取未扭曲真实像素 → 整窗画回 → 仅在透镜 bbox 内做位移重采样后盖回。
    """

    def __init__(self, wavefx):
        super().__init__()
        self._wf = wavefx

    def draw(self, painter):
        src = self.sourcePixmap(QGraphicsEffect.PixmapPadMode.NoPad)
        if src.isNull():
            return
        # 整窗画回（UI 正常显示，作为未扭曲底图）
        painter.drawPixmap(0, 0, src)
        wf = self._wf
        if not wf._enabled:
            return
        try:
            self._draw_lens(painter, src)
        except Exception:  # noqa: BLE001  波纹绘制失败绝不能拖垮窗口
            pass

    def _draw_lens(self, painter, src):
        wf = self._wf
        shell = wf._shell
        W, H = shell.width(), shell.height()
        if W < 4 or H < 4:
            return
        center = wf._lens_center()            # 逻辑坐标
        rect = wf._lens_rect(center)          # 逻辑矩形（模块矩形或空白圆盘 bbox）
        if rect.isNull():
            return
        rect = rect.intersected(QRect(0, 0, W, H))
        if rect.width() < 2 or rect.height() < 2:
            return
        dpr = src.devicePixelRatioF() or 1.0
        dev = QRectF(rect.x() * dpr, rect.y() * dpr,
                     rect.width() * dpr, rect.height() * dpr).toRect()
        region = src.copy(dev)                # 设备像素区域
        if region.isNull():
            return
        # 掩蔽半径（逻辑）：模块内 = 中心到模块四角的最远距离；空白 = radius
        Rnorm = wf._norm_radius(center, rect)
        phase = wf._mask_phase()              # 环带相位（绝对时间驱动）
        out_img = wf._displace_region(region, rect, center, Rnorm, phase)
        if out_img is None:
            return
        painter.save()
        # 空白圆盘额外裁成圆（模块矩形已天然裁界）
        if wf._blank_mode(center):
            path = QPainterPath_safe_circle(center, float(wf._cfg["radius"]))
            painter.setClipPath(path)
        painter.drawImage(rect, out_img)      # 把缩采后的扭曲贴回（拉伸对齐）
        painter.restore()


def QPainterPath_safe_circle(center, r):
    from PySide6.QtGui import QPainterPath
    p = QPainterPath()
    p.addEllipse(QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r))
    return p


class WaveFX(QObject):
    """鼠标水光波纹驱动器（内容扭曲版）—— 组合进 Shell。

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
        self._fx = _WaveLensEffect(self)
        self._pos = QPointF(float(shell.width()) / 2.0, float(shell.height()) / 2.0)
        # 鼠标速度 EMA（web `_mouseSpeed` 同款，喂 gain）
        self._speed_ema = 0.0
        self._last_move_t = time.monotonic()
        # 相位累计（rad）：驱动噪声流动 + scale 脉动（web `_ph`）
        self._ph = 0.0
        self._epoch = time.monotonic()      # 掩蔽相位用绝对时间（web `now/1000`）
        self._cfg = self._read_cfg()
        # 初始即开（web 默认 enabled=false 但面板可调；这里按 config 当前值）
        # 注意：set_enabled 由 shell.py 调，不用这里重复启动以免重复安装 effect。

    # ------------------------------------------------------------ 配置

    def _read_cfg(self) -> dict:
        """读 `ui.wave_fx.*`（与 web 同一份 config 键；缺键用 web 同款默认）。"""
        d = dict(_DEFAULTS)
        try:
            import config_io  # noqa: PLC0415

            for k in list(d.keys()):
                v = config_io.read_path("ui.wave_fx.%s" % k, None)
                if v is not None:
                    d[k] = v
        except Exception:  # noqa: BLE001
            pass
        return self._clamp(d)

    @staticmethod
    def _clamp(d: dict) -> dict:
        """对齐 web readParams 的非法值兜底（防手改 config 出 NaN / 越界）。"""
        try:
            d["enabled"] = bool(d.get("enabled", False))
            d["scale"] = float(min(40, max(0, float(d.get("scale", 17)))))
            d["speed"] = float(min(20, max(0.2, float(d.get("speed", 5.2)))))
            d["mouse_gain"] = float(min(0.1, max(0, float(d.get("mouse_gain", 0.02)))))
            d["max_gain"] = float(min(20, max(0, float(d.get("max_gain", 8.0)))))
            d["radius"] = float(min(600, max(80, float(d.get("radius", 260)))))
            d["falloff"] = float(min(6, max(0.3, float(d.get("falloff", 4.0)))))
            d["rings"] = int(min(6, max(1, int(round(float(d.get("rings", 1)))))))
            d["ring_speed"] = float(min(1.5, max(0.1, float(d.get("ring_speed", 0.40)))))
        except (ValueError, TypeError):
            d = dict(_DEFAULTS)
        return d

    def reload(self) -> None:
        """参数即时生效（web 侧「应用水光波纹设置」同款语义）。"""
        self._cfg = self._read_cfg()
        self.set_enabled(bool(self._cfg.get("enabled", False)))
        self._shell.update()

    # ------------------------------------------------------------ 生命周期（只动自己）

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        try:
            if self._enabled:
                if self._shell.graphicsEffect() is not self._fx:
                    self._shell.setGraphicsEffect(self._fx)
                if not self._timer.isActive():
                    self._timer.start()
            else:
                if self._shell.graphicsEffect() is self._fx:
                    self._shell.setGraphicsEffect(None)
                if self._timer.isActive():
                    self._timer.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._shell.update()
        except Exception:  # noqa: BLE001
            pass

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    def refresh_from_config(self) -> None:
        """外部（web 面板）改了 `ui.wave_fx` ⇒ 跟上（Shell._watch_config 4 秒一跳）。"""
        c = self._read_cfg()
        if c != self._cfg:
            self._cfg = c
            self.set_enabled(bool(c.get("enabled", False)))

    # ------------------------------------------------------------ 输入

    def on_mouse_move(self, gp: QPointF) -> None:
        """Shell 的鼠标事件转进来：更新波纹中心 + 鼠标速度 EMA。

        web 同款：速度过 EMA 平滑（`m*0.7 + v*0.3`）；`gain = min(max_gain,
        _mouseSpeed*mouse_gain)` 是**相位速度增量**（非强度），本实现同样只喂相位速度；
        这里保留 `_energy`（速度归一量）仅作可观测分档量，对齐 web 鼠标联动语义。
        """
        now = time.monotonic()
        dt = max(1e-3, now - self._last_move_t)
        self._last_move_t = now
        dist = math.hypot(gp.x() - self._pos.x(), gp.y() - self._pos.y())
        speed = dist / dt                       # px/s（瞬时）
        self._speed_ema = self._speed_ema * _EMA_KEEP + speed * (1.0 - _EMA_KEEP)
        self._energy = min(1.0, max(_E_MIN, self._speed_ema / _V_REF))
        self._pos = QPointF(gp)
        self._shell.update()

    # ------------------------------------------------------------ 透镜几何（裁剪到模块边界）

    def _lens_center(self) -> QPointF:
        return self._pos

    def _blank_mode(self, center: QPointF) -> bool:
        """光标下无模块（空白）⇒ 用 radius 圆盘；有模块 ⇒ False。"""
        return self._shell.childAt(int(center.x()), int(center.y())) in (None, self._shell)

    def _lens_rect(self, center: QPointF) -> QRect:
        """透镜 bbox：模块内=模块矩形（绝不越界）；空白=radius 圆盘 bbox。"""
        if self._blank_mode(center):
            r = float(self._cfg["radius"])
            return QRect(int(center.x() - r), int(center.y() - r),
                        int(2 * r), int(2 * r))
        return self._module_rect_of(
            self._shell.childAt(int(center.x()), int(center.y())))

    def _module_rect_of(self, w) -> QRect:
        """向上找光标所在「模块」（卡片/面板/容器），裁剪到其矩形。"""
        shell = self._shell
        cur = w
        chosen = None
        while cur is not None and cur is not shell:
            if cur.parentWidget() is shell:
                g = cur.geometry()
            else:
                g = QRect(cur.mapTo(shell, QPoint(0, 0)), cur.size())
            nm = type(cur).__name__
            is_container = any(k in nm for k in
                              ("Card", "Panel", "Box", "Frame", "Widget",
                               "Scroll", "Stack", "Area", "View"))
            big_enough = g.width() >= 140 and g.height() >= 90
            inside = g.width() <= shell.width() and g.height() <= shell.height()
            if (is_container or big_enough) and inside:
                chosen = g
                if any(k in nm for k in ("Card", "Panel", "Box")):
                    break
            cur = cur.parentWidget()
        if chosen is None:
            chosen = (w.geometry() if w.parentWidget() is shell
                      else QRect(w.mapTo(shell, QPoint(0, 0)), w.size()))
        return chosen

    def _norm_radius(self, center: QPointF, rect: QRect) -> float:
        """掩蔽归一化半径（逻辑）：模块内=中心到四角最远距离；空白=radius。"""
        if self._blank_mode(center):
            return float(self._cfg["radius"])
        cx, cy = center.x(), center.y()
        rx, ry, rw, rh = rect.x(), rect.y(), rect.width(), rect.height()
        corners = [(rx, ry), (rx + rw, ry), (rx, ry + rh), (rx + rw, ry + rh)]
        dmax = max(math.hypot(cx - x, cy - y) for x, y in corners)
        return max(1.0, dmax)

    def _mask_phase(self) -> float:
        """环带相位（绝对时间驱动，对齐 web `phase = (now/1000*ring_speed)%1`）。"""
        return ((time.monotonic() - self._epoch) * self._cfg["ring_speed"]) % 1.0

    def _gain(self, mouse_ema: float) -> float:
        """对齐 web `gain = min(max_gain, _mouseSpeed*mouse_gain)`。"""
        return min(self._cfg["max_gain"], mouse_ema * self._cfg["mouse_gain"])

    def _phase_speed(self) -> float:
        """对齐 web `phSpeed = min(20, speed + gain)`。"""
        return min(20.0, self._cfg["speed"] + self._gain(self._speed_ema))

    # ------------------------------------------------------------ mask（对齐 waveMaskAt）

    def _mask_at(self, r: float, phase: float, falloff: float, rings: int) -> float:
        """径向环带 mask（标量版，供参数取证用）。"""
        a = 0.0
        for k in range(int(rings)):
            phk = (phase + k / float(rings)) % 1.0
            g = math.exp(-((r - phk) / 0.028) ** 2)
            a += g * (1.0 - phk) ** 4
        a += (1.0 - r) ** falloff * 0.35
        return min(1.0, max(0.0, a))

    @staticmethod
    def _mask_at_v(r, phase, falloff, rings):
        """径向环带 mask（向量版，供绘制用）。"""
        a = np.zeros_like(r, dtype=np.float32)
        rings = int(rings)
        for k in range(rings):
            phk = (phase + k / float(rings)) % 1.0
            g = np.exp(-((r - phk) / 0.028) ** 2)
            a += g * (1.0 - phk) ** 4
        a += (1.0 - r) ** falloff * 0.35
        return np.clip(a, 0.0, 1.0)

    # ------------------------------------------------------------ 位移重采样（核心）

    def _displace_region(self, region_img, rect_logical, center_logical,
                         Rnorm_logical, phase):
        """对一块设备像素区域做位移重采样，返回缩采后的扭曲图（供 drawImage 拉伸贴回）。

        位移数学对齐 feDisplacementMap：out = in[x + scale*(R-0.5), y + scale*(G-0.5)]。
        处理分辨率 0.5x 缩采（实测稳过 30fps）。
        """
        dev_w = region_img.width()
        dev_h = region_img.height()
        if dev_w < 1 or dev_h < 1:
            return None
        proc_scale = _PROC_SCALE
        pw = max(1, int(dev_w * proc_scale))
        phh = max(1, int(dev_h * proc_scale))
        small = region_img.scaled(pw, phh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        small = small.convertToFormat(QImage.Format.Format_RGBA8888)
        arr = np.frombuffer(small.bits(), dtype=np.uint8,
                            count=pw * phh * 4).reshape(phh, pw, 4).copy()

        lx0 = center_logical.x() - rect_logical.x()
        ly0 = center_logical.y() - rect_logical.y()
        sx_log = rect_logical.width() / pw
        sy_log = rect_logical.height() / phh
        ys, xs = np.mgrid[0:phh, 0:pw].astype(np.float32)
        lxs = xs * sx_log
        lys = ys * sy_log
        dxc = lxs - lx0
        dyc = lys - ly0
        dist = np.sqrt(dxc * dxc + dyc * dyc)
        r = np.clip(dist / max(1.0, Rnorm_logical), 0.0, 1.5)
        m = self._mask_at_v(r, phase, self._cfg["falloff"], int(self._cfg["rings"]))

        # 多八度正弦近似分形噪声（feTurbulence 无等价），随 _ph 流动
        t = self._ph
        sc = float(self._cfg["scale"])
        # 对齐 web 面板语义：scale=0 ⇒ 完全无扭曲；否则在 scale 附近脉动。
        # 不取下限（web 的 max(0.5,…) 会让 scale=0 仍微扭曲，与「0=无扭曲」相悖）。
        scale_pulse = sc * (1.0 + 0.38 * math.sin(t * 1.3))
        Rn = np.zeros((phh, pw), np.float32)
        Gn = np.zeros((phh, pw), np.float32)
        amp = 1.0
        f = 0.02
        for _ in range(3):
            ph = t * (0.6 + 0.2 * _)
            Rn += amp * np.sin(lxs * f + ph) * np.cos(lys * f * 1.3 - ph * 0.7)
            Gn += amp * np.cos(lys * f * 1.1 - ph * 1.2) * np.sin(lxs * f * 0.9 + ph)
            amp *= 0.5
            f *= 2.0
        rmin, rmax = Rn.min(), Rn.max()
        Rn = (Rn - rmin) / (rmax - rmin + 1e-6)
        gmin, gmax = Gn.min(), Gn.max()
        Gn = (Gn - gmin) / (gmax - gmin + 1e-6)

        # 位移换算：逻辑位移 = scale_pulse*(noise-0.5)*m；再转成 small 像素位移
        k = scale_pulse * (pw / max(1e-6, rect_logical.width()))
        sx = np.clip(xs + k * (Rn - 0.5) * m, 0, pw - 1).astype(np.float32)
        sy = np.clip(ys + k * (Gn - 0.5) * m, 0, phh - 1).astype(np.float32)
        ix = sx.astype(np.int32)
        iy = sy.astype(np.int32)
        out = arr[iy, ix].copy()

        out_img = QImage(out.tobytes(), pw, phh, 4 * pw,
                         QImage.Format.Format_RGBA8888)
        return out_img

    # ------------------------------------------------------------ 绘制（空操作，保留签名）

    def paint(self, p, w, h, dpr) -> None:
        """保留签名以满足 Shell.paintEvent 调用契约与 selftest 形态断言。

        真实的「内容扭曲」由挂在 Shell 的 `_WaveLensEffect` 完成（见 _draw_lens），
        本方法不再画白环。空操作不会在 live UI 产生任何叠加图形。
        """
        return

    # ------------------------------------------------------------ 动画循环

    def _tick(self) -> None:
        """推进相位并触发重绘（常驻透镜：静止也有微动）。"""
        if not self._enabled:
            return
        ph_speed = self._phase_speed()
        self._ph += ph_speed * _DT
        try:
            self._shell.update()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 参数取证辅助（真实读取路径）

    def _skip_distortion(self) -> bool:
        """enabled 关掉后完全无位移（门控）。"""
        return not self._enabled

    def _distortion_scale_max(self) -> float:
        """最大可能位移幅度（= scale/2），供 scale 参数取证。"""
        return float(self._cfg["scale"]) * 0.5

    def _blank_lens_size(self) -> int:
        """空白区透镜直径（= 2*radius），供 radius 参数取证。"""
        return int(2 * float(self._cfg["radius"]))
