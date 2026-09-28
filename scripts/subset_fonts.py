# -*- coding: utf-8 -*-
"""字体子集生成（开发工具，不进发布包）：按**字符集口径**裁掉用不到的字形。

为什么需要：两个字体的体积 95% 以上落在 `glyf`（全量字形 3 万余、绝大多数永远用不到）。
按口径子集后 zip 体积降 50%~83%。本脚本是**唯一生成入口** —— 字符集口径、name 表保留
策略、hinting 取舍都写在这里，避免手工命令行漂移。

字符集口径（改口径只改本文件）：
  · 基础：GB2312 全量（程序化解码生成，不引外部字表）；
  · 加：**产品自身文本**用到的全部 >U+2000 字符（`ui_qt/`、`agent/`、`assets/`、
    `scripts/`、`tools.d/`、`launcher-src/` 与三份根文档）⇒ 界面文案要画的字一个不漏；
  · 加：拉丁 / 标点 / 全角 / 常用符号的**区段余量**（见 `SYMBOL_MARGIN`）；
  · profile=title ⇒ 上面三样（标题字体只用于固定文案：面板标题、顶栏那行）；
  · profile=body  ⇒ 再并上 CJK 基本区全量（U+4E00–9FFF）⇒ 简体 + 繁体常用字全量。

保留策略：`name` 表全量保留（`ensure_fonts()` 靠 Qt 动态取 family 名，名字必须不变）；
文件名不变；`notdef` 保留轮廓；hinting 默认**保留**（丢它只多省几个百分点，却要动小字号的
渲染观感）。OFL 要求随分发携带版权与许可声明 ⇒ 本脚本不动 name 表里的许可字段，
仓库里的 `LICENSE-屏显臻宋-OFL.txt` 继续随包。

依赖 fontTools（开发机自行 `pip install fonttools`；产品运行时里没有它，属预期）。

用法：
  python scripts/subset_fonts.py --profile title --check          # 只核对现文件是否等于按口径生成的结果
  python scripts/subset_fonts.py --profile title --build          # 生成并就地替换（原文件先备份）
  python scripts/subset_fonts.py --profile title --build --backup <目录>
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(ROOT, "ui_qt", "assets", "fonts")

#: profile → 字体文件名
FILES = {
    "title": "ZhaoHuaBiaoTiA.ttf",
    "body": "PingXianZhenSong.ttf",
}

#: 扫描"产品自身文本"的目录与扩展名（口径固定下来，免得每次生成结果漂移）
TEXT_DIRS = ("ui_qt", "agent", "assets", "scripts", "tools.d", "launcher-src")
TEXT_EXTS = (".py", ".html", ".js", ".css", ".json", ".txt", ".md", ".ps1", ".cs", ".vbs")
TEXT_ROOT_FILES = ("README.md", "使用说明.md", "更新日志.md")

#: 符号区段余量：源码里当前没用到、但界面很容易用到的那几段（字幅小、代价低）
SYMBOL_MARGIN = (
    (0x2000, 0x2070),  # 通用标点 / 上下标
    (0x20A0, 0x20D0),  # 货币符号
    (0x2100, 0x2140),  # 字母式符号（℃ ™ ℉ …）
    (0x2190, 0x21A0),  # 箭头
    (0x2200, 0x22A0),  # 数学运算符
    (0x2300, 0x2400),  # 技术符号（⌘ ⌥ ⌫ …）
    (0x2460, 0x2500),  # 带圈数字等
    (0x2500, 0x2580),  # 制表 / 方块
    (0x25A0, 0x2600),  # 几何图形（▸ ▾ ● ◆ …）
    (0x2600, 0x27C0),  # 杂项符号 / 装饰符号（★ ⚠ ✔ ✕ …）
    (0x2B00, 0x2C00),  # 杂项符号与箭头（⭐ …）
    (0x3000, 0x3040),  # CJK 标点
    (0xFF00, 0xFF70),  # 全角形式
)

#: CJK 基本区（profile=body 时并入）
CJK_BASIC = (0x4E00, 0xA000)


def gb2312_chars() -> set:
    """GB2312 全字符集（6763 汉字 + 682 符号）：按区位码逐字节对解码生成。

    非法字节对用 `errors="ignore"` 直接落成空串（那些区段本来就没有字符），
    不需要异常分支。
    """
    out = set()
    for hi in range(0xA1, 0xF8):
        for lo in range(0xA1, 0xFF):
            ch = bytes([hi, lo]).decode("gb2312", "ignore")
            if ch:
                out.add(ch)
    return out


def product_text_chars() -> set:
    """产品自身文本里出现的全部 >U+2000 字符（界面文案要画的字）。

    读不到的文本文件**如实计数并报出来** —— 它意味着口径可能少几个字，
    不能把它当成"这个文件不存在"。
    """
    chars = set()
    unreadable = []
    for sub in TEXT_DIRS:
        base = os.path.join(ROOT, sub)
        for dp, dn, fns in os.walk(base):
            dn[:] = [d for d in dn if d not in ("__pycache__", "fonts", "wheels")]
            for fn in fns:
                if not fn.lower().endswith(TEXT_EXTS):
                    continue
                try:
                    txt = io.open(os.path.join(dp, fn), encoding="utf-8", errors="ignore").read()
                except OSError as e:
                    unreadable.append("%s(%s)" % (fn, type(e).__name__))
                    continue
                chars.update(ch for ch in txt if ord(ch) > 0x2000)
    for fn in TEXT_ROOT_FILES:
        p = os.path.join(ROOT, fn)
        if os.path.exists(p):
            chars.update(ch for ch in io.open(p, encoding="utf-8", errors="ignore").read()
                         if ord(ch) > 0x2000)
    if unreadable:
        print("提示：%d 个文本文件读不到（口径可能少几个字）：%s"
              % (len(unreadable), "、".join(unreadable[:5])))
    return chars


def charset(profile: str) -> set:
    """按 profile 算字符集。"""
    s = set(gb2312_chars()) | product_text_chars()
    for lo, hi in SYMBOL_MARGIN:
        s.update(chr(c) for c in range(lo, hi))
    s.discard("\u0000")
    if profile == "body":
        s.update(chr(c) for c in range(*CJK_BASIC))
    return s


def options(keep_hinting: bool = True):
    from fontTools import subset
    o = subset.Options()
    o.layout_features = ["*"]
    o.name_IDs = ["*"]              # family/许可字段全保留 ⇒ Qt 取的 family 名不变
    o.name_legacy = True
    o.name_languages = ["*"]
    o.notdef_outline = True
    o.recalc_bounds = True
    o.hinting = keep_hinting
    o.drop_tables += ["DSIG"]
    return o


def build_to(src_path: str, chars: set, dst_path: str, keep_hinting: bool = True) -> str:
    """按口径生成子集到 dst_path，返回 sha256。"""
    from fontTools import subset
    opts = options(keep_hinting)
    font = subset.load_font(src_path, opts)
    try:
        sub = subset.Subsetter(options=opts)
        sub.populate(unicodes=[ord(c) for c in chars if ord(c) <= 0x10FFFF])
        sub.subset(font)
        subset.save_font(font, dst_path, opts)
    finally:
        font.close()
    return sha256_of(dst_path)


def sha256_of(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def coverage_report(font_path: str, chars: set) -> tuple:
    """现文件覆盖了口径字符集的多少（缺哪些：前 20 个）。返回 (缺失集, cmap 总数)。"""
    from fontTools.ttLib import TTFont
    f = TTFont(font_path, lazy=True)
    try:
        have = set(f.getBestCmap().keys())
    finally:
        f.close()
    miss = {c for c in chars if ord(c) not in have}
    return miss, len(have)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=("title", "body"), required=True)
    ap.add_argument("--check", action="store_true", help="只核对现文件是否等于按口径生成的结果")
    ap.add_argument("--build", action="store_true", help="生成并就地替换")
    ap.add_argument("--backup", default="", help="替换前把原文件备份到该目录")
    ap.add_argument("--no-hinting", action="store_true", help="丢掉 hinting 指令（更小，小字号观感有变）")
    a = ap.parse_args()
    if not (a.check or a.build):
        ap.error("要么 --check，要么 --build")

    fn = FILES[a.profile]
    src = os.path.join(FONT_DIR, fn)
    if not os.path.isfile(src):
        print("找不到字体文件：%s" % src)
        return 2
    chars = charset(a.profile)
    miss, cmap_n = coverage_report(src, chars)
    print("口径：profile=%s → 请求 %d 个字符；现文件 cmap %d 个码位；缺 %d 个%s"
          % (a.profile, len(chars), cmap_n, len(miss),
             ("：%s" % "".join(sorted(miss)[:20])) if miss else ""))

    if a.check:
        tmp = src + ".subsetcheck"
        try:
            h = build_to(src, chars, tmp, keep_hinting=not a.no_hinting)
            same = (sha256_of(src) == h)
            print("现文件 %s，按口径生成 %s ⇒ 一致=%s"
                  % (sha256_of(src)[:16], h[:16], same))
            return 0 if same else 1
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    if a.backup:
        os.makedirs(a.backup, exist_ok=True)
        dst_bak = os.path.join(a.backup, fn)
        import shutil
        shutil.copy2(src, dst_bak)
        print("原文件已备份：%s（sha256 %s）" % (dst_bak, sha256_of(dst_bak)[:16]))
    before = os.path.getsize(src)
    tmp = src + ".new"
    h = build_to(src, chars, tmp, keep_hinting=not a.no_hinting)
    os.replace(tmp, src)
    after = os.path.getsize(src)
    print("已替换 %s：%.2f MB → %.2f MB（-%.0f%%）· sha256 %s"
          % (fn, before / 1048576.0, after / 1048576.0,
             100 * (1 - after / max(1, before)), h))
    return 0


if __name__ == "__main__":
    sys.exit(main())
