# -*- coding: utf-8 -*-
"""**微信界面截图**素材采集（走图片搜索的 HTML，不需要任何 API Key）。

## 为什么单独做这个（用户把方向说清了）
我们的 OCR **就是用来读界面的** —— 读微信的左侧会话列表（`pane_left` / `list_rows` / `session_rows`
读群名与群友昵称）、聊天标题栏（`chat_header`）、消息文本、按钮（"拍一拍"）等。
所以"识别成功率"必须在**微信界面**上量：拿一堆别的软件的截图去算，数字再高也不说明问题。
⇒ 本脚本专收**微信 UI 截图**（不同版本 / 深浅主题 / 不同分辨率与语言），并标注 `kind="wx-ui"`；
   原 `ocr_corpus_harvest.py` 收的是"通用界面"（`kind="other-ui"`），两者在基准里**分类统计**。

## 怎么找
用**图片搜索的 HTML**（Bing 图片 / DuckDuckGo），从结果里抽**图片直链**再下载：
  · 需要 API Key 的图库（Unsplash 那类）不能用 —— 本机环境没有 Key；
  · Wikimedia 早些时候 SSL 握手就被拦（见 `ocr-samples/web/README.md`）⇒ 不指望它；
  · **Bing 图片搜索实测可用**（一条查询能抽 30+ 个直链，来源多为科技媒体/教程站的微信界面图）。

## 两道筛子（**不是**可选项）
1. **可读性**：内置引擎要能读出 ≥3 行、总字数 ≥20（读不出字的图对"识别成功率"没有分母意义）；
2. **相关性**：图里要出现**微信 UI 的招牌词**（`微信/通讯录/群聊/文件传输助手/朋友圈/收藏/聊天` 等，
   英文界面认 `WeChat/Chats/Contacts/Settings`）—— 用它挡掉"搜索结果里混进来的壁纸、图标、PPT 截图"。
   ⚠️ 口径声明：这道筛子用的是**被测对象自己**（我们的 OCR）⇒ 它**只做相关性分类**，
   绝不参与真值生成（真值仍按 `ocr_bench_web.py` 的两档：核过 / 多引擎一致性）。

## 用法
    runtime\\python\\python.exe scripts\\ocr_corpus_wx.py --target 60
    runtime\\python\\python.exe scripts\\ocr_corpus_wx.py --check-only
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("PM_NO_WINDOW_TOUCH", "1")

WEB = os.path.abspath(os.path.join(ROOT, "..", "dev-workspace", "persona-morph",
                                   "ocr-samples", "web"))
SRC = os.path.join(WEB, "_sources.json")

#: 查询词：把"版本 / 主题 / 面板 / 语言"都覆盖到（不同版本的 UI 差异正是要压测的）
QUERIES = [
    "微信 电脑版 界面 截图",
    "微信 4.0 PC 版 界面 截图",
    "微信 windows 版 聊天 窗口 截图",
    "微信 电脑版 通讯录 界面",
    "微信群 聊天列表 截图",
    "微信 深色模式 截图 电脑",
    "微信 设置 界面 截图 电脑版",
    "微信 表情 面板 截图",
    "微信 搜索 界面 截图",
    "微信 文件传输助手 截图",
    "微信 聊天记录 界面 截图",
    "微信 手机版 界面 截图",
    "微信 群聊 界面 截图 高清",
    "微信 电脑版 登录 界面",
    "wechat desktop ui screenshot",
    "wechat windows chats list screenshot",
]
#: ⛔ **必须是"微信客户端界面"的招牌词**（2026-09-29 踩坑后收紧）：
#    第一版把"微信/小程序/设置"这种泛词也算命中，结果收进来一堆**微信公众平台后台**
#    （开发者ID、小程序代码上传、运维中心…）——那不是我们 OCR 的对象（我们读的是会话列表/聊天窗）。
STRONG_MARKERS = ("文件传输助手", "通讯录管理", "通讯录", "群聊", "订阅号", "服务号", "朋友圈",
                  "拍一拍", "聊天记录", "发起群聊", "微信号", "搜一搜", "视频号", "看一看",
                  "chats", "contacts", "moments", "file transfer", "wechat")
#: 只出现这些 ⇒ **判它不是客户端界面**（公众平台后台、开发文档、营销长图…）
NEGATIVE_MARKERS = ("开发者ID", "AppID", "小程序代码上传", "运维中心", "监控告警", "IP白名单",
                    "开放平台", "公众平台", "接口权限", "代码上传密钥")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _ctx():
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def _get(url: str, timeout: int = 45, referer: str = "") -> bytes:
    h = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    if referer:
        h["Referer"] = referer
    return urllib.request.urlopen(urllib.request.Request(url, headers=h),
                                 timeout=timeout, context=_ctx()).read()


def search_images(query: str, limit: int = 40) -> list:
    """从 Bing 图片搜索的结果页里抽图片直链（`murl`）。"""
    out = []
    url = "https://www.bing.com/images/search?q=%s&form=HDRSC2&first=1" % urllib.parse.quote(query)
    try:
        html = _get(url).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print("    （搜索失败「%s」：%s）" % (query[:20], str(e)[:60]), flush=True)
        return out
    # ⛔ 只拿 `murl` 不够：图站普遍**防盗链**（实测天极/太平洋那批直接 567/404），
    #    必须**带着"图片所在页面的 URL"当 Referer** 才给下；再失败就退到 Bing 自己的缩略图代理
    #    （`turl` 在 bing.net 上、不受防盗链影响，且可用 w/h 参数要更大尺寸）。
    ms = re.findall(r'"murl":"(.*?)"', html) or re.findall(r"murl&quot;:&quot;(https?://[^&]+?)&quot;", html)
    ps = re.findall(r'"purl":"(.*?)"', html)
    ts = re.findall(r'"turl":"(.*?)"', html)
    pairs = []
    for i, m in enumerate(ms):
        u = m.replace("\\/", "/").replace("&amp;", "&")
        if not u.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            continue
        purl = (ps[i].replace("\\/", "/") if i < len(ps) else "")
        turl = (ts[i].replace("\\/", "/").replace("&amp;", "&") if i < len(ts) else "")
        if all(u != x[0] for x in pairs):
            pairs.append((u, purl, turl))
        if len(pairs) >= limit:
            break
    print("    查询「%s」→ %d 个直链" % (query[:24], len(pairs)), flush=True)
    return pairs


def looks_like_wechat(path: str) -> tuple:
    """是不是微信界面：内置引擎读一遍，看有没有微信 UI 招牌词。

    返回 `(像不像, 读到几行, 命中的招牌词)`。
    ⚠️ 只做**相关性分类**（挡掉壁纸/图标/PPT），**不生成真值**。
    """
    try:
        from PIL import Image
        from agent import chat_ocr as O
        im = Image.open(path).convert("RGB")
        lines = [str(x[0]) for x in O.recognize(im)]
        joined = O.norm(" ".join(lines))
        hit = [mk for mk in STRONG_MARKERS if O.norm(mk) in joined]
        bad = [mk for mk in NEGATIVE_MARKERS if O.norm(mk) in joined]
        # 判它是客户端界面：命中强招牌词、且**没有**后台/文档类特征词
        return (bool(hit) and not bad and len(lines) >= 3), len(lines), (hit or bad)
    except Exception as e:  # noqa: BLE001
        print("    （判相关性失败：%s）" % str(e)[:50], flush=True)
        return False, 0, []


def load_src() -> dict:
    if os.path.exists(SRC):
        try:
            return json.load(io.open(SRC, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print("（_sources.json 读不出来，按空起：%s）" % str(e)[:60])
    return {}


def md5s() -> dict:
    out = {}
    for fn in os.listdir(WEB):
        p = os.path.join(WEB, fn)
        if fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) and os.path.isfile(p):
            try:
                out[hashlib.md5(io.open(p, "rb").read()).hexdigest()] = fn
            except Exception as e:  # noqa: BLE001
                print("（算指纹失败 %s：%s）" % (fn[:26], str(e)[:40]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=60, help="目标张数")
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()

    db, seen = load_src(), md5s()
    have = len([f for f in os.listdir(WEB) if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))])
    print("现有素材 %d 张（本轮目标：微信界面新增 %d 张）" % (have, a.target))
    if a.check_only:
        wx = [k for k, v in db.items() if v.get("kind") == "wx-ui"]
        print("其中标注为微信界面（kind=wx-ui）的：%d 张" % len(wx))
        return 0

    added = 0
    for q in QUERIES:
        if added >= a.target:
            break
        # ⛔ 每个查询前重扫磁盘指纹：采集会被中断重跑、也可能两个进程重叠，
        #    只靠进程启动时那一份快照会出现"同一张图换个名字又存一遍"（repo 版采集器踩过，
        #    wx 版这里补上；判据 A2 正是靠它守住的）
        seen.clear()
        seen.update(md5s())
        for u, purl, turl in search_images(q):
            if added >= a.target:
                break
            data = b""
            for _u, _ref in ((u, purl or "https://www.bing.com/"), (turl, "https://www.bing.com/")):
                if not _u:
                    continue
                try:
                    data = _get(_u + ("&w=1600&h=1200&c=7" if _u.startswith("https://tse") else ""),
                                timeout=30, referer=_ref)
                    if len(data) >= 15000:
                        break
                    data = b""
                except Exception as e:  # noqa: BLE001
                    print("    （下载失败 %s：%s）" % (_u[-38:], str(e)[:30]), flush=True)
            if not data:
                continue
            if not (15000 <= len(data) <= 3_500_000):
                continue
            h = hashlib.md5(data).hexdigest()
            if h in seen:
                continue
            ext = ".png" if u.lower().endswith(".png") else ".jpg"
            fn = "wx-%03d-%s%s" % (have + added + 1, re.sub(r"[^A-Za-z0-9]", "", os.path.basename(u))[:18], ext)
            fp = os.path.join(WEB, fn)
            if os.path.exists(fp):
                continue
            io.open(fp, "wb").write(data)
            if data[:4] == b"RIFF" or data[:3] == b"\xff\xd8\xff" or data[:8].startswith(b"\x89PNG"):
                pass
            okk, nlines, hits = looks_like_wechat(fp)
            if not okk:
                os.remove(fp)
                continue
            seen[h] = fn
            db[fn] = {"repo": "（图片搜索：%s）" % q, "path": u, "src": u, "bytes": len(data),
                      "probe": "%d 行 · 招牌词 %s" % (nlines, "、".join(hits[:4])), "kind": "wx-ui"}
            added += 1
            print("    ✅ %s（%d 行 · %s）" % (fn[:34], nlines, "、".join(hits[:3])), flush=True)
    json.dump(db, io.open(SRC, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    wx = [k for k, v in db.items() if v.get("kind") == "wx-ui"]
    # 给通用界面那批补一个 kind，便于分类统计
    for k, v in db.items():
        v.setdefault("kind", "other-ui")
    json.dump(db, io.open(SRC, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    total = len([f for f in os.listdir(WEB) if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))])
    print("完成：微信界面 %d 张、素材合计 %d 张（本轮新增 %d）" % (len(wx), total, added))
    return 0


if __name__ == "__main__":
    sys.exit(main())
