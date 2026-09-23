# -*- coding: utf-8 -*-
"""群相 Qt 正式壳（丙-3 第一步落位）。

由 `_scratch/qt_proto/` 原型复制提升而来：
  · stylekit_qt.py 三主题 token + ensure_fonts 双字体：原样（token 不改，工单钉子）；
  · shell.py / panels_qt.py / panels_custom.py 27 面板：目录层级适配（parents 浅一级）；
  · addr.py / heal.py / agent_bridge.py：探活口径一行未动，只改目录层级；
  · selftest.py / shoot.py：自检与取证随包提升（HERE 适配 ui_qt 层级）。
入口：app.start_qt_shell()（persona_morph.py 主进程内的子线程）。
原型的 98/0 基线仍钉在 _scratch/qt_proto（不删不改坏）；本目录跑同款 selftest。
"""
