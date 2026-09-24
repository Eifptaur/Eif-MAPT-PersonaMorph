# -*- coding: utf-8 -*-
"""鱼光标全家桶 —— web `WHALE_CURSOR`（agent/console_html.py L2856-2979）的 Qt 复刻。

语义逐条对齐 web 源码（唯一真值）：
  · 开关    `ui.whale_cursor`（默认开，只有显式 false 才关；web `!== false` 口径）
  · 自定义  `ui.cursor_image == 'custom'` 且 `assets/custom-cursor.png` 存在 ⇒ 用自定义；
            否则回默认 `assets/cursor.png`（web setCustom('') 同款回退）
  · 覆盖面  web 是 `html.whale-cursor *{cursor:url() 8 8, auto!important}` ⇒ **全部元素**；
            Qt 侧把光标刷到 `QApplication.allWidgets()` 上（覆盖输入框的 IBeam）
  · 点头    mousedown 换歪头帧、180ms 换回（`/assets/cursor-nod.png`；自定义时优先
            `custom-cursor-nod.png`，没有就退默认歪头帧 —— web setCustom 同款链）
  · 中键    24 帧 × 22ms ≈ 530ms 原地转一圈；帧 = **当前光标图**绕中心旋转
            （15°/帧，canvas 同款），帧没备好就退点头，绝不"按了没反应"
  · 兜底    任一图片加载失败 ⇒ 保持系统光标（web `auto` 兜底），**不许崩**
  · 热点    固定 (8, 8)（web `cursor:url() 8 8`）

Qt 实现注记：点头/旋转用 `QApplication` 覆盖光标栈（set/change/restoreOverrideCursor）
—— 覆盖光标压过每个控件自己的 cursor，正好等价 web 的 `!important`；底图仍刷在
各控件上，特效结束 pop 掉覆盖层即自然回到底图。
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QCursor, QPixmap, QTransform
from PySide6.QtWidgets import QApplication

SPIN_FRAMES = 24 # web SPIN_FRAMES（console_html.py L2912）
SPIN_MS = 22 # web SPIN_MS（24 × 22 ≈ 530ms 一圈）
NOD_MS_DEFAULT = 400
NOD_MS_DEBUG = 1500 # PM_CURSOR_NOD_DEBUG=1 → 真机定因开关（肉眼必见，机制通不通一锤定音）
NOD_MS = NOD_MS_DEFAULT # 兼容旧引用；实际取值走 _nod_ms()
NOD_SCALE = 1.3
HOTSPOT = (8, 8) # web cursor:url() 8 8
_MAX_CUR = 128 # webui 服务端同款上限（图片已 resize ≤128）


def _nod_ms(env: str | None = None) -> int:
    """点头帧时长（毫秒）。

     #11 → K 两步走：
    · 默认 400ms —— 曾从 web 真值 180ms 提到 320ms，用户复验**仍然无感**
      ⇒ 机制取证 + 强调做足双管齐下：
      时长顶满 400ms 区间上限 + 歪头帧放大 1.3x（见 NOD_SCALE），
      让「看得见」不再依赖用户跑 PM_CURSOR_NOD_DEBUG。
    · 环境变量 PM_CURSOR_NOD_DEBUG=1 → 1500ms —— 真机定因开关：
      延长到肉眼必见，用户点一下就能判「机制通还是不通」。
    """
    v = os.environ.get("PM_CURSOR_NOD_DEBUG") if env is None else env
    if str(v or "").strip() == "1":
        return NOD_MS_DEBUG
    return NOD_MS_DEFAULT

_DEFAULT = "cursor.png"
_DEFAULT_NOD = "cursor-nod.png"
_CUSTOM = "custom-cursor.png"
_CUSTOM_NOD = "custom-cursor-nod.png"


def _read_ui() -> dict:
    """同进程直读 agent.config（Qt 壳与机器人同进程，共用指纹失效的单例）。

    读不到（异常/未启动完整后端）一律回空表 —— 光标按默认开、自定义按没传处理。
    """
    try:
        from agent.config import get_config # noqa: PLC0415

        return dict(get_config().get("ui") or {})
    except Exception: # noqa: BLE001
        return {}


class WhaleCursor(QObject):
    """鱼光标管理器：Shell 持有一个实例；配置变化调 refresh_from_config()。"""

    def __init__(self, root: Path, parent: QObject | None = None):
        super().__init__(parent)
        self._root = Path(root)
        self.enabled = False
        self._custom = False
        self._sig: tuple | None = None # (on, custom) 重建判据
        self._base: QCursor | None = None
        self._nod: QCursor | None = None
        self._frames: list[QCursor] = []
        self._mode = "" # "" | "nod" | "spin"
        self._spin_i = 0
        self._last_spin_idx = -1 # spinTo 相位分帧记忆（web lastSpinIdx）
        self.wheel = None # pm_wheel.WheelMode —— Shell 注入
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(SPIN_MS)
        self._spin_timer.timeout.connect(self._spin_step)
        self._nod_timer = QTimer(self)
        self._nod_timer.setSingleShot(True)
        self._nod_timer.setInterval(_nod_ms())
        self._nod_timer.timeout.connect(self._nod_end)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    # ------------------------------------------------------------ 资源加载

    def _asset(self, name: str) -> Path:
        return self._root / "assets" / name

    @staticmethod
    def _to_cursor(pm: QPixmap) -> QCursor | None:
        if pm.isNull():
            return None
        if pm.width() > _MAX_CUR or pm.height() > _MAX_CUR:
            pm = pm.scaled(
                _MAX_CUR, _MAX_CUR,
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            )
        return QCursor(pm, HOTSPOT[0], HOTSPOT[1])

    def _pick_pair(self, custom: bool) -> tuple[QPixmap, QPixmap]:
        """(底图, 歪头帧) 选取，含 web 同款回退链：自定义坏 → 默认；全坏 → 空。"""
        if custom:
            base = QPixmap(str(self._asset(_CUSTOM)))
            if not base.isNull():
                nod = QPixmap(str(self._asset(_CUSTOM_NOD)))
                if nod.isNull():
                    nod = QPixmap(str(self._asset(_DEFAULT_NOD))) # web setCustom 链
                return base, nod
        return QPixmap(str(self._asset(_DEFAULT))), QPixmap(str(self._asset(_DEFAULT_NOD)))

    def _rebuild(self, custom: bool) -> None:
        base_pm, nod_pm = self._pick_pair(custom)
        self._base = self._to_cursor(base_pm)
        self._nod = self._to_nod_cursor(nod_pm) if self._base else None
        self._frames = self._build_frames(base_pm) if self._base else []
        self._custom = custom

    @staticmethod
    def _to_nod_cursor(pm: QPixmap) -> QCursor | None:
        """歪头帧 cursor —— K：放大 NOD_SCALE(1.3x) 强调「点头看得见」，
        hotspot 同步 ×1.3 保持指向不变；超 _MAX_CUR 上限就退回原尺寸（不炸）。"""
        if pm.isNull():
            return None
        w = int(pm.width() * NOD_SCALE)
        h = int(pm.height() * NOD_SCALE)
        if w > _MAX_CUR or h > _MAX_CUR:
            return WhaleCursor._to_cursor(pm)
        big = pm.scaled(w, h,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
        return QCursor(big, int(HOTSPOT[0] * NOD_SCALE), int(HOTSPOT[1] * NOD_SCALE))

    def _build_frames(self, base_pm: QPixmap) -> list[QCursor]:
        """中键旋转帧：当前光标图绕中心转 24 帧（web buildFrames canvas 同款）。"""
        if base_pm.isNull():
            return []
        w, h = base_pm.width(), base_pm.height()
        out: list[QCursor] = []
        for i in range(SPIN_FRAMES):
            tr = QTransform()
            tr.translate(w / 2.0, h / 2.0)
            tr.rotate(i * 360.0 / SPIN_FRAMES)
            tr.translate(-w / 2.0, -h / 2.0)
            pm = base_pm.transformed(tr, Qt.TransformationMode.SmoothTransformation)
            cur = self._to_cursor(pm)
            if cur is not None:
                out.append(cur)
        return out

    # ------------------------------------------------------------ 开关与刷新

    def refresh_from_config(self) -> None:
        """按 config 现值刷新（Shell 的 4s 探活定时器顺带轮询；web 面板改了即跟）。"""
        ui = _read_ui()
        on = ui.get("whale_cursor", True) is not False # web `!== false` 口径
        custom = ui.get("cursor_image") == "custom" and self._asset(_CUSTOM).exists()
        if (on, custom) != self._sig:
            self._sig = (on, custom)
            self.set(on, custom)

    def set(self, on: bool, custom: bool | None = None) -> None:
        if custom is None:
            custom = self._custom
        self.enabled = bool(on)
        self._cancel_fx()
        if self.enabled:
            if self._base is None or custom != self._custom:
                self._rebuild(custom)
            self._apply_all()
        else:
            self._clear_all()

    def reapply(self) -> None:
        """主题切换整窗重建后控件全是新的 —— 把底图重新刷一遍。"""
        if self.enabled and self._base is not None:
            self._apply_all()

    def _apply_all(self) -> None:
        cur = self._base
        if cur is None:
            return
        for w in QApplication.allWidgets():
            w.setCursor(cur)

    @staticmethod
    def _clear_all() -> None:
        for w in QApplication.allWidgets():
            w.unsetCursor()

    # ------------------------------------------------------------ 点头 / 中键旋转

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool: # noqa: N802
        if not self.enabled:
            return False
        t = ev.type()
        w = self.wheel # pm_wheel.WheelMode
        # ── 滚轮模式的"活水"事件：移动/滚轮/键盘/失焦全程喂给 WheelMode ──
        if t == QEvent.Type.MouseMove and w is not None:
            try:
                w.on_move(int(ev.globalPosition().y()))
            except Exception: # noqa: BLE001
                pass
        elif t == QEvent.Type.Wheel and w is not None:
            if w.on_wheel():
                return False # 只观察不吃事件（web passive wheel）
        elif t == QEvent.Type.KeyPress and w is not None:
            if getattr(ev, "key", lambda: 0)() == Qt.Key.Key_Escape and w.on_esc():
                return False
        elif t == QEvent.Type.ApplicationDeactivate and w is not None:
            w.on_blur() # web window blur = 退出滚轮模式
        elif t == QEvent.Type.MouseButtonPress:
            btn = ev.button()
            if btn == Qt.MouseButton.MiddleButton:
                if w is not None:
                    # （web 侧两个 listener 并存有打架嫌疑，Qt 取干净语义）。
                    w.toggle(int(ev.globalPosition().x()), int(ev.globalPosition().y()))
                    return False # 只观察不吃事件
                if self._spin():
                    return False
            elif w is not None and w.on:
                # web mousedown 非 1 键 = 退出滚轮模式（本键自己的行为继续——左键接着点头）
                w.stop()
            if btn == Qt.MouseButton.LeftButton and self._mode != "spin":
                self._nod()
        return False

    def _push(self, cur: QCursor) -> None:
        app = QApplication.instance()
        if app is None:
            return
        if app.overrideCursor() is None:
            app.setOverrideCursor(cur)
        else:
            app.changeOverrideCursor(cur) # 换帧不压栈

    @staticmethod
    def _pop() -> None:
        app = QApplication.instance()
        if app is not None and app.overrideCursor() is not None:
            app.restoreOverrideCursor()

    def _nod(self) -> None:
        if self._nod is None: # 歪头帧没备好 → 不动（不闪系统箭头）
            return
        self._push(self._nod)
        self._nod_timer.start() # 连点 = 重置 180ms（web clearTimeout 同款）
        self._mode = "nod"

    def _nod_end(self) -> None:
        if self._mode != "nod":
            return
        self._pop()
        self._mode = ""

    def stop_transient(self) -> None:
        """停掉点头/转圈这类瞬时特效（滚轮模式进场前清场用，公开给 pm_wheel）。"""
        self._nod_timer.stop()
        if self._mode == "nod":
            self._pop()
            self._mode = ""

    def spin_to(self, deg: float) -> None:
        """相位驱动换帧（web spinTo 同款，console_html.py L2950-2956）：
        帧号变了才换，帧没备好/光标关了不动。滚轮模式专用路径，不动 _mode。"""
        if not self._frames:
            return
        n = len(self._frames)
        idx = int((((deg % 360.0) + 360.0) % 360.0) // (360.0 / n)) % n
        if idx == self._last_spin_idx:
            return
        self._last_spin_idx = idx
        self._push(self._frames[idx])

    def restore(self) -> None:
        """光标回底图（web restore 同款）—— 滚轮模式退出时调；覆盖栈 pop 即回控件底图。"""
        self._last_spin_idx = -1
        if self._mode:
            self._pop()
            self._mode = ""
        elif QApplication.instance() is not None and QApplication.overrideCursor() is not None:
            self._pop()

    def _spin(self) -> bool:
        if not self._frames: # 帧没备好 ⇒ 退点头（web spin() 同款）
            return False
        if self._mode == "nod":
            self._nod_timer.stop()
        self._spin_i = 0
        self._last_spin_idx = -1 # 相位记忆复位（滚轮模式共用帧表）
        self._push(self._frames[0])
        self._spin_timer.start()
        self._mode = "spin"
        return True

    def _spin_step(self) -> None:
        self._spin_i += 1
        if self._spin_i >= len(self._frames):
            self._spin_timer.stop()
            self._pop()
            self._mode = ""
            return
        self._push(self._frames[self._spin_i])

    def _cancel_fx(self) -> None:
        self._nod_timer.stop()
        self._spin_timer.stop()
        w = self.wheel
        if w is not None and getattr(w, "on", False):
            w.stop() # 光标关了 ⇒ 滚轮模式一并退（干净语义）
        if self._mode:
            self._pop()
        self._mode = ""


def _selftest() -> list[tuple[str, bool, str]]:
    """模块自检：默认对存在、24 帧旋转、自定义缺失回退、开关复位。"""
    out: list[tuple[str, bool, str]] = []

    def ck(name: str, cond: bool, extra: str = "") -> None:
        out.append((name, bool(cond), extra))

    root = Path(__file__).resolve().parents[1]
    wc = WhaleCursor(root)

    base_pm, nod_pm = wc._pick_pair(False)
    ck("cursor: 默认底图存在可读", not base_pm.isNull(), str(wc._asset(_DEFAULT)))
    ck("cursor: 默认歪头帧存在可读", not nod_pm.isNull())
    cur = wc._to_cursor(base_pm)
    ck("cursor: 热点 (8,8)", cur is not None and cur.hotSpot() is not None)

    frames = wc._build_frames(base_pm)
    ck("cursor: 旋转帧 24 帧", len(frames) == SPIN_FRAMES, str(len(frames)))

    wc_custom = WhaleCursor(root)
    b2, n2 = wc_custom._pick_pair(True) # 仓库此刻没有 custom-cursor.png ⇒ 回默认
    ck("cursor: 自定义缺失回退默认", not b2.isNull() and not n2.isNull())

    wc.set(True, custom=False)
    from PySide6.QtWidgets import QWidget # noqa: PLC0415

    dummy = QWidget()
    wc._apply_all()
    got = dummy.cursor()
    ck("cursor: 开启后控件刷上底图（位图光标）",
       got.shape() == Qt.CursorShape.BitmapCursor and got.pixmap().width() > 0,
       f"shape={got.shape()}")
    wc.set(False, custom=False)
    ck("cursor: 关闭后控件回到系统光标", dummy.cursor().shape() != Qt.CursorShape.BitmapCursor)
    wc_custom.set(False)
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
