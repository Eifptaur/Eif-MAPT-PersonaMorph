# -*- coding: utf-8 -*-
"""「评分系统按七字段改造」判据 —— 守第 103 项。

v3 相对 v2 的三处改动，每一处都要有**机械判据**钉住（不靠"我改了"）：
  · ① **判据可指认**：每维都声明它**看卡上的哪个字段**（`DIM_SOURCE`），
    且必须覆盖到 `persona_card.FIELDS` 的七字段（不是只在嘴上说"看行为"）；
  · ② **动机提权**：`fit`（最大权重 30%）的首要看处**必须是 `motive`**；
    且 `motive_ok()` 能自己判"非空 / 不太短 / 不是抽象口号"；
  · ③ **三项扣分**：前后矛盾 / 被诱导放弃立场 / 口头禅不稳定 —— 各自挂在正确的维度上、
    上限比"没这毛病"的档更低，且**都能在 RULES_TEXT 里被指认**（模型能看到）。

用法：runtime\\python\\python.exe scripts/persona_rating_v3_selftest.py
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


SRC = io.open(os.path.join(ROOT, "agent", "persona_rating.py"), encoding="utf-8").read()
from agent import persona_rating as R  # noqa: E402
from agent import persona_card as PC  # noqa: E402

print("── A. 五维契约不变（5 个调用点不必改）──")
ok("A1 分母守卫：拿到 `persona_rating.py` 正文（>4000 字符）", len(SRC) > 4000, "%d 字符" % len(SRC))
ok("A2 五维键名与权重**一字未改**（`persona_fix` / `persona_morph` 的 JSON 契约靠它）",
   R.WEIGHTS == {"style": 25, "fit": 30, "coher": 20, "natural": 15, "usable": 10},
   str(R.WEIGHTS))
ok("A3 `compute()` 仍是纯加权（无新隐式行为）",
   abs(R.compute({"style": 100, "fit": 0, "coher": 0, "natural": 0, "usable": 0}) - 25.0) < 1e-9,
   str(R.compute({"style": 100, "fit": 0, "coher": 0, "natural": 0, "usable": 0})))

print("── B. ① 每维的「看哪里」（可指认，且覆盖七字段）──")
ok("B1 分母守卫：`DIM_SOURCE` 里五维**一个不少**",
   set(R.DIM_SOURCE) == set(R.WEIGHTS), str(sorted(set(R.DIM_SOURCE))))
_seen_fields = set()
for _k, _fs in R.DIM_SOURCE.items():
    _seen_fields |= set(_fs)
ok("B2 分母守卫：`DIM_SOURCE` 引用的字段**全部是七字段的合法键**"
   "（拼错键名就等于「看了一个不存在的字段」）",
   _seen_fields <= set(PC.FIELDS), "越界=%s" % sorted(_seen_fields - set(PC.FIELDS)))
ok("B3 ⭐ 七字段**全部被某一维看到**（不漏字段：写了没人看的字段＝白写）",
   _seen_fields == set(PC.FIELDS), "没被看=%s" % sorted(set(PC.FIELDS) - _seen_fields))
ok("B4 ⛔ 每维都**必须**声明看哪里（空列表＝又退回抽象标签）",
   all(len(v) >= 1 for v in R.DIM_SOURCE.values()), str(R.DIM_SOURCE))

print("── C. ② 动机提权（实证 62.92% → 95.46%）──")
ok("C1 ⭐ `fit`（最大权重 30%）的首要看处**就是 `motive`**",
   R.DIM_SOURCE.get("fit", ("",))[0] == "motive", str(R.DIM_SOURCE.get("fit")))
ok("C2 ⭐ `coher` 也把 `motive` 纳入（「动机驱动下的行为是否连贯」）",
   "motive" in R.DIM_SOURCE.get("coher", ()), str(R.DIM_SOURCE.get("coher")))
ok("C3 `motive_ok()` 存在且可调用", callable(getattr(R, "motive_ok", None)), "")
ok("C4 ⛔ 动机为空 ⇒ 不通过", R.motive_ok({}) is False and R.motive_ok({"motive": ""}) is False, "")
ok("C5 ⛔ 动机太短（<6 字符）⇒ 不通过（「想赢」这种撑不起一张卡）",
   R.motive_ok({"motive": "想赢"}) is False, "")
ok("C6 ⛔ 只写抽象口号（「想要变强」）⇒ 不通过（换谁都成立）",
   R.motive_ok({"motive": "想要变强"}) is False, "")
# ⚠️ 反向锚必须用**长度够长**的样本 —— 「想要变强」只有 4 字符，
#    会**先被长度门槛拦下**，清空词表也还是 False ⇒ 那样测不出词表的作用。
_C8_SAMPLE = {"motive": "想要变得更强一些"}      # 8 字符：过长度门槛，只靠词表拦
ok("C7b 分母守卫：上面这个样本**过得了长度门槛**（否则下面的反向锚测不到东西）",
   len(str(_C8_SAMPLE["motive"])) >= 6, "%d 字符" % len(str(_C8_SAMPLE["motive"])))
ok("C7c ⛔ 该样本**被词表拦下**（这就是反向锚的对照面）",
   R.motive_ok(_C8_SAMPLE) is False, "")
ok("C7 ⭐ 具体动机（含缘由/手段）⇒ 通过",
   R.motive_ok({"motive": "为了替师父讨回公道，暗中查访多年"}) is True, "")
_saved_hints = R.MOTIVE_ABSTRACT_HINTS
try:
    R.MOTIVE_ABSTRACT_HINTS = ()
    _c8 = R.motive_ok(_C8_SAMPLE)              # 清空词表后，同一个样本就该过
finally:
    R.MOTIVE_ABSTRACT_HINTS = _saved_hints
ok("C8 ⭐ 反向锚：清空抽象词表 ⇒ 同一个样本就**通过**"
   "（证明 C7c 的拦截**确实是词表**在做，不是长度或别的判据）",
   _c8 is True, repr(_c8))
ok("C9 判据声明了七字段与 `persona_card.FIELDS` 对齐（防两边键名漂移）",
   re.search(r"persona_card\.FIELDS", SRC) is not None, "")

print("── D. ③ 三项扣分（前后矛盾 / 被诱导放弃立场 / 口头禅不稳定）──")
ok("D1 分母守卫：`DEDUCT_V3` 正好三项",
   len(R.DEDUCT_V3) == 3, str(sorted(R.DEDUCT_V3)))
ok("D2 三项名字与第 103 项**逐字一致**",
   set(R.DEDUCT_V3) == {"前后矛盾", "被诱导放弃立场", "口头禅不稳定"}, str(sorted(R.DEDUCT_V3)))
ok("D3 ⛔ 「前后矛盾」挂在 `coher`（内在一致）—— 挂错维度等于判据没接上",
   R.DEDUCT_V3["前后矛盾"][0] == "coher", str(R.DEDUCT_V3["前后矛盾"]))
ok("D4 ⛔ 「被诱导放弃立场」挂在 `fit`（角色贴合）",
   R.DEDUCT_V3["被诱导放弃立场"][0] == "fit", str(R.DEDUCT_V3["被诱导放弃立场"]))
ok("D5 ⛔ 「口头禅不稳定」挂在 `style`（风格辨识）",
   R.DEDUCT_V3["口头禅不稳定"][0] == "style", str(R.DEDUCT_V3["口头禅不稳定"]))
ok("D6 ⭐ 三项的**上限都比「没这毛病」的档更低**（否则扣了等于没扣）",
   all(float(v[1]) <= 60.0 for v in R.DEDUCT_V3.values()),
   str({k: v[1] for k, v in R.DEDUCT_V3.items()}))
ok("D7 ⛔ 每项都写清「怎么认出来的」（可指认）—— 不能是「感觉不好」",
   all(len(str(v[2]).strip()) >= 10 for v in R.DEDUCT_V3.values()),
   str({k: v[2][:14] for k, v in R.DEDUCT_V3.items()}))

print("── E. 模型真能看到这三项（RULES_TEXT 里可指认）──")
for _name in ("前后矛盾", "被诱导放弃立场", "口头禅不稳定"):
    ok("E RULES_TEXT 里有「%s」且带上限" % _name,
       (_name in R.RULES_TEXT) and re.search(_name + r"→[^；\n]*≤\d", R.RULES_TEXT) is not None, "")
ok("E4 ⭐ 动机提权也写进了 RULES_TEXT（否则模型打不出这个分）",
   "首先看「动机」" in R.RULES_TEXT, "")
ok("E5 ⭐ 「可指认」这条纪律写进了 RULES_TEXT（模型被要求指着原句说）",
   "可指认" in R.RULES_TEXT, "")
ok("E6 版本号升到 v3（旧 v2 的 dump 一眼能看出是哪版细则）",
   "v3" in R.RULES_TEXT and "v3.0" in SRC, "")

print("")
print("评分系统七字段改造判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
