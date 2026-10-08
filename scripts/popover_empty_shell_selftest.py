# -*- coding: utf-8 -*-
"""判据：搜索浮层的**空白外壳**必须被识别，且**绝不许拿用户自己的搜索历史当结果行点**。

跑法： runtime\\python\\python.exe scripts\\popover_empty_shell_selftest.py   退出码 0=全过 / 1=有失败

为什么要这条判据（真机现场 `wechatauto_logs\\fail\\20261004-130431_search_popover_row`）：
  用户把搜索框拖窄后，微信**会留下上一次没搜完的浮层**——打开时就是这幅样子（实测 552×312）：
    只有「搜索网络结果 / 文章、公众号、视频号等 / 最近在搜 / <两条历史>」，
    **一条真实联系人结果都没有**。
  而 `find_popover_row` 的兜底口径是"认到任一段标题就取它下面第一行"，而「最近在搜」在
  `POPOVER_SECTIONS` 里 ⇒ 它会**认到「最近在搜」并取它下面第一行** ——
  那是**用户自己的 wxid 搜索历史**（现场读到 `wxid_sample0007` / `10000000005@chatroom`）
  ⇒ 点下去什么会话都切不到 ⇒ 切会话永远失败 ⇒ **消息发不出去**，而且用户看不出原因。

三条闸（本判据逐条锚）：
  ① `popover_is_empty_shell` —— 认出空白外壳（「搜索网络结果」+「最近在搜」+ 副标题 + 位于浮层顶部）
  ② 「最近在搜」**不当结果段**（`find_popover_row` 的段扫描显式跳过它）
  ③ 历史条目（群号/wxid）**不当落点**（`_popover_history_row`）
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ⛔ 隔离：判据不许写产品 data/ 与 logs/。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _iso14 # noqa: E402
_iso14.wechat()
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import re # noqa: E402

from agent import chat_ocr as CO # noqa: E402

WECHAT_PY = os.path.join(ROOT, "agent", "wechat.py")

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  OK   " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


print("① 「最近在搜」标题的**宽容判据**（OCR 会把「搜」读成「瞍」）")
# 实测：同一次识别里它被读成 `最近在瞍` ⇒ 只按字面 `"最近在搜" in s` 会漏。
#   口径：前两字严格「最近在」+ 整段很短。
ok("真值「最近在搜」命中", CO._hit_recent_title("最近在搜") is True)
ok("★ 实测认岔的「最近在瞍」**也要**命中（这是本判据存在的理由）",
   CO._hit_recent_title("最近在瞍") is True)
ok("反例：「联系人」段标题**不许**命中", CO._hit_recent_title("联系人") is False)
ok("反例：「群聊」段标题**不许**命中", CO._hit_recent_title("群聊") is False)
ok("反例：长句子以「最近在」开头**不许**命中（防『整段很短』那条失效）",
   CO._hit_recent_title("最近在消息里说过的话很长很长") is False)
ok("反例：空串 / None 不炸、不命中",
   CO._hit_recent_title("") is False and CO._hit_recent_title(None) is False)
ok("反例：「搜索网络结果」不许命中（它不是段标题）",
   CO._hit_recent_title("搜索网络结果") is False)

print("\n② 历史条目识别：群号 / wxid ⇒ **不当落点**")
ok("群号 `10000000005@chatroom` 判为历史（实测现场读数）",
   CO._popover_history_row("10000000005@chatroom") is True)
ok("wxid `wxid_sample0007` 判为历史（实测现场读数）",
   CO._popover_history_row("wxid_sample0007") is True)
ok("OCR 带括号前缀 `(Dwxiduk12ti5eyhu029` 也判为历史（实测真读成这个）",
   CO._popover_history_row("(Dwxiduk12ti5eyhu029") is True)
ok("OCR 带括号前缀 `(DS10000000005@chatroom` 也判为历史",
   CO._popover_history_row("(DS10000000005@chatroom") is True)
# ⚠️ 反向对照：**正常会话名绝不许被误判**（误判 ⇒ 真的会话搜出来也点不了）。
for _nm in ("文件传输助手", "aaa偷啃使用者", "E", "群deepseek", "腾讯新闻",
            "海绵宝宝的微笑", "微信团队", "宋孟", "张三（3）"):
    ok("反例：正常会话名 %r **不许**被判成历史" % _nm,
       CO._popover_history_row(_nm) is False)

print("\n③ 源锚：三条闸**真的接进**了 find_popover_row（不只是定义了函数）")
_src = open(os.path.join(ROOT, "agent", "chat_ocr.py"), encoding="utf-8").read()


def _func_src(src: str, name: str) -> str:
    """粗切一个顶层函数的源码段（到下个顶层 def 为止）。"""
    m = re.search(r"^def %s\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    rest = src[m.start():]
    m2 = re.search(r"\n(?=def |class |@)", rest[10:])
    return rest if not m2 else rest[:10 + m2.start()]


def _code_only(seg: str) -> str:
    """剥掉**注释 + docstring**，只留可执行代码。

    ⛔ 为什么必须剥 docstring（不是只剥 `#`）：本项目大量"讲这条规矩"的话写在 docstring 里，
       而 `popover_is_empty_shell` 的 docstring 里**明说**了"绝不用 recognize_union"
       ⇒ 不剥 docstring 会把**解释**判成**违规**（本项目老坑「判据别把注释当违规」的同类）。
    """
    out = []
    in_doc = False
    for l in seg.split("\n"):
        s = l.strip()
        if not in_doc and (s.startswith('"""') or s.startswith("'''")):
            q = s[:3]
            if s.count(q) >= 2 and len(s) > 3:
                continue # 单行 docstring
            in_doc = True
            continue
        if in_doc:
            if s.endswith('"""') or s.endswith("'''"):
                in_doc = False
            continue
        if s.startswith("#"):
            continue
        out.append(l)
    return "\n".join(out)


_fpr = _func_src(_src, "find_popover_row")
ok("find_popover_row 里**调用**了 popover_is_empty_shell（闸①接线）",
   "popover_is_empty_shell(" in _fpr)
ok("find_popover_row 里**调用**了 _popover_history_row（闸③接线）",
   "_popover_history_row(s)" in _fpr)
# ⛔ 闸②：段扫描里「最近在搜」必须被显式跳过（否则它下面第一行＝用户历史）。
ok("找段时**显式跳过** POPOVER_RECENT_TITLE（闸②接线）",
   "k == POPOVER_RECENT_TITLE" in _fpr and "continue" in _fpr)
# 剥注释再验：注释里会讲这条规矩，但**不许**只有注释、没有代码。
_fpr_code = _code_only(_fpr)
ok("★ 剥掉注释后闸①②③**仍在代码里**（防『只写在注释里』）",
   "popover_is_empty_shell(" in _fpr_code
   and "_popover_history_row(s)" in _fpr_code
   and "k == POPOVER_RECENT_TITLE" in _fpr_code)

print("\n④ 源锚：`wechat.py` 的 box 路线**用上了**它（空白外壳 ⇒ 重打一次）")
_w = open(WECHAT_PY, encoding="utf-8").read()
ok("open_chat_by_search 里调用了 popover_is_empty_shell",
   "popover_is_empty_shell" in _w)
ok("★ 空白外壳 ⇒ **重打一次**查询词（不是拿历史当结果）",
   "重打一次" in _w and "popover_is_empty_shell(_pop2[2])" in _w)
ok("重打**只做一次**（不许无限循环戳用户微信）",
   _w.count("清空后**重打一次**") == 1)
ok("重打仍无结果 ⇒ 如实返回失败（fail-closed，不许退回『主窗里找行』拿历史当结果）",
   "仍只有「最近在搜」历史、没有真实结果行" in _w)

print("\n⑤ 代价口径：这条闸**不许**把 RapidOCR 拉进发送链")
# `recognize_union` 的 docstring 明令"不要放进每笔操作的发送链"（RapidOCR 整图 5s+）。
_shell = _func_src(_src, "popover_is_empty_shell")
_shell_code = _code_only(_shell)
ok("popover_is_empty_shell 用的是 recognize（内置），**不是** recognize_union",
   "recognize(img)" in _shell_code and "recognize_union" not in _shell_code)

print("\n⑥ 真机帧回归：现场那张浮层帧必须判「空白外壳」且**取不出行**")
_shot = os.path.join(ROOT, "wechatauto_logs", "fail",
                     "20261004-130431_search_popover_row", "shot.png")
if os.path.exists(_shot):
    from PIL import Image # noqa: E402
    _im = Image.open(_shot).convert("RGB")
    ok("现场帧尺寸 552×312（与现场记录一致）", _im.size == (552, 312), str(_im.size))
    _sh, _why = CO.popover_is_empty_shell(_im)
    ok("★ 现场帧判为**空白外壳**（%s）" % _why[:40], _sh is True)
    ok("★ 现场帧 `find_popover_row` 返回 **None**（不拿用户历史当结果行）",
       CO.find_popover_row(_im, "文件传输助手") is None)
    ok("现场帧对**任何**名字都不许取出行（含历史里的 wxid）",
       CO.find_popover_row(_im, "wxid_sample0007") is None
       and CO.find_popover_row(_im, "10000000005@chatroom") is None)
else:
    print("  （现场帧不在本机 ⇒ 跳过 ⑥，只跑静态锚）")

print("\n⑦ 反向对照：**有真实结果**的帧不许被判成空白外壳（防把正常搜索砍死）")
for _nm, _p in (("20261004-130046_search_entry", (933, 926)),
                ("20261004-130313_search_entry", (933, 926)),
                ("20260927-165014_search_box_row", (1449, 1367))):
    _f = os.path.join(ROOT, "wechatauto_logs", "fail", _nm, "shot.png")
    if not os.path.exists(_f):
        print("  （缺帧 %s ⇒ 跳过）" % _nm)
        continue
    from PIL import Image as _I # noqa: E402
    _i2 = _I.open(_f).convert("RGB")
    _s2, _w2 = CO.popover_is_empty_shell(_i2)
    ok("主窗帧 %s（%s）**不许**判成空白外壳" % (_nm[:20], _i2.size), _s2 is False)

print("\n结果：%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for _f in FAIL:
        print("  - " + _f)
sys.exit(1 if FAIL else 0)
