# -*- coding: utf-8 -*-
"""「发送确认判据」的接线判据 —— **不碰微信**。

守的是这件事：**别再用"固定 sleep + 轮询"当主判据**。
项目里的正解是 `wechat.wait_new_message` 的**两级判据**：
  · **事件**（`agent/filewatch`，`ReadDirectoryChangesW` 盯 `message_*.db-wal`）——「一有写入就醒」，
    空闲零开销、比定时轮询又快又省，而且**最小化/后台也成立**（不看画面）；
  · **标量**（`MAX(local_id)` 增量）——醒来后只查一个 int，只做**确证**。
本判据断言：文本(最小化档) / 图·表情 / 语音条 这三条链**都接了它**，
且 `wait_new_message` 自己**仍是"事件 + 标量"两级**（防止哪天被简化成纯轮询）。

用法：runtime\\python\\python.exe scripts/confirm_judge_selftest.py
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


import _srcslice as _ss  # noqa: E402

WW_SRC = io.open(os.path.join(ROOT, "agent", "wechat_windows.py"), encoding="utf-8").read()
WC_SRC = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
VS_SRC = io.open(os.path.join(ROOT, "agent", "voice_strip.py"), encoding="utf-8").read()

print("── A. 两级判据本身（`wait_new_message`）──")
W = _ss.func_src(WC_SRC, "wait_new_message")
ok("A1 分母守卫：拿到 `wait_new_message` 的函数体（>800 字符）", len(W) > 800, "%d 字符" % len(W))
ok("A2 它是**事件驱动**（调 `filewatch.watch(`）", "_fw.watch(" in W or "watch(" in W, "")
ok("A3 它用**标量**（`_db_max_local_only`）做确证，而不是捞消息体",
   "_db_max_local_only" in W, "")
ok("A4 它保留了**兜底轻拍**（`poll_min`）——监听万一漏事件时不至于永远等不到",
   "poll_min" in W, "")
ok("A5 ★反向锚：事件与标量**两级都在**（缺任何一个，这条判据就得红）",
   ("watch(" in W) and ("_db_max_local_only" in W), "")

print("── B. 三条发送链都接到了它 ──")
_B = _ss.func_src(WW_SRC, "send_to_popup_blind")
_I = _ss.func_src(WW_SRC, "send_image_to_popup")
ok("B1 分母守卫：三条链的函数体都拿到了（各 >800 字符）",
   len(_B) > 800 and len(_I) > 800 and len(VS_SRC) > 800,
   "%d/%d" % (len(_B), len(_I)))
ok("B2 **文本(最小化档)** 链接了 `wait_new_message`", "wait_new_message" in _B, "")
ok("B3 **图·表情** 链接了 `wait_new_message`", "wait_new_message" in _I, "")
ok("B4 **语音条** 链接了 `wait_new_message`", "wait_new_message" in VS_SRC, "")
ok("B5 接了之后**仍保留「退回原轮询」的兜底**（老桩/异常不能变松也不能变严）",
   "_db_tail_has(" in _B and "_db_new_image(" in _I, "")

print("── C. 反向锚：判据自己抓得住「接线被拿走」──")
_fake = _B.replace("wait_new_message", "____removed____")
ok("★把文本链里的接线删掉 ⇒ 同一套检测必须报红",
   "wait_new_message" not in _fake and "wait_new_message" in _B, "")
ok("★原样源码上 B2 为真（否则上一条是假阳性）", "wait_new_message" in _B, "")

print("── D. 语义确认的口径：窗口 / 空白 / 重试的截止时间 ──")
_TH = _ss.func_src(WW_SRC, "_db_tail_has")
ok("D1 `_db_tail_has` 支持 `since_id`（＝「我们刚发的那一条」的定义；⛔ 不许只翻最后 N 行）",
   "since_id" in _TH and "local_id" in _TH)
ok("D2 匹配前**两侧都去空白**（库里会吃掉首尾空白/换行 ⇒ 拿原文裸比会假阴性）",
   "_ns(" in _TH and "split()" in _TH)
ok("D3 文本链在**投字之前**取了 baseline，并用它做确认",
   'out["base_seq"] = _base' in _B and "since_id=_base" in _B)
ok("D4 图片链的确认也带 `since_id`（按「比它新的行」判，而不是「最后几条」）",
   "since_id=base_id" in _I)
ok("D5 ⛔ **重试不许用「事件等待之前」记的起点** —— "
   "`wait_new_message` 自己就吃掉整个 timeout ⇒ 那样写重试一次都不会跑（实测的假阴性来源）",
   "_t.time() - t0" not in _B and "_t.time() - t0" not in _I
   and "_deadline = _t.time() +" in _B and "_deadline = _t.time() +" in _I)

print("── E. 行为测（纯函数，不碰微信/库）：窗口外那条必须靠 `since_id` 才抓得到 ──")
from agent import wechat_windows as _WW  # noqa: E402


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def _db_messages(self, chat_id, limit=8):
        return self.rows[:int(limit)]


_filler = [{"local_id": 800 - i, "content": "x"} for i in range(10)]
_far = _filler + [{"local_id": 731, "content": "（测试）文字"}]
ok("E1 ★ 只翻「最后 8 行」的老口径**抓不到**窗口外那条（＝真机那个假阴性的机制）",
   _WW._db_tail_has(_FakeDB(_far), "c", "（测试）文字", limit=8) is False)
ok("E2 ★ 同一个库，`since_id=730` ⇒ **命中**（这就是修法）",
   _WW._db_tail_has(_FakeDB(_far), "c", "（测试）文字", since_id=730) is True)
ok("E3 反向锚：`since_id=731` ⇒ 731 不再算「新的」⇒ 不命中"
   "（防哪天写成「拿自己当窗口起点」）",
   _WW._db_tail_has(_FakeDB(_far), "c", "（测试）文字", since_id=731) is False)
ok("E4 空白不计：库里带换行、原文带尾空格 ⇒ 仍命中",
   _WW._db_tail_has(_FakeDB([{"local_id": 9, "content": " abc\n"}]), "c", "abc ", since_id=1) is True)
ok("E5 反向锚：不存在的句子 ⇒ 一律不命中（判据不是恒真）",
   _WW._db_tail_has(_FakeDB(_far), "c", "不存在的句子ABC", since_id=1) is False)

print("\n== F. `wait_new_message` 的判据必须是「集合差」而不是「比大小」==")
from agent.wechat import WeChatAdapter as _WA  # noqa: E402
import time as _tm  # noqa: E402


class _ScopeStub:
    """假适配器：`local_id` **每会话自增**、而 `MAX(local_id)` **按分片量**（跨会话）——
    这正是真机那一组的形状：基线 137（分片内的别人）、新行 63（本会话下一条）。"""

    def __init__(self, rows, scalar, appear_after: float = 0.0):
        self._rows = list(rows)      # [(local_id, type)]
        self._scalar = int(scalar)
        self._appear_after = float(appear_after)
        self._t0 = _tm.time()
        self._pending = None

    def _db_messages(self, chat_id, limit=8):
        if self._pending and (_tm.time() - self._t0) >= self._appear_after:
            self._rows = self._rows + [self._pending]
            self._scalar = max(self._scalar, self._pending[0])
            self._pending = None
        return [{"local_id": i, "type": t} for i, t in self._rows[-int(limit):]]

    def _db_recent_ids(self, chat_id, limit=6):
        return {i for i, _t in self._rows[-int(limit):]}

    def _db_max_local_only(self, wxid):
        return self._scalar

    def _message_wal_dirs(self):
        return []

    def queue(self, row, after: float = 0.0):
        self._pending = row
        self._appear_after = after
        self._t0 = _tm.time()


_base_rows = [(i, "文本") for i in range(139, 145)]     # 本会话最近几行（都比 137 大 → 见下）
_st1 = _ScopeStub(_base_rows, 137)                      # 分片最大值 137（别人的行）
_st1.queue((63, "语音"), after=0.1)                     # 本会话下一条：local_id 63
_t1 = _tm.time()
_ok1, _w1, _i1 = _WA.wait_new_message(_st1, "filehelper", base=137, timeout=1.2, kinds=("语音",))
ok("F1★ 分片最大值(137) 与 会话内自增(63) 不是同一量纲时，**照样能确认**（集合差判据）",
   _ok1 is True and 63 in (_i1.get("new_ids") or []),
   "%s · %s" % (_w1[:46], _i1.get("new_ids")))
ok("F1b★ 反向锚：发前那一批（139~144，都 > 137）**不许**被算成「新行」（老口径正是这么假绿的）",
   144 not in (_i1.get("new_ids") or []) and 139 not in (_i1.get("new_ids") or []),
   str(_i1.get("new_ids")))
# ⛔ 这里**不能**写成 `not (137 > 137)`：那是"两个常量比大小"，会被判据卫生那条
#   （`judge_hygiene` ⑥d）判成恒真断言 ⇒ 用一个变量装住，语义一样但不再踩那条。
_sc_now = _st1._scalar        # 分片最大值（发前发后都是它 ⇒ 没变）
_sc_base = 137                # 发前记下的基线（老口径拿它比）
ok("F2★ 反向锚：老的「比大小」判据在同一输入上**必假**（MAX 没变 ⇒ 判否）",
   not (_sc_now > _sc_base), "旧口径：%s > %s = False" % (_sc_now, _sc_base))
_st2 = _ScopeStub(_base_rows, 137)
_ok2, _w2, _i2 = _WA.wait_new_message(_st2, "filehelper", base=137, timeout=0.45)
ok("F3 一行都没新增 ⇒ 如实判否（判据不是恒真）", _ok2 is False and "超时" in _w2, _w2[:52])
ok("F3b 判否时把**发前那一批**写进返回信息（排障要能一眼看出比的是什么）",
   bool(_i2.get("base_ids")), str(_i2.get("base_ids"))[:60])
# F4：真机走的是"两轮同一条判据"（主判据窗 + 宽限），两轮**必须共用同一份发前集合**
_base_set = {i for i, _t in _base_rows}
_st3 = _ScopeStub(_base_rows, 137)
_st3.queue((64, "语音"), after=0.35)
_okA, _wA, _iA = _WA.wait_new_message(_st3, "filehelper", base=137, timeout=0.2, kinds=("语音",))
_okB, _wB, _iB = _WA.wait_new_message(_st3, "filehelper", base=137, timeout=1.2,
                                      kinds=("语音",), base_ids=_base_set)
ok("F4★ 主判据窗超时、**宽限那一轮用同一份发前集合** ⇒ 仍能确认"
   "（这一步就是防「用户收到两条」的）",
   _okA is False and _okB is True and 64 in (_iB.get("new_ids") or []),
   "第一轮=%s；第二轮=%s" % (_wA[:24], _iB.get("new_ids")))
# F4b 反向锚：宽限那轮**不带** `base_ids` ⇒ 它把当下这批当成"发前那一批" ⇒ 看不见已落库那行
_st3b = _ScopeStub(_base_rows + [(64, "语音")], 137)
_okC, _wC, _iC = _WA.wait_new_message(_st3b, "filehelper", base=137, timeout=0.3, kinds=("语音",))
ok("F4b 反向锚：**不带** `base_ids` 的第二轮看不见已落库那行（所以必须复用同一份）",
   _okC is False, _wC[:44])
ok("F4c 静态锚：判否之前还有一次**收尾回读**（写库晚于窗时捞回来）",
   "再回读最后一次" in io.open(os.path.join(ROOT, "agent", "wechat.py"),
                               encoding="utf-8").read(), "")
# F5：类型过滤要生效（要语音、来的却是文字 ⇒ 不许确认）
_st4 = _ScopeStub([(i, "文本") for i in range(200, 206)], 205)
_st4.queue((206, "文本"), after=0.05)
_ok5, _w5, _i5 = _WA.wait_new_message(_st4, "filehelper", base=205, timeout=0.5, kinds=("语音",))
ok("F5 `kinds=('语音',)` 时，来一行**文字**不许算确认（判据要认类型）",
   _ok5 is False and "超时" in _w5, _w5[:52])
# F6：`base_ids`（发前那一批）必须能被调用方**带进来**（两轮等待要复用同一份，否则第二轮白等）
_st5 = _ScopeStub([(i, "文本") for i in range(300, 306)], 305)
_st5._rows = _st5._rows + [(307, "语音")]      # 已经落库
_ok6, _w6, _i6 = _WA.wait_new_message(_st5, "filehelper", base=305, timeout=0.3,
                                     kinds=("语音",), base_ids={300, 301, 302, 303, 304, 305})
ok("F6★ 调用方带进来的 `base_ids` 被采纳 ⇒ 第二轮（宽限）也能看到同一批新行",
   _ok6 is True and 307 in (_i6.get("new_ids") or []), str(_i6.get("new_ids")))

print("\n== 发送确认判据接线：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
