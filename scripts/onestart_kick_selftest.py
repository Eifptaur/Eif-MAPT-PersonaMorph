# -*- coding: utf-8 -*-
"""V7 判据：一键启动的「踢掉旧实例」不许再只靠 `wmic`，也不许静默失败。
V-R1-3 判据：只许踢**本安装目录（ROOT）下**的脚本，且 `taskkill` 的退出码要纳入结果。

跑法： runtime\\python\\python.exe scripts\\onestart_kick_selftest.py     退出码 0=全过 / 1=有失败

判据（含阴/阳对照）：
  A 阳性：`wmic` 不存在（本机与 Win11 24H2 的常态）⇒ **第一条命令必须是 PowerShell**，且老实例真被踢掉；
  B 兜底：PowerShell 挂掉 ⇒ 退到 wmic，仍然要踢；
  C 留痕：两条都失败 ⇒ **必须写日志 + 返回值带原因**（V7 原缺陷就是 `except Exception: pass` 全吞）；
  D 判据自身的纪律（不写产品状态）；
  E **V-R1-3（本安装目录判定 + 退出码）**：
     E1 阴：命令行含 `watchdog.py`/`onestart.py`/`persona_morph.py` 但**路径不在 ROOT 下**
             ⇒ 一个都不许杀（原来"文件名像就杀"会误伤别人项目/另一份解压目录）；
     E2 阳：命令行里的脚本路径**在 ROOT 下**（含带引号、`/` 与 `\\` 混写）⇒ 必须被踢；
     E3 阴：`taskkill` 返回非 0 ⇒ 该 pid **不计入 killed**、必须进 `failed` 并留痕；
     E4 自己的那条（ROOT 下的 onestart.py，pid==自己）⇒ 永不自杀。

⛔ 纪律：本判据**不碰产品状态**——`onestart.LOG_PATH` 指到临时目录、产品 `logs/onestart.log` 前后字节数必须不变；
   `agent.log_housekeeping` 在导入 onestart 之前被打桩（否则导入即扫产品日志）。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


# ── 先把 log_housekeeping 打桩：onestart 是"导入即扫产品日志"的脚本，判据不许写产品状态 ──
_stub = types.ModuleType("agent.log_housekeeping")
_stub.sweep = lambda *a, **k: None
sys.modules["agent.log_housekeeping"] = _stub


def load_onestart(tmpdir):
    """按模块名加载 onestart.py（它不是包，且 must not 当 __main__ 跑）。"""
    spec = importlib.util.spec_from_file_location("onestart_under_test", os.path.join(HERE, "onestart.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.LOG_PATH = os.path.join(tmpdir, "onestart.log")     # 判据自己的日志，别写进 logs\
    return mod


class FakeProc(object):
    def __init__(self, stdout=b"", returncode=0, stderr=b""):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


class FakeSub(object):
    """假 subprocess：记录每一条命令，按 caller 给的规则作答。"""

    def __init__(self, ps=None, wmic=None, taskkill_rc=0, taskkill_err=b""):
        self.ps, self.wmic = ps, wmic
        self.taskkill_rc, self.taskkill_err = taskkill_rc, taskkill_err
        self.calls = []

    def run(self, cmd, **kw):
        self.calls.append(list(cmd))
        if str(cmd[0]).lower().startswith("powershell"):
            if isinstance(self.ps, Exception):
                raise self.ps
            return FakeProc(self.ps or b"", 0)
        if str(cmd[0]).lower() == "taskkill":
            return FakeProc(b"", self.taskkill_rc, self.taskkill_err)
        raise AssertionError("判据没预期的命令：%r" % (cmd,))

    def check_output(self, cmd, **kw):
        self.calls.append(["check_output", cmd])
        if self.wmic is None:
            raise FileNotFoundError("[WinError 2] 系统找不到指定的文件（wmic 不在，本机真实情况）")
        if isinstance(self.wmic, Exception):
            raise self.wmic
        return self.wmic


def first_kind(calls):
    for c in calls:
        if c and str(c[0]).lower().startswith("powershell"):
            return "powershell"
        if c and str(c[0]) == "check_output":
            return "wmic"
    return ""


def _func_src(src, name):
    """粗略切出某个函数的源码文本（判据只用来查"有没有吞异常/有没有留痕"）。"""
    i = src.find("def " + name + "(")
    if i < 0:
        return ""
    j = src.find("\ndef ", i + 1)
    return src[i:j if j > 0 else len(src)]


def main():
    tmp = tempfile.mkdtemp(prefix="onestart-kick-")
    prod_log = os.path.join(ROOT, "logs", "onestart.log")
    _before = os.path.getsize(prod_log) if os.path.exists(prod_log) else -1
    mod = load_onestart(tmp)
    # 时间也打桩：只替掉 sleep，别去改真 time 模块（否则会污染同进程里其它东西）
    import time as _time
    mod.time = types.SimpleNamespace(sleep=lambda *a: None, strftime=_time.strftime,
                                     localtime=_time.localtime, time=_time.time)

    try:
        print("== A. 阳性：wmic 不在（本机常态）⇒ 必须走 PowerShell，且老实例真被踢 ==")
        # ⚠️ 2026-09-20（V-R1-3 修）后判据**必须喂本安装目录（ROOT）下的路径**：
        #    "文件名像就杀"正是那条漏洞，现在只认 ROOT 前缀 ⇒ 夹具也要照真实命令行造。
        ps_out = ("4242\tpython.exe %s\\scripts\\watchdog.py --delay=3\r\n"
                  "4243\tpython.exe \"%s/scripts/persona_morph.py\"\r\n"
                  "4244\tpython.exe C:\\x\\tools\\plugin_helper.py\r\n"
                  % (ROOT, ROOT.replace("\\", "/"))).encode("utf-8")
        sub = FakeSub(ps=ps_out, wmic=None)          # wmic 一律失败（真机就是没有它）
        mod.subprocess = sub
        mod.time.sleep = lambda *a: None
        rep = mod._kick_old_instance()
        ok("枚举第一条命令是 PowerShell（不是 wmic）", first_kind(sub.calls) == "powershell", sub.calls[:3])
        ok("没碰过 wmic 那条路（wmic 不在时不许以它为先）",
           all("wmic process where" not in str(c) for c in sub.calls), [c for c in sub.calls if "wmic" in str(c)])
        ok("真踢掉了 watchdog/persona_morph 两个老实例", sorted(rep["killed"]) == [4242, 4243], rep)
        ok("不误杀无关进程（plugin 那条不许踢）", 4244 not in rep["killed"], rep)
        ok("没出错就必须 error 为空（不谎报成功）", rep["error"] == "", rep)
        ok("枚举方式写清了是 PowerShell", rep["how"].startswith("PowerShell"), rep["how"])
        sub2 = FakeSub(ps=("%d\tpython.exe %s\\scripts\\onestart.py\r\n"
                          % (os.getpid(), ROOT)).encode("utf-8"))
        mod.subprocess = sub2
        rep2 = mod._kick_old_instance()
        ok("阴性对照：自己那条 onestart.py **不许**被自己踢（否则启动器自杀）",
           rep2["killed"] == [], rep2)

        print("\n== B. 兜底：PowerShell 挂掉 ⇒ 退到 wmic，仍然要踢 ==")
        csv = ("Node,CommandLine,ProcessId\r\n"
               "PC,python.exe %s\\scripts\\watchdog.py --serve,5555\r\n"
               "PC,python.exe C:\\x\\tools\\plugin_helper.py,5556\r\n" % ROOT)
        sub3 = FakeSub(ps=OSError("powershell 不在 PATH"), wmic=csv)
        mod.subprocess = sub3
        rep3 = mod._kick_old_instance()
        ok("PowerShell 失败后确实试了 wmic", any(c[0] == "check_output" for c in sub3.calls), sub3.calls[-1][:1])
        ok("wmic 这条路也能踢掉老实例", rep3["killed"] == [5555], rep3)
        ok("兜底成功 ⇒ error 仍为空", rep3["error"] == "", rep3)

        print("\n== C. 留痕：两条都失败 ⇒ 必须写日志 + 返回值带原因（V7 原缺陷＝全吞）==")
        sub4 = FakeSub(ps=OSError("powershell 不在 PATH"), wmic=FileNotFoundError("wmic 不在"))
        mod.subprocess = sub4
        rep4 = mod._kick_old_instance()
        ok("两条都失败 ⇒ killed 空、error 非空（原因回传，不静默）",
           rep4["killed"] == [] and bool(rep4["error"]), rep4)
        ok("原因里两条路都点了名（PowerShell 与 wmic）",
           ("PowerShell" in rep4["error"]) and ("wmic" in rep4["error"]), rep4["error"][:160])
        _logtxt = ""
        if os.path.exists(mod.LOG_PATH):
            _logtxt = open(mod.LOG_PATH, encoding="utf-8", errors="replace").read()
        ok("**日志里留下了痕**（本次没有踢任何旧实例 + 原因）",
           ("没有踢任何旧实例" in _logtxt) and ("原因" in _logtxt), _logtxt.strip().splitlines()[-1:])
        ok("源代码级：_kick_old_instance 里不再有吞异常的 `except Exception: pass`，且必留痕",
           (lambda b: ("except Exception:" + chr(10) + "        pass") not in b and "没能枚举" in b)(
               _func_src(open(os.path.join(HERE, "onestart.py"), encoding="utf-8").read(),
                         "_kick_old_instance")))

        print("\n== D. 判据自身的纪律：不许写产品状态 ==")
        _after = os.path.getsize(prod_log) if os.path.exists(prod_log) else -1
        ok("产品 logs/onestart.log 字节数不变（判据不写产品日志）", _before == _after, "%s -> %s" % (_before, _after))
        ok("判据的日志写在临时目录（LOG_PATH 已被指向 tmp）", mod.LOG_PATH.startswith(tmp), mod.LOG_PATH)

        print("\n== E. V-R1-3：只许杀本安装目录里的脚本 + 看 taskkill 退出码 ==")
        _foreign = ("4711\tpython.exe X:\\demo\\someone\\myproj\\watchdog.py --serve\r\n"
                    "4712\tpython.exe D:\\tools\\onestart.py\r\n"
                    "4713\tpython.exe C:\\backup\\persona-morph-old\\scripts\\persona_morph.py\r\n"
                    "4714\tpython.exe %s\\scripts\\persona_morph.py plugin helper\r\n" % ROOT)
        sub_neg = FakeSub(ps=_foreign.encode("utf-8"))
        mod.subprocess = sub_neg
        rep_neg = mod._kick_old_instance()
        ok("E1 阴：命令行**含同名脚本但不在本安装目录** ⇒ 一个都不许杀（V-R1-3 本体）",
           rep_neg["killed"] == [], "killed=%s calls=%s" % (rep_neg["killed"], sub_neg.calls))
        ok("E1b 连 taskkill 都不该对它发起（不是「杀了但失败」，是「根本不许碰」）",
           not any(str(c[0]).lower() == "taskkill" for c in sub_neg.calls), sub_neg.calls)
        ok("E1c 被放弃的候选要留痕（为什么没杀说得出来）",
           len(rep_neg.get("skipped") or []) >= 3
           and any("本安装目录" in str(x.get("why") or "") for x in rep_neg["skipped"]),
           str(rep_neg.get("skipped"))[:160])
        sub_pos = FakeSub(ps=("4721\tpython.exe %s\\scripts\\watchdog.py\r\n"
                              % ROOT).encode("utf-8"))
        mod.subprocess = sub_pos
        rep_pos = mod._kick_old_instance()
        ok("E2 阳：脚本路径在 ROOT 下 ⇒ 必须被踢（别把正事一起禁掉）",
           rep_pos["killed"] == [4721], rep_pos)
        sub_fail = FakeSub(ps=("4731\tpython.exe %s\\scripts\\persona_morph.py\r\n"
                               % ROOT).encode("utf-8"),
                           taskkill_rc=5, taskkill_err="错误: 拒绝访问。".encode("gbk"))
        mod.subprocess = sub_fail
        rep_fail = mod._kick_old_instance()
        ok("E3 阴：taskkill 返回非 0 ⇒ 该 pid **不计入 killed**（原来丢弃返回值，一律算「已踢」）",
           rep_fail["killed"] == [], rep_fail)
        ok("E3b …且必须进 failed 带退出码（踢了/没踢/失败都留痕）",
           [x.get("pid") for x in (rep_fail.get("failed") or [])] == [4731]
           and int((rep_fail["failed"][0] or {}).get("rc") or 0) == 5, rep_fail)
        _logtxt2 = ""
        if os.path.exists(mod.LOG_PATH):
            _logtxt2 = open(mod.LOG_PATH, encoding="utf-8", errors="replace").read()
        ok("E3c taskkill 失败写进了日志", "踢旧实例 PID=4731 失败" in _logtxt2, _logtxt2.strip().splitlines()[-1:])
        ok("E4 返回形状不变（killed/how/error 三件套仍在，失败信息是**新增**键）",
           set(("killed", "how", "error")) <= set(rep_fail.keys()), sorted(rep_fail.keys()))
        ok("E4b _kick_old_instance 走的是「路径判定」而不是「文件名子串判定」",
           (lambda b: ("_is_our_install" in b) and ("any(k in cmd for k in" not in b))(
               _func_src(open(os.path.join(HERE, "onestart.py"), encoding="utf-8").read(),
                         "_kick_old_instance")))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n==== onestart 踢旧实例判据：%d 通过 / %d 失败 ====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
