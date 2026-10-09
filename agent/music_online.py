# -*- coding: utf-8 -*-
"""音乐推荐·在线档：**接网易云公开接口（免 key）＋ QQ 音乐官方接口（用户自填 key）**。

## 为什么有这一层（与本地标签库的关系）

2026-10-10 用户当面推翻了"本地热单库"这条思路，原话口径是：
**"本地库放一堆热门歌曲，就相当于是脱裤子放屁"** —— 推荐的价值在"给你一首你
没听过但会喜欢的"，而本地预置的必然是大路货，用户自己都听过。
⇒ 推荐**必须走在线**（曲子来自真实曲库，不是我们编的、也不是我们挑剩下的）。

- **默认档：网易云**（`music.163.com/api/*`，**公开接口、免 key、免登录**）。
- **可选档：QQ 音乐**（`a.y.qq.com`，需用户自己去官网申请 `qmk-` key；填了才启用）。

## 三条硬口径（与项目其它网络模块一致）

1. **只给「歌名 + 歌手 + 理由 + 可点链接」**：⛔ 不下载、不转发、不绕平台版权 ——
   让用户自己点开去听。这与本地档的红线完全一致。
2. **绝不编**：搜不到 / 网络不通 / 接口报错 ⇒ 返回 `ok=False` + 如实说原因，
   **绝不编歌名、绝不拿本地表来假装是在线推荐**。
3. **出网是被选中才发生**：只有模型真调 `recommend_music` 且在线档可用时才发请求；
   发出去的是**用户那句"想要什么样的"**（如「适合下雨天」），不发聊天记录、不发联系人。
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("persona-morph")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 8
#: 一次最多取几首（与提示词口径一致：2~3 首，给多了就是"列歌单"）
MAX_PICKS = 3
#: 在线结果缓存（秒）：同一句话短时间内重复问不多打接口
_CACHE_TTL = 300
_cache: dict = {}

# ── 网易云（免 key）──────────────────────────────────────────────
#: 搜索：type=1 单曲 / type=1000 歌单（实测均可用，无需 key、无需登录）
NE_SEARCH = "https://music.163.com/api/search/get/web"
NE_PLAYLIST = "https://music.163.com/api/playlist/detail"
#: 可点链接（明文数字 id 拼，不用加密 id）
NE_SONG_URL = "https://music.163.com/#/song?id=%s"
NE_PLAYLIST_URL = "https://music.163.com/#/playlist?id=%s"

# ── QQ 音乐（用户自填 key）────────────────────────────────────────
QQ_BASE = "https://a.y.qq.com"
QQ_SEARCH = "/discover/search"
QQ_SONG_URL = "https://i2.y.qq.com/a/song/%s"
QQ_PLAYLIST_URL = "https://i2.y.qq.com/a/playlist/%s"
#: skill 版本号（QQ 网关要求每次请求带上；跟它们官方文档同源）
QQ_SKILL_VERSION = "0.0.3"


def _get_json(url: str, *, headers: dict = None, data: bytes = None, timeout: int = TIMEOUT) -> dict:
    """发一次请求并解析 JSON ⇒ dict；任何一步失败 ⇒ 抛异常（由上层兜底成"如实说"）。"""
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("User-Agent", UA)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 只打固定 https 域名
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).strip().lower()


# ── 网易云：搜索 + 取歌单曲目 ────────────────────────────────────
def _ne_search(query: str, typ: int, limit: int = 10) -> list:
    """网易云搜索 ⇒ 原始结果列表（typ：1=单曲，1000=歌单）。失败抛异常。"""
    qs = urllib.parse.urlencode({"s": query, "type": typ, "limit": limit, "offset": 0})
    d = _get_json(NE_SEARCH + "?" + qs, headers={
        "Referer": "https://music.163.com/",
        "Accept": "application/json",
    })
    result = d.get("result") or {}
    if typ == 1:
        return list(result.get("songs") or [])
    return list(result.get("playlists") or [])


def _ne_playlist_tracks(pid) -> list:
    """取一个歌单里的曲目（用于"推歌单时也从里面挑几首"）。失败 ⇒ 空表。"""
    try:
        d = _get_json(NE_PLAYLIST + "?id=%s" % pid, headers={
            "Referer": "https://music.163.com/", "Accept": "application/json"})
        return list((d.get("result") or {}).get("tracks") or [])
    except Exception as e:  # noqa: BLE001
        log.debug("取歌单曲目失败：%s", e, exc_info=True)
        return []


def _ne_song_row(song: dict) -> dict:
    sid = song.get("id")
    artists = "、".join(str(a.get("name") or "") for a in (song.get("artists") or []) if a.get("name"))
    return {
        "title": str(song.get("name") or "").strip(),
        "artist": artists.strip(),
        "url": (NE_SONG_URL % sid) if sid else "",
        "source": "网易云",
    }


def recommend_netease(query: str, *, want_playlist: bool = True, limit: int = MAX_PICKS) -> dict:
    """网易云免 key 推荐 ⇒ `{ok, picks, note, source}`。

    **给的不保证是热单**：优先用"歌单"当入口（歌单＝别人按场景/心情攒的，
    天然带"发现"属性），从命中的歌单里挑曲目；歌单取不到再退回单曲搜索。
    """
    q = str(query or "").strip()
    if not q:
        # 没给"想要什么样的" ⇒ 不猜（猜出来的必然是热单，正是用户反对的）
        return {"ok": False, "picks": [], "source": "网易云",
                "note": "你得先说想要什么样的（场景/心情/像哪首），我照着找 —— "
                        "⛔ 没方向我不会随便塞几首热歌给你。"}
    cache_key = ("ne", _norm(q))
    hit = _cache.get(cache_key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]
    picks: list = []
    playlist_name = ""
    # ① 先按"歌单"找（歌单是别人攒的，更适合"发现惊喜"）
    if want_playlist:
        try:
            pls = _ne_search(q, 1000, limit=6)
        except Exception as e:  # noqa: BLE001
            pls = []
            log.debug("网易云歌单搜索失败：%s", e, exc_info=True)
        for pl in pls:
            pid = pl.get("id")
            if not pid:
                continue
            tracks = _ne_playlist_tracks(pid)
            if tracks:
                playlist_name = str(pl.get("name") or "").strip()
                picks = [r for r in (_ne_song_row(t) for t in tracks) if r["title"]]
                break
    # ② 歌单取不到 ⇒ 退回单曲搜索
    if not picks:
        try:
            songs = _ne_search(q, 1, limit=max(limit, 8))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "picks": [], "source": "网易云",
                    "note": "连不上网易云（%s）—— 不是我编不出，是这一下没通。" % str(e)[:50]}
        picks = [r for r in (_ne_song_row(s) for s in songs) if r["title"]]
    if not picks:
        return {"ok": False, "picks": [], "source": "网易云",
                "note": "网易云那边没搜到对得上「%s」的曲子 —— ⛔ 我不会编歌名。" % q[:30]}
    out = []
    for r in picks[:max(1, int(limit or MAX_PICKS))]:
        out.append({"title": r["title"], "artist": r["artist"], "url": r["url"],
                    "why": "按你说的「%s」找的" % q[:16], "source": r["source"]})
    note = "来自网易云（公开接口、不下载不转发，点链接你自己去听）。"
    if playlist_name:
        note += "这一批是从歌单「%s」里挑的。" % playlist_name[:24]
    res = {"ok": True, "picks": out, "source": "网易云", "playlist": playlist_name, "note": note}
    _cache[cache_key] = (time.time(), res)
    return res


# ── QQ 音乐（用户自填 key）────────────────────────────────────────
def qqmusic_enabled(key: str) -> bool:
    """填了 key 才启用（key 形如 `qmk-`；空 ⇒ 不启用，不报错）。"""
    return bool(str(key or "").strip())


def recommend_qqmusic(query: str, key: str, *, limit: int = MAX_PICKS) -> dict:
    """QQ 音乐官方接口推荐（需用户自己申请的 `qmk-` key）。"""
    q = str(query or "").strip()
    k = str(key or "").strip()
    if not k:
        return {"ok": False, "picks": [], "source": "QQ音乐", "note": "没填 QQ音乐 key（这一档要你自己去申请）。"}
    if not q:
        return {"ok": False, "picks": [], "source": "QQ音乐", "note": "先说想要什么样的。"}
    body = json.dumps({
        "params": {"keyword": q, "type": "0"},
        "comm": {"skill_version": QQ_SKILL_VERSION},
    }).encode("utf-8")
    try:
        d = _get_json(QQ_BASE + QQ_SEARCH, headers={
            "Authorization": "Bearer " + k,
            "Content-Type": "application/json",
        }, data=body)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "picks": [], "source": "QQ音乐",
                "note": "QQ音乐接口没通（%s）—— key 对不对、有没有过期，检查一下。" % str(e)[:50]}
    songs = list(d.get("songs") or [])
    if not songs:
        return {"ok": False, "picks": [], "source": "QQ音乐",
                "note": "QQ音乐没搜到对得上「%s」的歌 —— ⛔ 不编歌名。" % q[:30]}
    out = []
    for s in songs[:max(1, int(limit or MAX_PICKS))]:
        mid = s.get("songMid")
        out.append({"title": str(s.get("songName") or "").strip(),
                    "artist": str(s.get("singerName") or "").strip(),
                    "url": str(s.get("songH5Url") or (QQ_SONG_URL % mid if mid else "")),
                    "why": "按你说的「%s」找的" % q[:16], "source": "QQ音乐"})
    return {"ok": True, "picks": out, "source": "QQ音乐",
            "note": "来自 QQ音乐（不下载不转发，点链接自己去听）。"}


# ── 统一入口 ──────────────────────────────────────────────────────
def recommend_online(query: str, *, source: str = "netease", qq_key: str = "",
                     want_playlist: bool = True, limit: int = MAX_PICKS) -> dict:
    """在线推荐统一入口：`source` ∈ {netease, qqmusic}。

    ⛔ **任何失败都如实返回 `ok=False`**，绝不拿本地表冒充在线结果。
    """
    src = str(source or "netease").strip().lower()
    if src in ("qq", "qqmusic", "qq_music") and qqmusic_enabled(qq_key):
        return recommend_qqmusic(query, qq_key, limit=limit)
    return recommend_netease(query, want_playlist=want_playlist, limit=limit)
