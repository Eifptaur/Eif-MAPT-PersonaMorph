#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一把跑完 `scripts/*selftest*.py`，汇总「N 通过 / M 失败」。

为什么要它：收尾口径是「71 个判据全绿」，但在此之前只能一条条手工跑 —— 那个数字**没法复核**，
而且很容易漏跑（漏跑的那条往往就是红的）。现在一条命令给出全量与失败清单。

用法：
    py -3 scripts\\run_all_selftests.py            # 全部
    py -3 scripts\\run_all_selftests.py -k console  # 只跑名字含 console 的
    py -3 scripts\\run_all_selftests.py -v          # 顺带打印每个脚本的最后一行

退出码：0 = 全绿；1 = 有失败或超时。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SELF = os.path.basename(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

_NUM = re.compile(r"(\d+)\s*(?:个)?\s*通过\s*/\s*(\d+)\s*(?:个)?\s*失败")
_ANY_FAIL = re.compile(r"(\d+)\s*(?:个)?\s*失败")
_FAIL_LINE = re.compile(r"^\s*FAIL\b", re.M)
_TRACE = re.compile(r"Traceback \(most recent call last\)")


def _parse(out: str):
    """从输出里抽 `(通过, 失败)`；抽不到返回 (None, None)。"""
    hits = _NUM.findall(out)
    if hits:
        p, f = hits[-1]
        return int(p), int(f)
    hits = _ANY_FAIL.findall(out)
    if hits:
        return None, int(hits[-1])
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="", help="只跑文件名含该子串的")
    ap.add_argument("-v", action="store_true", help="打印每个脚本的最后一行")
    ap.add_argument("--timeout", type=int, default=300, help="单个脚本超时秒数（默认 300）")
    a = ap.parse_args()

    names = sorted(n for n in os.listdir(HERE)
                   if "selftest" in n and n.endswith(".py") and n != SELF and a.k in n)
    if not names:
        print("没有匹配的 selftest")
        return 1

    bad, total_p, total_f = [], 0, 0
    t0 = time.time()
    for n in names:
        p = os.path.join(HERE, n)
        t = time.time()
        try:
            # ⛔ 2026-09-16 修（已知现象：「运行的时候极短时间内闪一个弹窗，而且经常闪」）：
            #   这里原来没给子进程加"不要窗口"。全套要拉 84 个 `python.exe`（控制台程序），
            #   父进程一旦没有可见控制台（GUI / 隐藏控制台拉起时就是这样），**每个子进程都会新建
            #   一个黑窗、跑完即关** ⇒ 就是"连续闪 84 次"。项目里早有这条约定
            #   （`agent/video_read.py:12` 原话"Windows 下 CREATE_NO_WINDOW，不许弹黑框"），
            #   生产代码也一直在用（`persona_morph.py` 的 `0x08000000`），自检脚本这一片漏了。
            _flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            r = subprocess.run([sys.executable, p], cwd=ROOT, capture_output=True,
                               creationflags=_flags,
                               timeout=a.timeout)
            out = (r.stdout or b"").decode("utf-8", "replace") + \
                  (r.stderr or b"").decode("utf-8", "replace")
            rc = r.returncode
        except subprocess.TimeoutExpired:
            out, rc = "TIMEOUT", -9
        sec = time.time() - t
        ps, fs = _parse(out)
        ok = ((rc == 0) and not fs
              and not _FAIL_LINE.search(out) and not _TRACE.search(out))
        if ps:
            total_p += ps
        total_f += (fs or 0)
        if not ok:
            bad.append((n, rc, ps, fs, out))
        line = "%-34s %-4s %s" % (n, "OK" if ok else "RED",
                                  ("%s/%s" % (ps, fs)) if ps is not None else "rc=%d" % rc)
        if a.v or not ok:
            tail = [l for l in out.strip().splitlines() if l.strip()]
            if a.v and tail:
                line += "  | " + tail[-1].strip()[:90]
        print("%s  (%4.1fs)" % (line, sec))

    print("\n" + "=" * 72)
    print("脚本 %d 个 · 用时 %.0fs · 断言合计 %d 通过 / %d 失败 · %s"
          % (len(names), time.time() - t0, total_p, total_f,
             "全绿" if not bad else "**有 %d 个脚本红**" % len(bad)))
    for n, rc, ps, fs, out in bad:
        print("\n---- RED: %s (rc=%s, %s/%s) ----" % (n, rc, ps, fs))
        tail = [l for l in out.strip().splitlines() if l.strip()][-14:]
        print("\n".join(tail))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
