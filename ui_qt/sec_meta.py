# -*- coding: utf-8 -*-
"""sec 键元数据层 —— 从 web 控制台源码**运行时解析**出每个面板的行结构。

  `assets/console/index.html` 的每个 `<section id="sec-…">` 里，每行配置都带
  `data-cfg="点.path"`，控件类型就写在 HTML 标签上（input type / select /
  textarea / chips）。⇒ web 侧的「键元数据」就藏在这份 HTML 里，
  它就是版式与内容的唯一真值。
  ⇒ Qt 侧不复制这份数据，而是**照 web 侧同样的生成思路**：启动时解析
    HTML → 行元数据（标签 / 控件类型 / 键 / 占位 / 说明 / 下拉选项），
    再由 `panels_qt.py` 按元数据生成控件。web 改了行，Qt 侧跟着变。
  ⇒ 默认值不另编：从 `config.example.json` 按点路径回填（web 侧 data-cfg
    的回填逻辑同源）。

本文件只读：**不 import 产品的任何写接口**，只读两个文件（assets/console/index.html /
config.example.json），一行产品代码不动。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB_PATH = HERE.parents[0] / "assets" / "console" / "index.html"
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
     P0-2 新增（真机复验「按钮和选单都不见了」的根因）：
           buttons（按钮组）| status（状态行）| table（表格/矩阵）
    —— 此前无 input/select/textarea 的块一律降级成 info 纯文字，web 真实存在的
       按钮组/状态行/矩阵全蒸发。新增三类型把它们如实还原。

    `buttons`：`actions` 存 `[(文案, 动作id)]`；`status`：`status_id` 存 web 的 `<b id>`；
    `table`：`headers` 表头 + `rows` 数据行（list[list[str]]）。
    """

    kind: str
    label: str
    cfg: str = ""
    html_id: str = "" # 控件的 HTML id（providerSel 等纯 JS 控件的识别依据）
    placeholder: str = ""
    hint: str = ""
    options: list[tuple[str, str]] = field(default_factory=list) # (value, 文案)
    default: object = None
    actions: list[tuple[str, str]] = field(default_factory=list) # buttons：(文案, 动作id)
    status_id: str = "" # status：web <b id>
    headers: list[str] = field(default_factory=list) # table：表头
    rows: list[list[str]] = field(default_factory=list) # table：数据行
    # mid 子行的可辨识标记（渲染层据此缩进/折叠，与顶层 row 区分）
    sub: bool = False # True = 位于某个 <div class="mid"> 块内（mid 子行）
    group: str = "" # 所属 mid 块的标题（取最近前置 <div class="desc"> 或顶层 row 标签）
    indent: int = 0 # 渲染缩进档位（mid 子行 = 1，顶层 = 0）
    # table 每行首列是否为勾选框（功能自检清单「结果」列等）——
    #   原 _clean 把 <input type=checkbox> 剥成空文本 ⇒ Qt 渲染纯文字清单、勾选列蒸发。
    cell_checks: list[bool] = field(default_factory=list)


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


def _default_of(cfg: str, _cache: list = []) -> object: # noqa: B006
    """按点路径从 config.example.json 取默认值（找不到就 None，由控件用空态）。"""
    if not cfg:
        return None
    if not _cache:
        try:
            _cache.append(json.loads(CFG_PATH.read_text(encoding="utf-8")))
        except Exception: # noqa: BLE001
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
      。
    """
    spans = re.findall(r'<span class="hint">(.*?)</span>', chunk, re.S)
    if spans:
        return _first_sentence(spans[0])
    stripped = re.sub(r"<select.*?</select>|<option.*?</option>", " ", chunk, flags=re.S)
    return _first_sentence(stripped)


def _parse_row(chunk: str, label: str) -> Row:
    tag_m = re.search(r"<input[^>]*>|<select[^>]*>|<textarea[^>]*>|<div class=\"chips\"", chunk, re.S)
    if tag_m is None:
        # ⛔ P0-2：无控件 ≠ 纯说明。web 侧这里常是**按钮组 / 状态行 / 表格**，
        #   原来一律 info ⇒ 按钮与表格蒸发（真机复验「按钮和选单都不见了」）。
        #   ⇒ 按内含 DOM 分三档还原；都不命中的才是真「纯说明」。
        return _parse_nonwidget_row(chunk, label)
    tag = tag_m.group(0)
    cfg_m = re.search(r'data-cfg="([\w.]+)"', chunk)
    cfg = cfg_m.group(1) if cfg_m else ""
    idm = re.search(r'id="([\w-]+)"', tag)
    html_id = idm.group(1) if idm else ""
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
        if not opts and body_m: # 个别 option 不带 value 属性
            opts = [("", _clean(t)) for t in re.findall(r"<option[^>]*>([^<]*)</option>", body_m.group(1)) if _clean(t)]
        d = _default_of(cfg)
        return Row("select", label, cfg, html_id=html_id, hint=hint, options=opts, default=d)
    if tag.startswith("<textarea"):
        return Row("textarea", label, cfg, _placeholder(tag), hint, default=_default_of(cfg))
    if tag.startswith("<div class=\"chips\""):
        # ⛔ chips 行在 web 里常是「chips 勾选组 + 行内按钮 + 自定义添加输入」
        #   的组合（如 wechat 群白名单：wlChips + 检测/刷新按钮 + customGroup 输入）。
        # 原来只认 chips 本体 ⇒ 按钮与输入全蒸发。
        acts: list[tuple[str, str]] = []
        for bm in re.finditer(r"<button([^>]*)>(.*?)</button>", chunk, re.S):
            attrs, txt = bm.group(1), _clean(bm.group(2))
            if not txt:
                continue
            idm = re.search(r'id="([\w-]+)"', attrs)
            aid = idm.group(1) if idm else ""
            if not aid:
                dam = re.search(r'data-(?:act|action|id)="([\w-]+)"', attrs)
                aid = dam.group(1) if dam else ""
            acts.append((txt, aid))
        im = re.search(r'<input[^>]*type="text"[^>]*/?>', chunk)
        ph = _placeholder(im.group(0)) if im else ""
        return Row("chips", label, cfg, placeholder=ph, hint=hint,
                   default=_default_of(cfg), actions=acts)
    return Row("chips", label, cfg, hint=hint, default=_default_of(cfg))


def _parse_nonwidget_row(chunk: str, label: str) -> Row:
    """无 input/select/textarea 的块 → buttons / status / table / info 四档判定。

    ⛔ P0-2：这是「按钮与选单蒸发」的修复点。优先级 = table > buttons > status
    （块里同时有表格和状态行时，表格是主体；按钮组同理）。
       · `<table>`                        → table（表头 thead/th 或首行，数据行 td）
       · `<button …>`（≥1 个）             → buttons，动作 id 取 `id=` / `data-act=`
       · `<b id="stXxx">`（状态行） → status，status_id = 那个 id
       · 其余                              → info（真·纯说明行）
    """
    hint = _hint_of(chunk)
    # ① 表格 / 矩阵
    tm = re.search(r"<table[^>]*>(.*?)</table>", chunk, re.S)
    if tm:
        body = tm.group(1)
        headers = [_clean(x) for x in re.findall(r"<th[^>]*>(.*?)</th>", body, re.S) if _clean(x)]
        rows = []
        checks: list[bool] = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
            # web 清单表（功能自检清单等）首列常是勾选框 —— 原来被 _clean
            # 剥成空文本 ⇒ Qt 渲染成纯文字清单，「结果」勾选列蒸发。
            has_ck = bool(re.search(r'<input[^>]*type="checkbox"', tr))
            cells = [_clean(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if cells and any(cells):
                rows.append(cells)
                checks.append(has_ck)
        return Row("table", label, hint=hint, headers=headers, rows=rows, cell_checks=checks)
    # ② 按钮组
    btns = re.findall(r"<button[^>]*>(.*?)</button>", chunk, re.S)
    if btns:
        acts = []
        for bm in re.finditer(r"<button([^>]*)>(.*?)</button>", chunk, re.S):
            attrs, txt = bm.group(1), _clean(bm.group(2))
            if not txt:
                continue
            aid = ""
            idm = re.search(r'id="([\w-]+)"', attrs)
            if idm:
                aid = idm.group(1)
            else:
                dam = re.search(r'data-(?:act|action|id)="([\w-]+)"', attrs)
                if dam:
                    aid = dam.group(1)
            acts.append((txt, aid))
        if acts:
            return Row("buttons", label, hint=hint, actions=acts)
    # ③ 状态行：块内任何 `<b id="…">`（web 状态位 id 无统一前缀——实测有
    # `stXxx`、`wxver` / `vsWhy` / `ttsWhy` / `vmVer` 等）。
    #   判据 = 「有 id 的 <b>」即可，因为它就是「一个由 JS 填字的状态位」。
    sm = re.search(r'<b id="([\w-]+)"', chunk)
    if sm:
        return Row("status", label, hint=hint, status_id=sm.group(1))
    return Row("info", label, hint=hint)


_CACHE: dict[str, Sec] = {}


def _div_close(body: str, open_gt: int) -> int:
    """open_gt = 某个 <div ...> 右尖括号 '>' 的索引；返回与之匹配的 </div> 起始索引。

    用 div 深度计数做括号匹配，使每个 <div class="row"> 都能取到它「自己」的闭合，
    而不会在碰到 `<div class="mid">` / `<div class="btns">` 时提前停（这正是旧前瞻截断的根因）。

     修：正文里存在**未转义的裸 `<`**（persona「评分补足」的 hint 写着「否则一律<95」），
    把它当标签起点会让 `.find('>')` 一口吞掉后面真正的 `</div>`，深度计数彻底错位、返回 -1。
    ⇒ 只认「`<` 后紧跟字母 / `/` / `!`」才是标签起点，裸 `<` 一律跳过。
    """
    depth = 1
    i = open_gt + 1
    n = len(body)
    while i < n:
        lt = body.find('<', i)
        if lt < 0:
            break
        nxt = body[lt + 1:lt + 2]
        if not (nxt.isalpha() or nxt in ("/", "!")):
            i = lt + 1 # 裸 '<'（正文）——不是标签，跳过重找
            continue
        if body.startswith('</div>', lt):
            depth -= 1
            if depth == 0:
                return lt
            i = lt + 6
        elif body.startswith('<div', lt) and body[lt + 4:lt + 5] in ("", " ", ">", "\t", "\n", "/"):
            depth += 1
            gt = body.find('>', lt)
            i = gt + 1 if gt >= 0 else n
        else:
            gt = body.find('>', lt)
            i = gt + 1 if gt >= 0 else n
    return -1


def _mid_group(body: str, mid_start: int) -> str:
    """mid 块标题：取它之前最近的 <div class="desc"> 文本或顶层 <div class="row"> 标签。"""
    best, best_pos = "", -1
    for dm in re.finditer(r'<div class="desc">(.*?)</div>', body, re.S):
        if dm.start() < mid_start and dm.start() > best_pos:
            best_pos = dm.start()
            best = _first_sentence(dm.group(1), 36)
    for rm in re.finditer(r'<div class="row"[^>]*>\s*<label>([^<]+)</label>', body):
        if rm.start() < mid_start and rm.start() > best_pos:
            best_pos = rm.start()
            best = _clean(rm.group(1))
    return best


def _mid_ranges(body: str) -> list:
    """返回每个 <div class="mid"> 的 (open_start, close_start, group_title)。"""
    out = []
    for m in re.finditer(r'<div class="mid"', body):
        gt = body.find('>', m.start())
        close = _div_close(body, gt)
        if close < 0:
            continue
        out.append((m.start(), close, _mid_group(body, m.start())))
    return out


def _row_ranges(body: str) -> list[tuple[int, int]]:
    """每个 `<div class="row">` 的 (open_start, close_start) —— 用 div 深度取自身闭合。

    ⛔ 这就是「按钮和选单蒸发」的修复点（旧前瞻截断的根因）：
    旧实现用 `re.finditer(r'… (.*?)(?=<div class="row"|<div class="btns"|… )')`，结束前瞻
    一碰到**同一个 row 内部**的 `<div class="btns">` 就提前停 ⇒ row 后半截（按钮组、
    row 内联的 `<select>`/`<input>`、hint 段落）整段丢失：人设「选单」行只剩 1 个按钮、
    「评分补足」行只有文本输入框而没有三枚按钮 + 补足轮数下拉 + 允许模型处理勾选。
    改用 `_div_close`（div 深度计数）取每个 row 它自己的闭合，行内任何嵌套 div 都不再截断。
    """
    out: list[tuple[int, int]] = []
    i = 0
    n = len(body)
    while i < n:
        rm = re.search(r'<div class="row"[^>]*><label>([^<]+)</label>', body[i:])
        if rm is None:
            break
        start = i + rm.start()
        gt = body.find('>', start)
        if gt < 0:
            break
        close = _div_close(body, gt)
        if close < 0:
            break
        out.append((start, close))
        i = close + 6 # 跳过本次 </div>（长度 6），从下一个 row 继续找
    return out


def _row_of(body: str, row_start: int, row_close: int, mid_spans: list[tuple[int, int]]) -> list[Row]:
    """把一段 row 自身区间变成 Row 列表（含 mid 标记 + 行内按钮组回收）。

    三件事，都不改 HTML（web 源码是唯一真值、一个字不动）：

    · **mid 子行标记**：起点落在某个 `.mid` 区间内 ⇒ 标记 sub/group/indent=1
      （渲染层据此缩进；对顶层行是恒等，对 mid 行才加标记）。

    · **行内按钮组回收**：一行里同时有 `<input>`/`<select>` 与 `<div class="btns">`
      时，`_parse_row` 只认控件 ⇒ 按钮组整组蒸发（真机「按钮都不见了」的另一半）。
      这里把该 row 内每个 `.btns` 组额外产出一条 `buttons` 子行，接在控件行之后。

    · **级联包含裁剪**：row 区间把后续 row 也包进去时（浏览器对游离 `</div>` 的 dom
      修正所致），取**标签 `</label>` 之后第一个自己开始的子级 `<div>`** 的闭合作为
      真正终点，多包进来的兄弟行交回外层 while 各自认领 —— 否则它们会整块消失。
    """
    inner = _row_inner(body, row_start, row_close)
    label = _row_label(body, row_start)
    out = [_parse_row(inner, label)]
    sub = False
    group = ""
    span = next((s for s in mid_spans if s[0] <= row_start < s[1]), None)
    if span is not None:
        sub, group = True, _mid_group(body, span[0])
    # ⛔ buttons/chips 档自身已把行内 .btns 抽干
    #   —— 原来只排除 buttons，chips 行的按钮组又额外产出一条独立 buttons 子行
    # ⇒ 「微信页出现两个群白名单」。
    if out[0].kind not in ("buttons", "chips"):
        for acts in _btns_groups(inner):
            br = Row("buttons", label, actions=acts, hint=out[0].hint)
            out.append(br)
    for r in out:
        r.sub, r.group, r.indent = sub, group, (1 if sub else 0)
    return out


def _row_label(body: str, row_start: int) -> str:
    lm = re.search(r'<label>([^<]+)</label>', body[row_start:])
    return _clean(lm.group(1)) if lm else ""


def _row_inner(body: str, row_start: int, row_close: int) -> str:
    """row 内部 HTML（<label> 闭合之后 ~ 该 row 自身闭合），并做级联包含裁剪。"""
    lm = re.search(r'<label>([^<]+)</label>', body[row_start:])
    if lm is None:
        return ""
    content_from = row_start + lm.end()
    end = row_close
    cm = re.search(r"<div", body[content_from:])
    if cm is not None:
        child_start = content_from + cm.start()
        gt = body.find('>', child_start)
        if gt >= 0:
            c_close = _div_close(body, gt)
            # 子级闭合早于 row 自身闭合 ⇒ 那才是内容终点（后面的兄弟 row 交回外层）
            if 0 <= c_close < row_close:
                end = c_close
    return body[content_from:end]


def _btns_groups(chunk: str) -> list[list[tuple[str, str]]]:
    """chunk 内所有 `<div class="btns">…</div>` 各抽一组动作（组内无 button 则跳过）。

    ⚠️ 只认同级 `.btns` 组 —— 组内还可能嵌别的 div（label 包裹的 select/checkbox），
    `_div_close` 取的是该组自己的闭合，所以「补足轮数」这种夹在按钮之间的下拉也照收。
    """
    out: list[list[tuple[str, str]]] = []
    for bm in re.finditer(r'<div class="btns"', chunk):
        gt = chunk.find('>', bm.start())
        if gt < 0:
            continue
        b_close = _div_close(chunk, gt)
        body_end = b_close if b_close >= 0 else len(chunk)
        acts = _extract_btns(chunk[bm.start():body_end])
        if acts:
            out.append(acts)
    return out


def _sweep_orphan_btns(body: str, covered: list[tuple[int, int]]) -> list[list[tuple[str, str]]]:
    """兜底：把**没有被任何 row 认领**的 `<button>` 组也捞成 buttons 行。

    web 里有按钮挂在「有行号的 row」与「`.btns` 组」之外的裸容器上 —— 例如 advanced 的
    「确定学习 / 学习评估」。它们既不在任何 row 区间内，也不是 `<div class="btns">`，
    只靠上面两条路径会整组蒸发（真机「按钮都不见了」的第三半）。判据 = 按出现顺序把
    未认领的 `<button>` 归组：同层级连续按钮（中间只夹 span/em 等内联元素）算一组。
    """
    claimed = [i for a, b in covered for i in (a, b)]
    out: list[list[tuple[str, str]]] = []
    cur: list[tuple[str, str]] = []
    for bm in re.finditer(r"<button([^>]*)>(.*?)</button>", body, re.S):
        inside = any(a <= bm.start() < b for a, b in covered)
        txt = _clean(bm.group(2))
        if inside or not txt:
            if cur:
                out.append(cur)
                cur = []
            continue
        attrs = bm.group(1)
        aid = ""
        idm = re.search(r'id="([\w-]+)"', attrs)
        if idm:
            aid = idm.group(1)
        else:
            dam = re.search(r'data-(?:act|action|id)="([\w-]+)"', attrs)
            if dam:
                aid = dam.group(1)
        cur.append((txt, aid))
    if cur:
        out.append(cur)
    return out


def _extract_btns(chunk: str) -> list[tuple[str, str]]:
    """`<button>` → [(文案, 动作id)]；id 取 `id=`，退到 `data-act/action/id=`。"""
    acts: list[tuple[str, str]] = []
    for bm in re.finditer(r"<button([^>]*)>(.*?)</button>", chunk, re.S):
        attrs, txt = bm.group(1), _clean(bm.group(2))
        if not txt:
            continue
        aid = ""
        idm = re.search(r'id="([\w-]+)"', attrs)
        if idm:
            aid = idm.group(1)
        else:
            dam = re.search(r'data-(?:act|action|id)="([\w-]+)"', attrs)
            if dam:
                aid = dam.group(1)
        acts.append((txt, aid))
    return acts


def _strip_scripts(body: str) -> str:
    """把 `<script>…</script>` 整段抹掉（等长占位，保偏移）。

    为什么必须：section 正文里的 JS 模板字符串本身含 `<div class="btns">` /
    `<button>`（如本机模型探测卡的「用这个 / 连通测试」、叮嘱列表的「删除」都是
    JS 动态生成的）——解析器不认 script 边界就会把**模板字符串里的按钮**当成
    静态按钮抠出来 ⇒ 页面上出现一排点了只显示「（web 侧动作）」的假按钮
    。
    占位用空格保持其余偏移计算不变。"""
    return re.sub(r"<script\b.*?</script>", lambda m: " " * (m.end() - m.start()), body, flags=re.S)


def secs() -> dict[str, Sec]:
    """解析一次，全进程共享。web 源码变了重启即跟（原型的"诚实"边界）。"""
    global _CACHE
    if _CACHE:
        return _CACHE
    text = WEB_PATH.read_text(encoding="utf-8")
    for m in re.finditer(r'<section id="sec-([\w-]+)" class="card" data-sec>(.*?)</section>', text, re.S):
        key, body = m.group(1), _strip_scripts(m.group(2))
        hd = re.search(r'<div class="sec-hd"><h2>([^<]+)</h2>', body) or re.search(r"<h2>([^<]+)</h2>", body)
        dsc = re.search(r'<div class="desc">(.{0,400}?)</div>', body, re.S)
        sec = Sec(key=key, title=_clean(hd.group(1)) if hd else key,
                  desc=_first_sentence(dsc.group(1), 150) if dsc else "")
        mid_spans = [(a, b) for a, b, _g in _mid_ranges(body)]
        covered = _row_ranges(body)
        for a, b in covered:
            sec.rows.extend(_row_of(body, a, b, mid_spans))
        for acts in _sweep_orphan_btns(body, covered):
            sec.rows.append(Row("buttons", "操作", actions=acts))
        _CACHE[key] = sec
    return _CACHE


def get(sec: str) -> Sec:
    return secs().get(sec) or Sec(key=sec, title=sec)
