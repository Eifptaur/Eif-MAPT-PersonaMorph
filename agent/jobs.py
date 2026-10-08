# -*- coding: utf-8 -*-
"""一键动作的后台作业（⑦ 四选一里"一键升级适配层 / 更新本体"的执行层）。

为什么单独做一层：
  · 「升级适配层」＝`scripts/wechat_check.py --update`（实测**非交互**，直接 pip install -U）；
  · 「更新本体」＝`dep_heal` 的安装命令（`--no-index` 离线优先、其次镜像）；
  两条都是**分钟级**的动作，绝不能塞在 HTTP 请求里跑（会把控制台卡住）⇒ 统一在这里：
    ① `start(name, cmd, timeout)` 起一个 daemon 线程跑命令，**同名只允许一个在跑**（防连点）；
    ② `status(name)` 给控制台读：running / returncode / 尾部输出 / 起止时间；
    ③ `cancel(name)` 叫停一个正在跑的作业（按 pid 杀**进程树**）；
    ④ 尾部输出**截断保留**（默认最后 40 行），够控制台显示"跑到哪、成没成"，不把日志灌爆。
  铁律：本模块**只跑调用方给的命令**，自己不认识"装什么、升什么"——判断在 `dep_heal` / 版本门那边。

**终态有三档**（P0-5）：`成功` / `失败` / **`已取消`**。
"取消"**不是失败** —— 把它并进 `returncode != 0` 那一档，会让成功率统计被污染，
也会让下次误判"上次失败了要重试"（用户明明是自己停的）。
`start` 的"同名只允许一个在跑"闸门把三档**一律视作"已结束"**（含取消），
所以用户取消后能立刻重开一次。
"""
from __future__ import annotations

import locale
import logging
import subprocess
import threading
import time

from . import task_owner as _owner

log = logging.getLogger("persona-morph")

TAIL_LINES = 40
_jobs: dict = {}
_lock = threading.Lock()


def _decode(b) -> str:
    """把子进程输出字节解成字符串（**不许硬编码 UTF-8**）。

    ⛔ `start()` 起的是 `shell=True`（＝`cmd.exe`），它按**系统 ANSI 代码页**
    （中文机器＝GBK/cp936）输出 ⇒ 原来那句 `encoding="utf-8"` 会让所有中文变成乱码
    （`decide_link_selftest` 里"保留尾部输出"那条就是这么红的）。
    🔴 兜底编码**不许用 `locale.getpreferredencoding()`** —— Python 开了 UTF-8 模式
    （`PYTHONUTF8=1` / `-X utf8`）时它返回的就是 utf-8，于是"utf-8 解不出来 ⇒ 再拿 utf-8 解一遍"，
    中文照样乱码。⇒ 兜底改成 Windows 的 **`mbcs`（真 ANSI 代码页，与 Python 模式无关）**。
    ⇒ 先试 UTF-8，失败退回系统 ANSI 代码页，最后 replace 兜底（绝不抛）。
    """
    if isinstance(b, str):
        return b
    data = b or b""
    for enc in ("utf-8", "mbcs", locale.getpreferredencoding(False) or "gbk", "gbk"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def _empty() -> dict:
    return {"name": "", "cmd": "", "running": False, "started_at": 0.0, "finished_at": 0.0,
            "returncode": None, "output_tail": "", "err": "",
            # ★ P0-5：**取消是第三种终态**，与"成功/失败"并列。
            #   `canceled=True` 表示"这次跑是被用户主动叫停的"，它**不是失败**：
            #   若把它并进 `rc != 0` 那一档，成功率统计会被污染，下次还会误判
            #   "上次失败了要重试"（用户明明是自己停的）。
            "canceled": False,
            # 取消**本身**也可能失败（进程已退出 / 杀不动 / 没有 pid）⇒ 必须如实报出来，
            # 不许 fire-and-forget（见 `cancel()` 的返回契约）。
            "cancel_error": "",
            #: 子进程 pid。取消要按 pid 杀**进程树**（`shell=True` 起的是 cmd.exe，
            #: 只杀它本身会留下它派生的真活儿）。
            "pid": None,
            #: 取消请求发出但子进程还没回写终态 ⇒ 中间态。界面据此显示"正在取消…"，
            #: 而不是立刻显示"已取消"（那会是假的 —— 进程可能还在跑）。
            "canceling": False}


def _tail(text: str, lines: int = TAIL_LINES) -> str:
    t = str(text or "").replace("\r\n", "\n").strip()
    return "\n".join(t.split("\n")[-int(lines):])


def status(name: str = "") -> dict:
    """读作业状态。`name` 为空 ⇒ 返回全部（控制台一次拿全）。"""
    with _lock:
        if not name:
            return {"jobs": {k: dict(v) for k, v in _jobs.items()}}
        return dict(_jobs.get(str(name)) or _empty())


def cancel(name: str) -> dict:
    """★ P0-5：**取消一个正在跑的作业**。返回 `{"ok": bool, "why": str, "job": dict}`。

    **为什么取消本身也必须能失败、且必须报出来**：
    取消看着是"什么都不做"，其实要真去杀进程树。杀不动（权限/句柄）或进程早退出了，
    若静默当成功 ⇒ 界面显示"已取消"而**进程还在后台跑**（用户以为停了，实际没停，
    下一次"同名只允许一个在跑"还会被这个幽灵挡住）⇒ 比不提供取消更糟。

    **三段式语义**（与 `_run()` 的终态回写配合）：
      ① 没有这个作业 / 它已不在跑 ⇒ `ok=False` + 说清是哪一种（**不是**成功）；
      ② 发出杀死请求成功 ⇒ 置 `canceling=True` 并回报 `ok=True`，**终态由子进程侧回写**
         （`running=False, canceled=True`）—— 不在这里抢着写终态，否则会出现
         "界面已说取消、子进程随后又把 rc=0 写回来"的互相覆盖；
      ③ 连杀死请求都发不出去 ⇒ `ok=False` + 把底层错误带出来。
    """
    _n = str(name or "")
    with _lock:
        j = _jobs.get(_n)
        if not j:
            return {"ok": False, "why": "没有这个作业：%s" % _n}
        if not j.get("running"):
            # 已经跑完了（成功/失败/已被取消）⇒ 如实说"它已经结束了"，不算取消成功
            return {"ok": False, "why": "「%s」已经结束了（%s）" % (_n, _end_kind(j)), "job": dict(j)}
        _pid = j.get("pid")
        if not _pid:
            return {"ok": False, "why": "拿不到「%s」的进程号 ⇒ 取消不了（进程可能刚结束）" % _n,
                    "job": dict(j)}
        j["canceling"] = True
        _jobs[_n] = j
    _ok, _why = _kill_tree(int(_pid))
    if not _ok:
        # 杀不动 ⇒ 撤回"正在取消"标记，如实回报（界面不该停在"正在取消…"）
        with _lock:
            j2 = _jobs.get(_n) or _empty()
            j2["canceling"] = False
            j2["cancel_error"] = _why
            _jobs[_n] = j2
        return {"ok": False, "why": _why, "job": dict(j2)}
    log.info("已发出取消请求：作业 %s（pid=%s）", _n, _pid)
    return {"ok": True, "why": "", "job": status(_n)}


def _end_kind(j: dict) -> str:
    """给一个作业的终态起个人话名字（`canceled` 是独立的一档，不是"失败"）。"""
    if j.get("running"):
        return "进行中" if not j.get("canceling") else "正在取消"
    if j.get("canceled"):
        return "已取消"
    if j.get("returncode") in (0,):
        return "成功"
    return "失败"


def _kill_tree(pid: int) -> tuple:
    """按 pid 杀**整棵进程树**（Windows）。

    为什么必须杀树：`cmd.exe` 被 `shell=True` 起出来，它自己只是外壳，
    真正的活儿（`pip` / `python`）是它的子进程。只杀外壳 ⇒ 子进程变成孤儿继续跑。
    `/T` = 连带子进程，`/F` = 强制。返回 `(ok, why)`；**失败必须带原因**，不静默。
    """
    try:
        import subprocess as _sp
        r = _sp.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                    capture_output=True, creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0:
            return True, ""
        _msg = _decode(r.stderr) or _decode(r.stdout) or ("taskkill rc=%s" % r.returncode)
        return False, "结束进程失败：%s" % str(_msg).strip()[:120]
    except Exception as e:  # noqa: BLE001
        return False, "结束进程异常：%s: %s" % (type(e).__name__, e)


def start(name: str, cmd: str, timeout: int = 900) -> dict:
    """起一个后台作业。同名已在跑 ⇒ **不再起第二个**（防连点，如实回报）。"""
    name, cmd = str(name or ""), str(cmd or "")
    if not cmd:
        return {"ok": False, "why": "没有可执行的命令"}
    with _lock:
        cur = _jobs.get(name) or _empty()
        if cur.get("running"):
            # ★ `canceling` 中间态**同样挡住**：取消请求已发出、进程还没退，
            #   这时放行新作业 ⇒ 新旧两个一起干（同一个目标被改两遍）。
            _tail_why = "（正在取消中）" if cur.get("canceling") else ""
            return {"ok": False, "why": "上一次「%s」还在跑（%.0f 秒了）%s"
                    % (name, time.time() - float(cur.get("started_at") or 0), _tail_why),
                    "job": dict(cur)}
        job = _empty()
        job.update({"name": name, "cmd": cmd, "running": True, "started_at": time.time()})
        _jobs[name] = job
    # ★ P1-4 任务所有权：本次运行**认领**这个名字（拿令牌）。
    #   之后 `_run` 写终态前先核对"我还是不是这个名字当前的主人" —— 若期间又起了新的同名作业
    #   （闸门正常会挡住，但"取消→立刻重开"这类窗口期挡不住全部竞态），旧线程**不许**把
    #   自己的结果写回新作业的槽位（否则新作业还在跑，界面却显示旧结果/被旧的收尾清掉）。
    _own = _owner.claim(name)

    def _run():
        rc, out, err = None, "", ""
        _child = None
        try:
            # 按字节读、再自适应解码（见 `_decode` 注释：`shell=True` 走 cmd.exe，输出是 ANSI）
            # `creationflags`：不给"不要窗口"的话，控制台里点一次作业就会闪一个黑窗
            # ⛔ 用 `Popen` 而不是 `run`：要拿到 **pid** 才能取消（`run` 只在结束时才给你结果，
            #    中途没有句柄 ⇒ 想停也停不了）。
            _child = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            with _lock:
                if not _owner.is_owner(name, _own):
                    # 已经换主人了（这个进程是"前一代"）⇒ 别把自己的 pid 写进新作业的槽位
                    _stale = True
                else:
                    _stale = False
                    _j = _jobs.get(name) or _empty()
                    _j["pid"] = _child.pid
                    _jobs[name] = _j
            if _stale:
                # 前一代线程：把自己的子进程收掉（别留孤儿），然后**静默退出**（不写状态）
                try:
                    _kill_tree(_child.pid)
                except Exception:  # noqa: BLE001
                    pass
                log.warning("后台作业 %s 的旧线程（前一代）已让位 ⇒ 不再写状态", name)
                return
            try:
                _o, _e = _child.communicate(timeout=int(timeout))
            except subprocess.TimeoutExpired:
                # 超时 ⇒ 按进程树杀掉（与用户点"取消"同一条路），并如实标注
                _kill_tree(_child.pid)
                _o, _e = _child.communicate()
                err = "作业超时（%s 秒）已被结束" % timeout
            rc, out, err = _child.returncode, _decode(_o), (_decode(_e) or err)
        except Exception as e: # noqa: BLE001
            rc, err = -1, "%s: %s" % (type(e).__name__, e)
        with _lock:
            # ★ P1-4：**只有当前主人**才写终态。旧主人走到这里 ⇒ 直接放弃（它的结果属于上一代）。
            if not _owner.is_owner(name, _own):
                log.warning("后台作业 %s 的旧线程（前一代）结束时已不是主人 ⇒ 丢弃它的结果", name)
                return
            j = _jobs.get(name) or _empty()
            # ★ 终态口径（P0-5）：`canceled` 是**独立的一档**，与成功/失败并列。
            #   `canceling` 为真 ⇒ 这次结束是"用户点过取消"造成的 ⇒ 记 canceled，**不记失败**。
            #   为什么不在 `cancel()` 里直接写终态：那样会出现"界面已说取消、子进程随后
            #   又把 rc=0 写回来"的互相覆盖。终态**只有一个作者** = 这里。
            _was_cancel = bool(j.get("canceling"))
            j.update({"running": False, "finished_at": time.time(), "returncode": rc,
                      "output_tail": _tail(out + ("\n" + err if err else "")), "err": _tail(err, 6),
                      "pid": None, "canceled": _was_cancel, "canceling": False})
            _jobs[name] = j
            # 主人自己释放槽位（非主人释放会误删新主人的槽位 —— `release` 内已挡住）
            _owner.release(name, _own)
        log.info("后台作业 %s 结束：rc=%s%s", name, rc, "（已取消）" if _was_cancel else "")

    try:
        threading.Thread(target=_run, daemon=True, name="job-%s" % name).start()
    except Exception as e: # noqa: BLE001
        with _lock:
            j = _jobs.get(name) or _empty()
            j.update({"running": False, "returncode": -1, "err": str(e)})
            _jobs[name] = j
        return {"ok": False, "why": "作业线程起不来：%s" % e}
    return {"ok": True, "job": status(name)}


def reset(name: str = "") -> None:
    """清掉作业记录（判据用；也让控制台能"清空上一次结果"）。

    ⚠️ 同时清掉所有权槽位：记录都没了、槽位却还挂在旧主人名下 ⇒ 那个旧线程结束时
    `is_owner` 仍为真、会把终态写回一个"已重置"的记录（看起来"清空了自己又冒出来"）。
    """
    with _lock:
        if name:
            _jobs.pop(str(name), None)
            _owner.release(str(name), _owner.current(str(name)))
        else:
            _jobs.clear()
            _owner.reset()
