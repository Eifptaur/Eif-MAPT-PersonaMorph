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

#: `os.replace` 的**有界退避重试**预算（V-R10-22）。
#: Windows 上 `os.replace(tmp, path)` 与"任何人正打开着目标档"会撞出 WinError 5/32
#: （杀毒/索引器/另一个写者/读者句柄都算），实测 20 线程 × 10 写里 **66% 返回 False**。
#: 共享冲突是**瞬时**的 ⇒ 退避重试是对的；但不能无限等（调用方在发送链上），所以给死上限。
_REPLACE_TRIES = 24
_REPLACE_TOTAL_S = 2.0

#: 重试耗尽后仍失败的次数（模块级计数器，供判据/运维读取"是否真在丢写"）。
REPLACE_FAILURES = {"count": 0, "last": ""}


def _replace_retry(tmp: str, path: str) -> bool:
    """把临时档换到目标档，**撞共享冲突就退避重试**（有界）；真失败回 False。

    为什么要有这一步（V-R10-22）：老写法一次 `os.replace` 失败就直接返回 False
    ⇒ 竞争下 71% 的写**静默丢掉**，而调用点大多只看"没抛异常"就当写成功。
    重试解决的是"瞬时共享冲突"这一种（真·权限问题/路径被占死仍会如实回 False）。
    """
    delay = 0.002
    t0 = time.monotonic()
    last = None
    for i in range(_REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return True
        except Exception as e:                       # noqa: BLE001 - 逐类判：只有共享冲突才重试
            last = e
            transient = isinstance(e, PermissionError) or getattr(e, "winerror", None) in (5, 32, 33)
            if not transient:
                break
            if (time.monotonic() - t0) >= _REPLACE_TOTAL_S:
                break
            time.sleep(delay)
            delay = min(delay * 1.6, 0.06)
    REPLACE_FAILURES["count"] = int(REPLACE_FAILURES.get("count") or 0) + 1
    REPLACE_FAILURES["last"] = str(last)[:200]
    return False


def quarantine(path: str, now=None) -> str:
    """把坏档**改名**成 `<path>.bad.<YYYYmmdd-HHMMSS>` 留证；返回留证文件的路径（失败回空串）。

    **为什么是改名而不是删除**（V-R9-18 的口径）：
      坏文件里往往还有**用户数据**——人工能修回来、能看出是哪个版本写坏的、出事时能对上审计。
      删掉就只剩"数据没了"这一句结论，而且下游会拿着默认值继续跑、继续整体覆盖 ⇒
      这正是审计点的"**坏文件 + 一次写入 = 旧数据全没**"。所以：一个字节都不丢，只换个名字。
      改名失败（文件被独占/无权限）时**保持原样不删**，把失败本身也记进日志。

    V-R10-26（同一秒里连着坏 50 次）：后缀除时间戳外**一律再挂一段 4 字节随机数**，
    而不是"发现目标已存在才加"——后者在两次判存在之间仍可能撞车，把上一份留证盖掉。
    """
    if not path or not os.path.exists(path):
        return ""
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(now if now is not None else time.time()))
    dst = None
    for _ in range(8):                               # 撞上（理论概率极低）就再换一段随机后缀
        cand = "%s.bad.%s.%s" % (path, ts, secrets.token_hex(2))
        if not os.path.exists(cand):
            dst = cand
            break
    if dst is None:                                  # 8 次都撞上 ⇒ 退回旧形态，至少不覆盖上一份
        dst = "%s.bad.%s.%s" % (path, ts, secrets.token_hex(4))
    try:
        os.replace(path, dst)
        return dst
    except Exception as e:
        log.warning("坏档改名留证失败（原文件保持原样、未删）：%s ⇒ %s", path, e)
        return ""


def load_checked(path, default):
    """读一个 JSON 档 ⇒ `(值, 可覆盖)`。**`可覆盖=False` ＝ 原档还在、写着默认值会盖掉用户数据**。

    为什么要有这个返回值（V-R10-23，V-R9-18 的回归）：
      坏档**改名留证失败**（ACL 拒读 / 另一进程 `dwShareMode=0` 独占）时，原档会**留在原地**；
      调用方只拿到"默认值"，接着一次 `_save()` 就把它**整体覆盖** ⇒ 老毛病（"坏文件 + 一次写入
      = 旧数据全没"）原样复发。审计实测：抢独占句柄期间 20 次 `timers.add()`，**13 次回 ok=True
      而 20 条提醒只剩 2 条**（丢 18）。
      ⇒ 读失败且**留证也失败**时，强制把"不许覆盖"这件事告诉调用方，由调用方**拒绝写入并把
      默认值当临时只读视图**（宁可报错，也不静默丢用户数据）。

    返回值与传入的 `default` 是**同一个对象**（不做深拷贝）⇒ 调用方可以传哨兵对象，用 `is` 判断。
    """
    if not path or not os.path.exists(path):
        return default, True                 # 从来没落过档 ⇒ 空状态起步，可以写
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), True
    except Exception as e:
        bad = quarantine(path)
        log.warning("JSON 读不出来 ⇒ %s（原档：%s，原因：%s）",
                    "已按坏档留证" if bad else "**留证失败 ⇒ 禁止覆盖原档**", path, e)
        return default, bool(bad)


def load_or_quarantine(path, default):
    """读一个 JSON 档：**不存在 ⇒ 返回 default**；**解析失败/读失败 ⇒ 坏档改名 `.bad.<ts>` 留证 + 记一条 warn + 返回 default**。

    **为什么坏档要留证而不是删掉**：见 `quarantine()`——坏档里常常是用户唯一那份数据
    （`risk_state.json` 里的停机开关、`timers.json` 里没响的提醒都在里面），删掉等于替用户做决定；
    改名留证则"既不丢、也不让下游静默覆盖"。

    **为什么读失败要返回 default 而不是抛**：19 个调用点里绝大多数是"起不来比用默认值更糟"
    （威胁最大的是 `risk.py`：读不出来时**必须**按 fail-closed 处理，由调用方自己判，见那边 `_load`）。
    ⚠️ **要判"留证失败、原档还在"的调用方用 `load_checked()`**（写侧必须拒绝覆盖，V-R10-23）。

    返回值与传入的 `default` 是**同一个对象**（不做深拷贝）⇒ 调用方可以传一个哨兵对象，
    用 `is` 判断"到底读到了还是走的默认值"。
    """
    return load_checked(path, default)[0]


def _sweep_own_orphans(path: str, older_than_s: float = 3600.0) -> int:
    """清掉**本进程**历史上因崩溃留下的 `<path>.<pid>.<rand>.tmp`（V-R10-26）。

    只认两件事：①`os.getpid()` 那一段（**绝不碰别的进程的临时档**——它可能正在写）；
    ②mtime 比 `older_than_s` 还老（**绝不碰同进程别的线程正在写的那个**：它们几分钟内就被
    `os.replace` 换走了，1 小时的只有崩溃残留）。计数返回，由调用方记日志；清不掉不算错。
    """
    d = os.path.dirname(os.path.abspath(path))
    base = os.path.basename(path)
    n = 0
    try:
        names = os.listdir(d or ".")
    except Exception:
        return 0
    mine = "%s.%d." % (base, os.getpid())
    now = time.time()
    for nm in names:
        if not (nm.startswith(mine) and nm.endswith(".tmp")):
            continue
        full = os.path.join(d, nm)
        if os.path.normcase(os.path.abspath(full)) == os.path.normcase(os.path.abspath(path)):
            continue
        try:
            if (now - os.path.getmtime(full)) < float(older_than_s):
                continue                              # 新鲜 ⇒ 可能有人正在写，不许删
            os.remove(full)
            n += 1
        except Exception:
            pass
    return n


def atomic_write_json(path, data, indent: int = 1, sort_keys: bool = False) -> bool:
    """原子写 JSON：`<path>.<pid>.<random>.tmp` → `flush` + `fsync` → `os.replace`。

    **为什么 tmp 名要带 pid + 随机后缀**（V-R9-22）：老写法所有落盘点共用 `<path>.tmp`，
    两个进程/线程同时写同一个档时会**打开同一个临时文件**，内容互相穿插（写出半截 JSON、
    或 A 把 B 半截内容 `replace` 上去）——audit 实测 memory 并发写 200 次坏 **46 个**。
    带上 pid + 4 字节随机数后，每个写者有自己的临时文件，`os.replace` 在 NTFS 上是原子的
    （读者要么看到旧的、要么看到新的，**永远看不到半截**）。

    **竞争下不许静默丢写**（V-R10-22）：`os.replace` 撞 WinError 5/32 时走 `_replace_retry`
    有界退避重试（审计实测反复试就过）。重试后仍失败 ⇒ 清理自己的临时档、记 warn、返回 False
    （原档一个字节不动），并把这次失败记进 `REPLACE_FAILURES` 供运维/判据核对。
    """
    tmp = "%s.%d.%s.tmp" % (path, os.getpid(), secrets.token_hex(4))
    try:
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        _sweep_own_orphans(path)                      # V-R10-26：先清自己上次崩下的孤儿
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent, sort_keys=bool(sort_keys))
            f.flush()
            os.fsync(f.fileno())
        if _replace_retry(tmp, path):
            return True
        raise OSError("os.replace 重试 %d 次仍失败：%s" % (_REPLACE_TRIES, REPLACE_FAILURES["last"]))
    except Exception as e:
        # 只删**自己的**临时文件（名字里有本进程 pid + 随机数，不会误删别人的）
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception as e2:
            log.warning("原子写失败的临时文件删不掉（不影响原档）：%s ⇒ %s", tmp, e2)
        log.warning("原子写失败（原档未动、临时文件已清理）：%s ⇒ %s", path, e)
        return False


def atomic_write_text(path, text, newline: str = "") -> bool:
    """原子写**文本**（同一个 `<path>.<pid>.<rand>.tmp` → fsync → `os.replace` 配方）。

    V-R10-26：`webui.py` 有一处"重写日志/清单"是**自己拼 `<path>.tmp`** 的 —— 名字固定 ⇒
    两个写者撞同一个临时档（内容互相穿插），而且崩溃后留下的孤儿没人清。这里把同一套配方
    给出来：约定统一、临时档唯一、失败返回 False（**原档一个字节不动**）。`newline=""`
    ＝按文本原样写（调用方自己保证行尾）。
    """
    tmp = "%s.%d.%s.tmp" % (path, os.getpid(), secrets.token_hex(4))
    try:
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        _sweep_own_orphans(path)
        with open(tmp, "w", encoding="utf-8", newline=newline) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if _replace_retry(tmp, path):
            return True
        raise OSError("os.replace 重试 %d 次仍失败：%s" % (_REPLACE_TRIES, REPLACE_FAILURES["last"]))
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        log.warning("原子写文本失败（原档未动）：%s ⇒ %s", path, e)
        return False
