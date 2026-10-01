# -*- coding: utf-8 -*-
"""probe_popup_controls.py —— 标定独立弹窗内「输入框」与「发送」按钮的位置（T12 前置）

目的：为"只对独立弹窗操作"标定两个关键控件，用**比例**表达（跨尺寸稳定）：
      · 输入框（底部大白框）
      · 「发送」按钮（右下角）
方法：
  1. **OCR 找「发送」**（最直接：按钮上有这两个字）⇒ 给出其框位置；
  2. **几何法找输入框**：在底部区域找"连续的大块浅色矩形"（背景白 ⇒ 边框/圆角特征）；
  3. 把结果换算成**比例**（`x/w`, `y/h`），与用户给的截图交叉核对。
用法：./runtime/python/python.exe -u scripts/probe_popup_controls.py [会话名关键字]
"""
import logging
import os
import sys

logging.disable(logging.INFO)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PIL import Image  # noqa: E402
from agent import wechat_windows as WW  # noqa: E402


def main():
    key = sys.argv[1] if len(sys.argv) > 1 else ""
    wins = [w for w in WW.list_wechat_windows() if w["title"].strip()]
    if not wins:
        print("没有可见窗口")
        return 1
    tgt = [w for w in wins if (key and key in w["title"])] or [wins[0]]
    w0 = tgt[0]
    img, meta = WW.grab_window(w0["hwnd"], physical=True)
    if img is None:
        print("抓图失败")
        return 1
    W, H = img.size
    print("窗口 %r  hwnd=%d  逻辑=%s  scale=%.2f  ⇒ 图 %dx%d"
          % (w0["title"], w0["hwnd"], meta.get("logical"), meta.get("scale", 1.0), W, H))

    # ── ① OCR 找「发送」 ────────────────────────────────────────────────
    print("\n① OCR 找「发送」：")
    hits = []
    try:
        from agent import chat_ocr as CO
        # 只看下半部分（发送按钮在右下），减少误检
        bottom = img.crop((0, int(H * 0.70), W, H))
        txt = ""
        try:
            txt = CO.pane_text(bottom, limit=200)
        except Exception:
            txt = ""
        print("   下半部分 OCR 原文 = %r" % (txt or "")[:120])
        if "发送" in (txt or ""):
            hits.append("OCR 命中「发送」✓")
        else:
            hits.append("OCR 未读到「发送」（可能按钮为灰/字太小）")
    except Exception as e:
        hits.append("OCR 不可用：%s" % str(e)[:60])
    for h in hits:
        print("   " + h)

    # ── ② 几何法：底部找"大块浅色矩形 + 边框" ───────────────────────────
    print("\n② 几何法找输入框（底部 80%~100% 区域）：")
    px = img.load()
    y0, y1 = int(H * 0.80), H
    # 逐行统计"接近纯白/浅灰"的连续段，找最长的
    best = None
    for y in range(y0, y1, 3):
        run = 0
        start = None
        for x in range(0, W):
            r, g, b = px[x, y][:3]
            light = (r > 235 and g > 235 and b > 235)
            if light:
                if run == 0:
                    start = x
                run += 1
            else:
                if run > (best[2] if best else 0):
                    best = (y, start, run)
                run = 0
        if run and run > (best[2] if best else 0):
            best = (y, start, run)
    if best:
        y, x0, wpx = best
        print("   最长的浅色横段：y=%d  x=%d..%d  (宽 %d, 比例 x=%.3f~%.3f w, y=%.3f h)"
              % (y, x0, x0 + wpx, wpx, x0 / W, (x0 + wpx) / W, y / H))
        print("   ⇒ 若这段在底部、宽度占 80%%+ ⇒ 很可能就是**输入框内部**")
    else:
        print("   没找到明显的浅色大块")

    # ── ③ 输出比例参考 ─────────────────────────────────────────────────
    print("\n③ 供跨尺寸复用的比例（先按上面实测填，待多帧验证）：")
    print("   · 发送按钮（右下角）：x ≈ 0.88~0.96 w,  y ≈ 0.90~0.96 h")
    print("   · 输入框（底部大框）  ：x ≈ 0.06~0.94 w,  y ≈ 0.85~0.93 h")
    print("   ⚠️ 这些是**待实测校正**的初值；请在多张不同尺寸的独立窗上复核。")

    out = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "_临界样本",
                       "controls-%s.png" % w0["title"][:12].replace(" ", "_"))
    img.save(out)
    print("\n图已存 %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
