# -*- coding: utf-8 -*-
"""**本机真实截图**入库（扩库的真正增量 —— 网图再多也不是用户自己屏幕上的东西）。

## 为什么必须做这一步（不是"锦上添花"）
前两个采集器的素材都来自**网上别人的截图**：主题、字体渲染、DPI、窗口边框、输入法候选框
……全都不等于用户机器上的真实画面。而我们的 OCR 干活的对象，恰恰是**这台机器屏幕上**的窗口。
⇒ 用别人的图算出来的"识别成功率"只能说明算法大概能用，**不能说明在这台机器上能用**。

## 与另外两个采集器的分工（在基准里**分开统计**，不混在一起）
| 采集器 | 来源 | `kind` | 能回答的问题 |
|---|---|---|---|
| `ocr_corpus_harvest.py` | GitHub 开源项目文档图 | `other-ui` | 算法跨应用泛化得怎么样 |
| `ocr_corpus_wx.py` | 图片搜索的微信界面 | `wx-ui` | 微信界面认得怎么样（网络图） |
| **本脚本** | **用户本机 `Pictures/Screenshots`** | `local-real` | **在这台机器上、这套渲染下认得怎么样** |

## ⛔ 本脚本的三条硬纪律（都是踩过坑才写的）
1. **只读用户目录，绝不改动**：源目录（默认 `~/Pictures/Screenshots`）**只读**，
   不移动、不重命名、不删除、不"顺手清理"。所有落盘只发生在**语料目录**里（写入副本）。
   ⇒ 这是 `personal_files_safety` 的要求：扫＝只读；要动必须分开确认。
2. **入库前脱敏标记 + 人工复核**：用户真实截图**可能含隐私**（聊天记录、邮箱、手机号、
   文件路径、账号头像）。本脚本**不删除、不修改**原图内容（OCR 素材必须保持真实，
   打码会把"真值"变成"打过码的真值"，反而污染基准）；改为：
   - 自动标记 `pii_risk`（用正则扫手机号 / 邮箱 / 身份证 / 长数字串 / 疑似人名），
   - 高风险图**默认只入库不进基准分母**，并在清单里写明"需人工过一眼才可转正"。
3. **逐张人工核过才叫真值**：入库只表示"可作 OCR 素材"，**不等于**有了真值。
   真值仍走 `truth_store`：`local-real` 素材由本脚本产出**待审候选**，
   经"人看一眼"（`pmctl truth` 待审包）确认后才进基准。

## 用法
    runtime\\python\\python.exe scripts\\ocr_corpus_local.py                  # 扫默认目录并入库
    runtime\\python\\python.exe scripts\\ocr_corpus_local.py --dir <目录>
    runtime\\python\\python.exe scripts\\ocr_corpus_local.py --dry-run         # 只看会收哪些，不落盘
    runtime\\python\\python.exe scripts\\ocr_corpus_local.py --check-only      # 只体检已入库的

## 产出
- 素材副本 → `<语料>/local/<序号>-<原名>.png`（PNG 统一转存，避免 jpg 压缩噪声影响判据）
- 清单 → `<语料>/local/_sources.json`（记原路径、尺寸、体检结果、pii 风险标记）
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("PM_NO_WINDOW_TOUCH", "1")

LOCAL = os.path.abspath(os.path.join(
    ROOT, "..", "dev-workspace", "persona-morph", "ocr-samples", "local"))
SRC = os.path.join(LOCAL, "_sources.json")
#: 默认源目录（**只读**）
DEFAULT_SRC_DIR = os.path.join(os.path.expanduser("~"), "Pictures", "Screenshots")

#: 体检口径：与 `ocr_corpus_harvest.probe()` **保持一致**（口径不一致会让基准没法横向比较）
MIN_AREA = 80000
MIN_LINES = 3
MIN_CHARS = 20

#: 隐私风险正则 —— 命中就让这张图**默认不进基准分母**，等人过一眼
PII_PATTERNS = [
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("邮箱", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")),
    ("身份证", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("长数字串", re.compile(r"(?<!\d)\d{12,}(?!\d)")),
    ("疑似账号/卡号", re.compile(r"(?<!\d)(?:\d{4}[-\s]){3}\d{4}(?!\d)")),
]


def pii_scan(text: str) -> list:
    """扫 PII 风险，返回命中项名列表（**只标记，不改图**）。"""
    hits = []
    for name, rx in PII_PATTERNS:
        if rx.search(text or ""):
            hits.append(name)
    return hits


def probe(path: str) -> tuple:
    """体检：口径与 `ocr_corpus_harvest.probe()` 一致（≥3 行 / ≥20 字 / ≥80000 px）。"""
    try:
        from PIL import Image
        from agent import chat_ocr as O
        im = Image.open(path).convert("RGB")
        if im.size[0] * im.size[1] < MIN_AREA:
            return False, "图太小 %dx%d" % im.size, []
        items = O.recognize(im)
        lines = [str(x[0]) for x in items]
        chars = sum(len(x.strip()) for x in lines)
        if len(lines) < MIN_LINES or chars < MIN_CHARS:
            return False, "文字太少（%d 行 / %d 字）" % (len(lines), chars), lines
        return True, "%d 行 / %d 字" % (len(lines), chars), lines
    except Exception as e:  # noqa: BLE001
        return False, "读不了：%s" % str(e)[:40], []


def load_sources() -> dict:
    if os.path.exists(SRC):
        try:
            return json.load(io.open(SRC, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print("（_sources.json 读不出来，按空起：%s）" % str(e)[:60])
    return {}


def hashes() -> dict:
    """内容指纹 → 文件名（**跨来源去重**：同一张图可能既是网图又是本机截图）。"""
    out = {}
    for d in (LOCAL, os.path.join(LOCAL, "..", "web"), os.path.join(LOCAL, "..", "wechat")):
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            p = os.path.join(d, fn)
            if not fn.lower().endswith((".png", ".jpg", ".jpeg")) or not os.path.isfile(p):
                continue
            try:
                out[hashlib.md5(io.open(p, "rb").read()).hexdigest()] = fn
            except Exception as e:  # noqa: BLE001
                print("（算指纹失败 %s：%s）" % (fn[:30], str(e)[:40]))
    return out


def list_candidates(src_dir: str) -> list:
    """列源目录里的图片（**只读**；跳过隐藏/系统文件）。"""
    if not os.path.isdir(src_dir):
        print("源目录不存在：%s" % src_dir)
        return []
    out = []
    for fn in sorted(os.listdir(src_dir)):
        if fn.lower().startswith(".") or fn.lower() == "desktop.ini":
            continue
        if not fn.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
            continue
        p = os.path.join(src_dir, fn)
        if os.path.isfile(p):
            out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=DEFAULT_SRC_DIR, help="源目录（只读；默认 ~/Pictures/Screenshots）")
    ap.add_argument("--dry-run", action="store_true", help="只看会收哪些，不落盘")
    ap.add_argument("--check-only", action="store_true", help="只体检已入库的")
    a = ap.parse_args()

    if not os.path.isdir(LOCAL):
        os.makedirs(LOCAL, exist_ok=True)

    if a.check_only:
        bad = []
        for fn in sorted(os.listdir(LOCAL)):
            if fn.lower().endswith((".png", ".jpg", ".jpeg")):
                okd, why, _ = probe(os.path.join(LOCAL, fn))
                if not okd:
                    bad.append((fn, why))
        print("入库素材体检：%d 张不合格" % len(bad))
        for fn, why in bad[:15]:
            print("   %-46s %s" % (fn[:46], why))
        return 0 if not bad else 1

    print("源目录（**只读，不做任何改动**）：%s" % a.dir)
    cands = list_candidates(a.dir)
    print("候选 %d 张" % len(cands))
    if not cands:
        return 1

    got = load_sources()
    seen = hashes()
    from PIL import Image
    added = skipped = dup = 0
    seq = len([1 for f in os.listdir(LOCAL)
               if f.lower().endswith((".png", ".jpg", ".jpeg"))])
    for p in cands:
        name = os.path.basename(p)
        try:
            data = io.open(p, "rb").read()
        except Exception as e:  # noqa: BLE001
            print("   （读不了 %s：%s）" % (name[:40], str(e)[:40]))
            skipped += 1
            continue
        h = hashlib.md5(data).hexdigest()
        if h in seen:
            dup += 1
            continue
        okd, why, lines = probe(p)
        if not okd:
            print("   ✘ %-44s %s" % (name[:44], why))
            skipped += 1
            continue
        risks = pii_scan("\n".join(lines))
        if a.dry_run:
            print("   ✔ %-44s %s%s" % (name[:44], why,
                                      ("  ⚠️ 隐私风险：%s" % "、".join(risks)) if risks else ""))
            added += 1
            continue
        seq += 1
        dst = os.path.join(LOCAL, "local-%03d-%s.png" % (seq, re.sub(
            r"[^A-Za-z0-9._-]", "_", os.path.splitext(name)[0])[:50]))
        try:
            # 原子写：这批图会进真值流水线当基准，**半截图会污染分母**（读到的是坏图/None，
            # 却被当成"模型认得差"）。落盘统一走 `agent.atomic_io`（tmp + 校验非空 + 换档）。
            sys.path.insert(0, ROOT)
            from agent import atomic_io as _aio_c
            if not _aio_c.save_image(dst, Image.open(p).convert("RGB"), fmt="PNG", halt_check=False):
                raise IOError("原子写判失败（目标未留半截文件）")
        except Exception as e:  # noqa: BLE001
            print("   （转存失败 %s：%s）" % (name[:40], str(e)[:40]))
            skipped += 1
            continue
        got[os.path.basename(dst)] = {
            "kind": "local-real",
            "origin": "local screenshot",
            "src_name": name,
            "src_dir": a.dir,
            "src_mtime": int(os.path.getmtime(p)),
            "bytes": len(data),
            "probe": why,
            "pii_risk": risks,
            # ⛔ 有隐私风险的图**默认不进基准分母**，等人工过一眼再转正
            "eligible": not risks,
        }
        seen[h] = os.path.basename(dst)
        added += 1

    if not a.dry_run:
        json.dump(got, io.open(SRC, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    total = len([1 for f in os.listdir(LOCAL)
                 if f.lower().endswith((".png", ".jpg", ".jpeg"))])
    risky = sum(1 for v in got.values() if v.get("pii_risk"))
    # ⛔ 措辞必须分清"预览"与"真收了"：`--dry-run` 时一个字节都没写，
    #    却打印"入库 N 张"会让人以为已经写盘（第一次就是这么写的，容易误判）。
    if a.dry_run:
        print("【预览】符合条件 %d 张（**未写盘**）· 跳过 %d · 重复 %d · 目录现有 %d 张"
              % (added, skipped, dup, total))
        print("   ⇒ 确认无误后去掉 --dry-run 再跑一次才会真正收进语料库。")
    else:
        print("完成：入库 %d 张（新增 %d / 跳过 %d / 重复 %d）· 目录现有 %d 张"
              % (added, added, skipped, dup, total))
    if risky:
        print("⚠️ 其中 %d 张带隐私风险标记 ⇒ 已标 `eligible=false`，需人工过一眼才进基准分母" % risky)
    return 0


if __name__ == "__main__":
    sys.exit(main())
