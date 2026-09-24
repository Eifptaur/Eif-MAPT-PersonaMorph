# -*- coding: utf-8 -*-
"""PySide6 自举 —— import PySide6 之前的唯一通路。

=====================================================================
为什么必须有这个模块
=====================================================================
⇒ 老用户一键更新后，机器上
很可能还没有界面组件。而 launcher.exe 存在「旧版仍在跑」的窗口期（占用 →
半装 → 重启补换），旧 launcher 不会帮装 PySide6 ⇒ 自动装逻辑必须做在
**Python 侧入口**（本模块）；launcher 侧检测只是提前介入优化体验，
不是唯一通路。过渡期网页控制台并存，Qt 壳自举失败也不至于两眼一抹黑。

  1. 只装 Essentials      —— PySide6-Essentials==6.11.2（界面只用 Core/Gui/
                             Widgets/Svg；完整版 633MB 里 Addons 234MB 用不上）；
  2. 钉版本 ==6.11.2      —— 与原型一致；已装 6.11.2 直接跳过（幂等），
                             版本不符才重装，绝不悄悄换版本；
  3. 国内镜像 + 并行探测竞速  —— 借鉴 agent/update_check.py::fetch_any 的成熟机制
                             ：pip 一次只能指一个源 ⇒
                             先并发 GET 各镜像 /simple/<包>/ 页（单源 ~2s 超时），选**最快通的**
                             再 pip install；全败放慢（5s）整轮重试一次；403 视为该源失败
                             立即换下一个，不当终态（清华对 cp310 wheel 返回 403 的教训，
                             阿里成功）；全挂时逐源列「哪条、什么错」人话诊断；
                             仍是全国内源，不碰裸 pypi；
  4. 失败讲人话           —— 返回人话原因，绝不裸 traceback、绝不弹系统窗；
                             调用方（persona_morph.py）拿去记日志，过渡期
                             继续用网页控制台。

装到哪：**当前进程的解释器**（sys.executable）。生产上 persona_morph 由
runtime\\python\\python.exe 启动 ⇒ sys.executable 就是它 ⇒ PySide6 落进
runtime（update 链 NEVER_TOUCH 含 "runtime/"，不会被版本更新覆盖）；
开发机上系统 Python 跑则装系统 Python —— 安装与 import 永远同一解释器，
不存在「装到 A、B 来用」的错位。

自检：python qt_bootstrap.py   （幂等自检：已装/版本不符/缺装三分支真跑）
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ── 钉子 2：版本钉死，与 _scratch/qt_proto 原型一致 ──
PYSIDE_PIN = "6.11.2"
PYSIDE_PKG = "PySide6-Essentials"

# ── 钉子 3：国内镜像（默认清华，失败按序轮换；全是国内源，不碰裸 pypi）──
MIRRORS = (
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple/",
    "https://mirrors.cloud.tencent.com/pypi/simple/",
)

# pythonw 下起子进程不闪黑窗（与 agent_bridge.console_process_alive 同款）
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 单镜像超时：约 100MB 下载，慢机器也要给足；最坏 = 探测选中的那 1 个 × 900s
_PIP_TIMEOUT = 900

# 探测（借鉴 update_check 两阶段等待）：普通窗口 2s ⇒ 全败放慢 5s 再整轮试一次
_PROBE_TIMEOUT = 2.0
_PROBE_PATIENT = 5.0

# ── F：分块并发下载（把单连接 1MB/s 拉到近带宽上限）──
_CHUNK_WORKERS = 6
# 单块的探测/下载超时：块不大（~几 MB），给 60s 足够；卡住就整体回退单连接
_CHUNK_TIMEOUT = 60.0
# 测速：拿 wheel 头几 MB 比吞吐（比"通不通"更能反映真实下载速度）
_SPEED_SAMPLE_BYTES = 1 << 20 # 1 MiB 采样
_SPEED_TIMEOUT = 6.0 # 采样单源超时
# 低于这个大小不值得分块（分块起线程的开销会盖过收益）——100MB 的 wheel 远超之
_CHUNK_MIN_BYTES = 4 << 20 # 4 MiB
# 自适应闸门：**本机实测**单连接已达 34~37MB/s 时，分块并发因
# 每段各做一次 TLS 握手反而更慢（实测 21MB/s）⇒ 只有**单连接确实慢**（< 此阈值）才分块。
# 用户现场是 1MB/s，远低于阈值 ⇒ 会走分块；快机器不受影响（少一次并发开销）。
_CHUNK_SLOW_MBPS = 8.0 # 单连接低于此 MB/s 才值得并发分块
# 本地 wheel 下载目录名（装在 %TEMP% 下；断点续传靠它复用已下的 .part）
_FETCH_DIR_NAME = "pm-pyside6-wheel"

_UA = "Mozilla/5.0 (qt-bootstrap)"


def pyside6_installed(pin: str = PYSIDE_PIN) -> bool:
    """当前解释器里 PySide6 在不在、版本对不对（只查不装，幂等快路径）。

    查两层：find_spec（模块可导入）+ importlib.metadata 钉 Essentials 版本。
    只查 metadata 不真 import —— 真 import 会拉起 Qt 插件扫描，放在自举
    阶段太重；selftest 已经钉住「装上后 import 一定过」。
    """
    if importlib.util.find_spec("PySide6") is None:
        return False
    try:
        ver = importlib.metadata.version(PYSIDE_PKG)
    except importlib.metadata.PackageNotFoundError:
        # 有模块没 metadata（手工拷贝等）⇒ 按版本不符处理，重装拿回钉子
        return False
    return ver == pin


def _mirror_tag(mirror: str) -> str:
    """镜像的人话短名（逐源诊断行用）：tuna→清华 / aliyun→阿里 / tencent→腾讯。"""
    if "tuna" in mirror:
        return "清华"
    if "aliyun" in mirror:
        return "阿里"
    if "tencent" in mirror:
        return "腾讯"
    return mirror.split("//")[-1].split("/")[0]


def _probe_url(mirror: str) -> str:
    """探测地址 = 镜像的 PEP 503 simple 包页（证明「这个源真有这个包」，不只是索引活着）。"""
    return mirror.rstrip("/") + "/" + PYSIDE_PKG.lower() + "/"


def _probe_once(mirror: str, timeout: float) -> tuple[bool, float, str]:
    """轻量 GET /simple/<包>/ 页。返回 (通?, 耗时s, 人话原因)。

    403 = 源拒绝 —— 只是「该源没过」的依据，不当终态（钉子 3 教训：清华 403，阿里成）。
    """
    import time as _t
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        _probe_url(mirror),
        headers={"User-Agent": "Mozilla/5.0 (qt-bootstrap-probe)"},
    )
    t0 = _t.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code = getattr(r, "status", 200)
            elapsed = _t.perf_counter() - t0
            if code == 200:
                return True, elapsed, ""
            return False, elapsed, f"HTTP {code}"
    except urllib.error.HTTPError as e:
        return False, _t.perf_counter() - t0, f"HTTP {e.code}（源拒绝）"
    except Exception as e: # noqa: BLE001
        return False, _t.perf_counter() - t0, f"{type(e).__name__}"


def _probe_mirrors(mirrors=MIRRORS, timeout: float = _PROBE_TIMEOUT):
    """并行探测：每源一线程（update_check::fetch_any 同款），全部收齐 ——
    并行 ⇒ 最坏 ≈ 单源超时；串行轮询是老做法的病根（逐个等满最坏 3×超时）。"""
    import threading

    got: dict[str, tuple[bool, float, str]] = {}
    lock = threading.Lock()

    def _one(m: str) -> None:
        res = _probe_once(m, timeout)
        with lock:
            got[m] = res

    ts = [threading.Thread(target=_one, args=(m,), daemon=True) for m in mirrors]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout + 2.0)
    for m in mirrors:
        got.setdefault(m, (False, timeout, "探测未归（超时）"))
    return got


def _pick_mirror(probe: dict) -> list[str]:
    """探得通的按耗时升序排（最快通的最先装）；挂的不进装单（pip 对它必败，不再白等）。"""
    return sorted((m for m, r in probe.items() if r[0]), key=lambda m: probe[m][1])


def _diag_lines(pairs: list[tuple[str, str]]) -> str:
    """逐源诊断行——用户粘出来一眼可排障。"""
    return "；".join(f"{tag}→{(reason or 'ok')[:80]}" for tag, reason in pairs)


# ─────────────────────────── F：下载提速 ───────────────────────────
# 用户实测 1MB/s。下面这套 = ① 测速选最快镜像 ② Range 分块并发下 wheel 到本地
# ③ pip install --no-index 本地装 ④ 进度可见 ⑤ 断点续传；每步失败都**回退**既有
# 单连接 pip 通路，绝不把"没下成"当"下成了"（fail-closed）。

def _fast_download_enabled() -> bool:
    """快通路开关（环境变量 `PM_FAST_DL`，默认开）。`0/false/no/off` ⇒ 关（回到老路）。

    留开关的意义：万一某台机器的镜像对并发分块不友好，用户能用它一键退回老通路。
    """
    v = str(os.environ.get("PM_FAST_DL", "") or "").strip().lower()
    return v not in ("0", "false", "no", "off")


def _make_progress(_log, mirror: str):
    """造一个限频的进度回调（每 ~10% 或每 2s 报一次，**不刷屏**）。返回 None 也给得起。"""
    state = {"last_t": 0.0, "last_p": -1}

    def _progress(done: int, total: int) -> None:
        try:
            pct = int(done * 100 / total) if total else 0
        except Exception: # noqa: BLE001
            return
        now = time.time()
        if pct - state["last_p"] >= 10 or (now - state["last_t"] >= 2.0 and pct != state["last_p"]):
            state["last_p"] = pct
            state["last_t"] = now
            mb = done / (1024.0 * 1024.0)
            _log("info", f"下载进度 {pct}%（{mb:.0f}MB，来自{_mirror_tag(mirror)}）")

    return _progress


def _wheel_url(mirror: str, timeout: float = _SPEED_TIMEOUT) -> tuple[str, str]:
    """从镜像的 PEP 503 `/simple/<包>/` 页里抠出**与钉版本匹配的 wheel 绝对 URL**。

    返回 `(url, 原因)`；拿不到 url 时 `url == ""`，原因是人话（供回退诊断）。
    只认 win_amd64 cp310/cp3x wheel —— 与钉子 2（钉版本）一致。
    """
    url = _probe_url(mirror) # <mirror>/pyside6-essentials/
    # 包名里可能出现 - 或 _（PEP 503 归一化）⇒ 用 [_-] 类；**先 escape 再拼类**
    m = re.escape(PYSIDE_PKG.split("-")[0].lower()) + r"[-_]" + \
        re.escape(PYSIDE_PKG.split("-", 1)[1].lower() if "-" in PYSIDE_PKG else "")
    pat = re.compile(r'href\s*=\s*["\']([^"\']+\.whl)(?:#[^"\']*)?["\']', re.I)
    tag_ver = re.escape(PYSIDE_PIN)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception as e: # noqa: BLE001
        return "", f"{type(e).__name__}"
    cands = []
    for href in pat.findall(html):
        low = href.lower()
        if not re.search(r"%s[_-]%s" % (m, tag_ver), low):
            continue
        if "win_amd64" not in low:
            continue
        cands.append(href)
    if not cands:
        return "", "该源没给出匹配的 win_amd64 wheel"
    # 相对路径补全成绝对（有的镜像 simple 页给的是相对 href）
    from urllib.parse import urljoin
    return urljoin(url, cands[0]), ""


def _speed_of_mirror(mirror: str, timeout: float = _SPEED_TIMEOUT) -> tuple[bool, float, str, str]:
    """测一个镜像的**真实下载吞吐**：对 wheel 做 ranged GET 取 _SPEED_SAMPLE_BYTES。

    返回 `(通?, 每秒字节, wheel_url, 原因)`。比 `/simple/` 页探活更贴近"正式下会多快"。
    """
    wu, why = _wheel_url(mirror, timeout)
    if not wu:
        return False, 0.0, "", why
    req = urllib.request.Request(
        wu, headers={"User-Agent": _UA, "Range": "bytes=0-%d" % (_SPEED_SAMPLE_BYTES - 1)})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(_SPEED_SAMPLE_BYTES)
    except Exception as e: # noqa: BLE001
        return False, 0.0, wu, f"{type(e).__name__}"
    el = max(time.perf_counter() - t0, 1e-3)
    n = len(data or b"")
    if n <= 0:
        return False, 0.0, wu, "采样为空"
    return True, n / el, wu, ""


def _speed_mirrors(mirrors=MIRRORS, timeout: float = _SPEED_TIMEOUT) -> dict:
    """**并行**测各镜像吞吐（同 _probe_mirrors 的线程模型）。返回 {mirror: (通, bps, url, why)}。"""
    import threading

    got: dict[str, tuple[bool, float, str, str]] = {}
    lock = threading.Lock()

    def _one(m: str) -> None:
        res = _speed_of_mirror(m, timeout)
        with lock:
            got[m] = res

    ts = [threading.Thread(target=_one, args=(m,), daemon=True) for m in mirrors]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout + 4.0)
    for m in mirrors:
        got.setdefault(m, (False, 0.0, "", "测速未归（超时）"))
    return got


def _pick_fastest(speed: dict, fallback: list[str]) -> list[str]:
    """按测得的吞吐**降序**排可用镜像（最快的先试）；测不到吞吐的沿用探活顺序垫后。

    `fallback` = 探活挑出的装单（_pick_mirror 结果）。这样即使测速全挂，也不至于
    把原本探得通的源丢掉 —— 只是回到"按探活耗时"的老顺序。
    """
    usable = [m for m, r in speed.items() if r[0] and r[1] > 0]
    usable.sort(key=lambda m: speed[m][1], reverse=True)
    tail = [m for m in fallback if m not in usable]
    return usable + tail


def _http_get_range(url: str, start: int, end: int, timeout: float = _CHUNK_TIMEOUT,
                    retries: int = 2) -> bytes:
    """取 [start, end] 闭区间字节。**校验必须拿到整段**，否则抛异常（交给整体回退）。"""
    want = end - start + 1
    last = None
    for _ in range(max(1, retries + 1)):
        req = urllib.request.Request(
            url, headers={"User-Agent": _UA, "Range": "bytes=%d-%d" % (start, end)})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if data is not None and len(data) == want:
                return data
            last = "分块长度不符（要 %d 得 %d）" % (want, len(data or b""))
        except Exception as e: # noqa: BLE001
            last = "%s" % type(e).__name__
    raise RuntimeError(last or "分块下载失败")


def _should_chunk(bps: float) -> bool:
    """单连接实测吞吐是否**慢到值得并发分块**。拿不到吞吐（<=0）时保守返回 False（走单连接）。"""
    try:
        mbps = float(bps) / (1024.0 * 1024.0)
    except (TypeError, ValueError):
        return False
    return mbps > 0 and mbps < _CHUNK_SLOW_MBPS


def _wheel_total_size(url: str, timeout: float = _SPEED_TIMEOUT) -> tuple[int, bool]:
    """HEAD 探 wheel 总大小与**是否支持 Range**。返回 `(总字节, 支持Range?)`。

    支持 Range 的判据（两个都要）：`Accept-Ranges: bytes` 或对 ranged GET 回 206；
    总大小来自 `Content-Length`（HEAD 拿不到就 ranged GET 的 `Content-Range`）。
    拿不到总大小时返回 `(0, False)` —— 上层据此回退单连接。
    """
    total, accept = 0, False
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            cl = r.headers.get("Content-Length")
            total = int(cl) if (cl or "").isdigit() else 0
            ar = str(r.headers.get("Accept-Ranges") or "").lower()
            accept = "bytes" in ar
    except Exception: # noqa: BLE001
        pass
    if total <= 0:
        # HEAD 不通/无 CL ⇒ 用一次 ranged GET 的 Content-Range 兜底探总长与 206 支持
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": _UA, "Range": "bytes=0-0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                code = getattr(r, "status", 200)
                cr = str(r.headers.get("Content-Range") or "")
                m = re.search(r"/(\d+)\s*$", cr)
                if m:
                    total = int(m.group(1))
                if code == 206 or "bytes" in str(r.headers.get("Accept-Ranges") or "").lower():
                    accept = True
        except Exception: # noqa: BLE001
            pass
    if total <= 0:
        return 0, False
    return total, accept


def _fetch_wheel_chunked(url: str, dest: str, total: int, workers: int = _CHUNK_WORKERS,
                         progress=None) -> tuple[bool, str]:
    """Range 分块并发下 wheel 到 `dest`（断点续传：dest 已存在则读其长度续下）。

    返回 `(成功?, 原因)`。**失败即返回 False**，由上层回退单连接 pip —— 这里不吞错、
    不写半截文件冒充成品（unlink 半截文件后返回 False）。
    """
    import threading

    part = dest + ".part"
    have = 0
    if os.path.exists(part):
        have = os.path.getsize(part) # 断点：上次下到哪
        if have >= total:
            have = 0 # 已有 part 至少和 total 一样大 ⇒ 不可信，重下
            try:
                os.remove(part)
            except OSError:
                pass
    if have == 0 and os.path.exists(part):
        try:
            os.remove(part)
        except OSError:
            pass

    # 断点续传要能**按偏移改写**已下的 .part ⇒ 必须 r+b（"ab" 会忽略 seek、永远追加到末尾，
    if have and os.path.exists(part):
        fh = open(part, "r+b")
    else:
        fh = open(part, "wb")
    lock = threading.Lock()
    done = {"n": have}
    errs: list = []

    def _seg(s: int, e: int) -> None:
        try:
            data = _http_get_range(url, s, e)
        except Exception as ex: # noqa: BLE001
            with lock:
                errs.append(str(ex))
            return
        with lock:
            fh.seek(s)
            fh.write(data)
            done["n"] += len(data)
            if progress is not None:
                try:
                    progress(done["n"], total)
                except Exception: # noqa: BLE001
                    pass

    try:
        # 分块：从 have 之后开始切（已下的段不再重下 ⇒ 断点续传）
        span = total - have
        n = max(1, min(int(workers or 1), (span + (1 << 20) - 1) // (1 << 20) or 1))
        step = (span + n - 1) // n
        segs = []
        s = have
        while s < total:
            e = min(s + step - 1, total - 1)
            segs.append((s, e))
            s = e + 1
        ts = [threading.Thread(target=_seg, args=(a, b), daemon=True) for a, b in segs]
        for t in ts:
            t.start()
        for t in ts:
            t.join(_CHUNK_TIMEOUT + 10.0)
    finally:
        try:
            fh.close()
        except Exception: # noqa: BLE001
            pass

    if errs:
        # 半截 part 留着重试（断点续传）；本次如实返回失败
        return False, "分块下载失败：" + errs[0][:80]
    try:
        size = os.path.getsize(part)
    except OSError:
        size = -1
    if size != total:
        return False, "分块下载不完整（%d/%d）" % (size, total)
    try:
        if os.path.exists(dest):
            os.remove(dest)
        os.replace(part, dest)
    except OSError as e:
        return False, "落盘失败：%s" % type(e).__name__
    return True, ""


def _fetch_wheel(mirror: str, cache_dir: str, progress=None, bps: float = 0.0) -> tuple[str, str, str]:
    """把钉版本的 wheel 下到 cache_dir。返回 `(本地whl路径, 说明, 下载方式)`。

    下载方式 ∈ {"chunked", "single"}；失败时路径为 ""。
    **自适应**：先探总长 + Range 支持；只有当单连接实测吞吐
    `bps` 慢到 `_should_chunk()` 判定"值得并发"时，才走 Range 分块并发；
    否则走单连接（快机器上并发反而更慢）。分块失败一律回退单连接。
    """
    wu, why = _wheel_url(mirror)
    if not wu:
        return "", why, ""
    total, accept = _wheel_total_size(wu)
    if total <= 0:
        return "", "拿不到 wheel 大小", ""
    fname = wu.rsplit("/", 1)[-1].split("?")[0] or (PYSIDE_PKG + ".whl")
    dest = os.path.join(cache_dir, fname)
    if os.path.exists(dest) and os.path.getsize(dest) == total:
        return dest, "", "chunked" # 已有完整本地文件 ⇒ 命中断点
    os.makedirs(cache_dir, exist_ok=True)
    why_chunk = ""
    if accept and total >= _CHUNK_MIN_BYTES and _should_chunk(bps):
        ok, why = _fetch_wheel_chunked(wu, dest, total, _CHUNK_WORKERS, progress)
        if ok:
            return dest, "", "chunked"
        # 分块失败 ⇒ 不急着放弃：回退单连接（镜像可能限并发）
        why_chunk = why
    ok, why = _fetch_wheel_single(wu, dest, total, progress)
    if ok:
        return dest, "", "single"
    return "", (why_chunk + "；" if why_chunk else "") + why, ""


def _fetch_wheel_single(url: str, dest: str, total: int, progress=None) -> tuple[bool, str]:
    """单连接流式下 wheel（**不经过 pip**，但只有一条连接）。支持断点续传（Range from part size）。"""
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if have >= total:
        have = 0
    headers = {"User-Agent": _UA}
    if have > 0:
        headers["Range"] = "bytes=%d-" % have
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_CHUNK_TIMEOUT) as r:
            code = getattr(r, "status", 200)
            if have > 0 and code != 206:
                have = 0 # 源不吃续传 ⇒ 从头来（截断已下的）
            mode = "ab" if have else "wb"
            n = have
            with open(part, mode) as fh:
                while True:
                    buf = r.read(1 << 16)
                    if not buf:
                        break
                    fh.write(buf)
                    n += len(buf)
                    if progress is not None:
                        try:
                            progress(n, total)
                        except Exception: # noqa: BLE001
                            pass
    except Exception as e: # noqa: BLE001
        return False, "单连接下载失败：%s" % type(e).__name__
    try:
        size = os.path.getsize(part)
    except OSError:
        size = -1
    if size != total:
        return False, "单连接下载不完整（%d/%d）" % (size, total)
    try:
        if os.path.exists(dest):
            os.remove(dest)
        os.replace(part, dest)
    except OSError as e:
        return False, "落盘失败：%s" % type(e).__name__
    return True, ""


def _pip_install_local(python_exe: str, wheel_path: str) -> tuple[bool, str]:
    """对已下到本地的 wheel 跑 `pip install --no-index --find-links <dir>`（离线装）。

    为什么本地装：把"下载"与"安装"解耦 —— 下载走我们自己的分块通路（快），
    安装交给 pip（依赖解析/落盘/校验都成熟），不再让 pip 用单连接慢慢拉。
    """
    folder = os.path.dirname(os.path.abspath(wheel_path))
    cmd = [
        python_exe, "-m", "pip", "install",
        "--no-index", "--find-links", folder,
        f"{PYSIDE_PKG}=={PYSIDE_PIN}",
        "--no-warn-script-location",
        "--disable-pip-version-check",
        "--retries", "2", "--timeout", "30",
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=_PIP_TIMEOUT, creationflags=_CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return False, f"本地 pip 安装超时（>{_PIP_TIMEOUT}s）"
    except Exception as e: # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    tail = ((r.stdout or "") + (r.stderr or "")).strip()[-400:]
    return r.returncode == 0, tail


def _pip_install(python_exe: str, mirror: str) -> tuple[bool, str]:
    """对一个镜像跑一次 pip（**兜底通路**：分块下载不可用/失败时用它）。返回 (成功?, 输出尾)。"""
    cmd = [
        python_exe, "-m", "pip", "install",
        f"{PYSIDE_PKG}=={PYSIDE_PIN}", # 钉子 1+2：只装 Essentials、钉版本
        "-i", mirror, # 钉子 3：国内镜像
        "--no-warn-script-location",
        "--disable-pip-version-check",
        # F3：弱网加固 —— 别卡死重下、别写缓存占盘
        "--retries", "3", "--timeout", "30", "--no-cache-dir",
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=_PIP_TIMEOUT, creationflags=_CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return False, f"pip 超时（>{_PIP_TIMEOUT}s）{mirror}"
    except Exception as e: # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    tail = ((r.stdout or "") + (r.stderr or "")).strip()[-400:]
    return r.returncode == 0, tail


def _humanize_fail(tails: list[str]) -> str:
    """pip 全失败后的人话文案（钉子 4）。按可辨认的原因给不同的下一句。"""
    joined = " ".join(tails).lower()
    if "403" in joined or "404" in joined or "forbidden" in joined:
        why = "下载界面组件时被下载源拒绝了"
    elif any(k in joined for k in ("timed out", "timeout", "超时",
                                   "connection", "network", "unreachable",
                                   "temporary failure", "getaddrinfo")):
        why = "下载界面组件时网络不通或太慢"
    elif "no space" in joined or "磁盘" in joined:
        why = "磁盘空间不够，装不下界面组件"
    else:
        why = "界面组件没能装上"
    return (
        why + "。本次启动先用网页控制台，界面不受影响；"
        "请检查网络后重新打开程序再试一次，还是不行就看 logs/persona_morph.log 里 "
        "「界面组件」那几行。"
    )


def ensure_pyside6(log=None) -> tuple[bool, str]:
    """确保当前解释器可 import PySide6==钉子版本。返回 (成功?, 人话原因)。

    成功时原因固定 "ok"；失败时给人话（调用方直接写日志/界面，不要再加工）。
    幂等：已装且版本一致 → 立即返回，不碰网络。
    """
    def _log(level: str, msg: str) -> None:
        if log is not None:
            try:
                getattr(log, level)("界面组件：%s", msg)
            except Exception: # noqa: BLE001
                pass

    # 收尾（team-lead 批复）：32 位判别放最前、任何 pip 动作之前 ——
    # PySide6 官方 wheel 只有 win_amd64，
    # 32 位 Python 上怎么装都是失败，不如一开始就讲人话，别让用户白等三镜像竞速。
    import struct # noqa: PLC0415

    if struct.calcsize("P") == 4: # 指针 4 字节 = 32 位进程（64 位是 8）
        _log("error", "当前 Python 是 32 位，本产品不支持")
        return (False,
                "当前 Python 为 32 位，本产品仅支持 64 位 Windows"
                "（界面组件没有 32 位安装包）。请换用 64 位 Python 后重新打开程序。")

    # 快路径：已装且版本对（钉子 2 的幂等分支，首启之后每次启动都走这里）
    if pyside6_installed():
        return True, "ok"

    python_exe = sys.executable or "python"
    if not Path(python_exe).exists():
        # 极少见（嵌入式启动器）⇒ 退回 runtime python；再没有才放弃
        rt = ROOT / "runtime" / "python" / "python.exe"
        if rt.exists():
            python_exe = str(rt)
        else:
            return False, "找不到本程序的 Python 环境，界面组件装不上。请重新安装本程序。"

    # 已装但版本不符 ⇒ 讲清楚「更新」而不是「下载」（老用户升级路径的对白）
    had_old = importlib.util.find_spec("PySide6") is not None
    _log("info", ("版本不符，正在更新" if had_old else "首次启动，正在下载")
         + f"（约 100MB，{PYSIDE_PKG}=={PYSIDE_PIN}）")

    # ── 并行探测竞速（借鉴 update_check::fetch_any：并行 ⇒ 最坏 ≈ 一个超时）──
    # pip 一次只能指一个源 ⇒ 先并发探各镜像的 /simple/<包>/ 页，谁最快通指谁装。
    probe = _probe_mirrors(MIRRORS, _PROBE_TIMEOUT)
    if not any(r[0] for r in probe.values()):
        # 全败 ⇒ 耐心一轮（慢网 RTT 大的现场：几轮 TLS 握手就把 2s 吃光）
        _log("warning", "所有镜像第一遍都没探通，放慢再试一轮…")
        probe = _probe_mirrors(MIRRORS, _PROBE_PATIENT)
    usable = _pick_mirror(probe)
    if not usable:
        # 全挂：逐源列出哪条、什么错（BusyForm 口径的人话，用户粘出来一眼可排障）
        diag = _diag_lines([(_mirror_tag(m), probe[m][2]) for m in MIRRORS])
        _log("warning", "全部镜像都没探通：" + diag)
        return False, _humanize_fail([]) + "逐源情况：" + diag + "。"

    # ── F：快通路 —— 测速选最快源 → 自己分块并发下 wheel → 本地 pip 装 ──
    # 不通则 **完整回退** 既有单连接 pip 通路（下面那段），绝不半途而废。
    if _fast_download_enabled():
        _log("info", "正在为各镜像测速（挑最快的源）…")
        speed = _speed_mirrors(usable, _SPEED_TIMEOUT)
        order = _pick_fastest(speed, usable)
        cache_dir = os.path.join(tempfile.gettempdir(), _FETCH_DIR_NAME)
        fast_ok = False
        for mirror in order:
            prog = _make_progress(_log, mirror)
            # bps 来自测速采样：慢才分块（自适应，见 _fetch_wheel 注释）
            _bps = float((speed.get(mirror) or (False, 0.0, "", ""))[1] or 0.0)
            whl, why, how = _fetch_wheel(mirror, cache_dir, prog, _bps)
            if not whl:
                _log("warning", f"本地下载没成：{_mirror_tag(mirror)}→{why[:100]}")
                continue
            _log("info", f"已下到本地（{how}）：{os.path.basename(whl)}，开始安装…")
            iok, itail = _pip_install_local(python_exe, whl)
            if iok:
                fast_ok = True
                break
            _log("warning", f"本地 wheel 安装没成：{_mirror_tag(mirror)}（{itail[:160]}）")
        if not fast_ok:
            _log("warning", "快通路没成，回到常规下载（单连接 pip）…")

    tails: list[str] = []
    for mirror in usable:
        ok, tail = _pip_install(python_exe, mirror)
        if ok:
            break
        tails.append(tail)
        _log("warning", f"镜像没成，换下一个：{mirror}（{tail[:160]}）")
    else:
        # 装单上的镜像 pip 全败：诊断行取 pip 尾行（比探测原因更贴近真实死因）
        diag = _diag_lines([
            (_mirror_tag(m), (tails[i].strip().splitlines() or ["pip 失败"])[-1])
            for i, m in enumerate(usable)
        ])
        _log("warning", "所有镜像 pip 都没成：" + diag)
        return False, _humanize_fail(tails) + "逐源情况：" + diag + "。"

    # 装完复查：缓存失效 + 版本核对（不许「pip 说装好了」就完事）
    importlib.invalidate_caches()
    if not pyside6_installed():
        return False, (
            "界面组件下载后没能通过校验。本次先用网页控制台；"
            "重新打开程序会再试一次。"
        )
    _log("info", f"界面组件就绪（{PYSIDE_PKG}=={PYSIDE_PIN}）")
    return True, "ok"


# ---------------------------------------------------------------- 自检

def _selftest() -> list[tuple[str, bool, str]]:
    """三条纪律真跑：幂等快路径 / 版本钉子口径 / 失败文案讲人话。

    本机此刻 PySide6 已装（第一任务验证装进 runtime）⇒ 主流程走幂等分支；
    网络/安装分支用「钉子口径 + 文案加工」的纯逻辑断言覆盖，不真下载。
    """
    out: list[tuple[str, bool, str]] = []

    # 1) 幂等：已装 ⇒ 不碰网络直接 True
    ok, why = ensure_pyside6()
    out.append(("已装环境 ensure_pyside6 幂等通过（不下载）", ok and why == "ok", why))

    # 1b) 收尾：32 位判别 —— wheel 只有 win_amd64（审计第③项），
    #     32 位 Python 在任何 pip 动作之前就拒掉；64 位放行走正常流程
    import struct # noqa: PLC0415

    _real_calcsize = struct.calcsize
    try:
        struct.calcsize = lambda _fmt: 4 # mock：32 位进程
        ok32, why32 = ensure_pyside6()
        out.append(("32 位 Python 入口即拒（不碰 pip/镜像，讲人话）",
                    ok32 is False and "32 位" in why32 and "64 位" in why32, why32))
        struct.calcsize = lambda _fmt: 8 # mock：64 位进程
        ok64, why64 = ensure_pyside6()
        out.append(("64 位 Python 正常放行（不触发 32 位拒绝分支）",
                    ok64 is True and why64 == "ok", why64))
    finally:
        struct.calcsize = _real_calcsize

    # 2) 钉子 2：版本口径 —— pin 与 metadata 一致才放行
    try:
        ver = importlib.metadata.version(PYSIDE_PKG)
    except importlib.metadata.PackageNotFoundError:
        ver = ""
    out.append((f"当前 Essentials 版本 = 钉子 {PYSIDE_PIN}", ver == PYSIDE_PIN, ver))
    out.append(("pyside6_installed 对别的版本返回 False（钉版本不悄悄放行）",
                not pyside6_installed(pin="0.0.0"), ""))

    # 3) 钉子 1：只装 Essentials —— 安装命令里必须是「包名==钉子版本」，不许裸 PySide6
    import inspect
    src = inspect.getsource(_pip_install)
    out.append(("安装命令只装 Essentials 且钉 ==版本",
                "PYSIDE_PKG}=={PYSIDE_PIN" in src and '"-i", mirror' in src, ""))

    # 4) 钉子 4：失败文案讲人话 —— 不许裸术语、必须给下一步
    for tag, tails in (
        ("断网", ["ConnectionError: timed out"]),
        ("被拒", ["HTTP error 403 Forbidden"]),
        ("磁盘", ["No space left on device"]),
    ):
        msg = _humanize_fail(tails)
        plain = ("网页控制台" in msg and "重新" in msg
                 and "Traceback" not in msg and "pip install" not in msg)
        out.append((f"失败文案[{tag}] 讲人话并给下一步", plain, msg[:48]))

    # 5) 镜像列表全国内且默认清华（钉子 3）
    out.append(("镜像默认清华、候选全为国内源",
                MIRRORS[0].startswith("https://pypi.tuna") and
                all(("tuna" in m) or ("aliyun" in m) or ("tencent" in m) or ("ustc" in m)
                    or ("bit." in m) for m in MIRRORS), ";".join(MIRRORS)))

    # 6) 并行探测竞速：探得通的按耗时升序（最快通的最先装），挂的不进装单
    fake_probe = {
        MIRRORS[0]: (True, 0.9, ""),
        MIRRORS[1]: (True, 0.2, ""),
        MIRRORS[2]: (False, 2.0, "HTTP 403（源拒绝）"),
    }
    picked = _pick_mirror(fake_probe)
    out.append(("探测竞速：最快通的最先装、403 源不进装单",
                picked == [MIRRORS[1], MIRRORS[0]], str(picked)))

    # 7) 403 = 该源失败（不当终态，不当成「包不存在」）
    import unittest.mock as _m
    import urllib.error as _ue
    with _m.patch("urllib.request.urlopen",
                  side_effect=_ue.HTTPError(_probe_url(MIRRORS[0]), 403, "Forbidden", None, None)):
        pok, _pel, pwhy = _probe_once(MIRRORS[0], 2.0)
    out.append(("探测把 403 判为该源失败并记原因", (not pok) and "403" in pwhy, pwhy))

    # 8) 全部镜像挂 ⇒ 不碰 pip、耐心重试一轮、逐源诊断进人话文案
    calls = {"probe_rounds": 0, "pip": 0}

    def _fail_probe(mirrors=MIRRORS, timeout=_PROBE_TIMEOUT):
        calls["probe_rounds"] += 1
        return {m: (False, timeout, "ConnectionError") for m in mirrors}

    with _m.patch.object(sys.modules[__name__], "pyside6_installed", lambda pin=PYSIDE_PIN: False), \
         _m.patch.object(sys.modules[__name__], "_probe_mirrors", _fail_probe), \
         _m.patch.object(sys.modules[__name__], "_pip_install",
                         lambda *a, **k: calls.__setitem__("pip", calls["pip"] + 1) or (False, "")):
        bad_ok, bad_why = ensure_pyside6()
    out.append(("全部镜像挂：pip 一次没跑、耐心重试恰好一轮（共 2 轮探测）",
                (not bad_ok) and calls["pip"] == 0 and calls["probe_rounds"] == 2,
                f"pip={calls['pip']} rounds={calls['probe_rounds']}"))
    out.append(("全部镜像挂：人话文案带逐源诊断（三条源都在、给的还是原话）",
                "逐源情况" in bad_why and all(t in bad_why for t in ("清华", "阿里", "腾讯"))
                and "ConnectionError" in bad_why and "网页控制台" in bad_why,
                bad_why[-90:]))
    return out


if __name__ == "__main__":
    rows = _selftest()
    bad = [r for r in rows if not r[1]]
    for name, ok, extra in rows:
        print(("  OK  " if ok else "  FAIL") + "  " + name + (("   [" + extra + "]") if extra else ""))
    print()
    print(f"{len(rows) - len(bad)} 通过 / {len(bad)} 失败")
    raise SystemExit(1 if bad else 0)
