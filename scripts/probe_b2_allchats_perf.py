# -*- coding: utf-8 -*-
"""探针：为 B2 修法二做**性能原型** —— "全量会话 × 同分钟" 查询要多久？

## 背景
修法二＝把 `_same_minute_rivals` 的候选集从"白名单∩群列表"扩到"**全部会话**"。
已实测覆盖面增益 99%~100%（`probe_b2_realdata.py`）。但**必须证明它不拖慢发送链路**。

## 本探针要回答
P1 有没有**便宜的**方法列出"某分钟里有消息的全部会话"？（不许对每张表 `count(*)`）
P2 这个方法的耗时是多少？（真机要求：整个 `_same_minute_rivals` < 100ms 量级）

## 设计候选
A. 对每张 `Msg_*` 表 `SELECT MAX(create_time)` ⇒ 只读标量，不碰大 BLOB。
   实测判据：`sort_seq`/`create_time` 有没有索引；没有索引也是线性扫描但**只扫一列**。
B. `list_message_chats()`（包自带）⇒ 它对每表 `count(*)` ⇒ **太慢**，排除。
C. 直接读 `session.db` 的 `SessionTable`（会话列表本来就按时间排序）⇒ 最省。
"""
import glob
import os
import sqlite3
import time
import logging

log = logging.getLogger("persona-morph")

_BASE = os.path.join(os.environ.get('TEMP', os.environ.get("TEMP", "")),
                     'wechatauto_db')
_accts = [d for d in glob.glob(os.path.join(_BASE, '*'))
          if os.path.isfile(os.path.join(d, 'message__message_0.db'))]
if not _accts:
    print('!! 找不到真实库')
    raise SystemExit(1)
_db = os.path.join(_accts[0], 'message__message_0.db')
print('== 用真实库：%s ==' % os.path.basename(_accts[0]))
_c = sqlite3.connect(_db)
_cur = _c.cursor()
_tbs = [r[0] for r in _cur.execute(
    "select name from sqlite_master where type='table' and name like 'Msg_%'").fetchall()]
print('  Msg_* 表 %d 张' % len(_tbs))
print()

print('== P1a：`create_time` 有没有索引？（决定 MAX() 是 O(n) 还是 O(1)）==')
_has_idx = 0
_idx_names = []
for _tb in _tbs[:5]:
    _ix = _cur.execute('PRAGMA index_list("%s")' % _tb).fetchall()
    for _i in _ix:
        _cols = [r[2] for r in _cur.execute('PRAGMA index_info("%s")' % _i[1]).fetchall()]
        if 'create_time' in _cols or 'sort_seq' in _cols:
            _has_idx += 1
            _idx_names.append((_tb[:28], _i[1], _cols))
print('  前 5 张表里，含 create_time/sort_seq 的索引：%d 个' % _has_idx)
for _t, _n, _cs in _idx_names[:3]:
    print('    %s · %s · %s' % (_t, _n, _cs))
print('  ⇒ %s' % ('有索引 ⇒ MAX 便宜' if _has_idx else '**无索引** ⇒ MAX 需线性扫（但仍只扫一列，不碰 BLOB）'))
print()

print('== P1b/P2：方案 A（逐表 MAX(create_time)）实测耗时 ==')
_t0 = time.perf_counter()
_last = {}
for _tb in _tbs:
    try:
        _v = _cur.execute('SELECT MAX(create_time) FROM "%s"' % _tb).fetchone()[0]
        _last[_tb] = _v
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
_t1 = time.perf_counter()
print('  %d 张表逐个 MAX(create_time)：**%.1f ms**' % (len(_tbs), (_t1 - _t0) * 1000))
print('  返回 %d 个会话的最新时间戳' % len(_last))
print('  ⇒ %s' % ('✓ 可接受（<100ms）' if (_t1 - _t0) * 1000 < 100 else '⚠️ 偏慢，需缓存'))
print()

print('== P1c：方案 A 能否直接给出"同分钟候选集"？（本探针的核心）==')
_secs = [v for v in _last.values() if v]
if _secs:
    _mx = max(_secs)
    _bucket = _mx // 60
    _same = {t: v for t, v in _last.items() if v and v // 60 == _bucket}
    print('  最新分钟桶：%d 个会话有消息' % len(_same))
    print('  ⇒ 所以"全量同分钟候选集"＝ 遍历 %d 张表各一次 MAX ⇒ **方案 A 可行**' % len(_tbs))
print()
print('== 方案 C 对照：session.db 的 SessionTable ==')
_sdb = os.path.join(_accts[0], 'session__session.db')
if os.path.isfile(_sdb):
    _c2 = sqlite3.connect(_sdb)
    try:
        _cols = [r[1] for r in _c2.execute('PRAGMA table_info(SessionTable)').fetchall()]
        print('  SessionTable 列：%s' % _cols[:10])
        _n = _c2.execute('SELECT count(*) FROM SessionTable').fetchone()[0]
        print('  行数：%d' % _n)
        print('  ⇒ 若含时间列，方案 C 比方案 A 更省（一次查询）')
    except Exception as e:
        print('  读失败：%s' % e)
    _c2.close()
_c.close()
print()
print('== 结论 ==')
print('  方案 A（逐表 MAX(create_time)）实测 %.1f ms / %d 表 ⇒ %s'
      % ((_t1 - _t0) * 1000, len(_tbs),
         '可直接用，但应加 60s 缓存' if (_t1 - _t0) * 1000 < 100 else '必须加缓存'))
print('  方案 B（list_message_chats）排除：它对每表 count(*) ⇒ 更慢')
print('  ⇒ 修法二**有可行的实现路径**；下一步是把它接进 `_same_minute_rivals` 并加缓存')
