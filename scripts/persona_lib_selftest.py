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
    "🎸 BanG Dream!": ["kasumi_bd", "arisa_bd", "tae_bd", "rimi_bd", "saya_bd"],
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

print("── D. 新卡不许塞当代网络梗 ──")
_BAD_WORDS = ("V我50", "yyds", "绝绝子", "草", "6", "退钱", "先吃饭", "典", "破防")
for cat, keys in NEW_BATCHES.items():
    for k in keys:
        t = str((PERSONAS.get(k) or {}).get("text", ""))
        hit = [w for w in _BAD_WORDS if w in t]
        ok("%s 无网络梗" % k, not hit, str(hit))

print("")
print("人设库判据：%d 通过 / %d 失败（库共 %d 张 / 分区 %d 个）"
      % (PASS, FAIL, len(PERSONAS), len(set(PERSONA_CATS.values()))))
sys.exit(1 if FAIL else 0)
