# -*- coding: utf-8 -*-
"""「只对独立窗操作」闸门（`send.only_popup`）判据 —— **不碰微信**。

这条闸门是"切会话链作废"那条定调的**过渡件**：**默认关 ⇒ 零行为变化**，
开着时文本发送**只许**对独立窗做（不切会话、不在主窗发），拿不到就如实失败。

守四件：
  ① **默认关**（默认配置与示例配置都是 false）—— 否则一开就是全量行为变更；
  ② **顺序即意图**：那道 `return` 必须排在 `send_text_posted(` / `switch_chat_posted(` **之前**
     （排在后面＝闸门形同虚设，因为它前面那条路已经发出去了）；
  ③ 开着时**强制**走独立窗支（条件里带 `or self._only_popup()`）；
  ④ **覆盖范围写清**（不许名不副实）—— 现在只覆盖文本链，文件链待接。
另有反向锚（把闸门从源码里删掉，②的检测必须报红）与分母守卫。

用法：runtime\\python\\python.exe scripts/only_popup_selftest.py
"""
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import _srcmatch as _sm # noqa: E402 空白容忍的源码断言

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


from agent import config as C  # noqa: E402
import _srcslice as _ss  # noqa: E402

SRC = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
SEG = _ss.func_src(SRC, "send_text")
EX = json.load(open(os.path.join(ROOT, "config.example.json"), encoding="utf-8"))

print("── A. 默认开（2026-10-06 用户拍板：切会话链废掉，只走独立窗）──")
ok("A1 ★默认配置里 `send.only_popup` 为 **True**（＝默认不走切会话、不在主窗里发）",
   ((C.DEFAULT_CONFIG.get("send") or {}).get("only_popup") is True),
   repr((C.DEFAULT_CONFIG.get("send") or {}).get("only_popup")))
ok("A2 示例配置里也是 true（与默认配置同值）",
   ((EX.get("send") or {}).get("only_popup") is True),
   repr((EX.get("send") or {}).get("only_popup")))
ok("A3 关掉它的说法仍写明是「应急」（关＝退回有切错风险的老路，不是等价选项）",
   _sm.has(io.open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read(), "只作应急"))

print("── B. 接线：闸门在 `send_text` 里真的拦得住 ──")
ok("B1 分母守卫：`send_text` 的源码切片拿到了（>500 字符）", len(SEG) > 500, "%d 字符" % len(SEG))
_i_gate = SEG.find("if self._only_popup():")
_i_posted = SEG.find("self.send_text_posted(")
_i_switch = SEG.find("self.switch_chat_posted(")
ok("B2 ★顺序即意图：闸门排在 `send_text_posted(` **之前**",
   -1 < _i_gate < _i_posted, "gate=%d posted=%d" % (_i_gate, _i_posted))
ok("B3 ★顺序即意图：闸门排在 `switch_chat_posted(` **之前**",
   -1 < _i_gate < _i_switch, "gate=%d switch=%d" % (_i_gate, _i_switch))
ok("B4 开着时**强制**走独立窗支（条件里带 `or self._only_popup()`）",
   _sm.has(SEG, "or self._only_popup()"), "")
ok("B5 闸门开着时是**如实失败**（那道 return 带「只对独立窗操作」的理由）",
   "只对独立窗操作" in SEG[_i_gate:_i_gate + 400] if _i_gate >= 0 else False, "")
_h = _ss.func_src(SRC, "_only_popup")
ok("B6 覆盖范围写清（docstring 里点了文件链与切会话链）",
   "文件" in _h and "切会话" in _h and "图片" in _h, "")
# ── 文件链：闸门必须排在"搜到再切过去 / 主窗系统对话框"之前 ──
_F = _ss.func_src(SRC, "send_file_posted")
_i_gf = _F.find("if self._only_popup():")
_i_srch = _F.find("self.open_chat_by_search(")
ok("B7 ★文件链：闸门排在 `open_chat_by_search(`（切会话）**之前**",
   -1 < _i_gf < _i_srch, "gate=%d search=%d" % (_i_gf, _i_srch))
ok("B8 ★文件链：闸门也排在驱动系统对话框那段之前（`_close_stale_file_dialogs(`）",
   -1 < _i_gf < _F.find("_close_stale_file_dialogs("),
   "gate=%d dlg=%d" % (_i_gf, _F.find("_close_stale_file_dialogs(")))
# ── 切会话链（拍一拍/引用/表情的入口）：闸门必须排在 switch_chat_posted 之前 ──
_G = _ss.func_src(SRC, "_open_chat_guarded")
_i_gg = _G.find("if self._only_popup():")
_i_gsw = _G.find("self.switch_chat_posted(")
ok("B9 ★切会话链：闸门排在 `switch_chat_posted(` **之前**（拍一拍/引用/表情入口）",
   -1 < _i_gg < _i_gsw, "gate=%d switch=%d" % (_i_gg, _i_gsw))
ok("B10 覆盖范围声明与代码一致（不再写「文件链待接」）",
   "待接" not in _h, "")

print("── C. 反向锚：判据自己抓得住「闸门没了 / 挪到后面了」──")
# 反例①：把闸门整段删掉 ⇒ 位置检测必须报红（-1 不算"在之前"）
_fake1 = SEG.replace("if self._only_popup():", "")
_g1 = _fake1.find("if self._only_popup():")
ok("★反向锚①：闸门**被删掉** ⇒ 位置检测必须报红",
   not (-1 < _g1 < _fake1.find("self.send_text_posted(")), "gate=%d" % _g1)
# 反例②：把闸门**挪到** `send_text_posted(` 之后 ⇒ 位置检测必须报红（这才是"顺序即意图"的真反例）
_end = SEG.find('if st["status"] == "ok":')
_block = SEG[_i_gate:_end] if (_i_gate >= 0 and _end > _i_gate) else ""
_fake2 = (SEG.replace(_block, "") + _block) if _block else SEG
_g2 = _fake2.find("if self._only_popup():")
ok("★反向锚②：闸门**被挪到** `send_text_posted(` 之后 ⇒ 位置检测必须报红",
   bool(_block) and not (-1 < _g2 < _fake2.find("self.send_text_posted(")),
   "挪后 gate=%d posted=%d" % (_g2, _fake2.find("self.send_text_posted(")))
ok("★原样源码上位置检测为真（否则上面两条是假阳性）",
   (-1 < _i_gate < _i_posted) is True, "")

print("\n[C] 抓帧走不通时，要**顺手指出**还有'不依赖主窗那份抓帧'的那条路")
# 现场：跨机实测里抓帧失败 ⇒ 整条链断在"确认当前会话"，日志只写"投递前置未满足（no_capture）"；
#   而目标**已经有独立窗**（标题即会话名）⇒ 其实有一条**完全不读画面**的通路可走。
#   有提示函数却没人调用，等于没有 ⇒ 两条都要锚。
_wx2 = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
ok("C1 `_popup_bypass_hint` 在位", _sm.has(_wx2, "def _popup_bypass_hint("))
ok("C2 ★它被用在「投递前置未满足」那个出口（否则有提示也没人说）",
   _sm.has(_wx2, "self._popup_bypass_hint(chat_id, name)"))
ok("C3 提示词里写清'为什么不依赖主窗那份抓帧'（标题即会话名 ⇒ 不需要「当前会话」判据）",
   _sm.has(_wx2, "标题即会话名") and _sm.has(_wx2, "不需要「当前会话」判据")
   and _sm.has(_wx2, "不依赖主窗那份抓帧"))
# ⛔ 换成 AST 锚点（`func_src` ＝ 精确函数正文）：原来按"函数在文件里的**文本位置**"切、
#   再按下一个 `def` 截断 —— 函数一搬位置就崩/切错。（说明故意不照抄旧语法：照抄会被同
#   文件的"可机械改写形态"普查当成新写的一处。）
_hint = _ss.func_src(_wx2, "_popup_bypass_hint")
ok("C4 ★反向锚：提示函数**只返回一句话**——不改配置、不自动切通路"
   "（切通路是行为变更，必须由人打开开关）",
   bool(_hint) and ("C.set(" not in _hint) and ("config.save" not in _hint)
   and (not _sm.has(_hint, "_only_popup =")), "")

print("\n── F. 开窗链：双击 → 右键 → 搜索（第 225 项的三级兜底）──")
_ww = io.open(os.path.join(ROOT, "agent", "wechat_windows.py"), encoding="utf-8").read()
ok("F1★ 右键退路在位（`how=\"opened_right_click\"`）—— 双击开不出来时的第二招",
   _sm.has(_ww, 'how="opened_right_click"'), "")
ok("F2★ 位置对：**排在双击（含重试）之后、`open_failed` 之前**"
   "（放前面会动到现有成功路径；放后面就是死代码）",
   _ww.find("opened_right_click") < _ww.find('step="open_failed"')
   and _ww.find("opened_retry") < _ww.find("opened_right_click"), "")
ok("F3★ **候选词表**在位，且写明「全都匹配不上时不点」这个约束（锚意图，不锚文案字面）",
   _sm.has(_ww, "POPUP_MENU_LABELS = (") and _sm.has(_ww, "全部匹配不上时不点"), "")
_seg_rc = _ww[_ww.find("opened_right_click") - 2500: _ww.find("opened_right_click") + 1500]
ok("F4★ **匹配不到就不点**：候选有上限（最多两个）、且**没有**「兜底点最上面那项」这种写法",
   _sm.has(_ww, "POPUP_MENU_LABELS[:2]") and ("allow_top_fallback" not in _seg_rc), "")
ok("F5 搜索那条退路仍在（目标行不在可见区 ⇒ `search_then_highlight`）",
   _sm.has(_ww, 'how="search_then_highlight"'), "")
ok("F6 最后一道仍是「请用户自己拖」＋ 如实失败（不是静默放弃）",
   _sm.has(_ww, "user_action") and _sm.has(_ww, 'step="open_failed"'), "")

print("\n── G. 落点（**真机上错过一次**，这里把它钉住）──")
from agent import wechat_windows as _WW  # noqa: E402
ok("G0 分母守卫：落点函数在位（纯函数 ⇒ 判据能直接喂组合，不必真机）",
   hasattr(_WW, "row_click_x"), "")
_x1, _h1, _w1 = _WW.row_click_x(341, 350, 2560)
ok("G1★★ **真机踩过的那组**（名字左沿=341、列表右沿=350 互相矛盾）⇒ 必须退回保守对 "
   "（旧写法会**反过来把 pane 撑大到 541** ⇒ x=441 落进聊天区 ⇒ 双击/右键全部失效）",
   _h1 == "conflict_fallback" and _WW.ROW_X_FALLBACK_LO <= _x1 <= _WW.ROW_X_FALLBACK_HI,
   "x=%d how=%s" % (_x1, _h1))
_x2, _h2, _ = _WW.row_click_x(0, 0, 2560)
ok("G2 两个都没量到 ⇒ 也退回保守对（不猜）",
   _h2 == "conflict_fallback" and _WW.ROW_X_FALLBACK_LO <= _x2 <= _WW.ROW_X_FALLBACK_HI, "x=%d" % _x2)
_x3, _h3, _ = _WW.row_click_x(200, 350, 900)
ok("G3 窗口窄时再夹一道（`x` 不超过窗口宽 22%）", _x3 <= int(900 * 0.22) + 1, "x=%d" % _x3)
_x4, _, _ = _WW.row_click_x(180, 350, 2560)
ok("G4 正常组合仍是中点（没被改坏）", _x4 == int((180 + 350) / 2), "x=%d" % _x4)
_ww2 = io.open(os.path.join(ROOT, "agent", "wechat_windows.py"), encoding="utf-8").read()
ok("G5★ **x 安全阀**在位（越界 ⇒ 拒绝点击并说清，不许静默落到聊天区）",
   _sm.has(_ww2, 'step="guard_x"') and _sm.has(_ww2, "不在会话行内"), "")
ok("G6★ 反向锚：**旧的「把 pane 撑大」写法不许再出现**（那正是错的那一步）",
   not _sm.has(_ww2, "name_l + 200"), "")
ok("G7 落点来源写进返回体（`x_source`）⇒ 出问题时**可观测**，不用猜",
   _sm.has(_ww2, 'out["x_source"]'), "")

print("\n── H. 落点改走「DIP 常数 × 真实缩放」（唯一算法，**不依赖现场测量**）──")
ok("H0 分母守卫：三个 DIP 常数都在", all(hasattr(_WW, k) for k in
   ("AVATAR_X_DIP", "ROW0_DIP", "ROW_PITCH_DIP")),
   "头像x=%s 首行=%s 行高=%s" % (_WW.AVATAR_X_DIP, _WW.ROW0_DIP, _WW.ROW_PITCH_DIP))
_xr = (180, 120, 1289, 1190)
_pts = {s: _WW.row_click_pt(_xr, 0, s) for s in (1.0, 1.25, 1.5, 2.0)}
ok("H1★★ **落点 x 只跟缩放走**（严格成正比：x−窗口左沿 = %d×缩放）" % _WW.AVATAR_X_DIP,
   all(abs((_pts[s][0] - _xr[0]) - _WW.AVATAR_X_DIP * s) <= 1 for s in _pts),
   str({s: _pts[s][0] - _xr[0] for s in _pts}))
_wr = (0, 0, 3000, 2000)          # 换一个**宽得多**的窗口
_pts2 = _WW.row_click_pt(_wr, 0, 1.5)
ok("H2★★ **与窗口宽度无关**（列表再宽再窄，头像位置不变）—— 这正是「点头像最稳」的理由",
   (_pts2[0] - _wr[0]) == (_pts[1.5][0] - _xr[0]), "宽窗 x=%d｜窄窗 x=%d"
   % (_pts2[0] - _wr[0], _pts[1.5][0] - _xr[0]))
_dy = [_WW.row_click_pt(_xr, i, 1.5)[1] - _WW.row_click_pt(_xr, 0, 1.5)[1] for i in range(4)]
ok("H3 行距 = %d×缩放、且逐行递增" % _WW.ROW_PITCH_DIP,
   _dy == [0, 96, 192, 288], str(_dy))
ok("H4★ 落点确实落在**头像**上（落点常数就是头像中心的 DIP 值，不是依赖列表宽的「名字区中点」）",
   _WW.AVATAR_X_DIP == 98 and abs(_pts[1.0][0] - _xr[0] - 98) <= 1, "")
ok("H5★★ **代码里不许再有写死的缩放**（旧写法 `× 1.5` 只在 150% 的机器上对）",
   not any(ln.split("#")[0].count("* 1.5") for ln in _ww2.splitlines()), "")
ok("H6★ 缩放来自**窗口自己的 DPI**（`GetDpiForWindow`），**不是** `ui_adapt.detect_scale()`"
   "（后者是「进程 DPI 感知下的比值」，本机实测 1.0，与窗口内容缩放 1.5 是两回事）",
   _sm.has(_ww2, "GetDpiForWindow") and _sm.has(_ww2, "def win_scale("), "")
ok("H7★ 现场测量那套**降级为交叉校验**（不参与落点决策；不一致只记录、不改行为）",
   _sm.has(_ww2, '"cross_check"') and _sm.has(_ww2, "不参与"),
   "")
ok("H8★★ 安全阀**拒绝时不许返回落点**（否则调用方会拿一个不该用的坐标去点）",
   _sm.has(_ww2, "def _drop_click_pt(") and _ww2.count("_drop_click_pt(out)") >= 3, "")
_i_cc = _ww2.find('"cross_check"')
_i_dry = _ww2.rfind("if dry_run:", 0, _i_cc)
ok("H9★ 昂贵的诊断（交叉校验＝抓整帧+剖面，约 1 秒）**只在 dry_run 时跑**，"
   "不许挂在真实发送路径上（锚行为，不锚文案）",
   _i_cc > 0 and _i_dry > 0 and (_i_cc - _i_dry) < 900, "间隔 %d 字符" % (_i_cc - _i_dry))

print("\n== 只对独立窗闸门判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
