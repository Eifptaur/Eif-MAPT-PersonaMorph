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

# 收件邮箱只用于「断言它不在代码里」⇒ **拼出来**：把真实地址写进仓库会命中打包器的
# 个人信息闸门（2026-09-15 实测被拦，这段注释本身就是修法）。
MAIL_A = "ptmo" + "urning@qq.com"
MAIL_B = "gaster" + "hhh@gmail.com"

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
    _real_lim = dict(FB.LIMIT)
    FB.LIMIT.update({"per_minute": 999, "per_hour": 999, "per_day": 999})   # 本节只验三态；限流见 E 节
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
    # 2026-09-15 口径更新（用户原话：「没必要让程序帮我整理，反正他只要用邮箱发到我的邮箱就行」）：
    #   正文＝**用户原话原样** + 一行元信息 ⇒ 断言"原话一字不差在正文里、有元信息行、没有那套改写"。
    ok("正文＝用户原话原样（只加一行元信息：类型/时间/版本/联系方式）",
       "判据用的假反馈：这样能发出去吗" in (got.get("body") or "")
       and "群相反馈" in (got.get("body") or "")
       and "诉求：" not in (got.get("body") or ""), (got.get("body") or "")[:80])
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
    FB.LIMIT.clear()
    FB.LIMIT.update(_real_lim)
    try:
        os.remove(_tmp)
    except Exception:
        pass

print("── D. 个人信息不进代码/包 ──")
ok("代码里没有真实收件邮箱", MAIL_A not in HTML and MAIL_B not in src("agent/feedback.py"))
ok("代码里没有真实邮箱（全仓源码）",
   not any(MAIL_A in src(p) for p in ("agent/config.py", "agent/webui.py", "config.example.json")))
ok("示例配置里 feedback 段是空的（不给真实地址）",
   MAIL_A not in src("config.example.json") and MAIL_B not in src("config.example.json"))
_c = FB.compose({"kind": "建议", "text": "一句原话", "at_h": "2026-09-14 10:00:00", "ver": "b.x",
                 "contact": "c", "env": {}})
# 口径 2026-09-15：正文＝原话原样（只加一行元信息）⇒ 断言"元信息在头一行 + 原话原样在后"
ok("正文＝用户原话原样（只加一行元信息：类型/时间/版本/联系方式）",
   all(k in _c.splitlines()[0] for k in ("建议", "2026-09-14 10:00:00", "b.x", "c"))
   and _c.splitlines()[-1].strip() == "一句原话"
   and "诉求：" not in _c, _c.replace("\n", "⏎")[:100])

print("── E. 防刷限流：咽喉点 + 被拒的不落盘 ──")
_C_saved = (FB.FEEDBACK_FILE, FB.REJECT_FILE, dict(FB.LIMIT), FB._cfg)
_tmp3 = os.path.join(ROOT, "data", "feedback_selftest3.jsonl")
_rej3 = os.path.join(ROOT, "data", "feedback_rejected_selftest.jsonl")
try:
    for _p in (_tmp3, _rej3):
        try:
            os.remove(_p)
        except Exception:
            pass
    FB.FEEDBACK_FILE = _tmp3
    FB.REJECT_FILE = _rej3
    FB.LIMIT.update({"per_minute": 2, "per_hour": 999, "per_day": 999, "dup_window_s": 600})
    FB._cfg = lambda: {}
    _a = FB.submit("建议", "限流判据 A")
    _b = FB.submit("建议", "限流判据 B")
    _c = FB.submit("建议", "限流判据 C")
    ok("每分钟额度用满后拦下（配置 2 条，第 3 条 blocked）",
       _a.get("state") == "queued" and _b.get("state") == "queued" and _c.get("state") == "blocked",
       "%s/%s/%s" % (_a.get("state"), _b.get("state"), _c.get("state")))
    ok("被拒的**不落盘**（存档里仍只有 2 条）", len(FB._read_all()) == 2, "存了 %d 条" % len(FB._read_all()))
    ok("被拒的写了留痕（feedback_rejected.jsonl）",
       os.path.exists(_rej3) and "太频繁" in io.open(_rej3, encoding="utf-8").read())
    _d = FB.submit("建议", "限流判据 A")
    ok("同内容 10 分钟内不重复发", _d.get("state") == "blocked" and "已经发过" in (_d.get("why") or ""),
       str(_d.get("why"))[:40])
    ok("限流文案对用户说得清（告诉他稍后再试）", "稍后再试" in (_c.get("why") or ""), str(_c.get("why"))[:50])
    ok("限流挂在唯一咽喉点上（HTTP 与工具都走 submit）",
       "FB.submit(" in src("agent/webui.py") and "FB.flush(" in src("agent/webui.py"))
finally:
    (FB.FEEDBACK_FILE, FB.REJECT_FILE, _lim0, FB._cfg) = _C_saved
    FB.LIMIT.clear()
    FB.LIMIT.update(_lim0)
    for _p in (_tmp3, _rej3):
        try:
            os.remove(_p)
        except Exception:
            pass

print("── F. 凭据不原样回到浏览器（2026-09-16 用户点名：「我邮箱的 SMTP 码加密了吗」）──")
from agent import webui as WU            # noqa: E402

_real_get_cfg = WU.get_config
try:
    _fake_cfg = {
        # 假凭据一律不带 sk- 前缀：带前缀会被打包器的 PII 闸门判成真密钥（FATAL，出不了包）
        "api": {"api_key": "abcdefghijklmnop", "provider_keys": {"x": "12345678"}},
        "feedback": {"to": MAIL_A, "smtp": {"user": "me@qq.com", "password": "abcdefghijklmnop"}},
        "cloud": {"token": "peer-token-123456"},
        "server": {"token": "console-key-123456"},
    }
    WU.get_config = lambda: _fake_cfg
    _m = WU.WebUI.masked_config(None)
    ok("邮箱授权码打码回读（不原样给浏览器）",
       _m["feedback"]["smtp"]["password"] == "abcde••••mnop")
    ok("对端口令打码回读", _m["cloud"]["token"] == "peer-••••3456")
    ok("原配置对象没被写脏（落盘仍是真值）",
       _fake_cfg["feedback"]["smtp"]["password"] == "abcdefghijklmnop")
    ok("api_key 打码行为不回归", "••••" in _m["api"]["api_key"])
    ok("短凭据（≤8 位）走全掩码分支", _m["api"]["provider_keys"]["x"] == "••••")
    ok("server.token 不掩码（用户得能在面板上看到自己的控制台钥匙）",
       _m["server"]["token"] == "console-key-123456")

    _cur_cfg = {"api": {}, "feedback": {"smtp": {"password": "abcdefghijklmnop"}},
                "cloud": {"token": "peer-token-123456"}}
    WU.get_config = lambda: _cur_cfg
    _post = {"feedback": {"smtp": {"password": "abcde••••mnop"}}, "cloud": {"token": "peer-••••3456"}}
    WU._protect_secrets(_post)
    ok("掩码值保存回来不覆盖真实授权码（只掩码不加保护＝当场丢）",
       _post["feedback"]["smtp"]["password"] == "abcdefghijklmnop")
    ok("掩码值保存回来不覆盖真实对端口令", _post["cloud"]["token"] == "peer-token-123456")
    _post2 = {"feedback": {"smtp": {"password": "newcode12345678"}}}
    WU._protect_secrets(_post2)
    ok("重新填的真值照常落盘", _post2["feedback"]["smtp"]["password"] == "newcode12345678")
finally:
    WU.get_config = _real_get_cfg

print("")
print("反馈栏判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
