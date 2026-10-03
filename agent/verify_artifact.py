# -*- coding: utf-8 -*-
"""外部产物核对 —— **读别人的文件前先证明它写完了**（P0-8）。

**什么场景**：读第三方探针输出、读"另一个程序"产出的 `jsonl`、读下载工具落地的清单文件。
这些文件**不由本进程写**，所以"它还在写"与"它写完了"从文件内容上分不出来——
直接 `json.loads` 的后果是：
  · 拿到**半份数据**（最后一行是残行 ⇒ 整行解析失败 ⇒ 被 `except` 吞掉 ⇒ 表观"少了几条"，不报错）；
  · 或者**读的过程中它又追加了**（读到一半的内容与结束时的内容不是同一份，行数与体积对不上）。

**最省事又最准的那一条判据：末字节必须是 `\\n`。**
JSONL 是"一行一条"格式，写完的最后一条必然以换行结束；
**没写完时最后一行是残行、末尾没有换行** ⇒ 一个字节的检查就能挡住绝大多数情况。
再配"读前/读后各 `stat` 一次字节数"（防读取期间被追加/替换）与"行数核对"，
就是四道核对里最实用的三道。

**这一条比 P0-6（原子写）更保守**：P0-6 管"我们自己写得干不干净"，
本模块管"**别人给的，我们敢不敢用**" —— 两者不可互相替代。
"""
from __future__ import annotations

import json
import os

#: 允许的单文件体积上限（默认 64 MB）：超过就**先拒**，不把内存打爆再报错。
#: 外部产物的体量不可控，`stat` 一次就能判定，比"读进来才 OOM"划算。
MAX_BYTES = 64 * 1024 * 1024


class ArtifactError(Exception):
    """外部产物未通过核对。`reason` 是**可判定**的一句（带具体数字），便于上层回显/记日志。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = str(reason)


def _stat_size(path: str) -> int:
    try:
        return int(os.stat(path).st_size)
    except OSError as e:
        raise ArtifactError("stat 失败：%s（%s）" % (path, e)) from None


def check_complete(path: str, expect_bytes: int | None = None,
                   expect_lines: int | None = None) -> dict:
    """**只核对不读内容**：四道检查全过 ⇒ `{"ok": True, "bytes": n, "lines": m}`；
    任一不过 ⇒ 抛 `ArtifactError`。

    ① 存在且是**普通文件**（不是目录/符号链接指向的怪东西）；
    ② `stat` 字节数与 `expect_bytes`（外部清单/manifest 提供的值）一致 —— 传 `None` 则跳过；
    ③ **末字节必须是 `\\n`**（空文件 ⇒ 直接判未完成：JSONL 至少该有一条）；
    ④ 逐行计数 == `expect_lines`（传 `None` 则跳过）——
       计数用**二进制分块**读，不把整份装进内存。
    这是 `read_verified_jsonl` 的内部步骤，**也可单独调用**（只想确认"那边写完了没"时）。
    """
    if not path or not os.path.isfile(path):
        raise ArtifactError("不是普通文件（可能还没落地）：%s" % (path or "(空路径)"))
    n = _stat_size(path)
    if n <= 0:
        raise ArtifactError("文件为空（0 字节）⇒ 视为未写完：%s" % path)
    if n > MAX_BYTES:
        raise ArtifactError("文件太大（%d 字节 > 上限 %d）⇒ 先拒：%s" % (n, MAX_BYTES, path))
    if expect_bytes is not None and int(expect_bytes) != n:
        raise ArtifactError("字节数不符：盘上 %d ≠ 清单 %d ⇒ 未写完或被改过：%s"
                            % (n, int(expect_bytes), path))
    with open(path, "rb") as fh:
        fh.seek(-1, os.SEEK_END)
        if fh.read(1) != b"\n":
            raise ArtifactError("末字节不是换行 ⇒ 最后一行是残行（未写完）：%s" % path)
    if expect_lines is not None:
        cnt = 0
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                cnt += chunk.count(b"\n")
        if cnt != int(expect_lines):
            raise ArtifactError("行数不符：实际 %d ≠ 清单 %d：%s" % (cnt, int(expect_lines), path))
        return {"ok": True, "bytes": n, "lines": cnt}
    return {"ok": True, "bytes": n, "lines": None}


def read_verified_jsonl(path: str, manifest: dict | None = None, *,
                        budget_lines: int = 0) -> list:
    """读一份**外部** JSONL，**核对不过就抛 `ArtifactError`（绝不返回半份数据）**。

    `manifest`（外部清单/self 传过来的账）可含：
      · `"path"`  —— 期望路径：`resolve()` 后必须与实参一致（防"清单写 A、实际读 B"）；
      · `"bytes"` —— 期望字节数（对应 `check_complete` 的 `expect_bytes`）；
      · `"lines"` —— 期望行数（对应 `expect_lines`）。
    **缺哪个键就跳过哪道检查**，但 `path` 一旦给了就一定会比对（这条最容易被"顺手传了旧清单"绕过）。

    `budget_lines > 0` 时只返回**前 N 条**（调用方只想看头几行时用）——
    ⚠️ **核对照旧按全文件做**（否则"只看头 3 行"会让"末字节是 `\\n`"这道判据形同虚设）。

    读完之后**再 `stat` 一次**：字节数若与读前不同 ⇒ 读的过程中被改过 ⇒ 同样抛错。
    这是"读前 / 读后各一次 stat"里那**后一次**，专门挡"边读边追加"。
    """
    mf = dict(manifest or {})
    want_path = str(mf.get("path") or "").strip()
    if want_path:
        if os.path.normcase(os.path.abspath(want_path)) != os.path.normcase(os.path.abspath(path)):
            raise ArtifactError("路径不符：清单 %s ≠ 实参 %s" % (want_path, path))
    st = check_complete(path, mf.get("bytes"), mf.get("lines"))
    out: list = []
    seen = 0
    with open(path, "r", encoding="utf-8") as fh:
        for ln_no, line in enumerate(fh, 1):
            s = line.rstrip("\n")
            if not s.strip():
                continue # 允许夹空行（有些工具会补一行），但**不计数**：它不算"一条记录"
            try:
                out.append(json.loads(s))
            except Exception as e:
                # ⛔ 一条坏行 ⇒ 整份拒（不静默跳过）：外部产物的"少一条"没法与"本来就少"区分，
                #    唯一安全的处理是退回"这次没有可用数据"，让上层重取。
                raise ArtifactError("第 %d 行不是合法 JSON：%s（%s）" % (ln_no, s[:80], e)) from None
            seen += 1
            if budget_lines and seen >= int(budget_lines):
                # 仍要把文件读完做"读后 stat"（见 docstring），故不是 break 整个循环读文件语义，
                # 而是读完再截断 —— 这里用"读满即停解析、继续读字节"的方式。
                for _rest in fh:
                    pass
                break
    after = _stat_size(path)
    if after != int(st.get("bytes") or after):
        raise ArtifactError("读取期间文件被改过：读前 %d 字节、读后 %d 字节 ⇒ 拒用这份数据"
                            % (int(st.get("bytes") or 0), after))
    return out[:int(budget_lines)] if budget_lines else out
