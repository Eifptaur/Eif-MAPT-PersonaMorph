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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import input_backend as ib   # noqa: E402  （import 即锁 DPI）

OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name, (" · " + extra) if extra else ""))


# ── A 单元判据 ──────────────────────────────────────────────────────────
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
r = b.click(1234, (500, 600), right=True)
ck("B6 投递右键未实测 ⇒ 明确拒绝而不是乱点", r[0] is False and not sent, r[1])

sent[:] = []
ok, why = b.send_text(1234, "ab中")
ck("B7 send_text 逐字 WM_CHAR", [m[1] for m in sent] == [ib.WM_CHAR] * 3)
ck("B8 WM_CHAR 载的是字符码", [m[2] for m in sent] == [ord("a"), ord("b"), ord("中")])
ck("B9 send_text 不发伪激活（与实测口径一致）", ib.WM_ACTIVATE not in [m[1] for m in sent])
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
    ROOT + os.sep + "agent" + os.sep + "ui_adapt.py",      # L0 唯一下沉点（heal_input / 真鼠标 click）
    ROOT + os.sep + "agent" + os.sep + "wechat.py",        # 待收口（21 处）
    ROOT + os.sep + "agent" + os.sep + "wechat_ui.py",     # 待收口（7 处）
    ROOT + os.sep + "scripts" + os.sep + "persona_morph.py",  # 待收口（1 处）
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
            except Exception:
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
from agent import input_backend as IB        # noqa: E402
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

print("\n[结论] %d 通过 / %d 失败" % (len(OK), len(BAD)))
if BAD:
    print("失败项：%s" % BAD)
sys.exit(1 if BAD else 0)
