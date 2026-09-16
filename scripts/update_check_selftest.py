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

    print("[U1] 更新源：显式填了用填的；**空值回落到内置默认**（老 config 里那个空 url 不许盖掉新默认值）")
    # 2026-09-16 口径更新：原来"空 url ⇒ off"。但老用户的 config.json 是"默认值为空"那阵子存的，
    # 里面那个空 update.url 会把新默认值盖掉 ⇒ 他们永远接不到更新通知。现在空＝没配过 ⇒ 用默认。
    ok(UC.manifest_url({"url": ""}) == UC.DEFAULT_URL, "空 url 回落内置默认")
    ok(UC.manifest_url({}) == UC.DEFAULT_URL, "连这个键都没有也回落默认")
    ok(UC.manifest_url({"url": "https://example.com/x.json"}) == "https://example.com/x.json",
       "显式填了就用填的（回落只对空生效）")

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

print("\n[U9] 控制台「更新公告」条：分支齐、按钮各有各的行为、不弹窗")
# 2026-09-15 补：以前这条公告**一条判据都没有**（后端五态有判据，前端公告条全靠肉眼）。
_H = open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
_W = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
_i = _H.find('id="updBar"')
_seg = _H[_i:_i + 2600] if _i > 0 else ""
ok(_i > 0, "控制台有公告条 #updBar")
ok('id="updText"' in _seg and 'id="updGo"' in _seg and 'id="updLater"' in _seg and 'id="updSkip"' in _seg,
   "公告文案 + 三个按钮都在（立即更新 / 稍后 / 不再提醒这个版本）")
for st in ("'newer'", "'older'", "'error'"):
    ok(st in _seg, "有 %s 分支" % st)
ok("有新版本" in _seg and "s.notes" in _seg, "newer 分支写「有新版本」并把公告要点拼上")
ok(_seg.count("'warn'") >= 2, "older 与 error 都走 warn 样式（不是静默）")
ok("hide()" in _seg and "else { hide(); }" in _seg, "其余状态（current / off）走隐藏")
ok("getElementById('updLater').onclick = hide" in _seg, "「稍后」＝只隐藏，不发任何请求")
ok("/api/update_skip" in _seg and "cur.theirs" in _seg, "「不再提醒」＝POST /api/update_skip 且带上版本号")
ok("立即更新" in _H and "alert(" not in _seg, "「立即更新」＝就地给指引，**不弹窗**")
ok("fetch('/api/update')" in _seg, "取数只打 /api/update")
# 2026-09-16 补（给用户看公告条时当场发现的真缺陷）：条子的描边原来写 `var(--line,…)`，
#   而四套主题里**只有 `--bd` 没有 `--line`** ⇒ 描边永远落到硬编码的深灰 `#2a2f37`，
#   浅色主题下就是"白卡配深灰边"、不跟主题走。断言：描边必须优先取主题变量。
ok("var(--bd" in _seg, "公告条描边跟主题走（`--bd`，不是硬编码 `--line`）")
# 同一轮实拍发现的第二个外观缺陷：要点一长，三个按钮被压成"一个字一行"。
ok("min-width:0" in _seg and "white-space:nowrap" in _seg,
   "公告条正文可换行、按钮不被压成竖排（flex:none + nowrap）")
ok('"/api/update"' in _W and '"/api/update_skip"' in _W, "后端路由都在：GET /api/update + POST /api/update_skip")

print("\n==== 更新检查判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
