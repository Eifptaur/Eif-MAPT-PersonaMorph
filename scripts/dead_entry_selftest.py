# -*- coding: utf-8 -*-
"""「让用户去跑的入口文件必须真实存在」的判据。

**为什么要它**：。而这一轮实测抓到最刺眼的一条：
产品里有**多处**让，可**这个文件在包里根本不存在**
（`Get-ChildItem -Recurse -Filter '检查微信版本*'` ⇒ 0 命中；全仓 `git ls-files` 也没有它）——
用户按指引去找，只会找不到文件，然后来报「用不了」。这类**死指引**比功能缺陷更伤：
它把我们的问题变成用户的困惑。

判据只认三类**会让人去找文件**的写法（避免把普通叙述当成指引）：
  ① `"X.cmd|bat|exe|ps1"` / `'X.cmd…'` / `` `X.cmd…` `` / 「X.cmd…」（引号或书名号包着的）
  ② 带路径分隔的引用（`scripts\\setup_python.ps1`、`lib\\WebView2Loader.dll` 之类）
  ③ 动作词后面的裸名（`双击 X.cmd` / `运行 X.bat` / `执行 X.ps1`）
每条命中都要能在仓库里找到同名文件（`git ls-files` 的清单里），否则**红**。
反例锚：凭空造一处引用 ⇒ 必须红（判据自己带灵敏度自证）。

跑法：runtime\\python\\python.exe scripts\\dead_entry_selftest.py
"""
import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception: # noqa: BLE001
    pass

PASS, FAIL = [0], [0]


def ok(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("  [%s]" % detail if detail else ""))
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
    return bool(cond)


EXT = r"(?:cmd|bat|exe|ps1)(?![A-Za-z0-9_])" # ⛔ 不加这个前瞻：`sys.executable` 会被当成 `sys.exe`
#: 三类"让人去找文件"的写法
PATS = (
    re.compile(r"[\"'`「]([^\"'`「」\s]{1,60}\." + EXT + r")[\"'`」]"),
    re.compile(r"([A-Za-z0-9_\u4e00-\u9fff\\/.\-]{2,60}\." + EXT + r")"),
    re.compile(r"(?:双击|运行|执行|打开|点开)\s*([^\s，。、）)」\"'`]{1,40}\." + EXT + r")"),
)

#: 明确**不该存在**或**不在本仓**的引用（每条都要有理由；只许在证明它是反例时加）
OK_LIST = {
    "一键启动.bat": "fullcheck 里的**反例**：断言这些老入口不该存在",
    "安装依赖.bat": "同上（反例）",
    "自检.bat": "同上（反例）",
    "py -3": "不是文件名（命令片段）",
    "python.exe": "系统/便携解释器，不是随包入口",
    "cmd.exe": "系统自带",
    "powershell.exe": "系统自带",
    "csc.exe": "系统自带编译器（AGENTS.md 里记的编译命令）",
    "backend.exe": "对标对象（张苹果那套）自己的产物，不是我们的文件",
    "WeChat.exe": "微信本体，不是我们的文件",
    "Weixin.exe": "微信进程名（我们按进程找微信），不是随包文件",
    "yt-dlp.exe": "外部下载工具（注释里提的备选，不随包）",
    "Agent启动器.exe": "另一个项目（Agent启动器）的产物，不在本仓",
    "py.exe": "系统 Python 启动器",
    "pythonw.exe": "系统/便携解释器的无窗版",
    "wechat.exe": "微信本体进程名",
    "silk_v3_decoder.exe": "按需下载的语音解码器（不随包）",
    "silk_v3_encoder.exe": "同上（不随包）",
    "weixin.exe": "微信进程名（大小写不同）",
    "chrome.exe": "用户的浏览器（回退路径要开它）",
    "msedge.exe": "同上（Edge）",
    "firefox.exe": "同上（Firefox）",
    "code.exe": "VS Code（判据里当「用户正在用的别的程序」用）",
    "conhost.exe": "系统控制台宿主",
    "cscript.exe": "系统脚本宿主（判据里当外部进程用）",
    "explorer.exe": "系统资源管理器",
    "wmic.exe": "系统工具（判据里当被调用的外部程序用）",
    "msedgewebview2.exe": "WebView2 的宿主进程",
    "node.exe": "Node（DSH/启动器用）",
    "pwsh.exe": "PowerShell 7",
    "wscript.exe": "系统脚本宿主",
    "notepad.exe": "记事本（判据里当「别的程序」用）",
    # 判据自己造的反例/夹具名（它们本来就**不该**存在，正是用来验判据灵敏度的）
    "X.bat": "死指引判据的反例夹具", "X.cmd": "同上", "X.ps1": "同上",
    "sys.exe": "同上（顺带验 `sys.executable` 不被误认成文件）",
    "没有这个文件的东西.bat": "同上",
    "t.cmd": "cmd 入口判据的临时夹具", "t_lf.cmd": "同上（LF 行尾反例）",
    "result.cmd": "decide_ui 判据的夹具",
    "ps1": "cmd 入口判据里是扩展名本身",
    "python%.exe": "cmd 里的环境变量模板（不是文件名）",
    "%python%.exe": "同上",
}

#: 只扫**产品自己会看到**的那些文本（AGENTS.md 是开发文档，里面记着别人的产物与外部工具）
SCAN_DIRS = ("agent", "scripts")
SCAN_ROOT_EXT = (".cmd", ".md", ".txt")
SKIP_FILES = {"AGENTS.md"}
#: 带路径的引用只在**本仓自己的根目录**下才当回事
LOCAL_ROOTS = ("scripts/", "scripts\\", "lib/", "lib\\", "assets/", "assets\\",
               "agent/", "agent\\", "launcher-src/", "launcher-src\\", "runtime/", "runtime\\")
FILES = []
for d in SCAN_DIRS:
    for fn in sorted(os.listdir(os.path.join(ROOT, d))):
        if fn.endswith((".py", ".md")):
            FILES.append(os.path.join(ROOT, d, fn))
for fn in sorted(os.listdir(ROOT)):
    if fn.endswith(SCAN_ROOT_EXT) and fn not in SKIP_FILES:
        FILES.append(os.path.join(ROOT, fn))


def tracked():
    """仓库里真实存在的文件（相对路径，正斜杠）；取不到就退回磁盘列举。"""
    try:
        r = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return set(x.decode("utf-8", "replace").replace("\\", "/") for x in r.stdout.split(b"\0") if x)
    except Exception: # noqa: BLE001
        out = set()
        for base, _dirs, files in os.walk(ROOT):
            for f in files:
                out.add(os.path.relpath(os.path.join(base, f), ROOT).replace("\\", "/"))
        return out


TRACKED = tracked()
BASENAMES = {os.path.basename(p) for p in TRACKED}


def refs_in(text):
    """这份文本里所有"让人去找文件"的引用（去重、去噪）。"""
    out = set()
    for pat in PATS:
        for m in pat.finditer(text):
            name = m.group(1).strip(" .。，、）)\"'`「」*#")
            if not name or name in OK_LIST or os.path.basename(name.replace("\\", "/")) in OK_LIST:
                continue
            if name.startswith(("http", "//")):
                continue
            low = name.replace("\\", "/")
            if "/" in low or ":" in low or low.startswith("~"):
                # 带路径的：只认本仓自己的根（外部工具、别的产品、用户目录一概不管）
                if not low.startswith(tuple(r.replace("\\", "/") for r in LOCAL_ROOTS)):
                    continue
            out.add(name)
    return out


def missing_in(text):
    bad = []
    for name in sorted(refs_in(text)):
        base = os.path.basename(name.replace("\\", "/"))
        if base in BASENAMES or name.replace("\\", "/") in TRACKED:
            continue
        # 归一化：正则可能把紧邻的汉字一起吃进来（`一次「一键启动.exe`）⇒ 从最长的合法后缀里挑
        norm = None
        for k in range(1, len(name)):
            if name[k:] in BASENAMES:
                norm = name[k:]
                break
        if norm:
            continue
        bad.append(name)
    return bad


def main():
    print("== A. 全仓对账：产品/脚本里提到的入口文件必须真实存在 ==")
    bad = {}
    for f in FILES:
        try:
            txt = io.open(f, encoding="utf-8", errors="replace").read()
        except Exception: # noqa: BLE001
            continue
        b = missing_in(txt)
        if b:
            bad[os.path.relpath(f, ROOT).replace("\\", "/")] = b
    flat = [(k, n) for k, v in sorted(bad.items()) for n in v]
    ok("A1 %d 份文本里提到的入口文件都能在仓库里找到（找不到的一个都不许有）" % len(FILES),
       not flat, "找不到：" + "; ".join("%s ⇒ %s" % (k, n) for k, n in flat[:20]))

    print("== B. 反向锚：判据自己也抓得住凭空造的引用 ==")
    # 夹具名**运行时拼**（不在源码里以整词出现）⇒ 不会被 A 段自己扫到，也不用进 OK_LIST
    _fake = "没有" + "的这个" + "文件" + ".bat"
    _fk = "运行 " + _fake + " 试试"
    ok("B1 合成一句死指引 ⇒ 判据报它找不到", missing_in(_fk) == [_fake], str(missing_in(_fk)))
    _ok_case = "双击 一键启动.exe"
    ok("B2 合成一句**真实存在**的指引 ⇒ 判据不误报", missing_in(_ok_case) == [], str(missing_in(_ok_case)))
    _quote_case = "或运行「" + "那个不存在的版本检查器" + ".bat --update」"
    ok("B3 `--update` 这种带参数、带书名号的写法也认得出来（本轮真缺陷的原文形态）",
       missing_in(_quote_case) != [], str(missing_in(_quote_case)))

    print("== C. 三个真入口在位（用户真正会双击的东西）==")
    for nm in ("一键启动.exe", "一键关闭.exe", "一键检验（生成报告）.cmd", "一键体检（只读，不发消息）.cmd"):
        ok("C 存在：%s" % nm, os.path.exists(os.path.join(ROOT, nm)))

    print("== 死指引判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
    return 0 if FAIL[0] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
