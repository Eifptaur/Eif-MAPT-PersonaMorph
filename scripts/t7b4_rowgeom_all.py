# -*- coding: utf-8 -*-
"""全量回归：把 `samples.jsonl` 里**登记的每一帧**都跑一遍 row_geometry。

★ 技能纪律：报"稳定/一致/极差"前，先证明**抽样覆盖了全量分布**；
  不要"挑几个代表性的"。本脚本按 jsonl 全量遍历（不按目录取前 N 个）。

输出：报告/ocr瓶颈-20260930/t7b4-绿带尺-全量回归.json
"""
import io, json, os, sys, collections
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
BASE = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")


def main():
    from PIL import Image
    from agent import row_geometry as rg

    rows, missing = [], 0
    dirs = sorted(d for d in os.listdir(BASE)
                  if os.path.isfile(os.path.join(BASE, d, "samples.jsonl")))
    print("采集批次(%d): %s" % (len(dirs), dirs))

    for d in dirs:
        j = os.path.join(BASE, d, "samples.jsonl")
        for m in [json.loads(l) for l in io.open(j, encoding="utf-8")]:
            png = m.get("png")
            if not png:
                continue
            p = os.path.join(BASE, d, png)
            if not os.path.isfile(p):
                missing += 1
                continue
            im = Image.open(p).convert("RGB")
            g = rg.measure(im)
            rows.append({"batch": d, "png": png, "W": im.size[0], "H": im.size[1],
                         "dpi": m.get("dpi"), "theme": m.get("theme"),
                         "hl_name": m.get("hl_name"),
                         "row_h": g.row_h, "src": g.source, "pane": g.pane,
                         "n": g.n_names, "pitch": g.pitch_ocr, "scale": g.scale})

    n = len(rows)
    by_src = collections.Counter(r["src"] for r in rows)
    green = [r for r in rows if r["src"] == "green"]
    gh = [r["row_h"] for r in green]

    print("\n" + "=" * 96)
    print("全量帧数 = %d   缺图跳过 = %d" % (n, missing))
    print("按来源: %s" % dict(by_src))

    if gh:
        print("\n① 绿带尺：n=%d/%d (%.1f%%)" % (len(green), n, 100.0 * len(green) / max(1, n)))
        print("   原始样本 row_h 分布: %s"
              % dict(sorted(collections.Counter(gh).items())))
        print("   值域 %s..%s  极差 %s"
              % (min(gh), max(gh), max(gh) - min(gh)))
        # 按 dpi / theme / 窗口宽分组看它随谁变
        for key in ("dpi", "theme"):
            grp = collections.defaultdict(list)
            for r in green:
                grp[r[key]].append(r["row_h"])
            print("   按 %-6s: %s" % (key, {k: sorted(set(v)) for k, v in grp.items()}))
        # 窗口宽 vs 行高（跨宽是否恒定）
        wide = collections.defaultdict(list)
        for r in green:
            wide[r["W"] // 300 * 300].append(r["row_h"])
        print("   按窗口宽(300 档): %s"
              % {k: sorted(set(v)) for k, v in sorted(wide.items())})

    other = [r for r in rows if r["src"] != "green"]
    if other:
        print("\n② 非绿带 %d 帧（降级/拒绝）:" % len(other))
        src_cnt = collections.Counter(r["src"] for r in other)
        print("   来源分布: %s" % dict(src_cnt))
        for r in other[:20]:
            print("     %-22s %-14s W=%4d %-5s src=%-9s row_h=%-5s n=%s"
                  % (r["batch"][-20:], r["png"], r["W"], r["theme"],
                     r["src"], r["row_h"], r["n"]))
        if len(other) > 20:
            print("     ... 另有 %d 帧" % (len(other) - 20))

    io.open(os.path.join(_ROOT, "报告", "ocr瓶颈-20260930",
                         "t7b4-绿带尺-全量回归.json"), "w",
            encoding="utf-8").write(json.dumps({
                "_说明": "全量回归：每帧都用 agent/row_geometry.measure 量一次行高。"
                         "绿带尺 = 当前打开会话行的高亮带高。",
                "summary": {"n_frames": n, "missing_png": missing,
                            "by_source": dict(by_src),
                            "green_row_h_sample": gh,
                            "green_row_h_range": [min(gh), max(gh)] if gh else None},
                "frames": rows}, ensure_ascii=False, indent=1))
    print("\n落盘：报告/ocr瓶颈-20260930/t7b4-绿带尺-全量回归.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
