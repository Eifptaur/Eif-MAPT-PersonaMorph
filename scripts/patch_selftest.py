# -*- coding: utf-8 -*-
"""增量包判据（群相）—— 证明"载荷只含变更文件"，且 del 不进载荷、未变文件不进 plan。

自包含：现场跑 `make_manifest.py` 拿真表 → 由它**派生一张假旧表**（删两条 ⇒ add、改一条 ⇒ replace、
另加一条不存在的路径 ⇒ del）→ 跑 `make_patch.py` → 逐条断言。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

import pack_online as po          # noqa: E402
import make_patch as mp           # noqa: E402

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✔ " + msg)
    else:
        FAIL += 1
        print("  ✘ " + msg)


tmp = tempfile.mkdtemp(prefix="pm-patch-")
try:
    # ---- 0) 先拿真表 ----
    print("[P0] 现场生成新版文件表")
    r = subprocess.run([sys.executable, os.path.join(HERE, "make_manifest.py"), "--out", tmp],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    ok(r.returncode == 0, "make_manifest.py rc=%s" % r.returncode)
    if r.returncode != 0:
        print((r.stdout or "")[-600:], (r.stderr or "")[-600:])
        sys.exit(1)
    newp = os.path.join(tmp, "persona-morph-files.json")
    new = json.load(open(newp, encoding="utf-8"))
    nf = dict(new["files"])
    ok(len(nf) > 5, "新版表有 %d 条" % len(nf))

    # ---- 1) 派生假旧表 ----
    keys = sorted(nf)
    drop = keys[:2]                     # ⇒ 新版里它们算 add
    change = keys[2]                    # ⇒ 改哈希算 replace
    old = {k: dict(v) for k, v in nf.items()}
    for k in drop:
        old.pop(k)
    old[change] = {"sha256": "0" * 64, "size": 123}      # 故意改掉 ⇒ replace
    old["__ghost__/never.py"] = {"sha256": "1" * 64, "size": 1}   # 新版没有 ⇒ del
    oldp = os.path.join(tmp, "old-files.json")
    with open(oldp, "w", encoding="utf-8") as fh:
        json.dump({"version": "0.0.0-old", "files": old}, fh, ensure_ascii=False)

    # ---- 2) 纯函数 diff ----
    print("\n[P1] diff_plan 纯函数：add / replace / del 判定与排序")
    plan = mp.diff_plan(old, nf)
    bypath = {e["path"]: e for e in plan}
    ok(len(plan) == 4, "plan 条数 = 4（2 add + 1 replace + 1 del），实为 %d" % len(plan))
    ok(all(bypath[k]["op"] == "add" for k in drop), "被删掉的两条 ⇒ op=add")
    ok(bypath[change]["op"] == "replace", "哈希变了的 ⇒ op=replace")
    ok(bypath["__ghost__/never.py"]["op"] == "del", "新版没有的 ⇒ op=del")
    others = [k for k in nf if k not in drop and k != change]
    ok(all(k not in bypath for k in others), "**未变的 %d 条一条都没进 plan**" % len(others))
    ok([e["path"] for e in plan] == sorted([e["path"] for e in plan]), "plan 按路径排序（可比对、可复现）")
    ok(mp.diff_plan(old, nf) == plan, "同输入两次 ⇒ plan 完全一致")

    # ---- 3) 真跑 make_patch ----
    print("\n[P2] make_patch.py 真跑：载荷只含变更文件")
    r2 = subprocess.run([sys.executable, os.path.join(HERE, "make_patch.py"),
                         "--old", oldp, "--new", newp, "--out", tmp],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    ok(r2.returncode == 0, "make_patch.py rc=%s" % r2.returncode)
    if r2.returncode != 0:
        print((r2.stdout or "")[-600:], (r2.stderr or "")[-600:])
        sys.exit(1)
    zp = os.path.join(tmp, "persona-morph-patch-0.0.0-old-to-%s.zip" % new["version"])
    jp = zp[:-4] + ".json"
    ok(os.path.exists(zp), "增量载荷已产出")
    ok(os.path.exists(jp), "patch 块已产出")
    patch = json.load(open(jp, encoding="utf-8"))

    with zipfile.ZipFile(zp) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
    want = ["%s/%s" % (po.ZIP_TOP, e["path"]) for e in plan if e["op"] in ("add", "replace")]
    ok(len(names) == 3, "载荷里 3 个文件（2 add + 1 replace），实为 %d" % len(names))
    ok(sorted(names) == sorted(want), "**载荷路径集合 == plan 里 add/replace 的集合**")
    ok(all(n.startswith(po.ZIP_TOP + "/") for n in names), "包内顶层目录与在线包同构（%s/）" % po.ZIP_TOP)
    ok(not any("__ghost__" in n for n in names), "del 的那条**没有**被打进载荷（负向）")

    print("\n[P3] patch 块字段与数字自洽")
    ok(patch["from"] == "0.0.0-old" and patch["base"] == new["version"], "from / base 版本正确")
    ok(patch["counts"] == {"add": 2, "replace": 1, "del": 1}, "counts 正确（%s）" % patch["counts"])
    ok(len(patch["plan"]) == 4, "patch.plan 与纯函数结果同为 4 条")
    for e in patch["plan"]:
        if e["op"] in ("add", "replace"):
            ok(e["sha256"] == nf[e["path"]]["sha256"], "plan[%s].sha256 == 新版表里的哈希" % e["path"])
    import hashlib
    with open(zp, "rb") as fh:
        ok(hashlib.sha256(fh.read()).hexdigest() == patch["zipSha256"], "zipSha256 与实际载荷一致（可复算）")
    ok(patch["bytes"] == sum(os.path.getsize(os.path.join(ROOT, e["path"].replace("/", os.sep)))
                             for e in patch["plan"] if e["op"] in ("add", "replace")), "bytes 与载荷文件之和一致")

    # ---- 4) 负向：plan 指向不存在的文件必须明确报错（不许在错的源上打载荷） ----
    print("\n[P4] 负向：源里缺文件 ⇒ 拒绝打包")
    bad = dict(old)
    bad["__ghost__/never.py"] = {"sha256": "2" * 64, "size": 1}   # 仍在旧表里
    badp = os.path.join(tmp, "bad-old.json")
    with open(badp, "w", encoding="utf-8") as fh:
        json.dump({"version": "0.0.0-old", "files": bad}, fh, ensure_ascii=False)
    # 让"新增"的那条指向一个不存在的路径 ⇒ collect_files 应当发现
    nf2 = dict(nf)
    nf2["__ghost__/missing.py"] = {"sha256": "3" * 64, "size": 9}
    newp2 = os.path.join(tmp, "new2.json")
    with open(newp2, "w", encoding="utf-8") as fh:
        json.dump({"version": new["version"], "files": nf2}, fh, ensure_ascii=False)
    r3 = subprocess.run([sys.executable, os.path.join(HERE, "make_patch.py"),
                         "--old", badp, "--new", newp2, "--out", tmp],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    ok(r3.returncode == 2, "缺文件时 rc=2（明确拒绝），实为 %s" % r3.returncode)
    ok("找不到" in (r3.stdout or ""), "并打印了原因")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n==== 增量包判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
