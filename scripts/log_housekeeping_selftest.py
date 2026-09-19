# -*- coding: utf-8 -*-
"""日志体积治理自测（不需要微信、不碰真实日志目录）：裁剪 / 保尾 / 过期清理 / 幂等 / 轮转 handler。
用法：py -3 scripts/log_housekeeping_selftest.py
"""
import logging
import logging.handlers
import os
import re
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

print("⑦ V-R1-5：V-R1-5 补的三处「只增不减」")
# ① 真实厂造三个超限文件（真写盘，不伪造任何状态）⇒ sweep 后必须都被裁到上限内
_for_trim = (("data/input_audit.log", 1 * 1024 * 1024),
             ("logs/sd_local.log", 2 * 1024 * 1024),
             ("logs/installer.log", 2 * 1024 * 1024))
for _rel, _limb in _for_trim:
    _p = os.path.join(tmp, _rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(_p), exist_ok=True)
    with open(_p, "wb") as f:
        f.write(b"z" * (_limb + 400 * 1024))                # 真超限
    ck("V1 造出超限的 %s（%d 字节）" % (_rel, os.path.getsize(_p)), os.path.getsize(_p) > _limb)
_res7 = lh.sweep(tmp, keep_days=14)
for _rel, _limb in _for_trim:
    _p = os.path.join(tmp, _rel.replace("/", os.sep))
    ck("V1 %s 被裁到上限内（原来不在名单里 ⇒ 只增不减）" % _rel, os.path.getsize(_p) <= _limb,
       "size=%s lim=%s" % (os.path.getsize(_p), _limb))
    ck("V1b %s 记进了 trimmed（看得见治理动作）" % _rel, _rel in _res7["trimmed"], str(_res7["trimmed"].keys()))

# ② 失败现场：整目录按 mtime 清（老的删、当天留）
_fail = os.path.join(tmp, "wechatauto_logs", "fail")
_old_d = os.path.join(_fail, "20260101-000000_old")
_new_d = os.path.join(_fail, "20990101-000000_new")
os.makedirs(_old_d, exist_ok=True)
os.makedirs(_new_d, exist_ok=True)
with open(os.path.join(_old_d, "shot.png"), "wb") as f:
    f.write(b"p" * 4096)
with open(os.path.join(_old_d, "probe.json"), "w", encoding="utf-8") as f:
    f.write("{}")
with open(os.path.join(_new_d, "shot.png"), "wb") as f:
    f.write(b"p" * 4096)
_t_old = time.time() - 40 * 86400
os.utime(os.path.join(_old_d, "shot.png"), (_t_old, _t_old))
os.utime(os.path.join(_old_d, "probe.json"), (_t_old, _t_old))
os.utime(_old_d, (_t_old, _t_old))
_res7b = lh.sweep(tmp, keep_days=14)
ck("V2 过期失败现场**整目录**被删（原来只删 *.log、进不了子目录）", not os.path.exists(_old_d), _old_d)
ck("V2b 未过期的失败现场保留", os.path.isdir(_new_d), _new_d)
ck("V2c 删除项如实记进返回值（带目录名）", "fail/20260101-000000_old" in _res7b["deleted"], str(_res7b["deleted"]))
ck("V2d 释放字节数是真算的（≥ shot.png 的 4096）",
   int(_res7b["trimmed"].get("wechatauto_logs/fail/20260101-000000_old") or 0) >= 4096,
   str(_res7b["trimmed"].get("wechatauto_logs/fail/20260101-000000_old")))

# ③ GLOB_DAILY：要么真被用、要么不存在（不许留死代码）
ck("V3 GLOB_DAILY 要么被引用、要么已删（V-R1-5：定义后全仓无人使用＝死代码）",
   _lim.count("GLOB_DAILY") == 0 or _lim.count("GLOB_DAILY") >= 2, "出现 %d 次" % _lim.count("GLOB_DAILY"))

# ④ 名单必须覆盖源码里真的会追加写的日志路径（机械集合差，差不为空即红）
print("⑧ V-R1-5：名单覆盖度（源码里出现的 logs/data 日志路径必须都在册）")
_known = [r for r, _b, _k in lh.LOG_LIMITS]
_NAMED = ["logs/persona_morph.log", "logs/onestart.log", "logs/wx_agent.log", "logs/sd_local.log",
          "logs/installer.log", "data/runtime.log", "data/bot_crash.log", "data/input_audit.log",
          "data/listener_failed.jsonl"]
for _n in _NAMED:
    ck("V4 名单里有 %s" % _n, _n in _known, str(_known))
_scan_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_found = set()
for _sub in ("agent", "scripts"):
    for _fn in os.listdir(os.path.join(_scan_root, _sub)):
        if not _fn.endswith(".py") or "selftest" in _fn:
            continue                                # 判据自己的临时目录不算产品的日志
        _txt = open(os.path.join(_scan_root, _sub, _fn), encoding="utf-8", errors="replace").read()
        for _m in re.finditer(r"""["'](logs|data)["']\s*,\s*["']([^"']+\.log)["']""", _txt):
            _found.add("%s/%s" % (_m.group(1), _m.group(2)))
        for _m in re.finditer(r"""(?:LOG_DIR|LOGS_DIR|DATA_DIR)\s*,\s*["']([^"']+\.log)["']""", _txt):
            _found.add("logs/" + _m.group(1))
_missing = sorted(p for p in _found
                  if p not in _known and p.split("/", 1)[1] not in ("console.url", "browser_opened.txt",
                                                                    "browser_opened.lock", "python_path.txt"))
ck("V5 源码里追加写的**日志**路径没有漏在名单外（漏了就是下一个 V-R1-5）",
   not _missing, "扫到=%s 漏=%s" % (sorted(_found), _missing))
# ⚠️ `.jsonl` 那些是**记录存储**（台账/风险事件/模型思考），不是日志：它们由各自的模块按"条数/天数"
#    治理（`history_prune.py` 等），**不许**塞进 LOG_LIMITS —— 那会把台账当成日志截尾，等于丢证据。
#    这条机械断言就是为了防止下一个人"顺手"把它们并进日志名单。
for _store in ("data/message_ledger.jsonl", "data/session_log.jsonl", "logs/thoughts.jsonl"):
    ck("V6 记录存储 %s **不在**日志名单里（数据不是日志，别用截尾治理）" % _store, _store not in _known,
       str(_known))
_scan2 = set()
for _sub in ("agent", "scripts"):
    for _fn in os.listdir(os.path.join(_scan_root, _sub)):
        if not _fn.endswith(".py") or "selftest" in _fn:
            continue
        _txt = open(os.path.join(_scan_root, _sub, _fn), encoding="utf-8", errors="replace").read()
        for _m in re.finditer(r"""["'](logs|data)["']\s*,\s*["']([^"']+\.jsonl)["']""", _txt):
            _scan2.add("%s/%s" % (_m.group(1), _m.group(2)))
ck("V7 记录存储（.jsonl）单独立账：本判据知道有哪些、且都不在日志名单里",
   bool(_scan2) and not (_scan2 & set(_known)), "扫到=%s" % sorted(_scan2))

shutil.rmtree(tmp, ignore_errors=True)
print("\n== 结论：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
