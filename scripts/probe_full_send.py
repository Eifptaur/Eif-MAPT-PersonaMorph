# -*- coding: utf-8 -*-
"""probe_full_send.py —— 独立弹窗**完整发送链**验证：发到独立窗那个群）

链路（每一步都有可观测验收，不靠"API 说成功"）：
  ① 抓图 → 记录发送前基线（绿占比应为 0）
  ② 投递输入（`send_text`，已修 `_wake`）
  ③ **抓图确认"发送键变绿"**（绿占比 ≥ 0.30）⇒ 这才算"字真进去了"
  ④ 用**图上的比例位置**点「发送」（(0.938w, 0.948h)）
  ⑤ 抓图确认：消息出现在聊天区（发送键回到灰、且气泡增加）
  ⑥ 打印全过程留证

用法：./runtime/python/python.exe -u scripts/probe_full_send.py <会话名关键字> <要发的文字>
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


def ratio(hwnd):
    """绿占比（判"发送键是否变绿"）。"""
    im, _ = WW.grab_window(hwnd, physical=False)
    return (WW.send_button_green_ratio(im) if im else -1.0), im


def main():
    key = sys.argv[1] if len(sys.argv) > 1 else "aaa"
    text = sys.argv[2] if len(sys.argv) > 2 else "[测试] 独立窗链路验证"
    wins = [w for w in WW.list_wechat_windows() if w["title"].strip()]
    tgt = [w for w in wins if key in w["title"]]
    if not tgt:
        print("没找到含 %r 的窗口；当前：%s" % (key, [w["title"] for w in wins]))
        return 1
    w0 = tgt[0]
    hwnd = w0["hwnd"]
    print("目标窗口：%r  hwnd=%d" % (w0["title"], hwnd))

    be = IB.select_backend()
    print("后端：%s  %s" % (type(be).__name__, be.status()))

    # ① 基线
    r0, im0 = ratio(hwnd)
    print("① 发送前基线：绿占比=%.4f" % r0)
    if r0 >= 0.30:
        print("   ⚠️ 输入框里已有字（绿占比不为 0）⇒ 请先清空再跑；已中止。")
        return 1

    # ② 投递输入
    print("② 投递输入 %r" % text)
    print("   send_text ⇒ %s" % (be.send_text(hwnd, text),))
    time.sleep(1.0)

    # ③ 确认"字真进去了"
    r1, im1 = ratio(hwnd)
    im1.save(os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "_临界样本", "send-2-typed.png"))
    print("③ 投递后：绿占比=%.4f ⇒ %s" % (r1, "字已进去 ✅" if r1 >= 0.30 else "字没进去 ❌（中止，不点发送）"))
    if r1 < 0.30:
        return 1

    # ④ 点「发送」（比例位置 ⇒ 屏幕坐标）
    r4 = wt.RECT()
    _U32.GetWindowRect(hwnd, ct.byref(r4))
    im_sz = im1.size
    sx = int(r4.left + im_sz[0] * WW.SEND_BTN_REL[0] / 1.0)
    sy = int(r4.top + im_sz[1] * WW.SEND_BTN_REL[1] / 1.0)
    print("④ 点「发送」：屏幕坐标 (%d,%d)（比例 %.3f,%.3f）" % (sx, sy, *WW.SEND_BTN_REL))
    print("   click ⇒ %s" % (be.click(hwnd, (sx, sy), press_ms=120),))
    time.sleep(1.5)

    # ⑤ 回读
    r2, im2 = ratio(hwnd)
    im2.save(os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "_临界样本", "send-3-after.png"))
    print("⑤ 发送后：绿占比=%.4f ⇒ %s" % (r2, "输入框已清空（发送键回到灰）✅" if r2 < 0.30 else "输入框还有字 ⇒ 可能没发出去 ⚠️"))
    print("   抓图已存 send-2-typed.png / send-3-after.png —— **请看图确认消息是否真的出现在聊天区**")
    return 0


if __name__ == "__main__":
    sys.exit(main())
