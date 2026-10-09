# -*- coding: utf-8 -*-
"""查「这个文件被**哪个进程**占用了」—— 自更新的「杀掉占用者」这条路的前半段。

为什么单独一个模块：
  `agent/update_apply.py` 换入时，被占用的文件原来**直接跳过**（`locked` 名单），
  留给用户一句"再点一次即可补换"。可用户根本不知道该点什么、也不知道是谁占着。
  现在改成：**检出占用 ⇒ 查出占用者 ⇒ 弹窗告知 ⇒ 用户点头才临时关掉它 ⇒ 更新 ⇒ 重启**。
  这个模块只干第二步（查占用者），不做任何杀进程的动作（那是调用方的事）。

用什么查：**Windows Restart Manager API**（`rstrtmgr.dll`）——
  这是 Windows 安装器/更新器查"谁占用了我要换的文件"的**官方接口**（MSI、Chrome 更新都用它）。
  比"逐进程枚举句柄"（需 SeDebugPrivilege、要装驱动或 admin）轻得多，普通用户权限就能用。
  非 Windows / API 不可用 ⇒ **如实返回空表 + 原因**，绝不假装"没占用"。

纪律（与项目其它模块一致）：
  · 只读：**只查、只列**，不杀、不关、不动任何进程；
  · 查不到就如实说查不到（`ok=False` + `why`），不编一个进程名出来；
  · 出网：无（纯本机 API）。
"""
from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes

log = logging.getLogger(__name__)

#: 一次查询允许返回的最大进程数（Restart Manager 自己的上限是 CCH_RM_MAX_APP_NAME 相关，
#: 这里给个宽松上界；真超了也只是"列不全"，不影响判"有没有被占用"）。
_MAX = 64

_IS_WIN = os.name == "nt"


class _RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", wintypes.DWORD),
                ("ProcessStartTime", wintypes.FILETIME)]


class _RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [
        ("Process", _RM_UNIQUE_PROCESS),
        ("strAppName", wintypes.WCHAR * 256),
        ("strServiceShortName", wintypes.WCHAR * 64),
        ("ApplicationType", ctypes.c_uint),
        ("AppStatus", wintypes.DWORD),
        ("TSSessionId", wintypes.DWORD),
        ("bRestartable", wintypes.BOOL),
    ]


_RM_SESSION_KEY_LEN = 32 + 1  # CCH_RM_SESSION_KEY


def _rm_points() -> dict:
    """惰性取 Restart Manager 的函数指针表（非 Windows ⇒ 抛异常，由调用方兜）。

    ⛔ 必须显式声明 `argtypes`：`RmGetList` 第 3 个参数是 `UINT*`（入＝缓冲区容量、
    出＝实际条数），第 4 个是 `PRM_PROCESS_INFO`，第 5 个是 `LPDWORD`（重启原因位）。
    不声明时 ctypes 会把 64 位指针按 32 位整数传 ⇒ GetList 直接返回 87(参数错) 或写坏内存。
    """
    dll = ctypes.WinDLL("rstrtmgr")
    start = dll.RmStartSession
    start.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.c_wchar_p]
    start.restype = wintypes.DWORD
    reg = dll.RmRegisterResources
    reg.argtypes = [wintypes.DWORD, wintypes.UINT, ctypes.POINTER(wintypes.LPCWSTR),
                    wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p]
    reg.restype = wintypes.DWORD
    getl = dll.RmGetList
    getl.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT),
                     ctypes.POINTER(_RM_PROCESS_INFO), ctypes.POINTER(wintypes.DWORD)]
    getl.restype = wintypes.DWORD
    end = dll.RmEndSession
    end.argtypes = [wintypes.DWORD]
    end.restype = wintypes.DWORD
    return {"StartSession": start, "RegisterResources": reg, "GetList": getl, "EndSession": end}


def who_locks(paths) -> dict:
    """查出**哪些进程**占用了 `paths` 里的文件。

    返回 `{ok, users:[{pid, name, service, restartable}], why}`：
      · `ok=True`  ⇒ 查询成功（`users` 可能为空＝当前没人占用）；
      · `ok=False` ⇒ 查不出来（非 Windows / API 不可用 / 文件不适用），`why` 写明原因。
    只查、不动手 —— 杀不杀由调用方带着用户点头去做。
    """
    if not _IS_WIN:
        return {"ok": False, "users": [], "why": "不是 Windows，当前平台不提供此项查询"}
    plist = [str(p) for p in (paths or []) if p and os.path.exists(str(p))]
    if not plist:
        return {"ok": True, "users": [], "why": ""}
    try:
        rm = _rm_points()
    except Exception as e:  # noqa: BLE001
        log.debug("载入 rstrtmgr 失败：%s", e, exc_info=True)
        return {"ok": False, "users": [], "why": "系统不支持占用查询（rstrtmgr 载入失败）"}

    key = ctypes.create_unicode_buffer(_RM_SESSION_KEY_LEN)
    handle = wintypes.DWORD(0)
    try:
        # RmStartSession(&handle, 0, key)：句柄在**第一个**参数，密钥在第三个
        rc = rm["StartSession"](ctypes.byref(handle), 0, key)
        if rc != 0:
            return {"ok": False, "users": [], "why": "占用查询会话开不起来（RmStartSession=%d）" % rc}
        arr = (wintypes.LPCWSTR * len(plist))(*plist)
        rc = rm["RegisterResources"](handle, len(plist), arr, 0, None, 0, None)
        if rc != 0:
            return {"ok": False, "users": [], "why": "占用查询登记文件失败（RmRegisterResources=%d）" % rc}
        need = wintypes.UINT(0)
        count = wintypes.UINT(_MAX)
        reasons = wintypes.DWORD(0)
        buf = (_RM_PROCESS_INFO * _MAX)()
        # 第一次调用拿需要的大小（ERROR_MORE_DATA=234 ⇒ 缓冲区不够）
        rc = rm["GetList"](handle, ctypes.byref(need), ctypes.byref(count), buf, ctypes.byref(reasons))
        if rc == 234:  # 缓冲区不够 ⇒ 按它要的大小再来一次
            n = max(int(need.value), _MAX)
            buf = (_RM_PROCESS_INFO * n)()
            count = wintypes.UINT(n)
            reasons = wintypes.DWORD(0)
            rc = rm["GetList"](handle, ctypes.byref(need), ctypes.byref(count), buf, ctypes.byref(reasons))
        if rc != 0:
            return {"ok": False, "users": [], "why": "占用的进程列不出来（RmGetList=%d）" % rc}
        users, seen = [], set()
        for i in range(int(count.value)):
            pi = buf[i]
            pid = int(pi.Process.dwProcessId)
            if pid in seen:
                continue
            seen.add(pid)
            users.append({
                "pid": pid,
                "name": str(pi.strAppName or "").strip() or ("PID %d" % pid),
                "service": str(pi.strServiceShortName or "").strip(),
                # 该程序是否支持"关掉后由系统自动重启"（Restart Manager 的标志位）
                "restartable": bool(pi.bRestartable),
            })
        return {"ok": True, "users": users, "why": ""}
    except Exception as e:  # noqa: BLE001
        log.debug("占用查询异常：%s", e, exc_info=True)
        return {"ok": False, "users": [], "why": "占用查询异常：%s" % (str(e)[:80] or type(e).__name__)}
    finally:
        try:
            rm["EndSession"](handle)
        except Exception:  # noqa: BLE001
            pass


def who_locks_many(mapping) -> list:
    """`mapping` 是 `{文件相对路径: 绝对路径}` ⇒ 逐项查占用者，合并成一张人话清单。

    返回 `[{rel, path, users:[…], ok, why}]`（只列**真被占用**的那些；没占用的不进结果）。
    """
    out = []
    for rel, p in (mapping or {}).items():
        r = who_locks([p])
        if r.get("users"):
            out.append({"rel": rel, "path": p, "users": r["users"], "ok": True, "why": ""})
        elif not r.get("ok"):
            # 查不出来也要如实带上（前端会说明"某件查不到占用者"），而不是当"没人占用"
            out.append({"rel": rel, "path": p, "users": [], "ok": False, "why": r.get("why") or ""})
    return out
