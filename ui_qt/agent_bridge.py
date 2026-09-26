# -*- coding: utf-8 -*-
"""原型与真实后台之间的唯一桥梁。

为什么要这个文件（而不是在 shell.py 里直接拼 URL）：
  `agent/notify_ui.py` 的 `console_url()` 里那段注释已经把坑讲透了 ——
  地址来源有**权威顺序**（`logs/console.url` → 配置兜底），并且带活性检查。
  原型必须**复用同一份逻辑**，否则我们测的是"原型自己的地址拼法"，
  不是产品真实会走的路径，验证就白做了。

⚠️ 但"复用"有个前提：本文件**只读**产品代码，绝不改。
   `agent/` 下一个字都不动（用户硬约束："后端一行都不许改"）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] # ui_qt → 项目根（落位自 _scratch/qt_proto，层级浅一级）


def _ensure_path() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def current_url() -> str:
    """拿控制台地址 —— 优先走产品自己的 `notify_ui.console_url()`。

    拿不到（比如产品代码正在改、import 失败）就退回读 `logs/console.url` 文件，
    再不行给配置里的默认端口。**任何一步都不抛异常** ——
    原型界面不能因为拿不到地址就崩，那本身就是我们要演示的"不许点了没反应"。
    """
    _ensure_path()
    try:
        from agent.notify_ui import console_url # noqa: PLC0415
        u = console_url()
        if u:
            return u
    except Exception:
        pass

    # 兜底 1：地址文件
    try:
        f = ROOT / "logs" / "console.url"
        if f.exists():
            s = f.read_text(encoding="utf-8", errors="replace").strip()
            if s.startswith("http"):
                return s
    except Exception:
        pass

    # 兜底 2：配置里的端口
    port = 3210
    try:
        import json # noqa: PLC0415
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8", errors="replace"))
        port = int((cfg.get("server") or {}).get("port") or 3210)
    except Exception:
        pass
    return "http://127.0.0.1:%d/" % port


def post_api(api: str, timeout: float = 5.0, base: str = "") -> tuple[bool, str]:
    """POST 一个后台 API（顶栏「重启」「停止」用：/api/restart、/api/shutdown）。

    地址走 current_url() 的权威口径（base 参数留给自测/复用注入），
    拼接走 addr.join_url（ #0 的教训：
    base 自带 ?token= 时手拼 `rstrip+"/"+path` 会把路径塞进 query → 401）。

    返回 (ok, 说明)。**连接在响应读完前被切断也算送达** ——
    /api/shutdown 的实现是响应一发出就写 stopped.flag + `os._exit(0)`，
    客户端几乎必然读不到完整响应（web 侧 assets/console/index.html 对同款行为
    早有注释）。所以按异常类型细分：
    远端主动断开（RemoteDisconnected 一族）= 请求已被后台处理，算成功；
    拒绝连接 = 服务没在跑，算失败。任何路径都不抛异常 ——
    按钮点了必须给个说法，不能无声无息。
    """
    from addr import join_url # noqa: PLC0415

    url = join_url(base or current_url(), api)
    try:
        import urllib.error # noqa: PLC0415
        import urllib.request as ur # noqa: PLC0415

        opener = ur.build_opener(ur.ProxyHandler({})) # 必须绕代理（同 heal 口径）
        req = ur.Request(
            url,
            data=b"",
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with opener.open(req, timeout=timeout) as r:
            return True, "HTTP %d" % getattr(r, "status", 200)
    except urllib.error.HTTPError as e:
        # 4xx/5xx = 请求被拒/处理失败，动作没有执行 —— 一律不算送达
        # （尤其 401：token 不对，restart/shutdown 根本没发生，别骗）
        return False, "HTTP %d（被拒绝）" % e.code
    except Exception as e: # noqa: BLE001
        # urllib 会把多数网络错包进 URLError（.reason 是根因）；握手前的
        # ConnectionResetError 等则直接抛。先拿两头的类型名再归类。
        reason = getattr(e, "reason", e)
        names = {type(e).__name__, type(reason).__name__}
        if "ConnectionRefusedError" in names:
            return False, "没连上（后台没在跑）"
        # 远端处理完请求后主动断线：只有请求已到达并被处理才可能出现
        if names & {"RemoteDisconnected", "IncompleteRead", "BadStatusLine",
                    "ConnectionResetError", "BrokenPipeError"}:
            return True, "已送达（后台退出切断了响应）"
        if names & {"TimeoutError", "socket.timeout"}:
            # 本机实测（Windows）：连未监听端口多半不是 refused，而是 SYN 被静默
            # drop → 超时。restart/shutdown 的 POST 正常毫秒级返回，拖满超时
            # 基本就是没人听 —— 对用户如实说「没连上」，别误报「后台在忙」。
            return False, "没连上（后台没回应）"
        return False, "%s: %s" % (type(e).__name__, str(reason)[:80])


def post_json(api: str, body: dict | None = None, timeout: float = 8.0,
              base: str = "") -> dict | None:
    """POST 一个后台 API 并读回 JSON 响应（ #13 更新条用：/api/update_apply、
    /api/update_skip）。与 post_api 的差别：需要**响应体** —— update_apply 的
    {"ok": false, "why": …}、update_skip 的回执都要逐字给用户看，不能只给"送达"。

    地址与拼接口径同 post_api。
    返回 dict；连接层失败（没连上/超时）返回 None —— 调用方如实显示，别骗人。
    HTTPError 时尝试解析错误响应体（后端 500 也带 {"ok":false,"why"}）。
    """
    from addr import join_url # noqa: PLC0415

    url = join_url(base or current_url(), api)
    try:
        import json as _j # noqa: PLC0415
        import urllib.error # noqa: PLC0415
        import urllib.request as ur # noqa: PLC0415

        payload = _j.dumps(body or {}).encode("utf-8")
        opener = ur.build_opener(ur.ProxyHandler({})) # 必须绕代理（heal/post_api 同款）
        req = ur.Request(
            url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with opener.open(req, timeout=timeout) as r:
            return _j.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            import json as _j2 # noqa: PLC0415

            return _j2.loads(e.read().decode("utf-8", "replace"))
        except Exception: # noqa: BLE001
            return {"ok": False, "why": "HTTP %d（被拒绝）" % e.code}
    except Exception as e: # noqa: BLE001
        reason = getattr(e, "reason", e)
        return None if "ConnectionRefusedError" in {
            type(e).__name__, type(reason).__name__} else {
            "ok": False, "why": "%s: %s" % (type(e).__name__, str(reason)[:80])}


def console_process_alive() -> tuple[bool, str]:
    """本机有没有"群相"的 Python 进程在跑。

    这是**区分两种情况的关键**，`probe_backend()` 单靠自己做不到：
      · 「服务死了」      ← 端口没人听 + **进程也没了**  ⇒ 能自愈（自己拉起来）
      · 「服务还没起来」  ← 端口没人听 + **进程还在**    ⇒ 只需等
    两种都是 `ConnectionRefused`，只有进程表能分开。
    """
    try:
        import subprocess # noqa: PLC0415
        out = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "CommandLine"],
            capture_output=True, text=True, timeout=4,
            creationflags=0x08000000,
        ).stdout or ""
    except Exception as e: # noqa: BLE001
        return False, "查不到进程（%s）" % type(e).__name__
    # 判据：命令行里同时出现 persona_morph 与 .py —— 避免把无关 python 进程算进来
    hits = [ln for ln in out.splitlines() if "persona_morph" in ln and ".py" in ln]
    if hits:
        return True, hits[0].strip()[:120]
    return False, ""


if __name__ == "__main__":
    u = current_url()
    print("地址：", u.split("?")[0] + ("?token=…" if "?" in u else ""))
    alive, info = console_process_alive()
    print("进程活着：", alive, ("  " + info) if info else "")
