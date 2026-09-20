# -*- coding: utf-8 -*-
"""判据：会话档案读失败**不许**用空壳覆盖原文件（第四轮审计 V-R4-6，P1）。

跑法： runtime\\python\\python.exe scripts\\store_archive_selftest.py   退出码 0=全过 / 1=有失败

为什么（审计实测）：`store._load_chat` 无论什么失败都返回空壳，调用方随后一保存就
用"空壳 + 新条目"**覆盖掉原文件** ⇒ 8 条历史 165B → 1 条 279B，**静默丢数据**
（用户只会觉得"它把我之前的记录清了"）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import store as store_mod                                          # noqa: E402
from agent.store import ChatStore                                             # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def main():
    tmp = tempfile.mkdtemp(prefix="pm_store_")
    _keep = store_mod.MESSAGES_DIR
    store_mod.MESSAGES_DIR = tmp
    try:
        def _w(chat_key, obj_text):
            p = os.path.join(tmp, chat_key.replace(":", "_") + ".json")
            with open(p, "w", encoding="utf-8") as f:
                f.write(obj_text)
            return p

        # ── ① 正常档：8 条历史 + 追加一条 ⇒ 9 条（阳性对照，别把正常路也堵了）──
        st1 = ChatStore()
        for i in range(8):
            st1.append_incoming("group:g1", 100 + i, 1000 + i, "wxid_a", "甲", "第 %d 条" % i)
        st1b = ChatStore()
        st1b.append_incoming("group:g1", 108, 2000, "wxid_a", "甲", "新的那条")
        n = len(ChatStore().recent("group:g1", limit=50))
        ok("① 正常档：8 条历史 + 1 条新消息 = 9 条（没有被吃掉）", n == 9, n)

        # ── ② 内容坏了（JSON 截断）⇒ 原文件必须**被隔离保下来**，新档可用 ──
        _w("group:bad", '{"chat_key": "group:bad", "next_local_id": 9, "messages": [{"id": 1, "text": "老数据"')
        st2 = ChatStore()
        st2.append_incoming("group:bad", 900, 9000, "wxid_b", "乙", "坏档之后的新消息")
        _q = [f for f in os.listdir(tmp) if ".corrupt-" in f]
        ok("② 坏档被**隔离**成 *.corrupt-<时间戳>.json（数据没丢，可人工恢复）", len(_q) == 1, _q)
        _qtxt = ""
        if _q:
            with open(os.path.join(tmp, _q[0]), encoding="utf-8") as f:
                _qtxt = f.read()
        ok("② 隔离文件里就是**原来那段内容**（不是空壳）",
           "老数据" in _qtxt, _qtxt[:60])
        ok("② 坏档之后新消息仍能入档（不因为一次坏档就整个群不干活）",
           len(ChatStore().recent("group:bad", limit=10)) == 1)

        # ── ③ 读不动（IO 错）⇒ **一个字节都不许写**，那条不入档 ──
        _p3 = _w("group:locked", json.dumps({"chat_key": "group:locked", "next_local_id": 3,
                                             "messages": [{"id": 1, "text": "要紧的历史"},
                                                          {"id": 2, "text": "第二条"}]},
                                            ensure_ascii=False))
        # `store.py` 用的是**内置** `open` ⇒ 要打桩就打在 builtins 上（模块里没有 `open` 这个名字）
        import builtins                                                       # noqa: E402

        _orig_open = builtins.open

        def _boom(path, *a, **k):
            if str(path).endswith("locked.json"):
                raise PermissionError("[Errno 13] Permission denied（夹具）")
            return _orig_open(path, *a, **k)

        builtins.open = _boom
        try:
            st3 = ChatStore()
            st3.append_incoming("group:locked", 901, 9100, "wxid_c", "丙", "读不动的档")
        finally:
            builtins.open = _orig_open
        with open(_p3, encoding="utf-8") as f:
            _after = f.read()
        ok("③ 读不动时**原文件逐字节不变**（没被空壳覆盖）", "要紧的历史" in _after and "读不动的档" not in _after,
           _after[:80])
        ok("③ 读不动时那条**不入档**（fail-closed，且日志里有话）",
           len(ChatStore().recent("group:locked", limit=10)) == 2)

        # ── ④ 文件不存在＝新会话 ⇒ 正常空壳（合法路径，不许拦）──
        st4 = ChatStore()
        st4.append_incoming("group:fresh", 902, 9200, "wxid_d", "丁", "第一条")
        ok("④ 文件不存在（新会话）照常建档", len(ChatStore().recent("group:fresh", limit=10)) == 1)

        # ── ⑤ 静态：不许再出现"吞掉所有异常后返回空壳"的老写法 ──
        _src = open(os.path.join(ROOT, "agent", "store.py"), encoding="utf-8").read()
        _body = _src[_src.find("def _load_chat("):]
        _body = _body[:_body.find("def _save_chat(")]
        ok("⑤ `_load_chat` 里有 `_loadFailed`（读不动 ⇒ 上层拒写）", "_loadFailed" in _body)
        ok("⑤ `_load_chat` 里有隔离动作（`corrupt-`）与 `os.replace`", "corrupt-" in _body and "os.replace(" in _body)
        ok("⑤ `_save_chat` 见到 `_loadFailed` 就**直接返回、不写盘**",
           "_loadFailed" in _src[_src.find("def _save_chat("):][:600])
        # 反例锚：老写法（一把 pass 之后返回空壳）必须被判不合格
        _OLD = ('def _load_chat(chat_key):\n'
                '    try:\n'
                '        with open(chat_file(chat_key)) as f:\n'
                '            parsed = json.load(f)\n'
                '        if isinstance(parsed, dict):\n'
                '            return parsed\n'
                '    except Exception:\n'
                '        pass\n'
                '    return {"chat_key": chat_key, "messages": []}\n')
        ok("⑥ 反例锚：老写法（except pass ⇒ 返回空壳）确实会被判不合格",
           ("_loadFailed" not in _OLD) and ("corrupt-" not in _OLD) and ("except Exception:\n        pass" in _OLD))
    finally:
        store_mod.MESSAGES_DIR = _keep
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
