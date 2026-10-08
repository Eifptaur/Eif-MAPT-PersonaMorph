# -*- coding: utf-8 -*-
"""W7 自检：版本能力矩阵 + 依赖自愈。**不需要微信、不联网、不改环境**。

判据分两组：
  V 版本矩阵：能力目录、种子数据、未实测版本不许写成支持、覆盖式合并、原子落盘、摘要计数
  D 依赖自愈：requirements 解析、版本比较、三态体检、离线优先的计划、dry-run 不执行命令

用法：py -3 scripts\\w7_selftest.py   （非零退出＝有失败）
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import dep_heal as dh # noqa: E402
from agent import version_matrix as vm # noqa: E402

OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name, (" · " + extra) if extra else ""))


# ── V 版本能力矩阵 ───────────────────────────────────────────────────────
print("[V] 版本能力矩阵")
data = vm.load()
run = vm.find_run(data, "4.1.15.8", "1.2.2.2")
ck("V1 能力目录含关键项（发送/表情/朋友圈/授权门/UIA）",
   all(k in vm.CAPS for k in ("send_text", "emoji_send", "moments_open", "moments_scroll",
                              "moments_composer", "moments_publish", "uia_tree")))
ck("V2 种子里有 4.1.15.8 × 1.2.2.2 的实测记录", bool(run), "when=%s" % (run or {}).get("when"))

caps = vm.capabilities(data, "4.1.15.8", "1.2.2.2")
ck("V3a 发文本＝ok", caps["send_text"]["status"] == "ok", caps["send_text"]["evidence"][:40])
ck("V3b 发收藏表情＝ok", caps["emoji_send"]["status"] == "ok")
ck("V3c 刷朋友圈＝ok", caps["moments_scroll"]["status"] == "ok")
ck("V3d 点发表＝user_gated（机制通但要用户点头）", caps["moments_publish"]["status"] == "user_gated")
ck("V3e UIA 树＝no（本版本实测不可用）", caps["uia_tree"]["status"] == "no")

unknown_caps = vm.capabilities(data, "9.9.9", "0.0.1")
ck("V4 未实测版本对 ⇒ 全部 unknown（不许外推）",
   all(v["status"] == "unknown" for v in unknown_caps.values()) and len(unknown_caps) == len(vm.CAPS))
g_bad = vm.gate(data, "9.9.9", "0.0.1")
ck("V5 版本门：未实测 ⇒ measured=False 且给降级建议",
   g_bad["measured"] is False and "没有实测记录" in g_bad["advice"])
ck("V6 版本门：实测过 ⇒ measured=True", vm.gate(data, "4.1.15.8", "1.2.2.2")["measured"] is True)

# `gate()` 的**中间那条 partial 路径**原先一条断言都没有（把 `if missing:` 写成
# `if missing and False:` 判据仍全绿）——而这条正是"只实测了一项的版本会被判成可自动发送"的老 bug。
_pcaps = {"emoji_send": {"status": "ok"}}
_pdata = vm.merge_runs({"runs": []}, {"wechat": "4.1.15.8", "adapter": "1.2.2.2",
                                      "caps": _pcaps, "when": "2026-09-14"})
g_part = vm.gate(_pdata, "4.1.15.8", "1.2.2.2")
ck("V6b 只实测了部分能力 ⇒ measured=False 且 partial=True", 
   g_part["measured"] is False and g_part["partial"] is True, str(g_part["scope"]))
ck("V6c partial 时 missing 正好＝「必需能力 − 已测」（不许空表）",
   set(g_part["missing"]) == set(vm.REQUIRED_CAPS) - set(_pcaps) and len(g_part["missing"]) > 0,
   str(g_part["missing"]))
ck("V6d partial 的建议说「只实测了部分能力」且**不含**「没有实测记录」",
   "只实测了部分能力" in g_part["advice"] and "没有实测记录" not in g_part["advice"])
# 反向锚（V6b~V6d 不是恒真）：把必需能力补齐 ⇒ 必须翻成 measured=True / partial=False / missing 空
_fdata = vm.merge_runs(_pdata, {"wechat": "4.1.15.8", "adapter": "1.2.2.2",
                                "caps": {c: {"status": "ok"} for c in vm.REQUIRED_CAPS}})
g_full = vm.gate(_fdata, "4.1.15.8", "1.2.2.2")
ck("V6e 反向锚：必需能力补齐后 ⇒ measured=True / partial=False / missing 空",
   g_full["measured"] is True and g_full["partial"] is False and g_full["missing"] == [], str(g_full["scope"]))

m1 = vm.merge_runs({"runs": []}, {"wechat": "a", "adapter": "1", "caps": {}})
m2 = vm.merge_runs(m1, {"wechat": "a", "adapter": "1", "caps": {"send_text": {"status": "ok"}}})
m3 = vm.merge_runs(m2, {"wechat": "b", "adapter": "1", "caps": {}})
ck("V7 同版本对覆盖、不同版本对追加",
   len(m3["runs"]) == 2 and vm.find_run(m3, "a", "1")["caps"].get("send_text"))

with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "cap.json")
    vm.record("1.2.3", "9.9", {"send_text": {"status": "ok"}}, path=p)
    back = vm.load(p)
    ck("V8a 落盘可回读", vm.find_run(back, "1.2.3", "9.9") is not None)
    ck("V8b 原子写不留 .tmp", not os.path.exists(p + ".tmp"))
    ck("V8c record 会带 when", bool(str(vm.find_run(back, "1.2.3", "9.9").get("when") or "")))

ck("V9 summarize 计数", vm.summarize({
    "a": {"status": "ok"}, "b": {"status": "no"},
    "c": {"status": "unknown"}, "d": {"status": "user_gated"}}) == "可用 1 · 不可用 1 · 未实测 1 · 待用户点头 1")
ck("V10 适配层版本＝运行时实测值（不是 MIN_VER 回落的 1.1.5.1）",
   vm.adapter_version() == dh.installed_version("wechatauto-replica")
   and vm.adapter_version() not in ("unknown", "1.1.5.1"), vm.adapter_version())

# ── D 依赖自愈 ───────────────────────────────────────────────────────────
print("[D] 依赖自愈")
reqs = dh.parse_requirements()
names = [r[0] for r in reqs]
ck("D1 requirements 解析出包名（含核心驱动库与 cv2 链）",
   "wechatauto-replica" in names and "opencv-python" in names and "numpy" in names,
   "%d 项" % len(reqs))
got = [r for r in reqs if r[0] == "wechatauto-replica"][0]
# ⛔ **锚「不变量」，不锚「数字」**：这条原来写死 `==1.2.2.2`；那个库升到 1.2.4.4 之后它就变成**假红**
#   （判据数字过期 —— 每升一次库都要手改判据，早晚会漏）。真正要守的是两件事：
#    ① 核心库必须**精确 pin**（`==`）：这个库的 API 一变，我们的调用点就得跟着改 ⇒ 用 `>=`
#       等于把"哪天被升上去"交给运气；
#    ② 这个 pin **必须与运行时实际装的版本一致** —— 否则"要求"与"事实"两张皮
#       （体检报"全部满足"，而实际跑的是另一个版本）。
_inst_core = str(dh.installed_version("wechatauto-replica") or "")
ck("D2 核心库要求是**精确 pin**（`==`）且与运行时实装版本一致",
   got[1] == "==" and bool(_inst_core) and got[2] == _inst_core,
   "要求 %s%s ／ 实装 %r" % (got[1], got[2], _inst_core))

ck("D3a vtuple 数值版", dh.vtuple("1.2.2.2") == ((0, 1), (0, 2), (0, 2), (0, 2)))
ck("D3b vtuple 带字母后缀不炸",
   dh.vtuple("1.0.0b10") == ((0, 1), (0, 0), (0, 0), (1, "b"), (0, 10)))
ck("D4a >= 满足", dh.spec_ok("1.2.2.2", ">=", "1.1.5.1") is True)
ck("D4b == 满足", dh.spec_ok("1.2.2.2", "==", "1.2.2.2") is True)
ck("D4c == 不满足", dh.spec_ok("1.0.1", "==", "1.0.0") is False)
ck("D4d >= 不满足（低了）", dh.spec_ok("1.9", ">=", "2.2.6") is False)
ck("D4e 大版本号比较（12.1 >= 9.0）", dh.spec_ok("12.1.0", ">=", "9.0.0") is True)

fake = {"A": "1.0.0", "B": "0.1.0"}
cls = dh.classify([("A", ">=", "0.9"), ("B", ">=", "0.2"), ("C", "==", "3.0")],
                  lambda n: fake.get(n))
ck("D5 三态体检 ok/mismatch/missing",
   [c["status"] for c in cls] == ["ok", "mismatch", "missing"], str(cls))

p_off = dh.build_plan(["C"], "PY", offline_available=True)
ck("D6a 离线可用 ⇒ 第一条命令走 --no-index",
   "--no-index" in p_off["commands"][0] and "--find-links" in p_off["commands"][0])
p_on = dh.build_plan(["C"], "PY", offline_available=False)
ck("D6b 离线不可用 ⇒ 退镜像",
   "--index-url" in p_on["commands"][0] and dh.MIRROR in p_on["commands"][0])
ck("D6c 计划末尾永远带复查命令", "selftest.py" in p_on["commands"][-1])
ck("D6d 无缺失时不产生安装命令", len(dh.build_plan([], "PY", True)["steps"]) == 1)

diag = dh.diagnose()
s = dh.summarize(diag)
ck("D7 真机体检不抛异常且逐包有结论",
   isinstance(diag, list) and len(diag) == len(reqs) and all("status" in d for d in diag),
   dh.summary_line())
sp = dh.site_packages()
ck("D7b 体检盯的是**项目运行时**的 site-packages（防止「跑在哪个解释器上」骗结论）",
   bool(sp) and "runtime" in sp.lower(), dh.probe_source())
key = {d["name"]: d for d in diag}
ck("D7c 运行时里核心驱动库的**实装版本 == requirements 的 pin**（不是旧版回落的）",
   key["wechatauto-replica"]["installed"] == got[2]
   and key["wechatauto-replica"]["status"] == "ok",
   str(key["wechatauto-replica"]))
ck("D7d 运行时里 cv2 链与 numpy 都在",
   all(key[n]["status"] == "ok" for n in ("numpy", "opencv-python", "PyAutoGUI")),
   "缺 %s / 不符 %s" % (s["missing"], s["mismatch"]))
ck("D8 离线 wheels 目录探测返回布尔", dh.offline_available() in (True, False),
   "offline_available=%s" % dh.offline_available())
ck("D9 运行期解释器路径非空", bool(dh.runtime_python()), dh.runtime_python())

calls = {"n": 0}
_orig = dh.subprocess.run


def _spy(*a, **k):
    calls["n"] += 1
    raise AssertionError("dry-run 不应该真的执行命令")


dh.subprocess.run = _spy
try:
    r = dh.run(execute=False)
    executed = r.get("executed")
finally:
    dh.subprocess.run = _orig
ck("D10 dry-run 不执行任何命令", executed is False and calls["n"] == 0)

print("\n[F] 离线包（`offline/wheels`）真的能满足 requirements —— 直接判「离线装得上」")
# ⛔ 现场：`requirements.txt` 写 `wechatauto-replica==1.2.4.4`，而 `offline/wheels` 里只有 **1.2.2.2**
#   ⇒ "含依赖"的便携包**根本装不起来**，只能联网补齐（用户实测报回来的）。
#   而原来的判据 D8 只问"`offline/` 目录在不在"（返回布尔）⇒ **这种版本错位它看不见**。
#   ⇒ 这里补两条：① 逐个核"精确 pin"有没有对应版本的轮子；② **真跑一次离线解析**（`--no-index`）。
import glob as _glob
import re as _re
import subprocess as _sp

_W_HEELS = _glob.glob(os.path.join(ROOT, "offline", "wheels", "*.whl"))
ck("F1 分母守卫：`offline/wheels` 扫到 ≥40 个轮子（否则下面两条测的是空气）",
   len(_W_HEELS) >= 40, "%d 个" % len(_W_HEELS))

_HAVE = set()
for _f in _W_HEELS + _glob.glob(os.path.join(ROOT, "offline", "wheels", "*.tar.gz")):
    _m = _re.match(r"^([A-Za-z0-9_.\-]+?)-(\d[^-]*)", os.path.basename(_f))
    if _m:
        _HAVE.add((_m.group(1).lower().replace("_", "-"), _m.group(2)))
_PINS = []
for _l in open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8"):
    _l = _l.split("#")[0].strip()
    _m = _re.match(r"^([A-Za-z0-9_.\-]+)\s*==\s*([\w.]+)$", _l)
    if _m:
        _PINS.append((_m.group(1).lower().replace("_", "-"), _m.group(2)))
_MISS = [p for p in _PINS if p not in _HAVE]
ck("F2 ★ 每个**精确 pin** 都能在 `offline/wheels` 里找到对应版本（逐个核，不看目录在不在）",
   bool(_PINS) and not _MISS, "核了 %d 个；缺/错版：%s" % (len(_PINS), _MISS))

# ② **真跑一次离线解析**：`pip download --no-index --find-links offline/wheels -r requirements.txt`
#   ⇒ 成功 = "含依赖包"那句承诺成立（连传递依赖一起算）。只解析、不安装（下载到临时目录，用完删）。
_TMPF = tempfile.mkdtemp(prefix="pm-w7-offline-")
try:
    _r = _sp.run([dh.runtime_python(), "-m", "pip", "download", "--no-index",
                  "--find-links", os.path.join(ROOT, "offline", "wheels"),
                  "-r", os.path.join(ROOT, "requirements.txt"), "-d", _TMPF],
                 capture_output=True, text=True, timeout=300,
                 cwd=ROOT)
    _tail = (_r.stdout or "").strip().splitlines()[-1:] or [""]
    ck("F3 ★★ **离线（`--no-index`）能解析出全部依赖** —— 这是「含依赖包」承诺的直接判据",
       _r.returncode == 0, "rc=%s ｜ %s" % (_r.returncode, str(_tail[0])[:120]))
finally:
    import shutil as _sh
    _sh.rmtree(_TMPF, ignore_errors=True)

# ④ **`offline_available()` 必须真的核版本**（不是"目录里有没有这个文件"）
#   现场（用户实测报回）：错版轮子（1.2.2.2 而 requirements 要 1.2.4.4）也判"离线可用"
#   ⇒ 走 `--no-index` ⇒ 装不上 ⇒ 只能人工联网补齐。判据：正例真、反例假。
import shutil as _sh2
import tempfile as _tf

_BACKUP_WD = dh.WHEELS_DIR
_TMPD = _tf.mkdtemp(prefix="pm-w7-offline-bad-")
try:
    _src_files = sorted(os.listdir(_BACKUP_WD))
    for _f in _src_files[:12]:                      # 摆够分母（≥10 个才不像"空集")
        _sh2.copy2(os.path.join(_BACKUP_WD, _f), os.path.join(_TMPD, _f))
    # 把那个"精确 pin"的包换成**错版本**（模拟用户遇到的那次）
    _sh2.copy2(os.path.join(_BACKUP_WD, "wechatauto_replica-1.2.4.4-py3-none-any.whl"),
               os.path.join(_TMPD, "wechatauto_replica-1.2.2.2-py3-none-any.whl"))
    dh.WHEELS_DIR = _TMPD
    dh._OFFLINE_CACHE.clear()
    _bad_ok = dh.offline_available()
    _bad_why = dh.offline_why()
    ck("F4 ★★ 行为测：**错版轮子目录** ⇒ `offline_available()` 必须 False"
       "（旧实现只看「有没有这个文件」，这种错位它照样判可用）",
       _bad_ok is False, "返回 %s ｜ 原因：%s" % (_bad_ok, _bad_why[:110]))
finally:
    dh.WHEELS_DIR = _BACKUP_WD
    dh._OFFLINE_CACHE.clear()
    _sh2.rmtree(_TMPD, ignore_errors=True)
ck("F5 ★ 反例锚：真实 `offline/wheels` ⇒ `offline_available()` 为 True"
   "（否则上面的 F4 只是「永远假」）",
   dh.offline_available() is True, dh.offline_why()[:110])

# ⑤ **唯一实现点 + 失败要能退联网**（都是"改一处就得两处一起改"的收口）
_SD = io.open(os.path.join(ROOT, "scripts", "setup_deps.py"), encoding="utf-8").read()
_SD_CODE = "\n".join(l.split("#")[0] for l in _SD.splitlines())
ck("F6 ★ 安装脚本**调唯一实现** `dep_heal.offline_available()`，不许再自己抄一份弱检查",
   "dep_heal" in _SD_CODE and "offline_available(" in _SD_CODE
   and 'startswith("wechatauto_replica-")' not in _SD_CODE)
ck("F7 ★★ **离线装失败必须自动退联网**（原来没有这一步 ⇒ 用户只能自己想办法）",
   "自动改走联网安装" in _SD)

print("\n[结论] %d 通过 / %d 失败" % (len(OK), len(BAD)))
if BAD:
    print("失败项：%s" % BAD)
sys.exit(1 if BAD else 0)
