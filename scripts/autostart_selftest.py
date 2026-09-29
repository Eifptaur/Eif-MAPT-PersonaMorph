# -*- coding: utf-8 -*-
"""开机自启判据（`agent/autostart.py` + 前后端接线）。

**为什么必须有**：自启是"改用户系统状态"的动作 —— 一旦悄悄写错地方（注册表/计划任务/系统目录），
后果是用户开机就跑、还找不到怎么关。所以这里用**临时目录注入**验行为，
并用**反向锚**钉住"只动当前用户启动文件夹"这条边界。

跑法：`runtime\\python\\python.exe scripts\\autostart_selftest.py`
"""
import importlib
import io
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
import _srcmatch as _sm # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


def src(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


def code_only(text: str) -> str:
    """去掉三引号字符串与 `#` 注释后的**纯代码**。

    为什么：反向锚要扫"有没有真的碰注册表"，而模块的**文档里**偏偏写着
    "不写注册表（HKCU\\...\\Run）" —— 不剥注释就会把"声明不做"误判成"做了"（判据实测踩过）。
    """
    import re as _re
    no_doc = _re.sub(r'"""(?:.|\n)*?"""', "", text)
    no_doc = _re.sub(r"'''(?:.|\n)*?'''", "", no_doc)
    return "\n".join(ln.split("#", 1)[0] for ln in no_doc.splitlines())


from agent import autostart as A # noqa: E402

_tmp = tempfile.mkdtemp(prefix="pm-auto-")
print("── A. 行为（全部在临时目录里做，绝不碰真实启动文件夹）──")
ok("A1 默认未开启（我们**不**替用户决定开机就跑）",
   A.status(_tmp).get("enabled") is False, A.status(_tmp).get("why"))
_ok, _why = A.enable(_tmp)
_st = A.status(_tmp)
ok("A2 开启后**以文件为准**报已开启，且产物落在启动文件夹里",
   _ok and _st.get("enabled") is True and str(_st.get("path", "")).startswith(_tmp),
   "%s · %s" % (_st.get("method"), os.path.basename(_st.get("path") or "")))
ok("A3 幂等：再开一次不报错、不重复造文件",
   A.enable(_tmp)[0] is True and len([f for f in os.listdir(_tmp)]) == 1,
   "目录内容：%s" % os.listdir(_tmp))
_ok2, _why2 = A.disable(_tmp)
ok("A4 关闭＝删掉那个文件；再关一次也如实说「本来就是关着的」",
   _ok2 and A.status(_tmp).get("enabled") is False and os.listdir(_tmp) == []
   and _sm.has(A.disable(_tmp)[1], "本来就是关着"), _why2)

print("\n── B. 退路：快捷方式建不出来时写 .cmd（两条硬要求）──")
_real_sc, _real_target = A._make_shortcut, A.target
try:
    A._make_shortcut = lambda *a, **k: (False, "判据：故意让它失败")
    A.target = lambda: ("C:/fake/py.exe|C:/fake/boot.py", _tmp, "判据桩")
    _ok3, _why3 = A.enable(_tmp)
    _p = A.status(_tmp).get("path") or ""
    _raw = io.open(_p, "rb").read() if _p and os.path.exists(_p) else b""
    ok("B1 退路生效：写了启动脚本（源码运行方式＝py|boot 形式，直接写 .cmd）",
       _ok3 and _p.endswith(".cmd"), os.path.basename(_p))
    # 真·退路：目标是普通 exe（非 | 形式）但快捷方式建不出来 ⇒ 必须落到 .cmd 并如实说明原因
    for _f in os.listdir(_tmp):
        os.remove(os.path.join(_tmp, _f))
    A.target = lambda: ("C:/fake/launcher.exe", _tmp, "判据桩·普通 exe")
    _ok3b, _why3b = A.enable(_tmp)
    _pb = A.status(_tmp).get("path") or ""
    ok("B1b ★ 快捷方式失败 ⇒ 自动退到 .cmd 并**如实说明**（不假装成功）",
       _ok3b and _pb.endswith(".cmd") and _sm.has(_why3b, "快捷方式没能建出来"), os.path.basename(_pb))
    ok("B2 ★ 启动脚本**行尾 CRLF**（LF 会让 cmd 一行都跑不动 —— 本仓实测踩过）",
       _raw.count(b"\r\n") >= 4 and b"\n" not in _raw.replace(b"\r\n", b""), "%d 行" % _raw.count(b"\r\n"))
    ok("B3 ★ 启动脚本**内容纯 ASCII**（cmd 按 ANSI 读文件，中文会变乱码命令）",
       bool(_raw) and all(b < 128 for b in _raw), "非 ASCII 字节数=%d" % sum(1 for b in _raw if b > 127))
finally:
    A._make_shortcut, A.target = _real_sc, _real_target
    for _f in os.listdir(_tmp):
        try:
            os.remove(os.path.join(_tmp, _f))
        except OSError as e:
            print("   （临时文件清理失败：%s）" % e)
shutil.rmtree(_tmp, ignore_errors=True)

print("\n── C. 边界（反向锚：只动当前用户启动文件夹）──")
_s = src("agent/autostart.py")
_code = code_only(_s)
ok("C1 不碰注册表（代码里没有 winreg / HKEY_ / HKCU\…\Run —— 只看代码，不看注释里的声明）",
   (not _sm.has(_code, "winreg")) and (not _sm.has(_code, "HKEY_")) and (not _sm.has(_code, "HKCU"))
   and (not _sm.has(_code, "CurrentVersion")))
# 老机制（`scripts` 目录下那个开机自启 .bat 用计划任务注册、要管理员）产品里本来就有 ⇒ 口径改成：
# **只认、只删、不建**（建它交回原来那个 .bat）—— 只认一套会让「其实开着」显示成「未开启」，最误导。
ok("C2 ★ 老计划任务只认只删、不建（/query 与 /delete 有，/create 一处都没有）",
   _sm.has(_code, "/query") and _sm.has(_code, "/delete") and not _sm.has(_code, "/create"),
   "task=%s" % A.LEGACY_TASK)
ok("C3 只写「当前用户启动文件夹」（路径来自 %APPDATA% 下的 Startup）",
   _sm.has(_s, 'os.environ.get("APPDATA"') and _sm.has(_s, '"Startup"'))
ok("C4 非 Windows 如实拒绝（不假装成功）",
   _sm.has(_s, "def supported()") and _sm.has(_s, "只做 Windows 当前用户级自启"))

print("\n── C5. 老机制也要看得见 / 关得掉（否则用户以为关了其实还开着）──")
_real_legacy, _real_rm = A._legacy_task_enabled, A._remove_legacy_task
try:
    A._legacy_task_enabled = lambda: True
    _st_legacy = A.status(_tmp)
    ok("C5a 老机制开着时，状态必须如实报「已开启（老机制）」",
       _st_legacy.get("enabled") is True and _st_legacy.get("method") == "legacy-task",
       str(_st_legacy.get("method")))
    ok("C5b 人话里说清「要管理员才能关」", _sm.has(A.status_text(), "管理员"), A.status_text()[:60])
    _rm_called = []
    A._remove_legacy_task = lambda: (_rm_called.append(1), (True, "（判据桩）"))[1]
    _ok_d, _why_d = A.disable(_tmp)
    ok("C5c 关闭时一并尝试删老任务（不然「关了」是假的）",
       _ok_d and _rm_called and _sm.has(_why_d, "判据桩"), _why_d[:50])
finally:
    A._legacy_task_enabled, A._remove_legacy_task = _real_legacy, _real_rm

print("\n── D. 接线（前端 + 后端 + CLI/菜单）──")
_routes = src("agent/routes.py")
ok("D1 路由表声明了 `/api/autostart` 的 GET+POST",
   _sm.has(_routes, '"/api/autostart": ("GET", "POST")'))
ok("D2 HANDLERS 里成对接线到两个处理函数",
   _sm.has(_routes, '"/api/autostart": {"GET": "_rapi_autostart_get", "POST": "_rapi_autostart_post"}'))
_web = src("agent/webui.py")
ok("D3 两个处理函数真的存在，且 **POST 要求显式 enable**（不许含糊地改系统状态）",
   _sm.has(_web, "def _rapi_autostart_get(") and _sm.has(_web, "def _rapi_autostart_post(")
   and _sm.has(_web, '请求体要带 enable: true/false'))
_html = src("assets/console/index.html")
ok("D4 网页控制台有开关 + 说明 + 状态回显（用户要的「给选项」就是它）",
   _sm.has(_html, 'id="autostartCk"') and _sm.has(_html, 'id="autostartHint"')
   and _sm.has(_html, "loadAutostart()") and _sm.has(_html, "'/api/autostart'"))
ok("D5 开关**默认是关的**（页面加载时以服务端状态为准，不预先勾选）",
   _sm.has(_html, "ck.checked = !!a.enabled"))
_cli = src("scripts/pmctl.py")
ok("D6 无界面入口也能开关：CLI 子命令 + 菜单项都在",
   _sm.has(_cli, 'sub.add_parser("autostart"') and _sm.has(_cli, "开机自动启动（可选，默认不开）"))
ok("D7 反向锚：自启**不是**配置键（不掺进 config.json 语义）",
   not _sm.has(src("agent/config.py"), '"autostart"'))

print("\n自启判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
