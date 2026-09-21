# -*- coding: utf-8 -*-
"""统一 JSON 落盘 / 坏档隔离（审计第九轮 **V-R9-18 · V-R9-19 · V-R9-20 · V-R9-22**）。

**为什么要有这一个模块**：`data/` 下 19 个 JSON 落盘点原先各写一份，读侧一律 `except: 默认值`、
写侧一律 `path + ".tmp"` 再整体覆盖 ⇒ 审计实测"**坏文件 + 一次写入 = 旧数据全没**"（10 个点实证；
memory 并发写 200 次出 **46 个坏档**）。收口到一处，才能保证"坏档一律留证、写盘一律原子"。

两条口径（都写在下面的函数 docstring 里，改之前先读）：

1. **坏档改名留证，绝不删** —— `load_or_quarantine()`
2. **临时名带 pid + 随机后缀，`os.replace` 换上去** —— `atomic_write_json()`
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time

log = logging.getLogger("persona-morph")


def quarantine(path: str, now=None) -> str:
    """把坏档**改名**成 `<path>.bad.<YYYYmmdd-HHMMSS>` 留证；返回留证文件的路径（失败回空串）。

    **为什么是改名而不是删除**（V-R9-18 的口径）：
      坏文件里往往还有**用户数据**——人工能修回来、能看出是哪个版本写坏的、出事时能对上审计。
      删掉就只剩"数据没了"这一句结论，而且下游会拿着默认值继续跑、继续整体覆盖 ⇒
      这正是审计点的"**坏文件 + 一次写入 = 旧数据全没**"。所以：一个字节都不丢，只换个名字。
      改名失败（文件被独占/无权限）时**保持原样不删**，把失败本身也记进日志。
    """
    if not path or not os.path.exists(path):
        return ""
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(now if now is not None else time.time()))
    dst = "%s.bad.%s" % (path, ts)
    if os.path.exists(dst):
        # 同一秒里连着坏两次（或上一次留证还在）⇒ 再加一段随机后缀，绝不覆盖上一份留证
        dst = "%s.%s" % (dst, secrets.token_hex(2))
    try:
        os.replace(path, dst)
        return dst
    except Exception as e:
        log.warning("坏档改名留证失败（原文件保持原样、未删）：%s ⇒ %s", path, e)
        return ""


def load_or_quarantine(path, default):
    """读一个 JSON 档：**不存在 ⇒ 返回 default**；**解析失败/读失败 ⇒ 坏档改名 `.bad.<ts>` 留证 + 记一条 warn + 返回 default**。

    **为什么坏档要留证而不是删掉**：见 `quarantine()`——坏档里常常是用户唯一那份数据
    （`risk_state.json` 里的停机开关、`timers.json` 里没响的提醒都在里面），删掉等于替用户做决定；
    改名留证则"既不丢、也不让下游静默覆盖"。

    **为什么读失败要返回 default 而不是抛**：19 个调用点里绝大多数是"起不来比用默认值更糟"
    （威胁最大的是 `risk.py`：读不出来时**必须**按 fail-closed 处理，由调用方自己判，见那边 `_load`）。

    返回值与传入的 `default` 是**同一个对象**（不做深拷贝）⇒ 调用方可以传一个哨兵对象，
    用 `is` 判断"到底读到了还是走的默认值"。
    """
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as e:
        bad = quarantine(path)
        log.warning("JSON 读不出来 ⇒ 已按坏档留证：%s（原档：%s，原因：%s）",
                    bad or "留证失败，原档保持原样", path, e)
        return default


def atomic_write_json(path, data, indent: int = 1, sort_keys: bool = False) -> bool:
    """原子写 JSON：`<path>.<pid>.<random>.tmp` → `flush` + `fsync` → `os.replace`。

    **为什么 tmp 名要带 pid + 随机后缀**（V-R9-22）：老写法所有落盘点共用 `<path>.tmp`，
    两个进程/线程同时写同一个档时会**打开同一个临时文件**，内容互相穿插（写出半截 JSON、
    或 A 把 B 半截内容 `replace` 上去）——audit 实测 memory 并发写 200 次坏 **46 个**。
    带上 pid + 4 字节随机数后，每个写者有自己的临时文件，`os.replace` 在 NTFS 上是原子的
    （读者要么看到旧的、要么看到新的，**永远看不到半截**）。

    **失败不许静默吞**：临时文件删掉、记一条 warn、返回 `False`（原档一个字节都不动）。
    """
    tmp = "%s.%d.%s.tmp" % (path, os.getpid(), secrets.token_hex(4))
    try:
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent, sort_keys=bool(sort_keys))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception as e:
        # 只删**自己的**临时文件（名字里有本进程 pid + 随机数，不会误删别人的）
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception as e2:
            log.warning("原子写失败的临时文件删不掉（不影响原档）：%s ⇒ %s", tmp, e2)
        log.warning("原子写失败（原档未动、临时文件已清理）：%s ⇒ %s", path, e)
        return False
