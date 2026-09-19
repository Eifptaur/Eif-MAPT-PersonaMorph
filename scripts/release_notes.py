# -*- coding: utf-8 -*-
"""群相 · 发布说明写法（**单一事实源**）。

作者 2026-09-20 定的规矩（原话，逐字）：

    「现在去把 GitHub 上的所有说明改了，以后也是如此，之前的也要改。
      修 Bug 就只用说『修复了一些 bug』这句话，最多也就一句话，简单描述，
      不要把什么东西都全部列出来了。新增功能倒是可以详细说说。」

⇒ 拆成两条**可机械检查**的规则（本文件是唯一实现，`make_manifest.py` 与发版脚本都调它）：

  ① 说明里**出现「新增」类字样** ⇒ 认定这一版**有新增功能** ⇒ 允许详细写（不检查长度）；
  ② 没有 ⇒ 认定这一版**只是修 bug** ⇒ **只许一句**：
     · 公告要点（`announce.notes`）只许 **1 条**；
     · 正文（Release body）只许 **1 行**；
     · 且不超过 `MAX_FIX_CHARS` 字、不含 `；`/`;`/`·`/换行/圈码这类**列举标记**
       （出现了就说明在"把东西全部列出来"，正是要禁的形态）。

推荐写法就是那一句：`修复了一些 bug`（`FIX_ONLY_LINE`）。

自检：`py -3 scripts/release_notes_selftest.py`（阴/阳对照都在里面）。
"""
import re

FIX_ONLY_LINE = "修复了一些 bug"

#: 出现任一即视为"这一版有新增功能"⇒允许详细写
FEATURE_MARKS = ("新增", "新功能", "新能力", "新面板", "新选项", "新渠道", "新后端")

#: 纯修 bug 版本的说明长度上限（字）
MAX_FIX_CHARS = 24

#: 纯修 bug 版本里不许出现的"列举标记"（把东西一条条列出来正是要禁的）
_LIST_MARKS = re.compile(r"[；;·\n\r]|[①-⑳]|[（(]\s*\d+\s*[)）]|^\s*\d+[.、)]", re.M)


def has_feature(text: str) -> bool:
    """说明里有没有"新增功能"的迹象。"""
    t = text or ""
    return any(m in t for m in FEATURE_MARKS)


def _judge(text: str, count: int, label: str) -> list:
    """count＝这一版说明的"句/条"数（公告要点条数，或正文行数）。返回问题清单。"""
    t = (text or "").strip()
    if not t:
        return []                      # 空由调用方各自决定是否算问题（发版脚本要求非空）
    if has_feature(t):
        return []                      # 有新增功能 ⇒ 可以详细写，不设检查
    bad = []
    if count > 1:
        bad.append("%s 没有新增功能，只许一句（现在 %d 句）" % (label, count))
    n = len(t)
    if n > MAX_FIX_CHARS:
        bad.append("%s 没有新增功能，说明不得超过 %d 字（现在 %d 字）：%s"
                   % (label, MAX_FIX_CHARS, n, t[:40]))
    m = _LIST_MARKS.search(t)
    if m:
        bad.append("%s 没有新增功能，不许列举（命中了 %r）：%s" % (label, m.group(0), t[:40]))
    return bad


def note_problems(notes, label: str = "announce.notes") -> list:
    """检查公告要点（list[str]）。空列表⇒不判（调用方自定）。"""
    items = [str(s).strip() for s in (notes or []) if str(s).strip()]
    if not items:
        return []
    return _judge("；".join(items), len(items), label)


def text_problems(text: str, label: str = "Release 正文") -> list:
    """检查一段文本（Release 正文 / 更新公告正文）。按非空行数算"句数"。"""
    t = (text or "").strip()
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    return _judge(t, max(1, len(lines)), label)


def problems(*, notes=None, text: str = "", label: str = "发布说明") -> list:
    """一次查两边（只给哪边就查哪边）。空＝通过。"""
    bad = []
    if notes is not None:
        bad += note_problems(notes, label + "（公告要点）")
    if text:
        bad += text_problems(text, label + "（正文）")
    return bad
