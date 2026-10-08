# -*- coding: utf-8 -*-
"""「生成侧 IP 闸门」判据（第 187 项）—— 不联网、不生成图、不写产品 `data/`。

守四件事（**第一条是本项的灵魂**）：
  A **画风词全部放行**（反向对照，逐个词断言）—— 判据是"可识别的具体表达"，不是"风格接近"
    （广州 (2024)粤0192民初113号）。拦画风词是**净损失**（必然误伤），放行它的代价是 **0**。
  B **具体角色/作品名能拦住**（严格维权方五家各挑一个代表词）。
  C **名单里不许混进画风词**（静态自检 —— 防以后有人"顺手"往里塞风格词）。
  D **"不知道"不许混进"放行"**：查证无结论（`unclear=True`）⇒ `review`（留痕），**不是 pass、也不是 block**。
外加**接线锚**：生成链的过滤层真的过了这道闸门（且它出错时**不因此把图拦下**）。

用法：runtime\\python\\python.exe scripts/ip_guard_selftest.py
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import _srcmatch as _sm  # noqa: E402
from agent import ip_guard as IG  # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


print("── A. 画风词全部放行（★反向对照，逐个）──")
_bad = []
for _w in IG.STYLE_WORDS:
    _r = IG.judge("画一个%s的少女" % _w)
    if _r["action"] != "pass":
        _bad.append((_w, _r["action"]))
ok("A0 分母守卫：画风词清单非空（否则下面这条是空转）", len(IG.STYLE_WORDS) >= 20, str(len(IG.STYLE_WORDS)))
ok("A1★★ **每一个画风词都放行**（%d 个逐个断言）" % len(IG.STYLE_WORDS), not _bad, str(_bad[:4]))
ok("A2 混合句也放行：画风词 + 普通主体（按判决：「机甲风」不是可识别表达）",
   IG.judge("画一只机甲风的猫")["action"] == "pass"
   and IG.judge("赛博朋克雨夜的城市，赛璐璐上色")["action"] == "pass", "")
ok("A3★ **即使有人手滑把画风词填进 `extra_block`，也不拦**"
   "（按风格过滤是 `image_gen.style_block` 的事，职责不许混）",
   IG.judge("画个日系少女", cfg={"extra_block": ["日系"]})["action"] == "pass", "")

print("\n── B. 具体角色/作品名要拦住 ──")
_hits = {"米老鼠": "disney", "皮卡丘": "nintendo", "奥特曼": "ultraman",
         "熊出没": "boonie_bears", "哈利波特": "warner_hp"}
_bad2 = []
for _w, _h in _hits.items():
    _r = IG.judge("画一个%s" % _w)
    if _r["action"] != "block":
        _bad2.append((_w, _r["action"]))
ok("B1★ 五家严格维权方各挑一个代表词，**都拦**", not _bad2, str(_bad2))
ok("B1b 拦住时要说清**命中了谁、为什么**（不是一句「违规」）",
   "严格维权方" in IG.judge("画个皮卡丘")["why"], IG.judge("画个皮卡丘")["why"])
ok("B2 名单**不追求穷举**：查不到的词照常放行（漏了下游分类器兜底，不靠这里包打天下）",
   IG.judge("画一只不存在的猫")["action"] == "pass", "")

print("\n── C. 名单里不许混进画风词（静态自检）──")
_all = [w for ws in IG.STRICT_HOLDERS.values() for w in ws]
ok("C1★ **限制名单与画风词清单无交集**（防以后有人「顺手」把风格词塞进限制名单）",
   not (set(x.lower() for x in _all) & set(IG.STYLE_WORDS)), "")
ok("C2 名单只收五家（别越收越多 —— 收多了就是误伤）", len(IG.STRICT_HOLDERS) == 5,
   str(sorted(IG.STRICT_HOLDERS.keys())))
_readme = io.open(os.path.join(ROOT, "README.md"), encoding="utf-8", errors="ignore").read()
_leaked = [w for w in _all if w in _readme]
ok("C3★ **README 里不许出现名单里的角色名**（仓库文本可被关键词检索；文档只写机制与判据，"
   "清单留在代码/配置里。这条能反向抓「哪天有人把清单贴进文档」）",
   not _leaked, str(_leaked[:4]))

print("\n── D. 「不知道」不许混进「放行」 ──")
_r = IG.judge("画一个冷门作品的角色", unclear=True)
ok("D1★★ 查证无结论 ⇒ `review`（**留痕**，不静默放行）",
   _r["action"] == "review" and "留痕" in _r["why"], _r["action"])
ok("D2 但**画风词不因 `unclear` 被拉去 review**（它压根不涉及「某作品的政策」）",
   IG.judge("画一个赛璐璐风格的少女", unclear=True)["action"] == "pass", "")
ok("D3 三档齐备且互斥", tuple(IG.ACTIONS) == ("block", "review", "pass"), str(IG.ACTIONS))
ok("D4 关掉闸门 ⇒ 一律放行（用户能关，且关得干净）",
   IG.judge("画个皮卡丘", cfg={"enabled": False})["action"] == "pass", "")

print("\n── 接线锚 ──")
_ig = io.open(os.path.join(ROOT, "agent", "image_gen.py"), encoding="utf-8").read()
ok("W1★ 生成链的过滤层**真的调了**这道闸门（`_f_blacklist` 里 `ip_guard.judge`）",
   _sm.has(_ig, "from . import ip_guard as _ipg") and _sm.has(_ig, "_ipg.judge("), "")
ok("W2★ 闸门**自己出错时不把图拦下**（fail-open 在这一层是对的：拦错了就是画风词误伤，"
   "那正是本项要治的病）",
   _sm.has(_ig, "放行并留痕"), "")
ok("W3 `review` 走的是**留痕**（写进 meta 供上层看见），不是「当作没发生」",
   _sm.has(_ig, '_ip_review'), "")
ok("W4 控制台有开关（`image_gen.ip_guard.enabled`）＋ 段加进了 `MUST_EXPOSE`",
   _sm.has(io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read(),
           'data-cfg="image_gen.ip_guard.enabled"')
   and _sm.has(io.open(os.path.join(ROOT, "scripts", "console_expose_selftest.py"), encoding="utf-8").read(),
               '"image_gen.ip_guard.enabled"'), "")

print("\n== IP 闸门判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
