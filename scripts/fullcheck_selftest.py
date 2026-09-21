# -*- coding: utf-8 -*-
"""把 `scripts/fullcheck.py` 纳入自检套件（审计第七轮 **V-R7-2**）。

背景（审计实测）：`fullcheck.py` 有 126 项检查、自称"每次修改后必跑"，但它的文件名不含
`selftest` ⇒ `run_all_selftests.py` 的收集条件**永远收不到它**；于是它自己红着 3 项（夹具缺预热 /
示例配置漂移 / 垫片误判）几个月没人看见 —— 两个检查器各说各话。
⇒ 本判据当场跑一遍它，按它自己的汇总行判红绿：它红 ⇒ 套件同步红（两边不允许不一致）。

判据：`fullcheck.py` 退出码 0、汇总行 `==== N 项检查，0 项失败 ====`、且 N 不少于 100。
（N 的下限是防"检查被删空后照样绿"：当前实测 126。）
反例锚：把 `fullcheck.py` 的夹具预热撤掉（＝审计抓到的那个假红）⇒ 本判据必红（已实测）。

用法：`runtime\\python\\python.exe scripts\\fullcheck_selftest.py`
"""
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FULLCHECK = os.path.join(ROOT, "scripts", "fullcheck.py")
MIN_ITEMS = 100                      # 实测 126；掉到 100 以下说明检查被删空了

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


env = dict(os.environ)
env["PYTHONIOENCODING"] = "utf-8"    # 重定向下按 UTF-8 编码，否则中文汇总行会被 GBK 编崩
env["PM_JUDGE_NO_PROC"] = "1"        # 判据环境：不许起进程/开端口

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0   # 不许闪控制台窗
_r = subprocess.run([sys.executable, FULLCHECK], cwd=ROOT, env=env, creationflags=_NO_WINDOW,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
_out = _r.stdout.decode("utf-8", "replace")
_m = re.search(r"====\s*(\d+)\s*项检查，\s*(\d+)\s*项失败\s*====", _out)
_items = int(_m.group(1)) if _m else 0
_failed = int(_m.group(2)) if _m else -1
_ok = (_r.returncode == 0) and (_m is not None) and (_failed == 0) and (_items >= MIN_ITEMS)

ok("fullcheck 独立检查器跑通（%d 项 / %d 项失败，rc=%s，下限 %d 项）" % (_items, _failed, _r.returncode, MIN_ITEMS),
   _ok, "解析到汇总行=%s" % (_m is not None))
if not _ok:
    # 失败时把它的失败项原样带出来，省得再跑一遍
    _tail = [ln for ln in _out.splitlines() if ln.startswith("FAIL") or ln.strip().startswith("- ")]
    for ln in _tail[:12]:
        print("       " + ln.strip()[:140])
    print("       ⚠️ 跑 `scripts\\fullcheck.py` 看全量输出（两个检查器不允许一个绿一个红）")

print("== [fullcheck] 判据：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
