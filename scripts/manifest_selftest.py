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


def dlc_hash_ok(d):
    """V9 硬断言：`dlc[].sha256` 必须匹配 `^[0-9a-f]{64}$`，**或**该条显式 `placeholder is True`。

    两者都不满足 ⇒ False（"空哈希 + 有 note"这种老口径不再算过：那是给空哈希开后门）。
    """
    sha = (d or {}).get("sha256")
    return is_hex64(sha) or (sha == "" and (d or {}).get("placeholder") is True)


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

    print("\n[M6] dlc 段：字段齐 + requiresBase 合法 + **哈希非空（或显式 placeholder）**")
    dlc = man.get("dlc")
    ok(isinstance(dlc, list) and len(dlc) >= 1, "dlc 是非空数组（%d 条）" % (len(dlc) if isinstance(dlc, list) else -1))
    for d in (dlc or []):
        did = d.get("id", "?")
        for k in ("id", "name", "version", "requiresBase", "sha256", "url", "size", "enabledByDefault"):
            ok(k in d, "dlc[%s] 有字段 %s" % (did, k))
        ok(req_base_ok(d.get("requiresBase")), "dlc[%s].requiresBase 是合法区间（%s）" % (did, d.get("requiresBase")))
        ok(isinstance(d.get("enabledByDefault"), bool), "dlc[%s].enabledByDefault 是布尔" % did)
        # ⛔ 2026-09-20 修 V9（原来这里是"sha256 为空 ⇒ 只要有 note 就放行"，等于给空哈希开后门）：
        #    硬断言——每条 dlc[].sha256 必须是 ^[0-9a-f]{64}$，**或**该条显式 `placeholder is True`。
        ok(dlc_hash_ok(d), "dlc[%s] 哈希合规：真 sha256 或显式 placeholder=true（sha256=%r placeholder=%r）"
           % (did, d.get("sha256"), d.get("placeholder")))

    print("\n[M6a] 哈希判据的阴/阳对照（证明 M6 不是恒真）")
    ok(dlc_hash_ok({"sha256": "a" * 64}), "阳性对照：64 位小写十六进制 ⇒ 放行")
    ok(dlc_hash_ok({"sha256": "", "placeholder": True}), "降级路径：空哈希 + 显式 placeholder=true ⇒ 放行（本轮选的路）")
    ok(not dlc_hash_ok({"sha256": ""}), "阴性对照：空哈希且没标 placeholder ⇒ 判红（这就是 V9 原文那条）")
    ok(not dlc_hash_ok({"sha256": "", "placeholder": False}), "阴性对照：placeholder=false 救不了空哈希")
    ok(not dlc_hash_ok({"sha256": "A" * 64}), "阴性对照：大写十六进制不算数（正则钉死小写）")
    ok(not dlc_hash_ok({"sha256": "0" * 63}), "阴性对照：63 位（长度不对）判红")
    ok(not dlc_hash_ok({"sha256": "z" * 64}), "阴性对照：非十六进制字符判红")

    print("\n[M6b] 降级一致性：标了 placeholder 就必须真的「没接线」，且文档写明")
    n_ph = sum(1 for d in (dlc or []) if d.get("placeholder") is True)
    agent_hits = []
    _adir = os.path.join(ROOT, "agent")
    for _fn in sorted(os.listdir(_adir)) if os.path.isdir(_adir) else []:
        if not _fn.endswith(".py"):
            continue
        try:
            _t = open(os.path.join(_adir, _fn), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        if "requiresBase" in _t:
            agent_hits.append(_fn)
    if n_ph:
        # placeholder 的语义＝"客户端目前不读"；一旦有人接线了，清单必须同步摘掉 placeholder（否则就是骗人）
        ok(not agent_hits,
           "清单有 %d 条 placeholder ⇒ agent/ 下不应有 requiresBase 消费者（实命中 %s）" % (n_ph, agent_hits or "无"))
        _docp = os.path.join(ROOT, "docs", "设计-本体与DLC.md")
        _doc = open(_docp, encoding="utf-8", errors="replace").read() if os.path.exists(_docp) else ""
        ok("未接线" in _doc, "docs/设计-本体与DLC.md 写明「当前未接线」（与 placeholder 口径一致）")
    else:
        ok(bool(agent_hits),
           "清单没有任何 placeholder ⇒ 必须已经接线（agent/ 下 requiresBase 消费者：%s）" % (agent_hits or "无"))

    print("\n[M6c] 生成器那道闸**真的接线了**（不信源码，现场篡改 DLC 打一遍）")
    _gatedir = os.path.join(tmp, "gate")            # 空目录：用来验"拒绝时一个文件都不写"
    os.makedirs(_gatedir, exist_ok=True)
    _probe = (
        "import importlib.util,sys,os;"
        "sys.path.insert(0, %r);"
        "spec=importlib.util.spec_from_file_location('mm_gate', os.path.join(%r, 'make_manifest.py'));"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        "m.DLC=[{'id':'x.bad','name':'坏条目','version':'','requiresBase':'>=0.0.0','sha256':'','url':'','size':0,"
        "'enabledByDefault':False,'note':'故意留空哈希且不标 placeholder'}];"
        "sys.argv=['make_manifest.py','--out',%r];"
        "print('GATE_RC=%%d' %% m.main())" % (ROOT, HERE, _gatedir)
    )
    _r2 = subprocess.run([sys.executable, "-c", _probe], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=900, cwd=ROOT,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _o2 = (_r2.stdout or "").strip()
    ok("GATE_RC=3" in _o2,
       "空哈希且未标 placeholder ⇒ 生成器 rc=3（拒绝出清单，不是嘴上说说）｜末行 %s"
       % (_o2.splitlines()[-1:] or _r2.stderr[-200:]))
    ok(os.listdir(_gatedir) == [],
       "坏 DLC 时**一个文件都不写**（拒绝要拒绝干净）｜目录内容 %s" % os.listdir(_gatedir))

    print("\n[M7] announce 段：只读、不携带可执行内容")
    an = man.get("announce") or {}
    for k in ("version", "notes", "forceBase", "minBase"):
        ok(k in an, "announce 有字段 %s" % k)
    notes = an.get("notes")
    ok(isinstance(notes, list) and all(isinstance(x, str) for x in notes), "notes 是字符串数组")
    ok(isinstance(an.get("forceBase"), bool), "forceBase 是布尔")
    bad_note = [x for x in (notes or []) if re.search(r"\.(exe|dll|bat|cmd|ps1|py)\b|[;&|`$]\s*\w", x)]
    ok(not bad_note, "notes 里没有可执行文件名/命令（公告只读）" + ("（可疑：%s）" % bad_note[:2] if bad_note else ""))
    # 注意：minBase 是**裸版本号**、requiresBase 才是区间 —— 自检一开始把两者用同一个校验，自己抓出来了
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
