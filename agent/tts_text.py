# -*- coding: utf-8 -*-
"""念之前把文本整形：**断句 + 多音字**。

用户 2026-09-17 原话：「他说的明明是"行行行"，但是变成了"行行hang行"」——这类问题的根在**切词**：
引擎拿到「行行行」这种连续同字，会把中间那个切成别的词（行 háng/xíng 是多音字）。
换引擎解决不了（任何引擎都可能这么切），换引擎还是**用户侧的成本** ⇒ 我们在**送进引擎之前把话说清楚**：
加停顿、把念错的地方按用户给的念法换掉。这条路对全部后端（edge / sapi / 本地 http 服务）都有效。

三条规则（顺序＝用户表优先，用户永远能一票否决自动处理）：
  ① `voice_reply.pronounce` 念法表（"原文=念法"，多条用换行/分号/竖线分隔）；
  ② **连续同一个字 ≥3 个 ⇒ 每字之间插「，」**（行行行 → 行，行，行；哈哈哈同理，更自然且不伤原意）；
  ③ **没有标点的长串按 ~`SPLIT_LEN` 字断句**，优先断在虚词后面（一口气念 40 字谁都听不清）。
另外清掉表情/换行/重复标点（引擎对这些要么乱念、要么噎住）。
"""
import re

#: 没有标点时，一句话最多念多少个字就断一口
SPLIT_LEN = 22
#: 优先在这些字**后面**断句（虚词，断在这儿最像人说话）
GLUE = "的了是在和就都也很我你他她它吧啊嘛呀呢把被给对从向"
#: 算标点的字符（这些地方本来就该停）
PUNCT = "，。！？、；：,.;!?:~…—-（）()《》〈〉【】「」『』\"'“”‘’【】 \t\n\r"


def _clean(s: str) -> str:
    """去表情/换行/重复标点。"""
    keep = []
    for ch in str(s or ""):
        o = ord(ch)
        if (0x1F000 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF
                or 0xFE00 <= o <= 0xFE0F or o == 0x200D or 0x1F1E6 <= o <= 0x1F1FF):
            continue                                  # emoji / 变体选择符 / 零宽连接 / 区域旗
        keep.append(ch)
    s = "".join(keep).replace("\r", " ").replace("\n", "，")
    s = re.sub(r"[，、]{2,}", "，", s)
    s = re.sub(r"[。！？]{2,}", lambda m: m.group(0)[0], s)
    s = s.replace("，。", "。").replace("，！", "！").replace("，？", "？").replace("，、", "、")
    return s.strip("， ")


def pronounce_rules(cfg) -> list:
    """念法表 ⇒ [(原文, 念法)]，长原文排前面（先替换长串，免得被短串切碎）。"""
    raw = (cfg or {}).get("pronounce")
    pairs = []
    if isinstance(raw, dict):
        pairs = list(raw.items())
    elif isinstance(raw, (list, tuple)):
        for it in raw:
            if isinstance(it, (list, tuple)) and len(it) >= 2:
                pairs.append((it[0], it[1]))
            elif isinstance(it, str) and "=" in it:
                pairs.append(tuple(it.split("=", 1)))
    elif isinstance(raw, str):
        txt = raw.replace("|", "\n").replace("；", "\n").replace(";", "\n")
        for line in txt.splitlines():
            if "=" in line:
                pairs.append(tuple(line.split("=", 1)))
    out = [(str(a).strip(), str(b).strip()) for a, b in pairs if str(a).strip()]
    out.sort(key=lambda kv: -len(kv[0]))
    return out


def split_same_char(s: str, least: int = 3) -> str:
    """连续同一个字 ≥`least` 个 ⇒ 每字之间插「，」。"""
    out, i, n = [], 0, len(s)
    while i < n:
        ch = s[i]
        j = i
        while j < n and s[j] == ch:
            j += 1
        run = j - i
        if run >= least and ch not in PUNCT:
            out.append("，".join([ch] * run))
        else:
            out.append(s[i:j])
        i = j
    return "".join(out)


def _split_one(seg: str, maxlen: int) -> str:
    """把一段**没有标点**的话按长度断，优先断在虚词后。"""
    if len(seg) <= maxlen:
        return seg
    out, buf = [], ""
    for ch in seg:
        buf += ch
        if len(buf) >= maxlen:
            cut = -1
            for k in range(len(buf) - 1, max(0, len(buf) - 10) - 1, -1):
                if buf[k] in GLUE:
                    cut = k + 1
                    break
            if cut < 6:
                cut = len(buf)
            out.append(buf[:cut])
            buf = buf[cut:]
    if buf:
        out.append(buf)
    # 尾巴太短（<6 字）就别单成一口——「…白色的，花」这种断法听着更碎 ⇒ 并回上一段（不加停顿）
    if len(out) >= 2 and len(out[-1]) < 6:
        out[-2] = out[-2] + out[-1]
        out.pop()
    return "，".join(out)


def split_long(s: str, maxlen: int = SPLIT_LEN) -> str:
    """整句里，只对**没有标点**的长串做断句（有标点的地方原样保留）。"""
    out, buf = [], ""
    for ch in s:
        if ch in PUNCT:
            if buf:
                out.append(_split_one(buf, maxlen)); buf = ""
            out.append(ch)
        else:
            buf += ch
    if buf:
        out.append(_split_one(buf, maxlen))
    return "".join(out)


def prep(text, cfg=None, maxlen: int = SPLIT_LEN) -> tuple:
    """整形 ⇒ (要说的话, 说明列表)。

    说明列表用来在日志/返回里**如实写出改了什么**（不许默默改用户的话）。
    """
    s = _clean(text)
    notes = []
    if not s:
        return "", []
    for a, b in pronounce_rules(cfg):
        if a and a in s:
            s = s.replace(a, b)
            notes.append("念法表 %s→%s" % (a, b))
    s2 = split_same_char(s)
    if s2 != s:
        notes.append("连续同字断句")
        s = s2
    s3 = split_long(s, maxlen)
    if s3 != s:
        notes.append("长句断句")
        s = s3
    return s, notes
