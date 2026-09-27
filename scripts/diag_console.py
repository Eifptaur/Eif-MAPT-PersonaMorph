# -*- coding: utf-8 -*-
"""控制台「白屏」现场采集（**只读**，不启动机器人、不改任何配置）。

用法（在包目录里，也就是 `一键启动.exe` 旁边）：
    runtime\\python\\python.exe scripts\\diag_console.py
跑完会在同目录生成 `diag-console.txt`，把它发回来即可。

它只是"看"，不做这些事：不启机器人、不写 config.json、不动 data/、不联网、不改任何设置。
输出里会把 `logs\\console.url` 的 token 打码；其余日志**原样**附尾部若干行
（可能含群名/路径），发之前自己过一眼即可。
"""
from __future__ import annotations

import glob
import os
import re
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # 包目录（一键启动.exe 所在处）
OUT = os.path.join(ROOT, "diag-console.txt")

TAIL = 4000          # 每个日志取尾部多少字符
WANT_LOGS = ["logs/*.log", "logs/*.url", "logs/*.txt", "data/*.log"]
KEY_FILES = [
    "一键启动.exe", "一键关闭.exe", "WebView2Loader.dll",
    "lib/Microsoft.Web.WebView2.Core.dll", "lib/Microsoft.Web.WebView2.WinForms.dll",
    "assets/console/index.html", "assets/persona/cards.json",
    "agent/version.py", "scripts/persona_morph.py", "qt_bootstrap.py",
    "requirements.txt", "config.example.json",
    "ui_qt/shell.py", "ui_qt/app.py", "ui_qt/assets/fonts/PingXianZhenSong.ttf",
]


def redact(text: str) -> str:
    text = re.sub(r"(token=)[A-Za-z0-9._\-]+", r"\1«已打码»", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{12,}", r"\1«已打码»", text)
    text = re.sub(r"sk-[A-Za-z0-9_\-]{10,}", "«已打码»", text)
    return text


def section(title: str) -> None:
    out.append("")
    out.append("=" * 70)
    out.append("## " + title)
    out.append("=" * 70)


out: list = []
out.append("控制台白屏 · 现场采集（只读）")
out.append("包目录: " + ROOT)
out.append("Python : " + sys.version.replace("\n", " "))

# ⛔ 先自检"我是不是被产品运行时跑的" —— 用系统 Python 跑这里，
#    第五节必然报 `No module named 'PySide6'`，那是**采集方式错**，不是产品缺陷。
_RUNTIME = os.path.join(ROOT, "runtime", "python", "python.exe")
_ok_runtime = os.path.normcase(os.path.abspath(sys.executable)) == os.path.normcase(os.path.abspath(_RUNTIME))
if not _ok_runtime:
    out.append("")
    out.append("!! 注意：本次不是用产品运行时跑的（当前 = %s）" % sys.executable)
    out.append("!! 正确跑法（在包目录里）：runtime\\python\\python.exe scripts\\diag_console.py")
    out.append("!! 否则第一节的 PySide6 与第五节的界面构建结论**都不作数**。")

section("一、解释器与界面组件")
out.append("sys.executable = " + sys.executable)
try:
    import importlib.util as _u
    spec = _u.find_spec("PySide6")
    out.append("PySide6 已安装 = %s" % (spec is not None))
    if spec is not None:
        try:
            import PySide6  # noqa: PLC0415
            out.append("PySide6 版本  = " + getattr(PySide6, "__version__", "?"))
        except Exception:  # noqa: BLE001
            out.append("import PySide6 失败：\n" + traceback.format_exc(limit=4))
except Exception:  # noqa: BLE001
    out.append("探测 PySide6 时出错：\n" + traceback.format_exc(limit=4))

section("二、关键文件在不在（缺哪个 = 一眼看出来）")
for rel in KEY_FILES:
    p = os.path.join(ROOT, rel.replace("/", os.sep))
    out.append("  %-58s %s" % (rel, ("有（%d 字节）" % os.path.getsize(p)) if os.path.exists(p) else "**缺**"))

section("三、offline/ 有没有（只影响首次装依赖的快慢，不影响白屏）")
off = os.path.join(ROOT, "offline")
out.append("  offline/ 存在 = %s" % os.path.isdir(off))
w = os.path.join(off, "wheels")
if os.path.isdir(w):
    names = sorted(os.listdir(w))
    out.append("  offline/wheels 条目数 = %d" % len(names))
    out.append("  含 PySide6 轮子 = %s" % any(n.lower().startswith("pyside6") for n in names))
    out.append("  " + ", ".join(names[:12]))

section("四、日志（尾部；token 已打码）")
found = []
for pat in WANT_LOGS:
    found.extend(sorted(glob.glob(os.path.join(ROOT, pat.replace("/", os.sep)))))
found = [f for f in found if os.path.isfile(f)]
if not found:
    out.append("（一个日志文件都没有 —— 这本身就是重要线索：说明 onestart 可能一步都没走）")
for f in found:
    try:
        txt = open(f, "rb").read().decode("utf-8", "replace")
    except OSError as e:
        out.append("--- %s（读不到：%s）" % (os.path.relpath(f, ROOT), e))
        continue
    out.append("")
    out.append("--- %s  （共 %d 字符，取尾 %d）---" % (os.path.relpath(f, ROOT), len(txt), TAIL))
    out.append(redact(txt[-TAIL:]))

section("五、在离屏模式下试建一次界面（不弹窗、不影响你正在看的窗口）")
out.append("说明：这一步只验证「代码本身能不能把界面建出来」，不改你的窗口。")
try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # ⚠️ 两个搜索路径都要加：`ui_qt` 放 shell/stylekit_qt，包根放 `agent`。
    #    少加包根会得到 `ModuleNotFoundError: No module named 'agent'`
    #    —— 那是**采集脚本自己的**路径问题，不是产品缺陷，必须分清。
    for p in (os.path.join(ROOT, "ui_qt"), ROOT):
        if p not in sys.path:
            sys.path.insert(0, p)
    out.append("sys.path[0:3] = %r" % (sys.path[:3],))
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415
    app = QApplication.instance() or QApplication([])
    from stylekit_qt import THEMES, apply_font_to_app, ensure_fonts, resolve_family  # noqa: PLC0415
    d, b, e = ensure_fonts()
    out.append("ensure_fonts() -> display=%r body=%r emoji=%r" % (d, b, e))
    t = THEMES.get("whale") or THEMES["whale"]
    fam = resolve_family(t.font_family)
    if fam != t.font_family:
        for tk in THEMES.values():
            tk.font_family = fam
        out.append("本机没有 %s ⇒ 界面字体降级为 %s" % (t.font_family, fam))
    apply_font_to_app(app, t)
    from shell import Shell  # noqa: PLC0415
    w = Shell(t)
    out.append("Shell 构建成功：顶层控件数 = %d" % len(w.findChildren(object)))
except Exception:  # noqa: BLE001
    out.append("**界面构建失败**（这就是白屏的直接原因）：")
    out.append(traceback.format_exc(limit=12))

try:
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    print("已写出：" + OUT)
    print("把它发回来即可（里面的 token 已打码）。")
except OSError as e:
    print("写不出文件：%s" % e)
    print("\n".join(out))
