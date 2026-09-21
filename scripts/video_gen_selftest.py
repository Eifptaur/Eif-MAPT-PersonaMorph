# -*- coding: utf-8 -*-
"""AI 视频生成判据（2026-09-15）。

守用户点的七件事（"相关的一切事宜"）：
  A. **默认关**：没配后端时不假装能做——`generate()`/`submit()` 都如实拒绝。
  B. **不二择一**：后端可填多个、本地与在线都留着，按顺序挨个试；认不得的协议**如实报**，
     不许静默当通用口子。
  C. **红线在调用后端之前**：真人换脸这类，**根本不发请求**（用假服务计数验证"没被调用"）。
  D. **三种响应都收**：回视频字节 / 回 JSON 里的下载地址 / 回 JSON 里的本地路径。
  E. **过滤链 fail-closed**：体积超限不过、红线命中不过、**不过的产物当场删掉**。
  F. **异步不卡聊天**：`submit()` 立刻返回，后台跑完状态变 done/failed，原因可查。
  G. **六格接线**：配置键 + 示例同步 + 控制台面板与引导 + 工具注册（描述写明"不许说做好了"）
     + 只读快照。

用法：`py -3 scripts/video_gen_selftest.py`（全部在**本机假 HTTP 服务**上跑，不出网）
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import video_gen as VG          # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   %s%s" % (name, ("（%s）" % detail) if detail else ""))
    else:
        FAIL += 1
        print("  FAIL %s%s" % (name, ("（%s）" % detail) if detail else ""))


def sect(t):
    print("\n── %s ──" % t)


# ── 假后端：三种响应形态 + 计数（用来验"红线时根本没调用"）────────────────────
MP4 = (b"\x00\x00\x00\x20ftypisom" + b"\x00" * 9000)          # 够长，能过体积下限
HITS = {"bytes": 0, "url": 0, "path": 0}


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/dl"):
            HITS["url"] += 1
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(MP4)))
            self.end_headers()
            self.wfile.write(MP4)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        if self.path == "/bytes":
            HITS["bytes"] += 1
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(MP4)))
            self.end_headers()
            self.wfile.write(MP4)
        elif self.path == "/url":
            HITS["url"] += 1
            body = json.dumps({"url": BASE + "/dl"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/path":
            HITS["path"] += 1
            body = json.dumps({"files": [LOCAL_MP4]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = b'{"error":"nothing"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


TMP = tempfile.mkdtemp(prefix="pm-vgtest-")
LOCAL_MP4 = os.path.join(TMP, "local.mp4")
with open(LOCAL_MP4, "wb") as f:
    f.write(MP4)
_srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
BASE = "http://127.0.0.1:%d" % _srv.server_address[1]
threading.Thread(target=_srv.serve_forever, daemon=True).start()

_real_cfg = VG.cfg
_real_seen = VG._seen_path
# 自检不污染真数据：去重用的"见过指纹"文件指到临时目录（否则跑一次自检就把真库塞满测试指纹）
VG._seen_path = lambda: os.path.join(TMP, "seen.json")
# ⛔ V-R7-4：成品目录也指到临时目录 —— 原来往用户**生产媒体目录** `data/gen_videos/` 落
#   `gen_generic-*.mp4`（用户看得见）。产品默认行为不变：`out_dir()` 本身没改，只是这里打桩。
VG.out_dir = lambda: TMP


def set_cfg(**kw):
    base = {"enabled": True, "trigger_mode": "on_request", "backends": "", "online_allowed": False,
            "seconds_default": 5, "timeout": 30,
            # dup 在下面单测一次；其余用例关掉它，否则"同一段测试字节"会被判成重复
            "filter_chain": {"size": True, "duration": True, "dup": False,
                             "redline": True, "classifier": True}}
    base.update(kw)
    VG.cfg = lambda: base


try:
    sect("A. 默认关：没配后端就如实拒绝（不假装）")
    set_cfg(enabled=False, backends=BASE + "/bytes")
    r = VG.generate("给我做一条小猫的视频")
    ok("关着时 generate 如实拒绝", (not r["ok"]) and "没开" in r["why"], r["why"][:40])
    s = VG.submit("给我做一条小猫的视频")
    ok("关着时 submit 也不接", (not s["ok"]) and "没开" in s["why"], s["why"][:40])
    set_cfg(enabled=True, backends="")
    r0 = VG.generate("给我做一条小猫的视频")
    ok("开着但没配后端 ⇒ 如实说没配后端", (not r0["ok"]) and "还没配" in r0["why"], r0["why"][:44])

    sect("B. 不二择一：多后端解析 + 在线闸 + 认不得的协议如实报")
    set_cfg(backends="http://127.0.0.1:8189/a, comfyui:http://127.0.0.1:8188")
    bs = VG.backends()
    ok("两个后端都解析出来、协议各归各", len(bs) == 2 and bs[0]["proto"] == "generic" and bs[1]["proto"] == "comfyui")
    cands, why = VG.candidate_backends()
    ok("本机地址不经出网闸也能试", len(cands) == 2, why[:30])
    set_cfg(backends="generic:https://relay.example/v1/video", online_allowed=False)
    c2, w2 = VG.candidate_backends()
    ok("只配在线 + 出网闸关 ⇒ 一个都不试，且说明原因", (not c2) and "出网" in w2, w2[:40])
    set_cfg(backends="generic:https://relay.example/v1/video", online_allowed=True)
    ok("打开出网闸后才能试在线", len(VG.candidate_backends()[0]) == 1)
    set_cfg(backends="sora:https://api.example/v1/videos")
    b3 = VG.backends()
    ok("认不得的协议标成 __unsupported__（不静默当通用）",
       len(b3) == 1 and b3[0]["proto"] == "__unsupported__", str(b3[:1])[:60])
    c4, w4 = VG.candidate_backends()
    ok("认不得协议时给的原因点出是协议问题", (not c4) and "协议" in w4, w4[:40])

    sect("C. 红线在调用后端之前就拦（后端根本不该被调用）")
    before = dict(HITS)
    set_cfg(backends=BASE + "/bytes")
    rr = VG.generate("做一个真人换脸的视频")
    ok("真人换脸 ⇒ 拒绝", (not rr["ok"]) and "红线" in rr["why"], rr["why"][:40])
    ok("**后端一次都没被调用**（计数没变）", HITS == before, "%s -> %s" % (before, HITS))

    sect("D. 三种响应形态都收")
    set_cfg(backends=BASE + "/bytes")
    d1 = VG.generate("一条小猫")
    ok("① 回视频字节 ⇒ 收下并落盘", d1["ok"] and os.path.isfile(d1["files"][0]),
       "%s" % (d1.get("why") or os.path.basename(d1["files"][0] if d1["files"] else "")))
    set_cfg(backends=BASE + "/url")
    d2 = VG.generate("一条小狗")
    ok("② 回 JSON 里的下载地址 ⇒ 再下回来", d2["ok"] and os.path.getsize(d2["files"][0]) == len(MP4))
    set_cfg(backends=BASE + "/path")
    d3 = VG.generate("一条小鱼")
    ok("③ 回 JSON 里的本地路径 ⇒ 直接收下", d3["ok"] and d3["files"][0] == LOCAL_MP4)
    set_cfg(backends=BASE + "/nothing")
    d4 = VG.generate("一条小鸟")
    ok("反证：回的不是视频 ⇒ 如实报错（不落空文件）", (not d4["ok"]) and "视频" in d4["why"], d4["why"][:44])

    sect("E. 过滤链 fail-closed：不过的当场删掉")
    set_cfg(backends=BASE + "/bytes")
    VG.MAX_MB = 0.0001                                  # 让它必然超体积
    e1 = VG.generate("一条很短的猫")
    ok("体积超限 ⇒ 不过", (not e1["ok"]) and "过滤链" in e1["why"], e1["why"][:40])
    VG.MAX_MB = 30.0
    e2 = VG.generate("再看一条")
    ok("正常尺寸 ⇒ 过（反证前面不是恒不过）", e2["ok"], e2.get("why", ""))
    # 去重那层单独测：同一段字节第二次必须被判重复（这是它唯一的用途）
    set_cfg(backends=BASE + "/bytes", filter_chain={"dup": True})
    d_first = VG.generate("去重测试第一条")
    d_second = VG.generate("去重测试第二条")
    ok("反证：开了去重 ⇒ 一模一样的第二条会被判重复",
       d_first["ok"] and (not d_second["ok"]) and "重复" in json.dumps(d_second.get("notes") or [], ensure_ascii=False),
       str(d_second.get("notes"))[:60])

    sect("F. 异步：submit 立刻返回，后台跑完状态可查")
    set_cfg(backends=BASE + "/bytes")
    t0 = time.time()
    sub = VG.submit("一条异步测试的视频")
    el = time.time() - t0
    ok("submit 立刻返回（<1 秒）", sub["ok"] and el < 1.0, "%.3fs" % el)
    ok("拿到 job_id", bool(sub.get("job_id")))
    st = None
    for _ in range(80):
        time.sleep(0.25)
        st = VG.job(sub["job_id"])
        if st.get("state") != "running":
            break
    ok("后台跑完状态变 done 且有文件", st and st.get("state") == "done" and st.get("files"),
       str((st or {}).get("state")))

    sect("G. 六格接线：配置 / 示例 / 面板 / 引导 / 工具 / 快照")
    from agent.config import DEFAULT_CONFIG as D
    from agent import media_status as MS
    vc = D.get("video_gen") or {}
    ok("配置里有 video_gen 段且默认关", vc.get("enabled") is False and vc.get("trigger_mode") == "on_request")
    ex = json.load(open("config.example.json", encoding="utf-8"))
    ok("config.example.json 同步有这一段", ex.get("video_gen") == vc)
    H = open("agent/console_html.py", encoding="utf-8").read()
    for k in ("video_gen.enabled", "video_gen.trigger_mode", "video_gen.backends",
              "video_gen.online_allowed", "video_gen.seconds_default", "video_gen.comfy_workflow"):
        ok("控制台有 %s" % k, ('data-cfg="%s"' % k) in H)
    ok("有引导按钮 vgGuide 且挂到 video 引导", 'id="vgGuide"' in H and "['vgGuide','video']" in H)
    ok("引导里有后端写法示例与红线说明", "comfyui:http://127.0.0.1:8188" in H and "不做真人换脸" in H)
    defs = {t["name"]: t for t in __import__("agent.tools", fromlist=["x"]).build_tool_defs()}
    ok("工具表里有 gen_video", "gen_video" in defs)
    ok("描述写明慢、要分享后台、不许说做好了",
       "很慢" in defs["gen_video"]["description"] and "绝不许说已经做好了" in defs["gen_video"]["description"])
    snap = MS.snapshot().get("video_gen") or {}
    ok("只读快照在 /api/status 里（含 backends/limits/running）",
       all(k in snap for k in ("enabled", "backends", "limits", "running", "filter_chain")))
finally:
    VG.cfg = _real_cfg
    VG._seen_path = _real_seen
    _srv.shutdown()

print("\n%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
