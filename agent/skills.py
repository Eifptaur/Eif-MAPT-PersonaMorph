# -*- coding: utf-8 -*-
"""Skill：一段"什么情况该怎么做"的说明书，由**模型自己判断**该不该读。

**与"工具"的分工**（一条判据）：
  · **工具**（`tools.d/` ＋ `agent/user_tools.py`）＝ 一个**能调用的函数**（打接口、查数据）；
    调用时机由模型按 schema 决定，但**调用是确定的**（调了就执行）。
  · **Skill**（本模块）＝ 一段**给模型看的正文**（Markdown），**没有可执行代码**；
    模型先在清单里看见"有这么一个 skill"，**判断用得上**才去 `skill({name})` 把正文读进来。
  ⇒ 一句话：**规则能写死的走工具，要理解人话的走 Skill。**

**为什么清单与正文分开**（不是一次全塞进提示词）：
  正文动辄几百上千字，而绝大多数轮次一个 skill 都用不上 ⇒
  全量注入会让每一轮都白付这笔 token。所以：
  · **清单**（只有 name ＋ 一句说明）注入**用户消息**；
  · **正文**由 `skill` 工具按需取。
  ⛔ 清单**不许注入 system prompt** —— system 段必须保持纯静态（DeepSeek 前缀缓存靠它），
     而 skill 是用户随时会增删的目录内容。

**目录形态两型都收**（照 DSH / QQ-agent 的既有做法，两型**功能完全等价**）：
  · **目录包** `skills/<name>/SKILL.md`（可带附件、脚本等旁文件）
  · **扁平文件** `skills/<name>.md`
  `name` 一律 kebab-case（小写字母/数字，用 `-` 连接）。

**单开关**：`skills.enabled` 关掉 ⇒ 清单不注入、`skill` 工具不注册 ——
**一处关掉全失效**，不会出现"界面关了还在偷偷提供"。

**⛔ 扩展安全**：skill 只能提供**正文**，不能注册工具、不能改发送链、不能改落点。
（这是与"插件型"扩展的分界；本模块**不 import 任何发送/窗口模块**。）
"""
from __future__ import annotations

import logging
import os
import re

log = logging.getLogger("persona-morph")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: skill 名：kebab-case（小写字母开头，可含数字与 `-`，总长 2~40）
NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")

#: 取名的合法范围（与 `market.py` 同一口径，避免两处各写一遍）
_MAX_NAME = 40

#: 正文单文件上限（1 MB）—— 正常说明书几百字；超大文件多半是误放
_MAX_BODY_BYTES = 1024 * 1024

#: 目录根下的**文档**，不是 skill（`tools.d/` 靠 `.json` 后缀天然避开这类文件，
#: 本模块用 `.md` 后缀 ⇒ 必须显式排除，否则 README 会变成一条"坏清单"噪音）
_DOC_NAMES = frozenset(("readme.md", "skill.md", "index.md", "说明.md"))


def _cfg() -> dict:
    try:
        from .config import get_config
        return dict(get_config().get("skills") or {})
    except Exception:
        return {}


def skills_dir() -> str:
    """skill 目录的**唯一真值来源**。

    ⚠️ 与 `user_tools.manifest_dir()` 同一个道理：这里被加载器、市场模块、控制台三处读
    ⇒ 只留这一个函数，⛔ 别在别处再解析一次配置。
    """
    d = str(_cfg().get("dir") or "skills")
    return d if os.path.isabs(d) else os.path.join(ROOT, d)


def enabled() -> bool:
    """总开关。关掉 ⇒ 清单不注入、`skill` 工具不注册（**一处关掉全失效**）。"""
    return bool(_cfg().get("enabled"))


def _front_matter(text: str) -> tuple:
    """解析 SKILL.md 的 YAML 头（只支持 `key: value` 平铺，不引入 YAML 依赖）。

    返回 `(meta, body)`。**没有 YAML 头也能用**：缺 name 时由调用方按**文件名**定
    —— ⛔ 但 `description` 推不出来就报问题，不静默放行（原因见 `_one`）。
    """
    text = str(text or "")
    meta = {}
    body = text
    if text.lstrip().startswith("---"):
        stripped = text.lstrip()
        end = stripped.find("\n---", 3)
        if end != -1:
            head = stripped[3:end]
            body = stripped[end + 4:].lstrip("\n")
            for line in head.split("\n"):
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip().strip("'\"")
    return meta, body


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read(_MAX_BODY_BYTES)


def _bad(path: str, why: str) -> dict:
    return {"id": os.path.basename(path), "path": path, "name": "", "description": "",
            "body": "", "ok": False, "why": why}


def _one(path: str, name: str) -> dict:
    """读并校验一个 skill（目录形态传 SKILL.md，扁平形态传 .md）。

    ⭐ **id 一律取"文件名/目录名"**（`name` 参数由 `load()` 从路径推出）：
    id 要用来做落点目录名与 hash 校验（PLAN「落点＝`skills/<id>/`」），必须是**路径上
    就能唯一确定**的东西；front matter 里的 `name:` 只是给人看的显示名，**不参与校验**
    （允许中文、允许与文件名不同）。
    """
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return _bad(path, "读不到文件：%s" % e)
    if size > _MAX_BODY_BYTES:
        return _bad(path, "正文超过 %d KB（多半是放错了文件）" % (_MAX_BODY_BYTES // 1024))
    if not NAME_RE.match(name) or len(name) > _MAX_NAME:
        return _bad(path, "名字「%s」不合规（只能小写字母/数字，用 - 连接）" % name)
    try:
        meta, body = _front_matter(_read(path))
    except OSError as e:
        return _bad(path, "读文件失败：%s" % e)
    desc = str(meta.get("description") or "").strip()
    if not body.strip():
        return _bad(path, "正文是空的")
    if not desc:
        # ⛔ 不从正文里"拿第一段凑合用"：description 是**模型判断该不该用**的唯一依据，
        #   缺了它这条 skill **永远不会被选中** —— 那就是个"装了但永远不触发"的隐形坏项，
        #   必须报出来（"装上了却不工作"比"装不上"更难查）。
        return _bad(path, "缺 description（模型靠它判断该不该用这个 skill；"
                          "请在文件开头的 --- 头里加一行 `description: 一句话说明什么情况该用它`）")
    return {"id": name, "path": path, "name": str(meta.get("name") or name),
            "description": desc[:200], "body": body, "ok": True, "why": ""}


def load() -> tuple:
    """扫目录，返回 `(skills, problems)`。

    · `skills`   = 可用的（已按名字排序，顺序稳定 ⇒ 提示词前缀可复用）
    · `problems` = **坏清单逐条报出**（不静默跳过；界面直接展示）
    目录不存在 ⇒ 两者都空（**这不是错误**：出厂就是空目录）。
    """
    d = skills_dir()
    out, problems = [], []
    if not os.path.isdir(d):
        return out, problems
    try:
        entries = sorted(os.listdir(d))
    except OSError as e:
        return out, [{"id": d, "path": d, "why": "目录读不到：%s" % e}]
    seen = {}
    for ent in entries:
        if ent.startswith(".") or ent.startswith("_"):
            continue                      # 隐藏文件与 `_` 前缀＝本地草稿，不算 skill
        full = os.path.join(d, ent)
        if os.path.isdir(full):
            md = os.path.join(full, "SKILL.md")
            if not os.path.isfile(md):
                md2 = os.path.join(full, "skill.md")
                if not os.path.isfile(md2):
                    problems.append({"id": ent, "path": full,
                                     "why": "目录里没有 SKILL.md（目录形态必须有它；"
                                            "若只想放单个文件，请用 skills/<name>.md）"})
                    continue
                md = md2
            item = _one(md, ent)
        elif ent.lower().endswith(".md"):
            if ent.lower() in _DOC_NAMES:
                continue                  # 目录说明文档，不是 skill
            item = _one(full, os.path.splitext(ent)[0])
        else:
            continue                      # 其它后缀不认（README.txt 这类不该被当 skill）
        if not item["ok"]:
            problems.append({"id": item["id"], "path": item["path"], "why": item["why"]})
            continue
        if item["id"] in seen:
            problems.append({"id": item["id"], "path": item["path"],
                             "why": "名字与 %s 重复 ⇒ 两个都不收（先改名再放）"
                                    % os.path.relpath(seen[item["id"]], d)})
            continue
        seen[item["id"]] = item["path"]
        out.append(item)
    # 重复项要把先前收进去的那条也摘掉（否则"两个都不收"名不符实）
    dup = {p["id"] for p in problems if "重复" in p["why"]}
    if dup:
        out = [s for s in out if s["id"] not in dup]
    return out, problems


def get(name: str) -> dict | None:
    """按名字取一个 skill（给 `skill` 工具用）。取不到返回 None（调用方如实说"没有这个"）。"""
    n = str(name or "").strip().lower()
    if not n:
        return None
    for s in load()[0]:
        if s["id"] == n:
            return s
    return None


def catalog_text() -> str:
    """给模型看的**清单**（只有名字 ＋ 一句说明 ＋ 一句用法）。

    ⛔ 这里**不含正文**（正文由 `skill` 工具按需取）—— 全量塞进来等于每轮白付这笔 token。
    关掉总开关、或一个 skill 都没有 ⇒ 返回**空串**（调用方什么都不加，不硬凑一句话）。
    形态照 DSH：以 `<system-reminder>` 包起来，明确"这是可用清单、要读正文得调工具"。
    """
    if not enabled():
        return ""
    items = load()[0]
    if not items:
        return ""
    # 上限：清单是**每轮都注入**的，条数多了会持续挤占本轮 token ⇒ 按配置截断。
    # 截断时明说"还有 N 条没列"，⛔ 不静默丢（否则模型以为只有这些）。
    cap = _cfg().get("max_items")
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        cap = 0
    hidden = 0
    if cap > 0 and len(items) > cap:
        hidden = len(items) - cap
        items = items[:cap]
    lines = ["<system-reminder>",
             "以下是本机已装好的 Skill 清单（**只有名字和用途，正文要调 skill 工具取**）。",
             "它们是「什么情况该按什么套路做」的说明书 —— 判断用得上再读，用不上就别读。",
             "读法：调 skill 工具，name 填下表里的名字（原样照抄）。", ""]
    for s in items:
        lines.append("- %s：%s" % (s["id"], s["description"]))
    if hidden:
        lines.append("（另有 %d 条未列出：清单有上限。真要找，调 skill 工具时会告诉你" % hidden)
        lines.append("当前可用的全部名字。）")
    lines.append("</system-reminder>")
    return "\n".join(lines)


def snapshot() -> dict:
    """给控制台/市场模块看的现状（三态可解释：为什么没生效）。"""
    items, problems = load()
    it = [
        {"id": s["id"], "description": s["description"],
         "path": os.path.relpath(s["path"], ROOT).replace("\\", "/")}
        for s in items
    ]
    return {"enabled": enabled(), "dir": skills_dir(), "count": len(it),
            "items": it, "problems": problems}
