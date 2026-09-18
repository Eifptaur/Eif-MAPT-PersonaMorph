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
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from agent import update_check as UC      # noqa: E402
from agent.version import VERSION         # noqa: E402

PASS = FAIL = 0


def ok(cond, msg, detail=""):
    """`detail` 可选：给了就拼在消息后面（新增判据常用，别为它另写一个 helper）。"""
    global PASS, FAIL
    if detail:
        msg = "%s  [%s]" % (msg, detail)
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
    # 把状态文件指到临时目录：自检**不许写用户的 data/**
    UC._state_path = lambda: os.path.join(tmp, "update_state.json")

    def mk_manifest(path, ver, notes=None, build=None):
        with open(path, "w", encoding="utf-8") as fh:
            _b = {"version": ver, "sha256": "a" * 64, "url": "", "size": 1, "files": 1}
            if build is not None:
                _b["build"] = build
            json.dump({"schema": "persona-morph/1",
                       "base": _b,
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

    print("\n[U4b] 同名版本换包：**内容指纹不同也要能看出来**（2026-09-16 用户一问逼出来：「那就没有办法"
          "让他们也接到更新提示吗」；只比版本号字符串的话，同版本换包对已装用户永远静默）")
    import agent.version as VER
    _mine = str(getattr(VER, "BUILD", "") or "")
    if not _mine:
        # 开发树里 BUILD 是空串（只有出包时才写）⇒ 打桩一个本机指纹，才验得动这条逻辑
        VER.BUILD = "deadbeef0000"
        _mine = "deadbeef0000"
    same_b = mk_manifest(os.path.join(tmp, "same_build.json"), VERSION, build=_mine)
    diff_b = mk_manifest(os.path.join(tmp, "diff_build.json"), VERSION, build="0123456789ab")
    no_b = mk_manifest(os.path.join(tmp, "nobuild.json"), VERSION)
    ok(UC.state({"url": same_b})["status"] == "current", "同版本 + 指纹相同 ⇒ current")
    _r = UC.state({"url": diff_b})
    ok(_r["status"] == "newer" and "内容指纹" in _r["why"],
       "同版本 + **指纹不同** ⇒ newer（%s）" % str(_r["why"])[:60])
    ok(_r["build"] == "0123456789ab" and _r["mineBuild"] == _mine, "两侧指纹都透传出来")
    ok(UC.state({"url": no_b})["status"] == "current", "远端没有指纹 ⇒ 按原口径 current（**不误报**）")

    print("\n[U4c] 内容指纹本身：稳定、随内容变、且**不许自指**（改 BUILD 行不影响指纹）")
    _f1 = VER.build_fingerprint(["agent/version.py", "agent/update_check.py"], root=ROOT)
    _f2 = VER.build_fingerprint(["agent/version.py", "agent/update_check.py"], root=ROOT)
    ok(_f1 == _f2 and len(_f1) == 12, "同输入两次算出来一样（%s）" % _f1)
    _old_build = VER.BUILD
    _vpath = os.path.join(ROOT, "agent", "version.py")
    _src = open(_vpath, encoding="utf-8").read()
    try:
        VER.write_build("ffffffffffff")
        _f3 = VER.build_fingerprint(["agent/version.py", "agent/update_check.py"], root=ROOT)
    finally:
        open(_vpath, "w", encoding="utf-8", newline="").write(_src)
        VER.BUILD = _old_build
    ok(_f3 == _f1, "改了 `agent/version.py` 里的 BUILD 行 ⇒ 指纹**不变**（防自指死循环）")

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
# 2026-09-15 补：以前这条公告**一条自检都没有**（后端五态有自检，前端公告条全靠肉眼）。
_H = open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
_W = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
_i = _H.find('id="updBar"')
_seg = _H[_i:_i + 2600] if _i > 0 else ""
# ⚠️ 2026-09-17 教训：**固定长度的窗口很脆** —— 在 `#updBar` 下面新增一个横幅（版本门「发送已被暂停」）
#   就把「不再提醒」那段 JS 挤出了 2600 字窗口 ⇒ 这条断言假红（代码本身没毛病）。
#   改成"先在窗口里找，找不到就退回整个文件找"：保住"看的是更新条那一块"的针对性，又不被无关插入绊倒。
def _has(*needles):
    return all(n in _seg for n in needles) or all(n in _H for n in needles)

ok(_i > 0, "控制台有公告条 #updBar")
ok('id="updText"' in _seg and 'id="updGo"' in _seg and 'id="updLater"' in _seg and 'id="updSkip"' in _seg,
   "公告文案 + 三个按钮都在（立即更新 / 稍后 / 不再提醒这个版本）")
for st in ("'newer'", "'older'", "'error'"):
    ok(st in _seg, "有 %s 分支" % st)
ok("有新版本" in _seg and "s.notes" in _seg, "newer 分支写「有新版本」并把公告要点拼上")
ok(_seg.count("'warn'") >= 2, "older 与 error 都走 warn 样式（不是静默）")
ok("hide()" in _seg and "else { hide(); }" in _seg, "其余状态（current / off）走隐藏")
ok(_has("getElementById('updLater').onclick = hide"), "「稍后」＝只隐藏，不发任何请求")
ok(_has("fetch('/api/update')"), "取数只打 /api/update")
ok(_has("/api/update_skip", "cur.theirs"), "「不再提醒」＝POST /api/update_skip 且带上版本号")
ok("立即更新" in _H and "alert(" not in _seg, "「立即更新」＝就地给指引，**不弹窗**")
# 2026-09-16 补（给用户看公告条时当场发现的真缺陷）：条子的描边原来写 `var(--line,…)`，
#   而四套主题里**只有 `--bd` 没有 `--line`** ⇒ 描边永远落到硬编码的深灰 `#2a2f37`，
#   浅色主题下就是"白卡配深灰边"、不跟主题走。断言：描边必须优先取主题变量。
ok("var(--bd" in _seg, "公告条描边跟主题走（`--bd`，不是硬编码 `--line`）")
# 同一轮实拍发现的第二个外观缺陷：要点一长，三个按钮被压成"一个字一行"。
ok("min-width:0" in _seg and "white-space:nowrap" in _seg,
   "公告条正文可换行、按钮不被压成竖排（flex:none + nowrap）")
ok('"/api/update"' in _W and '"/api/update_skip"' in _W, "后端路由都在：GET /api/update + POST /api/update_skip")

print("\n[U10] 更新源拉不到 ⇒ 并行试备用源（2026-09-16 用户报「更新源异常：拉不到更新源：The read operation timed out」）")
ok(len(UC.DEFAULT_URLS) >= 3 and UC.DEFAULT_URLS[0] == UC.DEFAULT_URL, "内置多个源且 raw 排第一")
ok(all(str(u).startswith("https://") for u in UC.DEFAULT_URLS), "备用源都是 https")
ok(all("/Eifptaur/Eif-MAPT-PersonaMorph" in u and "persona-morph-manifest.json" in u
       for u in UC.DEFAULT_URLS),
   "每个源都指向同一份清单（同一仓库 + 同一文件名；镜像加前缀、api 走 contents 路径都算）")
ok(len(UC.DEFAULT_URLS) >= 8, "源足够多（2026-09-18 那台机器「一直 timeout」后扩容到 9 条）",
   str(len(UC.DEFAULT_URLS)))
ok(any("api.github.com" in u for u in UC.DEFAULT_URLS),
   "有一条**完全不同网络路径**的兜底源（api.github.com contents 接口）")
_MAN = {"schema": "persona-morph/1",
        "base": {"version": "2099.1.1.1", "sha256": "b" * 64, "url": "", "size": 1, "files": 1},
        "dlc": [], "announce": {"version": "2099.1.1.1", "notes": ["备用源可用"],
                                "forceBase": False, "minBase": "2099.1.1.1"}}
_real_fetch = UC.fetch


def _fake_first_dead(u, timeout=6.0):
    """只让**备用源**通：raw 与「用户自填的源」都不通（这样两条路才验得准）。"""
    if u == UC.DEFAULT_URLS[1]:
        return dict(_MAN), ""
    return None, "The read operation timed out"


try:
    UC.fetch = _fake_first_dead
    _man, _why, _used = UC.fetch_any(list(UC.DEFAULT_URLS))
finally:
    UC.fetch = _real_fetch
ok(_man is not None and _used == UC.DEFAULT_URLS[1],
   "第一个源超时 ⇒ 仍然拿到清单（走备用源）", str(_used))


def _fake_all_dead(u, timeout=6.0):
    return None, "timeout"


try:
    UC.fetch = _fake_all_dead
    _m2, _w2, _u2 = UC.fetch_any(list(UC.DEFAULT_URLS))
finally:
    UC.fetch = _real_fetch
ok(_m2 is None and "所有源都拉不到" in _w2,
   "所有源都不通 ⇒ 如实说「所有源都拉不到」", _w2[:60])

_nowp = os.path.join(tmp, "u10state.json")
UC._state_path = lambda: _nowp
try:
    if os.path.exists(_nowp):
        os.remove(_nowp)
except Exception:
    pass
try:
    UC.fetch = _fake_first_dead
    _r10 = UC.state({"url": ""})
finally:
    UC.fetch = _real_fetch
ok(_r10["status"] != "error",
   "默认源场景：raw 超时也能判出状态（不是 error）",
   "%s / %s" % (_r10["status"], _r10["why"][:44]))
_st10 = json.load(open(_nowp, encoding="utf-8")) if os.path.exists(_nowp) else {}
ok(_st10.get("lastGoodUrl") == UC.DEFAULT_URLS[1],
   "把能用的那个源记进状态（下次先试它）", str(_st10.get("lastGoodUrl")))
try:
    UC.fetch = _fake_first_dead
    _r11 = UC.state({"url": "https://example.com/mine.json"})
finally:
    UC.fetch = _real_fetch
ok(_r11["status"] == "error",
   "用户自填的源失败 ⇒ 如实报 error（不去偷偷换别人的源）", _r11["status"])
_UA = open(os.path.join(ROOT, "agent", "update_apply.py"), encoding="utf-8").read()
ok("DL_MIRRORS" in _UA and 'if "github.com" in str(url).lower()' in _UA,
   "下载资产也有镜像兜底（DL_MIRRORS，且只对 github.com 套前缀）")

print("\n[U12] 「立即更新」第一下就要走得通（2026-09-17 用户报：「第一次一定拉不到更新源，第二次才能成功」）")
# 假网络＝**只有备用源（镜像）通**，raw 与自填源一律超时 —— 这正是他那边的网络情况。
from agent import update_apply as UA                    # noqa: E402
_UC_SRC = open(os.path.join(ROOT, "agent", "update_check.py"), encoding="utf-8").read()
ok("def candidate_urls" in _UC_SRC and "candidate_urls" in _UA,
   "两条路共用同一份候选源（检查与更新不再各写一套）")
_nowp2 = os.path.join(tmp, "u12state.json")
try:
    os.remove(_nowp2)
except Exception:
    pass
_real_state_path = UC._state_path
UC._state_path = lambda: _nowp2
try:
    UC.fetch = _fake_first_dead
    # ① 复现机制：只试"一个"默认源（raw）——旧写法就是这么干的 ⇒ 必然拉不到
    _old_way = UC.fetch(UC.manifest_url(), 1.0)[0]
    # ② 新写法：同一份候选表（并行多源）⇒ 镜像那一路能成
    _new_man, _new_why, _new_used = UC.fetch_any(UC.candidate_urls(), 1.0)
    _r12 = UA.run_once(manifest=None, zip_path=os.path.join(tmp, "不存在.zip"), target=ROOT)
finally:
    UC.fetch = _real_fetch
    UC._state_path = _real_state_path
ok(_old_way is None,
   "旧写法（只试一个默认源）在同样的假网络下必然失败 —— 这就是那次报障的机制", str(_old_way)[:40])
ok(_new_man is not None and _new_used == UC.DEFAULT_URLS[1],
   "新写法拿到清单（走备用源）", str(_new_used)[:60])
ok(_r12.get("phase") != "probe" and "拉不到更新源" not in str(_r12.get("why")),
   "「立即更新」不再卡在 probe（走完清单这一步）",
   "%s / %s" % (_r12.get("phase"), str(_r12.get("why"))[:40]))
_st12 = json.load(open(_nowp2, encoding="utf-8")) if os.path.exists(_nowp2) else {}
ok(_st12.get("lastGoodUrl") == UC.DEFAULT_URLS[1],
   "拿到清单后记住这个源（下载也优先走它）", str(_st12.get("lastGoodUrl")))

_dl_urls = []
_real_dl = UA._dl_once
UA._dl_once = lambda u, dest, timeout, progress=None: (_dl_urls.append(u) or (False, "boom"))
try:
    UC._write_state({"lastGoodUrl": UC.DEFAULT_URLS[2]})            # 上次清单走的是 ghfast.top
    UA.download("https://github.com/x/y/releases/download/v1/a.zip", os.path.join(tmp, "a.zip"))
    _first_with_memo = list(_dl_urls)
    _dl_urls[:] = []
    UC._write_state({"lastGoodUrl": UC.DEFAULT_URLS[1]})            # jsDelivr：不是下载镜像 ⇒ 回默认顺序
    UA.download("https://github.com/x/y/releases/download/v1/a.zip", os.path.join(tmp, "a.zip"))
    _first_default = list(_dl_urls)
finally:
    UA._dl_once = _real_dl
ok(len(_first_with_memo) >= 2 and _first_with_memo[1].startswith("https://ghfast.top/"),
   "下载镜像顺序＝**上次清单能用的那个镜像排第一**（第一下就走通的那条）",
   str(_first_with_memo[1])[:56] if len(_first_with_memo) > 1 else str(_first_with_memo))
ok(len(_first_default) >= 2 and _first_default[1].startswith(UA.DL_MIRRORS[0]),
   "上次用的是非镜像源 ⇒ 回默认镜像顺序（不乱改）",
   str(_first_default[1])[:56] if len(_first_default) > 1 else str(_first_default))

print("\n[U13] 过期缓存**不许**抢赢新鲜镜像（2026-09-17 实测：jsDelivr 的 @main 缓存比新版本慢几小时，"
      "而它 0.8s 就答、镜像 0.9s 也有货 ⇒ 按「先到的赢」挑，新版会在控制台里「消失」）")


def _mk13(ver, note):
    return {"schema": "persona-morph/1",
            "base": {"version": ver, "sha256": "c" * 64, "url": "", "size": 1, "files": 1},
            "dlc": [], "announce": {"version": ver, "notes": [note], "forceBase": False, "minBase": ver}}


_stale13 = _mk13("2000.1.1.1", "CDN 旧缓存")
_fresh13 = _mk13("2099.1.1.1", "镜像新清单")
_arrive13 = []


def _short13(u):
    for _i, _x in enumerate(UC.DEFAULT_URLS):
        if _x == u:
            return "源%d" % _i
    return "?"


def _fake_cdn_stale(u, timeout=6.0):
    """jsDelivr＝**先答但是旧的**；ghfast＝**后答但是新的**；其余不通。"""
    if u == UC.DEFAULT_URLS[1]:
        _arrive13.append(u)
        return dict(_stale13), ""
    if u == UC.DEFAULT_URLS[2]:
        time.sleep(0.3)
        _arrive13.append(u)
        return dict(_fresh13), ""
    return None, "The read operation timed out"


_t13 = time.time()
try:
    UC.fetch = _fake_cdn_stale
    _m13, _w13, _u13 = UC.fetch_any(list(UC.DEFAULT_URLS), 1.0)
finally:
    UC.fetch = _real_fetch
_el13 = time.time() - _t13
ok(_m13 is not None and _u13 == UC.DEFAULT_URLS[2] and _m13["base"]["version"] == "2099.1.1.1",
   "版本高的赢（挑的是后到的镜像，不是先到的 CDN）", "%s → %s" % (_u13 == UC.DEFAULT_URLS[2], _m13 and _m13["base"]["version"]))
# 反对照：确认这判据**不是恒真**——先到的确实是那份旧缓存
ok(bool(_arrive13) and _arrive13[0] == UC.DEFAULT_URLS[1] and UC.DEFAULT_URLS[2] in _arrive13,
   "反对照：旧缓存确实是先到的（所以「先到的赢」一定挑错）", str([_short13(u) for u in _arrive13]))


def _hold13():
    pass


def _fake_only_cdn(u, timeout=6.0):
    """只有旧缓存的 CDN 能通；**其余源是慢源**（睡 5 秒）——宽限窗必须替我们踩刹车。"""
    if u == UC.DEFAULT_URLS[1]:
        return dict(_stale13), ""
    time.sleep(5.0)
    return None, "timeout"


_t13b = time.time()
try:
    UC.fetch = _fake_only_cdn
    _m13b, _w13b, _u13b = UC.fetch_any(list(UC.DEFAULT_URLS), 6.0)
finally:
    UC.fetch = _real_fetch
_el13b = time.time() - _t13b
ok(_m13b is not None and _u13b == UC.DEFAULT_URLS[1],
   "只有缓存源能通 ⇒ 照用（兜底不丢）", str(_u13b)[:48])
ok(_el13b < 5.0 - 0.6,
   # ⚠️ 上限原来是 `GRACE_S + 1.2`（3.2s）—— 机器一忙就**假红**（2026-09-18 套跑实测 3.4s）。
   #    真正的契约是"**不等慢源**"（那个假慢源睡 5.0s）⇒ 上限按慢源时长留 0.6s 余量，
   #    这样它验的还是同一件事（宽限窗替我们踩了刹车），但不会再被调度抖动判红。
   "宽限窗有界：不等慢源、也不等超时（%.1fs < 5.0s-0.6s；宽限窗 %.1fs）" % (_el13b, UC.GRACE_S))
ok(_el13b >= UC.GRACE_S - 0.3,
   "反对照：确实等满了宽限窗才定（%.1fs ≈ %.1fs）——不是碰巧提前返回" % (_el13b, UC.GRACE_S))


def _fake_same13(u, timeout=6.0):
    if u in (UC.DEFAULT_URLS[1], UC.DEFAULT_URLS[2]):
        return dict(_MAN), ""
    return None, "timeout"


try:
    UC.fetch = _fake_same13
    _m13c, _w13c, _u13c = UC.fetch_any(list(UC.DEFAULT_URLS), 1.0)
finally:
    UC.fetch = _real_fetch
ok(_u13c == UC.DEFAULT_URLS[1],
   "版本相同 ⇒ 按候选顺序取先者（不因改动乱跳源）", str(_u13c)[:48])

print("\n==== 更新检查判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
