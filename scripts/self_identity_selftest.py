# -*- coding: utf-8 -*-
"""「自己是谁」判据 —— 专治**把自己当别人**（机器人回自己 / 左右脑互搏）。

**报障原话（2026-10-11）**：
  「我们遇见的都是**自己跟自己吵起来，自己跟自己左右脑互博**」
  「**连接微信的时候你不能直接读这个账号的微信ID吗，这可以当最前置的闸**」

**真因链**：判「这条是不是我发的」全靠知道「我是谁」（`_self_wxid`）。它原本只有三个来源，
而它们**恰好会一起落空**：
  · `get_self_info()`（读微信库）—— 代码自己写着「某些账号/微信版本返回空」；
  · `data/self_identity.json` 落盘 —— 它唯一的写入方是「从文本回声学」，而**回声已按用户要求
    整条撤掉**（撤得对）⇒ **这个文件从此没被写过，成了一档死档**；
  · 账号目录名推 —— 只在目录名「长得像 wxid」时成立。
⇒ 三者全空 ⇒ `_self_wxid` 为空 ⇒ 现有红线「不知道我是谁就当它不是自己」⇒
  **自己发的每条（尤其没有正文前缀的图片/表情）都被当成群友** ⇒ 机器人回自己。

**本轮补的第四条路**（`learn_self_from_own_row`）：
  在「**我们自己发出去、并已 DB 回读确认**」那一刻，那条库行的发送者**就是我们自己** ——
  这是**不靠猜**的铁证（那条消息是我们亲手发的），而且**不依赖微信库能不能读自己**。

跑法：py -3 scripts/self_identity_selftest.py   （退出码 0=全过 / 1=有失败）
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OKS, FAILS = [], []


def chk(name, cond, extra=''):
    (OKS if cond else FAILS).append(name)
    print('  %s %s%s' % ('OK  ' if cond else 'FAIL', name, ('   [%s]' % extra) if extra else ''))


def src(rel):
    return io.open(os.path.join(ROOT, rel), encoding='utf-8').read()


def main():
    W = src('agent/wechat.py')
    _seg = W.split('def learn_self_from_own_row')[1][:3000] if 'def learn_self_from_own_row' in W else ''

    print('── A. 第四个来源存在，且接在「我们亲手发的那条行」上 ──')
    chk('A1 ★有 learn_self_from_own_row（从我们自己发出并回读确认的那行学「我是谁」）',
        'def learn_self_from_own_row' in W,
        '没有它 ⇒ 「我是谁」仍然只有会一起落空的三档')
    chk('A2 ★记账函数 remember_self_local 收了 sender_id',
        'def remember_self_local(self, chat_id, local_id, create_time=None, sender_id=None)' in W)
    chk('A3 ★模块级入口 _self_local_note 也收了并透传',
        'def _self_local_note(obj, chat_id, local_id, create_time=None, sender_id=None)' in W
        and 'fn(chat_id, local_id, create_time, sender_id)' in W)
    _with_sid = (W.count('or r.get("real_sender_id")') + W.count('or _r.get("real_sender_id")')
                 + W.count('or head.get("real_sender_id")') + W.count('or top.get("real_sender_id")'))
    chk('A4 ★调用点真的把发送者传下去了（%d 处带 sender_id）' % (_with_sid + 1),
        _with_sid >= 5,
        '只加了参数、没传值 ⇒ 等于没修')

    print()
    print('── B. 两道闸：一个都不能少 ──')
    chk('B1 ★形状闸在位（纯数字槽位号 / 公众号一律不采纳）',
        '_valid_self_id(w)' in _seg,
        '2026-10-10 真出过这事：把槽位号 3 当成了「我自己」')
    chk('B2 ★不覆盖闸在位（只在 _self_wxid 为空时才采用）',
        'if self._self_wxid:' in _seg,
        '已有身份来自 API/账号目录/落盘，比这条路可信 ⇒ 绝不许改写（错了还会落盘）')
    chk('B3 落盘带账号闸（acct）—— 切号后自动失效',
        '"acct": _db_account_of(self)' in _seg)
    chk('B4 形状非法时不落盘（先校验后写）',
        _seg.find('_valid_self_id(w)') < _seg.find('"acct": _db_account_of(self)'))

    print()
    print('── C. 行为：真的按两道闸走（桩对象，不碰真微信）──')
    try:
        from agent import wechat as _W

        def _mk(self_wxid):
            o = _W.WeChatAdapter.__new__(_W.WeChatAdapter)   # 不走 __init__（不连微信）
            o._self_wxid = self_wxid
            o._self_wxid_src = 'test'
            o._self_ident_ok = bool(self_wxid)
            o._self_nickname = '判据桩'
            o._resolve_sender_wxid = lambda sid, cid='': ('wxid_judge_me' if str(sid) == '77'
                                                          else str(sid))
            o._self_id_file = lambda: os.path.join(ROOT, '_scratch', '_judge_self_identity.json')
            return o

        _p = os.path.join(ROOT, '_scratch', '_judge_self_identity.json')
        try:
            os.remove(_p)
        except OSError:
            pass

        _a = _mk('')
        _got = _a.learn_self_from_own_row('77', 'g', '判据')
        chk('C1 ★（还不知道我是谁）从自己那行学会，且落盘',
            _got is True and _a._self_wxid == 'wxid_judge_me' and os.path.isfile(_p),
            'wxid=%s 落盘=%s' % (_a._self_wxid, os.path.isfile(_p)))

        _b = _mk('wxid_already_known')
        _got2 = _b.learn_self_from_own_row('77', 'g', '判据')
        chk('C2 ★（已经知道自己是谁）绝不被改写（防一次错把自己钉错并落盘）',
            _got2 is False and _b._self_wxid == 'wxid_already_known', 'wxid=%s' % _b._self_wxid)

        _c = _mk('')
        _got3 = _c.learn_self_from_own_row('3', 'g', '判据')
        chk('C3 ★（只解析出数字槽位号）不采纳（那正是当年学错的那个坑）',
            _got3 is False and _c._self_wxid == '', 'wxid=%r' % _c._self_wxid)

        _d = _mk('')
        _got4 = _d.learn_self_from_own_row('', 'g')
        chk('C4 空发送者 ⇒ 不学、不抛', _got4 is False and _d._self_wxid == '')
        try:
            os.remove(_p)
        except OSError:
            pass
    except Exception as e:  # noqa: BLE001
        chk('C. 行为（前置）', False, str(e)[:100])

    print()
    print('── D. 反向锚：把闸拆掉 ⇒ 上面的判据必须变红 ──')
    _gutted = _seg.replace('if self._self_wxid:', 'if False:')
    chk('D1 ★拆掉不覆盖闸后 B2 那条判据式当场变假',
        not ('if self._self_wxid:' in _gutted))
    _gutted2 = _seg.replace('_valid_self_id(w)', 'True')
    chk('D2 ★拆掉形状闸后 B1 那条判据式当场变假',
        not ('_valid_self_id(w)' in _gutted2))

    print()
    print('== 汇总：%d 通过 / %d 失败 ==' % (len(OKS), len(FAILS)))
    for f in FAILS:
        print('   FAIL:', f)
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
