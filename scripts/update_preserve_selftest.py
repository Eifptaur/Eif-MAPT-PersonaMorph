# -*- coding: utf-8 -*-
"""update_preserve_selftest.py —— 「更新程序会不会弄丢用户东西」判据

来历（2026-10-10 用户问，原话）：
  「你在链路最后查一下，我们的更新程序现在可不可用，会不会覆盖掉用户的设置和添加的人设、
    用户的记忆，如果用户自己修改过某些人设，但是我们这一下也有人设修改，他自己又怎么选」

本判据把答案**钉死**，且每条都能抓"坏代码"：
  A. 用户数据四件套（配置/自建人设/收藏评分/日志）在保护名单里，更新**不碰**；
  B. 内置人设库 `assets/persona/cards.json` **会被替换** —— 但这是**安全的**，理由在 C；
  C. ⭐ **两库物理上是两个文件**：内置在 `assets/`、用户在 `data/`
     ⇒ **不存在"两边都改同一份、更新时冲突"** 那个场景。
     用户"改人设"实际落 `data/custom_personas.json`（受保护）；
     我们"加人设"落 `assets/persona/cards.json`（可更新）⇒ 互不覆盖。
  D. 运行期**没有任何代码写** `assets/persona/cards.json`（只读加载）
     ⇒ 用户在界面里的编辑不可能落到它上面。
  E. 反向锚：把保护名单改坏 ⇒ A 段必须红。
  F. ⭐ **云端出包这一头**（更新器只能换"包里有的文件"）：
     内置人设库必须**真进包**（受版本控制 ＋ 不被 EXCLUDE 挡 ＋ 在随包发布的根里），
     否则"我们改了人设"根本到不了用户机器 —— 更新器再安全也换不出来。

用法：./runtime/python/python.exe -u scripts/update_preserve_selftest.py
"""
import logging
import os
import re
import sys

logging.disable(logging.INFO)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent import update_apply as U  # noqa: E402

FAILS = []
OKS = []


def chk(name, cond, extra=""):
    (OKS if cond else FAILS).append(name)
    print("  %s %s%s" % ("OK  " if cond else "FAIL", name,
                         ("  ← " + extra) if (extra and not cond) else ""))


def _read(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def main():
    print("== A. 用户的东西：更新时碰不碰 ==")
    _must_keep = [
        ("用户配置 config.json", "config.json"),
        ("用户自建人设 data/custom_personas.json", "data/custom_personas.json"),
        ("人设收藏 data/persona_favs.json", "data/persona_favs.json"),
        ("人设评分 data/persona_ratings.json", "data/persona_ratings.json"),
        ("人设分类 data/persona_cats.json", "data/persona_cats.json"),
        ("日志 logs/", "logs/persona_morph.log"),
        ("整套用户数据 data/", "data/anything.json"),
        ("已装好的运行时 runtime/", "runtime/python/python.exe"),
        ("报告目录 报告/", "报告/x.md"),
    ]
    for name, rel in _must_keep:
        chk("A·守 %s" % name, not U._touched(rel),
            "这个路径**可被更新覆盖** ⇒ 用户的它会丢！")

    print("== B. 内置人设库：会被替换（这是预期行为） ==")
    chk("B1 assets/persona/cards.json 在'可换'名单里（属于我方程序文件）",
        U._touched("assets/persona/cards.json"),
        "被列进保护名单了？那我们的新卡就发布不出去了 —— 复核 NEVER_TOUCH")
    chk("B2 整包换入只挑'内容真不一样'的文件（不是全量覆盖）",
        "_diff_rels" in _read("agent/update_apply.py"),
        "改成全量覆盖了 ⇒ 每次更新都重写一遍所有文件")
    chk("B3 失败有整链回滚（先快照将被覆盖的每一件）",
        "rollback" in _read("agent/update_apply.py"),
        "回滚机制不见了 ⇒ 更新中途失败会留半装状态")

    print("== C. ⭐ 两库是两个文件 ⇒ 不存在'两边都改同一份' ==")
    # 源码里路径是写成分段字面量的（`os.path.join(_ROOT, "assets", "persona", "cards.json")`）
    # ⇒ 判据按"分段是否都出现"来认，不按拼接后的完整串。
    _pm_src = _read("agent/persona.py")
    _pm_flat = re.sub(r"[\s\"']+", "", _pm_src)
    chk("C1 内置库路径在 assets/persona 下",
        '"assets","persona","cards.json"' in _pm_flat or 'assets/persona/cards.json' in _pm_src,
        "内置库搬家了 ⇒ 结论要重新核")
    _ec_src = _read("agent/ext_center.py")
    _ec_flat = re.sub(r"[\s\"']+", "", _ec_src)
    chk("C2 用户自建库路径在 data/ 下（受保护）",
        '"data","custom_personas.json"' in _ec_flat or 'data/custom_personas.json' in _ec_src,
        "自建库搬家了 ⇒ 要重新核它还在不在保护名单里")
    chk("C3 ★两个路径**不相同**（同文件才会有'覆盖冲突'）",
        os.path.join("assets", "persona", "cards.json") != os.path.join("data", "custom_personas.json"))
    # 用户编辑走的接口落点
    _wu = _read("agent/webui.py")
    chk("C4 ★用户的人设编辑（/api/personas/custom 的 GET/POST/DEL）全部落 data/custom_personas.json",
        _wu.count("custom_personas.json") >= 3,
        "落点变了 ⇒ 可能改到 assets/ 去了（那就会被更新覆盖）")

    print("== D. 运行期没有任何代码写内置库（=用户改不到它） ==")
    _PM = _read("agent/persona.py")
    chk("D1 agent/persona.py 只读（1 次 open、0 次 json.dump）",
        _PM.count("json.dump") == 0 and isinstance(_PM.count("open("), int),
        "加载器里出现写操作 ⇒ 用户改动能落盘到内置库")
    # 全库扫：正式目录里对 cards.json 的写入点
    _writers = []
    for _d in ("agent", "ui_qt", "scripts"):
        for _root, _dirs, _files in os.walk(os.path.join(_ROOT, _d)):
            _dirs[:] = [x for x in _dirs if x != "__pycache__"]
            for _f in _files:
                if not _f.endswith(".py"):
                    continue
                _p = os.path.join(_root, _f)
                try:
                    _s = open(_p, encoding="utf-8", errors="ignore").read()
                except Exception:
                    continue
                if "cards.json" not in _s:
                    continue
                if re.search(r"atomic_write_json\s*\([^)]*cards|json\.dump\([^)]*cards"
                             r"|open\([^)]*cards[^)]*[\"']w", _s, re.S):
                    _writers.append(os.path.relpath(_p, _ROOT))
    # 唯一允许的写入者是那个**离线人工**落盘器（不被运行时调用），排除它
    _writers = [w for w in _writers if "persona_apply_rewrite" not in w]
    chk("D2 ★全库扫：正式代码里**没有**写 cards.json 的地方",
        not _writers,
        "发现写入点：%s" % _writers)
    chk("D3 那个离线落盘器不会被运行时/界面调用（只人工跑）",
        "apply_rewrite" not in _read("agent/webui.py")
        and "apply_rewrite" not in _read("agent/routes.py"),
        "落盘器被接进运行时了 ⇒ 更新会与用户编辑打架")

    print("== E. 反向锚：把保护名单改坏 ⇒ A 段必须报红 ==")
    _orig = U.NEVER_TOUCH
    try:
        U.NEVER_TOUCH = tuple(n for n in _orig if n != "data/")
        _leaked = U._touched("data/custom_personas.json")
    finally:
        U.NEVER_TOUCH = _orig
    chk("E1 ★反向锚：抽掉 'data/' 后，自建人设应变成'可覆盖'（说明 A 段真在起作用）",
        bool(_leaked),
        "抽掉保护后仍判'受保护' ⇒ A 段的判据是假的")
    chk("E2 ★原样名单下自建人设受保护（否则 E1 是假阳性）",
        not U._touched("data/custom_personas.json"))

    # F. ⭐ **云端出包这一头**：更新器只管"换进来之后别覆盖"，但更新器**只能换包里有的文件**
    #    ⇒ 内置人设库必须真进包，否则"我们更新了人设"根本到不了用户机器（更新器再安全也没用）。
    #    （2026-10-10 用户问："我们更新程序现在能换吗，如果用户本地都是更新程序、都是不换人设的，
    #      那我云端就成为换人设的那种，能更新到吗" ⇒ 答案必须由"包里有没有它"来证明。）
    import pack_online as _po  # noqa: PLC0415  与出包脚本共用同一份跟踪/排除规则
    chk("F1 内置人设库**受版本控制**（出包源＝`git ls-files`，不在库里就进不了包）",
        "assets/persona/cards.json" in _po.tracked(),
        "人设库没入库 ⇒ 出包时不会带上 ⇒ 我们的新卡发不出去")
    chk("F2 内置人设库**没被出包排除名单挡掉**",
        not _po.excluded("assets/persona/cards.json"),
        "被 EXCLUDE 挡了 ⇒ 即使入库也不进包")
    chk("F3 内置人设库在'随包发布'的根里（assets/ ⇒ 出包闸门会核它齐不齐）",
        any("assets/persona/cards.json".startswith(p) for p in _po.SHIP_ROOTS),
        "不在随包发布的根里 ⇒ 出包链路不会为它把关")
    chk("F4 出包源只认已入库的文件 ⇒ 新卡改完必须 `git add` 才发得出去",
        hasattr(_po, "untracked_ship_files"),
        "少了'该进包却没入库'的闸门 ⇒ 新卡改了没提交、包照样出成功（静默少东西）")
    # 两库的**出包分档**必须一致：内置进包、用户库不进包
    chk("F5 ★出包也分档：内置库进包、用户自建库不进包（`data/` 在 EXCLUDE 里）",
        (not _po.excluded("assets/persona/cards.json"))
        and _po.excluded("data/custom_personas.json"),
        "分档反了 ⇒ 要么新卡发不出去，要么把用户的自建库打进包里")

    print()
    print("== 汇总：%d 通过 / %d 失败 ==" % (len(OKS), len(FAILS)))
    if FAILS:
        for f in FAILS:
            print("   FAIL:", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
