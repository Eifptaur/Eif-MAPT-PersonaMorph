# -*- coding: utf-8 -*-
"""核实 30 帧差异的**方向**：新口径是"把该并的并了"（对）还是"把该分的并了"（错）。

判据（结构性，不看数字）：一个真正的会话行 = 名字行 + 可选预览行，
其中**名字行**应满足 `split_name()` 后仍像名字（不含大段 URL / 不以空格开头）。
⇒ 若新口径把"疑似名字行"并进了前一行 ⇒ 说明并错了。
"""
import io, json, os, re, sys, collections
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
BASE = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")

URL_RE = re.compile(r"https?|\.com|\.edu|www\.|&amp;", re.I)


def old_session_rows(img, zoom=3, pane_left=0):
    from agent import chat_ocr as co
    lines = co.list_rows(img, zoom=zoom, pane_left=pane_left) if pane_left else co.list_rows(img, zoom=zoom)
    rows = []
    for ln in lines:
        if rows and (ln["y_abs"] - rows[-1]["y_abs"]) < co.ROW_PITCH * 0.6:
            rows[-1]["preview"] = (rows[-1]["preview"] or "") + ln["text"]
        else:
            rows.append({"name": ln["text"], "preview": None, "y_abs": ln["y_abs"]})
    for r in rows:
        r["full"] = r["name"]
        r["name"] = co.split_name(r["name"])
    return rows


def main():
    from PIL import Image
    from agent import chat_ocr as co

    cases = []
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
            a, b = old_session_rows(im), co.session_rows(im)
            if [r["y_abs"] for r in a] == [r["y_abs"] for r in b]:
                continue
            # 差异帧：找出"新口径并进去"的那些 y（在 a 里有、在 b 里没有）
            ya = set(r["y_abs"] for r in a)
            yb = set(r["y_abs"] for r in b)
            merged = sorted(ya - yb)        # 被并掉的
            split = sorted(yb - ya)         # 被拆出的
            lines = {l["y_abs"]: l["text"] for l in co.list_rows(im, zoom=3)}
            pitch, src = co._row_pitch_for(im)
            cases.append({"batch": d, "png": png, "src": src, "pitch": pitch,
                          "merged": [(y, lines.get(y, "")) for y in merged],
                          "split": [(y, lines.get(y, "")) for y in split]})

    print("差异帧 = %d" % len(cases))
    verdict = collections.Counter()
    for c in cases:
        print("\n" + "-" * 88)
        print("%s / %s   pitch=%s(%s)" % (c["batch"][-12:], c["png"], c["pitch"], c["src"]))
        for y, t in c["merged"]:
            is_url = bool(URL_RE.search(t or ""))
            is_name = 1 <= len((t or "").strip()) <= 14 and not is_url
            tag = "URL/续行" if is_url else ("像名字" if is_name else "其他")
            verdict["merged_" + ("url" if is_url else ("name" if is_name else "other"))] += 1
            print("   并掉 y=%-4d [%s] %s" % (y, tag, (t or "")[:50]))
        for y, t in c["split"]:
            print("   拆出 y=%-4d %s" % (y, (t or "")[:50]))

    print("\n" + "=" * 88)
    print("被并掉的行的性质分布: %s" % dict(verdict))
    print("★ 判读：`merged_url`（URL/续行）多 ⇒ 新口径在**修正旧口径把预览拆成两条**的错误（对）；")
    print("        `merged_name`（像名字）多 ⇒ 新口径**并错了**，需要收紧。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
