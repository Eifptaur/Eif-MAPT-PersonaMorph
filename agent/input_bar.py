# -*- coding: utf-8 -*-
"""输入栏图标行的**唯一实现**（产品与探针共用）—— 2026-09-16 立。

为什么必须只有一份（跨机 r7 实测，用户看到的后果＝「点到截图」）：
**同一台机器、同一屏**，产品内部数出 **4 簇**（漏了最左边那个 😊）、探针数出 **5 簇**
（312/357/402/446/509，间距 45/45/44/63）⇒ 产品按"第 3 簇"取，取到的是 ✂️**截图**（x≈447），
而真正的 📁文件 在 x=402 —— **全档错位，不是偶发**。两份实现、两套阈值、两个扫描窗口，
就会各测各的。
⇒ 规矩：**识别输入栏图标行/工具栏簇的代码只许有一份**（本模块）；产品（`agent/wechat.py`）与
跨机探针（`scripts/file_btn_probe.py`）都必须 import 它；判据里也钉住"探针不许自带行聚类"。

另一条 r7 实测教训：**簇要允许落在"面板左沿"左边一点**（`PANE_SLACK`）。原来产品把下界卡在
`pane_left + 10`，而第一枚图标紧贴/略左于检测到的 pane_left ⇒ **被筛掉** ⇒ 后面整体左移一档。
"""
from __future__ import annotations

from PIL import ImageStat                                        # noqa: F401  (探针也用它判假帧)

GRAY_THR = 140          # 灰度阈值：低于它算"暗像素"（图标线条）
GAP = 24                # 相邻暗列间隔超过它 ⇒ 算新的一簇
W_MIN, W_MAX = 4, 60    # 簇宽度在这个区间才算"小图标"（整行全暗的窗口边线会被排除）
BAND_PX = 200           # 只看渲染区底部这么多像素（图标行就在这一带）
PANE_SLACK = 60         # 允许簇落在 pane_left 左边多少
RUN_TOL = 0.5           # "等间距的一排"：相邻间距相对中位间距的偏差上限
#   ⚠️ 为什么是 0.5 而不是 0.35（2026-09-16 r7 跨机实测的实数）：那台机器的间距是
#   **45/45/44/63**——最后一枚（🎤语音）比前面宽 40%。按 0.35 会把语音切出去（工具栏只认出 4 簇）；
#   虽然"取第 3 个"的答案不受影响，但**落点自证要能看到整排**，所以放宽到 0.5。
RUN_MIN = 3             # 一排至少这么多簇才算工具栏


def row_clusters(gray, y: int, gray_thr: int = GRAY_THR, gap: int = GAP):
    """扫**一行** ⇒ `[(中心x, 宽度, 暗像素数)]`（不筛宽度；筛宽度是 `narrow()` 的事）。"""
    w = gray.size[0]
    px = gray.load()
    groups, cur = [], []
    for x in range(w):
        if px[x, y] < gray_thr:
            if cur and x - cur[-1] > gap:
                groups.append(cur)
                cur = []
            cur.append(x)
    if cur:
        groups.append(cur)
    return [(int((g[0] + g[-1]) / 2), g[-1] - g[0] + 1, len(g)) for g in groups]


def narrow(clusters):
    """只留"小图标尺寸"的簇（宽度 4~60px）。"""
    return [c for c in clusters if W_MIN <= c[1] <= W_MAX]


def best_row(gray, band_px: int = BAND_PX, gray_thr: int = GRAY_THR, gap: int = GAP):
    """在渲染区**底部 `band_px`** 里挑"窄簇最多"的那一行 ⇒ `(y_abs, [簇…], 该行簇总数)`。

    ⚠️ 判据是"**一行里多个窄簇**"，不是"整行暗像素最多"：图像最底下常有一条窗口边线，
    整行全暗必然赢（2026-09-15 首跑就踩了）。
    """
    h = gray.size[1]
    best = (0, 0, (), 0)
    for y in range(max(0, h - band_px), h):
        allc = row_clusters(gray, y, gray_thr, gap)
        nc = narrow(allc)
        if len(nc) > best[0]:
            best = (len(nc), y, tuple(nc), len(allc))
    n, y, cl, total = best
    return (y, list(cl), total) if n >= RUN_MIN else (0, [], total)


def toolbar_run(clusters, pane_left: int = 0, tol: float = RUN_TOL):
    """从该行的簇里挑**工具栏那一组**：面板左沿（允许左溢 `PANE_SLACK`）右边，
    相邻间距近等距的最长连续段（≥`RUN_MIN` 个）⇒ 返回该段 `[(中心x, 宽度)]`。

    为什么按"等间距"挑：整行可能有 9 个簇（工具栏 5 个 + 别的元素），中间的断口（实测最后一枚
    与前面间距 63 vs 45）就是天然分界；按间距断，比按像素上界断稳。
    """
    cand = [c for c in clusters if c[0] >= int(pane_left or 0) - PANE_SLACK]
    if len(cand) < RUN_MIN:
        return []
    best = []
    i = 0
    while i < len(cand):
        run = [cand[i]]
        j = i + 1
        while j < len(cand):
            gaps = [run[k + 1][0] - run[k][0] for k in range(len(run) - 1)]
            med = sorted(gaps)[len(gaps) // 2] if gaps else 0
            g = cand[j][0] - run[-1][0]
            if med and abs(g - med) > tol * med:
                break
            run.append(cand[j])
            j += 1
        if len(run) > len(best):
            best = run
        i = j
    return best if len(best) >= RUN_MIN else []


def file_point(gray, render_rect, pane_left: int = 0, band_px: int = BAND_PX):
    """定位「文件」图标（工具栏第 3 个）⇒ `((x_screen, y_screen), 说明)`；拿不到 ⇒ `(None, 说明)`。

    说明里**把该行的簇全列出来**（第 1..N 个的 x/宽 + 工具栏那组 + 间距 + 底往上多少）——
    这是 r7 跨机测出来的硬需求：「**落点自证**」：漏了第 1 簇这种事不许再靠用户目击发现。
    """
    r = render_rect or (0, 0, 0, 0)
    y_abs, cl, total = best_row(gray, band_px=band_px)
    if not y_abs or not cl:
        return None, "底部 %dpx 里没找到成排的小簇（不是聊天视图？或画面不可信）" % band_px
    run = toolbar_run(cl, pane_left=pane_left)
    if len(run) < RUN_MIN:
        return None, ("该行 %d 簇但挑不出等间距的工具栏一组（%s）"
                      % (total, "/".join(str(c[0]) for c in cl[:10])))
    if len(run) < 3:
        return None, ("工具栏只认出 %d 簇（%s）⇒ 不敢猜第 3 个"
                      % (len(run), "/".join(str(c[0]) for c in run)))
    gaps = [run[k + 1][0] - run[k][0] for k in range(len(run) - 1)]
    med = sorted(gaps)[len(gaps) // 2] if gaps else 0
    x = int(r[0]) + int(run[2][0])
    y = int(r[1]) + int(y_abs)
    why = ("实测第 3 簇｜该行 %d 簇→工具栏 %d 簇 %s · 间距≈%dpx · 底往上 %dpx"
           % (total, len(run), "/".join(str(c[0]) for c in run), med, int(r[3] - r[1]) - int(y_abs)))
    return (x, y), why
