# -*- coding: utf-8 -*-
"""念之前的文本整形：**只做"清场"，绝不替用户改语气**。

用户 2026-09-17 纠正（原话）：「**行行行不要断成三个字啊，它是一种语气偏快的连读，像那种拿你没办法的感觉**」。
⇒ 我上一版（v2.1.27）给"连续同字"自动插逗号是**错的**：逗号在引擎里就是停顿，等于把连读读成一字一顿，
语气全丢。**本模块从此不许自动插入任何标点**（只有"完全没有标点、长到一口气念不完"的长串才补停顿，
那是呼吸问题，不是语气问题）。

途径调研（详见 `research/TTS-发音与断句-方法调研.md`，含来源与实测）：
  ① **同音字替换**——「行行行」的读音是 xíng ⇒ 写 `形形形`（社区通用土办法，对 edge/sapi/自建服务**都有效**）；
  ② **拼音标注**——`角(jue2)色`（GPT-SoVITS PR#1728）/ `[h][ào]`（CosyVoice3）/ SSML
     `<phoneme alphabet="sapi" ph="xing 2">行</phoneme>`（Azure 文档；**本机实测：只有 .NET `SpeakSsml` 认它，
     `SAPI.SpVoice + SVSFIsXML` 会静默忽略**）。⇒ 引擎支持音素才生效；
  ③ **edge 档没有音素级控制**（维护者原话：自定义 XML 已移除且不会恢复）⇒ edge 档只能走 ①；
  ④ **语气/情绪**靠"参考音频 + 语速"，edge 给不了 ⇒ 要那口"无奈连读"，得用本机的 GPT-SoVITS/CosyVoice 类模型。

所以本模块的契约：
  · `prep(text, cfg)` → `(要说的话, 说明列表, 待标注的拼音)`；
  · 用户表 `voice_reply.pronounce` 一行一条，支持两种写法：
      `行行行=形形形`        —— 同音字替换（任何档都生效）
      `行=拼音:xing2`        —— 拼音标注（只有支持音素的档能用；不支持的档**如实说明并保持原字**）
  · 其余一切照原文念（表情/换行/重复标点这类"引擎会噎住"的东西才清）。
"""
import re

#: 没有标点时，一句话最多念多少个字就补一口呼吸（**只对完全没有标点的长串生效**）
SPLIT_LEN = 22
#: 优先在这些字**后面**断句（虚词，断在这儿最像人说话）
GLUE = "的了是在和就都也很我你他她它吧啊嘛呀呢把被给对从向"
#: 算标点的字符（这些地方本来就该停）
PUNCT = "，。！？、；：,.;!?:~…—-（）()《》〈〉【】「」『』\"'“”‘’【】 \t\n\r"


def _clean(s: str) -> str:
    """去表情/换行/重复标点（引擎对这些要么乱念、要么噎住）。**不动正常文字**。"""
    keep = []
    for ch in str(s or ""):
        o = ord(ch)
        if (0x1F000 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF
                or 0xFE00 <= o <= 0xFE0F or o == 0x200D or 0x1F1E6 <= o <= 0x1F1FF):
            continue
        keep.append(ch)
    s = "".join(keep).replace("\r", " ").replace("\n", "，")
    s = re.sub(r"[，、]{2,}", "，", s)
    s = re.sub(r"[。！？]{2,}", lambda m: m.group(0)[0], s)
    s = s.replace("，。", "。").replace("，！", "！").replace("，？", "？").replace("，、", "、")
    return s.strip("， ")


def _parse_pinyin(v: str):
    """把 `拼音:xing2` / `xing2` 解析成 `xing 2`（SAPI 音标集的写法＝拼音+空格+声调）。"""
    t = str(v or "").strip()
    if t.startswith("拼音:") or t.startswith("拼音："):
        t = t[3:]
    t = t.strip()
    m = re.match(r"^([a-zA-ZüÜ]+)\s*([0-5])$", t)
    if m:
        return "%s %s" % (m.group(1).lower(), m.group(2))
    return ""


def pronounce_rules(cfg) -> tuple:
    """念法表 ⇒ `(文本替换表, 拼音表)`。

    文本替换表＝[(原文, 替换字)]，长原文排前面（先替换长串，免得被短串切碎）。
    拼音表＝[(原文, "xing 2")]，写法 `原文=拼音:xing2`（或 `原文=xing2`）。
    """
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
    texts, phones = [], []
    for a, b in pairs:
        a, b = str(a).strip(), str(b).strip()
        if not a or not b:
            continue
        ph = _parse_pinyin(b) if (b.startswith("拼音") or re.match(r"^[a-zA-ZüÜ]+\s*[0-5]$", b)) else ""
        if ph:
            phones.append((a, ph))
        else:
            texts.append((a, b))
    texts.sort(key=lambda kv: -len(kv[0]))
    return texts, phones


def split_same_char(s: str, least: int = 3) -> str:
    """连续同一个字 ≥`least` 个 ⇒ 每字之间插「，」。

    ⚠️ **默认不再使用**（v2.1.27 的错误做法）：连续同字往往是**语气偏快的连读**（「行行行」「好好好」），
    插逗号＝一字一顿，把语气读没。留着只为"用户显式要求逐字念"的场景（如念验证码）。
    """
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
    """把一段**没有标点**的话按长度补一口呼吸，优先断在虚词后。"""
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
    # 尾巴太短（<6 字）别单成一口，并回上一段
    if len(out) >= 2 and len(out[-1]) < 6:
        out[-2] = out[-2] + out[-1]
        out.pop()
    return "，".join(out)


def split_long(s: str, maxlen: int = SPLIT_LEN) -> str:
    """整句里，只对**完全没有标点**的长串补停顿（有标点的地方原样保留）。"""
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


def prep(text, cfg=None) -> tuple:
    """整形 ⇒ `(要说的话, 说明列表, 拼音表)`。

    只用**用户表**改字；不插任何标点（长串呼吸除外）。说明列表用来**如实写出改了什么**。
    """
    s = _clean(text)
    notes = []
    if not s:
        return "", [], []
    texts, phones = pronounce_rules(cfg)
    for a, b in texts:
        if a in s:
            s = s.replace(a, b)
            notes.append("念法表 %s→%s" % (a, b))
    s2 = split_long(s)
    if s2 != s:
        notes.append("长句补呼吸")
        s = s2
    return s, notes, phones
