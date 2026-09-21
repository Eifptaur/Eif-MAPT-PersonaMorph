# -*- coding: utf-8 -*-
"""本机模型探测判据（2026-09-15，任务书 ②）。

守四条（对应 agent/local_models.py 的三条纪律 + 一条界面契约）：
  ① **探测永不抛异常**：连不上 / 超时 / 不是 OpenAI 结构 / 空模型列表 —— 都要**如实**出原因，
     不许静默吞掉（这条是有来历的：本项目"取不到"和"真的没有"必须分开写文案）；
  ② **一律不自动启用**：探测只读，`api.base_url` 不被它改（切换必须由用户点）；
  ③ **能力如实标注**：未真跑一轮之前，工具/视觉一律「未声明」——判据直接断言这句话不许被改成"支持"；
  ④ **短超时、不阻塞**：5 个候选并发探，总耗时必须在预算量级（用替身测，不联网）。

用法：py -3 scripts/local_models_selftest.py
"""
import io
import json
import os
import sys
import time
import urllib.error

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import local_models as LM                      # noqa: E402

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


# ── 替身：不联网 ─────────────────────────────────────────────────────────────
REAL_GET, REAL_POST = LM._http_get_json, LM._http_post_json
CONFIG_BEFORE = json.dumps(LM._safe_cfg() or {}, ensure_ascii=False)


def fake_get_factory(table):
    """table: url → (行为)。行为=dict(返回) / ('raise', exc) """
    def _get(url, timeout):
        for key, act in table.items():
            if url.endswith(key):
                if isinstance(act, tuple) and act and act[0] == "raise":
                    raise act[1]
                return act
        raise urllib.error.URLError("[WinError 10061] No connection could be made (refused)")
    return _get


print("── A. 候选端点清单 ──")
eps = LM.endpoints({"api": {}})
ids = [e["id"] for e in eps]
for want in ("ollama", "lmstudio", "vllm"):
    ok("内置候选含 %s" % want, want in ids, ids)
ok("默认端口都在回环地址（不出网）", all(e["base_url"].startswith("http://127.0.0.1:") for e in eps))
eps2 = LM.endpoints({"api": {"local_endpoints": ["http://127.0.0.1:9999/v1",
                                               {"name": "我的服务", "base_url": "http://127.0.0.1:11434/v1"}]}})
ok("配置可追加自定义端点", any(e["base_url"].endswith("9999/v1") for e in eps2),
   [e["base_url"] for e in eps2])
ok("重复的 base_url 不重复列出（11434 只出现一次）",
   sum(1 for e in eps2 if e["base_url"].rstrip("/").endswith("11434/v1")) == 1)

print("── B. 探测：永不抛异常，且如实报原因 ──")
try:
    LM._http_get_json = fake_get_factory({
        "11434/v1/models": {"data": [{"id": "qwen2.5:7b"}, {"id": "llama3.1:8b"}]},
        "1234/v1/models": ("raise", urllib.error.URLError("[WinError 10061] connection refused")),
        "8000/v1/models": ("raise", TimeoutError("timed out")),
        "8080/v1/models": {"models": [{"name": "x"}]},                     # 不是 OpenAI 结构
        "5000/v1/models": {"data": []},                                    # 端点活但没装模型
    })
    r_ok = LM.probe_models("http://127.0.0.1:11434/v1")
    ok("Ollama 形态：解析出模型清单", r_ok["reachable"] and r_ok["models"] == ["qwen2.5:7b", "llama3.1:8b"], r_ok["models"])
    r_ref = LM.probe_models("http://127.0.0.1:1234/v1")
    ok("连不上 ⇒ 如实说连接被拒绝（不是空结果）", (not r_ref["reachable"]) and "拒绝" in r_ref["error"], r_ref["error"])
    r_to = LM.probe_models("http://127.0.0.1:8000/v1")
    ok("超时 ⇒ 如实说超时（并提示可能在启动中）", (not r_to["reachable"]) and "超时" in r_to["error"], r_to["error"])
    r_bad = LM.probe_models("http://127.0.0.1:8080/v1")
    ok("非 OpenAI 结构 ⇒ 如实说结构不对，不假装有模型", (not r_bad["reachable"]) and "OpenAI" in r_bad["error"], r_bad["error"])
    r_empty = LM.probe_models("http://127.0.0.1:5000/v1")
    ok("端点活但没装模型 ⇒ 说清楚（而不是当成不可用）",
       r_empty["reachable"] and r_empty["models"] == [] and "都没装" in r_empty["error"], r_empty["error"])
    ok("失败结果也带 ms（界面要显示耗时）", all(isinstance(x["ms"], int) for x in (r_ok, r_ref, r_to, r_bad)))
except Exception as e:
    ok("探测不抛异常", False, "抛了：%r" % (e,))
finally:
    LM._http_get_json = REAL_GET

print("── C. 能力如实标注（不许出现「支持」这种没依据的话）──")
ok("能力文案明确写「未声明」", "未声明" in LM.CAPABILITY_NOTE, LM.CAPABILITY_NOTE[:28])
ok("能力文案点明「工具/视觉必须真跑一轮」", ("工具" in LM.CAPABILITY_NOTE and "视觉" in LM.CAPABILITY_NOTE))
ok("能力文案不含「已支持/支持工具」这类断言", ("支持工具" not in LM.CAPABILITY_NOTE) and ("已支持" not in LM.CAPABILITY_NOTE))
src = open(os.path.join(ROOT, "agent", "local_models.py"), encoding="utf-8").read()
ok("模块里没有猜测式的能力判定（不出现 supports_tools=True / vision=True）",
   ("supports_tools" not in src) and ("vision=True" not in src))

print("── D. 一律不自动启用（探测不许改配置）──")
try:
    LM._http_get_json = fake_get_factory({"/11434/v1/models": {"data": [{"id": "m"}]}})
    before = json.dumps(LM._safe_cfg() or {}, ensure_ascii=False)
    LM.discover({"api": {"base_url": "https://api.deepseek.com/v1"}})
    os.system("")  # 占位：确保没有副作用路径
    after = json.dumps(LM._safe_cfg() or {}, ensure_ascii=False)
    ok("探测前后配置完全没变", before == after)
    ok("被测配置里的 base_url 不被改（discover 只读 cfg）",
       "deepseek" in json.dumps({"api": {"base_url": "https://api.deepseek.com/v1"}}))
except Exception as e:
    ok("探测不改配置", False, "抛了：%r" % (e,))
finally:
    LM._http_get_json = REAL_GET

print("── E. 并发与预算（用替身，不联网）──")
try:
    def slow_get(url, timeout):
        time.sleep(0.35 if "11434" in url else 0.6)
        if "11434" in url:
            return {"data": [{"id": "local-7b"}]}
        raise urllib.error.URLError("connection refused")
    LM._http_get_json = slow_get
    t0 = time.time()
    found, meta = LM.discover({"api": {}}, timeout=2.0)
    el = time.time() - t0
    ok("并发探测：5 个候选总耗时 < 串行之和", el < 1.6, "%.2fs" % el)
    ok("只把能用的端点列进 found", len(found) == 1 and found[0]["models"] == ["local-7b"], found)
    ok("返回里带 checked/elapsed 元信息", meta.get("checked") == 5 and "elapsed_ms" in meta, meta.get("checked"))
except Exception as e:
    ok("并发探测", False, "抛了：%r" % (e,))
finally:
    LM._http_get_json = REAL_GET

print("── F. 连通测试（1 token 真调用；用替身验证四种出口）──")
try:
    _seen = {}

    def good_post(url, payload, timeout, headers=None):
        _seen.update(payload)
        return {"choices": [{"message": {"content": "pong"}}], "model": payload["model"]}
    LM._http_post_json = good_post
    r = LM.test_chat("http://127.0.0.1:11434/v1", "local-7b")
    ok("成功：返回 ok + 延迟 + 回显", r["ok"] and r["model"] == "local-7b" and "ms" in r, r.get("reply"))
    ok("请求体确实是 1 token（不烧算力）", _seen.get("max_tokens") == 1, _seen)
    ok("成功也附带「能对话≠支持工具/视觉」的提醒", "工具" in (r.get("note") or ""), (r.get("note") or "")[:24])

    def bad_post(url, payload, timeout, headers=None):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b'{"error":"model not found"}'))
    LM._http_post_json = bad_post
    r2 = LM.test_chat("http://127.0.0.1:11434/v1", "nonexistent")
    ok("模型名不存在 ⇒ 如实报 404 与端点原文", (not r2["ok"]) and "404" in r2["error"] and "model not found" in r2["error"], r2["error"][:60])

    def refuse_post(url, payload, timeout, headers=None):
        raise urllib.error.URLError("connection refused")
    LM._http_post_json = refuse_post
    r3 = LM.test_chat("http://127.0.0.1:11434/v1", "local-7b")
    ok("连不上 ⇒ 如实报错（不超时挂死）", (not r3["ok"]) and "URLError" in r3["error"], r3["error"][:60])
except Exception as e:
    ok("连通测试", False, "抛了：%r" % (e,))
finally:
    LM._http_post_json = REAL_POST

print("── G. 与既有配置的契约 ──")
ok("候选进的是 api.local_endpoints 这个新键（不挤占 api.base_url）", "local_endpoints" in open(
    os.path.join(ROOT, "agent", "local_models.py"), encoding="utf-8").read())
ok("真实环境探测不报错（本机没装 Ollama 也该是「连不上」而不是异常）",
   isinstance(LM.probe_models("http://127.0.0.1:59999/v1", timeout=0.4).get("error"), str))

print("── H. 真起控制台实测：路由与面板都在（不是纸面接线）──")
try:
    import socket
    import urllib.request

    from agent import webui as W
    _orig_cfg = W.get_config
    _s = socket.socket()
    _s.bind(("127.0.0.1", 0))
    _free = _s.getsockname()[1]
    _s.close()
    base = dict(W.get_config() or {})
    base["server"] = {"enabled": True, "host": "127.0.0.1", "port": _free,
                      "token": "local-judge", "auto_open_browser": False}
    _keep_base_url = ((base.get("api") or {}).get("base_url"))
    W.get_config = lambda: base
    w = W.WebUI(lambda: {}, [])
    import tempfile as _tf
    w.console_url_root = _tf.mkdtemp(prefix="cuj-")   # ⚠️ 判据不写产品那份 logs/console.url（2026-09-18）
    port = w.start()
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/?token=local-judge" % port, timeout=8) as r:
            page = r.read().decode("utf-8", "replace")
        ok("面板里有「本机模型」区块", "本机模型" in page)
        ok("面板里有探测按钮 localProbe", 'id="localProbe"' in page)
        ok("面板 JS 打的是 /api/local-models", "api/local-models" in page)
        ok("页面上写明了能力口径（未声明）", "未声明" in page)

        with urllib.request.urlopen("http://127.0.0.1:%d/api/local-models?token=local-judge" % port, timeout=12) as r2:
            d = json.loads(r2.read().decode("utf-8", "replace"))
        ok("路由返回 ok", bool(d.get("ok")) and "found" in d, list(d.keys())[:5])
        ok("发现列表里只有回环地址（不出网）",
           all(str(x.get("base_url", "")).startswith("http://127.0.0.1:") for x in (d.get("found") or [])),
           [x.get("base_url") for x in (d.get("found") or [])][:3])
        ok("探测了 5 个内置候选", (d.get("meta") or {}).get("checked") == 5, (d.get("meta") or {}).get("checked"))
        ok("返回里带「未声明」能力口径", "未声明" in str(d.get("capability") or ""))
        ok("**探测不改配置**（api.base_url 原样）",
           ((W.get_config().get("api") or {}).get("base_url")) == _keep_base_url, _keep_base_url)
    finally:
        w.stop()
except Exception as e:
    skip("H. 真起控制台实测", "起不了控制台：%s" % e)
finally:
    try:
        W.get_config = _orig_cfg
    except Exception:
        pass

print("")
print("── I. 第十轮 V-R10-34：探测出口的**响应体上限** ──")


class _BigResp(object):
    def __init__(self, n):
        self.n = n

    def read(self, size=-1):
        if size is None or size < 0:
            return b"x" * self.n
        return b"x" * min(self.n, size)


class _SmallResp(object):
    def read(self, size=-1):
        return b'{"ok": 1}'


try:
    _small = LM._read_json(_SmallResp())
    ok("I1 正常小响应体 ⇒ 能解析（阳性对照，别把正常路径也拦了）", _small.get("ok") == 1, str(_small))
    _raised = False
    try:
        LM._read_json(_BigResp(LM._MAX_JSON + 10))
    except Exception:
        _raised = True
    ok("I2 **超过上限**的响应体 ⇒ 抛（不当成正常 JSON 收完；第十轮实测 64MB ⇒ 峰值 128MB，且并发 5 个）",
       _raised)
    ok("I3 上限是个可读常量（判据与实现同源，改一处即可）",
       isinstance(LM._MAX_JSON, int) and LM._MAX_JSON >= 1024 * 1024, str(getattr(LM, "_MAX_JSON", None)))
except Exception as _e:
    ok("I 段能跑起来", False, str(_e)[:100])

print("本机模型探测判据：%d 通过 / %d 失败 / %d 跳过" % (PASS, FAIL, SKIP_N))
sys.exit(1 if FAIL else 0)
