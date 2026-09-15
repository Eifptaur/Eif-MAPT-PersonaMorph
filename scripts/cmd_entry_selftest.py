# -*- coding: utf-8 -*-
"""入口脚本（.cmd）判据（2026-09-15，跨机实测逼出来的）。

起因：跨机那台机器（Win10 22H2 · ACP=OEMCP=936）上，包里的两个 `.cmd` **双击一行都跑不动**。
对面做了 6 格对照实验（同一份内容，只改编码/行尾）：**LF 三种编码全断、CRLF 三种全通**
⇒ 是**打包缺陷**（UTF-8 无 BOM + 纯 LF），不是功能缺陷。根因：cmd.exe 解析不了 LF 行尾的
`if ... goto` / `for /f` / 括号块 —— 包本身看着完全正常。

这条判据守四件事（全部只读 + 临时目录，不联网、不动微信）：
  A. 随包的 `.cmd` 一律 **CRLF 且无 BOM**、UTF-8 可解码（防某天被编辑器/写盘工具改回 LF）。
  B. 两个入口各自该有的东西还在：`chcp 65001`（中文显示）· `cd /d "%~dp0"` ·
     `setup_python.ps1`（拿 Python）· `setup_deps.py`（装依赖 —— 跨机实测抓到的
     「只装 Python 不装依赖」缺口）· `collect_report.py`。
  C. 只读那一版**不许带** `--send-test` / `--allow-send`（防止哪天把"会发消息"混进只读入口）。
  D. **行为对照**：把 .cmd 里的动作行换成 `echo MARKER`、去掉 `pause`，造出同内容两份
     （LF / CRLF）放进临时目录真用 `cmd /c` 跑 ⇒ **CRLF 版必须走完 goto 链并打出 MARKER**。
     LF 版的结果只**记录**不断言（本机控制台代码页可能已是 65001，侥幸能过；跨机那台是 936）。

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
print("\n[二] 入口步骤（跨机实测抓到的两个缺口）")
for nm in (CHECK, ONLY):
    t = text(nm)
    ok("%s：会准备 Python（setup_python.ps1）" % nm, "setup_python.ps1" in t)
    ok("%s：**会装依赖**（setup_deps.py —— 原来只在 一键启动.onestart 里调）" % nm,
       "setup_deps.py" in t)
    ok("%s：会跑 collect_report.py" % nm, "collect_report.py" in t)
    ok("%s：goto 链完整（:deps 标签在）" % nm, re.search(r"^:deps\s*$", t, re.M) is not None)
    ok("%s：出错时不会一闪而过（有 pause）" % nm, "pause" in t)

t_check, t_only = text(CHECK), text(ONLY)
ok("只读版：**不带** --send-test", "--send-test" not in t_only)
ok("只读版：**不带** --allow-send", "--allow-send" not in t_only)
ok("检验版：带 --send-test（会真发一条给文件传输助手）", "--send-test" in t_check)
ok("检验版：带 --allow-send（解「版本门拦住发送实测」的鸡生蛋）", "--allow-send" in t_check)
ok("检验版：横幅里写明了会放行版本门（不许悄悄降级）",
   "版本门" in t_check and "放行" in t_check)

# ── C 行为对照（真跑 cmd）──────────────────────────────────────────────
print("\n[三] 行为对照：真用 cmd /c 跑一遍（动作行换成 echo，去掉 pause）")


def neuter(t):
    """把真正会干活的命令行换成 echo 标记，保留全部控制流（if/goto/for/括号块）。"""
    out = []
    for ln in t.split("\n"):
        s = ln.strip()
        low = s.lower()
        if s == "pause" or low == "pause":
            out.append("rem pause")
        elif "setup_python.ps1" in s:
            out.append("echo MARKER-PY")
        elif "setup_deps.py" in s:
            out.append("echo MARKER-DEPS")
        elif "collect_report.py" in s:
            out.append("echo MARKER-REPORT")
        else:
            out.append(ln)
    return "\n".join(out)


def run_variant(src_text, newline, tag):
    tmp = tempfile.mkdtemp(prefix="pm-cmdtest-")
    p = os.path.join(tmp, "t.cmd")
    data = src_text.replace("\r\n", "\n").replace("\n", newline)
    with open(p, "wb") as f:
        f.write(data.encode("utf-8"))
    try:
        r = subprocess.run(["cmd", "/c", p], cwd=tmp, capture_output=True,
                           timeout=30)
        out = (r.stdout or b"").decode("utf-8", "replace") + \
              (r.stderr or b"").decode("utf-8", "replace")
        return r.returncode, out, tmp
    except Exception as e:
        return -1, "跑不起来: %s" % e, tmp


base = neuter(text(CHECK)).replace("\r\n", "\n")

rc_ok, out_ok, _d1 = run_variant(base, "\r\n", "CRLF")
ok("CRLF 版：能走完 goto 链到 :deps（打出 MARKER-DEPS）", "MARKER-DEPS" in out_ok,
   "rc=%s 输出前 120 字=%r" % (rc_ok, out_ok[:120]))
ok("CRLF 版：能走到最后一步（打出 MARKER-REPORT）", "MARKER-REPORT" in out_ok,
   "rc=%s" % rc_ok)

rc_lf, out_lf, _d2 = run_variant(base, "\n", "LF")
# 不断言 LF 必失败：本机控制台代码页可能是 65001（跨机那台是 936）。只把事实记下来。
print("  · 参考（不断言）：LF 版 rc=%s · 打出 MARKER-REPORT=%s · 打出 MARKER-DEPS=%s"
      % (rc_lf, "MARKER-REPORT" in out_lf, "MARKER-DEPS" in out_lf))
if "MARKER-REPORT" not in out_lf:
    print("    ⇒ 本机也复现了「LF 版一行都跑不动」（与跨机那台一致）")
else:
    print("    ⇒ 本机 LF 版侥幸能过（控制台代码页已是 65001）；跨机那台 ACP=936，LF 必断")

print("\n%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
