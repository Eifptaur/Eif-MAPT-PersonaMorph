# -*- coding: utf-8 -*-
"""B 站视频解析（只读、免登录、只用公开接口）。

为什么有这个模块：用户 2026-09-15 点名「我们感觉丢掉了好多目标啊：解析B站视频、转发B站视频、
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
import os
import re
import subprocess
import urllib.error
import urllib.request

API_VIEW = "https://api.bilibili.com/x/web-interface/view?bvid=%s"
API_PLAYER = "https://api.bilibili.com/x/player/v2?bvid=%s&cid=%s"
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
            raw = r.read()
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
    # 2026-09-15 实测：手机 App 的视频详情页**不渲染**这一行（肇岁初十 BV1nkYV6oEMZ 后台写着
    # 「含AI生成内容」，手机端看不见、PC 网页端才看得见）⇒ 想判断有没有标识，只能读接口，别看界面。
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


def download(url_or_bvid: str, out_dir: str, timeout: int = 300):
    """下载 B 站视频到 `out_dir` ⇒ `(文件路径|None, 原因)`。

    **依赖外部 `yt-dlp`**（不随包分发，因为它是独立程序且版本更新频繁）。没有就如实报"没装 yt-dlp"，
    绝不假装下载过、也不生成空文件。
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
    try:
        r = subprocess.run([exe, "-f", "mp4/best", "--no-playlist", "-o", tmpl, str(url_or_bvid).strip()],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
    except Exception as e:
        return None, "yt-dlp 跑挂了：%s" % (str(e)[:70] or type(e).__name__)
    if r.returncode != 0:
        return None, "yt-dlp 下载失败（rc=%s；可能是需要登录/大会员，或视频已删）" % r.returncode
    best = None
    for fn in os.listdir(out_dir):
        p = os.path.join(out_dir, fn)
        if os.path.isfile(p) and os.path.getsize(p) > 10240:
            if best is None or os.path.getmtime(p) > os.path.getmtime(best):
                best = p
    if not best:
        return None, "yt-dlp 说成功但没找到产物文件（不假装下过）"
    return best, ""


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
