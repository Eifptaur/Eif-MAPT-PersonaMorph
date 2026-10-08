# -*- coding: utf-8 -*-
"""目录监听：**让系统在文件变化时通知我们**（替代「每隔几秒去看一眼」的定时轮询）。

=====================================================================
为什么要有它
=====================================================================
现在的做法是**定时轮询**：每几秒扫一次目录/文件 mtime。缺点两条：
  ① **空闲时也白耗 CPU**（哪怕什么都没发生）；
  ② **有延迟**（变化发生在两次轮询之间 ⇒ 最多晚一个轮询周期才被发现）。
Windows 自带 `ReadDirectoryChangesW`：调用后**阻塞在内核里等**，有变化才返回 ⇒ 空闲时几乎零开销。

⛔ **降级纪律**（照抄可靠来源的做法）：监听**失败一律如实退回轮询**，不许把「监听装不上」
   当成「没变化」——那会让上层**永远等不到**。所以本模块只做「有没有变」的**通知**，
   真正的"变了之后干什么"仍由调用方决定。

用法（两行）：
    ev = watch(path)              # 起一个后台线程，变化时 ev.set()
    while True:
        ev.wait(timeout=5.0)      # 等到变化或超时（超时也能顺便兜底轮询）
        ev.clear()
        do_something()
"""
from __future__ import annotations

import os
import threading

#: 关心的变化类型（文件名/最后写入/大小）
FILE_NOTIFY_CHANGE_FILE_NAME = 0x01
FILE_NOTIFY_CHANGE_DIR_NAME = 0x02
FILE_NOTIFY_CHANGE_LAST_WRITE = 0x10
FILE_NOTIFY_CHANGE_SIZE = 0x08
_DEFAULT_FLAGS = (FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_LAST_WRITE
                  | FILE_NOTIFY_CHANGE_SIZE)

#: 缓冲区大小：不够大会在"变化很密集"时丢事件，给 64KB 足够
_BUF = 65536


def available() -> bool:
    """当前平台能不能用目录监听（Windows 才行）。**只能用来决定"要不要退回轮询"**。"""
    return os.name == "nt"


def watch(path: str, flags: int = _DEFAULT_FLAGS, stop: threading.Event | None = None) -> threading.Event:
    """监听 `path`（目录），**有变化就 `set()` 返回的那个 Event**。

    `stop` 给了就由它来控制退出（`stop.set()` ⇒ 监听线程结束）；
    不给就创建一个、挂在返回的 Event 上（属性 `_stop`），由调用方在不需要时 `set()` 掉。

    ⛔ 监听起不来（非 Windows / 路径不存在 / 调用失败）⇒ **返回一个"永不 set"的 Event**，
      并在 `ev.error` 上写明原因 ⇒ **调用方据此退回轮询**（不许把"装不上"当"没变化"）。
    """
    ev = threading.Event()
    if stop is None:
        stop = threading.Event()
    ev._stop = stop               # 方便调用方收尾
    ev.error = ""                 # 空 = 监听正常；非空 = 为什么没监听成

    if not available():
        ev.error = "非 Windows 平台，不支持目录监听"
        return ev
    path = os.path.abspath(str(path or ""))
    if not os.path.isdir(path):
        ev.error = "目录不存在：%s" % path
        return ev

    def _loop() -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateFileW(path, 0x0001,          # FILE_LIST_DIRECTORY
                                     0x01 | 0x02 | 0x04,     # 共享读/写/删
                                     None, 3, 0x02000000, None)  # OPEN_EXISTING + BACKUP_SEMANTICS
        if handle == wintypes.HANDLE(-1).value or handle in (0, None):
            ev.error = "CreateFileW 失败（err=%s）" % ctypes.get_last_error()
            return
        buf = ctypes.create_string_buffer(_BUF)
        got = wintypes.DWORD(0)
        try:
            while not stop.is_set():
                ok = kernel32.ReadDirectoryChangesW(
                    wintypes.HANDLE(handle), buf, _BUF, True, flags,
                    ctypes.byref(got), None, None)
                if not ok:
                    ev.error = "ReadDirectoryChangesW 失败（err=%s）" % ctypes.get_last_error()
                    return
                ev.set()          # 有变化 ⇒ 通知（由调用方 clear）
        except Exception as e: # noqa: BLE001
            ev.error = "%s: %s" % (type(e).__name__, str(e)[:60])
        finally:
            try:
                kernel32.CloseHandle(wintypes.HANDLE(handle))
            except Exception: # noqa: BLE001
                pass

    threading.Thread(target=_loop, name="dirwatch", daemon=True).start()
    return ev
