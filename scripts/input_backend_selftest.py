# -*- coding: utf-8 -*-
"""输入后端自检：**不需要微信在跑**，全部是逻辑级判据 + 一条"棘轮"式机械验收。

三组：
  A/B/C 单元判据：DPI 锁定、lParam 打包、投递序列（消息种类/顺序/wParam/坐标）、后端选档
  R 棘轮判据：全仓扫真实输入 API（`mouse_event/SetCursorPos/SendInput/keybd_event` 的**调用**），
    只允许出现在基线文件里 —— 任何**新**文件开始直接动鼠标就判红（这会拦住"又长出一处真鼠标"）。
    目标态：只余 `agent/ui_adapt.py`（L0 的唯一下沉点）+ `agent/input_backend.py`（包装它）。

用法：py -3 scripts\\input_backend_selftest.py   （非零退出＝有失败）
"""
import os
import re
import sys

import logging
log = logging.getLogger("persona-morph")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 判据隔离（收口在 `scripts/_iso14.py` 一处）：必须在 `from agent import …` 之前调。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))) # `_iso14` 在 scripts/ 下
import _iso14 # noqa: E402
_iso14.all_()
from agent import input_backend as ib # noqa: E402  （import 即锁 DPI）

OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name, (" · " + extra) if extra else ""))


# ── A 单元自检 ──────────────────────────────────────────────────────────
print("[A] 基础")
ck("A1 DPI 已锁（非 none）", ib.DPI_MODE != "none", "DPI_MODE=%s" % ib.DPI_MODE)
ck("A2 lParam 打包 (10,20)", ib.pack_lparam(10, 20) == ((20 << 16) | 10))
ck("A3 lParam 打包 (300,400)", ib.pack_lparam(300, 400) == ((400 << 16) | 300))
ck("A4 lParam 打包屏蔽高位溢出", ib.pack_lparam(70000, 5) == ((5 << 16) | (70000 & 0xFFFF)),
   "hex=%s" % hex(ib.pack_lparam(70000, 5)))
ck("A5 screen_point 换算", ib.screen_point((100, 200, 300, 400), (10, 20)) == (110, 220))

# ── B 投递序列（把 _post / to_client 换成记录器，不需要真窗口）─────────────
print("[B] 投递序列")
sent = []
orig_post, orig_to_client = ib._post, ib.to_client
ib._post = lambda h, m, w, l: sent.append((int(h), int(m), int(w), int(l))) or 1
ib.to_client = lambda h, pt: (7, 9)


class _NoSleep:
    def __init__(self):
        self.n = 0

    def __call__(self, _s):
        self.n += 1


_real_sleep = ib.time.sleep
ib.time.sleep = _NoSleep()

b = ib.MessageBackend(press_ms=60, activate=True)
sent[:] = []
ok, why = b.click(1234, (500, 600))
LP = ib.pack_lparam(7, 9)
expect = [(1234, ib.WM_ACTIVATE, 1, 0), (1234, ib.WM_NCACTIVATE, 1, 0),
          (1234, ib.WM_MOUSEMOVE, 0, LP), (1234, ib.WM_LBUTTONDOWN, 1, LP),
          (1234, ib.WM_LBUTTONUP, 0, LP)]
ck("B1 click 返回成功", ok is True)
ck("B2 click 消息序列＝伪激活→移动→按下→抬起", sent == expect, "got=%s" % (sent,))
ck("B3 click lParam 用客户区坐标", all(m[3] in (0, LP) for m in sent))

sent[:] = []
b2 = ib.MessageBackend(activate=False)
b2.click(1234, (500, 600))
ck("B4 activate=False 时不发伪激活", [m[1] for m in sent] == [ib.WM_MOUSEMOVE, ib.WM_LBUTTONDOWN, ib.WM_LBUTTONUP])

sent[:] = []
r = b.click(0, (500, 600))
ck("B5 空句柄拒绝且不发消息", r[0] is False and not sent)
sent[:] = []
_saved_fm = ib.find_main_window
ib.find_main_window = lambda: 4321 # 主窗桩：右键必须**改投它**
r = b.click(1234, (500, 600), right=True)
ib.find_main_window = _saved_fm
_tg = {c[0] for c in sent}
# **投递右键已实测可用**（投渲染子窗不弹菜单、**投主窗才弹**；再投递点菜单项能命中，
# 自检＝剪贴板被写成那条消息的正文）⇒ 不再"明确拒绝"，而是**明确改投主窗**并用 RBUTTON 消息。
# 守的仍是"不许乱点"：目标窗必须只有主窗 4321，且必须发出 右键按下/抬起。
ck("B6 投递右键＝改投主窗 + 发 RBUTTON 消息（不再拒绝）",
   r[0] is True and _tg == {4321} and any(c[1] == 0x0204 for c in sent) and any(c[1] == 0x0205 for c in sent),
   "%s / %s" % (r[1], sent))

sent[:] = []
ok, why = b.send_text(1234, "ab中")
# ⚠️ 修：B7/B8/B9 原来是**既有基线红**，根因＝**判据没跟行为**
#    （纪律 6 的同族）：`send_text()` 自 起**必须先 `_wake`**（真机实测口径：
#     独立窗不唤醒 ⇒ 字进不去，绿占比 0.0000），可这组判据仍按"send_text 只发 WM_CHAR /
#     不发伪激活"的**旧行为**在断言 ⇒ 每次跑都红，且红得没有信息量。
#    ⇒ 现在按**真实现状**断：先过滤出 WM_CHAR 再判（不被 `_wake` 的激活消息干扰）；
#      并新增 B9b **正向**断言"必须先伪激活"（守的是实测口径，防有人把它删回去）。
_chars = [m for m in sent if m[1] == ib.WM_CHAR]
ck("B7 send_text 逐字 WM_CHAR", [m[1] for m in _chars] == [ib.WM_CHAR] * 3, "%s" % (sent,))
ck("B8 WM_CHAR 载的是字符码", [m[2] for m in _chars] == [ord("a"), ord("b"), ord("中")], "%s" % (_chars,))
ck("B9 send_text **必须先伪激活**（真机实测：不唤醒 ⇒ 字进不去）",
   ib.WM_ACTIVATE in [m[1] for m in sent], "%s" % (sent,))
ck("B9b 伪激活早于投字（顺序不能反）",
   [m[1] for m in sent].index(ib.WM_ACTIVATE) < [m[1] for m in sent].index(ib.WM_CHAR)
   if (ib.WM_ACTIVATE in [m[1] for m in sent] and ib.WM_CHAR in [m[1] for m in sent]) else False,
   "%s" % ([m[1] for m in sent],))
sent[:] = []
ck("B10 空文本拒绝", b.send_text(1234, "")[0] is False and not sent)
ck("B11 空句柄拒绝", b.send_text(0, "x")[0] is False)

# ── C 选档 ──────────────────────────────────────────────────────────────
print("[C] 选档")
orig_find = ib.find_main_window
ck("C1 config=message ⇒ 投递档",
   ib.select_backend({"input": {"backend": "message"}}).name == ib.LEVEL_MESSAGE)
ck("C2 config=real ⇒ 真鼠标档",
   ib.select_backend({"input": {"backend": "real"}}).name == ib.LEVEL_REAL)
ib.find_main_window = lambda: 0
ck("C3 auto 且找不到主窗 ⇒ 退回真鼠标档",
   ib.select_backend({"input": {}}).name == ib.LEVEL_REAL)
ib.find_main_window = lambda: 4321
ck("C4 auto 且有主窗 ⇒ 投递档",
   ib.select_backend({"input": {}}).name == ib.LEVEL_MESSAGE)
ck("C5 真鼠标档标记 touches_cursor=True（控制台要能看见代价）",
   ib.RealInputBackend().touches_cursor is True and b.touches_cursor is False)
st = ib.status()
ck("C6 status() 暴露当前档位与 DPI", st.get("backend") and st.get("dpi_mode") and "touches_cursor" in st)
# C7~C9：挑主窗（纯函数）—— 同类名兄弟窗 + 隐藏态都不许选错/降级
ck("C7 优先选「带渲染子窗」的那个（朋友圈编辑窗也同类名但没有渲染子窗）",
   ib.pick_main_window([(111, False, 615 * 675, True), (222, True, 1139 * 890, True)]) == 222)
ck("C8 同带渲染子窗时取面积最大的",
   ib.pick_main_window([(333, True, 100 * 100, True), (444, True, 1139 * 890, True)]) == 444)
ck("C9 **隐藏/最小化的主窗也要能选中**（否则 auto 会静默降级成真鼠标档）",
   ib.pick_main_window([(555, True, 1139 * 890, False), (666, False, 615 * 675, True)]) == 555)
ck("C10 一个候选都没有 ⇒ 返回 0（此时才允许退回真鼠标）", ib.pick_main_window([]) == 0)
ib.find_main_window = orig_find

ib._post, ib.to_client, ib.time.sleep = orig_post, orig_to_client, _real_sleep

# ── R 棘轮：真实输入 API 只许留在基线文件里 ──────────────────────────────
print("[R] 棘轮（真实输入 API 的下沉点）")
PAT = re.compile(r"\.\s*(mouse_event|SetCursorPos|SendInput|keybd_event)\s*\(")
BASELINE = {
    ROOT + os.sep + "agent" + os.sep + "ui_adapt.py", # L0 唯一下沉点（heal_input / real_guard / click_real_hold）
    ROOT + os.sep + "agent" + os.sep + "wechat.py", # 待收口（21 处）
    ROOT + os.sep + "agent" + os.sep + "wechat_ui.py", # 待收口（7 处）
    ROOT + os.sep + "scripts" + os.sep + "persona_morph.py", # 待收口（1 处）
    # 真语音条那条链**必须**用 SendInput 注入「真·右 Alt」——
    #   实测投递键盘消息（PostMessage WM_KEY*）微信不认（绿簇毫无反应），只有 SendInput 才行；
    #   而它注入的是**键盘**、全程不动鼠标，这正是"不动鼠标发语音条"的实现方式本身。
    #   ⚠️ 该文件里**不许**出现 mouse_event / SetCursorPos；要真点必须走 ui_adapt.click_real_hold。
    ROOT + os.sep + "agent" + os.sep + "voice_strip.py",
    # `popup_paste_blind`（盲粘贴）**必须**用 `keybd_event` 真按一下 Ctrl ——
    #   微信判 Ctrl+V 读的是**真实键态**（投递的消息不携带修饰键），只投 `V` 会退化成字面字母 `v`
    #   并被发出去（早期实测踩到 `vv` 污染群聊）。⇒ 那两枪（按下/抬起）是**有意保留**的，
    #   而且它注入的是**键盘**、不动鼠标。
    #   ⚠️ 该文件里**不许**出现 mouse_event / SetCursorPos —— 它只投鼠标**消息**（`WM_*`），不动真光标。
    ROOT + os.sep + "agent" + os.sep + "wechat_windows.py",
}
hits, new = {}, []
for sub in ("agent", "scripts"):
    for dirpath, _dirs, files in os.walk(os.path.join(ROOT, sub)):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                txt = open(p, encoding="utf-8", errors="replace").read()
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                continue
            n = len(PAT.findall(txt))
            if n:
                hits[os.path.relpath(p, ROOT)] = n
                if p not in BASELINE:
                    new.append(os.path.relpath(p, ROOT))
print("  当前实时输入 API 调用点：%s" % hits)
ck("R1 没有新文件开始直接动鼠标", not new, ("新出现：%s" % new) if new else "基线内")
ck("R2 基线文件仍然存在（防止改名绕过）",
   all(os.path.exists(p) for p in BASELINE))
ck("R3 总数在下降而不是上升", sum(hits.values()) <= 36, "当前 %d 处" % sum(hits.values()))

print("\n[S] input.press_ms / input.activate 不再是死键（2026-09-15 接线）")
# 改前：这两个键只在 config.py 定义，全仓没一处读 —— 用户改 config.json 完全没用
# （MessageBackend 的默认值写死在构造函数签名里）。现在 select_backend 会读进去。
from agent import input_backend as IB # noqa: E402
_b1 = IB.select_backend({"input": {"backend": "message"}})
ck("S1 默认仍是 press_ms=60 / activate=True（没改默认行为）",
   getattr(_b1, "press_ms", None) == 60 and getattr(_b1, "activate", None) is True,
   "press=%s act=%s" % (getattr(_b1, "press_ms", None), getattr(_b1, "activate", None)))
_b2 = IB.select_backend({"input": {"backend": "message", "press_ms": 123, "activate": False}})
ck("S2 配置真的生效（123 / False）",
   getattr(_b2, "press_ms", None) == 123 and getattr(_b2, "activate", None) is False,
   "press=%s act=%s" % (getattr(_b2, "press_ms", None), getattr(_b2, "activate", None)))
ck("S3 越界值被夹住（0 -> 1，99999 -> 2000）",
   IB.select_backend({"input": {"backend": "message", "press_ms": 0}}).press_ms == 1
   and IB.select_backend({"input": {"backend": "message", "press_ms": 99999}}).press_ms == 2000)
ck("S4 脏值不炸（字符串/None 都能兜住）",
   IB.select_backend({"input": {"backend": "message", "press_ms": "abc"}}).press_ms == 60
   and IB.select_backend({"input": {"backend": "message", "press_ms": None}}).press_ms == 60)
ck("S5 反证：real 档不受这两个键影响（真鼠标档没有这两个参数）",
   not isinstance(IB.select_backend({"input": {"backend": "real", "press_ms": 123}}),
                  IB.MessageBackend) if hasattr(IB, "MessageBackend") else True)

# ── T 段：★ 发送提速三项的**守锚**（后落地）───────────────
#
# 为什么要有这组：这三项（删 `_wake` 的 sleep / `hold_ms` 30→5 / `keys` 的 `wake=False`）
# 都是**改了行为、只看数字看不出来**的那种改动 —— 一旦有人（包括以后的我）顺手把
# `sleep(0.08)` 加回来、或把 `hold_ms` 调回 30，**产品照样能用，只是每次发送白等 90ms+**，
# 没有任何东西会报警。⇒ 用判据把它们钉住。
#
# ⛔ 判据必须**剥注释**再查源码：我在 `_wake` 的文档字符串里**逐字写了 `time.sleep(0.08)`**
#    （为了留痕"为什么删"）⇒ 不剥注释就会把自己写的说明当成"sleep 还在"，假红。
import ast as _ast # noqa: E402
import inspect as _inspect # noqa: E402
import logging



def _code_only(src: str) -> str:
    """把源码里的**注释与文档字符串**全部剥掉，只留可执行代码（AST 往返）。

    （`busy_quns_selftest` 里有同名同用途的函数；这里另写一份是因为两个判据脚本互相独立，
      不该为了共用一个小工具而互相 import。）
    """
    tree = _ast.parse(src)
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef, _ast.Module)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], _ast.Expr) and isinstance(body[0].value, _ast.Constant) \
               and isinstance(body[0].value.value, str):
                node.body = body[1:] or [_ast.Pass()]
    return _ast.unparse(tree)


_IB_SRC = open(os.path.join(ROOT, "agent", "input_backend.py"), encoding="utf-8").read()
_IB_CODE = _code_only(_IB_SRC)

# T1：`_wake` 里**不许再有 sleep**（真机实测：删掉后 5/5 真发出，耗时 201.7→18.4ms）
# ⛔ 必须在**剥过注释/文档字符串**的源码（`_IB_CODE`）上找 —— 否则我在 `_wake` 的 docstring 里
#    逐字写的 `time.sleep(0.08)`（用来留痕"为什么删"）会被当成"sleep 还在"⇒ 假红。
_wake_src = ""
try:
    _tree = _ast.parse(_IB_CODE)
    for _n in _ast.walk(_tree):
        if isinstance(_n, _ast.FunctionDef) and _n.name == "_wake":
            _wake_src = _ast.unparse(_n)
except Exception:
    pass
ck("T1 `_wake` 里不再有 `time.sleep`（提速：删掉 93ms 纯白等）",
   bool(_wake_src) and "sleep" not in _wake_src, _wake_src[:80])
ck("T1b 反向锚：`_wake` 段确实取到了（不是「取空所以通过」）",
   "_post" in _wake_src and "WM_ACTIVATE" in _wake_src, _wake_src[:60])

# T2/T3：签名默认值
_sig_keys = _inspect.signature(IB.MessageBackend.keys)
ck("T2 `keys` 默认 hold_ms=5（原 30 ⇒ 单键省 29ms；真机 5/5 真发出）",
   _sig_keys.parameters["hold_ms"].default == 5, str(_sig_keys))
ck("T3 `keys` 有 `wake` 开关且**默认 True**（独立调用时必须自唤醒，这个前提不能省）",
   "wake" in _sig_keys.parameters and _sig_keys.parameters["wake"].default is True, str(_sig_keys))

# T4：行为级 —— `wake=False` 真的不再发激活消息（用桩记录）
sent2 = []
_saved_post = IB._post
IB._post = lambda hwnd, msg, w, l: sent2.append((int(hwnd), int(msg)))
try:
    _bw = IB.MessageBackend(press_ms=0, activate=True)
    _bw.keys(1234, [0x0D], hold_ms=0, wake=True)
    _n_wake = sum(1 for m in sent2 if m[1] == IB.WM_ACTIVATE)
    sent2[:] = []
    _bw.keys(1234, [0x0D], hold_ms=0, wake=False)
    _n_nowake = sum(1 for m in sent2 if m[1] == IB.WM_ACTIVATE)
finally:
    IB._post = _saved_post
ck("T4 `keys(wake=True)` 发激活、`keys(wake=False)` **不发**（同链去重才有意义）",
   _n_wake >= 1 and _n_nowake == 0, "wake=True 时激活=%d，wake=False 时激活=%d" % (_n_wake, _n_nowake))

# T5：调用侧真的接上了 —— `_send_text_fast` 的回车必须传 `wake=False`
_WX_SRC = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
ck("T5 发送链的回车真的传了 `wake=False`（否则判据绿了、产品还在白等）",
   "VK_RETURN], wake=False" in _WX_SRC or "VK_RETURN], wake=False)" in _WX_SRC,
   "命中=%d" % _WX_SRC.count("wake=False"))

# T6：**全类核对** —— 同类方法的前置动作必须一致（"少数派"往往就是漏掉的那一个）
#   实测来源：`click`/`wheel`/`paste` 都调 `self._wake(hwnd)`，**只有 `send_text` 没调** ⇒
#   顶层窗投 `WM_CHAR` 绿占比 **0.0000**（字没进去）；补上后 **0.6291**（报告 §62）。
#   ⛔ 这类"同类里漏一个"不能靠人眼抽查：把 `MessageBackend` 的**每个公开方法**都过一遍，
#     有 `self._wake(` 或显式 `wake` 开关才算"前置动作一致"。
def _wake_parity(cls_name: str, src: str):
    """返回 (有唤醒/有开关的方法, 缺唤醒的方法)。纯 AST，不执行任何代码。"""
    try:
        _t = _ast.parse(src)
    except Exception: # noqa: BLE001
        return [], []
    cls = next((n for n in _t.body
                if isinstance(n, _ast.ClassDef) and n.name == cls_name), None)
    if cls is None:
        return [], []
    has, miss = [], []
    for n in cls.body:
        if not isinstance(n, _ast.FunctionDef) or n.name.startswith("_"):
            continue
        body = _ast.unparse(n)
        args = list(n.args.args) + list(getattr(n.args, "posonlyargs", [])) + list(n.args.kwonlyargs)
        if "self._wake(" in body or "wake" in [a.arg for a in args]:
            has.append(n.name)
        else:
            miss.append(n.name)
    return has, miss


_has_wake, _miss_wake = _wake_parity("MessageBackend", _IB_CODE)
ck("T6 分母守卫：真的取到了 MessageBackend 的公开方法（否则 T6a 测的是空气）",
   len(_has_wake) + len(_miss_wake) >= 5,
   "取到 %d 个：%s" % (len(_has_wake) + len(_miss_wake), sorted(_has_wake + _miss_wake)))
ck("T6a ★ 同类方法前置动作一致：每个公开方法都有 `self._wake(`（或显式 `wake` 开关）",
   not _miss_wake, "缺唤醒的：%s" % (_miss_wake or "无"))

# T6b 反向锚：合成一个「漏唤醒」的方法 ⇒ 分档器必须报出来（否则 T6a 恒真）
_SYN = ("class MessageBackend:\n"
        "    def click(self, h):\n        self._wake(h)\n        return 1\n"
        "    def oops(self, h):\n        return 2\n")
_h2, _m2 = _wake_parity("MessageBackend", _SYN)
ck("T6b 反向锚：合成的「漏唤醒」方法必须被报出来（证明 T6a 不是恒真）",
   _m2 == ["oops"] and _h2 == ["click"], "has=%s miss=%s" % (_h2, _m2))

print("\n[结论] %d 通过 / %d 失败" % (len(OK), len(BAD)))
if BAD:
    print("失败项：%s" % BAD)
sys.exit(1 if BAD else 0)
