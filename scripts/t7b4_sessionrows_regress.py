# -*- coding: utf-8 -*-
"""回归：`session_rows` 改成"本帧实测行高"后，**结果必须与旧口径一致**。

★ 技能纪律：改核心模块 ⇒ 必须真调用 + 全套回归；"改了但没效果"也要如实报。
判据：· 行数是否变化（应基本不变）
      · 每行的 y_abs / name 是否漂移（应基本不变）
      · `_row_pitch_for` 的 source 分布（看绿带尺覆盖率）
"""
import io, json, os, sys, collections
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
BASE = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")


def old_session_rows(img, zoom=3, pane_left=0):
    """旧口径：阈值 = ROW_PITCH * 0.6（写死 38.4）。"""
    from agent import chat_ocr as co
    lines = co.list_rows(img, zoom=zoom, pane_left=pane_left) if pane_left else co.list_rows(img, zoom=zoom)
    rows = []
    for ln in lines:
        if rows and (ln["y_abs"] - rows[-1]["y_abs"]) < co.ROW_PITCH * 0.6:
            if rows[-1]["preview"] is None:
                rows[-1]["preview"] = ln["text"]
            else:
                rows[-1]["preview"] += ln["text"]
        else:
            rows.append({"name": ln["text"], "preview": None, "y_abs": ln["y_abs"]})
    for r in rows:
        r["full"] = r["name"]
        r["name"] = co.split_name(r["name"])
    return rows


def main():
    from PIL import Image
    from agent import chat_ocr as co

    same = diff = skipped = 0
    src_cnt = collections.Counter()
    pitch_cnt = collections.Counter()
    details = []

    for d in sorted(os.listdir(BASE)):
        j = os.path.join(BASE, d, "samples.jsonl")
        if not os.path.isfile(j):
            continue
        for m in [json.loads(l) for l in io.open(j, encoding="utf-8")]:
            png = m.get("png")
            p = os.path.join(BASE, d, png or "")
            if not png or not os.path.isfile(p):
                continue
            im = Image.open(p).convert("RGB")
            try:
                pitch, src = co._row_pitch_for(im)
            except Exception as e:
                pitch, src = None, "ERR:%s" % e
            src_cnt[src] += 1
            pitch_cnt[pitch] += 1
            try:
                a = old_session_rows(im)
                b = co.session_rows(im)
            except Exception as e:
                skipped += 1
                details.append({"batch": d, "png": png, "err": str(e)})
                continue
            na, nb = len(a), len(b)
            ya = [r["y_abs"] for r in a]
            yb = [r["y_abs"] for r in b]
            if na == nb and ya == yb:
                same += 1
            else:
                diff += 1
                details.append({"batch": d, "png": png, "n_old": na, "n_new": nb,
                                "y_old": ya, "y_new": yb, "pitch": pitch, "src": src})

    print("=" * 96)
    print("_row_pitch_for 来源分布: %s" % dict(src_cnt))
    print("_row_pitch_for 取值分布: %s" % dict(sorted(pitch_cnt.items(),
                                                key=lambda kv: -kv[1])))
    print("\nsession_rows 结果对比（旧 ROW_PITCH*0.6  vs  新 本帧实测*0.4）:")
    print("  完全一致 : %d" % same)
    print("  有差异   : %d" % diff)
    print("  报错跳过 : %d" % skipped)
    if details:
        print("\n  差异明细（前 20）:")
        for x in details[:20]:
            if "err" in x:
                print("    %-14s %s  ERR=%s" % (x["batch"][-12:], x["png"], x["err"][:70]))
            else:
                print("    %-14s %-14s n:%s→%s pitch=%s(%s)"
                      % (x["batch"][-12:], x["png"], x["n_old"], x["n_new"],
                         x["pitch"], x["src"]))
                print("        y_old=%s" % (x["y_old"][:10],))
                print("        y_new=%s" % (x["y_new"][:10],))
    print("\n★ 判定：差异为 0 ⇒ 换口径**行为等价**（但换环境会自动跟行高变）；"
          "差异 >0 ⇒ 逐条核实是不是回归。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
