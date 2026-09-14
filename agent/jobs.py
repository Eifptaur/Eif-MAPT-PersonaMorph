# -*- coding: utf-8 -*-
"""一键动作的后台作业（⑦ 四选一里"一键升级适配层 / 更新本体"的执行层）。

为什么单独做一层（用户口径「弹窗按你推荐的做」＋项目现状）：
  · 「升级适配层」＝`scripts/wechat_check.py --update`（实测**非交互**，直接 pip install -U）；
  · 「更新本体」＝`dep_heal` 的安装命令（`--no-index` 离线优先、其次镜像）；
  两条都是**分钟级**的动作，绝不能塞在 HTTP 请求里跑（会把控制台卡住）⇒ 统一在这里：
    ① `start(name, cmd, timeout)` 起一个 daemon 线程跑命令，**同名只允许一个在跑**（防连点）；
    ② `status(name)` 给控制台读：running / returncode / 尾部输出 / 起止时间；
    ③ 尾部输出**截断保留**（默认最后 40 行），够控制台显示"跑到哪、成没成"，不把日志灌爆。
  铁律：本模块**只跑调用方给的命令**，自己不认识"装什么、升什么"——判断在 `dep_heal` / 版本门那边。
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time

log = logging.getLogger("persona-morph")

TAIL_LINES = 40
_jobs: dict = {}
_lock = threading.Lock()


def _empty() -> dict:
    return {"name": "", "cmd": "", "running": False, "started_at": 0.0, "finished_at": 0.0,
            "returncode": None, "output_tail": "", "err": ""}


def _tail(text: str, lines: int = TAIL_LINES) -> str:
    t = str(text or "").replace("\r\n", "\n").strip()
    return "\n".join(t.split("\n")[-int(lines):])


def status(name: str = "") -> dict:
    """读作业状态。`name` 为空 ⇒ 返回全部（控制台一次拿全）。"""
    with _lock:
        if not name:
            return {"jobs": {k: dict(v) for k, v in _jobs.items()}}
        return dict(_jobs.get(str(name)) or _empty())


def start(name: str, cmd: str, timeout: int = 900) -> dict:
    """起一个后台作业。同名已在跑 ⇒ **不再起第二个**（防连点，如实回报）。"""
    name, cmd = str(name or ""), str(cmd or "")
    if not cmd:
        return {"ok": False, "why": "没有可执行的命令"}
    with _lock:
        cur = _jobs.get(name) or _empty()
        if cur.get("running"):
            return {"ok": False, "why": "上一次「%s」还在跑（%.0f 秒了）" % (name, time.time() - float(cur.get("started_at") or 0)),
                    "job": dict(cur)}
        job = _empty()
        job.update({"name": name, "cmd": cmd, "running": True, "started_at": time.time()})
        _jobs[name] = job

    def _run():
        rc, out, err = None, "", ""
        try:
            r = subprocess.run(cmd, shell=True, timeout=int(timeout), capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            rc, out, err = r.returncode, (r.stdout or ""), (r.stderr or "")
        except Exception as e:                                     # noqa: BLE001
            rc, err = -1, "%s: %s" % (type(e).__name__, e)
        with _lock:
            j = _jobs.get(name) or _empty()
            j.update({"running": False, "finished_at": time.time(), "returncode": rc,
                      "output_tail": _tail(out + ("\n" + err if err else "")), "err": _tail(err, 6)})
            _jobs[name] = j
        log.info("后台作业 %s 结束：rc=%s", name, rc)

    try:
        threading.Thread(target=_run, daemon=True, name="job-%s" % name).start()
    except Exception as e:                                         # noqa: BLE001
        with _lock:
            j = _jobs.get(name) or _empty()
            j.update({"running": False, "returncode": -1, "err": str(e)})
            _jobs[name] = j
        return {"ok": False, "why": "作业线程起不来：%s" % e}
    return {"ok": True, "job": status(name)}


def reset(name: str = "") -> None:
    """清掉作业记录（判据用；也让控制台能"清空上一次结果"）。"""
    with _lock:
        if name:
            _jobs.pop(str(name), None)
        else:
            _jobs.clear()
