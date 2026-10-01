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
    print("== 汇总：%d 通过 / %d 失败 ==" % (len(OKS), len(FAILS)))
    for f in FAILS:
        print("   FAIL:", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
