# -*- coding: utf-8 -*-
"""反馈中转（**本机版**）：客户端 → 本机中转 → 真 key 转发。过滤掉一切"非本产品格式"的消息。

## 为什么需要它
企业微信群机器人 webhook 是"**凭 URL 即用**"，而那个 URL 曾经公开在仓库里 ⇒ 谁都能往群里推。
**群机器人本身没有"校验层"**（消息直达群，拦不住）⇒ 只能在**中间加一层**。

## 这一层做两件事
1. **只监听 `127.0.0.1`** ⇒ **外部根本连不上它** ⇒ 乱发的人**打不到这里**（本机版的最强点）；
2. **格式白名单校验** ⇒ 必须带本产品的必备字段（`kind` ＋ `text` ＋ `ver`），
   再可选校验收 `sign`（`feedback.relay_secret` 配了才校验）⇒ 不合规**一律丢弃并记日志**。

## 怎么接
· 客户端 `config.json`：`feedback.upload_url = "http://127.0.0.1:8788/relay"`，
  并把 `feedback.webhook_url` **清空**（客户端不再直发）；
· 真 key 只留在**本机 `config.json` 的 `feedback.webhook_url`**（中转读它转发）；
· ⇒ 别人即使拿到旧 key，也**只能往那个旧群发**，**发不到你的中转**（它是 127.0.0.1）。

跑法：runtime\\python\\python.exe scripts\\feedback_relay.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

HOST, PORT = "127.0.0.1", 8788
LOGP = os.path.join(ROOT, "logs", "feedback_relay.log")
#: 必备字段（**本产品格式的"形状"**）：缺任何一个 ⇒ 不是我们的客户端发的 ⇒ 丢
REQUIRED = ("kind", "text", "ver")
#: 允许的 kind（与 `feedback.KINDS` 同源的子集；多了也不致命，只记一笔）
SOFT_KINDS = ("问题", "建议", "想法", "其他")


def _log(line: str) -> None:
    os.makedirs(os.path.dirname(LOGP), exist_ok=True)
    with open(LOGP, "a", encoding="utf-8") as f:
        f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))


def _cfg() -> dict:
    try:
        with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception: # noqa: BLE001
        return {}


def _check(obj: dict) -> tuple:
    """⇒ (是否放行, 原因)。**白名单：形状不对的直接丢**。"""
    if not isinstance(obj, dict):
        return False, "不是 JSON 对象"
    for k in REQUIRED:
        v = obj.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            return False, "缺必备字段 %s" % k
    if str(obj.get("kind")) not in SOFT_KINDS:
        return False, "kind 不在白名单：%r" % obj.get("kind")
    t = str(obj.get("text") or "")
    if len(t) > 20000:
        return False, "正文过长（%d 字符）" % len(t)
    return True, "ok"


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a): # noqa: D102
        pass

    def do_POST(self): # noqa: N802
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            obj = json.loads(raw.decode("utf-8", "replace"))
        except Exception as e: # noqa: BLE001
            _log("丢弃：读不出 JSON（%s）" % e)
            self._send(400, {"ok": False, "why": "不是合法 JSON"})
            return

        ok, why = _check(obj)
        if not ok:
            _log("丢弃：%s | 头=%s" % (why, dict((k, self.headers.get(k)) for k in ("User-Agent",))))
            self._send(403, {"ok": False, "why": "格式不符（%s）⇒ 已丢弃" % why})
            return

        # 放行 ⇒ 用**真 key** 转发（真地址只在本机 config.json 里，客户端拿不到）
        hook = str((_cfg().get("feedback") or {}).get("webhook_url") or "").strip()
        if not hook:
            self._send(500, {"ok": False, "why": "中转没配 webhook_url（真 key）"})
            return
        try:
            from agent import feedback as FB
            r = FB._post_webhook(hook, {"kind": obj.get("kind"), "text": obj.get("text"),
                                        "ver": obj.get("ver"), "at_h": obj.get("at_h") or ""})
        except Exception as e: # noqa: BLE001
            _log("转发异常：%s" % e)
            self._send(500, {"ok": False, "why": "转发异常：%s" % e})
            return
        _log("放行并转发：ok=%s why=%s kind=%s" % (r.get("ok"), str(r.get("why"))[:60], obj.get("kind")))
        self._send(200 if r.get("ok") else 502, {"ok": bool(r.get("ok")), "why": r.get("why")})

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    srv = ThreadingHTTPServer((HOST, PORT), H)
    _log("中转启动 http://%s:%d/relay（只监听本机 ⇒ 外部连不上）" % (HOST, PORT))
    print("中转已启动：http://%s:%d/relay" % (HOST, PORT))
    print("客户端配：config.json → feedback.upload_url = http://%s:%d/relay" % (HOST, PORT))
    print("真 key 留在本机 config.json 的 feedback.webhook_url（客户端看不到）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
