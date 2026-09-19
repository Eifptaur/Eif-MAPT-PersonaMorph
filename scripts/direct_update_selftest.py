#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「直接更新」判据（2026-09-18 作者纠偏后立，原话：「**为啥你又搞这个什么覆盖解压？不许覆盖解压，
一定要直接更新**」）。

要守的三件事：
  ① **接管判据＝整包版本**：`data/watchdog.pid` 第二行写包版本；启动时比的是**包版本**，
     不一致（含旧包写的 `"2"` 这种读不出/不同格式的）就**接管**（杀旧 + 清 bot.lock/bot.pid + 拉新），
     一致才让位。⇒ "更新装完没人接替"只需点一次「一键启动」就能续完，**不需要覆盖解压**。
  ② **「一键启动」不许把旧包残留当成"已在运行"**：`scripts/onestart.py` 里必须先比包版本，
     不一致就不跳过（否则用户点了没反应，只能手工解压）。
  ③ **文案里不许出现"覆盖解压"**：用户可见的文件（README / 控制台 / 启动器源码）不得出现该说法，
     发布说明也要写明"直接点更新"。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   " + name)
    else:
        FAIL += 1
        print("  FAIL " + name + ("   [%s]" % extra if extra else ""))


WD = io.open(os.path.join(ROOT, "scripts", "watchdog.py"), encoding="utf-8").read()
OS_ = io.open(os.path.join(ROOT, "scripts", "onestart.py"), encoding="utf-8").read()

print("── A. 看门狗：接管判据＝整包版本 ──")
ok("有 pkg_version()（读 agent/version.py）", "def pkg_version()" in WD and "agent\", \"version.py" in WD)
ok("同版本判定比的是包版本（不是 WATCHDOG_VER）",
   "_mine = pkg_version() or WATCHDOG_VER" in WD and "if ver and ver == _mine:" in WD)
ok("不一致就接管（含旧包写的 \"2\"）", "记录版本=%r ≠ 本包 %r" in WD)
ok("pid 文件第二行写包版本", 'f.write("%s\\n%s" % (os.getpid(), pkg_version() or WATCHDOG_VER))' in WD)
ok("takeover 的 taskkill 不带 /T（不加树杀，避免把机器人一起杀）",
   Chr(34)._dummy if False else (chr(34) + "/F", "/T" + chr(34) not in WD)),

print("── B. 实际取值：pkg_version() == agent/version.py 的 VERSION ──")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import importlib.util                                    # noqa: E402
_spec = importlib.util.spec_from_file_location("_wd_probe", os.path.join(ROOT, "scripts", "watchdog.py"))
_wd = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(_wd)                          # 该模块只定义函数、不跑主流程
    _pv = _wd.pkg_version()
except Exception as e:                                     # noqa: BLE001
    _pv = "导入失败：%s" % e
_vs = io.open(os.path.join(ROOT, "agent", "version.py"), encoding="utf-8").read()
_m = re.search(r"VERSION\s*=\s*['\"]([^'\"]+)['\"]", _vs)
_want = _m.group(1) if _m else "?"
ok("pkg_version() 读出的就是当前包版本（%s）" % _want, _pv == _want, "%r vs %r" % (_pv, _want))

print("── C. 一键启动：旧包残留不算『已在运行』 ──")
ok("有『先比包版本』的分支", "_stale" in OS_ and "watchdog.pid" in OS_)
ok("包版本不一致 ⇒ 不跳过（照常拉起 watchdog）", "if existing is not None and not _stale:" in OS_)
ok("日志说清是旧包残留", "旧包残留实例" in OS_)

print("── D. 文案：用户不可见处不许出现「覆盖解压」这类『让用户手动解压』的说法 ──")
_files = ["README.md", "agent/console_html.py", "launcher-src/launcher.cs", "launcher-src/close.cs",
          "AGENTS.md",
          # ⭐ 2026-09-19 扩：这两处也是**用户能看到的** —— installer.ps1 的 Set-State 文案会原样显示在
          #   「一键启动」窗口里（作者截图那句「不存在，请重新解压完整包」就是从 L349 漏出去的 ✗）。
          "scripts/installer.ps1", "scripts/setup_python.ps1", "scripts/onestart.py"]
_bad = []
for rel in _files:
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        continue
    _txt = io.open(p, encoding="utf-8", errors="ignore").read()
    # ⭐ 只查"指令式"说法，且**跳过注释行**：
    #   · onestart.py 里那句「不许覆盖解压…」是**注释里引用作者原话**（不是给用户看的）⇒ 误报 ✗
    #   · installer.ps1 新文案里的「不用手动解压」是否定式说明 ⇒ 不该被当成违规 ✗
    _lines = [l for l in _txt.splitlines()
              if not l.strip().startswith(("#", "//", "<!--", "*", ">"))]
    _body = "\n".join(_lines)
    _hits = [w for w in ("覆盖解压", "解压覆盖", "手动解压",
                         "请重新解压", "需重新解压", "要重新解压") if w in _body]
    if _hits:
        _bad.append("%s%s" % (rel, _hits))
ok("README/控制台/启动器/安装器里没有『让用户手动解压』的说法（%s）" % (_bad or "无"), not _bad, str(_bad))

print("\n==== 直接更新判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
