# -*- coding: utf-8 -*-
"""probe_deliver_typing.py —— 验证「投递键盘」能否把字打进独立弹窗的输入框（T14 最后一块）

⛔ 安全约束（**必须遵守**）
  · **绝不点「发送」** —— 本脚本只做：点输入框 → 投递文字 → 抓图验证 → **清空**；
  · 清空用「End + 连续退格」（项目 `wechat.py` 里的既有手法：投递不携带修饰键状态，
    所以不能用 `Ctrl+A`）；
  · 任何异常都**立即停止并打印状态**，不继续动作。

判据：投递后抓图算 `send_button_green_ratio` ——
  · `>= 0.30` ⇒ **投递键盘可行**（字真进去了）✅
  · 仍 `≈0.00` ⇒ 投递打不进去 ⇒ 需改真实键盘（届时再评估"打扰"代价）

用法：./runtime/python/python.exe -u scripts/probe_deliver_typing.py [会话名关键字] [要打的字]
"""
import ctypes as ct
import logging
import os
import sys
import time

logging.disable(logging.INFO)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent import wechat_windows as WW  # noqa: E402

_U32 = ct.windll.user32
_VK_END = 0x23
_VK_BACK = 0x08


def screen_pt_for(hwnd: int, img, rel):
    """把"图上比例"换算成**屏幕坐标**（逻辑像素）。

    链：图上像素 / scale = 逻辑坐标（客户区）⇒ + 窗口客户区原点（屏幕，逻辑） = 屏幕坐标。
    ⚠️ `GetWindowRect` 返回的也是逻辑像素，所以这里只需处理"客户区原点"这一处偏移。
    """
    import ctypes.wintypes as _wt
    sc = 1.0
    try:
        dpi = int(_U32.GetDpiForWindow(int(hwnd)) or 0)
        if dpi > 0:
            sc = float(dpi) / 96.0
    except Exception:
        sc = 1.0
    p = _wt.POINT(0, 0)
    _U32.ClientToScreen(int(hwnd), ct.byref(p))
    ox, oy = int(p.x), int(p.y)          # 客户区左上角（屏幕，逻辑像素）
    return int(ox + img.size[0] * rel[0] / sc), int(oy + img.size[1] * rel[1] / sc)


def main():
    key = sys.argv[1] if len(sys.argv) > 1 else "aaa"
    text = sys.argv[2] if len(sys.argv) > 2 else "测"
    wins = [w for w in WW.list_wechat_windows() if w["title"].strip()]
    tgt = [w for w in wins if key in w["title"]]
    if not tgt:
        print("没找到含 %r 的窗口；当前：%s" % (key, [w["title"] for w in wins]))
        return 1
    w0 = tgt[0]
    hwnd = w0["hwnd"]
    print("目标窗口：%r  hwnd=%d" % (w0["title"], hwnd))

    # ── 0. 前置：确认当前是"未输入"（否则先清空，避免叠加）────────────────
    img, meta = WW.grab_window(hwnd, physical=True)
    r0 = WW.send_button_green_ratio(img)
    print("0) 投递前基线：绿占比=%.4f（应≈0；若不为 0 说明输入框已有字）" % r0)

    # ── 1. 取投递后端 ──────────────────────────────────────────────────
    try:
        from agent import input_backend as IB
        be = IB.select_backend()
        print("1) 后端：%s  status=%s" % (type(be).__name__, (be.status() or {})))
    except Exception as e:
        print("1) 取后端失败：%s" % str(e)[:160])
        return 1

    # ── 2. 点输入框（投递点击；尽量不激活）───────────────────────────────
    pt = screen_pt_for(hwnd, img, WW.INPUT_REL)
    print("2) 点输入框：屏幕坐标 %s（比例 %s）" % (pt, WW.INPUT_REL))
    try:
        if hasattr(be, "click"):
            try:
                res = be.click(hwnd, pt, press_ms=60)
            except TypeError:
                res = be.click(hwnd, pt)
            print("   click ⇒ %s" % (res,))
        else:
            print("   ⚠️ 该后端没有 click()")
    except Exception as e:
        print("   click 异常：%s" % str(e)[:160])

    time.sleep(0.5)

    # ── 3. 投递文字 ────────────────────────────────────────────────────
    print("3) 投递文字 %r" % text)
    try:
        res = be.send_text(hwnd, text)
        print("   send_text ⇒ %s" % (res,))
    except Exception as e:
        print("   send_text 异常：%s" % str(e)[:160])

    time.sleep(0.7)

    # ── 4. 抓图验证 ────────────────────────────────────────────────────
    img2, _ = WW.grab_window(hwnd, physical=True)
    r1 = WW.send_button_green_ratio(img2) if img2 else -1
    ok = (r1 >= 0.30)
    print("4) 投递后：绿占比=%.4f ⇒ **%s**" % (r1, "投递键盘可行 ✅" if ok else "投递没打进去 ❌"))
    if img2:
        img2.save(os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "_临界样本",
                               "deliver-after-%s.png" % w0["title"][:10].replace(" ", "_")))

    # ── 5. 清空（End + 连续退格；**不发送**）──────────────────────────────
    print("5) 清空输入框（End + 退格，绝不点发送）")
    try:
        if hasattr(be, "keys"):
            for _ in range(2):
                try:
                    be.keys(hwnd, [_VK_END])
                    time.sleep(0.12)
                    be.keys(hwnd, [_VK_BACK] * 20)
                    time.sleep(0.25)
                except Exception as e:
                    print("   清空异常：%s" % str(e)[:120])
                    break
        else:
            print("   ⚠️ 该后端没有 keys()，无法自动清空 ⇒ **请你手动清空**")
    except Exception as e:
        print("   清空异常：%s" % str(e)[:160])

    time.sleep(0.5)
    img3, _ = WW.grab_window(hwnd, physical=True)
    r2 = WW.send_button_green_ratio(img3) if img3 else -1
    print("6) 清空后：绿占比=%.4f ⇒ %s" % (r2, "已清空 ✅" if r2 < 0.30 else "⚠️ 可能还有残留，请手动检查"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
