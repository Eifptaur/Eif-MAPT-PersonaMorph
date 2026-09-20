# -*- coding: utf-8 -*-
"""监听目标解析：**按 wxid 认群**，群名只当"给人看"和老配置的兼容入口。

⛔ 第四轮审计候选 **W-1**（`console_html.py:2334/2336/2338/2380`、`scripts/persona_morph.py:1626`、
`persona_morph.py:642` 三处都拿**群名**当身份）：两个同名群 ⇒ **勾一个＝监听两个**；
运行明细里两间群同名 ⇒ 「这条回复挂在群 X 名下」无法唯一确定是哪一间（网友的串群报障就卡在这一层）。

口径（三处统一走这里，别再各写一份匹配）：
  · 白名单条目**优先按 wxid 认**（控制台勾选现在存 wxid）——wxid 是唯一身份；
  · 老配置里存的是**群名** ⇒ 仍然生效（**只有一个**群叫这个名字时）；
  · 同名群 + 白名单写的是名字 ⇒ **跳过并报出来**（fail-closed：不替用户猜是哪一间，
    免得"勾一个监听两个"把消息回到别的群去），并给出可复制的 wxid 让用户重勾。
"""
from __future__ import annotations


def resolve_groups(groups, whitelist, deny=None) -> dict:
    """把白名单解析成**唯一**的监听群列表。

    返回 `{"groups": [...], "ambiguous": [{"name", "wxids"}], "missing": [名字...], "used_name": [名字...]}`
      · `groups`     真正要监听的群（已按 wxid 去重、已排除 deny）
      · `ambiguous`  白名单里写名字、但**有多个**群叫这个名 ⇒ 这些群**没被监听**
      · `missing`    白名单里的名字/wxid 一个群都没匹配上（改名了？退群了？）
      · `used_name`  哪些条目是靠"群名唯一"匹配上的（老配置在用，建议重勾成 wxid）
    """
    out = {"groups": [], "ambiguous": [], "missing": [], "used_name": []}
    gs = [g for g in (groups or []) if isinstance(g, dict) and str(g.get("wxid") or "").strip()]
    _deny = {str(x).strip() for x in (deny or []) if str(x).strip()}
    by_wxid = {str(g["wxid"]): g for g in gs}
    by_name: dict = {}
    for g in gs:
        by_name.setdefault(str(g.get("name") or ""), []).append(g)
    items = [str(x).strip() for x in (whitelist or []) if str(x).strip()]
    picked, seen = [], set()

    def _add(g) -> None:
        w = str(g["wxid"])
        if w in seen:
            return
        seen.add(w)
        picked.append(g)

    def _denied(g) -> bool:
        return str(g["wxid"]) in _deny or str(g.get("name") or "") in _deny

    if not items:                                   # 白名单为空 = 所有群（老口径不变）
        for g in gs:
            if not _denied(g):
                _add(g)
    else:
        for it in items:
            g = by_wxid.get(it)
            if g is None:
                hits = list(by_name.get(it) or [])
                if len(hits) == 1:
                    g = hits[0]
                    out["used_name"].append(it)
                elif len(hits) > 1:
                    out["ambiguous"].append({"name": it, "wxids": [str(h["wxid"]) for h in hits]})
                    continue                        # fail-closed：不猜
                else:
                    out["missing"].append(it)
                    continue
            if not _denied(g):
                _add(g)
    out["groups"] = picked
    return out


def describe(groups, res, wxid_of=None) -> str:
    """一行给人看的说明（日志与概览共用一份文案，别两处各写一遍）。

    `wxid_of`：可选，`gw -> 展示用 wxid 尾巴`；默认自动取。
    """
    res = res or {}
    names = ", ".join(str(g.get("name") or g.get("wxid")) for g in (groups or [])[:15])
    parts = ["监听群 %d 个：%s" % (len(groups or []), names if names else "（无）")]
    amb = res.get("ambiguous") or []
    if amb:
        _a = "；".join("「%s」有 %d 个同名群（wxid: %s）" % (x["name"], len(x["wxids"]), "、".join(x["wxids"][:4]))
                       for x in amb[:3])
        parts.append("⚠️ **这几个群没监听**（名字对不上唯一身份）：%s ⇒ 到控制台重新勾一次"
                     "（勾选现在存 wxid，不会再靠名字认群）" % _a)
    miss = res.get("missing") or []
    if miss:
        parts.append("⚠️ 白名单里这些没匹配到任何群（改名/退群了？）：%s" % "、".join(miss[:5]))
    un = res.get("used_name") or []
    if un:
        parts.append("（这些是老配置写的群名，按「名字唯一」匹配上的，建议重勾：%s）" % "、".join(un[:5]))
    return "｜".join(parts)
