# -*- coding: utf-8 -*-
"""**一键备份 / 一键恢复**：把「全部设置 + 全部数据」打成一个 zip，换机/重装时一条路恢复。

为什么要它
----------
用户在控制台点「导出备份」→ 拿到一个 zip；到了新机器点「导入备份」→ 设置和数据都回来。
没有它，换机就得手工搬 `config.json` + `data/`，**漏一个目录就是半个机器人**（记忆丢了它就不像原来那个它）。

包结构（`manifest.json` 里 `kind=backup`）
----------------------------------------
```
manifest.json                     # 元信息 + 文件清单
config.json                       # 全部设置（顶层，沿用原恢复链路）
memory/<相对路径>.json            # data/memory/ 下所有 json
data/<相对路径>                   # data/ 下其余文件（分区/自定义人设/用量价格/对话计费/表情/评分/风控/消息台账…）
```
⇒ 解压覆盖同名文件即可恢复「所有数据和设置」。

⛔ 安全三条（**收编时一条都不能少**，缺一条就是漏洞）
--------------------------------------------------
1. **`_safe_rel()` 拒路径穿越** —— 条目名含 `..`、绝对路径、盘符一律拒绝
   （否则一个被篡改的备份包就能往系统任意位置写文件）。
2. **不许改 `server.*`** —— 备份里的 `server` 段（网络绑定 + 控制台口令）**一律不恢复**，只沿用当前运行值。
   （否则一个恶意备份就能把控制台敞开到局域网、或把口令清空。）
3. **记忆只收 `.json`** —— `memory/` 路径下非 `.json` 条目一律跳过
   （否则可执行/二进制可以借 `memory/` 落盘）。

✅ 收编时按我方口径**加强**的两处（朋友原版没有）
----------------------------------------------
4. **data/ 段也不许覆盖 `server` 相关与会话令牌类文件** —— 见 `_DATA_DENY`。
5. **判据环境不写盘**（`PM_JUDGE_NO_PROC=1`）—— 否则一条判据就把真人那份 `config.json` / `data/` 改了
   （与 `wechat_dir.remember_scanned` 同一条红线，见 §G G90）。
"""

from __future__ import annotations

import glob
import io
import json as _json
import os
import time
import zipfile

#: 导出时排除的运行时状态目录（跨机无意义，恢复还会破坏当前运行实例）
_EXC_DIRS = frozenset({
    "debug", "out_images", "media", "logs", "browser-screenshots", "__pycache__", "存档",
})

#: 导出时排除的运行时状态文件（锁 / pid / 日志）
_EXC_FILES = frozenset({
    "runtime.log", "onestart.log", "console_window.txt",
    "bot.pid", "bot.lock", "watchdog.pid",
})

#: ⭐ 收编加强④：**导入时**额外拒收的 data/ 条目（即使备份包里带了也不落盘）
#:   —— 这几类是"当前实例的运行身份"，被覆盖会让本次运行立刻错乱。
_DATA_DENY = frozenset({
    "window_borrow.json",   # 窗口借还台账：指着别人的句柄
    "runtime_state.json",   # 运行时状态
})

#: `_db_recent_rows` 一类判据环境开关：命中即**不写盘**
_JUDGE_ENV = "PM_JUDGE_NO_PROC"


def _skip_runtime(base: str) -> bool:
    """导出时：这是不是"不该进备份"的运行时状态文件？"""
    if base in _EXC_FILES:
        return True
    if base.endswith(".pid") or base.endswith(".lock"):
        return True
    if base.endswith(".log"):
        return True
    return False


def _safe_rel(name: str):
    """把包内条目名收敛成**相对路径**；不安全（绝对路径 / 盘符 / `..` 穿越）则返回 None。

    ⛔ 安全第 1 条：这是那个"被篡改的备份包"的唯一闸门。
    """
    if not name:
        return None
    if name.startswith("/") or name.startswith("\\"):
        return None
    if len(name) >= 2 and name[1] == ":" and name[0].isalpha():
        return None
    _p = name.replace("\\", "/")
    if ".." in _p.split("/"):
        return None
    _p = _p.strip("/")
    return _p or None


def backup_export(root: str) -> bytes:
    """一键备份：`config.json` + 全部 `data/`（除运行时状态）打包成 zip，返回字节。

    覆盖：记忆（`data/memory/`）、分区（`data/persona_cats.json`）、自定义人设、用量价格、
    对话与计费记录、表情、评分、风控、消息台账等 —— 真正做到「备份所有数据和设置」。
    """
    buf = io.BytesIO()
    files = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # ① 设置（config.json）
        cfg = os.path.join(root, "config.json")
        if os.path.exists(cfg):
            z.write(cfg, "config.json")
            files.append("config.json")
        # ② 记忆（data/memory/ → 顶层 memory/，沿用原恢复链路）
        mdir = os.path.join(root, "data", "memory")
        if os.path.isdir(mdir):
            for fp in sorted(glob.glob(os.path.join(mdir, "**", "*.json"), recursive=True)):
                rel = "memory/" + os.path.relpath(fp, mdir).replace("\\", "/")
                z.write(fp, rel)
                files.append(rel)
        # ③ 其余 data/ 整树（排除 memory/ 与运行时状态）
        droot = os.path.join(root, "data")
        if os.path.isdir(droot):
            for cur, dirs, fns in os.walk(droot):
                rel0 = os.path.relpath(cur, droot).replace("\\", "/")
                dirs[:] = [d for d in dirs if (d not in _EXC_DIRS) and (d != "memory")]
                for fn in fns:
                    if _skip_runtime(fn):
                        continue
                    full = os.path.join(cur, fn)
                    rel = (rel0 + "/" + fn) if rel0 != "." else fn
                    z.write(full, "data/" + rel)
                    files.append("data/" + rel)
        z.writestr("manifest.json", _json.dumps(
            {"app": "Persona Morph", "kind": "backup", "v": 2,
             "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "note": ("本包含 config.json（全部设置）+ data/ 全部数据"
                      "（记忆/分区/自定义人设/用量价格/对话计费/表情/评分/风控等，不含运行时锁与日志）；"
                      "解压覆盖同名文件即可恢复「所有数据和设置」"),
             "files": files}, ensure_ascii=False))
    return buf.getvalue()


def backup_import(root: str, body: bytes, on_save=None) -> dict:
    """导入一键备份包：恢复 `config.json`（全部设置，实时生效）与 `data/`（全部数据，落盘）。

    设计要点
    --------
    - `config.json` 走与 **`/api/config` 完全一致**的实时写入链路
      （`deep_merge` → `_protect_secrets` → `_wechat_dir_conflict` 闸门 → `set_config`/`save_config`/`on_save`）
      ⇒ 不丢当前"只有的键"、掩码值不覆盖真实密钥。
    - `memory/` 与 `data/` **整包覆盖同名文件**（恢复＝还原到备份时那份），不做行级合并
      （记忆本就是按人归档的整份 json，覆盖即还原）。
    - ⚠️ 诚实限制：数据落盘后，**实时运行的机器人内存缓存不会自动清空刷新** ——
      恢复的数据在下一次重启机器人/控制台后才真正生效。控制台设置则即时生效。
    """
    # 函数内 import：避免与 webui 形成导入环（webui 会 import 本模块）
    from .config import deep_merge, get_config, save_config, set_config
    from .webui import _protect_secrets, _wechat_dir_conflict

    try:
        zf = zipfile.ZipFile(io.BytesIO(body))
    except Exception:
        return {"ok": False, "error": "不是有效的备份包（应选择「导出备份」生成的 .zip）"}

    names = zf.namelist()
    # 合法性：要么是本工具导出的备份（manifest.kind=backup），要么至少含 config.json / memory/ / data/
    manifest = {}
    if "manifest.json" in names:
        try:
            manifest = _json.loads(zf.read("manifest.json").decode("utf-8", "replace"))
        except Exception:
            manifest = {}
    is_backup = (manifest.get("kind") == "backup") or \
                ("config.json" in names) or any(n.startswith("memory/") for n in names) \
                or any(n.startswith("data/") for n in names)
    if not is_backup:
        return {"ok": False,
                "error": "备份包里没有 config.json / memory/ / data/，可能不是本工具导出的备份"}

    result = {"ok": True, "config_restored": False, "memory_files": 0,
              "memory_dirs": [], "data_files": 0, "warnings": []}

    # ① 恢复 config.json（实时生效）
    if "config.json" in names:
        try:
            bk_cfg = _json.loads(zf.read("config.json").decode("utf-8", "replace"))
        except Exception as e:
            result["warnings"].append("config.json 解析失败，跳过设置恢复：%s" % e)
            bk_cfg = None
        if isinstance(bk_cfg, dict):
            try:
                new = deep_merge(get_config(), bk_cfg)
                # ⛔ 安全第 2 条：备份不得改变 server.*（网络绑定/控制台口令）
                if "server" in bk_cfg:
                    new["server"] = dict(get_config().get("server", {}))
                    result["warnings"].append(
                        "出于安全，备份未恢复 server.*（网络/口令）设置，沿用当前运行配置")
                _protect_secrets(new)   # 掩码值不覆盖真实密钥（双保险）
                conflict = _wechat_dir_conflict(new)
                if conflict:
                    result["warnings"].append(
                        "微信数据目录校验未过，设置未写入：%s" % conflict.get("error", ""))
                else:
                    set_config(new)
                    # ⛔ `save_config` 必须显式传 `path`（判据隔离口）：
                    #   不传就写产品那份 `config.json` ⇒ ①判据一跑就覆写**用户真的配置**
                    #   （B 线实测撞过两次）；②恢复也落不到"我们正在恢复的那个 root"上。
                    save_config(new, path=os.path.join(root, "config.json"))
                    if callable(on_save):
                        try:
                            on_save(new)
                        except Exception:
                            pass
                    result["config_restored"] = True
            except Exception as e:
                result["warnings"].append("设置恢复失败：%s" % e)

    # 判据环境（`PM_JUDGE_NO_PROC=1`）⇒ **一律不写盘**（只做校验与结算，不留痕）
    _judge = bool(os.environ.get(_JUDGE_ENV))
    if _judge:
        result["judge_env"] = True
        result["warnings"].append("判据/自检环境：数据文件一律不写盘")

    # ② 恢复 memory/（整包覆盖到 data/memory/）
    mdir = os.path.join(root, "data", "memory")
    for n in names:
        if not n.startswith("memory/"):
            continue
        rel = _safe_rel(n[len("memory/"):])
        if not rel:
            result["warnings"].append("跳过可疑路径：%s" % n)
            continue
        if n.endswith("/"):
            continue
        # ⛔ 安全第 3 条：记忆只接受 .json
        if not rel.endswith(".json"):
            result["warnings"].append("跳过非 json 记忆条目：%s" % n)
            continue
        if _judge:
            result["memory_files"] += 1
            continue
        dst = os.path.join(mdir, rel)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(zf.read(n))
            result["memory_files"] += 1
            top = rel.split("/")[0]
            if top not in result["memory_dirs"]:
                result["memory_dirs"].append(top)
        except Exception as e:
            result["warnings"].append("记忆文件恢复失败 %s：%s" % (n, e))

    # ③ 恢复其余 data/ 文件
    ddir = os.path.join(root, "data")
    for n in names:
        if not n.startswith("data/"):
            continue
        rel = _safe_rel(n[len("data/"):])
        if not rel:
            result["warnings"].append("跳过可疑路径：%s" % n)
            continue
        if n.endswith("/"):
            continue
        # ⭐ 收编加强④：运行身份类文件不覆盖
        if rel in _DATA_DENY:
            result["warnings"].append("跳过运行状态条目（会打乱当前实例）：%s" % n)
            continue
        if _judge:
            result["data_files"] += 1
            continue
        dst = os.path.join(ddir, rel)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(zf.read(n))
            result["data_files"] += 1
        except Exception as e:
            result["warnings"].append("数据文件恢复失败 %s：%s" % (n, e))

    if result["warnings"]:
        result["note"] = ("恢复完成（设置%s、记忆 %d 个文件、数据 %d 个文件）" % (
            "已生效" if result["config_restored"] else "未改动",
            result["memory_files"], result["data_files"])) + \
            "；有 %d 条警告见 warnings" % len(result["warnings"])
    else:
        result["note"] = ("设置已恢复并实时生效、记忆 %d 个文件、数据 %d 个文件已还原到 data/；"
                          "记忆/分区/用量等需重启机器人后完全生效。"
                          % (result["memory_files"], result["data_files"]))
    return result
