# -*- coding: utf-8 -*-
"""整图级择一：对每张图，把各候选档分别读一次，按"读出行数 + 名字分"整体选一档。

对比三种策略（在同一批样本上）：
  A. 只窄档（老口径，基线）
  B. 只宽档（绿带/全宽）
  C. 整图择一（按总得分选）
判据：C 的行数 >= max(A, B) 且名字集合覆盖 A 的字符。
"""
import json
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = r'%USERPROFILE%\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples'
BASE_JSON = os.path.join(os.path.dirname(__file__), 'list_rows_baseline.json')

from agent import chat_ocr as co  # noqa: E402
from agent import chat_header as ch  # noqa: E402


def norm(s):
    return ''.join(c for c in str(s) if c.strip())


def read_with_edge(img, x0, x1, h, zoom=2):
    x1 = int(x1)
    if x1 <= x0 + 20:
        return []
    crop = img.crop((x0, 30, x1, h))
    if zoom > 1:
        crop = crop.resize((crop.width * zoom, crop.height * zoom))
    rows = []
    for text, x, y, ww, hh in co.recognize(crop):
        yy = y / float(zoom)
        hit = None
        for r in rows:
            if abs(r["y"] - yy) <= max(8, hh / float(zoom)):
                hit = r
                break
        if hit is None:
            rows.append({"text": str(text), "y": yy})
        else:
            if x / float(zoom) < hit.get("x0", 1e9):
                hit["text"] = str(text) + hit["text"]
            else:
                hit["text"] = hit["text"] + str(text)
    return [r["text"] for r in rows]


def main():
    base = json.load(open(BASE_JSON, encoding='utf-8'))
    stats = {'A': 0, 'B': 0, 'C': 0}
    C_wins, A_wins, B_wins = [], [], []
    rows_report = []

    for d, items in base.items():
        for r in items:
            p = os.path.join(BASE, d, r['file'])
            if not os.path.exists(p):
                continue
            img = Image.open(p).convert('RGB')
            w, h = img.size
            try:
                left = ch.pane_left_for(img) or int(w * ch.PANE_LEFT_REL)
            except Exception:
                left = int(w * ch.PANE_LEFT_REL)
            x0 = max(0, left - 240)

            narrow = max(x0 + 40, left - 6)
            edges = co._row_edge_candidates(img, left, w)

            # A: 只窄档
            A = read_with_edge(img, x0, narrow, h)
            # B: 只最宽档
            B = read_with_edge(img, x0, edges[-1], h)
            # C: 整图择一（按 行数*10 + 名字总长 打分，简单稳健）
            best, best_key, chosen = A, None, narrow
            for e in edges:
                got = read_with_edge(img, x0, e, h)
                key = (len(got), sum(len(norm(t)) for t in got))
                if best_key is None or key > best_key:
                    best_key, best, chosen = key, got, e
            C = best

            nA, nB, nC = len(A), len(B), len(C)
            stats['A'] += 1 if nA else 0
            stats['B'] += 1 if nB else 0
            stats['C'] += 1 if nC else 0

            # 覆盖：旧(A)的字符在 C 里能否找到
            # ⚠️ 口径修正：旧串若是"新结果某个更长串的子序列"，属**改善**（旧的是残字）不是丢失。
            #    例：旧 `当地j` → 新 `当地通报`（`当地` 是子序列，`j` 是噪声残留）
            blob = '|'.join(norm(t) for t in C)
            miss = []
            for t in A:
                nt = norm(t)
                if not nt or nt in blob:
                    continue
                # 旧串的"可用部分"（去掉尾部噪声字符）是否仍在新结果里
                core = nt[:max(1, len(nt) - 1)]
                if core in blob or core[:max(1, len(core) - 1)] in blob:
                    continue
                if all(ch in blob for ch in nt):
                    continue
                miss.append(t)
            if nC > nA:
                C_wins.append((d, r['file'], nA, nC, chosen, left, w))
            elif nC < nA:
                A_wins.append((d, r['file'], nA, nC, chosen, left, w))
            if miss:
                rows_report.append((d, r['file'], nA, nC, miss[:2]))

    print('行数：A(窄)=%d  B(宽)=%d  C(择一)=%d' % (stats['A'], stats['B'], stats['C']))
    print()
    print('★ C 比 A 行多：%d 张' % len(C_wins))
    for d, f, a, c, e, l, w in C_wins[:12]:
        print('   %-8s %-38s %d→%d  选中右沿=%s (left=%d w=%d)' % (d, f[:36], a, c, e, l, w))
    print()
    print('★ C 比 A 行少（真退化）：%d 张' % len(A_wins))
    for d, f, a, c, e, l, w in A_wins[:12]:
        print('   %-8s %-38s %d→%d  选中右沿=%s' % (d, f[:36], a, c, e))
    print()
    print('★ 仍有字符级丢失：%d 张' % len(rows_report))
    for d, f, a, c, m in rows_report[:12]:
        print('   %-8s %-38s %d→%d %s' % (d, f[:36], a, c, m))


if __name__ == '__main__':
    main()
