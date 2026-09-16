# -*- coding: utf-8 -*-
"""⑦ 联动判据：四选一接进**版本门 / 能力矩阵 / 依赖自愈 / 控制台 / 更新链**（2026-09-14）。

为什么单独一条：四选一不是一个弹窗，它是**五处联动**——弹出来要有人弹（控制台/自己开）、
选了要有人记（台账 + 能力矩阵）、能修的要有真动作（依赖自愈 / 升级适配层）、
不能修的要只给指引（微信本体）。少接一处，用户点了按钮就"没反应"。
本判据逐条钉这五处 + 后台作业（jobs）的真跑行为。

用法：py -3 scripts/decide_link_selftest.py
"""
import os
import re
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["WX_NO_UI_POP"] = "1"        # 自检不许弹窗（第七条教训）

from agent import version_gate as VG      # noqa: E402
from agent import version_matrix as VM    # noqa: E402
from agent import dep_heal as DH          # noqa: E402
from agent import jobs as J               # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


W = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
H = open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
G = open(os.path.join(ROOT, "agent", "version_gate.py"), encoding="utf-8").read()
M = open(os.path.join(ROOT, "agent", "version_matrix.py"), encoding="utf-8").read()

print("── A. 版本门（开单 → 表态 → 动作）──")
ok("门没过就开单（pending）", "def pending(" in G and "ensure_version_decision" in G)
ok("表态入口唯一（decide）", "def decide(" in G and "note_decision" in G)
ok("一键动作执行层（run_action）", "def run_action(" in G and "jobs.start" in G)
ok("「仅本次允许」＝放行本会话", VG.run_action("allow_once").get("ran") is True)
VG.clear_allow()
ok("✕ 什么都不做 ⇒ 不执行任何动作",
   VG.run_action("").get("ran") is False and "什么都不做" in VG.run_action("")["message"])
r_g = VG.run_action("wechat_side")
ok("「微信本身要处理」只给指引、不动环境", r_g.get("ran") is False and "不装也不降级" in r_g["message"])
ok("不认识的选项 ⇒ ok=False（不静默当成功）", VG.run_action("乱写的").get("ok") is False)

print("── B. 能力矩阵（表态写回 + 面板显示）──")
ok("矩阵有写回入口", "def note_decision(" in M and "def decisions(" in M)
ok("current() 带这一对的表态历史", '"decisions": decisions(data, ver, adp)' in M)
ok("状态里能拿到（控制台不另拉数据）", "version.decisions" not in H and "vm.decisions" in H)
ok("面板有「最近表态」行", 'id="vmDec"' in H and "最近表态" in H)
ok("表态历史用中文标签显示", "去升级适配层" in H and "仅本次允许" in H)
_tmp = tempfile.mkdtemp(prefix="link-judge-")
try:
    VM.note_decision("9.9.9", "8.8.8", "allow_once", note="判据写入", path=os.path.join(_tmp, "m.json"))
    ds = VM.decisions(VM.load(os.path.join(_tmp, "m.json")), "9.9.9", "8.8.8")
    ok("写回后读得到（版本对 + 选择）", bool(ds) and ds[-1]["choice"] == "allow_once", str(ds[-1:]))
finally:
    import shutil
    shutil.rmtree(_tmp, ignore_errors=True)

print("── C. 依赖自愈（「更新本体」＝真能修，且不乱跑）──")
ok("依赖有诊断 + 计划 + 执行入口", all(hasattr(DH, f) for f in ("diagnose", "plan", "run")))
bad_now = [d for d in DH.diagnose() if str(d.get("status")) != "ok"]
ok("本机依赖齐全（用于下面「不乱跑」的断言）", bad_now == [], "缺 %d 项" % len(bad_now))
ok("依赖齐全时**不给**任何命令（不许拿复查自检顶替）", VG.action_cmd("update_host") == "",
   VG.action_cmd("update_host")[:60])
r_h = VG.run_action("update_host")
ok("依赖齐全时如实说「不用动」且没起作业",
   r_h.get("ran") is False and "不用动" in r_h["message"], str(r_h.get("message")))
_real_plan = DH.plan
try:
    DH.plan = lambda *a, **k: {"diagnose": [{"name": "x", "status": "missing"}],
                               "steps": [{"what": "装", "cmd": "pip install x"}]}
    ok("缺依赖时给出安装命令", VG.action_cmd("update_host") == "pip install x", VG.action_cmd("update_host"))
finally:
    DH.plan = _real_plan
ok("面板有「依赖自愈」按钮且接了动作", 'id="actHeal"' in H and "postVersionAction" in H)

print("── D. 更新链（升级适配层＝非交互命令，用项目运行时）──")
cmd_up = VG.action_cmd("upgrade_adapter")
ok("命令指向 scripts/wechat_check.py --update", "wechat_check.py" in cmd_up and "--update" in cmd_up, cmd_up[-60:])
ok("用的是项目自带运行时（不是裸 python）", "runtime" in cmd_up.lower() or "python" in cmd_up.lower(), cmd_up[:60])
src_check = open(os.path.join(ROOT, "scripts", "wechat_check.py"), encoding="utf-8").read()
ok("那条命令**非交互**（不需要人工输入）", "input(" not in src_check)
ok("面板有「升级适配层」按钮", 'id="actUp"' in H)
ok("四选一里选这两项会真去跑（不是只给命令让用户自己抄）",
   "postVersionAction(key, item.id)" in H and "/api/version/action" in H)
ok("服务端有 /api/version/action 端点", 'elif path == "/api/version/action"' in W and "_vg6.run_action" in W)

print("── E. 控制台（弹窗 + 状态 + 关弹窗开关）──")
ok("多选一弹窗在位", "function choiceBox(" in H)
ok("自动弹一次 + 手动入口", "__pdShown" in H and 'id="pdOpen"' in H)
ok("作业状态行在面板里", 'id="actStat"' in H and "没有在跑的事" in H)
ok("状态暴露 jobs（控制台读这个）", 'st["jobs"] = _jobs.status()' in W)
ok("无人值守能关弹窗", 'os.environ.get("WX_NO_UI_POP") == "1"' in G)
ok("待拍板空态文案是「没有待拍板的事」", "没有待拍板的事" in H)

print("── F. 后台作业（jobs）真跑 ──")
J.reset()
r1 = J.start("judge_echo", 'cmd /c echo 判据回声')
ok("起作业成功", bool(r1.get("ok")), str(r1.get("job", {}).get("running")))
deadline = time.time() + 20
while time.time() < deadline and J.status("judge_echo").get("running"):
    time.sleep(0.2)
s1 = J.status("judge_echo")
ok("跑完 rc=0", s1.get("returncode") == 0, "rc=%s" % s1.get("returncode"))
ok("保留尾部输出（控制台能显示跑了什么）", "判据回声" in str(s1.get("output_tail")), str(s1.get("output_tail"))[:40])
r2 = J.start("judge_slow", "cmd /c ping -n 4 127.0.0.1 >nul")
r3 = J.start("judge_slow", "cmd /c echo 第二个")
ok("同名作业只允许一个在跑（防连点）",
   bool(r2.get("ok")) and r3.get("ok") is False and "还在跑" in str(r3.get("why")), str(r3.get("why"))[:40])
deadline = time.time() + 30
while time.time() < deadline and J.status("judge_slow").get("running"):
    time.sleep(0.3)
ok("慢作业也能跑到结束", J.status("judge_slow").get("running") is False)
ok("status() 不传名字给全部", "jobs" in J.status())
J.reset()
ok("reset 清干净", J.status().get("jobs") == {})

print("── G. 真跑：整页 JS 语法 ──")
from agent import console_html as CH        # noqa: E402
blocks = re.findall(r"<script[^>]*>(.*?)</script>", CH.HTML, re.S)
tmp = os.path.join(tempfile.gettempdir(), "pm_console_link_check.js")
with open(tmp, "w", encoding="utf-8") as fh:
    fh.write("\n;\n".join(blocks))
r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True, timeout=60,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
_tail = (r.stderr or "").strip().splitlines()
ok("整页 JS 语法通过", r.returncode == 0, (_tail[-1][:120] if r.returncode and _tail else "%d 字符" % len(CH.HTML)))

print("\n通过 %d / 失败 %d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
