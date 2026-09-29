#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""pmctl —— **不依赖图形界面的操作入口**（纯终端 CLI，走产品自带的本地 HTTP API）。

## 为什么有它
现场（2026-09-29 群相反馈 · 另一台机器）：**AMD ASP2400 这类无独显的老 CPU** 上，
浏览器/WebView2 版的网页控制台"卡到不能用" ⇒ 用户在低配服务器上**根本没有可用的操作界面**。
但产品本来就有一套完整 HTTP API（84 条路由，网页控制台只是它的一个前端）
⇒ 这个 CLI 直接调那套 API：**不开浏览器、不跑 WebView2、不画任何界面**，纯文本进纯文本出。

## 用法（全部命令都不需要图形界面）
    python scripts/pmctl.py status              # 运行状态 / 监听目标 / 引擎 / OCR 档位
    python scripts/pmctl.py adapt               # 本机适配档案（只读）
    python scripts/pmctl.py verify              # 跑全部症状检验器
    python scripts/pmctl.py verify send_blocked # 只跑某一条
    python scripts/pmctl.py check               # 体检（可能耗时，会打印耗时）
    python scripts/pmctl.py sessions 20         # 最近 20 条会话
    python scripts/pmctl.py logs 80             # 日志尾部 80 行
    python scripts/pmctl.py cfg get wechat.poll_interval
    python scripts/pmctl.py cfg set wechat.poll_interval 6
    python scripts/pmctl.py pause / resume
    python scripts/pmctl.py lowspec on|off|show # **省资源预设**（低配机器用这个）
    python scripts/pmctl.py doctor              # status+adapt+check 一把跑完（远程排查用）

## 口径
- **只调产品自己的 API**（与网页控制台同一套路由、同一套权限），不直连数据库、不改文件。
- 地址发现顺序：`--url` > 环境变量 `PMCTL_URL` > `logs/console.url`（拥有 token 的进程写出来的现成地址）
  > `config.json` 的 `server.port`。找不到就**明确报错并给出下一步**，绝不猜。
- 输出**不用 emoji**（本仓 P0 规则：图标一律 SVG/文字，邮件与终端同理），`--json` 给机器读。
- `lowspec` 只碰**白名单里的省资源键**，并且**打印改了哪几条、怎么恢复**；安全开关（只走后台等）一条不碰。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 省资源预设（**白名单**）：低配机器上把"画面好看但吃 CPU"的都关掉。
#: 只列**确认存在**的键（见 `agent/config.py` 的 DEFAULT_CONFIG）——写错键名等于偷偷造垃圾配置。
LOWSPEC = {
    "ui.whale_cursor": False,        # 桌面挂件/光标特效
    "ui.whale_anim.worm": 0,         # 三种挂件动画强度归零
    "ui.whale_anim.plane": 0,
    "ui.whale_anim.zap": 0,
    "ui.wave_fx.enabled": False,     # 波纹特效
    "ui.background": "",             # 自定义背景（大图渲染）
    "wechat.poll_interval": 6,       # 轮询从 3s 放宽到 6s（CPU 占用近似减半）
}
#: `lowspec on` 之前的原值快照放这里（`off` 时恢复；重启也会读）
SNAP_PATH = os.path.join(ROOT, "data", "pmctl_lowspec_snapshot.json")


def _warn(msg: str) -> None:
    """失败痕迹（默认安静，`PMCTL_DEBUG=1` 时打 stderr）。

    ⛔ 本仓静默点棘轮要求「不许空的 except」—— 兜底必须留痕，哪怕是"只在调试时可见"。
    """
    if os.environ.get("PMCTL_DEBUG"):
        sys.stderr.write("[pmctl] %s\n" % msg)


def _die(msg: str, code: int = 2):
    print("错误：%s" % msg)
    sys.exit(code)


def _discover(args) -> tuple:
    """返回 `(base_url, token)`；找不到就 `_die`（给出可照做的下一步）。"""
    url = (getattr(args, "url", "") or os.environ.get("PMCTL_URL") or "").strip()
    if not url:
        try:
            sys.path.insert(0, ROOT)
            from agent.util import read_console_url
            url = (read_console_url() or "").strip()
        except Exception: # noqa: BLE001
            p = os.path.join(ROOT, "logs", "console.url")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as fh:
                    url = (fh.read() or "").strip()
    if not url:
        try:
            sys.path.insert(0, ROOT)
            from agent.config import get_config
            port = int(((get_config() or {}).get("server") or {}).get("port") or 3210)
            url = "http://127.0.0.1:%d/" % port
        except Exception as e: # noqa: BLE001
            _die("拿不到控制台地址（logs/console.url 不存在，配置也读不到：%s）。\n"
                 "      请在产品目录里执行，或用 --url http://127.0.0.1:端口/ 指定。" % str(e)[:80])
    tok = (getattr(args, "token", "") or os.environ.get("PMCTL_TOKEN") or "").strip()
    if not tok:
        q = urllib.parse.urlparse(url).query
        tok = urllib.parse.parse_qs(q).get("token", [""])[0]
    if not tok:
        try:
            sys.path.insert(0, ROOT)
            from agent.config import get_config
            tok = str(((get_config() or {}).get("server") or {}).get("token") or "")
        except Exception: # noqa: BLE001
            tok = ""
    base = url.split("?", 1)[0].rstrip("/") + "/"
    return base, tok


def _req(args, path: str, method: str = "GET", body=None, timeout: float = 60.0):
    base, tok = _discover(args)
    data = None
    headers = {"Accept": "application/json"}
    if tok:
        headers["Authorization"] = "Bearer " + tok
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path.lstrip("/"), data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if hasattr(e, "read") else ""
        _die("HTTP %s：%s\n      地址：%s（地址不对就 --url 覆盖；服务没起就先启动产品）"
             % (e.code, raw[:200] or e.reason, base))
    except Exception as e: # noqa: BLE001
        _die("连不上 %s：%s\n      服务没起？或端口不对（用 --url 指定）" % (base, str(e)[:120]))
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception: # noqa: BLE001
        return {"_raw": raw}


def _kv(title: str, pairs) -> None:
    print("== %s ==" % title)
    for k, v in pairs:
        if v is None or v == "":
            continue
        print("   %-16s %s" % (k, v))


def cmd_status(a) -> int:
    d = _req(a, "/api/status")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    g = (lambda *ks: next((d.get(k) for k in ks if d.get(k) is not None), None))
    _kv("运行状态", [
        ("版本", g("version", "ver")), ("运行", g("running", "alive")), ("暂停", g("paused")),
        ("监听目标", g("targets", "watchers")), ("模型", g("model")),
        ("微信", g("wechat", "wx_ready")), ("消息库", g("db", "db_ok")),
    ])
    try:
        sys.path.insert(0, ROOT)
        from agent import chat_ocr as _o
        st = _o.rapidocr_status()
        es = _o.available()
        _kv("OCR", [("内置引擎", "可用" if es else "不可用"),
                    ("第二引擎", "%s（%s）" % (st.get("tier"), st.get("why") or "可用") if st.get("ok") else "不可用")])
    except Exception as e: # noqa: BLE001
        print("   （本机 OCR 档位读不到：%s）" % str(e)[:60])
    print("")
    print("   提示：完整状态里没打印的字段可以用 `--json` 看原始返回。")
    return 0


def cmd_version(a) -> int:
    d = _req(a, "/api/version")
    print(json.dumps(d, ensure_ascii=False, indent=1) if a.json else
          "当前版本：%s" % (d.get("version") or d.get("current") or d))
    return 0


def cmd_adapt(a) -> int:
    d = _req(a, "/api/verify?id=machine")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    print(d.get("report") or json.dumps(d, ensure_ascii=False, indent=1))
    return 0


def _verify_ids(a) -> list:
    d = _req(a, "/api/verifiers")
    return [(str(v.get("id")), str(v.get("name"))) for v in (d.get("verifiers") or [])]


def cmd_verify(a) -> int:
    ids = _verify_ids(a)
    want = [x for x in (a.ids or [])] or [i for i, _n in ids]
    known = dict(ids)
    bad = [x for x in want if x not in known]
    if bad:
        _die("没有这些检验器：%s\n      可用的：%s" % ("、".join(bad), "、".join("%s(%s)" % (i, n) for i, n in ids)))
    for i in want:
        d = _req(a, "/api/verify?id=%s" % urllib.parse.quote(i), timeout=120.0)
        ok = d.get("ok")
        head = {True: "通过", False: "卡住", None: "没测到"}.get(ok, str(ok))
        print("── %s（%s）：%s" % (known[i], i, head))
        if a.json:
            print(json.dumps(d, ensure_ascii=False, indent=1))
        else:
            print((d.get("report") or "").strip())
        print("")
    return 0


def cmd_check(a) -> int:
    t0 = time.time()
    d = _req(a, "/api/selfcheck", method="POST", body={}, timeout=300.0)
    dt = time.time() - t0
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    print("== 体检（%.1fs）==" % dt)
    print("   %s" % (d.get("summary") or d.get("why") or "（没有摘要）"))
    for c in (d.get("checks") or [])[:40]:
        print("   [%s] %s：%s" % ("OK" if c.get("ok") else "!!", c.get("name"), str(c.get("detail") or "")[:110]))
    return 0


def cmd_sessions(a) -> int:
    d = _req(a, "/api/sessions")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    rows = d.get("sessions") or d.get("rows") or []
    print("== 最近 %d 条会话 ==" % min(a.n, len(rows)))
    for r in rows[:a.n]:
        print("   %-28s %s" % (str(r.get("name") or r.get("title") or "?")[:28],
                               str(r.get("last") or r.get("time") or "")[:20]))
    return 0


def cmd_logs(a) -> int:
    d = _req(a, "/api/logs")
    lines = d.get("lines") or []
    if isinstance(lines, str):
        lines = lines.splitlines()
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    for ln in lines[-a.n:]:
        print(str(ln).rstrip())
    return 0


def _get_path(cfg: dict, path: str):
    o = cfg
    for k in str(path).split("."):
        if not isinstance(o, dict) or k not in o:
            return None
        o = o[k]
    return o


def cmd_cfg(a) -> int:
    if a.action == "get":
        cfg = _req(a, "/api/config")
        v = _get_path(cfg, a.key)
        print("%s = %s" % (a.key, json.dumps(v, ensure_ascii=False) if v is not None else "（没这个键）"))
        return 0 if v is not None else 1
    if a.action != "set":
        _die("cfg 只支持 get / set")
    val: object = a.value
    if val in ("true", "false"):
        val = (val == "true")
    else:
        try:
            val = int(val)
        except ValueError as e_int:
            try:
                val = float(val)
            except ValueError as e_float:
                _warn("值既不是整数也不是小数，按字符串写入：%s / %s" % (type(e_int).__name__, type(e_float).__name__))
    node = body = {}
    ks = str(a.key).split(".")
    for k in ks[:-1]:
        node[k] = {}
        node = node[k]
    node[ks[-1]] = val
    body["_touched"] = []          # ⛔ 声明"没碰安全开关" ⇒ 后端守卫会挡住意外的连带翻转
    _req(a, "/api/config", method="POST", body=body)
    print("已写入 %s = %s（这是局部提交：只改这一个键，其它键保持原值）" % (a.key, val))
    return 0


def cmd_pause(a) -> int:
    want = (a.mode == "pause")
    _req(a, "/api/pause", method="POST", body={"paused": want})
    print("已%s。" % ("暂停" if want else "恢复"))
    return 0


def cmd_lowspec(a) -> int:
    cfg = _req(a, "/api/config")
    if a.action == "show":
        print("== 省资源预设当前值 ==")
        for k, v in LOWSPEC.items():
            print("   %-26s 现在=%s  低配目标=%s" % (k, json.dumps(_get_path(cfg, k), ensure_ascii=False), json.dumps(v)))
        return 0
    if a.action == "on":
        snap = {k: _get_path(cfg, k) for k in LOWSPEC}
        try:
            os.makedirs(os.path.dirname(SNAP_PATH), exist_ok=True)
            with open(SNAP_PATH, "w", encoding="utf-8") as fh:
                json.dump(snap, fh, ensure_ascii=False, indent=1)
        except Exception as e: # noqa: BLE001
            print("（原值快照没存下：%s；`off` 时会用内置默认值恢复）" % str(e)[:60])
        body = {"_touched": []}
        changed = []
        for k, v in LOWSPEC.items():
            if _get_path(cfg, k) == v:
                continue
            node = body
            ks = k.split(".")
            for kk in ks[:-1]:
                node = node.setdefault(kk, {})
            node[ks[-1]] = v
            changed.append(k)
        if len(body) <= 1:
            print("低配模式：本来就是省资源状态，没改任何键。")
            return 0
        _req(a, "/api/config", method="POST", body=body)
        print("已开低配模式，改了 %d 个键：" % len(changed))
        for k in changed:
            print("   %s -> %s" % (k, json.dumps(LOWSPEC[k], ensure_ascii=False)))
        print("")
        print("   恢复：python scripts/pmctl.py lowspec off")
        print("   另外建议（这几条没有配置键，得手动）：")
        print("     1) 别再开网页控制台/桌面窗口 —— 用这个 CLI；")
        print("     2) 不用「本地图片生成 / 本地 SD」（image_gen / local_sd），那是最吃 CPU 的；")
        print("     3) 不跑「UI 标定」这类一次性重活。")
        return 0
    if a.action == "off":
        snap = {}
        if os.path.exists(SNAP_PATH):
            try:
                snap = json.load(open(SNAP_PATH, encoding="utf-8"))
            except Exception as e: # noqa: BLE001
                print("（快照读不出来：%s）" % str(e)[:60])
        body = {"_touched": []}
        n = 0
        for k, v in LOWSPEC.items():
            orig = snap.get(k, None)
            if orig is None or orig == v:
                continue
            node = body
            ks = k.split(".")
            for kk in ks[:-1]:
                node = node.setdefault(kk, {})
            node[ks[-1]] = orig
            n += 1
        if n == 0:
            print("没找到低配前的快照（或值没变）⇒ 不改任何键。要手动改：cfg set <键> <值>。")
            return 0
        _req(a, "/api/config", method="POST", body=body)
        print("已恢复低配前的 %d 个键。" % n)
        return 0
    _die("lowspec 只支持 on / off / show")


def cmd_doctor(a) -> int:
    print("[1/3] 运行状态"); cmd_status(a)
    print("\n[2/3] 本机适配"); cmd_adapt(a)
    print("\n[3/3] 体检"); cmd_check(a)
    print("\n（把以上整段复制给对方即可定位问题；版本号见 status。）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pmctl", description="不依赖图形界面的操作入口（走产品本地 API）")
    ap.add_argument("--url", default="", help="控制台地址（默认从 logs/console.url 发现）")
    ap.add_argument("--token", default="", help="访问令牌（默认从地址/配置里取）")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON（给脚本用）")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("status", help="运行状态").set_defaults(func=cmd_status)
    sub.add_parser("version", help="版本").set_defaults(func=cmd_version)
    sub.add_parser("adapt", help="本机适配档案（只读）").set_defaults(func=cmd_adapt)
    sub.add_parser("doctor", help="status + adapt + check 一把跑完").set_defaults(func=cmd_doctor)
    p = sub.add_parser("verify", help="跑症状检验器（默认全部）"); p.add_argument("ids", nargs="*"); p.set_defaults(func=cmd_verify)
    p = sub.add_parser("check", help="体检"); p.set_defaults(func=cmd_check)
    p = sub.add_parser("sessions", help="最近会话"); p.add_argument("n", nargs="?", type=int, default=10); p.set_defaults(func=cmd_sessions)
    p = sub.add_parser("logs", help="日志尾部"); p.add_argument("n", nargs="?", type=int, default=60); p.set_defaults(func=cmd_logs)
    p = sub.add_parser("cfg", help="读/写单个配置键"); p.add_argument("action", choices=("get", "set"))
    p.add_argument("key"); p.add_argument("value", nargs="?"); p.set_defaults(func=cmd_cfg)
    p = sub.add_parser("pause", help="暂停"); p.set_defaults(func=cmd_pause, mode="pause")
    p = sub.add_parser("resume", help="恢复"); p.set_defaults(func=cmd_pause, mode="resume")
    p = sub.add_parser("lowspec", help="省资源预设"); p.add_argument("action", nargs="?", default="show",
                                                                 choices=("on", "off", "show")); p.set_defaults(func=cmd_lowspec)

    a = ap.parse_args(argv)
    if not getattr(a, "func", None):
        ap.print_help()
        return 0
    return int(a.func(a) or 0)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception as e: # noqa: BLE001
        _warn("stdout 改 UTF-8 失败（中文可能显示成乱码，不影响功能）：%s" % str(e)[:60])
    sys.exit(main())
