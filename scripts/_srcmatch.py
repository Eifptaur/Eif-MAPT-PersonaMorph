# -*- coding: utf-8 -*-
"""判据共用件：**对源码文本做"空白容忍"的包含判断**。

为什么要有它（2026-09-21，第四轮审计 **V-R4-13** 第三条）：判据里有大量
`"某段源码" in SRC` 的写法，其中**带缩进/换行的整段**一改格式（多一个空格、换行位置变了）
就红 —— 审计统计 **80/129 个判据文件**都有这类脆断言（前 26 个文件共 379 条），
典型例子是 `background_selftest.py` 里 `"bbox[0] + 4 <= x <= bbox[2] - 4" in SRC_WECHAT`
（源码里换个行距就红，而行为其实没变）。

用法：
    from _srcmatch import has, norm
    if has(SRC, "def foo(a, b):", "return a + b"):   # 多段：**顺序出现**即可，空白/缩进/换行都不计较
        ...

口径：**只归一化空白**（把连续的空白/换行折成一个空格并去首尾），不碰其它字符 ——
所以它仍然能钉住代码顺序与内容，只是不再被格式（改缩进、换行、对齐）影响。
"""
from __future__ import annotations

import re

_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    """把连续空白折成单个空格并去首尾（其它字符原样保留）。"""
    return _WS.sub(" ", str(s or "")).strip()


def has(hay: str, *frags: str) -> bool:
    """`frags` 是否**按顺序**出现在 `hay` 里（空白容忍）。任一段为空则忽略该段。"""
    t = norm(hay)
    at = 0
    for f in frags:
        n = norm(f)
        if not n:
            continue
        i = t.find(n, at)
        if i < 0:
            return False
        at = i + len(n)
    return True


def count(hay: str, *frags: str) -> int:
    """`frags` 按顺序出现的位置数（沿用 `str.count` 的"不重叠"语义，空白容忍）。"""
    t = norm(hay)
    first = [f for f in frags if norm(f)]
    if not first:
        return 0
    n = 0
    at = 0
    while True:
        i = t.find(norm(first[0]), at)
        if i < 0:
            return n
        ok_all = True
        j = i + len(norm(first[0]))
        for f in first[1:]:
            k = t.find(norm(f), j)
            if k < 0:
                ok_all = False
                break
            j = k + len(norm(f))
        if ok_all:
            n += 1
        at = i + 1
