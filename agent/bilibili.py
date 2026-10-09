# -*- coding: utf-8 -*-
"""B 站视频解析（只读、免登录、只用公开接口）。

为什么有这个模块：
看B站视频」。本模块负责第一件——**把群友丢进来的 B 站链接解析成人话**（标题 / UP / 时长 /
简介 / 分P / 字幕）。第二件（下载后当文件转发）与第三件（下载后抽帧 + 本机 ASR）走
`bilibili.download()`，不在这里。

三条口径（与项目其它网络模块一致）：
  1. **只读公开信息**：只用 `api.bilibili.com` 的公开 GET 接口，不带 cookie、不带登录态、
     不带任何用户凭据；不碰「需要登录才有」的接口。
  2. **绝不假装**：网络不通 / 视频不存在 / 接口返回错误码 / 拿不到字幕 ⇒ 返回 `None` + 明确原因，
     **绝不编造标题、绝不猜视频内容**。这是本项目反复钉的红线。
  3. **出网是被选中才发生**：本模块只有在模型真调 `read_bilibili` 时才发请求；控制台面板里
     这一项的开关与"会出网"提示由上层负责写清楚（产品口径＝绝大部分不出网、出网一律可选）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

API_VIEW = "https://api.bilibili.com/x/web-interface/view?bvid=%s"
API_PLAYER = "https://api.bilibili.com/x/player/v2?bvid=%s&cid=%s"
#: 取播放地址（fnval=16 ⇒ DASH 分片；我们**只要音频轨**，比整段视频小一个数量级）
API_PLAYURL = "https://api.bilibili.com/x/player/playurl?bvid=%s&cid=%s&fnval=16&qn=64"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 12

#: BV 号：BV + 10 位 base58 字符（B 站现行格式）
RE_BV = re.compile(r"\bBV[0-9A-Za-z]{10}\b")
#: 旧式 av 号
RE_AV = re.compile(r"\bav(\d{1,12})\b", re.I)
#: b23.tv 短链
RE_B23 = re.compile(r"https?://b23\.tv/[0-9A-Za-z]+", re.I)
#: 完整视频页
RE_PAGE = re.compile(r"https?://(?:www\.|m\.)?bilibili\.com/video/(BV[0-9A-Za-z]{10}|av\d{1,12})", re.I)

# ── 多平台外链识别─────────────────────────────────────
# 为什么放在这里而不是独立模块：本模块已经是「视频外链」这件事的唯一落点（B 站之外全仓零识别），
# 抽出去只会多一个 import 层、多一处要同步的常量表；而 B 站那段老识别（`parse`）**一个字节都不动**，
# 新表只看域名、不碰 BV/av 正则 ⇒ 「B 站优先走老路」天然成立。
# 口径：**只认域名，不猜内容**（认不出＝unknown，绝不硬认）。
#: 平台 → 该平台认得的域名（子域一律放行：`www.`/`m.`/`v.` 等前缀由 `_host_of` 归一后比对）
PLATFORM_HOSTS = {
    "bilibili": ("bilibili.com", "b23.tv"),
    "douyin": ("douyin.com", "iesdouyin.com"),
    "kuaishou": ("kuaishou.com",),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com"),
    "youtube": ("youtube.com", "youtu.be"),
    "weibo": ("weibo.com", "weibo.cn"),
}
#: 认视频链接用的粗正则（只要 http(s)://…，具体平台交给域名表判）
RE_URL = re.compile(r"https?://[^\s<>\"'）)】\]]+", re.I)

#: **各平台"打算怎么下"**（V-12 平台注册器：把"认链接"与"能不能下"收在同一张表）。
#   `via` 取值：`bilibili_api`（走本模块公开接口，能拿字幕/音频轨，最省）｜`yt_dlp`（通用下载器）。
#   ⛔ **这张表只说"打算怎么下"**；"**现在**到底能不能下"由 `can_download()` **现算**（看装了没装），
#      —— 写死"能下"会变成骗人的开关，写死"不能下"又会在装了之后仍说不行。
#   ⛔ **视频号不在表里**：它的识别不能按域名（`weixin.qq.com` 是通用域名，按域名判会把普通链接也认成视频号），
#      要按 `/sph/` **路径**判 ＋ 走浏览器自动化（未落地）⇒ 留到 V-11 单独做，**先不摆一张下不了的配置**。
_PLATFORM_DL = {
    "bilibili": {"via": "bilibili_api", "note": "B 站公开接口（音频轨/字幕，最省）"},
    "youtube": {"via": "yt_dlp", "note": "yt-dlp"},
    # ⭐ 下面三条是**本机实测**（`python -m yt_dlp --list-extractors`）：Douyin / XiaoHongShu / Weibo
    #   都有对应 extractor ⇒ **"能不能下"由 `can_download()` 现算**，这里只写"打算怎么下"。
    "douyin": {"via": "yt_dlp", "note": "yt-dlp（实测有 Douyin extractor，一般出无水印版）"},
    "xiaohongshu": {"via": "yt_dlp", "note": "yt-dlp（实测有 XiaoHongShu extractor）"},
    "weibo": {"via": "yt_dlp", "note": "yt-dlp（实测有 Weibo / WeiboVideo extractor）"},
    # ⛔ 快手：**实测 yt-dlp 没有 kuaishou extractor** ⇒ 这条路走不通。**如实写"没有"**，
    #    不许写成"yt-dlp"让人以为能下（那会让用户白等一次失败）。
    "kuaishou": {"via": "none", "note": "本机 yt-dlp **没有** kuaishou extractor ⇒ 这条路走不通"},
}

#: 视频号分享链接 `weixin.qq.com/sph/<id>` 与网页预览页 `channels.weixin.qq.com/finder-preview/pages/sph?id=<id>`。
#   ⛔ **按路径判，不按域名判** —— `weixin.qq.com` 是通用域名，按域名会把普通链接也认成视频号。
RE_SPH = re.compile(r"^https?://weixin\.qq\.com/sph/[0-9A-Za-z]+", re.I)
RE_SPH_PREVIEW = re.compile(
    r"^https?://channels\.weixin\.qq\.com/finder-preview/pages/sph\?(?:.*&)?id=[0-9A-Za-z]+", re.I)


def is_wechat_channels(url: str) -> bool:
    """是不是**视频号**链接（按**路径**判：`/sph/<id>` 或 `finder-preview/pages/sph?id=`）。

    为什么不并进 `PLATFORM_HOSTS`：那是**按域名**认平台的表，而 `weixin.qq.com` 是通用域名 ——
    把域名塞进去，任何 `weixin.qq.com` 的链接都会被认成"视频号"，于是**认错平台**比"认不出"更糟。
    """
    s = str(url or "").strip()
    return bool(RE_SPH.match(s) or RE_SPH_PREVIEW.match(s))


def channels_download_support():
    """视频号**现在**能不能下 ⇒ `(ok, why)`；不能时**给用户一个能照做的替代**。

    ⛔ 三态必须分清（否则就是"**假开关**"—— 面板能勾、后端不读、用户勾了没反应）：
      ① **开关关着**（默认）⇒ 说"未启用 + 怎么启用"；
      ② **开关开着但环境没齐**（没装 Playwright）⇒ 说"差哪一步、点哪看步骤"；
      ③ **都齐了** ⇒ 目前仍回"**解析链尚未落地**"（**如实**，不假装能下）。
    """
    _on = False
    try:
        from .config import get_config
        _on = bool(((get_config() or {}).get("wechat") or {}).get("channels_download"))
    except Exception:
        _on = False
    if not _on:
        return False, ("视频号下载**未启用**（「微信」面板那个开关默认关，本技能不开任何东西）⇒ "
                       "想用就在面板里打开它、再点「怎么用」看三步；"
                       "现在遇到视频号链接，可以先把它存成本地文件发我 —— 那条路是通的")
    # ⛔ 探测用 `find_spec`，**不许 `import playwright` 试**：playwright 包体很大，
    #    而这里只判"装没装"、一个符号都不用 ⇒ import 纯属白吃常驻内存。
    import importlib.util as _ilu
    _pw = _ilu.find_spec("playwright") is not None
    if not _pw:
        return False, ("视频号下载已启用，但本机**还没装 Playwright** ⇒ 点「微信」面板的「怎么用」"
                       "看三步：装 Playwright → 在专用窗口里扫码登录一次腾讯元宝 → 之后长期有效"
                       "（不装系统证书、不改系统代理）")
    return False, ("视频号下载已启用、Playwright 也在，但**解析链本体尚未落地**"
                   "（要接元宝 get_parse_result → finder-preview feed 那一段）⇒ "
                   "如实告诉你：现在这条还不能真下，可先把视频存成本地文件发我")


def _host_of(url: str) -> str:
    """从 URL 里抠出**主机名小写**（去掉 userinfo / 端口 / 路径 / 查询串）。取不到返回空串。"""
    s = str(url or "").strip()
    m = re.match(r"https?://(?:[^/@\s]*@)?([^/?#:\s]+)", s, re.I)
    return (m.group(1) if m else "").strip().lower().rstrip(".")


def _platform_of_host(host: str) -> str:
    """主机名 → 平台名。`bilibili.com` 与任意子域（`www.` / `m.` / `b23.tv` 等）都命中。"""
    for plat, hosts in PLATFORM_HOSTS.items():
        for h in hosts:
            if host == h or host.endswith("." + h):
                return plat
    return ""


def identify_media_url(text: str):
    """从一段文字里认出**任意支持平台**的视频外链 ⇒ `{"platform", "url", "kind"}` 或 `None`。

    与 `parse()`（只认 B 站）**互不影响**：本函数是"这是哪个平台的什么链接"的粗判，`parse()` 是
    "B 站视频标识是什么"的精判。上层口径是 **B 站优先走 `parse()` 老路**（本函数命中的 bilibili
    结果只当作"确实是 B 站链接"的信号，解析仍交给老路）。

    返回 `None` ＝ 这段话里没有可识别的外链（**不是报错**）。
    认不出平台 ⇒ 返回 `platform="unknown"` 且 `kind="unknown"`（由上层如实报"暂不支持"）。
    """
    s = str(text or "")
    m = RE_URL.search(s)
    if not m:
        return None
    url = m.group(0).rstrip(".,;，。；") # 中文句末标点常被正则一起吃进来，切掉
    host = _host_of(url)
    if not host:
        return None
    plat = _platform_of_host(host)
    # 只把"看起来是视频内容页/短链"的链接算 kind=video：**不做平台特有的路径猜解**
    # （各平台分享链接形态多变，硬猜路径＝编造；认不出就走 unknown，由上层如实说）。
    if not plat:
        return {"platform": "unknown", "url": url, "kind": "unknown"}
    return {"platform": plat, "url": url, "kind": "video"}


def download_external(url: str, out_dir: str, timeout: int = 300):
    """**任意平台外链的统一下载入口**（V-1／V-12）：按平台表挑下载器；**下不了就如实说为什么**。

    返回 `(路径|None, 说明)`。⛔ **绝不假装下过**（本项目红线）。
    分工：B 站走本模块的公开接口（能拿字幕/音频轨，最省）；其余平台走 **yt-dlp**。
    """
    info = identify_media_url(url) or {}
    if is_wechat_channels(info.get("url") or url):
        _ok2, _why2 = channels_download_support()
        return None, _why2
    plat = str(info.get("platform") or "")
    if not plat or plat == "unknown":
        return None, "认不出这是哪个平台的链接（本模块只认域名，不猜内容）"
    ok, how = can_download(plat)
    if not ok:
        return None, "%s：%s" % (_PLATFORM_DL.get(plat, {}).get("note") or plat, how)
    if how == "bilibili_api":
        return download(info.get("url") or url, out_dir, timeout=timeout)
    return _download_via_ytdlp(info.get("url") or url, out_dir, timeout=timeout)


def ytdlp_available():
    """yt-dlp 能不能用（**先查 python 模块，再查可执行**）。返回 `(ok, how|why)`。

    ⛔ 探测用 `find_spec`，**不许 `import yt_dlp` 试**：yt-dlp 带一大坨依赖（约 25 MB），
       而本函数只回答"能不能下"、**一个符号都不用** ⇒ import 纯属白吃内存。
       真要用它时（`_download_via_ytdlp`）再 import，代价只在"确实点了下载"那一刻付。
    """
    import importlib.util as _ilu
    if _ilu.find_spec("yt_dlp") is not None:
        return True, "python -m yt_dlp"
    p = ytdlp_bin()
    if p:
        return True, p
    return False, "本机没有 yt-dlp（既没有 python 模块、也没有可执行文件）"


def can_download(platform: str):
    """这个平台**现在**能不能下 ⇒ `(ok, how|why)`。**现算**（依赖装了没装），不写死。"""
    p = str(platform or "").strip().lower()
    if p == "bilibili":
        return True, "bilibili_api"
    if p in _PLATFORM_DL:
        via = _PLATFORM_DL[p].get("via")
        if via == "none":
            return False, _PLATFORM_DL[p].get("note") or "这条路走不通"
        if via == "playwright":
            return False, "这条路需要 Playwright 浏览器自动化（本机未装），且首次要一次性登录"
        return ytdlp_available()
    return False, "暂不支持这个平台（认得出域名，但没有对应下载器）"


def _download_via_ytdlp(url: str, out_dir: str, timeout: int = 300):
    """yt-dlp 路线（通用兜底）：下到 `out_dir`，回**真实文件路径**；失败回 yt-dlp 的**最后一行**原因。"""
    ok, how = ytdlp_available()
    if not ok:
        return None, how
    os.makedirs(out_dir, exist_ok=True)
    cmd = (["-m", "yt_dlp"] if how.startswith("python ") else [how])
    exe = sys.executable if how.startswith("python ") else cmd[0]
    args = ([exe] + cmd[1:] if how.startswith("python ") else cmd)
    out_tpl = os.path.join(out_dir, "%(title).60s_%(id)s.%(ext)s")
    try:
        r = subprocess.run(args + ["-f", "mp4/best", "--no-playlist",
                                   "--print", "after_move:filepath",
                                   "-o", out_tpl, url],
                           capture_output=True, text=True, timeout=timeout,
                           creationflags=(0x08000000 if os.name == "nt" else 0))
    except Exception as e: # noqa: BLE001
        return None, "调用 yt-dlp 失败：%s" % (str(e)[:80] or type(e).__name__)
    if r.returncode != 0:
        tail = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
        return None, "yt-dlp 没下成：%s" % (tail[-1][:120] if tail else "未知错误")
    lines = [x for x in (r.stdout or "").strip().splitlines() if x.strip()]
    path = lines[-1].strip() if lines else ""
    if not path or not os.path.isfile(path):
        return None, "yt-dlp 跑完了但没给出可用文件路径"
    return path, ""


def media_download_plan(url: str) -> dict:
    """**给面板/工具用的"这链接能不能下"预览**：`{platform, url, ok, how|why, note}`。

    为什么单独一个：模型要在**动手之前**知道"这条路通不通"，才能如实回答用户
    （而不是先答应、再失败）。
    """
    info = identify_media_url(url) or {}
    plat = str(info.get("platform") or "")
    if not plat or plat == "unknown":
        return {"platform": plat or "unknown", "url": str(url or ""),
                "ok": False, "why": "认不出平台（只认域名，不猜）", "note": ""}
    ok, how = can_download(plat)
    return {"platform": plat, "url": info.get("url") or str(url or ""),
            "ok": bool(ok), "how": how if ok else "", "why": "" if ok else how,
            "note": (_PLATFORM_DL.get(plat) or {}).get("note", "")}


def parse(text: str):
    """从一段文字里认出 B 站视频标识。返回 `{bvid|aid, raw, kind}` 或 `None`。

    认不出就返回 `None`（**不是报错**）——上层据此判断"这段话里没有 B 站视频"。
    """
    s = str(text or "")
    m = RE_BV.search(s)
    if m:
        return {"bvid": m.group(0), "aid": None, "raw": m.group(0), "kind": "bv"}
    m = RE_PAGE.search(s)
    if m:
        tag = m.group(1)
        return ({"bvid": tag, "aid": None, "raw": tag, "kind": "bv"} if tag.lower().startswith("bv")
                else {"bvid": None, "aid": tag[2:], "raw": tag, "kind": "av"})
    m = RE_AV.search(s)
    if m:
        return {"bvid": None, "aid": m.group(1), "raw": m.group(0), "kind": "av"}
    m = RE_B23.search(s)
    if m:
        return {"bvid": None, "aid": None, "raw": m.group(0), "kind": "short"}
    return None


def _get_json(url: str, timeout: int = TIMEOUT):
    """GET 一个 JSON 接口 ⇒ `(dict|None, 失败原因)`。任何异常都如实返回原因，不抛。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.bilibili.com/",
        "Accept": "application/json,text/plain,*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            # 带上限读（接口回包正常几 KB~几百 KB；4MB 是宽裕上限，超了当"回包异常"）
            try:
                from .safe_fetch import read_capped
                raw = read_capped(r, 4 * 1024 * 1024, "B 站接口回包")
            except ImportError:
                raw = r.read(4 * 1024 * 1024 + 1)[:4 * 1024 * 1024]
    except urllib.error.HTTPError as e:
        return None, "HTTP %s（接口拒绝或不存在）" % e.code
    except Exception as e:
        return None, "连不上 B 站接口：%s" % (str(e)[:80] or type(e).__name__)
    try:
        return json.loads(raw.decode("utf-8", "replace")), ""
    except Exception as e:
        return None, "接口回的不是 JSON：%s" % str(e)[:60]


def _resolve_short(url: str, timeout: int = TIMEOUT):
    """把 b23.tv 短链跟到真实页（只取 Location，不下载正文）。返回 `(最终URL|None, 原因)`。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.geturl(), ""
    except Exception as e:
        return None, "短链跟不过去：%s" % (str(e)[:60] or type(e).__name__)


def _mmss(sec) -> str:
    try:
        n = int(sec)
    except Exception:
        return ""
    if n <= 0:
        return ""
    return "%d:%02d" % (n // 60, n % 60) if n < 3600 else "%d:%02d:%02d" % (n // 3600, n % 3600 // 60, n % 60)


def view(bvid: str, aid: str | None = None, timeout: int = TIMEOUT):
    """取视频基本信息 ⇒ `(dict|None, 原因)`。只认公开接口的 `code==0`。"""
    url = API_VIEW % bvid if bvid else (API_VIEW % ("av" + str(aid)) if aid else "")
    if not url:
        return None, "没有可用的视频标识"
    data, why = _get_json(url, timeout)
    if data is None:
        return None, why
    code = data.get("code")
    if code != 0:
        return None, "B 站接口回了 code=%s（%s）" % (code, str(data.get("message") or "没有这条视频")[:40])
    d = data.get("data") or {}
    if not d.get("bvid"):
        return None, "接口没给 bvid（视频可能已删除或仅自己可见）"
    pages = d.get("pages") or []
    owner = d.get("owner") or {}
    stat = d.get("stat") or {}
    # AI 标识：**唯一可信来源是后台字段**（`argue_info.argue_msg`）。
    # **客户端显示不一致**——同一个视频后台写着「含AI生成内容」，
    # 实测**手机端看得见、电脑端看不见**；另一个视频后台是空的（真没标）。
    # ⇒ 想判断有没有标识，只能读接口，别看界面（两个端会给出相反的印象）。
    _argue = d.get("argue_info")
    out = {
        "bvid": d.get("bvid"), "aid": d.get("aid"),
        "title": str(d.get("title") or "").strip(),
        "up": str(owner.get("name") or "").strip(),
        "up_mid": owner.get("mid"),
        "desc": str(d.get("desc") or "").strip(),
        "duration": _mmss(d.get("duration")),
        "duration_sec": d.get("duration"),
        "pic": d.get("pic"),
        "pubdate": d.get("pubdate"),
        "cid": d.get("cid"),
        "pages": [{"cid": p.get("cid"), "page": p.get("page"), "part": str(p.get("part") or "").strip(),
                   "duration": _mmss(p.get("duration"))} for p in pages if isinstance(p, dict)],
        "stat": {"view": stat.get("view"), "like": stat.get("like"), "coin": stat.get("coin"),
                 "favorite": stat.get("favorite"), "reply": stat.get("reply"), "danmaku": stat.get("danmaku")},
        "url": "https://www.bilibili.com/video/%s" % d.get("bvid"),
        "ai_label": str((_argue or {}).get("argue_msg") or "").strip(),
        "ai_label_known": isinstance(_argue, dict),
    }
    return out, ""


def subtitles(bvid: str, cid, timeout: int = TIMEOUT):
    """取字幕 ⇒ `(文本, 原因)`。**没有字幕就如实说没有**，不猜、不编。"""
    if not bvid or not cid:
        return "", "缺 bvid 或 cid，取不了字幕"
    data, why = _get_json(API_PLAYER % (bvid, cid), timeout)
    if data is None:
        return "", why
    if data.get("code") != 0:
        return "", "字幕接口回了 code=%s" % data.get("code")
    sub = ((data.get("data") or {}).get("subtitle") or {})
    items = sub.get("subtitles") or []
    if not items:
        return "", "这条视频没有字幕（作者没传，或需要登录才看得到）"
    urls = []
    for it in items:
        u = str((it or {}).get("subtitle_url") or "")
        if u.startswith("//"):
            u = "https:" + u
        if u.startswith("http"):
            urls.append((str((it or {}).get("lan_doc") or (it or {}).get("lan") or ""), u))
    if not urls:
        return "", "字幕列表是空的（只有标题没有正文地址）"
    label, u = urls[0]
    body, why2 = _get_json(u, timeout)
    if body is None:
        return "", "字幕正文拉不下来：" + why2
    lines = []
    for seg in (body.get("body") or []):
        t = str((seg or {}).get("content") or "").strip()
        if t:
            lines.append(t)
    if not lines:
        return "", "字幕文件是空的"
    return "\n".join(lines), ""


_REAL_YTDLP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "runtime", "python", "Scripts", "yt-dlp.exe")


def ytdlp_bin() -> str:
    """找 yt-dlp：先看项目自带 runtime，再看 PATH。找不到返回空串（由调用方如实报错）。"""
    if os.path.exists(_REAL_YTDLP):
        return _REAL_YTDLP
    import shutil
    return shutil.which("yt-dlp") or ""


# ── 「自己听视频」：只下音频轨 + 本机识别（**不需要 yt-dlp**）─────────────────────
# 整段视频动辄几十 MB，而识别只需要一条
# 几十 kbps 的音频轨（实测 1:19 的视频音频 659 KB）⇒ 走 playurl 的 DASH 音频流，比下整段视频
# 便宜一个数量级，而且**不依赖 yt-dlp**。识别用项目既有的本机 SAPI 听写（离线、零下载）。


def audio_url(bvid: str, cid, timeout: int = TIMEOUT):
    """取音频流地址 ⇒ `(url|None, 原因)`。取最低码率那条（够识别用，省流量）。"""
    if not bvid or not cid:
        return None, "缺 bvid 或 cid，拿不到音频流"
    data, why = _get_json(API_PLAYURL % (bvid, cid), timeout)
    if data is None:
        return None, why
    if data.get("code") != 0:
        return None, "取播放地址的接口回了 code=%s（%s）" % (data.get("code"), str(data.get("message") or "")[:30])
    dash = ((data.get("data") or {}).get("dash") or {})
    auds = [a for a in (dash.get("audio") or []) if isinstance(a, dict)]
    if not auds:
        return None, "这条视频没有可取的音频流（可能是直播回放 / 付费内容 / 需要登录）"
    a = sorted(auds, key=lambda x: x.get("bandwidth") or 0)[0]
    u = str(a.get("baseUrl") or a.get("base_url") or "")
    return (u or None), ("" if u else "接口没给音频地址")


#: 合法媒体容器头的标记（B 站音频轨是 ISO-BMFF：`ftyp`/`styp`；少数带 `free`/`skip` 填充盒；再兜 ID3）。
_MEDIA_MAGICS = (b"ftyp", b"styp", b"free", b"skip", b"ID3")


def media_magic_ok(head: bytes) -> bool:
    """下载结果的文件头**像不像合法媒体容器**（纯函数，便于判据直接钉样本）。

    为什么单独抽出来：`_download` 里那一枪要**真下载**才跑得到，而"magic 判定"本身
    可以用三个样本直接钉住（真 m4a 头 / HTML 错误页头 / 空文件），
    ⇒ 判据不必联网、也不必伪造 CDN。
    """
    return any(m in (head or b"") for m in _MEDIA_MAGICS)


def _download(url: str, dest: str, timeout: int = 180, max_bytes: int = 384 * 1024 * 1024):
    """把音频流落到文件 ⇒ `(字节数, 原因)`。B 站 CDN 要带 Referer，否则 403。

    ⛔ 
    · 这个地址来自 **B 站接口的回包**（`audio_url()`）⇒ 下手前先过 `safe_fetch` 闸门
      （拿不到安全层就 fail-closed，一个字节都不下）；
    · 体积上限：低码率音频轨（1:19 的视频约 659KB）正常远小于它，超了就是异常。

    ⛔ 审计 第 5 条**（改了两处口径，都写在这里免得被改回去）：
    · **上限从 128MB 抬到 384MB** —— 128MB 会**误伤正经内容**：2 小时 192kbps ≈ 173MB、
      320kbps ≈ 288MB（审计点名的"2 小时高码率音频"就是这么被拒的）。384MB 仍是有界上限，
      而且超限走 `truncated` ⇒ 上层如实报错、**不落半成品**（`download_audio` 会删小文件）。
    · **整轮墙钟换成"逐 recv 的 socket 超时"**（ 的老写法用整轮墙钟，审计实测会把
      **稳定推进**的流掐断：2.5 秒中止、已收 589824B）。现在超时是 `timeout=180s` 级别的
      **空闲超时**：对面涓流照旧被掐，但**只要在推进就一直下**（`_pinned_exchange` 逐块读、
      socket 超时由 `PINNED_TIMEOUT_S`/`timeout` 管）。

    ⛔ 审计 老写法是"`guard_remote_url` 校验一次 ⇒ `urllib.urlopen(url)`
    **再解析一次域名**"——闸门看的是第 1 次解析，真正连接用的是第 2 次解析，
    E 线实测**把环回服务的 190 字节落盘**。现在整条下载走 `safe_fetch.fetch_pinned_stream()`：
    **一次解析、钉进 socket**，顺带把"上限 / 超时 / 上限读体"三件事按统一口径一起收掉。
    """
    try:
        from .safe_fetch import fetch_pinned_stream
    except Exception:
        return 0, "安全抓取层不可用：拒绝下载接口给的音频地址（fail-closed）"
    try:
        r = fetch_pinned_stream(url, dest, timeout=timeout, max_bytes=int(max_bytes),
                                headers={"Referer": "https://www.bilibili.com/"})
    except Exception as e:
        return 0, "接口给的音频地址不可信：%s" % (str(e)[:70] or type(e).__name__)
    if int(r.get("status") or 0) in (403, 401):
        return 0, "音频流 HTTP %s（B 站 CDN 拒绝，通常是防盗链）" % r.get("status")
    if int(r.get("status") or 0) >= 400:
        return 0, "音频流 HTTP %s" % r.get("status")
    if r.get("truncated"):
        return 0, "音频流超过 %.0fMB 上限（已中止）" % (int(max_bytes) / 1048576.0)
    # 第 4 重：**文件头 magic**（外部技能那套"四重校验"里我方独缺的一重）。
    #   ⛔ 为什么必须有：状态码 200 + Content-Type 正常，**仍可能存下的是错误页/风控页**
    #     （CDN 用 200 返回一段 HTML 是常见做法）⇒ 不校验就是"把 HTML 当 m4a"。
    #   B 站音频轨是 ISO-BMFF（`ftyp`/`styp`），少数带 `free`/`skip` 填充盒；再兜一层 ID3。
    magic = b""
    try:
        with open(dest, "rb") as fh:
            magic = fh.read(16)
    except Exception as e: # noqa: BLE001
        return 0, "读不出下载结果的文件头：%s" % (str(e)[:60] or type(e).__name__)
    if not media_magic_ok(magic):
        return 0, ("下载内容不是合法媒体容器（文件头 %r）——多半是风控/错误页，已丢弃"
                   % magic[:8])
    return int(r.get("bytes") or 0), ""


def download_audio(bvid: str, cid, out_dir: str, timeout: int = 180):
    """下音频轨 ⇒ `(m4a 路径|None, 原因)`。**失败不落半成品**（小文件一律删掉再报错）。"""
    u, why = audio_url(bvid, cid, timeout)
    if not u:
        return None, why
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        return None, "音频临时目录建不出来：%s" % str(e)[:60]
    dest = os.path.join(out_dir, "%s.m4a" % (bvid or "audio"))
    n, why2 = _download(u, dest, timeout)
    if n < 10240:
        try:
            if os.path.exists(dest):
                os.remove(dest)
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass
        return None, (why2 or "下回来的音频太小（%d 字节），当失败处理" % n)
    return dest, ""


def listen(text: str, max_seconds: int = 120, timeout: int = 180):
    """**自己听一遍这条 B 站视频** ⇒ `(听到的文字, 原因)`。

    链路：解析 → 取音频流 → 下音频 → ffmpeg 抽 16k 单声道 WAV → 本机 SAPI 听写。
    全程不出网（除了取音频那一次），不需要 yt-dlp。听不出内容就如实说，**绝不编**。
    ⚠️ 用完**必删临时目录**（音频 + WAV 加起来几 MB；`finally` 里删，成功失败都删）。
    """
    import tempfile
    from . import housekeeping as HK
    v, why = info(text, want_subtitle=False, timeout=TIMEOUT)
    if not v:
        return "", why
    tmp = tempfile.mkdtemp(prefix="pm-bili-listen-")
    try:
        path, why2 = download_audio(v["bvid"], v.get("cid"), tmp, timeout)
        if not path:
            return "", why2
        from . import video_read as VR
        from . import voice
        wav = os.path.join(tmp, "audio.wav")
        if not VR.extract_audio(path, wav, max_seconds=max_seconds):
            return "", "从音频轨里抽不出 WAV（这条视频可能没有声音）"
        # 约定：`recognize_wav()` 返回 **(文本, 错误说明)**（别写反，video_read 就为此踩过一个真 bug）
        txt, aerr = voice.recognize_wav(wav, max_seconds=max_seconds)
        if aerr:
            return "", str(aerr)
        txt = str(txt or "").strip()
        if not txt:
            return "", "识别跑通了，但没听出可辨认的说话内容（可能只是音乐/环境声）"
        return txt, ""
    except Exception as e:
        return "", "本机识别异常：%s" % str(e)[:80]
    finally:
        HK.cleanup_dir(tmp)


def download(url_or_bvid: str, out_dir: str, timeout: int = 300):
    """下载视频到 `out_dir` ⇒ `(文件路径|None, 原因)`。

    **依赖外部 `yt-dlp`**（不随包分发，因为它是独立程序且版本更新频繁）。没有就如实报"没装 yt-dlp"，
    绝不假装下载过、也不生成空文件。

    ⚠️ yt-dlp 本身就是通用抽取器（B 站 / 抖音 / 快手 /
    小红书 / YouTube 都吃），所以这里**不再前置 B 站断言** —— 传什么 URL 就下什么。
    平台识别交给 `identify_media_url()`、拒绝不支持的平台由上层做（本函数只管"能不能下"）。
    """
    if not str(url_or_bvid or "").strip():
        return None, "没给链接或 BV 号"
    exe = ytdlp_bin()
    if not exe:
        return None, "要下载视频得先有 yt-dlp（本机没找到：可 `py -3 -m pip install yt-dlp` 或用项目 runtime）"
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        return None, "下载目录建不出来：%s" % str(e)[:60]
    tmpl = os.path.join(out_dir, "%(id)s.%(ext)s")
    # ⛔ **开工前先给目录拍一张快照**：yt-dlp **不告诉你它写了哪些文件**，
    #   而这个目录是**调用方给的**、可能本来就有旧文件（上次下的/别的用途的）
    #   ⇒ 只按"最大最新"去挑，会把**上一次的产物**当成本次结果（而且不报错）。
    #   ⇒ 判据＝**前后差集**：只有"这次新建的"才可能是本次产物。
    try:
        from . import tree_snapshot as _ts
        _before = _ts.snapshot(out_dir)
    except Exception: # noqa: BLE001
        _ts, _before = None, {}
    # ⚠️ A2：yt-dlp 合并音视频轨要 ffmpeg ⇒ 把项目**唯一解析入口**的 ffmpeg 路径透传给它
    #   （`--ffmpeg-location`）。拿不到 ffmpeg 时**不传这一项**（让 yt-dlp 自己找 PATH），
    #   而不是传空串 —— 传空串会让 yt-dlp 直接报参数错，把"没有 ffmpeg"变成"参数非法"。
    args = [exe, "-f", "mp4/best", "--no-playlist", "-o", tmpl]
    ff = ""
    try:
        from .ffmpeg_bin import path as _ff_path
        ff = str(_ff_path() or "")
    except Exception:
        ff = ""
    if ff:
        args += ["--ffmpeg-location", ff]
    args.append(str(url_or_bvid).strip())
    try:
        r = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout,
                           creationflags=0x08000000 if os.name == "nt" else 0) # 不许闪控制台窗
    except Exception as e:
        return None, "yt-dlp 跑挂了：%s" % (str(e)[:70] or type(e).__name__)
    if r.returncode != 0:
        return None, ("yt-dlp 下载失败（rc=%s；可能是需要登录/大会员、视频已删，"
                      "或这个平台 yt-dlp 抽不出来）" % r.returncode)
    # ⛔ 产物只从**这次新建的**里面挑（见上面 `_before` 的注释）。
    #   中间件（`.part`/`.ytdl`）与原子写临时名已经在快照里被跳过了。
    _fresh = []
    if _ts is not None:
        try:
            _after = _ts.snapshot(out_dir)
            _new = _ts.new_since(_before, _after)
            log.info("下载目录差分：%s", _ts.describe(_before, _after))
            for _rel in _new:
                _p = os.path.join(out_dir, _rel)
                try:
                    if os.path.isfile(_p) and os.path.getsize(_p) > 10240:
                        _fresh.append(_p)
                except Exception: # noqa: BLE001
                    continue
        except Exception as _e: # noqa: BLE001
            log.info("目录快照差分不可用（回退旧口径）：%s", str(_e)[:80])
    if not _fresh:
        # 快照不可用（拿不到 `_ts`）时才回退旧口径；能用却"没有新文件"⇒ 如实报没产物。
        if _ts is None:
            _best = None
            for fn in os.listdir(out_dir):
                p = os.path.join(out_dir, fn)
                if os.path.isfile(p) and os.path.getsize(p) > 10240:
                    if _best is None or os.path.getmtime(p) > os.path.getmtime(_best):
                        _best = p
            _fresh = [_best] if _best else []
        else:
            return None, ("yt-dlp 说成功，但这次**没有在下载目录里新建任何文件**"
                          "（目录里原有的旧文件不算 —— 拿旧文件当结果会把上一次的内容当成这次下的）")
    # 一次下载可能同时落多个文件（合并前的音轨/视频轨）⇒ 取**最大的那个**（正片）
    best = max(_fresh, key=lambda p: os.path.getsize(p))
    if not best:
        return None, "yt-dlp 说成功但没找到产物文件（不假装下过）"
    return best, ""


# ── 外链视频「看一遍」：下载 → 抽帧 → 视觉模型───────────
# 为什么要它：B 站那条路（`info()`）只能拿标题/UP/字幕，「没字幕就只剩标题」；而视频理解在本项目
# 已有现成链路（`video_read.read()`：ffmpeg 抽帧 → `model_routes.image` 分流 → 视觉模型 + SAPI 听音频）。
# 这里**只做搬运**：平台识别 → 下载（`download()` 泛化后已能吃任意 URL）→ 交给 `video_read.read()`。
#
# fail-closed 三条：
#   ① 不支持的平台 / 认不出 ⇒ 返回原因，**不硬试**；
#   ② yt-dlp 没装 / 下载失败 ⇒ 原样透传 `download()` 的原因（它已经如实报）；
#   ③ 抽帧失败 ⇒ 原样透传 `video_read.read()` 的 error，**绝不返回空帧当成功**。
def read_external(text: str, max_frames: int = 4, max_seconds: int = 60,
                  download_timeout: int = 300):
    """从一段文字里认出外链视频并「看一遍」⇒ `(结果 dict|None, 原因)`。

    结果 dict 就是 `agent/video_read.py::read()` 的返回（含 `frames` / `audio_text` / `note` /
    `dir` / `ok`），外加 `platform` 与 `url` 两个字段方便上层如实报「读的是哪条」。
    **调用方负责 `video_read.cleanup(res["dir"])`**（临时帧图与音频）。

    `platform == "bilibili"` 时**不在这里处理**：B 站走 `info()` 老路（有字幕就更省），
    上层据此分流。这里只负责非 B 站外链。
    """
    import tempfile
    ident = identify_media_url(text)
    if not ident:
        return None, "这段话里没找到视频链接（抖音 / 快手 / 小红书 / YouTube 等分享链接都行）"
    plat = str(ident.get("platform") or "unknown")
    if plat == "bilibili":
        return None, "这是 B 站链接：请走 read_bilibili（B 站有字幕那条更省，不带字幕才需要下载看）"
    if plat == "unknown":
        return None, ("这个平台的链接我暂不支持（只认 B 站 / 抖音 / 快手 / 小红书 / YouTube）：%s"
                      % str(ident.get("url") or "")[:100])
    url = str(ident.get("url") or "")
    exe = ytdlp_bin()
    if not exe:
        return None, "要下载视频得先有 yt-dlp（本机没找到：可 `py -3 -m pip install yt-dlp` 或用项目 runtime）"
    tmp = tempfile.mkdtemp(prefix="pm-video-") # pm-video- 前缀 ⇒ video_read.cleanup 认得
    path, why = download(url, tmp, timeout=int(download_timeout or 300))
    if not path:
        from . import housekeeping as HK
        HK.cleanup_dir(tmp)
        return None, "这条 %s 链接没能下下来：%s" % (plat, why)
    from . import video_read as VR
    try:
        res = VR.read(path, max_frames=max_frames, max_seconds=max_seconds, work_dir=tmp)
    except Exception as e:
        res = {"ok": False, "error": "读视频异常：%s" % str(e)[:100], "frames": [], "note": "", "dir": tmp}
    res["platform"] = plat
    res["url"] = url
    if not res.get("ok"):
        # 抽帧失败：临时目录由调用方 cleanup（把 dir 带回去，别在这里偷偷删了让上层拿不到证据）
        return None, res.get("error") or "读视频失败"
    return res, ""


def info(text: str, want_subtitle: bool = True, timeout: int = TIMEOUT):
    """主入口：从一段文字里解析 B 站视频 ⇒ `(dict|None, 原因)`。

    返回的 dict 里除基本信息外，还会带 `subtitle`（拿不到就是空串）与 `subtitle_why`（为什么没有）。
    """
    t = str(text or "")
    p = parse(t)
    if not p:
        return None, "这段话里没找到 B 站视频标识（BV 号 / av 号 / b23.tv 短链 / 视频页链接）"
    if p["kind"] == "short":
        real, why = _resolve_short(p["raw"], timeout)
        if not real:
            return None, why
        p2 = parse(real)
        if not p2 or p2["kind"] == "short":
            return None, "短链跟过去之后仍然没认出视频（落点：%s）" % str(real)[:80]
        p = p2
    v, why = view(p.get("bvid"), p.get("aid"), timeout)
    if not v:
        return None, why
    if want_subtitle:
        sub, swhy = subtitles(v["bvid"], v.get("cid"), timeout)
        v["subtitle"] = sub
        v["subtitle_why"] = swhy
    return v, ""


def to_text(v: dict) -> str:
    """把 `info()` 的结果压成给模型看的短文本（控制长度，避免把两万字堆进上下文）。"""
    if not v:
        return ""
    lines = [
        "【B 站视频】%s" % v.get("title") or "",
        "UP：%s ｜ 时长：%s ｜ 播放：%s ｜ 点赞：%s" % (v.get("up") or "-", v.get("duration") or "-",
                                                     (v.get("stat") or {}).get("view") or "-",
                                                     (v.get("stat") or {}).get("like") or "-"),
        "链接：%s" % (v.get("url") or ""),
    ]
    if v.get("desc"):
        lines.append("简介：" + v["desc"][:400])
    # AI 标识只认后台字段；字段缺失时**什么都不说**（不假装"没有标识"）
    if v.get("ai_label_known"):
        lines.append("AI 标识：" + (v["ai_label"] if v.get("ai_label")
                                 else "这条没标（后台字段为空）"))
    if len(v.get("pages") or []) > 1:
        lines.append("分P：" + " / ".join("%s.%s" % (p.get("page"), p.get("part")) for p in v["pages"][:10]))
    if v.get("subtitle"):
        lines.append("字幕（前 1500 字）：\n" + v["subtitle"][:1500])
    else:
        lines.append("字幕：没有（%s）" % (v.get("subtitle_why") or "未取"))
    return "\n".join(x for x in lines if x)
