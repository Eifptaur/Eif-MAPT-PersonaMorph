# -*- coding: utf-8 -*-
"""判据：剪贴板原语（图片/文件/文本）不需要微信，纯本地可测。
用法：py -3 scripts/clipboard_selftest.py
"""
import ctypes
import os
import shutil
import sys

try: # 控制台默认 GBK：自检里的 ✔/✘ 一旦被重定向就 UnicodeEncodeError 崩掉整条自检
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 取同目录的共用件 `_probe.py`

from _probe import assert_probe_hit # noqa: E402
from PIL import Image # noqa: E402

from agent import clipboard as cb # noqa: E402


# ⛔ 判据**不碰真操作系统剪贴板**（原来会覆盖用户剪贴板里的东西）。
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
        self.blocks[ctypes.addressof(buf)] = buf # 保住 buffer 不被回收
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
_wrote = []                      # 三条写通路的成败，给 ④ 当分母（见下面的守卫）
ok, why = cb.set_text("剪贴板自测-ABC")
_wrote.append(bool(ok))
ck("写文本成功", ok, why)
ck("读回一致", cb.get_text() == "剪贴板自测-ABC", repr(cb.get_text()))

print("② 图片进剪贴板（CF_DIB）")
ok, why = cb.set_image(img)
_wrote.append(bool(ok))
ck("写图片成功", ok, why)

print("③ 文件进剪贴板（CF_HDROP）")
ok, why = cb.set_files([img])
_wrote.append(bool(ok))
ck("写文件成功", ok, why)

# 分母守卫：④ 那几条断言的全是"坏输入 ⇒ False"。内存假件（探针）一失效，
# **写入本身就会全部返回 False** ⇒ ④ 会静默全绿。所以先钉住"三条写通路真的都活着"。
assert_probe_hit(ck, "④ 探针有效：①②③ 三条写通路真的都写进去了", sum(_wrote), min_expected=3)

print("④ 坏输入：都要给可读原因、不抛异常")
ok, why = cb.set_image(os.path.join(root, "不存在.png"))
ck("不存在 ⇒ False + 文案", (not ok) and "不存在" in why, why)
ok, why = cb.set_files([])
ck("空列表 ⇒ False + 文案", (not ok) and "没有" in why, why)
ok, why = cb.set_files([os.path.join(root, "x.bin")])
ck("坏路径 ⇒ False + 文案", (not ok), why)

shutil.rmtree(root, ignore_errors=True)

# ── D. 死引用：对 `clipboard` 的调用**名字必须真实存在** ────────────────────────────
#   现场：`input_backend.paste_text` 写的是 `_cb.read_text()` / `_cb.write_text()`，
#   而本模块只有 `set_text` / `get_text` / `snapshot` / `restore` —— 那两个名字**不存在**
#   ⇒ `hasattr` 恒假（剪贴板永不还原）＋整条粘贴路**每次抛 `AttributeError` 被上层宽 `except`
#   吞掉** ⇒ **静默退回逐字投递**（"输入法把首字母串进中文"那个现场就是这么来的）。
#   ⇒ 机械守：`agent/` 里凡 `_cb.<名字>` 都必须能在本模块里找得到。
import ast as _ast  # noqa: E402
import glob as _glob  # noqa: E402
import io as _io  # noqa: E402

_HAVE = {n for n in dir(cb) if not n.startswith("_")}
_BADREF, _NREF = [], 0
for _p in sorted(_glob.glob(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                         "agent", "*.py"))):
    try:
        _tree = _ast.parse(_io.open(_p, encoding="utf-8", errors="ignore").read())
    except Exception:  # noqa: BLE001
        continue
    for _n in _ast.walk(_tree):
        if isinstance(_n, _ast.Attribute) and isinstance(_n.value, _ast.Name) \
                and _n.value.id in ("_cb", "CB") and not _n.attr.startswith("_"):
            _NREF += 1
            if _n.attr not in _HAVE:
                _BADREF.append("%s:%d %s.%s" % (os.path.basename(_p), _n.lineno, _n.value.id, _n.attr))
ck("D1b 分母守卫：真的扫到了对 clipboard 的调用点（否则下面那条测的是空气）", _NREF >= 4, "%d 处" % _NREF)
ck("D1 ⛔ 对 `clipboard` 的调用名字必须真实存在（死引用会被宽 except 吞成静默降级）",
   not _BADREF, "; ".join(_BADREF[:4]))

# ── E. 粘贴通路的**适用范围**（实测边界：不许接进最小化档）──────────────────────────
#   实测（本机、最小化档）：`paste_text` 返回 `ok=True`、剪贴板还原正常，
#   但**回车后 8 秒库里没有新行** ⇒ `WM_PASTE` 在最小化窗上**送不进去**；
#   而同一档的逐字 `WM_CHAR` 能用（本链多次实测发出）。
#   ⇒ 剪贴板粘贴那条路只在**有画面/前台**的场景（如搜索浮层）成立；
#     ⛔ 接进 `send_to_popup_blind` 就是**回归**（字一条都发不出去）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _srcslice as _ss  # noqa: E402

_WSRC = _io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "agent", "wechat_windows.py"), encoding="utf-8").read()
_BLIND = _ss.func_src(_WSRC, "send_to_popup_blind")
import re as _re  # noqa: E402


def _code_only(s):
    """剥掉 `#` 注释与三引号 docstring ⇒ 只看**代码本身**。

    ⛔ 必须剥：注释/docstring 里会**引用**被禁的写法做反面说明（"不许改成 paste_text"），
      拿整份函数体做子串匹配会把「叙述」当成「违规」（本项目已在多处踩过这个假红）。
    """
    s = "\n".join(l.split("#")[0] for l in s.splitlines())
    return _re.sub(r'""".*?"""', "", s, flags=_re.S)


_BLIND_CODE = _code_only(_BLIND)
ck("E1 ⛔ 最小化档的文字链**不许**改用剪贴板粘贴（实测 `WM_PASTE` 在最小化窗上送不进）",
   "paste_text" not in _BLIND_CODE and "be.send_text(hwnd, text)" in _BLIND_CODE)
ck("E2 这条边界要**写在注释里**（否则后人「顺手优化」就会踩）",
   ("WM_PASTE" in _BLIND) and ("剪贴板" in _BLIND))
_IB = _io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "agent", "input_backend.py"), encoding="utf-8").read()
# ⛔ 断言用**调用形态**（带括号），不用裸名字：docstring 里会提到那两个错名字做反面说明。
ck("E3 ★ 剪贴板保护走**项目自己的收口** `clipboard_guard`（异常出口也还原），"
   "且不再用不存在的 `read_text`/`write_text`",
   "_cb.clipboard_guard(" in _IB and "_cb.read_text(" not in _IB and "_cb.write_text(" not in _IB)

print("\n== 结论：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
