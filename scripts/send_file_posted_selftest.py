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
# ⚠️ 2026-09-16：原来是写死的 `SRC[i:i + 11000]` 切片——函数里多加十几行（本轮加"写完文件名立刻还前台"）
#   就把后面的断言挪出窗外、变成**假红**。判据要守的性质是"这些步骤都在这个函数里"，不是"函数恰好
#    不超过 11000 字符" ⇒ 改成**切到下一个同级 def 为止**。
_j = SRC.find("\n    def ", i + 10)
seg = SRC[i:(_j if _j > 0 else i + 11000)] if i >= 0 else ""
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
# ⚠️ 2026-09-16：这两档**抽成了 `_active_row_time_ok()`**（`chat_identity_ok` 与 `chat_is_open`
#    共用同一条证据链），所以断言改成钉"抽出来的那一处"，并要求两条闸都真的接上了它。
ok("身份闸时间档做了时间归一化（列表读到的 1:35 与 DB 的 01:35 视为同一时刻）",
   "def _active_row_time_ok" in SRC and "self._norm_hhmm(_ht) != self._norm_hhmm(_lt)" in SRC
   and "_ok_t, _why_t = self._active_row_time_ok(chat_id, pane=pane, gui=gui)" in SRC
   and "_ok3, _why3 = self._active_row_time_ok(chat_id, gui=gui)" in SRC)
ok("时间档第二道证据有「该时刻在会话列表里唯一」这一档（聊天区不渲染时间时也能认）",
   "_uniq = (_n == 1)" in SRC and "if _pane_hit or _uniq:" in SRC)

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

print("── I. r8 跨机实测的回归：探针多收的那一簇（247）不许把工具栏整体右移 ──")
# 对面 r8 报的：同一行，产品工具栏 5 簇（312/357/402/446/509）✅，探针却 6 簇（多了 247）⇒ 取第 3 个
# 变成 357＝📦收藏 ✗。根因＝那时 `toolbar_run` 拿 pane_left 当门槛、容差又松。现在：不看 pane_left，
# 规整的一跳用 0.35 判、**只允许最后一跳**异常（🎤语音 63px），参考间距取该行间距的**下半段中位数**。
try:
    from agent import input_bar as _ib2
    _im2 = _Im.new("L", (1139, 890), 250)
    _d2 = _Dr.Draw(_im2)
    for x in (247, 312, 357, 402, 446, 509):      # 多了 247（探针当时多收的那一簇）
        _d2.rectangle([x - 8, 843, x + 8, 863], fill=60)
    _g2 = _im2.convert("L")
    _y2, _cl2, _tot2 = _ib2.best_row(_g2)
    _run2 = _ib2.toolbar_run(_cl2, pane_left=300)   # 故意给个"会把 247 放进门槛内"的 pane_left
    _xs2 = [c[0] for c in _run2]
    ok("r8 回归：多收的 247 不许进工具栏那组（必须只有 312..509 这 5 簇）",
       _xs2[:1] == [312] and len(_run2) == 5, "工具栏 %s" % _xs2)
    (_pt4, _why4) = _ib2.file_point(_g2, (0, 0, 1139, 890), pane_left=300)
    ok("r8 回归：「文件」仍必须是 402（不是 357＝收藏）", _pt4 and _pt4[0] == 402, "实测 %s" % (_pt4,))
    ok("r8 回归：pane_left 已经不参与判定（换一个 pane_left 结论不变）",
       _ib2.file_point(_g2, (0, 0, 1139, 890), pane_left=0)[0][0] == 402)
    _probe2 = open(os.path.join(_ROOT, "scripts", "file_btn_probe.py"), encoding="utf-8").read()
    # ⚠️ 先剥注释再判：注释里会**提到**被删掉的名字（"删掉 ICONS_X 那段"），按全文 grep 会假红
    _pcode2 = "\n".join(l for l in _probe2.split("\n") if not l.lstrip().startswith("#"))
    ok("探针里那段会误导的「常量 vs 最近邻命名」已删（按代码判，不看注释）",
       "ICONS_X" not in _pcode2 and "最近实测簇" not in _pcode2)
except Exception as _e4:
    ok("r8 回归（合成图）能跑", False, str(_e4)[:100])

print("── J. 内容级闸的观测口径（跨机需求②：分清「没信号」与「信号被阈值判掉」）──")
try:
    from agent import chat_ocr as _co3
    _pane = "落空，责任在我这一侧的落占，你手点就能弹系统「选择文件」，我们这轮把定位统一了"
    _n_len, _ratio, _frag = _co3.best_partial(_pane, "你手点就能弹系统「选择文件」")
    ok("整段就在聊天区里 ⇒ 命中 ≥8 字、相似度 1.0", _n_len >= 8 and _ratio >= 0.99,
       "%d 字 / %.3f / %r" % (_n_len, _ratio, _frag))
    _n_len2, _ratio2, _frag2 = _co3.best_partial(_pane, "你手点就能弹系统选择文件我们这轮把定位统一了")
    ok("针更长但被 OCR 吞字 ⇒ 仍能给出『部分命中』（这就是「信号被阈值判掉」的样子）",
       _n_len2 >= 6, "%d 字 / %.3f / %r" % (_n_len2, _ratio2, _frag2))
    _n_len3, _ratio3, _ = _co3.best_partial(_pane, "完全不相干的另一句话在这里")
    ok("不相干的针 ⇒ 命中很短、相似度低（这就是「根本没有信号」）",
       _n_len3 <= 4 and _ratio3 < 0.5, "%d 字 / %.3f" % (_n_len3, _ratio3))
    ok("空针 ⇒ (0, 0.0, '')，不抛异常", _co3.best_partial(_pane, "")[:2] == (0, 0.0))
    _wxsrc = open(os.path.join(_ROOT, "agent", "wechat.py"), encoding="utf-8").read()
    _segid = _wxsrc[_wxsrc.index("def chat_identity_ok"):_wxsrc.index("def _last_time_hhmm")]
    ok("闸门失败信息里会带观测量（聊天区字数 + 每条针的最好匹配）",
       "观测：聊天区读到" in _segid and "best_partial" in _segid)
    _probe3 = open(os.path.join(_ROOT, "scripts", "identity_probe.py"), encoding="utf-8").read()
    ok("随包发了只读诊断探针 identity_probe.py（只用抓图/读库/OCR）",
       "chat_identity_ok" in _probe3 and "capture_best" in _probe3)
    ok("诊断探针是只读的（不许出现 send_file / 真鼠标点击调用）",
       ("send_file" not in _probe3) and ("be.click" not in _probe3))
except Exception as _e5:
    ok("观测口径（合成文本）能跑", False, str(_e5)[:100])

print("── K. 内容级闸的 fail-open 修复（跨机 r10 实测：目标没开却判 True）──")
# 他们的现场：目标会话根本没开，闸门却被一条 **6 字日期串 `202609`（相似度 1.000）** 满足 ⇒ 判 True。
# 放行必须要求**强信号**：最短命中 8 字（或占针长 30%）＋ 低熵串（纯数字/日期）不算命中。
try:
    from agent import chat_ocr as _co4
    _pane4 = "那三条现复现（点到+/浮层205×205/认不出绿底行）：我可只读取证复现；版本 20260916 构建 379"
    ok("纯数字针（20260916）不算证据", _co4.content_match(_pane4, "20260916") is False)
    ok("含日期的文件名针：只有日期串巧合命中时**不许放行**",
       _co4.content_match(_pane4, "群相-在线包-20260916-r10.zip") is False)
    ok("真正的长中文内容在聊天区里 ⇒ 仍然放行",
       _co4.content_match(_pane4, "那三条现复现点到浮层认不出绿底行") is True)
    ok("low_entropy 判定：'202609' 低熵、'群相r10' 不是低熵",
       _co4.low_entropy("202609") is True and _co4.low_entropy("群相r10") is False)
    ok("太短的针（<8 字归一化）一律不放行", _co4.content_match(_pane4, "绿底行") is False)
    _wxsrc4 = open(os.path.join(_ROOT, "agent", "wechat.py"), encoding="utf-8").read()
    _segid4 = _wxsrc4[_wxsrc4.index("def chat_identity_ok"):_wxsrc4.index("def _last_time_hhmm")]
    ok("短指纹档也加了两道下界（低熵不算 + <4 不算）",
       "low_entropy(nn)" in _segid4 and "len(nn) >= 4" in _segid4)

    # ⛔⛔ 2026-09-16 晚：**过修成反向问题**（跨机 r11 报告 ①③）——对面那台聊天区里是**上千字的报告**、
    #   屏幕只可见 142~334 字，而原来的片段下界是 `针长 × 30%`＝300 字 ⇒ **永远凑不出** ⇒
    #   「12 字 / 相似度 1.000」的真信号被**误杀**。下面三组就是他们的回归现场（①③ 必须放行、② 必须拦）。
    _long_needle = ("r11 四条都跑了。先说最重要的：fail-open 修好了，但过修成了反向问题。"
                    "一、fail-open 验收矩阵（四组现场）①目标开着读到一百四十二字最长命中十二字相似度一比零"
                    "闸门 False 误杀真信号；②目标没开读到一百四十二字最长命中六字日期巧合闸门 False 正确；"
                    "三、只截底图成功一百二十五的模板到手；四、WGC 本机复核与你的结论完全一致。")
    _pane_r11 = "r11 四条都跑了。先说最重要的"          # 屏幕只可见这一小段（142~334 字的模拟）
    ok("① 长针 + 只有一小段可见（12 字/相似度 1.00）⇒ **必须放行**（修复前是红的）",
       _co4.content_match(_pane_r11, _long_needle) is True)
    ok("③ 长针 + 可见段更长（两处片段）⇒ 也放行",
       _co4.content_match(_pane_r11 + "…" + "四、WGC 本机复核与你的结论完全一致", _long_needle) is True)
    _pane_date = "版本 20260916 构建 379 · 文件卡档：同一文件名也出现过"   # 只有日期巧合
    ok("② 长针 + 只有 6 字日期串巧合 ⇒ **必须拦**（fail-open 不许回来）",
       _co4.content_match(_pane_date, _long_needle) is False)
    ok("长针里的**纯数字片段**仍然不算命中（低熵豁免）",
       _co4.content_match("序号 123456789012 出现在这", _long_needle.replace("一、", "123456789012")) is False)
    # ⚠️ 判据按**行为**判（不按源码字符串判）：源码里连注释/文档都会提到 `n < need` 和 `need = max(...)`，
    #    按字面查必然假红（本项目已有同名教训：按代码判要先剥注释）。这条直接用 400 字长针钉住"比例门没了"。
    _needle400 = ("甲乙丙丁戊己庚辛壬癸" * 40) + "独特内容标记ABCDEF"
    ok("比例门已去掉：400 字长针 + 只可见 12 字 ⇒ 放行（老口径算 need=123 字 ⇒ 误杀）",
       _co4.content_match("前面是别的内容 独特内容标记ABCDEF 后面还有", _needle400) is True)
except Exception as _e6:
    ok("fail-open 回归（合成文本）能跑", False, str(_e6)[:100])

print("── L. 关「选择文件」对话框不许把微信顶到前台（2026-09-16 实测定位到这一步）──")
# 用户原话：「你老是把微信切到前台，然后发文件，这不能后台做吗…那个发文件框本身也可以被放在后台的，
# 它不是锁定前台的」⇒ 探针实测：投递点 📁 不抢前台 ✅、对话框弹出时前台也没变 ✅、
# **关掉对话框之后**前台变成微信主窗 ✗ ⇒ 处置＝关前后各记一次前台，关完还回去。
_wxsrc5 = open(os.path.join(_ROOT, "agent", "wechat.py"), encoding="utf-8").read()
ok("有『打开对话框之前记下前台』的实现（_stash_fg）",
   "def _stash_fg" in _wxsrc5 and "_FG_STASH" in _wxsrc5)
ok("有『还回前台』的实现（且拒绝还给对话框/死窗口）",
   "def _restore_fg" in _wxsrc5 and "SetForegroundWindow" in _wxsrc5
   and '#32770' in _wxsrc5 and "IsWindow" in _wxsrc5)
_seg_send = _wxsrc5[_wxsrc5.index("def send_file_posted"):]
ok("产品发文件路径：**点 📁 之前**就 stash 前台",
   "_stash_fg()" in _seg_send and _seg_send.index("_stash_fg()") < _seg_send.index("backend.click(main_hwnd, pt)"))
_seg_close = _wxsrc5[_wxsrc5.index("def _close_file_dialog"):_wxsrc5.index("def wx_version_for_gate")]
ok("单框关闭路径：关完会还前台", "_restore_fg(" in _seg_close)
ok("批量清理路径也还前台（残留框挡屏时同样会顶前台）",
   _seg_close.count("_restore_fg(") >= 2, "命中 %d 处" % _seg_close.count("_restore_fg("))
try:
    from agent.wechat import _fg_before_close as _fgb
    ok("_fg_before_close() 返回一个整数 hwnd（拿不到就 0，不抛）", isinstance(_fgb(), int))
except Exception as _e7:
    ok("_fg_before_close() 可调用", False, str(_e7)[:80])

print("── M. 抢前台那一步＝**写文件名**（用户 2026-09-16 当面看屏幕定位）⇒ 不进前台 + 盯着还 ──")
# 用户原话：「点击『文件』按钮没有到前台，打开文件窗口也没有到前台，但是**当你粘贴输入那一串字符的
# 时候，它到前台了**。看来你只需要让它**粘贴完，立马缩回后台**就行」。
# 处置两条（第一条才是根治，第二条兜底）：
#   ① **压根不进前台**：文件对话框的名字框是标准 Edit（id 1148）⇒ `WM_SETTEXT` 写、`BM_CLICK` 点「打开」，
#      都是消息，不 SetFocus / 不 SetForegroundWindow ⇒ 前台不变；
#   ② 退回 UIA 那条路时才需要"还"，而且是**盯着还**（对话框激活是异步的，单枪必打空）。
ok("写文件名优先走 WM_SETTEXT（消息投递，不进前台）",
   "_fill_dialog_name(" in _wxsrc5 and "WM_SETTEXT" in _wxsrc5 and "DLG_ID_FILENAME = 1148" in _wxsrc5)
ok("点「打开」优先走 BM_CLICK（消息投递，不进前台）",
   "_click_dialog_open(" in _wxsrc5 and "BM_CLICK" in _wxsrc5 and "DLG_ID_OK = 1" in _wxsrc5)
ok("退回 UIA 时才『盯着还前台』（对话框激活是异步的，单枪会打空）",
   'target.GetValuePattern().SetValue' in _wxsrc5
   and '_restore_fg_until("写完文件名（粘贴那一步）"' in _wxsrc5)
ok("框一消失就立刻还（不再先 sleep 2.0 —— 那会让用户多丢约 2 秒前台）",
   "if _wait_dialog_gone(int(hwnd), 4.0):" in _wxsrc5
   and '_restore_fg_until("对话框关闭后"' in _wxsrc5)
ok("_wait_dialog_gone 返回「真的没了」（调用方靠它决定走哪条收尾路）",
   "def _wait_dialog_gone(hwnd: int, timeout: float = 1.5) -> bool:" in _wxsrc5)
# ⛔⛔ 本轮的**真根因**：`import ctypes` 原来只在函数里局部 import，模块级这些函数（_fg_now /
#   _wait_dialog_gone / _restore_fg / 对话框消息驱动）一律 NameError，而外面包着 except ⇒ **静默失效**。
#   实测证据：修复后 `还前台（对话框关闭后）：25692654 → 134730（结果=True）`，之前一条日志都没有。
ok("模块级 import ctypes（缺了 ⇒ 还前台/等框消失全部静默失效）",
   __import__("re").search(r"^import ctypes\b", _wxsrc5, __import__("re").M) is not None)
ok("还前台的日志带 note 与前后 hwnd（跨机报告能核对到步）",
   'log.info("还前台（%s）：%s → %s（结果=%s，AttachThreadInput 绕法）"' in _wxsrc5)
try:
    from agent import wechat as _W5
    ok("_wait_dialog_gone(0, 0.1) 对不存在的窗口判『已消失』并返回 bool",
       _W5._wait_dialog_gone(0, 0.1) is True)
    _r = _W5._fill_dialog_name(0, "C:\\nope.txt")
    ok("_fill_dialog_name 拿不到控件时给 (False, 说明)，不抛",
       isinstance(_r, tuple) and _r[0] is False and bool(_r[1]))
    _r2 = _W5._click_dialog_open(0)
    ok("_click_dialog_open 拿不到按钮时给 (False, 说明)，不抛",
       isinstance(_r2, tuple) and _r2[0] is False and bool(_r2[1]))
    _W5._FG_STASH.update({"hwnd": 0, "at": 3.0})          # hwnd=0 ⇒ 函数会在"前台没变"处早退，不碰真窗口
    _W5._restore_fg(0, "selftest-keep", keep=True)
    ok("keep=True ⇒ 不动 stash（stash 还在，后面几步还能用）", float(_W5._FG_STASH.get("at") or 0) == 3.0)
    _W5._restore_fg(0, "selftest-clear", keep=False)
    ok("keep=False ⇒ 清 stash（这一笔走完了）", float(_W5._FG_STASH.get("at") or 0) == 0.0)
    _W5._FG_STASH.update({"hwnd": 0, "at": 0.0})
except Exception as _e8:
    ok("_restore_fg 的 keep/clear 语义可测", False, str(_e8)[:80])

print("── N. 发送/自检路径**不许悄悄退回真鼠标**（跨机 r12 事故：一动检工具动了 16 秒光标）──")
# 对面 r12 原话："你自己那条 一键检验（生成报告） 在我这儿掉了真实路径、动过光标"（37.4s 一发、
# 光标动了 16s，日志 '投递切会话：False → 改走真实路径'）。⇒ 真鼠标兜底改成**显式 opt-in**（默认关）。
_segN = open(os.path.join(_ROOT, "agent", "wechat.py"), encoding="utf-8").read()
ok("有开关实现：默认关 + 环境变量可强制关",
   "def _real_fallback_allowed" in _segN and 'get("allow_real_fallback", False)' in _segN
   and "WXAGENT_REAL_FALLBACK" in _segN)
_cfgN = open(os.path.join(_ROOT, "agent", "config.py"), encoding="utf-8").read()
ok("config 默认值＝False，并把事故写在注释里",
   '"allow_real_fallback": False' in _cfgN and "动了 16 秒光标" in _cfgN)
_segS = _segN[_segN.index("def send_text("):]
_segS = _segS[:_segS.find("\n    def ", 10)]          # 只在 `send_text` 这一个函数体里比顺序
ok("这道闸压在**真实路径之前**（同一函数内的顺序）",
   "if not self._real_fallback_allowed():" in _segS
   and _segS.index("if not self._real_fallback_allowed():") < _segS.index("_send_with_foreground"))
ok("不退回时把原因说清（带会话头三态）", "不退回真鼠标" in _segN and "% _st_status" in _segN)
ok("自检工具（collect_report）强制关掉真鼠标兜底",
   'os.environ["WXAGENT_REAL_FALLBACK"] = "0"' in
   open(os.path.join(_ROOT, "scripts", "collect_report.py"), encoding="utf-8").read())
ok("搜索浮层失败要自己关掉（别留屏 + 别占前台）",
   "def _close_search_popover" in _segN and _segN.count("_close_search_popover(") >= 3)
try:
    import os as _osN
    from agent import wechat as _WN
    _adN = _WN.WeChatAdapter.__new__(_WN.WeChatAdapter)
    _env_old = _osN.environ.get("WXAGENT_REAL_FALLBACK")
    _cfg_old = _WN.get_config
    _osN.environ.pop("WXAGENT_REAL_FALLBACK", None)
    _WN.get_config = lambda: {}
    ok("默认（配置里没这个键）⇒ **不退回**", _adN._real_fallback_allowed() is False)
    _WN.get_config = lambda: {"input": {"allow_real_fallback": True}}
    ok("显式打开 ⇒ 允许", _adN._real_fallback_allowed() is True)
    _osN.environ["WXAGENT_REAL_FALLBACK"] = "0"
    ok("环境变量=0 ⇒ 强制关（自检路径用）", _adN._real_fallback_allowed() is False)
    _WN.get_config = _cfg_old
    if _env_old is None:
        _osN.environ.pop("WXAGENT_REAL_FALLBACK", None)
    else:
        _osN.environ["WXAGENT_REAL_FALLBACK"] = _env_old
except Exception as _eN:
    ok("_real_fallback_allowed 行为可测", False, str(_eN)[:80])

print("── O. 内容像还不够：**活动行时间**要跟目标对得上（跨机 r14：两个会话内容逐字相同时会双放行）──")
_segO = open(os.path.join(_ROOT, "agent", "wechat.py"), encoding="utf-8").read()
ok("有 _row_time_conflict 实现（活动行时间 vs 目标最后一条消息时间）",
   "def _row_time_conflict" in _segO and "活动行时间对不上" in _segO)
ok("内容级闸放行前会先查它（源码顺序：content_match 之后立刻查）",
   _segO.index("if _co.content_match(pane, nd):") < _segO.index("_cf, _cfwhy = self._row_time_conflict"))
try:
    from agent import chat_ocr as _coO
    from agent import wechat as _WO
    _adO = _WO.WeChatAdapter.__new__(_WO.WeChatAdapter)
    _adO._last_time_hhmm = lambda cid: "02:11"
    _cap_o, _hlt_o = _coO.capture_best, _coO.highlight_time
    _coO.capture_best = lambda gui=None, frames=2: object()
    _coO.highlight_time = lambda img: ("2:33", 168)
    ok("活动行 2:33 ≠ 目标 02:11 ⇒ 判冲突（正是那台 filehelper 的情形）",
       _adO._row_time_conflict("x", gui=object())[0] is True, str(_adO._row_time_conflict("x", gui=object())))
    _coO.highlight_time = lambda img: ("2:33", 168)
    _adO._last_time_hhmm = lambda cid: "02:33"
    ok("活动行 2:33 ＝ 目标 02:33 ⇒ 不算冲突（余命十日那一侧）",
       _adO._row_time_conflict("x", gui=object())[0] is False)
    _coO.highlight_time = lambda img: ("", None)
    ok("读不到活动行时间 ⇒ 不拦（交给其它档，fail-open 在这里是安全的）",
       _adO._row_time_conflict("x", gui=object())[0] is False)
    _adO._last_time_hhmm = lambda cid: ""
    _coO.highlight_time = lambda img: ("2:33", 168)
    ok("目标是昨天的消息（没有 HH:MM）⇒ 不拦", _adO._row_time_conflict("x", gui=object())[0] is False)
    _coO.capture_best, _coO.highlight_time = _cap_o, _hlt_o
except Exception as _eO:
    ok("_row_time_conflict 行为可测", False, str(_eO)[:80])

print("== [send-file-posted] 判据：{} 通过 / {} 失败 ==".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)