# -*- coding: utf-8 -*-
"""自测：agent/row_geometry.py —— 用真实样本真调用一次，验证"绿带尺"落地。

★ 技能纪律：新模块不靠 ast.parse 收工，必须**真调用**。
★ 技能纪律：报"极差/一致"前，把**原始样本**一起打出来。
"""
import io
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# UTF-8 垫片：本脚本会打印 ✔/✘（`main()` 里的 ck）。输出被重定向到 GBK 控制台/文件时，
# 这两个字符直接抛 UnicodeEncodeError ⇒ **整条自检崩在第一个断言上**，后面的断言从未跑到过。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: # noqa: BLE001
    pass

BASE = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")


def main():
    from PIL import Image
    from agent import row_geometry as rg

    _ck = {"pass": 0, "fail": 0}

    def ck(label, ok, extra=""):
        # ⚠️ 项目自测契约：必须打 `N 通过 / M 失败` 汇总行，
        #    否则 `run_all_selftests.py` 判"无汇总行 ⇒ 可能被中途吃掉" ⇒ 假红。
        _ck["pass" if ok else "fail"] += 1
        tail = ("  [%s]" % (extra,)) if extra != "" else ""
        print("  %s %s%s" % ("✔" if ok else "✘", label, tail))
        return ok

    # ── 0. 类完整性锚（防"0 缩进插进 class"那类静默事故）──
    for m in ("measure", "row_h_from_green", "green_band", "name_column"):
        ck("模块级函数存在: %s" % m, hasattr(rg, m))
    for a in ("row_h", "source", "row_center", "row_span", "to_mouse", "ok"):
        ck("RowGeometry 属性存在: %s" % a, hasattr(rg.RowGeometry, a))

    # ── 1. RowGeometry 的派生量（纯逻辑，不用图）──
    g = rg.RowGeometry(row_h=97, source="green")
    ck("row_h=97 ⇒ ok", g.ok is True)
    ck("row_center(0)=48.5", g.row_center(0) == 48.5, g.row_center(0))
    ck("row_span(2)==(194,291)", g.row_span(2) == (194.0, 291.0), g.row_span(2))
    g0 = rg.RowGeometry(row_h=None, source="none")
    ck("row_h=None ⇒ not ok", g0.ok is False)
    try:
        g0.row_center(0)
        ck("row_h=None 时 row_center 应抛错（不许猜）", False)
    except ValueError:
        ck("row_h=None 时 row_center 抛错（不许猜）", True)

    # ── 2. 真调用：跑真实样本 ──
    # ⛔ 样本不在 ≠ 判据失败：那是"**没测成**"。原来这里 `fail + 1` 并 `return 1`，
    #   等于把"缺样本"记成"测失败"（三态混成一态，正是本项目明令禁止的写法）。
    #   ⇒ 改成**如实跳过**：计 SKIP、汇总行里带"跳过 N"、rc=0（"没测成"不是失败，但必须看得见）。
    if not os.path.isdir(BASE):
        print("  SKIP 样本目录不存在，跳过真调用:", BASE)
        print("\n== 结论：%d 通过 / %d 失败 / %d 跳过（样本目录不在 ⇒ 真调用这一段**没测成**）=="
              % (_ck["pass"], _ck["fail"], 1))
        return 0

    rows = []
    for d in sorted(os.listdir(BASE)):
        j = os.path.join(BASE, d, "samples.jsonl")
        if not os.path.isfile(j):
            continue
        meta = [json.loads(l) for l in io.open(j, encoding="utf-8")]
        meta = [m for m in meta if m.get("png")]
        seen, pick = set(), []
        for m in meta:
            k = (m["w"] // 150, m["h"] // 150)
            if k in seen:
                continue
            seen.add(k)
            pick.append(m)
        for m in pick[:6]:
            p = os.path.join(BASE, d, m["png"])
            if not os.path.isfile(p):
                continue
            im = Image.open(p).convert("RGB")
            g = rg.measure(im)
            # ★ 必须带目录标识：不同采集批次里存在**同名 png**，
            #   只打 png 名会把"另一批的 frame-031"错认成本批的（本轮踩过）。
            rows.append({"batch": d, "png": m["png"], "W": im.size[0],
                         "theme": m.get("theme"),
                         "row_h": g.row_h, "src": g.source, "scale": g.scale,
                         "pane": g.pane, "n": g.n_names, "promote": g.pitch_ocr})
            print("%-22s %-14s W=%4d %-5s | row_h=%-5s src=%-9s scale=%-4s pane=%-5s"
                  % (d[-14:], m["png"], im.size[0], m.get("theme"), g.row_h,
                     g.source, g.scale, g.pane))

    gr = [r for r in rows if r["src"] == "green"]
    hs = [r["row_h"] for r in gr]
    print("\n" + "=" * 88)
    ck("真调用跑到了帧（>0）", len(rows) > 0, "n=%d" % len(rows))
    if hs:
        print("★ 绿带尺命中 %d/%d 帧" % (len(gr), len(rows)))
        print("  原始样本 (行, 批次, 帧) =")
        for r in gr:
            print("     row_h=%-4s  %-22s %s" % (r["row_h"], r["batch"][-20:], r["png"]))
        print("  值域 %s..%s  极差 %s  (n=%d)"
              % (min(hs), max(hs), max(hs) - min(hs), len(hs)))
        lo, hi = rg.GREEN_H_EXPECT
        bad = [r for r in gr if r["row_h"] not in (lo, hi)]
        ck("绿带尺命中率 ≥ 80%", len(gr) / max(1, len(rows)) >= 0.8,
           "%.1f%%" % (100.0 * len(gr) / max(1, len(rows))))
        ck("绿带高全部落在本机预期 %s" % (rg.GREEN_H_EXPECT,),
           not bad, "偏离=%s" % ([(b["batch"][-12:], b["png"], b["row_h"]) for b in bad] or "无"))
    else:
        ck("绿带尺命中（不该是 0）", False, "0 命中")

    other = [r for r in rows if r["src"] != "green"]
    if other:
        # ⚠️ 本段口径更正过（改前先读这段）：
        #   原断言是「非绿带帧一律 row_h=None」。它写在**行高缓存**（`source="cached"`）引入之前 ——
        #   缓存是后来的**有意兜底**（见 `row_geometry.measure` 的说明：本帧没有绿带时先读上次
        #   量到的真值，因为 OCR 估的值本就有噪声，拿噪声当行高更糟）。
        #   ⇒ 非绿带帧的合法形态有两种：`None`（没依据）或 `cached`（有台账依据的兜底）。
        #   ★ 更要紧：这条断言此前**从未真正执行过** —— 本脚本原先缺 UTF-8 垫片，输出一被重定向
        #     就崩在第一条断言上（补垫片后才第一次跑到）。它长期是"看着有覆盖、其实没跑"的假绿。
        #   新口径**不是放宽**：cached 的值必须**有出处** —— 等于本次运行里任一 green 帧量到的值，
        #   或等于台账（`data/row_geometry.json`）里的值。两者都要认，因为台账是**运行期不断被更新**的：
        #   实测本批先量到 96（写进缓存）、后量到 97（台账被改写），于是那帧的 cached=96 只在
        #   "本次出现过的绿带值"里查得到。
        _ledger = rg.cached_row_h()
        _origin = {r["row_h"] for r in gr} | ({_ledger} if _ledger else set())
        print("\n  非绿带帧（只许 None，或 cached 且值有出处；本次绿带值=%s 台账=%s）:"
              % (sorted(x for x in {r["row_h"] for r in gr} if x is not None), _ledger))
        for r in other:
            okv = (r["row_h"] is None) or (r["src"] == "cached" and r["row_h"] in _origin)
            print("    %-22s %-14s src=%-6s row_h=%-6s n=%-3s %s"
                  % (r["batch"][-20:], r["png"], r["src"], r["row_h"],
                     r["n"], "OK" if okv else "!! 既不是 None、出处也查不到"))
        bad = [r for r in other if r["row_h"] is not None
               and not (r["src"] == "cached" and r["row_h"] in _origin)]
        ck("无绿带帧只许 None、或 cached 且出处可查（仍拒绝猜）", not bad,
           "违例=%s" % [(b["png"], b["src"], b["row_h"]) for b in bad])

    print("\n== 结论：%d 通过 / %d 失败 ==" % (_ck["pass"], _ck["fail"]))
    return 0 if _ck["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
