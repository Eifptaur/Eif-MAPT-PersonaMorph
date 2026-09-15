# -*- coding: utf-8 -*-
"""磁盘占用的「收尾」：临时目录清理、产物目录上限、占用体检。

**为什么有这个模块**（用户 2026-09-15 问：「下载下来不占用户存储空间吗？所有这种下载写入的
功能有没有做好删除措施或者限制写入措施」）：审计后发现确实有缺口——最刺眼的是系统临时目录里
躺着 **352 个条目 / 1192 个文件 / 22.8 MB** 我们自己的残留，而 `media/tts` 的合成产物
**221 个文件 / 12.6 MB 且只增不减**。

三条口径：
  1. **只删自己造的**：临时目录里只清 `pm-` 前缀（本项目的约定前缀），其它一概不碰；
     删目录前再核一次 basename 前缀（跟 `video_read.cleanup` 同一套规矩）。
  2. **刚出炉的不动**：任何文件在 `MIN_AGE_S`（默认 10 分钟）内一律不删——它可能正在被发送。
  3. **有账可查**：`footprint()` 只读、给控制台/日志看；`tick()` 每次返回"删了几个、回收了多少"，
     **不返回值就不算做过**（本项目的老规矩）。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 我们在系统临时目录里的前缀（只清这些；别的程序、别的工具的残留一概不碰）
TEMP_PREFIXES = ("pm-",)
#: 十分钟内产生的文件一律不动（可能正在被读/被发送）
MIN_AGE_S = 600.0
#: TTS 产物保留策略（媒体目录：合成出来的 wav/mp3，发完就没用了）
TTS_KEEP_NEWEST = 60
TTS_MAX_MB = 300


def temp_root(root: str | None = None) -> str:
    return root or tempfile.gettempdir()


def dir_footprint(path: str) -> dict:
    """只读：目录里有多少文件、多少字节。目录不存在就返回 0/0（不当错误）。"""
    n = 0
    b = 0
    if path and os.path.isdir(path):
        for dp, _dirs, fs in os.walk(path):
            for f in fs:
                try:
                    b += os.path.getsize(os.path.join(dp, f))
                    n += 1
                except Exception:
                    pass
    return {"files": n, "bytes": b, "mb": round(b / 1048576.0, 2)}


def known_dirs() -> dict:
    """我们会写的目录清单（只读体检用）。**用户自己的图库不在清理范围内，只报数。**"""
    return {
        "media/tts": os.path.join(ROOT, "media", "tts"),
        "media/img": os.path.join(ROOT, "media", "img"),
        "data/gen_images": os.path.join(ROOT, "data", "gen_images"),
        "logs": os.path.join(ROOT, "logs"),
        "wechatauto_logs": os.path.join(ROOT, "wechatauto_logs"),
        "data": os.path.join(ROOT, "data"),
    }


def footprint(root: str | None = None) -> dict:
    """占用体检（只读）：各目录 + 系统临时目录里**我们自己的**残留。"""
    out = {"dirs": {}, "temp": {"entries": 0, "files": 0, "bytes": 0, "mb": 0.0, "names": []}}
    for name, p in known_dirs().items():
        out["dirs"][name] = dir_footprint(p)
    tr = temp_root(root)
    try:
        for nm in os.listdir(tr):
            if not nm.startswith(TEMP_PREFIXES):
                continue
            p = os.path.join(tr, nm)
            fp = dir_footprint(p) if os.path.isdir(p) else (
                {"files": 1, "bytes": os.path.getsize(p), "mb": 0} if os.path.isfile(p) else {"files": 0, "bytes": 0, "mb": 0})
            out["temp"]["entries"] += 1
            out["temp"]["files"] += fp["files"]
            out["temp"]["bytes"] += fp["bytes"]
            if len(out["temp"]["names"]) < 40:
                out["temp"]["names"].append(nm)
    except Exception:
        pass
    out["temp"]["mb"] = round(out["temp"]["bytes"] / 1048576.0, 2)
    return out


def sweep_temp(prefixes=TEMP_PREFIXES, max_age_h: float = 24.0, root: str | None = None,
               now: float | None = None, dry: bool = False) -> dict:
    """清系统临时目录里**我们自己**的残留 ⇒ `{removed, bytes, kept, names}`。

    只删：名字以 `pm-` 开头 **且** 最后修改时间早于 `max_age_h` 小时。其它一切不碰。
    """
    now = time.time() if now is None else now
    tr = temp_root(root)
    cut = now - max(0.0, float(max_age_h)) * 3600.0
    res = {"removed": 0, "bytes": 0, "kept": 0, "names": []}
    try:
        names = os.listdir(tr)
    except Exception:
        return res
    for nm in names:
        if not nm.startswith(prefixes):
            continue
        p = os.path.join(tr, nm)
        try:
            mt = _entry_mtime(p)
        except Exception:
            continue
        if mt > cut:
            res["kept"] += 1
            continue
        fp = dir_footprint(p) if os.path.isdir(p) else (
            {"files": 1, "bytes": os.path.getsize(p)} if os.path.isfile(p) else {"files": 0, "bytes": 0})
        res["removed"] += 1
        res["bytes"] += fp["bytes"]
        if len(res["names"]) < 20:
            res["names"].append(nm)
        if not dry:
            try:
                shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
            except Exception:
                res["removed"] -= 1
                res["bytes"] -= fp["bytes"]
    return res


def prune_dir(path: str, keep_newest: int = 0, max_age_days: float = 0.0, max_mb: float = 0.0,
              min_age_s: float = MIN_AGE_S, now: float | None = None, dry: bool = False) -> dict:
    """按策略清一个目录里的**文件**（不递归、不删子目录）⇒ `{removed, bytes, kept}`。

    删除条件（满足任一，且都得先过"够老"这一关）：
      · 排在最新 `keep_newest` 个之外；
      · 超过 `max_age_days` 天；
      · 目录总量超过 `max_mb` MB 时，从最旧的开始删到不超。
    `min_age_s` 内的文件**一律不动**（可能正被发送）。
    """
    now = time.time() if now is None else now
    res = {"removed": 0, "bytes": 0, "kept": 0}
    if not path or not os.path.isdir(path):
        return res
    items = []
    for nm in os.listdir(path):
        p = os.path.join(path, nm)
        if not os.path.isfile(p):
            continue
        try:
            st = os.stat(p)
        except Exception:
            continue
        items.append((p, st.st_size, st.st_mtime))
    items.sort(key=lambda x: x[2], reverse=True)          # 新的在前
    total = sum(x[1] for x in items)
    keep_bytes_cut = max(0.0, float(max_mb)) * 1048576.0
    doomed = set()
    for idx, (p, sz, mt) in enumerate(items):
        if (now - mt) < max(0.0, float(min_age_s)):
            continue                                       # 刚出炉的不动
        too_old = max_age_days > 0 and (now - mt) > float(max_age_days) * 86400.0
        beyond = keep_newest > 0 and idx >= int(keep_newest)
        if too_old or beyond:
            doomed.add(p)
    # 总量仍超上限 ⇒ 从最旧的继续删（只删够老的）
    if keep_bytes_cut > 0:
        rest = [x for x in items if x[0] not in doomed]
        freed = sum(x[1] for x in items if x[0] in doomed)
        for p, sz, mt in reversed(rest):
            if total - freed <= keep_bytes_cut:
                break
            if (now - mt) < max(0.0, float(min_age_s)):
                continue
            doomed.add(p)
            freed += sz
    for p, sz, _mt in items:
        if p not in doomed:
            res["kept"] += 1
            continue
        res["removed"] += 1
        res["bytes"] += sz
        if not dry:
            try:
                os.remove(p)
            except Exception:
                res["removed"] -= 1
                res["bytes"] -= sz
    return res


def _entry_mtime(p: str) -> float:
    """一个条目的"最后一次活动时间"。

    目录**不能只看自己的 mtime**：目录的 mtime 只在直接子项增删时更新，往里写文件、改文件
    都不会动它（实测：把文件 mtime 回调 48 小时，目录 mtime 仍是"刚刚"）。所以目录取
    「自身 mtime 与**所有后代里最新的那个**」的较大值——这才是"最后一次活动"。
    """
    try:
        best = os.path.getmtime(p)
    except Exception:
        return 0.0
    if os.path.isdir(p):
        for dp, _dirs, fs in os.walk(p):
            for nm in fs:
                try:
                    best = max(best, os.path.getmtime(os.path.join(dp, nm)))
                except Exception:
                    pass
    return best


def cleanup_dir(d: str) -> bool:
    """删掉一个**我们自己建的**临时目录（名字必须带 `pm-` 前缀，否则不碰）。"""
    try:
        if d and os.path.isdir(d) and os.path.basename(os.path.normpath(d)).startswith(TEMP_PREFIXES):
            shutil.rmtree(d, ignore_errors=True)
            return not os.path.exists(d)
    except Exception:
        pass
    return False


def tick(dry: bool = False, root: str | None = None, now: float | None = None) -> dict:
    """一次收尾：清临时残留 + 收 TTS 产物。启动时调一次，也可以随时手动调。"""
    t = sweep_temp(root=root, now=now, dry=dry)
    m = prune_dir(os.path.join(ROOT, "media", "tts"), keep_newest=TTS_KEEP_NEWEST,
                  max_mb=TTS_MAX_MB, now=now, dry=dry)
    freed = t["bytes"] + m["bytes"]
    return {"temp": t, "media_tts": m, "freed_bytes": freed, "freed_mb": round(freed / 1048576.0, 2),
            "dry": bool(dry)}


def brief(rep: dict) -> str:
    """给日志/控制台的一句话（没有数字就不算做过）。"""
    if not rep:
        return "收尾：没有结果"
    return ("收尾：临时目录清 %d 项、产物目录清 %d 个文件，共回收 %.2f MB"
            % (rep["temp"]["removed"], rep["media_tts"]["removed"], rep.get("freed_mb") or 0))
