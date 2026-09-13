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
ok("没配后端 ⇒ 拒绝并给出人话原因", IG.pick_backend()[0] is None and ("没探到" in IG.pick_backend()[1] or "没配" in IG.pick_backend()[1]), IG.pick_backend()[1])
set_cfg(enabled=True, backends=[{"id": "online-x", "kind": "online", "url": "http://127.0.0.1:9/x"}])
ok("只配在线后端但没允许出网 ⇒ 拒绝", IG.pick_backend()[0] is None and "online_allowed" in IG.pick_backend()[1],
   IG.pick_backend()[1])
set_cfg(enabled=True, online_allowed=True, backends=[{"id": "online-x", "kind": "online", "url": "http://127.0.0.1:9/x"}])
ok("允许出网后才可选到在线后端", IG.pick_backend()[0]["id"] == "online-x")

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
set_cfg(enabled=True)
r = IG.generate("x", "把照片换成真人脸")
ok("真人换脸 ⇒ 拒（红线，无开关）", r["ok"] is False and "红线" in r["why"], r["why"])
r2 = IG.generate("x", "来张 r18 的图")
ok("成人内容 ⇒ 拒", r2["ok"] is False, r2["why"])
r3 = IG.generate("x", "画只猫")
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
#    ⇒ 判据假红（第一版就是这么红的）。随机一下，去重判据才稳定（第一张放行、第二张挡）。
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
finally:
    try:
        _srv.shutdown()
    except Exception:
        pass

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



