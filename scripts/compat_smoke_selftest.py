"""最小冒烟矩阵（`agent/compat.py::smoke`）的判据。

为什么值得一条判据：
  这张表是"**换一台机器，先跑一遍就知道哪条能力在这台机器上成立**"的入口，
  所以它必须满足四条硬性质，任一条坏了这张表就没意义：
    ① **11 条轴一条不少**，每条都有"做了什么(probe)"与"证据(evidence)"；
    ② 三态语义不许混：`ok`＝当场成立 · `skip`＝测不了**且说清原因** · `fail`＝做了确实不行；
    ③ **只读**：不写任何产品文件（跑前跑后 data/ + logs/ 顶层一字未变）；
    ④ **零打扰**：全程不动前台窗口、不动光标（这正是本项目最高目标那条线）。
"""

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _srcmatch as _sm # noqa: E402

from agent import compat as CP # noqa: E402

PASS, FAIL = [0], [0]

#: skip 必须能看出"为什么测不了"（词表故意宽，但**不许是空话**）
_SKIP_WHY = ("测不了", "不联网", "未装", "没定位到", "不在实测区间", "还没", "没找到", "现在没")


def ok(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("  [%s]" % detail if detail else ""))
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
    return bool(cond)


def _snap_dir(d):
    out = {}
    try:
        for n in os.listdir(d):
            p = os.path.join(d, n)
            try:
                out[n] = os.path.getmtime(p)
            except Exception:
                out[n] = None
    except Exception:
        pass
    return out


def _fg_cursor():
    """前台窗口 + 光标位置（只读；拿不到就返回 None —— 不硬编环境假设）。"""
    try:
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        u = ctypes.windll.user32
        pt = POINT()
        u.GetCursorPos(ctypes.byref(pt))
        return (int(u.GetForegroundWindow() or 0), int(pt.x), int(pt.y))
    except Exception:
        return None


def main():
    print("== A. 结构：11 条轴，每条都有 probe 与 evidence ==")
    rows = CP.smoke()
    ok("冒烟矩阵给出 11 条轴", len(rows) == 11, "条数=%d" % len(rows))
    _axes = [r.get("axis") for r in rows]
    ok("轴名不重复且都非空", len(set(_axes)) == len(_axes) and all(str(a or "").strip() for a in _axes),
       "、".join(a or "?" for a in _axes)[:110])
    ok("每条都写了「做了什么」与「证据」",
       all(str(r.get("probe") or "").strip() and str(r.get("evidence") or "").strip() for r in rows),
       str([r.get("axis") for r in rows if not str(r.get("evidence") or "").strip()]))
    ok("覆盖我们真踩过的差异（抽查 6 条轴名）",
       all(any(k in (a or "") for a in _axes)
           for k in ("UI 代", "DPI", "会话行点击", "加密模式", "WebView2", "端口")))

    print("== B. 三态语义 ==")
    _bad = [r for r in rows if r.get("verdict") not in ("ok", "skip", "fail")]
    ok("verdict 只能是 ok / skip / fail", not _bad, str([r.get("verdict") for r in _bad]))
    _skip_bad = [r["axis"] for r in rows if r.get("verdict") == "skip"
                 and not any(k in str(r.get("evidence")) for k in _SKIP_WHY)]
    ok("skip 必须**说清为什么测不了**（不许一句空话）", not _skip_bad, "、".join(_skip_bad))
    _fail = [r["axis"] for r in rows if r.get("verdict") == "fail"]
    ok("这台机器上没有 fail（fail＝做了确实不行，那是真环境异常）", not _fail, "、".join(_fail))

    print("== C. ASCII 形态（判据/用户都能逐行读）==")
    _ls = CP.smoke_lines()
    ok("smoke_lines() 行数与矩阵一致", len(_ls) == len(rows), "%d / %d" % (len(_ls), len(rows)))
    ok("每行都是 AXIS|轴|判定|做了什么|证据 五段（证据里可以再带竖线）",
       all(len(str(x).split("|", 4)) == 5 and str(x).split("|", 4)[0] == "AXIS"
           and str(x).split("|", 4)[2] in ("ok", "skip", "fail") for x in _ls),
       str(_ls[:1])[:90])

    print("== D. 只读 + 零打扰 ==")
    _d0d, _d0l = _snap_dir(os.path.join(ROOT, "data")), _snap_dir(os.path.join(ROOT, "logs"))
    # ⛔ 跑之前先探"这台机器现在有没有人在用鼠标"：有 ⇒ 光标那半**根本量不出来**，如实 SKIP。
    #    为什么前置探而不是事后比：人手一动就是**单步**位移，事后一次对照采样分不清
    #    "外部在动" 与 "我们自己动了鼠标"（实测全量并发跑时 (2261,825)->(2299,885) 就是用户在动鼠标）。
    import time as _tp
    _act = [_fg_cursor()]
    for _ in range(4):
        _tp.sleep(0.2)
        _act.append(_fg_cursor())
    _machine_busy = any(a and b and a[1:] != b[1:] for a, b in zip(_act, _act[1:]))
    _fg0 = _fg_cursor()
    _rows2 = CP.smoke()
    _fg1 = _fg_cursor()
    ok("再跑一次仍是 11 条（可复跑）", len(_rows2) == 11)
    ok("跑冒烟矩阵期间 **data/ 顶层一字未变**", _snap_dir(os.path.join(ROOT, "data")) == _d0d)
    ok("跑冒烟矩阵期间 **logs/ 顶层一字未变**", _snap_dir(os.path.join(ROOT, "logs")) == _d0l)
    if _fg0 and _fg1:
        if _fg0 == _fg1:
            ok("零打扰：跑冒烟矩阵期间前台窗口与光标都没动", True, str(_fg0))
        elif _fg0[0] != _fg1[0]:
            # 前台窗口变了 ⇒ **我们自己抢了前台** ⇒ 真问题，判否（这条是本项目最高目标那条线）
            ok("零打扰：跑冒烟矩阵期间**前台窗口**没变", False, "%s -> %s" % (_fg0, _fg1))
        else:
            # 只有光标变了：可能是**外部**在动鼠标（人手 / 别的程序 / 并发跑的其它判据）。
            # ⛔ 先看**开跑前**的探测结果：那时就有人在动 ⇒ 本次量不出"零打扰"，如实 SKIP。
            #   再补一次对照采样兜底（开跑后才有动作的情况）。
            if _machine_busy:
                print("  SKIP 零打扰·光标那半  [开跑前就有人在动鼠标（%s）⇒ 本次量不出，不算通过]"
                      % " -> ".join(str(x[1:]) for x in _act if x))
            else:
                import time as _t13
                _t13.sleep(0.25)
                _fg2 = _fg_cursor()
                if _fg2 and _fg2[1:] != _fg1[1:]:
                    print("  SKIP 零打扰·光标那半  [光标在被外部持续移动（%s -> %s -> %s）"
                          "⇒ 本次量不出，不算通过]" % (_fg0[1:], _fg1[1:], _fg2[1:]))
                else:
                    ok("零打扰：跑冒烟矩阵期间光标没动", False, "%s -> %s" % (_fg0, _fg1))
    else:
        print("  SKIP 零打扰断言（这台机器读不到前台/光标）")

    # ⛔ 光标那半在"有人在用机器"时**量不出来**（人手一动就是单步位移，无法与自身归因）
    #    ⇒ 牙齿靠这条**源级**断言补上：冒烟矩阵这条路径不许出现任何"移动/点击"的调用
    #    （与它自己的 docstring「只读、不动窗口、不写文件」一致）。源级断言不会因环境而抖。
    _cpsrc = io.open(os.path.join(ROOT, "agent", "compat.py"), encoding="utf-8").read()
    _banned = [w for w in ("SetCursorPos", "mouse_event", "SendInput", "click_real_at",
                           "wheel_real_at", "click_real_hold") if w in _cpsrc]
    ok("源级：冒烟矩阵**不含任何移动光标 / 点击的调用**", not _banned, "命中：%s" % _banned)

    print("== E. 接线：报告里也要有这一节 ==")
    _cr = io.open(os.path.join(ROOT, "scripts", "collect_report.py"), encoding="utf-8").read()
    ok("检验报告带冒烟矩阵（用户贴回来就能看哪条成立）", _sm.has(_cr, "smoke_lines()"))
    _cp_src = io.open(os.path.join(ROOT, "agent", "compat.py"), encoding="utf-8").read()
    ok("注释里写明这张表是「只读、不动窗口、不写文件」", _sm.has(_cp_src, "全部**只读**"))

    print("== 冒烟矩阵判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
    return 0 if FAIL[0] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
