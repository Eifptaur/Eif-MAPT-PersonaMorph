"""失败原因码（`agent/reason_codes.py` + `tools.execute_tool` 接线）的判据。

为什么它值得一条判据：
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

import _srcmatch as _sm # noqa: E402

from agent import reason_codes as RC # noqa: E402

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
        # ── 下面这些是补的：原来语料只盖住 11 个码，剩下几个码"文案改了也没人知道" ──
        ("发送频率超限（每分钟最多 20 条），请等一会再发", "rate_limited"),
        ("HTTP 401 Unauthorized", "auth_rejected"),
        ("403 Forbidden", "auth_rejected"),
        ("判为自己：self_wxid 命中", "self_detected"),
        ("保留（当别人的消息处理）", "kept"),
        ("跳过（系统提示）", "filtered"),
        # ⭐ 真鼠标档**闸门不放开** ⇒ 这一枪没发（这几条是生产代码里的真实原文）
        ("投递点击没成（判据模拟：投递没成）⇒ **不发这一枪**：不动你的鼠标是硬口径，"
         "而 `input.allow_real_fallback` 没开", "foreground_not_target"),
        ("按最高目标**不退回真鼠标**（这条发送路径会动你的光标）；要允许请打开 input.allow_real_fallback",
         "foreground_not_target"),
        # ⛔ 反向锚（这一条原来**假绿**）：文案里带"落点也是猜的"这种补充说明，
        #   加规则之前它被 `target_moved` 抢走（"落点变了"）—— 其实什么都没变，是闸门没放开真鼠标。
        #   ⇒ 规则顺序必须让 `foreground_not_target` 排在 `target_moved` 前面。
        ("引用已插入，但投递发送没成（投递没成）⇒ 按最高目标不退回真鼠标（库那条会动光标，落点也是猜的）",
         "foreground_not_target"),
        #: ⚠️ 已知取舍（钉住它，别让"顺手调顺序"悄悄改掉）：这一条原文同时讲了"认不准会话"与
        #   "闸门不放开真鼠标"，最终归后者（顺序原因见 `reason_codes._RULES` 的注释）；
        #   "认不准会话"那类统计靠下面那条（不含闸门措辞）继续覆盖。
        ("投递档确认不了目标会话（会话头三态=错一字，投递切会话也没成）⇒ 按最高目标**不退回真鼠标**",
         "foreground_not_target"),
        #: 反向锚：同样讲"确认不了目标会话"，但**不含闸门措辞** ⇒ 必须仍归 `identity_unconfirmed`
        ("投递档确认不了目标会话（会话头三态=错一字）⇒ 这条不发", "identity_unconfirmed"),
        # 另一个闸（落点守卫拒绝：光标没到位）**没有专属码** ⇒ 按"分不出来绝不猜"落 `unknown`，
        # 不许硬塞进相邻的码里（把它算成"落点变了"会让那一类的统计继续被污染）。
        ("真鼠标档守卫拒绝：SetCursorPos(300,500) 返回 0，光标没到位（多半是你正在用鼠标）", "unknown"),
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

    print("== B2. 每个码都必须**有可能被产出来** ==")
    #    码表里声明了、`_RULES` 里却没有任何键指向它 ⇒ 这个码**永远是空的**，
    #    而码表里躺着它会让读的人以为"这一类已经在统计里"。
    #    实测踩到：`foreground_not_target` 就这么空了很久（真鼠标闸拒发的原文一律落 unknown，
    #    还有一条因为带"落点"被误记成 `target_moved`）。
    #    两个例外都是**故意**的：`unknown` 是兜底；`manifest_bad` 由调用方按**字面量**声明
    #    （`_rc.label("manifest_bad")`），不靠散文分类。
    _LITERAL_ONLY = {"manifest_bad", "unknown"}
    _rule_codes = {c for c, _ in RC._RULES}
    _dead_codes = sorted(set(RC.CODES) - _rule_codes - _LITERAL_ONLY)
    ok("没有「有码无规则」的码（声明了却永远产不出的码 = 假统计）",
       not _dead_codes, "空码：%s" % _dead_codes)
    ok("反向控制：判据能认出「有码无规则」（编一个只进码表不进规则的码必须被抓到）",
       bool({"__编的码__"} - _rule_codes - _LITERAL_ONLY), "")

    print("== C. 附加语义（原文一字不改）==")
    _t = "会话没对上，这条不发"
    ok("tag() 只在后面追加码，原文完整保留", RC.tag(_t).startswith(_t) and RC.classify(_t) in RC.tag(_t))
    ok("tag() 对空串返回空串（不造出半个括号）", RC.tag("") == "")

    print("== D. 接线：**全部生产接线点**（不只是 tools）==")
    #    原来这一组只盯 `tools.py` 一个点，而生产侧真调它的有 **6 处**
    #    （重试队列 ×2 / 工具分发 / 自定义工具清单 / 版本门记账 / 微信台账）——
    #    剩下 5 处没有任何机械约束，"顺手拿码去做判断"没人拦。
    _WIRING = (
        ("agent/send_retry.py", 2, "重试队列（入队 + 去重更新各一处）"),
        ("agent/tools.py", 1, "工具分发（is_error 结果附加码）"),
        ("agent/user_tools.py", 1, "自定义工具清单（按字面量声明）"),
        ("agent/version_gate.py", 1, "版本门记账"),
        ("agent/wechat.py", 1, "消息台账（`_rc_classify` 唯一入口）"),
    )
    _miss = []
    for _rel, _want, _what in _WIRING:
        _txt = io.open(os.path.join(ROOT, _rel), encoding="utf-8").read()
        _n = _txt.count("reason_codes as _rc") + _txt.count("from . import reason_codes")
        if _n < _want:
            _miss.append("%s 期望≥%d 实得 %d（%s）" % (_rel, _want, _n, _what))
    ok("6 个生产接线点都在（逐个点名，不止 tools）", not _miss, "；".join(_miss)[:200])
    _tools = io.open(os.path.join(ROOT, "agent", "tools.py"), encoding="utf-8").read()
    ok("execute_tool 里对 is_error 结果 setdefault 了 code",
       _sm.has(_tools, "res.setdefault(\"code\"") or _sm.has(_tools, "setdefault('code'"))
    ok("接线是**附加**：没有拿码去改 content / 决定成败",
       _sm.has(_tools, "reason_codes as _rc") and not _sm.has(_tools, "content = _rc."))
    _rc_src = io.open(os.path.join(ROOT, "agent", "reason_codes.py"), encoding="utf-8").read()
    ok("纪律写进了代码注释：码不进群、不替换原文、不当放行判据",
       _sm.has(_rc_src, "绝不进群里") and _sm.has(_rc_src, "绝不替换原文"))

    print("== E. 码**不许驱动决定**（机械判据，别只靠注释约定）==")
    #    这条纪律原来只在注释里写着。两条 AST 判据（不数文本）：
    #      ① 这些调用**不许出现在条件位**（`if`/`while`/三元/`assert` 的判断里）；
    #      ② 结果只许落进"名字或键含 `code`"的地方，或喂给 `label()`/`tag()`/`str()`。
    import ast as _ast

    _CALLS = ("classify", "label", "tag")

    def _lit(node):
        try:
            return _ast.literal_eval(node)
        except Exception:
            return ""

    def _target_name(node):
        if isinstance(node, _ast.Name):
            return node.id
        if isinstance(node, _ast.Attribute):
            return node.attr
        if isinstance(node, _ast.Subscript):
            return str(_lit(node.slice))
        return ""

    def scan(path):
        """→ (条件位里的调用, 结果去向不明的调用)"""
        txt = io.open(path, encoding="utf-8").read()
        try:
            tree = _ast.parse(txt)
        except Exception:
            return [], []
        al = set()
        for n in _ast.walk(tree):
            if isinstance(n, _ast.ImportFrom):
                for a in n.names:
                    if a.name == "reason_codes":
                        al.add(a.asname or a.name)
            elif isinstance(n, _ast.Import):
                for a in n.names:
                    if a.name.endswith("reason_codes"):
                        al.add(a.asname or a.name.split(".")[-1])
        par = {}
        for n in _ast.walk(tree):
            for c in _ast.iter_child_nodes(n):
                par[c] = n

        def is_call(x):
            return (isinstance(x, _ast.Call) and isinstance(x.func, _ast.Attribute)
                    and x.func.attr in _CALLS and isinstance(x.func.value, _ast.Name)
                    and x.func.value.id in al)

        cond = []
        _cond_ids = set()
        for n in _ast.walk(tree):
            if not isinstance(n, (_ast.If, _ast.While, _ast.IfExp, _ast.Assert)):
                continue
            test = getattr(n, "test", None)
            if test is None:
                continue
            for x in _ast.walk(test):
                if is_call(x):
                    cond.append("%s:%d %s()" % (os.path.basename(path), x.lineno, x.func.attr))
                    _cond_ids.add(id(x))
        #: 每层函数名（判"转发型包装"用：`def _rc_classify(...): return _rc.classify(...)`
        #   —— 它的结果由**调用方**放进含 code 的字段，这层是合规的）
        _fnof = {}
        for n in _ast.walk(tree):
            if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                for x in _ast.walk(n):
                    _fnof[x] = n.name
        lost = []
        for x in [n for n in _ast.walk(tree) if is_call(n)]:
            cur, dest_ok = x, False
            for _ in range(3):
                cur = par.get(cur)
                if cur is None:
                    break
                if isinstance(cur, _ast.Assign):
                    if any("code" in _target_name(tg) for tg in cur.targets):
                        dest_ok = True
                elif isinstance(cur, _ast.Return):
                    # 转发型包装：函数名带 `code` 或 `classify`（如 `_rc_classify`）⇒
                    # 交给调用方落进含 code 的字段，这一层是合规的
                    _fn = str(_fnof.get(cur, ""))
                    if "code" in _fn or "classify" in _fn:
                        dest_ok = True
                elif isinstance(cur, _ast.Dict):
                    if any("code" in str(_lit(k)) for k in cur.keys):
                        dest_ok = True
                elif isinstance(cur, _ast.keyword):
                    if "code" in str(cur.arg):
                        dest_ok = True
                elif isinstance(cur, _ast.Call):
                    f = cur.func
                    nm = f.id if isinstance(f, _ast.Name) else (f.attr if isinstance(f, _ast.Attribute) else "")
                    if nm in ("str", "label", "tag", "get", "setdefault"):
                        dest_ok = True
            #: 已经在"条件位"报过的那条不再重复报（那是另一条判据的活）
            if not dest_ok and id(x) not in _cond_ids:
                lost.append("%s:%d %s()" % (os.path.basename(path), x.lineno, x.func.attr))
        return cond, lost

    #: 只管**生产代码**（`agent/`）：判据脚本里拿码做断言是应该的，不算"驱动决定"。
    _agent_dir = os.path.join(ROOT, "agent")
    _prod = [os.path.join(_agent_dir, f) for f in sorted(os.listdir(_agent_dir)) if f.endswith(".py")]
    _cond_all, _lost_all = [], []
    for _p in _prod:
        _c, _l = scan(_p)
        _cond_all += _c
        _lost_all += _l
    ok("码的调用**一条都没出现在条件位**（拿码做 if/while/三元/assert 判断 = 码在驱动决定）",
       not _cond_all, "；".join(_cond_all[:4]))
    ok("码的结果都落进「含 code 的名字/键」或喂给 label/tag/str/取值（不许拿去当别的用途）",
       not _lost_all, "；".join(_lost_all[:4]))
    #: 反向控制：三条探针各只犯一个错 —— 扫描器必须**都认出来**
    #    （夹具用 `tempfile`，**不落产品目录**：判据写脏 `logs/`/`data/` 会被跑分器的洁净度总闸判红）
    import tempfile as _tf

    _PROBE = ("from . import reason_codes as _rc\n"
              "code = _rc.classify(a)\n"            # 合规：落进含 code 的名字
              "if _rc.classify(b) == 'halted':\n"   # 条件位 ⇒ 该报 cond
              "    pass\n"
              "zz = _rc.classify(c)\n")             # 去向不明 ⇒ 该报 lost
    _pd = _tf.mkdtemp(prefix="rc-probe-")
    _pp = os.path.join(_pd, "probe.py")
    try:
        io.open(_pp, "w", encoding="utf-8").write(_PROBE)
        _pc, _pl = scan(_pp)
    finally:
        try:
            os.remove(_pp)
            os.rmdir(_pd)
        except OSError:
            pass  # 清理型：静默合法
    ok("扫描器有效：条件位报 1 条、去向不明报 1 条、合规那条不报"
       "（否则「没扫到」＝「扫不到」，两条闸门都会假绿）",
       len(_pc) == 1 and len(_pl) == 1
       and _pc[0].startswith("probe.py:3 ") and _pl[0].startswith("probe.py:5 "),
       "cond=%s lost=%s" % (_pc, _pl))

    print("== 失败原因码判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
    return 0 if FAIL[0] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
