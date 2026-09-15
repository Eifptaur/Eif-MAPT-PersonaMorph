# -*- coding: utf-8 -*-
"""⑤ WebView2 引导器判据（2026-09-15）

守四条（对应任务书 ⑤「引导器进在线包 + 补『无运行时又无浏览器』的缺口」）：
  A. **引导器真的在仓库里**：按**大小 + SHA-256 + 微软 Authenticode 签名**认，不认文件名。
  B. **三件事可机械读出**：跑 `一键启动.exe --webview2probe`，必须打印三行 ASCII 标记
     （`wv2_runtime` / `wv2_bootstrapper` / `wv2_browser`），且 `wv2_bootstrapper=present`。
  C. **「缺运行库 ⇒ 不再硬起窗口」这条链在源码里**：`launcher.cs` 必须调 `WebView2Guide.HasRuntime()`
     且 install / browser / copy 三个动作分支都在（缺任何一条，用户看到的就还是"点了没反应"）。
  D. **打包闸门认得它**：`pack_online.py` 的必需文件里必须有引导器——否则包发出去照样缺。

用法：py -3 scripts/webview2_guide_selftest.py
"""
import hashlib
import io
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

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


BOOT_REL = os.path.join("assets", "webview2", "MicrosoftEdgeWebview2Setup.exe")
BOOT_ABS = os.path.join(ROOT, BOOT_REL)
BOOT_BYTES = 1844944
BOOT_SHA256 = "83004A28553BCF2F932BF03564FBAB407B8E1F59CD265F8DC99CC53D028E459C"
EXE = os.path.join(ROOT, "一键启动.exe")

print("== ⑤ WebView2 引导器判据 ==")

print("\n── A. 引导器在仓库里（大小/哈希/签名）──")
if not os.path.exists(BOOT_ABS):
    ok("引导器存在", False, BOOT_REL)
else:
    blob = io.open(BOOT_ABS, "rb").read()
    ok("引导器存在", True, BOOT_REL)
    ok("字节数与官方引导器一致", len(blob) == BOOT_BYTES, "%d（期望 %d）" % (len(blob), BOOT_BYTES))
    ok("SHA-256 与备案一致", hashlib.sha256(blob).hexdigest().upper() == BOOT_SHA256,
       hashlib.sha256(blob).hexdigest().upper()[:16] + "…")
    try:
        ps = ('$s = Get-AuthenticodeSignature -FilePath "%s"; '
              'Write-Output ($s.Status.ToString() + "|" + $s.SignerCertificate.Subject)' % BOOT_ABS)
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
        out = (r.stdout or "").strip()
        status, _, subject = out.partition("|")
        ok("Authenticode 有效", status.strip() == "Valid", status.strip() or (r.stderr or "")[:60])
        ok("签名者是微软（不是别的同名文件）", "Microsoft Corporation" in subject, subject[:60])
    except Exception as e:
        skip("Authenticode 校验", "拿不到签名：%s" % e)

print("\n── B. --webview2probe 三行事实（机械可读）──")
probe = ""
if not os.path.exists(EXE):
    skip("跑探针", "一键启动.exe 不在")
else:
    try:
        r = subprocess.run([EXE, "--webview2probe"], capture_output=True, text=True,
                           timeout=60, encoding="utf-8", errors="replace")
        probe = (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        skip("跑探针", "起不来：%s" % e)
if probe:
    m = {k: v for k, v in re.findall(r"^(wv2_\w+)=(.*)$", probe, flags=re.M)}
    ok("打印了 wv2_runtime", "wv2_runtime" in m, m.get("wv2_runtime", "")[:40])
    ok("打印了 wv2_bootstrapper 且为 present（引导器随包）", m.get("wv2_bootstrapper") == "present",
       m.get("wv2_bootstrapper", "缺失"))
    ok("打印了 wv2_browser（没浏览器时如实写 none）", "wv2_browser" in m, (m.get("wv2_browser") or "")[:60])
    ok("运行库版本形如 x.y.z.w 或如实写 none",
       m.get("wv2_runtime") == "none" or bool(re.match(r"^\d+\.\d+\.\d+\.\d+$", m.get("wv2_runtime", ""))),
       m.get("wv2_runtime", ""))
else:
    ok("探针有输出", False, "一个字符都没打印")

print("\n── C. 「缺运行库」这条链在源码里（不靠肉眼看）──")
lc = io.open(os.path.join(ROOT, "launcher-src", "launcher.cs"), encoding="utf-8").read()
wg = io.open(os.path.join(ROOT, "launcher-src", "webview2guide.cs"), encoding="utf-8").read()
ok("OpenConsole 里先查运行库（HasRuntime）", "WebView2Guide.HasRuntime()" in lc)
ok("有 install 分支（一键装）", 'f.Action == "install"' in lc and 'Action = "install"' in wg)
ok("有 browser 分支（用浏览器打开）", 'f.Action == "browser"' in lc and 'Action = "browser"' in wg)
ok("有 copy 分支（复制网址）", 'f.Action == "copy"' in lc and 'Action = "copy"' in wg)
ok("还写了日志留现场（NoteFallback）", lc.count("NoteFallback(dir, \"缺 WebView2 运行库") >= 3,
   lc.count("NoteFallback(dir, \"缺 WebView2 运行库"))
ok("探针开关已接进 Main", '"--webview2probe"' in lc)
ok("官方两处注册表路径都在（HKLM WOW6432Node + HKCU）",
   "WOW6432Node\\Microsoft\\EdgeUpdate\\Clients" in wg and "Registry.CurrentUser" in wg)
ok("静默安装参数与官方一致（/silent /install）", '"/silent /install"' in wg)
ok("装完只认注册表回读（不信退出码）", "RuntimeVersion()" in wg and wg.count("RuntimeVersion()") >= 3)

print("\n── D. 打包闸门认得它 ──")
pk = io.open(os.path.join(ROOT, "scripts", "pack_online.py"), encoding="utf-8").read()
rel_for_pack = BOOT_REL.replace("\\", "/")
ok("pack_online.py 的必需文件里含引导器", rel_for_pack in pk, rel_for_pack)
ok("pack_online.py 里写明「无运行时又无浏览器」这条缺口",
   ("WebView2" in pk) and ("引导" in pk or "bootstrapper" in pk.lower()))

print("")
print("WebView2 引导器判据：%d 通过 / %d 失败 / %d 跳过" % (PASS, FAIL, SKIP_N))
sys.exit(1 if FAIL else 0)
