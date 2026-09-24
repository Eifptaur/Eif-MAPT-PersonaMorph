# -*- coding: utf-8 -*-
"""丙-31：波纹「一点动静都没有」终裁探针——真跑完整链路 + 像素差异实锤。

丙-29 探针的 `displace_differs` 恒为 n/a、只验挂载不验视觉——这次直接对答案：
① 开波纹 vs 关波纹 的整窗截图**像素差异**（波纹贡献了扭曲）
② 波纹开启时两个不同相位的截图**像素差异**（环带在动）
③ 全链路体检：sync_overlay 存在性 / overlay 几何 / _hit_deep 命中 / _grab_src 产物 /
   paintEvent 真被调用的次数。

必须用产品运行时跑（硬纪律，见 docs/裁决-丙11-自检环境纪律.md）：
    runtime/python/python.exe ui_qt/_c31_waveprobe2.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import numpy as np  # noqa: E402
from PySide6.QtCore import QPoint, QPointF, QRect  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from stylekit_qt import THEMES, apply_font_to_app, resolve_family  # noqa: E402

OUT_SHOTS = os.path.join(HERE, "shots")
RES = {}
_PE_STATS = {"n": 0}


def _grab_img(w):
    return w.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)


def _diff_px(a, b):
    """两图等尺寸前提下：任一通道差 >2 的像素数（None=尺寸不一致没法比）。"""
    if a.size() != b.size():
        return None
    aa = np.frombuffer(bytes(a.constBits()), dtype=np.uint8)
    bb = np.frombuffer(bytes(b.constBits()), dtype=np.uint8)
    if aa.size != bb.size or aa.size == 0:
        return None
    d = np.abs(aa.astype(np.int16) - bb.astype(np.int16))
    d4 = d.reshape(-1, 4)
    return int(np.count_nonzero(d4.max(axis=1) > 2))


def _tick_n(w, app, n):
    for _ in range(n):
        w._wavefx._tick()
        app.processEvents()


def main():
    import wavefx as wfx  # noqa: PLC0415
    from shell import Shell  # noqa: PLC0415

    app = QApplication.instance() or QApplication(sys.argv)
    t = THEMES["whale"]
    fam = resolve_family(t.font_family)
    if fam != t.font_family:
        for tk in THEMES.values():
            tk.font_family = fam
    apply_font_to_app(app, t)
    w = Shell(t)
    w.setAttribute(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    w.resize(1120, 720)
    w.show()

    # paintEvent 计数钩子（原方法前后包一层）
    _orig_pe = wfx.WaveOverlay.paintEvent

    def _pe(self, ev):
        _PE_STATS["n"] += 1
        return _orig_pe(self, ev)

    wfx.WaveOverlay.paintEvent = _pe

    RES["sync_overlay_exists"] = hasattr(wfx.WaveFX, "sync_overlay")
    RES["win"] = [w.width(), w.height()]
    RES["cfg"] = {k: w._wavefx._cfg.get(k) for k in
                  ("enabled", "scale", "speed", "max_gain", "radius", "ring_speed")}

    app.processEvents()

    def stage():
        try:
            # ---- 关波纹基线
            w._wavefx.set_enabled(False)
            _tick_n(w, app, 3)
            img_off = _grab_img(w)
            img_off.save(os.path.join(OUT_SHOTS, "c31-wave-off.png"))

            # ---- 开波纹
            w._wavefx.set_enabled(True)
            app.processEvents()
            ov = w._wavefx._overlay
            RES["overlay_created"] = ov is not None
            RES["overlay_geom_eq_shell"] = bool(
                ov is not None and ov.geometry() == w.rect())
            RES["overlay_visible"] = bool(ov is not None and ov.isVisible())

            # 光标放到内容区面板上（stack 区域内）
            pt = QPointF(700.0, 360.0)
            w._wavefx.on_mouse_move(pt)
            w._wavefx._pos = QPointF(pt)
            hit = w._wavefx._hit_deep(700, 360)
            RES["hit_deep_panel"] = type(hit).__name__ if hit is not None else None
            RES["hit_deep_not_overlay"] = hit is not w._wavefx._overlay
            RES["lens_rect"] = [w._wavefx._lens_rect(pt).x(), w._wavefx._lens_rect(pt).y(),
                                w._wavefx._lens_rect(pt).width(), w._wavefx._lens_rect(pt).height()]
            RES["blank_mode_panel_pt"] = w._wavefx._blank_mode(pt)

            # 抓源体检
            _tick_n(w, app, 4)
            src = w._wavefx._src_img
            r = w._wavefx._src_rect
            RES["src_img_ok"] = bool(src is not None and not src.isNull())
            RES["src_size"] = None if src is None else [src.width(), src.height()]
            RES["src_dpr"] = None if src is None else src.devicePixelRatioF()
            RES["src_rect"] = [r.x(), r.y(), r.width(), r.height()]

            pe_after_first = _PE_STATS["n"]
            img_on1 = _grab_img(w)
            img_on1.save(os.path.join(OUT_SHOTS, "c31-wave-on1.png"))
            RES["paint_calls_first"] = pe_after_first

            # 相位推进后再抓（环带在动 ⇒ 像素应继续变化）
            _tick_n(w, app, 10)
            img_on2 = _grab_img(w)
            img_on2.save(os.path.join(OUT_SHOTS, "c31-wave-on2.png"))
            RES["paint_calls_total"] = _PE_STATS["n"]

            RES["diff_on_vs_off_full"] = _diff_px(img_on1, img_off)      # 波纹贡献的扭曲
            RES["diff_on2_vs_on1_full"] = _diff_px(img_on2, img_on1)     # 环带随相位移动
            # 透镜 bbox 内的差异（更聚焦）
            # r 是逻辑 QRectF；PNG 来自 grab()，是设备像素 ⇒ 乘 dpr 换算
            dpr = w.devicePixelRatioF()
            lr = QRect(round(r.x() * dpr), round(r.y() * dpr),
                       round(r.width() * dpr), round(r.height() * dpr))
            if lr.isValid():
                RES["diff_on_vs_off_lens"] = _diff_px(img_on1.copy(lr), img_off.copy(lr))
                RES["diff_on2_vs_on1_lens"] = _diff_px(img_on2.copy(lr), img_on1.copy(lr))

            RES["VERDICT"] = "可见" if (
                (RES.get("diff_on_vs_off_lens") or 0) > 500
                and (RES.get("diff_on2_vs_on1_lens") or 0) > 200) else "仍不可见"
        except Exception as e:  # noqa: BLE001
            RES["probe_error"] = repr(e)
        finally:
            with open(os.path.join(HERE, "_c31_wave2.json"), "w", encoding="utf-8") as f:
                json.dump(RES, f, ensure_ascii=False, indent=1)
            print(json.dumps(RES, ensure_ascii=False))
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass
            app.quit()

    from PySide6.QtCore import QTimer  # noqa: PLC0415

    QTimer.singleShot(1200, stage)
    app.exec()


main()
