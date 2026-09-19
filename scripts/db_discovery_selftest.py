# -*- coding: utf-8 -*-
"""「微信数据目录找不到」判据（2026-09-19 立，起因＝网友反馈 v0919-2328）。

反馈原文（检验器「消息发不出去」）：`打不开消息库：未找到微信数据库目录，请通过 db_dir 参数手动指定`
—— 而我们的候选表只覆盖**默认位置 + 各盘根**，用户把微信「文件管理」位置改到
`E:\\wutong\\wxhf\\xwechat_files` 这种**嵌套**目录时一个都探不到。

⇒ 守五条（全部用 `tempfile` 造假树，**不连真库、不需要微信在跑**）：
  A. **有界深扫**能找到嵌套的 `xwechat_files`（含账号目录 + `db_storage`）；
  B. 阴性对照：空树 / `xwechat_files` 里没有 `db_storage` ⇒ **不许命中**；
  C. **有界**：深度上限与目录预算都生效（预算 1 时立刻停，且不抛）；
  D. 集成：候选表全不命中时，`_probe_db_dirs` 靠深扫把 `hit` 找出来（并如实标 `deep`）；
  E. 决策：`resolve_db_dir("")` 在这种情况下给出 (`…\\xwechat_files`, `"scanned"`)。

用法：`runtime\\python\\python.exe scripts\\db_discovery_selftest.py`
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # 名称里有「⇒」等非 GBK 字符
except Exception:
    pass

from agent import wechat as W      # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [%s]" % detail if detail else ""))


def make_tree(root, rel):
    """造 `<root>/<rel>/xwechat_files/wxid_judge0001/db_storage/message/message_0.db`。"""
    base = os.path.join(root, rel) if rel else root
    msg = os.path.join(base, "xwechat_files", "wxid_judge0001", "db_storage", "message")
    os.makedirs(msg, exist_ok=True)
    with open(os.path.join(msg, "message_0.db"), "wb") as f:
        f.write(b"x")
    return os.path.join(base, "xwechat_files")


TMP = tempfile.mkdtemp(prefix="pm_dbdisc_")
_orig_cands, _orig_drives = W._db_dir_candidates, W._fixed_drives
try:
    print("── A. 有界深扫能找到嵌套的自定义目录 ──")
    deep_dir = make_tree(TMP, os.path.join("E_drive", "wutong", "wxhf"))
    hit = W._deep_scan_xwechat(roots=[TMP], max_depth=6)
    ok("嵌套 3 层的 xwechat_files 被找到", deep_dir in hit, str(hit)[:120])

    print("── B. 阴性对照：没坏就别报 ──")
    empty = os.path.join(TMP, "empty")
    os.makedirs(empty, exist_ok=True)
    ok("空目录 ⇒ 不命中", W._deep_scan_xwechat(roots=[empty], max_depth=6) == [])
    fake = os.path.join(TMP, "fake", "xwechat_files")
    os.makedirs(os.path.join(fake, "wxid_judge0001"), exist_ok=True)   # 有 xwechat_files 但**没有 db_storage**
    got = W._deep_scan_xwechat(roots=[os.path.join(TMP, "fake")], max_depth=6)
    ok("只有壳、没有 db_storage ⇒ 不命中（不误报）", fake not in got, str(got))

    print("── C. 有界：深度与目录预算 ──")
    t0 = time.monotonic()
    ok("预算 0 ⇒ 立刻停、不抛", W._deep_scan_xwechat(roots=[TMP], max_depth=6, budget=0) == [])
    ok("深度 0 ⇒ 不往下走", W._deep_scan_xwechat(roots=[TMP], max_depth=0) == [])
    # ⚠️ Windows 上 `time.monotonic()` 是**毫秒级**的（GetTickCount64），所以墙上时钟预算只能拿
    #    "预算已耗尽"这种边界来验（给负数 ⇒ 第一次检查就该停）；给 1µs 是测不出来的（读到 0.0）。
    _tiny = W._deep_scan_xwechat(roots=[TMP], max_depth=6, time_budget=-1.0)
    ok("墙上时钟预算耗尽 ⇒ 立刻停（不再往下扫）", _tiny == [] and (time.monotonic() - t0) < 2.0,
       str(_tiny))

    print("── D. 集成：候选表全不命中时靠深扫救回 ──")
    W._db_dir_candidates = lambda extra="": [os.path.join(TMP, "no_such_a"), os.path.join(TMP, "no_such_b")]
    W._fixed_drives = lambda: [TMP]
    p = W._probe_db_dirs("")
    ok("深扫结果进了 hit（用户「未找到目录」的那一档被救回）", deep_dir in p["hit"], str(p["hit"])[:120])
    ok("如实标出这是深扫探到的", deep_dir in (p.get("deep") or []), str(p.get("deep"))[:120])
    ok("账号数与库文件数都数到了", int(p["accounts"]) >= 1 and int(p["dbs"]) >= 1,
       "accounts=%s dbs=%s" % (p["accounts"], p["dbs"]))

    print("── E. 决策：直接就能用，不用用户手填 ──")
    d, src = W.resolve_db_dir("")
    ok("resolve_db_dir 给出深扫到的那个目录 + 来源 scanned",
       os.path.normcase(str(d)) == os.path.normcase(deep_dir) and src == "scanned", "%s / %s" % (d, src))

    print("── F. 真机只读冒烟（不联网、不开窗）──")
    W._db_dir_candidates, W._fixed_drives = _orig_cands, _orig_drives
    t1 = time.monotonic()
    try:
        real = W._probe_db_dirs("")
        ok("真机跑一遍不抛、且有界（<=12s）", isinstance(real, dict) and (time.monotonic() - t1) <= 12.0,
           "用时 %.1fs · tried=%d · hit=%d · deep=%d · 命中：%s" % (time.monotonic() - t1, len(real["tried"]),
                                                                   len(real["hit"]), len(real.get("deep") or []),
                                                                   (real["hit"][:1] or ["-"])[0]))
    except Exception as e:                                              # noqa: BLE001
        ok("真机跑一遍不抛、且有界（<=12s）", False, str(e)[:80])
finally:
    W._db_dir_candidates, W._fixed_drives = _orig_cands, _orig_drives
    shutil.rmtree(TMP, ignore_errors=True)

print("\n==== 微信数据目录发现判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
