# -*- coding: utf-8 -*-
"""更新检查判据（群相）—— 三态如实、坏清单不假装最新、版本按数值比较、不污染真状态文件。

设计见 `docs/设计-本体与DLC.md` §五。
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from agent import update_check as UC      # noqa: E402
from agent.version import VERSION         # noqa: E402

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✔ " + msg)
    else:
        FAIL += 1
        print("  ✘ " + msg)


REAL_STATE = os.path.join(ROOT, "data", "update_state.json")
real_before = open(REAL_STATE, "rb").read() if os.path.exists(REAL_STATE) else None
real_sha = hashlib.sha256(real_before).hexdigest() if real_before else None

tmp = tempfile.mkdtemp(prefix="pm-updchk-")
try:
    # 把状态文件指到临时目录：判据**不许写用户的 data/**
    UC._state_path = lambda: os.path.join(tmp, "update_state.json")

    def mk_manifest(path, ver, notes=None):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"schema": "persona-morph/1",
                       "base": {"version": ver, "sha256": "a" * 64, "url": "", "size": 1, "files": 1},
                       "dlc": [], "announce": {"version": ver, "notes": notes or [], "forceBase": False,
                                               "minBase": ver}}, fh, ensure_ascii=False)
        return path

    newp = mk_manifest(os.path.join(tmp, "new.json"), "2099.1.1.1", ["要点一", "要点二"])
    samep = mk_manifest(os.path.join(tmp, "same.json"), VERSION)
    oldp = mk_manifest(os.path.join(tmp, "old.json"), "2000.1.1.1")
    nover = os.path.join(tmp, "nover.json")
    json.dump({"schema": "persona-morph/1", "base": {}, "announce": {}}, open(nover, "w", encoding="utf-8"))
    badp = os.path.join(tmp, "bad.json")
    open(badp, "w", encoding="utf-8").write("{ 这不是 JSON ")

    print("[U1] 没配更新源 ⇒ off（界面什么都不显示）")
    r = UC.state({"url": ""})
    ok(r["status"] == "off" and "没配" in r["why"], "空 url ⇒ off（%s）" % r["why"])

    print("\n[U2] 用户开了不再提醒 / 不再提醒这个版本 ⇒ off")
    ok(UC.state({"url": newp, "muted": True})["status"] == "off", "muted ⇒ off")
    ok(UC.state({"url": newp, "skip_version": "2099.1.1.1"})["status"] == "off", "skip_version 命中 ⇒ off")
    ok(UC.state({"url": newp, "skip_version": "其它版本"})["status"] == "newer", "skip 的是别的版本 ⇒ 正常判 newer")

    print("\n[U3] 有新版本 ⇒ newer（要点透传，供公告条显示）")
    r = UC.state({"url": newp})
    ok(r["status"] == "newer", "status=newer")
    ok(r["mine"] == VERSION and r["theirs"] == "2099.1.1.1", "本机 %s / 远端 %s" % (r["mine"], r["theirs"]))
    ok(r["notes"] == ["要点一", "要点二"], "notes 原样透传（%s）" % r["notes"])
    ok(r["checkedAt"] > 0, "记了检查时间")

    print("\n[U4] 版本相同/更旧")
    ok(UC.state({"url": samep})["status"] == "current", "同版本 ⇒ current")
    r = UC.state({"url": oldp})
    ok(r["status"] == "older" and "旧" in r["why"], "远端更旧 ⇒ older（%s）" % r["why"][:26])

    print("\n[U5] 坏清单**不许**被当成「已是最新」（负向，关键）")
    r = UC.state({"url": badp})
    ok(r["status"] == "error", "非法 JSON ⇒ error（不是 current）")
    ok("JSON" in r["why"], "原因写清了（%s）" % r["why"][:30])
    r2 = UC.state({"url": nover})
    ok(r2["status"] == "error", "清单缺版本号 ⇒ error")
    r3 = UC.state({"url": os.path.join(tmp, "不存在的文件.json")})
    ok(r3["status"] == "error", "拉不到 ⇒ error")

    print("\n[U6] 版本比较必须按**数值**（字符串比较会错）")
    ok(UC.vtuple("2026.9.9") < UC.vtuple("2026.9.15"), "2026.9.9 < 2026.9.15（数值）")
    # 这条是**反证**：字符串比较下 "2026.9.9" > "2026.9.15" 为真（'9' > '1'）⇒ 所以必须用数值元组
    ok(str("2026.9.9") > str("2026.9.15"), "反证：字符串比较确实会判反（故本模块用数值元组）")
    ok(UC.vtuple("x.y") == (), "非法版本 → 空元组")
    ok(UC.vtuple(VERSION) != (), "本机版本可解析")

    print("\n[U7] 状态文件（只看它写了什么，且写在临时目录里）")
    p = os.path.join(tmp, "update_state.json")
    ok(os.path.exists(p), "状态文件落盘")
    st = json.load(open(p, encoding="utf-8"))
    ok("lastCheck" in st and "lastStatus" in st, "记了 lastCheck / lastStatus（%s）" % st.get("lastStatus"))
    ok(st.get("lastStatus") == "error", "最后一次是 error（刚才那个不存在的文件）")

    print("\n[U8] 判据不污染真状态文件")
    real_now = hashlib.sha256(open(REAL_STATE, "rb").read()).hexdigest() if os.path.exists(REAL_STATE) else None
    ok(real_now == real_sha, "真 data/update_state.json 跑前跑后一致（%s）" % ("不存在则都为空" if real_sha is None else "sha 相同"))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n==== 更新检查判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
