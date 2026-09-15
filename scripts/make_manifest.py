# -*- coding: utf-8 -*-
"""群相 · 清单与文件哈希表生成器（本体 + DLC 契约）。

契约见 `docs/设计-本体与DLC.md` §三/§四，硬规矩三条：
  ① 清单里的每个包都带 **sha256**（没有哈希的更新链一律不认——MAA 的反面教材）；
  ② `requiresBase` 不满足**不装**（只提示，不静默降级）；
  ③ 公告**只读**、不携带可执行内容。

产出两份（默认落在仓库外层的 `_outbox/`；文件名固定，便于发布脚本引用）：
  `persona-morph-manifest.json`  清单：base + dlc + announce + updatedAt
  `persona-morph-files.json`     逐文件 sha256 + 大小（供 `make_patch.py` 做增量 diff）

用法：
  py -3 scripts/make_manifest.py [--out DIR] [--notes "要点1;要点2"] [--url URL]
                               [--version X] [--built-at ISO]
"""
import argparse
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pack_online as po          # noqa: E402  复用它的 tracked()/excluded()/ROOT/OUT_DIR，不重造排除规则

sys.path.insert(0, po.ROOT)
from agent.version import VERSION as CODE_VERSION   # noqa: E402

MANIFEST = "persona-morph-manifest.json"
FILES = "persona-morph-files.json"
SCHEMA = "persona-morph/1"

# 群相的 DLC 清单（本体＝Python 代码 + 一键启动.exe + launcher-src；**驱动微信那层锁在本体**，不许 DLC 化）
DLC = [
    {"id": "skills.eif-mapt", "name": "Eif-MAPT 技能包",
     "version": "", "requiresBase": ">=0.0.0", "sha256": "", "url": "",
     "size": 0, "enabledByDefault": True,
     "note": "装在 ~/.dsh/skills 的知识包（含 E 控制台与 EMAP Memorize）；版本以插件包 tan 为准"},
    {"id": "vcable.driver", "name": "虚拟声卡（真语音条）",
     "version": "Pack45", "requiresBase": ">=0.0.0", "sha256": "", "url": "",
     "size": 1318877, "enabledByDefault": False,
     "note": "VB-CABLE；真语音条需要它 + 把微信输入设备指到 CABLE Output。装驱动必须由用户点一次"},
]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(po.OUT_DIR, "_outbox"))
    ap.add_argument("--notes", default="", help="公告要点，分号分隔")
    ap.add_argument("--url", default="", help="下载地址（未发布时留空）")
    ap.add_argument("--version", default=CODE_VERSION)
    ap.add_argument("--built-at", default="")
    a = ap.parse_args()

    rels = sorted(r for r in po.tracked() if not po.excluded(r))
    if not rels:
        print("✘ git 里没有可跟踪的文件（是不是不在仓库里跑？）")
        return 2

    files, total = {}, 0
    for rel in rels:
        abs_p = os.path.join(po.ROOT, rel.replace("/", os.sep))
        if not os.path.exists(abs_p):
            continue
        size = os.path.getsize(abs_p)
        files[rel] = {"sha256": sha256_file(abs_p), "size": size}
        total += size

    # 文件树组合哈希：排序后 (相对路径 + 该文件哈希) 再哈希 ⇒ 稳定、与 zip 时间戳无关
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode("utf-8"))
        h.update(files[rel]["sha256"].encode("ascii"))
    tree_sha = h.hexdigest()

    notes = [s.strip() for s in a.notes.split(";") if s.strip()]
    manifest = {
        "schema": SCHEMA,
        "base": {"version": a.version, "sha256": tree_sha, "url": a.url,
                 "size": total, "files": len(files)},
        "dlc": DLC,
        "announce": {"version": a.version, "notes": notes,
                     "forceBase": False, "minBase": a.version},
        "builtAt": a.built_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updatedAt": int(time.time() * 1000),
    }

    os.makedirs(a.out, exist_ok=True)
    mp = os.path.join(a.out, MANIFEST)
    fp = os.path.join(a.out, FILES)
    with open(mp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(fp, "w", encoding="utf-8") as fh:
        json.dump({"version": a.version, "files": files}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print("✔ 清单：" + mp)
    print("   base.version = %s · files = %d · bytes = %d" % (a.version, len(files), total))
    print("   base.sha256  = %s（文件树组合哈希）" % tree_sha[:16] + "…")
    print("   dlc = %s" % "、".join(d["id"] for d in DLC))
    print("   announce.notes = %s" % ("；".join(notes) if notes else "（空——发布时用 --notes 补）"))
    print("✔ 文件哈希表：" + fp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
