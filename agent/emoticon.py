# -*- coding: utf-8 -*-
"""表情包**离线解密**（算法来自开源项目 CN-Grace/Wechat-Emoticon-Parser 的
`v4.0-plus` 分支 README_CN.md §1 与脚本 `derive_key/verify_key`，MIT 许可）。

**为什么值得**：微信 4.x 的表情在库里是加密数据、驱动库只认 3/34/43/49 ⇒ 47 号"动画表情"一直下不来，
之前只能用"截最新一条消息的图"兜（还必须是最新那条）。现在能**从 md5 直接拿到原文件**：

    1. 消息行 → content 里的 `<emoji md5="…">` → md5
    2. 按 md5 找文件：`business/emoticon/Persist|Thumb|PersistStore/<前2位>/<md5>`
       或 `cache/<月>/Emoticon/<前2位>/<md5>`
    3. 解密：**AES-128-CBC，key = IV = md5(f"{seed}{wxid}EMOTICON") 的十六进制解码前 16 字节**，PKCS7
    4. 明文首字节即魔数：`GIF8` / `\\x89PNG` / `\\xff\\xd8\\xff`（直出）或 `wxgf`（微信自研 HEVC 动图，
       交给 ffmpeg 可解出**全部帧** ⇒ 转成真动图 GIF；给视觉接口看的静态图仍走首帧）
    `seed`＝微信进程内存里的账号级常量（8~12 位十进制）；`wxid`＝数据目录名去掉尾部 `_xxxx`。

⚠️ 动图这件事的两条口径（真机实测：本机 140 个表情文件里 **100 个是 `wxgf`**，
   收藏目录 `Persist` 33 个**全是 wxgf** ⇒ "只拿到首帧"是这条链的常态，不是个例）：
   · `to_viewable` ⇒ **首帧静态图**（视觉接口多数不吃 GIF）；
   · `to_animated` ⇒ **真动图**（`wxgf` → GIF，实测 556x540/25fps 的源转出 30 帧）；
   `sticker_image(animated=True)` 走后者，收藏夹用它。

⚠️ 边界与纪律：①全程**只读**（读进程内存取 seed、读表情文件、解密），不写微信任何东西；
②seed/key **落盘缓存**（`data/emoticon_key.json`）并用本地任一表情文件**首块校验**再复用，
   校验不过才重扫（微信重登/换号会让它变）；
③拿不到 key 或解不出 ⇒ 返回 None，**由调用方退回截图路线**，绝不猜、绝不编。
"""
from __future__ import annotations

import glob
import hashlib
import io
import json
import logging
import os
import re
import time
from . import persist # noqa: E402 原子换档：撞共享冲突要退避重试（见 persist.replace_into）

log = logging.getLogger("persona-morph")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAGICS = (b"GIF8", b"\x89PNG", b"\xff\xd8\xff", b"wxgf")
_RE_MD5 = re.compile(r'md5\s*=\s*"([0-9a-fA-F]{32})"')
_NO_WINDOW = getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0) # ⛔ 不许闪控制台窗
_RE_SEED = re.compile(rb"(?<![0-9])(\d{8,12})(?![0-9])")


# ── 账号 ────────────────────────────────────────────────────────────────────
_AUTO_DB = {"db": None, "tried": False}


def _auto_db():
    """没传 db 时自己弄一个（库会自动定位当前登录账号的数据目录）。**懒建 + 缓存**。"""
    if _AUTO_DB["db"] is not None:
        return _AUTO_DB["db"]
    if _AUTO_DB["tried"]:
        return None
    _AUTO_DB["tried"] = True
    try:
        from wechatauto import WeChatDB
        _AUTO_DB["db"] = WeChatDB()
    except Exception as e: # noqa: BLE001
        log.debug("自建 WeChatDB 失败（表情解密不可用）：%s", e)
        _AUTO_DB["db"] = None
    return _AUTO_DB["db"]


def account_dir(db=None) -> str:
    try:
        return str(getattr(db if db is not None else _auto_db(), "account_dir", "") or "")
    except Exception:
        return ""


def account_wxid(db=None) -> str:
    """当前登录账号的 wxid（数据目录名去掉尾部 `_xxxx`）。"""
    base = os.path.basename(account_dir(db).rstrip("\\/"))
    if not base:
        return ""
    return base.rsplit("_", 1)[0] if "_" in base else base


# ── 派生与解密 ──────────────────────────────────────────────────────────────
def derive_key(seed, wxid: str) -> bytes:
    """`AES-128` 的 key：`md5(f"{seed}{wxid}EMOTICON")` 的十六进制解码后取前 16 字节。"""
    return bytes.fromhex(hashlib.md5(("%s%sEMOTICON" % (seed, wxid)).encode()).hexdigest())[:16]


def decrypt_bytes(key: bytes, data: bytes) -> bytes:
    """AES-128-CBC（key＝IV）解到最后一个完整块，并去 PKCS7 填充。"""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except Exception as e: # noqa: BLE001
        raise RuntimeError("没有 cryptography，解不了表情：%s" % e)
    n = (len(data) // 16) * 16
    if n <= 0:
        return b""
    d = Cipher(algorithms.AES(key), modes.CBC(key)).decryptor()
    out = d.update(data[:n]) + d.finalize()
    pad = out[-1] if out else 0
    if 1 <= pad <= 16 and all(b == pad for b in out[-pad:]):
        out = out[:-pad]
    return out


def sniff(data: bytes) -> str:
    for m in MAGICS:
        if data.startswith(m):
            return {b"GIF8": "gif", b"\x89PNG": "png", b"\xff\xd8\xff": "jpg", b"wxgf": "wxgf"}[m]
    return ""


def verify_key(key: bytes, sample_path: str = "") -> bool:
    """用本地任一表情文件的**首块**校验这把 key（首块命中魔数即成）。整函数只读。"""
    paths = [sample_path] if sample_path else []
    if not paths:
        paths = any_sticker_files(limit=3)
    for p in paths:
        try:
            with open(p, "rb") as fh:
                c0 = fh.read(16)
            if len(c0) == 16 and sniff(decrypt_bytes(key, c0)):
                return True
        except Exception:
            continue
    return False


# ── 找文件（md5 → 路径）────────────────────────────────────────────────────
def any_sticker_files(limit: int = 5, db=None) -> list:
    """本地随便几个表情文件（只用来校验 key / 做样本）。"""
    out = []
    for d in _sticker_dirs(db=db):
        for p in glob.glob(os.path.join(d, "*", "*")):
            if os.path.isfile(p) and os.path.getsize(p) >= 32:
                out.append(p)
                if len(out) >= limit:
                    return out
    return out


def _sticker_dirs(acct: str = "", db=None) -> list:
    acct = acct or account_dir(db)
    if not acct:
        return []
    dirs = [os.path.join(acct, "business", "emoticon", sub)
            for sub in ("Persist", "Thumb", "PersistStore", "ThumbStore")]
    # ⛔ 别漏**缓存目录**：**刚收到的**表情往往落在 `cache/<月份>/Emoticon/<p2>/<md5>`
    #   （`find_sticker_file` 的第二条路就是它）。原来只列 `business/emoticon` ⇒
    #   "按 mtime 找最新的那个"只会找到旧的（真机实测：群里刚发完表情，最新文件却是 09-18 的）。
    dirs += sorted(glob.glob(os.path.join(acct, "cache", "*", "Emoticon")), reverse=True)
    return dirs


def find_sticker_file(md5: str, acct: str = "", db=None) -> str:
    """按 md5 找表情文件。**先收藏目录，再按月缓存目录**（后者按月份倒序）。"""
    md5 = str(md5 or "").lower().strip()
    if len(md5) != 32:
        return ""
    p2 = md5[:2]
    for base in _sticker_dirs(acct, db):
        for name in (md5, md5 + ".thumb"):
            p = os.path.join(base, p2, name)
            if os.path.isfile(p):
                return p
    acct = acct or account_dir(db)
    if acct:
        cands = glob.glob(os.path.join(acct, "cache", "*", "Emoticon", p2, md5))
        if not cands:
            cands = glob.glob(os.path.join(acct, "cache", "*", "Emoticon", p2, md5 + ".thumb"))
        if cands:
            cands.sort(reverse=True) # 月份倒序 ⇒ 取最近的
            return cands[0]
    return ""


def md5_of_message(db, chat_id: str, local_id) -> str:
    """从消息行里取表情 md5（行里的 content 是 zstd 压缩的 XML，走 `replica_adapter` 正文还原）。"""
    try:
        from . import replica_adapter as ra
        xml = ra.message_text(db, chat_id, local_id)
        m = _RE_MD5.search(str(xml or ""))
        return m.group(1).lower() if m else ""
    except Exception as e: # noqa: BLE001
        log.debug("取表情 md5 失败：%s", e)
        return ""


# ── seed / key（内存扫描 + 落盘缓存）───────────────────────────────────────
def _key_file() -> str:
    return os.path.join(ROOT, "data", "emoticon_key.json")


def load_cached_key(wxid: str) -> bytes:
    try:
        with open(_key_file(), encoding="utf-8") as fh:
            d = json.load(fh) or {}
        if str(d.get("wxid") or "") != str(wxid or ""):
            return b""
        return bytes.fromhex(str(d.get("key") or ""))
    except Exception:
        return b""


def save_cached_key(wxid: str, seed, key: bytes) -> None:
    try:
        p = _key_file()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"wxid": str(wxid or ""), "seed": str(seed), "key": key.hex(),
                       "at": int(time.time())}, fh, ensure_ascii=False)
        persist.replace_into(tmp, p)
    except Exception as e: # noqa: BLE001
        log.debug("表情 key 落盘失败（本次进程内仍生效）：%s", e)


def _weixin_pids() -> list:
    try:
        import subprocess
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, errors="replace",
                             creationflags=_NO_WINDOW).stdout or ""
        return [int(m) for m in re.findall(r'"[^"]+","(\d+)"', out) if int(m) > 100]
    except Exception:
        return []


def _scan_memory(wxid: str, sample: str, budget_s: float = 45.0, fast: bool = True):
    """扫 Weixin.exe 内存取 seed → 派生 → 用样本文件首块验证。

    `fast=True` 先试**快路**：只扫"出现 wxid 字符串的页"并在其附近找 8~12 位数字
    （README 说 key/seed 就在 wxid 旁边的会话 key 表里）——命中就秒回；不命中再全量扫。
    """
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    READABLE = (0x02, 0x04, 0x20, 0x40, 0x80)

    class MBI(ctypes.Structure):
        _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                    ("AllocationProtect", wintypes.DWORD), ("__a1", wintypes.DWORD),
                    ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
                    ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD), ("__a2", wintypes.DWORD)]

    needle = str(wxid).encode()
    t0 = time.time()
    for pid in _weixin_pids()[:3]:
        h = k32.OpenProcess(0x0400 | 0x0010, False, pid)
        if not h:
            continue
        mbi = MBI()
        addr = 0
        while k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            base, size, prot = mbi.BaseAddress or 0, mbi.RegionSize or 0, mbi.Protect
            if mbi.State == 0x1000 and prot in READABLE and not (prot & 0x100):
                off = 0
                while off < size:
                    want = min(1 << 20, size - off)
                    buf = ctypes.create_string_buffer(want)
                    n = ctypes.c_size_t(0)
                    if k32.ReadProcessMemory(h, ctypes.c_void_p(base + off), buf, want,
                                             ctypes.byref(n)):
                        chunk = buf.raw[:n.value]
                        # 快路：只在这一页里出现 wxid 时才收数字（数字取自整页 + 前后各一页跨度）
                        if (not fast) or (needle and needle in chunk):
                            for m in _RE_SEED.finditer(chunk):
                                seed = m.group(1).decode()
                                key = derive_key(seed, wxid)
                                if verify_key(key, sample):
                                    k32.CloseHandle(h)
                                    return seed, key
                    off += 1 << 20
            addr = base + size
            if not addr or time.time() - t0 > budget_s:
                break
        k32.CloseHandle(h)
        if time.time() - t0 > budget_s:
            break
    return "", b""


def get_key(db=None, sample: str = "", allow_scan: bool = True) -> bytes:
    """拿表情解密用的 key：先用缓存（并**首块校验**），不行再扫内存。拿不到返回 `b""`。"""
    wxid = account_wxid(db)
    if not wxid:
        return b""
    sample = sample or (any_sticker_files(limit=1) or [""])[0]
    key = load_cached_key(wxid)
    if key and verify_key(key, sample):
        return key
    if not allow_scan:
        return b""
    log.info("表情 key：缓存不可用 ⇒ 扫微信进程内存取 seed（首次约 1~20 秒，之后走缓存）")
    seed, key = _scan_memory(wxid, sample, fast=True)
    if not key:
        seed, key = _scan_memory(wxid, sample, fast=False)
    if key:
        save_cached_key(wxid, seed, key)
        log.info("表情 key 已拿到并落盘（seed=%s）", seed)
    else:
        log.warning("表情 key 没扫到（微信没在跑？换了账号？）—— 表情改走截图路线")
    return key


# ── 明文 → 视觉能看的图 ────────────────────────────────────────────────────
def to_viewable(plain: bytes, out_dir: str, stem: str) -> str:
    """把解密后的明文存成"视觉模型能看的图"：GIF/PNG/JPG 直出，`wxgf` 取首帧，动图取首帧。"""
    kind = sniff(plain)
    os.makedirs(out_dir, exist_ok=True)
    if kind in ("png", "jpg"):
        p = os.path.join(out_dir, "%s.%s" % (stem, kind))
        with open(p, "wb") as fh:
            fh.write(plain)
        return p
    if kind == "gif":
        p = os.path.join(out_dir, "%s.gif" % stem)
        with open(p, "wb") as fh:
            fh.write(plain)
        try: # 动图取首帧（多数视觉接口不吃 GIF）
            from PIL import Image
            im = Image.open(io.BytesIO(plain))
            if getattr(im, "is_animated", False):
                png = os.path.join(out_dir, "%s_first.png" % stem)
                im.seek(0)
                im.convert("RGBA").save(png)
                return png
        except Exception:
            pass
        return p
    # wxgf：微信自研 HEVC 动图 ⇒ 提 HEVC 裸流取首帧
    hevc = plain
    i = plain.find(b"\x00\x00\x00\x01")
    if i >= 0:
        hevc = plain[i:]
    for maker in _wxgf_first_frame_makers():
        try:
            b = maker(hevc)
            if b:
                p = os.path.join(out_dir, "%s.jpg" % stem)
                with open(p, "wb") as fh:
                    fh.write(b)
                return p
        except Exception:
            continue
    return ""


def _wxgf_first_frame_makers() -> list:
    """两种转码器：驱动库自带的 `_wxgf_to_jpg`，以及 ffmpeg（imageio-ffmpeg / PATH 里的）。"""
    out = []

    def _lib(data):
        try:
            from wechatauto import WeChatDB, MediaDownloader
            md = MediaDownloader(WeChatDB())
            return md._wxgf_to_jpg(data)
        except Exception:
            return None

    def _ffmpeg(data):
        import os as _os
        import subprocess
        import tempfile
        exe = ffmpeg_exe()
        if not exe:
            return None
        with tempfile.TemporaryDirectory() as td:
            src = _os.path.join(td, "a.hevc")
            dst = _os.path.join(td, "a.jpg")
            with open(src, "wb") as fh:
                fh.write(data)
            r = subprocess.run([exe, "-y", "-loglevel", "error", "-f", "hevc", "-i", src,
                                "-frames:v", "1", dst], capture_output=True,
                               creationflags=_NO_WINDOW)
            if r.returncode == 0 and _os.path.exists(dst):
                with open(dst, "rb") as fh:
                    return fh.read()
        return None

    out.append(_lib)
    out.append(_ffmpeg)
    return out


# ── 动图：**保留动效**的那一条（收藏夹/发送用）──────────────────────────────
def ffmpeg_exe() -> str:
    """ffmpeg 可执行文件：PATH 优先，其次产品运行时自带的 `imageio-ffmpeg`。都没有给 `''`。

    为什么够用：产品自带的那个（实测 7.1）**带 libx265**，HEVC 编解码都能干
    ⇒ 不必麻烦用户另外装 ffmpeg。
    """
    import shutil
    try:
        got = shutil.which("ffmpeg")
    except Exception:
        got = None
    if got:
        return str(got)
    try:
        import imageio_ffmpeg
        return str(imageio_ffmpeg.get_ffmpeg_exe() or "")
    except Exception as e: # noqa: BLE001
        # 记录后给空串：调用方据此退回"静态首帧"，不是静默失败
        log.debug("没有可用的 ffmpeg（PATH 与产品自带的都取不到）：%s", e)
        return ""


def wxgf_stream(plain: bytes) -> bytes:
    """`wxgf` 容器里那段 HEVC 裸流（annex-b：从第一个起始码 `00 00 00 01` 起）。"""
    i = plain.find(b"\x00\x00\x00\x01")
    return plain[i:] if i > 0 else plain


def wxgf_to_gif(plain: bytes, out_path: str, fps: int = 15, max_px: int = 240) -> str:
    """`wxgf`（微信自研 HEVC 动图）→ **真动图 GIF**；任一环不成立返回 `''`。

    为什么做得到：`wxgf` 里装的是一段**标准 HEVC**（真机实测 556x540 / 25fps），
    交给 ffmpeg 解码就能拿到**全部帧**；过去只取首帧是"给视觉接口看"的口径，
    并不是这条链的上限。fps 收到 15 只是压体积（表情本身多为 10~25fps），
    两遍调色板（`palettegen` + `paletteuse`）避免 GIF 256 色抖动。
    """
    exe = ffmpeg_exe()
    if not exe or not plain:
        return ""
    import subprocess
    import tempfile
    try:
        with tempfile.TemporaryDirectory(prefix="wxgf-") as td:
            src = os.path.join(td, "a.hevc")
            with open(src, "wb") as fh:
                fh.write(wxgf_stream(plain))
            vf = ("fps=%d,scale=min(%d\\,iw):-2:flags=lanczos,split[a][b];"
                  "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer"
                  % (int(fps), int(max_px)))
            r = subprocess.run([exe, "-y", "-hide_banner", "-loglevel", "error",
                                "-f", "hevc", "-i", src, "-vf", vf, "-loop", "0", out_path],
                               capture_output=True, creationflags=_NO_WINDOW, timeout=45)
            if r.returncode != 0 or not os.path.exists(out_path):
                log.debug("wxgf 转动图没成（rc=%s）：%s", r.returncode,
                          (r.stderr or b"").decode("utf-8", "replace")[:160])
                return ""
        return out_path if os.path.getsize(out_path) > 0 else ""
    except Exception as e: # noqa: BLE001
        log.debug("wxgf 转动图异常：%s", e)
        return ""


def is_animated_file(path: str) -> bool:
    """这个文件**真的会动**吗（容器帧数 > 1）。读不动一律 False（不倒过来猜）。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return bool(getattr(im, "is_animated", False)) and int(getattr(im, "n_frames", 1)) > 1
    except Exception as e: # noqa: BLE001
        log.debug("判动图失败（%s）：%s", os.path.basename(str(path or "")), e)
        return False


def to_animated(plain: bytes, out_dir: str, stem: str) -> str:
    """解密后的明文 → **尽量保留动效**的本地文件（收藏夹/发送用）。认不出给 `''`。

    与 `to_viewable` 的分工写清楚：那一条服务"视觉接口能看的图"（动图只给首帧，
    多数视觉接口不吃 GIF）；这一条服务"收藏夹里存的是不是那个会动的表情"。
    """
    kind = sniff(plain)
    if not kind:
        return ""
    os.makedirs(out_dir, exist_ok=True)
    if kind in ("gif", "png", "jpg"):
        p = os.path.join(out_dir, "%s.%s" % (stem, kind))
        with open(p, "wb") as fh:
            fh.write(plain)
        return p
    # wxgf：先试转真动图；转不成才退回静态首帧（诚实降级，不假装它是动的）
    p = os.path.join(out_dir, "%s.gif" % stem)
    got = wxgf_to_gif(plain, p)
    if got:
        return got
    log.info("表情是微信自研动图，但转动图这一环没成 ⇒ 退回静态首帧")
    return to_viewable(plain, out_dir, stem)


# ── 对外的两个入口 ─────────────────────────────────────────────────────────
def sticker_image(db, chat_id: str, local_id, out_dir: str = "", allow_scan: bool = True,
                  animated: bool = False) -> str:
    """一条「动画表情」消息 → 本地图片路径；任何一环不成立都返回 `''`（调用方退回截图）。

    两条口径按用途分（这是本函数唯一的开关）：
      · `animated=False`（默认）→ `to_viewable`：**首帧静态图**，给视觉接口看；
      · `animated=True` → `to_animated`：**尽量保住动效**（wxgf 转真动图 GIF），收藏夹用。
    """
    md5 = md5_of_message(db, chat_id, local_id)
    if not md5:
        return ""
    path = find_sticker_file(md5, db=db)
    if not path:
        return ""
    key = get_key(db=db, sample=path, allow_scan=allow_scan)
    if not key:
        return ""
    try:
        with open(path, "rb") as fh:
            plain = decrypt_bytes(key, fh.read())
    except Exception as e: # noqa: BLE001
        log.debug("表情解密失败（%s）：%s", os.path.basename(path), e)
        return ""
    if not sniff(plain):
        return ""
    # ⛔ 落点必须是**前端「表情包收藏夹」读的那个目录**（`wechat.EMOJI_DIR` = `<ROOT>/data/emojis`）。
    #   原来落在 `media/emoji` ⇒ 解出来的表情**根本不出现在控制台收藏夹里**
    #   （真机实测：`data/emojis` 空、`media/emoji` 有 6 张 ⇒ 前端一张都看不到）。
    out_dir = out_dir or os.path.join(ROOT, "data", "emojis")
    if animated:
        return to_animated(plain, out_dir, md5)
    return to_viewable(plain, out_dir, md5)
