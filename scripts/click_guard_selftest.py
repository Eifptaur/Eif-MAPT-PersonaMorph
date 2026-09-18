#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「点偏 → 莫名打开群成员栏」防护判据（2026-09-18 用户反馈后立）。

**报障原话**：「up你那个机器人好像有点问题，发消息的时候总是莫名其妙打开群成员栏，隔了半分钟才发的出去。
……先点搜索栏，停顿十几秒，再停顿十几秒，然后再发消息。这一来一去，一条消息要发一分钟更多」

**查到的事实**：投递**不会切会话**，一切靠"当前打开的就是目标会话"的屏幕证据；某一枪落点偏了
（点到会话头那一下就会弹出群信息/群成员栏）时，**旧代码照旧往下走** ⇒ 越走越乱、还卡在那里。

**修法（本次保留的）**：把会话/发送链里的点击收敛到一个咽喉点 `wechat.WeChatAdapter._click_posted`：
  ① 点前记一次微信顶层窗快照；② 点后比一次；③ 多出**没预期**的窗 ⇒ 记下是哪一枪（tag + 落点）、
  存现场照片、用安全关窗把它关掉、返回失败让整条链**立刻停手**。
  ④ **tooltip 级小窗（两条边都 <200px）不算** —— 悬停提示气泡也是 Qt 顶层窗，算进去会误拦正常发送。
  ⑤ 预期会弹窗的那几枪显式写 `allow_new=True`（搜索浮层 / 表情面板 / 会话行双击被独立出去的窗）。

**本次撤掉的（同一条反馈里的"慢"，做了活体 A/B 后自己否掉）**：
  `_scratch/_live_ab_speed.py` 在同一台机器、同一个会话连续真发两次：
    · A 组（链内复用会话证据）：3.2s，但**判失败**（会话头三态 no_ref ⇒ 拒发）
    · B 组（把复用关掉＝改动前行为）：14.3s，**发送成功**
  ⇒ 复用改变了链里的取值顺序，把「本来能发出去的消息」变成发不出去（用户红线：能发的必须发得出）。
  ⇒ **这条路不采用**；慢的问题改在下一轮用更保守的办法（每条链只问一次、把证据由调用方传下去），
    并**必须再做一次同样的活体 A/B**。本判据最后一段就是防"把这类加速器偷偷加回来"。
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from agent import wechat as W                  # noqa: E402

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   " + name)
    else:
        FAIL += 1
        print("  FAIL " + name + ("   [%s]" % extra if extra else ""))


class _Ad(object):
    """只借 `WeChatAdapter` 上那三件纯逻辑（不构造真适配器：那会去连微信）。"""

    _click_posted = W.WeChatAdapter._click_posted

    def __init__(self, windows=None):
        self._windows = windows if windows is not None else {}
        self.closed = []
        self.dumps = []

    def _wx_toplevel_windows(self):
        return dict(self._windows)

    def _close_stray_window(self, hwnd, why=""):
        """判据里**绝不真去关窗**（那是产品动作）：只记账。"""
        self.closed.append((int(hwnd), str(why)))
        return True

    def _dump_fail_shot(self, tag, img, extra=None, keep=5):
        self.dumps.append((tag, extra))
        return "假现场"

    def _get_gui(self):
        return None


class _FakeBackend(object):
    def __init__(self, ad, after=None, result=True):
        self.calls = []
        self._ad = ad
        self._after = after
        self._result = result

    def click(self, hwnd, pt, right=False, **kw):
        self.calls.append((hwnd, tuple(pt), right))
        if self._after is not None:
            self._ad._windows = dict(self._after)
        return (self._result, "" if self._result else "假后端没打出去")


print("── A. 点后冒出不该有的窗 ⇒ 记下是哪一枪 + 关掉它 + 停手 + 留现场 ──")
ad = _Ad({1: "主窗"})
fb = _FakeBackend(ad, after={1: "主窗", 2: "群聊信息"})
okc, why = ad._click_posted(fb, 1, (500, 200), "聚焦输入栏")
ok("判失败（链会停手）", okc is False, str(okc))
ok("说明里点名是哪一枪 + 落点 + 冒出来的窗",
   "聚焦输入栏" in why and "(500, 200)" in why and "群聊信息" in why, why[:110])
ok("已经把那个窗关掉（判据里只记账，不真关）", ad.closed and ad.closed[0][0] == 2, str(ad.closed))
ok("留了现场（tag + 落点 + 新窗清单）",
   bool(ad.dumps) and ad.dumps[0][1].get("新的窗") and ad.dumps[0][1].get("落点") == [500, 200],
   str(ad.dumps[:1])[:120])
ok("点击本身仍然打出去了（只记不吞）", fb.calls == [(1, (500, 200), False)], str(fb.calls))

print("── B. 没有新窗 ⇒ 正常放行（不许误拦）──")
ad2 = _Ad({1: "主窗"})
fb2 = _FakeBackend(ad2, after={1: "主窗"})
okc2, why2 = ad2._click_posted(fb2, 1, (500, 200), "聚焦输入栏")
ok("返回成功", okc2 is True, str(why2))
ok("没出事就不许刷现场照片", ad2.dumps == [])
ok("也不许关任何窗", ad2.closed == [])

print("── C. 预期会弹窗的那几枪：显式 allow_new ⇒ 不判偏 ──")
ad3 = _Ad({1: "主窗"})
fb3 = _FakeBackend(ad3, after={1: "主窗", 2: "搜索浮层"})
okc3, _w3 = ad3._click_posted(fb3, 1, (236, 84), "搜索入口", allow_new=True)
ok("搜索入口点完弹浮层 ⇒ 照常放行", okc3 is True)
ok("也没留现场", ad3.dumps == [])

ad4 = _Ad({1: "主窗"})
fb4 = _FakeBackend(ad4, after={1: "主窗", 3: "被拖出去的聊天"})
okc4, _w4 = ad4._click_posted(fb4, 1, (200, 300), "会话行（切会话）", allow_new=True)
ok("会话行那枪允许多出窗（双击被独立出去的窗另有收回处置）", okc4 is True)

print("── D. 后端点击失败要如实带出来（不许静默）──")
ad5 = _Ad({1: "主窗"})
fb5 = _FakeBackend(ad5, after={1: "主窗"}, result=False)
okc5, why5 = ad5._click_posted(fb5, 1, (10, 10), "点「发送」")
ok("失败就返回 False 并带原因", okc5 is False, str(why5))
ok("没有新窗 ⇒ 不给『点偏』这种误判", ad5.dumps == [] and ad5.closed == [])

print("── E. tooltip 级小窗不算（源码级：两条边都 <200px 直接跳过）──")
_SRC = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
_seg = _SRC[_SRC.index("def _wx_toplevel_windows("):]
_seg = _seg[:_seg.index("\n    def ", 10)]
ok("小窗过滤在", "< 200 and (int(y1) - int(y0)) < 200" in _seg, _seg[:0])
ok("注释写明为什么（悬停提示也是 Qt 顶层窗）", "tooltip" in _seg)
ok("只认微信自己进程的窗（pid 比对）", "GetWindowThreadProcessId" in _seg and "!= pid" in _seg)
ok("只认 Qt 类名（不误判别的程序）", 'startswith("Qt")' in _seg)

print("── F. 机械完整性：会话/发送链里的每一次点击都走咽喉点 ──")
_need = ("会话行（切会话）", "搜索入口", "搜索浮层结果行", "搜索浮层结果行（box 路线）",
         "主窗搜索结果行", "聚焦输入栏", "发图聚焦输入栏", "点「发送」")
_missing = [t for t in _need if ('"%s"' % t) not in _SRC]
ok("八处点击都带了可对账的 tag（缺：%s）" % (_missing or "无"), not _missing)
ok("`_click_posted` 调用点 ≥ 8", _SRC.count("self._click_posted(") >= 8,
   str(_SRC.count("self._click_posted(")))
ok("关窗走安全咽喉点（绝不关微信主窗）",
   "_wm_close_safe(int(hwnd)" in _SRC[_SRC.index("def _close_stray_window("):][:900])

print("── G. 防回归：不许把『证据复用』这类加速器偷偷加回来 ──")
ok("没有会话证据记账/复用机器（`_proof_fresh`）", "_proof_fresh" not in _SRC)
ok("没有链作用域装饰器（`_chain_scoped`）", "_chain_scoped" not in _SRC)
ok("没有把 `chat_is_open` / `chat_identity_ok` 的调用改去别的包装",
   _SRC.count("self.chat_is_open(") >= 8 and _SRC.count("self.chat_identity_ok(") >= 5,
   "%d / %d" % (_SRC.count("self.chat_is_open("), _SRC.count("self.chat_identity_ok(")))
_AB = os.path.join(ROOT, "_scratch", "_live_ab_speed.py")
ok("A/B 活体脚本还在（下一轮提速改法必须再跑它一次）", os.path.exists(_AB),
   "缺 %s" % _AB)

print("\n==== 点偏防护判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
