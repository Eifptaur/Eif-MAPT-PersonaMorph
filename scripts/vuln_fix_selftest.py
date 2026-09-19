# -*- coding: utf-8 -*-
"""漏洞修复的回归判据（2026-09-20 立，对应只读审计清单 `漏洞清单-20260920.md` 的 V1~V5 / V8）。

为什么单开一个文件：这五条的现场都在**真机状态**上（HTTP 路由、临时包、慢网、配置脱敏），
必须用**临时目录 + 打桩**把它们封闭起来才可复跑；散进各自的 selftest 会污染那些既有判据的语义。

覆盖：
  V1  免认证路径穿越：`/assets/emoji/..%2F..%2Fconfig.json` 与 `/wallpaper/…` 必须 404
  V2  同版本号换包：版本相同但内容指纹不同 ⇒ `run_once` 必须继续装（不许回"已是最新"）
  V3  只读文件按真故障回滚；被占用(winerror=32) ⇒ 记 pendingFiles、版本不推进、解锁后能补换
  V4  `run_once(dry=True)` 不许交接（不调 `_relaunch_after_update`、needRestart=False）
  V5  慢源的"耐心阶段"要真的等（打桩 fetch 睡 5 秒 ⇒ 必须拿到清单）；复核拿到清单就直接用
  V8  `masked_config()` 必须掩掉 `feedback.webhook_token`，且 `_protect_secrets()` 不许用掩码值覆盖真值

用法：`runtime\\python\\python.exe scripts\\vuln_fix_selftest.py`
"""
from __future__ import annotations

import io
import os
import stat
import sys
import tempfile
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import update_apply as U        # noqa: E402
from agent import update_check as uc       # noqa: E402
from agent import webui as W               # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [%s]" % detail if detail else ""))


class _H(object):
    """假的 HTTP handler：只记状态码与字节数。"""

    def __init__(self):
        self.headers = {}
        self.code = None
        self.n = 0

    def _bytes(self, b, ctype="", code=200):
        self.code = code
        self.n = len(b or b"")


def mk_pkg(d, rel="agent/c.py", body="NEW"):
    zp = os.path.join(d, "p.zip")
    z = zipfile.ZipFile(zp, "w")
    z.writestr("persona morph/" + rel, body)
    z.close()
    return zp, U.zip_tree(zp)[0]


print("── V1 免认证路径穿越必须 404（且正常素材仍要能取到）──")
o = W.WebUI.__new__(W.WebUI)
o._asset_root = os.path.join(ROOT, "assets")
o._data_path = lambda n: os.path.join(ROOT, "data", n)
o.whale = None
o._icon_bytes = b""
h = _H()
o._serve_asset("/assets/emoji/..%2F..%2Fconfig.json", h)
ok("emoji 分支穿越 config.json ⇒ 404", h.code == 404, "code=%s bytes=%s" % (h.code, h.n))
h2 = _H()
o._serve_wallpaper("/wallpaper/..%2F..%2F..%2Fconfig.json", h2, "")
ok("wallpaper 分支穿越 ⇒ 404", h2.code == 404, "code=%s bytes=%s" % (h2.code, h2.n))
h3 = _H()
o._serve_asset("/assets/icon-whale.png", h3)
ok("正常素材仍能取到（阴性对照：别把功能一起关掉）", h3.code == 200 and h3.n > 1000,
   "code=%s bytes=%s" % (h3.code, h3.n))
_emoji_base = os.path.realpath(o._data_path("emojis"))
_esc = o._safe_join(_emoji_base, "..%2F..%2Fconfig.json")
ok("**越界名一律返回空串**（拼出来的路径绝不许跑到 base 外）",
   _esc == "" or os.path.realpath(_esc).startswith(_emoji_base + os.sep), str(_esc))
ok("正常文件名仍能拼出 base 内的真路径（阴性对照）",
   o._safe_join(_emoji_base, "abc123.jpg").startswith(_emoji_base + os.sep),
   o._safe_join(_emoji_base, "abc123.jpg"))

print("── V2 同版本号换包必须继续装 ──")
d = tempfile.mkdtemp(prefix="pm_vf_v2_")
zp, tree = mk_pkg(d)
mine = uc.current_version()
_relaunch = []
_real_relaunch = U._relaunch_after_update
U._relaunch_after_update = lambda v="": _relaunch.append(v)
try:
    r = U.run_once(manifest={"base": {"version": mine, "sha256": tree, "url": "", "build": "deadbeefcafe"}},
                   zip_path=zp, target=d)
    ok("版本相同 + 指纹不同 ⇒ 不回「已是最新」", r.get("msg") != "已是最新", str(r.get("msg"))[:60])
    ok("…且真的换了文件", os.path.exists(os.path.join(d, "agent", "c.py")), "rc=%s" % r.get("rc"))
finally:
    U._relaunch_after_update = _real_relaunch

print("── V3 只读=真故障回滚；被占用=记待补、解锁后能补 ──")
d2 = tempfile.mkdtemp(prefix="pm_vf_v3a_")
os.makedirs(os.path.join(d2, "agent"))
f2 = os.path.join(d2, "agent", "c.py")
io.open(f2, "w").write("OLD")
os.chmod(f2, stat.S_IREAD)
zp2, tree2 = mk_pkg(d2, body="NEW")
rc2, msg2, det2 = U.apply_full({"base": {"version": "9999.9.9", "sha256": tree2, "url": ""}}, zp2, d2)
ok("只读文件 ⇒ rc=1 回滚（不许算「被占用」）", rc2 == 1 and not det2.get("locked"),
   "rc=%s locked=%s" % (rc2, det2.get("locked")))
ok("…旧内容原样（真回滚）", io.open(f2).read() == "OLD", io.open(f2).read()[:12])
os.chmod(f2, stat.S_IWRITE)

d3 = tempfile.mkdtemp(prefix="pm_vf_v3b_")
os.makedirs(os.path.join(d3, "agent"))
f3 = os.path.join(d3, "agent", "c.py")
io.open(f3, "w").write("OLD")
zp3, tree3 = mk_pkg(d3, body="NEW")
man3 = {"base": {"version": "9999.9.9", "sha256": tree3, "url": ""}}
_real_copy = U.shutil.copy2


def _flaky(src, dst, *a, **k):
    if os.path.abspath(str(dst)) == os.path.abspath(f3):
        e = PermissionError(13, "in use by another process")
        try:
            e.winerror = 32
        except Exception:
            pass
        raise e
    return _real_copy(src, dst, *a, **k)


U.shutil.copy2 = _flaky
try:
    rc3, _m3, det3 = U.apply_full(man3, zp3, d3)
finally:
    U.shutil.copy2 = _real_copy
st3 = U.read_local_state(d3)
ok("被占用 ⇒ rc=0 + status=partial + pendingFiles 记下那一件",
   rc3 == 0 and det3.get("status") == "partial" and det3.get("pending") == ["agent/c.py"],
   "rc=%s status=%s pending=%s" % (rc3, det3.get("status"), det3.get("pending")))
ok("…installed.json 的**版本不许被推上去**（否则重跑会被「已是最新」吞掉）",
   str(st3.get("version") or "") == "", str(st3.get("version")))
_rc4, _m4, _d4 = U.apply_full(man3, zp3, d3)
ok("解锁后重跑 ⇒ 真的补换（V3 的核心回归）", io.open(f3).read() == "NEW",
   "文件内容=%s" % io.open(f3).read()[:12])

print("── V4 干跑只说不做 ──")
d5 = tempfile.mkdtemp(prefix="pm_vf_v4_")
zp5, tree5 = mk_pkg(d5, rel="agent/x.py", body="x=1")
hits5 = []
U._relaunch_after_update = lambda v="": hits5.append(v)
try:
    r5 = U.run_once(manifest={"base": {"version": "9999.9.9", "sha256": tree5, "url": ""}},
                    zip_path=zp5, target=d5, dry=True)
finally:
    U._relaunch_after_update = _real_relaunch
ok("干跑不调用交接（不许杀正在跑的机器人）", len(hits5) == 0, "交接被调用 %d 次" % len(hits5))
ok("干跑 needRestart=False、detail 带 dry", r5.get("needRestart") is False and r5["detail"].get("dry"),
   str(r5.get("needRestart")))

print("── V5 慢源的耐心阶段要真的等 ──")
_real_fetch = uc.fetch
# ⚠️ 打桩必须返回 **2 元组** `(man, why)`（写成一个 dict 会让线程里 unpack 失败 ⇒ 判据假红，
#    本次就是先踩了这个；见 lesson「判据的桩要跟被桩函数的返回值形状一致」）。
uc.fetch = lambda u, t=8.0: (time.sleep(5.0), ({"base": {"version": "1.0.0"}}, ""))[1]
try:
    t0 = time.time()
    man5, why5, used5 = uc.fetch_any(["http://a.invalid/1", "http://b.invalid/2"], 2.0, patient=10.0)
    el = time.time() - t0
finally:
    uc.fetch = _real_fetch
ok("源 5 秒才答 ⇒ 必须拿到清单（老代码 t≈3s 就放弃）",
   man5 is not None, "man=%s why=%s" % (bool(man5), str(why5)[:50]))
ok("…且确实等了 ≥4.5 秒（不是 50 毫秒）", el >= 4.5, "耗时=%.1fs" % el)

print("── V8 凭据脱敏：webhook_token 也要掩 + 掩码值不许覆盖真值 ──")
_real_cfg = W.get_config
W.get_config = lambda: {"api": {"api_key": "FAKE-KEY-0000000000"},
                        "feedback": {"smtp": {"password": "FAKE-AUTHCODE"},
                                     "webhook_token": "FAKE-PUSHPLUS-TOKEN"},
                        "cloud": {"token": "FAKE-CLOUD-TOKEN"}}
try:
    c = W.WebUI.__new__(W.WebUI).masked_config()
    ok("api.api_key 掩了", c["api"]["api_key"] != "FAKE-KEY-0000000000", c["api"]["api_key"])
    ok("feedback.smtp.password 掩了", c["feedback"]["smtp"]["password"] != "FAKE-AUTHCODE",
       c["feedback"]["smtp"]["password"])
    ok("**feedback.webhook_token 掩了**（V8 本体）",
       c["feedback"].get("webhook_token") != "FAKE-PUSHPLUS-TOKEN", str(c["feedback"].get("webhook_token")))
    ok("cloud.token 掩了", c["cloud"]["token"] != "FAKE-CLOUD-TOKEN", c["cloud"]["token"])
    _new = {"feedback": {"webhook_token": c["feedback"]["webhook_token"]}}
    W._protect_secrets(_new)
    ok("恢复侧：掩码值不许覆盖真值（两侧同源）",
       _new["feedback"]["webhook_token"] == "FAKE-PUSHPLUS-TOKEN",
       str(_new["feedback"]["webhook_token"]))
finally:
    W.get_config = _real_cfg

print("\n==== 漏洞修复回归判据（V1~V5 / V8）：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
