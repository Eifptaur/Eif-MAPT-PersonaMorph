# -*- coding: utf-8 -*-
"""**图标指纹（认图标）**判据：位置容差、一次抓图批量校验、以及"容差≠放宽"的反向锚。

背景（实测，`scripts/ui_fp_measure.py`，355 个非空白位置）：
  · 标定/渲染偏 1~3px ⇒ 类内距离最大 48 > 阈值 10 ⇒ **79% 的位置会被误判成"图标变了"**；
  · 在 ±3px 窗口里取"最像的一版"后 ⇒ 类内最大距离 **0**、误拒 **0/355**。
所以本轮给 `verify` 加了位置容差、并加了"一次抓图批量校验"。这条判据守四件事：

  A. **容差真的生效**：平移 1/2/3px 仍放行；
  B. ★ **容差不是放宽阈值**：平移 10px 必须判"不是"（否则就变成"什么都放行"）；
  C. **一次抓图**：一次批量校验只抓一次整窗（并带 200ms 帧缓存）；
  D. **纪律仍在**：拿不到画面 ⇒ 返回 None（不冒充对不上）；空白画面不写指纹。

跑法：`runtime\\python\\python.exe scripts\\ui_fp_selftest.py`
"""
import io
import os
import sys
import time
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
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


def make_frame(shift=(0, 0), blank=False):
    """造一帧"界面"：几个**各不相同**的假图标（有内部结构，dHash 才分得开）。"""
    im = Image.new("RGB", (640, 480), (246, 246, 248))
    if blank:
        return im
    d = ImageDraw.Draw(im)
    spots = [(80, 80, (30, 120, 220)), (240, 120, (220, 60, 60)),
             (400, 200, (40, 170, 90)), (200, 340, (150, 90, 200))]
    for x, y, col in spots:
        x += shift[0]
        y += shift[1]
        d.rectangle([x - 20, y - 20, x + 20, y + 20], fill=col)
        d.rectangle([x - 12, y - 4, x + 12, y + 12], fill=(255, 255, 255))
        d.line([x - 16, y - 14, x + 16, y - 14], fill=(20, 20, 20), width=3)
        # ⛔ **必须有细结构**：粗色块对 3px 平移根本不敏感（dHash 缩到 9×8 后 3px≈半个格子），
        #    那样"对照组"会假绿。真实图标的细纹才会让"偏一点就完全不像"。
        for k in range(5):
            d.line([x - 18 + k * 7, y - 18, x - 18 + k * 7, y + 18], fill=(10, 10, 10), width=1)
    for i in range(6):
        d.text((40, 420 + i * 3), "row %d 文本内容" % i, fill=(90, 90, 90))
    return im


FRAME = make_frame()
GUI = types.SimpleNamespace(render_rect=(0, 0, 640, 480))
PT = (80, 80)          # 第一个假图标

# 注入：固定的 key、内存里的指纹库、计数抓图
_real = {k: getattr(F, k) for k in ("key", "load", "save", "_grab")}
GRABS = {"n": 0}
try:
    F.reset_frame_cache()
    F.key = lambda *a, **k: "test-key"
    F._grab = lambda gui: (GRABS.__setitem__("n", GRABS["n"] + 1), FRAME)[1]

    ref = F.capture(GUI, "t1", pts={"t1": PT})
    ok("A0 合成帧能取到指纹（非空白）", ref.get("ok") is True, ref.get("why", ""))

    def _db():
        return {"test-key": {"t1": dict(ref, name="t1")}}

    F.load = lambda path=None: _db()

    print("\n── A. 位置容差生效（本轮核心优化）──")
    for s in (1, 2, 3):
        F.reset_frame_cache()
        F._grab = (lambda sh: (lambda gui: (GRABS.__setitem__("n", GRABS["n"] + 1),
                                            make_frame((sh, sh)))[1]))(s)
        v, info = F.verify(GUI, "t1", tol_px=F.TOL_PX, pts={"t1": PT})
        ok("A%d 平移 %dpx 仍放行（容差 %dpx 内）" % (s, s, F.TOL_PX), v is True,
           "%s · %s" % (v, info.get("reason", "")[:52]))
    # 对照：把容差关掉，同样的平移会怎样（证明"容差真的在起作用"）
    F.reset_frame_cache()
    F._grab = lambda gui: make_frame((3, 3))
    v0, i0 = F.verify(GUI, "t1", tol_px=0, pts={"t1": PT})
    ok("A4 对照组：容差关掉（tol_px=0）时同样的 3px 平移**会**判不过（证明容差在起作用）",
       v0 is False, "verdict=%s · dist=%s" % (v0, i0.get("dist")))

    print("\n── B. ★ 反向锚：容差不是放宽阈值 ──")
    for s in (8, 10, 14):
        F.reset_frame_cache()
        F._grab = (lambda sh: (lambda gui: make_frame((sh, sh))))(s)
        v, info = F.verify(GUI, "t1", tol_px=F.TOL_PX, pts={"t1": PT})
        ok("B%d 平移 %dpx（超过容差）必须判「不是」" % (s, s), v is False,
           "verdict=%s · dist=%s" % (v, info.get("dist")))

    print("\n── C. 一次抓图 / 帧缓存 ──")
    F.reset_frame_cache()
    GRABS["n"] = 0
    F._grab = lambda gui: (GRABS.__setitem__("n", GRABS["n"] + 1), FRAME)[1]
    _pts4 = {"t1": (80, 80), "t2": (240, 120), "t3": (400, 200), "t4": (200, 340)}
    out = F.verify_many(GUI, list(_pts4), pts=_pts4)
    ok("C1 ★ 批量校验 4 个目标只抓 **1 次**整窗（以前是 4 次）",
       GRABS["n"] == 1 and len(out) == 4, "抓图 %d 次 · 返回 %d 项" % (GRABS["n"], len(out)))
    F.reset_frame_cache()          # ⛔ 必须先清：上一段把帧缓存喂热了，不清就会数到 0 次
    GRABS["n"] = 0
    F.verify(GUI, "t1", pts={"t1": PT})
    F.verify(GUI, "t1", pts={"t1": PT})
    ok("C2 帧缓存：200ms 内连续两次校验也只抓 1 次", GRABS["n"] == 1, "抓图 %d 次" % GRABS["n"])
    F.reset_frame_cache()
    GRABS["n"] = 0
    F.verify(GUI, "t1", pts={"t1": PT})
    time.sleep(F.FRAME_TTL_MS / 1000.0 + 0.05)
    F.verify(GUI, "t1", pts={"t1": PT})
    ok("C3 帧缓存过期后会重抓（不拿旧画面下结论）", GRABS["n"] == 2, "抓图 %d 次" % GRABS["n"])

    print("\n── D. 纪律仍在（不冒充、不写假指纹）──")
    F.reset_frame_cache()
    F._grab = lambda gui: None
    v, info = F.verify(GUI, "t1", pts={"t1": PT})
    ok("D1 拿不到画面 ⇒ 返回 None（判据不可用，**不冒充**对不上）",
       v is None and "判据不可用" in str(info.get("reason")), str(info.get("reason"))[:48])
    F._grab = lambda gui: make_frame(blank=True)
    cap = F.capture(GUI, "t1", pts={"t1": PT})
    ok("D2 空白画面**不写指纹**（宁可报取不到，也不写一条假的）", cap.get("ok") is False,
       cap.get("why", ""))

    print("\n── E. 口径与常量在位 ──")
    _src = io.open(os.path.join(ROOT, "agent", "ui_fingerprint.py"), encoding="utf-8").read()
    ok("E1 位置容差常量 + 帧缓存 TTL 都在，且注释里写明实测数字",
       F.TOL_PX >= 1 and F.FRAME_TTL_MS >= 50 and _sm.has(_src, "79%") and _sm.has(_src, "0/355"))
    ok("E2 容差匹配的注释写明「不是放宽阈值」（防后人误用）",
       _sm.has(_src, "不是放宽阈值"))
    ok("E3 批量入口 verify_many 在位（一次抓图）", callable(getattr(F, "verify_many", None)))
finally:
    for k, v in _real.items():
        setattr(F, k, v)

print("\n图标指纹判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
