# -*- coding: utf-8 -*-
"""窗口 Z 序纪律判据。

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

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
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
        if f == "zorder_selftest.py": # 判据文件自己会提到这个写法，别自命中
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

print("\n── D. 关窗纪律（全仓）：`WM_CLOSE` 只许出自「关窗唯一入口」及其两处已声明例外 ──")
# 口径 = `agent/winops.py` 模块头写的那两条事故教训：**关微信的窗**一律走 `winops.close()`
# （它内部先过 `ok_to_close()`：当场重取主窗/渲染子窗，命中就拒绝）。
# 两处**已声明**的例外（关的**不是**微信的窗，理由写在各自模块头）：
#   · `tray.py`      —— 我们自己的托盘窗；
#   · `ui_adapt.py`  —— 系统输入叠加层（TouchKeyboard 一族，收窄手段＝类名 + 标题白名单）。
# 另有 `winops.py` 自己就是那个入口（`PostMessageW(h, WM_CLOSE, …)` 就在它里面）。
# ⚠️ **已知边界**：`_scratch/` 不纳入本闸门 —— 那里放着**产品文件的历史快照**
#   （`wechat.after-flip.py` 那类整份拷贝），扫进来会出一堆"快照命中"的假红，
#   而快照不是活代码。真要有探针在 `_scratch/` 里关窗，得靠人看（**如实记边界，不假装覆盖**）。
import glob as _glob

_ALLOW_CLOSE = {"winops.py", "tray.py", "ui_adapt.py"}
_RX_CLOSE = re.compile(r"PostMessageW?\s*\([^,]*,\s*(?:WM_CLOSE|0x0010)\s*,")


def _scan_raw_close(src: str, fname: str):
    """返回这份源码里"裸投 `WM_CLOSE`"的行号（注释不算；白名单文件名不算）。"""
    out = []
    if fname in _ALLOW_CLOSE:
        return out
    for i, line in enumerate(src.splitlines(), 1):
        if _RX_CLOSE.search(line.split("#")[0]):
            out.append(i)
    return out


_PATS = ("agent/*.py", "scripts/*.py", "launcher-src/*.py", "launcher-src/*.cs")
_raw_hits, _n_scanned = [], 0
for _pat in _PATS:
    for _p in sorted(_glob.glob(os.path.join(ROOT, _pat))):
        _f = os.path.basename(_p)
        if _f.endswith("_selftest.py") or _f == "zorder_selftest.py":
            continue  # 判据自己会把这个写法写进合成样例里，别自命中
        _n_scanned += 1
        for _ln in _scan_raw_close(_read(_p), _f):
            _raw_hits.append("%s:%d" % (_f, _ln))
ok("分母守卫：真的扫到了产品源码与打包装侧（否则下面那条测的是空气）",
   _n_scanned >= 250, "%d 个文件" % _n_scanned)
ok("⛔ 除 `winops.py`/`tray.py`/`ui_adapt.py` 外没有裸投 `WM_CLOSE`（关微信窗只许走 winops.close）",
   not _raw_hits, "、".join(_raw_hits[:6]) or "干净")
ok("反向对照：合成一段「裸投 `WM_CLOSE`」⇒ 同一个检测必须报出来",
   _scan_raw_close("u.PostMessageW(h, WM_CLOSE, 0, 0)\n", "wechat.py") == [1],
   str(_scan_raw_close("u.PostMessageW(h, WM_CLOSE, 0, 0)\n", "wechat.py")))
ok("反向对照的阴性侧：白名单内的文件用同样写法**不报**（证明白名单按文件生效，不是恒空）",
   _scan_raw_close("u.PostMessageW(h, WM_CLOSE, 0, 0)\n", "tray.py") == [])
_WINOPS = _read(os.path.join(ROOT, "agent", "winops.py"))
ok("⭐ 「唯一入口」里**真的装着那道闸**（`close()` 先过 `ok_to_close()` 才投消息）",
   bool(re.search(r"def close\(", _WINOPS))
   and _WINOPS.find("ok_to_close(h, why)") > 0
   and _WINOPS.find("ok_to_close(h, why)") < _WINOPS.find("PostMessageW(ctypes.c_void_p(h), WM_CLOSE"))

print("\n窗口 Z 序判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
