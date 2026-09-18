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
ck("C5 发图：DB 回读看不见时用**屏幕兜底确认**（输入框清空＝已发出），并写明是屏幕证据不是回读",
   "按屏幕证据判已发出" in SRC and "输入框已清空" in SRC)

# ── D 发图链：右键「粘贴」+ 进框自检 + 三枪提交（2026-09-18 现场两轮取证后定型）────
print("[D] 发图链：右键「粘贴」进框（不用投递组合键）、进框才打枪、提交三枪")
_SI = SRC.split("def send_image_posted(")[1]
_SI = _SI[:_SI.index("def _file_panel_point_live(")]
ck("D1 粘贴前先投递聚焦输入栏 —— 落点是**现算**的输入框上半部分（`_input_top_band`），"
   "量不到 / 掉进工具栏带就不开枪（2026-09-18 改口径：按比例猜点会落到引用条甚至 ✕ 上）",
   "_input_top_band(gui)" in _SI and "backend.click(main, _img_pt)" in _SI
   and "_img_pt[1] > int(r[1] + rh * 0.92)" in _SI
   and _SI.find("backend.click(main, _img_pt)") < _SI.find("_cb.set_image("))
ck("D2 粘贴改走**右键 → 「粘贴」菜单项**（投递组合键在微信上不成立：退化成字面字母 v）",
   '_right_click_menu_posted(gui, _RX, _RY, "粘贴"' in _SI)
ck("D3 发图链里**再也不许出现投递 Ctrl+V**（它只会把字母 v 敲进输入框）",
   "VK_CONTROL" not in _SI and "keys(child" not in _SI)
ck("D4 粘贴后先做**发送按钮颜色自检**，没进框就一枪都不打",
   "_input_has_content(gui, r)" in _SI
   and _SI.find("_input_has_content(gui, r)") < _SI.find("_shots = ("))
ck("D5 图片**只粘贴一次**（内容留在输入框，多打几枪不会重复发送）",
   _SI.count("_cb.set_image(") == 1 and _SI.count('"粘贴"') == 1)
ck("D6 提交是**三枪**且**回车优先**（回车那枪排在点「发送」之前）",
   "_shots = (" in _SI and "_deadline" in _SI
   and _SI.find("backend.keys(main, [ib.VK_RETURN])") < _SI.find("backend.click(main, send_pt)"))
ck("D7 成功回执写明「第几枪打出去的」（可复盘是哪一枪生效）",
   "第 %d 枪 %s · DB 回读" in _SI)
ck("D8 未生效时如实说「文字可能还留在输入框里」（与发文字同口径）",
   "文字可能还留在输入框里" in _SI)
# D9~D11 颜色自检本身（`_input_has_content` 必须存在、必须用屏幕实拍、量不出时按有内容放行）
_HC = SRC.split("def _input_has_content(")[1][:2200]
# 只查**代码**，不查文档字符串（注释里提 `capture_image` 是解释"为什么不用它"，不算违规）
_HC_CODE = _HC.split('"""', 2)[2] if _HC.count('"""') >= 2 else _HC
ck("D9 进框自检用**屏幕实拍**（ImageGrab），不用会返回缓存帧的 capture_image",
   "ImageGrab.grab" in _HC_CODE and "capture_image" not in _HC_CODE and "capture_best" not in _HC_CODE)
ck("D10 判据＝「发送」按钮的颜色（空框灰 / 有内容绿），且落点与点「发送」用同一个比例",
   "0.932" in _HC and "0.945" in _HC and "green" in _HC)
ck("D11 自检量不出来时按「有内容」放行（前置自检不许把发送链一刀切死；最终仍认 DB 回读）",
   "颜色自检不可用" in _HC and "按有内容继续" in _HC)

# ── E 身份复核：双档互证不许被"活动行时间读不出"翻案（2026-09-18 现场：文字回复连拒三次）──
print("[E] 身份复核：内容 × 会话头标题带 双档命中 ⇒ 直接放行")
_IDN = SRC.split("def chat_identity_ok(")[1][:16000]
_PM_SRC = io.open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
ck("E1 内容命中后先问会话头标题带，命中就放行",
   "self._screen_only_identity(chat_id, gui=gui, name=name)" in _IDN
   and "双档互证，放行" in _IDN)
ck("E2 这一问排在「活动行时间冲突」那套严格逻辑**之前**（否则时间戳读不出就会被翻成否）",
   _IDN.find("双档互证，放行") < _IDN.find("_row_time_conflict(chat_id, gui=gui)"))
_bad_rel = [l.strip()[:40] for l in _PM_SRC.splitlines() if l.strip().startswith("from .")]
ck("E3 相对导入修掉：人性化行为决策改走绝对导入（脚本跑时 `from .` 必抛、功能静默失效）",
   not _bad_rel and "from agent import behavior as bh" in _PM_SRC, str(_bad_rel[:2]))

# ── F 回声表要在**开枪那一刻**就写（2026-09-18 现场：机器人回了自己刚发的图与话）──────────
print("[F] 回声表：开枪前就记（否则回读那几秒会被监听器读成「别人的话」⇒ 回自己）")
_SI2 = SRC.split("def send_text_posted(")[1]
_SI2 = _SI2[:_SI2.index("def send_image_posted(")]
ck("F1 发文字：`_mark_sent(text)` 排在**第一枪之前**（不是等 DB 回读成功之后才记）",
   "_mark_sent(text)" in _SI2
   and _SI2.find("self._mark_sent(text)") < _SI2.find("for _i in range(1, 4)"))
_SI3 = SRC.split("def send_image_posted(")[1]
_SI3 = _SI3[:_SI3.index("def _file_panel_point_live(")]
ck("F2 发图：`_mark_sent(\"[图片]\")` 排在**枪之前**",
   'self._mark_sent("[图片]")' in _SI3
   and _SI3.find('self._mark_sent("[图片]")') < _SI3.find("for _i, (_lbl, _act) in enumerate(_shots, 1)"))
# 行为级：真跑一遍 —— 记过回声的"自己发的"必须被 normalize 丢掉，别人的必须留下
try:
    _ad = WC.WeChatAdapter.__new__(WC.WeChatAdapter)
    _ad.cfg = {"wechat": {}}
    _ad._group_by_wxid = {}
    _ad._nick_map = {}
    _ad._self_wxid = ""
    _ad._self_nickname = ""
    _ad._recent_sent = WC.deque(maxlen=200)
    _ad._LEDGER = WC.deque(maxlen=10)
    _IMG_ROW = {"local_id": 9, "type": "图片", "sender_id": 3, "create_time": int(time.time()),
                "content": '<?xml version="1.0"?><msg><img hdlength="28592"/></msg>'}
    _TXT_ROW = {"local_id": 10, "type": "文本", "sender_id": 3, "create_time": int(time.time()),
                "content": "这图我看不了"}
    _FOREIGN = {"local_id": 11, "type": "文本", "sender_id": 7, "create_time": int(time.time()),
                "content": ("wx" + "id_" + "some" + "oneelse") + ":\n在吗"}
    _ad._mark_sent("[图片]")
    _ad._mark_sent("这图我看不了")
    _img_out = _ad.normalize(dict(_IMG_ROW), "g1")
    _txt_out = _ad.normalize(dict(_TXT_ROW), "g1")
    _f_out = _ad.normalize(dict(_FOREIGN), "g1")
    ck("F3 行为：记过回声的**图片**与自己刚说的话都被丢掉（不再回自己）",
       _img_out is None and _txt_out is None, "图片=%s 文本=%s" % (_img_out, _txt_out))
    ck("F4 行为：别人的话照旧保留（回声窗没误伤群友）",
       bool(_f_out) and str(_f_out.get("text")) == "在吗", str(_f_out)[:60])
except Exception as e:
    ck("F3/F4 回声行为级", False, repr(e)[:90])

# ── G. 2026-09-18 加：两条"现场截图"级别的出站闸门 ────────────────────────────
#   ①群里出现了内部故障话术（「（会话投递失败，本轮未发言。）」「发送失败了，没能发出去。」）；
#   ②同一条消息连发两次（「早上好呀！」×2）。
print("[G] 出站闸门：内部故障话术 + 同会话短窗去重")
try:
    from agent import sender as _sd
    _hit = [_sd._is_internal_failure(x) for x in
            ("（会话投递失败，本轮未发言。）", "发送失败了，没能发出去。", "会话没对上。")]
    ck("G1 现场那三类内部故障话术都会被拦下", all(_hit), str(_hit))
    ck("G2 正常的群聊话不会被误拦",
       not any(_sd._is_internal_failure(x) for x in ("早上好呀！", "才不叫！", "哈哈哈这表情包太可爱了")),
       "")
    _ssrc = io.open(os.path.join(ROOT, "agent", "sender.py"), encoding="utf-8").read()
    ck("G3 拦网接在 send_text_batch 的**发之前**（不是只写在提示词里）",
       "_is_internal_failure(_t)" in _ssrc and "_blocked_internal.append" in _ssrc
       and _ssrc.index("_is_internal_failure(_t)") < _ssrc.index("for _t in parts:\n            _v = _risk.check"))
    ck("G4 同会话短窗去重在位（_DEDUP_WINDOW_S + 与最近自己发过的文本比对）",
       "_DEDUP_WINDOW_S" in _ssrc and "跳过重复发送" in _ssrc and "include_self=True" in _ssrc)
except Exception as e:
    ck("G 出站闸门", False, repr(e)[:90])

print("\n[结论] %d 通过 / %d 失败" % (len(OK), len(BAD)))
if BAD:
    print("失败项：%s" % BAD)
sys.exit(1 if BAD else 0)
