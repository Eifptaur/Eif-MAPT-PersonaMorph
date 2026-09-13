# -*- coding: utf-8 -*-
"""随机图"在线图源"层（可插拔）：每个图源自己负责"只取安全内容"的硬约束。

用户口径（2026-09-13）："**从 pixiv 等各种图源发，做好插件过滤，反正能过滤的都做好，以免涉黄之类的**"
⇒ 本模块只解决"**从哪拿图**"，并且**每个图源在请求参数里就带上安全约束**（rating:sfw / r18=0 / 只走 SFW 端点），
   拿到图后再交给 `agent/image_filter.py` 做第二、第三道过滤（标签黑名单 / 肤色比 / 视觉模型审核）。
   两道叠加，取的是"**纵深防御**"：**任何一道说不行就不发**。

图源清单（每个都能单独开关，取不到就换下一个，不抛异常）：
  · `pixiv`    —— 经公开代理接口（lolicon setu v2）取 pixiv 作品；**强制 `r18=0`**，并自带标签过滤
  · `konachan` —— 标签强制 `rating:safe`
  · `yande`    —— 标签强制 `rating:safe`
  · `safebooru`—— 站名本身就是全年龄站，仍带 `rating:safe`
  · `waifu`    —— waifu.pics，**只走 `/sfw/` 端点**（该站 SFW/NSFW 是分开的域名路径）
  · `nekos`    —— nekos.best（全年龄）
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

UA = "PersonaMorph/1.0 (+random image; safe-mode)"
DEFAULT_TIMEOUT_MS = 9000

# 图源注册表：name -> {label, kind, 说明}
SOURCES = {
    "pixiv": {"label": "Pixiv（代理接口）", "note": "强制 r18=0；可带标签"},
    "konachan": {"label": "Konachan", "note": "强制 rating:safe"},
    "yande": {"label": "yande.re", "note": "强制 rating:safe"},
    "safebooru": {"label": "Safebooru", "note": "全年龄站"},
    "waifu": {"label": "waifu.pics", "note": "只走 SFW 端点"},
    "nekos": {"label": "nekos.best", "note": "全年龄"},
}
DEFAULT_SOURCES = ["pixiv", "safebooru", "nekos", "konachan", "waifu", "yande"]   # 实测可达性好的排前面


def available() -> list:
    return [k for k in DEFAULT_SOURCES if k in SOURCES]


def _get(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS, tag: str = "") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    if tag:
        req.add_header("X-Tag", tag)          # 给单测/日志用，服务端会忽略
    with urllib.request.urlopen(req, timeout=max(1.0, timeout_ms / 1000.0)) as r:
        return r.read()


def _json(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> object:
    raw = _get(url, timeout_ms)
    return json.loads(raw.decode("utf-8", "ignore"))


def _clean_tags(tags) -> list:
    if isinstance(tags, str):
        tags = tags.replace("\n", " ").split(" ")
    return [str(t).strip() for t in (tags or []) if str(t).strip()]


# ── 各图源：build_url() 只负责"带上安全参数"，parse() 负责抽出 url/标签/分级 ──────────
def _pixiv(cfg: dict) -> tuple:
    """lolicon setu v2 代理 pixiv；**r18=0 是硬编码**（配置里没有打开 r18 的入口）。"""
    tag = str((cfg.get("tag") or "")).strip()
    q = {"r18": "0", "num": "1", "size": "regular", "excludeAI": "false"}
    if tag:
        q["tag"] = tag
    url = "https://api.lolicon.app/setu/v2?" + urllib.parse.urlencode(q)

    def parse(js):
        data = (js or {}).get("data") or []
        if not data:
            return None
        it = data[0]
        urls = it.get("urls") or {}
        link = urls.get("regular") or urls.get("original") or urls.get("small")
        if not link:
            return None
        return {"url": link, "tags": _clean_tags(it.get("tags")),
                "rating": "explicit" if it.get("r18") else "safe",
                "page": "https://www.pixiv.net/artworks/%s" % it.get("pid"),
                "title": it.get("title") or ""}
    return url, parse


def _booru(host: str, extra: str = "") -> tuple:
    tags = "rating:safe order:random" + ((" " + extra) if extra else "")
    url = "%s/post.json?tags=%s&limit=1" % (host, urllib.parse.quote(tags))

    def parse(js):
        if not isinstance(js, list) or not js:
            return None
        it = js[0]
        link = it.get("file_url") or it.get("sample_url") or it.get("jpeg_url")
        if not link:
            return None
        r = str(it.get("rating") or "s").lower()
        rating = {"s": "safe", "q": "questionable", "e": "explicit"}.get(r, "questionable")
        return {"url": link, "tags": _clean_tags(it.get("tags")), "rating": rating,
                "page": "%s/post/show/%s" % (host, it.get("id"))}
    return url, parse


def _safebooru(cfg: dict) -> tuple:
    url = ("https://safebooru.org/index.php?page=dapi&s=post&q=index&json=1"
           "&tags=%s&limit=1" % urllib.parse.quote("rating:safe"))

    def parse(js):
        if not isinstance(js, list) or not js:
            return None
        it = js[0]
        link = it.get("file_url") or ""
        if link and link.startswith("//"):
            link = "https:" + link
        if not link and it.get("directory") and it.get("image"):
            link = "https://safebooru.org/images/%s/%s" % (it["directory"], it["image"])
        if not link:
            return None
        return {"url": link, "tags": _clean_tags(it.get("tags")), "rating": "safe",
                "page": "https://safebooru.org/index.php?page=post&s=view&id=%s" % it.get("id")}
    return url, parse


def _waifu(cfg: dict) -> tuple:
    cat = str(cfg.get("category") or "waifu").strip().lower()
    if cat not in ("waifu", "neko", "shinobu", "megumin", "awoo", "smug", "wave", "blush", "dance", "happy", "kiss"):
        cat = "waifu"
    url = "https://api.waifu.pics/sfw/%s" % cat          # **只走 /sfw/**，这是该站的硬边界

    def parse(js):
        link = (js or {}).get("url")
        if not link:
            return None
        return {"url": link, "tags": ["sfw"], "rating": "safe", "page": "https://waifu.pics/"}
    return url, parse


def _nekos(cfg: dict) -> tuple:
    cat = str(cfg.get("category") or "neko").strip().lower()
    if cat not in ("neko", "waifu", "husbando", "kitsune"):
        cat = "neko"
    url = "https://nekos.best/api/v2/%s" % cat

    def parse(js):
        res = (js or {}).get("results") or []
        if not res:
            return None
        it = res[0]
        link = it.get("url")
        if not link:
            return None
        return {"url": link, "tags": [it.get("artist_name") or "neko"], "rating": "safe",
                "page": it.get("source_url") or "https://nekos.best/"}
    return url, parse


_BUILDERS = {
    "pixiv": _pixiv,
    "konachan": lambda cfg: _booru("https://konachan.net"),
    "yande": lambda cfg: _booru("https://yande.re"),
    "safebooru": _safebooru,
    "waifu": _waifu,
    "nekos": _nekos,
}


def fetch_meta(source: str, cfg: dict = None) -> tuple:
    """向某图源要一张图的**元数据**（不下载图片本身）：返回 (meta, 错误说明)。"""
    cfg = cfg or {}
    src = str(source or "").strip().lower()
    if src not in _BUILDERS:
        return None, "未知图源「%s」（可用：%s）" % (source, "、".join(available()))
    try:
        url, parse = _BUILDERS[src](cfg)
    except Exception as e:
        return None, "图源 %s 构造请求失败：%s" % (src, e)
    try:
        js = _json(url, int(cfg.get("timeout_ms") or DEFAULT_TIMEOUT_MS))
    except Exception as e:
        return None, "图源 %s 请求失败：%s: %s" % (src, type(e).__name__, str(e)[:110])
    try:
        meta = parse(js)
    except Exception as e:
        return None, "图源 %s 返回格式不认识：%s" % (src, e)
    if not meta:
        return None, "图源 %s 这次没返回可用作品（可能被限流或没有匹配的标签）" % src
    meta["source"] = src
    return meta, ""


def download(url: str, dest_dir: str, max_mb: float = 8.0, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> tuple:
    """下载到目标目录并校验是真图片：返回 (路径, 错误说明)。**成功时错误说明为空串**（与 fetch_meta 一致）。"""
    try:
        data = _get(url, timeout_ms)
        if len(data) > float(max_mb) * 1024 * 1024:
            return None, "图片超过 %.0fMB 上限，已放弃" % max_mb
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            ext = ".jpg"
        os.makedirs(dest_dir, exist_ok=True)
        p = os.path.join(dest_dir, "src_%s_%d%s" % (time.strftime("%H%M%S"), int(time.time() * 1000) % 1000, ext))
        with open(p, "wb") as fh:
            fh.write(data)
        try:
            from PIL import Image
            im = Image.open(p)
            im.verify()
        except Exception:
            os.remove(p)
            return None, "下载到的内容不是有效图片"
        return p, ""                           # 成功：第二项留空（调用方按"非空＝失败"判断）
    except Exception as e:
        return None, "下载失败：%s: %s" % (type(e).__name__, str(e)[:110])
