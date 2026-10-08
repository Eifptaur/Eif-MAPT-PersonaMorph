# -*- coding: utf-8 -*-
"""「开窗失败·请你帮一把」判据（第 225 项收尾）—— 不碰真机、不写产品 `data/`。

为什么要有这条弹窗：**开独立窗是唯一的发送入口**（切会话链已废）⇒ 卡在这一步＝机器人什么都不发了。
只写进返回值没人看得见（调用方在后台循环里）⇒ 必须**弹到用户眼前**。

守四件事：
  A **记录与去重**：失败才记 · 读得回 · **过期自动消失**（不长期挂着）· 再次失败**覆盖**旧的。
  B **文案单点**：正常档与鲸语档**都在 `whale_text` 里**（前端不许自己拼）；`{chat}` 占位符两版一致；
    群名真被填进去；鲸语档要**看得出是鲸语**。
  C **两个实现面同批**：web 与 Qt 都弹（且都按时间戳**只弹一次**，不反复打扰）。
  D 反向锚：TTL 设 0 ⇒ 立刻算过期（证明"过期"这条不是恒真）。

用法：runtime\\python\\python.exe scripts/popup_help_selftest.py
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import _srcmatch as _sm  # noqa: E402
from agent import whale_text as WT  # noqa: E402
from agent import wechat_windows as WW  # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


print("── A. 记录 / 读回 / 过期 / 覆盖 ──")
WW._HELP_LAST.clear()
ok("A0 分母守卫：一开始是空的（否则下面几条没意义）", WW.popup_help_now() == {}, "")
_r1 = WW.note_popup_help("测试群A", "请手动开一次")
_n = WW.popup_help_now()
ok("A1★ 失败时记一条，且**读得回**", bool(_n.get("at")) and _n.get("chat") == "测试群A",
   "%s" % _n.get("chat"))
ok("A2★ **过期即消失**（TTL 之后不再挂着 —— 挂着一件早就过期的事，用户会糊涂）",
   WW.popup_help_now(max_age_s=-1.0) == {}, "")
_r2 = WW.note_popup_help("测试群B", "请手动开一次")
ok("A3★ 再次失败 ⇒ **覆盖**（用户需要的是最新那条）",
   WW.popup_help_now().get("chat") == "测试群B", str(WW.popup_help_now().get("chat")))
ok("A4 记完不清空（用户可能还没看到 ⇒ 下次状态刷新还要带出去）",
   bool(WW.popup_help_now().get("at")), "")

print("\n── B. 文案单点（正常档 / 鲸语档）──")
ok("B0 分母守卫：两版文案都在 `whale_text` 里（`POPUP_HELP` / `POPUP_HELP_WHALE`）",
   hasattr(WT, "POPUP_HELP") and hasattr(WT, "POPUP_HELP_WHALE"), "")
ok("B1★ **`{chat}` 占位符两版一致**（不一致 ⇒ 鲸语版会漏掉群名）",
   ("{chat}" in WT.POPUP_HELP["body"]) and ("{chat}" in WT.POPUP_HELP_WHALE["body"]), "")
_dn = WT.popup_help_text("某某群")
_dw = WT.popup_help_text("某某群", whale=True)
ok("B2 群名真被填进去（两版都是）",
   ("某某群" in _dn["body"]) and ("某某群" in _dw["body"]), "")
ok("B3★ 两版都**不带**占位符残留（「没填」是低级错，用户会看到 `{chat}` 这几个字符）",
   ("{chat}" not in _dn["body"]) and ("{chat}" not in _dw["body"]), "")
ok("B4★ 鲸语档**看得出是鲸语**（有 🐋 且语气不同）",
   ("🐋" in _dw["title"] + _dw["body"]) and (_dw["title"] != _dn["title"]), _dw["title"])
ok("B5 两版都写清「要他做什么」（拖出来／双击），不是干说「失败了」",
   ("拖" in _dn["body"]) and ("拖" in _dw["body"]), "")
_chat_missing = WT.popup_help_text("")["body"]
ok("B6 群名缺失时给个占位说法（不是空字符串或 `{chat}`）",
   ("{chat}" not in _chat_missing) and len(_chat_missing) > 10, "")

print("\n── C. 接线（后端带出来 ＋ 两个实现面都弹）──")
_pm = io.open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
ok("C1★ `/api/status` 里带了 `popup_help`", _sm.has(_pm, '"popup_help": _popup_help()'), "")
_web = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
ok("C2★ web 弹窗在位（读 `s.popup_help`）", _sm.has(_web, "s.popup_help"), "")
ok("C2b★ **同一句只弹一次**（按 `at` 时间戳去重）—— 否则刷新一次弹一次，没法用",
   _sm.has(_web, "window.__popupHelpAt === ph.at"), "")
ok("C2c★ web **不自己拼文案**（标题/正文都取后端给的）",
   _sm.has(_web, "ph.title") and _sm.has(_web, "ph.text"), "")
_qt = io.open(os.path.join(ROOT, "ui_qt", "panels_custom.py"), encoding="utf-8").read()
ok("C3★★ **两个实现面同批**：Qt 也有（`_popup_help_dlg` ＋ 同样的去重）",
   _sm.has(_qt, "def _popup_help_dlg(") and _sm.has(_qt, "_popup_help_at"), "")
ok("C3b Qt 也不自己拼文案（取 `ph.get(\"title\")`/`ph.get(\"text\")`）",
   _sm.has(_qt, 'ph.get("title")') and _sm.has(_qt, 'ph.get("text")'), "")
ok("C4★ 失败出口**真的记了**（`ensure_popup` 的最后一条路上调 `note_popup_help`）",
   _sm.has(io.open(os.path.join(ROOT, "agent", "wechat_windows.py"), encoding="utf-8").read(),
           "note_popup_help(chat_name, out[\"user_action\"]"), "")

print("\n── D. 反向锚 ──")
ok("D1★ `max_age_s=-1` ⇒ 立刻算过期（证明 A2 不是恒真）", WW.popup_help_now(max_age_s=-1.0) == {}, "")
ok("D2★ 文案组装**只有一处**（`whale_text.popup_help_text`），别处不许再写一份",
   sum(1 for _p in ("agent/whale_text.py", "agent/wechat_windows.py",
                    "ui_qt/panels_custom.py", "assets/console/index.html")
       if "我动动小手" in io.open(os.path.join(ROOT, _p), encoding="utf-8", errors="ignore").read()) <= 1,
   "")

print("\n== 开窗求助弹窗判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
