# -*- coding: utf-8 -*-
"""窗口 Z 序纪律判据（2026-09-17 立，起因＝用户「佬」报「控制台有时候强制锁定在最上面」）。

真因（一次参数笔误，注释与实参相反）：`agent/ui_adapt.py::dismiss_overlays()` 里那句
`SetWindowPos(h, -1, …)` 注释写着"置于下层（HWND_BOTTOM）"，可 **-1 是 HWND_TOPMOST**
（`HWND_BOTTOM` 才是 `1`）⇒ 只要"挡路的窗口"与微信重叠，那个窗口就被**永久钉在最上层**；
挡路的又常常是我们自己的控制台窗口 ⇒ 用户怎么点都压不下去，而且"有时候"（只在重叠时）。

判据守三件（全部离线，不需要微信在跑）：
  A. 那句话必须用 `1`（HWND_BOTTOM），不许再用 `-1`；
  B. **全仓**不许再出现 `SetWindowPos(…, -1, …)` 这种写法（防别处再犯）；  C. 置顶这件事只允许出现在 `ui_adapt` 那处显式 pin（微信用、受 ui.allow_foreground 门控），
     且必须有对应的取消置顶入口。

用法：runtime\\python\\python.exe scripts\\zorder_selftest.py
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [%s]" % detail if detail else ""))


def _read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


SRC = _read(os.path.join(ROOT, "agent", "ui_adapt.py"))

print("── A. dismiss_overlays：必须真的是「置于下层」──")
m = re.search(r"SetWindowPos\(h,\s*(-?\d+),\s*0,\s*0,\s*0,\s*0,\s*0x0001\s*\|\s*0x0002\)", SRC)
ok("那句调用还在（别删掉了事）", bool(m), m.group(0) if m else "没找到")
ok("第二实参＝1（HWND_BOTTOM）", bool(m) and m.group(1) == "1", m.group(1) if m else "")
ok("不再是 -1（HWND_TOPMOST）", bool(m) and m.group(1) != "-1", m.group(1) if m else "")

print("── B. 全仓不许再有 SetWindowPos(…, -1, …) ──")
bad = []
for d, _sub, files in os.walk(os.path.join(ROOT, "agent")):
    for f in files:
        if not f.endswith(".py"):
            continue
        p = os.path.join(d, f)
        for i, line in enumerate(_read(p).splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if re.search(r"SetWindowPos\([^,]+,\s*-1\s*,", line):
                bad.append("%s:%d" % (f, i))
for d in ("scripts", "launcher-src"):
    base = os.path.join(ROOT, d)
    if not os.path.isdir(base):
        continue
    for f in os.listdir(base):
        if not (f.endswith(".py") or f.endswith(".cs")):
            continue
        p = os.path.join(base, f)
        if f == "zorder_selftest.py":                 # 判据文件自己会提到这个写法，别自命中
            continue
        for i, line in enumerate(_read(p).splitlines(), 1):
            if line.lstrip().startswith("//") or line.lstrip().startswith("#") or "re.search" in line:
                continue
            if re.search(r"SetWindowPos\([^,]+,\s*-1\s*,", line):
                bad.append("%s:%d" % (f, i))
ok("没有别的 TOPMOST 误用", not bad, "、".join(bad) or "干净")

print("── C. 置顶只有一处、且有取消入口 ──")
ok("置顶只出现在 ui_adapt（一处）",
   sum(1 for _f in os.listdir(os.path.join(ROOT, "agent"))
       if _f.endswith(".py")
       and re.search(r"SetWindowPos\([^,]+,\s*-1\s*,", _read(os.path.join(ROOT, "agent", _f)))) == 0)
ok("有取消置顶的入口（restore_zorder / NOTOPMOST 至少一个）",
   ("restore_zorder" in SRC) or ("NOTOPMOST" in SRC) or ("restore_zorder" in _read(os.path.join(ROOT, "agent", "wechat.py"))),
   "ui_adapt 或 wechat 里有")
ok("发送后收尾会调取消置顶", "restore_zorder" in _read(os.path.join(ROOT, "agent", "wechat.py")))

print("\n窗口 Z 序判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
