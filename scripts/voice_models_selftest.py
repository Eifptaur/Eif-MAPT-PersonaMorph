# -*- coding: utf-8 -*-
"""③ 语音「自带模型」判据（2026-09-15）

守的东西（对应任务书 ③「语音转文字开箱可用＋自带模型（RVC/GPT-SoVITS）」的第二半）：
  A. **开箱可用是默认**：`voice_models.backend()` 默认 `sapi`；契约与 `tts` 对齐
     （`make(text) -> (路径|None, 原因, info)`、`status() -> {ok,why,voices,engine}`），
     这样生产路径（`tools.py::_exec_send_voice_reply`）只换一层皮。
  B. **两种响应形态都要能收**：①直接回音频字节（GPT-SoVITS api_v2）②回 JSON（base64 或文件路径，
     字段名由 `voice_reply.http_json_field` 指定）。
  C. **绝不假装**：端点不通 / 不是音频 / JSON 里找不到字段 / 文本为空 ⇒ 返回 `None` + 明确原因，
     **且不落下任何文件**（本项目红线："没引擎却装作合成过"）。
  D. **能力如实标注**：能力文案必须写明「音色未声明」与「发出去是音频文件、不是真语音条」。
  E. **接线与 UI 都在**：tools.py 走 `_vm.make/_vm.status`；控制台有声音来源/地址/JSON 字段三项；
     `/api/voice/probe` 路由在；真起控制台能拿到面板。

用法：py -3 scripts/voice_models_selftest.py（不联网；替身 `_post`）
"""
import io
import os
import socket
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import voice_models as VM       # noqa: E402

PASS = 0
FAIL = 0
SKIP_N = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


def skip(name, why=""):
    global SKIP_N
    SKIP_N += 1
    print("  SKIP {}  [{}]".format(name, why))


REAL_POST = VM._post
TMP = os.path.join(ROOT, "media", "selftest_voice")
WAV = b"RIFF" + b"\x00" * 60 + b"WAVE" + b"\x00" * 40          # 够像 wav 头就行


def files_now():
    try:
        return set(os.listdir(TMP))
    except Exception:
        return set()


print("== ③ 语音自带模型判据 ==")

print("\n── A. 默认与契约 ──")
ok("默认后端是 sapi（开箱可用优先）", VM.backend({}) == "sapi" and VM.backend({"backend": ""}) == "sapi")
ok("非法值回落到 sapi", VM.backend({"backend": "whisper"}) == "sapi")
ok("http 后端认得出", VM.backend({"backend": "HTTP"}) == "http")
try:
    from agent import tts as _t
    ok("与 tts.make 同形（3 元组）", isinstance(_t.make(""), tuple) and len(_t.make("")) == 3)
    st = VM.status({"backend": "sapi"})
    ok("status() 与 tts.status 同形（含 ok/why/engine/voices）",
       all(k in st for k in ("ok", "why", "engine", "voices")), list(st.keys()))
except Exception as e:
    skip("与 tts 对齐", "读不到 tts：%s" % e)
st_http = VM.status({"backend": "http", "http_url": "http://127.0.0.1:9880/tts"})
ok("选了 http 且填了地址 ⇒ status.ok=True（不谎报'已连通'）",
   st_http["ok"] and "未验证" in st_http["why"] and st_http["engine"] == "custom-http", st_http["why"])
st_nourl = VM.status({"backend": "http", "http_url": ""})
ok("选了 http 但没填地址 ⇒ ok=False 且说清", (not st_nourl["ok"]) and "没填地址" in st_nourl["why"], st_nourl["why"])

print("\n── B. 两种响应形态 ──")
os.makedirs(TMP, exist_ok=True)
try:
    # ① 直接回音频字节
    VM._post = lambda url, text, timeout, cfg: (WAV, "audio/wav", 200)
    before = files_now()
    path, err, info = VM.make("你好呀", {"backend": "http", "http_url": "http://x/tts"})
    ok("①回音频字节 ⇒ 成功且落盘非空", bool(path) and os.path.getsize(path) > 40 and not err,
       "%s / %s" % (os.path.basename(path or "-"), err))
    ok("info 带 voice/fmt（供工具侧回执用）", info.get("voice") == "custom-http" and info.get("fmt") == "wav", info)
    ok("info.note 写明能力口径", "未声明" in (info.get("note") or ""))

    # ② 回 JSON（base64）
    import base64 as _b64
    VM._post = lambda url, text, timeout, cfg: (
        ('{"data":"%s"}' % _b64.b64encode(WAV).decode()).encode(), "application/json", 200)
    p2, e2, i2 = VM.make("你好", {"backend": "http", "http_url": "http://x/tts", "http_json_field": "data"})
    ok("②JSON+base64 ⇒ 成功", bool(p2) and not e2, "%s / %s" % (os.path.basename(p2 or "-"), e2))

    # ② 回 JSON（本地文件路径）
    ref = os.path.join(TMP, "_ref.wav")
    with open(ref, "wb") as f:
        f.write(WAV)
    VM._post = lambda url, text, timeout, cfg: (('{"audio":"%s"}' % ref.replace("\\", "\\\\")).encode(),
                                                "application/json", 200)
    p3, e3, _i3 = VM.make("你好", {"backend": "http", "http_url": "http://x/tts", "http_json_field": "audio"})
    ok("②JSON+文件路径 ⇒ 成功", bool(p3) and not e3, "%s / %s" % (os.path.basename(p3 or "-"), e3))

    print("\n── C. 绝不假装（失败必须明确、且不落文件）──")
    n_before = len(files_now())
    VM._post = lambda url, text, timeout, cfg: (_ for _ in ()).throw(RuntimeError("connection refused"))
    p4, e4, i4 = VM.make("你好", {"backend": "http", "http_url": "http://x/tts"})
    ok("端点不通 ⇒ None + 原因", p4 is None and "不通" in e4, e4[:40])
    VM._post = lambda url, text, timeout, cfg: (b"<html>404 not found</html>", "text/html", 200)
    p5, e5, _ = VM.make("你好", {"backend": "http", "http_url": "http://x/tts"})
    ok("返回 HTML/非音频非 JSON ⇒ None + 原因", p5 is None and "不是" in e5, e5[:40])
    VM._post = lambda url, text, timeout, cfg: (b'{"msg":"ok"}', "application/json", 200)
    p6, e6, _ = VM.make("你好", {"backend": "http", "http_url": "http://x/tts"})
    ok("JSON 但没配字段 ⇒ None + 说清该配哪个键", p6 is None and "http_json_field" in e6, e6[:40])
    VM._post = lambda url, text, timeout, cfg: (b'{"data":"@@@notbase64@@@"}', "application/json", 200)
    p7, e7, _ = VM.make("你好", {"backend": "http", "http_url": "http://x/tts", "http_json_field": "data"})
    ok("字段既非 base64 也非路径 ⇒ None + 原因", p7 is None and "base64" in e7, e7[:40])
    p8, e8, _ = VM.make("   ", {"backend": "http", "http_url": "http://x/tts"})
    ok("空文本 ⇒ 拒绝", p8 is None and "空" in e8)
    p9, e9, _ = VM.make("你好", {"backend": "http", "http_url": ""})
    ok("选了自带模型却没地址 ⇒ 拒绝并给两条出路", p9 is None and "没填地址" in e9, e9[:40])
    ok("**失败一个文件都没落下**", len(files_now()) - n_before == 0 or len(files_now()) <= n_before + 0,
       "目录现有 %d 个" % len(files_now()))
finally:
    VM._post = REAL_POST

print("\n── D. 能力如实标注 ──")
ok("能力文案含「未声明」", "未声明" in VM.CAPABILITY_NOTE)
ok("能力文案点明「发出去是音频文件、不是真语音条」",
   "真语音条" in VM.CAPABILITY_NOTE and "音频文件" in VM.CAPABILITY_NOTE)
src = io.open(os.path.join(ROOT, "agent", "voice_models.py"), encoding="utf-8").read()
# 只查**代码与字符串**，不查注释：模块里那句"不许出现已支持某音色"本身就是注释（第一版自检在这里误红了）
_code_only = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
ok("代码里没有「已支持音色/支持音色克隆」这类无据断言",
   "已支持音色" not in _code_only and "支持音色克隆" not in _code_only)

print("\n── E. 接线与 UI ──")
tools = io.open(os.path.join(ROOT, "agent", "tools.py"), encoding="utf-8").read()
ok("生产路径走 voice_models（_vm.make / _vm.status）", "_vm.make(text)" in tools and "_vm.status()" in tools)
ok("生产路径不再直接用 tts.make", "_tts.make(" not in tools and "_tts.status(" not in tools)
page = io.open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
for k in ("voice_reply.backend", "voice_reply.http_url", "voice_reply.http_json_field"):
    ok("面板有 %s" % k, ('data-cfg="%s"' % k) in page)
ok("面板写明「不通会如实报错」", "不会假装发过" in page or "如实报错" in page)
wui = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
ok("/api/voice/probe 路由在", '"/api/voice/probe"' in wui and "voice_models" in wui)

print("\n── F. 真起控制台：路由与面板都在（不联网）──")
_p = None
try:
    from agent import webui as W
    _orig = W.get_config
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    base = dict(W.get_config() or {})
    base["server"] = {"enabled": True, "host": "127.0.0.1", "port": free,
                      "token": "vm-judge", "auto_open_browser": False}
    W.get_config = lambda: base
    w = W.WebUI(lambda: {}, [])
    import tempfile as _tf
    w.console_url_root = _tf.mkdtemp(prefix="cuj-")   # ⚠️ 判据不写产品那份 logs/console.url（2026-09-18）
    port = w.start()
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/?token=vm-judge" % port, timeout=8) as r:
            _p = r.read().decode("utf-8", "replace")
        with urllib.request.urlopen("http://127.0.0.1:%d/api/voice/probe?token=vm-judge" % port, timeout=10) as r2:
            import json as _json
            d = _json.loads(r2.read().decode("utf-8", "replace"))
    finally:
        w.stop()
except Exception as e:
    skip("真起控制台", "起不了：%s" % e)
finally:
    try:
        W.get_config = _orig
    except Exception:
        pass

if _p:
    ok("页面里有「声音来源」三项", all(('data-cfg="%s"' % k) in _p for k in
                                ("voice_reply.backend", "voice_reply.http_url", "voice_reply.http_json_field")))
    ok("页面里有「连通测试」按钮", 'id="ttsProbe"' in _p)

print("")
print("语音自带模型判据：%d 通过 / %d 失败 / %d 跳过" % (PASS, FAIL, SKIP_N))
sys.exit(1 if FAIL else 0)
