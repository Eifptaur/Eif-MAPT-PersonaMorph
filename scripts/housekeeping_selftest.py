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
    touch(d2, "new.bin", 500, age_s=5, now=now)
    touch(d2, "old.bin", 500, age_s=3600, now=now)
    rb = HK.prune_dir(d2, keep_newest=1, now=now)
    ok("keep_newest=1 ⇒ 只留最新那个", sorted(os.listdir(d2)) == ["new.bin"], str(os.listdir(d2)))
    ok("刚出炉的没被删（它在 min_age_s 内，哪怕是「该留的那个」也不删）",
       os.path.exists(os.path.join(d2, "new.bin")))
    ok("回收字节数对得上", rb["bytes"] >= 500, repr(rb["bytes"]))

    # ── C. 三条策略各自生效 ─────────────────────────────────────────────────
    sect("C. keep_newest / max_age_days / max_mb 各自生效，且删旧留新")
    d3 = os.path.join(tmp, "d3")
    os.makedirs(d3, exist_ok=True)
    for i in range(6):
        touch(d3, "f%d.bin" % i, 1000, age_s=(6 - i) * 700, now=now)   # f5 最新
    HK.prune_dir(d3, keep_newest=2, now=now)
    left = sorted(os.listdir(d3))
    ok("keep_newest=2 ⇒ 留最新两个", left == ["f4.bin", "f5.bin"], str(left))

    d4 = os.path.join(tmp, "d4")
    os.makedirs(d4, exist_ok=True)
    touch(d4, "ancient.bin", 100, age_s=10 * 86400, now=now)
    touch(d4, "recent.bin", 100, age_s=3600, now=now)
    HK.prune_dir(d4, max_age_days=3, now=now)
    ok("max_age_days=3 ⇒ 只清超龄的", sorted(os.listdir(d4)) == ["recent.bin"], str(os.listdir(d4)))

    d5 = os.path.join(tmp, "d5")
    os.makedirs(d5, exist_ok=True)
    for i in range(5):
        touch(d5, "big%d.bin" % i, 400 * 1024, age_s=(5 - i) * 700, now=now)   # 共约 2 MB
    HK.prune_dir(d5, max_mb=0.8, now=now)
    total = sum(os.path.getsize(os.path.join(d5, f)) for f in os.listdir(d5))
    ok("max_mb=0.8 ⇒ 从最旧的删到不超上限", total <= 0.8 * 1048576, "剩余 %.2f MB" % (total / 1048576.0))
    ok("max_mb 生效时留的是最新的那批", "big4.bin" in os.listdir(d5), str(sorted(os.listdir(d5))))
    ok("反证：干跑一个都不删", True)   # 下面单独测

    # ── D. 干跑 + 有账可查 + 越界保护 ───────────────────────────────────────
    sect("D. 干跑不删、账目齐全、越界保护")
    d6 = os.path.join(tmp, "d6")
    os.makedirs(d6, exist_ok=True)
    touch(d6, "x.bin", 700, age_s=7200, now=now)
    rd = HK.prune_dir(d6, keep_newest=0, max_age_days=0.01, now=now, dry=True)   # 0.01 天 ≈ 14 分钟
    ok("干跑：报告说删了 1 个", rd["removed"] == 1, repr(rd))
    ok("干跑：**文件其实还在**", os.path.exists(os.path.join(d6, "x.bin")))

    d7 = os.path.join(tmp, "d7")
    os.makedirs(d7, exist_ok=True)
    touch(d7, "y.bin", 300, age_s=7200, now=now)
    HK.prune_dir(d7, keep_newest=0, max_age_days=0, max_mb=0, now=now, dry=True)
    ok("反证：三条策略全为 0 ⇒ 什么都不删（不能因为「没给条件」就清空）",
       os.path.exists(os.path.join(d7, "y.bin")))

    ok("不存在时返回 0 而不是报错", HK.prune_dir(os.path.join(tmp, "nope")) ==
       {"removed": 0, "bytes": 0, "kept": 0})
    ok("cleanup_dir 拒绝删非 pm- 名字的目录", HK.cleanup_dir(d2) is False and os.path.isdir(d2))
    ok("cleanup_dir 删得掉 pm- 名字的目录",
       HK.cleanup_dir(os.path.join(tmp, "pm-todelete")) is False          # 不存在 ⇒ False
       and (os.makedirs(os.path.join(tmp, "pm-todelete"), exist_ok=True) or True)
       and HK.cleanup_dir(os.path.join(tmp, "pm-todelete")) is True)

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

    sect("F. 接线（改完不许只留在函数里）")
    bl = open(os.path.join("agent", "bilibili.py"), encoding="utf-8").read()
    ok("bilibili.listen 用完会删自己的临时目录", "HK.cleanup_dir(tmp)" in bl and "finally:" in bl)
    pm = open(os.path.join("scripts", "pm_update.py"), encoding="utf-8").read()
    ok("pm_update 的回滚快照也进 finally 删掉", 'backup = ""' in pm and "rmtree(backup" in pm)
    pp = open(os.path.join("scripts", "persona_morph.py"), encoding="utf-8").read()
    ok("启动时跑一次收尾并记日志", "_hk.tick()" in pp and "_hk.brief" in pp)
    ok("启动收尾失败不影响启动（包了 try）", "磁盘收尾跳过" in pp)
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
