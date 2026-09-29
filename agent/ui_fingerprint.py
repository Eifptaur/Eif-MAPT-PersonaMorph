# -*- coding: utf-8 -*-
"""按「微信版本 × 渲染区尺寸 × DPI」存的**图标指纹表** —— 点之前先自校验（W7c）。

为什么要有它：
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
import logging
import os
import time
from . import persist # noqa: E402 原子换档：撞共享冲突要退避重试（见 persist.replace_into）

log = logging.getLogger("persona-morph")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
FP_FILE = os.path.join(DATA_DIR, "ui_fingerprints.json")

CROP = 48 # 目标点周围取多大一块（像素，渲染区坐标系）
HASH_W, HASH_H = 9, 8
MAX_DIST = 10 # 64 位 dHash 的汉明距离阈值（超过即判"不是同一个图标"）
#: ⭐ 允许的**位置容差**（像素）：在 ±TOL_PX 窗口内取"最像"的那一版再比指纹。
#: 为什么必须有它（实测，脚本 `scripts/ui_fp_measure.py`，355 个非空白位置）：
#:   标定/渲染只要偏 1~3px，**79% 的位置**就会因类内距离（最大 48）超过阈值 10 而被误判成
#:   "图标变了"（用户被迫反复"重新取指纹"）。加上 ±3px 取最优后，**类内最大距离降到 0、误拒 0/355**。
#: 反过来说：容差只用来吸收"同一目标的小位移"，偏 10px 必须仍然判"不是"（判据里有反向锚守着）。
TOL_PX = 3
#: 抓图帧缓存：同一帧里连续校验多个目标时**只抓一次**（PrintWindow 整窗是这条链路上最贵的一步）
FRAME_TTL_MS = 200


# ── 基础 ────────────────────────────────────────────────────────────────

def _dpi_of(hwnd) -> int:
    try:
        return int(ctypes.windll.user32.GetDpiForWindow(int(hwnd)) or 96)
    except Exception:
        return 96


def adapter_version() -> str:
    """适配层版本（**装着的那个**，不是最低要求）：拿不到就记 unknown，绝不编。

    踩过的坑：先去找 `wechat.ADAPTER_VERSION` 之类的常量会拿到 **MIN_VERSION
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
        img = img_or_pixels
        if not hasattr(img, "convert"):
            return "" # 不是图像（None/坏输入）⇒ 空指纹，绝不编一个"看起来像"的哈希出来
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
        return ImageGrab.grab((int(r[0]), int(r[1]), int(r[2]), int(r[3])), all_screens=True)
    except Exception:
        return None


#: 最近一次抓图（`(img, gui_id, ts)`）：同一帧内多个目标只抓一次
_LAST_GRAB = {"img": None, "who": None, "ts": 0.0}


def grab_frame(gui, max_age_ms: int = None):
    """**整窗抓图（带短时缓存）**：同一次校验流程里给多个目标取画面时只抓一次。

    为什么要缓存：`_grab()` 走 PrintWindow/抓屏**整窗**，是这条链路上最贵的一步；
    以前 `verify()` 每校验一个目标就抓一次（N 个目标 = N 次整窗截图）。
    缓存口径：**同一窗口 + `FRAME_TTL_MS` 之内**才复用（过了就重抓，避免拿着旧画面下结论）。
    """
    age = (FRAME_TTL_MS if max_age_ms is None else int(max_age_ms))
    now = time.time() * 1000.0
    who = None
    try:
        who = (id(gui), tuple(getattr(gui, "render_rect", ()) or ()))
    except Exception:
        who = None
    if (_LAST_GRAB["img"] is not None and _LAST_GRAB["who"] == who
            and (now - _LAST_GRAB["ts"]) <= age):
        return _LAST_GRAB["img"]
    img = _grab(gui)
    if img is not None:
        _LAST_GRAB.update({"img": img, "who": who, "ts": now})
    return img


def reset_frame_cache() -> None:
    """清掉抓图帧缓存（窗口移动/尺寸变化后必须清，否则会拿旧画面下结论）。"""
    _LAST_GRAB.update({"img": None, "who": None, "ts": 0.0})


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


def capture(gui, name: str, pts: dict = None, img=None) -> dict:
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
        img = img if img is not None else grab_frame(gui)   # 批量时由调用方传同一帧
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
        persist.replace_into(tmp, p)
        return True
    except Exception:
        return False


# ── 点击前自校验 ────────────────────────────────────────────────────────

# ══════════════ 偏移学习（把"素材/实跑"变成准确率的第一步）══════════════
# ⛔ 口径先说清：**指纹比对是"查表"，不是"学习"** —— 素材再多，也只是多了几张截图，
#    不会自己变准。要变准必须有**闭环**：把每次校验实测到的"最像的一版偏了多少"累计起来，
#    学出**系统性偏移**（标定误差、主题/DPI 造成的整体位移），下次直接从校正后的位置开始找。
#    这是本模块里**第一个真正的学习环**；它只影响"从哪儿开始找"，**不放松任何判定**。
#: 至少观测这么多次才敢"学"（少样本一律不应用 —— 别把两三帧的抖动当规律）
LEARN_MIN_N = 8
#: 稳健离散度上限（MAD＝中位数绝对偏差）：超过它说明是**抖动**而不是系统性偏移
LEARN_STABLE_MAD = 1.5
#: 偏移小于这个像素数不值得校正（1px 无关紧要，校正反而增加复杂度）
LEARN_MIN_PX = 2
#: 校正上限（安全）：绝不把"从哪儿开始找"挪出这么多像素
LEARN_MAX_PX = 8


def _median(xs: list) -> float:
    s = sorted(xs)
    n = len(s)
    if not n:
        return 0.0
    return float(s[n // 2]) if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _mad(xs: list, med: float) -> float:
    """MAD＝中位数绝对偏差（比标准差更抗离群点：机位抖一两帧不会把结论带跑）。"""
    return _median([abs(x - med) for x in xs]) if xs else 0.0


def learn_offset(rec: dict, off) -> dict:
    """把**一次通过校验的实测偏移**记进 `rec["learn"]`（原地更新并返回 learn 段）。

    ⛔ 只允许记"通过校验"的样本：对不上的样本里，那个 offset 是**乱找出来的**，不是标定误差，
       记进去会把学习带偏（判据 L7 守着）。
    """
    try:
        dx, dy = int(off[0]), int(off[1])
    except Exception:
        return rec.get("learn") or {}
    if abs(dx) > TOL_PX or abs(dy) > TOL_PX:
        return rec.get("learn") or {}      # 超出容差范围的样本本身可疑，不参与学习
    lr = rec.get("learn")
    if not isinstance(lr, dict):
        lr = {"xs": [], "ys": []}
    xs, ys = list(lr.get("xs") or []), list(lr.get("ys") or [])
    xs.append(dx)
    ys.append(dy)
    # 有界：只留最近 64 次（环境变了要能跟上，而不是被古老样本钉住）
    lr["xs"], lr["ys"] = xs[-64:], ys[-64:]
    lr["n"] = len(lr["xs"])
    lr["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    rec["learn"] = lr
    return lr


def learned_shift(rec: dict):
    """学到的**系统性偏移** `(dx, dy)`；样本不够 / 不稳 / 太小 ⇒ `(0, 0)`（不校正）。"""
    lr = rec.get("learn") if isinstance(rec, dict) else None
    if not isinstance(lr, dict):
        return (0, 0)
    xs, ys = list(lr.get("xs") or []), list(lr.get("ys") or [])
    if len(xs) < LEARN_MIN_N or len(ys) < LEARN_MIN_N:
        return (0, 0)                      # 样本不够 ⇒ 不学（防抖）
    mx, my = _median(xs), _median(ys)
    if _mad(xs, mx) > LEARN_STABLE_MAD or _mad(ys, my) > LEARN_STABLE_MAD:
        return (0, 0)                      # 离散度大 ⇒ 是抖动，不是系统性偏移
    if max(abs(mx), abs(my)) < LEARN_MIN_PX:
        return (0, 0)                      # 太小，不值得校正
    cap = LEARN_MAX_PX
    return (int(max(-cap, min(cap, round(mx)))), int(max(-cap, min(cap, round(my)))))


def learn_stats(rec: dict) -> dict:
    """给控制台看的读数：样本数、学到的偏移、离散度。"""
    lr = rec.get("learn") if isinstance(rec, dict) else None
    if not isinstance(lr, dict):
        return {"n": 0, "shift": [0, 0], "mad": [0.0, 0.0], "applied": False}
    xs, ys = list(lr.get("xs") or []), list(lr.get("ys") or [])
    mx, my = _median(xs), _median(ys)
    sh = learned_shift(rec)
    return {"n": len(xs), "shift": list(sh), "median": [mx, my],
            "mad": [round(_mad(xs, mx), 2), round(_mad(ys, my), 2)],
            "applied": sh != (0, 0)}


def reset_learning(key_str: str = None, name: str = None) -> dict:
    """清掉学习到的偏移（换环境/换微信版本后重来）。不给参数＝全清。"""
    db = load()
    n = 0
    for k, bucket in list(db.items()):
        if not isinstance(bucket, dict):
            continue
        if key_str and k != key_str:
            continue
        for nm, rec in list(bucket.items()):
            if name and nm != name:
                continue
            if isinstance(rec, dict) and rec.pop("learn", None) is not None:
                n += 1
    if n:
        save(db)
    return {"cleared": n}


def _last_frame(gui, img=None):
    """verify 需要整图来做"位置容差搜索"：优先用调用方传入的那一帧，否则取当前帧。"""
    if img is not None:
        return img
    _im = grab_frame(gui)
    return _im


def _match_near(img, pt, ref_hash: str, radius: int = 0):
    """在 `pt` 周围 ±radius 范围内找**最像参考**的那一版裁剪。

    返回 `(距离, (dx,dy), 当前哈希, 均值, 标准差)`；radius=0 时等价于"就按这个点比"。
    ⛔ 这一层只吸收"同一目标的小位移"（实测 ±3px 内同类距离可降到 0），**不是放宽阈值**：
      偏得远（判据里用 10px 验）照样判"不是"。
    """
    best = (64, (0, 0), "", 0.0, 0.0)
    offs = [(0, 0)]
    for r in range(1, max(1, int(radius)) + 1):
        offs += [(r, 0), (-r, 0), (0, r), (0, -r), (r, r), (-r, -r), (r, -r), (-r, r)]
    for dx, dy in offs:
        crop, _c = _crop(img, (pt[0] + dx, pt[1] + dy))
        if crop is None:
            continue
        mean, std = _stats(crop)
        d = dist(dhash(crop), ref_hash)
        if d < best[0] or (d == best[0] and (dx, dy) == (0, 0)):
            best = (d, (dx, dy), dhash(crop), mean, std)
    return best


def verify_many(gui, names, max_dist: int = None, tol_px: int = None, pts: dict = None) -> dict:
    """**一次抓图、批量校验**（同一个流程里要校验多个目标时用这个）。

    为什么要它：`verify()` 每个目标都会抓一次**整窗**（PrintWindow/抓屏是这条链路上最贵的一步）
    ⇒ N 个目标 = N 次整窗截图。这里只抓一次并共享给所有目标。
    """
    img = grab_frame(gui)
    out = {}
    for n in list(names or []):
        out[n] = verify(gui, n, max_dist=(MAX_DIST if max_dist is None else max_dist),
                        tol_px=(TOL_PX if tol_px is None else tol_px), img=img, pts=pts)
    return out


def verify(gui, name: str, max_dist: int = MAX_DIST, tol_px: int = None, img=None,
           pts: dict = None) -> tuple:
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
    cur = capture(gui, name, pts=pts, img=img)   # 调用方知道坐标时直接给，省一次坐标推算
    if not cur.get("ok"):
        # 抓不到画面 ⇒ 自检不可用，**不冒充"对不上"**（也不当"对得上"）
        return None, {"reason": "判据不可用：%s" % cur.get("why"), "dist": None,
                      "have": len(bucket), "key": k}
    _rad = TOL_PX if tol_px is None else max(0, int(tol_px))
    _pt = cur.get("pt")
    _learn = learned_shift(rec)
    if _learn != (0, 0) and _pt:
        # ⭐ 学到的系统性偏移：把"从哪儿开始找"挪到校正后的位置
        #    （因此搜索半径可以缩小 —— 校正量已经在搜索窗口里了，免得重复扫一遍）
        _pt = [_pt[0] + _learn[0], _pt[1] + _learn[1]]
        _rad = max(1, int(_rad) - max(abs(_learn[0]), abs(_learn[1])))
    if _rad <= 0 or not _pt:
        # ⛔ 两条都要走**老路径**：`_rad<=0`＝调用方明确不要容差；拿不到 pt＝做不了位移搜索。
        #    这时**必须直接比 `capture` 返回的那个哈希**（别自己再去读帧）——
        #    第一版就是在 tol_px=0 时也去重新裁剪比对，把"capture 的返回值"绕过去了，
        #    原有判据（把 capture 打桩成返回指定哈希）当场红。
        d, off = dist(cur.get("hash", ""), rec["hash"]), (0, 0)
    else:
        d, off, _h, _m, _s = _match_near(_last_frame(gui, img), _pt, rec["hash"], _rad)
    if d <= max_dist:
        # ⭐ 学习：只记**通过校验**的样本；写盘节流（别每次点击都落盘）
        try:
            learn_offset(rec, off)
            _now = time.time()
            if _now - float(rec.get("_learn_saved") or 0) > 5.0:
                rec["_learn_saved"] = _now
                # ⛔ 写盘要**重新 load 再改再存**：`verify` 里原来只有 `bucket` 这个局部名，
                #    我第一版写了 `db[k] = bucket` ⇒ `NameError: db` —— 还被下面的 `log.debug`
                #    静默吞掉，表现为"校验全过、学习一个样本都没落盘"（判据 L10 当场抓到）。
                #    ⇒ 这里用重载写法，并把失败日志提到 **warning**（这类错不许静默）。
                _db = load()
                if not isinstance(_db, dict):
                    _db = {}
                _b = _db.get(k)
                if not isinstance(_b, dict):
                    _b = dict(bucket)
                _b[name] = rec
                _db[k] = _b
                save(_db)
        except Exception as e:  # noqa: BLE001
            log.warning("记录偏移样本失败（不影响本次校验，但学习会丢）：%s", e)
        _hint = ("（位置容差内最像的一版在 %+d,%+d px 处）" % (off[0], off[1])
                 if off != (0, 0) else "")
        if _learn != (0, 0):
            _hint += "（已按学到的偏移 %+d,%+d 校正，搜索半径收窄到 %d）" % (
                _learn[0], _learn[1], _rad)
        return True, {"reason": "指纹一致（距离 %d/%d）%s" % (d, max_dist, _hint), "dist": d,
                      "off": list(off), "learn": list(_learn), "have": len(bucket), "key": k}
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
