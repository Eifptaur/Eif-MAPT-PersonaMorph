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
import concurrent.futures as _cf
import os
import re
import subprocess
import sys
import threading
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
# ⛔ 2026-09-21 加（第六轮 **V-R6-28**）：**"被提前吃掉"必须判红**。
#   原来只看 rc / FAIL 行 / Traceback ⇒ 判据若在中间 `os._exit(0)`（本项目的
#   `self_update_selftest` 就因为走了真更新链而自杀过），rc=0、无 FAIL、无 Traceback ⇒ **判绿**，
#   其后二十多条断言从没执行也没人知道（含一条真红）。⇒ 现在要求输出里必须有**汇总行**
#   （形如「N 通过 / M 失败」「131/131 通过」「结果：81 通过 / 0 失败」…）。
_SUMMARY = re.compile(
    r"\d+\s*/\s*\d+\s*通过"                                  # 131/131 通过
    r"|\d+\s*(?:个)?\s*通过\s*/\s*\d+\s*(?:个)?\s*失败"        # 44 通过 / 0 失败
    r"|通过\s*\d+\s*/\s*失败\s*\d+"                           # 通过 21 / 失败 0
    r"|\d+\s*PASS\s*/\s*\d+\s*FAIL"                           # 44 PASS / 0 FAIL
    r"|汇总|结论|结果[:：]")


def _has_summary(out: str) -> bool:
    """输出里有没有"跑完了"的汇总行（没有 ⇒ 这个脚本可能被中途吃掉）。"""
    return bool(_SUMMARY.search(out or ""))


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


_HEAVY = re.compile(r"(console_|cursor_|whale|voice_models|local_models|restart_button|decide_link|_ui_selftest)")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
_BELOW_NORMAL = 0x00004000 if os.name == "nt" else 0     # 别跟用户抢 CPU：全套默认低优先级


def _run_one(name: str, timeout: int, gate: threading.Semaphore) -> dict:
    """跑一条判据。**会开窗/开服务的那些先过 `gate`（同时只允许一条）**，其余并发跑。"""
    p = os.path.join(HERE, name)
    heavy = bool(_HEAVY.search(name))
    _hold = gate if heavy else None
    if _hold:
        _hold.acquire()
    t = time.time()                                   # 计时**从真正开跑算起**（不含等闸，读数才诚实）
    try:
        try:
            r = subprocess.run([sys.executable, p], cwd=ROOT, capture_output=True,
                               creationflags=_NO_WINDOW | _BELOW_NORMAL, timeout=timeout)
            out = (r.stdout or b"").decode("utf-8", "replace") + \
                  (r.stderr or b"").decode("utf-8", "replace")
            rc = r.returncode
        except subprocess.TimeoutExpired:
            out, rc = "TIMEOUT", -9
    finally:
        if _hold:
            _hold.release()
    sec = time.time() - t
    ps, fs = _parse(out)
    _summed = _has_summary(out)
    ok = ((rc == 0) and not fs and not _FAIL_LINE.search(out) and not _TRACE.search(out)
          and _summed)
    return {"name": name, "out": out, "rc": rc, "sec": sec, "ps": ps, "fs": fs, "ok": ok,
            "summed": _summed, "heavy": heavy}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="", help="只跑文件名含该子串的")
    ap.add_argument("-v", action="store_true", help="打印每个脚本的最后一行")
    ap.add_argument("--timeout", type=int, default=300, help="单个脚本超时秒数（默认 300）")
    # ⚠️ 2026-09-18 加（作者原话：「你跑检验为什么还能把我这么一台电脑给跑卡了？…天天空耗我十几分钟」）：
    #   原来 110 条**串行**跑 ⇒ 每条都要重新起一个 Python 进程 + 重新 import（PIL/opencv/win32/wechatato），
    #   单条自测本身只要 0.1~4 秒，实测总时长却被启动开销推到 ~280 秒。现在：
    #     ① 默认 `-j`（核数/4，封顶 6）并发；② **全部低优先级**（BELOW_NORMAL，不与作者抢 CPU）；
    #     ③ 会**真开窗/起服务**的判据（console_*/cursor_*/whale/_ui_selftest 等）过一把"同时只跑一条"的闸
    #        —— 它们是"屏幕闪窗 + 卡机"的来源（每条都真起 WebView2/HTTP 服务）。
    _cores = os.cpu_count() or 4
    ap.add_argument("-j", "--jobs", type=int, default=min(6, max(2, _cores // 4)),
                    help="并发数（默认 核数/4，封顶 6；开窗类判据始终串行）")
    a = ap.parse_args()

    names = sorted(n for n in os.listdir(HERE)
                   if "selftest" in n and n.endswith(".py") and n != SELF and a.k in n)
    if not names:
        print("没有匹配的 selftest")
        return 1

    bad, total_p, total_f = [], 0, 0
    t0 = time.time()
    gate = threading.Semaphore(2)                    # 开窗/开服务类：最多两条同时（既快又不刷屏）
    results = {}
    with _cf.ThreadPoolExecutor(max_workers=max(1, int(a.jobs))) as ex:
        futs = {ex.submit(_run_one, n, a.timeout, gate): n for n in names}
        for fu in _cf.as_completed(futs):
            r = fu.result()
            results[r["name"]] = r
    for n in names:                                   # 按文件名顺序输出（稳定、好对照）
        r = results[n]
        if r["ps"]:
            total_p += r["ps"]
        total_f += (r["fs"] or 0)
        if not r["ok"]:
            bad.append((n, r["rc"], r["ps"], r["fs"], r["out"]))
        line = "%-34s %-4s %s" % (n, "OK" if r["ok"] else "RED",
                                  ("%s/%s" % (r["ps"], r["fs"])) if r["ps"] is not None else "rc=%d" % r["rc"])
        if not r.get("summed", True):
            line += "  ⚠️无汇总行（可能被中途吃掉）"
        if a.v or not r["ok"]:
            tail = [l for l in r["out"].strip().splitlines() if l.strip()]
            if a.v and tail:
                line += "  | " + tail[-1].strip()[:90]
        print("%s  (%4.1fs%s)" % (line, r["sec"], "·开窗" if r["heavy"] else ""))

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
