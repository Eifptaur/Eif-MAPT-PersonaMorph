"""最小冒烟矩阵（`agent/compat.py::smoke`）的判据。

为什么值得一条判据（2026-09-22，兼容性落地第 ⑥ 项）：
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

import _srcmatch as _sm          # noqa: E402

from agent import compat as CP   # noqa: E402

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
    _fg0 = _fg_cursor()
    _rows2 = CP.smoke()
    _fg1 = _fg_cursor()
    ok("再跑一次仍是 11 条（可复跑）", len(_rows2) == 11)
    ok("跑冒烟矩阵期间 **data/ 顶层一字未变**", _snap_dir(os.path.join(ROOT, "data")) == _d0d)
    ok("跑冒烟矩阵期间 **logs/ 顶层一字未变**", _snap_dir(os.path.join(ROOT, "logs")) == _d0l)
    if _fg0 and _fg1:
        ok("零打扰：前台窗口与光标位置都没变", _fg0 == _fg1, "%s -> %s" % (_fg0, _fg1))
    else:
        print("  SKIP 零打扰断言（这台机器读不到前台/光标）")

    print("== E. 接线：报告里也要有这一节 ==")
    _cr = io.open(os.path.join(ROOT, "scripts", "collect_report.py"), encoding="utf-8").read()
    ok("检验报告带冒烟矩阵（用户贴回来就能看哪条成立）", _sm.has(_cr, "smoke_lines()"))
    _cp_src = io.open(os.path.join(ROOT, "agent", "compat.py"), encoding="utf-8").read()
    ok("注释里写明这张表是「只读、不动窗口、不写文件」", _sm.has(_cp_src, "全部**只读**"))

    print("== 冒烟矩阵判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
    return 0 if FAIL[0] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
