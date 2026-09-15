# -*- coding: utf-8 -*-
r"""「只截底图」工具（2026-09-16，跨机 r11 需求）：**只读**，不点、不发、不动鼠标。

用途：把渲染区**底部那一带**（输入栏图标行）截下来，并把每个图标**单独裁一张**存盘 ——
这些就是"图标身份定位（模板匹配）"要用的模板；同时打印共用实现算出来的行位置与各簇坐标，
方便与对面那台机器对照（同一屏两边应当给出同一组数字）。

用法（在待查的那台机器上）：
    runtime\\python\\python.exe scripts\\icon_bottom_shot.py
产物：`报告\iconrow-<时间戳>\row.png`（整条图标行）＋ `icon-1.png … icon-N.png`（逐个图标）
      ＋ `row.txt`（坐标/间距/说明，纯文本，便于原样发回）
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import chat_header as ch          # noqa: E402
from agent import input_bar as ib            # noqa: E402


def main():
    from agent.wechat import WeChatAdapter
    ad = WeChatAdapter()
    gui = ad._get_gui()
    try:
        gui._update_render_rect()
    except Exception:
        pass
    r = tuple(gui.render_rect or (0, 0, 0, 0))
    print("渲染区（屏幕坐标）= %s" % (r,))
    img = ch.capture_image(gui=gui)
    if img is None:
        print("抓不到渲染区画面 ⇒ 最小化/被遮挡时 PrintWindow 会失效（这正是要换 WGC 的场景）")
        return
    g = img.convert("L")
    w, h = g.size
    y_abs, cl, total = ib.best_row(g)
    run = ib.toolbar_run(cl, pane_left=0)
    pt, why = ib.file_point(g, r, pane_left=0)
    print("画面 %dx%d · 该行簇数 %d · 工具栏那组 %d 簇：%s"
          % (w, h, total, len(run), "/".join(str(c[0]) for c in run)))
    print("「文件」落点（屏幕坐标）= %s" % (pt,))
    print("说明：%s" % why)
    if not run:
        print("⚠️ 没认出工具栏那排 ⇒ 大概率不在聊天视图（或在滚动中）；先把会话点开再跑")
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = os.path.join(ROOT, "报告", "iconrow-%s" % stamp)
    os.makedirs(outdir, exist_ok=True)
    top = max(0, int(y_abs) - 14)
    bot = min(h, int(y_abs) + 15)
    strip = img.crop((0, top, w, bot))
    strip.save(os.path.join(outdir, "row.png"))
    lines = ["渲染区(屏幕坐标)=%s  画面=%dx%d" % (r, w, h),
             "该行簇数=%d 工具栏那组=%d 簇 %s" % (total, len(run), [c[0] for c in run]),
             "「文件」落点(屏幕坐标)=%s" % (pt,), "说明=%s" % why, ""]
    for i, c in enumerate(run, 1):
        cx, span = c[0], c[1]
        x0, x1 = max(0, cx - 16), min(w, cx + 17)
        crop = img.crop((x0, top, x1, bot))
        p = os.path.join(outdir, "icon-%d.png" % i)
        crop.save(p)
        lines.append("icon-%d: 中心x=%d 宽=%d 裁图=%s" % (i, cx, span, os.path.basename(p)))
    with open(os.path.join(outdir, "row.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n产物目录：%s" % outdir)
    print("（把整个目录打包发回即可：row.png + 每个 icon-*.png + row.txt；**只读操作，未点未发**）")
    try:
        from agent import window_borrow as wb
        wb.restore("探针结束")
    except Exception:
        pass


if __name__ == "__main__":
    main()
