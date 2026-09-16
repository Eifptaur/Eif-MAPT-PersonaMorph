# -*- coding: utf-8 -*-
"""OCR 硬超时 / 熔断 / 时间窗 判据（2026-09-14，测机手册 ④）。

背景：本机某次 OCR 步骤卡了 **8 分 19 秒**（不是 8 秒）。根因在驱动库
`wechatauto.guia.ScreenOCR.recognize`：`asyncio.run(asyncio.wait_for(_run(), timeout=8))` 的 8 秒是**软**的
——`wait_for` 只能取消 asyncio 任务，里面 await 的 WinRT `IAsyncOperation` 不响应取消时，
`asyncio.run` 收尾会一直等下去。⇒ 我们这边**绝不在调用方线程里直接跑它**，改成 daemon 线程 + 硬 join。

本判据钉住五件事（全部脱机，不需要微信）：
  A. **硬超时真的硬**：OCR 卡住时 `recognize()` 在 timeout_s+余量内返回 `[]`（不是"等到天荒地老"）；
  B. **熔断**：连续超时达 BREAK_AFTER 次后停止再试，下一次调用**立刻**返回并说明"熔断中"；
  C. **时间窗**：`begin_window()` 用完 ⇒ 返回空并记 `budget_hits`；**窗口自动失效** ⇒ 之后恢复正常；
  D. **合并点**：`current_chat_name` / `capture_best` / `find_row_scrolled` 在自己的预算内收手，
     并把"OCR 卡住"写进返回说明（调用方据此按"判据不可用"处理，而不是"画面上没有"）；
  E. **接线**：发送链三入口 + 内容级核对都开了窗；`ScreenOCR.recognize` 只在 `_run_hard` 里被调。

用法：py -3 scripts/ocr_timeout_selftest.py
"""
import os
import sys
import time
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

os.environ["WXAGENT_OCR_TIMEOUT"] = "1"      # 单次硬上限 1 秒（clamp 下限就是 1s）
os.environ["WXAGENT_OCR_COOLDOWN"] = "1"     # 熔断只持续 1 秒，自检不等待

# —— 假 OCR 引擎：必须在 import chat_ocr 之前塞进 sys.modules，让 recognize 的局部 import 拿到它 ——
_hang = {"on": True, "sleep": 30.0, "calls": 0}


class _FakeOCR:
    @staticmethod
    def recognize(image):
        _hang["calls"] += 1
        if _hang["on"]:
            time.sleep(float(_hang["sleep"]))        # 模拟"取消不掉的 WinRT 原生调用"
        return [("文件传输助手", 10, 10, 80, 20)]


_fake_pkg = types.ModuleType("wechatauto")
_fake_guia = types.ModuleType("wechatauto.guia")
_fake_guia.ScreenOCR = _FakeOCR
_fake_pkg.guia = _fake_guia
sys.modules.setdefault("wechatauto", _fake_pkg)
sys.modules["wechatauto.guia"] = _fake_guia

from agent import chat_ocr as co              # noqa: E402
from PIL import Image                         # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


def _fresh():
    co.reset_health()
    _hang["on"] = True
    _hang["sleep"] = 30.0
    _hang["calls"] = 0


IMG = Image.new("RGB", (400, 300), (250, 250, 250))

print("── A. 硬超时真的硬（调用方不会被无限拖住）──")
_fresh()
t0 = time.monotonic()
res = co.recognize(IMG)
el = time.monotonic() - t0
ok("卡住时 recognize 返回空", res == [], repr(res[:3]))
ok("在 timeout_s 附近返回（<4s，不是 30s）", el < 4.0, "耗时 %.2fs" % el)
ok("超时被记进 health()", co.health()["timeouts"] >= 1, str(co.health()["timeouts"]))
ok("原因串写明超时", "超时" in co.health()["last_why"], co.health()["last_why"])
ok("recent_timeout() 为真（调用方可据此判「判据不可用」）", co.recent_timeout() is True)
ok("健康快照带 open/timeout_s 字段", co.health()["open"] is False and co.health()["timeout_s"] == 1.0)

print("── B. 熔断：连续超时后不再硬等 ──")
_fresh()
co.recognize(IMG)                              # 第 1 次超时
co.recognize(IMG)                              # 第 2 次超时 ⇒ 达 BREAK_AFTER ⇒ 熔断
h = co.health()
ok("熔断已拉起", h["open"] is True, str(h["open_until"]))
ok("熔断计数 =1", h["breaks"] == 1, str(h["breaks"]))
t0 = time.monotonic()
r3 = co.recognize(IMG)
el3 = time.monotonic() - t0
ok("熔断期间立刻返回空（<0.3s，一次都不试）", r3 == [] and el3 < 0.3, "耗时 %.3fs" % el3)
ok("熔断期间不再调假引擎", _hang["calls"] == 2, "calls=%d" % _hang["calls"])
ok("说明里有「熔断」", "熔断" in co.health()["last_why"], co.health()["last_why"])
ok("blocked() 给出原因", "熔断" in co.blocked(), co.blocked())

print("── C. 时间窗：窗内限时、窗过期不拖累后续（不产生假失败）──")
_fresh()
_hang["on"] = False
_hang["sleep"] = 0.0
tok = co.begin_window(0.4)
ok("窗内 window_left 有值", co.window_left() is not None and co.window_left() <= 0.4 + 1e-6,
   # ⚠️ 2026-09-16：原来写死 `<= 0.4`，而 `window_left()` ＝ `tok - time.monotonic()`，两个量都在
   #    1e6 量级（Windows 单调钟从开机算起）⇒ 浮点相减有 ~1e-10 误差，实测真报过
   #    `0.40000000002328306 > 0.4` 的**假红**。自检守的是"窗内剩余不超过开窗时长"这个**性质**，
   #    容差 1e-6 比浮点误差大 4 个量级、比真实的 0.1s 越界小 5 个量级。
   str(co.window_left()))
ok("窗还没过期时 budget_out=False", co.budget_out(tok) is False)
time.sleep(0.5)                                # 等窗过期
ok("窗过期 ⇒ budget_out=True（开窗的人据此收手）", co.budget_out(tok) is True)
ok("窗过期即被清掉（window_left() is None）", co.window_left() is None)
r2 = co.recognize(IMG)
ok("过期窗**不**让后续 OCR 假失败（仍能读到文字）", bool(r2), repr(r2[:2]))
ok("过期窗不计 budget_hits（只有真收手才算）", co.health()["budget_hits"] == 0,
   str(co.health()["budget_hits"]))
tok2 = co.begin_window(0.4)
time.sleep(0.5)
r3 = co.recognize(IMG)
ok("窗过期后紧接着的那一次 OCR 也照常", bool(r3), repr(r3[:2]))
_fresh()
_hang["on"] = True
co.begin_window(1.0)                           # 窗里只剩不到一片（< WINDOW_MIN_SLICE_S=2s）
t0 = time.monotonic()
r4 = co.recognize(IMG)
el4 = time.monotonic() - t0
ok("余量太小 ⇒ 直接按预算用尽返回（不烧满一次超时）", r4 == [] and el4 < 0.2 and co.health()["timeouts"] == 0,
   "耗时 %.3fs timeouts=%d" % (el4, co.health()["timeouts"]))

print("── D. 合并点在自己预算内收手 ──")
_fresh()
t0 = time.monotonic()
name, why = co.current_chat_name(img=IMG, budget_s=3.0)
el = time.monotonic() - t0
ok("current_chat_name 不超预算（<4s）", el < 4.0, "耗时 %.2fs" % el)
ok("没拿到名字（fail-closed 侧）", name == "", repr(name))
ok("说明里点明 OCR 硬超时", "超时" in why, why)

_fresh()
_hang["sleep"] = 0.6
t0 = time.monotonic()
best = co.capture_best(img=IMG, frames=3, budget_s=3.0)
el = time.monotonic() - t0
ok("capture_best 不超预算（<4s）", el < 4.0, "耗时 %.2fs" % el)
ok("capture_best 超预算时记 budget_hits", co.health()["budget_hits"] >= 1, str(co.health()["budget_hits"]))

_fresh()
_hang["on"] = False
info, log = co.find_row_scrolled(capture_fn=lambda: IMG, find_fn=lambda im: None,
                                 scroll_fn=lambda n: True, max_steps=6,
                                 settle_s=0.2, gap_s=0.1, budget_s=0.5)
ok("find_row_scrolled 到点即停", "预算用尽" in log, log)
ok("并且返回 None（不假报命中）", info is None, repr(info))

print("── E. 接线：发送链开窗 + 唯一收口点 ──")
src_ocr = open(os.path.join(ROOT, "agent", "chat_ocr.py"), encoding="utf-8").read()
src_wx = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
ok("recognize 走 _run_hard（daemon 线程 + join）", "def _run_hard" in src_ocr and "_run_hard(lambda" in src_ocr)
_hits = [l for l in src_ocr.splitlines() if "ScreenOCR.recognize" in l]
ok("唯一直接调用点就是 _run_hard 里那一处",
   len([l for l in _hits if "lambda" in l]) == 1, "%d 行提及，其中 %d 行是调用" % (
       len(_hits), len([l for l in _hits if "lambda" in l])))
for fn in ("def send_text(", "def send_text_posted(", "def send_file_posted(", "def chat_identity_ok("):
    i = src_wx.find(fn)
    seg = src_wx[i:i + 2600] if i >= 0 else ""
    ok("%s 开了 OCR 时间窗" % fn.split("(")[0][4:], "begin_window" in seg)
ok("默认单次上限 25 秒", co.DEFAULT_TIMEOUT_S == 25.0, str(co.DEFAULT_TIMEOUT_S))
ok("发送链总窗 60 秒", co.SEND_WINDOW_S == 60.0, str(co.SEND_WINDOW_S))
ok("BREAK_AFTER=2 / COOLDOWN=120", co.BREAK_AFTER == 2 and co.BREAK_COOLDOWN_S == 120.0)
ok("环境变量可覆盖单次上限", co.timeout_s() == 1.0, str(co.timeout_s()))
ok("health() 带「还有多少秒自恢复」", "open_left" in co.health())

print("── F. UI/文案映射：一键体检里能看到 OCR 健康度 ──")
_fresh()
_s, _d, _h = co.health_line()
ok("没用过时是正常态", _s == "ok" and "从没卡住" in _d, "%s / %s" % (_s, _d))
_hang["on"] = True
co.recognize(IMG)                              # 一次超时（未到熔断）
_s2, _d2, _h2 = co.health_line()
ok("有超时 ⇒ warn 且给出原因", _s2 == "warn" and "卡住过 1 次" in _d2, "%s / %s" % (_s2, _d2))
ok("超时态提示「判据不可用 / 不发送」", "判据不可用" in _h2 and "不发送" in _h2, _h2)
co.recognize(IMG)                              # 第 2 次 ⇒ 熔断
_s3, _d3, _h3 = co.health_line()
ok("熔断 ⇒ warn 且写清多久自恢复", _s3 == "warn" and "正在熔断" in _d3 and "秒后自动恢复" in _d3, "%s / %s" % (_s3, _d3))
ok("health_line 三态文案都能格式化（不抛异常）", all(x for x in (_d, _d2, _d3)))
src_pm = open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
ok("体检项「识别·OCR 卡顿」已接", "识别·OCR 卡顿" in src_pm)
ok("体检项读的是 chat_ocr.health_line()", "from agent import chat_ocr as _co" in src_pm and "_co.health_line()" in src_pm)

print("\n通过 %d / 失败 %d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
