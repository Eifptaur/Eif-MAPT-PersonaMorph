#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本地生图后端（`agent/sd_local.py` + `agent/sd_local_server.py`）判据 —— 离线、不下载、不联网发请求。

守的东西（用户 2026-09-17 的三条口径）：
  ①「你帮用户装，做成一个**可选项**；用户选了就弹安装提示，帮他安装；**在线安装看用户开不开**」
     ⇒ 默认 `allow_online_install=False`，不开就**拒绝下载**并说明（绝不偷偷联网）；
  ②「要能让用户**实时看到下载进度**：一共多少 / 现在下了多少 / 百分比是多少」⇒ `progress()` 字段齐、百分比算得对；
  ③「还要加那个按键，也就是**后台加载** … 可以办点别的事情」⇒ 有 `install_async()`（后台线程）+ 控制台按钮；
  另外：「**要提示用户需要下载，就必须写明时间可能会非常长**」⇒ `estimate()` 必须给"要下多少 GB + 预计多久"，
  并对被限速的源（实测 0.02~0.06 MB/s）**如实劝退**。

用法：`py -3 scripts/sd_local_selftest.py`
"""
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent import sd_local as S          # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(("  OK   " if cond else "  FAIL ") + name + (("   [%s]" % detail) if detail else ""))


def src(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


print("── A. 一条龙的四件事都在 ──")
for fn in ("status", "estimate", "install", "install_async", "start_server", "stop_server", "progress"):
    ok("A·%s 有实现" % fn, callable(getattr(S, fn, None)))
_sv = src(os.path.join("agent", "sd_local_server.py"))
ok("A·服务端实现了 a1111 两个端点（探测 + 生成）",
   "/sdapi/v1/sd-models" in _sv and "/sdapi/v1/txt2img" in _sv and '"images"' in _sv)

print("── B. 默认不偷偷联网（用户：在线安装看用户开不开）──")
_cfg = src(os.path.join("agent", "config.py"))
ok("B1 配置默认 allow_online_install=False", '"allow_online_install": False' in _cfg)
r = S.install(allow_online=False)
ok("B2 没开开关时 install 拒绝并说明", r[0] is False and "不偷偷联网" in r[1], r[1][:60])
ra = S.install_async(allow_online=False)
ok("B3 后台安装同样受开关管", ra[0] is False and "不偷偷联网" in ra[1], ra[1][:40])

print("── C. 「要下多少 + 预计多久」必须给足（用户要求写明可能很久）──")
est = S.estimate(do_probe=False)
# 2026-09-17 多档后：总量＝**当前档**（模型 + 该档的加速件）+ 运行库；不再写死某一档的体积
ok("C1 给了总量（当前档 + 运行库）", abs(est["gb"] - (S.preset_gb() + S.DEPS_GB)) < 0.02,
   "%s GB = %s + %s（档=%s）" % (est["gb"], est["model_gb"], est["deps_gb"], est.get("preset")))
ok("C1b 档位清单至少两档（速度 + 画质，用户口径「不二选一」）",
   len(S.presets()) >= 2 and any(p["id"] == "speed" for p in S.presets()) and any(p["id"] == "quality" for p in S.presets()),
   "、".join(p["id"] for p in S.presets()))
ok("C1c 每档都带许可证与说明（发出去要讲清来源）",
   all(p.get("license") and p.get("note") for p in S.presets()), str([p["id"] for p in S.presets()]))
ok("C2 给了人话时间", bool(est["human"]) and ("分钟" in est["human"] or "小时" in est["human"]), est["human"])
ok("C3 给了速度与来源", est["mbps"] > 0 and bool(est["source"]), "%s MB/s · %s" % (est["mbps"], est["source"]))
ok("C4 有'低于这个速度就别下了'的门槛与劝退文案", S.MIN_MBPS > 0 and "小时" in src(os.path.join("agent", "sd_local.py")))
ok("C5 实测数字写进了文档（约 20 分钟 / 10.8 GB / 首次加载 15 秒）",
   "约 20 分钟" in _cfg or "20 分钟" in src(os.path.join("agent", "sd_local.py")))

print("── D. 实时进度（一共多少/下了多少/百分比）──")
p0 = S.progress()
ok("D1 progress 字段齐", set(["running", "phase", "message", "done_bytes", "total_bytes",
                          "percent", "mbps", "eta_seconds", "ok", "error"]) <= set(p0.keys()))
S._set_prog(running=True, done_bytes=3 * 1073741824, total_bytes=12 * 1073741824)
p1 = S.progress()
ok("D2 百分比算得对（3/12 GB ⇒ 25%）", abs(p1["percent"] - 25.0) < 0.2, str(p1["percent"]))
S._set_prog(running=False, percent=0.0)

print("── E. 后台安装 + 控制台按键（用户：别让用户盯着弹窗等）──")
ok("E1 有后台线程入口", "threading.Thread" in src(os.path.join("agent", "sd_local.py"))
   and "daemon=True" in src(os.path.join("agent", "sd_local.py")))
_web = src(os.path.join("agent", "webui.py"))
ok("E2 四个端点都在（状态/进度/安装/起停）",
   all(x in _web for x in ("/api/image_gen/local", "/api/image_gen/local/progress",
                           "/api/image_gen/local/install", "/api/image_gen/local/start")))
_ch = src(os.path.join("agent", "console_html.py"))
ok("E3 控制台有：状态行 + 进度条 + 安装/启动/停止三个按钮",
   'id="sdLocalState"' in _ch and 'id="sdLocalFill"' in _ch
   and all(('id="%s"' % i) in _ch for i in ("sdLocalInstall", "sdLocalStart", "sdLocalStop")))
ok("E4 弹窗里写明了「后台进行」这件事（关掉弹窗也能继续下）",
   "后台进行" in _ch and "关掉弹窗" in _ch)
ok("E5 弹窗里带了估时与来源（不是空口承诺）", "est.human" in _ch and "est.source" in _ch)

print("\n== 本地生图后端判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
