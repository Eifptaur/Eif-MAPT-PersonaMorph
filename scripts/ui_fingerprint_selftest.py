# -*- coding: utf-8 -*-
"""图标指纹表判据（⑦ 点击正确性 / W7c）——**不需要微信在跑**，全部是逻辑级 + 一条"不许盲点"的反面判据。

四组：
  A dHash 数学：同图距离 0、位移 1px 仍很近、不同图案拉得开、无效输入返回最远；
  B 钥匙与存取：key 含 微信版本 / 适配版本 / 渲染尺寸 / DPI 四样；原子写 + 坏文件不炸；
  C 自校验三态：对得上=True · 明确对不上=False（且话术说清"不点"+"重新取指纹"）· 没记录/判据瞎=None；
  D 接线与诚实：hit() 在指纹 False 时**绝不调用真实点击**（探针）；控制台有这颗开关与状态；模块里没有真实输入 API。

用法：py -3 scripts\\ui_fingerprint_selftest.py   （非零退出＝有失败）
"""
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name, (" · " + extra) if extra else ""))


from PIL import Image, ImageDraw        # noqa: E402

from agent import ui_fingerprint as UFP  # noqa: E402

# ── A dHash 数学 ─────────────────────────────────────────────────────────
print("[A] dHash（位移稳、异图拉得开）")


def _icon(kind="a", dx=0, dy=0):
    """造一个"图标样"的 48×48 图：几个亮块 + 渐变，可平移。"""
    im = Image.new("L", (48, 48), 30)
    d = ImageDraw.Draw(im)
    if kind == "a":
        d.rectangle([10 + dx, 10 + dy, 20 + dx, 20 + dy], fill=230)
        d.rectangle([26 + dx, 24 + dy, 40 + dx, 34 + dy], fill=180)
    elif kind == "b":
        d.ellipse([8 + dx, 26 + dy, 26 + dx, 44 + dy], fill=210)
        d.rectangle([28 + dx, 8 + dy, 42 + dx, 16 + dy], fill=150)
    elif kind == "blank":
        pass
    return im


h_a = UFP.dhash(_icon("a"))
ck("A1 同图距离 = 0", UFP.dist(h_a, h_a) == 0, h_a)
ck("A2 位移 1px 仍算「同一图标」（距离 ≤ 阈值）",
   0 < UFP.dist(h_a, UFP.dhash(_icon("a", 1, 0))) <= UFP.MAX_DIST,
   "dist=%d" % UFP.dist(h_a, UFP.dhash(_icon("a", 1, 0))))
ck("A3 不同图案拉得开（距离 > 阈值）",
   UFP.dist(h_a, UFP.dhash(_icon("b"))) > UFP.MAX_DIST,
   "dist=%d" % UFP.dist(h_a, UFP.dhash(_icon("b"))))
ck("A4 空/坏输入返回最远（64），不当成「一样」",
   UFP.dist("", h_a) == 64 and UFP.dist(h_a, "zz") == 64 and UFP.dhash(None) == "")
ck("A5 指纹是 16 位 hex（64 bit）", len(h_a) == 16 and all(c in "0123456789abcdef" for c in h_a))

# ── B 钥匙与存取 ─────────────────────────────────────────────────────────
print("[B] 钥匙（版本×尺寸×DPI）与存取")


class _G:
    main_hwnd = 0
    render_rect = (100, 100, 1300, 1000)
    origin_x = 0
    origin_y = 0


k1 = UFP.key(_G(), wx_ver="4.1.15.8", ad_ver="1.2.2.2")
ck("B1 key 含微信版本", "wx4.1.15.8" in k1, k1)
ck("B2 key 含适配层版本", "ad1.2.2.2" in k1)
ck("B3 key 含渲染尺寸", "1200x900" in k1)
ck("B4 key 含 DPI", "dpi" in k1)
k2 = UFP.key(_G(), wx_ver="4.1.16.0", ad_ver="1.2.2.2")
ck("B5 微信版本变了就是另一把钥匙（旧指纹不参与比对）", k1 != k2)

_tmp = os.path.join(tempfile.mkdtemp(prefix="ufp_"), "fp.json")
ck("B6 原子写 + 读回一致", UFP.save({"k": {"x": {"hash": "deadbeefdeadbeef"}}}, _tmp)
   and UFP.load(_tmp)["k"]["x"]["hash"] == "deadbeefdeadbeef")
io.open(_tmp, "w", encoding="utf-8").write("{ 坏 json")
ck("B7 坏文件不炸（当成空表）", UFP.load(_tmp) == {})
ck("B8 表不存在也不炸", UFP.load(os.path.join(tempfile.mkdtemp(), "nope.json")) == {})

# ── C 自校验三态 ─────────────────────────────────────────────────────────
print("[C] 自校验三态（放行 / 拦住 / 不判定）")
_orig_load, _orig_capture, _orig_key = UFP.load, UFP.capture, UFP.key
FAKE_KEY = "wxTEST|adTEST|100x100|dpi96"
UFP.key = lambda *a, **k: FAKE_KEY
try:
    good = UFP.dhash(_icon("a"))
    UFP.load = lambda path=None: {FAKE_KEY: {"sidebar.moments": {"hash": good, "pt": [10, 10]}}}
    UFP.capture = lambda gui, name, pts=None: {"ok": True, "hash": UFP.dhash(_icon("a"))}
    v1, i1 = UFP.verify(_G(), "sidebar.moments")
    ck("C1 指纹一致 ⇒ 放行", v1 is True and i1["dist"] <= UFP.MAX_DIST, i1["reason"])

    UFP.capture = lambda gui, name, pts=None: {"ok": True, "hash": UFP.dhash(_icon("b"))}
    v2, i2 = UFP.verify(_G(), "sidebar.moments")
    ck("C2 指纹对不上 ⇒ 拦住（False，而不是 None）", v2 is False, "dist=%s" % i2["dist"])
    ck("C3 拦住的话术说清「不点」+「重新取指纹」",
       "不点" in i2["reason"] and "重新取指纹" in i2["reason"], i2["reason"][:70])

    UFP.capture = lambda gui, name, pts=None: {"ok": False, "why": "取不到渲染区画面（窗口最小化）"}
    v3, i3 = UFP.verify(_G(), "sidebar.moments")
    ck("C4 判据不可用 ⇒ 不冒充「对不上」（None 且说明原因）",
       v3 is None and "判据不可用" in i3["reason"], i3["reason"][:60])

    UFP.load = lambda path=None: {}
    v4, i4 = UFP.verify(_G(), "sidebar.moments")
    ck("C5 没记录 ⇒ 放行但留痕（不把第一次用锁死）", v4 is None and "留痕" in i4["reason"], i4["reason"][:60])

    # 取不到画面 / 画面全黑 ⇒ 不许写指纹（拿不到就是拿不到，不编一条）
    UFP.load = _orig_load
    UFP.capture = _orig_capture
    _orig_grab = UFP._grab
    try:
        UFP._grab = lambda gui: None
        c1 = UFP.capture(_G(), "sidebar.moments", pts={"sidebar.moments": (10, 10)})
        ck("C6 抓不到画面 ⇒ 不写指纹（ok=False，理由说明）",
           c1.get("ok") is False and "取不到" in str(c1.get("why")), str(c1.get("why"))[:60])
        UFP._grab = lambda gui: Image.new("L", (200, 200), 20)
        c2 = UFP.capture(_G(), "sidebar.moments", pts={"sidebar.moments": (10, 10)})
        ck("C7 画面近乎空白 ⇒ 不写指纹（不拿空白当「图标」）",
           c2.get("ok") is False and "空白" in str(c2.get("why")), str(c2.get("why"))[:60])
        UFP._grab = lambda gui: _icon("a")
        c3 = UFP.capture(_G(), "sidebar.moments", pts={"sidebar.moments": (24, 24)})
        ck("C8 正常画面 ⇒ 取到指纹（hash/pt/ts 齐全）",
           c3.get("ok") is True and len(c3.get("hash", "")) == 16 and c3.get("pt") and c3.get("ts"),
           str({k: c3[k] for k in ("hash", "pt", "ts") if k in c3}))
    finally:
        UFP._grab = _orig_grab
finally:
    UFP.load, UFP.capture, UFP.key = _orig_load, _orig_capture, _orig_key

# ── D 接线与诚实 ─────────────────────────────────────────────────────────
print("[D] 接线：指纹对不上时**不许盲点**")
from agent import ui_adapt as UA           # noqa: E402
from agent import wechat_ui as WU          # noqa: E402
from agent import ui_fingerprint as UFP2   # noqa: E402

CLICKED = []
_o_icon_pos, _o_click, _o_prepare = WU.icon_pos, UA.click, UA.prepare_screen
_o_verify = UFP2.verify
try:
    WU.icon_pos = lambda name, gui: (500, 500)
    UA.click = lambda *a, **k: (CLICKED.append(a), (True, ""))[1]
    UA.prepare_screen = lambda gui: True
    UFP2.verify = lambda gui, name, max_dist=UFP2.MAX_DIST: (False, {"reason": "指纹对不上（测试）"})
    ok1, m1 = WU.hit("sidebar.moments", _G())
    ck("D1 指纹对不上 ⇒ hit 返回失败", ok1 is False, m1[:50])
    ck("D2 **全程没有发生真实点击**（探针记录 %d 次）" % len(CLICKED), not CLICKED)
    UFP2.verify = lambda gui, name, max_dist=UFP2.MAX_DIST: (True, {"reason": "ok"})
    CLICKED[:] = []
    ok2, m2 = WU.hit("sidebar.moments", _G())
    ck("D3 指纹一致 ⇒ 照常点击", ok2 is True and len(CLICKED) == 1, m2)
    UFP2.verify = lambda gui, name, max_dist=UFP2.MAX_DIST: (None, {"reason": "没记录"})
    CLICKED[:] = []
    ok3, _m3 = WU.hit("sidebar.moments", _G())
    ck("D4 没记录 ⇒ 放行（不锁死）", ok3 is True and len(CLICKED) == 1)
finally:
    WU.icon_pos, UA.click, UA.prepare_screen = _o_icon_pos, _o_click, _o_prepare
    UFP2.verify = _o_verify

SRC_UFP = io.open(os.path.join(ROOT, "agent", "ui_fingerprint.py"), encoding="utf-8").read()
import re
_real = re.findall(r"\.\s*(mouse_event|SetCursorPos|SendInput|keybd_event)\s*\(", SRC_UFP)
ck("D5 指纹模块自己绝不动鼠标（无真实输入 API 调用）", not _real, str(_real))
ck("D6 模块只「看」不「点」（没有 click/SendMessage 之类）",
   "mouse_event" not in SRC_UFP and "SendMessage" not in SRC_UFP)

SRC_WU = io.open(os.path.join(ROOT, "agent", "wechat_ui.py"), encoding="utf-8").read()
ck("D7 hit() 接了自校验（点前先比指纹）",
   "ui_fingerprint" in SRC_WU and "verdict is False" in SRC_WU)

SRC_C = io.open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
SRC_W = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
ck("D8 控制台有指纹行 + 两颗按钮", all(s in SRC_C for s in ('id="ufpHead"', 'id="ufpTake"', 'id="ufpForget"')))
ck("D9 后端有取指纹 / 丢指纹两个路由",
   '"/api/ui_fingerprint/take"' in SRC_W and '"/api/ui_fingerprint/forget"' in SRC_W)
ck("D10 /api/status 暴露指纹状态", 'st["ui_fp"] = _ufp.hits()' in SRC_W)
ck("D11 控制台把「对不上就不点」写在界面上（用户看得见为什么没点）",
   "指纹" in SRC_C and "就停手" in SRC_C)

print("\n[结论] %d 通过 / %d 失败" % (len(OK), len(BAD)))
if BAD:
    print("失败项：%s" % BAD)
sys.exit(1 if BAD else 0)
