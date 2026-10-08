# -*- coding: utf-8 -*-
"""W7 依赖自愈：**诊断 → 计划 → 可粘贴命令**（安装动作复用现成的 `scripts/setup_deps.py`，不重写）。

为什么要它：用户装好之后，微信版本一更新、驱动库一换代，缺依赖的表现是"某个功能莫名崩"，
而且现有 `setup_deps.py` 只在**启动流程里**跑，用户手里没有"哪里缺、怎么补"的可执行清单。
本模块补的正是这一层：**逐包体检 + 离线/镜像两条命令 + 一键自愈入口**，默认 dry（不联网、不改环境）。

口径（照项目现状）：
  · 离线优先：`offline/wheels` 里有 wheel 就走 `--no-index`，完全不出网；
  · 其次镜像：缺的包给 `--index-url https://mirrors.aliyun.com/pypi/simple/` 的**可粘贴命令**；
  · **默认只打印不执行**；要真装必须显式 `execute=True`（这条是为了不让"自愈"变成静默改环境）。
"""
from __future__ import annotations

import importlib.metadata as md
import os
import re
import subprocess
import sys

from .config import ROOT
import logging

log = logging.getLogger("persona-morph")

REQ_PATH = os.path.join(ROOT, "requirements.txt")
WHEELS_DIR = os.path.join(ROOT, "offline", "wheels")
MIRROR = "https://mirrors.aliyun.com/pypi/simple/"
SPEC_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*(==|>=|<=|>|<|~=)\s*([0-9][0-9A-Za-z.\-]*)\s*$")


# ── 纯函数（可单测，不需要真装包）────────────────────────────────────────
def parse_requirements(path: str = None) -> list:
    """解析 requirements.txt ⇒ [(名字, 运算符, 版本)]；注释/空行/无版本约束的行跳过。"""
    out = []
    try:
        with open(path or REQ_PATH, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                m = SPEC_RE.match(line)
                if m:
                    out.append((m.group(1), m.group(2), m.group(3)))
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    return out


def vtuple(v: str) -> tuple:
    """版本 → 可比较元组。数字段变 (0,int)、字母段变 (1,str)，混排也不会 TypeError。"""
    toks = []
    for part in re.split(r"[.\-_]", str(v or "")):
        for m in re.finditer(r"\d+|[A-Za-z]+", part):
            s = m.group(0)
            toks.append((0, int(s)) if s.isdigit() else (1, s.lower()))
    return tuple(toks)


def spec_ok(installed: str, op: str, want: str) -> bool:
    """判断已装版本是否满足约束（够用即可，不做完整 PEP440）。"""
    if not installed:
        return False
    a, b = vtuple(installed), vtuple(want)
    return {"==": a == b, ">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b,
            "~=": a >= b and a[:2] == b[:2]}.get(op, False)


def classify(reqs, installed_fn) -> list:
    """逐包体检 ⇒ [{name, required, installed, status}]，status ∈ ok/missing/mismatch。"""
    out = []
    for name, op, want in reqs:
        got = ""
        try:
            got = str(installed_fn(name) or "")
        except Exception:
            got = ""
        if not got:
            status = "missing"
        elif op == "" :
            status = "ok"
        else:
            status = "ok" if spec_ok(got, op, want) else "mismatch"
        out.append({"name": name, "required": "%s%s" % (op, want), "installed": got, "status": status})
    return out


def summarize(diag) -> dict:
    bad = [d for d in (diag or []) if d["status"] != "ok"]
    return {"total": len(diag or []), "ok": len(diag or []) - len(bad), "bad": len(bad),
            "missing": [d["name"] for d in bad if d["status"] == "missing"],
            "mismatch": ["%s(需%s,实%s)" % (d["name"], d["required"], d["installed"] or "无")
                         for d in bad if d["status"] == "mismatch"]}


def build_plan(bad_names, py_exe: str, offline_available: bool) -> dict:
    """按"离线优先、其次镜像"生成计划（纯函数，便于单测）。"""
    steps, cmds = [], []
    req = '"%s"' % REQ_PATH
    if bad_names:
        if offline_available:
            cmd = '"%s" -m pip install --no-index --find-links "%s" -r %s' % (py_exe, WHEELS_DIR, req)
            steps.append({"what": "从离线 wheels 安装缺失/不符的包（全程不出网）", "cmd": cmd})
        else:
            cmd = '"%s" -m pip install -r %s --index-url %s' % (py_exe, req, MIRROR)
            steps.append({"what": "离线包缺失 ⇒ 走镜像安装（需要网络）", "cmd": cmd})
        cmds.append(cmd)
        heal = '"%s" scripts\\setup_deps.py' % py_exe
        steps.append({"what": "一键自愈（项目自带脚本，内部就是「离线优先、其次镜像」）", "cmd": heal})
        cmds.append(heal)
    verify = '"%s" scripts\\selftest.py' % py_exe
    steps.append({"what": "装完复查", "cmd": verify})
    cmds.append(verify)
    return {"offline_available": bool(offline_available), "steps": steps, "commands": cmds}


def plan(py_exe: str = None, path: str = None) -> dict:
    """体检 + 计划（默认 dry，不装任何东西）。"""
    diag = diagnose(path=path)
    s = summarize(diag)
    bad_names = [d["name"] for d in diag if d["status"] != "ok"]
    p = build_plan(bad_names, py_exe or runtime_python(), offline_available())
    p.update({"diagnose": diag, "summary": s})
    return p


def diagnose(path: str = None, installed_fn=None, py_exe: str = None) -> list:
    """体检**指定解释器**（默认项目自带运行时）里的包 —— 不是当前进程的包。

    这条是本模块最容易被骗的地方：脚本用 `py -3` 跑时，当前进程是系统解释器，
    它里面**没有**离线包，于是会把"运行时里装得好好的 1.2.2.2"报成"实 1.1.5.1、缺 11 项"（假红）。
    所以默认去读**运行时的 site-packages**（文件级 .dist-info，不启子进程）。
    """
    return classify(parse_requirements(path),
                    installed_fn or (lambda n: installed_version(n, py_exe)))


def _norm(n: str) -> str:
    return re.sub(r"[-_.]+", "_", str(n or "")).lower()


def site_packages(py_exe: str = None) -> str:
    """某个解释器的 site-packages 目录（找不到返回 ""）。"""
    try:
        base = os.path.dirname(os.path.abspath(py_exe or runtime_python()))
        for sub in ("Lib", "lib"):
            p = os.path.join(base, sub, "site-packages")
            if os.path.isdir(p):
                return p
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    return ""


def installed_from_dir(name: str, sp: str) -> str:
    """从 `*.dist-info` 目录名读版本（`wechatauto_replica-1.2.2.2.dist-info`）。"""
    want = _norm(name)
    try:
        for d in os.listdir(sp):
            if d.endswith(".dist-info"):
                nm, _, ver = d[:-len(".dist-info")].rpartition("-")
                if _norm(nm) == want:
                    return ver
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    return ""


def installed_version(name: str, py_exe: str = None):
    """已装版本：优先读运行时的 site-packages，没有该目录才回落到当前解释器。"""
    sp = site_packages(py_exe)
    if sp:
        return installed_from_dir(name, sp)
    return md.version(name)


def probe_source(py_exe: str = None) -> str:
    """这次体检到底查的哪里（写进汇报，避免再被"跑在哪个解释器上"骗一次）。"""
    sp = site_packages(py_exe)
    return ("site-packages: " + sp) if sp else ("当前解释器: " + sys.executable)


_OFFLINE_CACHE = {}


def _wheel_files() -> list:
    """`offline/wheels` 里的全部 wheel / sdist 文件名（读不出 ⇒ 空表）。"""
    try:
        return [f for f in os.listdir(WHEELS_DIR)
                if f.endswith(".whl") or f.endswith(".tar.gz")]
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return []


def _wheel_name_ver(fn: str):
    """`文件名 ⇒ (归一化包名, 版本, 标签串)`；认不出 ⇒ None。"""
    base = fn[:-4] if fn.endswith(".whl") else (fn[:-7] if fn.endswith(".tar.gz") else fn)
    parts = base.split("-")
    if len(parts) < 2:
        return None
    name = parts[0].lower().replace("_", "-")
    ver = parts[1]
    tags = "-".join(parts[2:]) if len(parts) > 2 else ""
    return name, ver, tags


def _wheel_fits_python(tags: str, py_mm: tuple) -> bool:
    """编译型轮子能不能装进 `py_mm`（如 `(3, 10)`）的解释器。

    · `py3` / `none` 这类**纯 Python** 标签 ⇒ 任何 3.x 都行；
    · `abi3`（稳定 ABI）⇒ 标称版本 ≤ 目标的 3.10/3.11/3.12 都行；
    · 普通 `cp3XX` ⇒ **必须与目标完全一致**（实测：我们的 numpy/pillow/pywin32 等 12 个只有 cp310）。
    """
    t = (tags or "").lower()
    if not t:
        return True                      # 纯 Python 的 sdist（源码包）⇒ 不设限
    m = re.search(r"cp3(\d+)", t)
    if not m:
        return True                      # `py3-none-any` 一类
    want = int("%d%d" % (py_mm[0], py_mm[1]))
    got = int("3" + m.group(1))
    if "abi3" in t:
        return got <= want
    return got == want


def _satisfies(have_ver: str, op: str, want_ver: str) -> bool:
    """`have_ver` 满不满足 `op want_ver`（`==` / `>=` / `<=` / `~=`；空 op 视为任意）。"""
    if not op:
        return True
    if op == "==":
        return str(have_ver) == str(want_ver)
    try:
        a, b = vtuple(str(have_ver)), vtuple(str(want_ver))
    except Exception:  # noqa: BLE001
        return False
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    if op == "~=":
        return a >= b and a[:2] == b[:2]
    return False


def offline_available(py_exe: str = None) -> bool:
    """`offline/wheels` 能不能**真的**满足 requirements（即 `pip install --no-index` 装得上）。

    ⛔⛔ **现场（用户实测报回）**：旧实现是
      `any(f.startswith("wechatauto_replica-") and f.endswith(".whl") …)` —— **只问"目录里有没有这个文件"**。
      而那时目录里放的是 **1.2.2.2**，requirements 要 **1.2.4.4** ⇒ 它照样返回 `True`
      ⇒ 计划走 `--no-index` ⇒ **装不上**，只能人工联网补齐（"含依赖包"的承诺当场失效）。
      ⛔ 同一个弱检查当时在 `scripts/setup_deps.py` 里**还抄了一份** ⇒ 两处各自漂移。
    ✅ 现在**逐项核**（这里是**唯一实现点**，`setup_deps.py` 也调它）：
      ① 每个 requirement 都能在轮子目录里找到**名字对得上、版本满足**的 wheel / sdist；
      ② 给了 `py_exe`（默认＝项目自带运行时）时再核 **ABI**：编译型轮子的标签必须能装进那个解释器
         （我们的编译轮子只有 `cp310`；`py3-none-any` 与 `abi3` 通用）。
    ⚠️ 它是"**够不够**"的判断，不是"装没装成"的判断 —— 真装成不成交给 `dep_check` 复查。
    """
    exe = str(py_exe or runtime_python())
    key = exe
    if key in _OFFLINE_CACHE:
        return _OFFLINE_CACHE[key]
    ok = False
    try:
        files = _wheel_files()
        # 分母守卫：太少说明这不是一个完整的离线集（光有 wechatauto 一个包不算"能离线")
        if len(files) >= 10:
            py_mm = (3, 10)
            try:
                out = subprocess.run([exe, "-c",
                                      "import sys;print('%d %d'%sys.version_info[:2])"],
                                     capture_output=True, text=True, timeout=20,
                                     creationflags=(0x08000000 if os.name == "nt" else 0))
                if out.returncode == 0 and out.stdout.strip():
                    a, b = out.stdout.split()[:2]
                    py_mm = (int(a), int(b))
            except Exception:  # noqa: BLE001
                pass
            parsed = [_wheel_name_ver(f) for f in files]
            parsed = [p for p in parsed if p]
            ok = True
            for name, op, ver in parse_requirements():
                nm = str(name).lower().replace("_", "-")   # ⛔ requirements 里写的是 Pillow / PyAutoGUI 这种大小写
                cand = [p for p in parsed if p[0] == nm]
                if not cand:
                    ok = False
                    break
                if not any(_satisfies(p[1], op, ver) and _wheel_fits_python(p[2], py_mm)
                           for p in cand):
                    ok = False
                    break
    except Exception:  # noqa: BLE001
        ok = False
    _OFFLINE_CACHE[key] = bool(ok)
    return bool(ok)


def offline_why(py_exe: str = None) -> str:
    """`offline_available()` 为假时**说清差在哪**（给安装日志/排障用，不许只报 False）。"""
    try:
        files = _wheel_files()
        parsed = [p for p in (_wheel_name_ver(f) for f in files) if p]
        py_mm = (3, 10)
        try:
            out = subprocess.run([str(py_exe or runtime_python()), "-c",
                                  "import sys;print('%d %d'%sys.version_info[:2])"],
                                 capture_output=True, text=True, timeout=20,
                                 creationflags=(0x08000000 if os.name == "nt" else 0))
            if out.returncode == 0 and out.stdout.strip():
                a, b = out.stdout.split()[:2]
                py_mm = (int(a), int(b))
        except Exception:  # noqa: BLE001
            pass
        miss, badabi = [], []
        for name, op, ver in parse_requirements():
            nm = str(name).lower().replace("_", "-")
            cand = [p for p in parsed if p[0] == nm]
            if not cand:
                miss.append("%s%s%s" % (name, op, ver))
                continue
            if not any(_satisfies(p[1], op, ver) for p in cand):
                miss.append("%s%s%s（轮子里只有 %s）" % (name, op, ver,
                                                       "、".join(sorted({p[1] for p in cand}))))
                continue
            if not any(_satisfies(p[1], op, ver) and _wheel_fits_python(p[2], py_mm)
                       for p in cand):
                badabi.append("%s（该解释器 %d.%d 用不了）" % (name, py_mm[0], py_mm[1]))
        bits = []
        if len(files) < 10:
            bits.append("轮子太少（%d 个）" % len(files))
        if miss:
            bits.append("缺/版本不符：" + "、".join(miss[:4]))
        if badabi:
            bits.append("ABI 不符：" + "、".join(badabi[:4]))
        return "；".join(bits) or "未知"
    except Exception as e:  # noqa: BLE001
        return "检查异常：%s" % type(e).__name__


def runtime_python() -> str:
    """优先用项目自带运行时（离线包就是给它装的），没有就用当前解释器。"""
    p = os.path.join(ROOT, "runtime", "python", "python.exe")
    return p if os.path.exists(p) else sys.executable


def run(execute: bool = False, timeout: int = 900, py_exe: str = None) -> dict:
    """dry（默认）：只返回计划；execute=True：真跑第一步安装命令并回读尾部输出。"""
    p = plan(py_exe=py_exe)
    if not execute or not [d for d in p["diagnose"] if d["status"] != "ok"]:
        p["executed"] = False
        return p
    cmd = p["steps"][0]["cmd"]
    try:
        # `creationflags`：这条跑的是 pip 安装命令（分钟级、会输出一大堆），
        # 不给"不要窗口"的话用户会看到控制台窗闪一下。
        # ⚠️ 这里的 `encoding="utf-8"` 是**既有的编码坑**（`shell=True` 起的是 cmd.exe、
        #    输出是系统 ANSI 代码页）——与 `jobs.py` 同源，留待统一收，本轮不动它。
        r = subprocess.run(cmd, shell=True, timeout=timeout, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           creationflags=0x08000000 if os.name == "nt" else 0)
        p["executed"] = True
        p["returncode"] = r.returncode
        p["output_tail"] = "\n".join((r.stdout or "").splitlines()[-12:] +
                                     (r.stderr or "").splitlines()[-12:])
    except Exception as e:
        p["executed"] = True
        p["returncode"] = -1
        p["output_tail"] = "%s: %s" % (type(e).__name__, e)
    return p


def summary_line() -> str:
    s = summarize(diagnose())
    if not s["bad"]:
        return "依赖 %d 项全部满足" % s["total"]
    return "依赖缺 %d 项：缺 %s%s" % (
        s["bad"], ",".join(s["missing"]) or "-",
        ("；版本不符 " + ",".join(s["mismatch"])) if s["mismatch"] else "")
