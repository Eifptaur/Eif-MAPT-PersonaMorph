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
import random
import re
import time
import urllib.parse
import urllib.request
import uuid

from .safe_fetch import FetchError, validate_url

UA = "PersonaMorph/1.0 (+random image; safe-mode)"
DEFAULT_TIMEOUT_MS = 9000

# 图源注册表：name -> {label, kind, 说明}
SOURCES = {
    # ── 国内源（2026-09-18 加，用户口径：「国内的相对来说通路会比较好的吧，也不容易被 ban」）──
    #    特点：**认中文关键词**（不用翻译）、国内直连快；代价＝返回的是网页图，没有标签元数据，
    #    所以"按标签校验"这一步对它们不适用（安全性靠后面的过滤链＋视觉审核兜底）。
    #    baidu/sogou 实测会被判爬虫（要 Cookie）⇒ 没放进默认列表，只留 360 与必应。
    "so360": {"label": "360图片", "note": "国内直连，认中文关键词"},
    "bing": {"label": "必应图片", "note": "国内可直连，认中英文关键词"},
    # ── 国际源 ──
    "wallhaven": {"label": "Wallhaven", "note": "壁纸站，认英文关键词，强制 purity=100(sfw)"},
    "danbooru": {"label": "Danbooru", "note": "强制 rating:general，认英文标签"},
    "pixiv": {"label": "Pixiv（代理接口）", "note": "强制 r18=0；可带标签"},
    "konachan": {"label": "Konachan", "note": "强制 rating:safe"},
    "yande": {"label": "yande.re", "note": "强制 rating:safe"},
    "safebooru": {"label": "Safebooru", "note": "全年龄站"},
    "waifu": {"label": "waifu.pics", "note": "只走 SFW 端点"},
    "nekos": {"label": "nekos.best", "note": "全年龄"},
}
# 顺序＝"先试谁"。国内源排前面：直连快、不容易被墙，而且认中文关键词（国际源夜里经常 403/超时）。
DEFAULT_SOURCES = ["so360", "bing", "wallhaven", "danbooru",
                   "pixiv", "safebooru", "nekos", "konachan", "waifu", "yande"]


def available() -> list:
    return [k for k in DEFAULT_SOURCES if k in SOURCES]


def allow_private_hosts() -> bool:
    """图源是否允许指向内网/环回主机（`config.security.allow_private_image_hosts`，默认关）。

    只有自建图库服务（例如跑在本机的 http 图源）才需要开；开了就等于放开这一路的 SSRF 防护，
    所以**故意不做控制台开关**，要放开请手改 `config.json`。
    """
    try:
        from .config import get_config
        return bool((get_config().get("security") or {}).get("allow_private_image_hosts"))
    except Exception:
        return False


def _open(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS, tag: str = "", headers: dict = None):
    """开一个带 SSRF 闸门的连接（返回 response，调用方负责按**总时长**读）。

    ⚠️ 2026-09-17 实测教训：原来 `_get()` 里是 `r.read()` 一把梭。`urlopen(timeout=)` 只作用于
    **单次 recv**，慢速代理只要不断涓流就永远不超时 —— 实测一张 4.26MB 的图在 `i.pixiv.re`
    上拖了 **160 秒**（发张图和生成一张图一样久）。所以读取必须由上层按墙钟切块。
    `headers`：个别图源要 `Referer` 才不触发反爬（2026-09-18 加）。"""
    try:
        validate_url(url, allow_private=allow_private_hosts())
    except FetchError as e:
        raise RuntimeError("图源地址被安全策略拒绝：%s（确需内网图源请开 security.allow_private_image_hosts）" % e)
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update({str(k): str(v) for k, v in headers.items()})
    req = urllib.request.Request(url, headers=h)
    if tag:
        req.add_header("X-Tag", tag)          # 给单测/日志用，服务端会忽略
    return urllib.request.urlopen(req, timeout=max(1.0, timeout_ms / 1000.0))


def _read_all(r, budget_s: float, max_bytes: int = 0, t0: float = None) -> bytes:
    """按**墙钟预算**分块读完（超时/超限立刻放弃）。"""
    t0 = time.monotonic() if t0 is None else t0
    chunks, got = [], 0
    while True:
        if time.monotonic() - t0 > budget_s:
            raise TimeoutError("总耗时超过 %.1f 秒（慢速连接已放弃）" % budget_s)
        buf = r.read(65536)
        if not buf:
            break
        chunks.append(buf)
        got += len(buf)
        if max_bytes and got > max_bytes:
            raise RuntimeError("内容超过 %.1fMB 上限" % (max_bytes / 1048576.0))
    return b"".join(chunks)


def _get(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS, tag: str = "", max_bytes: int = 0,
         headers: dict = None) -> bytes:
    budget = max(1.0, timeout_ms / 1000.0)
    with _open(url, timeout_ms, tag, headers=headers) as r:
        return _read_all(r, budget, max_bytes)



def _json(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS, headers: dict = None) -> object:
    raw = _get(url, timeout_ms, headers=headers)
    return json.loads(raw.decode("utf-8", "ignore"))


# ── 关键词 → 图源标签（2026-09-18 加）────────────────────────────────────────────
# 🔴 为什么必须加：用户说「来张鲸鱼图片」，机器人调 `send_image_search(keyword="鲸鱼")`，
#   可真发出去的是一张动漫角色图（用户当场发现「跟我要的完全不一样」）。根因＝**关键词根本没进查询**：
#   `_safebooru` 写死 `tags=rating:safe`、`_booru` 只用配置里的固定 tag ⇒ 所谓"要图"实际是
#   "从图源随便抓一张全年龄图"。而且图源的标签体系是**英文**，中文关键词直接丢进去也多半无匹配。
#   ⇒ 两件：①常见中文说法翻成英文标签（下表）；②图源返回的 tags 要**校验含该标签**（见 fetch_meta）。
TAG_ALIAS = {
    "鲸鱼": "whale", "鲸": "whale", "海豚": "dolphin", "鲨鱼": "shark",
    "猫": "cat", "猫咪": "cat", "小猫": "cat", "猫娘": "cat_girl",
    "狗": "dog", "狗狗": "dog", "小狗": "dog", "柴犬": "shiba_inu",
    "兔": "rabbit", "兔子": "rabbit", "狐狸": "fox", "狼": "wolf", "熊猫": "panda",
    "鸟": "bird", "猫头鹰": "owl", "蝴蝶": "butterfly", "龙": "dragon", "恐龙": "dinosaur",
    "花": "flower", "樱花": "sakura", "玫瑰": "rose", "向日葵": "sunflower",
    "树": "tree", "森林": "forest", "草": "grass", "叶子": "leaf",
    "海": "sea", "海边": "beach", "海滩": "beach", "沙滩": "beach", "湖": "lake", "河": "river",
    "水": "water", "瀑布": "waterfall", "天空": "sky", "云": "cloud", "彩虹": "rainbow",
    "星空": "starry_sky", "星星": "star", "月亮": "moon", "太阳": "sun", "夕阳": "sunset",
    "夜": "night", "夜景": "night", "雨": "rain", "雪": "snow", "雾": "fog", "风": "wind",
    "山": "mountain", "雪山": "snow_mountain", "城市": "city", "街道": "street", "桥": "bridge",
    "风景": "scenery", "风景画": "scenery", "自然": "nature", "房间": "room", "教室": "classroom",
    "女孩": "girl", "少女": "girl", "男孩": "boy", "小孩": "child", "人物": "person",
    "和服": "japanese_clothes", "制服": "uniform", "校园": "school", "咖啡": "coffee",
    "书": "book", "音乐": "music", "吉他": "guitar", "钢琴": "piano", "吃": "food",
    "蛋糕": "cake", "甜品": "sweets", "茶": "tea", "酒": "alcohol",
    "水彩": "watercolor", "赛博朋克": "cyberpunk", "机甲": "mecha", "科幻": "sci-fi",
    "像素": "pixel_art", "可爱": "cute", "极简": "minimalism", "静物": "still_life",
}


def tag_candidates(keyword: str) -> list:
    """把请求关键词翻成**图源能用的标签候选**（英译优先、原词兜底，按顺序试）。

    多词（`猫 咖啡` / `cat,coffee`）按分隔符拆开，各自翻译；解析不出英文就保留原词
    （有些源支持日文/中文标签，试一下不亏）。返回空列表＝没给关键词。
    """
    kw = str(keyword or "").strip()
    if not kw:
        return []
    out = []
    for part in re.split(r"[\s,，、;；/]+", kw):
        part = part.strip()
        if not part:
            continue
        en = TAG_ALIAS.get(part)
        if en and en not in out:
            out.append(en)
        if part not in out:
            out.append(part)
    return out


# ── 国内图源（2026-09-18 加）：认中文关键词、国内直连 ─────────────────────────
#    它们返回的是网页图片搜索结果，**没有标签元数据** ⇒ 返回 tags=[]，"按标签校验"自动跳过
#    （安全性交给后面的过滤链 + 视觉审核）。JSON 结构各站会变，所以用**键名白名单递归找图链**，
#    找不到就报"格式不认识"，绝不猜。

_IMG_KEYS = ("middleurl", "picurl", "oripicurl", "objurl", "thumburl", "hoverurl",
             "thumb", "img", "pic", "image", "url")


def _collect_urls(js, prefer=("middleurl", "picurl", "oripicurl", "objurl", "thumburl", "hoverurl")) -> dict:
    """递归收图链：返回 `{优先键: [url...]}`（按键分组，调用方按 prefer 顺序取）。"""
    found = {}

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                kk = str(k).lower()
                if isinstance(v, str) and v.startswith("http") and kk in _IMG_KEYS:
                    found.setdefault(kk, []).append(v)
                elif isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(o, list):
            for it in o:
                walk(it)

    walk(js)
    return found


def _pick_from(found: dict, prefer) -> str:
    """按优先键取一个**随机**图链（同一关键词每次给不同的图，别老发同一张）。"""
    for k in prefer:
        urls = [u for u in (found.get(k) or []) if u]
        if urls:
            return random.choice(urls)
    for k in _IMG_KEYS:                     # 兜底：任何认识键名里的图链
        urls = [u for u in (found.get(k) or []) if u]
        if urls:
            return random.choice(urls)
    return ""


def _baidu(cfg: dict) -> tuple:
    kw = str((cfg or {}).get("tag") or "").strip()
    url = ("https://image.baidu.com/search/acjson?tn=resultjson_com&ipn=rj&nc=1"
           "&word=%s&pn=0&rn=30" % urllib.parse.quote(kw))

    def parse(js):
        link = _pick_from(_collect_urls(js), ("middleurl", "hoverurl", "thumburl"))
        if not link:
            return None
        return {"url": link, "tags": [], "rating": "safe", "page": "https://image.baidu.com/"}
    # 不带 Referer 会被它判成爬虫（实测返回 `{"antiFlag":1,"message":"Forbid spider access"}`）
    return url, parse, {"headers": {"Referer": "https://image.baidu.com/"}}


def _sogou(cfg: dict) -> tuple:
    kw = str((cfg or {}).get("tag") or "").strip()
    url = ("https://pic.sogou.com/napi/pc/searchList?mode=1&start=0&xml_len=30"
           "&query=%s" % urllib.parse.quote(kw))

    def parse(js):
        link = _pick_from(_collect_urls(js), ("oripicurl", "picurl", "thumburl"))
        if not link:
            return None
        return {"url": link, "tags": [], "rating": "safe", "page": "https://pic.sogou.com/"}
    return url, parse, {"headers": {"Referer": "https://pic.sogou.com/"}}


def _so360(cfg: dict) -> tuple:
    kw = str((cfg or {}).get("tag") or "").strip()
    url = ("https://image.so.com/j?q=%s&src=srp&sn=0&pn=30" % urllib.parse.quote(kw))

    def parse(js):
        link = _pick_from(_collect_urls(js), ("img", "pic", "thumb"))
        if not link:
            return None
        return {"url": link, "tags": [], "rating": "safe", "page": "https://image.so.com/"}
    return url, parse


def _bing(cfg: dict) -> tuple:
    """必应图片（国内可直连的 `cn.bing.com`）：返回的是 **HTML**，从 `m` 属性里抠原图地址。

    它一般不做反爬，认中英文关键词都行 —— 是 baidu/sogou 被反爬挡掉之后的国内备选。
    """
    kw = str((cfg or {}).get("tag") or "").strip()
    url = ("https://cn.bing.com/images/async?q=%s&first=0&count=35&mmasync=1"
           % urllib.parse.quote(kw))
    _re_murl = re.compile(r"murl&quot;:&quot;(https?://[^&]+?)&quot;")

    def parse(txt):
        hits = [h.replace("\\/", "/") for h in _re_murl.findall(str(txt or ""))]
        if not hits:
            hits = [h for h in re.findall(r'"murl":"(https?://.+?)"', str(txt or ""))]
        if not hits:
            return None
        return {"url": random.choice(hits), "tags": [], "rating": "safe",
                "page": "https://cn.bing.com/images/search?q=%s" % urllib.parse.quote(kw)}
    return url, parse, {"raw": True, "headers": {"Referer": "https://cn.bing.com/"}}


def _wallhaven(cfg: dict) -> tuple:
    """壁纸站：认英文关键词（猫/风景/鲸鱼这类"实物"比动漫站靠谱得多），强制 SFW。"""
    kw = str((cfg or {}).get("tag") or "").strip()
    url = ("https://wallhaven.cc/api/v1/search?purity=100&categories=111&sorting=relevance"
           "&atleast=1200x800" + (("&q=" + urllib.parse.quote(kw)) if kw else ""))

    def parse(js):
        data = (js or {}).get("data") or []
        if not data:
            return None
        it = random.choice(data)
        link = it.get("path") or ""
        if not link:
            return None
        return {"url": link, "tags": [], "rating": "safe",
                "page": it.get("url") or "https://wallhaven.cc/"}
    return url, parse


def _danbooru(cfg: dict) -> tuple:
    """danbooru：JSON API、无需 key；强制 `rating:general`（全年龄）。"""
    _t = str(((cfg or {}).get("tag") or "")).strip()
    tags = "rating:general" + ((" " + _t) if _t else "")
    url = ("https://danbooru.donmai.us/posts.json?tags=%s&limit=20"
           % urllib.parse.quote(tags))

    def parse(js):
        if not isinstance(js, list) or not js:
            return None
        it = random.choice(js)
        link = (it.get("large_file_url") or it.get("file_url") or it.get("preview_file_url") or "")
        if not link:
            return None
        return {"url": link, "tags": _clean_tags(it.get("tag_string")), "rating": "safe",
                "page": "https://danbooru.donmai.us/posts/%s" % it.get("id")}
    return url, parse


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


def _booru(host: str, cfg: dict = None, extra: str = "") -> tuple:
    # 🔴 2026-09-18：**把请求关键词带进查询**（原来只用配置里的固定 tag ⇒ "要鲸鱼"会拿到随机图）
    _t = str(((cfg or {}).get("tag") or extra or "")).strip()
    tags = "rating:safe order:random" + ((" " + _t) if _t else "")
    url = "%s/post.json?tags=%s&limit=20" % (host, urllib.parse.quote(tags))

    def parse(js):
        if not isinstance(js, list) or not js:
            return None
        # 🔴 2026-09-18：`limit=1` ⇒ 同一标签**永远只拿同一张**（那张若被黑名单/过滤链拦掉，
        #   这个源就永远过不去 —— 实测「鲸鱼」在 safebooru 上就是这种情况）。⇒ 取一批、随机挑一张。
        it = random.choice(js)
        # ⚠️ 2026-09-17：优先取 **sample/预览** 尺寸。原图动辄 3~8MB、下载慢，而聊天里发出去的
        #   还要先压到 ≤1600px（`img_compress.max_px`）⇒ 下原图纯属浪费用户时间。
        link = (it.get("sample_url") or it.get("jpeg_url") or it.get("file_url")
                or it.get("preview_url"))
        if not link:
            return None
        r = str(it.get("rating") or "s").lower()
        rating = {"s": "safe", "q": "questionable", "e": "explicit"}.get(r, "questionable")
        return {"url": link, "tags": _clean_tags(it.get("tags")), "rating": rating,
                "page": "%s/post/show/%s" % (host, it.get("id"))}
    return url, parse


def _safebooru(cfg: dict) -> tuple:
    # 🔴 2026-09-18：同上 —— 原来写死 `tags=rating:safe`，关键词被丢掉
    _t = str(((cfg or {}).get("tag") or "")).strip()
    _tags = "rating:safe" + ((" " + _t) if _t else "")
    url = ("https://safebooru.org/index.php?page=dapi&s=post&q=index&json=1"
           "&tags=%s&limit=20" % urllib.parse.quote(_tags))

    def parse(js):
        if not isinstance(js, list) or not js:
            return None
        # 同上：取一批、随机挑一张（`limit=1` 时同一标签永远同一张，被过滤链拦掉就永远过不去）
        it = random.choice(js)
        # 同 _booru：先取 sample（有就给），没有再拼原图地址
        link = it.get("sample_url") or ""
        if link and link.startswith("//"):
            link = "https:" + link
        if not link and it.get("directory") and it.get("image"):
            link = "https://safebooru.org/images/%s/%s" % (it["directory"], it["image"])
        if not link:
            link = it.get("file_url") or ""
        if link and link.startswith("//"):
            link = "https:" + link
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
    # 国内源（认中文关键词、国内直连）
    # ⚠️ 百度/搜狗**实测会被判爬虫**（`{"antiFlag":1,"message":"Forbid spider access"}` / `{"status":1,"info":"forbid"}`，
    #    加 Referer 也过不去——它们要 Cookie）⇒ 不放进默认列表，免得每次白等；真要接得自己做 Cookie 池。
    "so360": _so360,
    "bing": _bing,
    # 国际源
    "wallhaven": _wallhaven,
    "danbooru": _danbooru,
    "pixiv": _pixiv,
    "konachan": lambda cfg: _booru("https://konachan.net", cfg=cfg),
    "yande": lambda cfg: _booru("https://yande.re", cfg=cfg),
    "safebooru": _safebooru,
    "waifu": _waifu,
    "nekos": _nekos,
}

# 这些源**只按类目出图、不认任意关键词**（waifu.pics / nekos.best 是"随机可爱图"接口）
# ⇒ 有人点名要「鲸鱼」时**不能用它们**（否则就是今晚那幕：给了一张不相干的动漫图）。
TAGLESS_SOURCES = ("waifu", "nekos")


def fetch_meta(source: str, cfg: dict = None) -> tuple:
    """向某图源要一张图的**元数据**（不下载图片本身）：返回 (meta, 错误说明)。

    图源构造器可以返回 `(url, parse)` 或 `(url, parse, opts)`；`opts` 支持：
      · `headers`：额外请求头（个别站要 Referer 才不触发反爬）
      · `raw`：返回的**不是 JSON**（如必应的 HTML 结果页），`parse` 收到的是文本
    """
    cfg = cfg or {}
    src = str(source or "").strip().lower()
    if src not in _BUILDERS:
        return None, "未知图源「%s」（可用：%s）" % (source, "、".join(available()))
    try:
        _built = _BUILDERS[src](cfg)
        url, parse = _built[0], _built[1]
        opts = (_built[2] if len(_built) > 2 else {}) or {}
    except Exception as e:
        return None, "图源 %s 构造请求失败：%s" % (src, e)
    _hdr = opts.get("headers") or None
    _to = int(cfg.get("timeout_ms") or DEFAULT_TIMEOUT_MS)
    try:
        if opts.get("raw"):
            _txt = _get(url, _to, headers=_hdr)
            _payload = _txt.decode("utf-8", "ignore")
        else:
            _payload = _json(url, _to, headers=_hdr)
    except Exception as e:
        return None, "图源 %s 请求失败：%s: %s" % (src, type(e).__name__, str(e)[:110])
    try:
        meta = parse(_payload)
    except Exception as e:
        return None, "图源 %s 返回格式不认识：%s" % (src, e)
    if not meta:
        return None, "图源 %s 这次没返回可用作品（可能被限流或没有匹配的标签）" % src
    # 🔴 2026-09-18 加：**校验返回的图真的带这个标签** —— 否则图源悄悄忽略关键词时，
    #   我们会把一张风马牛不相及的图当"你要的那张"发出去（今晚就是这么翻车的）。
    #   只在"本次带了 tag"且"该源确实返回了 tags"时校验；tags 为空的源（waifu/nekos）不适用。
    _want = str((cfg or {}).get("tag") or "").strip().lower()
    _tags = [str(t).strip().lower() for t in (meta.get("tags") or []) if str(t).strip()]
    if _want and _tags:
        def _norm(s):
            return s.replace(" ", "_").replace("-", "_")
        _w = _norm(_want)
        if not any((_w in _norm(t)) or (_norm(t) in _w) for t in _tags):
            return None, ("图源 %s 返回的图不含标签「%s」（它这次给的 tags=%s）"
                          % (src, _want, "、".join(_tags[:6])))
    meta["source"] = src
    return meta, ""


def download(url: str, dest_dir: str, max_mb: float = 8.0, timeout_ms: int = DEFAULT_TIMEOUT_MS,
             soft_max_mb: float = 0.0) -> tuple:
    """下载到目标目录并校验是真图片：返回 (路径, 错误说明)。**成功时错误说明为空串**（与 fetch_meta 一致）。

    `timeout_ms` 是**整张图的总时长上限**（不是单次 recv 的），边下边查；超时/超限立刻收手并删掉半截文件。
    `soft_max_mb`>0 时：服务器报了 Content-Length 且超过它，就**立刻放弃**（留给并发的其它候选赢），
    这样"要图"不会为了发一张 1600px 的图先下 5MB 原图（实测 5.34MB / 2894×4970 下完还要压）。
    """
    p = ""
    try:
        cap = int(float(max_mb) * 1024 * 1024)
        budget = max(1.0, timeout_ms / 1000.0)
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            ext = ".jpg"
        suffix = ext + ".part"
        os.makedirs(dest_dir, exist_ok=True)
        # ⚠️ 文件名必须唯一：并发赛跑时两个线程可能落在同一毫秒，撞名会 PermissionError（2026-09-17 实测）
        tag = uuid.uuid4().hex[:6]
        p = os.path.join(dest_dir, "src_%s_%s%s" % (time.strftime("%H%M%S"), tag, suffix))
        with _open(url, timeout_ms) as r:
            try:
                clen = int(r.headers.get("Content-Length") or 0)
            except Exception:
                clen = 0
            if soft_max_mb and clen and clen > float(soft_max_mb) * 1024 * 1024:
                raise RuntimeError("图片偏大（%.1fMB 超过软上限 %.1fMB）" % (clen / 1048576.0, soft_max_mb))
            t0 = time.monotonic()
            got = 0
            with open(p, "wb") as fh:
                while True:
                    if time.monotonic() - t0 > budget:
                        raise TimeoutError("总耗时超过 %.1f 秒（慢速连接已放弃）" % budget)
                    buf = r.read(65536)
                    if not buf:
                        break
                    got += len(buf)
                    if got > cap:
                        raise RuntimeError("图片超过 %.0fMB 上限，已放弃" % max_mb)
                    fh.write(buf)
        final = p[:-5]                                   # 去掉 .part
        os.replace(p, final)
        p = final
        try:
            from PIL import Image
            im = Image.open(p)
            im.verify()
        except Exception:
            return None, "下载到的内容不是有效图片"
        return p, ""                           # 成功：第二项留空（调用方按"非空＝失败"判断）
    except Exception as e:
        for q in (p, p[:-5] if p.endswith(".part") else ""):
            if q:
                try:
                    os.remove(q)                 # 半截文件不留
                except OSError:
                    pass
        return None, "下载失败：%s: %s" % (type(e).__name__, str(e)[:110])
