# -*- coding: utf-8 -*-
"""在线包打包器（群相灵）—— 只打"版本库里跟踪的代码"，并做**个人信息/开发资料扫描闸门**。

用法：
  py -3 scripts/pack_online.py            # 打包 + 扫描（命中即拒绝出包 exit 3）
  py -3 scripts/pack_online.py --check    # 只扫描仓库（不出包）

口径（用户 2026-09-13 定）：
  ① 只有"在线包"：不含 `offline/`（运行时 + wheel），第一次运行由 `scripts/setup_python.ps1` 联网准备；
  ② 包里**不许有他的个人信息与开发资料**——家目录路径 / 密钥 / token / 聊天数据 /
     开发文档（AGENTS.md、docs/ 下的任务清单与 changelog 归档）/ 其它项目素材（whale-widget）；
  ③ 打包源＝`git ls-files`（未跟踪的草稿、报告、日志一律进不来）；
  ④ 扫描命中一律拒绝出包（要放行必须显式加进 ALLOW 并写明理由）。
"""
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.dirname(ROOT)                      # C:\Users\ptmou\Desktop\WX-chatbot
PKG_PREFIX = "群相灵-在线包-"

# 不进包（相对仓库根的 posix 路径前缀 / 精确名）
EXCLUDE = (
    "AGENTS.md",          # 开发守则：含红线自述与用户原话，不随包发
    "docs/",              # 开发资料：任务清单 / changelog 归档
    "whale-widget/",      # 另一个项目（鲸鱼挂件）的素材，与本包无关
    "scripts/pack_online.py",   # 打包器自身：里面有扫描规则字面量（含用户名样本），不进包
    "offline/",           # 离线运行时与 wheel（在线包不需要）
    "_scratch/", "报告/", "wechatauto_logs/", "data/", "runtime/", "logs/",
)

# 扫描规则：(名字, 正则, 是否致命)
SCAN = [
    ("家目录/用户名", r"ptmou", True),
    ("Windows 绝对路径", r"[A-Za-z]:\\+Users\\+[^\\\s\"']+", True),
    ("POSIX 家目录", r"/(?:home|Users)/[A-Za-z0-9._-]+/", True),
    ("API 密钥", r"sk-[A-Za-z0-9_\-]{10,}", True),
    ("Bearer 头", r"[Bb]earer\s+[A-Za-z0-9._\-]{12,}", True),
    ("token 值", r"token[\"'\s:=]{1,4}[A-Za-z0-9]{16,}", True),
    ("微信账号/数据", r"wxid_[A-Za-z0-9]{6,}|MsgAttach|WeChat Files[/\\]", True),
    ("开发资料引用", r"_scratch[/\\]", False),   # 注：`wechatauto_logs/` 是本产品自己的运行日志目录，不算开发资料
    ("署名/仓库名", r"Eifptaur|Eif-MAPT", False),
]

ALLOW = (
    # .gitignore 里的 `_scratch/` 是"别把本地草稿提交进来"这条规则本身，属正常仓库配置
    (".gitignore", "开发资料引用"),
    # 这条是**脱敏判据的输入夹具**：那一行故意塞满假 PII（假手机号/假邮箱/假姓名/假身份证/`wxid_abc123`），
    # 用来断言 scrub_prompt 会把它们都抹掉。它是合成的样本，不是真实账号数据 ⇒ 显式放行并写明理由。
    ("scripts/image_gen_selftest.py", "微信账号/数据"),
)

SKIP_BIN = re.compile(r"\.(png|jpe?g|gif|ico|woff2?|ttf|mp4|zip|db|sqlite3?)$", re.I)


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [p.decode("utf-8") for p in out.split(b"\x00") if p]


def excluded(rel):
    return any(rel == e or rel.startswith(e) for e in EXCLUDE)


def scan_file(abs_path, rel):
    """返回 [(规则名, 例子)]；二进制也扫（exe/dll 里可能嵌源码路径）"""
    if SKIP_BIN.search(rel):
        return []
    try:
        with open(abs_path, "rb") as fh:
            text = fh.read().decode("latin1", "ignore")
    except OSError:
        return []
    hits = []
    for name, pat, fatal in SCAN:
        found = [m.group(0) for m in re.finditer(pat, text)]
        if not found:
            continue
        if any((rel, name) in ALLOW or (rel.split("/")[-1], name) in ALLOW for _ in [0]):
            continue
        uniq = sorted(set(found))[:3]
        hits.append((name, fatal, uniq))
    return hits


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # 控制台是 GBK：中文/符号别炸
    except Exception:
        pass
    check_only = "--check" in sys.argv
    files = [f for f in tracked() if not excluded(f)]
    print(f"跟踪 {len(tracked())} 个文件 → 进包候选 {len(files)} 个")

    fatal_total, warn_total = 0, 0
    for rel in files:
        for name, fatal, uniq in scan_file(os.path.join(ROOT, rel), rel):
            tag = "FATAL" if fatal else "warn "
            print(f"  {tag} [{name}] {rel} 例: {uniq}")
            fatal_total += 1 if fatal else 0
            warn_total += 0 if fatal else 1

    if check_only:
        print(f"扫描结束：致命 {fatal_total} · 注意 {warn_total}")
        return 0 if fatal_total == 0 else 3

    if fatal_total:
        print(f"❌ 有 {fatal_total} 处致命命中 ⇒ 拒绝出包（先清干净或显式加入 ALLOW）")
        return 3

    stamp = datetime.now().strftime("%Y%m%d")
    out = os.path.join(OUT_DIR, f"{PKG_PREFIX}{stamp}.zip")
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in files:
            z.write(os.path.join(ROOT, rel), rel)
    size = os.path.getsize(out) / 1024 / 1024
    print(f"✅ 出包：{out}  {len(files)} 文件 / {size:.2f} MB  （注意项 {warn_total} 条，非致命）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
