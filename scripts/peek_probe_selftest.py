# -*- coding: utf-8 -*-
"""全屏顶栏「贴顶滑出」判据（群相）——作者报的「点不到最小化」这条不许回归。

背景（作者 2026-09-20 原话）：「**最大化后，上边栏碰触之后维持的时间太短，导致点不到最小化**」。
真因：全屏时顶栏的显示判据只有"光标贴屏幕顶端 4px 内"——而三个按钮在顶栏 y=8~34 那一带，
**鼠标一往下挪去点按钮就离开了那 4px** ⇒ 顶栏立刻收起 ⇒ 永远点不到。
修法：判据拆成 进入（贴顶 4px）/ 保持（还在顶栏那块区域内）/ 宽限（离开后 0.7 秒），
实现＝`launcher-src/launcher.cs` 的 `BarPeek`，离线仿真入口＝`一键启动.exe --peekprobe`（纯计算、不起窗）。

本判据验四件：
  ① 仿真四条路径的结论（含"不上顶端就绝不弹出"的反面控制）；
  ② **灵敏度对照**：同一条路径用**老判据**跑必须失败（A=1 而 A_OLD=0）——否则说明判据没在测东西；
  ③ 参数在合理区间（宽限 ≥500ms、定时器 ≤150ms、保持区按顶栏真实高度算）；
  ④ 接线是真的：定时器调 `BarPeek.Next`、用 `_bar.Height`，且 `--peekprobe` 那条分支不起窗。
"""
import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "一键启动.exe")
SRC = os.path.join(ROOT, "launcher-src", "launcher.cs")
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

PASS = FAIL = 0


def ok(cond, msg, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", msg, "  [%s]" % detail if detail else ""))


print("── ① 仿真：一键启动.exe --peekprobe（纯计算、不起窗）──")
ok(os.path.exists(EXE), "一键启动.exe 在（判据要跑的是**盘上那个 exe**，不是源码）", EXE)
r = subprocess.run([EXE, "--peekprobe"], cwd=ROOT, capture_output=True, timeout=60,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
out = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
kv = {}
for line in out.splitlines():
    line = line.strip()
    if not line.startswith("PEEK "):
        continue
    rest = line[5:].strip()
    m = re.match(r"^([A-Za-z_]+)\s*=\s*(\S+)$", rest)          # PEEK GRACE_MS=700
    if m:
        kv[m.group(1)] = m.group(2)
        continue
    m = re.match(r"^([A-Za-z_]+)\s+.*=\s*(\S+)$", rest)        # PEEK A over_button_visible=1
    if m:
        kv[m.group(1)] = m.group(2)
ok(bool(kv), "exe 打出了 PEEK 标记（老 exe 不会有 ⇒ 顺带证明跑的是新编出来的那个）", out.strip().splitlines()[-1][:60] if out.strip() else "无输出")

ok(kv.get("A") == "1",
   "A 贴顶端→挪到最小化按钮 ⇒ 按钮那一刻顶栏**仍然可见**", "A=%s" % kv.get("A"))
ok(kv.get("A_OLD") == "0",
   "A_OLD 同路径用**老判据**必须失败（灵敏度对照：修好的是「保持区+宽限」这一环）", "A_OLD=%s" % kv.get("A_OLD"))
ok(kv.get("B") == "1", "B 贴顶端→挪走并等过宽限 ⇒ 顶栏收起（该收的时候要收）", "B=%s" % kv.get("B"))
ok(kv.get("C") == "1", "C 中途抖出去又回来（未过宽限）⇒ 全程可见，不闪", "C=%s" % kv.get("C"))
ok(kv.get("D") == "1",
   "D **反面控制**：从不碰屏幕顶端、只在页面顶部晃 ⇒ 绝不许弹出（否则正常用页面被挡）", "D=%s" % kv.get("D"))
ok(kv.get("RESULT") == "ok", "仿真自评 RESULT=ok", "RESULT=%s" % kv.get("RESULT"))

print("\n── ② 参数区间 ──")


def _num(k, d=0):
    try:
        return int(str(kv.get(k) or d))
    except Exception:
        return d


ok(_num("GRACE_MS") >= 500, "离开宽限 ≥500ms（够从顶端挪到按钮上）", "GRACE_MS=%s" % kv.get("GRACE_MS"))
ok(0 < _num("TICK_MS") <= 150, "定时器间隔 ≤150ms（跟手；原来 200ms 偏木）", "TICK_MS=%s" % kv.get("TICK_MS"))
ok(_num("BAR_H") == 42, "保持区按**顶栏真实高度**算（42px，与 ConsoleForm 里一致）", "BAR_H=%s" % kv.get("BAR_H"))

print("\n── ③ 接线（源码级：判据要盯着真正跑的那几行）──")
src = io.open(SRC, encoding="utf-8", errors="replace").read()
ok("BarPeek.Next(_barPeek" in src, "定时器用 BarPeek.Next 决策（不是自己另写一套）")
ok(("+ BarPeek.KeepSlack" in src) and ("_bar.Height" in src),
   "保持区用的是 `_bar.Height`（顶栏多高就留多高，不写死）")
ok("peek.Interval = BarPeek.TickMs" in src, "定时器间隔取自 BarPeek.TickMs")
ok("bool atTop = (c.Y <= mo.Top + 4)" not in src,
   "老那一条「只看顶端 4px」的判据已经不在（`atTop` 那种写法清掉了）")
ok("_barPeek = false; _barLeaveAt = 0; SyncBarVisible();" in src,
   "离开时把倒计时清零（换到非全屏也不会留脏状态）")
_i = src.find('args[0] == "--peekprobe"')
ok(_i > 0, "--peekprobe 分支存在")
if _i > 0:
    _j = src.find('args[0] == "--', _i + 10)          # 截到**下一个**探针分支为止（别把别人的 Application.Run 算进来）
    seg = src[_i:_j] if _j > _i else src[_i:_i + 700]
    ok(("return;" in seg) and ("Application.Run(" not in seg),
       "…该分支**不起窗**（不调 Application.Run）且立刻 return", "段长 %d" % len(seg))
ok("unchecked(nowMs - leaveAtMs)" in src,
   "溢出安全：用无符号差值比较，TickCount 绕回也不会误判")

print("\n==== 全屏顶栏判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
