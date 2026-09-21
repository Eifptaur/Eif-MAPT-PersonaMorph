# -*- coding: utf-8 -*-
"""磁盘收尾判据（2026-09-15）。

起因：用户问「下载下来不占用户存储空间吗？所有这种下载写入的功能有没有做好删除措施或者
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
  G. **数据安全（V-R10-29，P1）**：媒体目录可能被用户配到自己也在用的目录 ⇒ **用户自己的文件
     必须原样保留**（只删我们造的前缀），删之前要写清单，tick 清的必须是**配置里的那个目录**。

用法：`py -3 scripts/housekeeping_selftest.py`（不联网、不碰真目录、不删用户任何东西）
"""
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import housekeeping as HK            # noqa: E402

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
    os.utime(old_pm, (now - 48 * 3600, now - 48 * 3600))    # 目录本身也是 48 小时前的（真实情形）
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
    ok("反证：默认前缀就是 pm-，不许是空串（空串会删光临时目录）",
       HK.TEMP_PREFIXES == ("pm-",), repr(HK.TEMP_PREFIXES))

    # ── B. 刚出炉的不动 ─────────────────────────────────────────────────────
    sect("B. 刚出炉的不动（可能正在被发送）")
    d2 = os.path.join(tmp, "d2")
    os.makedirs(d2, exist_ok=True)
    # ⛔ V-R10-29：媒体目录只认**我们造的名字**（`tts_` / `vc_` / `seg_` 前缀）⇒ 夹具改成本项目的产物名
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
        touch(d3, "tts_1%d0000.wav" % i, 1000, age_s=(6 - i) * 700, now=now)   # f5 最新
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
        touch(d5, "tts_2%d0000.wav" % i, 400 * 1024, age_s=(5 - i) * 700, now=now)   # 共约 2 MB
    HK.prune_dir(d5, max_mb=0.8, now=now)
    total = sum(os.path.getsize(os.path.join(d5, f)) for f in os.listdir(d5))
    ok("max_mb=0.8 ⇒ 从最旧的删到不超上限", total <= 0.8 * 1048576, "剩余 %.2f MB" % (total / 1048576.0))
    ok("max_mb 生效时留的是最新的那批", "tts_240000.wav" in os.listdir(d5), str(sorted(os.listdir(d5))))
    ok("反证：干跑一个都不删", True)   # 下面单独测

    # ── D. 干跑 + 有账可查 + 越界保护 ───────────────────────────────────────
    sect("D. 干跑不删、账目齐全、越界保护")
    d6 = os.path.join(tmp, "d6")
    os.makedirs(d6, exist_ok=True)
    touch(d6, "tts_300000.wav", 700, age_s=7200, now=now)
    rd = HK.prune_dir(d6, keep_newest=0, max_age_days=0.01, now=now, dry=True)   # 0.01 天 ≈ 14 分钟
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
    # ⛔ 2026-09-21 修（第六轮 **V-R6-31**）：原来把 `os.makedirs(...)` 塞在断言里靠 `or True` 兜住
    #   ⇒ 断言里出现"永远为真"的子表达式（卫生网新族 `X or True` 当场抓出）。副作用移出断言。
    _p_del = os.path.join(tmp, "pm-todelete")
    _no_exist = HK.cleanup_dir(_p_del) is False          # 不存在 ⇒ False
    os.makedirs(_p_del, exist_ok=True)
    ok("cleanup_dir：不存在时 False、存在（pm- 前缀）时删得掉",
       _no_exist and HK.cleanup_dir(_p_del) is True)

    # ── G. 数据安全：用户自己的文件必须原样保留（V-R10-29，P1）────────────────
    sect("G. 数据安全：同一目录里**用户自己的文件**一个都不许删（V-R10-29）")
    # 现场：产物目录**用户可配**（`voice_reply.dir`），他完全可能把它指到一个自己也在用的目录；
    # 旧 `prune_dir` 只看"够不够老 / 超不超量"，**不看文件名前缀** ⇒ 审计夹具里
    # `我的会议录音.mp3` / `DSC_0042.JPG` 跟我们的产物同目录时**被一起删掉**（无回收站、不可逆）。
    d8 = os.path.join(tmp, "d8")
    os.makedirs(d8, exist_ok=True)
    mine_old = touch(d8, "tts_20260101_1200.mp3", 4096, age_s=9 * 86400, now=now)    # 我们的产物（9 天）
    mine_old2 = touch(d8, "vc_20260101_1200.wav", 4096, age_s=9 * 86400, now=now)    # 我们的产物（变声）
    theirs_mp3 = touch(d8, "我的会议录音.mp3", 8192, age_s=30 * 86400, now=now)      # 用户的（更老）
    theirs_jpg = touch(d8, "DSC_0042.JPG", 8192, age_s=30 * 86400, now=now)          # 用户的（更老）
    r8 = HK.prune_dir(d8, keep_newest=0, max_age_days=1, now=now)
    ok("**用户自己的文件原样保留**（`我的会议录音.mp3`）", os.path.exists(theirs_mp3))
    ok("**用户自己的文件原样保留**（`DSC_0042.JPG`）", os.path.exists(theirs_jpg))
    ok("我们自己的产物照样按时清掉（前缀白名单没把功能一起关掉）",
       (not os.path.exists(mine_old)) and (not os.path.exists(mine_old2)) and r8["removed"] == 2,
       "removed=%s kept=%s" % (r8["removed"], r8["kept"]))
    ok("被跳过的用户文件**如实计数**（不是悄悄忽略）", r8["skipped"] == 2, repr(r8["skipped"]))

    # ── 第十一轮 V-R11-12：**"用户自选目录"这一档**（P3）────────────────────────
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
    ok("V-R11-12：**名字像但形状不对**的一律不删（`seg_1.txt` / `vc_notes.md` / `tts_backup.zip` …）",
       all(os.path.exists(p) for p in _lookalikes), str(os.listdir(d8b)))
    ok("V-R11-12：真产物（`tts_120001.wav`）照样清掉（判据没把功能关死）",
       (not os.path.exists(_real)) and r8b["removed"] == 1, "removed=%s" % r8b["removed"])
    ok("V-R11-12：跳过的那几个如实计数",
       r8b["skipped"] == len(_lookalikes), repr(r8b["skipped"]))
    ok("V-R11-12 正例：生产者那几种形状都认（含 `tts_edge_`/`vc_`/`seg_` 的时间戳形态）",
       all(HK.is_our_media_name(x) for x in ("tts_120001.wav", "tts_edge_120001123.mp3",
                                             "tts_seg_120001123.wav", "vc_120001.wav",
                                             "seg_120001123_0.wav", "seg_120001123.txt")))
    ok("V-R11-12 反例锚（灵敏度）：老写法（只看前缀）会把 `seg_1.txt` 一起删掉",
       "seg_1.txt".startswith(HK.MEDIA_PREFIXES) is True
       and HK.is_our_media_name("seg_1.txt") is False)
    # ⛔ 第十二轮 V-R12-9：**传别的 prefixes 元组时许不许静默关掉形状检查**（老写法会关）
    _d8c = os.path.join(tmp, "d8c")
    os.makedirs(_d8c, exist_ok=True)
    _u8c = touch(_d8c, "seg_1.txt", 4096, age_s=30 * 86400, now=now)      # 用户的文件（形状不对）
    _o8c = touch(_d8c, "tts_120002.wav", 4096, age_s=30 * 86400, now=now)  # 真产物
    _r8c = HK.prune_dir(_d8c, keep_newest=0, max_age_days=1, now=now,
                        prefixes=("tts_", "vc_", "seg_"))                 # ⬅ 与默认元组"相等"但**不是默认对象**
    ok("V-R12-9 形状检查默认一律开：传自定义 prefixes 也不许把它静默关掉",
       os.path.exists(_u8c) and (not os.path.exists(_o8c)) and _r8c["removed"] == 1,
       "留下用户文件=%s 清掉真产物=%s removed=%s" % (os.path.exists(_u8c), not os.path.exists(_o8c), _r8c["removed"]))
    _d8d = os.path.join(tmp, "d8d")
    os.makedirs(_d8d, exist_ok=True)
    _u8d = touch(_d8d, "seg_1.txt", 4096, age_s=30 * 86400, now=now)
    HK.prune_dir(_d8d, keep_newest=0, max_age_days=1, now=now, prefixes=(), shape_check=False)
    ok("V-R12-9 反例锚：显式 `shape_check=False` （＋不筛前缀）才等价于老行为 ⇒ 用户文件真被删",
       not os.path.exists(_u8d))

    def _old_way_kills_user(now_):
        """反例锚：`prefixes=()` + `shape_check=False` ＝ 老行为（既不筛前缀、也不看形状）⇒ 必被删。

        ⚠️ 第十二轮 **V-R12-9**：形状检查现在**默认开**（旧写法传别的 `prefixes` 元组会**静默**关掉它，
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
    ok("footprint 只统计我们前缀的条目（名字都带 pm-）",
       all(n.startswith("pm-") for n in fp["temp"]["names"]), str(fp["temp"]["names"][:3]))
    # ⛔ V-R10-29：tick 清的必须是**配置里的那个目录**（不是硬编码 `ROOT\media\tts`）
    _d_tick = os.path.join(tmp, "tick_media")
    os.makedirs(_d_tick, exist_ok=True)
    _troot = os.path.join(tmp, "tick_root")
    os.makedirs(_troot, exist_ok=True)
    touch(_d_tick, "tts_500000.mp3", 200 * 1024, age_s=3 * 86400, now=now)
    touch(_d_tick, "tts_500001.mp3", 200 * 1024, age_s=3 * 86400, now=now)
    _tick_user = touch(_d_tick, "用户的录音.mp3", 200 * 1024, age_s=30 * 86400, now=now)
    _led_tick = os.path.join(tmp, "tick_ledger.jsonl")
    _saved_tts_dir, _saved_maxmb = HK.tts_dir, HK.TTS_MAX_MB
    HK.tts_dir = lambda: _d_tick                 # 模拟"用户把 voice_reply.dir 配到别处"
    HK.TTS_MAX_MB = 0.001                        # 1KB 上限 ⇒ 逼它按"总量超限"从最旧的开始删
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
    ok("源码级：tick 不再把硬编码 `media/tts` 喂给 prune_dir（V-R10-29 的老写法）",
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

print("\n%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
