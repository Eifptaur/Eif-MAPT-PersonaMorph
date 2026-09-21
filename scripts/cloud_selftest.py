# -*- coding: utf-8 -*-
"""判据：上云预留接口（第 10 条排期里"上云三件"的接口层）——不联网、不发数据。

跑法： py -3 scripts\\cloud_selftest.py      退出码 0=全过 / 1=有失败
重点验三件：①URL 校验（含内网/环回默认拒）②探测**只发 HEAD、不带凭据、不带数据**（打桩抓请求）
③**默认关时 upload 一个请求都不发**，开了也先干跑；④接线与文案。
"""
from __future__ import annotations

import json
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import agent.config as cfgmod                        # noqa: E402
from agent import cloud                              # noqa: E402

PASS, FAIL = [], []
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ⛔ 2026-09-21（第九轮 V-R9-25 改动后）：`cloud.normalize_url` 现在**会做 DNS 解析**
#   （原来只比字符串表）⇒ 判据必须自己把解析钉住，否则 A/D/E 三段会去打真 DNS。
_DNS_TABLE = {"a.com": "93.184.216.34", "example.com": "93.184.216.34", "hook.example": "93.184.216.34"}


def _install_offline_resolver():
    """离线替身：只认表里的名字，别的一律 `gaierror`（判据不打公网、也不碰真 DNS）。"""
    real = socket.getaddrinfo

    def fake(host, port, *a, **k):
        ip = _DNS_TABLE.get(str(host).lower())
        if ip is None:
            raise socket.gaierror(-2, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, int(port or 0)))]

    socket.getaddrinfo = fake
    return real


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


class FakeResp:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def main():
    real_get = cfgmod.get_config
    real_urlopen = cloud.urllib.request.urlopen
    real_gai = socket.getaddrinfo
    real_conn = socket.create_connection

    def use(cfg):
        cfgmod.get_config = lambda: {"cloud": dict(cloud.DEFAULTS, **(cfg or {}))}

    try:
        print("== A. URL 校验 ==")
        _offline_gai = _install_offline_resolver()          # 离线：normalize_url 现在会解析
        use({})
        ok("空串 = 未配置（不算错）", cloud.normalize_url("") == (True, "", "未配置"))
        ok("http/https 都收", cloud.normalize_url("http://a.com/x")[0] and cloud.normalize_url("https://a.com/x")[0])
        ok("去掉末尾斜杠", cloud.normalize_url("https://a.com/hook/")[1] == "https://a.com/hook")
        ok("非 http(s) 拒", not cloud.normalize_url("ftp://a.com")[0])
        ok("缺主机名拒", not cloud.normalize_url("https:///hook")[0])
        for bad in ("http://127.0.0.1:8080/hook", "http://localhost:8080", "http://192.168.1.9/hook", "http://10.0.0.5/x"):
            ok("环回/内网默认拒：" + bad, not cloud.normalize_url(bad)[0], cloud.normalize_url(bad)[2][:30])
        # ⛔ 第九轮 V-R9-25：字符串表时代这 7 种形态**全部放行**（E 线实测），现在逐条守
        for bad2 in ("http://localhost.:8000/x", "http://169.254.169.254/latest/meta-data/",
                     "http://[::ffff:127.0.0.1]:8080/", "http://127.1:8080/x",
                     "http://0x7f000001:8080/x", "http://2130706433:8080/x", "http://100.64.0.1/x"):
            ok("新口径拦住绕过形态：" + bad2, not cloud.normalize_url(bad2)[0], cloud.normalize_url(bad2)[2][:34])
        use({"allow_private": True})
        ok("显式打开 allow_private 后内网放行", cloud.normalize_url("http://127.0.0.1:8080/hook")[0])
        ok("公网地址照旧放行", cloud.normalize_url("https://example.com/hook")[0])

        print("== B. 连通探测：分段定位 + 只发 HEAD/不带凭据/不带数据 ==")
        use({})
        ok("未配置 ⇒ stage=config", cloud.probe("persona")["stage"] == "config")
        ok("which 不认识 ⇒ 明确报错", cloud.probe("nope")["stage"] == "config")
        ok("网址格式错 ⇒ stage=config", cloud.probe(url="ftp://x")["stage"] == "config")

        def raise_dns(*a, **k):
            raise socket.gaierror("name resolution failed")

        socket.getaddrinfo = raise_dns
        r = cloud.probe(url="https://no-such-host.invalid/hook")
        ok("DNS 失败 ⇒ stage=dns 且说明原因", r["stage"] == "dns" and not r["ok"], r["why"][:40])
        _offline_gai = _install_offline_resolver()          # 装回离线替身（别落到真 DNS）

        def raise_tcp(*a, **k):
            raise TimeoutError("timed out")

        socket.create_connection = raise_tcp
        r = cloud.probe(url="https://93.184.216.34/hook")
        ok("TCP 连不上 ⇒ stage=tcp（＝可能被墙/没起服务）", r["stage"] == "tcp" and not r["ok"], r["why"][:46])
        # ⛔ 判据**不出网**：TCP 段改成"连得上"的假连接（原来这里 restore 真实现 ⇒ 会真连 example.com）
        class _FakeSock:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        socket.create_connection = lambda *a, **k: _FakeSock()
        r = cloud.probe(url="https://example.com/hook")
        ok("https 且 TLS 不通 ⇒ stage=tls（离线可判、说明卡在 TLS）",
           r["stage"] == "tls" and not r["ok"], str(r.get("why"))[:44])

        seen = []

        def fake_head(req, timeout=None):
            seen.append({"method": req.get_method(), "url": req.full_url,
                         "headers": {k.lower(): v for k, v in (req.headers or {}).items()},
                         "data": req.data})
            return FakeResp(200)

        cloud.urllib.request.urlopen = fake_head
        # 用 **http** 走 HEAD 那两段：https 会先过 TLS（上面那条已单独判过），
        # 而判据不许真出网 ⇒ 只在"假连接 + 假 urlopen"下跑 HTTP 段
        r = cloud.probe(url="http://example.com/hook")
        ok("HEAD 成功 ⇒ stage=http ok", r["ok"] and r["stage"] == "http" and r["status"] == 200, r)
        req = seen[-1]
        ok("探测用的是 HEAD", req["method"] == "HEAD", req["method"])
        ok("**探测不带任何数据**（req.data 为空）", not req["data"], str(req["data"])[:40])
        ok("**探测不带凭据**（没有 authorization 头）", "authorization" not in req["headers"], list(req["headers"]))

        def fake_405(req, timeout=None):
            raise cloud.urllib.error.HTTPError(req.full_url, 405, "Method Not Allowed", {}, None)

        cloud.urllib.request.urlopen = fake_405
        r = cloud.probe(url="http://example.com/hook")
        ok("接收端只收 POST（405）⇒ 仍算可达（说明地址对）", r["ok"] and r["status"] == 405, r["why"][:50])
        cloud.urllib.request.urlopen = real_urlopen

        print("== C. upload：默认关 ⇒ 一个请求都不发 ==")
        use({})
        calls = []
        cloud.urllib.request.urlopen = lambda *a, **k: (calls.append(1), FakeResp(200))[1]
        r = cloud.upload("persona", {"secret": "不该发出去"})
        ok("总开关关 ⇒ 拒绝（stage=disabled）", (not r["ok"]) and r["stage"] == "disabled" and r["sent"] is False, r["why"][:40])
        ok("**并且真的一个请求都没发**", len(calls) == 0, len(calls))
        use({"enabled": True})
        r = cloud.upload("persona", {"a": 1})
        ok("开了开关但没填网址 ⇒ 拒绝且不发", (not r["ok"]) and r["stage"] == "config" and len(calls) == 0, r["why"][:36])
        use({"enabled": True, "persona_url": "https://example.com/hook", "token": "T0KEN"})
        r = cloud.upload("persona", {"a": 1}, dry=True)
        ok("干跑：只算体积、不发请求", r["ok"] and r["sent"] is False and r["bytes"] > 0 and len(calls) == 0, r)
        sent = []

        def fake_post(req, timeout=None):
            sent.append({"method": req.get_method(), "headers": {k.lower(): v for k, v in (req.headers or {}).items()},
                         "data": req.data})
            return FakeResp(200)

        cloud.urllib.request.urlopen = fake_post
        r = cloud.upload("persona", {"a": 1}, dry=False)
        ok("真发：POST + 带 Token + 带 body", r["ok"] and sent and sent[0]["method"] == "POST"
           and sent[0]["headers"].get("authorization") == "Bearer T0KEN" and sent[0]["data"], r)
        ok("body 是 JSON（能解析回原对象）", json.loads(sent[0]["data"].decode("utf-8")) == {"a": 1})

        print("== C2. body_key 形态（自建站那种：凭据放 body + 判回包 ok:true）==")
        class FakeJsonResp:
            def __init__(self, obj, status=200):
                self._raw = json.dumps(obj).encode("utf-8")
                self.status = status
            def read(self, n=-1):
                # V-R9-26：产品改成 `read(上限+1)` 的带限读取 ⇒ 替身要认这个形参
                return self._raw if (n is None or int(n) < 0) else self._raw[:int(n)]
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        class FakeRawResp:
            status = 200
            def read(self, n=-1):
                b = b"<html>hi</html>"
                return b if (n is None or int(n) < 0) else b[:int(n)]
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        use({"enabled": True, "persona_url": "https://example.com/hook", "token": "T0KEN",
             "auth_style": "body_key"})
        sent2 = []

        def fake_post2(req, timeout=None):
            sent2.append({"headers": {k.lower(): v for k, v in (req.headers or {}).items()},
                          "data": req.data})
            return FakeJsonResp({"ok": True})

        cloud.urllib.request.urlopen = fake_post2
        r = cloud.upload("persona", {"a": 1}, dry=False)
        _b2 = json.loads(sent2[0]["data"].decode("utf-8"))
        ok("body_key：凭据在请求体的 key 字段、原载荷不丢",
           _b2.get("key") == "T0KEN" and _b2.get("a") == 1, str(_b2)[:44])
        ok("body_key：**不发 Authorization 头**（否则与「放请求头」没区别）",
           "authorization" not in sent2[0]["headers"] and "key" not in sent2[0]["headers"],
           list(sent2[0]["headers"]))
        ok("body_key：回包 ok:true ⇒ 成功且标 confirmed", r["ok"] and r.get("confirmed") is True, r)
        cloud.urllib.request.urlopen = lambda req, timeout=None: FakeJsonResp({"ok": False, "error": "not found"})
        r = cloud.upload("persona", {"a": 1}, dry=False)
        ok("body_key：回包 ok:false ⇒ **判失败并说明**（这正是「路径写错也回 200」的场景）",
           (not r["ok"]) and "ok=false" in str(r.get("why")), str(r.get("why"))[:52])
        cloud.urllib.request.urlopen = lambda req, timeout=None: FakeRawResp()
        r = cloud.upload("persona", {"a": 1}, dry=False)
        ok("body_key：回包不是配置格式 ⇒ 不敢说成功", not r["ok"], str(r.get("why"))[:52])
        cloud.urllib.request.urlopen = fake_post
        use({"enabled": True, "persona_url": "https://example.com/hook", "token": "T0KEN"})
        r = cloud.upload("persona", {"a": 1}, dry=False)
        ok("默认仍是 bearer（不写 auth_style 时行为不变）",
           sent[0]["headers"].get("authorization") == "Bearer T0KEN" and r["ok"], r.get("auth_style"))
        # ⛔ 2026-09-21（第五轮回执 V-R5R-4）：原来写成 `not ...["ok"] or True` ⇒ **恒真**（改成什么都过）
        ok("未知 which ⇒ 拒绝", cloud.upload("nope", {})["ok"] is False)
        cloud.urllib.request.urlopen = real_urlopen

        print("== D. 快照与接线 ==")
        use({"persona_url": "https://example.com/hook", "token": "TOKEN-SECRET-9f3a"})
        snap = cloud.snapshot()
        ok("快照带两件端点的三态", len(snap["endpoints"]) == 2
           and any(e["id"] == "persona" and e["configured"] for e in snap["endpoints"]), snap["enabled"])
        ok("快照只说 token 有没有，不回显 token 值",
           snap["token_set"] is True and "TOKEN-SECRET-9f3a" not in json.dumps(snap, ensure_ascii=False), "")
        html = open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
        wui = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
        cfg_py = open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read()
        doc = os.path.join(ROOT, "docs", "上云接口契约.md")
        ok("控制台四个键 + 两个测试按钮 + 读数",
           all(k in html for k in ('data-cfg="cloud.enabled"', 'data-cfg="cloud.persona_url"',
                                   'data-cfg="cloud.blocklist_url"', 'data-cfg="cloud.token"',
                                   'data-cloud-test="persona"', 'data-cloud-test="blocklist"', 'id="cloudStat"')))
        ok("文案写清「默认关＝一个字节都不会上传」", "关着时<b>一个字节都不会上传</b>" in html or "一个字节都不会上传" in html)
        ok("探测按钮打到 /api/cloud/test", "getJSON('/api/cloud/test'" in html and '"/api/cloud/test"' in wui)
        ok("读数来自 /api/status 的 cloud 段", 'st["cloud"] = _cl2.snapshot()' in wui)
        ok("配置默认段有 cloud（enabled 默认 False）",
           '"cloud": {' in cfg_py and '"enabled": False' in cfg_py)
        ok("接口契约文档在（可直接发给对方）", os.path.isfile(doc) and "POST" in open(doc, encoding="utf-8").read())

        print("== E. 阴性对照 ==")
        use({"enabled": True, "persona_url": "https://example.com/hook"})
        calls2 = []
        cloud.urllib.request.urlopen = lambda *a, **k: (calls2.append(1), FakeResp(500))[1]
        try:
            cloud.upload("persona", {"a": 1}, dry=False)
        except Exception:
            pass
        ok("阴性对照：真发路径确实会发请求（说明前面的『不发』不是恒真）", len(calls2) == 1, len(calls2))
        cloud.urllib.request.urlopen = real_urlopen
    finally:
        cfgmod.get_config = real_get
        cloud.urllib.request.urlopen = real_urlopen
        socket.getaddrinfo = real_gai
        socket.create_connection = real_conn

    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
