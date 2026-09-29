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
import json
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
#: **本机真实截图**语料（由 `scripts/ocr_corpus_local.py` 采入；只读用户目录、写副本到这里）
LOCAL = os.path.abspath(os.path.join(os.path.dirname(WEB), "local"))

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
    # ⛔ 真值修正（2026-09-29，看原图核过）：这条原来写着 `Ratio` —— **图里根本没有 Ratio**
    #    （右上只有 Uploaded: / Downloaded: / State: / Running Time: / Remaining Time: /
    #      Last Activity: / Availability: …）。那是早前**从缩略总览抄错**的真值，
    #    和本文件文档串里记的那次审计事故同一类 ⇒ 换成图上确凿可见的几条。
    ("gh-024-transmission-WI-main-screen.png", "No Selection", "Uploaded", "Downloaded",
     "Running Time", "Remaining Time", "Availability", "1 Transfer"),
    ("gh-001-PowerToys-pt_run_ui.png", "Maps", "Magnify", "Messaging", "Packaged application"),
    # 2026-09-29 新入库（看原图四象限逐条核过）
    ("gh-036-keepassxc-autoopen.png", "AutoOpen", "Database 1", "/keyfile.key", "/autoopen.kdbx",
     "Expiration", "Search (Ctrl+F)"),
]

#: 素材库目标张数（用户要求扩到 100 张）
TARGET_IMAGES = 100
#: 识别率标准（用户把标准提到 99%）——这是**目标**，不是"现在已经达到"
TARGET_RECALL = 0.99
#: 素材分类：`wx-ui`＝微信界面（**这才是我们要量的**：OCR 就是读微信界面的——左侧会话列表、
#: 聊天标题栏、消息、按钮），`other-ui`＝其他软件界面（用来防"只在自己那一类上过拟合"）。
#: 分类来自 `_sources.json` 的 `kind` 字段（采集时写入）。
KIND_WX = "wx-ui"
KIND_OTHER = "other-ui"
#: **本机真实截图**（用户自己机器上的画面）：与网图**分开统计**。理由：网图的字体渲染、DPI、
#: 窗口边框、输入法候选框都不等于用户机器上的真实画面 ⇒ 拿网图算出的成功率**不能**说明
#: "在这台机器上能不能认出来"。这一类的 `_sources.json` 在 `local/` 下（见 `ocr_corpus_local.py`）。
KIND_LOCAL = "local-real"
#: 棘轮（**分档记**）：总召回 + 微信界面专项召回，各自只许涨不许跌。
BEST_RECALL_WX = 0.0
#: **硬读档参数**（写在这里＝可复现；改它就要重测并更新棘轮）
#: ⭐ 2026-09-29 消融后定的两档（**热态**实测，6 张 tier-1 图 / 31 条真值）：
#:   · 基础档 recognize_dual：23/31 = 74.2%，0.22s/张
#:   · 快速档（纯内置 1x+2x+2x锐化）：26/31 = 83.9%，1.59s/张
#:   · 全对档（快速档 + 第二引擎 1x）：**31/31 = 100%**，4.47s/张（旧"全开"20.5s/张 ⇒ 快 4.6×）
#:   为什么全对档降不下来：那 5 条真值**只有第二引擎读得出**（内置九种预处理全试过也只有 26/31），
#:   而第二引擎单张就有 ≈1s 固定开销（裁成 400×40 仍要 1.10s ⇒ 裁 ROI 省不掉）；分块并行实测也无收益。
#: ⛔ `BOOST_HEAVY=False` 是**故意的**：第二引擎跑 2x 是耗时的全部来源（6s/张）且这批图零增益。
BOOST_SCALES = (1, 2)
BOOST_INVERT = True
BOOST_HEAVY = False
BOOST_DESC = "全对档：内置多档 + 第二引擎1x"
#: 快速档（纯内置，不叫第二引擎）——给"先试一遍/不关键"的场景；两档都量，数字都列出来
BOOST_FAST_DESC = "快速档：纯内置 1x+2x+2x锐化"
#: 对照用的"全开"档（含第二引擎 2x）——只在需要复现旧数字时打开，不进棘轮
BOOST_DESC_OLD = "全开：两引擎×两尺度×反色"


#: 棘轮（只许涨不许跌）。每次实测后**只往上改**，并记下当时的口径。
#: ⭐ 2026-09-29 实测（6 张图 / 31 条核过真值）：
#:   · 基础档 23/31 = **74.2%**（`BEST_RECALL_BASE`）
#:   · **硬读档 31/31 = 100.0%**（`BEST_RECALL`，**验收标准按这一档**）
#:   · 代价：硬读档 123s / 6 张 ≈ 20s 一张（基础档 1.2s）⇒ 它是"难读时加档"，不进每笔发送链。
BEST_RECALL = 1.0
#: 基础档的历史最好读数（同样只许涨）：它是**每笔操作实际付的代价**那一档，退化同样要报警。
BEST_RECALL_BASE = 0.742


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
    have = _count_imgs(WEB) + _count_imgs(LOCAL)
    print("   素材：现有 %d 张 / 目标 %d 张（网图 %d + 本机真实截图 %d）" % (
        have, TARGET_IMAGES, _count_imgs(WEB), _count_imgs(LOCAL)))
    # ⭐ 预热：第二引擎首次调用要**加载 ONNX 模型**（实测 20~30s）⇒ 不预热的话第一档会把这一笔
    #    算进去、看起来像"每张图都慢"。预热后报的数字才是**热态**的真实代价（冷启只付一次）。
    try:
        _t0 = time.time()
        O.recognize_union(Image.new("RGB", (320, 80), (250, 250, 250)))
        print("   （预热第二引擎：%.1fs，只付一次；下面的耗时都是热态）" % (time.time() - _t0))
    except Exception as e:  # noqa: BLE001
        print("   （预热失败，耗时含冷启：%s）" % str(e)[:50])
    # tier-1：**两个档位都量**（基础档＝每笔操作的常用路径；硬读档＝难读时加档，我们要的验收标准按它）
    for _tag, _reader in (("基础档（recognize_dual）", lambda i: O.recognize_dual(i)),
                          ("快速档（%s）" % BOOST_FAST_DESC,
                           lambda i: O.recognize_boost(i, scales=BOOST_SCALES, invert=BOOST_INVERT,
                                                       profile="fast")),
                          ("硬读档（%s）" % BOOST_DESC,
                           lambda i: O.recognize_boost(i, scales=BOOST_SCALES, invert=BOOST_INVERT,
                                                       heavy=BOOST_HEAVY))):
        ok = n = 0
        t0 = time.time()
        for name, *truths in CASES:
            path = _path_of(name)
            if not os.path.exists(path):
                continue
            lines = [str(x[0]) for x in _reader(Image.open(path).convert("RGB"))]
            hits = [t for t in truths if any(O.norm(t) in O.norm(ln) for ln in lines)]
            ok += len(hits)
            n += len(truths)
            if verbose:
                miss = [t for t in truths if t not in hits]
                print("   %-38s %-18s %d/%d%s" % (name[:38], _tag[:18], len(hits), len(truths),
                                                  ("（漏：%s）" % "、".join(miss)[:44]) if miss else ""))
        _r = ok / max(1, n)
        print("   tier-1【%s】：%d/%d = %5.1f%%（%.1fs）" % (_tag, ok, n, 100 * _r, time.time() - t0))
        if _tag.startswith("硬读档"):
            r1 = _r
    print("   ⇒ 标准按硬读档算（基础档数字同时列在上面，方便看「加档买到了多少」）")
    # tier-2：⛔ 这里**不报召回率**（口径见下面那段注释）。真值本身就是"两个引擎都读到了"
    # ⇒ 拿任一引擎单独去召回它必然 ≈100%（自证式读数，零信息量，第一版就写错成这样）。
    # 只统计"引擎差异"，那才是真正有用的信息（谁多读出了什么）。
    cons = consensus_truths()
    n2 = 0
    hit = {"both": 0, "only_winrt": 0, "only_rapid": 0, "neither": 0}
    for fn, truths in cons.items():
        im = Image.open(os.path.join(WEB, fn)).convert("RGB")
        try:
            a = [str(x[0]) for x in O.recognize(im)]
        except Exception:  # noqa: BLE001
            a = []
        try:
            b = [str(x[0]) for x in O._rapid_recognize(im)]
        except Exception:  # noqa: BLE001
            b = []
        for tr in truths:
            n2 += 1
            _wa = any(O.norm(tr) in O.norm(x) for x in a)
            _wb = any(O.norm(tr) in O.norm(x) for x in b)
            if _wa and _wb:
                hit["both"] += 1
            elif _wa:
                hit["only_winrt"] += 1
            elif _wb:
                hit["only_rapid"] += 1
            else:
                hit["neither"] += 1
    # 一致性覆盖率（**不是召回率**，见上面口径说明）：两引擎至少一方读到的占比
    r2 = (hit["both"] + hit["only_winrt"] + hit["only_rapid"]) / max(1, n2)
    # ⛔ 口径（必须写清，否则又是自证式读数）：tier-2 的真值**本来就是"两个引擎都读到了"**
    #    ⇒ 拿任一引擎单独去召回它必然≈100%（零信息量）。所以这里**不报召回率**，
    #    只报"引擎差异"：两引擎都读到 / 只有内置读到 / 只有第二引擎读到 / 两引擎都没读到。
    #    这才是"引擎对比"真正有用的信息（谁多读出了什么）。
    print("   tier-2 口径说明：一致性真值由两引擎共同产生 ⇒ **不能用来量召回**（自证式）；"
          "这里只报引擎差异")
    print("   tier-2 引擎差异（%d 张 / %d 条）：两引擎都读到 %d · 只有内置 %d · 只有第二引擎 %d · 都没读到 %d" % (
        len(cons), n2, hit["both"], hit["only_winrt"], hit["only_rapid"], hit["neither"]))
    # ── 按类别分开看：**微信界面**才是我们真正要量的东西 ──────────────────
    kinds = _kinds()
    wx = [c for c in CASES if kinds.get(c[0]) == KIND_WX]
    ot = [c for c in CASES if kinds.get(c[0]) == KIND_OTHER]
    lo = [c for c in CASES if kinds.get(c[0]) == KIND_LOCAL]
    print("   素材构成：微信界面 %d 张（%s）· 其他界面 %d 张（%s）" % (
        len(wx), ", ".join(c[0][:22] for c in wx[:3]), len(ot), ", ".join(c[0][:22] for c in ot[:3])))
    if lo:
        print("             本机真实截图 %d 张（%s）" % (len(lo), ", ".join(c[0][:24] for c in lo[:3])))
    for tag, group in (("微信界面", wx), ("其他界面", ot), ("本机真实截图", lo)):
        if not group:
            continue
        k = n3 = 0
        for name, *truths in group:
            path = _path_of(name)
            if not os.path.exists(path):
                continue
            lines = [str(x[0]) for x in O.recognize_dual(Image.open(path).convert("RGB"))]
            k += sum(1 for t in truths if any(O.norm(t) in O.norm(ln) for ln in lines))
            n3 += len(truths)
        print("   【%s】核过真值 %d/%d = %5.1f%%" % (tag, k, n3, 100.0 * k / max(1, n3)))
    if BEST_RECALL_WX > 0 and wx:
        _k = _n = 0
        for name, *truths in wx:
            _p = _path_of(name)
            if not os.path.exists(_p):
                continue
            _lines = [str(x[0]) for x in O.recognize_dual(Image.open(_p).convert("RGB"))]
            _k += sum(1 for t in truths if any(O.norm(t) in O.norm(ln) for ln in _lines))
            _n += len(truths)
        print("   棘轮（微信界面）：历史最好 %5.1f%% ⇒ %s" % (
            BEST_RECALL_WX * 100, "没退" if _k / max(1, _n) >= BEST_RECALL_WX - 1e-9 else "⚠️ 退了！"))
    # ⛔ 这两行原来被写死在 `_kinds()` 的 `return` 之后 ⇒ **永远执行不到**（死代码）。
    #    修 `_kinds` 时一并挪回这里（它本来就属于"量完之后的结论"）。
    print("   差距：tier-1 距标准还差 %5.1f 个百分点" % ((TARGET_RECALL - r1) * 100))
    if BEST_RECALL > 0:
        print("   棘轮：历史最好 %5.1f%% ⇒ %s" % (
            BEST_RECALL * 100, "没退" if r1 >= BEST_RECALL - 1e-9 else "⚠️ 退了！"))
    return r1, r2


def _path_of(name: str) -> str:
    """素材实际路径：`web/` 与 `local/` 两处都找（真实截图与网图混在同一份 CASES 里）。"""
    p = os.path.join(WEB, name)
    if os.path.exists(p):
        return p
    return os.path.join(LOCAL, name)


def _count_imgs(d: str) -> int:
    """数一个目录里的图片张数。

    ⛔ 这里**不能**用「读失败就当 0」的静默兜底（`except: return 0`）——那正是
       「目录读不到」与「目录里真没图」被混成同一个结论：前者是**量具坏了**（该报错让人知道
       目录/权限有问题），后者才该是 0。两者在"素材够不够"的口径下含义相反。
       ⇒ 目录不存在 = 明说的 0（这是**预期**情况：还没采集过）；其它 `OSError` 一律**抛**上去。
    """
    if not os.path.isdir(d):
        return 0
    return len([f for f in os.listdir(d) if f.lower().endswith((".png", ".jpg", ".jpeg"))])


def _kinds() -> dict:
    """读 `_sources.json` 的 `kind` 字段（没标的一律当"其他界面"，不猜）。

    ⛔ 注意：本函数**同时**要把 `local/` 语料（本机真实截图）并进来 —— 它们各有自己的
       `_sources.json`（`kind="local-real"`）⇒ 两处都读，别只读 `web/`（否则真实截图
       进了目录却永远不进任何统计口径，等于白采）。
    """
    out = {}
    for _d, _fn in ((WEB, "_sources.json"), (LOCAL, "_sources.json")):
        try:
            db = json.load(open(os.path.join(_d, _fn), encoding="utf-8"))
            for k, v in (db or {}).items():
                out[k] = str((v or {}).get("kind") or KIND_OTHER)
        except Exception as e:  # noqa: BLE001
            print("   （读素材分类失败 %s，该目录按其他界面算：%s）" % (_fn, str(e)[:50]))
    return out


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
        path = _path_of(name)
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
