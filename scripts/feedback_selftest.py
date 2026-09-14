# -*- coding: utf-8 -*-
"""「反馈」栏判据（2026-09-14 · 用户：「反馈功能需要在左导航单开一栏…让用户直接在控制台里面填，
然后自动提交就好，没必要让用户去邮箱那儿填，程序自动整理并把用户的诉求发邮件」）。

守四件事：
  ① **栏位齐全**：左导航有入口、有 `data-sec` 分区、分区在 `sec-send` 之前闭合（与其它分区同规矩）；
  ② **三态如实**：没配通道 ⇒ `queued`（明说没发出去）；配了 ⇒ `sent`；**绝不留假成功**；
  ③ **个人信息不进代码/包**：收件人邮箱、SMTP 授权码只能在 config.json（gitignore），
     代码与 config.example.json 里不许出现真实邮箱；
  ④ **发出去的东西是对的**：邮件正文=程序自动整理的诉求（类型/时间/版本/内容/联系方式），
     SMTP 走的是 SSL/STARTTLS 而非明文。

用法：py -3 scripts/feedback_selftest.py
"""
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import feedback as FB            # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


def src(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


HTML = src("agent/console_html.py")

print("── A. 左导航单开一栏 ──")
ok("导航有 #sec-feedback 入口", 'href="#sec-feedback"' in HTML)
ok("导航文字是「反馈」", re.search(r'href="#sec-feedback".*?<span class="lb">反馈</span>', HTML, re.S) is not None)
ok("有独立分区且带 data-sec", 'id="sec-feedback" class="card" data-sec' in HTML)
_i = HTML.index('id="sec-feedback"')
_j = HTML.index('id="sec-send"')
_seg = HTML[_i:_j]
ok("分区在「发送限制」之前且已闭合", "</section>" in _seg)
ok("表单四件齐全（类型/内容/联系方式/提交）",
   all(k in _seg for k in ('id="fbKind"', 'id="fbText"', 'id="fbContact"', 'id="fbSubmit"')))

print("── B. 配置项全部映射到界面（用户：所有功能都要能映射）──")
for key in ("feedback.to", "feedback.upload_url", "feedback.smtp.user",
            "feedback.smtp.password", "feedback.smtp.host", "feedback.smtp.port"):
    ok("界面有 %s" % key, ('data-cfg="%s"' % key) in _seg)
ok("有保存按钮", "保存设置（反馈）" in _seg)

print("── C. 三态如实：没通道就必须说「没发出去」──")
_saved = FB.FEEDBACK_FILE
_tmp = os.path.join(ROOT, "data", "feedback_selftest.jsonl")
FB.FEEDBACK_FILE = _tmp
try:
    try:
        os.remove(_tmp)
    except Exception:
        pass
    _real_cfg = FB._cfg
    FB._cfg = lambda: {}                      # 模拟"什么都没配"
    r1 = FB.submit("建议", "判据用的假反馈：希望它更好用", "tester@example.com", {"wechat": "4.1.15.8"})
    ok("没配通道 ⇒ state=queued（不是假的 sent）", r1.get("state") == "queued", str(r1.get("state")))
    ok("并如实说清原因", bool(r1.get("why")), str(r1.get("why"))[:50])
    ok("本地确实存下来了", len(FB.pending()) == 1, str(len(FB.pending())))
    ok("stats 也认「未配置」", FB.stats().get("can_send") is False)

    # 配一个假的中转网址（用本地 HTTP 服务接收）→ 应该真的发出去
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            got["body"] = self.rfile.read(n).decode("utf-8", "replace")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    FB._cfg = lambda: {"upload_url": "http://127.0.0.1:%d/fb" % port, "to": "", "smtp": {}}
    r2 = FB.submit("问题", "判据用的假反馈：这样能发出去吗")
    ok("配了中转网址 ⇒ state=sent", r2.get("state") == "sent", str(r2.get("state")))
    ok("对方真的收到了（POST 命中）", "text" in (got.get("body") or ""), (got.get("body") or "")[:60])
    ok("收到的正文是程序整理过的（含类型/版本/诉求）",
       "【群相 · 用户反馈】" in (got.get("body") or "") and "判据用的假反馈" in (got.get("body") or ""))
    ok("发送后不再算待发", len(FB.pending()) == 1, "剩 %d 条待发" % len(FB.pending()))
    _fl = FB.flush()
    ok("补发把排队的那条也发出去了（剩 0 条）", _fl.get("left") == 0, str(_fl))

    # 邮件通道：monkeypatch smtplib，检查确实是 SSL + 登录 + 发给了正确收件人
    import smtplib as _sm
    box = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            box["host"] = host
            box["port"] = port
            box["ssl"] = context is not None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            box["login"] = (u, p)

        def sendmail(self, frm, to, body):
            box["send"] = (frm, to, body)

    _orig = _sm.SMTP_SSL
    _sm.SMTP_SSL = FakeSMTP
    try:
        FB._cfg = lambda: {"to": "a@example.com, b@example.com", "upload_url": "",
                           "smtp": {"host": "smtp.qq.com", "port": 465, "user": "me@qq.com", "password": "授权码"}}
        r3 = FB.submit("想法", "判据用的假反馈：邮件这条路")
        ok("邮箱配齐 ⇒ 走邮件且 state=sent", r3.get("state") == "sent" and r3.get("via") == "smtp",
           "%s/%s" % (r3.get("state"), r3.get("via")))
        ok("用 465 + SSL（不是明文）", box.get("port") == 465 and box.get("ssl") is True, str(box.get("port")))
        ok("登录用的是配置里的账号", box.get("login") == ("me@qq.com", "授权码"), str(box.get("login")))
        ok("收件人是配置里那两个（逗号分隔被拆开）", box.get("send", ("", [], ""))[1] == ["a@example.com", "b@example.com"],
           str(box.get("send", ("", [], ""))[1]))
        # 主题用 Header 编码（中文必须编码）⇒ 判据要按 MIME 解码后再看，不能直接找字面量
        from email import message_from_string
        from email.header import decode_header
        _raw = box.get("send", ("", [], ""))[2]
        _msg = message_from_string(_raw)
        _subj = "".join((t.decode(enc or "utf-8") if isinstance(t, bytes) else t)
                        for t, enc in decode_header(_msg.get("Subject") or ""))
        ok("邮件标题带 [群相反馈] 前缀", _subj.startswith("[群相反馈]"), _subj[:60])
    finally:
        _sm.SMTP_SSL = _orig
        FB._cfg = _real_cfg
finally:
    FB.FEEDBACK_FILE = _saved
    try:
        os.remove(_tmp)
    except Exception:
        pass

print("── D. 个人信息不进代码/包 ──")
ok("代码里没有真实收件邮箱", "ptmourning@" not in HTML and "gasterhhh@" not in src("agent/feedback.py"))
ok("代码里没有真实邮箱（全仓源码）",
   not any("ptmourning@" in src(p) for p in ("agent/config.py", "agent/webui.py", "config.example.json")))
ok("示例配置里 feedback 段是空的（不给真实地址）",
   "ptmourning@" not in src("config.example.json") and "gasterhhh@" not in src("config.example.json"))
ok("程序把诉求整理成人类可读正文（含类型/时间/版本/联系方式）",
   all(k in FB.compose({"kind": "建议", "text": "x", "at_h": "2026-09-14 10:00:00", "ver": "b.x",
                        "contact": "c", "env": {}}) for k in ("类型：", "时间：", "版本：", "联系方式：")))

print("")
print("反馈栏判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
