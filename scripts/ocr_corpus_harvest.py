# -*- coding: utf-8 -*-
"""网图素材库采集/体检脚本（**只用 GitHub 通道**，不碰用户素材）。

## 为什么是"采集 + 自动体检"两步
第一版我按文件名过滤就收，结果混进吉祥物、电路板照片、启动画屏 logo（10/30 张不合格）
⇒ 现在**每张下载后立刻体检**：内置引擎能读出 ≥3 行、文字总数 ≥20、面积 ≥80000 才入库。

## 用法
    runtime\\python\\python.exe scripts\\ocr_corpus_harvest.py            # 采到默认目标张数
    runtime\\python\\python.exe scripts\\ocr_corpus_harvest.py --target 100
    runtime\\python\\python.exe scripts\\ocr_corpus_harvest.py --check-only   # 只体检现有素材

## 网络可达性（实测，2026-09-29）
  · `raw.githubusercontent.com` ✅ 通；`api.github.com`（走 gh）✅ 通
  · Wikimedia Commons ❌ SSL 握手超时（被网络拦）
  · tavily ❌ 环境里没有 API Key

## ⛔ 真值口径（血泪）
素材入库 ≠ 真值可用。第一版真值是从**缩略总览**抄的，审计发现约 30% 记错
（图上 `17 results in 6 files` 我抄成 `13 results in 8 files`…）⇒ 那些真值永远不可能命中。
规矩：**要么看原图逐条核**（四象限原分辨率放大），**要么用"多引擎一致性"当自动真值**（并标明口径）。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("PM_NO_WINDOW_TOUCH", "1")

WEB = os.path.join(ROOT, "..", "dev-workspace", "persona-morph", "ocr-samples", "web")
WEB = os.path.abspath(WEB)
SRC = os.path.join(WEB, "_sources.json")
EXCLUDED = os.path.join(WEB, "_excluded")
#: 项目里的相对执行器（沿用既有做法：把 gh 调用统一走它，便于日志/开关集中）
RUNNER = os.path.join(ROOT, "..", "dev-workspace", "persona-morph", "root-probes", "_rel_run.py")
RUNNER = os.path.abspath(RUNNER)

#: 采哪些仓库（挑了"截图多 + 界面文字密集"的；含中文界面与深色主题）
REPOS = [
    # ⚠️ 顺序＝采集顺序。**把还没采过的排前面**（每轮都会重新列树，网络慢时先把新仓库吃下来）
    ("obsproject/obs-studio", 5), ("OpenShot/openshot-qt", 4), ("KiCad/kicad-source-mirror", 4),
    ("apache/echarts", 4), ("lyswhut/lx-music-desktop", 5), ("huiyadanli/RevokeMsgPatcher", 4),
    ("zbezj/HEU_KMS_Activator", 4), ("kingToolbox/WindTerm", 4), ("agalwood/Motrix", 4),
    ("c0re100/qBittorrent-Enhanced-Edition", 4), ("HandBrake/HandBrake", 4), ("mpv-player/mpv", 3),
    ("BleachBit/bleachbit", 3), ("ShareX/ShareX", 4), ("dnSpy/dnSpy", 3),
    ("microsoft/terminal", 4), ("cmderdev/cmder", 3), ("giuspen/cherrytree", 3),
    ("JabRef/jabref", 3), ("FreeCAD/FreeCAD", 4), ("LibreCAD/LibreCAD", 3),
    ("audacity/audacity", 3), ("blender/blender", 3), ("LibreOffice/core", 4),
    ("VSCodium/vscodium", 4), ("SerenityOS/serenity", 3),
    # 下面这些已经采过（保留着，跑完上面的才会轮到它们）
    ("microsoft/PowerToys", 2), ("keepassxreboot/keepassxc", 2), ("microsoft/vscode", 2),
    ("files-community/Files", 2), ("notepad-plus-plus/notepad-plus-plus", 2),
    ("SumatraPDFReader/sumatrapdf", 2), ("transmission/transmission", 2),
    ("PaddlePaddle/PaddleOCR", 2), ("RapidAI/RapidOCR", 2), ("qBittorrent/qBittorrent", 2),
]
#: 文件名/路径里带这些词的一律不要（图标、吉祥物、启动画屏、验证码、板子照片、横幅…）
BAD = re.compile(r"(icon|logo|sprite|avatar|badge|favicon|mascot|splash|captcha|circuit|"
                 r"board|hero|banner|cover|wallpaper|workflow|diagram|chart|graph|sticker|"
                 r"emoji|flag|theme_preview|screenshot_dark_\d)", re.I)
NAME_OK = re.compile(r"(screenshot|demo|preview|ui|shot|main|window|dialog|settings|preference|"
                     r"editor|interface|panel|list|dark|light|zh|cn|中文)", re.I)
DIR_OK = re.compile(r"(screenshots?|docs?/|images?|assets|demo|preview)", re.I)


def _ctx():
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def probe(path: str) -> tuple:
    """体检：返回 `(合格?, 说明)`。口径＝内置引擎能读出 ≥3 行、总字数 ≥20、面积够大。"""
    try:
        from PIL import Image
        from agent import chat_ocr as O
        im = Image.open(path).convert("RGB")
        if im.size[0] * im.size[1] < 80000:
            return False, "图太小 %dx%d" % im.size
        lines = [str(x[0]) for x in O.recognize(im)]
        chars = sum(len(x.strip()) for x in lines)
        if len(lines) < 3 or chars < 20:
            return False, "文字太少（%d 行 / %d 字）" % (len(lines), chars)
        return True, "%d 行 / %d 字" % (len(lines), chars)
    except Exception as e:  # noqa: BLE001
        return False, "读不了：%s" % str(e)[:40]


def load_sources() -> dict:
    if os.path.exists(SRC):
        try:
            return json.load(io.open(SRC, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print("（_sources.json 读不出来，按空起：%s）" % str(e)[:60])
    return {}


def hashes() -> dict:
    """内容指纹 → 文件（用于**去重**：同一张图换个名字再来一遍很常见）。"""
    out = {}
    for fn in os.listdir(WEB):
        p = os.path.join(WEB, fn)
        if not fn.lower().endswith((".png", ".jpg", ".jpeg")) or not os.path.isfile(p):
            continue
        try:
            out[hashlib.md5(io.open(p, "rb").read()).hexdigest()] = fn
        except Exception as e:  # noqa: BLE001
            print("（算指纹失败 %s：%s）" % (fn[:30], str(e)[:40]))
    return out


def tree(repo: str) -> list:
    """列仓库文件树 —— **走 Python urllib 直连 GitHub API**。

    ⛔ 两条都试过并撞墙（2026-09-29 实测，都写在这里省得下次再撞）：
      · `gh api`：未认证直接失败；即便有认证也受 **60 次/小时匿名速率限制**，大仓库递归列树还会卡很久；
      · `git clone`（含 `-c http.schannelCheckRevoke=false`）：Windows schannel 报
        `CRYPT_E_NO_REVOCATION_CHECK (0x80092012)`，**关开关也压不住**（换了层）。
    ✅ Python urllib 直连**一直是通的**（前几轮下载靠的就是它）⇒ 列树也用它。
       匿名 60 次/小时够跑一轮（30 个仓库左右）；撞到 403 就跳过这个仓库，不重试。
    """
    print("  列树 %s …" % repo, flush=True)
    url = "https://api.github.com/repos/%s/git/trees/HEAD?recursive=1" % repo
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ocr-corpus/1.0",
                                                   "Accept": "application/vnd.github+json"})
        raw = urllib.request.urlopen(req, timeout=45, context=_ctx()).read()
        obj = json.loads(raw.decode("utf-8", "replace"))
        return [str(x.get("path")) for x in (obj.get("tree") or []) if x.get("path")]
    except Exception as e:  # noqa: BLE001
        print("  列树失败，跳过 %s：%s" % (repo, str(e)[:70]))
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=100, help="目标张数（默认 100）")
    ap.add_argument("--check-only", action="store_true", help="只体检现有素材，不下载")
    a = ap.parse_args()

    got = load_sources()
    seen = hashes()
    have = len([1 for f in os.listdir(WEB)
                if f.lower().endswith((".png", ".jpg", ".jpeg"))])
    print("现有可用 %d 张（目标 %d）" % (have, a.target))

    if a.check_only:
        bad = []
        for fn in sorted(os.listdir(WEB)):
            if not fn.lower().endswith((".png", ".jpg", ".jpeg")):
                continue
            okd, why = probe(os.path.join(WEB, fn))
            if not okd:
                bad.append((fn, why))
        print("体检结果：%d 张不合格" % len(bad))
        for fn, why in bad[:15]:
            print("   %-46s %s" % (fn[:46], why))
        return 0 if not bad else 1

    added = 0
    for repo, want in REPOS:
        if have + added >= a.target or not want:
            break
        # ⛔ 每个仓库开始前**重扫一遍磁盘指纹**：采集可能被中断重跑、甚至两个进程重叠，
        #    只靠进程启动时那一份快照会出现"同一张图换个名字又存一遍"（实测踩过 7 张）
        seen.clear()
        seen.update(hashes())
        paths = tree(repo)
        if not paths:
            continue
        cand = [p for p in paths
                if p.lower().endswith((".png", ".jpg", ".jpeg"))
                and not BAD.search(p)
                and (NAME_OK.search(os.path.basename(p)) or DIR_OK.search(os.path.dirname(p) or ""))
                and 3 < len(p) < 170]
        kept = 0
        for p in sorted(cand, key=lambda x: (len(x), x)):
            if kept >= want or have + added >= a.target:
                break
            url = "https://raw.githubusercontent.com/%s/HEAD/%s" % (repo, p)
            try:
                data = urllib.request.urlopen(urllib.request.Request(
                    url, headers={"User-Agent": "ocr-corpus/1.0"}), timeout=60, context=_ctx()).read()
            except Exception as e:  # noqa: BLE001
                # 单个 URL 下载失败很正常（403/超时/被墙）⇒ 跳过，但要留痕：
                # 整轮"一张都收不到"时，靠这行才看得出是网络问题而不是筛选问题
                print("    （下载失败 %s：%s）" % (os.path.basename(p)[:30], str(e)[:50]), flush=True)
                continue
            if not (15000 <= len(data) <= 3_500_000):
                continue
            h = hashlib.md5(data).hexdigest()
            if h in seen:
                continue
            fn = re.sub(r"[^A-Za-z0-9._-]", "_",
                        "gh-%03d-%s-%s" % (have + added + 1, repo.split("/")[-1][:14],
                                           os.path.basename(p)))[:80]
            fp = os.path.join(WEB, fn)
            if os.path.exists(fp):
                continue
            io.open(fp, "wb").write(data)
            okd, why = probe(fp)
            if not okd:
                os.remove(fp)
                continue
            seen[h] = fn
            got[fn] = {"repo": repo, "path": p, "src": url, "bytes": len(data), "probe": why}
            kept += 1
            added += 1
        print("  %-40s 收 %d 张（本轮累计 %d）" % (repo[:40], kept, added))
    json.dump(got, io.open(SRC, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    total = len([1 for f in os.listdir(WEB) if f.lower().endswith((".png", ".jpg", ".jpeg"))])
    print("完成：可用素材 %d 张（本轮新增 %d）" % (total, added))
    return 0 if total >= a.target else 1


if __name__ == "__main__":
    sys.exit(main())
