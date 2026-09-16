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
RUN_TOL = 0.35          # "规整的一跳"：间距相对参考值的偏差上限
#   ⚠️ 2026-09-16 r8 跨机实测后定在 0.35（不是 0.5）：那台机器的间距是 45/45/44/**63**（🎤语音比前面
#   宽 40%），而**开头**那一跳异常（247→312＝65px）必须被排除 ⇒ 0.5 会把 247 也收进来、导致
#   "取第 3 个"错到 📦收藏。现在的做法是：规整的一跳用 0.35 判，**最后一跳**允许异常（单独一条规则），
#   既排除开头杂簇、又能让语音那一枚出现在"落点自证"的整排里。
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
    """从该行的簇里挑**工具栏那一组**：一排"开头每一跳都规整、只允许**最后一跳**异常"的簇。

    ⚠️ 2026-09-16 r8 跨机实测后**重写**（对面报的第 1 条：产品 5 簇 / 探针 6 簇）：
    · **不再拿 `pane_left` 当硬门槛**——产品和探针各自检测 pane_left，值不一样就各测各的
      （探针多收了 pane 左侧的 247 那一簇 ⇒ 它取第 3 个变成 357＝📦收藏 ✗）。参数留着只为兼容，
      **不再参与判定**。
    · **允许"最后一跳"异常**：工具栏最后一枚是 🎤语音，实测比前面宽 40%（间距 45/45/44/**63**）。
      但"**开头**就异常"说明那一簇不属于这排（247→312 那一跳 65px）⇒ 必须排除。
    · 参考间距 `med` 取**该行间距的下半段中位数**（不被尾部异常和远处杂簇带偏）。
    · 多个候选段等长时取**最靠左**的那段（工具栏是聊天面板里最左的一组）。
    """
    cand = sorted(clusters, key=lambda c: c[0])
    if len(cand) < RUN_MIN:
        return []
    allgaps = [cand[k + 1][0] - cand[k][0] for k in range(len(cand) - 1)]
    half = sorted(allgaps)[:max(1, len(allgaps) // 2)]
    med = half[len(half) // 2] if half else 0
    if not med:
        return []
    best = []
    for i in range(len(cand)):
        run = [cand[i]]
        for j in range(i + 1, len(cand)):
            g = cand[j][0] - run[-1][0]
            if abs(g - med) <= tol * med:
                run.append(cand[j])                    # 规整的一跳：继续
                continue
            if len(run) >= RUN_MIN:
                run.append(cand[j])                    # 异常但已成一排 ⇒ 只许它当"最后一跳"
            break
        if len(run) > len(best) or (len(run) == len(best) and best and run[0][0] < best[0][0]):
            best = run
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
