# -*- coding: utf-8 -*-
"""§6-6 首次启动冒烟（判据版）：把**出的包**解到临时目录、无头启动、验"新机器第一次打开"的代码侧，再杀干净。

为什么要它：发布清单里这一条原来只能靠人"装一遍看看"。而它正是最容易出事的一步
（首启要自动生成口令与配置、控制台要起得来），而且**跑在副本上**才不会污染用户正在用的那份数据。

⛔ 跳过规则：**没有包就如实跳过**。包是 `scripts/pack_online.py` 的产物、不是每轮都出；
找不到包时打一条跳过说明 + 汇总行（不算失败，也不能因此判绿别的东西）。

验的是不需要真微信的那几项：自动生成口令 / 配置落盘 / 控制台起得来能应答 / 首页能取 /
挂件端点可达 / **鉴权两个方向**（`/api/version` 故意免认证；受保护端点不带口令必须 401）。
**"发一条测试消息"必须在真机上做**（本判据不碰微信）。
"""
import glob
import io
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMP = os.environ.get("TEMP") or r"C:\Windows\Temp"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
PASS, FAIL, SKIP = [0], [0], [0]


def ok(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("  [%s]" % detail if detail else ""))
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
    return bool(cond)


def get(url, timeout=4):
    req = urllib.request.Request(url, headers={"User-Agent": "pm-smoke"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", errors="replace")


def copy_pids(marker):
    """→ `(pids, err)`：命令行里带 `marker`（本次副本路径）的 python 进程 pid 集合。

    ⛔ **不能只比"跑前/跑后的 pid 差集"**：整包自检是**并发**跑的，别的判据也会起 python 进程
    （实测：并发跑时差集里冒出 4 个不属于本判据的 pid ⇒ 假红）。按**命令行归属**数才不会串。
    `err` 非 None 表示**拿不到命令行**（调用方据此"如实跳过"，不许当成失败）。
    取命令行优先 PowerShell（`Get-CimInstance`）——`wmic` 在新版 Windows 上已被移除（本机实测没有）。
    """
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Select-Object ProcessId,CommandLine | ConvertTo-Csv -NoTypeInformation")
    out = None
    err = None
    for cmd in (["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                ["wmic", "process", "where", "name='python.exe'",
                 "get", "ProcessId,CommandLine", "/format:csv"]):
        try:
            r = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW, timeout=40)
            if r.returncode == 0 and r.stdout.strip():
                out = r.stdout.decode("gbk", "ignore")
                err = None
                break
            err = "退出码 %s" % r.returncode
        except (OSError, subprocess.SubprocessError) as exc:
            err = exc
    if out is None:
        return None, err
    pids = set()
    for line in out.split("\n"):
        if marker.lower() not in line.lower():
            continue
        for cell in line.replace('"', "").strip().split(","):
            cell = cell.strip()
            if cell.isdigit():
                pids.add(cell)
    return pids, None


def newest_zip():
    cands = glob.glob(os.path.join(os.path.dirname(ROOT), "群相-在线包-*.zip"))
    cands += glob.glob(os.path.join(ROOT, "_outbox", "*.zip"))
    return max(cands, key=os.path.getmtime) if cands else None


def main():
    zp = newest_zip()
    if not zp:
        SKIP[0] += 1
        print("  SKIP 没有出好的包（`pack_online.py` 的产物）⇒ 首启冒烟这次跳过")
        print("== 首启冒烟判据：%d 通过 / %d 失败 / %d 跳过 ==" % (PASS[0], FAIL[0], SKIP[0]))
        return 0
    print("  用包：%s" % zp)
    root = os.path.join(TEMP, "pm-smoke-%d" % random.randint(100000, 999999))
    os.makedirs(root, exist_ok=True)
    with zipfile.ZipFile(zp) as z:
        z.extractall(root)
    inner = os.path.join(root, "persona morph")
    ok("包解出来有顶层目录 `persona morph/`", os.path.isdir(inner), inner)
    py = os.path.join(ROOT, "runtime", "python", "python.exe")
    if not os.path.isfile(py):
        print("  SKIP 本机没有产品运行时 ⇒ 首启冒烟跳过")
        SKIP[0] += 1
        print("== 首启冒烟判据：%d 通过 / %d 失败 / %d 跳过 ==" % (PASS[0], FAIL[0], SKIP[0]))
        return 0
    # 在线包**故意不带** Python：首启由启动器调 `scripts/setup_python.ps1` 现拉 ⇒ 这里只钉"脚本在、且不是空壳"
    sps = os.path.join(inner, "scripts", "setup_python.ps1")
    ok("包里带运行时供给脚本（启动器首启靠它拉 Python）", os.path.isfile(sps), sps)
    if os.path.isfile(sps):
        _ps = io.open(sps, encoding="utf-8", errors="ignore").read()
        ok("供给脚本真会拉嵌入式 Python 并落到 runtime/（不是空壳）",
           ("embed" in _ps.lower() or "python-3" in _ps.lower()) and "runtime" in _ps,
           "%d 字节" % len(_ps))
    ok("副本里没有 config.json（＝真·首次启动）", not os.path.isfile(os.path.join(inner, "config.json")), "")

    env = dict(os.environ)
    env.update({"QT_QPA_PLATFORM": "offscreen", "WX_NO_UI_POP": "1", "WXAGENT_FOREGROUND": "1",
                "PYTHONIOENCODING": "utf-8",
                "WX_AGENT_CONFIG": os.path.join(inner, "config.json")})
    logf = os.path.join(root, "boot.log")
    fh = io.open(logf, "wb")
    proc = subprocess.Popen([py, os.path.join("scripts", "persona_morph.py"), "--foreground"],
                            cwd=inner, env=env, stdout=fh, stderr=subprocess.STDOUT,
                            creationflags=NO_WINDOW)
    url = tok = None
    last_err = None
    t0 = time.time()
    try:
        while time.time() - t0 < 90:
            if proc.poll() is not None:
                break
            cu = os.path.join(inner, "logs", "console.url")
            if os.path.isfile(cu):
                try:
                    url = io.open(cu, encoding="utf-8").read().strip()
                except OSError as exc:
                    last_err = exc
                    url = None
            if url:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                tok = (q.get("token") or [""])[0]
                try:
                    st, _b = get(url.rstrip("/").split("?")[0] + "/api/version?token=" + tok)
                    if st == 200:
                        break
                except (urllib.error.URLError, OSError) as exc:
                    # 启动还没就绪时连不上是**正常**的 ⇒ 记下来（不静默），由下面的断言定夺
                    last_err = exc
            time.sleep(2)
        print("  启动用时 %.1fs" % (time.time() - t0))
        cfg_p = os.path.join(inner, "config.json")
        try:
            cfg = json.load(io.open(cfg_p, encoding="utf-8"))
        except (OSError, ValueError):
            cfg = None
        ok("① 首启自动生成 config.json", isinstance(cfg, dict), cfg_p)
        ok("② 自动生成控制台口令（≥16 位；空口令＝谁都能开控制台）",
           len(str(((cfg or {}).get("server") or {}).get("token") or "")) >= 16, "")
        ok("③ 写出了控制台地址（logs/console.url）", bool(url), str(url))

        base = (url or "").rstrip("/").split("?")[0]
        ver_ok = page_ok = False
        if base and tok:
            try:
                ver_ok = bool(json.loads(get(base + "/api/version?token=" + tok)[1]))
            except (urllib.error.URLError, OSError, ValueError):
                ver_ok = False
            try:
                st, page = get(base + "/?token=" + tok)
                page_ok = st == 200 and len(page) > 5000 and "<html" in page.lower()
            except (urllib.error.URLError, OSError):
                page_ok = False
        ok("④ 控制台能应答（带口令）", ver_ok, "")
        ok("⑤ 控制台页面能取到（首页 HTML）", page_ok, "")
        whale_ok = False
        whale_err = []
        if base and tok:
            for ep in ("/dsh-whale/size.json", "/dsh-whale/balance.json", "/dsh-whale/last-turn.json"):
                try:
                    if get(base + ep + "?token=" + tok)[0] == 200:
                        whale_ok = True
                        break
                except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
                    whale_err.append("%s: %s" % (ep, exc))   # 记下来（不静默）：三个都失败才判红
        ok("⑥ 挂件端点可达（/dsh-whale/*.json）", whale_ok, "；".join(whale_err[:2]))
        # 鉴权两个方向：探活口故意免认证；其它端点不带口令必须拒
        pub = False
        if base:
            try:
                pub = get(base + "/api/version")[0] == 200
            except (urllib.error.HTTPError, urllib.error.URLError, OSError):
                pub = False
        ok("⑦ `/api/version` 免认证可用（启动器认人靠它，故意公开、不含隐私）", pub, "")
        bad = []
        if base:
            for ep in ("/api/status", "/api/config"):
                try:
                    get(base + ep)
                    bad.append(ep + "=200")
                except urllib.error.HTTPError as exc:
                    if exc.code != 401:
                        bad.append("%s=%d" % (ep, exc.code))
                except (urllib.error.URLError, OSError) as exc:
                    bad.append("%s 连不上(%s)" % (ep, exc))
        ok("⑧ 受保护端点不带口令必须 401（反证口令闸真的生效）", not bad, "；".join(bad[:3]))
        try:
            ns = {}
            exec(io.open(os.path.join(inner, "agent", "version.py"), encoding="utf-8").read(), ns)
            print("  包内版本 %s（build %s）" % (ns.get("VERSION"), ns.get("BUILD")))
        except (OSError, NameError, SyntaxError) as exc:
            ver_err = exc   # 记下来（不静默）：包里的版本文件读不出来本身就该知道
            print("  读包内版本失败：%s" % ver_err)
    finally:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=NO_WINDOW)
        wait_to = False
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired as exc:
            wait_to = exc   # 记下来（不静默）：杀了 15 秒还没退 ⇒ 就是残留，下面的断言会判红
        fh.close()
        time.sleep(2.0)
        left, perr = copy_pids(root)
        if left is None:
            SKIP[0] += 1
            print("  SKIP 拿不到进程命令行（%s）⇒ 本次跳过『无残留进程』这条，不当失败" % str(perr)[:60])
        else:
            ok("没有残留的 python 进程（按命令行归属数：只数指向本次副本的）",
               not left and not wait_to,
               "残留 pid：%s%s" % ("、".join(sorted(left)[:4]),
                                   "（且 taskkill 后 15s 未退出）" if wait_to else ""))
    print("  副本目录（不自动删，供复盘）：%s" % root)
    print("== 首启冒烟判据：%d 通过 / %d 失败 / %d 跳过 ==" % (PASS[0], FAIL[0], SKIP[0]))
    return 0 if FAIL[0] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
