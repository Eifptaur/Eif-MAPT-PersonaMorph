# -*- coding: utf-8 -*-
"""模型清单与价目一致性判据（2026-09-14 · 用户口径「牵一发动全身，一改全改」）。

用户原话：「还有模型的更新，同步一下。现在都出到 V41 flash 了，模型栏里面没有。
牵一发动全身，一改全改啊。你要看好它联系了哪些东西，**这是最高准则**。
要是它会影响到别的东西的正常运作，就要把那个也修好」。

第一手事实（2026-09-14 现场取证，不是转述）：
  · 本机 API `GET /v1/models` → 只有 `deepseek-flash` / `deepseek-v4-pro` 两个名字；
  · 官方 Models & Pricing 页 → `deepseek-flash` = DeepSeek-V4.1-Flash（**支持视觉**、1M 上下文）、
    `deepseek-v4-pro` = V4-Pro-0813（不支持视觉）；旧名 `deepseek-v4-flash` /
    `deepseek-v4-flash-vision-exp` **仍接受但已退役，由 V4.1-Flash 服务、按 Flash 价计费**；
  · 本机实测：`deepseek-v4-flash-vision-exp` 与 `deepseek-chat` 调用的返回里 model 都是 `deepseek-flash`。

本判据守"改了模型清单之后，所有联动点都跟着改好"这件事：
  ① 官方现售名 × 2 必须出现在控制台模型栏；
  ② **模型栏里每个名字**在价目表里有价（漏一个＝用户账单算不出来）；
  ③ 旧名必须被标注"已退役/老别名"（用户看到也知道该换）；
  ④ 峰谷计价表认得出新名（`whale.price_for`）；
  ⑤ 默认模型 ∈ 模型栏；
  ⑥ 视觉能力提示不能因为改名而误报（`deepseek-flash` 其实支持视觉）。

用法：py -3 scripts/model_list_selftest.py
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent.llm import _OFFICIAL_PRICES, match_official_price   # noqa: E402
from agent import whale as WH                                   # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


def src(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


#: 官方现售（2026-09-14 现场取证）
CURRENT = ("deepseek-flash", "deepseek-v4-pro")
#: 已退役但官方仍接受的旧名（本机实测会被路由到 V4.1-Flash）
LEGACY = ("deepseek-v4-flash-vision-exp", "deepseek-v4-flash", "deepseek-v4-flash-0731",
          "deepseek-v4-pro-0813", "deepseek-chat", "deepseek-reasoner")

print("── A. 官方现售名都在控制台模型栏里 ──")
_html = src("agent/console_html.py")
_m = re.search(r"deepseek:\{label:'DeepSeek', base:'[^']*', keyHint:'[^']*',\s*models:\[([^\]]*)\]", _html)
_list = [x.strip().strip("'") for x in _m.group(1).split(",")] if _m else []
ok("取到模型栏清单", len(_list) >= 5, str(len(_list)))
for cur in CURRENT:
    ok("模型栏含 %s（官方正名）" % cur, cur in _list)
ok("两个正名排在最前面（用户第一眼就能选）", _list[:2] == list(CURRENT), str(_list[:3]))

print("── B. 联动点：模型栏 × 价目表 × 峰谷表全覆盖 ──")
_missing_price = [m for m in _list if m not in _OFFICIAL_PRICES]
ok("模型栏里每个名字价目表都有（漏一个＝账单算不出来）", not _missing_price, str(_missing_price))
for cur in CURRENT:
    _p = match_official_price(cur)
    ok("%s 有价且注明官方" % cur,
       isinstance(_p.get("out"), (int, float)) and _p["out"] > 0 and "官方" in str(_p.get("note") or ""),
       "out=%s" % _p.get("out"))
for old in LEGACY:
    if old in _OFFICIAL_PRICES:
        _note = str(_OFFICIAL_PRICES[old].get("note") or "")
        ok("%s 标注了退役/别名" % old, ("已退役" in _note) or ("老别名" in _note) or ("别名" in _note), _note[:28])

print("── C. 峰谷计价表认得出新名 ──")
ok("whale.price_for('deepseek-flash') = Flash 档", WH.price_for("deepseek-flash") is WH.BASE_PRICE)
ok("whale.price_for('deepseek-v4-pro') = Pro 档", WH.price_for("deepseek-v4-pro") is WH.PRO_PRICE)
ok("旧名 deepseek-v4-flash-vision-exp 仍按 Flash 档算",
   WH.price_for("deepseek-v4-flash-vision-exp") is WH.BASE_PRICE)
ok("空模型名不炸（按 Flash 档）", WH.price_for("") is WH.BASE_PRICE)

print("── D. 默认值与其他联动点 ──")
_cfg = src("agent/config.py")
ok("新装默认模型用官方正名 deepseek-flash",
   '"model": "deepseek-flash"' in _cfg and '"model": "deepseek-chat"' not in _cfg)
ok("联网搜索那段的模型也换了正名", '"model": "deepseek-flash", "timeout_ms": 60000' in _cfg)
_ex = src("config.example.json")
ok("示例配置也是正名", '"model": "deepseek-flash"' in _ex)
_st = src("scripts/selftest.py")
ok("视觉提示认得 deepseek-flash（不会误报『疑似非视觉模型』）",
   "deepseek-flash" in _st.split("_VISION_OK")[1][:200])
_hint = src("agent/console_html.py")
ok("界面里的模型示例提示也换成正名（不再教用户填旧名）",
   "deepseek-v4-flash-vision-exp" in _hint or "deepseek-flash" in _hint)
_fc = src("scripts/fullcheck.py")
ok("fullcheck 不再锚死旧名", "match_official_price(\"deepseek-chat\")" not in _fc)

print("")
print("模型清单判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
