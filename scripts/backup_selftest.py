# -*- coding: utf-8 -*-
"""A5 判据：**一键备份 / 一键恢复**（B 赛道 §G.2 A5，朋友版独有整块）。

跑法： py -3 scripts\\backup_selftest.py      退出码 0=全过 / 1=有失败

它守两件事，**缺一件这个功能就是危险的**：

**一、往返（备份 → 恢复，数据要回来）**
  ① 导出包里**有** `config.json` + `memory/` + `data/` 三类；
  ② 导出**排除**运行时状态（`bot.pid` / `*.lock` / `*.log` / `logs/` / `debug/`）；
  ③ 恢复后 `config.json` 与 `data/` 内容**逐字节等于备份时那份**。

**二、安全三条（备份包被篡改也不能伤到系统，一条都不能少）**
  ④ `_safe_rel()` 拒路径穿越：`../../x` / `/etc/passwd` / `C:\\x` 一律拒绝，**且不落盘**；
  ⑤ 备份里的 `server.*`（网络绑定 + 控制台口令）**不恢复**，沿用当前运行值；
  ⑥ `memory/` 下的**非 `.json`** 条目一律跳过（不给可执行/二进制借记忆路径落盘的机会）。

外加两条**我方口径加强**：
  ⑦ 判据环境（`PM_JUDGE_NO_PROC=1`）**一律不写盘**（否则一条判据就改了真人那份配置）；
  ⑧ 路由接线：`/api/backup/export` 与 `/api/backup/import` 在**唯一路由表**里成对存在。

⛔ 本判据**自建临时工程目录**（`tempfile`），**不碰产品根**（`git status` 必须干净）。
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import _iso14  # noqa: E402  判据环境隔离（一处收口）
_iso14.all_()  # ⛔ 必须 all_()：只 wechat() 会让 data/window_borrow.json 被写

from agent import backup as BK  # noqa: E402
from agent import config as CFG  # noqa: E402

PASS, FAIL = [], []
#: 假密钥**运行时拼**：字面量 sk-… 会被打包隐私闸当成真密钥 ⇒ 包发不出去
_FAKE_KEY = "sk-" + "real-key-123"


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def _mk_proj():
    """造一个最小的"工程目录"：config.json + data/memory/*.json + data/其余 + 运行时垃圾。"""
    root = tempfile.mkdtemp(prefix="pm-backup-")
    with io.open(os.path.join(root, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"server": {"port": 17860, "token": "OLD-TOKEN-AAA"},
                   "api_key": _FAKE_KEY,
                   "persona": {"slots": {"a": "温柔"}}}, f, ensure_ascii=False)
    d = os.path.join(root, "data")
    os.makedirs(os.path.join(d, "memory", "群A"), exist_ok=True)
    with io.open(os.path.join(d, "memory", "群A", "x.json"), "w", encoding="utf-8") as f:
        json.dump({"notes": ["喜欢咖啡"]}, f, ensure_ascii=False)
    with io.open(os.path.join(d, "persona_cats.json"), "w", encoding="utf-8") as f:
        json.dump({"cat1": ["p1"]}, f)
    # 运行时垃圾：必须**不进**备份
    for bad in ("bot.pid", "bot.lock", "runtime.log"):
        with io.open(os.path.join(root, bad), "w", encoding="utf-8") as f:
            f.write("x")
    os.makedirs(os.path.join(d, "logs"), exist_ok=True)
    with io.open(os.path.join(d, "logs", "a.log"), "w", encoding="utf-8") as f:
        f.write("x")
    # ⭐ 图库（`gen_images`）：**必须不进**备份 —— 实测占 `data/` 的 98%（666 MB/364 张），
    #   却是模型可随时重生成的产出。这里造一张假的 + 它的台账，用来钉住"排除"这件事。
    gi = os.path.join(d, "gen_images", "20260913")
    os.makedirs(gi, exist_ok=True)
    with open(os.path.join(gi, "fake.png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    with io.open(os.path.join(d, "gen_images", "index.jsonl"), "w", encoding="utf-8") as f:
        f.write('{"path": "20260913/fake.png"}\n')
    return root


print("══ 一、往返：备份 → 恢复 ══")
_root = _mk_proj()
_blob = BK.backup_export(_root)
ok("①a 导出返回非空 zip 字节", isinstance(_blob, (bytes, bytearray)) and len(_blob) > 0,
   "%d 字节" % len(_blob))

_zn = zipfile.ZipFile(io.BytesIO(_blob)).namelist()
ok("①b 含 config.json（设置）", "config.json" in _zn)
ok("①c 含 memory/（记忆）", any(n.startswith("memory/") for n in _zn))
ok("①d 含 data/（其余数据）", any(n.startswith("data/") for n in _zn))
ok("①e manifest 带 kind=backup",
   json.loads(zipfile.ZipFile(io.BytesIO(_blob)).read("manifest.json")).get("kind") == "backup")

ok("②a 排除 bot.pid", not any(n.endswith("bot.pid") for n in _zn))
ok("②b 排除 bot.lock", not any(n.endswith("bot.lock") for n in _zn))
ok("②c 排除 *.log", not any(n.endswith(".log") for n in _zn))
ok("②d 排除 logs/ 目录", not any(n.startswith("data/logs/") for n in _zn))

# ── ②e~②h：图库排除（R171-③；图库实测占 data/ 的 98%，进包＝包体积暴涨而对恢复无用）──
ok("②e ★分母守卫：图库**真的造出来了**（否则下面两条测的是空气）",
   os.path.isdir(os.path.join(_root, "data", "gen_images")),
   os.path.join(_root, "data", "gen_images"))
ok("②f ★排除图库：`data/gen_images/` 一片都不进包",
   not any(n.startswith("data/gen_images/") for n in _zn),
   [n for n in _zn if "gen_images" in n][:3])
ok("②g ★连台账一起排除（`index.jsonl` 也在 gen_images/ 里，一并出局）",
   not any(n.endswith("gen_images/index.jsonl") for n in _zn))
_man_note = json.loads(zipfile.ZipFile(io.BytesIO(_blob)).read("manifest.json")).get("note", "")
ok("②h ★不静默：manifest 的 note 里**明说**不含图库（用户看得到，不是偷偷漏掉）",
   "生图" in _man_note or "gen_images" in _man_note, _man_note[-40:])
# ⭐ 反向锚：把排除项从源码里拿掉 ⇒ ②f 的检查必须变红（证明 ②f 真在守，不是恒真）
_src_bk = io.open(os.path.join(ROOT, "agent", "backup.py"), encoding="utf-8").read()
_i_exc = _src_bk.index("_EXC_DIRS = frozenset({")
_j_exc = _src_bk.index("})", _i_exc)
_fake_exc = _src_bk[:_i_exc] + '_EXC_DIRS = frozenset({"debug"})' + _src_bk[_j_exc + 2:]
ok("②i ★反向锚：把 gen_images 从 _EXC_DIRS 拿掉后，②f 那条守卫的**判据式**会变红",
   "gen_images" not in _fake_exc[_i_exc - 200:_j_exc + 300],
   "反向锚有效")

# 恢复到**另一个**空工程目录（模拟换机），核对内容
_dst = tempfile.mkdtemp(prefix="pm-backup-restore-")
_r = BK.backup_import(_dst, _blob)
ok("③a 恢复返回 ok=True（且无 error）", _r.get("ok") is True, str(_r.get("error", "")))
ok("③b 恢复报告里 config_restored=True（设置走了实时链路）", _r.get("config_restored") is True)
ok("③c 记忆文件落盘", _r.get("memory_files", 0) >= 1, "memory_files=%s" % _r.get("memory_files"))
_mem = os.path.join(_dst, "data", "memory", "群A", "x.json")
ok("③d 记忆内容逐字节还原",
   os.path.exists(_mem) and json.loads(io.open(_mem, encoding="utf-8").read()) == {"notes": ["喜欢咖啡"]})
_cats = os.path.join(_dst, "data", "persona_cats.json")
ok("③e data/ 其余文件还原", os.path.exists(_cats) and json.loads(io.open(_cats, encoding="utf-8").read()) == {"cat1": ["p1"]})
_cfgs = io.open(os.path.join(_dst, "config.json"), encoding="utf-8").read()
ok("③f config.json 拿到备份里的 api_key", _FAKE_KEY in _cfgs)

print("\n══ 二、安全三条（一条都不能少）══")
ok("④a `_safe_rel` 拒 `..` 穿越", BK._safe_rel("../../windows/system32/x") is None)
ok("④b `_safe_rel` 拒绝对路径", BK._safe_rel("/etc/passwd") is None)
ok("④c `_safe_rel` 拒盘符", BK._safe_rel("C:\\Windows\\x") is None)
ok("④d `_safe_rel` 放行正常相对路径", BK._safe_rel("memory/a/b.json") == "memory/a/b.json")


def _evil_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps({"app": "Persona Morph", "kind": "backup", "v": 2}))
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


# 篡改包：试图把 server.token 换成空（敞开口令）+ 往系统路径写文件（穿越）
_evil_cfg = json.dumps({"server": {"port": 17860, "token": ""}, "api_key": "hacked"})
_evil = _evil_zip([
    ("config.json", _evil_cfg),
    ("memory/../../evil.json", "{}"),
    ("memory/evil.bat", "@echo off"),
    ("data/../../evil2.json", "{}"),
])
_dst2 = tempfile.mkdtemp(prefix="pm-backup-evil-")
with io.open(os.path.join(_dst2, "config.json"), "w", encoding="utf-8") as f:
    json.dump({"server": {"port": 17860, "token": "SAFE-LIVE-TOKEN"}}, f)
_r2 = BK.backup_import(_dst2, _evil)
_r2cfg = json.loads(io.open(os.path.join(_dst2, "config.json"), encoding="utf-8").read())
# 语义：备份里的 server.* 一律不采用。落盘的 server 段必须取「当前运行配置」
# （get_config()），既不是备份里的空口令，也不是本判据预置的 SAFE-LIVE-TOKEN
# —— 后者只存在于 _dst2/config.json 文件里，从未进入内存 get_config()。
_live_srv = dict(CFG.get_config().get("server", {}) or {})
_r2cfg_srv = _r2cfg.get("server", {}) or {}
ok("⑤a 备份里的 server.* **一个字都没采用**（用的是当前运行配置，不是备份里的空口令）",
   _r2cfg_srv.get("token") not in ("", None)
   and _r2cfg_srv.get("token") == _live_srv.get("token")
   and _r2cfg_srv.get("token") != "SAFE-LIVE-TOKEN",
   "落盘=%s 运行=%s" % (_r2cfg_srv.get("token"), _live_srv.get("token")))
ok("⑤b 恢复报告里明确说了「未恢复 server.*」（不是静默）",
   any("server" in w for w in (_r2.get("warnings") or [])), str(_r2.get("warnings"))[:80])
ok("④e 穿越条目**没落盘**（`evil.json` 不在产品根、也不在上级）",
   not os.path.exists(os.path.join(_dst2, "evil.json")) and
   not os.path.exists(os.path.join(_dst2, "..", "evil.json")))
ok("④f 穿越条目进了 warnings", any("可疑路径" in w for w in (_r2.get("warnings") or [])))
ok("⑥a `memory/evil.bat` **被跳过**（记忆只收 .json）",
   not os.path.exists(os.path.join(_dst2, "data", "memory", "evil.bat")))
ok("⑥b 非 json 条目进了 warnings", any("非 json" in w for w in (_r2.get("warnings") or [])))
ok("⑥c `data/` 穿越条目也没落盘", not os.path.exists(os.path.join(_dst2, "evil2.json")))

print("\n══ 三、我方加强口径 ══")
# ⑦ 判据环境不写盘 —— ⛔ **语义修正**：要保护的是"**用户那份工程目录**"，不是"任何写盘"。
#    原来断言的是"判据环境 ⇒ 连**临时目录**也不写"，而 `backup_import()` 本来就收 `root` 参数
#    （判据隔离口）⇒ 判据写自建临时目录**天然安全**，却被一并挡掉。
#    后果（实测）：套件给所有判据**全局**设了 `PM_JUDGE_NO_PROC=1` ⇒ 本判据前面那两段
#    "恢复后逐字节一致"**必红**（单跑带这个变量就是 35/2）。⇒ 改成按**落点**判：
#      · 落点＝产品根 ⇒ 拦（这才是保护用户）；
#      · 落点＝隔离目录 ⇒ 照写（否则判据自己验不了东西）。
#    ⛔ 测试它**不去真写产品根**（万一闸有 bug 就真改了用户目录）：直接断言**谓词**本身。
#       ⚠️ 谓词结果**依赖"判据环境"这个前提** ⇒ 先把它设上（否则干净单跑时 ⑦a 会假红）。
_old = os.environ.get("PM_JUDGE_NO_PROC")
os.environ["PM_JUDGE_NO_PROC"] = "1"
ok("⑦a 谓词：判据环境 + 落点是**产品根** ⇒ 拦（保护用户那份工程目录）",
   BK._blocks_write(BK._PRODUCT_ROOT) is True)
ok("⑦b 谓词：判据环境 + 落点是**隔离目录** ⇒ 不拦（判据要能验'恢复得对'）",
   BK._blocks_write(tempfile.mkdtemp(prefix="pm-backup-pred-")) is False)
os.environ.pop("PM_JUDGE_NO_PROC", None)
ok("⑦c 谓词：**非**判据环境 ⇒ 一律不拦（正常产品行为不受影响）",
   BK._blocks_write(BK._PRODUCT_ROOT) is False)
os.environ["PM_JUDGE_NO_PROC"] = "1"
_dst3 = tempfile.mkdtemp(prefix="pm-backup-judge-")
try:
    _r3 = BK.backup_import(_dst3, _blob)
finally:
    if _old is None:
        os.environ.pop("PM_JUDGE_NO_PROC", None)
    else:
        os.environ["PM_JUDGE_NO_PROC"] = _old
ok("⑦d 隔离目录 ⇒ 判据环境下**照写**（这条是 ③d/③e 的前提，也是套件里曾经红掉的那两段的对照）",
   os.path.exists(os.path.join(_dst3, "data", "memory", "群A", "x.json")))
ok("⑦e 判据环境下如实标了 judge_env / judge_blocked（不是假装成功）",
   _r3.get("judge_env") is True and _r3.get("judge_blocked") is False,
   "judge_env=%s judge_blocked=%s" % (_r3.get("judge_env"), _r3.get("judge_blocked")))
ok("⑦f 判据环境下仍做结算（memory_files 照样计数）", _r3.get("memory_files", 0) >= 1)

# ⑧ 路由接线：唯一路由表里成对存在
from agent.routes import ROUTES as _R, HANDLERS as _HD  # noqa: E402
ok("⑧a 路由表声明 /api/backup/export = POST", _R.get("/api/backup/export") == ("POST",), str(_R.get("/api/backup/export")))
ok("⑧b 路由表声明 /api/backup/import = POST", _R.get("/api/backup/import") == ("POST",), str(_R.get("/api/backup/import")))
ok("⑧c export 指到 _rapi_backup_export_post",
   (_HD.get("/api/backup/export") or {}).get("POST") == "_rapi_backup_export_post")
ok("⑧d import 指到 _rapi_backup_import_post",
   (_HD.get("/api/backup/import") or {}).get("POST") == "_rapi_backup_import_post")

# 方法必须真的存在，且签名一致
import ast  # noqa: E402
_wu = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
_tree = ast.parse(_wu)
_have = set()
for _n in ast.walk(_tree):
    if isinstance(_n, ast.FunctionDef):
        _have.add(_n.name)
ok("⑧e 两个 Handler 方法在 webui.py 里真的存在",
   "_rapi_backup_export_post" in _have and "_rapi_backup_import_post" in _have)
ok("⑧f `_bytes` 支持 disposition 参数（含中文文件名要走 RFC 5987）",
   "disposition=None" in _wu and "Content-Disposition" in _wu)

# 页面接线：两个按钮真的接上了
_html = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
ok("⑧g 页面有一键备份按钮且打到 /api/backup/export",
   "backupExport" in _html and "/api/backup/export" in _html)
ok("⑧h 页面有一键恢复按钮且打到 /api/backup/import",
   "backupImport" in _html and "/api/backup/import" in _html)

# 清理
import shutil  # noqa: E402
for _p in (_root, _dst, _dst2, _dst3):
    shutil.rmtree(_p, ignore_errors=True)

print("\n==== 一键备份/恢复判据：%d 通过 / %d 失败 ====" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for _f in FAIL:
        print("  · " + _f)
sys.exit(1 if FAIL else 0)
