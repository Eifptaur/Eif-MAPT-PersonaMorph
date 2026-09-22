"""兼容性红线 + 原因码铺开的判据（2026-09-22 立，来自业界调研的两条硬结论）。

守两件事：
A. **跨进程窗口亲缘是红线**：微软官方明文——跨进程 `SetParent` 会把**对方进程**（微信）的
   DPI 感知模式重置、跨进程 `CreateWindow` 会重置调用者。我们**从不做**这件事，但今天没做不代表
   明天不做 ⇒ 用静态判据把它钉住（命中即红）。
B. **原因码要铺到"拒发/被丢"的账本上**（不只是工具层兜一层）：`message_ledger` 与
   `send_retry` 是"这条为什么被留/被丢/排进队列"的两本权威账，必须带 `code` 字段，
   否则统计与判据永远只能对中文散文做子串匹配。
"""

import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _srcmatch as _sm          # noqa: E402

from agent import reason_codes as RC   # noqa: E402

PASS, FAIL = [0], [0]


def ok(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("  [%s]" % detail if detail else ""))
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
    return bool(cond)


def _src(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


def main():
    print("== A. 红线：不许跨进程动窗口亲缘（DPI 感知会被重置）==")
    _wx = _src(os.path.join("agent", "wechat.py"))
    _ib = _src(os.path.join("agent", "input_backend.py"))
    _ua = _src(os.path.join("agent", "ui_adapt.py"))
    _tray = _src(os.path.join("agent", "tray.py"))
    _bad = [n for n, s in (("wechat.py", _wx), ("input_backend.py", _ib),
                           ("ui_adapt.py", _ua), ("tray.py", _tray)) if "SetParent(" in s]
    ok("产品代码里没有 SetParent（跨进程挂亲缘＝重置对方 DPI 感知）", not _bad, "、".join(_bad))
    ok("tray.py 那个 CreateWindowExW 是**同进程**的消息窗（父窗口传 0、用 HWND_MESSAGE）",
       _sm.has(_tray, "HWND_MESSAGE") and _sm.has(_tray, "CreateWindowExW"))
    ok("这条红线的理由写进了代码（不然下一个人会顺手加回去）",
       _sm.has(_wx, "DPI") or _sm.has(_ua, "DPI"))

    print("== B. 原因码铺在两本账上 ==")
    ok("message_ledger 每条带 code（_rc_classify 唯一入口、出错给 unknown 不抛）",
       _sm.has(_wx, "\"code\": _rc_classify(why)") and _sm.has(_wx, "def _rc_classify("))
    _sr = _src(os.path.join("agent", "send_retry.py"))
    ok("send_retry 入队项带 code", _sm.has(_sr, "\"code\": _code"))
    ok("send_retry 去重更新时也刷新 code", _sm.has(_sr, "it[\"code\"] = _rc.classify"))

    print("== C. 账本语义的码（别把「判为自己」并进「身份未确认」）==")
    for text, want in (("判为自己：self_wxid 命中", "self_detected"),
                       ("判为自己：自家行号命中（我们自己发出去并回读确认过的库行）", "self_detected"),
                       ("判为自己：文本回声窗命中", "self_detected"),
                       ("保留（当别人的消息处理）", "kept"),
                       ("跳过（系统/空内容/其它过滤）", "filtered")):
        got = RC.classify(text)
        ok("账本原文分对：%s" % text[:16], got == want, "%s（期望 %s）" % (got, want))
    ok("self_detected 的码表说明写清了「只落库、不喂模型、不回拍」",
       _sm.has(RC.CODES["self_detected"], "不回拍"))

    print("== D. 行为：真的写进队列与账本 ==")
    from agent import send_retry as SR
    _tmp = tempfile.mkdtemp(prefix="pm-rc-")
    _real_path = SR.PATH

    def _reset_cache():
        """`_cache` 初始是 None（要 `_load()` 之后才变列表）⇒ 先触发一次再清，别硬赋。"""
        try:
            SR._load()
        except Exception:
            pass
        if isinstance(getattr(SR, "_cache", None), list):
            SR._cache[:] = []

    try:
        SR.PATH = os.path.join(_tmp, "send_retry.json")
        _reset_cache()
        r1 = SR.enqueue("group:x", "你好", why="没认准就是目标会话（宁可漏发，绝不发错）", now=1000.0)
        items = SR._load()
        ok("入队条目带 identity_unconfirmed 码",
           bool(items) and items[-1].get("code") == "identity_unconfirmed",
           str(items[-1].get("code")) if items else "（队列空）")
        r2 = SR.enqueue("group:x", "你好", why="抓不到微信画面（PrintWindow 失败）⇒ 量不到", now=1001.0)
        items2 = SR._load()
        ok("去重路径也把码刷新成 no_capture（不留下旧的错码）",
           bool(r2.get("deduped")) and items2[-1].get("code") == "no_capture",
           str(items2[-1].get("code")) if items2 else "（队列空）")
        ok("队列落盘文件确实存在（不是只在内存里）", os.path.exists(SR.PATH))
    finally:
        SR.PATH = _real_path
        _reset_cache()

    print("== 兼容性红线判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
    return 0 if FAIL[0] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
