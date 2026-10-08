# -*- coding: utf-8 -*-
"""N51-5 常驻闸门：**`launcher-src/README.md` 的编译命令必须列出全部用到的 .cs**（B 赛道 §G.1 N51-5）。

跑法： py -3 scripts\\launcher_build_files_selftest.py     退出码 0=全过 / 1=有失败

**为什么要有它**（真踩过）：
  `launcher-src/launcher.cs` 引用了**别的文件里**的类型 ——
  `wingliphs.cs` 的 `GlyphButton` / `GlyphKind`、`webview2guide.cs` 的 `WebView2Guide` / `WebView2MissingForm`
  —— 而 README 的第一条编译命令**只列了 `launcher.cs + stylekit.cs`**
  ⇒ **照文档编译直接 CS0246（找不到类型或命名空间）**。
  这类错"文档说能编、实际编不出"，只有真去编才发现 ⇒ **必须机器盯着**。

**它怎么判**（不靠人肉清单，直接从 README 与源码推）：
  ① 从 `README.md` 的代码块里**解析出每条编译命令的 .cs 列表**（按 `/out:` 分组）；
  ② 扫 `launcher-src/*.cs`，建"类型名 → 声明它的文件"表；
  ③ 对每条命令：**命令内的文件**若引用某类型，则该类型的**某个声明文件必须也在命令内**；
  ④ 反向控制：**故意从命令里删掉一个必需文件，必须被查出来**（防判据自己失效）。

⛔ 只读，不编译、不写盘（编译会产出 exe，属产品根留痕 ⇒ 交给 release 流程，不交给判据）。
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

import _iso14  # noqa: E402
_iso14.all_()  # ⛔ 必须 all_()

SRC = os.path.join(ROOT, "launcher-src")
README = os.path.join(SRC, "README.md")

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


# ── ① 从 README 解析每条编译命令的 .cs 列表 ────────────────────────────────
_md = io.open(README, encoding="utf-8", errors="replace").read()
_blocks = re.findall(r"```(?:powershell|bash|sh)?\n(.*?)```", _md, re.S)
_cmd_text = "\n".join(_blocks)

# 每条命令以 `/out:xxx.exe` 出现；把它到下一个 /out: 之间的 `launcher-src\X.cs` 归给它
_outs = list(re.finditer(r"/out:([^\s`]+\.exe)", _cmd_text))
CMDS = []           # [(exe名, [cs 文件名...])]
for i, m in enumerate(_outs):
    seg = _cmd_text[m.end(): _outs[i + 1].start() if i + 1 < len(_outs) else len(_cmd_text)]
    # 只取**本命令**的名字：遇换行后的注释/新命令即止（命令用行尾 ` 续行）
    cs = re.findall(r"launcher-src[\\/]([A-Za-z0-9_]+\.cs)", seg)
    CMDS.append((m.group(1), cs))

print("\n══ ① 从 README 解析出的编译命令 ══")
for _exe, _cs in CMDS:
    print("   %-16s ← %s" % (_exe, _cs))
ok("① README 里解析出至少 2 条编译命令（启动器 / 关闭器）", len(CMDS) >= 2, "%d 条" % len(CMDS))

# ── ② 类型名 → 声明文件 ───────────────────────────────────────────────────
_DECL_RE = re.compile(r"(?m)^\s*(?:public|internal|private|protected|static|sealed|abstract|partial|\s)*"
                      r"\b(?:class|struct|enum|interface)\s+([A-Za-z_][A-Za-z0-9_]*)")
_files = {}
for f in sorted(os.listdir(SRC)):
    if f.endswith(".cs"):
        _files[f] = io.open(os.path.join(SRC, f), encoding="utf-8", errors="replace").read()

_decl = {}      # 类型名 -> [声明它的文件...]
for f, txt in _files.items():
    for n in _DECL_RE.findall(txt):
        _decl.setdefault(n, []).append(f)

print("\n══ ② launcher-src 里声明的类型（前 12）══")
print("   %s" % sorted(_decl)[:12])


# ── ③ 覆盖率判定 ──────────────────────────────────────────────────────────
def _strip_comments(txt):
    """去掉 C# 注释，再判"用没用某个类型"。

    ⛔ 为什么必须去注释（本判据第一版就栽在这）：`stylekit.cs` 的 `///` 文档注释里**大量提到**
    `LauncherForm` / `BusyForm` / `DeadLinkForm` 等**别的文件里的类型名**（在讲"哪个窗体覆写了
    NoActivate"）。不去注释 ⇒ 全被当成"stylekit 用到了 LauncherForm" ⇒ **假红**。
    """
    txt = re.sub(r"/\*.*?\*/", " ", txt, flags=re.S)
    txt = re.sub(r"(?m)//.*$", " ", txt)
    return txt


def _uses(txt, type_name):
    """这个类型被**当作类型**用了吗？

    两条必须排除的形态（本判据先后栽在这两处，都是假红）：
      · **成员访问**：`close.cs` 写的是 `StyleKit.Ui(...)` —— `Ui` 是 StyleKit 的**方法**，
        不是 `launcher.cs` 里那个类型 `Ui`；
      · **自己的方法调用**：`stylekit.cs` 内部调自己 `Ui(size, fs)`（无点号前缀）——
        跟类型一点关系都没有。
    ⇒ 只认**类型上下文**：`new T` / `as T` / `is T` / `T.成员` / `T 变量名 =` / `: T`（继承）。
    """
    t = re.escape(type_name)
    pats = (
        r"(?<![\w.])new\s+%s\b" % t,
        r"(?<![\w.])as\s+%s\b" % t,
        r"(?<![\w.])is\s+%s\b" % t,
        r"(?<![\w.])%s\s*\." % t,
        r"(?<![\w.])%s\s+[A-Za-z_]\w*\s*[=;,)\(]" % t,
        r":\s*(?:[A-Za-z_][\w.]*\s*,\s*)*%s\b" % t,
    )
    return any(re.search(p, txt) for p in pats)


def missing_in(cs_list):
    """命令内文件引用了、但其声明文件**一个都不在命令内**的类型 → [(类型, 使用它的文件, 声明文件)]。"""
    in_cmd = set(cs_list)
    bad = []
    for f in cs_list:
        txt = _strip_comments(_files.get(f, ""))
        for t, decls in _decl.items():
            if any(d in in_cmd for d in decls):
                continue            # 该类型有声明文件在命令里 ⇒ 解析得到，OK
            if _uses(txt, t):
                bad.append((t, f, tuple(decls)))
    return bad


print("\n══ ③ 每条命令的类型覆盖 ══")
_results = {}
for _exe, _cs in CMDS:
    _bad = missing_in(_cs)
    _results[_exe] = _bad
    if _bad:
        print("   ✘ %s 缺文件：%s" % (_exe, _bad))
    else:
        print("   ✅ %s 覆盖完整" % _exe)

for _exe, _cs in CMDS:
    ok("③ 「%s」的编译命令覆盖它用到的全部自有类型" % _exe, not _results[_exe],
       ("缺：%s" % _results[_exe]) if _results[_exe] else "文件 %d 个" % len(_cs))

# ── ④ 两条具体断言（把这次的真缺陷钉死）────────────────────────────────────
# ⛔ 启动器产物的**主判据是"不是关闭器"**，不许拿产物名去匹配「启动」二字：
#   产物已改名 `群相.exe`（不再含"启动"），按名字匹配会让 `_launcher` 变空、
#   ④/④b/④c/⑤/⑤b 集体假红（改名漏改的重演）。关闭器仍叫 `一键关闭.exe`（含"关闭"）。
print("\n══ ④ 钉死本次真缺陷 ══")
_launcher = next((cs for exe, cs in CMDS if "关闭" not in exe), [])
_close = next((cs for exe, cs in CMDS if "关闭" in exe), [])
ok("④a 分母守卫：真区分出启动器命令与关闭器命令（否则 ④ 一族测的是空气）",
   bool(_launcher) and bool(_close) and _launcher != _close,
   "启动器 %d 个文件 / 关闭器 %d 个文件" % (len(_launcher), len(_close)))
ok("④ 启动器命令**带上** `wingliphs.cs`（GlyphButton 的定义处）", "wingliphs.cs" in _launcher, str(_launcher))
ok("④b 启动器命令**带上** `webview2guide.cs`（WebView2Guide 的定义处）",
   "webview2guide.cs" in _launcher, str(_launcher))
ok("④c 启动器命令**带上** `stylekit.cs`", "stylekit.cs" in _launcher, str(_launcher))
ok("④d 关闭器命令**不带**这两个（`close.cs` 对它们的引用数是 0）",
   "wingliphs.cs" not in _close and "webview2guide.cs" not in _close, str(_close))
for _f, _t in (("wingliphs.cs", "GlyphButton"), ("webview2guide.cs", "WebView2Guide")):
    _n = len(re.findall(r"\b%s\b" % _t, _files.get("launcher.cs", "")))
    ok("④e `launcher.cs` 确实用到 `%s`（%d 处）—— 所以漏了它必然 CS0246" % (_t, _n), _n > 0)
_n_close = len(re.findall(r"\bGlyphButton\b", _files.get("close.cs", "")))
ok("④f `close.cs` 对 `GlyphButton` 的引用数 = 0（两条命令不可照抄）", _n_close == 0, "%d 处" % _n_close)

# ── ⑤ 反向控制：故意删掉必需文件，必须被查出来 ─────────────────────────────
print("\n══ ⑤ 反向控制（判据自身不能被假绿骗过） ══")
_sim = [c for c in _launcher if c != "wingliphs.cs"]
ok("⑤ 从启动器命令里删掉 `wingliphs.cs` ⇒ 判据**必须**报缺",
   bool(missing_in(_sim)), "模拟缺文件时检出 %s" % (missing_in(_sim) or "空（判据失效！）"))
_sim2 = [c for c in _launcher if c != "webview2guide.cs"]
ok("⑤b 删掉 `webview2guide.cs` ⇒ 判据**必须**报缺", bool(missing_in(_sim2)))
ok("⑤c 完整命令**不该**误报（正向：0 缺）", not missing_in(_launcher))

# ── ⑥ README 里那句"自查办法"必须还在（防有人删了文档又不改命令）────────────
ok("⑥ README 里带「怎么一眼自查该带哪几个 .cs」的指引",
   "该带哪几个" in _md or "sweep" in _md or "sort -u" in _md)

# ── ⑦ ⭐ 改名漂移闸门：**打包脚本的必需文件清单**必须与**编译产物名**一致 ────────
#   真踩过：启动器改名 `一键启动.exe` → `群相.exe` 时，
#   `scripts/pack_online.py` 的 `need` 清单**漏改** ⇒ 打包以"缺必需文件：['一键启动.exe']"
#   拒绝出包（exit 4），而且**只有真去打包才发现**。
#   ⛔ 这条判据把"产物名"当唯一真值：`build.ps1` 的 `/out:` 产出什么，清单里就得有什么。
print("\n══ ⑦ 打包必需文件清单 vs 编译产物名（改名漂移闸门） ══")
_bp = io.open(os.path.join(ROOT, "build.ps1"), encoding="utf-8", errors="replace").read()
# `/out:群相.exe` → 产物名（build.ps1 是编译产物的唯一真值来源）
_built = re.findall(r"/out:([^\s\"']+\.exe)", _bp)
ok("⑦a 分母守卫：从 `build.ps1` 真解析出了产物名（否则下面测的是空气）",
   len(_built) >= 2, "产物 %s" % _built)
_pk = io.open(os.path.join(ROOT, "scripts", "pack_online.py"), encoding="utf-8", errors="replace").read()
_i_need = _pk.find("need = [")
_j_need = _pk.find("]", _i_need)
_need_block_raw = _pk[_i_need:_j_need] if _i_need > 0 else ""
# ⛔ 判据要**去注释**：说明性注释里会提到历史名（本项目已踩过：注释里提名字被误判成"还在用"）
_need_block = "\n".join(
    _ln for _ln in _need_block_raw.split("\n") if not _ln.strip().startswith("#"))
ok("⑦b 分母守卫：真读到了 `pack_online.py` 的 need 清单",
   _i_need > 0 and len(_need_block) > 80, "%d 字符" % len(_need_block))
# 启动器（`launcher.cs` 那条命令产出的那个）必须在清单里
_launcher_exe = ""
for _m in re.finditer(r'csc[^\n]*/out:([^\s"\']+\.exe)[^\n]*launcher\.cs', _bp):
    _launcher_exe = _m.group(1)
if not _launcher_exe and _built:
    _launcher_exe = _built[0]
ok("⑦c ★启动器产物名（现＝`%s`）必须在打包必需清单里" % _launcher_exe,
   ('"%s"' % _launcher_exe) in _need_block,
   "清单里 %s" % ("有" if ('"%s"' % _launcher_exe) in _need_block else "没有！"))
ok("⑦d ★反例锚：历史名 `一键启动.exe` **不许**再出现在必需清单里（改名后它就是错的）",
   "一键启动.exe" not in _need_block,
   "违规则说明改名又漏改了")
# 反向锚：人为把清单改回旧名 ⇒ ⑦c 的检查式必须变红
_fake_need = _need_block.replace('"%s"' % _launcher_exe, '"一键启动.exe"')
ok("⑦e ★反向锚：把清单改回旧名后，⑦c 那条守卫的**判据式**会变红",
   ('"%s"' % _launcher_exe) not in _fake_need, "反向锚有效")

print("\n==== launcher 编译命令覆盖判据：%d 通过 / %d 失败 ====" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for _f in FAIL:
        print("  · " + _f)
sys.exit(1 if FAIL else 0)
