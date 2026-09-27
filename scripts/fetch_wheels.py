# -*- coding: utf-8 -*-
"""把依赖预置成**离线轮子**（给"快速包"用）—— 首启不再联网装依赖。

用法：
  py -3 scripts/fetch_wheels.py                 # 取全部依赖（进 offline/wheels）
  py -3 scripts/fetch_wheels.py --key           # 只取"必需 14 项"+它们的传递依赖
  py -3 scripts/fetch_wheels.py --out DIR       # 指定落点

口径（为什么这样做）：
  · 首启慢**不是带宽**（实测单连接 1.8~19.9 MB/s），而是 pip 的**解析往返**与
    **少数只有源码包的依赖要现场构建**（例如 PyAutoGUI 在 PyPI 上只有 sdist）；
  · 所以分三趟：① `--only-binary=:all:` 把有轮子的全下来（禁掉一切源码编译）；
    ② 允许源码包补齐剩下的；③ 用 `pip wheel` 把 sdist **预构建**成轮子（构建只在这里做一次）。
  · 产物目录直接就是 `setup_deps.py` 认定的离线目录（`offline/wheels`）⇒ 它检测到就走
    `pip install --no-index --find-links`，**零下载**。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
#: 与 `agent.wechat.dep_check("key")` 的必需项同口径（那个是唯一真源；这里只是为了"只取必需"）
KEY = ("wechatauto-replica", "requests", "urllib3", "pillow", "cryptography", "zstandard",
       "uiautomation", "comtypes", "pywin32", "pyperclip", "psutil", "colorama", "winsdk",
       "imageio-ffmpeg", "sounddevice", "pypinyin")


def _run(args, timeout=1800):
    t0 = time.time()
    p = subprocess.run(args, capture_output=True, creationflags=NO_WINDOW, timeout=timeout)
    out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
    return time.time() - t0, p.returncode, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "offline", "wheels"))
    ap.add_argument("--key", action="store_true", help="只取必需项（体积小很多）")
    ap.add_argument("--mirror", default=MIRROR)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    py = sys.executable or "py"
    req = os.path.join(ROOT, "requirements.txt")
    if a.key:
        req = os.path.join(os.path.dirname(a.out), "_req_key.txt")
        with open(req, "w", encoding="utf-8") as fh:
            fh.write("\n".join(KEY))

    def mb():
        n = 0
        for f in os.listdir(a.out):
            try:
                n += os.path.getsize(os.path.join(a.out, f))
            except OSError:
                pass
        return n / 1048576.0

    print("落点：%s（已有 %.1f MB）" % (a.out, mb()))
    print("\n① 只取预编译轮子（--only-binary=:all:，禁掉一切源码编译）…")
    dt, rc, out = _run([py, "-m", "pip", "download", "-r", req, "-d", a.out,
                        "--only-binary=:all:", "--progress-bar", "off", "--timeout", "60",
                        "--retries", "3", "-i", a.mirror])
    print("   %.1fs · rc=%s · 现在 %.1f MB" % (dt, rc, mb()))
    miss = [l for l in out.splitlines() if "No matching distribution" in l
            or "Could not find a version" in l]
    if miss:
        print("   只有源码包的（下面这几条只能预构建）：")
        for l in miss[:8]:
            print("     · " + l.strip()[:120])

    print("\n② 补齐只有源码包的（允许 sdist，仅下载）…")
    dt, rc, out = _run([py, "-m", "pip", "download", "-r", req, "-d", a.out,
                        "--progress-bar", "off", "--timeout", "60", "--retries", "3",
                        "-i", a.mirror])
    print("   %.1fs · rc=%s · 现在 %.1f MB" % (dt, rc, mb()))

    print("\n③ 把源码包预构建成轮子（构建只在这里做一次，首启就不用编译了）…")
    dt, rc, out = _run([py, "-m", "pip", "wheel", "-r", req, "-w", a.out,
                        "--progress-bar", "off", "--timeout", "60", "--retries", "2",
                        "-i", a.mirror])
    print("   %.1fs · rc=%s" % (dt, rc))
    if rc != 0:
        for l in out.strip().splitlines()[-6:]:
            print("     | " + l[:140])

    files = sorted(os.listdir(a.out))
    print("\n完成：%d 个文件 / %.1f MB" % (len(files), mb()))
    print("下一步：py -3 scripts/pack_online.py --with-wheels   （出「快速包」）")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
