# -*- coding: utf-8 -*-
"""生成侧 IP 闸门（第 187 项）：**只拦具体的角色名/作品名，绝不拦画风词**。

## 判据是什么（来自判决）

广州 **(2024)粤0192民初113号**：认定侵权的依据是**生成图复现了可识别的具体表达**，
**不是**"像机甲风/日系"。⇒ 所以：

    「画一个赛璐璐风格的少女」   ⇒ **放行**（风格属思想，不受著作权保护）
    「画皮卡丘」                 ⇒ **拦**（可识别的具体角色）

拦画风词的后果是**必然误伤**（把"日系""Q 版"这种描述当成侵权），而放行它的后果是 **0**。

## 三档动作（**不许把"不知道"混进"放行"**）

| 动作 | 什么时候 | 谁处理 |
|---|---|---|
| `block` | 命中**严格维权方**的具体角色名/作品名 | 直接不发（这条是红线） |
| `review` | 命中**政策不明**的作品名（既没查到禁令、也没查到许可） | **留痕**，交给分类器/人工；⛔ **不静默放行** |
| `pass` | 其余（**含全部画风词**） | 照常发 |

## 清单边界（写清楚，免得后人乱加）

1. **严格维权方**只收这五家（依据：公开维权/下架/律师函记录）：
   迪士尼 · 任天堂 · 圆谷／新创华（奥特曼）· 华强方特（熊出没）· 华纳（哈利·波特）。
   ⛔ **不追求穷举**：漏了还有下游分类器兜底，**误伤画风是净损失**。
2. **画风词永不入库**：本模块带一份 `STYLE_WORDS`，判据会**逐个断言它们放行**，
   并且断言**内置清单里不含任何一个画风词**（防以后有人顺手塞进去）。
3. **名单放在代码/配置里，不写进 README**（仓库文本可被关键词检索；文档只写机制与判据）。
4. 用户可在配置里补自己的名单（`ip_guard.extra_block`），**但画风词仍走 `pass`**
   —— 想按风格过滤请用 `image_gen.style_block`（那是**用户自填**的风格过滤，与本模块职责不同）。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: 动作（三档；⛔ 别把 `review` 合并进 `pass`）
ACTIONS = ("block", "review", "pass")

#: **严格维权方** → 具体角色名/作品名关键词（小写匹配）。只收"一提就知道是它家"的词。
#  ⛔ 加词前先问：这是**具体角色/作品**，还是**风格/题材**？后者一律不收。
STRICT_HOLDERS = {
    "disney": ("米老鼠", "米奇", "唐老鸭", "冰雪奇缘", "爱莎", "艾莎", "小美人鱼", "狮子王",
               "mickey", "elsa", "frozen", "disney"),
    "nintendo": ("马里奥", "mario", "塞尔达", "zelda", "林克", "link", "皮卡丘", "pikachu",
                 "宝可梦", "pokemon", "口袋妖怪", "斯普拉遁", "splatoon", "卡比", "kirby"),
    "ultraman": ("奥特曼", "ultraman", "迪迦", "赛罗", "贝利亚", "圆谷", "新创华"),
    "boonie_bears": ("熊出没", "光头强", "熊大", "熊二", "方特"),
    "warner_hp": ("哈利波特", "哈利·波特", "harry potter", "霍格沃茨", "伏地魔", "voldemort",
                  "hogwarts"),
}

#: **政策不明**：查到"没禁令也没许可"的作品 ⇒ 走 `review`（留痕）。⚠️ 只是**举例**，
#  真正的判断靠"查过没有"这件事本身（`judge(..., unclear=True)`），不是靠这张表。
UNCLEAR_HINT = ()

#: **画风词**：风格属思想、不受著作权保护 ⇒ **永不拦**。判据会逐个断言它们放行，
#  并断言上面两张表里**不含**任何一个画风词。
STYLE_WORDS = ("二次元", "日系", "赛璐璐", "厚涂", "q版", "q 版", "机甲风", "兽耳", "国风",
               "中国风", "水彩", "油画", "像素", "像素风", "赛博朋克", "赛博", "手绘",
               "动漫", "写实", "简笔", "厚涂风", "日式", "美式", "古风", "水墨", "扁平",
               "3d 渲染", "3d渲染", "低多边形", "蒸汽波", "浮世绘", "洛丽塔", "哥特")


def _cfg() -> dict:
    """读配置：**挂在 `image_gen.ip_guard` 下**（它只管生成侧这一条路）。"""
    try:
        from .config import get_config
        return ((get_config() or {}).get("image_gen") or {}).get("ip_guard") or {}
    except Exception as e:  # noqa: BLE001
        log.debug("读 ip_guard 配置失败（按默认处理）：%s", str(e)[:80])
        return {}


def is_style_only(prompt: str) -> bool:
    """这条提示里**只**出现画风词、没出现任何具体角色/作品名 ⇒ `True`。

    给下游一个便宜的"确定能放行"判据（也方便判据做**反向对照**）。
    """
    t = str(prompt or "").lower()
    if not any(w in t for w in STYLE_WORDS):
        return False
    act, _hit, _why = _match(t, _cfg())
    return act == "pass"


def _match(t: str, c: dict) -> tuple:
    """内部：把提示词逐个名单过一遍 ⇒ `(action, hit, why)`。"""
    for holder, words in STRICT_HOLDERS.items():
        for w in words:
            if w and w in t:
                return ("block", w, "命中严格维权方（%s）的具体角色/作品名" % holder)
    for w in (c.get("extra_block") or []):
        w = str(w).strip().lower()
        if not w:
            continue
        # ⛔ 补充名单里若填的是**画风词** ⇒ **跳过不拦**：按风格过滤是
        #   `image_gen.style_block` 的事（用户自填、职责单一）。混进本闸门只会把
        #   "风格"当成"侵权"，那正是本项要治的病。
        if w in STYLE_WORDS:
            continue
        if w in t:
            return ("block", w, "命中你补充的名单")
    # ⛔ 画风词**在这里主动放行**（就算有人在 `extra_block` 里填了画风词，也不拦 ——
    #   想按风格过滤请用 `image_gen.style_block`，那是另一件事，职责不许混）。
    for w in STYLE_WORDS:
        if w in t:
            return ("pass", w, "只命中画风词（风格不受著作权保护）")
    return ("pass", "", "没有命中任何受限名单")


def judge(prompt: str, *, unclear: bool = False, cfg: dict = None) -> dict:
    """判这条提示词该怎么处理 ⇒ `{action, hit, why, style_only, checked}`。

    `unclear=True` 表示调用方**已经查过**该作品的政策、结论是"既无禁令也无许可"
    ⇒ 走 `review`（**留痕**，别静默放行）。⛔ 本模块**不联网**，查证是调用方的事。
    """
    c = dict(_cfg())
    if cfg:
        c.update(dict(cfg or {}))
    if c.get("enabled") is False:
        return {"action": "pass", "hit": "", "why": "ip_guard 被关掉了", "style_only": False,
                "checked": 0}
    t = str(prompt or "").lower()
    action, hit, why = _match(t, c)
    style_only = bool(action == "pass" and hit and hit in STYLE_WORDS)
    if action == "pass" and unclear and not style_only:
        # 政策不明 ⇒ 留痕（**不静默放行**）。画风词不在此列 —— 它压根不涉及"某作品的政策"。
        return {"action": "review", "hit": hit, "why": "该作品的政策没查到明确结论 ⇒ 留痕待人工判",
                "style_only": False, "checked": len(STRICT_HOLDERS)}
    return {"action": action, "hit": hit, "why": why, "style_only": style_only,
            "checked": len(STRICT_HOLDERS)}


def words_are_style_only(words) -> list:
    """给判据用：从一组词里挑出**画风词**（应当全部放行的那种）。"""
    out = []
    for w in (words or []):
        s = str(w).strip().lower()
        if s in STYLE_WORDS:
            out.append(s)
    return out
