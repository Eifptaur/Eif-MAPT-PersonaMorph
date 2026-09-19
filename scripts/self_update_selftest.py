#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""群相 · **整包自更新**判据（`agent/update_apply.py`）—— 点了「立即更新」真的会更新。

背景（2026-09-16 用户当场发火）：「做出来居然不给用户用，你是什么意思」——
引擎（`scripts/pm_update.py` 的增量 patch）早就做好了，但控制台那个「立即更新」按钮
只打印一句指路文案，而且指向的启动器按钮根本不存在。本判据守的就是"这条链真的通"：

  ① **包内容与清单对不上 ⇒ 一个文件都不许碰**（先只读验文件树组合哈希，再动盘）
  ② **换入中途出错 ⇒ 必须回滚**（含"新增的撤掉、覆盖的还原"）
  ③ **被占用的文件跳过并如实报告**（正在运行的 exe/dll 换不动，不许假装成功）
  ④ **绝不碰运行时/用户文件**（`data/`、`config.json`、日志）——连包里夹带的 `data/` 也不许落地
  ⑤ 接线：`POST /api/update_apply` 在 do_POST 段、控制台点按钮会轮询进度并调重启、
     **旧的指路文案必须已经消失**（它就是"做出来不给用户用"的原罪）

自包含：临时目录里造"旧版树 + 在线包 zip + 清单"，真跑 `apply_full()`。
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
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from agent import update_apply as UA        # noqa: E402

PASS = FAIL = 0


def ok(cond, msg, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   " + msg)
    else:
        FAIL += 1
        print("  FAIL " + msg + ("   [%s]" % extra if extra else ""))


def write(p, s):
    # newline="" 必须写：Windows 文本模式会把 \n 变 \r\n ⇒ 哈希与包里对不上（update_selftest 栽过）
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(s)


def make_pkg(zp, items):
    """造一个与在线包同构的 zip（顶层目录 `persona morph/`）。"""
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, body in items.items():
            z.writestr("persona morph/" + rel, body)


def tree_of(items):
    files = {r: hashlib.sha256(b.encode()).hexdigest() for r, b in items.items()}
    h = hashlib.sha256()
    for r in sorted(files):
        h.update(r.encode("utf-8"))
        h.update(files[r].encode("ascii"))
    return h.hexdigest(), files


def snap(root):
    """整棵树的 {rel: sha256}（用于断言"一个文件都没动"）。"""
    out = {}
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, root).replace("\\", "/")
            out[rel] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out


tmp = tempfile.mkdtemp(prefix="pm-selfup-")
try:
    target = os.path.join(tmp, "install")
    old = {"agent/a.py": "A=1\n", "agent/b.py": "B=1\n", "README.md": "old\n"}
    for rel, body in old.items():
        write(os.path.join(target, rel.replace("/", os.sep)), body)
    write(os.path.join(target, "data", "user_notes.txt"), "用户数据，别动\n")
    write(os.path.join(target, "config.json"), '{"api": {"model": "mine"}}\n')

    new = {"agent/a.py": "A=2  # changed\n", "agent/b.py": "B=1\n",
           "agent/c.py": "C=1\n", "README.md": "new readme\n"}
    # ⚠️ 包里**夹带**一个 data/ 条目：在线包里本来没有它，但闸门必须挡住（第二道闸）
    pkg_items = dict(new)
    pkg_items["data/should_not_land.txt"] = "包里的脏东西\n"
    pkg = os.path.join(tmp, "persona-morph-v9.9.9.zip")
    make_pkg(pkg, pkg_items)
    want_tree, files_map = tree_of(pkg_items)

    man = {"schema": "persona-morph/1",
           "base": {"version": "9.9.9", "sha256": want_tree, "url": pkg, "size": 1, "files": len(new)},
           "announce": {"version": "9.9.9", "notes": ["判据自测"]}}

    print("── A. 只读校验：算出来的文件树哈希 == 清单 ──")
    got_tree, got_files, top, n = UA.zip_tree(pkg)
    ok(got_tree == want_tree, "zip_tree 与 make_manifest 同一算法（%s…）" % got_tree[:12], got_tree[:12])
    ok(top == "persona morph" and n == len(pkg_items), "顶层目录与条数读对", "%s / %d" % (top, n))

    print("── B. 干跑：只说不做 ──")
    before = snap(target)
    rc, msg, det = UA.apply_full(man, pkg, target, dry=True)
    ok(rc == 0 and "干跑" in msg, "dry ⇒ rc=0 且说明是干跑", msg[:60])
    ok(snap(target) == before, "干跑没动任何文件")

    print("── C. 正常整包换入 ──")
    rc, msg, det = UA.apply_full(man, pkg, target)
    ok(rc == 0, "rc=0（%s）" % msg[:70], msg[:120])
    ok(open(os.path.join(target, "agent", "a.py"), encoding="utf-8").read() == new["agent/a.py"], "改过的文件已换新")
    ok(os.path.exists(os.path.join(target, "agent", "c.py")), "新增的文件已就位")
    ok(not det.get("locked"), "没有「被占用」的假报")
    ok(not os.path.exists(os.path.join(target, "data", "should_not_land.txt")),
       "**包里夹带的 data/ 条目没落地**（运行时目录第二道闸）")
    ok(open(os.path.join(target, "data", "user_notes.txt"), encoding="utf-8").read().strip() == "用户数据，别动",
       "**用户数据没被动**")
    ok(json.load(open(os.path.join(target, "config.json"), encoding="utf-8"))["api"]["model"] == "mine",
       "**config.json 没被动**")
    st = json.load(open(os.path.join(target, "data", "installed.json"), encoding="utf-8"))
    ok(st["version"] == "9.9.9" and st["sha256"] == want_tree, "版本记录已更新（可追溯）")

    print("── D. 版本相同 ⇒ 什么都不做 ──")
    rc, msg, _ = UA.apply_full(man, pkg, target)
    ok(rc == 0 and "已是最新" in msg, "同版本 ⇒ rc=0 已是最新", msg[:40])

    print("── E. 包被篡改（内容与清单对不上）⇒ 一个文件都不许碰 ──")
    for rel, body in old.items():
        write(os.path.join(target, rel.replace("/", os.sep)), body)
    if os.path.exists(os.path.join(target, "agent", "c.py")):
        os.remove(os.path.join(target, "agent", "c.py"))
    write(os.path.join(target, "data", "installed.json"), json.dumps({"version": "1.0.0", "sha256": "x" * 64}))
    tampered = os.path.join(tmp, "tampered.zip")
    bad_items = dict(pkg_items)
    bad_items["agent/a.py"] = "A=2 TAMPERED\n"
    make_pkg(tampered, bad_items)
    before = snap(target)
    rc, msg, det = UA.apply_full(man, tampered, target)
    ok(rc == 1 and "对不上" in msg, "哈希不符 ⇒ rc=1（%s）" % msg[:46], msg[:90])
    ok(snap(target) == before, "**整棵树一个字节都没动**（先只读验再动盘）")

    print("── F. 换入中途出错 ⇒ 回滚（覆盖的还原、新增的撤掉）──")
    real_copy2 = shutil.copy2
    _tgt = os.path.abspath(target)

    def _into_target(dst):
        # ⚠️ 只拦"写进安装目录"的那一次：快照那一步也会 copy2 到临时备份目录，
        #    文件名同样是 agent/b.py —— 第一版就把它一起拦了，结果连快照都没做成就炸了
        return os.path.abspath(str(dst)).startswith(_tgt)

    def boom(src, dst, *a, **k):
        if _into_target(dst) and str(dst).replace("\\", "/").endswith("agent/b.py"):
            raise OSError("模拟磁盘错误")
        return real_copy2(src, dst, *a, **k)

    shutil.copy2 = boom
    try:
        rc, msg, det = UA.apply_full(man, pkg, target)
    finally:
        shutil.copy2 = real_copy2
    ok(rc == 1 and "回滚" in msg, "中途出错 ⇒ rc=1 且已回滚（%s）" % msg[:46], msg[:90])
    ok(open(os.path.join(target, "agent", "a.py"), encoding="utf-8").read() == old["agent/a.py"], "覆盖的已还原")
    ok(not os.path.exists(os.path.join(target, "agent", "c.py")), "新增的已撤掉")

    print("── G. 文件被占用 ⇒ 跳过并如实报告（不许假装成功）——含 V3 回归 ──")
    def locked(src, dst, *a, **k):
        if _into_target(dst) and str(dst).replace("\\", "/").endswith("agent/c.py"):
            # ⚠️ 2026-09-20 修 V3 之后，判据只看 **winerror ∈ (32,33)**（真·共享冲突）；
            #    原来这里只构造了文本 + errno，没有 winerror ⇒ 会被正确地当成"真故障"回滚。
            #    桩要跟真实 Windows 一样把 winerror 带上（这才是"文件正被占用"的真形态）。
            e = PermissionError(13, "另一个程序正在使用此文件")
            try:
                e.winerror = 32
            except Exception:
                pass
            raise e
        return real_copy2(src, dst, *a, **k)

    shutil.copy2 = locked
    try:
        rc, msg, det = UA.apply_full(man, pkg, target)
    finally:
        shutil.copy2 = real_copy2
    ok(rc == 0 and det.get("locked"), "占用件 ⇒ 仍算成功但带 locked 清单", str(det.get("locked"))[:80])
    ok("正被使用" in msg and "agent/c.py" in msg, "消息里点名了没换成的那几件", msg[:100])
    ok(det.get("status") == "partial", "状态标成 partial（不许说成「已装好」）", str(det.get("status")))
    ok("再点一次" in msg, "消息给出可照着做的下一步（再点一次更新即可补换）", msg[:120])
    st = json.load(open(os.path.join(target, "data", "installed.json"), encoding="utf-8"))
    ok(st.get("locked"), "记录里留了 locked（下次重跑能接着换）")
    # ⭐ V3 的核心回归：版本**不许**被推上去（否则"再点一次"会被短路成"已是最新"）
    ok(str(st.get("version") or "") != str((man.get("base") or {}).get("version") or "?"),
       "版本没有推到位（V3：推了位就再也补不回来了）", str(st.get("version")))
    ok(det.get("pending") == ["agent/c.py"], "待补清单记下了那一件", str(det.get("pending")))
    # ⭐ 解锁后重跑必须真的补换（这才是"以后还能补"的证明，不只是记了一笔）
    rc2, msg2, det2 = UA.apply_full(man, pkg, target)
    ok(open(os.path.join(target, "agent", "c.py"), encoding="utf-8").read() == new["agent/c.py"],
       "解锁后重跑 ⇒ 真的补换（V3 回归）", "rc=%s msg=%s" % (rc2, str(msg2)[:60]))
    ok(open(os.path.join(target, "agent", "a.py"), encoding="utf-8").read() == new["agent/a.py"],
       "没被占用的那几件照样换新了")

    print("── H. 下载：本地路径也支持（离线自测/内网中转）──")
    src_zip = os.path.join(tmp, "src.zip")
    make_pkg(src_zip, new)
    dst = os.path.join(tmp, "dl", "out.zip")
    ok_dl, why = UA.download(src_zip, dst)
    ok(ok_dl and os.path.exists(dst) and UA.sha256_file(dst) == UA.sha256_file(src_zip), "本地路径下载 = 复制成功", why)
    ok_dl2, why2 = UA.download("", dst)
    ok(not ok_dl2 and "没给下载地址" in why2, "没给地址 ⇒ 明确报错（不是静默）", why2)
    ok_dl3, why3 = UA.download("http://127.0.0.1:1/nope.zip", dst, timeout=2)
    ok(not ok_dl3 and "下载失败" in why3, "下载失败 ⇒ 明确报错", why3[:60])
    ok(not os.path.exists(dst + ".part"), "失败后不留半截文件（.part 已清）")

    print("── I. run_once 一条龙（清单直接给，不走网络）──")
    for rel, body in old.items():
        write(os.path.join(target, rel.replace("/", os.sep)), body)
    if os.path.exists(os.path.join(target, "agent", "c.py")):
        os.remove(os.path.join(target, "agent", "c.py"))
    write(os.path.join(target, "data", "installed.json"), json.dumps({"version": "1.0.0", "sha256": "x" * 64}))
    # ⚠️ `run_once` 会拿**真实的本机版本**比大小：这里必须给一个比它大的版本号，否则会走"已是最新"分支
    man_i = json.loads(json.dumps(man))
    man_i["base"]["version"] = "9999.1.1"
    # 走**下载**这条路（base.url 指向本地包），才能顺带验"装完清缓存"
    man_i["base"]["url"] = pkg
    r = UA.run_once(manifest=man_i, zip_path=None, target=target)
    ok(r["ok"] and r["version"] == "9999.1.1", "run_once 成功返回版本", str(r)[:110])
    ok(not os.path.exists(os.path.join(target, UA.CACHE_REL, "persona-morph-9999.1.1.zip")),
       "装完把下载缓存删掉了（下载类功能必须有清理措施）")
    ok(r.get("needRestart") is True, "成功 ⇒ needRestart=True（控制台据此调 /api/restart）")
    j = UA.job()
    ok(j["state"] == "done" and j["msg"], "作业状态可被控制台读到（state=%s）" % j["state"], str(j)[:110])

    print("── J. start_async 防重入 ──")
    UA._set(state="running", phase="download")
    r2 = UA.start_async(target=target)
    ok(r2.get("note") == "更新已经在做了", "正在跑的时候再点 ⇒ 不重复起线程", str(r2)[:90])
    UA._set(state="idle", phase="")

    print("── K. 接线（源码级，防以后改回去）──")
    _web = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
    _con = open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
    _uc = open(os.path.join(ROOT, "agent", "update_check.py"), encoding="utf-8").read()
    _post_seg = _web[_web.index("def do_POST(self):"):]
    ok('/api/update_apply' in _post_seg and 'update_apply as _ua' in _post_seg,
       "POST /api/update_apply 在 do_POST 段里（不在 GET）")
    ok('_st["job"] = _ua.job()' in _web, "GET /api/update 带出作业进度（控制台才轮询得到）")
    ok('out["baseUrl"]' in _uc and 'out["baseSha256"]' in _uc, "清单里的下载地址/树哈希对上层可见")
    ok("/api/update_apply" in _con and "pollJob" in _con and "/api/restart" in _con,
       "控制台：点按钮 → 起更新 → 轮询进度 → 成功调重启")
    ok("更新动作在启动器里" not in _con,
       "**旧的指路文案已消失**（那句就是「做出来不给用户用」的原罪）")
    _go = _con[_con.index("document.getElementById('updGo').onclick"):]
    _go = _go[:_go.index("})();")]
    ok("uiConfirm" in _go, "点「立即更新」有二次确认（改文件的事不许一键无确认）")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ── 2026-09-17：用户转述「控制台上面的更新用不了，卡在 0% 不动，我都是直接去原地址下载覆盖的」 ──
#    根因＝老下载链是 120 秒/源 × 4 个源（最坏 8 分钟界面钉在 0%），而且**换源是静默的**
#    ⇒ 界面只有「0%」一个信息，用户只能判断它死了。这里把三条钉住。
_UA_SRC = open(os.path.join(ROOT, "agent", "update_apply.py"), encoding="utf-8").read()
ok(UA.STALL_S == 20.0 and "timeout: float = STALL_S" in _UA_SRC,
   "单个源的卡死判据＝20 秒（不是老值 120 秒；它是「没数据」判据，不是总时长上限）")
ok(len(UA.DL_MIRRORS) >= 4, "镜像源至少 4 个（含 gh.llkk.cc）   [%s]" % (UA.DL_MIRRORS,))
ok("progress(0, 0)" in _UA_SRC and "on_try(i, len(urls), u" in _UA_SRC,
   "换源前先把进度归零、并回调 on_try（界面看得见「在换源」，不是僵住的百分比）")
ok("换下一个源" in _UA_SRC and "_src_name" in _UA_SRC,
   "作业状态里写明「源 k/n（当前是谁）/ 上一个源不通，换下一个」")
ok("手动下载覆盖" in _UA_SRC, "全失败时把官方地址带回去（用户至少能手动下载覆盖）")
# 行为：5 个源全失败（把 _dl_once 钉成必失败），on_try/progress 的次序与内容要对
_saved_dl_once = UA._dl_once
_calls, _prog = [], []
try:
    UA._dl_once = lambda u, d, t, p=None: (False, "boom")
    _okd, _why = UA.download(
        "https://github.com/Eifptaur/Eif-MAPT-PersonaMorph/releases/download/v2.1.22/x.zip",
        os.path.join(tmp, "never.zip"),
        progress=lambda g, t: _prog.append((g, t)),
        on_try=lambda i, n, u, w="": _calls.append((i, n, u, w)))
finally:
    UA._dl_once = _saved_dl_once
_n_src = len(UA.DL_MIRRORS) + 1
ok(_okd is False, "全源失败 ⇒ 如实返回 False（不许假装成功）")
ok(len([c for c in _calls if c[0] > 0]) == _n_src,
   "官方直连 + 每个镜像**逐个都试过**   [试了 %d 个]" % len([c for c in _calls if c[0] > 0]))
ok(all(c[1] == _n_src for c in _calls if c[0] > 0),
   "每次回调都带上「总共几个源」（界面能显示 2/5）")
ok(len(_prog) >= _n_src and _prog.count((0, 0)) >= _n_src,
   "每个源开始前都归零一次进度（(0, 0) × 源数）")
ok("手动下载覆盖" in _why and "github.com" in _why,
   "失败原因里带官方地址（用户能照着手动下载覆盖）")

print("\n==== 自更新判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
