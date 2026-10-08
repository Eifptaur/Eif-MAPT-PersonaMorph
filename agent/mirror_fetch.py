# -*- coding: utf-8 -*-
"""通用下载提速：**多源竞速选最快（＋失败自动换源）**，给 pip／依赖安装／在线包下载共用。

=====================================================================
为什么有这个模块（一句话：**不是没做，是做了但没共用**）
=====================================================================
`qt_bootstrap.py` 里已经有一套成熟机制 —— 并行探测各镜像 → 选**最快通的** → 分块并发下 wheel
→ `pip install --no-index` 本地装 → 进度可见 → 断点续传 —— 但它**定位是「PySide6 自举专用」**。
后果：**装可选依赖（如 whisper）时走的是裸 `pip install`**（单源、单连接）⇒
实测 **`llvmlite 41.9MB` 下了 6 分钟**。

本模块把那套机制的**通用部分**拿出来：**多源并行探测 → 按实测耗时排序 → 逐个试**，
pip 安装、依赖安装、包下载都可以调它。

纪律（与项目其它网络模块一致）：
  1. **只用国内源**（清华／阿里／腾讯），**不碰裸 pypi**（用户在国内，裸 pypi 是"最慢的那条"）；
  2. **`403` 只是"该源没过"，不当终态** ⇒ 立刻换下一个（这条是 `qt_bootstrap` 注释里记着的血泪：
     「清华对 cp310 wheel 返回 403 的教训，阿里成功」—— 装 whisper 时**又踩了一次同样的坑**）；
  3. **失败讲人话**：逐源列"哪条、什么错"，不回裸 traceback；
  4. ⛔ **不改 `qt_bootstrap.py`**：自举是"用户开程序的第一关"，本轮只做"通用的这份"。
     将来若要让自举也切过来，属**独立一批**（要单独跑自举冒烟）。
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

#: 国内镜像（顺序＝默认优先级；真正排序靠**实测耗时**，不靠这个顺序）
MIRRORS = (
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple/",
    "https://mirrors.cloud.tencent.com/pypi/simple/",
)

#: 单源探测超时（秒）。并行探测 ⇒ 最坏 ≈ 这个值（串行逐个等才是老毛病）
PROBE_TIMEOUT = 2.0
#: pip 单次安装超时（秒）：约 100MB 下载，慢机器也要给足
PIP_TIMEOUT = 900
_UA = "Mozilla/5.0 (mirror-fetch)"


def mirror_tag(mirror: str) -> str:
    """镜像的人话短名（逐源诊断行用）：tuna→清华／aliyun→阿里／tencent→腾讯。"""
    if "tuna" in mirror:
        return "清华"
    if "aliyun" in mirror:
        return "阿里"
    if "tencent" in mirror:
        return "腾讯"
    return mirror.split("//")[-1].split("/")[0]


def simple_url(mirror: str, pkg: str = "") -> str:
    """该镜像上的 PEP 503 地址：**给了包名就指包页，不给就指索引首页**。

    ⚠️ 为什么两种都要：**装依赖前**（`setup_deps`）还不知道要装哪个包 ⇒ 只能探首页；
    而**装某个包时**（`pip_install`）探**包页**更准 —— 有的源索引活着、包页却 403/404
    （实测清华即如此）⇒ 只探首页会选中一个**装不了**的源、白等一轮。
    """
    base = mirror.rstrip("/") + "/"
    return base + (str(pkg).lower() + "/" if str(pkg or "").strip() else "")


def probe_once(mirror: str, pkg: str = "", timeout: float = PROBE_TIMEOUT):
    """轻量 GET ⇒ `(通?, 耗时s, 人话原因)`。**`pkg` 留空＝探索引首页**。**403 算"该源没过"，不算终态。**
    """
    req = urllib.request.Request(simple_url(mirror, pkg),
                                 headers={"User-Agent": _UA})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code = getattr(r, "status", 200)
            el = time.perf_counter() - t0
            return (code == 200), el, ("" if code == 200 else "HTTP %d" % code)
    except urllib.error.HTTPError as e:
        return False, time.perf_counter() - t0, "HTTP %d（源拒绝）" % e.code
    except Exception as e: # noqa: BLE001
        return False, time.perf_counter() - t0, type(e).__name__


def probe_mirrors(pkg: str = "", mirrors=MIRRORS, timeout: float = PROBE_TIMEOUT) -> dict:
    """**并行**探测各源 ⇒ `{mirror: (ok, 耗时, 原因)}`。`pkg` 留空 ⇒ 探各源索引首页。

    并行 ⇒ 最坏 ≈ 单源超时；**串行逐个等**才会把最坏放大成 N×超时。
    """
    got: dict = {}
    lock = threading.Lock()

    def _one(m: str) -> None:
        res = probe_once(m, pkg, timeout)
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


def pick_mirrors(probe: dict) -> list:
    """探得通的源按**实测耗时升序**排（最快通的最先试）；挂的不进单。

    ⛔ 只把"通的"排进来：让 pip 去试一个已经 403 的源，就是让用户白等多一轮。
    """
    return sorted([m for m, r in (probe or {}).items() if r and r[0]],
                  key=lambda m: probe[m][1])


def diag_lines(probe: dict) -> str:
    """逐源诊断（用户粘出来一眼可排障）：`清华→HTTP 403（源拒绝）；阿里→ok`。"""
    return "；".join("%s→%s" % (mirror_tag(m), (r[2] or "ok")[:60])
                     for m, r in (probe or {}).items())


def best_mirror(pkg: str):
    """选**唯一那个**最快的源 ⇒ `(mirror|"", 人话说明)`。全挂 ⇒ `("", 逐源诊断)`。"""
    probe = probe_mirrors(pkg)
    order = pick_mirrors(probe)
    if not order:
        return "", "所有国内源都不通：%s" % diag_lines(probe)
    return order[0], "选中 %s（%.2fs）" % (mirror_tag(order[0]), probe[order[0]][1])


def pip_install_args(pkg_specs, mirror: str) -> list:
    """拼 pip 安装命令（**纯函数**，便于判据直接钉住"有没有带上 -i 源"）。

    ⚠️ 必须带 `--disable-pip-version-check`（少一次出网）与 `-i <源>`；
    ⛔ **不带 `--trusted-host`**：那是给 http 源用的，我们全是 https。
    """
    args = [sys.executable, "-m", "pip", "install",
            "--disable-pip-version-check", "-i", mirror]
    if isinstance(pkg_specs, str):
        args.append(pkg_specs)
    else:
        args.extend(list(pkg_specs or []))
    return args


def pip_install(pkg_specs, pkg_for_probe: str = "", timeout: int = PIP_TIMEOUT):
    """**多源竞速 + 逐个试**地装包 ⇒ `(ok, 人话)`。

    `pkg_for_probe` 只影响"探哪个包页"（默认取第一个 spec 的包名）。
    ⛔ 失败时**逐源列出原因**（哪条、什么错），不裸 traceback —— 用户要能照着排障。
    """
    specs = [pkg_specs] if isinstance(pkg_specs, str) else list(pkg_specs or [])
    if not specs:
        return False, "没有要装的包"
    probe_pkg = (pkg_for_probe or "").strip()
    if not probe_pkg:
        # 从 spec 里抠包名：去掉版本约束与 extras（`foo==1.2` / `foo[socks]>=1` ⇒ `foo`）
        probe_pkg = specs[0].split("[")[0].split("=")[0].split(">")[0].split("<")[0].strip()
    probe = probe_mirrors(probe_pkg)
    order = pick_mirrors(probe)
    if not order:
        return False, "所有国内源都不通 ⇒ 没装成：%s" % diag_lines(probe)
    tried = []
    for m in order:
        args = pip_install_args(specs, m)
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                               creationflags=(0x08000000 if sys.platform == "win32" else 0))
        except Exception as e: # noqa: BLE001
            tried.append((mirror_tag(m), "%s" % type(e).__name__))
            continue
        if r.returncode == 0:
            return True, "装好了（源=%s）" % mirror_tag(m)
        tail = [x for x in ((r.stderr or "") + (r.stdout or "")).strip().splitlines() if x.strip()]
        tried.append((mirror_tag(m), (tail[-1][:80] if tail else "退出码 %d" % r.returncode)))
    return False, "逐个源都没装成：%s" % "；".join("%s→%s" % t for t in tried)
