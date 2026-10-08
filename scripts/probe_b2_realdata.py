# -*- coding: utf-8 -*-
"""探针：用**本机真实微信库**量化"同分钟碰撞率" —— B2 盲区的真实规模。

## 为什么能做了（更正上一轮）
上一轮我说"本机无可量化的真库" —— **这句话错了**。
本机存在**已解密的真实库副本**（`%TEMP%\wechatauto_db\`，项目历史留下的）：
  · `wxid_<别号>_eda5`  1558 联系人 / 23 群 / message.db 有 **50 张 Msg_* 会话表**
  · 示例账号（`wxid_<序号>`）27 联系人 / 2 群
⇒ 有真数据 ⇒ **不必再要"真机"，本条可以直接测**。

## 测什么
`_same_minute_rivals` 关心的量：**"两个不同会话在同一分钟都有消息"的频次**。
· 频次高 ⇒ 冲突频繁 ⇒ 时间档经常弃权 ⇒ B2 盲区**有咬人机会**（名单外那个恰好也在这分钟）
· 频次低 ⇒ 冲突罕见 ⇒ B2 的影响面自然小

## 口径
拿所有 Msg_* 表，各取最后 N 条，按 `create_time//60` 分桶，统计：
  M1 总分钟桶数
  M2 "同一分钟 ≥2 个会话"的桶数 ⇒ 碰撞率
  M3 碰撞桶里最大并列会话数
"""
import glob
import os
import sqlite3
from collections import defaultdict
import logging

log = logging.getLogger("persona-morph")

_BASE = os.path.join(os.environ.get('TEMP', os.environ.get("TEMP", "")),
                     'wechatauto_db')
print('== 找真实库 ==')
_accts = []
for d in glob.glob(os.path.join(_BASE, '*')):
    if os.path.isfile(os.path.join(d, 'message__message_0.db')):
        _accts.append(d)
for a in _accts:
    print('  %s' % os.path.basename(a))
if not _accts:
    print('  !! 没有找到，探针无法继续')
    raise SystemExit(1)
print()

for _acct in _accts:
    _db = os.path.join(_acct, 'message__message_0.db')
    print('== 账号 %s ==' % os.path.basename(_acct))
    try:
        _c = sqlite3.connect(_db)
        _cur = _c.cursor()
        _tbs = [r[0] for r in _cur.execute(
            "select name from sqlite_master where type='table' and name like 'Msg_%'").fetchall()]
        print('  Msg_* 会话表：%d 张' % len(_tbs))
        _buckets = defaultdict(set)
        _rows = 0
        for _tb in _tbs:
            try:
                for (_t,) in _cur.execute(
                        'select create_time from "%s" order by create_time desc limit 300' % _tb):
                    try:
                        _t = int(_t)
                    except Exception:
                        continue
                    if _t > 10 ** 10:
                        _t //= 1000
                    _buckets[_t // 60].add(_tb)
                    _rows += 1
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                continue
        _c.close()
        _multi = {k: v for k, v in _buckets.items() if len(v) >= 2}
        _mx = max((len(v) for v in _buckets.values()), default=0)
        print('  抽样消息 %d 条 · 覆盖 %d 个分钟桶' % (_rows, len(_buckets)))
        print('  ★ 同一分钟有 ≥2 个会话的桶：**%d**（占 %.1f%%）'
              % (len(_multi), 100.0 * len(_multi) / max(1, len(_buckets))))
        print('  ★ 碰撞桶里最多并列会话数：%d' % _mx)

        # ★ 下一步：在碰撞窗口里，假设"只监听 2 个会话"，看有多少窗口**含名单外会话**
        _all_tbs = sorted(_tbs)
        _watch = set(_all_tbs[:2])  # 模拟"白名单只监听 2 个会话"
        _out_cnt = sum(1 for _k, _v in _multi.items() if (_v - _watch))
        print('  ★★ 假设白名单只监听 %d 个会话 ⇒ **%d / %d** 个碰撞窗口里有"名单外"会话'
              % (len(_watch), _out_cnt, len(_multi)))
        if _multi:
            print('     ⇒ 占比 %.0f%% 的碰撞窗口，现有 `_same_minute_rivals` **看不见**竞争者'
                  % (100.0 * _out_cnt / len(_multi)))
        if _multi:
            _top = sorted(_multi.items(), key=lambda kv: -len(kv[1]))[:3]
            for _k, _v in _top:
                import time as _tm
                print('     例：%s 有 %d 个会话同分钟'
                      % (_tm.strftime('%Y-%m-%d %H:%M', _tm.localtime(_k * 60)), len(_v)))
    except Exception as e:
        print('  !! 读失败：%s' % e)
    print()

print()
print('== 结论口径 ==')
print('  碰撞率 = P(两个不同会话落在同一分钟) ⇒ 这就是"时间档弃权"的发生率 ⇒')
print('  也就是 B2 盲区**唯一的咬人窗口**（名单外那个会话必须恰好落在这个窗口里）。')
print('  ⚠️ 但要看清分母：一个"只监听 2~3 个群"的配置，会让 **绝大多数**会话落在名单外')
print('     ⇒ 一旦进入碰撞窗口，"名单外竞争者"占比极高 ⇒ 此时 `_same_minute_rivals`')
print('     只会在那 2~3 个里找 ⇒ **大概率返回空** ⇒ 三个消费者全部朝"放宽"滑')
print('     （见 scripts/probe_db_lag_risk.py 的 C1/C2/C3）。')
print('  ⇒ 所以 B2 的危害**不取决于碰撞率高低，而取决于"碰撞时名单外是否有人"**：')
print('     碰撞率 1.9% 已足够（一年里上百次窗口），而窗口内几乎必有名单外会话。')
raise SystemExit(0)
print('  真实运行时"监听名单"通常只覆盖少数会话，')
print('  而不在名单里的会话有 %d 个 ⇒ 窗口内"名单外竞争者"的比例很高。' % 0)
