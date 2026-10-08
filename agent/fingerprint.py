# -*- coding: utf-8 -*-
"""画面状态指纹 —— **"这份状态变了没有"的统一入口**（P0-1）。

**为什么需要这个模块**：项目里原本已有五个各管一摊的 `fingerprint`：

| 模块 | 管什么 |
|---|---|
| `chat_header.fingerprint()` | 会话头**文字带**的像素分箱（判"当前会话是谁"） |
| `compat.fingerprint()` | **环境**十一轴（系统/显示/微信窗口/版本/运行环境…） |
| `uia_probe.fingerprint()` | **UIA 节点树**结构 |
| `version.build_fingerprint()` | **文件集**哈希（判"版本变了没"） |
| `safe_name.session_fingerprint()` | 会话名的短哈希（防不同会话写出同名文件） |

它们回答的是"**某个东西长什么样**"。而"这次操作还算不算数"需要的却是另一件事：
**"同一份画面状态，隔一会儿再看是不是还一样"**。这件事原先没有任何一处统一承担 ——
各调用点分别去比 `pane_left`、比绿带、比包名，**判据散落在调用点里，谁也不知道自己漏了哪一维**。

**必须一起进指纹的五维**（少一维就会出现"状态明明变了、指纹却说没变"）：
1. **画面分区采样** —— 分区块的均值 ＋ 标准差（只看单帧像素或行数会漏内容变化）；
2. **滚动位置** —— 同一块区域滚过一屏，像素完全不同，但尺寸/主题都没变；
3. **窗口尺寸** ＋ **DPI** —— 尺寸或缩放一变，所有像素坐标的含义都变；
4. **UI 主题** —— 深浅主题下同一区域的亮度天生不同（**不是**"状态变了"）；
5. **口径版本号** —— 判据本身改了（如 `pane_left` 的算法换了），**历史指纹就不可比**，
   必须显式让版本号进指纹 ⇒ 版本一变，指纹必然不同，旧缓存自动作废。

**与 P0-4 的关系**：本模块的 `state_fingerprint()` 是 `sender.py` 那两个谓词的底座 ——
「这次改动还算不算数」（强谓词）与「界面能不能跟着更新」（弱谓词）都要先问指纹：
**指纹没变 ⇒ 讨论"更不更新"才有意义；指纹变了 ⇒ 必须回到强谓词重判。**
"""
from __future__ import annotations

import hashlib
import logging

log = logging.getLogger("persona-morph")

#: 判据口径版本。**任何影响指纹取值的算法改动都要 +1**。
#: 为什么把它编进指纹而不是当常量放着：口径变了以后，旧指纹与新指纹**语义不同**，
#: 拿它们比较等于拿两把不同刻度的尺子量同一件东西。版本号进指纹 ⇒ 天然不可比 ⇒ 缓存自动失效。
FINGERPRINT_SCHEMA = 1

#: 采样分区的默认网格（列 × 行）。取 4×4 的理由：
#:   · 更少（2×2）⇒ 局部变化（如某一行文字变了）会被同格平均值抹平 ⇒ 漏报；
#:   · 更多（8×8）⇒ 每格像素太少，抗噪差，**光标闪烁/波纹动画**这类无关变化会误报。
#: 两者都会让"变了没"这个判断不可信，4×4 是本项目实测下最稳的档（见判据的三条验收）。
DEFAULT_GRID = (4, 4)


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _quant_mean_std(img, box) -> tuple:
    """算一个矩形区域的 (均值, 标准差)，各自量化。

    **为什么要量化**：指纹要"同一帧算两次完全相同"，但浮点均值会因累加顺序、
    numpy 版本、SIMD 宽度而在末位漂移 ⇒ 两次调用给出 `63.99999997` 与 `64.00000003`
    两个不同字符串 ⇒ 指纹"同帧不同值"、判据当场假红。
    ⇒ 均值量化到 **1 灰阶**、标准差量化到 **0.5 灰阶**：前者是"看得见的差别"，
     后者比均值粗一档（标准差本身更抖），细于它的差别对"状态变了没"没有意义。
    """
    try:
        region = img.crop(box)
        px = list(region.getdata())
    except Exception:  # noqa: BLE001
        return (-1, -1)
    if not px:
        return (0, 0)
    # 兼容灰度与 RGB(A)
    vals = []
    for p in px:
        if isinstance(p, (tuple, list)):
            # 亮度用 0.299/0.587/0.114 —— 与 PIL `convert` 同系数，
            # 保证截图链（PIL）与本模块算的是同一个"亮度"。
            r = p[0] if len(p) > 0 else 0
            g = p[1] if len(p) > 1 else r
            b = p[2] if len(p) > 2 else r
            vals.append(0.299 * r + 0.587 * g + 0.114 * b)
        else:
            vals.append(float(p))
    n = len(vals)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    std = var ** 0.5
    return (int(round(mean)), int(round(std * 2)))


def sample_regions(img, grid: tuple = DEFAULT_GRID) -> list:
    """把整幅图切成 `grid` 网格，逐格算 (均值, 标准差)。

    返回**按坐标序（先行后列）展平**的列表 —— 顺序固定是判据的一部分：
    若按 `dict` 迭代顺序拼串，同一帧两次调用就可能给出不同字符串（Py 的 dict 保序，
    但分区的**生成顺序**若来自集合/并发收集就不保序）⇒ 显式排序再拼。
    """
    cols, rows = int(grid[0]), int(grid[1])
    try:
        w, h = int(img.size[0]), int(img.size[1])
    except Exception:  # noqa: BLE001
        return []
    if w <= 0 or h <= 0 or cols <= 0 or rows <= 0:
        return []
    out = []
    for ry in range(rows):
        for rx in range(cols):
            x0 = int(w * rx / cols)
            x1 = int(w * (rx + 1) / cols)
            y0 = int(h * ry / rows)
            y1 = int(h * (ry + 1) / rows)
            if x1 <= x0:
                x1 = x0 + 1
            if y1 <= y0:
                y1 = y0 + 1
            m, s = _quant_mean_std(img, (x0, y0, min(x1, w), min(y1, h)))
            out.append((rx, ry, m, s))
    # 显式按 (行, 列) 排序 —— 与上面的生成顺序一致，但**不依赖**它
    out.sort(key=lambda t: (t[1], t[0]))
    return out


def _scroll_sig(scroll_pos) -> int:
    """滚动位置的量化。`None` ⇒ -1（表示"这一维取不到值"，与"滚到 0"区分开）。"""
    if scroll_pos is None:
        return -1
    try:
        return int(round(float(scroll_pos)))
    except Exception:  # noqa: BLE001
        return -1


def state_fingerprint(img, *, scroll_pos=None, theme: str = "", dpi: float = 0.0,
                      grid: tuple = DEFAULT_GRID, schema: int = FINGERPRINT_SCHEMA) -> str:
    """**画面状态指纹**：把"这份状态"压成一个短字符串。

    入参（除 `img` 外都可省，省掉的维度记 `"-"` 并**照常进指纹** ——
    这一点很重要：**"取不到值"本身也是状态的一部分**，若省略它，
    一次"拿得到"与一次"拿不到"会算出同一个指纹 ⇒ 掩掉真实的状态差异）。

    :param img: 任何有 `size` 与 `getdata()` 的类 PIL 图像（本项目抓图链的产物）。
    :param scroll_pos: 纵向滚动位置（像素或行号，量化到整数）。
    :param theme: UI 主题标识（如 `"light"` / `"dark"`）。**必须进指纹**：深浅主题下
                  同一区域的亮度天生不同，若不进指纹，切主题会被误判成"画面变了"。
    :param dpi: 显示缩放系数（`ui_adapt.detect_scale()` 的产物）。尺寸相同的两帧、
                 缩放不同 ⇒ 像素坐标含义不同。
    :param grid: 采样网格 `(列, 行)`。
    :param schema: 口径版本，默认取模块级 `FINGERPRINT_SCHEMA`。

    返回：`"<schema>|<w>x<h>|<theme>|<dpi>|<scroll>|<各区…>"` 的十六进制摘要。

    ⚠️ **返回的是摘要，不是原串**：原串里含画面亮度读数，长度随网格增长（4×4 ⇒ 16 格），
    写日志会把每帧的内容亮度留在盘上 —— 那是"这台机器当时在看什么"的侧信道。
    摘要保留"可比性"，丢掉"可读性"，正是这里需要的。
    """
    parts = []
    try:
        w, h = int(img.size[0]), int(img.size[1])
    except Exception:  # noqa: BLE001
        w = h = 0
    parts.append("s%d" % int(schema))
    parts.append("%dx%d" % (w, h))
    parts.append("t%s" % (str(theme) if theme else "-"))
    # DPI 量化到 0.01：缩放系数本身是比值，1e-6 的抖动不构成"状态变了"
    parts.append("d%s" % (("%.2f" % float(dpi)) if dpi else "-"))
    parts.append("y%d" % _scroll_sig(scroll_pos))
    for rx, ry, m, s in sample_regions(img, grid):
        parts.append("%d.%d:%d:%d" % (rx, ry, m, s))
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def fingerprint_detail(img, **kw) -> dict:
    """与 `state_fingerprint` 同源，但返回**可解释**的结构（排障用）。

    `state_fingerprint` 的摘要是"能不能比"，排障要的是"**哪儿不一样**" ⇒
    把五维各留一份可读读数，并在 `parts` 里给出参与哈希的原始串。
    """
    grid = kw.get("grid", DEFAULT_GRID)
    schema = kw.get("schema", FINGERPRINT_SCHEMA)
    try:
        w, h = int(img.size[0]), int(img.size[1])
    except Exception:  # noqa: BLE001
        w = h = 0
    regions = sample_regions(img, grid)
    return {
        "fingerprint": state_fingerprint(img, **kw),
        "schema": int(schema),
        "size": [w, h],
        "theme": kw.get("theme") or "",
        "dpi": kw.get("dpi") or 0.0,
        "scroll": _scroll_sig(kw.get("scroll_pos")),
        "grid": [int(grid[0]), int(grid[1])],
        "regions": [[rx, ry, m, s] for rx, ry, m, s in regions],
    }


def diff_detail(a: dict, b: dict) -> dict:
    """比对两份 `fingerprint_detail`，指出**是哪一维变了**。

    为什么不让上层自己比：上层最容易犯的错是"只比指纹串，不等就重算一遍全流程" ——
    而实际上**滚动位置变了**只需重新定位、**主题变了**往往什么都不用做。
    给一张"哪一维变了"的清单，上层才能挑**最小**的补救动作。
    """
    out = {"same_fingerprint": a.get("fingerprint") == b.get("fingerprint"), "changed": []}
    for key in ("schema", "size", "theme", "scroll"):
        if a.get(key) != b.get(key):
            out["changed"].append({"axis": key, "old": a.get(key), "new": b.get(key)})
    ra, rb = a.get("regions") or [], b.get("regions") or []
    if len(ra) != len(rb):
        out["changed"].append({"axis": "grid", "old": len(ra), "new": len(rb)})
    else:
        moved = []
        for (rx, ry, m1, s1), (_, _, m2, s2) in zip(ra, rb):
            if m1 != m2 or s1 != s2:
                moved.append([rx, ry, m1, m2, s1, s2])
        if moved:
            out["changed"].append({"axis": "pixels", "cells": moved,
                                   "count": len(moved)})
    # dpi 是浮点，容差 1e-6（与进指纹的量化口径一致，免得"同一缩放"被判成变了）
    try:
        if abs(float(a.get("dpi") or 0) - float(b.get("dpi") or 0)) > 1e-6:
            out["changed"].append({"axis": "dpi", "old": a.get("dpi"), "new": b.get("dpi")})
    except Exception:  # noqa: BLE001
        pass
    return out


__all__ = ["FINGERPRINT_SCHEMA", "DEFAULT_GRID", "sample_regions",
           "state_fingerprint", "fingerprint_detail", "diff_detail"]
