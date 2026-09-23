# -*- coding: utf-8 -*-
"""sec 键元数据层 —— 从 web 控制台源码**运行时解析**出每个面板的行结构。

为什么必须有这一层（交接件硬规矩：不许手抄几百个配置项）：
  `agent/console_html.py` 的每个 `<section id="sec-…">` 里，每行配置都带
  `data-cfg="点.path"`，控件类型就写在 HTML 标签上（input type / select /
  textarea / chips）。⇒ web 侧的「键元数据」就藏在这份 HTML 里，
  它就是版式与内容的唯一真值。
  ⇒ Qt 侧不复制这份数据，而是**照 web 侧同样的生成思路**：启动时解析
    HTML → 行元数据（标签 / 控件类型 / 键 / 占位 / 说明 / 下拉选项），
    再由 `panels_qt.py` 按元数据生成控件。web 改了行，Qt 侧跟着变。
  ⇒ 默认值不另编：从 `config.example.json` 按点路径回填（web 侧 data-cfg
    的回填逻辑同源）。

本文件只读：**不 import 产品的任何写接口**，只读两个文件（console_html.py /
config.example.json），一行产品代码不动。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB_PATH = HERE.parents[0] / "agent" / "console_html.py"
CFG_PATH = HERE.parents[0] / "config.example.json"

# 本棒要建面板的 26 个 sec（27 减去机器人=主面板），顺序照 secs() 的 web 文档序。
# 第 2 棒 9 项（日常/智能），第 3 棒补齐 17 项（内容/运行/外观）。
SECS_OF_THIS_BATCH = (
    "overview", "wechat", "persona", "check",
    "model", "memory", "memory-set", "search", "community",
    "media", "tts", "imggen", "videogen", "tools", "poke",
    "sessions", "feedback", "send", "log", "vermat", "server", "advanced",
    "ui", "json", "cursor", "wavefx",
)


@dataclass
class Row:
    """一行配置的元数据 —— web 侧一个 `.row` 的 Qt 等价物。

    kind ∈ text | password | number | range | checkbox | select |
           textarea | chips | info（纯说明行，无控件）
    """

    kind: str
    label: str
    cfg: str = ""
    placeholder: str = ""
    hint: str = ""
    options: list[tuple[str, str]] = field(default_factory=list)  # (value, 文案)
    default: object = None


@dataclass
class Sec:
    key: str
    title: str
    desc: str = ""
    rows: list[Row] = field(default_factory=list)


def _clean(s: str) -> str:
    """去 HTML 标签 + 解常用实体（hint 里混着 <b>/<code> 与 &lt;）。"""
    s = re.sub(r"<[^>]+>", "", s)
    for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def _first_sentence(s: str, limit: int = 110) -> str:
    """说明只取第一句 —— web 侧 hint 常有长段落，Qt 行高装不下整段。"""
    s = _clean(s)
    m = re.match(r"(.{8,}?[。；!?！？])", s)
    out = m.group(1) if m else s
    return out[:limit] + ("…" if len(out) > limit else "")


def _placeholder(tag: str) -> str:
    m = re.search(r"""placeholder=["']([^"']*)["']""", tag)
    return _clean(m.group(1)) if m else ""


def _default_of(cfg: str, _cache: list = []) -> object:  # noqa: B006
    """按点路径从 config.example.json 取默认值（找不到就 None，由控件用空态）。"""
    if not cfg:
        return None
    if not _cache:
        try:
            _cache.append(json.loads(CFG_PATH.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            _cache.append({})
    cur: object = _cache[0]
    for part in cfg.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _hint_of(chunk: str) -> str:
    """行说明：优先取 web 侧真正的 `.hint` span。

    注意：不能直接对整段去标签 —— select 的 <option> 文本会被拼进说明里
      （「模型厂商」行的说明里混出十几个厂商名，2026-09-23 拍图实锤）。
    """
    spans = re.findall(r'<span class="hint">(.*?)</span>', chunk, re.S)
    if spans:
        return _first_sentence(spans[0])
    stripped = re.sub(r"<select.*?</select>|<option.*?</option>", " ", chunk, flags=re.S)
    return _first_sentence(stripped)


def _parse_row(chunk: str, label: str) -> Row:
    tag_m = re.search(r"<input[^>]*>|<select[^>]*>|<textarea[^>]*>|<div class=\"chips\"", chunk, re.S)
    if tag_m is None:
        # 纯说明 / 纯状态行（web 里是 <b id=…> 检测中 / 按钮组等）—— info 行
        return Row(kind="info", label=label, hint=_hint_of(chunk))
    tag = tag_m.group(0)
    cfg_m = re.search(r'data-cfg="([\w.]+)"', chunk)
    cfg = cfg_m.group(1) if cfg_m else ""
    hint = _hint_of(chunk)
    if tag.startswith("<input"):
        typ_m = re.search(r'type="(\w+)"', tag)
        typ = typ_m.group(1) if typ_m else "text"
        if typ == "checkbox":
            d = _default_of(cfg)
            d = ("checked" in tag) if d is None else bool(d)
            return Row("checkbox", label, cfg, hint=hint, default=d)
        if typ == "password":
            return Row("password", label, cfg, _placeholder(tag), hint, default=_default_of(cfg))
        if typ == "number":
            return Row("number", label, cfg, _placeholder(tag), hint, default=_default_of(cfg))
        if typ == "range":
            return Row("range", label, cfg, _placeholder(tag), hint, default=_default_of(cfg))
        return Row("text", label, cfg, _placeholder(tag), hint, default=_default_of(cfg))
    if tag.startswith("<select"):
        body_m = re.search(r"<select[^>]*>(.*?)</select>", chunk, re.S)
        opts = re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', body_m.group(1)) if body_m else []
        opts = [(v, _clean(t)) for v, t in opts if _clean(t)]
        if not opts and body_m:  # 个别 option 不带 value 属性
            opts = [("", _clean(t)) for t in re.findall(r"<option[^>]*>([^<]*)</option>", body_m.group(1)) if _clean(t)]
        d = _default_of(cfg)
        return Row("select", label, cfg, hint=hint, options=opts, default=d)
    if tag.startswith("<textarea"):
        return Row("textarea", label, cfg, _placeholder(tag), hint, default=_default_of(cfg))
    return Row("chips", label, cfg, hint=hint, default=_default_of(cfg))


_CACHE: dict[str, Sec] = {}


def secs() -> dict[str, Sec]:
    """解析一次，全进程共享。web 源码变了重启即跟（原型的"诚实"边界）。"""
    global _CACHE
    if _CACHE:
        return _CACHE
    text = WEB_PATH.read_text(encoding="utf-8")
    for m in re.finditer(r'<section id="sec-([\w-]+)" class="card" data-sec>(.*?)</section>', text, re.S):
        key, body = m.group(1), m.group(2)
        hd = re.search(r'<div class="sec-hd"><h2>([^<]+)</h2>', body) or re.search(r"<h2>([^<]+)</h2>", body)
        dsc = re.search(r'<div class="desc">(.{0,400}?)</div>', body, re.S)
        sec = Sec(key=key, title=_clean(hd.group(1)) if hd else key,
                  desc=_first_sentence(dsc.group(1), 150) if dsc else "")
        for rm in re.finditer(
            r'<div class="row"[^>]*>\s*<label>([^<]+)</label>(.*?)(?=<div class="row"|<div class="btns"|</section>|<div class="mid"|<hr)',
            body, re.S,
        ):
            sec.rows.append(_parse_row(rm.group(2), _clean(rm.group(1))))
        _CACHE[key] = sec
    return _CACHE


def get(sec: str) -> Sec:
    return secs().get(sec) or Sec(key=sec, title=sec)
