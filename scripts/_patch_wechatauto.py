# -*- coding: utf-8 -*-
"""给 `wechatauto-replica`（vendor 依赖）打两处**幂等补丁**。

为什么需要这个脚本（而不是直接改 `runtime/.../wechatauto/db.py`）：
  `runtime/` 是 gitignore 的（vendored 环境，不入库）。直接改那里的文件 ⇒
  **换台电脑 / 重跑 `setup_deps.py` / 重装依赖时就丢了**，而且没有任何痕迹能看出丢过。
  ⇒ 把改动固化成"安装后自动重放的补丁"，装完依赖必调一次；幂等，重复跑无副作用。

补丁内容（2026-09-30 从朋友的 `群相Lite-纯净版` 包里的 `wechatauto/db.py` 收编）：
  症状：报 `file is not a database` / 数据库合并失败，也就是诊断书里的"消息库损坏"。
  根因：页 1 的"明文头 / 全加密"模式**只看密钥长度**。但 48B(key+salt) 候选
        **也可能来自全加密库** —— 此时页 1 前 16 字节是**密文 salt** 而非明文头；
        旧码照"明文头模式"把这 16 字节密文当明文头保留 ⇒ 解出的页 1 没有
        `SQLite format 3\\0` ⇒ 该候选被误判不可用。
  修法：① `_decrypt_page` 页 1 模式**以磁盘事实为准**（`page[:16]` 是不是明文
        SQLite 头）；② 解密后统一要求**满配头**（`pt[:16]` + `pt[16:18]`），
        不再按"key 是否 48B"分叉 —— 与 ① 自洽。

用法：
    runtime\\python\\python.exe scripts\\_patch_wechatauto.py            # 打补丁（幂等）
    runtime\\python\\python.exe scripts\\_patch_wechatauto.py --check    # 只检查，不打
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "runtime", "python", "Lib", "site-packages",
                      "wechatauto", "db.py")

# ── 补丁 1：`_decrypt_page` 页 1 判据以磁盘事实为准 ──────────────────────────
_FN_OLD = '''def _decrypt_page(enc_key: bytes, page: bytes, pgno: int) -> bytes:
    iv = page[PAGE_SZ - RESERVE_SZ: PAGE_SZ - RESERVE_SZ + 16]
    if pgno == 1:
        # 48 字节 key（key+salt）= cipher_plaintext_header_size 明文头模式：
        # 页 1 前 16 字节是明文头（非加密 salt），加密数据从 offset 16 开始；
        # 解密后拼接明文头 + 明文数据 + reserve。
        if len(enc_key) == 48:
            enc = page[16: PAGE_SZ - RESERVE_SZ]
            return page[:16] + _aes_cbc_decrypt(enc_key[:32], iv, enc) + b"\\x00" * RESERVE_SZ
        enc = page[16: PAGE_SZ - RESERVE_SZ]
        return b"SQLite format 3\\x00" + _aes_cbc_decrypt(enc_key, iv, enc) + b"\\x00" * RESERVE_SZ
    enc = page[: PAGE_SZ - RESERVE_SZ]
    return _aes_cbc_decrypt(enc_key[:32] if len(enc_key) == 48 else enc_key, iv, enc) + b"\\x00" * RESERVE_SZ
'''

_FN_NEW = '''def _decrypt_page(enc_key: bytes, page: bytes, pgno: int) -> bytes:
    iv = page[PAGE_SZ - RESERVE_SZ: PAGE_SZ - RESERVE_SZ + 16]
    aes_key = enc_key[:32] if len(enc_key) == 48 else enc_key
    if pgno == 1:
        # 页 1 是否明文头以**磁盘事实**为准（前 16 字节是否明文的 SQLite 头），
        # 不能只看密钥长度：48B(key+salt) 候选**也可能来自全加密库**，若照旧按明文头
        # 模式保留前 16 字节密文 salt，解出的页 1 就没有 SQLite 头 ⇒ 该候选被误判为
        # 不可用 ⇒ 报 "file is not a database / 数据库合并失败"（正是"消息库损坏"那条）。
        # 2026-09-30 收编自朋友的 wechatauto/db.py（与我们仅此一处判据差异）。
        enc = page[16: PAGE_SZ - RESERVE_SZ]
        if page[:16] == b"SQLite format 3\\x00":
            return page[:16] + _aes_cbc_decrypt(aes_key, iv, enc) + b"\\x00" * RESERVE_SZ
        return b"SQLite format 3\\x00" + _aes_cbc_decrypt(aes_key, iv, enc) + b"\\x00" * RESERVE_SZ
    enc = page[: PAGE_SZ - RESERVE_SZ]
    return _aes_cbc_decrypt(aes_key, iv, enc) + b"\\x00" * RESERVE_SZ
'''

# ── 补丁 2：解密后统一要求满配头，不再按 48B 分叉 ─────────────────────────────
_VERIFY_OLD = '''                    if pgno == 1:
                        # 明文头模式（48B key）页 1 解密后保留明文头，
                        # 头部 magic 可能不是标准 SQLite；用版本字节兜底校验。
                        if len(key) == 48:
                            if len(pt) < PAGE_SZ or pt[16:18] != b"\\x01\\x01":
                                continue
                        elif pt[:16] != b"SQLite format 3\\x00":
                            continue
'''

_VERIFY_NEW = '''                    if pgno == 1:
                        # 无论明文头库还是全加密库，解密后页 1 都必须带明文
                        # SQLite 头 + 1.1 版本字节（模式判据见 _decrypt_page）。
                        # 2026-09-30 收编自朋友的 wechatauto/db.py：**统一收紧**——
                        # 旧写法只按"key 是否 48B"分叉（48B 就放过明文头、只查版本字节），
                        # 但 48B 候选也可能是全加密库 ⇒ 页 1 前 16 字节是**密文 salt** 而非明文头，
                        # 那道宽松闸会把这个坏候选放行 ⇒ 拼出来的页 1 没有 SQLite 头 ⇒
                        # 报 "file is not a database / 数据库合并失败"。
                        # `_decrypt_page` 现在以磁盘事实选模式 ⇒ 这里统一要求满配头，两边自洽。
                        if (len(pt) < PAGE_SZ or pt[:16] != b"SQLite format 3\\x00"
                                or pt[16:18] != b"\\x01\\x01"):
                            continue
'''

_MARKS = ("aes_key = enc_key[:32] if len(enc_key) == 48 else enc_key",
          "统一收紧")


def _apply(src: str) -> tuple[str, list[str]]:
    """返回 `(新内容, 动作说明)`。已打过或找不到锚点都不抛异常。"""
    acts: list[str] = []
    if _MARKS[0] not in src and _FN_OLD in src:
        src = src.replace(_FN_OLD, _FN_NEW, 1)
        acts.append("补丁1：页 1 模式改「以磁盘事实为准」")
    if _MARKS[1] not in src and _VERIFY_OLD in src:
        src = src.replace(_VERIFY_OLD, _VERIFY_NEW, 1)
        acts.append("补丁2：解密后统一要求满配 SQLite 头")
    return src, acts


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    if not os.path.isfile(TARGET):
        print("[patch] 找不到 %s（依赖还没装？先跑 setup_deps.py）" % TARGET)
        return 2
    src = open(TARGET, encoding="utf-8").read()

    # 先验证"能解析"——补丁不许把语法弄坏（宁可不打，也不许留个坏文件）
    import ast
    try:
        ast.parse(src)
    except SyntaxError as e:
        print("[patch] ⛔ 打补丁前文件本身就解析不了：%s —— 不动它。" % e)
        return 3

    new, acts = _apply(src)
    if not acts:
        if _MARKS[0] in src and _MARKS[1] in src:
            print("[patch] 已是最新（两处补丁都在），跳过 ✔")
            return 0
        print("[patch] ⚠️ 两处补丁都**找不到锚点**—— vendor 版本可能变了。")
        print("        这份补丁是按 wechatauto-replica==1.2.2.2 写的；请人工核对 db.py。")
        return 4

    try:
        ast.parse(new)
    except SyntaxError as e:
        print("[patch] ⛔ 打补丁后语法坏了（%s）—— 放弃，原文件不动。" % e)
        return 3

    if check_only:
        print("[patch] --check：需要打补丁（%s），但没有写入。" % "；".join(acts))
        return 1

    open(TARGET, "w", encoding="utf-8").write(new)
    print("[patch] wechatauto/db.py 已打补丁：%s" % "；".join(acts))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
