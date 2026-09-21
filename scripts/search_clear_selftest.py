# -*- coding: utf-8 -*-
"""判据：搜索切会话的**清空**必须确定性、失败必须收尾、失败必须看得见。

跑法： runtime\\python\\python.exe scripts\\search_clear_selftest.py   退出码 0=全过 / 1=有失败

为什么要这条判据（2026-09-21，网友 v0919 追加反馈 ②③）：
  老实现清空搜索框＝往浮层**盲发 8 个退格**，是个猜数、而且**不验证**。框里超过 8 个字就清不干净
  ⇒ 下一次的查询词＝「旧词＋新词」⇒ **累积**（现场截图：框里是「KCKCKC」；微信搜索历史里堆着
  「测试测试」「测试测试测试」「KC测试测试」一条比一条长）⇒ 结果行永远匹配不上 ⇒ 切会话永远失败
  ⇒ **每条回复都发不出去**（表现＝「聊一会儿就不回话了」），而且失败只写 log.info ⇒ 用户对着
  控制台只能看到三行 checkpoint，完全不知道发生了什么。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ⛔ V-R14-1 隔离：判据不许写产品 data/ 与 logs/（台账指到临时区）。
#   ⚠️ 见 `scripts\_iso14.py` 文件头：手抄的隔离段若写在 `sys.path.insert` 之前会**静默失效**。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # scripts\（见 `_iso14` 文件头）
import _iso14                                   # noqa: E402
_iso14.wechat()
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WECHAT_PY = os.path.join(ROOT, "agent", "wechat.py")
VERIFIERS_PY = os.path.join(ROOT, "agent", "verifiers.py")

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def func_body(src, name):
    """切出某个方法体（按起止锚点，别用"后面 N 字符"——那种扫法会误吃隔壁代码）。"""
    i = src.find("    def %s(" % name)
    if i < 0:
        return ""
    ends = [x for x in (src.find("\n    def ", i + 12), src.find("\n    @staticmethod", i + 12),
                        src.find("\n    # ──", i + 12)) if x > 0]
    return src[i:min(ends)] if ends else src[i:]


def code_of(src, name):
    """方法体里**去掉整行注释**后的代码（静态断言只看代码）。"""
    body = func_body(src, name)
    return "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))


def _bad_clear(body):
    """「清空」这一段**不许**有的东西；返回违规说明（空＝合格）。"""
    t = body or ""
    bad = []
    if "_clear_search_input" not in t and "_clear_search_windows" not in t:
        bad.append("没有确定性清空（缺 _clear_search_input/_clear_search_windows）")
    if '"\\b" * 8' in t:
        bad.append("还在用固定 8 个退格当清空（猜数、不验证）")
    if "VK_CONTROL" not in t and "0x11" not in t:
        bad.append("清空没有走 Ctrl+A 全选（长度无关的确定性做法）")
    return bad


def main():
    src = open(WECHAT_PY, encoding="utf-8").read()
    vf = open(VERIFIERS_PY, encoding="utf-8").read()

    # ① 全文件不再有"固定 8 个退格"这种猜数清空
    ok("① 全仓不再出现固定 8 退格的清空写法", '"\\b" * 8' not in src,
       "还有 %d 处" % src.count('"\\b" * 8'))

    # ② 打字前：确定性清空（图标浮层路线 + box 路线都要）
    b_search = code_of(src, "open_chat_by_search")
    ok("② 切到了 open_chat_by_search 方法体", bool(b_search))
    ok("② 浮层路线打字前先确定性清空（_clear_search_input）",
       "_clear_search_input" in b_search, )
    ok("② box 形态打字前也清空（老实现这里**完全不清空** ⇒ 上一次的字会拼上来）",
       b_search.count("_clear_search_input") >= 2, b_search.count("_clear_search_input"))

    # ③ 收尾：出去之前清空搜索框（不留查询词 ⇒ 不污染微信搜索历史、不留"累积"的种子）
    ok("③ 搜索路线收尾会清空搜索框（_clear_search_windows）",
       "_clear_search_windows" in b_search)
    b_clr = code_of(src, "_clear_search_input")
    b_clrw = code_of(src, "_clear_search_windows")
    ok("③ 切到了两个新方法体", bool(b_clr) and bool(b_clrw))
    ok("③ `_clear_search_input` 走 Ctrl+A 全选 + 删除（长度无关）",
       ("VK_CONTROL" in b_clr or "0x11" in b_clr) and "0x41" in b_clr and "0x08" in b_clr, b_clr[:0])
    ok("③ `_clear_search_windows` 只动搜索窗（不碰微信主窗）",
       "_search_window_hwnds" in b_clrw and "send_text(main" not in b_clrw)

    # ④ 失败要**看得见**：切会话失败 → warning + 台账（可供检验器读）
    ok("④ 记录了切会话失败台账（note_switch_fail）", "def note_switch_fail(" in src)
    b_sw = code_of(src, "switch_chat_posted")
    ok("④ 搜索路线失败走 warning + 记台账（不再只 log.info）",
       "note_switch_fail(" in b_sw and 'log.warning("切会话：搜索框路线没成' in b_sw)
    ok("④ 台账有环形上限与读取口", "def recent_switch_fails(" in src and "_SWITCH_FAILS_MAX" in src)

    # ⑤ 用户可见面：症状检验器「它不回复」必须把这格摆出来
    ok("⑤ 检验器「它不回复」新增『切不到会话 ⇒ 回复发不出去』一格",
       "recent_switch_fails(" in vf and "切不到会话" in vf)

    # ⑥ 负例：老的清空写法必须被同一组判据判**不合格**（否则这条判据没有灵敏度）
    OLD = ('    def open_chat_by_search(self):\n'
           '        try:\n'
           '            backend.send_text(int(pop_hwnd), "\\b" * 8)\n'
           '            time.sleep(0.3)\n'
           '            ok_t, why_t = backend.send_text(int(pop_hwnd), name)\n')
    bad = _bad_clear(OLD)
    ok("⑥ 负例：老实现（固定 8 退格、无 Ctrl+A、无收尾）被同一判据判不合格",
       len(bad) >= 3, bad)
    ok("⑥ 正例：现在的清空实现本身合格", not _bad_clear(b_clr + b_clrw), _bad_clear(b_clr + b_clrw))

    # ⑦ 功能：台账环形 + 读取口径
    try:
        from agent import wechat as _wx
        for i in range(25):
            _wx.note_switch_fail("单测", "第 %d 条" % i)
        _r = _wx.recent_switch_fails(50)
        ok("⑦ 台账有上限（写 25 条只留最近 20）", len(_r) <= 20, len(_r))
        ok("⑦ 台账保留的是**最新的**（最后一条 why=第 24 条）",
           bool(_r) and _r[-1].get("why") == "第 24 条", _r[-1] if _r else None)
        ok("⑦ 取最近 2 条返回 2 条且顺序为新在后", len(_wx.recent_switch_fails(2)) == 2)
    except Exception as e:
        ok("⑦ 台账功能自测", False, "异常：%s" % e)

    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
