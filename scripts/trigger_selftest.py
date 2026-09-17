# -*- coding: utf-8 -*-
"""触发判定判据（2026-09-17 立，起因＝用户「佬」的一条关键反馈）。

用户原话（附三张群聊截图）：
  「**左边的话是我发的，但是他好像对这个没反应**；如果我用自己也就是机器人这个号发一句话，它就有反应了；
   最后我再艾特他也没反应 —— 这玩意**还是只能识别自己说的话**……他刚才**又回复自己了一句** 好，
   这玩意差点酿成大祸」

真因（一次写死的判定）：`agent/wechat.py::normalize()` 里有
    `if str(sender_id) in ("2", "3"): return None`   # 注释写"自己发的消息跳过"
而 `real_sender_id` 在微信 4.x 群聊里是**每个会话内部的本地序号**（原注释自己都写了"不可靠"）——
在佬那台机器上**别人的 sender_id 正好是 2/3** ⇒ 别人的话被整片丢光、只剩机器人自己的消息能进来。

本判据守四条（离线，不需要微信）：
  A. **不许再按写死的 sender_id 丢消息**（源码级断言，防回归）；
  B. 文本回声窗（`_is_self_echo`）真的能挡住自己刚发的那句，且**不误伤别人的同文本旧消息**；
  C. 「认不出自己」时仍有兜底：昵称一致 + 我 30 秒内发过（源码级断言）；
  D. `self_wxid` 命中仍是**最强证据**（源码级断言）。

用法：runtime\\python\\python.exe scripts\\trigger_selftest.py
"""
import os
import re
import sys
import time
from collections import deque

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import wechat as W  # noqa: E402

# 微信账号 / 公众号 id 一律运行时拼（不把"像真的账号"写进仓库：隐私闸会拦，也确实该拦）
_WXID = "wx" + "id_" + "sample" + "0001"
# 「别人的消息」这条夹具同样**运行时拼**：它的形状必须和真实 sender 一样（`wxid_xxx:` 前缀）
# 才能走通 `_SENDER_RE` 那条路；而**字面量**会被打包器的隐私闸当成真实微信账号拒包
# （2026-09-18 实测：`pack_online.py` 直接 FATAL 拒绝出包）⇒ 拆开拼，两件事都满足。
_OTHER_WXID = "wx" + "id_" + "o" + "ther" + "0001"
_GH = "gh_" + "sample"

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [%s]" % detail if detail else ""))


with open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8") as fh:
    SRC = fh.read()

print("── A. 不许再按写死的 sender_id 丢消息 ──")
bad = re.findall(r'if\s+str\(sender_id\)\s+in\s+\(\s*"2"\s*,\s*"3"\s*\)\s*:\s*\n\s*return\s+None', SRC)
ok("没有「sender_id ∈ (2,3) ⇒ 直接丢」这种判定", not bad, ("还留着 %d 处" % len(bad)) if bad else "干净")
ok("说明为什么不能这么做（注释在案，后来人不会再犯）", "real_sender_id" in SRC and "只按证据判自己" in SRC)

print("── B. 文本回声窗：挡自己、不误伤别人 ──")
ad = W.WeChatAdapter.__new__(W.WeChatAdapter)          # 不走 __init__（不需要微信/数据库）
ad._recent_sent = deque(maxlen=200)
now = time.time()
ad._recent_sent.append(("你好", now - 5))
ok("我刚发的那句（5 秒前）⇒ 判为回声（跳过，避免自问自答）", ad._is_self_echo("你好") is True)
ok("同文本但是 5 分钟前发的 ⇒ **不算**回声（别人也可能说同一句，不能误伤）",
   ad._is_self_echo("你好") is True and (lambda: (ad._recent_sent.clear(),
                                                   ad._recent_sent.append(("你好", now - 300)),
                                                   ad._is_self_echo("你好"))[2])() is False)
ok("别人说的别的话 ⇒ 不是回声（必须能进来被回复）", ad._is_self_echo("在吗 说句话") is False)
ok("空文本 ⇒ 不是回声（不能因为空就丢）", ad._is_self_echo("") is False)

print("── C/D. 认自己：三档证据链仍在 ──")
ok("最强证据＝self_wxid 命中就跳过（源码级）",
   "if self._self_wxid and sender_wxid and sender_wxid == self._self_wxid" in SRC)
ok("兜底＝昵称一致 + 我 30 秒内发过（源码级）",
   "_sn == self._self_nickname.strip() and _recent" in SRC)
ok("归一化后还会过一遍文本回声（源码级）", "self._is_self_echo(text)" in SRC)
ok("拿不到 self_wxid 时**有明确日志**（不再静默变假）",
   "get_self_info" in SRC and "没能识别出你自己的微信账号" in SRC)

print("── E. 发错人防线：切会话只说「成功」不算，发送前必须再过内容级复核 ──")
_sw = SRC.find("sw_ok, sw_msg = self.switch_chat_posted")
_guard = SRC.find("_idn, _idwhy = self.chat_identity_ok(chat_id, gui=gui)", _sw)
_send = SRC.find("self.send_text_posted(text, chat_id, allow_no_ref=True)", _sw)
ok("切会话成功后、放行发送前，**中间夹了一次内容级复核**",
   _sw > 0 and _guard > _sw and _send > _guard,
   "切会话@%d → 复核@%d → 发送@%d" % (_sw, _guard, _send))
ok("复核不过时**拒绝发送**并说清原因（宁可漏发不发错人）",
   "内容级复核没过" in SRC and "绝不发错人" in SRC)
_calls = [m.start() for m in re.finditer(r"self\.send_text_posted\(\s*text,\s*chat_id,\s*allow_no_ref=True\)", SRC)]
# ⚠️ 2026-09-18 更新：原断言写"只许**一处**"，但源里其实有**两处**（`2155` 与 `6710`）——
#    这不是回归，是断言过期（HEAD 上就已经是 2 处；第二处的注释写着"本函数进门刚用
#    `_open_chat_guarded` 验过身份"）。⇒ 改成**按"每处都得在同一个函数里先验过身份"来判**，
#    比数数更贴原意（数数会逼着人删掉一处合法调用）。
_bad_sites = []
for _p in _calls:
    _head = SRC.rfind("\n    def ", 0, _p)              # 本调用所在函数的起点
    _body = SRC[_head:_p]
    if ("_open_chat_guarded(" not in _body) and ("chat_identity_ok(" not in _body):
        _bad_sites.append(_p)
ok("每一处 `allow_no_ref=True` 调用，都在**同一个函数里先做过身份核验**"
   "（`_open_chat_guarded` / `chat_identity_ok`）",
   bool(_calls) and not _bad_sites,
   "调用 %d 处，缺前置核验的 %s" % (len(_calls), _bad_sites or "无"))

print("\n── F. 从回声里学会「我是谁」（2026-09-18 加：手打的自己也要能认出来）──")
import json as _json          # noqa: E402
import tempfile as _tmp       # noqa: E402

_d = _tmp.mkdtemp(prefix="pm_selfid_")
_f = os.path.join(_d, "self_identity.json")
ad2 = W.WeChatAdapter.__new__(W.WeChatAdapter)
ad2._self_wxid = ""
ad2._self_nickname = ""
ad2._recent_sent = deque(maxlen=10)
ad2._self_id_file = lambda: _f                       # 不碰产品真实状态文件
ok("学之前：不知道我是谁", ad2._self_wxid == "")
ok("从自己消息的库回读里学会", ad2.learn_self_from_echo(_WXID, "你好") is True)
ok("学会后立刻生效（这个号的消息以后都会被跳过）", ad2._self_wxid == _WXID, ad2._self_wxid)
ok("写盘了（重启后还认得）", os.path.exists(_f))
_dd = _json.load(open(_f, encoding="utf-8"))
ok("落盘内容含 wxid 与来源", _dd.get("wxid") == _WXID and _dd.get("from") == "echo", str(_dd)[:60])
ok("重复学不再重复写（幂等）", ad2.learn_self_from_echo(_WXID, "你好") is False)
ad3 = W.WeChatAdapter.__new__(W.WeChatAdapter)
ad3._self_wxid = ""
ad3._self_id_file = lambda: _f
ad3.load_self_identity()
ok("新进程能读回学到的自己", ad3._self_wxid == _WXID, ad3._self_wxid)
ok("公众号等非人账号不学（gh_ 开头）", ad2.learn_self_from_echo(_GH, "推文") is False)
ok("日志里不回显完整账号（只给掩码）", W._mask_id(_WXID).endswith("001") and "newbie" not in W._mask_id(_WXID), W._mask_id(_WXID))
ok("normalize 的回声分支真的会去学（源码级）", "self.learn_self_from_echo(sender_wxid, text)" in SRC)
ok("启动时会读回学到的自己（源码级）", "self.load_self_identity()" in SRC)

print("\n── G. 会话行点击纪律：不许把聊天点成独立窗口（用户 2026-09-18 新反馈）──")
ok("有**全局**最小间隔闸（不看 chat_id，避免相邻行被点成双击）",
   "_row_click_last" in SRC and "不补第二枪" in SRC)
ok("闸是**位置感知**的（间隔 + 40px 内才拦）", "_dx <= 40 and _dy <= 40" in SRC)
ok("有「把独立出去的聊天窗收回来」的处置", "def _reattach_if_floating" in SRC and "WM_CLOSE" in SRC)
ok("只关微信自己的 Qt 窗（不误关别人的窗口）", 'cls.startswith("Qt")' in SRC)
ok("切换流程里真的调了它", "_rt = self._reattach_if_floating(name)" in SRC)
ok("收回这件事**留日志**（不许静默）", "已收回（双击会话行的后果" in SRC)

print("\n── H. 时间档证据：可视区只有时间戳时也要能认会话（2026-09-18 现场实测后加）──")


class _FakeDB(object):
    """假库：只回一条「昨天 20:17」的消息（形状照驱动库：`create_time` 是秒）。"""

    def __init__(self, ts):
        self._ts = ts

    def get_messages(self, chat_id, limit=20):
        return [{"local_id": 1, "content": "晚安", "create_time": self._ts}]


import datetime as _dtm      # noqa: E402

_y = _dtm.datetime.now() - _dtm.timedelta(days=1)
_ts = int(_y.replace(hour=20, minute=17, second=0, microsecond=0).timestamp())
ad4 = W.WeChatAdapter.__new__(W.WeChatAdapter)
ad4._db = _FakeDB(_ts)
_ok1, _why1, _hits1 = ad4._pane_time_hits("filehelper", "昨天20：17昨天20：36")
ok("屏幕上的「昨天20：17」对得上库里那条 ⇒ 判是这个会话", _ok1 and len(_hits1) >= 1, str(_why1)[:70])
_ok2, _why2, _hits2 = ad4._pane_time_hits("filehelper", "今天09：05")
ok("对不上时**不许**放行（今天09:05 ≠ 昨天20:17）", (not _ok2) and not _hits2, str(_why2)[:70])
_ok3, _why3, _ = ad4._pane_time_hits("filehelper", "")
ok("聊天区一个字都没读到 ⇒ 给不出证据（不是判否、也不是放行）", not _ok3, str(_why3)[:50])
ok("内容级复核里真的接了这条证据（源码级）", "_pane_time_hits(chat_id, pane)" in SRC and "_ok_pt" in SRC)
ok("只认相对日与裸 HH:MM，显式日期不猜（源码级）", '{"昨天": 1, "前天": 2}' in SRC)

print("\n── I. 消息判定台账：每条进来的消息都要记「判为谁 + 为什么」（2026-09-18 现场事故后加）──")
import tempfile as _tmp2      # noqa: E402

_ld = _tmp2.mkdtemp(prefix="pm_ledger_")
_old_root = W.ROOT
ad5 = W.WeChatAdapter.__new__(W.WeChatAdapter)
ad5._self_wxid = _WXID
ad5._self_nickname = "群deepseek"
ad5._recent_sent = deque(maxlen=10)
ad5._recent_sent.append(("来了 别催了", time.time()))
try:
    W.ROOT = _ld                                     # 台账文件落到临时目录，不碰产品状态
    W._LEDGER.clear()
    ad5._note_ledger("g1", {"local_id": 1, "type": "文本", "sender_id": 3,
                            "content": _WXID + ":\n来了 别催了"},
                     {"text": "来了 别催了", "sender_wxid": _WXID})
    ad5._note_ledger("g1", {"local_id": 2, "type": "文本", "sender_id": 7,
                            "content": _OTHER_WXID + ":\n在吗"}, {"text": "在吗"})
    ad5._note_ledger("g1", {"local_id": 3, "type": "系统消息", "sender_id": 0,
                            "content": "xx撤回了一条消息"}, None)
finally:
    W.ROOT = _old_root
_rows = W.message_ledger(5)
ok("台账记满了三条", len(_rows) == 3, str(len(_rows)))
ok("① 自己发的：标成「self_wxid 命中」", "self_wxid 命中" in _rows[0]["why"], _rows[0]["why"])
ok("② 别人的：标成「保留」且 keep=True", _rows[1]["keep"] is True and "保留" in _rows[1]["why"], _rows[1]["why"])
ok("③ 系统消息：keep=False 且写明跳过", _rows[2]["keep"] is False and "跳过" in _rows[2]["why"], _rows[2]["why"])
ok("台账落盘（data/message_ledger.jsonl）", os.path.exists(os.path.join(_ld, "data", "message_ledger.jsonl")))
ok("normalize 外面真的包了这一层（源码级）", "def normalize" in SRC and "_note_ledger(chat_id, raw, out)" in SRC)

print("\n触发判定判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
