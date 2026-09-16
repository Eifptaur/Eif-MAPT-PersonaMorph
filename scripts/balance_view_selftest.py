# -*- coding: utf-8 -*-
"""余额显示伪装判据（2026-09-16 立）。

用户原话：「还有没有一键隐藏剩余金额功能或者一键修改剩余金额功能，你在界面那个位置放这个功能，
**可以在界面显示上把金额改掉，但是实际上还是那么多**」⇒ 本判据钉三件事：
  ① `agent/balance_view.py::mask()` 的三种模式行为正确、**不改传进来的原对象**（真实余额不许被污染）；
  ② 认不出的模式／fake 没填数字 ⇒ **退回照实显示**（宁可照实，也不许把数字弄丢）；
  ③ 接线与界面都在：`balance_fn` 调它、余额胶囊旁边有那个按钮、配置键存在且默认 `real`。

跑法：py -3 scripts\\balance_view_selftest.py
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   %s%s" % (name, ("  [%s]" % detail) if detail else ""))
    else:
        FAIL += 1
        print("  FAIL %s%s" % (name, ("  [%s]" % detail) if detail else ""))


def src(rel):
    return open(os.path.join(ROOT, rel.replace("/", os.sep)), encoding="utf-8", errors="replace").read()


from agent import balance_view as BV      # noqa: E402

print("── A. 三种模式 ──")
_real = {"total_balance": "12.34", "topped_up_balance": "10.00", "currency": "CNY"}
ok("real ⇒ 原样返回", BV.mask(_real, "real") == _real, str(BV.mask(_real, "real")))
_h = BV.mask(_real, "hide")
ok("hide ⇒ 不给金额、带 hidden 标记", _h.get("hidden") is True and "total_balance" not in _h, str(_h)[:70])
_f = BV.mask(_real, "fake", "8888.88")
ok("fake ⇒ 金额字段换成那个数字",
   _f.get("total_balance") == "8888.88" and _f.get("topped_up_balance") == "8888.88", str(_f)[:80])
ok("伪装过的结果带可识别标记（别被当真实余额）", _f.get("display_masked") is True and _h.get("display_masked") is True)
ok("**不改传进来的原对象**（真实余额不许被污染）",
   _real == {"total_balance": "12.34", "topped_up_balance": "10.00", "currency": "CNY"}, str(_real))

print("── B. 边界：认不出的模式 / 没填数字 ⇒ 退回照实 ──")
ok("模式写错 ⇒ 照实", BV.mask(_real, "whatever") == _real, str(BV.mask(_real, "whatever")))
ok("模式为空 ⇒ 照实", BV.mask(_real, "") == _real, str(BV.mask(_real, "")))
ok("fake 但没填数字 ⇒ 照实（不许显示空白）", BV.mask(_real, "fake", "") == _real, str(BV.mask(_real, "fake", "")))
ok("查询本身报错时原样透出去（伪装不该掩盖真实失败）",
   BV.mask({"error": "没有可用 key"}, "fake", "8888") .get("error") == "没有可用 key")
ok("normalize_mode 只认三种", BV.normalize_mode("HIDE") == "hide" and BV.normalize_mode("x") == "real")

print("── C. 接线与界面 ──")
_pm = src("scripts/persona_morph.py")
ok("balance_fn 走 balance_view.mask（唯一实现）",
   "from agent.balance_view import mask as _mask" in _pm and "_ui.get(\"balance_display\")" in _pm, "")
_ui = src("agent/console_html.py")
ok("余额胶囊旁边有那个按钮（一键隐藏/一键改）",
   'id="balMask"' in _ui and "余额显示" in _ui, "")
ok("按钮里写明「只改界面上的数字，真实余额一点都不动」", "真实余额一点都不动" in _ui, "")
ok("保存走既有配置接口（没有为它新开后门）",
   "setPath(cfg,'ui.balance_display',mode)" in _ui and "postJSON('/api/config', cfg)" in _ui, "")
_cfg = src("agent/config.py")
ok("配置键存在且默认 real（用户不点就照实显示）",
   '"balance_display": "real"' in _cfg and '"balance_fake": ""' in _cfg, "")

print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
