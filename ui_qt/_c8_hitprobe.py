# -*- coding: utf-8 -*-
"""丙-8 P0-B 取证：WM_NCHITTEST 的 childAt 命中现场。

真机症状：窗口不能拖动、不能放大缩小。取证方式 = 离屏建 Shell，18 个采样点
**直接调用 Shell._hit_test()**（nativeEvent 的判定核心，生产代码同一逻辑，
不是复刻——丙-8 教训：脚本里复刻一套只会原地踏步）。打印每个采样点的
childAt 命中对象 → 最终 HT 结论，修前/修后各跑一次作对照。
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault(
    "WX_AGENT_CONFIG",
    str(Path(os.environ.get("TEMP", "/tmp")) / "qt-c8-probe-config.json"),
)

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from stylekit_qt import THEMES, apply_font_to_app, ensure_fonts, resolve_family  # noqa: E402

ensure_fonts()
t = THEMES["whale"]
fam = resolve_family(t.font_family)
if fam != t.font_family:
    for tk in THEMES.values():
        tk.font_family = fam
apply_font_to_app(app, t)

from shell import Shell  # noqa: E402

w = Shell(t)
w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
w.resize(1120, 720)
w.show()
app.processEvents()

W, H = w.width(), w.height()

out = []

HTNAMES = {"client": "HTCLIENT", "caption": "HTCAPTION(可拖)",
           "left": "HTLEFT", "right": "HTRIGHT",
           "top": "HTTOP", "topleft": "HTTOPLEFT", "topright": "HTTOPRIGHT",
           "bottom": "HTBOTTOM", "bottomleft": "HTBOTTOMLEFT",
           "bottomright": "HTBOTTOMRIGHT"}


def sample(label: str, x: int, y: int) -> None:
    hit = w.childAt(QPoint(x, y))
    chain = []
    cur = hit
    while cur is not None and cur is not w:
        chain.append(cur.objectName() or type(cur).__name__)
        cur = cur.parentWidget()
    r = w._hit_test(QPoint(x, y))   # 生产代码同一逻辑：None=放行 HTCLIENT
    verdict = "HTCLIENT(放行控件)" if r is None else HTNAMES[r]
    out.append(
        f"{label:26s} hit={'None' if hit is None else type(hit).__name__}"
        f"[{'>'.join(chain[:5])}] -> {verdict}"
    )


# ① 顶栏空白（y=30 在 60px 顶栏中部）
for x in (160, 240, 320, 420, 520, 620, 720, 780, 900, 960):
    sample(f"顶栏空白({x},30)", x, 30)
# ①b 顶栏可交互控件（应放行 HTCLIENT，不被穿透吞掉）
sample("顶栏鲸鱼徽章(40,30)", 40, 30)      # WhaleBadge(14,4,52x52)
sample("顶栏状态徽章(745,30)", 745, 30)    # Badge(721,19,60x22) 纯显示→应 HTCAPTION
sample("顶栏标题文字(110,30)", 110, 30)    # 名称 QLabel(76,4) 纯显示→应 HTCAPTION
# ② 四边中部（缩放主战场）
sample("左边中(4,360)", 4, 360)
sample("右边中(W-4,360)", W - 4, 360)
sample("上边中(560,4)", 560, 4)
sample("下边中(560,H-4)", 560, H - 4)
# ③ 四角
sample("右上角(W-4,4)", W - 4, 4)
sample("右下角(W-4,H-4)", W - 4, H - 4)
sample("左下角(4,H-4)", 4, H - 4)
sample("左上角(4,4)", 4, 4)

out.append("")
out.append(f"窗口尺寸 {W}x{H}")
bar = getattr(w, "titlebar", None)
out.append("titlebar 子控件几何：")
if bar is not None:
    for c in bar.findChildren(QWidget):
        if c.parent() is not bar and c.parent() is not None:
            continue  # 只列 bar 的直接子控件
        g = c.geometry()
        out.append(f"  {type(c).__name__:16s} name={c.objectName()!r:12s} "
                   f"geo=({g.x()},{g.y()},{g.width()}x{g.height()}) hidden={c.isHidden()}")

p = HERE / "_c8_hitprobe_out.log"
p.write_text("\n".join(out), encoding="utf-8")
print("written", p)
