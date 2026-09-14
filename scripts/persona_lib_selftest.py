# -*- coding: utf-8 -*-
"""人设库结构/来源判据（2026-09-14 · 配合「加三个新分区 + 尽量全地补卡」这条队列）。

用户口径两条（都要机器守住）：
  ① 「先加分区，再加人设」——新卡必须挂在**目标分区**上，且分区名要有；
  ② 「尽量贴合原人设，去网上找第一手资源，不要自己胡编乱造」——每张新卡都要能在
     `docs/人设来源台账.md` 里找到出处；**没有一手出处的台词必须标 `（按其口吻）`**。

另守三条库级结构（改了会静默坏掉的东西）：
  · 卡文本以 `# 角色卡：` 开头、含「你是…」的身份句（评分细则按这个判贴合）；
  · `PERSONA_CATS` 覆盖全部卡（漏一个 ⇒ 控制台里掉进「🔥 网络热门」）；
  · 新卡不许塞当代网络梗（细则里 V我50/6/草/yyds 这类对古装/正经角色是重罚项）。

用法：py -3 scripts/persona_lib_selftest.py
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent.persona import PERSONAS, PERSONA_CATS     # noqa: E402

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


#: 新分区 → 该分区下已入库的卡（每批做完在这里登记，判据跟着涨）
NEW_BATCHES = {
    "🎸 BanG Dream!": ["kasumi_bd", "arisa_bd", "tae_bd", "rimi_bd", "saya_bd",
                       "yukina_bd", "sayo_bd", "lisa_bd", "ako_bd", "rinko_bd",
                       "tomori_bd", "anon_bd", "rana_bd", "soyo_bd", "taki_bd",
                       "ran_bd", "moca_bd", "himari_bd", "tsugumi_bd", "tomoe_bd",
                       "aya_bd", "hina_bd", "chisato_bd", "maya_bd", "eve_bd",
                       "kokoro_bd", "kaoru_bd", "hagumi_bd", "kanon_bd", "michelle_bd",
                       "layer_bd", "lock_bd", "masking_bd", "pareo_bd", "chu2_bd"],
    "🀄 东方Project": ["reimu_th", "marisa_th", "sakuya_th"],
    "⏳ 重返未来：1999": ["sonetto_r1999", "regulus_r1999", "sotheby_r1999", "vertin_r1999"],
}
#: 本判据"严格口径"只约束这些新卡（老库历史卡有各自的格式，不在本轮返工范围）
_ALL_NEW = [k for keys in NEW_BATCHES.values() for k in keys]

print("── A. 库级结构 ──")
ok("卡数 ≥ 185（补卡只能多不能少）", len(PERSONAS) >= 185, str(len(PERSONAS)))
_miss_cat = [k for k in PERSONAS if k not in PERSONA_CATS]
ok("每张卡都有分区（漏一个就掉进「网络热门」）", not _miss_cat, str(_miss_cat[:5]))
_bad_head = [k for k, v in PERSONAS.items() if not str(v.get("text", "")).startswith("# 角色卡：")]
ok("新卡一律以「# 角色卡：」开头", not [k for k in _bad_head if k in _ALL_NEW])
# 库级只查"别继续退化"：老库里 xiaojingyu（内置小鲸鱼）用的是自己的标题格式，不动它
ok("全库「# 角色卡：」开头比例 ≥ 99%",
   (len(PERSONAS) - len(_bad_head)) / max(1, len(PERSONAS)) >= 0.99,
   "未达标 %d 张：%s" % (len(_bad_head), str(_bad_head[:3])))
_no_you = [k for k, v in PERSONAS.items() if "你是" not in str(v.get("text", ""))[:400]]
ok("新卡都有「你是…」身份句", not [k for k in _no_you if k in _ALL_NEW])
print("      （信息）全库带「你是…」开头的卡：%d/%d —— 老库本来就混着两种写法，本轮不返工"
      % (len(PERSONAS) - len(_no_you), len(PERSONAS)))
_common = [k for k, v in PERSONAS.items() if "说话规则（群聊通用）" not in str(v.get("text", ""))]
ok("每张卡都吃到通用群聊规则（enrich 生效）", not _common, str(_common[:5]))

print("── B. 新分区：先分区、再挂卡 ──")
for cat, keys in NEW_BATCHES.items():
    ok("分区存在：%s" % cat, cat in set(PERSONA_CATS.values()))
    bad = [k for k in keys if PERSONA_CATS.get(k) != cat]
    ok("该分区下的卡都挂对了（%d 张）" % len(keys), not bad, str(bad))
    ok("该分区非空（选单里不会出现空分区）",
       any(PERSONA_CATS.get(k) == cat for k in PERSONAS))

print("── C. 第一手来源与「不许编」──")
_ledger = src("docs/人设来源台账.md")
for cat, keys in NEW_BATCHES.items():
    for k in keys:
        nm = (PERSONAS.get(k) or {}).get("name", k)
        ok("台账里记了 %s 的出处" % nm.split("（")[0], (k in _ledger) or (nm.split("（")[0] in _ledger))
        t = str((PERSONAS.get(k) or {}).get("text", ""))
        ok("%s 卡里有引用（「」原文或标注）" % nm.split("（")[0], ("「" in t and "」" in t), str(len(t)))
_rimi = str(PERSONAS.get("rimi_bd", {}).get("text", ""))
ok("没有一手台词的卡标了「（按其口吻）」（rimi_bd）", "按其口吻" in _rimi)

print("── C2. 每张卡都要有评分可显示（用户：「人设卡没有评分显示的，记得补上」）──")
import json as _json                                  # noqa: E402
_RATINGS = os.path.join(ROOT, "data", "persona_ratings.json")
try:
    with io.open(_RATINGS, encoding="utf-8") as f:
        _R = _json.load(f)
except Exception:
    _R = {}
_no_score = [k for k in PERSONAS if not isinstance((_R.get(k) or {}).get("model"), (int, float))]
ok("全部卡都有模型评分（新卡不许再漏）", not _no_score, str(_no_score[:6]))
_bad = [k for k, v in _R.items() if k in PERSONAS and isinstance(v.get("model"), (int, float))
        and not (0 <= float(v["model"]) <= 100)]
ok("评分都在 0~100 区间", not _bad, str(_bad[:5]))
# 示例必须是"人话"：自动从引号里拼出来的崩坏示例（拿角色名/档案字段当回答）不许再出现
# ⚠️ 判据别把"话少的角色"判成占位符：「嗯。在弹琴。」「肉。汉堡肉。」这类短答是**正确**的
#    ⇒ 只认三种特征：等于角色名、带档案字段词、冒号后跟数字。
_ARCH = ("生日", "介质", "香调", "灵感", "种族", "稀有度", "属性", "定位标签", "实装版本")
_bad_ex = []
for k in _ALL_NEW:
    _nm = str((PERSONAS.get(k) or {}).get("name", ""))
    _head = _nm.split("（")[0]
    _head2 = _head.split("（")[0].strip()
    t = str((PERSONAS.get(k) or {}).get("text", ""))
    i = t.find("## 对话示例")
    blk = t[i:i + 400] if i >= 0 else ""
    for line in blk.split("\n"):
        if line.strip().startswith("- 群友") and ("你：「" in line):
            ans = line.split("你：「", 1)[1].split("」", 1)[0].strip()
            # ⚠️ 只用"整条就是名字"判定（咪歇露自称ミッシェル 是角色设定，不是占位符）
            if ans and ans in (_head, _head2, _head.replace(" ", "")) or any(w in ans for w in _ARCH) \
                    or re.search(r"：\s*\d", ans):
                _bad_ex.append("%s→%s" % (k, ans))
ok("新卡的对话示例不是拼出来的占位符（拿名字/档案字段当回答）", not _bad_ex, str(_bad_ex[:4]))

print("── D. 新卡不许塞当代网络梗 ──")# ⚠️ 判据也要防假阳：单字「典」会命中「祭典」、单字「6」会命中任何数字 ⇒ 只查**词**+孤立数字
_BAD_WORDS = ("V我50", "yyds", "YYDS", "绝绝子", "退钱", "先吃饭", "典中典", "破防", "栓Q", "666")
_BAD_RE = re.compile(r"(?<![0-9A-Za-z])6(?![0-9A-Za-z])")
for cat, keys in NEW_BATCHES.items():
    for k in keys:
        t = str((PERSONAS.get(k) or {}).get("text", ""))
        hit = [w for w in _BAD_WORDS if w in t]
        if _BAD_RE.search(t):
            hit.append("孤立数字 6")
        ok("%s 无网络梗" % k, not hit, str(hit))

print("")
print("人设库判据：%d 通过 / %d 失败（库共 %d 张 / 分区 %d 个）"
      % (PASS, FAIL, len(PERSONAS), len(set(PERSONA_CATS.values()))))
sys.exit(1 if FAIL else 0)
