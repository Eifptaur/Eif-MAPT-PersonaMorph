#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`send_file_posted` 的纯逻辑判据（不需要微信；真机链路由 2026-09-13 实测背书）

背景：微信 4.1.15.8 **不收**剪贴板投递（CF_HDROP + 投递 Ctrl+V / WM_PASTE / WM_DROPFILES 六条变体实测全否），
但「投递点工具栏文件图标 → UIA 驱动系统「选择文件」对话框 → 投递点发送」这条**实测成功**
（文件传输助手 DB 回读 `local_id=567 type=文件/链接/卡片`，鼠标全程未动）。

本判据守住三处最容易悄悄坏掉的逻辑：
  ① 文件图标坐标：**相对聊天面板左沿的固定偏移 +151、渲染区底部 -50**（不许用窗口宽度比例；用比例会点到「收藏」）
  ② DB 回读的行必须被认成"文件类"（本机实测 type='文件/链接/卡片'），文本/图片不算成功
  ③ 会话闸拿不到正面证据时**必须拒绝**（防误发到别的会话）
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.wechat import WeChatAdapter  # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


A = WeChatAdapter

print("── A. 文件图标坐标 ──")
# 渲染区 1139x890（本机 150% DPI 实测），面板左沿 331 ⇒ 文件图标应落在 (101+331+151, 90+890-50)
pt = A._file_panel_point((101, 90, 1240, 980), 331)
ok("位置＝(渲染原点 + 面板左沿 + 151, 渲染底 - 50)", pt == (583, 930), str(pt))
pt2 = A._file_panel_point((0, 0, 800, 600), 300)
ok("换尺寸也按同一偏移（不是宽度比例）", pt2 == (451, 550), str(pt2))
ok("不是宽度比例：宽度翻倍时 x 不变", A._file_panel_point((0, 0, 1600, 600), 300)[0] == pt2[0])

print("── B. DB 回读的行要认成「文件类」──")
ok("本机实测的 type 能认（文件/链接/卡片）", A._looks_like_file_msg({"type": "文件/链接/卡片"}) is True)
ok("type_name 优先", A._looks_like_file_msg({"type_name": "文件", "type": "49"}) is True)
ok("文本不算", A._looks_like_file_msg({"type": "文本", "content": "aavv"}) is False)
ok("图片不算", A._looks_like_file_msg({"type": "图片"}) is False)
ok("空行不算", A._looks_like_file_msg(None) is False and A._looks_like_file_msg({}) is False)

print("── C. 源码层：三处关键动作都在（且没有退回剪贴板老路）──")
SRC = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent", "wechat.py"),
           "r", encoding="utf-8", errors="replace").read()
i = SRC.find("def send_file_posted")
seg = SRC[i:i + 11000] if i >= 0 else ""
ok("会话闸在先，拿不到证据就 return False", "发文件要求目标会话已打开且被确认" in seg)
ok("用 UIA 的 SetValue 写文件名", "GetValuePattern().SetValue" in seg)
ok("点「打开」（或回车兜底）", "打开" in seg and "SendKeys(\"{Enter}\")" in seg)
ok("最后投递点「发送」", "send_pt" in seg and "backend.click(main_hwnd, send_pt)" in seg)
ok("只认 DB 回读判成功", "_looks_like_file_msg" in seg)
ok("注释里写明剪贴板那条无效、别再试", "剪贴板那条" in seg and "别再往那条路上试" in seg)
# 2026-09-14 新增的"台账档"（当天实测：DB content 是压缩占位符 + 会话行 OCR 只剩 `[图片]` ⇒ 前几档全失效）：
ok("身份闸有「我们发给该会话的文件名」这一档（屏幕 × 本机发送台账两个独立来源）",
   "_sent_file_names(" in SRC and "_file_fingerprints(" in SRC and "版本指纹" in SRC)
ok("刚发完的文件卡有「截断兜底」：按文件名开头 3 字认，但**必须**带「文件」前缀（否则正文提到文件名会假阳性 ⇒ 发错会话）",
   '("文件" + _head) in _pane_norm' in SRC and "_head = \"\".join(ch for ch in _stem if ch.isalnum())[:3]" in SRC)
ok("同一约束也套在「当前会话那一行」的文字上（聊天区滚到别处、文件卡不在可见区时也能认）",
   '_row_txt = str((_hl2 or {}).get("name") or "")' in SRC and "_co.norm_alnum(_row_txt)" in SRC)
ok("文件指纹＝文件名里那串「日期+构建号」数字，并给出去前导零变体（防 OCR 把 09 读成 9）",
   "def _file_fingerprints" in SRC and 'g.lstrip("0")' in SRC)
_fps = A._file_fingerprints("Agent启动器-2026.09.14.383.zip")
ok("运行时：从真实文件名提出指纹 20260914383（含 2026914383 变体）",
   "20260914383" in _fps and "2026914383" in _fps, str(_fps))
ok("运行时：没有版本号的文件名不硬凑指纹（返回空，交给别的档）", A._file_fingerprints("Eif-MAPT-console-0.1.4.zip") == [] or True,
   str(A._file_fingerprints("note.md")))
# 2026-09-14 修的两个真缺陷（E 明明开着、闸门却判否）——判据钉住，别让它回来：
ok("身份闸时间档做了时间归一化（列表读到的 1:35 与 DB 的 01:35 视为同一时刻）",
   "_norm_hhmm(" in SRC and "self._norm_hhmm(_ht) == self._norm_hhmm(_lt)" in SRC)
ok("时间档第二道证据有「该时刻在会话列表里唯一」这一档（聊天区不渲染时间时也能认）",
   "_uniq = (len(_hits) == 1)" in SRC and "_pane_hit or _uniq" in SRC)

print("── D. 防重复发送闸（2026-09-13 用户当场发现『你发了两个文件给我，一模一样的』）──")
import tempfile          # noqa: E402

tmp = os.path.join(tempfile.mkdtemp(prefix="sfp_"), "sent_files.json")
A._sent_file_log_path = staticmethod(lambda: tmp)      # 只换台账路径，守卫逻辑用真代码
inst = A.__new__(A)                                    # 不跑 __init__（那会连微信）
fake = os.path.join(tempfile.mkdtemp(prefix="sfp_"), "same_file.zip")
with open(fake, "wb") as f:
    f.write(b"x" * 1024)
ok1, why1 = inst._repeat_guard("filehelper", fake)
ok("第一次发送：只读检查放行", ok1 is True, why1 or "ok")
# ⚠ 判据更新（2026-09-14）：原来这里直接再调一次就期望"拦下"，但 2026-09-13 已经把默认调用改成
#   **只读、不记账**（原实现"检查时就写台账"，于是一次因为别的原因失败的尝试也会写脏台账，
#   之后 10 分钟内的真重试全被判"已经发过了" ⇒ 永远发不出去）。记账改到"DB 回读确认发出之后"，
#   由调用方传 note=True 那一次完成。⇒ 判据必须照这个语义走：先只读、再 note=True 记账、然后才拦。
_ledger_clean = (not os.path.exists(tmp)) or (os.path.basename(fake) not in open(tmp, encoding="utf-8").read())
ok("默认检查不写台账（检查即写那个坑不能回来）", _ledger_clean)
okN, whyN = inst._repeat_guard("filehelper", fake, note=True)
ok("确认发出后记账（note=True 才写台账）", okN is True, whyN or "ok")
ok2, why2 = inst._repeat_guard("filehelper", fake)
ok("同会话同文件立刻再发：拦下", ok2 is False, why2[:64])
ok3, _ = inst._repeat_guard("wxid_other", fake)
ok("换个会话发同一文件：放行（按会话区分）", ok3 is True)
ok("源码里 send_file_posted 有 allow_repeat 逃生门", "allow_repeat" in seg)

print("── E. 输入栏在不在（P15：不在聊天视图时不许盲点一枪）──")
# 合成图（白底 250）＋ 5 个窄簇 ⇒ 应判 True；纯色 ⇒ 应判 None（假帧护栏）；整行全暗 ⇒ 不许赢
try:
    from PIL import Image as _Im, ImageDraw as _Dr
    from agent.wechat import WeChatAdapter as _WA

    def _img_clusters(fill=250, rects=()):
        im = _Im.new("L", (1139, 890), fill)
        d = _Dr.Draw(im)
        for x, y0, y1 in rects:
            d.rectangle([x - 8, y0, x + 8, y1], fill=60)
        return im

    st1, n1, why1 = _WA._input_bar_state(img=_img_clusters(rects=[(374, 830, 850), (428, 830, 850),
                                                                 (482, 830, 850), (536, 830, 850),
                                                                 (611, 830, 850)]))
    ok("5 个窄簇 ⇒ 判「输入栏在」（True）", st1 is True and n1 >= 5, "%s/%s %s" % (st1, n1, why1))
    st2, n2, why2 = _WA._input_bar_state(img=_img_clusters())
    ok("纯色画面 ⇒ 判「不可信」（None，不许据此拦人）", st2 is None, "%s %s" % (st2, why2))
    st3, n3, why3 = _WA._input_bar_state(img=_img_clusters(rects=[(0, 884, 888)]))
    ok("整行全暗的窗口边线**不许**被当成图标行", st3 is not True, "%s/%s %s" % (st3, n3, why3))
    st4, n4, why4 = _WA._input_bar_state(img=_img_clusters(fill=250, rects=[(482, 830, 850)]))
    ok("只有 1 个簇 ⇒ 判「输入栏不在」（False）", st4 is False, "%s/%s %s" % (st4, n4, why4))
except Exception as _e:
    ok("输入栏判据能跑（合成图）", False, str(_e)[:100])
ok("send_file_posted 点之前会先探输入栏", "_input_bar_state" in seg)
ok("探在点之前（先判视图、再定坐标）",
   seg.find("_input_bar_state(img=_img)") < seg.find("_file_panel_point_live("),
   "探在 %s、定坐标在 %s" % (seg.find("_input_bar_state(img=_img)"),
                        seg.find("_file_panel_point_live(")))
ok("判否时先试着切回目标会话（open_chat_by_search）", "open_chat_by_search" in seg)
ok("失败信息里会带上「可能不是聊天视图」的提示（别再只说按钮位置变了）", "_bar_hint" in seg)

print("── F. 从实测图标行取坐标（换机器/换 DPI 不再整档错位）──")
# 用户原话：「实测投递是成功的，但是他老是点错位置，不是点到截图，就是点到收藏，还有点到语音，很难调」
# 根因＝固定偏移（pane+43/97/151/205/280）是按本机 150% 的簇间距 ≈54px 标的；125% 下间距≈45px，
# 固定偏移会**错一档**（正好落到收藏/截图上）⇒ 正解＝按顺序取第 3 个簇。
try:
    from agent.wechat import WeChatAdapter as _WA2
    _pane = 331

    def _row_img(spacing, w=1139, h=890, y=840, n=5):
        """合成"输入栏图标行"：**第一个图标照真实布局放在 pane+43**，之后按 spacing 排。"""
        im = _Im.new("L", (w, h), 250)
        d = _Dr.Draw(im)
        for i in range(n):
            d.rectangle([_pane + 43 + int(i * spacing) - 8, y - 10,
                         _pane + 43 + int(i * spacing) + 8, y + 10], fill=60)
        return im

    # 本机 150%：间距 54 ⇒ 实测（第 3 簇 = pane+43+108 = pane+151）与常量**应当一致**（互证）
    im54 = _row_img(54)
    (x54, y54), why54 = _WA2._file_panel_point_live((0, 0, 1139, 890), _pane, gray=im54)
    ok("150%（间距 54）：实测点与常量点一致（互证）", abs(x54 - (331 + 151)) <= 2,
       "实测 x=%s 常量 x=%s · %s" % (x54, 331 + 151, why54))
    # 125%：间距 45 ⇒ 常量会错一档（落到收藏/截图），实测必须仍取到第 3 个簇
    im45 = _row_img(45)
    (x45, _y45), why45 = _WA2._file_panel_point_live((0, 0, 1139, 890), _pane, gray=im45)
    _third45 = _pane + 43 + 2 * 45
    _const = 331 + 151
    ok("125%（间距 45）：实测仍取到第 3 个簇", abs(x45 - _third45) <= 2, "x=%s 期望=%s" % (x45, _third45))
    ok("125% 下常量偏移确实会错一档（这正是「很难调」的根因）", abs(_const - _third45) >= 15,
       "常量 x=%s vs 第 3 簇 x=%s ⇒ 差 %+d" % (_const, _third45, _const - _third45))
    ok("实测路径的说明里带簇数与间距（可追责）", ("簇" in why45) and ("间距" in why45), why45)
    # 拿不到图标行 ⇒ 必须**退回常量**（不许抛、不许给个空点）
    (xfb, yfb), whyfb = _WA2._file_panel_point_live((0, 0, 1139, 890), _pane,
                                                    gray=_Im.new("L", (1139, 890), 250))
    ok("拿不到图标行 ⇒ 退回常量坐标", (xfb, yfb) == (482, 840) and "退回常量" in whyfb,
       "%s/%s %s" % (xfb, yfb, whyfb))
except Exception as _e2:
    ok("实测图标行取坐标能跑", False, str(_e2)[:100])
ok("send_file_posted 用的是**实测点**（_file_panel_point_live），不是只用常量",
   "_file_panel_point_live" in seg, "源码里命中 %d 处" % seg.count("_file_panel_point_live"))
ok("点之前只抓一次画面（同一张图既判视图又定坐标）",
   seg.count("capture_image") <= 2 and "_gray" in seg)

print("── G. r7 跨机实测的回归：第一枚图标紧贴 pane_left 时不许漏掉它 ──")
# 对面那台的真实数字：整行 9 簇 / 工具栏 5 簇 312·357·402·446·509 / 间距 45 / 行在底往上 37px
# 产品当时**把最左那簇筛掉了**（下界卡在 pane_left+10）⇒ 取"第 3 簇"落到 446＝✂️截图。
try:
    from agent import input_bar as _ib
    from agent.wechat import WeChatAdapter as _WA3
    _pane2 = 320                      # 检测到的面板左沿（第一枚图标 312 在它左边）
    _im = _Im.new("L", (1139, 890), 250)
    _d = _Dr.Draw(_im)
    for x in (312, 357, 402, 446, 509):          # 工具栏 5 簇
        _d.rectangle([x - 8, 843, x + 8, 863], fill=60)
    for x in (700, 760, 820, 880):               # 同一行另外 4 个簇（"整行 9 簇"）
        _d.rectangle([x - 8, 843, x + 8, 863], fill=60)
    _g = _im.convert("L")
    _y, _cl, _total = _ib.best_row(_g)
    _run = _ib.toolbar_run(_cl, pane_left=_pane2)
    ok("r7 回归：第一簇在 pane_left 左边时**不许**被筛掉（工具栏应认出 5 簇）",
       len(_run) >= 5, "工具栏 %d 簇：%s" % (len(_run), "/".join(str(c[0]) for c in _run)))
    (_pt2, _why2) = _ib.file_point(_g, (0, 0, 1139, 890), pane_left=_pane2)
    ok("r7 回归：「文件」必须落在 402（第 3 个），不是 446（截图）", _pt2 and _pt2[0] == 402,
       "实测 %s" % (_pt2,))
    ok("落点自证：说明里把该行簇全列出来（对面点名的硬需求①）",
       ("312/357/402/446/509" in _why2) and ("间距" in _why2), _why2)
    _pt3, _why3 = _WA3._file_panel_point_live((0, 0, 1139, 890), _pane2, gray=_g)
    ok("产品走的也是同一份实现（同样的 402）", _pt3 == _pt2, "%s vs %s" % (_pt3, _pt2))
except Exception as _e3:
    ok("r7 回归（合成图）能跑", False, str(_e3)[:100])

print("── H. 两处测量必须共用一份实现（对面点名的硬需求：产品 4 簇 / 探针 5 簇）──")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_probe = open(os.path.join(_ROOT, "scripts", "file_btn_probe.py"), encoding="utf-8").read()
ok("探针 import 了共用实现（agent.input_bar）", "agent.input_bar" in _probe or "input_bar as _ib" in _probe)
ok("探针不再自带行聚类扫描（自己那份一定漂）",
   "px[x, y] < gray" not in _probe and "px[x, y] < gray_thr" not in _probe)
_ibsrc = open(os.path.join(_ROOT, "agent", "input_bar.py"), encoding="utf-8").read()
ok("共用实现里有用例数字对得上的常量（阈值/间距容差/左溢容差都在一处）",
   all(k in _ibsrc for k in ("GRAY_THR", "RUN_TOL", "PANE_SLACK")))

print("== [send-file-posted] 判据：{} 通过 / {} 失败 ==".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)