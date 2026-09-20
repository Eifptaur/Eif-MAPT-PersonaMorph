# -*- coding: utf-8 -*-
"""第 24 条判据：「删运行明细」必须**同时删掉那一段对话历史**（作者 2026-09-18 定的口径）。

跑法： py -3 scripts\\history_prune_selftest.py      退出码 0=全过 / 1=有失败

作者原话：「**就应该删的是历史啊**，因为我删运行明细那个地方，就是删他回了什么。也就是说，
我删明细就等于我想要删历史，就等于我想删掉『**我说什么而他回什么**』的这一段。怎么能只能删明细呢？
这就是错的，**从根上就是错的**，赶紧修啊」
· 事实：模型的上下文来自 `data/messages/<会话>.json`（`store.recent()`），而控制台原来删的
  `data/sessions/*.jsonl` **只是运行日志** ⇒ 只删它等于没删（现场：作者把运行明细删光，
  机器人照样叫他"复读机"）。
本判据钉四件：①窗口算得对（老口径，保留）②**归属法**：只删"归属于被删轮次"的存档条（且**不做清空**）
③撤销能把存档还原 ④控制台两条路都接了它。
⭐ 第二版关键回归（2026-09-18 真机场景）：**留下来的那一轮的回复不许被删**——
   轮 07:00:00 留下 · 轮 07:00:38 被删，而"轮 07:00:00 的回复"落在 07:00:36（在两轮之间）⇒
   老窗口法会把它一起删掉（出现"只有我问、没有他答"），归属法按"谁发的"判，正确留下。
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

    # ② 归属法：只删"归属于被删轮次"的存档条（窗口外一条都不许动；空 ids 不做清空）
    base = hp.iso_to_ms("2026-09-18T07:10:00")
    store = _FakeStore({"g1": [
        {"id": "kept-reply", "ts": str(base - 40_000), "self": True},    # 07:09:20 上一轮（留下）的回复
        {"id": "trig", "ts": str(base - 10_000), "self": False},         # 07:09:50 这一轮的触发语
        {"id": "reply", "ts": str(base + 20_000), "self": True},         # 07:10:20 这一轮的回复
        {"id": "later", "ts": str(base + 600_000), "self": False},       # 07:20:00 这一轮之后
    ]})
    res = hp.prune_for_deleted_runs(store, all_e, [all_e[2]], trash_root="")
    ok("② 只删归属于被删那轮的 3 条，上一轮（留下）的回复一条不动",
       sorted(store.deleted) == ["later", "reply", "trig"], store.deleted)
    ok("② 回显统计对得上", res.get("removed") == 3 and res.get("chats", {}).get("g1") == 3, res)

    # ②b 没有归属条目 ⇒ 不调删除（更不许"清空"）
    store2 = _FakeStore({"g1": [{"id": "x", "ts": "1"}]})
    res2 = hp.prune_for_deleted_runs(store2, all_e, [all_e[2]], trash_root="")
    ok("②b 没有归属条目 ⇒ 一条都不删（绝不做清空）",
       not store2.deleted and res2.get("removed") == 0, store2.deleted)

    # ②c ⭐ 第二版关键回归：留下的那轮的回复不许被删（老窗口法在这里是错的）
    runs = [{"chat_key": "g1", "ts": "2026-09-18T07:00:00"},
            {"chat_key": "g1", "ts": "2026-09-18T07:00:38"},     # 被删
            {"chat_key": "g1", "ts": "2026-09-18T07:01:30"},     # 被删
            {"chat_key": "g1", "ts": "2026-09-18T07:15:00"}]
    _b = hp.iso_to_ms("2026-09-18T07:00:00")
    store3 = _FakeStore({"g1": [
        {"id": "t0", "ts": str(_b - 1_000), "self": False},          # 06:59:59 留下的那轮触发语
        {"id": "r0", "ts": str(_b + 36_000), "self": True},          # 07:00:36 留下的那轮回复 ⭐
        {"id": "pat", "ts": str(_b + 74_000), "self": False},        # 07:01:14 拍一拍（近下一轮）
        {"id": "r1", "ts": str(_b + 111_000), "self": True},         # 07:01:51 被删轮的回复
        {"id": "old", "ts": "2026-09-18T05:00:00", "self": False},   # 两小时前、离第一轮太远 ⇒ 不归任何轮
    ]})
    hp.prune_for_deleted_runs(store3, runs, [runs[1], runs[2]], trash_root="")
    ok("②c 留下的那轮的回复（07:00:36）不许被删", "r0" not in store3.deleted, store3.deleted)
    ok("②c 留下的那轮的触发语（06:59:59）也要留下", "t0" not in store3.deleted, store3.deleted)
    ok("②c 被删那两轮的消息（拍一拍 / 它的回复）要删掉",
       sorted(store3.deleted) == ["pat", "r1"], store3.deleted)
    ok("②c 离得太远的孤儿条（05:00:00）不归任何轮 ⇒ 不动它", "old" not in store3.deleted)
    _w = hp.run_history_windows(runs, [runs[1]])["g1"][0]
    ok("②c 对照：老窗口法确实会覆盖 07:00:36（＝这次改口径的原因）",
       _w[0] <= _b + 36_000 <= _w[1], _w)

    # ③ 撤销：**真跑一遍生产者 → 消费者**（不许再手写夹具 —— 第五轮审计 V-R5B-1 正是被手写夹具漏掉的：
    #    生产者写 `x.json.json.<stamp>`、消费者只剥 `.stamp` ⇒ 撤销"报成功"但档案一个字节都没回来）
    tmp = tempfile.mkdtemp(prefix="hp-judge-")
    from agent import store as _st
    old_dir = _st.MESSAGES_DIR
    try:
        _st.MESSAGES_DIR = os.path.join(tmp, "messages")
        os.makedirs(_st.MESSAGES_DIR, exist_ok=True)
        trash = os.path.join(tmp, "_trash", "messages")
        stamp = "20260918-071500"
        fn = os.path.basename(_st.chat_file("g1"))
        _body = '{"chat_key":"g1","next_local_id":9,"messages":[{"id":"1","text":"我说什么","ts":"1"}]}'
        with open(os.path.join(_st.MESSAGES_DIR, fn), "w", encoding="utf-8") as f:
            f.write(_body)
        store4 = _FakeStore({"g1": [{"id": "reply", "ts": str(base + 20_000), "self": True}]})
        _res4 = hp.prune_for_deleted_runs(store4, all_e, [all_e[2]], trash_root=trash, stamp=stamp)
        _back = sorted(os.listdir(trash)) if os.path.isdir(trash) else []
        ok("③ 生产者备份出来的名字是 `<档案>.json.<stamp>`（**不是** `.json.json.<stamp>`）",
           _back == [fn + "." + stamp], _back)
        ok("③ 生产者真备份了（`backed` 有它，且内容＝原档案）",
           bool(_res4.get("backed")) and "我说什么" in open(_res4["backed"][0], encoding="utf-8").read(),
           _res4.get("backed"))
        with open(os.path.join(_st.MESSAGES_DIR, fn), "w", encoding="utf-8") as f:
            f.write('{"chat_key":"g1","next_local_id":1,"messages":[]}')      # 模拟删完之后档案被重写
        n = hp.restore_history(trash, stamp)
        _txt = open(os.path.join(_st.MESSAGES_DIR, fn), encoding="utf-8").read()
        ok("③ 撤销**真的把档案还原回来**（端到端；内容回来了，不是只把「撤销成功」写在界面上）",
           n == 1 and "我说什么" in _txt, (n, _txt[:60]))
        ok("③ 还原后备份被清掉（不留垃圾）", not os.path.exists(os.path.join(trash, fn + "." + stamp)))
        # 老格式（多一个 .json）也要能撤回来 —— 升级前存下的那份不许失联
        os.makedirs(trash, exist_ok=True)
        with open(os.path.join(trash, fn + ".json." + stamp), "w", encoding="utf-8") as f:
            f.write('{"chat_key":"g1","next_local_id":9,"messages":[{"id":"1","text":"老备份","ts":"1"}]}')
        n2 = hp.restore_history(trash, stamp)
        ok("③ 老格式备份（`x.json.json.<stamp>`）也能还原到 `x.json`（老数据不失联）",
           n2 == 1 and "老备份" in open(os.path.join(_st.MESSAGES_DIR, fn), encoding="utf-8").read(), n2)
        ok("③ 换个 stamp 撤不到东西（不会误还原）", hp.restore_history(trash, "19700101-000000") == 0)
        ok("③ 反例锚：老生产者的名字（多一个 `.json`）确实不是现在备份出来的名字",
           (fn + ".json." + stamp) != (fn + "." + stamp))
        # ⛔ V-R5R-2（第五轮回执）：备份是老名字、而档案已经用上新命名 ⇒ 还原必须写回**当前在用的**名字，
        #   否则界面报"已恢复了 1 个会话的对话历史"，模型读到的**一条都没回来**。
        os.makedirs(trash, exist_ok=True)
        with open(os.path.join(trash, "g1.json." + stamp), "w", encoding="utf-8") as f:
            f.write('{"chat_key":"g1","next_local_id":9,"messages":[{"id":"1","text":"老名字备份","ts":"1"}]}')
        with open(os.path.join(_st.MESSAGES_DIR, fn), "w", encoding="utf-8") as f:
            f.write('{"chat_key":"g1","next_local_id":1,"messages":[]}')      # 新命名档案先清空
        n3 = hp.restore_history(trash, stamp)
        _txt3 = open(os.path.join(_st.MESSAGES_DIR, fn), encoding="utf-8").read()
        ok("V-R5R-2 老名字的备份也还原到**当前在用的档案**（新命名优先 ⇒ 模型读得到）",
           n3 == 1 and "老名字备份" in _txt3, (n3, _txt3[:60]))
        ok("V-R5R-2 反例锚：老名字路径与新命名路径**确实是两个文件**（写错就没人读）",
           os.path.join(_st.MESSAGES_DIR, "g1.json") != _st.chat_file("g1"))
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
