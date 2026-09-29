# -*- coding: utf-8 -*-
"""**点击效果探针**：点完之后"效果到底发生了没有"。

## 为什么必须有它（这是"点准确"的度量前提）
现在 `wechat_ui.hit()` 点完只回一句"已点击" —— **点击有没有生效没人验证**：
  · 点空了（坐标偏、控件没响应）⇒ 上层以为成功，接着往下走 ⇒ 一步错步步错；
  · 也不会有任何数据告诉我们"哪个图标常点不中"。
⇒ 有了它才谈得上：**点准率**（可测）、失败样本（喂给多模板/学习）、点后自愈（重试/换路）。

## 判据怎么定（避免"把噪声当变化"）
不能拿"两张图不完全一样"当"效果发生了"：光标闪烁、时钟走字、抗锯齿都会让整图哈希不同。
本探针按**块级变化占比**判：把监视区切成 32×32 的块，**逐块比"块内 4×4 灰度"的平均绝对差**
（比 dHash 更适合这块用途：dHash 只看梯度，纯色块无论什么颜色哈希都是 0 ⇒ 蓝底变白底它判不出），
只有**变化块的数量（或面积占比）超过门槛**才算"效果发生了"。

## 三态（写死，不许含糊）
  · `True`  效果发生了（变化超门槛）
  · `False` 看着**没发生**（前后几乎一样）
  · `None`  **判不了**（抓不到画面 / 传不进来前后帧）—— 判不了就如实说判不了，
            绝不当成"成功"（否则账本里的成功率是假的）。
"""
from __future__ import annotations

import logging

log = logging.getLogger("persona-morph")

#: 块大小（像素）：32 ≈ 图标/一行文字量级，够小能定位变化、够大能抗单像素噪声
BLOCK = 32
#: 判定门槛：变化块占比超过它 ⇒ 判"效果发生了"
CHANGE_RATIO = 0.06
#: 单块"变了"的门槛：块内 4×4 灰度的**平均绝对差**（0~255）> 它才算这块变了。
#: ⛔ 这里**不能用 dHash**（第一版就用错了）：dHash 只编码"亮暗梯度"，**纯色块不论什么颜色，
#:    哈希全是 0** ⇒ 一块蓝底变成白底、dHash 距离仍是 0 ⇒ 探针彻底瞎（冒烟测试当场抓到：
#:    "明显变了"的合成帧被判 False）。改用块内灰度 MAD：对颜色/亮度变化敏感，
#:    而 4×4 下采样会平均掉单像素噪声（1px 抖动不会误判成"变了"）。
BLOCK_MIN_DIST = 10
#: 块内描述子的边长（4×4）
BLOCK_DESC = 4


def _desc(sub):
    """块描述子：缩到 4×4 灰度后的 16 个灰度值（列表）。"""
    return list(sub.convert("L").resize((BLOCK_DESC, BLOCK_DESC)).getdata())


def _blocks(img, box=None, size: int = BLOCK):
    """把（监视区里的）图切成块，返回 `{(x,y): 块描述子}`。key 用左上角坐标。"""
    im = img.crop(box) if box else img
    w, h = im.size
    out = {}
    y = 0
    while y < h:
        x = 0
        while x < w:
            sub = im.crop((x, y, min(w, x + size), min(h, y + size)))
            if sub.size[0] >= 4 and sub.size[1] >= 4:
                out[(x, y)] = _desc(sub)
            if x + size >= w:
                break
            x += size
        if y + size >= h:
            break
        y += size
    return out


def probe(before, after, box=None, ratio: float = CHANGE_RATIO,
          min_dist: int = BLOCK_MIN_DIST) -> tuple:
    """比两帧的**监视区**，判"效果发生了没有"。返回 `(verdict, info)`。

    `verdict`：True＝变了（效果发生）/ False＝几乎没变 / **None＝判不了**（缺帧）。
    `box`：监视区（渲染区坐标）；不传＝整帧。
    """
    if before is None or after is None:
        return None, {"reason": "缺前后帧（抓不到画面）⇒ 判不了", "changed": None}
    try:
        b = _blocks(before, box)
        a = _blocks(after, box)
        keys = set(b) & set(a)
        if not keys:
            return None, {"reason": "两次抓到的监视区尺寸不一致 ⇒ 判不了", "changed": None}
        n = len(keys)
        ch = 0
        for k in keys:
            d = _mad_desc(b[k], a[k])
            if d > min_dist:
                ch += 1
        r = ch / float(n)
        if r > ratio:
            return True, {"reason": "监视区有 %.0f%% 的块变了（阈值 %.0f%%）⇒ 效果发生了"
                                    % (100 * r, 100 * ratio), "changed": round(r, 3),
                          "blocks": n}
        return False, {"reason": "监视区只有 %.0f%% 的块变了（阈值 %.0f%%）⇒ 看着没发生"
                                % (100 * r, 100 * ratio), "changed": round(r, 3), "blocks": n}
    except Exception as e:  # noqa: BLE001
        log.warning("点击效果探针异常（按判不了处理）：%s", e)
        return None, {"reason": "探针异常：%s" % str(e)[:80], "changed": None}


def _mad_desc(a, b) -> float:
    """两个块描述子的**平均绝对差**（0~255）。取不到 ⇒ 返回 255（按"变了"处理，宁可多报变化）。"""
    try:
        if not a or not b or len(a) != len(b):
            return 255.0
        return sum(abs(int(x) - int(y)) for x, y in zip(a, b)) / float(len(a))
    except Exception as e:  # noqa: BLE001
        log.debug("比块描述子失败（按最远处理）：%s", e)
        return 255.0
