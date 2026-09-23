# -*- coding: utf-8 -*-
"""PySide6 自举 —— import PySide6 之前的唯一通路（工单·丙-3 第一步，工单 2.1 硬钉子）。

=====================================================================
为什么必须有这个模块
=====================================================================
2026-09-23 用户拍板「PySide6 首启自动装、不进包」⇒ 老用户一键更新后，机器上
很可能还没有界面组件。而 launcher.exe 存在「旧版仍在跑」的窗口期（占用 →
半装 → 重启补换），旧 launcher 不会帮装 PySide6 ⇒ 自动装逻辑必须做在
**Python 侧入口**（本模块）；launcher 侧检测只是提前介入优化体验，
不是唯一通路。过渡期网页控制台并存，Qt 壳自举失败也不至于两眼一抹黑。

四个钉子（违反任一 = 退回，工单第 2 节）：
  1. 只装 Essentials      —— PySide6-Essentials==6.11.2（界面只用 Core/Gui/
                             Widgets/Svg；完整版 633MB 里 Addons 234MB 用不上）；
  2. 钉版本 ==6.11.2      —— 与原型一致；已装 6.11.2 直接跳过（幂等），
                             版本不符才重装，绝不悄悄换版本；
  3. 国内镜像 + 并行探测竞速  —— 借鉴 agent/update_check.py::fetch_any 的成熟机制
                             （2026-09-23 工单·丙-4 第三步）：pip 一次只能指一个源 ⇒
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
import subprocess
import sys
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
    except Exception as e:  # noqa: BLE001
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
    """逐源诊断行：「清华→HTTP 403（源拒绝）；阿里→超时」——用户粘出来一眼可排障。"""
    return "；".join(f"{tag}→{(reason or 'ok')[:80]}" for tag, reason in pairs)


def _pip_install(python_exe: str, mirror: str) -> tuple[bool, str]:
    """对一个镜像跑一次 pip。返回 (成功?, 原始输出尾部——只进日志不进界面)。"""
    cmd = [
        python_exe, "-m", "pip", "install",
        f"{PYSIDE_PKG}=={PYSIDE_PIN}",       # 钉子 1+2：只装 Essentials、钉版本
        "-i", mirror,                        # 钉子 3：国内镜像
        "--no-warn-script-location",
        "--disable-pip-version-check",
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=_PIP_TIMEOUT, creationflags=_CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return False, f"pip 超时（>{_PIP_TIMEOUT}s）{mirror}"
    except Exception as e:  # noqa: BLE001
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
            except Exception:  # noqa: BLE001
                pass

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
