# -*- coding: utf-8 -*-
"""离线包校验与清单生成（W5）：`offline/wheels/` 到底够不够装一台干净机器。

做三件事：
  1. 读 `requirements.txt`（含 `wechatauto-replica` 的钉死版本）+ 读 `agent/replica_adapter.py`
     里的 `KNOWN_GOOD`（本仓库实测过的适配层版本）⇒ 得到"必须有的包及版本"；
  2. 扫 `offline/wheels/*.whl`，逐个对版本；
  3. 写 `offline/wheels/MANIFEST.txt`（文件名/版本/大小/sha256 + 缺失清单 + 补齐命令），
     `--check` 模式下缺包直接 exit 2（可以挂进门禁）。

只读本地文件、不联网；纯函数可单测（判据见 `scripts/pack_offline_selftest.py`）。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WHEELS = os.path.join(ROOT, "offline", "wheels")
REQ = os.path.join(ROOT, "requirements.txt")
ADAPTER = os.path.join(ROOT, "agent", "replica_adapter.py")
MANIFEST = os.path.join(WHEELS, "MANIFEST.txt")

_NAME_RE = re.compile(r"^(?P<name>.+?)-(?P<ver>\d[^-]*)-")


def norm_name(n: str) -> str:
    """PEP 503 归一：下划线/点→连字符、小写（PyAutoGUI == pyautogui，wechatauto_replica == wechatauto-replica）。"""
    return re.sub(r"[-_.]+", "-", str(n or "")).strip().lower()


def parse_requirements(text: str) -> list:
    """返回 [(name, spec)]；spec 形如 '==1.2.2.2' / '>=2.28.2' / ''（无版本要求）。"""
    out = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(\[[^\]]*\])?\s*(==|>=|<=|~=|>|<)?\s*([0-9][A-Za-z0-9_.\-]*)?$", line)
        if not m:
            continue
        name, _extra, op, ver = m.group(1), m.group(2), m.group(3) or "", m.group(4) or ""
        out.append((name, (op + ver) if ver else ""))
    return out


def scan_wheels(folder: str) -> dict:
    """扫 wheel 目录 ⇒ {归一包名: {file, version, size}}（同名取版本最高）。"""
    res = {}
    if not os.path.isdir(folder):
        return res
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith(".whl"):
            continue
        m = _NAME_RE.match(fn)
        if not m:
            continue
        key = norm_name(m.group("name"))
        ver = m.group("ver")
        cur = res.get(key)
        if cur and ver_tuple(cur["version"]) >= ver_tuple(ver):
            continue
        res[key] = {"file": fn, "version": ver, "size": os.path.getsize(os.path.join(folder, fn))}
    return res


def ver_tuple(v: str):
    parts = re.findall(r"\d+", str(v or ""))
    return tuple(int(p) for p in parts) if parts else (0,)


def satisfies(version: str, spec: str) -> bool:
    """支持 ==、>=、<=、>、<、~=、空（都算满足）。"""
    spec = (spec or "").strip()
    if not spec:
        return True
    m = re.match(r"^(==|>=|<=|~=|>|<)\s*([0-9][A-Za-z0-9_.\-]*)$", spec)
    if not m:
        return True
    op, want = m.group(1), m.group(2)
    a, b = ver_tuple(version), ver_tuple(want)
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    if op == "==":
        return a == b
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    if op == "~=":
        return a >= b and a[:max(1, len(b) - 1)] == b[:max(1, len(b) - 1)]
    return True


def adapter_pin(path: str = ADAPTER) -> list:
    """从 replica_adapter.py 里读 MIN_VERSION / KNOWN_GOOD ⇒ [('wechatauto-replica', '==KNOWN_GOOD')]。"""
    try:
        txt = open(path, "r", encoding="utf-8").read()
    except Exception:
        return []
    good = re.search(r'KNOWN_GOOD\s*=\s*"([^"]+)"', txt)
    if not good:
        return []
    return [("wechatauto-replica", "==" + good.group(1))]


def coverage(reqs: list, wheels: dict) -> tuple:
    """返回 (ok: [(name, spec, version, wheel_dict)], missing: [(name, spec, 原因)]）。"""
    ok, missing = [], []
    for name, spec in reqs:
        key = norm_name(name)
        w = wheels.get(key)
        if not w:
            missing.append((name, spec, "离线包里没有这个包"))
        elif not satisfies(w["version"], spec):
            missing.append((name, spec, "离线包版本 %s 不满足 %s" % (w["version"], spec)))
        else:
            ok.append((name, spec, w["version"], w))
    return ok, missing


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(reqs: list, wheels: dict, with_hash: bool = True) -> dict:
    ok, missing = coverage(reqs, wheels)
    used = {w["file"]: w for _, _, _, w in ok}
    lines = []
    lines.append("# 离线包清单（由 scripts/pack_offline.py 生成，勿手改）")
    lines.append("# 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("# 安装方式：pip install --no-index --find-links=offline/wheels -r requirements.txt")
    lines.append("# 包数：%d（对 requirements 覆盖 %d，缺 %d）" % (len(wheels), len(ok), len(missing)))
    lines.append("")
    for name, spec, ver, w in sorted(ok, key=lambda x: x[0].lower()):
        h = sha256(os.path.join(WHEELS, w["file"])) if with_hash else ""
        lines.append("%-28s %-12s %-52s %9d  %s" % (norm_name(name), ver, w["file"], w["size"], h[:16]))
    extra = [w for fn, w in wheels.items() if w["file"] not in used]
    if extra:
        lines.append("")
        lines.append("# 额外带的包（不在 requirements 里，供排查/兼容用）：")
        for w in sorted(extra, key=lambda x: x["file"].lower()):
            lines.append("#   %s" % w["file"])
    if missing:
        lines.append("")
        lines.append("!! 缺失 %d 个（装不上的原因）:" % len(missing))
        for name, spec, why in missing:
            lines.append("!!   %s %s —— %s" % (name, spec, why))
        lines.append("!! 补齐命令（联网机器上跑，然后把 whl 拷进 offline/wheels/）：")
        lines.append("!!   %s -m pip download -d offline/wheels --no-deps -i https://pypi.tuna.tsinghua.edu.cn/simple %s"
                     % (sys.executable or "python", " ".join(
                         ("%s%s" % (n, s)) for n, s, _ in missing)))
    return {"text": "\n".join(lines) + "\n", "ok": ok, "missing": missing, "extra": extra}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验：缺包时 exit 2")
    ap.add_argument("--no-hash", action="store_true", help="不计算 sha256（快）")
    args = ap.parse_args()

    if not os.path.exists(REQ):
        print("[离线包] 找不到 requirements.txt：%s" % REQ)
        return 2
    reqs = parse_requirements(open(REQ, "r", encoding="utf-8").read())
    pin = adapter_pin()
    for p in pin:                        # 适配层以代码里的"实测版本"为准
        reqs = [r for r in reqs if norm_name(r[0]) != norm_name(p[0])] + [p]
    wheels = scan_wheels(WHEELS)
    res = build_manifest(reqs, wheels, with_hash=not args.no_hash)

    print("[离线包] wheel 文件 %d 个；requirements 覆盖 %d/%d"
          % (len(wheels), len(res["ok"]), len(reqs)))
    if pin:
        print("[离线包] 适配层钉版本（读自 agent/replica_adapter.py 的 KNOWN_GOOD）：%s%s" % pin[0])
    for name, spec, why in res["missing"]:
        print("  ✘ 缺 %s %s —— %s" % (name, spec, why))
    if res["extra"]:
        print("  · 额外包 %d 个（不在 requirements 里）：%s"
              % (len(res["extra"]), ", ".join(w["file"] for w in res["extra"][:6])))
    if args.check:
        return 2 if res["missing"] else 0
    try:
        with open(MANIFEST, "w", encoding="utf-8") as f:
            f.write(res["text"])
        print("[离线包] 清单已写入 %s" % MANIFEST)
    except Exception as e:
        print("[离线包] 清单写盘失败：%s" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
