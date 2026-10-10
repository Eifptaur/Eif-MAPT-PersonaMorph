# -*- coding: utf-8 -*-
"""安装程序（setup.exe）打包链的判据（群相）。

验的是"装出来能不能用"里那些**不用真跑安装**就能判死的东西：
  · Inno 脚本的关键项有没有写对（免 UAC / 中文 / 塞依赖 / 卸载不删用户数据）；
  · **卸载那两头**：收得掉进程（按安装目录收，不按进程名乱杀）、留得住用户数据；
  · `一键关闭.exe` 的 `--silent` 模式，以及**编出来的 exe 跟不跟得上源码**；
  · 中文语言文件是真的中文（不是 404 落下来的英文 txt）；
  · 构建器会不会把 payload 装错层（多套一层目录 ⇒ 快捷方式指不到 exe）。

⛔ 不验的：真跑一次安装 —— 那要在干净机器上做，属于跨机线（见 PLAN 跨机项）。

用法：`py -3 scripts/installer_pack_selftest.py`
"""
import io
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

INSTALLER_DIR = os.path.join(ROOT, "installer")
ISS = os.path.join(INSTALLER_DIR, "群相.iss")
LANG = os.path.join(INSTALLER_DIR, "ChineseSimplified.isl")

_OK = 0
_FAIL = 0


def ck(name: str, cond: bool, extra: str = "") -> None:
    global _OK, _FAIL
    if cond:
        _OK += 1
        print("  OK    " + name + (("   [%s]" % extra) if extra else ""))
    else:
        _FAIL += 1
        print("  FAIL  " + name + (("   [%s]" % extra) if extra else ""))


def _read(p: str) -> str:
    with io.open(p, encoding="utf-8", errors="replace") as f:
        return f.read()


def t_files() -> None:
    print("\n[installer] 三件套在位（分母守卫）")
    ck("installer/群相.iss 在", os.path.isfile(ISS))
    ck("installer/ChineseSimplified.isl 在", os.path.isfile(LANG))
    ck("scripts/make_installer.py 在",
       os.path.isfile(os.path.join(HERE, "make_installer.py")))


def t_iss_contract() -> None:
    print("\n[installer] Inno 脚本：口径逐条对上")
    if not os.path.isfile(ISS):
        ck("能读到 .iss（前置）", False)
        return
    s = _read(ISS)

    ck("① 免 UAC：PrivilegesRequired=lowest", "PrivilegesRequired=lowest" in s)
    ck("① 默认装到用户目录（不写 Program Files）",
       "DefaultDirName={localappdata}" in s)
    ck("① 目录页关掉（口径：不提供改目录，与启动器 P2 页一致）",
       "DisableDirPage=yes" in s)
    ck("② 塞依赖：Source 指向 PayloadDir",
       "{#PayloadDir}\\*" in s and "recursesubdirs" in s)
    ck("② 不联网：脚本里没有任何下载类指令（DownloadTemporaryFile 等）",
       "DownloadTemporaryFile" not in s and "idpDownload" not in s)
    ck("③ 可足够大：用最重的压缩（lzma2）", "Compression=lzma2/" in s)
    ck("中文界面：Languages 指向 ChineseSimplified.isl",
       "ChineseSimplified.isl" in s)
    ck("产物名带 群相-Setup- 前缀",
       "OutputBaseFilename=群相-Setup-" in s)
    ck("装完拉起 群相.exe（首启准备运行环境）",
       '{app}\\{#AppExe}' in s and "[Run]" in s)
    ck("桌面快捷方式做成可勾选项（不是强塞）",
       'Name: "desktopicon"' in s and "checkedonce" in s)

    # ⛔ 检查前**先剥掉注释行** —— 本仓库的 .iss 注释里正好写了被禁的写法当反例，
    #    不剥的话判据会被自己的注释误伤（实测踩过一次：24 通过 / 1 失败）。
    #    ⭐ 卸载段的深度检查在 t_uninstall_contract()：**一处定义**，别两处漂移。
    code = "\n".join(ln for ln in s.splitlines()
                     if not ln.lstrip().startswith(";")
                     and not ln.lstrip().startswith("//"))

    # ⛔ Inno 自带向导**不许露出来**：界面必须走装完后拉起的自绘四页向导
    #    （用户当场指出"这不系统默认弹窗吗"）。关页 + 隐藏窗口，两者缺一不可。
    ck("向导页全部关掉（Welcome/Dir/Ready/Finished/StartupPrompt）",
       all(k in code for k in ("DisableWelcomePage=yes", "DisableDirPage=yes",
                               "DisableReadyPage=yes", "DisableFinishedPage=yes",
                               "DisableStartupPrompt=yes")))
    ck("[Code] 段隐藏了 Inno 窗口（界面交给自绘向导）",
       "[Code]" in code and "WizardForm.Visible := False" in code)
    ck("隐藏写在两个回调里（InitializeWizard + CurPageChanged，双保险）",
       code.count("WizardForm.Visible := False") >= 2)


def t_uninstall_contract() -> None:
    """卸载这条链的两头：**收得掉进程**（否则文件被占用、删不干净）、
    **留得住用户数据**（人设/记忆/配置不许跟着程序一起走）。"""
    print("\n[installer] 卸载：收得掉进程、留得住用户数据")
    if not os.path.isfile(ISS):
        ck("能读到 .iss（前置）", False)
        return
    s = _read(ISS)
    code = "\n".join(ln for ln in s.splitlines()
                     if not ln.lstrip().startswith(";")
                     and not ln.lstrip().startswith("//"))

    # ── A. 卸载前先收进程：手法必须精确到"本安装目录"，不能按进程名乱杀 ──
    ck("A1 有 [UninstallRun] 段（卸载前收进程，否则文件被占用删不动）",
       "[UninstallRun]" in code)
    ck("A2 ★先走我们自己的「一键关闭」：它按**可执行文件在不在安装目录下**判，"
       "才收得掉启动器拉起的 runtime\\python\\python.exe 子进程",
       "{app}\\一键关闭.exe" in code)
    ck("A3 用无界面模式调它（卸载流程里不能弹个窗等人点「好的」）",
       '"--silent"' in code)
    # ⛔ 反向判据：按进程名杀 python 会连**用户自己跑的** python 一起杀掉
    ck("A4 ⛔**不许**按名字杀 python/pythonw（会误杀用户的进程）",
       re.search(r"/im\s+pythonw?\.exe", code, re.I) is None)
    ck("A5 兜底那条只杀我们自己的 exe", "/im {#AppExe}" in code)
    ck("A6 兜底带 /t（连带**子进程树**，把它的 python 子进程一并带走）",
       re.search(r"/im\s+\{#AppExe\}\s+/t", code) is not None)
    ck("A7 每条 RunOnceId 都给了（不给 Inno 会告警、且重复卸载时重复执行）",
       code.count("RunOnceId:") >= 2)

    # ── B. 白名单式删除：程序产物清干净、用户数据一个不碰 ────────────
    ck("B1 清 runtime\\（程序自己解出来的依赖，卸载后最大的一块残留）",
       "{app}\\runtime" in code)
    ck("B2 清 releases\\（自更新在 versioned 布局下的版本目录）",
       "{app}\\releases" in code)
    ck("B3 清各子目录的 __pycache__（Inno 只删它装进去的文件，.pyc 不在清单里）",
       code.count("__pycache__") >= 4)
    ck("B4 ⛔**不许**把整个 {app} 全删",
       'Type: filesandordirs; Name: "{app}"' not in code)
    # ⛔ 最要紧的一条：用户数据一个都不许出现在删除清单里
    _userstuff = ["data", "config.json", "logs", "wechatauto_logs", "报告"]
    _leak = [u for u in _userstuff
             if re.search(r'Name:\s*"\{app\}\\%s' % re.escape(u), code)]
    ck("B5 ★用户数据（data/config.json/logs/wechatauto_logs/报告）一个都不许删",
       not _leak, "出现在删除清单里：%s" % _leak)

    # ── C. 装/卸都**自动**收进程，而不是弹一句"请先关闭"逼用户手动做 ──
    ck("C1 CloseApplications=yes（装的时候自动关掉占着待换文件的进程）",
       "CloseApplications=yes" in code)
    ck("C2 RestartApplications=no（它默认 yes ⇒ 会把它自己关掉的再拉起来、与 [Run] 撞成双开）",
       "RestartApplications=no" in code)
    ck("C3 没配 AppMutex（那东西只会在安装/卸载启动时弹「请先关闭应用」，反而要用户动手）",
       "AppMutex" not in code)

    # ── E. 反向锚：把用户数据塞进删除清单 ⇒ B5 必须报红 ──────────────
    _probe = code + '\nType: filesandordirs; Name: "{app}\\data"\n'
    _hit = [u for u in _userstuff
            if re.search(r'Name:\s*"\{app\}\\%s' % re.escape(u), _probe)]
    ck("E1 ★反向锚：把 data 塞进删除清单后 B5 那条必须报红（说明它真在起作用）",
       bool(_hit),
       "塞进去后命中的条目：%s" % _hit)


def t_closer_silent() -> None:
    """「一键关闭.exe」这头：它得有无界面模式，且**只碰我们安装目录下**的进程。"""
    print("\n[installer] 一键关闭.exe：无界面模式 + 「只碰自己目录」铁律")
    cs = os.path.join(ROOT, "launcher-src", "close.cs")
    if not os.path.isfile(cs):
        ck("launcher-src/close.cs 在（前置）", False)
        return
    src = _read(cs)
    ck("D1 有 --silent 分支（给卸载/覆盖安装用）", '"--silent"' in src)
    ck("D2 该分支直接 return（不 ShowDialog ⇒ 不会在卸载流程里卡住等人点）",
       re.search(r'"--silent"[\s\S]{0,900}?\n\s*return;', src) is not None)
    ck("D3 复用同一套 KillAll（不另写一份收进程逻辑）",
       '"--silent"' in src and "KillAll(root)" in src)
    ck("D4 ⛔铁律在位：只收「可执行文件在本安装目录下」的进程",
       "exeUnderRoot" in src and "StartsWith(rootLower)" in src)
    ck("D5 ⛔铁律在位：不是我们的目录一律不碰（那行闸门）",
       "if (!exeUnderRoot && !wv2Ours && !scriptOurs) continue;" in src)

    # ── 编译产物是否**跟着源码走** ────────────────────────────────────
    # ⛔ 这条抓的是"改了 close.cs 却没重编译"：那时源码里看着万事俱备，
    #    用户机器上跑的却还是旧的 exe（本轮实测：加了新分支后 exe 字节数**一模一样**，
    #    只看字节数根本发现不了；只有查 exe 里有没有那个字符串才判得出来）。
    exe = os.path.join(ROOT, "一键关闭.exe")
    if not os.path.isfile(exe):
        ck("D6 ★一键关闭.exe 在（卸载时靠它按目录收进程，必须随包发布）", False, exe)
        return
    _exe_t = os.path.getmtime(exe)
    _newer = [rel for rel in ("launcher-src/close.cs", "launcher-src/stylekit.cs")
              if os.path.isfile(os.path.join(ROOT, rel))
              and os.path.getmtime(os.path.join(ROOT, rel)) > _exe_t + 1]
    ck("D6 ★一键关闭.exe **不落后于**它的源文件（改了源码就要重编译）",
       not _newer, "这些比 exe 新：%s" % _newer)
    with open(exe, "rb") as f:
        _raw = f.read().decode("utf-16-le", "ignore")  # C# 字符串字面量是 UTF-16
    ck("D7 ★exe 里**真有** --silent（源码改了但没重编译 ⇒ 这里会红）",
       "--silent" in _raw)


def t_lang_real() -> None:
    print("\n[installer] 中文语言文件是真的中文")
    if not os.path.isfile(LANG):
        ck("语言文件在位（前置）", False)
        return
    raw = _read(LANG)
    ck("不是 404 落下来的短文本（>10 KB）", len(raw) > 10000, "%d 字节" % len(raw))
    ck("含 Inno 语言文件头（版本声明）",
       "Inno Setup version" in raw and "Chinese" in raw)
    ck("正文里有中文（真翻译，不是英文占位）",
       any("\u4e00" <= ch <= "\u9fff" for ch in raw))
    ck("含关键消息键（编译器会按它渲染界面）",
       "LanguageName=" in raw and "SetupAppTitle=" in raw)


def t_builder_logic() -> None:
    print("\n[installer] 构建器：装错层的坑挡住了")
    try:
        import make_installer as mi  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        ck("能 import 构建器", False, str(e))
        return

    # ① _payload_root：zip 里只有一个顶层目录时，要往里钻一层
    d = tempfile.mkdtemp()
    try:
        inner = os.path.join(d, "persona morph")
        os.makedirs(inner)
        with open(os.path.join(inner, "群相.exe"), "wb") as f:
            f.write(b"x")
        orig = mi.PAYLOAD_DIR
        try:
            mi.PAYLOAD_DIR = d
            got = mi._payload_root()
        finally:
            mi.PAYLOAD_DIR = orig
        ck("单顶层目录时钻进内层（不会装出 群相.exe 的上一级）",
           os.path.abspath(got) == os.path.abspath(inner), got)
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    # ② 多顶层项时保持原层
    d2 = tempfile.mkdtemp()
    try:
        for n in ("a", "b"):
            os.makedirs(os.path.join(d2, n))
        orig = mi.PAYLOAD_DIR
        try:
            mi.PAYLOAD_DIR = d2
            got = mi._payload_root()
        finally:
            mi.PAYLOAD_DIR = orig
        ck("多个顶层项时保持原层", os.path.abspath(got) == os.path.abspath(d2), got)
    finally:
        import shutil
        shutil.rmtree(d2, ignore_errors=True)

    # ③ iscc 定位：要么找到真编译器，要么给出可执行的安装指引
    iscc = mi.find_iscc()
    if iscc:
        ck("能定位 Inno 编译器", os.path.isfile(iscc), iscc)
    else:
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            mi._hint_no_iscc()
        msg = buf.getvalue()
        ck("没装 Inno 时给出可执行的安装指引（含 winget 命令）",
           "winget install" in msg and "JRSoftware.InnoSetup" in msg)

    # ④ 分母守卫：payload 缺 群相.exe 或缺 offline/wheels 必须拒绝出包
    src = _read(os.path.join(HERE, "make_installer.py"))
    ck("校验 payload 里有 群相.exe（缺了就拒绝出包）",
       'os.path.join(root, "群相.exe")' in src)
    ck("校验 payload 里有 offline/wheels（缺了就拒绝，防'装完还要联网'）",
       '"offline", "wheels"' in src)
    ck("用 --slim 出含依赖包（不是 --no-wheels 的在线包）",
       '"--slim"' in src and "--no-wheels" not in src)


def main() -> int:
    t_files()
    t_iss_contract()
    t_uninstall_contract()
    t_closer_silent()
    t_lang_real()
    t_builder_logic()
    print("\n%d 通过 / %d 失败" % (_OK, _FAIL))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
