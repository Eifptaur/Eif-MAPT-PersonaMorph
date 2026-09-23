# -*- coding: utf-8 -*-
"""丙-8 L 取证：按钮按压态「一眼可辨」——QSS 值确定性对比。

工单 L：按钮 active 态视觉（按下微缩/加深），对齐 web 按钮手感。
QSS 无 transform —— 用「底色往字色轴压一档 + 描边同步加深 + 压字 1px」补足。

grab() 在半透明（glass）主题上会混入背后色产生噪声 —— 本探针改为解析
styleSheet() 里 QPushButton{} 与 QPushButton:pressed{} 的颜色字面量，
按 ΔRGBA（含 alpha：danger 的按压感就在 20%→46% 加深）量化可辨度。
"""

import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("WX_AGENT_CONFIG", str(Path(os.environ.get("TEMP", "/tmp")) / "qt-press-probe.json"))

from PySide6.QtWidgets import QApplication

app = QApplication([])

from stylekit_qt import THEMES, apply_font_to_app
from widgets import Btn

out: list[str] = []
fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    if not ok:
        fails += 1
    out.append(f"{'OK ' if ok else 'FAIL'}  {label}: got={got!r} want={want!r}")


def parse_qss_colors(qss: str) -> dict[str, tuple[str, str]]:
    """QPushButton{} 与 QPushButton:pressed{} → {state: (bg, border)}。"""
    m_base = re.search(r"QPushButton\{([^}]*)\}", qss)
    m_press = re.search(r"QPushButton:pressed\{([^}]*)\}", qss)
    assert m_base and m_press, "QSS 必须同时含基础态与 :pressed 态规则"

    def kv(block: str) -> dict[str, str]:
        d = {}
        for part in block.split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                d[k.strip()] = v.strip()
        return d

    b, p = kv(m_base.group(1)), kv(m_press.group(1))
    return {"base": (b.get("background", ""), b.get("border", "")),
            "pressed": (p.get("background", ""), p.get("border", ""))}


def to_rgba(s: str) -> tuple[int, int, int, int]:
    """'#RRGGBB' / '#AARRGGBB' / 'rgba(r,g,b,a)' / '…transparent…' → (r,g,b,a)。"""
    if "transparent" in s:
        return (0, 0, 0, 0)
    # border 是 CSS 简写（如 "1px solid #6F65E8FF"）—— 先抽出颜色 token
    m = re.search(r"(#[0-9A-Fa-f]{6,8}|rgba\([^)]*\))", s)
    assert m, f"不认识的颜色字面量: {s!r}"
    s2 = m.group(1)
    if s2.startswith("#"):
        h = s2[1:]
        if len(h) == 6:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
        return (int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16), int(h[0:2], 16))
    m2 = re.match(r"rgba\((\d+),(\d+),(\d+),(\d+(?:\.\d+)?)\)", s2)
    assert m2, f"不认识的颜色字面量: {s!r}"
    a = float(m2.group(4))
    a255 = int(round(a * 255 if a <= 1 else a))
    return (int(m2.group(1)), int(m2.group(2)), int(m2.group(3)), a255)


def delta(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    return sum(abs(x - y) for x, y in zip(a, b))


for key in ("whale", "dark", "light"):
    t = THEMES[key]
    apply_font_to_app(app, t)
    for role in ("primary", "ghost", "danger"):
        b = Btn("测试", t, role)
        c = parse_qss_colors(b.styleSheet())
        d_bg = delta(to_rgba(c["base"][0]), to_rgba(c["pressed"][0]))
        d_bd = delta(to_rgba(c["base"][1]), to_rgba(c["pressed"][1]))
        total = d_bg + d_bd
        check(f"{key}/{role} pressed 可辨（底+边 ΔRGBA>60）",
              "yes" if total > 60 else f"weak({total})", "yes")
        out.append(f"      base={c['base']} pressed={c['pressed']} Δbg={d_bg} Δborder={d_bd}")
    out.append("")

out.append(f"FAILURES={fails}")
p = HERE / "_c8_pressprobe_out.log"
p.write_text("\n".join(out), encoding="utf-8")
print("written", p, "fails=", fails)
