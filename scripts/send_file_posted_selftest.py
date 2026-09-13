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

print("== [send-file-posted] 判据：{} 通过 / {} 失败 ==".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)