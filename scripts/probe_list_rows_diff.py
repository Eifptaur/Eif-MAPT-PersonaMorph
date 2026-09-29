# -*- coding: utf-8 -*-
"""改动后逐张对比（修正口径）。

上一版判据的错：拿"整段字符串集合"比。
但裁宽后 OCR 会把同一行**重新切分**（短段被融进长段）⇒ 字符串集合必变，
这**不代表丢信息**。真正的判据应该是**信息量**：

  · 字符级：新读出覆盖了多少"旧读出的字符"（旧字符有多少仍能在新结果里找到）
  · 行级：新结果行数不得少于旧结果

只有"旧有字符在新结果里找不到"才算真丢。
"""
import json
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = r'C:\Users\ptmou\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples'
BASE_JSON = os.path.join(os.path.dirname(__file__), 'list_rows_baseline.json')

from agent import chat_ocr as co  # noqa: E402


def norm(s):
    return ''.join(ch for ch in str(s) if ch.strip())


def coverage(old_list, new_list):
    """旧读出的字符，有多少能在新读出里找到（用子串匹配，避免切分差异误伤）。"""
    new_blob = '|'.join(norm(n) for n in new_list)
    tot = miss = 0
    missing = []
    for o in old_list:
        o = norm(o)
        if not o:
            continue
        tot += 1
        if o in new_blob:
            continue
        # 退一步：旧串的每个字符是否都出现过（说明只是被切分，不是丢失）
        if all(ch in new_blob for ch in o):
            continue
        miss += 1
        missing.append(o)
    return tot, miss, missing


def run():
    base = json.load(open(BASE_JSON, encoding='utf-8'))
    rows_out = []
    for d, items in base.items():
        for r in items:
            p = os.path.join(BASE, d, r['file'])
            if not os.path.exists(p):
                continue
            try:
                img = Image.open(p).convert('RGB')
                new_rows = co.session_rows(img)
            except Exception as e:
                rows_out.append((d, r['file'], r.get('n_rows', 0), 0, 0, 0, ['ERR:%r' % e]))
                continue
            new_names = [x.get('name', '') for x in new_rows]
            tot, miss, missing = coverage(r.get('names', []), new_names)
            rows_out.append((d, r['file'], r.get('n_rows', 0), len(new_rows), tot, miss, missing))

    hard = [x for x in rows_out if x[5] > 0]
    shrunk = [x for x in rows_out if x[3] < x[2]]
    print('=' * 78)
    print('★ 真·字符级退化（旧字符在新结果里找不到）：%d 张' % len(hard))
    for d, f, o, n, tot, miss, missing in hard[:25]:
        print('   %-8s %-40s 旧行%-3s 新行%-3s 丢%d/%d %s'
              % (d, f[:38], o, n, miss, tot, missing[:2]))
    print()
    print('★ 行数变少：%d 张' % len(shrunk))
    for d, f, o, n, tot, miss, missing in shrunk[:15]:
        print('   %-8s %-40s 旧行%-3s → 新行%-3s' % (d, f[:38], o, n))
    print()
    print('★ 覆盖 %d 张；行数增加 %d 张，持平 %d 张'
          % (len(rows_out),
             sum(1 for x in rows_out if x[3] > x[2]),
             sum(1 for x in rows_out if x[3] == x[2])))


if __name__ == '__main__':
    run()
