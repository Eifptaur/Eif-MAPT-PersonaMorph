# -*- coding: utf-8 -*-
"""wechat_windows.py —— **按窗口标题定位会话窗口**（绕过像素定位的另一条路）

★为什么有它（2026-10-01 用户提出 + 当场实测验证）
  用户："**微信的群聊双击可以直接把那个群变为一个单一弹窗**……这个**对所有在会话栏里的东西都生效，
  包括私聊**"。他双击一次后，我方 `EnumWindows` 立刻看到：
```
微信(pid=9336) 可见的 Qt 顶层窗口 = 2 个：
  hwnd=150667342  标题=「群deepseek」     687x602   ← 主窗
  hwnd=41885108   标题=「aaa偷啃使用者」   613x647   ← ★双击后新出现的独立窗口
```
  ⇒ 三点全部证实：① 双击会话 ⇒ **出现新的独立顶层窗口**；② **该窗口标题 = 会话名**；
     ③ 句柄独立、可被 `EnumWindows` 找到。

★这条路的价值（**对"单群 / 少群"场景可能是最优解**）
  · **"当前打开的是哪个会话"** ⇒ 读窗口标题（一次 `GetWindowText`，不需要截图/OCR/比对）
  · **"切到目标群"**        ⇒ `EnumWindows` 找标题匹配的窗口 ⇒ 激活（**不需要点会话列表**）
  ⇒ **完全没有"像素精度"问题**（窗口边界由系统给），也**不依赖 wxocr**（零第三方、零灰色操作）。

⚠️ 边界（实测/未实测，如实标注）
  · **依赖用户先把目标会话双击成独立窗口**（配置成本；可做成一次性引导）；
  · **主窗标题也 = 当前会话名**（实测 `群deepseek`）⇒ 所以"标题匹配"要**同时考虑主窗与独立窗**；
  · **微信重启后独立窗口是否保留 —— 未实测**（若不留，需要"自动重开"，但"双击列表行"比"精确定位某行"宽容得多）；
  · **独立窗内发送是否照常 —— 未实测**（输入框/发送键应在，但需真机确认）。
"""
from __future__ import annotations

import ctypes as _ct
import ctypes.wintypes as _wt

_user32 = _ct.windll.user32
_G32 = _ct.windll.gdi32

#: 微信主窗 + 独立会话窗的类名（实测 4.1.15.8 都是它）
WECHAT_QT_CLASS = "Qt51514QWindowIcon"

_WNDENUMPROC = _ct.WINFUNCTYPE(_ct.c_bool, _ct.c_int, _ct.c_int)


class _RECT(_ct.Structure):
    _fields_ = [("left", _ct.c_long), ("top", _ct.c_long),
                ("right", _ct.c_long), ("bottom", _ct.c_long)]


def list_wechat_windows(image_name: str = "Weixin.exe") -> list:
    """枚举**所有微信进程的可见顶层窗口** ⇒ `[{hwnd, pid, cls, title, w, h}]`。

    只返回**可见**窗口（隐藏的系统消息窗、托盘窗一律排除 —— 实测它们标题为空或类名不是 Qt）。
    """
    # 目标 pid 集合：按进程名收集（避免把别的进程窗口混进来）
    pids = set()
    for pid in _enum_pids(image_name):
        pids.add(pid)
    if not pids:
        return []
    out = []

    def _cb(hwnd, _lp):
        pid = _wt.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, _ct.byref(pid))
        if pid.value not in pids:
            return True
        if not _user32.IsWindowVisible(hwnd):
            return True
        cls = _ct.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, cls, 256)
        title = _ct.create_unicode_buffer(512)
        _user32.GetWindowTextW(hwnd, title, 512)
        r = _wt.RECT()
        _user32.GetWindowRect(hwnd, _ct.byref(r))
        out.append({
            "hwnd": int(hwnd), "pid": int(pid.value), "cls": cls.value,
            "title": title.value, "w": int(r.right - r.left), "h": int(r.bottom - r.top),
        })
        return True

    _user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    return out


def _enum_pids(image_name: str) -> list:
    """用 `tasklist` 取该进程名的所有 pid（兼容多开/多账号）。

    ⚠️ `tasklist` 在中文 Windows 上输出是 **GBK**（不是 UTF-8）⇒ 必须显式解码，
    否则会 `UnicodeDecodeError`（实测踩过）。
    """
    import subprocess
    try:
        p = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % image_name, "/FO", "CSV"],
                           capture_output=True)
        txt = (p.stdout or b"").decode("gbk", "ignore")
    except Exception:
        return []
    pids = []
    for line in txt.splitlines()[1:]:
        cols = [c.strip('"') for c in line.split('","')]
        if len(cols) >= 2 and cols[1].isdigit():
            pids.append(int(cols[1]))
    return pids


def strip_unread_suffix(title: str) -> str:
    """剥掉标题尾部的未读计数（实测独立窗标题形如 `aaa偷啃使用者 (3)`）。

    ⛔ 为什么必须有它（2026-10-01 实测）：独立会话窗的标题**带未读数** ——
       `GetWindowText` 拿到的是 `aaa偷啃使用者 (3)` 而不是 `aaa偷啃使用者`。
       而主窗标题**不带**（实测 `群deepseek`）。⇒ 直接精确比对会**匹配失败**。
    判据：只剥**末尾**的 ` (纯数字)`（会话名本身可能含括号 ⇒ 不能全局替换）。
    """
    import re as _re
    return _re.sub(r"\s*[（(]\s*\d+\s*[)）]\s*$", "", (title or "").strip())


def find_chat_window(name: str, exact: bool = True) -> dict:
    """按**会话名**找窗口 ⇒ `{hwnd, title, exact, candidates}`；找不到给 `hwnd=0`。

    ⚠️ 主窗的标题**也是**"当前打开的那个会话名"（实测 `群deepseek`）⇒
       所以本函数会**同时命中主窗与独立窗**；调用方按需选（想"不影响主窗"就优先取非主窗）。
    """
    wins = [w for w in list_wechat_windows() if w["title"].strip()]
    _want = (name or "").strip()
    if exact:
        # ⭐ 两边都剥掉未读计数后缀再比（独立窗标题带 `(3)`、主窗不带）
        hit = [w for w in wins if strip_unread_suffix(w["title"]) == _want]
    else:
        n = _want.lower()
        hit = [w for w in wins if n and n in strip_unread_suffix(w["title"]).lower()]
    if not hit:
        return {"hwnd": 0, "title": "", "exact": bool(exact),
                "candidates": [w["title"] for w in wins]}
    # 优先"独立窗"（尺寸较小者通常是双击出来的会话窗），主窗兜底
    hit.sort(key=lambda w: (w["w"] * w["h"]))
    return {"hwnd": hit[0]["hwnd"], "title": hit[0]["title"], "exact": bool(exact),
            "candidates": [w["title"] for w in wins]}


def main():
    """自检/演示：列出当前所有微信窗口 + 试按标题找。"""
    import sys
    wins = list_wechat_windows()
    print("微信可见顶层窗口 %d 个：" % len(wins))
    for w in wins:
        print("  hwnd=%-12d %-32s %-28s %dx%d"
              % (w["hwnd"], w["cls"][:30], w["title"][:26], w["w"], w["h"]))
    if len(sys.argv) > 1:
        r = find_chat_window(sys.argv[1])
        print("\n按标题找 %r ⇒ %s" % (sys.argv[1], r))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())


# ─────────────────────────────────────────────────────────────────────────────
# 抓图（**必须按 DPI 倍率建位图** —— 这是踩过一个大坑后定下来的）
# ─────────────────────────────────────────────────────────────────────────────

def window_scale(hwnd: int) -> float:
    """窗口的 DPI 缩放倍率（`GetDpiForWindow/96`）；取不到则退回 1.0。

    ⛔ **为什么必须按它建位图**（2026-10-01 实测，报告 §52）：
      `GetWindowRect` 返回的是**逻辑像素**（已按 DPI 折算），而窗口**按物理像素渲染**。
      用逻辑尺寸的位图去接 `PrintWindow` ⇒ **只画得出左上角约 1/scale² 的区域**，其余留底色。
      实测（本机 DPI=144 ⇒ scale=1.5）：
        · 逐字节地看"内容像素占比"（＝除背景色以外的像素比例）
        独立窗 逻辑尺寸位图 → **1.98%**；物理尺寸位图 → **11.12%**（正常窗口约 8~20%）
        · 主窗因内容多（37%）**掩盖了这个 bug** —— 所以"主窗一直能用"不等于"抓法是对的"。
      ⇒ **凡抓图，位图一律用 `逻辑尺寸 × window_scale()`**。
    """
    try:
        dpi = _user32.GetDpiForWindow(hwnd)
        if dpi and dpi > 0:
            return float(dpi) / 96.0
    except Exception:
        pass
    return 1.0


def grab_window(hwnd: int, physical: bool = True, flag: int = 2):
    """抓某窗口 ⇒ `(PIL.Image | None, meta)`；**按 DPI 倍率建位图**（见 `window_scale`）。

    `meta` 含 `logical`(GetWindowRect 的逻辑尺寸) / `scale` / `physical_size`，
    供调用方把"图上像素"换算回"逻辑/客户区坐标"（`逻辑 = 图上 / scale`）。
    """
    try:
        from PIL import Image  # noqa: F401
    except Exception:
        return None, {"err": "PIL 不可用"}
    _wt_rect = _wt.RECT()
    _user32.GetWindowRect(hwnd, _ct.byref(_wt_rect))
    r = _wt_rect
    lw, lh = int(r.right - r.left), int(r.bottom - r.top)
    if lw <= 0 or lh <= 0:
        return None, {"err": "窗口尺寸异常", "logical": (lw, lh)}
    sc = window_scale(hwnd) if physical else 1.0
    w, h = max(1, int(lw * sc)), max(1, int(lh * sc))
    hdc = _user32.GetWindowDC(hwnd)
    mdc = _G32.CreateCompatibleDC(hdc)
    bmp = _G32.CreateCompatibleBitmap(hdc, w, h)
    _G32.SelectObject(mdc, bmp)
    ok = _user32.PrintWindow(hwnd, mdc, flag)

    class _BMIH(_ct.Structure):
        _fields_ = [("biSize", _ct.c_uint32), ("biWidth", _ct.c_long), ("biHeight", _ct.c_long),
                    ("biPlanes", _ct.c_ushort), ("biBitCount", _ct.c_ushort),
                    ("biCompression", _ct.c_uint32), ("biSizeImage", _ct.c_uint32),
                    ("biXPelsPerMeter", _ct.c_long), ("biYPelsPerMeter", _ct.c_long),
                    ("biClrUsed", _ct.c_uint32), ("biClrImportant", _ct.c_uint32)]
    bi = _BMIH()
    bi.biSize = _ct.sizeof(_BMIH)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = _ct.create_string_buffer(w * h * 4)
    _G32.GetDIBits(mdc, bmp, 0, h, buf, _ct.byref(bi), 0)
    from PIL import Image as _I
    img = _I.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    _G32.DeleteObject(bmp)
    _G32.DeleteDC(mdc)
    _user32.ReleaseDC(hwnd, hdc)
    return img, {"ok": bool(ok), "logical": (lw, lh), "scale": sc, "physical_size": (w, h)}


def content_ratio(img) -> tuple:
    """图上"除背景以外"的像素占比（%）+ 主色 + 主色占比 —— **抓图质量体检**。

    用途：实测"逻辑尺寸位图"会掉到 1~2%、"物理尺寸位图"回到 8~20% ⇒
     ⇒ **任何抓图改动都该用这个数验收**，而不是"看起来有图就行"。
    """
    from collections import Counter
    px = img.load()
    w, h = img.size
    c = Counter()
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b = px[x, y][:3]
            c[(r // 8 * 8, g // 8 * 8, b // 8 * 8)] += 1
    tot = sum(c.values()) or 1
    bg, bgn = c.most_common(1)[0]
    keep = n = 0
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            r, g, b = px[x, y][:3]
            n += 1
            if not (abs(r - bg[0]) <= 14 and abs(g - bg[1]) <= 14 and abs(b - bg[2]) <= 14):
                keep += 1
    return 100.0 * keep / max(1, n), bg, 100.0 * bgn / tot


# ─────────────────────────────────────────────────────────────────────────────
# 独立弹窗内的两个关键控件（**比例坐标**，跨尺寸稳定）
# ─────────────────────────────────────────────────────────────────────────────

#: 「发送」按钮中心的**比例位置**（实测标定，图 1182×970；见报告 §56）
SEND_BTN_REL = (0.938, 0.948)
#: 输入框内可点的位置（避开工具栏与发送键）：中点偏上
INPUT_REL = (0.50, 0.85)
#: 输入框矩形（比例）
INPUT_BOX_REL = (0.019, 0.789, 0.981, 0.974)


def is_green_px(p, d: int = 20) -> bool:
    """这个像素是不是"绿"（用于判**发送键是否激活**）。"""
    try:
        r, g, b = p[0], p[1], p[2]
    except Exception:
        return False
    return (g > r + d) and (g > b + d)


#: 「发送」按钮的**采样矩形**（比例，围绕按钮中心稍放大，确保覆盖"键"而不越界）
SEND_BTN_BOX_REL = (0.895, 0.925, 0.980, 0.970)


def send_button_green(img, box=SEND_BTN_BOX_REL, min_ratio: float = 0.30) -> bool:
    """**「发送」键是否已激活（变绿）** ⇒ 等价于"输入框里确实有字"。

    ⛔ 为什么用它（2026-10-01 用户给出 + 实测）：
      用户："**只有你输入框里面有文字的时候，它才会变成绿色**；
            变绿的话，**那个字会变成白色**；没发的时候两个灰比较接近，字只比键深一点"。
      实测（未输入）：按钮区域 `(214,214,214)`（字） / `(236,236,236)`（键）⇒ 都灰；
      绿色参照（用户发的绿气泡）= `(175,239,165)`。

    ⚠️ **为什么不是"取中心像素的中位色"**（我第一版就错了）：
      激活态是「**绿底 + 白字**」⇒ 中心像素可能正好落在**白字**上 ⇒ 中位色判绿会**漏判**。
      ⇒ 改成 **统计矩形内"绿像素"的占比**（`>= min_ratio` 才算激活）。
      这样"绿底白字"里绿仍占多数，"灰底灰字"里几乎没有绿 ⇒ 两侧都稳。

    ★这是**投递键盘"是否真的打进去了"的可靠确认**（比 OCR 回读输入框更快更稳：
      禁用态"发送"二字对比度极低、`pane_text` 实测返回空，见报告 §56.3）。
    ⇒ **用法**：投递输入后**轮询本函数**，变绿才算成功；不变绿就重试或改真实键盘。
    """
    try:
        w, h = img.size
        x0, y0 = int(w * box[0]), int(h * box[1])
        x1, y1 = int(w * box[2]), int(h * box[3])
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, max(x1, x0 + 1)), min(h, max(y1, y0 + 1))
        n = green = 0
        step = max(1, (x1 - x0) // 24)
        for yy in range(y0, y1, step):
            for xx in range(x0, x1, step):
                n += 1
                if is_green_px(img.getpixel((xx, yy))[:3]):
                    green += 1
        return bool(n) and (float(green) / n >= float(min_ratio))
    except Exception:
        return False


def send_button_green_ratio(img, box=SEND_BTN_BOX_REL) -> float:
    """返回采样矩形内的**绿像素占比**（诊断用；正常态≈0，激活态应显著>0）。"""
    try:
        w, h = img.size
        x0, y0 = max(0, int(w * box[0])), max(0, int(h * box[1]))
        x1, y1 = min(w, int(w * box[2])), min(h, int(h * box[3]))
        n = green = 0
        step = max(1, (x1 - x0) // 24)
        for yy in range(y0, y1, step):
            for xx in range(x0, x1, step):
                n += 1
                if is_green_px(img.getpixel((xx, yy))[:3]):
                    green += 1
        return (float(green) / n) if n else 0.0
    except Exception:
        return 0.0


def rel_to_px(img_or_size, rel):
    """比例 ⇒ 图上像素（供换算到客户区/屏幕坐标用）。"""
    try:
        w, h = img_or_size.size
    except AttributeError:
        w, h = img_or_size
    return int(w * rel[0]), int(h * rel[1])


def popup_controls(scale: float = 1.0) -> dict:
    """独立弹窗两个控件的**逻辑坐标**比例说明（供点击用）。

    `grab_window(physical=True)` 给的是**物理像素图** ⇒ 图上坐标除以 `scale` 才是**逻辑坐标**，
    再加窗口客户区在屏幕上的原点，即得**屏幕坐标**（注意 `GetWindowRect` 也是逻辑像素，两者自洽）。
    """
    return {
        "send_btn_rel": SEND_BTN_REL,
        "input_rel": INPUT_REL,
        "input_box_rel": INPUT_BOX_REL,
        "scale": float(scale),
        "note": "图上坐标 / scale = 逻辑坐标；逻辑坐标 + 窗口左上角(逻辑) = 屏幕坐标",
    }
