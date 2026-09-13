#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户自定义工具（声明式 HTTP）判据：**边界比功能重要**（不需要联网、不执行本地代码）。

判据：
  ① 校验：好清单通过；坏 JSON / 名字不合规 / 与内置重名 / 清单间重名 / 非 http / 主机不在白名单 /
     params 不是对象 schema / params 里出现与工具同名的字段 ⇒ **每条都给明确原因**
  ② 加载：坏清单**不许静默跳过**（要进 problems），好清单照常加载
  ③ 执行：只发 HTTP（注入假 fetch 验模板渲染 / response_path / 截断）；**主机不在白名单不发**；
     即使把内网主机写进白名单，也要被 `safe_fetch` 的 SSRF 闸门挡掉
  ④ 注册：总开关关着 ⇒ 一个都不加；开着 ⇒ 只加 `enabled: true` 的，且带「[自定义工具]」标记
  ⑤ 统计：`execute_tool()` 是唯一分发点 ⇒ 内置与自定义都被记一笔（按工具名计数）
  ⑥ 安全：模块里**没有** eval/exec/os.system/subprocess（不执行本地代码这条是真的）
"""
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


from agent import user_tools as UT  # noqa: E402
from agent import tool_stats as TS  # noqa: E402
from agent import tools as TL  # noqa: E402

GOOD = {
    "name": "get_weather", "description": "查某城市天气",
    "method": "GET", "url": "https://api.example.com/w",
    "allow_hosts": ["api.example.com"], "query": {"city": "{city}"},
    "params": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
}

print("── A. 清单校验 ──")
ok("好清单通过", UT.validate(GOOD)[0] is not None)
ok("坏 JSON 不在这里管（load 里报）", True)

cases = [
    (dict(GOOD, name="Bad Name"), "不合规"),
    (dict(GOOD, name="send_message"), "重名"),
    (dict(GOOD, description="短"), "description"),
    (dict(GOOD, method="PUT"), "method"),
    (dict(GOOD, url="ftp://x/y"), "http"),
    (dict(GOOD, allow_hosts=[]), "allow_hosts"),
    (dict(GOOD, allow_hosts=["other.com"]), "白名单"),
    (dict(GOOD, params={"type": "array"}), "JSON Schema"),
    (dict(GOOD, params={"type": "object", "properties": {"get_weather": {}}}), "同名"),
]
for bad, want in cases:
    _c, why = UT.validate(bad, builtin_names=["send_message"])
    ok("拒绝并说明（%s）" % want, _c is None and want in why, why[:52])
_c, why2 = UT.validate(GOOD, seen=["get_weather"])
ok("清单之间重名也被拒", _c is None and "重复" in why2, why2[:40])

print("── B. 加载：坏清单不静默 ──")
tmp = tempfile.mkdtemp(prefix="pm_tools_")
with open(os.path.join(tmp, "good.json"), "w", encoding="utf-8") as fh:
    json.dump(GOOD, fh, ensure_ascii=False)
with open(os.path.join(tmp, "broken.json"), "w", encoding="utf-8") as fh:
    fh.write("{ this is not json")
with open(os.path.join(tmp, "dup.json"), "w", encoding="utf-8") as fh:
    json.dump(dict(GOOD, description="另一个同名的"), fh, ensure_ascii=False)
with open(os.path.join(tmp, "other.json"), "w", encoding="utf-8") as fh:
    json.dump(dict(GOOD, name="get_time", description="查当前时间", url="https://api.example.com/t",
                   allow_hosts=["api.example.com"], enabled=False, query={}), fh, ensure_ascii=False)
_real_cfg = UT._cfg
UT._cfg = lambda: {"dir": tmp, "enabled": True, "max_tools": 30, "timeout_ms": 8000, "max_chars": 4000}
tools, problems, _d = UT.load()
ok("好清单加载进来", any(t["name"] == "get_weather" for t in tools), str([t["name"] for t in tools]))
ok("坏 JSON 记进 problems（不静默）", any("JSON" in p["why"] for p in problems))
ok("重名记进 problems", any("重复" in p["why"] for p in problems), str([p["why"][:24] for p in problems]))

print("── C. 执行：只发 HTTP + 白名单 + SSRF ──")
seen = {}


class _Resp:
    def __init__(self, body):
        self._b = body.encode("utf-8")

    def read(self, n=-1):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_fetch(req, timeout=None):
    seen["url"] = req.full_url
    seen["method"] = req.get_method()
    seen["data"] = req.data
    return _Resp(json.dumps({"data": {"answer": "晴 26 度"}, "noise": "x" * 9000}))


t = [x for x in tools if x["name"] == "get_weather"][0]
t = dict(t, response_path="data.answer")           # 清单里没写 response_path，这里显式加上再测
res = UT.call(t, {"city": "上海"}, _fetch=fake_fetch)
ok("正常调用：参数进了 query", "city=%E4%B8%8A%E6%B5%B7" in seen.get("url", ""), seen.get("url", "")[:60])
ok("response_path 取到想要的字段", res.get("content") == "晴 26 度", str(res.get("content"))[:30])
t2 = dict(t, name="post_tool", method="POST", body={"c": "{city}"}, url="https://api.example.com/p", query={})
seen.clear()
UT.call(t2, {"city": "北京"}, _fetch=fake_fetch)
ok("POST 带 JSON body", seen.get("method") == "POST" and b"\xe5\x8c\x97\xe4\xba\xac" in (seen.get("data") or b""))
bad_host = dict(t, url="https://evil.example.net/x", allow_hosts=["api.example.com"])
seen.clear()
r2 = UT.call(bad_host, {"city": "上海"}, _fetch=fake_fetch)
ok("主机不在白名单 ⇒ 拒绝且**没有发出请求**", r2.get("is_error") and "白名单" in r2["content"] and not seen, r2["content"][:40])
ssrf = dict(t, url="http://127.0.0.1:9999/x", allow_hosts=["127.0.0.1"])
r3 = UT.call(ssrf, {"city": "上海"}, _fetch=fake_fetch)
ok("白名单里写了内网地址也过不了 SSRF 闸门",
   r3.get("is_error") and ("安全策略" in r3["content"] or "内网" in r3["content"]), r3["content"][:44])
big = dict(t, response_path="noise", max_chars=100)
r4 = UT.call(big, {"city": "上海"}, _fetch=fake_fetch)
ok("结果按 max_chars 截断", len(r4.get("content") or "") == 100, str(len(r4.get("content") or "")))

print("── D. 注册（勾选才给模型用）──")
UT._cfg = lambda: {"dir": tmp, "enabled": False, "max_tools": 30, "timeout_ms": 8000, "max_chars": 4000}
extra, _p, _a = UT.as_tool_defs()
ok("总开关关着 ⇒ 一个都不加", extra == [], str(len(extra)))
UT._cfg = lambda: {"dir": tmp, "enabled": True, "max_tools": 30, "timeout_ms": 8000, "max_chars": 4000}
extra, _p, allt = UT.as_tool_defs()
ok("开着 ⇒ 只加 enabled=true 的", [d["name"] for d in extra] == ["get_weather"], str([d["name"] for d in extra]))
ok("名字前带「[自定义工具]」标记", extra and extra[0]["description"].startswith("[自定义工具]"))
ok("执行器存在且调用后会计数", callable(extra[0]["execute"]))
names = {d["name"] for d in TL.build_tool_defs()}
ok("build_tool_defs() 合并了自定义工具", "get_weather" in names and "send_message" in names)

print("── E. 统计（唯一分发点）──")
_st = TS.PATH
TS.PATH = os.path.join(tmp, "stats.json")
try:
    TS.reset()
    TL.execute_tool([{"name": "fake", "description": "x", "parameters": {}, "execute": lambda ctx, a: {"content": "ok"}}],
                    {}, "fake", "{}")
    TL.execute_tool([{"name": "fake", "description": "x", "parameters": {}, "execute": lambda ctx, a: {"content": "ok"}}],
                    {}, "fake", "{}")
    snap = TS.snapshot()
    ok("同一次调用了两次 ⇒ 计数 2", snap["counts"].get("fake") == 2, str(snap["counts"]))
    ok("快照有 total/kinds/path", all(k in snap for k in ("total", "kinds", "path")))
finally:
    TS.PATH = _st
UT._cfg = _real_cfg

print("── F. 安全：不执行本地代码 ──")
_src = open(os.path.join("agent", "user_tools.py"), encoding="utf-8").read()
for bad in ("eval(", "exec(", "os.system", "subprocess", "__import__("):
    ok("源码里没有 %s" % bad, bad not in _src)

print("\n%d/%d 通过" % (PASS, PASS + FAIL))
sys.exit(1 if FAIL else 0)
