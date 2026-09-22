"""失败原因码（`agent/reason_codes.py` + `tools.execute_tool` 接线）的判据。

为什么它值得一条判据（2026-09-22 立）：
  原因码的全部价值是"**稳定、可统计、判据钉得住**"。它一旦漂了（分类错、码表漏、原文被改），
  后面所有基于它的统计与判据都会静默失真 —— 所以三条必须机械守着：
    ① 码表齐、无重复、每个码都有人话；
    ② 分类用**我们真实回执里的原文**当用例（不是我自己编的句子）；
    ③ 接线是**附加**字段：原文一个字不改、成功结果不许被加码。
"""

import io
import os
import sys

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


def main():
    print("== A. 码表 ==")
    ok("码表非空且每个码都有人话", bool(RC.CODES) and all(str(v).strip() for v in RC.CODES.values()),
       "%d 个码" % len(RC.CODES))
    ok("没有重复码（字典键天然唯一，这里查空键/空白键）",
       all(str(k).strip() for k in RC.CODES), str([k for k in RC.CODES if not str(k).strip()]))
    ok("有 unknown 兜底码（分不出来不许硬猜）", "unknown" in RC.CODES)
    ok("label() 对未知码原样返回、不编", RC.label("no-such-code") == "no-such-code"
       and RC.label("no_capture") == RC.CODES["no_capture"])

    print("== B. 分类：用**真实回执原文**当用例 ==")
    cases = [
        ("抓不到微信画面（PrintWindow 失败）⇒ 量不到", "no_capture"),
        ("微信主窗口不可见（恢复失败）", "no_capture"),
        ("帧整幅近乎全黑", "no_capture"),
        ("会话没对上，这条不发", "identity_unconfirmed"),
        ("没认准就是目标会话（宁可漏发，绝不发错）", "identity_unconfirmed"),
        ("机器人已暂停 ⇒ 这条不发", "halted"),
        ("机器人已停止", "halted"),
        ("用户在忙（全屏）⇒ 等空档", "busy"),
        ("数据库合并失败(文件被微信并发改写)", "db_unreadable"),
        ("读 contact.db 失败：database is locked", "db_unreadable"),
        ("没填 API key", "key_missing"),
        ("等控制台就绪超时", "timeout"),
        ("找不到那条消息", "not_found"),
        ("工具 send_message 的参数不是合法 JSON", "bad_args"),
        ("未知工具 send_messag", "bad_args"),
        ("锁屏 / 安全桌面下注入做不了", "no_interactive_desktop"),
        ("权限不足：拒绝访问", "access_denied"),
        ("", "unknown"),
        ("一切都好", "unknown"),
    ]
    _bad = []
    for text, want in cases:
        got = RC.classify(text)
        if got != want:
            _bad.append("%r→%s(期望 %s)" % (text[:24], got, want))
    ok("真实回执原文都能分对（%d 例）" % len(cases), not _bad, "；".join(_bad)[:200])
    ok("空串给 unknown（不猜）", RC.classify("") == "unknown" and RC.classify(None) == "unknown")

    print("== C. 附加语义（原文一字不改）==")
    _t = "会话没对上，这条不发"
    ok("tag() 只在后面追加码，原文完整保留", RC.tag(_t).startswith(_t) and RC.classify(_t) in RC.tag(_t))
    ok("tag() 对空串返回空串（不造出半个括号）", RC.tag("") == "")

    print("== D. 接线：tools 的唯一分发点 ==")
    _tools = io.open(os.path.join(ROOT, "agent", "tools.py"), encoding="utf-8").read()
    ok("execute_tool 里对 is_error 结果 setdefault 了 code",
       _sm.has(_tools, "res.setdefault(\"code\"") or _sm.has(_tools, "setdefault('code'"))
    ok("接线是**附加**：没有拿码去改 content / 决定成败",
       _sm.has(_tools, "reason_codes as _rc") and not _sm.has(_tools, "content = _rc."))
    _rc_src = io.open(os.path.join(ROOT, "agent", "reason_codes.py"), encoding="utf-8").read()
    ok("纪律写进了代码注释：码不进群、不替换原文、不当放行判据",
       _sm.has(_rc_src, "绝不进群里") and _sm.has(_rc_src, "绝不替换原文"))

    print("== 失败原因码判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
    return 0 if FAIL[0] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
