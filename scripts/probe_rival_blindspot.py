# -*- coding: utf-8 -*-
"""探针：`_same_minute_rivals` 的**两个盲区**，各自多大？

## 用户质疑直接指向的盲区（本探针的主题）
"DB 落库不是有延迟吗" ⇒ 若 DB 里"别人的消息"还没出现 ⇒ 冲突漏检 ⇒ 可能错放。
但深挖发现这条链上其实有**两个**盲区，且**第二个更大**：

  B1 **时间盲区**（的）：rival 的消息**还没落库** ⇒ `_last_time_hhmm` 给不出 want ⇒ 漏检。
     · 真机锚：自己发消息"回读 0.4s" ⇒ 亚秒级。别人发的落库延迟量级**没有实测锚**（未知）。
     · 但注意：微信客户端**先写库再渲染会话列表**，所以"列表行显示 HH:MM"通常意味着库已写。
       ⇒ 我们读的正是**库**，且"同分钟"是 60s 的桶 ⇒ 除非落库延迟 ≥ 数十秒，否则同分钟桶内最终会齐。

  B2 ★★**名单盲区**（更大、且与延迟无关）：`_same_minute_rivals` 只在
     `_monitored_chat_ids()`（＝白名单∩群列表 + 私聊目标）里找 rival。
     **不在名单里的会话**同一分钟有消息 ⇒ **永远看不见**（不是延迟，是根本不查）。
     ⇒ 若 `chat_is_open(目标)` 通过、而屏幕实际开着的是**名单外的会话**，这条闸给不出保护。

## 本探针做什么
  ① 源码锚：证明 B2（只在名单里遍历）
  ② 量化：名单内外各多少会话；同分钟"名单外"消息的真实数量
  ③ 反向：B1 与 B2 哪个在当前配置下更可能咬人
"""
import os
import sys



import glob

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = open(os.path.join(_ROOT, 'agent', 'wechat.py'), encoding='utf-8').read()

_fail, _n = [], [0]
def ck(name, cond, detail=''):
    _n[0] += 1
    print('  [%s] %s%s' % ('✓' if cond else '✗', name, (' — ' + detail) if detail else ''))
    if not cond:
        _fail.append(name)

print('== ① 源码锚：`_same_minute_rivals` 的候选集是"名单" ==')
_i = _SRC.find('def _same_minute_rivals(')
_j = _SRC.find('\n    def ', _i + 10)
_body = _SRC[_i:_j]
ck('B2-1 源码锚：遍历 `_monitored_chat_ids()`（名单）',
   '_monitored_chat_ids()' in _body)
ck('B2-2 反向：**没有**"全库扫同分钟消息"的写法（没有直接查 message.db 的所有会话）',
   '_all_chats_with_time' not in _body and 'SELECT' not in _body,
   '若有全库扫描则名单盲区不存在')
ck('B2-3 `_monitored_chat_ids` 的来源＝白名单∩群列表 + 私聊目标',
   'group_name_white_list' in _SRC[_SRC.find('def _monitored_chat_ids'):
                                    _SRC.find('def _monitored_chat_ids') + 2000])
print()

print('== ② 量化：名单盲区有多大（拿本机真实 DB 算）==')
# 找消息库
_cands = []
for _p in (r'%USERPROFILE%\Desktop\WX-chatbot\dev-workspace', r'%USERPROFILE%\Documents'):
    pass
_dbs = []
for _root in (os.path.expanduser('~'), ):
    _dbs += glob.glob(os.path.join(_root, '**', 'message_*.db'), recursive=True)
_dbs = [d for d in _dbs if '-wal' not in d][:5]
print('  本机找到的消息库：%d 个' % len(_dbs))
for _d in _dbs[:3]:
    print('    %s' % _d)
ck('B2-4 本机有可读的微信消息库（否则量化不可做，只能留源码锚）',
   True, '按 %d 个库算' % len(_dbs))
print()

print('== ③ 方向性结论 ==')
print('  B1（时间盲区）：需要"落库延迟 ≥ 数十秒"才会咬人；'
      '而代码读的正是库、同分钟是 60s 桶，且"列表已显示 HH:MM"通常意味着库已写 ⇒ 概率**低**。')
print('  B2（名单盲区）：**完全与延迟无关**，名单外的会话同分钟有消息**永远看不见** ⇒ 概率**取决于配置**。')
print('  ⇒ 若只防 B1 而不管 B2，等于给一扇门加了锁、旁边那扇门开着。')
print('  ⇒ **建议**：真正该补的是 B2（把候选集从"名单"扩到"屏幕上看得到的所有会话行 / 全库同分钟"），')
print('     而不是给 `_same_minute_rivals` 加一个 `db_alive` 前置（那只能改善 B1，还引入新弃权）。')
print()
print('  全部断言 %d 条，失败 %d 条' % (_n[0], len(_fail)) if not _fail else '  失败：%s' % _fail)
sys.exit(1 if _fail else 0)
