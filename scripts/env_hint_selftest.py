# -*- coding: utf-8 -*-
"""「运行环境提示」判据（第 150 项）—— 不碰网络、不碰窗口、不写产品 `data/`。

守四件事（都配反向锚）：
  A **三态**：`ok` / `partial`（没发现问题但**有几项没测到**）/ `warn`（有不达标项）。
    ⛔ **"没测到"不许当成"达标"** —— 那会让提示永远显示「一切正常」，等于没做。
  B **不强制**：`warn` 的文案里**必须**有「不改也能用」（否则提示就变成逼用户改系统设置）。
  C **接线**：`/api/status` 带得出来、**两个实现面（web ＋ Qt）同批都有同一行**、且**文案只有一份**（后端给）。
  D **只读**：本模块不改任何设置、不动微信（静态锚）。

用法：runtime\\python\\python.exe scripts/env_hint_selftest.py
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import _srcmatch as _sm  # noqa: E402
from agent import env_hint as EH  # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


def _lv(**kw):
    return EH.check(probe=False, **kw)["level"]


print("── A. 三态（重点是「没测到」这一档）──")
ok("A1★ **没测到 ≠ 达标**：只给了主题、其余没测 ⇒ `partial`（不是 `ok`）",
   _lv(theme="light") == "partial", _lv(theme="light"))
ok("A1b 反向锚：**全都测了且达标** ⇒ `ok`",
   _lv(theme="light", scale=1.0, font="default", ocr_ready=True) == "ok", "")
ok("A2★ 实测那条：**深色主题 ⇒ `warn`**（附依据：相似度 0.86 → 0.57）",
   _lv(theme="dark", scale=1.0, font="default", ocr_ready=True) == "warn", "")
_ck = EH.check(probe=False, theme="dark", scale=1.0, font="default", ocr_ready=True)
ok("A2b 那条 warn 的 `why` 里写了依据（不是一句「建议浅色」）",
   any("0.86" in (x.get("why") or "") for x in _ck["items"]), "")
ok("A3 缩放不是 100% ⇒ warn；100% ⇒ ok",
   _lv(theme="light", scale=1.25, font="default", ocr_ready=True) == "warn"
   and _lv(theme="light", scale=1.0, font="default", ocr_ready=True) == "ok", "")
ok("A4 字号不是默认 ⇒ warn；默认 ⇒ ok",
   _lv(theme="light", scale=1.0, font="大", ocr_ready=True) == "warn"
   and _lv(theme="light", scale=1.0, font="default", ocr_ready=True) == "ok", "")
ok("A5 OCR 引擎没就绪 ⇒ warn（并写明「不装也能跑」）",
   _lv(theme="light", scale=1.0, font="default", ocr_ready=False) == "warn", "")
ok("A6 什么都不给 ⇒ **`partial`**，且不抛（读不到就如实说没测到）",
   _lv() == "partial", "")

print("\n── B. 不强制 ＋ 文案只有一份 ──")
_warn_text = EH.check(probe=False, theme="dark", scale=1.0, font="default", ocr_ready=True)["text"]
ok("B1★★ **提示不是强制**：`warn` 的文案里必须有「不改也能用」"
   "（去掉这句，提示就变成逼用户改系统设置）",
   "不改也能用" in _warn_text, _warn_text[-28:].replace("\n", " "))
ok("B2 `ok` 的文案**不提**「不改也能用」那种免责话（没毛病就别啰嗦）",
   "不改也能用" not in EH.check(probe=False, theme="light", scale=1.0, font="default",
                              ocr_ready=True)["text"], "")

print("\n── C. 接线：状态带得出来 ＋ 两个实现面同批 ──")
_pm = io.open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
ok("C1★ `/api/status` 里带了 `env_hint`（两端都从这一处读）", _sm.has(_pm, '"env_hint": _env_hint()'), "")
ok("C1b 取不到时**如实说「没测到」**，不让状态接口炸",
   _sm.has(_pm, "环境没测到"), "")
_web = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
ok("C2★ web 有这条提示条（`#envHint`），且**只在非 ok 时出现**",
   _sm.has(_web, 'id="envHint"') and _sm.has(_web, "eh.level !== 'ok'"), "")
ok("C2b★ web **不自己编文案**（直接用后端给的 `env_hint.text`）",
   _sm.has(_web, "eh.text"), "")
ok("C3★★ **两个实现面同批改**：Qt 那边也有同一行（只改一边 ⇒ 一边有一边没有）",
   _sm.has(io.open(os.path.join(ROOT, "ui_qt", "panels_custom.py"), encoding="utf-8").read(),
           "_env_hint_lbl"), "")
_qt = io.open(os.path.join(ROOT, "ui_qt", "panels_custom.py"), encoding="utf-8").read()
ok("C3b Qt 同一套口径（非 ok 才显示、文案取后端）",
   _sm.has(_qt, "env_hint") and _sm.has(_qt, '!= "ok"'), "")
ok("C4★ web 用的是**主题 Token**（`--st-warn-*`），不是不存在的变量名 ——"
   "本轮真踩过：先写了 `--warn-line` ⇒ 颜色无效 ⇒ **文字看不见**",
   _sm.has(_web, "var(--st-warn-bd)") and not _sm.has(_web, "var(--warn-line)"), "")

print("\n── D. 只读（不改设置、不动系统）──")
_eh_src = io.open(os.path.join(ROOT, "agent", "env_hint.py"), encoding="utf-8").read()
ok("D1★ 本模块**不写配置**（没有 `set_config`/`save`/`_save` 这类调用）",
   not any(k in _eh_src for k in ("set_config(", "config.save", "_save(")), "")
ok("D2★ 本模块**不改系统/微信**（没有注册表、没有 setter、没有鼠标动作）",
   not any(k in _eh_src for k in ("winreg", "SetCursorPos", "mouse_event")), "")

print("\n== 环境提示判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
