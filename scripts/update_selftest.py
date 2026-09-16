# -*- coding: utf-8 -*-
"""更新器判据（群相）—— 换入能成、**换坏了必须回滚**、组合校验必须真验。

自包含：在临时目录里造一整套"旧版树 + 清单 + patch + 载荷 zip"，真跑 `pm_update.apply_update()`，
覆盖成功路径与四类失败路径（载荷被篡改 / 载荷缺件 / plan 越界 / 版本对不上），
外加"干跑不动文件"与 check 三态。

**载荷缺件那条＝断网演练的等价物**（下载中断 ⇒ 到手的 zip 不完整）。
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

import pm_update as U          # noqa: E402

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✔ " + msg)
    else:
        FAIL += 1
        print("  ✘ " + msg)


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def write(p, s):
    # ⚠️ newline="" 必须写：Windows 文本模式会把 \n 变成 \r\n，那样"未变的文件"哈希也会对不上
    #    （自检第一版就栽在这儿：换入成功却组合校验失败，根因是造数据时引入了 CRLF）
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(s)


def make_zip(zp, items):
    """items: {rel: 内容}；包内顶层目录与在线包同构（persona morph/）。"""
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, body in items.items():
            z.writestr("persona morph/" + rel, body)


tmp = tempfile.mkdtemp(prefix="pm-update-")
try:
    target = os.path.join(tmp, "install")
    # ---- 造旧版树（3 个本体文件 + 一个运行时文件，运行时那个更新不许碰） ----
    old = {"agent/a.py": "A=1\n", "agent/b.py": "B=1\n", "README.md": "old readme\n"}
    for rel, body in old.items():
        write(os.path.join(target, rel.replace("/", os.sep)), body)
    write(os.path.join(target, "data", "installed.json"),
          json.dumps({"version": "1.0.0", "sha256": "x" * 64}))
    write(os.path.join(target, "data", "user_notes.txt"), "用户数据，别动\n")
    old_hash_of_a = sha(os.path.join(target, "agent", "a.py"))

    # ---- 造新版（改 a、加 c、删 README） ----
    new = {"agent/a.py": "A=2  # changed\n", "agent/b.py": "B=1\n", "agent/c.py": "C=1\n"}
    expect = {rel: {"sha256": hashlib.sha256(body.encode()).hexdigest(), "size": len(body)}
              for rel, body in new.items()}
    want_tree = U.tree_hash(expect)
    manifest = {"schema": "persona-morph/1",
                "base": {"version": "1.0.1", "sha256": want_tree, "url": "", "size": 1, "files": len(new)},
                "dlc": [], "announce": {"version": "1.0.1", "notes": ["判据自测"], "forceBase": False, "minBase": "1.0.1"},
                "updatedAt": 0}
    mp = os.path.join(tmp, "manifest.json")
    json.dump(manifest, open(mp, "w", encoding="utf-8"), ensure_ascii=False)

    plan = [{"op": "replace", "path": "agent/a.py", **expect["agent/a.py"]},
            {"op": "add", "path": "agent/c.py", **expect["agent/c.py"]},
            {"op": "del", "path": "README.md"}]
    patch = {"from": "1.0.0", "base": "1.0.1", "plan": plan, "counts": {"add": 1, "replace": 1, "del": 1},
             "expectFiles": expect}
    pp = os.path.join(tmp, "patch.json")
    json.dump(patch, open(pp, "w", encoding="utf-8"), ensure_ascii=False)
    payload = os.path.join(tmp, "payload.zip")
    make_zip(payload, {"agent/a.py": new["agent/a.py"], "agent/c.py": new["agent/c.py"]})

    print("[U1] check 三态")
    r = U.check_update(manifest, target)
    ok(r["status"] == "newer", "本地 1.0.0 / 远端 1.0.1 ⇒ newer")
    r2 = U.check_update(manifest, os.path.join(tmp, "nope"))
    ok(r2["status"] == "newer" and r2["mine"] == "", "没有版本记录 ⇒ newer（第一次接入）")
    man_same = json.loads(json.dumps(manifest))
    man_same["base"]["version"] = "1.0.0"
    ok(U.check_update(man_same, target)["status"] == "current", "版本相同 ⇒ current")
    man_old = json.loads(json.dumps(manifest))
    man_old["base"]["version"] = "0.9.0"
    ok(U.check_update(man_old, target)["status"] == "older", "远端更旧 ⇒ older")

    print("\n[U2] 干跑：只说不做")
    before_a = sha(os.path.join(target, "agent", "a.py"))
    rc, msg, _ = U.apply_update(manifest, patch, payload, target, dry=True)
    ok(rc == 0 and "干跑" in msg, "dry ⇒ rc=0 且说明是干跑")
    ok(sha(os.path.join(target, "agent", "a.py")) == before_a, "干跑没动文件")

    print("\n[U3] 正常换入")
    rc, msg, detail = U.apply_update(manifest, patch, payload, target)
    ok(rc == 0, "apply rc=0（%s）" % msg)
    ok(open(os.path.join(target, "agent", "a.py"), encoding="utf-8").read() == new["agent/a.py"], "改过的文件已换新")
    ok(os.path.exists(os.path.join(target, "agent", "c.py")), "新增的文件已就位")
    ok(not os.path.exists(os.path.join(target, "README.md")), "删除的文件已移除")
    st = json.load(open(os.path.join(target, "data", "installed.json"), encoding="utf-8"))
    ok(st["version"] == "1.0.1" and st["sha256"] == want_tree, "版本记录已更新（%s）" % st["version"])
    ok(open(os.path.join(target, "data", "user_notes.txt"), encoding="utf-8").read().strip() == "用户数据，别动", "**运行时/用户文件没被动**")
    ok(st.get("from") == "1.0.0", "记录里留了 from（可追溯）")
    rc2, msg2, _ = U.apply_update(manifest, patch, payload, target)
    ok(rc2 == 0 and "已是最新" in msg2, "再跑一次 ⇒ 已是最新、什么都不做")

    print("\n[U4] 负向：载荷被篡改 ⇒ 必须失败且回滚")
    def reset_old():
        write(os.path.join(target, "agent", "a.py"), old["agent/a.py"])
        write(os.path.join(target, "agent", "b.py"), old["agent/b.py"])
        write(os.path.join(target, "README.md"), old["README.md"])
        if os.path.exists(os.path.join(target, "agent", "c.py")):
            os.remove(os.path.join(target, "agent", "c.py"))
        write(os.path.join(target, "data", "installed.json"), json.dumps({"version": "1.0.0", "sha256": "x" * 64}))
    reset_old()
    bad_payload = os.path.join(tmp, "payload-bad.zip")
    make_zip(bad_payload, {"agent/a.py": "A=2 TAMPERED\n", "agent/c.py": new["agent/c.py"]})
    rc, msg, _ = U.apply_update(manifest, patch, bad_payload, target)
    ok(rc == 1 and "哈希不符" in msg, "篡改载荷 ⇒ rc=1（%s）" % msg[:40])
    ok(open(os.path.join(target, "agent", "a.py"), encoding="utf-8").read() == old["agent/a.py"], "**内容已回滚**")
    ok(not os.path.exists(os.path.join(target, "agent", "c.py")), "**新增的文件已撤回**")
    ok(json.load(open(os.path.join(target, "data", "installed.json"), encoding="utf-8"))["version"] == "1.0.0",
       "版本记录没被改（回滚彻底）")

    print("\n[U5] 负向：载荷缺件（＝下载中断）⇒ 必须失败且回滚")
    reset_old()
    short_payload = os.path.join(tmp, "payload-short.zip")
    make_zip(short_payload, {"agent/a.py": new["agent/a.py"]})          # 少了 c.py
    rc, msg, _ = U.apply_update(manifest, patch, short_payload, target)
    ok(rc == 1 and "缺 plan 要求的文件" in msg, "载荷不完整 ⇒ rc=1（%s）" % msg[:40])
    ok(open(os.path.join(target, "agent", "a.py"), encoding="utf-8").read() == old["agent/a.py"], "内容已回滚")

    print("\n[U6] 负向：plan 越界（想动运行时文件）⇒ 前置拒绝")
    reset_old()
    bad_plan = json.loads(json.dumps(patch))
    bad_plan["plan"] = bad_plan["plan"] + [{"op": "add", "path": "data/hacked.txt", "sha256": "0" * 64, "size": 1}]
    rc, msg, _ = U.apply_update(manifest, bad_plan, payload, target)
    ok(rc == 2 and "不该动" in msg, "plan 含 data/ ⇒ rc=2（%s）" % msg[:40])

    print("\n[U7] 负向：patch.base 与清单版本对不上 ⇒ 前置拒绝")
    rc, msg, _ = U.apply_update(manifest, {"base": "9.9.9", "plan": plan, "expectFiles": expect}, payload, target)
    ok(rc == 2 and "不一致" in msg, "拿错载荷 ⇒ rc=2（%s）" % msg[:40])

    print("\n[U8] 负向：组合校验（换完后整棵树对不上）必须能红")
    reset_old()
    # 清单里写一个**错的** base.sha256 ⇒ 换入成功但组合校验必须失败并回滚
    man_bad_tree = json.loads(json.dumps(manifest))
    man_bad_tree["base"]["sha256"] = "f" * 64
    rc, msg, _ = U.apply_update(man_bad_tree, patch, payload, target)
    ok(rc == 1 and "组合校验失败" in msg, "文件树哈希对不上 ⇒ rc=1（%s）" % msg[:40])
    ok(open(os.path.join(target, "agent", "a.py"), encoding="utf-8").read() == old["agent/a.py"], "已回滚到旧内容")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n==== 更新器判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
