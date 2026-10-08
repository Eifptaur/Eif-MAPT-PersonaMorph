# -*- coding: utf-8 -*-
"""panel_probe_popup.py —— 在**独立窗**上做"开面板前后"像素对比（A 项收官，P0-1 用）

为什么走独立窗：
  · 独立窗布局极简（标题+聊天区+输入框），笑脸按钮位置固定且已标定比例；
  · 全程**投递**（`send_text`/`click` 都不动光标）⇒ 不打扰用户；
  · 主窗路径要 `_search_group`/`_open_chat_guarded`，风险高得多。

⛔ **最大的干扰源：用户在动鼠标**（第十二轮实测踩到两次·报告 §64 同类）
   · 表情面板是「**失焦即关**」的浮窗 —— 用户一动鼠标点到别处，面板**立刻消失**；
   · 症状：差分只抓到"笑脸按钮自己那 216x26 的一小条"，误判成"面板没弹出"；
   · ⇒ 本脚本必须**密集连拍**（不是点完等 0.9s 才抓一张），
     只要**任何一帧**出现"大面积变化"，就认定面板弹出过并据该帧定位；
   · ⇒ 并且**开头就提示**（可被用户打断，如实报告）。

做什么（**只读 + 一次投递点击，不发送任何消息**）：
  ① 抓"面板关闭"基线（连拍 3 帧取稳定值）
  ② 投递点笑脸（工具栏最左的 😊，比例标定）
  ③ **密集连拍 20 帧 / 每帧 0.15s**，逐帧与基线差分
  ④ 取"变化面积最大"的那一帧 ⇒ 就是**面板的真实 rect**
  ⑤ 换算相对主窗比例 ⇒ 打印可直接替换 `_panel_rect` 兜底的代码行

⚠️ 若全部帧都没出现大面积变化，如实报告（不假成功），并提示"可能被用户的鼠标点掉了"。

用法：runtime/python/python.exe -u scripts/panel_probe_popup.py [独立窗会话名]
"""
import ctypes as ct
import ctypes.wintypes as wt
import logging
import os
import sys
import time

logging.disable(logging.INFO)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent import wechat_windows as WW  # noqa: E402
from agent import input_backend as IB  # noqa: E402

_U32 = ct.windll.user32

#: 独立窗里「笑脸」按钮的比例位置（工具栏最左那个 😊）
#: 来自报告 §56.2：工具栏 y 900~930 / x 40~270（图 1182x970）⇒ 笑脸中心约 (0.045w, 0.945h)
EMOJI_BTN_REL = (0.045, 0.945)


def grab(hwnd):
    im, meta = WW.grab_window(hwnd, physical=False)
    return im, meta


def diff_bbox(a, b, tol: int = 24):
    """两张同尺寸图的"变化像素"包围盒 + 变化像素数 ⇒ ((l,t,r,b)|None, n)。"""
    if a is None or b is None or a.size != b.size:
        return None, 0
    pa, pb = a.load(), b.load()
    W, H = a.size
    l, t, r, bo = W, H, -1, -1
    n = 0
    for y in range(0, H, 2):
        for x in range(0, W, 2):
            ca, cb = pa[x, y], pb[x, y]
            if (abs(ca[0] - cb[0]) + abs(ca[1] - cb[1]) + abs(ca[2] - cb[2])) > tol * 3:
                n += 1
                if x < l:
                    l = x
                if x > r:
                    r = x
                if y < t:
                    t = y
                if y > bo:
                    bo = y
    if r < 0:
        return None, n
    return (l, t, r, bo), n


def scan_wechat_windows(main_hwnd):
    """枚举**微信进程的所有可见顶层窗**（含新出现的浮窗）⇒ 便于看清面板到底是什么窗。

    ★ 线索（第十二轮）：
      「那个表情面板是个**单独的浮窗**，它**直接浮在上面**来了，而**微信本身还在后面**」
      ⇒ 面板**不是内嵌弹层**，而是**独立顶层窗**（浮在最前）
      ⇒ 那么"枚举不到"只可能是**时序/被点掉**，不是"它不存在"。
    """
    try:
        import ctypes as _ct
        from ctypes import wintypes as _wt
        u32 = _ct.windll.user32
        pid = _wt.DWORD()
        u32.GetWindowThreadProcessId(int(main_hwnd), _ct.byref(pid))
        wx_pid = int(pid.value)
        out = []

        def cb(h, _l):
            p2 = _wt.DWORD()
            u32.GetWindowThreadProcessId(h, _ct.byref(p2))
            if int(p2.value) != wx_pid:
                return True
            if not u32.IsWindowVisible(h):
                return True
            cls = _ct.create_unicode_buffer(256)
            u32.GetClassNameW(h, cls, 256)
            ttl = _ct.create_unicode_buffer(512)
            u32.GetWindowTextW(h, ttl, 512)
            rr = _wt.RECT()
            u32.GetWindowRect(h, _ct.byref(rr))
            out.append({"hwnd": int(h), "cls": cls.value, "title": ttl.value,
                        "w": rr.right - rr.left, "h": rr.bottom - rr.top,
                        "rect": (rr.left, rr.top, rr.right, rr.bottom)})
            return True

        CB = _ct.WINFUNCTYPE(_ct.c_bool, _ct.c_void_p, _ct.c_void_p)
        u32.EnumWindows(CB(cb), 0)
        return out
    except Exception:
        return []


def main():
    win_name = sys.argv[1] if len(sys.argv) > 1 else ""
    wins = [w for w in WW.list_wechat_windows() if w["title"].strip()]
    if not wins:
        print("[✗] 没有微信可见窗口")
        return 1
    # 独立窗 = 有会话名标题且**不是面积最大**的那个；没指定就取最小的
    main_w = max(wins, key=lambda w: w["w"] * w["h"])
    others = [w for w in wins if w["hwnd"] != main_w["hwnd"]]
    if win_name:
        tgt = [w for w in wins if win_name in w["title"]]
        if not tgt:
            print("[✗] 没找到含 %r 的窗口；当前 = %s" % (win_name, [w["title"] for w in wins]))
            return 1
        w0 = tgt[0]
    elif others:
        w0 = min(others, key=lambda w: w["w"] * w["h"])
    else:
        print("[✗] 只有主窗、没有独立窗 ⇒ 本脚本要求独立窗（请先双击一个会话）")
        print("    当前窗口 = %s" % [(w['title'], w['w'], w['h']) for w in wins])
        return 1

    hwnd = w0["hwnd"]
    print("独立窗 = %r hwnd=%d %dx%d" % (w0["title"], hwnd, w0["w"], w0["h"]))
    print("主窗   = %r %dx%d" % (main_w["title"], main_w["w"], main_w["h"]))
    print()
    print("══════════════════════════════════════════════════════════")
    print("  ⚠️  接下来约 8 秒：**请不要动鼠标、不要点任何地方**")
    print("      （面板是「失焦即关」的浮窗 —— 您一动鼠标它就消失）")
    print("══════════════════════════════════════════════════════════")
    print()

    be = IB.select_backend()
    print("后端 = %s touches_cursor=%s" % (type(be).__name__, getattr(be, "touches_cursor", "?")))

    # ① 基线：连拍 3 帧取稳定值
    print("① 抓基线（3 帧）…")
    base = None
    for _i in range(3):
        im, meta = grab(hwnd)
        if im is not None:
            base = im
        time.sleep(0.12)
    if base is None:
        print("[✗] 抓图失败")
        return 1
    print("   基线图 = %dx%d" % base.size)
    w0_all = scan_wechat_windows(hwnd)
    print("   此时微信可见顶层窗 %d 个：%s" % (
        len(w0_all), [(w["title"][:14] or w["cls"][:18], w["w"], w["h"]) for w in w0_all]))

    # ② 投递点笑脸
    W, H = base.size
    sx = int(W * EMOJI_BTN_REL[0])
    sy = int(H * EMOJI_BTN_REL[1])
    rct = wt.RECT()
    _U32.GetWindowRect(hwnd, ct.byref(rct))
    pt = (int(rct.left + sx), int(rct.top + sy))
    print("② 点笑脸：图内 (%d,%d) ⇒ 屏幕 (%d,%d)" % (sx, sy, pt[0], pt[1]))
    print("   click ⇒ %s" % (be.click(hwnd, pt, press_ms=110),))

    # ③ ★ 密集连拍（这是关键改动：不能"点完等 0.9s 才抓一张"）
    print("③ 密集连拍 24 帧（每帧 0.14s，共约 3.4s）…")
    best = {"n": 0, "im": None, "bbox": None, "i": -1, "wins": None}
    for i in range(24):
        time.sleep(0.14)
        im, _m = grab(hwnd)
        if im is None or im.size != base.size:
            continue
        bbox, n = diff_bbox(base, im)
        wins_now = scan_wechat_windows(hwnd)
        flag = ""
        if n > best["n"]:
            best.update(n=n, im=im, bbox=bbox, i=i, wins=wins_now)
            flag = "  ← 新最大"
        if wins_now and len(wins_now) != len(w0_all):
            flag += "  ★窗口数变了: %d" % len(wins_now)
        print("   帧 %02d 变化像素=%-6d 窗口数=%d%s" % (i + 1, n, len(wins_now), flag))

    # ④ 判定
    n = best["n"]
    print()
    print("④ 最大变化帧 = 第 %d 帧，变化像素 = %d" % (best["i"] + 1, n))
    if n < 200:
        print("[!] 全部帧都没有大面积变化 ⇒ 面板**确实没弹出**（或被您的鼠标点掉了）。")
        print("    已存 _panel_probe_before.png（基线）供人工核对。")
        return 2

    im = best["im"]
    im.save(os.path.join(_ROOT, "报告", "_panel_probe_after.png"))
    if best["wins"]:
        print("   该帧微信可见顶层窗：")
        for w in best["wins"]:
            mark = "  ← ★新窗（只在面板打开时出现）" if len(best["wins"]) > len(w0_all) and \
                w["hwnd"] not in [x["hwnd"] for x in w0_all] else ""
            print("     hwnd=%-12d %-30s %dx%d%s" % (
                w["hwnd"], (w["title"][:26] or w["cls"][:26]), w["w"], w["h"], mark))

    if best["bbox"]:
        l, t, r, b = best["bbox"]
        print("   变化区域（图内）= (%d, %d, %d, %d)  %dx%d" % (l, t, r, b, r - l, b - t))
        lr = wt.RECT()
        _U32.GetWindowRect(hwnd, ct.byref(lr))
        print("   屏幕坐标 = (%d, %d, %d, %d)" % (lr.left + l, lr.top + t, lr.left + r, lr.top + b))

        rs = wt.RECT()
        _U32.GetWindowRect(main_w["hwnd"], ct.byref(rs))
        WL, HT = rs.right - rs.left, rs.bottom - rs.top
        pl = lr.left + l - rs.left
        ptt = lr.top + t - rs.top
        pr = lr.left + r - rs.left
        pb = lr.top + b - rs.top
        print()
        print("⑤ 换算到**主窗**比例（主窗 %dx%d）：" % (WL, HT))
        print("   面板相对主窗 = (%d, %d, %d, %d)" % (pl, ptt, pr, pb))
        print("   建议代码（比例形式）：")
        print("   return (l + int(W*%.4f), t + int(H*%.4f), l + int(W*%.4f), t + int(H*%.4f))"
              % (pl / WL, ptt / HT, pr / WL, pb / HT))
        print("   ⇒ 重点看 **top 比例**（当前兜底用 H*0.20）：实测 = %.4f（偏差 %d px）"
              % (ptt / HT, ptt - int(HT * 0.20)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
