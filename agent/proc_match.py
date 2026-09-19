# -*- coding: utf-8 -*-
"""「这个进程属于本安装吗」的**唯一判据**（2026-09-20 立，V-R1-3）。

为什么单开一个模块：`taskkill /F` 是强制终止，判据错了会**杀掉别人的进程** —— 实测过三种：
别人项目里的 `watchdog.py`、另一份解压目录里的群相副本、`D:\\tools\\onestart.py`。
本判据被两处共用（**一处实现、两处调用**，别各写一套）：
  · `scripts/onestart.py::_kick_old_instance` —— 一键启动时踢掉旧实例；
  · `scripts/stop_bot.py::kill_by_cmdline`   —— 一键关闭时按命令行兜底找进程。

判据＝把命令行里"可能是路径的那一段"回溯出来，`normcase(abspath())` 必须落在**本安装 ROOT** 下，
且文件名属于 `OWN_SCRIPT_NAMES`。路径比较一律 `normcase(abspath())`（盘符/大小写/`\\` 与 `/` 混写
都算同一个目录）。
"""
from __future__ import annotations

import os

#: 属于我们自己的三个脚本（命令行里出现它们才算候选）
OWN_SCRIPT_NAMES = ("persona_morph.py", "watchdog.py", "onestart.py")


def our_root(explicit: str = "") -> str:
    """本安装根目录（默认＝本文件的上上级，即 persona-morph 根）；规范化后返回。"""
    r = explicit or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.normcase(os.path.abspath(r)).rstrip("\\/")


def script_paths(cmd: str, names=OWN_SCRIPT_NAMES) -> list:
    """从一条命令行里抽出"指我们的那几个脚本"的**路径 token**（按脚本名回溯到路径起点）。

    只负责"把可能是路径的那一段抠出来"；是否真属于本安装由 `is_our_install` 判。
    """
    out = []
    s = str(cmd or "")
    for name in names:
        i = s.find(name)
        while i >= 0:
            j = i
            # 往回走到这个 token 的起点：引号/空白/逗号分号（wmic CSV 行）都算分隔符
            while j > 0 and s[j - 1] not in "\"' \t,;=|":
                j -= 1
            tok = s[j:i + len(name)]
            if tok:
                out.append(tok)
            i = s.find(name, i + len(name))
    return out


def is_our_install(cmd: str, root: str = "") -> bool:
    """这条命令行的**脚本完整路径**是否落在本安装目录下、且文件名是那三个之一。"""
    r = our_root(root)
    for tok in script_paths(cmd):
        try:
            p = os.path.normcase(os.path.abspath(tok))
        except Exception:
            continue
        if not p.startswith(r + os.sep):
            continue                      # ← 不在本安装目录下：**别人的进程，一个都不许碰**
        if os.path.basename(p) in OWN_SCRIPT_NAMES:
            return True
    return False
