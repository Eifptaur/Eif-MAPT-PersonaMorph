# -*- coding: utf-8 -*-
"""探针：字级框（word-level box）到底拿不拿得到、精度够不够切栏。

⚠️ 这是一次性**证据采集**脚本，不是生产代码、不进任何判据链。
   目的：为「字级框切栏」这条候选路线取**第一手数据**——不实测不许写结论（项目纪律）。

跑法（必须用项目运行时）：
    ./runtime/python/python.exe scripts/probe_word_boxes.py

输出：
    ① WinRT `line.words` 的单字框（中文场景是否＝单字？间距分布如何？）
    ② 每行的「字内 gap」vs「名字↔时间戳 gap」是否可分（切栏可行性的直接证据）
    ③ RapidOCR 侧的行级框（当前实现拿到的粒度）作对照
"""
import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

SAMPLE = os.path.abspath(os.path.join(
    os.path.dirname(ROOT), "dev-workspace", "persona-morph", "ocr-samples", "wechat"))


def probe_winrt_words(path):
    """直接调 WinRT，**不经过** ScreenOCR 的封装（那个只取 words[0]）。"""
    from PIL import Image
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.storage import StorageFile
    import asyncio
    import tempfile

    async def _run():
        im = Image.open(path).convert("RGB")
        tmp = os.path.join(tempfile.gettempdir(), "probe_wordbox.png")
        im.save(tmp)
        f = await StorageFile.get_file_from_path_async(tmp)
        s = await f.open_async(0)
        dec = await BitmapDecoder.create_async(s)
        bmp = await dec.get_software_bitmap_async()
        eng = OcrEngine.try_create_from_user_profile_languages()
        if eng is None:
            return []
        res = await eng.recognize_async(bmp)
        rows = []
        for line in res.lines:
            words = []
            for w in line.words:
                r = w.bounding_rect
                words.append({"t": w.text, "x": float(r.x), "y": float(r.y),
                              "w": float(r.width), "h": float(r.height)})
            rows.append({"line": line.text, "n_words": len(words), "words": words})
        return rows

    return asyncio.run(_run())


def gaps_of(words):
    """按 x 排序后相邻框的间距（右边界 → 下一个左边界）。"""
    ws = sorted(words, key=lambda d: d["x"])
    out = []
    for i in range(len(ws) - 1):
        right = ws[i]["x"] + ws[i]["w"]
        out.append((ws[i]["t"], ws[i + 1]["t"], round(ws[i + 1]["x"] - right, 1)))
    return out


def main():
    files = sorted(f for f in os.listdir(SAMPLE) if f.lower().endswith(".png"))
    print("=" * 78)
    print("样本目录：%s" % SAMPLE)
    print("文件：%s" % files)
    print("=" * 78)
    if not files:
        print("⛔ 没有样本，退出")
        return
    for fn in files:
        p = os.path.join(SAMPLE, fn)
        print("\n" + "─" * 78)
        print("【图】%s" % fn)
        try:
            rows = probe_winrt_words(p)
        except Exception as e:  # noqa: BLE001
            print("  WinRT 失败：%s: %s" % (type(e).__name__, e))
            continue
        print("  行数：%d" % len(rows))
        for i, r in enumerate(rows):
            ws = r["words"]
            txt = r["line"]
            print("  [%2d] n_words=%-3d line=%r" % (i, len(ws), txt[:40]))
            if not ws:
                continue
            # 单字性检查：中文行，词长分布
            lens = {}
            for w in ws:
                lens[len(w["t"])] = lens.get(len(w["t"]), 0) + 1
            print("        词长分布：%s" % json.dumps(lens, ensure_ascii=False))
            g = gaps_of(ws)
            if g:
                vals = sorted(x[2] for x in g)
                print("        gap 值(升序)：%s" % vals)
                print("        gap 明细：%s" % [
                    "%s|%s=%.1f" % (a, b, v) for a, b, v in g])


if __name__ == "__main__":
    main()
