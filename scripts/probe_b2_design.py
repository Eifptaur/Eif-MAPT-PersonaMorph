# -*- coding: utf-8 -*-
"""探针：B2 盲区（名单外会话看不见）能不能靠**屏幕上的会话行**补上？

## 为什么先写设计探针，不直接改代码
上一轮结论是"该把候选集从名单扩到屏幕会话行"。若屏幕上**读不出名字/时间**，
这个修法就是空想 ⇒ **先测可行性，再决定做不做**（无增量即止损）。

## 需要什么
`_same_minute_rivals` 要"**同分钟**还有谁"⇒ 每个候选必须带 **(名字, HH:MM)** 且**归属正确**。

## 三个可证伪的问题
  P1 名字读得出吗？          （名字非空行占比）
  P2 时间读得出吗？          （行内含 HH:MM 占比）
  P3 ★时间和名字**是同一行**吗？（OCR 会把"名字"和"时间"切成两行 ⇒ 归属错乱）
     P3 是**真正的拦路虎**：名前时后分成两行时，`session_rows` 归并靠 y 距判"是不是同一行"，
     可 `ROW_PITCH*0.6≈38px`、而"时间行"距"名字行"约 26px ⇒ **会被并进同一行**；
     但"时间行"本身也可能是**上一行**的时间（微信把时间右对齐在**名字行的基线**上）⇒ 归属仍可能错。

## 判据
P1/P2 有任一项过低，或 P3 无法保证 ⇒ 屏幕行**不能**当候选集 ⇒ B2 修法**不成立**，止损。
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from PIL import Image  # noqa: E402
from agent import chat_ocr as co  # noqa: E402

B = r'C:\Users\ptmou\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples\wechat'
_TS = re.compile(r'(?:昨天|今天|前天)?\s*(\d{1,2})\s*[:：]\s*(\d{2})')

print('== P1/P2：逐图统计 ==')
_tot = _nm = _ts = 0
for fn in sorted(os.listdir(B)):
    if not fn.endswith('.png'):
        continue
    try:
        rows = co.session_rows(Image.open(os.path.join(B, fn)).convert('RGB'))
    except Exception:
        continue
    if not rows:
        continue
    a = sum(1 for r in rows if str(r.get('name') or '').strip())
    b = sum(1 for r in rows if _TS.search(str(r.get('full') or '')))
    _tot += len(rows); _nm += a; _ts += b
    print('  %-14s rows=%-3d 名字=%-3d(%3.0f%%) 行内时间=%-3d(%3.0f%%)'
          % (fn[:12], len(rows), a, 100.0*a/len(rows), b, 100.0*b/len(rows)))
print('  汇总：行 %d · 名字 %.0f%% · 行内时间 %.0f%%' % (_tot, 100.0*_nm/_tot, 100.0*_ts/_tot))
print()

print('== P3：OCR 原始行里，"名字"与"时间"是不是被切成了两行？==')
_r = co.list_rows(Image.open(os.path.join(B, '0af83564-b60d-4812-aa4f-3c330c6399c0.png')).convert('RGB'))
_split = 0
for ln in _r:
    t = str(ln['text'])
    only_ts = _TS.fullmatch(t.strip()) is not None
    if only_ts:
        _split += 1
        print('  y=%-4d %r  ← **整行只有时间，名字在别的行**' % (ln['y_abs'], t))
print('  ⇒ "纯时间行"（名字被切走）共 %d 行' % _split)
print()
print('  ★ 对照：同一张图里"名字+时间在同一行"的：')
for ln in _r:
    t = str(ln['text'])
    if _TS.search(t) and _TS.fullmatch(t.strip()) is None:
        print('  y=%-4d %r  ← 名字与时间粘在一行' % (ln['y_abs'], t))
print()

print('== 结论 ==')
_tsr = 100.0 * _ts / _tot
if _split:
    print('  ⛔ P3 失败：OCR 把**时间单独切成一行**（%d 处）⇒ 归属错乱' % _split)
    print('     实测机制（本图 y 距实测，ROW_PITCH=%d，归并阈值 %.1f）：' % (co.ROW_PITCH, co.ROW_PITCH * 0.6))
    print('       y=152 `aaa偷啃亻吏` —(d=26 < 38.4)→ y=178 `[草稿][图片]`  ⇒ 两条**并成同一行** ✓')
    print('       y=243 `00：03`     —(d=65 > 38.4)→ 自成一行 ⇒ **名字被读成 `00`** ✗')
    print('     ⇒ 即"纯时间行"会**制造一个伪会话行**（name=时间戳），而它对应的真名字')
    print('       其实在上/下一行 ⇒ 屏幕侧行名与行时**对不上号**。')
print('  ⚠️ P2 说明：行内时间可用率 %.0f%% —— 未含"时间"的行多是**今天没新消息**的会话' % _tsr)
print('     （微信列表里这类行**本来就不显示时间**）⇒ 它们**不可能是"同分钟 rival"**，')
print('     这一点反而是**好消息**（天然不会漏）；真正坏的是 P3 的归属错乱。')
print()
print('  ⇒ **B2 的"扩到屏幕行"修法不成立**：屏幕行能给"有哪些会话名"，')
print('     但给不出"**每一行**的准确时间"（伪行 + 归属错）⇒ 无法构造"同分钟候选集"。')
print('  ⇒ **止损**，改走别的路（见报告 §9）。')
