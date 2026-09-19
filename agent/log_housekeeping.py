# -*- coding: utf-8 -*-
"""日志体积治理（Persona Morph）：①主日志按体积轮转 ②启动时把"只追加"的日志裁到上限 ③清过期日志。

为什么需要（2026-09-13 实测）：主日志 `logs/persona_morph.log` 用的是"每行 flush 的普通 FileHandler"，
**没有任何轮转/上限**；`logs/onestart.log`（442KB）、`data/runtime.log`（当时叫 `bot_crash.log`，360KB）、
`data/listener_failed.jsonl`、`wechatauto_logs/app_YYYYMMDD.log` 全是**只增不减**。
长期挂着跑（本项目就是"上班时也挂着"的用法）会几十 MB~几百 MB 地涨。

口径（可调，写在 LOG_LIMITS）：
  · 单文件超过上限 ⇒ **保留尾部**（丢掉最旧的，头一行写明裁了多少）——不整份删除，证据不丢；
  · `wechatauto_logs/` 里超过 KEEP_DAYS 天的按日期文件删掉（那是驱动库按天写的）；
  · `wechatauto_logs/fail/<时间戳>_<原因>/`（失败现场：`shot.png`/`probe.json`）**整目录**按 mtime 清；
  · 每次启动跑一次，只在真的裁了/删了的时候打一行日志。

⛔ 名单是**手写枚举**这件事本身就是隐患（V-R1-5）：2026-09-20 补齐了 `data/input_audit.log`、
`logs/sd_local.log`、`logs/installer.log`（那份是 `scripts/installer.ps1` 追加写的），并处理了
死代码 `GLOB_DAILY`。**新增日志时请回到 `LOG_LIMITS` 加一行** —— `log_housekeeping_selftest.py`
有一条"名单必须覆盖源码里出现的日志路径"的机械断言盯着它。
"""
import os
import shutil
import time

# (路径相对项目根, 单文件上限字节, 保留尾部字节)
LOG_LIMITS = [
    ("logs/persona_morph.log", 5 * 1024 * 1024, 1 * 1024 * 1024),
    ("logs/onestart.log", 2 * 1024 * 1024, 512 * 1024),
    ("logs/wx_agent.log", 2 * 1024 * 1024, 512 * 1024),
    # ⭐ V-R1-5 补：本机语音/生图的日志（`agent/sd_local.py` 追加写），原来不在名单里、无上限增长
    ("logs/sd_local.log", 2 * 1024 * 1024, 512 * 1024),
    # ⭐ V-R1-5 补：安装器日志（`scripts/installer.ps1` 用 AppendAllText 一直追加）
    ("logs/installer.log", 2 * 1024 * 1024, 512 * 1024),
    ("data/runtime.log", 1 * 1024 * 1024, 256 * 1024),
    # 旧名：2026-09-16 把 `bot_crash.log` 改名成 `runtime.log`（它本来就是主运行日志，
    # 名字让人找错文件）。这一条只为清用户机器上可能残留的旧文件。
    ("data/bot_crash.log", 1 * 1024 * 1024, 256 * 1024),
    ("data/listener_failed.jsonl", 2 * 1024 * 1024, 512 * 1024),
    # ⭐ V-R1-5 补：输入审计日志（`agent/input_audit.py` 打开 `WXAGENT_INPUT_AUDIT=1` 时**只 append**，
    #    排障时正是它被打开 —— 无上限增长最典型的一处）
    ("data/input_audit.log", 1 * 1024 * 1024, 256 * 1024),
]
KEEP_DAYS = 14                      # wechatauto_logs 的按天文件与失败现场目录的保留天数
# ⛔ 2026-09-20 删掉死代码 `GLOB_DAILY`（V-R1-5）：那个正则（`^(app|ui_probe)?_?\d{8}\.log$`）定义后
#    **全仓没有一处使用**，而它想表达的"只删按天文件"和实际行为（删 `wechatauto_logs/` 里所有
#    KEEP_DAYS 天没动过的 `*.log`）并不一致。按名字白名单删文件正是本条漏洞的病根（手写名单会漏），
#    ⇒ 这里明确取"**按年龄删，不按名字删**"这一条口径：`wechatauto_logs/` 下的 `*.log` 只要过期就删，
#    新出现的名字（`replica.log`、`ui_probe.log`…）天然被覆盖，不需要谁回来加白名单。
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


def _dir_size(path: str) -> int:
    """整目录字节数（失败按 0 算；只用来报"释放了多少"，不影响清理本身）。"""
    total = 0
    for r, _d, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return total


def sweep(root: str, keep_days: int = KEEP_DAYS, log=None) -> dict:
    """跑一轮治理，返回 {'trimmed': {路径: 字节}, 'deleted': [文件/目录], 'freed': 总字节}"""
    trimmed, deleted = {}, []
    for rel, max_b, keep_b in LOG_LIMITS:
        p = os.path.join(root, rel)
        n = trim_file(p, max_b, keep_b)
        if n:
            trimmed[rel] = n
    cutoff = time.time() - keep_days * 86400
    # 驱动库按天写的日志：删过期的（**按年龄，不按名字** —— 见 GLOB_DAILY 那段说明）
    daily_dir = os.path.join(root, "wechatauto_logs")
    if os.path.isdir(daily_dir):
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
    # ⭐ V-R1-5 补：失败现场 `wechatauto_logs/fail/<时间戳>_<原因>/` **整目录**清（原来是"只删 *.log
    #    且不进子目录"⇒ 15 个目录里的 shot.png/probe.json 一直在，比 .log 更占地方）。
    fail_dir = os.path.join(root, "wechatauto_logs", "fail")
    if os.path.isdir(fail_dir):
        for name in os.listdir(fail_dir):
            p = os.path.join(fail_dir, name)
            try:
                if os.path.isdir(p) and os.path.getmtime(p) < cutoff:
                    sz = _dir_size(p)
                    shutil.rmtree(p, ignore_errors=True)
                    if not os.path.exists(p):          # 真删掉了才记账（rmtree 失败不许谎报释放）
                        deleted.append("fail/" + name)
                        trimmed["wechatauto_logs/fail/" + name] = sz
            except OSError:
                pass
    freed = sum(trimmed.values())
    if log and (trimmed or deleted):
        log.info("日志治理：裁剪 %d 个文件、删除 %d 个过期日志，释放约 %.1f MB",
                 len(trimmed), len(deleted), freed / 1024.0 / 1024.0)
    return {"trimmed": trimmed, "deleted": deleted, "freed": freed}
