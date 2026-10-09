# -*- coding: utf-8 -*-
"""⑤ WebView2 引导器判据

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

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # ⛔ 先补路径再 import
EXE = __import__("agent.autostart", fromlist=["x"]).find_launcher_exe()

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
                           capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
                           timeout=60, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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


def _visible(src):
    """剥掉 `//` 注释与 `/* */` 块注释，只留真正会显示给用户的字面量。

    ⛔ 判「文案里有没有某个词」必须扫**可见文本**：本文件头部的注释里就写着
       「不许出现『怎么办：点……』」——直接扫原文会被我们自己的注释误伤（判据假失败）。
    """
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


wgv = _visible(wg)
# 守一刀：剥注释必须真的剥掉东西，否则剥失败会让下面所有"不许出现 X"的断言假通过。
ok("剥注释真的生效（不是空串，且确实少了一批字符）", 0 < len(wgv) < len(wg), "%d -> %d" % (len(wg), len(wgv)))
ok("OpenConsole 里先查运行库（HasRuntime）", "WebView2Guide.HasRuntime()" in lc)
# ⛔ **锚意图、不锚旧文案**：本项守的是"缺运行库 ⇒ 有一个窗口让用户能把它装上"。
#   2026-10-09 用户把交互改成**三段式**（忽略/下载 → 后台下载/等待 → 双进度条 → 完成），
#   旧的四按钮 `一键安装并打开/用浏览器打开/复制网址/知道了` 已作废 ⇒ 断言跟着改成三段式。
#   ① 初始两按钮：「忽略」「下载」
ok("① 初始给「忽略」", 'MakeButton("忽略")' in wg)
ok("① 初始给「下载」", 'MakeButton("下载")' in wg)
# ② 点下载 ⇒ 两按钮：「后台下载」「等待」
ok("② 给「后台下载」", 'MakeButton("后台下载")' in wg)
ok("② 给「等待」", 'MakeButton("等待")' in wg)
# ③ 双进度条（下载 + 安装）
ok("③ 有下载进度条 barDl", "barDl" in wg and "RoundBar" in wg)
ok("③ 有安装进度条 barIn", "barIn" in wg)
ok("③ 装完说「安装完成，您可以正常使用」", "安装完成，您可以正常使用" in wg)
# ⛔ 不许教用户做事（旧版是"怎么办：点……"那套教程式文案，用户点过）
#   扫的是**剥掉注释后的可见文本**：注释里写"不许出现怎么办：点"是给我们自己看的，不算。
ok("★ 没有「怎么办：点…」这类教程式文案", "怎么办：" not in wgv)
# 动作分支与 `launcher.cs` 的消费端必须对得上（名字不一致 ⇒ 用户点完什么都不会发生）
ok("launcher.cs 认 `install-now`（当场装好）", 'f.Action == "install-now"' in lc)
ok("launcher.cs 认 `download-bg`（后台静默装）", 'f.Action == "download-bg"' in lc)
ok("引导器里真的会赋这两个值",
   '"install-now"' in wg and '"download-bg"' in wg)
# ⛔ **锚意图、不锚文案字面**：这里要守的是"缺运行库这条链的**每种结局都写日志留现场**"，
#    不是"第一参数必须叫 `dir`"。第 162 项把日志落点从代码根改成**安装根**
#    （`logs\` 永远在安装根）⇒ 断言改成"不限定第一参数"的计数。
_NF_MISSING_RUNTIME = re.findall(r"NoteFallback\([^,]+,\s*\"缺 WebView2 运行库", lc)
ok("还写了日志留现场（NoteFallback）", len(_NF_MISSING_RUNTIME) >= 3,
   "命中 %d 处" % len(_NF_MISSING_RUNTIME))
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
