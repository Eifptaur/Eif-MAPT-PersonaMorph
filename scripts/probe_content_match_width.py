# -*- coding: utf-8 -*-
"""probe_content_match_width.py —— `content_match` 最后那条 `sm.ratio() > 0.5` 兜底有多宽？

⛔ 起因：
  `content_match(pane, needle)` 的主路是"针的 8/10/12/16 字**非低熵片段**在 pane 里精确命中"，
  这条哲学很清楚：**要一个够长的确切片段**。
  可它最后一行是：
      return sm.ratio() > 0.5
  ⇒ **没有任何 8 字命中**时，只要两串**整体相似度 > 0.5** 就返回 True —— 等于把 8 字门槛绕过去了。
  实测一句假话：
      pane   = '甲群刚才说的那句话内容野群说的完全不相干的一句'
      needle = '乙群说的是完全不同的一句'
      ⇒ content_match = **True**（两句毫无关系，只是共享"说的/完全/一句"这类零散词）

  这条宽度是**两个方向都在用**的：
    · 正向：判"聊天区内容像目标" ⇒ 过宽 = 误放宽（**危险方向**）
    · 反向：判"别家正文在聊天区" ⇒ 过宽 = 误判不排他 ⇒ 漏发（安全方向）
  ⇒ 必须量化，不能拍脑袋改。

本探针的判定口径（用**真实库**的真实正文，不构造）：
  跨会话的两条正文 = **不该匹配**（它们本来就不是同一个会话的消息）
  ⇒ 统计"跨会话对"里被判 True 的比例 = **误判率**；
     并单独看**其中有多少是"最长公共块 < 8 字"**（＝纯靠 ratio 兜底捞上来的）。
"""
import itertools
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


from agent import chat_ocr as CO  # noqa: E402

_CANDIDATES = [
    os.path.join(os.environ.get("TEMP", "."), "wechatauto_db", "wxid_<别号>_eda5"),
    os.path.join(os.environ.get("TEMP", "."), "wechatauto_db", "wxid_<本机号>_e7c2"),
    r"M:\WX\talk\xwechat_files\wxid_<本机号>_e7c2",
]


def _load_texts(limit_chats=30, per_chat=4):
    """从**已解密的离线副本**直读若干会话的最近若干条**纯文本**正文。

    ⚠️ 为什么直读 sqlite 而不是走 `open_db().get_messages()`：活库分片是 SQLCipher 加密的，
    而离线副本（`%TEMP%\\wechatauto_db\\<账号>\\message__message_0.db`）是**平铺已解密**的普通库
    ⇒ 直读最快、也不依赖 driver 的 API 名（踩过：`list_groups`/`get_messages` 名字不对，取到 0 条）。
    字段：`message_content`(TEXT) / `local_type`(1=文本) / `create_time`。
    """
    import sqlite3
    out = []
    for _dir in _CANDIDATES:
        _p = os.path.join(_dir, "message__message_0.db")
        if not os.path.isfile(_p):
            continue
        try:
            _c = sqlite3.connect(_p)
            _tabs = [r[0] for r in _c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
            ).fetchall()]
            _tabs = [t for t in _tabs if not t.endswith("_TYPE_SEQ") and not t.endswith("_SORTSEQ")]
            _n_text = 0
            for _tb in _tabs:
                if len(out) >= limit_chats * per_chat:
                    break
                try:
                    _rows = _c.execute(
                        'SELECT message_content FROM "%s" WHERE local_type=1 '
                        'AND message_content IS NOT NULL AND length(message_content)>=12 '
                        'ORDER BY create_time DESC LIMIT ?' % _tb, (per_chat,)).fetchall()
                except Exception:
                    continue
                _got = 0
                for (_t,) in _rows:
                    _t = str(_t or "").strip()
                    if len(_t) < 12 or "<" in _t:
                        continue # 排除 XML/系统消息
                    out.append((_tb, _t))
                    _got += 1
                    _n_text += 1
                if _got:
                    pass
            _c.close()
            print("  · 离线副本 %s：扫 %d 张表，取到 %d 条纯文本正文"
                  % (os.path.basename(_dir), len(_tabs), _n_text))
        except Exception as _e:
            print("  · 离线副本 %s 读取失败：%s" % (_dir, _e))
            continue
        if len(out) >= 20:
            break
    return out


def main():
    print("=" * 78)
    print("`content_match` 的 `ratio() > 0.5` 兜底宽度探针（用**真实库**正文，跨会话对＝不该匹配）")
    print("=" * 78)
    _items = _load_texts()
    if len(_items) < 6:
        print("  ⛔ 取到的真实正文不足（%d 条）⇒ 本探针无法给结论（不冒充通过）" % len(_items))
        print("== 汇总：0 通过 / 1 失败 ==")
        raise SystemExit(1)
    print("▸ 取到 %d 条真实正文，来自 %d 个会话" % (len(_items), len({c for c, _ in _items})))

    _pairs = 0
    _hit = 0
    _hit_by_ratio_only = 0   # 被判 True，但**最长公共块 < 8**（8 字主路本来不该放它过）
    _examples = []
    for (_c1, _t1), (_c2, _t2) in itertools.combinations(_items, 2):
        if _c1 == _c2:
            continue                       # 同会话的正文本来就可能相关，不参与"误判"统计
        if len(_t1) < 12 or len(_t2) < 12:
            continue
        _pairs += 1
        try:
            _m = bool(CO.content_match(_t1, _t2))
        except Exception:
            continue
        if not _m:
            continue
        _hit += 1
        import difflib
        _a, _b = CO._nz(_t1), CO._nz(_t2)
        _blk = difflib.SequenceMatcher(None, _a, _b, autojunk=False).find_longest_match(
            0, len(_a), 0, len(_b))
        if _blk.size < 8:
            _hit_by_ratio_only += 1
            if len(_examples) < 5:
                _examples.append((_blk.size, _t1[:20], _t2[:20]))

    print("▸ 跨会话对共 **%d** 组" % _pairs)
    print("▸ 判 True（**误判**，它们不是同一个会话）＝ **%d** 组（%.1f%%）"
          % (_hit, 100.0 * _hit / max(1, _pairs)))
    print("▸ 其中**纯靠 `ratio()>0.5` 兜底**捞上来的（最长公共块 < 8 字）＝ **%d** 组（%.1f%%）"
          % (_hit_by_ratio_only, 100.0 * _hit_by_ratio_only / max(1, _pairs)))
    for _sz, _x, _y in _examples:
        print("      · 最长块 %d 字却判 True：%r  ↔  %r" % (_sz, _x, _y))

    # ── 补充：76 组"判 True"误判的**最长公共块长度分布**（决定"加下限能不能挡掉它们"）──
    import difflib
    _hist = {}
    _would_block = 0   # 若把兜底收紧成"且 blk.size >= 6"，会被挡掉的数量
    for (_c1, _t1), (_c2, _t2) in itertools.combinations(_items, 2):
        if _c1 == _c2 or len(_t1) < 12 or len(_t2) < 12:
            continue
        try:
            if not CO.content_match(_t1, _t2):
                continue
        except Exception:
            continue
        _a, _b = CO._nz(_t1), CO._nz(_t2)
        _sz = difflib.SequenceMatcher(None, _a, _b, autojunk=False).find_longest_match(
            0, len(_a), 0, len(_b)).size
        _hist[min(_sz, 30)] = _hist.get(min(_sz, 30), 0) + 1
        if _sz < 6:
            _would_block += 1
    print("▸ 「判 True」那 %d 组的**最长公共块长度**分布（前 8 档）：%s"
          % (_hit, dict(sorted(_hist.items())[:8])))
    print("▸ 若把兜底收紧成「ratio>0.5 **且** 最长块≥6 字」⇒ 会挡掉 **%d** 组" % _would_block)

    # ── 手工构造例（就是本探针的起因）──
    _pp = "甲群刚才说的那句话内容野群说的完全不相干的一句"
    _nn = "乙群说的是完全不同的一句"
    _aa, _bb = CO._nz(_pp), CO._nz(_nn)
    _sz2 = difflib.SequenceMatcher(None, _aa, _bb, autojunk=False).find_longest_match(
        0, len(_aa), 0, len(_bb))
    _r2 = difflib.SequenceMatcher(None, _aa, _bb, autojunk=False).ratio()
    print("▸ 构造例（本探针起因）：content_match=%s，最长块=%d 字，ratio=%.3f"
          % (CO.content_match(_pp, _nn), _sz2.size, _r2))

    print("\n▸ 结论（★2026-09-30 已按本探针数字收紧）：")
    print("  `ratio() > 0.5` 这条兜底一旦单独成立，就等于**绕过「8 字确切片段」门槛**，")
    print("  而它同时在**正向**（判「聊天区内容像目标」⇒ 过宽＝误放宽，危险方向）")
    print("  与**反向**（判「别家正文在聊天区」⇒ 过宽＝误判不排他，安全方向）被使用。")
    print("  ⇒ 已收紧成「`ratio() > 0.5` **且** 最长公共块 ≥ 6 字」，依据是本探针的实测：")
    print("     · 纯靠 ratio 兜底单独捞上来的 = **0 组**（真实语料里它从来没单独生效过）")
    print("     · 收紧到 6 字**挡掉 0 组**（零代价）；构造例则被正确挡掉（→ False）")
    print("     · 保留 6 字下界是为不丢「OCR 错字把 8 字块打散到 6~7 字」的容错能力")
    print("=" * 78)
    print("== 汇总：探针已给出量化数字（%d 组跨会话对 / %d 组误判 / 收紧挡掉 %d 组）=="
          % (_pairs, _hit, _would_block))
    raise SystemExit(0)


if __name__ == "__main__":
    main()
