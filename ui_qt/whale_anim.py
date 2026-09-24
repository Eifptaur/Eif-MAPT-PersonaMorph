# -*- coding: utf-8 -*-
"""鲸鱼徽章动效 —— web 鲸鱼闭包（agent/console_html.py 4907-5214 只读真值）的 Qt 直译。

拖动/返回全由"浮层"承担（挂 Shell 窗口层、窗口局部坐标跟随），本体留在顶栏原槽只做变淡/恢复；
气泡与鲸图同画布分层（气泡先画 ⇒ z 恒在下）；km 系数解析只写在本文件 km_for() 一处。
"""
from __future__ import annotations

import math
import random
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

# ── 真值常量（web 闭包 / docs 第七章；三态时长 = clamp(rate×dist/100×km, lo, hi)）──
TICK_MS, FLY_SIZE, FLY_CANVAS = 16, 44, 96 # 逐帧节拍；浮层鲸 44×44；画布 96 防纸飞机旋转裁边
HERO_OPACITY = 0.12 # 按下后本体变淡（web: hero.opacity='0.12'）
WORM_RATE, PLANE_RATE, ZAP_RATE = 260.0, 170.0, 90.0
DUR_LIMITS = ((600, 2400), (420, 1500), (200, 720)) # 蠕动/纸飞机/扎地 (lo, hi)
MORPH_MS, UNMORPH_MS, HOLE_MS, EMIT_MS = 360, 220, 480, 300
POP_MS, POP_LIFE_MS, BUBBLE_COUNT = 140, 700, 7 # 入框超弹 / 浮层存活（180ms 后淡出）
KM_MIN, KM_MAX = 0.3, 3.0
MODE_KEYS = ("worm", "plane", "zap")
JELLY_POSES = ((0.62, 1.22), (1.24, 0.84), (0.58, 1.26), (1.14, 0.90), (0.90, 1.08), (1.04, 0.98), (1.0, 1.0)) # 7 帧 (sy,sx)
JELLY_FRAMES = len(JELLY_POSES)
JELLY_TOTAL_MS = sum(80 + i * 30 for i in range(JELLY_FRAMES - 1)) # 帧隔 80+seq*30 = 930ms
JELLY_KEYFRAMES = tuple(sum(80 + j * 30 for j in range(i)) / JELLY_TOTAL_MS for i in range(JELLY_FRAMES))


def clamp_km(v) -> float: # 解析不动（None/非数字/NaN）一律回 1.0，再夹 0.3–3
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 1.0
    return 1.0 if f != f else max(KM_MIN, min(KM_MAX, f)) # f != f 即 NaN


def km_for(key: str) -> float:
    """ui.whale_anim.<key> —— **系数解析只写这一处**（web getPath('ui.whale_anim')）。
    刻意不走 load_config()：它会对真实 config.json 触发一次性迁移（写盘）—— 对 agent/ 只读，只 import 常量。"""
    v = None
    try:
        import json, sys # noqa: PLC0415
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from agent.config import CONFIG_FILE, DEFAULT_CONFIG # noqa: PLC0415
        v = ((DEFAULT_CONFIG.get("ui") or {}).get("whale_anim") or {}).get(key)
        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
            over = ((json.load(f).get("ui") or {}).get("whale_anim") or {})
        v = over.get(key, v)
    except Exception: # noqa: BLE001
        v = None
    return clamp_km(v)


def duration_ms(mode: int, dist: float, km: float = 1.0) -> int: # 三态时长（docs 7.3 / web L5050-5052）
    lo, hi = DUR_LIMITS[mode]
    v = (WORM_RATE, PLANE_RATE, ZAP_RATE)[mode] * max(0.0, float(dist)) / 100.0 * clamp_km(km)
    return int(round(min(hi, max(lo, v))))


def quad_bez(a: float, m: float, b: float, t: float) -> float: # 二次贝塞尔（web quadBez）
    u = 1 - t
    return u * u * a + 2 * u * t * m + t * t * b


class FlyOverlay(QLabel):
    """游离鲸鱼浮层（web .whale-fly）—— 透明无框 QLabel：气泡先画（z 下）、鲸图/纸飞机在上。"""

    def __init__(self, pm: QPixmap, parent: QWidget, ink: str = "#6FCFFF"):
        super().__init__(parent)
        self._pm, self._ink = pm, QColor(ink)
        self._sx, self._sy, self._op, self._rot = 1.0, 1.0, 1.0, 0.0
        self._whale_op, self._plane_op, self._pitch = 1.0, 0.0, 0.0
        self._wsx, self._wsy = 1.0, 1.0 # 鲸图局部缩放（扎地冲刺纵向压扁用）
        self._bubs, self._bt0 = (), 0.0 # 入框气泡 (ang, r0)×7，随浮层同生命周期
        self.setFixedSize(FLY_CANVAS, FLY_CANVAS)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background:transparent;border:none;") # 显式透明底：防 QSS 继承砸花

    def set_xform(self, sx, sy, rot_deg=0.0, op=1.0) -> None:
        self._sx, self._sy, self._rot, self._op = sx, sy, rot_deg, max(0.0, min(1.0, op))
        self.update()

    def set_layers(self, whale_op, plane_op, pitch_deg=0.0, whale_sx=1.0, whale_sy=1.0) -> None:
        self._whale_op, self._plane_op, self._pitch = whale_op, plane_op, pitch_deg
        self._wsx, self._wsy = whale_sx, whale_sy
        self.update()

    def set_bubbles(self, bubs) -> None: # ang/r0 由种子化 rng 生成；700ms 后随浮层移除
        self._bubs, self._bt0 = tuple(bubs), time.monotonic()

    def paintEvent(self, _e): # noqa: N802
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        p.translate(FLY_CANVAS / 2, FLY_CANVAS / 2)
        p.rotate(self._rot)
        p.scale(self._sx, self._sy)
        if self._bubs: # 气泡层（web spawnBubbles：散开上浮消散）
            age = (time.monotonic() - self._bt0) * 1000.0
            p.setBrush(self._ink)
            p.setPen(Qt.PenStyle.NoPen)
            for ang, r0 in self._bubs:
                k = min(1.0, age / 550.0) # web：550ms 过渡
                if 0.9 * (1 - k) > 0:
                    p.setOpacity(0.9 * (1 - k))
                    p.drawEllipse(QPointF(math.cos(ang) * (r0 + 34 * k), math.sin(ang) * (r0 + 34 * k) - 10 * k),
                                  2.1 * (1 + 0.6 * k), 2.1 * (1 + 0.6 * k))
        p.setOpacity(self._op)
        if self._whale_op > 0 and not self._pm.isNull():
            p.save(); p.setOpacity(self._op * self._whale_op); p.scale(self._wsx, self._wsy)
            p.drawPixmap(-FLY_SIZE // 2, -FLY_SIZE // 2, FLY_SIZE, FLY_SIZE, self._pm)
            p.restore()
        if self._plane_op > 0:
            p.save(); p.rotate(self._pitch); p.setOpacity(self._op * self._plane_op)
            self._paint_plane(p)
            p.restore()

    @staticmethod
    def _paint_plane(p: QPainter) -> None: # 纸飞机 —— web 同一条 SVG path（console_html.py 5108）逐点直画
        p.scale(58 / 64, 58 / 64)
        p.translate(-32, -20)
        for pts, fill, alpha, stroke in (
            ([(2, 20), (62, 2), (38, 24), (34, 38)], "#F8FAFF", 1.0, "#9FC2DE"),
            ([(2, 20), (62, 2), (34, 28)], "#E8F0FF", 0.85, None),
            ([(34, 38), (38, 24), (34, 28)], "#D8E4F8", 0.90, None),
        ):
            path = QPainterPath(QPointF(*pts[0]))
            for x, y in pts[1:]:
                path.lineTo(x, y)
            path.closeSubpath()
            c = QColor(fill); c.setAlphaF(alpha) # 底色全不透明 ⇒ alpha 直乘等价
            p.fillPath(path, c)
            p.setPen(QPen(QColor(stroke), 1.4) if stroke else Qt.PenStyle.NoPen)
            p.drawPath(path)


class WhaleAnimator(QObject):
    """拖拽 + 三态返回状态机（web 闭包 4907-5214 直译）。坐标全为窗口局部系。"""

    def __init__(self, badge, seed=None, bubble_color="#6FCFFF"):
        super().__init__(badge)
        self.badge, self.ink = badge, bubble_color
        self.rng = random.Random(seed) # 种子注入 → mode/抖动/气泡全可复现
        self.phase = None # drag/worm/p_morph/p_fly/p_un/dash/hole/emit/pop
        self.mode, self.fly, self.dur = -1, None, 0
        self.vx = self.vy = self.wig = 0.0
        self._tm = QTimer(self); self._tm.setInterval(TICK_MS); self._tm.timeout.connect(self._tick)

    @property
    def active(self) -> bool: return self.phase is not None

    def _slot_center(self) -> QPointF:
        try:
            return QPointF(self.badge.mapTo(self.badge.window(), self.badge.rect().center()))
        except RuntimeError: return QPointF() # 徽章已被主题重建 deleteLater

    def _clamp(self, p: QPointF) -> QPointF: # 拖动限窗口内
        w, half = self.badge.window(), FLY_CANVAS / 2
        return QPointF(min(max(p.x(), half), w.width() - half), min(max(p.y(), half), w.height() - half))

    def _place_fly(self) -> None:
        self.fly.move(round(self.cur.x()) - FLY_CANVAS // 2, round(self.cur.y()) - FLY_CANVAS // 2)

    def begin_drag(self, pos, pm) -> None:
        if self.phase is not None:
            return
        self.cur, self.vx, self.vy, self.wig = self._clamp(QPointF(pos)), 0.0, 0.0, 0.0
        self.t_press, self.phase = time.monotonic(), "drag"
        if self.fly is None: # 浮层挂窗口层；气泡同画布 ⇒ z 恒在鲸图下
            self.fly = FlyOverlay(pm, self.badge.window(), self.ink)
        self.fly.set_layers(1.0, 0.0)
        self.fly.set_xform(1.0, 1.0, 0.0, 1.0)
        self._place_fly()
        self.fly.show(); self.fly.raise_()

    def move_drag(self, pos) -> None:
        if self.phase != "drag": return
        pos = self._clamp(QPointF(pos))
        self.vx, self.vy = pos.x() - self.cur.x(), pos.y() - self.cur.y()
        self.cur, self.wig = QPointF(pos), self.wig + 0.45
        self._apply_drag()

    def tick_drag(self) -> None: # QTimer 驱动的「挣扎」：光标不动也缓缓摆（幅度仍在衰减）
        if self.phase == "drag":
            self.wig += 0.2; self._apply_drag()

    def _apply_drag(self) -> None:
        amp = max(0.08, 0.4 - (time.monotonic() - self.t_press) * 0.12) # web: amp=max(.08,.4-held*.12)
        wig = math.sin(self.wig) * amp
        sx, sy = 1 + min(abs(self.vx), 60) / 200, 1 + min(abs(self.vy), 40) / 200 # 速度拉伸
        ang = math.atan2(self.vy, self.vx) if (self.vx or self.vy) else 0.0
        self.fly.set_xform(sx * (1 + wig * 0.12), sy, math.degrees(ang * 0.22 + wig * 0.35), 1.0)
        self._place_fly()

    def cancel_drag(self) -> None: # web pointercancel：本体立即恢复、浮层移除
        if self.phase == "drag":
            self.phase = None; self._tm.stop(); self._drop_fly(); self.badge.on_drag_cancelled()

    def pick_mode(self) -> int:
        return self.rng.randrange(3) # 等概率三选一（种子化 → 可复现）

    def end_drag(self, mode=None) -> int: # 松手：选 mode 并按距离算时长（km 只在 km_for 一处取）
        if self.phase != "drag": return self.mode
        self.mode = self.pick_mode() if mode is None else int(mode)
        self.start, self.c = QPointF(self.cur), self._slot_center()
        self.dur = duration_ms(self.mode, math.hypot(self.start.x() - self.c.x(), self.start.y() - self.c.y()),
                               km_for(MODE_KEYS[self.mode]))
        self.mid = QPointF(self.c.x() + (self.start.x() - self.c.x()) * 0.15 + (self.rng.random() * 40 - 20),
                           self.c.y() - 80 - self.rng.random() * 40) # 蠕动中段控制点随机上挑
        self.t0, self.phase = time.monotonic(), ("worm", "p_morph", "dash")[self.mode]
        self._tm.start()
        return self.mode

    def _drop_fly(self) -> None:
        if self.fly is not None:
            try: self.fly.deleteLater()
            except RuntimeError: pass
            self.fly = None

    def _alive(self) -> bool: # False = 徽章已随主题重建被删，收摊绝不崩
        try:
            self.badge.window(); return True
        except RuntimeError:
            self._tm.stop(); self.phase = None; self._drop_fly(); return False

    def _tick(self) -> None: # 三态返回状态机（公式逐条对齐 web 闭包）
        if self.phase is None or not self._alive():
            return
        et, ph, fl = (time.monotonic() - self.t0) * 1000.0, self.phase, self.fly
        if ph == "worm": # A：贝塞尔弧+波浪摆动+临近缩身钻入
            t = min(1.0, et / self.dur); e = t * t * (3 - 2 * t); w6 = math.sin(e * math.pi * 6)
            sc = 1.0 if t < 0.75 else max(0.05, 1 - (t - 0.75) * 2.2)
            fl.set_xform(sc * (1 - w6 * 0.08), sc * (1 + w6 * 0.16),
                         math.degrees(math.sin(e * math.pi * 6 + 1.2) * 0.3), 1.0)
            self.cur = QPointF(quad_bez(self.start.x(), self.mid.x(), self.c.x(), e),
                               quad_bez(self.start.y(), self.mid.y(), self.c.y(), e))
            self._place_fly()
            if t >= 1.0: self._begin_pop()
        elif ph == "p_morph": # B1：鲸鱼淡出→纸飞机淡入（360ms）
            m = min(1.0, et / MORPH_MS); e = m * m * (3 - 2 * m)
            fl.set_layers(1 - e, e)
            fl.set_xform(1 - e * 0.25, 1 - e * 0.25, math.degrees(e * 0.9), 1.0)
            self._place_fly()
            if m >= 1.0:
                fl.set_layers(0.0, 1.0); self.phase, self.t0 = "p_fly", time.monotonic()
        elif ph == "p_fly": # B2：抛物线滑翔（上冲→滑降）
            ft = min(1.0, et / self.dur)
            fall = ((ft - 0.35) / 0.65) ** 1.6 if ft >= 0.35 else 0.0
            pitch = -0.5 + (math.sin(ft / 0.35 * math.pi) * 0.2 if ft < 0.35 else fall * 0.35)
            sc = 1.0 if ft <= 0.85 else max(0.1, 1 - (ft - 0.85) * 3)
            fl.set_xform(sc, sc, 0.0, 1.0); fl.set_layers(0.0, 1.0, math.degrees(pitch))
            sxm = self.start.x() + (self.c.x() - self.start.x()) * 0.35
            self.cur = QPointF(quad_bez(self.start.x(), sxm, self.c.x(), ft),
                               quad_bez(self.start.y(), self.start.y() - 110, self.c.y() + 18, ft))
            self._place_fly()
            if ft >= 1.0: self.phase, self.t0 = "p_un", time.monotonic()
        elif ph == "p_un": # B3：纸飞机淡出→鲸鱼淡入（220ms）
            m = min(1.0, et / UNMORPH_MS); e = m * m * (3 - 2 * m)
            fl.set_layers(m, 1 - e)
            self._place_fly()
            if m >= 1.0: self._begin_pop()
        elif ph == "dash": # C1：冲刺拉伸冲向右下+纵向压扁
            d = min(1.0, et / self.dur); e = 1 - (1 - d) ** 3
            fl.set_layers(1.0, 0.0, 0.0, 1.0, 1 - e * 0.8)
            fl.set_xform(1 - e * 0.75, 1 - e * 0.75, math.degrees(e * 0.6), 1.0)
            self.cur = QPointF(self.start.x() + (self.c.x() + 34 - self.start.x()) * e,
                               self.start.y() + (self.c.y() - 40 - self.start.y()) * e)
            self._place_fly()
            if d >= 1.0:
                fl.set_xform(1.0, 1.0, 0.0, 0.0); self.phase, self.t0 = "hole", time.monotonic()
        elif ph == "hole": # C2：原地压扁成点（消失 480ms）
            if et >= HOLE_MS:
                fl.set_layers(1.0, 0.0); self.cur = QPointF(self.c); self._place_fly()
                self.phase, self.t0 = "emit", time.monotonic()
        elif ph == "emit": # C3：从槽中心喷出（超弹出，300ms）
            e3 = min(1.0, et / EMIT_MS); pop = 1 - (1 - e3) ** 3
            sc = max(0.1, 0.2 + pop * 1.1 - math.sin(e3 * math.pi) * 0.15)
            fl.set_xform(sc, sc, 0.0, 1.0)
            self._place_fly()
            if e3 >= 1.0: self._begin_pop()
        elif ph == "pop": # 入框：140ms 超弹 scale→1.06，180ms 后淡出
            t = min(1.0, et / POP_MS)
            back = max(0.0, 1 + 2.70158 * (t - 1) ** 3 + 1.70158 * (t - 1) ** 2)
            op = 1.0 if et < 180 else max(0.0, 1 - (et - 180) / 200)
            fl.set_xform(1.06 * back, 1.06 * back, 0.0, op)
            if et >= POP_LIFE_MS:
                self.phase = None; self._tm.stop(); self._drop_fly()

    def _begin_pop(self) -> None: # finishReturn：本体恢复+过冲回弹+气泡
        self.phase, self.t0 = "pop", time.monotonic()
        self.cur = QPointF(self.c); self._place_fly()
        self.fly.set_layers(1.0, 0.0)
        self.fly.set_xform(0.0, 0.0, 0.0, 1.0)
        self.fly.set_bubbles((self.rng.random() * math.pi * 2, 6 + self.rng.random() * 10)
                             for _ in range(BUBBLE_COUNT))
        self.badge.on_return_pop()
