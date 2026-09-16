# -*- coding: utf-8 -*-
"""微信 UI 图标库 + 闭环点击框架（坐标一次标定，各 DPI/窗口尺寸通用）。

设计（针对「图标是什么 → 按布局算坐标 → 瞬移点击 → 验证重试」）：
  1. 图标库 ICONS：每个 UI 元素有 id/名称/锚点规则（相对渲染区比例）+ 验证方式。
     标定一次（calibrate_ui 在真实微信上像素聚类检测侧栏图标序列），
     结果存 data/ui_layout.json；之后所有坐标 = 渲染区尺寸 × 比例（DPI 无关）。
  2. 统一点击入口 hit(name)：prepare_screen → 换算绝对坐标 → SetCursorPos **瞬移** → 点击。
  3. 闭环 achieve(fn, verify, ...)：执行 → 验证（OCR/UIA/回读）→ 失败重试（重新标定/重定位）→
     retries 次后放弃并返回原因（绝不"乱点"）。

元素清单（首批）：
  sidebar.avatar / sidebar.chat / sidebar.contacts / sidebar.collection /
  sidebar.moments(朋友圈·相机) / sidebar.channels(视频号) / sidebar.shake(发现?) /
  sidebar.mini(小程序) / sidebar.more(更多)
  search.box（顶部搜索框）· input.box / input.send（输入区/发送）
  moments.publish（朋友圈发表入口）
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Callable

from .config import get_config, DATA_DIR

log = logging.getLogger("persona-morph")

_LAYOUT_FILE = os.path.join(DATA_DIR, "ui_layout.json")

# 单项鼠标检验的「停止」标志（前端点击停止 → 置位；主要操作循环检查，遇置位立即中止）
UI_STOP = {"v": False}


def request_stop():
    UI_STOP["v"] = True


def clear_stop():
    UI_STOP["v"] = False


def stop_requested() -> bool:
    return bool(UI_STOP.get("v"))

# 侧栏图标语义（微信 PC 4.1 实测/悬停 tooltip 验证）：
# avatar 头像(90) / chat 消息(240) / contacts 通讯录(311=收藏? 实测tooltip为收藏) /
# 实测结论：252=消息 311=收藏 384=朋友圈 455=视频号 529=扫一扫 599=小程序 672=更多 phone 958/1030
ICONS = {
    "search.box":    {"type": "rel", "rel": (0.055, 0.045), "approve": "search"},
    "input.box":     {"type": "rel", "rel": (0.55, 0.955), "approve": "input"},
    "input.send":    {"type": "rel", "rel": (0.97, 0.955), "approve": "send"},
    "sidebar.avatar":   {"type": "order", "order": 0},
    "sidebar.chat":     {"type": "order", "order": 1},
    "sidebar.collection": {"type": "order", "order": 2},
    "sidebar.moments":  {"type": "order", "order": 3, "approve": "moments"},
    "sidebar.channels":  {"type": "order", "order": 4},
    "sidebar.scan":      {"type": "order", "order": 5},
    "sidebar.mini":      {"type": "order", "order": 6},
    "sidebar.more":      {"type": "order", "order": 9},
}

# 侧栏图标默认几何（相对渲染区；标定后写入 layout 覆盖）
_SIDEBAR = {"x_ratio": 0.038, "top_ratio": 0.062, "step_ratio": 0.062, "icon_ratio": 0.032}


def _load_layout() -> dict:
    try:
        with open(_LAYOUT_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}


def _save_layout(d: dict):
    try:
        os.makedirs(os.path.dirname(_LAYOUT_FILE), exist_ok=True)
        with open(_LAYOUT_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _layout_stale(gui) -> bool:
    """标定失效检测：当前渲染宽度相对标定时差异 >15%（窗口拖动改宽度/换分辨率）→ 自动重标。"""
    try:
        render = gui.render_rect or gui._update_render_rect() or (0, 0, 0, 0)
        sw = int(render[2] or 0)
        if not sw:
            return False
        lay = _load_layout()
        ref_w = int(lay.get("render_w") or 0)
        if ref_w <= 0:
            return False
        return abs(sw - ref_w) / max(1, ref_w) > 0.15
    except Exception:
        return False


def calibrate_ui(gui) -> dict:
    """在真实微信窗口上自动标定 UI 布局（像素聚类检测侧栏图标序列 + 搜索框）。

    自动预检：窗口不可见/移位时自动恢复并置前台（无需用户手动操作）。
    结果写 data/ui_layout.json 并返回。流程：
      · 截图渲染区 → 侧栏列亮度聚类 → 图标 y 序列（头像/消息/联系人/收藏/相机/…）
      · 搜索框：顶部左区域白色高光行
    """
    try:
        from . import ui_adapt
        ui_adapt.prepare_screen(gui)
    except Exception:
        pass
    try:
        from PIL import Image
    except Exception:
        return {}
    try:
        render = gui.render_rect or gui._update_render_rect() or (0, 0, 0, 0)
        sx, sy, sw, sh = render
        if not sh:
            return {}
        img = gui._grab_screen((sx, sy, sx + sw, sy + sh))
        px = img.convert("RGB").load()
        # ── 侧栏图标 y 聚类（暗色像素行）──
        rows = []
        for y in range(0, sh, 2):
            dark = 0
            for x in range(0, int(sw * 0.08), 3):
                r, g, b = px[x, y]
                if r < 150 and g < 150 and b < 150:
                    dark += 1
            if dark >= 2:
                rows.append(y)
        clusters = []
        if rows:
            start = prev = rows[0]
            for y in rows[1:]:
                if y - prev > 8:
                    clusters.append((start + prev) // 2)
                    start = y
                prev = y
            clusters.append((start + prev) // 2)
        # 过滤：只保留渲染区上部 80%（侧栏图标区）
        clusters = [c for c in clusters if 0.02 * sh < c < 0.85 * sh]
        # 保护：图标太少（窗口不可见/遮挡）→ 视为失败，不覆盖已有标定
        if len(clusters) < 5:
            return {}
        layout = {"sidebar_items": clusters, "sidebar_x_ratio": 50 / max(1, sw),
                  "render_w": int(sw), "render_h": int(sh)}
        if len(clusters) >= 3:
            step = (clusters[-1] - clusters[0]) / max(1, len(clusters) - 1)
            layout["sidebar_top_ratio"] = clusters[0] / sh
            layout["sidebar_step_ratio"] = step / sh
        _save_layout(layout)
        return layout
    except Exception:
        return {}


def icon_pos(name: str, gui) -> tuple | None:
    """按图标库换算元素的绝对屏幕坐标（DPI 无关：渲染区尺寸 × 比例）。
    返回 (x, y)；找不到返回 None。"""
    try:
        render = gui.render_rect or gui._update_render_rect() or (0, 0, 0, 0)
        sx, sy, sw, sh = render
        if not sh:
            return None
        spec = ICONS.get(name)
        if not spec:
            return None
        layout = _load_layout()
        if spec["type"] == "rel":
            rx, ry = spec["rel"]
            return sx + int(rx * sw), sy + int(ry * sh)
        # order 型（侧栏第 N 个图标）；窗口尺寸变化超过 15% → 自动重新标定（用户挪窗/改尺寸后仍准）
        if _layout_stale(gui):
            try:
                calibrate_ui(gui)
            except Exception:
                pass
        order = int(spec.get("order", 0))
        items = layout.get("sidebar_items") or []
        _lh = int(layout.get("render_h") or sh)
        _lw = int(layout.get("render_w") or sw)
        if order < len(items):
            # items[order] 是“标定时窗口下的绝对 y”，当前窗口高度可能不同（挪窗/DPI/分辨率），
            # 必须按 当前sh/标定sh 缩放，否则朋友圈等图标点偏（用户反馈点不到相机图标）。
            return sx + int(50 * (sw / 1278)), sy + int(items[order] * sh / _lh)
        top = layout.get("sidebar_top_ratio", _SIDEBAR["top_ratio"]) * sh
        step = layout.get("sidebar_step_ratio", _SIDEBAR["step_ratio"]) * sh
        return sx + int(50 * (sw / 1278)), sy + int(top + order * step)
    except Exception:
        return None


def hit(name: str, gui, right: bool = False, retries: int = 3) -> tuple:
    """瞬移点击图标库元素（闭环：**指纹自校验** + 命中窗口校验 + 重试）。返回 (ok, msg)。

    「点击正确性」的一环（W7c）：点之前先比一次**图标指纹**（`agent/ui_fingerprint.py`，
    按 微信版本×渲染尺寸×DPI 存）。指纹**明确对不上**就停手——因为那意味着这个位置上现在
    看着不像原来那个图标（微信更新了 UI / 窗口尺寸变了），照着过期比例盲点只会点到别处。
    没有记录或判据不可用（窗口最小化抓不到图）时**放行但留痕**，不把第一次用锁死。
    """
    from . import ui_adapt
    why2 = ""
    for i in range(retries):
        if not ui_adapt.prepare_screen(gui):
            return False, "屏幕预检失败"
        pos = icon_pos(name, gui)
        if pos is None:
            # 标定缺失：现场校准（不足 5 个图标会被 calibrate_ui 拒绝，不覆盖旧值）
            lay = calibrate_ui(gui)
            pos = icon_pos(name, gui)
            if pos is None:
                return False, "图标库无「%s」坐标（标定失败：微信窗口不可见？请先恢复微信窗口再试）" % name
        try:
            from . import ui_fingerprint as _ufp
            verdict, info = _ufp.verify(gui, name)
            if verdict is False:
                return False, info.get("reason") or ("「%s」图标指纹对不上，已停止点击" % name)
        except Exception:
            pass
        ok2, why2 = ui_adapt.click(gui, pos[0] - gui.origin_x, pos[1] - gui.origin_y, right=right)
        if ok2:
            return True, "已点击 %s" % name
        time.sleep(0.6)
    return False, "点击 %s 失败（重试 %d 次）：%s" % (name, retries, why2 or "")


def achieve(gui, fn: Callable, verify: Callable, desc: str, retries: int = 3, delay: float = 1.0) -> tuple:
    """闭环操作：执行 fn → verify 验证 → 失败重试（每次重新 prepare_screen）→ 放弃。

    fn: 无参可调用（执行动作，多为 hit/click）；verify: 无参可调用返回 bool。
    返回 (ok, 描述)。失败时会把最后一次 verify 失败作为原因。
    """
    last_why = ""
    for i in range(retries):
        try:
            if not ui_adapt.prepare_screen(gui):
                last_why = "屏幕预检失败"
                time.sleep(0.8)
                continue
            ok, why = fn()
            if not ok:
                last_why = why or "动作失败"
                time.sleep(0.8)
                continue
            time.sleep(delay)
            if verify():
                return True, "%s 成功" % desc
            last_why = "执行后验证未通过"
            time.sleep(0.8)
        except Exception as e:
            last_why = str(e)
    return False, "%s 失败（重试 %d 次）：%s" % (desc, retries, last_why)


# ═══════════ 微信子窗口关闭逻辑链 ═══════════
# 微信主窗只有一个（标题"微信"）；点侧栏图标会弹出独立子窗口（朋友圈/视频号/收藏…）。
# 逻辑链：做完工作 → 关闭子窗口（点右上角叉号）→ 验证已关；失败兜底 Alt+F4 → WM_CLOSE。

def _wechat_subwindows(main_hwnd) -> list:
    """枚举微信进程的可见顶层窗（不含主窗），返回 [(hwnd, title, rect)]。"""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(int(main_hwnd), ctypes.byref(pid))
    wx_pid = pid.value
    out = []

    def _info(h):
        t = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(h, t, 256)
        r = wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        return (t.value, (r.left, r.top, r.right, r.bottom))

    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, l):
        if h == int(main_hwnd):
            return True
        if not user32.IsWindowVisible(h):
            return True
        p2 = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(h, ctypes.byref(p2))
        if p2.value == wx_pid:
            t, rect = _info(h)
            if rect[2] - rect[0] > 220 and rect[3] - rect[1] > 160:
                out.append((h, t, rect))
        return True

    ref = CB(cb)
    user32.EnumWindows(ref, 0)
    return out


def close_subwindow(gui, hwnd, retries: int = 3) -> bool:
    """关闭一个微信子窗口：① 点右上角叉号 ② 验证 ③ Alt+F4 ④ WM_CLOSE。"""
    import ctypes
    user32 = ctypes.windll.user32
    for i in range(retries):
        try:
            user32.ShowWindow(int(hwnd), 9)  # SW_RESTORE（可能最小化了）
            user32.SetForegroundWindow(int(hwnd))
            time.sleep(0.3)
            r = wintypes.RECT()
            user32.GetWindowRect(int(hwnd), ctypes.byref(r))
            cx, cy = int(r.right - 14), int(r.top + 12)     # 右上角叉号
            # ⛔ 真鼠标点击前过 `real_guard`（归属 + SetCursorPos 返回值双重校验）：
            #    否则光标没到位时这一枪会落到用户当前真正指着的窗口上（2026-09-16 报障的机制本身）。
            from . import ui_adapt as _ua_c
            _ok, _why = _ua_c.real_guard(cx, cy, gui=gui, extra_hwnds=(int(hwnd),))
            if not _ok:
                log.warning("关子窗的点叉已拦下（不动鼠标）：%s", _why)
                raise RuntimeError(_why)     # 落到下面的 except ⇒ 直接走兜底 Alt+F4
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.8)
            if not user32.IsWindowVisible(int(hwnd)):
                return True
        except Exception:
            pass
        # 兜底 Alt+F4
        try:
            user32.SetForegroundWindow(int(hwnd))
            time.sleep(0.2)
            user32.keybd_event(0x12, 0, 0, 0)      # ALT down
            user32.keybd_event(0x73, 0, 0, 0)      # F4
            user32.keybd_event(0x73, 0, 0x0002, 0)
            user32.keybd_event(0x12, 0, 0x0002, 0)  # ALT up
            time.sleep(1.0)
            if not user32.IsWindowVisible(int(hwnd)):
                return True
        except Exception:
            pass
    # 最终兜底 WM_CLOSE
    try:
        user32.PostMessageW(int(hwnd), 0x0010, 0, 0)
        time.sleep(0.8)
        return not bool(user32.IsWindowVisible(int(hwnd)))
    except Exception:
        return False


def close_leftover_windows(gui) -> list:
    """关闭所有微信残留子窗口（工作完成后调用；返回已关列表）。"""
    closed = []
    try:
        for hwnd, title, rect in _wechat_subwindows(gui.main_hwnd):
            if close_subwindow(gui, hwnd):
                closed.append(title[:30] or hex(hwnd & 0xFFFFFFFF))
            time.sleep(0.4)
    except Exception:
        pass
    return closed
