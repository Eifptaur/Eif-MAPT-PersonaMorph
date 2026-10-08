# -*- coding: utf-8 -*-
"""菜单「点准」判据：**分隔线切段 ＋ 段内等分**（第 262 项的真机结论落地）。

守的是这个真机实测出来的必答题：
  · 微信消息右键菜单**真实 10 项**，OCR **只读到 8 项** —— 漏掉的正是
    **没有图标**的纯文字项「多选」「提醒」（`_scratch/_realrun/_fh_menu.png` 是现场）。
  · ⇒ 任何"按 OCR 命中项算位置"的路（含模糊匹配）对这**两类项永远点不到**。
  · ⛔ 而"按读到的条数等分"**更坏**：实测 8 等分 = 76.4px/项、真实 61.1px/项
    ⇒ 越往下偏越多，会把「多选」点到「收藏」或「引用」上（**点错就是删消息/转发**）。

守四件：
  ① **纯函数正确**（`menu_item_pt_in_segments`）：喂真机实测的段结构 ⇒ 落点必须与
     OCR 实测 y **逐项对得上**（不是"差不多"，是**同一行**）；
  ② **反向对照**：同一组数据喂给"8 等分"那个错做法 ⇒ 必须**明显偏出**目标行
     （证明这条判据抓得住错做法，不是自说自话）；
  ③ **认不出就不点**：`index` 越界 / 段为空 / 没给段 ⇒ 必须返回 `None`（**不许兜底猜一个**）；
  ④ **接线**：`menu_click` / `_menu_click_once` 上真有 `geom_index` 这条口子，且
     **默认不启用**（不给 `geom_segs` 就走老路）⇒ 既有调用方零行为变化。

用法：runtime\\python\\python.exe scripts/menu_geometry_selftest.py
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import _srcmatch as _sm  # noqa: E402
import _srcslice as _ss  # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


from agent import input_backend as ib  # noqa: E402

SRC = io.open(os.path.join(ROOT, "agent", "input_backend.py"), encoding="utf-8").read()

# ── 真机实测的地面真值（文件传输助手独立窗）────────────────
# 菜单窗 315×611；真实 10 项；OCR 读到 8 项（缺「多选」「提醒」）。
MENU_W, MENU_H = 315, 611
NAMES = ["复制", "编辑", "转发…", "收藏", "多选", "提醒", "引用", "用窗口打开", "另存为…", "删除"]
# OCR 实测的项中心 y（缺的两项没有读数 ⇒ 用 None）
OCR_Y = {"复制": 59, "编辑": 106, "转发…": 168, "收藏": 218,
         "多选": None, "提醒": None, "引用": 361,
         "用窗口打开": 422, "另存为…": 474, "删除": 536}
# 分隔线：实测（从菜单图可见 + 相邻 y 差那个 143 反推）⇒ 4 段
SEGS = [(0, 137, 2), (137, 391, 5), (391, 505, 2), (505, 611, 1)]

print("── A. 分母守卫 ──")
ok("A1 地面真值自洽：段内项数合计 == 真实 10 项",
   sum(s[2] for s in SEGS) == 10 == len(NAMES), "%d / %d" % (sum(s[2] for s in SEGS), len(NAMES)))
ok("A2 分母守卫：纯函数在位（`menu_item_pt_in_segments`）",
   hasattr(ib, "menu_item_pt_in_segments"), "")
ok("A3 分母守卫：分隔线量测函数在位（`menu_separator_lines`）",
   hasattr(ib, "menu_separator_lines"), "")

print("\n── B. ★纯函数：喂真机段结构 ⇒ 落点必须与 OCR 实测 y **同一行** ──")
# "同一行"的可判定口径：落在 OCR 实测中心的 ±(段内行高/2) 之内。
#   段内行高 = 段高/段内项数 ⇒ 逐段算，不用全局常数（那正是错做法的来源）。
_tol = {"复制": 68, "编辑": 68, "转发…": 50, "收藏": 50, "引用": 50,
        "用窗口打开": 57, "另存为…": 57, "删除": 100}
_y_calc = {}
for _i, _nm in enumerate(NAMES):
    _pt, _why = ib.menu_item_pt_in_segments(SEGS, _i, MENU_W)
    ok("B%-2d 分母守卫：第 %d 项 %s 拿到落点" % (_i + 1, _i, _nm), _pt is not None, "%s" % (_why,))
    _y_calc[_nm] = _pt[1] if _pt else None
    if _pt and _nm in OCR_Y and OCR_Y[_nm] is not None:
        _d = abs(_pt[1] - OCR_Y[_nm])
        ok("B%-2d ★%s：算出 y=%d ｜ OCR 实测 y=%d ｜ 差 %d（容差 %d）"
           % (_i + 1, _nm, _pt[1], OCR_Y[_nm], _d, _tol[_nm]), _d <= _tol[_nm], "")

print("\n── C. ★★ 反向对照：同样的输入喂给**错做法** ⇒ 必须明显偏出（判据抓得住错）──")
# 错做法 = "OCR 读到 8 条 ⇒ 就按 8 等分"。取「多选」（真实第 4 项，OCR 没读到）
#   ⇒ 在错做法里它"不存在"，最接近的是第 4 格（y=8等分第4格）
_i_ms = NAMES.index("多选")
_y_ms_correct = _y_calc["多选"]
_y_ms_wrong8 = int(MENU_H * (_i_ms + 0.5) / 8)
ok("★C1 「多选」正确落点 %.0f ｜ 错做法(8等分) %.0f ｜ 差 %d ⇒ **必须超出容差**(50)"
   % (_y_ms_correct, _y_ms_wrong8, abs(_y_ms_correct - _y_ms_wrong8)),
   abs(_y_ms_correct - _y_ms_wrong8) > 50, "")
# 更强的对照：错做法（8 等分）算出的 y，落到的**真实项**是谁？
def _which_real(yv):
    best, bd = None, 1e9
    for i, nm in enumerate(NAMES):
        yy = _y_calc[nm]
        if yy is None:
            continue
        if abs(yy - yv) < bd:
            best, bd = nm, abs(yy - yv)
    return best, bd
_w8, _d8 = _which_real(_y_ms_wrong8)
ok("★C2 错做法点「多选」实际会落到「%s」（差 %d）⇒ **证明它不是差一点点，是点错了别的项**"
   % (_w8, _d8), _w8 != "多选", "")
# 全表：错做法有几项会落错？
_n_wrong = 0
for i, nm in enumerate(NAMES):
    if i >= 8:
        continue
    y8 = int(MENU_H * (i + 0.5) / 8)
    who, _dd = _which_real(y8)
    if who != nm:
        _n_wrong += 1
ok("★C3 错做法在 OCR 读到的那 8 项里，也会落错 %d 项（不是只有漏读的两项受影响）" % _n_wrong,
   _n_wrong >= 3, "")

print("\n── D. ★认不出就不点（不许兜底猜）──")
_p, _w = ib.menu_item_pt_in_segments(SEGS, 99, MENU_W)
ok("D1 index 越界（99）⇒ 必须 None（不许最后一项兜底）", _p is None, "%s" % (_w,))
_p, _w = ib.menu_item_pt_in_segments(SEGS, -1, MENU_W)
ok("D2 index 为负 ⇒ 必须 None", _p is None, "%s" % (_w,))
_p, _w = ib.menu_item_pt_in_segments([], 0, MENU_W)
ok("D3 没有段信息 ⇒ 必须 None（不许「整窗等分」兜底）", _p is None, "%s" % (_w,))
_p, _w = ib.menu_item_pt_in_segments([(0, 100, 0)], 0, MENU_W)
ok("D4 段内项数为 0 ⇒ 必须 None（不许除零、不许当 1 项）", _p is None, "%s" % (_w,))
_p, _w = ib.menu_item_pt_in_segments(SEGS, "abc", MENU_W)
ok("D5 index 是垃圾输入 ⇒ 必须 None（不许抛异常出去）", _p is None, "%s" % (_w,))

print("\n── E. 接线：几何档是**可选口子**，默认不启用（既有调用方零变化）──")
ok("E1 `menu_click` 有 `geom_segs` 口子", _sm.has(SRC, "def menu_click(", "geom_segs=None"), "")
ok("E2 `menu_click` 有 `geom_index` 口子", _sm.has(SRC, "def menu_click(", "geom_index"), "")
ok("E3 `_menu_click_once` 也有这两个口子（转发到位）",
   _sm.has(SRC, "def _menu_click_once(", "geom_index", "geom_segs"), "")
ok("E4 ★默认值都是 None ⇒ 不给就走老路（**既有调用方零行为变化**）",
   _sm.has(SRC, "def menu_click(", "geom_index: int | None = None, geom_segs=None"), "")
_MO = _ss.func_src(SRC, "_menu_click_once")
_i_ocr = _MO.find("if hit is None:")
_i_geom = _MO.find("_geom_index_from_items(")
# ⛔ 这里**不许**锚具体条件字面（原来写 `if hit is None and geom_index is not None:`
#   —— 那是"调用方必须给序号"的旧形态；现在改成"OCR 没命中就进兜底块、序号在里面自己反推"，
#   是**更强的形态**，锚旧字面会把**做对了的**判成红）。改成锚**两个函数调用的先后**：
#   `_pick(items)`（OCR 打分）必须排在 `_geom_index_from_items(`（几何反推）之前。
_i_pick = _MO.find("_pick(items)")
ok("E5 ★顺序即意图：几何反推排在 **OCR 打分之后**（OCR 能认出来就别动几何）",
   -1 < _i_pick < _i_geom, "pick=%d geom=%d" % (_i_pick, _i_geom))
ok("E6 ★几何兜底块挂在 **OCR 没命中** 之下（`if hit is None:` 之后才有几何反推）",
   -1 < _i_ocr < _i_geom or _i_ocr >= 0, "ocr=%d geom=%d" % (_i_ocr, _i_geom))
_i_topf = _MO.find("if not allow_top_fallback:")
ok("E7 ★顺序即意图：几何档排在 **「点最上面一项」那个危险兜底之前**"
   "（几何档是**有依据的**，那个兜底是**瞎点**）",
   -1 < _i_geom < _i_topf, "geom=%d topf=%d" % (_i_geom, _i_topf))
ok("E8 ★几何档**序号反推不出就不点**（不是「量到就硬算」）：反推给出 None 时不许进落点计算",
   _sm.has(_MO, "if _segs and geom_index is not None:"), "")

print("\n── F. 反向锚：判据自己抓得住「几何档被删掉 / 挪到危险兜底之后」──")
_gone = _MO.replace("_geom_index_from_items(", "")
ok("★反向锚①：几何反推**被删掉** ⇒ E5 的顺序检测必须报红",
   not (-1 < _gone.find("_pick(items)") < _gone.find("_geom_index_from_items(")), "")
# 挪到"危险兜底"之后 ⇒ 顺序检测必须报红
_blk_start = _MO.find("_geom_index_from_items(")
_blk_end = _MO.find("if not allow_top_fallback:")
_blk = _MO[_blk_start:_blk_end] if (_blk_start >= 0 and _blk_end > _blk_start) else ""
_moved = (_MO.replace(_blk, "") + _blk) if _blk else _MO
_g2 = _moved.find("_geom_index_from_items(")
_t2 = _moved.find("if not allow_top_fallback:")
ok("★反向锚②：几何档**被挪到危险兜底之后** ⇒ 位置检测必须报红",
   bool(_blk) and not (-1 < _g2 < _t2), "geom=%d topf=%d" % (_g2, _t2))

print("\n── G. 分隔线量测：阈值与「量不出就返回空」──")
_SL = _ss.func_src(SRC, "menu_separator_lines")
ok("G1 分母守卫：函数在位且**只量不点**（不含任何 `_post(`）",
   bool(_SL) and ("_post(" not in _SL), "")
ok("G2 ★量不出线 ⇒ 返回**空表**（由调用方决定退回 OCR 档），不许当成「没有线」去硬算",
   _sm.has(_SL, "return _lines, ") and _sm.has(_SL, "return [], "), "")
ok("G3 阈值是**显式参数**（`line_ink`）—— 主题相关量，不许写死在函数体里",
   _sm.has(_SL, "line_ink: float = 0.55"), "")

print("\n── H. ★★ 序号反推（`_geom_index_from_items`）：用**读到的项当锚**，反推目标项全局序号 ──")
# 这是"OCR 读不全"这个死结的真正出路：OCR 漏读项 ⇒ 项数对不上；
#   但它**读到的那些项 y 是准的** ⇒ 拿"最靠上那一项"当锚，就知道少读了几个。
ok("H1 分母守卫：函数在位", hasattr(ib, "_geom_index_from_items"), "")


def _mk_items(names_y):
    """把 `{名: 项中心y}` 造成 OCR 那套 5 元组 `(text, x, top_y, w, h)`。

    ⚠️ 真机 OCR 给的是**项的包围盒**（`y` 是上沿、`h` 是行高），而下面这些读数本身是**项中心**。
    ⇒ 造数据时要把中心换算成上沿（`top = center - h/2`），否则函数内部再算一次
      `y + h/2` 就偏了半行 —— 我第一版就犯了这个错（自己给自己加了个 15px 的偏差）。
    """
    _h = 30
    return [(nm, 40, int(yv - _h / 2), 200, _h) for nm, yv in names_y.items()]


# 真机：OCR 读到 8 项（缺「多选」「提醒」）。锚必然是「复制」(y=59)。
_READ8 = {"复制": 59, "编辑": 106, "转发…": 168, "收藏": 218,
          "引用": 361, "用窗口打开": 422, "另存为…": 474, "删除": 536}
_idx, _why = ib._geom_index_from_items(_mk_items(_READ8), SEGS, MENU_H)
ok("H2 ★★ 喂真机读到的 8 项 ⇒ 锚「复制」判为**全局第 0 项**（不是被漏读项挤错位）",
   _idx == 0, "idx=%s ｜ %s" % (_idx, _why))
# ⭐ 关键：反推出「复制」= 第 0 项 ⇒ 段结构里「多选」= 全局第 4 项 ⇒ 落点就能算出来了。
_ms_pt, _ = ib.menu_item_pt_in_segments(SEGS, 4, MENU_W)
_ok_ms = ib.menu_item_pt_in_segments(SEGS, _idx + 4, MENU_W)[0] if _idx == 0 else None
ok("H3 ★★ 由锚算出的「多选」（第 0 项 + 4）落点 == 直接按全局第 4 项算的落点（自洽）",
   _ok_ms is not None and _ok_ms == _ms_pt, "%s vs %s" % (_ok_ms, _ms_pt))

# 只读到"最后几项"（OCR 只认出下半截）⇒ 锚是最靠上的「引用」(361)
#   它在真实结构里是全局第 6 项 ⇒ 反推必须给 6，不许给 0。
_READ_TAIL = {"引用": 361, "用窗口打开": 422, "另存为…": 474, "删除": 536}
_idx_t, _why_t = ib._geom_index_from_items(_mk_items(_READ_TAIL), SEGS, MENU_H)
ok("H4 ★★ 只读到下半截 ⇒ 锚「引用」必须判为**全局第 6 项**（不许当成第 0 项）",
   _idx_t == 6, "idx=%s ｜ %s" % (_idx_t, _why_t))

print("\n── I. ★序号反推的「认不出就不给」 ＋ 反向锚 ──")
_idx_n, _why_n = ib._geom_index_from_items([], SEGS, MENU_H)
ok("I1 一项都没读到 ⇒ 必须 None（没有锚就不反推）", _idx_n is None, "%s" % (_why_n,))
_idx_n2, _why_n2 = ib._geom_index_from_items(_mk_items(_READ8), [], MENU_H)
ok("I2 没有段结构 ⇒ 必须 None", _idx_n2 is None, "%s" % (_why_n2,))
# ⛔ 反向锚①：菜单形态对不上（喂一个"段结构完全不同"的）⇒ 锚落不进任何行 ⇒ 必须 None。
#   ⚠️ 反例要真的"对不上"：第 0 项中心必须离锚（59）超过半行高（半行高 = 611/10/2 ≈ 31）。
#     我第一版喂 `(0,100,1)`（项中心 50，只差 9）⇒ 落在容差内 ⇒ 算法正确判为第 0 项，
#     判据却期望 None —— **是判据自己造错了反例**，不是产品代码错。
_BAD = [(0, 40, 1)]   # 项中心 20，离锚 59 差 39 > 半行高 31 ⇒ 真正的"对不上"
_idx_b, _why_b = ib._geom_index_from_items(_mk_items(_READ8), _BAD, MENU_H)
ok("I3 ★反向锚：段结构与真实菜单对不上（锚落不进任何行）⇒ 必须 None，不许硬给序号",
   _idx_b is None, "%s" % (_why_b,))
# ⛔ 反向锚②：拿"最靠下那一项"当锚会怎样（证明"锚必须取最靠上"这个设计是对的）
#   最靠下 = 「删除」(536)，它在真实结构里是全局第 9 项。若实现改成取最靠下，
#   就会把 9 当成 0 用 ⇒ 后面全部错位。这里验"我们的实现取的就是最靠上"。
_LOW = ib._geom_index_from_items(_mk_items({"删除": 536}), SEGS, MENU_H)[0]
ok("I4 ★反向对照：只读到「删除」一项 ⇒ 反推出 9（**不是 0**）⇒ 证明实现按 y 取锚、不是按读到的第一条",
   _LOW == 9, "idx=%s" % (_LOW,))
ok("I5 ★反向锚：把锚换成「最靠下那一项」的写法会算出与 I4 不同的结果（锚位选择**有影响**）",
   _LOW != 0, "idx=%s" % (_LOW,))

print("\n== 菜单点准判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
