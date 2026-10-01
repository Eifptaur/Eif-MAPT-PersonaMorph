# -*- coding: utf-8 -*-
"""probe_layout_state.py —— 验证 T10「布局自检」（list_visible ⇔ 有绿底行）

决定性验证：用户给的两张同机"临界点"对照图（总宽只差 1px，一张隐藏一张显示）。
全量复查：130 帧的 list_visible 分布 + 绿带占比区间（应对齐"会话列表宽度 0.168~0.407"）。
"""
import collections
import glob
import io
import json
import logging
import os
import sys

logging.disable(logging.INFO)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PIL import Image  # noqa: E402
from agent import chat_header as CH  # noqa: E402


def main():
    print("=" * 74)
    print("决定性验证：用户两张临界图（总宽 875 vs 876，状态相反）")
    print("=" * 74)
    ok = bad = 0
    cases = [
        (os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "_临界样本", "hidden-873x901.png"),
         False, "列表隐藏"),
        (os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "_临界样本", "shown-1097x901.png"),
         True, "列表显示"),
    ]
    for f, want, tag in cases:
        if not os.path.isfile(f):
            print("  ⛔ 缺文件：%s" % f)
            continue
        st = CH.layout_state(Image.open(f).convert("RGB"))
        r = st["list_visible"] is want
        ok += int(r)
        bad += int(not r)
        print("  %s  期望 list_visible=%-5s 实得=%-5s  绿底=%-5s  | %s"
              % ("PASS" if r else "FAIL", want, st["list_visible"], st["green_right"] or "-", tag))
    print("  ⇒ 用户临界图：%d PASS / %d FAIL" % (ok, bad))

    print()
    print("=" * 74)
    print("全量复查：130 帧")
    print("=" * 74)
    cnt = collections.Counter()
    ratios, hidden_ws, shown_ws = [], [], []
    for d in sorted(glob.glob(os.path.join(_ROOT, "报告", "ocr瓶颈-20260930",
                                          "多尺寸样本", "sizes-*"))):
        rows = [json.loads(l) for l in io.open(os.path.join(d, "samples.jsonl"),
                                              encoding="utf-8").read().split("\n") if l.strip()]
        for r in rows:
            if not r.get("png"):
                continue
            p = os.path.join(d, r["png"])
            if not os.path.isfile(p):
                continue
            img = Image.open(p).convert("RGB")
            w = img.size[0]
            st = CH.layout_state(img)
            cnt[st["list_visible"]] += 1
            (shown_ws if st["list_visible"] else hidden_ws).append(w)
            if st["green_right"]:
                ratios.append(st["green_right"] / float(w))
    print("  list_visible：True=%d  False=%d" % (cnt[True], cnt[False]))
    if hidden_ws:
        print("  判为【不可见】的宽度：%s" % sorted(set(hidden_ws)))
    if shown_ws:
        print("  判为【可见】的宽度范围：%d ~ %d" % (min(shown_ws), max(shown_ws)))
    if ratios:
        ratios.sort()
        print("  绿底占比区间：%.3f ~ %.3f（应落在会话列表宽度 0.168~0.407 内）"
              % (ratios[0], ratios[-1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
