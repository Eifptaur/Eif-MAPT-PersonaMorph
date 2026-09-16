# -*- coding: utf-8 -*-
"""按「微信版本 × 渲染区尺寸 × DPI」存的**图标指纹表** —— 点之前先自校验（W7c）。

为什么要有它（AGENTS §2.3 / 用户 2026-09-14 点名的"点击正确性"那一项）：
  现在的坐标是「渲染区尺寸 × 比例」算出来的，**比例对不对没人验**：
  · `data/ui_layout.json` 里的标定尺寸（237）和当前窗口（1139）差一个量级，
    驱动库自己打了"忽略本次校准"——也就是说那套比例早已过期，没人知道点的是哪儿；
  · 微信一更新 UI（4.x 三次大改），比例还能"算出一个数"，但那个数落在哪儿只有天知道。
  ⇒ 本模块给每个**命名目标**（侧栏图标 / 输入框 / 发送键 / …）存一份**图标内容的指纹**
    （目标点周围 48×48 的 dHash 64 位 + 亮度统计），键＝微信版本 + 适配层版本 + 渲染区尺寸 + DPI。
    点击前先比指纹：**对不上就不点**（如实报"图标变了，请重新取指纹"），而不是照着一个过期比例盲点。

三条硬规矩：
  ① 指纹只能"看"，不能"猜"：抓不到画面 / 画面全黑 ⇒ 报"取不到"，**绝不写一条假指纹**；
  ② 没有记录 ≠ 拦死：没指纹时放行但**留痕**（否则第一次用就被锁死）；
  ③ 版本或尺寸变了就是**另一把钥匙**：旧指纹不参与比对（微信按尺寸重排 UI，跨尺寸复用等于自欺）。
"""
import ctypes
import hashlib
import io
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
FP_FILE = os.path.join(DATA_DIR, "ui_fingerprints.json")

CROP = 48            # 目标点周围取多大一块（像素，渲染区坐标系）
HASH_W, HASH_H = 9, 8
MAX_DIST = 10        # 64 位 dHash 的汉明距离阈值（超过即判"不是同一个图标"）


# ── 基础 ────────────────────────────────────────────────────────────────

def _dpi_of(hwnd) -> int:
    try:
        return int(ctypes.windll.user32.GetDpiForWindow(int(hwnd)) or 96)
    except Exception:
        return 96


def adapter_version() -> str:
    """适配层版本（**装着的那个**，不是最低要求）：拿不到就记 unknown，绝不编。

    踩过的坑（2026-09-15）：先去找 `wechat.ADAPTER_VERSION` 之类的常量会拿到 **MIN_VERSION
    （1.1.5.1）**——那是"最低要求"，不是"当前安装"，用它当钥匙会把指纹挂到错误的环境上。
    正路只有 `replica_adapter.installed_version()`（唯一收口点）。
    """
    try:
        from . import replica_adapter as _ra
        v = str(_ra.installed_version() or "")
        if v:
            return v
    except Exception:
        pass
    try:
        import importlib.metadata as _md
        return str(_md.version("wechatauto-replica"))
    except Exception:
        return "unknown"


def wechat_version() -> str:
    try:
        from . import version_matrix as _vm
        cur = _vm.current() or {}
        return str((cur.get("wechat") if isinstance(cur, dict) else "") or "unknown")
    except Exception:
        return "unknown"


def _render_of(gui) -> tuple:
    """渲染区矩形：有 gui 就用 gui 的；没有就退回"主窗 + 渲染子窗"两跳（不初始化驱动库）。"""
    if gui is not None:
        try:
            r = gui.render_rect or gui._update_render_rect()
            if r and int(r[2] - r[0]) > 0:
                return tuple(int(v) for v in r)
        except Exception:
            pass
    try:
        from . import input_backend as _ib
        main = _ib.find_main_window()
        hwnd = _ib.find_render_child(main) or main
        if not hwnd:
            return (0, 0, 0, 0)
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(int(hwnd), ctypes.byref(r))
        return (r.left, r.top, r.right, r.bottom)
    except Exception:
        return (0, 0, 0, 0)


def key(gui=None, render=None, wx_ver: str = None, ad_ver: str = None) -> str:
    """当前环境的钥匙：微信版本 + 适配层版本 + 渲染区尺寸 + DPI（四样都变就换钥匙）。"""
    if render is None:
        render = _render_of(gui)
    render = render or (0, 0, 0, 0)
    w, h = int(render[2] - render[0]), int(render[3] - render[1])
    hwnd = 0
    try:
        hwnd = int(getattr(gui, "main_hwnd", 0) or 0)
    except Exception:
        hwnd = 0
    if not hwnd:
        try:
            from . import input_backend as _ib2
            hwnd = int(_ib2.find_main_window() or 0)
        except Exception:
            hwnd = 0
    return "wx%s|ad%s|%dx%d|dpi%d" % (wx_ver or wechat_version(), ad_ver or adapter_version(), w, h, _dpi_of(hwnd))


def dhash(img_or_pixels, size=(HASH_W, HASH_H)) -> str:
    """dHash：缩到 9×8 灰度 → 逐行比较左右像素 → 64 位 → 16 位 hex。

    选 dHash 而不是"原图哈希"：微信图标有抗锯齿、轻微位移（±1~2px）就会让原图哈希全变，
    而 dHash 只关心"亮暗梯度"，对位移与缩放都稳。
    """
    try:
        from PIL import Image
        img = img_or_pixels
        if not hasattr(img, "convert"):
            return ""      # 不是图像（None/坏输入）⇒ 空指纹，绝不编一个"看起来像"的哈希出来
        g = img.convert("L").resize(size)
        px = g.load()
        bits = 0
        i = 0
        for y in range(size[1]):
            for x in range(size[0] - 1):
                if px[x, y] > px[x + 1, y]:
                    bits |= (1 << i)
                i += 1
        return "%016x" % bits
    except Exception:
        return ""


def dist(a: str, b: str) -> int:
    """两个 dHash 的汉明距离；任一侧无效返回 64（＝最远）。"""
    try:
        if not a or not b or len(a) != len(b):
            return 64
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except Exception:
        return 64


# ── 抓图与取指纹 ────────────────────────────────────────────────────────

def _grab(gui):
    """渲染区图像：优先 PrintWindow（遮挡也能拿），失败退回抓屏；拿不到返回 None。"""
    try:
        from . import chat_header as _ch
        img = _ch.grab_render(gui)
        if img is not None:
            return img
    except Exception:
        pass
    try:
        from PIL import ImageGrab
        r = gui.render_rect
        return ImageGrab.grab((int(r[0]), int(r[1]), int(r[2]), int(r[3])))
    except Exception:
        return None


def _crop(img, pt_render, size=CROP):
    """按渲染区相对点取一块（夹在图像内）。返回 (crop, 实际中心)。"""
    try:
        w, h = img.size
        cx, cy = int(pt_render[0]), int(pt_render[1])
        half = size // 2
        l = max(0, min(w - 1, cx - half))
        t = max(0, min(h - 1, cy - half))
        r = max(1, min(w, l + size))
        b = max(1, min(h, t + size))
        return img.crop((l, t, r, b)), (cx, cy)
    except Exception:
        return None, None


def _stats(crop):
    """(均值, 标准差)——用来判"这一小块是不是空白/全黑"（拿不到画面时不能写指纹）。"""
    try:
        hist = crop.convert("L").histogram()
        n = float(sum(hist)) or 1.0
        mean = sum(i * c for i, c in enumerate(hist)) / n
        var = sum(((i - mean) ** 2) * c for i, c in enumerate(hist)) / n
        return mean, var ** 0.5
    except Exception:
        return 0.0, 0.0


def capture(gui, name: str, pts: dict = None) -> dict:
    """给一个命名目标取指纹。返回记录 dict（取不到时 `{"ok": False, "why": ...}`）。"""
    try:
        from . import wechat_ui as _wu
        rec = dict(pts or {})
        if name not in rec:
            pos = _wu.icon_pos(name, gui)
            if not pos:
                return {"ok": False, "why": "算不出「%s」的坐标（图标库没有它 / 标定失败）" % name}
            r = gui.render_rect
            rec[name] = (int(pos[0] - r[0]), int(pos[1] - r[1]))
        img = _grab(gui)
        if img is None:
            return {"ok": False, "why": "取不到渲染区画面（窗口最小化或被完全遮住）"}
        crop, center = _crop(img, rec[name])
        if crop is None:
            return {"ok": False, "why": "裁剪失败"}
        mean, std = _stats(crop)
        if mean < 6 or std < 2:
            return {"ok": False, "why": "画面近乎空白（均值 %.1f / 标准差 %.1f）⇒ 不写指纹" % (mean, std)}
        return {"ok": True, "name": name, "hash": dhash(crop), "mean": round(mean, 1),
                "std": round(std, 1), "pt": [int(center[0]), int(center[1])], "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    except Exception as e:
        return {"ok": False, "why": str(e)[:120]}


def take(gui, names=None) -> dict:
    """给一批目标取指纹并落盘。返回 {"key":…, "ok":[…], "failed":[…] }。"""
    try:
        from . import wechat_ui as _wu
    except Exception:
        _wu = None
    names = list(names or (list(_wu.ICONS.keys()) if _wu else []))
    k = key(gui)
    db = load()
    bucket = dict(db.get(k) or {})
    okn, bad = [], []
    for nm in names:
        r = capture(gui, nm)
        if r.get("ok"):
            bucket[nm] = r
            okn.append(nm)
        else:
            bad.append("%s（%s）" % (nm, r.get("why")))
    if okn:
        db[k] = bucket
        db["_meta"] = {"last_key": k, "last_ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        save(db)
    return {"key": k, "ok": okn, "failed": bad}


# ── 存取（原子写）─────────────────────────────────────────────────────────

def load(path: str = None) -> dict:
    p = path or FP_FILE
    try:
        with io.open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save(db: dict, path: str = None) -> bool:
    p = path or FP_FILE
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(db, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


# ── 点击前自校验 ────────────────────────────────────────────────────────

def verify(gui, name: str, max_dist: int = MAX_DIST) -> tuple:
    """点击前自校验。返回 (verdict, info)：

      verdict=True  ⇒ 指纹对得上（或历史指纹"模糊接近"），放行
      verdict=False ⇒ **指纹对不上**：图标/布局变了 ⇒ 不点
      verdict=None  ⇒ 没有这个 key 的记录：放行但留痕（第一次用不该被锁死）

    info 里带 `reason`（话术）、`dist`、`have`（记录数）。
    """
    k = key(gui)
    bucket = (load().get(k) or {})
    rec = bucket.get(name)
    if not isinstance(rec, dict) or not rec.get("hash"):
        return None, {"reason": "本环境（%s）还没有「%s」的指纹记录，放行但留痕" % (k, name),
                      "dist": None, "have": len(bucket), "key": k}
    cur = capture(gui, name)
    if not cur.get("ok"):
        # 抓不到画面 ⇒ 判据不可用，**不冒充"对不上"**（也不当"对得上"）
        return None, {"reason": "判据不可用：%s" % cur.get("why"), "dist": None,
                      "have": len(bucket), "key": k}
    d = dist(cur["hash"], rec["hash"])
    if d <= max_dist:
        return True, {"reason": "指纹一致（距离 %d/%d）" % (d, max_dist), "dist": d,
                      "have": len(bucket), "key": k}
    return False, {"reason": ("图标指纹对不上（距离 %d > 阈值 %d）：这个位置上现在看着"
                              "不像原来的「%s」——微信可能更新了 UI 或改了窗口尺寸。"
                              "按「看不清就不点」处理，请到控制台点一次「重新取指纹」"
                              % (d, max_dist, name)),
                   "dist": d, "have": len(bucket), "key": k}


def hits() -> dict:
    """给控制台/API 的状态：每个 key 有多少条指纹、都是什么时候取的。"""
    db = load()
    out = {"file": os.path.relpath(FP_FILE, ROOT), "keys": {}, "current": key()}
    for k, v in db.items():
        if k.startswith("_") or not isinstance(v, dict):
            continue
        out["keys"][k] = {"n": len(v), "names": sorted(v.keys()),
                          "last": max([str((r or {}).get("ts") or "") for r in v.values()] or [""])}
    return out


def forget(key_str: str = None) -> dict:
    """丢掉某个 key（或全部）的指纹——UI 大改之后按需重取。"""
    db = load()
    if key_str:
        db.pop(key_str, None)
    else:
        db = {}
    save(db)
    return {"ok": True, "keys": len([k for k in db if not k.startswith("_")])}


def digest() -> str:
    """指纹表的短摘要（控制台显示用，便于核对"是不是同一份"）。"""
    db = load()
    body = json.dumps(db, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
