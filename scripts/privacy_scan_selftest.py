# -*- coding: utf-8 -*-
"""隐私扫描闸门：成品文件里**不许出现开发叙事**（轮次标签 / 协作过程词 / 计划条目 / 审计编号）。

为什么要有它：包是要发出去的，而开发过程留下的"轮次/对面/审计编号"会一起发出去 ——
既泄露开发过程，也是用户读不懂的黑话。发布清单里原本只有"发之前手工检索一次"，
本闸门把它变成**每次全量自检都跑**的常驻口径。

⚠️ **口径收窄过一次，理由要记住**：原口径用 `r1[0-9]\\b` 抓轮次标签，但 `\\b` 对
`_exe_dir14` / `_after16` / `pm-r11-maxseen` 这类**标识符**无效 ⇒ 一次扫描报 59 处，
其中绝大多数是标识符假阳性（"修"它们＝去改产品变量名，纯误伤）。
⇒ 改成**轮次语境**：`(?:跨机|对面|丙[-－]?)\\s*r\\d+` 或 `r\\d+\\s*(?:实测|需求|根因|轮次|那次)`。

⛔ **属于功能记号、绝不许清**（本闸门的反向控制就钉这一条）：`r18`（内容分级关键词）、
`deepseek-r1`（模型名）、`r0`/`r2`（局部变量名）、`ang/r0`（渲染参数）——
把"分类任务"当"重写任务"做，正是本项目反复吃过的亏。
"""
import io
import os
import re
import subprocess
import sys

PASS, FAIL = [0], [0]


def ok(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("  [%s]" % detail if detail else ""))
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
    return bool(cond)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 开发叙事的四条族（收窄后的**轮次语境**口径，见文件头）
NARRATIVE = re.compile(
    r"用户实锤|用户裁定|用户点单|你眼里"
    r"|(?:跨机|对面|丙[-－]?)\s*r\d+"
    r"|r\d+\s*(?:实测|需求|根因|轮次|那次)"
    r"|audit-r\d+"
    r"|commit [0-9a-f]{6}"
)
#: 白名单到**精确短语**（用户可见的产品文案，命中"你眼里"但不属开发叙事）。
#: 不用整文件白名单 —— 那会变成"这个文件里什么都查不出来"的黑洞。
WHITE_PHRASES = ("你眼里的火种",)

SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".otf",
            ".dll", ".exe", ".pyd", ".pyc", ".so", ".zip", ".db", ".mp3", ".mp4", ".wav",
            # ⭐ 2026-09-29：`vendor/ocr/*.whl`（随包带的 RapidOCR 轮子）是二进制压缩包，
            #    与 `.zip` 同类 ⇒ 必须一起跳过；否则本判据会因"读不出来"响亮地红（它自己就是这么设计的）。
            ".whl", ".tar", ".gz", ".bz2", ".7z",
            ".bin", ".dat", ".onnx", ".pt", ".webp", ".bmp", ".pak"}

#: ⛔ 起子进程一律不许闪控制台窗（Windows 下不设它，用户会看到黑窗一闪）
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


#: ⛔ 本文件必须从扫描面里排除：它把"要抓的模式"**当数据写在源码里**（正则 + 反向控制样本）
#: ⇒ 扫它等于**自指**（自己把自己数成违规）。豁免要付代价：见 B/C 段的"豁免面就这一个文件"断言
#: + 分母守卫。⚠️ 上一版没排除，是因为当时它还没 commit（`git ls-files` 查不到它）⇒ 一次都没红过，
#: 直到出包后按 §6-1 对**包内**再扫一次才暴露。
SELF_REL = "scripts/privacy_scan_selftest.py"


def tracked_files(exclude_self=True):
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, check=True,
                         creationflags=_NO_WINDOW).stdout.decode("utf-8").split("\n")
    for rel in out:
        rel = rel.strip()
        if not rel or os.path.splitext(rel)[1].lower() in SKIP_EXT:
            continue
        if exclude_self and rel == SELF_REL:
            continue
        if os.path.isfile(os.path.join(ROOT, rel.replace("/", os.sep))):
            yield rel


def hits_of(rel, text):
    out = []
    for ln, line in enumerate(text.split("\n"), 1):
        if not NARRATIVE.search(line):
            continue
        if any(w in line for w in WHITE_PHRASES):
            continue
        out.append((rel, ln, line.strip()[:120]))
    return out


def main():
    rows, n, whitelisted, unreadable = [], 0, 0, []
    for rel in tracked_files():
        try:
            text = io.open(os.path.join(ROOT, rel.replace("/", os.sep)), encoding="utf-8").read()
        except (UnicodeDecodeError, OSError) as exc: # ⛔ 不许静默跳过：跳过＝让这个文件逃出隐私扫描面
            unreadable.append("%s: %s" % (rel, exc))
            continue
        n += 1
        if any(w in text for w in WHITE_PHRASES):
            whitelisted += 1
        rows += hits_of(rel, text)

    print("== A. 读数 ==")
    print("   扫描面：git 跟踪的文本文件 %d 个（跳过二进制/媒体扩展名）" % n)
    print("   开发叙事命中：**%d 处**；白名单（产品文案）覆盖 %d 个文件" % (len(rows), whitelisted))

    print("== B. 硬零 ==")
    ok("成品文件里没有开发叙事（轮次标签 / 协作词 / 计划条目 / 审计编号）", not rows,
       "命中 %d 处：%s" % (len(rows), "、".join("%s:%d" % r[:2] for r in rows[:4])))
    for rel, ln, line in rows[:12]:
        print("      %s:%d  %s" % (rel, ln, line))

    print("== C. 分母与白名单 ==")
    _excluded = len([r for r in tracked_files(exclude_self=False)
                     if r == SELF_REL])
    ok("豁免面就本文件一个（模式写在源码里 ⇒ 必须自排除；但豁免不许扩大）",
       _excluded == 1, "排除了 %d 个" % _excluded)
    ok("分母守卫：真扫到了足够多的成品文件", n >= 300, "扫到 %d 个" % n)
    ok("扫描面里每个文件都真读出来了（读不出来的必须响亮地红，不许静默跳过）",
       not unreadable, "读不出来 %d 个：%s" % (len(unreadable), "、".join(unreadable[:3])))
    _missing = [w for w in WHITE_PHRASES
                if not any(w in io.open(os.path.join(ROOT, r.replace("/", os.sep)),
                                        encoding="utf-8").read()
                           for r in tracked_files())]
    ok("白名单短语**每条都仍在用**（表与实测一致；留一条没人命中过的＝空洞豁免）",
       not _missing, "待删 %d 条：%s" % (len(_missing), "、".join(_missing[:3])))

    print("== D. 反向控制 ==")
    _bad = ('# 对面 r25 实测它对不同会话也会判 True\n'
            '# （audit-r6 D2 新发现）\n'
            '# 另一条 r7 实测教训\n'
            'help="跨机 r13 实测：t=27.4s"\n')
    _good = ('_NSFW = ("r18", "18禁")\n'
             '"deepseek-r1-0528": { "in": 4.5 },\n'
             'r0 = cfg.get("rate")\n'
             'self.set_bubbles(bubs)  # ang/r0 由种子化 rng 生成\n'
             'label: "r18=0 强制"\n')
    _n_hit = sum(1 for l in _bad.split("\n") if NARRATIVE.search(l))
    _n_ok = sum(1 for l in _good.split("\n") if NARRATIVE.search(l))
    ok("反向控制：四条族都能认出来（4 个样本全部命中）", _n_hit == 4, "认出 %d 个" % _n_hit)
    ok("反向控制：功能记号（r18 / deepseek-r1 / 局部变量 r0 / 渲染参数 ang/r0）**不**被误认",
       _n_ok == 0, "误认 %d 个" % _n_ok)

    print("== 隐私扫描判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
    return 0 if FAIL[0] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
