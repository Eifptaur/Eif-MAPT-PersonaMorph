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
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
import _srcmatch as _sm  # noqa: E402  空白容忍的源码断言（脆断言只许降不许升）

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
   (chr(34) + "/F" + chr(34) in WD) and ("/T" + chr(34) + ", " not in WD),
   "原来这里的条件是**一个元组**（恒真 ⇒ 这条断言从立起来那天起就没验过任何东西）")

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

print("── E. 更新后的接管：**必须真 spawn 才退场**（2026-09-22 修的 P0）──")
# 背景（对标调研顺带实测出来的）：`agent/update_apply.py` 原来**没有 import sys/subprocess/log**，
# 而 `_relaunch_after_update()` 用 `sys.executable` + `subprocess.Popen` ⇒ NameError 被
# `except Exception: pass` 吞掉，随后**照旧 os._exit(0)** ⇒ "更新装好了、没人接替、机器人被杀、
# 控制台无法访问"（作者 2026-09-18 报的正是这个）。⇒ 这里改**行为断言**，并带一条反例锚。
import tempfile                                                # noqa: E402
import types                                                   # noqa: E402
import importlib.util                                          # noqa: E402

_spec_u = importlib.util.spec_from_file_location("_ua_probe", os.path.join(ROOT, "agent", "update_apply.py"))
_ua = importlib.util.module_from_spec(_spec_u)
_spec_u.loader.exec_module(_ua)                                # 只定义函数与常量，不跑主流程

ok("反例锚：模块里**真的有** sys / subprocess / log（缺一个就退化成「静默不退场／静默无人接替」）",
   hasattr(_ua, "sys") and hasattr(_ua, "subprocess") and hasattr(_ua, "log"),
   "sys=%s subprocess=%s log=%s" % (hasattr(_ua, "sys"), hasattr(_ua, "subprocess"), hasattr(_ua, "log")))

_tmpu = tempfile.mkdtemp(prefix="pm-handoff-")
_real_root, _real_popen, _real_exit = _ua.ROOT, _ua.subprocess.Popen, _ua.os._exit
_events = []


class _FakePopen(object):
    def __init__(self, cmd, **kw):
        _events.append(("spawn", list(cmd), dict(kw)))


class _BoomPopen(object):
    def __init__(self, cmd, **kw):
        raise OSError("spawn 失败（判据里故意造的）")


try:
    _ua.ROOT = _tmpu                                        # ⛔ 判据绝不写产品 data/
    _ua.subprocess.Popen = _FakePopen
    _ua.os._exit = lambda code: _events.append(("exit", code))
    _ua._relaunch_after_update("9.9.9")
    _spawn = [e for e in _events if e[0] == "spawn"]
    _exit = [e for e in _events if e[0] == "exit"]
    ok("① 真调到了 subprocess.Popen（不是被 NameError 吞掉）", len(_spawn) == 1, str(len(_spawn)))
    ok("② 命令行＝新看门狗 + --takeover + delay",
       bool(_spawn) and str(_spawn[0][1][1]).endswith("watchdog.py")
       and "--takeover" in _spawn[0][1] and any(str(a).startswith("--delay") for a in _spawn[0][1]),
       str(_spawn[0][1][1:])[:80] if _spawn else "-")
    ok("③ 顺序对：**先 spawn 再退场**（退场在前＝机器人被自己杀掉）",
       [e[0] for e in _events] == ["spawn", "exit"] and _exit and _exit[0][1] == 0,
       str([e[0] for e in _events]))
    ok("④ 交接留痕：写了 data/update_done.flag（带版本号）",
       os.path.exists(os.path.join(_tmpu, "data", "update_done.flag"))
       and "9.9.9" in io.open(os.path.join(_tmpu, "data", "update_done.flag"), encoding="utf-8").read())
    # 失败路径：拉不起来 ⇒ **不许退场**（否则没人接替），并把原因报到作业状态
    _events.clear()
    _ua.subprocess.Popen = _BoomPopen
    _ua._relaunch_after_update("9.9.9")
    _job = _ua.job() or {}
    ok("⑤ 拉不起新看门狗 ⇒ **不退场**（不退化成「杀了自己、没人接替」）",
       not [e for e in _events if e[0] == "exit"], str([e[0] for e in _events]))
    ok("⑥ 失败要看得见：作业状态进 error/handoff 且说清怎么办",
       _job.get("state") == "error" and _job.get("phase") == "handoff" and "重启" in str(_job.get("why") or ""),
       "%s/%s %s" % (_job.get("state"), _job.get("phase"), str(_job.get("why"))[:70]))
finally:
    _ua.ROOT, _ua.subprocess.Popen, _ua.os._exit = _real_root, _real_popen, _real_exit
    import shutil as _sh
    _sh.rmtree(_tmpu, ignore_errors=True)

print("── F. 依赖与运行期数据**不随版本变**：写成契约（2026-09-22 立约）──")
# 来源＝`research\更新机制-增量与实际做法.md` §五 4：以前"包里没有 runtime/"是**巧合**（靠排除规则），
# 不是契约 ⇒ 改一次打包规则就可能覆盖掉用户装好的依赖（用户抱怨"更新完又装一遍"）。
_UA = io.open(os.path.join(ROOT, "agent", "update_apply.py"), encoding="utf-8").read()
_PM = io.open(os.path.join(ROOT, "scripts", "pm_update.py"), encoding="utf-8").read()
_PK = io.open(os.path.join(ROOT, "scripts", "pack_online.py"), encoding="utf-8").read()
def _never_touch_block(txt):
    """取那个文件里**真正的赋值**（注释里也会提到 NEVER_TOUCH，别被带偏；赋值可能跨几行）。"""
    out, on = [], False
    for _l in txt.splitlines():
        if _l.lstrip().startswith("NEVER_TOUCH"):
            on = True
        if on:
            out.append(_l)
            if _l.rstrip().endswith(")"):
                break
    return "\n".join(out)


_UA_NT = _never_touch_block(_UA)
_PM_NT = _never_touch_block(_PM)
ok("整包更新不碰 runtime/（agent/update_apply.py 的 NEVER_TOUCH）",
   '"runtime/"' in _UA_NT, _UA_NT[:90] or "没找到 NEVER_TOUCH 赋值行")
ok("增量更新也不碰 runtime/（scripts/pm_update.py 的 NEVER_TOUCH）",
   '"runtime/"' in _PM_NT, _PM_NT[:90] or "没找到 NEVER_TOUCH 赋值行")
ok("出包时**断言**包里不含 runtime/、data/、config.json（不再靠巧合）",
   _sm.has(_PK, "forbidden = sorted(") and _sm.has(_PK, 'n.startswith(("runtime/", "data/"))')
   and _sm.has(_PK, "forbidden") and _sm.has(_PK, "含不该随包分发的运行时/数据"))

print("\n==== 直接更新判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
