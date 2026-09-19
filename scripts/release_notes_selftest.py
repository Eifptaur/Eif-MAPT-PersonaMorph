# -*- coding: utf-8 -*-
"""发布说明写法判据（群相）——作者 2026-09-20 定的规矩，机械守住。

规矩（原话）：修 Bug 就只用说「修复了一些 bug」这句话，最多也就一句话，简单描述，
不要把什么东西都全部列出来了；新增功能倒是可以详细说说。

本判据验两件事：
  ① `scripts/release_notes.py` 的判定本身对（阴/阳对照都在，证明它不是恒真/恒假）；
  ② **真接线**：`make_manifest.py` 真的调了它（纯修 bug 的长公告 ⇒ 拒绝生成清单、exit 4，
     且**不写文件**；带「新增」的详细公告 ⇒ 放行）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

import release_notes as rn   # noqa: E402

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✔ " + msg)
    else:
        FAIL += 1
        print("  ✘ " + msg)


print("[A] 判定本身（阴性 + 阳性对照）")

# —— 阳性：就该放行的三种 ——
ok(rn.note_problems(["修复了一些 bug"]) == [], "「修复了一些 bug」放行")
ok(rn.note_problems(["修复了一些 bug，顺带让启动快了一点"]) == [],
   "一句简单描述（≤24 字、不列举）放行")
_long_feature = "新增：本地模型支持；语音回复三档；控制台文案通俗化；WebView2 引导器进在线包；反馈栏与防刷限流"
ok(rn.note_problems(_long_feature.split("；")) == [],
   "带「新增」的详细公告放行（新增功能允许详细写）")

# —— 阴性：就该拦住的四种 ——
_v140 = ("第二次漏洞筛查查出的 11 条全修了，含 3 条 P0 —— ①上版把「文件被占用」的判据收窄后，"
         "真实共享冲突识别不出来 ⇒ 点「立即更新」会整包回滚；②安装路径含空格时解析不出绿色版 Python")
ok(len(rn.note_problems([_v140])) >= 1, "纯修 bug 的长公告被拦（这正是要禁的形态）")
ok(len(rn.note_problems(["修了崩溃", "修了超时", "修了卡住"])) >= 1,
   "纯修 bug 但列成三条 ⇒ 被拦（不是靠长度才拦得住）")
ok(len(rn.text_problems("修复了一些 bug\n还修了崩溃和超时\n顺手加了点别的")) >= 1,
   "正文多行（把东西列出来）⇒ 被拦")
ok(len(rn.text_problems("①修了崩溃 ②修了超时")) >= 1, "圈码列举 ⇒ 被拦")

# —— 边界：空输入不误报（由调用方决定空是不是问题）——
ok(rn.note_problems([]) == [] and rn.text_problems("") == [],
   "空输入不误报（发版脚本自己要求非空）")

print("\n[B] 真接线：make_manifest.py 必须真的调用这道闸")

MKM = os.path.join(HERE, "make_manifest.py")
src = open(MKM, encoding="utf-8").read()
ok("import release_notes as rn" in src, "make_manifest.py 引入了 release_notes（同一份实现）")
ok("rn.note_problems(" in src, "make_manifest.py 真的调了 note_problems（不是只 import）")


def run_mkm(notes, out):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, MKM, "--notes", notes, "--out", out],
                          cwd=ROOT, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


tmp = tempfile.mkdtemp(prefix="rn_selftest_")
try:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    # 用假的 tracked 列表不现实（要真仓库），所以直接跑真脚本：它会读 git ls-files。
    out_bad = os.path.join(tmp, "bad")
    os.makedirs(out_bad, exist_ok=True)
    r = run_mkm(_v140, out_bad)
    ok(r.returncode == 4,
       "纯修 bug 的长公告 ⇒ make_manifest 拒绝（exit=%s）" % r.returncode)
    ok("说明写法" in (r.stdout or ""), "拒绝时打印了「说明写法」闸门的提示")
    ok(not os.path.exists(os.path.join(out_bad, "persona-morph-manifest.json")),
       "被拒时**没有**写出清单文件（拒绝要生效，不只是打印）")

    out_ok = os.path.join(tmp, "good")
    os.makedirs(out_ok, exist_ok=True)
    r2 = run_mkm(rn.FIX_ONLY_LINE, out_ok)
    ok(r2.returncode == 0, "「修复了一些 bug」⇒ 正常生成清单（exit=%s）" % r2.returncode)
    mp = os.path.join(out_ok, "persona-morph-manifest.json")
    ok(os.path.exists(mp), "放行时清单真的落盘了")
    if os.path.exists(mp):
        m = json.load(open(mp, encoding="utf-8"))
        ok(m["announce"]["notes"] == [rn.FIX_ONLY_LINE],
           "清单里的 announce.notes 就是那一句：%r" % (m["announce"]["notes"],))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n==== 发布说明写法判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
