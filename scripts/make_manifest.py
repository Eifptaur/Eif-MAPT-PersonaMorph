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
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pack_online as po          # noqa: E402  复用它的 tracked()/excluded()/ROOT/OUT_DIR，不重造排除规则
import release_notes as rn        # noqa: E402  发布说明写法（作者 2026-09-20 定的规矩，唯一实现）

sys.path.insert(0, po.ROOT)
from agent.version import VERSION as CODE_VERSION   # noqa: E402

MANIFEST = "persona-morph-manifest.json"
FILES = "persona-morph-files.json"
SCHEMA = "persona-morph/1"

# 群相的 DLC 清单（本体＝Python 代码 + 一键启动.exe + launcher-src；**驱动微信那层锁在本体**，不许 DLC 化）
#
# ⛔ 2026-09-20 修 V9（选"先降级、但不骗人"这条路）：下面这些条目**当前未接线** ——
#    `sha256`/`url` 留空、并显式标 `"placeholder": true`，语义＝**未实现，客户端目前不读**：
#    ① `agent/` 下**没有任何代码**读 `dlc[]` / `requiresBase`（更新链只消费 base / announce）；
#    ② 所以硬规矩①（每个包都带 sha256）在这些条目上**暂不成立**；标 placeholder 是为了把"空哈希"
#       从"看起来像合法值"变成**显式声明**。`manifest_selftest.py` 有硬断言（空哈希且没标 placeholder ⇒ 判红）。
#    ③ `main()` 里的 `dlc_problems()` 是同一道闸：**没标 placeholder 又没真哈希的 DLC 直接拒绝生成清单**。
#    接线那天：填真 `sha256`/`url`、去掉 `placeholder`，并按 §三 硬规矩②在客户端按 `requiresBase` 判闸门。
DLC = [
    {"id": "skills.eif-mapt", "name": "Eif-MAPT 技能包",
     "version": "", "requiresBase": ">=0.0.0", "sha256": "", "url": "",
     "size": 0, "enabledByDefault": True, "placeholder": True,
     "note": "未实现（客户端目前不读 dlc/requiresBase）；装在 ~/.dsh/skills 的知识包（含 E 控制台与 EMAP Memorize），版本以插件包 tan 为准"},
    {"id": "vcable.driver", "name": "虚拟声卡（真语音条）",
     "version": "Pack45", "requiresBase": ">=0.0.0", "sha256": "", "url": "",
     "size": 1318877, "enabledByDefault": False, "placeholder": True,
     "note": "未实现（客户端目前不读 dlc/requiresBase）；VB-CABLE，真语音条需要它 + 把微信输入设备指到 CABLE Output，装驱动必须由用户点一次"},
]


def dlc_problems(dlc) -> list:
    """DLC 闸门：返回问题清单（空＝通过）。

    **硬规矩①**：每个包要么带真 sha256（64 位小写十六进制），要么**显式** `"placeholder": true`
    ——"空字符串"既不是哈希也不是声明，一律拒绝（否则等于把"没有哈希的更新链"发出去）。
    """
    bad = []
    for d in (dlc or []):
        did = d.get("id", "?")
        sha = d.get("sha256")
        if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha):
            continue
        if sha in ("", None) and d.get("placeholder") is True:
            if not d.get("note"):
                bad.append("dlc[%s] 标了 placeholder ⇒ 必须有 note 说明" % did)
            continue
        bad.append("dlc[%s].sha256 既不是 64 位十六进制、也没标 placeholder=true（实为 %r）" % (did, sha))
    return bad


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
    ap.add_argument("--build", default="", help="内容指纹（发布链从**包内**读出来传进来；留空则读源文件）")
    ap.add_argument("--built-at", default="")
    a = ap.parse_args()

    rels = sorted(r for r in po.tracked() if not po.excluded(r))
    if not rels:
        print("✘ git 里没有可跟踪的文件（是不是不在仓库里跑？）")
        return 2

    # ⛔ 出包闸门（V9）：空哈希的 DLC 一律拒绝生成清单（除非它**显式**声明 placeholder）
    _dlc_bad = dlc_problems(DLC)
    if _dlc_bad:
        print("✘ DLC 段没通过硬规矩①（每个包都带 sha256；没有哈希的更新链一律不认）：")
        for _b in _dlc_bad:
            print("   · " + _b)
        print("   ⇒ 要么填真 sha256/url，要么显式标 \"placeholder\": true（＝未实现、客户端目前不读）")
        return 3

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
    # ⛔ 说明写法闸门（作者 2026-09-20 定）：纯修 bug 只许一句「修复了一些 bug」，
    #    有「新增」字样才允许详细写。判据实现见 scripts/release_notes.py（与发版脚本共用同一份）。
    _notes_bad = rn.note_problems(notes)
    if _notes_bad:
        print("✘ 公告要点没通过「说明写法」闸门（修 bug 只写一句，新增功能才详细写）：")
        for _b in _notes_bad:
            print("   · " + _b)
        print("   ⇒ 纯修 bug 就写：--notes \"%s\"" % rn.FIX_ONLY_LINE)
        return 4
    # ⚠️ 内容指纹**直接读文件**（不走 import：BUILD 改写前后同尺寸，字节码缓存会给出旧值 —— 2026-09-18 实测
    #    造成"清单里的 build 与包里实际 BUILD 不一致"，用户侧会一直提示有新包）。发布链更该用 `--build`
    #    把**包内**那个值传进来（唯一事实来源＝即将发出去的那个包）。
    _BUILD = str(a.build or "")
    if not _BUILD:
        try:
            from agent.version import read_build_from as _rbf
            _BUILD = _rbf()
        except Exception:
            _BUILD = ""
    manifest = {
        "schema": SCHEMA,
        "base": {"version": a.version, "sha256": tree_sha, "url": a.url,
                 "build": str(_BUILD or ""),
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
