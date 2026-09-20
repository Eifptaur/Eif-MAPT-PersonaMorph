# -*- coding: utf-8 -*-
"""本地生图后端「一条龙」：**查状态 → 测速估时 → 下载安装 → 起服务 → 自动接进 image_gen**。

用户口径（2026-09-17 原话）：「**你可以帮用户装，做成一个可选项。用户选了之后，就弹出一个安装提示，
帮他安装。至于走在线安装，就看用户开不开吧**」＋「**不要让用户搞这搞那的操作**」＋
「**如果要提示用户需要下载，你必须要写明时间可能会非常长**（按本次实测数字算）」。
⇒ 三条硬规矩：
  ①**默认不下载**（`image_gen.local_sd.allow_online_install` 默认 **false**）——没开就只说明原因，绝不偷偷联网；
  ②**弹窗里必须给"要下多少 + 预计多久"**，而预计必须来自**当场测速**（本机实测：走国内源 4.5~25 MB/s；
     走到被限速的源只有 0.02~0.06 MB/s，那会变成几十小时 ⇒ 要如实劝退并说明可换源）；
  ③装完**自动配好**（写 `image_gen.backends` + 打开开关 + 起服务），用户不用再填任何地址。

本次实测（2026-09-17，本机 RTX 5070 Ti Laptop）：
  · 模型 `sd_xl_turbo_1.0_fp16.safetensors` **6.46 GB**（ModelScope 25 MB/s ≈ 4.5 分钟）
  · 运行库（torch 4.20 GB + torchvision/diffusers/transformers/safetensors ≈ 0.15 GB）**≈ 4.35 GB**（SJTU 4.5 MB/s ≈ 8 分钟）
  · **合计约 10.8 GB；加上解压/安装/首次加载，整条链约 20 分钟**（这正是用户要求写进提示的数字）
  · 首次加载模型进显存 **14.7 秒**；出图 1024² 四步 **13~33 秒**
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

from .config import ROOT, get_config, save_config

MODEL_NAME = "sd_xl_turbo_1.0_fp16.safetensors"
#: 模型下载源（**先国内**：本机实测 ModelScope 5~25 MB/s，hf-mirror/GitHub 只有 0.02~0.06）
MODEL_SOURCES = (
    ("ModelScope（国内，推荐）", "https://www.modelscope.cn/models/AI-ModelScope/sdxl-turbo/resolve/master/" + MODEL_NAME),
    ("hf-mirror（国内镜像，慢）", "https://hf-mirror.com/stabilityai/sdxl-turbo/resolve/main/" + MODEL_NAME),
)
#: pip 源（torch 走 SJTU 的 cu128 轮子镜像；其余走阿里云）
TORCH_INDEX = "https://mirror.sjtu.edu.cn/pytorch-wheels/cu128/"
PYPI_INDEX = "https://mirrors.aliyun.com/pypi/simple/"
MODEL_GB = 6.46
DEPS_GB = 4.35
#: 失败重试的最小可接受速度（MB/s）：低于它就别下了，如实劝退
MIN_MBPS = 0.5

# ——————————————— 模型档位（用户口径 2026-09-17：「**不二选一**」）———————————————
#   用户原话：「搞半天，质量和速度不能都要啊…你就非得二选一是啥意思？」
#   ⇒ 速度与画质**都给**，两个档并存：装哪个、用哪个由用户在控制台挑，不许替他砍掉一条路。
#   关键事实（查证过）：画质来自"更大的模型/更好的精调"，速度来自"蒸馏加速（少步数）"，
#   这两件在 2024-2026 已经能合在同一个组合里 —— 画质档＝SDXL 精调 checkpoint + 4 步加速 LoRA。
PRESETS = (
    {
        "id": "quality",
        "label": "画质档 · Animagine XL 3.1 + 4 步加速",
        "file": "animagine-xl-3.1.safetensors",
        "gb": 6.46,
        "lora_file": "lora/sdxl_lightning_4step_lora.safetensors",
        "lora_gb": 0.37,
        "steps": 4,
        "guidance": 2.0,
        "license": "Animagine XL 3.1（OpenRAIL++）· SDXL-Lightning LoRA（OpenRAIL++）",
        "note": "画质明显好于速度档（动画/插画向），仍然只跑 4 步 —— 速度和质量一起要",
        "sources": (("ModelScope（国内，实测 5~25 MB/s）",
                     "https://www.modelscope.cn/models/cagliostrolab/animagine-xl-3.1/resolve/master/animagine-xl-3.1.safetensors"),),
        "lora_sources": (("ModelScope（国内）",
                          "https://www.modelscope.cn/models/AI-ModelScope/SDXL-Lightning/resolve/master/sdxl_lightning_4step_lora.safetensors"),),
    },
    {
        "id": "speed",
        "label": "速度档 · SDXL-Turbo",
        "file": MODEL_NAME,
        "gb": MODEL_GB,
        "lora_file": "",
        "lora_gb": 0.0,
        "steps": 4,
        "guidance": 0.0,
        "license": "Stability AI 社区许可（非商用）",
        "note": "最快、最省显存；画质一般（这是原来的默认档）",
        "sources": MODEL_SOURCES,
        "lora_sources": (),
    },
)
DEFAULT_PRESET = "quality"


def preset(pid: str = "") -> dict:
    """按 id 取档位定义（找不到就回默认档，绝不抛）。"""
    want = str(pid or "").strip().lower()
    for p in PRESETS:
        if p["id"] == want:
            return p
    for p in PRESETS:
        if p["id"] == DEFAULT_PRESET:
            return p
    return PRESETS[0]


def active_id() -> str:
    return str(_cfg().get("preset") or DEFAULT_PRESET)

# ——————————————— 实时进度（控制台要轮询它画进度条）———————————————
#   ⚠️ 用户 2026-09-17 明确要求：「要能让用户实时看到下载进度啊，就是一共多少，现在下了多少？百分比是多少」
#   ⇒ 这里维护一份"当前在干什么 + 已下/总量/百分比/速度/预计剩余"，webui 用 `/api/image_gen/local/progress` 读它。
_PROG = {
    "running": False, "phase": "idle", "message": "", "done_bytes": 0, "total_bytes": 0,
    "percent": 0.0, "mbps": 0.0, "eta_seconds": 0, "started_at": 0.0, "finished_at": 0.0,
    "ok": None, "error": "",
}
#: 各阶段在总进度里占的权重（按体积：模型 6.46 / 依赖 4.35）
_W_MODEL, _W_DEPS, _W_FINISH = 0.60, 0.38, 0.02


def _set_prog(**kw):
    _PROG.update(kw)


def progress() -> dict:
    """当前安装/下载进度（**给控制台轮询**）⇒ 含 percent / done_bytes / total_bytes / mbps / eta_seconds。"""
    p = dict(_PROG)
    if p.get("total_bytes"):
        p["percent"] = round(100.0 * p["done_bytes"] / max(1, p["total_bytes"]), 1)
    return p


def _reset_prog():
    total_all = int((MODEL_GB + DEPS_GB) * 1073741824)
    _set_prog(running=True, phase="start", message="准备中…", done_bytes=0, total_bytes=total_all,
              percent=0.0, mbps=0.0, eta_seconds=0, started_at=time.time(), finished_at=0.0,
              ok=None, error="")


def _cfg() -> dict:
    try:
        c = (get_config().get("image_gen") or {}).get("local_sd") or {}
    except Exception:
        c = {}
    return {"enabled": True, "port": 7860, "model_dir": os.path.join("data", "sd_model"),
            "allow_online_install": False, "auto_start": True, "steps": 4, **(c or {})}


def _model_dir() -> str:
    c = _cfg()
    d = str(c.get("model_dir") or os.path.join("data", "sd_model"))
    return d if os.path.isabs(d) else os.path.join(ROOT, d)


def model_path(pid: str = "") -> str:
    return os.path.join(_model_dir(), preset(pid or active_id())["file"])


def lora_path(pid: str = "") -> str:
    p = preset(pid or active_id())
    lf = str(p.get("lora_file") or "")
    return os.path.join(_model_dir(), lf) if lf else ""


def preset_gb(pid: str = "") -> float:
    p = preset(pid or active_id())
    return round(float(p.get("gb") or 0) + float(p.get("lora_gb") or 0), 2)


def _file_ok(path: str, gb: float) -> bool:
    """文件"算不算下好了"：**按体积判**（≥ 声明体积的 97%）。只看 exists() 会把下到一半的
    文件当成已安装 —— 那样控制台会显示"已装好"，实际一加载就废（.part 只用于下载续传）。"""
    try:
        return bool(path) and os.path.exists(path) and os.path.getsize(path) >= int(float(gb) * 1073741824 * 0.97)
    except Exception:
        return False


def presets() -> list:
    """所有档位 + 各自"装了没有 / 是不是当前用的"（控制台据此画选择器）。"""
    act = active_id()
    out = []
    for p in PRESETS:
        mp = os.path.join(_model_dir(), p["file"])
        lp = lora_path(p["id"])
        ok_model = _file_ok(mp, float(p.get("gb") or 0))
        ok_lora = True if not lp else _file_ok(lp, float(p.get("lora_gb") or 0))
        out.append({"id": p["id"], "label": p["label"], "note": p["note"], "license": p["license"],
                    "gb": preset_gb(p["id"]), "steps": int(p.get("steps") or 4),
                    "guidance": float(p.get("guidance") or 0.0),
                    "installed": bool(ok_model and ok_lora),
                    "active": p["id"] == act})
    return out


def set_preset(pid: str) -> tuple:
    """切档：写配置 + （服务在跑就）重启服务让它按新档加载。返回 (ok, 说明)。"""
    p = preset(pid)
    try:
        cfg = get_config()
        ig = cfg.setdefault("image_gen", {})
        sd = ig.setdefault("local_sd", {})
        sd["preset"] = p["id"]
        sd["steps"] = int(p.get("steps") or 4)
        save_config(cfg)
    except Exception as e:                                   # noqa: BLE001
        return False, "写配置失败：%s" % str(e)[:80]
    if server_alive():
        stop_server()
        ok2, why2 = start_server()
        return ok2, "已切到「%s」；%s" % (p["label"], why2)
    return True, "已切到「%s」（服务没在跑，下次启动按这一档加载）" % p["label"]


def _pidfile() -> str:
    return os.path.join(ROOT, "data", "sd_local.pid")


def server_url(port: int = None) -> str:
    return "http://127.0.0.1:%d" % int(port or _cfg().get("port") or 7860)


# ——————————————— 状态 ———————————————
def _deps_ok() -> tuple:
    try:
        import torch                                    # noqa: F401
        import diffusers                                # noqa: F401
        return True, ""
    except Exception as e:                              # noqa: BLE001
        return False, "运行库没装（torch/diffusers）：%s" % str(e)[:60]


def server_alive(port: int = None) -> bool:
    """探活：本机服务现在**要口令**（V-R3-8），所以这里也得带上它（与客户端同一份实现）。"""
    from . import local_guard as lg
    u = server_url(port) + "/internal/ping"
    try:
        req = urllib.request.Request(u, headers=lg.client_headers(u, {"User-Agent": "PersonaMorph/probe"}))
        with urllib.request.urlopen(req, timeout=1.2) as r:
            return int(getattr(r, "status", 200) or 200) < 500
    except Exception:
        return False


def service_gated(port: int = None) -> tuple:
    """`7860` 上跑着的服务，**是"我们这一版（带门禁）"的吗**？返回 `(ok, why)`。

    ⛔ 2026-09-21（第四轮审计 **V-R4-3，P1**）：原来只判"有人答话"（`server_alive`）⇒
    机器上 09-19 起的**旧无门禁实例**（无 Host / 错口令一律 200）会被**一直复用**，
    **更新产品也不会换掉它** ⇒ 门禁代码在仓库里"修好了"，**活体从来没生效**。
    ⇒ 发**两个真请求**（缺一不可）：
      ①**反证**：不带口令 + **外域 Host** 打 `/internal/ping` —— 带门禁的服务必须**拒**（401/403）；
         若照样 200 ⇒ 那就是旧实例 ⇒ `(False, "跑着的是没有门禁的旧实例")`；
      ②**正证**：带口令 + 回环 Host —— 必须 200，且响应里声明 `gate` 标记（旧版没有这个字段）。
    """
    from . import local_guard as _lg
    u = server_url(port) + "/internal/ping"
    # ① 反证：故意不带口令、并用外域 Host
    try:
        _req_bad = urllib.request.Request(
            u, headers={"Host": "evil.example:%d" % int(port or _cfg().get("port") or 7860),
                        "User-Agent": "PersonaMorph/gatecheck"})
        with urllib.request.urlopen(_req_bad, timeout=1.5) as _r:
            if int(getattr(_r, "status", 200) or 200) < 400:
                return False, ("跑着的是**没有门禁的旧实例**：不带口令 + 外域 Host 也照样回 200"
                               "（这一版的代码带 Host+口令门禁，说明它不是我起的）")
    except urllib.error.HTTPError as _e:
        if int(getattr(_e, "code", 0) or 0) not in (401, 403):
            return False, ("服务对「无口令 / 外域 Host」的回应不是 401/403，而是 %s ⇒ 认不出它"
                           % getattr(_e, "code", "?"))
    except Exception as _e:
        return False, "连不上或读不到回应：%s" % str(_e)[:60]
    # ② 正证：带口令 + 回环 Host，并核对 `gate` 声明
    try:
        _req_ok = urllib.request.Request(u, headers=_lg.client_headers(
            u, {"User-Agent": "PersonaMorph/gatecheck"}))
        with urllib.request.urlopen(_req_ok, timeout=1.5) as _r:
            _body = _r.read(2000).decode("utf-8", "ignore")
        if '"gate"' in _body:
            return True, ""
        return False, "服务认了口令，但响应里没有 `gate` 声明（像是新老之间的版本）"
    except Exception as _e:
        return False, "带口令也读不到门禁声明：%s" % str(_e)[:60]


def _port_owner_pid(port: int):
    """谁在听这个端口？（解析 `netstat -ano`；查不到返回 None）"""
    try:
        r = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        for line in str(getattr(r, "stdout", "") or "").splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            if parts[1].endswith(":%d" % int(port)) and parts[3].upper() == "LISTENING":
                return int(parts[4])
    except Exception:
        pass
    return None


def kill_stale_owner(port: int) -> tuple:
    """把占用 `port` 的**我们自己旧实例**停掉；**只在我们能证明它是我们的时才动手**。

    证据链（两条都要成立）：①`data/sd_local.pid` 里记的 pid 就是**听这个端口的那个进程**；
    ②该进程还在。取不到证据 ⇒ **不杀**（宁可让用户手动处理，也不误杀别人跑在 7860 上的东西）。
    """
    try:
        pid = int(open(_pidfile(), encoding="utf-8").read().strip())
    except Exception:
        return False, "没有 pidfile 记录（无法证明那是我起的 ⇒ 不替你杀）"
    owner = _port_owner_pid(port)
    if owner is None:
        return False, "查不到端口占用者"
    if int(owner) != int(pid):
        return False, ("占着 %d 的是 PID %s，而 pidfile 记的是 PID %s ⇒ **不是我们起的** ⇒ 不替你杀"
                       % (int(port), owner, pid))
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                       creationflags=0x08000000 if os.name == "nt" else 0)
        time.sleep(0.6)
        return True, "已停掉旧实例（PID %d）" % pid
    except Exception as e:                                   # noqa: BLE001
        return False, "停旧实例失败：%s" % str(e)[:60]


def status() -> dict:
    c = _cfg()
    pid = active_id()
    p = preset(pid)
    mp = model_path()
    dok, dwhy = _deps_ok()
    mok = os.path.exists(mp)
    alive = server_alive(c.get("port"))
    # ⛔ V-R4-3：**"有人答话"不等于"是我们这一版的实例"** —— 旧无门禁实例必须被认出来
    gated, gwhy = (service_gated(c.get("port")) if alive else (False, ""))
    why = ""
    if not dok:
        why = dwhy
    elif not mok:
        why = "「%s」的模型还没下（要下 %.2f GB）" % (p["label"], preset_gb())
    elif not alive:
        why = "模型在，但本地服务没在跑（可以一键启动）"
    elif not gated:
        why = ("7860 上跑着的是**没有门禁的旧实例** ⇒ 点「一键启动」会换掉它（%s）" % str(gwhy)[:80])
    return {"ok": bool(dok and mok and alive and gated), "why": why, "deps_ok": dok, "model_ok": mok,
            "model_path": mp, "model_gb": preset_gb(), "deps_gb": DEPS_GB,
            "server_alive": alive, "gated": bool(gated), "gate_why": str(gwhy)[:120],
            "port": int(c.get("port") or 7860),
            "allow_online_install": bool(c.get("allow_online_install")),
            "switch": bool(c.get("enabled")), "auto_start": bool(c.get("auto_start")),
            "installed": bool(dok and mok),
            "preset": pid, "preset_label": p["label"], "preset_note": p["note"],
            "presets": presets()}


# ——————————————— 测速与估时（用户要求：必须写明"可能要很久"）———————————————
def probe_speed(url: str, bytes_to_read: int = 3 * 1024 * 1024, timeout: float = 20.0) -> float:
    """取前几 MB 估一下真实速度（MB/s）。失败返回 0。"""
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                                   "Range": "bytes=0-%d" % max(65535, bytes_to_read - 1)})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = r.read(bytes_to_read)
        dt = max(0.05, time.time() - t0)
        return len(d) / 1048576.0 / dt
    except Exception:
        return 0.0


def estimate(allow_online: bool = None, do_probe: bool = True, pid: str = "") -> dict:
    """估算「要下多少、多久」⇒ {gb, mbps, seconds, human, source, warn}。

    速度取**当场测速**（拿该档第一个模型源实测）；测不到就按本次实测的保守值 4.5 MB/s 算，
    并在 `warn` 里说明"这是按国内源的保守值估的"。
    """
    c = _cfg()
    p = preset(pid or active_id())
    if allow_online is None:
        allow_online = bool(c.get("allow_online_install"))
    total = preset_gb(p["id"]) + DEPS_GB
    src = p["sources"][0][0]
    mbps = 0.0
    if do_probe:
        for name, u in p["sources"]:
            sp = probe_speed(u)
            if sp > 0:
                mbps, src = sp, name
                break
    warn = ""
    if mbps <= 0:
        mbps = 4.5
        warn = "没测到速度，按本次实测的国内源保守值 4.5 MB/s 估算。"
    if mbps < MIN_MBPS:
        warn = ("⚠️ 实测只有 %.2f MB/s —— 这个源被限速了，按这个速度要 %.0f 小时，"
                "**强烈建议先换网络/关掉代理再试**（本次实测：走国内源 4.5~25 MB/s，"
                "被限速的源只有 0.02~0.06 MB/s）。" % (mbps, total * 1024 / mbps / 3600))
    secs = int(total * 1024 / max(0.05, mbps))
    human = ("约 %d 分钟" % max(1, secs // 60)) if secs < 7200 else ("约 %.1f 小时" % (secs / 3600.0))
    return {"gb": round(total, 2), "model_gb": preset_gb(p["id"]), "deps_gb": DEPS_GB,
            "mbps": round(mbps, 2), "source": src, "seconds": secs, "human": human,
            "allow_online": bool(allow_online), "warn": warn,
            "preset": p["id"], "preset_label": p["label"], "license": p["license"],
            "note": "下载在后台进行，不影响你用微信/控制台；失败可续传。"}


# ——————————————— 安装 ———————————————
def _download(url: str, dest: str, on_log=None, on_prog=None) -> tuple:
    """带续传与进度回调的下载 ⇒ (ok, 说明)。`on_prog(done_bytes, total_bytes, mbps)` 每 1 秒回一次。"""
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    have = os.path.getsize(dest) if os.path.exists(dest) else 0
    hdr = {"User-Agent": "Mozilla/5.0"}
    if have:
        hdr["Range"] = "bytes=%d-" % have
    try:
        req = urllib.request.Request(url, headers=hdr)
        with urllib.request.urlopen(req, timeout=60) as r:
            if have and int(getattr(r, "status", 200) or 200) == 200:
                have = 0                                     # 服务器不支持续传
            total = int(r.headers.get("Content-Length") or 0) + have
            got, t0, last = have, time.time(), 0.0
            with open(dest, "ab" if have else "wb") as f:
                while True:
                    chunk = r.read(1024 * 512)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    now = time.time()
                    if now - last >= 1.0:
                        last = now
                        sp = (got - have) / 1048576.0 / max(0.1, now - t0)
                        if on_prog:
                            on_prog(got, total, sp)
                        if on_log:
                            on_log("已下 %.2f / %.2f GB ｜ %.1f MB/s" % (got / 1073741824.0,
                                                                        total / 1073741824.0, sp))
        if total and abs(got - total) > 2 * 1048576:
            return False, "没下全（%.2f GB / %.2f GB）" % (got / 1073741824.0, total / 1073741824.0)
        return True, "已下 %.2f GB" % (os.path.getsize(dest) / 1073741824.0)
    except Exception as e:                                   # noqa: BLE001
        return False, "下载失败：%s" % str(e)[:80]


def _pip_install(args: list, on_log=None) -> tuple:
    exe = os.path.join(ROOT, "runtime", "python", "python.exe")
    if not os.path.exists(exe):
        exe = sys.executable
    cmd = [exe, "-m", "pip", "install", "--disable-pip-version-check"] + args
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=3600, creationflags=0x08000000 if os.name == "nt" else 0)
        tail = (p.stdout or "").strip().splitlines()[-1:] or [""]
        if p.returncode != 0:
            return False, "pip 失败：%s" % ((p.stderr or p.stdout or "")[-160:])
        if on_log:
            on_log(tail[0][:120])
        return True, tail[0][:120]
    except Exception as e:                                   # noqa: BLE001
        return False, "pip 跑不动：%s" % str(e)[:80]


def install(allow_online: bool = None, on_log=None, skip_deps: bool = False, pid: str = "") -> tuple:
    """一条龙安装：**先检查开关** → 测速告知 → 下模型（+加速件）→ 装运行库 → 自动配好并起服务。

    返回 `(ok, 说明, info)`。**未开在线安装时直接拒绝**（不偷偷联网）。
    **全程更新 `progress()`**：阶段 + 已下/总量/百分比/速度/预计剩余，控制台可实时画进度条。
    `pid`＝档位 id（画质档/速度档），默认用当前档。
    """
    c = _cfg()
    p = preset(pid or active_id())
    if allow_online is None:
        allow_online = bool(c.get("allow_online_install"))
    if not allow_online:
        return False, ("需要在控制台把「允许联网下载」打开我才能装（默认关，不偷偷联网）。"
                       "「%s」要下 %.2f GB。" % (p["label"], preset_gb(p["id"]))), {}
    log = on_log or (lambda s: None)
    mp = model_path(p["id"])
    lp = lora_path(p["id"])
    gb_model = float(p.get("gb") or 0)
    gb_all = preset_gb(p["id"])
    info = {"model_path": mp, "preset": p["id"], "lora_path": lp}
    _reset_prog()
    total_all = int((gb_all + DEPS_GB) * 1073741824)

    def _prog_model(done, total, mbps):
        # 模型阶段占总进度 60%
        done_all = int(done * (_W_MODEL * (gb_all + DEPS_GB) / max(0.01, gb_all)))
        _set_prog(phase="model", done_bytes=done_all, total_bytes=total_all, mbps=round(mbps, 2),
                  eta_seconds=int((total_all - done_all) / max(0.05, mbps * 1048576)),
                  message="正在下载模型（%.2f / %.2f GB）" % (done / 1073741824.0, total / 1073741824.0))

    try:
        _set_prog(phase="probe", message="先测一下你的网速，好告诉你大概要多久…")
        est = estimate(do_probe=True, pid=p["id"])
        log("预计：%.2f GB，%s（实测 %.2f MB/s 走 %s）" % (est["gb"], est["human"], est["mbps"], est["source"]))
        if est["mbps"] < MIN_MBPS:
            _set_prog(running=False, ok=False, error=est["warn"], finished_at=time.time())
            return False, est["warn"], {"estimate": est}
        if not os.path.exists(mp):
            okdl = False
            for name, url in p["sources"]:
                _set_prog(phase="model", message="从 %s 下模型…" % name)
                log("从 %s 下模型（%.2f GB）…" % (name, gb_model))
                okdl, why = _download(url, mp, on_log=log, on_prog=_prog_model)
                if okdl:
                    info["downloaded_from"] = name
                    break
                log("这个源不行（%s），换下一个" % why)
            if not okdl:
                _set_prog(running=False, ok=False, error=why, finished_at=time.time())
                return False, "模型没下来：%s" % why, info
        else:
            info["model_reused"] = True
            log("模型已存在，跳过下载")
        if lp and not os.path.exists(lp):                       # 加速件（画质档要它才跑得动 4 步）
            for name, url in (p.get("lora_sources") or ()):
                _set_prog(phase="model", message="从 %s 下加速件（4 步）…" % name)
                log("下加速件（%.2f GB）…" % float(p.get("lora_gb") or 0))
                okL, whyL = _download(url, lp, on_log=log)
                if okL:
                    break
                log("加速件这个源不行（%s），换下一个" % whyL)
            if not os.path.exists(lp):
                _set_prog(running=False, ok=False, error="加速件没下来", finished_at=time.time())
                return False, "加速件没下来（画质档需要它把步数压到 4 步）", info
        if not skip_deps:
            dok, _ = _deps_ok()
            if not dok:
                base = int(_W_MODEL * total_all)
                _set_prog(phase="deps", done_bytes=base, message="正在安装运行库（torch/diffusers，约 4.3 GB）…")
                log("装运行库（约 4.3 GB，走国内镜像）…")
                ok2, why2 = _pip_install(["-i", TORCH_INDEX, "torch", "torchvision"],
                                         on_log=lambda s: log(s))
                if not ok2:
                    _set_prog(running=False, ok=False, error=why2, finished_at=time.time())
                    return False, why2, info
                _set_prog(done_bytes=int(base + _W_DEPS * total_all * 0.6), message="正在安装 diffusers/transformers…")
                ok3, why3 = _pip_install(["-i", PYPI_INDEX, "diffusers", "transformers", "accelerate", "safetensors"],
                                         on_log=lambda s: log(s))
                if not ok3:
                    _set_prog(running=False, ok=False, error=why3, finished_at=time.time())
                    return False, why3, info
        # 自动配好：写 backends + 打开开关 + 起服务
        _set_prog(phase="config", done_bytes=int(0.98 * total_all), message="正在写入配置…")
        try:
            cfg = get_config()
            ig = cfg.setdefault("image_gen", {})
            cur = str(ig.get("backends") or "").strip()
            entry = "local-sd | local | %s | a1111" % server_url()
            if "local-sd" not in cur:
                ig["backends"] = (cur + ";" + entry) if cur else entry
            ig["enabled"] = True
            ls = ig.setdefault("local_sd", {})
            ls["enabled"] = True
            ls["allow_online_install"] = True
            ls["preset"] = p["id"]                      # 当前档（速度/画质由用户挑）
            ls["steps"] = int(p.get("steps") or 4)
            save_config(cfg)
            info["config"] = "已写入 image_gen.backends、档位＝%s、并打开开关" % p["label"]
        except Exception as e:                               # noqa: BLE001
            info["config"] = "配置写回失败（%s）" % str(e)[:60]
        _set_prog(phase="start", done_bytes=int(0.99 * total_all), message="正在启动本地生图服务（首次加载模型约 15 秒）…")
        ok4, why4 = start_server()
        info["server"] = why4
        _set_prog(running=False, ok=bool(ok4), phase="done",
                  done_bytes=total_all if ok4 else int(0.99 * total_all),
                  message=why4, finished_at=time.time(), error="" if ok4 else why4)
        return bool(ok4), ("装好了，本地服务已起：%s" % why4) if ok4 else why4, info
    except Exception as e:                                   # noqa: BLE001
        _set_prog(running=False, ok=False, error=str(e)[:120], finished_at=time.time())
        return False, "安装中断：%s" % str(e)[:100], info


def install_async(allow_online: bool = None, on_log=None, pid: str = "") -> tuple:
    """**后台安装**（用户口径：「要加那个按键，也就是后台加载，让用户可以不看着弹窗等它加载」）。

    起一条 daemon 线程跑 `install()`，立刻返回；进度用 `progress()` 轮询。
    已经在装 ⇒ 不重复起（返回 running=True 让界面继续显示那条进度）。
    """
    import threading
    p = preset(pid or active_id())
    if _PROG.get("running"):
        return True, "已经在装了（进度见 progress）", {"running": True}
    if not bool((allow_online if allow_online is not None else _cfg().get("allow_online_install"))):
        return False, ("需要在控制台把「允许联网下载」打开我才能装（默认关，不偷偷联网）。"
                       "「%s」要下 %.2f GB。" % (p["label"], preset_gb(p["id"]))), {}

    def _run():
        try:
            install(allow_online=True, on_log=on_log, pid=p["id"])
        except Exception as e:                               # noqa: BLE001
            _set_prog(running=False, ok=False, error="安装线程异常：%s" % str(e)[:120], finished_at=time.time())

    t = threading.Thread(target=_run, name="sd_local_install", daemon=True)
    t.start()
    return True, "已在后台开始安装「%s」（关掉这个弹窗也行，它会继续下）" % p["label"], {"running": True}


# ——————————————— 起停服务 ———————————————
def start_server(on_log=None) -> tuple:
    c = _cfg()
    p = preset(active_id())
    port = int(c.get("port") or 7860)
    if server_alive(port):
        # ⛔ V-R4-3：**先问"是不是我们这一版（带门禁）的实例"**，不是 ⇒ 换掉它（能证明是我们起的才动手）
        _g_ok, _g_why = service_gated(port)
        if _g_ok:
            return True, "本地服务已经在跑（%s）" % server_url(port)
        _k_ok, _k_why = kill_stale_owner(port)
        if not _k_ok:
            return False, ("7860 上跑着的服务**不认口令**（旧版本留下的实例），本回合不换它：%s"
                           "（%s）⇒ 请手动关掉它，或在控制台把端口改一个。" % (_k_why, str(_g_why)[:80]))
        if on_log:
            try:
                on_log("已停掉旧的无门禁实例：%s" % _k_why)
            except Exception:
                pass
    exe = os.path.join(ROOT, "runtime", "python", "python.exe")
    if not os.path.exists(exe):
        exe = sys.executable
    srv = os.path.join(ROOT, "agent", "sd_local_server.py")
    env = dict(os.environ)
    # V-R3-8：父进程先把口令定下来，**同一个值**通过环境变量交给子进程（两边读的也是同一个文件）
    from . import local_guard as _lg
    env[_lg.ENV_KEY] = _lg.token()
    env["SD_MODEL"] = model_path()
    env["SD_STEPS"] = str(int(c.get("steps") or p.get("steps") or 4))
    env["SD_GUIDANCE"] = str(float(p.get("guidance") or 0.0))
    _lp = lora_path()
    env["SD_LORA"] = _lp if (_lp and os.path.exists(_lp)) else ""
    env["SD_SPACING"] = "trailing" if env["SD_LORA"] else "leading"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        logf = open(os.path.join(ROOT, "logs", "sd_local.log"), "ab")
    except Exception:
        logf = subprocess.DEVNULL
    try:
        p = subprocess.Popen([exe, "-u", srv, str(port)], cwd=ROOT, env=env,
                             stdout=logf, stderr=subprocess.STDOUT,
                             creationflags=0x08000000 if os.name == "nt" else 0)
        with open(_pidfile(), "w", encoding="utf-8") as f:
            f.write(str(p.pid))
        for _ in range(60):                                  # 最多等 60 秒（首次要加载模型）
            time.sleep(1.0)
            if server_alive(port):
                return True, "本地服务已就绪（%s）" % server_url(port)
        return False, "服务起了但 60 秒内没就绪（看 logs\\sd_local.log）"
    except Exception as e:                                   # noqa: BLE001
        return False, "起服务失败：%s" % str(e)[:80]


def stop_server() -> tuple:
    try:
        pid = int(open(_pidfile(), encoding="utf-8").read().strip())
    except Exception:
        return True, "没有记录在案的本地服务进程"
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                       creationflags=0x08000000 if os.name == "nt" else 0)
        return True, "已停止本地服务（PID %d）" % pid
    except Exception as e:                                   # noqa: BLE001
        return False, "停止失败：%s" % str(e)[:60]


def ensure_running() -> tuple:
    """给 `image_gen.pick_backend()` 用：装了、开了自启、但服务没跑 ⇒ 顺手拉起来。"""
    c = _cfg()
    st = status()
    if not (st["deps_ok"] and st["model_ok"]):
        return False, st["why"]
    if st["server_alive"] and st.get("gated"):
        return True, "已在跑"
    if st["server_alive"] and not st.get("gated"):
        # ⛔ V-R4-3：端口被**旧的无门禁实例**占着 ⇒ 自愈（换掉它），别让它永久挡着
        return start_server()
    if not c.get("auto_start", True):
        return False, "本地服务没在跑，且自启关着"
    return start_server()
