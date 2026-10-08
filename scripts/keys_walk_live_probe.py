# -*- coding: utf-8 -*-
"""**按键走格 · 真机探针（T1–T3）** —— 为「B6 闪屏根治 R1」取实证。

⛔ **本探针已作废，只作历史存证，不要运行**：
  它依赖的开关 `WeChatAdapter._KEYS_WALK_ACTIVATE_FIRST_ONLY` 与整个「按键走格」实现
  **已被有意删除**（真机证据：走格必然让微信窜到前台、我方压不住 ⇒ 会打断用户 ⇒ 弃用；
  反向判据在 `scripts/background_selftest.py` 的 **B19a**：「走格的符号一个都不许留」）。
  保留本文件的唯一用途＝**留证**：`agent/wechat.py` 里解释"为什么弃用走格"时引用了它。

背景（`报告/外部项目考察-20261001/B6闪屏根治方案.md`）：
  R1 ＝ 把「伪激活」从**每格一次**改成**整条链一次**（N 格 ⇒ 1 次抢前台），
  但它依赖一个**未验证假设**：「第一格伪激活后，后续格仍响应方向键」。
  实现里做成开关 `WeChatAdapter._KEYS_WALK_ACTIVATE_FIRST_ONLY`（默认 False，零风险）。
  本探针就是那个"真机测一测"。

三个真机问题：
  · **T1** 第一格伪激活后，后续格还响应吗（能切到目标会话吗）
  · **T2** 闪屏是否消失（量化代理：走格期间「前台被微信抢走」的次数）
  · **T3** 成功率（连跑 N 次，记成功/失败）

⛔ **本探针不发任何消息、不点鼠标**；只往微信投递方向键（↑/↓）切换会话列表选中项。
  它直调 `_switch_by_keys`（走格专用），**不走** `switch_chat_posted`（那条会先试搜索框，测不到走格）。

用法（务必用产品自带运行时）：
  runtime\\python\\python.exe scripts\\keys_walk_live_probe.py --target-name <会话名>            # 基准：逐格伪激活
  runtime\\python\\python.exe scripts\\keys_walk_live_probe.py --target-name <会话名> --first-only # R1：只第一格伪激活
  runtime\\python\\python.exe scripts\\keys_walk_live_probe.py --target-name <会话名> --first-only --repeat 5

产出：`wechatauto_logs\\keys_walk_live\\<时间戳>_<tag>.json`（逐格明细 + 汇总）。
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
from agent import chat_ocr as co              # noqa: E402
from agent import chat_header as ch           # noqa: E402
from agent import input_backend as ib         # noqa: E402

import ctypes                                 # noqa: E402
import logging

log = logging.getLogger("persona-morph")
_u = ctypes.windll.user32


def _fg_is_wechat(main_hwnd: int) -> bool:
    """前台窗口是不是微信（或微信的子窗）。"""
    fg = _u.GetForegroundWindow()
    if not fg:
        return False
    if int(fg) == int(main_hwnd):
        return True
    # 子窗/顶层：用 root owner 链判断
    try:
        root = _u.GetAncestor(int(fg), 2)  # GA_ROOT
        if int(root) == int(main_hwnd):
            return True
        root2 = _u.GetAncestor(int(fg), 3)  # GA_ROOTOWNER
        if int(root2) == int(main_hwnd):
            return True
    except Exception:
        pass
    return False


class _FgWatcher(threading.Thread):
    """走格期间轮询前台：统计「非微信 → 微信」的抢占次数（＝闪屏的量化代理）。

    为什么用它当闪屏代理：闪屏的物理定义就是「前台在 微信↔用户窗口 之间高频跳变」
    （`B6闪屏根治方案.md` §一）。肉眼观察不可复现，这个计数是可复现的。
    """

    def __init__(self, main_hwnd: int, interval: float = 0.015):
        super().__init__(daemon=True)
        self.main = int(main_hwnd)
        self.interval = interval
        self._stop_ev = threading.Event()
        self.samples = 0
        self.wechat_samples = 0
        self.steals = 0            # 非微信 → 微信次数
        self._prev = False

    def run(self):
        while not self._stop_ev.is_set():
            cur = _fg_is_wechat(self.main)
            self.samples += 1
            if cur:
                self.wechat_samples += 1
                if not self._prev:
                    self.steals += 1
            self._prev = cur
            time.sleep(self.interval)

    def stop(self):
        self._stop_ev.set()


def main() -> int:
    ap = argparse.ArgumentParser(description="按键走格真机探针（T1–T3；不发消息）")
    ap.add_argument("--target-name", default="", help="目标会话显示名（walk-only 模式下可省）")
    ap.add_argument("--target-id", default="", help="目标 chat_id（给了就用；不给则按名字从 DB 找）")
    ap.add_argument("--walk-only", action="store_true",
                    help="**只走格不设目标**：直接投递 N 格方向键，记录每格高亮行名是否变化。"
                         "这是 T1 的本质问法（'后续格还响应吗'＝高亮行还在动吗），不依赖能否走到特定会话。")
    ap.add_argument("--dir", choices=("down", "up"), default="down", help="walk-only 走格方向（默认 down）")
    ap.add_argument("--first-only", action="store_true", help="R1：置 _KEYS_WALK_ACTIVATE_FIRST_ONLY=True")
    ap.add_argument("--steps", type=int, default=2, help="本次走几格（默认 2）")
    ap.add_argument("--repeat", type=int, default=1, help="连跑几轮（T3；默认 1）")
    ap.add_argument("--settle", type=float, default=0.30, help="每格后等读屏的秒数")
    a = ap.parse_args()

    vg.allow_session("P1 真机测 T1-T3：按键走格（%s）" % a.target_name)
    ad = WeChatAdapter(get_config())
    gui = ad._get_gui()
    try:
        gui._update_render_rect()
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    main = int(getattr(gui, "main_hwnd", 0) or 0) or ib.find_main_window()
    if not main:
        print("⛔ 找不到微信主窗")
        return 2

    cid = a.target_id or ""
    if not a.walk_only and not cid:
        # 按名字从 DB 找 chat_id（尽力而为）
        try:
            for g in (ad.list_groups() or []):
                if co.matches(str(g.get("name") or ""), a.target_name):
                    cid = str(g.get("wxid") or g.get("id") or "")
                    break
        except Exception as e:  # noqa: BLE001
            print("   找 chat_id 失败：%s" % e)
    if not a.walk_only and not cid:
        print("⛔ 找不到目标 chat_id（用 --target-id 显式给，或加 --walk-only）")
        return 2

    print("① 现场")
    print("   main=%s  render=%s" % (main, getattr(gui, "render_rect", None)))
    print("   目标：%s（%s）" % (a.target_name, cid))
    print("   开关 _KEYS_WALK_ACTIVATE_FIRST_ONLY = %s（本轮置为 %s）"
          % (WeChatAdapter._KEYS_WALK_ACTIVATE_FIRST_ONLY, bool(a.first_only)))

    # 探针用：只读当前会话
    im0 = ch.capture_image(gui=gui)
    cur0 = co.current_chat_name(im0)
    print("   起始当前会话 = %s" % (cur0[0] if isinstance(cur0, tuple) else cur0,))

    # ── 装钩子：数伪激活次数 ──────────────────────────────
    _wake_calls = {"n": 0}
    _orig_wake = ib.MessageBackend._wake

    def _wake_counted(self, hwnd):  # noqa: ANN001
        # ⛔ **只在真的会发激活消息时计数**：`_wake` 入口有 `if not self.activate: return`，
        #   若在入口无条件 +1，则 `activate=False` 的 backend 也把计数顶上去 ⇒ 判据失真
        #   （首测就踩了这个坑：R1 组报"1.00 次/格"，看着像没生效，其实只是计数错了）。
        if getattr(self, "activate", False):
            _wake_calls["n"] += 1
        return _orig_wake(self, hwnd)

    ib.MessageBackend._wake = _wake_counted

    # 置开关
    _saved = WeChatAdapter._KEYS_WALK_ACTIVATE_FIRST_ONLY
    WeChatAdapter._KEYS_WALK_ACTIVATE_FIRST_ONLY = bool(a.first_only)

    out = {"when": time.strftime("%Y-%m-%d %H:%M:%S"),
           "first_only": bool(a.first_only), "target_name": a.target_name,
           "target_id": cid, "main": main, "cur0": (cur0[0] if isinstance(cur0, tuple) else cur0),
           "rounds": [], "wake_scope": "只在 activate=True 时计数（真发的激活消息数）"}
    try:
        for r_i in range(int(a.repeat)):
            print("\n② 第 %d/%d 轮（走 %d 格）" % (r_i + 1, a.repeat, a.steps))
            im = ch.capture_image(gui=gui)
            before = co.current_chat_name(im)
            before = before[0] if isinstance(before, tuple) else before
            _wake_calls["n"] = 0
            watcher = _FgWatcher(main, interval=0.015)
            watcher.start()
            t0 = time.perf_counter()

            # ⚡ **逐格手动走**：budget=1 反复调，这样能精确知道"第 k 格是否响应"。
            #   若直接让它自己走完，只能得到"总结果"，分不出"第一格响应、后续不响应"。
            hop = []
            ok_any = False
            why_last = ""
            if a.walk_only:
                # ── walk-only：裸投递方向键（不设目标），只看高亮行动不动 ──
                from agent.wechat import WeChatAdapter as _WA
                _be_on = ib.MessageBackend(activate=True)
                _be_off = (ib.MessageBackend(activate=False)
                           if _WA._KEYS_WALK_ACTIVATE_FIRST_ONLY else _be_on)
                _be = _be_on
                _vk_dir = _WA._VK_UP if a.dir == "up" else _WA._VK_DOWN
                _arrow = "↑" if a.dir == "up" else "↓"
                for k in range(int(a.steps)):
                    imk = ch.capture_image(gui=gui)
                    curk = co.current_chat_name(imk)
                    curk = curk[0] if isinstance(curk, tuple) else curk
                    hlk = co.highlight(imk)
                    yk = (hlk or {}).get("y_abs")
                    w0 = _wake_calls["n"]
                    ok_k, why_k = _be.keys(main, [_vk_dir])
                    if _WA._KEYS_WALK_ACTIVATE_FIRST_ONLY:
                        _be = _be_off
                    why_last = str(why_k)
                    time.sleep(float(a.settle))
                    im2 = ch.capture_image(gui=gui)
                    cur2 = co.current_chat_name(im2)
                    cur2 = cur2[0] if isinstance(cur2, tuple) else cur2
                    hl2 = co.highlight(im2)
                    y2 = (hl2 or {}).get("y_abs")
                    # ★ 硬判据：高亮带 y 的位移（像素级、确定；名字 OCR 不可靠时仍能判）
                    moved = bool(yk is not None and y2 is not None and y2 != yk)
                    changed = bool(cur2 and curk and cur2 != curk) or moved
                    hop.append({"k": k, "ok": bool(ok_k), "why": str(why_k)[:120],
                                "wake_delta": _wake_calls["n"] - w0,
                                "cur": cur2, "y_before": yk, "y_after": y2,
                                "moved": moved, "changed": changed})
                    print("   格%d(%s): ok=%s wake+%d y=%s→%s moved=%s cur=%r changed=%s ｜ %s"
                          % (k, _arrow, ok_k, _wake_calls["n"] - w0, yk, y2, moved, cur2, changed,
                             str(why_k)[:60]))
                ok_any = any(h.get("changed") for h in hop)
            else:
                for k in range(int(a.steps)):
                    # 目标会话已到 ⇒ 停
                    imk = ch.capture_image(gui=gui)
                    curk = co.current_chat_name(imk)
                    curk = curk[0] if isinstance(curk, tuple) else curk
                    if curk and ad._header_match(a.target_name, curk):
                        hop.append({"k": k, "cur": curk, "note": "已到目标，停"})
                        ok_any = True
                        break
                    w0 = _wake_calls["n"]
                    ok_k, why_k = ad._switch_by_keys(cid, a.target_name, gui, main, budget=1)
                    why_last = str(why_k)
                    time.sleep(float(a.settle))
                    im2 = ch.capture_image(gui=gui)
                    cur2 = co.current_chat_name(im2)
                    cur2 = cur2[0] if isinstance(cur2, tuple) else cur2
                    hop.append({"k": k, "ok": bool(ok_k), "why": str(why_k)[:120],
                                "wake_delta": _wake_calls["n"] - w0,
                                "cur": cur2,
                                "changed": bool(cur2 and curk and cur2 != curk)})
                    print("   格%d: ok=%s wake+%d cur=%r changed=%s ｜ %s"
                          % (k, ok_k, _wake_calls["n"] - w0, cur2,
                             bool(cur2 and curk and cur2 != curk), str(why_k)[:70]))
                    if ok_k:
                        ok_any = True
                        break

            dt = (time.perf_counter() - t0) * 1000.0
            time.sleep(0.25)
            watcher.stop()
            watcher.join(timeout=1.0)
            print("   轮耗时=%.0fms  伪激活次数=%d  前台采样=%d  被抢=%d（微信样本 %d）"
                  % (dt, _wake_calls["n"], watcher.samples, watcher.steals, watcher.wechat_samples))
            out["rounds"].append({
                "i": r_i, "before": before, "hops": hop,
                "ms": round(dt, 1), "wake_n": _wake_calls["n"],
                "fg_samples": watcher.samples, "fg_steals": watcher.steals,
                "fg_wechat": watcher.wechat_samples,
                "ok": bool(ok_any), "why_last": why_last[:160],
            })
    finally:
        ib.MessageBackend._wake = _orig_wake
        WeChatAdapter._KEYS_WALK_ACTIVATE_FIRST_ONLY = _saved

    # ── 汇总 ────────────────────────────────────────────
    n = len(out["rounds"])
    succ = sum(1 for r in out["rounds"] if r["ok"])
    tot_wake = sum(r["wake_n"] for r in out["rounds"])
    tot_steal = sum(r["fg_steals"] for r in out["rounds"])
    tot_hops = sum(len([h for h in r["hops"] if "ok" in h]) for r in out["rounds"])
    changed = sum(1 for r in out["rounds"] for h in r["hops"] if h.get("changed"))
    out["summary"] = {"rounds": n, "success": succ, "success_rate": (succ / n if n else 0.0),
                      "wake_total": tot_wake, "wake_per_hop": (tot_wake / tot_hops if tot_hops else 0.0),
                      "fg_steals_total": tot_steal, "fg_steals_per_round": (tot_steal / n if n else 0.0),
                      "hops": tot_hops, "hops_changed": changed}
    print("\n③ 汇总（%s）" % ("R1 只第一格伪激活" if a.first_only else "基准 逐格伪激活"))
    print("   成功率 %d/%d  伪激活 %d 次（%.2f 次/格）  被抢 %d 次（%.1f 次/轮）  %d 格中 %d 格真的换了会话"
          % (succ, n, tot_wake, out["summary"]["wake_per_hop"],
             tot_steal, out["summary"]["fg_steals_per_round"], tot_hops, changed))

    d = os.path.join(ROOT, "wechatauto_logs", "keys_walk_live")
    os.makedirs(d, exist_ok=True)
    tag = "firstonly" if a.first_only else "baseline"
    p = os.path.join(d, "%s_%s.json" % (time.strftime("%Y%m%d_%H%M%S"), tag))
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("   明细落盘：%s" % p)
    return 0 if succ == n else 2


if __name__ == "__main__":
    sys.exit(main())
