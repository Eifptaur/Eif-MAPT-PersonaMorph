# -*- coding: utf-8 -*-
"""入口脚本（.cmd）判据（2026-09-15，两轮跨机实测逼出来的）。

**第一轮**：跨机那台（Win10 22H2 · ACP=OEMCP=936）上，包里的两个 `.cmd` **双击一行都跑不动**。
6 格对照（同内容只改编码/行尾）：**LF 三种编码全断、CRLF 三种全通** ⇒ 打包缺陷（UTF-8 无 BOM
+ 纯 LF），根因是 cmd.exe 解析不了 LF 行尾的 `if ... goto` / `for /f` / 括号块。

**第二轮**：`.cmd` 能跑了，但**安装路径含中文**时又踩一个：`setup_python.ps1` 用
`-Encoding Default`（中文机＝GBK）写 `logs\\python_path.txt`，`.cmd` 在 `chcp 65001` 下用
`for /f` 读 ⇒ 路径里的中文变乱码 ⇒ `if exist` 判否 ⇒ 静默回落到**系统 Python 3.14** 去跑
pip 源码编译（卡几分钟、多半失败）。修法＝setup 之后**先直接试 `runtime\\python\\python.exe`**，
不依赖那个文本文件。

这条判据守五件事（只读 + 临时目录，不联网、不动微信）：
  A. 随包的 `.cmd` 一律 **CRLF 且无 BOM**、UTF-8 可解码。
  B. 两个入口该有的步骤还在（`chcp 65001` · `cd /d "%~dp0"` · setup_python · setup_deps ·
     collect_report · `:deps` 标签 · `pause`），且**没有 `if ... set X & ...` 这种 `&` 串联坑**
     （`&` 后面的命令会无条件执行）。
  C. 只读那一版**不许带** `--send-test` / `--allow-send`；检验版必须带，且横幅写明会放行版本门。
  D. **行为对照**：真用 `cmd /c` 跑中性化后的脚本 ⇒ CRLF 版必须走完 goto 链打出标记；
     LF 版只记录不断言（本机控制台代码页可能已是 65001）。
  E. **非 ASCII 路径回归（P8）**：中文目录 + setup 之后才出现运行时 ⇒ 必须仍走自带 Python、
     **不许**回落到 `py`；反面再用"老写法只读 python_path.txt"证明那条路在中文路径下必断。

用法：`py -3 scripts/cmd_entry_selftest.py`
"""
import os
import re
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

CHECK = "一键检验（生成报告）.cmd"
ONLY = "一键体检（只读，不发消息）.cmd"

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✔ %s" % name)
    else:
        FAIL += 1
        print("  ✘ %s %s" % (name, ("— " + str(detail)) if detail else ""))


def raw(name):
    with open(os.path.join(ROOT, name), "rb") as f:
        return f.read()


def text(name):
    return raw(name).decode("utf-8")


# ── A 行尾 / 编码 ──────────────────────────────────────────────────────
print("\n[一] 行尾与编码（跨机双击跑不动的直接原因）")
for nm in (CHECK, ONLY):
    b = raw(nm)
    crlf = b.count(b"\r\n")
    bare = b.count(b"\n") - crlf
    ok("%s：存在且非空" % nm, len(b) > 300, "len=%d" % len(b))
    ok("%s：纯 CRLF（bare LF = 0）" % nm, bare == 0 and crlf > 0,
       "crlf=%d bareLF=%d ⇒ cmd.exe 会在 %s 上解析失败" % (crlf, bare, nm))
    ok("%s：无 BOM（BOM 会让第一行变成乱码命令）" % nm, b[:3] != b"\xef\xbb\xbf")
    try:
        text(nm)
        ok("%s：UTF-8 可解码" % nm, True)
    except Exception as e:
        ok("%s：UTF-8 可解码" % nm, False, str(e)[:80])
    ok("%s：含 chcp 65001（中文才显示得出来）" % nm, "chcp 65001" in text(nm))
    ok("%s：含 cd /d \"%%~dp0\"（切到包目录）" % nm, 'cd /d "%~dp0"' in text(nm))

# ── B 两个入口该有的步骤 ────────────────────────────────────────────────
print("\n[二] 入口步骤（两轮跨机实测抓到的缺口）")
for nm in (CHECK, ONLY):
    t = text(nm)
    ok("%s：会准备 Python（setup_python.ps1）" % nm, "setup_python.ps1" in t)
    ok("%s：**会装依赖**（setup_deps.py —— 原来只在 一键启动.onestart 里调）" % nm,
       "setup_deps.py" in t)
    ok("%s：会跑 collect_report.py" % nm, "collect_report.py" in t)
    ok("%s：goto 链完整（:deps 标签在）" % nm, re.search(r"^:deps\s*$", t, re.M) is not None)
    ok("%s：出错时不会一闪而过（有 pause）" % nm, "pause" in t)
    # P8：setup 之后必须先直接试 runtime\python\python.exe，不许只靠 logs\python_path.txt
    i = t.find("setup_python.ps1")
    after = t[i:] if i >= 0 else ""
    ok("%s：setup 之后**先直接试** runtime\\python\\python.exe（P8，不依赖那个文本文件）" % nm,
       'if exist "runtime\\python\\python.exe" (' in after)
    ok("%s：没有 `if ... set X & ...` 串联坑（`&` 后面的命令会无条件执行）" % nm,
       re.search(r'^if .*set ".*" &', t, re.M) is None)

t_check, t_only = text(CHECK), text(ONLY)
ok("只读版：**不带** --send-test", "--send-test" not in t_only)
ok("只读版：**不带** --allow-send", "--allow-send" not in t_only)
ok("检验版：带 --send-test（会真发一条给文件传输助手）", "--send-test" in t_check)
ok("检验版：带 --allow-send（解「版本门拦住发送实测」的鸡生蛋）", "--allow-send" in t_check)
ok("检验版：横幅里写明了会放行版本门（不许悄悄降级）",
   "版本门" in t_check and "放行" in t_check)


# ── 跑 cmd 的两个小工具 ─────────────────────────────────────────────────
def neuter(t, setup_fake=False):
    """把真正会干活的命令行换成 echo 标记，保留全部控制流（if/goto/for/括号块）。"""
    out = []
    for ln in t.split("\n"):
        s = ln.strip()
        if s.lower() == "pause":
            out.append("rem pause")
        elif "setup_python.ps1" in s and setup_fake:
            out.append('echo MARKER-PY & mkdir "runtime\\python" 2>nul & '
                       'echo dummy> "runtime\\python\\python.exe"')
        elif "setup_python.ps1" in s:
            out.append("echo MARKER-PY")
        elif "setup_deps.py" in s:
            out.append("echo MARKER-DEPS")
        elif "collect_report.py" in s:
            out.append("echo MARKER-REPORT")
        elif 'set "PYCMD=py"' in s:
            out.append("echo MARKER-PYFALLBACK")
        else:
            out.append(ln)
    return "\n".join(out)


def run_script(src_text, cwd, newline="\r\n", name="t.cmd", timeout=40):
    p = os.path.join(cwd, name)
    data = src_text.replace("\r\n", "\n").replace("\n", newline)
    with open(p, "wb") as f:
        f.write(data.encode("utf-8"))
    try:
        r = subprocess.run(["cmd", "/c", p], cwd=cwd, capture_output=True, timeout=timeout)
        out = (r.stdout or b"").decode("utf-8", "replace") + \
              (r.stderr or b"").decode("utf-8", "replace")
        return r.returncode, out
    except Exception as e:
        return -1, "跑不起来: %s" % e


def mk_cn_dir():
    """建一个**含中文**的临时目录（P8 只在这种路径下才复现）。"""
    d = tempfile.mkdtemp(prefix="pm-cmdtest-")
    cn = os.path.join(d, "跨机测试-r2-中文目录")
    os.makedirs(cn)
    return d, cn


# ── C 行为对照（真跑 cmd）──────────────────────────────────────────────
print("\n[三] 行为对照：真用 cmd /c 跑一遍（动作行换成 echo，去掉 pause）")
base = neuter(text(CHECK)).replace("\r\n", "\n")
d1 = tempfile.mkdtemp(prefix="pm-cmdtest-")

rc_ok, out_ok = run_script(base, d1, "\r\n")
ok("CRLF 版：能走完 goto 链到 :deps（打出 MARKER-DEPS）", "MARKER-DEPS" in out_ok,
   "rc=%s 输出前 120 字=%r" % (rc_ok, out_ok[:120]))
ok("CRLF 版：能走到最后一步（打出 MARKER-REPORT）", "MARKER-REPORT" in out_ok, "rc=%s" % rc_ok)

rc_lf, out_lf = run_script(base, d1, "\n", name="t_lf.cmd")
# 不断言 LF 必失败：本机控制台代码页可能是 65001（跨机那台是 936）。只把事实记下来。
print("  · 参考（不断言）：LF 版 rc=%s · 打出 MARKER-REPORT=%s" % (
    rc_lf, "MARKER-REPORT" in out_lf))
if "MARKER-REPORT" not in out_lf:
    print("    ⇒ 本机也复现了「LF 版一行都跑不动」（与跨机那台一致）")

# ── D P8 回归：非 ASCII 路径 ────────────────────────────────────────────
print("\n[四] 非 ASCII 路径回归（P8：中文路径下不许静默回落到系统 Python）")
_d2, cn2 = mk_cn_dir()
base_cn = neuter(text(CHECK), setup_fake=True).replace("\r\n", "\n")
rc_cn, out_cn = run_script(base_cn, cn2, "\r\n")
ok("中文路径 + setup 装好运行时：仍走自带 Python（打出 MARKER-REPORT）",
   "MARKER-REPORT" in out_cn, "rc=%s 输出末尾=%r" % (rc_cn, out_cn[-200:]))
ok("中文路径：**没有**回落到系统 py（不出现 MARKER-PYFALLBACK）",
   "MARKER-PYFALLBACK" not in out_cn, "输出末尾=%r" % (out_cn[-200:],))

OLD_STYLE = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYCMD=runtime\\python\\python.exe"
if exist "%PYCMD%" goto ok
if exist "logs\\python_path.txt" for /f "usebackq delims=" %%i in ("logs\\python_path.txt") do set "PYCMD=%%i"
if exist "%PYCMD%" (echo MARKER-OLD-RESOLVED) else (echo MARKER-OLD-MOJIBAKE)
goto :eof
:ok
echo MARKER-OLD-RESOLVED
"""
_d3, cn3 = mk_cn_dir()
os.makedirs(os.path.join(cn3, "logs"))
os.makedirs(os.path.join(cn3, "realpy"))
with open(os.path.join(cn3, "realpy", "python.exe"), "wb") as f:
    f.write(b"dummy")
real = os.path.join(cn3, "realpy", "python.exe")
try:
    with open(os.path.join(cn3, "logs", "python_path.txt"), "w", encoding="gbk") as f:
        f.write(real)                       # 模拟 setup_python.ps1 的 -Encoding Default
    rc_old, out_old = run_script(OLD_STYLE, cn3, "\r\n")
    ok("反面：老写法（GBK 写 + chcp 65001 读）在中文路径下**读不出真路径**（所以必须去掉这一步）",
       "MARKER-OLD-RESOLVED" not in out_old, "rc=%s 输出末尾=%r" % (rc_old, out_old[-200:]))
except Exception as e:
    print("  · 跳过老写法对照（本机没有 GBK 编码器）：%s" % e)

# ── E 跨文件耦合（两条都是「改一处、坏另一处」）────────────────────────
print("\n[五] 跨文件耦合（.ps1 的 BOM · python_path.txt 的读写两侧）")

# ① 随包 `.ps1` 必须是 **UTF-8 带 BOM**：PowerShell 5.1 把无 BOM 的 UTF-8 当 ANSI 解，
#    中文注释里的字节会把语法读崩（2026-09-15 本会话实测：编辑工具改一次就掉了 BOM，
#    `setup_python.ps1` 当场 **5 处语法错误**、脚本根本跑不起来 —— 而它在 HEAD 里本来是好的）。
try:
    _r = subprocess.run(["git", "ls-files", "*.ps1"], capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=20)
    ps1 = [x.strip() for x in (_r.stdout or "").splitlines() if x.strip()]
except Exception:
    ps1 = []
if not ps1:
    import glob
    ps1 = [os.path.relpath(x, ROOT) for x in glob.glob(os.path.join(ROOT, "scripts", "*.ps1"))]
bad_bom = []
for rel in ps1:
    p2 = os.path.join(ROOT, rel)
    if os.path.exists(p2) and open(p2, "rb").read()[:3] != b"\xef\xbb\xbf":
        bad_bom.append(rel)
ok("随包 .ps1 都是 UTF-8 **带 BOM**（无 BOM ⇒ PS 5.1 按 ANSI 解、中文注释会把语法读崩）",
   not bad_bom, "缺 BOM：%s（共查 %d 个）" % (bad_bom, len(ps1)))

# ② `setup_python.ps1` 必须继续按 ANSI 写 python_path.txt —— 产物 `一键启动.exe` 是
#    `File.ReadAllText(pth, Encoding.GetEncoding(936))` 读它的（launcher.cs:265）⇒
#    谁把这里改成 UTF-8，中文路径下 exe 就会读到乱码并报「Python 环境异常」。
_ps1 = text(os.path.join("scripts", "setup_python.ps1"))
ok("setup_python.ps1 仍按 ANSI 写 python_path.txt（一键启动.exe 那一侧按 936 读它）",
   "Set-Content -Path $pathTxt -Value $cmd -Encoding Default" in _ps1)

# ③ `.cmd` 代码里不许再读那个 txt（读就有跨编码风险，见 P8），且要有 nopy 守卫
for nm in (CHECK, ONLY):
    _t = text(nm)
    _code = "\n".join(l for l in _t.split("\r\n") if not l.lstrip().lower().startswith("rem"))
    ok("%s：代码里**不读** python_path.txt（P8 的正解：谁都不依赖那个文件）" % nm,
       "python_path.txt" not in _code)
    ok("%s：有 nopy 守卫（PYCMD 不存在时给人话，而不是天书报错）" % nm,
       ":nopy" in _t and 'if not defined PYARG if not exist "%PYCMD%"' in _t)
    _bad_end = [i + 1 for i, l in enumerate(_t.split("\r\n")) if l and ord(l[-1]) > 127]
    ok("%s：每行都以 ASCII 字节结尾（防多字节字符紧贴 CR 的解析事故）" % nm,
       not _bad_end, "行：%s" % _bad_end)

print("\n%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
