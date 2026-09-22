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
import _srcmatch as _sm  # noqa: E402  空白容忍的源码断言（脆断言只许降不许升）

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


from agent import console_html as CH  # noqa: E402
from agent import reason_codes as RC  # noqa: E402
from agent import tool_stats as TS  # noqa: E402
from agent import user_tools as UT  # noqa: E402

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
        json.dump(dict(_base, allow_hosts=["other.example.com"]), fh, ensure_ascii=False)   # 故意制造坏清单
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

print("")
print("「装完怎么引导」判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
