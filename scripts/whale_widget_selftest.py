# -*- coding: utf-8 -*-
"""小鲸鱼挂件（移植版）判据（2026-09-19 立）。

起因：把上游 `dsh-whale-widget` 从 **0.2.10 升到 0.3.5** 时对账，发现两件必须守死的事 ——
  ① **价目表唯一来源**：仓里原本三份（`agent/whale.py` 挂件那份、`agent/llm.py::_OFFICIAL_PRICES`
     成本估算那份、`agent/stats.py` 注释那份）且互相打架 —— 挂件 pro 行是 4.5/13.5/0.15（Pro 保持
     Flash 3 倍价），而 llm.py 那份写的是 2/8/0.04 ⇒ **同一个 usage 在控制台会算出两个数**。
     现在统一到 `agent/model_prices.py`（照抄上游 0.3.5 lib/index.js L436-495）。
  ② **输出不许重复计费**：上游 0.3.5 修了 issue #89 / PR #83 —— `reasoningTokens ⊆ outputTokens`
     ⇒ 输出侧只能算一次；旧移植版写的是 `(completion + reasoning)`，**偏高约一倍**。
另外守：上游改价/改资产时这两条要能变红（把上游 0.3.5 的数字与文件名钉进判据）。

用法：`py -3 scripts\\whale_widget_selftest.py`
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import tempfile  # noqa: E402

from agent import llm as L             # noqa: E402
from agent import model_prices as P    # noqa: E402
from agent import whale as WH          # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [%s]" % detail if detail else ""))


def src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
        return f.read()


print("── A. 价目表唯一来源（不许再各写一份）──")
ok("挂件那份就是唯一来源（同一个对象，不是抄了一份）", WH.BASE_PRICE is P.BASE_PRICE and WH.PRO_PRICE is P.PRO_PRICE)
ok("llm.py 的 DeepSeek flash 行与唯一来源一致",
   L._OFFICIAL_PRICES["deepseek-flash"]["in"] == P.BASE_PRICE["miss"][0]
   and L._OFFICIAL_PRICES["deepseek-flash"]["out"] == P.BASE_PRICE["out"][0]
   and L._OFFICIAL_PRICES["deepseek-flash"]["cached"] == P.BASE_PRICE["hit"][0],
   str(L._OFFICIAL_PRICES["deepseek-flash"])[:90])
ok("llm.py 的 DeepSeek pro 行与唯一来源一致（**本次对账的那处偏差**）",
   L._OFFICIAL_PRICES["deepseek-v4-pro"]["in"] == P.PRO_PRICE["miss"][0]
   and L._OFFICIAL_PRICES["deepseek-v4-pro"]["out"] == P.PRO_PRICE["out"][0]
   and L._OFFICIAL_PRICES["deepseek-v4-pro"]["cached"] == P.PRO_PRICE["hit"][0],
   str(L._OFFICIAL_PRICES["deepseek-v4-pro"])[:90])
_llm_src, _wh_src = src("agent/llm.py"), src("agent/whale.py")
ok("llm.py 里不再手写 DeepSeek 单价数字（值都来自 model_prices）",
   "'deepseek-flash': { 'in': _DS_FLASH" in _llm_src and "'in': 1, 'out': 4, 'cached': 0.02" not in _llm_src)
ok("whale.py 里不再有价目表定义（只剩导入）",
   "BASE_PRICE = {" not in _wh_src and "from .model_prices import" in _wh_src)
ok("上游调价时改哪儿写在文件抬头（下一个维护者不用猜）",
   "BASE_PRICE" in src("agent/model_prices.py") and "上游调价时改这一个文件" in src("agent/model_prices.py"))

print("── B. 表值 == 上游 dsh-whale-widget@0.3.5（上游再涨价这里要红）──")
ok("Flash 谷/峰四档 = 上游 0.3.5", P.BASE_PRICE == {"hit": [0.02, 0.04], "miss": [1.0, 2.0], "out": [4.0, 8.0]},
   str(P.BASE_PRICE))
ok("Pro 谷/峰四档 = 上游 0.3.5（Pro 保持 3 倍价、计费不变）",
   P.PRO_PRICE == {"hit": [0.15, 0.3], "miss": [4.5, 9.0], "out": [13.5, 27.0]}, str(P.PRO_PRICE))
ok("时段表 = 工作日 9-12 / 14-18（北京时间）", P.PEAK_HOURS == ((9, 12), (14, 18)), str(P.PEAK_HOURS))
ok("周末全天谷价的生效分界在场（2026-08-23）", P.WEEKEND_VALLEY_FROM_SEC > 0)
ok("认得出现行两个正名与退役旧名",
   P.price_for("deepseek-flash") is P.BASE_PRICE and P.price_for("deepseek-v4-pro") is P.PRO_PRICE
   and P.price_for("deepseek-v4-flash-vision-exp") is P.BASE_PRICE)
ok("认不出的模型名按 Flash 档（与上游 _default 一致）", P.price_for("") is P.BASE_PRICE)

print("── C. 输出侧只算一次（上游 issue #89 / PR #83）──")
_u0 = {"prompt_tokens": 1000, "completion_tokens": 200, "reasoning_tokens": 0}
_u1 = {"prompt_tokens": 1000, "completion_tokens": 200, "reasoning_tokens": 150}
_c0, _t0 = P.cost_of(_u0, "deepseek-flash", 0)
_c1, _t1 = P.cost_of(_u1, "deepseek-flash", 0)
ok("completion 不变、只多报 reasoning ⇒ 成本**不变**（旧写法这里会翻倍）",
   abs(_c0 - _c1) < 1e-12, "%.8f vs %.8f" % (_c0, _c1))
ok("token 总数也不重复计（prompt + completion）", _t0 == _t1 == 1200, "%s / %s" % (_t0, _t1))
ok("reasoning 报在 completion 之外（异常 provider）⇒ 取较大值兜底，不静默少计",
   P.billable_output({"completion_tokens": 100, "reasoning_tokens": 400}) == 400)
ok("嵌套写法也认（completion_tokens_details.reasoning_tokens）",
   P.billable_output({"completion_tokens": 100, "completion_tokens_details": {"reasoning_tokens": 260}}) == 260)

print("── D. 两个显示口径必须同源（挂件「今日已用」 vs 统计 cost）──")
_usage_nested = {"prompt_tokens": 1000, "completion_tokens": 200, "reasoning_tokens": 120,
                 "prompt_tokens_details": {"cached_tokens": 800}}
_usage_flat = {"prompt_tokens": 1000, "completion_tokens": 200, "reasoning_tokens": 120,
               "cached_tokens": 800}
_tmp = tempfile.mkdtemp(prefix="pm_whale_judge_")
w = WH.WhaleWidget(_tmp)
_w_cost, _w_tok = w._usage_cost(_usage_nested, "deepseek-flash", 0)     # 0 = 谷时
_e_nested = L.estimate_cost(_usage_nested, "deepseek-flash")["cost"]
_e_flat = L.estimate_cost(_usage_flat, "deepseek-flash")["cost"]
ok("挂件口径 == 统计口径（同一 usage、谷时）", abs(_w_cost - _e_nested) < 1e-12,
   "%.8f vs %.8f" % (_w_cost, _e_nested))
ok("estimate_cost 现在**两种 usage 形态都认**（旧写法只读顶层 cached_tokens ⇒ 把缓存命中当未命中，成本虚高）",
   abs(_e_nested - _e_flat) < 1e-12, "%.8f vs %.8f" % (_e_nested, _e_flat))
ok("缓存命中确实按命中价算（0.8M 命中 + 0.2M 新输入 + 200 输出 = 0.001016 元）",
   abs(_w_cost - 0.001016) < 1e-9, "%.9f" % _w_cost)
ok("token 总数 = prompt + completion", _w_tok == 1200, str(_w_tok))

print("── E. 峰谷判定（含周末全天谷价）──")
import datetime as _d  # noqa: E402
_BJ = _d.timezone(_d.timedelta(hours=8))
# 日期**算出来**别手写星期几（手写容易错一天，判据就假红）：
# 以 2026-09-14 那周为基准取"工作日"与"周六"
_anchor = _d.datetime(2026, 9, 14, 10, 0, tzinfo=_BJ)
_wed = _anchor + _d.timedelta(days=(2 - _anchor.weekday()) % 7)          # 工作日
_sat = _anchor + _d.timedelta(days=(5 - _anchor.weekday()) % 7)          # 周末（分界之后）
_anchor2 = _d.datetime(2026, 8, 10, 10, 0, tzinfo=_BJ)
_sat_old = _anchor2 + _d.timedelta(days=(5 - _anchor2.weekday()) % 7)    # 周末（2026-08-23 分界之前）
ok("（判据前提）挑出来的那天确实是工作日", _wed.weekday() < 5, _wed.strftime("%Y-%m-%d"))
ok("（判据前提）挑出来的那天确实是周六", _sat.weekday() == 5 and _sat_old.weekday() == 5)
_peak = _wed.replace(hour=10).timestamp()
_valley = _wed.replace(hour=13).timestamp()
ok("工作日 10:00 ⇒ 高峰", P.is_peak_time(_peak) is True)
ok("工作日 13:00 ⇒ 谷时", P.is_peak_time(_valley) is False)
ok("周末 10:00 ⇒ 谷时（2026-08-23 起生效）",
   _sat.timestamp() > P.WEEKEND_VALLEY_FROM_SEC and P.is_peak_time(_sat.timestamp()) is False)
ok("分界之前的周末仍按峰谷（那时还没有周末谷价）",
   _sat_old.timestamp() < P.WEEKEND_VALLEY_FROM_SEC and P.is_peak_time(_sat_old.timestamp()) is True)
_cp, _ = P.cost_of(_usage_nested, "deepseek-flash", _peak)
_cv, _ = P.cost_of(_usage_nested, "deepseek-flash", _valley)
ok("高峰 = 谷时 × 2", abs(_cp - _cv * 2) < 1e-12, "%.8f / %.8f" % (_cp, _cv))
_old_flash = {"hit": [0.05, 0.1], "miss": [1.5, 3.0], "out": [4.5, 9.0]}
ok("Pro 的价 = **旧 Flash 价** ×3（2026-08-17 那条口径）",
   all(abs(P.PRO_PRICE[k][i] - _old_flash[k][i] * 3) < 1e-12 for k in ("hit", "miss", "out") for i in (0, 1)))
ok("⚠️「Pro = Flash ×3」**今天不成立**（Flash 2026-09-10 降价、Pro 没跟）"
   "—— 这条钉住它，免得以后有人照着旧口径改回去",
   abs(P.PRO_PRICE["miss"][0] / P.BASE_PRICE["miss"][0] - 4.5) < 1e-9
   and abs(P.PRO_PRICE["out"][0] / P.BASE_PRICE["out"][0] - 3.375) < 1e-9)
ok("Pro 档算出来比 Flash 档贵（方向没错）",
   P.cost_of(_usage_nested, "deepseek-v4-pro", 0)[0] > _cv * 3)

print("── F. 移植件在位（前端脚本 / 素材 / 接口 / 许可说明）──")
for rel in ("whale-widget/client/widget.js", "whale-widget/assets/DSniang1.png",
            "whale-widget/assets/rua.gif", "whale-widget/assets/Ya1.mp3",
            "whale-widget/assets/Ya2.mp3", "whale-widget/assets/D1.mp3",
            "whale-widget/assets/D2.mp3", "whale-widget/LICENSE-原版.txt"):
    ok("在：%s" % rel, os.path.exists(os.path.join(ROOT, rel)), "")
_ui = src("agent/webui.py")
for ep in ("/dsh-whale/balance.json", "/dsh-whale/size.json", "/dsh-whale/last-turn.json",
           "/dsh-whale/image.png", "/dsh-whale/rua.gif", "/dsh-whale/widget.js"):
    ok("接口在位：%s" % ep, ep in _ui, "")
ok("素材许可范围写明了（上游 PROVENANCE：代码 MIT、assets 不在 MIT 内）",
   "PROVENANCE" in src("agent/model_prices.py") or "assets" in src("agent/whale.py"))

print("── G. 移植副本：补丁必须重打、版本必须是新版（上游再发版时这里要红）──")
_cli_path = os.path.join(ROOT, "whale-widget", "client", "widget.js")
_cli = src("whale-widget/client/widget.js")
ok("前端脚本在位且是 0.3.x 那个体量（0.2.x 只有 53KB 左右）",
   os.path.exists(_cli_path) and len(_cli) > 400000, "%d 字节" % len(_cli))
ok("**唯一补丁**（群相移植补丁 v1）恰好出现一次",
   _cli.count("if (!r) r = document.body") == 1 and _cli.count("群相移植补丁 v1") >= 1,
   "补丁行 %d 处 / 标记 %d 处" % (_cli.count("if (!r) r = document.body"), _cli.count("群相移植补丁 v1")))
ok("补丁打在自检函数里（不是别处乱改）",
   "function dshwIsChatRoot(r) {" in _cli
   and _cli.index("群相移植补丁 v1") > _cli.index("function dshwIsChatRoot(r) {"))
ok("上游的页面自检逻辑还在（只放宽页面根，没把自检删掉）",
   "'[contenteditable=\"true\"]'" in _cli and "dshwTryStart(true)" in _cli)
ok("是 0.3.x 的界面（带 0.3 的菜单文案与新版类名）",
   "自定义泡泡" in _cli and "避让滚动条" in _cli and _cli.count("dshwv-") > 500,
   "dshwv- × %d" % _cli.count("dshwv-"))
_pn = src("whale-widget/PORT-NOTES.md")
ok("移植记录在位（上游版本 / 补丁 / 接口覆盖 / 许可 / 重 vendor 步骤）",
   all(k in _pn for k in ("0.3.5", "群相移植补丁 v1", "接口覆盖", "PROVENANCE", "重新 vendor")))
ok("移植记录写明素材许可与「已获原作者同意」（继续 vendor 的前提）", "已获原作者同意" in _pn)
_up = os.path.join(ROOT, "whale-widget", "upstream-0.3.5")
ok("上游原文留档在位（README / PROVENANCE / package.json）",
   all(os.path.exists(os.path.join(_up, f2)) for f2 in ("README-原版.md", "PROVENANCE-原版.md", "package.json")))
try:
    import json as _json
    _pk = _json.load(open(os.path.join(_up, "package.json"), encoding="utf-8"))
    ok("留档那份 package.json 版本 = 0.3.5（与 PORT-NOTES 对得上）", _pk.get("version") == "0.3.5",
       str(_pk.get("version")))
except Exception as e:
    ok("留档那份 package.json 版本 = 0.3.5", False, str(e)[:60])
_wh = src("agent/whale.py")
ok("新端点两种处理都在：能落地的真存（bubble/audio）+ 其余如实说不支持",
   "def save_cfg" in _wh and "def unsupported" in _wh and "_UNSUPPORTED" in _wh)
ok("不许回假 ok:true（那会让界面显示假数据）", '"unsupported": True' in _wh and '"ok": False' in _wh)
_ui2 = src("agent/webui.py")
ok("webui 两处都接上了（GET 分发 + POST 保存）",
   "whale.cfg_payload" in _ui2 and "whale.save_cfg" in _ui2 and "whale.unsupported" in _ui2)

print("\n小鲸鱼挂件判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
