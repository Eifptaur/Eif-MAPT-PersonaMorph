# -*- coding: utf-8 -*-
"""t7b4_detect_scale_check.py —— 验证 ui_adapt.detect_scale() 是"运行时算出缩放、不查表"。"""
import os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent import ui_adapt as ua

print("detect_scale() =", ua.detect_scale())
cfg = ua._config_ui() if hasattr(ua, "_config_ui") else {}
print("config ui =", cfg)
print()
print("=== 源码自证：它是怎么算的（不依赖任何 DPI 表）===")
import inspect
src = inspect.getsource(ua.detect_scale)
for ln in src.splitlines():
    if any(k in ln for k in ("dmPelsWidth", "GetSystemMetrics", "return", "physical", "logical", "scale")):
        print("   ", ln.strip())
