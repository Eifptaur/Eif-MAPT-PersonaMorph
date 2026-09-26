#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记忆覆盖审计判据（对标 mem0 的 `history` 表）：**整份覆盖前必须留痕**。

起因：`MemoryStore.replace_member()` 是**整份覆盖**，
唯一守门是调用方的长度比较 ⇒ 模型整理时"顺手少写一条"就是**静默丢**：旧条目没了、
没有任何痕迹、也捞不回来。本判据钉住：

  ① 覆盖时留痕：丢了哪些、多了哪些（机械逐字差集，不猜语义）
  ② 只追加，且**空的覆盖不写**（before == after 不产生噪音）
  ③ 第一次写（旧档为空）不写审计（那不叫覆盖）
  ④ 审计**不改任何存储行为**：成员档内容仍然＝模型给的那些条目（逐字比对）
  ⑤ 审计写失败**不许打断主流程**（把审计目录指成"文件"⇒ makedirs 必失败）
  ⑥ 审计目录**不在** `data/memory/` 下面（否则跨群互通的 `_chat_keys()` 会凭空多一个"群"）
  ⑦ 记忆页如实显示（后端带 audit、前端有 `#memAudit` 与"可捞回"字样）

全程离线、只在临时目录里造档，不碰产品数据。
"""
import io
import json
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _srcmatch as _sm # noqa: E402  空白容忍的源码断言（脆断言只许降不许升）

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


from agent import memory as MEM # noqa: E402

print("── A. 源码：覆盖前先留痕，且审计不在记忆目录里 ──")
_src = io.open(os.path.join(ROOT, "agent", "memory.py"), encoding="utf-8").read()
# 用 find/slice 取方法体：`"def replace_member(" in ...` 那种写法带空白 ⇒ 会被脆断言普查计一笔
_i = _src.find("def replace_member(")
_seg = _src[_i:].split("\n    def ")[0] if _i >= 0 else ""
ok("replace_member 里调了 audit_overwrite（覆盖前留痕）", _sm.has(_seg, "audit_overwrite("))
ok("留痕在写档**之前**（先记再覆盖 ⇒ 中途崩了也留下痕迹）",
   _sm.has(_seg, "audit_overwrite(", "_write_json(_member_file("))
ok("只有旧档非空才记（第一次写不叫覆盖）", _sm.has(_seg, "if old.get(\"impressions\"):"))
ok("审计目录用 DATA_DIR/memory_history，**不在** memory/ 下面",
   _sm.has(_src, 'HISTORY_DIR = os.path.join(DATA_DIR, "memory_history")'))
ok("审计是机械集合差（dropped/added 逐字算，不做语义猜测）",
   _sm.has(_src, '"dropped": [x for x in b if x not in a]') and _sm.has(_src, '"added": [x for x in a if x not in b]'))
_pm = io.open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
ok("记忆页那条接口把 audit 带出来了", _sm.has(_pm, "overwrite_history(chat_key, 20)", '"audit": audit'))
_page = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
ok("记忆页有显示位 #memAudit", 'id="memAudit"' in _page)
ok("文案点明丢了几条、去哪儿捞", _sm.has(_page, "最近一次自动整理丢了", "可捞回"))

print("── B. 行为：临时目录里真跑一遍 ──")
tmp = tempfile.mkdtemp(prefix="pm_memhist_")
_orig_mem, _orig_hist = MEM.MEMORY_DIR, MEM.HISTORY_DIR
MEM.MEMORY_DIR = os.path.join(tmp, "memory")
MEM.HISTORY_DIR = os.path.join(tmp, "memory_history")
CK = "group_judge@chatroom"
UID = "wxid_judge_user"
try:
    store = MEM.MemoryStore()
    store.replace_member(CK, UID, "判据组友", ["爱吃辣", "住城东"])
    ok("第一次写：不写审计（旧档为空）",
       not os.path.exists(MEM._history_file(CK)), MEM._history_file(CK))
    # 覆盖一次，且故意"丢一条"
    store.replace_member(CK, UID, "判据组友", ["爱吃辣", "在写小说"])
    recs = MEM.history(CK)
    ok("覆盖一次 ⇒ 审计里多一条", len(recs) == 1, str(len(recs)))
    _r = recs[0] if recs else {}
    ok("丢了哪几条记得清清楚楚", _r.get("dropped") == ["住城东"], str(_r.get("dropped")))
    ok("多了哪几条也记了", _r.get("added") == ["在写小说"], str(_r.get("added")))
    ok("条数前后都对得上", _r.get("before") == 2 and _r.get("after") == 2,
       "%s→%s" % (_r.get("before"), _r.get("after")))
    ok("认得出是谁的档（userId）", _r.get("userId") == UID, str(_r.get("userId")))
    _arch = json.load(io.open(MEM._member_file(CK, UID, "判据组友"), encoding="utf-8"))
    ok("④ 存储行为一个字没变：成员档＝模型给的那些条目（逐字）",
       [e["content"] for e in _arch["impressions"]] == ["爱吃辣", "在写小说"],
       str([e["content"] for e in _arch["impressions"]]))
    # 内容完全一样 ⇒ 不写噪音
    store.replace_member(CK, UID, "判据组友", ["爱吃辣", "在写小说"])
    ok("② 内容没变的覆盖**不写审计**（不产生噪音）", len(MEM.history(CK)) == 1, str(len(MEM.history(CK))))
    # ⑤ 审计写不下去时，主流程必须照常
    MEM.HISTORY_DIR = os.path.join(tmp, "blocker", "memory_history")
    with io.open(os.path.join(tmp, "blocker"), "w", encoding="utf-8") as fh: # 占位文件 ⇒ makedirs 必失败
        fh.write("x")
    store.replace_member(CK, UID, "判据组友", ["爱吃辣", "养了只猫"])
    _arch2 = json.load(io.open(MEM._member_file(CK, UID, "判据组友"), encoding="utf-8"))
    ok("⑤ 审计写失败也不打断主流程（记忆照常落盘）",
       [e["content"] for e in _arch2["impressions"]] == ["爱吃辣", "养了只猫"],
       str([e["content"] for e in _arch2["impressions"]]))
    MEM.HISTORY_DIR = os.path.join(tmp, "memory_history")
    # ⑥ 不该在记忆目录下多出一个"群"
    MEM.MEMORY_DIR = os.path.join(tmp, "memory")
    names = sorted(os.listdir(MEM.MEMORY_DIR)) if os.path.isdir(MEM.MEMORY_DIR) else []
    ok("⑥ 记忆目录下没有审计目录（否则互通枚举会多出一个群）",
       "memory_history" not in names and "_history" not in names, str(names))
    # ⑦ limit 生效 + 读不出来的边界
    store.replace_member(CK, UID, "判据组友", ["爱吃辣"])
    store.replace_member(CK, UID, "判据组友", ["爱吃辣", "换了工作"])
    ok("history(limit) 真的截断（旧→新，最多 limit 条）",
       len(MEM.history(CK)) == 3 and len(MEM.history(CK, 2)) == 2,
       "%d / %d" % (len(MEM.history(CK)), len(MEM.history(CK, 2))))
    ok("没有审计的群／坏路径 ⇒ 空表，不抛异常",
       MEM.history("group_never_written@chatroom") == [])
finally:
    MEM.MEMORY_DIR, MEM.HISTORY_DIR = _orig_mem, _orig_hist
    shutil.rmtree(tmp, ignore_errors=True)

print("")
print("记忆覆盖审计判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
