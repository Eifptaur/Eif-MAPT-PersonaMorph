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

    # ⛔ 2026-09-21 加（第四轮审计 **V-R4-14，P2**）：版本比较原来是**裸字符串** ——
    #   `"2026.9.9" > "2026.9.10"` 在字符串序里是 **True**（'9' > '1'）⇒ 远端更旧也报"有新版本"。
    #   注意本文件的 helper 是 `ok(cond, msg)`（**条件在前**），别按别的文件那套写反。
    print("\n[U1b] 版本比较必须按**数值段**（不是字符串序）")
    _man = lambda v: {"base": {"version": v}}                                    # noqa: E731
    _t2 = os.path.join(tmp, "v2")
    _st2 = os.path.join(_t2, U.STATE_REL)          # 用模块自己的常量，别猜路径（`data/installed.json`）
    os.makedirs(os.path.dirname(_st2), exist_ok=True)
    with open(_st2, "w", encoding="utf-8") as _f:
        json.dump({"version": "2026.9.9", "sha256": "x"}, _f)
    _r_new = U.check_update(_man("2026.9.10"), _t2)
    ok(_r_new["status"] == "newer",
       "本地 2026.9.9 / 远端 2026.9.10 ⇒ newer（按数值）：%s" % _r_new["why"])
    _r_old = U.check_update(_man("2026.8.20"), _t2)
    ok(_r_old["status"] == "older",
       "本地 2026.9.9 / 远端 2026.8.20 ⇒ older（按数值：8 < 9）：%s" % _r_old["why"])
    _r_same = U.check_update(_man("2026.9.9.0"), _t2)
    ok(_r_same["status"] in ("newer", "older"),
       "本地 2026.9.9 / 远端 2026.9.9.0 ⇒ 不崩、给出方向（同一个键 ⇒ 按「不同即有新版」）：%s" % _r_same["why"])
    _r_xy = U.check_update(_man("x.y"), _t2)
    ok(_r_xy["status"] in ("newer", "older"),
       "版本号不是数字段式（x.y）也不崩、也不瞎定方向：%s" % _r_xy["why"])
    # 反例锚：老写法（裸字符串比较）在这一组用例上**判错** ⇒ 证明上面两条断言有灵敏度
    _old_cmp = (lambda theirs, mine: theirs > mine)
    ok(_old_cmp("2026.9.9", "2026.9.10") is True,
       "反例锚：裸字符串比较把 2026.9.9 > 2026.9.10 判成 True（老写法确实会误报有新版本）")

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
    # V-R7-5 #1：U4 的失败点其实在**载荷校验**（这一步在换入之前，一个文件都还没动过），所以原来那句
    # "内容已回滚"只证明了"没被改"——把 `rollback()` 换成 no-op 也照样绿。起手把 a.py 写成
    # **既非老版也非新版**的哨兵：只要失败前真换入过文件，判据就能看出"没还原"。
    _SENT = "A=2 SENTINEL-既非老版也非新版\n"
    write(os.path.join(target, "agent", "a.py"), _SENT)
    bad_payload = os.path.join(tmp, "payload-bad.zip")
    make_zip(bad_payload, {"agent/a.py": "A=2 TAMPERED\n", "agent/c.py": new["agent/c.py"]})
    rc, msg, _ = U.apply_update(manifest, patch, bad_payload, target)
    ok(rc == 1 and "哈希不符" in msg, "篡改载荷 ⇒ rc=1（%s）" % msg[:40])
    _after4 = open(os.path.join(target, "agent", "a.py"), encoding="utf-8").read()
    ok(_after4 == _SENT and "TAMPERED" not in _after4,
       "**一个文件都没碰**（校验先于换入；哨兵原样 ⇒ 也不许变成篡改内容）")
    ok(not os.path.exists(os.path.join(target, "agent", "c.py")), "**新增的文件已撤回**")
    ok(json.load(open(os.path.join(target, "data", "installed.json"), encoding="utf-8"))["version"] == "1.0.0",
       "版本记录没被改（回滚彻底）")

    print("\n[U5] 负向：载荷缺件（＝下载中断）⇒ 必须失败且回滚")
    reset_old()
    write(os.path.join(target, "agent", "a.py"), _SENT)      # V-R7-5 #1：同 U4，先放哨兵再验"没被碰"
    short_payload = os.path.join(tmp, "payload-short.zip")
    make_zip(short_payload, {"agent/a.py": new["agent/a.py"]})          # 少了 c.py
    rc, msg, _ = U.apply_update(manifest, patch, short_payload, target)
    ok(rc == 1 and "缺 plan 要求的文件" in msg, "载荷不完整 ⇒ rc=1（%s）" % msg[:40])
    ok(open(os.path.join(target, "agent", "a.py"), encoding="utf-8").read() == _SENT,
       "一个文件都没碰（哨兵原样）")

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
    # V-R7-5 #1：这条是全班**唯一真走到 rollback** 的用例 ⇒ 这里放哨兵当反向锚：
    # 真回滚 ⇒ a.py 回到哨兵；`rollback()` 变 no-op ⇒ a.py 会停在新版内容（判据必红）。
    _SENT8 = "A=2 ROLLBACK-PROBE-既非老版也非新版\n"
    write(os.path.join(target, "agent", "a.py"), _SENT8)
    # 清单里写一个**错的** base.sha256 ⇒ 换入成功但组合校验必须失败并回滚
    man_bad_tree = json.loads(json.dumps(manifest))
    man_bad_tree["base"]["sha256"] = "f" * 64
    rc, msg, _ = U.apply_update(man_bad_tree, patch, payload, target)
    ok(rc == 1 and "组合校验失败" in msg, "文件树哈希对不上 ⇒ rc=1（%s）" % msg[:40])
    _a8 = open(os.path.join(target, "agent", "a.py"), encoding="utf-8").read()
    ok(_a8 == _SENT8, "**真回滚**：回到换入前的内容（哨兵原样）")
    ok(_a8 != new["agent/a.py"], "回滚后**不是**新版内容（与上一条互为反向锚）")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n==== 更新器判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
