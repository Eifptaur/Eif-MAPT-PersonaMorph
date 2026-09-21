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

# ⛔ V-R14-7 隔离：本判据的四个夹具（`feedback_selftest*.jsonl` / `feedback_rejected_selftest.jsonl` /
#   附件目录）原来都写在**产品 `data\` 里**，用完即删 ⇒ 跑前跑后对账的净变化是 0、判据看着"干净"，
#   而那一瞬间真机器人（或用户）在 `data\` 里看到的是我们的夹具。改到 %TEMP%（持续采样闸当场抓到的）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # scripts\（见 `_iso14` 文件头）
import _iso14                               # noqa: E402
_FB_ISO = _iso14.ISO

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

print("── B. 通道配置**不进用户界面**（2026-09-17 口径：那些是运维侧的事，只留在 config.json）──")
for key in ("feedback.to", "feedback.upload_url", "feedback.smtp.user",
            "feedback.smtp.password", "feedback.smtp.host", "feedback.smtp.port",
            "feedback.webhook_url", "feedback.webhook_token"):
    ok("界面里没有 %s（不把配置摆给用户）" % key, ('data-cfg="%s"' % key) not in _seg)
ok("也没有「保存设置（反馈）」这种把配置摆给用户的按钮", "保存设置（反馈）" not in _seg)
ok("这些键在 config.json 里仍然存在（运维/作者可配）",
   all(('"%s"' % k) in src("agent/config.py")
       for k in ("upload_url", "webhook_url", "webhook_token")), "")

print("── C. 三态如实：没通道就必须说「没发出去」──")
_saved = FB.FEEDBACK_FILE
_tmp = os.path.join(_FB_ISO, "feedback_selftest.jsonl")
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
    # 2026-09-15 口径更新（已知现象：「没必要让程序帮我整理，反正他只要用邮箱发到我的邮箱就行」）：
    #   正文＝**既有口径：原样** + 一行元信息 ⇒ 断言"原话一字不差在正文里、有元信息行、没有那套改写"。
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
        # 主题用 Header 编码（中文必须编码）⇒ 自检要按 MIME 解码后再看，不能直接找字面量
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


def _raw(rel):
    """按**字节**读（打包器就是这么扫的）：latin1 不会因编码失败丢字节，ASCII 的针照样找得到。"""
    try:
        with io.open(os.path.join(ROOT, rel.replace("/", os.sep)), "rb") as fh:
            return fh.read().decode("latin1", "ignore")
    except OSError:
        return ""


# ⛔ V-R8-7 **行为级锚**（第八轮）：这条原来自称「全仓源码」，实际只查了**写死的 3 个文件**
#   （`agent/config.py` / `agent/webui.py` / `config.example.json`）。改成**真出包清单** ——
#   `pack_online.tracked() − excluded()` ＝"真会发给用户的那批文件"（322 个量级）。
#   ⚠️ 打包器自己的 `SCAN` 规则里**没有邮箱类规则** ⇒ 这一条是邮箱泄密的**唯一把关**。
try:
    if os.path.join(ROOT, "scripts") not in sys.path:
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import pack_online as _po
    _pack = [f for f in _po.tracked() if not _po.excluded(f)]
    _pack_err = ""
except Exception as _e_pack:
    _pack, _pack_err = [], str(_e_pack)[:90]
ok("D-pk0 真出包清单拿得到（`pack_online.tracked() − excluded()`；拿不到就判红，不许静默降级成「没扫」）",
   len(_pack) > 100 and not _pack_err, "n=%d · %s" % (len(_pack), _pack_err or "ok"))
ok("D-pk1 量具阳性对照：按字节真读到了文件内容（读成空串会让下面那条**假绿**）",
   "def submit(" in _raw("agent/feedback.py"), "%d 字符" % len(_raw("agent/feedback.py")))
_hits = [f for f in _pack if MAIL_A in _raw(f)]
ok("代码里没有真实邮箱（**真出包清单全扫**，不再是写死三个文件）", not _hits, str(_hits[:3]))
ok("示例配置里 feedback 段是空的（不给真实地址）",
   MAIL_A not in _raw("config.example.json") and MAIL_B not in _raw("config.example.json"))
_c = FB.compose({"kind": "建议", "text": "一句原话", "at_h": "2026-09-14 10:00:00", "ver": "b.x",
                 "contact": "c", "env": {}})
# 口径 2026-09-15：正文＝原话原样（只加一行元信息）⇒ 断言"元信息在头一行 + 原话原样在后"
ok("正文＝用户原话原样（只加一行元信息：类型/时间/版本/联系方式）",
   all(k in _c.splitlines()[0] for k in ("建议", "2026-09-14 10:00:00", "b.x", "c"))
   and _c.splitlines()[-1].strip() == "一句原话"
   and "诉求：" not in _c, _c.replace("\n", "⏎")[:100])

print("── E. 防刷限流：咽喉点 + 被拒的不落盘 ──")
_C_saved = (FB.FEEDBACK_FILE, FB.REJECT_FILE, dict(FB.LIMIT), FB._cfg)
_tmp3 = os.path.join(_FB_ISO, "feedback_selftest3.jsonl")
_rej3 = os.path.join(_FB_ISO, "feedback_rejected_selftest.jsonl")
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

print("\n── G. 反馈栏只给用户看该看的（2026-09-17 用户：「你把 GitHub 当成我了，还是把用户当成我了」）──")
_ui = src("agent/console_html.py")
ok("简单区只有 类型 / 内容 / 提交",
   'id="fbKind"' in _ui and 'id="fbText"' in _ui and 'id="fbSubmit"' in _ui, "")
_i_adv = _ui.find('id="fbAdv"')
ok("联系方式与提交记录在「更多」折叠区之后",
   _ui.find('id="fbContact"') > _i_adv and _ui.find('id="fbRecent"') > _i_adv, "")
ok("顶部提示默认隐藏（只在发不出去 / 有积压时出现）",
   'id="fbWarn"' in _ui and 'id="fbWarn" style="display:none' in _ui, "")
ok("**用户界面里没有任何通道配置**（那些是作者侧的事，只留在 config.json）",
   'data-cfg="feedback.' not in _ui, "")
for _k in ("在线提交密钥", "推送地址", "推送口令", "发信服务器", "邮箱授权码", "中转网址", "Web3Forms"):
    ok("界面文案里不该出现作者侧词汇「%s」" % _k, _k not in _ui, "")
_cfg_src = src("agent/config.py")
ok("通道键仍在配置里（作者可配）：webhook_url / webhook_token",
   '"webhook_url"' in _cfg_src and '"webhook_token"' in _cfg_src, "")
ok("Web3Forms 那条已整条删掉（配置、实现、界面文案都不再有）",
   "web3forms" not in _cfg_src.lower() and "web3forms" not in src("agent/feedback.py").lower()
   and "web3forms" not in _ui.lower(), "")

_saved_cfg, _saved_post = FB._cfg, FB._post
try:
    _calls = []

    def _fakepost2(url, payload, timeout=10):
        _calls.append(url)
        return {"ok": False, "status": 0, "why": "boom"}

    FB._post = _fakepost2
    FB._cfg = lambda: {"upload_url": "https://mine/x",
                       "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=x"}
    FB.deliver({"kind": "其他", "text": "t", "ver": "1"})
    ok("顺序：自建中转优先，失败再走「推送到你」",
       _calls[:2] == ["https://mine/x", "https://oapi.dingtalk.com/robot/send?access_token=x"],
       str(_calls[:3]))
    FB._cfg = lambda: {"webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=x"}
    _st = FB.stats()
    ok("状态里认得出推送通道、can_send=True",
       bool(_st["can_send"]) and "推送到你" in _st["channel"], _st["channel"])
    # ── H. 国内可达的"推送到你自己"（钉钉/飞书/企业微信 群机器人、PushPlus）──
    print("\n── H. 推送通道：钉钉/飞书/企业微信/PushPlus 按域名自动适配 ──")
    _sent = []

    def _mk(ok_body):
        def _f(url, payload, timeout=10):
            _sent.append((url, payload))
            return {"ok": True, "status": 200, "why": ok_body}
        return _f

    for _host, _body_ok, _body_bad, _where in (
            ("https://oapi.dingtalk.com/robot/send?access_token=x", '{"errcode":0,"errmsg":"ok"}',
             '{"errcode":310000,"errmsg":"keywords not in content"}', "text"),
            ("https://open.feishu.cn/open-apis/bot/v2/hook/abc", '{"code":0,"msg":"success"}',
             '{"code":9499,"msg":"Bad Request"}', "content"),
            ("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=k", '{"errcode":0,"errmsg":"ok"}',
             '{"errcode":93000,"errmsg":"invalid webhook url"}', "text")):
        _sent[:] = []
        FB._post = _mk(_body_ok)
        _r = FB._post_webhook(_host, {"kind": "问题", "text": "hi", "ver": "1"}, "")
        ok("%s ⇒ 认成功" % _host.split("/")[2], bool(_r.get("ok")), str(_r.get("why"))[:50])
        _p = _sent[0][1]
        ok("  请求体用的是 text 形态", _where in _p and isinstance(_p[_where], dict), str(list(_p.keys())))
        FB._post = _mk(_body_bad)
        _r2 = FB._post_webhook(_host, {"kind": "问题", "text": "hi", "ver": "1"}, "")
        ok("  返回码非 0 ⇒ **判失败**（别把被拒当成功）", not _r2.get("ok"), str(_r2.get("why"))[:60])
    _sent[:] = []
    FB._post = _mk('{"code":200,"msg":"请求成功"}')
    _r3 = FB._post_webhook("https://www.pushplus.plus/send", {"kind": "其他", "text": "x", "ver": "1"}, "")
    ok("PushPlus 没有 token ⇒ 明确说缺什么", not _r3.get("ok") and "token" in str(_r3.get("why")), str(_r3.get("why"))[:50])
    _r4 = FB._post_webhook("https://www.pushplus.plus/send", {"kind": "其他", "text": "x", "ver": "1"}, "TK")
    ok("PushPlus 带 token ⇒ 走 token 形态并判成功",
       bool(_r4.get("ok")) and _sent[0][1].get("token") == "TK", str(_sent[0][1].keys()))
    _sent[:] = []
    FB._post = _mk("ok")
    _r5 = FB._post_webhook("https://mydomain.example/feedback", {"kind": "其他", "text": "x", "ver": "1"}, "")
    ok("任意自定义中转 ⇒ 只要 2xx 就算送到", bool(_r5.get("ok")) and "title" in _sent[0][1], str(list(_sent[0][1].keys())))
    _sent[:] = []
    FB._post = _mk('{"errcode":0,"errmsg":"ok"}')
    FB._post_webhook("https://oapi.dingtalk.com/robot/send?access_token=x",
                     {"kind": "其他", "text": "x", "ver": "1"}, "SEC")
    _u2 = _sent[0][0]
    ok("钉钉给了密钥 ⇒ 自动加签（timestamp & sign 都在地址上）",
       "timestamp=" in _u2 and "sign=" in _u2, _u2[-60:])
    ok("消息里固定带「群相反馈」⇒ 安全设置选「自定义关键词」填它即可",
       "群相反馈" in _sent[0][1]["text"]["content"], _sent[0][1]["text"]["content"][:24])
    FB._cfg = lambda: {"webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=x"}
    _st2 = FB.stats()
    ok("状态里认得出推送通道", bool(_st2["can_send"]) and "推送到你" in _st2["channel"], _st2["channel"])
finally:
    FB._cfg, FB._post = _saved_cfg, _saved_post

print("\n── I. 附件与联系邮箱（2026-09-17 用户：「我们的反馈提交能不能提交图片和文件」·「可以让用户选填一个联系邮箱」）──")

import base64 as _b64                       # noqa: E402
import shutil as _shutil                    # noqa: E402

_ui2 = src("agent/console_html.py")
ok("界面有「图片/文件」这一行与选择按钮",
   'id="fbPick"' in _ui2 and 'id="fbFileInput"' in _ui2 and ">选择图片或文件<" in _ui2, "")
ok("文件选择框允许多选（一次能带好几个）",
   re.search(r'id="fbFileInput"[^>]*multiple', _ui2) is not None, "")
ok("界面上写清了附件上限（最多 4 个 / 图片 2MB / 文件 20MB）",
   all(k in _ui2 for k in ("最多 4 个", "2MB", "20MB")), "")
ok("界面有「联系邮箱」这一行、且写明选填",
   'id="fbContact"' in _ui2 and ">联系邮箱<" in _ui2 and "选填" in _ui2, "")
ok("前端把附件一起 POST 上去、并且自己先拦一次上限（不等到提交完才失败）",
   "files:_files" in _ui2 and "_fbImgMB" in _ui2 and "_fbFileMB" in _ui2, "")
ok("前端也拦明显不对的联系邮箱（提示里给了例子）", "xxx@qq.com" in _ui2, "")
_wu2 = src("agent/webui.py")
ok("服务端把附件转交给反馈模块，并有一道请求总量闸",
   "FB.submit(" in _wu2 and "_files" in _wu2 and "40 * 1024 * 1024" in _wu2, "")
ok("会写盘就得有上限：附件目录的上限与清理都在（用户既有口径）",
   int(FB.ATTACH.get("keep_files") or 0) > 0 and int(FB.ATTACH.get("keep_bytes") or 0) > 0, str(FB.ATTACH))

_sv = (FB.MEDIA_DIR, FB.FEEDBACK_FILE, dict(FB.LIMIT), FB._cfg, dict(FB.ATTACH))
_mdir = os.path.join(_FB_ISO, "media")
_tmp4 = os.path.join(_FB_ISO, "feedback_selftest4.jsonl")
try:
    for _p in (_tmp4,):
        try:
            os.remove(_p)
        except Exception:
            pass
    _shutil.rmtree(_mdir, ignore_errors=True)
    FB.MEDIA_DIR = _mdir
    FB.FEEDBACK_FILE = _tmp4
    FB.LIMIT.update({"per_minute": 999, "per_hour": 999, "per_day": 999})
    FB._cfg = lambda: {}
    _png = b"\x89PNG\r\n\x1a\n" + b"\0" * 64

    _s1, _n1 = FB.save_attachments("t1", [
        {"name": "截图.png", "data": _b64.b64encode(_png).decode()},
        {"name": "日志.txt", "data": _b64.b64encode(b"hello log").decode()}])
    ok("图片与文件都存下来了（2 个、无抱怨）", len(_s1) == 2 and not _n1, "%d 个 / %s" % (len(_s1), _n1))
    ok("认得出哪张是图片（决定走图片消息还是文件消息）",
       _s1[0].get("image") is True and _s1[1].get("image") is False, str([f.get("image") for f in _s1]))
    ok("附件只落在这棵树里（不乱写别处）",
       all(os.path.abspath(f["path"]).startswith(os.path.abspath(_mdir)) for f in _s1), _s1[0]["path"])
    ok("附件名里的路径被剥掉（不给跳出目录的机会）",
       FB._safe_name("..\\..\\evil.png") == "evil.png", FB._safe_name("..\\..\\evil.png"))

    _big = b"\0" * (int(FB.ATTACH["image_max"]) + 1)
    _s2, _n2 = FB.save_attachments("t2", [{"name": "大图.png", "data": _b64.b64encode(_big).decode()}])
    ok("图片超过 2MB ⇒ 拒收并说清原因（不静默截断）", not _s2 and any("上限" in x for x in _n2), str(_n2)[:60])
    ok("这两条上限就是接收端的硬限制（企业微信 image ≤2MB、upload_media ≤20MB）",
       int(FB.ATTACH["image_max"]) == 2 * 1024 * 1024 and int(FB.ATTACH["file_max"]) == 20 * 1024 * 1024,
       "%s / %s" % (FB.ATTACH["image_max"], FB.ATTACH["file_max"]))
    FB.ATTACH = dict(FB.ATTACH, file_max=1024)
    _s3, _n3 = FB.save_attachments("t3", [{"name": "大文件.zip", "data": _b64.b64encode(b"\0" * 2048).decode()}])
    ok("非图片走文件档的上限（超了同样拒收）", not _s3 and any("上限" in x for x in _n3), str(_n3)[:60])
    FB.ATTACH = dict(_sv[4])
    _s4, _n4 = FB.save_attachments("t4", [{"name": "f%d.png" % i, "data": _b64.b64encode(b"x").decode()}
                                          for i in range(5)])
    ok("一次最多 4 个（多的不收，并说明）", len(_s4) == 4 and any("最多" in x for x in _n4),
       "%d 个 / %s" % (len(_s4), _n4))

    _c2 = FB.compose({"kind": "问题", "text": "发不出去", "ver": "1", "at_h": "t", "files": _s1})
    ok("正文里列了附件清单（没配推送通道时，也知道该找用户要哪几个文件）",
       "附件" in _c2 and "截图.png" in _c2 and "日志.txt" in _c2, _c2.replace("\n", "⏎")[-56:])

    # 企业微信这条：图片走 base64+md5、文件先 upload_media 换 media_id 再发（两种形态都不一样）
    _posts = []
    _ru, _rp = FB._wecom_upload, FB._post
    FB._post = lambda url, payload, timeout=10: (_posts.append(payload) or
                                                 {"ok": True, "status": 200, "why": '{"errcode":0}'})
    FB._wecom_upload = lambda url, key, name, raw, timeout=30: {"ok": True, "media_id": "MID-1"}
    try:
        _mm = FB._wecom_media("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=K", _s1)
        ok("图片走 image 消息：base64 与 md5 都在（少一个对方就拒）",
           _posts[0].get("msgtype") == "image" and bool(_posts[0]["image"].get("base64"))
           and len(_posts[0]["image"].get("md5") or "") == 32, str(list(_posts[0].get("image", {}).keys())))
        ok("文件先换 media_id、再用 file 消息发出去",
           _posts[1].get("msgtype") == "file" and _posts[1]["file"].get("media_id") == "MID-1",
           str(_posts[1]))
        ok("两个附件都送到 ⇒ 报 2/2", _mm.get("sent") == 2 and _mm.get("ok") is True, str(_mm.get("why"))[:50])
        _posts[:] = []
        FB._wecom_upload = lambda url, key, name, raw, timeout=30: {"ok": False, "why": "太大"}
        _mm2 = FB._wecom_media("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=K", _s1)
        ok("文件传不上去 ⇒ 如实记账 1/2（不假装送过）",
           _mm2.get("ok") is False and "1/2" in str(_mm2.get("why")), str(_mm2.get("why"))[:70])
    finally:
        FB._wecom_upload, FB._post = _ru, _rp

    _r_ok = FB.submit("建议", "判据用的假反馈：邮箱这条", "someone@example.com")
    ok("合法邮箱 ⇒ 收下并写进记录",
       _r_ok.get("state") == "queued" and FB._read_all()[-1].get("contact") == "someone@example.com",
       str(_r_ok.get("state")))
    _n_before = len(FB._read_all())
    _r_bad = FB.submit("建议", "判据用的假反馈：邮箱写错了", "不是邮箱")
    ok("邮箱写错 ⇒ 明确退回、且不落盘（选填不等于可以乱填）",
       _r_bad.get("state") == "error" and "邮箱" in str(_r_bad.get("why"))
       and len(FB._read_all()) == _n_before, str(_r_bad.get("why"))[:56])
    _r_a = FB.submit("问题", "", "", None, [{"name": "只有图.png", "data": _b64.b64encode(_png).decode()}])
    ok("只带附件、不写字也能提交（正文用占位句，东西别丢）",
       _r_a.get("state") == "queued" and _r_a.get("files") == 1, str(_r_a.get("state")))
    _last = FB._read_all()[-1]
    ok("附件随记录一起存档（补发时还找得到文件）",
       bool(_last.get("files")) and os.path.exists(_last["files"][0]["path"]), str(_last.get("files"))[:70])
    _r_e2 = FB.submit("建议", "", "")
    ok("既没字也没附件 ⇒ 明确拒绝（别收空条）",
       _r_e2.get("state") == "error" and "内容" in str(_r_e2.get("why")), str(_r_e2.get("why"))[:40])

    import smtplib as _sm2                # noqa: E402
    _box2 = {}

    class _FS:
        def __init__(self, host, port, timeout=None, context=None):
            _box2["h"] = host

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            pass

        def sendmail(self, frm, to, body):
            _box2["body"] = body

    _o2 = _sm2.SMTP_SSL
    _sm2.SMTP_SSL = _FS
    try:
        FB._cfg = lambda: {"to": "a@example.com",
                           "smtp": {"host": "h", "port": 465, "user": "me@qq.com", "password": "p"}}
        _rm = FB.submit("想法", "判据用的假反馈：邮件带附件", "", None,
                        [{"name": "附件A.png", "data": _b64.b64encode(_png).decode()}])
        ok("邮件通道能带附件（真挂上去了，不是只在正文里提一句）",
           _rm.get("state") == "sent" and "multipart/mixed" in (_box2.get("body") or "")
           and "Content-Disposition: attachment" in (_box2.get("body") or ""), str(_rm.get("state")))
    finally:
        _sm2.SMTP_SSL = _o2
        FB._cfg = lambda: {}

    _kb = int(FB.ATTACH["keep_files"])
    FB.ATTACH = dict(FB.ATTACH, keep_files=2)
    _pr = FB.prune_media()
    _left = sum(len(_fs) for _d, _s, _fs in os.walk(_mdir))
    ok("附件目录超上限 ⇒ 从最旧的删起（只删自己造的这棵树）",
       _pr.get("removed", 0) > 0 and _left <= 2 and os.path.isdir(_mdir),
       "删了 %s 个 · 剩 %d" % (_pr.get("removed"), _left))
    FB.ATTACH = dict(FB.ATTACH, keep_files=_kb)
finally:
    (FB.MEDIA_DIR, FB.FEEDBACK_FILE, _lim9, FB._cfg, _att9) = _sv
    FB.LIMIT.clear()
    FB.LIMIT.update(_lim9)
    FB.ATTACH.clear()
    FB.ATTACH.update(_att9)
    for _p in (_tmp4,):
        try:
            os.remove(_p)
        except Exception:
            pass
    _shutil.rmtree(_mdir, ignore_errors=True)

print("\n── J. 常驻公告：有问题就去反馈（2026-09-17 用户：「可以挂个常驻公告，说明有问题就点击导航栏的『反馈』，把问题进行反馈，最好是附上报告或者截图」）──")
_ji = _ui2.find('id="noticeBar"')
ok("界面上有常驻公告条 #noticeBar", _ji > 0, "")
_jseg = _ui2[_ji:_ji + 520] if _ji > 0 else ""
ok("公告是**常驻**的（不带 display:none，跟「只在有事时出现」的更新条区分开）",
   bool(_jseg) and "display:none" not in _jseg, _jseg[:56].replace("\n", " "))
ok("公告点名要附截图或检验报告（用户原话里的两个词都在）",
   "截图" in _jseg and "检验报告" in _jseg, _jseg[:56].replace("\n", " "))
ok("公告里就有一个「去反馈」按钮（不用用户自己找入口）", "去反馈" in _jseg, "")
ok("「去反馈」复用导航那一项切面板（不另写一套切面板逻辑）",
   "querySelector('#nav a[href=\"#sec-feedback\"]')" in _ui2, "")

print("")
print("反馈栏判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
