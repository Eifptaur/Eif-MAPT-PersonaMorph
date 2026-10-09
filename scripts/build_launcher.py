# -*- coding: utf-8 -*-
"""编译启动器 exe（群相.exe / 一键关闭.exe）。

为什么要这个脚本：README 里给的是**手敲的 PowerShell 命令**，而编译所需的三件事
（引用的 dll、要一起编的 .cs、图标）全靠人记 —— 少带一个 `.cs` 就是 CS0246（找不到类型），
漏一个 `/r:` 就是"dll 找到了但类型不认"。把清单固化在这里，改完源码一条命令重编。

⛔ 编译必须带的文件（漏任何一个都编不过，来源：`launcher-src/README.md`）：
    · `launcher.cs`        —— 主程序 + 四页安装向导（`LauncherForm`）
    · `stylekit.cs`        —— 两个 exe 共用的自绘控件（StyleKit / RoundButton / WizardTabs / CardCheck）
    · `wingliphs.cs`       —— `GlyphButton`（launcher.cs 里用了 8 处）
    · `webview2guide.cs`   —— `WebView2Guide` / `WebView2MissingForm`（用了 3 处）
    ⚠️ `close.cs` **不用**后两个（实测引用数 = 0）⇒ 关闭器那条单独走。

用法：
  py -3 scripts/build_launcher.py            # 编两个 exe
  py -3 scripts/build_launcher.py --launcher  # 只编 群相.exe
  py -3 scripts/build_launcher.py --closer    # 只编 一键关闭.exe
  py -3 scripts/build_launcher.py --dry-run   # 只打印命令，不编
"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "launcher-src")

#: .NET Framework 自带编译器（Windows 上一定有；不用装 SDK）
CSC_CANDIDATES = (
    r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe",
)

#: 群相.exe 要引用的程序集（WebView2 两个；`WebView2Loader.dll` 由 exe 同目录提供）
LAUNCHER_REFS = (
    os.path.join(ROOT, "lib", "Microsoft.Web.WebView2.Core.dll"),
    os.path.join(ROOT, "lib", "Microsoft.Web.WebView2.WinForms.dll"),
)
#: 群相.exe 的源文件清单（顺序不重要，齐全才重要）
LAUNCHER_SRC = ("launcher.cs", "stylekit.cs", "wingliphs.cs", "webview2guide.cs")
#: 一键关闭.exe 的源文件清单（不带 wingliphs / webview2guide）
CLOSER_SRC = ("close.cs", "stylekit.cs")
CLOSER_REFS = ("System.Management.dll",)


def find_csc() -> str:
    for c in CSC_CANDIDATES:
        if os.path.isfile(c):
            return c
    return shutil.which("csc") or ""


def _cmd(csc: str, out_name: str, srcs, refs, icon: str) -> list:
    cmd = [csc, "/nologo", "/target:winexe", "/optimize+"]
    if icon:
        cmd.append("/win32icon:" + icon)
    for r in refs:
        cmd.append("/r:" + r)
    cmd.append("/out:" + os.path.join(ROOT, out_name))
    for s in srcs:
        cmd.append(os.path.join(SRC, s))
    return cmd


def build(name: str, srcs, refs, icon: str, dry: bool) -> bool:
    """编译一个 exe。先编到 `.new`，成功了再替换 —— 编挂了不会把能用的 exe 弄丢。"""
    csc = find_csc()
    if not csc:
        print("找不到 .NET 编译器（csc.exe）。", file=sys.stderr)
        return False

    # 分母守卫：清单里的源文件必须都在（少一个就是"编出来的 exe 少功能"）
    missing = [s for s in srcs if not os.path.isfile(os.path.join(SRC, s))]
    if missing:
        print("源文件清单里这些不在：%s" % "、".join(missing), file=sys.stderr)
        return False
    missing_r = [r for r in refs if os.path.isabs(r) and not os.path.isfile(r)]
    if missing_r:
        print("引用的程序集不在：%s" % "、".join(missing_r), file=sys.stderr)
        return False

    tmp_name = name.replace(".exe", "") + ".new.exe"
    cmd = _cmd(csc, tmp_name, srcs, refs, icon)
    print("[build] %s ← %s" % (name, "、".join(srcs)))
    if dry:
        print("        " + " ".join(cmd))
        return True

    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    errs = [ln for ln in out.splitlines() if ": error " in ln]
    warns = [ln for ln in out.splitlines() if ": warning " in ln]
    if r.returncode != 0 or errs:
        print("编译失败：", file=sys.stderr)
        for ln in errs[:20]:
            print("  " + ln, file=sys.stderr)
        return False
    print("        0 error / %d warning" % len(warns))

    final = os.path.join(ROOT, name)
    tmp = os.path.join(ROOT, tmp_name)
    try:
        # 旧 exe 留一份只读备份：替换失败时还能回去（Windows 下运行中的 exe 改名是可以的）
        if os.path.isfile(final):
            shutil.copy2(final, final + ".prev")
        shutil.move(tmp, final)
    except Exception as e:  # noqa: BLE001
        print("替换 %s 失败：%s（编好的在 %s）" % (name, e, tmp), file=sys.stderr)
        return False
    print("        → %s（%.0f KB）" % (final, os.path.getsize(final) / 1024.0))
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="编译启动器 exe")
    ap.add_argument("--launcher", action="store_true", help="只编 群相.exe")
    ap.add_argument("--closer", action="store_true", help="只编 一键关闭.exe")
    ap.add_argument("--dry-run", action="store_true", help="只打印命令")
    a = ap.parse_args()

    do_launcher = a.launcher or not (a.launcher or a.closer)
    do_closer = a.closer or not (a.launcher or a.closer)
    icon = os.path.join(ROOT, "assets", "exe.ico")
    ok = True
    if do_launcher:
        ok = build("群相.exe", LAUNCHER_SRC, LAUNCHER_REFS, icon, a.dry_run) and ok
    if do_closer:
        ok = build("一键关闭.exe", CLOSER_SRC, CLOSER_REFS, icon, a.dry_run) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
