# -*- coding: utf-8 -*-
"""「群名片名录」判据（第 148 项）—— 不碰网络、不碰窗口、**不碰真实配置**。

守四件事：
  A **行为**：起名/改名/去掉都落在 `member_notes` 这**唯一一处**；**按 wxid 存**
    （同名两人互不影响）；不像账号的 id 拒绝写入；写盘失败**如实报错**；列表按 uid 去重、别名在前、
    超上限**如实标截断**；读不到就返回空表（不抛）。
  B **接线**：路由两处都声明；**两个前端同批都有**这个入口（web「起名」按钮 ＋ Qt 同名弹窗）。
  C **唯一来源**：别名只有 `member_notes` 一份（没有第二个存储）。
  D 反向锚：把「空别名」当成一次普通写入 ⇒ 必须真的把键**删掉**（不是留个空串在配置里）。

用法：runtime\\python\\python.exe scripts/member_book_selftest.py
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import _srcmatch as _sm  # noqa: E402
import agent.config as CFG  # noqa: E402
from agent import member_book as MB  # noqa: E402

PASS = FAIL = 0
_FAKE = {"member_notes": {}}
_SAVED = {}


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


# ⛔ **不许动真实 config.json**：把 `agent.config` 的读/写在这个进程里换成假实现。
#    `member_book` 是在函数里 `from .config import get_config, save_config` ⇒ 取的是**调用时**的模块属性
#    ⇒ 这里替换属性就能拦住真实写盘（判据绝不写产品配置）。
CFG.get_config = lambda *a, **k: dict(_FAKE)          # type: ignore[assignment]
CFG.save_config = lambda cfg=None, path=None: _SAVED.update(dict(cfg or {}))  # type: ignore[assignment]

_U1, _U2 = "uid_sampleaaaa1", "uid_samplebbbb2"


def _mem_of(rows):
    return lambda ck, store=None: list(rows)


print("── A. 行为 ──")
r = MB.set_alias(_U1, "老张")
ok("A1★ 起别名写进 `member_notes`（唯一来源）",
   r["ok"] and (_SAVED.get("member_notes") or {}).get(_U1) == "老张", str(_SAVED.get("member_notes")))
ok("A1b 返回的是**人话**回执（不是 json 摘要）", "老张" in str(r.get("note") or ""), str(r.get("note")))
_FAKE["member_notes"] = dict(_SAVED.get("member_notes") or {})
MB.set_alias(_U2, "老张")     # 同名的另一个人
ok("A2★★ **按 wxid 存**：两个人可以都叫「老张」，互不覆盖（昵称重名不影响身份）",
   len(_SAVED.get("member_notes") or {}) == 2, str(_SAVED.get("member_notes")))
r3 = MB.set_alias(_U1, "")
ok("A3★ 空别名 ⇒ **真的把键删掉**（不是留个空串在配置里 —— 那会让下游读到一个「有人叫空名字」）",
   r3["ok"] and _U1 not in (_SAVED.get("member_notes") or {}), str(_SAVED.get("member_notes")))
r4 = MB.set_alias("", "x")
ok("A4 没给 id ⇒ 拒（不静默写一条空键）", not r4["ok"] and "id" in str(r4.get("note") or ""), str(r4.get("note")))
r5 = MB.set_alias("a", "x")
ok("A4b 不像账号的 id ⇒ 拒（防被误当成 uid 写进去）", not r5["ok"], str(r5.get("note")))


def _boom(*a, **k):
    raise OSError("磁盘满了")


_orig_save = CFG.save_config
CFG.save_config = _boom      # type: ignore[assignment]
r6 = MB.set_alias(_U2, "新名")
CFG.save_config = _orig_save  # type: ignore[assignment]
ok("A5★ 写盘失败 ⇒ **如实报错**（不假装成功 ——「改了个名但没生效」比「根本没这功能」更让人困惑）",
   (not r6["ok"]) and "保存失败" in str(r6.get("note") or ""), str(r6.get("note")))

print("\n── 列表（去重 / 顺序 / 截断 / 读不到）──")
_FAKE["member_notes"] = {_U1: "老张"}
_mem = _mem_of([{"userId": _U1, "name": "张三", "impressions": [1, 2]},
                {"userId": _U2, "name": "李四", "impressions": [1]},
                {"userId": "", "name": "没账号的档", "impressions": [1]}])
b = MB.list_book("g", memory=_mem, cfg=_FAKE)
ok("A6 列出来的人都在（含「没有 uid 的那种档」）", b["ok"] and b["total"] == 3,
   str([m["name"] for m in b["members"]]))
ok("A6b★ **有别名的排前面**（用户更关心自己起过名的）", b["members"][0]["userId"] == _U1, "")
ok("A6c 别名带在行上（`alias` 字段）", b["members"][0]["alias"] == "老张", str(b["members"][0]))
_dup = _mem_of([{"userId": _U1, "name": "张三", "impressions": []},
                {"userId": _U1, "name": "张三", "impressions": []}])
ok("A7★ **跨会话按 uid 去重**（同一个人不出现两次）", MB.list_book("g", memory=_dup, cfg=_FAKE)["total"] == 1, "")
_FAKE["member_notes"] = {}
_many = _mem_of([{"userId": "uid_%03d" % i, "name": "n%d" % i, "impressions": []}
                 for i in range(MB.LIST_MAX + 5)])
_bb = MB.list_book("g", memory=_many, cfg=_FAKE)
ok("A8★ 超上限 ⇒ **如实标截断**（不是悄悄少给）",
   len(_bb["members"]) == MB.LIST_MAX and _bb["truncated"] is True, "%d/%d" % (len(_bb["members"]), _bb["total"]))
ok("A9 读不到成员 ⇒ 空表、ok、不抛", (lambda d: d["ok"] and d["members"] == [])(MB.list_book("g", memory=_mem_of([]), cfg=_FAKE)), "")

print("\n── B/C. 接线 ＋ 唯一来源 ──")
_r = io.open(os.path.join(ROOT, "agent", "routes.py"), encoding="utf-8").read()
ok("B1★ 路由**两处都声明**（ROUTES 的允许方法 ＋ HANDLERS 的处理函数）",
   _sm.has(_r, '"/api/member_book": ("GET", "POST")') and _sm.has(_r, '"/api/member_book": {'), "")
_w = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
ok("B1b 处理器在位（GET 列表 / POST 起名）",
   _sm.has(_w, "def _rapi_member_book(") and _sm.has(_w, "_mb.set_alias("), "")
_web = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
ok("B2★ web 有入口（「起名」按钮 ＋ 调 `/api/member_book`）",
   _sm.has(_web, "nameBtn.textContent='起名'") and _sm.has(_web, "'/api/member_book'"), "")
ok("B2b web 显示成「别名（原昵称）」（让人一眼看出哪个是自己起的名）",
   _sm.has(_web, "_al + '（' + _raw + '）'"), "")
_qt = io.open(os.path.join(ROOT, "ui_qt", "panels_custom.py"), encoding="utf-8").read()
ok("B3★★ **两个实现面同批改**：Qt 侧也有（`起名` 按钮 ＋ `_mem_alias_dlg` 弹窗）"
   "（只改一边 ⇒ 一边有入口一边没有）",
   _sm.has(_qt, 'Btn("起名"') and _sm.has(_qt, "def _mem_alias_dlg("), "")
ok("B3b Qt 的会话 key **转义后**才进 query（`group:x@chatroom` 不转义会查空）",
   _sm.has(_qt, "_q(str(chat_key or \"\"), safe=\"\")"), "")
mb_src = io.open(os.path.join(ROOT, "agent", "member_book.py"), encoding="utf-8").read()
ok("C1★ 别名**只有一个存储**：本模块除 `member_notes` 外不写别的 key",
   ("member_notes" in mb_src) and not any(k in mb_src for k in ("aliases.json", "member_book.json")), "")
ok("C2 别名确实会进提示词（`prompt.py` 读的就是 `member_notes`）",
   _sm.has(io.open(os.path.join(ROOT, "agent", "prompt.py"), encoding="utf-8").read(),
           'cfg.get("member_notes")'), "")

print("\n== 群名片名录判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
