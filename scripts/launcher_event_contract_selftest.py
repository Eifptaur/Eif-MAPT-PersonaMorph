# -*- coding: utf-8 -*-
"""启动器事件契约：`scripts/onestart.py` 发的每个事件键，`launcher-src/launcher.cs` 都必须认。

⛔ 为什么要有它（真实 bug，用户截图现场）：
   Python 侧 `_prog()` 把进度键映射成 `deps / install / selftest / ui`（`ui` ＝「界面组件」＝PySide6，
   正式包自带轮子 ⇒ 本地装几乎瞬间；**在线包**才要下载，实测能跑几分钟）；
   而 C# 启动器的 `ParseLine` **只认 deps / install / selftest**
   ⇒ **`ui` 事件被静默丢弃** ⇒ 那一整段安装期间，界面**钉在 45%**、状态行还是上一步的
   「已完成 27 / 27 个包（100%），请稍候」⇒ 用户看到的就是"卡住了"。
   ⇒ 这类"两边口径对不上"的 bug **不会报错、只是不更新**，所以必须**用判据把契约钉住**。
"""
from __future__ import annotations

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

import _srcmatch as _sm # noqa: E402
import _srcslice as _ss # noqa: E402


def _cs_body(src: str, sig: str) -> str:
    """取 C# 里某个方法的**方法体**（从签名到下一个同级成员之前）。

    ⛔ 为什么不能用 `_srcslice.func_src`：那个助手走 **Python 的 `ast`**，
      喂 C# 源码会 `SyntaxError`（实测：`// 群相 …` 那行就被当成 Python 解析并报错）。
      ⛔ 也不能用正则硬剥 `"def xxx"` 那种"可机械改写形态"（`structure_guard_selftest` 明令）——
        不过那批规则针对的是**Python `def`**，这里是 C# 方法签名，不属该口径。
      ⇒ 做法：定位签名行，取到**下一个同级成员**（`\\n        <修饰符> `）为止；找不到就用一段窗口。
    """
    i = src.find(sig)
    if i < 0:
        return ""
    # 找到签名的 `{`，再**按花括号配对**扫到方法结束（第一版只切到签名行尾 ⇒ 取到的体是空的，
    # 于是 A3/A4 全红 —— 那种"切错"的表现和"文件里没有"一模一样，必须靠下面的前置断言区分）。
    _b = src.find("{", i)
    if _b < 0:
        return ""
    _depth = 0
    for _k in range(_b, len(src)):
        _c = src[_k]
        if _c == "{":
            _depth += 1
        elif _c == "}":
            _depth -= 1
            if _depth == 0:
                return src[i:_k + 1]
    return src[i:]

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


_ONE = io.open(os.path.join(ROOT, "scripts", "onestart.py"), encoding="utf-8").read()
_CS = io.open(os.path.join(ROOT, "launcher-src", "launcher.cs"), encoding="utf-8").read()
_BPS = io.open(os.path.join(ROOT, "build.ps1"), encoding="utf-8").read()
# 启动器文件名从 `agent/autostart.py` **一处**取（它就是唯一来源）
_AS = io.open(os.path.join(ROOT, "agent", "autostart.py"), encoding="utf-8").read()
_LAUNCH_EXE = (re.search(r'LAUNCHER_EXE\s*=\s*"([^"]+)"', _AS) or [None, "?"])[1]

print("\n══ A. 取两边的键表 ══")
_prog = _ss.func_src(_ONE, "_prog")
# Python 侧：`_prog` 里的 {"依赖检查": "deps", ..., "界面组件": "ui"} 映射 ⇒ 右边就是事件键
_py_keys = set(re.findall(r'"\s*[\u4e00-\u9fff]+\s*"\s*:\s*"([a-z_]+)"', _prog))
# `@@PHASE` 的三种 + 兜底 key（`.get(prefix, "step")`）
_phase_keys = set(re.findall(r'evt\(\s*"PHASE"\s*,\s*"([a-z_]+)"', _ONE))
_fallback = set(re.findall(r'\.get\(prefix,\s*"([a-z_]+)"\)', _prog))
ok("A1 取到了 Python 侧的进度键（否则下面全是空过）",
   len(_py_keys) >= 4, "keys=%s" % sorted(_py_keys))
ok("A2 取到了 PHASE 键", len(_phase_keys) >= 3, "phase=%s" % sorted(_phase_keys))

# C# 侧：`ParseLine` 里 `ps[0] == "xxx"` 与 `ph == "xxx"`
_parse = _cs_body(_CS, "void ParseLine(string line)")
_cs_prog_keys = set(re.findall(r'ps\[0\]\s*==\s*"([a-z_]+)"', _parse))
_cs_phase_keys = set(re.findall(r'ph\s*==\s*"([a-z_]+)"', _parse))
ok("A3 取到了启动器认的进度键", len(_cs_prog_keys) >= 3, "cs=%s" % sorted(_cs_prog_keys))
ok("A4 取到了启动器认的 PHASE 键", len(_cs_phase_keys) >= 3, "cs_phase=%s" % sorted(_cs_phase_keys))

print("\n══ B. 契约：Python 发的，启动器必须认 ══")
_py_all = _py_keys | _fallback
_missing_prog = sorted(_py_all - _cs_prog_keys)
ok("B1 ⭐ **进度键全覆盖**：Python 能发的每个键，启动器都认"
   "（缺一个 ⇒ 那一段界面不更新 ＝ 用户看到「卡住」）",
   not _missing_prog, "启动器没认：%s" % (_missing_prog or "无 ✅"))
_missing_phase = sorted(_phase_keys - _cs_phase_keys)
ok("B2 ⭐ **PHASE 键全覆盖**", not _missing_phase, "启动器没认：%s" % (_missing_phase or "无 ✅"))

print("\n══ C. 反向：启动器认的，Python 必须真的会发（防死分支）══")
_dead_prog = sorted(_cs_prog_keys - _py_all - {"step"})
ok("C1 没有「认了但永远不会来」的进度键（死分支）", not _dead_prog, "多余：%s" % (_dead_prog or "无 ✅"))
_dead_phase = sorted(_cs_phase_keys - _phase_keys)
ok("C2 没有「认了但永远不会来」的 PHASE 键", not _dead_phase, "多余：%s" % (_dead_phase or "无 ✅"))

print("\n══ D. 反例锚（灵敏度）：这个检查器真能发现缺口吗 ══")


def _missing_of(cs_text: str) -> list:
    _p = _cs_body(cs_text, "void ParseLine(string line)")
    _keys = set(re.findall(r'ps\[0\]\s*==\s*"([a-z_]+)"', _p))
    return sorted(_py_all - _keys)


_fake = _CS.replace('else if (ps[0] == "ui")', 'else if (ps[0] == "ui_XX")')
ok("D1 ⭐ 把 `ui` 分支改坏 ⇒ 检查器**必须报缺口**（否则它就是个恒真的摆设）",
   _missing_of(_fake) == ["ui"], "检出：%s" % _missing_of(_fake))

print("\n══ E. 本次修的现场（防回退）══")
ok("E0 ⭐ **代码里的启动器名 ⇄ 构建脚本的输出名一致**"
   "（改名时最容易漏的一处：代码找 `群相.exe`、构建却产出 `一键启动.exe` ⇒ 双击没反应）",
   ("/out:" + _LAUNCH_EXE) in _BPS, "launcher 名=%s" % _LAUNCH_EXE)
ok("E1 启动器**认 `ui`**（界面组件那一段的进度事件），且文案说清「在装什么」",
   _sm.has(_parse, 'ps[0] == "ui"') and _sm.has(_CS, "桌面控制台的图形库 —— 已完成"))
# ⛔ 反例锚：文案**不许再写"首次约 74MB"** —— 正式包自带 PySide6 轮子（`offline/wheels`），
#   首装不下载；写"要下载 74MB"会让装了整包的用户以为下错了包（用户口径："那不就直接下整包吗"）。
ok("E1′ ⭐ 启动器**不许**再对用户说「要下载 74MB 界面组件」（正式包自带，不下载）",
   (not _sm.has(_CS, "首次约 74MB")) and (not _sm.has(_CS, "首次会额外下载约 74MB")),
   "旧文案残留＝%s" % ("有" if (_sm.has(_CS, "首次约 74MB") or _sm.has(_CS, "首次会额外下载约 74MB")) else "无"))
ok("E2 该分支**把 45→65 留给它**（不占依赖段的 45）", _sm.has(_parse, "45 + (int)(20 * r)"))

print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for _x in FAIL:
        print("  - " + _x)
sys.exit(1 if FAIL else 0)
