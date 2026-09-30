# -*- coding: utf-8 -*-
"""capture_live_evidence.py —— 「机器人刚发完消息」现场**自动留证**（零侵入，只读）

⛔ 为什么要有它（报告 §4 挂了四轮的欠账）
  §3.3 想用「绿底行 + 非置顶区最上方」当第三重证据，§4 的处置是
  **"先补'机器人刚发完消息'的现场截图再谈行序"**。
  结果：语料里那 19 张连号截图经内容核查后是**人工点选**场景（§16.7 更正），
  欠账一直没还 —— 因为**没人能在"机器人刚发完消息"的那一刻手动截图**。

  解法不是去生产链上挂钩子，而是**在旁边定时连拍**：
      · `send_text` 有 **≥4 个成功返回点**（幂等拦截 / 投递档 L5 / 投递切会话档 / 真实档），
        逐个挂容易漏；
      · 往产品发送链里塞"取证"逻辑属**功能蔓延**，还要动 `agent/wechat.py`；
      · ⇒ **零侵入**：本脚本独立跑，每 `--every` 秒抓一帧并落盘。
        用户真机跑一次发送流程，这一段**自然包含**"刚发完消息"的那几帧。

  ⚠️ 它**只读**：不点鼠标、不切会话、不发送。

用法
  # 一边真机跑机器人发送流程，一边（或在发送前先启动）跑本脚本
  ./runtime/python/python.exe -u scripts/capture_live_evidence.py --every 2 --n 40 --tag after-send
  # 产出：<out>/frame-000.png + frames.jsonl（每行一帧的屏幕侧快照）

要求 / 限制
  · 微信窗口**不要最小化**（`PrintWindow` 抓不到最小化窗口，会得到近黑图）；
    脚本会用"读到几行会话"当有效性指标，连续无效会提示。
  · 帧有效性阈值：`session_rows` ≥ 1 行；`highlight()` 取到绿底行更佳。
  · 每帧的判据快照**只含屏幕侧**（绿底行 y/score/name、会话行数与摘要、图尺寸）——
    DB 侧（`_last_time_hhmm` 等）留到事后分析时再查，避免每帧开库拖慢连拍。

⛔⛔ **别把"本脚本要窗口可见"误读成"产品要窗口可见"**（2026-09-30 用户当面纠正）：
  · **产品发送不要求窗口可见** —— `README.md` 写明"后台消息投递为主…
    **不要求微信窗口可见**（**最小化与被遮挡时也能干**）"；
    机制依据在 `wechat_ui.hit()` 的注释："没有记录或**判据不可用（窗口最小化抓不到图）**时
    **放行但留痕**，不把第一次用锁死" ⇒ 最小化时判据不可用，但**点击/发送照走**。
  · 而且"微信最小化时仍能发送"在 `agent/version_matrix.py` 里是 **L5 最高目标**
    （`minimized_send`），只是 `SEED_RUNS` 里状态 = **`unknown` / "未测（最高目标的最严条件）"**。
  · ⇒ **本脚本需要窗口可见，纯粹是"留证/看屏幕"的需要**；
    它与"最小化能不能发送"是**两件独立的事**，不要混在一起说。
"""
import argparse
import io
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_HR = chr(0x2500)


def _row_line():
    return _HR * 74


def _snapshot(img):
    """一帧的**屏幕侧**判据快照（全部只读；任何异常都退化成缺项，绝不让连拍中断）。"""
    from agent import chat_ocr as CO
    out = {"w": int(img.width), "h": int(img.height)}
    try:
        _rows = list(CO.session_rows(img) or [])
    except Exception as _e:
        _rows = []
        out["rows_err"] = type(_e).__name__
    out["n_rows"] = len(_rows)
    out["rows"] = [{"y_abs": int(r.get("y_abs") or 0),
                    "name": str(r.get("name") or "")[:40],
                    "full": str(r.get("full") or "")[:60]} for r in _rows[:14]]
    try:
        _hl = CO.highlight(img)
    except Exception as _e:
        _hl = None
        out["hl_err"] = type(_e).__name__
    if _hl:
        out["hl"] = {"y_abs": int(_hl.get("y_abs") or 0),
                     "score": round(float(_hl.get("score") or 0.0), 3),
                     "name": str(_hl.get("name") or "")[:40],
                     "why": str(_hl.get("why") or "")[:40]}
        # 绿底行在会话行里的序号（供"是不是最上方"的分析直接读）
        _hy = int(_hl.get("y_abs") or -1)
        _best, _bd = None, 10 ** 9
        for _i, _r in enumerate(_rows):
            _d = abs(int(_r.get("y_abs") or 0) - _hy)
            if _d < _bd:
                _best, _bd = _i, _d
        out["hl_row_idx"] = _best if _bd <= 60 else None
        out["hl_row_dist"] = int(_bd) if _best is not None else None
    else:
        out["hl"] = None
    return out


def main():
    ap = argparse.ArgumentParser(description="「刚发完消息」现场自动留证（只读连拍）")
    ap.add_argument("--out", default="", help="输出目录（默认 报告/ocr瓶颈-<今天>/现场留证/<tag>-<时间戳>/）")
    ap.add_argument("--every", type=float, default=2.0, help="每帧间隔秒（默认 2）")
    ap.add_argument("--n", type=int, default=40, help="抓多少帧（默认 40）")
    ap.add_argument("--tag", default="live", help="标签，用在目录名（默认 live）")
    ap.add_argument("--frames", type=int, default=1, help="每帧内部多抓几次取最好（默认 1，连拍场景求快）")
    a = ap.parse_args()

    from agent.wechat import WeChatAdapter
    from agent import chat_ocr as CO

    _stamp = time.strftime("%Y%m%d-%H%M%S")
    _out = a.out or os.path.join(
        _ROOT, "报告", "ocr瓶颈-%s" % time.strftime("%Y%m%d"),
        "现场留证", "%s-%s" % (a.tag, _stamp))
    os.makedirs(_out, exist_ok=True)
    print(_row_line())
    print("现场自动留证（只读连拍）→ %s" % _out)
    print("  每 %.1fs 一帧，共 %d 帧（约 %.0f 秒）" % (a.every, a.n, a.every * a.n))
    print("  ⚠️ 请保持微信窗口**不要最小化**（PrintWindow 抓不到最小化窗口）")
    print("  ⚠️ 本脚本**只读**：不点鼠标、不切会话、不发送")
    print(_row_line())

    print("▸ 准备 GUI（只一次；之后的连拍都复用它，避免每帧重建触发自愈）...")
    _ad, _gui = None, None
    try:
        _ad = WeChatAdapter()
        _gui = _ad._get_gui()
    except Exception as _e:
        print("  ⛔ 拿不到微信窗口：%s: %s" % (type(_e).__name__, str(_e)[:120]))
        print("     请确认微信已登录、窗口存在（不必在前台）。")
        return 2
    print("  ✓ GUI 就绪：%s" % type(_gui).__name__)

    # ⛔ **前置检查：窗口是不是最小化/被挪到屏幕外**（2026-09-30 实测加）
    #   实测踩到：`WeChatGUI` 初始化成功、但 `rect=(-31989, -32000, -30103, -30561)`
    #   —— **-32000 是 Windows 最小化窗口的经典坐标**。此时 `PrintWindow` 拿不到帧
    #   （`chat_header` 会诚实地返回 None 而不是退回抓屏——抓屏会读到别人家的像素）。
    #   若不前置拦住，用户会白等一整轮连拍才看到"0 帧有效"。
    #   ⚠️ 为什么本脚本**不自动还原窗口**：`wechat.background_only=开（默认：全程后台）`
    #   ⇒ `calibrate_layout()` 会**拒绝置前/置顶**（置顶会压过用户用来遮挡的窗口）。
    #   ⇒ 这是**用户该做的动作**，脚本只负责说清楚。
    _rr = getattr(_gui, "render_rect", None)
    try:
        _x0, _y0 = int(_rr[0]), int(_rr[1])
    except Exception:
        _x0 = _y0 = 0
    if _x0 < -10000 or _y0 < -10000:
        print("  ⛔ 微信窗口**处于最小化 / 屏幕外**（render_rect=%s）⇒ **抓不到帧、无法留证**。" % (_rr,))
        print("     ⇒ 请先**点开微信窗口**（不最小化、不被完全遮挡），再重跑本脚本。")
        print("     （脚本**不会**替你置前：当前配置 `wechat.background_only=开`，"
              "置顶会压过你用来遮挡的窗口）")
        print("  ⚠️ 注意区分：**产品本身在最小化时仍能发送**（README：「不要求窗口可见」；"
              "判据不可用时放行但留痕）——")
        print("     这里失败的只是**本脚本的留证**（要看屏幕），**与「能不能发送」是两件事**。")
        return 2

    _jsonl = os.path.join(_out, "frames.jsonl")
    _n_ok, _n_blank, _n_hl, _bad_streak = 0, 0, 0, 0
    with io.open(_jsonl, "w", encoding="utf-8") as _f:
        for _i in range(max(1, int(a.n))):
            _t0 = time.time()
            _rec = {"i": _i, "t": time.strftime("%Y-%m-%d %H:%M:%S")}
            _img = None
            try:
                _img = CO.capture_best(gui=_gui, frames=max(1, int(a.frames)))
            except Exception as _e:
                _rec["cap_err"] = "%s: %s" % (type(_e).__name__, str(_e)[:80])
            if _img is not None:
                try:
                    _snap = _snapshot(_img)
                    _rec.update(_snap)
                    _png = os.path.join(_out, "frame-%03d.png" % _i)
                    _img.save(_png)
                    _rec["png"] = os.path.basename(_png)
                    if _rec.get("n_rows", 0) >= 1:
                        _n_ok += 1
                        _bad_streak = 0
                    else:
                        _n_blank += 1
                        _bad_streak += 1
                    if _rec.get("hl"):
                        _n_hl += 1
                    print("  [%02d] 行数=%-3s 绿底y=%-5s score=%-5s name=%-16r 行序=%s  %.0fms"
                          % (_i, _rec.get("n_rows"), ( _rec.get("hl") or {}).get("y_abs", "—"),
                             (_rec.get("hl") or {}).get("score", "—"),
                             str((_rec.get("hl") or {}).get("name", ""))[:16],
                             _rec.get("hl_row_idx"), (time.time() - _t0) * 1000))
                except Exception as _e:
                    _rec["snap_err"] = "%s: %s" % (type(_e).__name__, str(_e)[:80])
            else:
                _n_blank += 1
                _bad_streak += 1
                print("  [%02d] 抓帧失败/空" % _i)
            _f.write(json.dumps(_rec, ensure_ascii=False) + "\n")
            _f.flush()
            if _bad_streak == 5:
                print("  ⚠️ 连续 5 帧读不到会话行 —— 微信窗口是否被最小化/遮挡？")
            if _i < int(a.n) - 1:
                _sp = a.every - (time.time() - _t0)
                if _sp > 0:
                    time.sleep(_sp)

    print(_row_line())
    print("== 汇总：%d 帧落盘 · 有会话行 %d · 空帧 %d · 量到绿底行 %d ==" % (_n_ok + _n_blank, _n_ok, _n_blank, _n_hl))
    print("   帧目录：%s" % _out)
    print("   快照：  %s" % _jsonl)
    print("   下一步（事后分析）：直接看 frames.jsonl 的 `hl_row_idx` 分布 —— "
          "这就是「刚发完消息时，绿底行在不在最上方」的**真现场**答案。")
    print(_row_line())
    if _n_ok == 0:
        print("⛔ 一帧有效都没有 ⇒ **这次留证没有价值**，请检查窗口状态后重跑（不冒充成功）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
