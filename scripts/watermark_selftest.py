"""W2 判据：监听水位的落盘 / 成功才推进 / 失败重试留痕 / 每会话串行（不需要微信在跑）。

跑法：  py -3 scripts/watermark_selftest.py      退出码 0=全过 / 1=有失败
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import listener_watermark as lw  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def items(*seqs):
    return [{"mid": "m%d" % s, "sort_seq": s, "text": "t%d" % s} for s in seqs]


def main():
    tmp = tempfile.mkdtemp(prefix="wm-judge-")
    wm_path = os.path.join(tmp, "listener_watermark.json")
    dl_path = os.path.join(tmp, "listener_failed.jsonl")
    logs = []
    log = lambda lvl, fmt, *a: logs.append((lvl, fmt % a if a else fmt))

    print("== A. Watermark: 落盘 / 原子写 / 只前进 ==")
    wm = lw.Watermark(wm_path)
    ok("初始为空", wm.get("group:a") == 0)
    wm.set("group:a", 100)
    ok("flush 后文件存在且是合法 JSON", wm.flush() and os.path.exists(wm_path) and isinstance(json.load(open(wm_path, encoding="utf-8")), dict))
    ok("原子写不残留 .tmp", not os.path.exists(wm_path + ".tmp"))
    ok("重启后读回旧值（不跳 latest）", lw.Watermark(wm_path).get("group:a") == 100)
    wm.set("group:a", 50)
    ok("默认只前进、不许回退（防重复处理）", wm.get("group:a") == 100)
    wm.set("group:a", 50, forward_only=False)
    ok("显式 reset 才能回退", wm.get("group:a") == 50)

    print("== B. process_batch: 成功才推进 ==")
    wm2 = lw.Watermark(wm_path)
    wm2.set("group:b", 0, forward_only=False)
    seen = []

    def ok_handler(it):
        seen.append(it["sort_seq"])
        return {"id": it["sort_seq"]}          # 非空 dict ＝ 成功（对齐 store.append_incoming 的返回）

    st = lw.process_batch("group:b", items(1, 2, 3), ok_handler, wm2, log=log, deadletter_path=dl_path, sleep=lambda s: None)
    ok("三条全成功 ⇒ processed=3 且水位推进到最后一条", st["processed"] == 3 and st["advanced_to"] == 3, st)
    ok("每条都落盘（处理完文件里就是最新水位）", lw.Watermark(wm_path).get("group:b") == 3)

    print("== C. 失败重试 3 次：中途转成功 ==")
    logs.clear()
    calls = {"n": 0}

    def flaky(it):
        calls["n"] += 1
        if it["sort_seq"] == 2 and calls["n"] < 3:
            raise RuntimeError("落库失败")
        return {"ok": True}

    st = lw.process_batch("group:c", items(1, 2), flaky, wm2, log=log, deadletter_path=dl_path, sleep=lambda s: None)
    ok("重试后成功 ⇒ processed=2、retried>=1", st["processed"] == 2 and st["retried"] >= 1, st)
    ok("重试有留痕（日志里带 第 n/3 次 与原因）", any("重试" in m or "次处理失败" in m for _, m in logs), logs[:1])

    print("== D. 永久失败：dead-letter + 越过毒消息（不卡队列） ==")
    logs.clear()
    if os.path.exists(dl_path):
        os.remove(dl_path)
    wm3 = lw.Watermark(wm_path)
    wm3.set("group:d", 0, forward_only=False)

    def poison(it):
        if it["sort_seq"] == 2:
            raise RuntimeError("永远失败的毒消息")
        return {"ok": True}

    st = lw.process_batch("group:d", items(1, 2, 3), poison, wm3, log=log, deadletter_path=dl_path, sleep=lambda s: None)
    ok("毒消息计 failed=1，其余照样处理", st["failed"] == 1 and st["processed"] == 2, st)
    ok("水位越过毒消息（advanced_to=3，不会永远卡在第 2 条）", st["advanced_to"] == 3)
    ok("dead-letter 落了这条（含 mid/seq/attempts/error）", os.path.exists(dl_path) and json.loads(open(dl_path, encoding="utf-8").readline())["mid"] == "m2")
    ok("日志明确写了『已越过』", any("越过" in m for _, m in logs))

    print("== E. 重放保护 + 异常不打断整批 ==")
    wm4 = lw.Watermark(wm_path)
    wm4.set("group:e", 5, forward_only=False)
    ran = []
    st = lw.process_batch("group:e", items(4, 5, 6), lambda it: ran.append(it["sort_seq"]) or True, wm4, log=log, sleep=lambda s: None)
    ok("水位之前的消息被跳过（不重放）", st["skipped"] == 2 and ran == [6], (st, ran))

    print("== F. 每会话串行（并发调用者排队） ==")
    wm5 = lw.Watermark(wm_path)
    wm5.set("group:f", 0, forward_only=False)
    cur, peak = {"n": 0}, {"n": 0}
    guard = threading.Lock()

    def slow(it):
        with guard:
            cur["n"] += 1
            peak["n"] = max(peak["n"], cur["n"])
        time.sleep(0.05)
        with guard:
            cur["n"] -= 1
        return True

    ts = [threading.Thread(target=lw.process_batch, args=("group:f", items(1, 2, 3), slow, wm5), kwargs={"sleep": lambda s: None}) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ok("同一会话同一时刻只有一个处理者（peak=1）", peak["n"] == 1, "peak=%d" % peak["n"])
    ok("并发下水位仍单调到最后", wm5.get("group:f") == 3, wm5.get("group:f"))

    # ── 2026-09-17（用户问「我把聊天记录清空了，它会不会学不会、从而不发」）──
    #    水位只前进不回退是对的（防重复处理），但**微信清空记录后序号可能回落/换库** ⇒ 新消息会被
    #    判成"处理过"而永远跳过，而且**重启也救不回**（水位是从文件读回来的）⇒ 监听循环里必须有自愈。
    print("\n-- G. 记录被清空后的水位自愈（源码级，防以后被顺手删掉） --")
    _pm = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona_morph.py"),
               encoding="utf-8").read()
    ok("监听循环里有自愈限频表 `_wm_heal`", "_wm_heal = {}" in _pm and "_wm_heal.get(wxid" in _pm)
    ok("判据是「最新序号**低于**水位」（正常运行时不会成立 ⇒ 不误触发）",
       "_latest < _cur" in _pm and "latest_seq(wxid)" in _pm)
    ok("回退走显式 `forward_only=False`（默认只前进，不许悄悄退）",
       "wm.set(chat_key, _latest, forward_only=False)" in _pm)
    ok("自愈要落盘 + 留日志（否则用户永远不知道为什么它不回）",
       "wm.flush()" in _pm and "记录像是被清过" in _pm)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n== W2 水位判据：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
