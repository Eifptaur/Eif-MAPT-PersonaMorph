# -*- coding: utf-8 -*-
"""版本不匹配「四选一」待决台账 判据。

背景：「弹窗按你推荐的做」+「把弹窗切出来的那一秒，就应该立刻让它到后台」。
落地要守住四件事（本判据逐条钉）：①决策落 `data/pending_decisions.json`（原子写，关掉弹窗≠没发生）；
②**同一对版本只问一次**；③**✕＝什么都不做**（记 dismissed，仍留痕、仍算问过）；
④表态要能**写回能力矩阵**，且「升级适配层/更新本体/微信要处理」这三项**不许在本模块里真去装/降级**。

用法：py -3 scripts/pending_decisions_selftest.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))) # 同目录的 `_srcslice`
import _srcslice
os.chdir(ROOT)

from agent import pending_decisions as PD # noqa: E402
from agent import version_gate as VG # noqa: E402
from agent import version_matrix as VM # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


TMP = tempfile.mkdtemp(prefix="pd636-")
DEC = os.path.join(TMP, "pending_decisions.json")
MAT = os.path.join(TMP, "capability_matrix.json")
try:
    print("── A. 四选一：选项表与动作语义 ──")
    keys = [o["key"] for o in PD.VERSION_OPTIONS]
    ok("四个选项、key 固定、顺序固定",
       keys == ["upgrade_adapter", "update_host", "allow_once", "wechat_side"], "、".join(keys))
    ok("每个选项都有给人看的文案", all(o.get("label") and o.get("detail") for o in PD.VERSION_OPTIONS))
    acts = {o["key"]: o["action"] for o in PD.VERSION_OPTIONS}
    ok("「本次运行继续发送」＝allow_session", acts["allow_once"] == "allow_session", acts["allow_once"])
    ok("「我去微信那边看看」＝guidance（只给指引）", acts["wechat_side"] == "guidance", acts["wechat_side"])
    a4 = PD.apply_choice({"choice": "wechat_side", "options": PD.VERSION_OPTIONS})
    ok("选「我去微信那边看看」不带任何安装/降级动作", a4["action"] == "guidance" and not a4.get("cmd"),
       "%s / cmd=%r" % (a4["action"], a4.get("cmd")))
    src = open(os.path.join(ROOT, "agent", "pending_decisions.py"), encoding="utf-8").read()
    ok("本模块自己不执行外部命令（不装包/不改微信）",
       all(k not in src for k in ("subprocess", "os.system", "pip install", "os.popen")))

    print("── A2. 文案铁律（2026-09-30 用户当面指正后立）──")
    # ⚠️ 这一段用**自己的一次性台账**：写进共享的 `DEC` 会把 B 段"第一次新建"污染成 False
    #    （判据之间通过文件耦合过 —— 这正是要避免的）。
    DEC2 = os.path.join(TMP, "pending_decisions_copy.json")
    # ①「微信」只许指微信客户端：光秃秃的"微信"当主语 ⇒ 用户读成"我们的窗口在报错"
    opts_zh = " ｜ ".join("%s：%s" % (o["label"], o["detail"]) for o in PD.VERSION_OPTIONS)
    ok("选项文案里没有把「微信」当我家产品说的说法（如「微信本身要处理」）",
       "微信本身要处理" not in opts_zh and "微信 " not in opts_zh, opts_zh[:80])
    ok("选项 label 一律是「用户能做的动作」（祈使/我能…）",
       all(any(o["label"].startswith(x) for x in ("一键", "更新", "本次", "我去")) for o in PD.VERSION_OPTIONS),
       "、".join(o["label"] for o in PD.VERSION_OPTIONS))
    ok("每条 detail 都写明点了会发生什么（起 job / 给指引 / 置灰）",
       all(len(str(o.get("detail") or "")) >= 15 for o in PD.VERSION_OPTIONS))
    # ② **反向断言**：标题里绝不许出现 `unknown`（英文缩写等于没说）；
    #    读不到版本号时必须写「本机微信版本读不到」。
    _it, _n = PD.ensure_version_decision("", "1.2.2.2", p=DEC2)
    ok("读不到版本号 ⇒ 标题写「本机微信版本读不到」而不是 `unknown` / 光秃秃的「微信」",
       "unknown" not in _it["title"] and "本机微信版本读不到" in _it["title"], _it["title"])
    _it2, _n2 = PD.ensure_version_decision("4.1.13.65", "1.2.2.2", p=DEC2)
    ok("读得到版本号 ⇒ 标题写「本机微信版本 4.1.13.65」",
       _it2["title"].startswith("本机微信版本 4.1.13.65"), _it2["title"])
    # ③ 否定式选项必须写明"什么**不受影响**"（只写"什么不做"= 同一类错误的镜像）
    ok("「什么都不做」写明了「不影响发送」", "不影响发送" in PD.NONE_MESSAGE, PD.NONE_MESSAGE[:60])
    # ⛔ 最严重那条：`none` 曾被读成"不发送" ⇒ 机器人静默失效，且与 `_strict()` 默认 False 自相矛盾
    ok("「什么都不做」**绝不许**写成「不发送」",
       "不发送" not in PD.NONE_MESSAGE
       and "不发送" not in str(PD.apply_choice(None).get("message")))
    ok("「什么都不做」的动作描述符＝none（不额外做事）", PD.apply_choice(None)["action"] == "none")

    print("── B. 同一对版本只问一次 ──")
    it1, new1 = PD.ensure_version_decision("4.1.13.65", "1.2.2.2", reason="没实测记录", p=DEC)
    it2, new2 = PD.ensure_version_decision("4.1.13.65", "1.2.2.2", reason="重复问一次", p=DEC)
    ok("第一次新建", new1 is True, it1["id"])
    ok("第二次不再新建、返回同一条", new2 is False and it2["id"] == it1["id"], it2["id"])
    ok("待只有 1 件", len(PD.open_items(DEC)) == 1, str(len(PD.open_items(DEC))))
    ok("id 里带版本对（可读）", it1["id"] == "version_mismatch|4.1.13.65|1.2.2.2", it1["id"])
    ok("条目带四个选项与版本信息",
       len(it1["options"]) == 4 and it1["wechat"] == "4.1.13.65" and it1["adapter"] == "1.2.2.2")

    print("── C. ✕＝什么都不做（记 dismissed，不删）──")
    got, created = PD.ensure_version_decision("4.1.13.65", "1.2.2.2", p=DEC)
    r = VG.decide(got["id"], "", decisions_path=DEC, matrix_path=MAT) # 空串＝✕
    ok("状态＝dismissed", r["item"]["status"] == "dismissed", r["item"]["status"])
    ok("choice 为空（什么都没选）", r["item"]["choice"] == "", repr(r["item"]["choice"]))
    ok("动作＝none", r["action"]["action"] == "none", r["action"]["action"])
    ok("条目仍在台账里（关掉弹窗≠没发生）", PD.get(got["id"], DEC) is not None)
    ok("仍算「问过了」", PD.asked("version_mismatch", "4.1.13.65|1.2.2.2", DEC) is True)
    ok("不再重复问（第三次 ensure 仍不新建）",
       PD.ensure_version_decision("4.1.13.65", "1.2.2.2", p=DEC)[1] is False)
    ok("待清零", len(PD.open_items(DEC)) == 0, str(len(PD.open_items(DEC))))
    ok("✕ 也写回了矩阵（choice=none）",
       any(str(d.get("choice")) == "none" for d in VM.decisions(VM.load(MAT))),
       str(len(VM.decisions(VM.load(MAT)))) + " 条")

    print("── D. 非法选项不落定（fail-closed）──")
    it3, _n = PD.ensure_version_decision("9.9.9", "1.2.2.2", p=DEC)
    before = json.dumps(PD.get(it3["id"], DEC), ensure_ascii=False, sort_keys=True)
    threw = False
    try:
        PD.resolve(it3["id"], "随便写的选项", p=DEC)
    except ValueError:
        threw = True
    ok("非法 choice 抛 ValueError", threw)
    after = json.dumps(PD.get(it3["id"], DEC), ensure_ascii=False, sort_keys=True)
    ok("台账没被改坏（仍 open、字段不变）", before == after and PD.get(it3["id"], DEC)["status"] == "open")
    threw2 = False
    try:
        PD.resolve("不存在的单子", "allow_once", p=DEC)
    except ValueError:
        threw2 = True
    ok("不存在的单子也抛错（不许静默新建）", threw2)

    print("── E. 「仅本次允许」＝真的立刻生效 + 写回矩阵 ──")
    runs_before = json.dumps(VM.load(MAT).get("runs"), ensure_ascii=False, sort_keys=True)
    it4, _n4 = PD.ensure_version_decision("4.1.15.8", "9.9.9", p=DEC)
    VG.clear_allow()
    r4 = VG.decide(it4["id"], "allow_once", decisions_path=DEC, matrix_path=MAT)
    ok("动作＝allow_session", r4["action"]["action"] == "allow_session", r4["action"]["action"])
    ok("本会话真的放行了", VG.is_allowed() is True)
    ds = VM.decisions(VM.load(MAT), "4.1.15.8", "9.9.9")
    ok("矩阵里能查到这次表态（含版本对与选择）",
       bool(ds) and ds[-1]["choice"] == "allow_once" and ds[-1]["wechat"] == "4.1.15.8", str(ds[-1:]))
    ok("写回**没有动 runs**（决策≠实测结论，不许把 gate 点亮）",
       json.dumps(VM.load(MAT).get("runs"), ensure_ascii=False, sort_keys=True) == runs_before)
    VG.clear_allow()

    print("── F. 「升级适配层 / 更新本体依赖」只给命令，不立刻改环境 ──")
    it5, _n5 = PD.ensure_version_decision("4.1.13.65", "1.1.5.1", p=DEC)
    r5 = VG.decide(it5["id"], "upgrade_adapter", decisions_path=DEC, matrix_path=MAT)
    ok("动作＝upgrade_adapter 且带可复制命令",
       r5["action"]["action"] == "upgrade_adapter" and r5["action"]["cmd"], r5["action"].get("cmd"))
    ok("没有顺手放行（升级≠放行）", VG.is_allowed() is False)
    ok("矩阵里记着「一键升级适配层」", VM.decisions(VM.load(MAT), "4.1.13.65", "1.1.5.1")[-1]["choice"] == "upgrade_adapter")
    it6, _n6 = PD.ensure_version_decision("4.1.13.65", "1.0.0.0", p=DEC)
    r6 = VG.decide(it6["id"], "update_host", decisions_path=DEC, matrix_path=MAT)
    ok("动作＝heal_deps（更新本体依赖走依赖自愈）", r6["action"]["action"] == "heal_deps", r6["action"]["action"])

    print("── F2. 「点了没反应」的选项必须先置灰（用户 2026-09-30 当面指正）──")
    # ⛔ 根因不是功能缺失，而是**点了没反应**：`update_host` 依赖齐全时 `action_cmd()` 返回空串。
    #    判据要能分辨两种情况 ⇒ 一定要有**反向那条**（依赖缺时必须可点），否则"一律置灰"也能过。
    _it7, _n7 = PD.ensure_version_decision("7.7.7", "1.2.2.2", p=os.path.join(TMP, "pd_avail.json"))
    # ① 桩：假装本机依赖齐全（action_cmd 给空串）⇒ update_host 必须被标成不可用
    _orig_cmd = VG.action_cmd
    try:
        VG.action_cmd = lambda ch: ("" if ch == "update_host" else "python x.py --update")
        _av_ok = VG.option_availability(_it7, {"error": ""})
        ok("依赖齐全 ⇒ `update_host` 标为不可用（不是点完等一个不来的结果）",
           _av_ok["update_host"]["runnable"] is False and bool(_av_ok["update_host"]["why"]),
           str(_av_ok["update_host"]))
        ok("同一张单里 `upgrade_adapter` 仍可用（不是一律置灰）",
           _av_ok["upgrade_adapter"]["runnable"] is True)
        ok("只给指引 / 只放行本会话的选项**永远可点**",
           _av_ok["wechat_side"]["runnable"] is True and _av_ok["allow_once"]["runnable"] is True)
        # ② 反向：命令拿得到时 update_host 必须**可点**（证明这条判据能分辨，不是恒真/恒假）
        VG.action_cmd = lambda ch: "python y.py"
        _av_no = VG.option_availability(_it7, {})
        ok("依赖缺（命令拿得到）⇒ `update_host` 可点（反向验证：判据能分辨）",
           _av_no["update_host"]["runnable"] is True, str(_av_no["update_host"]))
        # ③ decorate_item：给副本打标，**绝不许**把"环境态"写进台账
        VG.action_cmd = lambda ch: ("" if ch == "update_host" else "python x.py --update")
        _dec = VG.decorate_item(_it7, {})
        _op = {o["key"]: o for o in _dec["options"]}
        ok("decorate_item 给副本带上了 disabled / why",
           _op["update_host"].get("disabled") is True and bool(_op["update_host"].get("why")),
           str(_op["update_host"].get("why")))
        ok("decorate_item 里可用的选项**不带** disabled（不是一律标灰）",
           not _op["upgrade_adapter"].get("disabled"))
        ok("★台账**没被写脏**（环境态不许进台账）",
           all(not o.get("disabled") for o in PD.get(_it7["id"], os.path.join(TMP, "pd_avail.json"))["options"]))
        # ④ 复用 `decorate_item` 时也得带上"旧文案换新口径"（历史单不许把废弃说法端给用户）
        _dec2 = VG.decorate_item(_it7, {})
        ok("decorate_item 同时把选项文案换成**当前口径**",
           all(o["label"] in [x["label"] for x in PD.VERSION_OPTIONS] for o in _dec2["options"]),
           str([o["label"] for o in _dec2["options"]]))
    finally:
        VG.action_cmd = _orig_cmd

    print("── G. 原子写与健壮性 ──")
    # ★ 历史单（文案是开单那刻冻结的旧写法）渲染时必须换成当前口径，且**台账数据不许动**
    _legacy = {"id": "version_mismatch|9|9", "options": [
        {"key": "allow_once", "label": "仅本次允许", "detail": "只对本次运行放行发送，重启后重新拦"},
        {"key": "wechat_side", "label": "微信本身要处理", "detail": "只给排查指引"},
        {"key": "未来新选项", "label": "未来新选项", "detail": "认不出来"},
    ]}
    _co = PD.current_options(_legacy)
    _gv = {o["key"]: o for o in _co}
    ok("历史单的旧 label 被换成当前口径（仅本次允许 → 本次运行继续发送）",
       _gv["allow_once"]["label"] == "本次运行继续发送", _gv["allow_once"]["label"])
    ok("历史单的旧说法被换掉（微信本身要处理 → 我去微信那边看看）",
       _gv["wechat_side"]["label"] == "我去微信那边看看", _gv["wechat_side"]["label"])
    ok("★认不出的 key 原样保留（fail-open：宁可显示旧字，也不许把选项弄丢）",
       _gv["未来新选项"]["label"] == "未来新选项")
    ok("★current_options **不改原对象**（历史记录不许被就地篡改）",
       _legacy["options"][0]["label"] == "仅本次允许")

    ok("没有 .tmp 残留", not os.path.exists(DEC + ".tmp"))
    raw = open(DEC, encoding="utf-8").read()
    parsed = json.loads(raw)
    ok("落盘是合法 JSON 且有条目", parsed.get("schema") == 1 and len(parsed.get("items") or []) >= 4,
       "%d 字节 / %d 条" % (len(raw), len(parsed.get("items") or [])))
    bad = os.path.join(TMP, "broken.json")
    open(bad, "w", encoding="utf-8").write("{这不是 JSON")
    got_bad = PD.load(bad)
    ok("坏台账被当成空台账（不抛、不删文件）",
       got_bad.get("items") == [] and os.path.exists(bad))
    ok("✕ / 已表态的单子不占「待」名额",
       all(str(x.get("status")) == "open" for x in PD.open_items(DEC)))

    print("── H. 接线：版本门 + 能力矩阵 ──")
    G = open(os.path.join(ROOT, "agent", "version_gate.py"), encoding="utf-8").read()
    ok("版本门有 pending()（门没过就保证有单子）", "def pending(" in G and "ensure_version_decision" in G)
    ok("版本门有 decide()（落台账 + 写回矩阵 + 副作用）",
       "def decide(" in G and "note_decision" in G and "allow_session" in G)
    M = open(os.path.join(ROOT, "agent", "version_matrix.py"), encoding="utf-8").read()
    ok("能力矩阵有写回/读取表态的入口", "def note_decision(" in M and "def decisions(" in M)
    _seg = _srcslice.from_func(M, "note_decision").split("\ndef ")[0] # 只看这一个函数体
    ok("写回用的是原子写（复用 save）", "save(data, path)" in _seg, "%d 字节" % len(_seg))
    print("\n通过 %d / 失败 %d" % (PASS, FAIL))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

sys.exit(1 if FAIL else 0)
