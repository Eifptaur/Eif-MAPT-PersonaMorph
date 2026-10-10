# -*- coding: utf-8 -*-
"""already_sent_selftest.py —— `already_sent_recently` 三态语义判据（P0-7）

为什么必须有这个判据：本函数的**全部价值在"三态"** ——
  `True`（已发过）/ `False`（确实没发过）/ **`None`（读不出来）**。
若有人把它改成"读不出来也返回 False"，就会把"读不出来"变成"没发出去" ⇒ **重发** ⇒
群里两条一样的话（正是用户截图里「早上好呀！」连发两次的成因）。
⇒ 所以本判据**逐条钉死三态**，并含**反向锚**（源码层面：不许把 None 分支删掉）。

用法：./runtime/python/python.exe -u scripts/already_sent_selftest.py
"""
import logging
import os
import sys
import time

logging.disable(logging.INFO)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent import wechat as W  # noqa: E402

FAILS = []
OKS = []


def chk(name, cond, extra=""):
    (OKS if cond else FAILS).append(name)
    print("  %s %s%s" % ("OK  " if cond else "FAIL", name, ("  ← " + extra) if (extra and not cond) else ""))


class _Stub:
    """只提供 `_db_recent_rows` 与 `_bare_key`，不碰 GUI/DB。"""
    _bare_key = staticmethod(lambda x: x)

    def __init__(self, rows):
        self._rows = rows

    def _db_recent_rows(self, wxid, limit=25):
        return self._rows


def main():
    cls = [v for v in vars(W).values() if isinstance(v, type) and hasattr(v, "already_sent_recently")]
    chk("类上仍定义 already_sent_recently", bool(cls), "函数被删了")
    if not cls:
        print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(OKS), len(FAILS)))
        return 1
    C = cls[0]
    call = lambda rows, text, w=120: C.already_sent_recently(_Stub(rows), "g", text, window_s=w)
    now = time.time()

    def sr(text, age=5):
        return {"self": True, "text": text, "ts": int((now - age) * 1000)}

    print("== A. 三态（核心） ==")
    chk("已发过 ⇒ True", call([sr("你好")], "你好") is True)
    chk("没发过 ⇒ False", call([sr("你好")], "早上好") is False)
    chk("★读不出来 ⇒ None（不许退化成 False）", call(None, "你好") is None,
        "退化成 False ⇒ 会把'读不出来'当'没发出去' ⇒ 重发")

    print("== B. 只看自己发的 ==")
    chk("别人发的同文本 ⇒ False",
        call([{"self": False, "text": "你好", "ts": int(now * 1000)}], "你好") is False)

    print("== C. 时间窗 ==")
    chk("超出窗口 ⇒ False", call([sr("你好", 999)], "你好", 120) is False)
    chk("窗口内 ⇒ True", call([sr("你好", 60)], "你好", 120) is True)
    chk("秒级 ts 也认 ⇒ True",
        call([{"self": True, "text": "你好", "ts": int(now - 5)}], "你好") is True)

    print("== D. 匹配口径（保守：完全相等） ==")
    chk("子串不算（'你好呀' vs '你好'）⇒ False", call([sr("你好呀")], "你好") is False)
    chk("空文本 ⇒ False", call([sr("")], "") is False)
    chk("前后空白被 strip 后相等 ⇒ True", call([sr(" 你好 ")], "你好") is True)

    print("== E. 异常健壮性 ==")
    class Bad(_Stub):
        def _db_recent_rows(self, wxid, limit=25):
            raise RuntimeError("boom")
    try:
        r = C.already_sent_recently(Bad(None), "g", "你好")
        chk("读库抛异常时不崩（返回 None 或 False）", r in (None, False), "返回 %r" % r)
    except Exception as e:
        chk("读库抛异常时不崩", False, str(e)[:60])

    print("== F. 反向锚（源码层） ==")
    src = open(os.path.join(_ROOT, "agent", "wechat.py"), encoding="utf-8").read()
    chk("仍存在 'return None' 的读不出来分支", "return None            # ⛔ 读不出来" in src or
        "读不出来 ⇒ 三态里的 None" in src, "None 分支被删 ⇒ 三态塌成两态")
    chk("函数仍被定义", "def already_sent_recently" in src)
    chk("底层读法 _db_recent_rows 仍在", "def _db_recent_rows" in src)

    print()
    print("== G. 发送链真的接了「三层去重」 ==")
    # 报障原话：「旧版和今天这版都有用着用着重复发好几条同样消息的情况」。
    # 只把 `already_sent_recently` 写出来不算数 —— 它的 docstring 早就写着
    # "每次发送都要调"，可**发送链从来没调过**（全库只有一处调用，且不是发送路径）。
    _send = open(os.path.join(_ROOT, "agent", "sender.py"), encoding="utf-8").read()
    chk("G0 ★第 0 层：本批内部去重（同一批里出现两次只留第一条）",
        # ⚠️ 锚点跟着"抽取后的位置"走：三层去重从 `send_text_batch` 抽成了独立函数
        #    （那函数已 300+ 行、被结构棘轮盯着）⇒ 判据认的是**同文件内的实现**，
        #    不是"必须长在 send_text_batch 里"（那会一重构就假红）。
        "def _dedup_in_batch(" in _send and "_dedup_in_batch(parts, part_src)" in _send,
        "本批自带的重复没人拦 ⇒ 一轮里能原样发出两条一样的")
    chk("G1 第 1 层：内存回望的取样条数已调离 12",
        "_DEDUP_MEM_LIMIT" in _send and "limit=12" not in _send,
        "仍是 limit=12 ⇒ 群里一热闹，自己刚发的那条就被挤出窗口、去重形同虚设")
    chk("G2 ★第 2 层：发送链**真的调了** `already_sent_recently`（从消息库回读）",
        "already_sent_recently(" in _send,
        "没接 ⇒ 超过 20 秒的重发、跨进程/重启后的重发全拦不住（正是报障的来路）")
    chk("G3 回读窗口是独立常量（不与 20 秒那层混用，两个语义不能串）",
        "_DEDUP_DB_WINDOW_S" in _send)
    # ⛔ 三态语义不许塌成两态：`None`（读不出来）**既不许**当"已发过"（那会让库一故障
    #    就整批不发），**也不许**当"没发过"去重发（那正是原来连发两条的成因）。
    chk("G4 ★三态只在 `is True` 时拦（`None` / `False` 都放行）",
        "if _dup is True:" in _send,
        "写成 `if _dup:` 会把 None 也拦掉 ⇒ 读不到库时整批不发")
    chk("G5 拦下时**如实记账**（进 deduped 分账，不静默吞掉）",
        "_deduped.append(_t)" in _send)

    print()
    print("== H. 反向锚：把这三层改坏 ⇒ 上面的判据必须报红 ==")
    # ⛔ 写法要点：反向锚要**把 G 段那条判据式原样拿来求值**，不能自己另写一个近似的条件
    #   （另写的只能证明"我以为它会红"，证明不了判据本身有效）。
    _g1 = _send.replace("already_sent_recently(", "REMOVED_CALL(")
    chk("H1 ★删掉消息库回读 ⇒ G2 那条判据式当场变假（说明它真在起作用）",
        not ("already_sent_recently(" in _g1))
    _g2 = _send.replace("if _dup is True:", "if _dup:")
    chk("H2 ★把三态写成两态 ⇒ G4 那条判据式当场变假",
        not ("if _dup is True:" in _g2))
    _g3 = _send.replace("limit=_DEDUP_MEM_LIMIT", "limit=12")
    chk("H3 ★把取样条数改回 12 ⇒ G1 那条判据式当场变假",
        not ("_DEDUP_MEM_LIMIT" in _g3 and "limit=12" not in _g3))

    print()
    print("== I. 真跑一遍：库里已有这条 ⇒ 必须拦住不发 ==")
    # ⛔ 安全前提：**`wechat` 传的是桩**，所有发送动作都落进桩里记着
    #   ⇒ 这个测试**不可能真往微信发**（桩缺方法时只会 AttributeError，不会发出去）。
    try:
        from agent import sender as _S

        class _Hold:  # `fg_hold` 在真实现里是上下文管理器
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _StubW:
            _chat_generation = 0

            def __init__(self, dup):
                self.dup = dup
                self.calls = []

            def already_sent_recently(self, *a, **k):
                return self.dup

            def fg_hold(self, *a, **k):
                return _Hold()

            def member_name(self, *a, **k):
                return "某人"

            def reply_quote(self, *a, **k):
                return (True, "")

            def send_image(self, *a, **k):
                self.calls.append(("img", a)); return (True, "")

            def send_text(self, *a, **k):
                self.calls.append(("txt", a)); return (True, "")

            def send_text_at(self, *a, **k):
                self.calls.append(("at", a)); return (True, "")

        class _StubStore:
            def recent(self, *a, **k):
                return []

            def append_self(self, *a, **k):
                return None

        _ck = "group:judge_dedup@chatroom"
        _one = "你好呀，今天天气不错"

        # ① 消息库说"这条已经发过" ⇒ 拦住，一个字节都不发
        _w1 = _StubW(True)
        _r1 = _S.SendQueue(_w1, _StubStore()).send_text_batch(_ck, [_one])
        chk("I1 ★（库里已有）拦住不发，且如实记进 deduped",
            _r1.get("sent") == [] and _r1.get("deduped") == [_one] and _w1.calls == [],
            "sent=%s deduped=%s 真发=%d" % (_r1.get("sent"), _r1.get("deduped"), len(_w1.calls)))

        # ② 库里说"没发过"，但**本批里**给了两条一样的 ⇒ 只发一条
        _w2 = _StubW(False)
        _r2 = _S.SendQueue(_w2, _StubStore()).send_text_batch(_ck, ["哈哈哈", "哈哈哈"])
        chk("I2 ★（本批内重复）两条一样的只发一条",
            len(_r2.get("sent") or []) == 1,
            "实发 %s 条" % len(_r2.get("sent") or []))

        # ③ 反向锚：库里说"读不出来"（None）⇒ **必须照常发**，
        #    不许因为读不到库就把整批发不出去（那是"为了防止重复发，结果什么都发不出"）
        _w3 = _StubW(None)
        _r3 = _S.SendQueue(_w3, _StubStore()).send_text_batch(_ck, ["这条该发出去"])
        chk("I3 ★反向锚（库里读不出来）⇒ 照常发，不拦",
            len(_r3.get("sent") or []) == 1,
            "实发 %s 条（读不到库就把整批吞了 = 把防重复做成了不发送）"
            % len(_r3.get("sent") or []))
    except Exception as _eI:  # noqa: BLE001
        chk("I. 真跑（前置）", False, str(_eI)[:100])

    print()
    print("== 汇总：%d 通过 / %d 失败 ==" % (len(OKS), len(FAILS)))
    for f in FAILS:
        print("   FAIL:", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
