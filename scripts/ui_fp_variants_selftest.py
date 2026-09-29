# -*- coding: utf-8 -*-
"""**多模板**判据：同一目标存多份指纹（浅/深主题、选中态…）⇒ 少误拒；但**绝不许**变成"什么都放行"。

守这些：
  V1 ★ 单模板的毛病：同一图标换个主题（浅↔深）⇒ 距离超阈值 ⇒ **误判"图标变了"**
  V2 ★ 多模板修好它：两版都存 ⇒ verify 通过（且如实报"命中哪一份"）
  V3 ★★ **反向锚**：变体再多，换成**别的图标**也必须判「不是」（多模板 ≠ 放松判定）
  V4 ★ **类间保护**：新变体若离**别的目标**太近 ⇒ 拒绝入库（否则等于放行别人）
  V5 上限 + 淘汰（不超过 MAX_VARIANTS；淘汰保留"常用"的那份）
  V6 去重（与已有变体太近不重复存）
  V7 **兼容**：只有 `hash` 字段的老记录照旧工作（不破坏既有指纹库与其它判据）
  V8 `take()` 是**合并**：重取保留学到的偏移与旧变体（旧实现会把它们冲掉）

跑法：`runtime\\python\\python.exe scripts\\ui_fp_variants_selftest.py`
"""
import io
import os
import sys
import types

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

from agent import ui_fingerprint as F # noqa: E402


def icon(shape="chat", theme="light", pt=(100, 100)):
    """造一个"图标"：浅色主题＝浅底深纹；深色主题＝深底浅纹（同一形状，视觉差别很大）。"""
    bg = (246, 246, 248) if theme == "light" else (32, 34, 38)
    fg = (30, 120, 220) if theme == "light" else (120, 190, 255)
    ink = (20, 20, 20) if theme == "light" else (235, 235, 240)
    im = Image.new("RGB", (200, 200), bg)
    d = ImageDraw.Draw(im)
    x, y = pt
    if shape == "chat":
        d.rounded_rectangle([x - 28, y - 22, x + 28, y + 18], radius=6, fill=fg)
        for k in range(5):
            d.line([x - 20 + k * 10, y - 12, x - 20 + k * 10, y + 8], fill=ink, width=2)
    else:                                   # 另一个图标（形状明显不同）
        d.ellipse([x - 26, y - 26, x + 26, y + 26], outline=fg, width=6)
        d.line([x - 18, y + 18, x + 18, y - 18], fill=ink, width=4)
    return im


GUI = types.SimpleNamespace(render_rect=(0, 0, 200, 200))
PT = (100, 100)
LIGHT = icon("chat", "light")
DARK = icon("chat", "dark")
OTHER = icon("other", "light")

_real = {k: getattr(F, k) for k in ("key", "load", "save", "_grab")}

print("── V1/V2：单模板的误拒 vs 多模板修好它 ──")
try:
    F.key = lambda *a, **k: "k"
    F._grab = lambda gui: LIGHT
    F.reset_frame_cache()
    h_light = F.capture(GUI, "sidebar.chats", pts={"sidebar.chats": PT})["hash"]
    F.reset_frame_cache()
    F._grab = lambda gui: DARK
    h_dark = F.capture(GUI, "sidebar.chats", pts={"sidebar.chats": PT})["hash"]
    _d_same_theme = F.dist(h_light, h_dark)
    _d_other = F.dist(h_light, F.dhash(OTHER.crop((76, 76, 124, 124))))
    print("   距离：同图标换主题 %d · 换图标 %d（阈值 %d）" % (_d_same_theme, _d_other, F.MAX_DIST))

    F.load = lambda path=None: {"k": {"sidebar.chats": {"hash": h_light, "pt": list(PT)}}}
    F.reset_frame_cache()
    v1, i1 = F.verify(GUI, "sidebar.chats", pts={"sidebar.chats": PT})
    # ⚠️ 这里**不能假设"必然误拒"**：实测容差搜索会在偏移里挑到最像的一版，
    #    暗色帧在 (-3,0) 处与亮色指纹距离只有 5 ⇒ 旧判据（只看"距离≤10"）会**误受**。
    #    所以断言只能写"**不许放行**"（误拒/误受都不行），并打印实际原因。
    ok("V1 ★ 单模板遇到「换主题」时**不许放行**（误拒或误受都不行）",
       v1 is not True, "verdict=%s dist=%s · %s" % (v1, i1.get("dist"), str(i1.get("reason"))[:40]))

    # V1b ★★ 归属锚：这一小片其实**更像别的目标** ⇒ 必须拒绝（哪怕本目标距离 ≤ 阈值）
    F.load = lambda path=None: {"k": {"sidebar.chats": {"hash": h_light, "pt": list(PT)},
                                      "sidebar.other": {"hash": h_dark, "pt": list(PT)}}}
    F.reset_frame_cache()
    v1b, i1b = F.verify(GUI, "sidebar.chats", pts={"sidebar.chats": PT})
    ok("V1b ★★ 归属锚：位置更像**别的目标** ⇒ 拒绝（并说清「更像谁」）",
       v1b is False and "别的目标" in str(i1b.get("reason")),
       "verdict=%s dist=%s d_other=%s" % (v1b, i1b.get("dist"), i1b.get("d_other")))

    _rec = {"hash": h_light, "pt": list(PT)}
    F.add_variant(_rec, h_dark, "theme:dark", "dark")
    F.load = lambda path=None: {"k": {"sidebar.chats": _rec}}
    F.reset_frame_cache()
    v2, i2 = F.verify(GUI, "sidebar.chats", pts={"sidebar.chats": PT})
    ok("V2 ★ 多模板：把深色那份也存上 ⇒ **不再误拒**，并如实报命中哪一份",
       v2 is True and "命中变体" in str(i2.get("reason")), "%s · %s" % (v2, str(i2.get("reason"))[:56]))

    print("\n── V3：★★ 反向锚（变体多 ≠ 什么都放行）──")
    F._grab = lambda gui: OTHER
    F.reset_frame_cache()
    v3, i3 = F.verify(GUI, "sidebar.chats", pts={"sidebar.chats": PT})
    ok("V3 ★★ 换成**别的图标**必须判「不是」（哪怕已存两份变体）",
       v3 is False and (i3.get("dist") or 0) > F.MAX_DIST, "verdict=%s dist=%s" % (v3, i3.get("dist")))
    # 存满 6 份变体后再验一次（防"变体越多越松"）
    _r2 = {"hash": h_light}
    for i in range(12):
        F.add_variant(_r2, "%016x" % (0x1111 * (i + 1)))
    F.load = lambda path=None: {"k": {"sidebar.chats": _r2}}
    F.reset_frame_cache()
    F._grab = lambda gui: OTHER
    v3b, i3b = F.verify(GUI, "sidebar.chats", pts={"sidebar.chats": PT})
    ok("V3b ★★ 变体**存满**之后，别的图标仍必须判「不是」",
       v3b is False, "变体 %d 份 · verdict=%s dist=%s"
       % (F.variant_stats(_r2)["count"], v3b, i3b.get("dist")))
finally:
    for k, v in _real.items():
        setattr(F, k, v)

print("\n── V4~V8：变体的纪律（纯函数，确定性）──")
rec = {"hash": "a" * 16}
r = F.add_variant(rec, "b" * 16, "theme:dark", "dark")
ok("V4a 正常加变体成功", r.get("ok") is True, r.get("why"))
ok("V6 去重：与已有变体太近（≤%d）不重复存" % F.VAR_DUP_DIST,
   F.add_variant(rec, "b" * 16).get("ok") is False)
# 造一个"离别的目标很近"的指纹：与 others 里的某个只差 3 位
_other_h = "c" * 16
_near = "%016x" % (int(_other_h, 16) ^ 0b111)
r4 = F.add_variant(rec, _near, "auto", others=[_other_h])
ok("V4b ★ 类间保护：离**别的目标**太近（≤%d）⇒ 拒绝入库（防什么都像）" % F.INTER_MIN_DIST,
   r4.get("ok") is False and "别的目标" in str(r4.get("why")), str(r4.get("why"))[:52])
# 上限 + 淘汰保常用
rec5 = {"hash": "1" * 16}
rec5["variants"] = [{"h": "%016x" % (0x2222 * (i + 1)), "label": "v%d" % i,
                     "uses": (10 if i == 0 else 0)} for i in range(F.MAX_VARIANTS)]
_before = {v["label"] for v in rec5["variants"]}
F.add_variant(rec5, "f" * 16, "new")
_after = F.variant_stats(rec5)
ok("V5a 上限：变体数不超过 %d" % F.MAX_VARIANTS, _after["count"] <= F.MAX_VARIANTS,
   "count=%d" % _after["count"])
ok("V5b 淘汰保留「常用」的那份（uses=10 的 v0 不许被淘汰）",
   any(i["label"] == "v0" for i in _after["items"]), str([i["label"] for i in _after["items"]]))
ok("V7 兼容：只有 `hash` 的老记录照旧能取到变体（既有指纹库不受影响）",
   F.variants_of({"hash": "9" * 16})[0]["h"] == "9" * 16)

_SRC = io.open(os.path.join(ROOT, "agent", "ui_fingerprint.py"), encoding="utf-8").read()
ok("V8 源码锚：`take()` 是**合并**（保留 learn 与旧变体，不再整条覆盖）",
   _sm.has(_SRC, "保留学到的偏移") and _sm.has(_SRC, 'rec["variants"] = _vs[:MAX_VARIANTS]'))
ok("V8b 源码锚：跨变体取最小距离（命中任一即算对得上）",
   _sm.has(_SRC, "跨变体取**最小**距离"))

print("\n多模板判据：%d 通过 / %d 失败" % (PASS, FAIL))
print("（实测：同图标换主题的距离 %d vs 换图标的距离 %d，阈值 %d）"
      % (_d_same_theme, _d_other, F.MAX_DIST))
sys.exit(1 if FAIL else 0)
