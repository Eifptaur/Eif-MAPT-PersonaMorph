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

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QCursor, QPixmap, QTransform
from PySide6.QtWidgets import QApplication

SPIN_FRAMES = 24          # web SPIN_FRAMES（console_html.py L2912）
SPIN_MS = 22              # web SPIN_MS（24 × 22 ≈ 530ms 一圈）
NOD_MS = 180              # web mousedown → 180ms 换回
HOTSPOT = (8, 8)          # web cursor:url() 8 8
_MAX_CUR = 128            # webui 服务端同款上限（图片已 resize ≤128）

_DEFAULT = "cursor.png"
_DEFAULT_NOD = "cursor-nod.png"
_CUSTOM = "custom-cursor.png"
_CUSTOM_NOD = "custom-cursor-nod.png"


def _read_ui() -> dict:
    """同进程直读 agent.config（Qt 壳与机器人同进程，共用指纹失效的单例）。

    读不到（异常/未启动完整后端）一律回空表 —— 光标按默认开、自定义按没传处理。
    """
    try:
        from agent.config import get_config  # noqa: PLC0415

        return dict(get_config().get("ui") or {})
    except Exception:  # noqa: BLE001
        return {}


class WhaleCursor(QObject):
    """鱼光标管理器：Shell 持有一个实例；配置变化调 refresh_from_config()。"""

    def __init__(self, root: Path, parent: QObject | None = None):
        super().__init__(parent)
        self._root = Path(root)
        self.enabled = False
        self._custom = False
        self._sig: tuple | None = None          # (on, custom) 重建判据
        self._base: QCursor | None = None
        self._nod: QCursor | None = None
        self._frames: list[QCursor] = []
        self._mode = ""                          # "" | "nod" | "spin"
        self._spin_i = 0
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(SPIN_MS)
        self._spin_timer.timeout.connect(self._spin_step)
        self._nod_timer = QTimer(self)
        self._nod_timer.setSingleShot(True)
        self._nod_timer.setInterval(NOD_MS)
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
                    nod = QPixmap(str(self._asset(_DEFAULT_NOD)))   # web setCustom 链
                return base, nod
        return QPixmap(str(self._asset(_DEFAULT))), QPixmap(str(self._asset(_DEFAULT_NOD)))

    def _rebuild(self, custom: bool) -> None:
        base_pm, nod_pm = self._pick_pair(custom)
        self._base = self._to_cursor(base_pm)
        self._nod = self._to_cursor(nod_pm) if self._base else None
        self._frames = self._build_frames(base_pm) if self._base else []
        self._custom = custom

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
        on = ui.get("whale_cursor", True) is not False      # web `!== false` 口径
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

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:  # noqa: N802
        if ev.type() == QEvent.Type.MouseButtonPress and self.enabled:
            btn = ev.button()
            if btn == Qt.MouseButton.MiddleButton and self._spin():
                return False                                 # 只观察不吃事件
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
            app.changeOverrideCursor(cur)                    # 换帧不压栈

    @staticmethod
    def _pop() -> None:
        app = QApplication.instance()
        if app is not None and app.overrideCursor() is not None:
            app.restoreOverrideCursor()

    def _nod(self) -> None:
        if self._nod is None:                                # 歪头帧没备好 → 不动（不闪系统箭头）
            return
        self._push(self._nod)
        self._nod_timer.start()                              # 连点 = 重置 180ms（web clearTimeout 同款）
        self._mode = "nod"

    def _nod_end(self) -> None:
        if self._mode != "nod":
            return
        self._pop()
        self._mode = ""

    def _spin(self) -> bool:
        if not self._frames:                                 # 帧没备好 ⇒ 退点头（web spin() 同款）
            return False
        if self._mode == "nod":
            self._nod_timer.stop()
        self._spin_i = 0
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
    b2, n2 = wc_custom._pick_pair(True)   # 仓库此刻没有 custom-cursor.png ⇒ 回默认
    ck("cursor: 自定义缺失回退默认", not b2.isNull() and not n2.isNull())

    wc.set(True, custom=False)
    from PySide6.QtWidgets import QWidget  # noqa: PLC0415

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
