# -*- coding: utf-8 -*-
"""探针：同分钟冲突时，用"绿底行"能否把候选从 N 收敛到 1（改生产代码前先算命）。

场景（`wechat.py:7560` 记的真机事故）：两个会话在同一分钟都有消息（「KC」/「测试」差 13 秒）
⇒ 所有"用时间认身份"的判据都没有区分力 ⇒ 现在整档弃权（fail-closed）。
但**屏幕上只有一个绿底行**（当前打开的那个），绿底占比 0.9~1.0 vs 其余 0.00。

本探针量：用绿底行的名字（`highlight().name`）与各候选做**唯一匹配**，
能不能把"N 个候选"收敛成"1 个"：
  · 收敛到 1 个 ⇒ 可放行（多一条屏幕侧独立证据，不降级）
  · 收敛不到 0 个（绿底行名字读不出）或 >1 个（歧义）⇒ 照旧弃权（fail-closed 不变）
"""
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = r'%USERPROFILE%\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples'

from agent import chat_ocr as co  # noqa: E402

# (目录, 文件, 候选集＝模拟"这一分钟都有消息的会话名", 真值＝当前真正打开的那个)
CASES = [
    ('wechat', '0af83564-b60d-4812-aa4f-3c330c6399c0.png',
     ['微信团队', '腾讯新闻', '某中文群名', '服务号'], '微信团队'),
    ('wechat', '6c29e9f4-2c81-4b4d-9a71-a826c521f403.png',
     ['微信团队', '腾讯新闻', '某中文群名', '服务号'], '服务号'),
    ('wechat', '7bcf1579-61e9-4c80-a54a-47eb45341b53.png',
     ['微信团队', '腾讯新闻', '某中文群名', '服务号'], '某会话'),
]


def norm(s):
    return ''.join(c for c in str(s) if c.strip())


print('%-14s %-22s %-26s %-22s %s' % ('图', '绿底行读出名字', '收敛结果', '真值', '判定'))
print('-' * 108)
n_ok = n_tot = 0
for d, f, cands, truth in CASES:
    p = os.path.join(BASE, d, f)
    if not os.path.exists(p):
        print('缺：%s' % f)
        continue
    n_tot += 1
    img = Image.open(p).convert('RGB')
    hl = co.highlight(img)
    gname = str((hl or {}).get('name') or '')
    gscore = float((hl or {}).get('score') or 0.0)

    # 收敛：绿底行名字与候选做"唯一匹配"（用候选集纠错的口径——唯一最近才算）
    conv = []
    for c in cands:
        # 子串包含 或 纠错唯一命中
        if norm(gname) and (norm(c) in norm(gname) or norm(gname) in norm(c)):
            conv.append(c)
    if not conv and norm(gname):
        got, sc, _why = co.best_candidate(gname, cands)
        if got:
            conv.append(got)
    ok = (len(conv) == 1 and norm(conv[0]) == norm(truth))
    n_ok += 1 if ok else 0
    print('%-14s %-22r %-26s %-22s %s' % (
        f[:12], gname, (conv[0] if len(conv) == 1 else ('%d 个⇒弃权' % len(conv))),
        truth, '✅' if ok else '❌'))
    print('       绿底 score=%.2f' % gscore)

print('-' * 108)
print('收敛正确：%d/%d' % (n_ok, n_tot))
print()
print('说明：本批样本的绿底行都是"人工点选"的，不是"同分钟冲突"现场 ⇒')
print('      只能验证"绿底行能否被唯一识别"，不能验证真实冲突场景。')
