# -*- coding: utf-8 -*-
"""strip_skill_meta.py —— 删掉技能描述里"关于包本身"的括号说明（用户口径：不要解释性文本）。

只动 `description:` 一行的**开头那类元信息括号**：
  · `（聚合 16 个技能）` / `（聚合三个自研技能）` / `（聚合 2 个技能）` …
  · `（当前含 群相 Persona Morph 实例）`
**不动**正文里"每个子技能包含什么"的枚举括号（如 `写码纪律（最小实现/外科补丁/…）`）——
那是内容不是解释；要一并删，说一声。

同时改两处（装好的包与源包各一份），改完打印前后对照。
用法： py -3 scripts\\strip_skill_meta.py [--dry]
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.environ.get("USERPROFILE") or os.path.expanduser("~")

TREES = [
    os.path.join(HOME, ".dsh", "skills"),                                    # 活库（运行时直接读）
    os.path.join(HOME, ".dsh", "packages", "Eif-MAPT-skills-src", "skills"),  # 源包
    os.path.join(HOME, ".dsh", "profiles", "web", "node_modules", "Eif-MAPT-skills", "skills"),  # 装好的包
]

# 只匹配 description 开头紧跟在名字后面的元信息括号，两种形态
PATTERNS = [
    re.compile(r"（聚合[^）]{0,12}个?技能）"),
    re.compile(r"（聚合[^）]{0,12}）"),
    re.compile(r"（当前含[^）]{0,40}）"),
]

dry = "--dry" in sys.argv
changed = 0
scanned = 0
for tree in TREES:
    if not os.path.isdir(tree):
        print("（跳过，不存在）" + tree)
        continue
    for name in sorted(os.listdir(tree)):
        f = os.path.join(tree, name, "SKILL.md")
        if not os.path.isfile(f):
            continue
        scanned += 1
        with open(f, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        touched = False
        for i, line in enumerate(lines):
            if not line.startswith("description:"):
                continue
            new = line
            for p in PATTERNS:
                new = p.sub("", new)
            new = new.replace("：：", "：")
            if new != line:
                lines[i] = new
                touched = True
                print("  %-58s → %s" % (name, new.strip()[:88]))
        if touched:
            changed += 1
            if not dry:
                with open(f, "w", encoding="utf-8", newline="") as fh:
                    fh.writelines(lines)

print("\n扫描 %d 个 SKILL.md，改动 %d 个%s" % (scanned, changed, "（dry-run，未写盘）" if dry else ""))
