# -*- coding: utf-8 -*-
"""日志体积治理（Persona Morph）：①主日志按体积轮转 ②启动时把"只追加"的日志裁到上限 ③清过期日志。

为什么需要（2026-09-13 实测）：主日志 `logs/persona_morph.log` 用的是"每行 flush 的普通 FileHandler"，
**没有任何轮转/上限**；`logs/onestart.log`（442KB）、`data/bot_crash.log`（360KB）、
`data/listener_failed.jsonl`、`wechatauto_logs/app_YYYYMMDD.log` 全是**只增不减**。
长期挂着跑（本项目就是"上班时也挂着"的用法）会几十 MB~几百 MB 地涨。

口径（可调，写在 LOG_LIMITS）：
  · 单文件超过上限 ⇒ **保留尾部**（丢掉最旧的，头一行写明裁了多少）——不整份删除，证据不丢；
  · `wechatauto_logs/` 里超过 KEEP_DAYS 天的按日期文件删掉（那是驱动库按天写的）；
  · 每次启动跑一次，只在真的裁了/删了的时候打一行日志。
"""
import os
import re
import time

# (路径相对项目根, 单文件上限字节, 保留尾部字节)
LOG_LIMITS = [
    ("logs/persona_morph.log", 5 * 1024 * 1024, 1 * 1024 * 1024),
    ("logs/onestart.log", 2 * 1024 * 1024, 512 * 1024),
    ("logs/wx_agent.log", 2 * 1024 * 1024, 512 * 1024),
    ("data/bot_crash.log", 1 * 1024 * 1024, 256 * 1024),
    ("data/listener_failed.jsonl", 2 * 1024 * 1024, 512 * 1024),
]
KEEP_DAYS = 14                      # wechatauto_logs 按天文件的保留天数
GLOB_DAILY = re.compile(r"^(app|ui_probe)?_?\d{8}\.log$|^app_\d{8}\.log$")
MARK = "[日志治理] 文件超过上限，已保留最近的部分；更早的内容见同目录 .1 备份或已删除\n"


def trim_file(path: str, max_bytes: int, keep_bytes: int) -> int:
    """超过 max_bytes 就只留最后 keep_bytes 字节（原子替换）。返回裁掉的字节数（0＝没动）。"""
    try:
        size = os.path.getsize(path)
        if size <= max_bytes:
            return 0
        with open(path, "rb") as fh:
            fh.seek(max(0, size - keep_bytes))
            tail = fh.read()
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(MARK.encode("utf-8"))
            fh.write(tail)
        os.replace(tmp, path)
        return size - (len(tail) + len(MARK.encode("utf-8")))
    except OSError:
        return 0


def sweep(root: str, keep_days: int = KEEP_DAYS, log=None) -> dict:
    """跑一轮治理，返回 {'trimmed': {路径: 字节}, 'deleted': [文件], 'freed': 总字节}"""
    trimmed, deleted = {}, []
    for rel, max_b, keep_b in LOG_LIMITS:
        p = os.path.join(root, rel)
        n = trim_file(p, max_b, keep_b)
        if n:
            trimmed[rel] = n
    # 驱动库按天写的日志：删过期的
    daily_dir = os.path.join(root, "wechatauto_logs")
    if os.path.isdir(daily_dir):
        cutoff = time.time() - keep_days * 86400
        for name in os.listdir(daily_dir):
            if not name.endswith(".log"):
                continue
            p = os.path.join(daily_dir, name)
            try:
                if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                    sz = os.path.getsize(p)
                    os.remove(p)
                    deleted.append(name)
                    trimmed["wechatauto_logs/" + name] = sz
            except OSError:
                pass
    freed = sum(trimmed.values())
    if log and (trimmed or deleted):
        log.info("日志治理：裁剪 %d 个文件、删除 %d 个过期日志，释放约 %.1f MB",
                 len(trimmed), len(deleted), freed / 1024.0 / 1024.0)
    return {"trimmed": trimmed, "deleted": deleted, "freed": freed}
