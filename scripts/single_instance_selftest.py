#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单实例锁判据（对账清单第 4 条「锁实例进程（只允许一个）」）。

不需要微信、不出网：临时目录里的锁文件 + 真子进程（跨进程才是真判据）。
判据要点：
  · 同进程二次取同名锁 **必须失败**（这是"双实例"的最小复现）
  · 跨进程：子进程持锁时父进程取不到，且能报出持有者 pid
  · **子进程被强杀（模拟崩溃）后立刻能再取**（互斥体由内核释放 ⇒ 没有残留文件问题）
  · **pid 复用不再误判**：锁文件里写一个"活着的、但不是机器人的"进程 pid，照样能启动
  · 旧版实例（只写 pid 文件、没有互斥体）仍被拦住（`legacy_holder` 判据＝那是个 Python 进程）
  · **建不起锁就 fail-closed**：非法互斥体名 ⇒ 取锁失败且 reason 讲清（不许静默裸奔）
  · 证据文件写不进去时**判据不受影响**（真正的锁是内核对象，不是那个文件）
  · 接线：机器人主程序与一键启动都真的用了这个模块
"""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import single_instance as SI  # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


TMP = tempfile.mkdtemp(prefix="silock_")
_n = [0]


def uniq():
    _n[0] += 1
    return SI.MUTEX_NAME + ".selftest%d" % _n[0]


def lockfile(tag):
    return os.path.join(TMP, "bot-%s.lock" % tag)


def spawn(code, **kw):
    p = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return p


def spawn_holder(name, path, out_path):
    """子进程：取锁 → 把结果写文件 → 睡 60 秒（父进程用文件当握手，不用管道）。"""
    code = (
        "import json, os, sys, time\n"
        "sys.path.insert(0, %r)\n" % ROOT +
        "from agent.single_instance import InstanceLock\n"
        "lk = InstanceLock(name=%r, lock_path=%r)\n" % (name, path) +
        "r = lk.acquire()\n"
        "open(%r, 'w', encoding='utf-8').write(json.dumps({'ok': bool(r), 'pid': os.getpid(), 'reason': r.reason}))\n" % out_path +
        "time.sleep(60)\n"
    )
    return spawn(code)


def wait_file(path, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.loads(f.read())
            except Exception:
                pass
        time.sleep(0.1)
    return None


def acquire_with_retry(lock, tries=30, gap=0.1):
    res = lock.acquire()
    for _ in range(tries):
        if res.ok:
            return res
        time.sleep(gap)
        res = lock.acquire()
    return res


print("== 单实例锁 ==")
print("[-] 平台=%s 互斥体名=%s" % (os.name, SI.MUTEX_NAME))

# ── C1/C2：同进程内"双实例"最小复现 ──────────────────────────────
name1, path1 = uniq(), lockfile("c1")
l1 = SI.InstanceLock(name=name1, lock_path=path1)
r1 = l1.acquire()
ok("C1 首次取锁成功", r1.ok, repr(r1))
ok("C1 取锁后 acquired=True", l1.acquired)

l1b = SI.InstanceLock(name=name1, lock_path=path1)
r1b = l1b.acquire()
ok("C2 同进程二次取同名锁被拦", (not r1b.ok) and bool(r1b.reason), repr(r1b))

# ── C3：证据文件仍是纯 pid（旧读取方 int() 能解析）───────────────
try:
    with open(path1, "r", encoding="utf-8") as f:
        txt = f.read().strip()
    ok("C3 锁文件内容＝纯 pid 数字（兼容旧读取方）", txt == str(os.getpid()), txt)
except Exception as e:
    ok("C3 锁文件内容＝纯 pid 数字（兼容旧读取方）", False, str(e))

# ── C4/C5：跨进程才是真自检 ───────────────────────────────────
name2, path2, out2 = uniq(), lockfile("c2"), os.path.join(TMP, "child2.json")
child = spawn_holder(name2, path2, out2)
info = wait_file(out2)
ok("C4 子进程持锁成功", bool(info) and info.get("ok") is True, str(info))
child_pid = (info or {}).get("pid")
r2 = SI.InstanceLock(name=name2, lock_path=path2).acquire()
ok("C4 子进程持锁时父进程取不到", not r2.ok, repr(r2))
ok("C4 报出的持有者 pid ＝ 子进程 pid", r2.holder_pid == child_pid, "%s vs %s" % (r2.holder_pid, child_pid))
held, pid_probe = SI.probe(name=name2, lock_path=path2)
ok("C5 probe 在有人持锁时＝(True, 子pid)", held and pid_probe == child_pid, "%s/%s" % (held, pid_probe))
held_free, _ = SI.probe(name=uniq(), lock_path=lockfile("nobody"))
ok("C5 probe 在无人持锁时＝(False, 0)", (not held_free), str(held_free))

# ── C6：崩溃（强杀）后立刻能再取同名锁（互斥体由内核释放）──────
child.kill()
try:
    child.wait(timeout=10)
except Exception:
    pass
r3 = acquire_with_retry(SI.InstanceLock(name=name2, lock_path=path2))
ok("C6 持锁进程被强杀后，同名锁立刻可取（无残留文件问题）", r3.ok, repr(r3))

# ── C7：pid 复用不再误判（旧实现的老毛病）──────────────────────
# 造一个"活着的、但不是机器人的"进程（cmd.exe），把它的 pid 写进锁文件
dummy = subprocess.Popen([os.environ.get("COMSPEC", "cmd.exe"), "/c", "ping", "-n", "60", "127.0.0.1"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
time.sleep(1.0)
dummy_pid = dummy.pid
name4, path4 = uniq(), lockfile("c4")
with open(path4, "w", encoding="utf-8") as f:
    f.write(str(dummy_pid))
ok("C7 活的非机器人进程 pid 不会被当成机器人",
   SI.legacy_holder(path4) == 0 and SI.looks_like_bot(dummy_pid) is False,
   "pid=%s name=%s" % (dummy_pid, SI.pid_image_name(dummy_pid)))
l4 = SI.InstanceLock(name=name4, lock_path=path4)
r4 = l4.acquire()
ok("C7 锁文件里是无关进程时照样能取到锁", r4.ok, repr(r4))
ok("C7 取锁后锁文件改写成自己的 pid", SI._read_pid(path4) == os.getpid())
held4, _ = SI.probe(name=name4, lock_path=path4)
ok("C7 此时 probe＝有人持锁", bool(held4))
l4.release()
held4b, _ = SI.probe(name=name4, lock_path=path4)
ok("C7 release 之后 probe＝没人持锁", not held4b)
ok("C7 release 幂等（再调一次不抛）", (l4.release() is None) and (l4.release() is None))

# ── C8：旧版实例仍被拦住（兜底自检＝那是个 Python 进程）────────
pysleep = spawn("import time\ntime.sleep(60)\n")
path5 = lockfile("c5")
with open(path5, "w", encoding="utf-8") as f:
    f.write(str(pysleep.pid))
ok("C8 活的 Python 进程被当成旧版实例（拦住）", SI.legacy_holder(path5) == pysleep.pid,
   "pid=%s name=%s" % (pysleep.pid, SI.pid_image_name(pysleep.pid)))
ok("C8 死进程的 pid 不算持有者", SI.legacy_holder(os.path.join(TMP, "nope.lock")) == 0)

# ── C9：fail-closed —— 建不起锁就必须拦下自己 ──────────────────
bad = SI.InstanceLock(name="PersonaMorph\\bad-name", lock_path=lockfile("c6"))
rbad = bad.acquire()
ok("C9 非法互斥体名 ⇒ 取锁失败（fail-closed，不静默裸奔）",
   (not rbad.ok) and bool(rbad.reason) and (not bad.acquired), repr(rbad))

# ── C10：证据文件写不进去时，自检不受影响 ──────────────────────
blocker = os.path.join(TMP, "not-a-dir")
with open(blocker, "w", encoding="utf-8") as f:
    f.write("x")
name7 = uniq()
l7 = SI.InstanceLock(name=name7, lock_path=os.path.join(blocker, "bot.lock"))
r7 = l7.acquire()
ok("C10 锁文件写不进去时仍取到锁（判据在内核对象上）", r7.ok, repr(r7))
ok("C10 并如实给出 note（不静默）", bool(r7.note or l7.note), (r7.note or l7.note)[:80])
r7b = SI.InstanceLock(name=name7, lock_path=os.path.join(blocker, "bot.lock")).acquire()
ok("C10 此时第二次取锁照样被拦（证明判据不依赖文件）", not r7b.ok, repr(r7b))

l1.release()
l1c = SI.InstanceLock(name=name1, lock_path=path1)
ok("C11 release 后可再次取锁", l1c.acquire().ok)
l1c.release()

for p in (child, dummy, pysleep):
    try:
        p.kill()
    except Exception:
        pass

# ── C12：接线断言（改完不接线＝白做）──────────────────────────
def src(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


_wx = src(os.path.join("scripts", "persona_morph.py"))
_os_ = src(os.path.join("scripts", "onestart.py"))
ok("C12 主程序用 InstanceLock 取锁", ("single_instance" in _wx) and ("InstanceLock" in _wx))
ok("C12 主程序保留冲突提示原文（自检项依赖）",
   ("已有 Persona Morph 实例在运行" in _wx) and ("bot.lock" in _wx))
ok("C12 主程序在启动早段注册 release", ("atexit" in _wx) and (".release" in _wx))
ok("C12 一键启动用 probe 只查不占（旧版兜底 legacy_holder）",
   ("single_instance" in _os_) and ("probe" in _os_) and ("legacy_holder" in _os_))
ok("C12 停止脚本仍按 bot.pid 收口（没被这次改动带偏）",
   "bot.pid" in src(os.path.join("scripts", "stop_bot.py")))
ok("C12 互斥体名与证据文件名是模块常量（两处共用同一份）",
   (SI.MUTEX_NAME == r"Local\PersonaMorphBot.singleinstance") and (SI.LOCK_FILE == os.path.join("data", "bot.lock")))

print("\n单实例锁判据：通过 %d / 失败 %d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
