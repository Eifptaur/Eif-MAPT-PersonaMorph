# -*- coding: utf-8 -*-
"""**点后回填**判据：点击效果探针 + 点击账本（\"点准率\"的唯一事实来源）。

守四件事：
  A. **探针判别力**：没变 ⇒ False；明显变了 ⇒ True；缺帧 ⇒ **None（判不了）**；
     **纯色变化（蓝底↔白底）必须判得出** —— 这是第一版用 dHash 时踩的坑：dHash 只看梯度，
     纯色块哈希恒为 0 ⇒ 颜色变了也判"没变"（探针等于瞎，冒烟测试当场抓到）。
  B. **账本口径**：成功率只按 `ok/fail`；**`unknown` 不进分母**；失败必须带原因；
     有界（每目标 ≤ MAX_SAMPLES）；线程安全；落盘跨重启。
  C. ★ **接线真的生效**（防"接上了但永远不生效"）：`wechat_ui.hit()` 点完会调回填、
     会把结果写进账本、且**抓点后帧前必须清帧缓存**（否则拿同一帧自比 ⇒ 永远判"没变"）。
  D. **只观测、不改判定**（本轮不动点击成败结论，先攒数据）—— 源码锚。

跑法：`runtime\\python\\python.exe scripts\\click_ledger_selftest.py`
"""
import io
import os
import sys
import tempfile
import threading

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
os.environ.setdefault("PM_NO_WINDOW_TOUCH", "1")
import _srcmatch as _sm # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


from PIL import Image, ImageDraw # noqa: E402

from agent import click_effect as CE # noqa: E402
from agent import click_ledger as CL # noqa: E402


def frame(mode="same"):
    im = Image.new("RGB", (640, 480), (246, 246, 248))
    d = ImageDraw.Draw(im)
    for i in range(6):
        d.rectangle([40, 40 + i * 40, 300, 70 + i * 40], fill=(200, 200 + i * 6, 220))
    if mode == "changed":            # 明显变化（深色面板弹出来）
        d.rectangle([320, 120, 620, 420], fill=(30, 120, 220))
    if mode == "flat_only":          # ⛔ 只用**纯色**变化（dHash 看不见的那种：纯色块哈希恒 0）
        d.rectangle([320, 120, 620, 420], fill=(20, 90, 200))   # 深蓝纯色：无梯度，但明暗差很大
    if mode == "jitter":             # 单块内 1 个灰度单位的抖动（噪声）
        d.rectangle([40, 40, 300, 70], fill=(201, 200, 220))
    return im


print("── A. 探针判别力 ──")
for mode, want in (("same", False), ("changed", True), ("flat_only", True), ("jitter", False)):
    v, info = CE.probe(frame(), frame(mode))
    ok("A %-9s ⇒ %s（期望 %s）" % (mode, v, want), v is want, info["reason"][:44])
v, info = CE.probe(None, frame())
ok("A 缺帧 ⇒ **None（判不了）**，绝不冒充成功", v is None, info["reason"][:30])
v, _i = CE.probe(frame(), None)
ok("A 后帧缺失 ⇒ 同样是 None", v is None)

print("\n── B. 账本口径 ──")
_tmp = os.path.join(tempfile.mkdtemp(prefix="click_ledger_"), "led.json")
_real_path = CL.PATH
try:
    CL.PATH = _tmp
    CL.clear()
    CL.record("env1", "sidebar.chats", "ok", "效果发生了")
    CL.record("env1", "sidebar.chats", "fail", "没变")
    CL.record("env1", "sidebar.chats", "unknown", "抓不到画面")
    s = CL.record("env1", "sidebar.chats", "ok", "")
    ok("B1 成功率只按 ok/fail 算（2/3 = 0.667，unknown 不进分母）",
       s["n"] == 4 and s["ok"] == 2 and s["fail"] == 1 and s["unknown"] == 1
       and abs(s["rate"] - 2 / 3.0) < 1e-6, str(s))
    ok("B2 失败必须带原因（取最近一次失败的原因）", s["last_fail"] == "没变", s["last_fail"])
    ok("B3 全是 unknown 时 rate 为 None（**不许**报成 0% 或 100%）",
       CL.record("env1", "sidebar.contacts", "unknown", "抓不到")["rate"] is None)
    for i in range(CL.MAX_SAMPLES + 25):
        CL.record("env1", "sidebar.favorites", "ok", "第 %d 次" % i)
    _f = CL.stats("env1")["env1|sidebar.favorites"]
    ok("B4 有界：每目标只留最近 %d 条样本" % CL.MAX_SAMPLES, _f["n"] == CL.MAX_SAMPLES, "n=%d" % _f["n"])
    _errs = []
    def _w():
        try:
            for _ in range(20):
                CL.record("env2", "send.button", "ok", "")
        except Exception as e:  # noqa: BLE001
            _errs.append(str(e)[:50])
    _ts = [threading.Thread(target=_w) for _ in range(6)]
    [t.start() for t in _ts]
    [t.join() for t in _ts]
    ok("B5 线程安全：6 线程各写 20 条不炸、账本格式没坏",
       not _errs and CL.stats("env2")["env2|send.button"]["ok"] >= 20, str(_errs[:1]))
    _hit = CL.worst(limit=3, min_n=1)
    ok("B6 能列出「最该修的目标」（样本够、成功率最低优先）", bool(_hit), str(_hit[:1])[:80])
    ok("B7 落盘：文件真的写出来了（跨重启仍在）", os.path.exists(_tmp), _tmp[-40:])
    _db = CL._load()
    ok("B8 落盘内容可读回且结构正确", isinstance(_db, dict) and "env1" in _db)
finally:
    CL.PATH = _real_path

print("\n── C. 接线真的生效（防「接上但永远不生效」）──")
_SRC = io.open(os.path.join(ROOT, "agent", "wechat_ui.py"), encoding="utf-8").read()
ok("C1 源码锚：hit() 点完后调用回填（`_note_click_effect`）",
   _sm.has(_SRC, "_note = _note_click_effect(gui, name, _before)"))
ok("C2 ★ 源码锚：**先清帧缓存再抓点后帧**（不清就会拿同一帧自比 ⇒ 永远判「没变」）",
   _SRC.find("reset_frame_cache()") < _SRC.find("after = _ufp.grab_frame(gui)")
   and _SRC.find("reset_frame_cache()") > 0)
ok("C3 ★ 源码锚：**只观测、不改判定**（点击成功与否只看 ok2；回填结果只拼进文案）",
   _sm.has(_SRC, "if ok2:") and _sm.has(_SRC, 'return True, "已点击 %s%s" % (name, _note)'))

from agent import wechat_ui as WU # noqa: E402
ok("C4 `_note_click_effect` 真的存在且可调用", callable(getattr(WU, "_note_click_effect", None)))
try:
    from agent import ui_fingerprint as _ufp # noqa: E402
    _real_grab, _real_key, _real_cfg = _ufp.grab_frame, _ufp.key, WU.get_config
    try:
        CL.PATH = _tmp
        CL.clear()
        # ⛔ 桩要认清调用序：`before` 是**传进来**的 ⇒ `grab_frame` 只被调一次（取**点后**帧）。
        #    第一版让"第一次调用返回没变"，等于把点后帧设成"没变" ⇒ C5 假红（判据自己写错）。
        #    桩要**可切换**，否则 C6（要点后帧"没变"）会复用 C5 的桩 ⇒ 又假红一次。
        _MODE = {"m": "changed"}
        def _grab(gui, **kw):
            return frame(_MODE["m"])
        _ufp.grab_frame = _grab
        _ufp.reset_frame_cache()
        _ufp.key = lambda *a, **k: "envX"
        WU.get_config = lambda: {"ui": {"click_effect_settle_s": 0.05}}
        _note = WU._note_click_effect(object(), "sidebar.chats", frame("same"))
        _rows = CL.stats("envX")
        ok("C5 ★ 端到端：点后帧与点前不同 ⇒ 记 `ok` 并返回说明",
           "点后" in _note and "envX|sidebar.chats" in _rows
           and _rows["envX|sidebar.chats"]["ok"] == 1, "%s · %s" % (_note[:34], str(_rows)[:50]))
        CL.clear()
        _MODE["m"] = "same"          # C6：点后帧"没变"
        _note2 = WU._note_click_effect(object(), "sidebar.chats", frame("same"))
        ok("C6 端到端：点后帧与点前相同 ⇒ 记 `fail`（不是静默通过）",
           CL.stats("envX")["envX|sidebar.chats"]["fail"] == 1, _note2[:40])
        WU.get_config = lambda: {"ui": {"click_effect_probe": False}}
        _note3 = WU._note_click_effect(object(), "sidebar.chats", frame("same"))
        ok("C7 开关能关掉回填（`ui.click_effect_probe=false` ⇒ 不做探针也不记账）",
           _note3 == "", repr(_note3))
    finally:
        _ufp.grab_frame, _ufp.key, WU.get_config = _real_grab, _real_key, _real_cfg
except Exception as e:  # noqa: BLE001
    ok("C5~C7 端到端回填（异常即失败）", False, str(e)[:60])

print("\n── D. 口径写明白 ──")
_ce = io.open(os.path.join(ROOT, "agent", "click_effect.py"), encoding="utf-8").read()
_cl = io.open(os.path.join(ROOT, "agent", "click_ledger.py"), encoding="utf-8").read()
ok("D1 探针文档写明三态、且**判不了不当成功**", _sm.has(_ce, "判不了"))
ok("D2 探针文档写明「不能用 dHash」的原因（纯色块哈希恒 0）", _sm.has(_ce, "纯色块"))
ok("D3 账本文档写明 `unknown` 不进分母", _sm.has(_cl, "不进成功率分母"))

print("\n点后回填判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
