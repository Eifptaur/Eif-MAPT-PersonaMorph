# -*- coding: utf-8 -*-
"""人设分区与「下载」落点：从资源市场装进来的人设**自动进「下载」分区**。

## 这个模块只干一件事
   用户口径（2026-10-10，逐字）：三类扩展**各自有统一管理页**（工具页 / skill 页 /
   人设页），不用再造一个"总页"；唯一要补的是 ——
   > 「人设你从资源网站上下载下来，就自动帮用户导入，所以要直接新建一个分区叫做**下载**，
   >   用户想把这个移到哪个分区，就是他的事儿；或者他想自创分区，也是他的事儿。」

   ⇒ 所以这里**不碰** skill / 工具的启停（它们各有各的页），只负责：
     ① 保证有一个「下载」分区（不存在就建）；
     ② 把下载来的人设写进 `data/custom_personas.json` 且 `cat="⬇️ 下载"`；
     ③ 给界面读"现有分区"与"改某项的分区"两个口（用户自建/挪动都走它）。
   移到别的分区、自建新分区**都是用户的事** —— 本模块只提供口子，不替用户决定。

## 落点为什么是 custom_personas.json
   人设页读的就是这份（`/api/personas/custom`）。装进来的卡若写到别处，
   人设页看不见 ⇒ 用户"装了却找不到"。**同一份数据、同一个家**，别开第二处。

## ⛔ 本模块不碰发送链
   只读写 `data/custom_personas.json` 与 `data/persona_cats.json`。
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("persona-morph")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 从资源市场下载来的人设默认落这个分区（用户之后想挪到哪都行）。
DOWNLOAD_CAT = "⬇️ 下载"

#: 用户没给分区时，自定义人设的默认家（与 web 端既有口径一致）。
DEFAULT_CAT = "📝 自定义"


def _custom_path() -> str:
    return os.path.join(ROOT, "data", "custom_personas.json")


def _cats_path() -> str:
    return os.path.join(ROOT, "data", "persona_cats.json")


def _read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as _e:  # noqa: BLE001
        log.debug("读 %s 失败（按空处理）：%s", path, _e, exc_info=True)
        return default


def _write_json(path: str, obj) -> bool:
    """原子写 —— 走 `persist.atomic_write_json`（tmp 带 pid+随机后缀 → fsync → 换档）。

    ⛔ 不自己拼 `os.replace`：Windows 上目标档被任何句柄占着就会撞 WinError 5/32，
       裸调用的写会**静默丢掉**（实测共享冲突下 71% 的写没落盘）。
       人设清单坏了＝整类人设读不出来，所以这里必须是带退避的原子写。
    """
    try:
        from . import persist
        return bool(persist.atomic_write_json(path, obj, indent=2))
    except Exception as _e:  # noqa: BLE001
        log.debug("写 %s 失败：%s", path, _e, exc_info=True)
        return False


def ensure_download_cat() -> bool:
    """保证「下载」分区存在 —— 不存在就在用户分区表里记一条。

    ⚠️ 只**补一条分区登记**，不碰任何人设的 `cat`：
       给已存在的人设改分区＝替用户做主（用户口径："移到哪是他的事"）。
    """
    cats = _read_json(_cats_path(), {})
    if not isinstance(cats, dict):
        cats = {}
    if DOWNLOAD_CAT in cats:
        return True
    cats[DOWNLOAD_CAT] = {"desc": "从资源市场下载来的人设（你可以把它移到别的分区）"}
    return _write_json(_cats_path(), cats)


def import_persona(item: dict, cat: str = "") -> dict:
    """把一份**下载来的人设**导进人设页（`custom_personas.json`）。

    `item` 至少要有 `name` 与 `text`（web 人设页读的就是这两个字段）；
    可选 `desc` / `key`。
    返回 `{ok, code, message, key, cat}`。

    ⛔ 同名覆盖要**如实说**（`message` 写"已覆盖同名的人设"），
       不许静默盖掉用户自己写过的那份 —— 那是别人写的内容，用户有权知道。
    """
    name = str((item or {}).get("name") or "").strip()
    text = str((item or {}).get("text") or "").strip()
    if not name or not text:
        return {"ok": False, "code": "bad-manifest",
                "message": "这份人设缺名字或缺正文，装不进人设页", "key": "", "cat": ""}

    want_cat = str(cat or "").strip() or DOWNLOAD_CAT
    # 下载来的一律先落「下载」分区（用户之后自己挪）⇒ 保证那个分区在。
    if want_cat == DOWNLOAD_CAT:
        ensure_download_cat()

    items = _read_json(_custom_path(), {})
    if not isinstance(items, dict):
        items = {}
    key = str((item or {}).get("key") or name)
    existed = key in items
    items[key] = {
        "name": name,
        "text": text,
        "cat": want_cat,
        "desc": str((item or {}).get("desc") or ""),
        "source": "market",  # 记一笔来源，界面可据此标"下载来的"
    }
    if not _write_json(_custom_path(), items):
        return {"ok": False, "code": "no-permission",
                "message": "写不进去（没有权限？人设页要用的那份没动）", "key": key, "cat": want_cat}
    return {"ok": True, "code": "ok",
            "message": ("已覆盖同名的人设（%s）" % name) if existed else "已装进人设页",
            "key": key, "cat": want_cat}


def cats() -> dict:
    """现有分区清单（人设页用来摆分类下拉）。

    内置分区来自 `PERSONA_CATS`（web 与 Qt 同源）；用户分区来自
    `data/persona_cats.json`（用户可自建/改名）。**「下载」分区永远在列。**
    """
    built = set()
    try:
        from .persona import PERSONA_CATS
        built = {str(x) for x in set(PERSONA_CATS.values()) if x}
    except Exception as _e:  # noqa: BLE001
        log.debug("取内置分区失败：%s", _e, exc_info=True)
    user = _read_json(_cats_path(), {})
    names = sorted(built)
    if isinstance(user, dict):
        names += [str(k) for k in user if str(k) not in built]
    if DOWNLOAD_CAT not in names:
        names.append(DOWNLOAD_CAT)
    return {"ok": True, "cats": names}


def set_cat(item_key: str, cat: str) -> dict:
    """改一项人设所属分区 —— 用户"把下载来的移到别的分区"就调这个。

    ⛔ 只改这一项的 `cat` 字段，别的一律不动（不是删了重加）。
    """
    k = str(item_key or "").strip()
    c = str(cat or "").strip() or DEFAULT_CAT
    items = _read_json(_custom_path(), {})
    if not isinstance(items, dict) or k not in items:
        return {"ok": False, "code": "not-installed", "message": "没找到这个人设"}
    v = items[k]
    if not isinstance(v, dict):
        return {"ok": False, "code": "broken", "message": "这一项不是合法条目"}
    v["cat"] = c
    # 用户自建的分区也要登记一下，否则下次列分区时它不在（用户会以为没建成）。
    cats = _read_json(_cats_path(), {})
    if not isinstance(cats, dict):
        cats = {}
    if c not in cats:
        try:
            from .persona import PERSONA_CATS
            if c not in set(PERSONA_CATS.values()):
                cats[c] = {"desc": ""}
                _write_json(_cats_path(), cats)
        except Exception:  # noqa: BLE001
            pass
    if not _write_json(_custom_path(), items):
        return {"ok": False, "code": "no-permission", "message": "写不进去（没有权限？）"}
    return {"ok": True, "code": "ok", "message": "已移动分区", "cat": c}
