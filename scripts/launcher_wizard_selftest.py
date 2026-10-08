# -*- coding: utf-8 -*-
"""安装向导（第 267 项）的**契约判据**：分页骨架 ＋ 完成页勾选 ＋ 就绪路径不许停在欢迎页。

跑法： runtime\\python\\python.exe scripts\\launcher_wizard_selftest.py      退出码 0=全过 / 1=有失败

**为什么要有它**（三条都是"改了就静默坏、跑起来才看得见"的形态）：
  ① **页序契约**：四页的名字与主按钮文案散在 `launcher.cs` 里，改名/漏配不会报错，
     只表现为"点着点着卡住"；
  ② **完成页的勾选项**（"在桌面创建快捷方式"）是用户那句"**下载完之后一点就能直接打开**"的落点 ——
     哪天有人把 `Checked = true` 删了，默认就变成"不建快捷方式"，而用户只会觉得"桌面没图标"；
  ③ ⭐ **就绪路径不许停在欢迎页**（本判据最重要的一条）：静默档下窗体**不可见**，
     若页序没跟着跳（`GoPage(PageInstalling)` 被删），流程会**卡在欢迎页等一个没人点的『开始安装』**
     ⇒ 这叫"**静默死锁**"：用户双击之后什么都没有，进程挂在后台。

⛔ 口径（沿用项目纪律）
  · **本判据不编译、不起窗、不写盘**：只读 `launcher-src/*.cs` 的**源码文本**做静态核对。
    （C# 编译器在本环境的工具安全策略下被拦 ⇒ 编译验证不在这里做，见 `launcher-src/README.md`。）
  · **注释不算**：说明性注释里会出现历史名与旧文案（本项目已踩过"注释里提名字被误判成还在用"）
    ⇒ 所有匹配都在**去注释后的正文**上做。
  · **必配反向锚**：判据自己也要能被"打坏"，否则分不清"判据对"还是"判据失效"。
"""

from __future__ import annotations

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

SRC = os.path.join(ROOT, "launcher-src")

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


def _read(p):
    return io.open(p, encoding="utf-8", errors="replace").read()


def _strip_cs_comments(src):
    """去掉 C# 的 `//` 行注释与 `/* */` 块注释（字符串字面量里的 `//` 极少见，本文件里没有）。

    ⛔ 为什么必须去：判据靠**文本**找文案与语句，而注释里会逐字提到旧文案/历史名
      ⇒ 不去注释就会"把说明当成实现"（本项目真踩过）。
    """
    out = []
    i, n = 0, len(src)
    in_str = False
    in_verbatim = False
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if in_str:
            out.append(c)
            if in_verbatim:
                if c == '"' and nxt == '"':
                    out.append(nxt); i += 2; continue
                if c == '"':
                    in_str = False; in_verbatim = False
            else:
                if c == "\\":
                    out.append(nxt); i += 2; continue
                if c == '"':
                    in_str = False
            i += 1; continue
        if c == '"':
            in_str = True
            in_verbatim = bool(out and out[-1] == "@")
            out.append(c); i += 1; continue
        if c == "/" and nxt == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and nxt == "*":
            i += 2
            while i < n and not (src[i] == "*" and i + 1 < n and src[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c); i += 1
    return "".join(out)


_LAUNCHER = os.path.join(SRC, "launcher.cs")
_STYLEKIT = os.path.join(SRC, "stylekit.cs")

_RAW_L = _read(_LAUNCHER)
_L = _strip_cs_comments(_RAW_L)
_RAW_S = _read(_STYLEKIT)
_S = _strip_cs_comments(_RAW_S)

print("── ① 分页骨架存在（`WizardTabs` 控件 ＋ 四页常量）──")
ok("①a 分母守卫：真读到了 `launcher.cs`（否则下面全是空过）",
   len(_L) > 50000, "%d 字符" % len(_L))
ok("①b `stylekit.cs` 里有自绘页签控件 `WizardTabs`（⛔ 不用系统 `TabControl`）",
   "class WizardTabs" in _S, "")
ok("①c ⛔ 全库不许用系统 `TabControl`（会撕开统一外观）",
   "TabControl" not in _L and "TabControl" not in _S,
   "出现处：" + str([f for f in ("launcher.cs", "stylekit.cs")
                   if "TabControl" in (_L if f == "launcher.cs" else _S)]))
# ⚠️ 上一版这条扫的是**原始源码** ⇒ 被我自己说明"为什么不用 TabControl"的注释判红。
#   ⇒ 改成扫**去注释后的正文**（判据口径：锚意图不锚文案；注释里提名字不算"在用"）。
ok("①c′ 反例锚：真写了一行 `new TabControl()` 会被 ①c 扫出来（证明它没变成空过）",
   "TabControl" in _strip_cs_comments("void X(){ var t = new TabControl(); }"), "")
ok("①d 完成页的勾选是自绘控件 `CardCheck`（⛔ 不用系统 `CheckBox`）",
   "class CardCheck" in _S, "")


print("\n── ② 页序契约：四页的名字与主按钮文案逐字命中 ──")
_PAGES = ["欢迎", "安装位置", "正在安装", "完成"]
for _nm in _PAGES:
    ok("② 页名「%s」在 `PageNames` 里" % _nm,
       ('"%s"' % _nm) in _L, "")
# 四页常量必须齐（少了任何一页，`GoPage` 的边界就会被钳成别的页 ⇒ 页序塌掉）
for _cn in ("PageWelcome", "PageLocation", "PageInstalling", "PageDone"):
    ok("② 页常量 `%s` 已定义" % _cn, _cn in _L, "")
ok("②★ 四页常量是**同一个 `const int` 声明行**里的连号（改名/漏配会当场散架）",
   bool(re.search(r"const\s+int\s+PageWelcome\s*=\s*0\s*,\s*PageLocation\s*=\s*1\s*,"
                  r"\s*PageInstalling\s*=\s*2\s*,\s*PageDone\s*=\s*3", _L)),
   "")
# 主按钮文案
for _t in ("开始安装", "下一步", "完成"):
    ok("② 主按钮文案「%s」在 `GoPage` 的分支里" % _t,
       ('"%s"' % _t) in _L, "")
ok("② 次按钮「上一步」存在", '"上一步"' in _L, "")
ok("② 安装页只有「关闭」（中止）—— `btnClose` 在非安装页被隐藏",
   "btnClose.Visible = installing" in _L, "")


print("\n── ③ 完成页的「在桌面创建快捷方式」（默认勾选）──")
ok("③a 勾选项文案含「在桌面创建快捷方式」",
   "在桌面创建快捷方式" in _L, "")
ok("③b ★ 默认勾选（`Checked = true` 紧跟在 `chkLaunch.Text` 之后）",
   bool(re.search(r"chkLaunch\.Text\s*=[^;]*;\s*(?://[^\n]*\n\s*)?chkLaunch\.Checked\s*=\s*true", _L)),
   "这是'一点就能直接打开'的落点")
ok("③c 完成页的主按钮走 `FinishAndMaybeLaunch()`（按勾选决定建不建）",
   "FinishAndMaybeLaunch()" in _L, "")
ok("③d ★建快捷方式的**目标**不许写字面量 —— 必须是 `Application.ExecutablePath`",
   "sc.TargetPath = Application.ExecutablePath" in _L,
   "写死 `一键启动.exe` 会建出指向不存在文件的死快捷方式")
ok("③e ★反例锚：把默认勾选删掉后，③b 的**判据式**会变红",
   not bool(re.search(r"chkLaunch\.Text\s*=[^;]*;\s*(?://[^\n]*\n\s*)?chkLaunch\.Checked\s*=\s*true",
                      _L.replace("chkLaunch.Checked = true;", "chkLaunch.Checked = false;"))),
   "反向锚有效")


print("\n── ④ 就绪路径：不许弹窗 ＋ 不许停在欢迎页（防静默死锁）──")
ok("④a `FastPathReady` 三条仍在（运行时 ＋ 界面组件 ＋ **真探测依赖**）",
   ("runtime" in _L and "FastPathReady" in _L and "QtBootForm.MissingReason(root)" in _L
    and "DepsProbeOk(root)" in _L), "")
# ⛔ 这一条是"每次都弹窗"那个真 bug 的守卫：判据**不许**再要求"只认标记文件"。
ok("④a′ ★就绪判定**不靠易失标记**：装了依赖就该静默（`data\\.deps_ok` 只作快通道）",
   ("File.Exists(Path.Combine(root, \"data\", \".deps_ok\"))" in _L
    and "DepsProbeOk(root)" in _L
    and bool(re.search(r"File\.Exists\(Path\.Combine\(root,\s*\"data\",\s*\"\.deps_ok\"\)\)\s*\)\s*return\s+true", _L))),
   "标记每次启动先删、装失败就不写回 ⇒ 只认标记会让用户**每次都白弹一次**（实测依赖已全装齐仍弹）")
ok("④a″ ★探测口径＝`dep_check('all')` ＋ 输出契约 `DEPS_OK` / `DEPS_MISSING:`",
   ("dep_check as _d" in _L and "_d('all')" in _L
    and "DEPS_OK" in _L and "DEPS_MISSING" in _L), "")
ok("④a‴ ★反例锚：探测**拿不准就当真不齐**（空输出/异常一律 return false，宁可多弹不要静默失败）",
   bool(re.search(r"if\s*\(string\.IsNullOrEmpty\(outp\)\)\s*return\s+false", _L))
   and bool(re.search(r"static bool DepsProbeOk\(string root\)[\s\S]*?catch\s*\{\s*return false;\s*\}", _L)),
   "")
ok("④b ★静默判定被接到向导页序上（`GoPage(Silent ? PageInstalling : PageWelcome)`）",
   bool(re.search(r"GoPage\(\s*Silent\s*\?\s*PageInstalling\s*:\s*PageWelcome\s*\)", _L)),
   "")
ok("④c ★`GoPage` 起手就把 `_page` 钳到合法区间（防越界页 ⇒ 四页全隐藏＝空白窗）",
   bool(re.search(r"if\s*\(\s*p\s*<\s*PageWelcome\s*\)\s*p\s*=\s*PageWelcome\s*;", _L))
   and bool(re.search(r"if\s*\(\s*p\s*>\s*PageDone\s*\)\s*p\s*=\s*PageDone\s*;", _L)),
   "")
ok("④d ★反例锚：把上面那句 `GoPage(Silent ? …)` 改写成「总是欢迎页」⇒ ④b 必须变红",
   not bool(re.search(r"GoPage\(\s*Silent\s*\?\s*PageInstalling\s*:\s*PageWelcome\s*\)",
                      _L.replace("GoPage(Silent ? PageInstalling : PageWelcome)", "GoPage(PageWelcome)"))),
   "反向锚有效")
ok("④e ⛔ 静默档下窗体不显示（`Opacity=0` ＋ 不进任务栏 ＋ 最小化）仍在",
   ("ShowInTaskbar = false" in _L and "Opacity = 0" in _L
    and "FormWindowState.Minimized" in _L), "")


print("\n── ⑤ 外观与硬规矩（本项目口径，向导新代码也不许破）──")
ok("⑤a ⛔ 0 系统 MessageBox（全部提示走自绘窗体）",
   "MessageBox.Show" not in _RAW_L and "MessageBox.Show" not in _RAW_S, "")
ok("⑤b `StyleKit.Apply(this, …)` 在 `BuildUI` 里是**最后一句**（硬规矩 5）",
   bool(re.search(r"StyleKit\.Apply\(this,\s*\"群相 安装向导\"\s*\)\s*;\s*\n\s*\}", _L)),
   "")
ok("⑤c ★窗高由「四页里最深那块」反推（分页后新引入的高度口径；不许写死）",
   "bottomMost" in _L and "Math.Max(Math.Max(btnClose.Bottom, cardWelcome.Bottom)" in _L, "")
ok("⑤d 页签高度不写死像素（按 `LineHeight` 实测给）",
   "StyleKit.LineHeight(StyleKit.Ui(StyleKit.TextScale.Small, FontStyle.Bold))" in _L, "")


print("\n── ⑥ `ShortcutInDir` 的口径修复（改名漏改的真 bug）──")
ok("⑥a ★不再把目标写死成单个文件名字面量",
   bool(re.search(r"foreach\s*\(\s*string\s+_n\s+in\s+new\[\]\s*\{", _L)),
   "启动器已改名 `群相.exe`，写死旧名会让老安装每次都再问一遍")
ok("⑥b ★「本进程自己的 exe」是主判据",
   "wants.Add(Path.Combine(here, Application.ExecutablePath))" in _L, "")
ok("⑥c ★反例锚：老名 `一键启动.exe` 作为**候选之一**保留（只认不建）",
   "\"一键启动.exe\"" in _L, "老安装里可能只有老名 ⇒ 不许删")


print("\n── ⑦ `--shot` 逐页出图（四页各一张）──")
for _shot in ("launcher-welcome", "launcher-location", "launcher-done", "launcher-step3"):
    ok("⑦ 探针里有 `%s` 这一张" % _shot, ('"%s"' % _shot) in _L, "")
ok("⑦b 有 `ProbePage(int)` 入口（探针靠它切页，⛔ 不靠真跑流程）",
   "public void ProbePage(int p)" in _L, "")


print("\n── ⑧ 探针不许改用户状态（跑判据不能把窗口位置写脏）──")
# `data\\console_window.txt` 是"用户把控制台窗调成什么样"的存档；探针一关窗就 `SaveGeo()` ⇒ 覆盖它。
# 反例锚：去掉那句 guard 后 ⑧a 必然变红（源码里 `if (ProbeMode) return;` 在 SaveGeo 内首行）。
_SAVEGEO = _L[_L.index("void SaveGeo()"):] if "void SaveGeo()" in _L else ""
_SAVEGEO = _SAVEGEO[: _SAVEGEO.index("catch { }") if "catch { }" in _SAVEGEO else len(_SAVEGEO)]
ok("⑧a ★`SaveGeo` 在探针模式下直接返回（不写 `data\\console_window.txt`）",
   bool(re.search(r"void SaveGeo\(\)\s*\{[^}]*?if\s*\(\s*ProbeMode\s*\)\s*return", _SAVEGEO, re.S)),
   "去掉它 ⇒ 用真 exe 跑一次 `--shot` 就把用户调好的窗口位置覆盖掉")
ok("⑧b ★反例锚：`SaveGeo` 里确实有写盘语句（⑧a 那条 guard 不是空过）",
   "File.WriteAllText(p," in _SAVEGEO, "")


print("\n==== launcher 安装向导判据：%d 通过 / %d 失败 ====" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for _f in FAIL:
        print("  · " + _f)
sys.exit(1 if FAIL else 0)
