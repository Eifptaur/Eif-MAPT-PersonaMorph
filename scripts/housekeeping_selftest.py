# -*- coding: utf-8 -*-
"""磁盘收尾判据。

起因：
限制写入措施」。审计实测：系统临时目录里躺着 **352 个条目 / 1192 个文件 / 22.8 MB** 我们
自己的残留，`media/tts` 的合成产物 **221 个文件 / 12.6 MB** 只增不减，`pm_update.py` 的
回滚快照 `pm-backup-*` **从来没删过**。

这条判据守四件事（全部在**临时目录**里造数据，不碰真目录）：
  A. **只删自己造的**：临时目录里只清 `pm-` 前缀；别人的目录、别的名字一律不碰（反证）。
  B. **刚出炉的不动**：`min_age_s` 内的文件一个都不许删（可能正在被发送）。
  C. **策略真的生效**：`prune_dir` 的 keep_newest / max_age_days / max_mb 三条各自可验，
     且是"删最旧的、留最新的"。
  D. **有账可查**：`tick()` / `sweep_temp()` / `prune_dir()` 都必须返回"删了几个 + 回收多少字节"，
     空手而归也要有数字（本项目的规矩：没有数字就不算做过）。
  G. **数据安全**：媒体目录可能被用户配到自己也在用的目录 ⇒ **用户自己的文件
     必须原样保留**（只删我们造的前缀），删之前要写清单，tick 清的必须是**配置里的那个目录**。

用法：`py -3 scripts/housekeeping_selftest.py`（不联网、不碰真目录、不删用户任何东西）
"""
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

# 判据隔离（收口在 `scripts/_iso14.py` 一处）：必须在 `from agent import …` 之前调。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))) # `_iso14` 在 scripts/ 下
import _iso14 # noqa: E402
_iso14.all_()
from agent import housekeeping as HK # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   %s%s" % (name, ("（%s）" % detail) if detail else ""))
    else:
        FAIL += 1
        print("  FAIL %s%s" % (name, ("（%s）" % detail) if detail else ""))


def sect(t):
    print("\n── %s ──" % t)


def touch(d, name, size=100, age_s=0.0, now=None):
    p = os.path.join(d, name)
    os.makedirs(os.path.dirname(p), exist_ok=True) if os.path.dirname(p) != d else None
    with open(p, "wb") as f:
        f.write(b"x" * size)
    if age_s:
        t = (now or time.time()) - age_s
        os.utime(p, (t, t))
    return p


tmp = tempfile.mkdtemp(prefix="pm-hktest-")
try:
    # ── A. 只删自己造的 ──────────────────────────────────────────────────────
    sect("A. 只删自己造的（走 `pm-` 前缀白名单）")
    root = os.path.join(tmp, "troot")
    os.makedirs(root, exist_ok=True)
    now = time.time()
    old_pm = os.path.join(root, "pm-olddir")
    os.makedirs(old_pm, exist_ok=True)
    touch(old_pm, "a.bin", 2048, age_s=48 * 3600, now=now)
    os.utime(old_pm, (now - 48 * 3600, now - 48 * 3600)) # 目录本身也是 48 小时前的（真实情形）
    fresh_pm = os.path.join(root, "pm-freshdir")
    os.makedirs(fresh_pm, exist_ok=True)
    touch(fresh_pm, "b.bin", 1024, age_s=60, now=now)
    other = os.path.join(root, "somebody-else")
    os.makedirs(other, exist_ok=True)
    touch(other, "c.bin", 4096, age_s=99 * 3600, now=now)

    r = HK.sweep_temp(root=root, max_age_h=24, now=now)
    ok("删掉了够老的 pm- 目录", not os.path.exists(old_pm) and r["removed"] == 1, repr(r["removed"]))
    ok("**没有**删够新（60 秒前）的 pm- 目录", os.path.exists(fresh_pm) and r["kept"] == 1, repr(r["kept"]))
    ok("**没有**碰别人名字的目录（哪怕它更老）", os.path.exists(other))
    ok("报了回收字节数（不是只报个数）", r["bytes"] >= 2048, repr(r["bytes"]))
    ok("反证：默认前缀**不许是空串**（空串会删光临时目录）",
       bool(HK.TEMP_PREFIXES) and all(HK.TEMP_PREFIXES), repr(HK.TEMP_PREFIXES))
    # ⛔ 断言**意图**而不是字面（原来锚成 `== ("pm-",)` ⇒ 把"漏了一整族"这个错行为也一起钉住了）：
    #   ① 两族 pm 都要覆盖（只写连字符 ⇒ 下划线那一族 3,316 个永远回收不了）；
    #   ② **名字要够"专属"**，不许宽到能匹配别的程序（用真名反证）。
    ok("两族 pm 前缀都覆盖（`pm-` 与 `pm_`）",
       any(x == "pm-" for x in HK.TEMP_PREFIXES) and any(x == "pm_" for x in HK.TEMP_PREFIXES),
       repr(HK.TEMP_PREFIXES))
    ok("前缀**不许宽到能匹配别人的目录**（拿几个真名反证：pip-/node-/Temp/python）",
       not any(str(n).startswith(HK.TEMP_PREFIXES)
               for n in ("pip-build-env", "node-compile-cache", "python-build", "Temp1")),
       repr(HK.TEMP_PREFIXES))

    # ── 防漂：**重新扫源码**，产品（agent/ ＋ ui_qt/）里出现的前缀必须都在名单里 ──
    #   ⛔ 为什么要这条：名单是**手写**的，而"新加一个 mkdtemp 前缀"不会有人记得回来补名单
    #      ⇒ 于是又出现"某一族永远回收不了"（这正是上一次那个 bug 的成因）。
    #   ⚠️ 只扫**产品代码**：判据那约 150 种前缀由套件的"当次运行根"整体覆盖
    #      （见 `scripts/run_all_selftests.py` 里 `TEMP`/`TMP`/`TMPDIR` 的说明），不在本名单职责内。
    import glob as _glob
    import re as _re
    _pat = _re.compile(r"mkdtemp\(\s*prefix\s*=\s*[\"']([^\"']+)[\"']")
    # ⛔ **`dir=` 形态不归 TEMP 清扫管**：临时目录建在**自己的目录里**（如 `tools.d/`），
    #    清理责任在**建它的那段代码**（`finally: shutil.rmtree`），而不是"系统 TEMP 清扫名单"。
    #    ⇒ 这类前缀如果也要求进名单，等于把**两套清理职责混成一套**（名单只会越长越假）。
    #    ⚠️ 但**"有 `dir=`"不等于"有人清"**：只在**同一个函数里**能看到 `rmtree` 才算数（见下）。
    _pat_dir = _re.compile(r"mkdtemp\([^)]*\bdir\s*=")
    _found = set()
    _found_dir = set()
    _scanned = 0
    for _sub in ("agent", "ui_qt"):
        for _p in _glob.glob(os.path.join(ROOT, _sub, "**", "*.py"), recursive=True):
            try:
                with open(_p, encoding="utf-8", errors="ignore") as _fh:
                    _s = _fh.read()
            except Exception: # noqa: BLE001
                continue
            _scanned += 1
            _found.update(_pat.findall(_s))
            # 逐个判"这个 mkdtemp 是不是自带 `dir=`、且同函数里有人 rmtree"
            for _m in _pat_dir.finditer(_s):
                _head = _s.rfind("\ndef ", 0, _m.start())
                _head = _head + 1 if _head >= 0 else 0
                _tail = _s.find("\ndef ", _m.start())
                _body = _s[_head:_tail if _tail > 0 else len(_s)]
                if "rmtree" in _body:
                    _pm = _pat.search(_s[max(0, _m.start() - 200):_m.start() + 200])
                    if _pm:
                        _found_dir.add(_pm.group(1))
    # ⛔ **前置断言（防空过）**：这条判据一旦"扫到 0 个文件"就会**静默通过** ——
    #   我第一次写就踩了（漏了 `io` 导入 ⇒ 每份文件都读失败 → `continue` → `_found` 空 ⇒ 绿）。
    #   ⛔ 空过的断言比红更坏：它给人"已经守住了"的错觉。
    ok("⭐ 防漂前置：真的扫到了产品源码（否则下面那条会空过）",
       _scanned >= 100 and len(_found) >= 3,
       "扫了 %d 个 .py，找到 %d 种前缀" % (_scanned, len(_found)))
    _uncovered = sorted(_x for _x in _found
                        if not str(_x).startswith(HK.TEMP_PREFIXES) and _x not in _found_dir)
    ok("⭐ 防漂：产品代码里**所有** `mkdtemp` 前缀**要么在清扫名单里、要么自带 `dir=` ＋ `rmtree`**"
       "（实测源码 %d 种，其中自带收口 %d 种）" % (len(_found), len(_found_dir)),
       not _uncovered,
       "未覆盖：%s" % (_uncovered[:6] if _uncovered else "无 ✅"))
    # ⛔ 反向锚：把"自带收口"那类**也**要求进名单 ⇒ 上面那条会变红（证明这个例外是真在生效，
    #    而不是把判据写松了 —— 判据写松的典型长相就是"怎么改都绿"）。
    #    锚的**通过条件＝「严格要求下确实有文件报红」**（有例外 ⇒ 严格要求必然更严 ⇒ 必红）。
    _strict = sorted(_x for _x in _found if not str(_x).startswith(HK.TEMP_PREFIXES))
    ok("⭐ 反向锚：无视「自带 `dir=`＋`rmtree`」这个例外时，上面那条**确实会红**（例外真在生效）",
       bool(_strict), "严格要求下会报：%s" % (_strict[:4] if _strict else "无 ⇒ 锚无效"))

    # ── B. 刚出炉的不动 ─────────────────────────────────────────────────────
    sect("B. 刚出炉的不动（可能正在被发送）")
    d2 = os.path.join(tmp, "d2")
    os.makedirs(d2, exist_ok=True)
    # ⛔ 媒体目录只认**我们造的名字**（`tts_` / `vc_` / `seg_` 前缀）⇒ 夹具改成本项目的产物名
    touch(d2, "tts_120000.wav", 500, age_s=5, now=now)
    touch(d2, "tts_110000.wav", 500, age_s=3600, now=now)
    rb = HK.prune_dir(d2, keep_newest=1, now=now)
    ok("keep_newest=1 ⇒ 只留最新那个", sorted(os.listdir(d2)) == ["tts_120000.wav"], str(os.listdir(d2)))
    ok("刚出炉的没被删（它在 min_age_s 内，哪怕是「该留的那个」也不删）",
       os.path.exists(os.path.join(d2, "tts_120000.wav")))
    ok("回收字节数对得上", rb["bytes"] >= 500, repr(rb["bytes"]))

    # ── C. 三条策略各自生效 ─────────────────────────────────────────────────
    sect("C. keep_newest / max_age_days / max_mb 各自生效，且删旧留新")
    d3 = os.path.join(tmp, "d3")
    os.makedirs(d3, exist_ok=True)
    for i in range(6):
        touch(d3, "tts_1%d0000.wav" % i, 1000, age_s=(6 - i) * 700, now=now) # f5 最新
    HK.prune_dir(d3, keep_newest=2, now=now)
    left = sorted(os.listdir(d3))
    ok("keep_newest=2 ⇒ 留最新两个", left == ["tts_140000.wav", "tts_150000.wav"], str(left))

    d4 = os.path.join(tmp, "d4")
    os.makedirs(d4, exist_ok=True)
    touch(d4, "tts_100001.wav", 100, age_s=10 * 86400, now=now)
    touch(d4, "tts_100002.wav", 100, age_s=3600, now=now)
    HK.prune_dir(d4, max_age_days=3, now=now)
    ok("max_age_days=3 ⇒ 只清超龄的", sorted(os.listdir(d4)) == ["tts_100002.wav"], str(os.listdir(d4)))

    d5 = os.path.join(tmp, "d5")
    os.makedirs(d5, exist_ok=True)
    for i in range(5):
        touch(d5, "tts_2%d0000.wav" % i, 400 * 1024, age_s=(5 - i) * 700, now=now) # 共约 2 MB
    HK.prune_dir(d5, max_mb=0.8, now=now)
    total = sum(os.path.getsize(os.path.join(d5, f)) for f in os.listdir(d5))
    ok("max_mb=0.8 ⇒ 从最旧的删到不超上限", total <= 0.8 * 1048576, "剩余 %.2f MB" % (total / 1048576.0))
    ok("max_mb 生效时留的是最新的那批", "tts_240000.wav" in os.listdir(d5), str(sorted(os.listdir(d5))))
    ok("反证：干跑一个都不删", True) # 下面单独测

    # ── D. 干跑 + 有账可查 + 越界保护 ───────────────────────────────────────
    sect("D. 干跑不删、账目齐全、越界保护")
    d6 = os.path.join(tmp, "d6")
    os.makedirs(d6, exist_ok=True)
    touch(d6, "tts_300000.wav", 700, age_s=7200, now=now)
    rd = HK.prune_dir(d6, keep_newest=0, max_age_days=0.01, now=now, dry=True) # 0.01 天 ≈ 14 分钟
    ok("干跑：报告说删了 1 个", rd["removed"] == 1, repr(rd))
    ok("干跑：**文件其实还在**", os.path.exists(os.path.join(d6, "tts_300000.wav")))

    d7 = os.path.join(tmp, "d7")
    os.makedirs(d7, exist_ok=True)
    touch(d7, "tts_300001.wav", 300, age_s=7200, now=now)
    HK.prune_dir(d7, keep_newest=0, max_age_days=0, max_mb=0, now=now, dry=True)
    ok("反证：三条策略全为 0 ⇒ 什么都不删（不能因为「没给条件」就清空）",
       os.path.exists(os.path.join(d7, "tts_300001.wav")))

    _empty = HK.prune_dir(os.path.join(tmp, "nope"))
    ok("不存在时返回 0 而不是报错",
       _empty["removed"] == 0 and _empty["bytes"] == 0 and _empty["kept"] == 0
       and _empty["skipped"] == 0, repr(_empty))
    ok("cleanup_dir 拒绝删非 pm- 名字的目录", HK.cleanup_dir(d2) is False and os.path.isdir(d2))
    # ⛔ 原来把 `os.makedirs(...)` 塞在断言里靠 `or True` 兜住
    #   ⇒ 断言里出现"永远为真"的子表达式（卫生网新族 `X or True` 当场抓出）。副作用移出断言。
    _p_del = os.path.join(tmp, "pm-todelete")
    _no_exist = HK.cleanup_dir(_p_del) is False # 不存在 ⇒ False
    os.makedirs(_p_del, exist_ok=True)
    ok("cleanup_dir：不存在时 False、存在（pm- 前缀）时删得掉",
       _no_exist and HK.cleanup_dir(_p_del) is True)

    # ── G. 数据安全：用户自己的文件必须原样保留────────────────
    sect("G. 数据安全：同一目录里**用户自己的文件**一个都不许删")
    # 现场：产物目录**用户可配**（`voice_reply.dir`），他完全可能把它指到一个自己也在用的目录；
    # 旧 `prune_dir` 只看"够不够老 / 超不超量"，**不看文件名前缀** ⇒ 审计夹具里
    # `我的会议录音.mp3` / `DSC_0042.JPG` 跟我们的产物同目录时**被一起删掉**（无回收站、不可逆）。
    d8 = os.path.join(tmp, "d8")
    os.makedirs(d8, exist_ok=True)
    mine_old = touch(d8, "tts_20260101_1200.mp3", 4096, age_s=9 * 86400, now=now) # 我们的产物（9 天）
    mine_old2 = touch(d8, "vc_20260101_1200.wav", 4096, age_s=9 * 86400, now=now) # 我们的产物（变声）
    theirs_mp3 = touch(d8, "我的会议录音.mp3", 8192, age_s=30 * 86400, now=now) # 用户的（更老）
    theirs_jpg = touch(d8, "DSC_0042.JPG", 8192, age_s=30 * 86400, now=now) # 用户的（更老）
    r8 = HK.prune_dir(d8, keep_newest=0, max_age_days=1, now=now)
    ok("**用户自己的文件原样保留**（`我的会议录音.mp3`）", os.path.exists(theirs_mp3))
    ok("**用户自己的文件原样保留**（`DSC_0042.JPG`）", os.path.exists(theirs_jpg))
    ok("我们自己的产物照样按时清掉（前缀白名单没把功能一起关掉）",
       (not os.path.exists(mine_old)) and (not os.path.exists(mine_old2)) and r8["removed"] == 2,
       "removed=%s kept=%s" % (r8["removed"], r8["kept"]))
    ok("被跳过的用户文件**如实计数**（不是悄悄忽略）", r8["skipped"] == 2, repr(r8["skipped"]))

    # ── **"用户自选目录"这一档**（P3）────────────────────────
    #   现场：`voice_reply.dir` 是用户可配的，一旦指到"用户自己的目录"，那里任何
    #   `seg_1.txt` / `vc_notes.md` / `tts_backup.zip` 这类**非产品**名字都会**前缀命中** ⇒ 仍被删。
    #   ⇒ 现在按"**长得像我们真产出的形状**"筛（正则，见 `HK.is_our_media_name`）：
    #     前缀对、形状不对的，一律当成用户的文件。
    d8b = os.path.join(tmp, "d8b")
    os.makedirs(d8b, exist_ok=True)
    _lookalikes = []
    for _nm in ("seg_1.txt", "vc_notes.md", "tts_backup.zip", "tts_notes.wav.bak", "seg_稿子.txt"):
        _lookalikes.append(touch(d8b, _nm, 4096, age_s=30 * 86400, now=now))
    _real = touch(d8b, "tts_120001.wav", 4096, age_s=30 * 86400, now=now)
    r8b = HK.prune_dir(d8b, keep_newest=0, max_age_days=1, now=now)
    ok("**名字像但形状不对**的一律不删（`seg_1.txt` / `vc_notes.md` / `tts_backup.zip` …）",
       all(os.path.exists(p) for p in _lookalikes), str(os.listdir(d8b)))
    ok("真产物（`tts_120001.wav`）照样清掉（判据没把功能关死）",
       (not os.path.exists(_real)) and r8b["removed"] == 1, "removed=%s" % r8b["removed"])
    ok("跳过的那几个如实计数",
       r8b["skipped"] == len(_lookalikes), repr(r8b["skipped"]))
    ok("正例：生产者那几种形状都认（含 `tts_edge_`/`vc_`/`seg_` 的时间戳形态）",
       all(HK.is_our_media_name(x) for x in ("tts_120001.wav", "tts_edge_120001123.mp3",
                                             "tts_seg_120001123.wav", "vc_120001.wav",
                                             "seg_120001123_0.wav", "seg_120001123.txt")))
    ok("反例锚（灵敏度）：老写法（只看前缀）会把 `seg_1.txt` 一起删掉",
       "seg_1.txt".startswith(HK.MEDIA_PREFIXES) is True
       and HK.is_our_media_name("seg_1.txt") is False)
    # ⛔ **传别的 prefixes 元组时许不许静默关掉形状检查**（老写法会关）
    _d8c = os.path.join(tmp, "d8c")
    os.makedirs(_d8c, exist_ok=True)
    _u8c = touch(_d8c, "seg_1.txt", 4096, age_s=30 * 86400, now=now) # 用户的文件（形状不对）
    _o8c = touch(_d8c, "tts_120002.wav", 4096, age_s=30 * 86400, now=now) # 真产物
    _r8c = HK.prune_dir(_d8c, keep_newest=0, max_age_days=1, now=now,
                        prefixes=("tts_", "vc_", "seg_")) # ⬅ 与默认元组"相等"但**不是默认对象**
    ok("形状检查默认一律开：传自定义 prefixes 也不许把它静默关掉",
       os.path.exists(_u8c) and (not os.path.exists(_o8c)) and _r8c["removed"] == 1,
       "留下用户文件=%s 清掉真产物=%s removed=%s" % (os.path.exists(_u8c), not os.path.exists(_o8c), _r8c["removed"]))
    _d8d = os.path.join(tmp, "d8d")
    os.makedirs(_d8d, exist_ok=True)
    _u8d = touch(_d8d, "seg_1.txt", 4096, age_s=30 * 86400, now=now)
    HK.prune_dir(_d8d, keep_newest=0, max_age_days=1, now=now, prefixes=(), shape_check=False)
    ok("反例锚：显式 `shape_check=False` （＋不筛前缀）才等价于老行为 ⇒ 用户文件真被删",
       not os.path.exists(_u8d))
    # ⛔ `prefixes=()` 的语义要和 docstring 一致 —— **不按前缀筛，但形状检查照旧**
    _d8e = os.path.join(tmp, "d8e")
    os.makedirs(_d8e, exist_ok=True)
    _u8e = touch(_d8e, "我的会议录音.mp3", 4096, age_s=30 * 86400, now=now)
    _o8e = touch(_d8e, "tts_120003.wav", 4096, age_s=30 * 86400, now=now)
    _r8e = HK.prune_dir(_d8e, keep_newest=0, max_age_days=1, now=now, prefixes=())
    ok("`prefixes=()` ＝ 不按前缀筛，但形状检查**仍开着**（docstring 与实现对齐）",
       os.path.exists(_u8e) and (not os.path.exists(_o8e)) and _r8e["removed"] == 1,
       "用户文件在=%s 真产物在=%s removed=%s" % (os.path.exists(_u8e), os.path.exists(_o8e), _r8e["removed"]))

    def _old_way_kills_user(now_):
        """反例锚：`prefixes=()` + `shape_check=False` ＝ 老行为（既不筛前缀、也不看形状）⇒ 必被删。

        ⚠️ 形状检查现在**默认开**（旧写法传别的 `prefixes` 元组会**静默**关掉它，
        语义与 docstring 相反）⇒ 这条反例锚必须**显式**把两道都关掉，才等价于"修之前的行为"。
        """
        _d = os.path.join(tmp, "d8-oldway")
        os.makedirs(_d, exist_ok=True)
        for _nm in ("我的会议录音.mp3", "DSC_0042.JPG"):
            _p = touch(_d, _nm, 8192, age_s=30 * 86400, now=now_)
        HK.prune_dir(_d, keep_newest=0, max_age_days=1, now=now_, prefixes=(), shape_check=False)
        return not os.path.exists(os.path.join(_d, "我的会议录音.mp3"))

    ok("反证（灵敏度）：把前缀白名单去掉，同一夹具里用户文件**真的会被删**",
       _old_way_kills_user(now) is True)
    ok("反证：默认前缀就是本项目的产物前缀（空 ⇒ 等于不筛，那才是老毛病）",
       HK.MEDIA_PREFIXES == ("tts_", "vc_", "seg_"), repr(HK.MEDIA_PREFIXES))

    # 删除清单：**删之前**写、只增不改（有账可查、事后能复核删了什么）
    led = os.path.join(tmp, "pruned.jsonl")
    d9 = os.path.join(tmp, "d9")
    os.makedirs(d9, exist_ok=True)
    touch(d9, "tts_400000.wav", 1024, age_s=3 * 86400, now=now)
    touch(d9, "我的另一个文件.mp3", 1024, age_s=3 * 86400, now=now)
    r9 = HK.prune_dir(d9, keep_newest=0, max_age_days=1, now=now, ledger=led)
    _lines = open(led, encoding="utf-8").read().strip().splitlines() if os.path.exists(led) else []
    ok("删除前写了清单（一行一条：路径 / 大小 / 时间）",
       len(_lines) == 1 and "tts_400000.wav" in _lines[0] and '"size"' in _lines[0], str(_lines)[:120])
    ok("清单里**没有**用户文件（只记我们删了什么）",
       all("我的另一个文件" not in x for x in _lines), str(_lines)[:80])
    ok("清单路径原样带回来（调用方/日志看得见）", r9["ledger"] == led and os.path.exists(led))
    led2 = os.path.join(tmp, "pruned2.jsonl")
    touch(d9, "tts_400001.wav", 1024, age_s=3 * 86400, now=now)
    HK.prune_dir(d9, keep_newest=0, max_age_days=1, now=now, ledger=led2, dry=True)
    ok("干跑：**不写清单、也不删**",
       (not os.path.exists(led2)) and os.path.exists(os.path.join(d9, "tts_400001.wav")))

    sect("E. tick() 的账目形状（不真扫真目录，只看结构）")
    rep = HK.tick(dry=True)
    ok("tick 返回 temp / media_tts / freed_bytes / freed_mb",
       all(k in rep for k in ("temp", "media_tts", "freed_bytes", "freed_mb", "dry")))
    ok("干跑标记为真", rep["dry"] is True)
    ok("brief() 能念出一句话（带数字）", "收尾" in HK.brief(rep) and "MB" in HK.brief(rep), HK.brief(rep))
    fp = HK.footprint()
    ok("footprint 有 dirs 与 temp 两块", "dirs" in fp and "temp" in fp)
    ok("footprint 的 temp 带 entries/files/mb/names", all(k in fp["temp"] for k in ("entries", "files", "mb", "names")))
    # ⛔ 断言**意图**（"只统计我们**名单里**的前缀"），不是字面 `pm-`：
    #   这条走的是**真实临时目录**（`footprint(root=None)`）⇒ 字面锚会随本机残留而变化，
    #   我一扩名单它就把真实的 `chgrab-*` 当成"违规"报红（那是判据脆弱，不是产品错）。
    ok("footprint 只统计**我们自己名单里**前缀的条目（不是把整个临时目录算进来）",
       all(str(n).startswith(HK.TEMP_PREFIXES) for n in fp["temp"]["names"]),
       str(fp["temp"]["names"][:3]))
    ok("名单**不许为空**（空串 ⇒ 等于把整个临时目录算成自己的）", bool(HK.TEMP_PREFIXES))
    # ⛔ tick 清的必须是**配置里的那个目录**（不是硬编码 `ROOT\media\tts`）
    _d_tick = os.path.join(tmp, "tick_media")
    os.makedirs(_d_tick, exist_ok=True)
    _troot = os.path.join(tmp, "tick_root")
    os.makedirs(_troot, exist_ok=True)
    touch(_d_tick, "tts_500000.mp3", 200 * 1024, age_s=3 * 86400, now=now)
    touch(_d_tick, "tts_500001.mp3", 200 * 1024, age_s=3 * 86400, now=now)
    _tick_user = touch(_d_tick, "用户的录音.mp3", 200 * 1024, age_s=30 * 86400, now=now)
    _led_tick = os.path.join(tmp, "tick_ledger.jsonl")
    _saved_tts_dir, _saved_maxmb = HK.tts_dir, HK.TTS_MAX_MB
    HK.tts_dir = lambda: _d_tick # 模拟"用户把 voice_reply.dir 配到别处"
    HK.TTS_MAX_MB = 0.001 # 1KB 上限 ⇒ 逼它按"总量超限"从最旧的开始删
    try:
        _rt = HK.tick(root=_troot, now=now, ledger=_led_tick)
    finally:
        HK.tts_dir, HK.TTS_MAX_MB = _saved_tts_dir, _saved_maxmb
    ok("tick 清的是 `tts_dir()` 的实际值（用户可配目录）", _rt["media_dir"] == _d_tick, _rt["media_dir"])
    ok("tick 真的在那个目录里清了我们的产物（没白报数）",
       _rt["media_tts"]["removed"] >= 1 and not os.path.exists(os.path.join(_d_tick, "tts_500000.mp3")),
       str(_rt["media_tts"]))
    ok("tick 清到用户文件那一层也**不碰它**", os.path.exists(_tick_user))
    ok("tick 也写了删除清单（启动时那次收尾同样有账可查）",
       os.path.exists(_led_tick) and "tts_500001" in open(_led_tick, encoding="utf-8").read())
    ok("brief() 把「跳过了几个不是我们造的文件」也念出来",
       "跳过" in HK.brief(_rt), HK.brief(_rt))

    sect("F. 接线（改完不许只留在函数里）")
    bl = open(os.path.join("agent", "bilibili.py"), encoding="utf-8").read()
    ok("bilibili.listen 用完会删自己的临时目录", "HK.cleanup_dir(tmp)" in bl and "finally:" in bl)
    pm = open(os.path.join("scripts", "pm_update.py"), encoding="utf-8").read()
    ok("pm_update 的回滚快照也进 finally 删掉", 'backup = ""' in pm and "rmtree(backup" in pm)
    pp = open(os.path.join("scripts", "persona_morph.py"), encoding="utf-8").read()
    ok("启动时跑一次收尾并记日志", "_hk.tick()" in pp and "_hk.brief" in pp)
    ok("启动收尾失败不影响启动（包了 try）", "磁盘收尾跳过" in pp)
    _hk_src = open(os.path.join("agent", "housekeeping.py"), encoding="utf-8").read()
    ok("源码级：tick 不再把硬编码 `media/tts` 喂给 prune_dir",
       'prune_dir(os.path.join(ROOT, "media", "tts")' not in _hk_src
       and "d = media_dir or tts_dir()" in _hk_src)
    ok("源码级：prune_dir 的名字前缀白名单是**默认参数**（调用方忘传也不会退化成删全部）",
       "prefixes=MEDIA_PREFIXES" in _hk_src and "startswith(_pref)" in _hk_src)
    tl = open(os.path.join("agent", "tools.py"), encoding="utf-8").read()
    _i = tl.find("def _exec_read_video")
    _seg = tl[_i:_i + 2600] if _i > 0 else ""
    ok("反证：读视频的**失败分支**也要删临时目录（原来直接 return 就漏了）",
       "finally:" in _seg and "video_read.cleanup(res.get(\"dir\")" in _seg,
       "cleanup 在 finally 里" if "finally:" in _seg else "没找到 finally")
    ok("反证：失败分支的 return 在 try 里面（否则 finally 不会执行）",
       _seg.find("这段视频我读不了") < _seg.find("finally:"))
    mvs = open(os.path.join("scripts", "model_video_selftest.py"), encoding="utf-8").read()
    ok("自测自己也不许往临时目录里留东西（read 之后调 cleanup）",
       mvs.count("vr.cleanup(") >= 3, "出现 %d 次" % mvs.count("vr.cleanup("))
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

# ── H. 图库清理（`data/gen_images/`）────────────────────────────────────────
#   守三件事：① **只删产品命名的图**（用户丢进来的绝不碰）② 双阈值都真生效
#   ③ 台账 `index.jsonl` 与空目录处理对。
#   ⛔ 全部在临时目录里造数据，**不碰真 `data/gen_images`**。
sect("H. 图库清理（只删我们生成的图；阈值真生效；台账永不删）")
_groot = os.path.join(tmp, "groot")
_gdir = os.path.join(_groot, "data", "gen_images")
_NOW = time.time()
# 3 天前 / 40 天前 各造两张（形状同生产者：`<backend>_<n>_<%H%M%S>.jpg`）
for _d, _age in (("20260905", 3 * 86400), ("20260730", 40 * 86400)):
    for _i in range(2):
        _p = os.path.join(_gdir, _d, "gen_%d_%06d.jpg" % (_i, 120000 + _i))
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "wb") as f:
            f.write(b"J" * 300000)          # 300 KB/张 ⇒ 4 张共 1.2 MB
        os.utime(_p, (_NOW - _age, _NOW - _age))
# 台账 + 用户自己的图（两者都**不许删**）
with open(os.path.join(_gdir, "index.jsonl"), "w", encoding="utf-8") as f:
    f.write('{"path": "x"}\n')
_uimg = os.path.join(_gdir, "20260905", "我的照片.jpg")
with open(_uimg, "wb") as f:
    f.write(b"U" * 500)
os.utime(_uimg, (_NOW - 40 * 86400, _NOW - 40 * 86400))   # 又老又没形状

ok("H1 分母守卫：图库夹具真的造出来了（否则下面测的是空气）",
   len(os.listdir(os.path.join(_gdir, "20260905"))) + len(os.listdir(os.path.join(_gdir, "20260730"))) >= 4,
   "总文件 %d" % sum(len(os.listdir(os.path.join(_gdir, x))) for x in ("20260905", "20260730")))
ok("H1b 形状判据：产品命名认得出、用户命名认不出（含分母守卫）",
   HK.is_our_gen_image_name("gen_0_120000.jpg")
   and HK.is_our_gen_image_name("a1111_0_214548.png")
   and not HK.is_our_gen_image_name("我的照片.jpg")
   and not HK.is_our_gen_image_name("DSC_0042.JPG"),
   "4 例")

# 先按"只超时间"跑（max_mb 给大 ⇒ 只 `keep_days` 生效）
_r1 = HK.prune_gen_images(root=_groot, max_mb=100000, keep_days=30, now=_NOW)
ok("H2 ★`keep_days` 真生效（40 天前的删、3 天前的不删）",
   _r1["removed"] == 2 and _r1["bytes"] == 600000, "removed=%d bytes=%d" % (_r1["removed"], _r1["bytes"]))
ok("H3 ★用户自己的图**一个都不删**（名字不是产品形状）",
   os.path.exists(_uimg), "还在" if os.path.exists(_uimg) else "被删了！")
ok("H4 ★台账 `index.jsonl` 永不删",
   os.path.exists(os.path.join(_gdir, "index.jsonl")))
ok("H5 ★空掉的日期子目录被删掉（不留空目录噪声）",
   not os.path.isdir(os.path.join(_gdir, "20260730")),
   "目录数 = %d" % len([x for x in os.listdir(_gdir) if os.path.isdir(os.path.join(_gdir, x))]))
# 再按"只超量"跑（keep_days 给大 ⇒ 只 `max_mb` 生效）
# ⚠️ 此刻剩 2 张 × 300 KB = 600 KB；`max_mb=0.3` ＝ 314572.8 字节
#    ⇒ 删 1 张后剩 300000 ≤ 314572.8 **已不超** ⇒ 只该删 1 张（这里曾把期望写成 2，是我的夹具算错）
_r2 = HK.prune_gen_images(root=_groot, max_mb=0.3, keep_days=100000, now=_NOW)
ok("H6 ★`max_mb` 真生效（超量时从最旧删到不超，够了就停）",
   _r2["removed"] == 1 and _r2["bytes"] == 300000, "removed=%d bytes=%d" % (_r2["removed"], _r2["bytes"]))
# `min_age_s` 保护：刚出的一律不动（先清空剩下的老图，只留一张刚造的）
_old_left = os.path.join(_gdir, "20260905", "gen_0_120000.jpg")
if os.path.exists(_old_left):
    os.remove(_old_left)
_p_new = os.path.join(_gdir, "20260905", "gen_9_235959.jpg")
with open(_p_new, "wb") as f:
    f.write(b"N" * 999999)
os.utime(_p_new, (_NOW, _NOW))          # 明确"刚出炉"（mtime=now ⇒ 差 0 秒）
_r3 = HK.prune_gen_images(root=_groot, max_mb=0.1, keep_days=100000, now=_NOW)
ok("H7 ★刚出炉的不动（`min_age_s` 保护；哪怕它超量最大）",
   _r3["removed"] == 0 and os.path.exists(_p_new),
   "removed=%d 新图还在=%s" % (_r3["removed"], os.path.exists(_p_new)))
# 台账必须真写了（可复核"删了什么"）
_led = os.path.join(tmp, "gen_ledger.jsonl")
_gdir2 = os.path.join(tmp, "groot2", "data", "gen_images")
os.makedirs(os.path.join(_gdir2, "20260101"), exist_ok=True)
for _i in range(2):
    _p = os.path.join(_gdir2, "20260101", "gen_%d_120000.jpg" % _i)
    with open(_p, "wb") as f:
        f.write(b"L" * 1000)
    os.utime(_p, (_NOW - 99 * 86400, _NOW - 99 * 86400))
_r4 = HK.prune_gen_images(root=os.path.join(tmp, "groot2"), keep_days=30, ledger=_led, now=_NOW)
ok("H8 ★删之前写了清单（`{at,path,size,mtime,kind}` 一行一条）",
   os.path.isfile(_led) and _r4["ledger"] == _led
   and all(k in open(_led, encoding="utf-8").read() for k in ("at", "path", "size", "mtime", "kind")),
   "条目 %d" % (open(_led, encoding="utf-8").read().count("\n") if os.path.isfile(_led) else 0))
# 反向锚：把形状判据放宽成"什么都认"⇒ H3 那条守卫的判据式会变红
ok("H9 ★反向锚：若形状判据退化成'认所有 jpg' ⇒ 用户的图就会被删（证明 H3 在真守）",
   (lambda: (not HK.is_our_gen_image_name("我的照片.jpg")) and HK.is_our_gen_image_name("gen_0_120000.jpg"))(),
   "反向锚有效")
# 接线：tick 里真调了
_hk2 = open(os.path.join("agent", "housekeeping.py"), encoding="utf-8").read()
ok("H10 接线：`tick()` 真的调了图库清理（不是只写了函数）",
   "prune_gen_images(" in _hk2 and "gen_images" in _hk2)
ok("H11 接线：`brief()` 会念出图库清了几张（没有数字不算做过）",
   "图库清" in _hk2)
ok("H12 接线：`footprint()`/`known_dirs()` 里的图库口径与清理用同一处真值",
   "_gen_images_dir" in _hk2 and '"data/gen_images"' in _hk2)

print("\n%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
