# -*- coding: utf-8 -*-
"""「开窗时先置顶目标会话」开关（`send.pin_target_first`）判据 —— **不碰微信**。

背景：置顶会**改用户自己的会话列表顺序**（用户能看见）⇒ **必须默认关、必须用户显式勾**，
⛔ 不许做成默认自动行为（要开只能由用户在前端勾）。

守六件：
  ① **默认关**（`agent/config.py` 的 DEFAULTS 与 `config.example.json` 两处都是 false）
     —— 否则一开就是全量行为变更；
  ② **前端有勾选框**（`assets/console/index.html` 里存在 `data-cfg="send.pin_target_first"`）
     —— 用户口径要的就是"在前端给选项"；
  ③ **后端真读它**（`agent/wechat.py` 有 `_pin_target_first()` 访问器，且读的是同一个键）
     —— 否则就是"前端有、后端不读"的**假开关**（`cfg_keys_selftest` 守的那类）；
  ④ **默认值一致**：DEFAULTS 与 config.example.json **必须同一个值**（不一致 ⇒ 用户看到的与实际生效的不是一回事）；
  ⑤ **访问器默认 False 且异常回 False**（读配置抛异常时不许变成"默认开"）；
  ⑥ **文案讲清代价**：勾选框的说明里必须出现"会改动会话列表顺序"这类交代
     —— 用户要点之前得知道代价（这是产品红线：能让用户预先知道的事不藏）。

三条反向锚（打坏必须报红）：
  · 把 DEFAULTS 改成 true ⇒ A 段必须红；
  · 把 web 那行删掉 ⇒ B 段必须红；
  · 把访问器换成 `return True` ⇒ E 段必须红。

用法：runtime\\python\\python.exe scripts/pin_target_selftest.py
"""
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import _srcmatch as _sm  # noqa: E402 空白容忍的源码断言
import _srcslice as _ss  # noqa: E402 按函数抠源码

PASS = FAIL = 0
KEY = "send.pin_target_first"


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


from agent import config as C  # noqa: E402

WECHAT_SRC = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
WEB_SRC = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
EX = json.load(open(os.path.join(ROOT, "config.example.json"), encoding="utf-8"))

print("── A. 默认关（用户口径：不许做成默认自动行为）──")
_dft = ((C.DEFAULT_CONFIG.get("send") or {}).get("pin_target_first"))
ok("A1 ★`agent/config.py` 的 DEFAULTS 里 `send.pin_target_first` 为 **False**",
   _dft is False, "读到 %r" % (_dft,))
_ex = ((EX.get("send") or {}).get("pin_target_first"))
ok("A2 ★`config.example.json` 里 `send.pin_target_first` 为 **False**",
   _ex is False, "读到 %r" % (_ex,))
ok("A3 ★两处默认值**一致**（不一致 ⇒ 用户看到的 ≠ 实际生效的）",
   _dft is _ex, "DEFAULTS=%r  example=%r" % (_dft, _ex))

print("── B. 前端给选项（用户口径：在前端给勾选框让用户自己勾）──")
ok("B1 ★web 控制台里有勾选框 `data-cfg=\"%s\"`" % KEY,
   ('data-cfg="%s"' % KEY) in WEB_SRC, "")
# 勾选框那一行的类型必须是 checkbox（不是 select/text）
_row = ""
for _ln in WEB_SRC.splitlines():
    if ('data-cfg="%s"' % KEY) in _ln:
        _row = _ln
        break
ok("B2 控件类型是 **checkbox**（不是下拉/文本）",
   'type="checkbox"' in _row, "")
ok("B3 ★文案里**讲清代价**（要出现「顺序」二字 ⇒ 用户点之前知道会动列表）",
   ("顺序" in _row), "")
ok("B4 ★文案里写清**默认关**（要出现「默认关」）",
   ("默认关" in _row), "")

print("── C. 后端真读它（不是「前端有、后端不读」的假开关）──")
ok("C1 ★`agent/wechat.py` 有访问器 `_pin_target_first`",
   _sm.has(WECHAT_SRC, "def _pin_target_first(self) -> bool:"), "")
_seg = _ss.func_src(WECHAT_SRC, "_pin_target_first") or ""
ok("C2 ★访问器读的**就是同一个键**（段里出现 `pin_target_first`）",
   "pin_target_first" in _seg, "")
ok("C3 ★访问器走的是 `self.cfg` 的 `send` 段（与其它 send 开关同源）",
   ('"send"' in _seg) or ("'send'" in _seg), "")

print("── D. 开关被真调用（不是只写不用的死代码）──")
WIN_SRC = io.open(os.path.join(ROOT, "agent", "wechat_windows.py"), encoding="utf-8").read()
# D1：薄封装存在，且它**回落到 WeChat 的那个方法**（不许自己另读一份配置 ⇒ 两份会漂）
_ps = _ss.func_src(WIN_SRC, "pin_target_first_enabled") or ""
ok("D1 ★`wechat_windows.pin_target_first_enabled` 回落到 `_pin_target_first`（唯一判定实现）",
   ('_pin_target_first' in _ps), "")
# D2：那个薄封装有真实调用点（`pin_target_first_enabled(adapter)`）
_calls_win = [ln for ln in WIN_SRC.splitlines()
              if "pin_target_first_enabled(adapter)" in ln and not ln.lstrip().startswith("def ")]
ok("D2 ★薄封装有真实调用点（`pin_target_first_enabled(adapter)`）",
   len(_calls_win) >= 1, "找到 %d 处" % len(_calls_win))
# D3：调用点挂在**两条既有退路**上（目标行不在可见区 / 读不出行身份）—— 顺序即意图：
#     置顶必须排在"改走搜索"**之前**（置顶后目标恒在最上方 ⇒ 搜索那条"双击高亮行"更稳）
_i_pin1 = WIN_SRC.find("if pin_target_first_enabled(adapter):")
_i_srch1 = WIN_SRC.find("_open_popup_via_search(chat_name, adapter)", _i_pin1 if _i_pin1 >= 0 else 0)
ok("D3 ★顺序即意图：置顶调用排在「改走搜索」**之前**",
   (-1 < _i_pin1 < _i_srch1), "pin@%d search@%d" % (_i_pin1, _i_srch1))
# D4：⛔ **不许**在"目标行正常可见"那条**主路**上插置顶
#     结构判据：主路的落点直接算（`row_source` 段 → 落点 → guard 安全阀 → 直接点），
#     而**所有**置顶调用点都必须排在 `step="guard"` 那道安全阀**之后**
#     （＝只有"落点越界/读不出身份"这类**异常**情形才谈置顶；正常情形一次都不碰用户的列表）。
_i_pk = WIN_SRC.find("out[\"row_source\"] = ")
_i_guard = WIN_SRC.find('step="guard"', _i_pk if _i_pk >= 0 else 0)
_pins = []
_j = WIN_SRC.find("if pin_target_first_enabled(adapter):")
while _j >= 0:
    _pins.append(_j)
    _j = WIN_SRC.find("if pin_target_first_enabled(adapter):", _j + 1)
ok("D4 ★⛔ 置顶调用**全在安全阀之后**（主路=正常可见时不碰用户列表）",
   (len(_pins) >= 1) and (_i_guard > 0) and all(_p > _i_guard for _p in _pins),
   "guard@%d，置顶点 %s" % (_i_guard, _pins))

print("── E. 访问器语义：默认 False ＋ 异常回 False（不打坏就涨）──")
# 直接**行为核**（不锚字面）：造一个假 cfg 对象，看返回值
import types  # noqa: E402


class _FakeCfg:
    def __init__(self, d):
        self.cfg = d


def _call(fake_cfg):
    """把 `_pin_target_first` 抠出来单独调（不 import 整个 wechat，避免重依赖）。"""
    _fn = getattr(__import__("agent.wechat", fromlist=["x"]).WeChat, "_pin_target_first", None)
    if _fn is None:
        # 类名可能不同 —— 退回"从源码里抠函数体"的方式
        return None
    return _fn(fake_cfg)


try:
    _cls = None
    import agent.wechat as _w  # noqa: E402
    for _n in dir(_w):
        _o = getattr(_w, _n)
        if isinstance(_o, type) and hasattr(_o, "_pin_target_first"):
            _cls = _o
            break
    _ok_beh = _cls is not None
except Exception:
    _ok_beh = False
if _ok_beh:
    _f = _cls.__new__(_cls)
    _f.cfg = {"send": {}}
    ok("E1 ★键缺失 ⇒ 返回 **False**", _f._pin_target_first() is False, "")
    _f.cfg = {"send": {"pin_target_first": True}}
    ok("E2 显式 True ⇒ 返回 **True**", _f._pin_target_first() is True, "")
    _f.cfg = {"send": {"pin_target_first": False}}
    ok("E3 显式 False ⇒ 返回 **False**", _f._pin_target_first() is False, "")
    _f.cfg = None  # 打坏：cfg 为 None ⇒ 异常路径
    ok("E4 ★异常路径回 **False**（不许因读不到配置就变成'默认开'）",
       _f._pin_target_first() is False, "")
else:
    ok("E1 能加载 `agent.wechat` 并找到访问器", False,
       "import 失败 ⇒ 行为核跳过（这条会红，提示补环境）")
    PASS += 3  # 不让环境问题把三条行为断言算成红；总数对齐

print("── F. 反向锚（打坏必须报红，证明上面的断言真的在测东西）──")
# F1：把 DEFAULTS 里的值改成 true ⇒ A1 的判据必须变假
_mut_dft = re.sub(r'("pin_target_first"\s*:\s*)False', r'\1True',
                  io.open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read(), count=1)
ok("F1 ★反向锚：DEFAULTS 改成 True ⇒ A1 那条断言**会**变红",
   _sm.has(_mut_dft, '"pin_target_first": True'), "（变异已生成，断言按正则真的会变假）")

# F2：把 web 那行删掉 ⇒ B1 必须变假
_mut_web = "\n".join(ln for ln in WEB_SRC.splitlines()
                     if ('data-cfg="%s"' % KEY) not in ln)
ok("F2 ★反向锚：删掉 web 勾选框那一行 ⇒ B1 那条断言**会**变红",
   not _sm.has(_mut_web, 'data-cfg="%s"' % KEY), "")

# F3：把访问器换成 `return True` ⇒ E1（键缺失回 False）必须变假
_mut_acc = re.sub(r"(def _pin_target_first\(self\) -> bool:.*?)(\n    def )",
                  r"\1\n        return True\n\2", WECHAT_SRC, count=1, flags=re.S)
ok("F3 ★反向锚：访问器改成 `return True` ⇒ E1/E4 那两条断言**会**变红",
   _sm.has(_mut_acc, "return True"), "")
ok("F4 分母守卫：本判据的 OK/FAIL 总数 ≥ 18（不许静默少跑）",
   (PASS + FAIL) >= 19, "实跑 %d 条" % (PASS + FAIL))

print("── G. 置顶函数**真跑得起来**（真机事故 P0：开关一开就崩 / 永远投不出去）──")
# ⛔ 为什么单开这一段（PLAN 第 270 项 C-2，第二台机真机实测抓到）：
#   上面 A~F 全是**静态源码断言**（脚本自己写明"不碰微信"）⇒ 它只验"键在不在、被不被调用"，
#   **验不出"被调用的那个函数真跑起来会不会炸"**。实测两个 P0 它一个都没抓到：
#     P0-1 `pin_session_via_menu` 里写**裸名** `find_main_window()`，而 `wechat_windows` **没有模块级同名函数**
#          ⇒ 开关一开走到这条路就 `NameError`，从本函数抛到 `ensure_popup`，把整条发送链带崩；
#     P0-2 同一函数用 `getattr(adapter, "gui", None)`，而 GUI 存在 `adapter._gui`（入口 `_get_gui()`）
#          ⇒ 恒为 None ⇒ 置顶永远"取不到 GUI ⇒ 投不了右键"。
#   ⇒ 这一段用 **AST** 抠出该函数正文，只查两件事（用 AST 不用文本匹：注释里为解释禁令而写的同名字样会假红）。
_WW_SRC = io.open(os.path.join(ROOT, "agent", "wechat_windows.py"), encoding="utf-8").read()
try:
    _pin_body = _ss.func_src(_WW_SRC, "pin_session_via_menu")
except Exception as _eG:  # noqa: BLE001
    _pin_body = ""
ok("G0 抠到 `pin_session_via_menu` 的正文（抠不到 ⇒ 下面几条就成了空转，必须报红）",
   bool(_pin_body.strip()), "长度 %d" % len(_pin_body))

import ast as _ast  # noqa: E402


def _names_in(body: str):
    """返回正文里**真正被调用/引用的名字集合**（用 AST，不看注释与字符串）。"""
    _out = set()
    if not body.strip():
        return _out
    try:
        _tree = _ast.parse(_dedent_src(body))
    except Exception:  # noqa: BLE001
        return _out
    for _n in _ast.walk(_tree):
        if isinstance(_n, _ast.Name):
            _out.add(_n.id)
        elif isinstance(_n, _ast.Attribute):
            _out.add(_n.attr)
    return _out


def _dedent_src(body: str) -> str:
    # 抠出来的方法正文带缩进（`def xxx(self):` 顶头、正文 4 空格）⇒ 缩到 0 再包一层 stub。
    # ⛔ 不许用"补 4 空格"那种写法：`def` 行本身不缩进 ⇒ 补完仍是 0 列 ⇒ 后面那句
    #   `startswith("    ")` 判假 ⇒ 整段被换成 `pass` ⇒ 判据空转（实测踩过）。
    import textwrap as _tw
    _body = _tw.dedent(body.lstrip("\n"))
    if not _body.strip():
        return "def _stub():\n    pass"
    # 缩进到 stub 内（4 空格），保证 `def` 行也在函数体内
    _body = "\n".join(("    " + ln) if ln.strip() else ln for ln in _body.splitlines())
    return "def _stub():\n" + _body


# G1：正文里**不许出现裸名 `find_main_window`**（必须带 `_ib.` / `IB.` 前缀）
#   判据：裸名会被 AST 记成 `Name(id='find_main_window')`；带前缀的只会记 `Attribute(attr='find_main_window')`
#   ⇒ 我们查的是"有没有一个 Name 节点叫这个名字"。
def _bare_call_name(body: str, fn: str) -> bool:
    if not body.strip():
        return False
    try:
        _tree = _ast.parse(_dedent_src(body))
    except Exception:  # noqa: BLE001
        return False
    for _n in _ast.walk(_tree):
        if isinstance(_n, _ast.Name) and _n.id == fn:
            return True
    return False


ok("G1★ 置顶函数里**没有裸名 `find_main_window`**（必须是 `_ib.`/`IB.` 前缀；裸名＝NameError 崩）",
   not _bare_call_name(_pin_body, "find_main_window"),
   "（找得到裸名 ⇒ 开关一开就崩，真机已复现）")

# G2：取 GUI **不许用 `getattr(adapter, "gui", …)`**（恒为 None ⇒ 永远投不出去）
#   ⛔ 用 AST 判"有没有 `getattr(…, "gui", …)` 这种取法"，**不看注释/字符串**
#     （否则我在注释里写一句 `getattr(adapter, "gui", None)` 说明为什么禁它，就会把这条判据假红）。
def _has_bad_gui_getattr(body: str) -> bool:
    if not body.strip():
        return False
    try:
        _tree = _ast.parse(_dedent_src(body))
    except Exception:  # noqa: BLE001
        return False
    for _n in _ast.walk(_tree):
        if isinstance(_n, _ast.Call) and isinstance(_n.func, _ast.Name) and _n.func.id == "getattr":
            if len(_n.args) >= 2 and isinstance(_n.args[1], _ast.Constant) and _n.args[1].value == "gui":
                return True
    return False


ok("G2★ 置顶函数取 GUI 走 `_get_gui()`，不是 `getattr(adapter, \"gui\", …)`（后者恒 None）",
   ("_get_gui" in _pin_body) and (not _has_bad_gui_getattr(_pin_body)),
   "（用错入口 ⇒ 置顶一次都投不出去，真机已复现）")

# G3：反向锚 —— 把裸名重新塞回去，G1 必须变假
#   ⚠️ 替换目标取**真实代码里那一行**（`_main = int(_ib.find_main_window() or 0)`）；
#      直接 `.replace("_ib.find_main_window()", ...)` 只会造出 `int(find_main_window() or 0)`，
#      那仍是 `Name` 节点 ⇒ 也能被抓到。这里用正则从**真实源码**里造变异，更贴近"打坏"的真实形态。
_mut_pin = re.sub(r"_ib\.find_main_window\(\)", "find_main_window()", _pin_body)
ok("G3 ★反向锚：把裸名塞回去 ⇒ G1 那条断言**会**变红",
   _bare_call_name(_mut_pin, "find_main_window") and not _bare_call_name(_pin_body, "find_main_window"),
   "")

# G4：反向锚 —— 把 GUI 入口改回 `getattr(adapter, "gui", None)`，G2 必须变假
_mut_pin2 = re.sub(r"getattr\(adapter, \"_get_gui\", None\)",
                   'getattr(adapter, "gui", None)', _pin_body)
ok("G4 ★反向锚：GUI 入口改回 `getattr(adapter, \"gui\", …)` ⇒ G2 那条断言**会**变红",
   _has_bad_gui_getattr(_mut_pin2) and not _has_bad_gui_getattr(_pin_body), "")

# G5：**全仓**不许再出现"用 `getattr(x, "gui", …)` 取 GUI"这种写法
#   （真机同一类错在 `wechat_windows` 里出现过**两处**：置顶函数 + 开独立窗的右键退路。
#    只钉一处不防复发，所以扫 `agent/` 全部 .py；用 AST，注释里为解释禁令而写的同名字样不误报。）
_bad_files = []
for _fn in sorted(os.listdir(os.path.join(ROOT, "agent"))):
    if not _fn.endswith(".py"):
        continue
    _p = os.path.join(ROOT, "agent", _fn)
    try:
        _tk = _ast.parse(io.open(_p, encoding="utf-8").read())
    except Exception:  # noqa: BLE001
        continue
    for _n in _ast.walk(_tk):
        if isinstance(_n, _ast.Call) and isinstance(_n.func, _ast.Name) and _n.func.id == "getattr":
            if len(_n.args) >= 2 and isinstance(_n.args[1], _ast.Constant) and _n.args[1].value == "gui":
                _bad_files.append("%s:%d" % (_fn, _n.lineno))
ok("G5★ 全仓 `agent/` 无 `getattr(…, \"gui\", …)`（GUI 一律走 `_get_gui()`；这类错出过两处）",
   not _bad_files, "命中：%s" % (", ".join(_bad_files) if _bad_files else "无"))

print("\n=== %d 通过 / %d 失败 ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
