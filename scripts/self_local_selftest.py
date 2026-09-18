#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「这条库行是不是我自己发的」判据（2026-09-18 用户两次反馈后立）。

**报障原话**：「**他有时候还是会把自己识别成别人**」。

**查到的事实**：判自己原来只有三档证据 —— ① `self_wxid` 命中 ② 昵称一致 + 我刚发过
③ 文本回声窗（默认 120 秒）。三条**都不是"行号"**，所以两处必然漏判：
  ⓐ 发图 / 发表情 / 发文件回读出来的 `text` 是 `[图片]` / `[表情]` / `[文件/链接/卡片]`
     —— 这类"无语义文本"在回声窗里对不上任何东西，**永远判不出自己**；
  ⓑ 手打一句（用户拿机器人号自己打的、不在我们发送台账里）或任何超出回声窗的回读
     ⇒ 被当成别人的话 ⇒ **机器人回自己**（7-8 条互吵那次就是这个根因链）。
**修法**：我们自己发出的消息，**发送成功那一刻**已经从 DB 回读到了它的 `local_id`
（`send_text_posted` / `send_image_posted` / `send_file_posted` 的三个确认点）⇒ 把
`(会话, 行号, 库行 create_time)` 记下来，监听侧按行号判自己（`agent/wechat.py::is_self_local`）。

本判据守四件事（顺序＝优先级）：
  ① **命中就是自己**：行号 + 时间都对得上 ⇒ 判自己（不依赖文本、不依赖回声窗）；
  ② **绝不把别人的话丢掉**（比漏判回声更糟）—— 只有"行号命中 **且** 时间接近"才算；
     用户「清空聊天记录」会把消息表整张删掉、`local_id` 从 1 重新开始 ⇒ 撞号必须靠时间挡掉；
  ③ **只在 DB 回读确认之后登记**（登记错＝把别人的消息登记成自己的 ⇒ 漏回）；
  ④ **覆盖无语义文本**：发图登记要求回读那行**确实是图片**、发文件要求**确实是文件类**。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from agent import wechat as W          # noqa: E402

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   " + name)
    else:
        FAIL += 1
        print("  FAIL " + name + ("   [%s]" % extra if extra else ""))


class _Shim(object):
    """只借 `WeChatAdapter` 的自我行号那几个方法（不碰微信、不碰数据库）。"""

    _SELF_LOCAL_WIN_S = W.WeChatAdapter._SELF_LOCAL_WIN_S
    _SELF_LOCAL_KEEP_S = W.WeChatAdapter._SELF_LOCAL_KEEP_S
    _ct_s = staticmethod(W.WeChatAdapter._ct_s)
    _self_local_file = W.WeChatAdapter._self_local_file
    _load_self_local = W.WeChatAdapter._load_self_local
    _save_self_local = W.WeChatAdapter._save_self_local
    remember_self_local = W.WeChatAdapter.remember_self_local
    is_self_local = W.WeChatAdapter.is_self_local
    db_account = W.WeChatAdapter.db_account

    def __init__(self, path, acct="wxid_self_0001"):
        self._path = path
        self._self_local = {}
        self._self_local_loaded = False
        # 账号（2026-09-19）：台账按账号隔离，夹具要像真 adapter 一样报得出"在读哪个号"
        self._db = type("D", (), {"account": acct})()

    def _self_local_file(self):           # 覆盖掉类方法（它指向产品 data/ 目录）
        return self._path


TMP = os.path.join(ROOT, "_scratch", "tmp-self-local")
os.makedirs(TMP, exist_ok=True)
DBF = os.path.join(TMP, "self_local_ids.json")
if os.path.exists(DBF):
    os.remove(DBF)

print("── A. create_time 归一化（秒/毫秒/垃圾值）──")
ok("秒原样", W.WeChatAdapter._ct_s(1758100000) == 1758100000)
ok("毫秒转秒", W.WeChatAdapter._ct_s(1758100000000) == 1758100000)
ok("空/垃圾值 ⇒ 0（不抛）", W.WeChatAdapter._ct_s(None) == 0 and W.WeChatAdapter._ct_s("x") == 0)

print("── B. 没记录 = 不判自己（红线：绝不影响别人）──")
s1 = _Shim(DBF)
ok("任意行号 ⇒ False", s1.is_self_local("群A", 4242, 1758100000) is False)
ok("空行号/垃圾行号 ⇒ False", s1.is_self_local("群A", None) is False
   and s1.is_self_local("群A", "abc") is False)

print("── C. 登记后再读到同一行 ⇒ 判自己（不依赖文本）──")
s1.remember_self_local("群A", 101, 1758100000)
ok("同会话同行号+同时间 ⇒ True", s1.is_self_local("群A", 101, 1758100000) is True)
ok("同一行在**别的会话**里 ⇒ False（会话隔离）",
   s1.is_self_local("群B", 101, 1758100000) is False)
ok("[表情]/[图片] 这类无语义文本也判得出自己（本判据要的就是这条）",
   s1.is_self_local("群A", 101, 1758100000) is True)

print("── D. 撞号保护：清空聊天记录后 local_id 重排（行号同、时间不同 ⇒ 不判自己）──")
ok("时间差 599 秒 ⇒ 仍判自己", s1.is_self_local("群A", 101, 1758100000 + 599) is True)
ok("时间差 601 秒 ⇒ **不判自己**（按别人处理）",
   s1.is_self_local("群A", 101, 1758100000 + 601) is False)
ok("入参时间缺失（0）⇒ 只按行号认（宁可信自己这一档，避免漏判回声）",
   s1.is_self_local("群A", 101, None) is True)

print("── E. 过期清理：超过 24 小时的记录不再认 ──")
s1.remember_self_local("群C", 202, 1758200000)
s1._self_local["群C"] = [(202, 1758200000, time.time() - 25 * 3600.0)]
ok("25 小时前的记录 ⇒ 不判自己", s1.is_self_local("群C", 202, 1758200000) is False)
s1._self_local["群C"] = []
s1._save_self_local()
s2 = _Shim(DBF)
s2.remember_self_local("群C", 203, 1758300000)
s2._self_local["群C"] = [(203, 1758300000, time.time() - 25 * 3600.0)]
s2._save_self_local()
s3 = _Shim(DBF)
ok("落盘过滤：重载后过期记录**不复活**", s3.is_self_local("群C", 203, 1758300000) is False)

print("── F. 持久化：重启进程后仍然认得出自己发过的行 ──")
s4 = _Shim(DBF)
s4.remember_self_local("群D", 300, 1758400000)
s5 = _Shim(DBF)
ok("新进程读回 ⇒ True", s5.is_self_local("群D", 300, 1758400000) is True)
ok("落盘是原子写（temp + os.replace）", not os.path.exists(DBF + ".tmp"))
with open(DBF, encoding="utf-8") as fh:
    import json as _json
    _d = _json.load(fh)
ok("落盘结构 = {账号, 会话: [[行号, create_time, 记录时刻]]}",
   isinstance(_d, dict) and isinstance(_d.get("rows"), dict)
   and isinstance(_d["rows"].get("群D"), list)
   and len(_d["rows"]["群D"][0]) == 3, str(_d)[:110])
ok("落盘带**账号**（2026-09-19 起：切号后台账不能跨账号复用）",
   _d.get("acct") == "wxid_self_0001", str(_d.get("acct")))

print("── F2. 账号闸：换到另一个微信号后，旧号的行号一律不认（否则会静默漏回）──")
_sB = _Shim(DBF, acct="wxid_other_0002")
ok("另一个账号读同一份台账 ⇒ 不判自己",
   _sB.is_self_local("群D", 300, 1758400000) is False)
ok("同账号照样认（闸门没把正常路堵死）",
   _Shim(DBF, acct="wxid_self_0001").is_self_local("群D", 300, 1758400000) is True)
_LEGACY = os.path.join(TMP, "legacy.json")
with open(_LEGACY, "w", encoding="utf-8") as _fh:
    import json as _j2
    _j2.dump({"群D": [[300, 1758400000, int(time.time())]]}, _fh)
ok("老格式台账（没账号信息）整份不用 —— 宁可漏判一次回声，也绝不丢掉别人的话",
   _Shim(_LEGACY).is_self_local("群D", 300, 1758400000) is False)

print("── G. 同一行重复登记只留一条（多枪发送会重复确认）──")
s4.remember_self_local("群D", 300, 1758400000)
s4.remember_self_local("群D", 300, 1758400000)
ok("去重后只有一条", len([r for r in s4._self_local["群D"] if r[0] == 300]) == 1)
s6 = _Shim(os.path.join(TMP, "cap.json"))
for i in range(1, 260):
    s6.remember_self_local("群E", i, 1758500000 + i)
ok("每会话上限 200 条（超了不无限长）", len(s6._self_local["群E"]) == 200)

print("── H. 落盘失败不许抛（它只是安全网，不该挡住发送）──")
s7 = _Shim(os.path.join(TMP, "不存在的盘符Z", "x.json"))
try:
    s7.remember_self_local("群F", 400, 1758600000)
    ok("坏路径 ⇒ 不抛异常、进程内仍然生效", s7.is_self_local("群F", 400, 1758600000) is True)
except Exception as e:                     # noqa: BLE001
    ok("坏路径 ⇒ 不抛异常、进程内仍然生效", False, repr(e))

print("── I. 回读那行「确实是图片 / 确实是文件类」才登记（登记错＝丢别人的话）──")
ok("type='图片' ⇒ 是图片", W.WeChatAdapter._looks_like_img_msg({"type": "图片"}) is True)
ok("type_name='图片' ⇒ 是图片", W.WeChatAdapter._looks_like_img_msg({"type_name": "图片"}) is True)
ok("type='文本' ⇒ 不是图片", W.WeChatAdapter._looks_like_img_msg({"type": "文本"}) is False)
ok("type='动画表情' ⇒ **不是**图片（表情走自己那条链，不许混进发图登记）",
   W.WeChatAdapter._looks_like_img_msg({"type": "动画表情"}) is False)
ok("空行 ⇒ False", W.WeChatAdapter._looks_like_img_msg(None) is False
   and W.WeChatAdapter._looks_like_img_msg({}) is False)
ok("文件类判据仍在（type='文件/链接/卡片'）",
   W.WeChatAdapter._looks_like_file_msg({"type": "文件/链接/卡片"}) is True
   and W.WeChatAdapter._looks_like_file_msg({"type": "文本"}) is False)

print("── J. 源码级接线：判定位置与三个登记点 + 记账是安全入口（防以后有人改错位置/改坏条件）──")
_SRC = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
_impl = _SRC[_SRC.index("def _normalize_impl("):]
_impl = _impl[:_impl.index("def _note_ledger(")] if "def _note_ledger(" in _impl else _impl
_impl = _SRC[_SRC.index("def _normalize_impl("):]
_impl = _impl[:_impl.index("\n    def ", 10)]
_i_recall = _impl.index("撤回事件")
_i_selflocal = _impl.index("self.is_self_local(chat_id, local_id, create_time)")
_i_textskip = _impl.index("自己发的消息跳过（避免自问自答）")
ok("判定在**撤回分支之后**（自己撤回要照旧处理）", _i_recall < _i_selflocal)
ok("判定在**文本判自己之前**（行号是唯一不依赖文本的证据）", _i_selflocal < _i_textskip)
ok("判定为 None 就 return（跳过这条消息）",
   "if self.is_self_local(chat_id, local_id, create_time):\n                return None" in _impl)
ok("判据异常时**按未命中继续**（不许因为判自己崩掉监听）",
   "自我行号判定异常（按未命中继续）" in _impl)

_seg = _SRC[_SRC.index("def send_text_posted("):]
_seg = _seg[:_seg.index("def send_image_posted(")]
ok("发文字：登记在『内容与本次一致』确认之内",
   'if str(text)[:20] in str(head.get("content") or ""):' in _seg
   and _seg.index("_self_local_note") > _seg.index('if str(text)[:20] in str(head.get("content") or ""):'))
ok("发文字：内容不一致那条『宽松成功』分支**不登记**",
   _seg.count("_self_local_note") == 1)

_seg2 = _SRC[_SRC.index("def send_image_posted("):]
_seg2 = _seg2[:_seg2.index("def send_file_posted(")]
ok("发图：登记被 `_looks_like_img_msg` 挡着",
   "if self._looks_like_img_msg(top):" in _seg2
   and _seg2.count("_self_local_note") == 1)

_seg3 = _SRC[_SRC.index("def send_file_posted("):]
_seg3 = _seg3[:_seg3.index("\n    def ", 10)]
ok("发文件：登记被 `_looks_like_file_msg` 挡着",
   "if self._looks_like_file_msg(top):" in _seg3
   and _seg3.count("_self_local_note") == 1)

ok("记账走模块级安全入口（没这能力的假对象也不许把成功判成失败）",
   "def _self_local_note(obj, chat_id, local_id, create_time=None)" in _SRC)
print("── K. 台账留痕：判自己必须能看出『靠行号判的』──")
ok("台账带 self_local_hit 字段", '"self_local_hit": bool(sl_hit)' in _SRC)
ok("台账 why 里写明行号命中",
   "判为自己：自家行号命中" in _SRC)
ok("台账把行号判据单拎出来算（不再只看 wxid/回声）",
   "sl_hit = self.is_self_local(" in _SRC)

print("\n==== 自我行号判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
