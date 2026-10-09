# -*- coding: utf-8 -*-
"""安装程序（setup.exe）打包链的判据（群相）。

验的是"装出来能不能用"里那些**不用真跑安装**就能判死的东西：
  · Inno 脚本的关键项有没有写对（免 UAC / 中文 / 塞依赖 / 卸载不删用户数据）；
  · 中文语言文件是真的中文（不是 404 落下来的英文 txt）；
  · 构建器会不会把 payload 装错层（多套一层目录 ⇒ 快捷方式指不到 exe）。

⛔ 不验的：真跑一次安装 —— 那要在干净机器上做，属于跨机线（见 PLAN 跨机项）。

用法：`py -3 scripts/installer_pack_selftest.py`
"""
import io
import os
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

    # ⛔ 最关键的一条：卸载**不许**把整个安装目录删掉。
    #    检查前**先剥掉注释行** —— 本仓库的 .iss 注释里正好写了这句被禁的写法当反例，
    #    不剥的话判据会被自己的注释误伤（实测踩过一次：24 通过 / 1 失败）。
    code = "\n".join(ln for ln in s.splitlines()
                     if not ln.lstrip().startswith(";")
                     and not ln.lstrip().startswith("//"))
    ck("⛔ 卸载不删整个 {app}（否则会连 data/logs/config.json 一起删）",
       'Type: filesandordirs; Name: "{app}"' not in code)
    ck("卸载只清程序自己的缓存目录（__pycache__）",
       "__pycache__" in code)

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
    t_lang_real()
    t_builder_logic()
    print("\n%d 通过 / %d 失败" % (_OK, _FAIL))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
