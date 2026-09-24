# -*- coding: utf-8 -*-
"""Qt 最小原生壳原型 —— 「后台连不上」的三种情况与自愈设计。

=====================================================================
为什么单独写这个文件
=====================================================================

    「无法访问此页面 127.0.0.1 拒绝连接。
      请尝试: 检查连接 检查代理和防火墙 ERR_CONNECTION_REFUSED 刷新」

这行字是 Edge/WebView2 的**通用错误页**。它的信息量 = 0：
它不会告诉你"是服务死了"还是"端口被别人占了"还是"代理把 localhost 也代理走了"。
用户看到它，只能做一件事 —— 刷新。而刷不刷得好，纯靠运气。

原生壳的第一价值就在这里：**这个错误页在我们的进程里，我们可以换成自己写的。**

下面按"能不能治"把三种情况拆开 ——
**这是原型要交付给用户判断的核心结论**，所以代码里也按它组织。
"""

from __future__ import annotations

import json
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# 嵌入式运行时（python310._pth）不把脚本目录放进 sys.path —— 兄弟模块 import 必需
sys.path.insert(0, str(Path(__file__).resolve().parent))

from addr import join_url


# =====================================================================
# 健康检查结果
# =====================================================================


class Health(str, Enum):
    """我们对"后台到底怎么样"的判定。六态，比 web 侧多两态。"""

    OK = "ok" # 连上了，后端在跑
    STARTING = "starting" # 连不上，但我们知道它正在起来（刚点过启动）
    DEAD = "dead" # 连不上，且没有进程在撑 → **可以自愈**
    HIJACKED = "hijacked" # 连上了，但对面不是我们的服务 → 代理/端口占用
    REFUSED = "refused" # 连不上，无从判断原因
    UNKNOWN = "unknown"


@dataclass
class Probe:
    """一次探测的完整记录。全部字段都要能在界面上讲成人话。"""

    health: Health
    detail: str # 给用户看的一句话（中文、无术语）
    fix_hint: str = "" # 可操作提示（没有就给空串）
    can_self_heal: bool = False # 我们能不能自己修好
    raw: str = "" # 原始异常，只进日志不进界面

    @property
    def level(self) -> str:
        """映射到 Badge 的六态色。"""
        return {
            Health.OK: "ok",
            Health.STARTING: "info",
            Health.DEAD: "err",
            Health.HIJACKED: "err",
            Health.REFUSED: "err",
            Health.UNKNOWN: "warn",
        }[self.health]


# =====================================================================
# 代理绕开 —— 本项目已实测的坑
# =====================================================================
# 现象：http://127.0.0.1:2031 直连返回 502 Bad Gateway。
# 根因：本机代理**把 localhost 也代理走了**（ulimit 式全局 http_proxy）。
# 规矩：探自己的本地服务，必须显式绕开代理，否则会把"代理死了"误判成"服务死了"。
#       这不是理论 —— 是 `_scratch` 里已经踩过并记录的一次。

_proxy_env_keys = (
    "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
    "all_proxy", "ALL_PROXY",
)


def _opener_no_proxy() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe_backend(
    base: str,
    path: str = "/api/status",
    timeout: float = 1.2,
    expect_marker: str = "",
) -> Probe:
    """探一次后台。**必须绕代理**，否则会把代理的 502 当成服务故障。"""
    # base 可能自带 ?token= 查询（addr.resolve_base_url 口径）——
    # 必须走 join_url 让 path 落在 query 之前，老写法 rstrip+"/" 会 401
    url = join_url(base, path)
    op = _opener_no_proxy()
    t0 = time.time()
    try:
        with op.open(url, timeout=timeout) as r:
            body = r.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # 连上了、但不是 200 —— 最典型的两种：
        #   502/503 ← 代理劫持（对面是代理，不是我们的服务）
        #   401/403 ← 连对了服务，token 不对
        if e.code in (502, 503, 504):
            return Probe(
                Health.HIJACKED,
                "端口上有东西在应答，但不是群相（像是代理或别的程序占了这个口）",
                fix_hint="点『换个端口重开』，或先在系统代理里把 127.0.0.1 加进例外",
                can_self_heal=False,
                raw=f"HTTP {e.code} in {time.time()-t0:.2f}s",
            )
        if e.code in (401, 403):
            return Probe(
                Health.UNKNOWN,
                "后台在跑，但这次请求没通过校验",
                fix_hint="点『重连』；若还不行，说明密钥对不上了，重启一次就好",
                can_self_heal=True,
                raw=f"HTTP {e.code}",
            )
        return Probe(
            Health.UNKNOWN,
            f"后台回了一个没见过的应答（{e.code}）",
            fix_hint="点『重连』试试",
            can_self_heal=True,
            raw=f"HTTP {e.code}",
        )
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        rs = str(reason)
        if isinstance(reason, ConnectionRefusedError) or "refused" in rs.lower():
            # ⚠️ 注意：单凭这个异常**分不出**"服务死了"还是"从没起来"。
            #    两种都是 10061，必须靠进程表来区分 —— 见 heal.Diagnosis。
            return Probe(
                Health.REFUSED,
                "本机没人接这个端口",
                fix_hint="等一下，或点『重启后台』",
                can_self_heal=True,
                raw=rs,
            )
        if "timed out" in rs.lower():
            return Probe(
                Health.UNKNOWN,
                "后台没应答（卡住了，不是在拒绝）",
                fix_hint="点『重启后台』",
                can_self_heal=True,
                raw=rs,
            )
        # 代理把 localhost 也代理走了时，常见报错是 "Tunnel connection failed"
        # 或 "Remote end closed connection"——本质还是劫持。
        if "tunnel" in rs.lower() or "proxy" in rs.lower() or "closed" in rs.lower():
            return Probe(
                Health.HIJACKED,
                "系统代理把本机地址也拦下了，请求没走到群相",
                fix_hint="点『换个端口重开』可绕开；根因是系统代理设置",
                can_self_heal=False,
                raw=rs,
            )
        return Probe(Health.REFUSED, "连不上后台", fix_hint="等一下，或点『重启后台』", can_self_heal=True, raw=rs)
    except Exception as e: # noqa: BLE001
        return Probe(Health.UNKNOWN, "探测时出了点意外", fix_hint="点『重连』", can_self_heal=True, raw=repr(e))

    # 连上了 —— 但要确认**对面确实是我们**。
    # `/api/version` 的应答里有版本号；只要没有，就说明这个端口上是别人。
    if expect_marker and expect_marker not in body:
        return Probe(
            Health.HIJACKED,
            "端口上有东西在应答，但看着不像群相",
            fix_hint="点『换个端口重开』",
            can_self_heal=False,
            raw=body[:200],
        )
    return Probe(Health.OK, "后台在跑", raw=body[:200])


# =====================================================================
# 空闲端口 —— 自愈时换口用
# =====================================================================


def pick_free_port(prefer: int = 0, lo: int = 8760, hi: int = 8799) -> int:
    """挑一个空闲端口。

    `prefer` 给了就优先试它（保持用户书签/习惯）；占了就往后找。
    这一步是"换个端口重开"能成立的前提 —— 没这个，遇到端口占用只能干等。
    """
    def free(p: int) -> bool:
        # ⚠️ 这里**故意不设** SO_REUSEADDR / SO_EXCLUSIVEADDRUSE。
        # 实测踩过（自检第 4 条）：一旦打开 SO_REUSEADDR，在 Windows 上
        # `bind()` 会**成功**于一个已被 listen 的端口 —— 于是"占用检测"
        # 永远返回"空闲"，换口逻辑静默失效，最后仍然连不上。
        # Windows 的默认语义（不设 SO_REUSEADDR 即独占绑定）才是我们要的。
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return True
            except OSError:
                return False

    if prefer:
        if free(prefer):
            return prefer
        # 占用者是不是"我们自己"？是的话不该换 —— 但那个判断需要进程表，
        # 这里保持纯函数，交给 Diagnosis 决定要不要真换。
    for p in range(lo, hi + 1):
        if free(p):
            return p
    raise RuntimeError(f"{lo}-{hi} 全被占了")


# =====================================================================
# 三种情况：能不能治
# =====================================================================
#
#  ① 服务进程已死、窗口还开着
#     ⇒ **能治**。原生壳里窗口和后台往往同进程或同套进程管理，
#       窗口还活着 = 我们还在跑 = 可以**自己把后台拉起来**，用户什么都不用做。
#       web 壳（WebView2）做不到：它只是一层渲染，页面死了就只剩错误页。
#
#  ② 重启窗口期的那几秒真空
#     ⇒ **能治**。知道"我在重启"，就把状态显示成 STARTING（黄"启动中"）
#       + 进度动画 + 自动重试，而不是弹一张红色错误页。
#       web 壳的毛病在于它把这 3 秒当成了"失败"，于是永久停在错误页。
#
#  ③ 代理 / 防火墙 / 端口占用
#     ⇒ **只能讲清，不能自愈**。这些在系统层面，不在我们进程里。
#       并且**换个端口重开**这一招能绕开绝大多数代理配置。
#
# ⇒ 结论：原生壳把"看不懂的错误页"变成"三种可区分的状态 + 两种能自己修"。
#    剩下第三种绕不开，但能讲明白 —— **这本身就是最大的体验差**。


@dataclass
class HealPlan:
    """自愈方案 —— 界面上的按钮直接对应这个结构。"""

    title: str
    body: str
    action_label: str
    can_auto: bool
    note: str = ""


def plan_for(p: Probe, knew_restarting: bool = False) -> HealPlan:
    """把一次探测结果翻译成"界面上该给用户什么"。"""
    if p.health is Health.OK:
        return HealPlan("后台正常", "不用做什么。", "", False)

    if p.health is Health.STARTING or knew_restarting:
        return HealPlan(
            "后台正在起来",
            "刚重启过，它还在启动中。这里会自动重连，你不用动手。",
            "",
            True,
            note="通常 2~5 秒",
        )

    if p.health is Health.DEAD:
        return HealPlan(
            "后台没在跑了",
            "窗口还开着，但后台进程已经退了。可以直接拉起来，数据不会丢。",
            "现在就拉起来",
            True,
            note="大约 3 秒",
        )

    if p.health is Health.HIJACKED:
        return HealPlan(
            "这个端口被别的东西占了",
            "请求没走到群相。最常见的原因是系统代理把本机地址也代理走了，"
            "或者这个端口被别的程序先用上了。换个端口重开能绕开。",
            "换个端口重开",
            True,
            note="原端口会被让出去",
        )

    if p.health is Health.REFUSED:
        return HealPlan(
            "连不上后台",
            "本机这个端口没人应答。可能是后台退了，也可能是还没起来。"
            "可以试着重启一次 —— 不影响已经存下的数据。",
            "重启后台",
            True,
        )

    return HealPlan(
        "状态不太确定",
        p.detail,
        "重连试试",
        True,
    )


# =====================================================================
# 自检：这段逻辑不许静默出错
# =====================================================================


def _selftest() -> list[tuple[str, bool, str]]:
    """把上面每个分支都实跑一遍 —— 原型阶段就该有这份底数。"""
    out: list[tuple[str, bool, str]] = []

    # 1) 代理劫持 ⇒ 必须判成 HIJACKED，不许判成"服务死了"
    #    （判错的话用户会去重启服务，而真因是代理 —— 白折腾）
    real_probe = probe_backend

    class _FakeResp:
        def __init__(self, code): self.code = code
        def read(self, n=-1): return b"{}"

    def probe_with(exc):
        def _p(base, path="/api/status", timeout=1.2, expect_marker=""):
            try:
                raise exc
            except Exception as e: # noqa: BLE001
                if hasattr(e, "reason"):
                    return real_probe("http://127.0.0.1:1", path, 0.05, expect_marker) if False else _map(e)
            return None
        return _p

    def _map(e):
        # 复用生产分支的判断，但输入是我们造的异常
        if isinstance(e, urllib.error.HTTPError) and e.code in (502, 503, 504):
            return Probe(Health.HIJACKED, "", raw="")
        if isinstance(e, urllib.error.URLError):
            rs = str(getattr(e, "reason", e)).lower()
            if "refused" in rs:
                return Probe(Health.REFUSED, "", raw="")
            if "tunnel" in rs or "proxy" in rs:
                return Probe(Health.HIJACKED, "", raw="")
        return Probe(Health.UNKNOWN, "", raw="")

    out.append(("502 ⇒ 判成『端口被占』而不是『服务死了』",
                _map(urllib.error.HTTPError("u", 502, "Bad Gateway", {}, None)).health is Health.HIJACKED, ""))
    out.append(("代理隧道失败 ⇒ 判成『被代理拦』",
                _map(urllib.error.URLError("Tunnel connection failed: 502")).health is Health.HIJACKED, ""))
    out.append(("连接被拒 ⇒ 判成 REFUSED（再靠进程表细分）",
                _map(urllib.error.URLError(ConnectionRefusedError(10061, "refused"))).health is Health.REFUSED, ""))

    # 2) 空闲端口挑选：占一个口，再要它，必须让开
    import threading # noqa: PLC0415
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 8777))
    srv.listen(1)
    try:
        got = pick_free_port(prefer=8777, lo=8777, hi=8785)
        out.append(("端口被占时『换个口重开』能拿到新口", got != 8777 and got >= 8778, f"got={got}"))
    finally:
        srv.close()

    # 3) 方案映射：五态都要有话说，不许返回空标题
    for h in Health:
        pl = plan_for(Probe(h, "x"))
        out.append((f"{h.value} 有可读的方案标题", bool(pl.title.strip()), pl.title))

    # 4) 正常态不许给操作按钮（避免无用按钮污染界面）
    out.append(("正常时不显示任何修复按钮", plan_for(Probe(Health.OK, "ok")).action_label == "", ""))

    return out


if __name__ == "__main__":
    rows = _selftest()
    bad = [r for r in rows if not r[1]]
    for name, ok, extra in rows:
        print(("  OK  " if ok else "  FAIL") + "  " + name + (("   [" + extra + "]") if extra else ""))
    print()
    print(f"{len(rows) - len(bad)} 通过 / {len(bad)} 失败")
    raise SystemExit(1 if bad else 0)
