# -*- coding: utf-8 -*-
"""依赖安装链判据（2026-09-16 立，起因＝用户报「安装依赖十几分钟，然后一键启动失败」）。

钉四件事：
  ① pip 输出必须**实时流式**（以前 `subprocess.run(capture_output=True)` ⇒ 十几分钟界面上
     一片空白，用户以为卡死；判据用「跑一条会打字的命令，看输出是否当场拿到」来证）；
  ② **不设总时长上限**，只在「连续无输出」时才判卡死（慢网装十几分钟是正常的）；
  ③ 判定口径＝**必需项齐就算过**，可选大件缺了不许阻断启动（`verdict()` 行为级断言）；
  ④ onestart 的等待同样按空闲判超时，且进度解析要认得 pip 那些**缩进**过的行。
跑法：py -3 scripts\\deps_install_selftest.py
"""
import contextlib
import io
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if os.path.join(ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "scripts"))

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   %s%s" % (name, ("  [%s]" % detail) if detail else ""))
    else:
        FAIL += 1
        print("  FAIL %s%s" % (name, ("  [%s]" % detail) if detail else ""))


def src(rel):
    return open(os.path.join(ROOT, rel.replace("/", os.sep)),
                encoding="utf-8", errors="replace").read()


import setup_deps as SD          # noqa: E402   （scripts/ 已在 sys.path 上）

print("── A. 判定口径：必需齐就算过，可选缺不阻断 ──")
_r1, _m1 = SD.verdict([("wechatauto-replica", "", "1.1.5.1", False)],
                      [("wechatauto-replica", "", "1.1.5.1", False)])
ok("必需项缺 ⇒ 判失败，并点明是必需依赖", _r1 == 1 and "必需依赖没装齐" in _m1, _m1[:64])
_r2, _m2 = SD.verdict([("wechatauto-replica", "1.2.2.2", "1.1.5.1", True)],
                      [("wechatauto-replica", "1.2.2.2", "1.1.5.1", True),
                       ("opencv-python", "", ">=5", False), ("edge-tts", "", ">=6", False)])
ok("必需齐、可选缺 ⇒ 判通过（不许让慢网把一键启动卡死）", _r2 == 0 and "不影响启动" in _m2, _m2[:72])
ok("可选缺时把缺的项列出来（用户知道还差什么）", "opencv-python" in _m2, _m2[:72])
_r3, _m3 = SD.verdict([("a", "1", ">=1", True)], [("a", "1", ">=1", True)])
ok("全齐 ⇒ 通过并报总数", _r3 == 0 and "全部" in _m3, _m3[:64])

print("── B. 行为级：流式 · 无总时长上限 · 空闲判卡死 ──")
# ⛔ V-R8-7（第八轮）：这里原有三条**源码级**判据（`"def _run_stream(" in _sd`、
#   `"capture_output=True" not in _sd`、`"IDLE_LIMIT" in _sd`）—— 它们只证明"名字在文件里"，
#   把 `_run_stream` 改成整段缓存、或把空闲上限真删掉，照样绿。**已按审计建议删掉**：
#   下面 C 段是**行为级**覆盖（真跑一条会打字的命令看输出是否当场拿到 = 流式；
#   真跑一条卡住的命令看它按空闲上限被杀 = 空闲判死），比这三条硬。
#   ⚠️ 留着的是"**不存在**"类判据（`timeout=900 not in 源码`）—— 那类没法用行为证否，只能扫源码。
_sd = src("scripts/setup_deps.py")
ok("没有 pip 的总时长上限（timeout=900 已去掉）", "timeout=900" not in _sd)
ok("所有源都失败后有再试一次的兜底（用最快的那个源）", "再试一次最快的那个源" in _sd)
ok("先并行测速挑最快的镜像（慢在往返次数，不是字节数）",
   "def pick_fastest_mirror(" in _sd and "镜像测速：" in _sd)
ok("两趟装：先 --no-deps 装主包，再补齐传递依赖",
   '"--no-deps",' in _sd and "补齐传递依赖" in _sd)
ok("镜像扩到 6 条（含腾讯/华为/中科大）",
   all(h in _sd for h in ("cloud.tencent.com", "huaweicloud.com", "ustc.edu.cn")))
ok("安装前如实说清体积与『不会重下/已提速』",
   "装过的包不会重下" in _sd and "先测速挑最快的源" in _sd)
_os_ = src("scripts/onestart.py")
ok("onestart 的等待按空闲判超时（读 last_out[0]）", "last_out[0] > timeout" in _os_)
ok("onestart 里不再用总时长判死", "time.time() - t0 > timeout" not in _os_)
ok("onestart 也先说清「慢网十几分钟正常」", "慢网十几分钟正常" in _os_)
_seg = _os_.split("def _deps_progress")[1][:520] if "def _deps_progress" in _os_ else ""
ok("进度解析先 strip（pip 的行是缩进的）", "strip()" in _seg, _seg[:70].replace("\n", " "))

print("── C. 行为：_run_stream 真的边跑边给输出 ──")
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _rc, _tail = SD._run_stream([sys.executable, "-c", "print('stream-ok')"])
ok("跑一条命令拿到 rc=0 与输出", _rc == 0 and "stream-ok" in _tail,
   "rc=%s tail=%r" % (_rc, _tail[:40]))
ok("输出是流式出去的（当场写进 stdout，而不是只留在返回值里）",
   "stream-ok" in _buf.getvalue(), _buf.getvalue()[:60])
_t0 = time.time()
with contextlib.redirect_stdout(io.StringIO()):
    _rc2, _ = SD._run_stream([sys.executable, "-c",
                              "import time;print('x',flush=True);time.sleep(30)"], idle_limit=2)
_spent = time.time() - _t0
ok("卡住（无输出）时按空闲上限杀掉并判失败", _rc2 == 1 and _spent < 25,
   "rc=%s 用了 %.1fs" % (_rc2, _spent))

print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
