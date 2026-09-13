# -*- coding: utf-8 -*-
"""W7 自检：版本能力矩阵 + 依赖自愈。**不需要微信、不联网、不改环境**。

判据分两组：
  V 版本矩阵：能力目录、种子数据、未实测版本不许写成支持、覆盖式合并、原子落盘、摘要计数
  D 依赖自愈：requirements 解析、版本比较、三态体检、离线优先的计划、dry-run 不执行命令

用法：py -3 scripts\\w7_selftest.py   （非零退出＝有失败）
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import dep_heal as dh      # noqa: E402
from agent import version_matrix as vm  # noqa: E402

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
ck("D2 核心库要求写成 ==1.2.2.2（与实测版本一致）", got[1] == "==" and got[2] == "1.2.2.2")

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
ck("D7c 运行时里核心驱动库＝1.2.2.2（不是 1.1.5.1）",
   key["wechatauto-replica"]["installed"] == "1.2.2.2" and key["wechatauto-replica"]["status"] == "ok",
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

print("\n[结论] %d 通过 / %d 失败" % (len(OK), len(BAD)))
if BAD:
    print("失败项：%s" % BAD)
sys.exit(1 if BAD else 0)
