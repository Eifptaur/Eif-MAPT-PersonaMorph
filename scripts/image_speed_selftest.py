# -*- coding: utf-8 -*-
"""「要图」速度纪律判据（2026-09-17 立）。

起因（用户实测）：在拍摄现场，说一句要图到停下来**过了一分多钟**。查下来三个独立病因：
  ① `_get()` 用一次阻塞 `read()` —— `urlopen(timeout=)` 只管**单次 recv**，慢速代理能涓流几分钟
     （实测一张 4.26MB 的图在 `i.pixiv.re` 上拖了 **160 秒**）；
  ② 图源按顺序问，四个死源（SSL/403/DNS/超时）每次白等 **27 秒**；
  ③ 慢源一旦被排在前面，就把整段预算吃光，后面的快源一张也轮不上。

本判据守六条（全部离线可跑，不真的出网）：
  A. 下载有**整张图的总时长上限**（不再受"单次 recv"摆布），超时**不留半截文件**；
  B. 下载有**体积硬上限**，超了立刻放弃；
  C. `soft_max_mb`：服务器报了大 Content-Length 就**立刻放弃**，不白下几 MB；
  D. 图源元数据是**并发**问的（一个慢源不能拖住整件事）；
  E. 失败过的图源**短冷却**，且**只惩罚真错误**（超时/慢不该被冷却）；
  F. 在线整体取不到时**用本地已有的图兜底**（用户在群里不能干等十几秒最后什么都没有）。

用法：runtime\\python\\python.exe scripts\\image_speed_selftest.py
"""
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import image_lib as IL      # noqa: E402
from agent import image_sources as IS  # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [%s]" % detail if detail else ""))


# ── 一个"能吐一点、然后卡住"的本地服务：专治"单次 recv 超时"那种假超时 ──────────────
class _Slow(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/big"):
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(9 * 1024 * 1024))     # 声明 9MB，也确实吐 9MB
            self.end_headers()
            try:
                left = 9 * 1024 * 1024
                while left > 0:
                    n = min(262144, left)
                    self.wfile.write(b"x" * n)
                    left -= n
                self.wfile.flush()
            except Exception:
                pass
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(50 * 1024 * 1024))        # 声明 50MB，但只慢慢吐
        self.end_headers()
        try:
            for _ in range(400):
                self.wfile.write(b"x" * 4096)
                self.wfile.flush()
                time.sleep(0.05)                                         # 合计约 20 秒，每块都不慢
        except Exception:
            pass


_srv = HTTPServer(("127.0.0.1", 0), _Slow)
_port = _srv.server_port
threading.Thread(target=_srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % _port
# 判据自己要打内网，所以临时放开 SSRF 闸门（只在本进程内）
IS.allow_private_hosts = lambda: True

tmp = tempfile.mkdtemp(prefix="pm_imgspeed_")

print("── A. 下载有整张图的总时长上限（不是单次 recv）──")
t0 = time.time()
p, err = IS.download(BASE + "/slow", tmp, max_mb=100, timeout_ms=2500)
dt = time.time() - t0
ok("慢速涓流会被总时长掐断（≤4.5 秒）", p is None and dt <= 4.5, "%.2fs / %s" % (dt, str(err)[:50]))
ok("掐断的原因是超时（如实报出来）", "TimeoutError" in str(err) or "总耗时" in str(err), str(err)[:60])
ok("不留半截文件（.part 与成品都不留）",
   not [f for f in os.listdir(tmp) if f.endswith(".part")] and not os.listdir(tmp), str(os.listdir(tmp)))

print("── B/C. 体积上限 + 软上限（大图当场放弃，别白下）──")
t0 = time.time()
p2, err2 = IS.download(BASE + "/big", tmp, max_mb=8, timeout_ms=4000, soft_max_mb=4)
ok("Content-Length 超软上限 ⇒ 立刻放弃（<1 秒）",
   p2 is None and (time.time() - t0) < 1.0 and "软上限" in str(err2), "%.2fs / %s" % (time.time() - t0, str(err2)[:44]))
t0 = time.time()
p3, err3 = IS.download(BASE + "/big", tmp, max_mb=8, timeout_ms=4000, soft_max_mb=0)
ok("关掉软上限后仍被体积硬上限挡住（不落盘几 MB）",
   p3 is None and (time.time() - t0) < 2.5 and ("上限" in str(err3)), "%.2fs / %s" % (time.time() - t0, str(err3)[:44]))
ok("超限的两条路都不留半截文件", not os.listdir(tmp), str(os.listdir(tmp)))

print("── D. 元数据并发问（慢源不许拖住整件事）──")
_real_meta = IS.fetch_meta


def _slow_meta(src, cfg=None):
    time.sleep(2.5)
    return None, "图源 %s 故意慢" % src


def _fast_meta(src, cfg=None):
    return {"url": "http://127.0.0.1:%d/ok" % _port, "page": "p", "tags": []}, ""


IS.fetch_meta = lambda src, cfg=None: (_fast_meta(src, cfg) if "fast" in str(src) else _slow_meta(src, cfg))
IS.download = lambda url, dest, max_mb=8, timeout_ms=9000, **kw: (None, "下载失败：假失败（只为量时序）")
cfg = {"image_reply": {"sources": ["slow", "fast"], "sources_per_try": 2, "total_budget_ms": 8000,
                       "meta_timeout_ms": 6000, "meta_grace_ms": 300}}
t0 = time.time()
got, why = IL.fetch_filtered(cfg, root=tmp)
dt = time.time() - t0
ok("慢源不会把整件事拖满（<2 秒就往下走）", dt < 2.0, "%.2fs / %s" % (dt, str(why)[:60]))
ok("真错误/假失败如实报出", "假失败" in str(why) or "下载" in str(why), str(why)[:60])

print("── E. 图源冷却：只惩罚真错误 ──")
IS.fetch_meta = lambda src, cfg=None: (None, "图源 %s 请求失败：HTTPError: HTTP Error 403" % src)
cfg2 = {"image_reply": {"sources": ["bad"], "sources_per_try": 1, "source_cooldown_s": 60}}
IL.fetch_filtered(cfg2, root=tmp)
ok("真错误（403）⇒ 该图源进入冷却", bool(IL.source_health().get("bad")), str(IL.source_health()))
IS.fetch_meta = lambda src, cfg=None: (None, "图源 %s 请求失败：URLError: timed out" % src)
IL._FAIL_UNTIL.clear()
cfg3 = {"image_reply": {"sources": ["slowone"], "sources_per_try": 1, "source_cooldown_s": 60}}
IL.fetch_filtered(cfg3, root=tmp)
ok("**超时不算真错误**（不冤慢源，只记原因）", not IL.source_health().get("slowone"), str(IL.source_health()))
IS.fetch_meta = _real_meta

print("── F. 在线取不到时用本地已有的图兜底 ──")
cache = os.path.join(tmp, "media", "images")
os.makedirs(cache, exist_ok=True)
old_img = os.path.join(cache, "src_000000_abc.jpg")
open(old_img, "wb").write(b"\xff\xd8\xff" + b"y" * 500)
_real_ff = IL.fetch_filtered
IL.fetch_filtered = lambda *a, **k: (None, "试了 4 个图源都没通过过滤")
try:
    p4, why4 = IL.search_image({"image_reply": {"enabled": True, "allow_search": True}}, "猫", root=tmp)
    ok("在线全失败 ⇒ 从以前要到的图里挑一张（不空手）",
       bool(p4) and "以前" in str(why4), "%s / %s" % (p4, str(why4)[:50]))
finally:
    IL.fetch_filtered = _real_ff

# 兜底池的上限：超过 keep 张要清掉老的（用户口径：会写盘就要有上限）
for i in range(60):
    open(os.path.join(cache, "src_%06d_x.jpg" % i), "wb").write(b"\xff\xd8\xff" + b"z" * 200)
IL._cache_pick(cache, keep=40)
ok("兜底池有上限（不会无限长胖）", len([f for f in os.listdir(cache) if f.endswith(".jpg")]) <= 45,
   "%d 张" % len([f for f in os.listdir(cache) if f.endswith(".jpg")]))

_srv.shutdown()
import shutil  # noqa: E402
shutil.rmtree(tmp, ignore_errors=True)
print("\n要图速度纪律判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
