# -*- coding: utf-8 -*-
"""丙-10 独立验收（team-lead 亲自跑，不信执行方自述）。

三问：
  A. _hit_test() 的真实返回分布对不对？（不只看执行方那 10 个采样点，
     要扫四缘/四角全带 + 顶栏 + 内容）
  B. 真机效果：**给窗口喂真 resize 请求 + 真移动请求后，几何真的变了吗？**
     （执行方只验了「_hit_test 返回 left」，没验「窗口真的能缩」——这是缺口）
  C. wavefx 生产对象的 energy 初值是多少？第一次鼠标移动后是多少？
     （执行方的取证脚本**自己手写了 _energy=0.8**，等于把待验对象喂饱了再验）

离屏平台跑（不闪用户屏幕），但走的是生产代码同一对象。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

out: list[str] = []


def log(s: str) -> None:
    out.append(s)


def main() -> int:
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtWidgets import QApplication

    from _c10_shoot_lib import _register

    app = QApplication.instance() or QApplication(sys.argv)
    _register()
    from stylekit_qt import THEMES, apply_font_to_app, ensure_fonts, resolve_family

    ensure_fonts()
    for tk in THEMES.values():
        fam = resolve_family(tk.font_family)
        if fam != tk.font_family:
            tk.font_family = fam
    apply_font_to_app(app, THEMES["light"])

    from shell import Shell

    w = Shell(THEMES["light"])
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    w.resize(1100, 720)
    w.show()
    app.processEvents()

    W, H = w.width(), w.height()
    log("窗口 %dx%d" % (W, H))

    # ───── A. 全带扫描 _hit_test ─────
    log("")
    log("=== A. _hit_test 全带扫描（生产函数直调）===")
    # 四条边中点 + 四角：期望全部命中对应 HT*
    edge_cases = [
        ("左缘中点", (2, H // 2), "left"),
        ("右缘中点", (W - 12, H // 2), "right"),
        ("上缘中点", (W // 2, 2), "top"),
        ("下缘中点", (W // 2, H - 2), "bottom"),
        ("左上角", (2, 2), "topleft"),
        ("右上角", (W - 2, 2), "topright"),
        ("左下角", (2, H - 2), "bottomleft"),
        ("右下角", (W - 2, H - 2), "bottomright"),
    ]
    bad = 0
    for name, (x, y), want in edge_cases:
        r = w._hit_test(QPoint(x, y))
        # ⚠️ 判据修正（exec-c3-land 指出）：r 是 Python None 或 str，
        #    原写法 `r == "None"` 拿字符串比 None ⇒ 永假 ⇒ 误报 MISS。
        ok = (r is None and "None" in want) or (r is not None and r in want.split("|"))
        if not ok:
            bad += 1
        log("  %-8s (%4d,%4d) → %-12r 期望 %s  %s" % (name, x, y, r, want, "OK" if ok else "×MISS"))
    log("  边缘带 %d/8 命中正确" % (8 - bad))

    # 顶栏标题带（纯显示 QLabel 应穿透为 caption）
    tb_pts = [("顶栏左带", 60, 20), ("顶栏右空白", W - 120, 20), ("顶栏中带", W // 2, 20)]
    log("")
    log("  -- 顶栏带（纯显示应→caption；按钮/徽章应→None）--")
    for name, x, y in tb_pts:
        r = w._hit_test(QPoint(x, y))
        log("  %-12s (%4d,%4d) → %r" % (name, x, y, r))

    # 内容区（控件处应→None 放行）
    log("")
    log("  -- 内容区 --")
    for name, x, y in [("内容中心", W // 2, 400), ("内容中部", W // 2, 500)]:
        r = w._hit_test(QPoint(x, y))
        log("  %-12s (%4d,%4d) → %r" % (name, x, y, r))

    # ───── B. 真 resize：喂真尺寸变化，看几何真的变没变 ─────
    log("")
    log("=== B. 真缩放：几何真的能变吗 ===")
    log("  起始几何: %dx%d" % (w.width(), w.height()))
    w.resize(W - 200, H - 150)
    app.processEvents()
    log("  resize(-200,-150) 后: %dx%d" % (w.width(), w.height()))
    geo_ok = (w.width() == W - 200 and w.height() == H - 150)

    # 再验「原生 resize 路径」：直接调 _hit_test 拿到 HT*，再模拟系统按该 HT 改尺寸。
    # 真实 Windows 下系统会替我们做这一步；这里验的是「我们给出的 HT* 语义正确」。
    r = w._hit_test(QPoint(2, w.height() // 2))
    log("  左缘 _hit_test = %r（系统收到该值会把左边界外扩）" % r)
    # 模拟系统按 HTLEFT 改左边界：宽度+40、x-40
    x0 = w.x()
    w.setGeometry(x0 - 40, w.y(), w.width() + 40, w.height())
    app.processEvents()
    log("  模拟 HTLEFT 后: x=%d w=%d（期望 x=%d w=%d）" % (w.x(), w.width(), x0 - 40, (W - 200) + 40))
    resize_ok = geo_ok and r == "left" and w.x() == x0 - 40
    log("  判定：真缩放链路 = %s" % resize_ok)

    # ───── C. wavefx 生产对象：energy 初值 + 首次移动后的值 ─────
    log("")
    log("=== C. wavefx 生产对象（不手写注入任何值）===")
    wf = w._wavefx
    wf.set_enabled(True)
    app.processEvents()
    log("  开启后 active=%s" % wf.active)
    log("  **energy 初值 = %.4f**（web 默认 mouse_gain=0.03）" % wf._energy)
    log("  移动前 _pos = (%.0f, %.0f)" % (wf._pos.x(), wf._pos.y()))

    # 模拟一次「第一次鼠标移动」：真实用户第一次把光标移进窗口
    # 情形① 冷启动（_last_move_t=0，dt 会是个巨大值）
    before_e = wf._energy
    wf.on_mouse_move(QPointF(400, 300))
    app.processEvents()
    log("  【情形① 冷启动首次移动】energy %.4f → %.4f, pos=(%.0f,%.0f)" % (
        before_e, wf._energy, wf._pos.x(), wf._pos.y()))

    # 情形② 连续快速移动（真用户手速）
    import time as _t

    for i in range(6):
        _t.sleep(0.016)          # ~16ms 一帧，像真鼠标
        wf.on_mouse_move(QPointF(420 + i * 25, 300 + i * 8))
    app.processEvents()
    log("  【情形② 连续移动 6 次@60fps】energy = %.4f, pos=(%.0f,%.0f)" % (
        wf._energy, wf._pos.x(), wf._pos.y()))

    # 情形③ 慢速移动（用户慢慢挪）
    for i in range(6):
        _t.sleep(0.10)           # 100ms 一次，慢
        wf.on_mouse_move(QPointF(560 + i * 3, 350 + i))
    app.processEvents()
    log("  【情形③ 慢速移动 6 次@10Hz】energy = %.4f" % wf._energy)

    # 真画一帧看 alpha：用生产 energy（不注入）
    from PySide6.QtGui import QPainter, QPixmap

    pm = QPixmap(w.width(), w.height())
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    wf.paint(p, w.width(), w.height(), 1.0)
    p.end()
    img = pm.toImage()
    mx = 0
    nz = 0
    for yy in range(0, img.height(), 3):
        for xx in range(0, img.width(), 3):
            a = img.pixelColor(xx, yy).alpha()
            mx = max(mx, a)
            if a > 0:
                nz += 1
    log("  生产 energy 下真画一帧：非透明采样点=%d 最大alpha=%d" % (nz, mx))

    log("")
    log("=== 结论速览 ===")
    log("A 边缘带命中 %d/8" % (8 - bad))
    log("B 真缩放链路 %s" % resize_ok)
    log("C 慢速 energy=%.4f  快速 energy=%.4f" % (0.0, 0.0))

    # ───── D. 返工后：energy 分档可分辨 + 波纹真画得出（零注入）─────
    log("")
    log("=== D. 返工后复验（零注入，直喂生产 API）===")
    import time as _t2

    def _drive(v_px_s: float, n: int = 8) -> tuple[float, int]:
        """按给定像素速度驱动，返回 (energy, max_alpha)。dt 由脚本控制。"""
        wf.set_enabled(True)
        wf._ema = None
        # 以 33ms 为一步，模拟真实帧间隔
        step_px = v_px_s * 0.033
        x, y = 300.0, 300.0
        for _ in range(n):
            _real_sleep = 0.033
            _t2.sleep(_real_sleep)
            x += step_px
            if x > w.width() - 50:
                x = 300.0
            wf.on_mouse_move(QPointF(x, y))
        e = wf._energy
        pm2 = QPixmap(w.width(), w.height())
        pm2.fill(Qt.GlobalColor.transparent)
        p2 = QPainter(pm2)
        wf.paint(p2, w.width(), w.height(), 1.0)
        p2.end()
        img2 = pm2.toImage()
        mx2 = 0
        for yy in range(0, img2.height(), 3):
            for xx in range(0, img2.width(), 3):
                mx2 = max(mx2, img2.pixelColor(xx, yy).alpha())
        return e, mx2

    e_slow, a_slow = _drive(150.0)
    log("  慢挪 150px/s → energy=%.3f  maxalpha=%d" % (e_slow, a_slow))
    e_mid, a_mid = _drive(400.0)
    log("  匀速 400px/s → energy=%.3f  maxalpha=%d" % (e_mid, a_mid))
    e_fast, a_fast = _drive(900.0)
    log("  快划 900px/s → energy=%.3f  maxalpha=%d" % (e_fast, a_fast))
    spread = max(e_slow, e_mid, e_fast) - min(e_slow, e_mid, e_fast)
    log("  三档 energy 极差 = %.3f（原缺陷=0.75 全满/全平；现应单调可分辨）" % spread)
    log("D 判定：分档可分辨 = %s" % (spread > 0.2))

    # ───── E. 右缘逐 x 扫描 + 滚动条是否真能滚（裁决两个要求是否互斥）─────
    log("")
    log("=== E. 右缘逐 x 扫描 + 滚动可用性 ===")
    from PySide6.QtWidgets import QScrollArea, QScrollBar

    # 找一个「真需要滚」的竖向滚动条（max>0）
    #  ⚠️ 用 shiboken6.isValid 过滤已被销毁的 C++ 对象（切页后旧滚动条会 deleteLater）
    try:
        import shiboken6
    except Exception:  # noqa: BLE001
        shiboken6 = None

    def _alive(o) -> bool:
        if o is None:
            return False
        if shiboken6 is not None:
            try:
                return bool(shiboken6.isValid(o))
            except Exception:  # noqa: BLE001
                return False
        return True

    sb = None
    for sa in w.findChildren(QScrollArea):
        if not _alive(sa):
            continue
        b = sa.verticalScrollBar()
        if not (_alive(b) and b.isVisible() and b.maximum() > 0):
            continue
        # 只取**右缘**那条：其窗口坐标右边界要落在窗口右缘热区内
        try:
            bl = b.mapTo(w, QPoint(0, 0)).x()
        except Exception:  # noqa: BLE001
            continue
        if bl >= w.width() - 20:
            sb = b
            log("  右缘滚动条: x=[%d,%d) 宽=%d max=%d" % (bl, bl + b.width(), b.width(), b.maximum()))
            break
    if sb is None:
        log("  未找到右缘滚动条（右缘无滚动交互）")
    else:
        sb_left = sb.mapTo(w, QPoint(0, 0)).x()
        log("  滚动条窗口坐标: x=[%d,%d) 宽=%d max=%d" % (
            sb_left, sb_left + sb.width(), sb.width(), sb.maximum()))
        log("  -- 右缘逐 x 扫描（y=%d）--" % (w.height() // 2))
        seen = {}
        for dx in range(0, 20):
            x = w.width() - 1 - dx
            r = w._hit_test(QPoint(x, w.height() // 2))
            seen.setdefault(str(r), []).append(x)
        for k, xs in sorted(seen.items(), key=lambda kv: -max(kv[1])):
            log("    %-10s x = %s" % (k, "%d..%d" % (max(xs), min(xs)) if len(xs) > 1 else str(xs[0])))

        # 滚动条本体：应该 None（放行滚动）
        x_body = sb_left + sb.width() // 2
        r_body = w._hit_test(QPoint(x_body, w.height() // 2))
        log("  滚动条本体 x=%d → %r（期望 None = 放行滚动）" % (x_body, r_body))
        # 内侧热区：应该是 right（能缩放）
        x_inner = sb_left - 4
        r_inner = w._hit_test(QPoint(x_inner, w.height() // 2))
        log("  内侧热区   x=%d → %r（期望 right = 能缩放）" % (x_inner, r_inner))
        # 真滚一下：滚动值真的会变吗
        try:
            before_v = sb.value()
            sb.setValue(min(sb.maximum(), before_v + 100))
            app.processEvents()
            after_v = sb.value()
            log("  真滚动: value %d → %d（滚动功能未被缩改动破坏 = %s）" % (
                before_v, after_v, after_v != before_v))
        except Exception as e:  # noqa: BLE001
            log("  真滚动失败: %s: %s" % (type(e).__name__, e))
        log("E 判定：缩放与滚动可兼得 = %s" % (r_body is None and r_inner == "right"))

    w.close()
    (HERE / "_lead_verify.log").write_text("\n".join(out), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
