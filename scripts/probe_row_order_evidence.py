# -*- coding: utf-8 -*-
"""probe_row_order_evidence.py —— 「绿底行 + 非置顶区最上方」这条**第三重证据**可用吗？

⛔ 为什么单开这支（报告 §3.3 与 §4 的欠账）：
  §3.3 提出：同分钟冲突时，"绿底行在哪"能收敛候选；若绿底行名字/时间都读不出（§2.3 的死局），
  再看"它是不是**非置顶区的最上方**"（用户口述线索：刚发消息的会话会冒泡到顶）。
  §4 却明确挂了一条处置：「**先补"机器人刚发完消息"的现场截图再谈行序**」——
  因为当时手上那 3 张 `wechat/` 图是**人工点选**会话时截的，绿底分别在第 5/3/7 行，
  **0/3 在第 1 行**，不能据此断定冒泡行为。

  ⇒ 本支要回答的就是这个欠账。语料里恰好有一段**连续操作的现场截图序列**：
     `ocr-samples/local/local-017..035`（`ScreenShot__2253xx~2258xx`，19 张，
      mtime 22:53:28 ~ 22:58:14，来自 `%TEMP%/wx194_stage`，`kind=local-real`）
     —— 间隔几秒~几十秒，是**同一段操作过程**，正合"现场序列"的要求。

本支**只测两件事**（不预设结论）：
  R1 每张图里，绿底行（`highlight()`）在 `session_rows()` 里是**第几行**（0-based）
  R2 绿底行的 `y_abs` 距列表顶部有多远、离图顶有多远（看它是不是"贴顶"）

判定口径（先写清楚，免得看数下菜）：
  · 若 19 张里绿底行**大多数落在第 0~1 行** ⇒ 行序信号**可用**，可以进 §3.3 的第三重证据；
  · 若仍然分散 ⇒ 行序**不可用**，如实记账（并说明"还缺什么样本才能判"）。
  ⚠️ 不做"挑几张支持的图"这种事后筛选 —— 全部 19 张都列出，含反例。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent import chat_ocr as CO  # noqa: E402

_SAMPLES = [
    r"%USERPROFILE%\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples\local",
    r"%USERPROFILE%\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples\wechat",
]


def _load(path):
    from PIL import Image
    return Image.open(path).convert("RGB")


def _rows_of(path):
    try:
        return list(CO.session_rows(_load(path)) or [])
    except Exception:
        return []


def main():
    print("=" * 78)
    print("「绿底行 + 非置顶区最上方」可用性探针（现场连续截图序列）")
    print("=" * 78)

    _series, _control = [], []
    for _dir in _SAMPLES:
        if not os.path.isdir(_dir):
            continue
        _tag = "现场序列" if "local" in os.path.basename(_dir).lower() else "对照组"
        for _fn in sorted(os.listdir(_dir)):
            if not _fn.lower().endswith(".png"):
                continue
            # 现场序列 = 那 19 张连号截图
            _is_series = _fn.startswith("local-") and "ScreenShot_2026-09-29_22" in _fn
            (_series if _is_series else _control).append(os.path.join(_dir, _fn))

    print("▸ 现场序列 %d 张 / 对照组 %d 张\n" % (len(_series), len(_control)))

    def _scan(paths, title):
        print("── %s ──" % title)
        _res = []
        for _p in paths:
            _fn = os.path.basename(_p)
            _rows = _rows_of(_p)
            _img = _load(_p)
            _hl = None
            try:
                _hl = CO.highlight(_img)
            except Exception:
                _hl = None
            if not _rows:
                print("  %-46s 无会话行（可能不是列表界面）" % _fn[:46])
                continue
            _ys = [int(r.get("y_abs") or 0) for r in _rows]
            _idx = None
            if _hl:
                _hy = int(_hl.get("y_abs") or -1)
                # 找最接近的会话行
                _best, _bd = None, 10 ** 9
                for _i, _y in enumerate(_ys):
                    _d = abs(_y - _hy)
                    if _d < _bd:
                        _best, _bd = _i, _d
                _idx = _best if _bd <= max(30, 60) else None
            _score = float((_hl or {}).get("score") or 0.0)
            _hy_s = int(_hl.get("y_abs")) if _hl else -1
            _top = min(_ys)
            _res.append({"fn": _fn, "n": len(_rows), "idx": _idx, "score": _score,
                         "y": _hy_s, "top": _top, "h": _img.height,
                         "name": str((_hl or {}).get("name") or "")})
            print("  %-46s 行数=%-3d 绿底比例=%.2f 绿底y=%-5s 首行y=%-5s 离首行=%-5s 行序=%s"
                  % (_fn[:46], len(_rows), _score, _hy_s, _top,
                     (_hy_s - _top) if _hy_s >= 0 else "—",
                     ("第%d行" % _idx) if _idx is not None else "未对齐"))
        return _res

    _s = _scan(_series, "R1/R2 现场序列（19 张连号截图）")
    print()
    _c = _scan(_control, "对照组（其余样本，多为人工点选/非列表）")

    print("\n" + "=" * 78)
    _ok = [r for r in _s if r.get("idx") is not None]
    _zero = [r for r in _ok if r["idx"] == 0]
    _le1 = [r for r in _ok if r["idx"] <= 1]
    print("▸ 现场序列里**对齐到会话行**的：%d / %d" % (len(_ok), len(_s)))
    if _ok:
        print("▸ 其中绿底行在**第 0 行**：**%d** 张（%.0f%%）"
              % (len(_zero), 100.0 * len(_zero) / len(_ok)))
        print("▸ 绿底行在**第 0~1 行**：**%d** 张（%.0f%%）"
              % (len(_le1), 100.0 * len(_le1) / len(_ok)))
        from collections import Counter
        print("▸ 行序分布：%s" % dict(sorted(Counter(r["idx"] for r in _ok).items())))
        _d = [r["y"] - r["top"] for r in _ok if r["y"] >= 0]
        if _d:
            print("▸ 绿底行距**首行**的像素差：min=%d 中位=%d max=%d"
                  % (min(_d), sorted(_d)[len(_d) // 2], max(_d)))

    # ── R3 ★绿底行**名字可读率**（决定 `_converge_by_highlight` 的实际可用率）──
    # 起因（§16.7 更正）：报告 §2.3 原结论是"绿底行名字永远读不出"（基于单张 `7bcf1579`），
    #   但内容核查时发现 `local-022/023/024` **完整读出** `'某会话'`、`local-025/026`
    #   读出 `'腾讯新闻'` ⇒ 那个结论**不普适**。这里把它量化：**读得出 / 读不出 各占多少**。
    # ⚠️ 只看"非空"是**上界指标**（没有真值，判不了"读错"）—— 但"非空率"直接决定
    #   `_converge_by_highlight`（要求名字非空）**有多少机会真的生效**。
    print("\n── R3 绿底行名字可读率（决定收敛机制的可用率）──")
    _all = [r for r in (_s + _c) if r.get("y", -1) >= 0]      # 有绿底行的
    _named = [r for r in _all if str(r.get("name") or "").strip()]
    print("▸ 有绿底行的样本：**%d** 张；其中**名字非空**：**%d** 张（%.0f%%）"
          % (len(_all), len(_named), 100.0 * len(_named) / max(1, len(_all))))
    if _named:
        print("▸ 读到的名字（前 14 个）：")
        for _r in _named[:14]:
            print("      %-46s %r" % (_r["fn"][:46], _r["name"][:24]))
    # 稳定性：同一名字是否在多帧上重复出现（多帧一致 ⇒ 读数可信，不是随机噪声）
    from collections import Counter as _C2
    _cnt = _C2(str(r["name"]).strip() for r in _named)
    _rep = {k: v for k, v in _cnt.items() if v >= 2}
    print("▸ **多帧重复出现**的名字（≥2 次 ⇒ 读数稳定、不是噪声）：%s" % (_rep or "无"))
    print("=" * 78)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
