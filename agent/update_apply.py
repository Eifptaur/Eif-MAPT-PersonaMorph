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


#: 下载资产走不通时的镜像前缀（2026-09-16，与「更新源异常」同源的问题）：
#: 国内直连 `github.com/.../releases/download/...` 经常超时 ⇒ 依次套前缀重试
DL_MIRRORS = ("https://ghfast.top/", "https://ghproxy.net/", "https://gh-proxy.com/", "https://gh.llkk.cc/")

#: 单个源的**卡死**判据（秒）——**不是总时长上限**：urllib 的 timeout 是"两次数据之间的间隔"，
#: 只要还有数据就一直下；**45 秒一个字节都没来**就判这个源不行、换下一个。
#: ⚠️ 2026-09-18 由 20 → 45（作者另一台机器实测延迟 ~1900ms）：2 秒 RTT 下，
#:    连接 + TLS 握手 + 首个数据块本身就可能 10 秒以上，20 秒会把"慢但在跑"的源误判成死了。
#: 为什么（2026-09-17 用户转述：「控制台上面的更新用不了，卡在 0% 不动，我都是直接去原地址下载覆盖的」）：
#: 老值是 120 秒 × 4 个源 ⇒ 最坏 8 分钟界面钉在 0%，任何人都会以为它死了。
STALL_S = 45.0


def _dl_once(url: str, dest: str, timeout: float, progress=None):
    """单次下载尝试；返回 `(ok, why)`。失败时清掉半截文件。"""
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
        return False, "%s: %s" % (type(e).__name__, str(e)[:100])


def _mirror_prefixes():
    """下载时套的镜像前缀：**上次能用的那个源所用的镜像排最前**（第一下就尽量走通的那条）。"""
    order = list(DL_MIRRORS)
    try:
        from . import update_check as uc
        used = str((uc._read_state() or {}).get("lastGoodUrl") or "")
        for m in DL_MIRRORS:
            if used.startswith(m):
                if order[0] != m:
                    order.remove(m)
                    order.insert(0, m)
                break
    except Exception:
        pass
    return order


def _src_name(u: str) -> str:
    """给用户看的"这个源是谁"：官方直连 / 镜像域名（界面要能显示它在换源重试）。"""
    s = str(u)
    for m in DL_MIRRORS:
        if m in s:
            return m.rstrip("/").replace("https://", "")
    return "官方直连"


def download(url: str, dest: str, timeout: float = STALL_S, progress=None, on_try=None):
    """下载在线包（流式写盘 + 进度回调）。**支持本地路径**（离线自测用，与 `update_check.fetch` 同口径）。

    2026-09-16：直连失败时**依次套国内镜像前缀重试**（只对 `github.com` 的地址套），全部失败才如实报原因。
    2026-09-17：镜像顺序不再是死的 `DL_MIRRORS`——**上次清单能用的那个镜像排第一**。
    2026-09-17（用户报「更新卡在 0% 不动、只能自己去原地址下载覆盖」）三处改：
      ① `timeout` 改成**卡死判据**（默认 `STALL_S`＝20 秒没数据就换源；老值 120 秒 × 4 源＝最坏 8 分钟不动）；
      ② 每换一个源**先把进度归零**并回调 `on_try(i, n, url)` ⇒ 界面看得见"在换源重试"，不是一个僵住的百分比；
      ③ 全失败时把**官方地址**带回去，用户至少能手上下载覆盖。
    """
    if not url:
        return False, "清单里没给下载地址（base.url 为空）"
    urls = [url]
    if "github.com" in str(url).lower():
        for m in _mirror_prefixes():
            urls.append(m + str(url))
    last = ""
    for i, u in enumerate(urls, 1):
        if progress:                       # 换源要归零，否则界面还挂着上一个源的百分比 ⇒ 看着像卡死
            try:
                progress(0, 0)
            except Exception:
                pass
        if on_try:
            try:
                on_try(i, len(urls), u, "")
            except Exception:
                pass
        ok, why = _dl_once(u, dest, timeout, progress)
        if ok:
            return True, ""
        last = why
        if on_try:
            try:
                on_try(0, len(urls), u, why)          # i=0 ⇒ "这个源不通，要换下一个了"
            except Exception:
                pass
    return False, ("下载失败（含 %d 个源的重试）：%s ⇒ 也可以直接到发布页手动下载覆盖：%s"
                   % (len(urls), last, url))


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


def _is_locked(e, path: str = "") -> bool:
    """这个异常是不是"文件正被别的进程使用"（共享冲突）。只有这一种才允许"跳过并继续"。

    ⛔ 2026-09-20 **二次修（V-R3-1，上一版是"假修"）**：上一版把它收窄成 `winerror in (32,33)`，
    可**真实**共享冲突走的是 CRT `open()`（`shutil.copy2`）——Windows 把它映射成 `EACCES(13)`、
    **winerror 直接丢掉**。实测（真独占句柄 + 真 `share=READ|DELETE`）：
        copy2  ⇒ PermissionError errno=13 winerror=None
        os.replace ⇒ PermissionError errno=13 winerror=5
    ⇒ 32/33 只出现在"判据自己伪造的属性"里，真实场景**恒假** ⇒ 换入失败 ⇒ 整包回滚：
    用户点「立即更新」几乎必然失败（包里必然含正在运行的 `一键启动.exe`）。
    现在按"**错误码 + 文件本身可不可写**"组合判：winerror ∈ (32,33) ⇒ 占用；
    errno==13 或 winerror==5 ⇒ 再看文件是不是**只读属性**（只读＝真故障 ⇒ 回滚，否则＝被持有 ⇒ 跳过）。
    """
    we = getattr(e, "winerror", None)
    err = getattr(e, "errno", None)
    if we in (32, 33):
        return True
    if we == 5 or err == 13:
        try:
            if path and os.path.exists(path) and not (os.stat(path).st_mode & 0o200):   # 0o200 = S_IWRITE
                return False        # 只读文件 ⇒ 真故障，必须回滚（不许降级成"部分成功"）
        except Exception:
            pass
        return True
    return False


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
        # ⛔ 2026-09-20 修 **V2/V3**：以前这里只比版本号 ⇒ ①"同版本号换包"（只修 bug 不改版本，
        #    内容指纹/树哈希不同）会被短路成"已是最新"，控制台一直提示有新包、点更新却什么都不做；
        #    ②上次"有文件被占用没换"留下的待办也一并被吞掉，**被跳过的文件永远不会补换**。
        #    现在：版本相同还要**树哈希相同**、且**没有待补文件**，才算真"已是最新"。
        _same_tree = str(cur.get("sha256") or "") == want_tree
        _pending = list(cur.get("pendingFiles") or [])
        if _same_tree and not _pending:
            return 0, "已是最新（%s），什么都没做" % want_ver, {"status": "current"}
        if _pending:
            print("[update] 上次有 %d 件没换成功（%s…）⇒ 本次接着补换"
                  % (len(_pending), "、".join(_pending[:3])))

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
        # ⛔ 2026-09-20 修 **V-R3-4**：这一步（以及下面的组合校验）原来**没有异常保护**——
        #   ①快照读失败会带出裸异常（用户看到的是 traceback，而不是"哪个文件读不了"）；
        #   ②**组合校验**里 `sha256_file(tp)` 一旦抛（文件被占用/被删/权限），异常会穿出去 ⇒
        #     **报失败但文件已经全换完、既不回滚也不写状态**（最坏的一种"半成功"）。
        #   ⇒ 快照失败：此时一个文件都还没换 ⇒ 干净返回 1；校验抛异常：按"校验失败"处理 ⇒ 回滚。
        try:
            for rel in rels:
                tp = os.path.join(target, rel.replace("/", os.sep))
                if os.path.exists(tp):
                    snaps[rel] = sha256_file(tp)
                    bp = os.path.join(backup, rel.replace("/", os.sep))
                    os.makedirs(os.path.dirname(bp), exist_ok=True)
                    shutil.copy2(tp, bp)
        except Exception as e:                                      # noqa: BLE001
            return 1, "更新前快照失败（一个文件都没动）：%s（%s）" % (
                str(e)[:80] or type(e).__name__, rel), {"phase": "snapshot"}

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
                #    自检 self_update_selftest 的 F 段就是拿这个当反面证据（模拟磁盘错误必须回滚）。
                if _is_locked(e, tp):
                    locked.append("%s（%s）" % (rel, str(e)[:60]))
                    continue
                return rollback("换入失败：%s（%s）" % (rel, str(e)[:80]))

        # ---- ⑤ 组合校验：逐件重算（换入件必须与清单哈希逐一对上）----
        # ⛔ V-R3-4：这一步抛异常（文件被占用/被删/读不了）**必须回滚**，不许"报失败但已经全换完"。
        try:
            for rel in placed:
                tp = os.path.join(target, rel.replace("/", os.sep))
                if sha256_file(tp) != files[rel]:
                    return rollback("组合校验失败：%s 哈希不符" % rel)
        except Exception as e:                                      # noqa: BLE001
            return rollback("组合校验时出错（%s）：%s" % (rel, str(e)[:70] or type(e).__name__))

        extra = {"from": cur.get("version") or "", "files": len(placed)}
        if locked:
            # ⛔ 2026-09-20 修 **V3**：**不许**把 installed.json 的 version 推到位 —— 否则"再点一次更新"
            #    会被上面的短路判据吞成"已是最新、什么都没做"，被跳过的文件永远补不回来（实测过）。
            #    改成：版本留在旧的、把没换成的记进 `pendingFiles`（下次一进来就接着补换）。
            extra["locked"] = locked[:20]
            extra["pendingFiles"] = [x.split("（")[0] for x in locked][:50]
            extra["pendingVersion"] = want_ver
            write_local_state(target, cur.get("version") or "", cur.get("sha256") or "", extra)
            return 0, ("更新只装了一半：%s → %s（换入 %d 件）；有 %d 件正被使用、本次没换：%s。"
                       "**再点一次「立即更新」即可补换**（版本号没被推上去，所以不会被\"已是最新\"跳过）"
                       % (cur.get("version") or "?", want_ver, len(placed), len(locked),
                          "、".join(locked[:4]))), {"locked": locked, "placed": len(placed),
                                                    "status": "partial", "pending": extra["pendingFiles"]}
        write_local_state(target, want_ver, want_tree, extra)
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
        # ⚠️ 2026-09-17 修（用户报：「立刻更新第一次一定拉不到更新源，第二次才能成功」）：
        # 这里过去只试 `manifest_url()` **一个**地址——默认是 `raw.githubusercontent.com`，
        # 国内常年超时；而"检查更新"那条路早就是**并行多源 + 记住上次能用的源**。
        # 两条路不一致 ⇒ 第一下必失败、第二下（换个源/重连）才成。现在与检查共用同一份候选表。
        man, why, used = uc.fetch_any(uc.candidate_urls(), 8.0)
        if man is None:
            return {"ok": False, "rc": 2, "why": why, "phase": "probe"}
        if used:
            try:                                     # 记住这个源：下载镜像的顺序也用它（见 _mirror_prefixes）
                _st = uc._read_state()
                _st["lastGoodUrl"] = used
                uc._write_state(_st)
            except Exception:
                pass
        manifest = man
    base = (manifest or {}).get("base") or {}
    theirs = str(base.get("version") or "")
    mine = uc.current_version()
    _set(state="running", phase="probe", version=theirs, msg="",
         why="本机 %s / 远端 %s" % (mine or "未记录", theirs or "?"))
    if not theirs:
        return {"ok": False, "rc": 2, "why": "清单里没有版本号", "phase": "probe"}
    # ⛔ 2026-09-20 修 **V2**：同版本号换包（"只修 bug 不改版本"这条路，`make_manifest --build` 就是
    #   为它准备的）以前**只比版本号** ⇒ 控制台侧比了内容指纹判 `newer`、这里却回"已是最新"，
    #   于是横幅永远消不掉、点「立即更新」静默什么都不做。现在两边都比：版本 ≤ 我的 **且** 指纹相同
    #   （任一侧没有指纹时按"没有更新"处理，避免老清单误报）。
    _b_mine = ""
    try:
        from .version import read_build_from as _rbf
        _b_mine = str(_rbf() or "")
    except Exception:
        _b_mine = ""
    _b_theirs = str(base.get("build") or "")
    _same_build = (not _b_theirs) or (not _b_mine) or (_b_theirs == _b_mine)
    if uc.vtuple(theirs) and uc.vtuple(mine) and uc.vtuple(theirs) <= uc.vtuple(mine) and _same_build:
        _set(state="done", phase="current", needRestart=False,
             msg="已是最新（%s），不需要更新" % (mine or theirs))
        return {"ok": True, "rc": 0, "phase": "current", "version": theirs, "needRestart": False,
                "msg": "已是最新"}
    if not _same_build:
        print("[update] 版本号相同（%s）但内容指纹不同（本机 %s / 远端 %s）⇒ 按「同版本换包」继续装"
              % (theirs or "?", _b_mine[:12], _b_theirs[:12]))

    if not zip_path:
        durl = str(base.get("url") or "")
        if not durl:
            return {"ok": False, "rc": 2, "why": "清单里没给下载地址（base.url 为空）", "phase": "probe"}
        # ⛔ 2026-09-20 修 **V-R1-2（P0）**：**清单里的下载地址也必须落在官方域**（跨域即拒）——
        #   否则"清单里写哪个 URL 就下哪个 URL"，把"哈希校验"变成"自洽即通过"。
        _u_ok, _u_why = uc._base_url_ok(durl)
        _local_ok = uc.allow_local_update() and os.path.exists(durl)
        if not _u_ok and not _local_ok:
            _set(state="error", phase="probe", why=_u_why)
            return {"ok": False, "rc": 2, "why": "清单给的下载地址不可信：%s" % _u_why, "phase": "probe"}
        zip_path = os.path.join(target, CACHE_REL, "persona-morph-%s.zip" % (theirs or "new"))
        _set(state="running", phase="download", why="正在下载 %s" % theirs, got=0, total=0)
        # 2026-09-17（用户报「卡在 0% 不动」）：把**换源重试**暴露到作业状态里 —— 老实现只在换源时
        #   静默重试，界面于是只有"0%"这一个信息，用户只能判断"它死了"。
        _try_no = [0]

        def _on_try(i, n, u, why=""):
            if i:
                _try_no[0] = i
                _set(phase="download", got=0, total=0,
                     why="源 %d/%d（%s）" % (i, n, _src_name(u)))
            else:
                _set(phase="download",
                     why="源 %d/%d 不通（%s），换下一个源…" % (_try_no[0], n, why))

        ok_dl, why_dl = download(durl, zip_path,
                                 progress=lambda g, t: _set(got=int(g), total=int(t)),
                                 on_try=_on_try)
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
    # ⛔ 2026-09-20 修 **V4**：`apply_full(dry=True)` 返回的 detail 是 `{"dry": True}`（**没有** status
    #   键），而下面原来只判 `detail.get("status") != "current"` ⇒ `None != "current"` 成立 ⇒
    #   **干跑也被当成"真装成功"** ⇒ 拉起新看门狗 + `os._exit(0)` **把正在跑的机器人杀掉**
    #   （而模块 docstring 给的示例用法正是 `run_once(dry=True)`）。干跑必须"只说不做"。
    _real = bool(rc == 0) and (not dry) and (not detail.get("dry")) \
        and (detail.get("status") != "current")
    if rc == 0:
        _set(state="done", phase="done", msg=msg, needRestart=_real, why="")
    else:
        _set(state="error", phase="apply", why=msg)
    if _real:
        _relaunch_after_update(theirs)
    return {"ok": rc == 0, "rc": rc, "msg": msg, "detail": detail, "version": theirs,
            "needRestart": _real, "phase": "done" if rc == 0 else "apply"}


def _relaunch_after_update(version: str = "") -> None:
    """更新成功后**由我们自己做交接**：拉起"新代码的看门狗"（`--takeover --delay=3`）再立刻退出。

    ⛔ 为什么不能让"更新装完 + 等用户点重启"（2026-09-18 作者实测：「点完更新之后…变『无法访问』、
    窗口也不关掉；我手动叉掉、点了一键关闭、再点一键启动，它仍然不起窗口」）：
      · 旧版本的进程内重启链是坏的（`watchdog.pid` 变两行 ⇒ `_kill_watchdog()` 静默失效 ⇒
        新旧看门狗互抢实例锁）⇒ **更新装上了、却没人接替** ⇒ 用户被迫自己下新包；
      · 而"已经装上坏代码"的机器，**没法靠进程内代码自救** —— 唯一可靠的是**用磁盘上的新代码**接手。
    ⇒ 所以更新这条链**不依赖旧的进程内重启**：直接 spawn 新看门狗（新文件、带 `--takeover`，
      它自己收旧看门狗 + 清实例证据 + 开机器人），然后本进程 `os._exit(0)`。
    """
    try:
        import json as _json
        import time as _time
        flag = os.path.join(ROOT, "data", "update_done.flag")
        os.makedirs(os.path.dirname(flag), exist_ok=True)
        with open(flag, "w", encoding="utf-8") as f:
            f.write(_json.dumps({"version": version, "at": _time.time()}, ensure_ascii=False))
    except Exception:
        pass
    try:
        exe = sys.executable
        if exe.lower().endswith("python.exe"):
            pyw = exe[:-10] + "pythonw.exe"
            if os.path.exists(pyw):
                exe = pyw
        flags = 0
        if os.name == "nt":
            flags = 0x00000008 | 0x00000200 | 0x08000000
        subprocess.Popen([exe, os.path.join(ROOT, "scripts", "watchdog.py"), "--takeover", "--delay=3"],
                         cwd=ROOT, creationflags=flags, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    try:
        import time as _t2
        _t2.sleep(0.4)                        # 让 spawn 落地（Popen 已返回，这里只是给文件系统一点时间）
    except Exception:
        pass
    os._exit(0)                               # 更新＝换新代码跑，本进程必须让位


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
