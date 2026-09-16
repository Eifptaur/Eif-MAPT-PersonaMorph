# -*- coding: utf-8 -*-
"""群相 · **整包自更新**（在线包路径）—— 下载 → 校验 → 换入 → 组合校验 → 失败回滚。

为什么单独一个模块（2026-09-16，用户当场问「做出来居然不给用户用，你是什么意思」）：
  `scripts/pm_update.py` 是**增量 patch** 引擎（契约见 `docs/设计-本体与DLC.md` §四），
  但我们对外发布的只有**整包** `persona-morph-vX.Y.Z.zip` + 清单（**没有** patch.json）
  ⇒ 用户今天要更新，只能自己去 GitHub 下载整包、手动解压覆盖。
  控制台那个「立即更新」按钮当时只打印一句指路文案（而且指向的启动器按钮根本不存在）
  ⇒ 等于"引擎做好了、却没接到用户手上"。这里补上这一跳。

三条硬规矩：
  ① **校验不过就一个文件都不碰**：先把下载来的包**只读**算一遍文件树组合哈希，与清单
     `base.sha256` 对齐之后，才允许动盘（算法与 `make_manifest.py` 完全同一份）。
  ② **绝不碰运行时/用户文件**：`data/`、`config.json`、`logs/`、`wechatauto_logs/`、`报告/`
     一律不覆盖、不删除（在线包里本来也没有它们，这里是第二道闸）。
  ③ 被占用的文件（正在运行的 `一键启动.exe` / 已加载的 dll）**跳过并如实报告**，
     不许假装成功——本次装的是"本体代码 + 其余文件"，那几件下次退出后重跑或重装即可。

用法（控制台「立即更新」按钮走 `start_async()`；命令行/自测走 `run_once()`）：
  py -3 -c "import sys;sys.path.insert(0,'.');from agent import update_apply as U;print(U.run_once(dry=True))"
"""
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 本体更新**一律不碰**的东西（在线包里也没有它们，这里是第二道闸）
NEVER_TOUCH = ("data/", "config.json", "logs/", "wechatauto_logs/", "报告/", "_scratch/", "offline/")
# 编译缓存不换（换它没意义，还可能被占用）
SKIP_REL = ("__pycache__/",)
UA = "persona-morph-update/1"

# 下载缓存放这儿（属于 data/ ⇒ 永远不进更新、也不进包）
CACHE_REL = os.path.join("data", "update")
STATE_REL = os.path.join("data", "installed.json")


def _touched(rel: str) -> bool:
    """这个包内相对路径允不允许写进用户目录。"""
    rel = str(rel or "").replace("\\", "/")
    if not rel or rel.endswith("/"):
        return False
    if any(rel == n.rstrip("/") or rel.startswith(n) for n in NEVER_TOUCH):
        return False
    if any(rel.startswith(s) or ("/" + s) in rel for s in SKIP_REL):
        return False
    return True


def sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def zip_tree(zip_path: str):
    """**只读**解析包：返回 (文件树组合哈希, {rel: sha256}, 顶层目录名, 条数)。

    算法与 `make_manifest.py` / `_scratch/verify_release.py` 同一份：
    排序后 (相对路径 + 该文件哈希) 再哈希 ⇒ 与 zip 时间戳无关、稳定可比。
    """
    files = {}
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        if not names:
            raise RuntimeError("包里一个文件都没有")
        tops = sorted(set(n.split("/")[0] for n in names))
        if len(tops) != 1:
            raise RuntimeError("包内顶层目录不唯一：%s" % tops)
        top = tops[0]
        for n in names:
            rel = n[len(top) + 1:].replace("\\", "/")
            if not rel:
                continue
            h = hashlib.sha256()
            with z.open(n) as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            files[rel] = h.hexdigest()
    th = hashlib.sha256()
    for rel in sorted(files):
        th.update(rel.encode("utf-8"))
        th.update(files[rel].encode("ascii"))
    return th.hexdigest(), files, top, len(files)


def download(url: str, dest: str, timeout: float = 120.0, progress=None):
    """下载在线包（流式写盘 + 进度回调）。**支持本地路径**（离线自测用，与 `update_check.fetch` 同口径）。"""
    if not url:
        return False, "清单里没给下载地址（base.url 为空）"
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if not str(url).lower().startswith(("http://", "https://")):
            shutil.copy2(url, dest)
            return True, ""
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r, open(dest + ".part", "wb") as fh:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
                got += len(chunk)
                if progress:
                    try:
                        progress(got, total)
                    except Exception:
                        pass
        os.replace(dest + ".part", dest)      # 半截文件不许冒充成品
        return True, ""
    except Exception as e:
        try:
            if os.path.exists(dest + ".part"):
                os.remove(dest + ".part")
        except Exception:
            pass
        return False, "下载失败：%s" % (str(e)[:100] or type(e).__name__)


def read_local_state(target: str) -> dict:
    p = os.path.join(target, STATE_REL)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def write_local_state(target: str, version: str, tree: str, extra=None) -> dict:
    p = os.path.join(target, STATE_REL)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    d = {"version": str(version), "sha256": str(tree), "appliedAt": int(time.time())}
    if extra:
        d.update(extra)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, p)
    return d


def _is_locked(e) -> bool:
    """这个异常是不是"文件正被别的进程使用"（Windows 共享冲突）。

    只有这一种才允许"跳过并继续"——磁盘满、权限不对、路径错都必须回滚。
    """
    if isinstance(e, PermissionError):
        return True
    return getattr(e, "winerror", None) in (32, 33)


def apply_full(manifest: dict, zip_path: str, target: str = ROOT, dry: bool = False, progress=None):
    """整包换入。返回 `(rc, msg, detail)`：rc 0=成功（可能带"跳过被占用的文件"）、1=失败已回滚、2=前置不成立。

    顺序（少一步都不安全）：
      读清单 → 版本已是最新就停 → **只读**算包的文件树哈希并与清单对齐 → 干跑就到此为止
      → 逐件快照 → 换入（跳过被占用件）→ 逐件组合校验 → 失败回滚 → 写 `data/installed.json`
    """
    base = (manifest or {}).get("base") or {}
    want_ver = str(base.get("version") or "")
    want_tree = str(base.get("sha256") or "")
    if not want_ver or not want_tree:
        return 2, "清单缺 base.version / base.sha256（更新源不完整）", {}
    if not zip_path or not os.path.exists(zip_path):
        return 2, "下载到的包不存在", {}

    cur = read_local_state(target)
    if str(cur.get("version") or "") == want_ver:
        return 0, "已是最新（%s），什么都没做" % want_ver, {"status": "current"}

    # ---- ① 只读校验：包内文件树哈希必须等于清单声称的那一个 ----
    try:
        got_tree, files, top, n_files = zip_tree(zip_path)
    except Exception as e:
        return 1, "包读不了（可能没下完）：%s" % str(e)[:80], {}
    if got_tree != want_tree:
        return 1, ("包内容与清单对不上（文件树哈希 %s… ≠ 清单 %s…）⇒ 一个文件都没动"
                   % (got_tree[:12], want_tree[:12])), {"got": got_tree, "want": want_tree, "files": n_files}

    rels = [r for r in sorted(files) if _touched(r)]
    skipped_scope = [r for r in sorted(files) if not _touched(r)]
    if not rels:
        return 2, "包里没有可换入的本体文件（顶层=%s）" % top, {}

    if dry:
        return 0, "【干跑】包内 %d 件（本体 %d / 不碰 %d），版本 %s → %s" % (
            n_files, len(rels), len(skipped_scope), cur.get("version") or "?", want_ver), {"dry": True}

    if progress:
        try:
            progress(0, 0)
        except Exception:
            pass

    stage = tempfile.mkdtemp(prefix="pm-full-stage-")
    backup = tempfile.mkdtemp(prefix="pm-full-backup-")
    placed, locked, snaps = [], [], {}
    try:
        # ---- ② 解压到暂存（不直接写用户目录）----
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(stage)
        src_root = os.path.join(stage, top)

        # ---- ③ 快照"将被覆盖或新增"的每一件 ----
        for rel in rels:
            tp = os.path.join(target, rel.replace("/", os.sep))
            if os.path.exists(tp):
                snaps[rel] = sha256_file(tp)
                bp = os.path.join(backup, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(bp), exist_ok=True)
                shutil.copy2(tp, bp)

        def rollback(why):
            for rel in placed:
                tp = os.path.join(target, rel.replace("/", os.sep))
                if rel in snaps:
                    bp = os.path.join(backup, rel.replace("/", os.sep))
                    try:
                        os.makedirs(os.path.dirname(tp), exist_ok=True)
                        shutil.copy2(bp, tp)
                    except Exception:
                        pass
                else:                      # 原来是新增的 ⇒ 撤掉
                    try:
                        os.remove(tp)
                    except Exception:
                        pass
            return 1, why + "（已回滚 %d 件）" % len(placed), {"rolledBack": list(placed)}

        # ---- ④ 换入（**只有"被占用"才跳过**；其它错误一律回滚，不许降级成"部分成功"）----
        for rel in rels:
            sp = os.path.join(src_root, rel.replace("/", os.sep))
            tp = os.path.join(target, rel.replace("/", os.sep))
            try:
                os.makedirs(os.path.dirname(tp), exist_ok=True)
                shutil.copy2(sp, tp)
                placed.append(rel)
            except Exception as e:
                # ⚠️ 2026-09-16：第一版把**任何** OSError 都当"文件被占用"跳过 ⇒ 磁盘满/权限不对
                #    这类真故障会被降级成"部分成功"，用户以为更新好了、其实没换。
                #    判据 self_update_selftest 的 F 段就是拿这个当反面证据（模拟磁盘错误必须回滚）。
                if _is_locked(e):
                    locked.append("%s（%s）" % (rel, str(e)[:60]))
                    continue
                return rollback("换入失败：%s（%s）" % (rel, str(e)[:80]))

        # ---- ⑤ 组合校验：逐件重算（换入件必须与清单哈希逐一对上）----
        for rel in placed:
            tp = os.path.join(target, rel.replace("/", os.sep))
            if sha256_file(tp) != files[rel]:
                return rollback("组合校验失败：%s 哈希不符" % rel)

        extra = {"from": cur.get("version") or "", "files": len(placed)}
        if locked:
            extra["locked"] = locked[:20]
        write_local_state(target, want_ver, want_tree, extra)
        if locked:
            return 0, ("更新已装好：%s → %s（换入 %d 件）；有 %d 件正被使用、本次没换：%s。"
                       "它们在重启后重跑一次更新即可换掉（不影响本体代码生效）"
                       % (cur.get("version") or "?", want_ver, len(placed), len(locked),
                          "、".join(locked[:4]))), {"locked": locked, "placed": len(placed)}
        return 0, ("更新成功：%s → %s（换入 %d 件，组合校验通过）"
                   % (cur.get("version") or "?", want_ver, len(placed))), {"placed": len(placed)}
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)


# ── 给控制台用的"一次更新"作业（进度可轮询）──────────────────────────────
JOB = {"state": "idle", "phase": "", "why": "", "msg": "", "got": 0, "total": 0,
       "version": "", "needRestart": False, "at": 0}
_LOCK = threading.Lock()


def _set(**kw):
    with _LOCK:
        JOB.update(kw)
        JOB["at"] = int(time.time())


def job() -> dict:
    with _LOCK:
        return dict(JOB)


def run_once(manifest=None, zip_path=None, target=ROOT, dry=False, progress=None):
    """一条龙：拉清单 → 下载（可给现成包）→ 整包换入。返回 dict（CLI/自测/控制台共用）。"""
    from . import update_check as uc          # 延迟导入：控制台只要读状态时不拉起这条链
    if progress is None:
        progress = lambda got, total: _set(got=int(got), total=int(total))   # noqa: E731
    if manifest is None:
        _set(state="running", phase="probe", why="", msg="", got=0, total=0)
        url = uc.manifest_url()
        man, why = uc.fetch(url, 8.0)
        if man is None:
            return {"ok": False, "rc": 2, "why": why, "phase": "probe"}
        manifest = man
    base = (manifest or {}).get("base") or {}
    theirs = str(base.get("version") or "")
    mine = uc.current_version()
    _set(state="running", phase="probe", version=theirs, msg="",
         why="本机 %s / 远端 %s" % (mine or "未记录", theirs or "?"))
    if not theirs:
        return {"ok": False, "rc": 2, "why": "清单里没有版本号", "phase": "probe"}
    if uc.vtuple(theirs) and uc.vtuple(mine) and uc.vtuple(theirs) <= uc.vtuple(mine):
        _set(state="done", phase="current", needRestart=False,
             msg="已是最新（%s），不需要更新" % (mine or theirs))
        return {"ok": True, "rc": 0, "phase": "current", "version": theirs, "needRestart": False,
                "msg": "已是最新"}

    if not zip_path:
        durl = str(base.get("url") or "")
        if not durl:
            return {"ok": False, "rc": 2, "why": "清单里没给下载地址（base.url 为空）", "phase": "probe"}
        zip_path = os.path.join(target, CACHE_REL, "persona-morph-%s.zip" % (theirs or "new"))
        _set(state="running", phase="download", why="正在下载 %s" % theirs, got=0, total=0)
        ok_dl, why_dl = download(durl, zip_path, progress=lambda g, t: _set(got=int(g), total=int(t)))
        if not ok_dl:
            _set(state="error", phase="download", why=why_dl)
            return {"ok": False, "rc": 1, "why": why_dl, "phase": "download"}

    _set(state="running", phase="verify", why="正在校验包内容")
    rc, msg, detail = apply_full(manifest, zip_path, target, dry=dry, progress=progress)
    # 装完就把下载缓存删掉（用户红线：**凡往磁盘写东西的功能都要有清理措施**）——
    # 暂存/备份目录在 apply_full 的 finally 里已经清了，这里只剩这个 zip（每版一个、4.5MB 量级）。
    # 失败时**留着**，方便重试与排障；下次成功后再清。
    if rc == 0 and zip_path and os.path.dirname(os.path.abspath(zip_path)) == \
            os.path.abspath(os.path.join(target, CACHE_REL)):
        try:
            os.remove(zip_path)
        except Exception:
            pass
    if rc == 0:
        _set(state="done", phase="done", msg=msg, needRestart=(detail.get("status") != "current"),
             why="")
    else:
        _set(state="error", phase="apply", why=msg)
    return {"ok": rc == 0, "rc": rc, "msg": msg, "detail": detail, "version": theirs,
            "needRestart": rc == 0 and detail.get("status") != "current", "phase": "done" if rc == 0 else "apply"}


def start_async(target=ROOT):
    """给控制台按钮用：**防重入**地起一条后台更新（立刻返回当前作业状态）。"""
    with _LOCK:
        if JOB.get("state") == "running":
            return {"ok": True, "state": "running", "note": "更新已经在做了", "job": dict(JOB)}
        JOB.update({"state": "running", "phase": "probe", "why": "", "msg": "", "got": 0,
                    "total": 0, "needRestart": False, "version": "", "at": int(time.time())})

    def _worker():
        try:
            r = run_once(target=target)
            if not r.get("ok"):
                _set(state="error", why=r.get("why") or r.get("msg") or "更新失败",
                     phase=r.get("phase") or "apply")
        except Exception as e:
            _set(state="error", phase="apply", why="更新异常：%s" % str(e)[:120])

    t = threading.Thread(target=_worker, name="pm-self-update", daemon=True)
    t.start()
    return {"ok": True, "state": "running", "note": "已开始更新", "job": job()}
