#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「微信数据目录」判据（2026-09-18 立，起因＝用户反馈原文）。

反馈原文（2026-09-18 22:23，控制台「反馈」面板）：
    「能不能让我自己选微信的地址，自己自定义的地址他检测不到，移动回默认地址后好了，
      但是监听后没反应，重启后又连接不上了，通过文件夹中的脚本检查出来的报告显示，
      他回我之前自定义的地址里去看文件了」

⇒ 守六条（全部用 `tempfile` 造假环境，**不连真库、不需要微信在跑、不读盘上真实环境状态**）：
  A. 校验：目录不存在 / 存在但没有库文件 / 存在且有 db_storage 或 .db —— 三态判定正确，且失败时
     **原因非空**（防静默）；
  B. 优先级三档：只有默认目录 ⇒ 用它（scanned）；配置可用 ⇒ 用它（config）；配置不可用 ⇒ 回落到
     可用的那个（scanned），**且回落原因非空**；全不可用 ⇒ 交回驱动库自探测（auto），原因仍非空；
  C. **旧路径必须失效**（本反馈的核心）：配置从 A 改成 B 之后，A **不许**再出现在决策结果或
     `db_open_tries` 这条链上；`config` 那层的内存缓存也要在被外部改盘之后自动作废；
  D. 保存闸：`save()` 只在**过校验**时写盘，不过关时一个字都不写、并把原因与回落目标返回；
  E. 暴露：`status()` 给的是**实际生效的目录**（有活 adapter 时以它的 `_db_how` 为准），
     且 `wechat_dir` 已接进 `/api/status`、`/api/wechat/dir`、控制台「微信数据目录」那一行；
  F. 判据不瞎（阴性对照）：把"可用目录"塞进去，回落原因必须**为空**（否则 A~D 里那些"非空"
     断言永远成立，等于没有判据）。

用法：`py -3 scripts\\wechat_dir_selftest.py`
"""
import json
import os
import shutil
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import config as C          # noqa: E402
from agent import wechat as W          # noqa: E402
from agent import wechat_dir as D      # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [%s]" % detail if detail else ""))


def _src(path_rel):
    with open(os.path.join(ROOT, path_rel), encoding="utf-8", errors="replace") as f:
        return f.read()


def make_xwechat_files(root, name="xwechat_files", n_db=1, mtime=None):
    """造一个「像微信数据目录」的目录：`<root>/xwechat_files/<账号>/db_storage/message/*.db`。"""
    base = os.path.join(root, name)
    msg = os.path.join(base, "wxid_judge0001", "db_storage", "message")
    os.makedirs(msg, exist_ok=True)
    for i in range(n_db):
        p = os.path.join(msg, "message_%d.db" % i)
        with open(p, "wb") as f:
            f.write(b"x")
        if mtime:
            os.utime(p, (mtime, mtime))
    return base


ROOT_TMP = tempfile.mkdtemp(prefix="pm_wechatdir_")
_saved_cands = W._db_dir_candidates
_saved_cfg_file = C.CONFIG_FILE
_saved_cfg = C.get_config()
try:
    print("── A. 校验：存在 + 里面有 db_storage 或消息库文件 ──")
    good = make_xwechat_files(ROOT_TMP, "only_custom")
    empty = os.path.join(ROOT_TMP, "empty_dir")
    os.makedirs(empty, exist_ok=True)
    missing = os.path.join(ROOT_TMP, "no_such_dir")
    a1 = D.check(good)
    ok("有 db_storage 且有 .db ⇒ 可用", a1["ok"] is True and a1["dbs"] >= 1, str(a1))
    a2 = D.check(empty)
    ok("目录存在但里面没有库文件 ⇒ 不可用", a2["ok"] is False)
    ok("不可用时**原因非空**（防静默）", bool(str(a2["why"]).strip()), a2["why"])
    a3 = D.check(missing)
    ok("目录不存在 ⇒ 不可用", a3["ok"] is False and a3["exists"] is False)
    ok("不存在时原因说清「目录不存在」", "不存在" in str(a3["why"]), a3["why"])
    a4 = D.check("")
    ok("没填 ⇒ 不可用且有原因", a4["ok"] is False and bool(str(a4["why"]).strip()), a4["why"])
    a5 = D.check(os.path.join(good, "wxid_judge0001", "db_storage"))
    ok("填到 db_storage 这一层也算对（既有口径：填哪一层都算对）", a5["ok"] is True, str(a5)[:90])
    a6 = D.check("%TEMP%")     # 环境变量要展开（既有 `_expand_path` 口径）
    ok("路径里的环境变量会被展开后再判", a6["path"] and "%TEMP%" not in a6["path"], a6["path"][:70])

    print("── B. 优先级：显式配置 > 扫盘最新可用 > 驱动库自探测 ──")
    # 造两个不同的"盘上目录"：older（旧）与 newer（新）——用 mtime 分出"最新"
    old_dir = make_xwechat_files(ROOT_TMP, "probe_old", mtime=1000000000)
    new_dir = make_xwechat_files(ROOT_TMP, "probe_new", n_db=2, mtime=2000000000)
    default_dir = make_xwechat_files(ROOT_TMP, "probe_default", mtime=1500000000)
    cands_all = [default_dir, old_dir, new_dir]

    def _patch_cands(seq):
        W._db_dir_candidates = lambda extra="": (([extra] if str(extra or "").strip() else []) + list(seq))

    _patch_cands([default_dir])
    b1 = D.decide("")
    ok("只有默认目录 ⇒ 用它，来源=自动检测",
       D._same(b1["effective"], default_dir) and b1["src"] == "scanned",
       "%s / %s" % (b1["effective"], b1["src"]))
    ok("这种情况没有回落原因（阴性对照：没坏就别报错）", b1["note"] == "", b1["note"])

    _patch_cands([default_dir])
    b2 = D.decide(good)
    ok("配置可用 ⇒ 用配置（哪怕扫盘也有别的）",
       D._same(b2["effective"], good) and b2["src"] == "config", "%s / %s" % (b2["effective"], b2["src"]))

    _patch_cands(cands_all)
    b3 = D.decide("")
    ok("没有任何配置时，扫盘取**最新的那个**可用目录",
       D._same(b3["effective"], new_dir) and b3["src"] == "scanned",
       "%s / %s" % (b3["effective"], b3["src"]))

    b4 = D.decide(missing)
    ok("配置目录不存在 ⇒ 回落到扫盘那个", D._same(b4["effective"], new_dir) and b4["src"] == "scanned",
       "%s / %s" % (b4["effective"], b4["src"]))
    ok("回落原因**非空**，且同时点名你填的那个与回落到哪",
       bool(b4["note"]) and missing in b4["note"] and b4["note"].count("回落") >= 1, b4["note"])
    ok("配置可用性如实标成 False 且原因非空",
       b4["configured_ok"] is False and bool(str(b4["configured_why"]).strip()), b4["configured_why"])

    _patch_cands([])
    b5 = D.decide(empty)
    ok("配置里有目录但没库文件 + 扫盘也没有 ⇒ 交回驱动库自探测（auto）",
       b5["effective"] == "" and b5["src"] == "auto", "%s / %s" % (b5["effective"], b5["src"]))
    ok("这种情况照样有非空原因（不许静默）", bool(b5["note"]) and "回落" in b5["note"], b5["note"])

    print("── C. 旧路径必须失效（本反馈的核心）──")
    _patch_cands(cands_all)
    before = D.decide(old_dir)            # 模拟：用户原来把"自定义地址"填成旧目录
    ok("先确认旧配置生效", D._same(before["effective"], old_dir) and before["src"] == "config",
       "%s / %s" % (before["effective"], before["src"]))
    after = D.decide(new_dir)             # 配置改成新目录之后
    ok("配置一改，用的是**新**目录", D._same(after["effective"], new_dir) and after["src"] == "config",
       "%s / %s" % (after["effective"], after["src"]))
    _tries = [d for d, _s in W.db_open_tries(new_dir)]
    ok("**旧目录不再出现在开库链上**（用户那句「他回我之前自定义的地址里去看文件了」的回归判据）",
       all(not D._same(d, old_dir) for d in _tries), "链上：%s" % _tries)
    ok("开库链的第一条就是新目录", bool(_tries) and D._same(_tries[0], new_dir), str(_tries[:1]))

    # config 层：外部改了盘上那份（手工编辑 / 另一个进程写的），内存里那份必须作废
    tmp_cfg = os.path.join(ROOT_TMP, "config.json")
    with open(tmp_cfg, "w", encoding="utf-8") as f:
        json.dump({"wechat": {"db_dir": old_dir}}, f, ensure_ascii=False)
    C.CONFIG_FILE = tmp_cfg
    c1 = C.reload_config()
    ok("读盘拿到旧值（A）", str((c1.get("wechat") or {}).get("db_dir")) == old_dir)
    import time as _t
    _t.sleep(0.02)
    with open(tmp_cfg, "w", encoding="utf-8") as f:
        json.dump({"wechat": {"db_dir": new_dir, "poll_interval": 9}}, f, ensure_ascii=False)
    c2 = C.get_config()
    ok("**盘上那份变了，内存里那份自动作废**（旧值不再返回）",
       str((c2.get("wechat") or {}).get("db_dir")) == new_dir, str((c2.get("wechat") or {}).get("db_dir")))
    ok("重读的是新内容（不是只有 db_dir 一个键对）",
       int((c2.get("wechat") or {}).get("poll_interval") or 0) == 9,
       str((c2.get("wechat") or {}).get("poll_interval")))
    ok("有显式的失效入口（reload_config）", callable(getattr(C, "reload_config", None)))

    # adapter 侧：长生命周期实例手里那份快照，接入前必须重取当前配置
    print("── C2. adapter 不许把旧配置抱一辈子 ──")
    _w6 = _src(os.path.join("agent", "wechat.py"))
    ok("`_init_db` 前会重取当前配置（refresh_cfg）", "self.refresh_cfg()" in _w6 and "def refresh_cfg(self)" in _w6)
    ok("显式传进来的 cfg 不被抢走（自检/夹具要能钉住输入）", "if self._cfg_local:" in _w6)

    print("── D. 保存闸：不过校验就不写盘 ──")
    _writes = []
    _saved_save_cfg = C.save_config
    C.save_config = lambda cfg=None: _writes.append(dict((cfg or {}).get("wechat") or {}))
    try:
        r1 = D.save(missing)
        ok("填不存在的目录 ⇒ ok=False", r1.get("ok") is False and r1.get("saved") is False, str(r1)[:90])
        ok("不通过时**一个字都不写盘**", _writes == [], str(_writes))
        ok("不通过时给出非空原因与回落目标",
           bool(str(r1.get("error") or "").strip()) and bool(str(r1.get("reason") or "").strip()),
           "%s / %s" % (r1.get("error"), r1.get("reason")))
        ok("不通过时点名**回落到哪个可用目录**（不许只说一句用不了）",
           D._same(r1.get("fallback"), new_dir), str(r1.get("fallback")))
        r1b = D.save(empty)
        ok("目录存在但没有库文件 ⇒ 同样拦下", r1b.get("ok") is False and _writes == [], str(r1b)[:90])
        r2 = D.save(good)
        ok("填可用目录 ⇒ ok=True 且写盘一次",
           r2.get("ok") is True and r2.get("saved") is True and len(_writes) == 1, str(r2)[:80])
        ok("写进去的值就是那个可用目录",
           bool(_writes) and D._same(_writes[0].get("db_dir"), good), str(_writes))
        r3 = D.save("")
        ok("清空 ⇒ 合法（回到自动检测）且写盘为空串",
           r3.get("ok") is True and bool(_writes) and _writes[-1].get("db_dir") == "", str(_writes[-1:]))
    finally:
        C.save_config = _saved_save_cfg

    print("── E. 暴露：报告 / 状态 / 控制台看的是「实际在读哪个」 ──")
    _patch_cands(cands_all)
    e1 = D.status(how={"dir": old_dir, "src": "scanned"}, explicit=new_dir)
    ok("有活 adapter 时以它的 `_db_how` 为准（实际在读哪个）",
       D._same(e1["effective"], old_dir) and e1["effective_from"] == "running", str(e1)[:110])
    ok("`now` 是给人看的那一个（不是配置值）", D._same(e1["now"], old_dir), e1["now"])
    e2 = D.status(how=None, explicit=missing)
    ok("没有活实例时按该用哪个给结论，并点名回落到哪",
       D._same(e2["effective"], new_dir) and "回落" in e2["text"], e2["text"][:110])
    ok("`text` 一行话术不含括号式解释", "（" not in e2["text"] and "(" not in e2["text"], e2["text"][:90])
    e3 = D.status(how=None, explicit=good)
    ok("配置可用时 text 说清在读哪个、来源是哪",
       D._same(e3["effective"], good) and "来源" in e3["text"], e3["text"][:90])
    ok("status **不带** candidates 键（否则控制台每 4 秒轮询会把候选列表擦掉）",
       "candidates" not in e3, str(sorted(e3.keys()))[:120])
    e4 = D.decide(missing)
    ok("真要清单时（decide/probe）候选逐个带「能不能用 + 为什么」",
       bool(e4["candidates"]) and all(("ok" in c and "why" in c and "path" in c) for c in e4["candidates"]),
       "%d 个候选" % len(e4["candidates"]))

    _ui = _src(os.path.join("agent", "webui.py"))
    ok("/api/status 带 wechat_dir", 'st["wechat_dir"] = ' in _ui and "wechat_dir as _wdir_s" in _ui)
    ok("/api/wechat/dir 端点在位（GET 与 POST 两条）", _ui.count('"/api/wechat/dir"') >= 2,
       str(_ui.count('"/api/wechat/dir"')))
    ok("POST 保存后会**立即重探**并把候选回显（不靠刷新页面）",
       '_st3["candidates"] = _wdir3.probe(' in _ui)
    ok("/api/config 保存时会拦下不可用的数据目录", "_wechat_dir_conflict" in _ui)
    _ch = _src(os.path.join("agent", "console_html.py"))
    _i_w = _ch.index('id="sec-wechat"')
    _i_n = _ch.index('<section id="sec-', _i_w + 10)
    _seg = _ch[_i_w:_i_n]
    ok("「微信数据目录」那一行铺在微信面板里",
       all(k in _seg for k in ('id="wxDirNow"', 'id="wxDirInput"', 'id="wxDirProbe"', 'id="wxDirSave"')),
       "段长 %d" % len(_seg))
    ok("前端会把 status 里的 wechat_dir 填进去", "renderWechatDir(s.wechat_dir" in _ch or "renderWechatDir(wd)" in _ch)
    ok("「自动检测」按钮真的调探测接口", "probeWechatDir" in _ch and "/api/wechat/dir" in _ch)
    ok("「保存并重探」按钮走 POST 并把结果回显", "保存失败：" in _ch and "renderWechatDir(r.wechat_dir)" in _ch)
    ok("保存被服务端拒了**不许说已保存**（控制台会如实报错）",
       "rs.ok === false" in _ch and "有一项没通过校验" in _ch)
    # 前端 JS 真过 `node --check`：字符串断言**抓不到语法错**，而 JS 一坏整页就没反应
    # （既有口径见 `autosave_ui_selftest.py` C 段，这里同款照做）
    import re as _re
    import subprocess as _sp
    try:
        from agent import console_html as _CHM
        _html = _CHM.HTML
    except Exception:
        _html = _ch
    _js_all = "\n;\n".join(_re.findall(r"<script[^>]*>(.*?)</script>", _html, _re.S))
    _js_tmp = os.path.join(ROOT_TMP, "console_check.js")
    with open(_js_tmp, "w", encoding="utf-8") as _f:
        _f.write(_js_all)
    try:
        _r = _sp.run(["node", "--check", _js_tmp], capture_output=True, text=True, timeout=60,
                     creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
        ok("前端 JS 过 node --check", _r.returncode == 0,
           (_r.stderr or "").strip().splitlines()[-1][:110] if _r.returncode else "%d 字符" % len(_js_all))
    except Exception as _e:
        ok("前端 JS 过 node --check", False, "%s: %s" % (type(_e).__name__, _e))
    _rep = _src(os.path.join("scripts", "collect_report.py"))
    ok("报告里印的是实际在读 + 来源，配置不可用时单独告警",
       "微信数据目录: 实际在读" in _rep and "不可用" in _rep and "wechat_dir" in _rep)
    ok("报告不再因为库打不开而整段退化成取不到",
       "_how_err" in _rep and "打不开或取不到" in _rep)

    print("── F. 判据不瞎（阴性对照）──")
    _patch_cands(cands_all)
    f1 = D.decide(good)
    ok("可用目录 ⇒ 回落原因为空", f1["note"] == "", f1["note"])
    ok("可用目录 ⇒ configured_ok=True 且 why 为空",
       f1["configured_ok"] is True and f1["configured_why"] == "", f1["configured_why"])
    f2 = D.check(empty)
    ok("A 段那条原因非空不是恒真（把空目录换成可用目录就为空）",
       f2["why"] != "" and D.check(good)["why"] == "", "%r / %r" % (f2["why"], D.check(good)["why"]))
    # 阴性对照（这条最要紧）：把 config 换回**旧版实现**（内存里那份永不失效），
    # C 段那两条"旧值必须作废"的断言必须能变红 —— 证明它们抓得住这次要修的回归。
    _real_get = C.get_config

    def _legacy_get():
        if C._current_config is None:
            C._current_config = C.load_config()
        return C._current_config

    try:
        C.CONFIG_FILE = tmp_cfg
        with open(tmp_cfg, "w", encoding="utf-8") as _f:
            json.dump({"wechat": {"db_dir": old_dir}}, _f, ensure_ascii=False)
        C.reload_config()                       # 先让内存里装的是旧值
        with open(tmp_cfg, "w", encoding="utf-8") as _f:
            json.dump({"wechat": {"db_dir": new_dir}}, _f, ensure_ascii=False)
        C.get_config = _legacy_get
        _legacy_val = str((C.get_config().get("wechat") or {}).get("db_dir") or "")
        ok("阴性对照：旧版实现（内存不失效）确实还返回旧值 ⇒ C 段那条判据抓得住回归",
           _legacy_val == old_dir, _legacy_val)
        _legacy_dec = D.decide(None)            # explicit=None ⇒ 现读当前配置
        ok("阴性对照：旧缓存下决策层拿到的也是旧目录（回归会从决策层就红）",
           D._same(_legacy_dec["configured"], old_dir), _legacy_dec["configured"])
    finally:
        C.get_config = _real_get
finally:
    W._db_dir_candidates = _saved_cands
    C.CONFIG_FILE = _saved_cfg_file
    C.set_config(_saved_cfg)
    shutil.rmtree(ROOT_TMP, ignore_errors=True)

print("\n微信数据目录判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
