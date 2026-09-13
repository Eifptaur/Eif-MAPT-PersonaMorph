# -*- coding: utf-8 -*-
"""第 3 条判据：API 错误重试 + 备选模型逐个重试（不需要联网、不花 token）。

跑法： py -3 scripts\\llm_fallback_selftest.py      退出码 0=全过 / 1=有失败

做法：把 `llm._session` 换成假会话（照 OpenAI 兼容响应造），**跑真代码路径**
（`chat_completion` / `_post_once` / `chat_completion_with_retry` 全是真的），
逐次断言「打了几个请求、按什么顺序换的模型、body 里的 model 真的换了吗、什么时候不该换」。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import requests                       # noqa: E402
from agent import llm                 # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


class FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("Expecting value: line 1 column 1")
        return self._payload


class FakeSession:
    """按脚本依次作答；脚本项可以是 FakeResp 或异常实例。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "model": (json or {}).get("model"), "headers": headers or {},
                           "tools": (json or {}).get("tools")})
        item = self.script.pop(0) if self.script else FakeResp(200, good_payload("m1"))
        if isinstance(item, BaseException):
            raise item
        return item


def good_payload(model, text="ok"):
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            "model": model}


def api(models=("m1", "m2", "m3"), primary="m1"):
    return {"base_url": "http://fake.local/v1", "api_key": "k", "model": primary,
            "fallback_models": list(models)[1:], "temperature": 0.5, "timeout_ms": 5000}


def main():
    tmp = tempfile.mkdtemp(prefix="llm-fb-judge-")
    llm._stats_path = lambda: os.path.join(tmp, "fallback_stats.json")
    msgs = [{"role": "user", "content": "hi"}]
    real_session = llm._session
    real_effective = llm.effective_api
    llm.effective_api = lambda cfg=None: api()      # 备选清单从"测试配置"里读，不碰用户的 config.json

    print("== A. 候选清单：解析 / 去重 / 上限 ==")
    ok("数组形态按顺序保留", llm.fallback_models({"fallback_models": ["a", "b"]}) == ["a", "b"])
    ok("字符串形态（中英文逗号+换行）也认（并按上限截到 3 个）",
       llm.fallback_models({"fallback_models": "a, b，c\nd"}) == ["a", "b", "c"])
    ok("去重", llm.fallback_models({"fallback_models": ["a", "a", " b ", "b"]}) == ["a", "b"])
    ok("上限 MAX_FALLBACK 截断", llm.fallback_models({"fallback_models": ["a", "b", "c", "d", "e"]}) == ["a", "b", "c"])
    ok("空/None/缺字段 ⇒ 空清单",
       llm.fallback_models({}) == [] and llm.fallback_models({"fallback_models": None}) == []
       and llm.fallback_models({"fallback_models": [" ", ""]}) == [])
    ok("candidates：主模型在前、备选在后且不与主模型重复",
       llm.candidates({"model": "m1", "fallback_models": ["m2", "m1"]}) == ["m1", "m2"])
    ok("主模型为空时只剩备选", llm.candidates({"model": "", "fallback_models": ["m2"]}) == ["m2"])

    print("== B. 什么错误值得换备选 ==")
    ok("HTTP 500 值得", llm.is_fallback_eligible(llm.LLMError("模型 API HTTP 500：boom")))
    ok("429 限流值得", llm.is_fallback_eligible(llm.LLMError("模型 API HTTP 429：rate limit")))
    ok("超时值得", llm.is_fallback_eligible(llm.LLMError("模型请求超时（5000ms）")))
    ok("返回不是 JSON 值得", llm.is_fallback_eligible(llm.LLMError("模型 API 返回了无法解析的 JSON")))
    ok("模型名不存在（404 / not found）值得——主模型下线是最常见的「换一个就能跑」",
       llm.is_fallback_eligible(llm.LLMError("模型 API HTTP 404：The model `m1` does not exist")))
    ok("401 不值得（换模型救不了 Key）",
       not llm.is_fallback_eligible(llm.LLMError("模型 API HTTP 401：Unauthorized")))
    ok("403 不值得", not llm.is_fallback_eligible(llm.LLMError("模型 API HTTP 403：Forbidden")))
    ok("Key 无效不值得", not llm.is_fallback_eligible(llm.LLMError("invalid api key")))
    ok("用户取消不值得", not llm.is_fallback_eligible(llm.LLMError("请求已取消 aborted")))
    ok("参数错（400）不值得", not llm.is_fallback_eligible(llm.LLMError("模型 API HTTP 400：bad request")))
    ok("「响应缺少 choices」值得（上游坏了）",
       llm.is_fallback_eligible(llm.LLMError("模型 API 响应缺少 choices：{}")))

    print("== C. chat_completion：主模型失败就按顺序换备选 ==")
    s = FakeSession([FakeResp(200, good_payload("m1"))])
    llm._session = s
    r = llm.chat_completion(msgs, overrides=api())
    ok("主模型成功 ⇒ 只打一次、不带 fallback 字段",
       len(s.calls) == 1 and s.calls[0]["model"] == "m1" and "fallback" not in r, len(s.calls))

    s = FakeSession([FakeResp(500, None, "boom"), FakeResp(200, good_payload("m2"))])
    llm._session = s
    r = llm.chat_completion(msgs, overrides=api())
    ok("主模型 500 ⇒ 自动换到备选 m2", r.get("fallback", {}).get("used") == "m2" and len(s.calls) == 2, r.get("fallback"))
    ok("请求体里 model 真的换成了 m2（不是只记了个标记）",
       [c["model"] for c in s.calls] == ["m1", "m2"], [c["model"] for c in s.calls])
    ok("如实记下主模型是谁、试过谁",
       r["fallback"]["from"] == "m1" and r["fallback"]["tried"] == ["m1"], r.get("fallback"))

    s = FakeSession([FakeResp(500, None, "b1"), FakeResp(500, None, "b2"), FakeResp(200, good_payload("m3"))])
    llm._session = s
    r = llm.chat_completion(msgs, overrides=api())
    ok("第二个备选才成功 ⇒ 三个模型按序各试一次",
       [c["model"] for c in s.calls] == ["m1", "m2", "m3"] and r["fallback"]["used"] == "m3", len(s.calls))

    s = FakeSession([FakeResp(500, None, "b1"), FakeResp(500, None, "b2"), FakeResp(500, None, "b3")])
    llm._session = s
    err = None
    try:
        llm.chat_completion(msgs, overrides=api())
    except Exception as e:
        err = e
    ok("全部失败 ⇒ 抛错（不吞、不死循环）", isinstance(err, llm.LLMError) and len(s.calls) == 3, str(err)[:60])

    s = FakeSession([FakeResp(401, None, "Unauthorized")])
    llm._session = s
    err = None
    try:
        llm.chat_completion(msgs, overrides=api())
    except Exception as e:
        err = e
    ok("鉴权错 ⇒ 一个备选都不试（不浪费、不掩盖 Key 问题）", isinstance(err, llm.LLMError) and len(s.calls) == 1)

    s = FakeSession([FakeResp(404, None, "The model `m1` does not exist"), FakeResp(200, good_payload("m2"))])
    llm._session = s
    r = llm.chat_completion(msgs, overrides=api())
    ok("模型名下线（404）⇒ 会换备选", r.get("fallback", {}).get("used") == "m2" and len(s.calls) == 2)

    s = FakeSession([FakeResp(200, None)])            # 200 但 body 不是 JSON
    llm._session = s
    try:
        llm.chat_completion(msgs, overrides={"base_url": "http://fake.local/v1", "model": "m1",
                                             "fallback_models": []})
        ok("没有备选 + 坏响应 ⇒ 抛错", False)
    except llm.LLMError:
        ok("没有备选 + 坏响应 ⇒ 抛错（不会无限换）", len(s.calls) == 1)

    s = FakeSession([FakeResp(200, None), FakeResp(200, good_payload("m2"))])
    llm._session = s
    r = llm.chat_completion(msgs, overrides=api())
    ok("坏响应（无法解析 JSON）⇒ 也换备选", r.get("fallback", {}).get("used") == "m2" and len(s.calls) == 2)

    s = FakeSession([requests.exceptions.Timeout("read timed out"), FakeResp(200, good_payload("m2"))])
    llm._session = s
    r = llm.chat_completion(msgs, overrides=api())
    ok("真·超时异常（requests.Timeout）也换备选",
       r.get("fallback", {}).get("used") == "m2" and len(s.calls) == 2, len(s.calls))

    s = FakeSession([FakeResp(500, None, "x"), FakeResp(200, good_payload("m2"))])
    llm._session = s
    seen = []
    llm.set_usage_hook(lambda u: seen.append(u))
    try:
        llm.chat_completion(msgs, overrides=api())
    finally:
        llm.set_usage_hook(None)
    ok("走备选成功时用量钩子照样被调用（记账不漏）",
       len(seen) == 1 and (seen[0] or {}).get("total_tokens") == 12, seen)

    ok("备选使用情况落盘（控制台读得到）",
       llm.fallback_status()["count"] >= 1 and llm.fallback_status()["last"]["used"] == "m2",
       llm.fallback_status()["count"])
    ok("fallback_status 里的 models 就是配置里的清单",
       llm.fallback_status()["models"] == ["m2", "m3"], llm.fallback_status()["models"])

    print("== D. chat_completion_with_retry：同模型重试 + 再换备选 ==")
    s = FakeSession([FakeResp(500, None, "1"), FakeResp(500, None, "2"), FakeResp(200, good_payload("m1"))])
    llm._session = s
    r = llm.chat_completion_with_retry({"messages": msgs, "overrides": api()}, retries=2)
    ok("可重试错误先在同一模型上重试（3 次都是 m1）",
       [c["model"] for c in s.calls] == ["m1", "m1", "m1"] and "fallback" not in r, [c["model"] for c in s.calls])

    s = FakeSession([FakeResp(500, None, "1"), FakeResp(500, None, "2"), FakeResp(500, None, "3"),
                     FakeResp(200, good_payload("m2"))])
    llm._session = s
    r = llm.chat_completion_with_retry({"messages": msgs, "overrides": api()}, retries=2)
    ok("重试用尽 ⇒ 再逐个换备选（3 次主 + 1 次备选）",
       len(s.calls) == 4 and r["fallback"]["used"] == "m2", [c["model"] for c in s.calls])

    s = FakeSession([FakeResp(401, None, "Unauthorized")])
    llm._session = s
    try:
        llm.chat_completion_with_retry({"messages": msgs, "overrides": api()}, retries=2)
        ok("鉴权错不重试不换备选", False)
    except llm.LLMError:
        ok("鉴权错不重试不换备选（只打 1 次）", len(s.calls) == 1)

    s = FakeSession([FakeResp(400, None, "invalid temperature")])
    llm._session = s
    try:
        llm.chat_completion_with_retry({"messages": msgs, "overrides": api()}, retries=2)
        ok("参数错（400）不重试不换备选", False)
    except llm.LLMError:
        ok("参数错（400）不重试不换备选（换备选也一样错）", len(s.calls) == 1)

    s = FakeSession([FakeResp(500, None, "1"), FakeResp(200, good_payload("m2"))])
    llm._session = s
    r = llm.chat_completion_with_retry({"messages": msgs, "overrides": api()}, retries=0)
    ok("retries=0 ⇒ 不原地重试，直接换备选", [c["model"] for c in s.calls] == ["m1", "m2"])

    s = FakeSession([FakeResp(500, None, "1")])
    llm._session = s
    try:
        llm.chat_completion_with_retry({"messages": msgs, "overrides": {"base_url": "http://fake.local/v1",
                                                                       "model": "m1", "fallback_models": []}},
                                       retries=0)
        ok("没有备选时原样抛错", False)
    except llm.LLMError:
        ok("没有备选时原样抛错（阴性对照：效果确实来自备选清单）", len(s.calls) == 1)

    llm._session = real_session
    llm.effective_api = real_effective

    print("== E. 接线断言（源码级）==")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "agent", "llm.py"), encoding="utf-8").read()
    html = open(os.path.join(root, "agent", "console_html.py"), encoding="utf-8").read()
    wui = open(os.path.join(root, "agent", "webui.py"), encoding="utf-8").read()
    cfg = open(os.path.join(root, "agent", "config.py"), encoding="utf-8").read()
    cex = open(os.path.join(root, "config.example.json"), encoding="utf-8").read()
    ok("chat_completion 里真的走了候选链", "_post_once(api, model," in src and "for idx, model in enumerate(models)" in src)
    ok("重试函数接上了备选", 'kwargs["_models"] = rest' in src and "is_fallback_eligible(last_error)" in src)
    ok("控制台有备选模型输入行", 'data-cfg="api.fallback_models"' in html)
    ok("控制台有备选使用读数行", 'id="fallbackStat"' in html and "s.fallback" in html)
    ok("读数来自 /api/status 的 fallback 段", 'st["fallback"] = _fbs()' in wui)
    ok("配置默认段有 api.fallback_models", '"fallback_models": []' in cfg)
    ok("config.example.json 同步", '"fallback_models": []' in cex)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
