# -*- coding: utf-8 -*-
"""OCR 成功率基准 · **网上素材（不使用用户截图）**。

素材来源：GitHub 各开源项目的真实 UI 截图 —— 由 `ocr-samples/web/_sources.json` 记录（repo / 路径 / URL）。
采集方式：`gh api repos/<repo>/git/trees/HEAD?recursive=1` 列路径 → 过滤文件名/目录（screenshot|docs|images…）
→ `raw.githubusercontent.com` 直下（30 张 / 11 个不同项目 / 3.4 MB）。

真值：由我**逐张看图**给定（本文件 `CASES`）。第一批先标 6 张（文字最密的），其余按同一格式继续加。

度量：`recognize_dual`（生产路径）在**整图**上的**召回**——真值串是否出现在任意一行读数里。
（屏幕截图场景里"整图召回"就是我们的真实需求：界面上的名字/按钮/标签得先被读出来。）

用法：
    runtime\\python\\python.exe scripts\\ocr_bench_web.py
    runtime\\python\\python.exe scripts\\ocr_bench_web.py --verbose
"""
import argparse
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import Image # noqa: E402

from agent import chat_ocr as O # noqa: E402

WEB = os.path.join(os.path.dirname(ROOT), "dev-workspace", "persona-morph", "ocr-samples", "web")

#: `(素材文件名, 真值清单...)` —— 真值是我看着图逐个写下来的（可在 overview 拼图上复核）
CASES = [
    # ⛔ **真值必须"看原图逐条核过"**（2026-09-29 教训）：第一版真值是我从**缩略总览**上抄的，
    #    审计发现约 **30% 记错**（例：图上写 `17 results in 6 files`，我记成 `13 results in 8 files`；
    #    Files 图里根本没有 `Gallery`/`Recent`；KeePassXC 是 `Expiration` 不是 `Expires`）
    #    ⇒ 那些真值**永远不可能命中**，把成功率**低报**了。现在这一批是**四象限原分辨率放大后逐条读出来的**。
    ("gh-012-vscode-demo.png",
     "highlights.ts", "Microsoft Corporation", "17 results in 6 files",
     "Find All References", "Change All Occurrences"),
    ("gh-027-Files-FilesScreenshot.png", "Home", "Documents", "Recycle Bin", "17 items", "1 item selected"),
    ("gh-005-keepassxc-main_interface.png", "Sample Entry", "Advanced", "Expiration", "Searches and Tags"),
    ("gh-024-transmission-WI-main-screen.png", "No Selection", "Uploaded", "Downloaded", "Ratio", "1 Transfer"),
    ("gh-001-PowerToys-pt_run_ui.png", "Maps", "Magnify", "Messaging", "Packaged application"),
    # 2026-09-29 新入库（看原图四象限逐条核过）
    ("gh-036-keepassxc-autoopen.png", "AutoOpen", "Database 1", "/keyfile.key", "/autoopen.kdbx",
     "Expiration", "Search (Ctrl+F)"),
]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    if not O.available():
        print("⚠️ 内置 OCR 不可用")
        return 2
    print("网图基准（不使用用户素材）· 目录 %s" % WEB)
    ok = n = 0
    per = []
    for name, *truths in CASES:
        path = os.path.join(WEB, name)
        if not os.path.exists(path):
            print("（跳过：找不到 %s）" % name)
            continue
        im = Image.open(path).convert("RGB")
        t0 = time.time()
        lines = [str(x[0]) for x in O.recognize_dual(im)]
        dt = time.time() - t0
        hits = [t for t in truths if any(O.norm(t) in O.norm(ln) for ln in lines)]
        miss = [t for t in truths if t not in hits]
        ok += len(hits)
        n += len(truths)
        per.append((name, len(hits), len(truths), dt))
        print("  %-44s %d/%d · %4.1fs · %d 行%s" % (
            name[:44], len(hits), len(truths), dt, len(lines),
            "（漏：%s）" % "、".join(miss)[:60] if miss else ""))
        if a.verbose and miss:
            print("      读数样本：%s" % str(lines[:6])[:150])
    print("")
    print("==== 网图召回（%d 张 / %d 条真值）====" % (len(per), n))
    print("   合计 %d/%d = %5.1f%%" % (ok, n, 100.0 * ok / max(1, n)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
