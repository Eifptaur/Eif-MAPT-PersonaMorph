# -*- coding: utf-8 -*-
"""群相 · 增量包生成器（本体 + DLC 契约 §四）。

做法（照 `docs/设计-本体与DLC.md` §四）：
  拿**新旧两版的逐文件 sha256 表**（`make_manifest.py` 产出的 `persona-morph-files.json`）做 diff
  ⇒ 生成 `patch{from, base, plan}`，plan 里每条＝`{op: add|replace|del, path, sha256, size}`
  ⇒ 再打一个**只含变更文件**的增量载荷 zip（内部路径与在线包同构：`persona morph/<相对路径>`）。

用法：
  py -3 scripts/make_patch.py --old <旧 files.json> --new <新 files.json> --out DIR
  py -3 scripts/make_patch.py --old ... --new ... --self-test-plan   # 只打印 plan，不打载荷

产出：
  <out>/persona-morph-patch-<from>-to-<to>.zip   只含 add/replace 的文件
  <out>/persona-morph-patch-<from>-to-<to>.json  {from, base, plan, counts, bytes, sha256(载荷)}
"""
import argparse
import hashlib
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import pack_online as po          # noqa: E402  复用 ZIP_TOP（包内顶层目录名要与在线包一致）

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


def load_files(path):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    return str(d.get("version") or ""), dict(d.get("files") or {})


def diff_plan(old, new):
    """纯函数：新旧文件表 → plan（add / replace / del），按路径排序，便于比对与复现。"""
    plan = []
    for rel in sorted(new):
        if rel not in old:
            plan.append({"op": "add", "path": rel,
                         "sha256": new[rel]["sha256"], "size": int(new[rel]["size"])})
        elif new[rel]["sha256"] != old[rel]["sha256"]:
            plan.append({"op": "replace", "path": rel,
                         "sha256": new[rel]["sha256"], "size": int(new[rel]["size"])})
    for rel in sorted(old):
        if rel not in new:
            plan.append({"op": "del", "path": rel})
    return plan


def collect_files(plan):
    """只收 add/replace 的**实际文件**（它们才是载荷）；返回 [(rel, abs_path)]，缺文件会明确报错。"""
    out, missing = [], []
    for e in plan:
        if e["op"] == "del":
            continue
        abs_p = os.path.join(ROOT, e["path"].replace("/", os.sep))
        if not os.path.exists(abs_p):
            missing.append(e["path"])
        else:
            out.append((e["path"], abs_p))
    return out, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True, help="旧版 files.json")
    ap.add_argument("--new", required=True, help="新版 files.json")
    ap.add_argument("--out", default=os.path.join(po.OUT_DIR, "_outbox"))
    ap.add_argument("--self-test-plan", action="store_true", help="只打印 plan，不打载荷")
    a = ap.parse_args()

    v_old, old = load_files(a.old)
    v_new, new = load_files(a.new)
    plan = diff_plan(old, new)
    counts = {"add": 0, "replace": 0, "del": 0}
    for e in plan:
        counts[e["op"]] += 1
    print("旧版 %s（%d 文件） → 新版 %s（%d 文件）" % (v_old or "?", len(old), v_new or "?", len(new)))
    print("plan：新增 %d · 替换 %d · 删除 %d · 合计 %d" % (counts["add"], counts["replace"], counts["del"], len(plan)))
    if a.self_test_plan:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    todo, missing = collect_files(plan)
    if missing:
        print("✘ plan 里有文件在仓库里找不到（不能在错的源上打载荷）：%s" % missing[:5])
        return 2

    os.makedirs(a.out, exist_ok=True)
    tag = "%s-to-%s" % (v_old or "old", v_new or "new")
    zp = os.path.join(a.out, "persona-morph-patch-%s.zip" % tag)
    jp = os.path.join(a.out, "persona-morph-patch-%s.json" % tag)

    total = 0
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel, abs_p in todo:
            arc = "%s/%s" % (po.ZIP_TOP, rel)
            z.write(abs_p, arc)
            total += os.path.getsize(abs_p)

    with open(zp, "rb") as fh:
        zh = hashlib.sha256(fh.read()).hexdigest()

    # expectFiles＝新版**全量**文件表（纯哈希）：客户端换入后拿它重算文件树哈希，与清单 base.sha256 比对
    # ——这是"组合校验"的依据；不带它，客户端只能逐件校验，换完无法自证整棵树是对的。
    patch = {"from": v_old, "base": v_new, "plan": plan,
             "counts": counts, "bytes": total, "zipSha256": zh,
             "zipName": os.path.basename(zp), "expectFiles": new}
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump(patch, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print("✔ 增量载荷：%s（%d 个文件 / %d 字节 / sha256 %s…）" % (os.path.basename(zp), len(todo), total, zh[:16]))
    print("✔ patch 块：%s" % jp)
    print("   客户端口径：只下 plan 里 add/replace 的那几件 → 逐件校验 sha256 → 暂存 → 换入 → 组合校验；del 的按 path 删。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
