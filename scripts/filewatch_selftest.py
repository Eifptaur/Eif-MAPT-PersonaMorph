# -*- coding: utf-8 -*-
"""「本地文件监听」判据 —— **纯本地、不碰微信、不出网**。

守的是第 123 项（`DirWatcher`，M-1）的**红线与降级纪律**：
  · **红线**：监听到文件变化 ⇒ **只许发一个通知（Event.set）**，
    ⛔ **绝不自动把文件发出去**（否则等于绕过 G1–G6 的发送闸门）。
  · **降级**：监听**装不上**（非 Windows / 路径不存在 / 调用失败）⇒
    ⛔ **不许把"装不上"当成"没变化"** —— 必须 `error` 写明原因、Event 永不 set，
    调用方据此**退回轮询**；且**轮询兜底必须真的留着**（不能只剩事件一条路）。
  · **省**：真空闲时**不许**变成"每 N 秒扫一次"（那正是它要治的病）。

用法：runtime\\python\\python.exe scripts/filewatch_selftest.py
"""
import io
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


import _srcslice as _ss  # noqa: E402

FW_PATH = os.path.join(ROOT, "agent", "filewatch.py")
FW_SRC = io.open(FW_PATH, encoding="utf-8").read()
WX_SRC = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()

print("── A. 模块本身的形态（零第三方依赖 · 走系统调用）──")
ok("A1 分母守卫：拿到 `agent/filewatch.py` 正文（>1500 字符）", len(FW_SRC) > 1500, "%d 字符" % len(FW_SRC))
ok("A2 ⛔ 零第三方依赖（只 import 标准库 os/threading，ctypes 在函数内延迟导入）",
   ("from __future__" in FW_SRC) and ("import os" in FW_SRC) and ("import threading" in FW_SRC)
   and not any(k in FW_SRC for k in ("import win32", "import pywintypes", "import watchdog",
                                     "import win32file", "import inotify")), "")
ok("A3 走系统原生 `ReadDirectoryChangesW`（不是定时轮询）",
   "ReadDirectoryChangesW" in FW_SRC, "")
ok("A4 用 `FILE_FLAG_BACKUP_SEMANTICS`（目录句柄必需，缺了 CreateFileW 会失败）",
   "0x02000000" in FW_SRC, "")

print("── B. ⛔ 红线：监听到变化**只发通知**，绝不自动发送 ──")
# 反例锚：这几种"自动发送"的写法一旦出现，本判据必须红
_SEND_CALLS = ("send_text", "send_file", "send_image", "send_to_popup", "SendKeys",
               "keyboard", "click_send", "post_message", "send_voice")
ok("B1 ⛔ 模块**不调用任何发送类接口**（只有 Event.set 通知）",
   not any(k in FW_SRC for k in _SEND_CALLS),
   "命中=%s" % [k for k in _SEND_CALLS if k in FW_SRC])
ok("B2 ⛔ 模块**不碰微信**（不导入 wechat / wechat_windows / win32 窗口类）",
   not any(k in FW_SRC for k in ("from . import wechat", "import wechat",
                                 "wechat_windows", "WeChatGUI", "FindWindow")), "")
ok("B3 ⛔ 模块**不出网**（不导入 socket/requests/urllib）",
   not any(k in FW_SRC for k in ("import socket", "import requests", "import urllib")), "")
# ② 反向锚：确认上面那组关键词本身是有效的（造一句假的发送调用，必须被 B1 的判据抓到）
_FAKE = FW_SRC + "\ndef _bad():\n    send_file('x')\n"
ok("B4 ★反向锚：往源码里塞一句 `send_file(...)` ⇒ B1 的判据**确实**会变红（锚本身有效）",
   any(k in _FAKE for k in _SEND_CALLS), "")

print("── C. 降级纪律：装不上 ⇒ 如实报，不当「没变化」 ──")
try:
    from agent import filewatch as _fw
    _imp = True
except Exception as e:  # noqa: BLE001
    _imp = False
    ok("C0 能导入 `agent.filewatch`", False, "%s: %s" % (type(e).__name__, str(e)[:80]))

if _imp:
    ok("C1 有 `available()`（只用来决定“要不要退回轮询”）", callable(getattr(_fw, "available", None)), "")
    ok("C2 有 `watch(path)`（返回 Event）", callable(getattr(_fw, "watch", None)), "")

    # ① 坏路径 ⇒ error 非空 + 永不 set（**不许假装在监听**）
    _bad = _fw.watch(os.path.join(ROOT, "no_such_dir_xyz_%d" % int(time.time())))
    ok("C3 ⛔ 坏路径 ⇒ `error` 写明原因（不是静默）", bool(getattr(_bad, "error", "")),
       str(getattr(_bad, "error", ""))[:60])
    ok("C4 ⛔ 坏路径 ⇒ Event **永不 set**（调用方据此退回轮询，不许永远等不到）",
       not _bad.is_set(), "")

    # ② 真建目录 ⇒ 真写文件 ⇒ 真等到事件（实测，不是静态锚）
    import tempfile as _tf
    with _tf.TemporaryDirectory(prefix="fwjudge-") as _td:
        _ev = _fw.watch(_td)
        if getattr(_ev, "error", ""):
            ok("C5 当前环境支持目录监听（Windows）⇒ 应无 error", False, _ev.error)
        else:
            time.sleep(0.15)
            _t0 = time.time()
            io.open(os.path.join(_td, "hello.txt"), "w", encoding="utf-8").write("x")
            _got = _ev.wait(timeout=3.0)
            ok("C5 ⭐ 真写一个文件 ⇒ 监听器 3 秒内通知（实测，非静态锚）", bool(_got),
               "%.2fs" % (time.time() - _t0) if _got else "3 秒没等到")
            # ③ 无人动目录时**不该**自己 set（证明是事件驱动、不是轮询）
            _ev.clear()
            _spurious = _ev.wait(timeout=1.5)
            ok("C6 ⭐ 反向锚：**没人动目录**时不该自己 set（证明是事件驱动，不是每拍轮询）",
               not _spurious, "误报 set" if _spurious else "1.5s 内无事件 ✓")
            _stop = getattr(_ev, "_stop", None)
            if _stop is not None:
                _stop.set()

print("── D. 兜底轮询**必须留着**（监听不一定装得上）──")
W = _ss.func_src(WX_SRC, "wait_new_message")
ok("D1 分母守卫：拿到 `wait_new_message` 函数体（>800 字符）", len(W) > 800, "%d 字符" % len(W))
ok("D2 它**接了**目录监听（调 `filewatch` / `_fw.watch`）",
   ("filewatch" in W) or ("_fw.watch" in W), "")
ok("D3 ⛔ 它**仍保留兜底轻拍**（`poll_min`）—— 监听漏事件时不至于永远等不到",
   "poll_min" in W, "")
ok("D4 ★反向锚：**事件 + 标量 + 兜底**三样都在（缺任何一个这条就得红）",
   (("watch(" in W) or ("_fw.watch" in W)) and ("_db_max_local_only" in W) and ("poll_min" in W), "")
ok("D5 ⛔ 监听起不来时**如实降级**（`mode` 会写明 `listen-failed` / `unavailable` 之类）",
   ("listen-failed" in W) or ("watch-exc" in W) or ("unavailable" in W), "")

print("")
print("本地文件监听判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
