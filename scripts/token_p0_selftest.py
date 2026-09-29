# -*- coding: utf-8 -*-
"""降 Token **P0 三条**判据（报告第 5 章「立即做、低难度高确定性」那三条）。

  · **P0-1** 修的 `web_search` provider 分支：配了 bing/google 就**不许**再偷发 DeepSeek 联网调用
    （那是纯隐藏开销：新会话无前缀可命中缓存 ⇒ 每次 ≈8.8k token 全按 fresh 全价）。
  · **P0-2** 发送参数校验前置：参数本身就错 ⇒ **在碰界面之前**挡下，且注明"这一类不用重发"
    （否则失败回执回到模型 ⇒ 整轮 9-15k 重发）。
  · **P0-3** 记忆整理（后台任务）**谷段优先 + 便宜档**，且 `overrides` 必须是**整份 api 配置**
    （实现是 `api = overrides or effective_api()`，传部分字典会报"Base URL 未配置"）。

跑法：`runtime\\python\\python.exe scripts\\token_p0_selftest.py`
"""
import datetime
import io
import os
import sys
import types

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


print("── P0-1 隐藏联网调用 ──")
from agent import web_search as W # noqa: E402

_calls = []
_real = {k: getattr(W, k) for k in ("_deepseek_search", "google_search", "bing_search")}
try:
    W._deepseek_search = lambda q: (_calls.append(("deepseek", q)), {"query": q, "results": []})[1]
    W.google_search = lambda q: (_calls.append(("google", q)), {"query": q, "results": []})[1]
    W.bing_search = lambda q: (_calls.append(("bing", q)), {"query": q, "results": []})[1]
    from agent.config import get_config
    _cfg = get_config()
    _old = (_cfg.get("web_search") or {}).copy()

    def _set(prov):
        _calls.clear()
        _cfg.setdefault("web_search", {})["provider"] = prov
        try:
            W.web_search("测试词")
        except Exception as e: # noqa: BLE001
            print("   （调用抛错，不影响断言：%s）" % str(e)[:60])

    _set("bing")
    ok("P0-1a ★ provider=bing ⇒ **不再**偷发 DeepSeek 调用（只走免费引擎）",
       not any(c[0] == "deepseek" for c in _calls) and _calls, str(_calls))
    _set("google")
    ok("P0-1b provider=google ⇒ 同样不偷发 DeepSeek", not any(c[0] == "deepseek" for c in _calls), str(_calls))
    _set("")   # ⚠️ 代码里 `provider or "bing"` 会把空串折成 bing ⇒ 默认也是免费引擎（有意为之）
    ok("P0-1c provider 留空 ⇒ 折成 bing（免费引擎），**同样不偷发** DeepSeek",
       not any(c[0] == "deepseek" for c in _calls) and _calls, str(_calls))
    _set("deepseek")
    ok("P0-1d 显式选 deepseek ⇒ 用它", any(c[0] == "deepseek" for c in _calls), str(_calls))
finally:
    for k, v in _real.items():
        setattr(W, k, v)
    try:
        _cfg["web_search"]["provider"] = _old.get("provider", "bing")
    except Exception as e: # noqa: BLE001
        print("   （恢复 provider 失败，仅影响本进程：%s）" % str(e)[:50])

print("\n── P0-2 发送参数校验前置 ──")
from agent import tools as T # noqa: E402

_v = T._validate_send_args
ok("P0-2a 空 messages ⇒ 挡下并给原因", "至少" in _v([], {}), _v([], {})[:40])
ok("P0-2b 空白内容 ⇒ 指出第几条", _sm.has(_v(["   "], {}), "第 1 条"), _v(["   "], {})[:40])
ok("P0-2c 超长（>4000 字）⇒ 挡下并给建议",
   "太长" in _v(["字" * 4001], {}), _v(["字" * 4001], {})[:30])
ok("P0-2d 条数上限 20 条", _sm.has(_v(["a"] * 21, {}), "20 条"), _v(["a"] * 21, {})[:30])
ok("P0-2e 空的 at_user_id / reply_to_message_id（形状错）⇒ 挡下",
   "at_user_id" in _v(["hi"], {"at_user_id": "  "})
   and "reply_to_message_id" in _v(["hi"], {"reply_to_message_id": ""}))
ok("P0-2f 正常参数 ⇒ 放行（不误伤）", _v(["你好"], {"at_user_id": "wxid_x"}) == "")

# 集成锚：参数不合格时**绝不能碰发送层**（拿桩把发送层设成"一碰就炸"）
class _Boom:
    def send_text_batch(self, *a, **k):
        raise AssertionError("参数不合格时不许碰发送层！")

_ctx = {"chat_key": "k", "store": None, "sender": _Boom(), "session": {"sent": []}}
_ctx["store"] = types.SimpleNamespace(find_by_mid=lambda *a, **k: None)
# 用"能过 normalize、但不合格"的输入（空 at_user_id）——空白内容会在 normalize 阶段就被判空，
# 走不到校验器（那是既有行为，不是本判据要验的东西）
_r = T._exec_send_message(_ctx, {"messages": ["你好"], "at_user_id": "   "})
_rt = str(getattr(_r, "content", _r))
ok("P0-2g ★ 参数不合格 ⇒ 在碰界面前返回**确定性错误**（且注明「不用重发」）",
   "参数不合格" in _rt and "不用重发" in _rt, _rt[:80])
ok("P0-2h 反向锚：校验器自己出错时**不拦**（绝不把发送挡死）",
   _v is not None and _sm.has(io.open(os.path.join(ROOT, "agent", "tools.py"), encoding="utf-8").read(),
                              "按不拦处理"))

print("\n── P0-3 记忆整理：谷段优先 + 便宜档 ──")
from agent import offpeak as O # noqa: E402

ok("P0-3a 默认窗口＝DeepSeek 官方谷段 00:30–08:30",
   O.parse_window(O.DEFAULT_WINDOW) == (30, 510) and "00:30" in O.describe())
ok("P0-3b 边界：08:29 算谷段、08:30 不算",
   O.in_off_peak(datetime.datetime(2026, 9, 29, 8, 29)) and not O.in_off_peak(datetime.datetime(2026, 9, 29, 8, 30)))
ok("P0-3c 跨零点窗口（22:00-06:00）也算对",
   O.in_off_peak(datetime.datetime(2026, 9, 29, 23, 0), "22:00-06:00")
   and O.in_off_peak(datetime.datetime(2026, 9, 29, 3, 0), "22:00-06:00")
   and not O.in_off_peak(datetime.datetime(2026, 9, 29, 12, 0), "22:00-06:00"))
ok("P0-3d 窗口写错 ⇒ 保守按峰段（不猜）",
   not O.in_off_peak(datetime.datetime(2026, 9, 29, 3, 0), "乱写"))
from agent.config import DEFAULT_CONFIG as _D # noqa: E402

ok("P0-3e 配置键齐（模型/开关/窗口/兜底小时）",
   all(k in (_D.get("memory") or {}) for k in ("consolidate_model", "consolidate_off_peak",
                                              "consolidate_off_peak_window",
                                              "consolidate_force_after_hours")))

# 行为锚：`_background_api()` 必须返回**整份** api（含 base_url/api_key），并换掉 model
import importlib.util as _ilu # noqa: E402

_spec = _ilu.spec_from_file_location("pm_entry", os.path.join(ROOT, "scripts", "persona_morph.py"))
_pm = _ilu.module_from_spec(_spec)
try:
    _spec.loader.exec_module(_pm)
    _cls = next(_c for _c in vars(_pm).values()
                if isinstance(_c, type) and hasattr(_c, "_background_api"))
    import agent.llm as _llm
    _real_api, _real_cfg = _llm.effective_api, _pm.get_config
    try:
        _llm.effective_api = lambda *a, **k: {"base_url": "http://x/v1", "api_key": "k",
                                             "model": "main-model"}
        _pm.get_config = lambda: {"memory": {"consolidate_model": "cheap-flash"}}
        _api = _cls._background_api(types.SimpleNamespace())
        ok("P0-3f ★ 后台档位＝**整份** api 且 model 换成便宜档（传部分字典会报 Base URL 未配置）",
           isinstance(_api, dict) and _api.get("model") == "cheap-flash"
           and _api.get("base_url") and _api.get("api_key"), str(_api)[:80])
        _pm.get_config = lambda: {"memory": {"consolidate_model": ""}}
        _api2 = _cls._background_api(types.SimpleNamespace())
        ok("P0-3g 没配便宜档 ⇒ 与主模型一致（零行为变化）",
           isinstance(_api2, dict) and _api2.get("model") == "main-model", str(_api2)[:60])
    finally:
        _llm.effective_api, _pm.get_config = _real_api, _real_cfg
except SystemExit as e: # noqa: PERF203
    ok("P0-3f/g 入口脚本可导入（否则跳过这两条）", False, "SystemExit %s" % e)
except Exception as e: # noqa: BLE001
    print("   （入口脚本导入失败，跳过 f/g：%s）" % str(e)[:80])

_pm_src = io.open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
ok("P0-3h 谷段门挂在 `_maybe_consolidate` 里，且**有超时兜底**（防白天永远不整理）",
   _sm.has(_pm_src, "记忆整理延后到谷段") and _sm.has(_pm_src, "consolidate_force_after_hours"))

print("\nToken P0 判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
