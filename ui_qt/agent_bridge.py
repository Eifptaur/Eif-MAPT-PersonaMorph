# -*- coding: utf-8 -*-
"""原型与真实后台之间的唯一桥梁。

为什么要这个文件（而不是在 shell.py 里直接拼 URL）：
  `agent/notify_ui.py` 的 `console_url()` 里那段注释已经把坑讲透了 ——
  地址来源有**权威顺序**（`logs/console.url` → 配置兜底），并且带活性检查。
  原型必须**复用同一份逻辑**，否则我们测的是"原型自己的地址拼法"，
  不是产品真实会走的路径，验证就白做了。

⚠️ 但"复用"有个前提：本文件**只读**产品代码，绝不改。
   `agent/` 下一个字都不动（用户硬约束："后端一行都不许改"）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # ui_qt → 项目根（落位自 _scratch/qt_proto，层级浅一级）


def _ensure_path() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def current_url() -> str:
    """拿控制台地址 —— 优先走产品自己的 `notify_ui.console_url()`。

    拿不到（比如产品代码正在改、import 失败）就退回读 `logs/console.url` 文件，
    再不行给配置里的默认端口。**任何一步都不抛异常** ——
    原型界面不能因为拿不到地址就崩，那本身就是我们要演示的"不许点了没反应"。
    """
    _ensure_path()
    try:
        from agent.notify_ui import console_url  # noqa: PLC0415
        u = console_url()
        if u:
            return u
    except Exception:
        pass

    # 兜底 1：地址文件
    try:
        f = ROOT / "logs" / "console.url"
        if f.exists():
            s = f.read_text(encoding="utf-8", errors="replace").strip()
            if s.startswith("http"):
                return s
    except Exception:
        pass

    # 兜底 2：配置里的端口
    port = 3210
    try:
        import json  # noqa: PLC0415
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8", errors="replace"))
        port = int((cfg.get("server") or {}).get("port") or 3210)
    except Exception:
        pass
    return "http://127.0.0.1:%d/" % port


def console_process_alive() -> tuple[bool, str]:
    """本机有没有"群相"的 Python 进程在跑。

    这是**区分两种情况的关键**，`probe_backend()` 单靠自己做不到：
      · 「服务死了」      ← 端口没人听 + **进程也没了**  ⇒ 能自愈（自己拉起来）
      · 「服务还没起来」  ← 端口没人听 + **进程还在**    ⇒ 只需等
    两种都是 `ConnectionRefused`，只有进程表能分开。
    """
    try:
        import subprocess  # noqa: PLC0415
        out = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "CommandLine"],
            capture_output=True, text=True, timeout=4,
            creationflags=0x08000000,
        ).stdout or ""
    except Exception as e:  # noqa: BLE001
        return False, "查不到进程（%s）" % type(e).__name__
    # 判据：命令行里同时出现 persona_morph 与 .py —— 避免把无关 python 进程算进来
    hits = [ln for ln in out.splitlines() if "persona_morph" in ln and ".py" in ln]
    if hits:
        return True, hits[0].strip()[:120]
    return False, ""


if __name__ == "__main__":
    u = current_url()
    print("地址：", u.split("?")[0] + ("?token=…" if "?" in u else ""))
    alive, info = console_process_alive()
    print("进程活着：", alive, ("  " + info) if info else "")
