# -*- coding: utf-8 -*-
"""probe_b2_live_verify.py —— 对**活微信真实库**验证 `_all_chat_last_hhmm`（B2 修法的端到端集成检查）

⛔ 为什么单开一支：`scripts/probe_b2_realdata.py` 用的是 `%TEMP%\\wechatauto_db\\` 的**离线副本**
   （那个副本的最新消息不是"今天"），只能证明「碰撞窗口里名单外占 99%」这个**结论**；
   却证明不了**新函数本身**在真库上跑得通 —— 它有可能是个"永远返回空 dict 的空壳"，
   而空壳也能让 I3/I5 那两条自测通过（因为自测用的是桩）。

   本支要回答的是：**在真的会返回非空的库上，它到底返回什么**。
   活微信目录：`M:\\WX\\talk\\xwechat_files\\`（账号 `wxid_<本机号>_e7c2`）。

本支的**判定口径**（无立场、可复核）：
  V1 `open_db` 能开、`_message_dbs` 非空、`_build_md5_index` 非空（前提成立）
  V2 逐表 `MAX(create_time)` 能读出真实时间戳（证明读得到，不是读空）
  V3 `_all_chat_last_hhmm()` 的返回 **严格等于**「独立手算的今日会话集合」
     —— 手算侧不走被测函数、不共用任何代码路径
  V4 若今日确实无消息 ⇒ 要求返回空 dict **且**手算也为空（不许把"读不到"混进"今天没有"）
  V5 缓存生效：第二次调用耗时 << 第一次
  V6 反例锚：**把今日过滤摘掉**，同一库上必须能返回非空 ⇒ 证明"返回空"是过滤判对了，
     不是函数整体读不到（这条最关键，专治"空壳假通过"）
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import agent.wechat as W  # noqa: E402
import logging

log = logging.getLogger("persona-morph")

LIVE = r"M:\WX\talk\xwechat_files\wxid_<本机号>_e7c2"

_n = 0
_bad = 0


def ck(name, cond, extra=""):
    global _n, _bad
    _n += 1
    if cond:
        print("  PASS %s" % name)
    else:
        _bad += 1
        print("  FAIL %s  [%s]" % (name, extra))
    return bool(cond)


def main():
    print("=" * 78)
    print("对**活微信真实库**验证 `_all_chat_last_hhmm`（B2 端到端）")
    print("库目录：%s" % LIVE)
    print("=" * 78)

    if not os.path.isdir(LIVE):
        print("  ⛔ 活目录不存在 ⇒ 本支无法执行（不冒充通过）")
        print("== 汇总：%d 通过 / %d 失败 ==" % (_n, _bad))
        raise SystemExit(1)

    _ad = W.WeChatAdapter.__new__(W.WeChatAdapter)
    try:
        # ⚠️ `open_db()` 返回的是 **(db, how, errors)** 三元组，不是 db 本身（踩过一次）
        _db, _how, _errs = W.open_db(LIVE)
    except Exception as _e:
        print("  ⛔ open_db 抛异常：%s" % _e)
        print("== 汇总：%d 通过 / %d 失败 ==" % (_n, _bad))
        raise SystemExit(1)
    if _db is None:
        print("  ⛔ open_db 全路失败，errors=%s" % _errs)
        print("== 汇总：%d 通过 / %d 失败 ==" % (_n, _bad))
        raise SystemExit(1)
    _ad._db = _db
    print("▸ 账号：%s（来源 %s / 目录 %s）"
          % (_how.get("account") or "?", _how.get("src"), _how.get("account_dir")))
    print("▸ 选号理由：%s" % (_how.get("account_why") or "-"))

    _dbs = list(_db._message_dbs() or [])
    _idx = {}
    try:
        _idx = dict(_db._build_md5_index() or {})
    except Exception:
        pass
    ck("V1 前提成立：能开库、有分片、md5 反查索引非空",
       bool(_dbs) and bool(_idx), "%d 分片 / %d 索引" % (len(_dbs), len(_idx)))

    # ── 独立手算：完全不碰被测函数 ──────────────────────────────────
    _today = time.localtime().tm_yday
    print("▸ 今天 yday=%d（%s）" % (_today, time.strftime("%Y-%m-%d %H:%M")))
    _hand = {}       # {裸id: "HH:MM"} —— 只收今天
    _hand_all = {}   # {裸id: epoch}  —— 不过滤，做反例锚用
    _raw_hits = 0
    for _rel in _dbs:
        _conn = None
        try:
            _conn = _db._open(_rel)
            _tabs = [r[0] for r in _conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
            ).fetchall()]
            for _tb in _tabs:
                _md5 = _tb[4:]
                _bid = _idx.get(_md5)
                if not _bid:
                    continue
                try:
                    _v = _conn.execute('SELECT MAX(create_time) FROM "%s"' % _tb).fetchone()[0]
                except Exception as _e:
                    log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                    continue
                if not _v:
                    continue
                _v = int(_v)
                if _v > 10 ** 10:
                    _v //= 1000
                _raw_hits += 1
                _bid = str(_bid)
                _hand_all[_bid] = _v
                _lt = time.localtime(_v)
                if _lt.tm_yday == _today:
                    _hand[_bid] = time.strftime("%H:%M", _lt)
        finally:
            try:
                if _conn is not None:
                    _conn.close()
            except Exception:
                pass

    print("▸ 手算：%d 张表读到了真实 MAX(create_time)" % _raw_hits)
    ck("V2 真读得到时间戳（不是读空）", _raw_hits > 0, "读到 %d 张" % _raw_hits)

    if _hand_all:
        _mx = max(_hand_all.values())
        print("▸ 全库最新一条：%s（%s）"
              % (time.strftime("%Y-%m-%d %H:%M", time.localtime(_mx)),
                 time.strftime("%H:%M", time.localtime(_mx))))
    if _hand:
        print("▸ 手算「今天有消息」的会话 %d 个：" % len(_hand))
        for _k, _hh in sorted(_hand.items(), key=lambda x: x[1])[:12]:
            print("      %-44s %s" % (_k[:44], _hh))
    else:
        print("▸ 手算：今天**确实没有**任何会话有消息 ⇒ 期望被测函数也返回空")

    # ── 被测函数 ──────────────────────────────────────────────
    _ad._allch_cache = None
    _t0 = time.time()
    _got = _ad._all_chat_last_hhmm()
    _d1 = (time.time() - _t0) * 1000
    _t1 = time.time()
    _got2 = _ad._all_chat_last_hhmm()
    _d2 = (time.time() - _t1) * 1000
    print("▸ `_all_chat_last_hhmm()` → %d 个会话，首调 %.1fms / 二调 %.1fms"
          % (len(_got), _d1, _d2))

    ck("V3 被测函数返回值 **严格等于** 独立手算的今日集合",
       dict(_got) == dict(_hand),
       "got=%s hand=%s" % (dict(_got), dict(_hand)))

    if not _hand:
        ck("V4 今日无消息 ⇒ 返回空 dict（与手算一致，没把「读不到」混进来）",
           len(_got) == 0, str(_got))
    else:
        ck("V4 今日有消息 ⇒ 返回非空（函数**不是空壳**）", len(_got) > 0, str(_got))

    ck("V5 缓存生效：第二次调用 ≤ 第一次（或都极快）", _d2 <= _d1 + 1.0,
       "%.1fms → %.1fms" % (_d1, _d2))

    # ── V6 ★反例锚：证明"返回空"是过滤判对了，而非整体读不到 ──
    _mk = max(_hand_all.values()) if _hand_all else 0
    ck("V6 ★反例锚：全库最新一条**不在今天**（若非空则说明是函数过滤掉的，不是读不到）",
       (not _hand) and (_mk > 0) and (time.localtime(_mk).tm_yday != _today),
       "最新=%s" % (time.strftime("%Y-%m-%d %H:%M", time.localtime(_mk)) if _mk else "无"))

    print("-" * 78)
    print("▸ V7 正向实证：活库今天恰好没消息 ⇒ 上面全是「过滤对了」，还差一条")
    print("      「今天**真有人发**时它能不能返回非空」。")
    print("      ⚠️ 活库分片是 SQLCipher **加密**的（复制出来 `file is not a database`）")
    print("         ⇒ 改用已解密的离线副本 `%TEMP%\\wechatauto_db\\`，把最活跃那张表的")
    print("         最新一条 create_time 改成「现在」，再跑被测函数。只动临时副本。")
    _live_done = _v7_forward(_db, _dbs, _idx, _today)

    print("=" * 78)
    print("== 汇总：%d 通过 / %d 失败 ==" % (_n, _bad))
    print("=" * 78)
    raise SystemExit(1 if _bad else 0)


def _v7_forward(_db, _dbs, _idx, _today):
    """在**临时副本**上把某会话最后一条改成"现在" ⇒ 验证被测函数返回非空且值正确。

    隔离纪律：只复制**单个分片文件**到 `%TEMP%`，改完即删；不碰活库任何字节。
    若连一个可写的分片都拿不到 ⇒ 记 FAIL 并说明（不冒充通过）。
    """
    import shutil
    import sqlite3
    _rel = _dbs[0] if _dbs else ""
    # ⚠️ 分片真实位置 = `<账号目录>/db_storage/<rel>`（`_rel` 形如 `message\message_0.db`）
    #    —— 踩过：一开始只拼 `账号目录/message/...`，漏了 `db_storage` 这一层。
    # ⚠️ 但**活**库分片是 SQLCipher 加密的 ⇒ 复制出来 `file is not a database`。
    #    ⇒ 这里干脆不用活库，改用**已解密的离线副本**（`%TEMP%\\wechatauto_db\\<账号>\\`
    #      里是平铺的 `message__message_0.db`）。跑得通才说明函数本身没问题。
    _tmp_root = os.path.join(os.environ.get("TEMP", "."), "wechatauto_db")
    _acct = str(getattr(_db, "account", "") or "")
    _src = ""
    for _cand in (
        os.path.join(_tmp_root, _acct, "message__message_0.db"),
        os.path.join(_tmp_root, _acct, "message__message_0.DB"),
    ):
        if os.path.isfile(_cand):
            _src = _cand
            break
    if not _src and os.path.isdir(os.path.join(_tmp_root, _acct)):
        for _f in os.listdir(os.path.join(_tmp_root, _acct)):
            if _f.lower().startswith("message__message_") and _f.lower().endswith(".db"):
                _src = os.path.join(_tmp_root, _acct, _f)
                break
    if not _src:
        ck("V7 ★正向实证：能拿到可读的解密副本", False,
           "离线副本里没有 message__message_*.db（活库是加密的，不能直接用）")
        return False
    _rel_for_tiny = "message\\" + os.path.basename(_src).replace("message__", "", 1)
    _tmp = os.path.join(os.environ.get("TEMP", "."), "_b2_v7_copy.db")
    try:
        shutil.copy2(_src, _tmp)
    except Exception as _e:
        ck("V7 ★正向实证：能拿到可写副本", False, "复制失败：%s" % _e)
        return False
    try:
        _c = sqlite3.connect(_tmp)
        _tabs = [r[0] for r in _c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
        ).fetchall()]
        # 挑那张 MAX 最大的（最活跃），把它改成"现在"
        _best, _best_v = "", 0
        for _tb in _tabs:
            try:
                _v = _c.execute('SELECT MAX(create_time) FROM "%s"' % _tb).fetchone()[0]
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                continue
            if _v and int(_v) > _best_v:
                _best, _best_v = _tb, int(_v)
        if not _best:
            ck("V7 ★正向实证：副本里有可改的表", False, "无 Msg_ 表")
            return False
        _bid = _idx.get(_best[4:]) or "(索引反查失败)"
        _now = int(time.time())
        _c.execute('UPDATE "%s" SET create_time=? WHERE create_time=?' % _best, (_now, _best_v))
        _c.commit()
        _c.close()

        # 用一个"手搓的极小 adapter"指向这个副本：只覆盖 _db，其它一律不带
        class _TinyDB(object):
            def __init__(self, path):
                self.account = "V7副本"
                self.account_dir = os.path.dirname(path)
                self.db_dir = self.account_dir
                self._path = path

            def _message_dbs(self):
                return [_rel_for_tiny]  # 与真实实现同形（`message\message_0.db`）

            def _open(self, _r):
                return sqlite3.connect(self._path)

            def _build_md5_index(self):
                return dict(_idx)

        _ad = W.WeChatAdapter.__new__(W.WeChatAdapter)
        _ad._db = _TinyDB(_tmp)
        _ad._allch_cache = None
        _g = _ad._all_chat_last_hhmm()
        _want_hh = time.strftime("%H:%M")
        _got_ok = (_bid in _g) and (_g[_bid] == _want_hh)
        print("      ▸ 改的是表 %s（裸 id %s）⇒ 期望今天 %s" % (_best, _bid, _want_hh))
        print("      ▸ 被测函数返回：%s" % (dict(_g),))
        ck("V7 ★正向实证：把最后一条改成「现在」后，被测函数**返回非空且值正确**"
           "（证明它真能读出今天的会话，不是空壳）", _got_ok,
           "got=%s want=%s@%s" % (dict(_g), _bid, _want_hh))
        return True
    finally:
        try:
            os.remove(_tmp)
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass  # 清理型：静默合法


if __name__ == "__main__":
    main()
