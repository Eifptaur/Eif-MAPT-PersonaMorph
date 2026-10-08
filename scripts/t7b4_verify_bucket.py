# -*- coding: utf-8 -*-
"""t7b4_verify_bucket.py —— 验证「行距两档(86/97)」是不是真的？

★ 怀疑：86 档是【调试面板文字污染 x 窗】造成的假象，不是真的第二档行高。
  证据（t7b4-原始框转储.json）：frame-033 的会话名单行 cy =
     252.5 / 447.5 / 643.5 / 738.0
  而 frame-000（标准档）的名单行 cy =
     154.5 / 349.5 / 447.5 / 643.5 / 738.0
  ⇒ **同一批 y 值（252.5/447.5/643.5/738.0）在两帧都出现** ⇒ 同一张 97 网格。
  真正混进来的是 x≈453~480 的**调试面板文字**（h=20~22、行距 85~86）。

本脚本做两件事：
  A. 把「名字列」判据收紧成**结构性的**：x 落在 [pane_left 之前的头像列右侧]
     —— 或更稳的：x 与头像框/左沿的相对位置。
  B. 对 28 帧同时跑「旧判据(170..470)」与「新判据」，全量比行距值域。
"""
import io
import json
import os
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

BASE = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")


def main():
    rows = json.load(io.open(os.path.join(_ROOT, "报告", "ocr瓶颈-20260930",
                                          "t7b3-常量兼容性.json"), encoding="utf-8"))
    dump = json.load(io.open(os.path.join(_ROOT, "报告", "ocr瓶颈-20260930",
                                          "t7b4-原始框转储.json"), encoding="utf-8"))
    # 对转储的那几帧做精确对照
    for d in dump:
        boxes = [tuple(b) for b in d["boxes"]]
        pane = d["pane_left"]
        W, H = d["size"]
        # 旧判据
        old = [b for b in boxes if 170 <= b[1] < 470 and 19 <= b[4] <= 22]
        old_cy = sorted(b[2] + b[4] // 2 for b in old)
        old_steps = [b - a for a, b in zip(old_cy, old_cy[1:]) if 70 <= b - a <= 130]
        # 新判据：只留"紧贴列表左沿(头像列)右侧"的名字 —— 结构性
        #   头像列：pane 左侧的那些方框；名字文字起点在头像右 + 小间隙
        #   这里先用一个"结构"近似：名字框的左端 x 与**同一行内**最左框之差
        #   （会话行有 头像 + 名字；调试面板文字没有头像伴随）
        # 更稳做法：名字列 x 应集中在**一个窄带**里（众数带），调试文字在另一带
        xs = sorted(set(b[1] for b in boxes))
        print("\n" + "=" * 92)
        print("%s / %s  W=%d  pane=%s" % (d["dir"], d["png"], W, pane))
        print("  旧判据 名字行数=%d  cy=%s" % (len(old), old_cy))
        print("  旧判据 行距 steps=%s  中位=%s" % (old_steps,
              statistics.median(old_steps) if old_steps else None))
        # 列出旧判据命中的框，看谁在污染
        print("  旧判据命中明细:")
        for t, x, y, w, h in old:
            tag = "  <== 可疑(调试面板?)" if x > 400 else ""
            print("     x=%4d y=%4d h=%2d  %-24s%s" % (x, y, h, t[:24], tag))
    return 0


if __name__ == "__main__":
    sys.exit(main())
