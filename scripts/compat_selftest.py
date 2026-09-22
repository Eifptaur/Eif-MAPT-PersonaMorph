"""兼容性指纹（`agent/compat.py` + 检验报告第七节）的判据。

为什么值得一条判据（2026-09-22 立）：
  这一节的全部价值就是"**贴出来就能定位差异**"，所以它必须满足三条：
    ① **永不抛异常**（一台机器上某条探测失败，不许把整份报告带崩）；
    ② **只读**（不许写任何产品文件——它是给用户跑的体检，不是又一个污染源）；
    ③ **不带口令**（报告会被贴到 B站评论/微信里，`token=` 绝不能跟着走）。
  这三条都不是"看一眼代码就知道对"的，得机械守着。
"""

import io
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _srcmatch as _sm      # noqa: E402

from agent import compat as CP   # noqa: E402


def ok(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("  [%s]" % detail if detail else ""))
    return bool(cond)


def _snap(paths):
    out = {}
    for p in paths:
        try:
            out[p] = (os.path.getmtime(p), os.path.getsize(p))
        except Exception:
            out[p] = None
    return out


def main():
    print("== A. 指纹结构 ==")
    _before = _snap([os.path.join(ROOT, "logs", "console.url"),
                     os.path.join(ROOT, "data", "paused.flag")])
    _ld0 = sorted(os.listdir(os.path.join(ROOT, "logs"))) if os.path.isdir(os.path.join(ROOT, "logs")) else []
    f = CP.fingerprint()
    ok("fingerprint() 返回五节且**不抛异常**",
       all(k in (f or {}) for k in ("windows", "display", "wechat_window", "data_dir", "runtime")),
       ",".join(sorted((f or {}).keys())))
    ok("每节都是 dict（某节失败也只影响它自己）",
       all(isinstance(f.get(k), dict) for k in ("windows", "display", "wechat_window", "data_dir", "runtime")),
       str([k for k in f if not isinstance(f.get(k), dict)]))
    ok("系统节给出版本/位数（报告里第一眼要看的两个值）",
       bool((f["windows"] or {}).get("release")) and bool((f["windows"] or {}).get("arch")),
       str(f["windows"])[:70])
    ok("显示节给出主屏分辨率与缩放（「点不准」的头号环境变量）",
       bool((f["display"] or {}).get("primary")) and bool((f["display"] or {}).get("scale")),
       str(f["display"])[:70])

    print("== B. 给人看的那几行 ==")
    ls = CP.lines()
    ok("lines() 至少 5 行、每行非空", len(ls) >= 5 and all(str(x).strip() for x in ls), "行数=%d" % len(ls))
    _all = "\n".join(str(x) for x in ls)
    ok("中文可读（含「系统」「显示」「消息库」这类标签）",
       all(w in _all for w in ("系统", "显示", "消息库")), _all[:60])
    ok("⛔ 不带口令：整段里不出现 token=/api_key/password",
       all(s not in _all.lower() for s in ("token=", "api_key", "password", "secret")), _all[:60])
    ok("页 1 的判据是**事实**（只能报「明文头/密文/未知」三种）",
       str((f["data_dir"] or {}).get("page1") or "") in ("", "明文头（SQLite）", "密文（无 SQLite 明文头）"),
       str((f["data_dir"] or {}).get("page1")))

    print("== C. 只读纪律（体检不许变成新的污染源）==")
    _after = _snap(list(_before.keys()))
    ok("跑完指纹后 logs/console.url 与 data/paused.flag 的 mtime+size 一字未变",
       _before == _after, str(_before) + " -> " + str(_after))
    _ld1 = sorted(os.listdir(os.path.join(ROOT, "logs"))) if os.path.isdir(os.path.join(ROOT, "logs")) else []
    ok("logs/ 顶层没有多出文件（也没少）", _ld0 == _ld1,
       str(set(_ld1) ^ set(_ld0))[:80])

    print("== D. 兼容性矩阵（11 条轴，全部是现测事实）==")
    _ax = CP.axes()
    ok("矩阵给出 11 条轴（业界口径：能力/事实矩阵，不是「支持/不支持」）",
       len(_ax) == 11, "条数=%d" % len(_ax))
    ok("每一行都有 axis / fact / value 三个字段且非空",
       all(str(a.get("axis") or "").strip() and str(a.get("fact") or "").strip()
           and str(a.get("value") or "").strip() for a in _ax),
       str([a.get("axis") for a in _ax if not str(a.get("value") or "").strip()]))
    ok("矩阵里不许出现「支持/不支持」这种结论式措辞（必须是探测事实）",
       not any("不支持" in str(a.get("value")) or "已支持" in str(a.get("value")) for a in _ax))
    _names = [a["axis"] for a in _ax]
    ok("11 条轴覆盖我们真踩过的差异（抽查 5 条）",
       all(any(k in n for n in _names) for k in ("UI 代", "DPI", "加密模式", "WebView2", "端口")),
       "、".join(_names)[:120])
    ok("axis_lines() 与 axes() 条数一致（报告里贴的就是同一份）",
       len(CP.axis_lines()) == len(_ax), "%d / %d" % (len(CP.axis_lines()), len(_ax)))

    print("== E. 接线（报告里必须有这一节）==")
    src = io.open(os.path.join(ROOT, "scripts", "collect_report.py"), encoding="utf-8").read()
    ok("collect_report 里有 sec_compat 这一节", _sm.has(src, "def sec_compat()"))
    ok("报告标题里点了名（用户能找到该发哪一段）", _sm.has(src, "七、兼容性指纹"))
    ok("它被加进 main 的节列表（不是写了没人调）", _sm.has(src, 'safe(sec_compat'))
    ok("指纹口径只有一处实现（compat.py 是唯一来源）",
       _sm.has(io.open(os.path.join(ROOT, "agent", "compat.py"), encoding="utf-8").read(), "def fingerprint()"))

    print("== 兼容性指纹判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
    return 0 if FAIL[0] == 0 else 1


PASS, FAIL = [0], [0]
_ok_real = ok


def ok(name, cond, detail=""):        # noqa: F811
    if _ok_real(name, cond, detail):
        PASS[0] += 1
    else:
        FAIL[0] += 1
    return bool(cond)


if __name__ == "__main__":
    sys.exit(main())
