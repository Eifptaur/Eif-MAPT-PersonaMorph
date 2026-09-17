#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真语音条（`agent/voice_strip.py`）的判据 —— 离线、不碰微信、不动鼠标。

守的东西：
  ① **三闸如实报**：虚拟声卡 / sounddevice / 合成引擎，缺哪道就说哪道，不假装能发；
  ② **位置运行时现算**：那个"进录音态"的圆圈会随右侧栏开关左右漂（实测 0.745 ↔ 0.878），
     所以每次发送前扫输入条图标行现算，配置值只当兜底（2026-09-17 改：不再要用户标定）；
  ③ **只认 DB 回读 `type=语音`**（不信 GUI 返回值——这是当年实验链最值钱的一条）；
  ④ **两道自检**：会话闸（发错人不可逆）+ 音量点闸（不发静音语音条）；
  ⑤ **形态选项**：用户选"真语音条/音频文件"，**默认真语音条**；前提不齐时如实回退并说明；
  ⑥ 依赖清单里有 `sounddevice`（否则装完还是播不了）。

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
ok("A1 status 给 ok / why / mic / out_idx / calibrated 等",
   set(["ok", "why", "mic", "out_idx", "calibrated"]) <= set(st.keys()))
ok("A2 拿不到虚拟麦克风时 why 里说清「要有虚拟声卡」" if not st["mic"] else "A2 本机有虚拟麦克风（%s）" % st["mic"],
   bool(st["mic"]) or ("虚拟声卡" in st["why"]))
ok("A3 位置不再是前置条件（不再因为「没标定」把能用的人挡在门外）",
   "auto_locate" in st and "还没标定" not in src(os.path.join("agent", "voice_strip.py")))

print("── B. 位置扫描（纯函数，造图谱判）──")
prof = [0] * 1000
for cx, w in ((314, 24), (360, 22), (405, 23), (513, 17), (878, 24), (935, 40)):
    for k in range(cx - w // 2, cx + w // 2):
        prof[k] = 20
x = VS.pick_record_x(prof, 1000)
ok("B1 一排图标里挑出圆圈（0.878），不会被更右边的「发送」抢走",
   x is not None and abs(x - 0.878) < 0.01, str(x))
ok("B2 绝不去点左半那些「会弹窗」的图标（表情/文件夹）", (x or 0) >= VS.RECORD_X_MIN)
narrow = [0] * 1000
for cx, w in ((271, 22), (513, 17), (745, 22), (930, 40)):     # 右侧栏开着 ⇒ 图标整体左移
    for k in range(cx - w // 2, cx + w // 2):
        narrow[k] = 20
x2 = VS.pick_record_x(narrow, 1000)
ok("B3 右侧栏开着（圆圈漂到 0.745）也认得出来", x2 is not None and abs(x2 - 0.745) < 0.01, str(x2))
ok("B4 空图谱 ⇒ None（交回配置值/兜底候选，不许瞎点）", VS.pick_record_x([0] * 1000, 1000) is None)
ok("B5 兜底候选全在右区（≥0.8）＝点空也只点到输入框/发送，不会弹窗",
   all(v >= 0.80 for v in VS.RECORD_FALLBACKS) and VS.RECORD_X_MAX <= 0.92)

print("── C. 窗口/点击链的源码级看守 ──")
_vs = src(os.path.join("agent", "voice_strip.py"))
ok("C1 进录音态＝**运行时扫图标行现算**（不写死比例，也不写死绝对坐标）",
   "def locate_record" in _vs and "def _icon_cols" in _vs and "pick_record_x" in _vs)
ok("C2 播的是虚拟声卡那一路（sounddevice RawOutputStream 到 out_device）",
   "sd.RawOutputStream" in _vs and "_out_device()" in _vs)
ok("C3 **只认 DB 回读**：等的是新出现的 `type=语音` 行",
   'str(row.get("type") or "") == "语音"' in _vs and "local_id" in _vs)
ok("C4 这俩控件只认真实点击（投递只出悬停），且**用完把光标还原**",
   "click_real_hold" in _vs and "click_real_hold" in src(os.path.join("agent", "ui_adapt.py"))
   and "SetCursorPos" in src(os.path.join("agent", "ui_adapt.py")))
ok("C5 发之前先过**会话闸**：当前会话不是目标就切过去、切不过去就不发",
   "chat_is_open" in _vs and "switch_chat_posted" in _vs and "不发语音条" in _vs)
ok("C6 录音音量点一直不亮 ⇒ **当场取消**（不发静音语音条）",
   "def _meter_level" in _vs and "meter_guard" in _vs and "静音语音条" in _vs)
ok("C7 绿色发送取**最右**绿簇（左边那串音量点同样是绿的，别把均值带偏）",
   "def _green_send" in _vs and "GREEN_X_MIN" in _vs)
ok("C8 没进录音态时逐个候选位置重试，失败要如实说" ,
   "def _enter_record" in _vs and "没进录音态" in _vs)
ok("C9 被防打扰闸拦下时**等一等再试**（用户手一离开鼠标，下一枪就能中）",
   "手一离开鼠标我就重试" in _vs and "wait_s" in _vs)
_ua = src(os.path.join("agent", "ui_adapt.py"))
ok("C10 真点**开枪前再确认一次落点**（防「检查完→开枪」之间被控制台/浏览器抢走）",
   _ua.count("real_guard(sx, sy, gui=gui") >= 2 and "开枪前落点已经变了" in _ua)

print("── D. 形态选项（默认真语音条）＋ 接线 ──")
_cfg = src(os.path.join("agent", "config.py"))
ok("D1 配置里有 voice_reply.form（默认 strip）与 fallback_file",
   '"form": "strip"' in _cfg and '"fallback_file": True' in _cfg)
ok("D2 voice_strip 开关默认开（形态默认就是真语音条），且有 meter_guard/real_click 两个行为键",
   '"voice_strip": {' in _cfg and '"meter_guard": True' in _cfg and '"real_click": True' in _cfg)
ok("D3 兜底默认位置就是实测到的圆圈（0.878），不是空白处",
   "[0.878" in _cfg)
_tl = src(os.path.join("agent", "tools.py"))
ok("D4 工具按形态分流：strip ⇒ 走 voice_strip.send", "_vs.send(ctx.get(\"wechat\")" in _tl)
ok("D5 前提不齐时**如实回退并说明**（不许静默降级）",
   "_strip_note" in _tl and "为什么这次是文件" in _tl)
_ch = src(os.path.join("agent", "console_html.py"))
ok("D6 控制台给出形态选项（真语音条 / 音频文件）",
   'data-cfg="voice_reply.form"' in _ch and "真语音条（微信语音气泡，推荐）" in _ch
   and "音频文件（点开才能听的那种）" in _ch)
ok("D7 控制台**能开关真语音条机制**并写明代价（右 Alt：不动鼠标、要微信在前台）",
   'data-cfg="voice_strip.enabled"' in _ch and "不会动你的鼠标" in _ch and "前台" in _ch)
ok("D9 默认走**右 Alt 路**（不动鼠标），真点只当兜底",
   '"enter_via": "alt"' in _cfg and '"fallback_click": True' in _cfg
   and "_enter_record_alt" in _vs and "_cancel_alt" in _vs)
ok("C10 Alt 路的前置检查：注入用 SendInput + 扩展位，且**先确认抓得到帧**（防假阴性）",
   "SendInput" in _vs and "KEYEVENTF_EXTENDEDKEY" in _vs and "抓不到微信画面" in _vs)
_wv = _vs.split("def _wait_voice")[1][:1400]
ok("C11 回读要**轮询等待**（微信写库有延迟；第一眼看到旧行就判失败＝假阴性，会害得又发一个文件）",
   "继续等" in _wv and "time.sleep(1.0)" in _wv and "还是那条旧语音" not in _wv)
ok("D8 控制台有「念法纠正」入口（多音字例外表）",
   'data-cfg="voice_reply.pronounce"' in _ch and "念法纠正" in _ch)

print("── E. 依赖 ──")
ok("E1 requirements 里有 sounddevice（装依赖时装得上）", "sounddevice" in src("requirements.txt"))
try:
    import sounddevice  # noqa: F401
    ok("E2 本机运行时装了 sounddevice", True)
except Exception as e:
    ok("E2 本机运行时装了 sounddevice", False, "%s（演示前先装：pip install sounddevice）" % e)

print("\n== 真语音条判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
