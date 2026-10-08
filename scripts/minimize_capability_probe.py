# -*- coding: utf-8 -*-
"""**最小化能力矩阵探针** —— 回答一个问题：**搜索能不能不跳前台**。

要查清的：能否**在后台完成搜索**（发消息已经能在后台做），从而不打断当前前台窗口。

三个要实测的问题（**必须用真机，不许推断**）：
  Q1 最小化时，**投递文字+回车**能不能真发出（不窜前台）
  Q2 最小化时，**搜索路线**能不能切会话；若能，**是否窜前台**
  Q3 最小化时，**走格**能不能切会话；若能，**是否窜前台**

判据（沿用提速实测的二值判据）：
  · 发送：投字后**发送键绿占比 ≈0.44** → 回车后**≈0.00** ⇒ 真发出（`probe_full_send.py` 口径）
  · 窜前台：全程轮询 `GetForegroundWindow`，记"微信占前台的采样点数"

⛔ 本探针**只对 `--chat-name` 指定的会话**投递，默认「文件传输助手」（发给自己，不打扰第三方）。
"""
import argparse
import json
import os
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import version_gate as vg          # noqa: E402
from agent.config import get_config           # noqa: E402
from agent.wechat import WeChatAdapter        # noqa: E402

from agent import chat_header as ch           # noqa: E402
from agent import input_backend as ib         # noqa: E402

import ctypes                                 # noqa: E402
import logging

log = logging.getLogger("persona-morph")
_u = ctypes.windll.user32
_SW_MINIMIZE, _SW_RESTORE, _SW_SHOWNOACTIVATE = 6, 9, 4


def _fg_is(hwnd: int) -> bool:
    fg = _u.GetForegroundWindow()
    if not fg:
        return False
    if int(fg) == int(hwnd):
        return True
    try:
        for aff in (2, 3):
            if int(_u.GetAncestor(int(fg), aff)) == int(hwnd):
                return True
    except Exception:
        pass
    return False


class _FgWatch(threading.Thread):
    def __init__(self, hwnd, interval=0.015):
        super().__init__(daemon=True)
        self.hwnd = int(hwnd); self.interval = interval
        self._ev = threading.Event()
        self.n = 0; self.hit = 0

    def run(self):
        while not self._ev.is_set():
            self.n += 1
            if _fg_is(self.hwnd):
                self.hit += 1
            time.sleep(self.interval)

    def stop(self):
        self._ev.set()


def _minimize(hwnd):
    _u.ShowWindow(int(hwnd), _SW_MINIMIZE)


def _restore(hwnd):
    _u.ShowWindow(int(hwnd), _SW_SHOWNOACTIVATE)  # 非激活还原：不抢用户前台
    _u.ShowWindow(int(hwnd), _SW_RESTORE)


def _green_ratio(gui):
    """发送键绿占比（**有字 ≈0.44 / 无字 ≈0.00**）—— 真发出的二值判据。

    出处：`agent/wechat_windows.py::send_button_green_ratio(img)`（`probe_full_send.py` 同口径）。
    """
    try:
        from agent import wechat_windows as _ww
        im = ch.capture_image(gui=gui)
        return round(float(_ww.send_button_green_ratio(im)), 4) if im is not None else None
    except Exception as e:  # noqa: BLE001
        print("   （绿占比读不到：%s）" % str(e)[:80])
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="最小化能力矩阵探针（只对指定会话发）")
    ap.add_argument("--chat-name", default="文件传输助手", help="只对这个会话投递（默认发给自己）")
    ap.add_argument("--chat-id", default="filehelper", help="该会话 chat_id")
    ap.add_argument("--text", default="", help="发送文本（空则自动生成带时间戳的）")
    ap.add_argument("--skip-send", action="store_true", help="不发消息（只测切会话）")
    ap.add_argument("--skip-search", action="store_true", help="不测搜索路线")
    a = ap.parse_args()

    text = a.text or ("MIN-PROBE " + time.strftime("%H:%M:%S"))
    vg.allow_session("最小化能力矩阵探针（%s）" % a.chat_name)
    ad = WeChatAdapter(get_config())
    gui = ad._get_gui()
    try:
        gui._update_render_rect()
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    main_hwnd = int(getattr(gui, "main_hwnd", 0) or 0) or ib.find_main_window()
    if not main_hwnd:
        print("⛔ 找不到微信主窗"); return 2

    out = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "main": main_hwnd,
           "chat_name": a.chat_name, "text": text, "steps": []}
    print("① 现场：main=%s  目标会话=%s（%s）" % (main_hwnd, a.chat_name, a.chat_id))

    # ═══ 先把窗口还原（确保起点是"正常显示"，与最小化组对照）═══
    _restore(main_hwnd)
    time.sleep(0.8)
    print("   已还原窗口；IsIconic=%s" % bool(_u.IsIconic(main_hwnd)))

    # ── 步骤 1：最小化后测**发送** ─────────────────────────
    if not a.skip_send:
        print("\n② 最小化 → 投联文字+回车（发给自己）")
        _minimize(main_hwnd)
        time.sleep(1.0)
        iconic = bool(_u.IsIconic(main_hwnd))
        print("   IsIconic=%s" % iconic)
        w = _FgWatch(main_hwnd); w.start()
        t0 = time.perf_counter()
        g_before = _green_ratio(gui)
        # 投字
        try:
            be = ib.MessageBackend(activate=True)
            ok_t, why_t = be.send_text(main_hwnd, text)
        except Exception as e:  # noqa: BLE001
            ok_t, why_t = False, "异常：%s" % e
        time.sleep(0.35)
        g_mid = _green_ratio(gui)
        # 回车
        try:
            ok_r, why_r = be.keys(main_hwnd, [0x0D], wake=False)
        except Exception as e:  # noqa: BLE001
            ok_r, why_r = False, "异常：%s" % e
        time.sleep(0.6)
        g_after = _green_ratio(gui)
        dt = (time.perf_counter() - t0) * 1000
        w.stop(); w.join(timeout=1)
        print("   投字 ok=%s ｜ 回车 ok=%s ｜ 耗时=%.0fms" % (ok_t, ok_r, dt))
        print("   绿占比：前=%s 中(投字后)=%s 后(回车后)=%s" % (g_before, g_mid, g_after))
        s1 = {"step": "send_minimized", "iconic": iconic, "ok_text": bool(ok_t), "ok_ret": bool(ok_r),
              "green_before": g_before, "green_mid": g_mid, "green_after": g_after,
              "ms": round(dt, 1), "fg_samples": w.n, "fg_wechat": w.hit,
              "why_text": str(why_t)[:120], "why_ret": str(why_r)[:120]}
        out["steps"].append(s1)
        _restore(main_hwnd); time.sleep(0.6)

    # ── 步骤 2：最小化后测**搜索切会话** ────────────────────
    if not a.skip_search:
        print("\n③ 最小化 → 搜索路线切会话")
        # 先切走：打开另一个会话（确保"当前不是目标"，否则会直接返回 already）
        _minimize(main_hwnd); time.sleep(0.8)
        w = _FgWatch(main_hwnd); w.start()
        t0 = time.perf_counter()
        try:
            ok_s, why_s = ad.open_chat_by_search(a.chat_id, name=a.chat_name, gui=gui)
        except Exception as e:  # noqa: BLE001
            ok_s, why_s = False, "异常：%s" % e
        dt = (time.perf_counter() - t0) * 1000
        w.stop(); w.join(timeout=1)
        print("   搜索切会话 ok=%s ｜ 耗时=%.0fms ｜ 微信占前台 %d/%d 采样"
              % (ok_s, dt, w.hit, w.n))
        print("   why=%s" % str(why_s)[:160])
        out["steps"].append({"step": "search_minimized", "ok": bool(ok_s), "ms": round(dt, 1),
                             "fg_samples": w.n, "fg_wechat": w.hit, "why": str(why_s)[:200]})
        _restore(main_hwnd); time.sleep(0.6)

    # ── 汇总 ────────────────────────────────────────────
    d = os.path.join(ROOT, "wechatauto_logs", "minimize_probe")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n④ 明细落盘：%s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
