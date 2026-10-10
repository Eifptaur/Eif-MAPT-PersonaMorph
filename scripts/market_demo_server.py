# -*- coding: utf-8 -*-
"""演示用：起一个真的资源市场后端（本机），自己造包 + 造链接。

用法：python _scratch/_demo_market_server.py
它会打印一个本机地址；把该地址填进 config.json 的 market.site_url，
再打开软件的「资源市场」，就能看到这三样并能一键下载（三栏各一个）。

也可以直接 curl 那些链接验证。
"""
import hashlib
import http.server
import json
import os
import socket
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PERSONA_TEXT = (
    "# 角色卡：演示·陈师傅\n\n"
    "## 身份背景\n开夜班出租车二十年，认得城里每一条小巷。车上常年挂着妻子编的中国结。\n\n"
    "## 性格\n话少，但乘客想聊他句句接得住。遇事不慌，堵车也不骂人。\n\n"
    "## 说话风格\n短句，带点口音。常说「成」「您放心」。不说网络流行语，不飙书面语。\n\n"
    "## 动机\n想让儿子别再为他熬夜跑车担心，所以再苦的班他也说「好着呢」。\n\n"
    "## 价值观\n说到的路费就那个数，绕远也不多收。捡到乘客落的东西一定想办法还。\n\n"
    "## 行为规则\n被叫到就回，话短。看到群友叹气会接一句。没人说话时不硬找话题。不主动劝人。\n\n"
    "## 缺点局限\n腰伤拖了多年不肯去医院，被问身体就说「老毛病」。不会用智能手机导航以外的功能。\n\n"
    "## 对话示例\n群友：今天加班到现在\n角色：成，那您慢点回。\n\n"
    "群友：导航把我导到死路了\n角色：您放心，往前走两百米有个口。\n\n"
    "群友：你是不是AI\n角色：您猜。\n"
)

SKILL_TEXT = (
    "---\n"
    "name: demo-late-shift\n"
    "description: 群友深夜还在加班、说睡不着、说刚下班时，怎么接话才不招人烦\n"
    "---\n\n"
    "# 深夜加班怎么接\n\n"
    "## 一、什么情况该用\n"
    "- 群友提到凌晨、通宵、刚下班\n\n"
    "## 二、该怎么做\n"
    "1. 先接住情绪。\n"
    "2. 短，一两句。\n"
    "3. 不劝人早睡。\n"
)

P = json.dumps({"schema": 2, "id": "demo_chen", "name": "演示·陈师傅", "text": PERSONA_TEXT},
               ensure_ascii=False).encode("utf-8")
S = SKILL_TEXT.encode("utf-8")
T = json.dumps({
    "name": "demo_utc_time",
    "description": "演示用：查 UTC 当前时间（示例接口，默认关）",
    "enabled": False, "method": "GET",
    "url": "https://worldtimeapi.org/api/timezone/Etc/UTC",
    "allow_hosts": ["worldtimeapi.org"],
    "params": {"type": "object", "properties": {}, "required": []},
    "timeout_ms": 8000, "max_chars": 2000,
}, ensure_ascii=False).encode("utf-8")

HP, HS, HT = (hashlib.sha256(x).hexdigest() for x in (P, S, T))

CATALOG = {
    "persona": [{"id": "demo_chen", "name": "演示·陈师傅", "desc": "夜班出租车司机",
                 "sha256": HP, "size": len(P), "code": "DEMO01"}],
    "skill": [{"id": "demo-late-shift", "name": "深夜加班接话", "desc": "怎么回才不招人烦",
               "sha256": HS, "size": len(S), "code": "DEMO02"}],
    "tool": [{"id": "demo_utc_time", "name": "查UTC时间", "desc": "示例接口",
              "sha256": HT, "size": len(T), "code": "DEMO03"}],
}
BLOB_BY_ID = {("persona", "demo_chen"): P, ("skill", "demo-late-shift"): S,
              ("tool", "demo_utc_time"): T}
BLOB_BY_CODE = {"DEMO01": (P, "demo_chen.json"), "DEMO02": (S, "demo-late-shift.md"),
                "DEMO03": (T, "demo_utc_time.json")}
VERIFY = {c: {"code": c, "found": True,
              "kind": k, "id": CATALOG[k][0]["id"], "title": CATALOG[k][0]["name"],
              "sha256": CATALOG[k][0]["sha256"], "fileSize": CATALOG[k][0]["size"]}
          for c, k in (("DEMO01", "persona"), ("DEMO02", "skill"), ("DEMO03", "tool"))}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        return

    def _j(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _blob(self, data, fname=""):
        self.send_response(200)
        self.send_header("content-type", "application/octet-stream")
        if fname:
            self.send_header("content-disposition", 'attachment; filename="%s"' % fname)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = u.path
        if p == "/":
            return self._j({"ok": True, "site": "演示资源市场（本机）",
                            "kinds": ["persona", "skill", "tool"],
                            "endpoints": ["/api/v1/health", "/api/v1/catalog?kind=persona",
                                          "/api/v1/catalog/persona/demo_chen/download",
                                          "/api/v1/install-codes/DEMO01/download"]})
        if p == "/api/v1/health":
            return self._j({"ok": True, "version": "demo-1"})
        if p == "/api/v1/catalog":
            k = (q.get("kind") or [""])[0]
            return self._j({"ok": True, "kind": k, "items": CATALOG.get(k, [])})
        if p.startswith("/api/v1/catalog/") and p.endswith("/download"):
            parts = [x for x in p[len("/api/v1/catalog/"):-len("/download")].split("/") if x]
            if len(parts) == 2:
                blob = BLOB_BY_ID.get((parts[0], parts[1]))
                if blob is None:
                    return self._j({"ok": False}, 404)
                return self._blob(blob)
        if p.startswith("/api/v1/install-codes/") and p.endswith("/download"):
            code = p[len("/api/v1/install-codes/"):-len("/download")].upper()
            hit = BLOB_BY_CODE.get(code)
            if not hit:
                return self._j({"ok": False}, 404)
            return self._blob(hit[0], hit[1])
        return self._j({"ok": False}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            body = {}
        if u.path == "/api/v1/install-codes/verify":
            res = [VERIFY.get(str(c).upper(), {"code": str(c).upper(), "found": False})
                   for c in (body.get("codes") or [])]
            return self._j({"ok": True, "results": res})
        return self._j({"ok": False}, 404)


def main():
    port = int(os.environ.get("DEMO_PORT") or "8765")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    base = "http://127.0.0.1:%d" % port
    print("=" * 66)
    print("演示资源市场已启动（本机）")
    print("  站点地址  : %s" % base)
    print("  健康检查  : %s/api/v1/health" % base)
    print("  可下载清单: %s/api/v1/catalog?kind=persona" % base)
    print("  人设包直链: %s/api/v1/catalog/persona/demo_chen/download" % base)
    print("  口令取件  : %s/api/v1/install-codes/DEMO03/download  （口令 DEMO03）" % base)
    print("=" * 66)
    print("把「站点地址」填进 config.json 的 market.site_url + market.site_ready=true，")
    print("再打开软件的「资源市场」，三栏各能看到一样并可一键下载。Ctrl+C 停止。")
    sys.stdout.flush()
    srv.serve_forever()


if __name__ == "__main__":
    main()
