#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""群友要图（生图链条）判据：脱敏 / 意图解析 / 后端选择 / 过滤链 fail-closed / 红线硬编码。

不需要任何真实生图后端、不出网：全部在假后端与临时文件上跑。
"""
import json
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import Image  # noqa: E402
from agent import image_gen as IG  # noqa: E402
from agent import config as CFG  # noqa: E402
from agent.config import DEFAULT_CONFIG  # noqa: E402

# ⛔ 本判据**全程不出网**（文件头写明"不需要真实生图后端、不出网"）。2026-09-18 产品改成"默认出网 +
#   在线优先"之后，只要哪一节忘了把出网关掉，`pick_backend()` 就会选到 pollinations ⇒ `generate()`
#   真去联网出图（实测把判据从 1 秒拖到 121 秒，还让判据结果依赖外网）。⇒ 这里**统一把在线后端封掉**，
#   各节要测"在线路径"时自己用假后端（`backends=[{...online...}]`）或断言预设表本身。
_orig_online_backend = IG.online_backend
IG.online_backend = lambda: {}

# ⛔ 2026-09-21：**判据不许碰用户正在跑的服务**（真机实测后果）。
#   `pick_backend()` 在"装过、但这次没探到"时会真的调 `sd_local.ensure_running()` —— 那是产品行为
#   （换掉没门禁的旧实例）。而 `sd_local.server_alive()` 是**机器级端口探针**：判据即使跑在
#   `%TEMP%` 副本里，探的也是**本机 7860**。服务正忙时探针会超时（实测它加载 CUDA 模型要 22 秒，
#   日志尾部就是"模型就绪…出图"）⇒ 判据会把**用户正在用的生图服务杀掉并重启**
#   （本机 2026-09-21 03:40:59 实测发生过一次：pidfile 4848 → 40232）。
#   ⇒ 判据里一律打桩；要测"错误路径"的段自己再包一层（见下面的 12b 段）。
from agent import sd_local as _sdl                                               # noqa: E402
_sdl.ensure_running = lambda *a, **k: (False, "判据环境：不许起/换真服务")
_sdl.status = lambda *a, **k: {"ok": False, "installed": False, "server_alive": False, "gated": False,
                               "gate_why": "", "why": "判据环境：不打探真端口"}

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


_orig_cfg = IG.cfg
CFG = dict(IG.DEFAULTS)


def set_cfg(**kw):
    CFG.clear()
    CFG.update(IG.DEFAULTS)
    CFG.update(kw)
    IG.cfg = lambda: dict(CFG)


print("① prompt 先脱敏")
s = IG.scrub_prompt("画个猫 13800138000 wxid_abc123 a@b.com @张三 110101199001011234")
ok("手机号被替换", "13800138000" not in s and "[手机号]" in s, s)
ok("wxid 被替换", "wxid_" not in s, s)
ok("邮箱被替换", "a@b.com" not in s, s)
ok("身份证被替换", "110101199001011234" not in s, s)
ok("@提及 被替换", "@张三" not in s, s)

print("② 意图解析")
i1 = IG.parse_intent("帮我画两张赛博风格的猫")
ok("抽到张数=2", i1["count"] == 2, str(i1["count"]))
ok("抽到风格=赛博", i1["style"] == "赛博", i1["style"])
ok("主体里有猫", "猫" in i1["subject"], i1["subject"])
i2 = IG.parse_intent("来三张竖的国风山水")
ok("中文数字张数=3", i2["count"] == 3, str(i2["count"]))
ok("尺寸=portrait", i2["size"] == "portrait", i2["size"])
i3 = IG.parse_intent("把这张照片换成我朋友的脸")
ok("真人换脸被识别", i3["wants_real_face"] is True, str(i3))
i4 = IG.parse_intent("给我来张 r18 的")
ok("成人内容被识别", i4["nsfw"] is True, str(i4))
i5 = IG.parse_intent("画只狗")
ok("默认一张、默认方形", i5["count"] == 1 and i5["size"] == "square", str(i5))

print("③ 后端选择（没配后端 ⇒ 明确失败，不假装）")
set_cfg(enabled=True)
# ⚠️ 2026-09-18：这一段判的是**"用户一个后端都没配"**时的行为，可 `pick_backend()` 还会
#   **自动探测本机跑着的本地服务**（那是产品特性）。本机恰好开着自带的 7860 ⇒ 探到了就不算"没配"，
#   于是这三条假红。⇒ 判据必须自己控制环境：这一节先把探测关掉，测的是"配置为空"这条路径。
_orig_detect = IG.detect_local
IG.detect_local = lambda *a, **k: []
try:
    ok("没配后端 ⇒ 拒绝并给出人话原因", IG.pick_backend()[0] is None and ("没探到" in IG.pick_backend()[1] or "没配" in IG.pick_backend()[1]), IG.pick_backend()[1])
    set_cfg(enabled=True, backends=[{"id": "online-x", "kind": "online", "url": "http://127.0.0.1:9/x"}])
    ok("只配在线后端但没允许出网 ⇒ 拒绝", IG.pick_backend()[0] is None and "online_allowed" in IG.pick_backend()[1],
       IG.pick_backend()[1])
finally:
    IG.detect_local = _orig_detect

# ⛔ 第四轮审计 V-R4-12b：**探后端时抛异常**不许被说成「你没装」——那是方向错的提示
#   （用户会去装一个已经装好的东西）。异常原文必须带出去。
import agent.sd_local as _sdl                                              # noqa: E402

_orig_status = _sdl.status
_orig_detect2 = IG.detect_local
IG.detect_local = lambda *a, **k: []
_sdl.status = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("夹具：status 炸了"))
set_cfg(enabled=True, online_allowed=False, backends=[])
try:
    _pb, _pw = IG.pick_backend()
finally:
    _sdl.status = _orig_status
    IG.detect_local = _orig_detect2
ok("探后端出错 ⇒ 明说「检查本地生图后端时出错」并带出异常类型",
   _pb is None and ("检查本地生图后端时出错" in _pw) and ("RuntimeError" in _pw), _pw)
ok("反例锚：这条错误路径上**不许**出现「本机没探到常见生图服务」（老行为＝冤枉成没装）",
   "没探到常见生图服务" not in _pw, _pw)

set_cfg(enabled=True, online_allowed=True, online_preset="custom",
        online_api={"url": "", "key": "", "model": ""},
        backends=[{"id": "online-x", "kind": "online", "url": "http://127.0.0.1:9/x"}])
# ⚠️ 2026-09-18 口径：**预设优先于手填的 backends**（预设就是用户在面板上选的那一家）。
#    所以这一条要先把预设设成 custom 且不填地址（=没选预设），才轮到 backends 里的在线后端。
ok("允许出网后，配置里的在线后端能被选到（预设未选时才轮到它）",
   (IG.pick_backend()[0] or {}).get("id") == "online-x", str(IG.pick_backend()[0]))

print("④ 过滤链：任一层判不出/判否 ⇒ 不发（fail-closed）")
tmp = tempfile.mkdtemp(prefix="imggen_")
good_png = os.path.join(tmp, "good.png")
Image.new("RGB", (512, 512), (30, 60, 120)).save(good_png)
small_png = os.path.join(tmp, "small.png")
Image.new("RGB", (64, 64), (30, 60, 120)).save(small_png)
bad_txt = os.path.join(tmp, "notimg.png")
open(bad_txt, "w", encoding="utf-8").write("not an image")
set_cfg(enabled=True, filter_chain={"size": True, "dup": True, "blacklist": True, "text": False, "classifier": False})
okf, resf = IG.run_filters(good_png, {"prompt": "猫"})
ok("三层开着且都过 ⇒ ok=True", okf is True, json.dumps([r for r in resf if r.get("skipped") is None], ensure_ascii=False)[:120])
set_cfg(enabled=True, filter_chain={"size": True, "dup": True, "blacklist": True, "text": True, "classifier": True})
okd, resd = IG.run_filters(good_png, {"prompt": "猫"})
ok("分类器未接（判不出）⇒ 整链判否", okd is False and any(r["name"] == "classifier" and r["ok"] is None for r in resd),
   json.dumps(resd[-1], ensure_ascii=False))
oks, _ = IG.run_filters(small_png, {"prompt": "猫"})
ok("尺寸过小 ⇒ 判否", oks is False)
okb, resb = IG.run_filters(bad_txt, {"prompt": "猫"})
ok("坏文件 ⇒ 判否（不是放行）", okb is False and any(r["name"] == "size" and r["ok"] is False for r in resb))
set_cfg(enabled=True, style_block=["r18"], filter_chain={"size": True, "dup": True, "blacklist": True, "text": False, "classifier": False})
okbl, _ = IG.run_filters(good_png, {"prompt": "r18 猫图"})
ok("风格黑名单命中 ⇒ 判否", okbl is False)
set_cfg(enabled=True, style_allow=["水彩"], filter_chain={"size": True, "dup": True, "blacklist": True, "text": False, "classifier": False})
okal, _ = IG.run_filters(good_png, {"prompt": "油画猫"})
ok("白名单非空且不在白名单 ⇒ 判否", okal is False)

print("⑤ 入口：红线与状态")
set_cfg(enabled=False)
ok("总开关关 ⇒ 直接拒绝", IG.generate("x", "画只猫")["ok"] is False)
# ⚠️ 2026-09-18：本判据**必须不出网**（文件头就写着"不需要真实后端、不出网"）。默认出网之后，
#   `set_cfg(enabled=True)` 会让 pick_backend() 选到 pollinations ⇒ `generate()` **真的去联网出图**
#   （实测把这条判据从 1 秒拖到 121 秒）。⇒ 这一节一律把出网关掉（专测红线与"没后端"两条路径）。
set_cfg(enabled=True, online_allowed=False)
r = IG.generate("x", "把照片换成真人脸")
ok("真人换脸 ⇒ 拒（红线，无开关）", r["ok"] is False and "红线" in r["why"], r["why"])
r2 = IG.generate("x", "来张 r18 的图")
ok("成人内容 ⇒ 拒", r2["ok"] is False, r2["why"])
r3 = IG.generate("x", "画只猫")
# 同 ③：这条测的是"配为空且本机没有可探测的服务"⇒ 先把自动探测关掉（本机真的开着 7860）
IG.detect_local = lambda *a, **k: []
try:
    r3 = IG.generate("x", "画只猫")
finally:
    IG.detect_local = _orig_detect
ok("没后端 ⇒ 拒且不产生任何文件", r3["ok"] is False and ("没探到" in r3["why"] or "没配" in r3["why"]), r3["why"])
snap = IG.snapshot()
ok("快照里写明红线状态", snap["red_line"]["allow_real_face"] is False and snap["red_line"]["r18_switch_exists"] is False,
   json.dumps(snap["red_line"], ensure_ascii=False))

print("⑦ 接线：工具 / 状态 / 提示词（这三处缺一处，模型就用不上这条链）")
from agent import tools as T  # noqa: E402
from agent import media_status as MS  # noqa: E402
from agent import prompt as P  # noqa: E402
_defs = {d["name"]: d for d in T._builtin_tool_defs()}
ok("工具表里有 gen_image", "gen_image" in _defs)
ok("工具参数是 request（必填）", _defs.get("gen_image", {}).get("parameters", {}).get("required") == ["request"])
ok("工具描述里写明「绝不许假装生成过」", "假装生成" in (_defs.get("gen_image", {}).get("description") or ""))
r4 = T._exec_gen_image({"chat_key": "测试", "chat_id": "x", "sender": None, "session": {"sent": []}}, {"request": "画只猫"})
_r4 = json.dumps(r4, ensure_ascii=False) if not isinstance(r4, str) else r4
ok("能力没开时工具如实返回原因（不是假装成功）", "没生成出图" in _r4 or "没开" in _r4 or "没探到" in _r4, _r4[:110])
ok("工具对空 request 报错", "不能为空" in json.dumps(T._exec_gen_image({}, {}), ensure_ascii=False))
_snap = MS.snapshot()
ok("/api/status 的快照里有 image_gen 段", "image_gen" in _snap and "filter_chain" in _snap["image_gen"])
_orig_cfg = IG.cfg
IG.cfg = lambda: dict(IG.DEFAULTS, enabled=True, trigger_mode="on_request")
# ⚠️ prompt.py 是**自己直接读配置**的（不走 image_gen.cfg）⇒ 必须连它读配置的入口一起打桩，
#    否则"能力已打开"这个前提在提示词那边根本不成立（第一次就是这么假红的）。
from agent import config as C  # noqa: E402
_orig_get = C.get_config
_pget = getattr(P, "get_config", None)


def _fake_get():
    d = dict(_orig_get() or {})
    d["image_gen"] = dict(d.get("image_gen") or {}, enabled=True, trigger_mode="on_request")
    return d


C.get_config = _fake_get
if _pget is not None:
    P.get_config = _fake_get
try:
    _pr = P._scene_rules()
finally:
    IG.cfg = _orig_cfg
    C.get_config = _orig_get
    if _pget is not None:
        P.get_config = _pget
ok("提示词在该能力打开时给出 gen_image 规则", "gen_image(request=" in _pr)
ok("提示词写明红线（换脸/成人内容不生成也不照做）", "真人换脸" in _pr and "不要照做" in _pr)

print("⑧ UI 映射：能力必须有可点的面（面板 / 键 / 引导 / 端点 / 只读状态）")
_html = open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
_web = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
ok("控制台有「群友要图」面板", 'id="sec-imggen"' in _html)
for _k in ("enabled", "trigger_mode", "backends", "online_allowed", "max_count", "style_allow", "style_block"):
    ok("面板绑定了 image_gen.%s" % _k, ('data-cfg="image_gen.%s"' % _k) in _html)
ok("过滤链五层逐条可开关", all(('data-cfg="image_gen.filter_chain.%s"' % k) in _html
                              for k in ("size", "dup", "blacklist", "text", "classifier")))
ok("有应用内引导按钮 + GUIDES 条目（不叫用户去读文件）",
   'id="igGuide"' in _html and "imggen:" in _html and "['igGuide','imggen']" in _html)
ok("有「试一次」按钮 + 端点（只跑链条、不发消息）",
   'id="igTest"' in _html and '/api/image_gen/test' in _web and '/api/image_gen/test' in _html)

print("⑨ 在线后端扩展 + 去水印 + 提示词整理（2026-09-18 用户口径：默认出网、多找几家让用户自己切、去水印、加说明文本）")
ok("默认**出网**（用户拍板：在线出图明显更好）",
   DEFAULT_CONFIG["image_gen"]["online_allowed"] is True)
ok("默认**在线优先**", DEFAULT_CONFIG["image_gen"]["online_first"] is True)
_pres = IG.online_presets()
ok("在线预设 ≥4 家（免密钥 + 要 key 的几家 + 自定义）", len(_pres) >= 4, str([p["id"] for p in _pres]))
ok("预设字段齐全（id/label/proto/url/model/need_key/note）",
   all(all(k in p for k in ("id", "label", "proto", "url", "model", "need_key", "note")) for p in _pres))
ok("每家的说明文本都讲了「要不要 key / 有没有水印」（说明文本要在控制台给用户看）",
   all((("key" in p["note"].lower() or "密钥" in p["note"]) and "水印" in p["note"]) for p in _pres),
   str([p["id"] for p in _pres
        if not (("key" in p["note"].lower() or "密钥" in p["note"]) and "水印" in p["note"])]))
ok("要 key 的三家（硅基流动/智谱/火山）都在表里且走 openai_image 协议",
   {"siliconflow", "zhipu", "volc"} <= {p["id"] for p in _pres}
   and all(p["proto"] == "openai_image" for p in _pres if p["id"] in ("siliconflow", "zhipu", "volc")))
ok("免密钥那家标注 need_key=False（零配置可用）",
   [p for p in _pres if p["id"] == "pollinations"][0]["need_key"] is False)
ok("call_backend 支持 openai_image 协议（一个协议覆盖三家兼容接口）+ 取 b64_json 或 url",
   'proto == "openai_image"' in open(os.path.join(ROOT, "agent", "image_gen.py"), encoding="utf-8").read()
   and "images/generations" in open(os.path.join(ROOT, "agent", "image_gen.py"), encoding="utf-8").read())
ok("pollinations 协议会带 token（官方：nologo 要有账号 ⇒ 带 Bearer 才免水印）",
   "Authorization" in open(os.path.join(ROOT, "agent", "image_gen.py"), encoding="utf-8").read())

# ⛔ 第十轮 **V-R10-34** 第 6 条（第九轮未闭合项）：pollinations 的**提示词不许进 URL**。
#   老写法 `GET /prompt/<urlencode(prompt)>` 把私聊原文塞进**请求行**（审计实测 2750 字 → 24.7KB）。
#   当天一手取证（4 组对照探针）：`POST {base}/?query` + JSON body
#   `{"prompt": …}` 回的是同一张图（HTTP 200 / image/jpeg）⇒ 内容放 body、非内容参数留查询串。
import io as _io_poll                                                                    # noqa: E402
import shutil as _sh_poll                                                                # noqa: E402
import urllib.parse as _up_poll                                                          # noqa: E402
import urllib.request as _ur_poll                                                        # noqa: E402

_POLL_PROMPT = "私聊原文：张三手机号13800000000，画只猫"
_seen_poll = {}
_orig_urlopen = _ur_poll.urlopen


class _FakePollResp(object):
    status = 200

    def __init__(self, data):
        self._d = data
        self.headers = {"Content-Type": "image/png"}

    def read(self, n=-1):
        return self._d if (not n or n < 0) else self._d[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_buf_poll = _io_poll.BytesIO()
Image.new("RGB", (96, 96), (7, 8, 9)).save(_buf_poll, "PNG")
_POLL_BYTES = _buf_poll.getvalue()


def _fake_poll_open(req, **kw):
    _seen_poll["url"] = req.full_url
    _seen_poll["method"] = req.get_method()
    _seen_poll["body"] = (req.data or b"").decode("utf-8", "replace")
    return _FakePollResp(_POLL_BYTES)


def _prompt_leaks(url, body):
    """同一条判据：URL 里出现（原文或 urlencode 后的）提示词、或 body 里没有它 ＝ 泄漏。"""
    return (_up_poll.quote(_POLL_PROMPT) in str(url)) or (_POLL_PROMPT in str(url)) \
        or ("prompt" not in str(body))


# ⛔ 判据**不许往产品 `data/gen_images` 落文件**（其余测试文件的既有口径）：把落盘口改到临时目录，
#   测完删掉 —— 这里只验"传输形状"，不验产品目录布局。
_orig_save_poll = IG._save_image_bytes
_tmp_poll_dir = tempfile.mkdtemp(prefix="pm-poll-")


def _save_tmp_poll(data, tag):
    _p = os.path.join(_tmp_poll_dir, "%s_%d.png" % (str(tag), int(time.time() * 1000) % 100000))
    with open(_p, "wb") as _fh:
        _fh.write(data)
    return _p


try:
    IG._save_image_bytes = _save_tmp_poll
    _ur_poll.urlopen = _fake_poll_open
    try:
        _rp_poll = IG.call_backend({"id": "pollinations", "kind": "online", "proto": "pollinations",
                                    "url": "https://image.pollinations.ai/prompt/", "model": "sana"},
                                   _POLL_PROMPT, count=1, size="square")
    finally:
        _ur_poll.urlopen = _orig_urlopen
finally:
    IG._save_image_bytes = _orig_save_poll
    _sh_poll.rmtree(_tmp_poll_dir, ignore_errors=True)
ok("① 走的是 POST + JSON body，提示词在 **body** 里",
   _seen_poll.get("method") == "POST" and "prompt" in (_seen_poll.get("body") or ""),
   str(_seen_poll)[:120])
ok("① URL 里**一个字符都不带**（原文与 urlencode 两种形态都没有）",
   not _prompt_leaks(_seen_poll.get("url") or "", _seen_poll.get("body") or ""),
   str(_seen_poll.get("url"))[:120])
ok("① 非内容参数（尺寸/模型/seed）仍在查询串里（后端照旧认得出）",
   "width=" in (_seen_poll.get("url") or "") and "model=sana" in (_seen_poll.get("url") or ""),
   str(_seen_poll.get("url"))[:130])
ok("① 正常拿到 1 张图（改了传输形状没改行为）",
   bool(_rp_poll.get("files")) and len(_rp_poll["files"]) == 1, str(_rp_poll.get("files"))[:80])
ok("① 反例锚：老写法（提示词拼进 URL 路径）用**同一条判据**判『漏』",
   _prompt_leaks("https://image.pollinations.ai/prompt/" + _up_poll.quote(_POLL_PROMPT)
                 + "?width=1024&height=1024", ""))

# ⛔ 2026-09-22 加（第十三轮 **V-R13-3** · P2）：image_gen 的回链**换成行为锚**（段内子串换种写法就绕过）。
#   手法与 `safe_fetch_selftest` L27 同一套：本地假后端 + **连接层取证**（连的必须是 IP 字面量）。
print("\n② 回链（backend 回包里的 data[].url）连的是 IP 字面量（V-R13-3 行为锚）")
import http.server as _hs13                                                              # noqa: E402
import socket as _sock13                                                                 # noqa: E402
import threading as _th13                                                                # noqa: E402
from agent import safe_fetch as _SF13                                                    # noqa: E402

_PORT13 = [0]


class _H13(_hs13.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        _n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(_n)
        _b = json.dumps({"created": 1, "data": [
            {"url": "http://pinned.test:%d/img13.png" % _PORT13[0]}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(_b)))
        self.end_headers()
        self.wfile.write(_b)

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(_POLL_BYTES)))
        self.end_headers()
        self.wfile.write(_POLL_BYTES)


_srv13 = _hs13.HTTPServer(("127.0.0.1", 0), _H13)
_PORT13[0] = _srv13.server_address[1]
_th13.Thread(target=_srv13.serve_forever, daemon=True).start()
_real_gai13 = _sock13.getaddrinfo
_real_conn13 = _sock13.create_connection
_real_priv13 = _SF13._is_private_ip
_conn13 = []


def _spy13(address, *a, **k):
    """只**记录**连接目标（不拒）—— 因为请求阶段（POST /images/generations）本来就是普通 urlopen
    （后端地址是用户配的，产品口径如此），只有**回链下载那一跳**必须用钉 IP 传输。"""
    _conn13.append(tuple(address) if isinstance(address, (tuple, list)) else (address,))
    return _real_conn13(("127.0.0.1", int(_conn13[-1][1])), *a, **k)


_orig_save13 = IG._save_image_bytes
_tmp13b = tempfile.mkdtemp(prefix="pm-ig13-")


def _save13(data, tag):
    _p = os.path.join(_tmp13b, "%s_13.png" % str(tag))
    with open(_p, "wb") as _f:
        _f.write(data)
    return _p


try:
    _sock13.getaddrinfo = lambda host, port, *a, **k: [
        (_sock13.AF_INET, _sock13.SOCK_STREAM, 6, "", ("127.0.0.1", int(port) if port else 80))]
    _sock13.create_connection = _spy13
    _SF13._is_private_ip = lambda *a, **k: False
    IG._save_image_bytes = _save13
    _r13b = IG.call_backend({"id": "online13", "kind": "online", "proto": "openai_image",
                             "url": "http://pinned.test:%d" % _PORT13[0], "model": "m13", "key": "k13"},
                            "一只猫", count=1, size="square")
    # 回链那一跳必须是**钉 IP**（地址是 IP 字面量）；老写法（urlopen 二次解析）这一跳会带域名
    # ⇒ 「IP 字面量连接数」为 0 ⇒ 这条必红。请求阶段带域名是**允许**的，所以这里只数不拒。
    _ip_conn13 = [x for x in _conn13 if str(x[0]) == "127.0.0.1"]
    ok("② 回链下载那一跳连的是**已校验的 IP 字面量**（连接层取证，不是段内 grep）",
       bool(_r13b.get("files")) and len(_ip_conn13) >= 1,
       "files=%s 连接目标=%s" % (len(_r13b.get("files") or []), _conn13[:4]))
except Exception as _e13:
    ok("② 回链下载时每次都连已校验的 IP", False, "跑不起来：%s" % str(_e13)[:100])
finally:
    _sock13.getaddrinfo = _real_gai13
    _sock13.create_connection = _real_conn13
    _SF13._is_private_ip = _real_priv13
    IG._save_image_bytes = _orig_save13
    try:
        _srv13.shutdown()
        _srv13.server_close()
    except Exception:
        pass
    _sh_poll.rmtree(_tmp13b, ignore_errors=True)

# 去水印：功能性（真裁一张图）+ 口径（带 token/要 key 的后端不动它）
_wt = tempfile.mkdtemp(prefix="imggen_wm_")
_wi = os.path.join(_wt, "wm.jpg")
Image.new("RGB", (300, 300), (10, 20, 30)).save(_wi)
_wk = os.path.join(_wt, "keyed.jpg")
Image.new("RGB", (300, 300), (10, 20, 30)).save(_wk)
_p1, _n1 = IG.strip_watermark(_wi, "pollinations", has_token=False)
ok("免密钥那条：裁掉底部水印带（尺寸真的变小）",
   bool(_n1) and Image.open(_p1).size[1] < 300, "%s / %s" % (_p1, _n1))
# ⚠️ 第二个用例**必须另用一份图**：`strip_watermark` 是就地裁切，共用一份会读到已裁过的图（假红）
_p2, _n2 = IG.strip_watermark(_wk, "zhipu", has_token=True)
ok("要 key / 带 token 的后端本来没水印 ⇒ 一个像素都不动", _n2 == "" and Image.open(_p2).size[1] == 300)
ok("去水印默认开、比例可配", DEFAULT_CONFIG["image_gen"]["strip_watermark"] is True
   and float(DEFAULT_CONFIG["image_gen"]["watermark_crop"]) > 0)
ok("提示词整理：主体 + 中性质量后缀（不加会和动漫风打架的'照片级'）",
   IG.build_prompt("a cat").startswith("a cat, highly detailed")
   and "photorealistic" not in IG.build_prompt("a cat"))
ok("提示词整理：画里已写画质词就不重复堆（避免提示词越滚越长）",
   IG.build_prompt("a cat, highly detailed") == "a cat, highly detailed")
ok("提示词整理：带风格时插在主体之后",
   IG.build_prompt("a cat", "watercolor").startswith("a cat, watercolor"))
ok("generate() 走的是整理后的提示词（不是把中文碎片原样丢给模型）",
   "build_prompt(intent.get(" in open(os.path.join(ROOT, "agent", "image_gen.py"), encoding="utf-8").read())
# UI：新增的在线后端/密钥/去水印都要有可点的面 + 说明文本
for _k in ("online_preset", "online_first", "strip_watermark", "online_api.url",
           "online_api.key", "online_api.model"):
    ok("面板绑定了 image_gen.%s" % _k, ('data-cfg="image_gen.%s"' % _k) in _html)
ok("面板写清了「哪家要 key / 有没有水印 / 花多少时间」（说明文本，不是只给字段）",
   "无水印" in _html and "水印" in _html and "免密钥" in _html and "要 key" in _html)
ok("面板给出各家的推荐模型名与接口地址（用户不用去别处查）",
   "FLUX.1-schnell" in _html and "cogview-3-flash" in _html
   and "api.siliconflow.cn/v1" in _html and "open.bigmodel.cn" in _html
   and "ark.cn-beijing.volces.com" in _html)
ok("面板会显示后端/过滤链/红线状态（只读，来自 /api/status）",
   'igWhy' in _html and 'igList' in _html and 'ig.red_line' in _html)
_cfg_src = open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read()
ok("默认配置里有 image_gen 段（面板的键才绑得上）", "image_gen" in _cfg_src)
ok("面板文案写明「没配后端不会假装生成」", "不会假装生成过" in _html)
_ig_src8 = open(os.path.join(ROOT, "agent", "image_gen.py"), encoding="utf-8").read()
ok("控制台能接受'列表写成字符串'（省掉自定义增删端点）", "def _as_list" in _ig_src8 and "def _as_backends" in _ig_src8)

print("⑨ 真后端协议适配 + 本地自动发现（用**本地假服务**跑，不出网）")
import base64  # noqa: E402
import io  # noqa: E402
import threading  # noqa: E402
import http.server  # noqa: E402

_buf = io.BytesIO()
# ⚠️ 每次跑都用**不同**的像素值：假服务每次返回同一张图会让"去重层"在第二次跑时就把第一张也判成重复
#    ⇒ 自检假红（第一版就是这么红的）。随机一下，去重自检才稳定（第一张放行、第二张挡）。
Image.new("RGB", (640, 640), (hash(os.getpid() + int(time.time())) % 200 + 30, 140, 200)).save(_buf, format="PNG")
_PNG_B64 = base64.b64encode(_buf.getvalue()).decode("ascii")


class _FakeGen(http.server.BaseHTTPRequestHandler):
    """假 A1111：/sdapi/v1/txt2img 回 base64 图；/sdapi/v1/sd-models 回 200（探测用）。"""
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b"[]" if self.path.startswith("/sdapi/v1/sd-models") else b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception:
            req = {}
        out = json.dumps({"images": [_PNG_B64] * max(1, int(req.get("batch_size") or 1))}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


_srv = http.server.HTTPServer(("127.0.0.1", 0), _FakeGen)
_port = _srv.server_address[1]
threading.Thread(target=_srv.serve_forever, daemon=True).start()
# ⛔ V-R7-4：判据**不写产品媒体目录** `data/gen_images/`（`_save_image_bytes`/`_note_generated`
#   用的是**相对路径** `data/gen_images/...`）⇒ 本节把 cwd 切到临时目录，跑完切回。
#   产品默认行为不变（相对路径写法没动，只是判据期间 cwd 不同）。
_cwd0 = os.getcwd()
os.chdir(tempfile.mkdtemp(prefix="pm-ig-judge-"))
try:
    _det = IG.detect_local(timeout=2.0, probes=[{"id": "fake-a1111", "kind": "local", "proto": "a1111",
                                                 "port": _port, "path": "/sdapi/v1/sd-models",
                                                 "url": "http://127.0.0.1:%d" % _port}])
    ok("探测：本地有服务 ⇒ 认出来", len(_det) == 1 and _det[0]["id"] == "fake-a1111", str(_det))
    _det2 = IG.detect_local(timeout=0.4, probes=[{"id": "nobody", "kind": "local", "proto": "a1111",
                                                  "port": 9, "path": "/x", "url": "http://127.0.0.1:9"}])
    ok("探测：没服务 ⇒ 不误报", _det2 == [], str(_det2))
    _be = {"id": "fake-a1111", "kind": "local", "proto": "a1111", "url": "http://127.0.0.1:%d" % _port}
    _r = IG.call_backend(_be, "两只猫", count=2, size="square")
    ok("a1111 协议：拿到 2 张并落盘", len(_r.get("files") or []) == 2 and all(os.path.exists(p) for p in _r["files"]),
       str([os.path.basename(x) for x in _r.get("files") or []]))
    _okf, _resf = IG.run_filters(_r["files"][0], {"prompt": "两只猫"})
    _okf2, _resf2 = IG.run_filters(_r["files"][1], {"prompt": "两只猫"})
    _chain = IG.cfg().get("filter_chain") or {}
    if all(_chain.get(k) is not False for k in ("size", "dup", "blacklist")):
        ok("生成出来的图能过「尺寸/黑白名单」这两层",
           all(x["ok"] is True for x in _resf if x["name"] in ("size", "blacklist")),
           json.dumps([x for x in _resf if x["name"] in ("size", "blacklist")], ensure_ascii=False)[:80])
    # 假服务故意返回两张**一模一样**的图。注意：去重台账**只在过滤全过之后**才写（generate() 里调
    # `_note_generated`）⇒ 直接调 call_backend + run_filters 时两张都放行是**对的**；这里手工走一遍
    # "第一张已入账"的时序，验证第二张（字节相同）会被 dup 层挡下。
    _ledger = os.path.join("data", "gen_images", "index.jsonl")
    _backup = None
    try:
        if os.path.exists(_ledger):
            _backup = open(_ledger, encoding="utf-8").read()
        ok("记账动作返回 sha256（台账写成功）", len(IG._note_generated(_r["files"][0], "selftest")) == 64)
        _okA, _resA = IG.run_filters(_r["files"][1], {"prompt": "两只猫"})
        ok("第一张入账后，字节相同的第二张会被 dup 层挡下（去重真在干活）",
           any(x["ok"] is False for x in _resA if x["name"] == "dup"),
           json.dumps([x for x in _resA if x["name"] == "dup"], ensure_ascii=False)[:90])
    finally:
        try:
            if _backup is None:
                if os.path.exists(_ledger):
                    os.remove(_ledger)
            else:
                open(_ledger, "w", encoding="utf-8").write(_backup)
        except Exception:
            pass
    ok("协议里有 pollinations（免密钥在线）", IG.ONLINE_FREE["id"] == "pollinations" and "pollinations" in IG.ONLINE_FREE["url"])
    set_cfg(enabled=True, online_allowed=False)
    ok("不允许出网时 pollinations 不出现", all(b["id"] != "pollinations" for b in IG.backends()))
    set_cfg(enabled=True, online_allowed=True)
    ok("允许出网时 pollinations 出现在可用后端里（免密钥、不用用户配）",
       any(b["id"] == "pollinations" for b in IG.backends()), str([b["id"] for b in IG.backends()]))

    # ⛔ 第九轮 V-R9-24：远端回包里的 `data[].url` 原来是**无校验二次 GET**（假出图服务把它指向
    #   127.0.0.1 ⇒ 产品真去打内网/环回）。这里用两个假后端守：①回链指向别处的内网地址 ⇒ 必须拒；
    #   ②回链指向**后端自己**（本地部署的常态）⇒ 照常取回（证明不是一刀切误伤本地后端）。
    class _FakeOpenAI(http.server.BaseHTTPRequestHandler):
        url_to_return = ""

        def log_message(self, *a):
            pass

        def do_GET(self):
            raw = base64.b64decode(_PNG_B64)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                self.rfile.read(n)
            out = json.dumps({"data": [{"url": _FakeOpenAI.url_to_return}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    _srv2 = http.server.HTTPServer(("127.0.0.1", 0), _FakeOpenAI)
    _port2 = _srv2.server_address[1]
    threading.Thread(target=_srv2.serve_forever, daemon=True).start()
    _online = {"id": "fake-online", "kind": "online", "proto": "openai_image",
               "url": "http://127.0.0.1:%d/v1" % _port2, "model": "fake-model"}
    _FakeOpenAI.url_to_return = "http://127.0.0.1:41011/png"       # 别处的内网地址（E 线复现形态）
    try:
        IG.call_backend(_online, "两只猫")
        _refused, _why = False, ""
    except Exception as e:
        _refused, _why = True, "%s: %s" % (type(e).__name__, str(e)[:60])
    ok("V-R9-24：回包里的 url 指向内网 ⇒ **拒**（不二次 GET）", _refused and "不可信" in _why, _why)
    _FakeOpenAI.url_to_return = "http://127.0.0.1:%d/img" % _port2   # 同源（本地后端自己的静态资源）
    try:
        _rr = IG.call_backend(_online, "两只猫")
        _same_ok = len(_rr.get("files") or []) == 1 and os.path.exists(_rr["files"][0])
        _same_why = str([os.path.basename(x) for x in _rr.get("files") or []])
    except Exception as e:
        _same_ok, _same_why = False, "%s: %s" % (type(e).__name__, str(e)[:60])
    ok("阳性对照：同源回链照常取回（本地后端不被误伤）", _same_ok, _same_why)
    ok("V-R9-24：二次 GET 之前调的是 safe_fetch 的统一闸门（源码断言）",
       "guard_remote_url" in open(os.path.join(ROOT, "agent", "image_gen.py"), encoding="utf-8").read())
    try:
        _srv2.shutdown()
    except Exception:
        pass
finally:
    try:
        _srv.shutdown()
    except Exception:
        pass
    os.chdir(_cwd0)

print("⑥ 红线在源码里是硬编码的（结构断言）")
src = open(os.path.join(ROOT, "agent", "image_gen.py"), encoding="utf-8").read()
ok("ALLOW_REAL_FACE 恒 False", "ALLOW_REAL_FACE = False" in src)
# ⚠️ 断言不能拿"整个函数体"找 r18：函数自己的 docstring 里就写着"不构造 r18 字段" ⇒ 假红。
#    只断言**真正拼请求体的那一行**（json.dumps 那行）里不含 r18。
_body_line = ""
for _ln in src.split("def call_backend")[1].split("\ndef ")[0].splitlines():
    if "json.dumps" in _ln:
        _body_line = _ln
ok("请求体那一行里没有 r18 字段", _body_line != "" and "r18" not in _body_line, _body_line.strip()[:90])
ok("没有 '过滤器出错就放行' 的分支（fail-closed）", "good is not True" in src and "ok = False" in src)
ok("真人换脸用**模式**匹配（不是只列固定词）", "_REAL_FACE_RX" in src and "def wants_real_face" in src)

IG.cfg = _orig_cfg
print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)



