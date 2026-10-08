# -*- coding: utf-8 -*-
"""量"候选集纠错"接进列表路径能救回多少（不改生产代码，先算收益）。

场景（来自 wechat.py:4836 真机实测注释）：会话行名字被 OCR 读残
（`文件传，“星期六` ← 目标 `文件传输助手`），配不上 ⇒ 整条切换路废。
纠错（`best_candidate`）恰好能救这种"差 1~2 字"的残读。

━━ 判据（v2：修掉 v1 的假增益）━━
v1 的错法：遍历**所有**读出的名字，取**第一个**能 `best_candidate` 成功的当"救回"
   ⇒ 目标=微信、读出全是 '0'，硬纠成 '某中文群名' 也记成救回 —— **纯假增益**。

v2 的两条硬约束（缺一不算救回）：
  ① **纠错结果必须恰等于目标**（归一化后相等）。纠到别的名字＝没救回，甚至更糟（点错会话）。
  ② 只对"**离目标最近的那个读出名字**"试纠错 —— 因为调用方只会在"整张列表里找目标"，
     不会替目标挑一个"刚好能纠对"的噪声串。用离目标最近的，才是真实场景。
  ③ 反向验证（灵敏度断言）：若把目标从候选集里**摘掉**，纠错必须**失败**（不许凭空纠出目标）
     ⇒ 证明 v2 的"救回"确实来自候选集，而不是探针自己凑的。
"""
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = r'%USERPROFILE%\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples'

from agent import chat_ocr as co  # noqa: E402
from agent import chat_header as ch  # noqa: E402

# (目录, 文件, 该图真实存在的会话名——既是"目标"也是"候选集")
# ⚠️ 目标名沿用 probe_name_recall.py 的真值口径：只写"**屏幕上确实完整可见**"的名字
#    （列表窄时微信自己截断成 `海绵宝...`，那种图上读全名物理上不可能）。
CASES = [
    ('local', 'local-030-ScreenShot_2026-09-29_225733_355.png',
     ['海绵', '真鼠标', '腾讯新闻', '微信团队']),
    ('local', 'local-031-ScreenShot_2026-09-29_225743_509.png',
     ['海绵宝', '腾讯新闻', '微信', 'aaa', '服务', '文件传']),
    ('local', 'local-033-ScreenShot_2026-09-29_225759_044.png',
     ['海绵', '腾讯新闻', '文件传']),
    ('local', 'local-034-ScreenShot_2026-09-29_225805_323.png',
     ['海绵', '腾讯新闻']),
    ('local', 'local-021-ScreenShot_2026-09-29_225525_975.png',
     ['课堂', '全量审计', '当地通报']),
    ('local', 'local-022-ScreenShot_2026-09-29_225605_285.png',
     ['课堂', '全量审计', '当地通报']),
    ('wechat', '0af83564-b60d-4812-aa4f-3c330c6399c0.png',
     ['aaa', '服务号', '文件传输']),
    ('wechat', '6c29e9f4-2c81-4b4d-9a71-a826c521f403.png',
     ['微信团队', 'aaa', '服务号']),
    ('wechat', '7bcf1579-61e9-4c80-a54a-47eb45341b53.png',
     ['服务号', '文件传输', '微信团队']),
    ('wechat', '聊天窗-1884x1439.png',
     ['海绵', '流离', '真鼠标']),
]


def norm(s):
    return ''.join(c for c in str(s) if c.strip())


def read_blob(img, zoom=3):
    """按当前生产口径（zoom=3）读出整段文本 blob（与 probe_name_recall 的 evaluate 同口径）。"""
    w, h = img.size
    try:
        left = ch.pane_left_for(img) or int(w * ch.PANE_LEFT_REL)
    except Exception:
        left = int(w * ch.PANE_LEFT_REL)
    x0 = max(0, left - 240)
    x1 = max(x0 + 40, left - 6)
    crop = img.crop((x0, 30, x1, h))
    if zoom > 1:
        crop = crop.resize((crop.width * zoom, crop.height * zoom))
    return '|'.join(norm(str(t)) for t, *_ in co.recognize(crop))


def rows_of(img, zoom=3):
    """该图 `session_rows` 读出的每一行（name / full），纠错就在这个层级做。"""
    return co.session_rows(img, zoom=zoom)


def near_read(cands_read, target):
    """从读出的候选串里挑"离目标最近"的那个（用 `_cand_score` 的距离，缺省用 best_candidate 的判定）。"""
    t = norm(target)
    best = None
    for s in cands_read:
        d, _a, _b = co._cand_score(str(s), str(target))
        if d < 0:
            continue
        if best is None or d < best[0]:
            best = (d, s)
    return best  # (距离, 串) 或 None


tot = hit_old = hit_new = 0
saved = []       # 真救回（纠错恰好等于目标）
failed = []      # 纠错了但**纠到别处**（比不纠更危险）
sens_bad = []    # 灵敏度反例：目标从候选集摘掉后**仍然**纠出了目标 ⇒ 探针自身有问题
for d, f, targets in CASES:
    p = os.path.join(BASE, d, f)
    if not os.path.exists(p):
        print('缺：%s' % f)
        continue
    img = Image.open(p).convert('RGB')
    rows = rows_of(img)
    blob = '|'.join(norm(r.get('name', '')) for r in rows) + '|' + \
           '|'.join(norm(r.get('full', '')) for r in rows) + '|' + read_blob(img)
    reads = [r.get('name', '') for r in rows] + [r.get('full', '') for r in rows]

    cands = list(targets)          # 候选集＝该图真值（模拟 DB 里已知的会话名）
    for target in targets:
        tot += 1
        if norm(target) in blob:
            hit_old += 1
            hit_new += 1
            continue
        # 老口径没中 ⇒ 只对"离目标最近的那个读出串"试纠错（真实场景）
        nr = near_read(reads, target)
        if nr is None:
            continue
        got, sc, why = co.best_candidate(nr[1], cands)
        if got and norm(got) == norm(target):
            hit_new += 1
            saved.append((f[:30], target, nr[1], got, sc))
            # ③ 灵敏度：把目标从候选集摘掉，必须纠不中（否则是探针凑的）
            g2, _s2, _w2 = co.best_candidate(nr[1], [c for c in cands if norm(c) != norm(target)])
            if g2 and norm(g2) == norm(target):
                sens_bad.append((f[:30], target))
        elif got:
            failed.append((f[:30], target, nr[1], got, sc))

print('目标会话名总数：%d' % tot)
print('① 老口径命中      ：%d/%d' % (hit_old, tot))
print('② ＋候选集纠错     ：%d/%d   （+%d）' % (hit_new, tot, hit_new - hit_old))
print()
print('真救回的案例（纠错结果**恰等于**目标）：%d' % len(saved))
for f, tgt, rd, got, sc in saved:
    print('   %-32s 目标=%-12s 读出=%-16r → 纠成 %r (%.2f)' % (f, tgt, rd, got, sc))
print()
print('纠错了但纠到**别处**（比不纠更危险，不该算增益）：%d' % len(failed))
for f, tgt, rd, got, sc in failed:
    print('   %-32s 目标=%-12s 读出=%-16r → 纠成 %r (%.2f) ❌' % (f, tgt, rd, got, sc))
print()
print('灵敏度反例（目标摘掉后仍纠出目标 ⇒ 探针有问题）：%d' % len(sens_bad))
for f, tgt in sens_bad:
    print('   %-32s 目标=%s' % (f, tgt))
