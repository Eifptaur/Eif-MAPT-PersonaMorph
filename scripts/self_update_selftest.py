#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""群相 · **整包自更新**判据（`agent/update_apply.py`）—— 点了「立即更新」真的会更新。

背景——
引擎（`scripts/pm_update.py` 的增量 patch）早就做好了，但控制台那个「立即更新」按钮
只打印一句指路文案，而且指向的启动器按钮根本不存在。本判据守的就是"这条链真的通"：

  ① **包内容与清单对不上 ⇒ 一个文件都不许碰**（先只读验文件树组合哈希，再动盘）
  ② **换入中途出错 ⇒ 必须回滚**（含"新增的撤掉、覆盖的还原"）
  ③ **被占用的文件跳过并如实报告**（正在运行的 exe/dll 换不动，不许假装成功）
  ④ **绝不碰运行时/用户文件**（`data/`、`config.json`、日志）——连包里夹带的 `data/` 也不许落地
  ⑤ 接线：`POST /api/update_apply` 在 do_POST 段、控制台点按钮会轮询进度并调重启、
     **旧的指路文案必须已经消失**（它就是"做出来不给用户用"的原罪）
  ⑥ **L 段**：两道闸在**真装那一刻**也生效 —— 走真路径 `run_once()`：
     版本回退清单 / `expires` 过期清单 / 低于 `maxSeenVersion` ⇒ 一律**拒装且一个文件都不动**；
     并断言"同一份清单 `state()` 与 `run_once()` 同一结论"＋反例锚（摘掉闸就必须真装进去）。

自包含：临时目录里造"旧版树 + 在线包 zip + 清单"，真跑 `apply_full()`。
"""
import hashlib
import io
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
sys.path.insert(0, HERE) # 同目录的 `_srcmatch`
import _srcmatch as _sm # noqa: E402 空白容忍的源码断言
import _srcslice
import _iso14 # noqa: E402
_iso14.all_() # 判据不许写产品的 data/ 与 logs/（挪窗借用记录 / 台账 / 锁…全部指到临时区）
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from agent import update_apply as UA # noqa: E402

# ⛔ 版本号跟着**版本体系**走（现行体系是 `V<符号>`）。符号代表几是
#    `version.RUNE_VALUES` 那张**人手写的对照表**定的 ⇒ 判据里**临时登记**三枚符号
#    （正常新版本 / 更旧 / 越界），而不是去改真表（真表只登记实际发过的版本）。
import agent.version as _VERX # noqa: E402
_VERX.RUNE_VALUES["ᛟ"] = 5          # 比当前(ᛞ=4)大一号 ⇒ 正常新版本
_VERX.RUNE_VALUES["ᚠ"] = 3          # 比当前小 ⇒ 更旧
_VERX.RUNE_VALUES["ᛉ"] = 9          # 跨两号以上 ⇒ 用来触发 `far_ahead`（`_vt[0] > _vm[0]+1`）
NEWER_V = "Vᛟ"
OLDER_V = "Vᚠ"
FAR_V = "Vᛉ"

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
# ⛔ `run_once()` 现在会（单调）记 `maxSeenVersion`，而本判据喂的是 9999.x 这种假版本
#   ⇒ 状态文件指到临时目录，**别写用户真的 `data/update_state.json`**（跑一次自检就把他的回滚闸顶高）。
from agent import update_check as _uc_top # noqa: E402
_uc_top._state_path = lambda: os.path.join(tmp, "update_state.json")
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
    # ⚠️ C 段已把目标换成了新版 ⇒ 这里**必须先把目标重置回旧版**，
    #   否则"只换内容不同的文件"这一步会发现"全都一样"、一件都不写 ⇒ 模拟的写失败无从触发。
    for rel, body in old.items():
        write(os.path.join(target, rel.replace("/", os.sep)), body)
    try:
        os.remove(os.path.join(target, "agent", "c.py"))
    except OSError:
        pass
    real_copy2 = shutil.copy2
    _tgt = os.path.abspath(target)

    def _into_target(dst):
        # ⚠️ 只拦"写进安装目录"的那一次：快照那一步也会 copy2 到临时备份目录，
        #    文件名同样是 agent/b.py —— 第一版就把它一起拦了，结果连快照都没做成就炸了
        return os.path.abspath(str(dst)).startswith(_tgt)

    def boom(src, dst, *a, **k):
        # ⚠️ 拦 `agent/a.py`（**内容真变过**的那件），不拦 b.py —— b.py 在包里没变，
        #   新实现"只换内容不同的文件"会正确地跳过它、压根不会写 ⇒ 拦它永远不触发。
        if _into_target(dst) and str(dst).replace("\\", "/").endswith("agent/a.py"):
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

    print("── G. 文件被占用 ⇒ **不肯跳过**、抛回占用者清单（等用户点头）──")
    # ⛔ 口径变更（2026-10-10）：**不许静默跳过**占用件、也不许报"半装 partial"。
    #   现在的行为：撞占用 ⇒ 查占用者 ⇒ `rc=3` + `detail["lockedBy"]` 抛回调用方，
    #   由前端弹窗问用户"要不要临时关掉它"；用户同意后带 `allow_kill=True` 重跑才动手。
    #   （旧判据测的是"跳过并报 partial"——那是被推翻的老口径。）
    # 仍然：先把目标重置成"旧版 + c.py 已存在且**内容与包内不同**"，这条夹具才真正在测
    #   "要换的那件被别人占着"。⚠️ c.py 的现有内容**必须与包内不同** —— 否则新实现
    #   "只换内容不同的文件"会正确地跳过它、占用分支压根不会走到（第一版夹具就踩了这个坑）。
    for rel, body in old.items():
        write(os.path.join(target, rel.replace("/", os.sep)), body)
    write(os.path.join(target, "agent", "c.py"), "C=OLD  # 与包内不同\n")
    write(os.path.join(target, "data", "installed.json"),
          json.dumps({"version": "1.0.0", "sha256": "x" * 64}))

    def locked(src, dst, *a, **k):
        if _into_target(dst) and str(dst).replace("\\", "/").endswith("agent/c.py"):
            # ⚠️ **不许手工给异常贴 `winerror`** —— 那是假绿
            #   （测的是"我自己伪造的占用"）。真实共享冲突走 CRT `open()` 报的就是这个形状：
            #   `PermissionError: [Errno 13] …`（errno=13、**winerror 被丢掉**）；而目标文件
            #   本身**可写**（不是只读）⇒ `_is_locked` 必须判"被占用"。
            raise PermissionError(13, "另一个程序正在使用此文件")
        return real_copy2(src, dst, *a, **k)

    # 第一次：不许杀 ⇒ 应 rc=3 抛回占用者
    shutil.copy2 = locked
    try:
        rc, msg, det = UA.apply_full(man, pkg, target)
    finally:
        shutil.copy2 = real_copy2
    ok(rc == 3, "占用件 ⇒ rc=3（**不肯跳过**，抛回调用方）", "rc=%s msg=%s" % (rc, msg[:80]))
    ok(bool(det.get("lockedBy")) and "agent/c.py" in det["lockedBy"],
       "detail 里带上了被占用的那件（lockedBy）", str(list((det or {}).get("lockedBy") or {}))[:80])
    ok("agent/c.py" in msg or "占用" in msg, "消息里点明了有文件被占用", msg[:120])
    # ⛔ 关键：**不许**把没换到的当作换好了 —— 这次一件都没换成（c.py 被占、a.py/README 未变）
    ok(not os.path.exists(os.path.join(target, "data", "installed.json"))
       or json.load(open(os.path.join(target, "data", "installed.json"), encoding="utf-8")).get("version") != "9.9.9",
       "没把版本推到位（等用户点头才算装）")

    # 第二次：用户点头（allow_kill=True）⇒ `_kill_pids` 关掉占用者后**必须能装成**。
    #   模拟"关掉后句柄释放"：`_kill_pids` 一被调用就把 locked 补丁撤下（c.py 恢复可写）。
    #   ⚠️ 还必须同时 patch `_lock_users` —— 真环境里 c.py 并没有被真占用，`who_locks` 查不到
    #     占用者；而"查不到占用者 ⇒ 即使点头也不乱杀"是**正确的安全设计**（不知道杀谁就别杀）。
    #     所以这里显式给它一个假的占用者，才测得到"点头 → 关掉 → 装成"这条链。
    _released = {"v": False}
    _real_kill = UA._kill_pids
    _real_lock_users = UA._lock_users

    def _fake_kill(pids, killed=None):
        _released["v"] = True # 模拟"进程被关掉、文件不再被占用"
        if killed is not None:
            killed.extend(list(pids or []))
        return list(pids or [])

    def _fake_lock_users(path):
        if str(path).replace("\\", "/").endswith("agent/c.py") and not _released["v"]:
            return [{"pid": 4242, "name": "FakeLocker", "service": "", "restartable": False}]
        return []

    def locked2(src, dst, *a, **k):
        if (not _released["v"]) and _into_target(dst) \
                and str(dst).replace("\\", "/").endswith("agent/c.py"):
            raise PermissionError(13, "另一个程序正在使用此文件")
        return real_copy2(src, dst, *a, **k)

    UA._kill_pids = _fake_kill
    UA._lock_users = _fake_lock_users
    shutil.copy2 = locked2
    try:
        rc2, msg2, det2 = UA.apply_full(man, pkg, target, allow_kill=True, killed=[])
    finally:
        shutil.copy2 = real_copy2
        UA._kill_pids = _real_kill
        UA._lock_users = _real_lock_users
    ok(rc2 == 0, "点头后 ⇒ 关掉占用者、这次真装成（rc=%s）" % rc2, "msg=%s" % str(msg2)[:80])
    ok(open(os.path.join(target, "agent", "c.py"), encoding="utf-8").read() == new["agent/c.py"],
       "被占用那件（c.py）点头后已换上")
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
    man_i["base"]["version"] = NEWER_V
    # 走**下载**这条路（base.url 指向本地包），才能顺带验"装完清缓存"
    man_i["base"]["url"] = pkg
    # ⚠️ 更新链现在**只认官方域的下载地址**（非官方要显式开关）——
    #   这条判据是"离线一条龙"，用本地包当下载地址 ⇒ 显式开这个**测试专用**开关
    #   （生产路径不设它：`allow_local_update()` 只认环境变量或配置里的显式开关）。
    os.environ["PM_ALLOW_LOCAL_UPDATE"] = "1"
    _relaunch_keep = getattr(UA, "_relaunch_after_update", None)
    UA._relaunch_after_update = lambda *a, **k: None
    try:
        r = UA.run_once(manifest=man_i, zip_path=None, target=target)
    finally:
        os.environ.pop("PM_ALLOW_LOCAL_UPDATE", None)
        if _relaunch_keep is not None:
            UA._relaunch_after_update = _relaunch_keep
    ok(r["ok"] and r["version"] == NEWER_V, "run_once 成功返回版本", str(r)[:110])
    ok(not os.path.exists(os.path.join(target, UA.CACHE_REL, "persona-morph-%s.zip" % NEWER_V)),
       "装完把下载缓存删掉了（下载类功能必须有清理措施）")
    ok(r.get("needRestart") is True, "成功 ⇒ needRestart=True（控制台据此调 /api/restart）")
    j = UA.job()
    ok(j["state"] == "done" and j["msg"], "作业状态可被控制台读到（state=%s）" % j["state"], str(j)[:110])

    print("── L. P0：版本回退 / expires 过期 ⇒ `run_once()` 必须**拒装**（不许真降级）──")
    # 现场（审计用假源实测）：`run_once()` **完全绕过** `state()` 的两道闸 ——
    #   · 远端 `2026.9.1.1`（< 本机 `2026.9.21.11`）：`state()` 判 `older`，`run_once()` **照样真装**；
    # · `expires=`state()` 报「清单已过期」，`run_once()` **照装**；
    #   · `maxSeenVersion` 是**死代码**（只有检查侧写、没有任何一侧读）。
    # ⇒ 这一节全部走**真路径** `run_once()`，并断言"同一份清单 `state()` 与 `run_once()` 同一结论"。
    from agent import update_check as _uc2 # noqa: E402
    _sf = os.path.join(tmp, "p0_update_state.json")
    _keep_sp, _keep_fa, _keep_ri = _uc2._state_path, _uc2.fetch_any, _uc2._read_installed
    _keep_gate, _keep_rel2 = _uc2.manifest_gates, UA._relaunch_after_update
    _uc2._state_path = lambda: _sf
    _uc2._read_installed = lambda: {} # 别去读真的 data/installed.json
    UA._relaunch_after_update = lambda *a, **k: None

    def _reset_tree():
        for _rel in ("agent/a.py", "agent/b.py", "README.md"):
            write(os.path.join(target, _rel.replace("/", os.sep)), old[_rel])
        if os.path.exists(os.path.join(target, "agent", "c.py")):
            os.remove(os.path.join(target, "agent", "c.py"))
        write(os.path.join(target, "data", "installed.json"),
              json.dumps({"version": "1.0.0", "sha256": "x" * 64}))

    def _man(ver, extra=None):
        _b = {"version": ver, "sha256": want_tree, "url": pkg, "size": 1, "files": len(new)}
        _b.update(extra or {})
        return {"schema": "persona-morph/1", "base": _b, "announce": {"version": ver, "notes": []}}

    # ⚠️ 低版本那份**必须带一个与本机不同的内容指纹**：否则会先被"已是最新"那条短路吞掉，
    #   这道闸就等于没被测到（审计现场那份清单正是带 build 的）。
    _LOW = _man(OLDER_V, {"build": "deadbeef0000"})
    _EXPIRED = _man(NEWER_V, {"expires": "2000-01-01T00:00:00Z"})
    _FINE = _man(NEWER_V)
    try:
        _reset_tree()
        _before = snap(target)
        _r_low = UA.run_once(manifest=dict(_LOW), zip_path=pkg, target=target)
        ok(_r_low.get("ok") is False and _r_low.get("gate") == "older",
           "**版本回退清单 ⇒ `run_once()` 拒装**（gate=%s）" % _r_low.get("gate"), str(_r_low)[:130])
        ok("还旧" in str(_r_low.get("why")) and "回滚" in str(_r_low.get("why")),
           "…理由说清是「比本机还旧」（不是含糊的失败）", str(_r_low.get("why"))[:90])
        ok(snap(target) == _before,
           "…**整棵树一个字节都没动**（真路径上拒装，不是只报了个错）")

        _r_exp = UA.run_once(manifest=dict(_EXPIRED), zip_path=pkg, target=target)
        ok(_r_exp.get("ok") is False and _r_exp.get("gate") == "expired",
           "**expires 过期清单 ⇒ `run_once()` 拒装**（gate=%s）" % _r_exp.get("gate"), str(_r_exp)[:130])
        ok("过期" in str(_r_exp.get("why")), "…理由里写明了「过期」", str(_r_exp.get("why"))[:90])
        ok(snap(target) == _before, "…树还是没动")

        # 单调版本（rollback）：远端**比见过最高版本低**也要拒（哪怕它比本机新）
        with open(_sf, "w", encoding="utf-8") as _f:
            json.dump({"maxSeenVersion": FAR_V}, _f)
        _r_rb = UA.run_once(manifest=dict(_FINE), zip_path=pkg, target=target)
        ok(_r_rb.get("ok") is False and _r_rb.get("gate") == "rollback",
           "**低于「见过的最高版本」⇒ `run_once()` 也拒**（maxSeenVersion 不再是死代码）",
           str(_r_rb)[:130])
        ok(snap(target) == _before, "…树仍然没动")
        os.remove(_sf)

        # ⭐ P0 的本质：**同一份清单，`state()` 与 `run_once()` 必须同一结论**
        def _state_of(_m):
            _uc2.fetch_any = lambda urls, timeout=12.0, patient=None: (dict(_m), "", "")
            return _uc2.state({"url": "https://example.com/m.json"})

        _pairs = []
        for _nm, _m in (("回退", _LOW), ("过期", _EXPIRED), ("正常新清单", _FINE)):
            _st = _state_of(_m)
            _by_state = _st.get("status") in ("older", "error") # 检查侧：不许据它更新
            _reset_tree()
            _r = UA.run_once(manifest=dict(_m), zip_path=pkg, target=target)
            _by_apply = not _r.get("ok") # 安装侧：拒装
            _pairs.append((_nm, _st.get("status"), _by_state, _by_apply))
        ok(all(a == b for _n, _s, a, b in _pairs),
           "**同一份清单 `state()` 与 `run_once()` 结论一致**（三份清单逐个对）",
           "；".join("%s：state=%s 拒绝=%s/装=%s" % (_n, _s, a, b) for _n, _s, a, b in _pairs))
        ok([_n for _n, _s, a, b in _pairs if a and b] == ["回退", "过期"],
           "…且三份清单里两份必须被拒（不是恒真：第三份是放行的阳性对照）",
           str([(n, s) for n, s, a, b in _pairs]))
        ok(_pairs[2][1] == "newer" and os.path.exists(os.path.join(target, "agent", "c.py")),
           "…阳性对照：正常新清单 **state=newer 且 run_once 真装**（闸门没把正常路堵死）",
           "%s / c.py=%s" % (_pairs[2][1], os.path.exists(os.path.join(target, "agent", "c.py"))))

        # ⭐ 唯一来源：检查侧与安装侧**调的是同一个函数**（不许各写一套 —— 那正是这条 P0 的成因）
        _seen_calls = []

        def _spy_gate(man, mine=None, max_seen=None, now=None):
            _seen_calls.append(str((man or {}).get("base", {}).get("version") or ""))
            return _keep_gate(man, mine=mine, max_seen=max_seen, now=now)

        _uc2.manifest_gates = _spy_gate
        if os.path.exists(_sf):
            os.remove(_sf)
        _state_of(_FINE)
        _reset_tree()
        UA.run_once(manifest=dict(_FINE), zip_path=pkg, target=target)
        _uc2.manifest_gates = _keep_gate
        ok(_seen_calls[:2] == [NEWER_V, NEWER_V],
           "检查侧与安装侧**调的是同一个判定函数**（唯一来源，不是两套实现）", str(_seen_calls[:3]))

        # 反例锚：把这道闸摘掉（＝修之前的样子）⇒ 同一份回退清单**真的会装进去**（判据不是恒真）
        if os.path.exists(_sf):
            os.remove(_sf)
        _reset_tree()
        _before2 = snap(target)
        _uc2.manifest_gates = lambda *a, **k: {"ok": True, "kind": "", "why": "", "block_install": False,
                                              "theirs": "", "mine": "", "expires": "", "maxSeenVersion": ""}
        try:
            _r_old = UA.run_once(manifest=dict(_LOW), zip_path=pkg, target=target)
        finally:
            _uc2.manifest_gates = _keep_gate
        ok(_r_old.get("ok") is True and snap(target) != _before2,
           "反例锚：**摘掉这道闸**（＝修之前）同一份回退清单会真装进去 ⇒ 上面那些断言抓得住回归",
           "ok=%s 树变了=%s" % (_r_old.get("ok"), snap(target) != _before2))
    finally:
        _uc2.manifest_gates = _keep_gate
        _uc2._state_path, _uc2.fetch_any, _uc2._read_installed = _keep_sp, _keep_fa, _keep_ri
        UA._relaunch_after_update = _keep_rel2
        if os.path.exists(_sf):
            os.remove(_sf)

    print("── J. start_async 防重入 ──")
    UA._set(state="running", phase="download")
    r2 = UA.start_async(target=target)
    ok(r2.get("note") == "更新已经在做了", "正在跑的时候再点 ⇒ 不重复起线程", str(r2)[:90])
    UA._set(state="idle", phase="")

    print("── K. 接线（源码级，防以后改回去）──")
    _web = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
    _con = open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
    _uc = open(os.path.join(ROOT, "agent", "update_check.py"), encoding="utf-8").read()
    _post_seg = _srcslice.from_func(_web, "do_POST")
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

# ── 用户转述「控制台上面的更新用不了，卡在 0% 不动，我都是直接去原地址下载覆盖的」 ──
#    根因＝老下载链是 120 秒/源 × 4 个源（最坏 8 分钟界面钉在 0%），而且**换源是静默的**
#    ⇒ 界面只有「0%」一个信息，用户只能判断它死了。这里把三条钉住。
_UA_SRC = open(os.path.join(ROOT, "agent", "update_apply.py"), encoding="utf-8").read()
ok(20.0 <= float(UA.STALL_S) <= 60.0 and "timeout: float = STALL_S" in _UA_SRC,
   "单个源的卡死判据在 20~60 秒之间（不是老值 120 秒；它是「没数据」判据，不是总时长上限）  [%s]"
   % UA.STALL_S)
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

print("\n── `maxSeenVersion` 的上界与**复位出口**（P1：一次异常回包不许把更新链永久砖死）──")
# ⛔ 现场：`maxSeenVersion` 单调、无上界、产品内无复位口 —— 源给一次 `9999.9.9`
#   就把**真清单与所有未来版本**全判成 rollback ⇒ 那台机器再也装不了任何更新。
# ⚠️ 换轨后"越界"的样本也得是**同一套体系**（`Vᛉ`，跨两号），否则会被"跨体系先出局"分支
#   如实跳过 ⇒ 这条闸根本不会响（那是另一条判据在守的事）。
_V11_MAN = {"base": {"version": FAR_V, "url": "https://github.com/x/y.zip"}, "announce": {}}
_g11 = _uc_top.manifest_gates(_V11_MAN, mine=_uc_top.current_version(),
                              max_seen=_uc_top.current_version(), now=time.time())
ok(_g11.get("kind") == "far_ahead" and _g11.get("block_install") is True and _g11.get("ok") is False,
   "越界版本（跨段超前）⇒ 判 `far_ahead` 且**拒装**   [%s]" % _g11.get("kind"))
ok("超前一年以上" in _g11.get("why", "") and "重置更新状态" in _g11.get("why", ""),
   "说出人话（点出「镜像改过回包 / 版本号打错」+ 指路「重置更新状态」）", _g11.get("why", "")[:90])
_MAN_REAL = json.load(io.open(os.path.join(ROOT, "persona-morph-manifest.json"), encoding="utf-8"))
_mine11 = _uc_top.current_version()
ok(str(((_MAN_REAL.get("base") or {}).get("version")) or "") == _mine11,
   "夹具到位：真清单版本 == 本机版本（所以它本身不该被拦）", _mine11)
_tmp11 = tempfile.mkdtemp(prefix="pm-r11-maxseen-") # ⚠️ 上面那个 `tmp` 已在 finally 里删了
_state11 = os.path.join(_tmp11, "poison_state.json")
with io.open(_state11, "w", encoding="utf-8") as _f11:
    json.dump({"maxSeenVersion": FAR_V, "lastStatus": "current", "other": 1}, _f11)
_saved_sp11 = _uc_top._state_path
try:
    _uc_top._state_path = lambda: _state11
    _g11b = _uc_top.manifest_gates(_MAN_REAL, mine=_mine11)
    ok(_g11b.get("kind") == "rollback" and _g11b.get("block_install") is True,
       "投毒后：**真清单**被误判成 rollback（砖死的现场，可复现）", "kind=%s" % _g11b.get("kind"))
    _r11 = _uc_top.reset_seen_version()
    ok(_r11.get("ok") is True and _r11.get("before") == FAR_V and _r11.get("after") == "",
       "出口在位：`reset_seen_version()` 清掉那个键并如实回报前后值", str(_r11)[:120])
    _after11 = json.load(io.open(_state11, encoding="utf-8"))
    ok("maxSeenVersion" not in _after11 and _after11.get("other") == 1
       and _after11.get("lastStatus") == "current",
       "只清**这一个键**（别的读数一个都不动）", str(_after11))
    _g11c = _uc_top.manifest_gates(_MAN_REAL, mine=_mine11)
    ok(_g11c.get("kind") == "" and _g11c.get("block_install") is False,
       "复位之后：真清单**不再被判回滚**（闸门解开，更新链活了）", "kind=%s" % _g11c.get("kind"))
finally:
    _uc_top._state_path = _saved_sp11
# 落盘侧：被拒的清单**不许**入账（否则一次异常回包就把闸顶死）
_src_uc11 = io.open(os.path.join(ROOT, "agent", "update_check.py"), encoding="utf-8").read()
_src_w11 = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
_src_c11 = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
# ⛔ 源码子串断言**抓不住**"那道守卫永不成立"
#   （`state()` 不把 `kind` 抄进 `out` ⇒ `out.get` 恒 None ⇒ `not ...` 恒真）。换成**行为锚**。
_far_r12 = {"base": {"version": FAR_V, "url": "https://github.com/x/y.zip"}, "announce": {}}
_saved_fa12 = _uc_top.fetch_any
_saved_sp12 = _uc_top._state_path
try:
    _uc_top.fetch_any = lambda urls, timeout=12.0, patient=None: (dict(_far_r12), "", "")
    _uc_top._state_path = lambda: _state11
    with io.open(_state11, "w", encoding="utf-8") as _f12:
        json.dump({"maxSeenVersion": OLDER_V}, _f12)
    _st_r12 = _uc_top.state({"url": "https://example.com/m.json"})
    _after_r12 = json.load(io.open(_state11, encoding="utf-8"))
finally:
    _uc_top.fetch_any = _saved_fa12
    _uc_top._state_path = _saved_sp12
ok(_st_r12.get("status") == "error" and _st_r12.get("kind") == "far_ahead"
   and str(_after_r12.get("maxSeenVersion") or "") == OLDER_V,
   "行为锚：`state()` 对超前清单**报 error 且不入账**",
   "%s / after=%s" % (str(_st_r12)[:90], _after_r12.get("maxSeenVersion")))
ok(_sm.has(_src_w11, '"/api/update_reset"') and _sm.has(_src_c11, 'id="updReset"')
   and _sm.has(_src_c11, "'/api/update_reset'"),
   "接线：webui 有 `/api/update_reset`，控制台横幅上有「重置更新状态」按钮且真打这个接口")
ok(_g11.get("kind") == "far_ahead",
   "反例锚：老写法（无条件写 `maxSeenVersion`）遇上越界版本 ⇒ 判据里那条 `far_ahead` 会红")
# ⛔ **入账时机的行为锚** ——
#   `_note_seen` 现在只在"真装成功之后"；老写法（放在"过了下载地址检查"）会让**装失败的清单也入账**。
#   夹具：清单合法 + 本地包是坏的（`zip_path` 指向一个不是 zip 的文件）⇒ 装失败 ⇒ 盘上不许动。
_state13 = os.path.join(_tmp11, "note_seen13.json")
_saved_sp13 = _uc_top._state_path
_tgt13 = os.path.join(_tmp11, "t13")
os.makedirs(_tgt13, exist_ok=True)
_badzip13 = os.path.join(_tmp11, "bad13.zip")
with io.open(_badzip13, "w", encoding="utf-8") as _f13:
    _f13.write("这不是一个 zip")
try:
    _uc_top._state_path = lambda: _state13
    with io.open(_state13, "w", encoding="utf-8") as _f13b:
        json.dump({"maxSeenVersion": OLDER_V}, _f13b)
    os.environ["PM_ALLOW_LOCAL_UPDATE"] = "1"
    try:
        _r13 = UA.run_once(manifest={"base": {"version": NEWER_V, "sha256": "0" * 64,
                                              "url": _badzip13}, "announce": {}},
                           zip_path=_badzip13, target=_tgt13)
    finally:
        os.environ.pop("PM_ALLOW_LOCAL_UPDATE", None)
    _after13 = json.load(io.open(_state13, encoding="utf-8"))
    ok(_r13.get("ok") is False and str(_after13.get("maxSeenVersion") or "") == OLDER_V,
       "行为锚：**装失败/干跑都不许把版本记进「见过的最高版本」**（老写法在过地址检查时就记）",
       "ok=%s after=%s" % (_r13.get("ok"), _after13.get("maxSeenVersion")))
finally:
    _uc_top._state_path = _saved_sp13
# ⛔ 上面那条锚走的是「**显式给包**」那条路
# （`zip_path=_badzip13`），而 真正修掉的是**下载分支**那处入账 —— 一份"地址可信、
#   下载失败"的清单照样会被记进 `maxSeenVersion`。审计实测：把 `_note_seen` 挪回下载分支那处，
#   上面那条锚**全绿**（抓不住）。这条补上：合法清单 + `zip_path=None` + 打桩下载函数必失败（不出网）。
_state14 = os.path.join(_tmp11, "note_seen14.json")
_saved_sp14, _saved_dl14 = _uc_top._state_path, UA.download
_tgt14b = os.path.join(_tmp11, "t14")
os.makedirs(_tgt14b, exist_ok=True)
try:
    _uc_top._state_path = lambda: _state14
    UA.download = lambda *a, **k: (False, "判据打桩：下载必失败（不出网）")
    with io.open(_state14, "w", encoding="utf-8") as _f14:
        json.dump({"maxSeenVersion": OLDER_V}, _f14)
    _r14b = UA.run_once(manifest={"base": {"version": NEWER_V, "sha256": "0" * 64,
                                           "url": "https://github.com/Eifptaur/Eif-MAPT-PersonaMorph/"
                                                  "releases/download/v9/pm.zip"},
                                  "announce": {}}, zip_path=None, target=_tgt14b)
    _after14 = json.load(io.open(_state14, encoding="utf-8"))
    ok(_r14b.get("ok") is False and _r14b.get("phase") == "download"
       and str(_after14.get("maxSeenVersion") or "") == OLDER_V,
       "行为锚：**下载分支**失败也不许入账（老写法在「过了地址检查」时就记 ⇒"
       "备份/镜像不通也白白顶高回滚闸）",
       "ok=%s phase=%s after=%s" % (_r14b.get("ok"), _r14b.get("phase"),
                                    _after14.get("maxSeenVersion")))
finally:
    _uc_top._state_path = _saved_sp14
    UA.download = _saved_dl14
shutil.rmtree(_tmp11, ignore_errors=True)

print("\n==== 自更新判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
