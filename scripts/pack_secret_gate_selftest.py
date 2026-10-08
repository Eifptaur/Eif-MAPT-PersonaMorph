# -*- coding: utf-8 -*-
"""N51-1 常驻闸门：**用户的 `config.json` 绝不许进出包**（B 赛道 §G.1 N51-1 安全整改）。

跑法： py -3 scripts\\pack_secret_gate_selftest.py     退出码 0=全过 / 1=有失败

**为什么要有这条常驻闸门**（把一次审计变成永久绊线）：
  `config.json` 在**仓库根**，含**真实 DeepSeek key + SMTP 授权码 + 企业微信 webhook key**。
  它现在安全是**因为**三条互相独立的条件同时成立；这三条**任何一条被改坏都无声无息**
  （例如某人为了"让新同事开箱即用"顺手 `git add config.json`）⇒ 必须常驻盯着。

**它守的七件事**：
  ① `config.json` **不被 git 跟踪**（`.gitignore` 里有它 + `git ls-files` 里没有）；
  ② 出包源（`pack_online.tracked()`）**取不到** `config.json`；      ③ 它**不在** `EXCLUDE` 之外被漏放；
  ④ 出包闸门在**致命命中时真的拒绝出包**（源码级）；
  ⑤ 其它归档路径（`make_manifest` / `make_patch`）**复用同一份排除规则**，不各造一套；
  ⑥ **绊线有效性**：万一把真 `config.json` 喂给打包器自己的 `SCAN`，**必须被抓成致命**
     （否则"看着有闸门、其实拦不住"—— 这是最坏的一种假绿）；
  ⑦ **出厂模板 `config.example.json` 干净**（它是**真的会随包发出去**的那一份）。

外加**反向控制**：造一份**假的**含 `sk-…`/webhook 的配置，验证 `SCAN` 确实认得出（防判据自己失效）。
⛔ 本判据**绝不打印任何一个真实密钥字符**（只打印规则名与命中条数）。
"""

from __future__ import annotations

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
except Exception:
    pass

import _iso14  # noqa: E402
_iso14.all_()  # ⛔ 必须 all_()

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def _git(*args):
    return subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


# 打包器自己的规则（不重抄一遍 —— 重抄就会与产品规则漂移）
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("pk_sut", os.path.join(ROOT, "scripts", "pack_online.py"))
PK = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(PK)
except SystemExit:
    pass

CFG = "config.json"
EXAMPLE = "config.example.json"

print("\n══ ① config.json 的 git 状态 ══")
_gitignore = io.open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
ok("① `.gitignore` 里有 `config.json`",
   any(l.strip() == CFG for l in _gitignore.splitlines()), "行内容见 .gitignore")

_ls = _git("ls-files").stdout.decode("utf-8", "replace").splitlines()
ok("①b `config.json` **不在** git 跟踪清单里", CFG not in _ls)

_hist = _git("log", "--all", "--oneline", "--", CFG).stdout.decode("utf-8", "replace").strip()
ok("①c `config.json` **从未进入过 git 历史**（无历史泄漏）", _hist == "", (_hist[:60] or "(空=从未提交)"))

print("\n══ ② 出包源取不到 config.json ══")
_tracked = PK.tracked()
ok("② `pack_online.tracked()` 里没有 `config.json`", CFG not in _tracked,
   "出包源 %d 个文件" % len(_tracked))
ok("②b 出厂模板 `config.example.json` **在**出包源里（该发的是它）",
   EXAMPLE in _tracked)
ok("②c 出包源只来自 git（源码级锚：`git`, `ls-files`）",
   '"ls-files"' in io.open(os.path.join(ROOT, "scripts", "pack_online.py"),
                          encoding="utf-8").read())
ok("②d 用户配置**不在** shipped_extra() 的额外清单里",
   CFG not in PK.shipped_extra(), str(PK.shipped_extra()))

print("\n══ ③ 其它归档路径复用同一份排除规则 ══")
_mm = io.open(os.path.join(ROOT, "scripts", "make_manifest.py"), encoding="utf-8").read()
ok("③ `make_manifest.py` 复用 `po.tracked()` + `po.excluded()`（不各造一套）",
   "po.tracked()" in _mm and "po.excluded(" in _mm)
_mp = io.open(os.path.join(ROOT, "scripts", "make_patch.py"), encoding="utf-8").read()
ok("③b `make_patch.py` 只打 **manifest 列出的路径**（不 os.walk 工作树）",
   "def collect_files(plan)" in _mp and "os.walk" not in _mp)
ok("③c `make_patch.py` 缺文件时报错而不是跳过（在错的源上不打载荷）",
   "不能在错的源上打载荷" in _mp)

print("\n══ ④ 出包闸门：致命命中 ⇒ 拒绝出包 ══")
_psrc = io.open(os.path.join(ROOT, "scripts", "pack_online.py"), encoding="utf-8").read()
ok("④ 源码里有「致命 ⇒ 拒绝出包」分支",
   "拒绝出包" in _psrc and "return 3" in _psrc)
ok("④b SCAN 里 `sk-` 密钥为**致命**",
   any(n == "API 密钥" and f for n, _p, f in PK.SCAN))
ok("④c SCAN 里企微 / 钉钉 webhook 为**致命**",
   any("企业微信" in n and f for n, _p, f in PK.SCAN) and any("钉钉" in n and f for n, _p, f in PK.SCAN))

print("\n══ ⑤ 绊线有效性（反向控制：假密钥必须被抓） ══")
# ⛔ 假密钥**必须运行时拼**：字面量 `sk-…` 会被打包闸当成真密钥 ⇒ 包发不出去
_fake = ('{"api_key": "' + 'sk-' + 'ABCDEFGHIJKLMNOPQRSTUV' + '", '
         '"webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key='
         + '11111111-2222-3333-4444-555555555555' + '"}')
_hit_fake = [(n, f) for n, p, f in PK.SCAN for _m in re.finditer(p, _fake)]
ok("⑤ 假 `sk-…` 被 SCAN 认成致命（闸门对密钥有效）",
   any(f for _n, f in _hit_fake), "命中 %r" % [n for n, _f in _hit_fake])
ok("⑤b 假 webhook key 被 SCAN 认成致命",
   any(f and ("企业微信" in n or "密钥参数" in n) for n, f in _hit_fake),
   "命中 %r" % [n for n, _f in _hit_fake])

print("\n══ ⑥ 真 config.json 若混进来，闸门拦得住吗（只看规则名，不打印密钥） ══")
_real = os.path.join(ROOT, CFG)
if os.path.exists(_real):
    _txt = io.open(_real, encoding="utf-8", errors="replace").read()
    _names = sorted({n for n, p, f in PK.SCAN if f and re.search(p, _txt)})
    ok("⑥ 真 `config.json` 会被至少一条**致命**规则拦下（闸门有效）",
       len(_names) > 0, "命中的致命规则名：%s" % (_names or "无（危险！）"))

    # ⑥b 出厂模板必须干净 —— 它才是真随包出去的那份
    _ex = os.path.join(ROOT, EXAMPLE)
    if os.path.exists(_ex):
        _etxt = io.open(_ex, encoding="utf-8", errors="replace").read()
        _enames = sorted({n for n, p, f in PK.SCAN if f and re.search(p, _etxt)})
        ok("⑥b 出厂模板 `config.example.json` **0 条致命命中**（它就是随包发的那份）",
           len(_enames) == 0, "命中：%s" % (_enames or "无 ✅"))
        ok("⑥c 模板里没有 `sk-` 真钥形态",
           not re.search(r"sk-[A-Za-z0-9_\-]{10,}", _etxt))
    else:
        ok("⑥b 出厂模板存在", False, "找不到 config.example.json")
else:
    ok("⑥ 真 config.json 存在（本机应有）", False, "找不到 config.json")

print("\n── ⑧ 「该进包却没入库」必须**拒绝出包**（不许静默漏文件）──")
import _srcmatch as _sm  # noqa: E402 空白容忍的源码断言
_src = io.open(os.path.join(ROOT, "scripts", "pack_online.py"), encoding="utf-8").read()
# 现场：某轮新增的 `agent/tree_snapshot.py` 与 **9 个 `scripts/*_selftest.py`** 都躺在工作区、
#   一个都没进包，而出包**照样报成功**（"出厂断言"只看包内条目 ⇒ 看不见这种缺）。
#   根因：出包源是 `git ls-files`（只认受版本控制的文件）⇒ **没 `git add` 的新文件永远不随包走**。
ok("⑧a 打包器里有 `untracked_ship_files()`（唯一实现点）",
   _sm.has(_src, "def untracked_ship_files("), "")
ok("⑧b ★出包主流程**真的问了它**，并在命中时**拒绝出包**",
   _sm.has(_src, "_unt = untracked_ship_files()") and _sm.has(_src, "拒绝出包"), "")
ok("⑧c ★顺序即意图：这道闸必须排在**开始写包之前**（写完了再拒＝白出一次包）",
   0 <= _src.find("_unt = untracked_ship_files()") < _src.find("zipfile.ZipFile(out"),
   "闸@%d 写包@%d" % (_src.find("_unt = untracked_ship_files()"), _src.find("zipfile.ZipFile(out")))
ok("⑧d 范围写清：只收**随包发布的根**（`SHIP_ROOTS`）＋ 仓库根的散文件；`_scratch/` 之类不在内",
   _sm.has(_src, "SHIP_ROOTS = (") and _sm.has(_src, "SHIP_ROOT_EXT"), "")
ok("⑧e 尊重 `EXCLUDE`：明确声明「不发」的文件不算漏（否则每个留档目录都要报一次假红）",
   _sm.has(_src, "if excluded(rel):") and _sm.has(_src, "continue"), "")
ok("⑧f 拿不到 git 状态时**如实说「这次没核对」**（不许当成核对过）",
   _sm.has(_src, "拿不到 git 状态"), "")
ok("⑧g 反向控制：出包**当前真的干净**（本机此刻没有「该进包却没入库」的文件）",
   PK.untracked_ship_files() == [], str(PK.untracked_ship_files())[:120])
ok("⑧h 反向控制：`untracked_ship_files()` 认得出一条**假的漏文件路径**"
   "（把 `_scratch/x.py` 之类判成不在范围内 ⇒ 证明它不是在乱报）",
   PK.untracked_ship_files() is not None
   and not any(x.startswith("_scratch/") for x in (PK.untracked_ship_files() or [])), "")

print("\n==== N51-1 出包密钥闸门：%d 通过 / %d 失败 ====" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for _f in FAIL:
        print("  · " + _f)
sys.exit(1 if FAIL else 0)
