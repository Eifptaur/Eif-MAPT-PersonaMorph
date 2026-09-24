# -*- coding: utf-8 -*-
"""丙-10 取证拍图：vermat 版本页 + P0-1 主题残留复验（离屏 grab，不闪真机屏）。

用法：python _c10_shoot.py
落盘：ui_qt/shots/c10-*.png
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from _c10_shoot_lib import mk_shell, save  # noqa: E402

OUT = HERE / "shots"


def main() -> int:
    made = []

    # ① vermat 版本页（P0-3）：版本与更新卡 + 能力矩阵
    w, t = mk_shell("light")
    w._go("vermat", "版本")
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()
    made.append(("light-vermat", save(w, "c10-light-vermat")))
    w.close()

    # ② P0-1 主题残留复验：whale → light，抓导航顶栏取色
    w, t = mk_shell("whale")
    from PySide6.QtWidgets import QApplication as A

    A.processEvents()
    made.append(("whale-初始", save(w, "c10-P1-whale")))
    w._switch_theme("light", persist=False)
    A.processEvents()
    made.append(("light-切换后", save(w, "c10-P1-light-after")))
    # 取顶栏背景色（正上方 60px 带内采样），证实不再是深蓝玻璃
    img = w.grab().toImage()
    px = [img.pixelColor(x, 30).name() for x in (200, 400, 600, 800)]
    made.append(("顶栏取色(light)", ",".join(px)))
    ck = w._backdrop_on
    made.append(("_backdrop_on", str(ck)))
    w.close()

    for n, info in made:
        print(f"  {n:20s} {info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
