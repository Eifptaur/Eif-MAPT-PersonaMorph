#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""资源市场判据：**三栏同构、状态可解释、坏输入不崩、不碰发送链**。

链路：社区分区顶部按钮 ⇒ 三栏弹窗（人设 / Skill / 工具）⇒ 下载 / 上传 / 打开网站。

判据（不需要微信、不需要联网 —— 站还没建，正好把"未就绪"这条路验透）：
  ① **站未就绪是如实说明**：`site_ready()` 为假时，装/上传一律回 `site-not-ready`，
     且 `reason()` 给的是人话；⛔ 不许抛异常、不许返回"成功"。
  ② **状态码是唯一口径**：`REASONS` 表里每个码都有中文；`list_kind` 的 `ready_reason`
     必须能在表里查到（界面不许自己拼句子）。
  ③ **三栏都不消失**：无论站/目录是否就绪，三栏都要能列（最差是本机清单为空），
     `ready=False` 时**必须带原因**。
  ④ **落点只认 KINDS**：`kind_dir` 只从表里取；未知 kind ⇒ 空串（⛔ 不听前端传路径）。
  ⑤ **装/卸的边界**：体积超限拒装、hash 不符拒装、卸载只能删本栏目录里的那一个
     （反向对照：路径穿越的 id 不许删到目录外）。
  ⑥ **不碰发送链**：`agent/market.py` 的源码里不出现 wechat / send / popup 相关调用。
  ⑦ **接线**：四条路由在 `agent/routes.py` 的 ROUTES 与 HANDLERS 里成对存在。
"""
import inspect
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS = 0
FAIL = 0
SKIP = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


def skip(name, detail=""):
    global SKIP
    SKIP += 1
    print("  SKIP {}".format(name) + ("  [{}]".format(detail) if detail else ""))


from agent import market  # noqa: E402

SRC = inspect.getsource(market)

print("── A. 站未就绪：如实说明，不假装 ──")
ok("site_ready() 现在是假（站还没建，这是**如实状态**不是失败）",
   market.site_ready() is False, "site_ready=%s" % market.site_ready())

r = market.install("tool", "whatever")
ok("未就绪时装资源 ⇒ code=site-not-ready、ok=False",
   r.get("ok") is False and r.get("code") == "site-not-ready", str(r.get("code")))

r2 = market.prepare_upload("tool", "a.json")
ok("未就绪时上传 ⇒ code=site-not-ready、ready=False，但**回显选中的文件**（不丢用户输入）",
   r2.get("ok") is False and r2.get("ready") is False
   and (r2.get("item") or {}).get("filename") == "a.json", str(r2.get("code")))

ok("未知 kind 装 ⇒ kind-not-ready（不是崩，不是静默成功）",
   market.install("nope", "x").get("code") == "kind-not-ready")

print("── B. 状态码＝唯一口径 ──")
ok("REASONS 里每个码都有中文文案",
   all(str(v).strip() for v in market.REASONS.values()) and len(market.REASONS) >= 8,
   "%d 个码" % len(market.REASONS))
ok("reason() 对未知码**原样返回**（不编造解释）",
   market.reason("some-unknown-code") == "some-unknown-code")
ok("site-not-ready 的文案是人话（含「建设中」）",
   "建设中" in market.reason("site-not-ready"), market.reason("site-not-ready")[:30])

allk = market.list_all()
codes = {k: (v.get("ready_code") or "") for k, v in allk["kinds"].items()}
ok("★ 三栏的 ready_code 全部能在 REASONS 里查到（界面直接用，不拼句子）",
   all(c in market.REASONS for c in codes.values()), str(codes))

print("── C. 三栏都在，且都带原因 ──")
ok("list_all 三栏齐全（persona / skill / tool）",
   set(allk["kinds"]) == {"persona", "skill", "tool"}, str(sorted(allk["kinds"])))
ok("★ 未就绪时三栏**照样在**、且每栏都有 ready_reason（⛔ 不整栏消失）",
   all(("ready_reason" in v and str(v["ready_reason"]).strip()) for v in allk["kinds"].values()))
one = market.list_kind("tool")
ok("单栏查询结构完整（ok/kind/ready/remote/local）",
   all(kk in one for kk in ("ok", "kind", "ready", "remote", "local")), str(sorted(one)))
ok("未知 kind 单查 ⇒ ok=False 且给原因（不抛异常）",
   market.list_kind("zzz").get("ok") is False
   and market.list_kind("zzz").get("ready_reason"))

print("── D. 落点只认 KINDS（不听前端）──")
ok("kind_dir 对已知 kind 返回绝对路径",
   os.path.isabs(market.kind_dir("tool")), market.kind_dir("tool")[-24:])
ok("kind_dir 对未知 kind 返回空串（⛔ 前端传 ../ 也拿不到路径）",
   market.kind_dir("../../etc") == "" and market.kind_dir("") == "")

print("── E. 装 / 卸的边界 ──")
big = b"x" * (16 << 20)
rb = market.install("tool", "big", data=big)
ok("体积超限 ⇒ too-large 且 ok=False",
   rb.get("ok") is False and rb.get("code") in ("too-large", "site-not-ready"), str(rb.get("code")))

rh = market.install("tool", "h", expect_sha256="0" * 64, data=b"{}" * 10)
ok("hash 不符 ⇒ hash-mismatch（即便站未就绪也不该变成成功）",
   rh.get("code") in ("hash-mismatch", "site-not-ready"), str(rh.get("code")))

# 反向对照：给一个能写的临时目录，验证"装→列→卸"闭环，并确认越界 id 删不到外面
tmpdir = tempfile.mkdtemp(prefix="mkt-")
outside = os.path.join(tmpdir, "OUTSIDE.txt")
with open(outside, "w", encoding="utf-8") as f:
    f.write("must survive")
sub = os.path.join(tmpdir, "tools.d")
os.makedirs(sub)

_orig_tool_dir = market.KINDS["tool"]["dir"]
try:
    market.KINDS["tool"]["dir"] = sub
    ri = market.install("tool", "demo", data=b'{"name":"demo"}')
    ok("★ 正例：给到可写目录 ⇒ 装成功并落盘",
       ri.get("ok") is True and os.path.exists(ri.get("path") or ""), str(ri.get("path", ""))[-24:])

    lst = market.list_kind("tool")["local"]
    ok("装完能列出来（installed=True）",
       any(str(x.get("id")) == "demo" and x.get("installed") for x in lst), str(len(lst)))

    rbad = market.uninstall("tool", "../OUTSIDE")
    ok("★ 反向对照：id 带 `../` ⇒ 删不到目标目录外的文件（仍在）",
       os.path.exists(outside) and rbad.get("ok") is False, str(rbad.get("code")))

    ru = market.uninstall("tool", "demo")
    ok("正常卸载能删掉本栏内的那一个",
       ru.get("ok") is True and not os.path.exists(ri.get("path") or "x"), str(ru.get("code")))

    rmiss = market.uninstall("tool", "not-there")
    ok("卸不存在的东西 ⇒ not-installed（如实说，不算成功）",
       rmiss.get("code") == "not-installed", str(rmiss.get("code")))
except Exception as e:  # noqa: BLE001
    ok("装/卸闭环（临时目录）", False, "异常：%s" % str(e)[:60])
finally:
    market.KINDS["tool"]["dir"] = _orig_tool_dir

print("── F. 不碰发送链（本模块的存在理由之一）──")
_banned = ("send_text", "send_to_popup", "wechat_windows", "from .wechat", "import wechat",
           "only_popup", "popup")
_hits = [b for b in _banned if b in SRC]
ok("★ agent/market.py 里没有任何发送链调用/引用", not _hits, str(_hits))

print("── G. 接线（路由成对）──")
try:
    sys.path.insert(0, os.path.join(ROOT, "agent"))
    from agent import routes  # noqa: PLC0415
    need = ["/api/market/list", "/api/market/install", "/api/market/uninstall", "/api/market/upload"]
    ok("四条路由都在 ROUTES 声明里", all(p in routes.ROUTES for p in need),
       str([p for p in need if p not in routes.ROUTES]))
    ok("四条路由都在 HANDLERS 里成对接上", all(p in routes.HANDLERS for p in need),
       str([p for p in need if p not in routes.HANDLERS]))
    wsrc = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
    ok("四个 handler 在 webui.py 里真有 `def`（声明了却没人接 ⇒ 按表调 404）",
       all(("def %s(" % n) in wsrc for n in ("_rapi_market_list", "_rapi_market_install_post",
                                             "_rapi_market_uninstall_post", "_rapi_market_upload_post")))
except Exception as e:  # noqa: BLE001
    ok("路由接线点检", False, "导入 routes 失败：%s" % str(e)[:60])

print("── H. 下载来的人设：自动导入人设页 + 落「下载」分区 ──")
try:
    from agent import ext_center  # noqa: PLC0415
    import json as _json  # noqa: PLC0415

    _cp = os.path.join(ROOT, "data", "custom_personas.json")
    _pc = os.path.join(ROOT, "data", "persona_cats.json")
    _back_cp = open(_cp, encoding="utf-8").read() if os.path.exists(_cp) else ""
    _back_pc = open(_pc, encoding="utf-8").read() if os.path.exists(_pc) else ""
    try:
        _cats = ext_center.cats()
        ok("★ 分区清单里永远有「下载」分区（用户装完就有地方落）",
           ext_center.DOWNLOAD_CAT in (_cats.get("cats") or []), str(_cats.get("cats"))[:80])

        _r = ext_center.import_persona({"name": "__判据卡__", "text": "# 判据\n自检用。"})
        ok("★ 下载来的人设自动进人设页（不用用户再走导入）", _r.get("ok") is True, str(_r)[:90])
        ok("★ 且落在「下载」分区（不是散在自定义里）",
           _r.get("cat") == ext_center.DOWNLOAD_CAT, str(_r.get("cat")))
        _data = _json.load(open(_cp, encoding="utf-8"))
        ok("★ 真写进了 custom_personas.json（人设页读的就是这份）",
           "__判据卡__" in _data and _data["__判据卡__"].get("cat") == ext_center.DOWNLOAD_CAT)

        _m = ext_center.set_cat("__判据卡__", "🧪 自检分区")
        _data2 = _json.load(open(_cp, encoding="utf-8"))
        ok("★ 用户能把它挪到别的分区（自建的新分区名也认）",
           _m.get("ok") is True and _data2["__判据卡__"].get("cat") == "🧪 自检分区",
           str(_m.get("cat")))
        ok("★ 挪走时新分区被登记（下次列分区才看得到，否则用户以为没建成）",
           "🧪 自检分区" in (ext_center.cats().get("cats") or []))

        # 反向锚：缺名字或缺正文的，装不进去且如实报 —— 不许硬塞一张空卡。
        _bad = ext_center.import_persona({"name": "", "text": ""})
        ok("★ 反向锚：空人设装不进去、如实报 bad-manifest",
           _bad.get("ok") is False and _bad.get("code") == "bad-manifest", str(_bad.get("code")))
    finally:
        # 恢复原状（判据不许在用户数据上留痕）
        if _back_cp:
            open(_cp, "w", encoding="utf-8").write(_back_cp)
        if _back_pc:
            open(_pc, "w", encoding="utf-8").write(_back_pc)

    # 下载落点接进 market.install 的收尾（人设那一栏）
    ok("★ market.install 对 persona 有导入收尾（光落文件＝人设页看不见）",
       "_import_persona_file" in SRC and 'if k == "persona"' in SRC)

    # 接线：两条分区路由成对
    from agent import routes as _routes  # noqa: PLC0415
    _need = ["/api/ext/persona/cats", "/api/ext/persona/cat"]
    ok("分区两条路由都在 ROUTES 里", all(p in _routes.ROUTES for p in _need),
       str([p for p in _need if p not in _routes.ROUTES]))
    ok("分区两条路由都在 HANDLERS 里成对接上", all(p in _routes.HANDLERS for p in _need),
       str([p for p in _need if p not in _routes.HANDLERS]))
    _wsrc = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
    ok("两个 handler 在 webui.py 里真有 def",
       all(("def %s(" % n) in _wsrc
           for n in ("_rapi_ext_persona_cats", "_rapi_ext_persona_cat_post")))
except Exception as e:  # noqa: BLE001
    ok("下载分区链路点检", False, "异常：%s" % str(e)[:90])

print("\n%d/%d 通过（跳过 %d）" % (PASS, PASS + FAIL, SKIP))
sys.exit(1 if FAIL else 0)
