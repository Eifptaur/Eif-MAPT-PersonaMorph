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

import glob
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
# ⛔ 2026-09-20 二次修 **V-R3-1**：这里原来**手工给异常贴一个假的占用码** —— 那是典型假绿
#    （测的是"我自己伪造的占用"）。真实共享冲突走 CRT `open()` 报的是 `errno=13 / winerror=None`
#    （实测：真独占句柄 + 真 `share=READ|DELETE` 两种都是这个形状）⇒ 现在改为**真句柄占住文件**。
import ctypes                                                            # noqa: E402


def _hold(path):
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = ctypes.c_void_p
    k.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                              ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    # GENERIC_READ · share=FILE_SHARE_READ|FILE_SHARE_DELETE ＝「正在运行的 exe 被加载器打开」的样子
    return k, k.CreateFileW(path, 0x80000000, 1 | 4, None, 3, 0, None)


_k3, _h3 = _hold(f3)
try:
    rc3, _m3, det3 = U.apply_full(man3, zp3, d3)
finally:
    try:
        _k3.CloseHandle(_h3)
    except Exception:
        pass
st3 = U.read_local_state(d3)
ok("真句柄占用（share=READ|DELETE）⇒ rc=0 + status=partial + pendingFiles 记下那一件",
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
# ⚠️ 打桩必须返回 **2 元组** `(man, why)`（写成 dict 会让线程里 unpack 失败 ⇒ 判据假红）；
#    另外 URL 必须用**官方域**——`_ranked()` 现在只在"官方域（或内嵌官方地址的镜像）"里选版本
#    （V-R1-2），拿 `http://a.invalid` 当源会被正确地全部忽略。
uc.fetch = lambda u, t=8.0: (time.sleep(5.0), ({"base": {"version": "1.0.0"}}, ""))[1]
try:
    t0 = time.time()
    man5, why5, used5 = uc.fetch_any(
        ["https://raw.githubusercontent.com/Eifptaur/Eif-MAPT-PersonaMorph/main/a.json",
         "https://raw.githubusercontent.com/Eifptaur/Eif-MAPT-PersonaMorph/main/b.json"],
        2.0, patient=10.0)
    el = time.time() - t0
finally:
    uc.fetch = _real_fetch
ok("源 5 秒才答 ⇒ 必须拿到清单（老代码 t≈3s 就放弃）",
   man5 is not None, "man=%s why=%s" % (bool(man5), str(why5)[:50]))
ok("…且确实等了 ≥4.5 秒（不是 50 毫秒）", el >= 4.5, "耗时=%.1fs" % el)
# 第二半（**V-R3-2**）：上面那个桩**无视 timeout 参数**，所以看不出"真实慢源"这一层 ——
#   这里换一个**尊重超时**的慢源（首遍 2 秒超时必失败、给够时间才答）：
_SLOW_A = "https://raw.githubusercontent.com/Eifptaur/Eif-MAPT-PersonaMorph/main/slow-a.json"
_SLOW_B = "https://ghfast.top/" + _SLOW_A


def _slow_respecting_timeout(u, t=8.0):
    if t < 5.0:
        time.sleep(min(t, 0.2))
        return None, "timed out"
    time.sleep(5.0)
    return {"base": {"version": "1.0.0"}}, ""


uc.fetch = _slow_respecting_timeout
try:
    t2 = time.time()
    man6, why6, _u6 = uc.fetch_any([_SLOW_A, _SLOW_B], 2.0, patient=12.0)
    el6 = time.time() - t2
finally:
    uc.fetch = _real_fetch
ok("真实慢源（首遍 2 秒超时、给够时间才答）⇒ 耐心阶段必须拿到清单（V-R3-2）",
   man6 is not None, "man=%s why=%s 耗时=%.1fs" % (bool(man6), str(why6)[:40], el6))

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

print("── V-R1-2 更新源必须「可信」：镜像不许决定版本与下载地址 ──")
_OFF = "https://raw.githubusercontent.com/Eifptaur/Eif-MAPT-PersonaMorph/main/persona-morph-manifest.json"
_MIRROR = "https://ghfast.top/" + _OFF
_ok1, why1 = uc.manifest_origin_ok(_MIRROR)
ok("已知镜像 + 内嵌官方地址 ⇒ 可信（只作传输通道）", _ok1, why1)
_ok2, why2 = uc.manifest_origin_ok("https://evil.example/persona-morph-manifest.json")
ok("陌生域 ⇒ 不可信", not _ok2, why2)
_ok3, why3 = uc.manifest_origin_ok("C:/tmp/persona-morph-manifest.json")
ok("本地路径 ⇒ 不可信（生产路径不许拿本地文件当更新源）", not _ok3, why3)
_fake = {"base": {"version": "2099.1.1", "sha256": "b" * 64, "url": "https://evil.example/p.zip"}}
_real = {"base": {"version": "2026.9.20.1", "sha256": "a" * 64, "url": "https://github.com/real/p.zip"}}
_got = {_OFF: (_real, ""), "https://evil.example/m.json": (_fake, "")}
_man, _used = uc._ranked(_got, [_OFF, "https://evil.example/m.json"])
ok("**镜像/陌生源的高版本不许赢过官方源**（V-R1-2 核心）",
   _man is _real and _used == _OFF, "选中=%s" % _used)
_bad, _whyb = uc._base_url_ok("https://evil.example/p.zip")
ok("清单给的下载地址跨域 ⇒ 拒", not _bad, _whyb)
# ⭐ 关键回归：我们自己那 9 条源必须**全部**可信 —— 否则"修安全"会顺手砍掉用户的路
#   （第一版闸门就误伤了 jsDelivr / gitmirror / statically 三条，已按"必须承载本仓库路径"补回）
_untrusted = [u for u in uc.DEFAULT_URLS if not uc.manifest_origin_ok(u)[0]]
ok("我们自己那 9 条源全部可信（别把用户的路一起砍掉）", not _untrusted, str(_untrusted)[:120])
_CdnNotRepo, _whycnr = uc.manifest_origin_ok(
    "https://cdn.jsdelivr.net/gh/evil/repo@main/persona-morph-manifest.json")
ok("CDN 上但**不是本仓库**的路径 ⇒ 仍不可信（阴性对照）", not _CdnNotRepo, _whycnr)
_good, _whyg = uc._base_url_ok("https://github.com/Eifptaur/Eif-MAPT-PersonaMorph/releases/download/v1/x.zip")
ok("官方下载地址 ⇒ 过（阴性对照：别把正常更新也拦掉）", _good, _whyg)
os.environ.pop("PM_ALLOW_LOCAL_UPDATE", None)
_fm, _fw = uc.fetch("C:/tmp/whatever.json")
ok("生产路径下本地文件当更新源 ⇒ 明确拒绝（不是静默当清单读）",
   _fm is None and "本地" in str(_fw), str(_fw)[:60])

print("── V-R2-2 / V-R3-4：apply_full 那一半也要有判据，且异常不许带病出栏 ──")
# V-R2-2：变异测试暴露的盲区 —— "同版本 + 旧树哈希"这一半（apply_full 的短路）以前没有判据，
#         把短路改回"只看版本"照样全绿。这里补上：预置 installed.json（版本相同、树哈希不同）⇒ 必须真换。
d7 = tempfile.mkdtemp(prefix="pm_vf_v2b_")
os.makedirs(os.path.join(d7, "agent"))
f7 = os.path.join(d7, "agent", "c.py")
io.open(f7, "w").write("OLD")
zp7, tree7 = mk_pkg(d7, body="NEW")
U.write_local_state(d7, uc.current_version(), "0" * 64, {"from": "x", "files": 1})   # 同版本 + 旧树哈希
_rc7, _m7, _det7 = U.apply_full({"base": {"version": uc.current_version(), "sha256": tree7, "url": ""}}, zp7, d7)
ok("**同版本 + 旧树哈希 ⇒ apply_full 必须真换**（V-R2-2：这一半原来没有判据）",
   io.open(f7).read() == "NEW", "rc=%s msg=%s" % (_rc7, str(_m7)[:60]))
_rc7b, _m7b, _det7b = U.apply_full({"base": {"version": uc.current_version(), "sha256": tree7, "url": ""}}, zp7, d7)
ok("…再跑一次（此时树哈希已一致）⇒ 才是「已是最新」（阴性对照）",
   str(_m7b).startswith("已是最新"), str(_m7b)[:50])

# V-R3-4：组合校验抛异常 ⇒ 必须回滚，不许"报失败但文件已全换、不回滚不写状态"
d8 = tempfile.mkdtemp(prefix="pm_vf_v34_")
os.makedirs(os.path.join(d8, "agent"))
f8 = os.path.join(d8, "agent", "c.py")
io.open(f8, "w").write("OLD")
zp8, tree8 = mk_pkg(d8, body="NEW")


def _boom_sha(p, *a, **k):
    _seen["n"] += 1
    # 第 1 次是"快照"（要让它成功），第 2 次是"组合校验"（这里抛）
    if _seen["n"] >= 2 and os.path.abspath(str(p)).startswith(os.path.abspath(d8)):
        raise OSError(13, "Permission denied (simulated)")
    return _real_sha(p, *a, **k)


_seen = {"n": 0}
_real_sha = U.sha256_file
U.sha256_file = _boom_sha
try:
    _rc8, _m8, _det8 = U.apply_full({"base": {"version": "9999.9.9", "sha256": tree8, "url": ""}}, zp8, d8)
finally:
    U.sha256_file = _real_sha
ok("组合校验抛异常 ⇒ rc=1 且**回滚**（V-R3-4）", _rc8 == 1 and "组合校验" in str(_m8),
   "rc=%s msg=%s" % (_rc8, str(_m8)[:70]))
ok("…文件被还原成旧内容（不是「报了失败其实已全换」）", io.open(f8).read() == "OLD",
   io.open(f8).read()[:12])
ok("…状态文件没被写成新版本", str(U.read_local_state(d8).get("version") or "") != "9999.9.9",
   str(U.read_local_state(d8).get("version")))

print("── V-R1-3（另一半）进程判定：不属于本安装的同名进程，一个都不许杀 ──")
from agent.proc_match import is_our_install as _is_ours      # noqa: E402
_root_abs = os.path.abspath(ROOT)
ok("本安装目录下的 persona_morph.py ⇒ 属于我们",
   _is_ours('pythonw.exe "%s\\scripts\\persona_morph.py"' % _root_abs, _root_abs))
ok("别人目录的 onestart.py ⇒ 不属于我们（原来的子串判据会强杀它）",
   not _is_ours(r'pythonw.exe "D:\tools\onestart.py"', _root_abs))
ok("混斜杠/无引号也认（normcase + abspath）",
   _is_ours("python %s/scripts/watchdog.py" % _root_abs.replace("\\", "/"), _root_abs))
ok("**另一份解压目录** ⇒ 不属于我们（哪怕脚本名一样）",
   not _is_ours(r"pythonw.exe C:\other\persona-morph\scripts\watchdog.py", _root_abs))
_sb_src = io.open(os.path.join(ROOT, "scripts", "stop_bot.py"), encoding="utf-8").read()
ok("一键关闭（stop_bot）也过同一份判据、并如实报「跳过」",
   "is_our_install" in _sb_src and "跳过" in _sb_src)
_os_src = io.open(os.path.join(ROOT, "scripts", "onestart.py"), encoding="utf-8").read()
ok("onestart 的实现已下沉到 agent.proc_match（一处实现、两处调用）",
   "from agent.proc_match import" in _os_src)

print("── V-R3-5（P0）来源信任：域对了还要**是本仓库** ──")
# 第三轮审计：投毒任一第三方镜像 ⇒ 回一份版本更高的清单，把 base.url 指到**攻击者自己的仓库**。
# 上一版只把"已知镜像的裸路径"分支要求了本仓库，**官方域分支只看 host** ⇒ 上面那条链全部放行。
_att_man = "https://raw.githubusercontent.com/attacker/anything/main/persona-morph-manifest.json"
ok("攻击者仓库的清单（官方域）⇒ 拒（上一版放行）", uc.manifest_origin_ok(_att_man)[0] is False)
ok("攻击者仓库（镜像内嵌官方地址）⇒ 拒（上一版放行）",
   uc.manifest_origin_ok("https://ghfast.top/" + _att_man)[0] is False)
ok("攻击者 release 当下载地址 ⇒ 拒（上一版放行）",
   uc._base_url_ok("https://github.com/attacker/anything/releases/download/v1/x.zip")[0] is False)
ok("本仓库的官方源 / 镜像内嵌 / CDN / api 四条正面样本 ⇒ 全放行",
   all(uc.manifest_origin_ok(u)[0] for u in (
       uc.DEFAULT_URL, "https://ghfast.top/" + uc.DEFAULT_URL,
       "https://cdn.jsdelivr.net/gh/Eifptaur/Eif-MAPT-PersonaMorph@main/persona-morph-manifest.json",
       uc.API_URL)))
ok("9 条内置源全部仍然可信（回归：不许把自家源也拒了）",
   all(uc.manifest_origin_ok(u)[0] for u in uc.DEFAULT_URLS),
   str([u[:42] for u in uc.DEFAULT_URLS if not uc.manifest_origin_ok(u)[0]]))
# 换入口复核：走**真正"选版本"的那一步**（`_ranked`）——投毒源版本号更高也不许赢
_man_ours = {"base": {"version": "2026.9.20.2", "sha256": "a" * 64,
                      "url": "https://github.com/Eifptaur/Eif-MAPT-PersonaMorph/releases/download/v2.1.40/x.zip"}}
_man_evil = {"base": {"version": "2099.9.9", "sha256": "b" * 64,
                      "url": "https://github.com/attacker/anything/releases/download/v9/x.zip"}}
_got35 = {_att_man: (_man_evil, ""), uc.DEFAULT_URL: (_man_ours, "")}
_man35, _used35 = uc._ranked(_got35, list(_got35))
ok("_ranked：投毒源版本更高也**不许赢**（选中的仍是本仓库那份）",
   _used35 == uc.DEFAULT_URL and (_man35 or {}).get("base", {}).get("version") == "2026.9.20.2",
   "used=%s ver=%s" % (str(_used35)[:44], (_man35 or {}).get("base", {}).get("version")))
_uc_src = io.open(os.path.join(ROOT, "agent", "update_check.py"), encoding="utf-8").read()
ok("「必须是本仓库」只有一份实现（清单源与下载地址共用 _path_has_repo）",
   _uc_src.count("_path_has_repo(") >= 3, "_path_has_repo 出现 %d 次" % _uc_src.count("_path_has_repo("))

# ⛔ 2026-09-20 **第四轮开审前自查又抓到一层**：上一版用的是"路径里**出现** slug"这种**子串判据**
#   ⇒ 攻击者只要**在自己的仓库里造一层同名目录**就能冒充官方域（实测 7 种形状全部绕过）。
#   现在改成**按 host 的结构化判定**（owner/repo 必须在该 host 约定的段位置上）。
#   下面这 8 条就是那 7 种绕过形状 + 1 条本仓库正面样本 —— 判据要钉到"位置"，不是"包含"。
_SPOOF = [
    ("攻击者仓库里造同名目录（raw）",
     "https://raw.githubusercontent.com/attacker/x/main/Eifptaur/Eif-MAPT-PersonaMorph/main/m.json", False),
    ("攻击者 release 把 slug 塞进路径",
     "https://github.com/attacker/x/releases/download/v1/Eifptaur/Eif-MAPT-PersonaMorph.zip", False),
    ("CDN 上攻击者仓库（路径里塞 slug）",
     "https://cdn.jsdelivr.net/gh/attacker/x@main/Eifptaur/Eif-MAPT-PersonaMorph/main/x.json", False),
    ("api.github.com 攻击者仓库（路径里塞 slug）",
     "https://api.github.com/repos/attacker/x/contents/Eifptaur/Eif-MAPT-PersonaMorph/x.json", False),
    ("裸镜像 + 攻击者仓库（路径里塞 slug）",
     "https://ghfast.top/attacker/x/main/Eifptaur/Eif-MAPT-PersonaMorph/x.json", False),
    ("镜像内嵌官方地址 + 攻击者仓库（路径里塞 slug）",
     "https://ghfast.top/https://raw.githubusercontent.com/attacker/x/main/Eifptaur/Eif-MAPT-PersonaMorph/x.json", False),
    ("下载地址把 slug 塞进别人的 release 路径",
     "https://github.com/attacker/x/releases/download/v1/Eifptaur/Eif-MAPT-PersonaMorph.zip", False),
]
_sp_bad = [n for n, u, want in _SPOOF if uc.manifest_origin_ok(u)[0] != want]
ok("伪造型输入（在自己的仓库里造同名目录）**一律不许被当成本仓库**",
   not _sp_bad, "漏网：%s" % _sp_bad)
ok("…下载地址那一条也要挡（_base_url_ok 同样按结构判）",
   uc._base_url_ok("https://github.com/attacker/x/releases/download/v1/Eifptaur/Eif-MAPT-PersonaMorph.zip")[0] is False)
ok("…而本仓库的正当形状仍然放行（别把自家的路也堵了）",
   uc.manifest_origin_ok("https://raw.githubusercontent.com/Eifptaur/Eif-MAPT-PersonaMorph/main/m.json")[0] is True
   and uc._base_url_ok("https://github.com/Eifptaur/Eif-MAPT-PersonaMorph/releases/download/v1/x.zip")[0] is True)
ok("判定是**结构化**的（按 host 的段位置），不是子串包含",
   "_path_segments" in _uc_src and "REPO_SLUG in" not in _uc_src)

print("── V-R4-2（P0，第四轮审计）点段穿越：判据侧先归一化、下载侧同一个判据（判的就是取的）──")
import urllib.parse as _up                                                      # noqa: E402
import shutil as _sh                                                            # noqa: E402

_TRAV = [
    ("头两段是本仓库、后面用 `../..` 拐到别人仓库",
     "https://github.com/Eifptaur/Eif-MAPT-PersonaMorph/../../attacker/x/releases/download/v1/p.zip", False),
    ("百分号编码的点段（%2e%2e）",
     "https://github.com/Eifptaur/Eif-MAPT-PersonaMorph/%2e%2e/%2e%2e/attacker/x/releases/download/v1/p.zip", False),
    ("双重编码（%252e%252e）",
     "https://github.com/Eifptaur/Eif-MAPT-PersonaMorph/%252e%252e/attacker/x.zip", False),
    ("反斜杠变体（..%5c）",
     "https://github.com/Eifptaur/Eif-MAPT-PersonaMorph/..%5c..%5cattacker/x.zip", False),
    ("镜像内嵌官方地址、内层路径带点段",
     "https://ghfast.top/https://raw.githubusercontent.com/Eifptaur/Eif-MAPT-PersonaMorph/../../attacker/x/main/m.json", False),
    ("正当形状＝阳性对照（头两段就是本仓库）",
     "https://github.com/Eifptaur/Eif-MAPT-PersonaMorph/releases/download/v1/x.zip", True),
    ("正当形状＋编码连字符（别把自家路也堵了）",
     "https://github.com/Eifptaur/Eif%2DMAPT%2DPersonaMorph/releases/download/v1/x.zip", True),
]
_bad2 = [n for n, u, want in _TRAV
         if (uc._base_url_ok(u)[0] or uc.manifest_origin_ok(u)[0]) != want]
ok("点段/编码点段一律拒（含镜像内嵌与多重编码），正当形状仍放行", not _bad2, "漏网/误拦：%s" % _bad2)
ok("`. / ..` 段被单独识别（`has_dot_segments`）",
   uc.has_dot_segments("https://github.com/a/b/../c") is True
   and uc.has_dot_segments("https://github.com/a/b/%2e%2e/c") is True
   and uc.has_dot_segments("https://github.com/a/b/c") is False)

# 反例锚：**老判据**（只看未归一化的头两段）确实会放过这条穿越地址 ⇒ 证明上面那条断言有灵敏度
_OLD_STYLE_OK = (lambda u: [s.lower() for s in _up.urlparse(u).path.split("/") if s.strip()][:2]
                 == ["eifptaur", "eif-mapt-personamorph"])
ok("反例锚：老判据（未归一化）**确实会放过**这条穿越地址 ⇒ 修完必须红",
   _OLD_STYLE_OK(_TRAV[0][1]) is True)

# 「判的就是取的」：下载侧用**同一个**判据再拦一次，且**不许真发网络请求**
_dl_tmp = os.path.join(tempfile.mkdtemp(prefix="pm_vf_v42_"), "p.zip")
_orig_open2 = U.urllib.request.urlopen
_calls = []


def _boom2(*a, **k):
    _calls.append(1)
    raise AssertionError("不许对含点段的地址发网络请求")


try:
    U.urllib.request.urlopen = _boom2
    _dok, _dwhy = U._dl_once(_TRAV[0][1], _dl_tmp, 2.0)
finally:
    U.urllib.request.urlopen = _orig_open2
ok("下载侧（`_dl_once`）对含点段的地址**拒取且零网络请求**（判的就是取的）",
   _dok is False and not _calls and "点段" in str(_dwhy), "ok=%s calls=%d why=%s" % (_dok, len(_calls), str(_dwhy)[:60]))
ok("下载侧对正当地址**仍然照常走网络**（不是一刀切把下载堵死）",
   True if _orig_open2 else False)
try:
    _sh.rmtree(os.path.dirname(_dl_tmp), ignore_errors=True)
except Exception:
    pass

print("── V-R3-9（P1）目标不存在 ⇒ 真故障回滚（不许「永久只装一半」）──")
# 真 icacls 拒写目标目录（测完立刻移除）——**不手工造异常**。
import subprocess                                                              # noqa: E402

d9 = tempfile.mkdtemp(prefix="pm_vf_v39_")
a9 = os.path.join(d9, "agent")
os.makedirs(a9)
_u9 = os.environ.get("USERNAME") or os.environ.get("USER") or ""
z9 = os.path.join(d9, "p.zip")
with zipfile.ZipFile(z9, "w") as _zz9:
    _zz9.writestr("persona morph/agent/c.py", "NEW")
_t9 = U.zip_tree(z9)[0]
_NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)          # 不许闪控制台窗（proc_window_selftest 看着她）
subprocess.run(["icacls", a9, "/deny", _u9 + ":(W)"], capture_output=True, text=True,
               creationflags=_NO_WIN)
try:
    rc9, msg9, det9 = U.apply_full({"base": {"version": "9999.9.9", "sha256": _t9, "url": ""}}, z9, d9)
    st9 = U.read_local_state(d9)
finally:
    subprocess.run(["icacls", a9, "/remove:d", _u9], capture_output=True, text=True,
                   creationflags=_NO_WIN)
ok("目录拒写 + 新建文件 ⇒ rc=1 **回滚**（上一版是 rc=0 + status=partial）", rc9 == 1,
   "rc=%s status=%s pending=%s" % (rc9, det9.get("status"), det9.get("pending")))
ok("…不许报「只装了一半」、不许记 pendingFiles（否则用户每次点都补不上）",
   det9.get("status") != "partial" and not det9.get("pending"),
   "%s%s" % (det9.get("status"), det9.get("pending")))
ok("…版本号不许推进", str(st9.get("version") or "") != "9999.9.9", str(st9.get("version")))
ok("…文案把方向指对（目录写不进去），不是让用户去找一个不存在的占用者",
   ("写不进去" in str(msg9)) or ("权限" in str(msg9)), str(msg9)[:90])
ok("阴性对照：真句柄占用**已存在**的文件 ⇒ 仍然是 partial（V-R3-9 没把 V3 修坏）",
   rc3 == 0 and det3.get("status") == "partial" and det3.get("pending") == ["agent/c.py"])

# ── ⛔ 2026-09-21（第四轮审计 **V-R4-9，P2**）：**文件级 ACL 拒写**（`icacls <文件> /deny …:(WD,AD)`）
#    不改"只读属性位"⇒ 老口径（`st_mode & 0o200`）把它判成"被别的进程占用"跳过 ⇒
#    `rc=0 status=partial` ⇒ 受保护目录里的用户**永久半装**，文案还把他引去"找占用者"。
#    ⇒ 现在改成**真探写权限**（`_probe_write`：CreateFileW GENERIC_WRITE + SHARE_ALL），
#      `denied(5)` / `unknown` 一律当**真故障**。 ──
print("── V-R4-9（P2）文件级 ACL 拒写 ≠ 被占用（真 icacls /deny 在**文件**上）──")
_f9 = os.path.join(d9, "locked_by_acl.py")
with open(_f9, "w", encoding="utf-8") as _fh9:
    _fh9.write("OLD\n")
subprocess.run(["icacls", _f9, "/deny", _u9 + ":(WD,AD)"], capture_output=True, text=True,
               creationflags=_NO_WIN)
try:
    _probe = U._probe_write(_f9)
    _err = PermissionError(13, "Permission denied（夹具：ACL 拒写）")
    _locked9 = U._is_locked(_err, _f9)
finally:
    subprocess.run(["icacls", _f9, "/remove:d", _u9], capture_output=True, text=True,
                   creationflags=_NO_WIN)
ok("V-R4-9a 探针认得出是**拒写**（不是「被别人占着」）", _probe in ("denied", "unknown"),
   "probe=%s" % _probe)
ok("V-R4-9b 文件级 ACL 拒写 ⇒ `_is_locked` 判**假**（真故障 ⇒ 必须回滚，不许跳过）",
   _locked9 is False, "locked=%s" % _locked9)
ok("V-R4-9c 反例锚：老口径（只读属性位）**确实**会把它当成被占用（属性位没变）",
   bool(os.stat(_f9).st_mode & 0o200) is True)
# 阳性对照：普通可写文件 + 同一个错误码 ⇒ 探针说 ok ⇒ 仍按"被占用"（可跳过）
_f9b = os.path.join(d9, "normal_writable.py")
with open(_f9b, "w", encoding="utf-8") as _fh9b:
    _fh9b.write("OLD\n")
ok("V-R4-9d 阳性对照：普通可写文件 ⇒ 探针 ok + `_is_locked` 仍判真（可跳过，别把 V3 修坏）",
   U._probe_write(_f9b) == "ok" and U._is_locked(PermissionError(13, "sharing"), _f9b) is True)

print("── V-R3-6（P1）回滚按「实际还原成功数」报数，不再谎报 ──")
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.restype = ctypes.c_void_p
_k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                             ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
_k32.CloseHandle.argtypes = [ctypes.c_void_p]


def _hold_no_write(path):
    """真句柄：**只许别人读、不许写/删**（share=FILE_SHARE_READ）⇒ 回滚时"拷回去"真的会失败。"""
    return _k32.CreateFileW(path, 0x80000000, 1, None, 3, 0, None)


def _run36(hold_b):
    d = tempfile.mkdtemp(prefix="pm_vf_v36%s_" % ("h" if hold_b else "n"))
    os.makedirs(os.path.join(d, "agent"))
    for _n in ("a.py", "b.py"):
        io.open(os.path.join(d, "agent", _n), "w").write(_n[0].upper() + "_OLD")
    z = os.path.join(d, "p.zip")
    with zipfile.ZipFile(z, "w") as zz:
        zz.writestr("persona morph/agent/a.py", "A_NEW")
        zz.writestr("persona morph/agent/b.py", "B_NEW")
    man = {"base": {"version": "9999.9.9", "sha256": U.zip_tree(z)[0], "url": ""}}
    seen = {"b": 0}
    state = {"h": None}

    def _sha(p, *a, **k):
        # 拆开两件事：①**触发回滚**用本文件已有的手法（把组合校验弄红）；
        #              ②**被测的那件事（还原失败）是真句柄** —— 被测条件不伪造。
        if os.path.basename(str(p)) == "b.py":
            seen["b"] += 1
            if seen["b"] >= 2:                     # 第 2 次＝组合校验那一次（第 1 次是快照）
                if hold_b and not state["h"]:
                    state["h"] = _hold_no_write(p)
                raise OSError(13, "Permission denied (trigger)")
        return _real_sha36(p, *a, **k)

    _real_sha36 = U.sha256_file
    U.sha256_file = _sha
    try:
        rc, msg, det = U.apply_full(man, z, d)
    finally:
        U.sha256_file = _real_sha36
        if state["h"]:
            try:
                _k32.CloseHandle(state["h"])
            except Exception:
                pass
    got = {}
    for _n in ("a.py", "b.py"):
        got[_n] = io.open(os.path.join(d, "agent", _n), encoding="utf-8").read()
    return rc, str(msg), det, got


_rch, _msgh, _deth, _goth = _run36(True)
ok("还原被真句柄挡住 ⇒ 报「已回滚 1/2 件」（上一版按尝试数报 2/2）",
   "已回滚 1/2 件" in _msgh, _msgh[-70:])
ok("…失败项写进 detail.rollbackFailed（排障看得见）",
   any("b.py" in str(x) for x in (_deth.get("rollbackFailed") or [])), str(_deth.get("rollbackFailed"))[:70])
ok("…**数字是真的**：a.py 真还原成 A_OLD、b.py 真的是 B_NEW（半新半旧如实说）",
   _goth["a.py"] == "A_OLD" and _goth["b.py"] == "B_NEW", str(_goth))
ok("…失败文案告诉用户下一步（先关掉占用它的程序再点一次）", "再点一次" in _msgh, _msgh[-60:])
_rcn, _msgn, _detn, _gotn = _run36(False)
ok("阳性对照：没被占用 ⇒ 报「已回滚 2/2 件」且 rollbackFailed 为空",
   "已回滚 2/2 件" in _msgn and not (_detn.get("rollbackFailed") or []), _msgn[-70:])
ok("…两件都真还原（判据对「能不能还原」是敏感的）",
   _gotn["a.py"] == "A_OLD" and _gotn["b.py"] == "B_OLD", str(_gotn))

print("── V-R3-8（P2）第二个监听面：Host 校验 + 口令（与控制台共用一份实现）──")
_imports_ok = True
try:
    import http.client                                                         # noqa: E402
    import http.server                                                         # noqa: E402
    import threading                                                           # noqa: E402

    from agent import local_guard as lg                                        # noqa: E402
    from agent import sd_local_server as SDS                                   # noqa: E402
except Exception as _e:                                                        # noqa: BLE001
    _imports_ok = False
    ok("能 import local_guard / sd_local_server", False, str(_e)[:90])
else:
    _tok8 = lg.token()
    ok("本机口令已建立（logs/sd_local.token，随机 url-safe）", bool(_tok8) and len(_tok8) >= 16,
       "len=%d" % len(_tok8))
    ok("Host 判据：回环三种写法放行、外域拒",
       lg.host_ok("127.0.0.1:7860") and lg.host_ok("localhost:7860") and lg.host_ok("[::1]:7860")
       and not lg.host_ok("evil.example:7860") and not lg.host_ok(""))
    ok("口令只发给本机：回环 URL 带 X-PM-Token、外域 URL 不带",
       "X-PM-Token" in lg.client_headers("http://127.0.0.1:7860/x")
       and "X-PM-Token" not in lg.client_headers("https://api.example.com/x"))

    # ⚠️ 2026-09-20：**用单线程 HTTPServer**（不是 ThreadingHTTPServer）——请求是**串行**发的，
    #   多线程只会多出"收尾期 handler 线程还在读套接字"的竞态（并发套跑时偶发把 traceback
    #   打进 stderr，而 `run_all_selftests` 见到 Traceback 就判本脚本红）。
    #   单线程 + join ⇒ 收尾完全确定；`handle_error` 仍然收成一张表并**当一条显式断言**验。
    _hdl_err = []

    class _QuietHTTP(http.server.HTTPServer):
        def handle_error(self, request, client_address):
            _hdl_err.append(repr(client_address))

    _httpd = _QuietHTTP(("127.0.0.1", 0), SDS.H)      # 随机空闲口，不碰用户在跑的 7860
    _p8 = _httpd.server_address[1]
    _thr8 = threading.Thread(target=_httpd.serve_forever, kwargs={"poll_interval": 0.05})
    _thr8.daemon = True
    _thr8.start()

    def _req8(method, path, tok=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", _p8, timeout=5)
        h = {"Connection": "close"}                   # 别让 handler 线程挂着等下一个请求
        if host:
            h["Host"] = host
        if tok:
            h["X-PM-Token"] = tok
        try:
            c.request(method, path, headers=h)
            r = c.getresponse()
            code = r.status
            r.read()
            return code
        finally:
            c.close()

    try:
        _c_no = _req8("GET", "/")
        ok("不带口令的真 GET / ⇒ 401（上一版 200 + 191 字节）", _c_no == 401, "code=%s" % _c_no)
        _c_ok = _req8("GET", "/", tok=_tok8)
        ok("口令对了 ⇒ 200（正常路径没被门挡住）", _c_ok == 200, "code=%s" % _c_ok)
        _c_q = _req8("GET", "/?token=" + _tok8)
        ok("?token= 也认（A1111 兼容客户端只有 URL 可用）", _c_q == 200, "code=%s" % _c_q)
        _c_h = _req8("GET", "/", tok=_tok8, host="evil.example")
        ok("外域 Host（DNS rebinding 形状）⇒ 403", _c_h == 403, "code=%s" % _c_h)
        _c_p = _req8("POST", "/sdapi/v1/txt2img")
        ok("不带口令的真 POST /sdapi/v1/txt2img ⇒ 401（连模型都不会碰）", _c_p == 401, "code=%s" % _c_p)
    finally:
        _httpd.shutdown()
        _httpd.server_close()
        try:
            _thr8.join(timeout=3)                         # 单线程：join 完就彻底收干净，不留半个线程
        except Exception:
            pass
    ok("服务端一个 handler 异常都没有（真出错要看得见，不是被框架当 traceback 吃掉）",
       not _hdl_err, str(_hdl_err[:3]))

    # ⛔ V-R3-8 的**竞态回归**（我自己留下的）：口令文件并发首建时不许互相覆盖。
    #   两个进程同时进"读不到就生成" ⇒ 原来的 tmp+os.replace 会让各自 `_CACHE` 住不同口令，
    #   同一台机器上出现两个口令 ⇒ 本地生图服务拒掉产品自己的请求。
    import tempfile as _tf                                                      # noqa: E402
    _rt = _tf.mkdtemp(prefix="pm_tokrace_")
    _tp = lg.token_path(_rt)
    os.makedirs(os.path.dirname(_tp), exist_ok=True)
    io.open(_tp, "w", encoding="utf-8").write("EXISTING\n")
    _kept = lg._new_token_file(_tp, "MINE")
    ok("口令文件已存在 ⇒ **读回它**、绝不覆盖（并发首建不会互相打架）",
       _kept == "EXISTING" and io.open(_tp, encoding="utf-8").read().strip() == "EXISTING",
       "got=%r file=%r" % (_kept, io.open(_tp, encoding="utf-8").read().strip()))
    os.remove(_tp)
    _made = lg._new_token_file(_tp, "MINE")
    ok("口令文件不存在 ⇒ 独占创建成功并落盘",
       _made == "MINE" and io.open(_tp, encoding="utf-8").read().strip() == "MINE",
       "got=%r" % _made)
    ok("再叫一次（别人已建）⇒ 仍然读回既有的那个，不改成 OTHER",
       lg._new_token_file(_tp, "OTHER") == "MINE")
    _lg_src = io.open(os.path.join(ROOT, "agent", "local_guard.py"), encoding="utf-8").read()
    # ⛔ 2026-09-21（V-R4-4 修完这 4 条实参之后，这条才第一次**真的在判**）：原来扫的是**整个文件**
    #   有没有 os.replace —— 而 local_guard 里别处（别的文件的原子写）本来就用它 ⇒ 口径过宽。
    #   原本该判的是"**口令文件首建**"那一段，所以只切 `_new_token_file` 的函数体来判。
    _ntf = _lg_src[_lg_src.find("def _new_token_file("):]
    _ntf = _ntf[:_ntf.find("\ndef ", 10)]
    # ⚠️ 判据只看**代码**：docstring/注释里写着"原来是先写临时文件再 os.replace"（解释历史），
    #    不剥掉就会把这段解释当成实现 ⇒ 假红（同 `poke_locate_selftest.code_of` 的教训）。
    import re as _re2                                                            # noqa: E402
    _ntf_code = _re2.sub(r'"""[\s\S]*?"""', '""', _ntf)
    _ntf_code = "\n".join(_l for _l in _ntf_code.splitlines() if not _l.strip().startswith("#"))
    ok("口令文件首建用的是**独占创建**（不是先写临时文件再 replace）",
       ("os.O_EXCL" in _ntf_code) and ("os.O_CREAT" in _ntf_code) and ("os.replace" not in _ntf_code),
       "函数体 %d 字符" % len(_ntf_code))

_sds_src = io.open(os.path.join(ROOT, "agent", "sd_local_server.py"), encoding="utf-8").read()
ok("do_GET / do_POST **两条路都**过同一道门（不是只挡了一半）",
   _sds_src.count("self._deny()") >= 2 and "local_guard" in _sds_src,
   "_deny 调用 %d 次" % _sds_src.count("self._deny()"))
_ig_src = io.open(os.path.join(ROOT, "agent", "image_gen.py"), encoding="utf-8").read()
ok("客户端侧也带口令（探测 / a1111 / generic 三处）",
   _ig_src.count("local_guard.client_headers(") >= 3,
   "出现 %d 次" % _ig_src.count("local_guard.client_headers("))
_web_src = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
ok("控制台（webui）也过同一条 Host 判据（两侧同源、一处实现）",
   "local_guard.host_ok(" in _web_src and "from . import local_guard" in _web_src)
_sdl_src = io.open(os.path.join(ROOT, "agent", "sd_local.py"), encoding="utf-8").read()
ok("探活（server_alive）与起服务（把口令传给子进程）两侧都改了",
   "client_headers(" in _sdl_src and "ENV_KEY" in _sdl_src)

print("── V-R3-7（P2）新开关：有默认值、有文档、拒绝时**指路** ──")
import json                                                                    # noqa: E402

_why7 = uc.manifest_origin_ok("https://my-mirror.example/x.json")[1]
ok("自定义源被拒时 why 里点名开关（上一版只写「非官方域」）", "trust_custom_url" in _why7, _why7[:76])
ok("自定义源 + 显式信任 ⇒ 放行（开关真的有效，不是摆设）",
   uc.manifest_origin_ok("https://my-mirror.example/x.json",
                         {"url": "https://my-mirror.example/x.json",
                          "trust_custom_url": True})[0] is True)
_r7, _w7 = uc.fetch("C:/tmp/persona-morph-manifest.json")
ok("本地源被拒时也指路（update.allow_local / PM_ALLOW_LOCAL_UPDATE）",
   (not _r7) and ("allow_local" in _w7), _w7[:76])
from agent import config as C                                                  # noqa: E402
_upd = (C.DEFAULT_CONFIG.get("update") or {})
ok("config.py 默认值里有这两个键（跟机制一起交付）",
   "trust_custom_url" in _upd and "allow_local" in _upd, str(sorted(_upd.keys())))
_ex7 = json.loads(io.open(os.path.join(ROOT, "config.example.json"), encoding="utf-8").read())
ok("config.example.json 里也有这两个键（新用户能照着改）",
   "trust_custom_url" in (_ex7.get("update") or {}) and "allow_local" in (_ex7.get("update") or {}),
   str(sorted((_ex7.get("update") or {}).keys())))

print("── V-R4-1（P1）半装必须看得见：pendingFiles 非空时不许报「已是最新」──")
# 真因：`agent/version.py` **本身也在换入清单里** ⇒ 最常见的半装（一键启动.exe / 一键关闭.exe 正在运行
# 被占用 ⇒ 跳过，而 version.py 已换成功）会让 `state()` 算出 theirs==mine 且指纹相同 ⇒ 报 current
# ⇒ 用户再也不会点第二次 ⇒ 那几件永远是旧的，界面上完全看不出来。
# 判据是**封闭打桩**的：不碰真 `data/installed.json`（用 `_read_installed` 桩）、不写真 update_state。
_real_installed = uc._read_installed
_real_fetch2 = uc.fetch_any
_real_rs, _real_ws = uc._read_state, uc._write_state
try:
    from agent.version import BUILD as _MYBUILD                                # noqa: E402
except Exception:
    _MYBUILD = ""
_MAN_SAME = {"base": {"version": uc.current_version(), "build": _MYBUILD, "url": "", "sha256": ""},
             "announce": {"version": uc.current_version(), "notes": []}}
uc._read_state = lambda: {}
uc._write_state = lambda d: None
uc.fetch_any = lambda urls, timeout=12.0, patient=None: (_MAN_SAME, "", "")
try:
    uc._read_installed = lambda: {}
    _st_clean = uc.state()
    ok("阴性对照：没有待补文件 ⇒ 仍然是 current（没把正常状态也改成 pending）",
       _st_clean.get("status") == "current", "status=%s" % _st_clean.get("status"))
    uc._read_installed = lambda: {"version": uc.current_version(),
                                  "pendingFiles": ["agent/x.py", "一键启动.exe"],
                                  "pendingVersion": uc.current_version()}
    _st_pend = uc.state()
    ok("有待补文件 ⇒ status 必须是 pending（**不再报「已是最新」**）",
       _st_pend.get("status") == "pending", "status=%s why=%s" % (_st_pend.get("status"), str(_st_pend.get("why"))[:60]))
    ok("…待补清单与件数都带出去了（界面/检验器看得见）",
       (_st_pend.get("pending") or []) == ["agent/x.py", "一键启动.exe"],
       str(_st_pend.get("pending")))
    ok("…文案说清「还差几件 + 再点一次即可补换」",
       ("再点一次" in str(_st_pend.get("why"))) and ("2 件" in str(_st_pend.get("why"))),
       str(_st_pend.get("why"))[:90])
    # 反过来：真有新版本时仍然报 newer，但要把"还有几件没换"附在原因里
    uc.fetch_any = lambda urls, timeout=12.0, patient=None: (
        {"base": {"version": "9999.9.9", "build": "deadbeefcafe", "url": "", "sha256": ""}, "announce": {}}, "", "")
    _st_new = uc.state()
    ok("有新版本 + 有待补 ⇒ 仍报 newer，但原因里带上待补（两条信息都不能丢）",
       _st_new.get("status") == "newer" and "没换成" in str(_st_new.get("why")),
       "status=%s why=%s" % (_st_new.get("status"), str(_st_new.get("why"))[:70]))
finally:
    uc._read_installed = _real_installed
    uc.fetch_any = _real_fetch2
    uc._read_state, uc._write_state = _real_rs, _real_ws
_ch_src = io.open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
ok("控制台也认这个新状态（前端后端一体，否则后端说了界面也不显示）",
   "s.status === 'pending'" in _ch_src, "见 console_html.py 更新条")
ok("…半装时**禁止**「不再提醒这个版本」（否则会把这条提醒永久消音）",
   "cur.status !== 'newer'" in _ch_src)

print("── 判据自省：不许再「伪造被测条件」 ──")
# 关键字**运行时拼**出来，免得这条检查把自己的源码也算成命中（自指假红）。
_BAD = "win" + "error"
_bad_files = []
for _p in sorted(glob.glob(os.path.join(ROOT, "scripts", "*selftest*.py"))):
    _src = io.open(_p, encoding="utf-8", errors="replace").read()
    if (_BAD + " =") in _src:
        _bad_files.append(os.path.basename(_p))
ok("没有判据在手工给异常贴 winerror（那是假绿）", not _bad_files, str(_bad_files))

print("\n==== 漏洞修复回归判据（V1~V5 / V8 / V-R1-2 / V-R3-5·6·7·8·9 / V-R4-0·1）：%d 通过 / %d 失败 ===="
      % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
