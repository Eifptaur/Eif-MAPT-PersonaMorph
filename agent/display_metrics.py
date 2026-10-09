# -*- coding: utf-8 -*-
"""**显示度量**（缩放倍率 / 窗口边框）—— 现测一次、记下来、之后读记下的值。

⛔ **为什么必须有这个模块**：
  > "缩放比例不能硬写！只是你这台机器是 1.5 而已。其他人的电脑可能是 100% 可能是 225%…
  >  要开机自启的时候测量一遍到底是 150% 还是 100% 还是 225%。"

  · 本机 DPI=144 ⇒ 缩放 **150%**；但**用户机器各不相同**（100% / 125% / 150% / 175% / 200% / 225%）；
  · 窗口边框也不恒定（微信独立窗是**自绘标题栏**，系统 `GetSystemMetrics` 给不出真实边框）；
  · ⇒ 凡是"按倍率/边框换算坐标"的地方，**必须读本模块**，不许在别处写 `1.5`、`22`、`11`。

**三条纪律**：
  ① **现测优先**：能当场测出（窗口可见时的 `GetWindowRect-GetClientRect`、`GetDpiForWindow`）就用当场值；
  ② **测不到读缓存**：窗口最小化/离屏时测不出，读本模块记下的值（**还是实测来的**，不是猜的）；
  ③ **缓存也没有 ⇒ 用系统级兜底**（`GetDpiForSystem`；边框退回"按倍率折算"），并在返回值里**标明来源**
     ⇒ 调用方/日志能一眼看出"这次是实测、缓存、还是兜底"。

存储：`data/env_profile.json` 的 `"display_metrics"` 节（与 `env_profile` 同一份机器档案）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

log = logging.getLogger("persona-morph")

# ⛔ **同一个档案的路径只有 `env_profile` 一个定义点**（原来这里又 join 了一遍同样的路径，
#   两处必然漂；而且判据隔离口也只该有一个）。
from .env_profile import PATH # noqa: E402  （与 `env_profile` 同一份档案，别再自己拼一遍）
_LOCK = threading.RLock()
_MEM = {"metrics": None}   # 进程内缓存（省得每次读盘）

#: 边框兜底（**仅当全测不到时**用）——按 96DPI 逻辑值，用时会 × 缩放倍率。
#: 这不是"标准值"，只是"总比 0 好"的最后一档；抓到实测值就会被覆盖。
_FRAME_FALLBACK_96 = (11.0, 0.0, 11.0, 11.0)   # (左, 上, 右, 下) 逻辑像素

#: 允许的缩放档（Windows 只可能是这些；测量结果吸附到最近档，滤掉抖动）
_KNOWN_SCALES = (1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.0)


def _snap_scale(v: float) -> float:
    """把测出的倍率吸附到最近的 Windows 标准档（滤掉 ±0.02 的读数抖动）。"""
    try:
        v = float(v)
        if v <= 0:
            return 1.0
        return min(_KNOWN_SCALES, key=lambda s: abs(s - v))
    except Exception:  # noqa: BLE001
        return 1.0


# ── 现测 ─────────────────────────────────────────────────────────────
def _primary_monitor_dpi() -> int:
    """主显示器的**每显示器 DPI**（`GetDpiForMonitor`）⇒ 96 = 100%、144 = 150%；取不到 ⇒ 0。

    ⛔⛔ **两个坑都踩过，别再换回去**：
      · **必须设 `argtypes`/`restype`**：不设时 `HMONITOR` 会被 ctypes 截成 32 位
        ⇒ 拿到**错误的监视器** ⇒ 实测给出假的 96（而那时真值是别的）。
      · **不要用 `GetDpiForWindow(GetDesktopWindow())`**：桌面窗属于 explorer 进程，
        实测它在同一台机器上给 **144**，而 `GetDpiForMonitor` 给 **96** ⇒ **它会谎报**。
        （曾经拿它当兜底 ⇒ 把正确的 1.0 改成了错的 1.5。）
    """
    try:
        import ctypes as _ct
        from ctypes import wintypes as _wt
        u = _ct.windll.user32
        sh = _ct.windll.shcore
        sh.GetDpiForMonitor.argtypes = [_wt.HMONITOR, _ct.c_int,
                                        _ct.POINTER(_ct.c_uint), _ct.POINTER(_ct.c_uint)]
        sh.GetDpiForMonitor.restype = _ct.c_long

        class _PT(_ct.Structure):
            _fields_ = [("x", _ct.c_long), ("y", _ct.c_long)]

        u.MonitorFromPoint.argtypes = [_PT, _ct.c_uint]
        u.MonitorFromPoint.restype = _wt.HMONITOR
        hmon = u.MonitorFromPoint(_PT(0, 0), 1)          # 1 = MONITOR_DEFAULTTOPRIMARY
        dx, dy = _ct.c_uint(0), _ct.c_uint(0)
        if hmon and sh.GetDpiForMonitor(hmon, 0, _ct.byref(dx), _ct.byref(dy)) == 0:
            return int(dx.value)
    except Exception:  # noqa: BLE001
        pass
    return 0


def _machine_scale_hint() -> float:
    """档案里"机器串"记着的缩放倍率（形如 `…|150%|…` ⇒ 1.5）；没有/读不出 ⇒ 0。

    ⭐ 为什么要它：`measure_scale` 的 `monitor` / `system` 两条路**都取决于本进程有没有
      锁 DPI 感知** —— 实测同一台 150% 的机器、同一个进程里，
      `import input_backend`（它在 import 时锁 PerMonitorV2）**之前** `GetDpiForMonitor` 给 96、
      **之后**给 144。⇒ 拿机器串做**交叉校验**：它只在"换机器 / 改显示设置"时变，是最稳的旁证。
    """
    try:
        m = str(_load_raw().get("machine") or "")
        for part in m.split("|"):
            p = part.strip()
            if p.endswith("%"):
                v = _snap_scale(float(p[:-1]) / 100.0)
                if v > 0:
                    return float(v)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def _with_hint(v: float, src: str) -> tuple:
    """给 `monitor` / `system` 两路做交叉校验（见 `_machine_scale_hint`）⇒ `(倍率, 来源)`。"""
    h = _machine_scale_hint()
    if h > 0 and abs(float(v) - h) > 1e-6:
        return h, "machine-hint"
    return float(v), src


def measure_scale(hwnd: int = 0) -> tuple:
    """**现测**缩放倍率 ⇒ `(倍率, 来源)`。来源：`window` / `monitor` / `system` / `machine-hint`。

    优先级：① 目标窗口的 `GetDpiForWindow`（最准，跟窗口所在显示器走）——
              **但只在窗口正常显示时**才可信（屏外/最小化会给 96）
            ② `GetDpiForMonitor(主显示器)`（与目标窗口是否显示无关，但**取决于本进程的
              DPI 感知有没有锁上**：没锁时给 96 ⇒ 见 `_machine_scale_hint` 的交叉校验）
            ③ `GetDpiForSystem()`
            ④ 档案里机器串记着的缩放（旁证）
            ⑤ 1.0（全失败）
    """
    try:
        import ctypes as _ct
        u = _ct.windll.user32
        if int(hwnd or 0) and window_is_shown(hwnd):
            try:
                d = int(u.GetDpiForWindow(int(hwnd)) or 0)
                if d > 0:
                    return _snap_scale(d / 96.0), "window"
            except Exception:  # noqa: BLE001
                pass
        d = _primary_monitor_dpi()
        if d > 0:
            return _with_hint(_snap_scale(d / 96.0), "monitor")
        try:
            d = int(u.GetDpiForSystem() or 0)
            if d > 0:
                return _with_hint(_snap_scale(d / 96.0), "system")
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass
    h = _machine_scale_hint()
    if h > 0:
        return h, "machine-hint"
    return 1.0, "fallback"


def measure_frame(hwnd: int) -> tuple:
    """**现测**窗口边框（物理像素，**含标题栏**）⇒ `((左,上,右,下), 来源)`。

    ⭐⭐ **"上"必须含标题栏**（实测钉死）：
      `GetWindowRect` 给的是**整窗**（含标题栏 + 四周边框）；
      最小化档要用"整窗 − 上边(标题栏+边框) − 下边框"才能还原**客户区高**。
      ⛔ 原来只减 `11`（下边框）⇒ 客户区**虚高约 35px** ⇒ 按比例算的 y **落到输入条以下**
        ⇒ 点击打到空白 ⇒ **发不出去**。

    做法：`GetWindowRect`（整窗）− `GetClientRect`（客户区）：
      · 左/右 对称取 `(整窗宽 − 客户区宽)/2`；
      · 上 = 整窗高 − 客户区高 − 下边框（下边框 ≈ 左框宽）；
      · ⇒ **上 自然含标题栏**（因为客户区从标题栏下方开始）。
    ⛔ 窗口最小化时 `GetWindowRect`/`GetClientRect` 都不可用 ⇒ 返回 `(None, None)`（读缓存）。
    """
    try:
        import ctypes as _ct
        from ctypes import wintypes as _wt
        u = _ct.windll.user32
        wr, cr = _wt.RECT(), _wt.RECT()
        u.GetWindowRect(int(hwnd), _ct.byref(wr))
        u.GetClientRect(int(hwnd), _ct.byref(cr))
        ww_, wh_ = int(wr.right - wr.left), int(wr.bottom - wr.top)
        cw, ch = int(cr.right - cr.left), int(cr.bottom - cr.top)
        # 离屏哨兵 / 退化值 ⇒ 视为测不到（最小化时 GetWindowRect=-32000、GetClientRect=215x28）
        if ww_ <= 0 or wh_ <= 0 or cw <= 100 or ch <= 100 or wr.left <= -10000:
            return None, None
        if cw > ww_ or ch > wh_:            # 客户区不可能比整窗大 ⇒ 数据不可信
            return None, None
        bw = max(0, (ww_ - cw) // 2)        # 左/右 各一半
        bh_total = max(0, wh_ - ch)          # 上下合计（上＝标题栏+上框，下＝下框）
        bt = max(0, bh_total - bw)           # 上（标题栏 + 上框）
        bb = bw
        if (ww_ - cw) == 0 and (wh_ - ch) == 0:
            return (0.0, 0.0, 0.0, 0.0), "window"   # 真·无边框（合法）
        return (float(bw), float(bt), float(bw), float(bb)), "window"
    except Exception:  # noqa: BLE001
        return None, None


# ── 存 / 读 ──────────────────────────────────────────────────────────
def _load_raw() -> dict:
    try:
        if not os.path.exists(PATH):
            return {}
        with open(PATH, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_raw(d: dict) -> None:
    """写法照项目唯一规范（`persist` 的 tmp + replace）：先写 `.tmp`，再原子替换。"""
    try:
        from . import persist
        os.makedirs(os.path.dirname(PATH) or ".", exist_ok=True)
        tmp = persist.tmp_for(PATH)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:  # noqa: BLE001
                pass
        persist.replace_into(tmp, PATH)
    except Exception as e:  # noqa: BLE001
        log.debug("[display_metrics] 写盘失败：%s", e)


def _cached() -> dict:
    with _LOCK:
        if _MEM["metrics"] is not None:
            return _MEM["metrics"]
        d = _load_raw()
        m = d.get("display_metrics") if isinstance(d.get("display_metrics"), dict) else {}
        _MEM["metrics"] = m or {}
        return _MEM["metrics"]


def remember(scale: float = 0.0, frame=None, src: str = "") -> dict:
    """把**量到的**值记进档案（`env_profile.json` 的 `display_metrics`）。

    只在"这次量到了、且与记着的不一样"时才写盘（避免每次动作都写文件）。
    """
    with _LOCK:
        cur = dict(_cached())
        changed = False
        if scale and float(scale) > 0:
            s = _snap_scale(scale)
            if abs(float(cur.get("scale") or 0) - s) > 1e-6:
                cur["scale"] = s
                cur["scale_src"] = src or "measured"
                changed = True
        if frame:
            try:
                fr = [round(float(x), 2) for x in frame]
                if list(cur.get("frame") or []) != fr:
                    cur["frame"] = fr
                    changed = True
            except Exception:  # noqa: BLE001
                pass
        if changed:
            cur["at"] = time.time()
            cur["at_text"] = time.strftime("%m-%d %H:%M:%S")
            d = _load_raw()
            d["display_metrics"] = cur
            _save_raw(d)
            _MEM["metrics"] = cur
        return cur


def window_is_shown(hwnd: int) -> bool:
    """窗口**是否正常显示在屏幕上**（可见、没最小化、矩形落在虚拟屏内）。

    ⛔ 为什么需要它：`GetDpiForWindow` 对**被移到屏幕外 / 最小化**的窗口会给 96 ⇒
      `measure_scale` 现测出 **1.0**。而 `scale()` 里"现测与缓存不同就以现测为准"那条
      ⇒ 会把档案里正确的 1.5 **污染成 1.0**。而落点的"逻辑像素"全靠 scale 折算
      ⇒ 之后算出来的落点**全偏**（真机实测：屏外抓帧时 `px_from_right_96` 竟等于物理像素）。
    """
    try:
        import ctypes as _ct
        u = _ct.windll.user32
        if not u.IsWindow(int(hwnd)):
            return False
        if u.IsIconic(int(hwnd)) or not u.IsWindowVisible(int(hwnd)):
            return False
        r = _ct.wintypes.RECT()
        u.GetWindowRect(int(hwnd), _ct.byref(r))
        vx = u.GetSystemMetrics(76)          # SM_XVIRTUALSCREEN
        vy = u.GetSystemMetrics(77)          # SM_YVIRTUALSCREEN
        vw = u.GetSystemMetrics(78)          # SM_CXVIRTUALSCREEN
        vh = u.GetSystemMetrics(79)          # SM_CYVIRTUALSCREEN
        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        return bool(vx <= cx <= vx + vw and vy <= cy <= vy + vh)
    except Exception:  # noqa: BLE001
        return False


def scale(hwnd: int = 0, refresh: bool = False) -> float:
    """**取缩放倍率**（现测 → 记下 → 以后读记下的）。

    `refresh=True` 强制现测并更新档案（开机自启用一次即可）。
    """
    if not refresh:
        cached = _cached()
        c = float(cached.get("scale") or 0)
        if c > 0:
            # ⛔ **窗口"没正常显示"时不许直接采信缓存** —— 缓存可能本身就是被污染的 1.0
            #   （现场取证：机器串写着 150%，而档案里是 `1.0 / src=monitor`；落点的"逻辑像素"
            #    全靠它折算 ⇒ 之后算出来的落点**全偏**，语音条那一枪就点空了 46px）。
            #   ⇒ 窗口量不了就退**与窗口无关**的那一路（`monitor` / `system`）现测一次；
            #     连它都拿不到才退回缓存。
            try:
                if not window_is_shown(hwnd):
                    now, src = measure_scale(0)
                    if now and src != "fallback" and abs(now - c) > 1e-6:
                        remember(now, src=src)
                        return now
                    return c
                # 窗口正常显示：顺手复核一次（窗口挪到别的显示器/缩放会变 ⇒ 以现测为准）
                now, src = measure_scale(hwnd)
                if now and abs(now - c) > 1e-6:
                    remember(now, src=src)
                    return now
                return c
            except Exception:  # noqa: BLE001
                return c
    now, src = measure_scale(hwnd)
    # ⛔ **`fallback`（全失败给的 1.0）不许落档**：那不是"量到的"，写进去就是把档案污染成 1.0
    #   （屏外/最小化窗口上量一次就会发生）。只有真量到的 `window` / `monitor` / `system` 才写。
    if src != "fallback":
        remember(now, src=src)
    return now


def frame(hwnd: int, refresh: bool = False) -> tuple:
    """**取窗口边框**（物理像素）⇒ `(左,上,右,下)`。

    现测 → 记下 → 以后读记下的；全没有 ⇒ 按 `_FRAME_FALLBACK_96 × scale` 折算（并如实标注来源）。
    """
    if refresh or True:
        fr, src = measure_frame(int(hwnd))
        if fr is not None:
            remember(frame=fr)
            return tuple(fr)
    cached = _cached()
    cf = cached.get("frame")
    if isinstance(cf, (list, tuple)) and len(cf) == 4:
        try:
            return tuple(float(x) for x in cf)
        except Exception:  # noqa: BLE001
            pass
    s = scale(hwnd)
    return tuple(float(x) * s for x in _FRAME_FALLBACK_96)


def remember_anchor(name: str, value_lp: float, src: str = "") -> dict:
    """**记下一个"与机器相关、会随微信版本/字号变"的度量**（单位：**逻辑像素 / 96DPI**）。

    现有用途两类（都是"当场量到的相对量"，不是绝对坐标）：
      · 落点距**客户区右缘**的距离（键 `toggle` / `green` / `cancel`）——
        抓不到帧时用它兜底，而不是用代码里的出厂默认；
      · 两个图标的**中心间距**（键 `search_gap`）—— 只认到一个图标时用它推出另一个。

    只在"与记着的值差 ≥0.5 逻辑px"时才写盘（避免每次动作都写文件）。
    """
    try:
        v = float(value_lp)
        if not (v > 0):
            return _cached()
    except Exception:  # noqa: BLE001
        return _cached()
    with _LOCK:
        cur = dict(_cached())
        anchors = dict(cur.get("anchors") or {}) if isinstance(cur.get("anchors"), dict) else {}
        old = anchors.get(name) if isinstance(anchors.get(name), dict) else {}
        if abs(float((old or {}).get("from_right_lp") or 0) - v) < 0.5:
            return cur
        anchors[name] = {"from_right_lp": round(v, 1), "src": src or "measured",
                         "at_text": time.strftime("%m-%d %H:%M:%S")}
        cur["anchors"] = anchors
        cur["at"] = time.time()
        cur["at_text"] = time.strftime("%m-%d %H:%M:%S")
        d = _load_raw()
        d["display_metrics"] = cur
        _save_raw(d)
        _MEM["metrics"] = cur
        return cur


def anchor(name: str) -> float:
    """取记下的度量（逻辑像素）。**没记过 ⇒ 0**（调用方自行兜底）。"""
    try:
        a = _cached().get("anchors") or {}
        return float(((a.get(name) or {}) if isinstance(a, dict) else {}).get("from_right_lp") or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def remember_client(cw: int, ch: int, win=None) -> dict:
    """**存下实测的客户区尺寸**（可见档量到就存）—— 最小化档靠它算落点。

    `win` ＝ 当时的整窗 `(w, h)`，用于最小化档"窗口被拉过 ⇒ 等比缩放"。

    ⭐⭐ **为什么必须存**（实测钉死）：
      最小化档拿不到 `GetClientRect`；而"整窗减边框"**推不准**
      （微信独立窗 `rcNormalPosition` 的高度口径与整窗不一致 ⇒ 直接减会虚高约 35px
       ⇒ 按比例算的 y 落到输入条以下 ⇒ **点空、发不出去**）。
      ⇒ 唯一可靠办法：**窗口可见时量一次真客户区、记下来**，之后一直用。
    """
    with _LOCK:
        cur = dict(_cached())
        changed = False
        try:
            cw, ch = int(cw), int(ch)
            if cw > 100 and ch > 100 and (int(cur.get("client_w") or 0) != cw
                                          or int(cur.get("client_h") or 0) != ch):
                cur["client_w"], cur["client_h"] = cw, ch
                cur["client_at"] = time.strftime("%m-%d %H:%M:%S")
                changed = True
            if win and len(tuple(win)) == 2:
                ww_, wh_ = int(win[0]), int(win[1])
                if (int(cur.get("win_w") or 0) != ww_ or int(cur.get("win_h") or 0) != wh_):
                    cur["win_w"], cur["win_h"] = ww_, wh_
                    changed = True
        except Exception:  # noqa: BLE001
            pass
        if changed:
            d = _load_raw()
            d["display_metrics"] = cur
            _save_raw(d)
            _MEM["metrics"] = cur
        return cur


def client_size(hwnd: int = 0, win_any=None) -> tuple:
    """**最小化档**算客户区尺寸 ⇒ `(w, h)`；没存过 ⇒ `(0, 0)`。

    做法：用存下的实测客户区 `(client_w, client_h)` 与当时的整窗 `(win_w, win_h)`
      算**相对比例**，再乘**当前整窗尺寸**（`win_any`，来自 `rcNormalPosition`）
      ⇒ 用户把窗口拉大/拉小也成立（比例不变）。
    """
    c = _cached()
    cw, ch = int(c.get("client_w") or 0), int(c.get("client_h") or 0)
    if cw <= 100 or ch <= 100:
        return 0, 0
    ww0, wh0 = int(c.get("win_w") or 0), int(c.get("win_h") or 0)
    if not (ww0 > 100 and wh0 > 100):
        return cw, ch
    if not win_any:
        return cw, ch
    try:
        wn_w, wn_h = int(win_any[2] - win_any[0]), int(win_any[3] - win_any[1])
        if wn_w <= 100 or wn_h <= 100:
            return cw, ch
        # 等比缩放（实测：窗口尺寸变了、客户区占整窗的比例不变）
        return max(1, int(round(cw * wn_w / float(ww0)))), max(1, int(round(ch * wn_h / float(wh0))))
    except Exception:  # noqa: BLE001
        return cw, ch


def info(hwnd: int = 0) -> dict:
    """给控制台/诊断看的**一眼读数**（含来源标注，便于判"是实测还是猜的"）。"""
    c = dict(_cached())
    now_s, now_src = measure_scale(hwnd)
    fr, fr_src = measure_frame(int(hwnd)) if int(hwnd or 0) else (None, None)
    return {"scale": float(c.get("scale") or now_s),
            "scale_cached": c.get("scale"), "scale_src": c.get("scale_src") or now_src,
            "scale_now": now_s, "scale_now_src": now_src,
            "frame": list(c.get("frame") or []), "frame_now": list(fr) if fr else None,
            "frame_now_src": fr_src, "at_text": c.get("at_text") or ""}


def geometry_signature() -> dict:
    """**几何签名**：把"会影响落点、且用户可能改动"的那几项打成一个指纹。

    为什么要它：用户平时**不会**改缩放/分辨率，但**一旦改了**，所有按逻辑像素折算的落点
      就全偏 —— 而**不会有任何报错**（表现成"偶尔点不准"）。⇒ 每次用之前比一次指纹：
      变了就**立刻重测并更新档案**，不等下一次开机。

    只含**便宜的**调用（不抓图、不碰窗口）：缩放倍率 / 屏幕物理尺寸 / 显示器个数与区域。
    """
    try:
        import ctypes as _ct
        from ctypes import wintypes as _wt
        u = _ct.windll.user32
        sig = {
            "scale": round(float(_primary_monitor_dpi() or 0) / 96.0, 3),
            "screen": [int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))],
            "monitors": int(u.GetSystemMetrics(80)),          # SM_CMONITORS
        }
        return sig
    except Exception:  # noqa: BLE001
        return {}


def check_geometry_change(hwnd: int = 0, log=None) -> dict:
    """比一次几何签名：**变了 ⇒ 立刻重测并更新档案**（不等下次开机）。

    返回 `{"changed": bool, "old":…, "new":…, "scale":…}`。
    ⛔ 只在**真的变了**时才写盘（不然每次调用都写一遍档案）。
    """
    try:
        new = geometry_signature()
        if not new:
            return {"changed": False, "reason": "取不到签名"}
        c = _cached()
        old = c.get("geometry") or {}
        changed = bool(old) and (old != new)
        if changed:
            try:
                if log:
                    log.warning("[显示] 几何变了：%s → %s ⇒ 立刻重测缩放与落点", old, new)
            except Exception:  # noqa: BLE001
                pass
            scale(hwnd, refresh=True)          # 重新量缩放并落档
        if not old or changed:
            remember_geometry(new)
        return {"changed": changed, "old": old, "new": new,
                "scale": float(_cached().get("scale") or 0)}
    except Exception as e:  # noqa: BLE001
        return {"changed": False, "reason": str(e)[:60]}


def remember_geometry(sig: dict) -> None:
    """把几何签名落进档案（`display_metrics.geometry`）。"""
    try:
        cur = _cached()
        cur["geometry"] = dict(sig or {})
        _write(cur)
    except Exception:  # noqa: BLE001
        pass


def boot_check(hwnd: int = 0, frame_hwnd: int = 0) -> dict:
    """**开机自检台账**：把"会影响点击的那些量"逐项量一遍，并记下**每一项的来源**。

    为什么要它：所有落点都是由这些量折算出来的 ⇒ **任何一项"没对齐"，点击就会偏**。
    台账把"哪一项是当场实测、哪一项只是读档案、哪一项退到了兜底"一次说清；
    ⛔ **来源 `fallback` 的项＝没对齐** ⇒ 必须让调用方/日志看得见（不许静默用一个猜的值）。

    返回 `{"items": {名: {"value":…, "src":…}}, "bad": [退到兜底的项…], "at_text": …}`。
    **只读**（本函数自己不写盘；落档由 `warmup()` 做）。
    """
    h = int(hwnd or 0)
    fh = int(frame_hwnd or 0) or h
    items = {}
    # ① 缩放倍率（最要紧的一项：所有"逻辑像素 → 物理像素"都乘它）
    s_now, s_src = measure_scale(h)
    items["scale"] = {"value": round(float(s_now or 0), 3), "src": str(s_src or "fallback")}
    # ② 窗口边框（帧坐标 → 客户区坐标要用）
    fr_now, _fr_src = measure_frame(fh) if fh else (None, None)
    if fr_now is not None:
        items["frame"] = {"value": [round(float(x), 2) for x in fr_now], "src": "window"}
    else:
        cf = _cached().get("frame")
        if isinstance(cf, (list, tuple)) and len(cf) == 4:
            items["frame"] = {"value": [round(float(x), 2) for x in cf], "src": "cached"}
        else:
            sc = float(s_now or 1.0)
            items["frame"] = {"value": [round(float(x) * sc, 2) for x in _FRAME_FALLBACK_96],
                              "src": "fallback"}
    # ③ 客户区尺寸（最小化档算落点要用）
    c = _cached()
    cw, ch = int(c.get("client_w") or 0), int(c.get("client_h") or 0)
    items["client"] = ({"value": [cw, ch], "src": "cached"} if cw > 100 and ch > 100
                       else {"value": [0, 0], "src": "fallback"})
    # ④ 落点锚（各元素"距客户区右缘的逻辑像素"）：记下的才算数，没记下的由上层报"出厂默认"
    got = c.get("anchors") if isinstance(c.get("anchors"), dict) else {}
    items["anchors"] = {"value": {k: float((v or {}).get("from_right_lp") or 0)
                                  for k, v in (got or {}).items()},
                        "src": "cached" if got else "missing"}
    bad = [k for k, v in items.items() if str(v.get("src")) == "fallback"]
    if not got:
        bad.append("anchors")
    return {"items": items, "bad": bad, "at_text": time.strftime("%m-%d %H:%M:%S")}


def warmup(hwnd: int = 0, frame_hwnd: int = 0) -> dict:
    """**开机预热**：现测一次缩放（可选边框），**只在真测到时**写进档案；再跑一次**自检台账**。

    在程序启动时调一次（有微信窗口就用它，没有就用系统级 DPI）。

    ⛔ **不许把"测不到"当"测到了 1.0"**（实测教训）：
      窗口不可用 / 传 0 时 `measure_scale` 的**来源是 `fallback`（或 `system`）** ——
      这时写盘会把档案里**真实的窗口倍率覆盖成系统倍率**。
      实例：本机档案原为窗口实测值，被一次 `warmup(0)` 覆写成 `system/1.0` ⇒
      按 scale 折算的落点会整体偏，且**档案已被 gitignore、无从回滚**。
      ⇒ 口径：**只有 `window` 来源（真从目标窗口量到的）才写**；
        `system` / `fallback` **只返回读数、不动档案**（调用方照样能拿到值，只是不落盘）。
    """
    s, ssrc = measure_scale(hwnd)
    _src = str(ssrc or "")
    if _src == "window" and float(s or 0) > 0:
        remember(scale=s, src=_src)
        if int(hwnd or 0):
            fr, _ = measure_frame(int(hwnd))
            if fr is not None:
                remember(frame=fr)
    # ★ 自检台账：逐项记来源 + 落档（事后可查"这次开机各项对齐没有"）
    try:
        led = boot_check(hwnd, frame_hwnd=frame_hwnd)
        with _LOCK:
            cur = dict(_cached())
            cur["boot_check"] = led
            cur["boot_at_text"] = led.get("at_text") or ""
            d = _load_raw()
            d["display_metrics"] = cur
            _save_raw(d)
            _MEM["metrics"] = cur
    except Exception as e:  # noqa: BLE001
        led = {"items": {}, "bad": [], "err": str(e)[:80]}
    log.info("[display_metrics] 预热：缩放=%.3f（%s）%s",
             s, _src, "已入档" if _src == "window" else "**未入档**（不是从窗口量到的，不覆盖档案）")
    if led.get("bad"):
        log.warning("[display_metrics] ⚠️ 开机自检：%d 项**没对齐**（退到兜底/没记过）＝ %s"
                    " ⇒ 按它们折算的落点会偏，请先让微信窗口可见地跑一次以补齐实测",
                    len(led["bad"]), "、".join(str(x) for x in led["bad"]))
    else:
        log.info("[display_metrics] 开机自检通过：各项来源＝%s",
                 {k: (v or {}).get("src") for k, v in (led.get("items") or {}).items()})
    return info(hwnd)
