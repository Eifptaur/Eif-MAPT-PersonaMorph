# -*- coding: utf-8 -*-
"""判据共用件：**按 AST 取"某个函数的源码"**，不要用文本边界。

为什么要有它：判据里有大量这种写法 ——

    _seg = SRC[SRC.index("def send_text_posted("):]
    _seg = SRC[SRC.index("def send_text_posted("):SRC.index("def send_image_posted(")]
    body = SRC.split("def emoji_panel_open(")[1][:3000]

它们靠"某个 `def` 行在文件里的**位置**"来定边界。项目做过多次"把整块函数搬到模块级"的
**等价搬家**（文件里函数的位置因此改变）⇒ 这类写法就会：

  · **直接崩** —— 锚点被搬到起点之前，`index` 抛 `ValueError`（判据整条红）；或者
  · **静默切错** —— 锚点还在，但切出来的区间已经不是那个函数了（**不报错**，
    判据的含义悄悄变了，甚至因为别处的文字而"假绿"）。

本件把这件事收敛到一处：**用 `ast` 求函数的真实跨度**。找不到就抛 `LookupError`
（**响亮地失败**，绝不静默返回空串 —— 空串会让后续断言全部假绿）。

用法（判据里 `import _srcslice as _ss`）：
    _ss.func_src(SRC, "send_text_posted")   # 该函数的精确正文（含 def 行与 docstring；方法也在内）
    _ss.from_func(SRC, "main")              # 从它的 def 行到文件末尾（保留原来"开区间"的语义）
    _ss.before_func(SRC, "main")            # 它之前的全部内容（保留原来"前缀"的语义）
同名函数有多个时用 `ordinal=` 指定第几个（按出现顺序）。

⛔ 与 `_srcmatch` 的分工：`_srcmatch` 解决"**空白/格式**变化"（`has` / `norm`）；
本件解决"**函数位置**变化"。两者都要用：`_ss.func_src(...)` 拿到段落，再交给 `_sm.has(...)` 断言。
"""
from __future__ import annotations

import ast

__all__ = ["func_node", "func_src", "from_func", "before_func", "func_names"]


def _funcs(src: str):
    tree = ast.parse(src)
    out = [n for n in ast.walk(tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    out.sort(key=lambda n: n.lineno)
    return out


def func_names(src: str):
    """源码里所有函数/方法名（按出现顺序，可能重名）。"""
    return [n.name for n in _funcs(src)]


def func_node(src: str, name: str, ordinal: int = 0):
    """→ 该名字对应的 AST 节点（不限层级：模块级函数与类方法都能查到）。找不到抛 `LookupError`。"""
    hits = [n for n in _funcs(src) if n.name == name]
    if not hits:
        raise LookupError("源码里没有名为 %r 的函数（锚点名字写错了？）" % (name,))
    if ordinal >= len(hits):
        raise LookupError("%r 只出现 %d 次（要第 %d 个）" % (name, len(hits), ordinal))
    return hits[ordinal]


def func_src(src: str, name: str, ordinal: int = 0) -> str:
    """该函数的**精确正文**：含 `def` 行（不含装饰器行）与 docstring，到函数最后一行为止。"""
    n = func_node(src, name, ordinal)
    return "\n".join(src.split("\n")[n.lineno - 1:n.end_lineno])


def from_func(src: str, name: str, ordinal: int = 0) -> str:
    """从该函数的 `def` 行到**文件末尾**（原来是 `SRC[SRC.index("def X"):]` 的写法）。"""
    n = func_node(src, name, ordinal)
    return "\n".join(src.split("\n")[n.lineno - 1:])


def before_func(src: str, name: str, ordinal: int = 0) -> str:
    """该函数 `def` 行**之前**的全部内容（原来是 `SRC[:SRC.index("def X")]` 的写法）。"""
    n = func_node(src, name, ordinal)
    return "\n".join(src.split("\n")[:n.lineno - 1])
