# -*- coding: utf-8 -*-
"""「本体 + DLC」清单判据（群相）—— 清单字段齐 · 组合哈希可复算 · 公告只读 · 不含 PII。

自包含：**自己跑一遍 `make_manifest.py`（输出到临时目录）**，不依赖 `_outbox` 的现状，
所以"上次跑过没有"不影响判据结论。

对应契约：`docs/设计-本体与DLC.md` §三（清单格式）§四（增量）三条硬规矩：
  ① 每个包都带 sha256 ② `requiresBase` 不满足不装 ③ 公告只读、不携带可执行内容。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✔ " + msg)
    else:
        FAIL += 1
        print("  ✘ " + msg)


def is_hex64(s):
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


def req_base_ok(s):
    """合法的版本区间：`>=x.y.z` 或 `>=x.y.z <a.b.c`（宽松：>= 开头即可）。"""
    return isinstance(s, str) and bool(re.match(r"^>=\s*\d+(\.\d+)*(\s*<\s*\d+(\.\d+)*)?$", s.strip()))


tmp = tempfile.mkdtemp(prefix="pm-manifest-")
try:
    print("[M1] 生成器能跑通")
    r = subprocess.run([sys.executable, os.path.join(HERE, "make_manifest.py"),
                        "--out", tmp, "--notes", "判据自测;第二条要点"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=600,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    ok(r.returncode == 0, "make_manifest.py rc=%s" % r.returncode)
    if r.returncode != 0:
        print((r.stdout or "")[-800:])
        print((r.stderr or "")[-800:])
        sys.exit(1)

    mp = os.path.join(tmp, "persona-morph-manifest.json")
    fp = os.path.join(tmp, "persona-morph-files.json")
    ok(os.path.exists(mp), "清单文件已产出")
    ok(os.path.exists(fp), "文件哈希表已产出")
    man = json.load(open(mp, encoding="utf-8"))
    fmap = json.load(open(fp, encoding="utf-8"))

    print("\n[M2] 清单骨架与 base 段")
    ok(man.get("schema") == "persona-morph/1", "schema = persona-morph/1")
    b = man.get("base") or {}
    for k in ("version", "sha256", "url", "size", "files"):
        ok(k in b, "base 有字段 %s" % k)
    # 版本号两段式（2026-09-16 用户定）：`YYYY.M.D.N`＝功能版本 · `YYYY.M.D.N.M`＝该版本下的小更新
    ok(isinstance(b.get("version"), str)
       and re.match(r"^\d{4}\.\d{1,2}\.\d{1,2}\.\d+(\.\d+)?$", b.get("version") or ""),
       "base.version 形如 YYYY.M.D.N[.M]（实为 %s）" % b.get("version"))
    ok(is_hex64(b.get("sha256")), "base.sha256 是 64 位十六进制")
    ok(isinstance(b.get("size"), int) and b["size"] > 0, "base.size 是正整数（%s）" % b.get("size"))
    ok(isinstance(b.get("files"), int) and b["files"] > 0, "base.files 是正整数（%s）" % b.get("files"))

    print("\n[M3] 版本号唯一来源（清单必须等于 agent/version.py）")
    from agent.version import VERSION as CODE_VERSION   # noqa: E402
    ok(b.get("version") == CODE_VERSION, "清单 version == agent/version.py（%s）" % CODE_VERSION)

    print("\n[M4] 组合哈希可复算（判据的核心：哈希不是摆设）")
    files = (fmap.get("files") or {})
    ok(len(files) > 0, "文件哈希表非空（%d 条）" % len(files))
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode("utf-8"))
        h.update(str(files[rel]["sha256"]).encode("ascii"))
    ok(h.hexdigest() == b.get("sha256"),
       "由哈希表重算的组合哈希 == 清单里的 base.sha256")
    ok(len(files) == b.get("files"), "条目数一致（表 %d / 清单 %d）" % (len(files), b.get("files")))
    ok(sum(int(v["size"]) for v in files.values()) == b.get("size"), "字节总数一致")

    print("\n[M5] 每条文件哈希都得是 64 位十六进制（没有哈希的更新链一律不认）")
    bad = [k for k, v in files.items() if not is_hex64(v.get("sha256"))]
    ok(not bad, "全部文件哈希合法" + ("（坏 %d 条：%s）" % (len(bad), bad[:3]) if bad else ""))

    print("\n[M6] dlc 段：字段齐 + requiresBase 合法 + 带 sha256 字段位")
    dlc = man.get("dlc")
    ok(isinstance(dlc, list) and len(dlc) >= 1, "dlc 是非空数组（%d 条）" % (len(dlc) if isinstance(dlc, list) else -1))
    for d in (dlc or []):
        did = d.get("id", "?")
        for k in ("id", "name", "version", "requiresBase", "sha256", "url", "size", "enabledByDefault"):
            ok(k in d, "dlc[%s] 有字段 %s" % (did, k))
        ok(req_base_ok(d.get("requiresBase")), "dlc[%s].requiresBase 是合法区间（%s）" % (did, d.get("requiresBase")))
        ok(isinstance(d.get("enabledByDefault"), bool), "dlc[%s].enabledByDefault 是布尔" % did)
        # 未发布时 sha256 允许为空，但必须写清为什么（否则就是"没有哈希的更新链"）
        if not d.get("sha256"):
            ok(bool(d.get("note")), "dlc[%s] sha256 为空 ⇒ 必须有 note 说明（未发布/以他处版本为准）" % did)

    print("\n[M7] announce 段：只读、不携带可执行内容")
    an = man.get("announce") or {}
    for k in ("version", "notes", "forceBase", "minBase"):
        ok(k in an, "announce 有字段 %s" % k)
    notes = an.get("notes")
    ok(isinstance(notes, list) and all(isinstance(x, str) for x in notes), "notes 是字符串数组")
    ok(isinstance(an.get("forceBase"), bool), "forceBase 是布尔")
    bad_note = [x for x in (notes or []) if re.search(r"\.(exe|dll|bat|cmd|ps1|py)\b|[;&|`$]\s*\w", x)]
    ok(not bad_note, "notes 里没有可执行文件名/命令（公告只读）" + ("（可疑：%s）" % bad_note[:2] if bad_note else ""))
    # 注意：minBase 是**裸版本号**、requiresBase 才是区间 —— 判据一开始把两者用同一个校验，自己抓出来了
    ok(bool(re.match(r"^\d+(\.\d+)*$", str(an.get("minBase") or ""))), "minBase 是合法版本号（%s）" % an.get("minBase"))

    print("\n[M8] 清单里不许出现本机路径/用户名（PII 闸门）")
    blob = json.dumps(man, ensure_ascii=False) + json.dumps(fmap, ensure_ascii=False)
    uni = os.environ.get("USERNAME") or ""
    leaks = []
    if re.search(r"[A-Za-z]:\\\\?Users\\\\?", blob):
        leaks.append("Windows 家目录")
    if uni and re.search(re.escape(uni), blob, re.I):
        leaks.append("用户名")
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.]{2,}", blob):
        leaks.append("邮箱")
    ok(not leaks, "无 PII 泄漏" + ("（命中：%s）" % leaks if leaks else ""))

    print("\n[M9] 负向对照：篡改哈希表后必须对不上（证明 M4 不是恒真）")
    tampered = dict((k, dict(v)) for k, v in files.items())
    k0 = sorted(tampered)[0]
    tampered[k0]["sha256"] = "0" * 64
    h2 = hashlib.sha256()
    for rel in sorted(tampered):
        h2.update(rel.encode("utf-8"))
        h2.update(str(tampered[rel]["sha256"]).encode("ascii"))
    ok(h2.hexdigest() != b.get("sha256"), "改一条文件哈希 ⇒ 组合哈希变了（判据有效）")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n==== 清单判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
