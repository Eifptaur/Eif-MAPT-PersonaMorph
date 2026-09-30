# -*- coding: utf-8 -*-
"""探针：B2 盲区的**可行**修法 —— 把候选集从"白名单∩群列表"扩到"**全部群+全部私聊**"。

## 上一版探针的结论（`probe_b2_design.py`）
"扩到屏幕会话行" **不成立**（伪行 + 时间归属错）。⇒ 换路。

## 这条路（本探针）
`_monitored_chat_ids` 现在＝ `resolve_groups(list_groups(), 白名单, 黑名单)` + `list_private_targets()`。
它**先过白名单**，所以名单外的群**根本不进候选集** ⇒ B2。

而 `list_groups()` / `list_privates()` 读的是 **contact.db 的全会话表**（不经白名单）
⇒ 直接用它们就能覆盖"本机所有会话"，**无需任何屏幕 OCR**。

## 代价（必须如实算）
候选集从 N 个变成 M 个 ⇒ `_same_minute_rivals` 要逐个查 `_last_time_hhmm`（各一次 DB 读）。
⇒ 必须测：**M 有多大**、**每次查询耗时**，否则会把发送链路拖慢（真机要求亚秒级）。

## 判据
· 覆盖面：M / N 的放大倍数（越大越安全，但越慢）
· 耗时：全量查一遍要多久（>300ms 就不可接受）
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from agent import wechat as W  # noqa: E402

print('== 源码锚：`_monitored_chat_ids` 是否**先过白名单** ==')
_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'agent', 'wechat.py'), encoding='utf-8').read()
_b = _src[_src.find('def _monitored_chat_ids'):_src.find('def _monitored_chat_ids') + 2200]
print('  [%s] 走了 `resolve_groups(list_groups(), 白名单, 黑名单)` ⇒ **白名单外被过滤掉**'
      % ('✓' if 'resolve_groups' in _b and 'group_name_white_list' in _b else '✗'))
print('  [%s] 私聊侧走 `list_private_targets()`（按 private_chat 档位）'
      % ('✓' if 'list_private_targets' in _b else '✗'))
print()

print('== 可用的"全量"入口（不经白名单）==')
print('  [%s] `list_groups()` 存在 —— 读 contact.db 全会话' % ('✓' if 'def list_groups' in _src else '✗'))
print('  [%s] `list_privates()` 存在 —— 读 contact.db 全私聊' % ('✓' if 'def list_privates' in _src else '✗'))
print('  ⇒ 两者**都不经白名单** ⇒ 可直接当"全量候选集"')
print()

print('== 代价：逐个查 `_last_time_hhmm` 的耗时（模拟真实规模）==')
_N = 200  # 模拟"本机 200 个会话"的最坏情况
print('  若本机有 %d 个会话，`_same_minute_rivals` 要对每个查一次 `_last_time_hhmm`' % _N)
print('  每次 = 一次 `_db_messages(limit=1)`（SQLite 索引查询）')
print('  ⚠️ 这是**未实测**项：本机没有可读的真库（只有夹具）⇒ 只能给量级判断')
print('  ⇒ 量级估算：SQLite 单行索引查询通常 0.1~2ms ⇒ %d 次 ≈ %s'
      % (_N, '20~400ms' if _N == 200 else '?'))
print('  ⇒ **建议**：加一层"先按 `-wal` 时间粗筛，只查最近被写的库"或"缓存 60s"')
print('     （`_monitored_chat_ids` 已有 60s 缓存，可照抄同一套）')
print()

print('== 结论 ==')
print('  ✓ 这条路（扩到 list_groups + list_privates）**技术上可行**：不碰屏幕、不碰 OCR')
print('  ⚠️ 代价是候选集变大 ⇒ **必须加缓存**（否则每次发送多几百 ms）')
print('  ⚠️ 覆盖面增益**无法本机实测**（没有真库）⇒ 只能靠"白名单 ≠ 全部会话"这个事实推断')
