#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据：**整批拒发 ⇒ 入队一次**（E5 会话回放干跑）。

跑法： runtime\\python\\python.exe scripts\\e5_replay_selftest.py   退出码 0=全过 / 1=有失败

为什么（缺陷回顾）：
  身份类拒发（"当前开着的很可能不是目标会话"）在发送层是**整批抛异常**——
  `sender.Sender.send_text_batch`：`if failed and not sent: raise RuntimeError(...)`。
  调用方 `tools._exec_send_message` 因此**走 `except` 分支**，而"进重试队列
  （`send_retry.enqueue`）"原先只写在"部分失败"分支里 ⇒ 这类拒发**从不入队**
  （`_queued` 恒 0），生产里"判不了就晚点补发"等于不存在。
  主线已在 `except` 分支补上入队入口；本判据用**回放**给它钉一条可复跑的证据。

回放链路（谁被替身 / 替身什么行为）：
  · **替身**：`ctx["sender"].send_text_batch` 被换成一个**直接 raise** 的假对象；
    raise 的文案按 `send_retry.retryable()` 的真实规则造（认发送层打的 `【可重试】`
    前缀，**不猜关键词**）。`tools._exec_send_message` 与 `send_retry` 全用**真模块**，
    链路里唯一被换掉的就是"真实微信发送"这一步。
  · **数据面**：`WX_AGENT_DATA_DIR` 指到临时目录 ⇒ `config.DATA_DIR` 与
    `send_retry.PATH` 都落在临时目录，产品 `data/` 一个字节都不动。

三条用例：
  ① 整批拒发（可重试）⇒ 错误返回 + 队列**恰好 +1** + 落在临时目录；
  ② 整批拒发（**不可重试**，如内容问题）⇒ 不入队；
  ③ 可重试失败但 `messages` 为空 ⇒ 不崩、不入队（含打 `except` 里那条
     `_tx.strip()` 空条目闸的定向探针）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ⛔ 必须在 import agent.* 之前改数据面：`config.DATA_DIR` / `send_retry.PATH`
#   都是在模块导入时按下标算好的常量，晚一步改就来不及。
_TMP_DATA = tempfile.mkdtemp(prefix="pm-e5-")
os.environ["WX_AGENT_DATA_DIR"] = _TMP_DATA

from agent import config as CFG     # noqa: E402
from agent import send_retry as SR  # noqa: E402
from agent import tools as T        # noqa: E402

PROD_QUEUE = os.path.join(ROOT, "data", "send_retry.json")
PASS, FAIL = [], []


def ok(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(extra) + "]") if extra else ""))


def _pending():
    """公开读数：队列里现有多少条待重发。"""
    return int(SR.stats().get("pending") or 0)


def _disk_items():
    """落盘读数：直接读队列文件，确认"入队一次"真的落了盘（不只是内存）。"""
    try:
        with open(SR.PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        return list((d.get("items") if isinstance(d, dict) else d) or [])
    except FileNotFoundError:
        return []


class _FakeSender:
    """替身：把"发送这一批"替换成**抛一段可/不可重试的失败原文**。

    保持 `send_text_batch(chat_key, messages, **kw)` 签名，其余什么都不做——
    真实微信、真实 store、真实 risk 闸全都不经过。
    """

    def __init__(self, err):
        self.err = str(err)
        self.calls = []

    def send_text_batch(self, chat_key, messages, **kw):
        self.calls.append({"chat_key": chat_key, "messages": list(messages)})
        raise RuntimeError(self.err)


class _FakeStore:
    """最小 store：仅覆盖 `_exec_send_message` 在传了 reply_to_message_id 时才会用到的读数。

    本判据不传 reply_to_message_id（那条路径与入队无关），所以这里恒返回 None。
    """

    def find_by_mid(self, *a, **k):
        return None


def _ctx(sender, chat_key):
    return {"chat_key": chat_key, "sender": sender, "store": _FakeStore(),
            "session": {"sent": []}}


def main():
    _prod_before = os.path.exists(PROD_QUEUE)
    print("临时数据目录：%s" % _TMP_DATA)
    print("产品队列文件：%s（跑前存在=%s）" % (PROD_QUEUE, _prod_before))

    print("\n── 0. 数据面隔离（一切只落临时目录）──")
    ok("0.1 `config.DATA_DIR` 被指到临时目录", os.path.abspath(CFG.DATA_DIR) == os.path.abspath(_TMP_DATA), CFG.DATA_DIR)
    ok("0.2 `send_retry.PATH` 落在临时目录（队列落盘不碰产品 data/）",
       os.path.abspath(SR.PATH).startswith(os.path.abspath(_TMP_DATA) + os.sep), SR.PATH)
    ok("0.3 队列起点为空（干净基线）", _pending() == 0, _pending())

    # 回放的输入形状要和真发送层的 `raise` 文案对齐（否则就是"造了一个生产里不会出现的东西"）
    # ⚠️ 这里只锚"分支存在 + 用「；」拼原因后 raise"，**不锚某一行的字面写法** ——
    #    发送层把拼接抽成中间变量（便于同时喂给版本门自证）是等价改动，锚太死会假红。
    _src = open(os.path.join(ROOT, "agent", "sender.py"), encoding="utf-8").read()
    ok("0.4 真发送层确实是「全失败即 raise」（回放 raise 的形状与它一致）",
       "if failed and not sent:" in _src and "raise RuntimeError(" in _src
       and '"；".join(' in _src,
       "sender.send_text_batch 的 `failed and not sent` 分支")

    _RETRY_ERR = SR.RETRY_MARK + "当前开着的很可能不是目标会话，拒绝投递（防发错会话）"
    ok("0.5 回放用的失败原文按 `retryable()` 真实规则造（认前缀，不猜关键词）",
       SR.retryable(_RETRY_ERR) is True, _RETRY_ERR)
    _PLAIN_ERR = "第1条「这条内容有问题」：消息内容为空"
    ok("0.6 反向用例的失败原文**不可重试**（不是发错会话，是内容问题）",
       SR.retryable(_PLAIN_ERR) is False, _PLAIN_ERR)

    print("\n── ① 整批拒发（可重试）⇒ 错误返回 + 队列恰好 +1 ──")
    _ck = "group:e5_replay_once"
    _txt = "E5 回放：这条整批被判不了现场"
    _snd = _FakeSender("第1条「%s」：%s" % (_txt[:20], _RETRY_ERR))
    _n0 = _pending()
    _res = T._exec_send_message(_ctx(_snd, _ck), {"messages": [_txt]})
    _n1 = _pending()
    ok("1.1 真发送层被调到（替身行使「发送这一步」）", len(_snd.calls) == 1 and _snd.calls[0]["messages"] == [_txt], _snd.calls)
    ok("1.2 返回值是错误（`is_error` 口径）",
       bool(_res.get("is_error")) is True and str(_res.get("content") or "").startswith("错误："),
       str(_res.get("content"))[:90])
    ok("1.3 错误文案里点名「已排进重试队列」（不让模型重发）",
       "已排进重试队列" in str(_res.get("content") or ""), str(_res.get("content"))[:140])
    ok("1.4 队列**恰好多了 1 条**（公开读数 `stats().pending`）", _n1 - _n0 == 1, "%d→%d" % (_n0, _n1))
    _it = _disk_items()
    ok("1.5 落盘的正是这条的**原文**与本次会话 key（不是前 20 字盲找）",
       len(_it) == 1 and str(_it[0].get("text")) == _txt and str(_it[0].get("chat_key")) == _ck,
       str(_it[:1])[:160])
    # 独立 ctx 复核 session.sent：整批失败**不许**把任何一条记成"已发送"
    _snd2 = _FakeSender("第1条「%s」：%s" % (_txt[:20], _RETRY_ERR))
    _ctx2 = _ctx(_snd2, "group:e5_replay_once2")
    T._exec_send_message(_ctx2, {"messages": [_txt + "（第二条）"]})
    ok("1.6 没有把「失败的」记成「已发送」（session.sent 仍为空）",
       _ctx2["session"]["sent"] == [], _ctx2["session"]["sent"])

    print("\n── ①b 整批拒发（可重试）多条目 ⇒ 逐条入队（N 条 = +N）──")
    _ck_b = "group:e5_replay_batch"
    _msgs = ["E5 批量甲", "E5 批量乙", "E5 批量丙"]
    _snd3 = _FakeSender("；".join("第%d条「%s」：%s" % (i + 1, _m[:10], _RETRY_ERR) for i, _m in enumerate(_msgs)))
    _nb0 = _pending()
    T._exec_send_message(_ctx(_snd3, _ck_b), {"messages": _msgs})
    _nb1 = _pending()
    ok("1b 整批失败 ⇒ 这一批每一条都排进队列（3 条 = +3）", _nb1 - _nb0 == 3, "%d→%d" % (_nb0, _nb1))

    print("\n── ② 整批拒发（不可重试）⇒ 不入队 ──")
    _ck2 = "group:e5_replay_plain"
    _snd4 = _FakeSender(_PLAIN_ERR)
    _m0 = _pending()
    _res4 = T._exec_send_message(_ctx(_snd4, _ck2), {"messages": ["这条是内容问题，不该补发"]})
    _m1 = _pending()
    ok("2.1 仍按错误返回（`is_error`）", bool(_res4.get("is_error")) is True, str(_res4.get("content"))[:90])
    ok("2.2 队列**不变**（不可重试的失败不排队）", _m1 - _m0 == 0, "%d→%d" % (_m0, _m1))
    ok("2.3 错误文案里**没有**「已排进重试队列」这层承诺",
       "已排进重试队列" not in str(_res4.get("content") or ""), str(_res4.get("content"))[:140])

    print("\n── ③ 可重试但 `messages` 为空 ⇒ 不崩、不入队 ──")
    _ck3 = "group:e5_replay_empty"
    _m0 = _pending()
    _crashed = None
    try:
        _res5 = T._exec_send_message(_ctx(_FakeSender(_RETRY_ERR), _ck3), {"messages": []})
    except Exception as e:  # noqa: BLE001
        _crashed, _res5 = e, {}
    _m1 = _pending()
    ok("3.1 空消息列表**不抛**（异常没漏出去）", _crashed is None, repr(_crashed))
    ok("3.2 空消息 ⇒ 直接按「消息内容为空」错误返回（连发送层都不进）",
       bool(_res5.get("is_error")) is True and "消息内容为空" in str(_res5.get("content") or ""),
       str(_res5.get("content"))[:90])
    ok("3.3 队列**不变**（空消息不排队）", _m1 - _m0 == 0, "%d→%d" % (_m0, _m1))

    # 定向探针：`except` 分支里的 `if _tx.strip()` 闸。
    # ⚠️ 公开入口下这条闸**够不到**——`normalize_message_list` 已把空串预先滤掉，
    #    "空列表"在入口就早退了。要打到它只能把"归一化"这一步也换成替身（下面这行
    #    就是那个替身：返回一个"过不了 strip"的条目）。这是**在测那句守卫本身**，
    #    不代表生产里必然经过它；报告里已如实标注。
    _m0 = _pending()
    _orig_norm = T.normalize_message_list
    _crashed2 = None
    try:
        T.normalize_message_list = lambda _v: ["   "]  # 空条目：绕过入口早退，逼 `except` 里的 strip 闸出手
        _res6 = T._exec_send_message(_ctx(_FakeSender(_RETRY_ERR), "group:e5_replay_blank"), {"messages": ["   "]})
    except Exception as e:  # noqa: BLE001
        _crashed2, _res6 = e, {}
    finally:
        T.normalize_message_list = _orig_norm
    _m1 = _pending()
    ok("3.4 空条目（strip 后为空）⇒ 不崩、不入队（`except` 里的 `_tx.strip()` 闸生效）",
       _crashed2 is None and (_m1 - _m0) == 0 and bool(_res6.get("is_error")) is True,
       "crash=%r pending %d→%d" % (_crashed2, _m0, _m1))

    print("\n── ④ 全程不碰产品 data/ ──")
    ok("4.1 跑完产品队列文件**仍不存在**（与跑前一致）", os.path.exists(PROD_QUEUE) == _prod_before,
       "before=%s after=%s" % (_prod_before, os.path.exists(PROD_QUEUE)))
    ok("4.2 本次所有队列写入都在临时目录里", os.path.abspath(SR.PATH).startswith(os.path.abspath(_TMP_DATA) + os.sep),
       SR.PATH)

    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    try:
        _rc = main()
    finally:
        shutil.rmtree(_TMP_DATA, ignore_errors=True)
    sys.exit(_rc)
