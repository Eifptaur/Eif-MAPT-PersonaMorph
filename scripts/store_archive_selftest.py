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
import re
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

        # ── ⑦ S-1：不同 chat_key 不许撞同一个档案文件（第四轮审计候选）──
        #    老实现只做字符替换 ⇒ `group:wxid_a-b` 与 `group:wxid_a_b` 撞成一个文件、互相覆盖。
        _k1, _k2 = "group:wxid_a-b", "group:wxid_a_b"
        ok("⑦ 反例锚：老归一化对这两个 key 确实同名（不靠哈希就区分不开）",
           store_mod._safe_name(_k1) == store_mod._safe_name(_k2), store_mod._safe_name(_k1))
        ok("⑦ 两个 key 的档案路径必须不同",
           store_mod.chat_file(_k1) != store_mod.chat_file(_k2),
           (os.path.basename(store_mod.chat_file(_k1)), os.path.basename(store_mod.chat_file(_k2))))
        ChatStore().append_incoming(_k1, 1, 1, "wxid_x", "甲", "只在短横线那个会话")
        ChatStore().append_incoming(_k2, 1, 1, "wxid_x", "甲", "只在下划线那个会话")
        _t1 = [m["text"] for m in ChatStore().recent(_k1, limit=10)]
        _t2 = [m["text"] for m in ChatStore().recent(_k2, limit=10)]
        ok("⑦ 两个会话各存各的（互不覆盖 —— 老实现就是在这丢数据）",
           _t1 == ["只在短横线那个会话"] and _t2 == ["只在下划线那个会话"], (_t1, _t2))
        _both = sorted(f for f in os.listdir(tmp)
                       if f.startswith("group_wxid_a_b_") and f.endswith(".json") and ".corrupt-" not in f)
        ok("⑦ 磁盘上是**两个**文件（同名归一 + 不同哈希）", len(_both) == 2, _both)
        _listed = set(ChatStore().list_chats())
        ok("⑦ list_chats 给的是**真 chat_key**，不是带哈希尾巴的幽灵名",
           {_k1, _k2} <= _listed and not [k for k in _listed if re.search(r"_[0-9a-f]{8}$", k)],
           sorted(_listed))
        # 老命名档案（无哈希、且内容里没有 chat_key 字段）仍要能被发现与读到 —— 老数据不许失联
        _w("group:legacy_nohash", json.dumps({"messages": [{"id": 1, "text": "老档案"}]}, ensure_ascii=False))
        _listed7 = set(ChatStore().list_chats())
        ok("⑦ 老命名档案仍能被发现（从文件名推回会话）并读到内容",
           ("group:legacy_nohash" in _listed7)
           and len(ChatStore().recent("group:legacy_nohash", limit=5)) == 1,
           sorted(_listed7))

        # ── ⑧ 第五轮回执 V-R5A-5 / V-R5A-6 / V-R5B-2 / V-R5A-7：迁移 · 幽灵会话 · 结构修复 ──
        _W = "wxid_" + "deadbeef"          # 运行时拼：别让出包 PII 闸门当成真账号
        _w("group:" + _W, json.dumps({"messages": [{"id": 1, "text": "合法尾巴"}]},
                                             ensure_ascii=False))
        ok("⑧ 文件名推导**不许**把合法 wxid 的尾巴削掉（`wxid_xxx` → `group:wxid` 是错的）",
           store_mod._filename_key("group_" + _W + ".json") == "group:" + _W,
           store_mod._filename_key("group_" + _W + ".json"))
        _old_strip = re.sub(r"_[0-9a-f]{8}$", "", "group_" + _W)
        ok("⑧ 反例锚：老实现的正则确实会削成 `group_wxid`（这就是「真会话被弄丢」的来历）",
           _old_strip == "group_wxid", _old_strip)

        _cp = _w("group:cp1", json.dumps({"chat_key": "group:cp1", "messages": []}, ensure_ascii=False))
        _bad_name = os.path.basename(_cp) + ".corrupt-20260921-000000.json"
        with open(os.path.join(tmp, _bad_name), "w", encoding="utf-8") as f:
            f.write("{坏档案")
        _l8 = set(ChatStore().list_chats())
        ok("⑧ 隔离档（`*.corrupt-*.json`）**不许**变成幽灵会话", not any(".corrupt-" in k for k in _l8),
           [k for k in _l8 if ".corrupt-" in k])
        _w("group:repairme", json.dumps({"messages": [{"id": 7, "text": "老档没有 next_local_id"}]},
                                        ensure_ascii=False))
        _st8 = ChatStore()
        _st8.append_incoming("group:repairme", 1, 2, "wxid_z", "丙", "补一条")
        _r8 = ChatStore().recent("group:repairme", limit=10)
        ok("⑧ 缺 `next_local_id`/`chat_key` 的老档：**补齐照常入档**（不再 KeyError 把整个群卡死）",
           len(_r8) == 2 and int(_r8[-1]["id"]) == 8, [(m["id"], m["text"]) for m in _r8])

        # 迁移：按**内容里的 chat_key** 改名到新命名（撞名不搬）
        _legacy_ck = "group:wxid_mig1"
        _lp = _w(_legacy_ck, json.dumps({"chat_key": _legacy_ck, "next_local_id": 2,
                                         "messages": [{"id": 1, "text": "迁移前就在的老档"}]},
                                        ensure_ascii=False))
        _new_path = store_mod.chat_file(_legacy_ck)
        ok("⑧ 迁移前：新命名档案还不存在（这就是「老档串群」的现场）",
           (not os.path.exists(_new_path)) and os.path.exists(_lp), os.path.basename(_lp))
        _mv = store_mod.migrate_legacy_files()
        ok("⑧ 迁移后：老档改名到新命名，内容一条不少",
           os.path.exists(_new_path) and (not os.path.exists(_lp))
           and len(ChatStore().recent(_legacy_ck, limit=5)) == 1, _mv.get("moved"))
        with open(_new_path, encoding="utf-8") as f:
            ok("⑧ 迁移是**改名**不是删+抄（内容原样）", "迁移前就在的老档" in f.read())
        # 撞名：新命名已被占 ⇒ 不许搬（读取路径本来就新命名优先）
        _ck2 = "group:wxid_mig2"
        _lp2 = _w(_ck2, json.dumps({"chat_key": _ck2, "messages": [{"id": 1, "text": "老的那份"}]},
                                   ensure_ascii=False))
        with open(store_mod.chat_file(_ck2), "w", encoding="utf-8") as f:
            f.write(json.dumps({"chat_key": _ck2, "next_local_id": 2,
                                "messages": [{"id": 1, "text": "新的那份"}]}, ensure_ascii=False))
        _mv2 = store_mod.migrate_legacy_files()
        ok("⑧ 新命名已被占 ⇒ **不搬**（只记账；老文件当备份留着，绝不覆盖）",
           os.path.exists(_lp2) and os.path.basename(_lp2) in _mv2.get("dup", []), _mv2.get("dup"))
    finally:
        store_mod.MESSAGES_DIR = _keep
        shutil.rmtree(tmp, ignore_errors=True)

    # ── V-R6-25：老命名残留**不许串群**（老档案里的 chat_key 与请求的不一致 ⇒ 当它不存在）──
    _keep3 = store_mod.MESSAGES_DIR
    _tmp3 = tempfile.mkdtemp(prefix="pm_store_v25_")
    try:
        store_mod.MESSAGES_DIR = _tmp3
        _ka, _kb = "group:甲会话", "group:乙会话"
        # 故意造一个"老命名的文件，内容其实是别的会话"
        _lg = store_mod.chat_file_legacy(_ka)
        with open(_lg, "w", encoding="utf-8") as f:
            f.write(json.dumps({"chat_key": _kb, "next_local_id": 9,
                                "messages": [{"id": 1, "text": "别人的话"}]}, ensure_ascii=False))
        _got = store_mod.chat_file_existing(_ka)
        ok("V-R6-25 老档案里的 chat_key 与请求的不一致 ⇒ **不拿它当这个会话的档案**（防串群）",
           _got == store_mod.chat_file(_ka) and _got != _lg, _got)
        # 正向对照：内容一致的老档案照样能用（老用户不丢档案）
        with open(_lg, "w", encoding="utf-8") as f:
            f.write(json.dumps({"chat_key": _ka, "next_local_id": 1, "messages": []},
                               ensure_ascii=False))
        ok("V-R6-25b 正向对照：chat_key 一致的老档案**照样认**（老用户不丢档案）",
           store_mod.chat_file_existing(_ka) == _lg, store_mod.chat_file_existing(_ka))
    finally:
        store_mod.MESSAGES_DIR = _keep3
        shutil.rmtree(_tmp3, ignore_errors=True)

    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
