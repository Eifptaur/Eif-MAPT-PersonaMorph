# -*- coding: utf-8 -*-
"""丙-8 J/I 取证：切页/切主题耗时分解 + idle 重绘基线（波浪已砍的 CPU 收益）。

工单 J：切界面卡 3-5 秒——量出真实瓶颈在哪一段（构建/换页/动效）。
工单 I：波浪动效砍掉——量 idle 时每秒重绘事件数（应为 ~0）。
"""

import io
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("WX_AGENT_CONFIG", str(Path(os.environ.get("TEMP", "/tmp")) / "qt-perf-probe.json"))

from PySide6.QtWidgets import QApplication

app = QApplication([])

out = []


def log(s):
    out.append(s)
    print(s)


from stylekit_qt import THEMES
from shell import Shell

# ① 全量构建（含 27 面板）—— 启动/主题切换共用的成本
t0 = time.perf_counter()
w = Shell(THEMES["whale"])
t1 = time.perf_counter()
log(f"[build] Shell 全量构建（27 面板+侧栏+顶栏）: {(t1 - t0) * 1000:.0f} ms")

w.show()
app.processEvents()

# ② 页面切换（_go：setCurrentIndex + 淡入调度）
t0 = time.perf_counter()
w._go("media", "媒体")
app.processEvents()
t1 = time.perf_counter()
log(f"[goto ] 点导航换页（含 processEvents 冲一次布局）: {(t1 - t0) * 1000:.0f} ms")

t0 = time.perf_counter()
w._go("bot", "机器人")
app.processEvents()
t1 = time.perf_counter()
log(f"[goto ] 换回主面板: {(t1 - t0) * 1000:.0f} ms")

# ③ 主题切换（_rebuild 整窗重建）—— 用户「卡三五秒」的主嫌疑
t0 = time.perf_counter()
w._switch_theme("dark", persist=False)
app.processEvents()
t1 = time.perf_counter()
log(f"[theme] whale→dark 整窗重建: {(t1 - t0) * 1000:.0f} ms")

t0 = time.perf_counter()
w._switch_theme("whale", persist=False)
app.processEvents()
t1 = time.perf_counter()
log(f"[theme] dark→whale 整窗重建: {(t1 - t0) * 1000:.0f} ms")

# ④ idle 重绘基线：1.2 秒内 Paint 事件计数（波浪已砍 ⇒ 应≈2 次以内）
from PySide6.QtCore import QEvent, QObject


class _PaintCounter(QObject):
    def __init__(self):
        super().__init__()
        self.n = 0

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Paint and obj is w:
            self.n += 1
        return False


c = _PaintCounter()
app.installEventFilter(c)
c.n = 0
loop_t0 = time.perf_counter()
while time.perf_counter() - loop_t0 < 1.2:
    app.processEvents()
    time.sleep(0.01)
log(f"[idle ] 1.2s 内 Shell Paint 事件: {c.n} 次（波浪砍掉前 ≈36 次/1.2s = 30fps）")
app.removeEventFilter(c)

w.close()
Path(HERE / "_c8_perfprobe_out.log").write_text("\n".join(out), encoding="utf-8")
print("DONE")
