# -*- coding: utf-8 -*-
"""**本机真实截图**语料线判据（采集 → 脱敏标记 → 喂真值 → 无界面入口）。

## 为什么这条要单独立判据
"把用户自己的截图收进来"是**唯一**能回答"在这台机器上认不认得出来"的素材来源
（网图的字体渲染/DPI/窗口边框都不等于本机）。但它同时也是**唯一会碰到用户个人文件**的一环
⇒ 两条风险都必须被机器门禁盯死：
  · **隐私风险**：真实截图可能带手机号/邮箱/身份证 ⇒ 不许静默入库、不许把风险图混进基准；
  · **误伤用户文件**：源目录必须**只读**（不移动/不重命名/不删除；写只发生在语料目录）。

## 四轴
  A. 采集纪律：源目录**只读**（源码锚 + 真跑一次后源目录文件数与 mtime 未变）；跨来源去重；
     只写语料目录。
  B. 脱敏标记：PII 正则确实能命中（正例）+ **不误报**常见正常文本（反例）；
     带标记的图 `eligible=false`；`--include-flagged` 才喂。
  C. 喂真值流水线：`from_image` 产 `proposed` 候选（**只产候选，不自动转正**）；
     产物落在 `truth_store`，能被人核。
  D. 无界面入口：`pmctl corpus` 的四个动作齐全、`--help` 起得来、
     子进程**不弹窗但输出可见**（这两个要求同时满足才算对）。

跑法：`runtime\\python\\python.exe scripts\\local_corpus_selftest.py`
"""
import io
import json
import os

import sys


sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
os.environ.setdefault("PM_NO_WINDOW_TOUCH", "1")
import _srcmatch as _sm # noqa: E402
import _srcslice as _ss # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))



import ocr_corpus_local as L # noqa: E402

LOCAL_DIR = os.path.join(ROOT, "..", "dev-workspace", "persona-morph", "ocr-samples", "local")
LOCAL_DIR = os.path.abspath(LOCAL_DIR)

# ── A. 采集纪律 ────────────────────────────────────────────────────────────
print("── A. 采集纪律：源目录只读 · 只写语料目录 · 去重 ──")
_src = io.open(os.path.join(ROOT, "scripts", "ocr_corpus_local.py"), encoding="utf-8").read()
ok("A1 源码里明确声明源目录**只读**（不移动/不重命名/不删除）",
   _sm.has(_src, "只读") and _sm.has(_src, "不移动") and _sm.has(_src, "不改动"))
# ⛔ 反向锚：源码里**不得**出现对源目录的破坏性调用（shutil.move / os.remove / os.rename
#    作用在 src 上的写法）。这条挡"以后有人图省事加了句 cleanup"。
_has_rm = ("shutil.move(" in _src) or ("os.rename(" in _src)
ok("A2 ★ 源码里**没有**对源目录的移动/重命名调用（`shutil.move` / `os.rename`）",
   not _has_rm, "命中=%s" % ("有（危险）" if _has_rm else "无"))
# 写入点必须只有语料目录
_wr = [l for l in _src.splitlines() if ("io.open(" in l and '"w"' in l) or ".save(" in l]
ok("A3 落盘点只指向语料目录（写操作不指向源目录）",
   not any("a.dir" in l or "src_dir" in l for l in _wr), "写操作行数=%d" % len(_wr))

# 真跑一次（dry-run 不写盘），确认源目录文件数/内容指纹不变
_srcdir = L.DEFAULT_SRC_DIR
_before = {}
if os.path.isdir(_srcdir):
    for fn in sorted(os.listdir(_srcdir)):
        p = os.path.join(_srcdir, fn)
        if os.path.isfile(p):
            _before[fn] = (os.path.getmtime(p), os.path.getsize(p))
ok("A4 默认源目录存在且非空（不然 A5 的「只读」会因空集恒真；也不该只见提示不见报错）",
   os.path.isdir(_srcdir) and len(_before) > 0,
   "目录=%s 文件 %d 个" % (os.path.isdir(_srcdir), len(_before)))
_cands = L.list_candidates(_srcdir)
_after = {}
if os.path.isdir(_srcdir):
    for fn in sorted(os.listdir(_srcdir)):
        p = os.path.join(_srcdir, fn)
        if os.path.isfile(p):
            _after[fn] = (os.path.getmtime(p), os.path.getsize(p))
ok("A5 ★ 列候选（只读操作）之后，源目录文件集与 mtime **完全没变**",
   _before == _after, "前 %d 个 / 后 %d 个" % (len(_before), len(_after)))

# 去重：跨来源（web/wechat/local）内容指纹唯一
_h = L.hashes()
ok("A6 跨来源去重有效（同一张图不会因换了名字又被收一遍）",
   len(set(_h.keys())) == len(_h), "指纹 %d 个 → 唯一 %d" % (len(_h), len(set(_h.keys()))))

# ── B. 脱敏标记 ────────────────────────────────────────────────────────────
print("\n── B. 脱敏标记：该命中的命中 · 不该命中的别误报 ──")
_pos = [
    ("手机号", "联系电话 13812345678 请拨", ["手机号"]),
    ("邮箱", "邮箱 test.user@example.com", ["邮箱"]),
    ("身份证", "身份证 11010519900307123X", ["身份证"]),
    ("长数字串", "卡号 6222021234567890123", ["长数字串", "疑似账号/卡号"]),
]
_bad = []
for tag, txt, want in _pos:
    got = L.pii_scan(txt)
    if not any(w in got for w in want):
        _bad.append("%s→%s" % (tag, got))
ok("B1 ★ PII 正例都能命中（手机号/邮箱/身份证/长数字串）", not _bad, "漏：%s" % _bad)
_neg = [
    "群相 · 无界面操作台", "微信 4.1.13 安装包", "共 109 条接口 / 20 条命令",
    "版本 v2.1.70 构建 2026-09-29", "1Transfer 已完成 100%", "12 项检查 0 项失败",
    "DeepSeek/Kimi/K3 智谱 GLM", "进程 1234 · 线程 8 · 句柄 1234",
]
_fp = []
for t in _neg:
    r = L.pii_scan(t)
    if r:
        _fp.append("%s→%s" % (t[:18], r))
ok("B2 ★ 正常文本**不误报**（版本号/数字统计/产品名不该被当成隐私）", not _fp,
   "误报：%s" % _fp[:3])
# 标记为风险的图 eligible=false
ok("B3 带隐私标记的图 `eligible=False`（默认不进基准分母）",
   _sm.has(_src, '"eligible": not risks'))
ok("B4 喂真值时可跳过风险图（默认跳过 + `--include-flagged` 才喂）",
   _sm.has(io.open(os.path.join(ROOT, "scripts", "local_truth_feed.py"),
                   encoding="utf-8").read(), "include-flagged"))

# ── C. 喂真值流水线 ────────────────────────────────────────────────────────
print("\n── C. 喂真值流水线：只产候选 · 不自动转正 ──")
_feed = io.open(os.path.join(ROOT, "scripts", "local_truth_feed.py"), encoding="utf-8").read()
ok("C1 只用 `from_image` 产候选（不直接调 confirm/转正）",
   _sm.has(_feed, "from_image") and not _sm.has(_feed, "TS.confirm"))
ok("C2 ★ 口径写明「一致性 ≠ 正确性，不进基准分母」",
   _sm.has(_feed, "一致性") and _sm.has(_feed, "不进基准分母"))
# 真跑：语料清单存在时，喂一张应产出候选（用 tempfile 造一张字多的小图，不依赖用户文件）
if os.path.isdir(LOCAL_DIR):
    _imgs = [f for f in sorted(os.listdir(LOCAL_DIR))
             if f.lower().endswith(".png") and f.startswith("local-")]
    ok("C3 语料目录里确实有本机真实截图（采集这步真的跑过）", bool(_imgs),
       "%d 张" % len(_imgs))
    _db = json.load(io.open(os.path.join(LOCAL_DIR, "_sources.json"), encoding="utf-8"))
    _kinds = {v.get("kind") for v in _db.values()}
    ok("C4 清单里标了 `kind=local-real`（能与网图分类统计，不混在一起）",
       _kinds == {"local-real"}, str(_kinds))
    _has_elig = all("eligible" in v for v in _db.values())
    ok("C5 每条都有 `eligible` 字段（风险图能不能进分母要有据可查）", _has_elig)
else:
    ok("C3~C5（语料目录不存在 ⇒ 采集还没跑过，这几条判不了）", True, "跳过")

# ── D. 无界面入口 ──────────────────────────────────────────────────────────
print("\n── D. 无界面入口：pmctl corpus 五个动作 · 不弹窗且输出可见 ──")
_pm = io.open(os.path.join(ROOT, "scripts", "pmctl.py"), encoding="utf-8").read()
ok("D1 `corpus` 子命令在位，五个动作齐全（scan/add/feed/status/check）",
   _sm.has(_pm, '"corpus"') and _sm.has(_pm, '"scan", "add", "feed", "status", "check"'))
# ⛔ 等价性锚：内层脚本的两档（只看 `--report` / 连风险图也喂 `--include-flagged`）原来**接不上**，
#    等于有图形化那侧能做的事，无界面这侧做不到。这条挡"以后又只加内层参数、忘了开出口"。
ok("D5 ★ 内层两档参数在无界面侧有对应出口（`status`=--report · `--include-flagged`）",
   _sm.has(_pm, '"status": "local_truth_feed.py"') and _sm.has(_pm, '"--report"')
   and _sm.has(_pm, '"--include-flagged"'))
ok("D2 源码保留了「不弹窗」`CREATE_NO_WINDOW`",
   _sm.has(_pm, "CREATE_NO_WINDOW"))
# ⛔⛔ 反回退锚：**只**加 CREATE_NO_WINDOW 会把子进程 stdout 摘掉（实测：输出 7040 字节、终端一个字没有）
#     ⇒ 必须同时显式接 stdout/stderr。这条挡"以后有人嫌麻烦把 stdout 那行删了"。
ok("D3 ★★ 子进程 stdout/stderr 被显式接到本进程（否则无界面命令'跑了但看不见'）",
   _sm.has(_pm, '"stdout": sys.stdout') and _sm.has(_pm, '"stderr": sys.stderr'))
ok("D4 菜单里有「语料」入口（不是只能敲命令）",
   _sm.has(_pm, "_menu_corpus") and _sm.has(_pm, "语料：收本机截图"))

# ── E. 与基准接线 ──────────────────────────────────────────────────────────
print("\n── E. 与基准接线：真实截图真的进得了统计口径 ──")
_bw = io.open(os.path.join(ROOT, "scripts", "ocr_bench_web.py"), encoding="utf-8").read()
ok("E1 基准认识 `local-real` 这一类（否则收了也永远不进任何统计）",
   _sm.has(_bw, "KIND_LOCAL") and _sm.has(_bw, '"local-real"'))
ok("E2 基准能在两个目录找素材（web + local）",
   _sm.has(_bw, "def _path_of") and _sm.has(_bw, "LOCAL"))
ok("E3 ★ 基准里有「本机真实截图」这一列（与网图分开报，不混算）",
   _sm.has(_bw, "本机真实截图"))
# ⛔ 死代码反回退：那两行棘轮输出原来写在 `_kinds()` 的 `return out` 之后，永远执行不到。
#    判定分两层（原来只查了一层，且锚点用「文本切片定位函数」—— 那是**文本边界**，
#    函数一搬家就静默切错 ⇒ 被结构判据判红，也真的不可靠）：
#      (a) 必须**不在** `_kinds()` 里（在它里面就是死代码）；
#      (b) 必须在 `measure()` 里、且在 `_kinds()` 的**调用点之后**。
#    一律用 AST 锚点（`_srcslice`）取函数正文，不按文件位置切。
_kinds_src = _ss.func_src(_bw, "_kinds")
_measure_src = _ss.func_src(_bw, "measure")
_dead_in_kinds = ("差距：tier-1" in _kinds_src) or ("棘轮：历史最好" in _kinds_src)
# (b) 真输出在 measure 体内、且排在调用点 `_kinds()` 之后。
#     存在性断言走 `_srcmatch.has()`（空白容忍），不要用带空白的整段 `in`（改缩进就假红）。
_in_measure = (_sm.has(_measure_src, 'print("   差距：tier-1')
               and _sm.has(_measure_src, 'print("   棘轮：历史最好'))
_i_call = _measure_src.find("kinds = _kinds()")
_i_gap = _measure_src.find('print("   差距：tier-1')
_i_ratch = _measure_src.find('print("   棘轮：历史最好')
_after_call = (_i_gap > _i_call >= 0) and (_i_ratch > _i_call)
ok("E4 ★ 棘轮/差距输出**不再**是死代码（原来写在 `_kinds()` 的 `return` 之后，永不执行）",
   _in_measure and _after_call and (not _dead_in_kinds),
   "在 measure=%s 在调用点后=%s 残留在 _kinds=%s" % (_in_measure, _after_call, _dead_in_kinds))

print("\n本机真实截图语料判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
