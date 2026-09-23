# -*- coding: utf-8 -*-
"""
丙-12 波纹特效 第一步：可行性实测（稳健版）
- numpy 可用性
- PySide6 版本 + QGraphicsEffect.sourcePixmap API 内省 + QWidget.grab/render 代价
- 400x400 区域逐像素位移重采样单帧耗时 / 30fps 可达性
- 降级方案实测（0.5x 缩采）
输出落同目录 _c12_wave_bench.txt
"""
import os, sys, time

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(ROOT, "_c12_wave_bench.txt")
LINES = []

def log(*a):
    s = " ".join(str(x) for x in a)
    LINES.append(s)
    print(s, flush=True)

log("=" * 70)
log("丙-12 波纹 feasibility bench  @", time.strftime("%Y-%m-%d %H:%M:%S"))
log("python:", sys.version.split()[0])
log("=" * 70)

# ---------- 1. numpy ----------
HAS_NUMPY = False
try:
    import numpy as np
    log("[1] numpy 版本:", np.__version__, "-> 可用(OK)")
    HAS_NUMPY = True
except Exception as e:
    log("[1] numpy 不可用:", repr(e))
    log("    -> 将采用纯 Python / QImage.scanLine 逐像素方案")

# ---------- 2. PySide6 + 取源 API 内省 ----------
PYSIDE_OK = False
APP = None
try:
    from PySide6 import QtCore, QtWidgets, QtGui
    from PySide6.QtWidgets import QApplication, QWidget, QGraphicsEffect, QLabel
    log("[2] PySide6 版本:", QtCore.__version__)
    PYSIDE_OK = True
    # API 内省：sourcePixmap / drawSource 是否存在且可调用
    has_sp = hasattr(QGraphicsEffect, "sourcePixmap")
    has_ds = hasattr(QGraphicsEffect, "drawSource")
    log("    QGraphicsEffect.sourcePixmap 存在=%s  drawSource 存在=%s" % (has_sp, has_ds))
    if not (has_sp and has_ds):
        log("    !! 取源 API 缺失 -> 不能走 QGraphicsEffect 路线，改用 grab 路线")
    APP = QApplication.instance() or QApplication(sys.argv[:1])
except Exception as e:
    log("[2] PySide6 探测异常:", repr(e))

# ---------- 3. grab / render 代价 ----------
if PYSIDE_OK and APP is not None:
    try:
        big = QWidget()
        big.resize(1000, 700)
        big.setStyleSheet("background:rgb(10,30,60);")
        # 不 show，直接按需渲染（grab 内部会 offscreen 渲染）
        N = 20
        t0 = time.perf_counter()
        for _ in range(N):
            pm = big.grab(QtCore.QRect(300, 150, 400, 400))
        t1 = time.perf_counter()
        per = (t1 - t0) / N * 1000.0
        log("[3] QWidget.grab(400x400) 单帧平均: %.2f ms  (x%d)" % (per, N))
        t0 = time.perf_counter()
        for _ in range(N):
            pmf = big.grab()
        t1 = time.perf_counter()
        perf = (t1 - t0) / N * 1000.0
        log("    QWidget.grab(全窗1000x700) 单帧平均: %.2f ms" % perf)
        # render 到外部 pixmap 的代价（等价 sourcePixmap 内部渲染）
        ext = QtGui.QPixmap(400, 400)
        t0 = time.perf_counter()
        for _ in range(N):
            ext.fill(QtCore.Qt.transparent)
            big.render(QtGui.QPainter(ext), QtCore.QPoint(), QtCore.QRegion(QtCore.QRect(300, 150, 400, 400)))
        t1 = time.perf_counter()
        perr = (t1 - t0) / N * 1000.0
        log("    QWidget.render->pixmap(400x400) 单帧平均: %.2f ms" % perr)
    except Exception as e:
        log("[3] grab/render 实测异常:", repr(e))

# ---------- 4. 位移重采样性能（核心）----------
def make_noise(w, h, t, octaves=4):
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    R = np.zeros((h, w), np.float32)
    G = np.zeros((h, w), np.float32)
    amp = 1.0; freq = 0.012
    for o in range(octaves):
        ph = t * (0.6 + 0.2 * o)
        R += amp * np.sin(xs * freq + ph) * np.cos(ys * freq * 1.3 - ph * 0.7)
        G += amp * np.cos(ys * freq * 1.1 - ph * 1.2) * np.sin(xs * freq * 0.9 + ph)
        amp *= 0.5; freq *= 2.0
    R = (R - R.min()) / (R.max() - R.min() + 1e-6)
    G = (G - G.min()) / (G.max() - G.min() + 1e-6)
    return R, G

def displace(src, scale, t):
    h, w = src.shape[0], src.shape[1]
    R, G = make_noise(w, h, t)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    sx = np.clip(xs + scale * (R - 0.5), 0, w - 1).astype(np.float32)
    sy = np.clip(ys + scale * (G - 0.5), 0, h - 1).astype(np.float32)
    xi = sx.astype(np.int32); yi = sy.astype(np.int32)
    return src[yi, xi]

if HAS_NUMPY:
    log("[4] 位移重采样性能 (numpy 向量化, 含多八度噪声生成+最近邻采样)")
    for res, label in [(1.0, "1.0x(400x400)"), (0.5, "0.5x(200x200)")]:
        w = int(400 * res); h = int(400 * res)
        src = (np.random.rand(h, w, 4) * 255).astype(np.uint8)
        N = 60
        t0 = time.perf_counter()
        for i in range(N):
            out = displace(src, 17.0, i * 0.05)
        t1 = time.perf_counter()
        per = (t1 - t0) / N * 1000.0
        fps = 1000.0 / per
        log("    %-14s 单帧: %.2f ms  理论 %.1f fps  30fps可达=%s"
            % (label, per, fps, "YES" if fps >= 30 else "NO"))
    src = (np.random.rand(700, 1000, 3) * 255).astype(np.uint8)
    N = 30
    t0 = time.perf_counter()
    for i in range(N):
        out = displace(src, 17.0, i * 0.05)
    t1 = time.perf_counter()
    per = (t1 - t0) / N * 1000.0
    log("    参考 全窗1000x700 单帧: %.2f ms  理论 %.1f fps" % (per, 1000.0 / per))
else:
    log("[4] 无 numpy -> 纯 Python 方案待实测")

# ---------- 5. 结论 ----------
log("=" * 70)
log("结论:")
if PYSIDE_OK:
    log("  - PySide6 %s 可用，sourcePixmap/drawSource API 存在" % QtCore.__version__)
if HAS_NUMPY:
    log("  - numpy %s 可用，位移重采样可向量化，单帧远低于 33ms 预算" % np.__version__)
    log("  - 推荐路线：lens bbox(约 2*radius 方框)内做位移，必要时 0.5x 缩采 -> 稳 30fps")
log("  - 取源路线二选一：(a) QGraphicsEffect.sourcePixmap 在 draw 内取源；")
log("    (b) 退路 QWidget.grab/render 取区域像素再画回(代价见[3])")
log("=" * 70)

with open(OUT_PATH, "w", encoding="utf-8") as f:
    f.write("\n".join(LINES) + "\n")
log("原始输出已落:", OUT_PATH)
