#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「没装微信就带他去装」判据（2026-09-13 用户要求）

用户原话：「Persona morph 加上一个功能：检测到用户没有微信时帮他安装，离线包就不需要了」
口径（刻意的边界）：**只做只读检测 + 带他去官网 + 一键重测，不做静默安装**（静默装要下安装包 + UAC，
且我们不该替用户动系统）。

判据（不需要微信、不起服务）：
  ① 三态映射：进程在跑 ⇒ running（action=none）；装了没跑 ⇒ installed_not_running（action=start）；
     都没有 ⇒ missing（action=install）
  ② 检测只读：三条来源（注册表卸载项 / 常见安装路径 / 开始菜单快捷方式）都在；
     函数体内**不许**出现 subprocess / os.system / ShellExecute / msiexec，也**不许写注册表**（winreg.SetValue*）
  ③ 文案：missing 分支必须给官网地址与"不替你静默安装 / 装好点重新检测"的说法
  ④ 接线：/api/status 带 wechat_install、有 /api/wechat/recheck、控制台有提示卡与两个按钮
  ⑤ 真机：本机 wechat_version_info() 的 state 落在三态里，且 running 与进程检测一致
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


SRC = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8", errors="replace").read()
i = SRC.find("def wechat_install_state")
seg = SRC[i:i + 6000] if i >= 0 else ""

print("── A. 函数与三态映射 ──")
ok("wechat_install_state 在位", i >= 0 and len(seg) > 1500, "片段 %d 字符" % len(seg))
from agent.wechat import wechat_install_state, wechat_version_info  # noqa: E402

r = wechat_install_state(proc_found=True, proc_path=r"M:\WX\Weixin\Weixin.exe")
ok("进程在跑 ⇒ running / action=none", r["state"] == "running" and r["action"] == "none" and r["installed"], str(r["state"]))
ok("字段齐（state/installed/path/sources/official_url/detail/action）",
   all(k in r for k in ("state", "installed", "path", "sources", "official_url", "detail", "action")), ",".join(sorted(r.keys())))
ok("官网地址是官方的", r["official_url"].startswith("https://weixin.qq.com"), r["official_url"])
ok("三态取值只可能是三种之一", r["state"] in ("missing", "installed_not_running", "running"))

print("── B. 检测只读（不许静默安装/不许写注册表）──")
for bad, label in [(r"subprocess", "subprocess"), (r"os\.system", "os.system"),
                   (r"ShellExecute", "ShellExecute"), (r"msiexec", "msiexec"),
                   (r"winreg\.SetValue", "写注册表")]:
    ok("函数体里没有 %s" % label, re.search(bad, seg) is None)
ok("三条只读来源都在（注册表 / 路径 / 开始菜单）",
   ("winreg" in seg) and ("os.path.isfile" in seg) and ("glob" in seg))

print("── C. 文案 ──")
ok("missing 文案提到官网下载与重新检测", ("官网下载" in seg) and ("重新检测" in seg))
ok("明确写了不替用户静默安装", "不替你静默安装" in seg or "不会替你静默安装" in seg)
ok("installed_not_running 文案说清不用重装", "不用重装" in seg)

print("── D. 接线（API + 控制台）──")
WEB = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8", errors="replace").read()
from agent import console_html as _ch  # noqa: E402
CH = _ch.HTML                          # 直接用"真正会被服务出去的页面字符串"，不是读源码文件
ok("/api/status 带 wechat_install", "wechat_install" in WEB)
ok("/api/wechat/recheck 端点在位", "/api/wechat/recheck" in WEB)
ok("控制台页面里有提示卡与两个按钮", all(k in CH for k in ("wxInstall", "wxOpenSite", "wxRecheck")), "HTML %d 字符" % len(CH))
ok("提示卡默认隐藏（只在没装/没跑时显示）", 'id="wxInstall" class="row" style="display:none"' in CH)
ok("控制台有重新检测的接线（调 recheck 端点）", "getJSON('/api/wechat/recheck')" in CH)
ok("打开官网按钮默认指向官网", "https://weixin.qq.com/" in CH)
ok("not-installed 文案含『不会替你静默安装』", "不会替你静默安装" in CH)

print("── E. 真机（本机当前状态）──")
info = wechat_version_info()
st = str(info.get("state") or "")
ok("wechat_version_info 带 state 字段", st in ("missing", "installed_not_running", "running"), st)
ok("进程检测与 state 一致（found=True ⇒ running）", (st == "running") == bool(info.get("found")), "found=%s state=%s" % (info.get("found"), st))
ok("老字段没被破坏（found/version/adapter/supported/detail 都在）",
   all(k in info for k in ("found", "version", "adapter", "supported", "detail")))

print("== [wechat-install] 判据：{} 通过 / {} 失败 ==".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
