# -*- coding: utf-8 -*-
"""「发文件」图标行的定位探针（2026-09-15，为跨机 P15「点不到」用）。

**默认不动鼠标**：截渲染区 → 找出输入栏那一行的图标中心 → 把「我们代码算的坐标」和「实测中心」
并排打出来，直接看差多少像素、差在哪个轴。
可选 `--click X Y`（**会动真鼠标**，阳性对照用）：在那个点上真鼠标点一枪，唯一判据＝
**#32770「选择文件」对话框有没有出现**（不是"我点了"）。点完把光标还回去。

用法（在待查的那台机器上）：
    runtime\\python\\python.exe _scratch\\file_btn_probe.py
    runtime\\python\\python.exe _scratch\\file_btn_probe.py --click 470 930
"""
import ctypes
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import chat_header as ch                                     # noqa: E402
from agent.input_backend import _lock_dpi, find_main_window, find_render_child, window_rect  # noqa: E402

# 代码里的常量（agent/wechat.py::_file_panel_point）
ICONS_X = {"表情": 43, "收藏": 97, "文件": 151, "截图": 205, "语音": 280}
ICON_ROW_UP = 50          # y = 渲染底 − 50


def find_dialog(timeout=3.0):
    """找「选择文件」对话框（#32770）。"""
    import win32gui
    dl = time.time() + timeout
    while time.time() < dl:
        hits = []

        def cb(h, _):
            try:
                if win32gui.GetClassName(h) == "#32770" and win32gui.IsWindowVisible(h):
                    t = win32gui.GetWindowText(h)
                    if ("选择文件" in t) or ("打开" in t):
                        hits.append((h, t))
            except Exception:
                pass
        win32gui.EnumWindows(cb, None)
        if hits:
            return hits[0]
        time.sleep(0.2)
    return (0, "")


def row_clusters(img, y, gray=140, gap=24):
    """扫**一行**，把有暗像素的列按 gap 分组 ⇒ [(中心x, 跨度, 暗像素数)]。"""
    g = img.convert("L")
    w = g.size[0]
    px = g.load()
    groups, cur, cnt = [], [], 0
    for x in range(w):
        if px[x, y] < gray:
            if cur and x - cur[-1] > gap:
                groups.append((cur, cnt))
                cur, cnt = [], 0
            cur.append(x)
            cnt += 1
    if cur:
        groups.append((cur, cnt))
    return [(int((c[0] + c[-1]) / 2), c[-1] - c[0] + 1, n) for c, n in groups]


def main():
    args = sys.argv[1:]
    click_pt = None
    if "--click" in args:
        i = args.index("--click")
        click_pt = (int(args[i + 1]), int(args[i + 2]))

    print("DPI 锁：", _lock_dpi())
    print("进程所见屏幕：", ctypes.windll.user32.GetSystemMetrics(0), "x", ctypes.windll.user32.GetSystemMetrics(1))
    hwnd = find_main_window()
    print("主窗 hwnd =", hwnd, "rect =", window_rect(hwnd))
    rc = find_render_child(hwnd)
    print("渲染子窗 =", rc, "rect =", window_rect(rc) if rc else None)

    from agent.wechat import WeChatAdapter
    ad = WeChatAdapter()
    gui = ad._get_gui()
    try:
        gui._update_render_rect()
    except Exception:
        pass
    r = tuple(gui.render_rect)
    print("渲染区（屏幕坐标）= %s  尺寸 %dx%d" % (r, r[2] - r[0], r[3] - r[1]))

    img = ch.capture_image(gui=gui)
    u32 = ctypes.windll.user32
    iconic = bool(u32.IsIconic(ctypes.c_void_p(int(hwnd)))) if hwnd else True
    if img is None:
        print("抓不到渲染区画面（窗口不可见/被遮挡）⇒ 后面的定位跳过")
    elif iconic or not u32.IsWindowVisible(ctypes.c_void_p(int(hwnd))):
        # ⚠️ 2026-09-15 本机首跑就撞上：微信**最小化**时 PrintWindow 拿到的是"假帧"
        #    （底部一片 250 的纯色），拿它找图标行必然找不到 —— 不是坐标错，是画面不可信。
        print("⚠️ 微信主窗当前**最小化/不可见**（IsIconic=%s）⇒ 抓到的画面不可信，定位结果一律不采信。"
              % iconic)
        print("   请先把微信窗口恢复正常（不要最小化、也别被别的窗口完全盖住）再跑这个探针。")
    else:
        w, h = img.size
        print("抓到渲染区画面 %dx%d" % (w, h))
        pane = ch.detect_pane_left(img)
        print("检测到的聊天面板左沿 =", pane, "（代码兜底值会用它算 x）")
        band_top = max(0, h - 200)
        # ⚠️ 别拿"整行暗像素最多"当图标行（2026-09-15 首跑就踩了）：图像最底下常有一条窗口边线，
        #    整行全暗 ⇒ 一定赢。图标行的特征是「一行里有很多**小簇**」⇒ 用"宽度 4~60px 的簇个数"评分，
        #    并且跳过最底下 15 行。
        scored = []
        for y in range(band_top, h):
            if y > h - 15:
                continue
            cl = row_clusters(img, y)
            good = [c for c in cl if 4 <= c[1] <= 60]
            scored.append((len(good), y, cl))
        scored.sort(key=lambda t: (-t[0], -t[1]))
        print("\n[底部 200px：按「小簇个数」排序的前 5 行]（换算成「渲染底往上多少 px」）")
        for n, y, _cl in scored[:5]:
            print("   渲染底往上 %3d px（y_abs=%4d）· 宽度 4~60px 的簇 %d 个" % (h - y, y, n))
        if scored and scored[0][0] >= 3:
            n, best, cl = scored[0]
            good = [c for c in cl if 4 <= c[1] <= 60]
            print("\n[该行按列聚类]（渲染区相对 x；间距>24px 算新簇）宽度 4~60px 的簇 %d 个" % len(good))
            for cx, span, ndark in good[:14]:
                print("   x=%4d  跨度=%3d  含暗像素列=%d" % (cx, span, ndark))
            if pane:
                print("\n[我们代码算的坐标 vs 实测]（都以渲染区左上为原点）")
                for name, off in ICONS_X.items():
                    want = pane + off
                    near = sorted(good, key=lambda c: abs(c[0] - want))[:1]
                    got = near[0][0] if near else -1
                    print("   %-4s 期望 x=%4d（pane %d + %d）｜最近实测簇 x=%4d ⇒ 偏差 %+d"
                          % (name, want, pane, off, got, got - want))
                print("   y：代码取 渲染底往上 %d px；上面那几行就是实测候选 ⇒ 偏差自己比一眼" % ICON_ROW_UP)
        else:
            print("   ⚠️ 这一带没找到「多个小簇」的行 ⇒ 输入栏图标行可能不在渲染区底部 200px 内，"
                  "或渲染区抓到的内容不是期望的那部分")
        # 把抓到的画面与底部一带的统计存下来：**定位失败时这张图就是最重要的证据**
        try:
            from PIL import ImageStat
            outdir = os.path.join(ROOT, "报告")
            os.makedirs(outdir, exist_ok=True)
            png = os.path.join(outdir, "file_btn_probe-%s.png" % time.strftime("%Y%m%d-%H%M%S"))
            img.save(png)
            band2 = img.crop((0, max(0, h - 120), w, h)).convert("L")
            st = ImageStat.Stat(band2)
            print("   画面已存：%s" % png)
            print("   底部 120px 统计：mean=%.1f std=%.1f（std<3 ⇒ 整片纯色 ⇒ 这不是当前画面/图标不在这里）"
                  % (st.mean[0], st.stddev[0]))
        except Exception as e:
            print("   存图失败：%s" % e)

    if click_pt:
        sx, sy = click_pt
        print("\n=== 真鼠标阳性对照（会动光标！）点 (%d, %d) ===" % (sx, sy))
        u = ctypes.windll.user32
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        old = POINT()
        u.GetCursorPos(ctypes.byref(old))
        from agent.input_backend import select_backend
        be = select_backend({"input": {"backend": "real"}}, gui=gui)
        okc, why = be.click(hwnd, (sx, sy))
        print("真鼠标点击返回：", okc, why)
        dlg, title = find_dialog(3.0)
        print("3 秒内「选择文件」对话框：", ("出现了 hwnd=%s title=%s" % (dlg, title)) if dlg else "**没有出现**")
        u.SetCursorPos(old.x, old.y)          # 用完还回去
        print("光标已还原到 (%d, %d)" % (old.x, old.y))

    try:
        from agent import window_borrow as _wb
        _wb.restore("探针结束")               # 别把借来的窗口留着
    except Exception:
        pass


if __name__ == "__main__":
    main()
