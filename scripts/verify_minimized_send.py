# -*- coding: utf-8 -*-
"""verify_minimized_send.py —— 验证 L5 目标「微信最小化时仍能发送」（把 `unknown` 测成有结论）

⛔ 为什么要有它
  `agent/version_matrix.py` 里：
      CAPS      = { ... "minimized_send": {"label": "微信最小化时仍能发送", "level": "L5"} ... }
      SEED_RUNS = [{ "wechat": "4.1.15.8", "when": "2026-09-13",
                     "caps": { ... "minimized_send": {"status": "unknown",
                              "evidence": "未测（最高目标的最严条件）"} } }]
  ⇒ **这是项目的 L5（最高）目标，却从 2026-09-13 至今「未测」。**
  而用户明确说过"朋友现在能在最小化收发消息" —— 也符合 `README` 的成文口径：
      「后台消息投递为主 … **不要求微信窗口可见**（**最小化与被遮挡时也能干**）」
  机制依据：`wechat_ui.hit()` 的注释「**判据不可用（窗口最小化抓不到图）**时**放行但留痕**」。

  ⇒ 所以缺的**不是能力，是"把它测下来"**。本脚本就是那把尺子。

⚠️ 与 `scripts/capture_live_evidence.py` 的区别（别搞混）
  · 留证脚本**要**窗口可见（它要看屏幕）；
  · 本脚本**要**窗口**最小化**（不然测的不是这个场景），且**完全不依赖截图** ——
    它只看两件事：`send_text` 的返回 + **库里能不能回读到那条新消息**。

⚠️⚠️ **本脚本默认 dry-run（不发送）**：真发送会产生一条**真实消息**。
   `--send` 才真发；收件人默认 **`filehelper`（文件传输助手，发给你自己）** —— 最安全。
   想换收件人必须显式给 `--chat`，并在输出里留痕。

用法
  # 1) 先看计划（不发送，安全）
  ./runtime/python/python.exe -u scripts/verify_minimized_send.py
  # 2) 确认后真发（默认发给自己 filehelper）
  ./runtime/python/python.exe -u scripts/verify_minimized_send.py --send
  # 3) 换收件人（会发到真实会话，谨慎）
  ./runtime/python/python.exe -u scripts/verify_minimized_send.py --send --chat <chat_id>
输出末尾给出可直接粘进 `version_matrix.SEED_RUNS` 的证据行。
"""
import argparse
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_HR = chr(0x2500)
_MIN_XY = -10000          # Windows 最小化窗口的经典坐标（-32000）比这更小


def _win_state(gui):
    """返回 (是否最小化/屏幕外, render_rect 原值)。"""
    _rr = getattr(gui, "render_rect", None)
    try:
        _x0, _y0 = int(_rr[0]), int(_rr[1])
    except Exception:
        return None, _rr
    return (_x0 < _MIN_XY or _y0 < _MIN_XY), _rr


def main():
    ap = argparse.ArgumentParser(description="验证「微信最小化时仍能发送」（默认 dry-run）")
    ap.add_argument("--send", action="store_true", help="真的发送（不加则只打印计划）")
    ap.add_argument("--chat", default="filehelper",
                    help="收件人 chat_id（默认 filehelper＝发给自己，最安全）")
    ap.add_argument("--text", default="", help="自定义文本（默认带时间戳的验证串）")
    ap.add_argument("--wait", type=float, default=15.0, help="发送后等库回读的秒数（默认 15）")
    a = ap.parse_args()

    print(_HR * 74)
    print("验证 L5「微信最小化时仍能发送」（version_matrix: minimized_send）")
    print(_HR * 74)

    from agent.wechat import WeChatAdapter
    _ad = None
    try:
        _ad = WeChatAdapter()
    except Exception as _e:
        print("⛔ 构造适配器失败：%s: %s" % (type(_e).__name__, str(_e)[:140]))
        return 2

    # ── 1) 报告窗口状态（**最小化才是要测的场景**）──
    _gui, _state = None, None
    try:
        _gui = _ad._get_gui()
        _state, _rr = _win_state(_gui)
    except Exception as _e:
        _rr = "<拿不到 GUI: %s>" % str(_e)[:80]
        _state = None
    print("▸ 微信窗口 render_rect = %s" % (_rr,))
    if _state is True:
        print("  ✓ 窗口**处于最小化/屏幕外** ⇒ 这正是要测的场景 ✓")
    elif _state is False:
        print("  ⚠️ 窗口**当前可见/未最小化** ⇒ 这次测的**不是**「最小化时发送」的场景。")
        print("     要测那条 L5，请先把微信**最小化**再跑本脚本（否则结论要标注成'可见时')。")
    else:
        print("  ⚠️ 窗口状态判不出来 ⇒ 结论里必须写明「未知」，别当成已验证。")

    # ── 2) 组文本（唯一串，便于库回读定位）──
    _stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    _text = a.text or ("[最小化发送验证] %s" % _stamp)
    print("▸ 收件人  = %s%s" % (a.chat, "（文件传输助手＝发给你自己）" if a.chat == "filehelper" else ""))
    print("▸ 文本    = %r" % _text)

    # ── 3) 发送前先记一次库里的最新时间（回读的"基线"）──
    _before = ""
    try:
        _before = _ad._last_time_hhmm(a.chat) or ""
    except Exception as _e:
        _before = "<取不到: %s>" % str(_e)[:40]
    print("▸ 发送前该会话最后一条时间 = %r" % (_before,))

    if not a.send:
        print(_HR * 74)
        print("== DRY-RUN：**没有发送任何消息** ==")
        print("   确认无误后加 `--send` 真跑（会产生一条真实消息，默认只发给你自己的 filehelper）。")
        print(_HR * 74)
        return 0

    # ── 4) 真发 ──
    print(_HR * 74)
    print("▸ 开始发送（窗口保持最小化 —— **不要**去点它）...")
    _t0 = time.time()
    try:
        _ok, _msg = _ad.send_text(a.chat, _text)
    except Exception as _e:
        print("⛔ send_text 抛异常：%s: %s" % (type(_e).__name__, str(_e)[:160]))
        _ok, _msg = False, "exception: %s" % type(_e).__name__
    _dt = time.time() - _t0
    print("  send_text 返回 = (%r, %r)  耗时 %.1fs" % (_ok, str(_msg)[:120], _dt))

    # ── 5) 回读：等库出现那条新消息（**这才是"真发出去"的判据**）──
    print("▸ 等库回读（最多 %.0fs）..." % a.wait)
    _hit, _after, _rows = False, "", []
    _deadline = time.time() + max(1.0, a.wait)
    while time.time() < _deadline:
        try:
            _rows = _ad._db_messages(a.chat, limit=3) or []
        except Exception:
            _rows = []
        for _r in _rows:
            if _text and _text in str(_r.get("text") or _r.get("content") or ""):
                _hit = True
                break
        if _hit:
            break
        time.sleep(1.0)
    try:
        _after = _ad._last_time_hhmm(a.chat) or ""
    except Exception:
        _after = ""
    print("  库回读命中本次文本 = **%s**" % _hit)
    print("  发送后最后一条时间   = %r（发送前 %r）" % (_after, _before))
    if not _hit:
        print("  最近 3 条：%s" % [(str(_r.get("text") or _r.get("content") or ""))[:24] for _r in _rows])

    # ── 6) 结论 ──
    print(_HR * 74)
    _scene = "最小化" if _state is True else ("窗口可见" if _state is False else "窗口状态未知")
    if _ok and _hit:
        print("✅ **通过**：【%s】场景下确实发出去了（send_text=True 且库回读命中）" % _scene)
        _verdict = ("ok", "【%s】场景：send_text=True 且库回读到本次文本（%s）；"
                    "窗口 render_rect=%s" % (_scene, _stamp, _rr))
    elif _hit:
        print("⚠️ **半通过**：库里有那条消息（真发出去了），但 send_text 返回 False ⇒ 判据本身要查")
        _verdict = ("partial", "库回读命中但 send_text=False（%s）" % _stamp)
    elif _ok:
        print("⚠️ **半通过**：send_text 说成功，但库回读**没命中** ⇒ 可信度不足（可能是假成功）")
        _verdict = ("partial", "send_text=True 但库回读未命中（%s）" % _stamp)
    else:
        print("❌ **未通过**：【%s】场景下没发出去（send_text=False 且库回读未命中）" % _scene)
        _verdict = ("no", "【%s】场景未发出去：%s" % (_scene, str(_msg)[:80]))

    print(_HR * 74)
    print("▸ 可直接粘进 `agent/version_matrix.py` 的 `SEED_RUNS[...]['caps']`：")
    print('  "minimized_send": {"status": "%s", "evidence": "%s"},'
          % (_verdict[0], _verdict[1].replace('"', "'")))
    print(_HR * 74)
    return 0 if _verdict[0] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
