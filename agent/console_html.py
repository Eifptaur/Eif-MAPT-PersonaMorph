# -*- coding: utf-8 -*-
"""Web 控制台界面模板的**加载器**（数据真身在 `assets/console/index.html`）。

为什么模板不在这个 `.py` 里：
  · 它是**一页 HTML+CSS+JS**（约 45 万字符 / 8000+ 行），放 `.py` 里就是"数据 in 代码"——
    改一行样式要在 Python 语法里编辑 HTML，任何符号写错都会让整个模块 import 失败；
  · 外置成 `.html` 之后它是**普通数据文件**：编辑器按 HTML 高亮、可以逐段 diff、
    隐私/文案扫描器能把它当文本扫（不必按 Python 语法解析）。

⛔ **行号对齐（本迁移唯一容易错的地方，改这个文件之前务必读）**：
  数据文件**前 10 行是占位**，目的只有一个 —— 让它的行号与**迁移前**的 `agent/console_html.py`
  **逐行相同**（旧文件里 `HTML = r\"\"\"` 在第 11 行，模板内容从第 11 行开始）。
  ⇒ 全仓的坐标注释绝大多数**只写行号**（形如 `web :3346`、`:1444-1495`，实测约 1344 处），
  **不带文件名** ⇒ 它们不用改，也**不能改数字**（改了就对不上）。
  把文件名与行号写在一起的只有几处，已统一换成 `assets/console/index.html`（**零偏移**）。
  ⇒ 因此：**不要增删数据文件的前 10 行**，也不要在它前面插内容。

`HTML` 的值与迁移前**逐字节相同**（迁移时用 `ast` 取旧字面量、与加载结果做过 `==` 比对）。
"""

import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ASSET = os.path.join(_ROOT, "assets", "console", "index.html")

#: 数据文件里**行号占位**的行数（见文件头"行号对齐"一节）—— 必须与数据文件保持一致。
_SKIP_LINES = 10

with open(_ASSET, encoding="utf-8") as _fh:
    _lines = _fh.readlines()

#: 控制台页面的完整模板（读法保持"逐字节原样"：用 `readlines()` 保住每行自带的换行符，
#: 不做 strip/join 之类的加工，否则首尾空白与行尾会变）。
HTML = "".join(_lines[_SKIP_LINES:])
