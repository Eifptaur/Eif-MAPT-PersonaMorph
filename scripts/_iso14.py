# -*- coding: utf-8 -*-
"""判据隔离区（第十四轮 **V-R14-1**）——**一条判据绝不许改产品的 `data/` 与 `logs/`**。

为什么要有这个文件（这不是洁癖，是真的出过事）：
  第九轮为止，"判据卫生"只在几个已知点上做过（`update_state` / `risk_events` / `logs\\sd_local.token`），
  **从没做过全目录对账**。第十四轮审计把 `data/` 与 `logs/` 的顶层文件跑前跑后对了一遍，发现
  `risk_selftest` 每跑一次就在**产品目录**里创建 `data\\paused.flag` —— 而**每个发送链的每一步都查它**
  ⇒ **我们跑一次复核，就把用户的机器人暂停了**（审计自己有 `data/paused.flag` 的 mtime 为证）。

⚠️ 第一版修法（各判据里手抄一段 `try: from agent import control … except: pass`）**本身是坏的**：
  那些判据把隔离段写在 `sys.path.insert(0, ROOT)` **之前**（脚本模式下 `sys.path[0]` 是 `scripts\\`，
  工作目录**不在** `sys.path` 里）⇒ `from agent import control` 抛 ImportError ⇒ 被 `except: pass`
  **静默吞掉** ⇒ 隔离一次都没生效，而每个判据看上去都"加了隔离"。第十四轮的**全目录对账**当场抓到
  （单跑 `risk_selftest` 仍然改 `data/paused.flag`）。⇒ 结论：**隔离不许手抄**，收口到本文件一处，
  且必须在 `sys.path` 就绪之后才可能 import —— 见本文件头顶的 `ROOT` 注入。

用法（判据里放在最前面、`import` 任何 `agent.*` 之前）：

    import _iso14
    _iso14.all_()                     # 或按需：_iso14.control() / .wechat() / .update_state() …

**报错不静默**：单个补丁失败会被记进 `MISSED`（判据可打印它），而不是 `except: pass` 一抹了事。
`scripts\\run_all_selftests.py` 的**产品目录洁净度总闸**是它的行为判据：任一判据写脏 `data/`/`logs/`
⇒ 全套判红并点名。
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ⛔ 必须在**任何** `from agent import …` 之前（这就是第一版修法失效的根因）
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

#: ⚠️ 这个运行时是**便携版**（`runtime\python\python310._pth` 存在 ⇒ 隔离模式），`sys.path` 里只有
#: `python310.zip` + `runtime\python` + `site-packages` —— **脚本自己所在目录不在里面**（与官方安装版
#: 不同，也不受 `os.chdir` 影响）。所以判据要 `import _iso14` 必须先自己把 `scripts\` 塞进去（一行）：
#:     sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
#: 同一个原因也解释了第一版修法为什么**静默**失效：`from agent import control` 在 `sys.path.insert(0, ROOT)`
#: 之前抛 ImportError，被 `except Exception: pass` 吞掉 ⇒ 看着"加了隔离"、实际一次没生效。

ISO = tempfile.mkdtemp(prefix="pm_iso14_")
#: 哪些补丁没打上（判据可以打印它；空字典＝全打上了）
MISSED: dict = {}


def path(name: str) -> str:
    """隔离区里的一个文件路径。"""
    return os.path.join(ISO, name)


def _run(tag: str, fn) -> bool:
    try:
        fn()
        return True
    except Exception as e:                              # noqa: BLE001 —— 记下来，不静默
        MISSED[tag] = "%s: %s" % (type(e).__name__, str(e)[:120])
        return False


def logs() -> bool:
    """主程序日志目录（`persona_morph` 的 FileHandler）——**必须在 import 它之前**调。"""
    def _do():
        os.environ["PM_LOG_DIR"] = path("logs")
        os.makedirs(os.environ["PM_LOG_DIR"], exist_ok=True)
    return _run("logs", _do)


def control() -> bool:
    """暂停/停止标记（`data/paused.flag` / `data/stopped.flag`）。"""
    def _do():
        from agent import control as c
        c.set_paths(paused=path("paused.flag"), stopped=path("stopped.flag"))
    return _run("control", _do)


def wechat() -> bool:
    """判定台账 / 切会话失败台账（`data/message_ledger.jsonl`、`data/switch_fails.jsonl`）。"""
    def _do():
        from agent import wechat as w
        w._ledger_path = lambda: path("message_ledger.jsonl")
        w._switch_fails_path = lambda: path("switch_fails.jsonl")
    return _run("wechat", _do)


def update_state() -> bool:
    """更新状态快照（`data/update_state.json`）。"""
    def _do():
        from agent import update_check as u
        u._state_path = lambda: path("update_state.json")
    return _run("update_state", _do)


def window_borrow() -> bool:
    """挪窗借用记录（`data/window_borrow.json`）。"""
    def _do():
        from agent import window_borrow as wb
        wb._persist_path = lambda: path("window_borrow.json")
    return _run("window_borrow", _do)


def console_lock() -> bool:
    """控制台开窗锁（`logs/browser_opened.lock`）。

    ⚠️ 这条是**瞬时**发现的（V-R14-7 的持续采样闸）：`console_open_selftest` 的 D2 段要"真锁的行为"
    （不 mock），它自己会建锁再删掉 ⇒ 跑前跑后对账看**净变化是 0**、判据看着"干净"，而那一刻产品的
    `logs/` 里确实躺着我们的锁文件 —— 真机器人正在开窗时，这把锁会让它**判定"别人刚开过"而不开窗**。
    """
    def _do():
        from agent import util as u
        u.console_lock_path = lambda root="": path("browser_opened.lock")
    return _run("console_lock", _do)


def all_() -> str:
    """把上面全部打上（各自独立成败，失败记进 `MISSED`）。返回隔离目录。"""
    MISSED.clear()
    logs()
    control()
    wechat()
    update_state()
    window_borrow()
    console_lock()
    return ISO
