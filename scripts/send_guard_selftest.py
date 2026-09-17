# -*- coding: utf-8 -*-
"""发送侧三道「别再自己搞自己」闸的行为判据 —— **不需要微信在跑**。

2026-09-18 拍摄现场的两起事故逼出这三道闸，这份判据是它们的尺子：

  · 事故一「机器人跟自己吵了 8 条」：用户**每次拍完都用微信「清空聊天记录」** ⇒ 该会话的
    `Msg_<md5>` 表**整张消失** ⇒ 回读/身份/回声三处全瞎。本文件 B 段量**回声窗**（文本归一化
    + 时间窗），A 段量**纯屏幕身份档**（会话头标题带 OCR，不依赖数据库）。
  · 事故二「图片他拿到了，但是又没有发给我」：清空后 `recent_texts()` 为空 ⇒
    `chat_identity_ok` 在**第一条守卫**就返回 `None` ⇒ 发文件链把 `None` 当无条件拒绝。

A 纯屏幕身份档 `_screen_only_identity`（假 OCR，真函数）
B 回声判定 `_is_self_echo` / `_echo_window`（真 deque + 真时间）
C 静态接线：这两档必须真的接在身份闸/回声调用点上（防"写了函数没人调"）

用法：py -3 scripts\\send_guard_selftest.py   （非零退出＝有失败）
"""
import io
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name, (" · " + extra) if extra else ""))


SRC = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()

from agent import chat_ocr as CO          # noqa: E402
from agent import wechat as WC            # noqa: E402


def _bare_adapter():
    """不跑构造函数（那会连着驱动库/配置），只装配这几个方法要用的字段。"""
    a = WC.WeChatAdapter.__new__(WC.WeChatAdapter)
    a.cfg = {"wechat": {}}
    a._group_by_wxid = {}
    a._nick_map = {}
    a._recent_sent = WC.deque(maxlen=200)
    return a


# ── A 纯屏幕身份档（假 OCR，真函数）───────────────────────────────────────
print("[A] 纯屏幕身份档：会话头标题带 OCR 与目标名对得上才算（不依赖数据库）")
_orig_header = CO.header_text
_CASES = []


def _fake_header(text, boom=False):
    def _f(img=None, gui=None, zoom=2):
        if boom:
            raise RuntimeError("假 OCR 崩了")
        return text
    return _f


try:
    a = _bare_adapter()
    # A1 完全一致 ⇒ 认
    CO.header_text = _fake_header("演示")
    ok1, why1 = a._screen_only_identity("58471307405@chatroom", gui="FAKE", name="演示")
    ck("A1 会话头标题＝目标名 ⇒ 认（纯屏幕证据）", ok1 is True, str(why1)[:70])
    # A2 另一个群 ⇒ 不认（这是"防发错人"的那一半）
    CO.header_text = _fake_header("闲聊群")
    ok2, why2 = a._screen_only_identity("58471307405@chatroom", gui="FAKE", name="演示")
    ck("A2 会话头标题是别的群 ⇒ 不认", ok2 is False, str(why2)[:70])
    # A3 单字母名字：`E班群` **不算** `E`（`matches()` 对单字是"以它开头"，这一档更严）
    CO.header_text = _fake_header("E班群")
    ok3, _ = a._screen_only_identity("E", gui="FAKE", name="E")
    CO.header_text = _fake_header("E")
    ok3b, _ = a._screen_only_identity("E", gui="FAKE", name="E")
    ck("A3 单字母名字必须完全相等（E班群≠E，E＝E）", ok3 is False and ok3b is True,
       "E班群=%s E=%s" % (ok3, ok3b))
    # A4 读不到标题 ⇒ 不认（fail-closed，且措辞说明是"读不到"）
    CO.header_text = _fake_header("")
    ok4, why4 = a._screen_only_identity("58471307405@chatroom", gui="FAKE", name="演示")
    ck("A4 标题读不到 ⇒ 不认且如实说「读不到」", ok4 is False and "读不到" in why4, str(why4)[:60])
    # A5 OCR 抛异常 ⇒ 不认、不冒泡
    CO.header_text = _fake_header("", boom=True)
    ok5, why5 = a._screen_only_identity("58471307405@chatroom", gui="FAKE", name="演示")
    ck("A5 OCR 异常 ⇒ 不认且不冒泡（发送链不会因它炸）", ok5 is False and "异常" in why5, str(why5)[:60])
    # A6 名字带后缀（`演示 (3)`）算同一个会话（沿用 matches 的包含口径）
    CO.header_text = _fake_header("演示 (3)")
    ok6, _ = a._screen_only_identity("58471307405@chatroom", gui="FAKE", name="演示")
    ck("A6 标题带后缀（演示 (3)）算同一个会话", ok6 is True)
finally:
    CO.header_text = _orig_header

# ── B 回声判定 ───────────────────────────────────────────────────────────
print("[B] 回声判定：归一化 + 时间窗（120 秒默认，可配 wechat.echo_window_s）")
try:
    a = _bare_adapter()
    now = time.time()
    a._recent_sent = WC.deque([("来了 别催了", now)], maxlen=200)
    ck("B1 完全相同 ⇒ 判为自己发的", a._is_self_echo("来了 别催了") is True)
    ck("B2 只差空白（多打一个空格）也算回声（归一化）", a._is_self_echo("来了  别催了") is True)
    ck("B3 前后带空白/零宽字符也算回声",
       a._is_self_echo(" 来了 别催了\u200b ") is True)
    ck("B4 别的话不算", a._is_self_echo("图呢 我没看到") is False)
    ck("B5 全角/半角标点差异也算回声（了，⇒了,）",
       WC.WeChatAdapter._echo_norm("我不回，你回。") == WC.WeChatAdapter._echo_norm("我不回,你回."))
    # 包含关系（差半句）
    a._recent_sent = WC.deque([("（本轮无法发出发言，保持安静。）", now)], maxlen=200)
    ck("B6 库里回读只剩半句（包含关系、且≥4 字）⇒ 仍判回声",
       a._is_self_echo("本轮无法发出发言") is True)
    # 太短不算（"哈" 不能靠包含关系当证据）
    a._recent_sent = WC.deque([("哈", now)], maxlen=200)
    ck("B7 太短的包含关系不当证据（哈 ⊂ 哈哈哈哈哈 ⇒ 不算）",
       a._is_self_echo("哈哈哈哈哈") is False)
    # 时间窗
    a._recent_sent = WC.deque([("来了 别催了", now - 119)], maxlen=200)
    _in119 = a._is_self_echo("来了 别催了")
    a._recent_sent = WC.deque([("来了 别催了", now - 121)], maxlen=200)
    _in121 = a._is_self_echo("来了 别催了")
    ck("B8 默认窗 120 秒：119s 内算回声、121s 外不算", _in119 is True and _in121 is False,
       "119s=%s 121s=%s" % (_in119, _in121))
    a.cfg = {"wechat": {"echo_window_s": 10}}
    a._recent_sent = WC.deque([("来了 别催了", now - 20)], maxlen=200)
    ck("B9 窗可配（echo_window_s=10 ⇒ 20 秒前的旧话不再当回声）",
       a._is_self_echo("来了 别催了") is False and abs(a._echo_window() - 10.0) < 0.01)
    a.cfg = {"wechat": {"echo_window_s": 0}}
    ck("B10 配 0/缺省 ⇒ 回落到 120 秒默认（不许变成「永远不判回声」）",
       abs(a._echo_window() - 120.0) < 0.01)
    a._recent_sent = WC.deque([], maxlen=200)
    ck("B11 没发过东西时不判回声（不误伤别人第一条消息）", a._is_self_echo("在吗") is False)
    ck("B12 空文本不判回声", a._is_self_echo("") is False and a._is_self_echo("   ") is False)
except Exception as e:
    ck("B 段回声判定", False, repr(e)[:100])

# ── C 静态接线 ───────────────────────────────────────────────────────────
print("[C] 静态接线：函数写了必须真的被调用（防「写了没人用」）")
ck("C1 身份闸在「库里没有可比对内容」那一档调了纯屏幕档",
   "self._screen_only_identity(chat_id, gui=gui, name=name)" in SRC.split("def chat_identity_ok(")[1][:12000])
ck("C2 昵称兜底那支用的也是同一个回声窗（不再写死 30 秒）",
   "self._echo_window()" in SRC and "< 30 for" not in SRC)
ck("C3 台账记的是 `_is_self_echo` 的真结果（可复盘「为什么判成自己」）",
   "echo_hit = bool(text) and self._is_self_echo(text)" in SRC)
ck("C4 发文件链：拿不到内容证据但名字档已过 ⇒ 放行并记账",
   "按名字档放行（记账）" in SRC)

# ── D 发图链：粘贴前聚焦 + 三枪提交（2026-09-18 现场「图片他拿到了，却没有发给我」）────
print("[D] 发图链：粘贴前先聚焦输入栏、提交打三枪（不再只点一枪干等 DB）")
_SI = SRC.split("def send_image_posted(")[1]
_SI = _SI[:_SI.index("def _file_panel_point_live(")]
ck("D1 粘贴前先投递聚焦输入栏（焦点不在输入框时 Ctrl+V 静默无效）",
   "focus_pt" in _SI and "backend.click(main, focus_pt)" in _SI
   and _SI.find("backend.click(main, focus_pt)") < _SI.find("_cb.set_image("))
ck("D2 提交是**三枪**且**回车优先**（回车那枪排在点「发送」之前）",
   "_shots = (" in _SI and "_deadline" in _SI
   and _SI.find("backend.keys(main, [ib.VK_RETURN])") < _SI.find("backend.click(main, send_pt)"))
ck("D3 图片**只粘贴一次**（内容留在输入框，多打几枪不会重复发送）",
   _SI.count("_cb.set_image(") == 1 and _SI.count("ib.VK_CONTROL, ib.VK_V") == 1)
ck("D4 成功回执写明「第几枪打出去的」（可复盘是哪一枪生效）",
   "第 %d 枪 %s · DB 回读" in _SI)
ck("D5 未生效时如实说「文字可能还留在输入框里」（与发文字同口径）",
   "文字可能还留在输入框里" in _SI)

print("\n[结论] %d 通过 / %d 失败" % (len(OK), len(BAD)))
if BAD:
    print("失败项：%s" % BAD)
sys.exit(1 if BAD else 0)
