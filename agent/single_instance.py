# -*- coding: utf-8 -*-
"""单实例锁：同一时间只允许一个机器人实例在跑（对账清单第 4 条）。

**为什么不用旧实现**（"只写一个 `data\\bot.lock` 存 pid，再 OpenProcess 猜死活"）：

1. **TOCTOU**：两次启动同时发生，"检查→写文件"之间双方都能通过 ⇒ 还是双实例；
2. **pid 复用**：死掉的机器人 pid 被系统分给了别的进程 ⇒ 旧实现判"还活着" ⇒ 机器人**再也起不来**；
3. **崩溃残留**：文件留在那儿只能靠猜（旧实现 `CloseHandle(_lpid)` 传的是 pid 不是句柄，本身也是错的）；
4. **静默失锁**：旧实现整段 `try/except pass` ⇒ 一旦出错就**悄悄没有锁**，谁都不知道。

**现在的判据**＝Windows **命名互斥体**（内核对象）：进程一死由内核自动释放，既没有残留文件问题，
也没有 pid 复用问题；POSIX 用 `flock`（同样随进程结束自动释放）。
`data\\bot.lock` 降级为"给人看的证据 + 旧读取方兼容"（内容仍是**纯 pid 数字**）。

规矩：拿到锁才算"在跑"；**建不起锁就 fail-closed 拒启动**（不许悄悄裸奔）；
旧版实例（只写 pid 文件、没有互斥体）用 `legacy_holder()` 兜住，判据是"那是个 Python 进程"。
"""
from __future__ import annotations

import ctypes
import os

IS_WIN = os.name == "nt"

#: 互斥体名。用 Local\ 命名空间（同一登录会话内唯一，不需要额外权限）。
MUTEX_NAME = r"Local\PersonaMorphBot.singleinstance"
#: 证据文件（相对仓库根）：内容＝持有者 pid（纯数字）。
LOCK_FILE = os.path.join("data", "bot.lock")

ERROR_ALREADY_EXISTS = 183

_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259

if IS_WIN:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateMutexW.restype = ctypes.c_void_p
    _k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    _k32.OpenMutexW.restype = ctypes.c_void_p
    _k32.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    _k32.CloseHandle.restype = ctypes.c_int
    _k32.CloseHandle.argtypes = [ctypes.c_void_p]
    _k32.OpenProcess.restype = ctypes.c_void_p
    _k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    _k32.GetExitCodeProcess.restype = ctypes.c_int
    _k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    _k32.QueryFullProcessImageNameW.restype = ctypes.c_int
    _k32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
else:  # pragma: no cover - 本机是 Windows，非 Windows 分支按文档口径实现、不声称实测过
    _k32 = None


# ────────────────────────────── 进程查询（只读） ──────────────────────────────

def pid_alive(pid: int) -> bool:
    """这个 pid 是不是一个**还活着**的进程（不用 psutil，零依赖）。"""
    try:
        pid = int(pid or 0)
    except Exception:
        return False
    if pid <= 0:
        return False
    if IS_WIN:
        h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
        if not h:
            return False
        try:
            code = ctypes.c_uint32(0)
            okq = _k32.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(okq) and code.value == _STILL_ACTIVE
        finally:
            _k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def pid_image_name(pid: int) -> str:
    """进程可执行文件名（拿不到就返回空串）。"""
    if not IS_WIN:
        return ""
    try:
        pid = int(pid or 0)
    except Exception:
        return ""
    if pid <= 0:
        return ""
    h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not h:
        return ""
    try:
        size = ctypes.c_uint32(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value or "")
        return ""
    except Exception:
        return ""
    finally:
        _k32.CloseHandle(h)


#: 旧版兼容用的进程名特征（机器人跑的是 Python）。
BOT_EXE_HINTS = ("python", "py.exe", "persona")


def looks_like_bot(pid: int) -> bool:
    """旧版兜底判据：pid 对应的进程**是不是一个 Python 进程**。

    旧版实例只写 pid 文件、没有互斥体；而 pid 复用会把 pid 分给随便什么进程
    ⇒ 光看"活着"就误判，必须再看进程名。
    """
    name = pid_image_name(pid).lower()
    if not name:
        return False
    return any(h in name for h in BOT_EXE_HINTS)


# ────────────────────────────── 结果对象 ──────────────────────────────

class LockResult:
    """acquire() 的返回：``bool(res)`` 即"拿到锁"。"""

    __slots__ = ("ok", "holder_pid", "reason", "note")

    def __init__(self, ok: bool, holder_pid=None, reason: str = "", note: str = ""):
        self.ok = bool(ok)
        self.holder_pid = holder_pid
        self.reason = reason
        self.note = note

    def __bool__(self):
        return self.ok

    def __repr__(self):
        return "LockResult(ok=%s, holder_pid=%s, reason=%r, note=%r)" % (
            self.ok, self.holder_pid, self.reason, self.note)


def _read_pid(path) -> int:
    try:
        with open(path, "r", encoding="utf-8") as f:
            t = (f.read() or "").strip()
        return int(t) if t.isdigit() else 0
    except Exception:
        return 0


def legacy_holder(lock_path) -> int:
    """旧版兜底：锁文件里的 pid 是否指向一个**正在跑的 Python 进程**（是则返回 pid，否则 0）。"""
    pid = _read_pid(lock_path)
    if pid and pid != os.getpid() and pid_alive(pid) and looks_like_bot(pid):
        return pid
    return 0


def probe(name: str = MUTEX_NAME, lock_path=None):
    """只查不占。返回 ``(held, pid)``：held＝有人持锁；pid＝持有者（查不到具体 pid 时为 0）。

    给"一键启动"这类**不属于持锁方**的进程用：绝不占用锁，只读内核对象是否存在。
    """
    if not IS_WIN:
        pid = legacy_holder(lock_path) if lock_path else 0
        return (bool(pid), pid)
    try:
        h = _k32.OpenMutexW(_SYNCHRONIZE, 0, name)
    except Exception:
        return (False, 0)
    if not h:
        return (False, 0)
    _k32.CloseHandle(h)
    return (True, _read_pid(lock_path) if lock_path else 0)


# ────────────────────────────── 锁本体 ──────────────────────────────

class InstanceLock:
    """命名互斥体（Windows）/ flock（POSIX）文档化的单实例锁。

    用法::

        lock = InstanceLock(lock_path=os.path.join(ROOT, "data", "bot.lock"))
        res = lock.acquire()
        if not res:
            ...  # res.holder_pid / res.reason 给用户看；这里必须退出（fail-closed）
        atexit.register(lock.release)   # release 幂等
    """

    def __init__(self, name: str = MUTEX_NAME, lock_path=None):
        self.name = name
        self.lock_path = lock_path
        self.handle = None
        self.fd = None
        self.acquired = False
        self.note = ""

    # ---- 证据文件（写失败不致命：自检在互斥体上）----
    def _write_pid_file(self) -> str:
        if not self.lock_path:
            return ""
        try:
            d = os.path.dirname(self.lock_path)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self.lock_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            os.replace(tmp, self.lock_path)
            return ""
        except Exception as e:
            return "锁文件写入失败（不影响判据，只是少一份给人和旧脚本看的证据）：%s" % e

    def _holder_hint(self):
        pid = _read_pid(self.lock_path) if self.lock_path else 0
        return pid or None

    def _try_win(self) -> LockResult:
        h = _k32.CreateMutexW(None, 0, self.name)
        err = ctypes.get_last_error()
        if not h:
            # 建不起内核对象 ⇒ 不能保证单实例 ⇒ fail-closed（不许"没有锁也照跑"）
            return LockResult(False, None, "无法创建单实例互斥体 %s（GetLastError=%s）" % (self.name, err))
        if err == ERROR_ALREADY_EXISTS:
            _k32.CloseHandle(h)
            return LockResult(False, self._holder_hint(), "已有实例持有互斥体 %s" % self.name)
        self.handle = h
        self.acquired = True
        return LockResult(True, os.getpid(), "")

    def _try_posix(self) -> LockResult:
        import fcntl
        path = self.lock_path or os.path.join(os.path.expanduser("~"), ".persona_morph.lock")
        self.lock_path = path
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return LockResult(False, self._holder_hint(), "锁文件已被占用：%s" % path)
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode("utf-8"))
        except Exception:
            pass
        self.fd = fd
        self.acquired = True
        return LockResult(True, os.getpid(), "")

    def acquire(self) -> LockResult:
        """取锁。**同进程重复取同名互斥体同样会被拦**（内核对象已存在）。"""
        if self.acquired:
            return LockResult(True, os.getpid(), "", "本对象已持锁")
        try:
            res = self._try_win() if IS_WIN else self._try_posix()
        except Exception as e:
            # 兜底：任何意外都当"拿不到锁"，并在 reason 里讲清（不允许静默）
            return LockResult(False, None, "取单实例锁异常：%s: %s" % (type(e).__name__, e))
        if res.ok:
            # POSIX 分支已经用 fd 写过 pid（不能 os.replace，会换掉被 flock 的那个 inode）
            self.note = "" if self.fd is not None else self._write_pid_file()
            res.note = self.note
        return res

    def release(self) -> None:
        """释放（幂等）。只删"内容还是本进程 pid"的证据文件。"""
        if self.handle:
            try:
                _k32.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = None
        if self.fd is not None:
            try:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                os.close(self.fd)
            except Exception:
                pass
            self.fd = None
        if self.acquired and self.lock_path:
            try:
                if _read_pid(self.lock_path) == os.getpid():
                    os.remove(self.lock_path)
            except Exception:
                pass
        self.acquired = False
