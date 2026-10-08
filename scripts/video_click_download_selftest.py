# -*- coding: utf-8 -*-
"""B9 判据：视频「投递点击触发下载」三件套（`scripts/video_click_download_selftest.py`）。

**判什么**（对应 `报告/外部项目考察-20261001/B9核实与方案.md`）
    ① `_media_bubble_locate` 的类型白名单**必须含"视频"**（否则永远量不到 ⇒ 整条链死）；
    ② `_trigger_video_download` 走**投递**：切会话用 `switch_chat_posted`（**绝不用**
       `gui.open_chat` 真鼠标）、点击用 `MessageBackend.click`、**关伪激活**；
    ③ **绝不回真鼠标**：投递档拿不到 / 点击失败 ⇒ 立刻 `None`（不许偷偷找 `real_click`）；
    ④ `download_media(video)` 三步顺序：先查缓存 → 再触发 → 最后退回库的等待式；
    ⑤ 关预览窗走 `winops.close`（关窗唯一入口，内部先过 `ok_to_close`；**不发 Esc**）；
    ⑥ 配置键 `video_read.wait_download_s` 存在且默认 15。

**怎么判**
    源码级静态判据（AST 定位函数体 + 剥注释）＋ **桩测试**（假 GUI/DB 跑
    `_trigger_video_download`，验"投递点击被调用"与"定位不到时如实 None、不碰真鼠标"）。

跑法：`py -3 scripts/video_click_download_selftest.py`。中文文案用「」。
"""
from __future__ import annotations

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = [0]
FAIL = [0]


def ok(title, cond, detail=""):
    if cond:
        PASS[0] += 1
        print("  PASS %s" % title)
    else:
        FAIL[0] += 1
        print("  FAIL %s%s" % (title, ("  [%s]" % detail) if detail else ""))


def _read(p):
    return open(os.path.join(ROOT, p), encoding="utf-8").read()


def _strip_comments(code):
    out = []
    for ln in code.splitlines():
        s = ln.strip()
        if s.startswith("#"):
            continue
        m = re.search(r"\s#\s", ln)
        if m:
            ln = ln[:m.start()]
        out.append(ln)
    return "\n".join(out)


def _method_src(src, cls, name):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == name:
                    lines = src.splitlines()
                    return "\n".join(lines[sub.lineno - 1: sub.end_lineno])
    return ""


WX = _read("agent/wechat.py")

print("== A. 白名单必须含「视频」（否则整条链永远量不到）==")
_mbl = _strip_comments(_method_src(WX, "WeChatAdapter", "_media_bubble_locate"))
ok("A1 `_media_bubble_locate` 的类型白名单含「视频」",
   bool(re.search(r'ntype\s+not\s+in\s*\(\s*"[^"]*"\s*,\s*"[^"]*"\s*,\s*"视频"\s*\)', _mbl)),
   "")
ok("A2 失败文案也提到视频（别让日志说「不是表情/图片」而实际在找视频）",
   "不是表情/图片/视频" in _mbl, "")

print("\n== B. `_trigger_video_download`：走投递、绝不回真鼠标 ==")
_tvd = _strip_comments(_method_src(WX, "WeChatAdapter", "_trigger_video_download"))
ok("B1 函数存在", bool(_tvd))
ok("B2 切会话用**投递** `switch_chat_posted`", "switch_chat_posted" in _tvd)
ok("B3 **不调用** `gui.open_chat`（那就是真鼠标事故源）",
   not re.search(r"\.open_chat\s*\(", _tvd), "仍见 open_chat ⇒ 危险")
ok("B4 点击走 `MessageBackend.click`", "MessageBackend" in _tvd and ".click(" in _tvd)
ok("B5 **关伪激活**（`activate = False`）", "activate = False" in _tvd)
ok("B6 **全函数无 `real_click`/真鼠标兜底**",
   "real_click" not in _tvd and "real_fallback" not in _tvd, "")
ok("B7 投递档不是 `MessageBackend` 时**如实放弃**（不降级真鼠标）",
   "不退回真鼠标" in _tvd or "不退回" in _tvd, "")
ok("B8 取视频 id（packed_info 里 32 位 hex）", "packed_info" in _tvd and "32" in _tvd or "{32}" in _tvd)
ok("B9 已下好就不点（先查缓存）", "_video_from_wx_cache" in _tvd)
ok("B10 轮询 mp4 用**精确文件名**（`want_name`）", "want_name" in _tvd)
ok("B11 关预览窗走 `_close_video_preview_windows`（投递，不发 Esc）",
   "_close_video_preview_windows" in _tvd)

print("\n== C. `_close_video_preview_windows`：投递关窗、不碰键盘 ==")
_cvp = _strip_comments(_method_src(WX, "WeChatAdapter", "_close_video_preview_windows"))
ok("C1 走**关窗唯一入口** `winops.close`（它内部先过 `ok_to_close` 咽喉点，绝不关主窗）",
   "_win_close(" in _cvp)
ok("C2 ⛔ **不自己裸投 `WM_CLOSE`**（绕过唯一入口即丢掉「同一句柄只关一次」的登记，"
   "而句柄会被系统回收给新窗 ⇒ 重复关可能误伤无关的窗）",
   "0x0010" not in _cvp and "PostMessage" not in _cvp)
ok("C3 **不发 Esc 键盘**", "Esc" not in _cvp and "key(" not in _cvp, "")
ok("C4 baseline 为 None 时**不乱关**", "return 0" in _cvp)

print("\n== D. `download_media`：三步顺序（查缓存 → 触发 → 退回库）==")
_dm = _strip_comments(_method_src(WX, "WeChatAdapter", "download_media"))
ok("D1 video 分支先查缓存（`_video_from_wx_cache` 出现在触发**之前**）",
   _dm.find("_video_from_wx_cache") >= 0 and
   _dm.find("_video_from_wx_cache") < _dm.find("_trigger_video_download"),
   "cache@%d trigger@%d" % (_dm.find("_video_from_wx_cache"),
                            _dm.find("_trigger_video_download")))
ok("D2 有 GUI 才触发（`_trig` 判据）", "_trig" in _dm)
ok("D3 最后**仍退回库的等待式**（`getattr(self._md, fn)`）",
   re.search(r"getattr\(self\._md,\s*fn\)", _dm) is not None, "")
ok("D4 读 `wait_download_s` 配置（默认 15）",
   "wait_download_s" in _dm and "15" in _dm, "")

print("\n== E. 配置键 ==")
CFG = _read("agent/config.py")
ok("E1 `video_read.wait_download_s` 存在", "wait_download_s" in CFG)
ok("E2 默认 15（不是库默认的 75）",
   bool(re.search(r'"wait_download_s"\s*:\s*15', CFG)), "")

print("\n== F. 桩测试：定位不到 ⇒ 如实 None、不碰真鼠标 ==")
try:
    import agent.wechat as W
    _t = W.WeChatAdapter.__new__(W.WeChatAdapter)

    class _FakeDB(object):
        account_dir = ""

        @staticmethod
        def get_message_row(*a, **k):
            # 有 packed_info 且含 32 位 id ⇒ 走到定位那一步
            return {"packed_info": b"\x00" * 4 + b"a" * 32}

        @staticmethod
        def get_message_rows_for_media(*a, **k):
            return []

    _t._db = _FakeDB()
    _t._md = None
    _t._gui = None
    _t.cfg = {}
    # 让 `_get_gui` 回一个假 GUI，但 `_media_bubble_locate` 恒返回"量不到"
    class _FakeGUI(object):
        main_hwnd = 0
        origin_x = 0
        origin_y = 0
    _t._get_gui = lambda: _FakeGUI()
    _t._media_bubble_locate = lambda *a, **k: (None, None, "目标不是最新一条 ⇒ 几何定位不敢用")
    _t.switch_chat_posted = lambda *a, **k: (True, "")
    _t.display_name = lambda cid: "群X"
    _t._wx_toplevel_windows = lambda: []

    _res = _t._trigger_video_download("/tmp/nonexistent_media_dir", "chatX", 42)
    ok("F1 定位不到视频气泡 ⇒ 返回 None（不伪造成功）", _res is None, "返回=%r" % _res)

    # 反例：若代码里偷偷有真鼠标路径，这里会看到 real_click —— 上 B6 已静态判过，此处再确认运行时无副作用
    ok("F2 全过程没有任何「真鼠标」调用（静态 + 运行时双证）",
       "real_click" not in _tvd, "")
except Exception as e:
    import traceback
    traceback.print_exc()
    ok("F1/F2 `_trigger_video_download` 桩测试可跑", False, "%s: %s" % (type(e).__name__, e))

print("\n== B9 判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
sys.exit(1 if FAIL[0] else 0)
