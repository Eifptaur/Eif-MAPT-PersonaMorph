# -*- coding: utf-8 -*-
"""水光波纹 —— 真正的「界面波动」（内容像素位移扭曲，非画圆环）。

「那个波纹特效不理想……你做的这个确实是涟漪了，但是我想要的那个效果好像没有
（指的是界面波动）」+「再看看页面设置的各种效果强度啊，各种调节项是否能真正生效」。

## web 真值（agent/console_html.py，只读）
- `#cardWave2` = `feTurbulence`(分形噪声) + `feDisplacementMap`(按噪声位移像素)：
  `out = in[x + scale*(R-0.5), y + scale*(G-0.5)]`，xChannelSelector=R, yChannelSelector=G。
- `#waveLens` 是 fixed 层，`backdrop-filter: url(#cardWave2)` 取**背后真实像素**做位移
- `waveMaskAt(now)`：径向高斯环带 `exp(-((r-phase)/0.028)^2)` + 衰减 `pow(1-phase,4)`
  + 中心辉光 `pow(1-r,8)*0.35`；`phase = (now/1000*ring_speed)%1` 环带随时间推进。
- 透镜范围 = 光标所在的**整个模块**（卡片/侧栏/顶栏），波纹绝不越出模块边界。
- 常驻透镜：静止也有微动（setInterval 恒跑，相位由绝对时间驱动）。
- 9 参数：enabled/scale/speed/mouse_gain/max_gain/radius/falloff/rings/ring_speed。

## Qt 实现
Qt 无 feTurbulence/feDisplacementMap，也无 backdrop-filter。采用：
- 给 Shell 根窗挂自定义 `QGraphicsEffect`：`draw()` 内 `sourcePixmap()` 取**未扭曲的真实
  像素**（sourcePixmap 不含自身 effect ⇒ 无递归），整窗画回（UI 正常显示），再仅在
  **透镜 bbox**（裁剪到模块边界）内做位移重采样后盖回 ⇒ 内容扭曲，等价于 web 的
  backdrop-filter 取背后像素做 displacement。
- 噪声：`feTurbulence fractalNoise(numOctaves=2)` 的 CPU 等价 = **平滑
  value noise**（格点随机 seed 固定 + smoothstep 双线性插值）2 八度叠加（f、2f，幅
  1:0.5）。旧「3 八度 sin/cos + 逐帧 min/max 归一化」的病灶：归一化把噪声压成近均匀
  随机 ⇒ 逐点不相干的细碎颗粒，而 web 是低频
  连贯的大漩涡。baseFrequency 按 web 同款公式逐帧呼吸（fx=0.008+0.004·sin(0.9φ)）。
- 位移数学严格对齐 `feDisplacementMap`：`out = in[x + scale*(R-0.5), y + scale*(G-0.5)]`。
  **位移场不乘 mask**（web 同款：扭曲全区域存在，mask 只管显示混合）——旧版把 mask 乘进
  位移幅度 ⇒ 只有细环带在动、中心没有持续翻滚 ⇒ 「扭得狠但没水感」。
- mask（对齐 `waveMaskAt`）：环带·(1-phase)⁴ + 中心辉光 (1-r)⁸·0.35，r=到光标距离/最远角
  距离。**maskImage 语义合成**：final = 原图·(1-m) + 位移图·m，**全分辨率**做——
  m==0 处逐像素等于真原图（不是缩采回拉的近似原图）⇒ 模块边缘/环带外零差异、无缝。
- 常驻：定时器每 ~33ms 推进相位并重绘 ⇒ 静止也有微动。

## 性能
- 仅处理透镜 bbox（模块内 ≤ 模块矩形，空白 ≤ radius 圆盘），不整窗位移。
- 最贵的噪声插值在 0.5x 网格生成（_PROC_SCALE）后 repeat 上采样（噪声低频，块状不可见）；
  位移采样与 mask 合成在**设备全分辨率**做（1080×440 区域 ≈ 25ms，30fps 可达）。

## 解耦纪律
- 本模块**不碰** `OceanWaves`：自己的 `QTimer`、自己的 `set_enabled`、自己的绘制层。
- 保留对外签名：`WaveFX(shell)` / `set_enabled` / `reload` / `refresh_from_config` /
  `on_mouse_move` / `paint(self, p, w, h, dpr)`（paint 现为空操作：真实扭曲在 effect 内，
  不再画白环；保留签名满足 Shell.paintEvent 调用契约与 selftest/回执形态断言）。
"""

from __future__ import annotations

import math
import os
import time

import numpy as np
from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QGraphicsEffect, QWidget

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

# 噪声场生成网格（相对设备像素的缩采比例）：feTurbulence 等价的 value noise 在 0.5x
# 网格生成后 repeat 上采样——噪声波长 ≥60 逻辑 px，2 设备 px 的块状完全不可见；
# 位移采样与 mask 合成仍走设备全分辨率。
_PROC_SCALE = 0.5

# 透镜 bbox 性能上限（逻辑 px，最长边）。模块矩形特别大（如接近全窗的容器）时
# 降级为「光标为中心、_MAX_LENS 见方」的裁剪盘——波纹仍困在模块内语义上等价于 web 的
# 模块透镜，但 render+位移的每帧成本有硬上界（全窗位移 120ms/帧 会拖死 30fps）。
_MAX_LENS = 720

# 位移在抓源 bbox 四边的渐隐带宽（逻辑 px）。web 是全屏连续位移场（backdrop-filter
# 层没有边界概念）；Qt 的透镜 bbox 是有限矩形，环带扫到 bbox 边缘时若位移仍在、盘外为零
# ⇒ 。边缘 mask 归零后过渡自然，方形感消失。
_EDGE_FADE = 48.0

# ⛔ 挂载恢复默认启用 —— draw() 已有签名修正 + 双层 fail-safe
#   （取源失败整帧放弃；_draw_lens 异常吞掉），最坏情况是「无波纹」而非崩溃。
# QT_NO_WAVE=1 为逃生门（禁用挂载）；波纹效果真机验收由
_LENS_MOUNT_OK = os.environ.get("QT_NO_WAVE") != "1"


class _WaveLensEffect(QGraphicsEffect):
    """挂在 Shell 根窗的取源-位移效果。

    draw()：取未扭曲真实像素 → 整窗画回 → 仅在透镜 bbox 内做位移重采样后盖回。
    """

    def __init__(self, wavefx):
        super().__init__()
        self._wf = wavefx

    def draw(self, painter):
        #   （正确签名：sourcePixmap(system, offset, mode)）⇒ **每帧 TypeError**。
        #   该异常发生在 Qt C++ 层调 Python 覆写的边界上，累计触发 PySide6 段错误
        # （0xC0000005）—— 即此所致
        #   （effect 挂在 Shell 根窗上，draw 一炸整窗绘制全断）。
        #   修：签名对齐 + fail-safe —— 取源失败就整帧放弃，绝不让波纹拖垮窗口绘制。
        try:
            src = self.sourcePixmap(Qt.CoordinateSystem.DeviceCoordinates,
                                    None, QGraphicsEffect.PixmapPadMode.NoPad)
        except Exception: # noqa: BLE001
            return
        if src.isNull():
            return
        # 整窗画回（UI 正常显示，作为未扭曲底图）
        painter.drawPixmap(0, 0, src)
        wf = self._wf
        if not wf._enabled:
            return
        try:
            self._draw_lens(painter, src)
        except Exception: # noqa: BLE001  波纹绘制失败绝不能拖垮窗口
            pass

    def _draw_lens(self, painter, src):
        wf = self._wf
        shell = wf._shell
        W, H = shell.width(), shell.height()
        if W < 4 or H < 4:
            return
        center = wf._lens_center() # 逻辑坐标
        rect = wf._lens_rect(center) # 逻辑矩形（模块矩形或空白圆盘 bbox）
        if rect.isNull():
            return
        rect = rect.intersected(QRect(0, 0, W, H))
        if rect.width() < 2 or rect.height() < 2:
            return
        dpr = src.devicePixelRatioF() or 1.0
        dev = QRectF(rect.x() * dpr, rect.y() * dpr,
                     rect.width() * dpr, rect.height() * dpr).toRect()
        region = src.copy(dev) # 设备像素区域
        if region.isNull():
            return
        # 掩蔽半径（逻辑）：模块内 = 中心到模块四角的最远距离；空白 = radius
        Rnorm = wf._norm_radius(center, rect)
        phase = wf._mask_phase() # 环带相位（绝对时间驱动）
        out_img = wf._displace_region(region, rect, center, Rnorm, phase)
        if out_img is None:
            return
        painter.save()
        # 空白圆盘额外裁成圆（模块矩形已天然裁界）
        if wf._blank_mode(center):
            path = QPainterPath_safe_circle(center, float(wf._cfg["radius"]))
            painter.setClipPath(path)
        painter.drawImage(rect, out_img) # 把缩采后的扭曲贴回（拉伸对齐）
        painter.restore()


class WaveOverlay(QWidget):
    """波纹覆盖层。

    全窗透明子控件（WA_TransparentForMouseEvents：不挡任何点击/拖动），paintEvent 只做
    「位移 + 贴回」——**抓源已移到 _tick（事件循环态）的 `_grab_src()`**。

    ## 根因记录（「一点动静都没有」的真根，三处叠加）
    1. **QPixmap 混进 QImage 链路**： paintEvent 里 `shell.grab()` 产 QPixmap 直接传
       `_displace_region`，其中 `convertToFormat(QImage.Format...)` / `bits()` 都是 QImage
       独有方法 ⇒ 每帧 AttributeError ⇒ 被 paintEvent 的 try/except **静默吞掉** ⇒ 零视觉。
       （selftest 只用 QImage 直测 _displace_region，没覆盖这条真链路。）
    2. **paint 内 grab 父窗的重入**：paintEvent 执行期间同步 render 整棵父窗——脆弱且贵。
    3. **childAt 被 overlay 自己截胡**：全窗 overlay 是 shell 最顶子控件 ⇒ `childAt()` 恒返
       overlay ⇒ `_blank_mode` 恒 False、`_module_rect_of(overlay)` 恒返回全窗矩形。
    4. **shell.resizeEvent 调用的 `sync_overlay()` 在本模块根本不存在** ⇒ AttributeError
       被 shell 的 except 吞掉 ⇒ overlay 几何只在创建那一刻对，窗口变化后全错。

    ## 架构
    - `_tick`（事件循环态，非 paint 期间）：`_grab_src()` 抓透镜区域到 **无 DPR 的 QImage**
      （ 起走 `shell.grab(QRect)` 官方路径；抓源与绘制解耦、无重入；抓取期间
      `_grabbing` 守卫防 overlay 在 DrawChildren 时自绘旧帧造成自反馈）。
    - `paintEvent`：读 `_src_img` → `_displace_region`（纯 QImage 链路）→ drawImage 1:1 贴回。
    - `_hit_deep(x, y)`：模拟 childAt 的「最深可见子控件」下钻，**排除 overlay 自身**，
      `_blank_mode`/`_lens_rect` 改用它 ⇒ 模块语义恢复、空白圆盘模式恢复。
    - `sync_overlay()`：补上缺失方法，shell.resizeEvent 每跳同步 overlay 几何。
    """

    def __init__(self, wavefx):
        super().__init__(wavefx._shell)
        self._wf = wavefx
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("background:transparent;")

    def paintEvent(self, ev): # noqa: N802
        wf = self._wf
        if not wf._enabled or wf._grabbing:
            return
        pm = wf._src_img
        rect = wf._src_rect
        if pm is None or pm.isNull() or rect.width() < 8 or rect.height() < 8:
            return
        try:
            center = wf._lens_center()
            out = wf._displace_region(pm, rect, center,
                                      wf._src_rnorm, wf._mask_phase())
            if out is None:
                return
            # 不再需要圆盘 clip——_displace_region 已按 mask 与原图合成，
            # m==0 处逐像素等于原图（无缝），空白圆盘外天然无位移。
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            p.drawImage(rect, out) # out 与 src 同为设备分辨率，1:1 贴回
            p.end()
        except Exception: # noqa: BLE001 — 波纹绘制失败绝不能拖垮窗口
            return


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
        super().__init__() # 不挂 QObject 父：shell 侧持引用保活即可
        self._shell = shell
        self._timer = QTimer(self)
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self._enabled = False
        self._fx = None # QGraphicsEffect 对顶层窗口不生效，弃用
        self._overlay = None # WaveOverlay 子控件（set_enabled 懒建）
        self._grabbing = False # render/paint 防重入守卫（_grab_src ↔ overlay.paintEvent）
        self._src_img = None # _grab_src 产的透镜区域 QImage（paint 只读）
        self._src_rect = QRectF() # 对应逻辑矩形
        self._src_rnorm = 1.0 # 按模块矩形取的 mask 归一化半径（web 最远角）
        self._pos = QPointF(float(shell.width()) / 2.0, float(shell.height()) / 2.0)
        # 鼠标速度 EMA（web `_mouseSpeed` 同款，喂 gain）
        self._speed_ema = 0.0
        self._last_move_t = time.monotonic()
        # 相位累计（rad）：驱动噪声流动 + scale 脉动（web `_ph`）
        self._ph = 0.0
        self._epoch = time.monotonic() # 掩蔽相位用绝对时间（web `now/1000`）
        self._cfg = self._read_cfg()
        # 初始即开（web 默认 enabled=false 但面板可调；这里按 config 当前值）
        # 注意：set_enabled 由 shell.py 调，不用这里重复启动以免重复安装 effect。

    # ------------------------------------------------------------ 配置

    def _read_cfg(self) -> dict:
        """读 `ui.wave_fx.*`（与 web 同一份 config 键；缺键用 web 同款默认）。"""
        d = dict(_DEFAULTS)
        try:
            import config_io # noqa: PLC0415

            for k in list(d.keys()):
                v = config_io.read_path("ui.wave_fx.%s" % k, None)
                if v is not None:
                    d[k] = v
        except Exception: # noqa: BLE001
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
                # WaveOverlay 子控件（懒建 + 跟随 shell 几何 + raise_ 置顶）。
                # QGraphicsEffect 对顶层窗口不生效 ⇒ 弃用 effect 路线（
                #   开关已开、effect 已挂、画面纹丝不动 = 此根因）。QT_NO_WAVE=1 逃生门。
                if _LENS_MOUNT_OK and self._overlay is None:
                    self._overlay = WaveOverlay(self)
                    self._overlay.setGeometry(self._shell.rect())
                    self._overlay.show()
                    self._overlay.raise_()
                self.sync_overlay() # 几何同步收敛到一个方法
                if not self._timer.isActive():
                    self._timer.start()
            else:
                if self._timer.isActive():
                    self._timer.stop()
        except Exception: # noqa: BLE001
            pass
        try:
            self._shell.update()
        except Exception: # noqa: BLE001
            pass

    def sync_overlay(self) -> None:
        """overlay 几何跟随 shell（shell.resizeEvent 每跳转发）。

         硬伤补漏：shell.py:resizeEvent 一直调 `self._wavefx.sync_overlay()`，
        但 忘了在本模块定义这个方法 ⇒ AttributeError 被 shell 的 except 吞掉 ⇒
        overlay 几何只在创建那一刻对、窗口变化后全错。
        """
        ov = self._overlay
        if ov is None:
            return
        try:
            ov.setGeometry(self._shell.rect())
        except Exception: # noqa: BLE001
            pass

    # ------------------------------------------------------------ 抓源（事件循环态，与绘制解耦）

    def _grab_src(self) -> None:
        """把透镜 bbox 的真实像素抓到 QImage。

        在 `_tick`（timer timeout，事件循环态）调用——**不在 paintEvent 里**。产物存
        `_src_img/_src_rect`，overlay 的 paintEvent 只读。

         抓源路径改 `shell.grab(QRect)`（Qt 官方 QWidget::grab，DPR/坐标语义有
        保证）。此前 `shell.render(painter+scale+translate, QRegion)` 在真机 DPR=1.5
        下产物**整块错乱**：大片未初始化黑 + 内容错位（_c33_srcprobe 贴图铁证——
        render 版 vs grab 版 interior 平均差 201/255）——
        细小刮痕」正是这块错乱内容的贴回； 探针的「可见」大半也是它贡献的
        假阳性。当年弃用 grab 的理由是「paint 期间抓父窗=重入」—— 已把抓源
        挪到 _tick（事件循环态），重入前提不复存在，grab 安全。
        （grab 仍以 `_grabbing` 包住：render(DrawChildren) 会重入 overlay.paintEvent，
        不挡的话旧波纹帧会被画进新源里造成自反馈。）

        模块矩形超 `_MAX_LENS` 降级时，裁剪盘**与模块矩形求交**（不是只与窗口求交）
        ——波纹绝不越出模块边界（web `fitLensToHost` 语义）；`_src_rnorm` 也按**模块矩形**
        取归一化半径（web 径向渐变的 r=1=模块最远角），降级不改变波纹的空间尺度感。
        """
        try:
            shell = self._shell
            center = self._lens_center()
            mod = self._lens_rect(center) # 模块矩形 / 空白圆盘 bbox（语义边界）
            rect = QRectF(mod)
            rect = rect.intersected(QRectF(0, 0, shell.width(), shell.height()))
            if rect.width() > _MAX_LENS or rect.height() > _MAX_LENS:
                half = _MAX_LENS // 2
                disk = QRectF(center.x() - half, center.y() - half,
                              float(_MAX_LENS), float(_MAX_LENS))
                # 裁剪盘**与模块矩形求交**——波纹绝不越出模块边界（fitLensToHost）
                rect = disk.intersected(QRectF(mod)).intersected(
                    QRectF(0, 0, shell.width(), shell.height()))
            # C：rect 对齐到**设备像素网格**。逻辑整点 ×1.5 = 半设备像素（如 y=155 →
            # 232.5），drawImage 落在半像素上 ⇒ 整块内容亚像素重采样模糊 ⇒ 卡片边线/
            # 锐利细节沿 rect 顶/左缘发糊成「分界线」。
            dpr = shell.devicePixelRatioF() or 1.0
            x2 = math.floor(rect.x() * dpr)
            y2 = math.floor(rect.y() * dpr)
            w2 = int(round(rect.width() * dpr))
            h2 = int(round(rect.height() * dpr))
            rect = QRectF(x2 / dpr, y2 / dpr, w2 / dpr, h2 / dpr)
            if rect.width() < 8 or rect.height() < 8:
                self._src_img = None
                self._src_rect = QRectF()
                return
            # grab 用整数逻辑矩形（外扩 ≤1 逻辑px），随后在结果里按**设备坐标**裁回
            # 对齐 rect——grab(QRect) 只吃整数逻辑坐标，直接取整会重新引入半像素。
            gx = math.floor(rect.x())
            gy = math.floor(rect.y())
            gw = math.ceil(rect.x() + rect.width()) - gx
            gh = math.ceil(rect.y() + rect.height()) - gy
            self._grabbing = True
            try:
                pm = shell.grab(QRect(gx, gy, gw, gh))
            finally:
                self._grabbing = False
            if pm.isNull():
                self._src_img = None
                self._src_rect = QRectF()
                return
            # ⛔ 根因纪律：处理用 QImage **不带 DPR**（raw=设备像素）。
            #   带 DPR 的图会把毒性带进 numpy 管线——Qt 规定 scaled() 的入参出参都是
            #   设备无关像素 ⇒ 缓冲尺寸与 frombuffer 的 count 不一致 ⇒ 剪切+错位。
            img = pm.toImage().convertToFormat(
                QImage.Format.Format_ARGB32_Premultiplied)
            img.setDevicePixelRatio(1.0)
            dx = x2 - int(round(gx * dpr))
            dy = y2 - int(round(gy * dpr))
            if dx != 0 or dy != 0 or img.width() != w2 or img.height() != h2:
                img = img.copy(dx, dy, w2, h2) # 精确设备对齐，零半像素
            self._src_img = img
            self._src_rect = QRectF(rect)
            self._src_rnorm = self._norm_radius(center, mod)
        except Exception: # noqa: BLE001 — 抓源失败宁可这帧没波纹，也不拖垮 UI
            self._src_img = None
            self._src_rect = QRectF()

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
        speed = dist / dt # px/s（瞬时）
        self._speed_ema = self._speed_ema * _EMA_KEEP + speed * (1.0 - _EMA_KEEP)
        self._energy = min(1.0, max(_E_MIN, self._speed_ema / _V_REF))
        self._pos = QPointF(gp)
        self._shell.update()

    # ------------------------------------------------------------ 透镜几何（裁剪到模块边界）

    def _hit_deep(self, x: int, y: int):
        """shell 逻辑坐标 → 该点下**最深的可见子控件**（childAt 的 overlay-排除版）。

        childAt 不能用：全窗 overlay 是 shell 最顶子控件 ⇒ 恒返 overlay 自己。
        这里逐层下钻：每层在直接子控件里找含该点者（children() 顺序=z 序 ⇒ 取最后一个命中），
        命中后把坐标换成相对新层，继续下钻；overlay 自身跳过。无命中 ⇒ None（=空白画卷）。
        """
        cur = self._shell
        ov = self._overlay
        veil = getattr(self._shell, "_fade_veil", None) # 切页 180ms 全窗渐隐 veil 同样排除
        lx, ly = x, y
        hit = None
        while True:
            nxt = None
            ngx = ngy = 0
            for ch in cur.children():
                if ch is ov or ch is veil or not isinstance(ch, QWidget):
                    continue
                if not ch.isVisible():
                    continue
                g = ch.geometry()
                if g.contains(lx, ly):
                    nxt = ch # 不 break：z 序最上（最后命中）优先
                    ngx, ngy = g.x(), g.y()
            if nxt is None:
                return hit
            hit = nxt
            cur = nxt
            lx -= ngx
            ly -= ngy

    def _lens_center(self) -> QPointF:
        return self._pos

    def _blank_mode(self, center: QPointF) -> bool:
        """光标下无模块（落在 shell 背景画卷上）⇒ 用 radius 圆盘；有模块 ⇒ False。

        （childAt 换 _hit_deep——overlay 全窗覆盖后 childAt 恒返 overlay，
        此分支此前永不触发、透镜恒为全窗矩形。）
        """
        hit = self._hit_deep(int(center.x()), int(center.y()))
        return hit is None or hit is self._shell

    def _lens_rect(self, center: QPointF) -> QRect:
        """透镜 bbox：模块内=模块矩形（绝不越界）；空白=radius 圆盘 bbox。"""
        hit = self._hit_deep(int(center.x()), int(center.y()))
        if hit is None or hit is self._shell:
            r = float(self._cfg["radius"])
            return QRect(int(center.x() - r), int(center.y() - r),
                         int(2 * r), int(2 * r))
        return self._module_rect_of(hit)

    def _module_rect_of(self, w) -> QRect:
        """光标所在「模块」矩形 —— 对齐 web `closest('.card,.side,.topbar')` 语义。

        规则：
        ① 顶栏内布局 wrapper（类名含 Widget/Frame）被误判成模块 ⇒ 透镜停在中途小容器，
          顶栏右段吃不到特效；②启发式可预测性差。改为确定性爬树：
        - 向上遇 **Card/Panel/Box 卡片级类名 → 取它**（web 卡片优先）；
        - 否则一路爬到 **shell 的直接子控件**（顶栏/侧栏/内容区等顶层模块）→ 取它；
        - 兜底 = 控件自身矩形。
        """
        shell = self._shell
        cur = w
        while cur is not None and cur is not shell:
            parent = cur.parentWidget()
            nm = type(cur).__name__
            if parent is shell:
                return cur.geometry()
            if any(k in nm for k in ("Card", "Panel", "Box")):
                return QRect(cur.mapTo(shell, QPoint(0, 0)), cur.size())
            cur = parent
        return QRect(w.mapTo(shell, QPoint(0, 0)), w.size())

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
        """对一块**设备分辨率**区域做「fractalNoise 位移 + maskImage 合成」。

        ⛔ 入参必须是 **无 DPR** 的 QImage（raw=设备像素）——带 DPR 的图会让 scaled()
        产出更大的原始缓冲、bits() 按逻辑数读 ⇒ 剪切+半块处理。

        对齐 web 真值三件套（console_html.py #cardWave2 / waveMaskAt / #waveLens）：
        1. 位移场 = fractalNoise 等价的平滑 value noise（2 八度、seed 固定），
           baseFrequency 逐帧呼吸（fx=0.008+0.004·sin(0.9φ)，cycles/逻辑px，web 同款）；
        2. feDisplacementMap 语义 out = in + scale·(R−0.5)（scale=17·(1+0.38·sin(1.3φ))
           逻辑 px，转设备 px 乘 dpr）——**不乘 mask**（web 的位移全区域存在，mask 只管
           显示混合；旧版乘 mask ⇒ 只有细环带在动、中心无持续翻滚 =「没水感」）；
        3. maskImage 语义 final = 原图·(1−m) + 位移图·m，m=环带·(1−phase)⁴+辉光(1−r)⁸·0.35
           （r=到光标距离/最远角距离），**设备全分辨率合成**——m==0 处逐像素等于真原图
           （D：旧版合成在 0.5x 缩采图上做 ⇒ 贴回 2x 上采样 ⇒ 边缘一圈「缩采模糊带」
           与盘外清晰区形成方形分界线；seamcheck border top/left max 133/129、bottom/right 0，
           差异严格跟随内容锐利度即缩采模糊铁证）。
        """
        dev_w = region_img.width()
        dev_h = region_img.height()
        if dev_w < 1 or dev_h < 1:
            return None
        src = region_img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        src_a = np.frombuffer(bytes(src.constBits()), dtype=np.uint8,
                              count=dev_w * dev_h * 4).reshape(dev_h, dev_w, 4)

        # ---- 设备→逻辑换算（img 是 rect 的设备像素快照 ⇒ dev/逻辑 = dpr）----
        dprx = dev_w / max(1e-6, rect_logical.width())
        dpry = dev_h / max(1e-6, rect_logical.height())
        xs_row = np.arange(dev_w, dtype=np.float32)[None, :] # (1,W) 广播用
        ys_col = np.arange(dev_h, dtype=np.float32)[:, None] # (H,1)
        lxs = xs_row / dprx # 设备 px → 逻辑 px
        lys = ys_col / dpry

        # ---- mask（web waveMaskAt：环带 + 中心辉光，r=最远角归一）----
        lx0 = center_logical.x() - rect_logical.x()
        ly0 = center_logical.y() - rect_logical.y()
        dxc = lxs - lx0
        dyc = lys - ly0
        dist = np.sqrt(dxc * dxc + dyc * dyc)
        r = np.clip(dist / max(1.0, Rnorm_logical), 0.0, 1.5)
        m = self._mask_at_v(r, phase, self._cfg["falloff"], int(self._cfg["rings"]))

        # A：边缘渐隐 —— 位移在抓源 bbox 四边 smoothstep 归零（带宽 _EDGE_FADE）。
        # web 的环带相位到模块边缘时 (1-phase)⁴ 已归零、天然无边界；这是等价保险。
        d_edge = np.minimum(np.minimum(lxs, rect_logical.width() - 1.0 - lxs),
                            np.minimum(lys, rect_logical.height() - 1.0 - lys))
        ef = np.clip(d_edge / _EDGE_FADE, 0.0, 1.0)
        ef = ef * ef * (3.0 - 2.0 * ef) # smoothstep
        m = m * ef

        # ---- 噪声（fractalNoise 等价；0.5x 网格生成 → repeat 上采样）----
        ph = self._ph
        fx = 0.008 + 0.004 * math.sin(ph * 0.9) # web console_html.py:6646 同款
        fy = 0.011 + 0.005 * math.cos(ph * 0.7) # web console_html.py:6647 同款
        pw = max(1, int(dev_w * _PROC_SCALE))
        phh = max(1, int(dev_h * _PROC_SCALE))
        plxs = np.arange(pw, dtype=np.float32)[None, :] * (rect_logical.width() / pw)
        plys = np.arange(phh, dtype=np.float32)[:, None] * (rect_logical.height() / phh)
        Rn = self._fractal_noise(plxs, plys, fx, fy, 5) # xChannelSelector=R
        Gn = self._fractal_noise(plxs, plys, fx, fy, 9) # yChannelSelector=G
        Rn = np.repeat(np.repeat(Rn, 2, axis=0), 2, axis=1)[:dev_h, :dev_w]
        Gn = np.repeat(np.repeat(Gn, 2, axis=0), 2, axis=1)[:dev_h, :dev_w]

        # ---- feDisplacementMap 位移（设备全分辨率最近邻 gather）----
        scale_pulse = max(0.5, float(self._cfg["scale"])
                          * (1.0 + 0.38 * math.sin(ph * 1.3))) # web :6649 同款
        sx = np.clip(xs_row + scale_pulse * dprx * (Rn - 0.5),
                     0, dev_w - 1).astype(np.int32)
        sy = np.clip(ys_col + scale_pulse * dpry * (Gn - 0.5),
                     0, dev_h - 1).astype(np.int32)
        out = src_a[sy, sx]

        # B：mask 合成（web `maskImage` 的语义）—— final = 原图*(1-m) + 位移图*m。
        # m==0 处逐像素等于**真原图** ⇒ 任何边界无缝，可见区域只剩径向 mask 圈（圆形观感）。
        a16 = src_a.astype(np.uint16)
        o16 = out.astype(np.uint16)
        mf = (m * 255.0).astype(np.uint16)[..., None]
        final = ((a16 * (255 - mf) + o16 * mf + 127) // 255).astype(np.uint8)

        return QImage(final.tobytes(), dev_w, dev_h, 4 * dev_w,
                      QImage.Format.Format_ARGB32_Premultiplied)

    @staticmethod
    def _fractal_noise(lxs, lys, fx, fy, seed):
        """feTurbulence fractalNoise(numOctaves=2) 的 CPU 等价：平滑 value noise。

        格点随机（RandomState seed 固定 ⇒ 图案逐帧连续呼吸而非闪烁）+ smoothstep
        双线性插值；第二八度频率×2、幅度×0.5（SVG 分形叠加标准）。fx/fy 单位 =
        cycles/逻辑px（= web baseFrequency）。值域 [0,1]、分布中心 0.5——与
        fractalNoise 的 R/G 通道分布同型，displacement 的 (R−0.5) 语义直接对齐。
        旧版（3 八度 sin/cos + 逐帧 min/max 归一化）的病灶：归一化把值压成近均匀
        随机 ⇒ 位移逐点不相干 =「细碎刮痕」，且高频八度权重被放大。
        """

        def octave(fx_, fy_, sd):
            gw = max(2, int(math.ceil(float(lxs.max()) * fx_)) + 2)
            gh = max(2, int(math.ceil(float(lys.max()) * fy_)) + 2)
            lat = np.random.RandomState(sd).rand(gh, gw).astype(np.float32)
            gx = lxs * fx_
            gy = lys * fy_
            x0 = np.clip(gx.astype(np.int32), 0, gw - 2)
            y0 = np.clip(gy.astype(np.int32), 0, gh - 2)
            tx = gx - x0
            ty = gy - y0
            tx = tx * tx * (3.0 - 2.0 * tx) # smoothstep 权重（Perlin 风）
            ty = ty * ty * (3.0 - 2.0 * ty)
            top = lat[y0, x0] * (1.0 - tx) + lat[y0, x0 + 1] * tx
            bot = lat[y0 + 1, x0] * (1.0 - tx) + lat[y0 + 1, x0 + 1] * tx
            return top * (1.0 - ty) + bot * ty

        n = octave(fx, fy, seed) + 0.5 * octave(fx * 2.0, fy * 2.0, seed + 1)
        return (n / 1.5).astype(np.float32)

    # ------------------------------------------------------------ 绘制（空操作，保留签名）

    def paint(self, p, w, h, dpr) -> None:
        """保留签名以满足 Shell.paintEvent 调用契约与 selftest 形态断言。

        真实的「内容扭曲」由挂在 Shell 的 `_WaveLensEffect` 完成（见 _draw_lens），
        本方法不再画白环。空操作不会在 live UI 产生任何叠加图形。
        """
        return

    # ------------------------------------------------------------ 动画循环

    def _tick(self) -> None:
        """推进相位 → 抓源 → 触发重绘（常驻透镜：静止也有微动）。

        抓源（render 透镜 bbox 到 QImage）在这里做——事件循环态，非 paint 期间，
        无重入。overlay.paintEvent 只消费 `_src_img/_src_rect` 做位移+贴回。
        """
        if not self._enabled:
            return
        ph_speed = self._phase_speed()
        self._ph += ph_speed * _DT
        try:
            if self._overlay is not None:
                self._grab_src() # 事件循环态抓最新像素
                self._overlay.update()
            else:
                self._shell.update()
        except Exception: # noqa: BLE001
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
