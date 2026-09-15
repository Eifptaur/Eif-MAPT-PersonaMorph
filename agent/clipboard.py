# -*- coding: utf-8 -*-
"""剪贴板原语（只做"把东西放进去"，不碰鼠标、不碰前台）：图片走 CF_DIB，文件走 CF_HDROP。

为什么需要：微信发送图片/文件最干净的投递路径是「**剪贴板放好 → 投递 `WM_PASTE` 给输入框 → 点发送**」，
比"真鼠标拖拽/走系统选择文件对话框"更贴合最高目标（不动鼠标、不打扰你（可能短暂置前约 1~3 秒后自动还回））。
另一个应用正占着剪贴板时会失败 ⇒ 自带重试，失败给可读原因。
"""
from __future__ import annotations

import ctypes
import io
import os
import time

CF_TEXT, CF_DIB, CF_HDROP, CF_UNICODETEXT = 1, 8, 15, 13
GMEM_MOVEABLE, GMEM_ZEROINIT = 0x0002, 0x0040

_u32 = ctypes.windll.user32
_k32 = ctypes.windll.kernel32

# ⚠️ 64 位下**必须声明 argtypes**：不声明时 ctypes 把 Python 整数按 32 位传
#    ⇒ GlobalLock 收到被截断的句柄 ⇒ **整个进程直接崩**（实测踩过，连异常都抓不到）。
_k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
_k32.GlobalAlloc.restype = ctypes.c_void_p
_k32.GlobalLock.argtypes = [ctypes.c_void_p]
_k32.GlobalLock.restype = ctypes.c_void_p
_k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_k32.GlobalFree.argtypes = [ctypes.c_void_p]
_u32.OpenClipboard.argtypes = [ctypes.c_void_p]
_u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
_u32.SetClipboardData.restype = ctypes.c_void_p
_u32.GetClipboardData.argtypes = [ctypes.c_uint]
_u32.GetClipboardData.restype = ctypes.c_void_p


def _open_clipboard(retries: int = 6) -> bool:
    for _ in range(max(1, retries)):
        if _u32.OpenClipboard(None):
            return True
        time.sleep(0.15)                       # 别的程序占着剪贴板：等一下再试
    return False


def _set_raw(fmt: int, data: bytes) -> tuple:
    """把一段原始字节塞进剪贴板（GlobalAlloc + SetClipboardData）。"""
    if not _open_clipboard():
        return False, "剪贴板被别的程序占用（OpenClipboard 失败）"
    h = None
    try:
        _u32.EmptyClipboard()
        h = _k32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, len(data))
        if not h:
            return False, "GlobalAlloc 失败"
        p = _k32.GlobalLock(h)
        if not p:
            return False, "GlobalLock 失败"
        ctypes.memmove(p, data, len(data))
        _k32.GlobalUnlock(h)
        if not _u32.SetClipboardData(fmt, ctypes.c_void_p(h)):
            return False, "SetClipboardData 失败"
        h = None                               # 所有权移交系统，不能再 free
        return True, ""
    except Exception as e:
        return False, "写剪贴板异常：%s: %s" % (type(e).__name__, e)
    finally:
        try:
            _u32.CloseClipboard()
        except Exception:
            pass
        if h:
            try:
                _k32.GlobalFree(ctypes.c_void_p(h))
            except Exception:
                pass


def set_image(path: str) -> tuple:
    """把图片文件放进剪贴板（CF_DIB）：返回 (ok, 说明)。"""
    if not path or not os.path.isfile(path):
        return False, "图片文件不存在：%s" % path
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")             # DIB 用 24 位真彩最稳
            buf = io.BytesIO()
            im.save(buf, "BMP")
        data = buf.getvalue()[14:]              # 去掉 BMP 的 14 字节文件头 ＝ CF_DIB
        return _set_raw(CF_DIB, data)
    except Exception as e:
        return False, "准备图片数据失败：%s: %s" % (type(e).__name__, e)


def set_files(paths) -> tuple:
    """把若干文件路径放进剪贴板（CF_HDROP，供"粘贴文件"用）。"""
    if isinstance(paths, str):
        paths = [paths]
    paths = [os.path.abspath(str(p)) for p in (paths or []) if p]
    miss = [p for p in paths if not os.path.isfile(p)]
    if miss:
        return False, "文件不存在：%s" % miss[0]
    if not paths:
        return False, "没有要放进剪贴板的文件"
    try:
        class DROPFILES(ctypes.Structure):
            _fields_ = [("pFiles", ctypes.c_uint32), ("pt", ctypes.c_int32 * 2),
                        ("fNC", ctypes.c_int32), ("fWide", ctypes.c_int32)]
        hdr = DROPFILES()
        hdr.pFiles = ctypes.sizeof(DROPFILES)
        hdr.fWide = 1
        raw = ("\0".join(paths) + "\0\0").encode("utf-16-le")
        return _set_raw(CF_HDROP, bytes(hdr) + raw)
    except Exception as e:
        return False, "准备文件列表失败：%s: %s" % (type(e).__name__, e)


def set_text(text: str) -> tuple:
    """把文本放进剪贴板（CF_UNICODETEXT）；给"粘贴长文本"这类场景备用。"""
    try:
        return _set_raw(CF_UNICODETEXT, (str(text) + "\0").encode("utf-16-le"))
    except Exception as e:
        return False, "写文本失败：%s: %s" % (type(e).__name__, e)


def get_text() -> str:
    """读回剪贴板文本（只读，用于自检）。"""
    try:
        if not _u32.OpenClipboard(0):
            return ""
        try:
            h = _u32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return ""
            p = _k32.GlobalLock(ctypes.c_void_p(h))
            if not p:
                return ""
            try:
                return ctypes.wstring_at(p)
            finally:
                _k32.GlobalUnlock(ctypes.c_void_p(h))
        finally:
            _u32.CloseClipboard()
    except Exception:
        return ""
