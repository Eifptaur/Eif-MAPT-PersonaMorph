# -*- coding: utf-8 -*-
"""「学会停手」判据（A 轮）。

守四件事（都照抄业界成熟口径，见 `agent/backoff.py` 文件头）：
  ① **三态**：closed → open（冷却期内一次都不许动手）→ half-open（只放一发）→ …
  ② **重试有界**：退避逐步加长、有上限、带抖动；**绝不允许无界重试**；
  ③ **借用前台有配额**：一分钟上限 + 两次之间最小间隔（「一直顶界面、闪来闪去」的直接解药）；
  ④ **行为锚**：停手期间切会话**一次都不许调用实现**（不是「调了再判断」）。

用法：`py -3 scripts/backoff_selftest.py`
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _srcslice # noqa: E402  AST 定位函数体（判据不许拿 `def` 行当文本边界）
from agent import backoff as B # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


def src(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


print("── A. 熔断三态（时钟注入，不靠 sleep）──")
_t = [0.0]
bk = B.Breaker("夹具", threshold=3, cooldown_s=90, base_s=8, max_s=120, clock=lambda: _t[0])
ok("A1 初始是合上的（可以动手）", bk.can_try()[0] is True)
_d1 = bk.fail("第一次")
ok("A2 第一次失败给退避（指数基数附近，且**不是 0**——0 就是「立刻再来」）",
   1.0 < _d1 < 20.0, "%.1fs" % _d1)
_d2 = bk.fail("第二次")
ok("A3 退避**逐步加长**（指数；抖动 ±25% 内）", _d2 > _d1 * 0.5, "%.1f → %.1f" % (_d1, _d2))
_ok, _why = bk.can_try()
ok("A4 还没到阈值 ⇒ 仍允许（但已退避）", _ok is True and bk.open is False, "")
_d3 = bk.fail("第三次")
ok("A5 到阈值 ⇒ **打开**，退避＝冷却时长", bk.open is True and abs(_d3 - 90.0) < 1e-6, "%.0fs" % _d3)
_ok, _why = bk.can_try()
ok("A6 ★ 打开期间**一次都不许**动手（这就是「停手」）", _ok is False and "停手" in _why, _why[:60])
_t[0] = 45.0
ok("A7 冷却中（45s < 90s）仍不许", bk.can_try()[0] is False)
_t[0] = 91.0
ok("A8 冷却到点 ⇒ **半开**放一发", bk.can_try()[0] is True)
ok("A9 半开那一发已放 ⇒ 再问就不许了（不许连着试探）", bk.can_try()[0] is False)
bk.fail("试探也没成")
ok("A10 试探失败 ⇒ 重新冷却（还是停手）", bk.can_try()[0] is False and bk.open is True)
_t[0] = 200.0
ok("A11 再等一个冷却 ⇒ 又放一发", bk.can_try()[0] is True)
bk.ok()
ok("A12 成功 ⇒ 合上、清零（计数不再累计）", bk.open is False and bk.fails == 0 and bk.can_try()[0] is True)
bk.reset()
ok("A13 `reset()`（新消息/用户手动处理）⇒ 立刻恢复", bk.can_try()[0] is True and bk.fails == 0)

print("\n── B. 借用前台的配额 ──")
_u = [0.0]
tb = B.TokenBucket(per_minute=2, min_gap_s=10, clock=lambda: _u[0])
ok("B1 第一次给", tb.take()[0] is True)
ok("B2 紧接着再要 ⇒ 拒（最小间隔 10s）", tb.take()[0] is False)
ok("B3 拒的原因写清了间隔", "最少隔" in tb.take()[1] or "最少隔" in tb.status()["last_why"],
   tb.status()["last_why"])
_u[0] = 20.0
ok("B4 过了最小间隔 ⇒ 给（这一分钟第 2 次）", tb.take()[0] is True)
_u[0] = 40.0
_ok4, _why4 = tb.take()
ok("B5 ★ 这一分钟额度用满 ⇒ 拒（「闪来闪去」的直接解药）", _ok4 is False and "上限" in _why4, _why4)
_u[0] = 90.0
ok("B6 滚出窗口 ⇒ 额度恢复", tb.take()[0] is True)
ok("B7 拒绝次数有记账（能汇报给用户）", tb.status()["refused"] >= 2, str(tb.status()["refused"]))

print("\n── C. 配置与默认值（缺键必须落到保守侧）──")
_bk_src = src("agent/backoff.py")
_cfg_src = src("agent/config.py")
ok("C1 参数集中在一处（`DEF`），没有散落的魔法数", "DEF = {" in _bk_src)
ok("C2 配置里六个键都在（用户可调）",
   all(('"%s"' % k) in _cfg_src for k in B.DEF), str(list(B.DEF)))
ok("C3 默认阈值是「少」的那一侧（≤3 次）", int(B.DEF["stop_after_failures"]) <= 3,
   str(B.DEF["stop_after_failures"]))
ok("C4 默认「两次借用之间」至少隔 5 秒以上（防闪）", float(B.DEF["foreground_min_gap_s"]) >= 5,
   str(B.DEF["foreground_min_gap_s"]))
ok("C5 退避有上限（不许无界增长）", float(B.DEF["backoff_max_s"]) <= 600, str(B.DEF["backoff_max_s"]))

print("\n── D. 接线（源码级：三条出口都真的接上了，不是写了没人调）──")
_ua = src("agent/ui_adapt.py")
_wx = src("agent/wechat.py")
ok("D1 借用前台的唯一咽喉点 `_guarded` 里取了配额",
   "_bo.foreground().take()" in _ua and "置前配额用尽" in _ua)
ok("D2 配额件不可用时**留痕并放行**（不静默吞）", "置前配额件不可用" in _ua)
ok("D3 切会话公开入口先过停手闸",
   "backoff as _bo" in _wx and "_bo.breaker(\"switch_chat\").can_try()" in _wx)
ok("D4 切会话结果记账（成功 ok / 失败 fail）",
   '_bo.breaker("switch_chat").ok()' in _wx and '_bo.breaker("switch_chat").fail(' in _wx)
ok("D5 记账只在**这一层**（实现里不再重复记：`note_switch_fail` 不碰 backoff）",
   _wx.count('breaker("switch_chat").fail(') == 2      # 异常路径 + 正常失败路径，两条互斥
   and "backoff" not in _srcslice.func_src(_wx, "note_switch_fail"),
   str(_wx.count('breaker("switch_chat").fail(')))

print("\n── E. 行为锚：停手期间**一次都不许调用实现** ──")
from agent.wechat import WeChatAdapter as _WA # noqa: E402
# ⛔ 公开方法外面还套着"身份事务"装饰器（它要 adapter 的一堆内部方法）⇒ 取**未包装**的那个
#    （`functools.wraps` 留了 `__wrapped__`）来测停手闸本身；夹具只需提供 `_switch_chat_posted_impl`。
_GATE = getattr(_WA.switch_chat_posted, "__wrapped__", _WA.switch_chat_posted)


class _Fake:
    """最小替身：只数「实现被调了几次」。"""

    def __init__(self):
        self.calls = 0

    def _switch_chat_posted_impl(self, chat_id, gui=None, name=None, confirm_s=8.0):
        self.calls += 1
        return False, "夹具：这一枪故意失败"


_f = _Fake()
B.rebind()


def _try(_chat="wxid_fixture_chat"):
    return _GATE(_f, _chat)


for _i in range(int(B.DEF["stop_after_failures"])):
    _r = _try()
ok("E1 前 N 次真的调了实现（闸门没把正常路堵死）", _f.calls == int(B.DEF["stop_after_failures"]),
   "calls=%d" % _f.calls)
ok("E2 到阈值后判定为「停手」", B.breaker("switch_chat").open is True)
_before = _f.calls
_r2 = _try()
ok("E3 ★ 停手期间**实现一次都没被调用**（不是调完再判断）", _f.calls == _before, "calls=%d" % _f.calls)
ok("E4 返回给用户的是人话（含「停手」与下一步）",
   (isinstance(_r2, tuple) and "停手" in str(_r2[1]) and "重置停手状态" in str(_r2[1])),
   str(_r2[1])[:80])
B.rebind()
_r3 = _try()
ok("E5 `rebind()`（等价于控制台「重置停手状态」）⇒ 恢复尝试", _f.calls == _before + 1, "calls=%d" % _f.calls)
B.rebind()

print("\n── F. 反向锚：它**只让动作更少**，不越任何安全闸门 ──")
# ⛔ 只看**代码**（文档串里会提到 fg_allowed 作为边界说明，那是注释不是调用）
_bk_code = "\n".join(l for l in _bk_src.splitlines() if not l.strip().startswith("#"))
_bk_code = _bk_code.split('"""', 2)[2] if _bk_code.count('"""') >= 2 else _bk_code
ok("F1 `backoff` 不碰前台闸门（代码里既没实现也没调用 `fg_allowed` / SetForegroundWindow）",
   "fg_allowed(" not in _bk_code and "SetForegroundWindow" not in _bk_code, "")
ok("F2 它不记用户内容（只有原因码/说明文本：没有 user_id / 没有正文键）",
   ("user_id" not in _bk_src) and ('"text":' not in _bk_src) and ("send_text" not in _bk_src), "")
ok("F3 文件头写明「只会让动作更少」这条边界", "只会让动作**更少**" in _bk_src)

print("")
print("学会停手判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
