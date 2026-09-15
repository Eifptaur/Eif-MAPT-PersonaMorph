# -*- coding: utf-8 -*-
"""群相 · 更新器（客户端落地）—— 校验 → 暂存 → 换入 → 组合校验 → 失败回滚。

契约见 `docs/设计-本体与DLC.md` §四：
  ① 只下 `plan` 里那几件 → 逐件校验 sha256 → 落暂存 → 换入 → **组合校验**；
  ② **失败即回滚**（换入前先把将被改动的文件快照下来）；
  ③ 组合校验**只针对清单里列出的文件**（不碰 `data/`、`config.json` 这些运行时/用户文件）。

命令：
  py -3 scripts/pm_update.py check --manifest <路径|URL> [--target DIR] [--json]
  py -3 scripts/pm_update.py apply --manifest <路径> --payload <patch.json> --zip <载荷zip>
                                  [--target DIR] [--dry]

退出码：0 = 成功（含"已是最新"）· 1 = 失败且**已回滚** · 2 = 前置不成立（没跑任何东西）
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

STATE_REL = os.path.join("data", "installed.json")
# 这些是**运行时/用户**文件，不属于本体，更新一律不碰（也不参与组合校验）
NEVER_TOUCH = ("data/", "config.json", "logs/", "wechatauto_logs/", "报告/")


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hash(files):
    """与 make_manifest.py **同一算法**：排序后 (相对路径 + 文件哈希) 再哈希。"""
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode("utf-8"))
        h.update(str(files[rel]["sha256"]).encode("ascii"))
    return h.hexdigest()


def load_json(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def read_local_state(target):
    p = os.path.join(target, STATE_REL)
    if os.path.exists(p):
        try:
            return load_json(p)
        except Exception:
            return {}
    return {}


def write_local_state(target, version, th, extra=None):
    p = os.path.join(target, STATE_REL)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    d = {"version": version, "sha256": th, "appliedAt": int(time.time())}
    if extra:
        d.update(extra)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, p)
    return d


def _touched(rel):
    return not any(rel == n.rstrip("/") or rel.startswith(n) for n in NEVER_TOUCH)


def check_update(manifest, target):
    """三态：newer / current / older（读不到清单由调用方处理）。"""
    base = manifest.get("base") or {}
    cur = read_local_state(target)
    mine = str(cur.get("version") or "")
    theirs = str(base.get("version") or "")
    if not mine:
        return {"status": "newer", "mine": "", "theirs": theirs,
                "why": "本地没有版本记录（第一次接入更新链）"}
    if theirs == mine:
        return {"status": "current", "mine": mine, "theirs": theirs, "why": "已是最新"}
    return {"status": "newer" if theirs > mine else "older", "mine": mine, "theirs": theirs,
            "why": "远端 %s / 本地 %s" % (theirs, mine)}


def apply_update(manifest, patch, payload_zip, target, dry=False):
    """换入增量。返回 (rc, msg, detail)。任一步失败 ⇒ 回滚已改动的那几件。"""
    base = manifest.get("base") or {}
    want_version = str(base.get("version") or "")
    want_tree = str(base.get("sha256") or "")
    if not want_version or not want_tree:
        return 2, "清单缺 base.version / base.sha256", {}
    if patch.get("base") != want_version:
        return 2, "patch.base（%s）与清单版本（%s）不一致 —— 拿错载荷了" % (patch.get("base"), want_version), {}

    cand = read_local_state(target)
    if str(cand.get("version") or "") == want_version:
        return 0, "已是最新（%s），什么都没做" % want_version, {"status": "current"}

    plan = patch.get("plan") or []
    if not plan:
        return 2, "patch.plan 是空的", {}
    bad_scope = [e["path"] for e in plan if not _touched(e["path"])]
    if bad_scope:
        return 2, "plan 里含本体不该动的文件：%s" % bad_scope[:4], {}

    # ---- 1) 载荷解压到暂存目录，逐件校验 sha256 ----
    #   `stage`（解压载荷）与 `backup`（换入前的回滚快照）都是系统临时目录里的东西，
    #   **两个都必须在 finally 里删掉**——2026-09-15 审计发现 backup 从来没删过，
    #   临时目录里积了一批 `pm-backup-*`（每次都装着一整份被替换文件）。
    stage = tempfile.mkdtemp(prefix="pm-stage-")
    backup = ""
    try:
        with zipfile.ZipFile(payload_zip) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            z.extractall(stage)        # 包内顶层目录 = "persona morph"
        top = sorted(set(n.split("/")[0] for n in names))
        if len(top) != 1:
            return 1, "载荷顶层目录不唯一：%s" % top, {}
        base_dir = os.path.join(stage, top[0])
        need = {e["path"]: e for e in plan if e["op"] in ("add", "replace")}
        have = {}
        for e in need.values():
            sp = os.path.join(base_dir, e["path"].replace("/", os.sep))
            if not os.path.exists(sp):
                return 1, "载荷里缺 plan 要求的文件：%s（疑似下载不完整）" % e["path"], {}
            got = sha256_file(sp)
            if got != e["sha256"]:
                return 1, "载荷文件哈希不符：%s（期望 %s… 实得 %s…）" % (e["path"], e["sha256"][:12], got[:12]), {}
            have[e["path"]] = sp
        extra = [p for p in need if p not in have]      # need 是 {path: entry}，迭代出来是 key（别按 entry 取字段）
        if extra:
            return 1, "载荷里多出未声明的文件：%s" % extra[:4], {}

        # ---- 2) 快照"将被改动/删除"的那几件（只快照本体的文件） ----
        snaps = {}
        for e in plan:
            rel = e["path"]
            tp = os.path.join(target, rel.replace("/", os.sep))
            if os.path.exists(tp):
                snaps[rel] = sha256_file(tp)
        if dry:
            return 0, "【干跑】将换入 %d 件 / 删除 %d 件（版本 %s → %s）" % (
                len(need), sum(1 for e in plan if e["op"] == "del"), cand.get("version") or "?", want_version), {"dry": True}

        backup = tempfile.mkdtemp(prefix="pm-backup-")

        def rollback(why):
            for rel in snaps:
                bp = os.path.join(backup, rel.replace("/", os.sep))
                tp = os.path.join(target, rel.replace("/", os.sep))
                if os.path.exists(bp):
                    os.makedirs(os.path.dirname(tp), exist_ok=True)
                    shutil.copy2(bp, tp)
            # 回滚"新增的"文件：plan 里 add 且备份里没有 ⇒ 删掉
            for e in plan:
                if e["op"] == "add":
                    tp = os.path.join(target, e["path"].replace("/", os.sep))
                    if os.path.exists(tp):
                        os.remove(tp)
            return 1, why + "（已回滚）", {}

        # 快照落盘（复制而非移动，安全第一）
        for rel in snaps:
            bp = os.path.join(backup, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(bp), exist_ok=True)
            shutil.copy2(os.path.join(target, rel.replace("/", os.sep)), bp)

        # ---- 3) 换入 ----
        try:
            for rel, sp in have.items():
                tp = os.path.join(target, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(tp), exist_ok=True)
                shutil.copy2(sp, tp)
            for e in plan:
                if e["op"] == "del":
                    tp = os.path.join(target, e["path"].replace("/", os.sep))
                    if os.path.exists(tp):
                        os.remove(tp)
        except Exception as ex:
            return rollback("换入过程出错：%s" % ex)

        # ---- 4) 组合校验：只对"清单里列出的文件"重算文件树哈希 ----
        #     清单里没有新旧两版的**全量**文件表时，退化为"逐件校验 plan + 抽查清单要求"
        expect = patch.get("expectFiles") or {}
        if expect:
            files = {}
            for rel, meta in expect.items():
                tp = os.path.join(target, rel.replace("/", os.sep))
                if not os.path.exists(tp):
                    return rollback("组合校验失败：缺文件 %s" % rel)
                files[rel] = {"sha256": sha256_file(tp)}
            got_tree = tree_hash(files)
            if got_tree != want_tree:
                return rollback("组合校验失败：文件树哈希 %s… ≠ 清单 %s…" % (got_tree[:12], want_tree[:12]))
        else:
            for rel, meta in (patch.get("planNew") or {}).items():
                tp = os.path.join(target, rel.replace("/", os.sep))
                if not os.path.exists(tp) or sha256_file(tp) != meta:
                    return rollback("组合校验失败：%s 哈希不符" % rel)

        st = write_local_state(target, want_version, want_tree,
                              {"from": cand.get("version") or "", "plan": len(plan)})
        return 0, "更新成功：%s → %s（换入 %d 件 / 删除 %d 件）" % (
            cand.get("version") or "?", want_version, len(need), sum(1 for e in plan if e["op"] == "del")), {"state": st}
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        if backup:
            shutil.rmtree(backup, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--manifest", required=True)
    c.add_argument("--target", default=ROOT)
    c.add_argument("--json", action="store_true")
    a2 = sub.add_parser("apply")
    a2.add_argument("--manifest", required=True)
    a2.add_argument("--payload", required=True, help="patch.json")
    a2.add_argument("--zip", required=True, help="增量载荷 zip")
    a2.add_argument("--target", default=ROOT)
    a2.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    try:
        man = load_json(a.manifest)
    except Exception as ex:
        print("✘ 读不到清单（离线/路径不对就静默跳过，不要弹任何东西）：%s" % ex)
        return 2

    if a.cmd == "check":
        r = check_update(man, a.target)
        print(json.dumps(r, ensure_ascii=False) if a.json else
              "%s：%s（本地 %s / 远端 %s）" % (r["status"], r["why"], r["mine"] or "无记录", r["theirs"]))
        return 0

    patch = load_json(a.payload)
    rc, msg, detail = apply_update(man, patch, a.zip, a.target, dry=a.dry)
    print(("✔ " if rc == 0 else "✘ ") + msg)
    return rc


if __name__ == "__main__":
    sys.exit(main())
