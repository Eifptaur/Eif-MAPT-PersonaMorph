# -*- coding: utf-8 -*-
"""一键关闭（`launcher-src/close.cs` → `一键关闭.exe`）判据。

立案原因（2026-09-16）：用户报「**一键关闭又关不掉一键启动了**」；我第一版改法把匹配放宽成
"命令行里出现我们的目录 / 出现「一键启动」" ⇒ **probe 会把正在跑它的 pwsh、甚至 DSH 的 node
一起列进"会结束"名单**（那不是关不掉，那是误杀）。本判据把这两件事一起钉住。

跑法：py -3 scripts\\close_exe_selftest.py     （退出码 0=全过 / 1=有失败）
"""
import os
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "一键关闭.exe")
SRC = os.path.join(ROOT, "launcher-src", "close.cs")

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   %s%s" % (name, ("  [%s]" % detail) if detail else ""))
    else:
        FAIL += 1
        print("  FAIL %s%s" % (name, ("  [%s]" % detail) if detail else ""))


def _src():
    return open(SRC, encoding="utf-8", errors="replace").read()


print("── A. 源码级：两轮收 · 精确闸门 · 如实汇报 ──")
_s = _src()
ok("先收看门狗/入口、再收本体（两轮）",
   "string[] first" in _s and "string[] second" in _s and _s.count("Sweep(rootLower") >= 2)
ok("第二轮里有关键本体（persona_morph.py / wx_app 旧名 / stop_bot.py）",
   "persona_morph.py" in _s and "wx_agent.py" in _s and "stop_bot.py" in _s)
ok("有「装在我们目录里」的闸门（exeUnderRoot）", "exeUnderRoot" in _s)
ok("系统宿主进程必须**同时**带我们的目录与我们的脚本名（scriptOurs）",
   "scriptOurs" in _s and "installer.ps1" in _s and "一键启动.vbs" in _s)
ok("自己的 WebView2 子进程按宿主名认（--webview-exe-name=一键启动）",
   "--webview-exe-name=一键启动" in _s)
ok("有 --probe（只列不动手）", '"--probe"' in _s and "static string Probe(" in _s)
ok("杀不掉要如实写（⚠ 关不掉）", "关不掉" in _s and "⚠" in _s)
ok("收尾还要复核残留（还有 N 个进程没关掉）", "还有 " in _s and "left.Count" in _s)
ok("结果窗不再只有成功项（notes 会被并进去）", "killed.AddRange(notes)" in _s)

print("── B. 行为：--probe 只列我们自己的进程（不许把别人的 pwsh/node 列进来）──")
ok("一键关闭.exe 在（判据要跑真东西）", os.path.exists(EXE), EXE)
ALLOWED = {"python.exe", "pythonw.exe", "一键启动.exe", "一键关闭.exe",
           "msedgewebview2.exe", "wscript.exe", "cscript.exe", "cmd.exe", "conhost.exe"}
FORBIDDEN = {"node.exe", "powershell.exe", "pwsh.exe", "explorer.exe", "chrome.exe",
             "msedge.exe", "wechat.exe", "weixin.exe", "code.exe"}
_tmp = os.path.join(tempfile.gettempdir(), "pm_close_probe.txt")
try:
    if os.path.exists(_tmp):
        os.remove(_tmp)
except Exception:
    pass
try:
    subprocess.run([EXE, "--probe", _tmp], timeout=60,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
except Exception as e:
    ok("能起 --probe", False, str(e)[:80])
_lines = []
try:
    with open(_tmp, encoding="utf-8", errors="replace") as fh:
        _lines = [ln.strip() for ln in fh if ln.strip()]
except Exception:
    _lines = []
ok("--probe 真的写出了清单（winexe 不接控制台 ⇒ 必须落文件）", bool(_lines), str(len(_lines)))
_names = []
for ln in _lines:
    if ln.startswith("[会结束]"):
        _n = ln.split("]", 1)[1].strip().split(" (pid")[0].strip()
        if _n:
            _names.append(_n)
ok("清单里每一项都在允许名单内（含空清单也算过）",
   all(n in ALLOWED for n in _names), str(sorted(set(_names))))
_bad = sorted(set(n for n in _names if n.lower() in FORBIDDEN))
ok("**绝不列别人的进程**（node/powershell 等一律不许出现）", not _bad, str(_bad))
ok("有合计行（读出条数）", any("PROBE 合计" in ln for ln in _lines),
   next((ln for ln in _lines if "PROBE 合计" in ln), ""))

print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
