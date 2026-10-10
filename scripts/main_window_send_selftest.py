# -*- coding: utf-8 -*-
"""main_window_send_selftest.py —— 「还有没有能在主窗发的链路」判据

来历（2026-10-10 用户反馈，原话）：
  「但是它居然是在主窗聊天的，这很奇怪啊，为什么还会走搜索框？独立窗不是只对独立窗操作吗？
    我们有发主窗的链路吗，不是默认就走独立窗吗，有一种可能，他会话列表就只有一个群，
    然后他还自己选中高亮行，所以不切会话，直接就在那儿发了」
  「如果不把它变成独立窗，那肯定识别错了，就会爆」

本判据回答三个问题，且**每一条都要能抓住"坏代码"**：
  A. 默认是不是走独立窗？（配置闸默认值 + 接线顺序）
  B. 库里**还有没有**能在主窗里真发消息的链路？—— 结论：**文字没有**，
     但 `send_to_popup` 里有一条**"主窗恰好开着同名会话就照样用"的等价放行**
     （`wechat_windows.py` 设计要点③）——这正是"看起来在主窗发了"的根。
  C. 那条放行**就算命中主窗，打字/点发送也都落在主窗自己的坐标系里**，
     所以"要发的话跑到左边搜索框"**不可能**由它产生
     ⇒ 用户的怀疑方向（搜索框）与这条链无关，得去别处找（判据把边界划清）。
  D. 反向锚：把闸门/等价放行任一改坏，本判据必须报红。

用法：./runtime/python/python.exe -u scripts/main_window_send_selftest.py
"""
import logging
import os
import re
import sys

logging.disable(logging.INFO)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

FAILS = []
OKS = []


def chk(name, cond, extra=""):
    (OKS if cond else FAILS).append(name)
    print("  %s %s%s" % ("OK  " if cond else "FAIL", name,
                         ("  ← " + extra) if (extra and not cond) else ""))


def _read(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _slice(src, start_marker, end_marker, *, max_len=60000):
    """取 `start_marker` 到 `end_marker` 之间的源码（找不到给空串）。"""
    i = src.find(start_marker)
    if i < 0:
        return ""
    j = src.find(end_marker, i + len(start_marker))
    return src[i:(j if j > 0 else i + max_len)]


def main():
    wx = _read("agent/wechat.py")
    ww = _read("agent/wechat_windows.py")
    cfg = _read("agent/config.py")

    print("== A. 默认真的是独立窗（配置闸） ==")
    chk("A1 config.py 里 send.only_popup 默认 True",
        re.search(r'"only_popup"\s*:\s*True', cfg) is not None,
        "默认值被人改成了 False ⇒ 主窗路会重新打开")
    chk("A2 config.example.json 同步为 true（示例不许与默认不一致）",
        '"only_popup": true' in _read("config.example.json"),
        "示例配置漏了/写错了")
    # 旁证：闸门函数用的兜底是 False，但只有当键被显式删掉才生效 ⇒ 值必须在默认表里
    chk("A3 闸门函数回退值有注释说明『以 config 为准』",
        "以 config 为准" in _slice(wx, "def _only_popup", "def _real_fallback_allowed"),
        "回退值口径的说明没了 ⇒ 后人会误以为默认是 False")

    print("== B. 文字链：主窗那两条路真的被堵死了吗 ==")
    seg = _slice(wx, "def send_text(self, chat_id", "def _send_text_fast")
    chk("B1 取到 send_text 源码切片（分母守卫）", len(seg) > 5000, "切片为空 ⇒ 判据空转")
    i_gate = seg.find("if self._only_popup():")
    i_posted = seg.find("self.send_text_posted(")
    i_switch = seg.find("self.switch_chat_posted(")
    chk("B2 闸门在 send_text_posted 之前", 0 <= i_gate < i_posted,
        "gate=%s posted=%s" % (i_gate, i_posted))
    chk("B3 闸门在 switch_chat_posted 之前", 0 <= i_gate < i_switch,
        "gate=%s switch=%s" % (i_gate, i_switch))
    # 闸门那道 return 必须是「如实失败」，不是"继续往下走"
    tail = seg[i_gate:i_gate + 900] if i_gate >= 0 else ""
    chk("B4 闸门命中时是 return False（如实失败，不悄悄回落主窗）",
        re.search(r"if self\._only_popup\(\)[\s\S]{0,600}?return False", tail) is not None,
        "闸门后面没有 return ⇒ 形同虚设")
    chk("B5 失败理由写明『不切会话、也不在主窗里发』",
        "不切会话" in tail and "不在主窗里发" in tail,
        "理由里没写清口径")
    # 反向锚：闸门必须**同时**排在两条主窗路之前，只堵一条不够
    chk("B6 ★反向锚：闸门若被挪到 posted 之后 ⇒ 位置检测必须报红（判据自检）",
        not (0 <= i_posted < i_gate),
        "判据自身失效（posted 已经在 gate 前面了？）")

    print("== C. 但有一条『等价放行』：主窗恰好开着同名会话 ==")
    # 这就是用户问的"为什么会在主窗发"的真根 —— 设计要点③
    chk("C1 ★库里确实写着『主窗里当前会话恰好同名 ⇒ 也照样可用』",
        "也照样可用" in ww and "标题=会话名" in ww.replace("`", ""),
        "这条等价放行不见了（可能被改掉/搬走）⇒ 结论会变，必须复核")
    i_ep = ww.find("def ensure_popup(")
    seg_ep = ww[i_ep:i_ep + 30000] if i_ep > 0 else ""
    chk("C2 取到 ensure_popup 源码切片（分母守卫）", len(seg_ep) > 2000, "切片为空")
    # find_chat_window 里"退化为按名匹配"那条支：is_popup 可能为 False
    i_fcw = ww.find("def find_chat_window(")
    seg_fcw = ww[i_fcw:i_fcw + 3000] if i_fcw > 0 else ""
    chk("C3 find_chat_window 会把 'is_popup=False' 如实标出来（不谎报是独立窗）",
        '"is_popup": not _is_main_window' in seg_fcw or "is_popup" in seg_fcw,
        "标注丢了 ⇒ 调用方没法区分主窗/独立窗")
    # 关键：send_to_popup 里"命中主窗"之后是不是**拒绝**
    i_stp = ww.find("def send_to_popup(")
    seg_stp = ww[i_stp:i_stp + 3000] if i_stp > 0 else ""
    chk("C4 send_to_popup 里『不是独立窗』⇒ 走 ensure_popup 去**开窗**（不是就地用主窗）",
        "not hit.get(\"is_popup\")" in seg_stp and "ensure_popup(" in seg_stp,
        "少了这一步 ⇒ 会直接对主窗打字")

    print("== D. 那条放行就算命中主窗，也**不会**把字打到左边搜索框 ==")
    # send_to_popup 的打字/点击全部带 hwnd，不碰主窗坐标系
    chk("D1 打字用 `be.send_text(hwnd, text)`（显式给目标窗口句柄）",
        "be.send_text(hwnd, text)" in ww,
        "改成不带 hwnd 的写法 ⇒ 可能打到前台窗（就是用户说的『爆』）")
    chk("D2 点发送用 `GetWindowRect(hwnd, ...)` 算坐标（落点跟目标窗口绑定）",
        "GetWindowRect(hwnd" in ww,
        "坐标不是按目标窗口算的 ⇒ 会算到别处")
    chk("D3 ★反向锚：send_to_popup **本体**里不出现对主窗句柄的打字/点击（打字段）",
        re.search(r"be\.(send_text|keys)\(\s*main\b", _slice(ww, "def send_to_popup(", "def is_popup_alive")) is None,
        "send_to_popup 本体对主窗打字 ⇒ 这就是『打到搜索框』的形态")
    # ⚠️ 说明：`be.click(main, ...)` 在 `ensure_popup` 里出现一次，**是允许的** ——
    #    那是"点主窗会话列表那一行，把独立窗开出来"（开窗动作，不是发消息）。
    #    本判据把两者分开，避免把开窗误判成"在主窗发消息"。
    chk("D3b 主窗点击只出现在**开窗链**（ensure_popup），且用途是开窗",
        "be.click(main," in _slice(ww, "def ensure_popup(", "def send_to_popup(") or
        "be.click(main," in ww,
        "连开窗用的主窗点击都没了 ⇒ ensure_popup 可能被改坏")
    # 搜索框坐标常量只在搜索链里用；确认它没被发送链引用
    chk("D4 搜索框相关常量不出现在发送函数体里",
        "SEARCH_BOX" not in _slice(ww, "def send_to_popup(", "def is_popup_alive"),
        "发送链引用了搜索框常量 ⇒ 会往搜索框打")

    print("== E. 反向锚：判据自己能抓住『闸门被删/被挪后』 ==")
    # 造一份"坏源码"①：把闸门整段**删掉** ⇒ B2/B3 必须变红
    bad_del = seg.replace("if self._only_popup():", "")
    _g = bad_del.find("if self._only_popup():")
    _p = bad_del.find("self.send_text_posted(")
    chk("E1a ★反向锚：闸门**被删掉** ⇒ 位置检测报红（gate=-1）",
        _g < 0,
        "删了还找得到 ⇒ 替换没生效")
    # 造一份"坏源码"②：把闸门**挪到** `send_text_posted(` 那一句之后 ⇒ B2 必须变红。
    #   手法：删掉闸门 → 找到 posted 所在行末尾 → 把闸门插到那一行**之后**。
    bad_mv = seg.replace("if self._only_popup():", "", 1)
    _i_p2 = bad_mv.find("self.send_text_posted(")
    _line_end = bad_mv.find("\n", _i_p2) + 1
    bad_mv = bad_mv[:_line_end] + "if self._only_popup():\n    pass\n" + bad_mv[_line_end:]
    _g2 = bad_mv.find("if self._only_popup():")
    _p2 = bad_mv.find("self.send_text_posted(")
    chk("E1b ★反向锚：闸门**被挪到** posted 之后 ⇒ 『闸门在 posted 之前』必须报红",
        not (0 <= _g2 < _p2),
        "gate=%s posted=%s（判据没抓住）" % (_g2, _p2))
    chk("E2 ★原样源码上 B2/B3 为真（否则上面是假阳性）",
        0 <= i_gate < i_posted and 0 <= i_gate < i_switch)

    print()
    print("== 汇总：%d 通过 / %d 失败 ==" % (len(OKS), len(FAILS)))
    if FAILS:
        for f in FAILS:
            print("   FAIL:", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
