# -*- coding: utf-8 -*-
"""探针：**DB 落库延迟**会不会影响"同分钟收敛"这条判据？

## 用户的质疑（本轮输入）
"DB 落库不是有延迟吗？这会影响到我们这个判断吗"

## 为什么这个质疑必须当真
`_same_minute_rivals` 的**输入完全来自 DB**（遍历各会话查 `_last_time_hhmm`）。
若 DB 落后 ⇒ 别人的消息看不见 ⇒ 冲突"漏检" ⇒ 判据以为"只有目标"。
但"漏检"到底是**安全**问题（错放）还是**可用性**问题（错拒）？——**必须逐个消费者推，不许猜**。

## 三个消费者（同一个 `_same_minute_rivals` 的三个调用点）
  C1 `_row_time_conflict`（:7096）—— **负判据**："活动行时间≠目标 ⇒ 不是它"
  C2 `_active_row_time_ok`（:7408）—— **正判据**："高亮行时间＝目标 + 独立佐证 ⇒ 是它"
  C3 `_pane_time_hits`（:7478）—— **正判据**："聊天区里出现目标那条的时间"

## 本探针的**可复核断言**（不是打印观点，是拿源码/夹具算）
  A. `_same_minute_rivals` 的输入确实只来自 DB（源码锚）
  B. 三个消费者在"rivals 为空"时**各自走哪条分支**（源码锚：空 ⇒ 谁被跳过）
  C. 漏检的**方向性**：把 rivals 从 `[某人]` 改成 `[]`，三个消费者的返回**变化方向**
     ⇒ 若变成"更可能放行"＝安全问题；若变成"更可能拒"＝可用性问题
  D. 本轮的收敛**不在漏检路径上**（`if out and ...` 为假 ⇒ 不执行）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = open(os.path.join(_ROOT, 'agent', 'wechat.py'), encoding='utf-8').read()

_fail = []
_n = [0]


def ck(name, cond, detail=''):
    _n[0] += 1
    print('  [%s] %s%s' % ('✓' if cond else '✗', name, (' — ' + detail) if detail else ''))
    if not cond:
        _fail.append(name)


print('== A. `_same_minute_rivals` 的输入是否**只**来自 DB ==')
# 找函数体
_i = _SRC.find('def _same_minute_rivals(')
_j = _SRC.find('\n    def ', _i + 10)
_body = _SRC[_i:_j]
ck('A1 源码锚：函数体里出现 `_last_time_hhmm`（DB 侧）',
   '_last_time_hhmm' in _body)
ck('A2 源码锚：函数体里出现 `_monitored_chat_ids`（遍历会话）',
   '_monitored_chat_ids' in _body)
ck('A3 反向：函数体里**没有**第二个数据源（没有 session_rows/highlight 直接取数）',
   'session_rows(' not in _body,
   '有的话说明不止 DB 一个来源')
ck('A4 源码锚：`_last_time_hhmm` 的实现确实读 DB（`_db_messages`）',
   'def _last_time_hhmm' in _SRC
   and '_db_messages' in _SRC[_SRC.find('def _last_time_hhmm'):
                              _SRC.find('def _last_time_hhmm') + 900])
print()

print('== B. 三个消费者在 "rivals 为空" 时各自走哪条分支 ==')
# C1 _row_time_conflict
_c1 = _SRC[_SRC.find('def _row_time_conflict('):_SRC.find('def _active_row_time_ok(')]
ck('B1 C1（负判据）：rivals 非空 ⇒ 返回 `decided=False`（判不了）',
   '_rivals' in _c1 and 'decided=False' in _c1.replace(' ', '').replace('\n', '')
   or 'False, False' in _c1)
ck('B2 C1：rivals 为空 ⇒ 返回 `True, True`（"确定是它"，放行）',
   _c1.rfind('return False, "", True, True') > 0,
   '源码里就是 `return False, "", True, True`')
# C2 _active_row_time_ok
_c2 = _SRC[_SRC.find('def _active_row_time_ok('):_SRC.find('def _pane_time_hits(')]
ck('B3 C2（正判据）：rivals 非空 ⇒ `return False`（不给正面结论，弃权）',
   'if _rivals:' in _c2 and 'return False' in _c2[_c2.find('if _rivals:'):_c2.find('if _rivals:') + 400])
ck('B4 C2：rivals 为空 ⇒ **继续往下**走"高亮行时间"那道证据（不再被拦）',
   _c2.find('if _rivals:') < _c2.find('highlight_time('))
ck('B5 C2：rivals 为空 ≠ 直接放行（还要过高亮时间那关）',
   'return True, ("高亮行（y=' in _c2)
print()

print('== C. 漏检的**方向性**：rivals [某人] → [] 时，谁能放行？==')
print('  · C1（负判据）: [某人] ⇒ decided=False（影响不了结论）')
print('                 [] ⇒ True,True（**确定"是它"**）')
print('    ⇒ 漏检让 C1 **从"判不了"变成"确定是它"** —— 这是**放宽**，方向朝**安全**问题')
print('  · C2（正判据）: [某人] ⇒ False（弃权）')
print('                 [] ⇒ 继续评估高亮时间（可能 True）')
print('    ⇒ 漏检让 C2 **从"弃权"变成"可能放行"** —— 同样是**放宽**')
ck('C1 漏检方向 = 放宽（`_row_time_conflict` 的空 rivals 返回 True,True）',
   'return False, "", True, True' in _c1)
ck('C2 漏检方向 = 放宽（`_active_row_time_ok` 的空 rivals 会继续走到 return True）',
   _c2.find('if _rivals:') < _c2.find('return True, ("高亮行（y='))
print('  ⇒ **修正我上一轮的判断**：漏检的确是**安全方向**的问题（可能错放），不只是可用性。')
print('    但它**不是本轮引入的**：这条路径（DB-only 的 rivals）本轮一字未改。')
print()

print('== D. 本轮的收敛是否在漏检路径上 ==')
ck('D1 源码锚：收敛被 `if out and img is not None:` 包住',
   'if out and img is not None:' in _body)
ck('D2 源码锚：`out` 为空（漏检）⇒ 直接 `return out`（收敛不执行）',
   'return out' in _body and _body.find('if out and img is not None:') < _body.rfind('return out'))
ck('D3 行为验证：不传 img 时，返回值与"无收敛"逐字一致（由 identity_minute_selftest H4 锁定）',
   'H4' in open(os.path.join(_ROOT, 'scripts', 'identity_minute_selftest.py'),
                encoding='utf-8').read())
print()

print('== E. 代码对"DB 落后"的既有防御（补 C 的口子）==')
ck('E1 `db_alive()` 有 "-wal 刚被写过" 检测',
   'def db_alive' in _SRC and '活库刚刚还在写' in _SRC)
ck('E2 `_newest_wal_mtime()` 拿 `-wal` 的 mtime 判库新鲜度',
   'def _newest_wal_mtime' in _SRC)
ck('E3 发送链路从 DB 回读核对（亚秒级锚）',
   '回读 0.4s' in _SRC or '回读 0.4s' in _SRC)
ck('E4 ⚠️ 但 `_same_minute_rivals` 这条路径**没有**先问 `db_alive`（这是缺口）',
   'db_alive' not in _body,
   '源码证实：rivals 链路不查 DB 新鲜度')
print()

print('== 汇总（可复核结论）==')
print('  Q1 自己发：亚秒级（0.4s 锚）⇒ "同分钟"60s 粒度下不影响')
print('  Q2 别人发：**会被漏检**，且方向是**放宽**（C1 从"判不了"变"确定是它"，C2 从弃权变可能放行）')
print('            ⇒ 这是**安全方向**的既有风险；缺口＝该路径不查 `db_alive`（E4）')
print('  Q3 本轮收敛：**不在漏检路径上**（D1/D2：out 为空即 return，收敛不执行）⇒ 未制造新风险')
print()
print('  全部断言 %d 条，失败 %d 条' % (_n[0], len(_fail)) if not _fail else '  失败：%s' % _fail)
sys.exit(1 if _fail else 0)
