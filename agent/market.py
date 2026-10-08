# -*- coding: utf-8 -*-
"""资源市场：**软件内「下载 / 上传 / 打开网站」三个动作**的后端半边。

三栏（人设 / Skill / 工具）共用同一套动作，只是**落点目录**与**清单字段**不同 ——
这三条正是本模块存在的理由：把"装到哪里、怎么校验、错了怎么说"收在一处，
界面侧只管显示，⛔ 不各写一份。

⛔ **本模块不碰发送链**：不 import 任何发送/窗口模块，也不写任何消息。
   它只做"读清单 / 装文件 / 卸文件 / 攒一份待上传的元数据"。

## 为什么状态用"码"而不是"句"
   界面要能解释"为什么这个按钮点不动"。若各处各拼一句，同一种失败在两个界面上会长得不一样
   （甚至一个说"不可用"、一个说"没启用"）。所以这里定一张 `REASONS` 表：
   **码 → 一句中文**，`agent/routes.py` 把它原样透出，界面直接用，⛔ 不拼字符串。

## 与网站的关系（当前阶段）
   网站还没建好 ⇒ 站侧接口不存在 ⇒ 本模块**没有联网动作**，
   `site_ready=False` 时所有装/卸动作返回 `site-not-ready`（如实说明，不是假按钮）。
   站建好后只需实现 `_fetch_catalog()` 与 `_fetch_package()` 两个函数，其余不动。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time

log = logging.getLogger("persona-morph")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 三栏的落点。`kind` 是**唯一**决定装到哪儿的依据（⛔ 不听前端传的目录）。
#:   · 工具栏落 `tools.d/`（已有实现，见 `agent/user_tools.py`）
#:   · 人设 / Skill 栏落点随各自机制建设（见 `报告/PLAN.md` 序 265-a / 265-b）
KINDS = {
    "persona": {"dir": os.path.join("assets", "persona"), "label": "人设"},
    "skill": {"dir": "skills", "label": "Skill"},
    "tool": {"dir": "tools.d", "label": "工具"},
}

#: 码 → 中文（界面直接显示这一句；⛔ 不许在界面里再拼别的说法）
REASONS = {
    "ok": "就绪",
    "site-not-ready": "资源站还在建设中，等建好后这里会自动可用",
    "kind-not-ready": "这一类资源的落点还没建好，暂时只能看清单",
    "not-installed": "本机还没装这个资源",
    "already-installed": "本机已经装了同名资源",
    "hash-mismatch": "下载下来的文件和站上登记的对不上，已丢弃",
    "too-large": "超过体积上限，没有安装",
    "bad-manifest": "清单读不动（格式不对或字段缺失）",
    "load-failed": "装上了但没能加载起来",
    "disabled": "已经装上，但开关是关的",
    "no-permission": "没有权限写入目标目录",
}


def reason(code: str) -> str:
    """取一句人话。未知码照原样回，⛔ 不编造解释。"""
    return REASONS.get(str(code or ""), str(code or ""))


def _cfg() -> dict:
    try:
        from .config import get_config
        return dict(get_config().get("market") or {})
    except Exception as _e:
        log.debug("读市场配置失败（用默认值继续）：%s", _e, exc_info=True)
        return {}


def site_ready() -> bool:
    """网站与站侧接口是否已就绪。现在恒 False —— 站还没建（见 `报告/PLAN.md` 序 157）。"""
    return bool(_cfg().get("site_url")) and bool(_cfg().get("site_ready"))


def kind_dir(kind: str) -> str:
    """某一栏的落点绝对路径。**只认 KINDS 表**，未知 kind 返回空串。

    ⚠️ 工具栏**以 `user_tools.manifest_dir()` 为准**（拿不到才回落到表里的值）：
       工具那一栏既被这里读、又被 `agent/user_tools.py` 的加载器读，
       两处各留一份目录配置 ⇒ 迟早漂移（一处改了另一处不知道，表现为"装上了但列不出来"）。
    """
    k = str(kind or "").strip().lower()
    spec = KINDS.get(k)
    if not spec:
        return ""
    if k == "tool":
        try:
            from . import user_tools
            return user_tools.manifest_dir()
        except Exception as _e:
            log.debug("取工具目录失败，回落表内值：%s", _e, exc_info=True)
    d = str(spec["dir"])
    return d if os.path.isabs(d) else os.path.join(ROOT, d)


def kind_ready(kind: str) -> bool:
    """该栏现在能不能真装东西（落点目录存在）。

    ⚠️ 与 `site_ready()` 是**两件事**：目录在但站没建好 ⇒ 仍然装不了；
       站在但目录没建 ⇒ 也装不了。两个都要满足。
    """
    d = kind_dir(kind)
    return bool(d) and os.path.isdir(d)


def _local_catalog(kind: str) -> list:
    """盘一遍本机已装的东西（不看网站）。

    这就是"登记与可用分离"的落法：**站点不可达时这一栏照样有内容**，
    用户至少能看到"我这儿现在有什么"。
    工具栏复用 `agent/user_tools.py` 的加载器（那里已经做好了校验与问题收集）。
    """
    k = str(kind or "").strip().lower()
    if k == "tool":
        out = []
        try:
            from . import user_tools
            tools, problems = user_tools.load()
            out = [{
                "id": str(t.get("name") or ""),
                "name": str(t.get("name") or ""),
                "desc": str(t.get("description") or ""),
                "installed": True,
                "enabled": bool(t.get("enabled")),
                "source": "local",
            } for t in (tools or [])]
            for p in (problems or []):
                out.append({
                    "id": str((p or {}).get("file") or ""),
                    "name": os.path.basename(str((p or {}).get("file") or "")),
                    "desc": str((p or {}).get("why") or ""),
                    "installed": False,
                    "enabled": False,
                    "source": "local",
                    "problem": reason("bad-manifest"),
                })
        except Exception as _e:
            log.debug("列本地工具失败：%s", _e, exc_info=True)
        # ⚠️ 加载器只认"清单合法"的 JSON ⇒ 刚装进来、还没被接受的包**也要能看见**
        #   （否则"装完却列不出来"，用户以为没装上）。按文件名补一遍差集。
        try:
            d0 = kind_dir(k)
            have = {str(x.get("id") or "") for x in out}
            for name in sorted(os.listdir(d0)) if os.path.isdir(d0) else []:
                stem = os.path.splitext(name)[0]
                if not name.lower().endswith(".json"):
                    continue
                if stem in have or name in have:
                    continue
                out.append({"id": stem, "name": stem, "desc": "",
                            "installed": True, "enabled": False, "source": "local"})
        except OSError as _e:
            log.debug("补列工具目录失败：%s", _e, exc_info=True)
        return out

    d = kind_dir(k)
    if not d or not os.path.isdir(d):
        return []
    out = []
    try:
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if os.path.isdir(p):
                out.append({"id": name, "name": name, "desc": "", "installed": True,
                            "enabled": True, "source": "local"})
            elif name.lower().endswith((".json", ".md")):
                out.append({"id": name, "name": os.path.splitext(name)[0], "desc": "",
                            "installed": True, "enabled": True, "source": "local"})
    except OSError as _e:
        log.debug("列目录失败 %s：%s", d, _e, exc_info=True)
        return []
    return out


def _fetch_catalog(kind: str) -> list:
    """从站上取目录。**站未就绪 ⇒ 空表**（不编造、不缓存假数据）。"""
    if not site_ready():
        return []
    # 站就绪后在此实现：GET <site_url>/api/catalog?kind=<kind> → 列表 JSON
    # ⛔ 只读；鉴权与重试照 agent/feedback.py::_post 的口径（设备 ID + 签名）。
    return []


def list_kind(kind: str) -> dict:
    """一栏的完整清单：**站上的（可下载）＋ 本机的（已装上）**，各带状态与原因。

    返回 `{ok, kind, ready, ready_reason, remote, local, error}`。
    ⛔ 任何一栏都不"整栏消失"—— 最差不外乎 `ready=False` ＋ 一句如实说明。
    """
    k = str(kind or "").strip().lower()
    if k not in KINDS:
        return {"ok": False, "kind": k, "ready": False,
                "ready_reason": reason("kind-not-ready"), "ready_text": reason("kind-not-ready"),
                "remote": [], "local": [], "error": ""}

    ready = site_ready() and kind_ready(k)
    if site_ready() and not kind_ready(k):
        code = "kind-not-ready"
    elif not site_ready():
        code = "site-not-ready"
    else:
        code = "ok"

    remote, err = [], ""
    try:
        remote = _fetch_catalog(k)
    except Exception as e:  # noqa: BLE001
        code, err = "load-failed", str(e)[:200]
        log.debug("取站上目录失败 kind=%s：%s", k, e, exc_info=True)

    local = _local_catalog(k)
    installed = {str(x.get("id") or "") for x in local if x.get("installed")}
    for item in remote:
        iid = str((item or {}).get("id") or "")
        item["installed"] = iid in installed
        item["source"] = "site"

    return {
        "ok": True,
        "kind": k,
        "label": KINDS[k]["label"],
        "ready": ready,
        "ready_code": code,
        "ready_reason": reason(code),
        "remote": remote,
        "local": local,
        "error": err,
    }


def list_all() -> dict:
    """三栏一起取（弹窗打开时一次拿全，省三次往返）。"""
    return {"ok": True, "kinds": {k: list_kind(k) for k in KINDS}}


# --------------------------------------------------------------- 装 / 卸


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def install(kind: str, item_id: str, expect_sha256: str = "", data: bytes | None = None) -> dict:
    """装一个资源：**先落到临时目录 → 校验 → 再整体换档**，失败不留半个包。

    `data` 只给测试与"从本机重新导入"用；正常路径是 `_fetch_package()` 去站上取。
    返回 `{ok, code, message, path}`。⛔ 本函数不执行任何下载来的代码。
    """
    k = str(kind or "").strip().lower()
    iid = str(item_id or "").strip()
    if k not in KINDS:
        return {"ok": False, "code": "kind-not-ready", "message": reason("kind-not-ready"), "path": ""}
    if not site_ready() and data is None:
        return {"ok": False, "code": "site-not-ready", "message": reason("site-not-ready"), "path": ""}
    if not iid:
        return {"ok": False, "code": "bad-manifest", "message": "没给要装什么", "path": ""}

    dst_dir = kind_dir(k)
    if not dst_dir or not os.path.isdir(dst_dir):
        return {"ok": False, "code": "kind-not-ready", "message": reason("kind-not-ready"), "path": ""}

    tmp_dir = ""
    try:
        if data is None:
            data = _fetch_package(k, iid)  # 站就绪后实现
        if not data:
            return {"ok": False, "code": "load-failed", "message": "没取到文件内容", "path": ""}
        if len(data) > int(_cfg().get("max_bytes") or (8 << 20)):
            return {"ok": False, "code": "too-large", "message": reason("too-large"), "path": ""}

        tmp_dir = tempfile.mkdtemp(prefix=".market-", dir=dst_dir)
        tmp_file = os.path.join(tmp_dir, "incoming.bin")
        with open(tmp_file, "wb") as f:
            f.write(data)

        if expect_sha256:
            got = _sha256(tmp_file)
            if got.lower() != str(expect_sha256).lower():
                return {"ok": False, "code": "hash-mismatch",
                        "message": reason("hash-mismatch"), "path": ""}

        # 目标名：`<id>`（工具栏的清单就是 tools.d/<id>.json，与该目录既有约定一致）
        safe = "".join(ch for ch in iid if ch.isalnum() or ch in "._-")[:64]
        if not safe:
            return {"ok": False, "code": "bad-manifest", "message": "资源标识不合法", "path": ""}
        dst = os.path.join(dst_dir, safe + (".bin" if data[:2] == b"PK" else ".json"))
        with open(tmp_file, "rb") as sf, open(dst, "wb") as df:
            shutil.copyfileobj(sf, df)
        return {"ok": True, "code": "ok", "message": "已安装", "path": dst}
    except OSError as e:
        code = "no-permission" if getattr(e, "errno", None) in (13, 1) else "load-failed"
        return {"ok": False, "code": code, "message": reason(code), "path": ""}
    except Exception as e:  # noqa: BLE001
        log.debug("装资源失败 kind=%s id=%s：%s", k, iid, e, exc_info=True)
        return {"ok": False, "code": "load-failed", "message": str(e)[:200], "path": ""}
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def uninstall(kind: str, item_id: str) -> dict:
    """卸一个资源。⛔ 只删**落在本栏目录里、且名字来自本栏清单**的那一个。"""
    k = str(kind or "").strip().lower()
    iid = str(item_id or "").strip()
    d = kind_dir(k)
    if not d or not iid:
        return {"ok": False, "code": "kind-not-ready", "message": reason("kind-not-ready"), "path": ""}

    safe = "".join(ch for ch in iid if ch.isalnum() or ch in "._-")[:64]
    if not safe:
        return {"ok": False, "code": "bad-manifest", "message": "资源标识不合法", "path": ""}

    hit = ""
    for cand in (safe, safe + ".json", safe + ".bin"):
        p = os.path.join(d, cand)
        if os.path.exists(p) and os.path.dirname(os.path.abspath(p)) == os.path.abspath(d):
            hit = p
            break
    if not hit:
        return {"ok": False, "code": "not-installed", "message": reason("not-installed"), "path": ""}
    try:
        if os.path.isdir(hit):
            shutil.rmtree(hit)
        else:
            os.remove(hit)
        return {"ok": True, "code": "ok", "message": "已卸载", "path": hit}
    except OSError as e:
        log.debug("卸资源失败 %s：%s", hit, e, exc_info=True)
        return {"ok": False, "code": "no-permission", "message": reason("no-permission"), "path": hit}


def _fetch_package(kind: str, item_id: str) -> bytes:
    """从站上取一个资源包。**站未就绪 ⇒ 空**。站建好后在此实现（预签名直链 + 校验）。"""
    return b""


# --------------------------------------------------------------- 上传半边


def prepare_upload(kind: str, filename: str, title: str = "", author: str = "") -> dict:
    """攒一份待上传的元数据。**现在不发起任何网络请求**（站没建好）。

    返回 `{ok, code, message, item, ready}`：`ready=False` 时界面照 `message` 如实置灰。
    站就绪后走预签名 URL 直传（⛔ Access Key 不进前端），本站内过审后才进目录。
    """
    k = str(kind or "").strip().lower()
    if k not in KINDS:
        return {"ok": False, "code": "kind-not-ready", "message": reason("kind-not-ready"),
                "item": None, "ready": False}
    name = os.path.basename(str(filename or "").strip())
    if not name:
        return {"ok": False, "code": "bad-manifest", "message": "没选文件",
                "item": None, "ready": False}
    item = {
        "kind": k,
        "filename": name,
        "title": str(title or os.path.splitext(name)[0]).strip()[:80],
        "author": str(author or "").strip()[:40],
        "bytes": 0,
        "ts": int(time.time()),
    }
    if not site_ready():
        return {"ok": False, "code": "site-not-ready", "message": reason("site-not-ready"),
                "item": item, "ready": False}
    return {"ok": True, "code": "ok", "message": "可以上传", "item": item, "ready": True}


def export_local(kind: str) -> dict:
    """把本机某一栏导出成一份可分享的 JSON 文本（复用"导出/导入"那套既有做法）。"""
    k = str(kind or "").strip().lower()
    if k not in KINDS:
        return {"ok": False, "code": "kind-not-ready", "message": reason("kind-not-ready"), "text": ""}
    items = _local_catalog(k)
    doc = {"kind": k, "generated": int(time.time()), "count": len(items), "items": items}
    return {"ok": True, "code": "ok", "message": "已生成清单",
            "text": json.dumps(doc, ensure_ascii=False, indent=2)}
