# -*- coding: utf-8 -*-
"""B5 判据：切会话后「内容级复核判否」必须分三态，不许直接拒发。

背景（`报告/外部项目考察-20261001/B5根治方案.md`）：
  发送前那道闸自称"内容级复核"，实则只核「当前会话内容像不像目标」——一个**间接推断**。
  OCR 一旦读不出聊天区就返回判否，而「判否」与「真切错了」在返回值和日志上**不可区分**。
  ⇒ 演示群（最近消息全是图/表情）**永远发不出去**（用户实际遇到）。
  ⇒ 朋友版的修法是"降级放行"，但它的依据是**被质疑环节的自报措辞** ⇒ 会复现 9/24 发错会话。

本判据要钉死的三态（在 `_idn is False` 那一支）：
  ① 绿底带**在目标行**       ⇒ 只是 OCR 瞎了     ⇒ 降级为 None（仍走名字档）
  ② 绿底带**量不到**         ⇒ 判据不可用         ⇒ 降级为 None（仍走名字档）
  ③ 绿底带**量到了、不在目标行** ⇒ 真的切错了       ⇒ **拒发** + note_switch_fail

跑法：py scripts/identity_fallback_selftest.py
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

WX = os.path.join(ROOT, "agent", "wechat.py")
_src = open(WX, encoding="utf-8").read()

PASS = [0]
FAIL = [0]


def ok(name, cond, extra=""):
    if cond:
        PASS[0] += 1
        print("  PASS %s" % name)
    else:
        FAIL[0] += 1
        print("  FAIL %s  %s" % (name, extra))


# ⛔ 纪律 17：grep 候选必看**作用域**。
#   判否现场在 `send_text` 里；而「绿底带」原语（`_band_on_row`）目前只在
#   `_click_visible_session` 内部有一个**局部**定义 —— 全局字符串搜索会把那个
#   沾进来，A1/A2 就会**误判通过**。⇒ 必须按 AST 限定在 `send_text` 函数体内取代码。
def _func_src(src, name):
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return ast.get_source_segment(src, n) or ""
    return ""


_send_src = _func_src(_src, "send_text")
_click_src = _func_src(_src, "_click_visible_session")

print("== A. 结构性：判否分支必须有「三态」而不是直接拒发 ==")

ok("A0 定位到 `send_text` 函数体（判否现场所在作用域）", bool(_send_src), "AST 没找到 send_text")

# 判否现场 = send_text 里"身份核对/内容级复核判否"那一支附近的代码
_i = _send_src.find("身份核对说不是这个会话")
if _i < 0:
    _i = _send_src.find("内容级复核说不是这个会话")
_around = _send_src[max(0, _i - 900):_i + 300] if _i >= 0 else ""
ok("A0b 判否现场确实在 `send_text` 内", _i >= 0, "send_text 里没有该分支")

# A1: 是否存在**方法级**（提升后）的绿底带原语 —— 必须定义在类里、不是局部函数
_has_prim = ("def _band_on_target_row" in _src)
ok("A1 存在**可复用的**「绿底带」原语 `_band_on_target_row`（方法级、非局部）",
   _has_prim,
   "只有局部 _band_on_row（作用域在 _click_visible_session 内）⇒ 发送前的判否用不到它")

# ⛔ 纪律 17 的深水区：**注释里的词也会骗过 grep**。
#   窗口里那句「它自己的证据是"绿底高亮行 + 名字 OCR"」是**注释**，不是代码。
#   ⇒ 必须用 AST 抽出**真实语句**再判，不许拿原始文本（含注释）当证据。
def _func_body_code(src, name):
    """返回函数体内**真实代码**（逐行剥掉 `#` 注释与文档字符串）。

    ⛔ 不能只用 `ast.get_source_segment` —— 对 `try:` / `with:` / `if:` 这类**复合语句**，
      它返回的是**整块**（把内部注释一并带出），于是注释里的「绿底带」又会骗过 grep。
    ⇒ 取函数体的**行区间**，逐行剔 `#` 之后的注释；再剔掉三引号块。
    """
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            body = src.splitlines()[n.lineno - 1: n.end_lineno]
            out, in_doc, quote = [], False, ""
            for ln in body:
                s = ln
                if in_doc:
                    if quote in s:
                        s = s.split(quote, 1)[1]
                        in_doc = False
                    else:
                        continue
                # 去单行 # 注释（本文件代码里没有 # 出现在字符串字面量中的情形需要保护）
                if "#" in s:
                    s = s.split("#", 1)[0]
                # 三引号文档串起止
                for q in ('"""', "'''"):
                    if not in_doc and q in s:
                        head = s.split(q, 1)[0]
                        rest = s.split(q, 1)[1]
                        if q in rest:
                            s = head + rest.split(q, 1)[1]
                        else:
                            s, in_doc, quote = head, True, q
                        break
                if s.strip():
                    out.append(s)
            return "\n".join(out)
    return ""


_code_all = _func_body_code(_src, "send_text")

# A2: send_text **真实语句**里必须出现绿底带调用（不是注释里提过）
ok("A2 `send_text` 的**真实语句**里出现绿底带证据（绿底/_band/_band_on_target_row）",
   bool(re.search(r"(绿底|_band)", _code_all)),
   "真实语句里没有绿底带 ⇒ 判否即拒发，未分态（注释里提过不算）")

# A3: 不允许存在"按措辞放行"（朋友版回流防护）—— 全库搜
ok("A3 全库**无** `_has_strong_switch` 这类「按自报措辞放行」",
   "_has_strong_switch" not in _src, "发现按措辞放行的回流")

print("\n== B. 行为级：三态各自走对路（桩测试）==")


class _FakeWm(object):
    def __init__(self):
        self.sql_writes = []

    def _ensure_main_visible(self, *a, **k):
        pass

    def _update_render_rect(self):
        pass

    def _busy_reason(self, *a, **k):
        return ""            # 用户不忙

    def _get_gui(self):
        return None

    def display_name(self, cid):
        return "群X"

    def chat_is_open(self, chat_id, gui=None, name=""):
        return False, "名字档：绿底行名字没对上"


def _make_stub():
    """复用 WeChatAdapter 的真实方法（只打桩外部依赖），跑发送前的判否三态。"""
    from agent import wechat as W
    t = W.WeChatAdapter.__new__(W.WeChatAdapter)
    # 最小字段补全
    t._md = None
    t._idn_txn = 0
    t._idn_cache = None
    t._gui = None
    t._monitored = lambda: []
    return t


try:
    from agent import wechat as W   # noqa: E402

    ok("B0 `WeChatAdapter` 可导入", True, "")

    # ---- 三态判定矩阵：直接测"判否之后该怎么走"这段逻辑 ----
    # 未改代码时不存在 `_band_on_target_row` ⇒ 下面全部落空（预期见红）
    _has_method = hasattr(W.WeChatAdapter, "_band_on_target_row")
    ok("B1 `WeChatAdapter._band_on_target_row` 存在（可复用原语已提升）",
       _has_method, "方法不存在 ⇒ 判否无法分态")

    if _has_method:
        t = _make_stub()

        def _mk(band, measured):
            t._band_on_target_row = lambda *a, **k: (band, measured, "桩")

        # ① 绿底带在目标行 ⇒ 不拒发
        _mk({"y0": 100, "y1": 130}, True)
        _res = t._identity_fallback_after_deny("chatX", "群X", "理由")
        ok("B2 判否 + 绿底带**在目标行** ⇒ 降级（不拒发）",
           _res == "degrade", "返回=%r（期望 degrade）" % (_res,))

        # ② 量不到 ⇒ 不拒发
        _mk(None, False)
        _res = t._identity_fallback_after_deny("chatX", "群X", "理由")
        ok("B3 判否 + 绿底带**量不到** ⇒ 降级（测不了≠没有）",
           _res == "degrade", "返回=%r（期望 degrade）" % (_res,))

        # ③ 量到了但不在目标行 ⇒ 拒发
        _mk(None, True)
        _res = t._identity_fallback_after_deny("chatX", "群X", "理由")
        ok("B4 判否 + 绿底带**量到、不在目标行** ⇒ 拒发",
           _res == "refuse", "返回=%r（期望 refuse）" % (_res,))
    else:
        ok("B2 判否 + 绿底带在目标行 ⇒ 降级", False, "方法未提升，无法分态")
        ok("B3 判否 + 绿底带量不到 ⇒ 降级", False, "方法未提升，无法分态")
        ok("B4 判否 + 绿底带量到、不在目标行 ⇒ 拒发", False, "方法未提升，无法分态")

except Exception as e:
    import traceback
    traceback.print_exc()
    ok("B0-B4 行为级桩测试可跑", False, "%s: %s" % (type(e).__name__, e))

print("\n== C. 放宽之后不许直接放行（仍要过名字档）==")

# 同样只认**真实语句**（去注释）
ok("C1 `send_text` 真实语句里仍然调用名字档 `chat_is_open`",
   "chat_is_open" in _code_all, "放宽路径丢了名字档 ⇒ 等于朋友版式放行")

ok("C2 `send_text` 真实语句里仍然调用 `note_switch_fail`（留痕）",
   "note_switch_fail" in _code_all, "拒发不留痕")

print("\n== B5 判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
sys.exit(1 if FAIL[0] else 0)
