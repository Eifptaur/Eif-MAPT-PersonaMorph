# -*- coding: utf-8 -*-
"""第 24 条判据：「删运行明细」必须**同时删掉那一段对话历史**（作者 2026-09-18 定的口径）。

跑法： py -3 scripts\\history_prune_selftest.py      退出码 0=全过 / 1=有失败

作者原话：「**就应该删的是历史啊**，因为我删运行明细那个地方，就是删他回了什么。也就是说，
我删明细就等于我想要删历史，就等于我想删掉『**我说什么而他回什么**』的这一段。怎么能只能删明细呢？
这就是错的，**从根上就是错的**，赶紧修啊」
· 事实：模型的上下文来自 `data/messages/<会话>.json`（`store.recent()`），而控制台原来删的
  `data/sessions/*.jsonl` **只是运行日志** ⇒ 只删它等于没删（现场：作者把运行明细删光，
  机器人照样叫他"复读机"）。
本判据钉三件：①窗口算得对 ②按窗口点名删存档条（且**不做清空**）③撤销能把存档还原。
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import history_prune as hp          # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


class _FakeStore:
    """假 store：只实现本功能用到的两个方法（`recent` / `delete_entries`）。"""

    def __init__(self, by_chat):
        self.by_chat = by_chat
        self.deleted = []
        self.calls = []

    def recent(self, chat_key, limit=80, **kw):
        return list(self.by_chat.get(chat_key, []))

    def delete_entries(self, chat_key, ids):
        self.calls.append((chat_key, list(ids)))
        self.deleted.extend(ids)
        return len(ids)


def main():
    # ① 窗口：同会话「上一条运行记录 → 本条」；没有前驱时回看 30 分钟
    all_e = [{"chat_key": "g1", "ts": "2026-09-18T07:00:00"},
             {"chat_key": "g1", "ts": "2026-09-18T07:05:00"},
             {"chat_key": "g1", "ts": "2026-09-18T07:10:00"},
             {"chat_key": "g2", "ts": "2026-09-18T07:06:00"}]
    w = hp.run_history_windows(all_e, [all_e[2]])
    lo, hi = w.get("g1", [(0, 0)])[0]
    ok("① 删 07:10 那轮 ⇒ 窗口＝(07:05:00, 07:10:05]（含 5s 回复余量）",
       lo == hp.iso_to_ms("2026-09-18T07:05:00") + 1 and hi == hp.iso_to_ms("2026-09-18T07:10:00") + 5000,
       (lo, hi))
    ok("① 别的会话（g2）不受影响", "g2" not in w, list(w.keys()))
    w2 = hp.run_history_windows(all_e, [all_e[0]])
    lo2 = w2["g1"][0][0]
    ok("① 没有前驱 ⇒ 只回看 30 分钟（不把开天辟地以来的历史全清）",
       lo2 == hp.iso_to_ms("2026-09-18T07:00:00") - 30 * 60 * 1000 + 1, lo2)

    # ② 按窗口点名删（只删窗口内的，窗口外一条都不许动；空 ids 不做清空）
    base = hp.iso_to_ms("2026-09-18T07:05:00")
    store = _FakeStore({"g1": [
        {"id": "in-old", "ts": str(base - 10_000)},          # 窗口外（更早）
        {"id": "in-a", "ts": str(base + 1_000)},             # 窗口内（这轮的触发语）
        {"id": "in-b", "ts": str(base + 60_000)},            # 窗口内（它的回复）
        {"id": "out-new", "ts": str(base + 10 * 60_000)},    # 窗口外（更晚）
    ]})
    res = hp.prune_for_deleted_runs(store, all_e, [all_e[2]], trash_root="")
    ok("② 只删窗口内的 2 条（窗口外一条不动）",
       sorted(store.deleted) == ["in-a", "in-b"], store.deleted)
    ok("② 回显统计对得上", res.get("removed") == 2 and res.get("chats", {}).get("g1") == 2, res)

    # ②b 窗口内没有条目 ⇒ 不调删除（更不许"清空"）
    store2 = _FakeStore({"g1": [{"id": "x", "ts": "1"}]})
    res2 = hp.prune_for_deleted_runs(store2, all_e, [all_e[2]], trash_root="")
    ok("②b 窗口内没条目 ⇒ 一条都不删（绝不做清空）",
       not store2.deleted and res2.get("removed") == 0, store2.deleted)

    # ③ 撤销：把 `_trash/messages/<name>.<stamp>` 放回 `MESSAGES_DIR`
    tmp = tempfile.mkdtemp(prefix="hp-judge-")
    from agent import store as _st
    old_dir, old_mk = _st.MESSAGES_DIR, None
    try:
        _st.MESSAGES_DIR = os.path.join(tmp, "messages")
        os.makedirs(_st.MESSAGES_DIR, exist_ok=True)
        trash = os.path.join(tmp, "_trash", "messages")
        os.makedirs(trash, exist_ok=True)
        stamp = "20260918-071500"
        fn = "group_g1.json"
        with open(os.path.join(trash, fn + "." + stamp), "w", encoding="utf-8") as f:
            f.write('{"chat_key":"g1","messages":[{"id":"1","text":"我说什么","ts":"1"}]}')
        n = hp.restore_history(trash, stamp)
        dst = os.path.join(_st.MESSAGES_DIR, fn)
        ok("③ 撤销把存档文件还原回 `MESSAGES_DIR`",
           n == 1 and os.path.exists(dst) and "我说什么" in open(dst, encoding="utf-8").read(),
           (n, os.path.exists(dst)))
        ok("③ 还原后备份被清掉（不留垃圾）",
           not os.path.exists(os.path.join(trash, fn + "." + stamp)))
        ok("③ 换个 stamp 撤不到东西（不会误还原）", hp.restore_history(trash, "19700101-000000") == 0)
    finally:
        _st.MESSAGES_DIR = old_dir
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    # ④ 静态：控制台的删/撤销两条路都接了 history_prune
    ws = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
    ok("④ 控制台「删运行明细」接了 `prune_for_deleted_runs`", "prune_for_deleted_runs" in ws)
    ok("④ 控制台「撤销上次删除」接了 `restore_history`", "restore_history" in ws)
    ok("④ 删除响应带回 `history_removed`（面板能显示「顺带清了几条历史」）",
       "history_removed" in ws)

    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
