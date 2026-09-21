# -*- coding: utf-8 -*-
"""判据：剪贴板原语（图片/文件/文本）不需要微信，纯本地可测。
用法：py -3 scripts/clipboard_selftest.py
"""
import ctypes
import os
import shutil
import sys

try:      # 控制台默认 GBK：自检里的 ✔/✘ 一旦被重定向就 UnicodeEncodeError 崩掉整条自检
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image                     # noqa: E402

from agent import clipboard as cb         # noqa: E402


# ⛔ V-R7-4：判据**不碰真操作系统剪贴板**（原来会覆盖用户剪贴板里的东西）。
#   做法＝把本模块用的 Win32 原语换成**内存假件**：产品的 `_set_raw / set_image / set_files /
#   set_text / get_text` 一行不改，照样走 GlobalAlloc → GlobalLock → memmove → SetClipboardData
#   那条真路径（含 BMP 去文件头、DROPFILES 结构、UTF-16 编码），只是落在内存里。
#   ⚠️ 这不是"把断言删掉"：写进去的数据仍可被 `get_text()` 原路读回（见 ①）。
class _MemK32(object):
    def __init__(self):
        self.blocks = {}

    @staticmethod
    def _a(h):
        # 产品里句柄既可能是 int（GlobalAlloc 的返回），也可能是 `ctypes.c_void_p(...)`
        # （GlobalLock/GlobalFree 那两处）—— 后者**不能直接 int()**（它是 buffer 协议对象）。
        return int(getattr(h, "value", h) or 0)

    def GlobalAlloc(self, flags, size):
        buf = ctypes.create_string_buffer(max(1, int(size)))
        self.blocks[ctypes.addressof(buf)] = buf          # 保住 buffer 不被回收
        return ctypes.addressof(buf)

    def GlobalLock(self, h):
        return self._a(h)

    def GlobalUnlock(self, h):
        return 1

    def GlobalFree(self, h):
        self.blocks.pop(self._a(h), None)
        return 0


class _MemU32(object):
    def __init__(self):
        self.data = {}

    def OpenClipboard(self, h):
        return 1

    def EmptyClipboard(self):
        self.data = {}
        return 1

    def SetClipboardData(self, fmt, h):
        self.data[int(fmt)] = _MemK32._a(h)
        return _MemK32._a(h)

    def GetClipboardData(self, fmt):
        return self.data.get(int(fmt), 0)

    def CloseClipboard(self):
        return 1


cb._k32, cb._u32 = _MemK32(), _MemU32()

PASS = FAIL = 0


def ck(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✔ %s" % name)
    else:
        FAIL += 1
        print("  ✘ %s  %s" % (name, detail))


root = tempfile.mkdtemp(prefix="clip_")
img = os.path.join(root, "t.png")
Image.new("RGB", (64, 48), (200, 60, 60)).save(img)

print("① 文本进出剪贴板（自证通路可用）")
ok, why = cb.set_text("剪贴板自测-ABC")
ck("写文本成功", ok, why)
ck("读回一致", cb.get_text() == "剪贴板自测-ABC", repr(cb.get_text()))

print("② 图片进剪贴板（CF_DIB）")
ok, why = cb.set_image(img)
ck("写图片成功", ok, why)

print("③ 文件进剪贴板（CF_HDROP）")
ok, why = cb.set_files([img])
ck("写文件成功", ok, why)

print("④ 坏输入：都要给可读原因、不抛异常")
ok, why = cb.set_image(os.path.join(root, "不存在.png"))
ck("不存在 ⇒ False + 文案", (not ok) and "不存在" in why, why)
ok, why = cb.set_files([])
ck("空列表 ⇒ False + 文案", (not ok) and "没有" in why, why)
ok, why = cb.set_files([os.path.join(root, "x.bin")])
ck("坏路径 ⇒ False + 文案", (not ok), why)

shutil.rmtree(root, ignore_errors=True)
print("\n== 结论：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
