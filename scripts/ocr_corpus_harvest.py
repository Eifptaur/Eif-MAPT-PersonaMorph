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
import urllib.error
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


def tree(repo: str):
    """列仓库文件树 —— **走 Python urllib 直连 GitHub API**。

    ⛔ 两条都试过并撞墙（2026-09-29 实测，都写在这里省得下次再撞）：
      · `gh api`：未认证直接失败；即便有认证也受 **60 次/小时匿名速率限制**，大仓库递归列树还会卡很久；
      · `git clone`（含 `-c http.schannelCheckRevoke=false`）：Windows schannel 报
        `CRYPT_E_NO_REVOCATION_CHECK (0x80092012)`，**关开关也压不住**（换了层）。
    ✅ Python urllib 直连**一直是通的**（前几轮下载靠的就是它）⇒ 列树也用它。
       匿名 60 次/小时够跑一轮（30 个仓库左右）；撞到 403 就跳过这个仓库，不重试。

    ⛔⛔ 返回值语义修正（本轮，修一个"静默空转"真缺陷）：
      旧版任何异常都 `return []`，调用方 `if not paths: continue` —— **配额耗尽（403）
       和"这个仓库确实没图"变得完全一样**。实测踩过：`--target 100` 跑了 25 分钟、
       一张没收到（GitHub 匿名 60/小时已用光），整份仓库清单被静默跳过，**没有任何提示**。
      ⇒ 现在分三种返回：
        · `list`  —— 列树成功（可能为空，表示该仓库确实没图）；
        · `None`  —— **配额耗尽/被限流**：调用方必须**立即停机并明确告知**，不再往下空转；
        · `[]`    —— 其他异常（超时/单仓库失败）：跳过该仓库，但会打印原因。
    """
    print("  列树 %s …" % repo, flush=True)
    url = "https://api.github.com/repos/%s/git/trees/HEAD?recursive=1" % repo
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ocr-corpus/1.0",
                                                   "Accept": "application/vnd.github+json"})
        raw = urllib.request.urlopen(req, timeout=45, context=_ctx()).read()
        obj = json.loads(raw.decode("utf-8", "replace"))
        return [str(x.get("path")) for x in (obj.get("tree") or []) if x.get("path")]
    except urllib.error.HTTPError as e:  # noqa: PERF203
        # 403/429 ＝ 匿名配额耗尽（或二级限流）⇒ 与"仓库没图"**必须区分**，回报 None 让上层停机
        if int(getattr(e, "code", 0)) in (403, 429):
            print("  ⛔ 配额耗尽/被限流（HTTP %s）：%s" % (e.code, repo), flush=True)
            return None
        print("  列树失败，跳过 %s：HTTP %s" % (repo, getattr(e, "code", "?")), flush=True)
        return []
    except Exception as e:  # noqa: BLE001
        print("  列树失败，跳过 %s：%s" % (repo, str(e)[:70]), flush=True)
        return []


def backfill_sources() -> int:
    """给「磁盘有图、`_sources.json` 查不到」的孤儿图**补来源记录**（可追溯性修复）。

    为什么会缺：旧版只在 `main()` 最末写一次 `_sources.json`，中途被杀（配额停机 /
    Ctrl-C / 超时）就丢掉本轮全部来源记录（实测 17 张 gh-057..073 成了孤儿）。
    `ocr_corpus_selftest` 的 A4「每张素材都能查到来源」把它抓了出来。

    怎么补：文件名形如 `gh-<序号>-<repo末段截断>-<原名>`，`<repo末段截断>` 能对回 `REPOS`
    里的仓库（截断到 14 字符）⇒ 据此重建 `repo`，并把文件名里剩下的部分作为 `path` 的线索。
    ⛔ 只是"**可追溯**"级别，不是"完整原始 URL"：`path` 用文件名的原名段近似、`src` 标
       `recovered`（诚实标注来源是回填的，不是采集时记下的）。
    """
    got = load_sources()
    imgs = [f for f in sorted(os.listdir(WEB))
            if f.lower().endswith((".png", ".jpg", ".jpeg"))]
    # 文件名里的 repo 截断段 → 完整 repo（取 REPOS 里截断后同名者）
    _tok2repo = {}
    for _r, _ in REPOS:
        _tok2repo[_r.split("/")[-1][:14].lower()] = _r
    fixed = 0
    for fn in imgs:
        if fn in got:
            continue
        m = re.match(r"gh-\d+-(.+?)-(.+)$", fn)
        tok = (m.group(1) if m else "").lower()
        repo = _tok2repo.get(tok, "?/%s" % (m.group(1) if m else "unknown"))
        got[fn] = {"repo": repo, "path": (m.group(2) if m else fn),
                   "src": "recovered", "bytes": os.path.getsize(os.path.join(WEB, fn)),
                   "probe": "（回填：来源为按文件名重建，非采集时记录）"}
        fixed += 1
    json.dump(got, io.open(SRC, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("回填来源：补了 %d 张 · 清单现有 %d 条（磁盘图 %d 张）" % (fixed, len(got), len(imgs)))
    still = [f for f in imgs if f not in got]
    if still:
        print("  仍无来源：%s" % still[:10])
    return 0 if not still else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=100, help="目标张数（默认 100）")
    ap.add_argument("--check-only", action="store_true", help="只体检现有素材，不下载")
    ap.add_argument("--backfill-sources", action="store_true",
                    help="给缺来源记录的孤儿图补来源（可追溯性修复），不下载")
    a = ap.parse_args()

    if a.backfill_sources:
        return backfill_sources()

    got = load_sources()
    seen = hashes()
    have = len([1 for f in os.listdir(WEB)
                if f.lower().endswith((".png", ".jpg", ".jpeg"))])
    # ⛔ 目标口径修正（本轮）：`--target` 是**素材总数**（含本机真实截图），不是 web 单目录。
    #    原来只数 web ⇒ 已经有 16 张 `local/` 时还硬要 web 再凑够 100，
    #    "现有 72 / 目标 100" 这个数就永远对不上（72 = web 56 + local 16）。
    #    ⇒ 先把 `local/` 那批算进来，本轮只为**差额**采网图。
    _local = os.path.abspath(os.path.join(os.path.dirname(WEB), "local"))
    n_local = len([f for f in os.listdir(_local)
                   if f.lower().endswith((".png", ".jpg", ".jpeg"))]) if os.path.isdir(_local) else 0
    have_all = have + n_local
    print("现有可用 %d 张（网图 %d + 本机真实截图 %d）· 目标总数 %d · 还差 %d 张"
          % (have_all, have, n_local, a.target, max(0, a.target - have_all)))

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
    # ⛔ 停机条件按**总素材**算（`have_all + added`）：只要 web+local 够 100 就停，
    #    不再"web 单独够 100"（那会多采 16 张、且与已有 local 重复覆盖同一批界面类型）。
    for repo, want in REPOS:
        if have_all + added >= a.target or not want:
            break
        # ⛔ 每个仓库开始前**重扫一遍磁盘指纹**：采集可能被中断重跑、甚至两个进程重叠，
        #    只靠进程启动时那一份快照会出现"同一张图换个名字又存一遍"（实测踩过 7 张）
        seen.clear()
        seen.update(hashes())
        paths = tree(repo)
        # ⛔ `None` ＝ 配额耗尽/被限流：**不是**"这个仓库没图"⇒ 立即停机并明确告知，
        #    否则就是旧版那个"静默空转 25 分钟、一张没收到"的坑（见 `tree()` docstring）。
        if paths is None:
            print("\n⛔ 停机：GitHub 匿名配额已耗尽（60 次/小时）⇒ 本轮到此为止，不再空转。")
            print("   已采 %d 张（网图 %d → %d）。配额 1 小时后自动重置，届时重跑即可。"
                  % (have_all + added, have, have + added))
            print("   提示：想跳过等配额，可先 `--target 100` 之外的活儿（如体检/喂真值）先干。")
            break
        if not paths:
            continue
        cand = [p for p in paths
                if p.lower().endswith((".png", ".jpg", ".jpeg"))
                and not BAD.search(p)
                and (NAME_OK.search(os.path.basename(p)) or DIR_OK.search(os.path.dirname(p) or ""))
                and 3 < len(p) < 170]
        kept = 0
        for p in sorted(cand, key=lambda x: (len(x), x)):
            if kept >= want or have_all + added >= a.target:
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
            # ⛔ **增量落盘来源清单**：旧版只在 `main()` 最末 `json.dump` 一次 ⇒ 中途被杀
            #    （配额停机/手动 Ctrl-C/超时）会让本轮已入库的图**丢掉来源记录**，变成
            #    「磁盘有图、`_sources.json` 查不到」的孤儿（实测踩过 17 张 gh-057..073，
            #    被 `ocr_corpus_selftest` 的 A4「每张素材都能查到来源」抓出）。
            #    ⇒ 每收一张就写一次（便宜，且进程随时死都不会再产生孤儿）。
            json.dump(got, io.open(SRC, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("  %-40s 收 %d 张（本轮累计 %d）" % (repo[:40], kept, added))
    json.dump(got, io.open(SRC, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    total = len([1 for f in os.listdir(WEB) if f.lower().endswith((".png", ".jpg", ".jpeg"))])
    # 结账也按**总素材**（web + local）报，并与 OCR 基准的口径对齐（那边也是这么数的）。
    total_all = total + n_local
    print("完成：素材总数 %d 张（网图 %d + 本机真实截图 %d；本轮新增网图 %d）· 目标 %d"
          % (total_all, total, n_local, added, a.target))
    return 0 if total_all >= a.target else 1


if __name__ == "__main__":
    sys.exit(main())
