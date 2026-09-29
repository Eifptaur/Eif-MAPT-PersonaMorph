# -*- coding: utf-8 -*-
"""OCR 成功率基准 · **网上素材（不使用用户截图）**。

素材来源：GitHub 各开源项目的真实 UI 截图 —— 由 `ocr-samples/web/_sources.json` 记录（repo / 路径 / URL）。
采集方式：`gh api repos/<repo>/git/trees/HEAD?recursive=1` 列路径 → 过滤文件名/目录（screenshot|docs|images…）
→ `raw.githubusercontent.com` 直下（30 张 / 11 个不同项目 / 3.4 MB）。

## 真值分两档（口径必须写清，2026-09-29 立的规矩）

**tier-1「核过」**（本文件 `CASES`）：**看原图逐条核过** —— 把图按四象限切到原分辨率再看，
只写"确凿看见的"串。它是**硬指标**，也用来做棘轮。
  ⛔ 血泪：第一版是从缩略总览抄的，审计发现约 30% 记错（图上 `17 results in 6 files` 抄成
    `13 results in 8 files`、Files 图里根本没有 `Gallery`/`Recent`…）⇒ 那些真值**永远不可能命中**，
    把成功率低报成 48.3%/65.5%。**不许再从缩略图抄真值。**

**tier-2「一致性」**（`consensus_truths()` 运行时算）：**多引擎（内置 WinRT + RapidOCR）互相印证**
才收的自动真值 —— 两个独立引擎读出同一串，几乎不可能是巧合。
  ⚠️ 它**只用于**引擎之间的横向对比（样本量大、覆盖全部素材），**不当硬指标**：
  两个引擎"一起错成同一个样"时它会把错当对（一致 ≠ 正确）。

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

#: 素材库目标张数（用户要求扩到 100 张）
TARGET_IMAGES = 100
#: 识别率标准（用户把标准提到 99%）——这是**目标**，不是"现在已经达到"
TARGET_RECALL = 0.99
#: 棘轮：历史最好读数（只许涨不许跌）。每次实测后**只往上改**。
#: 当前记录 = 2026-09-29 实测 tier-1 28/29 = 96.6%（当时 6 张图 / 23 条核过真值）。
BEST_RECALL = 0.966


def consensus_truths(min_engines: int = 2, max_len: int = 40):
    """**tier-2 自动真值**：多引擎对同一条字符串互相印证才收。

    ⚠️ 口径提醒：一致性 ≠ 正确（两个引擎可能一起错），**只用于**引擎横向对比，**不当硬指标**。
    好处是不用人工标注就能覆盖全部素材；代价是有乐观偏差，所以 tier-1 才是棘轮的尺子。
    """
    out = {}
    for fn in sorted(os.listdir(WEB)):
        if not fn.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        path = os.path.join(WEB, fn)
        try:
            im = Image.open(path).convert("RGB")
        except Exception as e:  # noqa: BLE001
            print("（跳过 %s：%s）" % (fn[:30], str(e)[:40]))
            continue
        seen = {}
        for tag, fn_read in (("winrt", lambda i: [str(x[0]) for x in O.recognize(i)]),
                             ("rapid", lambda i: [str(x[0]) for x in O._rapid_recognize(i)])):
            try:
                for ln in fn_read(im):
                    s = str(ln).strip()
                    if not s or len(s) > max_len:
                        continue
                    seen.setdefault(O.norm(s), [s, set()])[1].add(tag)
            except Exception as e:  # noqa: BLE001
                print("（%s 读 %s 失败：%s）" % (tag, fn[:24], str(e)[:40]))
        out[fn] = [v[0] for v in seen.values() if len(v[1]) >= min_engines]
    return out


def measure(verbose: bool = False):
    """按 `TARGET_RECALL` 的标准量一遍：tier-1（硬指标 + 棘轮）、tier-2（引擎对比）。

    返回 `(tier1_ratio, tier2_ratio)`。
    """
    print("\n==== 标准：识别率 %.0f%%（素材目标 %d 张）====" % (TARGET_RECALL * 100, TARGET_IMAGES))
    have = len([1 for f in os.listdir(WEB) if f.lower().endswith((".png", ".jpg", ".jpeg"))])
    print("   素材：现有 %d 张 / 目标 %d 张" % (have, TARGET_IMAGES))
    # tier-1
    ok = n = 0
    t0 = time.time()
    for name, *truths in CASES:
        path = os.path.join(WEB, name)
        if not os.path.exists(path):
            continue
        im = Image.open(path).convert("RGB")
        lines = [str(x[0]) for x in O.recognize_dual(im)]
        hits = [t for t in truths if any(O.norm(t) in O.norm(ln) for ln in lines)]
        ok += len(hits)
        n += len(truths)
        if verbose:
            miss = [t for t in truths if t not in hits]
            print("   %-42s %d/%d%s" % (name[:42], len(hits), len(truths),
                                        ("（漏：%s）" % "、".join(miss)[:50]) if miss else ""))
    r1 = ok / max(1, n)
    print("   tier-1（核过，硬指标）：%d/%d = %5.1f%%（%.1fs）" % (ok, n, 100 * r1, time.time() - t0))
    # tier-2
    cons = consensus_truths()
    ok2 = n2 = 0
    for fn, truths in cons.items():
        im = Image.open(os.path.join(WEB, fn)).convert("RGB")
        lines = [str(x[0]) for x in O.recognize_dual(im)]
        for t in truths:
            n2 += 1
            if any(O.norm(t) in O.norm(ln) for ln in lines):
                ok2 += 1
    r2 = ok2 / max(1, n2)
    print("   tier-2（多引擎一致性，%d 张 / %d 条；**只用于引擎对比**，不当硬指标）：%5.1f%%"
          % (len(cons), n2, 100 * r2))
    print("   差距：tier-1 距标准还差 %5.1f 个百分点" % ((TARGET_RECALL - r1) * 100))
    if BEST_RECALL > 0:
        print("   棘轮：历史最好 %5.1f%% ⇒ %s" % (
            BEST_RECALL * 100, "没退" if r1 >= BEST_RECALL - 1e-9 else "⚠️ 退了！"))
    return r1, r2


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
    print("==== 网图召回（%d 张 / %d 条核过真值）====" % (len(per), n))
    print("   合计 %d/%d = %5.1f%%" % (ok, n, 100.0 * ok / max(1, n)))
    measure(verbose=a.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
