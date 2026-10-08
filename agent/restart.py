"""单实例的"立即重启"：先结束旧实例、**等锁真的放开**，再让新实例接手。

**要解决的病**：单实例锁（`agent/single_instance.py` 的命名互斥体）保证同一时间只有一个实例，
但"我想马上换上新版/我怀疑它卡住了"这类**主动重启**以前没有可复用件 ——
旧实例还活着，新实例取锁必然失败 ⇒ 只剩两条路：要么让用户自己去点「停止机器人」，
要么内联在启动入口里写一段"条件很苛刻"的自愈（只有"跑够 90 秒 + 端口无应答"才动手）。
两条路都不够：前者把收尾留给用户，后者覆盖不到"进程健康、端口通、但我就是想换版本"。

**为什么不能"杀了就立刻启"**：进程被 `TerminateProcess` 之后，内核释放互斥体、
锁文件被清掉、端口退出 `TIME_WAIT` **都需要一点时间**。kill 完马上 `acquire()` 会撞上
"锁还在"而误判失败 ⇒ 用户看到"重启失败"，其实再等半秒就好了。
所以本模块的核心是 **有界等待**：杀掉之后**轮询到锁真的空出来**（或超时），再交给调用方去启。

**为什么 kill 目标只取"锁文件里记的 pid"**：锁文件在持有者正常退出时会被清掉，
所以里面的 pid 指向"自己人"的概率极高；相反，按进程名扫（`taskkill /IM python.exe`）
会误杀用户别的 Python 程序 —— 不可逆，坚决不做。

**边界**：本模块只负责"停 + 等"，**不负责启**（启是各入口自己的事，各自的参数/日志口径不同）。
"""
from __future__ import annotations

import logging
import os
import subprocess
import time

from . import single_instance as _si

log = logging.getLogger(__name__)

#: 杀掉之后最多等多久让锁真的空出来。实测：`TerminateProcess` 后内核立刻回收内核对象，
#: 但锁文件被"持有者的退出钩子"删除可能稍晚，端口退出等待更久 ⇒ 给 10 秒足够。
RELEASE_WAIT_S = 10.0
#: 轮询间隔。太密是白烧 CPU，太疏会拖长"重启"这个用户可感知的动作。
POLL_S = 0.25
#: 杀掉一个进程后，给它自己收尾（写日志、关句柄）的最长时间。
KILL_TIMEOUT_S = 20.0

CREATE_NO_WINDOW = 0x08000000


def holder_pid(lock_path: str) -> int:
    """当前**可能**持锁的 pid（用于 kill 目标）。取不到就返回 0。

    顺序：① `probe()` 认为有人持锁 ⇒ 用锁文件里的 pid（内核对象不给 pid，
    所以这是唯一可得的线索）；② 没人持锁 ⇒ 看旧版兜底（只写 pid 文件、没有互斥体的老实例）。
    """
    try:
        held, pid = _si.probe(lock_path=lock_path)
    except Exception:
        held, pid = (False, 0)
    if held and pid:
        return int(pid)
    try:
        return int(_si.legacy_holder(lock_path) or 0)
    except Exception:
        return 0


def is_locked(lock_path: str) -> bool:
    """**只查不占**：当前是不是有实例持锁（含旧版 pid 文件形态）。"""
    try:
        held, _pid = _si.probe(lock_path=lock_path)
    except Exception:
        held = False
    if held:
        return True
    try:
        return bool(_si.legacy_holder(lock_path))
    except Exception:
        return False


def kill_pid(pid: int) -> tuple:
    """结束单个进程，返回 `(ok, why)`。**失败要如实说为什么**（权限/不存在/超时）。"""
    try:
        pid = int(pid or 0)
    except Exception:
        return (False, "pid 非法：%r" % (pid,))
    if pid <= 0 or pid == os.getpid():
        return (False, "拒绝结束 pid=%s（不是有效目标，或就是本进程）" % pid)
    if not _si.pid_alive(pid):
        return (True, "该进程已经不在了，无需结束")
    try:
        r = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=KILL_TIMEOUT_S,
                           creationflags=CREATE_NO_WINDOW)
    except Exception as e: # noqa: BLE001
        return (False, "结束进程失败（%s）：%s" % (type(e).__name__, e))
    if r.returncode == 0:
        return (True, "已结束 pid=%s" % pid)
    _err = (r.stderr or b"").decode("utf-8", "replace").strip() or (r.stdout or b"").decode("utf-8", "replace").strip()
    # 竞态：查询时还活着、执行时刚好自己退了 ⇒ taskkill 报"找不到"也算成功（目标状态已达成）。
    if not _si.pid_alive(pid):
        return (True, "该进程在结束过程中自行退出（目标状态已达成）")
    return (False, "taskkill 退出码=%s：%s" % (r.returncode, _err[:160] or "(无输出)"))


def wait_release(lock_path: str, timeout_s: float = RELEASE_WAIT_S) -> tuple:
    """**有界等待**锁空出来，返回 `(released, elapsed_s)`。

    这是本模块的要点：kill 之后不能假设"立刻就好了" —— 必须**等到锁真空出来**再启，
    否则新实例会撞上还没释放的锁、被误判成"已有实例"。
    """
    t0 = time.time()
    while True:
        if not is_locked(lock_path):
            return (True, time.time() - t0)
        if (time.time() - t0) >= float(timeout_s):
            return (False, time.time() - t0)
        time.sleep(POLL_S)


def stop_for_restart(lock_path: str, *, watchdog_pids=None, extra_pids=None) -> dict:
    """**停**：结束当前持锁实例（连同调用方给的看门狗 pid），然后等锁放开。

    `watchdog_pids` / `extra_pids`：看门狗及其它需要一起收掉的进程（调用方知道自己记在哪）。
    返回一份**可复核**的结论（不要只看 bool）：
      `{ok, killed:[(pid,why)], failed:[(pid,why)], released:bool, waited_s:float, reason:str}`
    · `ok=True` 仅当"锁已放开"（哪怕本来就没锁 ⇒ 也算 ok，语义是"现在可以不冲突地启了"）；
    · **没杀掉但锁放开了**也算 ok（旧实例可能刚好自己退了）。
    """
    killed: list = []
    failed: list = []
    targets: list = []
    _hp = holder_pid(lock_path)
    if _hp:
        targets.append(_hp)
    for p in list(watchdog_pids or []) + list(extra_pids or []):
        try:
            _p = int(p or 0)
        except Exception:
            continue
        if _p > 0 and _p not in targets:
            targets.append(_p)
    if not is_locked(lock_path) and not targets:
        return {"ok": True, "killed": [], "failed": [], "released": True, "waited_s": 0.0,
                "reason": "启动前没有实例持锁，无需重启"}
    for _p in targets:
        _ok, _why = kill_pid(_p)
        (killed if _ok else failed).append((_p, _why))
        if not _ok:
            log.warning("结束进程未成功（pid=%s）：%s", _p, _why)
    released, waited = wait_release(lock_path)
    if not released:
        return {"ok": False, "killed": killed, "failed": failed, "released": False, "waited_s": waited,
                "reason": "已要求结束旧实例，但 %.1f 秒内锁仍未放开（可能还有别的进程持锁，"
                          "或旧实例卡在退出钩子上）——请稍后重试，或手动结束它" % waited}
    return {"ok": True, "killed": killed, "failed": failed, "released": True, "waited_s": waited,
            "reason": "已结束旧实例并确认锁放开（等了 %.1f 秒）" % waited if killed
                      else "锁已放开（旧实例自行退出）"}
