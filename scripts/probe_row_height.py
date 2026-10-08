# -*- coding: utf-8 -*-
"""probe_row_height.py —— 会话列表「行高」的稳健测法（当帧实测，不硬编码）

⛔ 为什么要单独做（用户纠出来的一个测量错）
  用户的实测结论：「行高不会变；窗口比列表矮时**不压缩、只裁掉**（服务号被截一半）」——
  这是微信的正常行为 ⇒ **行高是个可靠锚**。
  但我第一版测法用的是「相邻行 y 间距的**众数**」⇒ 被污染：
  `chat_ocr.session_rows` 会把**同一会话行里的"消息预览"**也当成一行
  ⇒ 相邻间距里混进"名字↔预览"的**半行距**（实测出现 48/78/81…）
  ⇒ 众数被带偏，误判出"行高 77~80"这个**根本不存在的次簇**。
  用图核对后确认：真实行高 ≈ **95~100**（本机 DPI=144）。

★本脚本的稳健测法
  h_row = 「相邻间距」里**去掉半行**之后的代表值：
    1. 取相邻间距（过滤 30~200 的明显噪声）；
    2. 用**中位数**做尺度 M ⇒ 只保留 `d >= 0.75*M` 的（滤掉"名字↔预览"的半行）；
    3. 取保留下来的**众数**（无众数则取中位）；
    4. **交叉校验**：结果必须落在 [80, 130]（本机 DPI=144 的观测区间），
       否则退回「总跨度 ÷ 间隔数」再校一次；仍不合格 ⇒ 返回 0（调用方退回"按 DPI 估"）。

用法：./runtime/python/python.exe -u scripts/probe_row_height.py
      输出：各帧的行高 + 分布（应**收敛到 95~100**，不再是 77~80 的次簇）
"""
import glob
import json
import os
import sys
from collections import Counter
import logging

log = logging.getLogger("persona-morph")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: 合理的行高区间（本机 DPI=144 实测；不同 DPI 由调用方按 DPI/96 缩放）
H_LO, H_HI = 80, 130


def detect_row_height(img, rows=None):
    """会话列表的**行高**（px）；测不出返回 0。当帧实测、不依赖窗口尺寸。"""
    try:
        if rows is None:
            from agent import chat_ocr as _co
            rows = list(_co.session_rows(img) or [])
        ys = sorted(int(x.get("y_abs") or 0) for x in rows)
        if len(ys) < 3:
            return 0
        gaps = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
        gaps = [d for d in gaps if 30 < d < 200]
        if len(gaps) < 2:
            return 0
        med = sorted(gaps)[len(gaps) // 2]
        big = [d for d in gaps if d >= med * 0.75]     # ⭐ 滤掉"名字↔预览"的半行距
        if not big:
            return 0
        h = Counter(big).most_common(1)[0][0]
        if not (H_LO <= h <= H_HI):
            # 兜底口径：总跨度 ÷ 间隔数
            h2 = int(round((ys[-1] - ys[0]) / float(max(1, len(ys) - 1))))
            h = h2 if (H_LO <= h2 <= H_HI) else 0
        return int(h)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return 0


def main():
    from PIL import Image

    _base = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")
    _dirs = sorted(d for d in glob.glob(os.path.join(_base, "sizes-*")) if os.path.isdir(d))
    _res = []
    for _d in _dirs:
        rows = [json.loads(l) for l in open(os.path.join(_d, "samples.jsonl"), encoding="utf-8")]
        for r in rows:
            if not r.get("png"):
                continue
            p = os.path.join(_d, r["png"])
            if not os.path.isfile(p):
                continue
            try:
                img = Image.open(p).convert("RGB")
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                continue
            h = detect_row_height(img)
            _res.append((img.size, r.get("theme"), h))
    print("=" * 74)
    print("行高（新测法：滤半行 + 区间校验）· 共 %d 帧" % len(_res))
    print("=" * 74)
    c = Counter(x[2] for x in _res if x[2])
    print("行高分布：%s" % dict(sorted(c.items())))
    _ok = [x[2] for x in _res if x[2]]
    print("测不出（返回 0）的帧：%d" % sum(1 for x in _res if not x[2]))
    if _ok:
        _ok.sort()
        print("⇒ 中位 %d · 均值 %.1f · 区间 [%d, %d]"
              % (_ok[len(_ok) // 2], sum(_ok) / float(len(_ok)), _ok[0], _ok[-1]))
    print()
    print("按尺寸抽样：")
    _seen = {}
    for sz, th, h in _res:
        k = "%dx%d" % sz
        if k not in _seen:
            _seen[k] = h
    for k, h in sorted(_seen.items()):
        print("  %-11s ⇒ 行高 %s" % (k, h or "测不出"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
