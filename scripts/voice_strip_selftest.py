#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真语音条（`agent/voice_strip.py`）的判据 —— 离线、不碰微信、不动鼠标。

守的东西：
  ① **三闸如实报**：虚拟声卡 / sounddevice / 合成引擎，缺哪道就说哪道，不假装能发；
  ② **位置运行时现算**：那个"进录音态"的圆圈会随右侧栏开关左右漂（实测 0.745 ↔ 0.878），
     所以每次发送前扫输入条图标行现算，配置值只当兜底；
  ③ **只认 DB 回读 `type=语音`**（不信 GUI 返回值——这是当年实验链最值钱的一条）；
  ④ **两道自检**：会话闸（发错人不可逆）+ 音量点闸（不发静音语音条）；
  ⑤ **形态选项**：用户选"真语音条/音频文件"，**默认真语音条**；前提不齐时如实回退并说明；
  ⑥ 依赖清单里有 `sounddevice`（否则装完还是播不了）。

用法：py -3 scripts/voice_strip_selftest.py
"""
import io
import os
import re as _re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 同目录的 `_srcslice`
import _srcmatch as _sm # noqa: E402 空白容忍的源码断言

import _srcslice  # noqa: E402  （按 AST 取函数正文）

from agent import voice_strip as VS # noqa: E402


def seg(name, ordinal=0, cap=0):
    """`agent/voice_strip.py` 里某个函数的**正文**（AST 锚点，不拿 `def` 行当文本边界）。

    ⛔ 为什么不用「按 `def` 行把源码切成两半、再取其中一半」的写法：那种写法靠"函数在文件里的
      **位置**"定边界 ⇒ 函数一搬家就会**崩**或**静默切错**（判据含义悄悄变了，甚至因别处的文字而假绿）；
      项目还有一条机械判据（`structure_guard_selftest`）专门盯它，新写这类写法直接判红。
    ⛔ 也**不许**再把它当"存在性检查"顺手用：名字写错时那种写法会给一句莫名其妙的
      `IndexError`；而这里的 `func_src` 给的是能读的 `LookupError` —— 所以统一走 `seg()`，
      找不到就返回 `""`（断言自然为假，且失败信息里能看出是"名字不对"）。
    """
    try:
        s = _srcslice.func_src(_vs, name, ordinal)
    except Exception as e:  # noqa: BLE001
        return ""
    return s[:cap] if cap else s

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
# ⛔ 夹具里**必须**有一个落在 `[RECORD_X_MIN, RECORD_X_MAX]` 窗内、且在圆圈右侧的干扰簇。
#   原来只放 0.878（圆圈）与 0.935（发送），而 `RECORD_X_MAX=0.92` 把后一簇**事先滤掉**
#   ⇒ 窗内恒只有 1 个簇，`voice_strip.py:166` 的 `good[0] → good[-1]`（挑到「发送」那一键）就没判据了。
#   现在：圆圈 0.878 · **窗内干扰簇 0.905** · 「发送」0.94（宽度收到 20 ⇒ 与干扰簇之间留出 >14px，
#   免得被并簇——`pick_record_x` 里间距 ≤14px 的两簇算同一簇）。
for cx, w in ((314, 24), (360, 22), (405, 23), (513, 17), (878, 18), (905, 8), (940, 20)):
    for k in range(cx - w // 2, cx + w // 2):
        prof[k] = 20
x = VS.pick_record_x(prof, 1000)
ok("B1 一排图标里挑出圆圈（0.878），不会被窗内右侧的干扰簇（0.905）与更右边的「发送」抢走",
   x is not None and abs(x - 0.878) < 0.01, str(x))
ok("B1b 夹具可区分：窗内确实还有第二个簇（0.905）⇒ 取最左 / 取最右是能被判据分开的",
   abs((VS.pick_record_x(prof, 1000, x_min=0.90, x_max=0.92) or 0) - 0.905) < 0.01,
   str(VS.pick_record_x(prof, 1000, x_min=0.90, x_max=0.92)))
ok("B2 绝不去点左半那些「会弹窗」的图标（表情/文件夹）", (x or 0) >= VS.RECORD_X_MIN)
narrow = [0] * 1000
# 右侧栏开着 ⇒ 图标整体左移；同样要有一个**窗内、在圆圈右侧**的干扰簇（0.79），
# 否则这条也和 B1 一样"窗内恒 1 簇"、挑左挑右不可区分。
for cx, w in ((271, 22), (513, 17), (745, 22), (790, 8), (930, 40)):
    for k in range(cx - w // 2, cx + w // 2):
        narrow[k] = 20
x2 = VS.pick_record_x(narrow, 1000)
ok("B3 右侧栏开着（圆圈漂到 0.745）也认得出来、也不会被窗内干扰簇（0.79）抢走",
   x2 is not None and abs(x2 - 0.745) < 0.01, str(x2))
ok("B4 空图谱 ⇒ None（交回配置值/兜底候选，不许瞎点）", VS.pick_record_x([0] * 1000, 1000) is None)
ok("B5 兜底候选全在右区（≥0.8）＝点空也只点到输入框/发送，不会弹窗",
   all(v >= 0.80 for v in VS.RECORD_FALLBACKS) and VS.RECORD_X_MAX <= 0.92)

print("── C. 窗口/点击链的源码级看守 ──")
_vs = src(os.path.join("agent", "voice_strip.py"))
ok("C1 进录音态＝**运行时扫图标行现算**（不写死比例，也不写死绝对坐标）",
   _sm.has(_vs, "def locate_record") and _sm.has(_vs, "def _icon_cols") and "pick_record_x" in _vs)
ok("C2 播的是虚拟声卡那一路（sounddevice RawOutputStream 到 out_device）",
   "sd.RawOutputStream" in _vs and "_out_device()" in _vs)
ok("C3 **只认 DB 回读**：等的是新出现的 `type=语音` 行",
   _sm.has(_vs, 'str(row.get("type") or "") == "语音"') and "local_id" in _vs)
ok("C4 这俩控件只认真实点击（投递只出悬停），且**用完把光标还原**",
   "click_real_hold" in _vs and "click_real_hold" in src(os.path.join("agent", "ui_adapt.py"))
   and "SetCursorPos" in src(os.path.join("agent", "ui_adapt.py")))
# ★ 口径更正（用户定性）：发语音条**只走独立窗**，**绝不切会话、绝不碰主窗**。
#   ⛔ 旧断言要的 `chat_is_open/switch_chat_posted`＝"当前会话不对就切过去"——那正是
#      "发一条语音条把用户主窗顶到前台"的**唯一真因**（`switch_chat_posted → _ensure_main_visible`，
#      调用栈实测钉死），已被删除。现在改成：**找不到独立窗就如实不发**。
ok("C5 发语音条**只走独立窗**：找不到独立窗如实不发，**绝不切会话/碰主窗**",
   "find_popup" in _vs and "没有独立窗" in _vs
   and "wechat.switch_chat_posted" not in _vs and "wechat.chat_is_open" not in _vs)
ok("C6 录音音量点一直不亮 ⇒ **当场取消**（不发静音语音条）",
   _sm.has(_vs, "def _meter_level") and "meter_guard" in _vs and "静音语音条" in _vs)
ok("C7 绿色发送取**最右**绿簇（左边那串音量点同样是绿的，别把均值带偏）",
   _sm.has(_vs, "def _green_send") and "GREEN_X_MIN" in _vs)
ok("C8 没进录音态时逐个候选位置重试，失败要如实说" ,
   _sm.has(_vs, "def _enter_record") and "没进录音态" in _vs)
# ★ 三态：抓不到帧 ⇒ `None`（判不了），**不许**折成 `False`（没进态）——两者结论不同：
#   前者只能说「看不见」，后者会被当成「Alt 没能进录音态」而去换位置/换档位。
from agent import chat_header as _CHD # noqa: E402
_keep_gr = _CHD.grab_render
try:
    _CHD.grab_render = lambda gui=None: None
    _gc_none = VS._green_cluster(None)
    def _boom(gui=None):
        raise RuntimeError("boom")
    _CHD.grab_render = _boom
    _gc_exc = VS._green_cluster(None)
finally:
    _CHD.grab_render = _keep_gr
ok("C8b `_green_cluster` 抓不到帧/量图异常 ⇒ **None（判不了）**，不许折成 False（没进态）",
   _gc_none is None and _gc_exc is None, "%r / %r" % (_gc_none, _gc_exc))
ok("C8c 调用方把「判不了」与「没进态」分开报（`blind` 计数 ＋ 「抓不到画面」文案）",
   _sm.has(_vs, "blind = 0") and "抓不到画面" in _vs and "blind_tail" in _vs)
# ★★ 「屏外显示」用完**必须还原**（独立窗被留在屏幕外过 ⇒ 从任务栏也拉不回来）。
#   两道锚：① 异常分支**不许**把 `_box` 置 None（那会让 finally 跳过还原）；
#          ② 还原之后必须**校验**"在屏内或已最小化"。
ok("C8d ★异常分支不许把 `_box` 置 None（置空＝finally 跳过还原＝窗口留在屏外）",
   (not _sm.has(_vs.split("_box.__enter__()")[1][:300], "_box = None\n")), "")
ok("C8e ★`_restore()` 还原后必须校验「回屏内或已最小化」"
   "（**屏外档**那个 `_restore`；透明档是另一个同名方法，它校验的是「透明位已清」）",
   "_rect_off_screen" in seg("_restore", ordinal=1), "")
ok("C8e2 透明档的 `_restore()` 也必须**校验透明位已清**（留着 ⇒ 用户的窗「一直看不见」）",
   "WS_EX_LAYERED" in seg("_restore") or "0x00080000" in seg("_restore"), "")
# 反向锚：**在切片里注入**违规（比"外部替换整串"稳 —— 后者一改文案就静默失效）
_h_vs, _t_vs = _vs.split("_box.__enter__()", 1)
_fake_vs = _h_vs + "_box.__enter__()" + "_box = None\n" + _t_vs
ok("C8f ★反向锚：把 `_box = None` 注入异常分支 ⇒ 同一套检测必须报红",
   _sm.has(_fake_vs.split("_box.__enter__()")[1][:300], "_box = None\n"), "")
ok("C9 被防打扰闸拦下时**等一等再试**（用户手一离开鼠标，下一枪就能中）",
   "手一离开鼠标我就重试" in _vs and "wait_s" in _vs)
_ua = src(os.path.join("agent", "ui_adapt.py"))
ok("C10 真点**开枪前再确认一次落点**（防「检查完→开枪」之间被控制台/浏览器抢走）",
   _ua.count("real_guard(sx, sy, gui=gui") >= 2 and "开枪前落点已经变了" in _ua)

print("── D. 形态选项（默认真语音条）＋ 接线 ──")
_cfg = src(os.path.join("agent", "config.py"))
ok("D1 配置里有 voice_reply.form（默认 strip）与 fallback_file",
   _sm.has(_cfg, '"form": "strip"') and _sm.has(_cfg, '"fallback_file": True'))
ok("D2 voice_strip 开关默认开（形态默认就是真语音条），且有 meter_guard/real_click 两个行为键",
   _sm.has(_cfg, '"voice_strip": {') and _sm.has(_cfg, '"meter_guard": True') and _sm.has(_cfg, '"real_click": True'))
ok("D3 兜底默认位置就是实测到的圆圈（0.878），不是空白处",
   "[0.878" in _cfg)
_tl = src(os.path.join("agent", "tools.py"))
ok("D4 工具按形态分流：strip ⇒ 走 voice_strip.send", "_vs.send(ctx.get(\"wechat\")" in _tl)
ok("D5 前提不齐时**如实回退并说明**（不许静默降级）",
   "_strip_note" in _tl and "为什么这次是文件" in _tl)
_ch = src(os.path.join("assets", "console", "index.html"))
ok("D6 控制台给出形态选项（真语音条 / 音频文件）",
   'data-cfg="voice_reply.form"' in _ch and "真语音条（微信语音气泡，推荐）" in _ch
   and "音频文件（点开才能听的那种）" in _ch)
ok("D7 控制台**能开关真语音条机制**并写明代价（右 Alt：不动鼠标、要微信在前台）",
   'data-cfg="voice_strip.enabled"' in _ch and "不会动你的鼠标" in _ch and "前台" in _ch)
ok("D9 默认走**纯投递路**（posted：不动鼠标 / 不借前台 / 不注入键盘），alt 与真点都留作可选档",
   _sm.has(_cfg, '"enter_via": "posted"') and _sm.has(_cfg, '"fallback_click": True')
   and "_enter_record_alt" in _vs and "_cancel_alt" in _vs and "_enter_record_posted" in _vs)
# ★ 反向锚：默认档在**三处**必须一致（后端默认 / 示例配置 / 源码兜底）——
#   改一处漏一处是这一类改动最容易犯的错，且症状是"用户看到 A、实际走 B"。
ok("D9b ★默认档三处一致：后端默认＝示例配置＝三处 `or ...` 兜底 ＝ 全 `posted`",
   _sm.has(_cfg, '"enter_via": "posted"') and _sm.has(src("config.example.json"), '"enter_via": "posted"')
   and _vs.count('get("enter_via") or "posted"') == 3 and (not _sm.has(_vs, 'or "alt"')))
# ★ 反向锚：投递档**不许**偷偷退回动鼠标的路 —— `fallback_click` 只对 alt 档生效。
#   （默认档从 alt 换成 posted 会**移除**一层兜底：投递算不出落点就如实失败，不改成动鼠标。）
ok("D9c ★`fallback_click` 只在 alt 档生效（投递档失败时不许偷换回动鼠标的真点路）",
   _sm.has(_vs, 'if want == "alt" and bool(c.get("fallback_click", True))'))
ok("C10 Alt 路的前置检查：注入用 SendInput + 扩展位，且**先确认抓得到帧**（防假阴性）",
   "SendInput" in _vs and "KEYEVENTF_EXTENDEDKEY" in _vs and "抓不到微信画面" in _vs)
_wv = _vs.split("def _wait_voice")[1][:4000]
# ★ 口径升级（固定间隔轮询太慢 ⇒ 改成"有写入就立刻确认"）：
#   老断言要的 `time.sleep(1.0)` 一秒一拍轮询**已被换成"事件 + 标量"**——
#     ① 优先走适配器 `wait_new_message`（`filewatch` 监听 `message_*.db-wal`，一有写入立刻查）；
#     ② 兜底也只查 `MAX(local_id)` **标量**、间隔 50ms（比原来 1s 快得多）。
ok("C11 回读＝**事件+标量**的快判据（不再 1s 一拍轮询；且不把「读失败」当「没发出去」）",
   "wait_new_message" in _wv and "_db_max_local_only" in _wv
   and "判据不可用" in _wv and "time.sleep(1.0)" not in _wv)
# 且适配器侧真有事件驱动实现（`filewatch` + `MAX(local_id)`）
_wx = src(os.path.join("agent", "wechat.py"))
ok("C11b 适配器侧有 `wait_new_message`：`filewatch` 盯 `-wal` + 只读 `MAX(local_id)` 标量",
   _sm.has(_wx, "def wait_new_message") and "filewatch" in _wx
   and _sm.has(_wx, "def _db_max_local_only") and "MAX(local_id)" in _wx)
ok("D8 控制台有「念法纠正」入口（多音字例外表）",
   'data-cfg="voice_reply.pronounce"' in _ch and "念法纠正" in _ch)
# ★ 分档口径（19:02 **最终定稿**）：**三档走同一条真语音条链**。
#   ⛔ 中间那版"最小化 ⇒ 改发语音文件兜底"的**前提已被实测证伪** —— 真语音条判据早就换成
#   **DB 回读**（`wait_new_message` / `MAX(local_id)` 增量），**不依赖画面** ⇒ 最小化档照样成立。
#   端到端实测（19:02）：`ok=True`、`filehelper` local_id 729 > 基线 728、
#   窗口零像素上屏、前台变化采样 0、跑后 `iconic=True`。见 `报告/调研/真语音条-最小化档查证.md`。
ok("D10 三档同链：**最小化档不再偷偷改发语音文件**（判据是 DB 回读、不依赖画面）",
   _sm.has(_tl, "def _voice_win_minimized") and "_auto_file" not in _tl
   and "三档（前台/后台/最小化）都走同一条真语音条链" in _tl)
ok("D11 只读观测用的判定**拿不到就按未最小化处理**（判不了不当作'是'，不把用户挡在门外）",
   _sm.has(_tl.split("def _voice_win_minimized")[1][:900], "return False, \"\""))
ok("D12 真发不出去时**如实说原因**、回退也要**明示**（不许静默降级成文件）",
   "真语音条这次没发成" in _tl and "fallback_file" in _tl)
# ★ 投递路（实测新增）：enter_via=posted ⇒ 纯投递点击，不动光标/不借前台/不注入键盘
ok("D13 投递路存在：`_enter_record_posted` 走**投递点击**（不是真鼠标、不是 SendInput）",
   _sm.has(_vs, "def _enter_record_posted") and _sm.has(_vs, "def _posted_click")
   and "_posted_click(" in _vs)
ok("D14 投递点击**自己拼客户区坐标**（最小化档屏幕坐标算不出来，不能走 ScreenToClient）",
   _sm.has(_vs, "def _posted_click") and "pack_lparam" in _vs and "WM_LBUTTONDOWN" in _vs
   and "客户区坐标" in _vs)
# ⚠️ 断言只认**代码行**（剥掉 `#` 注释）：注释里会**引用**旧写法做对比说明，
#    拿整份源码做子串匹配会把「叙述」当成「违规」（本项目踩过两次）。
_vs_code = "\n".join(l.split("#")[0] for l in _vs.splitlines())
ok("D15 投递路**不借前台**（只有 alt/真点才借；投递档只**读**一次当前前台，供收尾还回去）+ "
   "配置里登记了 posted 选项与 toggle 比例",
   _sm.has(_vs, "stashed_fg = (_fg_current() if _posted_mode else _borrow_foreground(gui))")
   and (not _sm.has(_vs_code, "stashed_fg = 0"))
   and _sm.has(_cfg, "posted ＝") and "toggle_x" in _cfg)
# ★ 口径更正：投递档**一律不走音量点闸** —— 量音量点必须抓**主窗**帧，
#   而抓主窗帧会把主窗摆回屏幕内 ⇒ 违反判据①「不影响用户的观看」；且录音态音量点属于独立窗，
#   主窗帧里根本没有。⇒ 投递档 `_meter_usable=False`，闸门短路（`and _meter_usable`），
#   成败一律交给 **DB 回读**（与发文件/图片同一口径：不靠画面下死结论）。
ok("D16 投递档**一律不走音量点闸**（量它要抓主窗帧 ⇒ 违反「不影响观看」）",
   _sm.has(_vs, "_meter_usable = False")
   and _sm.has(_vs, "and _meter_usable and peak <= base")
   and "量它必须抓主窗帧" in _vs)
# ★ 口径更正：投递进态**三态**——帧特征确认 True / 抓到帧但无特征 False /
#   **抓不到帧 None（最小化档）⇒ 不判断，以 DB 回读为准**（别把"测不了"混进"不是"）。
#   ⛔ 断言**锚行为**：`hit is True` 才作正向证据；**只有 `hit is True` 那一条能 `return` 失败以外的分支**，
#     其余一律 `return True, ...`（即 False/None 都**不判失败**）⇒ 交给 DB 回读。
#   ⚠️ `hit is False` **会**出现在实现里（`"未确认" if hit is False else ...` 只影响**文案**，不影响成败），
#     所以不能把"出现 hit is False"当违规 —— 要找的是"有没有拿它 return 失败"。
_d17_code = "\n".join(_l.split("#")[0] for _l in
                      _vs.split("def _enter_record_posted")[1].split("\ndef ")[0].splitlines())
ok("D17 投递进态**三态**：`True` 才作正向证据；`False`/`None` 一律**不判失败**（交给 DB 回读）",
   _sm.has(_vs, "def _posted_frame_shows_record")
   and _sm.has(_d17_code, "hit is True")
   and _d17_code.count("return True, ") >= 2          # True 分支 + 兜底分支
   and (not _sm.has(_d17_code.split("hit = _posted_frame_shows_record")[-1], "return False"))
   and _sm.has(_vs.split("def _posted_frame_shows_record")[1][:2000], "return None"))
# ★ 口径**反转**（复核后）：落点**优先"当场量"** —— 抓帧按帧特征找元素
#   （`_popup_find_toggle`），抓不到帧才退回"距锚点边的相对距离 × 当前缩放"。
#   理由：位置由**窗口自身画面**决定 ⇒ 换窗口尺寸 / DPI / 微信版本都**不用改代码**；
#   而任何写死的距离都只对一台机器成立。
#   ⛔ 兜底**不许**用"占窗口宽的比例"：实测左排图标贴左缘定距、右排图标贴右缘定距
#     （左排第 1 个图标在 919 帧与 1374 帧上中心 x 都是 55.5）⇒ 比例会随窗口拉宽而漂。
ok("D19 落点**优先当场量**（抓帧按帧特征找），抓不到帧才退回「距右缘 × 缩放」兜底算式",
   _sm.has(_vs, "def _popup_find_toggle") and _sm.has(_vs, "def _frame_icon_row")
   and _sm.has(_vs, "def _frame_to_client")
   and _sm.has(_vs, "feat = _popup_find_toggle(tgt)")
   and "_px_to_toggle" in _vs and "TOGGLE_FROM_RIGHT_PX" in _vs
   # 反向锚：兜底必须"距右缘逻辑像素 × 缩放"，且逻辑像素走 `_from_right_lp`（实测优先）
   and "_from_right_lp(" in _vs and _sm.has(_vs, "_lp * _sc"))
# ⭐⭐ 落点也要"**实测 → 记住 → 之后读记下的**"（与缩放倍率/窗口边框同一口径）：
#   抓帧量到元素后，把"该元素距客户区右缘的逻辑像素"记进 `display_metrics` 档案；
#   抓不到帧时**优先用记下的本机实测值**，出厂默认只保证"第一次能用"。
ok("D22 落点同样「实测→记住→优先用记下的」：抓帧量到就记档案，兜底读档案优先于出厂默认",
   _sm.has(_vs, "def _remember_anchor_from_client") and _sm.has(_vs, "def _from_right_lp")
   and "_dm.remember_anchor(" in _vs and "_dm.anchor(" in _vs
   and _sm.has(src("agent/display_metrics.py"), "def remember_anchor")
   and _sm.has(src("agent/display_metrics.py"), "def anchor"))
# ⭐⭐ **开机实测**：落点也要在启动链上量一遍，并留下"逐项来源"的台账
#   —— 任何一项"没对齐"都会让点击偏，所以要么当场量到、要么如实标注是读档案还是出厂兜底。
ok("D23 ★ 落点接进**启动链**实测：`measure_landing_points` 存在 + 启动链调它（带 try 罩）",
   _sm.has(_vs, "def measure_landing_points")
   and "measure_landing_points(" in src("scripts/persona_morph.py")
   and "落点实测跳过" in src("scripts/persona_morph.py"))
ok("D24 ★ **开机自检台账**：`display_metrics.boot_check` 逐项记来源，退到兜底的项要告警",
   _sm.has(src("agent/display_metrics.py"), "def boot_check")
   and "boot_check(" in src("agent/display_metrics.py")
   and "没对齐" in src("agent/display_metrics.py"))
ok("D25 ★ 反向锚：抓帧量到的落点必须过**合理性闸门**（0.3~3.0 倍），防一次误抓冲掉正确值",
   _sm.has(_vs, "0.3 * base <= lp <= 3.0 * base"))
ok("D26 ★ 只对**独立窗**量落点（主窗几何不同 ⇒ 量了会污染）",
   "只对独立窗量" in _vs)
# ⭐⭐ **第二道保险：图标相似度（模板匹配）**（本项目落点的两条标准之一）
#   落点是**算出来的**（相对距离）⇒ 万一某台机器上算偏了就会点到别的东西。
#   ⇒ **点之前先拿本机记住的图标模板比对那一块**；不像 ⇒ **不点**（宁可不发，也不点错）。
#   ⛔ 为什么不用"特征阈值"（深色占比/长宽比/中心亮度）：**实测分不开** ——
#     同一条输入条上有好几个"深色描边圆"，邻居图标全都合格（8 个错位置里 6 个判"像"），
#     而真位置反而有 2 个被判"不像" ⇒ 既挡不住点错、又会挡住正常发送。
ok("D27 ★★ 落点前先过**图标相似度（模板匹配）**闸门，不像就**不点**（fail-closed）",
   _sm.has(_vs, "def _icon_match") and _sm.has(_vs, "def _icon_learn")
   and _sm.has(_vs, '_icon_match(img, int(_m), int(fy), "toggle", half=_half)')
   and _sm.has(_vs, '_icon_match(img, _kx, _ky, _kd, half=_half)')
   and _sm.has(_vs, "for _cand in _order:")
   and "_inband[:-1]" in _vs
   and _sm.has(_vs, "elif _isc < _ICON_MATCH_MIN:"))
ok("D28 ★ 相似度**全用相对量**：取样半径＝帧短边×比例、阈值有探针标定来源（不写死像素）",
   _sm.has(_vs, "_ICON_HALF_REL = 0.022") and _sm.has(_vs, "_ICON_MATCH_MIN = 0.82")
   and "measure_icon_gate.py" in _vs and "measure_scale_invariance.py" in _vs)
ok("D28b ★ 首次定位**把模板记下来**（没记过不否决这一枪，否则第一次永远发不出）",
   _sm.has(_vs, "if _isc is None:") and "_icon_learn(" in _vs
   and "本机还没记过这个图标的模板" in _vs)
try:
    from PIL import Image as _PI, ImageDraw as _PD
    # ⛔ 合成图的**图标相对尺寸要与真机一致**（真机里那个圆约占帧短边的 4.4%）——
    #    造大了的话取样半径根本盖不住图标，量出来的分数没有意义（踩过）。
    _a = _PI.new("RGB", (400, 400), (255, 255, 255))
    _d = _PD.Draw(_a)
    _d.ellipse((191, 191, 209, 209), fill=(21, 172, 112))
    _d.ellipse((197, 195, 203, 201), fill=(255, 255, 255))     # 那个白箭头的位置
    _half = VS._icon_half(_a)
    _tpl = VS._icon_patch(_a, 200, 200, _half)
    _s_same, _o1, _w1 = VS._icon_match(_a, 200, 200, "green", half=_half, tpl=_tpl)
    # ② 换一块**不一样**的图案（纯白）⇒ 相似度必须低
    _b = _PI.new("RGB", (400, 400), (255, 255, 255))
    _s_diff, _o2, _w2 = VS._icon_match(_b, 200, 200, "green", half=_half, tpl=_tpl)
    ok("D29 ★行为锚：同图案相似度≈1（%.3f）｜换图案明显低（%.3f）⇒ 判据有灵敏度"
       % (_s_same or 0, _s_diff or 0),
       (_s_same or 0) >= VS._ICON_MATCH_MIN and (_s_diff or 0) < VS._ICON_MATCH_MIN,
       "同=%r 异=%r" % (_s_same, _s_diff))
    # ③ 跨尺寸：同一图案放大 1.5× 后仍要认得出（换 DPI 就靠这条）
    _c = _a.resize((600, 600), _PI.LANCZOS)
    _s_sc, _o3, _w3 = VS._icon_match(_c, 300, 300, "green", half=VS._icon_half(_c), tpl=_tpl)
    ok("D29b ★跨尺寸锚：同图案放大 1.5× 仍认得出（%.3f）—— 换 DPI 靠这条" % (_s_sc or 0),
       (_s_sc or 0) >= VS._ICON_MATCH_MIN, "放大后=%r" % (_s_sc,))
    # ④ 三态：**没记过模板** ⇒ `None`（判不了 ≠ 不像）
    _s_none, _o4, _w4 = VS._icon_match(_a, 200, 200, "从未记过的图标", half=_half)
    ok("D30 ★三态：没记过模板 ⇒ `None`（判不了 ≠ 不像，不许据此否决）",
       _s_none is None and bool(_w4) and "没记过" in str(_w4), repr((_s_none, _w4)))
except Exception as _e:
    ok("D29/D30 图标相似度的行为锚（构造失败 ⇒ 视为未测）", False, str(_e)[:70])
ok("D20 帧特征落点**从帧内比例算**并换算成客户区（边框当场量，不写死 11）",
   _sm.has(_vs, "def _frame_to_client") and "GetClientRect" in _vs.split("def _frame_to_client")[1][:1200])
# ★★ 落点口径（复核后重定）：**记"相对位置"，不记"坐标"**。
#
#   ⛔ 复核发现的错误（留痕，防后人重踩）：
#     原来三个"距右缘"常量 (`CANCEL_FROM_RIGHT_PX`=654.7 / `TOGGLE_FROM_RIGHT_PX`=100 /
#     `SEND_GREEN_FROM_RIGHT_PX`=46) 的值都不可用：
#       · ✕    实测 371.5（919 宽帧）  ← 代码 654.7（从"客户区宽 2382"折出，**两套基准混用**）
#       · 绿↑   实测 68.5（919 宽帧）   ← 代码 46（多折了一次 ÷1.5，无意义的换算）
#       · 语音键 代码 100（≈149.5 物理 @150%）—— **数值本来是对的**；
#                但复核时一度被改成 627，那量的是**左排的麦克风图标**、
#                不是右排的语音切换键（右排最右的真图标实测距右缘 149.5）。
#     ⇒ 教训两条：① **基准必须写清**（帧宽还是客户区宽？物理还是逻辑像素？）；
#                 ② **量之前先确认"量的是哪个元素"**（同一行里有两个麦克风形状的键）。
#
#   ⭐ 正确口径：**优先当场量**（抓帧按颜色 / 图标簇找元素，位置由画面自身决定）；
#     抓不到帧才用"与锚定边的相对距离 × 当前缩放"兜底，常量集中在 `_FROM_RIGHT_LP`。
ok("D18 投递路的 ✕/绿↑ 落点算得出（取消/发送不依赖抓帧）",
   _sm.has(_vs, "def _posted_cancel") and _sm.has(_vs, "def _posted_send")
   and "POSTED_CANCEL_X" not in _vs and "POSTED_SEND_X" not in _vs)
# ⭐ 真点/alt 档的 ✕：**改为"由当场量到的绿↑ 推"**（`_cancel_point_live` → `_cancel_from_green`）。
ok("D21 真点/alt 档的 ✕ 改成「由当场量到的绿↑ 推」（只写关系、不写坐标）",
   _sm.has(_vs, "def _cancel_point_live") and _sm.has(_vs, "def _cancel_from_green")
   and "_green_send(gui)" in _vs)
ok("D21a ★ 反向锚：作废的写死常量**不许被当成落点用**（`_cancel_point` 直接 raise）",
   _sm.has(_vs, "def _cancel_point(") and _sm.has(seg("_cancel_point", cap=900), "raise NotImplementedError")
   and "_cancel_point(rw" not in _vs)
ok("D21b ★ 反向锚：✕ 不许再各写一个「距右缘多少像素」的绝对常量（那还是坐标）",
   not __import__("re").search(r"^CANCEL_FROM_RIGHT_PX\s*=\s*[\d.]", _vs, __import__("re").M))
ok("D21c ★ 交点必须**当场量**：`_green_send` 抓的是独立窗渲染区 ⇒ 不碰主窗（判据①）",
   "ch.grab_render(gui)" in _vs)
ok("D21d ★ 算出的 ✕ 必过**越界守卫**（不在绿↑ 左边、或出窗口 ⇒ 返回 None，宁可不点）",
   _sm.has(_vs, "if x < 1 or x >= int(green_x)"))

print("── F. 最小化档「屏外显示」（判据①②的守门）──")
# ⭐ 用户定稿判据：① 不影响观看（零像素上屏）；② 不影响操作（不抢前台/焦点/光标）。
#   ⇒ 最小化档的唯一手段＝`_OffscreenShown`：把窗口**移到屏幕外**再显示（用户看不到、不抢前台），
#     用完**精确还原**（原位置 + 原显示态）。
_off = _vs.split("class _OffscreenShown")[1].split("def _get_window_placement")[0]
ok("F1 屏外显示：移到位（rect 与屏幕**无交集**）并**自检**，自检不过就还原且不投递",
   _sm.has(_vs, "def _rect_off_screen") and _sm.has(_vs, "class _OffscreenShown")
   and _sm.has(_vs, "与屏幕 %dx%d 有交集") and _sm.has(_off, "self.shown = True"))
# ⭐⭐ 顺序铁律（实测钉死）：进＝**先 ShowWindow 后 SetWindowPos、中间不 sleep**。
#   反了就会露（最小化时 SetWindowPos 只改 rcNormalPosition）；中间 sleep 也会露（窗口真画出来）。
_enter_off = _off.split("def _restore")[0]
_i_show = _enter_off.find("u.ShowWindow")
_i_pos = _enter_off.find("u.SetWindowPos")
ok("F2 进屏外的**顺序铁律**：`ShowWindow` 后**紧邻** `SetWindowPos`，**中间不 sleep**（反了/插 sleep 都会露）",
   _i_show > 0 and _i_pos > _i_show
   and "time.sleep" not in _enter_off[_i_show:_i_pos])
# ⭐ 还原顺序与进入**相反**：最小化档先"收成最小化"再 `SetWindowPos` 回原位（中间也不 sleep）。
# ⛔⛔ 收窗口的常量**必须**是 `_SW_SHOWMINNOACTIVE`(7)：`SW_MINIMIZE`(6) 的文档语义是
#   「最小化**并激活 Z 序里的下一个顶层窗**」，而独立窗的 Z 序邻居正是它的 owner ＝**微信主窗**
#   ⇒ 收一次独立窗就把**用户的主窗顶到前台一次**。⇒ 这一条同时做**反向锚**（不许再出现 6）。
_restore_off = _off.split("def _restore")[1]
ok("F3 还原**精确**且**顺序与进入相反**（最小化档先收成最小化再移回原位；最大化档先移后显）",
   "_SW_SHOWMINNOACTIVE" in _restore_off and "_SW_SHOWMAXIMIZED" in _restore_off
   and _restore_off.find("_SW_SHOWMINNOACTIVE") < _restore_off.find("int(self._nr[0])")
   and "rcNormalPosition" in _off)
ok("F3b ⛔ **收窗口不许用 `SW_MINIMIZE`/常量 6**（它会把用户的主窗顶到前台）—— 全模块反向锚",
   "_SW_MINIMIZE" not in _vs and "ShowWindow" in _restore_off
   and (not _sm.has(_restore_off, ", 6)")) and (not _sm.has(_enter_off, ", 6)")))
ok("F4 屏外显示**只在最小化档**用（可见档本就能投递 ⇒ 不动窗口）+ 无论成败都 `finally` 还原",
   "IsIconic" in _vs.split("_box = _InvisibleShown")[0].split("if _posted_mode")[-1]
   and _sm.has(_vs, "_box.__exit__(None, None, None)"))
# ⭐⭐ **全透明档是主路、屏外档是兜底**（实测对比）：移屏外显示时，微信自己那一下激活会落到
#   **它的 owner＝微信主窗**上（主窗 7 秒在最前）；换成"显示在原位 + alpha=0"后，
#   被激活的是**那个看不见的独立窗** ⇒ 主窗全程没到前台，而点击照样生效。
ok("F9 ⭐ **全透明档是主路**：`_InvisibleShown` 先试、`_OffscreenShown` 只作兜底",
   _sm.has(_vs, "_box = _InvisibleShown(") and _sm.has(_vs, "_box = _OffscreenShown(")
   and _vs.find("_box = _InvisibleShown(") < _vs.find("_box = _OffscreenShown("))
_INV = _vs.split("class _InvisibleShown")[1].split("class _OffscreenShown")[0]
ok("F9a 透明档**进入顺序**：先 `set_popup_invisible(True)` 再 `ShowWindow`（反了会露一帧）",
   _INV.find("set_popup_invisible(") < _INV.find("ShowWindow("))
ok("F9b 透明档**还原顺序与进入相反**：先收成最小化、再去透明位（反了会在屏上闪一下）",
   _INV.find("_SW_SHOWMINNOACTIVE", _INV.find("def _restore")) < _INV.rfind("set_popup_invisible("))
ok("F9c 透明档**自检**：读回 `GetLayeredWindowAttributes` 的 alpha，!=0 就不显示（交给兜底）",
   "GetLayeredWindowAttributes" in _INV and "透明自检不过" in _INV)
ok("F9d ⛔ 透明位**必须还原干净**（留着 ⇒ 用户的独立窗「一直看不见」，比留在屏外更难发现）",
   "0x00080000" in _INV and _sm.has(_INV, "LAYERED 仍在"))
# ⭐ 模块级日志器：`_popup_find_toggle` 的"模板过期⇒重学"与兜底还原都写 `log.warning`；
#   模块级没有 `log` 时那两处抛 `NameError`，各自被宽 `except` 吞掉 ⇒ **帧路静默失效**。
ok("F10 ⛔ 模块级 `log` **必须存在**（否则帧路自愈/兜底还原会因 `NameError` 被静默吞掉）",
   _sm.has(_vs, "log = logging.getLogger(") and _vs.count("log.warning(") >= 2)
ok("F10a 投递点击后**盯一小会儿把前台还回去**（微信处理投递消息是异步的，点完只查一次必漏）",
   "_fg_watch_back(_fg0)" in _vs and _sm.has(_vs, "def _fg_watch_back("))
# ⭐⭐ 投递档**完全绕开主窗 gui**（实测钉死）：`gui._update_render_rect()` 会把
#   主窗摆回屏幕内（实测 rect 从 -32000 变 (175,224,1549,1320) 上屏一瞬）⇒ 违反判据①。
ok("F5 投递档**不碰主窗 gui**（`_update_render_rect()` 会把主窗摆回屏幕内 ⇒ 违反「不影响观看」）",
   _sm.has(_vs, "gui = None")
   and "_posted_gate" in _vs
   and "投递档**不碰主窗**" in _vs
   # 投递分支体（到 `else:` 为止）里**不含** `_update_render_rect`（那是非投递档才走）
   and "_update_render_rect" not in _vs.split("if _posted_gate:")[1].split("else:")[0])
# ⭐ 投递档**不借前台**（借前台＝抢用户窗口一下）——只**读一次**当前前台，收尾仍走 `_give_back`。
ok("F6 投递档**不借前台**（只读一次当前前台）；收尾仍走 `_give_back(stashed_fg)`",
   _sm.has(_vs, "stashed_fg = (_fg_current() if _posted_mode else _borrow_foreground(gui))")
   and "_give_back(stashed_fg)" in _vs)
# ⭐⭐ 收尾**顺序**：先 `_box.__exit__`（收独立窗）再 `_give_back`（还前台）。
#   反过来的话，"收窗口"那一下可能又把激活交给主窗 ⇒ 刚还的前台立刻被顶掉（＝还了等于没还）。
_fin = _vs.split("finally:")[-1][:700]
ok("F6b ⭐ **收尾顺序**：先收窗口、再还前台（反了会被「收窗口」那一下再顶掉）",
   "_box.__exit__" in _fin and "_give_back(stashed_fg)" in _fin
   and _fin.find("_box.__exit__") < _fin.find("_give_back(stashed_fg)"))
ok("F6c 读前台**只走唯一实现点**（本模块不许自己拼 `ctypes...GetForegroundWindow`，"
   "那个写法默认按 32 位收句柄 ⇒ 实测会给 0 ⇒ 记下 0 ＝ 还无可还）",
   _sm.has(_vs, "def _fg_current(")
   and "_fg_now()" in _re.sub(r'""".*?"""', "", seg("_fg_current", cap=600), flags=_re.S)
   and "GetForegroundWindow" not in _re.sub(r'""".*?"""', "", seg("_fg_current", cap=600),
                                            flags=_re.S))
# ⭐ 投递档**找不到独立窗就如实不发**，绝不退回主窗（宁可不发，不动用户的窗）
ok("F7 投递档**只认独立窗**（拿不到＝如实不发），**绝不退回主窗**",
   _sm.has(_vs, "def _posted_target_hwnd") and "不退回主窗" in _vs
   and _sm.has(_vs.split("def _posted_target_hwnd")[1][:1400], "return 0"))
# ⭐ 老 `calibrate()` 已**弃用且上锁**：它会把主窗摆回屏幕内 + 借前台（违反判据①②）；
#   落点改成"距右缘像素"算式后**根本不需要标定** ⇒ 函数体改成直接 `raise`（防误接）。
ok("F8 老 `calibrate()` **已弃用并上锁**（含违规动作：摆主窗上屏 + 借前台；改为直接 raise）",
   _sm.has(_vs, "calibrate() 已弃用") and "不再需要标定" in _vs
   and _sm.has(_vs, "def _calibrate_legacy")
   and _sm.has(seg("calibrate", cap=1600), "raise RuntimeError(")
   and "违反判据①②" in _vs)

print("── E. 依赖 ──")
ok("E1 requirements 里有 sounddevice（装依赖时装得上）", "sounddevice" in src("requirements.txt"))
# ⚠️ E2 **不是功能判据**：`sounddevice` 只在**系统 python（`py`）**里装，
#   托管 python 没有 ⇒ "当前解释器 import 不到"只说明**换了个解释器跑**，不代表产品不能用。
#   ⇒ 本机装了 ⇒ 报 OK；没装 ⇒ 报**提示**（带安装命令），**不判失败**（不计入 FAIL）。
try:
    import sounddevice # noqa: F401
    ok("E2 本机（当前解释器）装了 sounddevice", True)
except Exception as e:
    print("  --   E2 环境提示：当前解释器没装 sounddevice（%s）" % e)
    print("       真语音条要发声必须装：%s -m pip install sounddevice" % (sys.executable or "py"))
    print("       ⚠️ 演示/实测请用**系统 python**：py scripts/voice_strip_selftest.py（托管 python 里没这个包）")

print("\n── F. 「设备找不到」时要说**人话**：把本机能用的设备列出来 + 两条出路 ──")
# 现场：跨机实测里对面那台**没有 CABLE 虚拟声卡**，配置钉着 cable ⇒ 必然失败；
#   报错原来只有"配置里指定的输出设备没找到：cable" ⇒ 使用者只能自己猜下一步。
#   这条判据钉住：报错必须能**自解释**（列出可用设备 ＋ 给出去路）。
import types as _types  # noqa: E402


def _fake_sd(names):
    m = _types.ModuleType("sounddevice")
    m.query_devices = lambda: [{"name": n, "max_output_channels": 2,
                                "max_input_channels": 0} for n in names]
    return m


_oSd = sys.modules.get("sounddevice")
_oSdFlag = ("sounddevice" in sys.modules)
try:
    # ① 这台机**没有**虚拟声卡（名字里没有 cable/scream），而配置钉着 cable ⇒ 必然找不到
    sys.modules["sounddevice"] = _fake_sd(["扬声器 (HECATE G2无线版)", "耳机 (Realtek Audio)"])
    VS._cfg = lambda cfg=None: {"out_device": "cable"}
    _i, _n, _why = VS._out_device()
    ok("F1 钉的设备找不到时，**列出本机能用的输出设备**（别再让人猜）",
       _i == -1 and "本机能用的输出设备有" in _why and "HECATE" in _why, _why[:110])
    ok("F2 报错里给出**两条出路**（装虚拟声卡 / 暂时指真实扬声器并说明会外放）",
       "VB-Cable" in _why and "外放" in _why, _why[:110])
    # ② 没钉设备、这台机也没有虚拟声卡 ⇒ 那一档也要把可用设备列出来
    VS._cfg = lambda cfg=None: {"out_device": ""}
    _i2b, _n2b, _why2b = VS._out_device()
    ok("F2b 没有虚拟声卡那一档同样要列出可用设备（两档都不能只丢一句「没找到」）",
       _i2b == -1 and "本机能用的输出设备有" in _why2b and "HECATE" in _why2b, _why2b[:110])
    # ③ 反过来：这台机**有** CABLE ⇒ 不钉设备时要按名字认出它（判据不是恒否）
    sys.modules["sounddevice"] = _fake_sd(["扬声器 (HECATE G2无线版)", "CABLE Input (VB-Audio)"])
    _i3, _n3, _why3 = VS._out_device()
    ok("F3 反向控制：不钉设备时按**名字**挑虚拟声卡（名字含 CABLE 的那块能认出来）",
       _i3 == 1 and "CABLE" in _n3 and not _why3, "%s/%s" % (_i3, _n3))
finally:
    if _oSdFlag and _oSd is not None:
        sys.modules["sounddevice"] = _oSd
    else:
        sys.modules.pop("sounddevice", None)
    try:
        import importlib as _il
        _il.reload(VS)
    except Exception:
        pass

print("\n== 真语音条判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
