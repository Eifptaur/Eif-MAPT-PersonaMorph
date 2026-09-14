# -*- coding: utf-8 -*-
"""版本能力矩阵 判据：**"只测了部分能力"不许把整对点亮**（2026-09-14 由测机报告推动）。

原来的病（测机报告 §三 P1）：
  `merge_runs` 对同版本对**整体覆盖**，`gate()` 只要看到有 run 就 `measured=True`
  ⇒ 只实测 1 项（还是失败的那项）也会把**整对**标成"已实测"，安全门不再提示、不再等用户放行。
本判据钉住：
  ① `merge_runs` **按能力逐项合并**（第二次只测 send_image 不许丢掉第一次的 send_text 结论）
  ② `gate()` 三态：measured / partial / 无记录，且部分实测 ⇒ **measured 必须仍为 False**
  ③ 必需能力集（`REQUIRED_CAPS`）口径可见、缺失项能报出来
用法：py -3 scripts/version_matrix_selftest.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import version_matrix as VM  # noqa: E402

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

print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
