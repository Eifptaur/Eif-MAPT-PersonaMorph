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
  3. 国内镜像             —— 默认清华源；失败自动轮换阿里/腾讯国内镜像
                             （2026-09-23 本机实测清华对 cp310 wheel 返回 403，
                             阿里成功 —— 轮换是「国内镜像」钉子的兜底，不是换源）；
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

# 单镜像超时：约 100MB 下载，慢机器也要给足；三个镜像最坏 3×900s
_PIP_TIMEOUT = 900


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

    tails: list[str] = []
    for mirror in MIRRORS:
        ok, tail = _pip_install(python_exe, mirror)
        if ok:
            break
        tails.append(tail)
        _log("warning", f"镜像没成，换下一个：{mirror}（{tail[:160]}）")
    else:
        return False, _humanize_fail(tails)

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
    return out


if __name__ == "__main__":
    rows = _selftest()
    bad = [r for r in rows if not r[1]]
    for name, ok, extra in rows:
        print(("  OK  " if ok else "  FAIL") + "  " + name + (("   [" + extra + "]") if extra else ""))
    print()
    print(f"{len(rows) - len(bad)} 通过 / {len(bad)} 失败")
    raise SystemExit(1 if bad else 0)
