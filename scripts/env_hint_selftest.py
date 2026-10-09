# -*- coding: utf-8 -*-
"""「运行环境提示」判据（第 150 项）—— 不碰网络、不碰窗口、不写产品 `data/`。

守五件事（都配反向锚）：
  A **只有两项、而且都在微信层级**（2026-10-10 用户当面纠正）：
    ⛔ **显示缩放 / DPI 不许出现** —— 改它会把用户**所有软件**的图标和显示全搞坏；
    ⛔ **OCR 引擎 / 依赖 / 驱动就绪不许出现** —— 那是**程序自己该搞定的活**，
       不该做成一条"请用户去装"的选项（用户最多只到微信这一层）。
    ✅ 只留：① 微信界面浅色 ② 微信标准字号。
  B **三态**：`ok` / `partial`（没发现问题但**有几项没测到**）/ `warn`（有不达标项）。
    ⛔ **"没测到"不许当成"达标"** —— 那会让提示永远显示「一切正常」，等于没做。
  C **不强制**：`warn` 的文案里**必须**有「不改也能用」（否则提示就变成逼用户改设置）。
  D **接线**：`/api/status` 带得出来、**两个实现面（web ＋ Qt）同批都有同一行**、文案只有一份（后端给）。
  E **只读**：本模块不改任何设置、不动微信（静态锚）。

用法：runtime\\python\\python.exe scripts/env_hint_selftest.py
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
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


def _code_only(src):
    """剥掉注释与 docstring，只留真代码。

    ⛔ 必须剥：本模块 docstring 里**故意**写着"⛔ 不许出现显示缩放／OCR"这类禁用说明，
       直接扫全文会被我们自己的说明误伤（实拍踩过 —— A2 就是被第 19 行那句说明判红的）。
    """
    import ast  # noqa: PLC0415
    try:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if (isinstance(body, list) and body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(getattr(body[0], "value", None), ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body.pop(0)  # 摘掉这一层的 docstring
        return ast.unparse(tree)
    except Exception:  # noqa: BLE001
        return src


def _lv(**kw):
    return EH.check(probe=False, **kw)["level"]


def _keys(**kw):
    return [x["key"] for x in EH.check(probe=False, **kw)["items"]]


print("── A. ★只有两项、且都在「用户能改的微信层级」 ──")
_EH_SRC = io.open(os.path.join(ROOT, "agent", "env_hint.py"), encoding="utf-8").read()
_EH_CODE = _code_only(_EH_SRC)
ok("A0 分母守卫：剥注释后源码非空且确实变短（剥失败 ⇒ 下面全是假绿）",
   0 < len(_EH_CODE) < len(_EH_SRC), "%d -> %d" % (len(_EH_SRC), len(_EH_CODE)))
ok("A1★★ 只有两项：微信浅色 + 微信标准字号（`items` 就这两个 key）",
   set(_keys(theme="dark", font="大")) == {"theme", "font"},
   str(_keys(theme="dark", font="大")))
ok("A2★★ **不许出现显示缩放**（改它会把用户所有软件的图标显示全搞坏）",
   "显示缩放" not in _EH_CODE and '"scale"' not in _EH_CODE and "'scale'" not in _EH_CODE,
   "代码里出现 scale 项")
ok("A3★★ **不许出现 OCR / 依赖 / 驱动就绪**（那是程序自己的活，不该问用户）",
   ('"ocr"' not in _EH_CODE) and ("_item_ocr" not in _EH_CODE),
   "代码里出现 ocr 项")
ok("A4 反向锚：**源码里不许有 `_read_ocr` / `ocr_ready`**（删干净了，不是只从 items 里藏起来）",
   "ocr_ready" not in _EH_SRC and "_read_ocr" not in _EH_SRC
   and "chat_ocr" not in _EH_SRC, "")
ok("A5 反向锚：**源码里不许有 `_read_scale` / `detect_scale` / `ui_adapt`**",
   "_read_scale" not in _EH_SRC and "detect_scale" not in _EH_SRC
   and "ui_adapt" not in _EH_SRC, "")

print("\n── B. 三态（重点是「没测到」这一档）──")
ok("B1★ **没测到 ≠ 达标**：只给了主题、字号没测 ⇒ `partial`（不是 `ok`）",
   _lv(theme="light") == "partial", _lv(theme="light"))
ok("B1b 反向锚：**两项都测了且达标** ⇒ `ok`",
   _lv(theme="light", font="default") == "ok", "")
ok("B2★ 实测那条：**微信深色 ⇒ `warn`**（附依据：相似度 0.86 → 0.57）",
   _lv(theme="dark", font="default") == "warn", "")
_ck = EH.check(probe=False, theme="dark", font="default")
ok("B2b 那条 warn 的 `why` 里写了依据（不是一句「建议浅色」）",
   any("0.86" in (x.get("why") or "") for x in _ck["items"]), "")
ok("B3 字号不是标准 ⇒ warn；标准 ⇒ ok",
   _lv(theme="light", font="大") == "warn" and _lv(theme="light", font="default") == "ok", "")
ok("B4 什么都不给 ⇒ **`partial`**，且不抛（读不到就如实说没测到）",
   _lv() == "partial", "")

print("\n── C. 用户原话（两条指令逐字在 warn 文案里）──")
_warn = EH.check(probe=False, theme="dark", font="大")["text"]
ok("C1★★ warn 文案里有**用户指定的那一句**：「请把您的微信界面设成浅色模式」",
   "请把您的微信界面设成浅色模式" in _warn, _warn[:40].replace("\n", " "))
ok("C2★★ warn 文案里有**用户指定的那一句**：「请把您的字号设为标准字号」",
   "请把您的字号设为标准字号" in _warn, "")
ok("C3★★ **提示不是强制**：warn 的文案里必须有「不改也能用」"
   "（去掉这句，提示就变成逼用户改设置）",
   "不改也能用" in _warn, _warn[-28:].replace("\n", " "))
ok("C4 `ok` 的文案**不提**「不改也能用」那种免责话（没毛病就别啰嗦）",
   "不改也能用" not in EH.check(probe=False, theme="light", font="default")["text"], "")

print("\n── D. 接线：状态带得出来 ＋ 两个实现面同批 ──")
_pm = io.open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
ok("D1★ `/api/status` 里带了 `env_hint`（两端都从这一处读）", _sm.has(_pm, '"env_hint": _env_hint()'), "")
ok("D1b 取不到时**如实说「没测到」**，不让状态接口炸",
   _sm.has(_pm, "环境没测到"), "")
_web = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
#: 只取「环境引导」那一段（从 `obRenderHint` 到下一个顶层函数）——别处那个
#  「坐标/遮挡清理」面板也提到"显示缩放"，那是**另一个功能**，不该被本条判据殃及。
_web_ob = _web
if "function obRenderHint()" in _web:
    _web_ob = _web.split("function obRenderHint()", 1)[1]
    _web_ob = _web_ob.split("\nfunction ", 1)[0]
ok("D2★ web 有这条提示条（`#envHint`），且**只在非 ok 时出现**",
   _sm.has(_web, 'id="envHint"') and _sm.has(_web, "eh.level !== 'ok'"), "")
ok("D2b★ web **不自己编文案**（直接用后端给的 `env_hint.text`）",
   _sm.has(_web, "eh.text"), "")
ok("D3★★ **两个实现面同批改**：Qt 那边也有同一行（只改一边 ⇒ 一边有一边没有）",
   _sm.has(io.open(os.path.join(ROOT, "ui_qt", "panels_custom.py"), encoding="utf-8").read(),
           "_env_hint_lbl"), "")
_qt = io.open(os.path.join(ROOT, "ui_qt", "panels_custom.py"), encoding="utf-8").read()
ok("D3b Qt 同一套口径（非 ok 才显示、文案取后端）",
   _sm.has(_qt, "env_hint") and _sm.has(_qt, '!= "ok"'), "")
ok("D4★ web 用的是**主题 Token**（`--st-warn-*`），不是不存在的变量名 ——"
   "本轮真踩过：先写了 `--warn-line` ⇒ 颜色无效 ⇒ **文字看不见**",
   _sm.has(_web, "var(--st-warn-bd)") and not _sm.has(_web, "var(--warn-line)"), "")
ok("D5★★ **环境引导那一段**里不出现缩放/OCR"
   "（后端删了、前端那段若还留着文案=漏改；"
   "⚠️ 只扫环境引导区，`index.html` 别处那个『坐标/遮挡清理』面板与本条无关，不算命中）",
   ("显示缩放" not in _web_ob and "OCR" not in _web_ob),
   "环境引导区仍含缩放/OCR")
_eg_src = io.open(os.path.join(ROOT, "ui_qt", "envguide.py"), encoding="utf-8").read()
_eg_code = _code_only(_eg_src)
ok("D6★★ 环境引导弹窗代码里**不提**缩放/OCR（弹窗只引导微信那两件事）",
   "显示缩放" not in _eg_code and "OCR" not in _eg_code, "")
ok("D6b 反向锚：**判据自己剥干净了**（envguide 的 docstring 里写了禁用说明，剥不掉会误伤）",
   0 < len(_eg_code) < len(_eg_src), "%d -> %d" % (len(_eg_src), len(_eg_code)))

print("\n── E. 只读（不改设置、不动系统）──")
ok("E1★ 本模块**不写配置**（没有 `set_config`/`save`/`_save` 这类调用）",
   not any(k in _EH_SRC for k in ("set_config(", "config.save", "_save(")), "")
ok("E2★ 本模块**不改系统/微信**（没有注册表、没有 setter、没有鼠标动作）",
   not any(k in _EH_SRC for k in ("winreg", "SetCursorPos", "mouse_event")), "")

print("\n== 环境提示判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
