# -*- coding: utf-8 -*-
"""probe_normalized_profile.py —— 用真实多尺寸样本裁决「归一化剖面」能否跨尺寸复用

⛔ 要回答的问题（调研报告 §1 的核心主张）
  报告 §1 说：把"每箱宽 = w/64 的绝对像素剖面"改成"按面板宽度等分的**相对坐标**分箱"，
  再配合带子的相对定位，就能让**一条参照跨尺寸复用**，从而不再"每个尺寸一个参照"。
  我方任务书 §A 也提了同一件事。

  本脚本用**真实采集数据**（`scripts/collect_size_samples.py` 产出：同会话 × 多种窗口尺寸）
  直接量三组数：
    · 同会话 **跨尺寸** 相似度（归一化定位 vs 现状绝对像素）  ← 越高越好，目标 ≥0.90
    · **不同会话**（同一尺寸）相似度                            ← 越低越好，目标 <0.80
    · 正负样本间隔 = 前者 − 后者                                ← 有工程余量才敢上线

用法
  ./runtime/python/python.exe -u scripts/probe_normalized_profile.py
  （默认取 `报告/ocr瓶颈-*/多尺寸样本/` 下最新的一个采集目录）
"""
import glob
import json
import os
import sys
import logging

log = logging.getLogger("persona-morph")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_DARK = 165
_BINS = 64
_HR = chr(0x2500)


def _profile(img, box):
    """box 区域内、**固定 64 箱**的墨点密度剖面（箱宽 = 框宽/64 ⇒ 天然是相对分箱）。"""
    g = img.convert("L").crop(box)
    w, h = g.size
    if w < _BINS or h < 4:
        return []
    px = g.load()
    step = max(1, h // 24)
    out = []
    for i in range(_BINS):
        x0, x1 = int(i * w / _BINS), max(int(i * w / _BINS) + 1, int((i + 1) * w / _BINS))
        dark = tot = 0
        for x in range(x0, x1):
            for y in range(0, h, step):
                tot += 1
                if px[x, y] < _DARK:
                    dark += 1
        out.append(dark / float(max(1, tot)))
    return out


def _norm(p):
    mx = max(p) if p else 0.0
    return [int(round(255.0 * v / mx)) for v in p] if mx > 0 else []


def _sim(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    return max(0.0, 1.0 - sum(abs(x - y) for x, y in zip(a, b)) / float(len(a)) / 255.0)


def _clean(name):
    """把读数清洗成"像会话名"的标识（去头像噪声/URL/空）。"""
    s = str(name or "").strip()
    if not s or len(s) < 2:
        return ""
    if s.startswith("http") or "." in s and "/" in s:
        return ""
    for ch in "O0《》〉，,．.、:：]|":
        s = s.replace(ch, "")
    # 归一化常见 OCR 错字
    s = s.replace("文亻牛", "文件").replace("亻牛", "件")
    return s.strip()


def main():
    _ds = sorted(glob.glob(os.path.join(_ROOT, "报告", "ocr瓶颈-*", "多尺寸样本", "*")))
    if not _ds:
        print("⛔ 没有采集目录（先跑 scripts/collect_size_samples.py）")
        return 2
    _d = _ds[-1]
    print(_HR * 74)
    print("归一化剖面跨尺寸裁决 · 数据 = %s" % os.path.basename(_d))
    print(_HR * 74)

    from PIL import Image
    _rows = [json.loads(l) for l in open(os.path.join(_d, "samples.jsonl"), encoding="utf-8")]
    _items = []
    for _r in _rows:
        _png = _r.get("png")
        if not _png or not _r.get("w"):
            continue
        _p = os.path.join(_d, _png)
        if not os.path.isfile(_p):
            continue
        _name = _clean(_r.get("title")) or _clean(_r.get("hl_name"))
        if not _name:
            continue
        _items.append({"i": _r["i"], "w": _r["w"], "h": _r["h"], "name": _name,
                       "pl": _r.get("pane_left") or 0, "png": _p})
    print("▸ 可用帧（标题或绿底行能清洗出会话名）：**%d**" % len(_items))
    if len(_items) < 4:
        print("⛔ 太少，无法裁决（如实退出，不冒充结论）")
        return 1

    _by_name = {}
    for _it in _items:
        _by_name.setdefault(_it["name"], []).append(_it)
    print("▸ 按会话分组：")
    for _n, _g in sorted(_by_name.items(), key=lambda kv: -len(kv[1])):
        _sz = sorted({"%dx%d" % (x["w"], x["h"]) for x in _g})
        print("    %-22s %2d 帧 · %d 种尺寸 %s" % (_n[:22], len(_g), len(_sz), _sz[:4]))

    # ── 算两种定位下的剖面 ──
    def _boxes(it):
        """返回 {'abs': 现状绝对像素带子, 'rel': 按面板左沿+图高比例的归一化带子}"""
        img = Image.open(it["png"]).convert("RGB")
        w, h, pl = img.size[0], img.size[1], (it["pl"] or int(img.size[0] * 0.263))
        _abs = (pl + 10, 38, min(w, pl + 10 + 340), 38 + 56)          # = chat_header.BAND_PX
        _rel = (max(0, pl - 12), int(h * 0.045),
                min(w, pl + int(h * 0.42)), int(h * 0.098))
        return img, _abs, _rel

    _profs = {}
    for _it in _items:
        try:
            img, _a, _r = _boxes(_it)
            _profs[_it["i"]] = (_norm(_profile(img, _a)), _norm(_profile(img, _r)))
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            continue

    # ── 同会话跨尺寸（正样本）──
    _pos = {"abs": [], "rel": []}
    for _n, _g in _by_name.items():
        for _x in range(len(_g)):
            for _y in range(_x + 1, len(_g)):
                _a, _b = _g[_x], _g[_y]
                if _a["w"] == _b["w"] and _a["h"] == _b["h"]:
                    continue                    # 只在"跨尺寸"上量
                if _a["i"] not in _profs or _b["i"] not in _profs:
                    continue
                for _k, _idx in (("abs", 0), ("rel", 1)):
                    _s = _sim(_profs[_a["i"]][_idx], _profs[_b["i"]][_idx])
                    if _s > 0:
                        _pos[_k].append(_s)

    # ── 不同会话同尺寸（负样本）──
    _neg = {"abs": [], "rel": []}
    _ns = list(_by_name.items())
    for _x in range(len(_ns)):
        for _y in range(_x + 1, len(_ns)):
            _, _g1 = _ns[_x]
            _, _g2 = _ns[_y]
            for _a in _g1:
                for _b in _g2:
                    if _a["w"] != _b["w"] or _a["h"] != _b["h"]:
                        continue
                    if _a["i"] not in _profs or _b["i"] not in _profs:
                        continue
                    for _k, _idx in (("abs", 0), ("rel", 1)):
                        _s = _sim(_profs[_a["i"]][_idx], _profs[_b["i"]][_idx])
                        if _s > 0:
                            _neg[_k].append(_s)

    def _stat(v):
        if not v:
            return "无样本"
        v = sorted(v)
        return "n=%-4d min=%.3f 中位=%.3f max=%.3f 均值=%.3f" % (
            len(v), v[0], v[len(v) // 2], v[-1], sum(v) / len(v))

    print()
    print("── 同会话 · **跨尺寸** 剖面相似度（正样本，目标 ≥0.90）──")
    print("   现状（绝对像素 BAND_PX）: %s" % _stat(_pos["abs"]))
    print("   归一化（左沿+图高比例）  : %s" % _stat(_pos["rel"]))
    print()
    print("── 不同会话 · 同尺寸 剖面相似度（负样本，目标 <0.80）──")
    print("   现状（绝对像素 BAND_PX）: %s" % _stat(_neg["abs"]))
    print("   归一化（左沿+图高比例）  : %s" % _stat(_neg["rel"]))
    print()
    _pa = (sum(_pos["abs"]) / len(_pos["abs"])) if _pos["abs"] else 0.0
    _pr = (sum(_pos["rel"]) / len(_pos["rel"])) if _pos["rel"] else 0.0
    _na = (sum(_neg["abs"]) / len(_neg["abs"])) if _neg["abs"] else 0.0
    _nr = (sum(_neg["rel"]) / len(_neg["rel"])) if _neg["rel"] else 0.0
    print("── 裁决 ──")
    print("   现状   正 %.3f · 负 %.3f ⇒ 间隔 %+.3f" % (_pa, _na, _pa - _na))
    print("   归一化 正 %.3f · 负 %.3f ⇒ 间隔 %+.3f" % (_pr, _nr, _pr - _nr))
    _verdict = ("归一化显著更优 ⇒ **方向A 成立**，可把参照从'每尺寸一档'改成'一会话一参照'"
                if (_pr - _nr) > (_pa - _na) + 0.02 else
                "归一化**没有显著改善**（或仍低于 0.90）⇒ 按报告 §5 的 E（重采样）或改走'文字判据'（本批数据里标题 OCR 已能读出会话名）")
    print("   ⇒ %s" % _verdict)
    print(_HR * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
