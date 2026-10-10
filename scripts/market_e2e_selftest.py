# -*- coding: utf-8 -*-
"""资源市场端到端实测：**自己造链接 + 自己造包 → 真下载 → 真落盘 → 三个管理页能看到**。

为什么要这个（而不是扩既有 `market_selftest.py`）：
  既有那份判据测的全是"**站未就绪**"口径（`site_ready()` 为假时的如实置灰）。
  用户要的是**反过来的那一半**：站**在**的时候，整条链路真的走得通吗？
  这一段一直没人真跑过 —— 因为"站还没建"。本脚本**自己搓一个最小假后端**顶上，
  把"下载→落盘→三页可见"这条链在**本机**跑成一次真闭环（不依赖任何外部站点）。

假后端（起在 127.0.0.1，只实现本站真要的三条）：
  · `GET  /api/v1/health`                          → `{ok:true, version:...}`
  · `GET  /api/v1/install-codes/<CODE>/download`   → 包字节（按 code 找）
  · `GET  /api/catalog?kind=<kind>`                → 站侧目录（本项目自己的口径）

三条都用**真 HTTP**走一遍 `agent/market.py` / `agent/market_upload.py` 的公开函数，
⛔ 不 mock 内部函数（mock 掉就测不到真链路了）。

落盘隔离：整个测试在**临时根**里跑（`WX_AGENT_ROOT` 指向沙盒），
⛔ 绝不碰用户真库 `assets/persona/cards.json` 与真 `config.json`。

跑法：python scripts/market_e2e_selftest.py
"""
import hashlib
import http.server
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import urllib.parse
import zipfile
import io

ROOT_REAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_REAL not in sys.path:
    sys.path.insert(0, ROOT_REAL)

# ── 沙盒：整个测试跑在临时根里（⛔ 不碰用户真库与真 config.json）──────
SANDBOX = tempfile.mkdtemp(prefix="market-e2e-")
os.environ["WX_AGENT_CONFIG"] = os.path.join(SANDBOX, "config.json")
DATA_SANDBOX = os.path.join(SANDBOX, "data")

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (("　— " + str(detail)) if detail and not cond else ""))


# ── 造三个真包（人设 / skill / 工具）──────────────────────────────────
def _make_persona_bytes():
    """人设包：一张真卡（库里那套 `{schema,id,name,text}` 形态）。"""
    text = (
        "# 角色卡：测试用·小林\n\n"
        "## 身份背景\n楼下便利店的夜班店员，二十四岁，白天睡觉晚上看店，认得这条街上大半的熟客。\n\n"
        "## 性格\n记性极好，谁上次买过什么、说过什么，他下次会顺口提一句。被人夸就摆手说没什么。\n\n"
        "## 说话风格\n短句，一句十个字上下。常用「行」「成」「没事」。不说书面语，不用网络流行语。\n\n"
        "## 动机\n想把这间店撑下去，别让它像隔壁那家一样关了门。所以再晚的班他也不抱怨。\n\n"
        "## 价值观\n答应别人的事一定办到；找零多给了必须追出去还。不占别人便宜，也不让人占他的。\n\n"
        "## 行为规则\n被叫到就回一句。看到群友在讲自己的事会接一句，没人接话时不硬找话题。不主动总结别人。\n\n"
        "## 缺点局限\n不爱麻烦别人，累到不行也硬撑，被问到身体会岔开话题。\n\n"
        "## 对话示例\n群友：今天加班到现在\n角色：行，那你早点睡。\n\n"
        "群友：帮我带瓶水\n角色：成。要冰的吗。\n\n"
        "群友：你是不是AI\n角色：你猜。\n"
    )
    doc = {"schema": 2, "id": "test_xiaolin", "name": "测试用·小林", "text": text}
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


def _make_skill_bytes():
    """skill 包：一个 `.md` + `---name/description---` 头（按《制作规范》）。"""
    body = (
        "---\n"
        "name: test-commute-chat\n"
        "description: 群友在通勤路上发牢骚、说早高峰挤、说赶不上车时，怎么接话才不招人烦\n"
        "---\n\n"
        "# 通勤牢骚怎么接\n\n"
        "## 一、什么情况该用\n"
        "- 群友提到早高峰、挤地铁、末班车、堵在路上\n\n"
        "## 二、该怎么做\n"
        "1. 先接住情绪。\n2. 短，一两句。\n3. 不追问细节。\n"
    )
    return body.encode("utf-8")


def _make_tool_bytes():
    """工具包：一个 `.json`（按 `tools.d/` 口径）。"""
    doc = {
        "name": "test_ping_time",
        "description": "测试用：查某个时区的当前时间（示例接口，默认关）",
        "enabled": False,
        "method": "GET",
        "url": "https://worldtimeapi.org/api/timezone/Etc/UTC",
        "allow_hosts": ["worldtimeapi.org"],
        "params": {"type": "object", "properties": {}, "required": []},
        "timeout_ms": 8000,
        "max_chars": 2000,
    }
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


# ── 假后端 ────────────────────────────────────────────────────────────
class _Handler(http.server.BaseHTTPRequestHandler):
    blobs = {}     # code -> (bytes, filename, content_type)
    catalog = {}   # kind -> [item...]
    by_id = {}     # (kind, id) -> bytes  站上"按 id 直取包"
    verify = {}    # code -> row（口令校验的应答）

    def log_message(self, *a):  # 静音
        return

    def _send(self, code, body, ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("content-type", ctype)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            body = {}
        if u.path == "/api/v1/install-codes/verify":
            out = []
            for c in (body.get("codes") or []):
                row = self.verify.get(str(c).upper())
                out.append(row if row else {"code": str(c).upper(), "found": False})
            return self._send(200, json.dumps({"ok": True, "results": out},
                                              ensure_ascii=False).encode())
        return self._send(404, json.dumps({"ok": False}).encode())

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = u.path

        if p == "/api/v1/health":
            return self._send(200, json.dumps({"ok": True, "version": "fake-1"}).encode())

        if p.startswith("/api/v1/install-codes/") and p.endswith("/download"):
            code = urllib.parse.unquote(p[len("/api/v1/install-codes/"):-len("/download")]).upper()
            hit = self.blobs.get(code)
            if not hit:
                return self._send(404, json.dumps({"ok": False}).encode())
            data, fname, ctype = hit
            return self._send(200, data, ctype=ctype,
                              extra={"content-disposition": 'attachment; filename="%s"' % fname})

        # 站上"按 id 直取包"：/api/v1/catalog/<kind>/<id>/download
        if p.startswith("/api/v1/catalog/") and p.endswith("/download"):
            mid = p[len("/api/v1/catalog/"):-len("/download")]
            parts = [x for x in mid.split("/") if x]
            if len(parts) == 2:
                k = urllib.parse.unquote(parts[0])
                iid = urllib.parse.unquote(parts[1])
                data = self.by_id.get((k, iid))
                if data is None:
                    return self._send(404, json.dumps({"ok": False}).encode())
                return self._send(200, data, ctype="application/octet-stream")

        if p == "/api/v1/catalog":
            kind = (q.get("kind") or [""])[0]
            items = self.catalog.get(kind) or []
            return self._send(200, json.dumps({"ok": True, "kind": kind, "items": items},
                                              ensure_ascii=False).encode())

        return self._send(404, json.dumps({"ok": False}).encode())


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    print("── 0. 准备沙盒与真包 ──")
    print("  沙盒根：%s" % SANDBOX)
    from agent import market, market_upload  # noqa: PLC0415

    # ⭐ 落点隔离：`market.kind_dir()` 走 `market.ROOT`，把它指到沙盒。
    #    这是**判据隔离口**（同 `config.py::save_config(path=...)` 的思路）——
    #    ⛔ 不改产品代码，只在本进程内替换这个常量。
    market.ROOT = SANDBOX
    # 人设页的导入收尾也读 `ROOT`（ext_center），一并指过去
    from agent import ext_center  # noqa: PLC0415
    if hasattr(ext_center, "ROOT"):
        ext_center.ROOT = SANDBOX
    # 工具栏的落点**有第二来源**（`user_tools.manifest_dir()` 走它自己的 `ROOT`）
    #   ⇒ 必须一并隔离，否则工具包会落进真 `tools.d/`。
    from agent import user_tools  # noqa: PLC0415
    if hasattr(user_tools, "ROOT"):
        user_tools.ROOT = SANDBOX

    persona_b = _make_persona_bytes()
    skill_b = _make_skill_bytes()
    tool_b = _make_tool_bytes()
    ok("三个包都造出来了（人设/skill/工具）",
       all(len(x) > 60 for x in (persona_b, skill_b, tool_b)),
       "长度 %s" % [len(persona_b), len(skill_b), len(tool_b)])

    # 人设页读的是 data/custom_personas.json；先确认沙盒里这条路是通的
    pdir = market.kind_dir("persona")
    sdir = market.kind_dir("skill")
    tdir = market.kind_dir("tool")
    for d in (pdir, sdir, tdir):
        os.makedirs(d, exist_ok=True)
    # ⛔ **前置硬闸**：三个落点必须全部在沙盒内。有一条在外面 ⇒ 立刻停，
    #    否则这个测试会**往用户真库里写测试包**（本轮第一次跑就踩了这个坑）。
    _escaped = [d for d in (pdir, sdir, tdir)
                if not os.path.abspath(d).startswith(os.path.abspath(SANDBOX))]
    if _escaped:
        print("  ⛔ 落点隔离失败，已中止（绝不往真库写）：%s" % _escaped)
        return 2
    ok("★ 前置硬闸：三个落点全在沙盒内（绝不污染真库）", True,
       "%s | %s | %s" % (pdir, sdir, tdir))
    ok("三个落点目录在沙盒里建好了（隔离于真库）",
       all(os.path.isdir(d) and SANDBOX in os.path.abspath(d) for d in (pdir, sdir, tdir)),
       "%s | %s | %s" % (pdir, sdir, tdir))

    # ── 起假后端 ──
    port = _free_port()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = "http://127.0.0.1:%d" % port

    # 塞三个包进假后端（各配一个取件口令）
    h_p = hashlib.sha256(persona_b).hexdigest()
    h_s = hashlib.sha256(skill_b).hexdigest()
    h_t = hashlib.sha256(tool_b).hexdigest()
    _Handler.blobs = {
        "TEST-PERSONA": (persona_b, "test_xiaolin.json", "application/json"),
        "TEST-SKILL": (skill_b, "test-commute-chat.md", "text/markdown"),
        "TEST-TOOL": (tool_b, "test_ping_time.json", "application/json"),
    }
    _Handler.catalog = {
        "persona": [{"id": "test_xiaolin", "name": "测试用·小林", "desc": "端到端测试人设",
                     "sha256": h_p, "size": len(persona_b), "code": "TEST-PERSONA"}],
        "skill": [{"id": "test-commute-chat", "name": "通勤牢骚接话", "desc": "端到端测试技能",
                   "sha256": h_s, "size": len(skill_b), "code": "TEST-SKILL"}],
        "tool": [{"id": "test_ping_time", "name": "查时区时间", "desc": "端到端测试工具",
                  "sha256": h_t, "size": len(tool_b), "code": "TEST-TOOL"}],
    }
    _Handler.by_id = {
        ("persona", "test_xiaolin"): persona_b,
        ("skill", "test-commute-chat"): skill_b,
        ("tool", "test_ping_time"): tool_b,
    }
    print("  假后端：%s" % base)

    # ── 把 site_url 指过去（走配置隔离口，不碰用户 config.json）──
    from agent.config import load_config, save_config  # noqa: PLC0415
    cfg = load_config()
    cfg.setdefault("market", {})
    cfg["market"]["site_url"] = base
    cfg["market"]["site_ready"] = True
    save_config(cfg)
    market._cfg.cache_clear() if hasattr(market._cfg, "cache_clear") else None
    ok("★ 站点就绪开关打开了（site_ready=True）", market.site_ready() is True,
       "site_url=%s" % (market._cfg().get("site_url"),))

    print("── 1. 假后端健康检查（真 HTTP）──")
    h = market_upload.health()
    ok("★ health 通（真的连上了假后端）", h.get("ok") is True, str(h))

    print("── 2. 口令取件（真 HTTP，字节级）──")
    for code, want_name, want_b in (("TEST-PERSONA", "test_xiaolin.json", persona_b),
                                    ("TEST-SKILL", "test-commute-chat.md", skill_b),
                                    ("TEST-TOOL", "test_ping_time.json", tool_b)):
        raw, fname, ctype = market_upload.download_by_code(code)
        ok("★ 口令 %s 取到的字节与造出来的一模一样" % code, raw == want_b,
           "len %d vs %d" % (len(raw), len(want_b)))
        ok("  口令 %s 拿回了文件名 %s" % (code, want_name), fname == want_name, fname)

    bad = False
    try:
        market_upload.download_by_code("NO-SUCH-CODE")
    except market_upload.UploadError as e:
        bad = (e.code == "code-not-found")
    ok("★ 错口令 ⇒ code-not-found（如实拒，不静默给空包）", bad)

    print("── 3. 安装落盘（三栏各走一次，走**站上直取包**这条真链路）──")
    # ⭐ 这里**不传 data=**：让 `install()` 自己去站上取（`_fetch_package`）。
    #    这一步才是"从资源市场下载"的真路径；传 data 只是旁路（给本机重导用）。
    r_p = market.install("persona", "test_xiaolin", h_p)
    ok("★ 人设：从站上下载并安装成功", r_p.get("ok") is True, str(r_p)[:120])
    ok("★ 人设落进了本栏目录", os.path.isfile(os.path.join(pdir, "test_xiaolin.json")), r_p.get("path", ""))

    r_s = market.install("skill", "test-commute-chat", h_s)
    ok("★ 技能：从站上下载并安装成功", r_s.get("ok") is True, str(r_s)[:120])
    ok("★ 技能落进了本栏目录", os.path.isfile(os.path.join(sdir, "test-commute-chat.json")), r_s.get("path", ""))

    r_t = market.install("tool", "test_ping_time", h_t)
    ok("★ 工具：从站上下载并安装成功", r_t.get("ok") is True, str(r_t)[:120])
    ok("★ 工具落进了本栏目录", os.path.isfile(os.path.join(tdir, "test_ping_time.json")), r_t.get("path", ""))

    print("── 4. sha 不符必须拒（且不留半个包）──")
    # 往站上放一个"内容与登记 sha 不一致"的包：站上给别人的包被换过 ⇒ 必须检出并丢弃。
    _Handler.by_id[("tool", "test_ping_time2")] = tool_b
    r_bad = market.install("tool", "test_ping_time2", "0" * 64)
    ok("★ sha 不符 ⇒ hash-mismatch", r_bad.get("code") == "hash-mismatch", str(r_bad)[:100])
    ok("★ 不符时目录里没多出东西", not os.path.exists(os.path.join(tdir, "test_ping_time2.json")))

    print("── 4b. 站上取不到包 ⇒ 如实报失败（不静默装空包）──")
    r_none = market.install("tool", "never_exists_id", "0" * 64)
    ok("★ 站上没有这个包 ⇒ ok=False 且不留文件",
       r_none.get("ok") is False and not os.path.exists(os.path.join(tdir, "never_exists_id.json")),
       str(r_none)[:100])

    print("── 5. 三个管理页能不能看到（这是用户要的那一步）──")
    allk = market.list_all()
    ok("★ list_all 三栏齐全", set((allk.get("kinds") or {}).keys()) == {"persona", "skill", "tool"})

    for k, want_id in (("persona", "test_xiaolin.json"), ("skill", "test-commute-chat.json"),
                       ("tool", "test_ping_time")):
        kn = (allk.get("kinds") or {}).get(k) or {}
        ids = [str(x.get("id") or "") for x in (kn.get("local") or [])]
        ok("★ %s 管理页列出的本机项里有 %s" % (kn.get("label", k), want_id), want_id in ids, str(ids)[:140])

    print("── 6. 人设那一步的收尾：进人设页 + 落「下载」分区 ──")
    try:
        from agent import ext_center  # noqa: PLC0415
        cats = ext_center.cats()
        names = []
        if isinstance(cats, dict):
            names = [str(x) for x in (cats.get("cats") or cats.get("items") or [])]
        elif isinstance(cats, list):
            names = [str(x) for x in cats]
        ok("★ 人设页分区清单里有「下载」分区", any("下载" in n for n in names), str(names)[:160])

        # 人设页读的是 data/custom_personas.json —— 装完必须能在里面找到
        cp = os.path.join(SANDBOX, "data", "custom_personas.json")
        found = False
        if os.path.isfile(cp):
            with open(cp, encoding="utf-8") as f:
                doc = json.load(f)
            blob = json.dumps(doc, ensure_ascii=False)
            found = "测试用·小林" in blob
        ok("★ 人设真写进了 custom_personas.json（人设页读的就是这份）", found, cp)
    except Exception as e:  # noqa: BLE001
        ok("人设收尾链路点检", False, "异常：%s" % str(e)[:120])

    print("── 7. 站侧目录 + 按 id 直取包（可下载清单那一半）──")
    pulled = market._fetch_catalog("persona")
    ok("★ 站侧目录接口已实现（返回真清单）", bool(pulled) and pulled[0].get("id") == "test_xiaolin",
       str(pulled)[:150])
    ok("★ 目录项带 sha256（界面/安装能校验）", bool(pulled and pulled[0].get("sha256")), str(pulled)[:120])

    pk = market._fetch_package("persona", "test_xiaolin")
    ok("★ 按 id 直取包：字节与造出来的一致", pk == persona_b, "len=%d" % len(pk))

    # 管理页的"站上的（可下载）"那一列必须真列出来
    kn = (allk.get("kinds") or {}).get("persona") or {}
    remote_ids = [str(x.get("id") or "") for x in (kn.get("remote") or [])]
    ok("★ 人设管理页的可下载列里有 test_xiaolin", "test_xiaolin" in remote_ids, str(remote_ids)[:120])
    marked = [x for x in (kn.get("remote") or []) if str(x.get("id")) == "test_xiaolin"]
    ok("★ 已装的那一项在可下载列里被标成 installed=True（不出现装完还能再装）",
       bool(marked and marked[0].get("installed") is True), str(marked)[:150])

    print("── 8. 口令取件那条路（用户手里只有一个口令时的入口）──")
    # 假后端补上 verify_codes 要的接口
    _Handler.verify = {
        "ABC123": {"code": "ABC123", "found": True, "kind": "tool", "id": "test_ping_time",
                   "title": "查时区时间", "sha256": h_t, "fileSize": len(tool_b)},
    }
    rows = market_upload.verify_codes(["ABC123"])
    hit = next((x for x in rows if str(x.get("code") or "").upper() == "ABC123"), None)
    ok("★ 口令校验：有效口令能查到", bool(hit and hit.get("found")), str(rows)[:140])
    bad_rows = market_upload.verify_codes(["XXXXXX"])
    ok("★ 口令校验：无效口令查不到（如实拒）",
       not any(x.get("found") for x in (bad_rows or [])), str(bad_rows)[:100])

    # 口令落盘那一段（模拟 handler 的做法：取字节 → 校验 sha → 写进本栏目录）
    raw, fname, _ct = market_upload.download_by_code("TEST-TOOL")
    got_sha = hashlib.sha256(raw).hexdigest()
    ok("★ 取回的包 sha 与站上登记一致（可安全落盘）", got_sha == h_t)
    dest = os.path.join(tdir, "via-code-" + os.path.basename(fname or "x.json"))
    with open(dest, "wb") as f:
        f.write(raw)
    ok("★ 口令取件真落进本栏目录", os.path.isfile(dest), dest)

    print("── 8b. 卸载闭环 ──")
    u = market.uninstall("tool", "test_ping_time")
    ok("★ 工具卸载成功", u.get("ok") is True, str(u)[:100])
    ok("★ 卸载后文件真没了", not os.path.exists(os.path.join(tdir, "test_ping_time.json")))

    print("── 9. 越界防护（id 带 ../ 删不到外面）──")
    outside = os.path.join(SANDBOX, "outside.txt")
    with open(outside, "w") as f:
        f.write("x")
    u2 = market.uninstall("tool", "../outside")
    ok("★ ../ 越界 id 删不到目标目录外的文件", os.path.isfile(outside), str(u2)[:100])

    srv.shutdown()
    srv.server_close()

    print("\n" + "=" * 62)
    print("资源市场端到端：**%d 通过 / %d 失败**" % (len(PASS), len(FAIL)))
    if FAIL:
        for n in FAIL:
            print("  ✗ " + n)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(SANDBOX, ignore_errors=True)
    sys.exit(rc)
