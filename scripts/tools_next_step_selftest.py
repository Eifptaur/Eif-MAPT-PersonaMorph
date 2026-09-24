#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「装完怎么引导」判据：状态条 + 下一步 + 展示字段（对标 nonebot2 / koishi 的落地项 A/B/D/E）。

为什么单独一条（对标结论）：两家的答案是"装完让你自己去配置页"，**"装完怎么引导"这一格是空的**；
我们已有 `GUIDES.tools` 弹窗，缺的是**状态**——面板上看不出"我卡在第几步"。
本判据钉住：

  ① 展示字段：`usage` / `examples` / `version` / `author` / `homepage` 能过校验、进快照；
     格式不对就**拒绝并说清**（homepage 非 http(s) / examples 不是数组）
  ② `usage` / `examples` **不进提示词**（只进控制台）——省 token 的边界（`tools.py` 的裁剪注释）
  ③ `next_step` 只读已有状态推导，六个码各有各的触发条件
  ④ 坏清单带上「修法」与原因码（`manifest_bad` 在码表里）
  ⑤ 文案只有一份：状态条那一步**取自 `GUIDES.tools.steps`**（后端不给说明文案）
  ⑥ 防抖：4 秒轮询重画时**只在文本变化时写 DOM**；「本次没给模型的工具」展开状态不被收回去
  ⑦ 空状态不啰嗦、全做完就收起（对标里写死的两条「不做」判据）

全程离线、只在临时目录里造清单，不碰产品数据、不出网。
"""
import io
import json
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _srcmatch as _sm # noqa: E402  空白容忍的源码断言（脆断言只许降不许升）

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


from agent import console_html as CH # noqa: E402
from agent import reason_codes as RC # noqa: E402
from agent import tool_stats as TS # noqa: E402
from agent import user_tools as UT # noqa: E402

HTML = CH.HTML
_SRC = io.open(os.path.join(ROOT, "agent", "user_tools.py"), encoding="utf-8").read()
_SEG = _SRC[_SRC.find("def snapshot("):]

print("── A. 展示字段：能过、进快照、坏格式会拒 ──")
_base = {"name": "demo_tool", "description": "判据里临时造的工具", "method": "GET",
         "url": "https://api.example.com/x", "allow_hosts": ["api.example.com"],
         "params": {"type": "object", "properties": {}, "required": []}}
_clean, _why = UT.validate(dict(_base, usage="查天气时用", examples=['{"city":"北京"}'],
                                version="1.0", author="老王", homepage="https://example.com/me"))
ok("五个展示字段都能过校验", bool(_clean), _why)
ok("字段原样进 clean（后面 snapshot 才带得出去）",
   _clean.get("usage") == "查天气时用" and _clean.get("examples") == ['{"city":"北京"}']
   and _clean.get("version") == "1.0" and _clean.get("author") == "老王"
   and _clean.get("homepage") == "https://example.com/me")
_bad, _why2 = UT.validate(dict(_base, homepage="ftp://example.com"))
ok("homepage 非 http(s) ⇒ 拒绝并说清", _bad is None and "http" in _why2, _why2[:60])
_bad2, _why3 = UT.validate(dict(_base, examples='{"city":"北京"}'))
ok("examples 不是数组 ⇒ 拒绝", _bad2 is None and "examples" in _why3, _why3[:60])
_many, _ = UT.validate(dict(_base, examples=["1", "2", "3", "4", "5"]))
ok("examples 最多留 3 条（不许把面板撑爆）", len(_many.get("examples") or []) == 3, str(_many.get("examples")))
ok("usage 不进提示词（快照字段在 snapshot 里，工具定义里没有它）",
   _sm.has(_SRC, '"usage": usage, "examples": examples') and not _sm.has(_SRC, '"description": "[自定义工具] " + t["description"] + t.get("usage"'))

print("── B. 下一步：六个码各有各的触发条件（只读已有状态）──")
tmp = tempfile.mkdtemp(prefix="pm_toolsstep_")
_real_cfg, _real_stats = UT._cfg, TS.snapshot
_d = {"dir": tmp, "enabled": False, "max_tools": 30, "timeout_ms": 8000, "max_chars": 4000}


def _stat(calls=0):
    c = {"user:demo_tool": calls} if calls else {}
    return {"counts": c, "last": {}, "errors": {}, "total": calls, "kinds": 1 if calls else 0}


try:
    UT._cfg = lambda: dict(_d, enabled=False)
    TS.snapshot = lambda: _stat(0)
    ok("空目录 ⇒ gen_template（第 1 步：生成模板）",
       UT.snapshot()["next_step"] == {"code": "gen_template", "step": 1}, str(UT.snapshot()["next_step"]))
    _p = os.path.join(tmp, "demo.json")
    with io.open(_p, "w", encoding="utf-8") as fh:
        json.dump(dict(_base, allow_hosts=["other.example.com"]), fh, ensure_ascii=False) # 故意制造坏清单
    _s = UT.snapshot()
    ok("有坏清单 ⇒ fix_manifest（第 2 步：改字段）",
       _s["next_step"] == {"code": "fix_manifest", "step": 2}, str(_s["next_step"]))
    ok("坏清单带上原因码与码的人话", _s["problems"][0].get("code") == "manifest_bad"
       and _s["problems"][0].get("code_label") == RC.CODES["manifest_bad"], str(_s["problems"][0])[:80])
    ok("坏清单带上**怎么改**（人话修法）", bool(str(_s["problems"][0].get("fix") or "").strip()),
       str(_s["problems"][0].get("fix"))[:60])
    with io.open(_p, "w", encoding="utf-8") as fh:
        json.dump(dict(_base, enabled=True), fh, ensure_ascii=False)
    ok("清单好了但总开关关着 ⇒ enable_switch（第 3 步）",
       UT.snapshot()["next_step"] == {"code": "enable_switch", "step": 3}, str(UT.snapshot()["next_step"]))
    UT._cfg = lambda: dict(_d, enabled=True)
    with io.open(_p, "w", encoding="utf-8") as fh:
        json.dump(dict(_base, enabled=False), fh, ensure_ascii=False)
    ok("开关开了但一个都没勾 ⇒ tick_tool（第 3 步）",
       UT.snapshot()["next_step"] == {"code": "tick_tool", "step": 3}, str(UT.snapshot()["next_step"]))
    with io.open(_p, "w", encoding="utf-8") as fh:
        json.dump(dict(_base, enabled=True), fh, ensure_ascii=False)
    ok("勾了但一次都没调用过 ⇒ try_call（第 4 步：试一下）",
       UT.snapshot()["next_step"] == {"code": "try_call", "step": 4}, str(UT.snapshot()["next_step"]))
    TS.snapshot = lambda: _stat(2)
    ok("调用过 ⇒ done（整条收起）", UT.snapshot()["next_step"] == {"code": "done", "step": 0},
       str(UT.snapshot()["next_step"]))
    _ss = UT.snapshot()
    ok("快照带上勾选数与展示字段",
       _ss["ticked"] == 1 and _ss["tools"][0].get("usage", "") == "" and "examples" in _ss["tools"][0])
finally:
    UT._cfg, TS.snapshot = _real_cfg, _real_stats
    shutil.rmtree(tmp, ignore_errors=True)

print("── C. 文案只有一份 + 防抖 + 两条「不做」判据（源码级）──")
_i = HTML.find('id="sec-tools"')
_seg = HTML[_i:HTML.find("</section>", _i)] if _i > 0 else ""
ok("分区顶部有状态条 #utBar", 'id="utBar"' in _seg)
ok("状态条四段都在（它是什么 / 状态 / 下一步 / 出错去哪看）",
   'id="utBarStat"' in _seg and 'id="utBarNext"' in _seg and "坏清单逐条列在下面" in _seg)
ok("⑤ 下一步的说明**取自 GUIDES.tools.steps**（后端不给说明文案）",
   _sm.has(HTML, "GUIDES.tools && GUIDES.tools.steps") and _sm.has(HTML, "steps[ns.step - 1]"))
ok("⑤ 状态行也只有一份（utStatLine 同时喂 #utGlobals 与状态条）",
   _sm.has(HTML, "g.textContent = utStatLine(ut)") and HTML.count("utStatLine(") >= 3)
ok("⑥ 只有变化才写 DOM（4 秒轮询不许抖）",
   _sm.has(HTML, "if(stat.textContent !== line)") and _sm.has(HTML, "box.dataset.sig === sig"))
ok("⑦ 空状态只留第一段（untouched 时不显示状态/下一步）", _sm.has(HTML, "const untouched ="))
ok("⑦ 全做完就收起（done ⇒ display:none）", _sm.has(HTML, "ns.code === 'done'", "bar.style.display = 'none'"))
ok("坏清单那几行显示原因码 + 修法", _sm.has(HTML, "p.code_label") and _sm.has(HTML, "p.fix"))
ok("⑥「本次没给模型的工具」接到界面（数据早就算好，之前零引用）",
   'id="utDropped"' in _seg and _sm.has(HTML, "utRenderDropped(s.tools || {})") and _sm.has(HTML, "tl.dropped"))
ok("码表里有 manifest_bad", "manifest_bad" in RC.CODES)
_rd = io.open(os.path.join(ROOT, "tools.d", "README.md"), encoding="utf-8").read()
ok("README 字段表补了五个展示字段 + 「不是安装源」边界",
   all(("`%s`" % k) in _rd for k in ("usage", "examples", "version", "author", "homepage"))
   and _sm.has(_rd, "不是安装源"))

print("── D. 导入/导出：文档 → 插件（前后端映射一起钉）──")
_tmp2 = tempfile.mkdtemp(prefix="pm_toolsexp_")
_real_cfg2 = UT._cfg
try:
    UT._cfg = lambda: {"dir": _tmp2, "enabled": True, "max_tools": 30, "timeout_ms": 8000, "max_chars": 4000}
    _a = dict(_base, name="exp_a", usage="甲怎么用", author="张三", version="1.0")
    _b = dict(_base, name="exp_b", url="https://api.example.com/b")
    _doc = json.dumps({"kind": UT.BUNDLE_KIND, "version": "1.0", "count": 2, "tools": [_a, _b]}, ensure_ascii=False)
    _r = UT.import_text(_doc)
    ok("导入一份捆：两个都写进去了", _r["ok"] and _r["added"] == ["exp_a", "exp_b"], str(_r["added"]))
    ok("落盘文件名＝工具名.json",
       sorted(os.listdir(_tmp2)) == ["exp_a.json", "exp_b.json"], str(sorted(os.listdir(_tmp2))))
    _txt, _why = UT.export_text()
    _back = json.loads(_txt)
    ok("导出全部＝一份带 kind/count/tools 的文档",
       _back.get("kind") == UT.BUNDLE_KIND and _back.get("count") == 2 and len(_back["tools"]) == 2, _why)
    _one, _onewhy = UT.export_text("exp_a")
    _onej = json.loads(_one)
    ok("单份导出只有那一份清单（没有捆的壳）",
       _onej.get("name") == "exp_a" and "kind" not in _onej and _onej.get("usage") == "甲怎么用", _onewhy)
    # 往返：把导出的文档导进**另一个目录**，结果必须一致
    _tmp3 = tempfile.mkdtemp(prefix="pm_toolsexp2_")
    UT._cfg = lambda: {"dir": _tmp3, "enabled": True, "max_tools": 30, "timeout_ms": 8000, "max_chars": 4000}
    _r2 = UT.import_text(_txt)
    _round = {t["name"]: t for t in UT.load()[0]}
    ok("往返一致：导出→导入另一处，名字/地址/白名单/展示字段都对得上",
       _r2["ok"] and _round.get("exp_a", {}).get("url") == "https://api.example.com/x"
       and _round.get("exp_b", {}).get("allow_hosts") == ["api.example.com"]
       and _round.get("exp_a", {}).get("version") == "1.0", str(_r2["added"]))
    UT._cfg = lambda: {"dir": _tmp2, "enabled": True, "max_tools": 30, "timeout_ms": 8000, "max_chars": 4000}
    _n_before = sorted(os.listdir(_tmp2))
    _r3 = UT.import_text(_doc)
    ok("重名默认不覆盖（跳过并说清怎么替换）",
       not _r3["added"] and len(_r3["skipped"]) == 2 and "覆盖同名" in _r3["skipped"][0]["why"],
       str(_r3["skipped"][0]["why"])[:40])
    ok("跳过时一个字节都没写（文件列表不变）", sorted(os.listdir(_tmp2)) == _n_before, str(sorted(os.listdir(_tmp2))))
    _r4 = UT.import_text(_doc, overwrite=True)
    ok("勾了「覆盖同名」才替换", _r4["replaced"] == ["exp_a", "exp_b"], str(_r4["replaced"]))
    _bad_doc = json.dumps([dict(_base, name="exp_bad", url="https://x.example.org/",
                                allow_hosts=["other.com"], description="白名单对不上的工具")], ensure_ascii=False)
    _r5 = UT.import_text(_bad_doc)
    ok("坏清单被拦下、带修法、**不写盘**",
       not _r5["added"] and "白名单" in _r5["skipped"][0]["why"] and "exp_bad.json" not in os.listdir(_tmp2),
       str(_r5["skipped"][0])[:70])
    ok("不是 JSON ⇒ 明确说清", UT.import_text("不是 json")["error"].startswith("不是合法的 JSON"))
    ok("空文档（[]）⇒ 明确说清", "没有工具" in UT.import_text("[]")["error"])
    ok("少 name 的清单 ⇒ 当坏清单列出来（不是静默通过）",
       "不合规" in UT.import_text("{}")["skipped"][0]["why"], str(UT.import_text("{}")["skipped"][0]["why"])[:50])
    _builtin = UT._builtin_names()
    if _builtin:
        _r6 = UT.import_text(json.dumps(dict(_base, name=_builtin[0], description="撞内置名的工具"), ensure_ascii=False))
        ok("与内置工具重名 ⇒ 拒（%s）" % _builtin[0],
           not _r6["added"] and "重名" in _r6["skipped"][0]["why"], str(_r6["skipped"][0]["why"])[:50])
    else:
        print("  SKIP 内置名清单取不到（这个环境 import 不进 tools.py）")
finally:
    UT._cfg = _real_cfg2
    shutil.rmtree(_tmp2, ignore_errors=True)
    shutil.rmtree(_tmp3, ignore_errors=True)

_src = io.open(os.path.join(ROOT, "agent", "user_tools.py"), encoding="utf-8").read()
_seg_imp = _src[_src.find("def import_text("):_src.find("def _next_step(")]
ok("导入只写 JSON：不执行代码、不下载、不碰 data/",
   not any(k in _seg_imp for k in ("exec(", "eval(", "subprocess", "urlopen", "urllib.request.urlopen", "shutil.copy")))
ok("导入走原子写（persist.atomic_write_json），不自己造 .tmp",
   _sm.has(_seg_imp, "persist.atomic_write_json"))
_wu2 = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
from agent.routes import ROUTES as _R2, HANDLERS as _HD2 # noqa: E402
ok("路由表：/api/tools/export = GET → _rapi_tools_export",
   _R2.get("/api/tools/export") == ("GET",) and (_HD2.get("/api/tools/export") or {}).get("GET") == "_rapi_tools_export")
ok("路由表：/api/tools/import = POST → _rapi_tools_import_post",
   _R2.get("/api/tools/import") == ("POST",)
   and (_HD2.get("/api/tools/import") or {}).get("POST") == "_rapi_tools_import_post")
ok("两个方法都在 webui 里，且走 user_tools 的同一实现（不另写一份）",
   _sm.has(_wu2, "def _rapi_tools_export(self, path, data, parsed, method):", "_ut6.export_text(nm)")
   and _sm.has(_wu2, "_ut8.import_text(text, overwrite=as_bool(", "r[\"tools\"] = _ut8.snapshot()"))
ok("前后端映射：面板有导入/导出按钮、且都打到了对应端点",
   'id="utExport"' in _seg and 'id="utImport"' in _seg
   and _sm.has(HTML, "const be = document.getElementById('utExport')")
   and _sm.has(HTML, "const bi = document.getElementById('utImport')")
   and _sm.has(HTML, "/api/tools/export") and _sm.has(HTML, "/api/tools/import"))
ok("每行也有「导出」（只导这一份）", _sm.has(HTML, "eb.textContent = '导出'", "utExportDlg(t.name)"))
ok("引导里写了这一步（第 ⑤ 步：导入/导出）", _sm.has(HTML, "⑤", "导入工具", "导出工具"))

print("")
print("「装完怎么引导」判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
