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

    print("── F. V-R1-4：多个候选时按「证据」选，不许再取第一个 ──")
    # 造两个候选（都真的落在盘上，不伪造 mtime 之外的任何条件）：
    #   ①「300 天前的残留」：1 个 .db，**文件与目录的 mtime 都是 300 天前**
    #   ②「正在用的」：42 个 .db，mtime 是现在，且有一个刚写过的 `-wal`
    _old_age = time.time() - 86400 * 300

    def make_cand(base, rel, n, age_s, wal_age_s=None, acc="wxid_judge0001"):
        ds = os.path.join(base, rel, "xwechat_files", acc, "db_storage")
        os.makedirs(ds, exist_ok=True)
        for i in range(n):
            p = os.path.join(ds, "message_%d.db" % i)
            with open(p, "wb") as f:
                f.write(b"x")
            os.utime(p, (age_s, age_s))
        if wal_age_s is not None:
            p = os.path.join(ds, "message_0.db-wal")
            with open(p, "wb") as f:
                f.write(b"w")
            os.utime(p, (wal_age_s, wal_age_s))
        os.utime(ds, (age_s, age_s))
        return os.path.join(base, rel, "xwechat_files")

    TMP2 = tempfile.mkdtemp(prefix="pm_dbpick_")
    _c2, _d2, _id2 = W._db_dir_candidates, W._fixed_drives, getattr(W, "_self_identity_hint", None)
    try:
        _resid = make_cand(TMP2, os.path.join("Public", "Documents"), 1, _old_age)
        _live = make_cand(TMP2, os.path.join("zhu", "WeChat"), 42, time.time(),
                          wal_age_s=time.time() - 60)
        W._db_dir_candidates = lambda extra="": []
        W._fixed_drives = lambda: [TMP2]
        W._self_identity_hint = lambda: ("", "")          # 先关掉身份这条，单看"旧/新"
        _p = W._probe_db_dirs("")
        ok("F1 阴（本条回归判据）：旧/小 vs 新/大 ⇒ hit[0] 必须是**新的那个**（原来取深扫先撞见的）",
           (_p["hit"] or [None])[0] == _live, "hit=%s" % [_p["hit"][i][len(TMP2):] for i in range(len(_p["hit"]))])
        ok("F1b 被放弃的候选也要在返回值里（谁被选中、为什么）",
           _resid in (_p["hit"] or []) and bool((_p.get("why") or {}).get(_live))
           and ("42" in (_p.get("why") or {}).get(_live, "")), str(_p.get("why"))[:200])
        ok("F1c 证据明摆着 ⇒ 不许说「需要用户指定」", _p.get("ambiguous") is False, str(_p.get("ambiguous")))
        _d, _s = W.resolve_db_dir("")
        ok("F1d 决策端就用新的那个（不再静默读 300 天前的残留）",
           os.path.normcase(str(_d)) == os.path.normcase(_live) and _s == "scanned", "%s / %s" % (_d, _s))
        # ⚠️ 单候选的"阳"对照在下面 F2b/F2c（**不写"恒真"的断言** —— 那正是"假绿"）
        shutil.rmtree(TMP2, ignore_errors=True)
        TMP3 = tempfile.mkdtemp(prefix="pm_dbsingle_")
        try:
            _only = make_cand(TMP3, os.path.join("E", "wxhf"), 3, time.time())
            W._fixed_drives = lambda: [TMP3]
            _p3 = W._probe_db_dirs("")
            ok("F2b 单候选 ⇒ hit=[它]、ambiguous=False", (_p3["hit"] == [_only]) and not _p3["ambiguous"],
               str(_p3["hit"])[:120])
            ok("F2c 单候选 ⇒ resolve_db_dir 给出它 + scanned",
               W.resolve_db_dir("") == (_only, "scanned"), str(W.resolve_db_dir("")))
        finally:
            shutil.rmtree(TMP3, ignore_errors=True)
        # ③ 证据相近（同档身份 / 同档新鲜度 / 写入时刻相差 <1 小时 / .db 同一量级）⇒ 不自动选
        TMP4 = tempfile.mkdtemp(prefix="pm_dbamb_")
        try:
            _a = make_cand(TMP4, os.path.join("p1"), 5, time.time(), wal_age_s=time.time() - 30)
            _b = make_cand(TMP4, os.path.join("p2"), 6, time.time(), wal_age_s=time.time() - 60)
            W._fixed_drives = lambda: [TMP4]
            _p4 = W._probe_db_dirs("")
            ok("F3 阴：两个候选证据相近 ⇒ 标 ambiguous（宁可让用户点一下）",
               _p4.get("ambiguous") is True and len(_p4["hit"]) == 2, str(_p4.get("ambiguous")))
            _d4, _s4 = W.resolve_db_dir("")
            ok("F3b 证据相近时**不自动选**：返回 (\"\", \"ambiguous\") 而不是 hit[0]",
               (_d4, _s4) == ("", "ambiguous"), "%r / %r" % (_d4, _s4))
            _dl, _sl = W.resolve_db_dir(str(_a))
            ok("F3c 用户在配置里指定了 ⇒ 照用（不再走歧义分支）",
               (_dl, _sl) == (_a, "config"), "%r / %r" % (_dl, _sl))
        finally:
            shutil.rmtree(TMP4, ignore_errors=True)
        # ④ 阳：账号目录与「本人」一致 ⇒ 优先（哪怕它 .db 更少）
        TMP5 = tempfile.mkdtemp(prefix="pm_dbid_")
        try:
            _mine = make_cand(TMP5, os.path.join("mine"), 2, time.time(),
                              acc="wxid_judge0001")
            _other = make_cand(TMP5, os.path.join("other"), 99, time.time(),
                               acc="wxid_someoneelse")
            W._fixed_drives = lambda: [TMP5]
            W._self_identity_hint = lambda: ("wxid_judge0001", "wxid_judge0001")
            _p5 = W._probe_db_dirs("")
            ok("F4 阳：账号目录与本人一致 ⇒ 排在第一位（哪怕 .db 比另一个少）",
               (_p5["hit"] or [None])[0] == _mine, "hit=%s" % [_p5["hit"][i][len(TMP5):] for i in range(len(_p5["hit"]))])
            ok("F4b 证据里如实写出「与本人一致」",
               "与本人一致" in ((_p5.get("why") or {}).get(_mine) or ""), str(_p5.get("why"))[:200])
        finally:
            shutil.rmtree(TMP5, ignore_errors=True)
    finally:
        W._db_dir_candidates, W._fixed_drives = _c2, _d2
        if _id2 is not None:
            W._self_identity_hint = _id2
        shutil.rmtree(TMP2, ignore_errors=True)

    print("── G. 真机只读冒烟（不联网、不开窗）──")
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
