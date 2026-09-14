# -*- coding: utf-8 -*-
"""版本不匹配「四选一」待决台账 判据（2026-09-14，测机报告 ⑦）。

背景（用户口径）：「弹窗按你推荐的做」+「把弹窗切出来的那一秒，就应该立刻让它到后台」。
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

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import pending_decisions as PD      # noqa: E402
from agent import version_gate as VG           # noqa: E402
from agent import version_matrix as VM         # noqa: E402

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
    ok("「仅本次允许」＝allow_session", acts["allow_once"] == "allow_session", acts["allow_once"])
    ok("「微信本身要处理」＝guidance（只给指引）", acts["wechat_side"] == "guidance", acts["wechat_side"])
    a4 = PD.apply_choice({"choice": "wechat_side", "options": PD.VERSION_OPTIONS})
    ok("选「微信本身要处理」不带任何安装/降级动作", a4["action"] == "guidance" and not a4.get("cmd"),
       "%s / cmd=%r" % (a4["action"], a4.get("cmd")))
    src = open(os.path.join(ROOT, "agent", "pending_decisions.py"), encoding="utf-8").read()
    ok("本模块自己不执行外部命令（不装包/不改微信）",
       all(k not in src for k in ("subprocess", "os.system", "pip install", "os.popen")))

    print("── B. 同一对版本只问一次 ──")
    it1, new1 = PD.ensure_version_decision("4.1.13.65", "1.2.2.2", reason="没实测记录", p=DEC)
    it2, new2 = PD.ensure_version_decision("4.1.13.65", "1.2.2.2", reason="重复问一次", p=DEC)
    ok("第一次新建", new1 is True, it1["id"])
    ok("第二次不再新建、返回同一条", new2 is False and it2["id"] == it1["id"], it2["id"])
    ok("待拍板只有 1 件", len(PD.open_items(DEC)) == 1, str(len(PD.open_items(DEC))))
    ok("id 里带版本对（可读）", it1["id"] == "version_mismatch|4.1.13.65|1.2.2.2", it1["id"])
    ok("条目带四个选项与版本信息",
       len(it1["options"]) == 4 and it1["wechat"] == "4.1.13.65" and it1["adapter"] == "1.2.2.2")

    print("── C. ✕＝什么都不做（记 dismissed，不删）──")
    got, created = PD.ensure_version_decision("4.1.13.65", "1.2.2.2", p=DEC)
    r = VG.decide(got["id"], "", decisions_path=DEC, matrix_path=MAT)   # 空串＝✕
    ok("状态＝dismissed", r["item"]["status"] == "dismissed", r["item"]["status"])
    ok("choice 为空（什么都没选）", r["item"]["choice"] == "", repr(r["item"]["choice"]))
    ok("动作＝none", r["action"]["action"] == "none", r["action"]["action"])
    ok("条目仍在台账里（关掉弹窗≠没发生）", PD.get(got["id"], DEC) is not None)
    ok("仍算「问过了」", PD.asked("version_mismatch", "4.1.13.65|1.2.2.2", DEC) is True)
    ok("不再重复问（第三次 ensure 仍不新建）",
       PD.ensure_version_decision("4.1.13.65", "1.2.2.2", p=DEC)[1] is False)
    ok("待拍板清零", len(PD.open_items(DEC)) == 0, str(len(PD.open_items(DEC))))
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

    print("── F. 「升级适配层 / 更新本体」只给命令，不立刻改环境 ──")
    it5, _n5 = PD.ensure_version_decision("4.1.13.65", "1.1.5.1", p=DEC)
    r5 = VG.decide(it5["id"], "upgrade_adapter", decisions_path=DEC, matrix_path=MAT)
    ok("动作＝upgrade_adapter 且带可复制命令",
       r5["action"]["action"] == "upgrade_adapter" and r5["action"]["cmd"], r5["action"].get("cmd"))
    ok("没有顺手放行（升级≠放行）", VG.is_allowed() is False)
    ok("矩阵里记着「去升级适配层」", VM.decisions(VM.load(MAT), "4.1.13.65", "1.1.5.1")[-1]["choice"] == "upgrade_adapter")
    it6, _n6 = PD.ensure_version_decision("4.1.13.65", "1.0.0.0", p=DEC)
    r6 = VG.decide(it6["id"], "update_host", decisions_path=DEC, matrix_path=MAT)
    ok("动作＝heal_deps（更新本体走依赖自愈）", r6["action"]["action"] == "heal_deps", r6["action"]["action"])

    print("── G. 原子写与健壮性 ──")
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
    ok("✕ / 已表态的单子不占「待拍板」名额",
       all(str(x.get("status")) == "open" for x in PD.open_items(DEC)))

    print("── H. 接线：版本门 + 能力矩阵 ──")
    G = open(os.path.join(ROOT, "agent", "version_gate.py"), encoding="utf-8").read()
    ok("版本门有 pending()（门没过就保证有单子）", "def pending(" in G and "ensure_version_decision" in G)
    ok("版本门有 decide()（落台账 + 写回矩阵 + 副作用）",
       "def decide(" in G and "note_decision" in G and "allow_session" in G)
    M = open(os.path.join(ROOT, "agent", "version_matrix.py"), encoding="utf-8").read()
    ok("能力矩阵有写回/读取表态的入口", "def note_decision(" in M and "def decisions(" in M)
    _seg = M.split("def note_decision(")[1].split("\ndef ")[0]        # 只看这一个函数体
    ok("写回用的是原子写（复用 save）", "save(data, path)" in _seg, "%d 字节" % len(_seg))
    print("\n通过 %d / 失败 %d" % (PASS, FAIL))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

sys.exit(1 if FAIL else 0)
