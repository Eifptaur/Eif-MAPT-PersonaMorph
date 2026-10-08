# -*- coding: utf-8 -*-
"""基线快照：对每个样本记录"当前 list_rows 读出的会话名集合"。

用途：改 list_rows 之后逐张对比，判据是"只许多不许少"。
输出 JSON 便于 diff，不写进生产目录。
"""
import json
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = r'%USERPROFILE%\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples'
OUT = os.path.join(os.path.dirname(__file__), 'list_rows_baseline.json')

from agent import chat_ocr as co  # noqa: E402


def snapshot(tag, path):
    try:
        img = Image.open(path).convert('RGB')
    except Exception as e:
        return {'file': os.path.basename(path), 'error': repr(e)}
    try:
        rows = co.session_rows(img)
    except Exception as e:
        return {'file': os.path.basename(path), 'error': repr(e), 'n': 0}
    names = [r.get('name', '') for r in rows]
    return {
        'file': os.path.basename(path),
        'size': list(img.size),
        'n_rows': len(rows),
        'names': names,
    }


def main():
    snap = {}
    for d in ('local', 'wechat', 'web'):
        S = os.path.join(BASE, d)
        if not os.path.isdir(S):
            continue
        snap[d] = []
        for f in sorted(os.listdir(S)):
            if not f.lower().endswith(('.png', '.jpg', '.jpeg')):
                continue
            r = snapshot(d, os.path.join(S, f))
            snap[d].append(r)
            print('%-8s %-46s 行=%-3s' % (d, r['file'][:44], r.get('n_rows', 'ERR')))

    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(snap, fh, ensure_ascii=False, indent=1)
    print('\n基线已写入 %s' % OUT)
    tot = sum(len(v) for v in snap.values())
    print('覆盖 %d 张图' % tot)


if __name__ == '__main__':
    main()
