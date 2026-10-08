#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「最小化档发文件」接链判据 —— 守住本轮新增的那一支

背景：主链 `wechat.send_file_posted` 原先只有一条路 —— 「投递点工具栏『文件』图标 →
系统『选择文件』对话框 → UIA 填名 → 发送」。那条路**要求主窗可见**（会话闸靠主窗 OCR 判
"目标会话已打开"），而且**会弹出系统对话框**打断用户 ⇒ 窗口最小化时**必然走不通**。

本轮补的是：**独立窗 + 最小化档 ⇒ `send_file_blind`（剪贴板 `CF_HDROP` ＋ 盲粘贴 ＋ 三枪 ＋ DB 回读）**，
判据全部**不依赖画面**（最小化抓不到帧，一切画面类判据在这里恒为假）。

本判据守住四件最容易悄悄坏掉的事：
  ① `send_image_to_popup` 的 `is_image` 参数**真的落实了**（`is_image=False` ⇒ 强制 `CF_HDROP`）
     —— 若退回"只看这个文件会不会动"，`.zip`/`.txt` 会被判成静态 ⇒ 走位图通道（`set_image`）
     ⇒ 放不进剪贴板、或放进去也不是那个文件；
  ② `send_file_blind` 存在，且**确实以 `is_image=False`** 调下层；
  ③ 主链那一支**只在最小化档接管**（条件里必须有 `TIER_MINIMIZED`）
     —— 不许无条件接管，否则会改掉可见档的既有行为（零回归是这条链的前提）；
  ④ 那一支的返回值**不许宣称"已送达"**（默认不等 DB 落库 ⇒ 只能说"已投递"）。
另有一条反向锚：**已停用**的 `send_file_to_popup` 首行仍自述"已停用" ——
防止有人把它当成"现成入口"接回主链（它走的是已判死的 `WM_PASTE`）。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _srcmatch as _sm  # noqa: E402  空白容忍的源码断言

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


_SRC_WW = open(os.path.join(_ROOT, "agent", "wechat_windows.py"), encoding="utf-8").read()
_SRC_WX = open(os.path.join(_ROOT, "agent", "wechat.py"), encoding="utf-8").read()


def _defbody(src, head, stop_at_class_method=True):
    """取一个**顶层函数**的函数体（到下一个顶层 `\\ndef ` 或 `\\n@` 为止）。"""
    i = src.index(head)
    j = src.find("\ndef ", i + len(head))
    if j < 0:
        j = len(src)
    return src[i:j]


def _methodbody(src, head):
    """取一个**类方法**的函数体（到下一个同级 `\\n    def ` 为止）。"""
    i = src.index(head)
    j = src.find("\n    def ", i + len(head))
    return src[i:j if j > 0 else len(src)]


print("── A. `send_image_to_popup`：`is_image=False` 必须强制走文件通道 ──")
_segA = _defbody(_SRC_WW, "def send_image_to_popup(")
ok("签名里有 `is_image` 开关", "is_image: bool = True" in _segA or "is_image" in _segA)
ok("`is_image=False` ⇒ `_anim` 强制为真（走 CF_HDROP，不再看文件会不会动）",
   _sm.has(_segA, "if not is_image:") and _sm.has(_segA, "_anim = True"))
ok("剪贴板通道名区分出「文件」（回执里看得出走的是哪条）",
   "CF_HDROP(文件)" in _segA)
ok("静态图那条路没被改坏（仍是「会不会动」判据 + CF_DIB）",
   _sm.has(_segA, "is_animated_file(local_path)") and "CF_DIB(静态)" in _segA)

print("── B. `send_file_blind`：存在，且以 `is_image=False` 复用下层 ──")
ok("函数已定义", "def send_file_blind(" in _SRC_WW)
_segB = _defbody(_SRC_WW, "def send_file_blind(")
ok("确实传 `is_image=False`（不是另抄一份实现）",
   _sm.has(_segB, "is_image=False"))
ok("返回值带 `as_file` 标记（调用方/回执能区分）",
   _sm.has(_segB, 'as_file') and "_r[\"as_file\"] = True" in _segB)
ok("docstring 写明「发出去的是什么取决于微信对 CF_HDROP 的归类」（不许声称已验证）",
   "尚未真机标定" in _segB or "取决于微信对" in _segB)

print("── C. 主链 `send_file_posted`：只在最小化档接管 ──")
_segC = _methodbody(_SRC_WX, "def send_file_posted(")
ok("调用了 `send_file_blind`", "send_file_blind" in _segC)
ok("接管条件是 `popup_tier(...) == TIER_MINIMIZED`（**不许**无条件接管）",
   _sm.has(_segC, "popup_tier(") and "TIER_MINIMIZED" in _segC)
ok("先找独立窗再判档（`find_popup` 在条件里）", _sm.has(_segC, "find_popup("))
ok("接管点排在 `_repeat_guard` **之后**（重复发送保护不许被绕过）",
   _segC.index("_repeat_guard") < _segC.index("send_file_blind"))
ok("可见档原路仍在（工具图标 → 选择文件对话框那条没被删）",
   "文件图标" in _segC or "选择文件" in _segC)

print("── D. 返回值口径：不等 DB 就只能说「已投递」──")
ok("`db_verified is None` 时文案是「已投递（未等 DB 核实）」",
   "已投递（未等 DB 核实）" in _segC)
ok("那一支里**不许**出现「已送达」（没等落库就宣称送达＝假成功）",
   "已送达" not in _segC.split("send_file_blind")[1][:1500])

print("── E. 反向锚：已停用的 `send_file_to_popup` 不许被接回主链 ──")
ok("它的 docstring 首行仍自述「已停用」",
   "【已停用 / 重复实现】" in _SRC_WW)
ok("主链没有调用它（`send_file_posted` 段里不得出现 `send_file_to_popup`）",
   "send_file_to_popup(" not in _segC.replace("send_file_blind", ""))

print("== [minimized-file-send] 判据：{} 通过 / {} 失败 ==".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
