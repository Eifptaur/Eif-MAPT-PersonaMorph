# -*- coding: utf-8 -*-
"""版本能力矩阵 判据：**"只测了部分能力"不许把整对点亮**。

原来的病（测机报告 §三 P1）：
  `merge_runs` 对同版本对**整体覆盖**，`gate()` 只要看到有 run 就 `measured=True`
  ⇒ 只实测 1 项（还是失败的那项）也会把**整对**标成"已实测"，安全门不再提示、不再等用户放行。
本判据钉住：
  ① `merge_runs` **按能力逐项合并**（第二次只测 send_image 不许丢掉第一次的 send_text 结论）
  ② `gate()` 三态：measured / partial / 无记录，且部分实测 ⇒ **measured 必须仍为 False**
  ③ 必需能力集（`REQUIRED_CAPS`）口径可见、缺失项能报出来
用法：py -3 scripts/version_matrix_selftest.py
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

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


W, A = "9.9.9", "0.0.0"

print("── A. 完全没有记录 ⇒ 未实测 ──")
g = VM.gate({"schema": 1, "runs": []}, W, A)
ok("measured=False", g["measured"] is False)
ok("partial=False（不是部分，是压根没测）", g["partial"] is False)
ok("missing 报出必需能力集", list(g["missing"]) == list(VM.REQUIRED_CAPS), str(g["missing"]))

print("── B. 【核心】只测了非必需能力 ⇒ 仍然按未实测处理 ──")
d = VM.merge_runs({"schema": 1, "runs": []},
                  {"wechat": W, "adapter": A, "caps": {"image_send": {"status": "ok", "evidence": "实测"}}})
g = VM.gate(d, W, A)
ok("有 run 了 ⇒ partial=True", g["partial"] is True)
ok("但 measured **仍为 False**（安全门不许被点亮）", g["measured"] is False, str(g["measured"]))
ok("missing 指出还缺 send_text", "send_text" in g["missing"], str(g["missing"]))
ok("advice 里写明「只实测了部分能力」", "部分能力" in g["advice"], g["advice"][:48])

print("── C. 必需能力测了但是 unknown ⇒ 仍不算实测 ──")
d2 = VM.merge_runs(d, {"wechat": W, "adapter": A,
                       "caps": {"send_text": {"status": "unknown", "evidence": "没测出结论"}}})
g2 = VM.gate(d2, W, A)
ok("unknown 不算结论 ⇒ measured=False", g2["measured"] is False and "send_text" in g2["missing"])

print("── D. 必需能力有结论（哪怕结论是 no）⇒ 算实测过 ──")
d3 = VM.merge_runs(d2, {"wechat": W, "adapter": A,
                        "caps": {"send_text": {"status": "no", "evidence": "实测发不出去"}}})
g3 = VM.gate(d3, W, A)
ok("measured=True（结论是 no 也算「测过了」）", g3["measured"] is True, g3["advice"][:40])

print("── E. 逐能力合并：后来的 run 不许丢掉先前的结论 ──")
d4 = VM.merge_runs({"schema": 1, "runs": []},
                   {"wechat": W, "adapter": A, "caps": {"send_text": {"status": "ok", "evidence": "第一次"}}})
d5 = VM.merge_runs(d4, {"wechat": W, "adapter": A,
                        "caps": {"image_send": {"status": "ok", "evidence": "第二次"}}})
caps = VM.capabilities(d5, W, A)
ok("send_text 的结论还在（没被整体覆盖）", caps["send_text"]["status"] == "ok", caps["send_text"]["evidence"][:20])
ok("image_send 也在", caps["image_send"]["status"] == "ok")
run = VM.find_run(d5, W, A) or {}
ok("scope 记下了这一对测过哪些能力", set(run.get("scope") or []) >= {"send_text", "image_send"}, str(run.get("scope")))
ok("同一个版本对只有一条 run（不重复追加）", len([r for r in d5["runs"] if r.get("wechat") == W]) == 1)

print("── F. 向后兼容：老数据没有 scope 字段也能用 ──")
old = {"schema": 1, "runs": [{"wechat": W, "adapter": A, "when": "2026-01-01",
                              "caps": {"send_text": {"status": "ok", "evidence": "老记录"}}}]}
g4 = VM.gate(old, W, A)
ok("老 run（无 scope）⇒ 仍能判 measured=True", g4["measured"] is True, str(g4.get("scope")))

print("── G. 事实键：同一个版本号的 UI 变了也要认出来（2026-09-22 兼容性落地第 ⑦ 项）──")
_fk = str(VM.fact_key(A))
ok("fact_key() 不抛、且带上适配层（没有微信在跑时也能返回）", A in _fk, _fk[:60])
_facts = "clsX|dpi2|" + A
_d7 = VM.merge_runs({"schema": 1, "runs": []},
                    {"wechat": W, "adapter": A, "facts": _facts,
                     "caps": {"send_text": {"status": "ok", "evidence": "同一台机器实测"}}})
ok("记录里带上了 facts 事实键", (VM.find_run_facts(_d7, _facts) or {}).get("facts") == _facts)
_g_facts = VM.gate(_d7, "9.9.9.9", A, facts=_facts)
ok("按事实键查得到 ⇒ measured=True 且 basis=facts（**版本号对不上也算实测过**）",
   _g_facts["measured"] is True and _g_facts.get("basis") == "facts",
   "basis=%s" % _g_facts.get("basis"))
_g_ver = VM.gate(_d7, W, A, facts="另一个指纹|dpi2|" + A)
ok("事实键对不上、版本键对得上 ⇒ basis=version（只作参考，并说得出来）",
   _g_ver["measured"] is True and _g_ver.get("basis") == "version"
   and "只作参考" in str(_g_ver.get("basis_note") or ""), "basis=%s" % _g_ver.get("basis"))
_g_none = VM.gate(_d7, "8.8.8.8", A, facts="没见过的指纹|" + A)
ok("两把键都对不上 ⇒ basis=none 且 measured=False",
   _g_none["measured"] is False and _g_none.get("basis") == "none")
ok("向后兼容：不传 facts 时行为和以前一样（basis 退成 version）",
   VM.gate(_d7, W, A)["measured"] is True and VM.gate(_d7, W, A).get("basis") == "version")

print("── H. ★ 实机登记锚：本机当前那一对（微信 4.1.15.13 × 适配层 1.2.2.2）必须真记进去 ──")
# 为什么钉这条：微信会自动更新（本机从 4.1.13.65 → 4.1.15.8 → 4.1.15.13）。
# 每换一次版本，矩阵里就**多一对 unknown**；而 `capabilities()` 对没测过的版本对返回全 unknown，
# 控制台就会显示「14 项全部未实测」。**这不是 bug，是没登记**（见 version_matrix 铁律）。
# ⇒ 真机跑过一次真发送（probe_full_send.py，绿占比判据：入框 0.4442 / 发后归 0）之后，
#    必须把结论写回矩阵、且**判据要能证明它写进去了**，否则下次又会被当成"炸了"。
_REAL = json.load(open(os.path.join(ROOT, "data", "capability_matrix.json"), encoding="utf-8"))
_runs = {str(r.get("wechat")): r for r in (_REAL.get("runs") or [])}
ok("矩阵里存在 4.1.15.13 的实测记录", "4.1.15.13" in _runs, str(sorted(_runs.keys())))
_r13 = _runs.get("4.1.15.13") or {}
ok("这条记录的 send_text 结论是 ok（不是 unknown）",
   str(((_r13.get("caps") or {}).get("send_text") or {}).get("status")) == "ok",
   str(((_r13.get("caps") or {}).get("send_text") or {}).get("status")))
ok("这条记录带 **facts 事实键**（主窗类名/DPI/适配层）⇒ 以后 UI 变了也认得出",
   bool(_r13.get("facts")) and "|" in str(_r13.get("facts")), str(_r13.get("facts")))
ok("这条记录的证据里写明了判定依据（绿占比）",
   "绿占比" in str(((_r13.get("caps") or {}).get("send_text") or {}).get("evidence")),
   str(((_r13.get("caps") or {}).get("send_text") or {}).get("evidence"))[:60])
_g13 = VM.gate(_REAL, "4.1.15.13", "1.2.2.2")
ok("⇒ 这一对版本的门是 **measured=True**（不再是 unknown）", _g13["measured"] is True, str(_g13.get("basis")))
ok("旧版本 4.1.15.8 的记录没被挤掉（逐能力合并、不整对覆盖）", "4.1.15.8" in _runs, str(sorted(_runs.keys())))

print("\n== 出厂种子：已实测的版本对**随包走**，且**只补空缺** ==")
# 为什么要有这一段：记录原来只在本机 `data/capability_matrix.json` 里 ⇒
#   换一台机器（哪怕版本对**已经真机核过**）第一次运行仍报「没实测 ⇒ 出横幅 + 降级」。
#   种子解决这件事；但方向不许反：**本机结论永远压过历史种子**（否则会把"本机发不出去"盖成"能用"）。
import tempfile as _tf  # noqa: E402

_pd = _tf.mkdtemp(prefix="vmseed-")
_p1 = os.path.join(_pd, "m1.json")
with open(_p1, "w", encoding="utf-8") as _f:
    json.dump({"schema": 1, "runs": [{"wechat": "4.1.15.8", "adapter": "1.2.2.2",
                                     "when": "2026-09-13",
                                     "caps": {"send_text": {"status": "ok", "evidence": "x"}}}]}, _f)
_d1 = VM.load(_p1)
ok("G1★ 本机只记了别的版本对 ⇒ **出厂种子垫进来**（第二台机器那一对到位）",
   any(r.get("wechat") == "4.1.13.65" and r.get("adapter") == "1.2.4.4" for r in _d1["runs"]),
   str([(r.get("wechat"), r.get("adapter")) for r in _d1["runs"]]))
ok("G2★ 于是那一对的门是 **measured=True**（装了这对版本的机器不再白报未实测）",
   VM.gate(_d1, "4.1.13.65", "1.2.4.4")["measured"] is True, "")
ok("G2b 反向锚：**没记录**的一对仍然判未实测（种子不许把门变松）",
   VM.gate(_d1, "4.1.20.0", "9.9.9.9")["measured"] is False, "")
_p2 = os.path.join(_pd, "m2.json")
with open(_p2, "w", encoding="utf-8") as _f:
    json.dump({"schema": 1, "runs": [{"wechat": "4.1.13.65", "adapter": "1.2.4.4",
                                     "when": "2026-10-06",
                                     "caps": {"send_text": {"status": "no", "evidence": "本机发不出去"}}}]}, _f)
_c2 = VM.capabilities(VM.load(_p2), "4.1.13.65", "1.2.4.4")
ok("G3★ 反向锚：**本机说不可用**的能力，种子**不许**把它盖成可用（合并方向只许向下）",
   str((_c2.get("send_text") or {}).get("status")) == "no", str(_c2.get("send_text")))
ok("G3b 种子只补**空缺**（本机没测过的那几项由种子补上）",
   str((_c2.get("image_send") or {}).get("status")) == "ok", str(_c2.get("image_send")))
# 诚实锚：种子里的结论**不许**超出真测过的范围
_seed_new = next((r for r in VM.SEED_RUNS
                  if r.get("wechat") == "4.1.13.65" and r.get("adapter") == "1.2.4.4"), {})
_scaps = _seed_new.get("caps") or {}
ok("G4★ 诚实锚：那台机**没拿到结论**的两项（切会话 / UIA）在种子里必须是 unknown",
   str((_scaps.get("switch_chat") or {}).get("status")) == "unknown"
   and str((_scaps.get("uia_tree") or {}).get("status")) == "unknown",
   str({k: (v or {}).get("status") for k, v in _scaps.items()})[:90])
ok("G4b 诚实锚：最小化档那条写的是**逐类**（五类都进库），不是只写文本",
   "逐类" in str((_scaps.get("minimized_send") or {}).get("evidence"))
   and "语音" in str((_scaps.get("minimized_send") or {}).get("evidence")), "")

print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
