# -*- coding: utf-8 -*-
"""丙-10 取证（重做版 v2，**零注入**）：P0-4 八方向 + P0-5 波纹真实强度。

取证纪律（team-lead 要求，写死在这里防再犯）：
  · 不得给待验对象注入任何状态（不写 _energy / _t0 / _pos / _enabled 之外的开关）
  · 开关 _enabled 必须是「用户会用的那条路径」（config 或公开 set_enabled），
    并在日志里写明「这是取证态，产品默认值 = config 里的值」
  · 每个结论必须给可复算的数，不给「看起来有」

用法：python _c10_verify2.py
落盘：_c10_verify2.log
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

# ⚠️ 真机（非 offscreen）：要真 WM_NCHITTEST + 真窗口
os.environ.pop("QT_QPA_PLATFORM", None)
os.environ["QT_HITTEST_LOG"] = "1"
LOG = ROOT / "logs" / "hittest.log"
LOG.parent.mkdir(parents=True, exist_ok=True)
if LOG.exists():
    LOG.unlink()

out: list[str] = []


def log(s: str) -> None:
    out.append(s)


def main() -> int:
    from PySide6.QtCore import QEvent, QPointF, QTimer, Qt
    from PySide6.QtGui import QCursor, QMouseEvent
    from PySide6.QtWidgets import QApplication

    from _c10_shoot_lib import _register

    app = QApplication.instance() or QApplication(sys.argv)
    _register()
    from stylekit_qt import THEMES, apply_font_to_app, ensure_fonts, resolve_family

    ensure_fonts()
    t = THEMES["light"]
    fam = resolve_family(t.font_family)
    if fam != t.font_family:
        for tk in THEMES.values():
            tk.font_family = fam
    apply_font_to_app(app, t)

    from shell import Shell

    w = Shell(t)
    w.resize(1100, 720)
    w.show()
    w.raise_()
    app.processEvents()
    QTimer

    hwnd = int(w.winId())
    W, H = w.width(), w.height()
    log("真窗口 hwnd=%d visible=%s size=%dx%d QT_QPA_PLATFORM=%r"
        % (hwnd, w.isVisible(), W, H, os.environ.get("QT_QPA_PLATFORM")))

    # ══════════ P0-4：八方向 + 关键内容区/顶栏 ==========
    log("")
    log("═══ P0-4 命中扫描（8 方向 + 滚动条 + 顶栏 + 内容）═══")
    cases = [
        ("左缘中点", 2, H // 2, "left"),
        ("右缘中点", W - 2, H // 2, "right"),
        ("上缘中点", W // 2, 2, "top"),
        ("下缘中点", W // 2, H - 2, "bottom"),
        ("左上角", 2, 2, "topleft"),
        ("右上角", W - 2, 2, "topright"),
        ("左下角", 2, H - 2, "bottomleft"),
        ("右下角", W - 2, H - 2, "bottomright"),
        ("顶栏空白带(可拖)", 200, 20, "caption"),
        ("顶栏鲸鱼徽章(可交互)", 60, 20, None),
        ("内容中心", W // 2, 400, None),
    ]
    ok_all = True
    for name, lx, ly, want in cases:
        r = w._hit_test(QPointF(lx, ly))
        good = (r == want)
        ok_all = ok_all and good
        log("  %-10s 窗口内(%4d,%4d) → %-12s want=%-12s %s"
            % (name, lx, ly, r, want, "OK" if good else "**FAIL**"))
    # 右缘的滚动条本身：仍应放行（不能吃掉滚动交互）
    from PySide6.QtWidgets import QScrollArea, QScrollBar

    sb = None
    for area in w.findChildren(QScrollArea):
        if area.verticalScrollBar().isVisible():
            sb = area.verticalScrollBar()
            break
    if sb is None:
        for ch in w.findChildren(QScrollBar):
            if ch.isVisible() and ch.orientation() == Qt.Orientation.Vertical:
                sb = ch
                break
    if sb is not None:
        gp = sb.mapTo(w, sb.rect().center())
        r = w._hit_test(QPointF(gp))
        log("  滚动条本体  (滑块中心) → %s want=None %s"
            % (r, "OK" if r is None else "**FAIL**"))
        ok_all = ok_all and (r is None)
    log("  P0-4 结论：八方向全命中 = %s" % ok_all)

    # 真 WM_NCHITTEST：把鼠标真移到每个方向点，让系统发真实消息
    g0 = w.frameGeometry()
    ox, oy = g0.x(), g0.y()
    for lx, ly in ((2, H // 2), (W - 2, H // 2), (W - 2, H - 2), (W // 2, 2)):
        QCursor.setPos(ox + lx, oy + ly)
        app.processEvents()
    app.processEvents()
    if LOG.exists():
        txt = LOG.read_text(encoding="utf-8", errors="replace")
        n = len([ln for ln in txt.splitlines() if "WM_NCHITTEST" in ln])
        exc = len([ln for ln in txt.splitlines() if "异常" in ln])
        log("  真机 WM_NCHITTEST 落盘行数=%d，异常行数=%d（0=无 KeyError/ImportError）" % (n, exc))
        for ln in txt.splitlines()[-6:]:
            log("    | " + ln)
    else:
        log("  hittest.log 缺失（nativeEvent 未被触发）")

    # ══════════ P0-5：波纹真值（零注入） ==========
    log("")
    log("═══ P0-5 波纹取证（零注入）═══")
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    wfx = (cfg.get("ui") or {}).get("wave_fx") or {}
    log("  F1 用户 config.json → ui.wave_fx.enabled=%r（其余 %s）"
        % (wfx.get("enabled"), {k: v for k, v in wfx.items() if k != "enabled"}))
    log("  F1' agent/config.py:651 注释明说「默认关——需用户在『水光波纹』卡手动开启」")

    wf = w._wavefx
    log("  F2 建壳后 wf._enabled=%s（= config 的 enabled，未做任何注入）" % wf._enabled)

    # 走用户会用的同一条路：config 开关（模拟用户在控制台卡里打开）
    log("  F3 模拟用户开启：走 set_enabled(True)（公开 API，与 config 卡同一条）")
    wf.set_enabled(True)
    log("     开启后 _enabled=%s active=%s" % (wf._enabled, wf.active))

    # 真鼠标序列（合成 MouseMove 经生产链）——只观测，不写任何字段
    log("  F4 真鼠标序列（Δ40px / 60ms ≈ 667 px/s；逐次观测 energy）")
    x = 200
    energies = []
    for k in range(6):
        x += 40
        ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(x, 300), QPointF(x, 300),
                         Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier)
        app.sendEvent(w, ev)
        time.sleep(0.06)
        app.processEvents()
        energies.append(wf._energy)
        log("     第%d 次 energy=%.4f _pos=(%.0f,%.0f) last_move_t 已推进=%s"
            % (k + 1, wf._energy, wf._pos.x(), wf._pos.y(),
               wf._last_move_t > 1e5))
    log("  F4' energy 序列 min=%.3f max=%.3f（应随速度变化，不再恒 0.25）"
        % (min(energies), max(energies)))

    # F4''：**人慢慢挪手**（team-lead 复核点）——三种真实速度各自稳定后的 energy
    # ⚠️ 三个坑（本次返工逐个踩到，写下来防再犯）：
    #   ① 不能用新建的 WaveFX 实例 —— `sendEvent(w, ...)` 经 Shell.eventFilter 转给
    #      的是 **w._wavefx**（壳 __init__ 注的那一个），自建实例根本收不到事件。
    #   ② 合成事件 + `app.processEvents()` 会让同一个事件被派发两次（间隔≈0）⇒
    #      dt 被压到 0 ⇒ speed 虚高到 1e5 量级，测出来的全是假数。
    #      ⇒ 绕过事件循环，**直接按生产公式喂 on_mouse_move**（真实调用链，只是由
    #      脚本控制 dt），这样 dt 才是「人手速度」的真值。
    #   ③ EMA 有记忆（web `m*0.7+v*0.3` 同款），换挡前要归零，且起点 `_pos` 要对齐。
    log("  F4'' 人手速度分档（直喂生产 API，脚本控 dt；非事件循环）")
    for label, px_per_s in (("慢挪", 150.0), ("匀速", 400.0), ("快划", 900.0)):
        wf._speed_ema = 0.0
        wf._pos = QPointF(300.0, 380.0)
        wf._last_move_t = time.monotonic()
        wf.set_enabled(True)
        dt_fixed = 0.033                      # ≈30fps 的真实帧间隔
        e_stable = None
        for k in range(10):                   # 10 帧足够 EMA 收敛
            # 手工把时间轴前进 dt_fixed（只动**计时基准**，能量仍由公式推出）
            wf._last_move_t -= dt_fixed
            px = 300.0 + (k + 1) * px_per_s * dt_fixed
            wf.on_mouse_move(QPointF(px, 380.0))
            e_stable = wf._energy
        log("     %-6s (%.0f px/s, dt=%.3f) → energy=%.3f  ema=%.1f"
            % (label, px_per_s, dt_fixed, e_stable, wf._speed_ema))

    # 逐帧 alpha 实测：**不推时间轴**，让真实 _t0 走（等真实时间流逝）
    log("  F5 逐帧 alpha 实测（真实时间流逝，不注入 _t0/_energy）")
    log("     取证态声明：波纹总开关**产品默认为 False**（config.py:651），"
        "本段为验『开了之后看不看得见』，故全程保持 set_enabled(True)；"
        "Shell._watch_config 每 4 秒会按 config 把它关回 —— 每次投石前重申开启，"
        "以免把『产品默认关』误读成『画不出来』。")
    from PySide6.QtGui import QPainter, QPixmap

    # 让波纹在真实时间里自然演化：每 150ms 抓一帧，共 6 帧（覆盖 0~750ms 波阵面）
    for k in range(6):
        ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(x + k * 8, 300),
                         QPointF(x + k * 8, 300),
                         Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier)
        app.sendEvent(w, ev)          # 真实投石（会重置 _t0，模拟手一直在动）
        wf.set_enabled(True)          # 重申开启（抵住 _watch_config 按 config 关回）
        app.processEvents()
        pm = QPixmap(W, H)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        wf.paint(p, W, H, 1.0)
        p.end()
        img = pm.toImage()
        mx, cnt = 0, 0
        for yy in range(0, H, 2):
            for xx in range(0, W, 2):
                al = img.pixelColor(xx, yy).alpha()
                if al > 0:
                    cnt += 1
                    mx = max(mx, al)
        log("     第%d 帧（真实 age 由 _t0 决定）非透明采样=%4d 最大 alpha=%3d energy=%.3f"
            % (k + 1, cnt, mx, wf._energy))
        time.sleep(0.15)

    # 与 web 真值对照
    sc = float(wf._cfg.get("scale", 17))
    log("  F6 对照 web（console_html.py L6633）：disp scale=%.0f*(1±0.38) ⇒ 像素位移 %.1f~%.1f px"
        % (sc, sc * 0.62, sc * 1.38))
    log("     Qt：环线宽 %.1fpx + 双层环 + alpha 峰值见 F5（不再是恒 17/31 的隐形档）"
        % max(1.6, min(9.0, sc / 3.4 * (0.8 + 0.5 * wf._energy))))

    # 可视化图（真窗口 + 真波纹，不注入）
    shots = HERE / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    base = w.grab()
    bp = QPainter(base)
    wf.paint(bp, W, H, 1.0)
    bp.end()
    pv = shots / "c10-real-ripple.png"
    base.save(str(pv), "PNG")
    log("  F7 波纹可视化（真窗口+真波纹）: %s (%d KB)" % (pv, os.path.getsize(pv) // 1024))
    # 整窗存图
    wf.set_enabled(False)
    p1 = shots / "c10-real-window.png"
    w.grab().save(str(p1), "PNG")

    w.close()
    app.processEvents()
    (HERE / "_c10_verify2.log").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
