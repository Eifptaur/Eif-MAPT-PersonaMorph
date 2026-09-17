#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真语音条（`agent/voice_strip.py`）的判据 —— 离线、不碰微信、不动鼠标。

守的东西：
  ① **三闸如实报**：虚拟声卡 / sounddevice / 合成引擎，缺哪道就说哪道，不假装能发；
  ② **没标定就拒发**（那个"进录音态"的圆圈位置随版本/尺寸变，位置存在配置里）；
  ③ **只认 DB 回读 `type=语音`**（不信 GUI 返回值——这是当年实验链最值钱的一条）；
  ④ **形态选项**：用户选"真语音条/音频文件"，**默认真语音条**；前提不齐时如实回退并说明；
  ⑤ 依赖清单里有 `sounddevice`（否则装完还是播不了）。

用法：py -3 scripts/voice_strip_selftest.py
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent import voice_strip as VS          # noqa: E402

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


print("── A. 三闸如实报 ──")
st = VS.status()
print("      status = %s" % str(st)[:220])
ok("status 一定给 ok / why / mic / out_idx / calibrated 五样",
   set(["ok", "why", "mic", "out_idx", "calibrated"]) <= set(st.keys()))
ok("拿不到虚拟麦克风时 why 里说清「要有虚拟声卡」" if not st["mic"] else "本机有虚拟麦克风（%s）" % st["mic"],
   bool(st["mic"]) or ("虚拟声卡" in st["why"]))

print("── B. 窗口/点击链的源码级看守 ──")
_vs = src(os.path.join("agent", "voice_strip.py"))
ok("B1 进录音态 = 点渲染区比例位置（不写死绝对坐标）",
   "wechat._click(gui, int(rw * rb[0]), int(rh * rb[1]))" in _vs)
ok("B2 播的是虚拟声卡那一路（sounddevice RawOutputStream 到 out_device）",
   "sd.RawOutputStream" in _vs and "_out_device()" in _vs)
ok("B3 **只认 DB 回读**：等的是新出现的 `type=语音` 行",
   'str(row.get("type") or "") == "语音"' in _vs and "local_id" in _vs)
ok("B4 没标定（record_btn 空）就拒发并提示标定",
   "还没标定" in _vs and "record_btn" in _vs)
ok("B5 标定用「绿簇」当判据，进态后立刻点 ✕ 退出（不留残余）",
   "def _green_cluster" in _vs and "绿簇=" in _vs and "0.30" in _vs)

print("── C. 形态选项（默认真语音条）──")
_cfg = src(os.path.join("agent", "config.py"))
ok("C1 配置里有 voice_reply.form（默认 strip）与 fallback_file",
   '"form": "strip"' in _cfg and '"fallback_file": True' in _cfg)
ok("C2 默认关的机制开关 voice_strip.enabled 在（没装虚拟声卡的人也不该被打扰）",
   '"voice_strip"' in _cfg and '"enabled": False' in _cfg)
_tl = src(os.path.join("agent", "tools.py"))
ok("C3 工具按形态分流：strip ⇒ 走 voice_strip.send", "_vs.send(ctx.get(\"wechat\")" in _tl)
ok("C4 前提不齐时**如实回退并说明**（不许静默降级）",
   "_strip_note" in _tl and "为什么这次是文件" in _tl)
_ch = src(os.path.join("agent", "console_html.py"))
ok("C5 控制台给出选项（真语音条 / 音频文件）",
   'data-cfg="voice_reply.form"' in _ch and "真语音条（微信语音气泡，推荐）" in _ch
   and "音频文件（点开才能听的那种）" in _ch)

print("── D. 依赖 ──")
ok("D1 requirements 里有 sounddevice（装依赖时装得上）",
   "sounddevice" in src("requirements.txt"))
try:
    import sounddevice  # noqa: F401
    ok("D2 本机运行时装了 sounddevice", True)
except Exception as e:
    ok("D2 本机运行时装了 sounddevice", False, "%s（演示前先装：pip install sounddevice）" % e)

print("\n== 真语音条判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
