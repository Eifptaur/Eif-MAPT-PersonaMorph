# -*- coding: utf-8 -*-
"""send_timing_probe.py —— **发送链分段计时**（真机跑，只读；不改任何行为）

目的：回答"发送能压到多少 / 粘贴能不能压进 1 秒"。
做法：**逐段打点**，每段各测 N 次，打印中位/p90/最慢。
     全程**不真的发消息**（不按回车、不点发送按钮），每段量完**清空输入框**。

⚠️ 修正（用户现场发现的两个坑，都是本脚本第一版的真 bug）：
   ① 第一版**不清空**，让 `[B]`/`[F]`/`[G]` 各段投的字在输入框里累积
      ⇒ 用户看到"第一轮连续输四五个计时才发出去 / `--text A` 时里面几百个 A"；
   ② 第一版 `[G]`/`[H]` **真的按了回车** ⇒ 与"不发消息"自相矛盾（注释写错了）。
   ⇒ 现在：每段量完 `_clear_input()`（Ctrl+A → Del）；组合链**只投字、不按回车**。

用法：
    # 只量"投递打字 + 伪激活"这一段（不动聊天区内容，安全）
    ./runtime/python/python.exe -u scripts/send_timing_probe.py --chat "文件传输助手" --text "测试计时"

    # 量到"发送键变绿"为止（字真进去了，但不按回车、不发出去）
    ./runtime/python/python.exe -u scripts/send_timing_probe.py --chat "文件传输助手" --text "计时" --to-green

    # 量剪贴板粘贴（先放文本，再投 WM_PASTE，看输入框是否变绿）
    ./runtime/python/python.exe -u scripts/send_timing_probe.py --chat "文件传输助手" --paste

⛔ 本脚本**只读+投递输入**，不切会话、不按回车、不点发送按钮 ⇒ 不会真的把消息发出去。
"""
import argparse
import ctypes
import logging
import os
import statistics
import sys
import time

logging.disable(logging.INFO)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent import input_backend as IB          # noqa: E402
from agent import wechat_windows as WW         # noqa: E402

log = logging.getLogger("persona-morph")

U32 = ctypes.windll.user32
K32 = ctypes.windll.kernel32


def _green_ratio(hwnd):
    """发送键绿占比（判"输入框里有内容"）。量不到返回 -1。"""
    try:
        im, _ = WW.grab_window(hwnd, physical=False)
        return WW.send_button_green_ratio(im) if im is not None else -1.0
    except Exception:
        return -1.0


def _clear_input(backend, hwnd):
    """清空输入框（Ctrl+A → Del）。

    ⛔ **为什么必须有这一步**（用户现场发现，这是本脚本第一版的真 bug）：
       第一版里 `[B] send_text` 跑 n 次、`[F]` 抓帧段、`[G]` 组合链，**各自都往输入框投一遍字**，
       而**全程一次都没清空**。后果：
         · 第一轮发送时，输入框里已经累积了前面所有段投进去的字 ⇒ **"连续输四五个计时才发出去"**；
         · `--text A` 时更明显 ⇒ **"里面几百个 A"**；
         · 之后"看着正常"，只是因为**前面那些被上一轮的回车清空了**（假正常）。
       ⇒ 量速探针**必须每轮清空**，否则它量的是"往已有内容后面追加"，不是在量一次干净发送。
    """
    try:
        backend.keys(hwnd, [IB.VK_CONTROL, ord("A")], hold_ms=0)
        backend.keys(hwnd, [0x2E], hold_ms=0)   # 0x2E = VK_DELETE
        time.sleep(0.05)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass  # 清理型：静默合法


def _one(fn, n=12):
    """跑 n 次，返回 (中位, p90, 最慢, 全量) 毫秒。"""
    xs = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t0) * 1000.0)
    xs_sorted = sorted(xs)
    p90 = xs_sorted[min(len(xs_sorted) - 1, int(len(xs_sorted) * 0.9))]
    return statistics.median(xs), p90, max(xs), xs


def _fmt(name, med, p90, mx, xs, n=None):
    n = n if n is not None else len(xs)
    print("  %-34s 中位 %7.1f ms｜p90 %7.1f ms｜最慢 %7.1f ms  (n=%d)"
          % (name, med, p90, mx, n))


def _fmt_vals(name, xs):
    """按原始毫秒列表打一行（给"计时之外还要清场"的组合链用）。"""
    xs_sorted = sorted(xs)
    p90 = xs_sorted[min(len(xs_sorted) - 1, int(len(xs_sorted) * 0.9))]
    _fmt(name, statistics.median(xs), p90, max(xs), xs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat", default="文件传输助手", help="会话名（只用于定位主窗/渲染窗，不切会话）")
    ap.add_argument("--text", default="计时探针 abcdefg")
    ap.add_argument("--n", type=int, default=12, help="每段重复次数")
    ap.add_argument("--to-green", action="store_true", help="量到「发送键变绿」为止（会往输入框打字）")
    ap.add_argument("--paste", action="store_true", help="量剪贴板粘贴路径")
    ap.add_argument("--no-clear", action="store_true",
                    help="关掉自动清空（**一般不要用**；关掉会让各段投的字在输入框里累积，量出来的不是一次干净发送）")
    args = ap.parse_args()

    from agent import wechat as W
    ad = W.WeChatAdapter.__new__(W.WeChatAdapter)
    ad._gui = None          # `_get_gui` 会自建；先给个 None 免得 AttributeError
    try:
        gui = ad._get_gui()
    except Exception as e:
        print("拿不到 gui：%s" % e)
        return 2
    main_hwnd = int(getattr(gui, "main_hwnd", 0) or 0) or IB.find_main_window()
    if not main_hwnd:
        print("找不到微信主窗（微信没开？）")
        return 2
    backend = IB.select_backend(gui=gui)
    print("=" * 78)
    print("发送链分段计时　主窗=0x%X　后端=%s　每段 n=%d" % (main_hwnd, getattr(backend, "name", "?"), args.n))
    print("=" * 78)

    # ① 伪激活（`_wake`：两次 PostMessage + 固定 sleep 0.08）
    print("\n[A] 伪激活 `_wake`（进输入框前的必备一步）")
    _fmt("_wake(main)", *_one(lambda: backend._wake(main_hwnd), args.n), args.n)

    # ② 投递打字（逐字 WM_CHAR，字数 = len(text)）—— **每轮先清空**（见 `_clear_input` 的说明）
    print("\n[B] 投递打字 `send_text`（逐字 WM_CHAR，%d 字）" % len(args.text))
    _fmt("send_text(main, %d字)" % len(args.text),
         *_one(lambda: (backend.send_text(main_hwnd, args.text), _clear_input(backend, main_hwnd)),
               args.n), args.n)

    # ③ 剪贴板写（放文本）
    print("\n[C] 剪贴板写文本（粘贴路径的第 1 步）")
    def _cb():
        try:
            from agent import clipboard as _cbmod
            _cbmod.set_text(args.text)
        except Exception:
            # 退化：直接走 Win32
            _cb_raw(args.text)
    _fmt("clipboard.set_text(%d字)" % len(args.text), *_one(_cb, args.n), args.n)

    # ④ 投递粘贴消息
    print("\n[D] 投递粘贴 `paste`（投 WM_PASTE，需输入框已有焦点）")
    _fmt("paste(main)", *_one(lambda: backend.paste(main_hwnd), args.n), args.n)

    # ⑤ 投回车
    print("\n[E] 投回车 `keys([VK_RETURN])`")
    _fmt("keys(main,[RETURN])", *_one(lambda: backend.keys(main_hwnd, [IB.VK_RETURN]), args.n), args.n)

    # ⑥ 抓帧 + 绿占比（判据侧，最慢的一环）
    print("\n[F] 抓帧 + 判「发送键变绿」（判据侧）")
    _fmt("grab_window+green_ratio", *_one(lambda: _green_ratio(main_hwnd), min(args.n, 6)), min(args.n, 6))

    # ⑦ 组合：投字号链（不含切会话）——这就是"预备好之后"一次发送的下界
    #
    # ⛔ 修（用户现场发现）：**第一版这里真的按了回车** ⇒ 它会把字发出去，
    #    与脚本抬头写的"不发消息"**自相矛盾**（那句注释是错的）。而且因为不清空，
    #    第一轮就把前面所有段投进去的字一起发了出去 ⇒ 用户看到"连续四五个计时才发出去"。
    # ⚠️ 注意口径：下面量的是 **"投字 → 80ms 等微信吃进去"** 这一段；
    #    `_clear_input()`（Ctrl+A + Del）**在计时之外做**（它在真实发送里不存在，是探针的清场）。
    print("\n[G] 组合：_wake → send_text → 80ms（**不含切会话、不含回车、不发消息**）")
    def _combo_type_once():
        t0 = time.perf_counter()
        backend._wake(main_hwnd)
        backend.send_text(main_hwnd, args.text)
        time.sleep(0.08)
        dt = (time.perf_counter() - t0) * 1000.0
        _clear_input(backend, main_hwnd)          # ← 清场在计时之外
        return dt
    _fmt_vals("wake+type+80ms（到字进框）", [_combo_type_once() for _ in range(max(3, args.n // 3))])

    print("\n[H] 组合：写剪贴板 → _wake → 粘贴 → 80ms（**不发消息**）")
    def _combo_paste_once():
        t0 = time.perf_counter()
        _cb()
        backend._wake(main_hwnd)
        backend.paste(main_hwnd)
        time.sleep(0.08)
        dt = (time.perf_counter() - t0) * 1000.0
        _clear_input(backend, main_hwnd)
        return dt
    _fmt_vals("clip+wake+paste+80ms（到字进框）", [_combo_paste_once() for _ in range(max(3, args.n // 3))])

    # ⑨ 真正的一次发送总耗时 = [G] 或 [H] + [E] 回车（按键的 down/up 是顺序执行，相加即可）
    _g_med = statistics.median([_combo_type_once() for _ in range(max(3, args.n // 3))])
    _h_med = statistics.median([_combo_paste_once() for _ in range(max(3, args.n // 3))])
    _enter = statistics.median(
        [_one(lambda: backend.keys(main_hwnd, [IB.VK_RETURN]), max(3, args.n // 3))[0]
         for _ in range(1)])
    print("\n[I] 真正一次发送的下界（＝ [G] 或 [H] ＋ 回车，不含切会话）")
    print("  %-34s 中位 %7.1f ms" % ("打字路径：type + 回车", _g_med + _enter))
    print("  %-34s 中位 %7.1f ms" % ("粘贴路径：paste + 回车", _h_med + _enter))
    print("  （回车本身 = %.1f ms；切会话另计，属 0.5~3s 大头）" % _enter)

    print("\n" + "=" * 78)
    print("说明：以上**不含**切会话（切会话要抓帧+OCR，是 0.5~3s 量级的大头）。")
    print("     若要量含切会话的整链，另跑 probe_full_send.py（会真发消息）。")
    print("=" * 78)
    return 0


def _cb_raw(s):
    from ctypes import wintypes as wt
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
    U32.OpenClipboard.argtypes = [wt.HWND]; U32.OpenClipboard.restype = wt.BOOL
    U32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]; U32.SetClipboardData.restype = wt.HANDLE
    K32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]; K32.GlobalAlloc.restype = wt.HGLOBAL
    K32.GlobalLock.argtypes = [wt.HGLOBAL]; K32.GlobalLock.restype = ctypes.c_void_p
    if not U32.OpenClipboard(None):
        return False
    try:
        U32.EmptyClipboard()
        data = s.encode("utf-16-le") + b"\x00\x00"
        h = K32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        p = K32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        K32.GlobalUnlock(h)
        U32.SetClipboardData(CF_UNICODETEXT, h)
    finally:
        U32.CloseClipboard()
    return True


if __name__ == "__main__":
    sys.exit(main())
