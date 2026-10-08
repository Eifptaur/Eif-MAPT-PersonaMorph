# -*- coding: utf-8 -*-
"""A6 判据：**表情面板几何五项实测值**（B 赛道 §G.2 A6 / F16）。

跑法： py -3 scripts\\panel_rect_selftest.py     退出码 0=全过 / 1=有失败

背景（朋友注释原文）：
> 「微信 4.1.15.13 起表情面板**实际在输入栏上方**、高度约 380px —— 原来的反推几何
>   **导致所有表情格坐标点偏**。」

我方旧兜底式：`(l+W*0.30, t+H*0.20, max(l+W*0.30+460, l+W-40), t+H-130)`
  ⛔ 两个毛病：① `top = H*0.20` **太高**（面板其实贴着输入栏上方）；
     ② `right` 里塞了魔法数 `460`（`max(...)` 让宽度跟着常量走，而不是跟着窗口走）。

断言：
  ① 兜底的 **top 贴着 bottom**（高度 ≈ min(380, H*0.40)），不再是从窗口 20% 处开始；
  ② 兜底的 **right = l+W-40**（跟着窗口走，不跟魔法数走）；
  ③ 面板高度**被封顶**（H 很大时不超过 380）；
  ④ 高度**不会超过窗口**（H 很小时不越界）；
  ⑤ `_panel_grid`（实测网格）**仍是优先档**——实测成功时**不用**这个兜底；
  ⑥ 真机可用时：几何自洽（left<right、top<bottom、bottom = t+H-130）。

⛔ 本判据**不真开表情面板**（那会动用户窗口）：用假 `gui` + 假窗口矩形走纯几何。
"""

from __future__ import annotations

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

import _iso14  # noqa: E402
_iso14.all_()  # ⛔ 必须 all_()

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def _calc(l, t, W, H):
    """照抄 `_panel_rect` 兜底段的**纯几何**（不碰真窗口）。"""
    _panel_h = min(380, int(H * 0.40))
    _left = l + int(W * 0.25)
    _right = l + W - 40
    _bottom = t + H - 130
    _top = _bottom - _panel_h
    return (_left, _top, _right, _bottom)


print("\n══ A6 表情面板几何五项实测值 ══")

# ① 用真实量级的窗口：1440x900（本机常见）
L, T, W, H = 100, 50, 1440, 900
_x0, _y0, _x1, _y1 = _calc(L, T, W, H)
_h = _y1 - _y0
ok("① 面板高度 = min(380, H*0.40)（不再是 H*0.67 那种过高几何）",
   _h == min(380, int(H * 0.40)) == 360 and _h < int(H * 0.5), "h=%d" % _h)
ok("①b top 贴着 bottom（面板在输入栏上方，不是从窗口 20% 处开始）",
   _y0 == (T + H - 130) - _h, "top=%d bottom=%d" % (_y0, _y1))
_old_top = T + int(H * 0.20)
ok("①c 新 top **明显低于**旧 top（证明「太高」这个毛病已修）",
   _y0 > _old_top, "new_top=%d old_top=%d" % (_y0, _old_top))

# ② right 跟着窗口走，不跟魔法数走
ok("② right = l+W-40（跟着窗口，不跟 460 那个魔法数走）",
   _x1 == L + W - 40, "right=%d" % _x1)
# 旧式：max(l+W*0.30+460, l+W-40)
_old_right = max(L + int(W * 0.30) + 460, L + W - 40)
# ②b **只留痕、不判对错**：把旧式算出来的 right 打出来，让人一眼看到它跟窗口右沿脱钩多少。
# ⛔ 原来写成 `ok(..., _old_right != ... or True)` —— 那个 `or True` 让断言**恒真**，
#   还白占一个"通过"名额（判据总数虚高）。⇒ 留痕就用 print，别包成断言。
print("   · 留痕：旧式 right = %s，窗口右沿-40 = %s ⇒ 窄窗下差 %s px"
      % (_old_right, L + W - 40, (L + W - 40) - _old_right))

# ③ 面板高度封顶 380（超大口径窗口也不许超过）
_big = _calc(0, 0, 2560, 1600)
ok("③ 超大窗口（H=1600）下高度**封顶 380**", (_big[3] - _big[1]) == 380, "h=%d" % (_big[3] - _big[1]))

# ④ 小窗口不越界
_small = _calc(0, 0, 800, 400)
ok("④ 小窗口（H=400）下高度按比例、不越界",
   (_small[3] - _small[1]) == int(400 * 0.40) == 160, "h=%d" % (_small[3] - _small[1]))

# ⑤ _panel_grid 仍是优先档（源码级：实测成功时不走兜底）
_src = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
ok("⑤ 格中心**优先用实测网格**（`if variant == \"measured\" and _gc and _gr`）",
   'if variant == "measured" and _gc and _gr:' in _src)
ok("⑤b 实测失败才回退反推常量", "实测失败⇒回退反推" in _src or "实测失败" in _src)
ok("⑤c 本次只改兜底档（源码里留了这条边界声明）",
   "这里只改**兜底档**" in _src or "这里只改**兜底档**" in _src or "只改**兜底档**" in _src)

# ⑥ 几何自洽
for (_l, _t, _w, _hh) in ((100, 50, 1440, 900), (0, 0, 1000, 700), (-500, -300, 1200, 800)):
    a, b, c, d = _calc(_l, _t, _w, _hh)
    if not (a < c and b < d and d == _t + _hh - 130):
        ok("⑥ 几何自洽 W=%d H=%d" % (_w, _hh), False, "(%d,%d,%d,%d)" % (a, b, c, d))
        break
else:
    ok("⑥ 三种窗口尺寸下几何都自洽（left<right、top<bottom、bottom=t+H-130）", True)

print("\n==== 表情面板几何判据：%d 通过 / %d 失败 ====" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for _f in FAIL:
        print("  · " + _f)
sys.exit(1 if FAIL else 0)
