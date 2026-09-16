# -*- coding: utf-8 -*-
"""日志体积治理自测（不需要微信、不碰真实日志目录）：裁剪 / 保尾 / 过期清理 / 幂等 / 轮转 handler。
用法：py -3 scripts/log_housekeeping_selftest.py
"""
import logging
import logging.handlers
import os
import shutil
import sys

try:      # 控制台默认 GBK：自检里的 ✔/✘ 一旦被重定向就 UnicodeEncodeError 崩掉整条自检
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent import log_housekeeping as lh        # noqa: E402

PASS = FAIL = 0


def ck(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✔ %s" % name)
    else:
        FAIL += 1
        print("  ✘ %s  %s" % (name, detail))


tmp = tempfile.mkdtemp(prefix="loghouse_")


def mkdirs():
    for d in ("logs", "data", "wechatauto_logs"):
        os.makedirs(os.path.join(tmp, d), exist_ok=True)


mkdirs()
print("① 超限文件：只保尾部，且头一行写明裁过")
big = os.path.join(tmp, "logs", "onestart.log")
with open(big, "w", encoding="utf-8") as f:
    f.write("旧" * 80000)                       # ~720KB（UTF-8 每字 3 字节）
    f.write("TAIL_MARKER_20260913")
n = lh.trim_file(big, max_bytes=50 * 1024, keep_bytes=20 * 1024)
size = os.path.getsize(big)
ck("裁掉了内容", n > 0, "n=%s" % n)
ck("体积降到上限附近", size <= 50 * 1024, "size=%s" % size)
body = open(big, encoding="utf-8").read()
ck("保留的是尾部", "TAIL_MARKER_20260913" in body)
ck("头一行有治理标记", body.startswith("[日志治理]"))

print("② 未超限文件：一个字都不动")
small = os.path.join(tmp, "logs", "wx_agent.log")
open(small, "w", encoding="utf-8").write("小日志\n" * 10)
mt = os.path.getmtime(small)
n2 = lh.trim_file(small, max_bytes=1024 * 1024, keep_bytes=1024)
ck("返回 0", n2 == 0)
ck("内容未变", open(small, encoding="utf-8").read().count("小日志") == 10)
ck("mtime 未变", abs(os.path.getmtime(small) - mt) < 1e-6)

print("③ 过期/未过期的驱动库日志")
old = os.path.join(tmp, "wechatauto_logs", "app_20200101.log")
new = os.path.join(tmp, "wechatauto_logs", "app_20990101.log")
open(old, "w").write("x")
open(new, "w").write("x")
past = time.time() - 30 * 86400
os.utime(old, (past, past))
res = lh.sweep(tmp, keep_days=14)
ck("过期日志被删", not os.path.exists(old))
ck("未过期日志保留", os.path.exists(new))
ck("返回里记了删除项", "app_20200101.log" in res["deleted"])

print("④ 幂等 + 目录缺失不炸")
res2 = lh.sweep(tmp, keep_days=14)
ck("第二次没有可裁的（除已裁那次）", not res2["deleted"])
empty = tempfile.mkdtemp(prefix="loghouse_empty_")
try:
    r3 = lh.sweep(empty, keep_days=14)
    ck("空目录不抛异常", r3["freed"] == 0)
except Exception as e:      # noqa: BLE001
    ck("空目录不抛异常", False, repr(e))
shutil.rmtree(empty, ignore_errors=True)

print("⑤ 轮转 handler：超过上限会生成 .1 备份且主文件不再涨")
rot = os.path.join(tmp, "logs", "persona_morph.log")
h = logging.handlers.RotatingFileHandler(rot, maxBytes=4096, backupCount=2, encoding="utf-8")
lg = logging.getLogger("rot-test")
lg.setLevel(logging.INFO)
lg.handlers = [h]
for i in range(400):
    lg.info("填充行 %04d %s", i, "x" * 60)
h.flush()
sz = os.path.getsize(rot)
ck("主文件受控（≤ 上限的 2 倍）", sz <= 8192, "size=%s" % sz)
ck("生成了 .1 备份", os.path.exists(rot + ".1"))
ck("备份数不超过 backupCount", not os.path.exists(rot + ".3"))
h.close()

print("⑥ 主运行日志改名（2026-09-16：bot_crash.log → runtime.log，名字误导排查）")
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_wd = open(os.path.join(_root, "scripts", "watchdog.py"), encoding="utf-8").read()
_lim = open(os.path.join(_root, "agent", "log_housekeeping.py"), encoding="utf-8").read()
_os_ = open(os.path.join(_root, "scripts", "onestart.py"), encoding="utf-8").read()
ck("watchdog 的主日志指向 data/runtime.log",
   'os.path.join(DATA, "runtime.log")' in _wd)
ck("watchdog 有一次性改名（老内容不丢）",
   "_migrate_crash_log_name" in _wd and "os.replace(_OLD_CRASH_LOG, CRASH_LOG)" in _wd)
ck("日志治理的新名在册", '("data/runtime.log"' in _lim)
ck("旧名仍在册（清用户机器上的残留）", '("data/bot_crash.log"' in _lim)
ck("启动提示不再把人指向旧名", "runtime.log" in _os_ and "bot_crash.log" not in _os_)

shutil.rmtree(tmp, ignore_errors=True)
print("\n== 结论：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
