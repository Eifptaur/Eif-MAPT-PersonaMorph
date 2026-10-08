# -*- coding: utf-8 -*-
"""验：真机注释里的那个真实残读，`best_candidate` 能不能救回来。

`wechat.py:4836-4838` 记录真机实测：同一状态连抓 5 帧，漏的那一帧
名字被读成 `文件传，“星期六`（目标 `文件传输助手`）。本探针直接喂这一串，
看护栏放不放行 —— 放行＝纠错能救；拒＝接上纠错也白搭。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from agent import chat_ocr as co  # noqa: E402

CANDS = ['文件传输助手', '腾讯新闻', '微信团队', '某会话']
READS = [
    '文件传，“星期六',      # 真机注释里的原串
    '文件传, "星期六',       # 同上，半角
    '文件传助手',            # 丢中间"输"
    '文件传输助',            # 丢尾字
    '文件传输助手',          # 丢中间"输"（错位）
    '文件传输助手',          # 读全（对照）
]
print('目标＝文件传输助手；候选集＝%s' % CANDS)
print('-' * 78)
for t in READS:
    got, sc, why = co.best_candidate(t, CANDS)
    ok = co.matches(t, '文件传输助手')
    print('读出 %-18r' % t)
    print('    老口径 matches=%s ｜ 纠错 → %r (%.2f)' % (ok, got, sc))
    print('    依据：%s' % why)
