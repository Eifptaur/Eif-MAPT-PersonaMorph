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
import logging

log = logging.getLogger("persona-morph")

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
        time.sleep(0.15) # 别的程序占着剪贴板：等一下再试
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
        h = None # 所有权移交系统，不能再 free
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
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                pass


def set_image(path: str) -> tuple:
    """把图片文件放进剪贴板（CF_DIB）：返回 (ok, 说明)。"""
    if not path or not os.path.isfile(path):
        return False, "图片文件不存在：%s" % path
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB") # DIB 用 24 位真彩最稳
            buf = io.BytesIO()
            im.save(buf, "BMP")
        data = buf.getvalue()[14:] # 去掉 BMP 的 14 字节文件头 ＝ CF_DIB
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
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# 备份 / 恢复：**发图片或文件前先备份用户剪贴板，发完立刻还回去**
# ─────────────────────────────────────────────────────────────────────────────

def snapshot() -> dict:
    """记录剪贴板当前内容 ⇒ 供 `restore()` 还原。

    ⛔ 为什么需要它（T20）：微信发图片/文件要走「剪贴板 + `WM_PASTE`」，
    这会**覆盖用户的剪贴板**。当天已实际发生一次冲突 —— 用户当时正在复制粘贴，
    结果"粘出来的是我们的测试文字"。**发东西不能把用户复制的内容弄丢。**

    做法：只备份**能可靠还原的形态**（文本 / 文件列表）；其他格式（图片二进制等）
    仅记录"存在过"，**不强行还原**（避免把别人的位图数据搞坏）。
    ⚠️ 已知局限：**图片不会还原**（CF_DIB 的原始字节未做深拷贝）⇒ 会在返回值里注明。
    """
    snap = {"has_text": False, "text": "", "files": [], "has_image": False,
            "formats": [], "ok": False}
    try:
        if not _u32.OpenClipboard(0):
            return snap
        try:
            snap["ok"] = True
            fmt = 0
            while True:
                try:
                    fmt = _u32.EnumClipboardFormats(fmt)
                except Exception:
                    break
                if not fmt:
                    break
                snap["formats"].append(int(fmt))
            snap["has_image"] = CF_DIB in snap["formats"]
            # ★ 文件列表（CF_HDROP）：**可可靠还原**（用 set_files 放回去）
            #   ⇒ 值得备份：用户剪贴板里经常是"复制的文件"，被我们覆盖后要能放回。
            if CF_HDROP in snap["formats"]:
                try:
                    h = _u32.GetClipboardData(CF_HDROP)
                    if h:
                        _sh = ctypes.windll.shell32
                        n = int(_sh.DragQueryFileW(ctypes.c_void_p(h), 0xFFFFFFFF, None, 0))
                        buf = ctypes.create_unicode_buffer(1024)
                        fs = []
                        for _i in range(max(0, min(int(n), 64))):
                            if _sh.DragQueryFileW(ctypes.c_void_p(h), _i, buf, 1024):
                                fs.append(buf.value)
                        snap["files"] = fs
                except Exception as _e:
                    log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                    pass
            if CF_UNICODETEXT in snap["formats"]:
                h = _u32.GetClipboardData(CF_UNICODETEXT)
                if h:
                    p = _k32.GlobalLock(ctypes.c_void_p(h))
                    if p:
                        try:
                            snap["text"] = ctypes.wstring_at(p)
                            snap["has_text"] = bool(snap["text"])
                        finally:
                            _k32.GlobalUnlock(ctypes.c_void_p(h))
        finally:
            _u32.CloseClipboard()
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    return snap


def restore(snap: dict) -> tuple:
    """尽力把剪贴板还原成 `snapshot()` 记录的样子 ⇒ `(ok, 说明)`。

    ⚠️ 只还原**文本**（最常用、且是我们唯一会覆盖的形态）；
    若快照里只有图片/文件 ⇒ **不还原**（不做不可靠的重建），但在说明里写清。
    """
    if not snap:
        return False, "空快照"
    if snap.get("has_text"):
        r = set_text(snap.get("text") or "")
        return (bool(r and r[0]), "已还原原剪贴板文本" if (r and r[0]) else "还原文本失败")
    if snap.get("files"):
        # ★ 文件列表可可靠还原（用 CF_HDROP 放回）
        try:
            r = set_files(list(snap["files"]))
            if r and r[0]:
                return True, "已还原原剪贴板文件列表（%d 项）" % len(snap["files"])
            return False, "还原文件列表失败：%s" % str(r[1])[:50]
        except Exception as e:
            return False, "还原文件列表异常：%s" % str(e)[:50]
    if snap.get("has_image"):
        return False, "原剪贴板是图片 ⇒ 未还原（避免重建出错）"
    return True, "原剪贴板为空 ⇒ 无需还原"


class clipboard_guard:
    """上下文管理器：**进入时备份、退出时还原**（异常也还原）。

    用法：
    ```python
    with clipboard_guard() as g:
        clipboard.set_files([path])      # 我们会覆盖用户剪贴板
        backend.paste(hwnd)              # 投递给目标窗口
    print(g.result)                      # 还原结果说明
    ```
    """

    def __init__(self):
        self.snap = {}
        self.result = ("", "")

    def __enter__(self):
        self.snap = snapshot()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.result = restore(self.snap)
        except Exception as e:
            self.result = (False, "还原异常：%s" % str(e)[:60])
        return False   # 不吞异常


def clipboard_safe(fn):
    """装饰器：**进函数先备份用户剪贴板，出函数（含异常）再放回**。

    ⛔ 为什么必须：发图/表情要走「剪贴板 + 粘贴」⇒ **会覆盖用户剪贴板**
      （实测发生过：用户正在复制粘贴，粘出来的却是我们的测试内容）。
    ⚠️ 能力边界：`restore()` **只可靠还原文本**；图片/文件列表**不重建**
      （避免把别人的位图数据搞坏）⇒ 别指望它把剪贴板"还原成原样"。
    ⚠️ **剪贴板生命期跨函数的链条不许直接套在写剪贴板那个函数上**：
      若在 A 里写、到 B 才粘贴，装饰 A 会在 A 返回时就把用户的剪贴板还回去 ⇒ B 粘到的是用户那份。
      ⇒ 这种链条要装饰**拥有整段生命期的那个函数**（例如 prepare→fire 的编排入口）。
      判据口径同为"写剪贴板的那个函数，或它的上层拥有者，必须带保护"。
    """
    import functools

    @functools.wraps(fn)
    def _wrap(*args, **kwargs):
        _snap, _r = None, None
        try:
            _snap = snapshot()
        except Exception:  # noqa: BLE001 — 备份失败不阻断主流程（宁可不动剪贴板也要把消息发出去）
            _snap = None
        try:
            _r = fn(*args, **kwargs)
        finally:
            if _snap is not None:
                try:
                    _ok, _why = restore(_snap)
                    if isinstance(_r, dict):
                        _r["clipboard_restored"] = bool(_ok)
                        _r["clipboard_restore_why"] = str(_why)
                except Exception:  # noqa: BLE001 — 还原失败不改返回值语义
                    pass
        return _r

    return _wrap

