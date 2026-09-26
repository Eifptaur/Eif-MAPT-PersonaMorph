# -*- coding: utf-8 -*-
"""控制台地址解析 —— Qt 侧唯一权威实现（照抄 agent/notify_ui.py 的口径）。

为什么必须有这个模块：
  heal.py 的 probe_backend(base) 只管「探一个给定地址」，不管「地址从哪来」。
  而 agent 侧的地址来源有**一个权威顺序**（
  起因与教训全记录在 agent/notify_ui.py:251-296 的 console_url()）：

    1. logs/console.url —— 拥有 token 的进程写出来的现成地址（带口令，只读）
       但它可能被「起在随机端口上的实例/判据」写脏（实测被写成 :14675 而没人听）
       所以必须做**活性检查**（TCP 连通 0.4s）：连不上复查一次仍死 ⇒ 弃用；
    2. 回落 config：server.port（默认 3210）+ server.token（结构化读取，
    3. 两边都拿不到 ⇒ http://127.0.0.1:3210/（文档化默认值）。
    文件值**活着**则照旧以它为准（随机端口实例仍优先——先到先得口径）。

   的 C# ResolveLiveUrl 迁 Qt、 正式壳启动时的首次探活，都用本模块。
  本模块对 agent/** 只读（logs/console.url 与 config.json），守硬约束。

用法：
    from addr import resolve_base_url
    url, source = resolve_base_url()        # source: "file" / "config" / "default"
    probe = probe_backend(url)              # 与 heal.py 的探活状态机直接串接

自检：python addr.py   （造脏文件/假活端口/空目录三种现场，全真跑）
"""
from __future__ import annotations

import json
import socket
import sys
import urllib.parse
from pathlib import Path

# 嵌入式运行时（python310._pth）不把脚本目录放进 sys.path —— 自检直跑必需
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 项目根 = 本文件的上级（ui_qt → persona-morph；落位自 _scratch/qt_proto，层级浅一级）
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = "http://127.0.0.1:3210/"


def join_url(base: str, path: str = "", q: str = "") -> str:
    """统一 URL 拼接 —— **path 必须落在 query 之前，且全 URL 只允许一个 `?`**。

    本模块拼出的 base 自带 `?token=…` 查询（_config_base / logs/console.url）。
    老写法「base 去尾斜杠后直接接路径」会把路径塞进 query ——
    实测 `/?token=x/api/status` → 401（token 连同后面的 /api/status 一起被
    当成 query 解析），这就是真机首跑全界面「状态不明」的根因。

    全 Qt 侧访问后端 URL 一律走本函数：
        join_url(base, "/api/status")                  # GET/POST 接口
        join_url(base, "/api/config", "k=v")           # 追加额外查询参数
        join_url(base, "/api/wechat-groups?refresh=1") # path 自带 query 也合法
    口径：摘掉 base 自带 query 得 root → root 去尾斜杠 → 摘掉 path 自带 query
    → 拼 path → 三段 query（base 自带 / path 自带 / 调用方追加）按序用 & 合并。
    path 自带 query 若不合并、直接往尾上再加 `?token=`，会拼出
    `?refresh=1?token=x` —— 服务器把 `1?token=x` 当成一个参数值，token 丢失
    ⇒ 鉴权 401（真机「刷新群列表 401」事故根因，症状检验器 /api/verify?id=
    与会话列表 /api/sessions?limit= 同源中招）。
    """
    b = (base or "").strip()
    if "?" in b:
        root, base_q = b.split("?", 1)
    else:
        root, base_q = b, ""
    root = root.rstrip("/")
    p = (path or "").strip()
    if p and not p.startswith("/"):
        p = "/" + p
    p_q = ""
    if "?" in p:
        p, p_q = p.split("?", 1)
    url = root + p
    qs = base_q
    if p_q:
        qs = (qs + "&" + p_q) if qs else p_q
    if q:
        qs = (qs + "&" + q) if qs else q
    if qs:
        url += "?" + qs
    return url


def url_live(u: str, timeout: float = 0.4) -> bool:
    """TCP 活性检查 —— 只测「端口上有没有人听」，不发 HTTP。

    与 agent/notify_ui.py 的 _url_live 同口径（0.4s：在忙机器上一次探测
    会误判，所以弃用前调用方要复查一次——见 resolve_base_url）。
    """
    try:
        p = urllib.parse.urlsplit(u)
        host = p.hostname or "127.0.0.1"
        port = int(p.port or 80)
        with socket.create_connection((host, port), timeout):
            return True
    except Exception:
        return False


def _read_config_server(root: Path) -> dict:
    """结构化读 server 段：config.json 优先（真实运行配置），example 兜底。"""
    for name in ("config.json", "config.example.json"):
        p = root / name
        if not p.exists():
            continue
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
            srv = cfg.get("server") or {}
            if isinstance(srv, dict):
                return srv
        except Exception:
            continue
    return {}


def _config_base(root: Path) -> str:
    """配置地址（兜底权威来源）。127.0.0.1 是口径内硬编码（同 agent 侧）。"""
    srv = _read_config_server(root)
    try:
        port = int(srv.get("port") or 3210)
    except (TypeError, ValueError):
        port = 3210
    tok = str(srv.get("token") or "")
    return "http://127.0.0.1:%d/" % port + (("?token=" + tok) if tok else "")


def resolve_base_url(root: str | Path = "") -> tuple[str, str]:
    """按权威顺序解析控制台地址，返回 (url, source)。

    source 取值（取证用，界面上不显示）：
      "file"    logs/console.url 活性检查通过，以它为准
      "config"  文件缺失或死链，回落配置地址
      "default" 连配置都没有，用文档化默认值
    """
    root = Path(root) if root else ROOT
    file_url = ""
    fp = root / "logs" / "console.url"
    if fp.exists():
        try:
            file_url = fp.read_text(encoding="utf-8").strip()
        except Exception:
            file_url = ""
    if file_url:
        # 活性检查；死了复查一次（0.4s 在忙机器上会误判，同 agent 侧口径）
        if url_live(file_url) or url_live(file_url):
            return file_url, "file"
        # 死链：弃用，回落配置地址（就算配置地址也连不上也回落——
        # 配置是文档化的唯一权威来源，绝不把死链交出去）
    cfg_url = _config_base(root)
    if cfg_url:
        return cfg_url, "config"
    return DEFAULT_BASE, "default"


# ---------------------------------------------------------------- 自检

def _selftest() -> list[tuple[str, bool, str]]:
    """三种现场全真跑：脏文件回落 / 活文件优先 / 空目录默认。"""
    import tempfile # noqa: PLC0415

    out: list[tuple[str, bool, str]] = []

    # 现场一：文件是死链（写了没人听的端口）⇒ 必须弃用回落 config
    with tempfile.TemporaryDirectory() as td:
        troot = Path(td)
        (troot / "logs").mkdir()
        (troot / "logs" / "console.url").write_text("http://127.0.0.1:14675/", encoding="utf-8")
        (troot / "config.json").write_text(
            json.dumps({"server": {"port": 3210, "token": "abc"}}), encoding="utf-8")
        url, src = resolve_base_url(troot)
        out.append(("死链文件被弃用，回落配置地址", src == "config" and url == "http://127.0.0.1:3210/?token=abc",
                    f"{src} {url}"))

    # 现场二：文件指向真活端口（本机临时起一个）⇒ 以文件为准
    import threading # noqa: PLC0415
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    live_port = srv.getsockname()[1]
    try:
        with tempfile.TemporaryDirectory() as td:
            troot = Path(td)
            (troot / "logs").mkdir()
            (troot / "logs" / "console.url").write_text(
                f"http://127.0.0.1:{live_port}/?token=x", encoding="utf-8")
            url, src = resolve_base_url(troot)
            out.append(("活文件优先于配置（随机端口实例先到先得）",
                        src == "file" and str(live_port) in url, f"{src} {url}"))
    finally:
        srv.close()

    # 现场三：什么都没有 ⇒ 拼默认端口 3210，来源归 config
    # （agent 权威口径：config 拼不出才落 default；缺 server 段时用默认端口拼出
    #   http://127.0.0.1:3210/ 并以 config 为源 —— 与 notify_ui.console_url 行为一致）
    with tempfile.TemporaryDirectory() as td:
        url, src = resolve_base_url(td)
        out.append(("空现场用默认端口 3210（来源=config，同 agent 口径）",
                    src == "config" and url == DEFAULT_BASE, f"{src} {url}"))

    # 链路串接：resolve 出来的地址直接能喂给 heal.probe_backend（类型/口径流转）
    from heal import probe_backend # noqa: PLC0415
    url, _src = resolve_base_url()
    probe = probe_backend("http://127.0.0.1:1", timeout=0.2)
    out.append(("resolve → probe 链路串通（不可达口判 REFUSED）",
                probe.health.value in ("refused", "unknown"), probe.health.value))

    # 口径防回归：config 里 token 为空时不得拼出 "?token="
    with tempfile.TemporaryDirectory() as td:
        troot = Path(td)
        (troot / "config.json").write_text(
            json.dumps({"server": {"port": 3210, "token": ""}}), encoding="utf-8")
        url, _src = resolve_base_url(troot)
        out.append(("空 token 不拼 ?token=（401 事故教训）",
                    "?token=" not in url, url))

    # #0：join_url —— base 带 query 时 path 必须落在 query 之前
    j = join_url("http://127.0.0.1:3210/?token=abc", "/api/status")
    out.append(("join_url: 带 token base 的 path 落在 query 之前",
                j == "http://127.0.0.1:3210/api/status?token=abc", j))
    j = join_url("http://127.0.0.1:3210", "/api/status")
    out.append(("join_url: 无 query base 照常拼接",
                j == "http://127.0.0.1:3210/api/status", j))
    j = join_url("http://127.0.0.1:3210/", "api/status")
    out.append(("join_url: 容忍尾斜杠与裸路径",
                j == "http://127.0.0.1:3210/api/status", j))
    j = join_url("http://127.0.0.1:3210/?token=abc", "/api/config", "k=1")
    out.append(("join_url: 追加 query 与自带 token 用 & 连接",
                j == "http://127.0.0.1:3210/api/config?token=abc&k=1", j))
    j = join_url("http://127.0.0.1:3210/?token=abc", "")
    out.append(("join_url: 空 path 只留 root+query",
                j == "http://127.0.0.1:3210?token=abc", j))
    # 真机 401 事故：path 自带 query 不得产生双 ?（token 被吞进前一个参数值 ⇒ 鉴权必挂）
    j = join_url("http://127.0.0.1:3210/?token=abc", "/api/wechat-groups?refresh=1")
    out.append(("join_url: path 自带 query 并入统一 query（双 ? 会 401）",
                j == "http://127.0.0.1:3210/api/wechat-groups?token=abc&refresh=1", j))
    j = join_url("http://127.0.0.1:3210/?token=abc", "/api/verify?id=v1")
    out.append(("join_url: /api/verify?id= 同样并入（症状检验器同源事故）",
                j.count("?") == 1 and "id=v1" in j and "token=abc" in j, j))
    # 链路防回归：resolve 出来的真 base 喂 join_url，path 必须不进 query
    b, _src = resolve_base_url()
    j = join_url(b, "/api/status")
    qpos, ppos = j.find("?"), j.find("/api/status")
    out.append(("join_url: 真实 resolve base → path 在 ? 之前",
                ppos != -1 and (qpos == -1 or ppos < qpos), j))
    return out


if __name__ == "__main__":
    rows = _selftest()
    bad = [r for r in rows if not r[1]]
    for name, ok, extra in rows:
        print(("  OK  " if ok else "  FAIL") + "  " + name + (("   [" + extra + "]") if extra else ""))
    print()
    print(f"{len(rows) - len(bad)} 通过 / {len(bad)} 失败")
    raise SystemExit(1 if bad else 0)
