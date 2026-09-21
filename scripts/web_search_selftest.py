#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据：联网搜索（`agent/web_search.py`）——**全离线**：把 `requests` 整个替身掉，一条真请求都不发。

为什么要有它（第九轮审计 **V-R9-31**）：这个模块原来**没有行为判据**
（唯一真调用在 `persona_model_selftest` 的 `--live` 分支里，标准套件从不执行）
⇒ `sanitize_query` 的长度上限 / 请求超时 / 无结果回退 / 引擎顺序 / 响应体上限**零断言**。
本轮修的是 V-R9-26（读完才截断 ⇒ 实测收完 200MB、峰值 601MB）与 V-R9-27（超时是"每 recv"）。

  ① `sanitize_query` 的长度上限与清洗（CQ 码 / NUL）
  ② 每个请求都带超时，且读取有**整轮墙钟预算**
  ③ 无结果时的回退（google 解析不到 ⇒ 退 bing；bing 没结果 ⇒ 如实回空，不是崩溃）
  ④ 引擎顺序（provider 指定谁就用谁；默认 google 优先、失败退 bing）
  ⑤ 响应体上限（超限 ⇒ 拒，且**没把整包收进内存**：按拉取字节数核）

跑法：py -3 scripts\\web_search_selftest.py      退出码 0=全过 / 1=有失败
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import web_search as WS                    # noqa: E402
from agent import safe_fetch as SF                    # noqa: E402

PASS, FAIL = 0, 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


class _FakeResp:
    """最小 requests 替身响应：只实现产品真正用到的那几个属性。"""

    def __init__(self, status=200, body: bytes = b"", encoding: str = "utf-8"):
        self.status_code = status
        self.encoding = encoding
        self._body = body
        self.pulled = 0
        self.closed = False

    def iter_content(self, chunk_size=65536):
        n = int(chunk_size or 65536)
        for i in range(0, len(self._body), n):
            c = self._body[i:i + n]
            self.pulled += len(c)
            yield c

    def close(self):
        self.closed = True


class _FakeRequests:
    """把 (method, url) 交给 route 决定回什么；把每次调用的参数记下来给判据看。"""

    def __init__(self, route):
        self.route = route
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(("get", str(url), kw))
        return self.route("get", str(url), kw)

    def post(self, url, **kw):
        self.calls.append(("post", str(url), kw))
        return self.route("post", str(url), kw)

    def urls(self, method=None):
        return [c[1] for c in self.calls if method is None or c[0] == method]


BING_HTML = ('<html><li class="b_algo"><a href="https://example.com/b">'
             '<h2>Bing 标题</h2></a><p>Bing 摘要</p></li></html>').encode("utf-8")
BING_EMPTY = b"<html><body>nothing here</body></html>"
GOOGLE_HTML = ('<html><div class="MjjYud"><a href="https://example.com/g">'
               '<h3>Google 标题</h3></a><div class="VwiC3b">Google 摘要</div></div></html>').encode("utf-8")
GOOGLE_EMPTY = b"<html><body>no results markup</body></html>"


def _cfg(**ws):
    base = {"max_results": 6, "timeout_ms": 20000}
    base.update(ws)
    return lambda: {"web_search": dict(base)}


def main() -> int:
    real_requests = WS.requests
    real_get_config = WS.get_config
    real_env = os.environ.pop("DEEPSEEK_API_KEY", None)

    print("== ① sanitize_query：长度上限与清洗 ==")
    ok("① -1 200 个字符 ⇒ 截到 ≤120", len(WS.sanitize_query("啊" * 200)) <= 120,
       str(len(WS.sanitize_query("啊" * 200))))
    ok("① -2 去掉 CQ 码（不让 [CQ:…] 进查询词）", "[CQ:" not in WS.sanitize_query("[CQ:at,qq=1] 你好")
       and "你好" in WS.sanitize_query("[CQ:at,qq=1] 你好"))
    ok("① -3 去掉 NUL（URL 里会截断参数）", "\x00" not in WS.sanitize_query("a\x00b"))
    ok("① -4 空/None ⇒ 空串", WS.sanitize_query(None) == "" and WS.sanitize_query("   ") == "")
    ok("① -5 首尾空白清掉", WS.sanitize_query("  猫  ") == "猫")

    try:
        print("== ② 请求带超时 + 读取有整轮墙钟预算 ==")
        resp_bing = _FakeResp(body=BING_HTML)

        def route_ok(method, url, kw):
            if "bing.com" in url:
                return resp_bing
            if "deepseek" in url:
                return _FakeResp(body=b'{"output":[]}')          # 无 key 分支之外的兜底：没文本 ⇒ 回退
            raise AssertionError("判据不该请求这个地址：%s" % url)

        WS.requests = _FakeRequests(route_ok)
        WS.get_config = _cfg()
        r = WS.bing_search("猫")
        calls = WS.requests.calls
        ok("② -1 请求确实发出去了（阴性对照）", len(calls) == 1, str(calls))
        _kw = calls[0][2] if calls else {}
        _to = _kw.get("timeout")
        ok("② -2 带 timeout，且是 (连接, 读取) 二元组（不是单个数字）",
           isinstance(_to, tuple) and len(_to) == 2 and _to[0] > 0 and _to[1] > 0, str(_to))
        ok("② -3 stream=True（否则 requests 会把整包先收进内存）", _kw.get("stream") is True, str(_kw.get("stream")))
        ok("② -4 正常解析出 1 条结果", len(r.get("results") or []) == 1
           and r["results"][0]["title"] == "Bing 标题", str(r.get("results"))[:80])
        ok("② -5 声明了整轮墙钟预算（WALL_S 有限且 ≤30 秒）",
           isinstance(WS.WALL_S, (int, float)) and 0 < WS.WALL_S <= 30, str(WS.WALL_S))
        # 墙钟语义：把"开始时刻"挪到预算之前 ⇒ 下一块就应当中止（不用真等 20 秒）
        try:
            WS.read_stream(_FakeResp(body=b"x" * 100), 1024 * 1024, budget_s=1.0,
                           t0=time.monotonic() - 60, what="判据用回包")
            _timed = False
        except TimeoutError:
            _timed = True
        ok("② -6 整轮超预算 ⇒ 中止（不是「每 recv 重置」）", _timed)

        print("== ③ 无结果时的回退 ==")

        def route_google_empty(method, url, kw):
            if "deepseek" in url:
                return _FakeResp(body=b'{"output":[]}')
            if "google.com" in url:
                return _FakeResp(body=GOOGLE_EMPTY)               # 解析不到 ⇒ 抛 ⇒ 该回退
            if "bing.com" in url:
                return _FakeResp(body=BING_HTML)
            raise AssertionError(url)

        WS.requests = _FakeRequests(route_google_empty)
        WS.get_config = _cfg()
        r = WS.web_search("猫")
        urls = WS.requests.urls()
        ok("③ -1 先试 google、解析不到再退 bing",
           any("google.com" in u for u in urls) and any("bing.com" in u for u in urls)
           and urls.index([u for u in urls if "google.com" in u][0]) < urls.index([u for u in urls if "bing.com" in u][0]),
           str([u[:28] for u in urls]))
        ok("③ -2 回退之后拿到了 bing 的结果", (r.get("results") or [{}])[0].get("title") == "Bing 标题",
           str(r.get("results"))[:70])

        def route_all_empty(method, url, kw):
            if "deepseek" in url:
                return _FakeResp(body=b'{"output":[]}')
            if "google.com" in url:
                return _FakeResp(body=GOOGLE_EMPTY)
            return _FakeResp(body=BING_EMPTY)

        WS.requests = _FakeRequests(route_all_empty)
        WS.get_config = _cfg()
        try:
            r = WS.web_search("猫")
            ok("③ -3 两个引擎都没结果 ⇒ 如实回空（不抛、不编造）", r.get("results") == [], str(r)[:70])
        except Exception as e:
            ok("③ -3 两个引擎都没结果 ⇒ 如实回空（不抛、不编造）", False, "%s: %s" % (type(e).__name__, e))

        print("== ④ 引擎顺序 ==")
        ZHIPU_JSON = ('{"search_result":[{"title":"智谱标题","link":"https://example.com/z",'
                      '"content":"智谱摘要"}]}').encode("utf-8")

        def route_zhipu(method, url, kw):
            if "bigmodel.cn" in url:
                return _FakeResp(body=ZHIPU_JSON)
            raise AssertionError("provider=zhipu 时不该请求别家：%s" % url)

        WS.requests = _FakeRequests(route_zhipu)
        WS.get_config = _cfg(provider="zhipu", zhipu={"api_key": "ZK"})
        r = WS.web_search("猫")
        urls = WS.requests.urls()
        ok("④ -1 provider=zhipu ⇒ 只打智谱，不碰 google/bing",
           len(urls) == 1 and "bigmodel.cn" in urls[0] and r["results"][0]["title"] == "智谱标题",
           str([u[:30] for u in urls]))

        def route_google_ok(method, url, kw):
            if "deepseek" in url:
                return _FakeResp(body=b'{"output":[]}')
            if "google.com" in url:
                return _FakeResp(body=GOOGLE_HTML)
            raise AssertionError("google 成功时不该再退 bing：%s" % url)

        WS.requests = _FakeRequests(route_google_ok)
        WS.get_config = _cfg()
        r = WS.web_search("猫")
        ok("④ -2 默认 google 优先；google 成功就不退 bing",
           not any("bing.com" in u for u in WS.requests.urls()) and r["results"][0]["title"] == "Google 标题",
           str([u[:30] for u in WS.requests.urls()]))

        def route_no_google(method, url, kw):
            if "deepseek" in url:
                return _FakeResp(body=b'{"output":[]}')
            if "bing.com" in url:
                return _FakeResp(body=BING_HTML)
            raise AssertionError("google_first=False 时不该打 google：%s" % url)

        WS.requests = _FakeRequests(route_no_google)
        WS.get_config = _cfg(google_first=False)
        r = WS.web_search("猫")
        ok("④ -3 google_first=False ⇒ 直接 bing", not any("google.com" in u for u in WS.requests.urls())
           and r["results"][0]["title"] == "Bing 标题", str([u[:30] for u in WS.requests.urls()]))

        def route_custom(method, url, kw):
            if "my-search.local" in url:
                _b = ('{"results":[{"title":"自定义标题","url":"https://e.com/c","snippet":"s"}]}'
                      ).encode("utf-8")
                return _FakeResp(body=_b)
            raise AssertionError(url)

        WS.requests = _FakeRequests(route_custom)
        WS.get_config = _cfg(provider="custom", custom={"base_url": "https://my-search.local/api", "type": "openai"})
        r = WS.web_search("猫")
        ok("④ -4 provider=custom ⇒ 打自定义端点并用它的结果",
           r["results"][0]["title"] == "自定义标题"
           and any("my-search.local" in u for u in WS.requests.urls()), str(r["results"])[:60])
        _ckw = WS.requests.calls[0][2]
        ok("④ -5 自定义端点也带超时二元组（每条路径都守）",
           isinstance(_ckw.get("timeout"), tuple) and len(_ckw["timeout"]) == 2, str(_ckw.get("timeout")))

        print("== ⑤ 响应体上限（读完才截断 ⇒ 现在超限就拒）==")
        BIG_HTML = b"<html>" + b"a" * (8 * 1024 * 1024) + b"</html>"
        big_resp = _FakeResp(body=BIG_HTML)

        def route_big(method, url, kw):
            return big_resp

        WS.requests = _FakeRequests(route_big)
        WS.get_config = _cfg()
        try:
            WS.bing_search("猫")
            _raised, _why = False, ""
        except Exception as e:
            _raised, _why = True, "%s: %s" % (type(e).__name__, str(e)[:50])
        ok("⑤ -1 远端 8MB（上限 2MB）⇒ 判失败而不是收完再截", _raised, _why)
        cap = WS.HTML_MAX_BYTES
        ok("⑤ -2 只从上游拉了 ≤ 上限+一个块就中止（内存不随远端增长）",
           big_resp.pulled <= cap + 65536, "pulled=%.2fMB cap=%.2fMB" % (big_resp.pulled / 1048576.0, cap / 1048576.0))
        ok("⑤ -3 中止时把连接关掉（不把对端挂着）", big_resp.closed is True)

        BIG_JSON = b'{"search_result":[' + b'{"link":"https://e.com/x","title":"t","content":"' \
                   + b"b" * (8 * 1024 * 1024) + b'"}]}'
        json_resp = _FakeResp(body=BIG_JSON)
        WS.requests = _FakeRequests(lambda m, u, k: json_resp)
        WS.get_config = _cfg(provider="zhipu", zhipu={"api_key": "ZK"})
        try:
            WS.web_search("猫")
            _r2 = False
        except Exception:
            _r2 = True
        ok("⑤ -4 API 引擎的超大回包同样被拒", _r2 and json_resp.pulled <= WS.JSON_MAX_BYTES + 65536,
           "pulled=%.2fMB" % (json_resp.pulled / 1048576.0))
        ok("⑤ -5 两个上限都有明确取值（HTML 2MB / JSON 4MB）",
           WS.HTML_MAX_BYTES == 2 * 1024 * 1024 and WS.JSON_MAX_BYTES == 4 * 1024 * 1024,
           "%d / %d" % (WS.HTML_MAX_BYTES, WS.JSON_MAX_BYTES))
        ok("⑤ -6 web_fetch 走的仍是带闸门的 safe_fetch（不另写一套抓取）",
           WS.safe_fetch is SF.safe_fetch and WS.web_fetch is not None, str(WS.safe_fetch))
    finally:
        WS.requests = real_requests
        WS.get_config = real_get_config
        if real_env is not None:
            os.environ["DEEPSEEK_API_KEY"] = real_env

    print("\nweb_search 判据：%d 通过 / %d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
